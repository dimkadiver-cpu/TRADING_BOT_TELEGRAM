# Log Templating System — Block-based DSL

**Data**: 2026-06-06
**Stato**: Approvato

---

## Obiettivo

Introdurre un sistema di templating dichiarativo per tutti i tipi di log (clean_log e tech_log) che permetta di modificare il rendering — label, ordine campi, campi inclusi/esclusi, blocchi custom — senza toccare la logica di rendering in `clean_log.py`.

---

## Contesto attuale

- `src/runtime_v2/control_plane/formatters/clean_log.py` — 658 righe, tutta la logica hardcoded come funzioni Python per 23 notification type.
- `src/runtime_v2/control_plane/formatters/tech_log.py` — 38 righe, formatter hardcoded.
- Il separatore è **dinamico**: `_finalize()` calcola la larghezza dal testo più lungo e sostituisce i sentinel `_SEP`. Questo meccanismo rimane invariato.
- La logica di business (cosa mettere nel payload, quale `summary_kind` usare) vive fuori dal formatter — questo non cambia.

---

## Struttura file dopo la migrazione

```
src/runtime_v2/control_plane/formatters/
├── _blocks.py              ← Block dataclasses + render_template()
├── _formatters.py          ← num(), money_signed(), pct_signed(), fee_rate(), …
├── templates/
│   ├── __init__.py
│   ├── clean_log.py        ← tutte le TemplateConfig + TEMPLATE_REGISTRY + shared renderers
│   └── tech_log.py         ← TemplateConfig per tech_log
├── clean_log.py            ← dispatcher thin (~20 righe)
├── tech_log.py             ← wrapper thin
└── display.py              ← invariato
```

`_blocks.py` e `_formatters.py` contengono solo meccanismi, nessuna regola di business. Tutto ciò che si vuole modificare vive in `templates/`.

---

## Catalogo Block (`_blocks.py`)

### Block primitivi

```python
@dataclass
class SeparatorBlock:
    """Riga separatore dinamica — usa _SEP sentinel, _finalize calcola larghezza."""

@dataclass
class StaticBlock:
    text: str

@dataclass
class DerivedBlock:
    text_fn: Callable[[dict], str]
```

### Block dati

```python
@dataclass
class HeaderBlock:
    """Prima riga (emoji + chain_id + event_label) + SEP + symbol/side + signal_link + SEP.
    chain_id, symbol, side, signal_link vengono sempre da payload — non configurabili.

    Emette in ordine:
      1. f"{emoji}{id_part} — {event_label}"   dove id_part = f" #{chain_id}" se presente
      2. _SEP
      3. f"{display_symbol(symbol)} — {side_emoji} {side}"
      4. signal_link (solo se presente nel payload)
      5. _SEP  ← incluso nel block, NON aggiungere SeparatorBlock() dopo HeaderBlock
    """
    emoji: str | Callable[[dict], str]
    event_label: str | Callable[[dict], str]

@dataclass
class FieldBlock:
    """Una riga 'Label: valore'.
    Usa key (lookup diretto) oppure value_fn (calcolo da payload).
    Se optional=True e valore è None → riga omessa.
    """
    label: str | Callable[[dict], str]
    key: str | None = None
    value_fn: Callable[[dict], Any] | None = None
    fmt: Callable[[Any], str] = num
    optional: bool = True
    default: str = "n/a"

@dataclass
class SectionBlock:
    """Label statico + sotto-blocchi. Es: 'Final Result:' con i suoi FieldBlock."""
    label: str
    blocks: list[Block]
```

### Block strutturali

