# DB SCHEMA

## Goal
The database must preserve:
- the original Telegram message
- what the parser understood
- the operational trade lifecycle
- update linkage decisions
- resolution decisions

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
-> `trades`
-> `trade_state_events`

### Update path
`raw_messages`
-> `parse_results`
-> `update_matches`
-> optional trade update
-> `trade_state_events`
