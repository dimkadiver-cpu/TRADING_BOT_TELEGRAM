# ENTRY OPENED / ENTRY UPDATED — Esempi reali

Formato prodotto dal template system (`_ENTRY_BLOCKS`).
`- - - - - - - - - - - - - - - ` = SeparatorBlock dinamico (larghezza calcolata da `_finalize()`).

---

## Struttura

```
{emoji} #{chain_id} — ENTRY OPENED | ENTRY UPDATED
- - - - - - - - - - -
{symbol} — {side_emoji} {side}
- - - - - - - - - - -
Filled:
Entry_N: {fill_price} {type}
Qty: {filled_qty}                            ← full fill
Qty: {filled_qty} (planned: {planned_qty})   ← partial fill
Value: {exec_value} USDT
Fee rate: {fee_rate}%                        ← prima di Fee
Fee: {fee} USDT
[Partial: {leg_fill_pct}%]                   ← solo se parziale
- - - - - - - - - - -
Position:
Avg entry: {avg_entry}
Total qty: {total_filled_qty}
Total value: {total_value} USDT
Total fees: {total_fees} USDT
Filled: {position_filled_pct}%
Risk: {actual_risk} USDT (planned: {planned_risk} USDT)   ← sempre
[Pending: Entry_N {price} Limit]             ← una riga per entry pending
Pending: none
[- - - - - - - - - - -]
[Changed:]
[SL qty: {planned_qty} → {filled_qty} (adj. to fill)]     ← solo se parziale
- - - - - - - - - - -
Source: exchange
```

**Regole:**
- `Fee rate` prima di `Fee` — ordine nel template: Value → Fee rate → Fee
- `Risk` appare **sempre** (opzione B) — condizione: `actual_risk_usdt is not None`
- `actual_risk = filled_entry_qty × |avg_entry − sl_price|` — usa prezzi reali, sensibile a slippage
- `Total qty/value/fees` sempre presenti — ridondanti su fill singolo, espliciti su UPDATED multi-leg
- `Partial`, `Changed: SL qty` solo quando `is_partial_leg = True` (filled_qty < planned_qty del leg)
- `Pending:` una riga per entry — non indentate
- `avg_entry` = Σ(fill_price_i × qty_i) / Σ(qty_i) — sempre prezzi di esecuzione reali

---

## Setup numeri

Casi 1–8: BTC/USDT LONG, Entry ~65,000, SL 39,000, qty 0.010, rischio pianificato 260 USDT.
Casi 9–11: BTC/USDT LONG, Entry ~65,000, SL 63,500 (SL tight), qty 0.010, rischio pianificato 15 USDT.
Fee rate: 0.200% per tutti i casi.

---

## Caso 1 — ONE_SHOT MARKET, fill completo

Fill 65,020 (slippage +20). actual_risk = 0.010 × (65,020 − 39,000) = **260.20 USDT**
Total qty 0.010 / Total value 650.20 / Total fees 1.30

```
📊 #145 — ENTRY OPENED
- - - - - - - - - - -- - - - - - - - - - -
BTC/USDT — 📈 LONG
- - - - - - - - - - -- - - - - - - - - - -
Filled:
Entry_1: 65,020 Market
Qty: 0.010
Value: 650.20 USDT
Fee rate: 0.200%
Fee: 1.30 USDT
- - - - - - - - - - -- - - - - - - - - - -
Position:
Avg entry: 65,020
Total qty: 0.010
Total value: 650.20 USDT
Total fees: 1.30 USDT
Filled: 100%
Risk: 260.20 USDT (planned: 260 USDT)
Pending: none
- - - - - - - - - - -- - - - - - - - - - -
Source: exchange
```

---

## Caso 2 — ONE_SHOT MARKET, fill parziale (70%)

Fill 65,020, qty 0.007 (70% dei 0.010 pianificati). Residuo perso — MARKET non lascia coda.
actual_risk = 0.007 × (65,020 − 39,000) = **182.14 USDT**
Total qty 0.007 / Total value 455.14 / Total fees 0.91

