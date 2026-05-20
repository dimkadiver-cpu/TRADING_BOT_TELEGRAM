# Runtime V2 — Live Test Plan

Piano di test da eseguire in ambiente live (Bybit Testnet o account reale con size minima) per verificare che l'intero flusso funzioni correttamente, dal segnale Telegram all'ordine sull'exchange.

**Prerequisiti generali:**
- Sistema avviato con `python main.py`
- Bybit testnet configurato (o account live con size 1-5 USDT)
- DB ops.sqlite3 pulito o con stato noto
- Accesso a query DB live (SQLite CLI o DB browser)
- Log streaming attivo (`tail -f logs/runtime.log` o equivalente)

**Query DB di base:**
```sql
-- Stato catene aperte
SELECT trade_chain_id, trader_id, symbol, side, lifecycle_state, lifecycle_state, created_at
FROM ops_trade_chains ORDER BY created_at DESC LIMIT 20;

-- Comandi di esecuzione
SELECT command_id, trade_chain_id, command_type, status, sent_at, result_payload_json
FROM ops_execution_commands ORDER BY created_at DESC LIMIT 30;

-- Eventi lifecycle
SELECT event_id, event_type, previous_state, next_state, created_at
FROM ops_lifecycle_events ORDER BY created_at DESC LIMIT 20;

-- Fill / eventi exchange
SELECT exchange_event_id, event_type, processing_status, payload_json, received_at
FROM ops_exchange_events ORDER BY received_at DESC LIMIT 20;
```

---

## SEZIONE 1 — INGESTION & PARSER

### TEST 1.1 — Segnale MARKET ONE_SHOT parsato correttamente

**Obiettivo:** Verificare che un messaggio Telegram venga letto, parsato e salvato in `canonical_messages`.

**Precondizioni:** Profilo trader configurato nel canale sorgente.

**Steps:**
1. Inviare nel canale Telegram sorgente un messaggio valido per il trader (es. segnale BUY BTCUSDT a mercato).
2. Attendere max 5 secondi.

**Verifica DB:**
```sql
SELECT * FROM raw_messages ORDER BY created_at DESC LIMIT 1;
SELECT * FROM canonical_messages ORDER BY created_at DESC LIMIT 1;
```

**Atteso:**
- `raw_messages`: 1 nuovo record con `processing_status = 'done'`
- `canonical_messages`: 1 record con `primary_class = 'SIGNAL'`, `parse_status = 'PARSED'`, symbol e side corretti

**Log attesi:** `[parser] canonical saved: id=X` o simile

---

### TEST 1.2 — Messaggio non parsabile

**Obiettivo:** Verificare che un messaggio casuale non blocchi il sistema.

**Steps:**
1. Inviare un messaggio generico non strutturato nel canale (es. "ciao come stai").

**Verifica DB:**
```sql
SELECT processing_status, parse_status FROM raw_messages ORDER BY created_at DESC LIMIT 1;
```

**Atteso:**
- `processing_status = 'done'` o `'failed'`
- `canonical_messages` ha un record con `primary_class = 'INFO'` o `parse_status = 'UNCLASSIFIED'`
- Nessuna entry in `enriched_canonical_messages`

---

### TEST 1.3 — Segnale UPDATE parsato

**Obiettivo:** Un messaggio di update (es. chiudi posizione, sposta SL) viene parsato come UPDATE.

**Steps:**
1. Inviare un messaggio di update specifico per il trader (es. "close all" o reply a un segnale precedente con "move SL to BE").

**Verifica DB:**
```sql
SELECT primary_class, parse_status, payload_json FROM canonical_messages ORDER BY created_at DESC LIMIT 1;
```

**Atteso:**
- `primary_class = 'UPDATE'`
- `parse_status = 'PARSED'`
- `payload_json` contiene action corretto (CLOSE_FULL, MOVE_STOP, ecc.)

---

## SEZIONE 2 — ENRICHMENT (POLICY GATE)

### TEST 2.1 — Segnale ammesso (PASS)

**Obiettivo:** Segnale valido supera enrichment e viene marcato PASS.

**Steps:**
1. Inviare segnale per trader con configurazione operations attiva (trader non bloccato, simbolo non in blacklist).

**Verifica DB:**
```sql
SELECT enrichment_id, enrichment_decision, warnings_json
FROM enriched_canonical_messages ORDER BY created_at DESC LIMIT 1;
```

**Atteso:**
- `enrichment_decision = 'PASS'`
- `warnings_json` vuoto o con warning non bloccanti

