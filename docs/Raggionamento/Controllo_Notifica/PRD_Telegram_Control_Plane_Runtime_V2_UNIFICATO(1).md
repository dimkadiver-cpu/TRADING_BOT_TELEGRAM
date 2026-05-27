# PRD Unificato — Telegram Control Plane per Runtime V2 con Topic Separati

## 0. Scopo del documento

Questo PRD unifica due proposte precedenti:

1. **Telegram Control Plane per `runtime_v2`**
2. **Telegram Control Bot con topic separati `COMMANDS`, `TECH_LOG`, `CLEAN_LOG`**

La versione unificata definisce una sola architettura coerente:

```text
Telegram private forum group
├── COMMANDS   → comandi manuali controllati
├── TECH_LOG   → log tecnici / errori / warning
└── CLEAN_LOG  → eventi operativi puliti e aggregati
```

Il modulo deve essere integrato in `runtime_v2`, non nel vecchio layer Telegram di intake.

---

# 1. Obiettivo

Introdurre in `TRADING_BOT_TELEGRAM` un **Telegram Control Plane** dedicato al controllo operativo del runtime V2.

Il modulo dovrà permettere di:

- leggere lo stato operativo del bot;
- controllare l’ammissione di nuove operazioni;
- ricevere notifiche tecniche e operative in topic separati;
- vedere eventi puliti del ciclo vita trade;
- predisporre in futuro comandi manuali sulle `TradeChain`;
- mantenere audit, idempotenza e sicurezza.

Il controllo Telegram **non deve sostituire** il listener Telegram già esistente che legge i segnali dai canali trader.

---

# 2. Decisione architetturale principale

## 2.1 Separare due ruoli Telegram

### A. Telegram Intake Listener — esistente

Responsabilità:

```text
- ascoltare canali/topic dei trader
- acquisire messaggi
- salvare raw_messages
- passare al parser_v2
- alimentare enrichment/lifecycle
```

Tecnologia attuale:

```text
Telethon
```

Questo layer resta separato.

### B. Telegram Control Bot — nuovo

Responsabilità:

```text
- ricevere comandi manuali dall’operatore
- mostrare stato runtime
- attivare/disattivare controlli operativi
- inviare log tecnici
- inviare eventi puliti del ciclo vita trade
```

Tecnologia consigliata:

```text
python-telegram-bot
```

Motivo: è più adatto per bot command-based, keyboard, callback e controllo autorizzazioni. Il listener Telethon rimane dedicato all’intake dei segnali.

---

# 3. Struttura Telegram proposta

Usare un **supergruppo privato Telegram con Topics abilitati**.

Non usare un canale come base principale.

Struttura:

```text
TeleSignalBot Control
├── COMMANDS
├── TECH_LOG
└── CLEAN_LOG
```

## 3.1 `COMMANDS`

Topic dedicato ai comandi manuali.

Esempi:

```text
/status
/health
/control
/pause
/resume
/reviews
/chains
/chain 145
/commands
/logs 50
```

Regole:

```text
- accettare comandi solo dal thread_id configurato;
- accettare comandi solo da user_id autorizzati;
- ignorare messaggi fuori dal topic COMMANDS;
- ogni comando deve essere scritto in audit DB;
- ogni comando deve produrre esito accepted/rejected/executed/failed.
```

## 3.2 `TECH_LOG`

Topic dedicato a log tecnici, warning, errori e problemi interni.

Esempi:

```text
[ERROR] bybit_adapter: trading_stop failed
[WARN] reconciliation mismatch: local order not found on exchange
[ERROR] ExecutionCommandWorker failed to dispatch command_id=123
```

Regole:

```text
- default: inviare solo WARNING, ERROR, CRITICAL;
- DEBUG solo se abilitato temporaneamente;
- batching obbligatorio;
- deduplicazione errori ripetuti;
- rate limit;
- nessun messaggio operativo pulito;
- nessun evento lifecycle formattato come stato trade.
```

## 3.3 `CLEAN_LOG`

Topic dedicato a eventi leggibili e aggregati del ciclo vita trade.

Esempio:

```text
BTCUSDT SHORT
Signal accepted
Entry placed: 0.6432
SL: 0.6550
TP1: 0.6300
Status: waiting fill
```

Altro esempio:

```text
BTCUSDT SHORT
TP1 hit
Closed: 50%
SL moved to BE
Remaining position: 50%
```

Regole:

```text
- deve essere basato su domain events, non sui log Python;
- deve essere aggregato per signal_id / chain_id / symbol / side;
- deve essere sintetico;
- niente traceback;
- niente dettagli interni inutili;
- niente messaggi raw del logger tecnico.
```

Principio chiave:

