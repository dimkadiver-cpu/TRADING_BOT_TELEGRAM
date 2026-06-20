# src/runtime_v2/control_plane/formatters/pnl.py
from __future__ import annotations

from src.runtime_v2.control_plane.formatters._blocks import render_template
from src.runtime_v2.control_plane.formatters.templates.commands import TEMPLATE_REGISTRY
from src.runtime_v2.control_plane.scope_resolver import QueryScope
from src.runtime_v2.control_plane.status_queries import PnlView


def _pnl_to_payload(view: PnlView, scope: QueryScope | None) -> dict:
    return {
        "account_id": scope.account_id if scope else (view.account_id or "—"),
        "trader_id": (
            scope.trader_ids[0]
            if scope and scope.trader_ids and len(scope.trader_ids) == 1
            else None
        ),
        "account_id_inner": view.account_id,
        "updated_at": view.updated_at,
        "captured_at": view.captured_at,
        "source": view.source,
        "equity_usdt": view.equity_usdt,
        "available_balance_usdt": view.available_balance_usdt,
        "total_open_risk_usdt": view.total_open_risk_usdt,
        "total_margin_used_usdt": view.total_margin_used_usdt,
        "open_count": view.open_count,
        "partial_count": view.partial_count,
        "waiting_entry_count": view.waiting_entry_count,
        "gross_pnl": view.gross_pnl,
        "total_fees": view.total_fees,
        "fees_usdt": view.fees_usdt,
        "funding_usdt": view.funding_usdt,
        "pnl_net": view.pnl_net,
    }


def format_pnl(view: PnlView, scope: QueryScope | None = None) -> str:
    payload = _pnl_to_payload(view, scope)
    return render_template(TEMPLATE_REGISTRY["pnl"].blocks, payload)


__all__ = ["format_pnl"]
