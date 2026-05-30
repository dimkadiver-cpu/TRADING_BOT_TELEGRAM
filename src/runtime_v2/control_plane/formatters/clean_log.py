# src/runtime_v2/control_plane/formatters/clean_log.py
from __future__ import annotations

_SEP = "────────────────"


def _side_emoji(side: str | None) -> str:
    if side == "LONG":
        return "📈"
    if side == "SHORT":
        return "📉"
    return "•"


def _num(value) -> str:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if f == int(f) and abs(f) < 1e15:
        return f"{int(f):,}"
    # Use up to 8 significant digits, strip trailing zeros.
    formatted = f"{f:.8g}"
    # Add thousands separator to integer part if large enough.
    if "e" not in formatted and "." in formatted:
        int_part, dec_part = formatted.split(".")
        try:
            int_part = f"{int(int_part):,}"
        except ValueError:
            pass
        return f"{int_part}.{dec_part}"
    return formatted


def _header(emoji: str, chain_id, event_label: str, symbol, side) -> list[str]:
    return [
        f"{emoji} #{chain_id} — {event_label}",
        _SEP,
        f"{symbol} — {_side_emoji(side)} {side}",
        "",
    ]


def _footer(source: str, link: str | None = None) -> list[str]:
    lines = [_SEP, f"Source: {source}"]
    if link:
        lines.append(link)
    return lines


def _signal_accepted(p: dict) -> str:
    lines = _header("✅", p.get("chain_id"), "SIGNAL ACCEPTED", p.get("symbol"), p.get("side"))

    # Entries
    for e in p.get("entries") or []:
        seq = e.get("sequence", 1)
        etype = e.get("entry_type", "LIMIT")
        price = e.get("price")
        if etype == "MARKET":
            price_str = f"Market ~{_num(price)}" if price is not None else "Market"
        else:
            price_str = f"{_num(price)} Limit" if price is not None else "Limit"
        lines.append(f"Entry_{seq}: {price_str}")

    # SL
    if p.get("sl") is not None:
        lines.append(f"SL: {_num(p['sl'])}")

    # TPs
    for i, tp in enumerate(p.get("tps") or [], start=1):
        lines.append(f"TP_{i}: {_num(tp)}")

    # Risk
    if p.get("risk_pct") is not None:
        lines.append(f"Risk: {p['risk_pct']}%")

    lines.append("")

    footer_lines = [_SEP]
    if p.get("trader_id"):
        footer_lines.append(f"Trader: {p['trader_id']}")
    footer_lines.append(f"Source: {p.get('source', 'original_message')}")
    if p.get("link"):
        footer_lines.append(p["link"])
    lines += footer_lines
    return "\n".join(lines)


def _signal_rejected(p: dict) -> str:
    lines = _header("❌", p.get("chain_id"), "SIGNAL REJECTED", p.get("symbol"), p.get("side"))
    for e in p.get("entries") or []:
        seq = e.get("sequence", 1)
        etype = e.get("entry_type", "LIMIT")
        price = e.get("price")
        if etype == "MARKET":
            price_str = f"Market ~{_num(price)}" if price is not None else "Market"
        else:
            price_str = f"{_num(price)} Limit" if price is not None else "Limit"
        lines.append(f"Entry_{seq}: {price_str}")
    if p.get("sl") is not None:
        lines.append(f"SL: {_num(p['sl'])}")
    lines.append("")
    footer_lines = [_SEP]
    if p.get("trader_id"):
        footer_lines.append(f"Trader: {p['trader_id']}")
    if p.get("reason"):
        footer_lines.append(f"Rejected: {p['reason']}")
    footer_lines.append(f"Source: {p.get('source', 'original_message')}")
    if p.get("link"):
        footer_lines.append(p["link"])
    lines += footer_lines
    return "\n".join(lines)


def _review_required(p: dict) -> str:
    lines = _header("⚠️", p.get("chain_id"), "REVIEW REQUIRED", p.get("symbol"), p.get("side"))
    lines.append(f"Reason: {p.get('reason', 'unknown')}")
    lines.append("Action: no automatic execution")
    lines.append("")
    lines += _footer(p.get("source", "runtime"), p.get("link"))
    return "\n".join(lines)


def _entry_opened(p: dict) -> str:
    lines = _header("📊", p.get("chain_id"), "ENTRY OPENED", p.get("symbol"), p.get("side"))

    if p.get("fill_price") is not None:
        lines.append("Filled:")
        lines.append(f"Price: {_num(p['fill_price'])}")
        if p.get("filled_qty") is not None:
            lines.append(f"Qty: {_num(p['filled_qty'])}")
        lines.append("")

    if p.get("avg_entry") is not None:
        lines.append("Position:")
        lines.append(f"Avg entry: {_num(p['avg_entry'])}")
        pending = p.get("pending_entries") or []
        if pending:
            for pe in pending:
                seq = pe.get("sequence", "?")
                price = pe.get("price")
                etype = pe.get("entry_type", "LIMIT")
                price_str = _num(price) if price is not None else "?"
                lines.append(f"Pending: Entry_{seq} {price_str} {etype.capitalize()}")
        else:
            lines.append("Pending: none")
        lines.append("")

    lines += _footer(p.get("source", "exchange"))
    return "\n".join(lines)


