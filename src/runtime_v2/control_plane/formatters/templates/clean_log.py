# src/runtime_v2/control_plane/formatters/templates/clean_log.py
from __future__ import annotations

from src.runtime_v2.control_plane.formatters._blocks import (
    _SEP, _BULLET,
    SeparatorBlock, StaticBlock, DerivedBlock, HeaderBlock,
    FieldBlock, ConditionalBlock, BranchBlock, ListBlock, FooterBlock,
    TemplateConfig,
)
from src.runtime_v2.control_plane.formatters._formatters import (
    num, text, money, money_signed, pct, pct_signed, fee_rate, price, r_mult,
)
from src.runtime_v2.control_plane.formatters.display import display_symbol


# ---------------------------------------------------------------------------
# Shared item renderers (used as item_renderer in ListBlock)
# ---------------------------------------------------------------------------

def _entry_label(seq: object, total: int | None) -> str:
    """Numbering rule: Entry_N only when the signal has more than one entry.
    total=None (count unknown) keeps the numbered label for backward compat."""
    return "Entry" if total == 1 else f"Entry_{seq}"


def _tp_label(level: object, total: int | None) -> str:
    """Numbering rule: TP_N only when the signal has more than one TP."""
    return "TP" if total == 1 else f"TP_{level}"


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
    label = _entry_label(seq, len(p.get("entries") or []))
    return [f"{label}: {price_str}{pct_suffix}"]


def _render_tp_item(tp: object, i: int, p: dict) -> list[str]:
    pcts = p.get("_tp_pcts") or []
    pct_suffix = f" ({pcts[i - 1]}%)" if len(pcts) >= 2 and i <= len(pcts) else ""
    label = _tp_label(i, len(p.get("tps") or []))
    return [f"{label}: {num(tp)}{pct_suffix}"]


def _render_pending_entry(entry: dict, i: int, p: dict) -> list[str]:
    seq = entry.get("sequence", "?")
    px = entry.get("price")
    etype = entry.get("entry_type", "LIMIT").capitalize()
    price_str = price(px) if px is not None else "?"
    label = _entry_label(seq, p.get("_total_legs"))
    return [f"Pending: {label} {price_str} {etype}"]


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
    FieldBlock("R",             value_fn=lambda p: (p.get("final_result") or {}).get("r_multiple"),
               fmt=r_mult,       optional=False, default="n/a"),
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
        _entry_label(p["filled_leg_sequence"], p.get("_total_legs", 1))
        + f": {price(p.get('fill_price'))} {p.get('entry_type_for_leg', 'Limit').capitalize()}"
        if p.get("filled_leg_sequence") is not None else ""
    )),
    BranchBlock(
        condition=lambda p: bool(p.get("is_partial_leg")),
        then_blocks=[
            DerivedBlock(text_fn=lambda p:
                f"Qty: {num(p.get('filled_qty', 0))} (planned: {num(p.get('planned_qty', 0))})"
            ),
        ],
        else_blocks=[FieldBlock("Qty", key="filled_qty", fmt=num)],
    ),
    FieldBlock("Value",    key="exec_value", fmt=money),
    FieldBlock("Fee rate", key="fee_rate",   fmt=fee_rate, optional=False, default="n/a"),
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
    FieldBlock("Avg entry",   key="_avg_entry",          fmt=price),
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
    if p.get("leverage_hint_applied"):
        notes.append("Leverage - Overridden by trader")
    trim = p.get("tp_trimmed") or {}
    if trim.get("original") is not None and trim.get("used") is not None:
        notes.append(f"TP - Reduced by policy ({trim['original']} → {trim['used']})")
    realigned = p.get("entry_sequence_realigned") or {}
    if realigned.get("original") and realigned.get("normalized"):
        notes.append(f"Entry - Reordered by side ({realigned.get('side')})")

    reshaped = p.get("reshaped") or {}
    if reshaped.get("rule_id"):
        notes.append(f"Setup - Reshaped by rule '{reshaped['rule_id']}'")

    reshape_rejected = p.get("reshape_rejected") or {}
    rr_phase = reshape_rejected.get("phase")
    rr_id = reshape_rejected.get("rule_id")
    if rr_id and rr_phase == "no_match":
        notes.append(f"Setup - Reshape rule '{rr_id}' did not match")
    elif rr_id and rr_phase == "invalid_output":
        notes.append(f"Setup - Reshape failed by rule '{rr_id}'")

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
        "exit_label": _tp_label(level, p.get("_total_tps")) if level is not None else "TP",
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
    sl = p.get("sl_price")
    price_value = sl if sl is not None else (p.get("exit_price") if p.get("exit_price") is not None else p.get("fill_price"))
    return {**p, "_emoji": "⚡", "exit_label": price_label, "exit_price": price_value}


