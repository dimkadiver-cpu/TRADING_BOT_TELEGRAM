"""Operation rules config loader.

Loads and merges YAML configs with 4-level priority:

    global_hard_caps  (max_capital_at_risk_pct, max_per_signal_pct, market_execution)
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
    hard_max_per_signal_risk_pct: float
    market_execution: dict[str, Any]


@dataclass
class EffectiveRules:
    """Fully resolved rules for a single trader after 4-level merge."""

    hard_caps: HardCaps
    registered_traders: tuple[str, ...]
    is_registered: bool

    # Trader on/off + mode
    enabled: bool
    gate_mode: str  # "block" | "warn"
    operation_rules: str  # "override" | "global"

    # Set A — position opening (risk-first model)
    use_trader_risk_hint: bool
    risk_mode: str                   # "risk_pct_of_capital" | "risk_usdt_fixed"
    risk_pct_of_capital: float       # % capitale per trade
    risk_usdt_fixed: float           # USDT fissi se risk_usdt_fixed
    capital_base_mode: str           # "static_config" | "live_equity"
    capital_base_usdt: float         # capitale di riferimento
    leverage: int
    max_capital_at_risk_per_trader_pct: float
    max_concurrent_same_symbol: int

    # Entry split config (nested dict keyed by entry type)
    entry_split: dict[str, Any]

    # TP management
    tp: dict[str, Any]

    # SL management
    sl: dict[str, Any]

    # UPDATE intents policy (Set B — snapshot letto da Sistema 1)
    updates: dict[str, Any]

    # Pending orders management
    pending: dict[str, Any]

    # Price corrections (future)
    price_corrections: dict[str, Any]

    # Price sanity (optional)
    price_sanity: dict[str, Any]

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


def _validate_enum_fields(merged: dict[str, Any], trader_id: str) -> None:
    """Fail-fast validation for enumerated string fields."""
    _GATE_MODE_VALUES = {"block", "warn"}
    _RISK_MODE_VALUES = {"risk_pct_of_capital", "risk_usdt_fixed"}
    _CAPITAL_BASE_MODE_VALUES = {"static_config", "live_equity"}

    gate_mode = str(merged.get("gate_mode", "block")).lower()
    if gate_mode not in _GATE_MODE_VALUES:
        raise ValueError(
            f"[{trader_id}] gate_mode must be one of {sorted(_GATE_MODE_VALUES)}, "
            f"got: {gate_mode!r}"
        )

    risk_mode = str(merged.get("risk_mode", "risk_pct_of_capital")).lower()
    if risk_mode not in _RISK_MODE_VALUES:
        raise ValueError(
            f"[{trader_id}] risk_mode must be one of {sorted(_RISK_MODE_VALUES)}, "
            f"got: {risk_mode!r}"
        )

    capital_base_mode = str(merged.get("capital_base_mode", "static_config")).lower()
    if capital_base_mode not in _CAPITAL_BASE_MODE_VALUES:
        raise ValueError(
            f"[{trader_id}] capital_base_mode must be one of "
            f"{sorted(_CAPITAL_BASE_MODE_VALUES)}, got: {capital_base_mode!r}"
        )


def _validate_new_sections(
    market_execution: dict[str, Any],
    tp: dict[str, Any],
    sl: dict[str, Any],
    pending: dict[str, Any],
    trader_id: str,
) -> None:
    """Fail-fast validation for new canonical sections."""
    _TP_LEVELS = {"tp1", "tp2", "tp3", "tp4"}

    mode = str(market_execution.get("mode", "tolerance")).lower()
    if mode not in {"tolerance", "free"}:
        raise ValueError(
            f"[{trader_id}] market_execution.mode must be tolerance|free, got: {mode!r}"
        )

    be_trigger = sl.get("be_trigger")
    if be_trigger is not None:
        be_trigger_str = str(be_trigger).lower()
        if be_trigger_str not in _TP_LEVELS:
            raise ValueError(
                f"[{trader_id}] sl.be_trigger must be null|tp1..tp4, got: {be_trigger!r}"
            )

    for key in ("cancel_averaging_pending_after", "cancel_unfilled_pending_after"):
        val = pending.get(key)
        if val is not None:
            val_str = str(val).lower()
            if val_str not in _TP_LEVELS:
                raise ValueError(
                    f"[{trader_id}] pending.{key} must be null|tp1..tp4, got: {val!r}"
                )

    cd = tp.get("close_distribution", {})
    if isinstance(cd, dict):
        cd_mode = str(cd.get("mode", "equal")).lower()
        if cd_mode not in {"equal", "table"}:
            raise ValueError(
                f"[{trader_id}] tp.close_distribution.mode must be equal|table, "
                f"got: {cd_mode!r}"
            )


def _validate_entry_split_config(entry_split: dict[str, Any]) -> None:
    """Fail-fast validation for canonical entry_split shape.

    Canonical families supported:
    - LIMIT
    - MARKET

    Legacy top-level families (e.g. AVERAGING, ZONE) are rejected.
    """
    if not isinstance(entry_split, dict):
        raise ValueError("entry_split must be an object")

    for top_key in ("LIMIT", "MARKET"):
        if top_key in entry_split and not isinstance(entry_split[top_key], dict):
            raise ValueError(f"entry_split.{top_key} must be an object")

    for legacy_key in ("AVERAGING", "ZONE"):
        if legacy_key in entry_split:
            raise ValueError(
                f"entry_split.{legacy_key} is deprecated; use LIMIT/MARKET canonical families"
            )

    typo_aliases = {"avareging", "averging", "averageing", "avg"}
    for family in ("LIMIT", "MARKET"):
        family_cfg = entry_split.get(family, {})
        if not isinstance(family_cfg, dict):
            continue

        has_averaging = "averaging" in family_cfg
        typo_hits = sorted(k for k in family_cfg.keys() if str(k).lower() in typo_aliases)
        if typo_hits and has_averaging:
            raise ValueError(
                f"entry_split.{family} has overlapping averaging keys: "
                f"averaging + {typo_hits}"
            )
        if typo_hits:
            raise ValueError(
                f"entry_split.{family} uses invalid key(s) {typo_hits}; "
                "did you mean 'averaging'?"
            )

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
    registered_traders_raw = global_data.get("registered_traders", [])
    registered_traders = tuple(
        str(value).strip()
        for value in registered_traders_raw
        if isinstance(value, str) and str(value).strip()
    )
    is_registered = trader_id in registered_traders if registered_traders else True

    # Load trader-specific YAML (missing file → empty dict, uses all defaults)
    trader_path = root / "trader_rules" / f"{trader_id}.yaml"
    trader_data = _load_yaml(trader_path)

    # operation_rules mode:
    # - override (default): trader YAML overrides global defaults
    # - global: ignore trader-specific rule overrides, keep only control switches
    operation_rules_mode = str(
        trader_data.get("operation_rules", global_defaults.get("operation_rules", "override"))
    ).lower()
    if operation_rules_mode not in {"override", "global"}:
        operation_rules_mode = "override"

    if operation_rules_mode == "global":
        merged = copy.deepcopy(global_defaults)
        for control_key in ("enabled", "gate_mode", "operation_rules"):
            if control_key in trader_data:
                merged[control_key] = copy.deepcopy(trader_data[control_key])
    else:
        merged = _deep_merge(global_defaults, trader_data)

    # Hard caps — never overridable by traders
    hard_max = hard_caps_raw.get(
        "hard_max_per_signal_risk_pct",
        hard_caps_raw.get("max_per_signal_pct", 2.0),
    )
    market_execution = dict(hard_caps_raw.get("market_execution", {})) or {
        "mode": "tolerance",
        "tolerance_pct": 0.5,
        "range_tolerance_pct": 0.2,
    }
    hard_caps = HardCaps(
        max_capital_at_risk_pct=float(hard_caps_raw.get("max_capital_at_risk_pct", 10.0)),
        hard_max_per_signal_risk_pct=float(hard_max),
        market_execution=market_execution,
    )

    _validate_enum_fields(merged, trader_id)

    entry_split = merged.get("entry_split", {})
    for et in ("LIMIT", "MARKET"):
        if et not in entry_split:
            entry_split[et] = {}
    _validate_entry_split_config(entry_split)

    tp = dict(merged.get("tp", {}))
    sl = dict(merged.get("sl", {}))
    updates = dict(merged.get("updates", {}))
    pending = dict(merged.get("pending", {}))

    _validate_new_sections(market_execution, tp, sl, pending, trader_id)

    resolved_operation_rules = str(merged.get("operation_rules", "override")).lower()
    if resolved_operation_rules not in {"override", "global"}:
        resolved_operation_rules = "override"

    return EffectiveRules(
        hard_caps=hard_caps,
        registered_traders=registered_traders,
        is_registered=is_registered,
        enabled=bool(merged.get("enabled", True)),
        gate_mode=str(merged.get("gate_mode", "block")).lower(),
        operation_rules=resolved_operation_rules,
        use_trader_risk_hint=bool(merged.get("use_trader_risk_hint", False)),
        risk_mode=str(merged.get("risk_mode", "risk_pct_of_capital")).lower(),
        risk_pct_of_capital=float(merged.get("risk_pct_of_capital", 1.0)),
        risk_usdt_fixed=float(merged.get("risk_usdt_fixed", 10.0)),
        capital_base_mode=str(merged.get("capital_base_mode", "static_config")).lower(),
        capital_base_usdt=float(merged.get("capital_base_usdt", 1000.0)),
        leverage=int(merged.get("leverage", 1)),
        max_capital_at_risk_per_trader_pct=float(
            merged.get("max_capital_at_risk_per_trader_pct", 5.0)
        ),
        max_concurrent_same_symbol=int(merged.get("max_concurrent_same_symbol", 1)),
        entry_split=entry_split,
        tp=tp,
        sl=sl,
        updates=updates,
        pending=pending,
        price_corrections=merged.get("price_corrections", {"enabled": False, "method": None}),
        price_sanity=merged.get("price_sanity", {"enabled": False, "symbol_ranges": {}}),
        _raw=merged,
    )


def validate_operation_rules_config(*, rules_dir: str = "config") -> None:
    """Validate global + trader operation rules config at startup."""
    root = Path(rules_dir)
    global_path = root / "operation_rules.yaml"
    if not global_path.exists():
        raise FileNotFoundError(f"Global operation rules not found: {global_path}")

    load_effective_rules("__defaults__", rules_dir=rules_dir)

    trader_dir = root / "trader_rules"
    if not trader_dir.exists():
        return
    for path in sorted(trader_dir.glob("*.yaml")):
        load_effective_rules(path.stem, rules_dir=rules_dir)
