# Chiusure parziali — TP_FILLED e PARTIAL_CLOSE_EXECUTED

Template unificato `_PARTIAL_RESULT_BLOCKS` con transform diverso — stesso pattern di `_CLOSED_BLOCKS`.
`TP_FILLED_FINAL` → usa `_CLOSED_BLOCKS` con `FINAL_RESULT`, non questo template.

| Campo | `TP_FILLED` | `PARTIAL_CLOSE_EXECUTED` |
|-------|-------------|--------------------------|
| Emoji | 📊 | ✅ |
| Label | `TP{N} FILLED` | `PARTIAL CLOSED` |
| Prima riga | `TP_N: price` | `Price: price` |
| `Value:` | ✅ presente | ❌ assente |
| Source | `exchange` | `trader_update` |

---

## Struttura

**TP_FILLED:**
```
📊 #<id> — TP<N> FILLED
- - -
<symbol> — <side>
- - -
TP_N: <fill_price>
Closed: <closed_pct>%
Qty: <closed_qty>
PnL: <pnl> USDT
Fee rate: <fee_rate>%
Fee: <fee> USDT
- - -
Remaining:
Qty: <remaining_qty>
Avg entry: <avg_entry>
Risk: <remaining_risk> USDT
- - -
Source: exchange
```

**PARTIAL_CLOSE_EXECUTED:**
```
✅ #<id> — PARTIAL CLOSED
- - -
<symbol> — <side>
- - -
Price: <fill_price>
Closed: <closed_pct>%
Qty: <closed_qty>
PnL: <pnl> USDT
Fee rate: <fee_rate>%
Fee: <fee> USDT
- - -
Remaining:
Qty: <remaining_qty>
Avg entry: <avg_entry>
Risk: <remaining_risk> USDT
- - -
Source: trader_update
<origin_link>
```

**Ordine fee:** Fee rate → Fee — coerente con ENTRY.
**Remaining.Risk** = `remaining_qty × |avg_entry − current_stop_price|`.
Se SL è a BE: `Risk: 0 USDT` (posizione protetta).
**`Value:`** presente solo in `TP_FILLED` (`_show_value=True`) — assente in `PARTIAL_CLOSE_EXECUTED`.

---

## Esempi — TP_FILLED

### Caso 1 — TP1, TWO_STEP (50% chiuso)

Posizione: 0.010 BTC, avg entry 65,000, SL 39,000.
TP1 = 69,000 (50% close). TP2 = 71,000 rimanente.
remaining_risk = 0.005 × (65,000 − 39,000) = **130 USDT**

```
📊 #12 — TP1 FILLED
- - - - - - - - - - - - -
BTC/USDT — 📈 LONG
https://t.me/c/123456/987
- - - - - - - - - - - - -
TP_1: 69,000
Closed: 50%
Qty: 0.005
PnL: +20.00 USDT
Fee rate: 0.055%
Fee: 0.38 USDT
- - - - - - - - - - - - -
Remaining:
Qty: 0.005
Avg entry: 65,000
Risk: 130 USDT
- - - - - - - - - - - - -
Source: exchange
```

---

### Caso 2 — TP1 con SL spostato a BE (Risk 0)

Stessa posizione. SL spostato a avg entry 65,000 dopo TP1.
remaining_risk = 0.005 × (65,000 − 65,000) = **0 USDT**

```
📊 #12 — TP1 FILLED
- - - - - - - - - - - - -
BTC/USDT — 📈 LONG
https://t.me/c/123456/987
- - - - - - - - - - - - -
TP_1: 69,000
Closed: 50%
Qty: 0.005
PnL: +20.00 USDT
Fee rate: 0.055%
Fee: 0.38 USDT
Value: 345.00 USDT
- - - - - - - - - - - - -
Remaining:
Qty: 0.005
Avg entry: 65,000
Risk: 0 USDT
- - - - - - - - - - - - -
Source: exchange
```

> Risk 0: SL a BE — 0 perdita possibile sulla posizione rimanente.

---

### Caso 3 — TP1, LADDER (30% chiuso, 2 TP rimanenti)

Posizione: 0.010 BTC LADDER, avg entry 64,300, SL 39,000.
TP1 30% → chiude 0.003 a 67,000. Rimanente 0.007.
remaining_risk = 0.007 × (64,300 − 39,000) = **177.10 USDT**

```
📊 #13 — TP1 FILLED
- - - - - - - - - - - - -
BTC/USDT — 📈 LONG
https://t.me/c/123456/987
- - - - - - - - - - - - -
TP_1: 67,000
Closed: 30%
Qty: 0.003
PnL: +8.10 USDT
Fee rate: 0.055%
Fee: 0.22 USDT
Value: 201.00 USDT
- - - - - - - - - - - - -
Remaining:
Qty: 0.007
Avg entry: 64,300
Risk: 177.10 USDT
- - - - - - - - - - - - -
Source: exchange
```

