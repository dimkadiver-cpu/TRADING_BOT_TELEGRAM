from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BreakevenPriceResult:
    new_stop_price: float
    open_fee_residual: float
    close_fee_rate: float
    close_fee_source: str


def resolve_close_fee_rate(
    *,
    protection_style: str,
    chain_fee_profile: dict | None,
    fallback_profile: dict,
) -> tuple[float, str]:
    if chain_fee_profile and protection_style in chain_fee_profile:
        return float(chain_fee_profile[protection_style]), "chain"
    return float(fallback_profile[protection_style]), "fallback"


def compute_breakeven_price(
    *,
    side: str,
    entry_avg_price: float,
    open_position_qty: float,
    open_fee_residual: float,
    close_fee_rate: float,
    close_fee_source: str = "chain",
) -> BreakevenPriceResult:
    quantity = open_position_qty
    if side not in {"LONG", "SHORT"}:
        raise ValueError(f"unsupported side: {side}")
    if quantity <= 0:
        raise ValueError("open_position_qty must be > 0")

    fee_denominator = 1 - close_fee_rate if side == "LONG" else 1 + close_fee_rate
    if fee_denominator <= 0:
        raise ValueError(
            f"invalid close_fee_rate for {side}: denominator must be > 0"
        )

    if side == "LONG":
        new_stop_price = (
            (entry_avg_price * quantity + open_fee_residual)
            / (quantity * fee_denominator)
        )
    else:
        new_stop_price = (
            (entry_avg_price * quantity - open_fee_residual)
            / (quantity * fee_denominator)
        )
    return BreakevenPriceResult(
        new_stop_price=new_stop_price,
        open_fee_residual=open_fee_residual,
        close_fee_rate=close_fee_rate,
        close_fee_source=close_fee_source,
    )


__all__ = [
    "BreakevenPriceResult",
    "resolve_close_fee_rate",
    "compute_breakeven_price",
]
