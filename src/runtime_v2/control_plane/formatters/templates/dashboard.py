# src/runtime_v2/control_plane/formatters/templates/dashboard.py
from __future__ import annotations

from src.runtime_v2.control_plane.formatters._blocks import (
    _SEP,
    SeparatorBlock, StaticBlock, DerivedBlock,
    ConditionalBlock, ListBlock, TableBlock,
    TemplateConfig,
)
from src.runtime_v2.control_plane.formatters._formatters import (
    money_signed,
)
from src.runtime_v2.control_plane.formatters.templates._shared import (
    _cmd_header,
    _render_trade_item,
    _side_emoji_str,
)


# ---------------------------------------------------------------------------
# Shared dashboard header helper
# ---------------------------------------------------------------------------

def _dash_header(emoji: str, view_key: str) -> list:
    return [
        DerivedBlock(text_fn=lambda p, _e=emoji: (
            f"{_e} DASHBOARD — {p['account_id']}"
            + (f" · {p['trader_id']}" if p.get("trader_id") else "")
        )),
        SeparatorBlock(),
    ]


# ---------------------------------------------------------------------------
# ATTIVI view
# ---------------------------------------------------------------------------

_ATTIVI_FRESHNESS_WARNING = ConditionalBlock(
    condition=lambda p: bool(p.get("_mark_stale")),
    blocks=[
        StaticBlock("⚠️ Snapshot oltre intervallo riconciliazione"),
    ],
)

_ATTIVI_BLOCKS: list = [
    *_dash_header("📊", "attivi"),
    DerivedBlock(text_fn=lambda p: (
        p.get("updated_at", "n/a")
        + (f"  |  Mark snapshot: {p['_mark_time']} ({p['_mark_age']}s fa)"
           if p.get("_mark_time") else "")
    )),
    SeparatorBlock(),
    ConditionalBlock(
        condition=lambda p: not p.get("rows"),
        blocks=[StaticBlock("Nessun trade attivo.")],
    ),
    ConditionalBlock(
        condition=lambda p: bool(p.get("rows")),
        blocks=[ListBlock(key="rows", item_renderer=_render_trade_item)],
    ),
    _ATTIVI_FRESHNESS_WARNING,
]

TEMPLATE_DASHBOARD_ATTIVI = TemplateConfig(_ATTIVI_BLOCKS, payload_transform=None)


# ---------------------------------------------------------------------------
# CHIUSI view
# ---------------------------------------------------------------------------

def _render_closed_item(row: dict, i: int, p: dict) -> list[str]:
    chain_id = row.get("chain_id", "?")
    symbol = row.get("symbol", "?")
    side = row.get("side", "?")
    closed_at = row.get("closed_at") or "—"
    # Format closed_at: show only HH:MM:SS if it's a full ISO timestamp
    if closed_at and len(closed_at) >= 19:
        closed_at = closed_at[11:19]
    emoji = _side_emoji_str(side)
    pnl = row.get("gross_pnl")
    pnl_str = money_signed(pnl) if pnl is not None else "—"
    return [f"#{chain_id}  {emoji} {symbol}   CLOSED  {closed_at}   PnL: {pnl_str}"]


_CHIUSI_BLOCKS: list = [
    *_dash_header("✅", "chiusi"),
    DerivedBlock(text_fn=lambda p: p.get("updated_at", "n/a")),
    SeparatorBlock(),
    ConditionalBlock(
        condition=lambda p: not p.get("rows"),
        blocks=[StaticBlock("Nessun trade chiuso.")],
    ),
    ConditionalBlock(
        condition=lambda p: bool(p.get("rows")),
        blocks=[ListBlock(key="rows", item_renderer=_render_closed_item)],
    ),
]

TEMPLATE_DASHBOARD_CHIUSI = TemplateConfig(_CHIUSI_BLOCKS, payload_transform=None)


# ---------------------------------------------------------------------------
# BLOCCATI view
# ---------------------------------------------------------------------------

def _render_blocked_item(row: dict, i: int, p: dict) -> list[str]:
    chain_id = row.get("chain_id", "?")
    symbol = row.get("symbol", "?")
    state = row.get("state", "?")
    reason = row.get("reason") or "—"
    return [f"#{chain_id}   {symbol}   {state}   {reason}"]