```text
TECH_LOG racconta perché il sistema ha avuto un problema.
CLEAN_LOG racconta cosa è successo al trade.
COMMANDS serve per controllare il sistema.
```

---

# 4. Problema da risolvere

Senza separazione, il sistema rischia di mischiare:

```text
- comandi manuali;
- errori tecnici;
- notifiche operative;
- debug interno;
- stato del ciclo vita trade;
- eventi exchange;
- review richieste;
- log rumorosi.
```

Conseguenze:

```text
- rumore operativo;
- debug più difficile;
- rischio di comandi non controllati;
- impossibilità di audit affidabile;
- notifiche Telegram ingestibili;
- accoppiamento improprio tra logger e dominio.
```

Il control plane deve quindi separare nettamente:

```text
input umano       → COMMANDS
diagnostica       → TECH_LOG
stato operativo   → CLEAN_LOG
```

---

# 5. Stato attuale runtime V2

Nel runtime V2 esistono già i componenti fondamentali:

```text
parser_v2
SignalEnrichmentProcessor
LifecycleGateWorker
TimeoutWorker
LifecycleEventWorker
ExecutionCommandWorker
ExchangeEventSyncWorker
ops.sqlite3
ops_control_state
```

## 5.1 Control state già previsto

Nel dominio lifecycle esiste già il concetto di controllo operativo:

```python
ControlMode = Literal["NONE", "BLOCK_NEW_ENTRIES", "FULL_STOP"]
```

Modello concettuale:

```python
class ControlState(BaseModel):
    scope_type: str
    scope_value: str | None = None
    execution_pause_mode: ControlMode = "NONE"
    emergency_action: str | None = None
    reason: str | None = None
    created_by: str | None = None
    active: bool = True
```

Il repository espone già una logica simile a:

```python
get_effective_mode(account_id, trader_id, symbol, side)
```

## 5.2 Comportamento attuale atteso

`LifecycleGateWorker` calcola il `control_mode` e lo passa a:

```python
LifecycleEntryGate.process_signal(...)
```

Se il controllo effettivo è:

```text
BLOCK_NEW_ENTRIES
FULL_STOP
```

un nuovo `SIGNAL` non deve generare `TradeChain` e deve finire in `REVIEW_REQUIRED`.

## 5.3 Gap attuale

Mancano:

```text
- bot Telegram di controllo;
- RuntimeControlService;
- estensioni write-side del ControlStateRepository;
- audit comandi Telegram;
- routing topic COMMANDS / TECH_LOG / CLEAN_LOG;
- notification outbox;
- dispatcher Telegram;
- formattatori per messaggi puliti;
- semantica completa e sicura di FULL_STOP.
```

---

# 6. Scope MVP

## 6.1 Incluso

MVP deve includere:

```text
- config Telegram control;
- bot Telegram;
- autorizzazione per chat_id, thread_id, user_id;
- topic COMMANDS, TECH_LOG, CLEAN_LOG;
- RuntimeControlService;
- /help;
- /status;
- /health;
- /control;
- /pause;
- /resume;
- /reviews;
- audit comandi;
- log tecnici con rate limit;
- notifiche CLEAN_LOG da domain events principali;
- test unitari e integration test minimi.
```

## 6.2 Escluso dal MVP

Non includere nel MVP:

```text
- dashboard grafica;
- conversazioni multi-step complesse;
- modifica avanzata dei segnali;
- comandi manuali diretti sull’exchange;
- FULL_STOP operativo;
- /halt;
- /panicclose;
- sistema multi-approvazione;
- auto-creazione obbligatoria dei topic;
- gestione ruoli sofisticata.
```

---

# 7. Architettura target

```text
Telegram private forum group
        ↓
COMMANDS topic
        ↓
TelegramControlBot
        ↓
TelegramAuthLayer
        ↓
RuntimeControlService
        ↓
┌───────────────────────────────────────────────┐
│ Read side                                     │
│ - runtime status                              │
│ - trade chains attive                         │
│ - execution commands                          │
│ - controlli attivi                            │
│ - review/errori recenti                       │
│ - lifecycle events recenti                    │
└───────────────────────────────────────────────┘
        ↓
┌───────────────────────────────────────────────┐
│ Write side                                    │
│ - pause nuove entry                           │
│ - resume nuove entry                          │
│ - audit comando                               │
│ - future lifecycle manual commands            │
└───────────────────────────────────────────────┘
        ↓
ops.sqlite3
```

Notifiche:

```text
Lifecycle / Execution / Reconciliation worker
        ↓
ops_notification_outbox
        ↓
TelegramNotificationDispatcher
        ↓
TECH_LOG oppure CLEAN_LOG
```

---

# 8. Nuovo package proposto

Collocazione consigliata:

