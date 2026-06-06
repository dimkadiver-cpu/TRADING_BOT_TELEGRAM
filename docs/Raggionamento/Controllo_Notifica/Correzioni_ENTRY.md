# ENTRY OPENED / ENTRY UPDATED — Esempi reali

Formato prodotto dal template system (`_ENTRY_BLOCKS`).
`────────────────` = SeparatorBlock dinamico (larghezza calcolata da `_finalize()`).

---

## Struttura

```
{emoji} #{chain_id} — ENTRY OPENED | ENTRY UPDATED
────────────────
{symbol} — {side_emoji} {side}
────────────────
Filled:
Entry_N: {fill_price} {type}
Qty: {filled_qty}                         ← full fill
Qty: {filled_qty} (planned: {planned_qty}) ← partial fill
Value: {exec_value} USDT
Fee: {fee} USDT
[Partial: {leg_fill_pct}%]                ← solo se parziale
────────────────
Position:
Avg entry: {avg_entry}
Filled: {position_filled_pct}%
[Risk: {actual} USDT (planned: {planned} USDT)]  ← solo se parziale
Pending: Entry_N {price} Limit            ← una riga per entry pending
Pending: none                             ← se nessuno
[────────────────]                        ← solo se sezione Changed segue
[Changed:]
[SL qty: {planned_qty} → {filled_qty} (adj. to fill)]
────────────────
Source: exchange
```

