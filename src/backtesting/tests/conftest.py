"""Shared fixtures for backtesting tests.

Creates an in-memory SQLite database with the full schema applied from all
migrations, and provides helpers for inserting test data.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import aiosqlite
import pytest
import pytest_asyncio

# ---------------------------------------------------------------------------
# Schema creation SQL
# ---------------------------------------------------------------------------

_CREATE_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS backtest_runs (
    run_id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario_name             TEXT NOT NULL,
    scenario_conditions_json  TEXT NOT NULL,
    trader_filter             TEXT,
    date_from                 TEXT,
    date_to                   TEXT,
    chains_count              INTEGER NOT NULL DEFAULT 0,
    chains_blocked            INTEGER NOT NULL DEFAULT 0,
    run_ts                    TEXT NOT NULL,
    status                    TEXT NOT NULL,
    error                     TEXT,
    output_dir                TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS backtest_trades (
    bt_trade_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id             INTEGER NOT NULL REFERENCES backtest_runs(run_id),
    chain_id           TEXT NOT NULL,
    trader_id          TEXT NOT NULL,
    pair               TEXT NOT NULL,
    side               TEXT NOT NULL,
    open_date          TEXT NOT NULL,
    close_date         TEXT,
    entry_price        REAL NOT NULL,
    close_price        REAL,
    profit_usdt        REAL,
    profit_pct         REAL,
    exit_reason        TEXT,
    max_drawdown_pct   REAL,
    duration_seconds   INTEGER,
    sl_moved_to_be     INTEGER NOT NULL DEFAULT 0,
    raw_freqtrade_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_bt_trades_run ON backtest_trades(run_id);
CREATE INDEX IF NOT EXISTS idx_bt_trades_chain ON backtest_trades(chain_id);

CREATE TABLE IF NOT EXISTS schema_migrations (
  version INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS signals (
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

CREATE TABLE IF NOT EXISTS raw_messages (
  raw_message_id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_chat_id TEXT NOT NULL,
  source_chat_title TEXT,
  source_type TEXT,
  source_trader_id TEXT,
  telegram_message_id INTEGER NOT NULL,
  reply_to_message_id INTEGER,
  raw_text TEXT,
  message_ts TEXT NOT NULL,
  acquired_at TEXT NOT NULL,
  acquisition_status TEXT NOT NULL DEFAULT 'ACQUIRED',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  processing_status TEXT NOT NULL DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS parse_results (
  parse_result_id INTEGER PRIMARY KEY AUTOINCREMENT,
  raw_message_id INTEGER NOT NULL,
  eligibility_status TEXT NOT NULL,
  eligibility_reason TEXT,
  declared_trader_tag TEXT,
  resolved_trader_id TEXT,
  trader_resolution_method TEXT,
  message_type TEXT NOT NULL,
  parse_status TEXT NOT NULL,
  completeness TEXT NOT NULL,
  is_executable INTEGER NOT NULL DEFAULT 0,
  symbol TEXT,
  direction TEXT,
  entry_raw TEXT,
  stop_raw TEXT,
  target_raw_list TEXT,
  leverage_hint TEXT,
  risk_hint TEXT,
  risky_flag INTEGER NOT NULL DEFAULT 0,
  linkage_method TEXT,
  linkage_status TEXT,
  warning_text TEXT,
  notes TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  parse_result_normalized_json TEXT,
  FOREIGN KEY(raw_message_id) REFERENCES raw_messages(raw_message_id)
);

CREATE TABLE IF NOT EXISTS operational_signals (
  op_signal_id           INTEGER PRIMARY KEY AUTOINCREMENT,
  parse_result_id        INTEGER NOT NULL
                           REFERENCES parse_results(parse_result_id),
  attempt_key            TEXT REFERENCES signals(attempt_key),
  trader_id              TEXT NOT NULL,
  message_type           TEXT NOT NULL,
  is_blocked             INTEGER NOT NULL DEFAULT 0,
  block_reason           TEXT,
  position_size_pct      REAL,
  position_size_usdt     REAL,
  entry_split_json       TEXT,
  leverage               INTEGER,
  risk_hint_used         INTEGER NOT NULL DEFAULT 0,
  management_rules_json  TEXT,
  price_corrections_json TEXT,
  applied_rules_json     TEXT,
  warnings_json          TEXT,
  resolved_target_ids    TEXT,
  target_eligibility     TEXT,
  target_reason          TEXT,
  created_at             TEXT NOT NULL,
  risk_mode              TEXT,
  risk_pct_of_capital    REAL,
  risk_usdt_fixed        REAL,
  capital_base_usdt      REAL,
  risk_budget_usdt       REAL,
  sl_distance_pct        REAL
);
"""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def test_db() -> aiosqlite.Connection:
    """In-memory SQLite database with the full schema applied."""
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    await db.executescript(_CREATE_SCHEMA)
    await db.commit()
    yield db
    await db.close()


