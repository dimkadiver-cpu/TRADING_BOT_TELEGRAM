# Leverage Hint Config Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Gate parser-driven leverage override behind a new config flag `use_trader_leverage_hint`, defaulting to `false`, so trader leverage hints are ignored unless explicitly enabled.

**Architecture:** Keep the current extraction and transport path unchanged. Add the new flag to `RiskConfig`, load it through the existing config loader and YAML defaults, and make `RiskCapacityEngine` the only owner of whether `signal.leverage_hint` is ignored or used. Because downstream metadata and clean-log already depend on `leverage_hint_applied`, they should naturally follow the new behavior once lifecycle only emits that metadata when the flag is enabled.

**Tech Stack:** Python 3.12, Pydantic v2, YAML config loading, pytest, runtime_v2 lifecycle/control-plane pipeline.

---

## File Map

- Modify: `src/runtime_v2/signal_enrichment/models.py`
  Responsibility: `RiskConfig` contract adds `use_trader_leverage_hint`.
- Modify: `src/runtime_v2/signal_enrichment/config_loader.py`
  Responsibility: merge/load the new `risk` flag from defaults and per-trader overrides via existing `RiskConfig(**merged.get("risk", {}))`.
- Modify: `config/operation_config.yaml`
  Responsibility: declare `use_trader_leverage_hint: false` in default runtime config.
- Modify: `src/runtime_v2/lifecycle/risk_capacity.py`
  Responsibility: ignore or honor `signal.leverage_hint` based on config flag.
- Modify: `tests/runtime_v2/signal_enrichment/test_config_loader.py`
  Responsibility: verify config loader accepts and surfaces the new flag.
- Modify: `tests/runtime_v2/lifecycle/test_risk_leverage_validation.py`
  Responsibility: verify off-by-default behavior, enabled override behavior, and enabled cap rejection.
- Modify: `tests/runtime_v2/lifecycle/test_entry_gate.py`
  Responsibility: verify `plan_state_json` only carries `leverage_hint_applied` when the flag is enabled.
- Modify: `tests/runtime_v2/control_plane/test_clean_log_formatter.py`
  Responsibility: verify passthrough/note behavior remains absent when the flag is off and present when it is on.

### Task 1: Add `use_trader_leverage_hint` to Config Contracts

**Files:**
- Modify: `src/runtime_v2/signal_enrichment/models.py`
- Modify: `config/operation_config.yaml`
- Test: `tests/runtime_v2/signal_enrichment/test_config_loader.py`

- [ ] **Step 1: Write the failing config-loader test**

```python
def test_config_loader_reads_use_trader_leverage_hint_flag():
    global_cfg = {
        "account_mode": "single",
        "account": {
            "id": "main",
            "capital_base_usdt": 1000.0,
            "max_leverage": 5,
            "max_capital_at_risk_pct": 10.0,
            "hard_max_per_signal_risk_pct": 2.0,
        },
        "registered_traders": ["trader_a"],
        "symbol_blacklist": {"global": [], "per_trader": {}},
        "defaults": {
            "enabled": True,
            "gate_mode": "block",
            "hedge_mode": False,
            "signal_policy": {...},
            "update_admission": {},
            "management_plan": {...},
            "risk": {
                "mode": "risk_pct_of_capital",
                "risk_pct_of_capital": 1.0,
                "capital_base_mode": "static_config",
                "capital_base_usdt": 1000.0,
                "leverage": 5,
                "use_trader_risk_hint": False,
                "use_trader_leverage_hint": True,
                "max_capital_at_risk_per_trader_pct": 5.0,
                "max_concurrent_trades": 5,
                "max_concurrent_same_symbol": 1,
            },
        },
    }
    loader = _make_loader_from_raw(global_cfg)
    cfg = loader.get_effective_config("trader_a")
    assert cfg is not None
    assert cfg.risk.use_trader_leverage_hint is True
```

