# MASTER PLAN

## System goal
Build a robust Telegram-to-trade pipeline that:
- saves all source messages
- classifies operational vs non-operational content
- resolves the effective trader per message
- links updates to the correct signal/trade with strong evidence
- keeps an auditable parser output in both legacy and normalized form

---

## Important project reality
A single Telegram source may contain messages from multiple traders.

Therefore:
- source chat identifies the publishing container
- trader identity must be resolved from message content and reply inheritance
- update linkage must follow strict priority and confidence rules

---

## Main rules

### Rule 1. Save raw first
Every allowed message must be persisted into `raw_messages`.

### Rule 2. Eligibility before operations
Operational promotion requires eligibility checks first.

### Rule 3. Source is not trader identity
Trader resolution is independent from source mapping and uses:
- direct tag
- reply inherit
- source default only if safe

### Rule 4. Updates need strong linkage
Short updates may auto-apply only with strong references:
- direct reply
- Telegram message link
- explicit message reference

### Rule 5. New signals require complete setup
A `NEW_SIGNAL` is operational only with:
- symbol
- direction
- entry
- stop
- at least one target

---

## Parser backbone

### Legacy parse result (backward compatibility)
The existing `parse_results` columns remain supported (`message_type`, extracted raw fields, linkage, flags, warnings).

### Normalized parse result (new standard)
A normalized JSON payload is now persisted in `parse_results.parse_result_normalized_json`.

Minimum normalized fields:
- `event_type`
- `trader_id`
- `source_chat_id`
- `source_message_id`
- `raw_text`
- `parser_mode`
- `confidence`
- `instrument`
- `side`
- `market_type`
- `entries`
- `stop_loss`
- `take_profits`
- `root_ref`
- `status`

Canonical event types:
- `NEW_SIGNAL`
- `UPDATE`
- `CANCEL_PENDING`
- `MOVE_STOP`
- `TAKE_PROFIT`
- `CLOSE_POSITION`
- `INFO_ONLY`
- `SETUP_INCOMPLETE`
- `INVALID`

---

## Current implementation status
Phase 4 minimum parser is implemented with:
- eligibility + trader resolution + minimal classification
- raw field extraction
- strong-link pre-check
- normalized parse result generation
- non-blocking normalized validator warnings
- parser replay support with normalized output samples

---

## Immediate next focus
- validate normalized output on larger real message samples
- improve multilingual update subtype mapping
- prepare Phase 5 update-to-trade matching with stronger root linkage
