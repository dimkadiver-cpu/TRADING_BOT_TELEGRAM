# Design: Dashboard `Not executed` and `Operational issues`

Date: 2026-06-24
Status: Approved in chat, pending user review of written spec
Scope: `runtime_v2` control plane dashboard classification for failed or problematic signal execution outcomes

## 1. Problem

The current dashboard tab `Blocked` mixes multiple categories that do not belong to the same business outcome:

- signals rejected before a valid entry ever exists;
- chains whose entry submission failed before any exchange acceptance or fill;
- post-entry operational failures on already active trades.

This makes the tab semantically noisy and causes the control plane to answer the wrong question.

The desired behavior is to split the current meaning into two views:

- `🚫 Not executed`
- `⚠️ Operational issues`

## 2. Goals

### Done means

The dashboard must classify non-successful execution paths into two explicit views with no ambiguity for the primary signal outcome.

### Acceptance contract

1. `Not executed` shows only signals/chains that never produced any exchange-accepted or filled entry.
2. `Operational issues` shows only post-entry problems or operational failures on trades that did have an accepted or filled entry.
3. `Not executed` must never duplicate records already represented by `Active` or `Closed`.
4. Legacy `blocked` and `bloccati` navigation must resolve to `not_executed`.
5. Query logic must be centralized outside dashboard formatting so classification is owned by the query/service layer.

### Primary signal

Given a signal, the dashboard answers one of these questions correctly:

- it never became a real executable entry: `Not executed`
- it became active or closed: `Active` or `Closed`
- it became active or closed but has a follow-up operational problem: `Operational issues`

### Secondary signals

- control-plane query tests;
- dashboard formatter tests;
- dashboard manager callback/filter tests.

## 3. Non-goals

- No new persistent outcome table in V1.
- No backfill migration for historical outcome projection.
- No change to trading execution semantics themselves.
- No redesign of unrelated dashboard tabs.

## 4. Current system grounding

Today the control plane uses different data shapes depending on the tab:

- `Active` reads mainly from `ops_trade_chains.lifecycle_state`.
- `Closed` reads mainly from `ops_trade_chains` terminal states with small event enrichment.
- `PnL` and `Stats` are aggregate views over stable projected state.
- `Trade detail` intentionally composes multiple sources because it shows history, not classification.

`Blocked` is the exception. It currently combines:

- `ops_trade_chains.lifecycle_state='REVIEW_REQUIRED'`
- any `ops_execution_commands.status='FAILED'`

This is the root cause of the semantic drift.

## 5. Architectural decision

### Decision

Implement V1 without a new outcome table, but create explicit query models and query paths for:

- `get_not_executed_trades(...)`
- `get_operational_issues(...)`

Classification must live in the query/service layer, not in dashboard formatters.

### Why this direction

- It fixes the current product problem without introducing a new persistence contract immediately.
- It keeps the classification logic centralized instead of scattering business rules across formatters.
- It prepares a future V2 outcome table by using explicit row models and view names from the start.

### Rejected alternative

Creating `ops_signal_execution_outcomes` immediately would produce a cleaner long-term architecture, but for this iteration it increases scope and rollout cost before the desired semantics have been validated against existing live data.

## 6. View definitions

### 6.1 `🚫 Not executed`

This view answers:

> Which signals never produced any exchange-accepted or filled entry, and why?

Included categories:

- signal-level rejection before chain execution;
- pre-entry manual/policy/risk/validation hold that prevented execution;
- final entry submission failure with zero accepted entry evidence and zero fill.

Excluded categories:

- waiting entry with exchange-acknowledged open order;
- partial fill;
- open position;
- closed position;
- any post-entry failure such as stop move, TP rebuild, close failure, sync mismatch.

### 6.2 `⚠️ Operational issues`

This view answers:

> Which trades did become executable or active, but now have operational problems that need attention?

Included categories:

- command failures after entry acceptance/fill;
- `REVIEW_REQUIRED` after entry acceptance/fill;
- protective-order management failures;
- breakeven/update/close/sync problems on active or previously active trades.

This view is not a primary outcome classification. A trade may appear in:

- `Active` and `Operational issues`
- `Closed` and `Operational issues` only if the issue is still relevant in the designed query window

V1 should optimize for active operational problems on currently relevant chains.

## 7. Classification rules

## 7.1 Required evidence concepts

The query layer must derive these concepts from existing tables:

- `has_entry_ack`: at least one entry command has evidence of exchange acceptance or open-order acknowledgement
- `has_entry_fill`: chain has positive `filled_entry_qty`
- `has_open_position`: chain has positive `open_position_qty`
- `entry_failure_final`: entry command failed with no later recovery to acknowledged/filled state
- `post_entry_context`: there is evidence that the chain crossed the line into an accepted or filled entry lifecycle

