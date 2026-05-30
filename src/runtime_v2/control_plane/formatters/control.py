# src/runtime_v2/control_plane/formatters/control.py
from __future__ import annotations

from src.runtime_v2.control_plane.status_queries import ControlView

_SEP = "────────────────"


def format_control(view: ControlView) -> str:
    lines = [
        "🛡️ CONTROL",
        _SEP,
        f"New entries: {'ENABLED' if view.new_entries_enabled else 'BLOCKED'}",
        "Open positions: managed",
        "Updates: processed",
        "",
    ]
    if view.active_blocks:
        lines.append("Active blocks:")
        for b in view.active_blocks:
            scope = b.scope_value or "GLOBAL"
            when = f" ({b.created_at})" if b.created_at else ""
            lines.append(f"{scope} — {b.mode}{when}")
    else:
        lines.append("Active blocks: none")
    lines += ["", "Symbol blacklist:"]
    lines.append(
        "Global: " + (", ".join(view.blacklist_global) if view.blacklist_global else "none")
    )
    if view.blacklist_per_trader:
        lines.append("Per trader:")
        for trader, syms in view.blacklist_per_trader.items():
            lines.append(f"  {trader}: {', '.join(syms)}")
    else:
        lines.append("Per trader: none")
    return "\n".join(lines)


__all__ = ["format_control"]
