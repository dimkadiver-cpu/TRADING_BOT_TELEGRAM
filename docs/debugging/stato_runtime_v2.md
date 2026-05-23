# Stato runtime_v2 — lifecycle & execution gateway

Documento aggiornato: 2026-05-23

---

## Bug risolti in sessione (live demo)

### 1. `SYNC_PROTECTIVE_ORDERS` falliva su SL attached
**File:** `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/adapter.py`
**Causa:** `edit_order` su un SL position-level (creato via `trading_stop`) → Bybit retCode 10001.
Il codice tornava errore immediato senza provare il path `trading_stop`.
**Fix:** Fall-through al path `trading_stop` in caso di eccezione su `edit_order`.

### 2. Chain finiva in `CANCELLED` con posizione aperta dopo cancel pending
**File:** `src/runtime_v2/lifecycle/event_processor.py`
**Causa:** `position_already_open = raw.filled_qty > 0.0` controllava i fill della leg cancellata
(sempre 0 per ordini pending non fillati), non se la posizione esisteva.
Con TWO_STEP: leg 1 fillata → posizione aperta; leg 2 cancellata (fill=0) → chain → `CANCELLED`.
**Fix:** `position_already_open = chain.open_position_qty > 0` — deriva dallo stato reale della chain.

### 3. `SYNC_PROTECTIVE_ORDERS` emesso inutilmente per attached-SL modes
**File:** `src/runtime_v2/lifecycle/entry_gate.py`, `event_processor.py`
**Causa:** Per i modi con SL position-level (UNIFIED_PLAN, C_*, D_*) il SL copre
automaticamente tutta la posizione — nessun aggiornamento qty necessario.
**Fix:** SYNC skippato quando `chain.execution_mode in _ATTACHED_PROTECTION_MODES`.
Applicato in entrambi i punti di emissione: `_apply_cancel_pending` e
`_process_pending_entry_cancelled_confirmed`.

### 4. Comandi fire-and-forget bloccati in `SENT`
**File:** `src/runtime_v2/execution_gateway/gateway.py`
**Causa:** `CANCEL_PENDING_ENTRY`, `SYNC_PROTECTIVE_ORDERS`, `MOVE_STOP_*`,
`SET_POSITION_TPSL_*` non creano ordini pollabili su Bybit. Il sync worker cercava
il `client_order_id` del comando → niente → SENT per sempre.
**Fix:** Aggiunto `_FIRE_AND_FORGET` — questi comandi vengono marcati `DONE` immediatamente
dopo `mark_sent`.

---

## UPDATE da Telegram — stato supporto

| `action_type` | Sottocaso | Comando emesso | Stato |
|---|---|---|---|
| `SET_STOP` | `target_type=ENTRY` | `MOVE_STOP_TO_BREAKEVEN` | ✅ |
| `SET_STOP` | altro target | → REVIEW | ✅ |
| `CLOSE` | `FULL` | `CLOSE_FULL` | ✅ |
| `CLOSE` | `PARTIAL` | `CLOSE_PARTIAL` | ✅ |
| `CANCEL_PENDING` | — | `CANCEL_PENDING_ENTRY` | ✅ |
| `MODIFY_ENTRIES` | `MARKET_NOW / UPDATE_PRICE / REPLACE_ENTRY` | `CANCEL_PENDING_ENTRY` + re-entry | ✅ |
| qualsiasi altro | — | → REVIEW | ✅ |

**Non implementati** (vanno in REVIEW con reason `unsupported_action_type:X`):
- `SET_STOP` su target diverso da ENTRY (es. prezzo esplicito)
- qualsiasi action_type fuori dalla lista sopra

---

## Automatismi da exchange (event_processor)

