"""Tests for src/operation_rules/engine.py.

Coverage:
  - apply: trader disabled → blocked("trader_disabled")
  - apply: max_concurrent_same_symbol exceeded → blocked
  - apply: per_signal_cap_exceeded (hard cap) → blocked
  - apply: trader_capital_at_risk_exceeded → blocked
  - apply: global_capital_at_risk_exceeded → blocked
  - apply: NEW_SIGNAL success → OperationalSignal with sizing + split + mgmt
  - apply: UPDATE passthrough → management_rules set, no gate
  - apply: INFO_ONLY / UNCLASSIFIED passthrough → minimal OperationalSignal
  - compute_entry_split: MARKET, LIMIT, ZONE endpoints, ZONE three_way, AVERAGING equal/decreasing
  - price sanity: out-of-range adds warning (does not block)
  - risk_hint_used: use_trader_risk_hint=true copies entities.risk_pct
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import aiosqlite
import pytest

from src.operation_rules.engine import apply, compute_entry_split
from src.operation_rules.loader import MergedRules, load_rules
from src.parser.models.canonical import Price, TraderParseResult
from src.parser.models.new_signal import EntryLevel, NewSignalEntities, StopLoss, TakeProfit


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------

def _write_global(cfg_dir: Path, content: str) -> None:
    (cfg_dir / "operation_rules.yaml").write_text(
        textwrap.dedent(content), encoding="utf-8"
    )


def _write_trader(cfg_dir: Path, trader_id: str, content: str) -> None:
    td = cfg_dir / "trader_rules"
    td.mkdir(parents=True, exist_ok=True)
    (td / f"{trader_id}.yaml").write_text(textwrap.dedent(content), encoding="utf-8")


_GLOBAL_YAML = """\
    global_hard_caps:
      max_capital_at_risk_pct: 10.0
      max_per_signal_pct: 2.0

    global_defaults:
      enabled: true
      gate_mode: block
      use_trader_risk_hint: false
      position_size_pct: 1.0
      leverage: 1
      max_capital_at_risk_per_trader_pct: 5.0
      max_concurrent_same_symbol: 1
      entry_split:
        ZONE:
          split_mode: endpoints
          weights: {E1: 0.50, E2: 0.50}
        AVERAGING:
          distribution: equal
        LIMIT:
          weights: {E1: 1.0}
        MARKET:
          weights: {E1: 1.0}
      price_corrections:
        enabled: false
        method: null
      price_sanity:
        enabled: false
        symbol_ranges: {}
      position_management:
        on_tp_hit:
          - {tp_level: 1, action: close_partial, close_pct: 50}
          - {tp_level: 2, action: move_to_be}
          - {tp_level: 3, action: close_full}
        auto_apply_intents:
          - U_MOVE_STOP
          - U_CLOSE_FULL
          - U_CLOSE_PARTIAL
          - U_CANCEL_PENDING
        log_only_intents:
          - U_TP_HIT
          - U_SL_HIT