def _t_liquidation_closed(p: dict) -> dict:
    return {
        **p,
        "_emoji": "💀",
        "exit_label": "Price",
        "exit_price": p.get("fill_price"),
        "close_reason": "LIQUIDATION",
    }


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


# ---------------------------------------------------------------------------
# Entry lifecycle (ENTRY_OPENED, ENTRY_UPDATED, ENTRY_CANCELLED)
# ---------------------------------------------------------------------------

_ENTRY_BLOCKS: list = [
    HeaderBlock(emoji=lambda p: p["_emoji"], event_label=lambda p: p["_event_label"]),
    *_FILL_SECTION,
    *_ENTRY_POSITION_SECTION,
    ConditionalBlock(
        condition=lambda p: bool(p.get("is_partial_leg")),
        blocks=[
            SeparatorBlock(),
            StaticBlock("Changed:"),
            DerivedBlock(text_fn=lambda p:
                f"SL qty: {num(p.get('planned_qty'))} → {num(p.get('filled_qty'))} (adj. to fill)"
            ),
        ]
    ),
    FooterBlock(default_source="exchange"),
]


def _t_entry_opened(p: dict) -> dict:
    return {**p, "_emoji": "📊", "_event_label": "ENTRY OPENED",
            "_avg_entry": p.get("avg_entry")}


def _t_entry_updated(p: dict) -> dict:
    avg = p["new_avg_entry"] if "new_avg_entry" in p else p.get("avg_entry")
    return {**p, "_emoji": "✏️", "_event_label": "ENTRY UPDATED", "_avg_entry": avg}


_ENTRY_CANCELLED_BLOCKS: list = [
    HeaderBlock(emoji="⚠️", event_label="ENTRY CANCELLED"),
    DerivedBlock(text_fn=lambda p:
        f"{_entry_label(p['_c_seq'], p.get('_total_legs'))}: {num(p['_c_price'])} {p['_c_etype']}"
        if p.get("_c_price") is not None
        else f"{_entry_label(p['_c_seq'], p.get('_total_legs'))}: {p['_c_etype']}"
    ),
    ConditionalBlock(
        condition=lambda p: p.get("partial_fill_pct") is not None,
        blocks=[
            DerivedBlock(text_fn=lambda p:
                f"Partial fill: {pct(p['partial_fill_pct'])}"
                + (f" ({num(p['partial_fill_qty'])} {p['_base_asset']} kept)"
                   if p.get("partial_fill_qty") is not None else "")
            ),
        ]
    ),
    FieldBlock("Avg entry",    key="avg_entry",       fmt=num),
    ConditionalBlock(
        condition=lambda p: p.get("total_filled_qty") is not None,
        blocks=[
            DerivedBlock(text_fn=lambda p: (
                f"Total filled: {num(p['total_filled_qty'])} {p['_base_asset']}".rstrip()
            )),
        ]
    ),
    FooterBlock(default_source="runtime"),
]


