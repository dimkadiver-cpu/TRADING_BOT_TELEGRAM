"""Tests for src/operation_rules/risk_calculator.py.

Coverage:
  - compute_exposure: formula position_size_pct × sl_dist × leverage
  - compute_exposure: MARKET (no entry) → 0.0
  - compute_exposure: non-NEW_SIGNAL → 0.0
  - compute_exposure: multiple entries → average used
  - sum_exposure: empty DB → 0.0
  - sum_exposure: rows with valid data → correct sum
  - sum_exposure_global: multi-trader sum
  - _row_exposure: None fields → 0.0
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.operation_rules.loader import MergedRules, load_rules
from src.operation_rules.risk_calculator import (
    _row_exposure,
    compute_exposure,
    sum_exposure,
    sum_exposure_global,
)
from src.parser.models.canonical import Price, TraderParseResult
from src.parser.models.new_signal import EntryLevel, NewSignalEntities, StopLoss, TakeProfit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rules(
    position_size_pct: float = 1.0,
    leverage: int = 1,
) -> MergedRules:
    """Return a MergedRules with the given sizing params and global defaults."""
    rules = load_rules("trader_3")
    # Override sizing fields via model_copy
    return rules.model_copy(update={"position_size_pct": position_size_pct, "leverage": leverage})


def _new_signal_pr(
    entry_value: float,
    sl_value: float,
    entry_type: str = "LIMIT",
) -> TraderParseResult:
    entry = EntryLevel(price=Price.from_float(entry_value), order_type="LIMIT")
    sl = StopLoss(price=Price.from_float(sl_value))
    tp = TakeProfit(price=Price.from_float(entry_value * 1.05))
    entities = NewSignalEntities(
        symbol="BTCUSDT",
        direction="LONG",
        entry_type=entry_type,  # type: ignore[arg-type]
        entries=[entry],
        stop_loss=sl,
        take_profits=[tp],
    )
    return TraderParseResult(
        message_type="NEW_SIGNAL",
        completeness="COMPLETE",
        trader_id="trader_3",
        raw_text="BTCUSDT LONG",
        entities=entities,
    )


# ---------------------------------------------------------------------------
# compute_exposure
# ---------------------------------------------------------------------------

class TestComputeExposure:
    def test_basic_formula(self) -> None:
        # entry=100, sl=90 → sl_dist=10/100=0.10; size=1.0, lev=1 → 0.10
        pr = _new_signal_pr(entry_value=100.0, sl_value=90.0)
        rules = _rules(position_size_pct=1.0, leverage=1)
        exp = compute_exposure(pr, rules)
        assert exp == pytest.approx(0.10)

    def test_leverage_multiplies(self) -> None:
        # entry=100, sl=90 → sl_dist=0.10; size=1.0, lev=10 → 1.0
        pr = _new_signal_pr(entry_value=100.0, sl_value=90.0)
        rules = _rules(position_size_pct=1.0, leverage=10)
        exp = compute_exposure(pr, rules)
        assert exp == pytest.approx(1.0)

    def test_position_size_scales(self) -> None:
        # entry=100, sl=90 → sl_dist=0.10; size=2.0, lev=1 → 0.20
        pr = _new_signal_pr(entry_value=100.0, sl_value=90.0)
        rules = _rules(position_size_pct=2.0, leverage=1)
        exp = compute_exposure(pr, rules)
        assert exp == pytest.approx(0.20)

    def test_sl_above_entry_uses_abs(self) -> None:
        # SHORT: entry=100, sl=110 → sl_dist=10/100=0.10
        entry = EntryLevel(price=Price.from_float(100.0), order_type="LIMIT")
        sl = StopLoss(price=Price.from_float(110.0))
        tp = TakeProfit(price=Price.from_float(90.0))
        entities = NewSignalEntities(
            symbol="BTCUSDT",
            direction="SHORT",
            entry_type="LIMIT",
            entries=[entry],
            stop_loss=sl,
            take_profits=[tp],
        )
        pr = TraderParseResult(
            message_type="NEW_SIGNAL",
            completeness="COMPLETE",
            trader_id="t",
            raw_text="x",
            entities=entities,
        )
        exp = compute_exposure(pr, _rules())
        assert exp == pytest.approx(0.10)

    def test_market_no_entry_returns_zero(self) -> None:
        entities = NewSignalEntities(
            symbol="BTCUSDT",
            direction="LONG",
            entry_type="MARKET",
            stop_loss=StopLoss(price=Price.from_float(90.0)),
            take_profits=[TakeProfit(price=Price.from_float(105.0))],
        )
        pr = TraderParseResult(
            message_type="NEW_SIGNAL",
            completeness="COMPLETE",
            trader_id="t",
            raw_text="x",
            entities=entities,
        )
        assert compute_exposure(pr, _rules()) == 0.0

    def test_update_returns_zero(self) -> None:
        pr = TraderParseResult(
            message_type="UPDATE",
            trader_id="t",
            raw_text="move sl",
        )
        assert compute_exposure(pr, _rules()) == 0.0

    def test_no_entities_returns_zero(self) -> None:
        pr = TraderParseResult(
            message_type="NEW_SIGNAL",
            completeness="INCOMPLETE",
            trader_id="t",
            raw_text="x",
            entities=None,
        )
        assert compute_exposure(pr, _rules()) == 0.0

    def test_no_stop_loss_returns_zero(self) -> None:
        entities = NewSignalEntities(
            symbol="BTCUSDT",
            direction="LONG",
            entry_type="LIMIT",
            entries=[EntryLevel(price=Price.from_float(100.0), order_type="LIMIT")],
            stop_loss=None,
            take_profits=[],
        )
        pr = TraderParseResult(
            message_type="NEW_SIGNAL",
            completeness="INCOMPLETE",
            trader_id="t",
            raw_text="x",
            entities=entities,
        )
        assert compute_exposure(pr, _rules()) == 0.0

    def test_multiple_entries_uses_average(self) -> None:
        # entries: 90, 110 → avg=100; sl=90 → sl_dist=10/100=0.10
        entries = [
            EntryLevel(price=Price.from_float(90.0), order_type="LIMIT"),
            EntryLevel(price=Price.from_float(110.0), order_type="LIMIT"),
        ]
        sl = StopLoss(price=Price.from_float(80.0))
        tp = TakeProfit(price=Price.from_float(120.0))
        entities = NewSignalEntities(
            symbol="BTCUSDT",
            direction="LONG",
            entry_type="AVERAGING",
            entries=entries,
            stop_loss=sl,
            take_profits=[tp],
        )
        pr = TraderParseResult(
            message_type="NEW_SIGNAL",
            completeness="COMPLETE",
            trader_id="t",
            raw_text="x",
            entities=entities,
        )
        # avg entry = 100, sl=80, sl_dist=20/100=0.20, size=1.0, lev=1
        exp = compute_exposure(pr, _rules(position_size_pct=1.0, leverage=1))
        assert exp == pytest.approx(0.20)


# ---------------------------------------------------------------------------
# _row_exposure (internal helper — tested directly)
# ---------------------------------------------------------------------------

class TestRowExposure:
    def test_basic(self) -> None:
        # entry=100, sl=90 → sl_dist=0.10; pos=1.0, lev=1 → 0.10
        entry_json = json.dumps([{"price": 100.0}])
        result = _row_exposure(1.0, 1, 90.0, entry_json)
        assert result == pytest.approx(0.10)

    def test_none_position_size(self) -> None:
        entry_json = json.dumps([{"price": 100.0}])
        assert _row_exposure(None, 1, 90.0, entry_json) == 0.0

    def test_none_leverage(self) -> None:
        entry_json = json.dumps([{"price": 100.0}])
        assert _row_exposure(1.0, None, 90.0, entry_json) == 0.0

    def test_none_sl(self) -> None:
        entry_json = json.dumps([{"price": 100.0}])
        assert _row_exposure(1.0, 1, None, entry_json) == 0.0

    def test_none_entry_json(self) -> None:
        assert _row_exposure(1.0, 1, 90.0, None) == 0.0

    def test_invalid_entry_json(self) -> None:
        assert _row_exposure(1.0, 1, 90.0, "not-json") == 0.0

    def test_empty_entry_list(self) -> None:
        assert _row_exposure(1.0, 1, 90.0, json.dumps([])) == 0.0

    def test_scalar_entry_list(self) -> None:
        # entry_json as list of floats (alternative format)
        entry_json = json.dumps([100.0])
        result = _row_exposure(1.0, 1, 90.0, entry_json)
        assert result == pytest.approx(0.10)

    def test_leverage_multiplies(self) -> None:
        entry_json = json.dumps([{"price": 100.0}])
        result = _row_exposure(1.0, 5, 90.0, entry_json)
        assert result == pytest.approx(0.50)


# ---------------------------------------------------------------------------
# sum_exposure / sum_exposure_global — async DB tests
# ---------------------------------------------------------------------------

import aiosqlite


async def _make_test_db(tmp_path: Path) -> Path:
    """Create minimal DB schema for exposure tests."""
    db_path = tmp_path / "test.db"
    async with aiosqlite.connect(db_path) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS signals (
                attempt_key TEXT PRIMARY KEY,
                trader_id   TEXT NOT NULL,
                symbol      TEXT,
                status      TEXT NOT NULL DEFAULT 'PENDING',
                sl          REAL,
                entry_json  TEXT
            );
            CREATE TABLE IF NOT EXISTS operational_signals (
                op_signal_id      INTEGER PRIMARY KEY AUTOINCREMENT,
                parse_result_id   INTEGER NOT NULL DEFAULT 1,
                attempt_key       TEXT,
                trader_id         TEXT NOT NULL,
                message_type      TEXT NOT NULL,
                is_blocked        INTEGER NOT NULL DEFAULT 0,
                position_size_pct REAL,
                leverage          INTEGER,
                created_at        TEXT NOT NULL DEFAULT '2026-01-01T00:00:00'
            );
        """)
        await db.commit()
    return db_path


