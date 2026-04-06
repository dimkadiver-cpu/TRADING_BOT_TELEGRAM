"""Tests for ScenarioLoader v2 — sweep, matrix, preset, and validation.

Step 25 — Fase 8.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from src.backtesting.scenario import ScenarioLoader


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_yaml(tmp_path: Path, content: str) -> str:
    """Write *content* to a temp YAML file and return the path string."""
    p = tmp_path / "test_scenarios.yaml"
    p.write_text(content, encoding="utf-8")
    return str(p)


_SETTINGS = """\
backtest_settings:
  capital_base_usdt: 1000.0
"""


# ---------------------------------------------------------------------------
# Explicit scenario (backward compatibility)
# ---------------------------------------------------------------------------

class TestExplicitScenario:
    def test_load_single_explicit_scenario(self, tmp_path: Path) -> None:
        """An explicit scenario without preset loads with correct conditions."""
        path = _write_yaml(tmp_path, """\
scenarios:
  - name: signals_only
    description: "Solo segnali puri"
    conditions:
      risk_pct: 1.5
      gate_mode: strict
backtest_settings:
  capital_base_usdt: 1000.0
""")
        config = ScenarioLoader.load(path)
        assert len(config.scenarios) == 1
        s = config.scenarios[0]
        assert s.name == "signals_only"
        assert s.conditions.risk_pct == 1.5
        assert s.conditions.gate_mode == "strict"

    def test_load_legacy_fase7_fields(self, tmp_path: Path) -> None:
        """Old Fase 7 YAML fields are migrated transparently."""
        path = _write_yaml(tmp_path, """\
scenarios:
  - name: follow_full_chain
    description: "Legacy"
    conditions:
      follow_full_chain: true
      signals_only: false
      sl_to_be_after_tp2: false
      vary_entry_pct: null
      risk_pct_variant: null
      gate_mode_variant: null
backtest_settings:
  capital_base_usdt: 1000.0
""")
        config = ScenarioLoader.load(path)
        assert len(config.scenarios) == 1
        assert config.scenarios[0].name == "follow_full_chain"


# ---------------------------------------------------------------------------
# Preset + override
# ---------------------------------------------------------------------------

class TestPresetAndOverride:
    def test_extends_preset_applies_base_conditions(self, tmp_path: Path) -> None:
        """A scenario that extends a preset inherits its conditions."""
        path = _write_yaml(tmp_path, """\
presets:
  base:
    risk_pct: 1.0
    entry:
      selection: all
      price_mode: exact

scenarios:
  - name: conservative
    description: "Conservative"
    extends: base
backtest_settings:
  capital_base_usdt: 1000.0
""")
        config = ScenarioLoader.load(path)
        assert len(config.scenarios) == 1
        s = config.scenarios[0]
        assert s.conditions.risk_pct == 1.0
        assert s.conditions.entry.selection == "all"
        assert s.conditions.entry.price_mode == "exact"

    def test_overrides_scalar_replaces_preset_value(self, tmp_path: Path) -> None:
        """Scalar override replaces the preset scalar."""
        path = _write_yaml(tmp_path, """\
presets:
  base:
    risk_pct: 1.0
    gate_mode: strict

scenarios:
  - name: aggressive
    description: "More risk"
    extends: base
    overrides:
      risk_pct: 2.0
backtest_settings:
  capital_base_usdt: 1000.0
""")
        config = ScenarioLoader.load(path)
        s = config.scenarios[0]
        assert s.conditions.risk_pct == 2.0
        assert s.conditions.gate_mode == "strict"  # inherited from preset

    def test_deep_merge_does_not_replace_entire_sub_object(self, tmp_path: Path) -> None:
        """Override of a sub-dict merges, not replaces the sub-object."""
        path = _write_yaml(tmp_path, """\
presets:
  base:
    management:
      sl_to_be_after_tp: null
    entry:
      selection: all
      price_mode: exact

scenarios:
  - name: be_after_tp1
    description: "BE after TP1"
    extends: base
    overrides:
      management:
        sl_to_be_after_tp: 1
backtest_settings:
  capital_base_usdt: 1000.0
""")
        config = ScenarioLoader.load(path)
        s = config.scenarios[0]
        # overridden field
        assert s.conditions.management.sl_to_be_after_tp == 1
        # inherited from preset — NOT replaced by override
        assert s.conditions.entry.selection == "all"
        assert s.conditions.entry.price_mode == "exact"

    def test_unknown_preset_raises_value_error(self, tmp_path: Path) -> None:
        """Referencing a nonexistent preset raises ValueError."""
        path = _write_yaml(tmp_path, """\
presets:
  base:
    risk_pct: 1.0

scenarios:
  - name: oops
    extends: nonexistent_preset
backtest_settings:
  capital_base_usdt: 1000.0
