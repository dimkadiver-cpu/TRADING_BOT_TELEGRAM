# Leverage Hint Runtime Override Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `leverage_hint` extracted from the signal override configured runtime leverage when present, block the signal when the extracted leverage exceeds `account.max_leverage`, and surface the applied override in persisted plan metadata and clean-log notes.

**Architecture:** Reuse the existing `risk_hint_applied` pattern. The parser stays responsible for extraction, signal enrichment transports `leverage_hint`, `RiskCapacityEngine` becomes the single owner of effective leverage resolution and policy enforcement, `entry_gate` persists `leverage_hint_applied` in `plan_state_json`, and the clean-log pipeline reads that metadata to render a note. No parser logic or config schema changes are needed.

**Tech Stack:** Python 3.12, Pydantic v2 models, pytest, SQLite-backed runtime_v2 lifecycle/control-plane pipeline.

---

## File Map

- Modify: `src/runtime_v2/signal_enrichment/models.py`
  Responsibility: enriched signal contract carried from parser output to lifecycle.
- Modify: `src/runtime_v2/signal_enrichment/processor.py`
  Responsibility: copy canonical `signal.leverage_hint` into `EnrichedSignalPayload`.
- Modify: `src/runtime_v2/lifecycle/risk_capacity.py`
  Responsibility: resolve effective leverage, enforce account max leverage, persist effective leverage in `risk_snapshot`, emit optional applied metadata.
- Modify: `src/runtime_v2/lifecycle/entry_gate.py`
  Responsibility: copy leverage override metadata into `plan_state_json` during chain creation.
- Modify: `src/runtime_v2/control_plane/outbox_writer.py`
  Responsibility: expose `leverage_hint_applied` from `plan_state_json` in signal notification payloads.
- Modify: `src/runtime_v2/control_plane/formatters/templates/clean_log.py`
  Responsibility: render `Leverage - Overridden by trader` in `Notes:` for signal-phase notifications.
- Modify: `tests/runtime_v2/signal_enrichment/test_processor_signal.py`
  Responsibility: verify enrichment transports `leverage_hint`.
- Modify: `tests/runtime_v2/lifecycle/test_risk_leverage_validation.py`
  Responsibility: verify fallback/config behavior, override behavior, and account-cap rejection reason.
- Modify: `tests/runtime_v2/lifecycle/test_entry_gate.py`
  Responsibility: verify `plan_state_json` includes `leverage_hint_applied` only when the override is really applied.
- Modify: `tests/runtime_v2/control_plane/test_clean_log_formatter.py`
  Responsibility: verify signal-phase clean-log notes include leverage override note only when metadata exists.

### Task 1: Transport `leverage_hint` Into Enriched Signals

**Files:**
- Modify: `src/runtime_v2/signal_enrichment/processor.py`
- Test: `tests/runtime_v2/signal_enrichment/test_processor_signal.py`

- [ ] **Step 1: Write the failing enrichment transport test**

```python
def _make_parse_result_with_leverage_hint(
    leverage_hint_value: float,
    *,
    trader_id: str = "trader_a",
    canonical_message_id: int = 77,
    raw_message_id: int = 770,
):
    from src.parser_v2.contracts.canonical_message import CanonicalMessage, SignalPayload
    from src.parser_v2.contracts.context import RawContext
    from src.parser_v2.contracts.entities import EntryLeg, Price, StopLoss, TakeProfit
    from src.runtime_v2.parser_pipeline.models import CanonicalParseResult
    import datetime

    entries = [EntryLeg(sequence=1, entry_type="LIMIT", price=Price(raw="50000", value=50000.0))]
    take_profits = [TakeProfit(sequence=1, price=Price(raw="51000", value=51000.0))]
    stop_loss = StopLoss(price=Price(raw="49000", value=49000.0))

    try:
        signal = SignalPayload(
            completeness="COMPLETE",
            symbol="BTC/USDT",
            side="LONG",
            entry_structure="ONE_SHOT",
            entries=entries,
            take_profits=take_profits,
            stop_loss=stop_loss,
            leverage_hint=leverage_hint_value,
        )
    except Exception:
        signal = SignalPayload(
            symbol="BTC/USDT",
            side="LONG",
            entry_structure="ONE_SHOT",
            entries=entries,
            take_profits=take_profits,
            stop_loss=stop_loss,
            leverage_hint=leverage_hint_value,
        )

    canonical = CanonicalMessage(
        parser_profile=trader_id,
        primary_class="SIGNAL",
        parse_status="PARSED",
        confidence=1.0,
        signal=signal,
        raw_context=RawContext(raw_text="BTC long x20"),
    )
    return CanonicalParseResult(
        raw_message_id=raw_message_id,
        canonical_message_id=canonical_message_id,
        parser_profile=trader_id,
        primary_class="SIGNAL",
        parse_status="PARSED",
        canonical_message=canonical,
        warnings=[],
        parsed_at=datetime.datetime.now(datetime.timezone.utc),
    )


def test_processor_propagates_leverage_hint_to_enriched_signal(processor):
    result = _make_parse_result_with_leverage_hint(leverage_hint_value=20.0)
    enriched = processor.process(result)
    assert enriched.enriched_signal is not None
    assert enriched.enriched_signal.leverage_hint == pytest.approx(20.0)
```

