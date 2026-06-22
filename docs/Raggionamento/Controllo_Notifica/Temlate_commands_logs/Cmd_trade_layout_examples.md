# `/trades` e `/trade #n` — Esempi di layout renderizzati

Tutti gli esempi mostrano l'output testuale atteso nel topic Telegram.
I separatori usano `- - - - - - - - - - - - - - - - - - - -`.

---

## `/trades` — casi base

### Account scope, nessun filtro

```
📊 TRADES — demo_1
- - - - - - - - - - - - - - - - - - - -
Total: 3   Updated: 14:32:05
- - - - - - - - - - - - - - - - - - - -
#5 · BTC/USDT · LONG · OPEN
uPnL: +12.40 USDT  rPnL: +0.00 USDT
Details: /trade #5
- - - - - - - - - - - - - - - - - - - -
#7 · ETH/USDT · SHORT · OPEN
uPnL: -3.20 USDT  rPnL: -0.20 USDT
Details: /trade #7
- - - - - - - - - - - - - - - - - - - -
#9 · SOL/USDT · LONG · WAITING_ENTRY
rPnL: —
Details: /trade #9
```

### Account scope, filtro trader

```
📊 TRADES — demo_1 · trader_a
- - - - - - - - - - - - - - - - - - - -
Total: 1   Updated: 14:32:05
- - - - - - - - - - - - - - - - - - - -
#5 · BTC/USDT · LONG · PARTIALLY_CLOSED
uPnL: +34.20 USDT  rPnL: +14.20 USDT
Details: /trade #5
```

### Empty state

```
📊 TRADES — demo_1
- - - - - - - - - - - - - - - - - - - -
Total: 0   Updated: 14:32:05
- - - - - - - - - - - - - - - - - - - -
No trades in scope.
```

---

## `/trades` — global scope

### Header

```
📊 TRADES — All accounts
- - - - - - - - - - - - - - - - - - - -
Total: 27   Updated: 14:32:05
Filters: All accounts · All traders
Order: Updated desc
- - - - - - - - - - - - - - - - - - - -
```

### Esempio completo

```
📊 TRADES — All accounts
- - - - - - - - - - - - - - - - - - - -
Total: 27   Updated: 14:32:05
Filters: All accounts · All traders
Order: Updated desc
- - - - - - - - - - - - - - - - - - - -
#5 · BTC/USDT · LONG · PARTIALLY_CLOSED
Trader: trader_devos_crypto · Account: demo_2
uPnL: +34.20 USDT  rPnL: +14.20 USDT
Details: /trade #5
- - - - - - - - - - - - - - - - - - - -
#17 · ETH/USDT · SHORT · OPEN
Trader: trader_alpha · Account: demo_1
uPnL: -3.20 USDT  rPnL: -0.20 USDT
Details: /trade #17
- - - - - - - - - - - - - - - - - - - -
#22 · SOL/USDT · LONG · WAITING_ENTRY
Trader: trader_beta · Account: demo_3
rPnL: —
Details: /trade #22
```

### Variante ordinamento chain

```
📊 TRADES — All accounts
- - - - - - - - - - - - - - - - - - - -
Total: 27   Updated: 14:32:05
Filters: All accounts · All traders
Order: Chain updated desc
- - - - - - - - - - - - - - - - - - - -
#5 · BTC/USDT · LONG · PARTIALLY_CLOSED
Trader: trader_devos_crypto · Account: demo_2
uPnL: +34.20 USDT  rPnL: +14.20 USDT
Details: /trade #5
- - - - - - - - - - - - - - - - - - - -
#44 · XRP/USDT · SHORT · OPEN
Trader: trader_gamma · Account: demo_1
uPnL: +4.10 USDT  rPnL: +0.00 USDT
Details: /trade #44
```

---

## `/trade #n` — WAITING_ENTRY

```
#9 · BTC/USDT · LONG · WAITING_ENTRY
- - - - - - - - - - - - - - - - - - - -
Trader: trader_devos_crypto
Exchange Account: demo_2
Updated: 14:32:05
- - - - - - - - - - - - - - - - - - - -
Entry: 63,500 · 63,200 · 62,800
TP:    64,000 · 65,200 · 66,500
SL:    62,000 · BE: No
- - - - - - - - - - - - - - - - - - - -
Actions: /cancel_9
- - - - - - - - - - - - - - - - - - - -
Events:
• SIGNAL ACCEPTED · 14 Jun 09:10:00
  Source: Signal → [clean_log](url)
```

