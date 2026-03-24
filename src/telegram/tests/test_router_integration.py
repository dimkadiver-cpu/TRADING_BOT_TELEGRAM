"""Integration tests for MessageRouter — real stores, real parser, real DB.

Differenza rispetto a test_router.py (unit):
- ProcessingStatusStore, ParseResultStore, ReviewQueueStore, RawMessageStore REALI su SQLite
- parser reale via get_profile_parser (nessun mock sul layer di parsing)
- solo EffectiveTraderResolver resta mockato (dipende da alias runtime e DB live)

Scenari coperti:
  blacklisted   → processing_status = blacklisted, nessuna entry in parse_results
  unresolved    → processing_status = review, entry in review_queue
  parse OK      → processing_status = done, entry in parse_results con resolved_trader_id corretto
  exception     → processing_status = failed, nessuna entry in parse_results
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

from src.core.migrations import apply_migrations
from src.storage.parse_results import ParseResultStore
from src.storage.processing_status import ProcessingStatusStore
from src.storage.raw_messages import RawMessageRecord, RawMessageStore
from src.storage.review_queue import ReviewQueueStore
from src.telegram.channel_config import ChannelEntry, ChannelsConfig
from src.telegram.effective_trader import EffectiveTraderResolver
from src.telegram.eligibility import MessageEligibilityEvaluator
from src.telegram.router import MessageRouter, QueueItem

_CHAT_ID = "-100999"
_CHAT_ID_INT = -100999


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_db(tmp_path: Path) -> str:
    db_path = str(tmp_path / "integration.sqlite3")
    apply_migrations(db_path=db_path, migrations_dir=str(Path("db/migrations").resolve()))
    return db_path


def _insert_raw(db_path: str, *, raw_text: str, telegram_message_id: int = 1) -> int:
    store = RawMessageStore(db_path=db_path)
    result = store.save_with_id(RawMessageRecord(
        source_chat_id=_CHAT_ID,
        telegram_message_id=telegram_message_id,
        message_ts="2026-01-01T00:00:00+00:00",
        acquired_at="2026-01-01T00:00:00+00:00",
        raw_text=raw_text,
    ))
    assert result.raw_message_id is not None, "insert raw_message fallito"
    return result.raw_message_id


def _status(db_path: str, raw_message_id: int) -> str:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT processing_status FROM raw_messages WHERE raw_message_id = ?",
            (raw_message_id,),
        ).fetchone()
    assert row is not None, f"raw_message_id={raw_message_id} non trovato nel DB"
    return str(row[0])


def _has_parse_result(db_path: str, raw_message_id: int) -> bool:
    with sqlite3.connect(db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM parse_results WHERE raw_message_id = ?",
            (raw_message_id,),
        ).fetchone()[0]
    return int(count) > 0


def _make_router(
    db_path: str,
    *,
    resolved_trader_id: str | None = "trader_3",
    active: bool = True,
    blacklist_global: list[str] | None = None,
    override_parse_results: object | None = None,
) -> MessageRouter:
    raw_store = RawMessageStore(db_path=db_path)
    resolver = MagicMock(spec=EffectiveTraderResolver)
    resolver.resolve.return_value = MagicMock(
        trader_id=resolved_trader_id, method="content_alias"
    )
    return MessageRouter(
        effective_trader_resolver=resolver,
        eligibility_evaluator=MessageEligibilityEvaluator(raw_store),
        parse_results_store=override_parse_results or ParseResultStore(db_path=db_path),
        processing_status_store=ProcessingStatusStore(db_path=db_path),
        review_queue_store=ReviewQueueStore(db_path=db_path),
        raw_message_store=raw_store,
        logger=MagicMock(),
        channels_config=ChannelsConfig(
            recovery_max_hours=4,
            blacklist_global=blacklist_global or [],
            channels=[ChannelEntry(
                chat_id=_CHAT_ID_INT,
                label="integration_test",
                active=active,
                trader_id=resolved_trader_id,
                blacklist=[],
            )],
        ),
    )


def _item(raw_message_id: int, *, raw_text: str = "test message") -> QueueItem:
    return QueueItem(
        raw_message_id=raw_message_id,
        source_chat_id=_CHAT_ID,
        telegram_message_id=1,
        raw_text=raw_text,
        source_trader_id=None,
        reply_to_message_id=None,
        acquisition_mode="live",
    )


# ---------------------------------------------------------------------------
# test class
# ---------------------------------------------------------------------------

class TestRouterIntegration:

    def test_blacklisted_message(self, tmp_path: Path) -> None:
        """Testo con tag blacklist globale → status=blacklisted, nessun parse_result."""
        db_path = _make_db(tmp_path)
        raw_id = _insert_raw(db_path, raw_text="weekly recap #admin")
        router = _make_router(db_path, blacklist_global=["#admin"])

        router.route(_item(raw_id, raw_text="weekly recap #admin"))

        assert _status(db_path, raw_id) == "blacklisted"
        assert not _has_parse_result(db_path, raw_id)

    def test_unresolved_trader_goes_to_review(self, tmp_path: Path) -> None:
        """Trader non risolto → status=review, entry in review_queue con reason=unresolved_trader."""
        db_path = _make_db(tmp_path)
        raw_id = _insert_raw(db_path, raw_text="close the trade")
        router = _make_router(db_path, resolved_trader_id=None)

        router.route(_item(raw_id, raw_text="close the trade"))

        assert _status(db_path, raw_id) == "review"
        assert not _has_parse_result(db_path, raw_id)
        pending = ReviewQueueStore(db_path=db_path).get_pending()
        assert len(pending) == 1
        assert pending[0].raw_message_id == raw_id
        assert pending[0].reason == "unresolved_trader"

    def test_successful_parse_saves_result(self, tmp_path: Path) -> None:
        """Parser reale trader_3 chiamato → status=done, entry in parse_results con trader corretto."""
        raw_text = "BTC/USDT LONG entry 60000-62000 stop 57000 targets 65000-70000"
        db_path = _make_db(tmp_path)
        raw_id = _insert_raw(db_path, raw_text=raw_text)
        router = _make_router(db_path, resolved_trader_id="trader_3")

        router.route(_item(raw_id, raw_text=raw_text))

        assert _status(db_path, raw_id) == "done"
        assert _has_parse_result(db_path, raw_id)
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT resolved_trader_id FROM parse_results WHERE raw_message_id = ?",
                (raw_id,),
            ).fetchone()
        assert row is not None
        assert row[0] == "trader_3"

    def test_exception_during_persistence_sets_failed(self, tmp_path: Path) -> None:
        """Eccezione in upsert parse_results → status=failed, nessun parse_result nel DB."""
        db_path = _make_db(tmp_path)
        raw_id = _insert_raw(db_path, raw_text="signal text")
        broken_store = MagicMock()
        broken_store.upsert.side_effect = RuntimeError("simulated db failure")
        router = _make_router(
            db_path,
            resolved_trader_id="trader_3",
            override_parse_results=broken_store,
        )

        router.route(_item(raw_id, raw_text="signal text"))

        assert _status(db_path, raw_id) == "failed"
        assert not _has_parse_result(db_path, raw_id)