- [ ] **Step 2: Run the focused enrichment test to verify it fails**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/signal_enrichment/test_processor_signal.py::test_processor_propagates_leverage_hint_to_enriched_signal -v
```

Expected: `FAIL` because `EnrichedSignalPayload` saved by the processor does not yet populate `leverage_hint`.

- [ ] **Step 3: Write the minimal enrichment implementation**

```python
enriched_signal = EnrichedSignalPayload(
    symbol=symbol or None,
    side=signal.side,
    entry_structure=normalized_structure,
    entries=entries,
    take_profits=take_profits,
    stop_loss=signal.stop_loss,
    range_derivation=range_derivation,
    risk_hint=signal.risk_hint,
    leverage_hint=signal.leverage_hint,
    entry_sequence_realigned=entry_sequence_realigned,
    original_tp_count=original_tp_count,
)
```

For the reshape branch, keep parity with the passthrough branch:

```python
return EnrichedSignalPayload(
    symbol=symbol or None,
    side=signal.side,
    entry_structure=derived_structure,
    entries=operative_legs,
    take_profits=new_tps,
    stop_loss=new_sl,
    risk_hint=signal.risk_hint,
    leverage_hint=signal.leverage_hint,
    reshaped=audit,
)
```

- [ ] **Step 4: Run the focused enrichment test to verify it passes**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/signal_enrichment/test_processor_signal.py::test_processor_propagates_leverage_hint_to_enriched_signal -v
```

Expected: `PASS`

- [ ] **Step 5: Commit**

```bash
git add tests/runtime_v2/signal_enrichment/test_processor_signal.py src/runtime_v2/signal_enrichment/processor.py
git commit -m "test: propagate leverage hint through enrichment"
```

### Task 2: Resolve Effective Leverage in `RiskCapacityEngine`

**Files:**
- Modify: `src/runtime_v2/lifecycle/risk_capacity.py`
- Test: `tests/runtime_v2/lifecycle/test_risk_leverage_validation.py`

- [ ] **Step 1: Write failing lifecycle tests for override, fallback, and rejection**

```python
def _make_enriched(
    leverage: int = 5,
    max_leverage: int = 5,
    hedge_mode: bool = False,
    leverage_hint: float | None = None,
):
    entries = [
        EnrichedEntryLeg(
            sequence=1,
            entry_type="LIMIT",
            price=Price(raw="65000", value=65000.0),
            weight=1.0,
        )
    ]
    signal = EnrichedSignalPayload(
        symbol="BTC/USDT:USDT",
        side="LONG",
        entry_structure="ONE_SHOT",
        entries=entries,
        take_profits=[TakeProfit(price=Price(raw="70000", value=70000.0), sequence=1)],
        stop_loss=StopLoss(price=Price(raw="63000", value=63000.0)),
        leverage_hint=leverage_hint,
    )
    ...


def test_leverage_hint_overrides_configured_leverage():
    engine = RiskCapacityEngine()
    enriched = _make_enriched(leverage=5, max_leverage=25, leverage_hint=20.0)
    result = engine.validate(enriched, [], None, _make_market_snapshot())
    assert result.passed is True
    assert result.leverage == 20
    assert result.risk_snapshot["leverage"] == 20
    assert result.leverage_hint_applied == {
        "hint_used": True,
        "hint_raw": "20.0",
        "hint_effective": 20,
        "configured_leverage": 5,
        "effective_leverage": 20,
    }


def test_leverage_hint_absent_falls_back_to_configured_leverage():
    engine = RiskCapacityEngine()
    enriched = _make_enriched(leverage=5, max_leverage=25, leverage_hint=None)
    result = engine.validate(enriched, [], None, _make_market_snapshot())
    assert result.passed is True
    assert result.leverage == 5
    assert result.risk_snapshot["leverage"] == 5
    assert result.leverage_hint_applied is None


def test_leverage_hint_above_account_max_blocks_signal():
    engine = RiskCapacityEngine()
    enriched = _make_enriched(leverage=5, max_leverage=10, leverage_hint=20.0)
    result = engine.validate(enriched, [], None, _make_market_snapshot())
    assert result.passed is False
    assert result.reason == "signal_leverage_hint_exceeds_account_max_leverage"
```