""")
        with pytest.raises(ValueError, match="nonexistent_preset"):
            ScenarioLoader.load(path)


# ---------------------------------------------------------------------------
# Sweep expansion
# ---------------------------------------------------------------------------

class TestSweepExpansion:
    def test_sweep_generates_correct_count(self, tmp_path: Path) -> None:
        """Sweep over 3 values generates exactly 3 scenarios."""
        path = _write_yaml(tmp_path, """\
sweep:
  - name: risk_sweep
    description: "Risk sweep"
    base_conditions:
      risk_pct: 1.0
    sweep_variable: risk_pct
    sweep_values: [0.5, 1.0, 2.0]
backtest_settings:
  capital_base_usdt: 1000.0
""")
        config = ScenarioLoader.load(path)
        assert len(config.scenarios) == 3

    def test_sweep_names_are_prefixed_with_value(self, tmp_path: Path) -> None:
        """Each sweep scenario name is f'{prefix}_{value}'."""
        path = _write_yaml(tmp_path, """\
sweep:
  - name: risk_sweep
    description: "Risk sweep"
    base_conditions:
      risk_pct: 1.0
    sweep_variable: risk_pct
    sweep_values: [0.5, 1.0, 2.0]
backtest_settings:
  capital_base_usdt: 1000.0
""")
        config = ScenarioLoader.load(path)
        names = [s.name for s in config.scenarios]
        assert names == ["risk_sweep_0.5", "risk_sweep_1.0", "risk_sweep_2.0"]

    def test_sweep_values_applied_to_conditions(self, tmp_path: Path) -> None:
        """Each scenario gets the swept value in its conditions."""
        path = _write_yaml(tmp_path, """\
sweep:
  - name: risk_sweep
    description: "Risk sweep"
    base_conditions:
      risk_pct: 1.0
    sweep_variable: risk_pct
    sweep_values: [0.5, 1.5]
backtest_settings:
  capital_base_usdt: 1000.0
""")
        config = ScenarioLoader.load(path)
        risk_values = [s.conditions.risk_pct for s in config.scenarios]
        assert risk_values == [0.5, 1.5]

    def test_sweep_nested_variable(self, tmp_path: Path) -> None:
        """Sweep on a nested path like 'entry.selection' resolves correctly."""
        path = _write_yaml(tmp_path, """\
sweep:
  - name: entry_sel
    description: "Entry selection sweep"
    base_conditions:
      risk_pct: 1.0
    sweep_variable: entry.selection
    sweep_values: [first_only, all]
backtest_settings:
  capital_base_usdt: 1000.0
""")
        config = ScenarioLoader.load(path)
        assert len(config.scenarios) == 2
        assert config.scenarios[0].conditions.entry.selection == "first_only"
        assert config.scenarios[1].conditions.entry.selection == "all"

    def test_sweep_invalid_variable_raises_error(self, tmp_path: Path) -> None:
        """Sweep on a nonexistent ScenarioConditions field raises ValueError."""
        path = _write_yaml(tmp_path, """\
sweep:
  - name: bad_sweep
    description: "Bad sweep"
    base_conditions:
      risk_pct: 1.0
    sweep_variable: nonexistent_field
    sweep_values: [1, 2]
backtest_settings:
  capital_base_usdt: 1000.0
""")
        with pytest.raises(ValueError, match="nonexistent_field"):
            ScenarioLoader.load(path)

    def test_sweep_invalid_nested_variable_raises_error(self, tmp_path: Path) -> None:
        """Sweep on a nonexistent nested field (e.g. 'entry.bad') raises ValueError."""
        path = _write_yaml(tmp_path, """\
sweep:
  - name: bad_nested
    description: "Bad nested sweep"
    base_conditions:
      risk_pct: 1.0
    sweep_variable: entry.nonexistent_sub
    sweep_values: [a, b]
backtest_settings:
  capital_base_usdt: 1000.0
""")
        with pytest.raises(ValueError, match="entry.nonexistent_sub"):
            ScenarioLoader.load(path)


# ---------------------------------------------------------------------------
# Matrix expansion
# ---------------------------------------------------------------------------

class TestMatrixExpansion:
    def test_matrix_generates_cartesian_product(self, tmp_path: Path) -> None:
        """Matrix with 2×3 variables generates 6 scenarios."""
        path = _write_yaml(tmp_path, """\
matrix:
  - name: entry_x_tp
    description: "Entry x TP matrix"
    variables:
      entry.price_mode: [exact, average]
      tp.count: [1, 2, null]
backtest_settings:
  capital_base_usdt: 1000.0
""")
        config = ScenarioLoader.load(path)
        assert len(config.scenarios) == 6

    def test_matrix_scenario_names_contain_combo_values(self, tmp_path: Path) -> None:
        """Matrix scenario names are f'{prefix}_{v1}_{v2}'."""
        path = _write_yaml(tmp_path, """\
matrix:
  - name: m
    description: "Matrix"
    variables:
      entry.selection: [first_only, all]
      tp.count: [1, 2]
backtest_settings:
  capital_base_usdt: 1000.0
