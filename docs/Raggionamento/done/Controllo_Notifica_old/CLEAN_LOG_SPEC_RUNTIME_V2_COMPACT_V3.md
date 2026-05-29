# CLEAN_LOG_SPEC_RUNTIME_V2_COMPACT_V3

## 1. Scopo

`CLEAN_LOG` deve essere una timeline operativa leggibile per seguire una `TradeChain`, non un mirror degli eventi interni del runtime.

Regola principale:

```text
CLEAN_LOG = milestone operative aggregate.
TECH_LOG = diagnostica tecnica.
DB/AUDIT = storico completo e granulare.
```

Questa versione consolida cinque decisioni:

```text
1. Rimuovere `State:` dai messaggi visibili.
2. Usare `UPDATE DONE / PARTIAL / REJECTED` come formato unico per tutti gli update.
3. Aggregare update multipli sulla stessa chain in un unico messaggio composito.
4. Tenere target/entry/ref granulari nel DB, ma compattare il rendering Telegram.
5. Gestire update multi-chain con reply separate per chain, più summary opzionale oltre soglia.
```

---

## 2. Decisioni vincolanti

### 2.1 `State` non va mostrato nel CLEAN_LOG

Nel messaggio Telegram non mostrare:

```text
State: OPEN
State: REVIEW_REQUIRED
State: PARTIALLY_CLOSED
State: CLOSED
```

Motivo: lo stato tecnico è utile al runtime, ma sporca il log operativo.

Lo stato deve restare in:

```text
- ops_trade_chains
- ops_lifecycle_events
- ops_notification_outbox.payload_json
- /chain <id>
- /status
- audit/debug
```

Nel CLEAN_LOG lo stato deve essere implicito nel titolo:

```text
✅ #145 — SIGNAL ACCEPTED
📊 #145 — ENTRY OPENED
📊 #145 — TP1 FILLED
📊 #145 — TP2 FILLED — POSITION CLOSED
❌ #146 — SIGNAL REJECTED
⚠️ #147 — REVIEW REQUIRED
```

### 2.2 Non notificare eventi intermedi

Non mandare messaggi per:

```text
- order placed
- execution command created
- execution command sent
- order ACK generico
- SL/TP attached se tutto è normale
- protective sync normale
- reconciliation OK
- polling exchange OK
- retry tecnico riuscito
- micro-fill sotto soglia
```

Questi dati devono restare nel DB e, se serve, in TECH_LOG.

### 2.3 Aggregare per chain e finestra temporale

Aggregazione consigliata:

```yaml
clean_log:
  debounce_seconds: 20
  aggregate_fills_seconds: 30
  aggregate_updates_seconds: 20
  max_messages_per_chain_per_minute: 4
  min_partial_fill_notify_pct: 10
```

---

## 3. Formato standard senza `State`

Formato base:

```text
<emoji> #<chain_id> — <EVENT>
────────────────
<symbol> — <side>

<body essenziale>

────────────────
Source: <source>
<link se utile>
```

Regole:

```text
- massimo 8-12 righe operative per messaggi normali;
- risultato finale può essere più lungo;
- `Source` resta, perché chiarisce se l'evento viene da trader, exchange, rules, runtime;
- `State` non deve comparire;
- ogni update successivo al setup deve essere reply al root message della chain.
```

Source ammessi:

```text
original_message
trader_update
operation_rules
exchange
runtime
manual_command
reconciliation
timeout_worker
```

Nota importante: `trader_update`, `operation_rules`, `manual_command` e `runtime` possono produrre lo stesso formato `UPDATE DONE`. La sorgente cambia, non cambia la struttura del messaggio.

---

## 4. Messaggio root della chain

### 4.1 SIGNAL_ACCEPTED

Nuovo messaggio nel topic `CLEAN_LOG`.

