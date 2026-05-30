from __future__ import annotations

from src.runtime_v2.control_plane.status_queries import PnlView

_SEP = "----------------"


def _fmt_money(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f} USDT"


def format_pnl(view: PnlView) -> str:
    lines = [
        "PNL SNAPSHOT",
        _SEP,
        f"Updated: {view.updated_at}",
        f"Account: {view.account_id or 'n/a'}",
        f"Snapshot at: {view.captured_at or 'n/a'}",
        f"Source: {view.source or 'n/a'}",
        "",
        "Persisted account data:",
        f"Equity: {_fmt_money(view.equity_usdt)}",
        f"Available balance: {_fmt_money(view.available_balance_usdt)}",
        f"Open risk: {_fmt_money(view.total_open_risk_usdt)}",
        f"Margin used: {_fmt_money(view.total_margin_used_usdt)}",
        "",
        "Open chains:",
        f"Open: {view.open_count}",
        f"Partial: {view.partial_count}",
        f"Waiting entry: {view.waiting_entry_count}",
        "",
        "Unavailable in current persistence:",
        "Realized PnL: n/a",
        "Unrealized PnL: n/a",
        "ROI/Funding/Fees: n/a",
    ]
    return "\n".join(lines)


__all__ = ["format_pnl"]