---

### TEST 2.2 — Simbolo in blacklist (BLOCK)

**Obiettivo:** Segnale su simbolo blacklistato viene bloccato.

**Precondizioni:** Aggiungere temporaneamente il simbolo del segnale alla blacklist in `config/operations/<trader>.yaml`.

**Steps:**
1. Inviare segnale per il simbolo blacklistato.

**Verifica DB:**
```sql
SELECT enrichment_decision, block_reason FROM enriched_canonical_messages ORDER BY created_at DESC LIMIT 1;
```

**Atteso:**
- `enrichment_decision = 'BLOCK'`
- `block_reason` indica simbolo in blacklist
- Nessuna entry in `ops_trade_chains`

---

### TEST 2.3 — Segnale senza SL bloccato (se policy richede SL)

**Obiettivo:** Policy `require_sl = true` blocca segnali senza stop loss.

**Precondizioni:** `require_sl: true` in operations config del trader.

**Steps:**
1. Inviare segnale senza SL esplicito.

**Atteso:**
- `enrichment_decision = 'BLOCK'`
- `block_reason` indica missing SL

---

## SEZIONE 3 — LIFECYCLE ENTRY GATE (RISK & ORDER GENERATION)

### TEST 3.1 — Segnale ONE_SHOT MARKET accettato → chain creata

**Obiettivo:** Segnale ONE_SHOT MARKET supera risk check, viene creata una `TradeChain` + comandi PENDING.

**Steps:**
1. Inviare segnale valido, attendere ciclo worker (max 10 secondi).

**Verifica DB:**
```sql
-- Catena creata
SELECT trade_chain_id, lifecycle_state, entry_mode, planned_entry_qty
FROM ops_trade_chains ORDER BY created_at DESC LIMIT 1;

-- Comandi generati
SELECT command_type, status, payload_json
FROM ops_execution_commands
WHERE trade_chain_id = (SELECT MAX(trade_chain_id) FROM ops_trade_chains);

-- Evento accettazione
SELECT event_type, next_state FROM ops_lifecycle_events ORDER BY created_at DESC LIMIT 3;
```

**Atteso:**
- `lifecycle_state = 'WAITING_ENTRY'`
- Comandi: `PLACE_ENTRY` (status=PENDING) + `PLACE_PROTECTIVE_STOP` (status=WAITING_POSITION) + `PLACE_TAKE_PROFIT` (status=WAITING_POSITION)
- Evento: `SIGNAL_ACCEPTED` + `TRADE_CHAIN_CREATED`

---

### TEST 3.2 — Segnale ONE_SHOT LIMIT accettato

**Obiettivo:** Segnale LIMIT genera comando PLACE_ENTRY con price valorizzato.

**Steps:**
1. Inviare segnale con prezzo limite esplicito.

**Verifica:**
```sql
SELECT payload_json FROM ops_execution_commands
WHERE command_type = 'PLACE_ENTRY' AND trade_chain_id = (SELECT MAX(trade_chain_id) FROM ops_trade_chains);
```

**Atteso:**
- `payload_json.entry_type = 'LIMIT'`
- `payload_json.price` valorizzato con il prezzo del segnale

---

### TEST 3.3 — Segnale LADDER (3+ entries) → comandi multipli

**Obiettivo:** Entry structure LADDER genera un PLACE_ENTRY per ogni leg.

**Precondizioni:** Trader che emette segnali con 3+ entry level.

**Verifica:**
```sql
SELECT COUNT(*), command_type FROM ops_execution_commands
WHERE trade_chain_id = (SELECT MAX(trade_chain_id) FROM ops_trade_chains)
GROUP BY command_type;
```

**Atteso:**
- 3+ righe `PLACE_ENTRY` (una per leg)
- 1 `PLACE_PROTECTIVE_STOP`
- N `PLACE_TAKE_PROFIT`

---

### TEST 3.4 — Duplicato posizione bloccato

**Obiettivo:** Secondo segnale sullo stesso simbolo+side viene rifiutato dal risk check.

**Precondizioni:** Chain in stato OPEN o WAITING_ENTRY per BTCUSDT LONG.

**Steps:**
1. Inviare secondo segnale BTCUSDT LONG.

**Verifica:**
```sql
SELECT event_type, payload_json FROM ops_lifecycle_events ORDER BY created_at DESC LIMIT 3;
```

**Atteso:**
- Evento `REVIEW_REQUIRED` con reason `duplicate_position`
- Nessuna nuova catena creata

---

