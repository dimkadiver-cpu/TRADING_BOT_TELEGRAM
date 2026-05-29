# Control Plane Telegram — Design Spec

Date: 2026-05-29  
Status: APPROVED  
Refs: `docs/Raggionamento/Controllo_Notifica/COMMANDS_SPEC.md`, `CLEAN_LOG_SPEC.md`, `TECH_LOG_SPEC.md`

---

## 1. Obiettivo

Implementare il Control Plane Telegram del runtime V2: un bot Telegram che:
1. Invia notifiche operative (CLEAN_LOG) e diagnostiche (TECH_LOG) su un supergruppo privato
2. Riceve comandi dall'operatore (COMMANDS) per osservare e controllare il runtime

Il sistema si integra nel processo asyncio esistente di `main.py` come due task aggiuntivi.

---

## 2. Architettura

### Componenti

```
main.py (asyncio loop)
├── [worker esistenti]
│   ├── LifecycleGateWorker
│   ├── LifecycleEventWorker
│   ├── ExecutionCommandWorker
│   └── ExchangeEventSyncWorker
│
└── [nuovi — control_plane]
    ├── TelegramControlBot              ← riceve comandi operatore
    └── TelegramNotificationDispatcher  ← consuma outbox → Telegram
```

### Flusso notifiche (outbox pattern)

```
Worker lifecycle/exchange
    ↓ outbox_writer.write_clean_log_event(conn, event_type, payload)
ops_notification_outbox (DB)
    ↓ poll ogni 2s
TelegramNotificationDispatcher
    ↓ topic_router → CLEAN_LOG | TECH_LOG
Formatter dedicato
    ↓
python-telegram-bot API
```

Nessun worker scrive direttamente su Telegram. L'outbox garantisce retry, deduplicazione e rate limiting disaccoppiati dal runtime.

### Flusso comandi

```
Telegram (operatore)
    ↓ update
TelegramControlBot (polling)
    ↓ auth.validate()
    ↓ route_command()
CommandHandler
    ↓
RuntimeControlService
    ↓ legge/scrive ops.sqlite3
risposta → reply nel topic COMMANDS
```

`RuntimeControlService` è l'unico entry point per le operazioni sul DB dal bot.

### Interfacce tra parti

| Confine | Interfaccia |
|---|---|
| Worker esistenti → Parte 2 | `outbox_writer.write_clean_log_event()` (SQL insert) |
| Parte 2 → Parte 3 | nessuna — indipendenti |
| Parte 3/4 → DB | `RuntimeControlService` (unico entry point) |
| Parte 4 → LifecycleGate | `ops_control_state` (già letto da `LifecycleGateWorker`) |
| Tutto → `main.py` | Parte 5 crea task asyncio e gestisce SIGTERM |

---

## 3. Package Layout

```
src/runtime_v2/control_plane/
├── __init__.py
├── models.py                   — Pydantic: ControlCommand, NotificationOutboxEntry, ControlState, ConfigOverride
├── config.py                   — carica e valida telegram_control.yaml
├── auth.py                     — valida chat_id, thread_id, user_id su ogni update
├── outbox_writer.py            — API per worker: write_clean_log_event(), write_tech_log_event()
├── topic_router.py             — mappa destination → thread_id da config
├── notification_dispatcher.py  — loop asyncio: poll outbox, formatta, invia, retry
├── telegram_bot.py             — inizializza python-telegram-bot, registra handler, polling
├── service.py                  — RuntimeControlService: tutti i metodi read + write
├── status_queries.py           — query read-only su ops.sqlite3
├── audit_store.py              — scrive ops_telegram_control_commands
├── override_store.py           — gestisce ops_config_overrides
├── snapshot_store.py           — salva/legge ops_runtime_snapshot
└── formatters/
    ├── __init__.py
    ├── clean_log.py            — formatter eventi CLEAN_LOG
    ├── tech_log.py             — formatter errori TECH_LOG
    ├── status.py
    ├── trades.py
    ├── trade_detail.py
    ├── health.py
    ├── control.py
    ├── reviews.py
    ├── pnl.py
    ├── pause.py
    ├── block.py
    └── debug.py
```

---

## 4. DB — Nuove Tabelle