"""


async def _empty_db(tmp_path: Path) -> Path:
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


def _limit_signal(
    entry: float = 90000.0,
    sl: float = 88000.0,
    symbol: str = "BTCUSDT",
    trader_id: str = "trader_x",
) -> TraderParseResult:
    e = EntryLevel(price=Price.from_float(entry), order_type="LIMIT")
    s = StopLoss(price=Price.from_float(sl))
    t = TakeProfit(price=Price.from_float(entry * 1.05))
    entities = NewSignalEntities(
        symbol=symbol,
        direction="LONG",
        entry_type="LIMIT",
        entries=[e],
        stop_loss=s,
        take_profits=[t],
    )
    return TraderParseResult(
        message_type="NEW_SIGNAL",
        completeness="COMPLETE",
        trader_id=trader_id,
        raw_text="BTCUSDT LONG",
        entities=entities,
    )


def _update_signal(trader_id: str = "trader_x") -> TraderParseResult:
    return TraderParseResult(
        message_type="UPDATE",
        trader_id=trader_id,
        raw_text="move SL to BE",
    )


# ---------------------------------------------------------------------------
# Gate checks — blocked cases
# ---------------------------------------------------------------------------

class TestGateBlocked:
    @pytest.mark.asyncio
    async def test_trader_disabled(self, tmp_path: Path) -> None:
        _write_global(tmp_path, _GLOBAL_YAML)
        _write_trader(tmp_path, "trader_x", "enabled: false\n")
        db = await _empty_db(tmp_path)
        pr = _limit_signal()
        op = await apply(pr, "trader_x", db, config_dir=tmp_path)
        assert op.is_blocked is True
        assert op.block_reason == "trader_disabled"

    @pytest.mark.asyncio
    async def test_max_concurrent_same_symbol(self, tmp_path: Path) -> None:
        _write_global(tmp_path, _GLOBAL_YAML)
        _write_trader(tmp_path, "trader_x", "max_concurrent_same_symbol: 1\n")
        db = await _empty_db(tmp_path)
        # Insert one open BTCUSDT signal
        async with aiosqlite.connect(db) as conn:
            await conn.execute(
                "INSERT INTO signals(attempt_key,trader_id,symbol,status) VALUES(?,?,?,?)",
                ("k1", "trader_x", "BTCUSDT", "ACTIVE"),
            )
            await conn.commit()
        pr = _limit_signal(symbol="BTCUSDT")
        op = await apply(pr, "trader_x", db, config_dir=tmp_path)
        assert op.is_blocked is True
        assert op.block_reason == "max_concurrent_same_symbol"

    @pytest.mark.asyncio
    async def test_per_signal_cap_exceeded(self, tmp_path: Path) -> None:
        """position_size=1.0, leverage=30, sl_dist=0.10 → exp=3.0 > cap=2.0"""
        _write_global(tmp_path, _GLOBAL_YAML)
        _write_trader(tmp_path, "trader_x", "leverage: 30\n")
        db = await _empty_db(tmp_path)
        # entry=100, sl=90 → sl_dist=10%
        pr = _limit_signal(entry=100.0, sl=90.0)
        op = await apply(pr, "trader_x", db, config_dir=tmp_path)
        assert op.is_blocked is True
        assert op.block_reason == "per_signal_cap_exceeded"

    @pytest.mark.asyncio
    async def test_trader_capital_at_risk_exceeded(self, tmp_path: Path) -> None:
        """Fill trader exposure to ~4.9%, then add signal with 0.2% → 5.1% > 5.0%.

        Existing signal: entry=100, sl=51 → sl_dist=0.49; leverage=10 → exp=4.9%
        New signal: entry=100, sl=80 → sl_dist=0.20; leverage=1 (global default) → exp=0.2%
        Total: 4.9 + 0.2 = 5.1 > max_capital_at_risk_per_trader_pct=5.0 → blocked.
        """
        _write_global(tmp_path, _GLOBAL_YAML)
        db = await _empty_db(tmp_path)
        entry_json = json.dumps([{"price": 100.0}])
        async with aiosqlite.connect(db) as conn:
            # leverage=10 in DB → _row_exposure uses it → 1.0 × 0.49 × 10 = 4.9
            await conn.execute(
                "INSERT INTO signals(attempt_key,trader_id,symbol,status,sl,entry_json) "
                "VALUES(?,?,?,?,?,?)",
                ("k1", "trader_x", "XYZUSDT", "ACTIVE", 51.0, entry_json),
            )
            await conn.execute(
                "INSERT INTO operational_signals"
                "(parse_result_id,attempt_key,trader_id,message_type,is_blocked,position_size_pct,leverage)"
                " VALUES(?,?,?,?,?,?,?)",
                (1, "k1", "trader_x", "NEW_SIGNAL", 0, 1.0, 10),
            )
            await conn.commit()
        # New signal with global default leverage=1: exp = 1.0 × 0.20 × 1 = 0.2
        # Total = 4.9 + 0.2 = 5.1 > 5.0 → blocked
        pr = _limit_signal(entry=100.0, sl=80.0, symbol="BTCUSDT")
        op = await apply(pr, "trader_x", db, config_dir=tmp_path)
        assert op.is_blocked is True
        assert op.block_reason == "trader_capital_at_risk_exceeded"

    @pytest.mark.asyncio
    async def test_global_capital_at_risk_exceeded(self, tmp_path: Path) -> None:
        """Fill global exposure near 10% hard cap, then one more → blocked."""
        _write_global(tmp_path, _GLOBAL_YAML)
        # Use a trader with high per-trader cap to hit global cap first
        _write_trader(tmp_path, "trader_x", "max_capital_at_risk_per_trader_pct: 20.0\n")
        db = await _empty_db(tmp_path)
        entry_json = json.dumps([{"price": 100.0}])
        async with aiosqlite.connect(db) as conn:
            # 5 existing signals for different traders, each 2% → global=10%
            for i in range(5):
                key = f"k{i}"
                await conn.execute(
                    "INSERT INTO signals(attempt_key,trader_id,symbol,status,sl,entry_json) "
                    "VALUES(?,?,?,?,?,?)",
                    (key, f"trader_{i}", "BTCUSDT", "ACTIVE", 80.0, entry_json),
                )
                await conn.execute(
                    "INSERT INTO operational_signals"
                    "(parse_result_id,attempt_key,trader_id,message_type,is_blocked,position_size_pct,leverage)"
                    " VALUES(?,?,?,?,?,?,?)",
                    (1, key, f"trader_{i}", "NEW_SIGNAL", 0, 1.0, 1),
                )
            await conn.commit()
        # Global: 5 × (20% sl_dist × 1.0 × 1) = 5 × 0.20 = 1.0
        # That's not 10%. Let me use leverage=10 to get each signal at 2%
        # entry=100, sl=80 → sl_dist=20%=0.20; size=1.0, lev=10 → exp=2.0 × 5 = 10
        # Use leverage in the existing signals (stored as leverage col)
        async with aiosqlite.connect(db) as conn:
            await conn.execute("DELETE FROM operational_signals")
            await conn.execute("DELETE FROM signals")
            for i in range(5):
                key = f"k{i}"
                await conn.execute(
                    "INSERT INTO signals(attempt_key,trader_id,symbol,status,sl,entry_json) "
                    "VALUES(?,?,?,?,?,?)",
                    (key, f"trader_{i}", "BTCUSDT", "ACTIVE", 80.0, entry_json),
                )
                await conn.execute(
                    "INSERT INTO operational_signals"
                    "(parse_result_id,attempt_key,trader_id,message_type,is_blocked,position_size_pct,leverage)"
                    " VALUES(?,?,?,?,?,?,?)",
                    (1, key, f"trader_{i}", "NEW_SIGNAL", 0, 1.0, 10),
                )
            await conn.commit()
        # Global exposure = 5 × 0.20 × 10 = 10.0 (at the cap)
        # New signal: any non-zero exposure → 10 + ε > 10.0 → blocked
        pr = _limit_signal(entry=100.0, sl=90.0, symbol="ETHUSDT", trader_id="trader_x")
        op = await apply(pr, "trader_x", db, config_dir=tmp_path)
        assert op.is_blocked is True
        assert op.block_reason == "global_capital_at_risk_exceeded"


# ---------------------------------------------------------------------------
# Gate passes — successful NEW_SIGNAL
# ---------------------------------------------------------------------------

class TestGateSuccess:
    @pytest.mark.asyncio
    async def test_new_signal_produces_operational_signal(self, tmp_path: Path) -> None:
        _write_global(tmp_path, _GLOBAL_YAML)
        db = await _empty_db(tmp_path)
        pr = _limit_signal()
        op = await apply(pr, "trader_x", db, config_dir=tmp_path)
        assert op.is_blocked is False
        assert op.block_reason is None
        assert op.parse_result is pr

    @pytest.mark.asyncio
    async def test_sizing_fields_populated(self, tmp_path: Path) -> None:
        _write_global(tmp_path, _GLOBAL_YAML)
        _write_trader(tmp_path, "trader_x", "position_size_pct: 0.5\nleverage: 3\n")
        db = await _empty_db(tmp_path)
        pr = _limit_signal()
        op = await apply(pr, "trader_x", db, config_dir=tmp_path)
        assert op.is_blocked is False
        assert op.position_size_pct == 0.5
        assert op.leverage == 3

    @pytest.mark.asyncio
    async def test_entry_split_populated(self, tmp_path: Path) -> None:
        _write_global(tmp_path, _GLOBAL_YAML)
        db = await _empty_db(tmp_path)
        pr = _limit_signal()
        op = await apply(pr, "trader_x", db, config_dir=tmp_path)
        assert op.entry_split is not None
        assert "E1" in op.entry_split
        assert abs(sum(op.entry_split.values()) - 1.0) < 1e-9

    @pytest.mark.asyncio
    async def test_management_rules_snapshot(self, tmp_path: Path) -> None:
        _write_global(tmp_path, _GLOBAL_YAML)
        db = await _empty_db(tmp_path)
        pr = _limit_signal()
        op = await apply(pr, "trader_x", db, config_dir=tmp_path)
        assert op.management_rules is not None
        assert "on_tp_hit" in op.management_rules
        assert "auto_apply_intents" in op.management_rules

    @pytest.mark.asyncio
    async def test_applied_rules_set(self, tmp_path: Path) -> None:
        _write_global(tmp_path, _GLOBAL_YAML)
        db = await _empty_db(tmp_path)
        pr = _limit_signal()
        op = await apply(pr, "trader_x", db, config_dir=tmp_path)
        assert len(op.applied_rules) >= 1

    @pytest.mark.asyncio
    async def test_different_symbol_allowed_when_one_open(self, tmp_path: Path) -> None:
        """One BTCUSDT open; ETHUSDT should pass gate."""
        _write_global(tmp_path, _GLOBAL_YAML)
        db = await _empty_db(tmp_path)
        async with aiosqlite.connect(db) as conn:
            await conn.execute(
                "INSERT INTO signals(attempt_key,trader_id,symbol,status) VALUES(?,?,?,?)",
                ("k1", "trader_x", "BTCUSDT", "ACTIVE"),
            )
            await conn.commit()
        pr = _limit_signal(symbol="ETHUSDT")
        op = await apply(pr, "trader_x", db, config_dir=tmp_path)
        assert op.is_blocked is False


# ---------------------------------------------------------------------------
# UPDATE passthrough
# ---------------------------------------------------------------------------

class TestUpdatePassthrough:
    @pytest.mark.asyncio
    async def test_update_not_blocked(self, tmp_path: Path) -> None:
        _write_global(tmp_path, _GLOBAL_YAML)
        db = await _empty_db(tmp_path)
        pr = _update_signal()
        op = await apply(pr, "trader_x", db, config_dir=tmp_path)
        assert op.is_blocked is False

    @pytest.mark.asyncio
    async def test_update_disabled_trader_still_blocked(self, tmp_path: Path) -> None:
        """Gate step 1 (trader disabled) applies to UPDATE too."""
        _write_global(tmp_path, _GLOBAL_YAML)
        _write_trader(tmp_path, "trader_x", "enabled: false\n")
        db = await _empty_db(tmp_path)
        pr = _update_signal()
        op = await apply(pr, "trader_x", db, config_dir=tmp_path)
        assert op.is_blocked is True
        assert op.block_reason == "trader_disabled"

    @pytest.mark.asyncio
    async def test_update_management_rules_populated(self, tmp_path: Path) -> None:
        _write_global(tmp_path, _GLOBAL_YAML)
        db = await _empty_db(tmp_path)
        pr = _update_signal()
        op = await apply(pr, "trader_x", db, config_dir=tmp_path)
        assert op.management_rules is not None
        assert "auto_apply_intents" in op.management_rules

    @pytest.mark.asyncio
    async def test_update_no_sizing_fields(self, tmp_path: Path) -> None:
        _write_global(tmp_path, _GLOBAL_YAML)
        db = await _empty_db(tmp_path)
        pr = _update_signal()
        op = await apply(pr, "trader_x", db, config_dir=tmp_path)
        assert op.position_size_pct is None
        assert op.leverage is None
        assert op.entry_split is None


# ---------------------------------------------------------------------------
# INFO_ONLY / UNCLASSIFIED passthrough
# ---------------------------------------------------------------------------

class TestInfoPassthrough:
    @pytest.mark.asyncio
    async def test_info_only_passthrough(self, tmp_path: Path) -> None:
        _write_global(tmp_path, _GLOBAL_YAML)
        db = await _empty_db(tmp_path)
        pr = TraderParseResult(
            message_type="INFO_ONLY",
            trader_id="trader_x",
            raw_text="📊 Stats",
        )
        op = await apply(pr, "trader_x", db, config_dir=tmp_path)
        assert op.is_blocked is False
        assert op.management_rules is None
        assert op.position_size_pct is None

    @pytest.mark.asyncio
    async def test_unclassified_passthrough(self, tmp_path: Path) -> None:
        _write_global(tmp_path, _GLOBAL_YAML)
        db = await _empty_db(tmp_path)
        pr = TraderParseResult(
            message_type="UNCLASSIFIED",
            trader_id="trader_x",
            raw_text="???",
        )
        op = await apply(pr, "trader_x", db, config_dir=tmp_path)
        assert op.is_blocked is False


# ---------------------------------------------------------------------------
# compute_entry_split
# ---------------------------------------------------------------------------

class TestComputeEntrySplit:
    def _rules(self, tmp_path: Path, trader_yaml: str = "") -> MergedRules:
        _write_global(tmp_path, _GLOBAL_YAML)
        if trader_yaml:
            _write_trader(tmp_path, "trader_x", trader_yaml)
        return load_rules("trader_x", config_dir=tmp_path)

    def _entities(
        self,
        entry_type: str,
        prices: list[float],
        symbol: str = "BTCUSDT",
    ) -> NewSignalEntities:
        entries = [EntryLevel(price=Price.from_float(p), order_type="LIMIT") for p in prices]
        return NewSignalEntities(
            symbol=symbol,
            direction="LONG",
            entry_type=entry_type,  # type: ignore[arg-type]
            entries=entries,
            stop_loss=StopLoss(price=Price.from_float(min(prices) * 0.9)),
            take_profits=[TakeProfit(price=Price.from_float(max(prices) * 1.1))],
        )

    def test_market_returns_e1_full(self, tmp_path: Path) -> None:
        rules = self._rules(tmp_path)
        entities = NewSignalEntities(entry_type="MARKET", symbol="BTCUSDT", direction="LONG")
        split = compute_entry_split(entities, rules)
        assert split == {"E1": 1.0}

    def test_limit_returns_e1_full(self, tmp_path: Path) -> None:
        rules = self._rules(tmp_path)
        entities = self._entities("LIMIT", [90000.0])
        split = compute_entry_split(entities, rules)
        assert split == {"E1": 1.0}

    def test_zone_endpoints_equal_weights(self, tmp_path: Path) -> None:
        rules = self._rules(tmp_path)  # split_mode: endpoints, weights {E1:0.5, E2:0.5}
        entities = self._entities("ZONE", [88000.0, 92000.0])
        split = compute_entry_split(entities, rules)
        assert set(split.keys()) == {"E1", "E2"}
        assert split["E1"] == pytest.approx(0.5)
        assert split["E2"] == pytest.approx(0.5)

    def test_zone_three_way(self, tmp_path: Path) -> None:
        rules = self._rules(
            tmp_path,
            "entry_split:\n  ZONE:\n    split_mode: three_way\n    weights: {E1: 0.30, E2: 0.40, E3: 0.30}\n",
        )
        entities = self._entities("ZONE", [88000.0, 92000.0])
        split = compute_entry_split(entities, rules)
        assert set(split.keys()) == {"E1", "E2", "E3"}
        assert abs(sum(split.values()) - 1.0) < 1e-9
        assert split["E2"] == pytest.approx(0.40)

    def test_zone_midpoint(self, tmp_path: Path) -> None:
        rules = self._rules(
            tmp_path,
            "entry_split:\n  ZONE:\n    split_mode: midpoint\n",
        )
        entities = self._entities("ZONE", [88000.0, 92000.0])
        split = compute_entry_split(entities, rules)
        assert split == {"E1": 1.0}

    def test_averaging_equal_two_entries(self, tmp_path: Path) -> None:
        rules = self._rules(tmp_path)
        entities = self._entities("AVERAGING", [90000.0, 91000.0])
        split = compute_entry_split(entities, rules)
        assert set(split.keys()) == {"E1", "E2"}
        assert split["E1"] == pytest.approx(0.5)
        assert split["E2"] == pytest.approx(0.5)

    def test_averaging_equal_three_entries(self, tmp_path: Path) -> None:
        rules = self._rules(tmp_path)
        entities = self._entities("AVERAGING", [90000.0, 91000.0, 92000.0])
        split = compute_entry_split(entities, rules)
        for k in ("E1", "E2", "E3"):
            assert split[k] == pytest.approx(1 / 3)

    def test_averaging_decreasing_weights(self, tmp_path: Path) -> None:
        rules = self._rules(
            tmp_path,
            "entry_split:\n"
            "  AVERAGING:\n"
            "    distribution: decreasing\n"
            "    weights: {E1: 0.40, E2: 0.30, E3: 0.20, E4: 0.10}\n",
        )
        entities = self._entities("AVERAGING", [90000.0, 91000.0, 92000.0, 93000.0])
        split = compute_entry_split(entities, rules)
        assert split["E1"] == pytest.approx(0.40)
        assert split["E4"] == pytest.approx(0.10)
        assert abs(sum(split.values()) - 1.0) < 1e-9

    def test_split_weights_sum_to_one(self, tmp_path: Path) -> None:
        rules = self._rules(tmp_path)
        for entry_type, prices in [
            ("LIMIT", [90000.0]),
            ("ZONE", [88000.0, 92000.0]),
            ("AVERAGING", [90000.0, 91000.0, 92000.0]),
        ]:
            entities = self._entities(entry_type, prices)
            split = compute_entry_split(entities, rules)
            assert abs(sum(split.values()) - 1.0) < 1e-9, f"failed for {entry_type}"


# ---------------------------------------------------------------------------
# Price sanity — warning, no block
# ---------------------------------------------------------------------------

class TestPriceSanity:
    @pytest.mark.asyncio
    async def test_out_of_range_adds_warning(self, tmp_path: Path) -> None:
        _write_global(tmp_path, _GLOBAL_YAML)
        _write_trader(
            tmp_path,
            "trader_x",
            "price_sanity:\n"
            "  enabled: true\n"
            "  symbol_ranges:\n"
            "    BTCUSDT: {min: 10000, max: 500000}\n",
        )
        db = await _empty_db(tmp_path)
        # Entry at 1.0 is below min=10000
        pr = _limit_signal(entry=1.0, sl=0.9)
        op = await apply(pr, "trader_x", db, config_dir=tmp_path)
        # Not blocked by sanity check alone (gate_mode: block applies to caps)
        sanity_warnings = [w for w in op.warnings if "price_out_of_static_range" in w]
        assert len(sanity_warnings) >= 1

    @pytest.mark.asyncio
    async def test_in_range_no_warning(self, tmp_path: Path) -> None:
        _write_global(tmp_path, _GLOBAL_YAML)
        _write_trader(
            tmp_path,
            "trader_x",
            "price_sanity:\n"
            "  enabled: true\n"
            "  symbol_ranges:\n"
            "    BTCUSDT: {min: 10000, max: 500000}\n",
        )
        db = await _empty_db(tmp_path)
        pr = _limit_signal(entry=90000.0, sl=88000.0)
        op = await apply(pr, "trader_x", db, config_dir=tmp_path)
        sanity_warnings = [w for w in op.warnings if "price_out_of_static_range" in w]
        assert sanity_warnings == []


# ---------------------------------------------------------------------------
# risk_hint_used
# ---------------------------------------------------------------------------

class TestRiskHintUsed:
    @pytest.mark.asyncio
    async def test_risk_hint_not_used_by_default(self, tmp_path: Path) -> None:
        _write_global(tmp_path, _GLOBAL_YAML)
        db = await _empty_db(tmp_path)
        pr = _limit_signal()
        op = await apply(pr, "trader_x", db, config_dir=tmp_path)
        assert op.risk_hint_used is False

    @pytest.mark.asyncio
    async def test_risk_hint_used_when_enabled(self, tmp_path: Path) -> None:
        _write_global(tmp_path, _GLOBAL_YAML)
        _write_trader(tmp_path, "trader_x", "use_trader_risk_hint: true\n")
        db = await _empty_db(tmp_path)
        # Create a signal with risk_pct set in entities
        e = EntryLevel(price=Price.from_float(90000.0), order_type="LIMIT")
        s = StopLoss(price=Price.from_float(88000.0))
        t = TakeProfit(price=Price.from_float(95000.0))
        entities = NewSignalEntities(
            symbol="BTCUSDT",
            direction="LONG",
            entry_type="LIMIT",
            entries=[e],
            stop_loss=s,
            take_profits=[t],
            risk_pct=0.5,
        )
        pr = TraderParseResult(
            message_type="NEW_SIGNAL",
            completeness="COMPLETE",
            trader_id="trader_x",
            raw_text="BTCUSDT LONG",
            entities=entities,
        )
        op = await apply(pr, "trader_x", db, config_dir=tmp_path)
        assert op.risk_hint_used is True
        assert op.position_size_pct == pytest.approx(0.5)
