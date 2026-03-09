# MASTER PLAN

## System goal
Build a robust Telegram-to-trade pipeline that:
- saves all source messages
- understands which messages are operational
- resolves which trader each message represents
- links updates to the correct signal/trade
- applies operational logic only when confidence is strong

---

## Important project reality
A single Telegram source may contain messages from multiple traders.

Therefore:
- the source chat identifies the publishing container
- the trader must often be resolved from the message content or inherited from a replied parent message

This affects:
- parser design
- DB schema
- update linkage
- validation rules

---

## Main rules

### Rule 1. Save raw first
Every message from the allowed source must be saved as a raw message.

### Rule 2. Eligibility before operations
Before any operational parsing, the system must decide whether the message is operationally eligible.

### Rule 3. Trader resolution is separate from source resolution
A source chat is not automatically the trader.
Trader resolution uses:
- direct tag
- reply inheritance
- source default only when truly safe

### Rule 4. Updates need strong linkage
Short updates may be auto-applied only if linkage is strong:
- direct reply
- Telegram message link
- explicit message reference

### Rule 5. New signals require complete setup
A new signal is operational only if it has:
- symbol
- direction
- entry
- stop
- at least one target

Otherwise it is saved as incomplete or informational.

---

## Parser backbone

### Eligibility filter
Exclude admin/stats/service content from the operational flow.

### Trader resolution
Resolve:
- `declared_trader_tag`
- `resolved_trader_id`
- `trader_resolution_method`

### Intent classification
Classify:
- `NEW_SIGNAL`
- `SETUP_INCOMPLETE`
- `UPDATE`
- `INFO_ONLY`
- `UNCLASSIFIED`

### Linkage resolution
Resolve update target using:
- `REPLY`
- `MESSAGE_LINK`
- `EXPLICIT_MESSAGE_ID`
- cautious context fallback

### Validation
Promote to operational flow only if:
- eligible
- trader resolved
- data complete for new signals
- confirmed linkage for updates

---

## Immediate next implementation focus
Phase 4 minimum parser must include:
- eligibility filter
- trader tag extraction
- trader resolution with reply inheritance
- minimal intent classification
- minimal field extraction
- minimal linkage support for replies
- parse result persistence
