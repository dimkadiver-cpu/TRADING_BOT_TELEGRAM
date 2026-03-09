# CONFIG SCHEMA

## Existing config blocks
- source filters
- telegram source map
- trader aliases

## New recommended config blocks

### `message_filters`
Used to exclude non-operational messages.

Suggested content:
- admin markers
- excluded hashtags
- service patterns
- stats/report patterns
- source-specific exclusions

### `trader_tag_map`
Maps trader tags found in text to canonical trader ids.

Examples:
- `trader#A` -> `TA`
- `trader#B` -> `TB`
- `trader#3` -> `T3`

### `linkage_rules`
Defines supported linkage methods and priorities.

Examples:
- allowed methods per source or trader
- reply priority
- message link support
- explicit message id support
- whether short updates require strong linkage

## Important note
`telegram_source_map.json` may still be used for source identification,
but in a multi-trader source it must not be the sole source of trader truth.
