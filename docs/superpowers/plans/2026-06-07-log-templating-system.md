# Log Templating System — Block-based DSL Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor `clean_log.py` (658 lines, 23 hardcoded notification type functions) into a declarative block-based DSL. Public API (`format_clean_log`) is unchanged; internals are completely replaced.

**Architecture:** `_blocks.py` provides the renderer engine and dataclasses; `_formatters.py` provides 7 formatting utilities; `templates/clean_log.py` holds all 23 templates + `TEMPLATE_REGISTRY`; `clean_log.py` becomes a ~25-line thin dispatcher.

**Tech Stack:** Python 3.12+, stdlib `dataclasses`, zero new dependencies. `from __future__ import annotations` in every file.

---

## Split assessment

**One plan is the right call.** The spec describes one subsystem. Tasks 1–3 are infrastructure-only (no behavior change — existing tests still pass after them). Tasks 4–11 complete the migration. You can stop after Task 3 for a review if desired, but there's no gain from a separate plan since the infrastructure is never exercised until the templates are wired up.

---

## File map

| File | Action | What changes |
|------|--------|--------------|
| `src/runtime_v2/control_plane/formatters/_formatters.py` | **Create** | 7 formatting utilities extracted from clean_log.py |
| `src/runtime_v2/control_plane/formatters/_blocks.py` | **Create** | Block dataclasses + renderer engine + `_finalize` |
| `src/runtime_v2/control_plane/formatters/templates/__init__.py` | **Create** | Package marker (empty) |
| `src/runtime_v2/control_plane/formatters/templates/clean_log.py` | **Create** | All 23 templates, shared renderers, TEMPLATE_REGISTRY |
| `src/runtime_v2/control_plane/formatters/clean_log.py` | **Rewrite** | Thin dispatcher replacing 650+ lines |
| `tests/runtime_v2/control_plane/test_blocks_formatters.py` | **Create** | Unit tests for _blocks.py and _formatters.py |
| `tests/runtime_v2/control_plane/test_clean_log_formatter.py` | **Update** | Fix assertions for 6 broken tests |
| `tests/runtime_v2/control_plane/test_clean_log_formatter_full.py` | **Update** | Fix assertions for 3 broken tests |

---

## Behavioral differences old → new (read before Task 11)

| Notification type | Old behavior | New behavior |
|-------------------|-------------|-------------|
| `UPDATE_DONE` / `PARTIAL` / `REJECTED` | `->` arrow in changed items | `→` arrow (Unicode U+2192) |
| `UPDATE_PARTIAL` | "Applied:" + "Rejected:" sections | Unified "Operation:" with `*` suffix on failed ops; footnotes after SEP |
| `UPDATE_REJECTED` | "Reason: x" label | "Failed: x" label |
| `UPDATE_DONE` | `changed_fields` list shown | `changed_fields` is dead code — not rendered |
| `UPDATE_DONE` | `display_lines` shown as-is | `display_lines` backward-compat: converted to bullet lines in Changed section |
| `PARTIAL_CLOSE_EXECUTED` | event label "UPDATE DONE" | event label "PARTIAL CLOSED" |
| `CANCEL_FAILED` | "Requires manual review required to resolve" (typo) | "Requires manual review to resolve the position." |
| `SL_FILLED` + `close_reason=BREAKEVEN_AFTER_TP` | Routed to BE_EXIT behavior | Preserved in dispatcher as special case |
| All closed types | No `RoR` in Final Result | Adds `RoR:` field between ROI net and Total PnL net |

---

## ════════ INFRASTRUCTURE PHASE (Tasks 1–3) ════════

## Task 1: `_formatters.py`

**Files:**
- Create: `src/runtime_v2/control_plane/formatters/_formatters.py`
- Create: `tests/runtime_v2/control_plane/test_blocks_formatters.py`

- [ ] **Step 1: Write `_formatters.py`**

```python
# src/runtime_v2/control_plane/formatters/_formatters.py
from __future__ import annotations


def num(value) -> str:
    if value is None:
        return "n/a"
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if f == int(f) and abs(f) < 1e15:
        return f"{int(f):,}"
    formatted = f"{f:.8g}"
    if "e" not in formatted and "." in formatted:
        int_part, dec_part = formatted.split(".")
        try:
            int_part = f"{int(int_part):,}"
        except ValueError:
            pass
        return f"{int_part}.{dec_part}"
    return formatted


def text(value) -> str:
    if value is None:
        return "n/a"
    return str(value)


def money(value) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.2f} USDT"


def money_signed(value) -> str:
    if value is None:
        return "n/a"
    number = float(value)
    prefix = "+" if number >= 0 else ""
    return f"{prefix}{number:.2f} USDT"


def pct(value) -> str:
    if value is None:
        return "n/a"
    number = float(value)
    result = f"{number:.2f}%"
    return result.replace(".00%", "%")


def pct_signed(value) -> str:
    if value is None:
        return "n/a"
    number = float(value)
    prefix = "+" if number >= 0 else ""
    result = f"{prefix}{number:.2f}%"
    return result.replace(".00%", "%")


def fee_rate(value) -> str:
    if value is None:
        return "n/a"
    return f"{float(value) * 100:.3f}%"


__all__ = ["num", "text", "money", "money_signed", "pct", "pct_signed", "fee_rate"]
```

- [ ] **Step 2: Write formatter unit tests**

```python
# tests/runtime_v2/control_plane/test_blocks_formatters.py
from __future__ import annotations

from src.runtime_v2.control_plane.formatters._formatters import (
    num, text, money, money_signed, pct, pct_signed, fee_rate,
)


def test_num_none():        assert num(None) == "n/a"
def test_num_int():         assert num(65000) == "65,000"
def test_num_float():       assert num(65020.5) == "65,020.5"
def test_num_small():       assert num(0.004) == "0.004"
def test_num_zero():        assert num(0) == "0"
def test_num_str_bad():     assert num("abc") == "abc"

def test_text_none():       assert text(None) == "n/a"
def test_text_str():        assert text("hello") == "hello"

def test_money_none():      assert money(None) == "n/a"
def test_money_pos():       assert money(12.34) == "12.34 USDT"
def test_money_neg():       assert money(-5.00) == "-5.00 USDT"

def test_money_signed_none():    assert money_signed(None) == "n/a"
def test_money_signed_pos():     assert money_signed(12.34) == "+12.34 USDT"
def test_money_signed_neg():     assert money_signed(-5.00) == "-5.00 USDT"
def test_money_signed_zero():    assert money_signed(0.0) == "+0.00 USDT"

def test_pct_none():        assert pct(None) == "n/a"
def test_pct_whole():       assert pct(30.0) == "30%"
def test_pct_frac():        assert pct(12.34) == "12.34%"

def test_pct_signed_pos():  assert pct_signed(5.0) == "+5%"
def test_pct_signed_neg():  assert pct_signed(-5.17) == "-5.17%"

def test_fee_rate_none():   assert fee_rate(None) == "n/a"
def test_fee_rate():        assert fee_rate(0.001) == "0.100%"
```

- [ ] **Step 3: Run formatter tests — expect PASS**