```python
@dataclass
class ConditionalBlock:
    """Renderizza i sotto-blocchi solo se condition(payload) è True."""
    condition: Callable[[dict], bool]
    blocks: list[Block]

@dataclass
class BranchBlock:
    """if/else dichiarativo."""
    condition: Callable[[dict], bool]
    then_blocks: list[Block]
    else_blocks: list[Block] = field(default_factory=list)

@dataclass
class ListBlock:
    """Loop su una lista nel payload.
    item_renderer(item, index, parent_payload) -> list[str].
    index parte da index_start (default 1).
    L'item_renderer può restituire _SEP come stringa sentinel — viene gestito da _finalize.
    """
    key: str
    item_renderer: Callable[[Any, int, dict], list[str]]
    fallback_key: str | None = None
    index_start: int = 1

@dataclass
class FooterBlock:
    """Inizia con _SEP, poi trader_id/account_id/reason opzionali, poi Source + link.
    NON aggiungere SeparatorBlock() prima di FooterBlock — è incluso internamente.

    Struttura emessa:
      _SEP
      Trader: {trader_id}       (se include_trader_id e presente)
      Exchange Account: {id}   (se include_account_id e presente)
      Rejected: {reason}       (se include_rejected_reason e presente)
      Source: {source}
      _SEP                     (solo se link presente)
      {link}                   (solo se presente)
    """
    source_key: str = "source"
    default_source: str = "runtime"
    link_key: str = "link"
    include_trader_id: bool = False
    include_account_id: bool = False
    include_rejected_reason: bool = False
```

---

## Renderer (`_blocks.py`)

```python
@dataclass
class TemplateConfig:
    blocks: list[Block]
    payload_transform: Callable[[dict], dict] | None = None


def render_template(
    blocks: list[Block],
    payload: dict,
    *,
    transform: Callable[[dict], dict] | None = None,
) -> str:
    p = transform(payload) if transform else payload
    lines: list[str] = []
    _render_blocks(blocks, p, lines)
    return _finalize(lines)  # invariato


def _render_blocks(blocks: list[Block], p: dict, lines: list[str]) -> None:
    for block in blocks:
        match block:
            case SeparatorBlock():
                lines.append(_SEP)
            case StaticBlock(text=t):
                lines.append(t)
            case DerivedBlock(text_fn=fn):
                lines.append(fn(p))
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
```

---

## Formatters (`_formatters.py`)

Raccoglie i formatter oggi sparsi in `clean_log.py`, con nomi stabili importabili nei template:

| Nome | Comportamento |
|------|--------------|
| `num(v)` | numero con virgole, sig figs — ex `_num` |
| `text(v)` | str passthrough — per campi testuali già formattati |
| `money(v)` | `12.34 USDT`, `n/a` se None |
| `money_signed(v)` | `+12.34 USDT` / `-5.00 USDT` |
| `pct(v)` | `12.34%` |
| `pct_signed(v)` | `+12.34%` / `-5.00%` |
| `fee_rate(v)` | `float * 100` con 3 decimali |

Tutti restituiscono `"n/a"` se `value is None`.

---

## Shared renderers (`templates/clean_log.py`)

Funzioni usate come `item_renderer` nei `ListBlock`. Importano `_SEP` da `_blocks` e
`display_symbol` da `display` dove necessario.

```python
def _render_entry_item(entry: dict, i: int, p: dict) -> list[str]:
    seq = entry.get("sequence", i)
    etype = entry.get("entry_type", "LIMIT")
    price = entry.get("price")
    if etype == "MARKET":
        price_str = f"Market ~{num(price)}" if price is not None else "Market"
    else:
        price_str = f"{num(price)} Limit" if price is not None else "Limit"
    return [f"Entry_{seq}: {price_str}"]


def _render_pending_entry(entry: dict, i: int, p: dict) -> list[str]:
    seq = entry.get("sequence", "?")
    price = entry.get("price")
    etype = entry.get("entry_type", "LIMIT").capitalize()
    price_str = num(price) if price is not None else "?"
    return [f"Pending: Entry_{seq} {price_str} {etype}"]


def _render_changed_item(item, i: int, p: dict) -> list[str]:
    if isinstance(item, dict):
        field = item.get("field", "?")
        value = f"{num(item.get('old'))} -> {num(item.get('new'))}"
        note = item.get("note")
        if note:
            return [f"{field}: {value} *", f"* {note}"]
        return [f"{field}: {value}"]
    return [f"{_BULLET} {item}"]
```

---

## Sezioni condivise riusabili

### CLOSE_METRICS

