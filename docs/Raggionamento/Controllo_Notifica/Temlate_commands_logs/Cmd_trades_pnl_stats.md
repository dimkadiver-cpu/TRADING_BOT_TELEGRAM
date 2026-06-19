# Template — /trades · /pnl · /stats

---

## /trades

### Caso base — account intero, più trade aperti

```
📊 TRADES — demo_1
────────────────
Updated: 14:32:05  |  Snapshot: 18s fa

#5  📈 BTCUSDT   LONG    OPEN
    Entry: 63,500  SL: 62,800  BE: ✓
    Qty: 0.0100  |  PnL: +12.40 USDT

#7  📉 ETHUSDT   SHORT   OPEN
    Entry: 2,140   SL: 2,180
    Qty: 0.5000  |  PnL: -3.20 USDT

#9  📈 SOLUSDT   LONG    PARTIALLY_CLOSED
    Entry: 148.50  SL: 143.00
    Qty: 2.0000  |  PnL: +5.80 USDT
────────────────
/trade #id  ·  /close <symbol>  ·  /cancel_all
```

> `BE: ✓` compare solo se `be_protection_status = PROTECTED`.
> Snapshot age = secondi da `captured_at` di `ops_position_snapshots`.
> PnL = unrealized da position snapshot, non calcolato live.

---

### Con filtro trader — /trades trader_a

```
📊 TRADES — demo_1 · trader_a
────────────────
Updated: 14:32:05  |  Snapshot: 18s fa

#5  📈 BTCUSDT   LONG    OPEN
    Entry: 63,500  SL: 62,800  BE: ✓
    Qty: 0.0100  |  PnL: +12.40 USDT
────────────────
/trade #id  ·  /close <symbol>  ·  /cancel_all
```

---

### Snapshot stale (>120s)

```
📊 TRADES — demo_1
────────────────
Updated: 14:32:05  |  Snapshot: 183s fa

#5  📈 BTCUSDT   LONG    OPEN
    Entry: 63,500  SL: 62,800
    Qty: 0.0100  |  PnL: +12.40 USDT

⚠️ Snapshot >120s — PnL non aggiornato
────────────────
/trade #id  ·  /close <symbol>  ·  /cancel_all
```

---

### Nessun trade aperto

```
📊 TRADES — demo_1
────────────────
Updated: 14:32:05

Nessun trade aperto.
────────────────
/status  ·  /stats
```

---

### Con WAITING_ENTRY (ordine in attesa, non ancora riempito)

```
📊 TRADES — demo_1
────────────────
Updated: 14:32:05  |  Snapshot: 22s fa

#5  📈 BTCUSDT   LONG    OPEN
    Entry: 63,500  SL: 62,800
    Qty: 0.0100  |  PnL: +12.40 USDT

#6  📈 NEARUSDT  LONG    WAITING_ENTRY
    Entry attesa: 4.820  SL: 4.650
    Qty: —  |  PnL: —
────────────────
/trade #id  ·  /close <symbol>  ·  /cancel_all
```

> WAITING_ENTRY non ha PnL — mostra `—`.
> Entry price = `expected_entry_price` dal plan, non fill reale.

---

---

## /pnl

### Caso base — account intero

```
💰 PNL — demo_1
────────────────
Account: master_account  |  14:32:05

Snapshot account:
  Equity:          10,432.50 USDT
  Balance:          9,100.00 USDT
  Margin usato:       820.00 USDT
  Snapshot: 14:28:41

Realizzato (trade chiusi):
  Gross PnL:         +234.80 USDT
  Fees:               -18.40 USDT
  Funding:             -2.10 USDT
  ──────────────────────────
  Netto:             +214.30 USDT

Open: 3  |  Partial: 1  |  Waiting: 2
```

---

### Con filtro trader — /pnl trader_a

```
💰 PNL — demo_1 · trader_a
────────────────
Account: master_account  |  14:32:05

Snapshot account: (dati account, non per-trader)
  Equity:          10,432.50 USDT
  Balance:          9,100.00 USDT
  Margin usato:       820.00 USDT

Realizzato trader_a (trade chiusi):
  Gross PnL:         +142.60 USDT
  Fees:               -11.20 USDT
  Funding:             -1.40 USDT
  ──────────────────────────
  Netto:             +130.00 USDT

Open: 1  |  Partial: 0  |  Waiting: 1
```

> Snapshot account è sempre per-account (non filtrabile per trader — è dati exchange).
> La sezione "Realizzato" filtra per trader_id su ops_trade_chains.

---

### Nessun snapshot account disponibile

```
💰 PNL — demo_1
────────────────
Account: master_account  |  14:32:05

Snapshot account:
  n/a — nessun snapshot disponibile

Realizzato (trade chiusi):
  Gross PnL:         +234.80 USDT
  Fees:               -18.40 USDT
  Netto:             +214.30 USDT

Open: 3  |  Partial: 1  |  Waiting: 0
```

---

### Nessun trade chiuso (DB vuoto o nuovo account)

```
💰 PNL — demo_1
────────────────
Account: master_account  |  14:32:05

Snapshot account:
  Equity:          10,000.00 USDT
  Balance:         10,000.00 USDT
  Margin usato:         0.00 USDT

Realizzato (trade chiusi):
  Nessun trade chiuso.

Open: 0  |  Partial: 0  |  Waiting: 0
```

---

---

## /stats

### Caso base — account intero

```
📈 STATS — demo_1
────────────────
           Trades   Win%    PnL netto    Fees
Oggi:           3    67%      +42.10     -3.20
7 giorni:      18    61%     +180.40    -14.50
30 giorni:     52    58%     +420.80    -38.20
Totale:        87    59%     +214.30    -62.40
────────────────
Best:   #12  BTCUSDT   +89.20 USDT
Worst:  #31  ETHUSDT   -45.10 USDT
────────────────
/stats trader_a  per filtrare per trader
```

> Win% = trade con cumulative_gross_pnl > 0 / totale trade CLOSED.
> PnL netto = gross - fees - funding.
> Best/Worst calcolati sul totale storico, non sulla finestra.

---

### Con filtro trader — /stats trader_b

```
📈 STATS — demo_1 · trader_b
────────────────
           Trades   Win%    PnL netto    Fees
Oggi:           1   100%      +18.40     -1.20
7 giorni:       6    67%      +62.10     -5.40
30 giorni:     19    63%     +148.30    -14.80
Totale:        31    61%      +98.20    -22.10
────────────────
Best:   #8   SOLUSDT   +34.50 USDT
Worst:  #22  BNBUSDT   -12.80 USDT
────────────────
```

---

### Nessun trade nella finestra (ma dati storici esistono)

```
📈 STATS — demo_1
────────────────
           Trades   Win%    PnL netto    Fees
Oggi:           0     —          —          —
7 giorni:       2    50%      +12.40     -2.10
30 giorni:     14    57%      +88.30    -10.20
Totale:        42    60%     +214.30    -38.40
────────────────
Best:   #12  BTCUSDT   +89.20 USDT
Worst:  #31  ETHUSDT   -45.10 USDT
```

> Se la finestra ha 0 trade, Win% e PnL mostrano `—`.

---

### Nessun trade in assoluto

```
📈 STATS — demo_1
────────────────
Nessun trade chiuso — statistiche non disponibili.
```