---

## `/trade #n` — OPEN

```
#5 · BTC/USDT · LONG · OPEN
- - - - - - - - - - - - - - - - - - - -
Trader: trader_devos_crypto
Exchange Account: demo_2
Updated: 14:32:05
- - - - - - - - - - - - - - - - - - - -
Entry: 63,500 ✓ · 63,200 ✗ · 62,800 ✗
TP:    64,000 · 65,200 · 66,500
SL:    62,000 · BE: No
- - - - - - - - - - - - - - - - - - - -
uPnL:  +18.40 USDT  rPnL:  +0.00 USDT
- - - - - - - - - - - - - - - - - - - -
Actions: /cancel_5 · /close_5
- - - - - - - - - - - - - - - - - - - -
Events:
• SIGNAL ACCEPTED · 14 Jun 09:10:00
  Source: Signal → [clean_log](url)

• ENTRY OPENED · 14 Jun 09:10:05
  Source: exchange → [clean_log](url)
```

---

## `/trade #n` — PARTIALLY_CLOSED

```
#5 · BTC/USDT · LONG · PARTIALLY_CLOSED
- - - - - - - - - - - - - - - - - - - -
Trader: trader_devos_crypto
Exchange Account: demo_2
Updated: 14:32:05
- - - - - - - - - - - - - - - - - - - -
Entry: 63,500 ✓ · 63,200 ✗ · 62,800 ✗
TP:    64,000 ✓ · 65,200 · 66,500
SL:    62,000 · BE: No
- - - - - - - - - - - - - - - - - - - -
uPnL:  +34.20 USDT  rPnL:  +14.20 USDT
- - - - - - - - - - - - - - - - - - - -
Actions: /cancel_5 · /close_5
- - - - - - - - - - - - - - - - - - - -
Events:
• SIGNAL ACCEPTED · 14 Jun 09:10:00
  Source: Signal → [clean_log](url)

• ENTRY OPENED · 14 Jun 09:10:05
  Source: exchange → [clean_log](url)

• TP1 FILLED · 14 Jun 09:15:20
  Source: exchange → [clean_log](url)

• UPDATE DONE · 14 Jun 09:20:00
  Type: CANCEL_PENDING
  Source: operation_rules → [clean_log](url)
```

---

## `/trade #n` — OPEN con BE attivo

```
#5 · BTC/USDT · LONG · OPEN
- - - - - - - - - - - - - - - - - - - -
Trader: trader_devos_crypto
Exchange Account: demo_2
Updated: 14:32:05
- - - - - - - - - - - - - - - - - - - -
Entry: 63,500 ✓ · 63,200 ✗ · 62,800 ✗
TP:    64,000 ✓ · 65,200 · 66,500
SL:    — · BE: 63,500
- - - - - - - - - - - - - - - - - - - -
uPnL:  +18.40 USDT  rPnL:  +14.20 USDT
- - - - - - - - - - - - - - - - - - - -
Actions: /cancel_5 · /close_5
- - - - - - - - - - - - - - - - - - - -
Events:
• SIGNAL ACCEPTED · 14 Jun 09:10:00
  Source: Signal → [clean_log](url)

• ENTRY OPENED · 14 Jun 09:10:05
  Source: exchange → [clean_log](url)

• TP1 FILLED · 14 Jun 09:15:20
  Source: exchange → [clean_log](url)

• SL MOVED TO BE · 14 Jun 09:16:00
  Source: operation_rules → [clean_log](url)
```

---

## `/trade #n` — REVIEW_REQUIRED

