# Dashboard Tab PNL — Layout Examples (Redesign)

Design approvato in sessione 2026-06-24.
Sostituisce la sezione `/pnl` di `Cmd_trades_pnl_stats.md` per il contesto dashboard.

---

## Principi

- `Equity` rimossa — sostituita da `Futures wallet = Available + Margin in use`
- Snapshot è sempre **account-wide** (Bybit non espone dati per-trader)
- Label esplicita `Account snapshot (account_id):` quando scope ha trader specifici
- Realized PnL diviso in **Closed** (trade CLOSED) + **Partial open** (trade PARTIALLY_CLOSED con fills)
- Breakdown `By trader:` appare solo con 2+ trader nello scope
- `Open risk*` mostrato se disponibile (da snapshot o calcolato da ops_trade_chains)
- Refresh automatico su nuovo snapshot account (throttle 5s esistente)

---

## Scope 1 — Account singolo, tutti i trader

```
💰 PnL — demo_1
─────────────────────────────────────
Total: 1   Page: 1/1   Updated: 14:32:05
─────────────────────────────────────
Account snapshot (demo_1):
  Available:      1,234.00 USDT
  Margin in use:    456.00 USDT
  ─────────────────────────────
  Futures wallet: 1,690.00 USDT
  uPnL live:        +12.34 USDT
  Open risk*:       123.00 USDT
  Snapshot: 14:32:05 UTC · age 45s
─────────────────────────────────────
Realized — demo_1:
  Closed:        +890.20 USDT
  Partial open:   +45.80 USDT
  ─────────────────────────────
  Totale:        +936.00 USDT
─────────────────────────────────────
Open: 5 · Waiting entry: 1
```

> `Open risk*` = somma `risk_amount` dai trade aperti (da `risk_snapshot_json`).
> `Partial open` = `cumulative_gross_pnl - fees - funding` dei trade in stato PARTIALLY_CLOSED.

---

## Scope 2 — Trader singolo

```
💰 PnL — demo_1 · trader_a
─────────────────────────────────────
Total: 1   Page: 1/1   Updated: 14:32:05
─────────────────────────────────────
Account snapshot (demo_1):
  Available:      1,234.00 USDT
  Margin in use:    456.00 USDT
  ─────────────────────────────
  Futures wallet: 1,690.00 USDT
  uPnL live:        +12.34 USDT
  Open risk*:       123.00 USDT
  Snapshot: 14:32:05 UTC · age 45s
─────────────────────────────────────
Realized — trader_a:
  Closed:        +234.50 USDT
  Partial open:   +45.20 USDT
  ─────────────────────────────
  Totale:        +279.70 USDT
─────────────────────────────────────
Open: 3 · Waiting entry: 1
```

> Snapshot è sempre `demo_1` completo — label chiarisce che non è per-trader.
> `Realized — trader_a` filtra `ops_trade_chains` per `trader_id`.

---

## Scope 3 — 2 trader sullo stesso account

```
💰 PnL — demo_1 · trader_a, trader_b
─────────────────────────────────────
Total: 1   Page: 1/1   Updated: 14:32:05
─────────────────────────────────────
Account snapshot (demo_1):
  Available:      1,234.00 USDT
  Margin in use:    456.00 USDT
  ─────────────────────────────
  Futures wallet: 1,690.00 USDT
  uPnL live:        +12.34 USDT
  Open risk*:       220.00 USDT
  Snapshot: 14:32:05 UTC · age 45s
─────────────────────────────────────
Realized — trader_a, trader_b:
  Closed:        +1,124.70 USDT
  Partial open:    +112.90 USDT
  ─────────────────────────────
  Totale:        +1,237.60 USDT

By trader:
  trader_a · Open: 3 · Risk: 120.00 · Closed: +890.20 · Partial: +45.80
  trader_b · Open: 2 · Risk: 100.00 · Closed: +234.50 · Partial: +67.10
─────────────────────────────────────
Open: 5 · Waiting entry: 1
```

---

## Scope 4 — 3 trader sullo stesso account

```
💰 PnL — demo_1 · trader_a, trader_b, trader_c
─────────────────────────────────────
Total: 1   Page: 1/1   Updated: 14:32:05
─────────────────────────────────────
Account snapshot (demo_1):
  Available:      1,234.00 USDT
  Margin in use:    890.00 USDT
  ─────────────────────────────
  Futures wallet: 2,124.00 USDT
  uPnL live:        +34.50 USDT
  Open risk*:       345.00 USDT
  Snapshot: 14:32:05 UTC · age 22s
─────────────────────────────────────
Realized — trader_a, trader_b, trader_c:
  Closed:        +1,234.50 USDT
  Partial open:    +156.80 USDT
  ─────────────────────────────
  Totale:        +1,391.30 USDT

By trader:
  trader_a · Open: 3 · Risk: 120.00 · Closed: +890.20 · Partial: +45.80
  trader_b · Open: 2 · Risk: 100.00 · Closed: +234.50 · Partial: +67.10
  trader_c · Open: 3 · Risk: 125.00 · Closed: +109.80 · Partial: +43.90
─────────────────────────────────────
Open: 8 · Waiting entry: 2
```