```text
src/runtime_v2/control_plane/
├── __init__.py
├── config.py
├── auth.py
├── telegram_bot.py
├── service.py
├── status_queries.py
├── topic_router.py
├── notification_dispatcher.py
├── audit_store.py
├── models.py
└── formatters/
    ├── command_formatter.py
    ├── status_formatter.py
    ├── chain_formatter.py
    ├── review_formatter.py
    ├── tech_log_formatter.py
    └── clean_event_formatter.py
```

## 8.1 Perché non `src/telegram_control/`

Il modulo non è solo Telegram. È un **control plane del runtime**.

Deve leggere e scrivere:

```text
ops.sqlite3
ops_control_state
ops_trade_chains
ops_execution_commands
ops_lifecycle_events
ops_exchange_events
ops_notification_outbox
```

Quindi la collocazione più coerente è:

```text
src/runtime_v2/control_plane/
```

Non:

```text
src/telegram_control/
```

---

# 9. Responsabilità dei componenti

## 9.1 `config.py`

Carica e valida:

```text
config/telegram_control.yaml
```

Responsabilità:

```text
- token env;
- chat_id;
- topic ids;
- utenti autorizzati;
- keyboard;
- policy notifiche;
- rate limit;
- batching;
- livelli TECH_LOG.
```

## 9.2 `auth.py`

Valida ogni update Telegram.

Controlli obbligatori:

```text
chat_id == configured_control_chat_id
message_thread_id == topics.commands.thread_id
from_user_id in authorized_users
```

Per messaggi non autorizzati:

```text
- non eseguire il comando;
- non rivelare dettagli;
- scrivere audit/log tecnico;
- opzionalmente rispondere con messaggio generico.
```

## 9.3 `telegram_bot.py`

Responsabilità:

```text
- inizializzare python-telegram-bot;
- registrare handler comandi;
- delegare al RuntimeControlService;
- inviare risposte nel topic COMMANDS;
- gestire shutdown ordinato.
```

Non deve contenere logica trading.

## 9.4 `service.py`

API applicativa del control plane.

Esempi:

```python
get_runtime_status()
get_health()
get_active_chains()
get_chain_detail(chain_id)
get_execution_commands(limit)
get_recent_reviews(limit)
get_active_controls()
pause_new_entries(...)
resume_new_entries(...)
```

Regola: il service può parlare con repository e query layer, ma non con exchange adapter diretto.

## 9.5 `status_queries.py`

Query read-only verso `ops.sqlite3`.

Metodi suggeriti:

```python
get_active_chain_summary()
get_chain_by_id(chain_id)
get_recent_reviews(limit)
get_execution_command_summary()
get_recent_execution_commands(limit)
get_recent_lifecycle_events(limit)
get_unprocessed_exchange_event_count()
get_control_summary()
```

## 9.6 `topic_router.py`

Centralizza la scelta del topic di destinazione.

Esempio:

```text
COMMANDS reply → topics.commands.thread_id
technical log  → topics.tech_log.thread_id
domain event   → topics.clean_log.thread_id
```

Motivo: evitare `message_thread_id` sparsi nel codice.

## 9.7 `notification_dispatcher.py`

Consuma `ops_notification_outbox`.

Responsabilità:

```text
- leggere notifiche PENDING;
- applicare policy on/silent/off;
- scegliere topic TECH_LOG o CLEAN_LOG;
- formattare messaggio;
- inviare a Telegram;
- marcare SENT;
- gestire retry;
- gestire dedupe;
- applicare rate limit.
```

## 9.8 `audit_store.py`

Scrive audit dei comandi manuali.

Responsabilità:

```text
- comando ricevuto;
- comando rifiutato;
- comando accettato;
- comando eseguito;
- comando fallito;
- motivazione;
- risultato;
- idempotency key.
```

---

# 10. Configurazione proposta

File:

```text
config/telegram_control.yaml
```

Configurazione consigliata:

```yaml
enabled: true

token_env: CONTROL_TELEGRAM_BOT_TOKEN
chat_id: "-1001234567890"

topics:
  commands:
    thread_id: 101

  tech_log:
    thread_id: 102
    min_level: WARNING
    batch_seconds: 10
    max_messages_per_minute: 20
    dedupe_window_seconds: 60

  clean_log:
    thread_id: 103
    aggregate_by:
      - chain_id
      - signal_id
      - symbol
      - side
    debounce_seconds: 5

authorized_users:
  - "123456789"

keyboard:
  - ["/status", "/health", "/control"]
  - ["/pause", "/resume", "/reviews"]
  - ["/chains", "/commands", "/logs"]

commands:
  aliases:
    pause:
      - "/pause"
      - "/pause_execution"
    resume:
      - "/resume"
      - "/resume_execution"

notifications:
  startup: "on"
  shutdown: "on"
  control_change: "on"
  review_required: "on"
  entry_order_placed: "silent"
  entry_filled: "on"
  tp_filled: "on"
  sl_filled: "on"
  close_full_filled: "on"
  close_partial_filled: "on"
  order_rejected: "on"
  reconciliation_warning: "on"
  technical_error: "on"
```

