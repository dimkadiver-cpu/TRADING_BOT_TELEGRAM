# Design: `cancel_unfilled_pending_after`

**Data:** 2026-06-28
**Stato:** Approvato — pronto per implementazione

---

## Problema

Il campo `cancel_unfilled_pending_after` (valori: `null | tp1 | tp2`) è presente nel modello
`ManagementPlanConfig` e nel config YAML ma non è implementato. Era marcato come "BLOCCATO:
richiede price-watcher non presente nell'architettura".

### Semantica

- **`cancel_averaging_pending_after`** — quando TP N scatta (c'è stata almeno una fill), cancella
  le leg averaging (`sequence > 1`) ancora PENDING.
- **`cancel_unfilled_pending_after`** — nessuna leg è mai stata fillata, ma il prezzo ha già
  superato il livello TP configurato: il setup non ha più senso → annullare l'intera chain.

La differenza chiave: la seconda non ha un evento di fill su cui agganciarsi. Richiede un
price-watcher periodico indipendente.

---

## Soluzione: worker periodico `UnfilledPriceWatcher`

### Architettura generale

Nuovo task async avviato nel bootstrap insieme agli altri worker. Gira ogni `N` secondi
(default 60, configurabile).

**Per ogni tick:**

1. Query DB — carica chain in stato `WAITING_ENTRY` dove:
   - `cancel_unfilled_pending_after` ≠ `null` (letto da `management_plan_json`)
   - `cancel_pending_by_engine = true` (gate globale)
   - Nessuna leg ha status `FILLED` nel `plan_state_json`

2. Raggruppa per `(execution_account_id, symbol)` → 1 chiamata `fetch_mark_price` per gruppo.

3. Per ogni chain: risolve la soglia TP, confronta con mark price in base al `side`.

4. Se condizione soddisfatta: emette `LifecycleEvent(UNFILLED_TP_CANCEL)` +
   `ExecutionCommand(CANCEL_PENDING_ENTRY)` con idempotency key `unfilled_tp_cancel:{chain_id}`.
   La chain transisce a `EXPIRED`.

5. Emette notifica Telegram (clean-log) dedicata, distinta dal timeout.

### Logica di confronto

```python
plan = json.loads(chain.plan_state_json)
intermediate = plan.get("intermediate_tps", [])
final_tp = plan.get("final_tp")

# Risolvi soglia
if tp_level == "tp1":
    threshold = intermediate[0] if intermediate else final_tp
elif tp_level == "tp2":
    threshold = intermediate[1] if len(intermediate) > 1 else final_tp

# Direzione per side
if chain.side == "LONG":
    triggered = mark_price >= threshold   # prezzo sale oltre TP senza fill entry
else:  # SHORT
    triggered = mark_price <= threshold   # prezzo scende sotto TP senza fill entry
```

Se `threshold` risolve a `None` (plan senza TP prices), il worker salta la chain silenziosamente
(log warning).

**Check "nessuna leg fillata":**
```python
legs = plan.get("legs", [])
no_fill = all(leg.get("status") != "FILLED" for leg in legs)
```

### Configurazione intervallo

Letto da `global_safety` nel config YAML:
```yaml
global_safety:
  unfilled_price_check_interval_seconds: 60  # default
```
Se assente, usa 60 secondi.

---

## File coinvolti

### Nuovi
| File | Contenuto |
|---|---|
| `src/runtime_v2/lifecycle/unfilled_price_watcher.py` | Worker async con ciclo di check |

### Modificati
| File | Modifica |
|---|---|
| `src/runtime_v2/lifecycle/models.py` | Aggiunge `"UNFILLED_TP_CANCEL"` a `LifecycleEventType` |
| `src/runtime_v2/control_plane/outbox_writer.py` | Mappa `UNFILLED_TP_CANCEL` → `"ENTRY_CANCELLED_TP_REACHED"` |
| `src/runtime_v2/control_plane/formatters/templates/clean_log.py` | Template notifica dedicata |
| `src/runtime_v2/control_plane/bootstrap.py` | Avvio del nuovo worker nel loop principale |
| `config/operation_config.yaml` | Aggiunge `unfilled_price_check_interval_seconds`, rimuove `//da implimentare` |
| `docs/debugging/stato_runtime_v2.md` | Segna feature come implementata |

---

## Evento e payload

```python
LifecycleEvent(
    trade_chain_id=chain_id,
    event_type="UNFILLED_TP_CANCEL",
    source_type="unfilled_price_watcher",
    payload_json=json.dumps({
        "tp_level": "tp1",          # o "tp2"
        "threshold_price": 42000.0,
        "mark_price": 43500.0,
        "cancel_reason": "unfilled_tp_reached",
    }),
    idempotency_key=f"unfilled_tp_cancel:{chain_id}",
)
```

L'`ExecutionCommand` emesso è `CANCEL_PENDING_ENTRY` — stesso usato dal timeout worker e dal
cancel manuale Telegram. Il `cancel_expander` esistente gestisce già la fan-out verso le singole
leg PENDING.

---

## Notifica clean-log

Template blocks (stesso sistema di `_PENDING_TIMEOUT_BLOCKS`):

```python
_UNFILLED_TP_CANCEL_BLOCKS: list = [
    HeaderBlock(emoji="⛔", event_label="SETUP CANCELLED"),
    DerivedBlock(text_fn=lambda p:
        f"Entry never filled. Price already crossed TP{p.get('tp_level', '?').lstrip('tp').upper()}."
    ),
    FieldBlock("Threshold", key="threshold_price", fmt=num),
    FieldBlock("Mark price", key="mark_price",     fmt=num),
    FooterBlock(default_source="unfilled_price_watcher"),
]
```

Output renderizzato:

```
⛔ #12 — SETUP CANCELLED
- - - - - - - - - - - - - - -
BTC/USDT — 📈 LONG
https://t.me/c/3897279123/480
- - - - - - - - - - - - - - -
Entry never filled. Price already crossed TP1.
- - - - - - - - - - - - - - -
Threshold: 42,000
Mark price: 43,500
- - - - - - - - - - - - - - -
Source: unfilled_price_watcher
```

Evento outbox: `ENTRY_CANCELLED_TP_REACHED` — distinto da `PENDING_ENTRY_EXPIRED` (timeout).

---

## Riconciliazione al restart

Non serve un meccanismo dedicato. Il primo ciclo del worker al restart interroga il DB e controlla
il prezzo corrente: se la condizione è soddisfatta, agisce. Questo primo ciclo è già la
riconciliazione.

**Garanzie di idempotenza:**
- Il `LifecycleEvent` usa `idempotency_key=f"unfilled_tp_cancel:{chain_id}"` →
  `INSERT OR IGNORE` scarta duplicati se il record era già in DB prima dello spegnimento.
- Se l'entry si è fillata sull'exchange durante il downtime (evento WebSocket perso), la chain
  non sarà più in `WAITING_ENTRY` → il worker la salta correttamente.
- Il problema di eventi WebSocket persi durante downtime è gestito dalla reconciliazione esistente
  (`audit_watch_positions`, etc.) — fuori scope di questa feature.

---

## Criteri di accettazione

| # | Criterio | Segnale |
|---|---|---|
| 1 | Chain `WAITING_ENTRY` con nessuna fill e prezzo oltre soglia → transisce a `EXPIRED` | Lifecycle event `UNFILLED_TP_CANCEL` in DB |
| 2 | Notifica clean-log distinta dal timeout | Evento outbox `ENTRY_CANCELLED_TP_REACHED` |
| 3 | 1 sola chiamata `fetch_mark_price` per simbolo per tick | Log worker |
| 4 | Se `cancel_pending_by_engine = false`, il worker non agisce | Test unitario |
| 5 | Idempotency: secondo ciclo su stessa chain non emette eventi duplicati | `INSERT OR IGNORE` + test |
| 6 | Config `null` → worker ignora la chain | Test unitario |
