## Leverage Hint Runtime Override Design

Date: 2026-06-27
Status: Draft
Scope: `EnrichedSignalPayload`, `RiskCapacityEngine`, `ExecutionPlanBuilder`, clean-log signal notifications

## Goal

Use `leverage_hint` extracted from the signal text as the effective runtime leverage when present, while preserving the configured leverage as the fallback when no hint is provided.

The applied override must be persisted so downstream layers such as clean-log and auditing can report it without re-reading parser or enrichment storage.

## Problem

The parser already extracts `leverage_hint`, but the runtime risk path still treats `risk.leverage` from config as the only leverage source.

Current gaps:

1. `leverage_hint` is not guaranteed to reach the lifecycle decision point.
2. The lifecycle has no explicit rule to resolve effective leverage from signal override vs config fallback.
3. The applied override is not persisted in `plan_state_json`, so clean-log cannot reliably show a note based on the actual runtime decision.

## Design Decisions

### 1. Same integration principle as `risk_hint`, different semantics

`leverage_hint` follows the same runtime integration principle as `risk_hint`:

- parser extracts the value;
- signal enrichment transports it;
- lifecycle decides whether and how it applies;
- downstream layers read persisted metadata instead of re-deriving the decision.

The semantics are intentionally different:

- `risk_hint` is reduce-only;
- `leverage_hint` is a full override of configured leverage when present.

### 2. Effective leverage resolution

The lifecycle resolves effective leverage using this rule:

```python
effective_leverage = signal.leverage_hint if signal.leverage_hint is not None else risk.leverage
```

Behavior:

- if `leverage_hint` is present, it overrides configured leverage;
- if `leverage_hint` is absent, runtime behavior remains unchanged and `risk.leverage` is used;
- no reduce-only or min/max merge rule is applied between hint and config.

### 3. Hard account leverage cap remains authoritative

After resolving effective leverage, the lifecycle validates it against `account.max_leverage`.

```python
if effective_leverage > config.account.max_leverage:
    return RiskDecision(
        passed=False,
        reason="signal_leverage_hint_exceeds_account_max_leverage",
    )
```

Behavior:

- the signal is blocked when the extracted leverage exceeds the account hard cap;
- no clamping is performed;
- no automatic fallback to configured leverage is performed in this case.

This keeps the signal-author intent explicit while preserving account-level safety.

### 4. `signal_enrichment` transports `leverage_hint`

`EnrichedSignalPayload` gets:

```python
leverage_hint: float | None = None
```

`SignalEnrichmentProcessor._process_signal()` copies `canonical_message.signal.leverage_hint` into the enriched payload.

No validation or policy branch is added in enrichment. The owner layer remains lifecycle/risk.

### 5. `risk_snapshot` stores effective leverage

The `risk_snapshot` written by `RiskCapacityEngine` must store the resolved effective leverage, not always the configured one.

This matters because:

- execution gateway uses the snapshot leverage when issuing commands;
- audit and downstream reporting should reflect the actual runtime decision;
- margin-related calculations should remain aligned with the leverage that was actually accepted.

### 6. Persist `leverage_hint_applied` in `plan_state_json`

When a chain is created with an extracted leverage override, `plan_state_json` gets:

```json
"leverage_hint_applied": {
  "hint_used": true,
  "hint_raw": "20x",
  "hint_effective": 20,
  "configured_leverage": 10,
  "effective_leverage": 20
}
```

Rules:

- present only when the extracted leverage was actually applied;
- absent when the signal had no `leverage_hint`;
- absent when no chain is created because the leverage exceeded the account cap.

This metadata should be assembled at chain-creation time through the same `extra_plan_metadata` pattern already used for other runtime annotations.

### 7. Clean-log note for leverage override

Signal-phase notifications may surface the applied override only when it actually happened.

Expected note copy:

- `Leverage - Overridden by trader`

Placement rule:

- include it in the optional `Notes:` section, in the same style used for risk and similar runtime notes;
- derive it from persisted runtime metadata or the same lifecycle decision object in the reject path, never by re-parsing raw text.

This keeps notification text aligned with the real runtime decision.

## Affected Layers

### `src/runtime_v2/signal_enrichment/models.py`

- `EnrichedSignalPayload`: add `leverage_hint: float | None = None`

### `src/runtime_v2/signal_enrichment/processor.py`

- `_process_signal()`: copy `signal.leverage_hint` into the enriched payload

### `src/runtime_v2/lifecycle/risk_capacity.py`

- resolve `effective_leverage` from signal override vs config fallback
- validate `effective_leverage` against `account.max_leverage`
- return a dedicated block reason when the override exceeds the account cap
- write effective leverage into `risk_snapshot`
- expose optional metadata for downstream persistence

### `src/runtime_v2/lifecycle/execution_plan.py`

- no semantic ownership change
- continue supporting top-level metadata merge through `extra_plan_metadata`

### `src/runtime_v2/lifecycle/entry_gate.py`

- when chain creation succeeds with leverage override applied, add `leverage_hint_applied` to `extra_plan_metadata` before building `plan_state_json`

### `src/runtime_v2/control_plane` formatters / outbox path

- signal-phase notifications read the persisted metadata and append the leverage note in `Notes:`

## Non-Goals

- Changing parser extraction rules for `leverage_hint`
- Introducing reduce-only semantics for leverage
- Clamping extracted leverage down to account max
- Falling back to configured leverage when a present override exceeds account max
- Re-deriving the note from raw signal text in clean-log

## Acceptance Contract

Done means an extracted leverage value can drive execution planning when present, is blocked if it exceeds the account cap, and is visible in runtime metadata and clean-log.

Pass/fail criteria:

1. When `leverage_hint` is present and within `account.max_leverage`, `risk_snapshot["leverage"]` matches the extracted value.
2. When `leverage_hint` is absent, runtime uses configured `risk.leverage` unchanged.
3. When `leverage_hint` is present and exceeds `account.max_leverage`, validation fails with a dedicated policy reason and no automatic fallback is used.
4. When chain creation succeeds with leverage override applied, `plan_state_json` contains `leverage_hint_applied` with the correct raw and effective values.
5. Signal-phase clean-log notifications show `Leverage - Overridden by trader` only when the override metadata is present.
6. Signal-phase clean-log notifications do not show the note when runtime used configured leverage.

## Testing Strategy

Minimum coverage:

1. lifecycle test: extracted leverage inside cap overrides configured leverage in `risk_snapshot`
2. lifecycle test: absent hint falls back to configured leverage
3. lifecycle test: extracted leverage above cap blocks with dedicated reason
4. entry-gate or execution-plan integration test: `plan_state_json` persists `leverage_hint_applied`
5. control-plane formatter test: note appears when metadata exists
6. control-plane formatter test: note is absent when metadata does not exist

## Risks

### Schema extension in enriched signal payload

Adding `leverage_hint` is additive and optional.

### Block reason naming

If existing reject/review formatting relies on known reason strings, the new reason must be checked across notification and audit surfaces.

### Plan-state readers

Any reader that assumes a fixed top-level shape for `plan_state_json` must tolerate the additional `leverage_hint_applied` key.

## Documentation Notes

No user-facing setup or configuration contract changes are required by this design. If implementation reveals that any operator-facing docs describe configured leverage as the only runtime source, those docs should be aligned during implementation.