```
#7 · ETH/USDT · LONG · REVIEW_REQUIRED
- - - - - - - - - - - - - - - - - - - -
Trader: trader_devos_crypto
Exchange Account: demo_2
Updated: 14:32:05
- - - - - - - - - - - - - - - - - - - -
Entry: 2,140 ✓
TP:    2,180 · 2,220
SL:    —
- - - - - - - - - - - - - - - - - - - -
uPnL:  -3.20 USDT  rPnL:  0.00 USDT
- - - - - - - - - - - - - - - - - - - -
Actions: /close_7
- - - - - - - - - - - - - - - - - - - -
Events:
• SIGNAL ACCEPTED · 14 Jun 11:50:00
  Source: Signal → [clean_log](url)

• ENTRY OPENED · 14 Jun 11:52:00
  Source: exchange → [clean_log](url)

• REVIEW REQUIRED · 14 Jun 11:52:05
  Reason: missing_sl
  Source: system → [clean_log](url)
```

---

## `/trade #n` — POSITION CLOSED (tutti i TP riempiti)

L'ultimo evento è il TP finale — non esiste un evento separato "POSITION CLOSED" per chiusura via TP.

```
#5 · BTC/USDT · LONG · POSITION CLOSED
- - - - - - - - - - - - - - - - - - - -
Trader: trader_devos_crypto
Exchange Account: demo_2
Updated: 14:32:05
- - - - - - - - - - - - - - - - - - - -
Entry: 63,500 ✓ · 63,200 ✗ · 62,800 ✗
TP:    64,000 ✓ · 65,200 ✓
SL:    62,000 · BE: No
- - - - - - - - - - - - - - - - - - - -
Final Result:
ROI net: +3.67% · RoR: +9.12% · R: +0.22R
PnL net: +44.17 USDT · PnL gross: +45.20 USDT
Fees: -2.06 USDT · Funding: +0.03 USDT
- - - - - - - - - - - - - - - - - - - -
Events:
• SIGNAL ACCEPTED · 14 Jun 09:10:00
  Source: Signal → [clean_log](url)

• ENTRY OPENED · 14 Jun 09:10:05
  Source: exchange → [clean_log](url)

• TP1 FILLED · 14 Jun 09:15:20
  Source: exchange → [clean_log](url)

• UPDATE DONE · 14 Jun 09:20:00
  Type: CANCEL_PENDING
  Source: operation_rules → [clean_log](url)

• TP2 FILLED · 14 Jun 09:25:00
  Source: exchange → [clean_log](url)
```

---

## `/trade #n` — POSITION CLOSED (SL hit — stop loss normale)

```
#5 · BTC/USDT · LONG · POSITION CLOSED
- - - - - - - - - - - - - - - - - - - -
Trader: trader_devos_crypto
Exchange Account: demo_2
Updated: 14:32:05
- - - - - - - - - - - - - - - - - - - -
Entry: 63,500 ✓ · 63,200 ✗ · 62,800 ✗
TP:    64,000 · 65,200
SL:    62,000 · BE: No
- - - - - - - - - - - - - - - - - - - -
Final Result:
ROI net: -1.20% · RoR: -3.10% · R: -0.08R
PnL net: -14.80 USDT · PnL gross: -13.50 USDT
Fees: -1.30 USDT · Funding: +0.00 USDT
- - - - - - - - - - - - - - - - - - - -
Events:
• SIGNAL ACCEPTED · 14 Jun 09:10:00
  Source: Signal → [clean_log](url)

• ENTRY OPENED · 14 Jun 09:10:05
  Source: exchange → [clean_log](url)

• SL HIT · 14 Jun 09:22:10
  Reason: STOP_LOSS
  Source: exchange → [clean_log](url)
```

---

## `/trade #n` — POSITION CLOSED (SL hit — BE attivo dopo TP)