""")
        config = ScenarioLoader.load(path)
        names = {s.name for s in config.scenarios}
        assert names == {
            "m_first_only_1",
            "m_first_only_2",
            "m_all_1",
            "m_all_2",
        }

    def test_matrix_conditions_reflect_combo_values(self, tmp_path: Path) -> None:
        """Each matrix scenario has the correct variable values in conditions."""
        path = _write_yaml(tmp_path, """\
matrix:
  - name: m
    description: "Matrix"
    variables:
      entry.selection: [first_only]
      tp.count: [1]
backtest_settings:
  capital_base_usdt: 1000.0
""")
        config = ScenarioLoader.load(path)
        assert len(config.scenarios) == 1
        s = config.scenarios[0]
        assert s.conditions.entry.selection == "first_only"
        assert s.conditions.tp.count == 1

    def test_matrix_over_50_emits_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Matrix generating >50 scenarios emits a WARNING log."""
        # 4 × 4 × 4 = 64 scenarios
        path = _write_yaml(tmp_path, """\
matrix:
  - name: big
    description: "Big matrix"
    variables:
      tp.count: [1, 2, 3, null]
      entry.selection: [all, first_only, last_only, all]
      entry.price_mode: [exact, average, extreme_min, extreme_max]
backtest_settings:
  capital_base_usdt: 1000.0
""")
        with caplog.at_level(logging.WARNING, logger="src.backtesting.scenario"):
            config = ScenarioLoader.load(path)

        assert len(config.scenarios) > 50
        assert any("50" in record.message for record in caplog.records)

    def test_matrix_invalid_variable_raises_error(self, tmp_path: Path) -> None:
        """Matrix with nonexistent variable path raises ValueError."""
        path = _write_yaml(tmp_path, """\
matrix:
  - name: bad_matrix
    description: "Bad matrix"
    variables:
      entry.bad_field: [a, b]
backtest_settings:
  capital_base_usdt: 1000.0
""")
        with pytest.raises(ValueError, match="entry.bad_field"):
            ScenarioLoader.load(path)


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------

class TestValidationErrors:
    def test_invalid_conditions_raise_value_error(self, tmp_path: Path) -> None:
        """A scenario with conditions that fail ScenarioConditions validation raises ValueError."""
        # entry.split [0.3, 0.3] sums to 0.6, not 1.0 → fails EntryConfig validation
        path = _write_yaml(tmp_path, """\
scenarios:
  - name: bad_split
    description: "Invalid split"
    conditions:
      entry:
        split: [0.3, 0.3]
backtest_settings:
  capital_base_usdt: 1000.0
""")
        with pytest.raises(ValueError, match="bad_split"):
            ScenarioLoader.load(path)

    def test_sweep_generates_invalid_scenario_raises_error(self, tmp_path: Path) -> None:
        """If sweep produces a scenario with invalid conditions, ValueError is raised."""
        # tp.close_scheme sums to 0.6, not 1.0 → fails TpConfig validation
        path = _write_yaml(tmp_path, """\
sweep:
  - name: bad_scheme_sweep
    description: "Bad close scheme sweep"
    base_conditions:
      tp:
        close_scheme: [0.3, 0.3]
    sweep_variable: risk_pct
    sweep_values: [1.0]
backtest_settings:
  capital_base_usdt: 1000.0
""")
        with pytest.raises(ValueError):
            ScenarioLoader.load(path)


# ---------------------------------------------------------------------------
# Combined modes + backward compatibility
# ---------------------------------------------------------------------------

class TestCombinedAndBackwardCompat:
    def test_all_modes_combined_in_one_file(self, tmp_path: Path) -> None:
        """File with presets + scenarios + sweep + matrix loads without error."""
        path = _write_yaml(tmp_path, """\
presets:
  base:
    risk_pct: 1.0

scenarios:
  - name: explicit_one
    extends: base
    overrides:
      risk_pct: 0.5

sweep:
  - name: risk_sweep
    description: "Risk sweep"
    base_conditions:
      risk_pct: 1.0
    sweep_variable: risk_pct
    sweep_values: [0.5, 1.0]

matrix:
  - name: m
    description: "Matrix"
    variables:
      entry.selection: [all, first_only]
backtest_settings:
  capital_base_usdt: 1000.0
""")
        config = ScenarioLoader.load(path)
        # 1 explicit + 2 sweep + 2 matrix = 5
        assert len(config.scenarios) == 5

    def test_old_backtest_scenarios_yaml_still_loads(self) -> None:
        """The original Fase 7 YAML file loads without error."""
        config = ScenarioLoader.load("config/backtest_scenarios.yaml")
        assert len(config.scenarios) == 6  # original file has 6 scenarios

    def test_new_backtest_scenarios_v2_yaml_loads(self) -> None:
        """The new v2 YAML file loads without error and generates the expected count."""
        config = ScenarioLoader.load("config/backtest_scenarios_v2.yaml")
        # 2 explicit + 3 risk_sweep + 2 entry_selection_sweep + 6 matrix = 13
        assert len(config.scenarios) == 13
