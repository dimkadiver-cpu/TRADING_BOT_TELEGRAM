# src/runtime_v2/control_plane/formatters/status.py
from __future__ import annotations

from src.runtime_v2.control_plane.status_queries import StatusView

_SEP = "────────────────"


def status_level(view: StatusView) -> str:
    if view.failed_commands > 0 or view.no_sl_count > 0:
        return "🔴"
    stale = view.sync_age_seconds is not None and view.sync_age_seconds > 30
    if view.review_count > 0 or stale:
        return "🟡"
    return "🟢"


def format_status(view: StatusView) -> str:
    sync = (
        f"{int(view.sync_age_seconds)}s ago" if view.sync_age_seconds is not None else "n/a"
    )
    lines = [
        f"{status_level(view)} Runtime V2 — STATUS",
        _SEP,
        f"Updated: {view.updated_at}",
        "",
        "Mode:",
        f"New entries: {'ENABLED' if view.new_entries_enabled else 'BLOCKED'}",
        f"Control: {view.control_mode}",
        f"Sync: {sync}",
        "",
        "Trades:",
        f"Open: {view.open_count}",
        f"Waiting entry: {view.waiting_entry_count}",
        f"Partial: {view.partial_count}",
        f"Review required: {view.review_count}",
        "",
        "Execution:",
        f"Pending commands: {view.pending_commands}",
        f"Failed commands: {view.failed_commands}",
        "",
        "Risk:",
        f"No SL: {view.no_sl_count}",
        "",
        "Use:",
        "/trades",
        "/reviews",
        "/control",
    ]
    return "\n".join(lines)


__all__ = ["format_status", "status_level"]
