# src/runtime_v2/control_plane/formatters/_blocks.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from src.runtime_v2.control_plane.formatters._formatters import num
from src.runtime_v2.control_plane.formatters.display import display_symbol

_SEP = "__SEP__"
_BULLET = "▪️"


# ---------------------------------------------------------------------------
# Block primitives
# ---------------------------------------------------------------------------

@dataclass
class SeparatorBlock:
    pass


@dataclass
class StaticBlock:
    text: str


@dataclass
class DerivedBlock:
    text_fn: Callable[[dict], str]


# ---------------------------------------------------------------------------
# Block data
# ---------------------------------------------------------------------------

@dataclass
class HeaderBlock:
    """Header: emoji + chain_id + event_label; SEP; symbol/side (if both present);
    signal_link (if present); SEP. Do NOT add SeparatorBlock after HeaderBlock."""
    emoji: str | Callable[[dict], str]
    event_label: str | Callable[[dict], str]


@dataclass
class FieldBlock:
    """Single 'Label: value' line. Use key OR value_fn, not both."""
    label: str | Callable[[dict], str]
    key: str | None = None
    value_fn: Callable[[dict], Any] | None = None
    fmt: Callable[[Any], str] = field(default_factory=lambda: num)
    optional: bool = True
    default: str = "n/a"

    def __post_init__(self) -> None:
        if self.key is not None and self.value_fn is not None:
            raise ValueError("FieldBlock: set key OR value_fn, not both")


@dataclass
class SectionBlock:
    """Static label + sub-blocks. label may be callable(payload) -> str."""
    label: str | Callable[[dict], str]
    blocks: list


@dataclass
class TableBlock:
    """Renders aligned columnar data.
    rows_key: payload key containing list of dicts.
    columns: list of (header_label, row_key, min_width, fmt_fn).
    fmt_fn receives the raw cell value and returns a string.
    """
    rows_key: str
    columns: list[tuple[str, str, int, Callable]]
    show_header: bool = True
    fallback: str = "—"


# ---------------------------------------------------------------------------
# Block structural
# ---------------------------------------------------------------------------

@dataclass
class ConditionalBlock:
    """Renders sub-blocks only if condition(payload) is True."""
    condition: Callable[[dict], bool]
    blocks: list


@dataclass
class BranchBlock:
    """Declarative if/else."""
    condition: Callable[[dict], bool]
    then_blocks: list
    else_blocks: list = field(default_factory=list)


@dataclass
class ListBlock:
    """Iterates a list in payload. item_renderer(item, index, payload) -> list[str].
    index starts at index_start (default 1). item_renderer may return _SEP sentinel."""
    key: str
    item_renderer: Callable[[Any, int, dict], list[str]]
    fallback_key: str | None = None
    index_start: int = 1


@dataclass
class FooterBlock:
    """_SEP + optional trader/account/reason + Source + optional link.
    Do NOT add SeparatorBlock before FooterBlock — it emits _SEP internally."""
    source_key: str = "source"
    default_source: str = "runtime"
    link_key: str = "link"
    include_trader_id: bool = False
    include_account_id: bool = False
    include_rejected_reason: bool = False


# ---------------------------------------------------------------------------
# Template config
# ---------------------------------------------------------------------------

@dataclass
class TemplateConfig:
    blocks: list
    payload_transform: Callable[[dict], dict] | None = None


# ---------------------------------------------------------------------------
# Renderer helpers
# ---------------------------------------------------------------------------

def _side_emoji(side: str | None) -> str:
    if side == "LONG":
        return "\U0001f4c8"
    if side == "SHORT":
        return "\U0001f4c9"
    return "•"


