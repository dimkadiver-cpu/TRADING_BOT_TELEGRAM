# src/runtime_v2/control_plane/formatters/dashboard.py
from __future__ import annotations

from datetime import datetime, timezone

from src.runtime_v2.control_plane.formatters._blocks import render_template
from src.runtime_v2.control_plane.formatters.templates.commands import (
    TEMPLATE_REGISTRY as DASHBOARD_TEMPLATE_REGISTRY,
)
from src.runtime_v2.control_plane.scope_resolver import QueryScope
from src.runtime_v2.control_plane.status_queries import StatusQueries


_RECONCILIATION_INTERVAL_SECONDS = 120.0


def _now_hms() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def _format_time(ts: str | None) -> str | None:
    """Extract HH:MM:SS from an ISO timestamp string."""
    if not ts:
        return None
    if len(ts) >= 19:
        return ts[11:19]
    return ts


def _age_seconds(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds()


def _build_scope_meta(scope: QueryScope) -> dict:
    """Return account_id and optional trader_id for header rendering."""
    trader_id: str | None = None
    if scope.trader_ids and len(scope.trader_ids) == 1:
        trader_id = scope.trader_ids[0]
    return {"account_id": scope.account_id, "trader_id": trader_id}


# ---------------------------------------------------------------------------
# Per-view payload builders
# ---------------------------------------------------------------------------

def _build_attivi_payload(
    scope: QueryScope,
    queries: StatusQueries,
    page: int,
    page_size: int,
) -> tuple[dict, int]:
    view = queries.get_open_trades(scope)
    rows = view.rows

    total = view.total
    # Paginate
    start = page * page_size
    page_rows = rows[start: start + page_size]

    # Mark snapshot staleness
    mark_stale = False
    mark_time: str | None = None
    mark_age: int | None = None

    if view.mark_snapshot_max_age_seconds is not None:
        if view.mark_snapshot_max_age_seconds > _RECONCILIATION_INTERVAL_SECONDS:
            mark_stale = True
        # Find the freshest captured_at across rows
        freshest: str | None = None
        for r in rows:
            if r.mark_captured_at:
                if freshest is None or r.mark_captured_at > freshest:
                    freshest = r.mark_captured_at
        if freshest:
            mark_time = _format_time(freshest)
            age = _age_seconds(freshest)
            mark_age = int(age) if age is not None else None

    # Convert TradeRow objects to dicts for template rendering
    row_dicts = [
        {
            "chain_id": r.chain_id,
            "symbol": r.symbol,
            "symbol_display": r.symbol,
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
        }
        for r in page_rows
    ]

    payload = {
        **_build_scope_meta(scope),
        "updated_at": view.updated_at,
        "rows": row_dicts,
        "_mark_stale": mark_stale,
        "_mark_time": mark_time,
        "_mark_age": mark_age,
    }
    return payload, total


def _build_chiusi_payload(
    scope: QueryScope,
    queries: StatusQueries,
    page: int,
    page_size: int,
) -> tuple[dict, int]:
    view = queries.get_closed_trades(scope, page=page, page_size=page_size)

    row_dicts = [
        {
            "chain_id": r.chain_id,
            "symbol": r.symbol,
            "side": r.side,
            "closed_at": r.closed_at,
            "gross_pnl": r.gross_pnl,
        }
        for r in view.rows
    ]

    payload = {
        **_build_scope_meta(scope),
        "updated_at": view.updated_at,
        "rows": row_dicts,
    }
    return payload, view.total_count


def _build_bloccati_payload(
    scope: QueryScope,
    queries: StatusQueries,
) -> tuple[dict, int]:
    view = queries.get_blocked_trades(scope)

    row_dicts = [
        {
            "chain_id": r.chain_id,
            "symbol": r.symbol,
            "state": r.state,
            "reason": r.reason,
        }
        for r in view.rows
    ]

    payload = {
        **_build_scope_meta(scope),
        "updated_at": view.updated_at,
        "rows": row_dicts,
    }
    return payload, len(view.rows)


def _build_pnl_payload(
    scope: QueryScope,
    queries: StatusQueries,
) -> tuple[dict, int]:
    view = queries.get_pnl(scope)

    payload = {
        **_build_scope_meta(scope),
        "updated_at": view.updated_at,
        "equity_usdt": view.equity_usdt,
        "available_balance_usdt": view.available_balance_usdt,
        "total_margin_used_usdt": view.total_margin_used_usdt,
        "gross_pnl": view.gross_pnl,
        "total_fees": view.total_fees,
        "pnl_net": view.pnl_net,
        "open_count": view.open_count,
        "waiting_entry_count": view.waiting_entry_count,
    }
    return payload, 0


def _build_stats_payload(
    scope: QueryScope,
    queries: StatusQueries,
) -> tuple[dict, int]:
    view = queries.get_stats(scope)

    stats_rows = [
        {
            "label": r.label,
            "trade_count": r.trade_count,
            "win_pct": r.win_pct,
            "pnl_net": r.pnl_net,
            "fees": r.fees,
        }
        for r in view.rows
    ]

    payload = {
        **_build_scope_meta(scope),
        "updated_at": view.updated_at,
        "stats_rows": stats_rows,
        "best_chain_id": view.best_chain_id,
        "best_pnl": view.best_pnl,
        "best_symbol": None,
        "worst_chain_id": view.worst_chain_id,
        "worst_pnl": view.worst_pnl,
        "worst_symbol": None,
    }
    return payload, 0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def format_dashboard_view(
    view_name: str,
    scope: QueryScope,
    queries: StatusQueries,
    page: int = 0,
    page_size: int = 5,
) -> tuple[str, int]:
    """Render the text of a dashboard view.

    Returns (text, total_count) where total_count is used for pagination.
    """
    template_key = f"dashboard_{view_name}"
    template = DASHBOARD_TEMPLATE_REGISTRY.get(template_key)
    if template is None:
        raise ValueError(f"Unknown dashboard view: {view_name!r}")

    if view_name == "attivi":
        payload, total = _build_attivi_payload(scope, queries, page, page_size)
    elif view_name == "chiusi":
        payload, total = _build_chiusi_payload(scope, queries, page, page_size)
    elif view_name == "bloccati":
        payload, total = _build_bloccati_payload(scope, queries)
    elif view_name == "pnl":
        payload, total = _build_pnl_payload(scope, queries)
    elif view_name == "stats":
        payload, total = _build_stats_payload(scope, queries)
    else:
        raise ValueError(f"Unknown dashboard view: {view_name!r}")

    text = render_template(template.blocks, payload, transform=template.payload_transform)
    return text, total


# ---------------------------------------------------------------------------
# Keyboard builder
# ---------------------------------------------------------------------------

def build_dashboard_keyboard(
    current_view: str,
    page: int,
    total_count: int,
    page_size: int = 5,
):
    """Build InlineKeyboardMarkup for the dashboard.

    current_view: "attivi", "chiusi", "bloccati", "pnl", "stats"
    page: 0-based current page
    total_count: total items (for pagination row)
    page_size: items per page (default 5)

    Row 1: [⚡ Attivi]  [✅ Chiusi]  [🚫 Bloccati]
    Row 2: [💰 PnL]    [📉 Stats]   [🔄 Refresh]
    Row 3 (conditional, only if total_count > page_size):
      [← Prec] (se page>0) | [Pagina N/M] (noop) | [Succ →] (se non ultima pagina)

    Lazy import of telegram to avoid breaking tests without python-telegram-bot.
    """
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup  # noqa: PLC0415

    def _tab(label: str, view: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(label, callback_data=f"view:{view}")

    row1 = [
        _tab("⚡ Attivi", "attivi"),
        _tab("✅ Chiusi", "chiusi"),
        _tab("🚫 Bloccati", "bloccati"),
    ]
    row2 = [
        _tab("💰 PnL", "pnl"),
        _tab("📉 Stats", "stats"),
        InlineKeyboardButton("🔄 Refresh", callback_data="refresh"),
    ]

    keyboard = [row1, row2]

    if total_count > page_size:
        total_pages = (total_count + page_size - 1) // page_size
        pagination_row: list[InlineKeyboardButton] = []

        if page > 0:
            pagination_row.append(
                InlineKeyboardButton("← Prec", callback_data="page:prev")
            )

        pagination_row.append(
            InlineKeyboardButton(
                f"Pagina {page + 1}/{total_pages}",
                callback_data="noop",
            )
        )

        if page < total_pages - 1:
            pagination_row.append(
                InlineKeyboardButton("Succ →", callback_data="page:next")
            )

        keyboard.append(pagination_row)

    return InlineKeyboardMarkup(keyboard)


__all__ = ["format_dashboard_view", "build_dashboard_keyboard"]
