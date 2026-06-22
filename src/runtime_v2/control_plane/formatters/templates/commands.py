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
from src.runtime_v2.control_plane.formatters.templates._shared import (
    _cmd_header,
    _side_emoji_str,
    _protection_str,
    _pnl_str,
    _render_trade_item,
)

# Re-export so existing importers of these names from commands still work
__all_shared__ = [_cmd_header, _side_emoji_str, _protection_str, _pnl_str, _render_trade_item]


_FRESHNESS_WARNING = ConditionalBlock(
    condition=lambda p: bool(p.get("_mark_stale")),
    blocks=[
        StaticBlock("⚠️ Snapshot oltre intervallo riconciliazione — PnL aperta non aggiornata"),
    ],
)

_TRADES_BLOCKS: list = [
    *_cmd_header("📊", "TRADES"),
    DerivedBlock(text_fn=lambda p: (
        f"Total: {p.get('total', 0)}   Updated: {p.get('updated_at', 'n/a')}"
        + (f"  |  Mark: {p['_mark_time']} ({p['_mark_age']}s)"
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
    if p.get("fees_usdt") is not None:
        parts.append(f"  Fees:         {p['fees_usdt']:.2f} USDT")
    if p.get("funding_usdt") is not None:
        parts.append(f"  Funding:       {p['funding_usdt']:.2f} USDT")
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
    DerivedBlock(text_fn=lambda p: (
        f"{p.get('_level', '🟢')} Runtime V2 — STATUS  |  "
        + (p.get("account_id") or "All accounts")
    )),
    SeparatorBlock(),
    DerivedBlock(text_fn=lambda p: f"Updated: {p.get('updated_at', 'n/a')}"),
    StaticBlock(""),
    StaticBlock("Mode:"),
    DerivedBlock(text_fn=lambda p: f"  New entries: {'ENABLED' if p.get('new_entries_enabled') else 'BLOCKED'}"),
    DerivedBlock(text_fn=lambda p: f"  Control: {p.get('control_mode', 'NONE')}"),
    DerivedBlock(text_fn=lambda p: f"  Sync: {p.get('_sync_str', 'n/a')}"),
    StaticBlock(""),
    StaticBlock("Trades:"),
    DerivedBlock(text_fn=lambda p: f"  Open: {p.get('open_count', 0)}"),
    DerivedBlock(text_fn=lambda p: f"  Waiting entry: {p.get('waiting_entry_count', 0)}"),
    DerivedBlock(text_fn=lambda p: f"  Partial: {p.get('partial_count', 0)}"),
    DerivedBlock(text_fn=lambda p: (
        f"  Review required: {p.get('review_count', 0)}"
        + ("  ⚠️" if p.get('review_count', 0) > 0 else "")
    )),
    StaticBlock(""),
    StaticBlock("Execution:"),
    DerivedBlock(text_fn=lambda p: f"  Pending commands: {p.get('pending_commands', 0)}"),
    DerivedBlock(text_fn=lambda p: (
        f"  Failed commands: {p.get('failed_commands', 0)}"
        + ("  🔴" if p.get('failed_commands', 0) > 0 else "")
    )),
    StaticBlock(""),
    StaticBlock("Risk:"),
    DerivedBlock(text_fn=lambda p: (
        f"  No SL: {p.get('no_sl_count', 0)}"
        + ("  🔴" if p.get('no_sl_count', 0) > 0 else "")
    )),
    # By account breakdown (only shown in global scope)
    ConditionalBlock(
        condition=lambda p: bool(p.get("by_account")),
        blocks=[
            StaticBlock(""),
            StaticBlock("By account:"),
            ListBlock(key="by_account", item_renderer=lambda a, i, p: [
                f"  {a['account_id']}  Open: {a['open_count']}  "
                f"Waiting: {a['waiting_count']}  Failed: {a['failed_commands']}"
            ]),
        ],
    ),
    StaticBlock(""),
    StaticBlock("/trades  ·  /reviews  ·  /control"),
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
# HEALTH template — delegates to health.py (block system template)
# ---------------------------------------------------------------------------

from src.runtime_v2.control_plane.formatters.health import TEMPLATE_HEALTH


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
# TEMPLATE_REGISTRY — unified registry (spec §7)
# Aggregates read-only, emergency, and dashboard templates in one dict.
# emergency.py and dashboard.py keep their own module-level registries for
# internal use; those registries are also merged here so callers have a
# single import point.
# ---------------------------------------------------------------------------

from src.runtime_v2.control_plane.formatters.templates.emergency import EMERGENCY_REGISTRY
from src.runtime_v2.control_plane.formatters.templates.dashboard import DASHBOARD_TEMPLATE_REGISTRY

TEMPLATE_REGISTRY: dict[str, TemplateConfig] = {
    # Read-only commands
    "trades":  TEMPLATE_TRADES,
    "pnl":     TEMPLATE_PNL,
    "stats":   TEMPLATE_STATS,
    "status":  TEMPLATE_STATUS,
    "health":  TEMPLATE_HEALTH,
    "control": TEMPLATE_CONTROL,
    "reviews": TEMPLATE_REVIEWS,
    # Emergency (close_all, close_single, cancel_all — preview + results)
    **EMERGENCY_REGISTRY,
    # Dashboard views
    **DASHBOARD_TEMPLATE_REGISTRY,
}