@pytest_asyncio.fixture
async def test_db_path(tmp_path) -> str:
    """Path to a temporary on-disk SQLite database with the full schema.

    Returned as a string path so SignalChainBuilder.build_all() can open it.
    """
    db_file = tmp_path / "test.sqlite3"
    async with aiosqlite.connect(str(db_file)) as db:
        await db.executescript(_CREATE_SCHEMA)
        await db.commit()
    return str(db_file)


# ---------------------------------------------------------------------------
# Insert helpers
# ---------------------------------------------------------------------------

async def insert_raw_message(
    db: aiosqlite.Connection,
    *,
    source_chat_id: str = "chat_001",
    telegram_message_id: int,
    message_ts: str,
    reply_to_message_id: int | None = None,
    source_trader_id: str | None = None,
    raw_text: str = "test message",
) -> int:
    """Insert a raw_messages row and return raw_message_id."""
    cursor = await db.execute(
        """
        INSERT INTO raw_messages(
            source_chat_id, source_trader_id, telegram_message_id,
            reply_to_message_id, raw_text, message_ts, acquired_at,
            acquisition_status, created_at, processing_status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_chat_id,
            source_trader_id,
            telegram_message_id,
            reply_to_message_id,
            raw_text,
            message_ts,
            message_ts,
            "ACQUIRED",
            message_ts,
            "done",
        ),
    )
    await db.commit()
    return cursor.lastrowid  # type: ignore[return-value]


async def insert_parse_result(
    db: aiosqlite.Connection,
    *,
    raw_message_id: int,
    message_type: str,
    normalized_json: str | None = None,
    symbol: str | None = None,
    direction: str | None = None,
) -> int:
    """Insert a parse_results row and return parse_result_id."""
    now = "2025-01-01T00:00:00"
    cursor = await db.execute(
        """
        INSERT INTO parse_results(
            raw_message_id, eligibility_status, message_type, parse_status,
            completeness, is_executable, symbol, direction,
            created_at, updated_at, parse_result_normalized_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            raw_message_id,
            "ELIGIBLE",
            message_type,
            "OK",
            "COMPLETE" if message_type == "NEW_SIGNAL" else "N/A",
            1 if message_type == "NEW_SIGNAL" else 0,
            symbol,
            direction,
            now,
            now,
            normalized_json,
        ),
    )
    await db.commit()
    return cursor.lastrowid  # type: ignore[return-value]


