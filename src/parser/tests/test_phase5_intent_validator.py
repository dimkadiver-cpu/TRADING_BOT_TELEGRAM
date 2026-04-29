from __future__ import annotations

import sqlite3
from pathlib import Path

from src.core.migrations import apply_migrations
from src.parser.canonical_v1.models import RawContext, TargetRef, TargetScope, Targeting
from src.parser.intent_types import IntentType
from src.parser.parsed_message import (
    CloseFullEntities,
    IntentResult,
    ParsedMessage,
    ReportFinalResultEntities,
    TpHitEntities,
)
from src.parser.intent_validator.history_provider import SignalLifecycle, SQLiteHistoryProvider
from src.parser.intent_validator.validator import HistoryBackedIntentValidator


def _migrations_dir() -> str:
    return str(Path("db/migrations").resolve())


def _raw_context() -> RawContext:
    return RawContext(
        raw_text="test",
        reply_to_message_id=1002,
        extracted_links=[],
        hashtags=[],
        source_chat_id="-100123",
        acquisition_mode="live",
    )


def _parsed_message(*, intent: IntentResult, targeting: Targeting | None = None) -> ParsedMessage:
    return ParsedMessage(
        parser_profile="trader_a",
        primary_class="UPDATE",
        parse_status="PARSED",
        confidence=0.9,
        intents=[intent],
        primary_intent=intent.type,
        targeting=targeting,
        raw_context=_raw_context(),
    )


def _insert_raw_message(
    conn: sqlite3.Connection,
    *,
    raw_message_id: int,
    telegram_message_id: int,
    reply_to_message_id: int | None,
    message_ts: str,
    source_chat_id: str = "-100123",
) -> None:
    conn.execute(
        """
        INSERT INTO raw_messages(
            raw_message_id,
            source_chat_id,
            telegram_message_id,
            reply_to_message_id,
            raw_text,
            message_ts,
            acquired_at,
            acquisition_status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            raw_message_id,
            source_chat_id,
            telegram_message_id,
            reply_to_message_id,
            f"message-{telegram_message_id}",
            message_ts,
            message_ts,
            "ACQUIRED",
        ),
    )


def _insert_parsed_message(
    conn: sqlite3.Connection,
    *,
    raw_message_id: int,
    primary_class: str,
    parse_status: str,
    intents_confirmed_json: str,
    validation_status: str = "VALIDATED",
    source_chat_id: str = "-100123",
) -> None:
    parsed_json = (
        ParsedMessage(
            parser_profile="trader_a",
            primary_class=primary_class,
            parse_status=parse_status,
            confidence=0.9,
            raw_context=RawContext(
                raw_text=f"message-{raw_message_id}",
                reply_to_message_id=None,
                extracted_links=[],
                hashtags=[],
                source_chat_id=source_chat_id,
                acquisition_mode="live",
            ),
        )
        .model_dump_json(exclude_none=True)
    )
    conn.execute(
        """
        INSERT INTO parsed_messages(
            raw_message_id,
            trader_id,
            primary_class,
            validation_status,
            composite,
            parsed_json,
            intents_confirmed_json,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            raw_message_id,
            "trader_a",
            primary_class,
            validation_status,
            0,
            parsed_json,
            intents_confirmed_json,
            "2026-04-29T10:00:00+00:00",
        ),
    )


def _seed_chain(db_path: Path, *, confirmed_terminal: bool) -> None:
    apply_migrations(db_path=str(db_path), migrations_dir=_migrations_dir())
    with sqlite3.connect(db_path) as conn:
        _insert_raw_message(
            conn,
            raw_message_id=1,
            telegram_message_id=1001,
            reply_to_message_id=None,
            message_ts="2026-04-29T10:00:00+00:00",
        )
        _insert_raw_message(
            conn,
            raw_message_id=2,
            telegram_message_id=1002,
            reply_to_message_id=1001,
            message_ts="2026-04-29T10:01:00+00:00",
        )
        _insert_raw_message(
            conn,
            raw_message_id=3,
            telegram_message_id=1003,
            reply_to_message_id=1002,
            message_ts="2026-04-29T10:02:00+00:00",
        )

        _insert_parsed_message(
            conn,
            raw_message_id=1,
            primary_class="SIGNAL",
            parse_status="PARSED",
            intents_confirmed_json="[]",
        )
        _insert_parsed_message(
            conn,
            raw_message_id=2,
            primary_class="UPDATE",
            parse_status="PARSED",
            intents_confirmed_json='["MOVE_STOP_TO_BE"]',
        )
        _insert_parsed_message(
            conn,
            raw_message_id=3,
            primary_class="UPDATE",
            parse_status="PARSED",
            intents_confirmed_json='["CLOSE_FULL"]' if confirmed_terminal else "[]",
            validation_status="VALIDATED" if confirmed_terminal else "PENDING",
        )
        conn.commit()


