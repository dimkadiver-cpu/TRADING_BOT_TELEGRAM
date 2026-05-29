# TECH_LOG_SPEC — Diagnostica Tecnica Runtime

Versione: 1.0
Integra e completa la sezione 21 di: `PRD_Telegram_Control_Plane_Runtime_V2_UNIFICATO`

---

## 1. Principio Fondamentale

`TECH_LOG` racconta **perché il sistema ha avuto un problema**.
Non racconta cosa è successo al trade (quello è CLEAN_LOG).
Non racconta le azioni dell'operatore (quello è COMMANDS).

```text
TECH_LOG = diagnostica tecnica, anomalie operative, fallimenti silenziosi
```

### 1.1 Cosa va in TECH_LOG

```text
- Errori exchange (API failure, rate limit, order rejected)
- Errori DB (scrittura fallita, lock timeout)
- Fallimenti adapter (connessione persa, websocket disconnesso)
- Worker critici (lifecycle worker fermo, queue overflow)
- Fallimenti silenziosi (entry non piazzata senza exception esplicita)
- Decisioni interne runtime con esito anomalo (in modalità operational_events)
- Riconciliazione con anomalie che non richiedono review operativo
- Attività debug (solo quando /debug_on è attivo)
```

### 1.2 Cosa NON va in TECH_LOG

```text
- Trade outcomes (vanno in CLEAN_LOG)
- Comandi operatore e risposte (vanno in COMMANDS)
- Operazioni normali andate a buon fine
- Poll exchange OK, reconciliation OK
- Execution command sent normalmente
- Log di avvio componenti senza anomalie
```

---

## 2. Tre Livelli di Verbosità

### Livello 1 — Sempre attivo (produzione)

Errori e fallimenti che impattano il runtime operativo.

```text
- Errori exchange: API error, retCode non zero, rate limit
- Errori adapter: ws disconnesso, reconnect fallito, timeout
- Errori DB: scrittura fallita, lock timeout, query error
- Worker critici: lifecycle fermo, execution queue overflow
- Errori notification dispatcher: invio Telegram fallito
```

### Livello 2 — Configurabile (`operational_events: true`)

Anomalie operative che non sono errori espliciti ma cambiano il comportamento.

```text
- Entry non piazzata per motivo non-exception (es. controllo interno bloccato)
- Signal bloccato da LifecycleGateWorker con motivazione dettagliata
- Reconciliation anomalia risolta automaticamente
- Timeout worker: cancellazione pending completata
- Retry riusciti dopo fallimento (con conteggio tentativi)
- Override config DB applicato al boot
```

### Livello 3 — Debug temporaneo (`/debug_on`)

Step interni del runtime, attivo solo quando esplicitamente abilitato.

```text
- Decisioni interne lifecycle step-by-step
- Enrichment: ogni campo calcolato
- Gate: control_mode calcolato, motivo accept/block
- Execution plan: ogni command generato
- Raw exchange events: classificazione per ogni evento
- Stato worker ogni 30 secondi
- DB query significative con timing
```

---

## 3. Configurazione

Nel file `config/telegram_control.yaml`:

```yaml
topics:
  tech_log:
    thread_id: 102
    enabled: true                   # false = disabilita interamente il canale
    min_level: WARNING              # WARNING | INFO (Livello 2) | DEBUG (solo con /debug_on)
    operational_events: false       # Livello 2 on/off
    batch_seconds: 10               # raggruppa messaggi dello stesso tipo in finestra
    max_messages_per_minute: 20     # rate limit totale per minuto
    dedupe_window_seconds: 60       # finestra deduplicazione errori ripetuti
    max_repeated_before_summary: 5  # dopo N ripetizioni invia riepilogo aggregato
    debug_max_duration_minutes: 60  # durata massima /debug_on
```

### 3.1 Effetto di `enabled: false`

Quando `enabled: false`:

```text
- Nessun messaggio inviato su TECH_LOG
- Gli errori vengono comunque loggati su file
- I comandi /debug_on e /debug_off vengono ignorati con risposta in COMMANDS
```

---

## 4. Formato Messaggi

### 4.1 Struttura base

```text
[LIVELLO] <categoria>: <descrizione>

<dettaglio opzionale>

Source: <componente>
```

### 4.2 Formato esteso (per errori con contesto)

```text
[LIVELLO] <categoria>
────────────────
<descrizione chiara>

Context:
<campo>: <valore>
<campo>: <valore>

Action: <cosa ha fatto il sistema>
────────────────
Source: <componente>
```