```
📊 #145 — ENTRY OPENED
- - - - - - - - - - -- - - - - - - - - - -
BTC/USDT — 📈 LONG
- - - - - - - - - - -- - - - - - - - - - -
Filled:
Entry_1: 65,020 Market
Qty: 0.007 (planned: 0.010)
Value: 455.14 USDT
Fee rate: 0.200%
Fee: 0.91 USDT
Partial: 70%
- - - - - - - - - - -- - - - - - - - - - -
Position:
Avg entry: 65,020
Total qty: 0.007
Total value: 455.14 USDT
Total fees: 0.91 USDT
Filled: 70%
Risk: 182.14 USDT (planned: 260 USDT)
Pending: none
- - - - - - - - - - -- - - - - - - - - - -
Changed:
SL qty: 0.010 → 0.007 (adj. to fill)
- - - - - - - - - - -- - - - - - - - - - -
Source: exchange
```

> Nessun ENTRY UPDATED — senza pending non ci sono fill futuri.

---

## Caso 3 — ONE_SHOT LIMIT, fill completo, nessun slippage

Fill 65,000 (= limit price). actual_risk = 0.010 × (65,000 − 39,000) = **260 USDT** (identico a planned).
Total qty 0.010 / Total value 650.00 / Total fees 1.30

```
📊 #145 — ENTRY OPENED
- - - - - - - - - - -- - - - - - - - - - -
BTC/USDT — 📈 LONG
- - - - - - - - - - -- - - - - - - - - - -
Filled:
Entry_1: 65,000 Limit
Qty: 0.010
Value: 650.00 USDT
Fee rate: 0.200%
Fee: 1.30 USDT
- - - - - - - - - - -- - - - - - - - - - -
Position:
Avg entry: 65,000
Total qty: 0.010
Total value: 650.00 USDT
Total fees: 1.30 USDT
Filled: 100%
Risk: 260 USDT (planned: 260 USDT)
Pending: none
- - - - - - - - - - -- - - - - - - - - - -
Source: exchange
```

---

## Caso 4 — ONE_SHOT LIMIT, fill parziale (40%) + ENTRY UPDATED

Fill 65,000, qty 0.004 (40%). Il residuo 0.006 rimane pending.
actual_risk ENTRY OPENED = 0.004 × (65,000 − 39,000) = **104 USDT**

**→ ENTRY OPENED:**

Total qty 0.004 / Total value 260.00 / Total fees 0.52

```
📊 #145 — ENTRY OPENED
- - - - - - - - - - -- - - - - - - - - - -
BTC/USDT — 📈 LONG
- - - - - - - - - - -- - - - - - - - - - -
Filled:
Entry_1: 65,000 Limit
Qty: 0.004 (planned: 0.010)
Value: 260.00 USDT
Fee rate: 0.200%
Fee: 0.52 USDT
Partial: 40%
- - - - - - - - - - -- - - - - - - - - - -
Position:
Avg entry: 65,000
Total qty: 0.004
Total value: 260.00 USDT
Total fees: 0.52 USDT
Filled: 40%
Risk: 104 USDT (planned: 260 USDT)
Pending: Entry_1 65,000 Limit
- - - - - - - - - - -- - - - - - - - - - -
Changed:
SL qty: 0.010 → 0.004 (adj. to fill)
- - - - - - - - - - -- - - - - - - - - - -
Source: exchange
```

Residuo 0.006 filla completamente.
actual_risk ENTRY UPDATED = 0.010 × (65,000 − 39,000) = **260 USDT**

**→ ENTRY UPDATED (residuo filla):**

Total qty 0.010 / Total value 650.00 / Total fees 0.52 + 0.78 = 1.30

```
✏️ #145 — ENTRY UPDATED
- - - - - - - - - - -- - - - - - - - - - -
BTC/USDT — 📈 LONG
- - - - - - - - - - -- - - - - - - - - - -
Filled:
Entry_1: 65,000 Limit
Qty: 0.006
Value: 390.00 USDT
Fee rate: 0.200%
Fee: 0.78 USDT
- - - - - - - - - - -- - - - - - - - - - -
Position:
Avg entry: 65,000
Total qty: 0.010
Total value: 650.00 USDT
Total fees: 1.30 USDT
Filled: 100%
Risk: 260 USDT (planned: 260 USDT)
Pending: none
- - - - - - - - - - -- - - - - - - - - - -
Source: exchange
```