- [ ] **Step 2: Run the focused config-loader test to verify it fails**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/signal_enrichment/test_config_loader.py -k use_trader_leverage_hint -v
```

Expected: `FAIL` because `RiskConfig` does not yet declare the field.

- [ ] **Step 3: Implement the minimal config contract changes**

In `src/runtime_v2/signal_enrichment/models.py` update `RiskConfig`:

```python
class RiskConfig(BaseModel):
    mode: Literal["risk_pct_of_capital", "risk_usdt_fixed"] = "risk_pct_of_capital"
    risk_pct_of_capital: float = 1.0
    risk_usdt_fixed: float = 10.0
    capital_base_mode: Literal["static_config", "live_equity"] = "static_config"
    capital_base_usdt: float = 1000.0
    leverage: int = 1
    use_trader_risk_hint: bool = False
    use_trader_leverage_hint: bool = False
    risk_hint_range_mode: Literal["min_value", "max_value", "midpoint"] = "min_value"
    max_capital_at_risk_per_trader_pct: float = 5.0
    max_concurrent_trades: int = 5
    max_concurrent_same_symbol: int = 1
```

In `config/operation_config.yaml` add the new default under `defaults.risk`:

```yaml
  risk:
    mode: risk_pct_of_capital
    risk_pct_of_capital: 0.5
    risk_usdt_fixed: 10.0
    capital_base_mode: static_config
    capital_base_usdt: 10000.0
    leverage: 10
    use_trader_risk_hint: true
    use_trader_leverage_hint: false
    risk_hint_range_mode: min_value
```

No explicit loader branching is needed if the field is accepted by `RiskConfig(**merged.get("risk", {}))`.

- [ ] **Step 4: Run the focused config-loader test to verify it passes**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/signal_enrichment/test_config_loader.py -k use_trader_leverage_hint -v
```

Expected: `PASS`

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/signal_enrichment/models.py config/operation_config.yaml tests/runtime_v2/signal_enrichment/test_config_loader.py
git commit -m "feat: add config gate for trader leverage hints"
```

### Task 2: Gate Leverage Override in `RiskCapacityEngine`

**Files:**
- Modify: `src/runtime_v2/lifecycle/risk_capacity.py`
- Test: `tests/runtime_v2/lifecycle/test_risk_leverage_validation.py`

- [ ] **Step 1: Write failing lifecycle tests for flag-off and flag-on behavior**

Extend the helper:

```python
def _make_risk(leverage: int = 5, use_trader_leverage_hint: bool = False) -> RiskConfig:
    return RiskConfig(
        leverage=leverage,
        capital_base_usdt=1000.0,
        risk_pct_of_capital=1.0,
        use_trader_leverage_hint=use_trader_leverage_hint,
    )
```

Extend `_make_enriched(...)` to accept `use_trader_leverage_hint`.

Add tests:

```python
def test_leverage_hint_is_ignored_when_flag_is_false():
    engine = RiskCapacityEngine()
    enriched = _make_enriched(
        leverage=5,
        max_leverage=10,
        leverage_hint=3.0,
        use_trader_leverage_hint=False,
    )
    result = engine.validate(enriched, [], None, _make_market_snapshot())
    assert result.passed is True
    assert result.leverage == 5
    assert result.risk_snapshot["leverage"] == 5
    assert result.leverage_hint_applied is None


def test_leverage_hint_overrides_configured_leverage_when_flag_is_true():
    engine = RiskCapacityEngine()
    enriched = _make_enriched(
        leverage=5,
        max_leverage=10,
        leverage_hint=3.0,
        use_trader_leverage_hint=True,
    )
    result = engine.validate(enriched, [], None, _make_market_snapshot())
    assert result.passed is True
    assert result.leverage == 3
    assert result.risk_snapshot["leverage"] == 3
    assert result.leverage_hint_applied is not None


def test_leverage_hint_above_account_max_blocks_only_when_flag_is_true():
    engine = RiskCapacityEngine()
    enriched = _make_enriched(
        leverage=3,
        max_leverage=5,
        leverage_hint=6.0,
        use_trader_leverage_hint=True,
    )
    result = engine.validate(enriched, [], None, _make_market_snapshot())
    assert result.passed is False
    assert result.reason == "signal_leverage_hint_exceeds_account_max_leverage"
```

- [ ] **Step 2: Run the focused lifecycle test file to verify failure**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/lifecycle/test_risk_leverage_validation.py -v
```

Expected: `FAIL` because current lifecycle behavior still treats `leverage_hint` as always enabled.

- [ ] **Step 3: Implement the minimal lifecycle gate**

In `src/runtime_v2/lifecycle/risk_capacity.py`, gate `_resolve_effective_leverage(...)` at the call site:

```python
raw_effective_leverage, effective_leverage, leverage_hint_applied = _resolve_effective_leverage(
    risk.leverage,
    signal.leverage_hint if risk.use_trader_leverage_hint else None,
)
```

