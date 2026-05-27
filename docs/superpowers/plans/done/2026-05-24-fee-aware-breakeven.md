# Fee-Aware Breakeven Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `be_buffer_pct` with a fee-aware breakeven model that computes `MOVE_STOP_TO_BREAKEVEN` prices from real/stimulated fees and emits `new_stop_price` consistently across manual and automatic BE paths.

**Architecture:** The ownership of BE pricing stays in lifecycle. A new lifecycle helper computes the corrected stop from chain state plus a fallback fee profile. `entry_gate.py` and `event_processor.py` emit `new_stop_price`, `_is_be_or_better` uses the same helper, and the Bybit builder becomes a thin transport layer that consumes the already-computed stop price.

**Tech Stack:** Python 3.12, Pydantic v2, pytest, runtime_v2 lifecycle, CCXT Bybit adapter

---

## File Map

- Create: `src/runtime_v2/lifecycle/breakeven_pricing.py`
- Modify: `src/runtime_v2/signal_enrichment/models.py`
- Modify: `src/runtime_v2/signal_enrichment/config_loader.py`
- Modify: `src/runtime_v2/lifecycle/entry_gate.py`
- Modify: `src/runtime_v2/lifecycle/event_processor.py`
- Modify: `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/order_builder.py`
- Modify: `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/adapter.py`
- Modify: `config/operation_config.yaml`
- Modify: `config/traders/trader_a.yaml`
- Test: `tests/runtime_v2/signal_enrichment/test_models.py`
- Test: `tests/runtime_v2/signal_enrichment/test_config_loader.py`
- Test: `tests/runtime_v2/lifecycle/test_entry_gate.py`
- Test: `tests/runtime_v2/lifecycle/test_event_processor.py`
- Test: `tests/runtime_v2/execution_gateway/test_bybit_order_builder.py`
- Test: `tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py`
- Test: `tests/runtime_v2/lifecycle/test_breakeven_pricing.py`

### Task 1: Replace Management Plan Percentage Fields

**Files:**
- Modify: `src/runtime_v2/signal_enrichment/models.py`
- Modify: `src/runtime_v2/signal_enrichment/config_loader.py`
- Modify: `config/operation_config.yaml`
- Modify: `config/traders/trader_a.yaml`
- Test: `tests/runtime_v2/signal_enrichment/test_models.py`
- Test: `tests/runtime_v2/signal_enrichment/test_config_loader.py`

- [ ] **Step 1: Write the failing model/config tests**

```python
# tests/runtime_v2/signal_enrichment/test_models.py
def test_management_plan_config_defaults():
    from src.runtime_v2.signal_enrichment.models import ManagementPlanConfig

    plan = ManagementPlanConfig()

    assert plan.be_fee_correction_enabled is False
    assert plan.be_fee_fallback_profile is None
    assert not hasattr(plan, "be_buffer_pct")
```

```python
# tests/runtime_v2/signal_enrichment/test_config_loader.py
def test_load_effective_config_reads_fee_aware_be_fields(tmp_path):
    loader = ConfigLoader(str(tmp_path / "operation.yaml"), str(tmp_path / "traders"))
    # fixture config should include:
    # management_plan:
    #   be_fee_correction_enabled: true
    #   be_fee_fallback_profile: "bybit-default"
    cfg = loader.load_effective_config("trader_a")

    assert cfg.management_plan.be_fee_correction_enabled is True
    assert cfg.management_plan.be_fee_fallback_profile == "bybit-default"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/signal_enrichment/test_models.py tests/runtime_v2/signal_enrichment/test_config_loader.py -q
```

Expected:
- FAIL because `be_fee_correction_enabled` and `be_fee_fallback_profile` do not exist yet
- FAIL because config loader still reads `be_buffer_pct`

- [ ] **Step 3: Implement minimal model and config changes**

```python
# src/runtime_v2/signal_enrichment/models.py
class ManagementPlanConfig(BaseModel):
    be_trigger: str | None = None
    be_fee_correction_enabled: bool = False
    be_fee_fallback_profile: str | None = None
    cancel_pending_on_timeout: bool = False
    pending_timeout_hours: int = 24
    close_distribution: CloseDistributionConfig = Field(default_factory=CloseDistributionConfig)
```

```python
# src/runtime_v2/signal_enrichment/config_loader.py
management_plan = ManagementPlanConfig(
    be_trigger=mgmt_raw.get("be_trigger"),
    be_fee_correction_enabled=bool(mgmt_raw.get("be_fee_correction_enabled", False)),
    be_fee_fallback_profile=mgmt_raw.get("be_fee_fallback_profile"),
    cancel_pending_on_timeout=bool(mgmt_raw.get("cancel_pending_on_timeout", False)),
    pending_timeout_hours=int(mgmt_raw.get("pending_timeout_hours", 24)),
    close_distribution=close_distribution,
)
```

