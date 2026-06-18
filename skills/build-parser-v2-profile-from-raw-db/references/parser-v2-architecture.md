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
- `extract_signal(text: NormalizedText, context: ParserContext, evidence: list[MarkerEvidence]) -> SignalDraft | None`
- `extract_intent_entities(text: NormalizedText, context: ParserContext, evidence: list[MarkerEvidence]) -> list[ParsedIntent]`

Both extractors receive the resolved `evidence` list produced by `MarkerEvidenceResolver` — not `classification_hint` or `signal_hint`, which do not exist in the current protocol.

The runtime flow is:

1. normalize text (`TextNormalizer`);
2. load markers and rules from the profile;
3. match markers (`MarkerMatcher`) and resolve evidence (`MarkerEvidenceResolver`);
4. **INFO short-circuit**: if any evidence marker has `kind == "info"`, skip steps 5–8 and return an INFO `ParsedMessage` immediately;
5. extract signal-level structures (`profile.extract_signal`, receives `evidence`);
6. extract intents/entities (`profile.extract_intent_entities`, receives `evidence`);
7. disambiguate intents (`LocalDisambiguator`), bind targets (`TargetBindingResolver`), build `ParsedMessage` (`ParsedMessageBuilder`), normalize semantics (`SemanticNormalizer`);
8. translate to `CanonicalMessage` (`CanonicalTranslator`).

## Current Profile Layout

Current profiles live under:

- `src/parser_v2/profiles/<trader>/`

Typical files:

- `profile.py`
- `signal_extractor.py`
- `intent_entity_extractor.py`
- `rules.json`
- `semantic_markers.json`

Existing concrete examples (use these as structural templates):

- `src/parser_v2/profiles/trader_prova/` — canonical reference, clean standalone profile
- `src/parser_v2/profiles/trader_c/`
- `src/parser_v2/profiles/trader_d/`

Do not use `trader_a` or `trader_b` as templates: they delegate their extractors to `Legacy/trader_a_legacy/` and `Legacy/trader_b_legacy/` respectively.

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