### 4.3 Prefissi livello

```text
[ERROR]    → errore operativo, impatto immediato
[WARN]     → anomalia, monitorare
[CRITICAL] → errore grave, intervento richiesto
[INFO]     → informativo (solo con operational_events: true)
[DEBUG]    → verbose (solo con /debug_on)
```

---

## 5. Categorie di Errori con Esempi

### 5.1 Exchange Error

**Errore API generico:**

```text
[ERROR] Exchange: API error
────────────────
bybit_adapter: trading_stop failed
retCode: 10001
retMsg: Parameter error

Context:
Symbol: BTCUSDT
Order ID: tsb:2:6:entry:1:...
Chain: #145

Action: retry scheduled (1/3)
────────────────
Source: ccxt_bybit_adapter
```

**Rate limit:**

```text
[WARN] Exchange: Rate limit hit
────────────────
bybit_adapter: too many requests

Context:
Endpoint: /v5/order/create
Retry after: 1000ms

Action: request queued
────────────────
Source: ccxt_bybit_adapter
```

**Order rejected dall'exchange:**

```text
[ERROR] Exchange: Order rejected
────────────────
Order placement rejected by exchange.
retCode: 110007
retMsg: Insufficient margin

Context:
Symbol: BTCUSDT
Side: LONG
Chain: #145
Entry: 65,000 Limit

Action: REVIEW_REQUIRED set on chain
────────────────
Source: ccxt_bybit_adapter
```

### 5.2 Adapter / Connectivity Error

**WebSocket disconnesso:**

```text
[WARN] Adapter: WebSocket disconnected
────────────────
bybit ws_fill_watcher lost connection.
Reconnecting...

Context:
Last event: 45s ago
Attempt: 1/5

Action: reconnect in 5s
────────────────
Source: ws_fill_watcher
```

**Reconnect fallito (dopo tutti i retry):**

```text
[CRITICAL] Adapter: WebSocket reconnect failed
────────────────
bybit ws_fill_watcher failed to reconnect after 5 attempts.
Exchange sync is now OFFLINE.

Context:
Last successful event: 4m 30s ago
Fallback: polling mode activated

Action: manual check recommended
────────────────
Source: ws_fill_watcher
```

### 5.3 Worker Error

**Lifecycle worker fermo:**

```text
[CRITICAL] Worker: LifecycleGateWorker not responding
────────────────
LifecycleGateWorker has not processed events in 120s.

Context:
Last run: 2m 5s ago
Queue depth: 14 pending signals

Action: worker restart attempted
────────────────
Source: runtime_supervisor
```

**Execution queue overflow:**

```text
[WARN] Worker: ExecutionCommandWorker queue overflow
────────────────
Execution command queue exceeds threshold.

Context:
Queue depth: 87 (threshold: 50)
Oldest pending: 45s ago

Action: processing at max rate
────────────────
Source: execution_command_worker
```

### 5.4 DB Error

**Scrittura fallita:**

```text
[ERROR] DB: Write failed
────────────────
Failed to write lifecycle event to ops.sqlite3.

Context:
Table: ops_lifecycle_events
Chain: #145
Error: database is locked

Action: retry in 100ms (1/3)
────────────────
Source: lifecycle_event_repo
```

### 5.5 Notification Dispatcher Error

**Invio Telegram fallito:**

```text
[WARN] Notification: Send failed
────────────────
Failed to send message to CLEAN_LOG topic.

Context:
notification_id: 342
Topic: clean_log (thread_id: 103)
Error: Telegram API timeout

Action: retry scheduled (1/3)
────────────────
Source: notification_dispatcher
```

### 5.6 Anomalie Operative — Livello 2 (`operational_events: true`)

**Signal bloccato da gate:**

```text
[INFO] Lifecycle: Signal blocked by gate
────────────────
Signal #172 blocked at LifecycleGateWorker.

Context:
Chain: #172
Trader: trader_b
Control mode: BLOCK_NEW_ENTRIES (scope: PER_TRADER)
Effective rule: trader_b block set at 14:10:33

Outcome: REVIEW_REQUIRED
────────────────
Source: lifecycle_gate_worker
```

**Entry non piazzata — motivo interno:**

```text
[INFO] Lifecycle: Entry dispatch skipped
────────────────
Entry command for chain #145 not dispatched.

Context:
Chain: #145
Reason: chain state is REVIEW_REQUIRED
Expected state: WAITING_FILL

Action: no retry, manual review needed
────────────────
Source: execution_command_worker
```

