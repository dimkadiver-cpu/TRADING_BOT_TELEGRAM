# src/runtime_v2/control_plane/formatters/control.py
from __future__ import annotations

from src.runtime_v2.control_plane.formatters._blocks import render_template
from src.runtime_v2.control_plane.formatters.display import display_symbol_list
from src.runtime_v2.control_plane.formatters.templates.commands import TEMPLATE_REGISTRY
from src.runtime_v2.control_plane.scope_resolver import QueryScope
from src.runtime_v2.control_plane.status_queries import ControlView


def _control_to_payload(view: ControlView, scope: QueryScope | None) -> dict:
    # Serialize active_blocks to dicts
    active_blocks = [
        {
            "scope_type": b.scope_type,
            "scope_value": b.scope_value,
            "mode": b.mode,
            "created_at": b.created_at,
        }
        for b in (view.active_blocks or [])
    ]

    # Serialize blacklist_global using display_symbol_list
    blacklist_global = display_symbol_list(view.blacklist_global) if view.blacklist_global else []

    # Build per-trader lines list
    per_trader = view.blacklist_per_trader or {}
    blacklist_per_trader_lines: list[str] = []
    for trader, syms in per_trader.items():
        blacklist_per_trader_lines.append(
            f"  {trader}: {', '.join(display_symbol_list(syms))}"
        )

    return {
        "account_id": scope.account_id if scope else "—",
        "trader_id": (
            scope.trader_ids[0]
            if scope and scope.trader_ids and len(scope.trader_ids) == 1
            else None
        ),
        "new_entries_enabled": view.new_entries_enabled,
        "active_blocks": active_blocks,
        "blacklist_global": blacklist_global,
        "blacklist_per_trader": per_trader,
        "blacklist_per_trader_lines": blacklist_per_trader_lines,
    }


def format_control(view: ControlView, scope: QueryScope | None = None) -> str:
    payload = _control_to_payload(view, scope)
    return render_template(TEMPLATE_REGISTRY["control"].blocks, payload)


__all__ = ["format_control"]
