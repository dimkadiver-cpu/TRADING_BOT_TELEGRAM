"""Inject synthetic Telegram-like messages into the bot DB and route them locally.

This script is meant for fast manual testing without waiting for live Telegram.
It inserts raw_messages rows, then feeds them through the real MessageRouter so
parser, operation rules, target resolution, and signal persistence all run.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sqlite3
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.config_loader import load_config
from src.core.migrations import apply_migrations
from src.execution.dynamic_pairlist import DynamicPairlistManager
from src.operation_rules.engine import OperationRulesEngine
from src.storage.operational_signals_store import OperationalSignalsStore
from src.storage.parse_results import ParseResultStore
from src.storage.processing_status import ProcessingStatusStore
from src.storage.raw_messages import RawMessageRecord, RawMessageStore
from src.storage.review_queue import ReviewQueueStore
from src.storage.signals_store import SignalsStore
from src.target_resolver.resolver import TargetResolver
from src.telegram.channel_config import ChannelEntry, ChannelsConfig
from src.telegram.effective_trader import EffectiveTraderContext, EffectiveTraderResult
from src.telegram.eligibility import MessageEligibilityEvaluator
from src.telegram.router import MessageRouter, QueueItem


@dataclass(slots=True)
class InjectMessage:
    raw_text: str
    telegram_message_id: int | None = None
    reply_to_message_id: int | None = None
    trader_id: str | None = None
    source_chat_id: str | None = None
    source_chat_title: str | None = None
    source_trader_id: str | None = None
    message_ts: str | None = None
    acquisition_mode: str = "injected"


class FixedTraderResolver:
    """Small resolver for injection mode.

    We keep trader resolution deterministic: if the injected message declares a
    source_trader_id we use that, otherwise we fall back to the configured fixed
    trader for the script invocation.
    """

    def __init__(self, default_trader_id: str) -> None:
        self._default_trader_id = default_trader_id

    def resolve(self, ctx: EffectiveTraderContext) -> EffectiveTraderResult:
        del ctx
        return EffectiveTraderResult(trader_id=self._default_trader_id, method="inject_fixed")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inject fake Telegram messages into the bot DB.")
    parser.add_argument("--db-path", default=str(PROJECT_ROOT / "db" / "tele_signal_bot.sqlite3"), help="Target bot DB.")
    parser.add_argument("--rules-dir", default=str(PROJECT_ROOT / "config"), help="Operation rules directory.")
    parser.add_argument("--chat-id", required=True, help="Synthetic Telegram chat id, e.g. -1003171748254")
    parser.add_argument("--trader", required=True, help="Trader id to force during injection, e.g. trader_3")
    parser.add_argument("--text", default=None, help="Single raw message text to inject.")
    parser.add_argument("--reply-to", type=int, default=None, help="reply_to_message_id for a single injected message.")
    parser.add_argument("--message-id", type=int, default=None, help="telegram_message_id for a single injected message.")
    parser.add_argument("--scenario-file", default=None, help="Path to JSON file containing one object or a list of messages.")
    parser.add_argument("--source-chat-title", default="manual_injection", help="Optional synthetic chat title.")
    parser.add_argument("--dynamic-pairlist-path", default=str(PROJECT_ROOT / "freqtrade" / "user_data" / "dynamic_pairs.json"), help="Dynamic pairlist path.")
    parser.add_argument("--no-dynamic-pairlist", action="store_true", help="Disable dynamic pairlist writes during injection.")
    return parser.parse_args()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_messages(args: argparse.Namespace) -> list[InjectMessage]:
    if bool(args.text) == bool(args.scenario_file):
        raise SystemExit("Use exactly one of --text or --scenario-file.")

    if args.text:
        return [
            InjectMessage(
                raw_text=args.text,
                telegram_message_id=args.message_id,
                reply_to_message_id=args.reply_to,
                trader_id=args.trader,
                source_chat_id=args.chat_id,
                source_chat_title=args.source_chat_title,
            )
        ]

    scenario_path = Path(args.scenario_file).resolve()
    payload = json.loads(scenario_path.read_text(encoding="utf-8-sig"))
    items = payload if isinstance(payload, list) else [payload]
    messages: list[InjectMessage] = []
    for idx, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise SystemExit(f"scenario item #{idx} must be an object")
        raw_text = str(item.get("text") or item.get("raw_text") or "").strip()
        if not raw_text:
            raise SystemExit(f"scenario item #{idx} missing text")
        messages.append(
            InjectMessage(
                raw_text=raw_text,
                telegram_message_id=int(item["telegram_message_id"]) if item.get("telegram_message_id") is not None else None,
                reply_to_message_id=int(item["reply_to_message_id"]) if item.get("reply_to_message_id") is not None else None,
                trader_id=str(item.get("trader") or item.get("trader_id") or args.trader),
                source_chat_id=str(item.get("chat_id") or args.chat_id),
                source_chat_title=str(item.get("source_chat_title") or args.source_chat_title),
                source_trader_id=str(item.get("source_trader_id") or item.get("trader") or item.get("trader_id") or args.trader),
                message_ts=str(item.get("message_ts")) if item.get("message_ts") else None,
                acquisition_mode=str(item.get("acquisition_mode") or "injected"),
            )
        )
    return messages


def _next_message_id(status_store: ProcessingStatusStore, chat_id: str, floor: int = 1) -> int:
    current = status_store.get_last_telegram_message_id(chat_id)
    if current is None:
        return floor
    return max(floor, current + 1)


def _build_router(*, db_path: str, rules_dir: str, trader_id: str, chat_id: str, dynamic_pairlist_path: str | None) -> MessageRouter:
    config = load_config(str(PROJECT_ROOT))
    raw_store = RawMessageStore(db_path=db_path)
    channels_config = ChannelsConfig(
        recovery_max_hours=4,
        blacklist_global=[],
        channels=[
            ChannelEntry(
                chat_id=int(chat_id),
                label="manual_injection",
                active=True,
                trader_id=trader_id,
                blacklist=[],
            )
        ],
    )
    dynamic_pairlist_manager = None
    if dynamic_pairlist_path:
        dynamic_pairlist_manager = DynamicPairlistManager(Path(dynamic_pairlist_path))
    return MessageRouter(
        effective_trader_resolver=FixedTraderResolver(trader_id),
        eligibility_evaluator=MessageEligibilityEvaluator(raw_store),
        parse_results_store=ParseResultStore(db_path=db_path),
        processing_status_store=ProcessingStatusStore(db_path=db_path),
        review_queue_store=ReviewQueueStore(db_path=db_path),
        raw_message_store=raw_store,
        logger=logging.getLogger("inject_fake_messages"),
        channels_config=channels_config,
        db_path=db_path,
        operation_rules_engine=OperationRulesEngine(rules_dir=rules_dir),
        target_resolver=TargetResolver(),
        signals_store=SignalsStore(db_path=db_path),
        operational_signals_store=OperationalSignalsStore(db_path=db_path),
        dynamic_pairlist_manager=dynamic_pairlist_manager,
    )


def _save_raw_message(*, db_path: str, message: InjectMessage, telegram_message_id: int) -> int:
    store = RawMessageStore(db_path=db_path)
    result = store.save_with_id(
        RawMessageRecord(
            source_chat_id=str(message.source_chat_id),
            telegram_message_id=telegram_message_id,
            message_ts=message.message_ts or _utc_now_iso(),
            acquired_at=_utc_now_iso(),
            raw_text=message.raw_text,
            source_chat_title=message.source_chat_title,
            source_type="manual_injection",
            source_trader_id=message.source_trader_id or message.trader_id,
            reply_to_message_id=message.reply_to_message_id,
            acquisition_status="ACQUIRED",
        )
    )
    if result.raw_message_id is None:
        raise RuntimeError(f"failed to persist raw_message chat={message.source_chat_id} telegram_message_id={telegram_message_id}")
    return result.raw_message_id


def _route_message(*, router: MessageRouter, raw_message_id: int, message: InjectMessage, telegram_message_id: int) -> None:
    router.route(
        QueueItem(
            raw_message_id=raw_message_id,
            source_chat_id=str(message.source_chat_id),
            telegram_message_id=telegram_message_id,
            raw_text=message.raw_text,
            source_trader_id=message.source_trader_id or message.trader_id,
            reply_to_message_id=message.reply_to_message_id,
            acquisition_mode=message.acquisition_mode,
        )
    )


def _query_summary(*, db_path: str, raw_message_id: int) -> dict[str, Any]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        raw_row = conn.execute(
            "SELECT raw_message_id, source_chat_id, telegram_message_id, reply_to_message_id, processing_status FROM raw_messages WHERE raw_message_id = ?",
            (raw_message_id,),
        ).fetchone()
        parse_row = conn.execute(
            "SELECT parse_result_id, message_type, parse_status, resolved_trader_id, eligibility_status, warning_text FROM parse_results WHERE raw_message_id = ?",
            (raw_message_id,),
        ).fetchone()
        signal_row = conn.execute(
            """
            SELECT s.attempt_key, s.status, s.symbol, s.side
            FROM signals s
            JOIN raw_messages rm ON rm.telegram_message_id = CAST(s.root_telegram_id AS INTEGER) AND rm.source_chat_id = s.channel_id
            WHERE rm.raw_message_id = ?
            ORDER BY s.rowid DESC
            LIMIT 1
            """,
            (raw_message_id,),
        ).fetchone()
        op_row = None
        if parse_row is not None:
            op_row = conn.execute(
                "SELECT op_signal_id, attempt_key, message_type, target_eligibility FROM operational_signals WHERE parse_result_id = ? ORDER BY op_signal_id DESC LIMIT 1",
                (parse_row["parse_result_id"],),
            ).fetchone()
    return {
        "raw": dict(raw_row) if raw_row is not None else None,
        "parse": dict(parse_row) if parse_row is not None else None,
        "signal": dict(signal_row) if signal_row is not None else None,
        "operational_signal": dict(op_row) if op_row is not None else None,
    }


def main() -> None:
    args = parse_args()
    db_path = str(Path(args.db_path).resolve())
    apply_migrations(db_path=db_path, migrations_dir=str((PROJECT_ROOT / "db" / "migrations").resolve()))

    messages = _load_messages(args)
    status_store = ProcessingStatusStore(db_path=db_path)
    router = _build_router(
        db_path=db_path,
        rules_dir=str(Path(args.rules_dir).resolve()),
        trader_id=args.trader,
        chat_id=args.chat_id,
        dynamic_pairlist_path=None if args.no_dynamic_pairlist else str(Path(args.dynamic_pairlist_path).resolve()),
    )

    next_auto_id = _next_message_id(status_store, args.chat_id)
    summaries: list[dict[str, Any]] = []

    for message in messages:
        telegram_message_id = message.telegram_message_id if message.telegram_message_id is not None else next_auto_id
        if message.telegram_message_id is None:
            next_auto_id = telegram_message_id + 1
        store = RawMessageStore(db_path=db_path)
        save_result = store.save_with_id(
            RawMessageRecord(
                source_chat_id=str(message.source_chat_id),
                telegram_message_id=telegram_message_id,
                message_ts=message.message_ts or _utc_now_iso(),
                acquired_at=_utc_now_iso(),
                raw_text=message.raw_text,
                source_chat_title=message.source_chat_title,
                source_type="manual_injection",
                source_trader_id=message.source_trader_id or message.trader_id,
                reply_to_message_id=message.reply_to_message_id,
                acquisition_status="ACQUIRED",
            )
        )
        raw_id = save_result.raw_message_id
        if raw_id is None:
            raise RuntimeError(
                f"failed to persist raw_message chat={message.source_chat_id} telegram_message_id={telegram_message_id}"
            )
        if save_result.saved:
            _route_message(router=router, raw_message_id=raw_id, message=message, telegram_message_id=telegram_message_id)
        summary = _query_summary(db_path=db_path, raw_message_id=raw_id)
        summary["injection"] = {
            "saved_new_raw_message": save_result.saved,
            "routed": save_result.saved,
            "duplicate_raw_message": not save_result.saved,
            "duplicate_key": {
                "source_chat_id": str(message.source_chat_id),
                "telegram_message_id": telegram_message_id,
            },
        }
        summaries.append(summary)

    print(json.dumps({"ok": True, "count": len(summaries), "results": summaries}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