```
#5 · BTC/USDT · LONG · POSITION CLOSED
- - - - - - - - - - - - - - - - - - - -
Trader: trader_devos_crypto
Exchange Account: demo_2
Updated: 14:32:05
- - - - - - - - - - - - - - - - - - - -
Entry: 63,500 ✓ · 63,200 ✗ · 62,800 ✗
TP:    64,000 ✓ · 65,200
SL:    62,000 · BE: 63,500
- - - - - - - - - - - - - - - - - - - -
Final Result:
ROI net: +0.00% · RoR: +0.00% · R: +0.00R
PnL net: +0.20 USDT · PnL gross: +1.40 USDT
Fees: -1.20 USDT · Funding: +0.00 USDT
- - - - - - - - - - - - - - - - - - - -
Events:
• SIGNAL ACCEPTED · 14 Jun 09:10:00
  Source: Signal → [clean_log](url)

• ENTRY OPENED · 14 Jun 09:10:05
  Source: exchange → [clean_log](url)

• TP1 FILLED · 14 Jun 09:15:20
  Source: exchange → [clean_log](url)

• SL MOVED TO BE · 14 Jun 09:16:00
  Source: operation_rules → [clean_log](url)

• SL HIT · 14 Jun 09:25:00
  Reason: BREAKEVEN_AFTER_TP
  Source: exchange → [clean_log](url)
```

---

## `/trade #n` — POSITION CLOSED (chiusura manuale via `/close_n`)

```
#5 · BTC/USDT · LONG · POSITION CLOSED
- - - - - - - - - - - - - - - - - - - -
Trader: trader_devos_crypto
Exchange Account: demo_2
Updated: 14:32:05
- - - - - - - - - - - - - - - - - - - -
Entry: 63,500 ✓ · 63,200 ✗ · 62,800 ✗
TP:    64,000 ✓ · 65,200
SL:    62,000 · BE: No
- - - - - - - - - - - - - - - - - - - -
Final Result:
ROI net: +2.10% · RoR: +5.40% · R: +0.14R
PnL net: +26.50 USDT · PnL gross: +27.80 USDT
Fees: -1.30 USDT · Funding: +0.00 USDT
- - - - - - - - - - - - - - - - - - - -
Events:
• SIGNAL ACCEPTED · 14 Jun 09:10:00
  Source: Signal → [clean_log](url)

• ENTRY OPENED · 14 Jun 09:10:05
  Source: exchange → [clean_log](url)

• TP1 FILLED · 14 Jun 09:15:20
  Source: exchange → [clean_log](url)

• POSITION CLOSED · 14 Jun 09:30:00
  Reason: USER_MANUAL_CLOSE
  Source: trader_update → [clean_log](url)
```

---

## `/trade #n` — POSITION CLOSED (chiusura manuale dall'exchange)

```
#5 · BTC/USDT · LONG · POSITION CLOSED
- - - - - - - - - - - - - - - - - - - -
Trader: trader_devos_crypto
Exchange Account: demo_2
Updated: 14:32:05
- - - - - - - - - - - - - - - - - - - -
Entry: 63,500 ✓
TP:    64,000 · 65,200
SL:    62,000 · BE: No
- - - - - - - - - - - - - - - - - - - -
Final Result:
ROI net: +1.50% · RoR: +3.80% · R: +0.10R
PnL net: +18.20 USDT · PnL gross: +19.50 USDT
Fees: -1.30 USDT · Funding: +0.00 USDT
- - - - - - - - - - - - - - - - - - - -
Events:
• SIGNAL ACCEPTED · 14 Jun 09:10:00
  Source: Signal → [clean_log](url)

• ENTRY OPENED · 14 Jun 09:10:05
  Source: exchange → [clean_log](url)

• POSITION CLOSED · 14 Jun 09:30:00
  Reason: MANUAL_CLOSE
  Source: exchange_manual → [clean_log](url)
```

---

## `/trade #n` — POSITION CLOSED (liquidazione)

```
#5 · BTC/USDT · LONG · POSITION CLOSED
- - - - - - - - - - - - - - - - - - - -
Trader: trader_devos_crypto
Exchange Account: demo_2
Updated: 14:32:05
- - - - - - - - - - - - - - - - - - - -
Entry: 63,500 ✓
TP:    64,000 · 65,200
SL:    62,000 · BE: No
- - - - - - - - - - - - - - - - - - - -
Final Result:
ROI net: -8.40% · RoR: -21.20% · R: -0.56R
PnL net: -102.50 USDT · PnL gross: -100.80 USDT
Fees: -1.70 USDT · Funding: +0.00 USDT
- - - - - - - - - - - - - - - - - - - -
Events:
• SIGNAL ACCEPTED · 14 Jun 09:10:00
  Source: Signal → [clean_log](url)

• ENTRY OPENED · 14 Jun 09:10:05
  Source: exchange → [clean_log](url)

• POSITION CLOSED · 14 Jun 10:15:00
  Reason: LIQUIDATION
  Source: exchange → [clean_log](url)
```

