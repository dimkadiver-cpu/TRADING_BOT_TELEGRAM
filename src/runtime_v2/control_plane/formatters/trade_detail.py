from __future__ import annotations

from datetime import datetime, timezone

from src.runtime_v2.control_plane.formatters._blocks import (
    ConditionalBlock,
    DerivedBlock,
    ListBlock,
    SeparatorBlock,
    StaticBlock,
    TemplateConfig,
    render_template,
)
from src.runtime_v2.control_plane.formatters._formatters import (
    money_signed,
    pct_signed,
    r_mult,
)
from src.runtime_v2.control_plane.formatters.display import display_symbol
from src.runtime_v2.control_plane.status_queries import TradeDetail


def _fmt_leg(leg: dict) -> str:
    price = leg.get("price", "?")
    status = leg.get("status", "pending")
    if status == "filled":
        return f"{price} ✓"
    if status == "cancelled":
        return f"{price} ✗"
    return str(price)


def _render_event(ev: dict, i: int, p: dict) -> list[str]:
    label = ev.get("label", "EVENT")
    ts = ev.get("timestamp", "")
    lines = [""] if i > 0 else []  # blank line between events
    lines.append(f"• {label} · {ts}")
    if ev.get("event_type"):
        lines.append(f"  Type: {ev['event_type']}")
    if ev.get("reason"):
        lines.append(f"  Reason: {ev['reason']}")
    source = ev.get("source")
    link = ev.get("clean_log_link")
    if source:
        source_part = f"Source: {source}"
        if link:
            source_part += f" -> {link}"
        lines.append(f"  {source_part}")
    return lines


_TRADE_DETAIL_BLOCKS: list = [
    # 1. Header
    DerivedBlock(
        text_fn=lambda p: (
            f"#{p['chain_id']} · {display_symbol(p['symbol'])} "
            f"· {p['side']} · {p['state']}"
        )
    ),
    SeparatorBlock(),
    # 2. Meta
    DerivedBlock(text_fn=lambda p: f"Trader: {p['trader_id']}"),
    DerivedBlock(text_fn=lambda p: f"Exchange Account: {p['account_id']}"),
    DerivedBlock(text_fn=lambda p: f"Updated: {p['updated_at']}"),
    SeparatorBlock(),
    # 3. Order structure
    ConditionalBlock(
        condition=lambda p: bool(p.get("entry_legs")),
        blocks=[
            DerivedBlock(
                text_fn=lambda p: "Entry: " + " · ".join(
                    _fmt_leg(leg) for leg in p["entry_legs"]
                )
            )
        ],
    ),
    ConditionalBlock(
        condition=lambda p: bool(p.get("tp_legs")),
        blocks=[
            DerivedBlock(
                text_fn=lambda p: "TP:    " + " · ".join(
                    _fmt_leg(leg) for leg in p["tp_legs"]
                )
            )
        ],
    ),
    ConditionalBlock(
        condition=lambda p: bool(p.get("sl_price")),
        blocks=[
            DerivedBlock(
                text_fn=lambda p: (
                    f"SL:    {p['sl_price']}"
                    + (" · BE: set" if p.get("has_be") else " · BE: No")
                )
            )
        ],
    ),
    # 4a. Economic state — open/actionable, not WAITING_ENTRY/PARTIALLY_FILLED, not terminal
    ConditionalBlock(
        condition=lambda p: (
            bool(p.get("is_actionable"))
            and p.get("state") not in ("WAITING_ENTRY", "PARTIALLY_FILLED")
            and not p.get("is_terminal")
        ),
        blocks=[
            DerivedBlock(
                text_fn=lambda p: (
                    f"uPnL:  {money_signed(p.get('unrealized_pnl'))}  "
                    f"rPnL:  {money_signed(p.get('cum_realized_pnl', 0.0))}"
                )
            ),
        ],
    ),
    # 4b. Final Result for terminal trades (not CANCELLED_UNFILLED)
    ConditionalBlock(
        condition=lambda p: (
            bool(p.get("is_terminal"))
            and bool(p.get("final_result"))
            and p.get("state") != "CANCELLED_UNFILLED"
        ),
        blocks=[
            SeparatorBlock(),
            StaticBlock("Final Result:"),
            DerivedBlock(
                text_fn=lambda p: (
                    f"ROI net: {pct_signed(p['final_result'].get('roi_net'))}  "
                    f"· RoR: {pct_signed(p['final_result'].get('ror'))}  "
                    f"· R: {r_mult(p['final_result'].get('r_mult'))}"
                )
            ),
            DerivedBlock(
                text_fn=lambda p: (
                    f"PnL net: {money_signed(p['final_result'].get('pnl_net'))}  "
                    f"· PnL gross: {money_signed(p['final_result'].get('pnl_gross'))}"
                )
            ),
            DerivedBlock(
                text_fn=lambda p: (
                    f"Fees: {money_signed(p['final_result'].get('fees'))}  "
                    f"· Funding: {money_signed(p['final_result'].get('funding'))}"
                )
            ),
        ],
    ),
    # 4c. Cancelled unfilled
    ConditionalBlock(
        condition=lambda p: p.get("state") == "CANCELLED_UNFILLED",
        blocks=[
            SeparatorBlock(),
            StaticBlock("Final Result:"),
            StaticBlock("PnL: No fill"),
        ],
    ),
    # 5. Actions — only if actionable and not terminal
    ConditionalBlock(
        condition=lambda p: bool(p.get("is_actionable")) and not p.get("is_terminal"),
        blocks=[
            SeparatorBlock(),
            DerivedBlock(
                text_fn=lambda p: (
                    f"Actions: /cancel_{p['chain_id']} · /close_{p['chain_id']}"
                )
            ),
        ],
    ),
    # 6. Timeline
    ConditionalBlock(
        condition=lambda p: bool(p.get("events")),
        blocks=[
            SeparatorBlock(),
            StaticBlock("Events:"),
            ListBlock(key="events", item_renderer=_render_event, index_start=0),
        ],
    ),
]

_TEMPLATE_TRADE_DETAIL = TemplateConfig(_TRADE_DETAIL_BLOCKS, payload_transform=None)


def format_trade_detail(detail: TradeDetail | None) -> str:
    if detail is None:
        return "Trade not found."

    updated_at = datetime.now(timezone.utc).strftime("%H:%M:%S")

    events_payload = [
        {
            "label": ev.label,
            "timestamp": ev.timestamp,
            "source": ev.source,
            "event_type": ev.event_type,
            "reason": ev.reason,
            "clean_log_link": ev.clean_log_link,
        }
        for ev in (detail.events or [])
    ]

    payload = {
        "chain_id": detail.chain_id,
        "symbol": detail.symbol,
        "side": detail.side,
        "state": detail.state,
        "trader_id": detail.trader_id,
        "account_id": detail.account_id,
        "updated_at": updated_at,
        "entry_legs": detail.entry_legs,
        "tp_legs": detail.tp_legs,
        "sl_price": detail.sl_price,
        "has_be": detail.has_be,
        "unrealized_pnl": detail.unrealized_pnl,
        "cum_realized_pnl": detail.cum_realized_pnl,
        "final_result": detail.final_result,
        "is_actionable": detail.is_actionable,
        "is_terminal": detail.is_terminal,
        "events": events_payload,
    }
    return render_template(_TEMPLATE_TRADE_DETAIL.blocks, payload)


__all__ = ["format_trade_detail"]
