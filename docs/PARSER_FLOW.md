# PARSER FLOW

## Scope
The parser converts Telegram raw messages into a consistent internal parse result.

In this project, a single Telegram source can publish messages from multiple traders.
Because of that, the source chat alone does not identify the effective trader.

## Parsing pipeline

1. Save raw message
2. Apply eligibility filter
3. Resolve trader
4. Classify message intent
5. Resolve linkage
6. Validate operational eligibility
7. Save parse result

---

## 1. Eligibility Filter

### Goal
Decide whether a message may enter the operational parsing flow.

### Rule
All messages are saved as raw messages, but not all of them are candidates for operational parsing.

### Possible outcomes
- `ELIGIBLE`
- `EXCLUDED_ADMIN`
- `EXCLUDED_STATS`
- `EXCLUDED_SERVICE`
- `EXCLUDED_BY_RULE`

### Typical exclusions
- admin tags such as `#admin` or `[admin]`
- recap, statistics, rankings, performance reports
- service or maintenance announcements
- source-specific or trader-specific excluded patterns

### Important rule
An excluded message may still produce a parse result, but it must not enter the operational flow.

---

## 2. Trader Resolution

### Goal
Resolve the effective internal trader for the message.

### Important project rule
A single source chat may contain messages from multiple traders.
Therefore, `source_chat_id` is not enough to resolve the trader.

### Relevant logical fields
- `source_chat_id`
- `declared_trader_tag`
- `resolved_trader_id`
- `trader_resolution_method`

### Resolution methods, in order
1. `DIRECT_TAG`
   - The message contains an explicit trader tag.
   - Example: `[trader#A]`, `[trader#B]`, `[trader#3]`

2. `REPLY_INHERIT`
   - The message has no trader tag, but it is a reply to a known parent message.
   - The trader is inherited from the parent.

3. `SOURCE_DEFAULT`
   - Used only when a source truly represents a single trader.
   - For multi-trader channels this should be avoided or used with caution.

4. `UNRESOLVED`
   - No explicit tag, no usable parent, no reliable default.

### Rule
A message may become operational only if the trader is resolved.

---

## 3. Intent Classification

### Minimum categories
- `NEW_SIGNAL`
- `SETUP_INCOMPLETE`
- `UPDATE`
- `INFO_ONLY`
- `UNCLASSIFIED`

### New signal
A `NEW_SIGNAL` is operational only if it includes:
- symbol
- direction
- entry
- stop
- at least one target

If the idea is present but required data is missing, classify as `SETUP_INCOMPLETE`.

---

## 4. Linkage Resolution

### Goal
Resolve which prior message or trade an update refers to.

### Relevant logical fields
- `linkage_method`
- `linkage_status`
- `linkage_target_raw_message_id`
- `linkage_target_trade_id`

### Supported linkage methods
1. `REPLY`
   - direct reply to a prior message
2. `MESSAGE_LINK`
   - Telegram message link pointing to the original message
3. `EXPLICIT_MESSAGE_ID`
   - explicit message reference inside the text
4. `CONTEXT_FALLBACK`
   - context based on trader, symbol, time, state, uniqueness

### Linkage status
- `CONFIRMED`
- `POSSIBLE`
- `NONE`

### Strong rule for short updates
Short updates such as:
- `cancel`
- `close`
- `breakeven`
- `move sl`

may be auto-applied only if linkage is strong:
- `REPLY`
- `MESSAGE_LINK`
- `EXPLICIT_MESSAGE_ID`

Not by weak contextual guess alone.

---

## 5. Operational Promotion Rules

### New signals
A new signal may enter the operational flow only if:
- `eligibility_status = ELIGIBLE`
- trader resolved
- setup complete

### Incomplete setups
Saved, but never auto-executed.

### Updates
An update may enter the operational flow only if:
- `eligibility_status = ELIGIBLE`
- trader resolved
- linkage status is `CONFIRMED`

Otherwise:
- save it
- do not auto-apply it

---

## 6. Special cases for this project

### Full signal with trader tag
Example:
`[trader#A] BUY BTC ...`

Result:
- `declared_trader_tag = trader#A`
- `resolved_trader_id = TA`
- method = `DIRECT_TAG`

### Reply cancellation without tag
Parent:
`[trader#A] BUY BTC ...`

Reply:
`cancel`

Result:
- trader inherited via `REPLY_INHERIT`
- linkage resolved via `REPLY`
- update may be operational

### Admin message mentioning trader
Example:
`#admin trader#A weekly stats`

Result:
- raw saved
- eligibility excluded
- not operational

---

## 7. Recommended config blocks

### `message_filters`
For excluded tags, admin markers, stats patterns, service messages.

### `trader_tag_map`
Maps textual trader tags to canonical trader ids.
Examples:
- `trader#A` -> `TA`
- `trader#B` -> `TB`
- `trader#3` -> `T3`

### `linkage_rules`
For source-specific or trader-specific linkage priorities.
Examples:
- prefer reply
- allow Telegram message links
- require strong linkage for short updates
- disable weak context fallback

---

## 8. Parse result fields to keep explicit
- `eligibility_status`
- `eligibility_reason`
- `declared_trader_tag`
- `resolved_trader_id`
- `trader_resolution_method`
- `linkage_method`
- `linkage_status`
- `linkage_target_raw_message_id`
- `linkage_target_trade_id`