```text
✅ #145 — SIGNAL ACCEPTED
────────────────
BTC/USDT — 📈 LONG

Entry_1: 65,000 Market
Entry_2: 64,000 Limit
SL: 62,000
TP_1: 68,000
TP_2: 71,000
Risk: 0.5% *

────────────────
Trader: trader_a
Source: original_message
* Risk from operation_rules
────────────────
https://t.me/c/3927267771/206
```

Non aggiungere subito messaggi separati per `ORDER_PLACED`, `SL_ATTACHED`, `TP_ATTACHED`.

### 4.2 SIGNAL_REJECTED

```text
❌ #146 — SIGNAL REJECTED
────────────────
BTC/USDT — 📈 LONG

Entry_1: 65,000 Market
Entry_2: 64,000 Limit
SL: 62,000
TP_1: 68,000

────────────────
Trader: trader_b
Rejected: invalid_risk_profile
Reason: risk exceeds max allowed for trader_b
Source: original_message
────────────────
https://t.me/c/3927267771/206
```

### 4.3 REVIEW_REQUIRED

```text
⚠️ #147 — REVIEW REQUIRED
────────────────
ETH/USDT — 📉 SHORT

Entry_1: 3,420 Limit
SL: 3,520
TP_1: 3,300

────────────────
Trader: trader_a
Reason: ambiguous_entry_zone
Action: no automatic execution
Source: original_message
────────────────
https://t.me/c/3927267771/207
```

---

## 5. Entry aggregate

### 5.1 ENTRY_OPENED

Reply al root.

Questo messaggio sostituisce:

```text
ENTRY_ORDER_PLACED
ENTRY_FILLED market
SL_ATTACHED
TP_ATTACHED
```

Template:

```text
📊 #145 — ENTRY OPENED
────────────────
BTC/USDT — 📈 LONG

Filled:
Entry_1: 65,020 Market
Qty: 0.004 BTC
Fee: 1.42 USDT

Position:
Avg entry: 65,020
Filled: 50%
Pending: Entry_2 64,000 Limit

Protection:
SL: 62,000
TP_1: 68,000
TP_2: 71,000

────────────────
Source: exchange
```

Usare quando la posizione è realmente aperta. Non usare per ordine solo piazzato.

### 5.2 ENTRY_UPDATED

Reply al root.

Usare quando un leg successivo cambia esposizione o prezzo medio.

```text
📊 #145 — ENTRY UPDATED
────────────────
BTC/USDT — 📈 LONG

Filled:
Entry_2: 64,000 Limit
Qty: 0.004 BTC
Fee: 1.35 USDT

Position:
Avg entry: 64,510
Filled: 100%
Pending: none

────────────────
Source: exchange
```

Se il fill è minore della soglia configurata, aggregarlo e non notificare subito.

---

## 6. Update compositi e multi-source

### 6.1 Decisione principale

`UPDATE DONE`, `UPDATE PARTIAL` e `UPDATE REJECTED` sono il formato unico per tutti gli update operativi della chain.

Questo vale per update provenienti da:

```text
- trader_update       → messaggio esterno esplicito del trader
- operation_rules     → regola interna, es. BE dopo TP
- manual_command      → comando umano dal Control Plane
- runtime             → decisione lifecycle interna
- reconciliation      → correzione operativa reale
- timeout_worker      → scadenza/cancellazione automatica
```

Non creare un formato diverso per ogni sorgente. Il corpo resta uguale; cambia solo `Source:`.

### 6.2 Quando unire più azioni nello stesso update

Un singolo messaggio update può contenere più operazioni sulla stessa chain:

```text
- cancella pending entry
- sposta SL a BE
- modifica TP
- chiudi parziale
- rimuove target
```

Nel CLEAN_LOG non creare un messaggio per ogni operazione. Creare un solo messaggio composito quando le azioni hanno:

```text
same chain_id
same source_message_id oppure same engine_batch_id
same processing batch
same debounce window
```

Regola pratica:

```text
1 chain + N modifiche vicine = 1 UPDATE composito
```

### 6.3 UPDATE_DONE composito — trader update

Esempio: cancella ordine pendente + SL a BE.

