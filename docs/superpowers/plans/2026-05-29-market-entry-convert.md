# Market Entry Convert Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a Telegram UPDATE says "enter at market now" on a multi-leg LIMIT plan, convert leg1 to MARKET and either cancel or keep subsequent legs based on a per-trader config flag — fixing the CLO-chain-3 class of bug where pending LIMIT orders remain open untracked.

**Architecture:** Four changes in two files. (1) `ManagementPlanConfig` gets `market_convert_mode`. (2) `UpdateChainResult` gets `new_plan_state_json`. (3) `_apply_action_to_chain` routes `MARKET_NOW` + empty entries to a new `_apply_market_entry_now` method. (4) `_persist_update` saves `new_plan_state_json` to DB. The new method reuses the existing `deferred_market` + `EntryCommandFactory` machinery — no new risk-calc logic.

**Tech Stack:** Python 3.12, Pydantic v2, SQLite, pytest

---

## File Map

| File | Change |
|---|---|
| `src/runtime_v2/signal_enrichment/models.py` | Add `market_convert_mode` to `ManagementPlanConfig` |
| `src/runtime_v2/lifecycle/entry_gate.py` | Add field to `UpdateChainResult`; add routing + `_apply_market_entry_now`; fix `_persist_update` |
| `tests/runtime_v2/signal_enrichment/test_models.py` | Tests for new config field |
| `tests/runtime_v2/lifecycle/test_entry_gate.py` | Tests for new UPDATE path |

---

### Task 1: `market_convert_mode` in `ManagementPlanConfig`

**Files:**
- Modify: `src/runtime_v2/signal_enrichment/models.py:82-94`
- Test: `tests/runtime_v2/signal_enrichment/test_models.py`

- [ ] **Step 1: Write the failing tests**

Open `tests/runtime_v2/signal_enrichment/test_models.py` and append:

```python
def test_management_plan_config_market_convert_mode_default():
    from src.runtime_v2.signal_enrichment.models import ManagementPlanConfig
    mp = ManagementPlanConfig()
    assert mp.market_convert_mode == "cancel_subsequent"


def test_management_plan_config_market_convert_mode_keep():
    from src.runtime_v2.signal_enrichment.models import ManagementPlanConfig
    mp = ManagementPlanConfig(market_convert_mode="keep_subsequent")
    assert mp.market_convert_mode == "keep_subsequent"


def test_management_plan_config_market_convert_mode_invalid():
    import pytest
    from pydantic import ValidationError
    from src.runtime_v2.signal_enrichment.models import ManagementPlanConfig
    with pytest.raises(ValidationError):
        ManagementPlanConfig(market_convert_mode="invalid_value")
```

- [ ] **Step 2: Run to confirm failure**

```
pytest tests/runtime_v2/signal_enrichment/test_models.py -k "market_convert_mode" -v
```

Expected: `AttributeError` or `ValidationError` — field does not exist yet.

- [ ] **Step 3: Add field to `ManagementPlanConfig`**

In `src/runtime_v2/signal_enrichment/models.py`, add one line to `ManagementPlanConfig` (after line 93, before the closing of the class):

```python
class ManagementPlanConfig(BaseModel):
    be_trigger: Literal["tp1", "tp2", "tp3"] | None = None
    be_fee_correction_enabled: bool = False
    be_fee_fallback_profile: str | None = None
    close_distribution: CloseDistributionConfig = Field(default_factory=CloseDistributionConfig)
    cancel_pending_by_engine: bool = True
    cancel_pending_on_timeout: bool = True
    pending_timeout_hours: int = 24
    cancel_averaging_pending_after: Literal["tp1", "tp2"] | None = None
    cancel_unfilled_pending_after: Literal["tp1", "tp2"] | None = None
    risk_freed_by_be: bool = True
    protective_sl_mode: Literal["exchange_native_first", "bot_managed"] = "exchange_native_first"
    market_convert_mode: Literal["cancel_subsequent", "keep_subsequent"] = "cancel_subsequent"
```

- [ ] **Step 4: Run tests to confirm pass**

```
pytest tests/runtime_v2/signal_enrichment/test_models.py -k "market_convert_mode" -v
```

Expected: 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/signal_enrichment/models.py tests/runtime_v2/signal_enrichment/test_models.py
git commit -m "feat(lifecycle): add market_convert_mode to ManagementPlanConfig"
```

---

### Task 2: `new_plan_state_json` field on `UpdateChainResult`

**Files:**
- Modify: `src/runtime_v2/lifecycle/entry_gate.py:49-54`
- Test: `tests/runtime_v2/lifecycle/test_entry_gate.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/runtime_v2/lifecycle/test_entry_gate.py`:

```python
def test_update_chain_result_has_new_plan_state_json_field():
    from src.runtime_v2.lifecycle.entry_gate import UpdateChainResult
    cr = UpdateChainResult(
        trade_chain_id=1,
        new_lifecycle_state=None,
        new_be_protection_status=None,
        lifecycle_events=[],
        execution_commands=[],
        new_plan_state_json='{"legs": []}',
    )
    assert cr.new_plan_state_json == '{"legs": []}'