Valori possibili per notifiche:

```text
on      → invia messaggio normale
silent  → invia senza notifica sonora, se supportato
off     → non inviare
```

---

# 11. Autorizzazione e sicurezza

Ogni comando deve essere eseguito solo se:

```text
1. chat_id corrisponde al gruppo di controllo;
2. message_thread_id corrisponde a COMMANDS;
3. telegram_user_id è autorizzato;
4. comando è nella whitelist;
5. payload è valido.
```

Pseudo-flusso:

```python
if message.chat_id != CONTROL_CHAT_ID:
    ignore()

if message.message_thread_id != COMMANDS_THREAD_ID:
    ignore()

if message.from_user.id not in AUTHORIZED_USERS:
    audit_rejected(reason="unauthorized_user")
    reject()

if command_name not in ALLOWED_COMMANDS:
    audit_rejected(reason="unknown_command")
    reject()

route_command(command)
```

Per comandi pericolosi futuri:

```text
- richiesta conferma;
- dry-run mode;
- idempotency key;
- controllo stato posizione;
- audit obbligatorio.
```

---

# 12. Audit DB comandi

Tabella proposta in `ops.sqlite3`:

```sql
CREATE TABLE IF NOT EXISTS ops_telegram_control_commands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    command_request_id TEXT NOT NULL UNIQUE,
    chat_id TEXT NOT NULL,
    message_thread_id TEXT NOT NULL,
    telegram_user_id TEXT NOT NULL,
    telegram_username TEXT,
    command_text TEXT NOT NULL,
    command_name TEXT,
    payload_json TEXT,
    received_at TEXT NOT NULL,
    status TEXT NOT NULL,
    reject_reason TEXT,
    execution_result TEXT,
    idempotency_key TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

Stati:

```text
RECEIVED
REJECTED
ACCEPTED
EXECUTED
FAILED
IGNORED
```

Motivo: senza audit non è possibile sapere chi ha mandato quale comando, quando, con quale effetto e con quale esito.

---

# 13. Command set MVP

## 13.1 Comandi informativi

| Comando | Funzione |
|---|---|
| `/help` | Elenco comandi disponibili |
| `/version` | Versione runtime / commit se disponibile |
| `/health` | Stato processo e componenti principali |
| `/status` | Riepilogo operativo |
| `/status_table` | Vista compatta delle chain attive |
| `/chains` | Elenco `TradeChain` attive |
| `/chain <id>` | Dettaglio di una chain |
| `/commands` | Execution commands aperti o recenti |
| `/reviews` | Ultimi eventi `REVIEW_REQUIRED` |
| `/control` | Controlli operativi attivi |
| `/logs [n]` | Ultime righe log applicativo filtrate |

## 13.2 Comandi controllo MVP

| Comando | Funzione |
|---|---|
| `/pause` | Blocca nuove entry globalmente |
| `/resume` | Rimuove blocco globale nuove entry |

Alias ammessi:

```text
/pause_execution  → /pause
/resume_execution → /resume
```

## 13.3 Comandi esclusi dal MVP

Non implementare subito:

```text
/stop
/fullstop
/halt
/panicclose
/forceclose
/closepartial
/cancelpending
/movetobe
/syncprotective
```

Questi richiedono una semantica più rigida e maggiori protezioni.

---

# 14. Semantica di `/pause`

## 14.1 Obiettivo

Bloccare l’apertura di nuove operazioni, senza interrompere:

```text
- gestione posizioni già aperte;
- processing degli update Telegram;
- fill detection;
- riconciliazione exchange;
- automazioni BE/TP/SL già attive;
- protezione posizioni esistenti.
```

## 14.2 Scrittura su `ops_control_state`

`/pause` crea o mantiene un controllo attivo:

```text
scope_type = GLOBAL
scope_value = NULL
execution_pause_mode = BLOCK_NEW_ENTRIES
emergency_action = NULL
reason = "telegram:/pause"
created_by = "<telegram_user_id>"
active = 1
```

## 14.3 Effetto sul runtime

Per nuovi `SIGNAL`:

```text
SIGNAL
  → enrichment PASS
  → LifecycleGateWorker
  → control_mode = BLOCK_NEW_ENTRIES
  → REVIEW_REQUIRED
  → nessuna TradeChain creata
```

Per `UPDATE` su posizioni esistenti:

```text
UPDATE
  → continua a essere elaborato
