# COMMANDS_SPEC — Telegram Control Plane

Versione: 2.0
Sostituisce: `PRD_Telegram_Control_Plane_Runtime_V2_UNIFICATO`, `COMMAND_RESPONSES_RUNTIME_V2_COMPACT`

---

## 1. Principi fondamentali

Il Control Plane Telegram è separato dal listener segnali trader esistente.

```text
Telegram Intake Listener  → legge segnali canali trader → Telethon
Telegram Control Bot      → riceve comandi operatore   → python-telegram-bot
```

Il bot di controllo usa un supergruppo privato Telegram con tre topic separati:

```text
COMMANDS   → comandi manuali dell'operatore
TECH_LOG   → diagnostica tecnica, errori, warning
CLEAN_LOG  → eventi operativi del ciclo vita trade
```

Separazione netta:

```text
input umano       → COMMANDS
diagnostica       → TECH_LOG
stato operativo   → CLEAN_LOG
```

Nessun handler Telegram deve mai chiamare direttamente un adapter exchange.
Tutti i comandi operativi passano attraverso la pipeline lifecycle/execution/ops.sqlite3.

---

## 2. Configurazione — `config/telegram_control.yaml`

```yaml
enabled: true

token_env: CONTROL_TELEGRAM_BOT_TOKEN   # variabile d'ambiente con il token bot

chat_id: "-1001234567890"               # id supergruppo privato di controllo

topics:
  commands:
    thread_id: 101
  tech_log:
    thread_id: 102
    min_level: WARNING                  # WARNING | INFO | DEBUG
    batch_seconds: 10
    max_messages_per_minute: 20
    dedupe_window_seconds: 60
    operational_events: false           # mostra decisioni interne runtime
  clean_log:
    thread_id: 103
    debounce_seconds: 20
    aggregate_fills_seconds: 30
    aggregate_updates_seconds: 20
    max_messages_per_chain_per_minute: 4
    min_partial_fill_notify_pct: 10

authorized_users:
  - "123456789"                         # telegram user_id autorizzati

startup:
  mode: auto                            # auto | standby | restore
  restore_max_age_seconds: 300          # usato solo con mode=restore

keyboard:
  - ["/status", "/health", "/control"]
  - ["/trades", "/reviews", "/logs"]
  - ["/pause", "/resume"]
  - ["/block", "/debug_on"]

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

Valori possibili per `notifications`:

```text
on      → invia messaggio con notifica sonora
silent  → invia senza notifica sonora
off     → non inviare
```

---

## 3. Startup e Shutdown

### 3.1 Modalità di avvio — `startup.mode`

**`auto`**

Il bot parte operativo. Rispetta i controlli attivi presenti in DB (pause, blocchi).
Se nel DB è presente `BLOCK_NEW_ENTRIES`, il runtime parte già bloccato.
Nessun comando manuale richiesto per iniziare ad operare.

**`standby`**

Il bot parte sempre in `BLOCK_NEW_ENTRIES` indipendentemente dal DB.
Il runtime non accetta nuovi segnali finché l'operatore non invia `/start` o `/resume`.

Notifica all'avvio in CLEAN_LOG:

```text
⏸️ RUNTIME IN STANDBY
────────────────
Bot avviato in modalità standby.
Nuove entry bloccate.

Usa /start o /resume per attivare.

────────────────
Source: runtime
```

**`restore`**

Il bot legge l'ultimo snapshot salvato in DB e ripristina lo stato esatto.
Se lo snapshot è più vecchio di `restore_max_age_seconds` secondi, ricade in modalità `auto`.

Notifica all'avvio in CLEAN_LOG:

```text
▶️ RUNTIME RESTORED
────────────────
Stato ripristinato da snapshot DB.
Snapshot age: 42s

Active blocks: BLOCK_NEW_ENTRIES (GLOBAL)
Open chains: 3
Pending commands: 1

