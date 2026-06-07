# Guida al sistema di template — clean_log formatter

## File rilevanti

| File | Ruolo |
|------|-------|
| `_blocks.py` | Definizione blocchi + motore di rendering |
| `_formatters.py` | Funzioni di formattazione valori |
| `templates/clean_log.py` | Tutti i template (modifica qui) |
| `clean_log.py` | Dispatcher (tocca solo per routing speciale) |

---

## Blocchi disponibili

### `HeaderBlock(emoji, event_label)`
Header fisso: emoji + `#chain_id` + label, poi separatore, poi symbol/side se entrambi presenti.

```python
HeaderBlock("✅", "SIGNAL ACCEPTED")
HeaderBlock(emoji=lambda p: "📈" if p.get("side") == "LONG" else "📉",
            event_label=lambda p: f"ENTRY {p.get('seq', '')}")
```

### `FieldBlock(label, key=..., value_fn=..., fmt=num, optional=True, default="n/a")`
Riga `Label: valore`. Usa `key` per leggere dal payload, oppure `value_fn` per calcolare.

```python
FieldBlock("SL",     key="sl_price",   fmt=num)        # SL: 64,000
FieldBlock("PnL",    key="pnl",        fmt=money_signed)
FieldBlock("Risk",   key="risk_pct",   fmt=pct)
FieldBlock("Custom", value_fn=lambda p: p.get("a") or p.get("b"), fmt=num)
```

`optional=True` (default): se il valore è None, la riga non appare.
`optional=False`: mostra `n/a` anche se assente.

### `StaticBlock(text)`
Testo fisso, sempre visibile.

```python
StaticBlock("Close Metrics:")
StaticBlock("⚠️ Nota importante")
```

### `DerivedBlock(text_fn)`
Testo calcolato dal payload. Se `text_fn` restituisce falsy, la riga non appare.

```python
DerivedBlock(text_fn=lambda p: f"Reason: {p['reason']}" if p.get("reason") else "")
```

### `SeparatorBlock()`
Inserisce un separatore dinamico `- - - - - - - -`. La larghezza si adatta al contenuto.

```python
SeparatorBlock()
```

### `FooterBlock(...)`
Separatore + Source + link opzionale. Va sempre messo **alla fine**.

```python
FooterBlock()                                           # solo Source + link
FooterBlock(default_source="exchange")                  # source default se assente nel payload
FooterBlock(include_trader_id=True, include_account_id=True)  # aggiunge Trader + Account + secondo sep
FooterBlock(include_rejected_reason=True)               # aggiunge riga "Rejected: ..."
```

### `ConditionalBlock(condition, blocks)`
Mostra i blocchi solo se la condizione è vera.

```python
ConditionalBlock(
    condition=lambda p: bool(p.get("final_result")),
    blocks=FINAL_RESULT,
)
```

### `BranchBlock(condition, then_blocks, else_blocks)`
If/else dichiarativo.

```python
BranchBlock(
    condition=lambda p: p.get("is_final"),
    then_blocks=[StaticBlock("Posizione chiusa definitivamente")],
    else_blocks=[StaticBlock("Chiusura parziale")],
)
```

### `SectionBlock(label, blocks)`
Label statico seguito da sotto-blocchi (nessun separatore aggiuntivo).

```python
SectionBlock("Changed:", [
    ListBlock(key="changed", item_renderer=_render_changed_item),
])
```

### `ListBlock(key, item_renderer, fallback_key=None, index_start=1)`
Itera una lista nel payload chiamando `item_renderer(item, index, payload) -> list[str]`.

```python
ListBlock(key="entries", item_renderer=_render_entry_item)
ListBlock(key="take_profits", item_renderer=_render_tp_item, index_start=1)
```

---

## Formatter valori (`_formatters.py`)

| Funzione | Input | Output esempio |
|----------|-------|----------------|
| `num` | `64820.5` | `"64,820.5"` |
| `text` | `"hello"` | `"hello"` |
| `money` | `1.70` | `"1.70 USDT"` |
| `money_signed` | `-50.0` | `"-50.00 USDT"` |
| `pct` | `2.5` | `"2.5%"` |
| `pct_signed` | `-1.15` | `"-1.15%"` |
| `fee_rate` | `0.001` | `"0.100%"` |

Tutti restituiscono `"n/a"` se il valore è `None` o non numerico.

---

## Ricette pratiche

### Cambiare una label

```python
# Prima
FieldBlock("Price", key="fill_price", fmt=num)

# Dopo
FieldBlock("Fill price", key="fill_price", fmt=num)
```

### Rendere un campo obbligatorio (mostra n/a invece di sparire)

```python
FieldBlock("Qty", key="closed_qty", fmt=num, optional=False)
```

### Aggiungere un campo nuovo

Trova il template in `templates/clean_log.py` e aggiungi un `FieldBlock` nella posizione voluta:

```python
_CLOSED_BLOCKS: list = [
    HeaderBlock("🛑", "POSITION CLOSED"),
    *CLOSE_METRICS,
    FieldBlock("Spread",  key="spread_pct", fmt=pct),   # ← aggiunto
    ConditionalBlock(...),
    ...
]
```

