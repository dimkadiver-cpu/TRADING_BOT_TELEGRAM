from __future__ import annotations

from src.runtime_v2.control_plane.status_queries import TradeDetail

_SEP = "__SEP__"


def _side_emoji(side: str) -> str:
    return "\U0001f4c8" if side == "LONG" else ("\U0001f4c9" if side == "SHORT" else "\u2022")


def _separator(width: int) -> str:
    dash_count = max(4, (max(width, 1) + 1) // 2)
    return " ".join("-" for _ in range(dash_count))


def _finalize(lines: list[str]) -> str:
    width = max((len(line) for line in lines if line and line != _SEP), default=8)
    sep = _separator(width)
    return "\n".join(sep if line == _SEP else line for line in lines)


def format_trade_detail(detail: TradeDetail | None) -> str:
    if detail is None:
        return "Trade not found."
    lines = [
        f"\U0001f4cc TRADE #{detail.chain_id}",
        _SEP,
        f"{detail.symbol} - {_side_emoji(detail.side)} {detail.side}",
        f"Trader: {detail.trader_id}",
        f"Exchange Account: {detail.account_id}",
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
    if detail.original_message_link:
        lines += ["", _SEP, "Use:", detail.original_message_link]
    return _finalize(lines)


__all__ = ["format_trade_detail"]
