# src/runtime_v2/control_plane/formatters/trades.py
from __future__ import annotations

from src.runtime_v2.control_plane.status_queries import TradesView

_SEP = "────────────────"


def _side_emoji(side: str) -> str:
    return "📈" if side == "LONG" else ("📉" if side == "SHORT" else "•")


def format_trades(view: TradesView) -> str:
    lines = [
        f"📊 OPEN TRADES — {view.total} active",
        _SEP,
        f"Updated: {view.updated_at}",
        "",
    ]
    if not view.rows:
        lines.append("No open trades.")
    else:
        for r in view.rows:
            sl = "SL: set" if r.has_sl else "NoSL"
            lines.append(
                f"#{r.chain_id} {r.symbol} {_side_emoji(r.side)} {r.side} | {r.state} | {sl}"
            )
    lines += ["", _SEP, "Use:", "/trade #id for details", "/reviews for blocked cases"]
    return "\n".join(lines)


__all__ = ["format_trades"]
