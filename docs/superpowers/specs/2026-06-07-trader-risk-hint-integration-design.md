# Trader Risk Hint Integration Design

Date: 2026-06-07
Status: Approved
Scope: `RiskCapacityEngine`, `EnrichedSignalPayload`, `ExecutionPlanBuilder`, `operation_config.yaml`

## Goal

Wire the `risk_hint` extracted by the parser into the runtime risk sizing decision, and persist the applied-hint metadata in `plan_state_json` so downstream layers (clean-log, auditing) can read it without re-querying enrichment storage.

## Problem

`use_trader_risk_hint: true` is declared in `config/operation_config.yaml` and loaded into `RiskConfig`, but the `RiskCapacityEngine` never reads it. The parser extracts `risk_hint` correctly for all profiles (except trader_3), but that value is dropped at the enrichment boundary and never reaches the risk-sizing path.

Three gaps:

1. `EnrichedSignalPayload` does not carry `risk_hint` — the value is lost after enrichment.
2. `RiskCapacityEngine` computes `risk_amount` only from `risk_pct_of_capital`; there is no branch for the hint.
3. `plan_state_json` contains no record of whether an hint was applied or what it was.

## Design Decisions

### 1. Semantics: reduce-only

When `use_trader_risk_hint=true` and the signal carries a `risk_hint`, the hint can only **reduce** the configured risk, never increase it.

```
effective_risk_pct = min(config.risk_pct_of_capital, hint_value)
```

If `hint_value >= config.risk_pct_of_capital`, the configured value is kept unchanged and `hint_applied` is not emitted (the hint had no effect).

All existing cap guards (`hard_max_per_signal_risk_pct`, `max_capital_at_risk_per_trader_pct`) continue to apply on top of the resolved effective risk.

### 2. Range hint resolution via config

`risk_hint` can be a single value (`value: float`) or a range (`min_value/max_value`). When it is a range, which endpoint to use is controlled by a new config field:

```yaml
risk_hint_range_mode: min_value  # Valori: min_value | max_value | midpoint
```

Resolution rules:

| `risk_hint` shape | `risk_hint_range_mode` | resolved value |
|---|---|---|
| single (`value` present) | any | `risk_hint.value` |
| range | `min_value` | `risk_hint.min_value` |
| range | `max_value` | `risk_hint.max_value` |
| range | `midpoint` | `(min_value + max_value) / 2` |

If resolved value is `None` (malformed hint), the hint is skipped and config risk is used unchanged.

### 3. `EnrichedSignalPayload` carries `risk_hint`

`EnrichedSignalPayload` gets a new optional field:

```python
risk_hint: RiskHint | None = None
```

`SignalEnrichmentProcessor._process_signal()` copies `signal.risk_hint` (from `canonical_message.signal`) into `EnrichedSignalPayload`. No gate or validation is added here — the risk engine is the decision point.

### 4. `RiskCapacityEngine` applies the hint

After the base `risk_amount` is computed and before the `max_capital_at_risk` guard, the engine checks:

```python
hint_applied: dict | None = None
if risk.use_trader_risk_hint and signal.risk_hint is not None:
    hint_value = _resolve_risk_hint(signal.risk_hint, risk.risk_hint_range_mode)
    if hint_value is not None:
        hint_risk_amount = capital * hint_value / 100.0
        if hint_risk_amount < risk_amount:
            risk_amount = hint_risk_amount
            hint_applied = {
                "hint_used": True,
                "hint_raw": signal.risk_hint.raw,
                "hint_effective_pct": hint_value,
                "configured_risk_pct": risk.risk_pct_of_capital,
                "effective_risk_pct": hint_value,
            }
```

`_resolve_risk_hint(hint, mode)` is a pure private function. Returns `None` if the hint cannot be resolved (missing values).

When `risk.mode == "risk_usdt_fixed"`, the hint is skipped entirely — percent-based hints are not meaningful against a fixed-USDT budget. The branch is only entered for `risk_pct_of_capital` mode.

`RiskDecision` gets a new optional field `hint_applied: dict | None = None` to carry the metadata forward.

### 5. `plan_state_json` — `risk_hint_applied` block

`ExecutionPlanBuilder.build()` receives an optional `extra_plan_metadata: dict | None = None` parameter. If present, its keys are merged into the top-level plan dict before serialisation.

```python
plan = {
    "plan_version": 1,
    ...
    "legs": legs_out,
}
if extra_plan_metadata:
    plan.update(extra_plan_metadata)
return json.dumps(plan)
```

`entry_gate.py` assembles `extra_plan_metadata` at chain-creation time:

```python
extra: dict = {}
if decision.hint_applied:
    extra["risk_hint_applied"] = decision.hint_applied
if signal.range_derivation is not None:
    extra["range_derivation"] = signal.range_derivation.model_dump()
plan_state = ExecutionPlanBuilder.build(
    eid, signal.entries, signal.take_profits, decision.risk_snapshot,
    extra_plan_metadata=extra or None,
)
```