```
pytest tests/runtime_v2/control_plane/test_blocks_formatters.py -v
```

Expected: all pass.

- [ ] **Step 4: Commit**

```
git add src/runtime_v2/control_plane/formatters/_formatters.py tests/runtime_v2/control_plane/test_blocks_formatters.py
git commit -m "feat: add _formatters.py with 7 format utilities"
```

---

## Task 2: `_blocks.py` — Dataclasses

**Files:**
- Create: `src/runtime_v2/control_plane/formatters/_blocks.py`

- [ ] **Step 1: Write block dataclasses and `TemplateConfig`**

```python
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
    blocks: list[Block]


# ---------------------------------------------------------------------------
# Block structural
# ---------------------------------------------------------------------------

@dataclass
class ConditionalBlock:
    """Renders sub-blocks only if condition(payload) is True."""
    condition: Callable[[dict], bool]
    blocks: list[Block]


@dataclass
class BranchBlock:
    """Declarative if/else."""
    condition: Callable[[dict], bool]
    then_blocks: list[Block]
    else_blocks: list[Block] = field(default_factory=list)


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


Block = (
    SeparatorBlock | StaticBlock | DerivedBlock | HeaderBlock | FieldBlock
    | SectionBlock | ConditionalBlock | BranchBlock | ListBlock | FooterBlock
)


# ---------------------------------------------------------------------------
# Template config
# ---------------------------------------------------------------------------

@dataclass
class TemplateConfig:
    blocks: list[Block]
    payload_transform: Callable[[dict], dict] | None = None


__all__ = [
    "SeparatorBlock", "StaticBlock", "DerivedBlock", "HeaderBlock",
    "FieldBlock", "SectionBlock", "ConditionalBlock", "BranchBlock",
    "ListBlock", "FooterBlock", "Block", "TemplateConfig",
    "_SEP", "_BULLET",
    "render_template",
]
```

- [ ] **Step 2: Add dataclass instantiation tests to `test_blocks_formatters.py`**

Append to the existing test file:

```python
# --- block dataclass tests ---
from src.runtime_v2.control_plane.formatters._blocks import (
    SeparatorBlock, StaticBlock, DerivedBlock, HeaderBlock, FieldBlock,
    SectionBlock, ConditionalBlock, BranchBlock, ListBlock, FooterBlock,
    TemplateConfig, _SEP, _BULLET,
)


def test_sep_constant():        assert _SEP == "__SEP__"
def test_bullet_constant():     assert _BULLET == "▪️"

def test_separator_block():     assert SeparatorBlock() is not None
def test_static_block():        assert StaticBlock("hi").text == "hi"
def test_header_block():        assert HeaderBlock("✅", "SIGNAL ACCEPTED").emoji == "✅"
def test_field_block_defaults():
    fb = FieldBlock("Label", key="k")
    assert fb.optional is True
    assert fb.default == "n/a"

def test_footer_block_defaults():
    fb = FooterBlock()
    assert fb.source_key == "source"
    assert fb.default_source == "runtime"
    assert fb.include_trader_id is False

def test_template_config():
    tc = TemplateConfig([StaticBlock("x")])
    assert tc.payload_transform is None
```

- [ ] **Step 3: Run tests — expect PASS**

```
pytest tests/runtime_v2/control_plane/test_blocks_formatters.py -v
```

- [ ] **Step 4: Commit**

```
git add src/runtime_v2/control_plane/formatters/_blocks.py tests/runtime_v2/control_plane/test_blocks_formatters.py
git commit -m "feat: add _blocks.py block dataclasses and TemplateConfig"
```

---

## Task 3: `_blocks.py` — Renderer engine

**Files:**
- Modify: `src/runtime_v2/control_plane/formatters/_blocks.py` (append renderer functions)

- [ ] **Step 1: Append renderer functions to `_blocks.py`** (add before `__all__`)

```python
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
    blocks: list[Block],
    payload: dict,
    *,
    transform: Callable[[dict], dict] | None = None,
) -> str:
    p = transform(payload) if transform else payload
    lines: list[str] = []
    _render_blocks(blocks, p, lines)
    return _finalize(lines)


def _render_blocks(blocks: list[Block], p: dict, lines: list[str]) -> None:
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
```

- [ ] **Step 2: Add renderer tests to `test_blocks_formatters.py`**

Append:

```python
# --- renderer tests ---
from src.runtime_v2.control_plane.formatters._blocks import render_template


def test_render_static():
    from src.runtime_v2.control_plane.formatters._blocks import StaticBlock
    result = render_template([StaticBlock("hello")], {})
    assert "hello" in result


def test_render_separator_dynamic_width():
    blocks = [StaticBlock("short"), SeparatorBlock(), StaticBlock("longer line here")]
    result = render_template(blocks, {})
    lines = result.split("\n")
    sep_line = lines[1]
    assert "-" in sep_line
    assert len(sep_line) >= 4


def test_render_field_optional_missing():
    blocks = [FieldBlock("Price", key="price")]
    result = render_template(blocks, {})
    assert "Price" not in result


def test_render_field_optional_present():
    blocks = [FieldBlock("Price", key="price")]
    result = render_template(blocks, {"price": 100})
    assert "Price: 100" in result


def test_render_field_not_optional():
    blocks = [FieldBlock("Price", key="price", optional=False)]
    result = render_template(blocks, {})
    assert "Price: n/a" in result


def test_render_conditional_true():
    blocks = [ConditionalBlock(condition=lambda p: p.get("show"), blocks=[StaticBlock("visible")])]
    assert "visible" in render_template(blocks, {"show": True})


def test_render_conditional_false():
    blocks = [ConditionalBlock(condition=lambda p: p.get("show"), blocks=[StaticBlock("visible")])]
    assert "visible" not in render_template(blocks, {"show": False})


def test_render_branch():
    blocks = [BranchBlock(
        condition=lambda p: p.get("flag"),
        then_blocks=[StaticBlock("yes")],
        else_blocks=[StaticBlock("no")],
    )]
    assert "yes" in render_template(blocks, {"flag": True})
    assert "no" in render_template(blocks, {"flag": False})


def test_render_list():
    blocks = [ListBlock(key="items", item_renderer=lambda x, i, p: [f"Item {i}: {x}"])]
    result = render_template(blocks, {"items": ["a", "b"]})
    assert "Item 1: a" in result
    assert "Item 2: b" in result


def test_render_header_with_chain_id():
    blocks = [HeaderBlock("✅", "TEST EVENT")]
    result = render_template(blocks, {"chain_id": 42, "symbol": "BTC/USDT", "side": "LONG"})
    assert "#42" in result
    assert "TEST EVENT" in result
    assert "BTC/USDT" in result
    assert "📈" in result


def test_render_header_no_symbol_side_omits_line():
    blocks = [HeaderBlock("✅", "TEST")]
    result = render_template(blocks, {"chain_id": 1})
    assert "None" not in result


def test_render_footer_source():
    blocks = [FooterBlock(default_source="exchange")]
    result = render_template(blocks, {})
    assert "Source: exchange" in result


def test_render_footer_link():
    blocks = [FooterBlock()]
    result = render_template(blocks, {"link": "https://t.me/c/1/2"})
    source_pos = result.find("Source:")
    link_pos = result.find("https://t.me/c/1/2")
    assert source_pos < link_pos


def test_render_footer_trader_id_hidden_by_default():
    blocks = [FooterBlock()]
    result = render_template(blocks, {"trader_id": "trader_a"})
    assert "Trader:" not in result


def test_render_footer_trader_id_shown_when_enabled():
    blocks = [FooterBlock(include_trader_id=True)]
    result = render_template(blocks, {"trader_id": "trader_a"})
    assert "Trader: trader_a" in result


def test_render_transform():
    blocks = [StaticBlock("x"), FieldBlock("V", key="_v")]
    result = render_template(blocks, {"val": 5}, transform=lambda p: {**p, "_v": p["val"] * 2})
    assert "V: 10" in result
```