def test_update_chain_result_new_plan_state_json_defaults_to_none():
    from src.runtime_v2.lifecycle.entry_gate import UpdateChainResult
    cr = UpdateChainResult(
        trade_chain_id=1,
        new_lifecycle_state=None,
        new_be_protection_status=None,
        lifecycle_events=[],
        execution_commands=[],
    )
    assert cr.new_plan_state_json is None
```

- [ ] **Step 2: Run to confirm failure**

```
pytest tests/runtime_v2/lifecycle/test_entry_gate.py -k "update_chain_result_has_new_plan" -v
```

Expected: `TypeError` — unexpected keyword argument.

- [ ] **Step 3: Add field to `UpdateChainResult`**

In `src/runtime_v2/lifecycle/entry_gate.py`, find the `UpdateChainResult` dataclass (lines 49–54) and add the field:

```python
@dataclass
class UpdateChainResult:
    trade_chain_id: int
    new_lifecycle_state: LifecycleState | None
    new_be_protection_status: BeProtectionStatus | None
    lifecycle_events: list[LifecycleEvent]
    execution_commands: list[ExecutionCommand]
    new_plan_state_json: str | None = None
```

- [ ] **Step 4: Run tests**

```
pytest tests/runtime_v2/lifecycle/test_entry_gate.py -k "update_chain_result" -v
```

Expected: 2 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/lifecycle/entry_gate.py tests/runtime_v2/lifecycle/test_entry_gate.py
git commit -m "feat(lifecycle): add new_plan_state_json to UpdateChainResult"
```

---

### Task 3: `_apply_market_entry_now` — cancel mode

**Files:**
- Modify: `src/runtime_v2/lifecycle/entry_gate.py` (routing at line 521, new method after line 613)
- Test: `tests/runtime_v2/lifecycle/test_entry_gate.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/runtime_v2/lifecycle/test_entry_gate.py`. First add two helpers at module level (after the existing helpers, before the first test):

```python
def _make_gate_attached():
    from src.runtime_v2.lifecycle.entry_gate import LifecycleEntryGate
    from src.runtime_v2.lifecycle.risk_capacity import RiskCapacityEngine
    from src.runtime_v2.lifecycle.static_exchange_data_port import StaticExchangeDataPort
    return LifecycleEntryGate(
        risk_engine=RiskCapacityEngine(),
        exchange_port=StaticExchangeDataPort(),
        simple_attached_enabled=True,
    )


def _make_two_step_chain_for_market(
    market_convert_mode: str = "cancel_subsequent",
    risk_remaining: float = 0.0,
):
    import json
    from src.runtime_v2.lifecycle.models import TradeChain
    from src.runtime_v2.signal_enrichment.models import ManagementPlanConfig
    plan = json.dumps({
        "plan_version": 1,
        "protection_policy": "TPSL_ATTACHED_FIRST_LEG",
        "rebuild_policy": "NONE",
        "risk_policy": "REBALANCE_REMAINING_RISK_ON_REPLAN",
        "stop_loss": 800.0,
        "final_tp": 1200.0,
        "intermediate_tps": [],
        "legs": [
            {
                "leg_id": "leg_1", "sequence": 1, "entry_type": "LIMIT", "price": 1000.0,
                "risk_budget": 70.0, "qty": 0.35, "qty_mode": "fixed", "weight": 0.7,
                "status": "PENDING", "client_order_id": "place_entry_attached:5:leg1",
            },
            {
                "leg_id": "leg_2", "sequence": 2, "entry_type": "LIMIT", "price": 900.0,
                "risk_budget": 30.0, "qty": 0.15, "qty_mode": "fixed", "weight": 0.3,
                "status": "PENDING", "client_order_id": "place_entry:5:leg2",
            },
        ],
    })
    risk_snap = json.dumps({
        "risk_amount": 100.0,
        "sl_price": 800.0,
        "entry_price": 1000.0,
        "leverage": 1,
        "hedge_mode": False,
        "legs": [
            {"sequence": 1, "risk_amount": 70.0, "qty": 0.35, "qty_mode": "fixed", "weight": 0.7},
            {"sequence": 2, "risk_amount": 30.0, "qty": 0.15, "qty_mode": "fixed", "weight": 0.3},
        ],
    })
    mp = ManagementPlanConfig(market_convert_mode=market_convert_mode)
    return TradeChain(
        trade_chain_id=1,
        source_enrichment_id=5, canonical_message_id=50, raw_message_id=500,
        trader_id="t1", account_id="acc_1",
        symbol="TOKEN/USDT:USDT", side="LONG",
        lifecycle_state="WAITING_ENTRY", entry_mode="TWO_STEP",
        expected_stop_price=800.0,
        management_plan_json=mp.model_dump_json(),
        risk_snapshot_json=risk_snap,
        plan_state_json=plan,
        risk_remaining=risk_remaining,
    )


def _make_market_now_update_enriched(canonical_message_id: int = 200):
    import json
    from src.parser_v2.contracts.canonical_message import (
        ActionItem, ModifyEntriesOperation, TargetActionGroup,
    )
    from src.parser_v2.contracts.context import TargetHints
    from src.runtime_v2.signal_enrichment.models import (
        ManagementPlanConfig, EnrichedCanonicalMessage,
    )
    action = ActionItem(
        action_type="MODIFY_ENTRIES",
        modify_entries=ModifyEntriesOperation(kind="MARKET_NOW", entries=[]),
        source_intent="MODIFY_ENTRY",
    )
    tag = TargetActionGroup(
        targeting=TargetHints(scope_hint="SINGLE_SIGNAL"),
        actions=[action],
    )
    return EnrichedCanonicalMessage(
        enrichment_id=20, canonical_message_id=canonical_message_id,
        raw_message_id=200, trader_id="t1", account_id="acc_1",
        primary_class="UPDATE", enrichment_decision="PASS",
        enriched_signal=None, enriched_actions=[tag],
        management_plan=ManagementPlanConfig(), policy_snapshot={},
    )
```