```text
✅ #145 — UPDATE DONE
────────────────
BTC/USDT — 📈 LONG

Operation:
▪️ Cancel pending entry
▪️ Move SL to BE

Cancelled:
Entry_2: 64,000 Limit

Changed:
SL: 62,000 → 65,020 BE *

────────────────
Source: trader_update
* Changed from original signal
────────────────
https://t.me/c/3927267771/220
```

### 6.4 UPDATE_DONE composito — engine/rules

Stessa struttura, sorgente diversa.

```text
✅ #145 — UPDATE DONE
────────────────
BTC/USDT — 📈 LONG

Operation:
▪️ Cancel remaining pending entry
▪️ Move SL to BE

Cancelled:
Entry_2: 64,000 Limit

Changed:
SL: 62,000 → 65,020 BE *

────────────────
Source: operation_rules
* Changed by rule after TP_1
```

### 6.5 UPDATE_DONE con TP modificati

```text
✅ #145 — UPDATE DONE
────────────────
BTC/USDT — 📈 LONG

Operation:
▪️ Modify take profits
▪️ Move SL

Changed:
TP_1: 68,000 → 67,500 *
TP_2: 71,000 → 70,500 *
SL: 62,000 → 65,000 *

────────────────
Source: trader_update
* Changed from original signal
────────────────
https://t.me/c/3927267771/221
```

### 6.6 UPDATE_PARTIAL

Se nello stesso update alcune operazioni sono applicate e altre respinte, non fare due messaggi per la stessa chain. Usare `UPDATE PARTIAL`.

```text
⚠️ #145 — UPDATE PARTIAL
────────────────
BTC/USDT — 📈 LONG

Applied:
▪️ Cancel pending entry

Rejected:
▪️ Move SL to BE
Reason: entry is not filled yet

Cancelled:
Entry_2: 64,000 Limit

────────────────
Source: trader_update
────────────────
https://t.me/c/3927267771/222
```

### 6.7 UPDATE_REJECTED

Se nessuna operazione è applicabile:

```text
❌ #145 — UPDATE REJECTED
────────────────
BTC/USDT — 📈 LONG

Operation:
▪️ Move SL to BE
▪️ Cancel pending entry

Rejected:
Entry is not filled and no pending order exists.

Reason: no_applicable_target
Source: trader_update
────────────────
https://t.me/c/3927267771/223
```

### 6.8 Priorità dentro update composito

Ordine consigliato delle sezioni:

```text
1. Operation
2. Applied / Rejected, se parziale
3. Cancelled
4. Filled / Closed, se presente
5. Changed
6. Protection
7. Result, se chiude la posizione
8. Source + link
```

### 6.9 Update multi-chain

Se un messaggio update aggiorna più trade/chain, non fare un unico log generico come se fosse una sola operazione.

Regola dominio:

```text
1 source update → N per-chain update results
```

Motivo:

```text
- ogni chain deve mantenere la propria cronologia CLEAN_LOG;
- ogni reply deve stare sotto il root message corretto della chain;
- ogni chain può avere esito diverso: DONE, PARTIAL, REJECTED, SKIPPED;
- il source_message_link resta uguale, ma i risultati operativi sono separati.
```

Policy di rendering:

```text
Se update colpisce 1 chain:
→ 1 reply sotto quella chain

Se update colpisce 2-3 chain:
→ 1 reply per ogni chain

Se update colpisce più di 3 chain:
→ 1 summary aggregato nel CLEAN_LOG
→ reply dettagliate obbligatorie per REJECTED / PARTIAL / rischio operativo
→ reply compatte per DONE opzionali, in base alla policy di rumore
```

### 6.10 Multi-chain — esempio con 2 chain

Messaggio trader:

```text
Move all BTC and ETH stops to BE
```

Reply sotto `#145`:

