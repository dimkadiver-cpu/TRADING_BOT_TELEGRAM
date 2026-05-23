# Lifecycle Behaviors — Segnali e Update

Documento di riferimento tecnico. Descrive cosa succede step-by-step da quando
arriva un segnale/update fino all'esecuzione sull'exchange, e cosa accade dopo
i fill (sincronizzazione ordini protettivi, aggiornamento stato).

---

## 1. Flusso di ingresso: da Telegram a LifecycleGateWorker

```
TelegramListener → raw_messages
                 → canonical_messages (parser_v2)
                 → enriched_canonical_messages (SignalEnrichmentProcessor)
                       enrichment_decision: PASS | BLOCK | REVIEW
                       lifecycle_processed: 0 (PASS) | 1 (BLOCK/REVIEW/REPORT/INFO)
                 ↓
LifecycleGateWorker.run_once()
  SELECT FROM enriched_canonical_messages
  WHERE lifecycle_processed=0
    AND enrichment_decision='PASS'
    AND primary_class IN ('SIGNAL','UPDATE')
  ORDER BY created_at ASC
  LIMIT 50
```

Il worker gira in loop. Per ogni riga:
1. Deserializza `EnrichedCanonicalMessage` (signal payload, actions, management_plan).
2. Carica le chain aperte del trader con `chain_repo.get_active_by_trader(trader_id)`.
3. **Rehydration** — per ogni chain carica gli `ops_exchange_events` già processati
   (`_rehydrate_chain_from_history`) e ricalcola `entry_avg_price`, `filled_entry_qty`,
   `open_position_qty`, `closed_position_qty` se i valori in DB sono vuoti.
4. Legge `control_mode` effettiva per `(account_id, trader_id, symbol, side)`.
5. Branch su `primary_class`:
   - `SIGNAL` → `process_signal()`
   - `UPDATE` → `process_update()`

---

## 2. Path SIGNAL

### 2.1 Validazione iniziale

```
process_signal(enriched, open_chains, control_mode)
```

Gate sequenziali, ognuno può restituire `REVIEW_REQUIRED`:

| Controllo | Motivo review |
|---|---|
| `control_mode in (BLOCK_NEW_ENTRIES, FULL_STOP)` | `control_mode:new_entries_paused` |
| `signal is None` o manca `symbol` / `side` | `missing_symbol_or_side` |
| `signal.entries` vuoto | `no_entry_legs` |

### 2.2 Risk check

```python
account_snapshot = port.get_account_state(account_id)
market_snapshot  = port.get_symbol_market_state(account_id, symbol)
decision = RiskCapacityEngine.validate(enriched, open_chains, account_snapshot, market_snapshot)
```

`RiskDecision` contiene: `passed`, `reason`, `size_usdt`, `leverage`, `risk_snapshot`.
Se `passed=False` → `REVIEW_REQUIRED` con `reason`.

### 2.3 Determinazione execution_mode

```python
if simple_attached_enabled and sl_price is not None:
    chain_execution_mode = "UNIFIED_PLAN"
else:
    chain_execution_mode = "D_POSITION_TPSL"
```

`UNIFIED_PLAN` è il modo predefinito quando il segnale ha uno stop loss.

### 2.4 Build ExecutionPlan

```python
plan_state_json = ExecutionPlanBuilder.build(eid, entries, take_profits, risk_snapshot)
```

Il piano contiene:
- `legs[]` — una per entry, con `status=PENDING`, `client_order_id` deterministico,
  `qty`, `qty_mode`, `risk_budget`.
- `rebuild_policy`: `ON_EACH_ENTRY_FILL` se TP > 1, altrimenti `NONE`.
- `protection_policy`: sempre `TPSL_ATTACHED_FIRST_LEG`.
- `stop_loss`, `final_tp`, `intermediate_tps[]`.

### 2.5 Timeout

Se `management_plan.cancel_pending_on_timeout=True`:
```python
timeout_at = now + timedelta(hours=management_plan.pending_timeout_hours)
```
Scritto in `chain.entry_timeout_at`. `TimeoutWorker` scade la chain → `EXPIRED` +
`CANCEL_PENDING_ENTRY` + evento `TIMEOUT_REACHED`.

### 2.6 Costruzione comandi entry

**UNIFIED_PLAN** — via `EntryCommandFactory.build_entry_commands()`:

| leg | command_type | Payload chiave |
|---|---|---|
| sequence=1 | `PLACE_ENTRY_WITH_ATTACHED_TPSL` | sl_price, final_tp, leverage, hedge_mode, position_idx |
| sequence>1 | `PLACE_ENTRY` | price, qty/qty_mode, leverage, hedge_mode |