Now the cancel-mode tests:

```python
def test_market_entry_now_cancel_mode_produces_two_cancels_and_one_market_entry():
    import json
    gate = _make_gate_attached()
    chain = _make_two_step_chain_for_market("cancel_subsequent")
    enriched = _make_market_now_update_enriched()
    result = gate.process_update(enriched, [chain], {1: []})

    assert len(result.chain_results) == 1
    cr = result.chain_results[0]
    cmd_types = [c.command_type for c in cr.execution_commands]
    assert cmd_types.count("CANCEL_PENDING_ENTRY") == 2
    assert cmd_types.count("PLACE_ENTRY_WITH_ATTACHED_TPSL") == 1


def test_market_entry_now_cancel_mode_market_command_uses_full_risk_deferred():
    import json
    gate = _make_gate_attached()
    chain = _make_two_step_chain_for_market("cancel_subsequent")
    enriched = _make_market_now_update_enriched()
    result = gate.process_update(enriched, [chain], {1: []})

    cr = result.chain_results[0]
    market_cmd = next(
        c for c in cr.execution_commands
        if c.command_type == "PLACE_ENTRY_WITH_ATTACHED_TPSL"
    )
    p = json.loads(market_cmd.payload_json)
    assert p["entry_type"] == "MARKET"
    assert p.get("qty_mode") == "deferred_market"
    assert p.get("risk_amount") == pytest.approx(100.0)  # full risk_amount from snap


def test_market_entry_now_cancel_mode_plan_marks_leg1_market_leg2_cancelled():
    import json
    gate = _make_gate_attached()
    chain = _make_two_step_chain_for_market("cancel_subsequent")
    enriched = _make_market_now_update_enriched()
    result = gate.process_update(enriched, [chain], {1: []})

    cr = result.chain_results[0]
    assert cr.new_plan_state_json is not None
    plan = json.loads(cr.new_plan_state_json)
    by_seq = {l["sequence"]: l for l in plan["legs"]}
    assert by_seq[1]["entry_type"] == "MARKET"
    assert by_seq[1]["status"] == "PENDING"
    assert by_seq[1]["qty_mode"] == "deferred_market"
    assert by_seq[2]["status"] == "CANCELLED"


def test_market_entry_now_cancel_mode_emits_telegram_update_accepted_event():
    gate = _make_gate_attached()
    chain = _make_two_step_chain_for_market("cancel_subsequent")
    enriched = _make_market_now_update_enriched()
    result = gate.process_update(enriched, [chain], {1: []})

    cr = result.chain_results[0]
    event_types = [e.event_type for e in cr.lifecycle_events]
    assert "TELEGRAM_UPDATE_ACCEPTED" in event_types
```

- [ ] **Step 2: Run to confirm failure**

```
pytest tests/runtime_v2/lifecycle/test_entry_gate.py -k "market_entry_now_cancel" -v
```

Expected: `AssertionError` — no `_apply_market_entry_now` yet; the old path processes `MARKET_NOW` with empty entries and produces nothing meaningful.

- [ ] **Step 3: Add routing in `_apply_action_to_chain`**

In `src/runtime_v2/lifecycle/entry_gate.py`, find the block at line 521 and replace:

```python
        if action_type == "MODIFY_ENTRIES":
            op = action.modify_entries
            if op and op.kind in {"MARKET_NOW", "UPDATE_PRICE", "REPLACE_ENTRY"}:
                return self._apply_modify_entries(enriched, chain, action, active_commands)
            return self._review_chain(enriched, chain, "unsupported_modify_entries_kind")
```

with:

```python
        if action_type == "MODIFY_ENTRIES":
            op = action.modify_entries
            if op and op.kind == "MARKET_NOW" and not op.entries:
                return self._apply_market_entry_now(enriched, chain, active_commands)
            if op and op.kind in {"MARKET_NOW", "UPDATE_PRICE", "REPLACE_ENTRY"}:
                return self._apply_modify_entries(enriched, chain, action, active_commands)
            return self._review_chain(enriched, chain, "unsupported_modify_entries_kind")
```