Do not change the metadata shape or rejection reason.

This keeps all existing leverage-hint logic but makes it opt-in through config.

- [ ] **Step 4: Run the focused lifecycle test file to verify it passes**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/lifecycle/test_risk_leverage_validation.py -v
```

Expected: `PASS`

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/lifecycle/risk_capacity.py tests/runtime_v2/lifecycle/test_risk_leverage_validation.py
git commit -m "feat: gate leverage override behind config flag"
```

### Task 3: Align `plan_state_json` Persistence With the New Flag

**Files:**
- Modify: `tests/runtime_v2/lifecycle/test_entry_gate.py`

- [ ] **Step 1: Write failing entry-gate tests for flag-off persistence**

Extend `_make_enriched_signal(...)` with:

```python
use_trader_leverage_hint: bool = False,
```

and pass it through `RiskConfig(...)` in the helper.

Add tests:

```python
def test_gate_signal_no_leverage_hint_applied_key_when_flag_false():
    gate = _make_gate()
    enriched = _make_enriched_signal(
        leverage=5,
        leverage_hint=3.0,
        use_trader_leverage_hint=False,
    )
    result = gate.process_signal(enriched, [], "NONE")
    plan = json.loads(result.trade_chain.plan_state_json)
    assert "leverage_hint_applied" not in plan


def test_gate_signal_copies_leverage_hint_applied_when_flag_true():
    gate = _make_gate()
    enriched = _make_enriched_signal(
        leverage=5,
        leverage_hint=3.0,
        use_trader_leverage_hint=True,
    )
    result = gate.process_signal(enriched, [], "NONE")
    plan = json.loads(result.trade_chain.plan_state_json)
    assert plan["leverage_hint_applied"]["configured_leverage"] == 5
    assert plan["leverage_hint_applied"]["effective_leverage"] == 3
```

- [ ] **Step 2: Run the focused entry-gate tests to verify failure**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/lifecycle/test_entry_gate.py -k "leverage_hint_applied" -v
```

Expected: `FAIL` because the helper currently cannot express the new flag and existing expectations assume always-on behavior.

- [ ] **Step 3: Implement the minimal helper/test alignment**

In `_make_enriched_signal(...)`, pass the flag into `RiskConfig(...)`:

```python
risk=RiskConfig(
    mode="risk_pct_of_capital",
    risk_pct_of_capital=risk_pct,
    capital_base_mode="static_config",
    capital_base_usdt=capital_base_usdt,
    leverage=leverage,
    max_capital_at_risk_per_trader_pct=50.0,
    max_concurrent_trades=max_concurrent_trades,
    max_concurrent_same_symbol=max_concurrent_same_symbol,
    use_trader_risk_hint=use_trader_risk_hint,
    use_trader_leverage_hint=use_trader_leverage_hint,
)
```

No `entry_gate.py` semantic code change should be necessary if Task 2 is correct, because `entry_gate` already persists only when `decision.leverage_hint_applied` exists.

- [ ] **Step 4: Run the focused entry-gate tests to verify they pass**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/lifecycle/test_entry_gate.py -k "leverage_hint_applied" -v
```

Expected: `PASS`

- [ ] **Step 5: Commit**

```bash
git add tests/runtime_v2/lifecycle/test_entry_gate.py
git commit -m "test: align entry gate leverage metadata with config flag"
```

### Task 4: Align Clean-Log Expectations With the New Flag

**Files:**
- Modify: `tests/runtime_v2/control_plane/test_clean_log_formatter.py`

- [ ] **Step 1: Write failing projection/formatter tests for disabled flag behavior**

Add an integration-style projection test using the real `SIGNAL_ACCEPTED` outbox path:

```python
def test_outbox_writer_signal_accepted_omits_leverage_hint_applied_when_plan_omits_it(tmp_path):
    ...
    without_hint = project(12, base_plan)
    assert "leverage_hint_applied" not in without_hint
```

Add/update formatter expectations:

```python
def test_signal_accepted_no_leverage_override_no_note():
    text = format_clean_log("SIGNAL_ACCEPTED", {
        "chain_id": 15,
        "symbol": "BTC/USDT",
        "side": "LONG",
        "entries": [{"sequence": 1, "entry_type": "LIMIT", "price": 65000.0}],
        "sl": 62000.0,
        "tps": [68000.0],
        "source": "original_message",
    })
    assert "Leverage - Overridden by trader" not in text
```