This also closes the `range_derivation` propagation gap identified in the range-entry normalization spec (`2026-06-07-range-entry-normalization-design.md`).

### 6. `plan_state_json` shape when hint is applied

```json
{
  "plan_version": 1,
  "protection_policy": "TPSL_ATTACHED_FIRST_LEG",
  "rebuild_policy": "ON_EACH_ENTRY_FILL",
  "risk_policy": "REBALANCE_REMAINING_RISK_ON_REPLAN",
  "stop_loss": 64000.0,
  "final_tp": 68000.0,
  "intermediate_tps": [66000.0],
  "legs": [...],
  "risk_hint_applied": {
    "hint_used": true,
    "hint_raw": "1%",
    "hint_effective_pct": 1.0,
    "configured_risk_pct": 2.0,
    "effective_risk_pct": 1.0
  }
}
```

When no hint is applied (flag false, hint absent, or hint did not reduce risk), `risk_hint_applied` is absent. Readers must treat its absence as "no hint applied."

## Affected Layers

### `src/runtime_v2/signal_enrichment/models.py`

- `RiskConfig`: add `risk_hint_range_mode: Literal["min_value", "max_value", "midpoint"] = "min_value"`
- `EnrichedSignalPayload`: add `risk_hint: RiskHint | None = None`

### `src/runtime_v2/signal_enrichment/processor.py`

- `_process_signal()`: copy `signal.risk_hint` into `EnrichedSignalPayload`

### `src/runtime_v2/lifecycle/risk_capacity.py`

- `RiskDecision`: add `hint_applied: dict | None = None`
- `RiskCapacityEngine.validate()`: add hint resolution branch after base `risk_amount`
- Add `_resolve_risk_hint(hint, mode) -> float | None` private function

### `src/runtime_v2/lifecycle/execution_plan.py`

- `ExecutionPlanBuilder.build()`: add `extra_plan_metadata: dict | None = None` parameter; merge into plan before serialisation

### `src/runtime_v2/lifecycle/entry_gate.py`

- Chain-creation callsite: assemble `extra_plan_metadata` from `decision.hint_applied` and `signal.range_derivation`; pass to builder

### `config/operation_config.yaml` and `config/traders/*.yaml`

- Add `risk_hint_range_mode: min_value` under `risk:` block in defaults and in any per-trader override that has a `risk:` block

## Non-Goals

- Deriving the risk note text from anything other than `risk_hint_applied` persisted metadata
- Changing the risk model for `risk_usdt_fixed` mode (hint is percent-based; fixed-USDT mode is unaffected)
- Adding `risk_hint` gating at enrichment level (the risk engine is the decision point)

## Clean-log display contract

The clean-log signal templates may surface the applied hint only in signal-phase notifications
(`SIGNAL_ACCEPTED`, `SIGNAL_REJECTED`, `REVIEW_REQUIRED`) and only inside the optional `Notes:`
section.

Expected copy:

- `Risk - Reduced by trader`

Placement rule:

- `Notes:` appears after the operational body (`Entry_*`, `SL`, `TP_*`, `Risk`, `Leverage`) and
  before the informational footer (`Trader`, `Exchange Account`, `Rejected`, `Source`)

## Acceptance Contract

Done means the hint is applied at runtime and the metadata is readable from `ops_trade_chains.plan_state_json`.

Pass/fail criteria:

1. When `use_trader_risk_hint=true` and signal carries `risk_hint` with a value smaller than `risk_pct_of_capital`, `risk_amount` in `risk_snapshot_json` reflects the reduced value.
2. When `use_trader_risk_hint=true` and `risk_hint.value >= risk_pct_of_capital`, config risk is used unchanged and `risk_hint_applied` is absent from `plan_state_json`.
3. When `use_trader_risk_hint=false`, the hint field is ignored regardless of signal content.
4. `plan_state_json` on the created chain contains `risk_hint_applied` with correct `hint_raw`, `hint_effective_pct`, `configured_risk_pct`, `effective_risk_pct`.
5. Range hints resolve according to `risk_hint_range_mode` config.
6. `extra_plan_metadata` in `ExecutionPlanBuilder.build()` also propagates `range_derivation` when present (closes range spec gap).

## Risks

### `EnrichedSignalPayload` schema change

Adding `risk_hint` is additive and optional (`None` default). Existing serialised enrichment rows are unaffected.

### `RiskDecision` schema change

Adding `hint_applied` is additive (`None` default). No existing callsite breaks.

### `ExecutionPlanBuilder.build()` signature change

The new parameter is optional with `None` default. All existing call sites remain valid without modification (except the entry_gate callsite which is intentionally updated).

### `plan_state_json` readers

Any code that reads `plan_state_json` and is sensitive to unknown top-level keys must be verified. The pattern `json.loads(plan_state_json or "{}")` then `.get("risk_hint_applied")` is safe — missing key returns `None`.
