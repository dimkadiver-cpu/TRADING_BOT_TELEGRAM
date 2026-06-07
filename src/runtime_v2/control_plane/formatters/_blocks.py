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


@dataclass
class SectionBlock:
    """Static label + sub-blocks."""
    label: str
    blocks: list


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
                lines.append(lbl)
                _render_blocks(sub, p, lines)
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
    if block.include_trader_id and p.get("trader_id"):
        lines.append(f"Trader: {p['trader_id']}")
    if block.include_account_id and p.get("account_id"):
        lines.append(f"Exchange Account: {p['account_id']}")
    if block.include_rejected_reason and p.get("reason"):
        lines.append(f"Rejected: {p['reason']}")
    source = p.get(block.source_key) or block.default_source
    lines.append(f"Source: {source}")
    link = p.get(block.link_key)
    if link:
        lines.extend([_SEP, link])


__all__ = [
    "SeparatorBlock", "StaticBlock", "DerivedBlock", "HeaderBlock",
    "FieldBlock", "SectionBlock", "ConditionalBlock", "BranchBlock",
    "ListBlock", "FooterBlock", "TemplateConfig",
    "_SEP", "_BULLET",
    "render_template",
]