@pytest.mark.asyncio
async def test_sum_exposure_empty_db(tmp_path: Path) -> None:
    db_path = await _make_test_db(tmp_path)
    result = await sum_exposure("trader_3", db_path)
    assert result == 0.0


@pytest.mark.asyncio
async def test_sum_exposure_global_empty_db(tmp_path: Path) -> None:
    db_path = await _make_test_db(tmp_path)
    result = await sum_exposure_global(db_path)
    assert result == 0.0


@pytest.mark.asyncio
async def test_sum_exposure_fresh_db_no_tables(tmp_path: Path) -> None:
    """DB with no tables at all — must not raise, must return 0."""
    db_path = tmp_path / "fresh.db"
    async with aiosqlite.connect(db_path) as db:
        await db.commit()
    result = await sum_exposure("trader_3", db_path)
    assert result == 0.0


@pytest.mark.asyncio
async def test_sum_exposure_one_open_signal(tmp_path: Path) -> None:
    db_path = await _make_test_db(tmp_path)
    entry_json = json.dumps([{"price": 100.0}])
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO signals(attempt_key,trader_id,symbol,status,sl,entry_json) "
            "VALUES(?,?,?,?,?,?)",
            ("key1", "trader_3", "BTCUSDT", "PENDING", 90.0, entry_json),
        )
        await db.execute(
            "INSERT INTO operational_signals"
            "(parse_result_id,attempt_key,trader_id,message_type,is_blocked,position_size_pct,leverage)"
            " VALUES(?,?,?,?,?,?,?)",
            (1, "key1", "trader_3", "NEW_SIGNAL", 0, 1.0, 1),
        )
        await db.commit()

    # entry=100, sl=90 → sl_dist=0.10; pos=1.0, lev=1 → 0.10
    result = await sum_exposure("trader_3", db_path)
    assert result == pytest.approx(0.10)


