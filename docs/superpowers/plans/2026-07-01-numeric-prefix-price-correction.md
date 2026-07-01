# Numeric Prefix Price Correction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** normalizzare in `signal_enrichment` i prezzi fuori scala per simboli con prefisso numerico exchange, come `1000PEPEUSDT`, usando il `mark_price` e rifiutando i casi non risolvibili.

**Architecture:** introdurre un modulo dedicato `price_corrections.py` con una funzione orchestratrice e una regola specifica per `numeric_prefix`. Il processor applica la correzione prima di `price_sanity`, cosi' `risk_capacity`, piano ed esecuzione leggono tutti lo stesso setup corretto.

**Tech Stack:** Python 3.12, Pydantic v2, pytest, YAML config loader, runtime_v2 signal enrichment.

---

### Task 1: Estendere il modello config `price_corrections`

**Files:**
- Modify: `src/runtime_v2/signal_enrichment/models.py`
- Modify: `src/runtime_v2/signal_enrichment/config_loader.py`
- Modify: `config/operation_config.yaml`
- Test: `tests/runtime_v2/signal_enrichment/test_config_loader.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/runtime_v2/signal_enrichment/test_config_loader.py` an assertion that these keys are parsed:

```python
assert cfg.signal_policy.price_corrections.enabled is True
assert cfg.signal_policy.price_corrections.numeric_prefix_exchange_rescale is True
assert cfg.signal_policy.price_corrections.numeric_prefix_max_mark_deviation_ratio == 0.2
assert cfg.signal_policy.price_corrections.reject_on_unresolved_numeric_prefix_mismatch is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\\Scripts\\python.exe -m pytest tests/runtime_v2/signal_enrichment/test_config_loader.py -q`
Expected: FAIL because `PriceCorrectionsConfig` lacks new fields.

- [ ] **Step 3: Write minimal implementation**

Update `PriceCorrectionsConfig` in `src/runtime_v2/signal_enrichment/models.py`:

```python
class PriceCorrectionsConfig(BaseModel):
    enabled: bool = False
    numeric_prefix_exchange_rescale: bool = False
    numeric_prefix_max_mark_deviation_ratio: float = 0.20
    reject_on_unresolved_numeric_prefix_mismatch: bool = True
    round_to_tick: bool = False
    clamp_to_exchange_precision: bool = False
```

Ensure `src/runtime_v2/signal_enrichment/config_loader.py` keeps constructing the model from `signal_policy_raw.get("price_corrections", {})`.

Update `config/operation_config.yaml` with didascalia comments.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\\Scripts\\python.exe -m pytest tests/runtime_v2/signal_enrichment/test_config_loader.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/runtime_v2/signal_enrichment/test_config_loader.py src/runtime_v2/signal_enrichment/models.py src/runtime_v2/signal_enrichment/config_loader.py config/operation_config.yaml
git commit -m "feat: add numeric prefix price correction config"
```

### Task 2: Introdurre il modulo `price_corrections.py`

**Files:**
- Create: `src/runtime_v2/signal_enrichment/price_corrections.py`
- Test: `tests/runtime_v2/signal_enrichment/test_price_corrections.py`

- [ ] **Step 1: Write the failing test**

Create `tests/runtime_v2/signal_enrichment/test_price_corrections.py` with a focused case:

```python
def test_numeric_prefix_rescales_asset_style_prices_to_contract_style():
    signal = ...
    market_snapshot = ...
    config = PriceCorrectionsConfig(
        enabled=True,
        numeric_prefix_exchange_rescale=True,
        numeric_prefix_max_mark_deviation_ratio=0.20,
        reject_on_unresolved_numeric_prefix_mismatch=True,
    )

    result = apply_price_corrections(signal, market_snapshot, config)

    assert result.rejected is False
    assert result.signal.entries[0].price.value == 0.00226
    assert result.signal.stop_loss.price.value == 0.00263
    assert result.signal.take_profits[0].price.value == 0.00192
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\\Scripts\\python.exe -m pytest tests/runtime_v2/signal_enrichment/test_price_corrections.py -q`
Expected: FAIL because module/function does not exist.

- [ ] **Step 3: Write minimal implementation**

Create `src/runtime_v2/signal_enrichment/price_corrections.py` with:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from copy import deepcopy
import re


@dataclass
class PriceCorrectionAudit:
    check: str
    details: dict[str, object] = field(default_factory=dict)


@dataclass
class PriceCorrectionResult:
    signal: object
    audits: list[PriceCorrectionAudit] = field(default_factory=list)
    rejected: bool = False
    reason_code: str | None = None


def apply_price_corrections(signal, market_snapshot, config) -> PriceCorrectionResult:
    if not config.enabled:
        return PriceCorrectionResult(signal=signal)
    if config.numeric_prefix_exchange_rescale:
        return _correct_numeric_prefix_contract_prices(signal, market_snapshot, config)
    return PriceCorrectionResult(signal=signal)
```

Implement `_correct_numeric_prefix_contract_prices(...)` minimally to:
- extract numeric prefix from base symbol;
- multiply all setup prices by prefix when needed;
- verify entry vs mark price deviation ratio;
- return reject if unresolved.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\\Scripts\\python.exe -m pytest tests/runtime_v2/signal_enrichment/test_price_corrections.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/runtime_v2/signal_enrichment/test_price_corrections.py src/runtime_v2/signal_enrichment/price_corrections.py
git commit -m "feat: add numeric prefix price correction helper"
```

### Task 3: Coprire i casi reject e no-op del helper

**Files:**
- Modify: `tests/runtime_v2/signal_enrichment/test_price_corrections.py`

- [ ] **Step 1: Write the failing tests**

Add three tests:

```python
def test_numeric_prefix_leaves_already_coherent_prices_unchanged(): ...
def test_numeric_prefix_rejects_when_mark_price_missing_and_unresolved(): ...
def test_numeric_prefix_rejects_when_scaled_setup_is_still_incoherent(): ...
```

Key assertions:
- no-op keeps original values and no reject;
- unresolved returns `rejected is True` and `reason_code == "numeric_prefix_price_mismatch_unresolved"`.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\\Scripts\\python.exe -m pytest tests/runtime_v2/signal_enrichment/test_price_corrections.py -q`
Expected: FAIL on missing reject/no-op logic.

