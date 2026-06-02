---
name: build-parser-v2-profile-from-raw-db
description: Build or update a `src/parser_v2` trader profile from `raw_messages` data in the parser test SQLite DB. Use when you need to derive markers, rules, extractors, and validation loops from real Telegram raw messages instead of guessing from docs or legacy parser code.
---

# Build Parser V2 Profile From Raw DB

## Overview

Use this skill when the task is to create or evolve a trader parser profile from the current `parser_v2` architecture and a real dataset stored in `raw_messages`.

This skill is for the current runtime only. It targets `src/parser_v2/core/runtime.py`, `src/parser_v2/profiles/<trader>/`, `parser_test/scripts/replay_parser_v2.py`, and the SQLite datasets under `parser_test/db/`. Do not anchor the work on legacy `profili_vecchi` layouts unless you are explicitly mining them for ideas.

## When To Use

Use this skill when one of these is true:

- the user wants a new `parser_v2` trader profile built from raw Telegram messages;
- the user wants to refine markers, rules, or extractor logic using a parser test DB;
- the user wants a profile "prepared" from real message families before implementation;
- the current parser profile exists but has drift from actual `raw_messages`;
- replay results need to drive parser profile design, not intuition.

Do not use this skill for:

- generic Telegram ingestion problems unrelated to parser behavior;
- legacy `src/parser_v2/profiles/profili_vecchi/...` maintenance unless explicitly requested;
- pure report export work with no parser profile changes.

## Workflow

### 1. Ground On The Current Architecture

Inspect the real `parser_v2` shape before designing anything:

- `src/parser_v2/core/runtime.py`
- `src/parser_v2/profiles/registry.py`
- one current concrete profile such as `src/parser_v2/profiles/trader_a/`
- `parser_test/scripts/replay_parser_v2.py`
- `parser_test/README.md`

Use [references/parser-v2-architecture.md](./references/parser-v2-architecture.md) as the fast map.

Non-negotiable rule:

- design for the current `TraderParserProfile` protocol and current profile directory layout;
- do not invent `parsing_rules.json`, legacy parser wrappers, or old `TraderParseResult` contracts if the current runtime does not use them.

### 2. Identify The Dataset Slice

Find the DB and the subset of messages that matter:

- known DB path such as `parser_test/db/parser_test__trader_a_topic.sqlite3`;
- trader-filtered or topic-filtered subsets;
- reply chains when update messages depend on prior entry signals;
- optional date windows when the dataset is too large.

Use [scripts/sample_raw_messages.py](./scripts/sample_raw_messages.py) to inspect and export representative raw samples from `raw_messages`.

Minimum dataset hygiene:

- verify the table is `raw_messages`;
- inspect `resolved_trader_id`, `source_trader_id`, `source_topic_id`, `reply_to_message_id`, `message_ts`, `raw_text`;
- check whether the same trader style contains distinct message families that should be handled differently.

### 3. Build The Message Family Map

Before touching code, cluster the dataset into a compact behavioral map. At minimum classify:

- entry/opening signals;
- update/manage/modify messages;
- close/partial/BE/SL messages;
- info/noise/non-actionable messages;
- ambiguous or malformed messages.

For each family, extract:

- repeated opening markers;
- repeated management markers;
- recurring symbol formats;
- direction vocabulary;
- entry/TP/SL numeric patterns;
- references to prior legs, prior targets, or reply context;
- edge cases that should deliberately become `INFO` or `UNCLASSIFIED`.

The output of this phase should be a profile design note, not yet implementation detail soup.

### 4. Derive The Profile Contract

Translate the family map into the current profile structure:

- `semantic_markers.json`: classification and intent cues;
- `rules.json`: profile-level extraction and normalization rules already supported by current code;
- `signal_extractor.py`: message-to-signal extraction logic for entry-like messages;
- `intent_entity_extractor.py`: management/update/entity extraction logic;
- `profile.py`: current profile loader/delegator implementation;
- `registry.py`: registration is updated automatically when a new trader profile is scaffolded.