---

## `/trade #n` — CANCELLED_UNFILLED

```
#24 · ETH/USDT · LONG · CANCELLED_UNFILLED
- - - - - - - - - - - - - - - - - - - -
Trader: trader_devos_crypto
Exchange Account: demo_2
Updated: 14:32:05
- - - - - - - - - - - - - - - - - - - -
Entry: 2,140 · 2,120
TP:    2,180 · 2,220
SL:    2,090 · BE: No
- - - - - - - - - - - - - - - - - - - -
Final Result:
PnL: No fill
- - - - - - - - - - - - - - - - - - - -
Events:
• SIGNAL ACCEPTED · 14 Jun 16:12:00
  Source: Signal → [clean_log](url)

• UPDATE DONE · 14 Jun 16:14:10
  Type: CANCEL_PENDING
  Source: operation_rules → [clean_log](url)

• POSITION CANCELLED · 14 Jun 16:14:12
  Reason: CANCEL_PENDING
  Source: exchange → [clean_log](url)
```

---

## Catalogo eventi timeline

Tutti gli eventi che possono comparire nella sezione `Events:` di `/trade #n`.
Ogni evento può avere campi opzionali: `Type:`, `Reason:`, `Note:`.

---

### SIGNAL ACCEPTED

Segnale ricevuto e accettato dal sistema.

```
• SIGNAL ACCEPTED · 14 Jun 09:10:00
  Source: Signal → [clean_log](url)
```

| Campo | Valore |
|---|---|
| Source | `Signal` |
| Reason | — |
| Type | — |

---

### ENTRY OPENED

Prima entry fill o fill successivo sullo stesso livello.

```
• ENTRY OPENED · 14 Jun 09:10:05
  Source: exchange → [clean_log](url)
```

| Campo | Valore |
|---|---|
| Source | `exchange` |
| Reason | — |
| Type | — |

---

### ENTRY PARTIALLY FILLED

Entry parzialmente riempita (fill qty inferiore alla qty pianificata per quel livello).

```
• ENTRY PARTIALLY FILLED · 14 Jun 09:10:05
  Source: exchange → [clean_log](url)
```

| Campo | Valore |
|---|---|
| Source | `exchange` |
| Reason | — |
| Type | — |

---

### TP1 FILLED / TP2 FILLED / ...

Take profit parziale. Il numero corrisponde al livello (1-based).

```
• TP1 FILLED · 14 Jun 09:15:20
  Source: exchange → [clean_log](url)

• TP2 FILLED · 14 Jun 09:25:00
  Source: exchange → [clean_log](url)
```

| Campo | Valore |
|---|---|
| Source | `exchange` |
| Reason | — |
| Type | — |

---

### SL MOVED TO BE

Stop loss spostato al break even da `operation_rules`. Appare in due casi:
- evento `SL_MOVED_TO_BE` diretto
- evento `STOP_MOVE_CONFIRMED` con `is_breakeven=true` nel payload

```
• SL MOVED TO BE · 14 Jun 09:16:00
  Source: operation_rules → [clean_log](url)
```

| Campo | Valore |
|---|---|
| Source | `operation_rules` |
| Reason | — |
| Type | — |

---

### SL UPDATED

Stop loss spostato a un livello non-BE (`STOP_MOVE_CONFIRMED` con `is_breakeven=false`).

```
• SL UPDATED · 14 Jun 09:18:00
  Source: operation_rules → [clean_log](url)
```

| Campo | Valore |
|---|---|
| Source | `operation_rules` |
| Reason | — |
| Type | — |

---

### UPDATE DONE

Aggiornamento applicato dalle operation_rules. `Type:` indica l'azione eseguita.

```
• UPDATE DONE · 14 Jun 09:20:00
  Type: CANCEL_PENDING
  Source: operation_rules → [clean_log](url)
```

```
• UPDATE DONE · 14 Jun 09:20:00
  Type: MOVE_SL_TO_BE
  Source: operation_rules → [clean_log](url)
```

