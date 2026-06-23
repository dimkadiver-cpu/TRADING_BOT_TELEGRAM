# Dashboard Stats Filters Design

> Based on review of `docs/Raggionamento/spec_dashboard_stats_filters.md` and aligned to the current control-plane implementation.

## Goal

Make the Telegram dashboard `Stats` view semantically correct and operationally coherent across three scopes:

- single trader
- single account
- global `commands` topic across all accounts and traders

The work also fixes cross-view filter behavior, corrects `Closed` net PnL labeling, and introduces a hierarchical global stats breakdown `Account -> Trader`.

## Confirmed Product Decisions

- Scope filters remain visible in all dashboard scopes, but are limited by the immutable base scope.
- Trader selection is only valid after account selection.
- `PnL` does not expose `period` in this change.
- `Closed` keeps `side` as a local per-view filter.
- Compatibility with legacy flat `filters_json` is lazy and write-forward.

## Current-Code Findings

The current code confirms the main problems from the original spec:

- `StatusQueries.get_stats()` filters by `created_at`, not closure time.
- Win rate is currently derived from `cumulative_gross_pnl > 0`.
- Best and worst trades are ordered by gross PnL, not net PnL.
- Global stats breakdown is account-only and lacks trader hierarchy.
- `dashboard_manager.py` stores flat filters and applies them without hierarchy-aware normalization.
- Trader selector currently loads distinct traders globally, even when no account is selected.
- `Closed` renders `Net PnL` while passing `gross_pnl` to the template.
- Formatters compute a narrowed scope, but the filter model is still too flat to safely isolate scope filters from local view filters.

## Architecture

The change remains a targeted refactor, not a dashboard rewrite.

Responsibilities stay split this way:

- `status_queries.py`: semantic source of truth for stats, closed trades, and selector datasets
- `dashboard_manager.py`: persistent dashboard state, filter normalization, callback orchestration
- `formatters/dashboard.py`: effective scope derivation and per-view payload shaping
- `formatters/templates/dashboard.py`: presentation-only rendering with dedicated Stats header and breakdown layout

This keeps risk contained while replacing the fragile parts of the current flow.

## Scope Model

### Base scope

Each dashboard has an immutable base scope persisted by existing columns:

- `scope_account_id`
- `scope_trader_id`

That base scope derives from where the dashboard was opened:

- global `commands` topic -> all accounts and traders
- account clean-log fallback -> one account, all traders
- trader clean-log dashboard -> one account, one trader

### Effective scope

The user can only narrow the base scope.

Effective scope is computed as:

`effective_scope = base_scope + filters.scope`

Rules:

- filters never expand the base scope
- account filter is accepted only if compatible with base scope
- trader filter is accepted only if compatible with selected account and base scope
- trader-specific dashboards still show account/trader controls, but only within their fixed allowed values

## Filter Persistence Model

Replace the flat filter dictionary with this normalized structure:

```json
{
  "scope": {
    "account": "demo_1",
    "trader": "trader_a"
  },
  "views": {
    "active": {
      "status": "OPEN",
      "side": "LONG"
    },
    "closed": {
      "period": "week",
      "side": "LONG"
    },
    "stats": {
      "side": "LONG"
    },
    "pnl": {}
  }
}
```

### Normalization Rules

- changing `scope.account` always removes `scope.trader`
- if `scope.account` is absent, `scope.trader` must be absent
- `scope.trader` must validate against the selected account and the base scope
- unsupported filters for the current view are ignored and not rendered
- `Reset all` clears both `scope` and all `views`
- `Clear Account` also clears `Trader`
- `Clear Trader` preserves `Account`
- local clear actions affect only the current view branch under `views`

### Legacy Compatibility

Legacy payloads such as:

```json
{"account":"demo_1","side":"LONG"}
```

are normalized in memory on read into the new structure. The normalized shape is persisted on the first write. No DB migration is required.

## Stats Semantics

`Stats` must use shared expressions and shared inclusion rules.

### Inclusion

A trade is included only when:

- `lifecycle_state = 'CLOSED'`

`CANCELLED_UNFILLED` is excluded from stats because it has no realized position result.

### Shared expressions

```text
net_pnl_expr = cumulative_gross_pnl - cumulative_fees - cumulative_funding
closed_ts_expr = COALESCE(closed_at, updated_at)
```

These must be the only semantic basis for:

- period windows
- win/loss/breakeven
- best/worst
- account totals
- trader totals
- any field labeled `Net`

### Outcome classification

```text
win        net_pnl > 0
loss       net_pnl < 0
breakeven  net_pnl = 0
```

### Win rate

```text
win_pct = wins / (wins + losses) * 100
```

Breakeven trades are excluded from the denominator. If `wins + losses = 0`, render `—`.

### Time windows

- `Today`: UTC date of `closed_ts_expr` equals current UTC date
- `Last 7d`: `closed_ts_expr >= now - 7 days`
- `Last 30d`: `closed_ts_expr >= now - 30 days`
- `All time`: all closed trades in effective scope

