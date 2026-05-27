# CLEAN_LOG_SPEC_RUNTIME_V2_COMPACT

## 1. Obiettivo

`CLEAN_LOG` non deve essere un log completo di tutti gli eventi runtime. Deve essere una timeline operativa leggibile, compressa e utile per seguire una `TradeChain` senza rumore.

Regola principale:

```text
CLEAN_LOG mostra milestone operative.
TECH_LOG mostra problemi tecnici.
AUDIT/DB conserva tutti gli eventi granulari.
```

Quindi `CLEAN_LOG` non deve notificare ogni ordine creato, ogni comando interno, ogni sync o ogni micro-fill. Questi dati restano in DB, audit, lifecycle events e tech log.

---

## 2. Problema della versione troppo granulare

La versione estesa produce troppi messaggi:

```text
SIGNAL_ACCEPTED
ENTRY_ORDER_PLACED
ENTRY_FILLED market
ENTRY_FILLED limit
SL_PLACED
TP_PLACED
SYNC_PROTECTIVE
COMMAND_SENT
ORDER_ACK
...
```

Per Telegram questo è eccessivo. L'utente deve vedere pochi messaggi chiari:

```text
1. Segnale accettato/rifiutato/review
2. Entry aperta o aggiornata
3. Update trader/rules importante
4. TP/SL/BE/close
5. Risultato finale
```

---

## 3. Principio di compressione

### 3.1 Non notificare eventi tecnici intermedi

Da NON mandare in `CLEAN_LOG`:

```text
- execution_command_created
- execution_command_sent
- order_ack generico
- protective_order_placed se non cambia stato leggibile
- sync_protective se tutto è normale
- polling exchange ok
- reconciliation ok
- state transition interna senza effetto operativo
- retry tecnico
- debug adapter
```

Questi eventi devono restare in DB/TECH_LOG.

### 3.2 Notificare solo eventi che cambiano la lettura operativa

Da mandare in `CLEAN_LOG`:

```text
- segnale ammesso o respinto
- posizione aperta
- posizione aumentata da entry limit fillata
- pending entry cancellata se rilevante
- SL/TP modificati
- TP fillato
- SL fillato
- uscita BE
- chiusura parziale o totale
- review richiesta
- rischio operativo rilevante
- risultato finale
```

### 3.3 Aggregare gli eventi vicini

Se più eventi avvengono nello stesso momento operativo, produrre un solo messaggio.

Esempio:

```text
MARKET entry filled
SL attached
TP orders created
Limit entry still pending
```

Non devono diventare quattro messaggi. Devono diventare:

```text
📊 #145 — ENTRY OPENED
```

---

## 4. Debounce / aggregazione consigliata

Configurazione consigliata:

```yaml
clean_log:
  debounce_seconds: 20
  aggregate_fills_seconds: 30
  max_messages_per_chain_per_minute: 4
  notify_order_placed: false
  notify_protective_sync_ok: false
  notify_execution_command_sent: false
```

Significato:

```text
debounce_seconds:
  aspetta pochi secondi prima di inviare, così può unire eventi molto vicini.

aggregate_fills_seconds:
  se arrivano più fill della stessa fase entro la finestra, li compatta.

notify_order_placed: false:
  non notificare il semplice invio ordine, notificare solo quando cambia lo stato operativo.
```

---

## 5. Famiglie di messaggi CLEAN_LOG

Usare solo queste famiglie.

```text
SETUP
  SIGNAL_ACCEPTED
  SIGNAL_REJECTED
  REVIEW_REQUIRED

ENTRY
  ENTRY_OPENED
  ENTRY_UPDATED
  PENDING_ENTRY_CANCELLED

UPDATE
  UPDATE_DONE
  UPDATE_REJECTED
  SL_UPDATED
  BE_ACTIVATED
  TP_UPDATED

EXIT / REPORT
  TP_FILLED
  SL_FILLED
  BE_EXIT
  POSITION_PARTIALLY_CLOSED
  POSITION_CLOSED
  FINAL_RESULT

RISK / CONTROL
  RECONCILIATION_WARNING
  RECONCILIATION_FIXED
  EXECUTION_PAUSED
  EXECUTION_RESUMED
```