---

## Esempi — PARTIAL_CLOSE_EXECUTED

Evento exchange: arriva dopo `UPDATE_DONE` che già lista `CLOSE_PARTIAL` tra le operazioni.
Vedi `Clean_log_updates.md` per l'ack runtime corrispondente.

### Caso 4 — close 50% semplice

Posizione: 0.010 BTC, avg entry 65,000, SL 39,000. Chiude 0.005 a 68,500.
remaining_risk = 0.005 × (65,000 − 39,000) = **130 USDT**

```
✅ #12 — PARTIAL CLOSED
- - - - - - - - - - - - -
BTC/USDT — 📈 LONG
https://t.me/c/123456/987
- - - - - - - - - - - - -
Price: 68,500
Closed: 50%
Qty: 0.005
PnL: +17.50 USDT
Fee rate: 0.055%
Fee: 0.38 USDT
- - - - - - - - - - - - -
Remaining:
Qty: 0.005
Avg entry: 65,000
Risk: 130 USDT
- - - - - - - - - - - - -
Source: trader_update
https://t.me/c/3927267771/376
```

---

### Caso 5 — close 50% + SL moved to BE (Risk 0)

Stessa posizione. SL originale 39,000, spostato a BE = 65,000 dall'update.
remaining_risk = 0.005 × (65,000 − 65,000) = **0 USDT**

```
✅ #12 — PARTIAL CLOSED
- - - - - - - - - - - - -
BTC/USDT — 📈 LONG
https://t.me/c/123456/987
- - - - - - - - - - - - -
Price: 68,500
Closed: 50%
Qty: 0.005
PnL: +17.50 USDT
Fee rate: 0.055%
Fee: 0.38 USDT
- - - - - - - - - - - - -
Remaining:
Qty: 0.005
Avg entry: 65,000
Risk: 0 USDT
- - - - - - - - - - - - -
Source: trader_update
https://t.me/c/3927267771/376
```

> Risk 0 USDT: SL ora a BE — la posizione rimanente non può andare in perdita.
> Il PARTIAL CLOSED non mostra cosa ha fatto l'update (SL move ecc.) — quello è in UPDATE_DONE.

---

### Caso 6 — close 25%, LADDER parzialmente fillato

Posizione: 0.008 BTC (LADDER 80% fillato), avg entry 64,625, SL 39,000.
Chiude 25% della posizione totale pianificata (0.0025 BTC) a 68,000.
remaining_qty = 0.008 − 0.0025 = 0.0055
remaining_risk = 0.0055 × (64,625 − 39,000) = **140.94 USDT**

```
✅ #13 — PARTIAL CLOSED
- - - - - - - - - - - - -
BTC/USDT — 📈 LONG
https://t.me/c/123456/987
- - - - - - - - - - - - -
Price: 68,000
Closed: 25%
Qty: 0.0025
PnL: +8.44 USDT
Fee rate: 0.055%
Fee: 0.09 USDT
- - - - - - - - - - - - -
Remaining:
Qty: 0.0055
Avg entry: 64,625
Risk: 140.94 USDT
- - - - - - - - - - - - -
Source: trader_update
https://t.me/c/3927267771/401
```

---

## Note implementative

Campi iniettati dai transform (`_t_tp_partial` / `_t_partial_close`):

| Campo | `TP_FILLED` | `PARTIAL_CLOSE_EXECUTED` |
|-------|-------------|--------------------------|
| `_emoji` | `"📊"` | `"✅"` |
| `_event_label` | `f"TP{level} FILLED"` | `"PARTIAL CLOSED"` |
| `_price_label` | `f"TP_{level}"` | `"Price"` |
| `_price_value` | `fill_price` ∣ `tp_price` | `fill_price` |
| `_show_value` | `True` | `False` |

Campi dal payload (comuni):

| Campo | Fonte | Nota |
|-------|-------|------|
| `closed_qty` | payload fill | Qty chiusa in questo evento |
| `closed_pct` | payload | % della posizione totale chiusa |
| `remaining_qty` | `filled_entry_qty - cumulative_closed_qty` | Aggiunto in `_build_payload` |
| `remaining_risk` | `remaining_qty × \|avg_entry − current_stop_price\|` | Aggiunto in `_build_payload` |
| `avg_entry` | già nel payload | Invariato su chiusura parziale |

`TP_FILLED_FINAL` (ultimo TP) → `_CLOSED_BLOCKS` + `FINAL_RESULT`, non questo template.
La distinzione è nel payload: `is_final=True` → router usa `TP_FILLED_FINAL`.
