# Runtime V2 Control Plane — Messaggi Telegram (riferimento completo)

Aggiornato al codice corrente in `src/runtime_v2/control_plane/`.  
Ultima revisione: 2026-06-06 (layout unificato per tutte le chiusure finali, `Close reason` in testa, categorie sempre visibili con `n/a`).

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

## 1b. Vocabolario `Source:`

Ogni notifica riporta un campo `Source:` che indica chi ha originato l'evento.

| Valore | Significato |
|---|---|
| `trader_signal` | nuovo segnale da messaggio Telegram del trader |
| `trader_update` | modifica da messaggio Telegram del trader su chain esistente |
| `operation_rules` | regola automatica del sistema |
| `manual_command` | comando bot dell'utente (`/close`, `/pause`, ecc.) |
| `exchange` | fill o evento arrivato dall'exchange |
| `runtime` | logica interna (riconciliazione, reentry, decisioni lifecycle) |
| `timeout_worker` | worker che gestisce la scadenza degli ordini pending |

Per `SIGNAL_REJECTED`: `trader_signal` se il problema è nel contenuto del segnale (dati mancanti/inconsistenti), `runtime` se il problema è nello stato o configurazione del sistema.

---

## 2. CLEAN_LOG — mappa eventi

### 2a. Proiezione per-chain — `_CLEAN_LOG_EVENT_MAP` (`outbox_writer.py`)

Letta da `project_clean_log_for_chain` iterando `ops_lifecycle_events`.

| Evento lifecycle (`event_type`) | Notification type | Note |
|---|---|---|
| `SIGNAL_ACCEPTED` | `SIGNAL_ACCEPTED` | |
| `SIGNAL_REJECTED` | `SIGNAL_REJECTED` | segnali non eseguiti per qualsiasi motivo |
| `REVIEW_REQUIRED` | `REVIEW_REQUIRED` | solo per update su chain esistenti (non per nuovi segnali) |
| `ENTRY_FILLED` | `ENTRY_OPENED` | |
| `TP_FILLED` | `TP_FILLED` / `TP_FILLED_FINAL` | promosso a `TP_FILLED_FINAL` se `is_final=True` |
| `SL_FILLED` | `SL_FILLED` | |
| `CLOSE_FULL_FILLED` | `POSITION_CLOSED` / `BE_EXIT` | `BE_EXIT` se catena in stato `PROTECTED` |
| `ENTRY_UPDATED` | `ENTRY_UPDATED` | |
| `PENDING_TIMEOUT` | `PENDING_ENTRY_EXPIRED` | |
| `CLOSE_PARTIAL_FILLED` | `PARTIAL_CLOSE_EXECUTED` | filtrato se `source != manual_command` |
| `PENDING_ENTRY_CANCELLED` | `ENTRY_CANCELLED` | vedere regole filtro §7.5 |
| `ENTRY_CANCEL_FAILED` | `CANCEL_FAILED` | |
| `RECONCILIATION_WARNING` | `RECONCILIATION_WARNING` | |
| `RECONCILIATION_FIXED` | `RECONCILIATION_FIXED` | |
| `REENTRY_ACCEPTED` | `REENTRY_ACCEPTED` | |

### 2b. Notifiche sintetizzate — `_write_update_clean_log` (`entry_gate.py`)

Non passano da `_CLEAN_LOG_EVENT_MAP`. Scritte direttamente dopo aver processato un update canonico.

| Lifecycle events coinvolti | Notification type | Condizione |
|---|---|---|
| `TELEGRAM_UPDATE_ACCEPTED` (tutti, nessun NOOP) | `UPDATE_DONE` | tutte le azioni accettate |
| `TELEGRAM_UPDATE_ACCEPTED` + `NOOP_*` (misti) | `UPDATE_PARTIAL` | almeno una accettata e una rifiutata |
| solo `NOOP_*` (nessun ACCEPTED) | `UPDATE_REJECTED` | nessuna azione accettata |