Tutto il resto non entra nel `CLEAN_LOG` salvo casi eccezionali.

---

## 6. Strategia Telegram

### 6.1 Messaggio root

Il primo messaggio della chain crea il root nel topic `CLEAN_LOG`:

```text
SIGNAL_ACCEPTED
SIGNAL_REJECTED
REVIEW_REQUIRED
```

### 6.2 Reply al root

Tutti gli eventi successivi della stessa `chain_id` devono essere reply al root message.

```text
ENTRY_OPENED
ENTRY_UPDATED
UPDATE_DONE
TP_FILLED
SL_FILLED
POSITION_CLOSED
FINAL_RESULT
```

### 6.3 Dati da salvare in DB

Serve salvare almeno:

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

Senza `clean_log_root_message_id`, Telegram diventa una lista piatta ingestibile.

---

## 7. Formato standard compatto

```text
<emoji> #<chain_id> — <EVENT>
────────────────
<symbol> — <side>

<body essenziale>

────────────────
State: <state>
Source: <source>
<link se utile>
```

Regola: massimo 8-12 righe operative, salvo messaggio finale.

---

# 8. Template dei messaggi

## 8.1 SIGNAL_ACCEPTED

Nuovo messaggio root.

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
State: WAITING_ENTRY
Trader: trader_a
Source: original_message
* Risk from operation_rules
────────────────
https://t.me/c/3927267771/206
```

Quando usarlo:

```text
- segnale valido
- operation rules applicate
- chain creata
- pronto per execution
```

Non aggiungere subito un messaggio separato `ENTRY_ORDER_PLACED` se l'ordine viene inviato immediatamente.

---

## 8.2 SIGNAL_REJECTED

Nuovo messaggio root.

```text
❌ #146 — SIGNAL REJECTED
────────────────
BTC/USDT — 📈 LONG

Entry_1: 65,000 Market
Entry_2: 64,000 Limit
SL: 62,000
TP_1: 68,000

────────────────
State: REJECTED
Trader: trader_b
Source: original_message
Rejected: invalid_risk_profile
Reason: risk exceeds max allowed for trader_b
────────────────
https://t.me/c/3927267771/206
```

Quando usarlo:

```text
- segnale letto correttamente
- segnale non ammesso da rules/runtime
```

---

## 8.3 REVIEW_REQUIRED

Nuovo messaggio root, oppure reply se la chain esiste già.

```text
⚠️ #147 — REVIEW REQUIRED
────────────────
ETH/USDT — 📉 SHORT

Entry_1: 3,420 Limit
SL: 3,520
TP_1: 3,300

────────────────
State: REVIEW_REQUIRED
Trader: trader_a
Reason: ambiguous_entry_zone
Action: no automatic execution
────────────────
https://t.me/c/3927267771/207
```

Quando usarlo:

```text
- il sistema non è sicuro
- serve controllo manuale
- nessuna execution automatica
```

---

## 8.4 ENTRY_OPENED

Reply al root.

Questo messaggio sostituisce:

```text
ENTRY_ORDER_PLACED
ENTRY_FILLED market
SL_PLACED
TP_PLACED
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
Pending entry: Entry_2 64,000 Limit

Protection:
SL: 62,000
TP_1: 68,000
TP_2: 71,000

────────────────
State: OPEN
Source: exchange
```

Quando usarlo:

```text
- prima entry fillata
- posizione realmente aperta
```

Non usarlo quando l'ordine è solo piazzato ma non fillato.

---

## 8.5 ENTRY_UPDATED

Reply al root.

Usare quando un altro leg viene fillato dopo la prima apertura.

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
Pending entry: none

────────────────
State: OPEN
Source: exchange
```

