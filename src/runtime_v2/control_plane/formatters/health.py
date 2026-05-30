# src/runtime_v2/control_plane/formatters/health.py
from __future__ import annotations

from src.runtime_v2.control_plane.status_queries import HealthView

_SEP = "────────────────"


def format_health(view: HealthView) -> str:
    lines = [
        "💊 HEALTH",
        _SEP,
        f"Updated: {view.updated_at}",
        "",
        "Workers:",
    ]
    for name, status, detail in view.workers:
        suffix = f" — {detail}" if detail else ""
        lines.append(f"{name}: {status}{suffix}")
    lines += [
        "",
        "DB:",
        f"ops.sqlite3: {'OK' if view.db_ok else 'ERROR'}",
        "",
        "Exchange:",
        f"Connected: {'YES' if view.exchange_connected else 'NO'}",
    ]
    return "\n".join(lines)


__all__ = ["format_health"]
