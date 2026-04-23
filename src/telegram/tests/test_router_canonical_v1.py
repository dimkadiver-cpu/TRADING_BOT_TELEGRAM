"""Integration tests for router + canonical v1 normalizer (Fase 7).

Verifies that the v1 normalizer is always-on when parse_results_v1_store is
wired via constructor, without requiring enable_shadow_normalizer().
"""

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


def _config() -> ChannelsConfig:
    return ChannelsConfig(
        recovery_max_hours=4,
        blacklist_global=[],
        channels=[ChannelEntry(chat_id=-100123, label="t", active=True, trader_id="trader_a")],
    )


def _item(raw_message_id: int = 42, **overrides: object) -> QueueItem:
    defaults: dict[str, object] = {
        "raw_message_id": raw_message_id,
        "source_chat_id": "-100123",
        "telegram_message_id": 9001,
        "raw_text": "BTC long entry: 50000 sl: 48000 tp1: 52000",
        "source_trader_id": "trader_a",
        "reply_to_message_id": None,
        "acquisition_mode": "live",
    }
    defaults.update(overrides)
    return QueueItem(**defaults)  # type: ignore[arg-type]


def _signal_result() -> TraderParseResult:
    return TraderParseResult(
        message_type="NEW_SIGNAL",
        intents=["NS_CREATE_SIGNAL"],
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
        confidence=0.85,
        primary_intent="NS_CREATE_SIGNAL",
    )


def _update_result() -> TraderParseResult:
    return TraderParseResult(
        message_type="UPDATE",
        intents=["U_CLOSE_FULL"],
        entities={"close_scope": "FULL"},
        target_refs=[{"kind": "reply", "ref": 9000}],
        confidence=0.75,
        primary_intent="U_CLOSE_FULL",
    )


def _build_router(tmp_path: Path, *, wire_v1: bool = True) -> tuple[MessageRouter, ParseResultV1Store]:
    db_path = str(tmp_path / "router.sqlite3")
    apply_migrations(db_path=db_path, migrations_dir=_migrations_dir())

    v1_store = ParseResultV1Store(db_path=db_path)

    router = MessageRouter(
        effective_trader_resolver=MagicMock(**{
            "resolve.return_value": MagicMock(trader_id="trader_a", method="config", detail=None)
        }),
        eligibility_evaluator=MagicMock(**{
            "evaluate.return_value": MagicMock(
                status="ACQUIRED_ELIGIBLE", reason="eligible", strong_link_method=None
            )
        }),
        parse_results_store=MagicMock(),
        processing_status_store=MagicMock(),
        review_queue_store=ReviewQueueStore(db_path=db_path),
        raw_message_store=MagicMock(**{"get_by_source_and_message_id.return_value": None}),
        logger=MagicMock(),
        channels_config=_config(),
        parse_results_v1_store=v1_store if wire_v1 else None,
    )
    return router, v1_store


# ---------------------------------------------------------------------------
# Tests: constructor wiring
# ---------------------------------------------------------------------------

class TestRouterCanonicalV1Constructor:
    def test_v1_store_via_constructor_writes_row_without_enable_call(self, tmp_path: Path) -> None:
        """v1 normalization fires when store is passed via constructor, no enable_shadow_normalizer needed."""
        router, v1_store = _build_router(tmp_path, wire_v1=True)

        with patch("src.telegram.router.get_profile_parser") as mock_factory:
            mock_parser = MagicMock()
            mock_parser.parse_message.return_value = _signal_result()
            mock_factory.return_value = mock_parser
            router.route(_item(raw_message_id=10))

        record = v1_store.get_by_raw_message_id(10)
        assert record is not None
        assert record.trader_id == "trader_a"
        assert record.primary_class == "SIGNAL"

    def test_no_v1_store_means_no_v1_row(self, tmp_path: Path) -> None:
        """Router without v1 store does NOT write canonical rows."""
        router, v1_store = _build_router(tmp_path, wire_v1=False)

        # Give the store a separate DB so we can check it's empty
        db_path = str(tmp_path / "router.sqlite3")
        check_store = ParseResultV1Store(db_path=db_path)

        with patch("src.telegram.router.get_profile_parser") as mock_factory:
            mock_parser = MagicMock()
            mock_parser.parse_message.return_value = _signal_result()
            mock_factory.return_value = mock_parser
            router.route(_item(raw_message_id=11))

        assert check_store.get_by_raw_message_id(11) is None

    def test_disable_shadow_normalizer_clears_store(self, tmp_path: Path) -> None:
        """disable_shadow_normalizer() stops v1 rows even when constructor wired it."""
        router, v1_store = _build_router(tmp_path, wire_v1=True)
        router.disable_shadow_normalizer()

        with patch("src.telegram.router.get_profile_parser") as mock_factory:
            mock_parser = MagicMock()
            mock_parser.parse_message.return_value = _signal_result()
            mock_factory.return_value = mock_parser
            router.route(_item(raw_message_id=12))

        assert v1_store.get_by_raw_message_id(12) is None

    def test_enable_shadow_normalizer_still_works_as_override(self, tmp_path: Path) -> None:
        """enable_shadow_normalizer() on a router without constructor store activates v1."""
        router, v1_store = _build_router(tmp_path, wire_v1=False)
        router.enable_shadow_normalizer(v1_store)

        with patch("src.telegram.router.get_profile_parser") as mock_factory:
            mock_parser = MagicMock()
            mock_parser.parse_message.return_value = _signal_result()
            mock_factory.return_value = mock_parser
            router.route(_item(raw_message_id=13))

        record = v1_store.get_by_raw_message_id(13)
        assert record is not None
        assert record.primary_class == "SIGNAL"


