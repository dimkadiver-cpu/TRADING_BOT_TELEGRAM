# src/runtime_v2/control_plane/formatters/templates/clean_log.py
from __future__ import annotations

from src.runtime_v2.control_plane.formatters._blocks import (
    _SEP, _BULLET,
    SeparatorBlock, StaticBlock, DerivedBlock, HeaderBlock,
    FieldBlock, ConditionalBlock, BranchBlock, ListBlock, FooterBlock,
    TemplateConfig,
)
from src.runtime_v2.control_plane.formatters._formatters import (
    num, text, money, money_signed, pct, pct_signed, fee_rate,
)
from src.runtime_v2.control_plane.formatters.display import display_symbol


# ---------------------------------------------------------------------------
# Shared item renderers (used as item_renderer in ListBlock)
# ---------------------------------------------------------------------------

def _render_entry_item(entry: dict, i: int, p: dict) -> list[str]:
    seq = entry.get("sequence", i)
    etype = entry.get("entry_type", "LIMIT")
    price = entry.get("price")
    if etype == "MARKET":
        price_str = f"Market ~{num(price)}" if price is not None else "Market"
    else:
        price_str = f"{num(price)} Limit" if price is not None else "Limit"
    pcts = p.get("_entry_pcts") or []
    pct_suffix = f" ({pcts[i - 1]}%)" if len(pcts) >= 2 and i <= len(pcts) else ""
    return [f"Entry_{seq}: {price_str}{pct_suffix}"]


def _render_tp_item(tp: object, i: int, p: dict) -> list[str]:
    pcts = p.get("_tp_pcts") or []
    pct_suffix = f" ({pcts[i - 1]}%)" if len(pcts) >= 2 and i <= len(pcts) else ""
    return [f"TP_{i}: {num(tp)}{pct_suffix}"]


def _render_pending_entry(entry: dict, i: int, p: dict) -> list[str]:
    seq = entry.get("sequence", "?")
    price = entry.get("price")
    etype = entry.get("entry_type", "LIMIT").capitalize()
    price_str = num(price) if price is not None else "?"
    return [f"Pending: Entry_{seq} {price_str} {etype}"]


def _render_changed_item(item: object, i: int, p: dict) -> list[str]:
    if isinstance(item, dict):
        field_name = item.get("field", "?")
        value = f"{num(item.get('old'))} → {num(item.get('new'))}"
        note = item.get("note")
        if note:
            return [f"{_BULLET} {field_name}: {value} *"]
        return [f"{_BULLET} {field_name}: {value}"]
    return [f"{_BULLET} {item}"]