- [ ] **Step 3: Run tests — expect PASS**

```
pytest tests/runtime_v2/control_plane/test_blocks_formatters.py -v
```

- [ ] **Step 4: Verify existing clean_log tests still pass (no regression)**

```
pytest tests/runtime_v2/control_plane/test_clean_log_formatter.py tests/runtime_v2/control_plane/test_clean_log_formatter_full.py -v
```

Expected: all pass (nothing in `clean_log.py` changed yet).

- [ ] **Step 5: Commit**

```
git add src/runtime_v2/control_plane/formatters/_blocks.py tests/runtime_v2/control_plane/test_blocks_formatters.py
git commit -m "feat: add _blocks.py renderer engine (render_template, _render_blocks, _finalize)"
```

---

## ════════ MIGRATION PHASE (Tasks 4–11) ════════

## Task 4: `templates/__init__.py` + shared item renderers

**Files:**
- Create: `src/runtime_v2/control_plane/formatters/templates/__init__.py`
- Create: `src/runtime_v2/control_plane/formatters/templates/clean_log.py` (skeleton + shared renderers)

- [ ] **Step 1: Create empty `templates/__init__.py`**

```python
# src/runtime_v2/control_plane/formatters/templates/__init__.py
```

- [ ] **Step 2: Create `templates/clean_log.py` with shared item renderers**

```python
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


def _render_tp_item(tp, i: int, p: dict) -> list[str]:
    pcts = p.get("_tp_pcts") or []
    pct_suffix = f" ({pcts[i - 1]}%)" if len(pcts) >= 2 and i <= len(pcts) else ""
    return [f"TP_{i}: {num(tp)}{pct_suffix}"]


def _render_pending_entry(entry: dict, i: int, p: dict) -> list[str]:
    seq = entry.get("sequence", "?")
    price = entry.get("price")
    etype = entry.get("entry_type", "LIMIT").capitalize()
    price_str = num(price) if price is not None else "?"
    return [f"Pending: Entry_{seq} {price_str} {etype}"]


def _render_changed_item(item, i: int, p: dict) -> list[str]:
    if isinstance(item, dict):
        field = item.get("field", "?")
        value = f"{num(item.get('old'))} → {num(item.get('new'))}"
        note = item.get("note")
        if note:
            return [f"{_BULLET} {field}: {value} *"]
        return [f"{_BULLET} {field}: {value}"]
    return [f"{_BULLET} {item}"]
```

- [ ] **Step 3: Run existing tests — expect PASS**

```
pytest tests/runtime_v2/control_plane/ -v --tb=short
```

- [ ] **Step 4: Commit**

```
git add src/runtime_v2/control_plane/formatters/templates/
git commit -m "feat: add templates package with shared item renderers"
```

---

## Task 5: Shared block lists

**Files:**
- Modify: `src/runtime_v2/control_plane/formatters/templates/clean_log.py` (append)

- [ ] **Step 1: Append shared block lists to `templates/clean_log.py`**

```python
# ---------------------------------------------------------------------------
# Shared block lists
# ---------------------------------------------------------------------------

CLOSE_METRICS: list = [
    FieldBlock(label=lambda p: p.get("exit_label", "Price"), key="exit_price",
               fmt=num, optional=False, default="n/a"),
    FieldBlock("Qty",      key="closed_qty",  fmt=num),
    FieldBlock("PnL",      key="pnl",         fmt=money_signed),
    FieldBlock("Fee rate", key="fee_rate",     fmt=fee_rate),
    FieldBlock("Fee",      key="fee",          fmt=money),
]

FINAL_RESULT: list = [
    SeparatorBlock(),
    StaticBlock("Final Result:"),
    FieldBlock("ROI net",       value_fn=lambda p: (p.get("final_result") or {}).get("roi_net_pct"),
               fmt=pct_signed,   optional=False, default="n/a"),
    FieldBlock("RoR",           value_fn=lambda p: (p.get("final_result") or {}).get("return_on_risk_pct"),
               fmt=pct_signed,   optional=False, default="n/a"),
    FieldBlock("Total PnL net", value_fn=lambda p: (p.get("final_result") or {}).get("total_pnl_net"),
               fmt=money_signed, optional=False, default="n/a"),
    FieldBlock("Gross PnL",     value_fn=lambda p: (p.get("final_result") or {}).get("gross_pnl"),
               fmt=money_signed, optional=False, default="n/a"),
    FieldBlock("Fees",          value_fn=lambda p: (p.get("final_result") or {}).get("fees"),
               fmt=money_signed, optional=False, default="n/a"),
    FieldBlock("Funding",       value_fn=lambda p: (p.get("final_result") or {}).get("funding"),
               fmt=money_signed, optional=False, default="n/a"),
]

_FILL_SECTION: list = [
    StaticBlock("Filled:"),
    DerivedBlock(text_fn=lambda p: (
        f"Entry_{p['filled_leg_sequence']}: {num(p['fill_price'])} "
        f"{p.get('entry_type_for_leg', 'Limit').capitalize()}"
        if p.get("filled_leg_sequence") is not None else ""
    )),
    BranchBlock(
        condition=lambda p: bool(p.get("is_partial_leg")),
        then_blocks=[
            DerivedBlock(text_fn=lambda p:
                f"Qty: {num(p['filled_qty'])} (planned: {num(p['planned_qty'])})"
            ),
        ],
        else_blocks=[FieldBlock("Qty", key="filled_qty", fmt=num)],
    ),
    FieldBlock("Value",    key="exec_value", fmt=money),
    FieldBlock("Fee rate", key="fee_rate",   fmt=fee_rate),
    FieldBlock("Fee",      key="fee",        fmt=money),
    ConditionalBlock(
        condition=lambda p: bool(p.get("is_partial_leg")),
        blocks=[FieldBlock("Partial", key="_leg_fill_pct", fmt=pct)],
    ),
    SeparatorBlock(),
]

_SIGNAL_BODY: list = [
    ListBlock(key="entries", item_renderer=_render_entry_item),
    FieldBlock("SL",   key="sl",       fmt=num),
    ListBlock(key="tps", item_renderer=_render_tp_item),
    FieldBlock("Risk", key="risk_pct", fmt=lambda v: f"{v}%"),
]

_ENTRY_POSITION_SECTION: list = [
    StaticBlock("Position:"),
    FieldBlock("Avg entry",   key="_avg_entry",          fmt=num),
    FieldBlock("Total qty",   key="total_filled_qty",    fmt=num),
    FieldBlock("Total value", key="total_value",         fmt=money),
    FieldBlock("Total fees",  key="total_fees",          fmt=money),
    FieldBlock("Filled",      key="position_filled_pct", fmt=pct),
    ConditionalBlock(
        condition=lambda p: p.get("actual_risk_usdt") is not None,
        blocks=[
            DerivedBlock(text_fn=lambda p:
                f"Risk: {money(p.get('actual_risk_usdt'))} "
                f"(planned: {money(p.get('planned_risk_usdt'))})"
            ),
        ]
    ),
    BranchBlock(
        condition=lambda p: bool(p.get("pending_entries")),
        then_blocks=[ListBlock(key="pending_entries", item_renderer=_render_pending_entry)],
        else_blocks=[StaticBlock("Pending: none")],
    ),
]


def _build_signal_notes(p: dict) -> list[str]:
    notes: list[str] = []
    rd = p.get("range_derivation") or {}
    if rd.get("derived_from_range"):
        mode = str(rd.get("split_mode") or "").capitalize()
        min_p = rd.get("original_min_price")
        max_p = rd.get("original_max_price")
        if mode and min_p is not None and max_p is not None:
            notes.append(f"Entry - {mode} [{num(min_p)}-{num(max_p)}]")
    if p.get("risk_hint_applied"):
        notes.append("Risk - Reduced by trader")
    return notes
```

