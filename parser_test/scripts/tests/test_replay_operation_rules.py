"""Tests for replay_operation_rules.py.

Covers:
- Safety check refuses live DB path
- NEW_SIGNAL creates a row in both signals and operational_signals
- UPDATE resolves target and links to existing signal
- Blocked signal is recorded with is_blocked=True in operational_signals
- --dry-run processes without writing to DB
- --trader filter restricts which parse_results are processed
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]

# Full DB schema needed for the script (same tables as backtesting conftest)
_SCHEMA = """
PRAGMA foreign_keys=ON;

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


def _make_db(path: Path) -> str:
    """Create a test SQLite DB with the required schema."""
    db_str = str(path)
    with sqlite3.connect(db_str) as conn:
        conn.executescript(_SCHEMA)
        conn.commit()
    return db_str


def _insert_raw_message(
    conn: sqlite3.Connection,
    *,
    telegram_message_id: int,
    message_ts: str = "2025-06-01T10:00:00",
    source_chat_id: str = "chat_001",
    reply_to_message_id: int | None = None,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO raw_messages (
            source_chat_id, telegram_message_id, reply_to_message_id,
            raw_text, message_ts, acquired_at, acquisition_status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_chat_id,
            telegram_message_id,
            reply_to_message_id,
            "test",
            message_ts,
            message_ts,
            "ACQUIRED",
            message_ts,
        ),
    )
    conn.commit()
    return cursor.lastrowid  # type: ignore[return-value]