| Exchange event | Cosa fa | Stato |
|---|---|---|
| `ENTRY_FILLED` | Aggiorna qty/avg, emette TP/SL successivi (UNIFIED_PLAN) | ✅ |
| `TP_FILLED` non finale | Aggiorna qty, emette `MOVE_STOP_TO_BREAKEVEN` se `be_trigger` corrisponde | ✅ |
| `TP_FILLED` finale | Chain → `CLOSED` | ✅ |
| `SL_FILLED` | Chain → `CLOSED` | ✅ |
| `CLOSE_FULL_FILLED` | Chain → `CLOSED` | ✅ |
| `CLOSE_PARTIAL_FILLED` | Aggiorna qty, chain → `PARTIALLY_CLOSED` | ✅ |
| `STOP_MOVED_CONFIRMED` | Aggiorna `current_stop_price`, eventuale `PROTECTED` | ✅ |
| `PENDING_ENTRY_CANCELLED_CONFIRMED` | Posizione aperta → OPEN; altrimenti → `CANCELLED` | ✅ (fix #2) |

---

## Automatismi da `management_plan`

### Implementati

| Campo | Trigger | Effetto |
|---|---|---|
| `be_trigger` (tp1/tp2/tp3) | TP N colpito | Emette `MOVE_STOP_TO_BREAKEVEN` automatico |
| `be_buffer_pct` | Con `be_trigger` | Aggiunge buffer % al prezzo BE |
| `close_distribution` | Ogni TP | Calcola % di chiusura per TP successivo |
| `cancel_pending_on_timeout` + `pending_timeout_hours` | Worker periodico | Cancella entry pending scaduta → chain `EXPIRED` |

### Definiti ma NON ancora implementati

| Campo | Semantica | Note |
|---|---|---|
| `cancel_averaging_pending_after` | Cancella leg averaging dopo tp1/tp2 | Solo nel modello |
| `cancel_unfilled_pending_after` | Cancella entry non fillata dopo tp1/tp2 | Solo nel modello |
| `cancel_pending_by_engine` | On/off del cancel automatico da engine | Solo nel modello |
| `risk_freed_by_be` | Libera rischio allocato quando BE scatta | Solo nel modello |
| `protective_sl_mode` | `exchange_native_first` vs `bot_managed` | Solo nel modello |

---

## Da fare / verificare

### Funzionalità mancanti nel lifecycle

- [ ] `cancel_averaging_pending_after` — implementare logica in `event_processor._process_tp_filled`
- [ ] `cancel_unfilled_pending_after` — idem
- [ ] `risk_freed_by_be` — aggiornare `risk_remaining` della chain quando BE scatta
- [ ] `SET_STOP` su prezzo esplicito (non solo ENTRY) — ora va in REVIEW

### Test live ancora da completare (da `Test.md`)

- [ ] `CLOSE_PARTIAL` su posizione aperta
- [ ] `CLOSE_FULL` su posizione aperta
- [ ] `MOVE_STOP_TO_BREAKEVEN` da Telegram
- [ ] `MOVE_STOP` (non implementato nel gateway bybit?)
- [ ] `CANCEL_PENDING_ENTRY` + `MOVE_STOP_TO_BREAKEVEN` in unico messaggio
- [ ] TP hit automatico → verifica aggiornamento qty + SYNC skip per attached
- [ ] BE automatico da `be_trigger` — verifica end-to-end
- [ ] Caso `Market + SL + TP` (Caso 2)
- [ ] Caso `Market + Limit + SL + TP` (Caso 2_1)

### Domande aperte (da `Altro.md`)

- [ ] Cosa succede se un ordine non parte per errore e si piazza poi un ordine sullo stesso simbolo?
- [ ] Aggiornamento manuale su exchange viene registrato su DB? (position reconciliation worker esiste, va testato)
- [ ] Ottimizzare il worker di esecuzione comandi sul ciclo lifecycle
- [ ] Verificare `docs/debugging/market_entry_qty_deferred.md` prima di implementare
- [ ] Pulizia DB tra sessioni di test
- [ ] Aggiornare CLAUDE.md e README

### Infrastruttura / cleanup

- [ ] Pulizia file legacy non in uso
- [ ] `MOVE_STOP` da Telegram: verificare se il comando gateway è cablato per bybit
- [ ] Test aggiornamento manuale posizione su exchange → reconciliation → DB
