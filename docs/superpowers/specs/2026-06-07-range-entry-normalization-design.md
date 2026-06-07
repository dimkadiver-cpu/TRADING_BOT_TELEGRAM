# Range Entry Normalization Design

Date: 2026-06-07
Status: Proposed and user-approved in chat
Scope: `runtime_v2` signal enrichment, lifecycle chain creation, clean-log signal notifications

## Goal

Normalize parser `RANGE` entries into the runtime semantics already supported by the lifecycle engine, while preserving the origin metadata needed for `SIGNAL_ACCEPTED`, `SIGNAL_REJECTED`, and `REVIEW_REQUIRED` clean-log messages.

## Problem

Today `RANGE` handling is split across layers:

- signal enrichment can collapse a range to one derived price (`midpoint`, `firstpoint`, `lastpoint`) or keep both endpoints;
- downstream lifecycle logic does not have native `RANGE` behavior;
- runtime behavior is effectively driven by:
  - number of legs in `plan_state_json`;
  - leg `sequence`;
  - averaging detection as `sequence > 1`;
- clean-log signal notifications are projected from `ops_trade_chains` and lifecycle events, not directly from parser output.

This creates two issues:

1. A collapsed range can still be labeled `RANGE` even though runtime semantics are `ONE_SHOT`.
2. Endpoint-based ranges behave like averaging in practice, but the semantic normalization is implicit rather than explicit.
3. The derived-rule origin (`midpoint`, `lastpoint`, etc.) is not available where clean-log needs it.

## Design Decisions

### 1. Runtime semantic normalization

`RANGE` must be normalized during enrichment into the runtime entry structure the engine already understands.

- `RANGE + midpoint` -> `ONE_SHOT`
- `RANGE + firstpoint` -> `ONE_SHOT`
- `RANGE + lastpoint` -> `ONE_SHOT`
- `RANGE + endpoints` -> `TWO_STEP`

Rationale:

- the lifecycle engine already treats a single leg as `ONE_SHOT`;
- the lifecycle engine already treats `sequence > 1` as averaging;
- `endpoints` is operationally equivalent to an initial entry plus a pending averaging leg;
- using `TWO_STEP` for `endpoints` makes the runtime contract explicit instead of accidental.

### 2. Parser origin must remain separate from runtime semantics

The normalized `entry_structure` must express runtime behavior only.

The fact that a signal originally came from a parser-level `RANGE` must be stored as separate metadata, not encoded inside `entry_structure`.

Required metadata:

- `derived_from_range: true|false`
- `range_split_mode: endpoints|midpoint|firstpoint|lastpoint|none`
- `range_original_bounds`
  - minimum original entry price
  - maximum original entry price

If the incoming signal is not parser-level `RANGE`, these fields may be absent or set to neutral values.

### 3. Metadata persistence location

Range-derivation metadata must be copied into `plan_state_json` when the trade chain is created.

Rationale:

- clean-log already reads from `ops_trade_chains` and `plan_state_json`;
- no extra read back to `enriched_canonical_messages` is needed;
- this keeps runtime notification projection self-contained.

This design intentionally does not require new dedicated DB columns.

### 4. Clean-log visibility rules

Range-derivation metadata must be shown only in initial signal-phase notifications:

- `SIGNAL_ACCEPTED`
- `SIGNAL_REJECTED`
- `REVIEW_REQUIRED`

It must not be repeated in later execution-phase notifications such as:

- `ENTRY_OPENED`
- `ENTRY_UPDATED`
- `TP_FILLED`
- `SL_FILLED`
- `POSITION_CLOSED`

### 5. Endpoints behavior after first fill

When `RANGE + endpoints` is normalized to `TWO_STEP`, the second leg must remain active as a real averaging leg after the first leg fills.

This means:

- no automatic cancellation merely because the first leg filled;
- standard existing averaging lifecycle rules continue to apply;
- `cancel_averaging_pending_after` remains the owner rule for post-TP auto-cancel behavior.

## Affected Layers

### Signal enrichment