- [ ] **Step 4: Implement `_apply_market_entry_now`**

Add the following method to `LifecycleEntryGate`, immediately after `_apply_modify_entries` (after line 613):

```python
    def _apply_market_entry_now(
        self,
        enriched: EnrichedCanonicalMessage,
        chain: TradeChain,
        active_commands: list[ExecutionCommand],
    ) -> UpdateChainResult:
        from src.runtime_v2.lifecycle.entry_command_factory import EntryCommandFactory
        from src.runtime_v2.signal_enrichment.models import EnrichedEntryLeg, ManagementPlanConfig
        from src.parser_v2.contracts.entities import Price as _Price, TakeProfit as _TakeProfit

        chain_id = chain.trade_chain_id
        cmid = enriched.canonical_message_id

        try:
            plan = json.loads(chain.plan_state_json or "{}")
            risk_snap = json.loads(chain.risk_snapshot_json or "{}")
        except Exception:
            return self._review_chain(enriched, chain, "market_entry_now_invalid_json")

        pending_legs = [l for l in plan.get("legs", []) if l.get("status") == "PENDING"]
        if not pending_legs:
            return self._review_chain(enriched, chain, "no_pending_legs_for_market_convert")

        try:
            mp = ManagementPlanConfig.model_validate_json(chain.management_plan_json)
        except Exception:
            mp = ManagementPlanConfig()
        mode = mp.market_convert_mode

        leg1 = min(pending_legs, key=lambda l: l["sequence"])
        others = [l for l in pending_legs if l["sequence"] != leg1["sequence"]]

        sl_price_raw = risk_snap.get("sl_price", chain.expected_stop_price)
        if sl_price_raw is None:
            return self._review_chain(enriched, chain, "market_entry_now_missing_sl_price")
        sl_price = float(sl_price_raw)

        risk_total = float(risk_snap.get("risk_amount", 0.0) or 0.0)
        risk_remaining = (
            chain.risk_remaining
            if chain.risk_remaining > 0
            else max(0.0, risk_total - chain.risk_already_realized)
        )

        if mode == "cancel_subsequent":
            risk_amount = risk_remaining
        else:
            leg1_snap = next(
                (s for s in risk_snap.get("legs", []) if s.get("sequence") == leg1["sequence"]),
                {},
            )
            risk_amount = float(leg1_snap.get("risk_amount") or risk_remaining)

        commands: list[ExecutionCommand] = []

        # Cancel existing leg1 LIMIT on exchange
        commands.append(ExecutionCommand(
            trade_chain_id=chain_id,
            command_type="CANCEL_PENDING_ENTRY",
            payload_json=json.dumps({
                "symbol": chain.symbol,
                "side": chain.side,
                "entry_client_order_id": leg1.get("client_order_id"),
            }),
            idempotency_key=f"cancel_entry:{chain_id}:{cmid}:seq{leg1['sequence']}",
        ))

        # Build replacement MARKET command via EntryCommandFactory
        hedge_mode = bool(risk_snap.get("hedge_mode", False))
        leverage = int(risk_snap.get("leverage", 1) or 1)
        position_idx = self.resolve_position_idx(chain.side, hedge_mode)
        is_leg1_attached = leg1["sequence"] == 1

        final_tp_val = plan.get("final_tp")
        tp_list: list[_TakeProfit] = []
        if final_tp_val is not None and is_leg1_attached:
            tp_price = _Price(raw=str(final_tp_val), value=float(final_tp_val))
            tp_list = [_TakeProfit(sequence=1, price=tp_price)]

        replacement_leg = EnrichedEntryLeg(
            sequence=leg1["sequence"],
            entry_type="MARKET",
            price=None,
            weight=float(leg1.get("weight") or 1.0),
        )
        replacement_snap = {
            "sequence": leg1["sequence"],
            "qty": None,
            "qty_mode": "deferred_market",
            "risk_amount": risk_amount,
            "weight": float(leg1.get("weight") or 1.0),
        }
        market_commands = EntryCommandFactory().build_entry_commands(
            enrichment_id=cmid,
            symbol=chain.symbol,
            side=chain.side,
            entries=[replacement_leg],
            take_profits=tp_list,
            sl_price=sl_price,
            leverage=leverage,
            hedge_mode=hedge_mode,
            position_idx=position_idx,
            risk_snapshot={"legs": [replacement_snap]},
        )
        commands.extend(market_commands)

        # Cancel subsequent legs (cancel mode only)
        if mode == "cancel_subsequent":
            for leg in others:
                commands.append(ExecutionCommand(
                    trade_chain_id=chain_id,
                    command_type="CANCEL_PENDING_ENTRY",
                    payload_json=json.dumps({
                        "symbol": chain.symbol,
                        "side": chain.side,
                        "entry_client_order_id": leg.get("client_order_id"),
                    }),
                    idempotency_key=f"cancel_entry:{chain_id}:{cmid}:seq{leg['sequence']}",
                ))

        # Build updated plan_state_json
        if is_leg1_attached:
            new_leg1_coid = f"place_entry_attached:{cmid}:leg{leg1['sequence']}"
        else:
            new_leg1_coid = f"place_entry:{cmid}:leg{leg1['sequence']}"

        other_seqs_to_cancel = {l["sequence"] for l in others} if mode == "cancel_subsequent" else set()
        updated_legs = []
        for leg in plan.get("legs", []):
            if leg["sequence"] == leg1["sequence"]:
                updated_legs.append({
                    **leg,
                    "entry_type": "MARKET",
                    "price": None,
                    "qty": None,
                    "qty_mode": "deferred_market",
                    "status": "PENDING",
                    "client_order_id": new_leg1_coid,
                })
            elif leg["sequence"] in other_seqs_to_cancel:
                updated_legs.append({**leg, "status": "CANCELLED"})
            else:
                updated_legs.append(leg)
        new_plan_state_json = json.dumps({**plan, "legs": updated_legs})

        event = LifecycleEvent(
            trade_chain_id=chain_id,
            event_type="TELEGRAM_UPDATE_ACCEPTED",
            source_type="telegram_update",
            source_id=str(cmid),
            payload_json=json.dumps({"action": "MARKET_ENTRY_NOW", "mode": mode}),
            idempotency_key=f"update_market_entry_now:{chain_id}:{cmid}",
        )
        return UpdateChainResult(
            trade_chain_id=chain_id,
            new_lifecycle_state=None,
            new_be_protection_status=None,
            lifecycle_events=[event],
            execution_commands=commands,
            new_plan_state_json=new_plan_state_json,
        )
```

