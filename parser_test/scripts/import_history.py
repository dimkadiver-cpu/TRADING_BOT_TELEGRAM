"""Import Telegram chat history into parser_test raw_messages only."""

from __future__ import annotations

import asyncio
import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import sys

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dependency
    load_dotenv = None

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.logger import setup_logging
from src.core.migrations import apply_migrations
from src.storage.raw_messages import RawMessageStore
from src.telegram.ingestion import RawMessageIngestionService, TelegramIncomingMessage
from parser_test.scripts.db_paths import resolve_parser_test_db_path


@dataclass(slots=True)
class Stats:
    read: int = 0
    inserted: int = 0
    duplicates: int = 0
    skipped: int = 0


@dataclass(slots=True)
class MediaPayload:
    has_media: bool = False
    media_kind: str | None = None
    media_mime_type: str | None = None
    media_filename: str | None = None
    media_blob: bytes | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import Telegram history into parser_test raw_messages.")
    parser.add_argument("--chat-id", default=None, help="Target chat id/username/link. Falls back to env.")
    parser.add_argument("--topic-id", type=int, default=None, help="Optional forum topic root message id to import only one topic/thread.")
    parser.add_argument("--limit", type=int, default=None, help="Max messages to read from Telegram history.")
    parser.add_argument("--from-date", default=None, help="Inclusive lower bound (YYYY-MM-DD or ISO timestamp).")
    parser.add_argument("--to-date", default=None, help="Inclusive upper bound (YYYY-MM-DD or ISO timestamp).")
    parser.add_argument("--reverse", action="store_true", help="Read messages oldest -> newest.")
    parser.add_argument("--db-path", default=None, help="Path to parser_test sqlite DB.")
    parser.add_argument("--db-name", default=None, help="Logical DB name under parser_test/db (e.g. trader_a_mar).")
    parser.add_argument(
        "--db-per-chat",
        action="store_true",
        help="Create/use parser_test/db/parser_test__chat_<chat>.sqlite3 based on chat-id.",
    )
    parser.add_argument("--session", default=None, help="Telegram session name/path for parser_test.")
    parser.add_argument("--only-new", action="store_true", help="Skip ids <= current max id for this chat in DB.")
    parser.add_argument(
        "--download-media",
        action="store_true",
        help="Download Telegram media and store it in raw_messages.media_blob when available.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        from telethon import TelegramClient
    except ImportError as exc:
        raise RuntimeError("telethon is required for import_history.py. Install dependencies from requirements.txt.") from exc
    asyncio.run(_run_import(args=args, TelegramClient=TelegramClient))


async def _run_import(args: argparse.Namespace, TelegramClient: object) -> None:
    parser_test_dir = PROJECT_ROOT / "parser_test"
    env_path = parser_test_dir / ".env"
    if env_path.exists():
        _load_env_file(env_path)

    chat_ref = args.chat_id or os.getenv("PARSER_TEST_CHAT_ID")
    if not chat_ref or str(chat_ref).strip() in {"", "//"}:
        raise RuntimeError(
            "Missing chat target. Provide --chat-id or set PARSER_TEST_CHAT_ID in parser_test/.env."
        )
    print(f"chat target used: {chat_ref}")
    if args.topic_id is not None:
        print(f"topic target used: {args.topic_id}")

    db_path = resolve_parser_test_db_path(
        project_root=PROJECT_ROOT,
        parser_test_dir=parser_test_dir,
        explicit_db_path=args.db_path,
        db_name=args.db_name,
        db_per_chat=args.db_per_chat,
        chat_ref=str(chat_ref).strip(),
    )
    _ensure_not_live_db(db_path)
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    apply_migrations(db_path=db_path, migrations_dir=str(PROJECT_ROOT / "db" / "migrations"))

    log_path = os.getenv("PARSER_TEST_LOG_PATH", str(parser_test_dir / "logs" / "import_history.log"))
    log_path = str((PROJECT_ROOT / log_path).resolve()) if not Path(log_path).is_absolute() else log_path
    logger = setup_logging(log_path=log_path, level=os.getenv("LOG_LEVEL", "INFO"))

    api_id = int(_required_env("TELEGRAM_API_ID"))
    api_hash = _required_env("TELEGRAM_API_HASH")
    session_name = args.session or os.getenv("PARSER_TEST_TELEGRAM_SESSION") or os.getenv("TELEGRAM_SESSION") or "parser_test"

    from_ts = _parse_cli_date(args.from_date, end_of_day=False) if args.from_date else None
    to_ts = _parse_cli_date(args.to_date, end_of_day=True) if args.to_date else None
    if from_ts and to_ts and from_ts > to_ts:
        raise RuntimeError("--from-date must be <= --to-date")

    raw_store = RawMessageStore(db_path=db_path)
    ingestion = RawMessageIngestionService(store=raw_store, logger=logger)
    stats = Stats()

    async with TelegramClient(session_name, api_id, api_hash) as client:
        entity, resolution_method = await _resolve_target_entity(client=client, chat_ref=str(chat_ref).strip())
        print(f"chat resolution method: {resolution_method}")
        source_chat_id = str(getattr(entity, "id", chat_ref))
        source_chat_title = getattr(entity, "title", None) or getattr(entity, "username", None)
        source_type = _resolve_source_type(entity)
        max_existing_id = _max_existing_message_id(db_path=db_path, source_chat_id=source_chat_id) if args.only_new else None

        async for message in _iter_target_messages(
            client=client,
            entity=entity,
            limit=args.limit,
            reverse=args.reverse,
            topic_id=args.topic_id,
        ):
            stats.read += 1
            if message is None or getattr(message, "id", None) is None:
                stats.skipped += 1
                continue
            if max_existing_id is not None and int(message.id) <= max_existing_id:
                stats.duplicates += 1
                continue

            message_ts = _ensure_utc(message.date)
            if from_ts and message_ts < from_ts:
                continue
            if to_ts and message_ts > to_ts:
                continue

            reply_to_message_id = None
            if message.reply_to and getattr(message.reply_to, "reply_to_msg_id", None):
                reply_to_message_id = int(message.reply_to.reply_to_msg_id)
            media_payload = await _extract_media_payload(
                client=client,
                message=message,
                download_media=args.download_media,
            )

            incoming = TelegramIncomingMessage(
                source_chat_id=source_chat_id,
                source_chat_title=source_chat_title,
                source_type=source_type,
                source_trader_id=None,
                telegram_message_id=int(message.id),
                reply_to_message_id=reply_to_message_id,
                raw_text=message.message,
                message_ts=message_ts,
                acquisition_status="ACQUIRED_HISTORY",
                has_media=media_payload.has_media,
                media_kind=media_payload.media_kind,
                media_mime_type=media_payload.media_mime_type,
                media_filename=media_payload.media_filename,
                media_blob=media_payload.media_blob,
            )
            result = ingestion.ingest(incoming)
            if result.saved:
                stats.inserted += 1
            else:
                stats.duplicates += 1

    print(f"db_path: {db_path}")
    print(f"source chat id used: {chat_ref}")
    print(f"source chat resolved id: {source_chat_id}")
    print(f"total messages read: {stats.read}")
    print(f"total inserted: {stats.inserted}")
    print(f"total duplicates: {stats.duplicates}")
    print(f"total skipped: {stats.skipped}")

def _ensure_not_live_db(db_path: str) -> None:
    candidate = Path(db_path).resolve()
    live = (PROJECT_ROOT / "db" / "tele_signal_bot.sqlite3").resolve()
    if candidate == live:
        raise RuntimeError(f"Refusing to run on live DB path: {db_path}")


async def _iter_target_messages(
    *,
    client: object,
    entity: object,
    limit: int | None,
    reverse: bool,
    topic_id: int | None,
):
    if topic_id is None:
        async for message in client.iter_messages(entity, limit=limit, reverse=reverse):
            yield message
        return

    yielded_ids: set[int] = set()
    root_message = await client.get_messages(entity, ids=topic_id)
    root_is_valid = root_message is not None and getattr(root_message, "id", None) is not None
    remaining_limit = limit

    if reverse and root_is_valid:
        yielded_ids.add(int(root_message.id))
        yield root_message
        if remaining_limit is not None:
            remaining_limit -= 1
            if remaining_limit <= 0:
                return

    async for message in client.iter_messages(entity, limit=remaining_limit, reverse=reverse, reply_to=topic_id):
        message_id = getattr(message, "id", None)
        if message_id is None:
            yield message
            continue
        normalized_id = int(message_id)
        if normalized_id in yielded_ids:
            continue
        yielded_ids.add(normalized_id)
        yield message

    if not reverse and root_is_valid and int(root_message.id) not in yielded_ids:
        yield root_message


async def _extract_media_payload(*, client: object, message: object, download_media: bool) -> MediaPayload:
    if getattr(message, "media", None) is None:
        return MediaPayload()

    payload = MediaPayload(
        has_media=True,
        media_kind=_resolve_media_kind(message),
        media_mime_type=_resolve_media_mime_type(message),
        media_filename=_resolve_media_filename(message),
    )
    if not download_media:
        return payload

    try:
        media_blob = await client.download_media(message, file=bytes)
    except Exception:
        media_blob = None
    if isinstance(media_blob, (bytes, bytearray)):
        payload.media_blob = bytes(media_blob)
    return payload


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _parse_cli_date(value: str, end_of_day: bool) -> datetime:
    text = value.strip()
    if "T" not in text:
        day = datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if end_of_day:
            day = day.replace(hour=23, minute=59, second=59, microsecond=999999)
        return day

    normalized = text.replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)
    return _ensure_utc(dt)


