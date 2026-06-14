# MOVE_STOP Risk Target Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `%` and `R/RR` risk-target support to `MOVE_STOP`, scoped to `trader_prova`, while preserving legacy move-stop behavior and clean-log compatibility.

**Architecture:** Extend shared parser/canonical contracts so `MOVE_STOP` can carry a structured `risk_reduction_target`, teach only `trader_prova` to extract it from new semantic markers, and resolve that target into a concrete `new_stop_price` in the lifecycle update gate. Keep the gateway contract unchanged by still emitting a normal `MOVE_STOP` command with a resolved price and logging metadata.

**Tech Stack:** Python 3.12, Pydantic v2, pytest, parser_v2 profile assets, runtime_v2 lifecycle, clean-log formatter/tests

---

### Task 1: Extend shared contracts for risk-target move stop

**Files:**
- Modify: `src/parser_v2/contracts/entities.py`
- Modify: `src/parser_v2/contracts/enums.py`
- Modify: `src/parser_v2/contracts/canonical_message.py`
- Modify: `src/parser_v2/translation/canonical_translator.py`
- Test: `tests/parser_v2/test_canonical_translator_phase11.py`

- [ ] **Step 1: Write the failing canonical translator tests**

```python
def test_move_stop_risk_percent_translates_to_risk_target() -> None:
    intents = [
        _intent(
            "MOVE_STOP",
            "UPDATE",
            MoveStopEntities(
                risk_reduction_target=RiskReductionTarget(
                    unit="PERCENT_OF_INITIAL_RISK",
                    value=0.4,
                )
            ),
        )
    ]
    canonical = CanonicalTranslator().translate(_parsed_update(intents))
    action = canonical.target_action_groups[0].actions[0]
    assert action.set_stop.target_type == "RISK_TARGET"
    assert action.set_stop.risk_reduction_target.unit == "PERCENT_OF_INITIAL_RISK"
    assert action.set_stop.risk_reduction_target.value == 0.4


def test_move_stop_risk_r_multiple_translates_to_risk_target() -> None:
    intents = [
        _intent(
            "MOVE_STOP",
            "UPDATE",
            MoveStopEntities(
                risk_reduction_target=RiskReductionTarget(
                    unit="R_MULTIPLE",
                    value=0.4,
                )
            ),
        )
    ]
    canonical = CanonicalTranslator().translate(_parsed_update(intents))
    action = canonical.target_action_groups[0].actions[0]
    assert action.set_stop.target_type == "RISK_TARGET"
    assert action.set_stop.risk_reduction_target.unit == "R_MULTIPLE"
```

- [ ] **Step 2: Run the targeted canonical translator tests and verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/parser_v2/test_canonical_translator_phase11.py -k risk_target -v`
Expected: FAIL because `RiskReductionTarget` and `RISK_TARGET` do not exist yet.

- [ ] **Step 3: Add minimal shared contract support**

```python
# src/parser_v2/contracts/entities.py
class RiskReductionTarget(ContractModel):
    unit: Literal["PERCENT_OF_INITIAL_RISK", "R_MULTIPLE"]
    value: float = Field(ge=0.0)


class MoveStopEntities(IntentEntities):
    new_stop_price: Price | None = None
    stop_to_tp_level: int | None = Field(default=None, ge=1)
    risk_reduction_target: RiskReductionTarget | None = None
```

```python
# src/parser_v2/contracts/enums.py
SetStopTargetType = Literal["ENTRY", "PRICE", "TP_LEVEL", "RISK_TARGET"]
```

```python
# src/parser_v2/contracts/canonical_message.py
class SetStopOperation(CanonicalModel):
    target_type: SetStopTargetType
    price: Price | None = None
    tp_level: int | None = Field(default=None, ge=1)
    risk_reduction_target: RiskReductionTarget | None = None
```

```python
# src/parser_v2/translation/canonical_translator.py
elif entities.risk_reduction_target is not None:
    set_stop = SetStopOperation(
        target_type="RISK_TARGET",
        risk_reduction_target=entities.risk_reduction_target,
    )
```

- [ ] **Step 4: Run the canonical translator tests and verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/parser_v2/test_canonical_translator_phase11.py -k risk_target -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/parser_v2/contracts/entities.py src/parser_v2/contracts/enums.py src/parser_v2/contracts/canonical_message.py src/parser_v2/translation/canonical_translator.py tests/parser_v2/test_canonical_translator_phase11.py
git commit -m "feat: add risk target stop contract"
```