- [ ] **Step 5: Run cancel-mode tests**

```
pytest tests/runtime_v2/lifecycle/test_entry_gate.py -k "market_entry_now_cancel" -v
```

Expected: 4 tests PASS.

- [ ] **Step 6: Run full suite to check no regressions**

```
pytest tests/runtime_v2/lifecycle/test_entry_gate.py -v
```

Expected: all previously passing tests still PASS.

- [ ] **Step 7: Commit**

```bash
git add src/runtime_v2/lifecycle/entry_gate.py tests/runtime_v2/lifecycle/test_entry_gate.py
git commit -m "feat(lifecycle): implement _apply_market_entry_now cancel mode"
```

---

### Task 4: `_apply_market_entry_now` — keep mode

**Files:**
- Test: `tests/runtime_v2/lifecycle/test_entry_gate.py` (new tests only — implementation already in place from Task 3)

- [ ] **Step 1: Write the failing tests**

Append to `tests/runtime_v2/lifecycle/test_entry_gate.py`:

```python
def test_market_entry_now_keep_mode_produces_one_cancel_and_one_market_entry():
    import json
    gate = _make_gate_attached()
    chain = _make_two_step_chain_for_market("keep_subsequent")
    enriched = _make_market_now_update_enriched()
    result = gate.process_update(enriched, [chain], {1: []})

    assert len(result.chain_results) == 1
    cr = result.chain_results[0]
    cmd_types = [c.command_type for c in cr.execution_commands]
    assert cmd_types.count("CANCEL_PENDING_ENTRY") == 1  # leg1 only
    assert cmd_types.count("PLACE_ENTRY_WITH_ATTACHED_TPSL") == 1


def test_market_entry_now_keep_mode_uses_leg1_risk_only():
    import json
    gate = _make_gate_attached()
    chain = _make_two_step_chain_for_market("keep_subsequent")
    enriched = _make_market_now_update_enriched()
    result = gate.process_update(enriched, [chain], {1: []})

    cr = result.chain_results[0]
    market_cmd = next(
        c for c in cr.execution_commands
        if c.command_type == "PLACE_ENTRY_WITH_ATTACHED_TPSL"
    )
    p = json.loads(market_cmd.payload_json)
    assert p["entry_type"] == "MARKET"
    assert p.get("qty_mode") == "deferred_market"
    assert p.get("risk_amount") == pytest.approx(70.0)  # leg1 risk_amount only


def test_market_entry_now_keep_mode_plan_leg1_market_leg2_pending_unchanged():
    import json
    gate = _make_gate_attached()
    chain = _make_two_step_chain_for_market("keep_subsequent")
    enriched = _make_market_now_update_enriched()
    result = gate.process_update(enriched, [chain], {1: []})

    cr = result.chain_results[0]
    assert cr.new_plan_state_json is not None
    plan = json.loads(cr.new_plan_state_json)
    by_seq = {l["sequence"]: l for l in plan["legs"]}
    assert by_seq[1]["entry_type"] == "MARKET"
    assert by_seq[1]["status"] == "PENDING"
    assert by_seq[1]["qty_mode"] == "deferred_market"
    # leg2 completely untouched
    assert by_seq[2]["status"] == "PENDING"
    assert by_seq[2]["entry_type"] == "LIMIT"
    assert by_seq[2]["price"] == pytest.approx(900.0)
```