### TEST 3.5 — max_concurrent_trades raggiunto

**Obiettivo:** Risk check blocca quando si supera il limite concurrent trades.

**Precondizioni:** `max_concurrent_trades: 2` in config, 2 chain già OPEN.

**Steps:**
1. Inviare un terzo segnale.

**Atteso:**
- Evento `REVIEW_REQUIRED` con reason `max_concurrent_trades_exceeded`
- Nessuna nuova catena

---

### TEST 3.6 — Control mode BLOCK_NEW_ENTRIES

**Obiettivo:** Con control mode attivo, nessun nuovo segnale viene processato.

**Precondizioni:** Impostare `execution_pause_mode = 'BLOCK_NEW_ENTRIES'` in `ops_control_state`.

**Steps:**
```sql
INSERT INTO ops_control_state (scope_type, scope_value, execution_pause_mode, active)
VALUES ('GLOBAL', NULL, 'BLOCK_NEW_ENTRIES', 1);
```
1. Inviare segnale.

**Atteso:**
- Evento `REVIEW_REQUIRED` con reason `control_mode_block`

**Cleanup:** Eliminare il record di controllo dopo il test.

---

## SEZIONE 4 — EXECUTION GATEWAY (ORDER PLACEMENT)

### TEST 4.1 — Ordine MARKET inviato all'exchange

**Obiettivo:** Comando PLACE_ENTRY MARKET viene inviato a Bybit e marcato SENT.

**Steps:**
1. Attendere ciclo ExecutionCommandWorker dopo creazione chain (max 10 secondi).

**Verifica DB:**
```sql
SELECT command_id, status, client_order_id, sent_at, result_payload_json
FROM ops_execution_commands
WHERE command_type = 'PLACE_ENTRY' ORDER BY created_at DESC LIMIT 1;
```

**Atteso:**
- `status = 'SENT'` o `'ACK'`
- `client_order_id` valorizzato nel formato `tsb:<chain_id>:<cmd_id>:entry:1`
- `sent_at` valorizzato

**Verifica exchange:** Controllare su Bybit (testnet) che l'ordine sia comparso con l'orderLinkId corrispondente.

---

### TEST 4.2 — Idempotenza ordine già inviato

**Obiettivo:** Se l'ordine esiste già su Bybit (riavvio sistema), non viene duplicato.

**Steps:**
1. Inviare segnale, attendere invio ordine.
2. Resettare `status = 'PENDING'` e `sent_at = NULL` sulla riga del comando nel DB.
3. Attendere il prossimo ciclo worker.

**Atteso:**
- Il worker recupera via `get_order_status(client_order_id)` che l'ordine esiste già
- Marca `status = 'ACK'` senza re-inviare
- Bybit non ha ordini duplicati

---

### TEST 4.3 — Ordine LIMIT inviato con prezzo corretto

**Verifica exchange:** Ordine su Bybit con tipo `limit` e prezzo corrispondente al segnale.

---

### TEST 4.4 — SL e TP inviati dopo fill entry (mode A_SEQUENTIAL)

**Obiettivo:** In mode A_SEQUENTIAL, SL e TP vengono rilasciati solo dopo ENTRY_FILLED.

**Steps:**
1. Inviare segnale, attendere fill entry (su testnet, fillare manualmente se necessario).
2. Attendere ciclo.

**Verifica:**
```sql
SELECT command_type, status FROM ops_execution_commands
WHERE trade_chain_id = X ORDER BY command_id;
```

**Atteso:**
- Prima: PLACE_ENTRY=DONE, PLACE_PROTECTIVE_STOP=WAITING_POSITION, PLACE_TAKE_PROFIT=WAITING_POSITION
- Dopo fill: PLACE_PROTECTIVE_STOP=SENT, PLACE_TAKE_PROFIT=SENT

---

### TEST 4.5 — Retry su errore transiente

**Obiettivo:** Se Bybit restituisce errore temporaneo, il sistema riprova.

**Precondizioni:** Simulare errore disabilitando temporaneamente la rete o usando un account con API key errata.

**Verifica:**
```sql
SELECT retry_count, next_retry_at, status FROM ops_execution_commands ORDER BY created_at DESC LIMIT 1;
```

**Atteso:**
- `retry_count > 0`
- `next_retry_at` valorizzato nel futuro
- `status = 'SENT'` non passa a FAILED al primo errore

---

## SEZIONE 5 — EVENT SYNC (FILL DETECTION)

### TEST 5.1 — Fill entry rilevato (polling)

