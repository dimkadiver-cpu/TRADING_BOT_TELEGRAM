from __future__ import annotations

import json

from src.runtime_v2.lifecycle.breakeven_pricing import (
    compute_breakeven_price, resolve_close_fee_rate,
)
from src.runtime_v2.lifecycle.models import TradeChain
from src.runtime_v2.signal_enrichment.models import ManagementPlanConfig

_FALLBACK_CLOSE_FEE_PROFILES = {
    "bybit_linear": {"attached_full": 0.0006, "standalone_order": 0.001},
}


def resolve_be_stop_price(
    chain: TradeChain,
    management_plan: ManagementPlanConfig,
    *,
    protection_style: str,
) -> float | None:
    entry_avg_price = chain.entry_avg_price
    if entry_avg_price is None:
        return None
    if not management_plan.be_fee_correction_enabled:
        return entry_avg_price

    risk_snapshot = _load_risk_snapshot(chain)
    fees = risk_snapshot.get("fees") if isinstance(risk_snapshot.get("fees"), dict) else {}
    open_position_qty = float(chain.open_position_qty or 0.0)
    open_fee_residual = float(
        risk_snapshot.get("open_fee_residual")
        or fees.get("open_fee_residual")
        or 0.0
    )
    chain_fee_profile = (
        risk_snapshot.get("fee_profile")
        or risk_snapshot.get("close_fee_profile")
        or risk_snapshot.get("be_fee_profile")
        or fees.get("close_fee_profile")
    )
    fallback_profile_name = management_plan.be_fee_fallback_profile
    fallback_profile = _FALLBACK_CLOSE_FEE_PROFILES.get(fallback_profile_name or "")
    if open_position_qty <= 0 or not fallback_profile:
        return entry_avg_price

    try:
        close_fee_rate, close_fee_source = resolve_close_fee_rate(
            protection_style=protection_style,
            chain_fee_profile=chain_fee_profile,
            fallback_profile=fallback_profile,
        )
        return compute_breakeven_price(
            side=chain.side,
            entry_avg_price=entry_avg_price,
            open_position_qty=open_position_qty,
            open_fee_residual=open_fee_residual,
            close_fee_rate=close_fee_rate,
            close_fee_source=close_fee_source,
        ).new_stop_price
    except Exception:
        return entry_avg_price


def _load_risk_snapshot(chain: TradeChain) -> dict:
    try:
        return json.loads(chain.risk_snapshot_json or "{}")
    except Exception:
        return {}


__all__ = ["resolve_be_stop_price"]
