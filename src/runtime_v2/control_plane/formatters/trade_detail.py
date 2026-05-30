# src/runtime_v2/control_plane/formatters/trade_detail.py
from __future__ import annotations

from src.runtime_v2.control_plane.status_queries import TradeDetail

_SEP = "────────────────"


def _side_emoji(side: str) -> str:
    return "📈" if side == "LONG" else ("📉" if side == "SHORT" else "•")


def format_trade_detail(detail: TradeDetail | None) -> str:
    if detail is None:
        return "Trade not found."
    lines = [
        f"📌 TRADE #{detail.chain_id}",
        _SEP,
        f"{detail.symbol} — {_side_emoji(detail.side)} {detail.side}",
        f"Trader: {detail.trader_id}",
        f"Exchange Account: {detail.account_id}",
    ]
    if detail.original_message_link:
        lines.append(f"Source link: {detail.original_message_link}")
    lines += [
        "",
        "Position:",
        f"Avg entry: {detail.entry_avg_price if detail.entry_avg_price is not None else 'n/a'}",
        f"State: {detail.state}",
        "",
        "Protection:",
        f"SL: {detail.current_stop_price if detail.current_stop_price is not None else 'none'}",
    ]
    if detail.last_events:
        lines += ["", "Last events:"]
        lines += detail.last_events
    return "\n".join(lines)


__all__ = ["format_trade_detail"]