```text
✅ #145 — UPDATE DONE
────────────────
BTC/USDT — 📈 LONG

Operation:
▪️ Move SL to BE

Changed:
SL: 62,000 → 65,020 BE *

────────────────
Source: trader_update
* Changed from original signal
────────────────
https://t.me/c/3927267771/220
```

Reply sotto `#148`:

```text
✅ #148 — UPDATE DONE
────────────────
ETH/USDT — 📈 LONG

Operation:
▪️ Move SL to BE

Changed:
SL: 3,280 → 3,410 BE *

────────────────
Source: trader_update
* Changed from original signal
────────────────
https://t.me/c/3927267771/220
```

### 6.11 Multi-chain — esempio con esiti diversi

Messaggio trader:

```text
Move all longs to BE
```

Risultato:

```text
#145 BTC LONG → DONE
#148 ETH LONG → DONE
#151 SOL LONG → REJECTED perché entry non fillata
```

Reply sotto `#151`:

```text
❌ #151 — UPDATE REJECTED
────────────────
SOL/USDT — 📈 LONG

Operation:
▪️ Move SL to BE

Rejected:
Cannot move SL to BE because entry is not filled.

────────────────
Source: trader_update
Reason: position_not_open
────────────────
https://t.me/c/3927267771/220
```

### 6.12 Multi-chain — summary oltre soglia

Per update su molte chain, es. `Close all open shorts`, inviare un summary per ridurre rumore.

```text
🔁 UPDATE APPLIED — MULTI CHAIN
────────────────
Operation:
▪️ Close full position

Affected chains:
#160 BTC/USDT SHORT — DONE
#161 ETH/USDT SHORT — DONE
#162 SOL/USDT SHORT — DONE
#163 XRP/USDT SHORT — REJECTED
#164 DOGE/USDT SHORT — DONE

Summary:
Done: 4
Rejected: 1

────────────────
Source: trader_update
────────────────
https://t.me/c/3927267771/250
```

Poi inviare dettagli separati almeno per le chain problematiche:

```text
❌ #163 — UPDATE REJECTED
────────────────
XRP/USDT — 📉 SHORT

Operation:
▪️ Close full position

Rejected:
No open position found on exchange.

────────────────
Source: trader_update
Reason: position_not_found
────────────────
https://t.me/c/3927267771/250
```

### 6.13 Dati minimi per multi-chain update

Nel dominio/payload interno servono:

```text
source_update_id
source_message_link
source_message_id
update_group_id
affected_chain_ids[]
per_chain_result[]:
  chain_id
  symbol
  side
  status: DONE | PARTIAL | REJECTED | SKIPPED
  applied_actions[]
  rejected_actions[]
  reason
```

Il CLEAN_LOG decide quanto renderizzare, ma il dominio deve conservare il dettaglio per chain.

---

## 7. Target multipli

### 7.1 Decisione principale

I target devono restare granulari nel DB e nel payload interno, ma possono essere aggregati nel messaggio Telegram.

Formula:

```text
DB atomico, Telegram compatto.
```

Significato pratico:

```text
Nel DB:
TP_1, TP_2, TP_3 sono entità/ref separate.

Nel CLEAN_LOG:
TP_1 e TP_2 possono apparire nello stesso messaggio se vengono fillati nello stesso batch o entro la finestra di debounce.
```

Non fondere i target nel dominio. Fondere solo il rendering Telegram quando riduce rumore.

### 7.2 Perché i ref target devono restare singoli

Ogni target deve avere ref persistente:

```text
TP_1
TP_2
TP_3
```

Non usare ref generici tipo:

```text
TP
target
take_profit
```

Motivo: servono per collegare correttamente:

```text
- fill exchange;
- update trader;
- PnL per target;
- fee per target;
- funding attribuibile, se disponibile;
- close reason;
- report finale;
- stato target: PENDING / FILLED / CANCELLED / MODIFIED.
```

Esempio DB concettuale:

```text
target_ref = TP_1
price = 68,000
planned_close = 30%
status = FILLED

target_ref = TP_2
price = 71,000
planned_close = 40%
status = FILLED

target_ref = TP_3
price = 75,000
planned_close = 30%
status = PENDING
```