```python
CLOSE_METRICS: list[Block] = [
    FieldBlock(label=lambda p: p.get("exit_label", "Price"), key="exit_price",
               fmt=num, optional=False, default="n/a"),
    FieldBlock("Qty",      key="closed_qty",  fmt=num),
    FieldBlock("PnL",      key="pnl",         fmt=money_signed),
    FieldBlock("Fee",      key="fee",          fmt=money),
    FieldBlock("Fee rate", key="fee_rate",     fmt=fee_rate),
]
```

### FINAL_RESULT

```python
FINAL_RESULT: list[Block] = [
    SeparatorBlock(),
    StaticBlock("Final Result:"),
    FieldBlock("ROI net",       value_fn=lambda p: (p.get("final_result") or {}).get("roi_net_pct"),
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
```

### _FILL_SECTION

Shared tra `ENTRY_OPENED`, `ENTRY_UPDATED`. Formato: `Filled:` statico + `Entry_N: price type`
+ qty con/senza planned + Value/Fee + `Partial %` se fill parziale.
Trailing `SeparatorBlock` incluso — non aggiungerne uno dopo in `_ENTRY_BLOCKS`.

```python
_FILL_SECTION: list[Block] = [
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
        else_blocks=[
            FieldBlock("Qty", key="filled_qty", fmt=num),
        ]
    ),
    FieldBlock("Value",    key="exec_value", fmt=money),
    FieldBlock("Fee rate", key="fee_rate",   fmt=fee_rate),
    FieldBlock("Fee",      key="fee",        fmt=money),
    ConditionalBlock(
        condition=lambda p: bool(p.get("is_partial_leg")),
        blocks=[
            FieldBlock("Partial", key="_leg_fill_pct", fmt=pct),
        ]
    ),
    SeparatorBlock(),
]
```

### _SIGNAL_BODY

Shared tra `SIGNAL_ACCEPTED`, `SIGNAL_REJECTED`, `REVIEW_REQUIRED`.

```python
_SIGNAL_BODY: list[Block] = [
    ListBlock(key="entries", item_renderer=_render_entry_item),
    FieldBlock("SL",   key="sl",       fmt=num),
    ListBlock(key="tps", item_renderer=lambda tp, i, p: [f"TP_{i}: {num(tp)}"]),
    FieldBlock("Risk", key="risk_pct", fmt=lambda v: f"{v}%"),
]
```

---

## Tipi close — stessi block, `payload_transform` diverso

```python
_CLOSED_BLOCKS: list[Block] = [
    HeaderBlock(emoji=lambda p: p["_emoji"], event_label="POSITION CLOSED"),
    FieldBlock("Close reason", key="close_reason", optional=False, default="n/a"),
    SeparatorBlock(),
    *CLOSE_METRICS,
    *FINAL_RESULT,
    FooterBlock(default_source="exchange"),
]
```

`close_reason` viene sempre dal payload (impostato upstream dal worker/lifecycle) — i transform
non lo iniettano mai. `FooterBlock` inizia con `_SEP` internamente — non serve `SeparatorBlock()`
prima di esso.

Ogni tipo inietta `_emoji`, `exit_label`, `exit_price` via `payload_transform`:

| Tipo | `_emoji` | `exit_label` | `exit_price` |
|------|----------|-------------|-------------|
| `SL_FILLED` | 🛑 | `"SL"` | `sl_price \| fill_price` |
| `TP_FILLED_FINAL` | ✅ | `"TP_{level}"` | `fill_price \| tp_price` |
| `POSITION_CLOSED` | ✋ | `"Price"` | `fill_price` |
| `BE_EXIT` | ⚡ | `"SL" \| "Price"` | `sl_price \| exit_price \| fill_price` |

```python
def _t_sl_filled(p):
    return {**p, "_emoji": "🛑", "exit_label": "SL",
            "exit_price": p.get("sl_price", p.get("fill_price"))}

def _t_tp_final(p):
    level = p.get("tp_level")
    display_price = p.get("fill_price") if p.get("fill_price") is not None else p.get("tp_price")
    return {**p, "_emoji": "✅",
            "exit_label": f"TP_{level}" if level is not None else "TP",
            "exit_price": display_price}

def _t_position_closed(p):
    return {**p, "_emoji": "✋", "exit_label": "Price", "exit_price": p.get("fill_price")}

def _t_be_exit(p):
    price_label = "SL" if p.get("sl_price") is not None else "Price"
    price_value = p.get("sl_price") or p.get("exit_price") or p.get("fill_price")
    return {**p, "_emoji": "⚡", "exit_label": price_label, "exit_price": price_value}
```

