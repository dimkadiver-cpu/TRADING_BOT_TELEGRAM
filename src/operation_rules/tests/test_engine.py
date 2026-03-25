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
            "max_per_signal_pct": 2.0,
        },
        "global_defaults": {
            "enabled": True,
            "gate_mode": "block",
            "use_trader_risk_hint": False,
            "position_size_pct": 1.0,
            "leverage": 1,
            "max_capital_at_risk_per_trader_pct": 5.0,
            "max_concurrent_same_symbol": 1,
            "entry_split": {
                "ZONE": {"split_mode": "endpoints", "weights": {"E1": 0.50, "E2": 0.50}},
                "AVERAGING": {"distribution": "equal"},
                "LIMIT": {"weights": {"E1": 1.0}},
                "MARKET": {"weights": {"E1": 1.0}},
            },
            "price_corrections": {"enabled": False, "method": None},
            "price_sanity": {"enabled": False, "symbol_ranges": {}},
            "position_management": {
                "on_tp_hit": [
                    {"tp_level": 1, "action": "close_partial", "close_pct": 50},
                ],
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
        assert op.position_size_pct == 1.0
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
        assert "on_tp_hit" in op.management_rules


class TestEngineUpdatePassthrough:
    def test_update_not_blocked(self, rules_dir: Path, db_path: str) -> None:
        engine = OperationRulesEngine(rules_dir=str(rules_dir))
        result = _make_result(message_type="UPDATE", intents=["U_CLOSE_FULL"])
        op = engine.apply(result, "trader_x", db_path=db_path)
        assert op.is_blocked is False
        assert op.position_size_pct is None
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
                "position_size_pct": 50.0,  # huge to trigger caps
                "leverage": 100,
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
        assert op.position_size_pct is None
