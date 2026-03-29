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
import warnings
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

    # TP handling
    tp_handling: dict[str, Any]

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


def _normalize_position_management(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize legacy/new position-management config to the v2 shape."""
    # Legacy v1 shape: {auto_apply_intents, log_only_intents}
    has_legacy_intents = "auto_apply_intents" in raw or "log_only_intents" in raw
    has_v2_sections = "trader_hint" in raw or "machine_event" in raw or "mode" in raw

    passthrough = {
        k: copy.deepcopy(v)
        for k, v in raw.items()
        if k not in {"mode", "trader_hint", "machine_event", "auto_apply_intents", "log_only_intents"}
    }

    if has_legacy_intents and not has_v2_sections:
        return {
            "mode": "trader_hint",
            "trader_hint": {
                "auto_apply_intents": list(raw.get("auto_apply_intents", [])),
                "log_only_intents": list(raw.get("log_only_intents", [])),
            },
            "machine_event": {"rules": []},
            **passthrough,
        }

    mode = str(raw.get("mode", "hybrid")).lower()
    if mode not in {"machine_event", "trader_hint", "hybrid"}:
        mode = "hybrid"

    trader_hint = raw.get("trader_hint", {})
    if not isinstance(trader_hint, dict):
        trader_hint = {}
    machine_event = raw.get("machine_event", {})
    if not isinstance(machine_event, dict):
        machine_event = {}

    return {
        "mode": mode,
        "trader_hint": {
            "auto_apply_intents": list(trader_hint.get("auto_apply_intents", [])),
            "log_only_intents": list(trader_hint.get("log_only_intents", [])),
        },
        "machine_event": {
            "rules": list(machine_event.get("rules", [])),
        },
        **passthrough,
    }


def _validate_position_management_config(position_management: dict[str, Any]) -> None:
    """Fail-fast validation for overlapping/conflicting position rules."""
    mode = str(position_management.get("mode", "hybrid")).lower()
    if mode not in {"machine_event", "trader_hint", "hybrid"}:
        raise ValueError(f"Invalid position_management.mode: {mode}")

    trader_hint = position_management.get("trader_hint", {})
    if not isinstance(trader_hint, dict):
        raise ValueError("position_management.trader_hint must be an object")

    auto_apply = set(str(x) for x in trader_hint.get("auto_apply_intents", []))
    log_only = set(str(x) for x in trader_hint.get("log_only_intents", []))
    overlap = sorted(auto_apply.intersection(log_only))
    if overlap:
        raise ValueError(
            "position_management trader_hint overlap between auto_apply_intents "
            f"and log_only_intents: {overlap}"
        )

    machine_event = position_management.get("machine_event", {})
    if not isinstance(machine_event, dict):
        raise ValueError("position_management.machine_event must be an object")
    rules = machine_event.get("rules", [])
    if not isinstance(rules, list):
        raise ValueError("position_management.machine_event.rules must be a list")

    # Exclusion check: no duplicated event selectors (no overlap on same trigger).
    seen_selectors: set[tuple[str, str | None]] = set()
    for idx, rule in enumerate(rules):
        if not isinstance(rule, dict):
            raise ValueError(f"position_management.machine_event.rules[{idx}] must be an object")
        event_type = str(rule.get("event_type", "")).upper().strip()
        if not event_type:
            raise ValueError(f"position_management.machine_event.rules[{idx}].event_type is required")
        when = rule.get("when", {})
        if when is None:
            when = {}
        if not isinstance(when, dict):
            raise ValueError(f"position_management.machine_event.rules[{idx}].when must be an object")
        tp_selector = when.get("tp_level") or when.get("tp_level_gte")
        selector = (event_type, str(tp_selector) if tp_selector is not None else None)
        if selector in seen_selectors:
            raise ValueError(
                "position_management.machine_event overlapping selector detected: "
                f"{selector}"
            )
        seen_selectors.add(selector)


def _validate_enum_fields(merged: dict[str, Any], trader_id: str) -> None:
    """Fail-fast validation for enumerated string fields.

    Raises ValueError immediately if a field contains a value outside its
    allowed set, so misconfigurations are caught at load time rather than
    producing silent wrong behaviour at runtime.
    """
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


def _validate_entry_split_config(entry_split: dict[str, Any]) -> None:
    """Fail-fast validation for entry_split shape and ambiguous averaging keys."""
    if not isinstance(entry_split, dict):
        raise ValueError("entry_split must be an object")

    for top_key in ("ZONE", "AVERAGING", "LIMIT", "MARKET"):
        if top_key in entry_split and not isinstance(entry_split[top_key], dict):
            raise ValueError(f"entry_split.{top_key} must be an object")

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

    averaging_cfg = entry_split.get("AVERAGING", {})
    if isinstance(averaging_cfg, dict):
        distribution = str(averaging_cfg.get("distribution", "equal")).lower()
        if distribution not in {"equal", "decreasing"}:
            raise ValueError(
                "entry_split.AVERAGING.distribution must be one of: "
                "equal | decreasing"
            )
        if distribution == "decreasing":
            weights = averaging_cfg.get("weights", {})
            if not isinstance(weights, dict) or not weights:
                raise ValueError(
                    "entry_split.AVERAGING.weights must be a non-empty object when "
                    "distribution=decreasing"
                )
            total = 0.0
            for key, value in weights.items():
                if not str(key).upper().startswith("E"):
                    raise ValueError(
                        "entry_split.AVERAGING.weights keys must follow Ex format "
                        "(e.g. E1, E2)"
                    )
                try:
                    w = float(value)
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        f"entry_split.AVERAGING.weights[{key}] must be numeric"
                    ) from exc
                if w < 0:
                    raise ValueError(
                        f"entry_split.AVERAGING.weights[{key}] must be >= 0"
                    )
                total += w
            if total <= 0:
                raise ValueError(
                    "entry_split.AVERAGING.weights must have sum > 0 "
                    "when distribution=decreasing"
                )
        # Soft deprecation: legacy block still accepted for backward compatibility.
        if distribution != "equal" or "weights" in averaging_cfg:
            warnings.warn(
                "entry_split.AVERAGING is deprecated; use LIMIT.averaging and/or "
                "MARKET.averaging instead.",
                DeprecationWarning,
                stacklevel=2,
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
        # Merge: start from global_defaults, apply trader overrides on top
        merged = _deep_merge(global_defaults, trader_data)

    # Hard caps are always final (never overridable)
    # Support both old key (max_per_signal_pct) and new key for backward compat
    hard_max = hard_caps_raw.get(
        "hard_max_per_signal_risk_pct",
        hard_caps_raw.get("max_per_signal_pct", 2.0),
    )
    hard_caps = HardCaps(
        max_capital_at_risk_pct=float(hard_caps_raw.get("max_capital_at_risk_pct", 10.0)),
        hard_max_per_signal_risk_pct=float(hard_max),
    )

    _validate_enum_fields(merged, trader_id)

    entry_split = merged.get("entry_split", {})
    # Normalise entry_split: ensure each entry type exists
    for et in ("ZONE", "AVERAGING", "LIMIT", "MARKET"):
        if et not in entry_split:
            entry_split[et] = {}
    _validate_entry_split_config(entry_split)

    resolved_operation_rules = str(merged.get("operation_rules", "override")).lower()
    if resolved_operation_rules not in {"override", "global"}:
        resolved_operation_rules = "override"

    position_management = _normalize_position_management(
        merged.get("position_management", {})
        if isinstance(merged.get("position_management", {}), dict)
        else {}
    )
    _validate_position_management_config(position_management)

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
        tp_handling=merged.get("tp_handling", {
            "tp_handling_mode": "follow_all_signal_tps",
            "max_tp_levels": 5,
            "tp_close_distribution": {2: [50, 50], 3: [30, 30, 40], 5: [20, 20, 20, 20, 20]},
        }),
        price_corrections=merged.get("price_corrections", {"enabled": False, "method": None}),
        price_sanity=merged.get("price_sanity", {"enabled": False, "symbol_ranges": {}}),
        position_management=position_management,
        _raw=merged,
    )


def validate_operation_rules_config(*, rules_dir: str = "config") -> None:
    """Validate global + trader operation rules config at startup."""
    root = Path(rules_dir)
    global_path = root / "operation_rules.yaml"
    if not global_path.exists():
        raise FileNotFoundError(f"Global operation rules not found: {global_path}")

    # Validate defaults path
    load_effective_rules("__defaults__", rules_dir=rules_dir)

    trader_dir = root / "trader_rules"
    if not trader_dir.exists():
        return
    for path in sorted(trader_dir.glob("*.yaml")):
        load_effective_rules(path.stem, rules_dir=rules_dir)