Modificare il layout di chiusura significa toccare `_CLOSED_BLOCKS` una sola volta — si propaga
a tutti e quattro i tipi.

---

## Signal — stessi body block, transform differente

```python
_SIGNAL_BASE_BLOCKS: list[Block] = [
    HeaderBlock(emoji=lambda p: p["_emoji"], event_label=lambda p: p["_event_label"]),
    *_SIGNAL_BODY,
    FieldBlock("Leverage", key="leverage", fmt=lambda v: f"x{v}"),
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

def _t_signal_accepted(p): return {**p, "_emoji": "✅", "_event_label": "SIGNAL ACCEPTED"}
def _t_signal_rejected(p): return {**p, "_emoji": "❌", "_event_label": "SIGNAL REJECTED"}
```

`REVIEW_REQUIRED` non ha leverage né il warning PARTIAL — usa block list separata:

```python
_REVIEW_REQUIRED_BLOCKS: list[Block] = [
    HeaderBlock(emoji="⚠️", event_label="REVIEW REQUIRED"),
    *_SIGNAL_BODY,
    FooterBlock(default_source="runtime",
                include_trader_id=True, include_account_id=True, include_rejected_reason=True),
]
```

---

## TP parziale

```python
_TP_PARTIAL_BLOCKS: list[Block] = [
    HeaderBlock(emoji="📊", event_label=lambda p: p["_event_label"]),
    DerivedBlock(text_fn=lambda p:
        f"{p['_tp_label']}: {num(p['_tp_price']) if p.get('_tp_price') is not None else '-'}"
    ),
    FieldBlock("Closed", key="closed_pct",  fmt=pct),
    FieldBlock("PnL",    key="pnl",         fmt=money_signed),
    FieldBlock("Fee",    key="fee",         fmt=money),
    FieldBlock("Fee rate", key="fee_rate",  fmt=fee_rate),
    FieldBlock("Value",  key="exec_value",  fmt=money),
    StaticBlock(""),
    FooterBlock(default_source="exchange"),
]

def _t_tp_partial(p):
    level = p.get("tp_level")
    display_price = p.get("fill_price") if p.get("fill_price") is not None else p.get("tp_price")
    return {
        **p,
        "_event_label": f"TP{level} FILLED" if level is not None else "TP FILLED",
        "_tp_label":    f"TP_{level}" if level is not None else "TP",
        "_tp_price":    display_price,
    }
```

---

## Entry lifecycle

### Payload enrichment (outbox_writer)

`_build_payload` aggiunge questi campi al payload di `ENTRY_OPENED` e `ENTRY_UPDATED` a partire
dai dati di chain già disponibili. Il formatter non calcola nulla — legge solo il payload.

| Campo aggiunto | Fonte | Uso nel template |
|----------------|-------|-----------------|
| `planned_qty` | `risk["legs"][seq]["qty"]` | `Qty: x (planned: y)` |
| `entry_type_for_leg` | `plan["legs"][seq]["entry_type"]` | `Entry_N: price Type` |
| `is_partial_leg` | `filled_qty < planned_qty` | condizionale sezioni Partial/Changed |
| `_leg_fill_pct` | `filled_qty / planned_qty * 100` | `Partial: xx%` |
| `position_filled_pct` | `filled_entry_qty / total_planned_qty * 100` | `Filled: xx%` in Position |
| `actual_risk_usdt` | `filled_entry_qty * abs(avg_entry - current_stop_price)` | `Risk: actual USDT` |
| `planned_risk_usdt` | `initial_risk_amount` | `Risk: planned USDT` |

`entry_type_for_leg` è "MARKET" o "LIMIT". `is_partial_leg=False` se `planned_qty` non disponibile.

### _ENTRY_BLOCKS (unificato)

