```
✅ #12 — SIGNAL ACCEPTED
- - - - - - - - - - - - -
BTC/USDT — 📈 LONG
- - - - - - - - - - - - -
Entry_1: Market ~68,500 (70%)
Entry_2: 67,200 Limit (30%)
SL: 66,400
TP_1: 69,200 (50%)
TP_2: 70,500 (50%)
Risk: 0.5%
Leverage: x5
- - - - - - - - - - - - -
Notes:
Risk - Reduced by trader
- - - - - - - - - - - - -
Trader: Pipsygnal
Exchange Account: main
- - - - - - - - - - - - -
Source: trader_signal
https://t.me/c/123456/987
```

> TWO_STEP + 2 TP — % mostrate su entrambi.

---

```
✅ #13 — SIGNAL ACCEPTED
- - - - - - - - - - - - -
BTC/USDT — 📈 LONG
- - - - - - - - - - - - -
Entry_1: 65,000 Limit (50%)
Entry_2: 64,000 Limit (30%)
Entry_3: 63,000 Limit (20%)
SL: 62,000
TP_1: 67,000 (30%)
TP_2: 69,000 (40%)
TP_3: 72,000 (30%)
Risk: 1.0%
Leverage: x5
- - - - - - - - - - - - -
Trader: Pipsygnal
Exchange Account: main
- - - - - - - - - - - - -
Source: trader_signal
https://t.me/c/123456/987
```

> LADDER 3 leg + 3 TP — split asimmetrico visibile su entrambi.

---

```
✅ #14 — SIGNAL ACCEPTED
- - - - - - - - - - - - -
ETH/USDT — 📈 LONG
- - - - - - - - - - - - -
Entry_1: 3,500 Limit
SL: 3,400
TP_1: 3,650
Risk: 0.8%
Leverage: x3
- - - - - - - - - - - - -
Trader: TraderA
Exchange Account: main
- - - - - - - - - - - - -
Source: trader_signal
https://t.me/c/123456/987
```

> ONE_SHOT + TP singolo — nessuna % (liste con 1 elemento).

---

```
✅ #15 — SIGNAL ACCEPTED
- - - - - - - - - - - - -
BTC/USDT — 📈 LONG
- - - - - - - - - - - - -
Entry_1: 64,000 Limit
SL: 62,000
TP_1: 67,000 (50%)
TP_2: 69,000 (50%)
Risk: 0.8%
Leverage: x5
- - - - - - - - - - - - -
Notes:
Entry - Midpoint [63,000-65,000]
Risk - Reduced by trader
- - - - - - - - - - - - -
Trader: Pipsygnal
Exchange Account: main
- - - - - - - - - - - - -
Source: trader_signal
https://t.me/c/123456/987
```

> RANGE collassato con `midpoint` — la derivazione range compare in `Notes:` e non altera il body operativo.

---

```
❌ — SIGNAL REJECTED
- - - - - - - - - - - - -
ETH/USDT — 📉 SHORT
- - - - - - - - - - - - -
Entry_1: 3,820 Limit (60%)
Entry_2: 3,900 Limit (40%)
SL: 3,910
TP_1: 3,600 (100%)
Risk: 0.8%
Leverage: x5
- - - - - - - - - - - - -
Trader: TraderA
Exchange Account: main
Rejected: max_capital_at_risk_exceeded
- - - - - - - - - - - - -
Source: runtime
https://t.me/c/123456/987
```

> TWO_STEP rejected — % entry mostrate, TP singolo senza %.

---

```
❌ — SIGNAL REJECTED
- - - - - - - - - - - - -
ETH/USDT — 📉 SHORT
- - - - - - - - - - - - -
Entry_1: 3,820 Limit
SL: n/a
TP_1: 4,100
TP_2: 4,250
Risk: 0.8%
Leverage: x5
- - - - - - - - - - - - -
Trader: TraderA
Exchange Account: main
Rejected: missing_stop_loss
- - - - - - - - - - - - -
Source: runtime
https://t.me/c/123456/987
```

> ONE_SHOT rejected per SL mancante — nessuna % (entry singola, 2 TP ma senza `_tp_pcts`
> perché il plan non è stato creato: il rejected avviene prima della plan generation).

---

## Note

| Condizione | % mostrata |
|-----------|-----------|
| entry singola (ONE_SHOT) | no |
| 2+ entry (TWO_STEP / LADDER / RANGE) | sì |
| TP singolo | no |
| 2+ TP | sì, se `_tp_pcts` popolato |
| REJECTED prima del plan | no (liste vuote) |

`_entry_pcts` e `_tp_pcts` sono liste di interi (round). Popolate in `_build_payload` da
`plan["legs"][i]["qty"]` e `plan["tps"][i]["close_pct"]`. Se il plan non esiste (rejected
pre-plan), le liste sono vuote e il renderer non mostra nulla.

---

## REVIEW REQUIRED

Segnale parsato ma flaggato per review umana — posizione non aperta.
Struttura come SIGNAL_ACCEPTED/REJECTED ma **senza `Leverage:`** (campo non in `_REVIEW_REQUIRED_BLOCKS`).
Header senza `#chain_id` — la chain non è ancora stata creata.
Nessuna % su entry/TP — il plan non è ancora stato creato (`_entry_pcts`/`_tp_pcts` vuoti, nessun transform).

```
⚠️ — REVIEW REQUIRED
- - - - - - - - - - - - -
ETH/USDT — 📉 SHORT
https://t.me/c/123456/987
- - - - - - - - - - - - -
Entry_1: 3,820 Limit
SL: n/a
TP_1: 3,600
Risk: 0.8%
- - - - - - - - - - - - -
Trader: TraderA
Exchange Account: main
Rejected: ambiguous_entry_type
- - - - - - - - - - - - -
Source: runtime
```

> `Rejected:` = motivo della review — campo `rejected_reason` nel payload, reso da `include_rejected_reason=True` nel `FooterBlock`.
> `SL: n/a` — SL assente nel segnale parsato.
> Nessun link nel footer — review flaggata da runtime, non da un comando trader.
> Nessun `Leverage:` — `_REVIEW_REQUIRED_BLOCKS` include `*_SIGNAL_BODY` direttamente, senza il `FieldBlock("Leverage")`.

| Condizione | `REVIEW_REQUIRED` |
|-----------|-----------------|
| chain_id | assente (no `#N`) |
| Leverage | assente |
| % entry/TP | assente (no transform, no plan) |
| `Rejected:` in footer | sì, se `rejected_reason` nel payload |

---

## Notes

`Notes:` compare solo se esiste almeno una nota di contesto sul segnale. Al momento le righe previste sono:

- `Entry - Midpoint [min-max]`
- `Entry - Firstpoint [min-max]`
- `Entry - Lastpoint [min-max]`
- `Entry - Endpoints [min-max]`
- `Risk - Reduced by trader`

Regole:

- `Notes:` è mostrato solo per `SIGNAL_ACCEPTED`, `SIGNAL_REJECTED`, `REVIEW_REQUIRED`
- la sezione sta tra body operativo e blocco `Trader / Exchange Account / Rejected`
- nessun marker `*` / `**`
- se non ci sono note, il template resta identico a oggi
