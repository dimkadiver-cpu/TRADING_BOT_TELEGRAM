# TRADE STATE MACHINE

## Goal
Define the operational lifecycle of a trade in a way that is simple, explicit, and compatible with parser, linkage, DB, and future execution logic.

This project distinguishes:
- message parsing state
- trade operational state

The state machine below refers to the **trade lifecycle**, not to the raw Telegram message itself.

---

## Core states

### `PARSED`
The source message has been parsed enough to produce an internal parse result.

This is a technical entry state, not an execution state.
It may apply to:
- new signals
- incomplete setups
- updates
- informational messages

---

### `INCOMPLETE`
A setup exists, but it does not contain the minimum required data to become an operational new signal.

Typical causes:
- missing stop
- missing target
- missing entry
- incomplete operational structure

Rule:
- saved
- never auto-executed

---

### `READY`
A new signal is complete enough to become an operational candidate.

Minimum requirements:
- trader resolved
- symbol
- direction
- entry
- stop
- at least one target
- eligibility positive

Rule:
- not executed yet
- ready for planning

---

### `PLANNED`
The system has resolved the operational plan.

Typical contents resolved:
- final entry
- final stop
- final targets
- final leverage
- final risk
- final TP allocation

Rule:
- planning completed
- execution may follow

---

### `PENDING_ENTRY`
The order has been prepared/submitted or the setup is live waiting for entry.

Examples:
- limit order active
- setup waiting for trigger
- order not filled yet

Allowed updates typically include:
- `ENTRY_UPDATE`
- `STOP_UPDATE`
- `TARGET_UPDATE`
- `CANCEL_ORDER`

Not valid here:
- `PARTIAL_CLOSE`
- `FULL_CLOSE`
as position management actions, because the position is not truly open yet.

---

### `OPEN`
A real position exists.

Allowed updates typically include:
- `STOP_UPDATE`
- `TARGET_UPDATE`
- `PARTIAL_CLOSE`
- `FULL_CLOSE`

Not valid:
- `CANCEL_ORDER` as if the trade were still pending

---

### `PARTIALLY_CLOSED`
The position is still open, but part of it has already been closed.

This state matters because:
- remaining size changed
- some targets may already be consumed
- future management rules may differ

Allowed updates:
- more `PARTIAL_CLOSE`
- `STOP_UPDATE`
- `TARGET_UPDATE`
- `FULL_CLOSE`

---

### `CLOSED`
The trade was opened and is now fully closed.

Rule:
- no further operational update may be applied

---

### `CANCELED`
The setup/order was canceled before a real open position existed.

Important difference from `CLOSED`:
- `CANCELED`: no actual position lifecycle completed
- `CLOSED`: a position existed and was closed

---

### `REJECTED`
The signal was parsed and understood, but rejected by system rules.

Examples:
- not allowed by policy
- invalid for execution
- risk/leverage constraints
- inconsistent operational context

This is different from `INCOMPLETE`.

---

## Main flows

### Complete new signal
`PARSED`
-> `READY`
-> `PLANNED`
-> `PENDING_ENTRY`
-> `OPEN`
-> `PARTIALLY_CLOSED` (optional)
-> `CLOSED`

---

### Canceled before entry
`PARSED`
-> `READY`
-> `PLANNED`
-> `PENDING_ENTRY`
-> `CANCELED`

---

### Incomplete setup
`PARSED`
-> `INCOMPLETE`

---

### Rejected signal
`PARSED`
-> `READY`
-> `REJECTED`

---

## Update compatibility by state

### `ENTRY_UPDATE`
Usually compatible with:
- `READY`
- `PLANNED`
- `PENDING_ENTRY`

Usually not operational on:
- `CLOSED`
- `CANCELED`

---

### `STOP_UPDATE`
Usually compatible with:
- `PENDING_ENTRY`
- `OPEN`
- `PARTIALLY_CLOSED`

Not compatible with:
- `CLOSED`
- `CANCELED`

---

### `TARGET_UPDATE`
Usually compatible with:
- `PENDING_ENTRY`
- `OPEN`
- `PARTIALLY_CLOSED`

---

### `PARTIAL_CLOSE`
Compatible only with:
- `OPEN`
- `PARTIALLY_CLOSED`

Never with:
- `PENDING_ENTRY`

---

### `FULL_CLOSE`
Compatible only with:
- `OPEN`
- `PARTIALLY_CLOSED`

---

### `CANCEL_ORDER`
Compatible only with:
- `READY`
- `PLANNED`
- `PENDING_ENTRY`

Never with:
- `OPEN`
- `PARTIALLY_CLOSED`
- `CLOSED`

---

## Critical rule
Even if an update has:
- clear trader resolution
- clear intent
- confirmed linkage

it must still be rejected if the current trade state is incompatible with that update.

Example:
- update says `partial close`
- linkage is confirmed
- but trade is still `PENDING_ENTRY`

Result:
- save the update
- do not apply it

---

## Event history
The current trade state alone is not enough.

The system should also persist transition history through `trade_state_events`, including:
- previous state
- new state
- timestamp
- origin
- cause
- note if useful