**Obiettivo:** Il sistema rileva che l'entry order è stato fillato.

**Steps:**
1. Inviare segnale MARKET, attendere fill (ordine a mercato si filla subito).
2. Attendere ciclo ExchangeEventSyncWorker (max 10 secondi).

**Verifica:**
```sql
SELECT event_type, payload_json FROM ops_exchange_events ORDER BY received_at DESC LIMIT 3;
SELECT lifecycle_state, entry_avg_price, filled_entry_qty, open_position_qty
FROM ops_trade_chains WHERE trade_chain_id = X;
```

**Atteso:**
- `ops_exchange_events` contiene `event_type = 'ENTRY_FILLED'` con fill_price e filled_qty
- Chain aggiornata: `lifecycle_state = 'OPEN'`, `entry_avg_price` valorizzato, `open_position_qty > 0`

---

### TEST 5.2 — TP parziale rilevato

**Obiettivo:** Quando un TP (non finale) viene fillato, la chain va in PARTIALLY_CLOSED.

**Steps:**
1. Con posizione OPEN e più TP, far fillare il primo TP su exchange.
2. Attendere ciclo sync.

**Verifica:**
```sql
SELECT lifecycle_state, closed_position_qty, open_position_qty FROM ops_trade_chains WHERE trade_chain_id = X;
SELECT event_type, payload_json FROM ops_exchange_events WHERE trade_chain_id = X ORDER BY received_at DESC LIMIT 3;
```

**Atteso:**
- `event_type = 'TP_FILLED'` con `is_final = false`
- `lifecycle_state = 'PARTIALLY_CLOSED'`
- `closed_position_qty` aumentato
- Comando `SYNC_PROTECTIVE_ORDERS` generato

---

### TEST 5.3 — TP finale → chain CLOSED

**Obiettivo:** Ultimo TP porta la chain a CLOSED.

**Atteso:**
- `event_type = 'TP_FILLED'` con `is_final = true`
- `lifecycle_state = 'CLOSED'`

---

### TEST 5.4 — SL fillato → chain CLOSED

**Obiettivo:** Stop loss triggerato porta la chain a CLOSED.

**Steps:**
1. Far scendere/salire il prezzo fino allo stop (testnet).

**Verifica:**
```sql
SELECT event_type FROM ops_exchange_events WHERE trade_chain_id = X ORDER BY received_at DESC LIMIT 1;
SELECT lifecycle_state FROM ops_trade_chains WHERE trade_chain_id = X;
```

**Atteso:**
- `event_type = 'SL_FILLED'`
- `lifecycle_state = 'CLOSED'`

---

### TEST 5.5 — Breakeven automatico su trigger TP

**Obiettivo:** Se `be_trigger` è configurato al TP1, dopo il fill del TP1 viene emesso un comando MOVE_STOP_TO_BREAKEVEN.

**Precondizioni:** `management_plan.be_trigger: 1` (o equivalente) nella config del trader.

**Verifica dopo fill TP1:**
```sql
SELECT command_type, status, payload_json FROM ops_execution_commands
WHERE trade_chain_id = X AND command_type = 'MOVE_STOP_TO_BREAKEVEN';
```

**Atteso:**
- Comando `MOVE_STOP_TO_BREAKEVEN` generato in PENDING
- Dopo invio: BE protection inviata a Bybit con nuovo trigger price

---

### TEST 5.6 — Fill rilevato via WebSocket (se abilitato)

**Obiettivo:** WebSocket riporta il fill prima del polling.

**Precondizioni:** WS abilitato in `execution.yaml`.

**Verifica:** Controllare nei log il timestamp del fill — deve essere <1 secondo dopo il fill su exchange, non dopo 10 secondi.

---

## SEZIONE 6 — POSITION RECONCILIATION

### TEST 6.1 — Posizione chiusa esternamente rilevata

**Obiettivo:** Se la posizione viene chiusa su Bybit manualmente (o da liquidation), il sistema lo rileva.

**Steps:**
1. Con chain in stato OPEN, chiudere manualmente la posizione su Bybit.
2. Attendere ciclo position reconciliation (max 60 secondi).

**Verifica:**
```sql
SELECT event_type, payload_json FROM ops_exchange_events
WHERE trade_chain_id = X AND event_type = 'CLOSE_FULL_FILLED' ORDER BY received_at DESC LIMIT 1;
SELECT lifecycle_state FROM ops_trade_chains WHERE trade_chain_id = X;
```