Se nel DB salvi solo `targets_closed = 70%`, perdi la possibilità di capire quale target è stato modificato, fillato, cancellato o confermato dall'exchange.

### 7.3 CLEAN_LOG può aggregare più target

Esempio singolo target:

```text
📊 #145 — TP1 FILLED
────────────────
BTC/USDT — 📈 LONG

TP_1: 68,000
Closed: 30%
PnL: +70.20 USDT
Fee: 1.10 USDT

Remaining:
Position: 70%
SL: 65,020 BE

────────────────
Source: exchange
```

Esempio target multipli nello stesso batch:

```text
📊 #145 — TP1 + TP2 FILLED
────────────────
BTC/USDT — 📈 LONG

Filled targets:
TP_1: 68,000 | Closed: 30% | PnL: +70.20 USDT | Fee: 1.10 USDT
TP_2: 71,000 | Closed: 40% | PnL: +165.40 USDT | Fee: 1.65 USDT

Total:
Closed: 70%
PnL: +235.60 USDT
Fees: 2.75 USDT

Remaining:
Position: 30%
SL: 65,020 BE

────────────────
Source: exchange
```

### 7.4 Regola di grouping target

Aggregare target solo se appartengono alla stessa chain e allo stesso tipo di evento.

```text
group_key = chain_id + TARGET_FILLED + source + debounce_window
```

Esempio:

```text
#145 + TARGET_FILLED + exchange + 10s
→ TP_1 + TP_2 nello stesso messaggio
```

Internamente restano due eventi/ref:

```text
target_ref: TP_1
target_ref: TP_2
```

### 7.5 Target modificati da update composito

Se un update modifica più target, usare `UPDATE DONE`, non messaggi separati per target.

```text
✅ #145 — UPDATE DONE
────────────────
BTC/USDT — 📈 LONG

Operation:
▪️ Remove take profits
▪️ Move SL to BE

Removed:
TP_2: 71,000
TP_3: 75,000

Changed:
SL: 62,000 → 65,020 BE *

Remaining targets:
TP_1: 68,000

────────────────
Source: trader_update
* Changed from original signal
────────────────
https://t.me/c/3927267771/220
```

Nel DB:

```text
TP_2.status = CANCELLED
TP_3.status = CANCELLED
SL.current_price = 65,020
```

### 7.6 Target finale aggregato

Se uno dei target chiude la posizione, il messaggio deve includere il risultato finale.

```text
📊 #145 — TP2 FILLED — POSITION CLOSED
────────────────
BTC/USDT — 📈 LONG

TP_2: 71,000
Closed: 50%
PnL: +231.60 USDT
Fee: 1.95 USDT

────────────────
Final Result:
ROI net: +4.82%
Total PnL net: +344.25 USDT
Gross PnL: +350.00 USDT
Fees: -5.75 USDT
Funding: +0.00 USDT

────────────────
Close reason: TAKE_PROFIT
Source: exchange
```

Se più target chiudono insieme:

```text
📊 #145 — TP2 + TP3 FILLED — POSITION CLOSED
────────────────
BTC/USDT — 📈 LONG

Filled targets:
TP_2: 71,000 | Closed: 30% | PnL: +140.00 USDT | Fee: 1.30 USDT
TP_3: 74,000 | Closed: 20% | PnL: +180.00 USDT | Fee: 1.10 USDT

────────────────
Final Result:
ROI net: +6.10%
Total PnL net: +430.20 USDT
Gross PnL: +438.00 USDT
Fees: -7.80 USDT
Funding: +0.00 USDT

────────────────
Close reason: TAKE_PROFIT
Source: exchange
```

### 7.7 Quando non aggregare target

Non aggregare se:

```text
- i target sono fillati in momenti distanti;
- tra TP_1 e TP_2 avviene un update importante;
- cambia SL/BE manualmente tra i due target;
- uno dei target produce errore/rejected/reconciliation warning;
- l'aggregazione renderebbe meno chiara la sequenza operativa.
```

