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
    if f == int(f):
        return f"{int(f):,}"
    return f"{f:,.2f}"


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
    if p.get("trader_id"):
        lines.append(f"Trader: {p['trader_id']}")
    lines.append("")
    lines += _footer(p.get("source", "original_message"), p.get("link"))
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
        lines.append(f"Entry: {_num(p['fill_price'])}")
        if p.get("filled_qty") is not None:
            lines.append(f"Qty: {p['filled_qty']}")
        lines.append("")
    lines += _footer(p.get("source", "exchange"))
    return "\n".join(lines)


def _tp_filled(p: dict, final: bool) -> str:
    level = p.get("tp_level")
    label = f"TP{level} FILLED" if level is not None else "TP FILLED"
    if final:
        label += " — POSITION CLOSED"
    lines = _header("📊", p.get("chain_id"), label, p.get("symbol"), p.get("side"))
    if p.get("tp_price") is not None:
        lines.append(f"TP_{level}: {_num(p['tp_price'])}")
    if p.get("pnl") is not None:
        lines.append(f"PnL: {p['pnl']} USDT")
    lines.append("")
    lines += _footer(p.get("source", "exchange"))
    return "\n".join(lines)


def _sl_filled(p: dict) -> str:
    lines = _header("🛑", p.get("chain_id"), "SL FILLED — POSITION CLOSED",
                    p.get("symbol"), p.get("side"))
    if p.get("pnl") is not None:
        lines.append(f"PnL: {p['pnl']} USDT")
    lines.append("Close reason: STOP_LOSS")
    lines.append("")
    lines += _footer(p.get("source", "exchange"))
    return "\n".join(lines)


def _position_closed(p: dict) -> str:
    lines = _header("📊", p.get("chain_id"), "POSITION CLOSED", p.get("symbol"), p.get("side"))
    if p.get("pnl") is not None:
        lines.append(f"PnL: {p['pnl']} USDT")
    lines.append("Close reason: MANUAL_CLOSE")
    lines.append("")
    lines += _footer(p.get("source", "exchange"))
    return "\n".join(lines)


def _fallback(notification_type: str, p: dict) -> str:
    lines = _header("📊", p.get("chain_id"), notification_type, p.get("symbol"), p.get("side"))
    lines += _footer(p.get("source", "runtime"))
    return "\n".join(lines)


def format_clean_log(notification_type: str, payload: dict) -> str:
    if notification_type == "SIGNAL_ACCEPTED":
        return _signal_accepted(payload)
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
    return _fallback(notification_type, payload)


__all__ = ["format_clean_log"]