> Total value/fees aumentano nell'UPDATED: ora riflettono la posizione completa 0.010.

---

## Caso 5 — TWO_STEP (MARKET + LIMIT), fill normali

Entry_1 Market ~65,000 (70%) → filla a 65,020 / Entry_2 64,000 Limit (30%).

**→ ENTRY OPENED (Entry_1):**

actual_risk = 0.007 × (65,020 − 39,000) = **182.14 USDT**
Total qty 0.007 / Total value 455.14 / Total fees 0.91

```
📊 #145 — ENTRY OPENED
- - - - - - - - - - -- - - - - - - - - - -
BTC/USDT — 📈 LONG
- - - - - - - - - - -- - - - - - - - - - -
Filled:
Entry_1: 65,020 Market
Qty: 0.007
Value: 455.14 USDT
Fee rate: 0.200%
Fee: 0.91 USDT
- - - - - - - - - - -- - - - - - - - - - -
Position:
Avg entry: 65,020
Total qty: 0.007
Total value: 455.14 USDT
Total fees: 0.91 USDT
Filled: 70%
Risk: 182.14 USDT (planned: 260 USDT)
Pending: Entry_2 64,000 Limit
- - - - - - - - - - -- - - - - - - - - - -
Source: exchange
```

**→ ENTRY UPDATED (Entry_2, posizione completa):**

avg = (65,020 × 0.007 + 64,000 × 0.003) / 0.010 = **64,714**
actual_risk = 0.010 × (64,714 − 39,000) = **257.14 USDT**
Total qty 0.010 / Total value 0.010 × 64,714 = 647.14 / Total fees 0.91 + 0.38 = 1.29

```
✏️ #145 — ENTRY UPDATED
- - - - - - - - - - -- - - - - - - - - - -
BTC/USDT — 📈 LONG
- - - - - - - - - - -- - - - - - - - - - -
Filled:
Entry_2: 64,000 Limit
Qty: 0.003
Value: 192.00 USDT
Fee rate: 0.200%
Fee: 0.38 USDT
- - - - - - - - - - -- - - - - - - - - - -
Position:
Avg entry: 64,714
Total qty: 0.010
Total value: 647.14 USDT
Total fees: 1.29 USDT
Filled: 100%
Risk: 257.14 USDT (planned: 260 USDT)
Pending: none
- - - - - - - - - - -- - - - - - - - - - -
Source: exchange
```

> avg_entry finale 64,714 (non 65,000): Entry_2 a prezzo inferiore ha abbassato l'avg → rischio migliore.
> Total value cambia da 455.14 → 647.14 (aggiunge il valore del secondo leg).

---

## Caso 6 — TWO_STEP, fill parziale su Entry_2

Stessa apertura del Caso 5. Entry_2 filla 0.002 dei 0.003 pianificati.

**→ ENTRY UPDATED (Entry_2 parziale):**

avg = (65,020 × 0.007 + 64,000 × 0.002) / 0.009 = **64,793**
actual_risk = 0.009 × (64,793 − 39,000) = **232.13 USDT**
Total qty 0.009 / Total value 0.009 × 64,793 = 583.14 / Total fees 0.91 + 0.26 = 1.17

```
✏️ #145 — ENTRY UPDATED
- - - - - - - - - - -- - - - - - - - - - -
BTC/USDT — 📈 LONG
- - - - - - - - - - -- - - - - - - - - - -
Filled:
Entry_2: 64,000 Limit
Qty: 0.002 (planned: 0.003)
Value: 128.00 USDT
Fee rate: 0.200%
Fee: 0.26 USDT
Partial: 66.7%
- - - - - - - - - - -- - - - - - - - - - -
Position:
Avg entry: 64,793
Total qty: 0.009
Total value: 583.14 USDT
Total fees: 1.17 USDT
Filled: 90%
Risk: 232.13 USDT (planned: 260 USDT)
Pending: Entry_2 64,000 Limit
- - - - - - - - - - -- - - - - - - - - - -
Changed:
SL qty: 0.003 → 0.002 (adj. to fill)
- - - - - - - - - - -- - - - - - - - - - -
Source: exchange
```

