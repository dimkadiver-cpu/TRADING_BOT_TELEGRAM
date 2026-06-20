# src/runtime_v2/control_plane/formatters/stats.py
from __future__ import annotations

from src.runtime_v2.control_plane.formatters._blocks import render_template
from src.runtime_v2.control_plane.formatters.templates.commands import TEMPLATE_REGISTRY
from src.runtime_v2.control_plane.scope_resolver import QueryScope
from src.runtime_v2.control_plane.status_queries import StatsView


def _stats_to_payload(view: StatsView, scope: QueryScope | None) -> dict:
    stats_rows = [
        {
            "label": row.label,
            "trade_count": row.trade_count,
            "win_pct": row.win_pct,
            "pnl_net": row.pnl_net,
            "fees": row.fees,
        }
        for row in view.rows
    ]
    return {
        "account_id": scope.account_id if scope else "—",
        "trader_id": (
            scope.trader_ids[0]
            if scope and scope.trader_ids and len(scope.trader_ids) == 1
            else None
        ),
        "updated_at": view.updated_at,
        "stats_rows": stats_rows,
        "best_chain_id": view.best_chain_id,
        "best_pnl": view.best_pnl,
        "best_symbol": view.best_symbol,
        "worst_chain_id": view.worst_chain_id,
        "worst_pnl": view.worst_pnl,
        "worst_symbol": view.worst_symbol,
    }


def format_stats(view: StatsView, scope: QueryScope | None = None) -> str:
    payload = _stats_to_payload(view, scope)
    return render_template(TEMPLATE_REGISTRY["stats"].blocks, payload)


__all__ = ["format_stats"]
