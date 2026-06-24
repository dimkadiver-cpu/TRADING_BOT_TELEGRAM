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

_PERIOD_LABELS = {
    "today": "Today",
    "week": "Last 7d",
    "month": "Last 30d",
}


def _effective_scope(scope: QueryScope, filters: dict | None) -> QueryScope:
    """Narrow scope by account/trader filters — cannot expand beyond original scope."""
    if not filters:
        return scope
    account = filters.get("account")
    trader = filters.get("trader")
    eff_account = scope.account_id
    if account:
        if scope.account_id is None or scope.account_id == account:
            eff_account = account
    eff_traders = scope.trader_ids
    if trader:
        if scope.trader_ids is None or trader in scope.trader_ids:
            eff_traders = [trader]
    if eff_account == scope.account_id and eff_traders == scope.trader_ids:
        return scope
    return QueryScope(account_id=eff_account, trader_ids=eff_traders)


def _build_filters_str(is_global: bool, filters: dict | None) -> str | None:
    parts: list[str] = []
    f = filters or {}
    account = f.get("account")
    trader = f.get("trader")
    side = f.get("side")
    period = f.get("period")
    status = f.get("status")
    not_executed_outcome = f.get("not_executed_outcome")
    not_executed_phase = f.get("not_executed_phase")
    issue_type = f.get("issue_type")
    issue_phase = f.get("issue_phase")

    if is_global:
        parts.append(account or "All accounts")
        parts.append(trader or "All traders")
    else:
        if trader:
            parts.append(trader)

    if side:
        parts.append(side.capitalize())
    if period:
        parts.append(_PERIOD_LABELS.get(period, period))
    if status:
        parts.append(status.replace("_", " ").lower().capitalize())
    if not_executed_outcome:
        parts.append(str(not_executed_outcome))
    if not_executed_phase:
        parts.append(str(not_executed_phase))
    if issue_type:
        parts.append(str(issue_type))
    if issue_phase:
        parts.append(str(issue_phase))

    return " · ".join(parts) if parts else None


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


def _human_duration(seconds: float | None) -> str:
    """Convert a duration in seconds to a human-readable string like '2h 34m'."""
    if seconds is None or seconds < 0:
        return "—"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    rem_minutes = minutes % 60
    if rem_minutes == 0:
        return f"{hours}h"
    return f"{hours}h {rem_minutes}m"


def _parse_duration(created_at: str | None, closed_at: str | None) -> str:
    """Compute human-readable duration between two ISO timestamps."""
    if not created_at or not closed_at:
        return "—"
    try:
        dt_start = datetime.fromisoformat(created_at.rstrip("Z"))
        dt_end = datetime.fromisoformat(closed_at.rstrip("Z"))
        delta = (dt_end - dt_start).total_seconds()
        return _human_duration(delta)
    except Exception:
        return "—"


# ---------------------------------------------------------------------------
# Per-view payload builders
# ---------------------------------------------------------------------------

def _build_active_payload(
    scope: QueryScope,
    queries: StatusQueries,
    page: int,
    page_size: int,
    filters: dict | None = None,
) -> tuple[dict, int]:
    is_global = scope.account_id is None
    query_scope = _effective_scope(scope, filters)
    f = filters or {}
    view = queries.get_open_trades(query_scope, side=f.get("side"), status=f.get("status"))
    rows = view.rows

    total = view.total
    # Paginate
    start = page * page_size
    page_rows = rows[start: start + page_size]

    # page_display
    total_pages = max(1, (total + page_size - 1) // page_size)
    page_display = f"{page + 1}/{total_pages}"

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
            "trader_id": r.trader_id or (scope.trader_ids[0] if scope.trader_ids and len(scope.trader_ids) == 1 else None),
            "account_id": r.account_id or scope.account_id,
        }
        for r in page_rows
    ]

    payload = {
        **_build_scope_meta(scope),
        "account_id": scope.account_id or "All accounts",
        "updated_at": view.updated_at,
        "rows": row_dicts,
        "total": total,
        "page_display": page_display,
        "filters_str": _build_filters_str(is_global, filters),
        "order_str": "Updated desc" if is_global else None,
        "is_global": is_global,
        "_mark_stale": mark_stale,
        "_mark_time": mark_time,
        "_mark_age": mark_age,
    }
    return payload, total