@pytest.mark.asyncio
async def test_sum_exposure_closed_signal_excluded(tmp_path: Path) -> None:
    db_path = await _make_test_db(tmp_path)
    entry_json = json.dumps([{"price": 100.0}])
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO signals(attempt_key,trader_id,symbol,status,sl,entry_json) "
            "VALUES(?,?,?,?,?,?)",
            ("key1", "trader_3", "BTCUSDT", "CLOSED", 90.0, entry_json),
        )
        await db.execute(
            "INSERT INTO operational_signals"
            "(parse_result_id,attempt_key,trader_id,message_type,is_blocked,position_size_pct,leverage)"
            " VALUES(?,?,?,?,?,?,?)",
            (1, "key1", "trader_3", "NEW_SIGNAL", 0, 1.0, 1),
        )
        await db.commit()

    result = await sum_exposure("trader_3", db_path)
    assert result == 0.0


@pytest.mark.asyncio
async def test_sum_exposure_blocked_excluded(tmp_path: Path) -> None:
    db_path = await _make_test_db(tmp_path)
    entry_json = json.dumps([{"price": 100.0}])
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO signals(attempt_key,trader_id,symbol,status,sl,entry_json) "
            "VALUES(?,?,?,?,?,?)",
            ("key1", "trader_3", "BTCUSDT", "PENDING", 90.0, entry_json),
        )
        await db.execute(
            "INSERT INTO operational_signals"
            "(parse_result_id,attempt_key,trader_id,message_type,is_blocked,position_size_pct,leverage)"
            " VALUES(?,?,?,?,?,?,?)",
            (1, "key1", "trader_3", "NEW_SIGNAL", 1, 1.0, 1),  # is_blocked=1
        )
        await db.commit()

    result = await sum_exposure("trader_3", db_path)
    assert result == 0.0