**Retry riuscito:**

```text
[INFO] Exchange: Order placed on retry
────────────────
Order placed successfully after 2 failed attempts.

Context:
Chain: #145
Entry: Entry_1
Attempts: 3
Total time: 1.4s

────────────────
Source: ccxt_bybit_adapter
```

---

## 6. Deduplicazione

### 6.1 Problema

Un errore exchange che si ripete ogni secondo produce messaggi inutilizzabili:

```text
[ERROR] Exchange: API error — retCode: 10001  ← 1
[ERROR] Exchange: API error — retCode: 10001  ← 2
[ERROR] Exchange: API error — retCode: 10001  ← 3
... 97 messaggi identici ...
```

### 6.2 Comportamento atteso

```text
1. Primo messaggio: inviato normalmente
2. Ripetizioni nella finestra `dedupe_window_seconds`: soppresse
3. Dopo `max_repeated_before_summary` ripetizioni: invia riepilogo aggregato
```

Riepilogo aggregato:

```text
[ERROR] Exchange: API error (ripetuto)
────────────────
bybit_adapter: retCode 10001 on trading_stop

Repeated 37 times in the last 60 seconds.
First seen: 14:31:10
Last seen: 14:32:10

Action: check exchange status
────────────────
Source: ccxt_bybit_adapter
```

### 6.3 Chiave di deduplicazione

```text
dedupe_key = hash(source + error_type + error_code + symbol)
```

Errori con stesso dedupe_key nella stessa finestra → aggregati.

---

## 7. Rate Limiting

### 7.1 Limite

```yaml
max_messages_per_minute: 20
batch_seconds: 10
```

### 7.2 Batching

Messaggi dello stesso tipo nella stessa finestra `batch_seconds` vengono raggruppati in un unico messaggio Telegram.

Esempio batch:

```text
[WARN] Exchange (3 warnings in 10s)
────────────────
1. bybit_adapter: retCode 10001 — BTCUSDT (14:32:01)
2. bybit_adapter: retCode 10001 — ETHUSDT (14:32:04)
3. bybit_adapter: retCode 10001 — SOLUSDT (14:32:09)

────────────────
Source: ccxt_bybit_adapter
```

### 7.3 Comportamento al rate limit

Se `max_messages_per_minute` viene superato:

```text
- I messaggi eccedenti vengono loggati su file
- Viene inviato un singolo avviso:
```

```text
[WARN] TECH_LOG: Rate limit reached
────────────────
Troppi messaggi nel TECH_LOG (>20/min).
Alcuni messaggi soppresse temporaneamente.

Controlla il log file per il dettaglio completo.
────────────────
Source: notification_dispatcher
```

---

## 8. Debug Mode — `/debug_on`

### 8.1 Attivazione

Quando l'operatore invia `/debug_on [durata]` dal topic COMMANDS, il sistema:

1. Imposta `min_level = DEBUG` per la durata specificata
2. Attiva output step-by-step dei worker
3. Invia conferma in COMMANDS (vedi COMMANDS_SPEC)
4. Annuncia attivazione in TECH_LOG:

```text
[INFO] Debug mode attivato
────────────────
Durata: 10 minuti
Scadenza: 14:42:10
Operatore: 123456789

TECH_LOG mostrerà tutte le decisioni interne.
────────────────
Source: control_plane
```

### 8.2 Esempi messaggi debug

**Enrichment step:**

```text
[DEBUG] Enrichment: signal #172
────────────────
trader_a → signal #172

risk_pct: 2.0
leverage: 5
entry_price: 65,000
sl_price: 62,000
sl_pct: 4.62%
position_size: 0.004 BTC
notional: 260 USDT

Gate: PASS
────────────────
Source: signal_enrichment_processor
```

**Lifecycle gate decision:**

```text
[DEBUG] Gate: signal #172 decision
────────────────
Signal #172 evaluation

control_mode (global): NONE
control_mode (per_trader): NONE
symbol_blacklist: not matched
gate_mode: block

Decision: ACCEPT → TradeChain will be created
────────────────
Source: lifecycle_gate_worker
```

**Execution plan:**

```text
[DEBUG] Execution: plan for chain #172
────────────────
Chain #172 execution plan

Commands generated: 3
  1. PLACE_ENTRY_ORDER (Entry_1: 65,000 Market)
  2. PLACE_SL_ORDER (SL: 62,000)
  3. PLACE_TP_ORDER (TP_1: 68,000)

Dispatch: immediate
────────────────
Source: lifecycle_event_worker
```