def _ensure_utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _resolve_media_kind(message: object) -> str | None:
    if getattr(message, "photo", None) is not None:
        return "photo"
    if getattr(message, "video", None) is not None:
        return "video"
    if getattr(message, "document", None) is not None:
        return "document"
    if getattr(message, "media", None) is not None:
        return type(message.media).__name__.lower()
    return None


def _resolve_media_mime_type(message: object) -> str | None:
    file_info = getattr(message, "file", None)
    mime_type = getattr(file_info, "mime_type", None)
    if isinstance(mime_type, str) and mime_type.strip():
        return mime_type.strip()
    if getattr(message, "photo", None) is not None:
        return "image/jpeg"
    return None


def _resolve_media_filename(message: object) -> str | None:
    file_info = getattr(message, "file", None)
    name = getattr(file_info, "name", None)
    if isinstance(name, str) and name.strip():
        return name.strip()
    return None


def _max_existing_message_id(db_path: str, source_chat_id: str) -> int | None:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT MAX(telegram_message_id)
            FROM raw_messages
            WHERE source_chat_id = ?
            """,
            (source_chat_id,),
        ).fetchone()
    if row is None or row[0] is None:
        return None
    return int(row[0])


def _resolve_source_type(entity: object) -> str | None:
    if getattr(entity, "broadcast", False):
        return "channel"
    if getattr(entity, "megagroup", False):
        return "supergroup"
    if getattr(entity, "username", None) is not None and getattr(entity, "broadcast", None) is None:
        return "user"
    return entity.__class__.__name__.lower() if entity is not None else None


async def _resolve_target_entity(client: object, chat_ref: str) -> tuple[object, str]:
    try:
        return await client.get_entity(chat_ref), "direct entity"
    except Exception as direct_error:
        target_int = _to_int_or_none(chat_ref)
        if target_int is None:
            raise RuntimeError(
                f"Cannot resolve chat '{chat_ref}'. Ensure the id/username/link is valid and accessible."
            ) from direct_error

        async for dialog in client.iter_dialogs():
            entity = dialog.entity
            if _target_matches_entity(chat_ref=chat_ref, target_int=target_int, entity=entity):
                return entity, "dialog fallback"

        raise RuntimeError(
            f"Cannot resolve chat '{chat_ref}'. The authenticated account must already have access to that chat/channel."
        ) from direct_error


def _target_matches_entity(chat_ref: str, target_int: int, entity: object) -> bool:
    entity_id = _to_int_or_none(getattr(entity, "id", None))
    if entity_id is None:
        return False
    if str(entity_id) == chat_ref:
        return True
    # Channel ids may be configured as full Telegram id: -100<channel_id>.
    full_channel_id = -1000000000000 - entity_id
    if str(full_channel_id) == chat_ref:
        return True
    if entity_id == target_int:
        return True
    if full_channel_id == target_int:
        return True
    return False


def _to_int_or_none(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value).strip())
    except (ValueError, TypeError):
        return None


def _load_env_file(path: Path) -> None:
    if load_dotenv is not None:
        load_dotenv(path)
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


if __name__ == "__main__":
    main()
