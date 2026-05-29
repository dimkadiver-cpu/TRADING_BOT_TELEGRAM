# CLEAN_LOG_SPEC — Eventi Operativi Trade

Versione: 2.0
Sostituisce: `CLEAN_LOG_SPEC_RUNTIME_V2_COMPACT_V3`

---

## 1. Principio Fondamentale

`CLEAN_LOG` è una **timeline operativa leggibile** per seguire una `TradeChain`.
Non è un mirror degli eventi interni del runtime.
Non è una versione abbellita del logger tecnico.

```text
CLEAN_LOG  = milestone operative aggregate — cosa è successo al trade
TECH_LOG   = diagnostica tecnica           — perché il sistema ha avuto un problema
DB / AUDIT = storico completo granulare    — fonte di verità per reconciliation e report
```

### 1.1 Regole fondamentali

```text
- CLEAN_LOG non mostra `State:` dei trade (OPEN, PARTIALLY_CLOSED, ecc.)
- CLEAN_LOG non mostra JSON, traceback, order id exchange, execution command id
- CLEAN_LOG non mostra log tecnici del logger Python
- CLEAN_LOG non mostra eventi intermedi tecnici (order placed, SL attached, poll OK)
- CLEAN_LOG non mostra eventi di controllo runtime (pause/resume/block) — quelli stanno in COMMANDS
- Lo stato tecnico è implicito nel titolo del messaggio
- Ogni update successivo al setup è una reply al root message della chain
- Un trade normale non deve produrre più di 4-6 messaggi
```

### 1.2 Source ammesse

```text
original_message    → segnale originale dal canale trader
trader_update       → messaggio update esplicito del trader
operation_rules     → regola interna (es. BE automatico dopo TP)
exchange            → evento confermato dall'exchange (fill, cancelled)
runtime             → decisione lifecycle interna
manual_command      → comando umano dal Control Plane
reconciliation      → correzione operativa da reconciliation worker
timeout_worker      → scadenza/cancellazione automatica
```

---

## 2. Mappa eventi interni → CLEAN_LOG visibile

| Evento interno | CLEAN_LOG visibile | Policy |
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
| `PENDING_ENTRY_CANCELLED` | dentro `UPDATE DONE` | aggregate |
| `SL_MOVED` | dentro `UPDATE DONE` | aggregate |
| `MOVE_SL_TO_BE` | dentro `UPDATE DONE` oppure dentro `TP FILLED` | aggregate |
| `TP_FILLED_PARTIAL` | `TPn FILLED` oppure `TPn + TPm FILLED` | aggregate |
| `TP_FILLED_FINAL` | `TPn FILLED — POSITION CLOSED` | always |
| `SL_FILLED` | `SL FILLED — POSITION CLOSED` | always |
| `BE_EXIT` | `BE EXIT — POSITION CLOSED` | always |
| `CLOSE_REQUESTED` | `CLOSE REQUESTED` | only if delayed |
| `POSITION_CLOSED` | `POSITION CLOSED` | always |
| `REENTRY_ACCEPTED` | `REENTRY ACCEPTED` | always |
| `ENTRY_CANCELLED_PARTIAL_FILL` | `ENTRY CANCELLED` | always |
| `PENDING_TIMEOUT` | `PENDING ENTRY EXPIRED` | always |
| `RECONCILIATION_OK` | nessun messaggio | off |
| `RECONCILIATION_WARNING` | `RECONCILIATION WARNING` | only if risk |
| `RECONCILIATION_FIXED` | `RECONCILIATION FIXED` | if previous warning |
| `EXECUTION_COMMAND_SENT` | nessun messaggio | off |
| `EXCHANGE_POLL_OK` | nessun messaggio | off |
| `MULTI_CHAIN_UPDATE` | per-chain `UPDATE DONE/PARTIAL/REJECTED` + summary opzionale | aggregate by chain |

---

## 3. Formato Standard

```text
<emoji> #<chain_id> — <EVENT>
────────────────
<symbol> — <side emoji> <SIDE>

<body essenziale>

────────────────
Source: <source>
<link se disponibile>
```

**Regole formato:**

```text
- Massimo 8-12 righe per messaggi normali
- Il messaggio di chiusura (final result) può essere più lungo
- State: non deve mai comparire nel body
- Source: sempre presente
- Link al messaggio originale: quando disponibile e rilevante
- Emoji lato: 📈 LONG | 📉 SHORT
```

**Emoji per tipo evento:**

```text
✅  — accettato, completato, done
❌  — rifiutato, failed
⚠️  — warning, review required, partial
📊  — evento operativo trade (entry, TP, close)
🛑  — SL hit, stop loss
🔁  — multi-chain update
⏸️  — pausa esecuzione
▶️  — ripresa esecuzione
```

---

## 4. Messaggi Root della Chain

### 4.1 SIGNAL_ACCEPTED