> `planned_qty` in Changed è la qty del singolo leg (0.003), non il totale posizione.

**→ ENTRY UPDATED (residuo 0.001 filla):**

avg = (455.14 + 128.00 + 64.00) / 0.010 = **64,714**
actual_risk = 0.010 × (64,714 − 39,000) = **257.14 USDT**
Total qty 0.010 / Total value 647.14 / Total fees 0.91 + 0.26 + 0.13 = 1.30

```
✏️ #145 — ENTRY UPDATED
- - - - - - - - - - -- - - - - - - - - - -
BTC/USDT — 📈 LONG
- - - - - - - - - - -- - - - - - - - - - -
Filled:
Entry_2: 64,000 Limit
Qty: 0.001
Value: 64.00 USDT
Fee rate: 0.200%
Fee: 0.13 USDT
- - - - - - - - - - -- - - - - - - - - - -
Position:
Avg entry: 64,714
Total qty: 0.010
Total value: 647.14 USDT
Total fees: 1.30 USDT
Filled: 100%
Risk: 257.14 USDT (planned: 260 USDT)
Pending: none
- - - - - - - - - - -- - - - - - - - - - -
Source: exchange
```

---

## Caso 7 — LADDER (3 leg), fill normali

Entry_1 65,000 (50%) / Entry_2 64,000 (30%) / Entry_3 63,000 (20%).

**→ ENTRY OPENED (Entry_1, 50%):**

actual_risk = 0.005 × (65,000 − 39,000) = **130 USDT**
Total qty 0.005 / Total value 325.00 / Total fees 0.65

```
📊 #145 — ENTRY OPENED
- - - - - - - - - - -- - - - - - - - - - -
BTC/USDT — 📈 LONG
- - - - - - - - - - -- - - - - - - - - - -
Filled:
Entry_1: 65,000 Limit
Qty: 0.005
Value: 325.00 USDT
Fee rate: 0.200%
Fee: 0.65 USDT
- - - - - - - - - - -- - - - - - - - - - -
Position:
Avg entry: 65,000
Total qty: 0.005
Total value: 325.00 USDT
Total fees: 0.65 USDT
Filled: 50%
Risk: 130 USDT (planned: 260 USDT)
Pending: Entry_2 64,000 Limit
Pending: Entry_3 63,000 Limit
- - - - - - - - - - -- - - - - - - - - - -
Source: exchange
```

**→ ENTRY UPDATED (Entry_2, 80%):**

avg = (325.00 + 192.00) / 0.008 = **64,625**
actual_risk = 0.008 × (64,625 − 39,000) = **205 USDT**
Total qty 0.008 / Total value 0.008 × 64,625 = 517.00 / Total fees 0.65 + 0.38 = 1.03

```
✏️ #145 — ENTRY UPDATED
- - - - - - - - - - -- - - - - - - - - - -
BTC/USDT — 📈 LONG
- - - - - - - - - - -- - - - - - - - - - -
Filled:
Entry_2: 64,000 Limit
Qty: 0.003
Value: 192.00 USDT
Fee rate: 0.200%
Fee: 0.38 USDT
- - - - - - - - - - -- - - - - - - - - - -
Position:
Avg entry: 64,625
Total qty: 0.008
Total value: 517.00 USDT
Total fees: 1.03 USDT
Filled: 80%
Risk: 205 USDT (planned: 260 USDT)
Pending: Entry_3 63,000 Limit
- - - - - - - - - - -- - - - - - - - - - -
Source: exchange
```

**→ ENTRY UPDATED (Entry_3, 100%):**