I TP intermedi **non** vengono creati qui; li crea `PostFillProtectionRebuilder` dopo ogni fill.

**D_POSITION_TPSL** — via `_build_d_commands()`:

| Ordine | command_type | Status iniziale |
|---|---|---|
| entry (ogni leg) | `PLACE_ENTRY` | `PENDING` |
| 1 TP | `SET_POSITION_TPSL_FULL` | `WAITING_POSITION` |
| N TP (ognuno) | `SET_POSITION_TPSL_PARTIAL` | `WAITING_POSITION` |

`WAITING_POSITION` = aspetta che la position esista prima di inviare all'exchange.
Vengono rilasciati a `PENDING` al primo `ENTRY_FILLED`.

### 2.7 Persistenza (ops-first atomicity)

In una singola transazione su `ops.sqlite3`:

```
INSERT OR IGNORE INTO ops_trade_chains          → stato WAITING_ENTRY
INSERT OR IGNORE INTO ops_lifecycle_events      → SIGNAL_ACCEPTED, TRADE_CHAIN_CREATED
INSERT OR IGNORE INTO ops_execution_commands    → entry commands (PENDING/WAITING_POSITION)
INSERT INTO ops_account_snapshots               → snapshot rischio
INSERT INTO ops_market_snapshots                → snapshot mercato
```

Poi, su `parser.sqlite3`:
```
UPDATE enriched_canonical_messages SET lifecycle_processed=1 WHERE enrichment_id=?
```

Crash prima di questa seconda write → al retry `INSERT OR IGNORE` è idempotente, nessun duplicato.

---

## 3. Exchange: esecuzione comandi entry

```
ExecutionCommandWorker.run_once()
  → legge ops_execution_commands WHERE status='PENDING'
  → ExecutionGateway.process(cmd)
  → CcxtBybitAdapter / HummingbotApiAdapter
  → piazza ordine (PLACE_ENTRY_WITH_ATTACHED_TPSL o PLACE_ENTRY)
  → aggiorna status: PENDING → SENT → ACK (o FAILED/REVIEW_REQUIRED)
```

`client_order_id` deterministico: `tsb:<chain_id>:<command_id>:<role>:<seq>`.

---

## 4. Fill degli entry: loop exchange → lifecycle

### 4.1 Ricezione fill

Due percorsi paralleli, entrambi scrivono in `ops_exchange_events`:

| Percorso | Meccanismo |
|---|---|
| `ExchangeEventSyncWorker` | polling REST su comandi SENT/ACK → `get_order_status()` |
| `BybitWsFillWatcher` (opzionale) | `ccxt.pro.watch_orders()` real-time |

Evento scritto: `event_type=ENTRY_FILLED`, `processing_status=NEW`.

### 4.2 Processamento fill

`LifecycleEventWorker.run_once()` legge `ops_exchange_events WHERE processing_status='NEW'`.

Prima arricchisce l'evento con `client_order_id` e `command_payload` originale
(lookup su `ops_execution_commands` per `command_id`).

Poi chiama `LifecycleEventProcessor._process_entry_filled()`:

**Calcoli:**

```python
new_filled_qty    = old_filled_qty + fill_qty
new_avg_price     = (old_avg * old_filled + fill_price * fill_qty) / new_filled_qty
new_open_position = old_open + fill_qty
fill_risk         = fill_qty * abs(fill_price - sl_price)
risk_already_realized += fill_risk
risk_remaining    = max(0, risk_total - risk_already_realized)
```

**Transizione stato:**

```
primo fill (WAITING_ENTRY) → OPEN
fill successivi            → stato invariato
```

**Piano aggiornato:**
`_mark_entry_leg_status()` cerca la leg in `plan_state_json` per `client_order_id`
(fallback su `command_payload.sequence`, fallback su unica pending leg).
Imposta `status=FILLED`.

**TP intermedi (rebuild_policy=ON_EACH_ENTRY_FILL):**
`PostFillProtectionRebuilder.build_after_fill()` → per ogni TP intermedio:

```python
tp_qty = round(filled_entry_qty * (100.0 / n_total_tps) / 100.0, 8)
```

Emette `SET_POSITION_TPSL_PARTIAL` con `supersedes_previous=True`.

**Release WAITING_POSITION:**
Se `is_first_fill=True` (WAITING_ENTRY→OPEN):
```sql
UPDATE ops_execution_commands SET status='PENDING' WHERE status='WAITING_POSITION'
```
I comandi `SET_POSITION_TPSL_*` diventano processabili.