- [ ] **Step 2: Run to confirm they pass immediately** (implementation already handles keep mode)

```
pytest tests/runtime_v2/lifecycle/test_entry_gate.py -k "market_entry_now_keep" -v
```

Expected: 3 tests PASS. If any fail, fix `_apply_market_entry_now` from Task 3 before continuing.

- [ ] **Step 3: Commit**

```bash
git add tests/runtime_v2/lifecycle/test_entry_gate.py
git commit -m "test(lifecycle): add keep mode tests for _apply_market_entry_now"
```

---

### Task 5: Edge cases

**Files:**
- Test: `tests/runtime_v2/lifecycle/test_entry_gate.py`

- [ ] **Step 1: Write the edge case tests**

Append to `tests/runtime_v2/lifecycle/test_entry_gate.py`:

```python
def test_market_entry_now_no_pending_legs_returns_review():
    import json
    from src.runtime_v2.lifecycle.entry_gate import LifecycleEntryGate
    from src.runtime_v2.lifecycle.risk_capacity import RiskCapacityEngine
    from src.runtime_v2.lifecycle.models import TradeChain
    from src.runtime_v2.signal_enrichment.models import ManagementPlanConfig
    # All legs already FILLED
    plan = json.dumps({"legs": [
        {"leg_id": "leg_1", "sequence": 1, "entry_type": "LIMIT", "price": 1000.0,
         "risk_budget": 100.0, "qty": 0.5, "qty_mode": "fixed", "weight": 1.0,
         "status": "FILLED", "client_order_id": "place_entry_attached:5:leg1"},
    ]})
    risk_snap = json.dumps({"risk_amount": 100.0, "sl_price": 800.0, "entry_price": 1000.0,
                             "leverage": 1, "hedge_mode": False, "legs": []})
    chain = TradeChain(
        trade_chain_id=1,
        source_enrichment_id=5, canonical_message_id=50, raw_message_id=500,
        trader_id="t1", account_id="acc_1", symbol="TOKEN/USDT:USDT", side="LONG",
        lifecycle_state="OPEN", entry_mode="ONE_SHOT",
        management_plan_json=ManagementPlanConfig().model_dump_json(),
        risk_snapshot_json=risk_snap, plan_state_json=plan,
    )
    gate = _make_gate_attached()
    enriched = _make_market_now_update_enriched()
    result = gate.process_update(enriched, [chain], {1: []})
    cr = result.chain_results[0]
    assert any(e.event_type == "REVIEW_REQUIRED" for e in cr.lifecycle_events)
    assert cr.new_plan_state_json is None
    assert cr.execution_commands == []


def test_market_entry_now_single_pending_leg_produces_one_cancel_one_market():
    import json
    from src.runtime_v2.lifecycle.entry_gate import LifecycleEntryGate
    from src.runtime_v2.lifecycle.risk_capacity import RiskCapacityEngine
    # ONE_SHOT: 1 leg only
    from src.runtime_v2.lifecycle.models import TradeChain
    from src.runtime_v2.signal_enrichment.models import ManagementPlanConfig
    plan = json.dumps({
        "plan_version": 1, "protection_policy": "TPSL_ATTACHED_FIRST_LEG",
        "rebuild_policy": "NONE", "risk_policy": "REBALANCE_REMAINING_RISK_ON_REPLAN",
        "stop_loss": 800.0, "final_tp": 1200.0, "intermediate_tps": [],
        "legs": [
            {"leg_id": "leg_1", "sequence": 1, "entry_type": "LIMIT", "price": 1000.0,
             "risk_budget": 100.0, "qty": 0.5, "qty_mode": "fixed", "weight": 1.0,
             "status": "PENDING", "client_order_id": "place_entry_attached:5:leg1"},
        ],
    })
    risk_snap = json.dumps({
        "risk_amount": 100.0, "sl_price": 800.0, "entry_price": 1000.0,
        "leverage": 1, "hedge_mode": False,
        "legs": [{"sequence": 1, "risk_amount": 100.0, "qty": 0.5, "qty_mode": "fixed", "weight": 1.0}],
    })
    # Test with cancel mode
    chain_cancel = TradeChain(
        trade_chain_id=1,
        source_enrichment_id=5, canonical_message_id=50, raw_message_id=500,
        trader_id="t1", account_id="acc_1", symbol="TOKEN/USDT:USDT", side="LONG",
        lifecycle_state="WAITING_ENTRY", entry_mode="ONE_SHOT",
        management_plan_json=ManagementPlanConfig(market_convert_mode="cancel_subsequent").model_dump_json(),
        risk_snapshot_json=risk_snap, plan_state_json=plan,
    )
    gate = _make_gate_attached()
    enriched = _make_market_now_update_enriched()
    result_cancel = gate.process_update(enriched, [chain_cancel], {1: []})
    cr_cancel = result_cancel.chain_results[0]
    cmd_types = [c.command_type for c in cr_cancel.execution_commands]
    assert cmd_types.count("CANCEL_PENDING_ENTRY") == 1
    assert cmd_types.count("PLACE_ENTRY_WITH_ATTACHED_TPSL") == 1

    # Test with keep mode — same result (no "others" to keep)
    chain_keep = chain_cancel.model_copy(update={
        "management_plan_json": ManagementPlanConfig(market_convert_mode="keep_subsequent").model_dump_json()
    })
    result_keep = gate.process_update(enriched, [chain_keep], {1: []})
    cr_keep = result_keep.chain_results[0]
    cmd_types_keep = [c.command_type for c in cr_keep.execution_commands]
    assert cmd_types_keep.count("CANCEL_PENDING_ENTRY") == 1
    assert cmd_types_keep.count("PLACE_ENTRY_WITH_ATTACHED_TPSL") == 1
```