ENTRY_OPENED e ENTRY_UPDATED condividono la stessa block list. Le differenze
(emoji, label, avg_entry source) vengono iniettate dai rispettivi transform.

Sezioni: Filled (da `_FILL_SECTION` con trailing SEP) → Position → Changed (solo se parziale) → Footer.

```python
_ENTRY_POSITION_SECTION: list[Block] = [
    StaticBlock("Position:"),
    FieldBlock("Avg entry", key="_avg_entry",          fmt=num),
    FieldBlock("Filled",    key="position_filled_pct", fmt=pct),
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


_ENTRY_BLOCKS: list[Block] = [
    HeaderBlock(emoji=lambda p: p["_emoji"], event_label=lambda p: p["_event_label"]),
    *_FILL_SECTION,                          # include trailing SeparatorBlock
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


def _t_entry_opened(p):
    return {**p, "_emoji": "📊", "_event_label": "ENTRY OPENED",
            "_avg_entry": p.get("avg_entry")}


def _t_entry_updated(p):
    avg = p["new_avg_entry"] if "new_avg_entry" in p else p.get("avg_entry")
    return {**p, "_emoji": "✏️", "_event_label": "ENTRY UPDATED", "_avg_entry": avg}
```

### ENTRY_CANCELLED

`cancelled_entry` (dict annidato) flattened via transform. `base_asset` calcolato una sola volta.

