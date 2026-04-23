"""Tests for src/operation_rules/loader.py."""

from __future__ import annotations

import pytest
import yaml
from pathlib import Path

from src.operation_rules.loader import (
    HardCaps,
    load_effective_rules,
    validate_operation_rules_config,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def rules_dir(tmp_path: Path) -> Path:
    """Create a minimal config directory structure."""
    global_yaml = {
        "registered_traders": [
            "unknown_trader",
            "any",
            "my_trader",
            "global_mode",
            "badmode",
            "disabled",
            "hacker",
            "splitter",
            "bad_avg_key",
            "legacy_avg_rejected",
            "bad_gate",
            "bad_risk",
            "bad_cap",
            "ok_gate",
            "ok_risk",
            "upper_gate",
            "ok",
            "bad_me_mode",
            "bad_be_trigger",
            "bad_cancel_avg",
            "bad_cd_mode",
        ],
        "global_hard_caps": {
            "max_capital_at_risk_pct": 10.0,
            "hard_max_per_signal_risk_pct": 2.0,
            "market_execution": {
                "mode": "tolerance",
                "tolerance_pct": 0.5,
                "range_tolerance_pct": 0.2,
            },
        },
        "global_defaults": {
            "enabled": True,
            "gate_mode": "block",
            "operation_rules": "override",
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
                "LIMIT": {
                    "single": {"weights": {"E1": 1.0}},
                    "averaging": {"weights": {"E1": 0.5, "E2": 0.5}},
                },
                "MARKET": {
                    "single": {"weights": {"E1": 1.0}},
                    "averaging": {"weights": {"E1": 0.5, "E2": 0.5}},
                },
            },
            "tp": {
                "use_tp_count": None,
                "close_distribution": {
                    "mode": "table",
                    "table": {1: [100], 2: [50, 50], 3: [30, 30, 40]},
                },
            },
            "sl": {
                "use_original_sl": True,
                "be_trigger": None,
            },
            "updates": {
                "apply_move_stop": True,
                "apply_close_partial": True,
                "apply_close_full": True,
                "apply_cancel_pending": True,
                "apply_add_entry": True,
            },
            "pending": {
                "cancel_pending_by_engine": True,
                "cancel_pending_on_timeout": True,
                "pending_timeout_hours": 24,
                "chain_timeout_hours": 168,
                "cancel_averaging_pending_after": None,
                "cancel_unfilled_pending_after": None,
            },
            "price_corrections": {"enabled": False, "method": None},
            "price_sanity": {"enabled": False, "symbol_ranges": {}},
        },
    }
    global_file = tmp_path / "operation_rules.yaml"
    global_file.write_text(yaml.dump(global_yaml), encoding="utf-8")

    trader_dir = tmp_path / "trader_rules"
    trader_dir.mkdir()
    return tmp_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestLoaderDefaults:
    def test_loads_without_trader_file(self, rules_dir: Path) -> None:
        rules = load_effective_rules("unknown_trader", rules_dir=str(rules_dir))
        assert rules.is_registered is True
        assert rules.enabled is True
        assert rules.gate_mode == "block"
        assert rules.operation_rules == "override"
        assert rules.risk_mode == "risk_pct_of_capital"
        assert rules.risk_pct_of_capital == 1.0
        assert rules.capital_base_usdt == 1000.0
        assert rules.leverage == 1
        assert rules.max_capital_at_risk_per_trader_pct == 5.0
        assert rules.max_concurrent_same_symbol == 1

    def test_hard_caps_loaded(self, rules_dir: Path) -> None:
        rules = load_effective_rules("any", rules_dir=str(rules_dir))
        assert isinstance(rules.hard_caps, HardCaps)
        assert rules.hard_caps.max_capital_at_risk_pct == 10.0
        assert rules.hard_caps.hard_max_per_signal_risk_pct == 2.0
        assert rules.hard_caps.market_execution["mode"] == "tolerance"
        assert rules.hard_caps.market_execution["tolerance_pct"] == 0.5

    def test_unregistered_trader_flagged(self, rules_dir: Path) -> None:
        rules = load_effective_rules("not_listed", rules_dir=str(rules_dir))
        assert rules.is_registered is False

    def test_entry_split_defaults(self, rules_dir: Path) -> None:
        rules = load_effective_rules("any", rules_dir=str(rules_dir))
        assert "LIMIT" in rules.entry_split
        assert "MARKET" in rules.entry_split
        assert "AVERAGING" not in rules.entry_split

    def test_missing_global_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_effective_rules("any", rules_dir=str(tmp_path))

    def test_new_sections_loaded(self, rules_dir: Path) -> None:
        rules = load_effective_rules("any", rules_dir=str(rules_dir))
        assert isinstance(rules.tp, dict)
        assert isinstance(rules.sl, dict)
        assert isinstance(rules.updates, dict)
        assert isinstance(rules.pending, dict)
        assert rules.sl["use_original_sl"] is True
        assert rules.sl["be_trigger"] is None
        assert rules.updates["apply_move_stop"] is True
        assert rules.pending["cancel_pending_by_engine"] is True
        assert rules.pending["pending_timeout_hours"] == 24

    def test_tp_section_loaded(self, rules_dir: Path) -> None:
        rules = load_effective_rules("any", rules_dir=str(rules_dir))
        assert rules.tp["use_tp_count"] is None
        assert rules.tp["close_distribution"]["mode"] == "table"