def _insert_parse_result(
    conn: sqlite3.Connection,
    *,
    raw_message_id: int,
    message_type: str,
    resolved_trader_id: str = "trader_3",
    normalized_json: str | None = None,
    is_executable: int | None = None,
) -> int:
    if is_executable is None:
        is_executable = 1 if message_type == "NEW_SIGNAL" else 0
    now = "2025-06-01T10:00:00"
    cursor = conn.execute(
        """
        INSERT INTO parse_results (
            raw_message_id, eligibility_status, resolved_trader_id,
            message_type, parse_status, completeness, is_executable,
            created_at, updated_at, parse_result_normalized_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            raw_message_id,
            "ELIGIBLE",
            resolved_trader_id,
            message_type,
            "PARSED",
            "COMPLETE",
            is_executable,
            now,
            now,
            normalized_json,
        ),
    )
    conn.commit()
    return cursor.lastrowid  # type: ignore[return-value]


def _new_signal_json(symbol: str = "BTCUSDT") -> str:
    return json.dumps({
        "message_type": "NEW_SIGNAL",
        "intents": [],
        "entities": {
            "symbol": symbol,
            "direction": "LONG",
            "entry_raw": "90000",
            "stop_raw": "85000",
        },
        "target_refs": [],
        "warnings": [],
        "confidence": 0.9,
    })


def _update_json(reply_to: int | None = None) -> str:
    target_refs: list[dict[str, Any]] = []
    if reply_to is not None:
        target_refs = [{"kind": "REPLY", "ref": reply_to}]
    return json.dumps({
        "message_type": "UPDATE",
        "intents": [{"name": "U_MOVE_STOP", "kind": "ACTION"}],
        "entities": {"new_sl_level": None},
        "target_refs": target_refs,
        "warnings": [],
        "confidence": 0.8,
    })


def _make_mock_op_signal(
    parse_result,
    trader_id: str,
    *,
    is_blocked: bool = False,
    block_reason: str | None = None,
) -> MagicMock:
    sig = MagicMock()
    sig.parse_result = parse_result
    sig.trader_id = trader_id
    sig.is_blocked = is_blocked
    sig.block_reason = block_reason
    sig.risk_mode = None
    sig.risk_pct_of_capital = None
    sig.risk_usdt_fixed = None
    sig.capital_base_usdt = None
    sig.risk_budget_usdt = None
    sig.sl_distance_pct = None
    sig.position_size_usdt = None
    sig.position_size_pct = None
    sig.entry_split = None
    sig.leverage = None
    sig.risk_hint_used = False
    sig.management_rules = None
    sig.applied_rules = ["mocked"]
    sig.warnings = []
    return sig


def _make_mock_resolved_target(position_ids: list[int]) -> MagicMock:
    rt = MagicMock()
    rt.position_ids = position_ids
    rt.eligibility = "ELIGIBLE" if position_ids else "UNRESOLVED"
    rt.reason = None if position_ids else "no_matching_open_signal"
    return rt


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_safety_check_refuses_live_db() -> None:
    from parser_test.scripts.replay_operation_rules import run_replay

    live_db = str((PROJECT_ROOT / "db" / "tele_signal_bot.sqlite3").resolve())
    with pytest.raises(RuntimeError, match="Refusing to run on live DB"):
        run_replay(db_path=live_db)


def test_process_new_signal_creates_signal_and_op_signal(
    tmp_path: Path,
) -> None:
    from parser_test.scripts.replay_operation_rules import run_replay

    db_path = _make_db(tmp_path / "bt.sqlite3")

    with sqlite3.connect(db_path) as conn:
        rm_id = _insert_raw_message(conn, telegram_message_id=100)
        _insert_parse_result(
            conn,
            raw_message_id=rm_id,
            message_type="NEW_SIGNAL",
            normalized_json=_new_signal_json(),
        )

    with (
        patch("parser_test.scripts.replay_operation_rules.apply_migrations"),
        patch(
            "parser_test.scripts.replay_operation_rules.OperationRulesEngine"
        ) as MockEngine,
        patch(
            "parser_test.scripts.replay_operation_rules.TargetResolver"
        ) as MockResolver,
    ):
        mock_engine = MagicMock()
        MockEngine.return_value = mock_engine
        mock_resolver = MagicMock()
        MockResolver.return_value = mock_resolver

        # Engine returns a non-blocked signal
        def fake_apply(parse_result, trader_id, *, db_path, **kwargs):
            return _make_mock_op_signal(parse_result, trader_id)

        mock_engine.apply.side_effect = fake_apply

        stats = run_replay(db_path=db_path, dry_run=False)

    assert stats.total == 1
    assert stats.new_signal_inserted == 1
    assert stats.new_signal_blocked == 0
    assert stats.errors == 0

    with sqlite3.connect(db_path) as conn:
        signals = conn.execute("SELECT * FROM signals").fetchall()
        op_signals = conn.execute("SELECT * FROM operational_signals").fetchall()

    assert len(signals) == 1
    assert len(op_signals) == 1


def test_blocked_signal_marked_correctly(tmp_path: Path) -> None:
    from parser_test.scripts.replay_operation_rules import run_replay

    db_path = _make_db(tmp_path / "bt.sqlite3")

    with sqlite3.connect(db_path) as conn:
        rm_id = _insert_raw_message(conn, telegram_message_id=200)
        _insert_parse_result(
            conn,
            raw_message_id=rm_id,
            message_type="NEW_SIGNAL",
            normalized_json=_new_signal_json(),
        )

    with (
        patch("parser_test.scripts.replay_operation_rules.apply_migrations"),
        patch(
            "parser_test.scripts.replay_operation_rules.OperationRulesEngine"
        ) as MockEngine,
        patch("parser_test.scripts.replay_operation_rules.TargetResolver"),
    ):
        mock_engine = MagicMock()
        MockEngine.return_value = mock_engine

        def fake_apply_blocked(parse_result, trader_id, *, db_path, **kwargs):
            return _make_mock_op_signal(
                parse_result, trader_id, is_blocked=True, block_reason="trader_disabled"
            )

        mock_engine.apply.side_effect = fake_apply_blocked

        stats = run_replay(db_path=db_path, dry_run=False)

    assert stats.new_signal_blocked == 1
    assert stats.new_signal_inserted == 0

    with sqlite3.connect(db_path) as conn:
        signals = conn.execute("SELECT * FROM signals").fetchall()
        op_sigs = conn.execute("SELECT is_blocked, block_reason FROM operational_signals").fetchall()

    # Blocked signals are NOT inserted into signals table
    assert len(signals) == 0
    # But they ARE recorded in operational_signals
    assert len(op_sigs) == 1
    assert op_sigs[0][0] == 1  # is_blocked
    assert op_sigs[0][1] == "trader_disabled"


def test_process_update_resolves_target(tmp_path: Path) -> None:
    from parser_test.scripts.replay_operation_rules import run_replay

    db_path = _make_db(tmp_path / "bt.sqlite3")

    with sqlite3.connect(db_path) as conn:
        # NEW_SIGNAL first (chronologically)
        rm1 = _insert_raw_message(
            conn, telegram_message_id=300, message_ts="2025-06-01T10:00:00"
        )
        _insert_parse_result(
            conn,
            raw_message_id=rm1,
            message_type="NEW_SIGNAL",
            normalized_json=_new_signal_json(),
        )
        # UPDATE after
        rm2 = _insert_raw_message(
            conn,
            telegram_message_id=301,
            message_ts="2025-06-01T11:00:00",
            reply_to_message_id=300,
        )
        _insert_parse_result(
            conn,
            raw_message_id=rm2,
            message_type="UPDATE",
            is_executable=0,
            normalized_json=_update_json(reply_to=300),
        )

    with (
        patch("parser_test.scripts.replay_operation_rules.apply_migrations"),
        patch(
            "parser_test.scripts.replay_operation_rules.OperationRulesEngine"
        ) as MockEngine,
        patch(
            "parser_test.scripts.replay_operation_rules.TargetResolver"
        ) as MockResolver,
    ):
        mock_engine = MagicMock()
        MockEngine.return_value = mock_engine
        mock_resolver = MagicMock()
        MockResolver.return_value = mock_resolver

        def fake_apply(parse_result, trader_id, *, db_path, **kwargs):
            return _make_mock_op_signal(parse_result, trader_id)

        mock_engine.apply.side_effect = fake_apply
        mock_resolver.resolve.return_value = _make_mock_resolved_target([1])

        stats = run_replay(db_path=db_path, dry_run=False)

    assert stats.total == 2
    assert stats.new_signal_inserted == 1
    assert stats.update_linked == 1
    assert stats.update_orphan == 0

    with sqlite3.connect(db_path) as conn:
        op_sigs = conn.execute(
            "SELECT message_type, resolved_target_ids FROM operational_signals ORDER BY op_signal_id"
        ).fetchall()

    assert len(op_sigs) == 2
    # UPDATE should have resolved_target_ids set
    update_row = next(r for r in op_sigs if r[0] == "UPDATE")
    assert json.loads(update_row[1]) == [1]


def test_dry_run_no_writes(tmp_path: Path) -> None:
    from parser_test.scripts.replay_operation_rules import run_replay

    db_path = _make_db(tmp_path / "bt.sqlite3")

    with sqlite3.connect(db_path) as conn:
        rm_id = _insert_raw_message(conn, telegram_message_id=400)
        _insert_parse_result(
            conn,
            raw_message_id=rm_id,
            message_type="NEW_SIGNAL",
            normalized_json=_new_signal_json(),
        )

    with (
        patch("parser_test.scripts.replay_operation_rules.apply_migrations"),
        patch(
            "parser_test.scripts.replay_operation_rules.OperationRulesEngine"
        ) as MockEngine,
        patch("parser_test.scripts.replay_operation_rules.TargetResolver"),
    ):
        mock_engine = MagicMock()
        MockEngine.return_value = mock_engine

        def fake_apply(parse_result, trader_id, *, db_path, **kwargs):
            return _make_mock_op_signal(parse_result, trader_id)

        mock_engine.apply.side_effect = fake_apply

        stats = run_replay(db_path=db_path, dry_run=True)

    assert stats.new_signal_inserted == 1  # counted but not written

    with sqlite3.connect(db_path) as conn:
        signals = conn.execute("SELECT * FROM signals").fetchall()
        op_sigs = conn.execute("SELECT * FROM operational_signals").fetchall()

    assert len(signals) == 0
    assert len(op_sigs) == 0


def test_filter_by_trader(tmp_path: Path) -> None:
    from parser_test.scripts.replay_operation_rules import run_replay

    db_path = _make_db(tmp_path / "bt.sqlite3")

    with sqlite3.connect(db_path) as conn:
        # trader_3 signal
        rm1 = _insert_raw_message(
            conn, telegram_message_id=500, message_ts="2025-06-01T10:00:00"
        )
        _insert_parse_result(
            conn,
            raw_message_id=rm1,
            message_type="NEW_SIGNAL",
            resolved_trader_id="trader_3",
            normalized_json=_new_signal_json("BTCUSDT"),
        )
        # trader_a signal — should be excluded
        rm2 = _insert_raw_message(
            conn, telegram_message_id=501, message_ts="2025-06-01T11:00:00"
        )
        _insert_parse_result(
            conn,
            raw_message_id=rm2,
            message_type="NEW_SIGNAL",
            resolved_trader_id="trader_a",
            normalized_json=_new_signal_json("ETHUSDT"),
        )

    with (
        patch("parser_test.scripts.replay_operation_rules.apply_migrations"),
        patch(
            "parser_test.scripts.replay_operation_rules.OperationRulesEngine"
        ) as MockEngine,
        patch("parser_test.scripts.replay_operation_rules.TargetResolver"),
    ):
        mock_engine = MagicMock()
        MockEngine.return_value = mock_engine

        def fake_apply(parse_result, trader_id, *, db_path, **kwargs):
            return _make_mock_op_signal(parse_result, trader_id)

        mock_engine.apply.side_effect = fake_apply

        stats = run_replay(db_path=db_path, trader="trader_3", dry_run=True)

    assert stats.total == 1
    assert stats.new_signal_inserted == 1
