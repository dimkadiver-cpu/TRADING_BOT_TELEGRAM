"""Tests for src/operation_rules/loader.py."""

from __future__ import annotations

import pytest
import yaml
from pathlib import Path

from src.operation_rules.loader import load_effective_rules, HardCaps


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def rules_dir(tmp_path: Path) -> Path:
    """Create a minimal config directory structure."""
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
            "price_corrections": {"enabled": False, "method": None},
            "price_sanity": {"enabled": False, "symbol_ranges": {}},
            "position_management": {
                "on_tp_hit": [
                    {"tp_level": 1, "action": "close_partial", "close_pct": 50},
                    {"tp_level": 2, "action": "move_to_be"},
                    {"tp_level": 3, "action": "close_full"},
                ],
                "auto_apply_intents": ["U_MOVE_STOP", "U_CLOSE_FULL"],
                "log_only_intents": ["U_TP_HIT", "U_SL_HIT"],
            },
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
        """Missing trader file → all defaults from global."""
        rules = load_effective_rules("unknown_trader", rules_dir=str(rules_dir))
        assert rules.enabled is True
        assert rules.gate_mode == "block"
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

    def test_entry_split_defaults(self, rules_dir: Path) -> None:
        rules = load_effective_rules("any", rules_dir=str(rules_dir))
        assert "ZONE" in rules.entry_split
        assert "LIMIT" in rules.entry_split
        assert "MARKET" in rules.entry_split
        assert "AVERAGING" in rules.entry_split

    def test_missing_global_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_effective_rules("any", rules_dir=str(tmp_path))


class TestLoaderTraderOverride:
    def test_trader_overrides_gate_mode(self, rules_dir: Path) -> None:
        trader_yaml = {"gate_mode": "warn", "risk_pct_of_capital": 0.5}
        (rules_dir / "trader_rules" / "my_trader.yaml").write_text(
            yaml.dump(trader_yaml), encoding="utf-8"
        )
        rules = load_effective_rules("my_trader", rules_dir=str(rules_dir))
        assert rules.gate_mode == "warn"
        assert rules.risk_pct_of_capital == 0.5
        # Non-overridden keys still from defaults
        assert rules.leverage == 1

    def test_trader_disables(self, rules_dir: Path) -> None:
        (rules_dir / "trader_rules" / "disabled.yaml").write_text(
            yaml.dump({"enabled": False}), encoding="utf-8"
        )
        rules = load_effective_rules("disabled", rules_dir=str(rules_dir))
        assert rules.enabled is False

    def test_hard_caps_not_overridable(self, rules_dir: Path) -> None:
        """Trader YAML cannot override global_hard_caps."""
        trader_yaml = {
            "global_hard_caps": {  # This key is not in the merge path
                "max_capital_at_risk_pct": 999.0,
                "hard_max_per_signal_risk_pct": 999.0,
            }
        }
        (rules_dir / "trader_rules" / "hacker.yaml").write_text(
            yaml.dump(trader_yaml), encoding="utf-8"
        )
        rules = load_effective_rules("hacker", rules_dir=str(rules_dir))
        # Hard caps remain from global
        assert rules.hard_caps.max_capital_at_risk_pct == 10.0
        assert rules.hard_caps.hard_max_per_signal_risk_pct == 2.0

    def test_entry_split_deep_merge(self, rules_dir: Path) -> None:
        """Trader can override specific entry types without losing others."""
        trader_yaml = {
            "entry_split": {
                "ZONE": {"split_mode": "three_way"},
            }
        }
        (rules_dir / "trader_rules" / "splitter.yaml").write_text(
            yaml.dump(trader_yaml), encoding="utf-8"
        )
        rules = load_effective_rules("splitter", rules_dir=str(rules_dir))
        assert rules.entry_split["ZONE"]["split_mode"] == "three_way"
        # Other entry types still present
        assert "LIMIT" in rules.entry_split
        assert "MARKET" in rules.entry_split
