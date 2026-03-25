"""Layer 4 — Operation Rules Engine.

Receives a validated TraderParseResult and produces an OperationalSignal
with gate check result, sizing parameters, and management rules snapshot.

Modello risk-first: si specifica il rischio massimo accettato (% o USDT fissi),
il sistema calcola la size della posizione in base a entry, stop loss e leva.

Public API:
    engine = OperationRulesEngine(rules_dir="config")
    op_signal = engine.apply(parse_result, trader_id, db_path=db_path)
"""

from __future__ import annotations

import re
from typing import Any

from src.operation_rules.loader import EffectiveRules, load_effective_rules
from src.operation_rules.risk_calculator import (
    compute_position_size_from_risk,
    compute_risk_budget_usdt,
    compute_risk_pct,
    count_open_same_symbol,
    sum_global_exposure,
    sum_trader_exposure,
)
from src.parser.models.operational import OperationalSignal
from src.parser.trader_profiles.base import TraderParseResult


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_NUMBER_RE = re.compile(r"[\d]+(?:[.,][\d]+)?")


def _extract_trader_id(parse_result: TraderParseResult) -> str:
    """Best-effort extraction of trader_id from a parse_result."""
    linking = getattr(parse_result, "linking", {}) or {}
    tid = linking.get("trader_id", "")
    if not tid and isinstance(parse_result.entities, dict):
        tid = parse_result.entities.get("resolved_trader_id", "") or ""
    return str(tid)


def _parse_first_float(s: str | None) -> float | None:
    """Extract the first number from a raw string. Returns None on failure."""
    if not s:
        return None
    cleaned = s.replace(" ", "").replace("\xa0", "")
    match = _NUMBER_RE.search(cleaned)
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", "."))
    except ValueError:
        return None


def _parse_all_floats(s: str | None) -> list[float]:
    """Extract all numbers from a raw string."""
    if not s:
        return []
    cleaned = s.replace(" ", "").replace("\xa0", "")
    results: list[float] = []
    for match in _NUMBER_RE.finditer(cleaned):
        try:
            results.append(float(match.group(0).replace(",", ".")))
        except ValueError:
            pass
    return results


def _extract_entry_prices(entities: dict[str, Any]) -> list[float]:
    """Extract entry price(s) from raw entities dict."""
    # Prefer structured entries list if present
    entries_raw = entities.get("entries")
    if isinstance(entries_raw, list) and entries_raw:
        prices: list[float] = []
        for e in entries_raw:
            if isinstance(e, dict):
                p = e.get("price")
                if p is not None:
                    try:
                        prices.append(float(p))
                    except (TypeError, ValueError):
                        pass
            elif isinstance(e, (int, float)):
                prices.append(float(e))
        if prices:
            return prices

    # Fall back to entry_raw string
    entry_raw = entities.get("entry_raw") or entities.get("entry")
    parsed = _parse_all_floats(str(entry_raw) if entry_raw is not None else None)
    return parsed if parsed else []


def _extract_sl_price(entities: dict[str, Any]) -> float | None:
    """Extract stop-loss price from entities dict."""
    # Structured sl object
    sl_obj = entities.get("stop_loss") or entities.get("sl")
    if isinstance(sl_obj, dict):
        p = sl_obj.get("price") or sl_obj.get("value")
        if p is not None:
            try:
                return float(p)
            except (TypeError, ValueError):
                pass

    # Raw stop string
    stop_raw = entities.get("stop_raw") or entities.get("stop")
    if stop_raw is not None:
        return _parse_first_float(str(stop_raw))
    return None


def _extract_symbol(entities: dict[str, Any]) -> str:
    return str(entities.get("symbol") or "").strip().upper()