- [ ] **Step 2: Run the focused lifecycle test file to verify failure**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/lifecycle/test_risk_leverage_validation.py -v
```

Expected: `FAIL` because `EnrichedSignalPayload` and `RiskDecision` do not yet support leverage override metadata and the engine still validates only `risk.leverage`.

- [ ] **Step 3: Implement the minimal lifecycle override logic**

Update the decision dataclass:

```python
@dataclass
class RiskDecision:
    passed: bool
    reason: str | None
    size_usdt: float | None = None
    leverage: int | None = None
    risk_snapshot: dict = field(default_factory=dict)
    hint_applied: dict | None = None
    leverage_hint_applied: dict | None = None
```

Resolve effective leverage before the account guard:

```python
configured_leverage = int(risk.leverage)
effective_leverage = configured_leverage
leverage_hint_applied: dict | None = None

if signal.leverage_hint is not None:
    effective_leverage = int(signal.leverage_hint)
    leverage_hint_applied = {
        "hint_used": True,
        "hint_raw": str(signal.leverage_hint),
        "hint_effective": effective_leverage,
        "configured_leverage": configured_leverage,
        "effective_leverage": effective_leverage,
    }
```

Replace the old guard:

```python
if config.account is not None:
    if effective_leverage > config.account.max_leverage:
        return RiskDecision(
            passed=False,
            reason="signal_leverage_hint_exceeds_account_max_leverage",
        )
```

Write the effective leverage into the snapshot and return object:

```python
risk_snapshot = {
    "capital": capital,
    "risk_amount": risk_amount,
    "entry_price": entry_price,
    "entry_price_deferred": entry_price_deferred,
    "sl_price": sl_price,
    "risk_distance": risk_distance,
    "size_usdt": size_usdt,
    "leverage": effective_leverage,
    "hedge_mode": config.hedge_mode,
    "capital_base_mode": risk.capital_base_mode,
    "legs": legs_snapshot,
}

return RiskDecision(
    passed=True,
    reason=None,
    size_usdt=size_usdt,
    leverage=effective_leverage,
    risk_snapshot=risk_snapshot,
    hint_applied=hint_applied,
    leverage_hint_applied=leverage_hint_applied,
)
```

- [ ] **Step 4: Run the focused lifecycle test file to verify it passes**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/lifecycle/test_risk_leverage_validation.py -v
```

Expected: `PASS`

- [ ] **Step 5: Commit**

```bash
git add tests/runtime_v2/lifecycle/test_risk_leverage_validation.py src/runtime_v2/lifecycle/risk_capacity.py
git commit -m "feat: apply leverage hint in risk capacity"
```

### Task 3: Persist `leverage_hint_applied` in `plan_state_json`

**Files:**
- Modify: `src/runtime_v2/lifecycle/entry_gate.py`
- Test: `tests/runtime_v2/lifecycle/test_entry_gate.py`

- [ ] **Step 1: Write failing entry-gate tests for plan metadata**

```python
def _make_enriched_signal(
    *,
    ...,
    leverage: int = 1,
    leverage_hint: float | None = None,
):
    ...
    signal = EnrichedSignalPayload(
        symbol=symbol,
        side=side,
        entry_structure=entry_structure,
        entries=entry_legs,
        take_profits=tps,
        stop_loss=sl,
        range_derivation=(RangeDerivation.model_validate(range_derivation) if range_derivation is not None else None),
        risk_hint=risk_hint,
        leverage_hint=leverage_hint,
        original_tp_count=original_tp_count,
        entry_sequence_realigned=entry_sequence_realigned,
    )
    ...
    risk=RiskConfig(..., leverage=leverage, ...)


def test_gate_signal_copies_leverage_hint_applied_into_plan_state_json():
    gate = _make_gate()
    enriched = _make_enriched_signal(leverage=5, leverage_hint=20.0)
    result = gate.process_signal(enriched, [], "NONE")
    plan = json.loads(result.trade_chain.plan_state_json)
    assert "leverage_hint_applied" in plan
    assert plan["leverage_hint_applied"]["configured_leverage"] == 5
    assert plan["leverage_hint_applied"]["effective_leverage"] == 20


def test_gate_signal_no_leverage_hint_applied_key_when_hint_absent():
    gate = _make_gate()
    enriched = _make_enriched_signal(leverage=5, leverage_hint=None)
    result = gate.process_signal(enriched, [], "NONE")
    plan = json.loads(result.trade_chain.plan_state_json)
    assert "leverage_hint_applied" not in plan
```

