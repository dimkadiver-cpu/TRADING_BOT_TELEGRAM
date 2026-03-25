"""Operation rules config loader.

Loads and merges YAML configs with 4-level priority:

    global_hard_caps  (max_capital_at_risk_pct, max_per_signal_pct)
    > trader_on_off   (enabled, gate_mode)
    > trader_specific (all other trader YAML keys)
    > global_defaults (everything in global_defaults section)

Usage:
    rules = load_effective_rules("trader_3")
    rules = load_effective_rules("trader_3", rules_dir="config")
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class HardCaps:
    max_capital_at_risk_pct: float
    max_per_signal_pct: float


@dataclass
class EffectiveRules:
    """Fully resolved rules for a single trader after 4-level merge."""

    hard_caps: HardCaps

    # Trader on/off + mode
    enabled: bool
    gate_mode: str  # "block" | "warn"

    # Set A — position opening
    use_trader_risk_hint: bool
    position_size_pct: float
    leverage: int
    max_capital_at_risk_per_trader_pct: float
    max_concurrent_same_symbol: int

    # Entry split config (nested dict keyed by entry type)
    entry_split: dict[str, Any]

    # Price corrections (future)
    price_corrections: dict[str, Any]

    # Price sanity (optional)
    price_sanity: dict[str, Any]

    # Set B — snapshot management rules
    position_management: dict[str, Any]

    # Raw merged dict for serialization in management_rules_json
    _raw: dict[str, Any] = field(default_factory=dict, repr=False)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge override into a copy of base."""
    result = copy.deepcopy(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = copy.deepcopy(val)
    return result


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_effective_rules(trader_id: str, *, rules_dir: str = "config") -> EffectiveRules:
    """Load and merge configs for *trader_id*.

    Raises:
        FileNotFoundError: if the global config file is missing.
    """
    root = Path(rules_dir)
    global_path = root / "operation_rules.yaml"
    if not global_path.exists():
        raise FileNotFoundError(f"Global operation rules not found: {global_path}")

    global_data = _load_yaml(global_path)
    global_defaults: dict[str, Any] = global_data.get("global_defaults", {})
    hard_caps_raw: dict[str, Any] = global_data.get("global_hard_caps", {})

    # Load trader-specific YAML (missing file → empty dict, uses all defaults)
    trader_path = root / "trader_rules" / f"{trader_id}.yaml"
    trader_data = _load_yaml(trader_path)

    # Merge: start from global_defaults, apply trader overrides on top
    merged = _deep_merge(global_defaults, trader_data)

    # Hard caps are always final (never overridable)
    hard_caps = HardCaps(
        max_capital_at_risk_pct=float(hard_caps_raw.get("max_capital_at_risk_pct", 10.0)),
        max_per_signal_pct=float(hard_caps_raw.get("max_per_signal_pct", 2.0)),
    )

    entry_split = merged.get("entry_split", {})
    # Normalise entry_split: ensure each entry type exists
    for et in ("ZONE", "AVERAGING", "LIMIT", "MARKET"):
        if et not in entry_split:
            entry_split[et] = {}

    return EffectiveRules(
        hard_caps=hard_caps,
        enabled=bool(merged.get("enabled", True)),
        gate_mode=str(merged.get("gate_mode", "block")),
        use_trader_risk_hint=bool(merged.get("use_trader_risk_hint", False)),
        position_size_pct=float(merged.get("position_size_pct", 1.0)),
        leverage=int(merged.get("leverage", 1)),
        max_capital_at_risk_per_trader_pct=float(
            merged.get("max_capital_at_risk_per_trader_pct", 5.0)
        ),
        max_concurrent_same_symbol=int(merged.get("max_concurrent_same_symbol", 1)),
        entry_split=entry_split,
        price_corrections=merged.get("price_corrections", {"enabled": False, "method": None}),
        price_sanity=merged.get("price_sanity", {"enabled": False, "symbol_ranges": {}}),
        position_management=merged.get("position_management", {}),
        _raw=merged,
    )
