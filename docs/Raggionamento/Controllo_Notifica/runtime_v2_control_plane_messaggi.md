# Runtime V2 Control Plane — Messaggi Telegram (riferimento completo)

Aggiornato al codice corrente in `src/runtime_v2/control_plane/`.

---

## 1. Architettura messaggi

```
COMMANDS_REPLY   → risposte ai comandi Telegram: /status, /trades, /trade, ...
CLEAN_LOG        → log operativi: segnale, entry, TP, SL, update, ...
TECH_LOG         → log tecnici: startup, warning, errori interni, ...
```

In `supergroup_topics` ogni destinazione va nel proprio topic.
In `private_bot` tutto va nella stessa chat, i TECH_LOG vengono prefissati con `⚠️ --SYSTEM--`.

---

## 2. CLEAN_LOG — mappa eventi

| Evento lifecycle | Notification type |
|---|---|
| `SIGNAL_ACCEPTED` | `SIGNAL_ACCEPTED` |
| `SIGNAL_REJECTED` | `SIGNAL_REJECTED` |
| `REVIEW_REQUIRED` | `REVIEW_REQUIRED` |
| `ENTRY_FILLED` | `ENTRY_OPENED` |
| `TP_FILLED` | `TP_FILLED` / `TP_FILLED_FINAL` (se `is_final=True`) |
| `SL_FILLED` | `SL_FILLED` |
| `CLOSE_FULL_FILLED` | `POSITION_CLOSED` oppure `BE_EXIT` (se catena PROTECTED) |
| `ENTRY_UPDATED` | `ENTRY_UPDATED` |
| `PENDING_TIMEOUT` | `PENDING_ENTRY_EXPIRED` |
| `PENDING_ENTRY_CANCELLED` | `ENTRY_CANCELLED` (filtrato se `cancel_reason=position_closed`) |
| `ENTRY_CANCEL_FAILED` | `CANCEL_FAILED` |
| `RECONCILIATION_WARNING` | `RECONCILIATION_WARNING` |
| `RECONCILIATION_FIXED` | `RECONCILIATION_FIXED` |
| `REENTRY_ACCEPTED` | `REENTRY_ACCEPTED` |
| `UPDATE_DONE` | `UPDATE_DONE` |
| `UPDATE_PARTIAL` | `UPDATE_PARTIAL` |
| `UPDATE_REJECTED` | `UPDATE_REJECTED` |
| `MULTI_CHAIN_SUMMARY` / `MULTI_CHAIN_UPDATE` / `MULTI_CHAIN_CLOSED` | `MULTI_CHAIN_SUMMARY` |

Nota: il separatore `- - -` si adatta alla larghezza del contenuto. Esempi qui sotto usano una lunghezza rappresentativa.

---

## 3. CLEAN_LOG — esempi visuali

### 3.1 SIGNAL_ACCEPTED

Emesso quando il segnale passa il gate ed è accettato per l'esecuzione.

```
✅ #12 — SIGNAL ACCEPTED
- - - - - - - - - - - - -
BTCUSDT — 📈 LONG
- - - - - - - - - - - - -
Entry_1: Market ~68,500
Entry_2: 67,200 Limit
SL: 66,400
TP_1: 69,200
TP_2: 70,500
Risk: 0.5%
- - - - - - - - - - - - -
Trader: Pipsygnal
Exchange Account: main
Source: original_message
- - - - - - - - - - - - -
https://t.me/c/123456/987
```

Variante con entry MARKET senza prezzo indicativo:
```
Entry_1: Market
```

Variante con entry LIMIT senza prezzo (dati assenti):
```
Entry_1: Limit
```

---

### 3.2 SIGNAL_REJECTED

Emesso quando il segnale non supera il gate (risk, blacklist, regole).

```
❌ #13 — SIGNAL REJECTED
- - - - - - - - - - - - -
ETHUSDT — 📉 SHORT
- - - - - - - - - - - - -
Entry_1: 3,820 Limit
SL: 3,910
- - - - - - - - - - - - -
Trader: TraderA
Exchange Account: main
Rejected: risk_exceeds_limit
Source: original_message
- - - - - - - - - - - - -
https://t.me/c/123456/987
```