avg = (325.00 + 192.00 + 126.00) / 0.010 = **64,300**
actual_risk = 0.010 × (64,300 − 39,000) = **253 USDT**
Total qty 0.010 / Total value 0.010 × 64,300 = 643.00 / Total fees 0.65 + 0.38 + 0.25 = 1.28

```
✏️ #145 — ENTRY UPDATED
- - - - - - - - - - -- - - - - - - - - - -
BTC/USDT — 📈 LONG
- - - - - - - - - - -- - - - - - - - - - -
Filled:
Entry_3: 63,000 Limit
Qty: 0.002
Value: 126.00 USDT
Fee rate: 0.200%
Fee: 0.25 USDT
- - - - - - - - - - -- - - - - - - - - - -
Position:
Avg entry: 64,300
Total qty: 0.010
Total value: 643.00 USDT
Total fees: 1.28 USDT
Filled: 100%
Risk: 253 USDT (planned: 260 USDT)
Pending: none
- - - - - - - - - - -- - - - - - - - - - -
Source: exchange
```

> LADDER con tutti i leg fillati: avg migliora progressivamente, rischio scende
> perché i leg successivi hanno prezzo più favorevole (più vicini al SL → distanza minore).
> Total value finale 643.00 < Total value Entry_1 × 2 — leg successivi costano meno.

---

## Caso 8 — SHORT, ONE_SHOT LIMIT completo

Entry_1 65,000 Limit SHORT, SL 91,000 (sopra per SHORT), rischio 260 USDT.
actual_risk = 0.010 × (91,000 − 65,000) = **260 USDT**
Total qty 0.010 / Total value 650.00 / Total fees 1.30

```
📊 #146 — ENTRY OPENED
- - - - - - - - - - -- - - - - - - - - - -
BTC/USDT — 📉 SHORT
- - - - - - - - - - -- - - - - - - - - - -
Filled:
Entry_1: 65,000 Limit
Qty: 0.010
Value: 650.00 USDT
Fee rate: 0.200%
Fee: 1.30 USDT
- - - - - - - - - - -- - - - - - - - - - -
Position:
Avg entry: 65,000
Total qty: 0.010
Total value: 650.00 USDT
Total fees: 1.30 USDT
Filled: 100%
Risk: 260 USDT (planned: 260 USDT)
Pending: none
- - - - - - - - - - -- - - - - - - - - - -
Source: exchange
```

---

## Caso 9 — MARKET, slippage pesante (SL tight)

SL 63,500 (tight), rischio pianificato 15 USDT. Fill a 65,800 (slippage +800).
actual_risk = 0.010 × (65,800 − 63,500) = **23 USDT** (+53% vs planned).
Total qty 0.010 / Total value 658.00 / Total fees 1.32

```
📊 #147 — ENTRY OPENED
- - - - - - - - - - -- - - - - - - - - - -
BTC/USDT — 📈 LONG
- - - - - - - - - - -- - - - - - - - - - -
Filled:
Entry_1: 65,800 Market
Qty: 0.010
Value: 658.00 USDT
Fee rate: 0.200%
Fee: 1.32 USDT
- - - - - - - - - - -- - - - - - - - - - -
Position:
Avg entry: 65,800
Total qty: 0.010
Total value: 658.00 USDT
Total fees: 1.32 USDT
Filled: 100%
Risk: 23 USDT (planned: 15 USDT)
Pending: none
- - - - - - - - - - -- - - - - - - - - - -
Source: exchange
```

> Con SL tight (+800 slippage su 1,500 distance = 53% di impatto sul rischio).

---

## Caso 10 — LIMIT, price improvement (SL tight)

SL 63,500, rischio pianificato 15 USDT. Limit 64,000, filla a 63,800 (price improvement −200).
actual_risk = 0.010 × (63,800 − 63,500) = **3 USDT** (−80% vs planned).
Total qty 0.010 / Total value 638.00 / Total fees 1.28

