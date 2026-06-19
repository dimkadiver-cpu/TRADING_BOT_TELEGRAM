# src/runtime_v2/control_plane/formatters/templates/commands.py
from __future__ import annotations

from src.runtime_v2.control_plane.formatters._blocks import (
    _SEP,
    SeparatorBlock, StaticBlock, DerivedBlock,
    FieldBlock, SectionBlock, ConditionalBlock, ListBlock, TableBlock,
    TemplateConfig,
)
from src.runtime_v2.control_plane.formatters._formatters import (
    num, money_signed,
)


# ---------------------------------------------------------------------------
# Shared header helper
# ---------------------------------------------------------------------------

def _cmd_header(emoji: str, command: str) -> list:
    return [
        DerivedBlock(text_fn=lambda p, _e=emoji, _c=command:
            f"{_e} {_c} — {p['account_id']}"
            + (f" · {p['trader_id']}" if p.get("trader_id") else "")
        ),
        SeparatorBlock(),
    ]


# ---------------------------------------------------------------------------
# TRADES template
# ---------------------------------------------------------------------------

def _pnl_str(row: dict) -> str:
    v = row.get("unrealized_pnl")
    if v is None:
        return "PnL: —"
    return f"PnL: {money_signed(v)}"


def _protection_str(row: dict) -> str:
    if row.get("has_be"):
        sl_price = row.get("current_stop_price")
        sl_part = f"SL: {num(sl_price)}" if sl_price is not None else "SL: set"
        be_part = "  BE: set"
        return sl_part + be_part
    if row.get("has_sl"):
        sl_price = row.get("current_stop_price")
        if sl_price is not None:
            return f"SL: {num(sl_price)}"
        return "SL: set"   # has_sl but price not tracked on TradeRow
    return "SL: —"


def _side_emoji_str(side: str | None) -> str:
    if side == "LONG":
        return "📈"
    if side == "SHORT":
        return "📉"
    return "•"


def _render_trade_item(row: dict, i: int, p: dict) -> list[str]:
    chain_id = row.get("chain_id", "?")
    symbol = row.get("symbol_display", row.get("symbol", "?"))
    side = row.get("side", "?")
    state = row.get("state", "?")
    emoji = _side_emoji_str(side)
    entry_price = row.get("entry_avg_price")
    qty = row.get("open_position_qty")

    line1 = f"#{chain_id}  {emoji} {symbol}  {side}   {state}"
    parts2 = []
    if entry_price is not None:
        parts2.append(f"Entry: {num(entry_price)}")
    prot = _protection_str(row)
    parts2.append(prot)
    line2 = "    " + "  ".join(parts2) if parts2 else ""

    parts3 = []
    if qty is not None:
        parts3.append(f"Qty: {num(qty)}")
    parts3.append(_pnl_str(row))
    line3 = "    " + "  ".join(parts3)

    result = [line1]
    if line2.strip():
        result.append(line2)
    result.append(line3)
    return result


_FRESHNESS_WARNING = ConditionalBlock(
    condition=lambda p: bool(p.get("_mark_stale")),
    blocks=[
        StaticBlock("⚠️ Snapshot oltre intervallo riconciliazione — PnL aperta non aggiornata"),
    ],
)

_TRADES_BLOCKS: list = [
    *_cmd_header("📊", "TRADES"),
    DerivedBlock(text_fn=lambda p: (
        "Updated: " + p.get("updated_at", "n/a")
        + (f"  |  Mark snapshot: {p['_mark_time']} ({p['_mark_age']}s fa)"
           if p.get("_mark_time") else "")
    )),
    SeparatorBlock(),
    ConditionalBlock(
        condition=lambda p: not p.get("rows"),
        blocks=[StaticBlock("No open trades.")],
    ),
    ConditionalBlock(
        condition=lambda p: bool(p.get("rows")),
        blocks=[ListBlock(key="rows", item_renderer=_render_trade_item)],
    ),
    _FRESHNESS_WARNING,
    SeparatorBlock(),
    StaticBlock("/trade #id  · /close <symbol>  · /cancel_all"),
]

