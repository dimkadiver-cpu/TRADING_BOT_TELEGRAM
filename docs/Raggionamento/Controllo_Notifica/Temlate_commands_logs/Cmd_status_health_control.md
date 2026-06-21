# Template — /status · /health · /control · /reviews · /help

Comandi esistenti aggiornati con scope `global / account / trader`.

**Scope filtering:**
- `/status` -> supporta `global scope`, `account scope`, `trader scope`
- `/control` -> supporta `global scope`, `account scope`, `trader scope`
- `/reviews` -> supporta `global scope`, `account scope`, `trader scope`
- `/health` -> sempre globale e basato su check reale del runtime

---

## Modello scope

| Scope | Header | Note |
|---|---|---|
| Global scope | `All accounts` | usato nel topic `commands` generale |
| Account scope | `demo_1` | tutti i trader dell'account |
| Trader scope | `demo_1 · trader_a` | vista ristretta a un trader |

### Regole

- nel topic `commands` generale, `/status`, `/control`, `/reviews` devono poter mostrare `All accounts`
- nei topic scoped, i comandi mantengono la forma mono-account o mono-trader
- `/health` non eredita lo scope del topic: resta globale perche' descrive lo stato reale del processo
- CTA e riferimenti a `/trades`, `/trade n`, `/dashboard` seguono i nuovi documenti funzionali

---

## /status

### Caso normale — 🟢

```text
🟢 Runtime V2 — STATUS  |  demo_1
————————————————
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

### Warning — 🟡

```text
🟡 Runtime V2 — STATUS  |  demo_1
————————————————
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

### Critico — 🔴

```text
🔴 Runtime V2 — STATUS  |  demo_1
————————————————
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

### Global scope — All accounts

```text
🟡 Runtime V2 — STATUS  |  All accounts
————————————————
Updated: 14:32:05

Mode:
  New entries: ENABLED
  Control: MIXED
  Sync: mixed  (latest 12s ago, worst 87s ago)  ⚠️

Trades:
  Open: 7
  Waiting entry: 4
  Partial: 2
  Review required: 2  ⚠️

Execution:
  Pending commands: 1
  Failed commands: 3  🔴

Risk:
  No SL: 2  🔴

By account:
  demo_2  Open: 3  Waiting: 1  Failed: 1
  demo_1  Open: 2  Waiting: 2  Failed: 0
  demo_3  Open: 2  Waiting: 1  Failed: 2

/trades  ·  /reviews  ·  /control
```

### Trader scope

```text
🟢 Runtime V2 — STATUS  |  demo_1 · trader_a
————————————————
Updated: 14:32:05

Mode:
  New entries: ENABLED
  Control: NONE
  Sync: 12s ago

Trades:
  Open: 1
  Waiting entry: 1
  Partial: 0
  Review required: 0

Execution:
  Pending commands: 0
  Failed commands: 0

Risk:
  No SL: 0

/trades trader_a  ·  /reviews  ·  /control
```

### Regole `/status`

- l'header deve sempre rendere esplicito lo scope
- in global scope i valori top-level sono aggregati sul perimetro filtrato
- `By account` compare quando ci sono almeno 2 account nel filtro
- `Control: MIXED` quando account o trader hanno stati diversi
- `Sync: mixed` quando le latenze non sono uniformi

---

## /health

### Principio fondamentale

`/health` non deve essere un semplice riepilogo dell'ultimo stato noto.
Deve fare un **check reale** del runtime al momento della richiesta.

Questo significa che il comando deve verificare realmente, per quanto possibile:

- worker attesi vivi;
- freshness reale di heartbeat o ultimo evento;
- raggiungibilita' DB;
- raggiungibilita' exchange adapter / sync layer;
- backlog o stallo operativo;
- coerenza minima dei componenti critici.

In pratica:

```text
/health
-> non dice solo cosa risultava l'ultima volta
-> prova a misurare cosa e' vivo adesso
```

### Caso normale

```text
🩺 HEALTH  |  Global runtime
————————————————
Updated: 14:32:05

Workers:
  Parser pipeline     OK
  Lifecycle gate      OK
  Execution worker    OK
  Exchange sync       OK
  Notification disp.  OK

DB: OK
Exchange: connected
Checks: live probe passed
```

### Caso warning / probe degradata

```text
🩺 HEALTH  |  Global runtime
————————————————
Updated: 14:32:05