Quando usarlo:

```text
- secondo leg fillato
- media prezzo cambiata
- esposizione aumentata
```

Non notificare ogni fill parziale minore. Aggregare se possibile.

---

## 8.6 PENDING_ENTRY_CANCELLED

Reply al root.

```text
✅ #145 — PENDING ENTRY CANCELLED
────────────────
BTC/USDT — 📈 LONG

Cancelled:
Entry_2: 64,000 Limit

Position:
Entry_1 filled
Remaining pending entries: none

────────────────
State: OPEN
Source: trader_update
────────────────
https://t.me/c/3927267771/220
```

Quando usarlo:

```text
- trader cancella pending
- rule cancella pending dopo TP/fill/timeout
- cancellazione cambia il piano operativo
```

Non mandarlo se la cancellazione è solo pulizia tecnica non rilevante per l'utente.

---

## 8.7 UPDATE_DONE — SL_UPDATED

Reply al root.

```text
✅ #145 — SL UPDATED
────────────────
BTC/USDT — 📈 LONG

Changed:
SL: 62,000 → 65,000 *

────────────────
State: OPEN
Source: trader_update
* Changed from original signal
────────────────
https://t.me/c/3927267771/221
```

Quando usarlo:

```text
- SL modificato dal trader
- SL modificato da operation rules
- modifica rilevante per rischio posizione
```

---

## 8.8 BE_ACTIVATED

Reply al root.

```text
✅ #145 — BE ACTIVATED
────────────────
BTC/USDT — 📈 LONG

Trigger:
TP_1 filled

Changed:
SL: 62,000 → 65,020 BE *

Position:
Remaining: 50%

────────────────
State: PROTECTED
Source: operation_rules
* Changed by rule
```

Quando usarlo:

```text
- stop portato a breakeven
- posizione protetta
```

Se BE è parte automatica dello stesso evento TP, può essere unito dentro `TP_FILLED` invece di produrre messaggio separato.

---

## 8.9 TP_FILLED

Reply al root.

```text
📊 #145 — TP1 FILLED
────────────────
BTC/USDT — 📈 LONG

TP_1: 68,000
Closed: 50%
PnL: +118.40 USDT
Fee: 1.80 USDT

Position:
Remaining: 50%
SL: 65,020 BE

────────────────
State: PARTIALLY_CLOSED
Source: exchange
```

Quando usarlo:

```text
- take profit parziale fillato
- posizione ancora aperta
```

Se nello stesso momento viene attivato BE, includerlo qui e non mandare un messaggio BE separato.

---

## 8.10 TP_FINAL_FILLED

Reply al root.

Include risultato finale. Non serve mandare un ulteriore `FINAL_RESULT` separato, salvo se vuoi un report più ricco.

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
State: CLOSED
Close reason: TAKE_PROFIT
Source: exchange
```

Formula:

```text
Total PnL net = Gross PnL - Fees + Funding
ROI net = Total PnL net / allocated_margin_or_position_basis
```

La base ROI deve essere definita nel runtime, altrimenti il numero sarà ambiguo.

---

## 8.11 SL_FILLED

Reply al root.

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
State: CLOSED
Close reason: STOP_LOSS
Source: exchange
```

Quando usarlo:

```text
- stop loss fillato
- posizione chiusa
```

---

## 8.12 BE_EXIT

Reply al root.

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
State: CLOSED
Close reason: BREAKEVEN_AFTER_TP
Source: exchange
```

Quando usarlo:

```text
- posizione chiusa a breakeven dopo TP parziale
- il risultato finale include i TP precedenti
```

---

## 8.13 CLOSE_REQUESTED

Reply al root.

Questo è opzionale. Usarlo solo se tra richiesta e fill passa tempo significativo.

```text
✅ #145 — CLOSE REQUESTED
────────────────
BTC/USDT — 📈 LONG

Requested:
Close 100%

Reason:
Trader close command