TEMPLATE_TRADES = TemplateConfig(_TRADES_BLOCKS, payload_transform=None)


# ---------------------------------------------------------------------------
# PNL template
# ---------------------------------------------------------------------------

def _fmt_money_line(label: str, value: float | None) -> str | None:
    if value is None:
        return None
    sign = "+" if value >= 0 else ""
    return f"{label}: {sign}{value:.2f} USDT"


def _pnl_snapshot_lines(p: dict) -> str:
    parts = []
    if p.get("equity_usdt") is not None:
        parts.append(f"  Equity:    {p['equity_usdt']:,.2f} USDT")
    if p.get("available_balance_usdt") is not None:
        parts.append(f"  Balance:    {p['available_balance_usdt']:,.2f} USDT")
    if p.get("total_margin_used_usdt") is not None:
        parts.append(f"  Margin:       {p['total_margin_used_usdt']:,.2f} USDT")
    return "\n".join(parts) if parts else "  n/a"


def _pnl_realized_lines(p: dict) -> str:
    parts = []
    if p.get("gross_pnl") is not None:
        sign = "+" if p["gross_pnl"] >= 0 else ""
        parts.append(f"  Gross PnL:   {sign}{p['gross_pnl']:.2f} USDT")
    if p.get("total_fees") is not None:
        parts.append(f"  Fees:         {p['total_fees']:.2f} USDT")
    if p.get("pnl_net") is not None:
        sign = "+" if p["pnl_net"] >= 0 else ""
        parts.append(f"  Netto:       {sign}{p['pnl_net']:.2f} USDT")
    return "\n".join(parts) if parts else "  n/a"


_PNL_BLOCKS: list = [
    *_cmd_header("💰", "PNL"),
    DerivedBlock(text_fn=lambda p: (
        f"Account: {p.get('account_id_inner') or 'n/a'}  |  {p.get('captured_at') or p.get('updated_at') or 'n/a'}"
    )),
    SeparatorBlock(),
    StaticBlock("Snapshot account:"),
    DerivedBlock(text_fn=_pnl_snapshot_lines),
    SeparatorBlock(),
    StaticBlock("Realizzato (trade chiusi):"),
    DerivedBlock(text_fn=_pnl_realized_lines),
    SeparatorBlock(),
    DerivedBlock(text_fn=lambda p: (
        f"Posizioni aperte: {p.get('open_count', 0)}  |  Waiting: {p.get('waiting_entry_count', 0)}"
    )),
]

TEMPLATE_PNL = TemplateConfig(_PNL_BLOCKS, payload_transform=None)


# ---------------------------------------------------------------------------
# STATS template
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
    """Format a signed float with 2 decimal places."""
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
    return str(int(value))


_STATS_TABLE = TableBlock(
    rows_key="stats_rows",
    columns=[
        ("", "label", 10, str),
        ("Trades", "trade_count", 6, _fmt_trade_count),
        ("Win%", "win_pct", 5, _fmt_win_pct),
        ("PnL netto", "pnl_net", 10, _fmt_signed_float),
        ("Fees", "fees", 8, _fmt_signed_float),
    ],
    show_header=True,
    fallback="—",
)