```yaml
# config/operation_config.yaml
management_plan:
  be_trigger:
  be_fee_correction_enabled: false
  be_fee_fallback_profile: "default"
```

```yaml
# config/traders/trader_a.yaml
management_plan:
  be_trigger: tp1
  be_fee_correction_enabled: true
  be_fee_fallback_profile: "bybit-default"
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/signal_enrichment/test_models.py tests/runtime_v2/signal_enrichment/test_config_loader.py -q
```

Expected:
- PASS

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/signal_enrichment/models.py src/runtime_v2/signal_enrichment/config_loader.py config/operation_config.yaml config/traders/trader_a.yaml tests/runtime_v2/signal_enrichment/test_models.py tests/runtime_v2/signal_enrichment/test_config_loader.py
git commit -m "refactor(runtime): replace be buffer config with fee-aware flags"
```

### Task 2: Add Lifecycle Fee-Aware Pricing Helper

**Files:**
- Create: `src/runtime_v2/lifecycle/breakeven_pricing.py`
- Test: `tests/runtime_v2/lifecycle/test_breakeven_pricing.py`

- [ ] **Step 1: Write the failing pricing tests**

```python
def test_compute_breakeven_price_long_uses_open_fee_and_close_fee():
    from src.runtime_v2.lifecycle.breakeven_pricing import compute_breakeven_price

    result = compute_breakeven_price(
        side="LONG",
        entry_avg_price=100.0,
        open_position_qty=2.0,
        open_fee_residual=0.2,
        close_fee_rate=0.001,
    )

    net = ((result.new_stop_price - 100.0) * 2.0) - 0.2 - (result.new_stop_price * 2.0 * 0.001)
    assert abs(net) < 1e-9
```

```python
def test_compute_breakeven_price_short_uses_open_fee_and_close_fee():
    from src.runtime_v2.lifecycle.breakeven_pricing import compute_breakeven_price

    result = compute_breakeven_price(
        side="SHORT",
        entry_avg_price=100.0,
        open_position_qty=2.0,
        open_fee_residual=0.2,
        close_fee_rate=0.001,
    )

    net = ((100.0 - result.new_stop_price) * 2.0) - 0.2 - (result.new_stop_price * 2.0 * 0.001)
    assert abs(net) < 1e-9
```

```python
def test_resolve_close_fee_source_falls_back_when_chain_has_no_specific_fee():
    from src.runtime_v2.lifecycle.breakeven_pricing import resolve_close_fee_rate

    rate, source = resolve_close_fee_rate(
        protection_style="attached_full",
        chain_fee_profile=None,
        fallback_profile={"attached_full": 0.0006, "standalone_order": 0.001},
    )

    assert rate == 0.0006
    assert source == "fallback"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/lifecycle/test_breakeven_pricing.py -q
```

Expected:
- FAIL because helper module does not exist yet

- [ ] **Step 3: Implement minimal pricing helper**

```python
from dataclasses import dataclass


@dataclass
class BreakevenPriceResult:
    new_stop_price: float
    open_fee_residual: float
    close_fee_rate: float
    close_fee_source: str


def resolve_close_fee_rate(*, protection_style: str, chain_fee_profile: dict | None, fallback_profile: dict) -> tuple[float, str]:
    if chain_fee_profile and protection_style in chain_fee_profile:
        return float(chain_fee_profile[protection_style]), "chain"
    return float(fallback_profile[protection_style]), "fallback"