**Regole:**
- `Qty` senza simbolo base — non nel payload
- `Value` (exec_value) sempre presente
- `Partial`, `Risk`, `Changed` solo quando `is_partial_leg = True` (filled_qty < planned_qty per quel leg)
- Ogni entry pending → riga `Pending:` separata (non indentate sotto un'unica label)
- `Filled: xx%` = filled_entry_qty totale / total_planned_qty — sempre presente
- avg_entry calcolato correttamente: Σ(price_i × qty_i) / Σ(qty_i)

---

## Caso 1 — ONE_SHOT MARKET, fill completo

Segnale: Entry_1 Market ~65,000 — qty 0.010, rischio 260 USDT

```
📊 #145 — ENTRY OPENED
────────────────
BTC/USDT — 📈 LONG
────────────────
Filled:
Entry_1: 65,020 Market
Qty: 0.010
Value: 650.20 USDT
Fee: 1.30 USDT
────────────────
Position:
Avg entry: 65,020
Filled: 100%
Pending: none
────────────────
Source: exchange
```

---

## Caso 2 — ONE_SHOT MARKET, fill parziale (70%)

Segnale: Entry_1 Market ~65,000 — qty 0.010, rischio 260 USDT
Exchange ha eseguito 0.007 (70%). Il residuo 30% è perso — MARKET non lascia coda.

```
📊 #145 — ENTRY OPENED
────────────────────────────────
BTC/USDT — 📈 LONG
────────────────────────────────
Filled:
Entry_1: 65,020 Market
Qty: 0.007 (planned: 0.010)
Value: 455.14 USDT
Fee: 0.91 USDT
Partial: 70%
────────────────────────────────
Position:
Avg entry: 65,020
Filled: 70%
Risk: 182 USDT (planned: 260 USDT)
Pending: none
────────────────────────────────
Changed:
SL qty: 0.010 → 0.007 (adj. to fill)
────────────────────────────────
Source: exchange
```

> Nessun ENTRY UPDATED successivo — senza pending non ci sono fill futuri.

---

## Caso 3 — ONE_SHOT LIMIT, fill completo

Segnale: Entry_1 65,000 Limit — qty 0.010, rischio 260 USDT

```
📊 #145 — ENTRY OPENED
────────────────
BTC/USDT — 📈 LONG
────────────────
Filled:
Entry_1: 65,000 Limit
Qty: 0.010
Value: 650.00 USDT
Fee: 1.30 USDT
────────────────
Position:
Avg entry: 65,000
Filled: 100%
Pending: none
────────────────
Source: exchange
```

---

## Caso 4 — ONE_SHOT LIMIT, fill parziale (40%) + ENTRY UPDATED sul residuo

Segnale: Entry_1 65,000 Limit — qty 0.010, rischio 260 USDT
Exchange ha eseguito 0.004 (40%). Il residuo 0.006 rimane pending sullo stesso ordine.

**→ ENTRY OPENED:**

```
📊 #145 — ENTRY OPENED
────────────────────────────────
BTC/USDT — 📈 LONG
────────────────────────────────
Filled:
Entry_1: 65,000 Limit
Qty: 0.004 (planned: 0.010)
Value: 260.00 USDT
Fee: 0.52 USDT
Partial: 40%
────────────────────────────────
Position:
Avg entry: 65,000
Filled: 40%
Risk: 104 USDT (planned: 260 USDT)
Pending: Entry_1 65,000 Limit
────────────────────────────────
Changed:
SL qty: 0.010 → 0.004 (adj. to fill)
────────────────────────────────
Source: exchange
```

Quando il residuo 0.006 filla completamente → ENTRY UPDATED:

**→ ENTRY UPDATED (residuo filla):**

```
✏️ #145 — ENTRY UPDATED
────────────────
BTC/USDT — 📈 LONG
────────────────
Filled:
Entry_1: 65,000 Limit
Qty: 0.006
Value: 390.00 USDT
Fee: 0.78 USDT
────────────────
Position:
Avg entry: 65,000
Filled: 100%
Pending: none
────────────────
Source: exchange
```

> `is_partial_leg = False` → niente Partial/Risk/Changed. Avg invariato (stesso prezzo).

---

## Caso 5 — TWO_STEP (MARKET + LIMIT), fill normali

Segnale: Entry_1 Market ~65,000 (70%) / Entry_2 64,000 Limit (30%)
qty totale 0.010, rischio 260 USDT.

**→ ENTRY OPENED (Entry_1, 70% della posizione):**

```
📊 #145 — ENTRY OPENED
────────────────────────────
BTC/USDT — 📈 LONG
────────────────────────────
Filled:
Entry_1: 65,020 Market
Qty: 0.007
Value: 455.14 USDT
Fee: 0.91 USDT
────────────────────────────
Position:
Avg entry: 65,020
Filled: 70%
Pending: Entry_2 64,000 Limit
────────────────────────────
Source: exchange
```

**→ ENTRY UPDATED (Entry_2, posizione completa):**

avg = (65,020 × 0.007 + 64,000 × 0.003) / 0.010 = (455.14 + 192.00) / 0.010 = **64,714**

```
✏️ #145 — ENTRY UPDATED
────────────────
BTC/USDT — 📈 LONG
────────────────
Filled:
Entry_2: 64,000 Limit
Qty: 0.003
Value: 192.00 USDT
Fee: 0.38 USDT
────────────────
Position:
Avg entry: 64,714
Filled: 100%
Pending: none
────────────────
Source: exchange
```

---

## Caso 6 — TWO_STEP, fill parziale su Entry_2

Segnale: Entry_1 Market ~65,000 (70%) / Entry_2 64,000 Limit (30%)
qty totale 0.010, rischio 260 USDT.
Entry_1 eseguita normalmente (→ Caso 5, ENTRY OPENED identico).
Entry_2 filla solo 0.002 dei 0.003 pianificati. Il residuo 0.001 rimane pending.

**→ ENTRY UPDATED (Entry_2 parziale):**

avg = (65,020 × 0.007 + 64,000 × 0.002) / 0.009 = (455.14 + 128.00) / 0.009 = **64,793**
position_filled_pct = 0.009 / 0.010 = 90%
actual_risk = 260 × 0.009 / 0.010 = **234 USDT**

```
✏️ #145 — ENTRY UPDATED
────────────────────────────────────
BTC/USDT — 📈 LONG
────────────────────────────────────
Filled:
Entry_2: 64,000 Limit
Qty: 0.002 (planned: 0.003)
Value: 128.00 USDT
Fee: 0.26 USDT
Partial: 66.7%
────────────────────────────────────
Position:
Avg entry: 64,793
Filled: 90%
Risk: 234 USDT (planned: 260 USDT)
Pending: Entry_2 64,000 Limit
────────────────────────────────────
Changed:
SL qty: 0.003 → 0.002 (adj. to fill)
────────────────────────────────────
Source: exchange
```

> `planned_qty` nella sezione Changed è la qty del singolo leg (0.003), non il totale posizione.

**→ ENTRY UPDATED (residuo 0.001 filla):**

avg = (455.14 + 128.00 + 64.00) / 0.010 = 647.14 / 0.010 = **64,714**

```
✏️ #145 — ENTRY UPDATED
────────────────
BTC/USDT — 📈 LONG
────────────────
Filled:
Entry_2: 64,000 Limit
Qty: 0.001
Value: 64.00 USDT
Fee: 0.13 USDT
────────────────
Position:
Avg entry: 64,714
Filled: 100%
Pending: none
────────────────
Source: exchange
```

---

## Caso 7 — LADDER (3 leg), fill normali

Segnale: Entry_1 65,000 Limit (50%) / Entry_2 64,000 Limit (30%) / Entry_3 63,000 Limit (20%)
qty totale 0.010, rischio 260 USDT.

**→ ENTRY OPENED (Entry_1, 50%):**

```
📊 #145 — ENTRY OPENED
────────────────────────────────────
BTC/USDT — 📈 LONG
────────────────────────────────────
Filled:
Entry_1: 65,000 Limit
Qty: 0.005
Value: 325.00 USDT
Fee: 0.65 USDT
────────────────────────────────────
Position:
Avg entry: 65,000
Filled: 50%
Pending: Entry_2 64,000 Limit
Pending: Entry_3 63,000 Limit
────────────────────────────────────
Source: exchange
```

> Ogni entry pending è una riga `Pending:` separata — non indentate.

**→ ENTRY UPDATED (Entry_2, 80% totale):**

avg = (65,000 × 0.005 + 64,000 × 0.003) / 0.008 = (325.00 + 192.00) / 0.008 = **64,625**

```
✏️ #145 — ENTRY UPDATED
────────────────────────────────────
BTC/USDT — 📈 LONG
────────────────────────────────────
Filled:
Entry_2: 64,000 Limit
Qty: 0.003
Value: 192.00 USDT
Fee: 0.38 USDT
────────────────────────────────────
Position:
Avg entry: 64,625
Filled: 80%
Pending: Entry_3 63,000 Limit
────────────────────────────────────
Source: exchange
```

**→ ENTRY UPDATED (Entry_3, posizione completa):**

avg = (325.00 + 192.00 + 126.00) / 0.010 = 643.00 / 0.010 = **64,300**

```
✏️ #145 — ENTRY UPDATED
────────────────
BTC/USDT — 📈 LONG
────────────────
Filled:
Entry_3: 63,000 Limit
Qty: 0.002
Value: 126.00 USDT
Fee: 0.25 USDT
────────────────
Position:
Avg entry: 64,300
Filled: 100%
Pending: none
────────────────
Source: exchange
```

---

## Caso 8 — SHORT, ONE_SHOT LIMIT completo

Segnale: Entry_1 65,000 Limit SHORT — qty 0.010, rischio 260 USDT

```
📊 #146 — ENTRY OPENED
────────────────
BTC/USDT — 📉 SHORT
────────────────
Filled:
Entry_1: 65,000 Limit
Qty: 0.010
Value: 650.00 USDT
Fee: 1.30 USDT
────────────────
Position:
Avg entry: 65,000
Filled: 100%
Pending: none
────────────────
Source: exchange
```

> Identico al Caso 3 tranne emoji lato: 📉 SHORT.

---

## Caso 9 — MARKET, slippage pesante (full fill)

Setup: Entry_1 Market ~65,000, SL 63,500 (distance 1,500/BTC), qty 0.010, **rischio pianificato 15 USDT**.
Exchange filla a 65,800 (+800, slippage +1.2%).

Rischio effettivo = 0.010 × |65,800 − 63,500| = 0.010 × 2,300 = **23 USDT** (+53% vs planned).

```
📊 #147 — ENTRY OPENED
────────────────
BTC/USDT — 📈 LONG
────────────────
Filled:
Entry_1: 65,800 Market
Qty: 0.010
Value: 658.00 USDT
Fee: 1.32 USDT
────────────────
Position:
Avg entry: 65,800
Filled: 100%
Pending: none
────────────────
Source: exchange
```

> `is_partial_leg = False` (fill completo) → sezione Risk **non appare** nel template attuale.
> Con SL tight il rischio reale cresce significativamente: l'utente non lo vede.
> **Domanda di design:** mostrare `Risk: actual (planned)` anche su full fill con slippage?

---

## Caso 10 — LIMIT, price improvement (LONG)

Setup stesso del Caso 9.
Entry_2 Limit 64,000. Exchange filla a 63,800 (price improvement −200, meglio per LONG).

Rischio effettivo = 0.010 × |63,800 − 63,500| = 0.010 × 300 = **3 USDT** (−80% vs planned 15 USDT).

```
📊 #148 — ENTRY OPENED
────────────────
BTC/USDT — 📈 LONG
────────────────
Filled:
Entry_1: 63,800 Limit
Qty: 0.010
Value: 638.00 USDT
Fee: 1.28 USDT
────────────────
Position:
Avg entry: 63,800
Filled: 100%
Pending: none
────────────────
Source: exchange
```

> Anche qui `is_partial_leg = False` → Risk non mostrato. Ma il rischio è migliorato drasticamente.
> Nota: il `fill_price` è 63,800 (esecuzione reale), non il limit price 64,000.
> La sezione Pending userebbe ancora 64,000 (dal piano) se ci fosse residuo.

---

## Caso 11 — TWO_STEP: slippage Entry_1 + price improvement Entry_2

Segnale: Entry_1 Market ~65,000 (70%) / Entry_2 64,000 Limit (30%)
qty totale 0.010, SL 63,500, rischio pianificato 15 USDT.

- Entry_1 filla a **65,800** (slippage +800)
- Entry_2 filla a **63,800** (price improvement −200)
- avg_entry finale = (65,800 × 0.007 + 63,800 × 0.003) / 0.010
  = (460.60 + 191.40) / 0.010 = 652.00 / 0.010 = **65,200**
  (vs avg "atteso" dal segnale = (65,000 × 0.007 + 64,000 × 0.003) / 0.010 = 64,700)

**→ ENTRY OPENED (Entry_1, slippage):**

```
📊 #149 — ENTRY OPENED
────────────────────────────
BTC/USDT — 📈 LONG
────────────────────────────
Filled:
Entry_1: 65,800 Market
Qty: 0.007
Value: 460.60 USDT
Fee: 0.92 USDT
────────────────────────────
Position:
Avg entry: 65,800
Filled: 70%
Pending: Entry_2 64,000 Limit
────────────────────────────
Source: exchange
```

**→ ENTRY UPDATED (Entry_2, price improvement):**

avg = (65,800 × 0.007 + 63,800 × 0.003) / 0.010 = **65,200**
rischio finale = 0.010 × |65,200 − 63,500| = 0.010 × 1,700 = **17 USDT** (vs pianificato 15 USDT)

```
✏️ #149 — ENTRY UPDATED
────────────────────────────
BTC/USDT — 📈 LONG
────────────────────────────
Filled:
Entry_2: 63,800 Limit
Qty: 0.003
Value: 191.40 USDT
Fee: 0.38 USDT
────────────────────────────
Position:
Avg entry: 65,200
Filled: 100%
Pending: none
────────────────────────────
Source: exchange
```

> avg_entry finale 65,200 vs proiezione segnale 64,700 (+500 di slippage netto).
> Il rischio reale 17 USDT (+13%) non è visibile — `is_partial_leg = False` su entrambi i fill.
> **Pending mostra il prezzo piano (64,000)**, non il prezzo di esecuzione (63,800) — corretto.

---

## Note implementative

| Campo | Da dove viene | Nota |
|-------|---------------|------|
| `planned_qty` | `risk["legs"][seq]["qty"]` | Per il leg specifico, non totale |
| `entry_type_for_leg` | `plan["legs"][seq]["entry_type"]` | "Market" / "Limit" capitalizzato |
| `is_partial_leg` | `filled_qty < planned_qty` | False se planned_qty non disponibile |
| `_leg_fill_pct` | `filled_qty / planned_qty * 100` | Es. 70%, 40%, 66.7% |
| `position_filled_pct` | `filled_entry_qty / total_planned_qty * 100` | Cumulativo dopo questo fill |
| `actual_risk_usdt` | `filled_entry_qty × \|avg_entry − current_stop_price\|` | Usa prezzi reali — sensibile a slippage |
| `planned_risk_usdt` | `initial_risk_amount` | Dal risk snapshot — basato su prezzi segnale |
| avg_entry | Calcolato in event_processor come Σ(p×q)/Σq | `new_avg_entry` nel payload ENTRY_UPDATED |

### Gap di design: slippage su full fill

Con `is_partial_leg = False`, la sezione `Risk` non appare anche quando lo slippage sposta il
rischio significativamente (Caso 9: +53%, Caso 11: +13%).

Opzioni:
- **A — Invariato**: Risk solo se partial (implementazione attuale della spec)
- **B — Sempre**: Risk mostrato sempre se `actual_risk_usdt` disponibile
- **C — Soglia**: Risk mostrato se `|actual − planned| / planned > 5%`

Impatto: se si sceglie B o C, `is_partial_leg` non è più l'unica condizione per Risk
— serve una condizione separata `show_risk` iniettata dal transform.