────────────────
Source: runtime
```

Se lo snapshot è stale:

```text
⚠️ RESTORE FALLBACK — AUTO
────────────────
Snapshot DB troppo vecchio (480s > max 300s).
Fallback a modalità auto.

────────────────
Source: runtime
```

### 3.2 Notifica di avvio standard (tutte le modalità)

Inviata in CLEAN_LOG:

```text
🟢 RUNTIME AVVIATO
────────────────
Modalità: auto
Control: NONE
Open chains: 5
Pending commands: 2

────────────────
Source: runtime
```

### 3.3 Shutdown graceful (SIGTERM)

Al ricevimento di SIGTERM il bot:

1. Salva snapshot stato corrente in DB (`ops_runtime_snapshot`)
2. Invia notifica in TECH_LOG:

```text
⚠️ RUNTIME SHUTDOWN
────────────────
Motivo: SIGTERM

Stato al momento dello shutdown:
Open chains: 5
Pending commands: 2
Active blocks: NONE
Control mode: NONE

Snapshot salvato in DB.

────────────────
Source: runtime
```

3. Shutdown ordinato dei task asyncio

### 3.4 Crash

In caso di crash non gestito non è garantita la scrittura dello snapshot.
`startup.mode: restore` con snapshot assente o stale ricade in `auto`.

### 3.5 Tabella snapshot DB

```sql
CREATE TABLE IF NOT EXISTS ops_runtime_snapshot (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_at TEXT NOT NULL,
    control_mode TEXT NOT NULL,
    active_blocks_json TEXT NOT NULL,
    open_chain_count INTEGER NOT NULL,
    pending_command_count INTEGER NOT NULL,
    shutdown_reason TEXT,
    created_at TEXT NOT NULL
);
```

---

## 4. Autorizzazione

### 4.1 Modello

Un solo livello: autorizzato o non autorizzato.
La whitelist di `telegram_user_id` è definita in `config/telegram_control.yaml` sotto `authorized_users`.

### 4.2 Flusso di validazione

Ogni update Telegram ricevuto passa attraverso:

```python
if message.chat_id != CONTROL_CHAT_ID:
    ignore()

if message.message_thread_id != COMMANDS_THREAD_ID:
    ignore()

if message.from_user.id not in authorized_users:
    audit_rejected(reason="unauthorized_user")
    # non rispondere per non rivelare dettagli
    return

if command_name not in ALLOWED_COMMANDS:
    audit_rejected(reason="unknown_command")
    reply("Comando non riconosciuto.")
    return

