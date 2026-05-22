from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from src.runtime_v2.lifecycle.models import TradeChain
from src.runtime_v2.lifecycle.ports import AccountStateSnapshot, SymbolMarketSnapshot
from src.runtime_v2.signal_enrichment.models import (
    EffectiveEnrichmentConfig,
    EnrichedCanonicalMessage,
)

logger = logging.getLogger(__name__)


@dataclass
class RiskDecision:
    passed: bool
    reason: str | None
    size_usdt: float | None = None
    leverage: int | None = None
    risk_snapshot: dict = field(default_factory=dict)


class RiskCapacityEngine:
    def validate(
        self,
        enriched: EnrichedCanonicalMessage,
        open_chains: list[TradeChain],
        account_snapshot: AccountStateSnapshot | None,
        market_snapshot: SymbolMarketSnapshot | None,
    ) -> RiskDecision:
        signal = enriched.enriched_signal
        if signal is None:
            return RiskDecision(passed=False, reason="no_signal_payload")

        try:
            config = EffectiveEnrichmentConfig.model_validate(enriched.policy_snapshot)
        except Exception as exc:
            logger.warning("invalid policy_snapshot: %s", exc)
            return RiskDecision(passed=False, reason="invalid_policy_snapshot")

        risk = config.risk
        symbol = signal.symbol or ""
        side = signal.side or ""

        trader_chains = [c for c in open_chains if c.trader_id == enriched.trader_id]

        # ── concurrency guards ────────────────────────────────────────────────
        if len(trader_chains) >= risk.max_concurrent_trades:
            return RiskDecision(passed=False, reason="max_concurrent_trades_reached")

        same_symbol = [c for c in trader_chains if c.symbol == symbol]
        if len(same_symbol) >= risk.max_concurrent_same_symbol:
            return RiskDecision(passed=False, reason="max_concurrent_same_symbol_reached")

        if any(c.symbol == symbol and c.side == side for c in trader_chains):
            return RiskDecision(passed=False, reason="duplicate_position")

        # ── stop-loss required ────────────────────────────────────────────────
        if signal.stop_loss is None or signal.stop_loss.price is None:
            return RiskDecision(passed=False, reason="missing_stop_loss_for_risk_calc")
        sl_price = signal.stop_loss.price.value

        # ── entry price resolution ────────────────────────────────────────────
        if not signal.entries:
            return RiskDecision(passed=False, reason="no_entry_legs")

        first_leg = signal.entries[0]
        entry_price_deferred = False

        if first_leg.entry_type == "MARKET":
            if market_snapshot is not None and market_snapshot.mark_price is not None:
                entry_price: float | None = market_snapshot.mark_price
            else:
                entry_price = None
                entry_price_deferred = True
        else:
            if first_leg.price is None:
                return RiskDecision(passed=False, reason="missing_limit_price")
            entry_price = first_leg.price.value

        if not entry_price_deferred:
            risk_distance: float | None = abs(entry_price - sl_price)  # type: ignore[arg-type]
            if risk_distance == 0:
                return RiskDecision(passed=False, reason="zero_risk_distance")
        else:
            risk_distance = None

        # ── capital base ──────────────────────────────────────────────────────
        if risk.capital_base_mode == "live_equity":
            if account_snapshot is None or account_snapshot.equity_usdt is None:
                return RiskDecision(passed=False, reason="missing_account_snapshot_for_live_equity")
            capital = account_snapshot.equity_usdt
        else:
            capital = risk.capital_base_usdt

        # ── risk amount ───────────────────────────────────────────────────────
        if risk.mode == "risk_usdt_fixed":
            risk_amount = risk.risk_usdt_fixed
        else:
            risk_amount = capital * risk.risk_pct_of_capital / 100.0

        # ── max capital-at-risk guard ─────────────────────────────────────────
        max_risk = capital * risk.max_capital_at_risk_per_trader_pct / 100.0
        current_open_risk = 0.0
        for c in trader_chains:
            try:
                snap = json.loads(c.risk_snapshot_json)
                current_open_risk += float(snap.get("risk_amount", 0.0))
            except (json.JSONDecodeError, ValueError, TypeError):
                pass
        if current_open_risk + risk_amount > max_risk:
            return RiskDecision(passed=False, reason="max_capital_at_risk_exceeded")

        # ── max_leverage guard ────────────────────────────────────────────────
        if config.account is not None:
            if risk.leverage > config.account.max_leverage:
                return RiskDecision(
                    passed=False,
                    reason="risk_leverage_exceeds_account_max_leverage",
                )

        # ── per-leg risk allocation ───────────────────────────────────────────
        n_legs = len(signal.entries)
        legs_snapshot: list[dict] = []
        for leg in signal.entries:
            w = float(leg.weight) if leg.weight is not None else 1.0 / n_legs
            leg_risk = risk_amount * w
            leg_price_val: float | None = leg.price.value if leg.price else (
                entry_price if not entry_price_deferred else None
            )
            is_leg_deferred = leg.entry_type == "MARKET" and entry_price_deferred
            if not is_leg_deferred and leg_price_val is not None:
                leg_rd = abs(leg_price_val - sl_price)
                leg_qty_val: float | None = leg_risk / leg_rd if leg_rd > 0 else 0.0
                qty_mode = "fixed"
            else:
                leg_qty_val = None
                qty_mode = "deferred_market"
            legs_snapshot.append({
                "sequence": leg.sequence,
                "entry_type": leg.entry_type,
                "weight": w,
                "price": leg_price_val,
                "risk_amount": leg_risk,
                "qty": leg_qty_val,
                "qty_mode": qty_mode,
            })

        # ── size calculation ──────────────────────────────────────────────────
        if not entry_price_deferred:
            size_usdt: float | None = risk_amount / risk_distance * entry_price  # type: ignore[operator]
        else:
            size_usdt = None
        leverage = risk.leverage

        risk_snapshot = {
            "capital": capital,
            "risk_amount": risk_amount,
            "entry_price": entry_price,
            "entry_price_deferred": entry_price_deferred,
            "sl_price": sl_price,
            "risk_distance": risk_distance,
            "size_usdt": size_usdt,
            "leverage": leverage,
            "hedge_mode": config.hedge_mode,
            "capital_base_mode": risk.capital_base_mode,
            "legs": legs_snapshot,
        }

        return RiskDecision(
            passed=True,
            reason=None,
            size_usdt=size_usdt,
            leverage=leverage,
            risk_snapshot=risk_snapshot,
        )


__all__ = ["RiskCapacityEngine", "RiskDecision"]
