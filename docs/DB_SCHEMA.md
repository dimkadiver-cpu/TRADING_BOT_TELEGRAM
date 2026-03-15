# DB SCHEMA

## Goal
The database must preserve:
- the original Telegram message
- what the parser understood
- the operational trade lifecycle
- update linkage decisions
- resolution decisions

Implementation status:
- `raw_messages` and `parse_results` below are implemented in current migrations
- `signals`, `events`, `warnings`, and `trades` also exist in current migrations as legacy/H1 tables
- `update_matches`, `trade_state_events`, and `resolution_logs` are design targets and are not created by current migrations yet

---

## Core tables

### `raw_messages`
Stores the original Telegram message.

Minimum fields:
- internal id
- source chat id
- telegram message id
- reply to message id
- raw text
- message timestamp
- ingestion timestamp
- source type
- acquisition status

Recommended:
- source chat username/title snapshot if available

---

### `parse_results`
Stores what the parser understood from one raw message.

Minimum fields:
- internal id
- raw message reference
- message type
- parse status
- completeness
- executability flag

Relevant fields for this project:
- `eligibility_status`
- `eligibility_reason`
- `declared_trader_tag`
- `resolved_trader_id`
- `trader_resolution_method`
- symbol
- direction
- entry raw
- stop raw
- target raw list
- leverage hint
- risk hint
- risky flag
- warning text
- parse result normalized json (`parse_result_normalized_json`)

#### Legacy + semantic contract in `parse_result_normalized_json`
The normalized payload is additive and backward-compatible.

Legacy/event-like envelope fields remain available:
- `event_type`
- `instrument`
- `side`
- `status`
- `entries` / `stop_loss` / `take_profits`

Semantic parser contract fields are now included for regex/llm/hybrid alignment:
- `parser_used` (`regex` | `llm` | null)
- `parser_mode` (`regex_only` | `llm_only` | `hybrid_auto` | null)
- `message_type` (`NEW_SIGNAL` | `UPDATE` | `INFO_ONLY` | `SETUP_INCOMPLETE` | `UNCLASSIFIED` | null)
- `message_subtype`
- `symbol`
- `direction` (`LONG` | `SHORT` | null)
- `entry_main`, `entry_mode`, `average_entry`
- `stop_loss_price`
- `take_profit_prices`
- `actions`
- `target_refs`
- `reported_results`
- `notes`
- `raw_entities`
- `validation_warnings`

For updates:
- `linkage_method`
- `linkage_status`
- `linkage_target_raw_message_id`
- `linkage_target_trade_id`

Important:
`resolved_trader_id` must represent the effective trader after trader resolution.
It must not be assumed from `source_chat_id` alone in a multi-trader source.

---

### `trades`
Stores the operational trade object.

Current status:
- a `trades` table exists in `001_init.sql`
- its actual columns differ from the target shape described below
- current runtime does not populate or manage this table yet

Minimum fields:
- internal trade id
- origin raw message id
- origin parse result id
- resolved trader id
- symbol
- direction
- entry raw
- stop raw
- target raw list
- entry final
- stop final
- target final list
- leverage final
- risk final
- TP allocation final
- current state
- last state change timestamp
- active flag
- exchange order id if available

---

### `update_matches`
Stores the linkage decision for update messages.

Current status:
- not implemented in current migrations
- documented here as target schema for future lifecycle/linking work

Minimum fields:
- internal id
- update raw message id
- update parse result id
- candidate trade id
- linkage method
- linkage status
- linkage reason
- state compatibility flag
- auto-applied flag
- not-applied reason

Important:
A received update is not the same as an applied update.

---

### `trade_state_events`
Stores state transition history.

Current status:
- not implemented in current migrations
- documented here as target schema for future lifecycle work

Minimum fields:
- internal id
- trade id
- previous state
- new state
- timestamp
- cause
- origin
- optional note

---

### `resolution_logs`
Strongly recommended.

Current status:
- not implemented in current migrations
- documented here as target schema for future planner/risk audit work

Stores how final values were chosen.

Examples:
- leverage hint ignored due to global cap
- trader policy used
- entry normalized by Number Theory
- TP allocation chosen from trader defaults

Minimum fields:
- internal id
- trade id
- parameter name
- message value
- trader value
- global value
- final value
- decision reason
- timestamp

---

## Important design rule for this project

### Source channel is not the effective trader
Because one Telegram source may publish multiple traders, the database must distinguish:

- `source_chat_id`
- `declared_trader_tag`
- `resolved_trader_id`

This distinction is mandatory for correct parsing and update linkage.

---

## Data flow

### New signal path
`raw_messages`
-> `parse_results`
-> optional future `trades`
-> optional future `trade_state_events`

### Update path
`raw_messages`
-> `parse_results`
-> optional future `update_matches`
-> optional future trade update
-> optional future `trade_state_events`