### Eliminare un campo

Rimuovi il `FieldBlock` corrispondente dalla lista.

### Spostare un campo

Taglia la riga dal punto A e incollala nel punto B nella stessa lista di blocchi.

### Aggiungere un separatore manuale

```python
_MY_BLOCKS: list = [
    HeaderBlock("✅", "MY EVENT"),
    FieldBlock("A", key="a", fmt=num),
    SeparatorBlock(),           # ← separatore qui
    FieldBlock("B", key="b", fmt=num),
    FooterBlock(),
]
```

**Nota:** non aggiungere `SeparatorBlock()` subito prima di `FooterBlock()` — il footer aggiunge già il suo separatore interno.

### Aggiungere un testo fisso condizionale

```python
ConditionalBlock(
    condition=lambda p: p.get("is_partial"),
    blocks=[StaticBlock("⚠️ Segnale parziale — dati incompleti")],
)
```

### Cambiare emoji dell'header

```python
HeaderBlock("📊", "MY EVENT")               # emoji fissa
HeaderBlock(lambda p: "🟢" if p.get("ok") else "🔴", "MY EVENT")  # emoji dinamica
```

### Cambiare event_label dell'header

```python
HeaderBlock("✅", lambda p: f"TP_{p.get('tp_level')} FILLED")
```

### Aggiungere una sezione con label

```python
SectionBlock("Entry aperte:", [
    ListBlock(key="open_entries", item_renderer=_render_pending_entry),
])
```

### Campo con valore calcolato (non direttamente in payload)

```python
FieldBlock("Avg entry",
           value_fn=lambda p: (p.get("entry_a", 0) + p.get("entry_b", 0)) / 2,
           fmt=num)
```

### Template completamente custom

Aggiungi in `TEMPLATE_REGISTRY` alla fine di `templates/clean_log.py`:

```python
_MY_EVENT_BLOCKS: list = [
    HeaderBlock("🔔", "MY EVENT"),
    FieldBlock("Symbol", key="symbol", fmt=text),
    FieldBlock("Valore", key="my_value", fmt=num),
    FooterBlock(),
]

TEMPLATE_REGISTRY = {
    ...
    "MY_EVENT": TemplateConfig(blocks=_MY_EVENT_BLOCKS),
}
```

Il dispatcher in `clean_log.py` lo troverà automaticamente.

---

## Blocchi condivisi (non duplicare)

Questi blocchi sono già definiti in `templates/clean_log.py` e usati da più template. Modificarli cambia tutti i template che li usano.

| Variabile | Usata in |
|-----------|----------|
| `CLOSE_METRICS` | SL_FILLED, TP_FILLED_*, POSITION_CLOSED, BE_EXIT |
| `FINAL_RESULT` | SL_FILLED, TP_FILLED_FINAL, POSITION_CLOSED, BE_EXIT |
| `_FILL_SECTION` | ENTRY_OPENED, ENTRY_UPDATED |
| `_SIGNAL_BODY` | SIGNAL_ACCEPTED, SIGNAL_PARTIAL, REVIEW_REQUIRED |
| `_ENTRY_POSITION_SECTION` | ENTRY_OPENED, ENTRY_UPDATED |

---

## Come trovare il template di un tipo specifico

1. Apri `templates/clean_log.py`
2. Cerca `TEMPLATE_REGISTRY` (in fondo al file)
3. Trova la chiave corrispondente al tipo di notifica, es. `"TP_FILLED_FINAL"`
4. Il valore punta a una `TemplateConfig` con `blocks=_t_qualcosa` — risali alla definizione

```python
# Esempio: per modificare TP_FILLED_FINAL
"TP_FILLED_FINAL": TemplateConfig(blocks=_CLOSED_BLOCKS, payload_transform=_t_tp_final),
#                                          ↑ blocchi fissi    ↑ transform che modifica il payload prima del render
```

Se c'è un `payload_transform`, la funzione riceve il payload originale e restituisce un dizionario arricchito. I blocchi leggono da quello.

---

## Testare le modifiche

```bash
# Test veloci su tutti i formatter
pytest tests/runtime_v2/control_plane/test_blocks_formatters.py \
       tests/runtime_v2/control_plane/test_clean_log_formatter.py \
       tests/runtime_v2/control_plane/test_clean_log_formatter_full.py -v
```

Per vedere il rendering reale di un template:

```python
from src.runtime_v2.control_plane.formatters.clean_log import format_clean_log

print(format_clean_log("SIGNAL_ACCEPTED", {
    "chain_id": 99,
    "symbol": "BTC/USDT",
    "side": "LONG",
    "entries": [{"sequence": 1, "price": 65000, "entry_type": "LIMIT"}],
    "take_profits": [68000, 71000],
    "sl_price": 62000,
    "risk_pct": 2.0,
    "leverage": 5,
    "trader_id": "trader_test",
    "account_id": "main",
    "source": "trader_signal",
    "link": "https://t.me/c/123/456",
}))
```