- `Source` in output: `trader_update` · `operation_rules` · `manual_command` · `runtime` (fallback)
- Il link al messaggio Telegram originale è risolto da `raw_messages` e appare in footer dopo `Source:`.
- **Merge operazioni**: se un singolo messaggio genera più azioni sulla stessa chain (es. `CANCEL_PENDING` + `MOVE_SL_TO_BE`), tutti i `TELEGRAM_UPDATE_ACCEPTED` vengono fusi in un unico `UPDATE_DONE` con la lista completa di `Operation:` e `Changed:`. Non vengono scritti messaggi separati per ogni operazione.

### 2c. Notifiche multi-chain — `_write_multi_chain_summary` (`entry_gate.py`)

Scritte direttamente quando un update canonico impatta ≥ 2 chain **uniche** (distinte per `trade_chain_id`).

Per ogni update su N ≥ 2 chain vengono sempre scritti **entrambi**:
- N messaggi `UPDATE_DONE/PARTIAL/REJECTED` (uno per chain, con sezione `Changed:` dettagliata)
- 1 messaggio `MULTI_CHAIN_SUMMARY` (aggregato, `send_after=+3s`)

| Notification type | Condizione |
|---|---|
| `MULTI_CHAIN_SUMMARY` | ≥ 2 chain uniche colpite; per ogni chain viene mantenuto il worst-status tra le operazioni (`REVIEW > PARTIAL > SKIPPED > DONE`) |

**Status per chain nel summary:**

| Lifecycle events sulla chain | Status |
|---|---|
| solo `TELEGRAM_UPDATE_ACCEPTED` | `DONE` |
| `TELEGRAM_UPDATE_ACCEPTED` + almeno un `NOOP_*` | `PARTIAL` |
| solo `REVIEW_REQUIRED` (senza ACCEPTED né NOOP) | `REVIEW` |
| solo `NOOP_*` (senza ACCEPTED né REVIEW) | `SKIPPED` |

- La lista `chains` è deduplicata per `trade_chain_id`: più operazioni sulla stessa chain non generano righe duplicate nel summary.
- Se un messaggio ha N operazioni su 1 sola chain → nessun summary (il singolo UPDATE_DONE copre tutto).
- Il formatter gestisce anche `MULTI_CHAIN_UPDATE` e `MULTI_CHAIN_CLOSED` come alias dello stesso template, ma nell'implementazione attuale viene scritto solo `MULTI_CHAIN_SUMMARY`.
- **Link per chain**: risolto a tempo di scrittura leggendo `clean_log_root_message_id` da `ops_clean_log_tracking` — punta al `SIGNAL_ACCEPTED` della chain (stabile). Il dispatcher usa il link dal payload; fa lookup live solo come fallback per chain senza tracking row.
- **Source**: derivato dal `source_type` del primo evento della prima chain colpita (`telegram_update` → `trader_update`, `operation_rules` → `operation_rules`, ecc.).

**Comportamento per tipo di azione sulle chain `WAITING_ENTRY`** (incluse negli scope globali):

| Azione | Risultato | Status nel summary |
|---|---|---|
| `CANCEL_PENDING` | cancella ordini di entry pendenti → `DONE` | `DONE` |
| `MOVE_SL_TO_BE` | `NOOP_NOT_PENDING` (no fill, no avg price) | `SKIPPED` |
| `CLOSE_FULL` | rediretto a `_apply_cancel_pending` → appare come `CANCEL_PENDING` nell'UPDATE_DONE | `DONE` |

Nota: se un update CLOSE_FULL globale colpisce chain OPEN e chain WAITING_ENTRY, il summary mostra sia `Close full` che `Cancel pending` nella sezione Operation (operazioni reali per chain, non intenzione del messaggio). Le sezioni `Changed:` dei singoli UPDATE_DONE per-chain chiariscono cosa è successo su ognuna.

**Sequenza visibile in Telegram per azione tipo CLOSE_FULL su N chain OPEN:**

```
t+0s   ✅ #12 — UPDATE DONE   [Operation: Close full / Changed: Position open→closed]
       ✅ #13 — UPDATE DONE   [...]
       ...
t+3s   ✅ UPDATE APPLICATO - N chain   [MULTI_CHAIN_SUMMARY, send_after]
t+5s   ✅ #12 — POSITION CLOSED        [exchange fill confermato]
       ✅ #13 — POSITION CLOSED
       ...
```