Migration: `db/ops_migrations/007_ops_control_plane.sql`

### `ops_notification_outbox`

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

### `ops_telegram_control_commands`

```sql
CREATE TABLE IF NOT EXISTS ops_telegram_control_commands (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    command_request_id  TEXT NOT NULL UNIQUE,
    chat_id             TEXT NOT NULL,
    message_thread_id   TEXT NOT NULL,
    telegram_user_id    TEXT NOT NULL,
    telegram_username   TEXT,
    command_text        TEXT NOT NULL,
    command_name        TEXT,
    payload_json        TEXT,
    received_at         TEXT NOT NULL,
    status              TEXT NOT NULL,
    reject_reason       TEXT,
    execution_result    TEXT,
    idempotency_key     TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
```

### `ops_config_overrides`

```sql
CREATE TABLE IF NOT EXISTS ops_config_overrides (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    override_key TEXT NOT NULL,
    scope_type   TEXT NOT NULL,       -- GLOBAL | PER_TRADER
    scope_value  TEXT,                -- trader_id se PER_TRADER
    value_json   TEXT NOT NULL,
    created_by   TEXT NOT NULL,
    reason       TEXT,
    active       INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
```

### `ops_runtime_snapshot`

```sql
CREATE TABLE IF NOT EXISTS ops_runtime_snapshot (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_at           TEXT NOT NULL,
    control_mode          TEXT NOT NULL,
    active_blocks_json    TEXT NOT NULL,
    open_chain_count      INTEGER NOT NULL,
    pending_command_count INTEGER NOT NULL,
    shutdown_reason       TEXT,
    created_at            TEXT NOT NULL
);
```

**Nota:** `ops_control_state` esiste già in `ops_migrations/001_ops_lifecycle_core.sql` — compatibile con lo spec, nessuna modifica necessaria.

---

## 5. Config — `config/telegram_control.yaml`

Template completo da COMMANDS_SPEC §2. Valori sensibili (token, chat_id, user_id) vengono letti da variabili d'ambiente tramite sostituzione in `config.py` o direttamente da `.env`.

Campi chiave:
- `token_env: CONTROL_TELEGRAM_BOT_TOKEN`
- `chat_id` — id supergruppo privato
- `topics.commands.thread_id`, `topics.tech_log.thread_id`, `topics.clean_log.thread_id`
- `authorized_users: []` — lista telegram user_id
- `startup.mode: auto | standby | restore`

---

## 6. Error Handling

### Dispatcher — fallimenti Telegram API

```
Tentativo 1 fallisce → retry dopo 5s
Tentativo 2 fallisce → retry dopo 30s
Tentativo 3 fallisce → marca FAILED, logga su file, prosegue
```

Il runtime non si ferma per fallimenti di notifica. L'outbox accumula — quando Telegram torna disponibile il dispatcher svuota la coda in ordine.

### Comandi bot

Ogni handler è wrappato in `try/except`. Eccezioni interne → risposta generica in COMMANDS + log su file. Mai stacktrace su Telegram.

### SQLite concorrenza

Worker e dispatcher girano nello stesso processo asyncio. `ops_notification_outbox` usa `BEGIN IMMEDIATE` per evitare double-dispatch in lettura concorrente. Le query di scrittura dei worker rimangono sync (sqlite3) — già serializzate nel loop asyncio.

### Telegram flood

`python-telegram-bot` gestisce automaticamente `RetryAfter` con backoff. Il dispatcher rispetta `max_messages_per_minute` dalla config prima di chiamare l'API.

---

## 7. Le 5 Parti — Sequenza Implementazione

### Parte 1 — Foundation

**File:** `007_ops_control_plane.sql`, `telegram_control.yaml`, `models.py`, `config.py`, `auth.py`

**Deliverable:** infrastruttura condivisa. Nessuna funzionalità visibile su Telegram.

**Test:** unit su `config.py` (YAML valido/invalido, campi mancanti) e `auth.py` (user autorizzato, non autorizzato, topic sbagliato).

---

### Parte 2 — CLEAN_LOG Notifiche

**File:** `outbox_writer.py`, `topic_router.py`, `notification_dispatcher.py`, `formatters/clean_log.py`

