# src/runtime_v2/control_plane/formatters/reviews.py
from __future__ import annotations

from src.runtime_v2.control_plane.formatters._blocks import (
    render_template,
    StaticBlock,
    SeparatorBlock,
    DerivedBlock,
    ConditionalBlock,
    ListBlock,
    TemplateConfig,
)
from src.runtime_v2.control_plane.formatters.display import display_symbol
from src.runtime_v2.control_plane.scope_resolver import QueryScope
from src.runtime_v2.control_plane.status_queries import ReviewsView


def _render_review_item(item: dict, i: int, p: dict) -> list[str]:
    cid = item.get("chain_id")
    symbol = display_symbol(item.get("symbol")) or item.get("symbol") or "?"
    reason = item.get("reason", "unknown")
    line = f"#{cid}  {symbol}  {reason}" if cid is not None else f"?  {symbol}  {reason}"
    lines = [line]
    if p.get("is_global") and (item.get("trader_id") or item.get("account_id")):
        lines.append(
            f"     Trader: {item.get('trader_id', '?')} · Account: {item.get('account_id', '?')}"
        )
    return lines


_REVIEWS_BLOCKS: list = [
    DerivedBlock(text_fn=lambda p: (
        f"{'⚠️' if p.get('items') else '✅'} REVIEWS  |  "
        + (p.get("account_id") or "All accounts")
    )),
    SeparatorBlock(),
    DerivedBlock(text_fn=lambda p: f"Updated: {p.get('updated_at', 'n/a')}"),
    ConditionalBlock(
        condition=lambda p: bool(p.get("items")),
        blocks=[
            StaticBlock(""),
            ListBlock(key="items", item_renderer=_render_review_item),
            StaticBlock(""),
            StaticBlock("/trade #id  for details"),
        ],
    ),
    ConditionalBlock(
        condition=lambda p: not p.get("items"),
        blocks=[StaticBlock("No pending reviews.")],
    ),
]

_TEMPLATE_REVIEWS = TemplateConfig(_REVIEWS_BLOCKS, payload_transform=None)


def format_reviews(view: ReviewsView, scope: QueryScope | None = None) -> str:
    is_global = scope is None or scope.account_id is None
    payload = {
        "account_id": scope.account_id if (scope and scope.account_id) else None,
        "is_global": is_global,
        "updated_at": view.updated_at,
        "items": [
            {
                "chain_id": item.chain_id,
                "symbol": item.symbol,
                "reason": item.reason,
                "trader_id": item.trader_id,
                "account_id": item.account_id,
            }
            for item in view.items
        ],
    }
    return render_template(_TEMPLATE_REVIEWS.blocks, payload)


__all__ = ["format_reviews"]