_STATS_BLOCKS: list = [
    *_cmd_header("📈", "STATS"),
    _STATS_TABLE,
    SeparatorBlock(),
    ConditionalBlock(
        condition=lambda p: p.get("best_chain_id") is not None,
        blocks=[
            DerivedBlock(text_fn=lambda p: (
                f"Best trade:   #{p['best_chain_id']} {p.get('best_symbol', '')} {money_signed(p.get('best_pnl'))}"
                if p.get("best_pnl") is not None
                else f"Best trade:   #{p['best_chain_id']}"
            )),
        ]
    ),
    ConditionalBlock(
        condition=lambda p: p.get("worst_chain_id") is not None,
        blocks=[
            DerivedBlock(text_fn=lambda p: (
                f"Worst trade:  #{p['worst_chain_id']} {p.get('worst_symbol', '')} {money_signed(p.get('worst_pnl'))}"
                if p.get("worst_pnl") is not None
                else f"Worst trade:  #{p['worst_chain_id']}"
            )),
        ]
    ),
    SeparatorBlock(),
    DerivedBlock(text_fn=lambda p: (
        f"/stats {(p.get('trader_id') or 'trader_a')}  per filtrare per trader"
    )),
]

TEMPLATE_STATS = TemplateConfig(_STATS_BLOCKS, payload_transform=None)


# ---------------------------------------------------------------------------
# STATUS template
# ---------------------------------------------------------------------------

_STATUS_BLOCKS: list = [
    DerivedBlock(text_fn=lambda p: f"{p.get('_level', '🟢')} Runtime V2 — STATUS — {p.get('account_id', '')}"),
    SeparatorBlock(),
    DerivedBlock(text_fn=lambda p: f"Updated: {p.get('updated_at', 'n/a')}"),
    StaticBlock(""),
    StaticBlock("Mode:"),
    DerivedBlock(text_fn=lambda p: f"New entries: {'ENABLED' if p.get('new_entries_enabled') else 'BLOCKED'}"),
    DerivedBlock(text_fn=lambda p: f"Control: {p.get('control_mode', 'n/a')}"),
    DerivedBlock(text_fn=lambda p: f"Sync: {p.get('_sync_str', 'n/a')}"),
    StaticBlock(""),
    StaticBlock("Trades:"),
    DerivedBlock(text_fn=lambda p: f"Open: {p.get('open_count', 0)}"),
    DerivedBlock(text_fn=lambda p: f"Waiting entry: {p.get('waiting_entry_count', 0)}"),
    DerivedBlock(text_fn=lambda p: f"Partial: {p.get('partial_count', 0)}"),
    DerivedBlock(text_fn=lambda p: f"Review required: {p.get('review_count', 0)}"),
    StaticBlock(""),
    StaticBlock("Execution:"),
    DerivedBlock(text_fn=lambda p: f"Pending commands: {p.get('pending_commands', 0)}"),
    DerivedBlock(text_fn=lambda p: f"Failed commands: {p.get('failed_commands', 0)}"),
    StaticBlock(""),
    StaticBlock("Risk:"),
    DerivedBlock(text_fn=lambda p: f"No SL: {p.get('no_sl_count', 0)}"),
    StaticBlock(""),
    StaticBlock("Use:"),
    StaticBlock("/trades"),
    StaticBlock("/reviews"),
    StaticBlock("/control"),
]

TEMPLATE_STATUS = TemplateConfig(_STATUS_BLOCKS, payload_transform=None)


# ---------------------------------------------------------------------------
# CONTROL template
# ---------------------------------------------------------------------------

def _render_block_item(b: dict, i: int, p: dict) -> list[str]:
    scope = b.get("scope_value") or "GLOBAL"
    mode = b.get("mode", "")
    when = f" ({b['created_at']})" if b.get("created_at") else ""
    return [f"{scope} — {mode}{when}"]


