POSITION CLOSED:

Modello unico per tutte le chiusure finali (`TP_FILLED_FINAL`, `SL_FILLED`, `POSITION_CLOSED`, `BE_EXIT`).

Regole:
- `Close reason:` subito sotto il titolo.
- i separatori `- - -` dividono blocchi semantici diversi.
- tutte le categorie sono sempre visibili.
- se un dato non è disponibile, mostrare `n/a`.
- `BE_EXIT` non ha più un layout dedicato: resta solo come semantica in `Close reason: BREAKEVEN_AFTER_TP`.

Template:

<emoji> #<id> — POSITION CLOSED
Close reason: <reason>
- - -
<symbol> — <side>
<signal_link>
- - -
<exit_field>: <price | n/a>
Qty: <qty | n/a>
PnL: <pnl | n/a>
Fee: <fee | n/a>
Fee rate: <fee_rate | n/a>
- - -
Final Result:
ROI net: <roi | n/a>
Total PnL net: <net | n/a>
Gross PnL: <gross | n/a>
Fees: <fees | n/a>
Funding: <funding | n/a>
- - -
Source: <source>
<origin_link se presente>

Esempi:

✅ #12 — POSITION CLOSED
Close reason: FINAL TP FILLED
- - -
BTCUSDT — 📈 LONG
https://t.me/c/123456/987
- - -
TP_2: 70,500
Qty: 0.015
PnL: +45.20 USDT
Fee: 1.03 USDT
Fee rate: 0.055%
- - -
Final Result:
ROI net: +3.67%
Total PnL net: +44.17 USDT
Gross PnL: +45.20 USDT
Fees: -2.06 USDT
Funding: +0.03 USDT
- - -
Source: exchange

🛑 #5 — POSITION CLOSED
Close reason: STOP_LOSS
- - -
ICNTUSDT — 📈 LONG
https://t.me/c/3897279123/499
- - -
SL: 0.2499
Qty: 120
PnL: +3.99 USDT
Fee: 0.69 USDT
Fee rate: 0.055%
- - -
Final Result:
ROI net: +3.67%
Total PnL net: +2.62 USDT
Gross PnL: +3.99 USDT
Fees: -1.37 USDT
Funding: +0.00 USDT
- - -
Source: exchange

⚡ #1 — POSITION CLOSED
Close reason: BREAKEVEN_AFTER_TP
- - -
BTCUSDT — 📈 LONG
https://t.me/c/3897279123/480
- - -
Price: 60,982.7
Qty: 0.01
PnL: +4.44 USDT
Fee: 1.74 USDT
Fee rate: 0.055%
- - -
Final Result:
ROI net: +3.67%
Total PnL net: +0.96 USDT
Gross PnL: +4.44 USDT
Fees: -3.49 USDT
Funding: +0.00 USDT
- - -
Source: trader_update
https://t.me/c/3927267771/376