# Backward-compat alias
def _build_attivi_payload(
    scope: QueryScope,
    queries: StatusQueries,
    page: int,
    page_size: int,
) -> tuple[dict, int]:
    return _build_active_payload(scope, queries, page, page_size)


def _build_closed_payload(
    scope: QueryScope,
    queries: StatusQueries,
    page: int,
    page_size: int,
    filters: dict | None = None,
) -> tuple[dict, int]:
    is_global = scope.account_id is None
    query_scope = _effective_scope(scope, filters)
    f = filters or {}
    view = queries.get_closed_trades(
        query_scope, page=page, page_size=page_size,
        side=f.get("side"), period=f.get("period"),
    )

    total = view.total_count
    total_pages = max(1, (total + page_size - 1) // page_size)
    page_display = f"{page + 1}/{total_pages}"

    row_dicts = [
        {
            "chain_id": r.chain_id,
            "symbol": r.symbol,
            "side": r.side,
            "closed_at": r.closed_at,
            "gross_pnl": r.gross_pnl,
            "lifecycle_state": r.lifecycle_state,
            "closed_reason": r.closed_reason,
            "duration": _parse_duration(r.created_at, r.closed_at),
            "trader_id": r.trader_id,
            "account_id": r.account_id or scope.account_id,
        }
        for r in view.rows
    ]

    payload = {
        **_build_scope_meta(scope),
        "account_id": scope.account_id or "All accounts",
        "updated_at": view.updated_at,
        "rows": row_dicts,
        "total": total,
        "page_display": page_display,
        "filters_str": _build_filters_str(is_global, filters),
        "order_str": "Closed desc" if is_global else None,
        "is_global": is_global,
    }
    return payload, view.total_count


# Backward-compat alias
def _build_chiusi_payload(
    scope: QueryScope,
    queries: StatusQueries,
    page: int,
    page_size: int,
) -> tuple[dict, int]:
    return _build_closed_payload(scope, queries, page, page_size)


def _build_not_executed_payload(
    scope: QueryScope,
    queries: StatusQueries,
    page: int = 0,
    page_size: int = 5,
    filters: dict | None = None,
) -> tuple[dict, int]:
    is_global = scope.account_id is None
    query_scope = _effective_scope(scope, filters)
    f = filters or {}
    view = queries.get_not_executed_trades(
        query_scope,
        side=f.get("side"),
        outcome=f.get("not_executed_outcome"),
        phase=f.get("not_executed_phase"),
    )

    total = len(view.rows)

    # Paginate
    start = page * page_size
    page_rows = view.rows[start: start + page_size]

    # page_display
    total_pages = max(1, (total + page_size - 1) // page_size)
    page_display = f"{page + 1}/{total_pages}"

    row_dicts = [
        {
            "reference": r.reference,
            "trade_chain_id": r.trade_chain_id,
            "signal_reference": r.signal_reference,
            "symbol": r.symbol,
            "side": r.side or "?",
            "outcome": r.outcome,
            "phase": r.phase,
            "reason": r.reason,
            "command_type": r.command_type,
            "occurred_at": r.occurred_at,
            "details_command": r.details_command,
            "trader_id": r.trader_id,
            "account_id": r.account_id or scope.account_id,
        }
        for r in page_rows
    ]

    payload = {
        **_build_scope_meta(scope),
        "account_id": scope.account_id or "All accounts",
        "updated_at": view.updated_at,
        "rows": row_dicts,
        "total": total,
        "page_display": page_display,
        "filters_str": _build_filters_str(is_global, filters),
        "order_str": "Latest desc" if is_global else None,
        "is_global": is_global,
    }
    return payload, total


def _build_operational_issues_payload(
    scope: QueryScope,
    queries: StatusQueries,
    page: int = 0,
    page_size: int = 5,
    filters: dict | None = None,
) -> tuple[dict, int]:
    is_global = scope.account_id is None
    query_scope = _effective_scope(scope, filters)
    f = filters or {}
    view = queries.get_operational_issues(
        query_scope,
        side=f.get("side"),
        issue_type=f.get("issue_type"),
        phase=f.get("issue_phase"),
    )

    total = len(view.rows)
    start = page * page_size
    page_rows = view.rows[start: start + page_size]
    total_pages = max(1, (total + page_size - 1) // page_size)
    page_display = f"{page + 1}/{total_pages}"

    row_dicts = [
        {
            "trade_chain_id": r.trade_chain_id,
            "symbol": r.symbol,
            "side": r.side or "?",
            "issue_type": r.issue_type,
            "phase": r.phase,
            "reason": r.reason,
            "command_type": r.command_type,
            "occurred_at": r.occurred_at,
            "details_command": r.details_command,
            "trader_id": r.trader_id,
            "account_id": r.account_id or scope.account_id,
        }
        for r in page_rows
    ]

    payload = {
        **_build_scope_meta(scope),
        "account_id": scope.account_id or "All accounts",
        "updated_at": view.updated_at,
        "rows": row_dicts,
        "total": total,
        "page_display": page_display,
        "filters_str": _build_filters_str(is_global, filters),
        "order_str": "Latest desc" if is_global else None,
        "is_global": is_global,
    }
    return payload, total


def _build_pnl_payload(
    scope: QueryScope,
    queries: StatusQueries,
    filters: dict | None = None,
) -> tuple[dict, int]:
    is_global = scope.account_id is None
    query_scope = _effective_scope(scope, filters)
    view = queries.get_pnl(query_scope)
    accounts_in_scope = view.accounts_in_scope or 0

    payload = {
        **_build_scope_meta(scope),
        "account_id": scope.account_id or "All accounts",
        "updated_at": view.updated_at,
        "total": accounts_in_scope if is_global else 1,
        "page_display": "1/1",
        "filters_str": _build_filters_str(is_global, filters),
        "order_str": "Net desc" if is_global else None,
        "is_global": is_global,
        # equity_usdt removed — use futures_wallet_usdt instead
        "available_balance_usdt": view.available_balance_usdt,
        "total_margin_used_usdt": view.total_margin_used_usdt,
        "futures_wallet_usdt": (
            (view.available_balance_usdt or 0.0) + (view.total_margin_used_usdt or 0.0)
            if (view.available_balance_usdt is not None or view.total_margin_used_usdt is not None)
            else None
        ),
        "gross_pnl": view.gross_pnl,
        "total_fees": view.total_fees,
        "pnl_net": view.pnl_net,
        "partial_pnl": view.partial_pnl,
        "partial_fees": view.partial_fees,
        "partial_pnl_net": view.partial_pnl_net,
        "by_trader": view.by_trader,
        "open_count": view.open_count,
        "waiting_entry_count": view.waiting_entry_count,
        "accounts_in_scope": view.accounts_in_scope,
        "by_account": view.by_account,
        "captured_at": view.captured_at,
        "source": view.source,
        "account_unrealized_pnl_usdt": view.account_unrealized_pnl_usdt,
        "snapshot_age_seconds": view.snapshot_age_seconds,
        "snapshot_stale": view.snapshot_stale,
        "total_open_risk_usdt": view.total_open_risk_usdt,
        "accounts_fresh": view.accounts_fresh,
        "accounts_stale": view.accounts_stale,
    }
    return payload, 0


def _build_stats_payload(
    scope: QueryScope,
    queries: StatusQueries,
    filters: dict | None = None,
) -> tuple[dict, int]:
    is_global = scope.account_id is None
    query_scope = _effective_scope(scope, filters)
    f = filters or {}
    view = queries.get_stats(query_scope, side=f.get("side"))
    accounts_in_scope = len(view.by_account) if view.by_account else 0

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
        "account_id": scope.account_id or "All accounts",
        "updated_at": view.updated_at,
        "total": accounts_in_scope if is_global else 0,
        "page_display": "1/1",
        "filters_str": _build_filters_str(is_global, filters),
        "order_str": "Net desc" if is_global else None,
        "is_global": is_global,
        "stats_rows": stats_rows,
        "best_chain_id": view.best_chain_id,
        "best_pnl": view.best_pnl,
        "best_symbol": None,
        "worst_chain_id": view.worst_chain_id,
        "worst_pnl": view.worst_pnl,
        "worst_symbol": None,
        "by_account": view.by_account,
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
    filters: dict | None = None,
) -> tuple[str, int]:
    """Render the text of a dashboard view.

    Returns (text, total_count) where total_count is used for pagination.
    Accepts both English names and legacy aliases for backward compatibility.
    """
    _name_map = {
        "attivi": "active",
        "chiusi": "closed",
        "bloccati": "not_executed",
        "blocked": "not_executed",
    }
    normalized = _name_map.get(view_name, view_name)

    template_key = f"dashboard_{normalized}"
    template = DASHBOARD_TEMPLATE_REGISTRY.get(template_key)
    if template is None:
        raise ValueError(f"Unknown dashboard view: {view_name!r}")

    if normalized == "active":
        payload, total = _build_active_payload(scope, queries, page, page_size, filters)
    elif normalized == "closed":
        payload, total = _build_closed_payload(scope, queries, page, page_size, filters)
    elif normalized == "not_executed":
        payload, total = _build_not_executed_payload(scope, queries, page, page_size, filters)
    elif normalized == "operational_issues":
        payload, total = _build_operational_issues_payload(scope, queries, page, page_size, filters)
    elif normalized == "pnl":
        payload, total = _build_pnl_payload(scope, queries, filters)
    elif normalized == "stats":
        payload, total = _build_stats_payload(scope, queries, filters)
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

    current_view: "active", "closed", "not_executed", "operational_issues", "pnl", "stats"
    page: 0-based current page
    total_count: total items (for pagination row)
    page_size: items per page (default 5)

    Row 1: [⚡ Active]  [✅ Closed]  [🚫 Blocked]
    Row 2: [💰 PnL]    [📉 Stats]   [🔄 Refresh]
    Row 3: [🔎 Filters]  [🧹 Clear]
    Row 4 (conditional, only if total_count > page_size):
      [← Prev] (if page>0) | [Page N/M] (noop) | [Next →] (if not last page)

    Lazy import of telegram to avoid breaking tests without python-telegram-bot.
    """
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup  # noqa: PLC0415

    def _tab(label: str, view: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(label, callback_data=f"view:{view}")

    row1 = [
        _tab("Active", "active"),
        _tab("Closed", "closed"),
        _tab("Not executed", "not_executed"),
    ]
    row2 = [
        _tab("Operational issues", "operational_issues"),
        _tab("PnL", "pnl"),
        _tab("Stats", "stats"),
    ]
    row3 = [
        InlineKeyboardButton("Refresh", callback_data="refresh"),
        InlineKeyboardButton("Filters", callback_data="filters"),
        InlineKeyboardButton("Clear", callback_data="clear"),
    ]

    keyboard = [row1, row2, row3]

    if total_count > page_size:
        total_pages = (total_count + page_size - 1) // page_size
        pagination_row: list[InlineKeyboardButton] = []

        if page > 0:
            pagination_row.append(
                InlineKeyboardButton("← Prev", callback_data="page:prev")
            )

        pagination_row.append(
            InlineKeyboardButton(
                f"Page {page + 1}/{total_pages}",
                callback_data="noop",
            )
        )

        if page < total_pages - 1:
            pagination_row.append(
                InlineKeyboardButton("Next →", callback_data="page:next")
            )

        keyboard.append(pagination_row)

    return InlineKeyboardMarkup(keyboard)


__all__ = ["format_dashboard_view", "build_dashboard_keyboard"]
