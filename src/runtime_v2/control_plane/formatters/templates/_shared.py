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
    cid = row.get("chain_id", "?")
    symbol = row.get("symbol_display", row.get("symbol", "?"))
    side = row.get("side", "?")
    state = row.get("state", "?")
    is_global = p.get("is_global", False)

    lines = [f"#{cid} · {symbol} · {side} · {state}"]

    if is_global:
        trader = row.get("trader_id") or p.get("trader_id") or "?"
        account = row.get("account_id") or p.get("account_id") or "?"
        lines.append(f"Trader: {trader} · Account: {account}")

    if state in ("WAITING_ENTRY", "PARTIALLY_FILLED"):
        lines.append("rPnL: —")
    else:
        upnl = row.get("unrealized_pnl")
        rpnl = row.get("cum_realized_pnl")
        upnl_str = money_signed(upnl) if upnl is not None else "—"
        rpnl_str = money_signed(rpnl) if rpnl is not None else "+0.00 USDT"
        lines.append(f"uPnL: {upnl_str}  rPnL: {rpnl_str}")

    lines.append(f"Details: /trade {cid}")
    return lines


__all__ = [
    "_cmd_header",
    "_side_emoji_str",
    "_protection_str",
    "_pnl_str",
    "_render_trade_item",
]