def _separator(width: int) -> str:
    dash_count = max(4, (max(width, 1) + 1) // 2)
    return " ".join("-" for _ in range(dash_count))


def _finalize(lines: list[str]) -> str:
    width = max((len(line) for line in lines if line and line != _SEP), default=8)
    sep = _separator(width)
    return "\n".join(sep if line == _SEP else line for line in lines)


# ---------------------------------------------------------------------------
# Block render dispatch
# ---------------------------------------------------------------------------

def render_template(
    blocks: list,
    payload: dict,
    *,
    transform: Callable[[dict], dict] | None = None,
) -> str:
    p = transform(payload) if transform else payload
    lines: list[str] = []
    _render_blocks(blocks, p, lines)
    return _finalize(lines)


def _render_blocks(blocks: list, p: dict, lines: list[str]) -> None:
    for block in blocks:
        match block:
            case SeparatorBlock():
                lines.append(_SEP)
            case StaticBlock(text=t):
                lines.append(t)
            case DerivedBlock(text_fn=fn):
                result = fn(p)
                if result:
                    lines.append(result)
            case HeaderBlock():
                _render_header(block, p, lines)
            case FieldBlock():
                _render_field(block, p, lines)
            case SectionBlock(label=lbl, blocks=sub):
                lines.append(lbl(p) if callable(lbl) else lbl)
                _render_blocks(sub, p, lines)
            case TableBlock():
                _render_table(block, p, lines)
            case ConditionalBlock(condition=cond, blocks=sub):
                if cond(p):
                    _render_blocks(sub, p, lines)
            case BranchBlock(condition=cond, then_blocks=tb, else_blocks=eb):
                _render_blocks(tb if cond(p) else eb, p, lines)
            case ListBlock():
                _render_list(block, p, lines)
            case FooterBlock():
                _render_footer(block, p, lines)


def _render_header(block: HeaderBlock, p: dict, lines: list[str]) -> None:
    emoji = block.emoji(p) if callable(block.emoji) else block.emoji
    event_label = block.event_label(p) if callable(block.event_label) else block.event_label
    chain_id = p.get("chain_id")
    id_part = f" #{chain_id}" if chain_id is not None else ""
    lines.append(f"{emoji}{id_part} — {event_label}")
    lines.append(_SEP)
    symbol = p.get("symbol")
    side = p.get("side")
    if symbol and side:
        lines.append(f"{display_symbol(symbol)} — {_side_emoji(side)} {side}")
    signal_link = p.get("signal_link")
    if signal_link:
        lines.append(signal_link)
    lines.append(_SEP)


def _render_field(block: FieldBlock, p: dict, lines: list[str]) -> None:
    value = block.value_fn(p) if block.value_fn else p.get(block.key)
    if value is None and block.optional:
        return
    label = block.label(p) if callable(block.label) else block.label
    formatted = block.fmt(value) if value is not None else block.default
    lines.append(f"{label}: {formatted}")


def _render_list(block: ListBlock, p: dict, lines: list[str]) -> None:
    items = p.get(block.key)
    if not items and block.fallback_key:
        items = p.get(block.fallback_key)
    for i, item in enumerate(items or [], start=block.index_start):
        lines.extend(block.item_renderer(item, i, p))


def _render_footer(block: FooterBlock, p: dict, lines: list[str]) -> None:
    lines.append(_SEP)
    has_meta = False
    if block.include_trader_id and p.get("trader_id"):
        lines.append(f"Trader: {p['trader_id']}")
        has_meta = True
    if block.include_account_id and p.get("account_id"):
        lines.append(f"Exchange Account: {p['account_id']}")
        has_meta = True
    if block.include_rejected_reason and p.get("reason"):
        lines.append(f"Rejected: {p['reason']}")
        has_meta = True
    if has_meta:
        lines.append(_SEP)
    source = p.get(block.source_key) or block.default_source
    lines.append(f"Source: {source}")
    link = p.get(block.link_key)
    if link:
        lines.append(link)


def _render_table(block: TableBlock, p: dict, lines: list[str]) -> None:
    rows = p.get(block.rows_key) or []
    columns = block.columns  # [(header, row_key, min_width, fmt_fn), ...]

    # Build all cell strings first
    headers = [col[0] for col in columns]
    cell_rows: list[list[str]] = []
    for row in rows:
        cells: list[str] = []
        for _header, row_key, _min_w, fmt_fn in columns:
            raw = row.get(row_key)
            cells.append(fmt_fn(raw) if raw is not None else block.fallback)
        cell_rows.append(cells)

    # Compute column widths
    col_widths: list[int] = []
    for col_idx, (header, _rk, min_w, _fn) in enumerate(columns):
        w = max(min_w, len(header))
        for cr in cell_rows:
            w = max(w, len(cr[col_idx]))
        col_widths.append(w)

    if block.show_header:
        header_line = "  ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers))
        lines.append(header_line.rstrip())

    for cr in cell_rows:
        row_line = "  ".join(cr[i].ljust(col_widths[i]) for i in range(len(cr)))
        lines.append(row_line.rstrip())


__all__ = [
    "SeparatorBlock", "StaticBlock", "DerivedBlock", "HeaderBlock",
    "FieldBlock", "SectionBlock", "TableBlock", "ConditionalBlock", "BranchBlock",
    "ListBlock", "FooterBlock", "TemplateConfig",
    "_SEP", "_BULLET",
    "render_template",
]