```
📊 #148 — ENTRY OPENED
- - - - - - - - - - -- - - - - - - - - - -
BTC/USDT — 📈 LONG
- - - - - - - - - - -- - - - - - - - - - -
Filled:
Entry_1: 63,800 Limit
Qty: 0.010
Value: 638.00 USDT
Fee rate: 0.200%
Fee: 1.28 USDT
- - - - - - - - - - -- - - - - - - - - - -
Position:
Avg entry: 63,800
Total qty: 0.010
Total value: 638.00 USDT
Total fees: 1.28 USDT
Filled: 100%
Risk: 3 USDT (planned: 15 USDT)
Pending: none
- - - - - - - - - - -- - - - - - - - - - -
Source: exchange
```

> fill_price = 63,800 (esecuzione reale), non il limit price 64,000.
> Rischio drasticamente migliorato: il prezzo è quasi sul SL.

---

## Caso 11 — TWO_STEP: slippage Entry_1 + price improvement Entry_2 (SL tight)

SL 63,500, rischio pianificato 15 USDT.
Entry_1 Market ~65,000 → filla a 65,800 (+800). Entry_2 Limit 64,000 → filla a 63,800 (−200).

**→ ENTRY OPENED (Entry_1, slippage):**

actual_risk = 0.007 × (65,800 − 63,500) = **16.10 USDT**
Total qty 0.007 / Total value 460.60 / Total fees 0.92

```
📊 #149 — ENTRY OPENED
- - - - - - - - - - -- - - - - - - - - - -
BTC/USDT — 📈 LONG
- - - - - - - - - - -- - - - - - - - - - -
Filled:
Entry_1: 65,800 Market
Qty: 0.007
Value: 460.60 USDT
Fee rate: 0.200%
Fee: 0.92 USDT
- - - - - - - - - - -- - - - - - - - - - -
Position:
Avg entry: 65,800
Total qty: 0.007
Total value: 460.60 USDT
Total fees: 0.92 USDT
Filled: 70%
Risk: 16.10 USDT (planned: 15 USDT)
Pending: Entry_2 64,000 Limit
- - - - - - - - - - -- - - - - - - - - - -
Source: exchange
```

> `Pending` mostra il prezzo piano (64,000), non il prezzo di esecuzione futuro.

**→ ENTRY UPDATED (Entry_2, price improvement):**

avg = (65,800 × 0.007 + 63,800 × 0.003) / 0.010 = (460.60 + 191.40) / 0.010 = **65,200**
actual_risk = 0.010 × (65,200 − 63,500) = **17 USDT**
Total qty 0.010 / Total value 0.010 × 65,200 = 652.00 / Total fees 0.92 + 0.38 = 1.30

```
✏️ #149 — ENTRY UPDATED
- - - - - - - - - - -- - - - - - - - - - -
BTC/USDT — 📈 LONG
- - - - - - - - - - -- - - - - - - - - - -
Filled:
Entry_2: 63,800 Limit
Qty: 0.003
Value: 191.40 USDT
Fee rate: 0.200%
Fee: 0.38 USDT
- - - - - - - - - - -- - - - - - - - - - -
Position:
Avg entry: 65,200
Total qty: 0.010
Total value: 652.00 USDT
Total fees: 1.30 USDT
Filled: 100%
Risk: 17 USDT (planned: 15 USDT)
Pending: none
- - - - - - - - - - -- - - - - - - - - - -
Source: exchange
```

> avg finale 65,200 vs proiezione segnale 64,700 (+500 netto di slippage).
> Il price improvement su Entry_2 ha parzialmente compensato lo slippage su Entry_1.
> Total value 652.00 ≠ Caso 7 finale 643.00: stesso totale qty ma avg più alto per slippage Entry_1.

---

## Note implementative