- [ ] **Step 2: Run existing tests — expect PASS**

```
pytest tests/runtime_v2/control_plane/ -v --tb=short
```

- [ ] **Step 3: Commit**

```
git add src/runtime_v2/control_plane/formatters/templates/clean_log.py
git commit -m "feat: add shared block lists (CLOSE_METRICS, FINAL_RESULT, _FILL_SECTION, _SIGNAL_BODY)"
```

---

## Task 6: Close + Signal templates

**Files:**
- Modify: `src/runtime_v2/control_plane/formatters/templates/clean_log.py` (append)

- [ ] **Step 1: Append close templates**

```python
# ---------------------------------------------------------------------------
# Close templates (SL_FILLED, TP_FILLED_FINAL, POSITION_CLOSED, BE_EXIT)
# ---------------------------------------------------------------------------

_CLOSED_BLOCKS: list = [
    HeaderBlock(emoji=lambda p: p["_emoji"], event_label="POSITION CLOSED"),
    FieldBlock("Close reason", key="close_reason", optional=False, default="n/a"),
    SeparatorBlock(),
    *CLOSE_METRICS,
    *FINAL_RESULT,
    FooterBlock(default_source="exchange"),
]


def _t_sl_filled(p: dict) -> dict:
    return {**p, "_emoji": "🛑", "exit_label": "SL",
            "exit_price": p.get("sl_price", p.get("fill_price"))}


def _t_tp_final(p: dict) -> dict:
    level = p.get("tp_level")
    display_price = p.get("fill_price") if p.get("fill_price") is not None else p.get("tp_price")
    return {
        **p,
        "_emoji": "✅",
        "exit_label": f"TP_{level}" if level is not None else "TP",
        "exit_price": display_price,
        # inject close_reason so "FINAL TP FILLED" appears in output
        "close_reason": p.get("close_reason") or "FINAL TP FILLED",
    }


def _t_position_closed(p: dict) -> dict:
    return {
        **p,
        "_emoji": "✋",
        "exit_label": "Price",
        "exit_price": p.get("fill_price"),
        # backward compat: default close_reason if not provided by upstream
        "close_reason": p.get("close_reason") or "MANUAL_CLOSE",
    }


def _t_be_exit(p: dict) -> dict:
    price_label = "SL" if p.get("sl_price") is not None else "Price"
    price_value = p.get("sl_price") or p.get("exit_price") or p.get("fill_price")
    return {**p, "_emoji": "⚡", "exit_label": price_label, "exit_price": price_value}
```

- [ ] **Step 2: Append signal templates**

```python
# ---------------------------------------------------------------------------
# Signal templates (SIGNAL_ACCEPTED, SIGNAL_REJECTED, REVIEW_REQUIRED)
# ---------------------------------------------------------------------------

_SIGNAL_NOTES_BLOCKS: list = [
    SeparatorBlock(),
    StaticBlock("Notes:"),
    ListBlock(key="_signal_notes", item_renderer=lambda note, i, p: [note]),
]

_SIGNAL_BASE_BLOCKS: list = [
    HeaderBlock(emoji=lambda p: p["_emoji"], event_label=lambda p: p["_event_label"]),
    *_SIGNAL_BODY,
    FieldBlock("Leverage", key="leverage", fmt=lambda v: f"x{v}"),
    ConditionalBlock(
        condition=lambda p: bool(p.get("_signal_notes")),
        blocks=_SIGNAL_NOTES_BLOCKS,
    ),
    ConditionalBlock(
        condition=lambda p: p.get("parse_status") == "PARTIAL",
        blocks=[
            DerivedBlock(text_fn=lambda p:
                f"Parser: PARTIAL ({', '.join(p.get('parse_warnings') or []) or 'incomplete parse'})"
            ),
        ]
    ),
    FooterBlock(default_source="trader_signal",
                include_trader_id=True, include_account_id=True, include_rejected_reason=True),
]

_REVIEW_REQUIRED_BLOCKS: list = [
    HeaderBlock(emoji="⚠️", event_label="REVIEW REQUIRED"),
    *_SIGNAL_BODY,
    ConditionalBlock(
        condition=lambda p: bool(p.get("_signal_notes")),
        blocks=_SIGNAL_NOTES_BLOCKS,
    ),
    FooterBlock(default_source="runtime",
                include_trader_id=True, include_account_id=True, include_rejected_reason=True),
]


def _t_signal_accepted(p: dict) -> dict:
    return {**p, "_emoji": "✅", "_event_label": "SIGNAL ACCEPTED",
            "_entry_pcts": p.get("_entry_pcts", []),
            "_tp_pcts":    p.get("_tp_pcts", []),
            "_signal_notes": _build_signal_notes(p)}


def _t_signal_rejected(p: dict) -> dict:
    return {**p, "_emoji": "❌", "_event_label": "SIGNAL REJECTED",
            "_entry_pcts": p.get("_entry_pcts", []),
            "_tp_pcts":    p.get("_tp_pcts", []),
            "_signal_notes": _build_signal_notes(p)}


def _t_review_required(p: dict) -> dict:
    return {**p, "_signal_notes": _build_signal_notes(p)}
```

- [ ] **Step 3: Run existing tests — expect PASS** (templates not wired into dispatcher yet)

```
pytest tests/runtime_v2/control_plane/ -v --tb=short
```

- [ ] **Step 4: Commit**

```
git add src/runtime_v2/control_plane/formatters/templates/clean_log.py
git commit -m "feat: add close and signal templates with transforms"
```

