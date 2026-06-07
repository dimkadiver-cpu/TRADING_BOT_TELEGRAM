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


# Renderer will be added in Task 3 — leave render_template as a forward declaration
def render_template(blocks: list, payload: dict, *, transform: Callable[[dict], dict] | None = None) -> str:
    raise NotImplementedError("Renderer not yet implemented — Task 3 will define this")


__all__ = [
    "SeparatorBlock", "StaticBlock", "DerivedBlock", "HeaderBlock",
    "FieldBlock", "SectionBlock", "ConditionalBlock", "BranchBlock",
    "ListBlock", "FooterBlock", "TemplateConfig",
    "_SEP", "_BULLET",
    "render_template",
]
