# `/dashboard` — Functional Specification

## 1. Purpose and invariants

`/dashboard` creates a **single pin-able inline dashboard per Telegram topic**.

- Available only in `clean_log` and `commands` topics.
- Not available in `tech_log`.
- The same Telegram message is updated in place through `edit_message_text`.
- Refreshes, callbacks, and lifecycle events must never create another dashboard message.
- One dashboard has one immutable **scope**; filters may only narrow that scope.

### Unsupported topic

```text
Command is not available in this topic.
```

### Expired or inactive dashboard callback

```text
Dashboard is no longer active. Use /dashboard to create a new one.
```

---

## 2. Scope model

A dashboard has two layers of visibility.

```text
Dashboard scope
→ the maximum set of visible data, resolved when /dashboard is executed.

Filters
→ temporary restrictions applied inside that fixed scope.
```

### 2.1 Scope types

| Scope | Header example | Visible data | Trader selector |
|---|---|---|---|
| Trader scope | `demo_1 · trader_a` | Only that trader within the account | Hidden |
| Account scope | `demo_1` | All traders within the account | Available |

A trader-scoped dashboard never exposes a trader filter because its result is fixed by scope.

### 2.2 Example

```text
Dashboard scope: demo_1, all traders
Global trader filter: trader_a
Active filter: Open

Result:
only OPEN trades for trader_a in demo_1.
```

---

## 3. Initial state and dashboard lifecycle

### 3.1 Default state

When `/dashboard` creates a new dashboard:

```text
current_view = active
current_page = 0
all filters = default values
```

The first render must therefore show the **Active** view directly, or `No active trades.` when the scoped result is empty.

> The former `Select a view or pin this message.` splash screen is intentionally removed because it conflicts with the required default `active:0` state.

### 3.2 Header contract

Every view uses this structure:

```text
<view icon> <View name> — <account> [· <trader>]
- - - - - - - - - - - - - - - - - - - -
Updated: HH:MM:SS
[Filters: <active filters>]

<view content>
```

- The trader suffix is present only for trader scope, or when the current account-scoped view is filtered to one trader.
- `Filters:` appears only when at least one filter is active.
- `Updated:` is the render/snapshot timestamp. It is not a replacement for a freshness state; see section 10.4.

---

## 4. Main keyboard

### 4.1 Canonical layout

```text
[⚡ Active]  [✅ Closed]  [🚫 Blocked]
[💰 PnL]     [📉 Stats]   [🔄 Refresh]
[🔎 Filters] [🧹 Clear]
[← Prev]     [Page 2/5]  [Next →]
```

### 4.2 Behaviour

- `⚡ Active`, `✅ Closed`, `🚫 Blocked`, `💰 PnL`, and `📉 Stats` switch the current view.
- Changing view sets `current_page = 0`.
- `🔄 Refresh` reloads data while keeping the current view, page, and filters.
- `🔎 Filters` opens the filter panel for the current view.
- Main `🧹 Clear` removes **all** filters, including the global trader filter, then sets `current_page = 0`.
- `Page N/M` is inert: `callback_data = "noop"`.
- Pagination exists only in `Active`, `Closed`, and `Blocked`.
- The pagination row is shown only when the filtered result contains more than five items.

### 4.3 Pagination variants

First page:

```text
[Page 1/3]   [Next →]
```

Intermediate page:

```text
[← Prev]     [Page 2/3]  [Next →]
```

Last page:

```text
[← Prev]     [Page 3/3]
```

Rules:

- `← Prev` is absent on the first page.
- `Next →` is absent on the last page.
- Page count is calculated **after** all active filters are applied.
- If an update makes the selected page invalid, render the final valid page.

---

## 5. Shared trade conventions

### 5.1 Entry and target markers

```text
✓ = Filled
✗ = Cancelled
no marker = Pending
```

### 5.2 Trade commands shown in Active cards

```text
/trade <id>      # show trade details
/cancel <id>     # cancel pending entry orders for this trade
/close <id>      # request closure for this trade
```

`/cancel_all` must not appear in a single-trade card because it is ambiguous and could affect more than one chain.

---

## 6. Views

## 6.1 Active

### Purpose

Shows non-terminal trades inside the dashboard scope.

### Trader-scope example