_CONTROL_BLOCKS: list = [
    StaticBlock("🛡️ CONTROL"),
    SeparatorBlock(),
    DerivedBlock(text_fn=lambda p: f"New entries: {'ENABLED' if p.get('new_entries_enabled') else 'BLOCKED'}"),
    StaticBlock("Open positions: managed"),
    StaticBlock("Updates: processed"),
    StaticBlock(""),
    ConditionalBlock(
        condition=lambda p: bool(p.get("active_blocks")),
        blocks=[
            StaticBlock("Active blocks:"),
            ListBlock(key="active_blocks", item_renderer=_render_block_item),
        ],
    ),
    ConditionalBlock(
        condition=lambda p: not p.get("active_blocks"),
        blocks=[StaticBlock("Active blocks: none")],
    ),
    StaticBlock(""),
    StaticBlock("Symbol blacklist:"),
    DerivedBlock(text_fn=lambda p: "Global: " + (
        ", ".join(p.get("blacklist_global") or []) or "none"
    )),
    ConditionalBlock(
        condition=lambda p: bool(p.get("blacklist_per_trader")),
        blocks=[
            StaticBlock("Per trader:"),
            ListBlock(
                key="blacklist_per_trader_lines",
                item_renderer=lambda line, i, p: [line],
            ),
        ],
    ),
    ConditionalBlock(
        condition=lambda p: not p.get("blacklist_per_trader"),
        blocks=[StaticBlock("Per trader: none")],
    ),
]

TEMPLATE_CONTROL = TemplateConfig(_CONTROL_BLOCKS, payload_transform=None)


# ---------------------------------------------------------------------------
# HEALTH template (passthrough — format_health stays as-is, but alias here)
# ---------------------------------------------------------------------------

_HEALTH_BLOCKS: list = [
    StaticBlock("💊 HEALTH"),
    SeparatorBlock(),
    DerivedBlock(text_fn=lambda p: f"Updated: {p.get('updated_at', 'n/a')}"),
    StaticBlock(""),
    StaticBlock("Workers:"),
    ListBlock(
        key="workers",
        item_renderer=lambda w, i, p: [
            f"{w[0]}: {w[1]}" + (f" — {w[2]}" if w[2] else "")
        ],
    ),
    StaticBlock(""),
    StaticBlock("DB:"),
    DerivedBlock(text_fn=lambda p: f"ops.sqlite3: {'OK' if p.get('db_ok') else 'ERROR'}"),
    StaticBlock(""),
    StaticBlock("Exchange:"),
    DerivedBlock(text_fn=lambda p: f"Connected: {'YES' if p.get('exchange_connected') else 'NO'}"),
]

TEMPLATE_HEALTH = TemplateConfig(_HEALTH_BLOCKS, payload_transform=None)


# ---------------------------------------------------------------------------
# REVIEWS template
# ---------------------------------------------------------------------------

def _render_review_item(item: dict, i: int, p: dict) -> list[str]:
    chain_id = item.get("chain_id")
    symbol = item.get("symbol")
    reason = item.get("reason", "unknown")
    parts = []
    if chain_id is not None:
        parts.append(f"#{chain_id}")
    if symbol:
        parts.append(symbol)
    parts.append(f"— {reason}")
    return [" ".join(parts)]


_REVIEWS_BLOCKS: list = [
    StaticBlock("⚠️ REVIEWS"),
    SeparatorBlock(),
    DerivedBlock(text_fn=lambda p: f"Updated: {p.get('updated_at', 'n/a')}"),
    StaticBlock(""),
    ConditionalBlock(
        condition=lambda p: not p.get("items"),
        blocks=[StaticBlock("No pending reviews.")],
    ),
    ConditionalBlock(
        condition=lambda p: bool(p.get("items")),
        blocks=[ListBlock(key="items", item_renderer=_render_review_item)],
    ),
]

TEMPLATE_REVIEWS = TemplateConfig(_REVIEWS_BLOCKS, payload_transform=None)


# ---------------------------------------------------------------------------
# TEMPLATE_REGISTRY
# ---------------------------------------------------------------------------

TEMPLATE_REGISTRY: dict[str, TemplateConfig] = {
    "trades":  TEMPLATE_TRADES,
    "pnl":     TEMPLATE_PNL,
    "stats":   TEMPLATE_STATS,
    "status":  TEMPLATE_STATUS,
    "health":  TEMPLATE_HEALTH,
    "control": TEMPLATE_CONTROL,
    "reviews": TEMPLATE_REVIEWS,
}