Nuovo messaggio nel topic `CLEAN_LOG`.

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
Trader: trader_a
Source: original_message
* Risk from operation_rules
────────────────
https://t.me/c/3927267771/206
```

**Varianti del campo `Risk:`**

```text
Risk: 0.5% *
* Risk from operation_rules
→ calcolato dal sistema, trader non ha fornito hint
```

```text
Risk: 0.5% ↓ *
* Reduced from trader hint 1.2% — cap: 0.5% per signal (trader_a)
→ trader ha indicato risk hint, operation_rules l'ha ridotto al cap
```

```text
Risk: 0.8% (trader hint) *
* use_trader_risk_hint: true
→ risk hint del trader accettato senza modifiche
```

```text
Risk: N/A
→ SL non presente o sizing non calcolabile
```

Regola: mostrare `↓` solo quando il risk effettivo è inferiore a quello indicato nel messaggio originale.
Il footnote spiega sempre perché.

Non mandare subito messaggi separati per `ORDER_PLACED`, `SL_ATTACHED`, `TP_ATTACHED`.

### 4.2 SIGNAL_REJECTED

```text
❌ #146 — SIGNAL REJECTED
────────────────
BTC/USDT — 📈 LONG

Entry_1: 65,000 Market
SL: 62,000
TP_1: 68,000

────────────────
Trader: trader_b
Rejected: invalid_risk_profile
Reason: risk exceeds max allowed for trader_b
Source: original_message
────────────────
https://t.me/c/3927267771/206
```

### 4.3 REVIEW_REQUIRED

```text
⚠️ #147 — REVIEW REQUIRED
────────────────
ETH/USDT — 📉 SHORT

Entry_1: 3,420 Limit
SL: 3,520
TP_1: 3,300

────────────────
Trader: trader_a
Reason: ambiguous_entry_zone
Action: no automatic execution
Source: original_message
────────────────
https://t.me/c/3927267771/207
```

---

## 4b. Formati Prezzo Entry — Rendering per Struttura

Il rendering delle entry nel CLEAN_LOG dipende dalla struttura (`EntryStructure`) e dal tipo (`EntryType`).
Questa sezione riflette il comportamento **effettivo del codice** (enrichment + execution gateway).

### Strutture supportate — stato attuale

| EntryStructure | EntryType leg 1 | EntryType leg 2+ | Note |
|---|---|---|---|
| `ONE_SHOT` | MARKET o LIMIT | — | Supportato |
| `TWO_STEP` | MARKET o LIMIT | LIMIT | Supportato |
| `RANGE` | LIMIT | LIMIT | Supportato — zona [min, max] |
| `LADDER` | MARKET o LIMIT | LIMIT | Supportato (LIMIT). MARKET leg 1 cade su `market.single` |

### ONE_SHOT MARKET — nessun prezzo nell'ordine exchange

Il parser può aver estratto un prezzo indicativo, ma il comando inviato all'exchange ha sempre `price=null`.

Nel `SIGNAL_ACCEPTED` mostrare il prezzo indicativo se disponibile:

```text
Entry_1: Market ~65,000
```

Se il parser non ha estratto alcun prezzo:

```text
Entry_1: Market
```

Il tilde `~` indica prezzo indicativo, non di esecuzione.

### ONE_SHOT LIMIT

```text
Entry_1: 65,000 Limit
```

### TWO_STEP — leg 1 MARKET + leg 2 LIMIT

```text
Entry_1: Market ~65,000
Entry_2: 64,000 Limit
```

oppure senza prezzo indicativo:

```text
Entry_1: Market
Entry_2: 64,000 Limit
```

### RANGE — zona di entrata (due LIMIT)

Il config `entry_split.LIMIT.range.split_mode` è **implementato** nel processor
(`SignalEnrichmentProcessor._apply_range_split`). I quattro valori producono comportamenti distinti:

```text
endpoints  → E1=min, E2=max della zona (prezzi del parser usati as-is)
firstpoint → E1=min, E2=min (entrambe le leg al prezzo inferiore)
lastpoint  → E1=max, E2=max (entrambe le leg al prezzo superiore)
midpoint   → E1=mid, E2=mid dove mid=(min+max)/2 arrotondato a 8 decimali
```

Nota: `firstpoint`, `lastpoint` e `midpoint` producono due leg con prezzo identico —
è una scelta intenzionale per concentrare l'esposizione su un solo livello
mantenendo la struttura RANGE e i pesi configurati.

Nel CLEAN_LOG mostrare sempre la zona come da **segnale originale** (non le leg calcolate
dopo il split) — i prezzi effettivi di esecuzione si vedono in `ENTRY_OPENED`:

```text
Entry zone: 64,000 — 65,000 Limit
```

I pesi di split (default E1: 50%, E2: 50%) non vengono mostrati nel CLEAN_LOG — sono dettagli interni visibili in `/trade #id` o nel DB.