def _tp_filled(p: dict, final: bool) -> str:
    level = p.get("tp_level")
    label = f"TP{level} FILLED" if level is not None else "TP FILLED"
    if final:
        label += " — POSITION CLOSED"
    lines = _header("📊", p.get("chain_id"), label, p.get("symbol"), p.get("side"))

    if level is not None:
        tp_label = f"TP_{level}"
        if p.get("tp_price") is not None:
            lines.append(f"{tp_label}: {_num(p['tp_price'])}")
        else:
            lines.append(f"{tp_label}: —")

    lines.append("")

    if not final and p.get("sl_current") is not None:
        lines.append("Remaining:")
        lines.append(f"SL: {_num(p['sl_current'])}")
        lines.append("")

    if final:
        lines.append("Close reason: TAKE_PROFIT")
        lines.append("")

    lines += _footer(p.get("source", "exchange"))
    return "\n".join(lines)


def _sl_filled(p: dict) -> str:
    lines = _header("🛑", p.get("chain_id"), "SL FILLED — POSITION CLOSED",
                    p.get("symbol"), p.get("side"))
    if p.get("fill_price") is not None:
        lines.append(f"Fill: {_num(p['fill_price'])}")
        lines.append("")
    lines.append("Close reason: STOP_LOSS")
    lines.append("")
    lines += _footer(p.get("source", "exchange"))
    return "\n".join(lines)


def _position_closed(p: dict) -> str:
    lines = _header("📊", p.get("chain_id"), "POSITION CLOSED", p.get("symbol"), p.get("side"))
    if p.get("fill_price") is not None:
        lines.append(f"Fill: {_num(p['fill_price'])}")
        lines.append("")
    lines.append("Close reason: MANUAL_CLOSE")
    lines.append("")
    lines += _footer(p.get("source", "exchange"))
    return "\n".join(lines)


def _entry_updated(p: dict) -> str:
    lines = _header("✏️", p.get("chain_id"), "ENTRY UPDATED", p.get("symbol"), p.get("side"))
    if p.get("fill_price") is not None:
        lines.append(f"Fill price: {_num(p['fill_price'])}")
    if p.get("filled_qty") is not None:
        lines.append(f"Filled qty: {_num(p['filled_qty'])}")
    if p.get("new_avg_entry") is not None:
        lines.append(f"New avg entry: {_num(p['new_avg_entry'])}")
    lines.append("")
    lines += _footer(p.get("source", "exchange"), p.get("link"))
    return "\n".join(lines)


def _update_done(p: dict) -> str:
    lines = _header("✅", p.get("chain_id"), "UPDATE DONE", p.get("symbol"), p.get("side"))
    applied = p.get("applied_actions") or []
    if applied:
        lines.append("Applied:")
        for action in applied:
            lines.append(f"  • {action}")
    changed = p.get("changed_fields") or []
    if changed:
        lines.append("Changed fields:")
        for field in changed:
            lines.append(f"  • {field}")
    lines.append("")
    lines += _footer(p.get("source", "runtime"), p.get("link"))
    return "\n".join(lines)


def _update_partial(p: dict) -> str:
    lines = _header("⚠️", p.get("chain_id"), "UPDATE PARTIAL", p.get("symbol"), p.get("side"))
    applied = p.get("applied_actions") or []
    if applied:
        lines.append("Applied:")
        for action in applied:
            lines.append(f"  • {action}")
    rejected = p.get("rejected_actions") or []
    if rejected:
        lines.append("Rejected:")
        for action in rejected:
            lines.append(f"  • {action}")
    lines.append("")
    lines += _footer(p.get("source", "runtime"), p.get("link"))
    return "\n".join(lines)


def _update_rejected(p: dict) -> str:
    lines = _header("❌", p.get("chain_id"), "UPDATE REJECTED", p.get("symbol"), p.get("side"))
    if p.get("reason") is not None:
        lines.append(f"Reason: {p['reason']}")
    lines.append("")
    lines += _footer(p.get("source", "runtime"), p.get("link"))
    return "\n".join(lines)


def _pending_timeout(p: dict) -> str:
    lines = _header("⏰", p.get("chain_id"), "PENDING ENTRY EXPIRED",
                    p.get("symbol"), p.get("side"))
    lines.append("Timeout: order expired before fill")
    lines.append("")
    lines += _footer(p.get("source", "worker"), p.get("link"))
    return "\n".join(lines)


def _reconciliation_warning(p: dict) -> str:
    lines = _header("⚠️", p.get("chain_id"), "RECONCILIATION WARNING",
                    p.get("symbol"), p.get("side"))
    if p.get("issue") is not None:
        lines.append(f"Issue: {p['issue']}")
    if p.get("risk") is not None:
        lines.append(f"Risk: {p['risk']}")
    if p.get("action") is not None:
        lines.append(f"Action: {p['action']}")
    lines.append("")
    lines += _footer(p.get("source", "runtime"), p.get("link"))
    return "\n".join(lines)


def _reconciliation_fixed(p: dict) -> str:
    lines = _header("✅", p.get("chain_id"), "RECONCILIATION FIXED",
                    p.get("symbol"), p.get("side"))
    if p.get("issue") is not None:
        lines.append(f"Issue resolved: {p['issue']}")
    lines.append("")
    lines += _footer(p.get("source", "runtime"), p.get("link"))
    return "\n".join(lines)


def _reentry_accepted(p: dict) -> str:
    lines = _header("🔄", p.get("chain_id"), "REENTRY ACCEPTED", p.get("symbol"), p.get("side"))
    if p.get("previous_chain_id") is not None:
        lines.append(f"Previous chain: #{p['previous_chain_id']}")
    lines.append("")
    lines += _footer(p.get("source", "runtime"), p.get("link"))
    return "\n".join(lines)


def _fallback(notification_type: str, p: dict) -> str:
    lines = _header("📊", p.get("chain_id"), notification_type, p.get("symbol"), p.get("side"))
    lines += _footer(p.get("source", "runtime"))
    return "\n".join(lines)


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
    return _fallback(notification_type, payload)


__all__ = ["format_clean_log"]