```

Questa distinzione è obbligatoria. Una pausa sulle nuove entry non deve impedire la protezione o chiusura di posizioni già esistenti.

## 14.4 Idempotenza

Doppio `/pause` non deve creare stati incoerenti.

Comportamento atteso:

```text
- se non esiste controllo attivo: crearne uno;
- se esiste già BLOCK_NEW_ENTRIES globale: mantenere quello o aggiornare metadata;
- auditare comunque il comando ricevuto.
```

---

# 15. Semantica di `/resume`

## 15.1 Obiettivo

Ripristinare l’ammissione di nuovi segnali operativi.

## 15.2 Comportamento corretto

`/resume` **non deve inserire un nuovo controllo con `NONE`**.

Deve invece disattivare i controlli attivi globali con:

```text
execution_pause_mode = BLOCK_NEW_ENTRIES
```

## 15.3 Effetto sul runtime

Dopo `/resume`:

```text
nuovo SIGNAL
  → enrichment PASS
  → LifecycleGateWorker
  → control_mode = NONE
  → può creare TradeChain
```

## 15.4 Idempotenza

Doppio `/resume` non deve creare errori.

Comportamento atteso:

```text
- se c’è un blocco attivo: disattivarlo;
- se non c’è blocco attivo: rispondere "nessun blocco attivo";
- auditare comunque il comando.
```

---

# 16. Gestione di `FULL_STOP`

## 16.1 Problema

Il dominio prevede già:

```text
FULL_STOP
```

ma il significato operativo non è ancora definito.

Possibili interpretazioni:

```text
1. bloccare nuove entry;
2. bloccare nuovi execution commands;
3. ignorare update Telegram;
4. annullare ordini pendenti;
5. chiudere posizioni aperte;
6. fermare il processo runtime;
7. scollegare adapter exchange;
8. bloccare reconciliation.
```

Sono comportamenti troppo diversi per essere esposti senza PRD dedicato.

## 16.2 Decisione MVP

Non esporre `FULL_STOP` nel MVP Telegram.

Quindi niente:

```text
/stop
/fullstop
/halt
/panicclose
```

## 16.3 Lavoro successivo

Creare PRD separato per:

```text
- semantica FULL_STOP;
- emergency policy;
- eventuale /halt;
- eventuale /panicclose;
- trattamento update;
- trattamento reconciliation;
- trattamento ordini pendenti;
- trattamento posizioni aperte.
```

---

# 17. Comandi manuali operativi — Fase 3

Una volta stabilizzato il control plane, si potranno introdurre comandi manuali sulle `TradeChain`.

## 17.1 Comandi candidati

| Comando | Effetto dominio |
|---|---|
| `/forceclose <chain_id>` | Genera `CLOSE_FULL` |
| `/closepartial <chain_id> <pct>` | Genera `CLOSE_PARTIAL` |
| `/cancelpending <chain_id>` | Genera `CANCEL_PENDING_ENTRY` |
| `/movetobe <chain_id>` | Genera `MOVE_STOP_TO_BREAKEVEN` |
| `/syncprotective <chain_id>` | Genera `SYNC_PROTECTIVE_ORDERS` |

## 17.2 Regola non negoziabile

Questi comandi **non devono mai chiamare direttamente l’adapter exchange**.

Flusso corretto:

```text
Telegram command
      ↓
RuntimeControlService
      ↓
LifecycleEvent / ExecutionCommand
      ↓
ops.sqlite3
      ↓
ExecutionCommandWorker
      ↓
ExecutionGateway
      ↓