class _RecordingHistoryProvider:
    def __init__(self, lifecycle: SignalLifecycle | None = None) -> None:
        self.lifecycle = lifecycle or SignalLifecycle(
            ref_message_id=1002,
            new_signal_message_id=1001,
            ordered_history=["NEW_SIGNAL"],
            is_terminal=False,
        )
        self.calls: list[tuple[int, str | None]] = []

    def get_signal_lifecycle(
        self,
        *,
        ref_message_id: int,
        source_chat_id: str | None = None,
    ) -> SignalLifecycle:
        self.calls.append((ref_message_id, source_chat_id))
        return self.lifecycle


def test_sqlite_history_provider_returns_new_signal_and_confirmed_intents_only(tmp_path: Path) -> None:
    db_path = tmp_path / "validator.sqlite3"
    _seed_chain(db_path, confirmed_terminal=False)

    provider = SQLiteHistoryProvider(db_path=str(db_path))
    lifecycle = provider.get_signal_lifecycle(ref_message_id=1003, source_chat_id="-100123")

    assert lifecycle.new_signal_message_id == 1001
    assert lifecycle.ordered_history == ["NEW_SIGNAL", "MOVE_STOP_TO_BE"]
    assert lifecycle.is_terminal is False


def test_validator_auto_confirms_intent_without_rule_without_touching_history() -> None:
    provider = _RecordingHistoryProvider()
    validator = HistoryBackedIntentValidator(history_provider=provider)
    parsed = _parsed_message(
        intent=IntentResult(
            type=IntentType.REPORT_FINAL_RESULT,
            category="REPORT",
            entities=ReportFinalResultEntities(),
            confidence=0.7,
        )
    )

    validated = validator.validate(parsed)

    assert validated.validation_status == "VALIDATED"
    assert validated.intents[0].status == "CONFIRMED"
    assert validated.intents[0].valid_refs == []
    assert provider.calls == []


def test_validator_auto_confirms_ruled_intent_with_non_single_signal_scope() -> None:
    provider = _RecordingHistoryProvider()
    validator = HistoryBackedIntentValidator(history_provider=provider)
    parsed = _parsed_message(
        intent=IntentResult(
            type=IntentType.CLOSE_FULL,
            category="UPDATE",
            entities=CloseFullEntities(),
            confidence=0.8,
        ),
        targeting=Targeting(
            refs=[],
            scope=TargetScope(kind="ALL_OPEN", applies_to_all=True),
            strategy="GLOBAL_SCOPE",
            targeted=True,
        ),
    )

    validated = validator.validate(parsed)

    assert validated.validation_status == "VALIDATED"
    assert validated.intents[0].status == "CONFIRMED"
    assert validated.intents[0].valid_refs == []
    assert provider.calls == []


def test_validator_marks_intent_invalid_when_confirmed_terminal_event_exists(tmp_path: Path) -> None:
    db_path = tmp_path / "validator.sqlite3"
    _seed_chain(db_path, confirmed_terminal=True)

    validator = HistoryBackedIntentValidator(db_path=str(db_path))
    parsed = _parsed_message(
        intent=IntentResult(
            type=IntentType.TP_HIT,
            category="REPORT",
            entities=TpHitEntities(),
            confidence=0.85,
        ),
        targeting=Targeting(
            refs=[TargetRef(ref_type="MESSAGE_ID", value=1003)],
            scope=TargetScope(kind="SINGLE_SIGNAL"),
            strategy="REPLY_OR_LINK",
            targeted=True,
        ),
    )

    validated = validator.validate(parsed)

    assert validated.validation_status == "VALIDATED"
    assert validated.intents[0].status == "INVALID"
    assert validated.intents[0].valid_refs == []
    assert validated.intents[0].invalid_refs == [1003]
    assert validated.intents[0].invalid_reason == "no_open_signal"
