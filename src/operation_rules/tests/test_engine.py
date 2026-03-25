"""Tests for src/operation_rules/engine.py."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import yaml

from src.core.migrations import apply_migrations
from src.operation_rules.engine import OperationRulesEngine
from src.parser.trader_profiles.base import TraderParseResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def rules_dir(tmp_path: Path) -> Path:
    global_yaml = {
        "global_hard_caps": {
            "max_capital_at_risk_pct": 10.0,
            "hard_max_per_signal_risk_pct": 2.0,
        },
        "global_defaults": {
            "enabled": True,
            "gate_mode": "block",
            "use_trader_risk_hint": False,
            "risk_mode": "risk_pct_of_capital",
            "risk_pct_of_capital": 1.0,
            "risk_usdt_fixed": 10.0,
            "capital_base_mode": "static_config",
            "capital_base_usdt": 1000.0,
            "leverage": 1,
            "max_capital_at_risk_per_trader_pct": 5.0,
            "max_concurrent_same_symbol": 1,
            "entry_split": {
                "ZONE": {"split_mode": "endpoints", "weights": {"E1": 0.50, "E2": 0.50}},
                "AVERAGING": {"distribution": "equal"},
                "LIMIT": {"weights": {"E1": 1.0}},
                "MARKET": {"weights": {"E1": 1.0}},
            },
            "tp_handling": {
                "tp_handling_mode": "follow_all_signal_tps",
                "max_tp_levels": 5,
                "tp_close_distribution": {2: [50, 50], 3: [30, 30, 40], 5: [20, 20, 20, 20, 20]},
            },
            "price_corrections": {"enabled": False, "method": None},
            "price_sanity": {"enabled": False, "symbol_ranges": {}},
            "position_management": {
                "auto_apply_intents": ["U_MOVE_STOP"],
                "log_only_intents": ["U_TP_HIT"],
            },
        },
    }
    (tmp_path / "operation_rules.yaml").write_text(yaml.dump(global_yaml), encoding="utf-8")
    (tmp_path / "trader_rules").mkdir()
    return tmp_path


@pytest.fixture()
def db_path(tmp_path: Path) -> str:
    path = str(tmp_path / "engine_test.sqlite3")
    apply_migrations(db_path=path, migrations_dir=str(Path("db/migrations").resolve()))
    return path


def _make_result(
    message_type: str = "NEW_SIGNAL",
    entities: dict | None = None,
    intents: list | None = None,
    confidence: float = 0.9,
) -> TraderParseResult:
    return TraderParseResult(
        message_type=message_type,
        intents=intents or [],
        entities=entities or {
            "symbol": "BTCUSDT",
            "side": "BUY",
            "entry_raw": "60000",
            "stop_raw": "57000",
        },
        confidence=confidence,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEngineDisabledTrader:
    def test_disabled_trader_blocks(self, rules_dir: Path, db_path: str) -> None:
        (rules_dir / "trader_rules" / "disabled_t.yaml").write_text(
            yaml.dump({"enabled": False}), encoding="utf-8"
        )
        engine = OperationRulesEngine(rules_dir=str(rules_dir))
        result = _make_result()
        op = engine.apply(result, "disabled_t", db_path=db_path)
        assert op.is_blocked is True
        assert op.block_reason == "trader_disabled"


class TestEngineNewSignalPassthrough:
    def test_new_signal_not_blocked(self, rules_dir: Path, db_path: str) -> None:
        engine = OperationRulesEngine(rules_dir=str(rules_dir))
        result = _make_result()
        op = engine.apply(result, "trader_x", db_path=db_path)
        assert op.is_blocked is False
        # risk-first model: risk_budget = 1% of 1000 = 10 USDT
        # sl_distance = |60000-57000|/60000 = 0.05 (5%)
        # position_size_usdt = 10 / (0.05 * 1) = 200 USDT
        assert op.risk_budget_usdt == pytest.approx(10.0)
        assert op.sl_distance_pct == pytest.approx(0.05)
        assert op.position_size_usdt == pytest.approx(200.0)
        assert op.leverage == 1
        assert op.entry_split is not None
        assert op.management_rules is not None

    def test_entry_split_single_entry(self, rules_dir: Path, db_path: str) -> None:
        engine = OperationRulesEngine(rules_dir=str(rules_dir))
        result = _make_result(entities={"symbol": "BTCUSDT", "side": "BUY",
                                        "entry_raw": "60000", "stop_raw": "55000"})
        op = engine.apply(result, "trader_x", db_path=db_path)
        assert op.entry_split == {"E1": 1.0}

    def test_entry_split_two_entries(self, rules_dir: Path, db_path: str) -> None:
        engine = OperationRulesEngine(rules_dir=str(rules_dir))
        result = _make_result(entities={"symbol": "BTCUSDT", "side": "BUY",
                                        "entry_raw": "60000-62000", "stop_raw": "55000"})
        op = engine.apply(result, "trader_x", db_path=db_path)
        assert op.entry_split is not None
        assert len(op.entry_split) == 2

    def test_management_rules_snapshot(self, rules_dir: Path, db_path: str) -> None:
        engine = OperationRulesEngine(rules_dir=str(rules_dir))
        op = engine.apply(_make_result(), "trader_x", db_path=db_path)
        assert isinstance(op.management_rules, dict)
        assert "tp_handling" in op.management_rules


class TestEngineNewSignalMissingData:
    def test_missing_entry_blocks(self, rules_dir: Path, db_path: str) -> None:
        """Signal without entry prices is blocked — size cannot be computed."""
        engine = OperationRulesEngine(rules_dir=str(rules_dir))
        result = _make_result(entities={"symbol": "BTCUSDT", "side": "BUY",
                                        "stop_raw": "57000"})
        op = engine.apply(result, "trader_x", db_path=db_path)
        assert op.is_blocked is True
        assert op.block_reason == "missing_entry"

    def test_missing_sl_blocks(self, rules_dir: Path, db_path: str) -> None:
        """Signal without stop loss is blocked — risk cannot be computed."""
        engine = OperationRulesEngine(rules_dir=str(rules_dir))
        result = _make_result(entities={"symbol": "BTCUSDT", "side": "BUY",
                                        "entry_raw": "60000"})
        op = engine.apply(result, "trader_x", db_path=db_path)
        assert op.is_blocked is True
        assert op.block_reason == "missing_stop_loss"

    def test_zero_sl_distance_blocks(self, rules_dir: Path, db_path: str) -> None:
        """Entry == SL → zero SL distance → blocked."""
        engine = OperationRulesEngine(rules_dir=str(rules_dir))
        result = _make_result(entities={"symbol": "BTCUSDT", "side": "BUY",
                                        "entry_raw": "60000", "stop_raw": "60000"})
        op = engine.apply(result, "trader_x", db_path=db_path)
        assert op.is_blocked is True
        assert op.block_reason == "zero_sl_distance"

    def test_invalid_leverage_blocks(self, rules_dir: Path, db_path: str) -> None:
        """Leverage 0 → blocked."""
        (rules_dir / "trader_rules" / "lev0.yaml").write_text(
            yaml.dump({"leverage": 0}), encoding="utf-8"
        )
        engine = OperationRulesEngine(rules_dir=str(rules_dir))
        result = _make_result()
        op = engine.apply(result, "lev0", db_path=db_path)
        assert op.is_blocked is True
        assert op.block_reason == "invalid_leverage"


class TestEngineUpdatePassthrough:
    def test_update_not_blocked(self, rules_dir: Path, db_path: str) -> None:
        engine = OperationRulesEngine(rules_dir=str(rules_dir))
        result = _make_result(message_type="UPDATE", intents=["U_CLOSE_FULL"])
        op = engine.apply(result, "trader_x", db_path=db_path)
        assert op.is_blocked is False
        assert op.position_size_usdt is None
        assert op.risk_budget_usdt is None
        assert op.management_rules is not None

    def test_update_disabled_trader_still_blocked(self, rules_dir: Path, db_path: str) -> None:
        (rules_dir / "trader_rules" / "dis2.yaml").write_text(
            yaml.dump({"enabled": False}), encoding="utf-8"
        )
        engine = OperationRulesEngine(rules_dir=str(rules_dir))
        result = _make_result(message_type="UPDATE")
        op = engine.apply(result, "dis2", db_path=db_path)
        assert op.is_blocked is True


class TestEngineGateMode:
    def test_warn_mode_does_not_block_on_cap(self, rules_dir: Path, db_path: str) -> None:
        """With gate_mode=warn, cap breaches add warnings but don't block."""
        (rules_dir / "trader_rules" / "warn_t.yaml").write_text(
            yaml.dump({
                "gate_mode": "warn",
                "risk_pct_of_capital": 50.0,   # huge — exceeds hard cap of 2%
                "capital_base_usdt": 1000.0,
            }),
            encoding="utf-8",
        )
        engine = OperationRulesEngine(rules_dir=str(rules_dir))
        result = _make_result(entities={"symbol": "BTCUSDT", "side": "BUY",
                                        "entry_raw": "100", "stop_raw": "90"})
        op = engine.apply(result, "warn_t", db_path=db_path)
        assert op.is_blocked is False
        assert len(op.warnings) > 0

    def test_block_mode_blocks_on_same_symbol(self, rules_dir: Path, db_path: str) -> None:
        """Open signal for same symbol → blocked in block mode."""
        # Insert an open signal for BTCUSDT
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """INSERT INTO signals
                   (attempt_key,env,channel_id,root_telegram_id,trader_id,trader_prefix,
                    symbol,side,entry_json,sl,tp_json,status,confidence,raw_text,
                    created_at,updated_at)
                   VALUES ('T_100_1_tx','T','100','1','trader_x','TX',
                           'BTCUSDT','BUY','[]',55000.0,'[]','PENDING',0.9,'x',
                           '2026-01-01','2026-01-01')"""
            )
            conn.commit()

        engine = OperationRulesEngine(rules_dir=str(rules_dir))
        result = _make_result()  # BTCUSDT again
        op = engine.apply(result, "trader_x", db_path=db_path)
        assert op.is_blocked is True
        assert op.block_reason == "max_concurrent_same_symbol"


