# src/runtime_v2/control_plane/formatters/templates/dashboard.py
from __future__ import annotations

from src.runtime_v2.control_plane.formatters._blocks import (
    _SEP,
    SeparatorBlock, StaticBlock, DerivedBlock,
    ConditionalBlock, ListBlock, TableBlock,
    TemplateConfig,
)
from src.runtime_v2.control_plane.formatters._formatters import (
    num,
    money_signed,
)
from src.runtime_v2.control_plane.formatters.display import display_symbol
from src.runtime_v2.control_plane.formatters.templates._shared import (
    _cmd_header,
    _side_emoji_str,
    _pnl_str,
    _protection_str,
)


# ---------------------------------------------------------------------------
# Shared dashboard header helper — spec-compliant compact header
# ---------------------------------------------------------------------------

def _dash_header_full(emoji: str, view_label: str) -> list:
    """Compact spec header:
    ⚡ Active — demo_1 · trader_a
    ─────────────────────────────────────
    Total: 10   Page: 1/2   Updated: 14:32:05
    [Filters: ...]   ← only if filters_str is set
    [Order: ...]     ← only if order_str is set (global scope)
    ─────────────────────────────────────
    """
    return [
        DerivedBlock(text_fn=lambda p, _e=emoji, _v=view_label: (
            f"{_e} {_v} — "
            + (p.get("account_id") or "All accounts")
            + (f" · {p['trader_id']}" if p.get("trader_id") else "")
        )),
        SeparatorBlock(),
        DerivedBlock(text_fn=lambda p: (
            f"Total: {p.get('total', 0)}   "
            f"Page: {p.get('page_display', '1/1')}   "
            f"Updated: {p.get('updated_at', 'n/a')}"
        )),
        ConditionalBlock(
            condition=lambda p: bool(p.get("filters_str")),
            blocks=[DerivedBlock(text_fn=lambda p: f"Filters: {p['filters_str']}")],
        ),
        ConditionalBlock(
            condition=lambda p: bool(p.get("order_str")),
            blocks=[DerivedBlock(text_fn=lambda p: f"Order: {p['order_str']}")],
        ),
        SeparatorBlock(),
    ]


# ---------------------------------------------------------------------------
# Active view item renderer
# ---------------------------------------------------------------------------

def _render_active_item(row: dict, i: int, p: dict) -> list[str]:
    cid = row.get("chain_id", "?")
    symbol = display_symbol(row.get("symbol") or "?")
    side = row.get("side", "?")
    state = row.get("state", "?")
    lines = [_SEP] if i > 0 else []
    lines.append(f"#{cid} · {symbol} · {side} · {state}")

    if p.get("is_global"):
        trader = row.get("trader_id") or "?"
        account = row.get("account_id") or "?"
        lines.append(f"Trader: {trader} · Account: {account}")

    upnl = row.get("unrealized_pnl")
    rpnl = row.get("cum_realized_pnl")
    if state not in ("WAITING_ENTRY", "PARTIALLY_FILLED"):
        upnl_str = money_signed(upnl) if upnl is not None else "—"
        rpnl_str = money_signed(rpnl) if rpnl is not None else "+0.00 USDT"
        lines.append(f"uPnL: {upnl_str}  rPnL: {rpnl_str}")
    else:
        lines.append("rPnL: —")

    lines.append(f"/trade_{cid} · /cancel_{cid} · /close_{cid}")
    return lines


_ACTIVE_FRESHNESS_WARNING = ConditionalBlock(
    condition=lambda p: bool(p.get("_mark_stale")),
    blocks=[
        StaticBlock("⚠️ Snapshot oltre intervallo riconciliazione"),
    ],
)

_ACTIVE_BLOCKS: list = [
    *_dash_header_full("⚡", "Active"),
    ConditionalBlock(
        condition=lambda p: not p.get("rows"),
        blocks=[StaticBlock("No active trades.")],
    ),
    ConditionalBlock(
        condition=lambda p: bool(p.get("rows")),
        blocks=[ListBlock(key="rows", item_renderer=_render_active_item, index_start=0)],
    ),
    _ACTIVE_FRESHNESS_WARNING,
]

