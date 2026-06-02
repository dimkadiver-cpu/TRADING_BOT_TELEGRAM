# Parser V2 Architecture

This skill targets the current parser runtime, not the legacy profile stack under `profili_vecchi`.

## Core Runtime

Primary entrypoints:

- `src/parser_v2/core/runtime.py`
- `src/parser_v2/profiles/registry.py`

The runtime expects a profile object that satisfies the current `TraderParserProfile` protocol. In practice, the profile must expose:

- `trader_code`
- `load_markers()`
- `load_rules()`
- `extract_signal(text, context)`
- `extract_intent_entities(text, context, classification_hint=None, signal_hint=None)`

The runtime flow is:

1. normalize text;
2. load markers and rules from the profile;
3. run classification and evidence resolution;
4. extract signal-level structures;
5. extract intents/entities;
6. build a `ParsedMessage`;
7. translate to canonical output when required by downstream code.

## Current Profile Layout

Current profiles live under:

- `src/parser_v2/profiles/<trader>/`

Typical files:

- `profile.py`
- `signal_extractor.py`
- `intent_entity_extractor.py`
- `rules.json`
- `semantic_markers.json`

Existing concrete examples:

- `src/parser_v2/profiles/trader_a/`
- `src/parser_v2/profiles/trader_b/`
- `src/parser_v2/profiles/trader_c/`

## What Not To Model Against

These are useful only as idea mines, not as the target contract:

- `src/parser_v2/profiles/profili_vecchi/...`
- old `parsing_rules.json` layouts
- old parser wrapper classes that do not participate in `core/runtime.py`

If an older profile has a useful rule, port the behavior, not the architecture.

## Replay Harness

Behavior should be validated through:

- `parser_test/scripts/replay_parser_v2.py`

This script reads from `raw_messages`, resolves the effective profile, runs `UniversalParserRuntime`, and persists parser results for review/reporting.

## Dataset Source Of Truth

For profile-building work, the source of truth is the parser test SQLite DB, especially:

- `raw_messages`
- `parser_results_v2` after replay

Useful fields in `raw_messages`:

- `raw_message_id`
- `telegram_message_id`
- `source_chat_id`
- `source_topic_id`
- `reply_to_message_id`
- `message_ts`
- `raw_text`
- `source_trader_id`
- `resolved_trader_id`

## Design Implication

A parser profile built from DB evidence should answer two questions clearly:

1. Which message families exist in the real dataset?
2. Which part of the current profile structure owns each family?

If that mapping is unclear, keep researching before editing code.