Workers:
  Parser pipeline     OK
  Lifecycle gate      OK
  Execution worker    OK
  Exchange sync       WARNING  (last event 87s ago)
  Notification disp.  OK

DB: OK
Exchange: degraded
Checks: live probe partial

Warnings:
  - exchange sync stale
  - last account snapshot older than threshold
```

### Caso critico

```text
🩺 HEALTH  |  Global runtime
————————————————
Updated: 14:32:05

Workers:
  Parser pipeline     OK
  Lifecycle gate      FAILED
  Execution worker    OK
  Exchange sync       FAILED
  Notification disp.  OK

DB: OK
Exchange: disconnected
Checks: live probe failed

Critical:
  - lifecycle gate heartbeat missing
  - exchange connectivity probe failed
  - command backlog above threshold
```

### Regole `/health`

- sempre globale
- l'header non deve mostrare account specifico
- `Checks:` deve dichiarare l'esito della probe reale
- `Workers` deve riflettere heartbeat o segnali vivi, non solo stato configurato
- warning e critical devono essere motivati
- se una probe non e' eseguibile, va mostrato `unknown`, non `OK`

---

## /control

### Nessun blocco attivo

```text
🔓 CONTROL  |  demo_1
————————————————
New entries: ENABLED

Nessun blocco attivo.

Blacklist globale: —
Blacklist per trader: —

/pause  ·  /block <symbol>
```

### Con blocchi attivi

```text
🔒 CONTROL  |  demo_1
————————————————
New entries: BLOCKED

Blocchi attivi:
  GLOBAL    BLOCK_NEW_ENTRIES  (14:10:22)
  trader_a  BLOCK_NEW_ENTRIES  (14:15:01)

Blacklist globale:
  BTCUSDT  ETHUSDT

Blacklist per trader:
  trader_b: SOLUSDT  BNBUSDT

/resume  ·  /unblock <symbol>
```

### Global scope — All accounts

```text
🔒 CONTROL  |  All accounts
————————————————
New entries: MIXED

Blocchi attivi:
  demo_2  GLOBAL    BLOCK_NEW_ENTRIES  (14:10:22)
  demo_2  trader_a  BLOCK_NEW_ENTRIES  (14:15:01)
  demo_3  trader_b  BLOCK_SYMBOL       SOLUSDT

Blacklist globale:
  demo_2: BTCUSDT  ETHUSDT

Blacklist per trader:
  demo_3 / trader_b: SOLUSDT  BNBUSDT

/resume  ·  /unblock <symbol>
```

### Regole `/control`

- `New entries: MIXED` se il perimetro contiene stati diversi
- in global scope ogni blocco deve mostrare `account` e, se presente, `trader`
- la blacklist non deve essere ambigua tra account

---

## /reviews

### Con casi aperti

```text
⚠️ REVIEWS  |  demo_1
————————————————
Updated: 14:32:05
Casi aperti: 2

#7   ETHUSDT   missing_sl
#12  SOLUSDT   capability_not_supported

/trade #id  per dettaglio
```

### Nessun caso

```text
✅ REVIEWS  |  demo_1
————————————————
Updated: 14:32:05

Nessun caso in review.
```

### Global scope — All accounts

```text
⚠️ REVIEWS  |  All accounts
————————————————
Updated: 14:32:05
Casi aperti: 4

#7   ETHUSDT   missing_sl
     Trader: trader_devos_crypto · Account: demo_2

#12  SOLUSDT   capability_not_supported
     Trader: trader_beta · Account: demo_3

#44  XRPUSDT   review_required
     Trader: trader_alpha · Account: demo_1

/trade #id  per dettaglio
```

### Regole `/reviews`

- ordinamento consigliato: `updated desc`
- in global scope ogni caso deve rendere espliciti `Trader` e `Account`
- CTA primaria: `/trade #id`

---

## /help

```text
COMANDI DISPONIBILI
————————————————
Informativi:
/status              - salute bot e conteggi
/trades [trader]     - trade sintetici nel perimetro corrente
/trade #id           - dettaglio audit completo della chain
/stats [trader]      - statistiche oggi/7d/30d/totale
/pnl [trader]        - PnL realizzato + snapshot account
/health              - check reale dello stato runtime
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

### Note `/help`

- nel topic `commands` generale i comandi read-only possono operare in `All accounts`
- nei topic scoped il filtro implicito restringe il perimetro
- `/health` resta globale e deve fare un check reale del runtime