────────────────
State: CLOSING
Source: trader_update
────────────────
https://t.me/c/3927267771/240
```

Se il fill arriva subito, saltare questo messaggio e mandare direttamente `POSITION_CLOSED`.

---

## 8.14 POSITION_CLOSED

Reply al root.

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
State: CLOSED
Close reason: MANUAL_CLOSE
Source: exchange
```

Quando usarlo:

```text
- posizione chiusa da comando trader
- posizione chiusa da comando manuale
- posizione chiusa non per TP/SL standard
```

---

## 8.15 UPDATE_REJECTED

Reply al root.

```text
❌ #145 — UPDATE REJECTED
────────────────
BTC/USDT — 📈 LONG

Operation:
Move SL to BE

Rejected:
Entry is not filled yet.

────────────────
State: WAITING_ENTRY
Reason: position_not_open
Source: trader_update
────────────────
https://t.me/c/3927267771/230
```

Quando usarlo:

```text
- update capito ma non applicabile
- update rifiutato da rules
```

---

## 8.16 RECONCILIATION_WARNING

Reply al root.

Solo se c'è rischio operativo reale.

```text
⚠️ #145 — RECONCILIATION WARNING
────────────────
BTC/USDT — 📈 LONG

Issue:
Expected SL not found on exchange.

Risk:
Position may be unprotected.

Action:
REVIEW_REQUIRED

────────────────
State: REVIEW_REQUIRED
Source: reconciliation
```

Il dettaglio tecnico API va in `TECH_LOG`, non qui.

---

## 8.17 RECONCILIATION_FIXED

Reply al root.

```text
✅ #145 — RECONCILIATION FIXED
────────────────
BTC/USDT — 📈 LONG

Fixed:
Missing SL restored on exchange.

Protection:
SL: 65,020 BE

────────────────
State: PROTECTED
Source: reconciliation
```

Quando usarlo:

```text
- problema operativo reale risolto
```

---

## 8.18 EXECUTION_PAUSED

Nuovo messaggio, non reply.

```text
⏸️ EXECUTION PAUSED
────────────────
Scope: GLOBAL
Mode: BLOCK_NEW_ENTRIES

Effect:
New signals go to REVIEW_REQUIRED.
Open positions, updates and reconciliation remain active.

────────────────
Source: manual_command
Command: /pause
```

---

## 8.19 EXECUTION_RESUMED

Nuovo messaggio, non reply.

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

---

# 9. Mappa evento dominio → CLEAN_LOG compatto

| Domain event interno | CLEAN_LOG | Policy |
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
| `PENDING_ENTRY_CANCELLED` | `PENDING ENTRY CANCELLED` | if relevant |
| `SL_MOVED` | `SL UPDATED` | always if changed |
| `BE_ACTIVATED` | dentro `TP_FILLED` o messaggio proprio | aggregate |
| `TP_FILLED_PARTIAL` | `TP1 FILLED` | always |
| `TP_FILLED_FINAL` | `TP2 FILLED — POSITION CLOSED` | always |
| `SL_FILLED` | `SL FILLED — POSITION CLOSED` | always |
| `BE_EXIT` | `BE EXIT — POSITION CLOSED` | always |
| `CLOSE_REQUESTED` | `CLOSE REQUESTED` | only if delayed |
| `POSITION_CLOSED` | `POSITION CLOSED` | always |
| `RECONCILIATION_OK` | nessun messaggio | off |
| `RECONCILIATION_WARNING` | `RECONCILIATION WARNING` | only if risk |
| `RECONCILIATION_FIXED` | `RECONCILIATION FIXED` | if previous warning |
| `EXECUTION_COMMAND_SENT` | nessun messaggio | off |
| `EXCHANGE_POLL_OK` | nessun messaggio | off |

---

# 10. Regole per final result

Il risultato finale va inserito nel messaggio che chiude la posizione:

```text
TP_FINAL_FILLED
SL_FILLED
BE_EXIT
POSITION_CLOSED
```