@pytest.mark.asyncio
async def test_sum_exposure_global_two_traders(tmp_path: Path) -> None:
    db_path = await _make_test_db(tmp_path)
    entry_json = json.dumps([{"price": 100.0}])
    async with aiosqlite.connect(db_path) as db:
        for key, trader in [("k1", "trader_a"), ("k2", "trader_b")]:
            await db.execute(
                "INSERT INTO signals(attempt_key,trader_id,symbol,status,sl,entry_json) "
                "VALUES(?,?,?,?,?,?)",
                (key, trader, "ETHUSDT", "ACTIVE", 90.0, entry_json),
            )
            await db.execute(
                "INSERT INTO operational_signals"
                "(parse_result_id,attempt_key,trader_id,message_type,is_blocked,position_size_pct,leverage)"
                " VALUES(?,?,?,?,?,?,?)",
                (1, key, trader, "NEW_SIGNAL", 0, 1.0, 1),
            )
        await db.commit()

    # Each: 0.10; total: 0.20
    result = await sum_exposure_global(db_path)
    assert result == pytest.approx(0.20)


@pytest.mark.asyncio
async def test_sum_exposure_trader_scoped(tmp_path: Path) -> None:
    """sum_exposure filters by trader_id; sum_exposure_global returns both."""
    db_path = await _make_test_db(tmp_path)
    entry_json = json.dumps([{"price": 100.0}])
    async with aiosqlite.connect(db_path) as db:
        for key, trader in [("k1", "trader_a"), ("k2", "trader_b")]:
            await db.execute(
                "INSERT INTO signals(attempt_key,trader_id,symbol,status,sl,entry_json) "
                "VALUES(?,?,?,?,?,?)",
                (key, trader, "BTCUSDT", "ACTIVE", 90.0, entry_json),
            )
            await db.execute(
                "INSERT INTO operational_signals"
                "(parse_result_id,attempt_key,trader_id,message_type,is_blocked,position_size_pct,leverage)"
                " VALUES(?,?,?,?,?,?,?)",
                (1, key, trader, "NEW_SIGNAL", 0, 1.0, 1),
            )
        await db.commit()

    a_exp = await sum_exposure("trader_a", db_path)
    b_exp = await sum_exposure("trader_b", db_path)
    global_exp = await sum_exposure_global(db_path)

    assert a_exp == pytest.approx(0.10)
    assert b_exp == pytest.approx(0.10)
    assert global_exp == pytest.approx(0.20)
