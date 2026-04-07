"""Tests for src/operation_rules/engine.py."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import yaml

from src.core.migrations import apply_migrations
from src.operation_rules.engine import OperationRulesEngine
from src.parser.models.new_signal import EntryLevel, NewSignalEntities, StopLoss, TakeProfit
from src.parser.models.canonical import Price
from src.parser.trader_profiles.base import TraderParseResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def rules_dir(tmp_path: Path) -> Path:
    global_yaml = {
        "registered_traders": [
            "trader_x",
            "disabled_t",
            "legacy_avg",
            "lev0",
            "dis2",
            "warn_t",
            "hint_ok",
            "hint_over",
            "hint_warn",
            "hint_bad",
            "tp_t",
        ],
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
                "LIMIT": {
                    "single": {"weights": {"E1": 1.0}},
                    "averaging": {"weights": {"E1": 0.4, "E2": 0.6}},
                },
                "MARKET": {
                    "single": {"weights": {"E1": 1.0}},
                    "averaging": {"weights": {"E1": 0.7, "E2": 0.3}},
                },
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
    def test_unregistered_trader_blocks(self, rules_dir: Path, db_path: str) -> None:
        engine = OperationRulesEngine(rules_dir=str(rules_dir))
        result = _make_result()
        op = engine.apply(result, "not_registered", db_path=db_path)
        assert op.is_blocked is True
        assert op.block_reason == "trader_not_registered"

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

    def test_legacy_averaging_fallback_warns(self, rules_dir: Path, db_path: str) -> None:
        (rules_dir / "trader_rules" / "legacy_avg.yaml").write_text(
            yaml.dump(
                {
                    "entry_split": {
                        "AVERAGING": {
                            "distribution": "decreasing",
                            "weights": {"E1": 0.7, "E2": 0.3},
                        },
                        "LIMIT": {"single": {"weights": {"E1": 1.0}}, "averaging": {}},
                    }
                }
            ),
            encoding="utf-8",
        )
        engine = OperationRulesEngine(rules_dir=str(rules_dir))
        result = _make_result(
            entities={
                "symbol": "BTCUSDT",
                "side": "BUY",
                "entry_raw": "60000-62000",
                "stop_raw": "55000",
            }
        )
        with pytest.warns(DeprecationWarning, match="deprecated"):
            op = engine.apply(result, "legacy_avg", db_path=db_path)
        assert op.entry_split == {"E1": pytest.approx(0.7), "E2": pytest.approx(0.3)}

    def test_entry_split_single_market_plan(self, rules_dir: Path, db_path: str) -> None:
        engine = OperationRulesEngine(rules_dir=str(rules_dir))
        result = _make_result(
            entities={
                "symbol": "BTCUSDT",
                "side": "BUY",
                "entry_plan_type": "SINGLE_MARKET",
                "entry_plan_entries": [{"role": "PRIMARY", "order_type": "MARKET", "price": 60000}],
                "stop_raw": "55000",
            }
        )
        op = engine.apply(result, "trader_x", db_path=db_path)
        assert op.entry_split == {"E1": 1.0}

    def test_market_signal_without_explicit_price_uses_tp_sl_reference(
        self, rules_dir: Path, db_path: str
    ) -> None:
        engine = OperationRulesEngine(rules_dir=str(rules_dir))
        result = _make_result(
            entities={
                "symbol": "BTCUSDT",
                "side": "BUY",
                "entry_plan_type": "SINGLE_MARKET",
                "entry_plan_entries": [{"role": "PRIMARY", "order_type": "MARKET", "price": None}],
                "stop_raw": "57000",
                "take_profits": [{"price": 65000.0}],
            }
        )
        op = engine.apply(result, "trader_x", db_path=db_path)
        assert op.is_blocked is False
        assert op.entry_split == {"E1": 1.0}
        assert op.sl_distance_pct == pytest.approx((61000.0 - 57000.0) / 61000.0)

    def test_entry_split_market_with_limit_averaging(self, rules_dir: Path, db_path: str) -> None:
        engine = OperationRulesEngine(rules_dir=str(rules_dir))
        result = _make_result(
            entities={
                "symbol": "BTCUSDT",
                "side": "BUY",
                "entry_plan_type": "MARKET_WITH_LIMIT_AVERAGING",
                "entry_plan_entries": [
                    {"role": "PRIMARY", "order_type": "MARKET", "price": 60000},
                    {"role": "AVERAGING", "order_type": "LIMIT", "price": 59000},
                ],
                "stop_raw": "55000",
            }
        )
        op = engine.apply(result, "trader_x", db_path=db_path)
        assert op.entry_split == {"E1": pytest.approx(0.7), "E2": pytest.approx(0.3)}

    def test_market_with_limit_averaging_without_primary_price_uses_next_leg(
        self, rules_dir: Path, db_path: str
    ) -> None:
        engine = OperationRulesEngine(rules_dir=str(rules_dir))
        result = _make_result(
            entities={
                "symbol": "BTCUSDT",
                "side": "BUY",
                "entry_plan_type": "MARKET_WITH_LIMIT_AVERAGING",
                "entry_plan_entries": [
                    {"role": "PRIMARY", "order_type": "MARKET", "price": None},
                    {"role": "AVERAGING", "order_type": "LIMIT", "price": 59000},
                ],
                "stop_raw": "55000",
            }
        )
        op = engine.apply(result, "trader_x", db_path=db_path)
        assert op.is_blocked is False
        assert op.entry_split == {"E1": pytest.approx(0.7), "E2": pytest.approx(0.3)}

    def test_entry_split_limit_with_limit_averaging(self, rules_dir: Path, db_path: str) -> None:
        engine = OperationRulesEngine(rules_dir=str(rules_dir))
        result = _make_result(
            entities={
                "symbol": "BTCUSDT",
                "side": "BUY",
                "entry_plan_type": "LIMIT_WITH_LIMIT_AVERAGING",
                "entry_plan_entries": [
                    {"role": "PRIMARY", "order_type": "LIMIT", "price": 60000},
                    {"role": "AVERAGING", "order_type": "LIMIT", "price": 59000},
                ],
                "stop_raw": "55000",
            }
        )
        op = engine.apply(result, "trader_x", db_path=db_path)
        assert op.entry_split == {"E1": pytest.approx(0.4), "E2": pytest.approx(0.6)}

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


class TestEngineCanonicalEntities:
    """Engine must handle NewSignalEntities (Pydantic model) as well as plain dicts."""

    def _make_canonical_result(
        self,
        entries: list[tuple[float, str]],   # (price, order_type)
        sl_price: float,
        symbol: str = "BTCUSDT",
        direction: str = "LONG",
    ) -> TraderParseResult:
        entry_levels = [
            EntryLevel(price=Price(raw=str(p), value=p), order_type=ot)  # type: ignore[arg-type]
            for p, ot in entries
        ]
        entities = NewSignalEntities(
            symbol=symbol,
            direction=direction,  # type: ignore[arg-type]
            entry_type="LIMIT",
            entries=entry_levels,
            stop_loss=StopLoss(price=Price(raw=str(sl_price), value=sl_price)),  # type: ignore[arg-type]
            take_profits=[TakeProfit(price=Price(raw=str(sl_price * 1.1), value=sl_price * 1.1))],  # type: ignore[arg-type]
        )
        return TraderParseResult(
            message_type="NEW_SIGNAL",
            intents=[],
            entities=entities,
            confidence=0.9,
        )

    def test_canonical_entities_not_spuriously_blocked(
        self, rules_dir: Path, db_path: str
    ) -> None:
        """NewSignalEntities must not produce missing_entry/missing_stop_loss blocks.

        Before P2 fix, isinstance(entities, dict) fell through to {}, causing
        _extract_entry_prices → [] → blocked("missing_entry").
        """
        engine = OperationRulesEngine(rules_dir=str(rules_dir))
        result = self._make_canonical_result(
            entries=[(60000.0, "LIMIT")], sl_price=57000.0
        )
        op = engine.apply(result, "trader_x", db_path=db_path)
        assert op.is_blocked is False
        assert op.position_size_usdt is not None

    def test_canonical_entities_sl_extracted(
        self, rules_dir: Path, db_path: str
    ) -> None:
        """SL price must be correctly extracted from nested StopLoss model."""
        engine = OperationRulesEngine(rules_dir=str(rules_dir))
        result = self._make_canonical_result(
            entries=[(100.0, "LIMIT")], sl_price=90.0
        )
        op = engine.apply(result, "trader_x", db_path=db_path)
        assert op.is_blocked is False
        # sl_distance = |100-90|/100 = 0.10
        assert op.sl_distance_pct == pytest.approx(0.10)

    def test_canonical_entities_symbol_extracted(
        self, rules_dir: Path, db_path: str
    ) -> None:
        """Symbol from NewSignalEntities must flow into OperationalSignal."""
        engine = OperationRulesEngine(rules_dir=str(rules_dir))
        result = self._make_canonical_result(
            entries=[(100.0, "LIMIT")], sl_price=90.0, symbol="ETHUSDT"
        )
        op = engine.apply(result, "trader_x", db_path=db_path)
        # Not blocked; symbol gate was evaluated on ETHUSDT (no open signals)
        assert op.is_blocked is False

    def test_canonical_entities_multi_entry(
        self, rules_dir: Path, db_path: str
    ) -> None:
        """Multiple entries in NewSignalEntities produce a multi-key entry_split."""
        engine = OperationRulesEngine(rules_dir=str(rules_dir))
        result = self._make_canonical_result(
            entries=[(60000.0, "LIMIT"), (58000.0, "LIMIT")], sl_price=55000.0
        )
        op = engine.apply(result, "trader_x", db_path=db_path)
        assert op.is_blocked is False
        assert op.entry_split is not None
        assert len(op.entry_split) == 2


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


class TestEngineRiskHint:
    """risk_hint must be applied BEFORE cap gates — not after."""

    def test_risk_hint_within_cap_passes(self, rules_dir: Path, db_path: str) -> None:
        """risk_hint within hard cap → signal not blocked, hint used."""
        (rules_dir / "trader_rules" / "hint_ok.yaml").write_text(
            yaml.dump({
                "use_trader_risk_hint": True,
                "risk_pct_of_capital": 0.5,
                "capital_base_usdt": 1000.0,
            }),
            encoding="utf-8",
        )
        engine = OperationRulesEngine(rules_dir=str(rules_dir))
        # hint=1.5% — below hard cap of 2%
        result = _make_result(entities={
            "symbol": "BTCUSDT", "side": "BUY",
            "entry_raw": "100", "stop_raw": "90",
            "risk_hint": 1.5,
        })
        op = engine.apply(result, "hint_ok", db_path=db_path)
        assert op.is_blocked is False
        assert op.risk_hint_used is True
        assert op.risk_pct_of_capital == pytest.approx(1.5)

    def test_risk_hint_exceeds_hard_cap_blocks(self, rules_dir: Path, db_path: str) -> None:
        """risk_hint above hard_max_per_signal_risk_pct (2%) → blocked.

        Before the P1 fix, the cap was evaluated against the config value (0.5%)
        which passed; the hint was applied after, bypassing the gate.
        """
        (rules_dir / "trader_rules" / "hint_over.yaml").write_text(
            yaml.dump({
                "use_trader_risk_hint": True,
                "gate_mode": "block",
                "risk_pct_of_capital": 0.5,      # config < hard cap
                "capital_base_usdt": 1000.0,
            }),
            encoding="utf-8",
        )
        engine = OperationRulesEngine(rules_dir=str(rules_dir))
        # hint=5% — above hard cap of 2%
        result = _make_result(entities={
            "symbol": "BTCUSDT", "side": "BUY",
            "entry_raw": "100", "stop_raw": "90",
            "risk_hint": 5.0,
        })
        op = engine.apply(result, "hint_over", db_path=db_path)
        assert op.is_blocked is True
        assert op.block_reason == "per_signal_cap_exceeded"

    def test_risk_hint_exceeds_hard_cap_warn_mode(self, rules_dir: Path, db_path: str) -> None:
        """In warn mode, hint above hard cap adds warning but does not block."""
        (rules_dir / "trader_rules" / "hint_warn.yaml").write_text(
            yaml.dump({
                "use_trader_risk_hint": True,
                "gate_mode": "warn",
                "risk_pct_of_capital": 0.5,
                "capital_base_usdt": 1000.0,
            }),
            encoding="utf-8",
        )
        engine = OperationRulesEngine(rules_dir=str(rules_dir))
        result = _make_result(entities={
            "symbol": "BTCUSDT", "side": "BUY",
            "entry_raw": "100", "stop_raw": "90",
            "risk_hint": 5.0,
        })
        op = engine.apply(result, "hint_warn", db_path=db_path)
        assert op.is_blocked is False
        assert any("per_signal_cap_exceeded" in w for w in op.warnings)
        assert op.risk_hint_used is True

    def test_invalid_risk_hint_falls_back_to_config(self, rules_dir: Path, db_path: str) -> None:
        """Non-numeric risk_hint → warning added, config value used."""
        (rules_dir / "trader_rules" / "hint_bad.yaml").write_text(
            yaml.dump({
                "use_trader_risk_hint": True,
                "risk_pct_of_capital": 1.0,
                "capital_base_usdt": 1000.0,
            }),
            encoding="utf-8",
        )
        engine = OperationRulesEngine(rules_dir=str(rules_dir))
        result = _make_result(entities={
            "symbol": "BTCUSDT", "side": "BUY",
            "entry_raw": "100", "stop_raw": "90",
            "risk_hint": "not-a-number",
        })
        op = engine.apply(result, "hint_bad", db_path=db_path)
        assert op.is_blocked is False
        assert op.risk_hint_used is False
        assert op.risk_pct_of_capital == pytest.approx(1.0)
        assert any("risk_hint_parse_failed" in w for w in op.warnings)


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