## Stats Data Contract

`StatsView` should be extended to carry explicit rendering data instead of overloading generic dashboard metadata.

Recommended shape additions:

- `closed_trade_count`
- `account_count`
- `trader_count`
- `best_trade` with `trade_chain_id`, `symbol`, `account_id`, `trader_id`, `net_pnl`
- `worst_trade` with the same fields
- `breakdown_accounts`, each containing:
  - account aggregate fields
  - ordered list of trader aggregates

`StatsRow` should also include enough raw counters for testability and future rendering:

- `wins`
- `losses`
- `breakevens`

## Breakdown Rules

### Global scope

Render:

- header with `Accounts`, `Traders`, `Closed trades`
- `Best` and `Worst` with account and trader context when outside title scope
- `By account` section with nested traders

Sorting rules:

- accounts by net PnL descending
- traders by net PnL descending inside each account

Consistency rules:

- each account total equals the sum of its traders for trade count, wins, losses, breakevens, and net PnL
- accounts with zero closed trades in filtered scope are omitted

### Account scope

Render:

- header with `Traders`, `Closed trades`
- `By trader` section only
- best/worst include trader but not account

### Trader scope

Render:

- header with `Closed trades`
- no redundant breakdown section
- best/worst omit account and trader context because already implied by title

## Closed View Correction

`Closed` currently labels `Net PnL` while rendering gross PnL.

This feature fixes that mismatch by making the payload explicitly pass net PnL whenever the label is `Net PnL`. Renaming the field to gross is not the recommended path because the spec wants semantic consistency around net metrics.

## Filter UX Design

### Panel behavior

The filter panel remains view-aware.

Common scope controls:

- `Account`
- `Trader`

Local view controls:

- `Active`: `Status`, `Side`
- `Closed`: `Side`, `Period`
- `Stats`: `Side`
- `PnL`: none in this change
- `Blocked`: optional `Side` only if already supported end-to-end; otherwise do not expose new unsupported controls

### Trader selector behavior

- with no selected account in global scope, trader selector shows an explanatory empty state
- with selected account, trader selector lists only distinct traders within that account and still constrained by base scope
- when account changes, trader is silently removed and the user returns to the previous dashboard view

### Clear behavior

Replace generic `Clear view` semantics with normalized clear actions:

- `Clear Account`
- `Clear Trader`
- `Clear Side`
- `Clear Status`
- `Clear Period`
- `Reset all`

Only actions relevant to the current view are shown.

## Implementation Boundaries

### `status_queries.py`

Add or change:

- shared `net_pnl_expr`
- shared `closed_ts_expr`
- corrected `get_stats()` semantics
- nested global stats breakdown data
- account-constrained trader selector query
- closed-trades net PnL payload support

### `dashboard_manager.py`

Add or change:

- normalized filter read/write helpers
- legacy filter normalization
- hierarchy validation and cleanup rules
- callback handlers for selective clears and view-safe filter application

The existing base scope persistence columns remain unchanged.

### `formatters/dashboard.py`

Add or change:

- compute effective scope from normalized `filters.scope`
- isolate local filters per view from `filters.views[view_name]`
- remove `period` from `PnL`
- pass Stats-specific metadata instead of generic `total/page/order`

### `formatters/templates/dashboard.py`

Add or change:

- dedicated Stats header renderer
- nested `By account -> trader` renderer
- scope-aware best/worst formatting
- corrected Closed net PnL line
- filters string built only from applicable filters

## Testing Strategy

### Query-level tests

Cover:

- stats windows use closure time, not creation time
- win/loss/breakeven classification uses net PnL
- win rate excludes breakeven trades
- best/worst use net PnL ordering
- global breakdown is account -> trader and internally consistent
- trader selector is constrained by account
- closed view returns true net PnL

### Manager-level tests

Cover:

- legacy flat filters normalize correctly
- trader without account is rejected or cleared
- changing account clears trader
- unsupported view filters are ignored
- `Reset all` clears all branches
- selective clears affect only intended keys

### Formatter/template tests

Cover:

- Stats header per effective scope
- no `Page` in Stats header
- correct filter string per view
- global breakdown visible only in global stats
- account breakdown visible only in account stats
- no breakdown in trader stats
- best/worst context adapts to scope

## Acceptance Summary

The implementation is complete when:

- Stats is closure-based and net-based everywhere
- Closed no longer labels gross as net
- global stats show account and trader provenance clearly
- scope and local filters are isolated and normalized
- trader selection is hierarchy-safe
- dashboard scope can be narrowed but never expanded
- legacy filter rows keep working without a DB migration

## Recommended Execution Order

1. Fix stats semantics in `status_queries.py`
2. Fix Closed net PnL payload semantics
3. Extend Stats payload contract and nested breakdown
4. Introduce normalized filter model with lazy compatibility
5. Update filter panels and dashboard templates
6. Add query, manager, and formatter tests