Responsibilities:

- apply range split policy;
- normalize runtime `entry_structure`;
- produce range-derivation metadata;
- append explicit enrichment log entries describing the derivation.

Required output behavior:

- collapsed range returns one entry leg and `entry_structure=ONE_SHOT`;
- endpoint range returns two entry legs and `entry_structure=TWO_STEP`;
- original parser-level range provenance is preserved separately.

### Lifecycle chain creation

Responsibilities:

- copy normalized `entry_structure` into `trade_chain.entry_mode`;
- copy range-derivation metadata into `plan_state_json`.

Required outcome:

- runtime logic works entirely from normalized semantics;
- clean-log signal projection can read the provenance metadata locally from `ops`.

### Clean-log projection and formatting

Responsibilities:

- read the provenance metadata from `plan_state_json`;
- enrich only signal-phase notification payloads with a human-readable label.

Suggested display behavior:

- `Derived entry: midpoint from range 63000-65000`
- `Derived entry: firstpoint from range 63000-65000`
- `Derived entry: lastpoint from range 63000-65000`
- `Derived entry: endpoints from range 63000-65000`

The exact copy can be adjusted later, but the information content should remain the same.

## Logging Requirements

Enrichment must record derivation in `enrichment_log`.

Minimum log shape:

- `check="range_price_derived"` for collapsed modes
- `original="<min>-<max>"`
- `result="<derived price>"`
- `detail="<split_mode>"`

For `endpoints`, enrichment should still record that normalization happened, even if no new single derived price is computed. A separate check name such as `range_endpoints_retained` is acceptable.

## Acceptance Contract

Done means runtime semantics, persistence, and clean-log visibility are aligned for parser-level `RANGE` entries.

Pass/fail criteria:

1. `midpoint`, `firstpoint`, and `lastpoint` produce a single runtime leg and `entry_structure=ONE_SHOT`.
2. `endpoints` produces two runtime legs and `entry_structure=TWO_STEP`.
3. Range provenance metadata is available in `plan_state_json` for created chains.
4. `SIGNAL_ACCEPTED`, `SIGNAL_REJECTED`, and `REVIEW_REQUIRED` can show the derivation label without re-reading enrichment storage.
5. Existing averaging behavior for the second endpoint leg remains intact.

Primary signal:

- a parser `RANGE` message leads to the expected normalized runtime shape and the expected signal-phase clean-log annotation.

Secondary signals:

- targeted unit tests for signal enrichment normalization;
- targeted lifecycle tests for chain creation metadata propagation;
- targeted clean-log projection/formatter tests for signal notifications;
- no regression in averaging auto-cancel and deferred-BE behavior.

## Non-Goals

- introducing native runtime semantics distinct from `ONE_SHOT` and `TWO_STEP` just for parser-level ranges;
- adding new dedicated DB columns for range provenance;
- changing post-entry notifications to repeatedly mention the original range rule.

## Risks

### Semantic drift in tests

Some tests may currently assume parser `RANGE` survives into runtime `entry_mode`. Those tests will need to be updated to assert normalized runtime semantics instead.

### Hidden downstream readers of `entry_mode`

Any code that implicitly expects `RANGE` in `ops_trade_chains.entry_mode` must be identified and updated to use the provenance metadata if it truly needs parser-origin information.

### Copy inconsistency

If provenance metadata is generated in enrichment but not copied into `plan_state_json`, signal clean-log will remain blind to it. Propagation must be covered by tests.

## Implementation Notes

Recommended metadata shape inside `plan_state_json`:

```json
{
  "range_derivation": {
    "derived_from_range": true,
    "split_mode": "midpoint",
    "original_min_price": 63000.0,
    "original_max_price": 65000.0
  }
}
```

For non-range signals, omit `range_derivation` entirely.

## Open Questions

None for this phase. The behavioral choices were resolved in chat:

- collapsed range -> `ONE_SHOT`
- endpoints -> `TWO_STEP`
- provenance kept separately
- provenance shown only in signal-phase clean-log messages