| Campo | Valore |
|---|---|
| Source | `operation_rules` |
| Reason | — |
| Type | `CANCEL_PENDING` / `MOVE_SL_TO_BE` / altri |

---

### REVIEW REQUIRED

Trade bloccato in attesa di revisione manuale. `Reason:` indica il motivo.

```
• REVIEW REQUIRED · 14 Jun 11:52:05
  Reason: missing_sl
  Source: system → [clean_log](url)
```

| Campo | Valore |
|---|---|
| Source | `system` |
| Reason | `missing_sl` / altri |
| Type | — |

---

### SL HIT

Stop loss colpito dall'exchange. `Reason:` discrimina il tipo di chiusura.

```
• SL HIT · 14 Jun 09:22:10
  Reason: STOP_LOSS
  Source: exchange → [clean_log](url)
```

```
• SL HIT · 14 Jun 09:25:00
  Reason: BREAKEVEN_AFTER_TP
  Source: exchange → [clean_log](url)
```

```
• SL HIT · 14 Jun 09:25:00
  Reason: TRADER_COMMAND
  Source: trader_update → [clean_log](url)
```

| Reason | Causa |
|---|---|
| `STOP_LOSS` | SL normale colpito dall'exchange |
| `BREAKEVEN_AFTER_TP` | SL era al BE, colpito dopo almeno un TP |
| `TRADER_COMMAND` | SL chiuso via comando trader (`trader_update`) |

| Campo | Valore |
|---|---|
| Source | `exchange` oppure `trader_update` |

---

### POSITION CLOSED

Posizione chiusa per via non-TP e non-SL. `Reason:` indica chi ha chiuso e come.

```
• POSITION CLOSED · 14 Jun 09:30:00
  Reason: USER_MANUAL_CLOSE
  Source: trader_update → [clean_log](url)
```

```
• POSITION CLOSED · 14 Jun 09:30:00
  Reason: MANUAL_CLOSE
  Source: exchange_manual → [clean_log](url)
```

```
• POSITION CLOSED · 14 Jun 10:15:00
  Reason: LIQUIDATION
  Source: exchange → [clean_log](url)
```

| Reason | Causa | Source |
|---|---|---|
| `USER_MANUAL_CLOSE` | Chiusura via `/close_n` (bot) | `trader_update` |
| `MANUAL_CLOSE` | Chiusura manuale dall'interfaccia exchange | `exchange_manual` |
| `LIQUIDATION` | Liquidazione forzata dall'exchange | `exchange` |

> **Nota:** chiusura via TP finale non genera questo evento — l'ultimo evento visibile è `TP2 FILLED` (o il livello finale). Chiusura via SL non genera questo evento — usa `SL HIT`.

---

### POSITION CANCELLED

Posizione cancellata senza alcun fill. Compare solo per trade in stato `CANCELLED_UNFILLED`.

```
• POSITION CANCELLED · 14 Jun 16:14:12
  Reason: CANCEL_PENDING
  Source: exchange → [clean_log](url)
```

| Campo | Valore |
|---|---|
| Source | `exchange` |
| Reason | `CANCEL_PENDING` / altri cancel_reason |

---

## Riepilogo marcatori setup ordine

| Marcatore | Significato |
|---|---|
| ✓ | filled / colpito |
| ✗ | cancellato / saltato |
| *(nessuno)* | pending / ancora aperto |

## Matrice azioni per stato

| Stato | `/cancel_n` | `/close_n` |
|---|---|---|
| `WAITING_ENTRY` | ✓ | ✗ |
| `OPEN` | ✓ | ✓ |
| `PARTIALLY_CLOSED` | ✓ | ✓ |
| `REVIEW_REQUIRED` | ✗ | ✓ |
| `POSITION CLOSED` | ✗ | ✗ |
| `CANCELLED_UNFILLED` | ✗ | ✗ |

## Regole BE

| Condizione | Rendering SL |
|---|---|
| BE inattivo, SL presente | `SL:    62,000 · BE: No` |
| BE attivo | `SL:    — · BE: 63,500` |
| SL assente (REVIEW_REQUIRED) | `SL:    —` |
