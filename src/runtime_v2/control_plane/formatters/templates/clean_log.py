# src/runtime_v2/control_plane/formatters/templates/clean_log.py
from __future__ import annotations

from src.runtime_v2.control_plane.formatters._blocks import (
    _SEP, _BULLET,
    SeparatorBlock, StaticBlock, DerivedBlock, HeaderBlock,
    FieldBlock, ConditionalBlock, BranchBlock, ListBlock, FooterBlock,
    TemplateConfig,
)
from src.runtime_v2.control_plane.formatters._formatters import (
    num, text, money, money_signed, pct, pct_signed, fee_rate,
)
from src.runtime_v2.control_plane.formatters.display import display_symbol


# ---------------------------------------------------------------------------
# Shared item renderers (used as item_renderer in ListBlock)
# ---------------------------------------------------------------------------

def _render_entry_item(entry: dict, i: int, p: dict) -> list[str]:
    seq = entry.get("sequence", i)
    etype = entry.get("entry_type", "LIMIT")
    price = entry.get("price")
    if etype == "MARKET":
        price_str = f"Market ~{num(price)}" if price is not None else "Market"
    else:
        price_str = f"{num(price)} Limit" if price is not None else "Limit"
    pcts = p.get("_entry_pcts") or []
    pct_suffix = f" ({pcts[i - 1]}%)" if len(pcts) >= 2 and i <= len(pcts) else ""
    return [f"Entry_{seq}: {price_str}{pct_suffix}"]


def _render_tp_item(tp: object, i: int, p: dict) -> list[str]:
    pcts = p.get("_tp_pcts") or []
    pct_suffix = f" ({pcts[i - 1]}%)" if len(pcts) >= 2 and i <= len(pcts) else ""
    return [f"TP_{i}: {num(tp)}{pct_suffix}"]


def _render_pending_entry(entry: dict, i: int, p: dict) -> list[str]:
    seq = entry.get("sequence", "?")
    price = entry.get("price")
    etype = entry.get("entry_type", "LIMIT").capitalize()
    price_str = num(price) if price is not None else "?"
    return [f"Pending: Entry_{seq} {price_str} {etype}"]


def _render_changed_item(item: object, i: int, p: dict) -> list[str]:
    if isinstance(item, dict):
        field_name = item.get("field", "?")
        value = f"{num(item.get('old'))} → {num(item.get('new'))}"
        note = item.get("note")
        if note:
            return [f"{_BULLET} {field_name}: {value} *"]
        return [f"{_BULLET} {field_name}: {value}"]
    return [f"{_BULLET} {item}"]


# ---------------------------------------------------------------------------
# Shared block lists
# ---------------------------------------------------------------------------

CLOSE_METRICS: list = [
    FieldBlock(label=lambda p: p.get("exit_label", "Price"), key="exit_price",
               fmt=num, optional=False, default="n/a"),
    FieldBlock("Qty",      key="closed_qty",  fmt=num),
    FieldBlock("PnL",      key="pnl",         fmt=money_signed),
    FieldBlock("Fee rate", key="fee_rate",     fmt=fee_rate),
    FieldBlock("Fee",      key="fee",          fmt=money),
]

FINAL_RESULT: list = [
    SeparatorBlock(),
    StaticBlock("Final Result:"),
    FieldBlock("ROI net",       value_fn=lambda p: (p.get("final_result") or {}).get("roi_net_pct"),
               fmt=pct_signed,   optional=False, default="n/a"),
    FieldBlock("RoR",           value_fn=lambda p: (p.get("final_result") or {}).get("return_on_risk_pct"),
               fmt=pct_signed,   optional=False, default="n/a"),
    FieldBlock("Total PnL net", value_fn=lambda p: (p.get("final_result") or {}).get("total_pnl_net"),
               fmt=money_signed, optional=False, default="n/a"),
    FieldBlock("Gross PnL",     value_fn=lambda p: (p.get("final_result") or {}).get("gross_pnl"),
               fmt=money_signed, optional=False, default="n/a"),
    FieldBlock("Fees",          value_fn=lambda p: (p.get("final_result") or {}).get("fees"),
               fmt=money_signed, optional=False, default="n/a"),
    FieldBlock("Funding",       value_fn=lambda p: (p.get("final_result") or {}).get("funding"),
               fmt=money_signed, optional=False, default="n/a"),
]