In questi casi inviare messaggi separati:

```text
📊 #145 — TP1 FILLED
📊 #145 — TP2 FILLED — POSITION CLOSED
```

---

## 8. SL, BE, close finale

### 8.1 BE dentro TP

Se BE è attivato nello stesso ciclo di TP, non inviare `BE ACTIVATED` separato.

```text
📊 #145 — TP1 FILLED
────────────────
BTC/USDT — 📈 LONG

TP_1: 68,000
Closed: 50%
PnL: +118.40 USDT
Fee: 1.80 USDT

Changed:
SL: 62,000 → 65,020 BE *

Remaining:
Position: 50%

────────────────
Source: exchange + operation_rules
* Changed by rule after TP_1
```

Inviare `BE ACTIVATED` separato solo se accade più tardi e non è collegato allo stesso evento TP.

### 8.2 SL_FILLED

```text
🛑 #145 — SL FILLED — POSITION CLOSED
────────────────
BTC/USDT — 📈 LONG

SL: 62,000
Closed: 100%
PnL: -182.00 USDT
Fee: 3.20 USDT

────────────────
Final Result:
ROI net: -2.64%
Total PnL net: -185.20 USDT
Gross PnL: -182.00 USDT
Fees: -3.20 USDT
Funding: +0.00 USDT

────────────────
Close reason: STOP_LOSS
Source: exchange
```

### 8.3 BE_EXIT

```text
📊 #145 — BE EXIT — POSITION CLOSED
────────────────
BTC/USDT — 📈 LONG

Exit: 65,020 BE
Closed: 50%
PnL: +0.00 USDT
Fee: 1.70 USDT

────────────────
Final Result:
ROI net: +1.15%
Total PnL net: +112.30 USDT
Gross PnL: +118.00 USDT
Fees: -5.70 USDT
Funding: +0.00 USDT

────────────────
Close reason: BREAKEVEN_AFTER_TP
Source: exchange
```

---

## 9. Manual close / trader close

### 9.1 CLOSE_REQUESTED

Messaggio opzionale. Usare solo se tra richiesta e fill passa tempo significativo.

```text
✅ #145 — CLOSE REQUESTED
────────────────
BTC/USDT — 📈 LONG

Requested:
Close 100%

Reason:
Trader close command

────────────────
Source: trader_update
────────────────
https://t.me/c/3927267771/240
```

Se il fill arriva subito, saltare questo messaggio e mandare direttamente `POSITION CLOSED`.

### 9.2 POSITION_CLOSED

```text
📊 #145 — POSITION CLOSED
────────────────
BTC/USDT — 📈 LONG

Closed:
Qty: 100%
Price: 66,200
Reason: trader close command

PnL: +74.20 USDT
Fee: 2.40 USDT

────────────────
Final Result:
ROI net: +1.02%
Total PnL net: +71.80 USDT
Gross PnL: +74.20 USDT
Fees: -2.40 USDT
Funding: +0.00 USDT

────────────────
Close reason: MANUAL_CLOSE
Source: exchange
```

---

## 10. Reconciliation e rischio operativo

### 10.1 RECONCILIATION_WARNING

Solo se c'è rischio operativo reale. Non usarlo per differenze tecniche innocue.

```text
⚠️ #145 — RECONCILIATION WARNING
────────────────
BTC/USDT — 📈 LONG

Issue:
Expected SL not found on exchange.

Risk:
Position may be unprotected.

Action:
manual review required

────────────────
Source: reconciliation
```

### 10.2 RECONCILIATION_FIXED

```text
✅ #145 — RECONCILIATION FIXED
────────────────
BTC/USDT — 📈 LONG

Fixed:
Missing SL restored on exchange.

Protection:
SL: 65,020 BE

────────────────
Source: reconciliation
```

---

## 11. Controlli runtime globali

### 11.1 EXECUTION_PAUSED