Note:
- `Rejected:` mostra il `reason` dal payload dell'evento.
- TPs e Risk non compaiono (non sempre presenti al momento del reject).

---

### 3.3 REVIEW_REQUIRED

Emesso quando il segnale viene messo in review manuale (pause attiva, anomalia, etc.).
Struttura identica a SIGNAL_ACCEPTED/REJECTED con entries, SL, TPs, Risk.

```
⚠️ #14 — REVIEW REQUIRED
- - - - - - - - - - - - -
SOLUSDT — 📈 LONG
- - - - - - - - - - - - -
Entry_1: Market ~68,500
Entry_2: 67,200 Limit
SL: 66,400
TP_1: 69,200
TP_2: 70,500
Risk: 0.5%
- - - - - - - - - - - - -
Trader: TraderA
Exchange Account: main
Rejected: pause_active
Source: runtime
- - - - - - - - - - - - -
https://t.me/c/123456/987
```

Note:
- `Rejected:` mostra il motivo della review (es. `pause_active`, `risk_exceeds_limit`).
- Se TPs o Risk non sono nel piano, quelle righe non appaiono.

---

### 3.4 ENTRY_OPENED

Emesso al fill della prima leg di entrata.

```
📊 #12 — ENTRY OPENED
- - - - - - - - - - - - - - - -
BTCUSDT — 📈 LONG
https://t.me/c/123456/987
- - - - - - - - - - - - - - - -
Entry_1 - Filled
Price: 68,500
Qty: 0.015
Fee: 0.56 USDT
Fee rate: 0.055%
Value: 1,027.50 USDT

Position:
Avg entry: 68,500
Pending: Entry_2 67,200 Limit
- - - - - - - - - - - - - - - -
Source: exchange
```

Variante senza entry pending:
```
Pending: none
```

---

### 3.5 ENTRY_UPDATED

Emesso al fill di una leg successiva (averaging, TWO_STEP, LADDER).

```
✏️ #12 — ENTRY UPDATED
- - - - - - - - - - - - - - - -
BTCUSDT — 📈 LONG
https://t.me/c/123456/987
- - - - - - - - - - - - - - - -
Entry_2 - Filled
Price: 67,200
Qty: 0.010
Fee: 0.37 USDT
Fee rate: 0.055%
Value: 672.00 USDT

Position:
Avg entry: 67,980
Pending: none
- - - - - - - - - - - - - - - -
Source: exchange
```

---

### 3.6 ENTRY_CANCELLED

Emesso quando un ordine pending viene cancellato (timeout o comando).
Non mostrato se `cancel_reason = position_closed`.

```
⚠️ #12 — ENTRY CANCELLED
- - - - - - - - - - - - - - - -
BTCUSDT — 📈 LONG
https://t.me/c/123456/987
- - - - - - - - - - - - - - - -
Entry_2: 67,200 Limit
Partial fill: 40% (0.010 BTCUSDT kept)
Avg entry: 67,980
Total filled: 0.025 BTCUSDT
- - - - - - - - - - - - - - - -
Source: timeout_worker
```

Variante senza partial fill (cancellato a zero):
```
Entry_2: 67,200 Limit
```

---

### 3.7 CANCEL_FAILED

Emesso se la cancellazione di un ordine pending fallisce dopo 3 tentativi. Richiede intervento manuale.

```
🚨 #12 — CANCEL FAILED
- - - - - - - - - - - - - - - - - - - - - -
BTCUSDT — 📈 LONG
https://t.me/c/123456/987
- - - - - - - - - - - - - - - - - - - - - -
Cancellation of Entry_2 failed after 3 attempts.
Requires manual review required to resolve the position.
Entry price: 67,200
- - - - - - - - - - - - - - - - - - - - - -
Source: timeout_worker
```

---

### 3.8 TP_FILLED — parziale

Emesso ad ogni TP intermedio. Non mostra SL corrente né percentuale posizione residua.

