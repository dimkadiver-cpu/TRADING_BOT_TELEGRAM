from __future__ import annotations

_SEP = "__SEP__"
_BULLET = "\u25aa\ufe0f"


def _side_emoji(side: str | None) -> str:
    if side == "LONG":
        return "\U0001f4c8"
    if side == "SHORT":
        return "\U0001f4c9"
    return "\u2022"


def _num(value) -> str:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if f == int(f) and abs(f) < 1e15:
        return f"{int(f):,}"
    formatted = f"{f:.8g}"
    if "e" not in formatted and "." in formatted:
        int_part, dec_part = formatted.split(".")
        try:
            int_part = f"{int(int_part):,}"
        except ValueError:
            pass
        return f"{int_part}.{dec_part}"
    return formatted


def _separator(width: int) -> str:
    dash_count = max(4, (max(width, 1) + 1) // 2)
    return " ".join("-" for _ in range(dash_count))


def _finalize(lines: list[str]) -> str:
    width = max((len(line) for line in lines if line and line != _SEP), default=8)
    sep = _separator(width)
    return "\n".join(sep if line == _SEP else line for line in lines)


def _header(
    emoji: str, chain_id, event_label: str, symbol, side, *, signal_link: str | None = None
) -> list[str]:
    id_part = f" #{chain_id}" if chain_id is not None else ""
    lines: list[str] = [f"{emoji}{id_part} \u2014 {event_label}", _SEP]
    lines.append(f"{symbol} \u2014 {_side_emoji(side)} {side}")
    if signal_link:
        lines.append(signal_link)
    lines.append(_SEP)
    return lines


def _footer(
    source: str,
    link: str | None = None,
    trader_id: str | None = None,
    account_id: str | None = None,
    reason: str | None = None,
) -> list[str]:
    lines = [_SEP]
    if trader_id:
        lines.append(f"Trader: {trader_id}")
    if account_id:
        lines.append(f"Exchange Account: {account_id}")
    if reason:
        lines.append(f"Rejected: {reason}")
    lines.append(f"Source: {source}")
    if link:
        lines.extend([_SEP, link])
    return lines


def _fmt_money(value, *, signed: bool = False) -> str:
    if value is None:
        return "n/a"
    number = float(value)
    prefix = "+" if signed and number >= 0 else ""
    return f"{prefix}{number:.2f} USDT"


def _fmt_pct(value, *, signed: bool = False) -> str:
    if value is None:
        return "n/a"
    number = float(value)
    prefix = "+" if signed and number >= 0 else ""
    return f"{prefix}{number:.2f}%".replace(".00%", "%")


def _final_result_lines(final_result: dict | None) -> list[str]:
    if not final_result:
        return []
    lines = [_SEP, "Final Result:"]
    if final_result.get("roi_net_pct") is not None:
        lines.append(f"ROI net: {_fmt_pct(final_result['roi_net_pct'], signed=True)}")
    lines.append(f"Total PnL net: {_fmt_money(final_result.get('total_pnl_net'), signed=True)}")
    lines.append(f"Gross PnL: {_fmt_money(final_result.get('gross_pnl'), signed=True)}")
    lines.append(f"Fees: {_fmt_money(final_result.get('fees'), signed=True)}")
    lines.append(f"Funding: {_fmt_money(final_result.get('funding'), signed=True)}")
    return lines


def _signal_accepted(p: dict) -> str:
    lines = _header("\u2705", p.get("chain_id"), "SIGNAL ACCEPTED", p.get("symbol"), p.get("side"))
    for entry in p.get("entries") or []:
        seq = entry.get("sequence", 1)
        etype = entry.get("entry_type", "LIMIT")
        price = entry.get("price")
        if etype == "MARKET":
            price_str = f"Market ~{_num(price)}" if price is not None else "Market"
        else:
            price_str = f"{_num(price)} Limit" if price is not None else "Limit"
        lines.append(f"Entry_{seq}: {price_str}")
    if p.get("sl") is not None:
        lines.append(f"SL: {_num(p['sl'])}")
    for i, tp in enumerate(p.get("tps") or [], start=1):
        lines.append(f"TP_{i}: {_num(tp)}")
    if p.get("risk_pct") is not None:
        lines.append(f"Risk: {p['risk_pct']}%")
    if p.get("parse_status") == "PARTIAL":
        warnings = p.get("parse_warnings") or []
        warn_str = ", ".join(warnings) if warnings else "incomplete parse"
        lines.append(f"⚠️ Parser: PARTIAL ({warn_str})")
    lines += _footer(p.get("source", "trader_signal"), p.get("link"), trader_id=p.get("trader_id"), account_id=p.get("account_id"))
    return _finalize(lines)


def _signal_rejected(p: dict) -> str:
    lines = _header("\u274c", p.get("chain_id"), "SIGNAL REJECTED", p.get("symbol"), p.get("side"))
    for entry in p.get("entries") or []:
        seq = entry.get("sequence", 1)
        etype = entry.get("entry_type", "LIMIT")
        price = entry.get("price")
        if etype == "MARKET":
            price_str = f"Market ~{_num(price)}" if price is not None else "Market"
        else:
            price_str = f"{_num(price)} Limit" if price is not None else "Limit"
        lines.append(f"Entry_{seq}: {price_str}")
    if p.get("sl") is not None:
        lines.append(f"SL: {_num(p['sl'])}")
    for i, tp in enumerate(p.get("tps") or [], start=1):
        lines.append(f"TP_{i}: {_num(tp)}")
    if p.get("risk_pct") is not None:
        lines.append(f"Risk: {p['risk_pct']}%")
    lines += _footer(
        p.get("source", "trader_signal"),
        p.get("link"),
        trader_id=p.get("trader_id"),
        account_id=p.get("account_id"),
        reason=p.get("reason"),
    )
    return _finalize(lines)


def _review_required(p: dict) -> str:
    lines = _header("\u26a0\ufe0f", p.get("chain_id"), "REVIEW REQUIRED", p.get("symbol"), p.get("side"))
    for entry in p.get("entries") or []:
        seq = entry.get("sequence", 1)
        etype = entry.get("entry_type", "LIMIT")
        price = entry.get("price")
        if etype == "MARKET":
            price_str = f"Market ~{_num(price)}" if price is not None else "Market"
        else:
            price_str = f"{_num(price)} Limit" if price is not None else "Limit"
        lines.append(f"Entry_{seq}: {price_str}")
    if p.get("sl") is not None:
        lines.append(f"SL: {_num(p['sl'])}")
    for i, tp in enumerate(p.get("tps") or [], start=1):
        lines.append(f"TP_{i}: {_num(tp)}")
    if p.get("risk_pct") is not None:
        lines.append(f"Risk: {p['risk_pct']}%")
    lines += _footer(
        p.get("source", "runtime"),
        p.get("link"),
        trader_id=p.get("trader_id"),
        account_id=p.get("account_id"),
        reason=p.get("reason"),
    )
    return _finalize(lines)


def _entry_opened(p: dict) -> str:
    lines = _header("\U0001f4ca", p.get("chain_id"), "ENTRY OPENED", p.get("symbol"), p.get("side"), signal_link=p.get("signal_link"))
    if p.get("fill_price") is not None or p.get("filled_qty") is not None:
        seq = p.get("filled_leg_sequence")
        lines.append(f"Entry_{seq} - Filled" if seq is not None else "Filled:")
        if p.get("fill_price") is not None:
            lines.append(f"Price: {_num(p['fill_price'])}")
        if p.get("filled_qty") is not None:
            lines.append(f"Qty: {_num(p['filled_qty'])}")
        if p.get("fee") is not None:
            lines.append(f"Fee: {_fmt_money(p['fee'])}")
        if "fee_rate" in p:
            fee_rate = p.get("fee_rate")
            lines.append(f"Fee rate: {float(fee_rate) * 100:.3f}%" if fee_rate is not None else "Fee rate: n/a")
        if "exec_value" in p:
            lines.append(f"Value: {_fmt_money(p.get('exec_value'))}")
        lines.append("")
    if p.get("avg_entry") is not None:
        lines.append("Position:")
        lines.append(f"Avg entry: {_num(p['avg_entry'])}")
        pending = p.get("pending_entries") or []
        if pending:
            for entry in pending:
                seq = entry.get("sequence", "?")
                price = entry.get("price")
                etype = entry.get("entry_type", "LIMIT")
                price_str = _num(price) if price is not None else "?"
                lines.append(f"Pending: Entry_{seq} {price_str} {etype.capitalize()}")
        else:
            lines.append("Pending: none")
        lines += _footer(p.get("source", "exchange"))
    return _finalize(lines)


def _tp_filled(p: dict, final: bool) -> str:
    level = p.get("tp_level")
    if final:
        label = "POSITION CLOSED"
        emoji = "\u2705"
    else:
        label = f"TP{level} FILLED" if level is not None else "TP FILLED"
        emoji = "\U0001f4ca"
    lines = _header(emoji, p.get("chain_id"), label, p.get("symbol"), p.get("side"), signal_link=p.get("signal_link"))
    if level is not None:
        tp_label = f"TP_{level}"
        display_price = p.get("fill_price") if p.get("fill_price") is not None else p.get("tp_price")
        lines.append(f"{tp_label}: {_num(display_price) if display_price is not None else '-'}")
    if p.get("closed_pct") is not None:
        lines.append(f"Closed: {_fmt_pct(p['closed_pct'])}")
    if p.get("pnl") is not None:
        lines.append(f"PnL: {_fmt_money(p['pnl'], signed=True)}")
    if p.get("fee") is not None:
        lines.append(f"Fee: {_fmt_money(p['fee'])}")
    if "fee_rate" in p:
        fee_rate = p.get("fee_rate")
        lines.append(f"Fee rate: {float(fee_rate) * 100:.3f}%" if fee_rate is not None else "Fee rate: n/a")
    if "exec_value" in p:
        lines.append(f"Value: {_fmt_money(p.get('exec_value'))}")
    lines.append("")
    if final:
        lines.append("Close reason: FINAL TP FILLED")
    lines += _final_result_lines(p.get("final_result"))
    lines += _footer(p.get("source", "exchange"))
    return _finalize(lines)


def _sl_filled(p: dict) -> str:
    lines = _header("\U0001f6d1", p.get("chain_id"), "POSITION CLOSED", p.get("symbol"), p.get("side"), signal_link=p.get("signal_link"))
    sl_price = p.get("sl_price", p.get("fill_price"))
    if sl_price is not None:
        lines.append(f"SL: {_num(sl_price)}")
    if p.get("closed_pct") is not None:
        lines.append(f"Closed: {_fmt_pct(p['closed_pct'])}")
    if p.get("pnl") is not None:
        lines.append(f"PnL: {_fmt_money(p['pnl'], signed=True)}")
    if p.get("fee") is not None:
        lines.append(f"Fee: {_fmt_money(p['fee'])}")
    lines.append("")
    lines.append("Close reason: STOP_LOSS")
    lines += _final_result_lines(p.get("final_result"))
    lines += _footer(p.get("source", "exchange"))
    return _finalize(lines)


def _position_closed(p: dict) -> str:
    lines = _header("✋", p.get("chain_id"), "POSITION CLOSED", p.get("symbol"), p.get("side"), signal_link=p.get("signal_link"))
    if p.get("fill_price") is not None:
        lines.append(f"Price: {_num(p['fill_price'])}")
    if p.get("pnl") is not None:
        lines.append(f"PnL: {_fmt_money(p['pnl'], signed=True)}")
    if p.get("fee") is not None:
        lines.append(f"Fee: {_fmt_money(p['fee'])}")
    lines.append("")
    lines.append(f"Close reason: {p.get('close_reason', 'MANUAL_CLOSE')}")
    lines += _final_result_lines(p.get("final_result"))
    lines += _footer(p.get("source", "exchange"))
    return _finalize(lines)


def _entry_updated(p: dict) -> str:
    lines = _header("\u270f\ufe0f", p.get("chain_id"), "ENTRY UPDATED", p.get("symbol"), p.get("side"), signal_link=p.get("signal_link"))
    if p.get("fill_price") is not None or p.get("filled_qty") is not None:
        seq = p.get("filled_leg_sequence")
        lines.append(f"Entry_{seq} - Filled" if seq is not None else "Filled:")
        if p.get("fill_price") is not None:
            lines.append(f"Price: {_num(p['fill_price'])}")
        if p.get("filled_qty") is not None:
            lines.append(f"Qty: {_num(p['filled_qty'])}")
        if p.get("fee") is not None:
            lines.append(f"Fee: {_fmt_money(p['fee'])}")
        if "fee_rate" in p:
            fee_rate = p.get("fee_rate")
            lines.append(f"Fee rate: {float(fee_rate) * 100:.3f}%" if fee_rate is not None else "Fee rate: n/a")
        if "exec_value" in p:
            lines.append(f"Value: {_fmt_money(p.get('exec_value'))}")
        lines.append("")
    avg_entry = p.get("new_avg_entry", p.get("avg_entry"))
    pending = p.get("pending_entries") or []
    lines.append("Position:")
    if avg_entry is not None:
        lines.append(f"Avg entry: {_num(avg_entry)}")
    if pending:
        for entry in pending:
            seq = entry.get("sequence", "?")
            price = entry.get("price")
            etype = entry.get("entry_type", "LIMIT")
            price_str = _num(price) if price is not None else "?"
            lines.append(f"Pending: Entry_{seq} {price_str} {etype.capitalize()}")
    else:
        lines.append("Pending: none")
    lines += _footer(p.get("source", "exchange"), p.get("link"))
    return _finalize(lines)


def _update_done(p: dict) -> str:
    lines = _header("\u2705", p.get("chain_id"), "UPDATE DONE", p.get("symbol"), p.get("side"), signal_link=p.get("signal_link"))
    operations = p.get("operations") or p.get("applied_actions") or []
    if operations:
        lines.append("Operation:")
        for op in operations:
            lines.append(f"{_BULLET} {op}")
    changed = p.get("changed") or []
    if changed:
        lines.append("Changed:")
        for item in changed:
            if isinstance(item, dict):
                field = item.get("field", "?")
                value = f"{_num(item.get('old'))} -> {_num(item.get('new'))}"
                note = item.get("note")
                if note:
                    lines.append(f"{field}: {value} *")
                    lines.append(f"* {note}")
                else:
                    lines.append(f"{field}: {value}")
            else:
                lines.append(f"{_BULLET} {item}")
    changed_fields = p.get("changed_fields") or []
    if changed_fields and not changed:
        lines.append("Changed fields:")
        for field in changed_fields:
            lines.append(f"  \u2022 {field}")
    lines += _footer(p.get("source", "runtime"), p.get("link"))
    return _finalize(lines)


def _update_partial(p: dict) -> str:
    lines = _header("\u26a0\ufe0f", p.get("chain_id"), "UPDATE PARTIAL", p.get("symbol"), p.get("side"), signal_link=p.get("signal_link"))
    applied = p.get("applied_actions") or []
    if applied:
        lines.append("Applied:")
        for action in applied:
            lines.append(f"  \u2022 {action}")
    changed = p.get("changed") or []
    if changed:
        lines.append("Changed:")
        for item in changed:
            if isinstance(item, dict):
                field = item.get("field", "?")
                value = f"{_num(item.get('old'))} -> {_num(item.get('new'))}"
                note = item.get("note")
                if note:
                    lines.append(f"{field}: {value} *")
                    lines.append(f"* {note}")
                else:
                    lines.append(f"{field}: {value}")
            else:
                lines.append(f"{_BULLET} {item}")
    rejected = p.get("rejected_actions") or []
    if rejected:
        lines.append("Rejected:")
        for action in rejected:
            lines.append(f"  \u2022 {action}")
    lines += _footer(p.get("source", "runtime"), p.get("link"))
    return _finalize(lines)


def _update_rejected(p: dict) -> str:
    lines = _header("\u274c", p.get("chain_id"), "UPDATE REJECTED", p.get("symbol"), p.get("side"), signal_link=p.get("signal_link"))
    if p.get("reason") is not None:
        lines.append(f"Reason: {p['reason']}")
    rejected = p.get("rejected_actions") or []
    if rejected:
        lines.append("Rejected:")
        for action in rejected:
            lines.append(f"  \u2022 {action}")
    lines += _footer(p.get("source", "runtime"), p.get("link"))
    return _finalize(lines)


def _pending_timeout(p: dict) -> str:
    lines = _header("\u23f0", p.get("chain_id"), "PENDING ENTRY EXPIRED", p.get("symbol"), p.get("side"), signal_link=p.get("signal_link"))
    lines.append("Timeout: order expired before fill")
    lines += _footer(p.get("source", "timeout_worker"), p.get("link"))
    return _finalize(lines)


def _reconciliation_warning(p: dict) -> str:
    lines = _header("\u26a0\ufe0f", p.get("chain_id"), "RECONCILIATION WARNING", p.get("symbol"), p.get("side"), signal_link=p.get("signal_link"))
    if p.get("issue") is not None:
        lines.append(f"Issue: {p['issue']}")
    if p.get("risk") is not None:
        lines.append(f"Risk: {p['risk']}")
    if p.get("action") is not None:
        lines.append(f"Action: {p['action']}")
    lines += _footer(p.get("source", "runtime"), p.get("link"))
    return _finalize(lines)


def _reconciliation_fixed(p: dict) -> str:
    lines = _header("\u2705", p.get("chain_id"), "RECONCILIATION FIXED", p.get("symbol"), p.get("side"), signal_link=p.get("signal_link"))
    if p.get("issue") is not None:
        lines.append(f"Issue resolved: {p['issue']}")
    lines += _footer(p.get("source", "runtime"), p.get("link"))
    return _finalize(lines)


def _reentry_accepted(p: dict) -> str:
    lines = _header("\U0001f504", p.get("chain_id"), "REENTRY ACCEPTED", p.get("symbol"), p.get("side"), signal_link=p.get("signal_link"))
    if p.get("previous_chain_id") is not None:
        lines.append(f"Previous chain: #{p['previous_chain_id']}")
    lines += _footer(p.get("source", "runtime"), p.get("link"))
    return _finalize(lines)


def _entry_cancelled(p: dict) -> str:
    lines = _header("\u26a0\ufe0f", p.get("chain_id"), "ENTRY CANCELLED", p.get("symbol"), p.get("side"), signal_link=p.get("signal_link"))
    cancelled = p.get("cancelled_entry") or {}
    seq = cancelled.get("sequence", "?")
    price = cancelled.get("price")
    etype = cancelled.get("entry_type", "LIMIT").capitalize()
    lines.append(f"Entry_{seq}: {_num(price)} {etype}" if price is not None else f"Entry_{seq}: {etype}")
    if p.get("partial_fill_pct") is not None:
        qty = p.get("partial_fill_qty")
        symbol = p.get("symbol", "")
        base_asset = symbol.split("/")[0] if "/" in symbol else symbol
        qty_suffix = f" ({_num(qty)} {base_asset} kept)" if qty is not None else ""
        lines.append(f"Partial fill: {_fmt_pct(p['partial_fill_pct'])}{qty_suffix}")
    if p.get("avg_entry") is not None:
        lines.append(f"Avg entry: {_num(p['avg_entry'])}")
    if p.get("total_filled_qty") is not None:
        symbol = p.get("symbol", "")
        base_asset = symbol.split("/")[0] if "/" in symbol else symbol
        lines.append(f"Total filled: {_num(p['total_filled_qty'])} {base_asset}")
    lines += _footer(p.get("source", "runtime"), p.get("link"))
    return _finalize(lines)


def _be_exit(p: dict) -> str:
    lines = _header("\u26a1", p.get("chain_id"), "BE EXIT", p.get("symbol"), p.get("side"), signal_link=p.get("signal_link"))
    if p.get("exit_price") is not None:
        lines.append(f"Exit: {_num(p['exit_price'])} BE")
    if p.get("pnl") is not None:
        lines.append(f"PnL: {_fmt_money(p['pnl'], signed=True)}")
    if p.get("fee") is not None:
        lines.append(f"Fee: {_fmt_money(p['fee'])}")
    lines.append("")
    lines.append(f"Close reason: {p.get('close_reason', 'BREAKEVEN_AFTER_TP')}")
    lines.append("")
    lines += _final_result_lines(p.get("final_result"))
    if p.get("final_result"):
        lines += _footer(p.get("source", "exchange"))
    return _finalize(lines)


def _partial_close_executed(p: dict) -> str:
    lines = _header("✅", p.get("chain_id"), "UPDATE DONE", p.get("symbol"), p.get("side"), signal_link=p.get("signal_link"))
    lines.append("Executed:")
    lines.append(f"{_BULLET} CLOSE_PARTIAL")
    lines.append(_SEP)
    if p.get("fill_price") is not None:
        lines.append(f"Price: {_num(p['fill_price'])}")
    if p.get("closed_qty") is not None:
        lines.append(f"Qty: {_num(p['closed_qty'])}")
    if p.get("closed_pct") is not None:
        lines.append(f"Closed: {_fmt_pct(p['closed_pct'])}")
    if p.get("pnl") is not None:
        lines.append(f"PnL: {_fmt_money(p['pnl'], signed=True)}")
    if p.get("fee") is not None:
        lines.append(f"Fee: {_fmt_money(p['fee'])}")
    lines += _footer(p.get("source", "manual_command"))
    return _finalize(lines)


def _cancel_failed(p: dict) -> str:
    lines = _header("\U0001f6a8", p.get("chain_id"), "CANCEL FAILED", p.get("symbol"), p.get("side"), signal_link=p.get("signal_link"))
    entry_ref = p.get("entry_ref", "entry")
    attempts = p.get("attempts", 3)
    lines.append(f"Cancellation of {entry_ref} failed after {attempts} attempts.")
    lines.append("Requires manual review required to resolve the position.")
    if p.get("entry_price") is not None:
        lines.append(f"Entry price: {_num(p['entry_price'])}")
    lines += _footer(p.get("source", "timeout_worker"))
    return _finalize(lines)


def _multi_chain_summary(p: dict) -> str:
    requested = p.get("requested_operations") or p.get("operations") or []
    chains = p.get("chains") or []
    counts = p.get("counts") or {}
    summary_kind = p.get("summary_kind", "immediate")
    is_close_full = summary_kind == "final_close"

    has_issues = any(chain.get("status") in {"PARTIAL", "SKIPPED", "REVIEW", "ERROR"} for chain in chains)
    emoji = "⚠️" if has_issues else "✅"
    header_line = f"{emoji} UPDATE APPLICATO - {len(chains)} chain"

    lines = [header_line, _SEP]
    lines.append("Operation requested:" if is_close_full else "Operations requested:")
    for op in requested:
        lines.append(f"{_BULLET} {op}")
    lines.append(_SEP)
    for chain in chains:
        chain_id = chain.get("chain_id", "?")
        symbol = chain.get("symbol", "?")
        side = chain.get("side", "?")
        status = chain.get("status", "DONE")
        lines.append(f"#{chain_id} {symbol} {side} — {status}")
        if chain.get("link"):
            lines.append(chain["link"])
        if not is_close_full:
            for item in chain.get("display_lines") or []:
                lines.append(item)
        lines.append(_SEP)

    if summary_kind and not counts:
        counts = {
            "done": sum(1 for chain in chains if chain.get("status") == "DONE"),
            "partial": sum(1 for chain in chains if chain.get("status") == "PARTIAL"),
            "skipped": sum(1 for chain in chains if chain.get("status") == "SKIPPED"),
            "review": sum(1 for chain in chains if chain.get("status") == "REVIEW"),
            "error": sum(1 for chain in chains if chain.get("status") == "ERROR"),
        }

    done = counts.get("done", 0)
    partial = counts.get("partial", 0)
    skipped = counts.get("skipped", 0)
    review = counts.get("review", 0)
    error = counts.get("error", 0)
    if is_close_full:
        summary_parts = [f"Done: {done}"]
        if partial:
            summary_parts.append(f"Partial: {partial}")
        if review:
            summary_parts.append(f"Review: {review}")
        summary_parts.append(f"Skipped: {skipped}")
        summary_parts.append(f"Error: {error}")
        lines.append(" | ".join(summary_parts))
    else:
        summary_parts = [
            f"Done: {done}",
            f"Partial: {partial}",
            f"Skipped: {skipped}",
        ]
        if review:
            summary_parts.append(f"Review: {review}")
        summary_parts.append(f"Error: {error}")
        lines.append(" | ".join(summary_parts))
    lines += _footer(p.get("source", "runtime"), p.get("link"))
    return _finalize(lines)


def _fallback(notification_type: str, p: dict) -> str:
    lines = _header("\U0001f4ca", p.get("chain_id"), notification_type, p.get("symbol"), p.get("side"), signal_link=p.get("signal_link"))
    lines += _footer(p.get("source", "runtime"))
    return _finalize(lines)


def format_clean_log(notification_type: str, payload: dict) -> str:
    if notification_type == "SIGNAL_ACCEPTED":
        return _signal_accepted(payload)
    if notification_type == "SIGNAL_REJECTED":
        return _signal_rejected(payload)
    if notification_type == "REVIEW_REQUIRED":
        return _review_required(payload)
    if notification_type == "ENTRY_OPENED":
        return _entry_opened(payload)
    if notification_type == "TP_FILLED":
        return _tp_filled(payload, final=False)
    if notification_type == "TP_FILLED_FINAL":
        return _tp_filled(payload, final=True)
    if notification_type == "SL_FILLED":
        if payload.get("close_reason") == "BREAKEVEN_AFTER_TP":
            be_payload = {
                **payload,
                "exit_price": payload.get("sl_price", payload.get("fill_price")),
            }
            return _be_exit(be_payload)
        return _sl_filled(payload)
    if notification_type == "POSITION_CLOSED":
        return _position_closed(payload)
    if notification_type == "ENTRY_UPDATED":
        return _entry_updated(payload)
    if notification_type == "UPDATE_DONE":
        return _update_done(payload)
    if notification_type == "UPDATE_PARTIAL":
        return _update_partial(payload)
    if notification_type == "UPDATE_REJECTED":
        return _update_rejected(payload)
    if notification_type == "PENDING_ENTRY_EXPIRED":
        return _pending_timeout(payload)
    if notification_type == "RECONCILIATION_WARNING":
        return _reconciliation_warning(payload)
    if notification_type == "RECONCILIATION_FIXED":
        return _reconciliation_fixed(payload)
    if notification_type == "REENTRY_ACCEPTED":
        return _reentry_accepted(payload)
    if notification_type == "ENTRY_CANCELLED":
        return _entry_cancelled(payload)
    if notification_type == "BE_EXIT":
        return _be_exit(payload)
    if notification_type == "PARTIAL_CLOSE_EXECUTED":
        return _partial_close_executed(payload)
    if notification_type == "CANCEL_FAILED":
        return _cancel_failed(payload)
    if notification_type == "MULTI_CHAIN_SUMMARY":
        return _multi_chain_summary(payload)
    if notification_type in ("MULTI_CHAIN_UPDATE", "MULTI_CHAIN_CLOSED"):
        return _multi_chain_summary(payload)
    return _fallback(notification_type, payload)


__all__ = ["format_clean_log"]