# ---------------------------------------------------------------------------
# Tests: canonical output quality
# ---------------------------------------------------------------------------

class TestCanonicalOutputQuality:
    def test_canonical_json_is_valid_and_has_schema_version(self, tmp_path: Path) -> None:
        router, v1_store = _build_router(tmp_path)

        with patch("src.telegram.router.get_profile_parser") as mock_factory:
            mock_parser = MagicMock()
            mock_parser.parse_message.return_value = _signal_result()
            mock_factory.return_value = mock_parser
            router.route(_item(raw_message_id=20))

        record = v1_store.get_by_raw_message_id(20)
        assert record is not None
        assert record.normalizer_error is None
        data = json.loads(record.canonical_json)
        assert data.get("schema_version") == "1.0"
        assert "primary_class" in data

    def test_update_message_produces_update_class(self, tmp_path: Path) -> None:
        router, v1_store = _build_router(tmp_path)

        with patch("src.telegram.router.get_profile_parser") as mock_factory:
            mock_parser = MagicMock()
            mock_parser.parse_message.return_value = _update_result()
            mock_factory.return_value = mock_parser
            router.route(_item(raw_message_id=21, reply_to_message_id=9000))

        record = v1_store.get_by_raw_message_id(21)
        assert record is not None
        assert record.primary_class == "UPDATE"

    def test_normalizer_error_stored_and_route_completes(self, tmp_path: Path) -> None:
        """Normalizer crash is captured; legacy parse_results.upsert still called."""
        router, v1_store = _build_router(tmp_path)

        with patch("src.telegram.router.get_profile_parser") as mock_factory, \
             patch("src.parser.canonical_v1.normalizer.normalize", side_effect=RuntimeError("norm_crash")):
            mock_parser = MagicMock()
            mock_parser.parse_message.return_value = _signal_result()
            mock_factory.return_value = mock_parser
            router.route(_item(raw_message_id=22))

        record = v1_store.get_by_raw_message_id(22)
        assert record is not None
        assert record.normalizer_error is not None
        assert "norm_crash" in record.normalizer_error
        router._parse_results.upsert.assert_called_once()  # type: ignore[attr-defined]

    def test_legacy_upsert_always_called_regardless_of_v1(self, tmp_path: Path) -> None:
        """parse_results (legacy) is always written; v1 is additive."""
        router, v1_store = _build_router(tmp_path)

        with patch("src.telegram.router.get_profile_parser") as mock_factory:
            mock_parser = MagicMock()
            mock_parser.parse_message.return_value = _signal_result()
            mock_factory.return_value = mock_parser
            router.route(_item(raw_message_id=23))

        router._parse_results.upsert.assert_called_once()  # type: ignore[attr-defined]
        assert v1_store.get_by_raw_message_id(23) is not None

    def test_confidence_persisted_in_v1_record(self, tmp_path: Path) -> None:
        router, v1_store = _build_router(tmp_path)

        with patch("src.telegram.router.get_profile_parser") as mock_factory:
            mock_parser = MagicMock()
            mock_parser.parse_message.return_value = _signal_result()
            mock_factory.return_value = mock_parser
            router.route(_item(raw_message_id=24))

        record = v1_store.get_by_raw_message_id(24)
        assert record is not None
        assert 0.0 < record.confidence <= 1.0
