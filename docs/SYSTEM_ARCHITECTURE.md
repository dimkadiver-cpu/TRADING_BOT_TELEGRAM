# SYSTEM ARCHITECTURE

## Goal
Provide a clear high-level architecture aligned with current implementation:
- Telegram source ingestion
- multi-trader support
- cautious parsing and linkage
- backward-compatible parser persistence
- normalized parser output for future modules

---

## High-level pipeline
`Telegram Message`
-> `Raw Ingestion`
-> `Eligibility Filter`
-> `Trader Resolution`
-> `Intent Classification`
-> `Entity Extraction`
-> `Linkage Resolution`
-> `Parse Result Persistence (Legacy + Normalized)`
-> `Policy Resolver`
-> `Level Normalizer`
-> `Trade Planner`
-> `State Machine`
-> `Exchange Adapter`
-> `Monitoring / Reconciliation`

---

## 1. Telegram ingestion
Output: `raw_messages`.

Important:
- source chat identifies message origin
- source chat does not always identify effective trader

---

## 2. Eligibility filter
Purpose: decide operational promotion eligibility.

Messages may still be stored even if not operationally promotable.

---

## 3. Trader resolution
Purpose: resolve effective trader from content/reply context.

Outputs:
- `declared_trader_tag`
- `resolved_trader_id`
- `trader_resolution_method`

---

## 4. Intent classification and extraction
Legacy classification output (`message_type`):
- `NEW_SIGNAL`
- `SETUP_INCOMPLETE`
- `UPDATE`
- `INFO_ONLY`
- `UNCLASSIFIED`

Extraction output includes raw levels and hints.

---

## 5. Linkage resolution
Strong linkage methods:
- direct reply
- message link
- explicit message id

Short updates must not auto-apply with weak context only.

---

## 6. Parse result persistence
`parse_results` now stores:
- legacy columns (existing behavior)
- normalized JSON payload in `parse_result_normalized_json`

Normalized schema aligns regex parser and future LLM parser contract.

---

## 7. Normalized parser contract
Normalized payload minimum fields:
- `event_type`, `trader_id`, `source_chat_id`, `source_message_id`
- `raw_text`, `parser_mode`, `confidence`
- `instrument`, `side`, `market_type`
- `entries`, `stop_loss`, `take_profits`
- `root_ref`, `status`, `validation_warnings`

Canonical event types:
- `NEW_SIGNAL`, `UPDATE`, `CANCEL_PENDING`, `MOVE_STOP`
- `TAKE_PROFIT`, `CLOSE_POSITION`, `INFO_ONLY`
- `SETUP_INCOMPLETE`, `INVALID`

---

## 8. Validator behavior
Normalized validator is non-blocking:
- emits warnings
- does not block persistence
- keeps audit trail in parse warnings/normalized warnings

---

## 9. Architectural rules
1. Raw messages are always saved first.
2. Eligibility is checked before operational promotion.
3. Source chat is not effective trader identity.
4. Updates require strong linkage before auto-apply.
5. Legacy parse result contract remains valid while normalized schema is adopted.