_FILL_SECTION: list = [
    StaticBlock("Filled:"),
    DerivedBlock(text_fn=lambda p: (
        f"Entry_{p['filled_leg_sequence']}: {num(p['fill_price'])} "
        f"{p.get('entry_type_for_leg', 'Limit').capitalize()}"
        if p.get("filled_leg_sequence") is not None else ""
    )),
    BranchBlock(
        condition=lambda p: bool(p.get("is_partial_leg")),
        then_blocks=[
            DerivedBlock(text_fn=lambda p:
                f"Qty: {num(p['filled_qty'])} (planned: {num(p['planned_qty'])})"
            ),
        ],
        else_blocks=[FieldBlock("Qty", key="filled_qty", fmt=num)],
    ),
    FieldBlock("Value",    key="exec_value", fmt=money),
    FieldBlock("Fee rate", key="fee_rate",   fmt=fee_rate),
    FieldBlock("Fee",      key="fee",        fmt=money),
    ConditionalBlock(
        condition=lambda p: bool(p.get("is_partial_leg")),
        blocks=[FieldBlock("Partial", key="_leg_fill_pct", fmt=pct)],
    ),
    SeparatorBlock(),
]

_SIGNAL_BODY: list = [
    ListBlock(key="entries", item_renderer=_render_entry_item),
    FieldBlock("SL",   key="sl",       fmt=num),
    ListBlock(key="tps", item_renderer=_render_tp_item),
    FieldBlock("Risk", key="risk_pct", fmt=lambda v: f"{v}%"),
]

_ENTRY_POSITION_SECTION: list = [
    StaticBlock("Position:"),
    FieldBlock("Avg entry",   key="_avg_entry",          fmt=num),
    FieldBlock("Total qty",   key="total_filled_qty",    fmt=num),
    FieldBlock("Total value", key="total_value",         fmt=money),
    FieldBlock("Total fees",  key="total_fees",          fmt=money),
    FieldBlock("Filled",      key="position_filled_pct", fmt=pct),
    ConditionalBlock(
        condition=lambda p: p.get("actual_risk_usdt") is not None,
        blocks=[
            DerivedBlock(text_fn=lambda p:
                f"Risk: {money(p.get('actual_risk_usdt'))} "
                f"(planned: {money(p.get('planned_risk_usdt'))})"
            ),
        ]
    ),
    BranchBlock(
        condition=lambda p: bool(p.get("pending_entries")),
        then_blocks=[ListBlock(key="pending_entries", item_renderer=_render_pending_entry)],
        else_blocks=[StaticBlock("Pending: none")],
    ),
]


def _build_signal_notes(p: dict) -> list[str]:
    notes: list[str] = []
    rd = p.get("range_derivation") or {}
    if rd.get("derived_from_range"):
        mode = str(rd.get("split_mode") or "").capitalize()
        min_p = rd.get("original_min_price")
        max_p = rd.get("original_max_price")
        if mode and min_p is not None and max_p is not None:
            notes.append(f"Entry - {mode} [{num(min_p)}-{num(max_p)}]")
    if p.get("risk_hint_applied"):
        notes.append("Risk - Reduced by trader")
    return notes


# ---------------------------------------------------------------------------
# Close templates (SL_FILLED, TP_FILLED_FINAL, POSITION_CLOSED, BE_EXIT)
# ---------------------------------------------------------------------------

_CLOSED_BLOCKS: list = [
    HeaderBlock(emoji=lambda p: p["_emoji"], event_label="POSITION CLOSED"),
    FieldBlock("Close reason", key="close_reason", optional=False, default="n/a"),
    SeparatorBlock(),
    *CLOSE_METRICS,
    *FINAL_RESULT,
    FooterBlock(default_source="exchange"),
]


