"""Operation Rules Engine — Layer 4.

Riceve un TraderParseResult e produce un OperationalSignal con:
  - gate check (trader abilitato? cap rispettati? stesso symbol?)
  - sizing (position_size_pct, leverage, entry_split)
  - snapshot management rules (Set B)

Entry point:
    result = await apply(parse_result, trader_id, db_path)

Logica gate (NEW_SIGNAL, in ordine):
  1. Trader abilitato?
  2. Stesso symbol già aperto?
  3. Calcola esposizione nuovo segnale
  4. Cap hard per singolo segnale (non overridabile)
  5. Cap per trader
  6. Cap globale (non overridabile)
  7. Price sanity statica (se abilitata)
  8. Calcola parametri (size, split, snapshot)

UPDATE: passthrough — solo snapshot Set B, nessun gate check.
INFO_ONLY / UNCLASSIFIED: passthrough senza parametri.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.operation_rules.loader import EntrySplitConfig, MergedRules, load_rules
from src.operation_rules.risk_calculator import (
    compute_exposure,
    sum_exposure,
    sum_exposure_global,
)
from src.parser.models.canonical import TraderParseResult
from src.parser.models.new_signal import NewSignalEntities
from src.parser.models.operational import OperationalSignal


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _blocked(
    parse_result: TraderParseResult,
    reason: str,
    applied_rules: list[str],
    warnings: list[str],
) -> OperationalSignal:
    return OperationalSignal(
        parse_result=parse_result,
        is_blocked=True,
        block_reason=reason,
        applied_rules=list(applied_rules),
        warnings=list(warnings),
    )


def _snapshot_management_rules(rules: MergedRules) -> dict[str, Any]:
    """Serialise position_management to a plain dict (snapshot at signal time)."""
    mgmt = rules.position_management
    return {
        "on_tp_hit": [r.model_dump(exclude_none=True) for r in mgmt.on_tp_hit],
        "auto_apply_intents": list(mgmt.auto_apply_intents),
        "log_only_intents": list(mgmt.log_only_intents),
    }


def _check_price_sanity(
    entities: NewSignalEntities,
    price_sanity: Any,
    warnings: list[str],
) -> bool:
    """Check static price ranges. Returns True if all entries are within range.

    Appends warnings for each out-of-range entry. Does not block — the engine
    decides whether to block based on gate_mode.
    """
    symbol = entities.symbol
    if symbol is None:
        return True

    ranges = price_sanity.symbol_ranges or {}
    if symbol not in ranges:
        return True

    sym_range = ranges[symbol]
    min_price = sym_range.get("min")
    max_price = sym_range.get("max")
    if min_price is None and max_price is None:
        return True

    all_ok = True
    priced = [e.price.value for e in entities.entries if e.price is not None]
    for price in priced:
        if (min_price is not None and price < min_price) or (
            max_price is not None and price > max_price
        ):
            warnings.append(
                f"price_out_of_static_range: {price} not in "
                f"[{min_price}, {max_price}] for {symbol}"
            )
            all_ok = False
    return all_ok


def compute_entry_split(
    entities: NewSignalEntities, rules: MergedRules
) -> dict[str, float]:
    """Compute entry split weights for a NEW_SIGNAL.

    Returns a dict {E1: weight, E2: weight, ...} where weights sum to 1.0.
    """
    entry_type = entities.entry_type
    split_cfg: EntrySplitConfig = rules.entry_split

    if entry_type == "MARKET":
        return dict(split_cfg.MARKET.weights)

    if entry_type == "LIMIT":
        return dict(split_cfg.LIMIT.weights)

    if entry_type == "ZONE":
        return _zone_split(entities, split_cfg)

    if entry_type == "AVERAGING":
        return _averaging_split(entities, split_cfg)

    # Fallback
    return {"E1": 1.0}


def _zone_split(entities: NewSignalEntities, split_cfg: EntrySplitConfig) -> dict[str, float]:
    """Split entries for a ZONE entry_type."""
    zone_cfg = split_cfg.ZONE
    mode = zone_cfg.split_mode

    priced = sorted(
        [e.price.value for e in entities.entries if e.price is not None]
    )

    if len(priced) < 2:
        return {"E1": 1.0}

    low = priced[0]
    high = priced[-1]

    if mode == "endpoints":
        # E1 = low, E2 = high with default 50/50
        w = zone_cfg.weights
        w1 = w.get("E1", 0.5)
        w2 = w.get("E2", 0.5)
        total = w1 + w2
        return {"E1": w1 / total, "E2": w2 / total}

    if mode == "midpoint":
        return {"E1": 1.0}

    if mode == "three_way":
        mid = (low + high) / 2
        _ = mid  # price is informational; weights drive the split
        w = zone_cfg.weights
        w1 = w.get("E1", 1 / 3)
        w2 = w.get("E2", 1 / 3)
        w3 = w.get("E3", 1 / 3)
        total = w1 + w2 + w3
        return {
            "E1": w1 / total,
            "E2": w2 / total,
            "E3": w3 / total,
        }

    # Unknown mode — equal split
    return {"E1": 0.5, "E2": 0.5}


def _averaging_split(
    entities: NewSignalEntities, split_cfg: EntrySplitConfig
) -> dict[str, float]:
    """Split entries for an AVERAGING entry_type."""
    avg_cfg = split_cfg.AVERAGING
    n = len([e for e in entities.entries if e.price is not None])
    if n == 0:
        return {"E1": 1.0}

    if avg_cfg.distribution == "equal" or not avg_cfg.weights:
        weight = 1.0 / n
        return {f"E{i + 1}": weight for i in range(n)}

    # decreasing: use configured weights, pad or trim to match n entries
    configured = avg_cfg.weights
    keys = [f"E{i + 1}" for i in range(n)]
    weights = {k: configured.get(k, 0.0) for k in keys}
    total = sum(weights.values())
    if total <= 0:
        # fallback to equal if weights sum to 0
        w = 1.0 / n
        return {k: w for k in keys}
    return {k: v / total for k, v in weights.items()}


async def _count_open_same_symbol(
    trader_id: str, symbol: str | None, db_path: Path | str
) -> int:
    """Count open signals for trader_id+symbol from the signals table."""
    if symbol is None:
        return 0

    import aiosqlite

    try:
        async with aiosqlite.connect(db_path) as db:
            async with db.execute(
                "SELECT COUNT(*) FROM signals "
                "WHERE trader_id = ? AND symbol = ? AND status != 'CLOSED'",
                (trader_id, symbol),
            ) as cursor:
                row = await cursor.fetchone()
                return int(row[0]) if row else 0
    except aiosqlite.OperationalError:
        return 0


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def apply(
    parse_result: TraderParseResult,
    trader_id: str,
    db_path: Path | str,
    *,
    config_dir: Path | str | None = None,
) -> OperationalSignal:
    """Apply operation rules to a TraderParseResult and produce an OperationalSignal.

    Args:
        parse_result: Validated TraderParseResult from the parser.
        trader_id: Trader identifier used to load trader-specific rules.
        db_path: Path to the SQLite DB (used for exposure sum queries).
        config_dir: Optional override for the config directory (useful in tests).

    Returns:
        OperationalSignal with is_blocked=True if any gate check fails,
        or with sizing + management_rules populated on success.
    """
    rules: MergedRules = load_rules(trader_id, config_dir=config_dir)
    applied_rules: list[str] = ["global_defaults"]
    warnings: list[str] = []

    # Check if a trader-specific file was loaded (heuristic: gate_mode can differ)
    # (applied_rules is informational only — no strict tracking needed here)

    # ── Gate step 1: trader abilitato? ──────────────────────────────────────
    if not rules.enabled:
        return _blocked(parse_result, "trader_disabled", applied_rules, warnings)

    # ── UPDATE passthrough ──────────────────────────────────────────────────
    if parse_result.message_type == "UPDATE":
        mgmt_rules = _snapshot_management_rules(rules)
        return OperationalSignal(
            parse_result=parse_result,
            management_rules=mgmt_rules,
            applied_rules=applied_rules,
            warnings=warnings,
        )

    # ── INFO_ONLY / UNCLASSIFIED passthrough ────────────────────────────────
    if parse_result.message_type not in ("NEW_SIGNAL",):
        return OperationalSignal(
            parse_result=parse_result,
            applied_rules=applied_rules,
            warnings=warnings,
        )

    # ── NEW_SIGNAL: gate checks + sizing ────────────────────────────────────
    entities: NewSignalEntities | None = parse_result.entities

    # Gate step 2: stesso symbol già aperto?
    symbol = entities.symbol if entities else None
    open_same = await _count_open_same_symbol(trader_id, symbol, db_path)
    if open_same >= rules.max_concurrent_same_symbol:
        return _blocked(parse_result, "max_concurrent_same_symbol", applied_rules, warnings)

    # Gate step 3: calcola esposizione nuovo segnale
    new_exp = compute_exposure(parse_result, rules)

    # Gate step 4: cap hard per singolo segnale (non overridabile)
    if new_exp > rules.hard_caps.max_per_signal_pct:
        return _blocked(parse_result, "per_signal_cap_exceeded", applied_rules, warnings)

    # Gate step 5: cap per trader
    trader_exp = await sum_exposure(trader_id, db_path)
    if trader_exp + new_exp > rules.max_capital_at_risk_per_trader_pct:
        return _blocked(
            parse_result, "trader_capital_at_risk_exceeded", applied_rules, warnings
        )

    # Gate step 6: cap globale (non overridabile)
    global_exp = await sum_exposure_global(db_path)
    if global_exp + new_exp > rules.hard_caps.max_capital_at_risk_pct:
        return _blocked(
            parse_result, "global_capital_at_risk_exceeded", applied_rules, warnings
        )

    # Gate step 7: price sanity statica (se abilitata)
    if rules.price_sanity.enabled and entities is not None:
        sanity_ok = _check_price_sanity(entities, rules.price_sanity, warnings)
        if not sanity_ok:
            warnings.append("price_sanity_static_check_failed")
            # Non blocca qui — la decisione spetta al chiamante in base a gate_mode
            # (Level 3 del price sanity — Sistema 1 farà il check live)

    # Gate step 8: calcola parametri
    size_pct = rules.position_size_pct
    leverage = rules.leverage

    split: dict[str, float] | None = None
    if entities is not None:
        split = compute_entry_split(entities, rules)

    mgmt_rules = _snapshot_management_rules(rules)

    # risk_hint_used
    risk_hint_used = False
    if rules.use_trader_risk_hint and entities is not None:
        risk_hint_raw = getattr(entities, "risk_pct", None)
        if risk_hint_raw is not None:
            size_pct = float(risk_hint_raw)
            risk_hint_used = True

    return OperationalSignal(
        parse_result=parse_result,
        position_size_pct=size_pct,
        entry_split=split,
        leverage=leverage,
        risk_hint_used=risk_hint_used,
        management_rules=mgmt_rules,
        applied_rules=applied_rules,
        warnings=warnings,
    )
