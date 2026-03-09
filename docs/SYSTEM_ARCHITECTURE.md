# SYSTEM ARCHITECTURE

## Goal
Provide a clear high-level architecture aligned with the current project reality:
- Telegram source ingestion
- multi-trader source support
- cautious parsing
- strong-link update handling
- later policy, planning, execution, and monitoring

---

## High-level pipeline

`Telegram Message`
-> `Raw Ingestion`
-> `Eligibility Filter`
-> `Trader Resolution`
-> `Intent Classification`
-> `Entity Extraction`
-> `Linkage Resolution`
-> `Parse Result Persistence`
-> `Policy Resolver`
-> `Level Normalizer`
-> `Trade Planner`
-> `State Machine`
-> `Exchange Adapter`
-> `Monitoring / Reconciliation`

---

## 1. Telegram ingestion

Purpose:
- receive Telegram messages
- filter by allowed sources
- persist raw messages
- avoid trivial duplicates

Output:
- `raw_messages`

Important:
The source chat identifies where the message was published, but not always which trader it belongs to.

---

## 2. Eligibility filter

Purpose:
decide whether a message may enter the operational parsing flow.

Examples of excluded content:
- admin messages
- statistics
- reports
- service announcements
- other excluded patterns

Important:
excluded messages are still saved as raw data, but must not be promoted to operational parsing.

---

## 3. Trader resolution

Purpose:
resolve the effective trader represented by the message.

In this project a single source may publish multiple traders.
Therefore trader resolution must use:
- direct trader tag in message content
- reply inheritance from parent message
- source default only when truly safe

Key logical outputs:
- `declared_trader_tag`
- `resolved_trader_id`
- `trader_resolution_method`

---

## 4. Intent classification

Purpose:
classify the message into a minimum internal category.

Minimum categories:
- `NEW_SIGNAL`
- `SETUP_INCOMPLETE`
- `UPDATE`
- `INFO_ONLY`
- `UNCLASSIFIED`

---

## 5. Entity extraction

Purpose:
extract operational raw fields when present.

Typical fields:
- symbol
- direction
- entry
- stop
- target list
- leverage hint
- risk hint
- risky flag

Important:
extraction reads message content, but does not yet decide final operational values.

---

## 6. Linkage resolution

Purpose:
resolve which previous message or trade an update refers to.

Strong methods:
- `REPLY`
- `MESSAGE_LINK`
- `EXPLICIT_MESSAGE_ID`

Weak method:
- cautious contextual fallback

Critical rule:
short updates such as `cancel`, `close`, `breakeven`, `move sl`
must not become operational by weak context alone.

---

## 7. Parse result persistence

Purpose:
persist what the parser understood separately from raw data.

Typical stored elements:
- eligibility outcome
- trader resolution outcome
- message type
- extracted fields
- linkage info
- executability flags
- warnings

Output:
- `parse_results`

---

## 8. Policy resolver

Purpose:
resolve final operational parameters using:
- message hints
- trader defaults
- global defaults

Typical decisions:
- leverage
- risk
- TP allocation

---

## 9. Level normalizer

Purpose:
transform raw levels into final levels using:
- rounding
- precision rules
- Number Theory
- tick-size adaptation

Important:
raw and final values must remain distinguishable.

---

## 10. Trade planner

Purpose:
build the operational trade object from parse result, policy, and normalized levels.

Output:
- `trades`
- initial operational state

---

## 11. State machine

Purpose:
manage the lifecycle of the trade in a controlled way.

Core states include:
- `PARSED`
- `INCOMPLETE`
- `READY`
- `PLANNED`
- `PENDING_ENTRY`
- `OPEN`
- `PARTIALLY_CLOSED`
- `CLOSED`
- `CANCELED`
- `REJECTED`

Important:
state compatibility is mandatory for update application.

---

## 12. Exchange adapter

Purpose:
translate internal trade actions into real exchange operations.

Not in current minimum scope.

---

## 13. Monitoring and reconciliation

Purpose:
compare internal state with external reality and detect inconsistencies.

Not in current minimum scope.

---

## Architectural rules

### Rule 1
Raw messages are always saved first.

### Rule 2
Eligibility is checked before operational parsing.

### Rule 3
Source chat is not always the effective trader.

### Rule 4
Updates require strong linkage before operational promotion.

### Rule 5
Final operational values are resolved after parsing, not during raw extraction.