def _compute_entry_split(
    entry_prices: list[float],
    entities: dict[str, Any],
    rules: EffectiveRules,
) -> dict[str, float]:
    """Compute entry split weights based on entry count and config."""
    n = len(entry_prices)

    if n == 0:
        # MARKET
        cfg = rules.entry_split.get("MARKET", {})
        return dict(cfg.get("weights", {"E1": 1.0}))

    if n == 1:
        # LIMIT
        cfg = rules.entry_split.get("LIMIT", {})
        return dict(cfg.get("weights", {"E1": 1.0}))

    # Check for ZONE type hint in entities
    entry_type = str(entities.get("entry_type", "")).upper()
    if "ZONE" in entry_type or "ZONE" in str(entities.get("entry_mode", "")).upper():
        cfg = rules.entry_split.get("ZONE", {})
        split_mode = cfg.get("split_mode", "endpoints")
        if split_mode == "midpoint":
            return {"E1": 1.0}
        if split_mode == "three_way":
            weights = cfg.get("weights", {"E1": 0.33, "E2": 0.34, "E3": 0.33})
            return {k: float(v) for k, v in weights.items()}
        # endpoints (default)
        weights = cfg.get("weights", {"E1": 0.50, "E2": 0.50})
        return {k: float(v) for k, v in weights.items()}

    # AVERAGING (n >= 2, not ZONE)
    cfg = rules.entry_split.get("AVERAGING", {})
    distribution = cfg.get("distribution", "equal")
    if distribution == "decreasing" and "weights" in cfg:
        raw = cfg["weights"]
        available = list(raw.items())[:n]
        total = sum(float(v) for _, v in available)
        if total > 0:
            return {k: float(v) / total for k, v in available}

    # Equal distribution
    weight = round(1.0 / n, 6)
    return {f"E{i + 1}": weight for i in range(n)}


def _snapshot_management_rules(rules: EffectiveRules) -> dict[str, Any]:
    """Snapshot the management rules (Set B + tp_handling) at signal creation time."""
    import copy
    snapshot = copy.deepcopy(rules.position_management)
    snapshot["tp_handling"] = copy.deepcopy(rules.tp_handling)
    return snapshot


