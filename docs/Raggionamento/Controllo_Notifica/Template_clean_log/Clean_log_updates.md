UPDATE_DONE, UPDATE_PARTIAL, UPDATE_REJECTED

Struttura comune (`_UPDATE_BLOCKS`):
- `Operation:` — azioni ordinate/applicate, bullet ▪️
- `Changed:` — delta su campi posizione, bullet ▪️
- `Failed: reason` — su riga singola, dopo separatore, solo se presente
- Footer: Source + link al comando trader

`_BULLET = "▪️"`

---

## UPDATE DONE — operazione singola

```
✅ #1 — UPDATE DONE
- - - - - - - - - - - - - - -
BTCUSDT — 📈 LONG
https://t.me/c/3897279123/480
- - - - - - - - - - - - - - -
Operation:
▪️ MOVE_SL_TO_BE
Changed:
▪️ SL: 66,400 → 65,000 *
- - - - - - - - - - - - - - -
* Reference: Entry avg
- - - - - - - - - - - - - - -
Source: trader_update
https://t.me/c/3927267771/376
```

---

## UPDATE DONE — operazioni multiple

```
✅ #1 — UPDATE DONE
- - - - - - - - - - - - - - -
BTCUSDT — 📈 LONG
https://t.me/c/3897279123/480
- - - - - - - - - - - - - - -
Operation:
▪️ CLOSE_FULL
▪️ MOVE_SL_TO_BE                
Changed:
▪️ Position: open → closed 100%
▪️ SL: 66,400 → 68,500 *
- - - - - - - - - - - - - - -
* Reference: Entry avg
- - - - - - - - - - - - - - -
Source: trader_update
https://t.me/c/3927267771/376

---

## UPDATE DONE — close parziale (ack runtime; il fill arriva come PARTIAL CLOSED separato)

```
✅ #12 — UPDATE DONE
- - - - - - - - - - - - - - -
BTCUSDT — 📈 LONG
https://t.me/c/123456/987
- - - - - - - - - - - - - - -
Operation:
▪️ CLOSE_PARTIAL
▪️ MOVE_SL_TO_BE
▪️ CANCEL_PENDING
Changed:
▪️ Position: open → closed 50%
▪️ SL: 39,000 → 65,000 (BE)
▪️ Entry_2: 61,192.03 -> cancelled
- - - - - - - - - - - - - - -
Source: trader_update
https://t.me/c/3927267771/376
```

> Nessun dato di fill qui — il fill exchange arriva in PARTIAL CLOSED separato.

---

## UPDATE REJECTED

```
❌ #2 — UPDATE REJECTED
- - - - - - - - - - - - - - -
ICNTUSDT — 📈 LONG
https://t.me/c/3897279123/489
- - - - - - - - - - - - - - -
Operation:
▪️ MOVE_SL_TO_BE
- - - - - - - - - - - - - - -
Failed: unsupported_set_stop_target_type
- - - - - - - - - - - - - - -
Source: trader_update
https://t.me/c/3927267771/376
```

> Nessuna sezione `Changed:` — niente è stato applicato.

---

## UPDATE PARTIAL

```
⚠️ #2 — UPDATE PARTIAL
- - - - - - - - - - - - - - -
ICNTUSDT — 📈 LONG
https://t.me/c/3897279123/489
- - - - - - - - - - - - - - -
Operation:
▪️ CANCEL_PENDING * 
▪️ MOVE_SL_TO_BE
Changed:
▪️ SL: 66,400 → 68,500
- - - - - - - - - - - - - - -
* Failed: cancel_pending_order_not_found
- - - - - - - - - - - - - - -
Source: trader_update
https://t.me/c/3927267771/376
```

> `*` mostra le azioni non applicate.
> `Failed:` riporta il motivo del fallimento parziale.

---

## Note implementative

| Campo | Fonte | Nota |
|-------|-------|------|
| `_operations` | DONE: `applied_actions`; PARTIAL: tutte le azioni nell'ordine originale, fallite con `*`; REJECTED: `rejected_actions` | Lista stringhe già formattate |
| `changed` | dict `{"field", "old", "new", "note"}` per ogni campo modificato | `_render_changed_item` aggiunge `▪️` e `*` se nota presente (nota non inline) |
| `_footnotes` | Note da `changed["note"]` + `Failed: {reason}` per ogni azione PARTIAL (action già visibile con `*` in Operation) | `None` se vuota → sezione assente; preceduta da SEP |
| `_failed_reason` | `reason` o `failed_reason` dal payload | Solo REJECTED — `None` per DONE/PARTIAL |
| `failed_actions` | `[{"action": str, "reason": str}]` | Solo PARTIAL — costruito in `entry_gate` da ogni NOOP event con `removeprefix("NOOP_")` |

`Failed:` (senza `*`) appare solo per REJECTED via `_failed_reason` — preceduto da SEP.
`Footnotes:` (righe `* ...`) raccoglie note changed + azioni fallite PARTIAL — precedute da SEP.
`Changed:` assente se lista vuota (es. REJECTED dove niente è stato applicato).
