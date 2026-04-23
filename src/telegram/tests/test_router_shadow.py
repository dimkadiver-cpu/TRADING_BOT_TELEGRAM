"""Tests for MessageRouter shadow normalizer v1 (Fase 4)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.core.migrations import apply_migrations
from src.parser.trader_profiles.base import TraderParseResult
from src.storage.parse_results_v1 import ParseResultV1Store
from src.storage.review_queue import ReviewQueueStore
from src.telegram.channel_config import ChannelEntry, ChannelsConfig
from src.telegram.router import MessageRouter, QueueItem


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _migrations_dir() -> str:
    return str(Path("db/migrations").resolve())


def _config(*, channels: list[ChannelEntry] | None = None) -> ChannelsConfig:
    return ChannelsConfig(
        recovery_max_hours=4,
        blacklist_global=[],
        channels=channels or [ChannelEntry(chat_id=-100123, label="t", active=True, trader_id="trader_a")],
    )


def _item(**overrides: object) -> QueueItem:
    defaults: dict[str, object] = {
        "raw_message_id": 42,
        "source_chat_id": "-100123",
        "telegram_message_id": 9001,
        "raw_text": "BTC long entry: 50000 sl: 48000 tp1: 52000",
        "source_trader_id": "trader_a",
        "reply_to_message_id": None,
        "acquisition_mode": "live",
    }
    defaults.update(overrides)
    return QueueItem(**defaults)  # type: ignore[arg-type]


def _fake_result(message_type: str = "NEW_SIGNAL") -> TraderParseResult:
    return TraderParseResult(
        message_type=message_type,
        intents=["NS_CREATE_SIGNAL"] if message_type == "NEW_SIGNAL" else ["U_CLOSE_FULL"],
        entities={
            "symbol": "BTCUSDT",
            "side": "LONG",
            "stop_loss": 48000.0,
            "take_profits": [52000.0],
            "entry_plan_entries": [
                {"sequence": 1, "role": "PRIMARY", "order_type": "LIMIT", "price": 50000.0, "is_optional": False}
            ],
            "entry_structure": "SINGLE",
            "entry_plan_type": "SINGLE_LIMIT",
            "has_averaging_plan": False,
        },
        confidence=0.8,
        primary_intent="NS_CREATE_SIGNAL" if message_type == "NEW_SIGNAL" else "U_CLOSE_FULL",
    )


def _router_with_shadow(tmp_path: Path) -> tuple[MessageRouter, ParseResultV1Store]:
    db_path = str(tmp_path / "router.sqlite3")
    apply_migrations(db_path=db_path, migrations_dir=_migrations_dir())

    v1_store = ParseResultV1Store(db_path=db_path)

    deps: dict[str, object] = {
        "effective_trader_resolver": MagicMock(),
        "eligibility_evaluator": MagicMock(),
        "parse_results_store": MagicMock(),
        "processing_status_store": MagicMock(),
        "review_queue_store": ReviewQueueStore(db_path=db_path),
        "raw_message_store": MagicMock(),
        "logger": MagicMock(),
    }
    deps["eligibility_evaluator"].evaluate.return_value = MagicMock(  # type: ignore[attr-defined]
        status="ACQUIRED_ELIGIBLE",
        reason="eligible",
        strong_link_method=None,
    )
    deps["effective_trader_resolver"].resolve.return_value = MagicMock(  # type: ignore[attr-defined]
        trader_id="trader_a",
        method="config",
        detail=None,
    )
    deps["raw_message_store"].get_by_source_and_message_id.return_value = None  # type: ignore[attr-defined]

    router = MessageRouter(
        effective_trader_resolver=deps["effective_trader_resolver"],  # type: ignore[arg-type]
        eligibility_evaluator=deps["eligibility_evaluator"],  # type: ignore[arg-type]
        parse_results_store=deps["parse_results_store"],  # type: ignore[arg-type]
        processing_status_store=deps["processing_status_store"],  # type: ignore[arg-type]
        review_queue_store=deps["review_queue_store"],  # type: ignore[arg-type]
        raw_message_store=deps["raw_message_store"],  # type: ignore[arg-type]
        logger=deps["logger"],  # type: ignore[arg-type]
        channels_config=_config(),
    )
    router.enable_shadow_normalizer(v1_store)
    return router, v1_store


# ---------------------------------------------------------------------------
# Tests: storage layer
# ---------------------------------------------------------------------------

class TestParseResultV1Store:
    def test_upsert_and_read_back(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "test.sqlite3")
        apply_migrations(db_path=db_path, migrations_dir=_migrations_dir())
        store = ParseResultV1Store(db_path=db_path)

        from src.storage.parse_results_v1 import ParseResultV1Record
        record = ParseResultV1Record(
            raw_message_id=1,
            trader_id="trader_a",
            primary_class="SIGNAL",
            parse_status="PARSED",
            confidence=0.9,
            canonical_json='{"schema_version":"1.0"}',
            normalizer_error=None,
            created_at="2026-04-22T00:00:00Z",
        )
        store.upsert(record)

        fetched = store.get_by_raw_message_id(1)
        assert fetched is not None
        assert fetched.primary_class == "SIGNAL"
        assert fetched.parse_status == "PARSED"
        assert fetched.confidence == 0.9
        assert fetched.normalizer_error is None

    def test_upsert_is_idempotent(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "test.sqlite3")
        apply_migrations(db_path=db_path, migrations_dir=_migrations_dir())
        store = ParseResultV1Store(db_path=db_path)

        from src.storage.parse_results_v1 import ParseResultV1Record
        rec = ParseResultV1Record(
            raw_message_id=5,
            trader_id="trader_a",
            primary_class="INFO",
            parse_status="UNCLASSIFIED",
            confidence=0.1,
            canonical_json="{}",
            normalizer_error=None,
            created_at="2026-04-22T00:00:00Z",
        )
        store.upsert(rec)

        # Upsert again with updated class
        rec2 = ParseResultV1Record(
            raw_message_id=5,
            trader_id="trader_a",
            primary_class="SIGNAL",
            parse_status="PARSED",
            confidence=0.95,
            canonical_json='{"updated":true}',
            normalizer_error=None,
            created_at="2026-04-22T01:00:00Z",
        )
        store.upsert(rec2)

        fetched = store.get_by_raw_message_id(5)
        assert fetched is not None
        assert fetched.primary_class == "SIGNAL"
        assert fetched.confidence == 0.95

    def test_get_missing_returns_none(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "test.sqlite3")
        apply_migrations(db_path=db_path, migrations_dir=_migrations_dir())
        store = ParseResultV1Store(db_path=db_path)
        assert store.get_by_raw_message_id(9999) is None

    def test_store_normalizer_error(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "test.sqlite3")
        apply_migrations(db_path=db_path, migrations_dir=_migrations_dir())
        store = ParseResultV1Store(db_path=db_path)

        from src.storage.parse_results_v1 import ParseResultV1Record
        rec = ParseResultV1Record(
            raw_message_id=7,
            trader_id="trader_a",
            primary_class="INFO",
            parse_status="UNCLASSIFIED",
            confidence=0.0,
            canonical_json="",
            normalizer_error="ValueError: something went wrong",
            created_at="2026-04-22T00:00:00Z",
        )
        store.upsert(rec)

        fetched = store.get_by_raw_message_id(7)
        assert fetched is not None
        assert fetched.normalizer_error == "ValueError: something went wrong"

    def test_count_by_class(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "test.sqlite3")
        apply_migrations(db_path=db_path, migrations_dir=_migrations_dir())
        store = ParseResultV1Store(db_path=db_path)

        from src.storage.parse_results_v1 import ParseResultV1Record
        for i, cls in enumerate(["SIGNAL", "SIGNAL", "UPDATE", "REPORT", "INFO"]):
            store.upsert(ParseResultV1Record(
                raw_message_id=100 + i,
                trader_id="trader_a",
                primary_class=cls,
                parse_status="PARSED",
                confidence=0.8,
                canonical_json="{}",
                normalizer_error=None,
                created_at="2026-04-22T00:00:00Z",
            ))
        counts = store.count_by_class()
        assert counts["SIGNAL"] == 2
        assert counts["UPDATE"] == 1
        assert counts["REPORT"] == 1
        assert counts["INFO"] == 1


# ---------------------------------------------------------------------------
# Tests: shadow mode in router
# ---------------------------------------------------------------------------

class TestRouterShadowMode:
    def test_shadow_disabled_by_default(self, tmp_path: Path) -> None:
        """Router built without enable_shadow_normalizer should not write v1 rows."""
        db_path = str(tmp_path / "test.sqlite3")
        apply_migrations(db_path=db_path, migrations_dir=_migrations_dir())
        v1_store = ParseResultV1Store(db_path=db_path)

        # Router without shadow enabled
        router = MessageRouter(
            effective_trader_resolver=MagicMock(**{
                "resolve.return_value": MagicMock(trader_id="trader_a", method="config", detail=None)
            }),
            eligibility_evaluator=MagicMock(**{
                "evaluate.return_value": MagicMock(
                    status="ACQUIRED_ELIGIBLE", reason="ok", strong_link_method=None
                )
            }),
            parse_results_store=MagicMock(),
            processing_status_store=MagicMock(),
            review_queue_store=ReviewQueueStore(db_path=db_path),
            raw_message_store=MagicMock(**{"get_by_source_and_message_id.return_value": None}),
            logger=MagicMock(),
            channels_config=_config(),
        )

        with patch("src.telegram.router.get_profile_parser") as mock_parser_factory:
            mock_parser = MagicMock()
            mock_parser.parse_message.return_value = _fake_result()
            mock_parser_factory.return_value = mock_parser
            router.route(_item())

        assert v1_store.get_by_raw_message_id(42) is None

    def test_shadow_enabled_writes_v1_row(self, tmp_path: Path) -> None:
        """With shadow enabled, a v1 row is written after each parse."""
        router, v1_store = _router_with_shadow(tmp_path)

        with patch("src.telegram.router.get_profile_parser") as mock_parser_factory:
            mock_parser = MagicMock()
            mock_parser.parse_message.return_value = _fake_result("NEW_SIGNAL")
            mock_parser_factory.return_value = mock_parser
            router.route(_item(raw_message_id=42))

        record = v1_store.get_by_raw_message_id(42)
        assert record is not None
        assert record.trader_id == "trader_a"
        assert record.primary_class in {"SIGNAL", "INFO", "UPDATE", "REPORT"}
        assert record.parse_status in {"PARSED", "PARTIAL", "UNCLASSIFIED", "ERROR"}
        assert record.normalizer_error is None

    def test_shadow_signal_canonical_json_is_valid(self, tmp_path: Path) -> None:
        """canonical_json must be valid JSON with schema_version."""
        router, v1_store = _router_with_shadow(tmp_path)

        with patch("src.telegram.router.get_profile_parser") as mock_parser_factory:
            mock_parser = MagicMock()
            mock_parser.parse_message.return_value = _fake_result("NEW_SIGNAL")
            mock_parser_factory.return_value = mock_parser
            router.route(_item(raw_message_id=43))

        record = v1_store.get_by_raw_message_id(43)
        assert record is not None
        data = json.loads(record.canonical_json)
        assert data.get("schema_version") == "1.0"
        assert "primary_class" in data

    def test_shadow_old_flow_unaffected(self, tmp_path: Path) -> None:
        """parse_results.upsert must still be called with the legacy record."""
        router, v1_store = _router_with_shadow(tmp_path)

        parse_results_mock = router._parse_results  # type: ignore[attr-defined]

        with patch("src.telegram.router.get_profile_parser") as mock_parser_factory:
            mock_parser = MagicMock()
            mock_parser.parse_message.return_value = _fake_result("NEW_SIGNAL")
            mock_parser_factory.return_value = mock_parser
            router.route(_item(raw_message_id=44))

        parse_results_mock.upsert.assert_called_once()

    def test_shadow_normalizer_error_stored_and_does_not_crash_route(self, tmp_path: Path) -> None:
        """If normalizer raises, the error is stored and routing completes normally."""
        router, v1_store = _router_with_shadow(tmp_path)

        with patch("src.telegram.router.get_profile_parser") as mock_parser_factory, \
             patch("src.parser.canonical_v1.normalizer.normalize", side_effect=RuntimeError("boom")):
            mock_parser = MagicMock()
            mock_parser.parse_message.return_value = _fake_result("NEW_SIGNAL")
            mock_parser_factory.return_value = mock_parser
            router.route(_item(raw_message_id=45))

        record = v1_store.get_by_raw_message_id(45)
        assert record is not None
        assert record.normalizer_error is not None
        assert "boom" in record.normalizer_error
        # Old flow still ran
        router._parse_results.upsert.assert_called_once()  # type: ignore[attr-defined]

    def test_enable_disable_shadow(self, tmp_path: Path) -> None:
        """disable_shadow_normalizer stops writing new rows."""
        router, v1_store = _router_with_shadow(tmp_path)
        router.disable_shadow_normalizer()

        with patch("src.telegram.router.get_profile_parser") as mock_parser_factory:
            mock_parser = MagicMock()
            mock_parser.parse_message.return_value = _fake_result("NEW_SIGNAL")
            mock_parser_factory.return_value = mock_parser
            router.route(_item(raw_message_id=46))

        assert v1_store.get_by_raw_message_id(46) is None

    def test_shadow_update_message(self, tmp_path: Path) -> None:
        """UPDATE messages with U_CLOSE_FULL produce primary_class=UPDATE."""
        router, v1_store = _router_with_shadow(tmp_path)

        update_result = TraderParseResult(
            message_type="UPDATE",
            intents=["U_CLOSE_FULL"],
            entities={"close_scope": "FULL"},
            target_refs=[{"kind": "reply", "ref": 100}],
            confidence=0.7,
            primary_intent="U_CLOSE_FULL",
        )

        with patch("src.telegram.router.get_profile_parser") as mock_parser_factory:
            mock_parser = MagicMock()
            mock_parser.parse_message.return_value = update_result
            mock_parser_factory.return_value = mock_parser
            router.route(_item(raw_message_id=47, reply_to_message_id=100))

        record = v1_store.get_by_raw_message_id(47)
        assert record is not None
        assert record.primary_class == "UPDATE"
