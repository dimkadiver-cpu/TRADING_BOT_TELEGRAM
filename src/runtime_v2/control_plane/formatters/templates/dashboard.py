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
# PNL view
# ---------------------------------------------------------------------------

def _pnl_account_lines(p: dict) -> str:
    from datetime import datetime
    parts = []
    captured_at = p.get("captured_at")
    age = p.get("snapshot_age_seconds")
    source = p.get("source")
    stale = p.get("snapshot_stale", False)

    if captured_at:
        try:
            dt = datetime.fromisoformat(captured_at)
            time_str = dt.strftime("%H:%M:%S") + " UTC"
        except ValueError:
            time_str = captured_at
        age_str = f"age {int(age)}s" if age is not None else "age ?"
        stale_str = " · STALE" if stale else ""
        source_str = f" · {source}" if source else ""
        parts.append(f"Snapshot: {time_str} · {age_str}{source_str}{stale_str}")

    if p.get("equity_usdt") is not None:
        parts.append(f"Equity:        {p['equity_usdt']:,.2f} USDT")
    if p.get("available_balance_usdt") is not None:
        parts.append(f"Available:     {p['available_balance_usdt']:,.2f} USDT")
    if p.get("total_margin_used_usdt") is not None:
        parts.append(f"Margin used:   {p['total_margin_used_usdt']:,.2f} USDT")
    if p.get("account_unrealized_pnl_usdt") is not None:
        sign = "+" if p["account_unrealized_pnl_usdt"] >= 0 else ""
        parts.append(f"uPnL live:     {sign}{p['account_unrealized_pnl_usdt']:.2f} USDT")
    if p.get("total_open_risk_usdt") is not None:
        parts.append(f"Open risk*:    {p['total_open_risk_usdt']:.2f} USDT")
    return "\n".join(parts) if parts else "n/a"


def _pnl_realized_label(p: dict) -> str:
    if p.get("is_global"):
        return "Realized — All accounts:"
    trader_id = p.get("trader_id")
    if trader_id:
        return f"Realized — {trader_id}:"
    return "Realized:"


def _pnl_realized_lines(p: dict) -> str:
    parts = []
    if p.get("gross_pnl") is not None:
        sign = "+" if p["gross_pnl"] >= 0 else ""
        parts.append(f"  Gross:      {sign}{p['gross_pnl']:.2f} USDT")
    if p.get("total_fees") is not None:
        parts.append(f"  Fees:        {p['total_fees']:.2f} USDT")
    if p.get("pnl_net") is not None:
        sign = "+" if p["pnl_net"] >= 0 else ""
        parts.append(f"  Net:        {sign}{p['pnl_net']:.2f} USDT")
    return "\n".join(parts) if parts else "  n/a"


def _pnl_by_account_lines(p: dict) -> str:
    rows = p.get("by_account") or []
    lines = []
    for r in rows:
        acc_id = r.get("account_id", "?")
        net = r.get("net_pnl", 0.0)
        sign = "+" if net >= 0 else ""
        open_c = r.get("open_count", 0)
        age = r.get("age_seconds")
        stale = r.get("stale", False)
        if stale:
            age_str = f"{int(age)}s ago" if age is not None else "?"
            lines.append(f"{acc_id} · STALE · last {age_str}")
        else:
            age_str = f" · age {int(age)}s" if age is not None else ""
            lines.append(f"{acc_id} · Net: {sign}{net:.2f} USDT · Open: {open_c}{age_str}")
    return "\n".join(lines) if lines else "n/a"


_PNL_BLOCKS: list = [
    *_dash_header_full("💰", "PnL"),
    # Non-global: show account snapshot
    ConditionalBlock(
        condition=lambda p: not p.get("is_global"),
        blocks=[
            StaticBlock("Account snapshot:"),
            DerivedBlock(text_fn=_pnl_account_lines),
            SeparatorBlock(),
        ],
    ),
    # Global: show accounts in scope summary
    ConditionalBlock(
        condition=lambda p: bool(p.get("is_global")),
        blocks=[
            DerivedBlock(text_fn=lambda p: f"Accounts in scope: {p.get('accounts_in_scope', 0)}"),
            StaticBlock("Snapshot mode: per-account latest"),
            SeparatorBlock(),
        ],
    ),
    DerivedBlock(text_fn=_pnl_realized_label),
    DerivedBlock(text_fn=_pnl_realized_lines),
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
    "dashboard_blocked": TEMPLATE_DASHBOARD_BLOCKED,
    "dashboard_pnl":     TEMPLATE_DASHBOARD_PNL,
    "dashboard_stats":   TEMPLATE_DASHBOARD_STATS,
}

__all__ = [
    "DASHBOARD_TEMPLATE_REGISTRY",
    "TEMPLATE_DASHBOARD_ACTIVE",
    "TEMPLATE_DASHBOARD_CLOSED",
    "TEMPLATE_DASHBOARD_BLOCKED",
    "TEMPLATE_DASHBOARD_PNL",
    "TEMPLATE_DASHBOARD_STATS",
]