Ensure there is also a positive case representing enabled-and-applied metadata.

- [ ] **Step 2: Run the focused clean-log test file to verify failure**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/control_plane/test_clean_log_formatter.py -k "leverage_hint_applied or leverage_override_note" -v
```

Expected: if existing tests assume always-on persistence, at least one should fail until adjusted.

- [ ] **Step 3: Implement the minimal test expectation alignment**

Update tests so they reflect the new contract:

- when `plan_state_json` lacks `leverage_hint_applied`, outbox payload must omit it;
- when payload lacks `leverage_hint_applied`, clean-log must not render the note;
- when payload contains `leverage_hint_applied`, clean-log must render the note.

No code changes in `outbox_writer.py` or `clean_log.py` should be necessary if upstream tasks are correct.

- [ ] **Step 4: Run the focused clean-log test file to verify it passes**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/control_plane/test_clean_log_formatter.py -k "leverage_hint_applied or leverage_override_note" -v
```

Expected: `PASS`

- [ ] **Step 5: Commit**

```bash
git add tests/runtime_v2/control_plane/test_clean_log_formatter.py
git commit -m "test: align clean log leverage note with config gate"
```

### Task 5: Run the Narrow Cross-Layer Regression Slice

**Files:**
- Test: `tests/runtime_v2/signal_enrichment/test_config_loader.py`
- Test: `tests/runtime_v2/lifecycle/test_risk_leverage_validation.py`
- Test: `tests/runtime_v2/lifecycle/test_entry_gate.py`
- Test: `tests/runtime_v2/control_plane/test_clean_log_formatter.py`

- [ ] **Step 1: Run the exact cross-layer slice**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/signal_enrichment/test_config_loader.py tests/runtime_v2/lifecycle/test_risk_leverage_validation.py tests/runtime_v2/lifecycle/test_entry_gate.py tests/runtime_v2/control_plane/test_clean_log_formatter.py -q
```

Expected: all tests that cover the new config gate behavior `PASS`, or the run is blocked only by unrelated baseline failures.

- [ ] **Step 2: If failures are leverage-gate regressions, fix only the owned surface**

Ownership guide:

```text
- config field missing / merge issue -> models.py or test_config_loader.py
- lifecycle still always-on -> risk_capacity.py or test_risk_leverage_validation.py
- plan_state_json still persists when flag off -> test_entry_gate.py helper or lifecycle gate path
- clean-log note mismatch -> test_clean_log_formatter.py expectations, or upstream payload if actually wrong
```

- [ ] **Step 3: Re-run the same cross-layer slice**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/signal_enrichment/test_config_loader.py tests/runtime_v2/lifecycle/test_risk_leverage_validation.py tests/runtime_v2/lifecycle/test_entry_gate.py tests/runtime_v2/control_plane/test_clean_log_formatter.py -q
```

Expected: `PASS`, or clearly reported unrelated blockers.

- [ ] **Step 4: Commit the integrated follow-up**

```bash
git add src/runtime_v2/signal_enrichment/models.py src/runtime_v2/lifecycle/risk_capacity.py config/operation_config.yaml tests/runtime_v2/signal_enrichment/test_config_loader.py tests/runtime_v2/lifecycle/test_risk_leverage_validation.py tests/runtime_v2/lifecycle/test_entry_gate.py tests/runtime_v2/control_plane/test_clean_log_formatter.py
git commit -m "feat: gate trader leverage hints behind config"
```

## Self-Review

### Spec coverage

- New field `use_trader_leverage_hint` with default `false`: covered by Task 1.
- Loader/config support for defaults and per-trader overrides: covered by Task 1.
- Lifecycle gate deciding whether parser leverage is ignored or honored: covered by Task 2.
- Account-cap blocking only in enabled path: covered by Task 2.
- `plan_state_json` metadata only in enabled-and-applied path: covered by Task 3.
- Clean-log note only in enabled-and-applied path: covered by Task 4.
- Cross-layer validation of the follow-up behavior: covered by Task 5.

### Placeholder scan

- No `TODO`/`TBD` placeholders remain.
- Each task contains concrete code or test snippets and exact commands.
- No step relies on “similar to previous task”.

### Type consistency

- Flag name is consistently `use_trader_leverage_hint`.
- Metadata key remains consistently `leverage_hint_applied`.
- Existing rejection reason remains consistently `signal_leverage_hint_exceeds_account_max_leverage`.
