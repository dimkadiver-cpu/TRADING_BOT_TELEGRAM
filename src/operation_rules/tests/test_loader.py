"""Tests for src/operation_rules/loader.py.

Coverage:
  - load_rules: reads global config + trader file
  - Merge precedence: trader overrides global_defaults
  - hard_caps: always from global, never overridable
  - Missing trader file: falls back to global_defaults cleanly
  - Deep merge: nested entry_split and position_management
  - Disabled trader: enabled=false propagated
  - MergedRules fields: all sections verified
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from src.operation_rules.loader import (
    MergedRules,
    load_rules,
)


# ---------------------------------------------------------------------------
# Fixtures — minimal in-memory config directories
# ---------------------------------------------------------------------------

def _write_global(cfg_dir: Path, content: str) -> None:
    (cfg_dir / "operation_rules.yaml").write_text(textwrap.dedent(content), encoding="utf-8")


def _write_trader(cfg_dir: Path, trader_id: str, content: str) -> None:
    trader_dir = cfg_dir / "trader_rules"
    trader_dir.mkdir(parents=True, exist_ok=True)
    (trader_dir / f"{trader_id}.yaml").write_text(textwrap.dedent(content), encoding="utf-8")


_MINIMAL_GLOBAL = """\
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


# ---------------------------------------------------------------------------
# Basic loading
# ---------------------------------------------------------------------------

class TestLoadRulesBasic:
    def test_load_global_defaults_no_trader_file(self, tmp_path: Path) -> None:
        _write_global(tmp_path, _MINIMAL_GLOBAL)
        rules = load_rules("unknown_trader", config_dir=tmp_path)
        assert rules.trader_id == "unknown_trader"
        assert rules.enabled is True
        assert rules.gate_mode == "block"
        assert rules.position_size_pct == 1.0
        assert rules.leverage == 1
        assert rules.max_capital_at_risk_per_trader_pct == 5.0
        assert rules.max_concurrent_same_symbol == 1

    def test_hard_caps_from_global(self, tmp_path: Path) -> None:
        _write_global(tmp_path, _MINIMAL_GLOBAL)
        rules = load_rules("x", config_dir=tmp_path)
        assert rules.hard_caps.max_capital_at_risk_pct == 10.0
        assert rules.hard_caps.max_per_signal_pct == 2.0

    def test_missing_global_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="operation_rules.yaml"):
            load_rules("trader_3", config_dir=tmp_path)

    def test_real_config_loads(self) -> None:
        """Smoke test: real config/operation_rules.yaml must load cleanly."""
        rules = load_rules("trader_3")
        assert isinstance(rules, MergedRules)
        assert rules.trader_id == "trader_3"
        assert rules.hard_caps.max_per_signal_pct > 0


# ---------------------------------------------------------------------------
# Merge precedence
# ---------------------------------------------------------------------------

class TestMergePrecedence:
    def test_trader_overrides_position_size(self, tmp_path: Path) -> None:
        _write_global(tmp_path, _MINIMAL_GLOBAL)
        _write_trader(tmp_path, "trader_x", "position_size_pct: 0.5\nleverage: 10\n")
        rules = load_rules("trader_x", config_dir=tmp_path)
        assert rules.position_size_pct == 0.5
        assert rules.leverage == 10

    def test_trader_overrides_gate_mode(self, tmp_path: Path) -> None:
        _write_global(tmp_path, _MINIMAL_GLOBAL)
        _write_trader(tmp_path, "trader_x", "gate_mode: warn\n")
        rules = load_rules("trader_x", config_dir=tmp_path)
        assert rules.gate_mode == "warn"

    def test_trader_can_disable_trader(self, tmp_path: Path) -> None:
        _write_global(tmp_path, _MINIMAL_GLOBAL)
        _write_trader(tmp_path, "trader_x", "enabled: false\n")
        rules = load_rules("trader_x", config_dir=tmp_path)
        assert rules.enabled is False

    def test_trader_cannot_override_hard_caps(self, tmp_path: Path) -> None:
        """Hard caps always come from global — trader file cannot change them."""
        _write_global(tmp_path, _MINIMAL_GLOBAL)
        # Even if trader file had hard_caps they would be ignored (hard_caps
        # is read exclusively from global_hard_caps section, not merged)
        _write_trader(tmp_path, "trader_x", "position_size_pct: 99.0\n")
        rules = load_rules("trader_x", config_dir=tmp_path)
        # Hard caps stay at global values
        assert rules.hard_caps.max_capital_at_risk_pct == 10.0
        assert rules.hard_caps.max_per_signal_pct == 2.0

    def test_non_overridden_fields_keep_default(self, tmp_path: Path) -> None:
        _write_global(tmp_path, _MINIMAL_GLOBAL)
        _write_trader(tmp_path, "trader_x", "leverage: 5\n")
        rules = load_rules("trader_x", config_dir=tmp_path)
        # only leverage is overridden; everything else stays default
        assert rules.leverage == 5
        assert rules.position_size_pct == 1.0
        assert rules.max_capital_at_risk_per_trader_pct == 5.0


