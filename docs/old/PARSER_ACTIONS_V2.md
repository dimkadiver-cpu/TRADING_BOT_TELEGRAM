# Parser Actions V2

This document describes the canonical operational contract used by the parser and downstream update planning.

## Canonical chain

`message_type` -> `intents` -> `entities` -> `actions_structured`

Legacy `actions` are no longer the source of truth. They may still be emitted as a derived compatibility view, but new code should consume `actions_structured`. The reporting layer may expose them as `legacy_actions` for explicit debug-only compatibility.

## Canonical action types

The shared builder currently emits these canonical `action_type` values:

- `CREATE_SIGNAL`
- `MOVE_STOP`
- `CANCEL_PENDING`
- `CLOSE_POSITION`
- `MARK_TP_HIT`
- `MARK_STOP_HIT`
- `MARK_FILLED`
- `INVALIDATE_SETUP`
- `ATTACH_RESULT`
- `ADD_ENTRY`
- `UPDATE_PENDING_ENTRY`
- `REMOVE_PENDING_ENTRY`
- `UPDATE_TAKE_PROFITS`
- `REENTER_POSITION`
- `REVERSE_SIGNAL`
- `RISK_NOTE`
- `ACTIVATION`

## Core policies

- `U_MOVE_STOP_TO_BE` and `U_MOVE_STOP` both map to `MOVE_STOP`.
- `U_CLOSE_FULL` and `U_CLOSE_PARTIAL` both map to `CLOSE_POSITION`; `close_scope` distinguishes full vs partial.
- `U_CANCEL_PENDING_ORDERS` maps to `CANCEL_PENDING`. Canonical `cancel_scope` values are `TARGETED`, `ALL_PENDING_ENTRIES`, `ALL_LONG`, `ALL_SHORT`.
- `close_scope` describes how much is closed; `target_scope` / `applies_to` describes what the action applies to. Do not overload them with a single `ALL_ALL`-style value.
- `U_TP_HIT` maps to `MARK_TP_HIT`.
- `U_REPORT_FINAL_RESULT` maps to `ATTACH_RESULT`.

## Structured fields

`actions_structured` items should remain human-readable and explicit. Typical fields include:

- `action_type`
- `intent`
- `primary_intent`
- `message_type`
- `linking`
- `entry_plan`
- `risk_plan`
- `results_v2`
- `diagnostics`
- `target_refs`
- `target_refs_count`
- `target_scope`
- `applies_to`
- `symbol`
- `side`
- `new_stop_level`
- `new_stop_price`
- `close_scope`
- `close_fraction`
- `cancel_scope`
- `hit_target`
- `result_mode`
- `reported_results`
- `entries`
- `take_profits`

## Downstream consumers

- `src/parser/pipeline.py` builds canonical V2 `actions_structured`.
- `src/execution/update_planner.py` now reads `actions_structured` first and derives legacy actions only for compatibility.
- `src/execution/update_applier.py` consumes canonical action types.
- `parser_test/reporting/flatteners.py` exports `actions_structured` in CSV summaries.
