# src/runtime_v2/control_plane/formatters/trades.py
from __future__ import annotations

from src.runtime_v2.control_plane.formatters._blocks import render_template
from src.runtime_v2.control_plane.formatters.display import display_symbol
from src.runtime_v2.control_plane.formatters.templates.commands import TEMPLATE_REGISTRY
from src.runtime_v2.control_plane.scope_resolver import QueryScope
from src.runtime_v2.control_plane.status_queries import TradesView

_RECONCILIATION_THRESHOLD_SECONDS = 60.0


def _trades_to_payload(view: TradesView, scope: QueryScope | None) -> dict:
    is_global = scope is None or scope.account_id is None
    rows = []
    for r in view.rows:
        rows.append({
            "chain_id": r.chain_id,
            "symbol": r.symbol,
            "symbol_display": display_symbol(r.symbol),
            "side": r.side,
            "state": r.state,
            "has_sl": r.has_sl,
            "has_be": r.has_be,
            "entry_avg_price": r.entry_avg_price,
            "open_position_qty": r.open_position_qty,
            "unrealized_pnl": r.unrealized_pnl,
            "cum_realized_pnl": r.cum_realized_pnl,
            "mark_price": r.mark_price,
            "mark_captured_at": r.mark_captured_at,
            "current_stop_price": None,
            "trader_id": r.trader_id,
            "account_id": r.account_id,
        })

    mark_time: str | None = None
    mark_age: str | None = None
    mark_stale = False
    max_age = view.mark_snapshot_max_age_seconds
    if max_age is not None:
        # Try to extract HH:MM:SS part from mark_captured_at of first row with data
        for r in view.rows:
            if r.mark_captured_at:
                t = r.mark_captured_at
                # If it's a full ISO string, take the time portion
                if "T" in t:
                    t = t.split("T")[1][:8]
                elif " " in t:
                    t = t.split(" ")[1][:8]
                mark_time = t
                break
        mark_age = str(int(max_age))
        mark_stale = max_age > _RECONCILIATION_THRESHOLD_SECONDS

    payload: dict = {
        "account_id": scope.account_id if (scope and scope.account_id) else "All accounts",
        "trader_id": (
            scope.trader_ids[0]
            if scope and scope.trader_ids and len(scope.trader_ids) == 1
            else None
        ),
        "is_global": is_global,
        "updated_at": view.updated_at,
        "total": view.total,
        "rows": rows,
        "_mark_time": mark_time,
        "_mark_age": mark_age,
        "_mark_stale": mark_stale,
    }
    return payload


def format_trades(view: TradesView, scope: QueryScope | None = None) -> str:
    payload = _trades_to_payload(view, scope)
    return render_template(TEMPLATE_REGISTRY["trades"].blocks, payload)


__all__ = ["format_trades"]
