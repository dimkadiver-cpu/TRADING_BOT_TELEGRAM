# Template — `/dashboard`

Messaggio inline pinnabile, **uno per topic**.
Creato con `/dashboard` da topic `clean_log` o `commands`; non disponibile in `tech_log`.

Aggiornato in-place tramite `edit_message_text`: nessun messaggio aggiuntivo durante refresh, callback o lifecycle update.

---

## Keyboard

```text
[⚡ Active]  [✅ Closed]  [🚫 Blocked]
[💰 PnL]     [📉 Stats]   [🔄 Refresh]
[← Prev]     [Page 2/5]  [Next →]
```

Regole:

* Terza riga visibile solo per `Active`, `Closed`, `Blocked` e solo se i trade sono più di 5.
* `← Prev` assente sulla prima pagina.
* `Next →` assente sull’ultima pagina.
* `Page N/M` è un bottone inerte: `callback_data = "noop"`.
* Cambio vista: reset pagina a `0`.
* Default alla creazione: `active:0`.

---

## Dashboard appena creato — trader scope

```text
📊 Dashboard — demo_1 · trader_a
────────────────────────
Updated: 14:32:05

Select a view or pin this message.
```

---

## Dashboard appena creato — account scope

```text
📊 Dashboard — demo_1
────────────────────────
Updated: 14:32:05

Select a view or pin this message.
```

---

# View: Active — trader scope

```text
📊 ⚡ Active — demo_1 · trader_a
────────────────────────
Updated: 14:32:05 · Position snapshot: 18s ago

#5  BTC/USDT  LONG  PARTIALLY_CLOSED
Source: Signal

Entry: 63,500 ✓ · 63,200 × · 62,800 ×
TP:    64,000 ✓ · 65,200 · 66,500
SL:    62,000 · BE: Yes
uPnL:  +34.20 USDT

Actions: /trade 5 · /cancel 5 · /close 5
- - - - - - - - - - - - - - - - - - - -

#9  SOL/USDT  LONG  WAITING_ENTRY
Source: Signal

Entry: 148.50 · 147.00
TP:    155.00 · 160.00
SL:    143.00
Status: Waiting for fill

Actions: /trade 9 · /cancel 9 · /close 9
```

Legenda:

```text
✓ = Filled
× = Cancelled
nessun simbolo = Pending
```

---

## View: Active — account scope

```text
📊 ⚡ Active — demo_1
────────────────────────
Updated: 14:32:05 · Position snapshot: 18s ago

#5  BTC/USDT  LONG   OPEN  [trader_a]
Entry: 63,500 ✓
TP:    64,000
SL:    62,800 · BE: Yes
uPnL:  +12.40 USDT
Source: Signal
- - - - - - - - - - - - - - - - - - - -

#7  ETH/USDT  SHORT  OPEN  [trader_b]
Entry: 2,140 ✓
TP:    2,000
SL:    2,180
uPnL:  -3.20 USDT
Source: Signal
```

---

## View: Active — nessun trade

```text
📊 ⚡ Active — demo_1 · trader_a
────────────────────────
Updated: 14:32:05

No active trades.
```

---

## Snapshot stale

```text
📊 ⚡ Active — demo_1 · trader_a
────────────────────────
Updated: 14:32:05 · Position snapshot: 183s ago ⚠️

#5  BTC/USDT  LONG  OPEN
Entry: 63,500 ✓
TP:    64,000
SL:    62,800
uPnL:  +12.40 USDT
```

---

# View: Closed

```text
📊 ✅ Closed — demo_1 · trader_a
────────────────────────
Updated: 14:32:05

#22  BTC/USDT  LONG  CLOSED
Reason: STOP_LOSS
Opened: 14 Jun 11:52 · Signal
Closed: 14 Jun 14:26 · Close event
Net PnL: -3.20 USDT · ⏱ 2h 34m
- - - - - - - - - - - - - - - - - - - -

#18  SOL/USDT  LONG  CLOSED
Reason: TP_COMPLETE
Opened: 14 Jun 09:10 · Signal
Closed: 14 Jun 13:55 · Close event
Net PnL: +34.50 USDT · ⏱ 4h 45m
```

Trade annullato senza fill:

```text
#24  ETH/USDT  LONG  CANCELLED_UNFILLED
Reason: CANCEL_PENDING
Created: 14 Jun 16:12 · Signal
PnL: — · No fill
```

Nessun trade:

```text
📊 ✅ Closed — demo_1 · trader_a
────────────────────────
Updated: 14:32:05

No closed trades.
```

---

# View: Blocked

```text
📊 🚫 Blocked — demo_1 · trader_a
────────────────────────
Updated: 14:32:05

#7  ETH/USDT  LONG  REVIEW_REQUIRED
Reason: missing_sl
Blocked: 14 Jun 11:52
Source: Signal
- - - - - - - - - - - - - - - - - - - -

#12  SOL/USDT  LONG  EXEC_FAILED
Reason: insufficient_margin
Blocked: 14 Jun 14:26
Source: Technical error
```

Nessun trade:

```text
📊 🚫 Blocked — demo_1 · trader_a
────────────────────────
Updated: 14:32:05

No blocked trades.
```

---

# View: PnL

```text
📊 💰 PnL — demo_1 · trader_a
────────────────────────
Updated: 14:32:05 · Account snapshot: 18s ago

Account demo_1:
Equity:        10,432.50 USDT
Balance:        9,100.00 USDT
Margin used:      820.00 USDT

Realized — trader_a:
Gross:          +142.60 USDT
Fees:            -11.20 USDT
Net:            +130.00 USDT

Open: 1 · Waiting entry: 1
```

Per scope account:

```text
Realized — All traders:
```

---

# View: Stats

```text
📊 📉 Stats — demo_1 · trader_a
────────────────────────
Updated: 14:32:05

Period          Trades   Win%      Net
Today                1   100%   +18.40
Last 7d             6    67%   +62.10
Last 30d           19    63%  +148.30
All time           31    61%   +98.20

Best:  #8  SOL/USDT  +34.50 USDT
Worst: #22 BNB/USDT -12.80 USDT
```

---

## Paginazione

Prima pagina:

```text
[⚡ Active]  [✅ Closed]  [🚫 Blocked]
[💰 PnL]     [📉 Stats]   [🔄 Refresh]
[Page 1/3]   [Next →]
```

Pagina intermedia:

```text
[⚡ Active]  [✅ Closed]  [🚫 Blocked]
[💰 PnL]     [📉 Stats]   [🔄 Refresh]
[← Prev]     [Page 2/3]  [Next →]
```

Ultima pagina:

```text
[⚡ Active]  [✅ Closed]  [🚫 Blocked]
[💰 PnL]     [📉 Stats]   [🔄 Refresh]
[← Prev]     [Page 3/3]
```

---

## Comandi trade

```text
/trade <id>      # mostra dettaglio trade
/cancel <id>     # cancella gli entry pending del trade
/close <id>      # richiede chiusura del trade
```

Non usare `/cancel_all` nella card di un singolo trade: è ambiguo e rischia di agire su più chain.

---

## Risposta da tech_log

```text
Command is not available in this topic.
```

## Callback da dashboard non più attiva

```text
Dashboard is no longer active. Use /dashboard to create a new one.
```