def compute_breakeven_price(*, side: str, entry_avg_price: float, open_position_qty: float, open_fee_residual: float, close_fee_rate: float, close_fee_source: str = "chain") -> BreakevenPriceResult:
    q = open_position_qty
    if side == "LONG":
        new_stop_price = (entry_avg_price * q + open_fee_residual) / (q * (1 - close_fee_rate))
    else:
        new_stop_price = (entry_avg_price * q - open_fee_residual) / (q * (1 + close_fee_rate))
    return BreakevenPriceResult(
        new_stop_price=new_stop_price,
        open_fee_residual=open_fee_residual,
        close_fee_rate=close_fee_rate,
        close_fee_source=close_fee_source,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/lifecycle/test_breakeven_pricing.py -q
```

Expected:
- PASS

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/lifecycle/breakeven_pricing.py tests/runtime_v2/lifecycle/test_breakeven_pricing.py
git commit -m "feat(runtime): add fee-aware breakeven pricing helper"
```

### Task 3: Switch Lifecycle BE Emission And Protection Checks To `new_stop_price`

**Files:**
- Modify: `src/runtime_v2/lifecycle/entry_gate.py`
- Modify: `src/runtime_v2/lifecycle/event_processor.py`
- Test: `tests/runtime_v2/lifecycle/test_entry_gate.py`
- Test: `tests/runtime_v2/lifecycle/test_event_processor.py`

- [ ] **Step 1: Write the failing lifecycle tests**

```python
def test_update_move_to_be_payload_uses_new_stop_price_not_buffer():
    result = gate.process_update(enriched, [chain], {})
    command = next(c for c in result.chain_results[0].execution_commands if c.command_type == "MOVE_STOP_TO_BREAKEVEN")
    payload = json.loads(command.payload_json)

    assert "new_stop_price" in payload
    assert "be_buffer_pct" not in payload
    assert "target_price" not in payload
```

```python
def test_tp_filled_be_trigger_payload_uses_new_stop_price_not_buffer():
    result = proc.process(event, chain, [])
    command = next(c for c in result.execution_commands if c.command_type == "MOVE_STOP_TO_BREAKEVEN")
    payload = json.loads(command.payload_json)

    assert "new_stop_price" in payload
    assert "be_buffer_pct" not in payload
```

```python
def test_is_be_or_better_uses_fee_aware_price():
    chain = chain.model_copy(update={"current_stop_price": 50050.0})
    assert gate._is_be_or_better(chain) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/lifecycle/test_entry_gate.py tests/runtime_v2/lifecycle/test_event_processor.py -q
```

Expected:
- FAIL because lifecycle still emits `target_price` and `be_buffer_pct`
- FAIL because `_is_be_or_better` still uses percentage logic

- [ ] **Step 3: Implement minimal lifecycle integration**

```python
# src/runtime_v2/lifecycle/entry_gate.py
be_result = resolve_chain_breakeven_price(chain, mp)
payload = {
    "symbol": chain.symbol,
    "side": chain.side,
    "new_stop_price": be_result.new_stop_price,
    "is_breakeven": True,
    **_be_move_extra(chain),
}
```

```python
# src/runtime_v2/lifecycle/event_processor.py
be_result = resolve_chain_breakeven_price(chain, mp)
cmd_payload = {
    "symbol": chain.symbol,
    "side": chain.side,
    "new_stop_price": be_result.new_stop_price,
    "is_breakeven": True,
    **_be_move_extra(chain),
}
```

```python
# src/runtime_v2/lifecycle/entry_gate.py
def _is_be_or_better(self, chain: TradeChain) -> bool:
    if chain.entry_avg_price is None or chain.current_stop_price is None:
        return False
    mp = ManagementPlanConfig.model_validate_json(chain.management_plan_json)
    if not mp.be_fee_correction_enabled:
        target = chain.entry_avg_price
    else:
        target = resolve_chain_breakeven_price(chain, mp).new_stop_price
    if chain.side == "LONG":
        return chain.current_stop_price >= target
    return chain.current_stop_price <= target
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/lifecycle/test_entry_gate.py tests/runtime_v2/lifecycle/test_event_processor.py -q
```

Expected:
- PASS

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/lifecycle/entry_gate.py src/runtime_v2/lifecycle/event_processor.py tests/runtime_v2/lifecycle/test_entry_gate.py tests/runtime_v2/lifecycle/test_event_processor.py
git commit -m "feat(lifecycle): emit fee-aware breakeven stop prices"
```

### Task 4: Make Bybit Transport Consume `new_stop_price`

**Files:**
- Modify: `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/order_builder.py`
- Modify: `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/adapter.py`
- Test: `tests/runtime_v2/execution_gateway/test_bybit_order_builder.py`
- Test: `tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py`

- [ ] **Step 1: Write the failing builder/adapter tests**

```python
def test_move_stop_to_breakeven_uses_new_stop_price():
    params = _builder().build(
        "MOVE_STOP_TO_BREAKEVEN",
        {
            "symbol": "BTC/USDT:USDT",
            "side": "LONG",
            "new_stop_price": 50075.0,
            "is_breakeven": True,
        },
        "tsb:10:5:sl:1",
    )

    assert params.action == "edit_sl"
    assert params.new_trigger_price == 50075.0
```

```python
def test_move_stop_to_breakeven_attached_uses_new_stop_price():
    params = _builder().build(
        "MOVE_STOP_TO_BREAKEVEN",
        {
            "symbol": "BTC/USDT:USDT",
            "side": "LONG",
            "new_stop_price": 50075.0,
            "is_breakeven": True,
            "protection_style": "attached_full",
            "position_idx": 0,
        },
        "tsb:10:5:sl:2",
    )

    assert params.extra_params["stopLoss"] == "50075.0"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/execution_gateway/test_bybit_order_builder.py tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py -q
```

Expected:
- FAIL because builder still expects `target_price` and `be_buffer_pct`

- [ ] **Step 3: Implement minimal transport-layer changes**

```python
# src/runtime_v2/execution_gateway/adapters/ccxt_bybit/order_builder.py
def _move_stop(self, command_type: str, payload: dict) -> BybitOrderParams:
    if command_type == "MOVE_STOP_TO_BREAKEVEN":
        new_trigger_price = float(payload["new_stop_price"])
    else:
        new_trigger_price = float(payload["new_stop_price"])
```

```python
# src/runtime_v2/execution_gateway/adapters/ccxt_bybit/adapter.py
if command_type == "MOVE_STOP_TO_BREAKEVEN" and "entry_price" in payload and "new_stop_price" not in payload:
    payload = {**payload, "new_stop_price": payload["entry_price"]}
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/execution_gateway/test_bybit_order_builder.py tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py -q
```

Expected:
- PASS

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/execution_gateway/adapters/ccxt_bybit/order_builder.py src/runtime_v2/execution_gateway/adapters/ccxt_bybit/adapter.py tests/runtime_v2/execution_gateway/test_bybit_order_builder.py tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py
git commit -m "refactor(bybit): consume lifecycle breakeven stop price directly"
```

### Task 5: Backward Compatibility, Fallback Wiring, And Final Verification

**Files:**
- Modify: `src/runtime_v2/lifecycle/breakeven_pricing.py`
- Modify: `src/runtime_v2/lifecycle/entry_gate.py`
- Modify: `src/runtime_v2/lifecycle/event_processor.py`
- Modify: `tests/runtime_v2/lifecycle/test_entry_gate.py`
- Modify: `tests/runtime_v2/lifecycle/test_event_processor.py`
- Modify: `tests/runtime_v2/lifecycle/test_breakeven_pricing.py`

- [ ] **Step 1: Write the failing compatibility/fallback tests**

```python
def test_old_management_plan_json_without_fee_fields_uses_safe_defaults():
    mp = ManagementPlanConfig.model_validate_json('{"be_trigger":"tp1","be_buffer_pct":0.05}')
    assert mp.be_fee_correction_enabled is False
    assert mp.be_fee_fallback_profile is None
```

```python
def test_fee_correction_disabled_keeps_pure_entry_breakeven():
    payload = json.loads(command.payload_json)
    assert payload["new_stop_price"] == 50000.0
```

```python
def test_fee_correction_enabled_uses_fallback_when_chain_fee_missing():
    result = resolve_chain_breakeven_price(chain, mp)
    assert result.close_fee_source == "fallback"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/lifecycle/test_breakeven_pricing.py tests/runtime_v2/lifecycle/test_entry_gate.py tests/runtime_v2/lifecycle/test_event_processor.py -q
```

Expected:
- FAIL until defaults and fallback wiring are complete

- [ ] **Step 3: Implement minimal compatibility/fallback support**

```python
# src/runtime_v2/lifecycle/breakeven_pricing.py
def resolve_chain_breakeven_price(chain: TradeChain, mp: ManagementPlanConfig) -> BreakevenPriceResult:
    if not mp.be_fee_correction_enabled:
        return BreakevenPriceResult(
            new_stop_price=float(chain.entry_avg_price or 0.0),
            open_fee_residual=0.0,
            close_fee_rate=0.0,
            close_fee_source="disabled",
        )
    # resolve chain profile first, then fallback profile
```

- [ ] **Step 4: Run focused verification**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/lifecycle/test_breakeven_pricing.py tests/runtime_v2/lifecycle/test_entry_gate.py tests/runtime_v2/lifecycle/test_event_processor.py tests/runtime_v2/execution_gateway/test_bybit_order_builder.py tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py tests/runtime_v2/signal_enrichment/test_models.py tests/runtime_v2/signal_enrichment/test_config_loader.py -q
```

Expected:
- PASS

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/lifecycle/breakeven_pricing.py src/runtime_v2/lifecycle/entry_gate.py src/runtime_v2/lifecycle/event_processor.py tests/runtime_v2/lifecycle/test_breakeven_pricing.py tests/runtime_v2/lifecycle/test_entry_gate.py tests/runtime_v2/lifecycle/test_event_processor.py
git commit -m "feat(runtime): finalize fee-aware breakeven fallback behavior"
```

## Self-Review

- Spec coverage:
  - config/model replacement covered by Task 1
  - helper + formula covered by Task 2
  - manual/automatic BE emission and `_is_be_or_better` covered by Task 3
  - builder/adapter contract covered by Task 4
  - fallback and backward compatibility covered by Task 5
- Placeholder scan:
  - no `TODO`/`TBD`
  - all commands and touched files are explicit
- Type consistency:
  - uses one contract consistently: `new_stop_price`, `be_fee_correction_enabled`, `be_fee_fallback_profile`

## Notes

- This plan intentionally keeps docs updates out of the execution path until code is green. After implementation, align any runtime docs that still mention `be_buffer_pct`.
- The commit steps are part of the plan because the skill requires them; execute them only if the user explicitly wants commits in the implementation phase.