### 7.2 Entry command family

V1 must treat these command types as entry submission:

- `PLACE_ENTRY`
- `PLACE_ENTRY_WITH_ATTACHED_TPSL`

If additional entry aliases exist in the runtime, they must be normalized into the same category in the query layer.

### 7.3 `Not executed` inclusion rules

A record belongs to `Not executed` when one of the following is true.

#### A. Signal-level rejection

- lifecycle event is `SIGNAL_REJECTED`; and
- the signal has no later evidence of accepted/filled entry.

This path may have no `trade_chain_id`.

#### B. Pre-entry review hold

- lifecycle event or equivalent business hold is `REVIEW_REQUIRED`; and
- no entry command was ever acknowledged; and
- `filled_entry_qty = 0`; and
- `open_position_qty = 0`; and
- the reason belongs to a pre-entry phase such as validation, policy, risk, or manual review.

#### C. Entry submission failure

- entry command is in the entry command family; and
- failure is terminal for the current execution path; and
- there is no acknowledged entry evidence; and
- `filled_entry_qty = 0`; and
- `open_position_qty = 0`.

### 7.4 `Not executed` exclusion rules

Exclude from `Not executed` if any of the following is true:

- any entry command reached acknowledged/open-order evidence;
- `filled_entry_qty > 0`;
- `open_position_qty > 0`;
- lifecycle state is effectively active because entry was accepted by the exchange;
- the chain later recovered from an earlier entry failure.

### 7.5 `Operational issues` inclusion rules

A record belongs to `Operational issues` when all of the following are true:

- the chain has `post_entry_context`;
- the problem is not an initial signal rejection or pre-entry execution failure;
- the failing command or review event belongs to an operational phase after entry acceptance/fill.

Typical command families:

- `MOVE_STOP`
- `MOVE_STOP_TO_BREAKEVEN`
- `REBUILD_PARTIAL_TPS`
- `CLOSE_PARTIAL`
- `CLOSE_FULL`
- `SYNC_PROTECTIVE_ORDERS`
- `CANCEL_PENDING_ENTRY` only when the chain had prior acknowledged/fill evidence and the issue is operational rather than a never-started entry

### 7.6 Anti-duplication rules

- A signal/chain cannot appear in both `Not executed` and `Active`.
- A signal/chain cannot appear in both `Not executed` and `Closed`.
- A chain may appear in `Operational issues` and `Active`.
- A chain may appear in `Operational issues` and `Closed` only if the issue remains intentionally visible by the query contract.
- Each view shows one current row per signal-only reject or per chain, not a row per event.

## 8. Data model for V1 query results

### 8.1 `NotExecutedRow`

- `reference`
- `trade_chain_id`
- `signal_reference`
- `account_id`
- `trader_id`
- `symbol`
- `side`
- `outcome`
- `phase`
- `reason`
- `command_type`
- `occurred_at`
- `details_command`

Field semantics:

- `reference`: `#<trade_chain_id>` when a chain exists, otherwise `#S-<signal/source_id>`
- `outcome`: `REJECTED` or `NOT_EXECUTED`
- `phase`: `Validation`, `Policy`, `Risk`, `Manual review`, or `Entry submission`
- `details_command`: `/trade_<id>` or `/signal_<id>`

### 8.2 `OperationalIssueRow`

- `trade_chain_id`
- `account_id`
- `trader_id`
- `symbol`
- `side`
- `issue_type`
- `phase`
- `reason`
- `command_type`
- `occurred_at`
- `details_command`

Field semantics:

- `issue_type`: `REVIEW_REQUIRED` or `COMMAND_FAILED`
- `phase`: normalized operational phase such as `Protection`, `Breakeven`, `Take profit`, `Close`, `Sync`, `Entry cancel`
- `details_command`: always `/trade_<id>`

### 8.3 Normalization defaults

- missing human-readable reason renders as `unavailable`
- entry command family is normalized as entry submission
- timestamps are always outcome timestamps, not generic chain `updated_at`

## 9. Query strategy

### 9.1 `Not executed`

The view is built as the union of:

- signal-level rejected/pre-entry review rows;
- chain-level final entry-submission failures.

For signal-only rows, the query layer must recover `account_id`, `trader_id`, `symbol`, and `side` when possible from stored context; otherwise render missing business fields safely.

For chain rows, the query layer must confirm absence of accepted or filled entry evidence before inclusion.

### 9.2 `Operational issues`

The view is built only from chain-level rows and only after proving `post_entry_context`.

The query must ignore generic failed commands that belong to the initial entry path and instead classify them into `Not executed` when they match the entry-failure rules.

### 9.3 Ordering

Both views order by:

- `occurred_at DESC`

### 9.4 Recovery handling