---

## Task 7: Entry lifecycle templates

**Files:**
- Modify: `src/runtime_v2/control_plane/formatters/templates/clean_log.py` (append)

- [ ] **Step 1: Append entry lifecycle templates**

```python
# ---------------------------------------------------------------------------
# Entry lifecycle (ENTRY_OPENED, ENTRY_UPDATED, ENTRY_CANCELLED)
# ---------------------------------------------------------------------------

_ENTRY_BLOCKS: list = [
    HeaderBlock(emoji=lambda p: p["_emoji"], event_label=lambda p: p["_event_label"]),
    *_FILL_SECTION,                          # includes trailing SeparatorBlock
    *_ENTRY_POSITION_SECTION,
    ConditionalBlock(
        condition=lambda p: bool(p.get("is_partial_leg")),
        blocks=[
            SeparatorBlock(),
            StaticBlock("Changed:"),
            DerivedBlock(text_fn=lambda p:
                f"SL qty: {num(p.get('planned_qty'))} → {num(p.get('filled_qty'))} (adj. to fill)"
            ),
        ]
    ),
    FooterBlock(default_source="exchange"),
]


def _t_entry_opened(p: dict) -> dict:
    return {**p, "_emoji": "📊", "_event_label": "ENTRY OPENED",
            "_avg_entry": p.get("avg_entry")}


def _t_entry_updated(p: dict) -> dict:
    avg = p["new_avg_entry"] if "new_avg_entry" in p else p.get("avg_entry")
    return {**p, "_emoji": "✏️", "_event_label": "ENTRY UPDATED", "_avg_entry": avg}


_ENTRY_CANCELLED_BLOCKS: list = [
    HeaderBlock(emoji="⚠️", event_label="ENTRY CANCELLED"),
    DerivedBlock(text_fn=lambda p:
        f"Entry_{p['_c_seq']}: {num(p['_c_price'])} {p['_c_etype']}"
        if p.get("_c_price") is not None
        else f"Entry_{p['_c_seq']}: {p['_c_etype']}"
    ),
    ConditionalBlock(
        condition=lambda p: p.get("partial_fill_pct") is not None,
        blocks=[
            DerivedBlock(text_fn=lambda p:
                f"Partial fill: {pct(p['partial_fill_pct'])}"
                + (f" ({num(p['partial_fill_qty'])} {p['_base_asset']} kept)"
                   if p.get("partial_fill_qty") is not None else "")
            ),
        ]
    ),
    FieldBlock("Avg entry",    key="avg_entry",       fmt=num),
    ConditionalBlock(
        condition=lambda p: p.get("total_filled_qty") is not None,
        blocks=[
            DerivedBlock(text_fn=lambda p:
                f"Total filled: {num(p['total_filled_qty'])} {p['_base_asset']}"
            ),
        ]
    ),
    FooterBlock(default_source="runtime"),
]


def _t_entry_cancelled(p: dict) -> dict:
    cancelled = p.get("cancelled_entry") or {}
    symbol = display_symbol(p.get("symbol", ""))
    base_asset = symbol.split("/")[0] if "/" in symbol else symbol
    return {
        **p,
        "_c_seq":      cancelled.get("sequence", "?"),
        "_c_price":    cancelled.get("price"),
        "_c_etype":    cancelled.get("entry_type", "LIMIT").capitalize(),
        "_base_asset": base_asset,
    }
```

- [ ] **Step 2: Run existing tests — expect PASS**

```
pytest tests/runtime_v2/control_plane/ -v --tb=short
```

- [ ] **Step 3: Commit**

```
git add src/runtime_v2/control_plane/formatters/templates/clean_log.py
git commit -m "feat: add entry lifecycle templates (ENTRY_OPENED, ENTRY_UPDATED, ENTRY_CANCELLED)"
```

---

## Task 8: Partial close + Update lifecycle templates

**Files:**
- Modify: `src/runtime_v2/control_plane/formatters/templates/clean_log.py` (append)

- [ ] **Step 1: Append partial close templates**

```python
# ---------------------------------------------------------------------------
# Partial close (TP_FILLED, PARTIAL_CLOSE_EXECUTED)
# ---------------------------------------------------------------------------

_PARTIAL_RESULT_BLOCKS: list = [
    HeaderBlock(emoji=lambda p: p["_emoji"], event_label=lambda p: p["_event_label"]),
    DerivedBlock(text_fn=lambda p:
        f"{p['_price_label']}: {num(p['_price_value']) if p.get('_price_value') is not None else '-'}"
    ),
    FieldBlock("Closed",   key="closed_pct",  fmt=pct),
    FieldBlock("Qty",      key="closed_qty",  fmt=num),
    FieldBlock("PnL",      key="pnl",         fmt=money_signed),
    FieldBlock("Fee rate", key="fee_rate",    fmt=fee_rate),
    FieldBlock("Fee",      key="fee",         fmt=money),
    ConditionalBlock(
        condition=lambda p: p.get("_show_value"),
        blocks=[FieldBlock("Value", key="exec_value", fmt=money)],
    ),
    SeparatorBlock(),
    StaticBlock("Remaining:"),
    FieldBlock("Qty",       key="remaining_qty",  fmt=num),
    FieldBlock("Avg entry", key="avg_entry",      fmt=num),
    FieldBlock("Risk",      key="remaining_risk", fmt=money),
    FooterBlock(default_source="exchange"),
]


def _t_tp_partial(p: dict) -> dict:
    level = p.get("tp_level")
    display_price = p.get("fill_price") if p.get("fill_price") is not None else p.get("tp_price")
    return {
        **p,
        "_emoji":       "📊",
        "_event_label": f"TP{level} FILLED" if level is not None else "TP FILLED",
        "_price_label": f"TP_{level}" if level is not None else "TP",
        "_price_value": display_price,
        "_show_value":  True,
    }


def _t_partial_close(p: dict) -> dict:
    return {
        **p,
        "_emoji":       "✅",
        "_event_label": "PARTIAL CLOSED",
        "_price_label": "Price",
        "_price_value": p.get("fill_price"),
        "_show_value":  False,
    }
```

- [ ] **Step 2: Append update lifecycle templates**

