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
│   ├── clean_log.py        ← tutte le TemplateConfig + TEMPLATE_REGISTRY
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
    """
    key: str
    item_renderer: Callable[[Any, int, dict], list[str]]
    fallback_key: str | None = None
    index_start: int = 1

@dataclass
class FooterBlock:
    """Source: <x> + optional link. Gestisce anche trader_id, account_id, rejected reason."""
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
| `money(v)` | `12.34 USDT`, `n/a` se None |
| `money_signed(v)` | `+12.34 USDT` / `-5.00 USDT` |
| `pct(v)` | `12.34%` |
| `pct_signed(v)` | `+12.34%` / `-5.00%` |
| `fee_rate(v)` | `float * 100` con 3 decimali |

Tutti restituiscono `"n/a"` se `value is None`.

---

## Sezioni condivise riusabili

```python
CLOSE_METRICS: list[Block] = [
    FieldBlock(label=lambda p: p.get("exit_label", "Price"), key="exit_price",
               fmt=num, optional=False, default="n/a"),
    FieldBlock("Qty",      key="closed_qty",  fmt=num),
    FieldBlock("PnL",      key="pnl",         fmt=money_signed),
    FieldBlock("Fee",      key="fee",          fmt=money),
    FieldBlock("Fee rate", key="fee_rate",     fmt=fee_rate),
]

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

---

## Tipi close — stessi block, `payload_transform` diverso

```python
_CLOSED_BLOCKS: list[Block] = [
    HeaderBlock(emoji=lambda p: p["_emoji"], event_label="POSITION CLOSED"),
    SeparatorBlock(),
    FieldBlock("Close reason", key="close_reason", optional=False, default="n/a"),
    SeparatorBlock(),
    *CLOSE_METRICS,
    *FINAL_RESULT,
    SeparatorBlock(),
    FooterBlock(default_source="exchange"),
]
```

Ogni tipo inietta `_emoji`, `exit_label`, `exit_price`, `close_reason` via `payload_transform`:

| Tipo | `_emoji` | `exit_label` | `exit_price` | `close_reason` |
|------|----------|-------------|-------------|---------------|
| `SL_FILLED` | 🛑 | `"SL"` | `sl_price \| fill_price` | `"SL_FILLED"` |
| `TP_FILLED_FINAL` | ✅ | `"TP_{level}"` | `fill_price \| tp_price` | `"FINAL TP FILLED"` |
| `POSITION_CLOSED` | ✋ | `"Price"` | `fill_price` | `close_reason \| "MANUAL_CLOSE"` |
| `BE_EXIT` | ⚡ | `"SL" \| "Price"` | `sl_price \| exit_price \| fill_price` | `"BREAKEVEN_AFTER_TP"` |

Modificare il layout di chiusura significa toccare `_CLOSED_BLOCKS` una sola volta — si propaga a tutti e quattro i tipi.

---

## Multi-chain

Il tipo più complesso. Usa `payload_transform` + `ListBlock` con `item_renderer` parent-aware + `DerivedBlock` per la riga counts.

```python
_MULTI_CHAIN_BLOCKS: list[Block] = [
    HeaderBlock(
        emoji=lambda p: "⚠️" if p["_has_issues"] else "✅",
        event_label=lambda p: f"UPDATE APPLICATO - {len(p.get('chains') or [])} chain",
    ),
    SeparatorBlock(),
    BranchBlock(
        condition=lambda p: p.get("summary_kind") == "final_close",
        then_blocks=[StaticBlock("Operation requested:")],
        else_blocks=[StaticBlock("Operations requested:")],
    ),
    ListBlock(key="requested_operations", fallback_key="operations",
              item_renderer=lambda item, i, p: [f"▪️ {item}"]),
    SeparatorBlock(),
    ListBlock(key="chains", item_renderer=_render_chain_item),
    DerivedBlock(text_fn=_fmt_counts),
    FooterBlock(),
]
```

`_render_chain_item(chain, i, p)` accede a `p["summary_kind"]` per decidere se mostrare `display_lines`. La logica di business che imposta `summary_kind` rimane invariata nel lifecycle/workers.

`_fmt_counts(p)` calcola la riga `"Done: 3 | Partial: 1 | ..."` con parti condizionali in base a `summary_kind` e valori zero/nonzero.

---

## TEMPLATE_REGISTRY e dispatcher

```python
TEMPLATE_REGISTRY: dict[str, TemplateConfig] = {
    "SIGNAL_ACCEPTED":        TemplateConfig(_SIGNAL_ACCEPTED_BLOCKS),
    "SIGNAL_REJECTED":        TemplateConfig(_SIGNAL_REJECTED_BLOCKS),
    "REVIEW_REQUIRED":        TemplateConfig(_REVIEW_REQUIRED_BLOCKS),
    "ENTRY_OPENED":           TemplateConfig(_ENTRY_OPENED_BLOCKS),
    "ENTRY_UPDATED":          TemplateConfig(_ENTRY_UPDATED_BLOCKS),
    "ENTRY_CANCELLED":        TemplateConfig(_ENTRY_CANCELLED_BLOCKS),
    "SL_FILLED":              TemplateConfig(_CLOSED_BLOCKS, _t_sl_filled),
    "TP_FILLED_FINAL":        TemplateConfig(_CLOSED_BLOCKS, _t_tp_final),
    "POSITION_CLOSED":        TemplateConfig(_CLOSED_BLOCKS, _t_position_closed),
    "BE_EXIT":                TemplateConfig(_CLOSED_BLOCKS, _t_be_exit),
    "TP_FILLED":              TemplateConfig(_TP_PARTIAL_BLOCKS),
    "UPDATE_DONE":            TemplateConfig(_UPDATE_DONE_BLOCKS),
    "UPDATE_PARTIAL":         TemplateConfig(_UPDATE_PARTIAL_BLOCKS),
    "UPDATE_REJECTED":        TemplateConfig(_UPDATE_REJECTED_BLOCKS),
    "PARTIAL_CLOSE_EXECUTED": TemplateConfig(_PARTIAL_CLOSE_BLOCKS),
    "PENDING_ENTRY_EXPIRED":  TemplateConfig(_PENDING_TIMEOUT_BLOCKS),
    "REENTRY_ACCEPTED":       TemplateConfig(_REENTRY_BLOCKS),
    "CANCEL_FAILED":          TemplateConfig(_CANCEL_FAILED_BLOCKS),
    "RECONCILIATION_WARNING": TemplateConfig(_RECONCILIATION_WARN_BLOCKS),
    "RECONCILIATION_FIXED":   TemplateConfig(_RECONCILIATION_FIXED_BLOCKS),
    "MULTI_CHAIN_SUMMARY":    TemplateConfig(_MULTI_CHAIN_BLOCKS, _t_multi_chain),
    "MULTI_CHAIN_UPDATE":     TemplateConfig(_MULTI_CHAIN_BLOCKS, _t_multi_chain),
    "MULTI_CHAIN_CLOSED":     TemplateConfig(_MULTI_CHAIN_BLOCKS, _t_multi_chain),
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
| Aggiungere un nuovo notification type | Aggiungere funzione + if/elif | Definire block + entry nel registry |
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