Non serve sempre un messaggio `CHAIN_COMPLETED` separato.

Campi consigliati:

```text
ROI net
Total PnL net
Gross PnL
Fees
Funding
Close reason
```

Formula minima:

```text
Total PnL net = Gross PnL - Fees + Funding
```

Attenzione: `ROI` deve avere base esplicita. Possibili basi:

```text
- margin allocated
- position notional
- risk amount
```

Decisione consigliata:

```text
ROI net = Total PnL net / allocated_margin
```

Se `allocated_margin` non è disponibile, non mostrare ROI. Mostrare solo PnL netto.

---

# 11. Regole di esclusione

Non mandare CLEAN_LOG per:

```text
- ordine piazzato ma non fillato, salvo segnale in pending da molto tempo
- SL/TP piazzati correttamente dopo entry
- sync normale di protective orders
- polling exchange senza anomalie
- command worker ok
- retry interno riuscito
- duplicate trader report già confermato da exchange
- micro-fill parziali sotto soglia configurata
```

Soglia consigliata:

```yaml
clean_log:
  min_partial_fill_notify_pct: 10
```

Se un fill parziale è sotto il 10% e non chiude/apre una fase importante, aggregarlo.

---

# 12. Regole di unione pratiche

## 12.1 Market entry + protection

Interno:

```text
ENTRY_ORDER_PLACED
ENTRY_FILLED
SL_ATTACHED
TP_ATTACHED
```

Telegram:

```text
📊 #145 — ENTRY OPENED
```

## 12.2 TP + BE nello stesso ciclo

Interno:

```text
TP1_FILLED
BE_ACTIVATED
SL_AMENDED
```

Telegram:

```text
📊 #145 — TP1 FILLED
...
SL: 65,020 BE
```

Non mandare `BE_ACTIVATED` separato.

## 12.3 Close request + immediate fill

Interno:

```text
CLOSE_FULL_REQUESTED
ORDER_SENT
ORDER_FILLED
POSITION_CLOSED
```

Telegram:

```text
📊 #145 — POSITION CLOSED
```

Non mandare `CLOSE REQUESTED` se il fill arriva subito.

## 12.4 Limit fill successivo

Interno:

```text
ENTRY_LIMIT_FILLED
AVG_ENTRY_RECALCULATED
PROTECTIVE_SYNC
```

Telegram:

```text
📊 #145 — ENTRY UPDATED
```

Non mandare `PROTECTION SYNCED` se tutto è normale.

---

# 13. Decisione finale consigliata

`CLEAN_LOG` deve avere pochi messaggi per una trade normale.

Esempio trade normale con market + limit + due TP:

```text
1. ✅ #145 — SIGNAL ACCEPTED
2. 📊 #145 — ENTRY OPENED
3. 📊 #145 — ENTRY UPDATED          # solo se il limit viene fillato dopo
4. 📊 #145 — TP1 FILLED             # include BE se attivato
5. 📊 #145 — TP2 FILLED — POSITION CLOSED
```

Esempio trade con cancel pending:

```text
1. ✅ #145 — SIGNAL ACCEPTED
2. 📊 #145 — ENTRY OPENED
3. ✅ #145 — PENDING ENTRY CANCELLED
4. 📊 #145 — TP1 FILLED
5. 📊 #145 — BE EXIT — POSITION CLOSED
```

Esempio trade respinto:

```text
1. ❌ #146 — SIGNAL REJECTED
```

Esempio trade problematico:

```text
1. ✅ #145 — SIGNAL ACCEPTED
2. 📊 #145 — ENTRY OPENED
3. ⚠️ #145 — RECONCILIATION WARNING
4. ✅ #145 — RECONCILIATION FIXED
5. 📊 #145 — POSITION CLOSED
```

Questa è la soglia giusta: abbastanza informazione per capire il ciclo vita, ma senza trasformare Telegram in un event bus.