Per MOVE_SL_TO_BE, CANCEL_PENDING, e altre azioni senza fill exchange: la sequenza si ferma al MULTI_CHAIN_SUMMARY (nessun messaggio asincrono successivo).

**Sequenza visibile per CLOSE_FULL su N chain (mix OPEN + WAITING_ENTRY):**

```
t+0s   ✅ #12 — UPDATE DONE   [Operation: Close full]     ← catena OPEN
       ✅ #13 — UPDATE DONE   [Operation: Cancel pending]  ← catena WAITING_ENTRY
t+3s   ✅ UPDATE APPLICATO - N chain   [Operation: Close full / Cancel pending]
t+5s   ✅ #12 — POSITION CLOSED        ← solo le catene OPEN ottengono il fill
```

---

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
Source: trader_signal
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

Emesso quando il segnale non viene eseguito per qualsiasi motivo (risk, concorrenza, dati mancanti, etc.).
Struttura identica a `SIGNAL_ACCEPTED`: entries, SL, TPs, Risk quando disponibili.

`#id` non compare — nessuna chain viene creata per i segnali rigettati.

```
❌ — SIGNAL REJECTED
- - - - - - - - - - - - -
ETHUSDT — 📉 SHORT
- - - - - - - - - - - - -
Entry_1: 3,820 Limit
SL: 3,910
TP_1: 4,100
TP_2: 4,250
Risk: 0.8%
- - - - - - - - - - - - -
Trader: TraderA
Exchange Account: main
Rejected: max_capital_at_risk_exceeded
Source: runtime
- - - - - - - - - - - - -
https://t.me/c/123456/987
```

Note:
- `Rejected:` mostra il `reason` dal payload dell'evento.
- TPs e Risk sono omessi se non disponibili (es. il gate si ferma prima del calcolo del rischio).
- Motivi possibili: `duplicate_position`, `max_concurrent_trades_reached`, `max_concurrent_same_symbol_reached`, `max_capital_at_risk_exceeded`, `risk_leverage_exceeds_account_max_leverage`, `missing_stop_loss_for_risk_calc`, `missing_limit_price`, `zero_risk_distance`, `missing_account_snapshot_for_live_equity`, `invalid_policy_snapshot`, `missing_symbol_or_side`, `no_entry_legs`, `control_mode:new_entries_paused`.

---

### 3.3 REVIEW_REQUIRED

> **Nota:** `REVIEW_REQUIRED` non viene più emesso per nuovi segnali in ingresso.
> Viene usato esclusivamente per **update su chain esistenti** che non possono essere applicati automaticamente.

Struttura: stessa di UPDATE_DONE/PARTIAL/REJECTED con chain id, symbol, side e motivo.

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

Emesso quando un ordine pending viene cancellato **con fill parziale rilevante** o per ragione sconosciuta dall'exchange.  
Vedere §7.5 per le regole di soppressione complete basate su `cancel_origin`.