TEMPLATE_DASHBOARD_ACTIVE = TemplateConfig(_ACTIVE_BLOCKS, payload_transform=None)


# ---------------------------------------------------------------------------
# Closed view item renderer
# ---------------------------------------------------------------------------

def _render_closed_item(row: dict, i: int, p: dict) -> list[str]:
    cid = row.get("chain_id", "?")
    symbol = display_symbol(row.get("symbol") or "?")
    side = row.get("side", "?")
    reason = row.get("closed_reason")
    state = row.get("lifecycle_state")
    is_cancelled = state == "CANCELLED_UNFILLED"

    first_line = f"#{cid} · {symbol} · {side}"
    if reason:
        first_line += f" · {reason}"
    elif is_cancelled:
        first_line += " · CANCELLED_UNFILLED"
    lines = [_SEP, first_line] if i > 0 else [first_line]

    if p.get("is_global"):
        trader = row.get("trader_id") or "?"
        account = row.get("account_id") or "?"
        lines.append(f"Trader: {trader} · Account: {account}")

    if is_cancelled:
        lines.append("PnL: No fill")
    else:
        pnl = row.get("gross_pnl")
        pnl_str = money_signed(pnl) if pnl is not None else "—"
        duration = row.get("duration") or "—"
        lines.append(f"Net PnL: {pnl_str} · ⏱ {duration}")
    lines.append(f"Details: /trade_{cid}")
    return lines


_CLOSED_BLOCKS: list = [
    *_dash_header_full("✅", "Closed"),
    ConditionalBlock(
        condition=lambda p: not p.get("rows"),
        blocks=[StaticBlock("No closed trades.")],
    ),
    ConditionalBlock(
        condition=lambda p: bool(p.get("rows")),
        blocks=[ListBlock(key="rows", item_renderer=_render_closed_item, index_start=0)],
    ),
]

TEMPLATE_DASHBOARD_CLOSED = TemplateConfig(_CLOSED_BLOCKS, payload_transform=None)


# ---------------------------------------------------------------------------
# Blocked view item renderer
# ---------------------------------------------------------------------------

def _render_blocked_item(row: dict, i: int, p: dict) -> list[str]:
    cid = row.get("chain_id", "?")
    symbol = display_symbol(row.get("symbol") or "?")
    side = row.get("side", "?")
    lines = [_SEP, f"#{cid} · {symbol} · {side}"] if i > 0 else [f"#{cid} · {symbol} · {side}"]

    if p.get("is_global"):
        trader = row.get("trader_id") or "?"
        account = row.get("account_id") or "?"
        lines.append(f"Trader: {trader} · Account: {account}")

    blocked_at = row.get("blocked_at") or "—"
    reason = row.get("reason") or "—"
    lines.append(f"Blocked: {blocked_at} · Reason: {reason}")
    lines.append(f"Details: /trade_{cid}")
    return lines


_BLOCKED_BLOCKS: list = [
    *_dash_header_full("🚫", "Blocked"),
    ConditionalBlock(
        condition=lambda p: not p.get("rows"),
        blocks=[StaticBlock("No blocked trades.")],
    ),
    ConditionalBlock(
        condition=lambda p: bool(p.get("rows")),
        blocks=[ListBlock(key="rows", item_renderer=_render_blocked_item, index_start=0)],
    ),
]

TEMPLATE_DASHBOARD_BLOCKED = TemplateConfig(_BLOCKED_BLOCKS, payload_transform=None)


# ---------------------------------------------------------------------------
# Not executed / operational issues views
# ---------------------------------------------------------------------------

