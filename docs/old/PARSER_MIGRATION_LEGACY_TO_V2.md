# Legacy to V2 Migration

This file maps the legacy `ACT_*` compatibility vocabulary to canonical V2 `action_type` values.

## Policy summary

- Canonical source of truth: `actions_structured`
- Legacy `actions`: derived compatibility only
- `TP_HIT`: keep as a real V2 action (`MARK_TP_HIT`)
- Final result: keep as a real V2 action (`ATTACH_RESULT`)
- Cancel scope canonical domain: `TARGETED`, `ALL_PENDING_ENTRIES`, `ALL_LONG`, `ALL_SHORT`
- `close_scope` should stay `FULL` / `PARTIAL`; application scope belongs in `target_scope` / `applies_to`

## Mapping table

| Legacy action | V2 action_type | Source intents | Required fields | Status |
| --- | --- | --- | --- | --- |
| `ACT_CREATE_SIGNAL` | `CREATE_SIGNAL` | `NS_CREATE_SIGNAL` | `symbol`, `entries`, `stop_loss`, `take_profits` | KEEP TEMP |
| `ACT_MOVE_STOP_LOSS` | `MOVE_STOP` | `U_MOVE_STOP` | `new_stop_level` or `new_stop_price` | KEEP TEMP |
| `ACT_MOVE_STOP_LOSS_TO_BE` | `MOVE_STOP` | `U_MOVE_STOP_TO_BE` | `new_stop_level=ENTRY` or equivalent | KEEP TEMP |
| `ACT_CANCEL_ALL_PENDING_ENTRIES` | `CANCEL_PENDING` | `U_CANCEL_PENDING_ORDERS` | `cancel_scope` | KEEP TEMP |
| `ACT_CLOSE_PARTIAL` | `CLOSE_POSITION` | `U_CLOSE_PARTIAL` | `close_scope=PARTIAL`, optional `close_fraction` | KEEP TEMP |
| `ACT_CLOSE_FULL` | `CLOSE_POSITION` | `U_CLOSE_FULL` | `close_scope=FULL` | KEEP TEMP |
| `ACT_CLOSE_FULL_AND_MARK_CLOSED` | `CLOSE_POSITION` | `U_CLOSE_FULL` | `close_scope=FULL`, `close_status_passive` if relevant | DEPRECATE |
| `ACT_MARK_TP_HIT` | `MARK_TP_HIT` | `U_TP_HIT` | `hit_target`, optional `close_fraction`, `result_mode` | KEEP TEMP |
| `ACT_MARK_STOP_HIT` | `MARK_STOP_HIT` | `U_STOP_HIT` | `hit_target=STOP` | KEEP TEMP |
| `ACT_ATTACH_RESULT` | `ATTACH_RESULT` | `U_REPORT_FINAL_RESULT` | `reported_results`, `result_mode` | KEEP TEMP |
| `ACT_MARK_ORDER_FILLED` | `MARK_FILLED` | `U_MARK_FILLED`, `U_ACTIVATION` | `fill_state` | KEEP TEMP |
| `ACT_MARK_SIGNAL_INVALID` | `INVALIDATE_SETUP` | `U_INVALIDATE_SETUP` | `reason` | KEEP TEMP |
| `ACT_MARK_POSITION_CLOSED` | `MARK_POSITION_CLOSED` | `U_EXIT_BE` / close lifecycle updates | `close_scope=BREAKEVEN` or equivalent | DEPRECATE |
| `ACT_REMOVE_PENDING_ENTRY` | `REMOVE_PENDING_ENTRY` | `U_REMOVE_PENDING_ENTRY` | `target_entry_id` or `cancel_scope` | KEEP TEMP |
| `ACT_UPDATE_PENDING_ENTRY` | `UPDATE_PENDING_ENTRY` | `U_UPDATE_PENDING_ENTRY` | `target_entry_id`, `new_entry_price` | KEEP TEMP |
| `ACT_UPDATE_TAKE_PROFITS` | `UPDATE_TAKE_PROFITS` | `U_UPDATE_TAKE_PROFITS` | `take_profits`, `target_refs` | KEEP TEMP |
| `ACT_REENTER_POSITION` | `REENTER_POSITION` | `U_REENTER` | `entries`, `stop_loss`, `take_profits` | KEEP TEMP |
| `ACT_REVERSE_SIGNAL_OR_CREATE_OPPOSITE` | `REVERSE_SIGNAL` | `U_REVERSE_SIGNAL` | `symbol`, `side`, related target scope | KEEP TEMP |
| `ACT_ATTACH_RISK_NOTE` | `RISK_NOTE` | `U_RISK_NOTE` | `risk_text` | KEEP TEMP |
| `ACT_MARK_SIGNAL_ACTIVE` | `ACTIVATION` | `U_ACTIVATION` | `fill_state` / activation details | KEEP TEMP |

## Notes

- Downstream planners should read `actions_structured` first and may derive legacy `actions` only for compatibility.
- `parser_test` CSV reports now flatten V2 fields first and keep legacy actions only behind `--include-legacy-debug`.
- CSV cannot persist Excel freeze panes; if needed, freeze the header manually in Excel.