> `By trader:` appare solo se 2+ trader nello scope.
> `Risk` per trader = somma `risk_amount` dai trade aperti di quel trader.
> `Open` per trader = count trade OPEN + PARTIALLY_CLOSED di quel trader.

---

## Scope 5 — Globale (multi-account)

```
💰 PnL — All accounts
─────────────────────────────────────
Total: 3   Page: 1/1   Updated: 14:32:05
─────────────────────────────────────
Accounts: 3 · Snapshots: 2 fresh · 1 stale
Futures wallet: 4,230.00 USDT   (fresh only)
Available:      2,890.00 USDT
Margin in use:  1,340.00 USDT
uPnL aggregate:   +87.40 USDT
─────────────────────────────────────
Realized — All accounts:
  Closed:      +1,234.50 USDT
  Partial open:  +156.80 USDT
  ─────────────────────────────
  Totale:      +1,391.30 USDT
─────────────────────────────────────
Open: 8 · Waiting entry: 2
─────────────────────────────────────
By account:
  demo_1 · Avail: 1,450 · Margin: 890 · Net: +890.20 · age 32s
  demo_2 · Avail: 1,440 · Margin: 450 · Net: +344.30 · age 18s
  demo_3 · STALE · last 4m ago · Net: +156.80
```

> Aggregati header = somma solo account con snapshot < 180s (fresh).
> Account STALE incluso nel breakdown PnL storico ma escluso dai totali di balance.
> `Open risk*` non mostrato in scope globale (è campo per-account snapshot).

---

## Edge case — Snapshot stale (singolo account)

```
💰 PnL — demo_1
─────────────────────────────────────
Total: 1   Page: 1/1   Updated: 14:32:05
─────────────────────────────────────
Account snapshot (demo_1):
  Available:      1,234.00 USDT
  Margin in use:    456.00 USDT
  ─────────────────────────────
  Futures wallet: 1,690.00 USDT
  uPnL live:        +12.34 USDT
  Open risk*:       123.00 USDT
  Snapshot: 14:28:41 UTC · age 213s · STALE
─────────────────────────────────────
Realized — demo_1:
  Closed:        +890.20 USDT
  Partial open:   +45.80 USDT
  ─────────────────────────────
  Totale:        +936.00 USDT
─────────────────────────────────────
Open: 5 · Waiting entry: 1
```

> `STALE` = age > 180s. Dati mostrati ma marcati.

---

## Edge case — Nessun snapshot disponibile

```
💰 PnL — demo_1
─────────────────────────────────────
Total: 1   Page: 1/1   Updated: 14:32:05
─────────────────────────────────────
Account snapshot (demo_1):
  n/a — nessun snapshot disponibile
─────────────────────────────────────
Realized — demo_1:
  Closed:        +890.20 USDT
  Partial open:   +45.80 USDT
  ─────────────────────────────
  Totale:        +936.00 USDT
─────────────────────────────────────
Open: 5 · Waiting entry: 1
```

---

## Edge case — Nessun trade chiuso, nessun partial

```
💰 PnL — demo_1
─────────────────────────────────────
Total: 1   Page: 1/1   Updated: 14:32:05
─────────────────────────────────────
Account snapshot (demo_1):
  Available:     10,000.00 USDT
  Margin in use:      0.00 USDT
  ─────────────────────────────
  Futures wallet: 10,000.00 USDT
  uPnL live:          0.00 USDT
  Snapshot: 14:32:05 UTC · age 12s
─────────────────────────────────────
Realized — demo_1:
  Nessun trade chiuso.
─────────────────────────────────────
Open: 0 · Waiting entry: 0
```

---

## Mapping metriche → sorgente dati

| Metrica display | Campo DB / sorgente |
|---|---|
| `Available` | `ops_account_snapshots.available_balance_usdt` → Bybit `totalAvailableBalance` |
| `Margin in use` | `ops_account_snapshots.total_margin_used_usdt` → Bybit `totalInitialMargin` |
| `Futures wallet` | `available + margin_used` (calcolato lato display) |
| `uPnL live` | `ops_account_snapshots.account_unrealized_pnl_usdt` → Bybit `totalPerpUPL` |
| `Open risk*` | `ops_account_snapshots.total_open_risk_usdt` oppure SUM(`risk_amount`) da `ops_trade_chains.risk_snapshot_json` WHERE open |
| `Realized Closed` | SUM(`cumulative_gross_pnl - cumulative_fees - cumulative_funding`) WHERE `lifecycle_state='CLOSED'` |
| `Realized Partial open` | SUM(`cumulative_gross_pnl - cumulative_fees - cumulative_funding`) WHERE `lifecycle_state='PARTIALLY_CLOSED'` |
| `By trader Risk` | SUM(`risk_amount` da `risk_snapshot_json`) per trader WHERE open |
| `By trader Open` | COUNT WHERE `lifecycle_state IN ('OPEN','PARTIALLY_CLOSED')` per trader |
| `Snapshot age` | `now() - ops_account_snapshots.captured_at` |
| `STALE` | age > 180s |
