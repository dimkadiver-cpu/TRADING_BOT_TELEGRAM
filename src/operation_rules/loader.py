"""Operation rules loader — carica e merge config YAML a 4 livelli.

Ordine di precedenza (più alta = vince):
  1. global_hard_caps     — non overridabili da nessun trader
  2. trader_on_off        — il flag `enabled` dal file trader (check rapido)
  3. trader_specific      — tutti gli altri override dal file trader
  4. global_defaults      — base

Usage:
    from src.operation_rules.loader import load_rules, MergedRules

    rules = load_rules("trader_3")
    print(rules.leverage)
    print(rules.hard_caps.max_per_signal_pct)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Pydantic models — config typed
# ---------------------------------------------------------------------------

class HardCaps(BaseModel):
    """Global hard caps — non overridabili."""
    max_capital_at_risk_pct: float = 10.0
    max_per_signal_pct: float = 2.0


class ZoneSplitConfig(BaseModel):
    split_mode: Literal["endpoints", "midpoint", "three_way"] = "endpoints"
    weights: dict[str, float] = Field(default_factory=lambda: {"E1": 0.50, "E2": 0.50})


class AveragingSplitConfig(BaseModel):
    distribution: Literal["equal", "decreasing"] = "equal"
    weights: dict[str, float] = Field(default_factory=dict)
    """Pesi opzionali per distribution=decreasing (es. {E1: 0.4, E2: 0.3, E3: 0.2, E4: 0.1})."""


class LimitSplitConfig(BaseModel):
    weights: dict[str, float] = Field(default_factory=lambda: {"E1": 1.0})


class MarketSplitConfig(BaseModel):
    weights: dict[str, float] = Field(default_factory=lambda: {"E1": 1.0})


class EntrySplitConfig(BaseModel):
    ZONE: ZoneSplitConfig = Field(default_factory=ZoneSplitConfig)
    AVERAGING: AveragingSplitConfig = Field(default_factory=AveragingSplitConfig)
    LIMIT: LimitSplitConfig = Field(default_factory=LimitSplitConfig)
    MARKET: MarketSplitConfig = Field(default_factory=MarketSplitConfig)


class PriceCorrectionsConfig(BaseModel):
    enabled: bool = False
    method: str | None = None


class PriceSanityConfig(BaseModel):
    enabled: bool = False
    symbol_ranges: dict[str, dict[str, float]] = Field(default_factory=dict)
    """Mappa symbol → {min: float, max: float}."""


class TpHitRule(BaseModel):
    tp_level: int
    action: str
    close_pct: float | None = None


class PositionManagementConfig(BaseModel):
    on_tp_hit: list[TpHitRule] = Field(default_factory=list)
    auto_apply_intents: list[str] = Field(default_factory=list)
    log_only_intents: list[str] = Field(default_factory=list)


class MergedRules(BaseModel):
    """Regole operative per un trader specifico dopo il merge a 4 livelli.

    hard_caps è sempre preso da global e non può essere overridato.
    """

    trader_id: str
    hard_caps: HardCaps

    # Trader on/off + gate
    enabled: bool
    gate_mode: Literal["block", "warn"]

    # Set A — apertura posizione
    use_trader_risk_hint: bool
    position_size_pct: float
    leverage: int
    max_capital_at_risk_per_trader_pct: float
    max_concurrent_same_symbol: int

    # Splits
    entry_split: EntrySplitConfig

    # Hooks futuri
    price_corrections: PriceCorrectionsConfig

    # Sanity check prezzi statici
    price_sanity: PriceSanityConfig

    # Set B — snapshot gestione posizione
    position_management: PositionManagementConfig


# ---------------------------------------------------------------------------
# Default config dir
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG_DIR = Path(__file__).parent.parent.parent / "config"


# ---------------------------------------------------------------------------
# Deep merge helper
# ---------------------------------------------------------------------------

def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Merge override onto base recursively. override wins on conflicts."""
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def load_rules(
    trader_id: str,
    *,
    config_dir: Path | str | None = None,
) -> MergedRules:
    """Load and merge operation rules for *trader_id*.

    Merge order (highest priority first):
      1. global_hard_caps  — always wins, non-overridable
      2. trader_on_off     — enabled flag from trader file (skip if no trader file)
      3. trader_specific   — all other fields from trader file
      4. global_defaults   — base values from operation_rules.yaml

    Args:
        trader_id: Identifier of the trader (e.g. "trader_3").
        config_dir: Directory containing operation_rules.yaml and trader_rules/.
            Defaults to <project_root>/config.

    Returns:
        MergedRules with all fields resolved.

    Raises:
        FileNotFoundError: If operation_rules.yaml is not found.
        ValueError: If the YAML structure is invalid.
    """
    cfg_dir = Path(config_dir) if config_dir else _DEFAULT_CONFIG_DIR

    global_path = cfg_dir / "operation_rules.yaml"
    if not global_path.exists():
        raise FileNotFoundError(f"operation_rules.yaml not found at {global_path}")

    with global_path.open(encoding="utf-8") as fh:
        global_raw: dict[str, Any] = yaml.safe_load(fh) or {}

    hard_caps_raw: dict[str, Any] = global_raw.get("global_hard_caps", {})
    defaults_raw: dict[str, Any] = global_raw.get("global_defaults", {})

    # Level 4 (lowest): global_defaults
    merged: dict[str, Any] = dict(defaults_raw)

    # Level 3 + 2: trader_specific + trader_on_off (same file)
    trader_path = cfg_dir / "trader_rules" / f"{trader_id}.yaml"
    if trader_path.exists():
        with trader_path.open(encoding="utf-8") as fh:
            trader_raw: dict[str, Any] = yaml.safe_load(fh) or {}
        merged = _deep_merge(merged, trader_raw)

    # Level 1 (highest): hard_caps — always from global, never overridable
    hard_caps = HardCaps(**hard_caps_raw)

    # Build typed result
    entry_split_raw = merged.get("entry_split", {})
    entry_split = _build_entry_split(entry_split_raw)

    price_corr_raw = merged.get("price_corrections", {})
    price_sanity_raw = merged.get("price_sanity", {})
    pos_mgmt_raw = merged.get("position_management", {})

    return MergedRules(
        trader_id=trader_id,
        hard_caps=hard_caps,
        enabled=merged.get("enabled", True),
        gate_mode=merged.get("gate_mode", "block"),
        use_trader_risk_hint=merged.get("use_trader_risk_hint", False),
        position_size_pct=float(merged.get("position_size_pct", 1.0)),
        leverage=int(merged.get("leverage", 1)),
        max_capital_at_risk_per_trader_pct=float(
            merged.get("max_capital_at_risk_per_trader_pct", 5.0)
        ),
        max_concurrent_same_symbol=int(merged.get("max_concurrent_same_symbol", 1)),
        entry_split=entry_split,
        price_corrections=PriceCorrectionsConfig(**price_corr_raw)
        if price_corr_raw
        else PriceCorrectionsConfig(),
        price_sanity=PriceSanityConfig(**price_sanity_raw)
        if price_sanity_raw
        else PriceSanityConfig(),
        position_management=_build_position_management(pos_mgmt_raw),
    )


def _build_entry_split(raw: dict[str, Any]) -> EntrySplitConfig:
    zone_raw = raw.get("ZONE", {})
    avg_raw = raw.get("AVERAGING", {})
    limit_raw = raw.get("LIMIT", {})
    market_raw = raw.get("MARKET", {})
    return EntrySplitConfig(
        ZONE=ZoneSplitConfig(**zone_raw) if zone_raw else ZoneSplitConfig(),
        AVERAGING=AveragingSplitConfig(**avg_raw) if avg_raw else AveragingSplitConfig(),
        LIMIT=LimitSplitConfig(**limit_raw) if limit_raw else LimitSplitConfig(),
        MARKET=MarketSplitConfig(**market_raw) if market_raw else MarketSplitConfig(),
    )


def _build_position_management(raw: dict[str, Any]) -> PositionManagementConfig:
    on_tp_hit_raw = raw.get("on_tp_hit", [])
    on_tp_hit = [TpHitRule(**r) for r in on_tp_hit_raw]
    return PositionManagementConfig(
        on_tp_hit=on_tp_hit,
        auto_apply_intents=raw.get("auto_apply_intents", []),
        log_only_intents=raw.get("log_only_intents", []),
    )