async def insert_signal(
    db: aiosqlite.Connection,
    *,
    attempt_key: str,
    trader_id: str,
    symbol: str,
    side: str,
    channel_id: str = "chat_001",
) -> None:
    """Insert a signals row (needed for FK from operational_signals.attempt_key)."""
    now = "2025-01-01T00:00:00"
    await db.execute(
        """
        INSERT OR IGNORE INTO signals(
            attempt_key, channel_id, root_telegram_id, trader_id, trader_prefix,
            symbol, side, status, confidence, raw_text, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            attempt_key,
            channel_id,
            attempt_key,
            trader_id,
            trader_id[:3],
            symbol,
            side,
            "OPEN",
            0.9,
            "test",
            now,
            now,
        ),
    )
    await db.commit()


async def insert_operational_signal(
    db: aiosqlite.Connection,
    *,
    parse_result_id: int,
    trader_id: str,
    message_type: str,
    attempt_key: str | None = None,
    is_blocked: bool = False,
    block_reason: str | None = None,
    risk_budget_usdt: float | None = None,
    position_size_usdt: float | None = None,
    entry_split_json: str | None = None,
    management_rules_json: str | None = None,
    resolved_target_ids: str | None = None,
) -> int:
    """Insert an operational_signals row and return op_signal_id."""
    now = "2025-01-01T00:00:00"
    cursor = await db.execute(
        """
        INSERT INTO operational_signals(
            parse_result_id, attempt_key, trader_id, message_type,
            is_blocked, block_reason, risk_budget_usdt, position_size_usdt,
            entry_split_json, management_rules_json, resolved_target_ids, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            parse_result_id,
            attempt_key,
            trader_id,
            message_type,
            1 if is_blocked else 0,
            block_reason,
            risk_budget_usdt,
            position_size_usdt,
            entry_split_json,
            management_rules_json,
            resolved_target_ids,
            now,
        ),
    )
    await db.commit()
    return cursor.lastrowid  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# JSON factory helpers
# ---------------------------------------------------------------------------

def make_new_signal_json(
    *,
    symbol: str = "BTCUSDT",
    direction: str = "LONG",
    entry_type: str = "LIMIT",
    entry_price: float = 90000.0,
    sl_price: float = 85000.0,
    tp_prices: list[float] | None = None,
    intents: list[dict[str, str]] | None = None,
) -> str:
    """Generate a valid parse_result_normalized_json for a NEW_SIGNAL."""
    if tp_prices is None:
        tp_prices = [95000.0, 100000.0]
    if intents is None:
        intents = []

    entities = {
        "symbol": symbol,
        "direction": direction,
        "entry_type": entry_type,
        "entries": [{"price": {"raw": str(entry_price), "value": entry_price}, "order_type": "LIMIT"}],
        "stop_loss": {"price": {"raw": str(sl_price), "value": sl_price}, "trailing": False, "condition": None},
        "take_profits": [
            {"price": {"raw": str(p), "value": p}, "label": f"TP{i+1}", "close_pct": None}
            for i, p in enumerate(tp_prices)
        ],
        "leverage": None,
        "risk_pct": None,
        "conditions": None,
        "warnings": [],
    }
    return json.dumps({
        "message_type": "NEW_SIGNAL",
        "intents": intents,
        "entities": entities,
    })


def make_update_json(
    *,
    intents: list[dict[str, str]] | None = None,
    new_sl_level: float | None = None,
    close_pct: float | None = None,
    close_price: float | None = None,
) -> str:
    """Generate a valid parse_result_normalized_json for an UPDATE."""
    if intents is None:
        intents = [{"name": "U_MOVE_STOP", "kind": "ACTION"}]

    entities: dict[str, Any] = {
        "new_sl_level": {"raw": str(new_sl_level), "value": new_sl_level} if new_sl_level else None,
        "close_price": {"raw": str(close_price), "value": close_price} if close_price else None,
        "close_pct": close_pct,
        "reenter_entries": [],
        "reenter_entry_type": None,
        "new_entry_price": None,
        "new_entry_type": None,
        "old_entry_price": None,
        "modified_entry_price": None,
        "old_take_profits": None,
        "new_take_profits": [],
        "tp_hit_number": None,
        "reported_profit_r": None,
        "reported_profit_pct": None,
    }
    return json.dumps({
        "message_type": "UPDATE",
        "intents": intents,
        "entities": entities,
    })
