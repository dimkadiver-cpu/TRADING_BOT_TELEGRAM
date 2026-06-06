# src/runtime_v2/control_plane/formatters/trades.py
from __future__ import annotations

from src.runtime_v2.control_plane.formatters.display import display_symbol
from src.runtime_v2.control_plane.status_queries import TradesView

_SEP = "────────────────"


def _side_emoji(side: str) -> str:
    return "📈" if side == "LONG" else ("📉" if side == "SHORT" else "•")


def _protection(r) -> str:
    if r.has_be:
        return "BE: set"
    if r.has_sl:
        return "SL: set"
    return "NoSL"


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
        id_w = max((len(str(r.chain_id)) for r in view.rows), default=2)
        display_rows = [(r, display_symbol(r.symbol)) for r in view.rows]
        sym_w = max((len(sym) for _, sym in display_rows), default=6)
        side_w = max((len(r.side) for r in view.rows), default=4)
        state_w = max((len(r.state) for r in view.rows), default=5)
        prot_w = max((len(_protection(r)) for r in view.rows), default=6)
        lines.append(
            f"{'ID'.ljust(id_w)} | {'Symbol'.ljust(sym_w)} | {'Side'.ljust(side_w)} | {'State'.ljust(state_w)} | {'Protection'.ljust(prot_w)}"
        )
        lines.append("- " * ((id_w + sym_w + side_w + state_w + prot_w + 12) // 2))
        for r, sym in display_rows:
            lines.append(
                f"{str(r.chain_id).ljust(id_w)} | {sym.ljust(sym_w)} | {r.side.ljust(side_w)} | {r.state.ljust(state_w)} | {_protection(r)}"
            )
    lines += ["", _SEP, "Use:", "/trade #id for details", "/reviews for blocked cases"]
    return "\n".join(lines)


__all__ = ["format_trades"]