```
📊 #12 — TP1 FILLED
- - - - - - - - - - - - - - - -
BTCUSDT — 📈 LONG
https://t.me/c/123456/987
- - - - - - - - - - - - - - - -
TP_1: 69,200
Closed: 50%
PnL: +17.50 USDT
Fee: 0.42 USDT
Fee rate: 0.055%
Value: 760.00 USDT

- - - - - - - - - - - - - - - -
Source: exchange
```

Note: `Fee rate` e `Value` compaiono solo se presenti nel payload (path WS).

---

### 3.9 TP_FILLED_FINAL

Emesso all'ultimo TP (chiusura completa via TP). Mostra Final Result.

```
✅ #12 — POSITION CLOSED
- - - - - - - - - - - - - - - -
BTCUSDT — 📈 LONG
https://t.me/c/123456/987
- - - - - - - - - - - - - - - -
TP_2: 70,500
Closed: 100%
PnL: +45.20 USDT
Fee: 1.03 USDT
Fee rate: 0.055%
Value: 1,234.50 USDT

Close reason: FINAL TP FILLED
- - - - - - - - - - - - - - - -
Final Result:
ROI net: +3.67%
Total PnL net: +44.17 USDT
Gross PnL: +45.20 USDT
Fees: -2.06 USDT
Funding: +0.03 USDT
- - - - - - - - - - - - - - - -
Source: exchange
```

Note: `ROI net` appare solo se `roi_net_pct` è nel payload.

---

### 3.10 SL_FILLED

Emesso quando lo stop loss viene colpito.

```
🛑 #12 — POSITION CLOSED
- - - - - - - - - - - - - - - -
BTCUSDT — 📈 LONG
https://t.me/c/123456/987
- - - - - - - - - - - - - - - -
SL: 66,400
Closed: 100%
PnL: -32.40 USDT
Fee: 0.91 USDT

Close reason: STOP_LOSS
- - - - - - - - - - - - - - - -
Final Result:
ROI net: -2.81%
Total PnL net: -33.31 USDT
Gross PnL: -32.40 USDT
Fees: -0.91 USDT
Funding: +0.00 USDT
- - - - - - - - - - - - - - - -
Source: exchange
```

---

### 3.11 POSITION_CLOSED — da comando bot (U_CLOSE_FULL)

Emesso quando la chiusura arriva come conferma di un ordine piazzato dal bot su comando esplicito.

```
✋ #12 — POSITION CLOSED
- - - - - - - - - - - - - - - -
BTCUSDT — 📈 LONG
https://t.me/c/123456/987
- - - - - - - - - - - - - - - -
Price: 68,920
PnL: +8.30 USDT
Fee: 0.48 USDT

Close reason: BOT_COMMAND
- - - - - - - - - - - - - - - -
Final Result:
ROI net: +0.61%
Total PnL net: +7.82 USDT
Gross PnL: +8.30 USDT
Fees: -0.48 USDT
Funding: +0.00 USDT
- - - - - - - - - - - - - - - -
Source: bot_command
```

---

### 3.12 POSITION_CLOSED — chiusura esterna rilevata da riconciliazione

Emessa quando la riconciliazione rileva una posizione già chiusa sull'exchange (chiusura manuale o liquidazione fuori dal bot).

```
✋ #12 — POSITION CLOSED
- - - - - - - - - - - - - - - - - - - - -
BTCUSDT — 📈 LONG
https://t.me/c/123456/987
- - - - - - - - - - - - - - - - - - - - -
Price: 68,500
PnL: +3.20 USDT
Fee: 0.38 USDT

Close reason: MANUAL_CLOSE
- - - - - - - - - - - - - - - - - - - - -
Final Result:
...
- - - - - - - - - - - - - - - - - - - - -
Source: position_reconciliation
```

Tabella distinzione chiusure:

| Scenario | `Close reason` | `Source` |
|---|---|---|
| TP finale | `FINAL TP FILLED` | `exchange` |
| Stop loss | `STOP_LOSS` | `exchange` |
| Comando bot (`U_CLOSE_FULL`) | `BOT_COMMAND` | `bot_command` |
| Chiusura manuale/esterna | `MANUAL_CLOSE` | `position_reconciliation` |