def _render_not_executed_item(row: dict, i: int, p: dict) -> list[str]:
    reference = row.get("reference") or "#?"
    symbol = display_symbol(row.get("symbol") or "?")
    side = row.get("side", "?")
    outcome = row.get("outcome") or "UNKNOWN"
    lines = [_SEP, f"{reference} · {symbol} · {side} · {outcome}"] if i > 0 else [f"{reference} · {symbol} · {side} · {outcome}"]

    if p.get("is_global"):
        trader = row.get("trader_id") or "?"
        account = row.get("account_id") or "?"
        lines.append(f"Trader: {trader} · Account: {account}")

    phase = row.get("phase") or "Unknown"
    reason = row.get("reason") or "—"
    occurred_at = row.get("occurred_at") or "—"
    command_type = row.get("command_type")
    details = [f"Phase: {phase}"]
    if command_type:
        details.append(f"Command: {command_type}")
    details.extend([f"Reason: {reason}", f"At: {occurred_at}"])
    lines.append(" · ".join(details))
    trade_chain_id = row.get("trade_chain_id")
    if trade_chain_id is not None:
        lines.append(f"Details: /trade_{trade_chain_id}")
    return lines


def _render_operational_issue_item(row: dict, i: int, p: dict) -> list[str]:
    trade_chain_id = row.get("trade_chain_id", "?")
    symbol = display_symbol(row.get("symbol") or "?")
    side = row.get("side", "?")
    command_type = row.get("command_type") or row.get("details_command") or "UNKNOWN"
    lines = [_SEP, f"#{trade_chain_id} · {symbol} · {side} · {command_type}"] if i > 0 else [f"#{trade_chain_id} · {symbol} · {side} · {command_type}"]

    if p.get("is_global"):
        trader = row.get("trader_id") or "?"
        account = row.get("account_id") or "?"
        lines.append(f"Trader: {trader} · Account: {account}")

    issue_type = row.get("issue_type") or "UNKNOWN"
    phase = row.get("phase") or "Unknown"
    reason = row.get("reason") or "—"
    occurred_at = row.get("occurred_at") or "—"
    lines.append(f"Type: {issue_type} · Phase: {phase} · Reason: {reason} · At: {occurred_at}")
    lines.append(f"Details: /trade_{trade_chain_id}")
    return lines


_NOT_EXECUTED_BLOCKS: list = [
    *_dash_header_full("🚫", "Not executed"),
    ConditionalBlock(
        condition=lambda p: not p.get("rows"),
        blocks=[StaticBlock("No not executed trades.")],
    ),
    ConditionalBlock(
        condition=lambda p: bool(p.get("rows")),
        blocks=[ListBlock(key="rows", item_renderer=_render_not_executed_item, index_start=0)],
    ),
]


_OPERATIONAL_ISSUES_BLOCKS: list = [
    *_dash_header_full("⚠️", "Operational issues"),
    ConditionalBlock(
        condition=lambda p: not p.get("rows"),
        blocks=[StaticBlock("No operational issues.")],
    ),
    ConditionalBlock(
        condition=lambda p: bool(p.get("rows")),
        blocks=[ListBlock(key="rows", item_renderer=_render_operational_issue_item, index_start=0)],
    ),
]


TEMPLATE_DASHBOARD_NOT_EXECUTED = TemplateConfig(_NOT_EXECUTED_BLOCKS, payload_transform=None)
TEMPLATE_DASHBOARD_OPERATIONAL_ISSUES = TemplateConfig(_OPERATIONAL_ISSUES_BLOCKS, payload_transform=None)


# ---------------------------------------------------------------------------
# PNL view
# ---------------------------------------------------------------------------