```python
# ---------------------------------------------------------------------------
# Update lifecycle (UPDATE_DONE, UPDATE_PARTIAL, UPDATE_REJECTED)
# ---------------------------------------------------------------------------

_UPDATE_BLOCKS: list = [
    HeaderBlock(emoji=lambda p: p["_emoji"], event_label=lambda p: p["_event_label"]),
    ConditionalBlock(
        condition=lambda p: bool(p.get("_operations")),
        blocks=[
            StaticBlock("Operation:"),
            ListBlock(key="_operations", item_renderer=lambda op, i, p: [f"{_BULLET} {op}"]),
        ]
    ),
    ConditionalBlock(
        condition=lambda p: bool(p.get("changed")),
        blocks=[
            StaticBlock("Changed:"),
            ListBlock(key="changed", item_renderer=_render_changed_item),
        ]
    ),
    ConditionalBlock(
        condition=lambda p: bool(p.get("_footnotes")),
        blocks=[
            SeparatorBlock(),
            ListBlock(key="_footnotes", item_renderer=lambda note, i, p: [f"* {note}"]),
        ]
    ),
    ConditionalBlock(
        condition=lambda p: p.get("_failed_reason") is not None,
        blocks=[
            SeparatorBlock(),
            DerivedBlock(text_fn=lambda p: f"Failed: {p['_failed_reason']}"),
        ]
    ),
    FooterBlock(default_source="runtime"),
]


def _t_update_done(p: dict) -> dict:
    ops = p.get("applied_actions") or []
    changed = p.get("changed") or []
    # backward compat: display_lines converted to plain changed items (bullet-prefixed)
    if not changed and p.get("display_lines"):
        changed = list(p["display_lines"])
    footnotes = [item["note"] for item in changed if isinstance(item, dict) and item.get("note")]
    return {**p, "_emoji": "✅", "_event_label": "UPDATE DONE",
            "_operations": ops, "_failed_reason": None,
            "_footnotes": footnotes or None,
            "changed": changed}


def _t_update_partial(p: dict) -> dict:
    applied     = p.get("applied_actions") or []
    failed_list = p.get("failed_actions") or []   # [{"action": str, "reason": str}]
    failed_set  = {f["action"] for f in failed_list}
    all_ops     = applied + [f["action"] for f in failed_list]
    ops_display = [f"{op} *" if op in failed_set else op for op in all_ops]
    changed     = p.get("changed") or []
    fn_changed  = [item["note"] for item in changed if isinstance(item, dict) and item.get("note")]
    fn_failed   = [f"Failed: {f['reason']}" for f in failed_list]
    footnotes   = fn_changed + fn_failed
    return {**p, "_emoji": "⚠️", "_event_label": "UPDATE PARTIAL",
            "_operations": ops_display, "_failed_reason": None,
            "_footnotes": footnotes or None}


def _t_update_rejected(p: dict) -> dict:
    ops     = p.get("rejected_actions") or []
    reason  = p.get("reason") or p.get("failed_reason")
    changed = p.get("changed") or []
    footnotes = [item["note"] for item in changed if isinstance(item, dict) and item.get("note")]
    return {**p, "_emoji": "❌", "_event_label": "UPDATE REJECTED",
            "_operations": ops, "_failed_reason": reason,
            "_footnotes": footnotes or None}
```

- [ ] **Step 3: Run existing tests — expect PASS**

```
pytest tests/runtime_v2/control_plane/ -v --tb=short
```

- [ ] **Step 4: Commit**

```
git add src/runtime_v2/control_plane/formatters/templates/clean_log.py
git commit -m "feat: add partial close and update lifecycle templates"
```

---

## Task 9: Simple notifications + Multi-chain templates

**Files:**
- Modify: `src/runtime_v2/control_plane/formatters/templates/clean_log.py` (append)

- [ ] **Step 1: Append simple notification blocks**

```python
# ---------------------------------------------------------------------------
# Simple notifications
# ---------------------------------------------------------------------------

_PENDING_TIMEOUT_BLOCKS: list = [
    HeaderBlock(emoji="⏰", event_label="PENDING ENTRY EXPIRED"),
    StaticBlock("Timeout: order expired before fill"),
    FooterBlock(default_source="timeout_worker"),
]

_REENTRY_BLOCKS: list = [
    HeaderBlock(emoji="🔄", event_label="REENTRY ACCEPTED"),
    FieldBlock("Previous chain",
               value_fn=lambda p: f"#{p['previous_chain_id']}" if p.get("previous_chain_id") is not None else None,
               fmt=text),
    FooterBlock(default_source="runtime"),
]

_CANCEL_FAILED_BLOCKS: list = [
    HeaderBlock(emoji="🚨", event_label="CANCEL FAILED"),
    DerivedBlock(text_fn=lambda p:
        f"Cancellation of {p.get('entry_ref', 'entry')} failed after {p.get('attempts', 3)} attempts."
    ),
    StaticBlock("Requires manual review to resolve the position."),
    FieldBlock("Entry price", key="entry_price", fmt=num),
    FooterBlock(default_source="timeout_worker"),
]

_RECONCILIATION_WARN_BLOCKS: list = [
    HeaderBlock(emoji="⚠️", event_label="RECONCILIATION WARNING"),
    FieldBlock("Issue",  key="issue",  fmt=text),
    FieldBlock("Risk",   key="risk",   fmt=text),
    FieldBlock("Action", key="action", fmt=text),
    FooterBlock(default_source="runtime"),
]

_RECONCILIATION_FIXED_BLOCKS: list = [
    HeaderBlock(emoji="✅", event_label="RECONCILIATION FIXED"),
    FieldBlock("Issue resolved", key="issue", fmt=text),
    FooterBlock(default_source="runtime"),
]
```

- [ ] **Step 2: Append multi-chain templates**

```python
# ---------------------------------------------------------------------------
# Multi-chain (MULTI_CHAIN_SUMMARY, MULTI_CHAIN_UPDATE, MULTI_CHAIN_CLOSED)
# ---------------------------------------------------------------------------

def _render_chain_item(chain: dict, i: int, p: dict) -> list[str]:
    chain_id = chain.get("chain_id", "?")
    symbol = display_symbol(chain.get("symbol", "?"))
    side = chain.get("side", "?")
    status = chain.get("status", "DONE")
    lines = [f"#{chain_id} {symbol} {side} — {status}"]
    if chain.get("link"):
        lines.append(chain["link"])
    if p.get("summary_kind") != "final_close":
        for item in chain.get("display_lines") or []:
            lines.append(item)
    lines.append(_SEP)
    return lines


def _fmt_counts(p: dict) -> str:
    counts = p.get("_counts", {})
    summary_kind = p.get("summary_kind", "immediate")
    done    = counts.get("done", 0)
    partial = counts.get("partial", 0)
    skipped = counts.get("skipped", 0)
    review  = counts.get("review", 0)
    error   = counts.get("error", 0)
    if summary_kind == "final_close":
        parts = [f"Done: {done}"]
        if partial: parts.append(f"Partial: {partial}")
        if review:  parts.append(f"Review: {review}")
        parts.append(f"Skipped: {skipped}")
        parts.append(f"Error: {error}")
    else:
        parts = [f"Done: {done}", f"Partial: {partial}", f"Skipped: {skipped}"]
        if review: parts.append(f"Review: {review}")
        parts.append(f"Error: {error}")
    return " | ".join(parts)


_MULTI_CHAIN_BLOCKS: list = [
    DerivedBlock(text_fn=lambda p:
        ("⚠️" if p["_has_issues"] else "✅")
        + f" UPDATE APPLICATO — {len(p.get('chains') or [])} chain"
    ),
    SeparatorBlock(),
    BranchBlock(
        condition=lambda p: p.get("summary_kind") == "final_close",
        then_blocks=[StaticBlock("Operation requested:")],
        else_blocks=[StaticBlock("Operations requested:")],
    ),
    ListBlock(key="requested_operations", fallback_key="operations",
              item_renderer=lambda item, i, p: [f"{_BULLET} {item}"]),
    SeparatorBlock(),
    ListBlock(key="chains", item_renderer=_render_chain_item),
    DerivedBlock(text_fn=_fmt_counts),
    FooterBlock(),
]


def _t_multi_chain(p: dict) -> dict:
    chains = p.get("chains") or []
    has_issues = any(
        chain.get("status") in {"PARTIAL", "SKIPPED", "REVIEW", "ERROR"}
        for chain in chains
    )
    counts = p.get("counts") or {
        "done":    sum(1 for c in chains if c.get("status") == "DONE"),
        "partial": sum(1 for c in chains if c.get("status") == "PARTIAL"),
        "skipped": sum(1 for c in chains if c.get("status") == "SKIPPED"),
        "review":  sum(1 for c in chains if c.get("status") == "REVIEW"),
        "error":   sum(1 for c in chains if c.get("status") == "ERROR"),
    }
    return {**p, "_has_issues": has_issues, "_counts": counts}
```