class TestLoaderTraderOverride:
    def test_trader_overrides_gate_mode(self, rules_dir: Path) -> None:
        trader_yaml = {"gate_mode": "warn", "risk_pct_of_capital": 0.5}
        (rules_dir / "trader_rules" / "my_trader.yaml").write_text(
            yaml.dump(trader_yaml), encoding="utf-8"
        )
        rules = load_effective_rules("my_trader", rules_dir=str(rules_dir))
        assert rules.gate_mode == "warn"
        assert rules.risk_pct_of_capital == 0.5
        assert rules.leverage == 1  # not overridden

    def test_global_mode_ignores_trader_rule_overrides(self, rules_dir: Path) -> None:
        trader_yaml = {
            "operation_rules": "global",
            "enabled": False,
            "gate_mode": "warn",
            "risk_pct_of_capital": 0.25,  # must be ignored
            "leverage": 7,                 # must be ignored
        }
        (rules_dir / "trader_rules" / "global_mode.yaml").write_text(
            yaml.dump(trader_yaml), encoding="utf-8"
        )
        rules = load_effective_rules("global_mode", rules_dir=str(rules_dir))
        assert rules.operation_rules == "global"
        assert rules.enabled is False
        assert rules.gate_mode == "warn"
        assert rules.risk_pct_of_capital == 1.0
        assert rules.leverage == 1

    def test_invalid_operation_rules_mode_falls_back_to_override(self, rules_dir: Path) -> None:
        trader_yaml = {"operation_rules": "invalid_mode", "risk_pct_of_capital": 0.75}
        (rules_dir / "trader_rules" / "badmode.yaml").write_text(
            yaml.dump(trader_yaml), encoding="utf-8"
        )
        rules = load_effective_rules("badmode", rules_dir=str(rules_dir))
        assert rules.operation_rules == "override"
        assert rules.risk_pct_of_capital == 0.75

    def test_trader_disables(self, rules_dir: Path) -> None:
        (rules_dir / "trader_rules" / "disabled.yaml").write_text(
            yaml.dump({"enabled": False}), encoding="utf-8"
        )
        rules = load_effective_rules("disabled", rules_dir=str(rules_dir))
        assert rules.enabled is False

    def test_hard_caps_not_overridable(self, rules_dir: Path) -> None:
        trader_yaml = {
            "global_hard_caps": {
                "max_capital_at_risk_pct": 999.0,
                "hard_max_per_signal_risk_pct": 999.0,
            }
        }
        (rules_dir / "trader_rules" / "hacker.yaml").write_text(
            yaml.dump(trader_yaml), encoding="utf-8"
        )
        rules = load_effective_rules("hacker", rules_dir=str(rules_dir))
        assert rules.hard_caps.max_capital_at_risk_pct == 10.0
        assert rules.hard_caps.hard_max_per_signal_risk_pct == 2.0

    def test_entry_split_deep_merge(self, rules_dir: Path) -> None:
        trader_yaml = {
            "entry_split": {
                "LIMIT": {"single": {"weights": {"E1": 1.0}}, "averaging": {"weights": {"E1": 0.6, "E2": 0.4}}},
            }
        }
        (rules_dir / "trader_rules" / "splitter.yaml").write_text(
            yaml.dump(trader_yaml), encoding="utf-8"
        )
        rules = load_effective_rules("splitter", rules_dir=str(rules_dir))
        assert rules.entry_split["LIMIT"]["averaging"]["weights"]["E1"] == 0.6
        assert "MARKET" in rules.entry_split

    def test_validate_operation_rules_config_validates_all_traders(self, rules_dir: Path) -> None:
        (rules_dir / "trader_rules" / "ok.yaml").write_text(
            yaml.dump({"enabled": True}), encoding="utf-8"
        )
        validate_operation_rules_config(rules_dir=str(rules_dir))

    def test_entry_split_averaging_typo_raises(self, rules_dir: Path) -> None:
        trader_yaml = {
            "entry_split": {
                "LIMIT": {
                    "averaging": {"weights": {"E1": 0.5, "E2": 0.5}},
                    "avareging": {"weights": {"E1": 0.6, "E2": 0.4}},
                }
            }
        }
        (rules_dir / "trader_rules" / "bad_avg_key.yaml").write_text(
            yaml.dump(trader_yaml), encoding="utf-8"
        )
        with pytest.raises(ValueError, match="overlapping averaging keys"):
            load_effective_rules("bad_avg_key", rules_dir=str(rules_dir))

    def test_entry_split_legacy_averaging_rejected(self, rules_dir: Path) -> None:
        trader_yaml = {
            "entry_split": {
                "AVERAGING": {"distribution": "decreasing", "weights": {"E1": 0, "E2": 0}}
            }
        }
        (rules_dir / "trader_rules" / "legacy_avg_rejected.yaml").write_text(
            yaml.dump(trader_yaml), encoding="utf-8"
        )
        with pytest.raises(ValueError, match="entry_split.AVERAGING is deprecated"):
            load_effective_rules("legacy_avg_rejected", rules_dir=str(rules_dir))

    def test_trader_overrides_sl_be_trigger(self, rules_dir: Path) -> None:
        (rules_dir / "trader_rules" / "my_trader.yaml").write_text(
            yaml.dump({"sl": {"be_trigger": "tp1"}}), encoding="utf-8"
        )
        rules = load_effective_rules("my_trader", rules_dir=str(rules_dir))
        assert rules.sl["be_trigger"] == "tp1"

    def test_trader_overrides_pending_timeout(self, rules_dir: Path) -> None:
        (rules_dir / "trader_rules" / "my_trader.yaml").write_text(
            yaml.dump({"pending": {"pending_timeout_hours": 48}}), encoding="utf-8"
        )
        rules = load_effective_rules("my_trader", rules_dir=str(rules_dir))
        assert rules.pending["pending_timeout_hours"] == 48
        assert rules.pending["cancel_pending_by_engine"] is True  # from defaults