---

### 3.13 BE_EXIT

Emesso al posto di POSITION_CLOSED quando la catena è in stato `PROTECTED` (breakeven attivo) e arriva un `CLOSE_FULL_FILLED`.

```
⚡ #12 — BE EXIT
- - - - - - - - - - - - - - - -
BTCUSDT — 📈 LONG
https://t.me/c/123456/987
- - - - - - - - - - - - - - - -
Exit: 68,500 BE
PnL: +0.20 USDT
Fee: 0.39 USDT

Close reason: BREAKEVEN_AFTER_TP
- - - - - - - - - - - - - - - -
Final Result:
ROI net: -0.02%
Total PnL net: -0.19 USDT
Gross PnL: +0.20 USDT
Fees: -0.39 USDT
Funding: +0.00 USDT
- - - - - - - - - - - - - - - -
Source: exchange
```

---

### 3.14 UPDATE_DONE

Emesso quando un update viene applicato con successo a una singola catena.

```
✅ #12 — UPDATE DONE
- - - - - - - - - - - - - - - -
BTCUSDT — 📈 LONG
https://t.me/c/123456/987
- - - - - - - - - - - - - - - -
Operation:
▪️ move_sl_to_be
▪️ cancel_pending_entry

Changed:
SL: 66,400 -> 68,500
- - - - - - - - - - - - - - - -
Source: runtime
```

Variante con nota su un campo:
```
Changed:
SL: 66,400 -> 68,500 *
* adjusted to entry avg price
```

Variante senza operation né changed (update vuoto applicato):
```
✅ #12 — UPDATE DONE
- - - - - - -
BTCUSDT — 📈 LONG
https://t.me/c/123456/987
- - - - - - -
Source: runtime
```

---

### 3.15 UPDATE_PARTIAL

Emesso quando alcune azioni dell'update sono state applicate e altre rifiutate.

```
⚠️ #12 — UPDATE PARTIAL
- - - - - - - - - - - - - - - -
BTCUSDT — 📈 LONG
https://t.me/c/123456/987
- - - - - - - - - - - - - - - -
Applied:
  • move_sl_to_be

Rejected:
  • cancel_entry_2: exchange_order_not_found
- - - - - - - - - - - - - - - -
Source: runtime
```

---

### 3.16 UPDATE_REJECTED

Emesso quando l'update viene rifiutato integralmente (catena non aperta, stato incompatibile).

```
❌ #12 — UPDATE REJECTED
- - - - - - - - - - - - - - - -
BTCUSDT — 📈 LONG
https://t.me/c/123456/987
- - - - - - - - - - - - - - - -
Reason: chain_not_open
- - - - - - - - - - - - - - - -
Source: runtime
```

---

### 3.17 PENDING_ENTRY_EXPIRED

Emesso quando un ordine pending scade senza essere eseguito (timeout).

```
⏰ #12 — PENDING ENTRY EXPIRED
- - - - - - - - - - - - - - - -
BTCUSDT — 📈 LONG
https://t.me/c/123456/987
- - - - - - - - - - - - - - - -
Timeout: order expired before fill
- - - - - - - - - - - - - - - -
Source: worker
```

---

### 3.18 RECONCILIATION_WARNING

Emesso quando la riconciliazione rileva una discrepanza tra stato locale ed exchange.

```
⚠️ #12 — RECONCILIATION WARNING
- - - - - - - - - - - - - - - -
BTCUSDT — 📈 LONG
https://t.me/c/123456/987
- - - - - - - - - - - - - - - -
Issue: exchange_position_qty_mismatch
Risk: local_state_stale
Action: refresh_position_and_rebuild_orders
- - - - - - - - - - - - - - - -
Source: runtime
```

---

### 3.19 RECONCILIATION_FIXED

Emesso quando il problema rilevato in RECONCILIATION_WARNING è stato risolto automaticamente.

```
✅ #12 — RECONCILIATION FIXED
- - - - - - - - - - - - - - - -
BTCUSDT — 📈 LONG
https://t.me/c/123456/987
- - - - - - - - - - - - - - - -
Issue resolved: exchange_position_qty_mismatch
- - - - - - - - - - - - - - - -
Source: runtime
```

