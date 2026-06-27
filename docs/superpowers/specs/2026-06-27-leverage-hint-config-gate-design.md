## Leverage Hint Config Gate Design

Date: 2026-06-27
Status: Draft
Scope: `RiskConfig`, operation-config loading, `RiskCapacityEngine`, `plan_state_json`, clean-log signal notes

## Goal

Make parser-extracted `leverage_hint` opt-in via configuration, using a new flag:

```yaml
use_trader_leverage_hint: false
```

When the flag is `false`, runtime must ignore `signal.leverage_hint` and use configured `risk.leverage`.
When the flag is `true`, runtime may use `signal.leverage_hint` as the effective leverage override, subject to `account.max_leverage`.

## Why This Follow-Up Exists

The earlier design for runtime leverage override assumed that parser `leverage_hint` should always override configured leverage when present.

That is no longer the desired product behavior.

The intended behavior is now aligned with the configurability model used by `risk_hint`:

- there is an explicit runtime flag controlling whether trader-provided leverage hints are honored;
- when the flag is off, configured leverage remains authoritative;
- when the flag is on, parser leverage may override config.

The semantics still differ from `risk_hint`:

- `risk_hint` is reduce-only;
- `leverage_hint` remains full override when enabled.

## Design Decisions

### 1. New config field

`RiskConfig` gets:

```python
use_trader_leverage_hint: bool = False
```

Default is `False`.

This means the system is conservative by default and preserves pre-existing configured leverage behavior unless an operator explicitly enables leverage hints.

### 2. Ownership stays in lifecycle

The parser continues to extract `leverage_hint`.
Signal enrichment continues to transport it unchanged.

The decision whether the hint matters belongs only to `RiskCapacityEngine`.

This preserves the same ownership model already used for runtime risk/capacity decisions:

- parser extracts
- enrichment transports
- lifecycle decides
- downstream layers read persisted metadata

### 3. Effective leverage resolution with config gate

Runtime resolves leverage as follows:

```python
if risk.use_trader_leverage_hint and signal.leverage_hint is not None:
    effective_leverage = signal.leverage_hint
else:
    effective_leverage = risk.leverage
```

Behavior matrix:

| `use_trader_leverage_hint` | `signal.leverage_hint` | effective leverage |
|---|---|---|
| `false` | absent | `risk.leverage` |
| `false` | present | `risk.leverage` |
| `true` | absent | `risk.leverage` |
| `true` | present | `signal.leverage_hint` |

### 4. Account cap still applies only to the resolved value

If the flag is `false`, the parser hint is ignored and cannot trigger a block.

If the flag is `true` and the parser hint is present, the runtime validates the extracted value against `account.max_leverage`.

If the extracted value exceeds account max:

- the signal is blocked;
- no fallback to configured leverage is performed;
- the reject reason remains the leverage-hint-specific reason already introduced for the feature path.

This keeps operator intent explicit: enabling trader leverage hints means trusting the text when valid, and rejecting when it violates account policy.

### 5. `leverage_hint_applied` metadata only when the flag is on and the hint is used

`plan_state_json` should include:

```json
"leverage_hint_applied": {
  "hint_used": true,
  "hint_raw": "20.0",
  "hint_effective": 20,
  "configured_leverage": 10,
  "effective_leverage": 20
}
```

only when all of the following are true:

1. `use_trader_leverage_hint == true`
2. `signal.leverage_hint` is present
3. the signal is accepted and the override is actually used

The metadata must be absent when:

- the flag is `false`
- the hint is absent
- no chain is created because validation blocked the signal before plan creation

### 6. Clean-log note remains metadata-driven

No clean-log semantic change is needed beyond the existing behavior.

The note:

- `Leverage - Overridden by trader`

continues to appear only when `leverage_hint_applied` is present in the signal payload.

Because the metadata now becomes conditional on `use_trader_leverage_hint`, the note automatically disappears when the flag is off.

This is the desired behavior.

## Affected Layers

### `src/runtime_v2/signal_enrichment/models.py`

- `RiskConfig`: add `use_trader_leverage_hint: bool = False`

### `src/runtime_v2/signal_enrichment/config_loader.py`

- load and merge `use_trader_leverage_hint` from defaults and per-trader overrides in the same way other `risk` fields are handled

### `config/operation_config.yaml`

- add `use_trader_leverage_hint: false` under the default `risk:` block

### Optional per-trader config overrides

- any trader config may set:

```yaml
risk:
  use_trader_leverage_hint: true
```

when operator wants parser leverage to override config for that trader.

### `src/runtime_v2/lifecycle/risk_capacity.py`

- gate leverage-hint usage behind `risk.use_trader_leverage_hint`
- when flag is `false`, ignore parser hint entirely
- when flag is `true`, preserve existing leverage-hint override behavior
- only emit `leverage_hint_applied` when override is active

### `src/runtime_v2/lifecycle/entry_gate.py`

- no semantic change beyond consuming `decision.leverage_hint_applied` when present

### `src/runtime_v2/control_plane/outbox_writer.py`

- no semantic change beyond copying `leverage_hint_applied` if present

### `src/runtime_v2/control_plane/formatters/templates/clean_log.py`

- no semantic change beyond rendering the note when metadata is present

## Non-Goals

- Changing parser extraction rules for `leverage_hint`
- Making leverage reduce-only
- Adding a second clean-log note for “hint ignored by policy”
- Falling back to configured leverage when enabled hint exceeds account max
- Making the feature globally enabled by default

## Acceptance Contract

Done means parser leverage hints are controlled by explicit config and only affect execution when operators enable them.

Pass/fail criteria:

1. With `use_trader_leverage_hint=false`, runtime uses configured `risk.leverage` even if `signal.leverage_hint` is present.
2. With `use_trader_leverage_hint=true` and `signal.leverage_hint` present, runtime uses the parser hint as effective leverage.
3. With `use_trader_leverage_hint=true` and parser hint above `account.max_leverage`, validation fails with the existing leverage-hint-specific policy reason.
4. With `use_trader_leverage_hint=false`, `plan_state_json` does not contain `leverage_hint_applied`.
5. With `use_trader_leverage_hint=true` and applied override, `plan_state_json` contains `leverage_hint_applied`.
6. Clean-log shows `Leverage - Overridden by trader` only in the enabled-and-applied case.

## Risks

### Backward behavior change relative to the just-added feature

This follow-up intentionally changes the default semantics from “always use parser leverage when present” to “ignore parser leverage unless explicitly enabled”.

That is the desired product correction, but it must be called out because it changes newly introduced behavior.

### Config drift

If `RiskConfig` gains the new field but `operation_config.yaml` or per-trader docs are not updated, operators may not discover the flag.

### Partial rollout

If lifecycle is updated but tests still assume always-on leverage override, test expectations will drift.

## Documentation Notes

Operator-facing config docs should be updated during implementation if they describe leverage as either:

- always coming from config only, or
- always being overridden by parser text.

After this change, the correct statement is:

- parser leverage override is optional and controlled by `risk.use_trader_leverage_hint`.