### Task 2: Teach `trader_prova` to extract risk-target `MOVE_STOP`

**Files:**
- Modify: `src/parser_v2/profiles/trader_prova/semantic_markers.json`
- Modify: `src/parser_v2/profiles/trader_prova/intent_entity_extractor.py`
- Test: `tests/parser_v2/test_intent_entity_extractor_phase6.py`
- Test: `tests/parser_v2/test_runtime_profile_phase12.py`

- [ ] **Step 1: Add failing extractor tests for `trader_prova`**

```python
def test_trader_prova_move_stop_extracts_percent_of_initial_risk() -> None:
    runtime = UniversalParserRuntime()
    canonical = runtime.parse(
        "сокращаем риск до 0.4%",
        ParserContext(message_id=1),
        TraderProvaProfile(),
    )
    action = canonical.target_action_groups[0].actions[0]
    assert action.set_stop.target_type == "RISK_TARGET"
    assert action.set_stop.risk_reduction_target.unit == "PERCENT_OF_INITIAL_RISK"
    assert action.set_stop.risk_reduction_target.value == 0.4


def test_trader_prova_move_stop_extracts_r_multiple() -> None:
    runtime = UniversalParserRuntime()
    canonical = runtime.parse(
        "сокращаем риск до 0.4R",
        ParserContext(message_id=2),
        TraderProvaProfile(),
    )
    action = canonical.target_action_groups[0].actions[0]
    assert action.set_stop.risk_reduction_target.unit == "R_MULTIPLE"
    assert action.set_stop.risk_reduction_target.value == 0.4
```

- [ ] **Step 2: Run the `trader_prova` parser tests and verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/parser_v2/test_runtime_profile_phase12.py -k trader_prova -v`
Expected: FAIL because current extractor only returns `new_stop_price` or `stop_to_tp_level`.

- [ ] **Step 3: Add the minimal profile extraction logic**

```python
# src/parser_v2/profiles/trader_prova/intent_entity_extractor.py
_RE_RISK_TO_PERCENT = re.compile(r"(?P<value>\d+(?:[.,]\d+)?)\s*%")
_RE_RISK_TO_R = re.compile(r"(?P<value>\d+(?:[.,]\d+)?)\s*(?:r|rr)\b", re.IGNORECASE)

def _move_stop_entities(ev: MarkerEvidence, normalized: NormalizedText) -> MoveStopEntities:
    risk_window = normalized.normalized_text[ev.start:ev.end + 64]
    m_r = _RE_RISK_TO_R.search(risk_window)
    if m_r:
        return MoveStopEntities(
            risk_reduction_target=RiskReductionTarget(
                unit="R_MULTIPLE",
                value=_float_from_raw(m_r.group("value")),
            )
        )
    m_pct = _RE_RISK_TO_PERCENT.search(risk_window)
    if m_pct:
        return MoveStopEntities(
            risk_reduction_target=RiskReductionTarget(
                unit="PERCENT_OF_INITIAL_RISK",
                value=_float_from_raw(m_pct.group("value")),
            )
        )
    ...
```

```json
// src/parser_v2/profiles/trader_prova/semantic_markers.json
"MOVE_STOP": {
  "strong": [
    "...existing...",
    "сокращаем риск до"
  ],
  "weak": ["...existing..."]
}
```

- [ ] **Step 4: Run the profile parser tests and verify they pass without breaking legacy `MOVE_STOP`**

Run: `.venv\Scripts\python.exe -m pytest tests/parser_v2/test_runtime_profile_phase12.py tests/parser_v2/test_intent_entity_extractor_phase6.py -k "trader_prova or move_stop" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/parser_v2/profiles/trader_prova/semantic_markers.json src/parser_v2/profiles/trader_prova/intent_entity_extractor.py tests/parser_v2/test_intent_entity_extractor_phase6.py tests/parser_v2/test_runtime_profile_phase12.py
git commit -m "feat: parse trader_prova move stop risk targets"
```

### Task 3: Resolve risk targets into concrete stop prices in the lifecycle

**Files:**
- Create: `src/runtime_v2/lifecycle/move_stop_risk_resolver.py`
- Modify: `src/runtime_v2/lifecycle/entry_gate.py`
- Test: `tests/runtime_v2/lifecycle/test_entry_gate.py`

- [ ] **Step 1: Add failing lifecycle tests for risk-target updates**

```python
def test_update_move_stop_risk_target_percent_creates_move_stop_command() -> None:
    enriched = _make_update_enriched(scope_hint="SINGLE_SIGNAL", symbols=["BTC/USDT"])
    enriched.enriched_actions[0].actions[0].source_intent = "MOVE_STOP"
    enriched.enriched_actions[0].actions[0].set_stop = SetStopOperation(
        target_type="RISK_TARGET",
        risk_reduction_target=RiskReductionTarget(
            unit="PERCENT_OF_INITIAL_RISK",
            value=0.4,
        ),
    )
    chain = _make_open_chain(entry_avg_price=50000.0, current_stop_price=49000.0)
    chain = chain.model_copy(update={
        "open_position_qty": 0.01,
        "initial_risk_amount": 100.0,
        "risk_snapshot_json": json.dumps({"sl_price": 49000.0, "risk_amount": 100.0}),
    })
    result = _make_gate().process_update(enriched, [chain], {})
    command = next(c for c in result.chain_results[0].execution_commands if c.command_type == "MOVE_STOP")
    payload = json.loads(command.payload_json)
    assert payload["new_stop_price"] == pytest.approx(49960.0)
    assert payload["reference"] == "Risk"


