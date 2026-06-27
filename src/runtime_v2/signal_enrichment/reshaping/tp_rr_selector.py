from __future__ import annotations


def compute_anchor(operative_entries: list[tuple[float, float]]) -> float:
    """Weighted average price of operative entries.

    Args:
        operative_entries: list of (price, weight) tuples. Weights need not sum to 1.

    Returns:
        Weighted average price (planned_weighted_average anchor).
    """
    total_weight = sum(w for _, w in operative_entries)
    if total_weight <= 0:
        raise ValueError("operative_entries weights must sum to a positive value")
    return sum(price * w for price, w in operative_entries) / total_weight


def select_tps_by_rr(
    tp_prices: list[float],
    desired_rr: list[float],
    anchor: float,
    r_unit: float,
    strategy: str,
    max_rr_deviation_abs: float,
    on_missing_target: str,
) -> list[float] | None:
    """Select TPs from tp_prices by matching desired RR targets.

    Uses nearest_unique strategy: each source TP can be selected at most once.
    Returns selected TPs in ascending order, or None if any target cannot be matched
    and on_missing_target == "REJECT".

    Args:
        tp_prices: Available TP prices from the signal.
        desired_rr: List of desired RR values to target.
        anchor: Weighted average entry price.
        r_unit: |anchor - stop|, the risk unit.
        strategy: Must be "nearest_unique".
        max_rr_deviation_abs: Maximum absolute RR deviation from target allowed.
        on_missing_target: "REJECT" → return None if any target has no match.
    """
    if r_unit <= 0:
        return None

    rr_for_tp = {price: abs(price - anchor) / r_unit for price in tp_prices}
    available = set(tp_prices)
    selected: list[float] = []

    for target in desired_rr:
        candidates = [
            (abs(rr_for_tp[p] - target), p)
            for p in available
            if abs(rr_for_tp[p] - target) <= max_rr_deviation_abs
        ]
        if not candidates:
            if on_missing_target == "REJECT":
                return None
            continue
        _, best_tp = min(candidates)
        selected.append(best_tp)
        available.discard(best_tp)

    if all(price < anchor for price in selected):
        return sorted(selected, reverse=True)
    return sorted(selected)


__all__ = ["compute_anchor", "select_tps_by_rr"]