```python
_ENTRY_CANCELLED_BLOCKS: list[Block] = [
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

def _t_entry_cancelled(p):
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

---

## Update lifecycle

### _UPDATE_BLOCKS (unificato)

UPDATE_DONE, UPDATE_PARTIAL e UPDATE_REJECTED condividono la stessa block list.
Ogni transform inietta `_emoji`, `_event_label`, `_operations`, `_failed`.

Struttura: Operation → Changed → Failed → Reason (opzionale) → Footer.

- `Operation:` = azioni applicate (DONE/PARTIAL) o tentate (REJECTED)
- `Changed:` = delta su posizione, usa `_render_changed_item` (supporta `note` per riferimenti)
- `Failed:` = azioni rifiutate (solo PARTIAL)
- `Reason:` = motivo rifiuto (solo REJECTED, condizionale via `optional=True`)

> **Migrazione `entry_gate.py`**: L'attuale `display_lines` è usato solo per MOVE_STOP
> (SL old→new + eventuale "Reference:"). Va eliminato: MOVE_STOP deve scrivere nel dict `changed`
> come `{"field": "SL", "old": old_level, "new": new_level, "note": reference_str_or_None}`.
> `changed_fields` è dead code (mai popolato) — non migrato.

```python
_UPDATE_BLOCKS: list[Block] = [
    HeaderBlock(emoji=lambda p: p["_emoji"], event_label=lambda p: p["_event_label"]),
    ConditionalBlock(
        condition=lambda p: bool(p.get("_operations")),
        blocks=[
            StaticBlock("Operation:"),
            ListBlock(key="_operations", item_renderer=lambda op, i, p: [f"  • {op}"]),
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
        condition=lambda p: bool(p.get("_failed")),
        blocks=[
            StaticBlock("Failed:"),
            ListBlock(key="_failed", item_renderer=lambda a, i, p: [f"  • {a}"]),
        ]
    ),
    FieldBlock("Reason", key="reason", fmt=text),   # optional=True → assente in DONE/PARTIAL
    FooterBlock(default_source="runtime"),
]


def _t_update_done(p):
    ops = p.get("applied_actions") or []
    return {**p, "_emoji": "✅", "_event_label": "UPDATE DONE",
            "_operations": ops, "_failed": []}


def _t_update_partial(p):
    ops    = p.get("applied_actions") or []
    failed = p.get("rejected_actions") or []
    return {**p, "_emoji": "⚠️", "_event_label": "UPDATE PARTIAL",
            "_operations": ops, "_failed": failed}


def _t_update_rejected(p):
    ops = p.get("rejected_actions") or []
    return {**p, "_emoji": "❌", "_event_label": "UPDATE REJECTED",
            "_operations": ops, "_failed": []}
```

---

## Partial close

`event_label="UPDATE DONE"` — parità con codice attuale (`_partial_close_executed` usa questo label).

```python
_PARTIAL_CLOSE_BLOCKS: list[Block] = [
    HeaderBlock(emoji="✅", event_label="UPDATE DONE"),
    StaticBlock("Executed:"),
    StaticBlock(f"{_BULLET} CLOSE_PARTIAL"),
    SeparatorBlock(),
    FieldBlock("Price",  key="fill_price",  fmt=num),
    FieldBlock("Qty",    key="closed_qty",  fmt=num),
    FieldBlock("Closed", key="closed_pct",  fmt=pct),
    FieldBlock("PnL",    key="pnl",         fmt=money_signed),
    FieldBlock("Fee",    key="fee",         fmt=money),
    FooterBlock(default_source="manual_command"),
]
```

---

## Notifiche semplici

```python
_PENDING_TIMEOUT_BLOCKS: list[Block] = [
    HeaderBlock(emoji="⏰", event_label="PENDING ENTRY EXPIRED"),
    StaticBlock("Timeout: order expired before fill"),
    FooterBlock(default_source="timeout_worker"),
]

_REENTRY_BLOCKS: list[Block] = [
    HeaderBlock(emoji="🔄", event_label="REENTRY ACCEPTED"),
    FieldBlock("Previous chain",
               value_fn=lambda p: f"#{p['previous_chain_id']}" if p.get("previous_chain_id") is not None else None,
               fmt=text),
    FooterBlock(default_source="runtime"),
]

_CANCEL_FAILED_BLOCKS: list[Block] = [
    HeaderBlock(emoji="🚨", event_label="CANCEL FAILED"),
    DerivedBlock(text_fn=lambda p:
        f"Cancellation of {p.get('entry_ref', 'entry')} failed after {p.get('attempts', 3)} attempts."
    ),
    StaticBlock("Requires manual review required to resolve the position."),
    FieldBlock("Entry price", key="entry_price", fmt=num),
    FooterBlock(default_source="timeout_worker"),
]

_RECONCILIATION_WARN_BLOCKS: list[Block] = [
    HeaderBlock(emoji="⚠️", event_label="RECONCILIATION WARNING"),
    FieldBlock("Issue",  key="issue",  fmt=text),
    FieldBlock("Risk",   key="risk",   fmt=text),
    FieldBlock("Action", key="action", fmt=text),
    FooterBlock(default_source="runtime"),
]

_RECONCILIATION_FIXED_BLOCKS: list[Block] = [
    HeaderBlock(emoji="✅", event_label="RECONCILIATION FIXED"),
    FieldBlock("Issue resolved", key="issue", fmt=text),
    FooterBlock(default_source="runtime"),
]
```

---

## Multi-chain

Il tipo più complesso. Usa `DerivedBlock` per la prima riga (struttura header diversa dagli altri —
nessun `chain_id`, nessuna riga symbol/side). `_t_multi_chain` inietta `_has_issues` e `_counts`.

```python
_MULTI_CHAIN_BLOCKS: list[Block] = [
    DerivedBlock(text_fn=lambda p:
        ("⚠️" if p["_has_issues"] else "✅")
        + f" UPDATE APPLICATO - {len(p.get('chains') or [])} chain"
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
```

### `_render_chain_item`

Accede a `p["summary_kind"]` per decidere se mostrare `display_lines`. Restituisce `_SEP`
come ultimo elemento — necessario perché ogni chain termina con separatore.

```python
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
```

### `_fmt_counts`

Legge `_counts` iniettato dal transform. Logica condizionale per `final_close` vs altri:

```python
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
```

### `_t_multi_chain`

```python
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

---

## TEMPLATE_REGISTRY e dispatcher

```python
TEMPLATE_REGISTRY: dict[str, TemplateConfig] = {
    "SIGNAL_ACCEPTED":        TemplateConfig(_SIGNAL_BASE_BLOCKS,  _t_signal_accepted),
    "SIGNAL_REJECTED":        TemplateConfig(_SIGNAL_BASE_BLOCKS,  _t_signal_rejected),
    "REVIEW_REQUIRED":        TemplateConfig(_REVIEW_REQUIRED_BLOCKS),
    "ENTRY_OPENED":           TemplateConfig(_ENTRY_BLOCKS,        _t_entry_opened),
    "ENTRY_UPDATED":          TemplateConfig(_ENTRY_BLOCKS,        _t_entry_updated),
    "ENTRY_CANCELLED":        TemplateConfig(_ENTRY_CANCELLED_BLOCKS, _t_entry_cancelled),
    "SL_FILLED":              TemplateConfig(_CLOSED_BLOCKS,       _t_sl_filled),
    "TP_FILLED_FINAL":        TemplateConfig(_CLOSED_BLOCKS,       _t_tp_final),
    "POSITION_CLOSED":        TemplateConfig(_CLOSED_BLOCKS,       _t_position_closed),
    "BE_EXIT":                TemplateConfig(_CLOSED_BLOCKS,       _t_be_exit),
    "TP_FILLED":              TemplateConfig(_TP_PARTIAL_BLOCKS,   _t_tp_partial),
    "UPDATE_DONE":            TemplateConfig(_UPDATE_BLOCKS,       _t_update_done),
    "UPDATE_PARTIAL":         TemplateConfig(_UPDATE_BLOCKS,       _t_update_partial),
    "UPDATE_REJECTED":        TemplateConfig(_UPDATE_BLOCKS,       _t_update_rejected),
    "PARTIAL_CLOSE_EXECUTED": TemplateConfig(_PARTIAL_CLOSE_BLOCKS),
    "PENDING_ENTRY_EXPIRED":  TemplateConfig(_PENDING_TIMEOUT_BLOCKS),
    "REENTRY_ACCEPTED":       TemplateConfig(_REENTRY_BLOCKS),
    "CANCEL_FAILED":          TemplateConfig(_CANCEL_FAILED_BLOCKS),
    "RECONCILIATION_WARNING": TemplateConfig(_RECONCILIATION_WARN_BLOCKS),
    "RECONCILIATION_FIXED":   TemplateConfig(_RECONCILIATION_FIXED_BLOCKS),
    "MULTI_CHAIN_SUMMARY":    TemplateConfig(_MULTI_CHAIN_BLOCKS,  _t_multi_chain),
    "MULTI_CHAIN_UPDATE":     TemplateConfig(_MULTI_CHAIN_BLOCKS,  _t_multi_chain),
    "MULTI_CHAIN_CLOSED":     TemplateConfig(_MULTI_CHAIN_BLOCKS,  _t_multi_chain),
}
```

`clean_log.py` diventa un thin dispatcher di ~20 righe:

```python
def format_clean_log(notification_type: str, payload: dict) -> str:
    config = TEMPLATE_REGISTRY.get(notification_type)
    if config:
        return render_template(config.blocks, payload, transform=config.payload_transform)
    return _fallback(notification_type, payload)
```

---

## Invarianti garantite

| Cosa | Prima | Dopo |
|------|-------|------|
| Modificare un campo in POSITION CLOSED | Toccare `_closed_template()` in `clean_log.py` | Toccare `CLOSE_METRICS` o `FINAL_RESULT` in `templates/clean_log.py` |
| Aggiungere un nuovo notification type | Aggiungere funzione + if/elif | Definire block list (o riusare esistente) + entry nel registry |
| Separatori dinamici | `_finalize` | `_finalize` invariato |
| Logica business | Fuori dal formatter | Fuori dal formatter |
| Test esistenti | Output attuale | Output identico — i test devono continuare a passare senza modifiche |

---

## Dipendenze nuove

Nessuna. Zero pacchetti aggiuntivi.

---

## Scope escluso

- Hot-reload dei template a runtime — non richiesto.
- File di config esterni (YAML/JSON) — non richiesto.
- Modifica del meccanismo `_finalize` / `_SEP` — invariato.
- Layer business (lifecycle, workers, outbox writer) — non toccati.
- `tech_log.py` — non migrato in questa fase: usa separatore fisso e parametro `delivery_mode`
  che non si mappa sul sistema template. Rimane hardcoded.