### LADDER — 3+ leg LIMIT

Solo leg LIMIT. Il peso % si mostra perché la distribuzione è operativamente rilevante.

```text
Entry_1: 65,000 Limit (50%)
Entry_2: 64,000 Limit (30%)
Entry_3: 63,000 Limit (20%)
```

### Caso speciale — Deferred Market

Quando la leg MARKET ha `qty_mode: deferred_market`, la quantità è calcolata al fill (non pre-calcolata).
Il SIGNAL_ACCEPTED non può mostrare la qty — mostrare invece il rischio allocato:

```text
Entry_1: Market  (qty at fill, risk: 2.0%)
```

Nel ENTRY_OPENED si mostrerà la qty effettiva una volta confermata dall'exchange.

### Regole generali di rendering

```text
- MARKET senza prezzo parser → "Market"
- MARKET con prezzo parser   → "Market ~<prezzo>"
- LIMIT                      → sempre con prezzo numerico
- RANGE                      → "Entry zone: <min> — <max> Limit"
- LADDER                     → lista con peso %, solo se ≥3 leg
- TWO_STEP                   → nessun peso % (implicito: E1 70%, E2 30%)
```

### SIGNAL_ACCEPTED vs ENTRY_OPENED — quale prezzo mostrare

```text
SIGNAL_ACCEPTED:
→ prezzi dal segnale originale (parser)
→ MARKET mostra prezzo indicativo se disponibile (~)
→ RANGE mostra zona [min, max]

ENTRY_OPENED:
→ prezzo effettivo di fill dall'exchange
→ mai il prezzo del segnale
→ slippage solo se supera market_execution.tolerance_pct
```

Esempio ENTRY_OPENED con slippage rilevante:

```text
📊 #145 — ENTRY OPENED
────────────────
BTC/USDT — 📈 LONG

Filled:
Entry_1: 65,320 Market
Qty: 0.004 BTC
Fee: 1.45 USDT

Position:
Avg entry: 65,320
Filled: 50%
Pending: Entry_2 64,000 Limit

Changed vs signal:
Entry_1: ~65,000 → 65,320 (slippage: +0.49%)

────────────────
Source: exchange
```

Lo slippage si mostra solo se supera `market_execution.tolerance_pct` (default: 0.5%).
Slippage entro soglia → non mostrare la sezione `Changed vs signal`.

---

## 5. Entry Events

### 5.1 ENTRY_OPENED

Reply al root message della chain.

Sostituisce e aggrega: `ENTRY_ORDER_PLACED`, `ENTRY_FILLED`, `SL_ATTACHED`, `TP_ATTACHED`.

Usare quando la posizione è realmente aperta. Non usare per ordine solo piazzato.

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
Pending: Entry_2 64,000 Limit

────────────────
Source: exchange
```

SL e TP non vengono ripetuti — sono già nel root message `SIGNAL_ACCEPTED`.
Mostrare solo se qualcosa è cambiato rispetto al segnale originale:

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
Pending: Entry_2 64,000 Limit

Changed vs signal:
SL: 62,000 → 62,500 *

────────────────
Source: exchange
* Adjusted by operation_rules (tick rounding)
```

### 5.2 ENTRY_UPDATED

Reply al root message.

Usare quando un leg successivo viene fillato e cambia prezzo medio o esposizione.

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
Pending: none

────────────────
Source: exchange
```

---

## 6. Update Compositi

### 6.1 Principio

`UPDATE DONE`, `UPDATE PARTIAL` e `UPDATE REJECTED` sono il formato unico
per tutti gli update operativi indipendentemente dalla sorgente.

```text
trader_update, operation_rules, manual_command, runtime, reconciliation, timeout_worker
→ stesso formato UPDATE, source diversa
```

Un singolo messaggio update può contenere più operazioni sulla stessa chain
se condividono:

```text
same chain_id
same source_message_id o same engine_batch_id
same debounce window
```

Regola pratica: `1 chain + N modifiche vicine = 1 UPDATE composito`

### 6.2 Ordine sezioni dentro update composito

```text
1. Operation
2. Applied / Rejected (se parziale)
3. Cancelled
4. Filled / Closed (se presente)
5. Changed
6. Protection
7. Result (se chiude la posizione)
8. Source + link
```

### 6.3 UPDATE_DONE — trader update

```text
✅ #145 — UPDATE DONE
────────────────
BTC/USDT — 📈 LONG

Operation:
▪️ Cancel pending entry
▪️ Move SL to BE

Cancelled:
Entry_2: 64,000 Limit

Changed:
SL: 62,000 → 65,020 BE *

────────────────
Source: trader_update
* Changed from original signal
────────────────
https://t.me/c/3927267771/220
```

### 6.4 UPDATE_DONE — operation_rules / engine

```text
✅ #145 — UPDATE DONE
────────────────
BTC/USDT — 📈 LONG