def _pnl_account_lines(p: dict) -> str:
    from datetime import datetime
    available = p.get("available_balance_usdt")
    margin = p.get("total_margin_used_usdt")
    futures_wallet = p.get("futures_wallet_usdt")
    upnl = p.get("account_unrealized_pnl_usdt")
    risk = p.get("total_open_risk_usdt")
    captured_at = p.get("captured_at")
    age = p.get("snapshot_age_seconds")
    stale = p.get("snapshot_stale", False)

    if available is None and margin is None and captured_at is None:
        return "  n/a — nessun snapshot disponibile"

    parts = []
    if available is not None:
        parts.append(f"  Available:      {available:>12,.2f} USDT")
    if margin is not None:
        parts.append(f"  Margin in use:  {margin:>12,.2f} USDT")
    if available is not None or margin is not None:
        parts.append(f"  {'─' * 29}")
    if futures_wallet is not None:
        parts.append(f"  Futures wallet: {futures_wallet:>12,.2f} USDT")
    if upnl is not None:
        sign = "+" if upnl >= 0 else ""
        parts.append(f"  uPnL live:      {sign}{upnl:>11.2f} USDT")
    if risk is not None:
        parts.append(f"  Open risk*:     {risk:>12.2f} USDT")
    if captured_at:
        try:
            dt = datetime.fromisoformat(captured_at)
            time_str = dt.strftime("%H:%M:%S") + " UTC"
        except ValueError:
            time_str = captured_at
        age_str = f"age {int(age)}s" if age is not None else "age ?"
        stale_str = " · STALE" if stale else ""
        parts.append(f"  Snapshot: {time_str} · {age_str}{stale_str}")

    return "\n".join(parts) if parts else "  n/a — nessun snapshot disponibile"


def _pnl_realized_label(p: dict) -> str:
    if p.get("is_global"):
        return "Realized — All accounts:"
    by_trader = p.get("by_trader")
    if by_trader and len(by_trader) >= 2:
        names = ", ".join(t["trader_id"] for t in by_trader)
        return f"Realized — {names}:"
    trader_id = p.get("trader_id")
    if trader_id:
        return f"Realized — {trader_id}:"
    account_id = p.get("account_id") or ""
    return f"Realized — {account_id}:"


def _pnl_realized_lines(p: dict) -> str:
    pnl_net = p.get("pnl_net")
    partial_pnl_net = p.get("partial_pnl_net")

    has_closed = pnl_net is not None
    has_partial = partial_pnl_net is not None and partial_pnl_net != 0.0

    if not has_closed and not has_partial:
        return "  Nessun trade chiuso."

    parts = []
    if has_closed:
        sign = "+" if pnl_net >= 0 else ""
        parts.append(f"  Closed:        {sign}{pnl_net:.2f} USDT")
    if has_partial:
        sign = "+" if partial_pnl_net >= 0 else ""
        parts.append(f"  Partial open:   {sign}{partial_pnl_net:.2f} USDT")
    totale = (pnl_net or 0.0) + (partial_pnl_net or 0.0)
    parts.append(f"  {'─' * 29}")
    sign = "+" if totale >= 0 else ""
    parts.append(f"  Totale:        {sign}{totale:.2f} USDT")
    return "\n".join(parts)


def _pnl_by_trader_lines(p: dict) -> str:
    rows = p.get("by_trader") or []
    lines = []
    for t in rows:
        parts = [t["trader_id"], f"Open: {t['open_count']}"]
        if t.get("risk_usdt") is not None:
            parts.append(f"Risk: {t['risk_usdt']:.2f}")
        sign = "+" if t["closed_pnl"] >= 0 else ""
        parts.append(f"Closed: {sign}{t['closed_pnl']:.2f}")
        if t["partial_pnl"] != 0.0:
            sign = "+" if t["partial_pnl"] >= 0 else ""
            parts.append(f"Partial: {sign}{t['partial_pnl']:.2f}")
        lines.append("  " + " · ".join(parts))
    return "\n".join(lines)


def _pnl_by_account_lines(p: dict) -> str:
    rows = p.get("by_account") or []
    lines = []
    for r in rows:
        acc_id = r.get("account_id", "?")
        net = r.get("net_pnl", 0.0)
        sign = "+" if net >= 0 else ""
        age = r.get("age_seconds")
        stale = r.get("stale", False)
        available = r.get("available_usdt")
        margin = r.get("margin_usdt")

        if stale:
            if age is not None and age >= 60:
                age_human = f"{int(age // 60)}m ago"
            elif age is not None:
                age_human = f"{int(age)}s ago"
            else:
                age_human = "?"
            lines.append(f"{acc_id} · STALE · last {age_human} · Net: {sign}{net:.2f}")
        else:
            parts = [acc_id]
            if available is not None:
                parts.append(f"Avail: {available:.0f}")
            if margin is not None:
                parts.append(f"Margin: {margin:.0f}")
            parts.append(f"Net: {sign}{net:.2f}")
            if age is not None:
                parts.append(f"age {int(age)}s")
            lines.append(" · ".join(parts))
    return "\n".join(lines) if lines else "n/a"