class TestLoaderEnumValidation:
    """Enum fields gate_mode, risk_mode, capital_base_mode must be validated fail-fast."""

    def test_invalid_gate_mode_raises(self, rules_dir: Path) -> None:
        (rules_dir / "trader_rules" / "bad_gate.yaml").write_text(
            yaml.dump({"gate_mode": "blokk"}), encoding="utf-8"
        )
        with pytest.raises(ValueError, match="gate_mode"):
            load_effective_rules("bad_gate", rules_dir=str(rules_dir))

    def test_invalid_risk_mode_raises(self, rules_dir: Path) -> None:
        (rules_dir / "trader_rules" / "bad_risk.yaml").write_text(
            yaml.dump({"risk_mode": "fixed_amount"}), encoding="utf-8"
        )
        with pytest.raises(ValueError, match="risk_mode"):
            load_effective_rules("bad_risk", rules_dir=str(rules_dir))

    def test_invalid_capital_base_mode_raises(self, rules_dir: Path) -> None:
        (rules_dir / "trader_rules" / "bad_cap.yaml").write_text(
            yaml.dump({"capital_base_mode": "dynamic"}), encoding="utf-8"
        )
        with pytest.raises(ValueError, match="capital_base_mode"):
            load_effective_rules("bad_cap", rules_dir=str(rules_dir))

    def test_valid_gate_mode_warn_loads(self, rules_dir: Path) -> None:
        (rules_dir / "trader_rules" / "ok_gate.yaml").write_text(
            yaml.dump({"gate_mode": "warn"}), encoding="utf-8"
        )
        rules = load_effective_rules("ok_gate", rules_dir=str(rules_dir))
        assert rules.gate_mode == "warn"

    def test_valid_risk_mode_fixed_loads(self, rules_dir: Path) -> None:
        (rules_dir / "trader_rules" / "ok_risk.yaml").write_text(
            yaml.dump({"risk_mode": "risk_usdt_fixed", "risk_usdt_fixed": 20.0}),
            encoding="utf-8",
        )
        rules = load_effective_rules("ok_risk", rules_dir=str(rules_dir))
        assert rules.risk_mode == "risk_usdt_fixed"

    def test_invalid_gate_mode_in_global_defaults_raises(self, tmp_path: Path) -> None:
        global_yaml = {
            "global_hard_caps": {
                "max_capital_at_risk_pct": 10.0,
                "hard_max_per_signal_risk_pct": 2.0,
                "market_execution": {"mode": "tolerance", "tolerance_pct": 0.5, "range_tolerance_pct": 0.2},
            },
            "global_defaults": {
                "enabled": True,
                "gate_mode": "bloock",  # typo
                "risk_mode": "risk_pct_of_capital",
                "capital_base_mode": "static_config",
                "capital_base_usdt": 1000.0,
                "leverage": 1,
            },
        }
        (tmp_path / "operation_rules.yaml").write_text(
            yaml.dump(global_yaml), encoding="utf-8"
        )
        (tmp_path / "trader_rules").mkdir()
        with pytest.raises(ValueError, match="gate_mode"):
            load_effective_rules("any", rules_dir=str(tmp_path))

    def test_gate_mode_case_insensitive(self, rules_dir: Path) -> None:
        (rules_dir / "trader_rules" / "upper_gate.yaml").write_text(
            yaml.dump({"gate_mode": "WARN"}), encoding="utf-8"
        )
        rules = load_effective_rules("upper_gate", rules_dir=str(rules_dir))
        assert rules.gate_mode == "warn"