```text
⚡ Active — demo_1 · trader_a
- - - - - - - - - - - - - - - - - - - -
Updated: 14:32:05

#5  BTC/USDT  LONG  PARTIALLY_CLOSED
Source: Signal

Entry: 63,500 ✓ · 63,200 ✗ · 62,800 ✗
TP:    64,000 ✓ · 65,200 · 66,500
SL:    62,000 · BE: Yes
uPnL:  +34.20 USDT  rPnL:  +14.20 USDT

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

### Account-scope example

```text
⚡ Active — demo_1
- - - - - - - - - - - - - - - - - - - -
Updated: 14:32:05

#5  BTC/USDT  LONG  OPEN  [trader_a]
Entry: 63,500 ✓
TP:    64,000
SL:    62,800 · BE: Yes
uPnL:  +12.40 USDT rPnL:  -0.20 USDT
Source: Signal
- - - - - - - - - - - - - - - - - - - -

#7  ETH/USDT  SHORT  OPEN  [trader_b]
Entry: 2,140 ✓
TP:    2,000
SL:    2,180
uPnL:  -3.20 USDT rPnL:  -0.20 USDT
Source: Signal
```

### Empty state

```text
⚡ Active — demo_1 · trader_a
- - - - - - - - - - - - - - - - - - - -
Updated: 14:32:05

No active trades.
```

### Active status filter mapping

| UI value | Canonical lifecycle value |
|---|---|
| Waiting entry | `WAITING_ENTRY` |
| Partially filled | `PARTIALLY_FILLED` |
| Open | `OPEN` |
| Partially closed | `PARTIALLY_CLOSED` |
| Closing | `CLOSE_PENDING` |

---

## 6.2 Closed

### Purpose

Shows terminal trade chains, including cancelled chains with no fill.

```text
✅ Closed — demo_1 · trader_a
- - - - - - - - - - - - - - - - - - - -
Updated: 14:32:05

#22  BTC/USDT  LONG  
Reason: STOP_LOSS
Opened: 14 Jun 11:52 · Signal
Closed: 14 Jun 14:26 · Close event
Net PnL: -3.20 USDT · ⏱ 2h 34m
- - - - - - - - - - - - - - - - - - - -

#18  SOL/USDT  LONG  
Reason: TP_COMPLETE
Opened: 14 Jun 09:10 · Signal
Closed: 14 Jun 13:55 · Close event
Net PnL: +34.50 USDT · ⏱ 4h 45m
```

### Cancelled without fill

```text
#24  ETH/USDT  LONG  CANCELLED_UNFILLED
Reason: CANCEL_PENDING
Created: 14 Jun 16:12 · Signal
PnL: — · No fill
```

### Empty state

```text
✅ Closed — demo_1 · trader_a
- - - - - - - - - - - - - - - - - - - -
Updated: 14:32:05

No closed trades.
```

### Exit filter mapping

The mapping is based on `close_reason`, not only on lifecycle state.

| UI value | `close_reason` |
|---|---|
| Take profit | `TP_COMPLETE` |
| Stop loss | `SL_HIT`, `STOP_LOSS` |
| Manual close | `MANUAL_CLOSE` |
| Exchange close | `EXCHANGE_CLOSE` |
| Cancelled no fill | `CANCELLED_UNFILLED` |
| Other | `UNKNOWN` and other terminal reasons |

### Closed period rule

The selected period is calculated from the final closing timestamp (`closed_at`).

`CANCELLED_UNFILLED` is shown in `Closed`, but it is excluded from net PnL, win rate, best, and worst calculations.

---

## 6.3 Blocked

### Purpose

Shows trade chains that need manual intervention or reconciliation.

```text
🚫 Blocked — demo_1 · trader_a
- - - - - - - - - - - - - - - - - - - -
Updated: 14:32:05

#7  ETH/USDT  LONG  
Reason: missing_sl
Blocked: 14 Jun 11:52
Source: Signal
- - - - - - - - - - - - - - - - - - - -

#12  SOL/USDT  LONG  
Reason: insufficient_margin
Blocked: 14 Jun 14:26
Source: Technical error          
```

### Empty state

```text
🚫 Blocked — demo_1 · trader_a
- - - - - - - - - - - - - - - - - - - -
Updated: 14:32:05