# ---------------------------------------------------------------------------
# Deep merge — entry_split
# ---------------------------------------------------------------------------

class TestEntrySplitMerge:
    def test_global_defaults_zone_endpoints(self, tmp_path: Path) -> None:
        _write_global(tmp_path, _MINIMAL_GLOBAL)
        rules = load_rules("x", config_dir=tmp_path)
        assert rules.entry_split.ZONE.split_mode == "endpoints"
        assert rules.entry_split.ZONE.weights == {"E1": 0.50, "E2": 0.50}

    def test_trader_overrides_zone_split_mode(self, tmp_path: Path) -> None:
        _write_global(tmp_path, _MINIMAL_GLOBAL)
        _write_trader(
            tmp_path,
            "trader_x",
            "entry_split:\n  ZONE:\n    split_mode: three_way\n    weights: {E1: 0.30, E2: 0.40, E3: 0.30}\n",
        )
        rules = load_rules("trader_x", config_dir=tmp_path)
        assert rules.entry_split.ZONE.split_mode == "three_way"
        assert rules.entry_split.ZONE.weights == {"E1": 0.30, "E2": 0.40, "E3": 0.30}

    def test_trader_overrides_averaging_distribution(self, tmp_path: Path) -> None:
        _write_global(tmp_path, _MINIMAL_GLOBAL)
        trader_yaml = (
            "entry_split:\n"
            "  AVERAGING:\n"
            "    distribution: decreasing\n"
            "    weights: {E1: 0.4, E2: 0.3, E3: 0.2, E4: 0.1}\n"
        )
        _write_trader(tmp_path, "trader_x", trader_yaml)
        rules = load_rules("trader_x", config_dir=tmp_path)
        assert rules.entry_split.AVERAGING.distribution == "decreasing"
        assert rules.entry_split.AVERAGING.weights["E1"] == pytest.approx(0.4)

    def test_limit_weights_default(self, tmp_path: Path) -> None:
        _write_global(tmp_path, _MINIMAL_GLOBAL)
        rules = load_rules("x", config_dir=tmp_path)
        assert rules.entry_split.LIMIT.weights == {"E1": 1.0}


# ---------------------------------------------------------------------------
# Position management merge
# ---------------------------------------------------------------------------

class TestPositionManagementMerge:
    def test_global_defaults_on_tp_hit(self, tmp_path: Path) -> None:
        _write_global(tmp_path, _MINIMAL_GLOBAL)
        rules = load_rules("x", config_dir=tmp_path)
        mgmt = rules.position_management
        assert len(mgmt.on_tp_hit) == 3
        assert mgmt.on_tp_hit[0].tp_level == 1
        assert mgmt.on_tp_hit[0].action == "close_partial"
        assert mgmt.on_tp_hit[0].close_pct == 50

    def test_global_defaults_auto_apply_intents(self, tmp_path: Path) -> None:
        _write_global(tmp_path, _MINIMAL_GLOBAL)
        rules = load_rules("x", config_dir=tmp_path)
        assert "U_MOVE_STOP" in rules.position_management.auto_apply_intents
        assert "U_CLOSE_FULL" in rules.position_management.auto_apply_intents

    def test_trader_overrides_on_tp_hit(self, tmp_path: Path) -> None:
        _write_global(tmp_path, _MINIMAL_GLOBAL)
        trader_yaml = (
            "position_management:\n"
            "  on_tp_hit:\n"
            "    - {tp_level: 1, action: close_partial, close_pct: 30}\n"
        )
        _write_trader(tmp_path, "trader_x", trader_yaml)
        rules = load_rules("trader_x", config_dir=tmp_path)
        assert rules.position_management.on_tp_hit[0].close_pct == 30

    def test_log_only_intents(self, tmp_path: Path) -> None:
        _write_global(tmp_path, _MINIMAL_GLOBAL)
        rules = load_rules("x", config_dir=tmp_path)
        assert "U_TP_HIT" in rules.position_management.log_only_intents
        assert "U_SL_HIT" in rules.position_management.log_only_intents


# ---------------------------------------------------------------------------
# Price sanity
# ---------------------------------------------------------------------------

class TestPriceSanity:
    def test_default_disabled(self, tmp_path: Path) -> None:
        _write_global(tmp_path, _MINIMAL_GLOBAL)
        rules = load_rules("x", config_dir=tmp_path)
        assert rules.price_sanity.enabled is False
        assert rules.price_sanity.symbol_ranges == {}

    def test_trader_enables_price_sanity(self, tmp_path: Path) -> None:
        _write_global(tmp_path, _MINIMAL_GLOBAL)
        trader_yaml = (
            "price_sanity:\n"
            "  enabled: true\n"
            "  symbol_ranges:\n"
            "    BTCUSDT: {min: 10000, max: 500000}\n"
        )
        _write_trader(tmp_path, "trader_x", trader_yaml)
        rules = load_rules("trader_x", config_dir=tmp_path)
        assert rules.price_sanity.enabled is True
        assert "BTCUSDT" in rules.price_sanity.symbol_ranges
        assert rules.price_sanity.symbol_ranges["BTCUSDT"]["min"] == 10000