- [ ] **Step 3: Run existing tests — expect PASS**

```
pytest tests/runtime_v2/control_plane/ -v --tb=short
```

- [ ] **Step 4: Commit**

```
git add src/runtime_v2/control_plane/formatters/templates/clean_log.py
git commit -m "feat: add simple notification templates and multi-chain templates"
```

---

## Task 10: `TEMPLATE_REGISTRY` + rewrite thin dispatcher

**Files:**
- Modify: `src/runtime_v2/control_plane/formatters/templates/clean_log.py` (append REGISTRY)
- Rewrite: `src/runtime_v2/control_plane/formatters/clean_log.py`

- [ ] **Step 1: Append `TEMPLATE_REGISTRY` to `templates/clean_log.py`**

```python
# ---------------------------------------------------------------------------
# TEMPLATE_REGISTRY
# ---------------------------------------------------------------------------

TEMPLATE_REGISTRY: dict[str, TemplateConfig] = {
    "SIGNAL_ACCEPTED":        TemplateConfig(_SIGNAL_BASE_BLOCKS,       _t_signal_accepted),
    "SIGNAL_REJECTED":        TemplateConfig(_SIGNAL_BASE_BLOCKS,       _t_signal_rejected),
    "REVIEW_REQUIRED":        TemplateConfig(_REVIEW_REQUIRED_BLOCKS,   _t_review_required),
    "ENTRY_OPENED":           TemplateConfig(_ENTRY_BLOCKS,             _t_entry_opened),
    "ENTRY_UPDATED":          TemplateConfig(_ENTRY_BLOCKS,             _t_entry_updated),
    "ENTRY_CANCELLED":        TemplateConfig(_ENTRY_CANCELLED_BLOCKS,   _t_entry_cancelled),
    "SL_FILLED":              TemplateConfig(_CLOSED_BLOCKS,            _t_sl_filled),
    "TP_FILLED_FINAL":        TemplateConfig(_CLOSED_BLOCKS,            _t_tp_final),
    "POSITION_CLOSED":        TemplateConfig(_CLOSED_BLOCKS,            _t_position_closed),
    "BE_EXIT":                TemplateConfig(_CLOSED_BLOCKS,            _t_be_exit),
    "TP_FILLED":              TemplateConfig(_PARTIAL_RESULT_BLOCKS,    _t_tp_partial),
    "UPDATE_DONE":            TemplateConfig(_UPDATE_BLOCKS,            _t_update_done),
    "UPDATE_PARTIAL":         TemplateConfig(_UPDATE_BLOCKS,            _t_update_partial),
    "UPDATE_REJECTED":        TemplateConfig(_UPDATE_BLOCKS,            _t_update_rejected),
    "PARTIAL_CLOSE_EXECUTED": TemplateConfig(_PARTIAL_RESULT_BLOCKS,   _t_partial_close),
    "PENDING_ENTRY_EXPIRED":  TemplateConfig(_PENDING_TIMEOUT_BLOCKS),
    "REENTRY_ACCEPTED":       TemplateConfig(_REENTRY_BLOCKS),
    "CANCEL_FAILED":          TemplateConfig(_CANCEL_FAILED_BLOCKS),
    "RECONCILIATION_WARNING": TemplateConfig(_RECONCILIATION_WARN_BLOCKS),
    "RECONCILIATION_FIXED":   TemplateConfig(_RECONCILIATION_FIXED_BLOCKS),
    "MULTI_CHAIN_SUMMARY":    TemplateConfig(_MULTI_CHAIN_BLOCKS,       _t_multi_chain),
    "MULTI_CHAIN_UPDATE":     TemplateConfig(_MULTI_CHAIN_BLOCKS,       _t_multi_chain),
    "MULTI_CHAIN_CLOSED":     TemplateConfig(_MULTI_CHAIN_BLOCKS,       _t_multi_chain),
}
```

- [ ] **Step 2: Rewrite `clean_log.py` as thin dispatcher**

Replace the entire content of `src/runtime_v2/control_plane/formatters/clean_log.py`:

```python
# src/runtime_v2/control_plane/formatters/clean_log.py
from __future__ import annotations

from src.runtime_v2.control_plane.formatters._blocks import (
    render_template, HeaderBlock, FooterBlock, TemplateConfig,
)
from src.runtime_v2.control_plane.formatters.templates.clean_log import TEMPLATE_REGISTRY


def format_clean_log(notification_type: str, payload: dict) -> str:
    # SL_FILLED with close_reason=BREAKEVEN_AFTER_TP routes to BE_EXIT behavior
    if notification_type == "SL_FILLED" and payload.get("close_reason") == "BREAKEVEN_AFTER_TP":
        notification_type = "BE_EXIT"
        payload = {**payload, "exit_price": payload.get("sl_price", payload.get("fill_price"))}

    config = TEMPLATE_REGISTRY.get(notification_type)
    if config:
        return render_template(config.blocks, payload, transform=config.payload_transform)
    return _fallback(notification_type, payload)


def _fallback(notification_type: str, payload: dict) -> str:
    blocks = [HeaderBlock("📊", notification_type), FooterBlock()]
    return render_template(blocks, payload)


__all__ = ["format_clean_log"]
```

- [ ] **Step 3: Run ALL tests — identify which ones fail**

```
pytest tests/runtime_v2/control_plane/ -v --tb=short 2>&1 | head -100
```

Note which tests fail — they are the ones listed in the "Breaking changes" section at the top of this plan. Proceed to Task 11.

- [ ] **Step 4: Commit (even with failing tests — Task 11 will fix them)**

```
git add src/runtime_v2/control_plane/formatters/templates/clean_log.py src/runtime_v2/control_plane/formatters/clean_log.py
git commit -m "feat: wire TEMPLATE_REGISTRY and rewrite clean_log.py as thin dispatcher"
```

---

## Task 11: Update existing tests

**Files:**
- Modify: `tests/runtime_v2/control_plane/test_clean_log_formatter.py`
- Modify: `tests/runtime_v2/control_plane/test_clean_log_formatter_full.py`

### Changes to `test_clean_log_formatter.py`