def _t_entry_cancelled(p: dict) -> dict:
    cancelled = p.get("cancelled_entry") or {}
    symbol = display_symbol(p.get("symbol", ""))
    base_asset = symbol.split("/")[0] if "/" in symbol else symbol
    return {
        **p,
        "_c_seq":      cancelled.get("sequence", "?"),
        "_c_price":    cancelled.get("price"),
        "_c_etype":    cancelled.get("entry_type", "LIMIT").capitalize(),
        "_base_asset": base_asset,
    }


# ---------------------------------------------------------------------------
# Partial close (TP_FILLED, PARTIAL_CLOSE_EXECUTED)
# ---------------------------------------------------------------------------

_PARTIAL_RESULT_BLOCKS: list = [
    HeaderBlock(emoji=lambda p: p["_emoji"], event_label=lambda p: p["_event_label"]),
    DerivedBlock(text_fn=lambda p:
        f"{p['_price_label']}: {num(p['_price_value']) if p.get('_price_value') is not None else '-'}"
    ),
    FieldBlock("Closed",   key="closed_pct",  fmt=pct),
    FieldBlock("Qty",      key="closed_qty",  fmt=num),
    FieldBlock("PnL",      key="pnl",         fmt=money_signed),
    FieldBlock("Fee rate", key="fee_rate",    fmt=fee_rate,  optional=False, default="n/a"),
    FieldBlock("Fee",      key="fee",         fmt=money),
    ConditionalBlock(
        condition=lambda p: p.get("_show_value"),
        blocks=[FieldBlock("Value", key="exec_value", fmt=money, optional=False, default="n/a")],
    ),
    SeparatorBlock(),
    StaticBlock("Remaining:"),
    FieldBlock("Qty",       key="remaining_qty",  fmt=num),
    FieldBlock("Avg entry", key="avg_entry",      fmt=num),
    FieldBlock("Risk",      key="remaining_risk", fmt=money),
    FooterBlock(default_source="exchange"),
]


def _t_tp_partial(p: dict) -> dict:
    level = p.get("tp_level")
    display_price = p.get("fill_price") if p.get("fill_price") is not None else p.get("tp_price")
    single_tp = p.get("_total_tps") == 1
    return {
        **p,
        "_emoji":       "📊",
        "_event_label": f"TP{level} FILLED" if level is not None and not single_tp else "TP FILLED",
        "_price_label": _tp_label(level, p.get("_total_tps")) if level is not None else "TP",
        "_price_value": display_price,
        "_show_value":  True,
    }


def _t_partial_close(p: dict) -> dict:
    return {
        **p,
        "_emoji":       "✅",
        "_event_label": "PARTIAL CLOSED",
        "_price_label": "Price",
        "_price_value": p.get("fill_price"),
        "_show_value":  False,
    }


# ---------------------------------------------------------------------------
# Update lifecycle (UPDATE_DONE, UPDATE_PARTIAL, UPDATE_REJECTED)
# ---------------------------------------------------------------------------

_UPDATE_BLOCKS: list = [
    HeaderBlock(emoji=lambda p: p["_emoji"], event_label=lambda p: p["_event_label"]),
    ConditionalBlock(
        condition=lambda p: bool(p.get("_operations")),
        blocks=[
            StaticBlock("Operation:"),
            ListBlock(key="_operations", item_renderer=lambda op, i, p: [f"{_BULLET} {op}"]),
        ]
    ),
    ConditionalBlock(
        condition=lambda p: bool(p.get("changed")),
        blocks=[
            StaticBlock("Changed:"),
            ListBlock(key="changed", item_renderer=_render_changed_item),
        ]
    ),
    ConditionalBlock(
        condition=lambda p: bool(p.get("_footnotes")),
        blocks=[
            SeparatorBlock(),
            ListBlock(key="_footnotes", item_renderer=lambda note, i, p: [f"* {note}"]),
        ]
    ),
    ConditionalBlock(
        condition=lambda p: p.get("_failed_reason") is not None,
        blocks=[
            SeparatorBlock(),
            DerivedBlock(text_fn=lambda p: f"Failed: {p['_failed_reason']}"),
        ]
    ),
    FooterBlock(default_source="runtime"),
]


