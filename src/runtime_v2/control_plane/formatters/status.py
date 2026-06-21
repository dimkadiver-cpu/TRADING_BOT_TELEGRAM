# src/runtime_v2/control_plane/formatters/status.py
from __future__ import annotations

from src.runtime_v2.control_plane.formatters._blocks import render_template
from src.runtime_v2.control_plane.formatters.templates.commands import TEMPLATE_REGISTRY
from src.runtime_v2.control_plane.scope_resolver import QueryScope
from src.runtime_v2.control_plane.status_queries import StatusView


def status_level(view: StatusView) -> str:
    if view.failed_commands > 0 or view.no_sl_count > 0:
        return "🔴"
    stale = view.sync_age_seconds is not None and view.sync_age_seconds > 30
    if view.review_count > 0 or stale:
        return "🟡"
    return "🟢"


def _status_to_payload(view: StatusView, scope: QueryScope | None) -> dict:
    sync = (
        f"{int(view.sync_age_seconds)}s ago"
        if view.sync_age_seconds is not None
        else "n/a"
    )
    is_global = scope is None or scope.account_id is None
    account_label = "All accounts" if is_global else scope.account_id  # type: ignore[union-attr]
    return {
        "account_id": account_label,
        "is_global": is_global,
        "trader_id": (
            scope.trader_ids[0]
            if scope and scope.trader_ids and len(scope.trader_ids) == 1
            else None
        ),
        "updated_at": view.updated_at,
        "control_mode": view.control_mode,
        "new_entries_enabled": view.new_entries_enabled,
        "sync_age_seconds": view.sync_age_seconds,
        "_sync_str": sync,
        "open_count": view.open_count,
        "partial_count": view.partial_count,
        "waiting_entry_count": view.waiting_entry_count,
        "review_count": view.review_count,
        "pending_commands": view.pending_commands,
        "failed_commands": view.failed_commands,
        "no_sl_count": view.no_sl_count,
        "_level": status_level(view),
        "by_account": view.by_account,
    }


def format_status(view: StatusView, scope: QueryScope | None = None) -> str:
    payload = _status_to_payload(view, scope)
    return render_template(TEMPLATE_REGISTRY["status"].blocks, payload)


__all__ = ["format_status", "status_level"]
