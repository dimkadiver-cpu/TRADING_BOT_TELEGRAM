# TASKS

## Current priority
Prepare Phase 4 parser minimum around the real project behavior:
- one allowed source may publish multiple traders
- some updates do not include a trader tag
- some updates are only one short word in reply to a prior signal
- some admin messages may mention trader tags but must not be treated as signals

---

## Phase 4 minimum implementation tasks

### 4.1 Eligibility filter
- define excluded tags and patterns
- support admin/stats/service exclusions
- keep excluded messages saved as raw, but not operational

### 4.2 Trader tag extraction
- extract tags like `[trader#A]`, `[trader#B]`, `[trader#3]`
- normalize them through a trader tag map
- store declared trader tag

### 4.3 Trader resolution
- support `DIRECT_TAG`
- support `REPLY_INHERIT`
- support `SOURCE_DEFAULT` only if safe
- support `UNRESOLVED`

### 4.4 Minimal intent classification
- classify:
  - `NEW_SIGNAL`
  - `SETUP_INCOMPLETE`
  - `UPDATE`
  - `INFO_ONLY`
  - `UNCLASSIFIED`

### 4.5 Minimal field extraction
- symbol
- direction
- entry
- stop
- target list
- leverage hint
- risk hint
- risky flag

### 4.6 Minimal linkage support
- support direct reply linkage first
- prepare support for Telegram message links
- do not auto-apply short updates without strong linkage

### 4.7 Parse result persistence
- store:
  - eligibility fields
  - trader resolution fields
  - linkage fields
  - extracted raw fields
  - executability flags

---

## Explicit examples to support

### Example A. Full signal
`[trader#A] BUY BTC ...`

Expected:
- eligible
- trader resolved by direct tag
- classified as `NEW_SIGNAL` if complete

### Example B. Reply cancellation without tag
Parent:
`[trader#A] BUY BTC ...`

Reply:
`cancel`

Expected:
- eligible
- trader resolved by reply inheritance
- linkage resolved by reply
- classified as update
- not treated as unknown trader

### Example C. Admin message with trader mention
`#admin trader#A weekly stats`

Expected:
- raw saved
- excluded from operational flow

---

## Not in scope for this parser minimum
- exchange
- planner
- final risk policy resolver
- full update subtyping beyond minimum useful classification
- weak context-only auto-linking