def _t_update_done(p: dict) -> dict:
    ops = p.get("applied_actions") or []
    changed = p.get("changed") or []
    # backward compat: display_lines converted to plain changed items (bullet-prefixed)
    if not changed and p.get("display_lines"):
        changed = list(p["display_lines"])
    footnotes = [item["note"] for item in changed if isinstance(item, dict) and item.get("note")]
    return {**p, "_emoji": "✅", "_event_label": "UPDATE DONE",
            "_operations": ops, "_failed_reason": None,
            "_footnotes": footnotes or None,
            "changed": changed}


def _t_update_partial(p: dict) -> dict:
    applied     = p.get("applied_actions") or []
    failed_list = p.get("failed_actions") or []   # [{"action": str, "reason": str}]
    failed_set  = {f.get("action", "") for f in failed_list}
    all_ops     = applied + [f.get("action", "") for f in failed_list]
    ops_display = [f"{op} *" if op in failed_set else op for op in all_ops]
    changed     = p.get("changed") or []
    fn_changed  = [item["note"] for item in changed if isinstance(item, dict) and item.get("note")]
    fn_failed   = [f"Failed: {f.get('reason', '')}" for f in failed_list]
    footnotes   = fn_changed + fn_failed
    return {**p, "_emoji": "⚠️", "_event_label": "UPDATE PARTIAL",
            "_operations": ops_display, "_failed_reason": None,
            "_footnotes": footnotes or None}


def _t_update_rejected(p: dict) -> dict:
    ops     = p.get("rejected_actions") or []
    reason  = p.get("reason") or p.get("failed_reason")
    changed = p.get("changed") or []
    footnotes = [item["note"] for item in changed if isinstance(item, dict) and item.get("note")]
    return {**p, "_emoji": "❌", "_event_label": "UPDATE REJECTED",
            "_operations": ops, "_failed_reason": reason,
            "_footnotes": footnotes or None}


# ---------------------------------------------------------------------------
# Stop moved (STOP_MOVED)
# ---------------------------------------------------------------------------

_STOP_MOVED_BLOCKS: list = [
    HeaderBlock(emoji=lambda p: p["_emoji"], event_label="STOP MOVED"),
    FieldBlock("New SL", key="new_stop_price", fmt=num, optional=False, default="n/a"),
    ConditionalBlock(
        condition=lambda p: bool(p.get("is_breakeven")),
        blocks=[StaticBlock("Breakeven protection active")],
    ),
    FooterBlock(default_source="exchange"),
]


def _t_stop_moved(p: dict) -> dict:
    return {**p, "_emoji": "🛡️" if p.get("is_breakeven") else "📍"}


# ---------------------------------------------------------------------------
# Simple notifications
# ---------------------------------------------------------------------------

_PENDING_TIMEOUT_BLOCKS: list = [
    HeaderBlock(emoji="⏰", event_label="PENDING ENTRY EXPIRED"),
    StaticBlock("Timeout: order expired before fill"),
    FooterBlock(default_source="timeout_worker"),
]

_REENTRY_BLOCKS: list = [
    HeaderBlock(emoji="🔄", event_label="REENTRY ACCEPTED"),
    FieldBlock("Previous chain",
               value_fn=lambda p: f"#{p['previous_chain_id']}" if p.get("previous_chain_id") is not None else None,
               fmt=text),
    FooterBlock(default_source="runtime"),
]

_CANCEL_FAILED_BLOCKS: list = [
    HeaderBlock(emoji="🚨", event_label="CANCEL FAILED"),
    DerivedBlock(text_fn=lambda p:
        f"Cancellation of {p.get('entry_ref', 'entry')} failed after {p.get('attempts', 3)} attempts."
    ),
    StaticBlock("Requires manual review to resolve the position."),
    FieldBlock("Entry price", key="entry_price", fmt=num),
    FooterBlock(default_source="timeout_worker"),
]