No blocked trades.
```

### Blocked type filter mapping

| UI value | Canonical value |
|---|---|
| Review required | `REVIEW_REQUIRED` |
| Execution failed | `EXEC_FAILED` |
| Reconciliation required | `RECONCILIATION_REQUIRED` |

### Blocked age rule

Age is calculated from `blocked_at`; use `updated_at` only as fallback.

---

## 6.4 PnL

### Purpose

Shows current account-level balances plus realized performance for the filtered scope.

```text
💰 PnL — demo_1 · trader_a
- - - - - - - - - - - - - - - - - - - -
Updated: 14:32:05

Account snapshot:
Equity:        10,432.50 USDT
Balance:        9,100.00 USDT
Margin used:      820.00 USDT

Realized — trader_a:
Gross:          +142.60 USDT
Fees:            -11.20 USDT
Net:            +130.00 USDT

Open: 1 · Waiting entry: 1
```

For account scope without a trader filter:

```text
Realized — All traders:
```

### PnL rules

- Equity, Balance, and Margin used are always account-level snapshot values.
- Gross, Fees, and Net respect the global trader filter and the PnL period filter.
- Realized PnL period is calculated from `closed_at`.
- `Open` and `Waiting entry` are **current-state counts**, respecting scope and trader filter but not PnL period. A historical period cannot consistently describe current non-terminal trades.
- The title or filter line must clearly show active trader and period filters.

Example:

```text
💰 PnL — demo_1
- - - - - - - - - - - - - - - - - - - -
Updated: 14:32:05
Filters: trader_a · Last 7d

Account snapshot:
Equity:        10,432.50 USDT
Balance:        9,100.00 USDT
Margin used:      820.00 USDT

Realized — trader_a · Last 7d:
Gross:          +142.60 USDT
Fees:            -11.20 USDT
Net:            +130.00 USDT
```

---

## 6.5 Stats

### Purpose

Shows performance aggregates for multiple fixed periods at once.

```text
📉 Stats — demo_1 · trader_a
- - - - - - - - - - - - - - - - - - - -
Updated: 14:32:05

Period          Trades   Win%      Net
Today                1   100%   +18.40
Last 7d             6    67%   +62.10
Last 30d           19    63%  +148.30
All time           31    61%   +98.20

Best:  #8  SOL/USDT  +34.50 USDT
Worst: #22 BNB/USDT -12.80 USDT
```

### Stats rules

- Stats respect the global trader filter and Stats side filter.
- Stats have no user-selectable period filter because the view already contains `Today`, `Last 7d`, `Last 30d`, and `All time`.
- Do not add `Wins only` or `Losses only`: those filters would make win rate mechanically misleading.
- `CANCELLED_UNFILLED` is excluded from trade count, win rate, net PnL, best, and worst.

---

## 7. Filter system

## 7.1 Global behaviour

```text
- The trader filter is global and persists when the user changes views.
- Every other filter is local to its own view.
- Any filter change sets current_page = 0.
- Refresh preserves all current filters.
- Pagination is calculated after filtering.
- The current filter summary is displayed directly under the header.
```

Example summary:

```text
Filters: trader_a · Open · Long
```

### Clear actions

| Button | Effect |
|---|---|
| Main `[🧹 Clear]` | Clears the global trader filter and every view-specific filter |
| Panel `[🧹 Clear view]` | Clears only the filters for the active view; keeps the global trader filter |

## 7.2 Shared trader selector

Available only in account scope.

```text
[All traders]
[trader_a]  [trader_b]  [trader_c]
[← Back]
```

When the number of traders exceeds one keyboard page, the selector is paginated.

## 7.3 Shared side selector

```text
[All sides]  [Long]  [Short]
[← Back]
```

## 7.4 Shared realized-period selector

Used by `Closed` and `PnL`.

```text
[All time]  [Today]  [Last 7d]
[Last 30d]  [This month]
[← Back]
```

---

## 8. Filter panels

## 8.1 Active filters

```text
🔎 Filters — Active
- - - - - - - - - - - - - - - - - - - -
Trader: All traders
Status: All statuses
Side: All sides