_PNL_BLOCKS: list = [
    *_dash_header_full("💰", "PnL"),
    # Non-global: show account snapshot with dynamic header
    ConditionalBlock(
        condition=lambda p: not p.get("is_global"),
        blocks=[
            DerivedBlock(text_fn=lambda p: f"Account snapshot ({p.get('account_id')}):"),
            DerivedBlock(text_fn=_pnl_account_lines),
            SeparatorBlock(),
        ],
    ),
    # Global: summary line + financial aggregates
    ConditionalBlock(
        condition=lambda p: bool(p.get("is_global")),
        blocks=[
            DerivedBlock(text_fn=lambda p: (
                f"Accounts: {p.get('accounts_in_scope', 0)} · "
                f"Snapshots: {p.get('accounts_fresh', 0)} fresh · {p.get('accounts_stale', 0)} stale"
            )),
            ConditionalBlock(
                condition=lambda p: p.get("futures_wallet_usdt") is not None,
                blocks=[
                    DerivedBlock(text_fn=lambda p: f"Futures wallet: {p['futures_wallet_usdt']:,.2f} USDT   (fresh only)"),
                ],
            ),
            ConditionalBlock(
                condition=lambda p: p.get("available_balance_usdt") is not None,
                blocks=[
                    DerivedBlock(text_fn=lambda p: f"Available:      {p['available_balance_usdt']:,.2f} USDT"),
                ],
            ),
            ConditionalBlock(
                condition=lambda p: p.get("total_margin_used_usdt") is not None,
                blocks=[
                    DerivedBlock(text_fn=lambda p: f"Margin in use:  {p['total_margin_used_usdt']:,.2f} USDT"),
                ],
            ),
            ConditionalBlock(
                condition=lambda p: p.get("account_unrealized_pnl_usdt") is not None,
                blocks=[
                    DerivedBlock(text_fn=lambda p: (
                        f"uPnL aggregate: +{p['account_unrealized_pnl_usdt']:.2f} USDT"
                        if p["account_unrealized_pnl_usdt"] >= 0
                        else f"uPnL aggregate: {p['account_unrealized_pnl_usdt']:.2f} USDT"
                    )),
                ],
            ),
            SeparatorBlock(),
        ],
    ),
    DerivedBlock(text_fn=_pnl_realized_label),
    DerivedBlock(text_fn=_pnl_realized_lines),
    # By trader: only when 2+ traders in scope
    ConditionalBlock(
        condition=lambda p: len(p.get("by_trader") or []) >= 2,
        blocks=[
            StaticBlock(""),
            StaticBlock("By trader:"),
            DerivedBlock(text_fn=_pnl_by_trader_lines),
        ],
    ),
    SeparatorBlock(),
    DerivedBlock(text_fn=lambda p: (
        f"Open: {p.get('open_count', 0)} · Waiting entry: {p.get('waiting_entry_count', 0)}"
    )),
    # Global: by_account breakdown
    ConditionalBlock(
        condition=lambda p: bool(p.get("by_account")),
        blocks=[
            SeparatorBlock(),
            StaticBlock("By account:"),
            DerivedBlock(text_fn=_pnl_by_account_lines),
        ],
    ),
]

TEMPLATE_DASHBOARD_PNL = TemplateConfig(_PNL_BLOCKS, payload_transform=None)


# ---------------------------------------------------------------------------
# STATS view
# ---------------------------------------------------------------------------

def _fmt_win_pct(value: object) -> str:
    if value is None:
        return "—"
    try:
        f = float(value)
        return f"{f:.0f}%"
    except (TypeError, ValueError):
        return str(value)