---

### 3.20 REENTRY_ACCEPTED

Emesso quando un re-entry su una catena chiusa viene accettato come nuova catena.

```
🔄 #18 — REENTRY ACCEPTED
- - - - - - - - - -
BTCUSDT — 📈 LONG
- - - - - - - - - -
Previous chain: #12
- - - - - - - - - -
Source: runtime
```

---

### 3.21 MULTI_CHAIN_SUMMARY / MULTI_CHAIN_UPDATE / MULTI_CHAIN_CLOSED

Emesso quando un update impatta più catene contemporaneamente. Stesso formatter per tutti e tre i tipi.

Variante con problemi (almeno un PARTIAL o SKIPPED → emoji ⚠️):

```
⚠️ UPDATE APPLICATO - 4 chain
- - - - - - - - - - - - - - - - - - - - - - - - -
Operation:
▪️ move_sl_to_be
▪️ cancel_pending_entries
- - - - - - - - - - - - - - - - - - - - - - - - -
ID  | Symbol  | Side  | State   | link
- - - - - - - - - - - - - - - - - - - - - - - - -
#12 | BTCUSDT | LONG  | DONE    | https://t.me/c/123456/1001
#13 | ETHUSDT | SHORT | DONE    | https://t.me/c/123456/1002
#14 | SOLUSDT | LONG  | PARTIAL | https://t.me/c/123456/1003
#15 | XRPUSDT | LONG  | SKIPPED | https://t.me/c/123456/1004
- - - - - - - - - - - - - - - - - - - - - - - - -
Done: 2   Partial: 1   Skipped: 1
- - - - - - - - - - - - - - - - - - - - - - - - -
Source: runtime
```

Variante tutto OK (tutti DONE → emoji ✅):

```
✅ UPDATE APPLICATO - 3 chain
- - - - - - - - - - - - - - - - - - - - - - -
Operation:
▪️ move_sl_to_be
- - - - - - - - - - - - - - - - - - - - - - -
ID  | Symbol  | Side  | State | link
- - - - - - - - - - - - - - - - - - - - - - -
#12 | BTCUSDT | LONG  | DONE  | https://t.me/c/123456/1001
#13 | ETHUSDT | SHORT | DONE  | https://t.me/c/123456/1002
#14 | SOLUSDT | LONG  | DONE  | https://t.me/c/123456/1003
- - - - - - - - - - - - - - - - - - - - - - -
Done: 3
- - - - - - - - - - - - - - - - - - - - - - -
Source: runtime
```

Note:
- Le colonne si adattano alla larghezza massima dei valori presenti.
- `link` è assente se non ci sono messaggi precedenti tracciati in `ops_clean_log_tracking`.
- `Partial:` e `Skipped:` nel summary appaiono solo se > 0.

---

## 4. TECH_LOG — messaggi tecnici

Formato base:
```
[LEVEL] Category: Title
────────────────
Description

Context:
key: value

Action: ...
────────────────
Source: ...
```

In modalità `private_bot` viene preposta la riga `⚠️ --SYSTEM--`.

### 4.1 Startup

```
[INFO] Runtime: startup
────────────────
Runtime avviato

Context:
started_at: 2026-06-03 09:42:10 UTC
────────────────
Source: runtime_main
```

### 4.2 Shutdown

```
[INFO] Runtime: shutdown
────────────────
Runtime shutdown — SIGTERM

Context:
reason: SIGTERM
open_chains: 2
pending_commands: 0
────────────────
Source: runtime_main
```

### 4.3 Warning generico

```
[WARNING] Exchange: websocket_reconnect
────────────────
watchMyTrades disconnected, reconnect scheduled.

Context:
symbol: BTCUSDT
attempt: 2
last_error: timeout

Action: fallback to polling until WS restored
────────────────
Source: ccxt_ws_worker
```

### 4.4 Rate limit TECH_LOG

