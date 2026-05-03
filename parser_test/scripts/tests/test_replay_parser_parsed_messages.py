from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.core.migrations import apply_migrations
from src.parser.canonical_v1.models import RawContext
from src.parser.intent_types import IntentType
from src.parser.parsed_message import ExitBeEntities, IntentResult, ParsedMessage
from src.parser.trader_profiles.base import ParserContext
from src.storage.parse_results import ParseResultStore
from src.storage.parse_results_v1 import ParseResultV1Store
from src.storage.parsed_messages import ParsedMessageStore

from parser_test.scripts.replay_parser import ReplayRawMessage, SelectedRaw, backfill_parsed_messages, fetch_raw_messages


def _migrations_dir() -> str:
    return str(Path("db/migrations").resolve())


def _make_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "parser_test.sqlite3"
    apply_migrations(db_path=str(db_path), migrations_dir=_migrations_dir())
    return db_path


def _parsed_message() -> ParsedMessage:
    return ParsedMessage(
        parser_profile="trader_a",
        primary_class="UPDATE",
        parse_status="PARSED",
        confidence=0.9,
        intents=[
            IntentResult(
                type=IntentType.EXIT_BE,
                category="UPDATE",
                entities=ExitBeEntities(),
                confidence=0.9,
            )
        ],
        primary_intent=IntentType.EXIT_BE,
        raw_context=RawContext(
            raw_text="move to be",
            reply_to_message_id=None,
            extracted_links=[],
            hashtags=[],
            source_chat_id="-1003722628653",
            acquisition_mode="live",
        ),
    )


class _FakeProfile:
    def parse(self, text: str, context: ParserContext) -> ParsedMessage:
        return _parsed_message()


def test_backfill_parsed_messages_persists_confirmed_intents(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO raw_messages(
                raw_message_id,
                source_chat_id,
                telegram_message_id,
                raw_text,
                message_ts,
                acquired_at,
                acquisition_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1,
                "-1003722628653",
                1346,
                "закрыта в бу",
                "2026-04-29T10:00:00+00:00",
                "2026-04-29T10:00:01+00:00",
                "ACQUIRED",
            ),
        )
        conn.commit()
    selected = [
        SelectedRaw(
            row=ReplayRawMessage(
                raw_message_id=1,
                source_chat_id="-1003722628653",
                source_chat_title=None,
                source_chat_username=None,
                telegram_message_id=1346,
                reply_to_message_id=None,
                raw_text="закрыта в бу",
                message_ts="2026-04-29T10:00:00+00:00",
            ),
            resolved_trader_id="trader_a",
            trader_resolution_method="topic",
        )
    ]

    with patch("parser_test.scripts.replay_parser.get_profile_parser", return_value=_FakeProfile()):
        backfill_parsed_messages(db_path=str(db_path), selected=selected, show_normalized_samples=0)

    record = ParsedMessageStore(db_path=str(db_path)).get_by_raw_message_id(1)
    assert record is not None
    assert record.trader_id == "trader_a"
    assert record.primary_class == "UPDATE"
    assert record.validation_status == "VALIDATED"
    assert json.loads(record.intents_confirmed_json) == ["EXIT_BE"]


def test_fetch_raw_messages_only_unparsed_uses_parsed_messages_for_current_parser(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO raw_messages(
                raw_message_id,
                source_chat_id,
                telegram_message_id,
                raw_text,
                message_ts,
                acquired_at,
                acquisition_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?), (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1,
                "-1003722628653",
                1346,
                "already parsed",
                "2026-04-29T10:00:00+00:00",
                "2026-04-29T10:00:01+00:00",
                "ACQUIRED",
                2,
                "-1003722628653",
                1347,
                "new raw",
                "2026-04-29T10:01:00+00:00",
                "2026-04-29T10:01:01+00:00",
                "ACQUIRED",
            ),
        )
        conn.execute(
            "INSERT INTO parsed_messages(raw_message_id, trader_id, primary_class, validation_status, composite, parsed_json, intents_confirmed_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                1,
                "trader_a",
                "INFO",
                "VALIDATED",
                0,
                "{}",
                "[]",
                "2026-04-29T10:00:02+00:00",
            ),
        )
        conn.commit()

    rows = fetch_raw_messages(
        db_path=str(db_path),
        only_unparsed=True,
        limit=None,
        chat_id=None,
        from_date=None,
        to_date=None,
        parser_system="parsed_message",
    )

    assert [row.raw_message_id for row in rows] == [2]


def test_backfill_parsed_messages_force_reparse_rebuilds_existing_rows(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO raw_messages(
                raw_message_id,
                source_chat_id,
                telegram_message_id,
                raw_text,
                message_ts,
                acquired_at,
                acquisition_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1,
                "-1003722628653",
                1346,
                "already parsed",
                "2026-04-29T10:00:00+00:00",
                "2026-04-29T10:00:01+00:00",
                "ACQUIRED",
            ),
        )
        conn.execute(
            "INSERT INTO parsed_messages(raw_message_id, trader_id, primary_class, validation_status, composite, parsed_json, intents_confirmed_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                1,
                "trader_a",
                "INFO",
                "VALIDATED",
                0,
                json.dumps({"primary_class": "INFO"}),
                "[]",
                "2026-04-29T10:00:02+00:00",
            ),
        )
        conn.execute(
            "INSERT INTO parse_results(raw_message_id, eligibility_status, eligibility_reason, declared_trader_tag, resolved_trader_id, trader_resolution_method, message_type, parse_status, completeness, is_executable, symbol, direction, entry_raw, stop_raw, target_raw_list, leverage_hint, risk_hint, risky_flag, linkage_method, linkage_status, warning_text, notes, parse_result_normalized_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                1,
                "ACQUIRED_ELIGIBLE",
                "eligible",
                None,
                "trader_a",
                "topic",
                "UPDATE",
                "PARSED",
                "COMPLETE",
                0,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                0,
                None,
                None,
                None,
                None,
                "{}",
                "2026-04-29T10:00:02+00:00",
                "2026-04-29T10:00:02+00:00",
            ),
        )
        conn.execute(
            "INSERT INTO parse_results_v1(raw_message_id, trader_id, primary_class, parse_status, confidence, canonical_json, normalizer_error, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                1,
                "trader_a",
                "UPDATE",
                "PARSED",
                0.9,
                json.dumps({"primary_class": "UPDATE"}),
                None,
                "2026-04-29T10:00:02+00:00",
            ),
        )
        conn.commit()

    selected = [
        SelectedRaw(
            row=ReplayRawMessage(
                raw_message_id=1,
                source_chat_id="-1003722628653",
                source_chat_title=None,
                source_chat_username=None,
                telegram_message_id=1346,
                reply_to_message_id=None,
                raw_text="already parsed",
                message_ts="2026-04-29T10:00:00+00:00",
            ),
            resolved_trader_id="trader_a",
            trader_resolution_method="topic",
        )
    ]

    with patch("parser_test.scripts.replay_parser.get_profile_parser", return_value=_FakeProfile()):
        backfill_parsed_messages(
            db_path=str(db_path),
            selected=selected,
            show_normalized_samples=0,
            force_reparse=True,
        )

    record = ParsedMessageStore(db_path=str(db_path)).get_by_raw_message_id(1)
    assert record is not None
    assert record.primary_class == "UPDATE"
    assert json.loads(record.intents_confirmed_json) == ["EXIT_BE"]
    assert ParseResultStore(db_path=str(db_path)).get_by_raw_message_id(1) is None
    assert ParseResultV1Store(db_path=str(db_path)).get_by_raw_message_id(1) is None