Adapter exchange
```

Motivo:

```text
- idempotenza;
- audit trail;
- retry;
- coerenza con lifecycle;
- tracciabilità;
- nessuna scorciatoia fuori dalla pipeline.
```

---

# 18. Notifiche Telegram

Il control plane deve gestire due classi di notifiche:

```text
TECH_LOG   → diagnostica tecnica
CLEAN_LOG  → eventi dominio leggibili
```

## 18.1 Eventi ad alta priorità

Da inviare normalmente:

```text
REVIEW_REQUIRED
ORDER_REJECTED
SL_FILLED
CLOSE_FULL_FILLED
RECONCILIATION_WARNING
RECONCILIATION_FIXED
GATEWAY_ERROR_PERSISTENT
WATCHER_CRITICAL_FAILURE
CONTROL_CHANGED
EXECUTION_PAUSED
EXECUTION_RESUMED
```

## 18.2 Eventi a media priorità

Da inviare secondo configurazione:

```text
SIGNAL_ACCEPTED
SIGNAL_REJECTED
ENTRY_ORDER_PLACED
ENTRY_FILLED
TP_FILLED
CLOSE_PARTIAL_FILLED
PENDING_ENTRY_CANCELLED_CONFIRMED
STOP_MOVED
BE_ACTIVATED
```

## 18.3 Eventi a bassa priorità

Default: silent o off.

```text
TRADE_CHAIN_CREATED
EXECUTION_COMMAND_CREATED
EXECUTION_COMMAND_SENT
PROTECTIVE_SYNC_REQUESTED
TIMEOUT_CHECK_OK
```

---

# 19. Notification Outbox

## 19.1 Motivazione

Non inviare Telegram direttamente dai worker lifecycle/execution.

Problemi se si invia direttamente:

```text
- accoppiamento forte tra dominio e Telegram;
- perdita notifiche se Telegram non risponde;
- retry difficile;
- deduplicazione difficile;
- policy on/silent/off sparse;
- test più fragili.
```

## 19.2 Tabella proposta

```sql
CREATE TABLE IF NOT EXISTS ops_notification_outbox (
    notification_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    notification_type  TEXT NOT NULL,
    destination        TEXT NOT NULL,
    payload_json       TEXT NOT NULL,
    priority           TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'PENDING',
    dedupe_key         TEXT NOT NULL UNIQUE,
    attempts           INTEGER NOT NULL DEFAULT 0,
    last_error         TEXT,
    created_at         TEXT NOT NULL,
    sent_at            TEXT
);
```

`destination` può essere:

```text
TECH_LOG
CLEAN_LOG
COMMANDS_REPLY
```

## 19.3 Flusso

```text
Lifecycle / Execution / Reconciliation worker
        ↓
ops_notification_outbox
        ↓
TelegramNotificationDispatcher
        ↓
TopicRouter
        ↓
Telegram topic
```

---

# 20. Eventi CLEAN_LOG minimi

Primo set:

```text
SIGNAL_ACCEPTED
SIGNAL_REJECTED
ENTRY_ORDER_PLACED
ENTRY_FILLED
TP_HIT
SL_HIT
SL_MOVED
BE_ACTIVATED
POSITION_PARTIALLY_CLOSED
POSITION_CLOSED
PENDING_ORDER_CANCELLED
RECONCILIATION_WARNING
RECONCILIATION_FIXED
EXECUTION_PAUSED
EXECUTION_RESUMED
```

Questi eventi devono arrivare dal dominio lifecycle/execution, non dal logger tecnico.

---

# 21. TECH_LOG policy

## 21.1 Default

Inviare solo:

```text
WARNING
ERROR
CRITICAL
```

## 21.2 DEBUG

DEBUG deve essere temporaneo e configurabile.

Esempio futuro:

```text
/debug_on 10m
/debug_off
```

Non incluso nel MVP se non necessario.

## 21.3 Deduplicazione

Errore ripetuto:

```text
[ERROR] Bybit retCode 10001 on trading_stop
```

Non deve produrre 100 messaggi uguali.

Comportamento desiderato:

```text
1 messaggio iniziale
1 riepilogo aggregato dopo N ripetizioni
```

Esempio:

```text
[ERROR] Bybit retCode 10001 on trading_stop
Repeated 37 times in the last 60 seconds.
```

---

# 22. Messaggi Telegram suggeriti

## 22.1 `/status`

```text
Runtime V2 — STATUS

Control:
- Global mode: BLOCK_NEW_ENTRIES

Trade chains:
- Active: 4
- Waiting entry: 1
- Open: 2
- Partially closed: 1

Execution:
- Pending commands: 3
- Sent/ACK: 2
- Review required: 1

Exchange sync:
- New exchange events: 0
```

## 22.2 `/chain 145`

```text
Trade Chain #145

Trader: trader_a
Symbol: BTCUSDT
Side: LONG
State: OPEN

Entry:
- Avg price: 67240.5
- Filled qty: 0.004

Protection:
- Current SL: 66500
- BE status: NOT_PROTECTED

Execution:
- Active commands: 2
- Last event: ENTRY_FILLED
```

## 22.3 `/pause`

```text
Nuove entry bloccate.

Scope: GLOBAL
Mode: BLOCK_NEW_ENTRIES

Update, lifecycle, reconciliation e gestione posizioni aperte restano attivi.
```

## 22.4 `/resume`

```text
Blocco globale sulle nuove entry rimosso.

Il runtime può nuovamente accettare nuovi SIGNAL operativi.
```

## 22.5 CLEAN_LOG — entry filled

```text
BTCUSDT LONG
Entry filled

Chain: #145
Avg entry: 67240.5
Qty: 0.004
SL: 66500
Status: OPEN
```

## 22.6 CLEAN_LOG — TP hit

```text
BTCUSDT LONG
TP1 hit

Closed: 50%
Remaining: 50%
SL moved: BE
Status: PARTIALLY_CLOSED
```

## 22.7 TECH_LOG — reconciliation warning

```text
[WARN] Reconciliation mismatch