_RECONCILIATION_WARN_BLOCKS: list = [
    HeaderBlock(emoji="⚠️", event_label="RECONCILIATION WARNING"),
    FieldBlock("Issue",  key="issue",  fmt=text),
    FieldBlock("Risk",   key="risk",   fmt=text),
    FieldBlock("Action", key="action", fmt=text),
    FooterBlock(default_source="runtime"),
]

_RECONCILIATION_FIXED_BLOCKS: list = [
    HeaderBlock(emoji="✅", event_label="RECONCILIATION FIXED"),
    FieldBlock("Issue resolved", key="issue", fmt=text),
    FooterBlock(default_source="runtime"),
]


# ---------------------------------------------------------------------------
# Update not applied — no chain found (UPDATE_NOT_APPLIED)
# ---------------------------------------------------------------------------

_UPDATE_NOT_APPLIED_BLOCKS: list = [
    HeaderBlock(emoji="⚠️", event_label="UPDATE NOT APPLIED"),
    FieldBlock("Reason",  key="reason",      fmt=text, optional=False, default="unknown"),
    FieldBlock("Action",  key="action_hint", fmt=text, optional=True),
    FooterBlock(default_source="trader_update"),
]


# ---------------------------------------------------------------------------
# Multi-chain (MULTI_CHAIN_SUMMARY, MULTI_CHAIN_UPDATE, MULTI_CHAIN_CLOSED)
# ---------------------------------------------------------------------------

def _render_chain_item(chain: dict, i: int, p: dict) -> list[str]:
    chain_id = chain.get("chain_id", "?")
    symbol = display_symbol(chain.get("symbol", "?"))
    side = chain.get("side", "?")
    status = chain.get("status", "DONE")
    lines = [f"#{chain_id} {symbol} {side} — {status}"]
    if chain.get("link"):
        lines.append(chain["link"])
    if p.get("summary_kind") != "final_close":
        for item in chain.get("display_lines") or []:
            lines.append(item)
    lines.append(_SEP)
    return lines


def _fmt_counts(p: dict) -> str:
    counts = p.get("_counts", {})
    summary_kind = p.get("summary_kind", "immediate")
    done    = counts.get("done", 0)
    partial = counts.get("partial", 0)
    skipped = counts.get("skipped", 0)
    review  = counts.get("review", 0)
    error   = counts.get("error", 0)
    if summary_kind == "final_close":
        parts = [f"Done: {done}"]
        if partial: parts.append(f"Partial: {partial}")
        if review:  parts.append(f"Review: {review}")
        parts.append(f"Skipped: {skipped}")
        parts.append(f"Error: {error}")
    else:
        parts = [f"Done: {done}", f"Partial: {partial}", f"Skipped: {skipped}"]
        if review: parts.append(f"Review: {review}")
        parts.append(f"Error: {error}")
    return " | ".join(parts)


_MULTI_CHAIN_BLOCKS: list = [
    DerivedBlock(text_fn=lambda p:
        ("⚠️" if p["_has_issues"] else "✅")
        + f" UPDATE APPLICATO — {len(p.get('chains') or [])} chain"
    ),
    SeparatorBlock(),
    BranchBlock(
        condition=lambda p: p.get("summary_kind") == "final_close",
        then_blocks=[StaticBlock("Operation requested:")],
        else_blocks=[StaticBlock("Operations requested:")],
    ),
    ListBlock(key="requested_operations", fallback_key="operations",
              item_renderer=lambda item, i, p: [f"{_BULLET} {item}"]),
    SeparatorBlock(),
    ListBlock(key="chains", item_renderer=_render_chain_item),
    DerivedBlock(text_fn=_fmt_counts),
    FooterBlock(),
]


