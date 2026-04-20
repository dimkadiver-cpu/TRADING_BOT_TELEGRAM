"""Tests for source_topic_id provenance in signals and operational_signals (WP6)."""

from __future__ import annotations

import sqlite3

import pytest

from src.storage.operational_signals_store import OperationalSignalRecord, OperationalSignalsStore
from src.storage.signals_store import SignalRecord, SignalsStore


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------

_SIGNALS_WITH_TOPIC = """
CREATE TABLE signals (
  attempt_key TEXT PRIMARY KEY,
  env TEXT NOT NULL DEFAULT 'T',
  channel_id TEXT NOT NULL,
  root_telegram_id TEXT NOT NULL,
  trader_id TEXT NOT NULL,
  trader_prefix TEXT NOT NULL,
  trader_signal_id INTEGER,
  symbol TEXT,
  side TEXT,
  entry_json TEXT,
  sl REAL,
  tp_json TEXT,
  status TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 0.0,
  raw_text TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  source_topic_id INTEGER
);
"""

_SIGNALS_WITHOUT_TOPIC = """
CREATE TABLE signals (
  attempt_key TEXT PRIMARY KEY,
  env TEXT NOT NULL DEFAULT 'T',
  channel_id TEXT NOT NULL,
  root_telegram_id TEXT NOT NULL,
  trader_id TEXT NOT NULL,
  trader_prefix TEXT NOT NULL,
  trader_signal_id INTEGER,
  symbol TEXT,
  side TEXT,
  entry_json TEXT,
  sl REAL,
  tp_json TEXT,
  status TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 0.0,
  raw_text TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
"""

_OP_SIGNALS_WITH_TOPIC = """
CREATE TABLE operational_signals (
  op_signal_id INTEGER PRIMARY KEY AUTOINCREMENT,
  parse_result_id INTEGER NOT NULL,
  attempt_key TEXT,
  trader_id TEXT NOT NULL,
  message_type TEXT NOT NULL,
  is_blocked INTEGER NOT NULL DEFAULT 0,
  block_reason TEXT,
  risk_mode TEXT,
  risk_pct_of_capital REAL,
  risk_usdt_fixed REAL,
  capital_base_usdt REAL,
  risk_budget_usdt REAL,
  sl_distance_pct REAL,
  position_size_usdt REAL,
  position_size_pct REAL,
  entry_split_json TEXT,
  leverage INTEGER,
  risk_hint_used INTEGER NOT NULL DEFAULT 0,
  management_rules_json TEXT,
  price_corrections_json TEXT,
  applied_rules_json TEXT,
  warnings_json TEXT,
  resolved_target_ids TEXT,
  target_eligibility TEXT,
  target_reason TEXT,
  created_at TEXT NOT NULL,
  source_topic_id INTEGER
);
"""

_OP_SIGNALS_WITHOUT_TOPIC = """
CREATE TABLE operational_signals (
  op_signal_id INTEGER PRIMARY KEY AUTOINCREMENT,
  parse_result_id INTEGER NOT NULL,
  attempt_key TEXT,
  trader_id TEXT NOT NULL,
  message_type TEXT NOT NULL,
  is_blocked INTEGER NOT NULL DEFAULT 0,
  block_reason TEXT,
  risk_mode TEXT,
  risk_pct_of_capital REAL,
  risk_usdt_fixed REAL,
  capital_base_usdt REAL,
  risk_budget_usdt REAL,
  sl_distance_pct REAL,
  position_size_usdt REAL,
  position_size_pct REAL,
  entry_split_json TEXT,
  leverage INTEGER,
  risk_hint_used INTEGER NOT NULL DEFAULT 0,
  management_rules_json TEXT,
  price_corrections_json TEXT,
  applied_rules_json TEXT,
  warnings_json TEXT,
  resolved_target_ids TEXT,
  target_eligibility TEXT,
  target_reason TEXT,
  created_at TEXT NOT NULL
);
"""


def _signal_record(attempt_key: str = "k1", source_topic_id: int | None = None) -> SignalRecord:
    return SignalRecord(
        attempt_key=attempt_key,
        env="T",
        channel_id="-1001",
        root_telegram_id="10",
        trader_id="trader_a",
        trader_prefix="TRAD",
        symbol="BTCUSDT",
        side="LONG",
        entry_json=None,
        sl=None,
        tp_json=None,
        status="PENDING",
        confidence=0.9,
        raw_text="signal",
        created_at="2026-04-20",
        updated_at="2026-04-20",
        source_topic_id=source_topic_id,
    )