def _fmt_signed_float(value: object) -> str:
    if value is None:
        return "—"
    try:
        f = float(value)
        sign = "+" if f >= 0 else ""
        return f"{sign}{f:.2f}"
    except (TypeError, ValueError):
        return str(value)


def _fmt_trade_count(value: object) -> str:
    if value is None:
        return "—"
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return str(value)


_STATS_TABLE = TableBlock(
    rows_key="stats_rows",
    columns=[
        ("Period", "label", 10, str),
        ("Trades", "trade_count", 6, _fmt_trade_count),
        ("Win%", "win_pct", 5, _fmt_win_pct),
        ("Net", "pnl_net", 9, _fmt_signed_float),
    ],
    show_header=True,
    fallback="—",
)


def _stats_by_account_lines(p: dict) -> str:
    rows = p.get("by_account") or []
    lines = []
    for r in rows:
        acc_id = r.get("account_id", "?")
        tc = r.get("trade_count", 0)
        wp = r.get("win_pct")
        win_str = f"{wp:.0f}%" if wp is not None else "—"
        net = r.get("net_pnl", 0.0)
        sign = "+" if net >= 0 else ""
        lines.append(f"{acc_id} · Trades: {tc} · Win%: {win_str} · Net: {sign}{net:.2f}")
    return "\n".join(lines) if lines else "n/a"


_STATS_BLOCKS: list = [
    *_dash_header_full("📉", "Stats"),
    _STATS_TABLE,
    SeparatorBlock(),
    ConditionalBlock(
        condition=lambda p: p.get("best_chain_id") is not None,
        blocks=[
            DerivedBlock(text_fn=lambda p: (
                f"Best:  #{p['best_chain_id']}  {p.get('best_symbol', '')}  {money_signed(p.get('best_pnl'))}"
                if p.get("best_pnl") is not None
                else f"Best:  #{p['best_chain_id']}"
            )),
        ],
    ),
    ConditionalBlock(
        condition=lambda p: p.get("worst_chain_id") is not None,
        blocks=[
            DerivedBlock(text_fn=lambda p: (
                f"Worst: #{p['worst_chain_id']} {p.get('worst_symbol', '')} {money_signed(p.get('worst_pnl'))}"
                if p.get("worst_pnl") is not None
                else f"Worst: #{p['worst_chain_id']}"
            )),
        ],
    ),
    ConditionalBlock(
        condition=lambda p: bool(p.get("by_account")),
        blocks=[
            SeparatorBlock(),
            StaticBlock("By account:"),
            DerivedBlock(text_fn=_stats_by_account_lines),
        ],
    ),
]

TEMPLATE_DASHBOARD_STATS = TemplateConfig(_STATS_BLOCKS, payload_transform=None)


# ---------------------------------------------------------------------------
# TEMPLATE REGISTRY
# ---------------------------------------------------------------------------

DASHBOARD_TEMPLATE_REGISTRY: dict[str, TemplateConfig] = {
    "dashboard_active":  TEMPLATE_DASHBOARD_ACTIVE,
    "dashboard_closed":  TEMPLATE_DASHBOARD_CLOSED,
    "dashboard_not_executed": TEMPLATE_DASHBOARD_NOT_EXECUTED,
    "dashboard_operational_issues": TEMPLATE_DASHBOARD_OPERATIONAL_ISSUES,
    "dashboard_blocked": TEMPLATE_DASHBOARD_NOT_EXECUTED,
    "dashboard_pnl":     TEMPLATE_DASHBOARD_PNL,
    "dashboard_stats":   TEMPLATE_DASHBOARD_STATS,
}

__all__ = [
    "DASHBOARD_TEMPLATE_REGISTRY",
    "TEMPLATE_DASHBOARD_ACTIVE",
    "TEMPLATE_DASHBOARD_CLOSED",
    "TEMPLATE_DASHBOARD_BLOCKED",
    "TEMPLATE_DASHBOARD_NOT_EXECUTED",
    "TEMPLATE_DASHBOARD_OPERATIONAL_ISSUES",
    "TEMPLATE_DASHBOARD_PNL",
    "TEMPLATE_DASHBOARD_STATS",
]
