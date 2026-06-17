# tests/runtime_v2/lifecycle/test_position_closed_trader_link.py
"""
TDD tests for propagating the trader-update source link through to
POSITION_CLOSED notifications when source=trader_update.

Flow under test:
  1. entry_gate._persist_update injects source_message_link into the
     CLOSE_FULL / CLOSE_PARTIAL command payload_json.
  2. repositories.get_command_source_link reads source_message_link from
     the command payload (new method).
  3. repositories.insert_raw_and_classified (WS path) resolves command_source
     and source_message_link from the command payload when order_link_id is set,
     and includes both in the ExchangeEventPayload.
  4. outbox_writer reads ev.get("source_message_link") to populate the link field.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository


# ---------------------------------------------------------------------------
# Minimal DB helpers
# ---------------------------------------------------------------------------

def _make_ops_db(tmp_path: Path) -> str:
    db_path = str(tmp_path / "ops.sqlite3")
    for f in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn = sqlite3.connect(db_path)
        conn.executescript(f.read_text(encoding="utf-8"))
        conn.commit()
        conn.close()
    return db_path


def _make_minimal_ops_db(tmp_path: Path, suffix: str = "ops.sqlite3") -> str:
    """Minimal schema — no migrations needed."""
    db_path = str(tmp_path / suffix)
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS ops_execution_commands (
            command_id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_chain_id INTEGER,
            command_type TEXT,
            status TEXT DEFAULT 'PENDING',
            payload_json TEXT DEFAULT '{}',
            idempotency_key TEXT UNIQUE,
            client_order_id TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS ops_trade_chains (
            trade_chain_id INTEGER PRIMARY KEY,
            symbol TEXT,
            side TEXT,
            lifecycle_state TEXT,
            updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS exchange_raw_events (
            raw_event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            exchange_event_id TEXT NOT NULL,
            source_stream TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            create_type TEXT,
            stop_order_type TEXT,
            exec_type TEXT,
            order_status TEXT,
            order_link_id TEXT,
            order_id TEXT,
            seq INTEGER,
            exec_price REAL,
            exec_qty REAL,
            closed_size REAL,
            leaves_qty REAL,
            pos_qty REAL,
            exec_value REAL,
            exec_fee REAL,
            fee_rate REAL,
            cum_exec_qty REAL,
            position_take_profit REAL,
            position_stop_loss REAL,
            classified_event_type TEXT,
            classified_source TEXT,
            trade_chain_id INTEGER,
            tp_level INTEGER,
            forwarded_to_lifecycle INTEGER DEFAULT 0,
            forwarded_at TEXT,
            raw_info_json TEXT NOT NULL DEFAULT '{}',
            exchange_time TEXT,
            received_at TEXT NOT NULL,
            idempotency_key TEXT UNIQUE NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ops_exchange_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_chain_id INTEGER,
            event_type TEXT,
            payload_json TEXT,
            processing_status TEXT DEFAULT 'NEW',
            idempotency_key TEXT UNIQUE,
            received_at TEXT
        );
        CREATE TABLE IF NOT EXISTS ops_lifecycle_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_chain_id INTEGER,
            event_type TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_id TEXT,
            previous_state TEXT,
            next_state TEXT,
            payload_json TEXT NOT NULL DEFAULT '{}',
            idempotency_key TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        );
    """)
    conn.commit()
    conn.close()
    return db_path


def _insert_command(
    conn: sqlite3.Connection,
    command_id: int,
    trade_chain_id: int,
    command_type: str,
    payload: dict,
    idempotency_key: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO ops_execution_commands "
        "(command_id, trade_chain_id, command_type, status, payload_json, idempotency_key) "
        "VALUES (?,?,?,?,?,?)",
        (command_id, trade_chain_id, command_type, "PENDING",
         json.dumps(payload), idempotency_key or f"cmd:{command_id}"),
    )


# ---------------------------------------------------------------------------
# Group 1: get_command_source_link (new method on GatewayCommandRepository)
# ---------------------------------------------------------------------------