Caso tipico visibile: entry cancellata con fill parziale ≥ 1% (l'info del fill residuo è operativamente rilevante):

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

Caso **non visibile** (soppresso — coperto da altro messaggio): entry cancellata a zero fill da trader_update, timeout_worker, o engine_rule. Vedere §7.5.

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

Emesso all'ultimo TP (chiusura completa via TP). Usa il layout unificato `POSITION CLOSED`.

Regole comuni per tutte le chiusure finali (`TP_FILLED_FINAL`, `SL_FILLED`, `POSITION_CLOSED`, `BE_EXIT`):
- `Close reason:` compare sempre subito sotto il titolo.
- i separatori `- - -` dividono blocchi semantici distinti: intestazione, identità trade, dati esecuzione, risultato finale, source/link.
- `Qty`, `PnL`, `Fee`, `Fee rate`, e tutte le righe di `Final Result` sono sempre visibili.
- se un valore manca, il formatter stampa `n/a`.

```
✅ #12 — POSITION CLOSED
Close reason: FINAL TP FILLED
- - - - - - - - - - - - - - - -
BTCUSDT — 📈 LONG
https://t.me/c/123456/987
- - - - - - - - - - - - - - - -
TP_2: 70,500
Qty: 0.015
PnL: +45.20 USDT
Fee: 1.03 USDT
Fee rate: 0.055%
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

Note: `ROI net` ora resta visibile anche quando assente (`n/a`).

---

### 3.10 SL_FILLED

Emesso quando lo stop loss viene colpito. Usa il layout unificato `POSITION CLOSED`.

```
🛑 #12 — POSITION CLOSED
Close reason: STOP_LOSS
- - - - - - - - - - - - - - - -
BTCUSDT — 📈 LONG
https://t.me/c/123456/987
- - - - - - - - - - - - - - - -
SL: 66,400
Qty: 0.015
PnL: -32.40 USDT
Fee: 0.91 USDT
Fee rate: n/a
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

### 3.11 POSITION_CLOSED — da comando bot (CLOSE_FULL)

Emesso quando la chiusura arriva come conferma di un ordine piazzato dal bot su comando esplicito. Usa il layout unificato `POSITION CLOSED`.

```
✋ #12 — POSITION CLOSED
Close reason: MANUAL_CLOSE
- - - - - - - - - - - - - - - -
BTCUSDT — 📈 LONG
https://t.me/c/123456/987
- - - - - - - - - - - - - - - -
Price: 68,920
Qty: 0.015
PnL: +8.30 USDT
Fee: 0.48 USDT
Fee rate: n/a
- - - - - - - - - - - - - - - -
Final Result:
ROI net: +0.61%
Total PnL net: +7.82 USDT
Gross PnL: +8.30 USDT
Fees: -0.48 USDT
Funding: +0.00 USDT
- - - - - - - - - - - - - - - -
Source: manual_command
```

---

### 3.12 POSITION_CLOSED — chiusura esterna rilevata da riconciliazione

Emessa quando la riconciliazione rileva una posizione già chiusa sull'exchange (chiusura manuale o liquidazione fuori dal bot).  
Usa lo stesso layout unificato `POSITION CLOSED`.

```
✋ #12 — POSITION CLOSED
Close reason: MANUAL_CLOSE
- - - - - - - - - - - - - - - - - - - - -
BTCUSDT — 📈 LONG
https://t.me/c/123456/987
- - - - - - - - - - - - - - - - - - - - -
Price: 68,500
Qty: n/a
PnL: +3.20 USDT
Fee: 0.38 USDT
Fee rate: n/a
- - - - - - - - - - - - - - - - - - - - -
Final Result:
ROI net: n/a
Total PnL net: n/a
Gross PnL: n/a
Fees: n/a
Funding: n/a
- - - - - - - - - - - - - - - - - - - - -
Source: position_reconciliation
```

Tabella distinzione chiusure:

| Scenario | `Close reason` | `Source` |
|---|---|---|
| TP finale | `FINAL TP FILLED` | `exchange` |
| Stop loss | `STOP_LOSS` | `exchange` |
| Comando bot (`CLOSE_FULL`) | `MANUAL_CLOSE` | `manual_command` |
| Chiusura manuale/esterna | `MANUAL_CLOSE` | `position_reconciliation` |

---

### 3.13 BE_EXIT

`BE_EXIT` resta una semantica di chiusura, ma non ha più un layout dedicato.  
Viene renderizzato con lo stesso template `POSITION CLOSED` e con `Close reason: BREAKEVEN_AFTER_TP`.

```
⚡ #12 — POSITION CLOSED
Close reason: BREAKEVEN_AFTER_TP
- - - - - - - - - - - - - - - -
BTCUSDT — 📈 LONG
https://t.me/c/123456/987
- - - - - - - - - - - - - - - -
Price: 68,500
Qty: 0.015
PnL: +0.20 USDT
Fee: 0.39 USDT
Fee rate: n/a
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
**Più operazioni sullo stesso messaggio e chain vengono fuse in un unico messaggio.**

Esempio con due operazioni (es. "убираем лимитки" + "передвинуть стоп в бу"):

```
✅ #12 — UPDATE DONE
- - - - - - - - - - - - - - - -
BTCUSDT — 📈 LONG
https://t.me/c/123456/987
- - - - - - - - - - - - - - - -
Operation:
▪️ CANCEL_PENDING
▪️ MOVE_SL_TO_BE

Changed:
Entry_2: 61,192.03 -> cancelled
SL: 66,400 -> 68,500 *
* BE
- - - - - - - - - - - - - - - -
Source: trader_update
- - - - - - - - - - - - - - - -
https://t.me/c/123456/1005
```

Esempio con singola operazione:

```
✅ #12 — UPDATE DONE
- - - - - - - - - - - - - - - -
BTCUSDT — 📈 LONG
https://t.me/c/123456/987
- - - - - - - - - - - - - - - -
Operation:
▪️ MOVE_SL_TO_BE

Changed:
SL: 66,400 -> 68,500 *
* BE
- - - - - - - - - - - - - - - -
Source: trader_update
- - - - - - - - - - - - - - - -
https://t.me/c/123456/1005
```

Variante senza operation né changed (update vuoto applicato):
```
✅ #12 — UPDATE DONE
- - - - - - -
BTCUSDT — 📈 LONG
https://t.me/c/123456/987
- - - - - - -
Source: trader_update
- - - - - - -
https://t.me/c/123456/1005
```

Note:
- `Source: trader_update` = triggered da messaggio Telegram del trader.
- Il secondo link (dopo l'ultimo separatore) è il link al messaggio Telegram che ha originato l'update — costruito da `raw_messages` al momento del persist.
- Operazioni: nomi raw dall'event payload (`MOVE_SL_TO_BE`, `CANCEL_PENDING`, `CLOSE_FULL`, `CLOSE_PARTIAL`, `MODIFY_ENTRIES`, `MARKET_ENTRY_NOW`).
- `Changed:` mostra i campi effettivamente modificati: `SL`, `Entry_N`, `Position`.
- Quando un singolo messaggio ha N operazioni su 1 chain, viene scritto **1 solo UPDATE_DONE** aggregato (non N messaggi separati).

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
  • MOVE_SL_TO_BE

Changed:
SL: 66,400 -> 68,500

Rejected:
  • NOOP_NOT_PENDING
- - - - - - - - - - - - - - - -
Source: trader_update
- - - - - - - - - - - - - - - -
https://t.me/c/123456/1005
```

Note:
- `Applied:` = action strings raw (`MOVE_SL_TO_BE`, `CANCEL_PENDING`, ecc.).
- `Changed:` = campi modificati dalle azioni applicate (stessa logica di UPDATE_DONE).
- `Rejected:` = NOOP event type strings (`NOOP_NOT_PENDING`, `NOOP_ALREADY_PROTECTED_BE`, `NOOP_DUPLICATE_COMMAND`, `NOOP_ALREADY_CLOSED`).

---

### 3.16 UPDATE_REJECTED

Emesso quando l'update viene rifiutato integralmente (nessuna azione accettata).

```
❌ #12 — UPDATE REJECTED
- - - - - - - - - - - - - - - -
BTCUSDT — 📈 LONG
https://t.me/c/123456/987
- - - - - - - - - - - - - - - -
Rejected:
  • NOOP_ALREADY_CLOSED
- - - - - - - - - - - - - - - -
Source: trader_update
- - - - - - - - - - - - - - - -
https://t.me/c/123456/1005
```

Note:
- `Reason:` appare solo se il payload del NOOP event include un campo `reason` (raro — la maggior parte dei NOOP non lo include).
- `Rejected:` = lista dei NOOP event type strings.
- `Source:` e link al messaggio Telegram: stessa logica di UPDATE_DONE.

---

### 3.16b PARTIAL_CLOSE_EXECUTED

Emesso quando il fill di un `CLOSE_PARTIAL` da Telegram viene confermato dall'exchange.
Fill esterni (chiusure manuali su exchange) vengono filtrati (`source != manual_command`).

```
✅ #12 — UPDATE DONE
- - - - - - - - - - - - - - - -
BTCUSDT — 📈 LONG
https://t.me/c/123456/987
- - - - - - - - - - - - - - - -
Executed:
▪️ CLOSE_PARTIAL
- - - - - - - - - - - - - - - -
Price: 68,500
Qty: 0.015
Closed: 50%
PnL: +12.30 USDT
Fee: 0.48 USDT
- - - - - - - - - - - - - - - -
Source: manual_command
```

Note:
- `signal_link` nel header punta al messaggio SIGNAL_ACCEPTED della chain (standard per tutti i fill).
- PnL calcolato con lo stesso principio di `TP_FILLED`: `qty × (fill_price − entry_avg_price) × sign(side)`.
- PnL e Fee appaiono solo se disponibili nel payload exchange.

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
Source: timeout_worker
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

### 3.21 MULTI_CHAIN_SUMMARY

Emesso quando un update impatta ≥ 2 chain. Appare **sempre insieme** ai singoli `UPDATE_DONE` per-chain.  
Due layout distinti in base al campo `summary_kind`.

---

#### 3.21a Autosufficient summary — `summary_kind: "immediate"` (non-CLOSE_FULL)

Usato per `CANCEL_PENDING`, `MOVE_SL_TO_BE`, `MOVE_STOP`, e qualsiasi operazione diversa da `CLOSE_FULL`.  
Emesso a `t+3s` dopo i singoli `UPDATE_DONE`. Contiene i `display_lines` per-chain.

Variante tutti DONE (emoji ✅):

```
✅ UPDATE APPLICATO - 2 chain
- - - - - - - - - - - - - - - - - - - - - - -
Operations requested:
▪️ CANCEL_PENDING
▪️ MOVE_SL_TO_BE
- - - - - - - - - - - - - - - - - - - - - - -
#6 WLD LONG — DONE
https://t.me/c/3897279123/468
Entry_2: 61,192.03 -> cancelled
Entry_3: 60,192.03 -> cancelled
SL: 66,400 -> 68,500 BE
- - - - - - - - - - - - - - - - - - - - - - -
#7 ICNT LONG — DONE
https://t.me/c/3897279123/469
SL: 66,400 -> 68,500 BE
- - - - - - - - - - - - - - - - - - - - - - -
Done: 2 | Partial: 0 | Skipped: 0 | Error: 0
- - - - - - - - - - - - - - - - - - - - - - -
Source: trader_update
- - - - - - - - - - - - - - - - - - - - - - -
https://t.me/c/3927267771/365
```

Variante con PARTIAL (emoji ⚠️):

```
⚠️ UPDATE APPLICATO - 2 chain
- - - - - - - - - - - - - - - - - - - - - - -
Operations requested:
▪️ CANCEL_PENDING
▪️ MOVE_SL_TO_BE
- - - - - - - - - - - - - - - - - - - - - - -
#6 WLD LONG — DONE
https://t.me/c/3897279123/468
Entry_2: 61,192.03 -> cancelled
SL: 66,400 -> 68,500 BE
- - - - - - - - - - - - - - - - - - - - - - -
#7 ICNT LONG — PARTIAL
https://t.me/c/3897279123/469
Entry_2: SKIPPED - no pending averaging order
SL: 66,400 -> 68,500 BE
- - - - - - - - - - - - - - - - - - - - - - -
Done: 1 | Partial: 1 | Skipped: 0 | Error: 0
- - - - - - - - - - - - - - - - - - - - - - -
Source: trader_update
- - - - - - - - - - - - - - - - - - - - - - -
https://t.me/c/3927267771/365
```

Struttura footer count: `Done: N | Partial: N | Skipped: N | Error: N` (tutti i campi sempre presenti).  
Il link sorgente update appare in fondo dopo l'ultimo separatore (ultima riga del messaggio).

---

#### 3.21b Compact summary — `summary_kind: "final_close"` (CLOSE_FULL)

Emesso **in differita** quando tutti i `POSITION_CLOSED` per le chain coinvolte sono stati ricevuti.  
Nessun `display_lines` — layout compatto. Il link per chain punta al `POSITION_CLOSED` finale.

```
✅ UPDATE APPLICATO - 2 chain
- - - - - - - - - - - - - - - - - - - - - - -
Operation requested:
▪️ Close full
- - - - - - - - - - - - - - - - - - - - - - -
#6 WLD LONG — DONE
https://t.me/c/3897279123/468
- - - - - - - - - - - - - - - - - - - - - - -
#7 ICNT LONG — DONE
https://t.me/c/3897279123/469
- - - - - - - - - - - - - - - - - - - - - - -
Done: 2 | Partial: 0 | Review: 0 | Skipped: 0 | Error: 0
- - - - - - - - - - - - - - - - - - - - - - -
Source: trader_update
- - - - - - - - - - - - - - - - - - - - - - -
https://t.me/c/3927267771/365
```

Footer: include `Review:` (non compare nel layout `immediate`). Nessun `display_lines` renderizzato.

---

#### 3.21c Semantica dei link per-chain

| `summary_kind` | `link_mode` | Origine link | Campo DB |
|---|---|---|---|
| `immediate` | `signal_root` | `SIGNAL_ACCEPTED` della chain | `clean_log_root_message_id` |
| `final_close` | `final_close` | `POSITION_CLOSED` della chain | `clean_log_last_message_id` |

Il dispatcher usa il link dal payload; fa lookup live solo come fallback per chain senza tracking row.

---

#### 3.21d Move-stop reference semantics

Nelle `display_lines` per-chain (`summary_kind: "immediate"`):

| Azione | display_lines |
|---|---|
| `MOVE_SL_TO_BE` / `MOVE_STOP_BE` | `SL: <old> -> <new> BE` |
| `MOVE_STOP` con riferimento TP | `SL: <old> -> <new>` + `Reference: TP_N` |
| `MOVE_STOP` con riferimento Price | `SL: <old> -> <new>` + `Reference: Price` |

---

Valori `State` possibili:

| State | Significato |
|---|---|
| `DONE` | tutte le azioni accettate |
| `PARTIAL` | almeno una accettata + almeno una NOOP |
| `SKIPPED` | solo NOOP (es. `NOOP_NOT_PENDING`, `NOOP_ALREADY_PROTECTED_BE`) |
| `REVIEW` | chain mandata in review (`REVIEW_REQUIRED`) senza nessuna azione accettata |

Note:
- Il summary arriva `+3s` dopo i singoli UPDATE_DONE per-chain (`immediate`), o in differita dopo i fill exchange (`final_close`).
- `Source:` è derivato dal tipo di aggiornamento: `trader_update`, `operation_rules`, `manual_command`.
- Il formatter gestisce anche `MULTI_CHAIN_UPDATE` e `MULTI_CHAIN_CLOSED` come alias dello stesso template.

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

### 7.5 ENTRY_CANCELLED — regole di soppressione

`ENTRY_CANCELLED` viene soppresso quando la cancellazione è già coperta da un altro messaggio.  
La decisione si basa sul campo `cancel_origin` nel payload dell'evento `PENDING_ENTRY_CANCELLED`,  
propagato dal payload del comando `CANCEL_PENDING_ENTRY` via `event_sync._get_command_cancel_origin()`.

| `cancel_reason` | `cancel_origin` | `partial_fill_pct` | Risultato | Coperto da |
|---|---|---|---|---|
| `position_closed` | qualsiasi | qualsiasi | ❌ soppresso | chiusura posizione implicita |
| qualsiasi | `timeout_worker` | qualsiasi | ❌ soppresso | `PENDING_ENTRY_EXPIRED` (§3.17) |
| qualsiasi | `trader_update` | < 1% | ❌ soppresso | `UPDATE_DONE` (§3.14) |
| qualsiasi | `trader_update` | ≥ 1% | ✅ visibile | fill parziale rilevante |
| qualsiasi | `engine_rule` | < 1% | ❌ soppresso | `UPDATE_DONE` da `operation_rules` |
| qualsiasi | `engine_rule` | ≥ 1% | ✅ visibile | fill parziale rilevante |
| qualsiasi | assente / sconosciuto | qualsiasi | ✅ visibile | origine ignota = potenziale problema |

**`cancel_origin` — chi lo imposta:**
- `trader_update` → `entry_gate._apply_cancel_pending()` (CANCEL_PENDING da messaggio trader)
- `timeout_worker` → `TimeoutWorker._process_timeout()` (scadenza pending_timeout_hours)
- `engine_rule` → non ancora implementato (cancel_averaging_pending_after usa ancora il path base)
- assente → cancellazioni pre-esistenti nel DB, path da `event_sync` senza lookup riuscito, o cancel exchange-side

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