Nuovo messaggio, non reply.

```text
⏸️ EXECUTION PAUSED
────────────────
Scope: GLOBAL
Mode: BLOCK_NEW_ENTRIES

Effect:
New signals go to review.
Open positions, updates and reconciliation remain active.

────────────────
Source: manual_command
Command: /pause
```

### 11.2 EXECUTION_RESUMED

```text
▶️ EXECUTION RESUMED
────────────────
Scope: GLOBAL
Mode: NONE

Effect:
New valid signals can create TradeChains again.

────────────────
Source: manual_command
Command: /resume
```

Nota: qui `Mode` è accettabile perché descrive il controllo globale, non lo stato della chain.

---

## 12. Mappa eventi interni → CLEAN_LOG visibile

| Evento interno | CLEAN_LOG visibile | Policy |
|---|---|---|
| `SIGNAL_ACCEPTED` | `SIGNAL ACCEPTED` | always |
| `SIGNAL_REJECTED` | `SIGNAL REJECTED` | always |
| `REVIEW_REQUIRED` | `REVIEW REQUIRED` | always |
| `ENTRY_ORDER_PLACED` | nessun messaggio | off |
| `ENTRY_MARKET_FILLED` | `ENTRY OPENED` | aggregate |
| `ENTRY_LIMIT_FILLED` | `ENTRY UPDATED` | aggregate |
| `PARTIAL_FILL_MINOR` | nessun messaggio separato | aggregate/off |
| `SL_ATTACHED` | dentro `ENTRY OPENED` | aggregate |
| `TP_ATTACHED` | dentro `ENTRY OPENED` | aggregate |
| `PENDING_ENTRY_CANCELLED` | dentro `UPDATE DONE` | aggregate |
| `SL_MOVED` | dentro `UPDATE DONE` | aggregate |
| `MOVE_SL_TO_BE` | dentro `UPDATE DONE` oppure dentro `TP FILLED` | aggregate |
| `TP_FILLED_PARTIAL` | `TPn FILLED` oppure `TPn + TPm FILLED` | aggregate |
| `TP_FILLED_FINAL` | `TPn FILLED — POSITION CLOSED` | always |
| `SL_FILLED` | `SL FILLED — POSITION CLOSED` | always |
| `BE_EXIT` | `BE EXIT — POSITION CLOSED` | always |
| `CLOSE_REQUESTED` | `CLOSE REQUESTED` | only if delayed |
| `POSITION_CLOSED` | `POSITION CLOSED` | always |
| `RECONCILIATION_OK` | nessun messaggio | off |
| `RECONCILIATION_WARNING` | `RECONCILIATION WARNING` | only if risk |
| `RECONCILIATION_FIXED` | `RECONCILIATION FIXED` | if previous warning |
| `EXECUTION_COMMAND_SENT` | nessun messaggio | off |
| `EXCHANGE_POLL_OK` | nessun messaggio | off |
| `MULTI_CHAIN_UPDATE` | per-chain `UPDATE DONE/PARTIAL/REJECTED` + summary opzionale | aggregate by chain |

---

## 13. Dati da salvare per supportare aggregazione

### 13.1 Tabella/mapping Clean Log

Servono almeno:

```text
chain_id
clean_log_root_message_id
clean_log_last_message_id
telegram_chat_id
telegram_thread_id
original_message_link
last_clean_log_event_type
last_clean_log_sent_at
```

### 13.2 Campi per update compositi

Per aggregare update multipli:

```text
chain_id
source_message_id
source_message_link
update_group_id
applied_actions[]
rejected_actions[]
affected_entry_refs[]
affected_sl_ref
affected_tp_refs[]
changed_fields[]
reason
source
created_at
```

### 13.3 Campi per update multi-chain

Per update che colpiscono più chain:

```text
source_update_id
source_message_id
source_message_link
update_group_id
affected_chain_ids[]
summary_status
per_chain_result[]
  chain_id
  symbol
  side
  result_status: DONE | PARTIAL | REJECTED | SKIPPED
  applied_actions[]
  rejected_actions[]
  reason
  clean_log_message_id
```