class TestGetCommandSourceLink:
    def test_returns_link_when_present(self, tmp_path):
        """get_command_source_link returns the link stored in the command payload."""
        db_path = _make_minimal_ops_db(tmp_path)
        link = "https://t.me/c/12345/67"
        conn = sqlite3.connect(db_path)
        _insert_command(conn, command_id=1, trade_chain_id=10, command_type="CLOSE_FULL", payload={
            "symbol": "BTCUSDT",
            "side": "LONG",
            "command_source": "trader_update",
            "source_message_link": link,
        })
        conn.commit()
        conn.close()

        repo = GatewayCommandRepository(db_path)
        assert repo.get_command_source_link(10, 1) == link

    def test_returns_none_when_absent(self, tmp_path):
        """get_command_source_link returns None when payload has no source_message_link."""
        db_path = _make_minimal_ops_db(tmp_path)
        conn = sqlite3.connect(db_path)
        _insert_command(conn, command_id=2, trade_chain_id=10, command_type="CLOSE_FULL", payload={
            "symbol": "BTCUSDT",
            "side": "LONG",
            "command_source": "trader_update",
        })
        conn.commit()
        conn.close()

        repo = GatewayCommandRepository(db_path)
        assert repo.get_command_source_link(10, 2) is None

    def test_returns_none_for_unknown_command(self, tmp_path):
        """get_command_source_link returns None for unknown command_id."""
        db_path = _make_minimal_ops_db(tmp_path)
        repo = GatewayCommandRepository(db_path)
        assert repo.get_command_source_link(99, 999) is None


# ---------------------------------------------------------------------------
# Group 2: WS path — insert_raw_and_classified carries source_message_link
# ---------------------------------------------------------------------------

class TestInsertRawAndClassifiedSourceLink:
    def _make_raw_event(self, order_link_id: str, event_id: str = "evt-001") -> "ExchangeRawEvent":
        from src.runtime_v2.execution_gateway.event_ingest.models import ExchangeRawEvent
        return ExchangeRawEvent(
            source_stream="watch_my_trades",
            exchange_event_id=event_id,
            idempotency_key=event_id,
            symbol="BTCUSDT",
            side="Buy",
            create_type=None,
            stop_order_type=None,
            exec_type="Trade",
            order_status="Filled",
            order_link_id=order_link_id,
            order_id=f"order-{event_id}",
            seq=2001,
            exec_price=50000.0,
            exec_qty=0.01,
            closed_size=0.01,
            leaves_qty=0.0,
            pos_qty=0.0,
            exec_value=500.0,
            exec_fee=0.2,
            fee_rate=0.0004,
            cum_exec_qty=0.01,
            position_take_profit=None,
            position_stop_loss=None,
            exchange_time="2026-06-17T10:00:00Z",
            received_at="2026-06-17T10:00:01Z",
            raw_info={},
        )

    def _make_classified(self, raw, event_type: str = "CLOSE_FULL_FILLED",
                          trade_chain_id: int = 1) -> "ClassifiedEvent":
        from src.runtime_v2.execution_gateway.event_ingest.models import ClassifiedEvent
        return ClassifiedEvent(
            raw=raw,
            event_type=event_type,
            source="exchange",
            trade_chain_id=trade_chain_id,
            tp_level=None,
            is_actionable=True,
        )

    def test_carries_link_for_close_full_filled(self, tmp_path):
        """
        CLOSE_FULL_FILLED via WS: when command payload has source_message_link,
        the ops_exchange_events payload includes it and source=trader_update.
        """
        db_path = _make_minimal_ops_db(tmp_path)
        link = "https://t.me/c/99999/42"

        conn = sqlite3.connect(db_path)
        _insert_command(conn, command_id=5, trade_chain_id=1, command_type="CLOSE_FULL", payload={
            "symbol": "BTCUSDT",
            "side": "LONG",
            "command_source": "trader_update",
            "source_message_link": link,
        })
        conn.commit()
        conn.close()

        # order_link_id format: tsb:{chain_id}:{command_id}:{role}:{seq}
        raw = self._make_raw_event(order_link_id="tsb:1:5:exit_full:1")
        classified = self._make_classified(raw)

        repo = GatewayCommandRepository(db_path)
        repo.insert_raw_and_classified(classified)

        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT payload_json FROM ops_exchange_events WHERE event_type='CLOSE_FULL_FILLED'"
        ).fetchone()
        conn.close()

        assert row is not None, "No CLOSE_FULL_FILLED event inserted"
        payload = json.loads(row[0])
        assert payload.get("source") == "trader_update", (
            f"Expected source=trader_update, got {payload.get('source')!r}"
        )
        assert payload.get("source_message_link") == link, (
            f"Expected link={link!r}, got {payload.get('source_message_link')!r}"
        )

    def test_no_link_when_command_has_none(self, tmp_path):
        """
        CLOSE_FULL_FILLED via WS: when command has no source_message_link,
        the payload must not carry one.
        """
        db_path = _make_minimal_ops_db(tmp_path)

        conn = sqlite3.connect(db_path)
        _insert_command(conn, command_id=7, trade_chain_id=2, command_type="CLOSE_FULL", payload={
            "symbol": "ETHUSDT",
            "side": "SHORT",
            "command_source": "trader_update",
            # no source_message_link
        })
        conn.commit()
        conn.close()

        raw = self._make_raw_event(order_link_id="tsb:2:7:exit_full:1", event_id="evt-002")
        classified = self._make_classified(raw, trade_chain_id=2)

        repo = GatewayCommandRepository(db_path)
        repo.insert_raw_and_classified(classified)

        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT payload_json FROM ops_exchange_events WHERE event_type='CLOSE_FULL_FILLED'"
        ).fetchone()
        conn.close()

        assert row is not None
        payload = json.loads(row[0])
        assert payload.get("source_message_link") is None

    def test_no_link_when_no_order_link_id(self, tmp_path):
        """
        When the raw event has no order_link_id, source remains as classified.source
        and source_message_link stays None.
        """
        db_path = _make_minimal_ops_db(tmp_path)

        raw = self._make_raw_event(order_link_id="", event_id="evt-003")
        # Manually override order_link_id to None
        from src.runtime_v2.execution_gateway.event_ingest.models import (
            ClassifiedEvent, ExchangeRawEvent,
        )
        raw2 = ExchangeRawEvent(
            source_stream="watch_my_trades",
            exchange_event_id="evt-003",
            idempotency_key="evt-003",
            symbol="BTCUSDT",
            side="Buy",
            create_type=None,
            stop_order_type=None,
            exec_type="Trade",
            order_status="Filled",
            order_link_id=None,
            order_id="order-evt-003",
            seq=2003,
            exec_price=50000.0,
            exec_qty=0.01,
            closed_size=0.01,
            leaves_qty=0.0,
            pos_qty=0.0,
            exec_value=500.0,
            exec_fee=0.2,
            fee_rate=0.0004,
            cum_exec_qty=0.01,
            position_take_profit=None,
            position_stop_loss=None,
            exchange_time="2026-06-17T10:00:00Z",
            received_at="2026-06-17T10:00:01Z",
            raw_info={},
        )
        classified = ClassifiedEvent(
            raw=raw2,
            event_type="CLOSE_FULL_FILLED",
            source="exchange",
            trade_chain_id=3,
            tp_level=None,
            is_actionable=True,
        )

        repo = GatewayCommandRepository(db_path)
        repo.insert_raw_and_classified(classified)

        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT payload_json FROM ops_exchange_events WHERE event_type='CLOSE_FULL_FILLED'"
        ).fetchone()
        conn.close()

        assert row is not None
        payload = json.loads(row[0])
        assert payload.get("source_message_link") is None