_BLOCCATI_BLOCKS: list = [
    *_dash_header("🚫", "bloccati"),
    DerivedBlock(text_fn=lambda p: p.get("updated_at", "n/a")),
    SeparatorBlock(),
    ConditionalBlock(
        condition=lambda p: not p.get("rows"),
        blocks=[StaticBlock("Nessun trade bloccato.")],
    ),
    ConditionalBlock(
        condition=lambda p: bool(p.get("rows")),
        blocks=[ListBlock(key="rows", item_renderer=_render_blocked_item)],
    ),
]

TEMPLATE_DASHBOARD_BLOCCATI = TemplateConfig(_BLOCCATI_BLOCKS, payload_transform=None)


# ---------------------------------------------------------------------------
# PNL view
# ---------------------------------------------------------------------------

def _pnl_account_lines(p: dict) -> str:
    parts = []
    if p.get("equity_usdt") is not None:
        parts.append(f"  Equity:    {p['equity_usdt']:,.2f} USDT")
    if p.get("available_balance_usdt") is not None:
        parts.append(f"  Balance:    {p['available_balance_usdt']:,.2f} USDT")
    if p.get("total_margin_used_usdt") is not None:
        parts.append(f"  Margin:       {p['total_margin_used_usdt']:,.2f} USDT")
    return "\n".join(parts) if parts else "  n/a"


def _pnl_realized_label(p: dict) -> str:
    trader_id = p.get("trader_id")
    if trader_id:
        return f"Realizzato ({trader_id}):"
    return "Realizzato:"


def _pnl_realized_lines(p: dict) -> str:
    parts = []
    if p.get("gross_pnl") is not None:
        sign = "+" if p["gross_pnl"] >= 0 else ""
        parts.append(f"  Gross:      {sign}{p['gross_pnl']:.2f} USDT")
    if p.get("total_fees") is not None:
        parts.append(f"  Fees:        {p['total_fees']:.2f} USDT")
    if p.get("pnl_net") is not None:
        sign = "+" if p["pnl_net"] >= 0 else ""
        parts.append(f"  Netto:      {sign}{p['pnl_net']:.2f} USDT")
    return "\n".join(parts) if parts else "  n/a"


_PNL_BLOCKS: list = [
    *_dash_header("💰", "pnl"),
    DerivedBlock(text_fn=lambda p: p.get("updated_at", "n/a")),
    SeparatorBlock(),
    StaticBlock("Account:"),
    DerivedBlock(text_fn=_pnl_account_lines),
    SeparatorBlock(),
    DerivedBlock(text_fn=_pnl_realized_label),
    DerivedBlock(text_fn=_pnl_realized_lines),
    SeparatorBlock(),
    DerivedBlock(text_fn=lambda p: (
        f"Open: {p.get('open_count', 0)}  |  Waiting: {p.get('waiting_entry_count', 0)}"
    )),
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
        ("", "label", 10, str),
        ("Trades", "trade_count", 6, _fmt_trade_count),
        ("Win%", "win_pct", 5, _fmt_win_pct),
        ("Netto", "pnl_net", 9, _fmt_signed_float),
    ],
    show_header=True,
    fallback="—",
)

_STATS_BLOCKS: list = [
    *_dash_header("📉", "stats"),
    DerivedBlock(text_fn=lambda p: p.get("updated_at", "n/a")),
    SeparatorBlock(),
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
]

TEMPLATE_DASHBOARD_STATS = TemplateConfig(_STATS_BLOCKS, payload_transform=None)


# ---------------------------------------------------------------------------
# TEMPLATE REGISTRY
# ---------------------------------------------------------------------------

DASHBOARD_TEMPLATE_REGISTRY: dict[str, TemplateConfig] = {
    "dashboard_attivi":   TEMPLATE_DASHBOARD_ATTIVI,
    "dashboard_chiusi":   TEMPLATE_DASHBOARD_CHIUSI,
    "dashboard_bloccati": TEMPLATE_DASHBOARD_BLOCCATI,
    "dashboard_pnl":      TEMPLATE_DASHBOARD_PNL,
    "dashboard_stats":    TEMPLATE_DASHBOARD_STATS,
}

__all__ = [
    "DASHBOARD_TEMPLATE_REGISTRY",
    "TEMPLATE_DASHBOARD_ATTIVI",
    "TEMPLATE_DASHBOARD_CHIUSI",
    "TEMPLATE_DASHBOARD_BLOCCATI",
    "TEMPLATE_DASHBOARD_PNL",
    "TEMPLATE_DASHBOARD_STATS",
]