def _t_multi_chain(p: dict) -> dict:
    chains = p.get("chains") or []
    has_issues = any(
        chain.get("status") in {"PARTIAL", "SKIPPED", "REVIEW", "ERROR"}
        for chain in chains
    )
    counts = p.get("counts") or {
        "done":    sum(1 for c in chains if c.get("status") == "DONE"),
        "partial": sum(1 for c in chains if c.get("status") == "PARTIAL"),
        "skipped": sum(1 for c in chains if c.get("status") == "SKIPPED"),
        "review":  sum(1 for c in chains if c.get("status") == "REVIEW"),
        "error":   sum(1 for c in chains if c.get("status") == "ERROR"),
    }
    return {**p, "_has_issues": has_issues, "_counts": counts}


# ---------------------------------------------------------------------------
# TEMPLATE_REGISTRY
# ---------------------------------------------------------------------------

TEMPLATE_REGISTRY: dict[str, TemplateConfig] = {
    "SIGNAL_ACCEPTED":        TemplateConfig(_SIGNAL_BASE_BLOCKS,       _t_signal_accepted),
    "SIGNAL_REJECTED":        TemplateConfig(_SIGNAL_BASE_BLOCKS,       _t_signal_rejected),
    "REVIEW_REQUIRED":        TemplateConfig(_REVIEW_REQUIRED_BLOCKS,   _t_review_required),
    "ENTRY_OPENED":           TemplateConfig(_ENTRY_BLOCKS,             _t_entry_opened),
    "ENTRY_UPDATED":          TemplateConfig(_ENTRY_BLOCKS,             _t_entry_updated),
    "ENTRY_CANCELLED":        TemplateConfig(_ENTRY_CANCELLED_BLOCKS,   _t_entry_cancelled),
    "SL_FILLED":              TemplateConfig(_CLOSED_BLOCKS,            _t_sl_filled),
    "TP_FILLED_FINAL":        TemplateConfig(_CLOSED_BLOCKS,            _t_tp_final),
    "POSITION_CLOSED":        TemplateConfig(_CLOSED_BLOCKS,            _t_position_closed),
    "BE_EXIT":                TemplateConfig(_CLOSED_BLOCKS,            _t_be_exit),
    "LIQUIDATION_CLOSED":     TemplateConfig(_CLOSED_BLOCKS,            _t_liquidation_closed),
    "STOP_MOVED":             TemplateConfig(_STOP_MOVED_BLOCKS,        _t_stop_moved),
    "TP_FILLED":              TemplateConfig(_PARTIAL_RESULT_BLOCKS,    _t_tp_partial),
    "UPDATE_DONE":            TemplateConfig(_UPDATE_BLOCKS,            _t_update_done),
    "UPDATE_PARTIAL":         TemplateConfig(_UPDATE_BLOCKS,            _t_update_partial),
    "UPDATE_REJECTED":        TemplateConfig(_UPDATE_BLOCKS,            _t_update_rejected),
    "PARTIAL_CLOSE_EXECUTED": TemplateConfig(_PARTIAL_RESULT_BLOCKS,   _t_partial_close),
    "PENDING_ENTRY_EXPIRED":  TemplateConfig(_PENDING_TIMEOUT_BLOCKS),
    "REENTRY_ACCEPTED":       TemplateConfig(_REENTRY_BLOCKS),
    "CANCEL_FAILED":          TemplateConfig(_CANCEL_FAILED_BLOCKS),
    "RECONCILIATION_WARNING": TemplateConfig(_RECONCILIATION_WARN_BLOCKS),
    "RECONCILIATION_FIXED":   TemplateConfig(_RECONCILIATION_FIXED_BLOCKS),
    "UPDATE_NOT_APPLIED":     TemplateConfig(_UPDATE_NOT_APPLIED_BLOCKS),
    "MULTI_CHAIN_SUMMARY":    TemplateConfig(_MULTI_CHAIN_BLOCKS,       _t_multi_chain),
    "MULTI_CHAIN_UPDATE":     TemplateConfig(_MULTI_CHAIN_BLOCKS,       _t_multi_chain),
    "MULTI_CHAIN_CLOSED":     TemplateConfig(_MULTI_CHAIN_BLOCKS,       _t_multi_chain),
}