- [ ] **Step 1: Fix `test_update_done_uses_operation_label_and_square_bullet`**

The test uses `operations` key (old); new dispatcher reads `applied_actions`. Arrow changes from `->` to `→`. Footnote is now in a separate section (no inline `*`).

Replace the test:
```python
def test_update_done_uses_operation_label_and_square_bullet():
    text = format_clean_log("UPDATE_DONE", {
        "chain_id": 145,
        "symbol": "BTC/USDT",
        "side": "LONG",
        "applied_actions": ["Move SL to BE"],
        "changed": [{"field": "SL", "old": 64000, "new": 65020, "note": "Changed by rule after TP_1"}],
        "source": "trader_update",
    })
    assert "Operation:" in text
    assert f"▪️ Move SL to BE" in text
    assert "SL: 64,000 → 65,020 *" in text
    assert "* Changed by rule after TP_1" in text
```

- [ ] **Step 2: Fix `test_update_partial_renders_changed_and_rejected_sections`**

`rejected_actions` is now a list of strings in old code; new `_t_update_partial` reads `failed_actions: [{"action": str, "reason": str}]`. Arrow and footnote changes.

Replace the test:
```python
def test_update_partial_renders_changed_and_rejected_sections():
    text = format_clean_log("UPDATE_PARTIAL", {
        "chain_id": 146,
        "symbol": "BTC/USDT",
        "side": "LONG",
        "applied_actions": ["U_MOVE_STOP"],
        "failed_actions": [{"action": "NOOP_ALREADY_PROTECTED_BE", "reason": "already at BE"}],
        "changed": [
            {"field": "SL", "old": 91000, "new": 94200, "note": "Adjusted after TP_1"},
            {"field": "Entry_2", "old": 92500, "new": "cancelled"},
        ],
        "source": "trader_update",
    })
    assert "UPDATE PARTIAL" in text
    assert "Changed:" in text
    assert "SL: 91,000 → 94,200 *" in text
    assert "* Adjusted after TP_1" in text
    assert "Entry_2: 92,500 → cancelled" in text
    assert "NOOP_ALREADY_PROTECTED_BE *" in text   # in Operation section
    assert "* Failed: already at BE" in text        # in footnotes section
```

- [ ] **Step 3: Fix `test_update_rejected_renders_reason_and_rejected_actions`**

"Reason:" label is now "Failed:"; "Rejected:" section replaced by "Operation:" list.

Replace the test:
```python
def test_update_rejected_renders_reason_and_rejected_actions():
    text = format_clean_log("UPDATE_REJECTED", {
        "chain_id": 147,
        "symbol": "BTC/USDT",
        "side": "LONG",
        "reason": "not_pending",
        "rejected_actions": ["NOOP_NOT_PENDING"],
        "source": "runtime",
    })
    assert "UPDATE REJECTED" in text
    assert "Failed: not_pending" in text
    assert "Operation:" in text
    assert "NOOP_NOT_PENDING" in text
```

- [ ] **Step 4: Fix `test_cancel_failed_formatter`**

Typo "manual review required" is fixed to "manual review to resolve".

Replace the assertion:
```python
    assert "manual review to resolve" in text.lower()
```
(Remove the old `assert "manual review required" in text.lower()` line.)

- [ ] **Step 5: Fix `test_multi_chain_summary_*` tests — check em-dash**

The new `_render_chain_item` uses `—` (em-dash, `—`) while old code used `—`. Check that tests checking `#43 ETH/USDT LONG — REVIEW` use the right character. The spec uses `—` so existing string literals should still match. No change needed if the existing test strings use `—`.

Run to verify:
```
pytest tests/runtime_v2/control_plane/test_clean_log_formatter.py::test_multi_chain_summary_legacy_review_count_is_preserved -v
```

### Changes to `test_clean_log_formatter_full.py`

- [ ] **Step 6: Fix `test_update_done_renders_operations_and_changes`**

`changed_fields` is dead code. Remove it from the payload and its assertion.

Replace the test:
```python
def test_update_done_renders_operations_and_changes():
    text = format_clean_log("UPDATE_DONE", {
        "chain_id": 300, "symbol": "BTC/USDT", "side": "LONG",
        "applied_actions": ["U_MOVE_STOP", "U_UPDATE_TAKE_PROFITS"],
        "source": "runtime",
    })
    assert "UPDATE DONE" in text
    assert "#300" in text
    assert "✅" in text
    assert "U_MOVE_STOP" in text
    assert "Source: runtime" in text
```

- [ ] **Step 7: Fix `test_update_partial_renders_applied_and_rejected`**

`rejected_actions` strings list → `failed_actions` dicts; "Applied:" → "Operation:".

Replace the test:
```python
def test_update_partial_renders_applied_and_rejected():
    text = format_clean_log("UPDATE_PARTIAL", {
        "chain_id": 400, "symbol": "SOL/USDT", "side": "LONG",
        "applied_actions": ["U_MOVE_STOP"],
        "failed_actions": [{"action": "U_ADD_ENTRY", "reason": "no pending slot"}],
        "source": "runtime",
    })
    assert "UPDATE PARTIAL" in text
    assert "#400" in text
    assert "⚠️" in text
    assert "U_MOVE_STOP" in text
    assert "U_ADD_ENTRY *" in text
    assert "Source: runtime" in text
```

- [ ] **Step 9: Fix em-dash in multi-chain header (`-` → `—`)**

The new `_MULTI_CHAIN_BLOCKS` uses em-dash `—` in `UPDATE APPLICATO — N chain`.

In `test_clean_log_formatter.py`, replace:
```python
    assert "UPDATE APPLICATO - 2 chain" in text
```
with:
```python
    assert "UPDATE APPLICATO — 2 chain" in text
```

In `test_clean_log_formatter_full.py`, replace:
```python
    assert "UPDATE APPLICATO - 3 chain" in text
```
with:
```python
    assert "UPDATE APPLICATO — 3 chain" in text
```

- [ ] **Step 10: Run ALL tests — expect all PASS**

```
pytest tests/runtime_v2/control_plane/ -v --tb=short
```

- [ ] **Step 11: Commit**

```
git add tests/runtime_v2/control_plane/test_clean_log_formatter.py tests/runtime_v2/control_plane/test_clean_log_formatter_full.py
git commit -m "test: update clean_log formatter tests for new template system"
```

---

## Final verification

- [ ] **Run full test suite**

```
pytest tests/ -v --tb=short
```

Expected: all pass.

- [ ] **Verify the legacy functions are gone from `clean_log.py`**

```
grep -n "def _signal_accepted\|def _entry_opened\|def _closed_template\|def _tp_filled\|def _num\|def _fmt_money" src/runtime_v2/control_plane/formatters/clean_log.py
```

Expected: no output (all legacy functions deleted).

- [ ] **Verify `_finalize` lives in `_blocks.py`**

```
grep -n "_finalize" src/runtime_v2/control_plane/formatters/_blocks.py
```

Expected: matches found.

- [ ] **Final commit**

```
git add -u
git commit -m "refactor: complete log templating system migration — clean_log.py now thin dispatcher"
```
