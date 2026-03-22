# TASKS

## Current priority
Validate and stabilize Phase 4 parser output with normalized schema in production-like replay.

---

## Phase 4 completed baseline
Implemented:
- eligibility checks and persistence
- trader resolution with reply inherit support
- minimal classification and extraction
- parse result persistence
- additive normalized payload (`parse_result_normalized_json`)
- non-blocking normalized validator
- replay script output for normalized samples

---

## Phase 4 remaining tasks

### 4.1 Validation on real data
- replay larger historical windows
- measure distribution by `message_type` and normalized `event_type`
- collect ambiguous cases per trader

### 4.2 Update subtype quality
- improve update subtype mapping (`MOVE_STOP`, `TAKE_PROFIT`, etc.)
- add multilingual keyword coverage where needed

### 4.3 Root linkage quality
- improve `root_ref` population consistency
- flag weak/implicit linkage paths more clearly

### 4.4 Test coverage
- add regression fixtures for:
  - `CANCEL_PENDING`
  - `TAKE_PROFIT`
  - `CLOSE_POSITION`
  - `SETUP_INCOMPLETE`
  - `INVALID`

### 4.5 Documentation consistency
- keep parser docs aligned with real statuses and field names
- keep backward-compatibility notes explicit

---

## Out of scope (unchanged)
- exchange execution
- planner internals
- risk policy final resolver
- broad architecture refactor