Symbol: BTCUSDT
Chain: #145
Issue: local protective order missing on exchange
Action: REVIEW_REQUIRED
```

---

# 23. Integrazione in `main.py`

Nel bootstrap di `main.py`, dopo la costruzione dei repository e del runtime execution:

```python
control_service = RuntimeControlService(
    ops_db_path=ops_db_path,
    parser_db_path=parser_db_path,
    control_repo=control_repo,
    chain_repo=chain_repo,
    command_repo=command_repo,
    lifecycle_event_repo=lifecycle_event_repo,
    exchange_event_repo=exchange_event_repo,
)

telegram_control_bot = TelegramControlBot(
    config=telegram_control_config,
    service=control_service,
    logger=logger,
)

telegram_notification_dispatcher = TelegramNotificationDispatcher(
    config=telegram_control_config,
    outbox_repo=notification_outbox_repo,
    topic_router=topic_router,
    logger=logger,
)

telegram_control_task = asyncio.create_task(
    telegram_control_bot.run()
)

telegram_notifications_task = asyncio.create_task(
    telegram_notification_dispatcher.run()
)
```

Shutdown:

```python
telegram_control_task.cancel()
telegram_notifications_task.cancel()

await telegram_control_bot.shutdown()
await telegram_notification_dispatcher.shutdown()
```

---

# 24. Estensioni repository necessarie

## 24.1 `ControlStateRepository`

Aggiungere:

```python
def create_control(self, control: ControlState) -> ControlState:
    ...

def deactivate_controls(
    self,
    *,
    scope_type: str,
    scope_value: str | None = None,
    mode: str | None = None,
) -> int:
    ...

def list_active_controls(self) -> list[ControlState]:
    ...

def get_latest_active_control(
    self,
    *,
    scope_type: str,
    scope_value: str | None = None,
    mode: str | None = None,
) -> ControlState | None:
    ...
