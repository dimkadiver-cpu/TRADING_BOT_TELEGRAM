# Correzioni ENTRY OPENED / ENTRY UPDATED

---

## Principio

- **ENTRY OPENED** — primo leg fillato → la posizione esiste per la prima volta
- **ENTRY UPDATED** — leg successivo fillato → la posizione cambia (avg entry aggiornato)

ONE_SHOT: solo ENTRY OPENED, mai ENTRY UPDATED.
Multi-entry: ENTRY OPENED al primo fill, ENTRY UPDATED per ogni fill successivo.

---

## Struttura comune

```
📊 #id — ENTRY OPENED | ENTRY UPDATED
- - - -
Symbol — side
- - - - - - - - - - - - - - -
Filled:
Entry_N: price type
Qty: x.xxx
Value: xxx.xx USDT
Fee: x.xx USDT
[Partial: xx%]          ← solo se fill parziale
- - - - - - - - - - - - - - -
Position:
Avg entry: price
Filled: xx%             ← % della posizione totale pianificata
[Risk: xx USDT (planned: xx USDT)]  ← solo se fill parziale
Pending: Entry_N price Limit | none
- - - - - - - - - - - - - - -
[Changed:]              ← solo se qualcosa è cambiato rispetto al segnale
[SL qty: x.xxx → x.xxx (adj.)]
- - - - - - - - - - - - - - -
Source: exchange
```

Regole:
- Qty senza simbolo base (non disponibile nel payload)
- Value in USDT sempre presente (exec_value dal payload exchange)
- Partial e Risk vs planned solo quando il fill è parziale
- Changed solo quando SL/TP sono stati aggiustati rispetto al segnale originale

---

## Caso 1 — ONE_SHOT MARKET, fill completo

Segnale: Entry_1: Market ~65,000 — pianificato 0.010, rischio 260 USDT

```
📊 #145 — ENTRY OPENED
────────────────
BTC/USDT — 📈 LONG

Filled:
Entry_1: 65,020 Market
Qty: 0.010
Value: 650.20 USDT
Fee: 1.30 USDT

Position:
Avg entry: 65,020
Filled: 100%
Pending: none

────────────────
Source: exchange
```

---

## Caso 2 — ONE_SHOT MARKET, fill parziale

Segnale: Entry_1: Market ~65,000 — pianificato 0.010, rischio 260 USDT
Exchange ha eseguito solo 0.007 (70% della qty pianificata).
SL qty si adatta automaticamente alla qty effettiva.

```
📊 #145 — ENTRY OPENED
────────────────
BTC/USDT — 📈 LONG

Filled:
Entry_1: 65,020 Market
Qty: 0.007 (planned: 0.010)
Value: 455.14 USDT
Fee: 0.91 USDT
Partial: 70%

Position:
Avg entry: 65,020
Filled: 70%
Risk: 182 USDT (planned: 260 USDT)
Pending: none

Changed:
SL qty: 0.010 → 0.007 (adj. to fill)

────────────────
Source: exchange
```

Nessun ENTRY UPDATED — il residuo 30% non è più pending (MARKET non lascia coda).

---

## Caso 3 — ONE_SHOT LIMIT, fill completo

Segnale: Entry_1: 65,000 Limit

```
📊 #145 — ENTRY OPENED
────────────────
BTC/USDT — 📈 LONG

Filled:
Entry_1: 65,000 Limit
Qty: 0.010
Value: 650.00 USDT
Fee: 1.30 USDT

Position:
Avg entry: 65,000
Filled: 100%
Pending: none

────────────────
Source: exchange
```

---

## Caso 4 — ONE_SHOT LIMIT, fill parziale

Segnale: Entry_1: 65,000 Limit — pianificato 0.010, rischio 260 USDT
Exchange ha eseguito 0.004 (40%). Il residuo 0.006 rimane pending sullo stesso ordine.

```
📊 #145 — ENTRY OPENED
────────────────
BTC/USDT — 📈 LONG

Filled:
Entry_1: 65,000 Limit
Qty: 0.004 (planned: 0.010)
Value: 260.00 USDT
Fee: 0.52 USDT
Partial: 40%

Position:
Avg entry: 65,000
Filled: 40%
Risk: 104 USDT (planned: 260 USDT)
Pending: Entry_1 65,000 Limit (rem. 0.006)

Changed:
SL qty: 0.010 → 0.004 (adj. to fill)

────────────────
Source: exchange
```

Quando il residuo filla → ENTRY UPDATED (fill completo o ulteriore parziale).

---

## Caso 5 — TWO_STEP (MARKET + LIMIT), fill normale

Segnale: Entry_1: Market ~65,000 / Entry_2: 64,000 Limit
Pesi: 70% Entry_1, 30% Entry_2. Rischio totale pianificato: 260 USDT.

Primo fill → ENTRY OPENED:

```
📊 #145 — ENTRY OPENED
────────────────
BTC/USDT — 📈 LONG

Filled:
Entry_1: 65,020 Market
Qty: 0.007
Value: 455.14 USDT
Fee: 0.91 USDT

Position:
Avg entry: 65,020
Filled: 70%
Pending: Entry_2 64,000 Limit

────────────────
Source: exchange
```

Secondo fill → ENTRY UPDATED:

```
📊 #145 — ENTRY UPDATED
────────────────
BTC/USDT — 📈 LONG

Filled:
Entry_2: 64,000 Limit
Qty: 0.003
Value: 192.00 USDT
Fee: 0.38 USDT

Position:
Avg entry: 64,660
Filled: 100%
Pending: none

────────────────
Source: exchange
```

---

## Caso 6 — LADDER (3 leg), fill normali

Segnale: Entry_1: 65,000 (50%) / Entry_2: 64,000 (30%) / Entry_3: 63,000 (20%)
Rischio totale pianificato: 260 USDT.

Primo fill → ENTRY OPENED:

```
📊 #145 — ENTRY OPENED
────────────────
BTC/USDT — 📈 LONG

Filled:
Entry_1: 65,000 Limit
Qty: 0.005
Value: 325.00 USDT
Fee: 0.65 USDT

Position:
Avg entry: 65,000
Filled: 50%
Pending: Entry_2 64,000 Limit
         Entry_3 63,000 Limit

────────────────
Source: exchange
```

Secondo fill → ENTRY UPDATED:

```
📊 #145 — ENTRY UPDATED
────────────────
BTC/USDT — 📈 LONG

Filled:
Entry_2: 64,000 Limit
Qty: 0.003
Value: 192.00 USDT
Fee: 0.38 USDT

Position:
Avg entry: 64,600
Filled: 80%
Pending: Entry_3 63,000 Limit

────────────────
Source: exchange
```

Terzo fill → ENTRY UPDATED:

```
📊 #145 — ENTRY UPDATED
────────────────
BTC/USDT — 📈 LONG

Filled:
Entry_3: 63,000 Limit
Qty: 0.002
Value: 126.00 USDT
Fee: 0.25 USDT

Position:
Avg entry: 64,260
Filled: 100%
Pending: none

────────────────
Source: exchange
```

---

## Domande aperte

1. Filled xx% e Risk USDT — upstream li manda già, o vanno calcolati dal formatter?
    non saprei da verificare in codice
2. planned_qty / planned_risk — upstream li manda nel payload per i casi parziali?
   non saprei da verificare in codice
3. Pending multipli — una riga per entry (come sopra) ok?
  si
4. SL qty nella sezione Changed — upstream manda old_sl_qty e new_sl_qty?
  non saprei da verificare in codice
5.   usiamo " - - - -", come separatore