def test_update_move_stop_risk_target_clamps_to_be() -> None:
    ...
    assert any(c.command_type == "MOVE_STOP_TO_BREAKEVEN" for c in cr.execution_commands)
```

- [ ] **Step 2: Run the lifecycle tests and verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/runtime_v2/lifecycle/test_entry_gate.py -k "risk_target or move_stop" -v`
Expected: FAIL because `RISK_TARGET` is unsupported in `process_update()`.

- [ ] **Step 3: Implement a pure resolver helper and wire it into `entry_gate.py`**

```python
# src/runtime_v2/lifecycle/move_stop_risk_resolver.py
def resolve_risk_target_stop_price(
    *,
    side: str,
    entry_avg_price: float,
    open_position_qty: float,
    base_initial_risk: float,
    target_unit: str,
    target_value: float,
) -> float:
    if target_unit == "PERCENT_OF_INITIAL_RISK":
        target_abs_risk = base_initial_risk * target_value / 100.0
    else:
        target_abs_risk = base_initial_risk * target_value
    distance = target_abs_risk / open_position_qty
    return entry_avg_price - distance if side == "LONG" else entry_avg_price + distance
```

```python
# src/runtime_v2/lifecycle/entry_gate.py
if op and op.target_type == "RISK_TARGET" and op.risk_reduction_target is not None:
    return self._apply_move_stop_risk_target(enriched, chain, op.risk_reduction_target, active_commands)
```

```python
def _apply_move_stop_risk_target(...):
    ...
    event_payload = {
        "action": "MOVE_STOP",
        "old_sl_price": old_sl_price,
        "new_sl_price": new_stop_price,
        "reference": "Risk",
        "risk_target_unit": target.unit,
        "risk_target_value": target.value,
    }
```

- [ ] **Step 4: Run the lifecycle tests and verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/runtime_v2/lifecycle/test_entry_gate.py -k "risk_target or move_stop" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/lifecycle/move_stop_risk_resolver.py src/runtime_v2/lifecycle/entry_gate.py tests/runtime_v2/lifecycle/test_entry_gate.py
git commit -m "feat: resolve move stop risk targets in lifecycle"
```

### Task 4: Preserve clean-log and multi-chain summary readability

**Files:**
- Modify: `src/runtime_v2/lifecycle/entry_gate.py`
- Test: `tests/runtime_v2/control_plane/test_clean_log_formatter.py`
- Test: `tests/runtime_v2/control_plane/test_outbox_writer.py`

- [ ] **Step 1: Add failing logging tests for risk-based move stop**

```python
def test_update_done_move_stop_risk_reference_is_renderable():
    text = format_clean_log("UPDATE_DONE", {
        "chain_id": 1,
        "symbol": "BTC/USDT",
        "side": "LONG",
        "applied_actions": ["MOVE_STOP"],
        "changed": [{"field": "SL", "old": 49000.0, "new": 49960.0, "note": "Risk"}],
    })
    assert "SL:" in text
    assert "Risk" in text