**Atteso:**
- `CLOSE_FULL_FILLED` con `source = 'position_reconciliation'`
- `lifecycle_state = 'CLOSED'`

---

## SEZIONE 7 — UPDATE HANDLING (COMANDI TELEGRAM)

### TEST 7.1 — CANCEL_PENDING_ENTRY (cancella entry non ancora fillata)

**Obiettivo:** Update CANCEL_PENDING_ENTRY cancella ordine LIMIT non ancora fillato.

**Precondizioni:** Chain in WAITING_ENTRY con entry LIMIT non fillata.

**Steps:**
1. Inviare messaggio di update di cancellazione (es. "cancel") in reply al segnale.
2. Attendere ciclo worker.

**Verifica:**
```sql
SELECT command_type, status FROM ops_execution_commands WHERE trade_chain_id = X;
SELECT lifecycle_state FROM ops_trade_chains WHERE trade_chain_id = X;
```

**Atteso:**
- Comando `CANCEL_PENDING_ENTRY` generato
- Dopo conferma cancellazione: `lifecycle_state = 'CANCELLED'`
- Evento `PENDING_ENTRY_CANCELLED_CONFIRMED`

---

### TEST 7.2 — CANCEL_PENDING_ENTRY con posizione già aperta

**Obiettivo:** Cancel su entry parzialmente fillata: le entry non fillate vengono cancellate, la posizione rimane aperta.

**Precondizioni:** Chain LADDER, primo entry fillato, altri pending.

**Atteso dopo cancel:**
- Ordini entry restanti cancellati su Bybit
- Chain non passa a CANCELLED ma rimane OPEN
- Comando `SYNC_PROTECTIVE_ORDERS` per aggiustare qty SL

---

### TEST 7.3 — CLOSE_FULL (chiusura immediata)

**Obiettivo:** Update CLOSE_FULL genera ordine market di chiusura per la posizione.

**Precondizioni:** Chain in stato OPEN.

**Steps:**
1. Inviare messaggio "close all" o equivalente.

**Verifica:**
```sql
SELECT command_type, payload_json, status FROM ops_execution_commands
WHERE trade_chain_id = X AND command_type = 'CLOSE_FULL';
```

**Atteso:**
- Comando `CLOSE_FULL` generato e inviato
- Ordine market al contrario su Bybit (SELL se era LONG)
- Dopo fill: `lifecycle_state = 'CLOSED'`

---

### TEST 7.4 — MOVE_STOP (SL spostato a nuovo livello)

**Obiettivo:** Update SET_STOP genera comando MOVE_STOP_TO_BREAKEVEN (o a livello specificato).

**Precondizioni:** Chain OPEN con SL attivo.

**Steps:**
1. Inviare messaggio di update per spostare SL (es. "move sl to be" o prezzo specifico).

**Verifica:**
```sql
SELECT command_type, payload_json FROM ops_execution_commands
WHERE trade_chain_id = X AND command_type = 'MOVE_STOP_TO_BREAKEVEN';
```

**Atteso:**
- Comando generato con nuovo `trigger_price`
- Dopo conferma: `current_stop_price` aggiornato nella chain

---

### TEST 7.5 — Target resolution ambiguo → REVIEW_REQUIRED

**Obiettivo:** Update senza target chiaro (es. "close" senza specificare simbolo con più posizioni aperte) finisce in REVIEW_REQUIRED.

**Precondizioni:** 2+ chain OPEN per lo stesso trader su simboli diversi.

**Steps:**
1. Inviare update generico senza target esplicito.

**Atteso:**
- Evento `REVIEW_REQUIRED` con reason `ambiguous_target`
- Nessun comando di chiusura generato

---

## SEZIONE 8 — ENTRY TIMEOUT

### TEST 8.1 — Chain scaduta → EXPIRED

**Obiettivo:** Chain WAITING_ENTRY con timeout superato viene marcata EXPIRED e i pending entries cancellati.

**Precondizioni:** `entry_timeout_minutes` configurato in management_plan (es. 5 minuti). Chain WAITING_ENTRY con entry LIMIT non fillata.

**Steps:**
1. Inviare segnale LIMIT lontano dal mercato.
2. Attendere scadenza timeout (o impostare `entry_timeout_at` nel passato via SQL).

**Verifica:**
```sql
SELECT lifecycle_state FROM ops_trade_chains WHERE trade_chain_id = X;
SELECT command_type, status FROM ops_execution_commands WHERE trade_chain_id = X;
SELECT event_type FROM ops_lifecycle_events WHERE trade_chain_id = X ORDER BY created_at DESC LIMIT 3;
```

