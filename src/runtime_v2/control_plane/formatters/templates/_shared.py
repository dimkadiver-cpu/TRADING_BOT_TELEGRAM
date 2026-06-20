# src/runtime_v2/control_plane/formatters/templates/_shared.py
"""Shared helpers used by both commands.py and dashboard.py templates.

Extracted here to avoid a circular import:
  commands.py imports from emergency.py and dashboard.py (to unify the registry)
  dashboard.py needed helpers from commands.py → moved here instead.
"""
from __future__ import annotations

from src.runtime_v2.control_plane.formatters._blocks import (
    DerivedBlock,
    SeparatorBlock,
)
from src.runtime_v2.control_plane.formatters._formatters import num, money_signed


def _cmd_header(emoji: str, command: str) -> list:
    return [
        DerivedBlock(text_fn=lambda p, _e=emoji, _c=command:
            f"{_e} {_c} — {p['account_id']}"
            + (f" · {p['trader_id']}" if p.get("trader_id") else "")
        ),
        SeparatorBlock(),
    ]


def _side_emoji_str(side: str | None) -> str:
    if side == "LONG":
        return "📈"
    if side == "SHORT":
        return "📉"
    return "•"


def _protection_str(row: dict) -> str:
    if row.get("has_be"):
        sl_price = row.get("current_stop_price")
        sl_part = f"SL: {num(sl_price)}" if sl_price is not None else "SL: set"
        return sl_part + "  BE: set"
    if row.get("has_sl"):
        sl_price = row.get("current_stop_price")
        if sl_price is not None:
            return f"SL: {num(sl_price)}"
        return "SL: set"
    return "SL: —"


def _pnl_str(row: dict) -> str:
    v = row.get("unrealized_pnl")
    if v is None:
        return "PnL: —"
    return f"PnL: {money_signed(v)}"


def _render_trade_item(row: dict, i: int, p: dict) -> list[str]:
    chain_id = row.get("chain_id", "?")
    symbol = row.get("symbol_display", row.get("symbol", "?"))
    side = row.get("side", "?")
    state = row.get("state", "?")
    emoji = _side_emoji_str(side)
    entry_price = row.get("entry_avg_price")
    qty = row.get("open_position_qty")

    line1 = f"#{chain_id}  {emoji} {symbol}  {side}   {state}"
    parts2 = []
    if entry_price is not None:
        parts2.append(f"Entry: {num(entry_price)}")
    parts2.append(_protection_str(row))
    line2 = "    " + "  ".join(parts2) if parts2 else ""

    parts3 = []
    if qty is not None:
        parts3.append(f"Qty: {num(qty)}")
    parts3.append(_pnl_str(row))
    line3 = "    " + "  ".join(parts3)

    result = [line1]
    if line2.strip():
        result.append(line2)
    result.append(line3)
    return result


__all__ = [
    "_cmd_header",
    "_side_emoji_str",
    "_protection_str",
    "_pnl_str",
    "_render_trade_item",
]