def _t_sl_filled(p: dict) -> dict:
    return {**p, "_emoji": "🛑", "exit_label": "SL",
            "exit_price": p.get("sl_price", p.get("fill_price"))}


def _t_tp_final(p: dict) -> dict:
    level = p.get("tp_level")
    display_price = p.get("fill_price") if p.get("fill_price") is not None else p.get("tp_price")
    return {
        **p,
        "_emoji": "✅",
        "exit_label": f"TP_{level}" if level is not None else "TP",
        "exit_price": display_price,
        "close_reason": p.get("close_reason") or "FINAL TP FILLED",
    }


def _t_position_closed(p: dict) -> dict:
    return {
        **p,
        "_emoji": "✋",
        "exit_label": "Price",
        "exit_price": p.get("fill_price"),
        "close_reason": p.get("close_reason") or "MANUAL_CLOSE",
    }


def _t_be_exit(p: dict) -> dict:
    price_label = "SL" if p.get("sl_price") is not None else "Price"
    price_value = p.get("sl_price") or p.get("exit_price") or p.get("fill_price")
    return {**p, "_emoji": "⚡", "exit_label": price_label, "exit_price": price_value}


# ---------------------------------------------------------------------------
# Signal templates (SIGNAL_ACCEPTED, SIGNAL_REJECTED, REVIEW_REQUIRED)
# ---------------------------------------------------------------------------

_SIGNAL_NOTES_BLOCKS: list = [
    SeparatorBlock(),
    StaticBlock("Notes:"),
    ListBlock(key="_signal_notes", item_renderer=lambda note, i, p: [note]),
]

_SIGNAL_BASE_BLOCKS: list = [
    HeaderBlock(emoji=lambda p: p["_emoji"], event_label=lambda p: p["_event_label"]),
    *_SIGNAL_BODY,
    FieldBlock("Leverage", key="leverage", fmt=lambda v: f"x{v}"),
    ConditionalBlock(
        condition=lambda p: bool(p.get("_signal_notes")),
        blocks=_SIGNAL_NOTES_BLOCKS,
    ),
    ConditionalBlock(
        condition=lambda p: p.get("parse_status") == "PARTIAL",
        blocks=[
            DerivedBlock(text_fn=lambda p:
                f"Parser: PARTIAL ({', '.join(p.get('parse_warnings') or []) or 'incomplete parse'})"
            ),
        ]
    ),
    FooterBlock(default_source="trader_signal",
                include_trader_id=True, include_account_id=True, include_rejected_reason=True),
]

_REVIEW_REQUIRED_BLOCKS: list = [
    HeaderBlock(emoji="⚠️", event_label="REVIEW REQUIRED"),
    *_SIGNAL_BODY,
    ConditionalBlock(
        condition=lambda p: bool(p.get("_signal_notes")),
        blocks=_SIGNAL_NOTES_BLOCKS,
    ),
    FooterBlock(default_source="runtime",
                include_trader_id=True, include_account_id=True, include_rejected_reason=True),
]


def _t_signal_accepted(p: dict) -> dict:
    return {**p, "_emoji": "✅", "_event_label": "SIGNAL ACCEPTED",
            "_entry_pcts": p.get("_entry_pcts", []),
            "_tp_pcts":    p.get("_tp_pcts", []),
            "_signal_notes": _build_signal_notes(p)}


def _t_signal_rejected(p: dict) -> dict:
    return {**p, "_emoji": "❌", "_event_label": "SIGNAL REJECTED",
            "_entry_pcts": p.get("_entry_pcts", []),
            "_tp_pcts":    p.get("_tp_pcts", []),
            "_signal_notes": _build_signal_notes(p)}


def _t_review_required(p: dict) -> dict:
    return {**p, "_signal_notes": _build_signal_notes(p)}