**Events emessi:** `ENTRY_FILLED`, `POSITION_SIZE_UPDATED`, `ENTRY_AVG_PRICE_UPDATED`.

---

## 5. Fill TP / SL / Close

### 5.1 TP_FILLED

```
non-final → PARTIALLY_CLOSED
            + SYNC_PROTECTIVE_ORDERS (adegua qty SL/TP agli ordini in libro)
            + se be_trigger=tp{N} e non già protetto → MOVE_STOP_TO_BREAKEVEN command
              → be_protection_status = BE_MOVE_PENDING
final     → CLOSED
```

### 5.2 SL_FILLED

```
→ CLOSED
open_position_qty = 0
```

### 5.3 CLOSE_FULL_FILLED

```
→ CLOSED
open_position_qty = 0
```

### 5.4 CLOSE_PARTIAL_FILLED

```
new_open = open - fill_qty
new_open > 0 → PARTIALLY_CLOSED + SYNC_PROTECTIVE_ORDERS
new_open = 0 → CLOSED
```

### 5.5 STOP_MOVED_CONFIRMED

```
current_stop_price = new_stop_price
se is_breakeven=True → be_protection_status = PROTECTED
event: STOP_MOVE_CONFIRMED
```

### 5.6 PENDING_ENTRY_CANCELLED_CONFIRMED

```
position_already_open = (chain.open_position_qty > 0)

se NON open:
    → lifecycle_state = CANCELLED

se aperto (leg parzialmente eseguita):
    → stato invariato
    se execution_mode NOT IN _ATTACHED_PROTECTION_MODES:
        → SYNC_PROTECTIVE_ORDERS

plan: marca leg come CANCELLED
event: PENDING_ENTRY_CANCELLED
```

---

## 6. Path UPDATE

### 6.1 Risoluzione target

```python
_resolve_targets(enriched, open_chains, tag, tg_id_to_raw_id)
```

Priorità di matching (scope=SINGLE_SIGNAL o UNKNOWN):

| Step | Criterio | Risultato se multipli match |
|---|---|---|
| 1 | `scope` globale (ALL_POSITIONS, ALL_LONG, ALL_SHORT, SYMBOL) | tutte le chain corrispondenti |
| 2 | `tag.targeting.symbols` | unica chain o `None` (ambiguous) |
| 3 | `tag.targeting.explicit_ids` | lista o vuota |
| 4 | `telegram_message_ids` + `reply_to_message_id` → lookup su `raw_messages` | lista (no fallthrough) |
| 5 | fallback: unica chain aperta del trader | `None` se >1 |

`None` → `REVIEW_REQUIRED(ambiguous_update_target)`.  
`[]` → `REVIEW_REQUIRED(no_update_target)`.

### 6.2 Applicazione azioni

Per ogni `chain × action`:

#### CANCEL_PENDING (action_type=CANCEL_PENDING)

Stati ammessi: `WAITING_ENTRY`, `OPEN`, `PARTIALLY_CLOSED`.  
Altri stati → `NOOP_NOT_PENDING`.

**WAITING_ENTRY:**
```
CANCEL_PENDING_ENTRY (espanso per client_order_id attivi)
```

**OPEN / PARTIALLY_CLOSED:**
```
CANCEL_PENDING_ENTRY (espanso)
+ se execution_mode NOT IN _ATTACHED_PROTECTION_MODES:
    SYNC_PROTECTIVE_ORDERS
```

L'espansione (`_expand_cancel_pending_commands`) carica in tempo reale i
`client_order_id` delle entry PENDING/SENT/ACK e crea un comando per ciascuno.

#### SET_STOP con target=ENTRY → Move to Breakeven

```
se già PROTECTED → NOOP_ALREADY_PROTECTED_BE
se command MOVE_STOP_TO_BREAKEVEN già attivo → NOOP_DUPLICATE_COMMAND
altrimenti:
    MOVE_STOP_TO_BREAKEVEN command (target_price=entry_avg_price, be_buffer_pct)
    be_protection_status = BE_MOVE_PENDING
    event: BE_MOVE_REQUESTED
```

Blocking check per `C_SIMPLE_ATTACHED`: se entry non ancora filled → `REVIEW_REQUIRED`.

#### CLOSE FULL

```
se stato in (CLOSED, CANCELLED, EXPIRED) → NOOP_ALREADY_CLOSED
altrimenti:
    CLOSE_FULL command
    event: TELEGRAM_UPDATE_ACCEPTED {action: CLOSE_FULL}
```

#### CLOSE PARTIAL

```
CLOSE_PARTIAL command (fraction=op.fraction, default 0.5)
event: TELEGRAM_UPDATE_ACCEPTED {action: CLOSE_PARTIAL}
```