```

## 24.2 Query layer

Aggiungere:

```python
get_active_chain_summary()
get_chain_by_id(chain_id)
get_recent_reviews(limit)
get_execution_command_summary()
get_recent_execution_commands(limit)
get_recent_lifecycle_events(limit)
get_unprocessed_exchange_event_count()
get_control_summary()
```

## 24.3 `NotificationOutboxRepository`

Aggiungere:

```python
enqueue_notification(...)
get_pending_notifications(limit)
mark_sent(notification_id)
mark_failed(notification_id, error)
increment_attempts(notification_id)
```

## 24.4 `TelegramControlAuditRepository`

Aggiungere:

```python
record_received(...)
record_rejected(...)
record_accepted(...)
record_executed(...)
record_failed(...)
```

---

# 25. Database / migration

Nuove tabelle minime:

```text
ops_telegram_control_commands
ops_notification_outbox
```

Possibile tabella opzionale futura:

```text
ops_telegram_control_sessions
```

Non richiesta nel MVP.

---

# 26. Test plan

## 26.1 Unit test — auth

Casi:

```text
chat errata → comando ignorato/rifiutato
topic errato → comando ignorato/rifiutato
user non autorizzato → comando rifiutato
user autorizzato → comando inoltrato
comando sconosciuto → comando rifiutato
payload invalido → comando rifiutato
```

## 26.2 Unit test — service

Casi:

```text
/pause crea controllo BLOCK_NEW_ENTRIES
/pause ripetuto non crea duplicati incoerenti
/resume disattiva controllo attivo
/resume senza controllo attivo non fallisce
/control restituisce stato coerente
/status aggrega dati da ops.sqlite3
```

## 26.3 Unit test — repository

Casi:

```text
create_control
deactivate_controls
list_active_controls
get_effective_mode compatibile
audit command lifecycle
outbox enqueue/dedupe/retry
```

## 26.4 Unit test — formatter

Casi:

```text
/status
/chains
/chain <id>
/reviews
CLEAN_LOG entry filled
CLEAN_LOG TP hit
TECH_LOG warning
```

## 26.5 Integration test

Casi:

```text
pause attivo → nuovo SIGNAL finisce in REVIEW_REQUIRED e non crea TradeChain
resume → nuovo SIGNAL torna a creare TradeChain
update su posizione aperta continua a essere processato durante pause
evento REVIEW_REQUIRED → outbox PENDING → dispatcher invia → SENT
utente non autorizzato → audit REJECTED
messaggio in topic sbagliato → ignorato
```

---

# 27. Acceptance criteria

La feature è accettata quando:

```text
1. Il bot di controllo Telegram parte insieme al runtime V2.
2. Il bot usa un supergruppo privato con topic separati.
3. I comandi vengono letti solo da COMMANDS.
4. I messaggi fuori da COMMANDS vengono ignorati.
5. Gli utenti non autorizzati vengono respinti.
6. /status restituisce dati coerenti da ops.sqlite3.
7. /pause blocca nuove entry senza fermare update e lifecycle.
8. /resume riabilita nuove entry.
9. /control mostra controlli attivi.
10. /reviews mostra casi recenti da gestire.
11. TECH_LOG riceve solo log tecnici filtrati/rate-limited.
12. CLEAN_LOG riceve solo eventi dominio puliti.
13. Ogni comando viene scritto in audit DB.
14. Le notifiche critiche passano da outbox.
15. Nessun handler Telegram interagisce direttamente con exchange adapter.
16. Il listener segnali trader resta separato dal bot di controllo.
17. Il comportamento è coperto da unit e integration test.
```

---

# 28. Piano di implementazione consigliato

## Fase 1 — Control Plane minimale

Implementare:

```text
- config;
- auth;
- telegram_bot;
- RuntimeControlService;
- audit comandi;
- /help;
- /status;
- /health;
- /control;
- /pause;
- /resume;
- /reviews;
- test.
```

Output: controllo operativo base funzionante.

## Fase 2 — Topic e notifiche

Implementare:

```text
- topic_router;
- TECH_LOG;
- CLEAN_LOG;
- notification outbox;
- notification dispatcher;
- clean event formatter;
- tech log formatter;
- rate limit;
- dedupe.
```

Output: separazione completa tra comandi, log tecnici, eventi puliti.

## Fase 3 — Osservabilità operativa

Implementare:

```text
- /chains;
- /chain <id>;
- /commands;
- /logs;
- status_table;
- keyboard.
```

Output: Telegram diventa una mini-console operativa.

## Fase 4 — Comandi lifecycle manuali

Implementare solo dopo stabilizzazione:

```text
- /forceclose;
- /closepartial;
- /cancelpending;
- /movetobe;
- /syncprotective.
```

Vincolo: devono generare eventi/comandi dominio, mai chiamate dirette all’adapter.

## Fase 5 — Emergency mode

PRD separato per:

```text
- FULL_STOP;
- /halt;
- /panicclose;
- policy ordini pendenti;
- policy posizioni aperte;
- blocco execution dispatch;
- stato reconciliation.
```

---

# 29. Rischi principali e mitigazioni

## 29.1 Spam tecnico

Rischio:

```text
TECH_LOG diventa inutilizzabile.
```

Mitigazione:

```text
- min_level WARNING;
- batching;
- dedupe;
- rate limit;
- riepiloghi aggregati.
```

## 29.2 Comandi non autorizzati

Rischio:

```text
utente nel gruppo invia comandi operativi.
```

Mitigazione:

```text
- chat_id;
- message_thread_id;
- user_id whitelist;
- command whitelist;
- audit obbligatorio.
```

## 29.3 Confusione tra TECH_LOG e CLEAN_LOG

Rischio:

```text
CLEAN_LOG diventa una versione abbellita dei log tecnici.
```

Mitigazione:

```text
TECH_LOG deriva dal logger tecnico.
CLEAN_LOG deriva da domain events.
```

## 29.4 Doppia esecuzione comandi

Rischio:

```text
/close BTCUSDT
/close BTCUSDT
```

Mitigazione:

```text
- command_request_id;
- idempotency_key;
- controllo stato posizione;
- audit;
- comandi pericolosi non nel MVP.
```

## 29.5 Bypass della pipeline runtime

Rischio:

```text
handler Telegram chiama direttamente adapter exchange.
```

Mitigazione:

```text
vietato.
Tutti i comandi manuali devono passare da lifecycle/event/command pipeline.
```

---

# 30. Decisione finale

Implementare un **Telegram Control Plane dedicato dentro `runtime_v2`**, separato dal listener di intake, usando un **supergruppo Telegram privato con topic separati**.

Decisioni vincolanti:

```text
1. Il listener segnali resta in src/telegram e usa Telethon.
2. Il control bot sta in src/runtime_v2/control_plane.
3. COMMANDS riceve solo comandi autorizzati.
4. TECH_LOG riceve solo diagnostica tecnica filtrata.
5. CLEAN_LOG riceve solo eventi dominio puliti.
6. /pause blocca solo nuove entry.
7. /resume rimuove il blocco nuove entry.
8. FULL_STOP non entra nel MVP.
9. Nessun comando Telegram bypassa lifecycle/execution/ops.sqlite3.
10. Audit e idempotenza sono obbligatori.
```

Sintesi:

```text
COMMANDS controlla il sistema.
TECH_LOG spiega problemi tecnici.
CLEAN_LOG racconta cosa succede ai trade.
```
