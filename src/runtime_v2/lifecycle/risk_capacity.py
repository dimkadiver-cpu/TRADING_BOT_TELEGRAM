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

        # ── entry price resolution ────────────────────────────────────────────
        if not signal.entries:
            return RiskDecision(passed=False, reason="no_entry_legs")

        first_leg = signal.entries[0]

        if first_leg.entry_type == "MARKET":
            if market_snapshot is None or market_snapshot.mark_price is None:
                return RiskDecision(passed=False, reason="missing_market_price_for_market_entry")
            entry_price = market_snapshot.mark_price
        else:
            if first_leg.price is None:
                return RiskDecision(passed=False, reason="missing_limit_price")
            entry_price = first_leg.price.value

        # ── stop-loss required ────────────────────────────────────────────────
        if signal.stop_loss is None or signal.stop_loss.price is None:
            return RiskDecision(passed=False, reason="missing_stop_loss_for_risk_calc")
        sl_price = signal.stop_loss.price.value

        risk_distance = abs(entry_price - sl_price)
        if risk_distance == 0:
            return RiskDecision(passed=False, reason="zero_risk_distance")

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

        # ── size calculation ──────────────────────────────────────────────────
        size_usdt = risk_amount / risk_distance * entry_price
        leverage = risk.leverage

        risk_snapshot = {
            "capital": capital,
            "risk_amount": risk_amount,
            "entry_price": entry_price,
            "sl_price": sl_price,
            "risk_distance": risk_distance,
            "size_usdt": size_usdt,
            "leverage": leverage,
            "hedge_mode": config.hedge_mode,
            "capital_base_mode": risk.capital_base_mode,
        }

        return RiskDecision(
            passed=True,
            reason=None,
            size_usdt=size_usdt,
            leverage=leverage,
            risk_snapshot=risk_snapshot,
        )


__all__ = ["RiskCapacityEngine", "RiskDecision"]