#### MODIFY_ENTRIES

1. Costruisce piano target (`_build_target_plan_from_modify_entries`): copia `plan_state_json`
   sostituendo le leg modificate.
2. `ExecutionPlanDiffEngine.diff(current, target, risk_remaining, sl_price)`:
   - leg FILLED → `keep_entry_leg`
   - leg invariata → `keep_entry_leg`
   - leg modificata (entry_type o price cambiato) → `cancel_pending_entry` + `replace_entry_leg`
   - leg extra nel current PENDING → `cancel_pending_entry`
3. Per ogni `replace_entry_leg`:
   - `EntryCommandFactory.build_entry_commands()` con la nuova leg e qty ricalcolata da rischio.
4. Event: `TELEGRAM_UPDATE_ACCEPTED {action: MODIFY_ENTRIES}`.

### 6.3 Persistenza update

```
UPDATE ops_trade_chains SET lifecycle_state=?, be_protection_status=?, updated_at=?
INSERT OR IGNORE INTO ops_lifecycle_events
INSERT OR IGNORE INTO ops_execution_commands   ← CANCEL, SYNC, MOVE_STOP, ecc.
```

Poi `lifecycle_processed=1` su `parser.sqlite3`.

---

## 7. SYNC_PROTECTIVE_ORDERS

Emesso in questi casi:
- TP non-finale eseguito (adegua qty SL/TP residui)
- Partial close eseguito (idem)
- Cancel pending entry con posizione aperta (non-attached mode)
- Pending entry cancellato da exchange con posizione già aperta (non-attached mode)

`BybitOrderBuilder` traduce `SYNC_PROTECTIVE_ORDERS` in `amend_sl_qty` verso Bybit.
Non genera un exchange event di risposta → nessuna transizione di stato lifecycle.

---

## 8. Stati lifecycle — macchina a stati

```
                       ┌──────────────────────────────────┐
                       │            REVIEW_REQUIRED       │  (segnale non accettato)
                       └──────────────────────────────────┘
SIGNAL_ACCEPTED
      │
      ▼
WAITING_ENTRY ─── TIMEOUT_REACHED ──► EXPIRED
      │
      │  ENTRY_FILLED (primo)
      ▼
    OPEN ──────────────────────────────► CLOSED  (SL / CLOSE_FULL)
      │                                    ▲
      │  TP_FILLED (non-final)             │  TP_FILLED (final)
      ▼                                    │
PARTIALLY_CLOSED ───────────────────────► CLOSED
      │
      │  CANCEL_PENDING (WAITING_ENTRY)
      ▼
  CANCELLED
```

`be_protection_status` è un campo separato (non interrompe il flusso principale):
```
NOT_PROTECTED → BE_MOVE_PENDING → PROTECTED
```

---

## 9. Worker — orchestrazione temporale

| Worker | DB sorgente | Trigger | Output |
|---|---|---|---|
| `LifecycleGateWorker` | `parser.sqlite3` enriched_canonical_messages | polling | chain + commands |
| `TimeoutWorker` | `ops.sqlite3` trade_chains | polling | EXPIRED + CANCEL command |
| `LifecycleEventWorker` | `ops.sqlite3` exchange_events | polling | state update + commands |
| `ExecutionCommandWorker` | `ops.sqlite3` execution_commands | polling | invia a exchange |
| `ExchangeEventSyncWorker` | `ops.sqlite3` commands SENT/ACK | polling REST | exchange_events |
| `BybitWsFillWatcher` | exchange WS (ccxt.pro) | push real-time | exchange_events |

Il flusso è **asincrono**: ogni worker è indipendente, scrivi → leggi al giro successivo.

---

## 10. Problematiche riscontrate

### P1 — `entry_avg_price=None` in `_apply_move_to_be`

**File:** `entry_gate.py:748`

Se `chain.entry_avg_price` è `None` (chain appena rihydrata o fill non ancora
registrato), il comando `MOVE_STOP_TO_BREAKEVEN` viene inviato con
`target_price=None`. L'adapter riceve un payload nullo e può mandare un
`amend_sl_qty` senza prezzo target, oppure fallire silenziosamente.

`_is_already_be()` controlla `entry_avg_price is None` per restituire `False`,
quindi non blocca il path — il comando parte comunque.

**Rischio:** ordine stop mosso a 0 o rifiutato dall'exchange senza errore visibile.

---

### P2 — `risk_remaining` non ripristinato dalla rehydration

**File:** `entry_gate.py:1051–1137`