### 13.4 Campi per target multipli

Ogni target deve avere ref persistente:

```text
tp_ref: TP_1 | TP_2 | TP_3
tp_price
tp_close_pct
tp_order_id
tp_fill_price
tp_fill_qty
tp_gross_pnl
tp_fee
tp_funding_delta_if_available
tp_filled_at
```

Il messaggio Telegram può aggregare, ma il DB deve restare normalizzato.

---

## 14. Regole di final result

Il risultato finale va nel messaggio che chiude la posizione:

```text
TP_FINAL_FILLED
SL_FILLED
BE_EXIT
POSITION_CLOSED
```

Non mandare `CHAIN_COMPLETED` separato salvo report manuale richiesto.

Campi consigliati:

```text
ROI net
Total PnL net
Gross PnL
Fees
Funding
Close reason
```

Formula:

```text
Total PnL net = Gross PnL - Fees + Funding
```

Decisione consigliata:

```text
ROI net = Total PnL net / allocated_margin
```

Se `allocated_margin` non è disponibile, non mostrare ROI. Mostrare solo PnL netto.

---

## 15. Esempi di timeline compatta

### 15.1 Trade normale: market + limit + due TP

```text
1. ✅ #145 — SIGNAL ACCEPTED
2. 📊 #145 — ENTRY OPENED
3. 📊 #145 — ENTRY UPDATED
4. 📊 #145 — TP1 FILLED
5. 📊 #145 — TP2 FILLED — POSITION CLOSED
```

### 15.2 Trade con update multiplo

```text
1. ✅ #145 — SIGNAL ACCEPTED
2. 📊 #145 — ENTRY OPENED
3. ✅ #145 — UPDATE DONE
   - Cancel pending entry
   - Move SL to BE
4. 📊 #145 — TP1 FILLED
5. 📊 #145 — BE EXIT — POSITION CLOSED
```

### 15.3 Trade con target multipli nello stesso sync

```text
1. ✅ #145 — SIGNAL ACCEPTED
2. 📊 #145 — ENTRY OPENED
3. 📊 #145 — TP1 + TP2 FILLED
4. 📊 #145 — TP3 FILLED — POSITION CLOSED
```

### 15.4 Trade respinto

```text
1. ❌ #146 — SIGNAL REJECTED
```

### 15.5 Trade problematico

```text
1. ✅ #145 — SIGNAL ACCEPTED
2. 📊 #145 — ENTRY OPENED
3. ⚠️ #145 — RECONCILIATION WARNING
4. ✅ #145 — RECONCILIATION FIXED
5. 📊 #145 — POSITION CLOSED
```

### 15.6 Update multi-chain

```text
1. 🔁 UPDATE APPLIED — MULTI CHAIN          # opzionale, se >3 chain
2. ✅ #160 — UPDATE DONE                    # reply per-chain, se policy lo richiede
3. ❌ #163 — UPDATE REJECTED                # sempre dettaglio per errori/rischi
```

---

## 16. Decisione finale

`CLEAN_LOG` deve restare compatto.

Regola pratica:

```text
Una trade normale non dovrebbe produrre più di 4-6 messaggi.
```

La forma target è:

```text
SETUP
ENTRY
UPDATE composito, solo se cambia il piano operativo
TP/SL/CLOSE
FINAL RESULT dentro il messaggio di chiusura
```

In particolare:

```text
- niente `State:` visibile;
- update multipli sulla stessa chain = un solo messaggio composito;
- update multi-chain = risultati separati per chain, con summary opzionale oltre soglia;
- target multipli = ref singoli nel DB, aggregabili nel messaggio Telegram;
- BE automatico dopo TP = dentro TP_FILLED, non messaggio separato;
- close request = solo se il fill non arriva subito;
- final result = dentro il messaggio che chiude la posizione.
```