# ---------------------------------------------------------------------------
# Group 3: entry_gate._persist_update injects link into command payload
# ---------------------------------------------------------------------------

class TestPersistUpdateInjectsLink:
    def _make_parser_db(self, tmp_path: Path, raw_message_id: int,
                        source_chat_id: str | None, telegram_message_id: str | None,
                        suffix: str = "parser.sqlite3") -> str:
        db_path = str(tmp_path / suffix)
        conn = sqlite3.connect(db_path)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS raw_messages (
                raw_message_id INTEGER PRIMARY KEY,
                source_chat_id TEXT,
                telegram_message_id TEXT,
                raw_text TEXT DEFAULT '',
                processing_status TEXT DEFAULT 'pending',
                created_at TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS enriched_canonical_messages (
                enrichment_id INTEGER PRIMARY KEY,
                canonical_message_id INTEGER,
                lifecycle_processed INTEGER DEFAULT 0
            );
        """)
        conn.execute(
            "INSERT INTO raw_messages (raw_message_id, source_chat_id, telegram_message_id) "
            "VALUES (?,?,?)",
            (raw_message_id, source_chat_id, telegram_message_id),
        )
        # Insert enrichment_id row matching what the test will pass
        conn.execute(
            "INSERT INTO enriched_canonical_messages (enrichment_id, canonical_message_id) "
            "VALUES (?,?)",
            (raw_message_id, raw_message_id),
        )
        conn.commit()
        conn.close()
        return db_path

    def _make_gate(self, ops_db: str, parser_db: str):
        from src.runtime_v2.lifecycle.entry_gate import LifecycleGateWorker
        gate = LifecycleGateWorker.__new__(LifecycleGateWorker)
        gate._ops_db = ops_db
        gate._parser_db = parser_db
        return gate

    def _seed_chain(self, ops_db: str, chain_id: int) -> None:
        conn = sqlite3.connect(ops_db)
        now = "2026-06-17T00:00:00Z"
        conn.execute(
            "INSERT OR IGNORE INTO ops_trade_chains "
            "(trade_chain_id, source_enrichment_id, canonical_message_id, raw_message_id, "
            " trader_id, account_id, symbol, side, lifecycle_state, entry_mode, "
            " management_plan_json, risk_snapshot_json, plan_state_json, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (chain_id, chain_id, chain_id, chain_id, "trader_a", "main", "BTC/USDT", "LONG",
             "OPEN", "ONE_SHOT", "{}", "{}", "{}", now, now),
        )
        conn.commit()
        conn.close()

    def _make_enriched(self, raw_message_id: int, canonical_message_id: int):
        from src.runtime_v2.signal_enrichment.models import EnrichedCanonicalMessage
        enriched = MagicMock(spec=EnrichedCanonicalMessage)
        enriched.raw_message_id = raw_message_id
        enriched.canonical_message_id = canonical_message_id
        enriched.enrichment_id = canonical_message_id
        return enriched

    def test_close_full_gets_source_link(self, tmp_path):
        """
        _persist_update writes source_message_link into CLOSE_FULL payload
        when the raw_message has source_chat_id and telegram_message_id.
        """
        from src.runtime_v2.lifecycle.entry_gate import UpdateGateResult
        from src.runtime_v2.lifecycle.models import ExecutionCommand, LifecycleEvent
        from src.runtime_v2.lifecycle.entry_gate import UpdateChainResult

        ops_db = _make_ops_db(tmp_path)
        parser_db = self._make_parser_db(
            tmp_path, raw_message_id=1,
            source_chat_id="-1001234567890",
            telegram_message_id="99",
        )
        self._seed_chain(ops_db, chain_id=10)

        cmd = ExecutionCommand(
            trade_chain_id=10,
            command_type="CLOSE_FULL",
            payload_json=json.dumps({
                "symbol": "BTCUSDT",
                "side": "LONG",
                "command_source": "trader_update",
            }),
            idempotency_key="close_full:10:1",
        )
        result = UpdateGateResult(
            chain_results=[UpdateChainResult(
                trade_chain_id=10,
                new_lifecycle_state=None,
                new_be_protection_status=None,
                lifecycle_events=[],
                execution_commands=[cmd],
            )],
            review_events=[],
        )

        gate = self._make_gate(ops_db, parser_db)
        enriched = self._make_enriched(raw_message_id=1, canonical_message_id=1)
        gate._persist_update(enriched, result)

        conn = sqlite3.connect(ops_db)
        row = conn.execute(
            "SELECT payload_json FROM ops_execution_commands "
            "WHERE command_type='CLOSE_FULL' LIMIT 1"
        ).fetchone()
        conn.close()

        assert row is not None, "CLOSE_FULL command was not inserted"
        stored = json.loads(row[0])
        expected_link = "https://t.me/c/1234567890/99"
        assert stored.get("source_message_link") == expected_link, (
            f"Expected link={expected_link!r}, got {stored.get('source_message_link')!r}"
        )

    def test_close_partial_gets_source_link(self, tmp_path):
        """_persist_update also writes source_message_link into CLOSE_PARTIAL payload."""
        from src.runtime_v2.lifecycle.entry_gate import UpdateGateResult, UpdateChainResult
        from src.runtime_v2.lifecycle.models import ExecutionCommand

        ops_db = _make_ops_db(tmp_path)
        parser_db = self._make_parser_db(
            tmp_path, raw_message_id=2,
            source_chat_id="-1009876543210",
            telegram_message_id="55",
            suffix="parser2.sqlite3",
        )
        self._seed_chain(ops_db, chain_id=20)

        cmd = ExecutionCommand(
            trade_chain_id=20,
            command_type="CLOSE_PARTIAL",
            payload_json=json.dumps({
                "symbol": "ETHUSDT",
                "side": "LONG",
                "fraction": 0.5,
                "command_source": "trader_update",
            }),
            idempotency_key="close_partial:20:2",
        )
        result = UpdateGateResult(
            chain_results=[UpdateChainResult(
                trade_chain_id=20,
                new_lifecycle_state=None,
                new_be_protection_status=None,
                lifecycle_events=[],
                execution_commands=[cmd],
            )],
            review_events=[],
        )

        gate = self._make_gate(ops_db, parser_db)
        enriched = self._make_enriched(raw_message_id=2, canonical_message_id=2)
        gate._persist_update(enriched, result)

        conn = sqlite3.connect(ops_db)
        row = conn.execute(
            "SELECT payload_json FROM ops_execution_commands "
            "WHERE command_type='CLOSE_PARTIAL' LIMIT 1"
        ).fetchone()
        conn.close()

        assert row is not None
        stored = json.loads(row[0])
        expected_link = "https://t.me/c/9876543210/55"
        assert stored.get("source_message_link") == expected_link, (
            f"Expected link={expected_link!r}, got {stored.get('source_message_link')!r}"
        )

    def test_no_link_when_raw_message_has_no_chat_id(self, tmp_path):
        """
        When raw_message has no source_chat_id, source_message_link is NOT injected.
        """
        from src.runtime_v2.lifecycle.entry_gate import UpdateGateResult, UpdateChainResult
        from src.runtime_v2.lifecycle.models import ExecutionCommand

        ops_db = _make_ops_db(tmp_path)
        parser_db = self._make_parser_db(
            tmp_path, raw_message_id=3,
            source_chat_id=None,
            telegram_message_id=None,
            suffix="parser3.sqlite3",
        )
        self._seed_chain(ops_db, chain_id=30)

        cmd = ExecutionCommand(
            trade_chain_id=30,
            command_type="CLOSE_FULL",
            payload_json=json.dumps({
                "symbol": "BTCUSDT",
                "side": "LONG",
                "command_source": "trader_update",
            }),
            idempotency_key="close_full:30:3",
        )
        result = UpdateGateResult(
            chain_results=[UpdateChainResult(
                trade_chain_id=30,
                new_lifecycle_state=None,
                new_be_protection_status=None,
                lifecycle_events=[],
                execution_commands=[cmd],
            )],
            review_events=[],
        )

        gate = self._make_gate(ops_db, parser_db)
        enriched = self._make_enriched(raw_message_id=3, canonical_message_id=3)
        gate._persist_update(enriched, result)

        conn = sqlite3.connect(ops_db)
        row = conn.execute(
            "SELECT payload_json FROM ops_execution_commands "
            "WHERE command_type='CLOSE_FULL' LIMIT 1"
        ).fetchone()
        conn.close()

        assert row is not None
        stored = json.loads(row[0])
        assert "source_message_link" not in stored, (
            f"Did not expect source_message_link, got: {stored}"
        )

    def test_non_close_command_not_modified(self, tmp_path):
        """
        Non-CLOSE commands (e.g. CANCEL_PENDING_ENTRY) must NOT receive source_message_link.
        """
        from src.runtime_v2.lifecycle.entry_gate import UpdateGateResult, UpdateChainResult
        from src.runtime_v2.lifecycle.models import ExecutionCommand

        ops_db = _make_ops_db(tmp_path)
        parser_db = self._make_parser_db(
            tmp_path, raw_message_id=4,
            source_chat_id="-1001111111111",
            telegram_message_id="77",
            suffix="parser4.sqlite3",
        )
        self._seed_chain(ops_db, chain_id=40)

        # This command is not a close command
        cmd = ExecutionCommand(
            trade_chain_id=40,
            command_type="CANCEL_PENDING_ENTRY",
            payload_json=json.dumps({"symbol": "BTCUSDT", "side": "LONG"}),
            idempotency_key="cancel_pending:40:4",
        )
        result = UpdateGateResult(
            chain_results=[UpdateChainResult(
                trade_chain_id=40,
                new_lifecycle_state=None,
                new_be_protection_status=None,
                lifecycle_events=[],
                execution_commands=[cmd],
            )],
            review_events=[],
        )

        gate = self._make_gate(ops_db, parser_db)
        enriched = self._make_enriched(raw_message_id=4, canonical_message_id=4)
        gate._persist_update(enriched, result)

        conn = sqlite3.connect(ops_db)
        row = conn.execute(
            "SELECT payload_json FROM ops_execution_commands "
            "WHERE command_type='CANCEL_PENDING_ENTRY' LIMIT 1"
        ).fetchone()
        conn.close()

        if row:
            stored = json.loads(row[0])
            assert "source_message_link" not in stored
