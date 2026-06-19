# Template — /status · /health · /control · /reviews · /help

Comandi esistenti — aggiornati con scope account.

**Scope filtering:**
- `/status` → filtrato per `account_id` (dati di quell'account)
- `/control` → filtrato per `account_id` (blocchi di quell'account)
- `/health` → **globale** — i worker sono unici per processo, non per account

---

## /status

### Caso normale — 🟢

```
🟢 Runtime V2 — STATUS  |  demo_1
────────────────
Updated: 14:32:05

Mode:
  New entries: ENABLED
  Control: NONE
  Sync: 12s ago

Trades:
  Open: 3
  Waiting entry: 2
  Partial: 1
  Review required: 0

Execution:
  Pending commands: 0
  Failed commands: 0

Risk:
  No SL: 0

/trades  ·  /reviews  ·  /control
```

### Warning — 🟡 (review aperte o sync stale)

```
🟡 Runtime V2 — STATUS  |  demo_1
────────────────
Updated: 14:32:05

Mode:
  New entries: ENABLED
  Control: NONE
  Sync: 87s ago  ⚠️

Trades:
  Open: 3
  Waiting entry: 2
  Partial: 1
  Review required: 2  ⚠️

Execution:
  Pending commands: 0
  Failed commands: 0

Risk:
  No SL: 0

/trades  ·  /reviews  ·  /control
```

### Critico — 🔴 (failed commands o no SL)

```
🔴 Runtime V2 — STATUS  |  demo_1
────────────────
Updated: 14:32:05

Mode:
  New entries: ENABLED
  Control: BLOCK_NEW_ENTRIES
  Sync: 12s ago

Trades:
  Open: 5
  Waiting entry: 0
  Partial: 2
  Review required: 0

Execution:
  Pending commands: 0
  Failed commands: 3  🔴

Risk:
  No SL: 2  🔴

/trades  ·  /reviews  ·  /control
```

> `|  demo_1` aggiunto nell'header per identificare l'account.
> Rimane invariato il resto della struttura.

---

## /health

```
🩺 HEALTH  |  demo_1
────────────────
Updated: 14:32:05

Workers:
  Parser pipeline     OK
  Lifecycle gate      OK
  Execution worker    OK
  Exchange sync       WARNING  (last event 87s ago)
  Notification disp.  OK

DB: OK
Exchange: connected
```

---

## /control

### Nessun blocco attivo

```
🔓 CONTROL  |  demo_1
────────────────
New entries: ENABLED

Nessun blocco attivo.

Blacklist globale: —
Blacklist per trader: —

/pause  ·  /block <symbol>
```

### Con blocchi attivi

```
🔒 CONTROL  |  demo_1
────────────────
New entries: BLOCKED

Blocchi attivi:
  GLOBAL  BLOCK_NEW_ENTRIES  (14:10:22)
  trader_a  BLOCK_NEW_ENTRIES  (14:15:01)

Blacklist globale:
  BTCUSDT  ETHUSDT

Blacklist per trader:
  trader_b: SOLUSDT  BNBUSDT

/resume  ·  /unblock <symbol>
```

---

## /reviews

### Con casi aperti

```
⚠️ REVIEWS  |  demo_1
────────────────
Updated: 14:32:05
Casi aperti: 2

#7   ETHUSDT   missing_sl
#12  SOLUSDT   capability_not_supported

/trade #id  per dettaglio
```

### Nessun caso

```
✅ REVIEWS  |  demo_1
────────────────
Updated: 14:32:05

Nessun caso in review.
```

---

## /help (aggiornato)

```
COMANDI DISPONIBILI
────────────────
Informativi:
/status              - salute bot e conteggi
/trades [trader]     - trade aperti con PnL snapshot
/trade #id           - dettaglio singola chain
/stats [trader]      - statistiche oggi/7d/30d/totale
/pnl [trader]        - PnL realizzato + snapshot account
/health              - stato workers
/control             - blocchi operativi
/reviews             - casi da controllare
/logs [n]            - ultime N righe log (default: 20)
/debug_on [dur] / /debug_off
/version             - versione runtime
/dashboard           - crea dashboard inline pinnabile
/help                - questo messaggio

Controllo:
/pause [trader]
/resume [trader]
/start
/block <symbol>
/block <trader> <symbol>
/unblock <symbol>
/unblock <trader> <symbol>

Emergenza (richiede conferma):
/close_all [trader]        - chiude tutte le posizioni
/close [trader] <symbol>   - chiude singola posizione
/cancel_all [trader]       - cancella ordini entry in attesa
```