**Integrazione worker:** `LifecycleEventWorker` e `ExchangeEventSyncWorker` chiamano `outbox_writer` dopo ogni transizione lifecycle significativa (SIGNAL_ACCEPTED, ENTRY_OPENED, TP_FILLED, SL_FILLED, POSITION_CLOSED, UPDATE_DONE/PARTIAL/REJECTED).

**Deliverable:** messaggi CLEAN_LOG arrivano su Telegram al lifecycle dei trade.

**Test:** unit su ogni formatter (payload → testo atteso), integration su dispatcher (outbox con entry → messaggi inviati, retry su failure, deduplicazione su `dedupe_key`).

---

### Parte 3 — Bot Read-Only

**File:** `telegram_bot.py`, `service.py` (metodi read), `status_queries.py`, `formatters/status.py`, `trades.py`, `trade_detail.py`, `health.py`, `control.py`, `reviews.py`

**Comandi:** `/help`, `/status`, `/trades`, `/trade <id>`, `/health`, `/control`, `/reviews`, `/version`

**Deliverable:** operatore può interrogare il runtime da Telegram.

**Test:** unit su formatter (dati mock → testo, semaforo 🟢/🟡/🔴 corretto), unit su `status_queries` con DB in-memory.

---

### Parte 4 — Comandi Controllo

**File:** `audit_store.py`, `override_store.py`, `formatters/pause.py`, `formatters/block.py`  
**Aggiornamenti:** `service.py` (metodi write), `telegram_bot.py` (nuovi handler)

**Comandi:** `/pause [trader]`, `/resume [trader]`, `/block <symbol>`, `/block <trader> <symbol>`, `/unblock ...`, `/start`

**Deliverable:** operatore può bloccare/sbloccare entry e blacklistare simboli.

**Test:** integration con DB in-memory — `/pause` → record in `ops_control_state` → `/control` mostra blocco → `/resume` → record disattivato. Idempotenza verificata su doppio `/pause`.

---

### Parte 5 — Avanzati + Integrazione main.py

**File:** `snapshot_store.py`, `formatters/pnl.py`, `formatters/debug.py`, `formatters/tech_log.py`  
**Aggiornamenti:** `service.py` (get_pnl, debug), `notification_dispatcher.py` (TECH_LOG: dedup, batch, rate limit), `main.py` (task asyncio, SIGTERM, startup mode)

**Comandi:** `/pnl`, `/logs`, `/debug_on [durata]`, `/debug_off`

**Startup modes:** `auto` (default), `standby` (parte bloccato), `restore` (legge snapshot DB, fallback auto se stale)

**SIGTERM:** `snapshot_store.save()` → notifica TECH_LOG → `task.cancel()` su tutti i worker → shutdown ordinato

**Deliverable:** sistema completo integrato in main.py con lifecycle completo.

**Test:** shutdown graceful (mock SIGTERM → snapshot scritto → task cancellati), startup restore con snapshot fresco vs stale (> `restore_max_age_seconds`).

---

## 8. Comandi Esclusi (fuori scope)

Da COMMANDS_SPEC §14 — richiedono PRD dedicato:

```
/forceclose, /closepartial, /cancelpending, /movetobe, /halt, /panicclose
```

Non implementati nemmeno come stub.

---

## 9. Dipendenze

Aggiungere a `requirements.txt`:

```
python-telegram-bot>=21.0
```

Già presenti: `aiosqlite`, `pydantic>=2.0`, `python-dotenv`, `pytest-asyncio`.

---

## 10. Acceptance Criteria Consolidati

Da COMMANDS_SPEC §15, CLEAN_LOG_SPEC §18, TECH_LOG_SPEC §11 — tutti applicabili.

Criteri aggiuntivi specifici di questo design:

```
1. Nessun worker scrive direttamente su Telegram — tutto passa dall'outbox.
2. RuntimeControlService è l'unico entry point per scritture DB dal bot.
3. Il runtime non si ferma per fallimenti di notifica Telegram.
4. ops_control_state già esistente è usato senza migration aggiuntive.
5. I due task asyncio (bot + dispatcher) si avviano e terminano coordinati con main.py.
6. Nessun comando escluso da §14 è implementato nemmeno parzialmente.
```