If starting from zero, use [scripts/scaffold_parser_v2_profile.py](./scripts/scaffold_parser_v2_profile.py) to scaffold the directory shape under `src/parser_v2/profiles/<trader>/` and update `src/parser_v2/profiles/registry.py`.

Required rule for `semantic_markers.json`:

- do not leave `semantic_markers.json` as scaffold placeholder content;
- populate it from markers actually observed in the sampled `raw_messages`;
- include trader-specific classification, intent, and entity cues when the dataset supports them;
- if a marker is uncertain or weakly supported, call it out explicitly instead of presenting it as stable.

Important discipline:

- first reuse current profile patterns;
- only add new logic when the dataset proves current abstractions are insufficient;
- if a rule belongs in shared runtime instead of one trader profile, call that out explicitly before patching the profile.

### 5. Implement With Evidence

When editing profile files:

- keep `profile.py` thin and loader-oriented;
- keep dataset-specific heuristics in the extractors or marker/rule files;
- make `semantic_markers.json` a real dataset-backed artifact, not an empty placeholder;
- prefer explicit, readable matching over premature abstractions;
- add narrow tests where the repo already has a natural place for them;
- preserve existing unrelated trader profiles.

For a new profile, use one existing profile as the structural template, but adapt behavior only from the target dataset.

### 6. Validate Through Replay

Validation priority:

1. targeted unit tests for new parsing branches or normalization logic;
2. replay against the relevant parser test DB using `parser_test/scripts/replay_parser_v2.py`;
3. optional report generation if the task needs a measurable review artifact.

Use replay to answer:

- are entry messages becoming the expected canonical shape;
- are second-leg or management messages classified as updates rather than fresh opens when appropriate;
- are noise/info messages being suppressed or downgraded correctly;
- are extracted fields stable across the main message families.

Use [references/raw-db-workflow.md](./references/raw-db-workflow.md) for the practical loop.

## Required Output

When you use this skill successfully, produce all of the following:

- the target profile scope: new profile or update of existing profile;
- the message family map derived from `raw_messages`;
- the code/files changed in `src/parser_v2`;
- the validation commands actually run;
- what replay proved and what it did not prove;
- any remaining blind spots in the dataset.

## Decision Rules

- Prefer the owner layer. If the dataset exposes a shared runtime defect, do not hide it inside a trader profile.
- Prefer current `parser_v2` contracts over legacy parser habits.
- Prefer replay-backed rules over guessed regexes.
- If the dataset is too small or too noisy, say that the profile is only partially grounded.
- If reply context is required for correctness, verify it explicitly rather than assuming standalone messages.

## Fast Start

Typical sequence:

```powershell
# 1. inspect representative messages
python skills/build-parser-v2-profile-from-raw-db/scripts/sample_raw_messages.py `
  --db-path parser_test/db/parser_test__trader_a_topic.sqlite3 `
  --resolved-trader trader_a `
  --limit 80

# 2. scaffold a new profile if needed
python skills/build-parser-v2-profile-from-raw-db/scripts/scaffold_parser_v2_profile.py `
  --trader-code trader_x `
  --class-name TraderXProfile

# 3. replay after implementation
python parser_test/scripts/replay_parser_v2.py `
  --db-path parser_test/db/parser_test__trader_a_topic.sqlite3 `
  --trader-filter trader_a `
  --parser-profile trader_a `
  --force-reparse
```

## Resources

### scripts/

- `sample_raw_messages.py`: inspect and export representative rows from `raw_messages`.
- `scaffold_parser_v2_profile.py`: scaffold the current `parser_v2` profile directory shape.

### references/

- `parser-v2-architecture.md`: compact map of the current runtime/profile structure this skill must respect.
- `raw-db-workflow.md`: practical loop for extracting evidence from DB and validating with replay.