| Campo | Fonte | Nota |
|-------|-------|------|
| `planned_qty` | `risk["legs"][seq]["qty"]` | Qty del leg specifico, non totale posizione |
| `entry_type_for_leg` | `plan["legs"][seq]["entry_type"]` | "Market" / "Limit" capitalizzato |
| `is_partial_leg` | `filled_qty < planned_qty` | False se `planned_qty` non disponibile |
| `_leg_fill_pct` | `filled_qty / planned_qty × 100` | Es. 70%, 40%, 66.7% |
| `position_filled_pct` | `filled_entry_qty / total_planned_qty × 100` | Cumulativo dopo questo fill |
| `total_filled_qty` | `filled_entry_qty` (chain_row) | Cumulativo — aumenta ad ogni fill |
| `total_value` | `filled_entry_qty × avg_entry` | Valore totale posizione corrente |
| `total_fees` | `risk["open_fee_residual"]` | Accumulato ad ogni fill di apertura |
| `actual_risk_usdt` | `filled_entry_qty × \|avg_entry − current_stop_price\|` | Usa prezzi reali — sensibile a slippage |
| `planned_risk_usdt` | `initial_risk_amount` | Dal risk snapshot — basato su prezzi segnale |
| `avg_entry` | Calcolato in event_processor come Σ(p×q)/Σq | Nel payload come `new_avg_entry` per ENTRY_UPDATED |

**`Risk` appare sempre** (condizione: `actual_risk_usdt is not None`) — opzione B scelta.
**`Total qty/value/fees` sempre presenti** — anche su fill singolo ONE_SHOT dove sono ridondanti.
**Ordine Fee:** Value → Fee rate → Fee (fee_rate opzionale — solo se nel payload).
`Changed: SL qty` appare solo se `is_partial_leg = True`.

---

## ENTRY CANCELLED

Ordine limit pending cancellato. La posizione rimane aperta con la qty fillata fino a quel momento.
`avg_entry` e `total_filled_qty` riflettono lo stato **dopo** la cancellazione.

### Caso 12 — Entry_2 cancellata, mai fillata

TWO_STEP: Entry_1 fillata a 65,020 (0.007 BTC). Entry_2 64,000 Limit cancellata prima del fill.
`avg_entry` = 65,020 (solo Entry_1). `total_filled` = 0.007 BTC.

```
⚠️ #12 — ENTRY CANCELLED
- - - - - - - - - - - - - - -
BTC/USDT — 📈 LONG
- - - - - - - - - - - - - - -
Entry_2: 64,000 Limit
Avg entry: 65,020
Total filled: 0.007 BTC
- - - - - - - - - - - - - - -
Source: runtime
```

### Caso 13 — Entry_2 parzialmente fillata poi cancellata

TWO_STEP: Entry_1 fillata 65,020 (0.007). Entry_2 Limit 64,000: filla 0.001 dei 0.003 pianificati (33%) poi cancellata.
`avg` = (65,020 × 0.007 + 64,000 × 0.001) / 0.008 = **64,886**
`total_filled` = 0.008 BTC.

```
⚠️ #12 — ENTRY CANCELLED
- - - - - - - - - - - - - - -
BTC/USDT — 📈 LONG
- - - - - - - - - - - - - - -
Entry_2: 64,000 Limit
Partial fill: 33% (0.001 BTC kept)
Avg entry: 64,886
Total filled: 0.008 BTC
- - - - - - - - - - - - - - -
Source: runtime
```

> `Partial fill:` appare solo quando `partial_fill_pct` è nel payload.
> `(qty BTC kept)` da `partial_fill_qty` e `_base_asset` — entrambi opzionali.
> `Avg entry` = media aggiornata che include il fill parziale di Entry_2.
> La posizione rimane aperta — non è una chiusura.

| Campo | Fonte | Nota |
|-------|-------|------|
| `_c_seq` | `cancelled_entry["sequence"]` | Numero del leg cancellato |
| `_c_price` | `cancelled_entry["price"]` | `None` → riga solo con tipo (es. "Entry_2: Market") |
| `_c_etype` | `cancelled_entry["entry_type"].capitalize()` | "Market" / "Limit" |
| `partial_fill_pct` | payload | `None` → blocco Partial fill omesso |
| `partial_fill_qty` | payload | opzionale — se assente omette "(qty BTC kept)" |
| `avg_entry` | chain_row | Media aggiornata post-cancellazione |
| `total_filled_qty` | `filled_entry_qty` | `None` → riga "Total filled" omessa |
