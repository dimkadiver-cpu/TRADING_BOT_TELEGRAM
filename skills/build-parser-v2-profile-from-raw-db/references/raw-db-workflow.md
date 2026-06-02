# Raw DB Workflow

Use this loop when building or updating a profile from `raw_messages`.

## 1. Pick The Right DB

Typical locations:

- `parser_test/db/*.sqlite3`

If the user gives a trader or topic but not a path:

- inspect `parser_test/README.md`;
- search the DB folder for matching datasets;
- prefer the smallest DB that still preserves the real message families you need.

## 2. Sample Before You Generalize

Never start by editing parser rules blind.

First pull a representative sample:

- all messages for one `resolved_trader_id`;
- or one `source_topic_id`;
- or one reply-chain-heavy slice if updates depend on message threading.

Recommended first pass:

- 50 to 150 rows;
- ordered by `raw_message_id`;
- include `reply_to_message_id`.

Use:

- `skills/build-parser-v2-profile-from-raw-db/scripts/sample_raw_messages.py`

## 3. Build A Family Table

For sampled messages, annotate compactly:

- entry signal
- management/update
- partial close
- stop move / break-even
- info
- noise
- ambiguous

Then note the evidence:

- exact lexical markers;
- numeric shape;
- whether reply context is required;
- whether direction/symbol can be inferred locally.

## 4. Decide Profile Ownership

Map each family to one of:

- `semantic_markers.json`
- `rules.json`
- `signal_extractor.py`
- `intent_entity_extractor.py`
- shared runtime change

Avoid profile-local hacks if the issue is actually shared runtime behavior.

## 5. Implement In Small Steps

Good order:

1. markers/rules;
2. extractor logic;
3. registry wiring for new profiles;
4. narrow tests;
5. replay.

## 6. Replay And Read Results

Typical replay:

```powershell
python parser_test/scripts/replay_parser_v2.py `
  --db-path parser_test/db/<dataset>.sqlite3 `
  --trader-filter <trader_code> `
  --parser-profile <trader_code> `
  --force-reparse
```

Look for:

- false opens vs true updates;
- missed SL/TP extraction;
- messages that should be `INFO` but become actionable;
- inconsistent target binding across reply chains.

## 7. Close With Evidence

Your final report should separate:

- what the dataset demonstrated;
- what the code now supports;
- what still lacks coverage because the DB does not contain enough examples.