**Worker heartbeat:**

```text
[DEBUG] Worker status (every 30s)
────────────────
14:32:10

LifecycleGateWorker:    OK (last: 1s ago, queue: 0)
LifecycleEventWorker:   OK (last: 2s ago, queue: 0)
ExecutionCommandWorker: OK (last: 3s ago, queue: 2)
ExchangeEventSyncWorker: OK (last: 4s ago, queue: 0)
TimeoutWorker:          OK (last: 58s ago)
NotificationDispatcher: OK (last: 5s ago, outbox: 1)
────────────────
Source: runtime_supervisor
```

### 8.3 Scadenza automatica

Alla scadenza il sistema:

1. Ripristina `min_level` al valore configurato in YAML
2. Invia notifica in TECH_LOG:

```text
[INFO] Debug mode scaduto
────────────────
Debug automaticamente disattivato.
Durata effettiva: 10 minuti
Ritorno a min_level: WARNING

────────────────
Source: control_plane
```

### 8.4 `/debug_off` manuale

Stessa notifica di scadenza, con motivo `manual`:

```text
[INFO] Debug mode disattivato
────────────────
Debug disattivato dall'operatore.
Ritorno a min_level: WARNING

────────────────
Source: control_plane
```

---

## 9. Startup e Shutdown Notifications

### 9.1 Avvio runtime

Inviato in TECH_LOG ad ogni avvio:

```text
[INFO] Runtime avviato
────────────────
Modalità: auto
Python: 3.12.x
DB: ops.sqlite3 OK

Workers inizializzati:
- LifecycleGateWorker: OK
- LifecycleEventWorker: OK
- ExecutionCommandWorker: OK
- ExchangeEventSyncWorker: OK
- TimeoutWorker: OK
- NotificationDispatcher: OK
- TelegramControlBot: OK

────────────────
Source: runtime_main
```

### 9.2 Shutdown graceful

Inviato in TECH_LOG prima dello shutdown:

```text
[INFO] Runtime shutdown
────────────────
Motivo: SIGTERM

Stato finale:
Open chains: 5
Pending commands: 2
Active blocks: NONE

Snapshot salvato in DB.
────────────────
Source: runtime_main
```

---

## 10. Tabella outbox — Notification Outbox

Le notifiche TECH_LOG vengono scritte in `ops_notification_outbox` e inviate
dal `TelegramNotificationDispatcher`.

```sql
CREATE TABLE IF NOT EXISTS ops_notification_outbox (
    notification_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    notification_type  TEXT NOT NULL,
    destination        TEXT NOT NULL,       -- TECH_LOG | CLEAN_LOG | COMMANDS_REPLY
    payload_json       TEXT NOT NULL,
    priority           TEXT NOT NULL,       -- HIGH | MEDIUM | LOW
    status             TEXT NOT NULL DEFAULT 'PENDING',
    dedupe_key         TEXT NOT NULL UNIQUE,
    attempts           INTEGER NOT NULL DEFAULT 0,
    last_error         TEXT,
    created_at         TEXT NOT NULL,
    sent_at            TEXT
);
```

---

## 11. Acceptance Criteria

```text
1.  TECH_LOG riceve solo messaggi tecnici, mai eventi dominio trade.
2.  Il canale TECH_LOG può essere disabilitato completamente con enabled: false.
3.  Errori exchange, DB, adapter, worker critici vengono sempre inviati (Livello 1).
4.  Con operational_events: true, le anomalie operative silenziose appaiono in TECH_LOG.
5.  Errori ripetuti vengono deduplicati nella finestra configurata.
6.  Dopo max_repeated_before_summary ripetizioni, viene inviato un riepilogo aggregato.
7.  Il rate limit (max_messages_per_minute) viene rispettato.
8.  Quando il rate limit è superato, viene inviato un singolo avviso.
9.  /debug_on attiva il Livello 3 per la durata specificata.
10. /debug_on rispetta debug_max_duration_minutes.
11. /debug_off disattiva il debug prima della scadenza.
12. Alla scadenza di /debug_on, TECH_LOG torna al min_level configurato.
13. Startup e shutdown inviano notifiche in TECH_LOG.
14. TECH_LOG non contiene trade outcomes, link messaggi trader, PnL.
15. Tutti i messaggi TECH_LOG passano dall'outbox, non da invii diretti.
```