`_rehydrate_chain_from_history` ricalcola `filled_entry_qty`, `entry_avg_price`,
`open_position_qty`, `closed_position_qty` dai fill storici, ma **non** ricalcola
`risk_already_realized` né `risk_remaining`. Se i valori in DB sono 0 (ad es.
dopo un riavvio con DB ops parzialmente aggiornato), `_apply_modify_entries`
legge `chain.risk_remaining=0` e usa `risk_total` come fallback, ma potrebbe
sovrastimare il rischio disponibile.

---

### P3 — Multi-leg entry: correlazione fill → leg nel piano

**File:** `event_processor.py:187–229`

`_mark_entry_leg_status` cerca la leg per `client_order_id`. Se il fill arriva
dal WS watcher senza `command_id` nel payload, il fallback a `fallback_first_pending`
funziona solo se c'è **esattamente 1 leg pending**. Con una TWO_STEP o LADDER entry
dove la prima leg è già filled e la seconda è pending — funziona. Ma se arrivano
due fill quasi simultanei (leg 1 e leg 2) prima che il worker processi il primo,
entrambi i fill possono atterrare con 1 sola pending leg ciascuno, e il secondo
può fallire a marcare la leg corretta (nessun match per `client_order_id`,
`command_payload` assente, 0 pending legs → `_mark_entry_leg_status` restituisce
`None`, il piano non viene aggiornato).

---

### P4 — `PostFillProtectionRebuilder`: qty TP ricalcolata cumulativa

**File:** `post_fill_rebuilder.py:35`

```python
tp_qty = round(filled_entry_qty * close_pct / 100.0, 8)
```

`filled_entry_qty` è il **totale cumulativo** al momento del fill. Su un LADDER
a 3 leg con `rebuild_policy=ON_EACH_ENTRY_FILL`, ogni fill emette nuovi
`SET_POSITION_TPSL_PARTIAL` con `supersedes_previous=True` e qty crescente.
L'exchange deve cancellare i precedenti prima di impostare i nuovi.
Se il precedente TP era già parzialmente eseguito prima del supersede, la qty
calcolata può essere inferiore alla size reale della posizione.

---

### P5 — `_expand_cancel_pending_commands`: race condition fill/cancel

**File:** `entry_gate.py:1399–1444`

L'espansione dei client_order_id avviene **dentro la transazione di persist** leggendo
`ops_execution_commands`. Se tra la decisione di cancel (in `_apply_cancel_pending`)
e la persistenza un fill è già arrivato e il comando è passato a `DONE`,
il cancel viene comunque inserito e inviato sull'ordine già eseguito.
L'exchange rifiuta il cancel → il comando finisce in `FAILED` o `REVIEW_REQUIRED`.
Non è un data-corruption, ma produce rumore nell'audit trail.

---

### P6 — Guard `C_SIMPLE_ATTACHED` è legacy code, non copre `UNIFIED_PLAN`

**File:** `entry_gate.py:497–507`

```python
if chain_exec_mode == "C_SIMPLE_ATTACHED":
    entry_pending = any(c.command_type == "PLACE_ENTRY_WITH_ATTACHED_TPSL" ...)
    if entry_pending:
        return REVIEW_REQUIRED("c_mode_update_blocked:entry_pending_not_filled")
```

`C_SIMPLE_ATTACHED` **non viene più assegnato a nuove chain**. Dal passaggio a UNIFIED_PLAN,
tutta la logica di segnale usa:

```python
# entry_gate.py:133–136
if self._simple_attached_enabled is True and sl_price is not None:
    chain_execution_mode = "UNIFIED_PLAN"
else:
    chain_execution_mode = "D_POSITION_TPSL"
```

Il guard rimane nel codice solo per retrocompatibilità con chain già esistenti in DB
create con il vecchio sistema. `UNIFIED_PLAN` usa anch'esso `PLACE_ENTRY_WITH_ATTACHED_TPSL`
ma non ha il guard corrispondente — un update in arrivo mentre l'entry è SENT/ACK procede
senza blocco. Per chain recenti questo è il comportamento attivo.

---

### P7 — Rehydration scrive su DB in lettura

**File:** `entry_gate.py:1110–1137`

`_rehydrate_chain_from_history` scrive direttamente su `ops.sqlite3` con
`UPDATE ops_trade_chains` dentro la stessa connessione usata per la lettura.
Non è nella transazione principale del `_persist_signal`/`_persist_update`.
Se il processo crasha dopo la rehydration write e prima della persist principale,
la chain in DB ha qty aggiornate ma nessun evento/comando associato.
Al retry il worker rileverà la chain già rehydrata e non riscriverà — OK —
ma l'audit trail mancherà dell'evento di aggiornamento.
