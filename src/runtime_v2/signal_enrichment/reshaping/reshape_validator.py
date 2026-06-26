from __future__ import annotations


def validate_reshape(
    operative_prices: list[float],
    stop_loss_price: float,
    take_profits: list[float],
    side: str,
    anchor: float,
) -> str | None:
    """Validate reshape output against spec §7.2 invariants.

    Returns None if valid, or a reason_code string if the reshape should be REJECTED.
    Side must be "LONG" or "SHORT".
    """
    if not operative_prices:
        return "reshape_no_operative_entry"

    if not take_profits:
        return "reshape_no_take_profit"

    if abs(anchor - stop_loss_price) == 0:
        return "reshape_zero_risk_distance"

    if stop_loss_price in operative_prices:
        return "reshape_stop_equals_entry"

    if side == "LONG":
        if stop_loss_price >= min(operative_prices):
            return "reshape_stop_wrong_side"
        for tp in take_profits:
            if tp <= anchor:
                return "reshape_tp_not_profitable"
        for i in range(1, len(take_profits)):
            if take_profits[i] <= take_profits[i - 1]:
                return "reshape_tp_not_monotonic"

    elif side == "SHORT":
        if stop_loss_price <= max(operative_prices):
            return "reshape_stop_wrong_side"
        for tp in take_profits:
            if tp >= anchor:
                return "reshape_tp_not_profitable"
        for i in range(1, len(take_profits)):
            if take_profits[i] >= take_profits[i - 1]:
                return "reshape_tp_not_monotonic"

    return None


__all__ = ["validate_reshape"]