Operation:
▪️ Cancel remaining pending entry
▪️ Move SL to BE

Cancelled:
Entry_2: 64,000 Limit

Changed:
SL: 62,000 → 65,020 BE *

────────────────
Source: operation_rules
* Changed by rule after TP_1
```

### 6.5 UPDATE_DONE — TP modificati

```text
✅ #145 — UPDATE DONE
────────────────
BTC/USDT — 📈 LONG

Operation:
▪️ Modify take profits
▪️ Move SL

Changed:
TP_1: 68,000 → 67,500 *
TP_2: 71,000 → 70,500 *
SL: 62,000 → 65,000 *

────────────────
Source: trader_update
* Changed from original signal
────────────────
https://t.me/c/3927267771/221
```

### 6.6 UPDATE_PARTIAL

```text
⚠️ #145 — UPDATE PARTIAL
────────────────
BTC/USDT — 📈 LONG

Applied:
▪️ Cancel pending entry

Rejected:
▪️ Move SL to BE
Reason: entry is not filled yet

Cancelled:
Entry_2: 64,000 Limit

────────────────
Source: trader_update
────────────────
https://t.me/c/3927267771/222
```

### 6.7 UPDATE_REJECTED

```text
❌ #145 — UPDATE REJECTED
────────────────
BTC/USDT — 📈 LONG

Operation:
▪️ Move SL to BE
▪️ Cancel pending entry

Rejected:
Entry is not filled and no pending order exists.

Reason: no_applicable_target
Source: trader_update
────────────────
https://t.me/c/3927267771/223
```

---

## 7. Multi-Chain Updates

### 7.1 Regola dominio

```text
1 source update → N per-chain update results
```

Ogni chain mantiene la propria cronologia CLEAN_LOG.
La reply va sotto il root message corretto di ogni chain.
Il source_message_link è uguale, i risultati operativi sono separati.

### 7.2 Policy di rendering

```text
Update colpisce 1 chain     → 1 reply sotto quella chain
Update colpisce 2-3 chain   → 1 reply per ogni chain
Update colpisce >3 chain    → 1 summary aggregato nel CLEAN_LOG
                               + reply dettagliate per REJECTED/PARTIAL/rischio operativo
                               + reply compatte per DONE opzionali (per policy rumore)
```

### 7.3 Esempio 2 chain — esiti uguali

Messaggio: `Move all BTC and ETH stops to BE`

Reply sotto `#145`:

```text
✅ #145 — UPDATE DONE
────────────────
BTC/USDT — 📈 LONG

Operation:
▪️ Move SL to BE

Changed:
SL: 62,000 → 65,020 BE *

────────────────
Source: trader_update
* Changed from original signal
────────────────
https://t.me/c/3927267771/220
```

Reply sotto `#148`:

```text
✅ #148 — UPDATE DONE
────────────────
ETH/USDT — 📈 LONG

Operation:
▪️ Move SL to BE

Changed:
SL: 3,280 → 3,410 BE *

────────────────
Source: trader_update
* Changed from original signal
────────────────
https://t.me/c/3927267771/220
```

### 7.4 Esempio multi-chain con esiti diversi

Messaggio: `Move all longs to BE`

```text
#145 BTC LONG → DONE
#148 ETH LONG → DONE
#151 SOL LONG → REJECTED (entry non fillata)
```

Reply sotto `#145` e `#148` come sopra. Reply sotto `#151`:

```text
❌ #151 — UPDATE REJECTED
────────────────
SOL/USDT — 📈 LONG

Operation:
▪️ Move SL to BE

Rejected:
Cannot move SL to BE because entry is not filled.

────────────────
Source: trader_update
Reason: position_not_open
────────────────
https://t.me/c/3927267771/220
```

### 7.5 Summary oltre soglia

Per update su molte chain (es. `Close all open shorts`):

**Update generico (senza close totale o PnL non ancora confermato):**

```text
🔁 UPDATE APPLIED — MULTI CHAIN
────────────────
Operation:
▪️ Close full position

Affected chains:
#160 BTC/USDT SHORT — DONE
#161 ETH/USDT SHORT — DONE
#162 SOL/USDT SHORT — DONE
#163 XRP/USDT SHORT — REJECTED
#164 DOGE/USDT SHORT — DONE

Summary:
Done: 4
Rejected: 1

────────────────
Source: trader_update
────────────────
https://t.me/c/3927267771/250
```