def _op_record(source_topic_id: int | None = None) -> OperationalSignalRecord:
    return OperationalSignalRecord(
        parse_result_id=1,
        attempt_key="k1",
        trader_id="trader_a",
        message_type="NEW_SIGNAL",
        is_blocked=False,
        block_reason=None,
        risk_mode=None,
        risk_pct_of_capital=None,
        risk_usdt_fixed=None,
        capital_base_usdt=None,
        risk_budget_usdt=None,
        sl_distance_pct=None,
        position_size_usdt=None,
        position_size_pct=None,
        entry_split_json=None,
        leverage=None,
        risk_hint_used=False,
        management_rules_json=None,
        price_corrections_json=None,
        applied_rules_json="[]",
        warnings_json=None,
        resolved_target_ids=None,
        target_eligibility=None,
        target_reason=None,
        created_at="2026-04-20",
        source_topic_id=source_topic_id,
    )


# ---------------------------------------------------------------------------
# signals — source_topic_id
# ---------------------------------------------------------------------------


def test_signals_store_inserts_topic_id(tmp_path) -> None:
    db = str(tmp_path / "db.sqlite3")
    with sqlite3.connect(db) as conn:
        conn.executescript(_SIGNALS_WITH_TOPIC)
    store = SignalsStore(db_path=db)
    store.insert(_signal_record(source_topic_id=3))
    with sqlite3.connect(db) as conn:
        row = conn.execute("SELECT source_topic_id FROM signals WHERE attempt_key='k1'").fetchone()
    assert row is not None
    assert row[0] == 3


def test_signals_store_inserts_none_topic(tmp_path) -> None:
    db = str(tmp_path / "db.sqlite3")
    with sqlite3.connect(db) as conn:
        conn.executescript(_SIGNALS_WITH_TOPIC)
    store = SignalsStore(db_path=db)
    store.insert(_signal_record(source_topic_id=None))
    with sqlite3.connect(db) as conn:
        row = conn.execute("SELECT source_topic_id FROM signals WHERE attempt_key='k1'").fetchone()
    assert row is not None
    assert row[0] is None


def test_signals_store_legacy_schema_inserts_without_topic(tmp_path) -> None:
    """On legacy schema (no column), INSERT succeeds and record is saved."""
    db = str(tmp_path / "db.sqlite3")
    with sqlite3.connect(db) as conn:
        conn.executescript(_SIGNALS_WITHOUT_TOPIC)
    store = SignalsStore(db_path=db)
    store.insert(_signal_record(source_topic_id=5))
    with sqlite3.connect(db) as conn:
        row = conn.execute("SELECT attempt_key FROM signals WHERE attempt_key='k1'").fetchone()
    assert row is not None


# ---------------------------------------------------------------------------
# operational_signals — source_topic_id
# ---------------------------------------------------------------------------


def test_op_signals_store_inserts_topic_id(tmp_path) -> None:
    db = str(tmp_path / "db.sqlite3")
    with sqlite3.connect(db) as conn:
        conn.executescript(_OP_SIGNALS_WITH_TOPIC)
    store = OperationalSignalsStore(db_path=db)
    op_id = store.insert(_op_record(source_topic_id=4))
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT source_topic_id FROM operational_signals WHERE op_signal_id=?", (op_id,)
        ).fetchone()
    assert row is not None
    assert row[0] == 4


def test_op_signals_store_inserts_none_topic(tmp_path) -> None:
    db = str(tmp_path / "db.sqlite3")
    with sqlite3.connect(db) as conn:
        conn.executescript(_OP_SIGNALS_WITH_TOPIC)
    store = OperationalSignalsStore(db_path=db)
    op_id = store.insert(_op_record(source_topic_id=None))
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT source_topic_id FROM operational_signals WHERE op_signal_id=?", (op_id,)
        ).fetchone()
    assert row is not None
    assert row[0] is None


def test_op_signals_store_legacy_schema_inserts_without_topic(tmp_path) -> None:
    """On legacy schema (no column), INSERT succeeds and returns a valid op_signal_id."""
    db = str(tmp_path / "db.sqlite3")
    with sqlite3.connect(db) as conn:
        conn.executescript(_OP_SIGNALS_WITHOUT_TOPIC)
    store = OperationalSignalsStore(db_path=db)
    op_id = store.insert(_op_record(source_topic_id=7))
    assert isinstance(op_id, int)
    assert op_id > 0
