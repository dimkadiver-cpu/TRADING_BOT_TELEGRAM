# src/runtime_v2/control_plane/formatters/reviews.py
from __future__ import annotations

from src.runtime_v2.control_plane.status_queries import ReviewsView

_SEP = "────────────────"


def format_reviews(view: ReviewsView) -> str:
    lines = [
        f"⚠️ REVIEWS — {len(view.items)} required",
        _SEP,
        f"Updated: {view.updated_at}",
        "",
    ]
    if not view.items:
        lines.append("No reviews pending.")
    else:
        for it in view.items:
            cid = f"#{it.chain_id}" if it.chain_id is not None else "#?"
            sym = it.symbol or "?"
            lines.append(f"{cid} {sym} | {it.reason}")
    lines += ["", "Use:", "/trade #id for details", "/control for pause/resume"]
    return "\n".join(lines)


__all__ = ["format_reviews"]