class TestNewSectionsValidation:
    """Validation for market_execution, sl.be_trigger, pending, tp.close_distribution."""

    def test_invalid_market_execution_mode_raises(self, rules_dir: Path) -> None:
        (rules_dir / "trader_rules" / "bad_me_mode.yaml").write_text(
            yaml.dump({}), encoding="utf-8"
        )
        # Inject bad mode via direct global_hard_caps — need fresh rules_dir
        bad_global = {
            "registered_traders": ["any"],
            "global_hard_caps": {
                "max_capital_at_risk_pct": 10.0,
                "hard_max_per_signal_risk_pct": 2.0,
                "market_execution": {"mode": "aggressive"},  # invalid
            },
            "global_defaults": {
                "enabled": True,
                "gate_mode": "block",
                "risk_mode": "risk_pct_of_capital",
                "capital_base_mode": "static_config",
                "capital_base_usdt": 1000.0,
                "leverage": 1,
            },
        }
        (rules_dir / "operation_rules.yaml").write_text(
            yaml.dump(bad_global), encoding="utf-8"
        )
        with pytest.raises(ValueError, match="market_execution.mode"):
            load_effective_rules("any", rules_dir=str(rules_dir))

    def test_invalid_be_trigger_raises(self, rules_dir: Path) -> None:
        (rules_dir / "trader_rules" / "bad_be_trigger.yaml").write_text(
            yaml.dump({"sl": {"be_trigger": "tp9"}}), encoding="utf-8"
        )
        with pytest.raises(ValueError, match="sl.be_trigger"):
            load_effective_rules("bad_be_trigger", rules_dir=str(rules_dir))

    def test_valid_be_trigger_loads(self, rules_dir: Path) -> None:
        (rules_dir / "trader_rules" / "bad_be_trigger.yaml").write_text(
            yaml.dump({"sl": {"be_trigger": "tp2"}}), encoding="utf-8"
        )
        rules = load_effective_rules("bad_be_trigger", rules_dir=str(rules_dir))
        assert rules.sl["be_trigger"] == "tp2"

    def test_invalid_cancel_averaging_pending_after_raises(self, rules_dir: Path) -> None:
        (rules_dir / "trader_rules" / "bad_cancel_avg.yaml").write_text(
            yaml.dump({"pending": {"cancel_averaging_pending_after": "tp5"}}), encoding="utf-8"
        )
        with pytest.raises(ValueError, match="cancel_averaging_pending_after"):
            load_effective_rules("bad_cancel_avg", rules_dir=str(rules_dir))

    def test_invalid_close_distribution_mode_raises(self, rules_dir: Path) -> None:
        (rules_dir / "trader_rules" / "bad_cd_mode.yaml").write_text(
            yaml.dump({"tp": {"close_distribution": {"mode": "weighted"}}}), encoding="utf-8"
        )
        with pytest.raises(ValueError, match="tp.close_distribution.mode"):
            load_effective_rules("bad_cd_mode", rules_dir=str(rules_dir))
