---
name: pydantic-models
description: Usa questa skill quando devi aggiungere o modificare i modelli Pydantic canonici del parser (TraderParseResult, Price, Intent, TargetRef, NewSignalEntities, UpdateEntities), o quando devi costruire un'entità tipizzata per un nuovo tipo di messaggio.
---

# Obiettivo

Mantenere il contratto canonico tra il layer parser e tutti i consumer downstream (validazione, operation rules, execution, backtesting). Ogni campo è tipizzato e validato da Pydantic v2.

# Quando usarla

- aggiunta di un nuovo campo a TraderParseResult
- creazione di nuove entità per un tipo di messaggio non ancora coperto
- modifica al modello Price (nuovi formati numerici)
- aggiunta di un nuovo intent kind o target ref method
- debug di errori di validazione Pydantic nel parser

# File coinvolti

```
src/parser/models/
├── canonical.py     ← Price, Intent, TargetRef, TraderParseResult
├── new_signal.py    ← NewSignalEntities
├── update.py        ← UpdateEntities
└── __init__.py
```

# Convenzioni obbligatorie

- `from __future__ import annotations` in ogni file
- `model_config = ConfigDict(frozen=True)` sui modelli immutabili (Price, Intent)
- nessun `dict` raw — tutto tipizzato
- `model_validator(mode="after")` per invarianti cross-field
- type hints ovunque, niente `Any` salvo casi documentati

# Modello Price

`Price` è immutabile e normalizza automaticamente il valore grezzo.

```python
# Costruzione da stringa grezza (caso normale)
price = Price.from_raw("90 000.5", decimal_separator=".", thousands_separator=" ")
# → Price(raw="90 000.5", value=90000.5)

# Costruzione da float già normalizzato
price = Price.from_float(90000.5)
# → Price(raw="90000.5", value=90000.5)
```

`normalize_price()` supporta:
- separatore decimale `.` o `,`
- migliaia con spazio (formato russo/francese: `"90 000"`)
- migliaia con virgola o punto
- il `number_format` del profilo determina quale usare

# TraderParseResult — campi chiave

```python
message_type:     "NEW_SIGNAL" | "UPDATE" | "INFO_ONLY" | "UNCLASSIFIED"
completeness:     "COMPLETE" | "INCOMPLETE" | None   # None se non NEW_SIGNAL
missing_fields:   list[str]                          # campi assenti in INCOMPLETE
entities:         NewSignalEntities | UpdateEntities | None
intents:          list[Intent]                       # vuota se non UPDATE
target_ref:       TargetRef | None
confidence:       float 0.0–1.0
warnings:         list[str]
trader_id:        str
raw_text:         str
acquisition_mode: "live" | "catchup"
```

Invariante validata automaticamente:
- `completeness` deve essere set se e solo se `message_type == "NEW_SIGNAL"`

# Intent

```python
Intent(name="U_MOVE_STOP", kind="ACTION")
Intent(name="U_TP_HIT",    kind="CONTEXT")
```

Intents CONTEXT: `U_TP_HIT`, `U_SL_HIT`
Intents ACTION: `U_MOVE_STOP`, `U_CLOSE_FULL`, `U_CLOSE_PARTIAL`, `U_CANCEL_PENDING`,
                `U_REENTER`, `U_ADD_ENTRY`, `U_MODIFY_ENTRY`, `U_UPDATE_TAKE_PROFITS`

# TargetRef

```python
# STRONG — riferimento esatto
TargetRef(kind="STRONG", method="REPLY")
TargetRef(kind="STRONG", method="TELEGRAM_LINK", ref="t.me/channel/123")
TargetRef(kind="STRONG", method="EXPLICIT_ID", ref=456)

# SYMBOL — cerca tra posizioni aperte del trader
TargetRef(kind="SYMBOL", symbol="BTCUSDT")

# GLOBAL — si applica a un gruppo di posizioni
TargetRef(kind="GLOBAL", scope="all_long")
```

Invariante: ogni kind richiede il suo campo specifico (method / symbol / scope).

# NewSignalEntities — campi

```
symbol:       str — uppercase, es. "BTCUSDT"
direction:    "LONG" | "SHORT"
entry_type:   "MARKET" | "LIMIT" | "AVERAGING" | "ZONE"
entries:      list[Price]   # opzionale per MARKET
stop_loss:    Price         # obbligatorio
take_profits: list[Price]   # almeno uno
leverage:     float | None
risk_pct:     float | None
conditions:   str | None    # testo libero non strutturato
```

Completeness:
- `COMPLETE` → symbol, direction, entry_type, stop_loss, ≥1 take_profit presenti
- `INCOMPLETE` → uno o più mancanti; popolare `missing_fields` in TraderParseResult

# UpdateEntities — struttura per intent

Ogni intent ACTION ha le sue entità:

```
U_MOVE_STOP           → new_sl_level: Price | None
U_CLOSE_FULL          → (nessuna entità specifica)
U_CLOSE_PARTIAL       → close_pct: float
U_CANCEL_PENDING      → (nessuna entità specifica)
U_REENTER             → entries: list[Price], entry_type: str
U_ADD_ENTRY           → new_entry_price: Price, entry_type: str
U_MODIFY_ENTRY        → old_entry_price: Price, new_entry_price: Price | None
U_UPDATE_TAKE_PROFITS → old_take_profits: list[Price] | None, new_take_profits: list[Price]
```

# Regole

- non modificare `canonical.py` senza aggiornare tutti i profili che lo istanziano
- non aggiungere logica estrattiva nei modelli — sono solo contenitori validati
- se un campo è opzionale in alcuni trader ma obbligatorio in altri, renderlo opzionale nel modello canonico e validarlo nel profilo
- dopo ogni modifica ai modelli, rieseguire tutti i test dei profili

# Output richiesto

Quando usi questa skill, restituisci:
- file toccati con le modifiche specifiche
- invarianti aggiunte o modificate
- impatto sui profili esistenti (trader_3, trader_a, trader_b, trader_c, trader_d)
- test aggiornati o da aggiornare