- [ ] **Step 3: Write minimal implementation**

Extend `_correct_numeric_prefix_contract_prices(...)` to:
- detect already coherent setup;
- reject when `mark_price` is absent and config demands reject on unresolved mismatch;
- reject when scaled prices violate side ordering or remain too far from mark.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\\Scripts\\python.exe -m pytest tests/runtime_v2/signal_enrichment/test_price_corrections.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/runtime_v2/signal_enrichment/test_price_corrections.py src/runtime_v2/signal_enrichment/price_corrections.py
git commit -m "test: cover numeric prefix correction reject cases"
```

### Task 4: Integrare il helper nel `SignalEnrichmentProcessor`

**Files:**
- Modify: `src/runtime_v2/signal_enrichment/processor.py`
- Test: `tests/runtime_v2/signal_enrichment/test_processor_signal.py`

- [ ] **Step 1: Write the failing test**

Add a processor-level test that:
- enables `price_corrections.numeric_prefix_exchange_rescale`;
- injects a mocked market snapshot with `mark_price=0.0022537`;
- feeds a `1000PEPEUSDT` signal with micro-prices;
- asserts `PASS` and corrected `enriched_signal` values.

Example assertions:

```python
assert enriched.enrichment_decision == "PASS"
assert enriched.enriched_signal.entries[0].price.value == pytest.approx(0.00226)
assert any(log.check == "numeric_prefix_exchange_rescale" for log in enriched.enrichment_log)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\\Scripts\\python.exe -m pytest tests/runtime_v2/signal_enrichment/test_processor_signal.py -q`
Expected: FAIL because processor never applies the correction.

- [ ] **Step 3: Write minimal implementation**

In `src/runtime_v2/signal_enrichment/processor.py`:
- fetch `market_snapshot` for the symbol using the existing port/retrieval path already used downstream;
- call `apply_price_corrections(...)` before `price_sanity`;
- if result is rejected, return `block("numeric_prefix_price_mismatch_unresolved")`;
- if corrected, use corrected signal/entries/SL/TP to build `EnrichedSignalPayload`;
- map correction audits into `EnrichmentLogEntry`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\\Scripts\\python.exe -m pytest tests/runtime_v2/signal_enrichment/test_processor_signal.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/runtime_v2/signal_enrichment/test_processor_signal.py src/runtime_v2/signal_enrichment/processor.py src/runtime_v2/signal_enrichment/price_corrections.py
git commit -m "fix: apply numeric prefix price correction during enrichment"
```

### Task 5: Regressione end-to-end sul sizing runtime

**Files:**
- Modify: `tests/runtime_v2/lifecycle/test_risk_capacity.py`

- [ ] **Step 1: Write the failing test**

Add a regression that validates corrected prices eliminate the mixed-scale sizing:

```python
def test_risk_capacity_uses_corrected_numeric_prefix_prices_consistently():
    signal = corrected_signal_for_1000pepe()
    decision = validator.validate(enriched, open_chains=[], account_snapshot=None, market_snapshot=market_snapshot)
    assert decision.passed is True
    assert decision.risk_snapshot["entry_price"] == pytest.approx(0.0022537)
    assert decision.risk_snapshot["legs"][0]["qty"] < 110000000
```
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\\Scripts\\python.exe -m pytest tests/runtime_v2/lifecycle/test_risk_capacity.py -q`
Expected: FAIL until the fixture/setup uses corrected enrichment output or an equivalent corrected signal.

- [ ] **Step 3: Write minimal implementation**

Keep production code unchanged if Task 4 already fixes the path. Adjust the test fixture/setup so the corrected signal is what reaches `RiskCapacityValidator`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\\Scripts\\python.exe -m pytest tests/runtime_v2/lifecycle/test_risk_capacity.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/runtime_v2/lifecycle/test_risk_capacity.py
git commit -m "test: add numeric prefix sizing regression"
```

### Task 6: Focused verification

**Files:**
- Test: `tests/runtime_v2/signal_enrichment/test_config_loader.py`
- Test: `tests/runtime_v2/signal_enrichment/test_price_corrections.py`
- Test: `tests/runtime_v2/signal_enrichment/test_processor_signal.py`
- Test: `tests/runtime_v2/lifecycle/test_risk_capacity.py`

- [ ] **Step 1: Run focused suite**

Run:

```bash
.venv\Scripts\python.exe -m pytest ^
  tests/runtime_v2/signal_enrichment/test_config_loader.py ^
  tests/runtime_v2/signal_enrichment/test_price_corrections.py ^
  tests/runtime_v2/signal_enrichment/test_processor_signal.py ^
  tests/runtime_v2/lifecycle/test_risk_capacity.py -q
```

Expected: all targeted tests PASS.

- [ ] **Step 2: Review config comments**

Confirm `config/operation_config.yaml` contains the new `price_corrections` keys with didascalia matching repository style.

- [ ] **Step 3: Commit**

```bash
git add config/operation_config.yaml
git commit -m "docs: document numeric prefix price correction config"
```