route_command(command)
```

### 4.3 Regole

```text
- Messaggi in topic errato: ignorati silenziosamente
- Utenti non autorizzati: auditati come REJECTED, nessuna risposta
- Comandi sconosciuti: risposta generica, auditati come REJECTED
- Payload invalido: risposta di errore, auditato come REJECTED
```

---

## 5. DB Override — Config a Runtime

### 5.1 Principio

Il file `operation_config.yaml` è la configurazione base (default).
Le modifiche fatte via comando Telegram vengono salvate in DB come override.
Il runtime merge: `config_effettiva = YAML_base + DB_override`.

### 5.2 Cosa è modificabile via Telegram

Solo due categorie:

```text
1. Stato di controllo (pause/resume)  → tabella ops_control_state
2. Symbol blacklist                   → tabella ops_config_overrides
```

Tutto il resto della config resta esclusivamente nel file YAML.

### 5.3 Tabella override

```sql
CREATE TABLE IF NOT EXISTS ops_config_overrides (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    override_key TEXT NOT NULL,         -- es. "symbol_blacklist.global"
    scope_type TEXT NOT NULL,           -- GLOBAL | PER_TRADER
    scope_value TEXT,                   -- trader_id se PER_TRADER
    value_json TEXT NOT NULL,           -- valore serializzato
    created_by TEXT NOT NULL,           -- telegram_user_id
    reason TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

---

## 6. Command Set Informativo

Questi comandi sono read-only. Non modificano lo stato del runtime.
Tutte le risposte vengono inviate nel topic `COMMANDS`.

### 6.1 `/help`

Mostra l'elenco dei comandi disponibili.

```text
📋 COMANDI DISPONIBILI
────────────────
Informativi:
/status    — salute bot e conteggi
/trades    — trade aperti
/trade #id — dettaglio singola chain
/pnl       — PnL e ROI per account attivi
/health    — stato workers
/control   — blocchi operativi
/reviews   — casi da controllare
/logs [n]  — ultime righe log

Controllo:
/pause              — blocca nuove entry (globale)
/pause trader_a     — blocca nuove entry per trader
/resume             — riabilita nuove entry (globale)
/resume trader_a    — riabilita per trader

Config:
/block BTCUSDT              — blacklist simbolo globale
/block trader_a BTCUSDT     — blacklist simbolo per trader
/unblock BTCUSDT
/unblock trader_a BTCUSDT

Debug:
/debug_on [10m]   — attiva log verbose
/debug_off        — disattiva log verbose

/start    — attiva runtime (solo in modalità standby)
```

### 6.2 `/status`

Mostra salute generale del bot. Non duplica `/trades`.

**Versione OK:**

```text
🟢 Runtime V2 — STATUS
────────────────
Updated: 14:32:10

Mode:
New entries: ENABLED
Control: none
Exchange: OK
Sync: fresh, 4s ago

Workers:
Parser: OK
Lifecycle: OK
Execution: OK
Exchange sync: OK
Notifications: OK

Trades:
Open: 7
Waiting entry: 2
Partial: 1
Review required: 1

Execution:
Pending commands: 2
Failed commands: 0
Rejected last hour: 1

Risk:
No SL: 0
SL not at BE: 3
Reconciliation warnings: 1

PnL:
Unrealized: +128.40 USDT
Fees today: 7.25 USDT
Funding today: -1.80 USDT

Use:
/trades
/reviews
/control
```

**Versione WARNING:**

```text
🟡 Runtime V2 — STATUS
────────────────
Updated: 14:32:10

Mode:
New entries: ENABLED
Control: none
Exchange: OK
Sync: stale, 48s ago

Workers:
Parser: OK
Lifecycle: OK
Execution: OK
Exchange sync: WARNING
Notifications: OK

Trades:
Open: 7
Waiting entry: 2
Partial: 1
Review required: 3

Execution:
Pending commands: 5
Failed commands: 1
Rejected last hour: 2

Risk:
No SL: 1
SL not at BE: 3
Reconciliation warnings: 2

PnL:
Unrealized: +128.40 USDT
Fees today: 7.25 USDT
Funding today: -1.80 USDT

Attention:
⚠️ #151 missing SL
⚠️ Exchange sync stale
⚠️ 3 reviews required

Use:
/trades
/reviews
/control
```

**Semaforo:**

```text
🟢 OK      — workers attivi, sync fresca, nessun trade senza SL, nessun errore critico
🟡 WARNING  — sync vecchia, review required, failed commands, trade con SL non ideale
🔴 CRITICAL — exchange offline, execution worker fermo, trade senza SL, adapter error persistente
```

### 6.3 `/trades`

Lista compatta dei trade aperti.

```text
📊 OPEN TRADES — 7 active
────────────────
Updated: 14:32:10
Control: ACTIVE | Exchange: OK | Sync: 4s ago

Total:
Unrealized: +128.40 USDT
Fees today: 7.25 USDT
Funding today: -1.80 USDT

────────────────
#145 BTC LONG  | +83.20 USDT | +3.19% | SL: BE     | TP: 1/2
#148 ETH LONG  | +31.50 USDT | +1.32% | SL: 3,280  | TP: 0/3 | Pend: E2
#151 SOL SHORT | +13.70 USDT | +1.50% | SL: 176.00 | TP: 0/2
#155 XRP LONG  | -4.80 USDT  | -0.60% | SL: 0.512  | TP: 0/2
#160 ADA SHORT | +5.20 USDT  | +0.90% | SL: BE     | TP: 1/3

────────────────
Warnings: 2
⚠️ #148 pending limit entry
⚠️ #151 SL not at BE

Use:
/trade #id for details
/reviews for blocked cases
```

**Formato riga:**

```text
#chain_id SYMBOL SIDE | PnL USDT | ROI% | SL | TP progress | flags opzionali
```

**Flag ammessi:**

```text
Pend: E2   → entry limit ancora pendente
NoSL       → SL mancante
Risk       → rischio operativo
Review     → richiede controllo
Sync?      → dato exchange non fresco
```

**Ordinamento:**

```text
1. trade con warning/rischio
2. trade in perdita
3. trade aperti normali
4. partially closed
5. waiting entry
```

**Soglie:**

```text
0 trade    → messaggio vuoto chiaro
1-10 trade → mostra tutti
>10 trade  → mostra top 10 per rischio, aggiungi "Showing 10/24"
```

**Account mode:**

```text
account_mode: single
→ nessun raggruppamento per account, account implicito
→ "Exchange Account" visibile solo in /trade #id

account_mode: per_trader_subaccount
→ trades raggruppati per account con header
→ es. "── sub_01 (trader_a) ──" prima delle righe di quel trader
→ /pnl per breakdown completo per account
```

### 6.4 `/trade <id>`

Dettaglio singola chain.

```text
/trade #145
```

```text
📌 TRADE #145
────────────────
BTC/USDT — 📈 LONG
Trader: trader_a
Exchange Account: main

Position:
Avg entry: 65,020
Mark: 67,100
Size: 0.004 BTC
Unrealized PnL: +83.20 USDT / +3.19%

Protection:
SL: 65,020 BE

Targets:
TP_1: 68,000 — filled 50%
TP_2: 71,000 — pending 50%

Orders:
Entry_1: filled
Entry_2: cancelled
SL: active
TP_2: active

Costs:
Fees: 2.10 USDT
Funding: -0.35 USDT

Last events:
14:10 Entry filled
14:25 TP1 filled
14:25 SL moved to BE

Original:
https://t.me/c/3927267771/206
```

### 6.5 `/health`

Stato dettagliato dei worker.

```text
💊 HEALTH
────────────────
Updated: 14:32:10

Workers:
Parser pipeline:     OK — last run 2s ago
Lifecycle gate:      OK — last run 1s ago
Execution worker:    OK — last run 3s ago
Exchange sync:       OK — last run 4s ago
Notification disp.:  OK — last run 5s ago
Timeout worker:      OK — last run 60s ago

DB:
ops.sqlite3: OK
parser.sqlite3: OK

Exchange:
Connected: YES
Last ws event: 4s ago
```

### 6.6 `/control`

Mostra blocchi operativi attivi.

**Nessun blocco:**

```text
🛡️ CONTROL
────────────────
New entries: ENABLED
Open positions: managed
Updates: processed

Active blocks: none

Symbol blacklist:
Global: none
Per trader: none

Commands:
/pause
/block SYMBOL
```

**Pausa globale:**

```text
🛡️ CONTROL
────────────────
New entries: BLOCKED
Open positions: managed
Updates: processed

Active block:
GLOBAL — BLOCK_NEW_ENTRIES
Set by: operator (14:10:33)

Effect:
New signals go to REVIEW_REQUIRED.

Symbol blacklist:
Global: none
Per trader: none

Commands:
/resume
/status
/trades
```

**Blacklist attive:**

```text
🛡️ CONTROL
────────────────
New entries: ENABLED
Open positions: managed
Updates: processed

Active blocks: none

Symbol blacklist:
Global: BTCUSDT, ETHUSDT
Per trader:
  trader_a: SOLUSDT

Commands:
/unblock SYMBOL
/pause
```

**Blocco per trader:**

```text
🛡️ CONTROL
────────────────
New entries: PARTIALLY BLOCKED
Open positions: managed
Updates: processed

Active blocks:
trader_a — BLOCK_NEW_ENTRIES (14:10:33)
trader_b — BLOCK_NEW_ENTRIES (14:15:22)

Symbol blacklist:
Global: none
Per trader: none

Effect:
Signals from trader_a and trader_b go to REVIEW_REQUIRED.

Commands:
/resume trader_a
/resume trader_b
/resume
```

### 6.7 `/reviews`

Casi che richiedono attenzione manuale.

```text
⚠️ REVIEWS — 3 required
────────────────
Updated: 14:32:10

#151 SOL LONG  | missing SL       | action required
#166 BTC SHORT | ambiguous update | parser review
#170 ETH LONG  | order rejected   | exchange review

Use:
/trade #id for details
/control for pause/resume
```

### 6.8 `/pnl`

PnL e ROI aggregati per account attivi.

Risponde alla domanda:

```text
Come sta andando il portafoglio? Quanto ho guadagnato/perso oggi per account?
```

**Account singolo (`account_mode: single`):**

```text
💰 PnL — account: main
────────────────
Updated: 14:32:10

Unrealized:
Total: +128.40 USDT
Long: +114.90 USDT (4 trades)
Short: +13.50 USDT (1 trade)

Realized today:
Closed trades: 3
Gross PnL: +210.50 USDT
Fees: -8.20 USDT
Funding: -1.80 USDT
Net PnL: +200.50 USDT

All time today:
Total net: +328.90 USDT

Exposure:
Capital at risk: 4.80% / 10,000 USDT
Leverage avg: 4.2x
Open trades: 5
────────────────
Use:
/trades for per-trade details
/status for system health
```

**Multi-account (`account_mode: per_trader_subaccount`):**

```text
💰 PnL — 3 accounts
────────────────
Updated: 14:32:10

trader_a — account: sub_01
  Unrealized:  +83.20 USDT (1 trade)
  Realized:    +140.00 USDT
  Fees today:  -3.10 USDT
  Net today:   +220.10 USDT

trader_b — account: sub_02
  Unrealized:  +45.20 USDT (2 trade)
  Realized:    +70.50 USDT
  Fees today:  -2.80 USDT
  Net today:   +112.90 USDT

trader_c — account: sub_03
  Unrealized:  +0.00 USDT (0 trade)
  Realized:    +0.00 USDT
  Net today:   +0.00 USDT

────────────────
Grand Total:
Unrealized: +128.40 USDT
Net today:  +333.00 USDT
────────────────
Use:
/trades for per-trade details
```

**Note sul formato:**

```text
- ROI % non mostrato se allocated_margin non disponibile
- "Realized today" = trade chiusi dalla mezzanotte UTC
- Funding incluso se disponibile dall'exchange
- Sezione "All time today" = unrealized + realized net
```

### 6.9 `/logs [n]`

Ultime N righe del log applicativo filtrate (default: 20).

```text
/logs 10
```

```text
📋 LOGS — last 10
────────────────
14:32:05 [INFO] LifecycleGateWorker: signal #172 accepted
14:32:06 [INFO] ExecutionCommandWorker: command #88 dispatched
14:32:07 [WARN] ExchangeSync: order not found, retry 1/3
14:32:08 [INFO] ExchangeSync: order found on retry
14:32:09 [INFO] LifecycleEventWorker: #145 TP1 filled
14:32:10 [INFO] NotificationDispatcher: sent CLEAN_LOG event
```

### 6.10 `/version`

```text
📦 VERSION
────────────────
Runtime: v2
Commit: a3f9c12
Branch: main
Started: 2026-05-29 08:00:01
Uptime: 6h 32m
```

---

## 7. Command Set Controllo

### 7.1 `/pause` — Blocco globale nuove entry

Crea un controllo attivo in `ops_control_state`:

```text
scope_type = GLOBAL
scope_value = NULL
execution_pause_mode = BLOCK_NEW_ENTRIES
reason = "telegram:/pause"
created_by = "<telegram_user_id>"
active = 1
```

Effetto:

```text
Nuovi SIGNAL → enrichment PASS → LifecycleGateWorker → control_mode=BLOCK_NEW_ENTRIES → REVIEW_REQUIRED
UPDATE su posizioni esistenti → continua normalmente
```

Idempotenza: doppio `/pause` non crea stati incoerenti. Se esiste già un blocco globale, lo mantiene e auditala il comando.

Risposta in COMMANDS:

```text
⏸️ NUOVE ENTRY BLOCCATE
────────────────
Scope: GLOBAL
Mode: BLOCK_NEW_ENTRIES
Set by: operator

Effect:
Nuovi segnali vanno in REVIEW_REQUIRED.
Posizioni aperte, update, lifecycle e
reconciliation restano attivi.

Commands:
/resume
/control
```

### 7.2 `/pause <trader_id>` — Blocco per trader

```text
/pause trader_a
```

Crea un controllo per singolo trader:

```text
scope_type = PER_TRADER
scope_value = "trader_a"
execution_pause_mode = BLOCK_NEW_ENTRIES
```

Risposta in COMMANDS:

```text
⏸️ trader_a — NUOVE ENTRY BLOCCATE
────────────────
Scope: trader_a
Mode: BLOCK_NEW_ENTRIES
Set by: operator

Effect:
Nuovi segnali di trader_a vanno in REVIEW_REQUIRED.
Altri trader non sono influenzati.

Commands:
/resume trader_a
/control
```

### 7.3 `/resume` — Rimozione blocco globale

Non inserisce un nuovo controllo con `NONE`.
Disattiva i controlli attivi globali con `execution_pause_mode = BLOCK_NEW_ENTRIES`.

Idempotenza: se non c'è blocco attivo, risponde senza errore.

Risposta in COMMANDS:

```text
▶️ NUOVE ENTRY RIABILITATE
────────────────
Blocco globale rimosso.

Effect:
Il runtime può nuovamente accettare
nuovi SIGNAL operativi.

Commands:
/control
/status
```

Se nessun blocco attivo:

```text
ℹ️ NESSUN BLOCCO ATTIVO
────────────────
Non esiste un blocco globale sulle nuove entry.

Commands:
/control
```

### 7.4 `/resume <trader_id>` — Rimozione blocco per trader

```text
/resume trader_a
```

Disattiva i controlli attivi per `trader_a`.

Risposta:

```text
▶️ trader_a — NUOVE ENTRY RIABILITATE
────────────────
Blocco su trader_a rimosso.

Effect:
I segnali di trader_a possono nuovamente
creare TradeChain.

Commands:
/control
```

### 7.5 `/start` — Attivazione da standby

Disponibile solo quando `startup.mode: standby`.
Equivalente a `/resume` ma con semantica di attivazione iniziale.

Risposta:

```text
▶️ RUNTIME ATTIVATO
────────────────
Il runtime è ora operativo.
Nuove entry abilitate.

Commands:
/status
/control
```

---

## 8. Command Set Config — Symbol Blacklist

La blacklist è persistente in DB come override su `operation_config.yaml`.
Il runtime al boot merge YAML + override DB.

### 8.1 `/block <symbol>` — Blacklist globale

```text
/block BTCUSDT
```

Aggiunge `BTCUSDT` alla blacklist globale in `ops_config_overrides`.

Effetto: qualsiasi segnale su BTCUSDT viene bloccato indipendentemente dal trader.

Risposta:

```text
🚫 BTCUSDT BLOCCATO
────────────────
Scope: GLOBAL
Effetto: tutti i segnali su BTCUSDT
vanno in REVIEW_REQUIRED.

Blacklist globale attuale:
BTCUSDT, ETHUSDT

Commands:
/unblock BTCUSDT
/control
```

### 8.2 `/block <trader_id> <symbol>` — Blacklist per trader

```text
/block trader_a SOLUSDT
```

Aggiunge `SOLUSDT` alla blacklist del solo `trader_a`.

Risposta:

```text
🚫 trader_a / SOLUSDT BLOCCATO
────────────────
Scope: trader_a
Effetto: segnali di trader_a su SOLUSDT
vanno in REVIEW_REQUIRED.

Blacklist trader_a:
SOLUSDT

Commands:
/unblock trader_a SOLUSDT
/control
```

### 8.3 `/unblock <symbol>` — Rimozione blacklist globale

```text
/unblock BTCUSDT
```

Risposta:

```text
✅ BTCUSDT SBLOCCATO
────────────────
Scope: GLOBAL
BTCUSDT rimosso dalla blacklist globale.

Blacklist globale attuale:
ETHUSDT

Commands:
/control
```

### 8.4 `/unblock <trader_id> <symbol>` — Rimozione per trader

```text
/unblock trader_a SOLUSDT
```

Risposta:

```text
✅ trader_a / SOLUSDT SBLOCCATO
────────────────
Scope: trader_a
SOLUSDT rimosso dalla blacklist di trader_a.

Commands:
/control
```

---

## 9. Command Set Debug

### 9.1 `/debug_on [durata]` — Attiva log verbose

Attiva temporaneamente il Livello 3 di TECH_LOG (decisioni interne, step-by-step).

```text
/debug_on        → attiva per 10 minuti (default)
/debug_on 5m     → attiva per 5 minuti
/debug_on 30m    → attiva per 30 minuti
/debug_on 1h     → attiva per 1 ora
```

Limite massimo configurabile in YAML (default: 60 minuti).

Risposta:

```text
🔍 DEBUG MODE ATTIVATO
────────────────
Durata: 10 minuti
Scadenza: 14:42:10

TECH_LOG mostrerà:
- Decisioni interne lifecycle
- Step enrichment/gate/execution
- Classificazione raw exchange events
- Stato worker ogni 30s

Usa /debug_off per disattivare prima.

Commands:
/debug_off
```

Alla scadenza automatica, TECH_LOG invia:

```text
🔍 DEBUG MODE SCADUTO
────────────────
Debug disattivato automaticamente.
Ritorno a min_level: WARNING.
```

### 9.2 `/debug_off` — Disattiva log verbose

```text
✅ DEBUG MODE DISATTIVATO
────────────────
Ritorno a min_level: WARNING.
```

---

## 10. Regole generali per le risposte comando

```text
- Tutte le risposte vanno nel topic COMMANDS
- Nessuna risposta viene scritta come evento CLEAN_LOG
- Nessuna risposta contiene JSON/debug/traceback
- Nessuna risposta mostra execution command id tecnici
- Nessuna risposta mostra order id exchange lunghi
- Nessuna risposta mostra storico completo lifecycle
```

---

## 11. Audit DB — Comandi

### 11.1 Schema

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

### 11.2 Stati

```text
RECEIVED   → comando ricevuto, in validazione
REJECTED   → rifiutato (utente non autorizzato, comando sconosciuto, payload invalido)
ACCEPTED   → passato validazione, in esecuzione
EXECUTED   → completato con successo
FAILED     → errore durante esecuzione
IGNORED    → messaggio fuori topic o chat, ignorato silenziosamente
```

Ogni comando ricevuto, anche quelli rifiutati, viene sempre auditato.

---

## 12. Package — Collocazione

```text
src/runtime_v2/control_plane/
├── __init__.py
├── config.py                   — carica e valida telegram_control.yaml
├── auth.py                     — validazione ogni update Telegram
├── telegram_bot.py             — inizializza python-telegram-bot, registra handler
├── service.py                  — API applicativa (get_status, pause, resume, block...)
├── status_queries.py           — query read-only su ops.sqlite3
├── topic_router.py             — centralizza scelta topic destinazione
├── notification_dispatcher.py  — consuma ops_notification_outbox
├── audit_store.py              — scrive audit comandi
├── override_store.py           — gestisce ops_config_overrides
├── snapshot_store.py           — gestisce ops_runtime_snapshot
├── models.py
└── formatters/
    ├── status_formatter.py
    ├── trades_formatter.py
    ├── trade_detail_formatter.py
    ├── control_formatter.py
    ├── reviews_formatter.py
    ├── pause_formatter.py
    ├── block_formatter.py
    └── debug_formatter.py
```

---

## 13. Integrazione in `main.py`

```python
control_service = RuntimeControlService(
    ops_db_path=ops_db_path,
    control_repo=control_repo,
    chain_repo=chain_repo,
    command_repo=command_repo,
    lifecycle_event_repo=lifecycle_event_repo,
    exchange_event_repo=exchange_event_repo,
    override_store=override_store,
    snapshot_store=snapshot_store,
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

telegram_control_task = asyncio.create_task(telegram_control_bot.run())
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

## 14. Comandi esclusi dal MVP — Fase futura

I seguenti comandi richiedono semantica più rigida e protezioni aggiuntive.
Non implementare finché non esiste un PRD dedicato.

```text
/forceclose <chain_id>          → genera CLOSE_FULL
/closepartial <chain_id> <pct>  → genera CLOSE_PARTIAL
/cancelpending <chain_id>       → genera CANCEL_PENDING_ENTRY
/movetobe <chain_id>            → genera MOVE_STOP_TO_BREAKEVEN
/syncprotective <chain_id>      → genera SYNC_PROTECTIVE_ORDERS
/halt                           → FULL_STOP (PRD separato richiesto)
/panicclose                     → emergency close (PRD separato richiesto)
```

Regola non negoziabile per tutti i comandi futuri:
nessuno deve mai chiamare direttamente l'adapter exchange.
Il flusso è sempre: Telegram → RuntimeControlService → LifecycleEvent/ExecutionCommand → ops.sqlite3 → worker → exchange.

---

## 15. Acceptance Criteria

```text
1.  Il bot di controllo parte insieme al runtime V2.
2.  Il bot usa un supergruppo privato con topic separati.
3.  I comandi vengono letti solo da COMMANDS.
4.  I messaggi fuori da COMMANDS vengono ignorati.
5.  Gli utenti non autorizzati vengono auditati come REJECTED senza risposta.
6.  /status restituisce dati coerenti da ops.sqlite3.
7.  /trades mostra lista compatta, non duplica /status.
8.  /pause blocca nuove entry senza fermare update e lifecycle.
9.  /pause trader_a blocca solo quel trader.
10. /resume riabilita nuove entry.
11. /resume trader_a riabilita solo quel trader.
12. /block SYMBOL aggiunge alla blacklist globale in DB.
13. /block trader_a SYMBOL aggiunge alla blacklist per trader in DB.
14. /unblock rimuove dalla blacklist.
15. /control mostra blocchi attivi e blacklist.
16. /reviews mostra casi da gestire.
17. /debug_on attiva TECH_LOG verboso per la durata specificata.
18. Startup mode auto/standby/restore funziona come specificato.
19. Shutdown graceful salva snapshot in DB e notifica in TECH_LOG.
20. Ogni comando è auditato in ops_telegram_control_commands.
21. Nessun handler Telegram chiama direttamente adapter exchange.
22. Il listener segnali trader resta separato dal bot di controllo.
```