- [ ] **Step 2: Run the focused entry-gate tests to verify failure**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/lifecycle/test_entry_gate.py::test_gate_signal_copies_leverage_hint_applied_into_plan_state_json tests/runtime_v2/lifecycle/test_entry_gate.py::test_gate_signal_no_leverage_hint_applied_key_when_hint_absent -v
```

Expected: `FAIL` because `entry_gate` currently persists only `risk_hint_applied`, `range_derivation`, `tp_trimmed`, `entry_sequence_realigned`, and reshape metadata.

- [ ] **Step 3: Implement the minimal plan-state persistence**

In the chain-creation branch of `LifecycleEntryGate.process_signal`, extend `extra_plan`:

```python
extra_plan: dict = {}
if decision.hint_applied:
    extra_plan["risk_hint_applied"] = decision.hint_applied
if decision.leverage_hint_applied:
    extra_plan["leverage_hint_applied"] = decision.leverage_hint_applied
if signal.range_derivation is not None:
    extra_plan["range_derivation"] = signal.range_derivation.model_dump()
if signal.original_tp_count is not None:
    extra_plan["tp_trimmed"] = {
        "original": signal.original_tp_count,
        "used": len(signal.take_profits),
    }
if signal.entry_sequence_realigned is not None:
    extra_plan["entry_sequence_realigned"] = signal.entry_sequence_realigned.model_dump()
```

Do not modify `ExecutionPlanBuilder`; it already merges `extra_plan_metadata`.

- [ ] **Step 4: Run the focused entry-gate tests to verify they pass**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/lifecycle/test_entry_gate.py::test_gate_signal_copies_leverage_hint_applied_into_plan_state_json tests/runtime_v2/lifecycle/test_entry_gate.py::test_gate_signal_no_leverage_hint_applied_key_when_hint_absent -v
```

Expected: `PASS`

- [ ] **Step 5: Commit**

```bash
git add tests/runtime_v2/lifecycle/test_entry_gate.py src/runtime_v2/lifecycle/entry_gate.py
git commit -m "feat: persist leverage hint override in plan state"
```

### Task 4: Surface the Override in Clean-Log Payloads and Formatter Notes

**Files:**
- Modify: `src/runtime_v2/control_plane/outbox_writer.py`
- Modify: `src/runtime_v2/control_plane/formatters/templates/clean_log.py`
- Test: `tests/runtime_v2/control_plane/test_clean_log_formatter.py`

- [ ] **Step 1: Write failing clean-log formatter tests**

```python
def test_signal_accepted_leverage_hint_note():
    text = format_clean_log("SIGNAL_ACCEPTED", {
        "chain_id": 14,
        "symbol": "BTC/USDT",
        "side": "LONG",
        "entries": [{"sequence": 1, "entry_type": "LIMIT", "price": 65000.0}],
        "sl": 62000.0,
        "tps": [68000.0],
        "leverage": 20,
        "leverage_hint_applied": {
            "hint_used": True,
            "hint_raw": "20.0",
            "hint_effective": 20,
            "configured_leverage": 5,
            "effective_leverage": 20,
        },
        "source": "original_message",
    })
    assert "Notes:" in text
    assert "Leverage - Overridden by trader" in text


def test_signal_accepted_without_leverage_hint_note():
    text = format_clean_log("SIGNAL_ACCEPTED", {
        "chain_id": 15,
        "symbol": "BTC/USDT",
        "side": "LONG",
        "entries": [{"sequence": 1, "entry_type": "LIMIT", "price": 65000.0}],
        "sl": 62000.0,
        "tps": [68000.0],
        "leverage": 5,
        "source": "original_message",
    })
    assert "Leverage - Overridden by trader" not in text
```

- [ ] **Step 2: Run the focused formatter tests to verify failure**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/control_plane/test_clean_log_formatter.py::test_signal_accepted_leverage_hint_note tests/runtime_v2/control_plane/test_clean_log_formatter.py::test_signal_accepted_without_leverage_hint_note -v
```

Expected: `FAIL` because the formatter only checks `risk_hint_applied` and the outbox writer does not yet expose `leverage_hint_applied`.

- [ ] **Step 3: Implement the minimal outbox + formatter support**

In `src/runtime_v2/control_plane/outbox_writer.py`, extend the signal payload builder:

```python
if plan.get("risk_hint_applied"):
    payload["risk_hint_applied"] = plan["risk_hint_applied"]