- [ ] **Step 2: Run edge case tests**

```
pytest tests/runtime_v2/lifecycle/test_entry_gate.py -k "market_entry_now_no_pending or market_entry_now_single" -v
```

Expected: 2 tests PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/runtime_v2/lifecycle/test_entry_gate.py
git commit -m "test(lifecycle): add edge case tests for _apply_market_entry_now"
```

---

### Task 6: Fix `_persist_update` — save `new_plan_state_json` to DB

**Files:**
- Modify: `src/runtime_v2/lifecycle/entry_gate.py:1349-1416` (`_persist_update` method)
- Test: `tests/runtime_v2/lifecycle/test_entry_gate.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/runtime_v2/lifecycle/test_entry_gate.py`:

```python
def test_persist_update_saves_new_plan_state_json_to_db(tmp_path):
    import json
    import sqlite3
    from pathlib import Path
    from src.runtime_v2.lifecycle.entry_gate import LifecycleEntryGate, UpdateGateResult, UpdateChainResult
    from src.runtime_v2.lifecycle.risk_capacity import RiskCapacityEngine
    from src.runtime_v2.lifecycle.models import LifecycleEvent
    from src.runtime_v2.signal_enrichment.models import ManagementPlanConfig, EnrichedCanonicalMessage

    # Bootstrap DB
    db_path = str(tmp_path / "ops.sqlite3")
    conn = sqlite3.connect(db_path)
    for f in sorted(Path("db/migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit()
    # Insert a minimal chain
    original_plan = json.dumps({"legs": [{"sequence": 1, "status": "PENDING"}]})
    conn.execute(
        """INSERT INTO ops_trade_chains (
            source_enrichment_id, canonical_message_id, raw_message_id,
            trader_id, account_id, symbol, side, lifecycle_state, entry_mode,
            management_plan_json, plan_state_json, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))""",
        (1, 1, 1, "t1", "acc_1", "SYM", "LONG", "WAITING_ENTRY", "ONE_SHOT", "{}", original_plan),
    )
    conn.commit()
    chain_id = conn.execute("SELECT trade_chain_id FROM ops_trade_chains LIMIT 1").fetchone()[0]
    conn.close()

    new_plan = json.dumps({"legs": [{"sequence": 1, "status": "FILLED"}]})
    cr = UpdateChainResult(
        trade_chain_id=chain_id,
        new_lifecycle_state=None,
        new_be_protection_status=None,
        lifecycle_events=[LifecycleEvent(
            trade_chain_id=chain_id, event_type="TELEGRAM_UPDATE_ACCEPTED",
            source_type="test", idempotency_key="test:persist:1",
        )],
        execution_commands=[],
        new_plan_state_json=new_plan,
    )

    from src.runtime_v2.signal_enrichment.models import EnrichedCanonicalMessage
    enriched = EnrichedCanonicalMessage(
        enrichment_id=99, canonical_message_id=99, raw_message_id=99,
        trader_id="t1", account_id="acc_1", primary_class="UPDATE",
        enrichment_decision="PASS", policy_snapshot={},
    )

    # Build minimal gate worker with only the DB path wired
    from src.runtime_v2.lifecycle.repositories import (
        TradeChainRepository, LifecycleEventRepository,
        ExecutionCommandRepository,
    )
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository
    # We call _persist_update directly — it only needs ops_db_path
    gate = LifecycleEntryGate(
        risk_engine=RiskCapacityEngine(),
        exchange_port=_make_port(),
        simple_attached_enabled=True,
    )
    # Inject ops_db_path via the worker
    from src.runtime_v2.lifecycle.entry_gate import LifecycleGateWorker
    worker = LifecycleGateWorker(
        parser_db_path=db_path, ops_db_path=db_path,
        gate=gate, chain_repo=None, event_repo=None,
        command_repo=None, snapshot_repo=None, control_repo=None,
    )
    worker._persist_update(enriched, UpdateGateResult(chain_results=[cr], review_events=[]))

    # Verify plan was saved
    conn2 = sqlite3.connect(db_path)
    row = conn2.execute(
        "SELECT plan_state_json FROM ops_trade_chains WHERE trade_chain_id=?", (chain_id,)
    ).fetchone()
    conn2.close()
    saved_plan = json.loads(row[0])
    assert saved_plan["legs"][0]["status"] == "FILLED"
```

- [ ] **Step 2: Run to confirm failure**

```
pytest tests/runtime_v2/lifecycle/test_entry_gate.py -k "persist_update_saves_new_plan" -v
```

Expected: FAIL — plan in DB still has `PENDING` (persist doesn't save it yet).

- [ ] **Step 3: Fix `_persist_update`**

In `src/runtime_v2/lifecycle/entry_gate.py`, find `_persist_update` (around line 1349). Replace the update block:

```python
                for cr in result.chain_results:
                    if cr.new_lifecycle_state or cr.new_be_protection_status:
                        fields = ["updated_at=?"]
                        vals: list = [now]
                        if cr.new_lifecycle_state:
                            fields.append("lifecycle_state=?")
                            vals.append(cr.new_lifecycle_state)
                        if cr.new_be_protection_status:
                            fields.append("be_protection_status=?")
                            vals.append(cr.new_be_protection_status)
                        vals.append(cr.trade_chain_id)
                        conn.execute(
                            f"UPDATE ops_trade_chains SET {', '.join(fields)} WHERE trade_chain_id=?",
                            vals,
                        )
```

with:

```python
                for cr in result.chain_results:
                    if cr.new_lifecycle_state or cr.new_be_protection_status or cr.new_plan_state_json is not None:
                        fields = ["updated_at=?"]
                        vals: list = [now]
                        if cr.new_lifecycle_state:
                            fields.append("lifecycle_state=?")
                            vals.append(cr.new_lifecycle_state)
                        if cr.new_be_protection_status:
                            fields.append("be_protection_status=?")
                            vals.append(cr.new_be_protection_status)
                        if cr.new_plan_state_json is not None:
                            fields.append("plan_state_json=?")
                            vals.append(cr.new_plan_state_json)
                        vals.append(cr.trade_chain_id)
                        conn.execute(
                            f"UPDATE ops_trade_chains SET {', '.join(fields)} WHERE trade_chain_id=?",
                            vals,
                        )
```

- [ ] **Step 4: Run the test**

```
pytest tests/runtime_v2/lifecycle/test_entry_gate.py -k "persist_update_saves_new_plan" -v
```

Expected: PASS.

- [ ] **Step 5: Run full test suite**

```
pytest tests/runtime_v2/ -v --tb=short
```

Expected: all tests pass. Fix any failures before continuing.

- [ ] **Step 6: Commit**

```bash
git add src/runtime_v2/lifecycle/entry_gate.py tests/runtime_v2/lifecycle/test_entry_gate.py
git commit -m "fix(lifecycle): persist new_plan_state_json in _persist_update"
```

---

### Task 7: Final integration smoke test

**Files:**
- Test: `tests/runtime_v2/lifecycle/test_entry_gate.py`

- [ ] **Step 1: Write smoke test for full round-trip**

Append to `tests/runtime_v2/lifecycle/test_entry_gate.py`:

```python
def test_market_entry_now_cancel_mode_full_roundtrip(tmp_path):
    """cancel mode: market order placed + leg2 cancelled + plan updated in DB."""
    import json
    import sqlite3
    from pathlib import Path
    from src.runtime_v2.lifecycle.entry_gate import LifecycleEntryGate, LifecycleGateWorker
    from src.runtime_v2.lifecycle.risk_capacity import RiskCapacityEngine

    db_path = str(tmp_path / "ops.sqlite3")
    conn = sqlite3.connect(db_path)
    for f in sorted(Path("db/migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()

    chain = _make_two_step_chain_for_market("cancel_subsequent")
    gate = _make_gate_attached()
    enriched = _make_market_now_update_enriched(canonical_message_id=300)
    result = gate.process_update(enriched, [chain], {1: []})

    cr = result.chain_results[0]
    # Commands: 2 cancels + 1 MARKET entry
    cmd_types = [c.command_type for c in cr.execution_commands]
    assert cmd_types.count("CANCEL_PENDING_ENTRY") == 2
    assert cmd_types.count("PLACE_ENTRY_WITH_ATTACHED_TPSL") == 1
    # Plan in result
    plan = json.loads(cr.new_plan_state_json)
    by_seq = {l["sequence"]: l for l in plan["legs"]}
    assert by_seq[1]["entry_type"] == "MARKET"
    assert by_seq[2]["status"] == "CANCELLED"
    # Event
    assert any(e.event_type == "TELEGRAM_UPDATE_ACCEPTED" for e in cr.lifecycle_events)
```

- [ ] **Step 2: Run smoke test**

```
pytest tests/runtime_v2/lifecycle/test_entry_gate.py -k "full_roundtrip" -v
```

Expected: PASS.

- [ ] **Step 3: Run complete test suite one final time**

```
pytest tests/runtime_v2/ -v --tb=short
```

Expected: all tests pass.

- [ ] **Step 4: Final commit**

```bash
git add tests/runtime_v2/lifecycle/test_entry_gate.py
git commit -m "test(lifecycle): add market_entry_now full roundtrip smoke test"
```