**Atteso:**
- Evento `ENTRY_TIMEOUT_EXPIRED`
- Comando `CANCEL_PENDING_ENTRY` generato
- `lifecycle_state = 'EXPIRED'` dopo conferma cancellazione

---

## SEZIONE 9 — EXECUTION MODE C (NATIVE SL/TP)

### TEST 9.1 — Entry MARKET con SL/TP nativi Bybit

**Obiettivo:** In mode C, l'ordine entry include i parametri `stopLoss`, `takeProfit` Bybit nativi.

**Precondizioni:** `execution_mode: C` configurato per il trader.

**Verifica:**
```sql
SELECT payload_json FROM ops_execution_commands WHERE command_type = 'PLACE_ENTRY' AND trade_chain_id = X;
```

**Atteso:**
- `payload_json` contiene `bybit_native_sl`, `bybit_native_tp`, `tpslMode: 'Partial'`
- Su Bybit: ordine con SL/TP allegati
- Nessun comando separato `PLACE_PROTECTIVE_STOP` generato

---

## SEZIONE 10 — SYNC PROTECTIVE ORDERS

### TEST 10.1 — SL qty aggiornato dopo TP parziale

**Obiettivo:** Dopo fill parziale di TP, il SL viene aggiornato alla qty residua della posizione.

**Steps:**
1. Fill TP1 (non finale).
2. Attendere ciclo.

**Verifica Bybit:** Stop order SL qty = `open_position_qty` aggiornato.

**Verifica DB:**
```sql
SELECT command_type, payload_json FROM ops_execution_commands
WHERE trade_chain_id = X AND command_type = 'SYNC_PROTECTIVE_ORDERS' ORDER BY created_at DESC LIMIT 1;
```

---

## CHECKLIST FINALE PRE-PRODUZIONE

- [ ] TEST 1.1 — Segnale parsato correttamente
- [ ] TEST 1.2 — Messaggio non strutturato non blocca il sistema
- [ ] TEST 1.3 — Update parsato correttamente
- [ ] TEST 2.1 — Segnale ammesso PASS
- [ ] TEST 2.2 — Simbolo blacklistato bloccato
- [ ] TEST 3.1 — Chain creata con comandi PENDING
- [ ] TEST 3.3 — Segnale LADDER genera N entry commands
- [ ] TEST 3.4 — Duplicato posizione bloccato
- [ ] TEST 3.6 — Control mode blocca nuovi segnali
- [ ] TEST 4.1 — Ordine MARKET inviato a Bybit
- [ ] TEST 4.2 — Idempotenza: no ordini duplicati
- [ ] TEST 4.4 — SL/TP rilasciati dopo fill entry (mode A)
- [ ] TEST 5.1 — Fill entry rilevato
- [ ] TEST 5.2 — TP parziale → PARTIALLY_CLOSED
- [ ] TEST 5.3 — TP finale → CLOSED
- [ ] TEST 5.4 — SL fillato → CLOSED
- [ ] TEST 5.5 — BE automatico su trigger TP
- [ ] TEST 6.1 — Chiusura esterna rilevata da reconciliation
- [ ] TEST 7.1 — CANCEL_PENDING_ENTRY funziona
- [ ] TEST 7.3 — CLOSE_FULL funziona
- [ ] TEST 7.4 — MOVE_STOP funziona
- [ ] TEST 7.5 — Ambiguità target → REVIEW_REQUIRED
- [ ] TEST 8.1 — Timeout chain → EXPIRED + cancel
- [ ] TEST 10.1 — SYNC_PROTECTIVE_ORDERS dopo TP parziale

---

## NOTE OPERATIVE

**Resettare DB tra test:**
```sql
-- Usare con cautela — cancella tutte le chain
DELETE FROM ops_execution_commands;
DELETE FROM ops_lifecycle_events;
DELETE FROM ops_exchange_events;
DELETE FROM ops_trade_chains;
DELETE FROM ops_account_snapshots;
DELETE FROM ops_market_snapshots;
```

**Verificare loop running:**
```
Log atteso ogni 10 secondi per ogni worker:
  [lifecycle_gate_worker] run_once done
  [execution_gateway] run_once done
  [event_sync] run_once done
  [lifecycle_event_worker] run_once done
```

**Simboli consigliati per testnet:** BTCUSDT, ETHUSDT (liquidità alta, fills veloci su testnet).

**Size minima su live:** 5-10 USDT per minimizzare l'esposizione durante i test.