if plan.get("leverage_hint_applied"):
    payload["leverage_hint_applied"] = plan["leverage_hint_applied"]
if plan.get("tp_trimmed"):
    payload["tp_trimmed"] = plan["tp_trimmed"]
```

In `src/runtime_v2/control_plane/formatters/templates/clean_log.py`, extend `_build_signal_notes`:

```python
def _build_signal_notes(p: dict) -> list[str]:
    notes: list[str] = []
    rd = p.get("range_derivation") or {}
    if rd.get("derived_from_range"):
        ...
    if p.get("risk_hint_applied"):
        notes.append("Risk - Reduced by trader")
    if p.get("leverage_hint_applied"):
        notes.append("Leverage - Overridden by trader")
    trim = p.get("tp_trimmed") or {}
    ...
    return notes
```

- [ ] **Step 4: Run the focused formatter tests to verify they pass**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/control_plane/test_clean_log_formatter.py::test_signal_accepted_leverage_hint_note tests/runtime_v2/control_plane/test_clean_log_formatter.py::test_signal_accepted_without_leverage_hint_note -v
```

Expected: `PASS`

- [ ] **Step 5: Commit**

```bash
git add tests/runtime_v2/control_plane/test_clean_log_formatter.py src/runtime_v2/control_plane/outbox_writer.py src/runtime_v2/control_plane/formatters/templates/clean_log.py
git commit -m "feat: show leverage override note in clean log"
```

### Task 5: Run the Coupled Regression Slice

**Files:**
- Test: `tests/runtime_v2/signal_enrichment/test_processor_signal.py`
- Test: `tests/runtime_v2/lifecycle/test_risk_leverage_validation.py`
- Test: `tests/runtime_v2/lifecycle/test_entry_gate.py`
- Test: `tests/runtime_v2/control_plane/test_clean_log_formatter.py`

- [ ] **Step 1: Run the exact regression slice for the changed surface**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/signal_enrichment/test_processor_signal.py tests/runtime_v2/lifecycle/test_risk_leverage_validation.py tests/runtime_v2/lifecycle/test_entry_gate.py tests/runtime_v2/control_plane/test_clean_log_formatter.py -q
```

Expected: all selected tests `PASS`

- [ ] **Step 2: If any assertion fails, fix only the owning layer**

Use this ownership rule while debugging:

```text
- enrichment transport failure -> fix processor/models
- effective leverage or rejection reason failure -> fix risk_capacity
- missing plan_state metadata -> fix entry_gate
- missing note in rendered text -> fix outbox_writer or clean_log formatter
```

- [ ] **Step 3: Re-run the same regression slice until green**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/signal_enrichment/test_processor_signal.py tests/runtime_v2/lifecycle/test_risk_leverage_validation.py tests/runtime_v2/lifecycle/test_entry_gate.py tests/runtime_v2/control_plane/test_clean_log_formatter.py -q
```

Expected: `PASS`

- [ ] **Step 4: Commit the final integrated change**

```bash
git add tests/runtime_v2/signal_enrichment/test_processor_signal.py tests/runtime_v2/lifecycle/test_risk_leverage_validation.py tests/runtime_v2/lifecycle/test_entry_gate.py tests/runtime_v2/control_plane/test_clean_log_formatter.py src/runtime_v2/signal_enrichment/processor.py src/runtime_v2/lifecycle/risk_capacity.py src/runtime_v2/lifecycle/entry_gate.py src/runtime_v2/control_plane/outbox_writer.py src/runtime_v2/control_plane/formatters/templates/clean_log.py
git commit -m "feat: apply signal leverage override through runtime"
```

## Self-Review

### Spec coverage

- Runtime integration principle reused from `risk_hint`: covered by Tasks 1-4.
- Override semantics instead of reduce-only: covered by Task 2.
- Block when `leverage_hint > account.max_leverage`: covered by Task 2.
- Fallback to config when no hint exists: covered by Task 2.
- Persist `leverage_hint_applied` in `plan_state_json`: covered by Task 3.
- Clean-log note `Leverage - Overridden by trader`: covered by Task 4.
- Targeted validation on changed surface: covered by Task 5.

### Placeholder scan

- No `TODO`/`TBD` placeholders remain.
- Each code-changing task includes concrete code snippets and exact test commands.
- Commit messages are explicit and scoped.

### Type consistency

- `signal.leverage_hint` is treated as `float | None` from parser through enrichment and lifecycle.
- Runtime metadata key is consistently named `leverage_hint_applied`.
- Rejection reason is consistently named `signal_leverage_hint_exceeds_account_max_leverage`.