def _make_blocked(
    parse_result: TraderParseResult,
    reason: str,
    rules: EffectiveRules,
    applied: list[str],
    trader_id: str = "",
) -> OperationalSignal:
    return OperationalSignal(
        parse_result=parse_result,
        trader_id=trader_id,
        is_blocked=True,
        block_reason=reason,
        management_rules=_snapshot_management_rules(rules),
        applied_rules=applied + [f"BLOCKED:{reason}"],
    )


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class OperationRulesEngine:
    """Apply operation rules to a validated TraderParseResult.

    Args:
        rules_dir: Directory containing operation_rules.yaml and trader_rules/.
                   Defaults to "config" (relative to cwd at call time).
    """

    def __init__(self, rules_dir: str = "config") -> None:
        self._rules_dir = rules_dir

    def apply(
        self,
        parse_result: TraderParseResult,
        trader_id: str,
        *,
        db_path: str,
    ) -> OperationalSignal:
        """Apply gate checks and compute parameters for *parse_result*.

        Args:
            parse_result: Validated parser output (validation_status == VALID).
            trader_id:    Trader identifier used to load trader-specific rules.
            db_path:      SQLite DB path used for open-signal queries.

        Returns:
            OperationalSignal with is_blocked=True if any gate failed, or with
            computed parameters if all gates passed.
        """
        rules = load_effective_rules(trader_id, rules_dir=self._rules_dir)
        applied: list[str] = ["load_rules"]
        warnings: list[str] = []

        # ── Gate 1: Trader enabled? ────────────────────────────────────────
        if not rules.enabled:
            return _make_blocked(parse_result, "trader_disabled", rules, applied, trader_id)
        applied.append("gate_enabled_ok")

        message_type = parse_result.message_type
        entities: dict[str, Any] = parse_result.entities if isinstance(
            parse_result.entities, dict
        ) else {}

        # ── UPDATE: no gate on capital, just snapshot ──────────────────────
        if message_type == "UPDATE":
            applied.append("update_passthrough")
            mgmt = _snapshot_management_rules(rules)
            return OperationalSignal(
                parse_result=parse_result,
                trader_id=trader_id,
                management_rules=mgmt,
                applied_rules=applied,
                warnings=warnings,
            )

        # ── NEW_SIGNAL: full gate checks ───────────────────────────────────
        if message_type != "NEW_SIGNAL":
            # INFO_ONLY, UNCLASSIFIED, etc. — passthrough, no gate
            applied.append("passthrough_non_actionable")
            return OperationalSignal(
                parse_result=parse_result,
                trader_id=trader_id,
                applied_rules=applied,
                warnings=warnings,
            )

        symbol = _extract_symbol(entities)
        entry_prices = _extract_entry_prices(entities)
        sl_price = _extract_sl_price(entities)

        # ── Gate 2: entry prezzi presenti? (hard block — senza entry non si calcola) ──
        if not entry_prices:
            return _make_blocked(parse_result, "missing_entry", rules, applied, trader_id)
        applied.append("gate_entry_ok")

        # ── Gate 3: stop loss presente? (hard block — senza SL non si calcola il rischio) ──
        if sl_price is None or sl_price <= 0:
            return _make_blocked(parse_result, "missing_stop_loss", rules, applied, trader_id)
        applied.append("gate_sl_ok")

        # ── Gate 4: leva valida? (hard block) ─────────────────────────────
        if rules.leverage <= 0:
            return _make_blocked(parse_result, "invalid_leverage", rules, applied, trader_id)
        applied.append("gate_leverage_ok")

        # ── Gate 5: max concurrent same symbol ────────────────────────────
        if symbol:
            open_count = count_open_same_symbol(trader_id, symbol, db_path)
            applied.append(f"gate_same_symbol:open={open_count}")
            if open_count >= rules.max_concurrent_same_symbol:
                if rules.gate_mode == "warn":
                    warnings.append(
                        f"max_concurrent_same_symbol:open={open_count}"
                        f",max={rules.max_concurrent_same_symbol}"
                    )
                else:
                    return _make_blocked(
                        parse_result, "max_concurrent_same_symbol", rules, applied, trader_id
                    )

        # ── Calcola budget rischio e size ──────────────────────────────────
        risk_budget_usdt = compute_risk_budget_usdt(
            rules.risk_mode,
            rules.risk_pct_of_capital,
            rules.risk_usdt_fixed,
            rules.capital_base_usdt,
        )

        try:
            position_size_usdt, position_size_pct, sl_distance_pct = compute_position_size_from_risk(
                entry_prices, sl_price, risk_budget_usdt, rules.leverage, rules.capital_base_usdt
            )
        except ValueError:
            # sl_distance == 0: entry == SL (degenerate signal)
            return _make_blocked(parse_result, "zero_sl_distance", rules, applied, trader_id)

        new_risk_pct = compute_risk_pct(
            rules.risk_mode,
            rules.risk_pct_of_capital,
            rules.risk_usdt_fixed,
            rules.capital_base_usdt,
        )
        applied.append(f"gate_risk:new_risk_pct={new_risk_pct:.4f}")

        # ── Gate 6: hard cap per signal ────────────────────────────────────
        if new_risk_pct > rules.hard_caps.hard_max_per_signal_risk_pct:
            applied.append("gate_per_signal_cap")
            if rules.gate_mode == "warn":
                warnings.append(
                    f"per_signal_cap_exceeded:risk={new_risk_pct:.4f}"
                    f",cap={rules.hard_caps.hard_max_per_signal_risk_pct}"
                )
            else:
                return _make_blocked(parse_result, "per_signal_cap_exceeded", rules, applied, trader_id)

        # ── Gate 7: trader capital cap ─────────────────────────────────────
        trader_exp = sum_trader_exposure(trader_id, db_path)
        applied.append(f"gate_trader_cap:trader_exp={trader_exp:.4f}")
        if trader_exp + new_risk_pct > rules.max_capital_at_risk_per_trader_pct:
            if rules.gate_mode == "warn":
                warnings.append(
                    f"trader_capital_at_risk_exceeded"
                    f":total={trader_exp + new_risk_pct:.4f}"
                    f",cap={rules.max_capital_at_risk_per_trader_pct}"
                )
            else:
                return _make_blocked(
                    parse_result, "trader_capital_at_risk_exceeded", rules, applied, trader_id
                )

        # ── Gate 8: global capital hard cap ───────────────────────────────
        global_exp = sum_global_exposure(db_path)
        applied.append(f"gate_global_cap:global_exp={global_exp:.4f}")
        if global_exp + new_risk_pct > rules.hard_caps.max_capital_at_risk_pct:
            if rules.gate_mode == "warn":
                warnings.append(
                    f"global_capital_at_risk_exceeded"
                    f":total={global_exp + new_risk_pct:.4f}"
                    f",cap={rules.hard_caps.max_capital_at_risk_pct}"
                )
            else:
                return _make_blocked(
                    parse_result, "global_capital_at_risk_exceeded", rules, applied, trader_id
                )

        # ── Gate 9: static price sanity (optional) ────────────────────────
        if rules.price_sanity.get("enabled") and entry_prices and symbol:
            ranges = rules.price_sanity.get("symbol_ranges", {})
            if symbol in ranges:
                sym_range = ranges[symbol]
                lo = sym_range.get("min", 0)
                hi = sym_range.get("max", float("inf"))
                for ep in entry_prices:
                    if not (lo <= ep <= hi):
                        applied.append(f"gate_price_sanity:failed:{symbol}={ep}")
                        if rules.gate_mode == "warn":
                            warnings.append(f"price_out_of_static_range:{symbol}={ep}")
                        else:
                            return _make_blocked(
                                parse_result, "price_out_of_static_range", rules, applied, trader_id
                            )

        applied.append("all_gates_passed")

        # ── Compute parameters ─────────────────────────────────────────────
        entry_split = _compute_entry_split(entry_prices, entities, rules)
        mgmt = _snapshot_management_rules(rules)

        # risk_hint override (if enabled and available) — overrides risk_pct_of_capital
        risk_hint_used = False
        effective_risk_pct = rules.risk_pct_of_capital
        if rules.use_trader_risk_hint:
            risk_hint = entities.get("risk_hint") or parse_result.entities.get("risk_hint")  # type: ignore[union-attr]
            if risk_hint is not None:
                try:
                    effective_risk_pct = float(risk_hint)
                    risk_hint_used = True
                    applied.append(f"risk_hint_applied:{effective_risk_pct}")
                    # Recompute budget and size with hint value
                    risk_budget_usdt = compute_risk_budget_usdt(
                        rules.risk_mode, effective_risk_pct,
                        rules.risk_usdt_fixed, rules.capital_base_usdt,
                    )
                    position_size_usdt, position_size_pct, sl_distance_pct = (
                        compute_position_size_from_risk(
                            entry_prices, sl_price, risk_budget_usdt,
                            rules.leverage, rules.capital_base_usdt,
                        )
                    )
                except (TypeError, ValueError) as exc:
                    if "zero" in str(exc).lower():
                        return _make_blocked(parse_result, "zero_sl_distance", rules, applied, trader_id)
                    warnings.append(f"risk_hint_parse_failed:{risk_hint}")

        return OperationalSignal(
            parse_result=parse_result,
            trader_id=trader_id,
            risk_mode=rules.risk_mode,
            risk_pct_of_capital=effective_risk_pct,
            risk_usdt_fixed=rules.risk_usdt_fixed if rules.risk_mode == "risk_usdt_fixed" else None,
            capital_base_usdt=rules.capital_base_usdt,
            risk_budget_usdt=risk_budget_usdt,
            sl_distance_pct=sl_distance_pct,
            position_size_usdt=position_size_usdt,
            position_size_pct=position_size_pct,
            entry_split=entry_split,
            leverage=rules.leverage,
            risk_hint_used=risk_hint_used,
            management_rules=mgmt,
            is_blocked=False,
            block_reason=None,
            applied_rules=applied,
            warnings=warnings,
        )