[Trader ▸]  [Status ▸]  [Side ▸]
[🧹 Clear view]  [← Back]
```

Status selector:

```text
[All statuses]
[Waiting entry]      [Partially filled]
[Open]               [Partially closed]
[Closing]
[← Back]
```

## 8.2 Closed filters

```text
🔎 Filters — Closed
- - - - - - - - - - - - - - - - - - - -
Trader: All traders
Exit: All exits
Period: All time

[Trader ▸]  [Exit ▸]  [Period ▸]
[🧹 Clear view]  [← Back]
```

Exit selector:

```text
[All exits]
[Take profit]      [Stop loss]
[Manual close]     [Exchange close]
[Cancelled no fill]
[Other]
[← Back]
```

## 8.3 Blocked filters

```text
🔎 Filters — Blocked
- - - - - - - - - - - - - - - - - - - -
Trader: All traders
Type: All types
Age: Any age

[Trader ▸]  [Type ▸]  [Age ▸]
[🧹 Clear view]  [← Back]
```

Type selector:

```text
[All types]
[Review required]
[Execution failed]
[Reconciliation required]
[← Back]
```

Age selector:

```text
[Any age]  [Last hour]  [Last 24h]
[Older than 24h]
[← Back]
```

## 8.4 PnL filters

```text
🔎 Filters — PnL
- - - - - - - - - - - - - - - - - - - -
Trader: All traders
Period: All time

[Trader ▸]  [Period ▸]
[🧹 Clear view]  [← Back]
```

## 8.5 Stats filters

```text
🔎 Filters — Stats
- - - - - - - - - - - - - - - - - - - -
Trader: All traders
Side: All sides

[Trader ▸]  [Side ▸]
[🧹 Clear view]  [← Back]
```

---

## 9. Persisted dashboard state

Use a single versioned JSON field rather than many independently evolving columns.

```json
{
  "version": 1,
  "current_view": "active",
  "current_page": 0,
  "filters": {
    "trader_id": null,
    "active": {
      "status": null,
      "side": null
    },
    "closed": {
      "exit_reason": null,
      "period": "all_time"
    },
    "blocked": {
      "type": null,
      "age": "any_age"
    },
    "pnl": {
      "period": "all_time"
    },
    "stats": {
      "side": null
    }
  }
}
```

### Minimum persisted dashboard identity

The dashboard record must also contain, outside this UI-state JSON:

```text
telegram_chat_id
telegram_message_id
topic_id
account_id
scope_trader_id          # null for account scope
is_active
created_at
updated_at
```

---

## 10. Refresh and auto-update behaviour

### 10.1 Event-driven render flow

```text
1. A lifecycle event or account snapshot arrives.
2. Reload data inside the immutable dashboard scope.
3. Apply the persisted filters.
4. Recalculate rendered text, pagination, and keyboard.
5. Clamp current_page to the final valid page when necessary.
6. Compare the new render with the last successful render.
7. Call edit_message_text only when text or keyboard changed.
```

### 10.2 Filtered-out events

An event outside the active filters must not cause an edit when it changes neither visible content nor visible counts.

### 10.3 No-op render rule

When both text and keyboard are unchanged, do not call Telegram edit APIs.

### 10.4 Snapshot freshness

The source specification contains a “stale snapshot” example but no visible stale indicator. A fresh dashboard must distinguish stale account data explicitly, for example:

```text
Snapshot: stale · last exchange sync 14:29:11
```

Without this line, `Updated:` only shows when the dashboard was rendered and can falsely suggest that account/PnL information is current. This indicator should appear in Active and PnL whenever the snapshot exceeds the configured freshness threshold.

---

## 11. Acceptance criteria

1. `/dashboard` creates at most one active dashboard per topic.
2. The dashboard opens on `Active`, page 1.
3. Every callback edits the original dashboard message only.
4. Scope cannot be expanded through filtering.
5. Account scope exposes the trader selector; trader scope does not.
6. Global trader filtering persists across all views.
7. View-local filters do not leak to unrelated views.
8. `Clear view` preserves the global trader filter; main `Clear` removes it.
9. Filtered pagination is correct and page bounds are always valid.
10. `CANCELLED_UNFILLED` is visible in Closed but excluded from performance calculations.
11. PnL account snapshot values remain account-level even when trader filters are active.
12. No Telegram edit is sent when the rendered message and keyboard have not changed.