class TestEngineNonActionable:
    def test_info_only_passthrough(self, rules_dir: Path, db_path: str) -> None:
        engine = OperationRulesEngine(rules_dir=str(rules_dir))
        result = _make_result(message_type="INFO_ONLY")
        op = engine.apply(result, "trader_x", db_path=db_path)
        assert op.is_blocked is False
        assert op.position_size_usdt is None
        assert op.risk_budget_usdt is None


class TestEngineTpHandling:
    def test_tp_handling_in_management_rules(self, rules_dir: Path, db_path: str) -> None:
        """tp_handling config must appear in the management_rules snapshot."""
        engine = OperationRulesEngine(rules_dir=str(rules_dir))
        op = engine.apply(_make_result(), "trader_x", db_path=db_path)
        tp = op.management_rules.get("tp_handling", {})
        assert tp.get("tp_handling_mode") == "follow_all_signal_tps"
        assert "tp_close_distribution" in tp
        assert tp["tp_close_distribution"][2] == [50, 50]

    def test_trader_can_override_tp_handling(self, rules_dir: Path, db_path: str) -> None:
        (rules_dir / "trader_rules" / "tp_t.yaml").write_text(
            yaml.dump({
                "tp_handling": {
                    "tp_handling_mode": "limit_to_max_levels",
                    "max_tp_levels": 3,
                    "tp_close_distribution": {3: [40, 30, 30]},
                }
            }),
            encoding="utf-8",
        )
        engine = OperationRulesEngine(rules_dir=str(rules_dir))
        op = engine.apply(_make_result(), "tp_t", db_path=db_path)
        tp = op.management_rules.get("tp_handling", {})
        assert tp.get("tp_handling_mode") == "limit_to_max_levels"
        assert tp.get("max_tp_levels") == 3
        assert tp["tp_close_distribution"][3] == [40, 30, 30]