Messaggio speciale inviato direttamente dal dispatcher (non passa dall'outbox):

```
[WARN] TECH_LOG: Rate limit raggiunto
────────────────
Troppi messaggi in TECH_LOG (>20/min).
Alcuni messaggi soppressi temporaneamente.

Controlla il log file per il dettaglio completo.
────────────────
Source: notification_dispatcher
```

Policy TECH_LOG:
- Soppressi se `enabled=false`.
- Soppressi se `level=DEBUG` e debug mode non attivo.
- Soppressi se `level=INFO` e `operational_events=false`.
- Soppressi se sotto `min_level` configurato.
- Rate limit: max 20 messaggi/minuto (configurabile).

---

## 5. COMMANDS_REPLY — risposte ai comandi

### 5.1 /help

```
COMANDI DISPONIBILI
────────────────
Informativi:
/status    - salute bot e conteggi
/trades    - trade aperti
/trade #id - dettaglio singola chain
/health    - stato workers
/control   - blocchi operativi
/reviews   - casi da controllare
/pnl       - ultimo snapshot account persistito
/logs [n]  - ultime N righe log (default: 20)
/debug_on [5m|30m|1h]
/debug_off
/version   - versione runtime
/help      - questo messaggio

Controllo:
/pause [trader]
/resume [trader]
/start
/block <symbol>
/block <trader> <symbol>
/unblock <symbol>
/unblock <trader> <symbol>
```

---

### 5.2 /status

```
🟢 Runtime V2 — STATUS
────────────────
Updated: 09:44:12

Mode:
New entries: ENABLED
Control: AUTO
Sync: 4s ago

Trades:
Open: 2
Waiting entry: 1
Partial: 1
Review required: 0

Execution:
Pending commands: 0
Failed commands: 0

Risk:
No SL: 0

Use:
/trades
/reviews
/control
```

Logica emoji stato:
- 🔴 se `failed_commands > 0` o `no_sl_count > 0`
- 🟡 se `review_count > 0` o sync stale (> 30s)
- 🟢 altrimenti

`Control` mostra il modo del blocco globale attivo: `AUTO`, `BLOCK_NEW_ENTRIES`, `FULL_STOP`.

---

### 5.3 /trades

```
📊 OPEN TRADES — 4 active
────────────────
Updated: 09:44:12

ID | Symbol   | Side  | State            | Protection
- - - - - - - - - - - - - - - - - - - - - - - - - -
12 | BTCUSDT  | LONG  | OPEN             | SL: set
13 | ETHUSDT  | SHORT | PARTIALLY_CLOSED | SL: set
14 | SOLUSDT  | LONG  | WAITING_ENTRY    | NoSL
15 | DOGEUSDT | LONG  | WAITING_ENTRY    | BE: set

────────────────
Use:
/trade #id for details
/reviews for blocked cases
```

Logica colonna `Protection`:
- `BE: set` se `be_protection_status = "PROTECTED"`
- `SL: set` se SL presente (e non BE)
- `NoSL` se nessuna protezione

Variante vuota:
```
📊 OPEN TRADES — 0 active
────────────────
Updated: 09:44:12

No open trades.

────────────────
Use:
/trade #id for details
/reviews for blocked cases
```

---

### 5.4 /trade #id

```
📌 TRADE #12
- - - - - - - - - - - - -
BTCUSDT - 📈 LONG
Trader: Pipsygnal
Exchange Account: main

Position:
Avg entry: 68,500
State: OPEN

Protection:
SL: 66,400

Last events:
09:40:11 ENTRY_FILLED
09:51:02 TP_FILLED

- - - - - - - - - - - - -
Use:
https://t.me/c/123456/987
```

Variante senza SL:
```
Protection:
SL: none
```

Variante senza eventi registrati: sezione `Last events:` assente.

Trade non trovato:
```
Trade not found.
```

---

### 5.5 /health

```
💊 HEALTH
────────────────
Updated: 09:44:12

Workers:
lifecycle_gate: OK
exchange_event_processor: OK
exchange_sync: OK
notification_dispatcher: OK
command_worker: OK

DB:
ops.sqlite3: OK

Exchange:
Connected: YES
```

Variante con problema worker:
```
exchange_sync: ERROR — connection timeout
```

---

### 5.6 /control

```
🛡️ CONTROL
────────────────
New entries: BLOCKED
Open positions: managed
Updates: processed

Active blocks:
GLOBAL — BLOCK_NEW_ENTRIES (2026-06-03T09:30:00Z)
TraderA — BLOCK_NEW_ENTRIES (2026-06-03T09:35:00Z)

Symbol blacklist:
Global: DOGEUSDT, XRPUSDT
Per trader:
  TraderA: SOLUSDT
```

Variante senza blocchi:
```
Active blocks: none

Symbol blacklist:
Global: none
Per trader: none
```

---

### 5.7 /reviews

```
⚠️ REVIEWS — 2 required
────────────────
Updated: 09:44:12

#14 SOLUSDT | missing_stop_loss
#15 XRPUSDT | risk_exceeds_limit

Use:
/trade #id for details
/control for pause/resume
```

Variante vuota:
```
⚠️ REVIEWS — 0 required
────────────────
Updated: 09:44:12

No reviews pending.

Use:
/trade #id for details
/control for pause/resume
```

---

### 5.8 /pnl

```
PNL SNAPSHOT
----------------
Updated: 09:44:12
Account: main
Snapshot at: 2026-06-03T09:44:00Z
Source: account_snapshot_worker

Persisted account data:
Equity: 1,234.50 USDT
Available balance: 980.20 USDT
Open risk: 12.40 USDT
Margin used: 220.00 USDT

Open chains:
Open: 2
Partial: 1
Waiting entry: 1

Unavailable in current persistence:
Realized PnL: n/a
Unrealized PnL: n/a
ROI/Funding/Fees: n/a
```

Nota: Realized PnL, Unrealized PnL, ROI/Funding/Fees sono sempre `n/a` nell'implementazione attuale.

---

### 5.9 /version

```
📦 VERSION
────────────────
Runtime: v2
Commit: a90ab1d
Branch: main
Uptime: 2h 14m
```

Formati uptime: `Xh Ym` / `Ym Xs` / `Xs` a seconda della durata.

---

### 5.10 /logs [n]

```
📋 LOGS — last 20
────────────────
2026-06-03 09:40:11 INFO  lifecycle_gate: ENTRY_FILLED chain=12
2026-06-03 09:40:12 INFO  notification_dispatcher: sent CLEAN_LOG
2026-06-03 09:41:02 WARN  exchange_sync: reconnect attempt 1
```

Variante log vuoto:
```
📋 LOGS — last 20
────────────────
(log vuoto)
```

`n` viene clampato tra 1 e 100. Default 20 se non specificato o non valido.

---

## 6. Comandi controllo — risposte

### 6.1 /pause (globale)

```
⏸️ NUOVE ENTRY BLOCCATE
────────────────
Scope: GLOBAL
Mode: BLOCK_NEW_ENTRIES

Effect:
New signals are routed to REVIEW_REQUIRED.

Commands:
/resume
/control
```

Variante blocco già attivo:
```
⏸️ NUOVE ENTRY BLOCCATE
────────────────
Scope: GLOBAL
Mode: BLOCK_NEW_ENTRIES
Block already active.

Effect:
New signals are routed to REVIEW_REQUIRED.

Commands:
/resume
/control
```

### 6.2 /pause TraderA

```
⏸️ TraderA — NUOVE ENTRY BLOCCATE
────────────────
Scope: TraderA
Mode: BLOCK_NEW_ENTRIES

Effect:
New signals for TraderA are routed to REVIEW_REQUIRED.

Commands:
/resume TraderA
/control
```

### 6.3 /resume (globale)

```
▶️ NUOVE ENTRY RIABILITATE
────────────────
Global block removed.

Commands:
/control
/status
```

### 6.4 /resume TraderA

```
▶️ TraderA — NUOVE ENTRY RIABILITATE
────────────────
Block removed for TraderA.

Commands:
/control
```

### 6.5 /resume senza blocco attivo

```
ℹ️ NESSUN BLOCCO ATTIVO
────────────────
No pause block exists for this scope.

Commands:
/control
```

### 6.6 /start

```
▶️ RUNTIME ATTIVATO
────────────────
Global block removed.

Commands:
/status
/control
```

Variante se già attivo:
```
▶️ RUNTIME ATTIVATO
────────────────
Runtime was already accepting new entries.

Commands:
/status
/control
```

### 6.7 /block BTCUSDT

```
🚫 BTCUSDT BLOCCATO
────────────────
Scope: GLOBAL
Blacklist: BTCUSDT, DOGEUSDT

Commands:
/unblock BTCUSDT
/control
```

### 6.8 /block TraderA SOLUSDT

```
🚫 TraderA / SOLUSDT BLOCCATO
────────────────
Scope: TraderA
Blacklist: SOLUSDT

Commands:
/unblock TraderA SOLUSDT
/control
```

### 6.9 /unblock BTCUSDT

```
✅ BTCUSDT SBLOCCATO
────────────────
Scope: GLOBAL
Blacklist: DOGEUSDT

Commands:
/control
```

### 6.10 /unblock TraderA SOLUSDT

```
✅ TraderA / SOLUSDT SBLOCCATO
────────────────
Scope: TraderA
Blacklist: none

Commands:
/control
```

### 6.11 /debug_on 30m

```
DEBUG MODE ATTIVATO
----------------
Durata: 30m
Scade: 2026-06-03 10:14:12 UTC
```

Formati durata: `1h`, `30m`, `5m`, `300s` (in base al valore passato).

### 6.12 /debug_off

```
DEBUG MODE DISATTIVATO
----------------
Il runtime torna alla policy standard dei log tecnici.
```

---

## 7. Casi silenziosi e errori

### 7.1 Comando sconosciuto

```
Comando non riconosciuto.
```

### 7.2 Argomenti errati

```
Usage: /trade <chain_id>
```

```
Usage: /pause  oppure  /pause <trader>
```

```
Usage: /block <symbol>  oppure  /block <trader> <symbol>
```

### 7.3 Utente non autorizzato

Nessuna risposta Telegram. Il comando viene registrato internamente come `REJECTED / unauthorized_user`.

### 7.4 Comando nel topic sbagliato

Nessuna risposta Telegram. Il comando viene registrato come `IGNORED / wrong_topic`.

### 7.5 ENTRY_CANCELLED filtrato

Se `cancel_reason = position_closed` il messaggio non viene inviato. La cancellazione delle entry pending alla chiusura posizione è implicita.

---

## 8. File sorgenti

```
src/runtime_v2/control_plane/notification_dispatcher.py  ← dispatch loop + rate limit
src/runtime_v2/control_plane/outbox_writer.py            ← build payload + mappa eventi→notification
src/runtime_v2/control_plane/topic_router.py             ← routing destinazione→chat/topic
src/runtime_v2/control_plane/telegram_bot.py             ← command parsing + dispatch
src/runtime_v2/control_plane/service.py                  ← logica operativa (pause, block, ...)
src/runtime_v2/control_plane/status_queries.py           ← query DB per /status /trades /trade ...

src/runtime_v2/control_plane/formatters/clean_log.py     ← tutti i formatter CLEAN_LOG
src/runtime_v2/control_plane/formatters/tech_log.py      ← formatter TECH_LOG
src/runtime_v2/control_plane/formatters/status.py        ← /status
src/runtime_v2/control_plane/formatters/trades.py        ← /trades
src/runtime_v2/control_plane/formatters/trade_detail.py  ← /trade #id
src/runtime_v2/control_plane/formatters/control.py       ← /control
src/runtime_v2/control_plane/formatters/reviews.py       ← /reviews
src/runtime_v2/control_plane/formatters/health.py        ← /health
src/runtime_v2/control_plane/formatters/pnl.py           ← /pnl
src/runtime_v2/control_plane/formatters/pause.py         ← /pause /resume /start
src/runtime_v2/control_plane/formatters/block.py         ← /block /unblock
src/runtime_v2/control_plane/formatters/debug.py         ← /debug_on /debug_off
```