**Close totale con PnL consolidato** (fill confermati dall'exchange):

```text
🔁 MULTI CHAIN CLOSED
────────────────
Operation:
▪️ Close all positions

Chains closed: 4
#160 BTC/USDT SHORT | +83.20 USDT | +3.19%
#161 ETH/USDT SHORT | +31.50 USDT | +1.32%
#162 SOL/USDT SHORT | -12.40 USDT | -1.10%
#164 DOGE/USDT SHORT | +5.20 USDT | +0.90%

Rejected: 1
#163 XRP/USDT SHORT — position not found

────────────────
Aggregate Result:
Gross PnL: +107.50 USDT
Fees: -8.40 USDT
Funding: +0.00 USDT
Net PnL: +99.10 USDT

────────────────
Close reason: MANUAL_CLOSE
Source: trader_update
────────────────
https://t.me/c/3927267771/250
```

Il PnL aggregato si mostra solo quando:

```text
- l'operazione è CLOSE_FULL o CLOSE_PARTIAL che azzera la posizione
- i fill exchange sono confermati al momento del rendering
- almeno una chain ha PnL calcolabile
```

Se i fill non sono ancora confermati, inviare il summary senza PnL e aggiornare
le reply per-chain quando i fill arrivano dall'exchange.

Poi reply dettagliate obbligatorie per le chain con problemi:

```text
❌ #163 — UPDATE REJECTED
────────────────
XRP/USDT — 📉 SHORT

Operation:
▪️ Close full position

Rejected:
No open position found on exchange.

────────────────
Source: trader_update
Reason: position_not_found
────────────────
https://t.me/c/3927267771/250
```

---

## 8. Target Multipli

### 8.1 Principio

```text
DB atomico, Telegram compatto.
```

Nel DB: TP_1, TP_2, TP_3 sono entità separate con ref persistente.
Nel CLEAN_LOG: possono essere aggregati in un messaggio se cadono nello stesso batch/debounce.

### 8.2 Ref target obbligatori nel DB

```text
target_ref: TP_1 | TP_2 | TP_3 (non usare ref generici)
tp_price
tp_close_pct
tp_order_id
tp_fill_price
tp_fill_qty
tp_gross_pnl
tp_fee
tp_filled_at
```

### 8.3 TP singolo

```text
📊 #145 — TP1 FILLED
────────────────
BTC/USDT — 📈 LONG

TP_1: 68,000
Closed: 30%
PnL: +70.20 USDT
Fee: 1.10 USDT

Remaining:
Position: 70%
SL: 65,020 BE

────────────────
Source: exchange
```

### 8.4 TP multipli nello stesso batch

```text
📊 #145 — TP1 + TP2 FILLED
────────────────
BTC/USDT — 📈 LONG

Filled targets:
TP_1: 68,000 | Closed: 30% | PnL: +70.20 USDT | Fee: 1.10 USDT
TP_2: 71,000 | Closed: 40% | PnL: +165.40 USDT | Fee: 1.65 USDT

Total:
Closed: 70%
PnL: +235.60 USDT
Fees: 2.75 USDT

Remaining:
Position: 30%
SL: 65,020 BE

────────────────
Source: exchange
```

### 8.5 TP finale — chiusura posizione

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
Close reason: TAKE_PROFIT
Source: exchange
```

### 8.6 Grouping key

```text
group_key = chain_id + TARGET_FILLED + source + debounce_window
```

Non aggregare se:
- I target cadono in momenti distanti
- Tra TP_1 e TP_2 avviene un update importante
- Cambia SL/BE manualmente tra i fill
- Uno dei target produce errore o reconciliation warning

---

## 9. SL / BE / Close

### 9.1 BE incluso in TP

Se BE è attivato nello stesso ciclo di TP, non inviare `BE ACTIVATED` separato.

```text
📊 #145 — TP1 FILLED
────────────────
BTC/USDT — 📈 LONG

TP_1: 68,000
Closed: 50%
PnL: +118.40 USDT
Fee: 1.80 USDT

Changed:
SL: 62,000 → 65,020 BE *

Remaining:
Position: 50%

────────────────
Source: exchange + operation_rules
* Changed by rule after TP_1
```

Inviare `BE ACTIVATED` separato solo se accade in un momento diverso, senza essere collegato a un TP.

### 9.2 SL_FILLED

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
Close reason: STOP_LOSS
Source: exchange
```

### 9.3 BE_EXIT

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
Close reason: BREAKEVEN_AFTER_TP
Source: exchange
```

### 9.4 CLOSE_REQUESTED (opzionale)

Mandare solo se tra richiesta e fill passa tempo significativo.
Se il fill arriva subito, saltare e mandare direttamente `POSITION CLOSED`.

```text
✅ #145 — CLOSE REQUESTED
────────────────
BTC/USDT — 📈 LONG

Requested:
Close 100%

Reason:
Trader close command

────────────────
Source: trader_update
────────────────
https://t.me/c/3927267771/240
```

### 9.5 POSITION_CLOSED — manual close

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
Close reason: MANUAL_CLOSE
Source: exchange
```

---

## 10. Reconciliation

### 10.1 RECONCILIATION_WARNING

Solo se c'è rischio operativo reale. Non per differenze tecniche innocue.

```text
⚠️ #145 — RECONCILIATION WARNING
────────────────
BTC/USDT — 📈 LONG

Issue:
Expected SL not found on exchange.

Risk:
Position may be unprotected.

Action:
manual review required

────────────────
Source: reconciliation
```

### 10.2 RECONCILIATION_FIXED

```text
✅ #145 — RECONCILIATION FIXED
────────────────
BTC/USDT — 📈 LONG

Fixed:
Missing SL restored on exchange.

Protection:
SL: 65,020 BE

────────────────
Source: reconciliation
```

---

## 11. Casi Complessi

### 11.1 Sequenza multi-step: SL → BE → TP → close finale

Un trade può attraversare molti step. Ogni messaggio è reply al root.

Timeline completa:

```text
1. ✅ #145 — SIGNAL ACCEPTED      → root message, nuovo nel CLEAN_LOG
2. 📊 #145 — ENTRY OPENED         → reply, entry market fillata
3. 📊 #145 — ENTRY UPDATED        → reply, entry limit fillata
4. ✅ #145 — UPDATE DONE          → reply, trader sposta SL + cancella pending
   - Cancel pending entry
   - Move SL: 62,000 → 63,500
5. 📊 #145 — TP1 FILLED           → reply, TP1 hit + BE automatico incluso
   Changed: SL: 63,500 → 65,020 BE *
6. 📊 #145 — TP2 FILLED — POSITION CLOSED  → reply, chiusura + final result
```

Nota: il messaggio 5 assorbe il BE automatico (source: exchange + operation_rules).
Non creare un messaggio separato per BE se è nello stesso ciclo del TP.

### 11.2 Re-entry dopo chiusura

Un trader può segnalare un rientro su un simbolo già chiuso.
Il re-entry crea sempre una **nuova chain** (nuovo chain_id).

```text
Chain #145 chiusa → chain #145 è CLOSED nel DB
Re-entry signal → nuova chain #178 (stesso simbolo, stesso trader)
```

CLEAN_LOG per il re-entry: nuovo messaggio root (non reply alla chain chiusa).

```text
✅ #178 — REENTRY ACCEPTED
────────────────
BTC/USDT — 📈 LONG

Entry_1: 64,500 Limit
SL: 62,000
TP_1: 68,000

────────────────
Trader: trader_a
Previous chain: #145 (closed)
Source: trader_update
────────────────
https://t.me/c/3927267771/280
```

Il campo `Previous chain:` è opzionale ma utile per tracciare la continuità operativa.

Se il re-entry viene rifiutato (es. chain precedente non ancora chiusa, position limit):

```text
❌ #0 — REENTRY REJECTED
────────────────
BTC/USDT — 📈 LONG

Reason:
Previous chain #145 is not fully closed yet.

────────────────
Trader: trader_a
Source: trader_update
────────────────
https://t.me/c/3927267771/280
```

### 11.3 Entry parzialmente fillata + cancellazione

Caso: entry limit parzialmente fillata, poi arriva update del trader che cancella.

```text
Stato: Entry_1 fillata al 100%, Entry_2 fillata al 30%, poi cancellata.
```

Sequenza messaggi:

```text
1. ✅ #150 — SIGNAL ACCEPTED
2. 📊 #150 — ENTRY OPENED     → Entry_1 fillata, posizione aperta parzialmente
3. 📊 #150 — ENTRY UPDATED    → Entry_2 fillata al 30% (sopra soglia min_partial_fill_notify_pct)
4. 📊 #150 — ENTRY CANCELLED  → Entry_2 cancellata prima del fill completo
```

Template `ENTRY CANCELLED` (reply al root):

```text
📊 #150 — ENTRY CANCELLED
────────────────
BTC/USDT — 📈 LONG

Cancelled:
Entry_2: 64,000 Limit
Partial fill: 30% (0.002 BTC kept)

Position:
Avg entry: 64,820
Total filled: 0.006 BTC
Pending: none

────────────────
Source: trader_update
────────────────
https://t.me/c/3927267771/290
```

Se la cancellazione avviene prima di qualsiasi fill:

```text
📊 #150 — ENTRY CANCELLED
────────────────
BTC/USDT — 📈 LONG

Cancelled:
Entry_2: 64,000 Limit
Fill: 0% (no fill occurred)

Position:
Avg entry: 65,020 (Entry_1 only)
Total filled: 0.004 BTC
Pending: none

────────────────
Source: trader_update
────────────────
https://t.me/c/3927267771/290
```

### 11.4 Timeout automatico su pending entry

Il `timeout_worker` cancella automaticamente ordini pending scaduti.

Caso: Entry_2 non fillata entro `pending_timeout_hours`.

Template (reply al root):

```text
📊 #145 — PENDING ENTRY EXPIRED
────────────────
BTC/USDT — 📈 LONG

Expired:
Entry_2: 64,000 Limit
Open since: 24h (timeout exceeded)

Action:
Order cancellation sent to exchange.

Position:
Avg entry: 65,020 (Entry_1 only)
Total filled: 0.004 BTC
Pending: none

────────────────
Source: timeout_worker
```

Se la cancellazione sull'exchange conferma:

Nessun messaggio aggiuntivo — il timeout message è già completo.

Se la cancellazione fallisce (exchange error):

Il fallimento va in TECH_LOG, non in CLEAN_LOG.
Se dopo retry il cancel riesce, il messaggio timeout è sufficiente.
Se dopo tutti i retry il cancel non riesce, aggiungere una reply:

```text
⚠️ #145 — CANCEL FAILED
────────────────
BTC/USDT — 📈 LONG

Issue:
Cancellation of Entry_2 failed after 3 attempts.
Order may still be active on exchange.

Action:
manual review required

────────────────
Source: timeout_worker
```

---

## 12. Controlli Runtime Globali

Questi messaggi vengono inviati come **nuovi messaggi** nel CLEAN_LOG (non come reply).

### 12.1 EXECUTION_PAUSED

```text
⏸️ EXECUTION PAUSED
────────────────
Scope: GLOBAL
Mode: BLOCK_NEW_ENTRIES

Effect:
New signals go to review.
Open positions, updates and reconciliation remain active.

────────────────
Source: manual_command
Command: /pause
```

### 12.2 EXECUTION_RESUMED

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

## 13. Final Result

Il risultato finale va nel messaggio che chiude la posizione:

```text
TP_FILLED_FINAL
SL_FILLED
BE_EXIT
POSITION_CLOSED
```

Non inviare `CHAIN_COMPLETED` separato.

Campi obbligatori:

```text
ROI net (solo se allocated_margin disponibile)
Total PnL net
Gross PnL
Fees
Funding
Close reason: TAKE_PROFIT | STOP_LOSS | BREAKEVEN_AFTER_TP | MANUAL_CLOSE | TIMEOUT
```

Formula:

```text
Total PnL net = Gross PnL - Fees + Funding
ROI net = Total PnL net / allocated_margin
```

---

## 14. Dati da Salvare per Supportare Aggregazione

### 14.1 Tabella clean log tracking

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

### 14.2 Per update compositi

```text
chain_id
source_message_id
source_message_link
update_group_id
applied_actions[]
rejected_actions[]
affected_entry_refs[]
affected_sl_ref
affected_tp_refs[]
changed_fields[]
reason
source
created_at
```

### 14.3 Per update multi-chain

```text
source_update_id
source_message_id
source_message_link
update_group_id
affected_chain_ids[]
summary_status
per_chain_result[]:
  chain_id
  symbol
  side
  result_status: DONE | PARTIAL | REJECTED | SKIPPED
  applied_actions[]
  rejected_actions[]
  reason
  clean_log_message_id
```

### 14.4 Per target multipli

```text
target_ref: TP_1 | TP_2 | TP_3
tp_price
tp_close_pct
tp_order_id
tp_fill_price
tp_fill_qty
tp_gross_pnl
tp_fee
tp_funded_delta (se disponibile)
tp_filled_at
tp_status: PENDING | FILLED | CANCELLED | MODIFIED
```

---

## 15. Config Aggregazione

```yaml
clean_log:
  debounce_seconds: 20
  aggregate_fills_seconds: 30
  aggregate_updates_seconds: 20
  max_messages_per_chain_per_minute: 4
  min_partial_fill_notify_pct: 10      # sotto questa soglia, fill parziale non notificato
```

---

## 16. Esempi di Timeline Completa

### 16.1 Trade normale: market + limit + due TP

```text
1. ✅ #145 — SIGNAL ACCEPTED
2. 📊 #145 — ENTRY OPENED
3. 📊 #145 — ENTRY UPDATED
4. 📊 #145 — TP1 FILLED
5. 📊 #145 — TP2 FILLED — POSITION CLOSED
```

### 16.2 Trade con update multiplo

```text
1. ✅ #145 — SIGNAL ACCEPTED
2. 📊 #145 — ENTRY OPENED
3. ✅ #145 — UPDATE DONE (cancel pending + move SL)
4. 📊 #145 — TP1 FILLED (+ BE automatico)
5. 📊 #145 — BE EXIT — POSITION CLOSED
```

### 16.3 Trade con target multipli nello stesso sync

```text
1. ✅ #145 — SIGNAL ACCEPTED
2. 📊 #145 — ENTRY OPENED
3. 📊 #145 — TP1 + TP2 FILLED
4. 📊 #145 — TP3 FILLED — POSITION CLOSED
```

### 16.4 Trade respinto

```text
1. ❌ #146 — SIGNAL REJECTED
```

### 16.5 Trade con reconciliation

```text
1. ✅ #145 — SIGNAL ACCEPTED
2. 📊 #145 — ENTRY OPENED
3. ⚠️ #145 — RECONCILIATION WARNING
4. ✅ #145 — RECONCILIATION FIXED
5. 📊 #145 — POSITION CLOSED
```

### 16.6 Trade con re-entry

```text
Chain #145:
1. ✅ #145 — SIGNAL ACCEPTED
2. 📊 #145 — ENTRY OPENED
3. 🛑 #145 — SL FILLED — POSITION CLOSED

Chain #178 (re-entry):
4. ✅ #178 — REENTRY ACCEPTED
5. 📊 #178 — ENTRY OPENED
6. 📊 #178 — TP1 FILLED — POSITION CLOSED
```

### 16.7 Trade con entry cancellata parzialmente

```text
1. ✅ #150 — SIGNAL ACCEPTED
2. 📊 #150 — ENTRY OPENED
3. 📊 #150 — ENTRY CANCELLED (Entry_2 cancellata, 30% già fillato)
4. 📊 #150 — TP1 FILLED — POSITION CLOSED
```

### 16.8 Trade con timeout pending

```text
1. ✅ #152 — SIGNAL ACCEPTED
2. 📊 #152 — ENTRY OPENED
3. 📊 #152 — PENDING ENTRY EXPIRED (Entry_2 scaduta dopo 24h)
4. 📊 #152 — TP1 FILLED — POSITION CLOSED
```

### 16.9 Update multi-chain

```text
🔁 UPDATE APPLIED — MULTI CHAIN      (se >3 chain colpite)
✅ #160 — UPDATE DONE                (reply per chain DONE)
❌ #163 — UPDATE REJECTED            (sempre dettaglio per errori)
```

---

## 17. Estensibilità

Il sistema CLEAN_LOG è progettato per crescere senza richiedere refactoring strutturali.

### 17.1 Aggiungere un nuovo tipo di evento

1. Aggiungere una riga nella mappa eventi (sezione 2) con policy `off` inizialmente
2. Scrivere il formatter dedicato in `formatters/`
3. Cambiare la policy quando la feature è pronta

Non toccare il dispatcher, l'outbox, o altri formatter.

### 17.2 Funzionalità exchange non ancora implementate

Esempi di eventi che il dominio potrà produrre in futuro:

```text
FUNDING_CHARGED         → off (dati funding granulari per TP)
LIQUIDATION_WARNING     → off (margin call / liquidation risk)
PARTIAL_CLOSE_FILLED    → aggregate (close parziale confermata exchange)
ORDER_MODIFIED          → aggregate (ordine SL/TP modificato exchange-side)
```

Questi entrano nella mappa con `off` e non inviano nulla finché non implementati.
Il dominio può iniziare a scrivere gli eventi in outbox anche prima.

### 17.3 Aggiungere un nuovo trader o exchange

Nessun impatto sul CLEAN_LOG. I formatter usano `symbol`, `side`, `chain_id` — agnostici rispetto a trader o exchange specifici.

### 17.4 Aggiungere un nuovo topic o canale

Il `TopicRouter` centralizza la scelta del topic. Per aggiungere un quarto canale (es. `REPORT_LOG`):

1. Aggiungere la config in `telegram_control.yaml`
2. Aggiungere una `destination` nella tabella `ops_notification_outbox`
3. Aggiornare `TopicRouter` con la nuova destinazione
4. Creare i formatter dedicati

Gli eventi esistenti non cambiano.

---

## 18. Acceptance Criteria

```text
1.  CLEAN_LOG mostra solo eventi dominio trade, mai messaggi del logger tecnico.
2.  CLEAN_LOG non contiene State: visibile nei messaggi.
3.  SIGNAL_ACCEPTED, REJECTED, REVIEW_REQUIRED sono sempre inviati.
4.  ENTRY_ORDER_PLACED non genera messaggio.
5.  ENTRY_OPENED aggrega entry, SL, TP attachments.
6.  Update multipli sulla stessa chain nello stesso batch → 1 messaggio composito.
7.  Multi-chain update → reply separate per chain, summary opzionale oltre 3.
8.  TP multipli nello stesso batch → 1 messaggio aggregato.
9.  BE automatico dopo TP → incluso nel TP message, non separato.
10. Final result incluso nel messaggio di chiusura.
11. Re-entry crea nuova chain con messaggio REENTRY ACCEPTED.
12. Entry cancellata parzialmente → messaggio ENTRY CANCELLED con dettaglio fill.
13. Timeout pending → messaggio PENDING ENTRY EXPIRED.
14. Sequenza multi-step SL→BE→TP→close produce timeline leggibile ≤6 messaggi.
15. Comandi /pause /resume non appaiono in CLEAN_LOG.
16. Nessun messaggio contiene JSON, traceback, order id tecnici.
```