If an earlier entry failure was followed by later acknowledged or filled entry evidence, the chain must not remain in `Not executed`.

V1 resolves this through query-time suppression, not historical mutation.

## 10. UI and navigation

### Tabs

Dashboard tabs become:

- `⚡ Active`
- `✅ Closed`
- `🚫 Not executed`
- `⚠️ Operational issues`
- `📊 PnL`
- `📈 Stats`

### Navigation compatibility

Legacy callbacks and aliases:

- `blocked` -> `not_executed`
- `bloccati` -> `not_executed`

The old `Blocked` semantics are removed.

## 11. Filters

### 11.1 Shared scope behavior

- `Account` and `Trader` remain shared scope filters.
- `Trader` is selectable only after `Account`.
- changing `Account` resets `Trader`.
- `Reset all` clears all filters.

### 11.2 `Not executed` filters

- `Account`
- `Trader`
- `Outcome`: `All | Rejected | Entry not executed`
- `Phase`: `All | Validation | Policy | Risk | Manual review | Entry submission`
- `Side`: `All | LONG | SHORT`

### 11.3 `Operational issues` filters

- `Account`
- `Trader`
- `Issue type`: `All | Review required | Command failed`
- `Phase`: `All | Protection | Breakeven | Take profit | Close | Sync | Entry cancel`
- `Side`: `All | LONG | SHORT`

Non-shared filters are local to the view.

## 12. Rendering requirements

### 12.1 `Not executed`

- title reflects actual scope
- global scope shows account and trader
- use `At:` label, never `Blocked:`
- empty state text: `No non-executed signals.`
- signal-only reference renders as `#S-...`
- chain-backed reference renders as `#...`

### 12.2 `Operational issues`

- title reflects actual scope
- global scope shows account and trader
- empty state text: `No operational issues.`
- details action always opens the trade detail command

## 13. Affected layers

- `src/runtime_v2/control_plane/status_queries.py`
  add row/view types and classification queries
- `src/runtime_v2/control_plane/service.py`
  expose new read methods
- `src/runtime_v2/control_plane/formatters/dashboard.py`
  build payloads for new views
- `src/runtime_v2/control_plane/formatters/templates/dashboard.py`
  render new views and labels
- `src/runtime_v2/control_plane/dashboard_manager.py`
  tab names, callback routing, filter panels, static filter values
- tests for status queries, formatters, and dashboard manager

No runtime execution behavior changes are required for V1 beyond correctly interpreting existing persisted facts.

## 14. Testing plan

### Query cases

- `SIGNAL_REJECTED` without `trade_chain_id` is included in `Not executed`
- pre-entry `REVIEW_REQUIRED` is included in `Not executed`
- post-entry `REVIEW_REQUIRED` is excluded from `Not executed` and included in `Operational issues`
- failed `PLACE_ENTRY` with no fill is included in `Not executed`
- failed `PLACE_ENTRY_WITH_ATTACHED_TPSL` with no fill is included in `Not executed`
- acknowledged waiting entry is excluded from `Not executed`
- partial fill is excluded from `Not executed`
- recovered entry path after earlier failure is excluded from `Not executed`
- failed `MOVE_STOP` on open trade is included in `Operational issues`
- failed `SYNC_PROTECTIVE_ORDERS` on active trade is included in `Operational issues`
- two qualifying failures for same chain produce one current row per view
- sorting is `occurred_at DESC`
- account/trader/outcome/issue-type/phase/side filters work correctly

### Formatter cases

- signal-only references render as `#S-...`
- chain references render as `#...`
- global scope shows account and trader
- account-scoped view does not repeat account unnecessarily
- missing reason renders as `Reason: unavailable`
- `Not executed` uses `At:`
- correct empty-state strings for both views

### Dashboard manager cases

- tab switch supports `not_executed`
- legacy `blocked`/`bloccati` route to `not_executed`
- `Operational issues` tab is reachable and filterable

## 15. Risks and follow-up

### Risks in V1

- query-time classification may still be complex for historical edge cases
- signal-only context may be incomplete for some old rows if the necessary metadata was not persisted in an easily recoverable form
- distinguishing pre-entry vs post-entry `REVIEW_REQUIRED` may require careful evidence precedence

### Follow-up candidate

If V1 proves stable and useful, move the classification into a dedicated persisted projection such as `ops_signal_execution_outcomes`, so the dashboard becomes a simple reader of already-resolved business outcomes.

## 16. Open implementation notes

- Keep naming aligned with the existing control-plane conventions, but use `not_executed` as the canonical view key.
- Do not preserve the old `BlockedTradeRow` semantics under a new label.
- Prefer isolated helper functions for evidence derivation if the query body becomes hard to read, but avoid introducing a new abstraction layer unless it clearly simplifies the logic.