```

- [ ] **Step 2: Run the clean-log tests and verify the failing case**

Run: `.venv\Scripts\python.exe -m pytest tests/runtime_v2/control_plane/test_clean_log_formatter.py tests/runtime_v2/control_plane/test_outbox_writer.py -k "risk or move_stop" -v`
Expected: FAIL if the note/reference path does not render `Risk` cleanly.

- [ ] **Step 3: Make the minimal logging-compatible adjustment**

```python
# src/runtime_v2/lifecycle/entry_gate.py
_VALID_REFS = {"Price", "TP_1", "TP_2", "TP_3", "Risk"}
```

```python
# if formatter assertions require it, keep output identical except accepting "Risk"
changed.append({
    "field": "SL",
    "old": p.get("old_sl_price"),
    "new": p.get("new_sl_price"),
    "note": p.get("reference") if p.get("reference") in _VALID_REFS else None,
})
```

- [ ] **Step 4: Run the logging tests and verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/runtime_v2/control_plane/test_clean_log_formatter.py tests/runtime_v2/control_plane/test_outbox_writer.py -k "risk or move_stop" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/lifecycle/entry_gate.py tests/runtime_v2/control_plane/test_clean_log_formatter.py tests/runtime_v2/control_plane/test_outbox_writer.py
git commit -m "test: preserve clean log output for risk stop moves"
```

### Task 5: Run focused regression coverage

**Files:**
- Test only: `tests/parser_v2/test_canonical_translator_phase11.py`
- Test only: `tests/parser_v2/test_intent_entity_extractor_phase6.py`
- Test only: `tests/parser_v2/test_runtime_profile_phase12.py`
- Test only: `tests/runtime_v2/lifecycle/test_entry_gate.py`
- Test only: `tests/runtime_v2/control_plane/test_clean_log_formatter.py`
- Test only: `tests/runtime_v2/control_plane/test_outbox_writer.py`

- [ ] **Step 1: Run parser regression suite**

Run: `.venv\Scripts\python.exe -m pytest tests/parser_v2/test_canonical_translator_phase11.py tests/parser_v2/test_intent_entity_extractor_phase6.py tests/parser_v2/test_runtime_profile_phase12.py -v`
Expected: PASS

- [ ] **Step 2: Run lifecycle regression suite**

Run: `.venv\Scripts\python.exe -m pytest tests/runtime_v2/lifecycle/test_entry_gate.py -k "move_stop or move_to_be or risk_target" -v`
Expected: PASS

- [ ] **Step 3: Run control-plane regression suite**

Run: `.venv\Scripts\python.exe -m pytest tests/runtime_v2/control_plane/test_clean_log_formatter.py tests/runtime_v2/control_plane/test_outbox_writer.py -k "move_stop or update_done or multi_chain" -v`
Expected: PASS

- [ ] **Step 4: Inspect final diff**

Run: `git diff -- src/parser_v2/contracts/entities.py src/parser_v2/contracts/enums.py src/parser_v2/contracts/canonical_message.py src/parser_v2/translation/canonical_translator.py src/parser_v2/profiles/trader_prova/semantic_markers.json src/parser_v2/profiles/trader_prova/intent_entity_extractor.py src/runtime_v2/lifecycle/move_stop_risk_resolver.py src/runtime_v2/lifecycle/entry_gate.py tests/parser_v2/test_canonical_translator_phase11.py tests/parser_v2/test_intent_entity_extractor_phase6.py tests/parser_v2/test_runtime_profile_phase12.py tests/runtime_v2/lifecycle/test_entry_gate.py tests/runtime_v2/control_plane/test_clean_log_formatter.py tests/runtime_v2/control_plane/test_outbox_writer.py`
Expected: only the scoped parser/canonical/lifecycle/logging files changed.

- [ ] **Step 5: Commit final verification state**

```bash
git add src/parser_v2/contracts/entities.py src/parser_v2/contracts/enums.py src/parser_v2/contracts/canonical_message.py src/parser_v2/translation/canonical_translator.py src/parser_v2/profiles/trader_prova/semantic_markers.json src/parser_v2/profiles/trader_prova/intent_entity_extractor.py src/runtime_v2/lifecycle/move_stop_risk_resolver.py src/runtime_v2/lifecycle/entry_gate.py tests/parser_v2/test_canonical_translator_phase11.py tests/parser_v2/test_intent_entity_extractor_phase6.py tests/parser_v2/test_runtime_profile_phase12.py tests/runtime_v2/lifecycle/test_entry_gate.py tests/runtime_v2/control_plane/test_clean_log_formatter.py tests/runtime_v2/control_plane/test_outbox_writer.py
git commit -m "feat: add risk-target support to move stop"
```
