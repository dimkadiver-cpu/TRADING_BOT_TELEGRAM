# Unified Execution Plan With Risk Replan Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace four separate entry-command builders with a single `ExecutionPlan`-driven pipeline that handles all 8 entry/TP combinations under one unified rule, adds `plan_state_json` as the authoritative diff source, and introduces `ExecutionPlanDiffEngine` for entry-changing updates.

**Architecture:** Five sequential phases. Phase 1 adds `ExecutionPlan` model + shadow `plan_state_json` with no behavioral change. Phase 2 replaces the 4 builder methods with `EntryCommandFactory` (unified rule: leg-1 → FULL attached, legs-2+ → plain entry). Phase 3 extracts `PostFillProtectionRebuilder` from `event_processor`. Phase 4 adds risk tracking + `ExecutionPlanDiffEngine` for entry-changing updates. Phase 5 removes legacy mode routing.

**Tech Stack:** Python 3.12+, Pydantic v2, SQLite (via sqlite3), pytest

---

## File Map

```
PHASE 1 — Shadow plan state
  Create: src/runtime_v2/lifecycle/execution_plan.py
  Create: db/ops_migrations/004_ops_plan_state.sql
  Modify: src/runtime_v2/lifecycle/models.py          (add plan_state_json to TradeChain)
  Modify: src/runtime_v2/lifecycle/repositories.py    (add column to SQL)
  Modify: src/runtime_v2/lifecycle/entry_gate.py      (populate plan_state_json shadow)
  Create: tests/runtime_v2/lifecycle/test_execution_plan.py

PHASE 2 — Unified entry command factory
  Create: src/runtime_v2/lifecycle/entry_command_factory.py
  Create: tests/runtime_v2/lifecycle/test_entry_command_factory.py
  Modify: src/runtime_v2/lifecycle/entry_gate.py      (replace 4 builders)
  Modify: tests/runtime_v2/lifecycle/test_entry_gate.py

PHASE 3 — Post-fill TP rebuilder extraction
  Create: src/runtime_v2/lifecycle/post_fill_rebuilder.py
  Create: tests/runtime_v2/lifecycle/test_post_fill_rebuilder.py
  Modify: src/runtime_v2/lifecycle/event_processor.py (use rebuilder, trigger by plan_state_json)
  Modify: tests/runtime_v2/lifecycle/test_event_processor.py

PHASE 4 — Risk tracking + diff engine
  Create: db/ops_migrations/005_ops_risk_tracking.sql
  Modify: src/runtime_v2/lifecycle/models.py          (add risk_already_realized, risk_remaining)
  Modify: src/runtime_v2/lifecycle/repositories.py    (add columns)
  Create: src/runtime_v2/lifecycle/diff_engine.py
  Create: tests/runtime_v2/lifecycle/test_diff_engine.py
  Modify: src/runtime_v2/lifecycle/event_processor.py (update risk_already_realized on fill)
  Modify: src/runtime_v2/lifecycle/entry_gate.py      (entry-changing updates via diff engine)

PHASE 5 — Remove legacy routing
  Modify: src/runtime_v2/lifecycle/entry_gate.py      (remove legacy mode methods)
  Modify: src/runtime_v2/lifecycle/event_processor.py (remove execution_mode branch)
  Modify: tests/runtime_v2/lifecycle/test_entry_gate.py
```

---

## Phase 1 — Shadow plan state

### Task 1: ExecutionPlan model

**Files:**
- Create: `src/runtime_v2/lifecycle/execution_plan.py`
- Create: `tests/runtime_v2/lifecycle/test_execution_plan.py`

- [x] **Step 1.1: Write the tests first**

```python
# tests/runtime_v2/lifecycle/test_execution_plan.py
from __future__ import annotations
import json
import pytest


def _make_risk_snap(
    *,
    sl_price: float = 49000.0,
    tp_prices: list[float] | None = None,
    legs: list[dict] | None = None,
) -> dict:
    if tp_prices is None:
        tp_prices = [51000.0]
    if legs is None:
        legs = [{
            "sequence": 1, "entry_type": "LIMIT",
            "price": 50000.0, "risk_amount": 100.0, "qty": 0.02,
            "qty_mode": "fixed", "weight": 1.0,
        }]
    return {"sl_price": sl_price, "tp_rebuild": {}, "legs": legs}


def _make_tp(sequence: int, price: float):
    from src.parser_v2.contracts.entities import Price, TakeProfit
    return TakeProfit(sequence=sequence, price=Price(raw=str(price), value=price))


def _make_entries(specs: list[tuple[int, str, float | None, float]]):
    from src.parser_v2.contracts.entities import Price
    from src.runtime_v2.signal_enrichment.models import EnrichedEntryLeg
    result = []
    for seq, etype, price, weight in specs:
        p = Price(raw=str(price), value=price) if price is not None else None
        result.append(EnrichedEntryLeg(sequence=seq, entry_type=etype, price=p, weight=weight))
    return result


def _build(enrichment_id: int, entries, tps, risk_snap: dict) -> dict:
    from src.runtime_v2.lifecycle.execution_plan import ExecutionPlanBuilder
    plan_json = ExecutionPlanBuilder.build(enrichment_id, entries, tps, risk_snap)
    return json.loads(plan_json)


# ── Case 1a: 1 LIMIT + 1 TP → rebuild_policy=NONE ────────────────────────────
def test_case_1a_single_limit_single_tp():
    entries = _make_entries([(1, "LIMIT", 50000.0, 1.0)])
    tps = [_make_tp(1, 51000.0)]
    risk_snap = _make_risk_snap(sl_price=49000.0, tp_prices=[51000.0])
    plan = _build(1, entries, tps, risk_snap)

    assert plan["plan_version"] == 1
    assert plan["protection_policy"] == "TPSL_ATTACHED_FIRST_LEG"
    assert plan["rebuild_policy"] == "NONE"
    assert plan["final_tp"] == 51000.0
    assert plan["intermediate_tps"] == []
    assert plan["stop_loss"] == 49000.0
    assert len(plan["legs"]) == 1
    leg = plan["legs"][0]
    assert leg["sequence"] == 1
    assert leg["entry_type"] == "LIMIT"
    assert leg["status"] == "PENDING"
    assert leg["client_order_id"] == "place_entry_attached:1:leg1"


# ── Case 1b: 1 LIMIT + N TP → rebuild_policy=ON_EACH_ENTRY_FILL ──────────────
def test_case_1b_single_limit_multi_tp():
    entries = _make_entries([(1, "LIMIT", 50000.0, 1.0)])
    tps = [_make_tp(1, 51000.0), _make_tp(2, 52000.0)]
    risk_snap = _make_risk_snap(sl_price=49000.0, tp_prices=[51000.0, 52000.0])
    plan = _build(2, entries, tps, risk_snap)

    assert plan["rebuild_policy"] == "ON_EACH_ENTRY_FILL"
    assert plan["final_tp"] == 52000.0
    assert plan["intermediate_tps"] == [51000.0]


# ── Case 2a: N LIMIT + 1 TP → leg1=attached, legs2+=plain ───────────────────
def test_case_2a_multi_limit_single_tp():
    entries = _make_entries([
        (1, "LIMIT", 50000.0, 0.5),
        (2, "LIMIT", 48000.0, 0.5),
    ])
    tps = [_make_tp(1, 51000.0)]
    legs_snap = [
        {"sequence": 1, "entry_type": "LIMIT", "price": 50000.0,
         "risk_amount": 50.0, "qty": 0.01, "qty_mode": "fixed", "weight": 0.5},
        {"sequence": 2, "entry_type": "LIMIT", "price": 48000.0,
         "risk_amount": 50.0, "qty": 0.0167, "qty_mode": "fixed", "weight": 0.5},
    ]
    risk_snap = _make_risk_snap(sl_price=49000.0, legs=legs_snap)
    plan = _build(3, entries, tps, risk_snap)

    assert plan["rebuild_policy"] == "NONE"
    assert len(plan["legs"]) == 2
    assert plan["legs"][0]["client_order_id"] == "place_entry_attached:3:leg1"
    assert plan["legs"][1]["client_order_id"] == "place_entry:3:leg2"


# ── Case 3a: 1 MARKET + 1 TP (deferred) ─────────────────────────────────────
def test_case_3a_market_deferred_single_tp():
    entries = _make_entries([(1, "MARKET", None, 1.0)])
    tps = [_make_tp(1, 51000.0)]
    legs_snap = [
        {"sequence": 1, "entry_type": "MARKET", "price": None,
         "risk_amount": 100.0, "qty": None, "qty_mode": "deferred_market", "weight": 1.0},
    ]
    risk_snap = _make_risk_snap(sl_price=49000.0, legs=legs_snap)
    plan = _build(4, entries, tps, risk_snap)

    leg = plan["legs"][0]
    assert leg["qty_mode"] == "deferred_market"
    assert leg["qty"] is None
    assert plan["rebuild_policy"] == "NONE"


# ── Final TP is always the last TP ───────────────────────────────────────────
def test_final_tp_is_last_tp():
    entries = _make_entries([(1, "LIMIT", 50000.0, 1.0)])
    tps = [_make_tp(1, 51000.0), _make_tp(2, 52000.0), _make_tp(3, 53000.0)]
    risk_snap = _make_risk_snap(sl_price=49000.0)
    plan = _build(5, entries, tps, risk_snap)

    assert plan["final_tp"] == 53000.0
    assert plan["intermediate_tps"] == [51000.0, 52000.0]
```

- [x] **Step 1.2: Run tests — expect ImportError**

```
pytest tests/runtime_v2/lifecycle/test_execution_plan.py -v
```
Expected: `ImportError: cannot import name 'ExecutionPlanBuilder'`

- [x] **Step 1.3: Implement ExecutionPlanBuilder**

```python
# src/runtime_v2/lifecycle/execution_plan.py
from __future__ import annotations

import json
from typing import Literal


LegStatus = Literal["PENDING", "FILLED", "CANCELLED"]
ProtectionPolicy = Literal["TPSL_ATTACHED_FIRST_LEG"]
RebuildPolicy = Literal["NONE", "ON_EACH_ENTRY_FILL"]
RiskPolicy = Literal["REBALANCE_REMAINING_RISK_ON_REPLAN"]


class ExecutionPlanBuilder:
    @staticmethod
    def build(
        enrichment_id: int,
        entries: list,           # list[EnrichedEntryLeg]
        take_profits: list,      # list[TakeProfit]
        risk_snapshot: dict,
    ) -> str:
        """Returns plan_state_json string for persistence."""
        sl_price: float | None = risk_snapshot.get("sl_price")
        legs_snap: list[dict] = risk_snapshot.get("legs", [])

        tp_count = len(take_profits)
        final_tp: float | None = None
        intermediate_tps: list[float] = []

        if tp_count == 1:
            if take_profits[0].price:
                final_tp = take_profits[0].price.value
            rebuild_policy: RebuildPolicy = "NONE"
        elif tp_count > 1:
            sorted_tps = sorted(take_profits, key=lambda t: t.sequence)
            if sorted_tps[-1].price:
                final_tp = sorted_tps[-1].price.value
            intermediate_tps = [
                t.price.value for t in sorted_tps[:-1] if t.price
            ]
            rebuild_policy = "ON_EACH_ENTRY_FILL"
        else:
            rebuild_policy = "NONE"

        snap_by_seq: dict[int, dict] = {
            s["sequence"]: s for s in legs_snap
        }
        leg_plans: list[dict] = []
        for leg in entries:
            snap = snap_by_seq.get(leg.sequence, {})
            is_first = leg.sequence == 1
            client_order_id = (
                f"place_entry_attached:{enrichment_id}:leg{leg.sequence}"
                if is_first
                else f"place_entry:{enrichment_id}:leg{leg.sequence}"
            )
            leg_plans.append({
                "leg_id": f"leg_{leg.sequence}",
                "sequence": leg.sequence,
                "entry_type": leg.entry_type,
                "price": leg.price.value if leg.price else None,
                "risk_budget": float(snap.get("risk_amount", 0.0)),
                "qty": snap.get("qty"),
                "qty_mode": snap.get("qty_mode", "fixed"),
                "weight": float(snap.get("weight", 0.0)),
                "status": "PENDING",
                "client_order_id": client_order_id,
            })

        plan = {
            "plan_version": 1,
            "protection_policy": "TPSL_ATTACHED_FIRST_LEG",
            "rebuild_policy": rebuild_policy,
            "risk_policy": "REBALANCE_REMAINING_RISK_ON_REPLAN",
            "stop_loss": sl_price,
            "final_tp": final_tp,
            "intermediate_tps": intermediate_tps,
            "legs": leg_plans,
        }
        return json.dumps(plan)

    @staticmethod
    def update_leg_status(
        plan_state_json: str,
        leg_id: str,
        new_status: LegStatus,
        *,
        client_order_id: str | None = None,
    ) -> str:
        plan = json.loads(plan_state_json)
        for leg in plan["legs"]:
            if leg["leg_id"] == leg_id:
                leg["status"] = new_status
                if client_order_id is not None:
                    leg["client_order_id"] = client_order_id
                break
        return json.dumps(plan)

    @staticmethod
    def get_rebuild_policy(plan_state_json: str) -> RebuildPolicy:
        try:
            plan = json.loads(plan_state_json or "{}")
            return plan.get("rebuild_policy", "NONE")
        except Exception:
            return "NONE"

    @staticmethod
    def get_pending_legs(plan_state_json: str) -> list[dict]:
        try:
            plan = json.loads(plan_state_json or "{}")
            return [l for l in plan.get("legs", []) if l["status"] == "PENDING"]
        except Exception:
            return []


__all__ = ["ExecutionPlanBuilder", "LegStatus", "ProtectionPolicy", "RebuildPolicy"]
```

- [x] **Step 1.4: Run tests — expect pass**

```
pytest tests/runtime_v2/lifecycle/test_execution_plan.py -v
```
Expected: 5 PASSED

- [ ] **Step 1.5: Commit**

```
git add src/runtime_v2/lifecycle/execution_plan.py tests/runtime_v2/lifecycle/test_execution_plan.py
git commit -m "feat(lifecycle): add ExecutionPlanBuilder with plan_state_json serialization"
```

---

### Task 2: Add plan_state_json to DB and TradeChain model

**Files:**
- Create: `db/ops_migrations/004_ops_plan_state.sql`
- Modify: `src/runtime_v2/lifecycle/models.py`
- Modify: `src/runtime_v2/lifecycle/repositories.py`

- [x] **Step 2.1: Write migration**

```sql
-- db/ops_migrations/004_ops_plan_state.sql
ALTER TABLE ops_trade_chains ADD COLUMN plan_state_json TEXT NOT NULL DEFAULT '{}';
```

- [x] **Step 2.2: Add field to TradeChain**

In `src/runtime_v2/lifecycle/models.py`, in the `TradeChain` class, add after `execution_mode`:

```python
plan_state_json: str = "{}"
```

- [x] **Step 2.3: Update repositories.py**

In `src/runtime_v2/lifecycle/repositories.py`:

Update `_CHAIN_COLS` constant — append `, plan_state_json` at the end before `created_at`:

```python
_CHAIN_COLS = (
    "trade_chain_id, source_enrichment_id, canonical_message_id, raw_message_id, "
    "trader_id, account_id, symbol, side, lifecycle_state, entry_mode, "
    "entry_avg_price, current_stop_price, expected_stop_price, be_protection_status, "
    "entry_timeout_at, management_plan_json, risk_snapshot_json, "
    "planned_entry_qty, filled_entry_qty, open_position_qty, closed_position_qty, "
    "last_position_sync_at, execution_mode, plan_state_json, created_at, updated_at"
)
```

Update `_chain_from_row()` — add `plan_state_json` to the destructuring and `TradeChain()` call:

```python
def _chain_from_row(row: tuple) -> TradeChain:
    (trade_chain_id, source_enrichment_id, canonical_message_id, raw_message_id,
     trader_id, account_id, symbol, side, lifecycle_state, entry_mode,
     entry_avg_price, current_stop_price, expected_stop_price, be_protection_status,
     entry_timeout_at, management_plan_json, risk_snapshot_json,
     planned_entry_qty, filled_entry_qty, open_position_qty, closed_position_qty,
     last_position_sync_at, execution_mode, plan_state_json, created_at, updated_at) = row
    return TradeChain(
        # ... all existing fields ...
        plan_state_json=plan_state_json or "{}",
        # ... created_at, updated_at ...
    )
```

Update `save()` in `TradeChainRepository` — add `plan_state_json` to INSERT:

```python
cursor = conn.execute(
    """
    INSERT OR IGNORE INTO ops_trade_chains (
        source_enrichment_id, canonical_message_id, raw_message_id,
        trader_id, account_id, symbol, side, lifecycle_state, entry_mode,
        entry_avg_price, current_stop_price, expected_stop_price,
        be_protection_status, entry_timeout_at, management_plan_json,
        risk_snapshot_json, planned_entry_qty, filled_entry_qty,
        open_position_qty, closed_position_qty, last_position_sync_at,
        execution_mode, plan_state_json, created_at, updated_at
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """,
    (
        chain.source_enrichment_id, chain.canonical_message_id, chain.raw_message_id,
        chain.trader_id, chain.account_id, chain.symbol, chain.side,
        chain.lifecycle_state, chain.entry_mode,
        chain.entry_avg_price, chain.current_stop_price, chain.expected_stop_price,
        chain.be_protection_status,
        chain.entry_timeout_at.isoformat() if chain.entry_timeout_at else None,
        chain.management_plan_json, chain.risk_snapshot_json,
        chain.planned_entry_qty, chain.filled_entry_qty,
        chain.open_position_qty, chain.closed_position_qty,
        chain.last_position_sync_at.isoformat() if chain.last_position_sync_at else None,
        chain.execution_mode, chain.plan_state_json, now, now,
    ),
)
```

- [x] **Step 2.4: Run existing lifecycle tests**

```
pytest tests/runtime_v2/lifecycle/ -v
```
Expected: all existing tests pass (the migration is applied in tests via `_apply_migrations`)

- [ ] **Step 2.5: Commit**

```
git add db/ops_migrations/004_ops_plan_state.sql src/runtime_v2/lifecycle/models.py src/runtime_v2/lifecycle/repositories.py
git commit -m "feat(lifecycle): add plan_state_json column to ops_trade_chains and TradeChain model"
```

---

### Task 3: Populate plan_state_json as shadow in entry_gate.py

**Files:**
- Modify: `src/runtime_v2/lifecycle/entry_gate.py`

- [x] **Step 3.1: Write failing test for plan_state_json population**

Add to `tests/runtime_v2/lifecycle/test_execution_plan.py`:

```python
def test_entry_gate_populates_plan_state_json(tmp_path):
    """Integration: signal processing must populate plan_state_json on TradeChain."""
    from pathlib import Path
    import sqlite3
    from src.runtime_v2.lifecycle.entry_gate import LifecycleEntryGate
    from src.runtime_v2.lifecycle.risk_capacity import RiskCapacityEngine

    db_path = str(tmp_path / "ops.db")
    conn = sqlite3.connect(db_path)
    for f in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()

    # Build enriched signal with 1 LIMIT entry + 1 TP
    from tests.runtime_v2.lifecycle.test_entry_gate import _make_enriched_signal
    enriched = _make_enriched_signal(
        enrichment_id=10, entry_type="LIMIT", entry_price=50000.0,
        sl_price=49000.0, tp_prices=[51000.0], capital_base_usdt=1000.0,
    )
    gate = LifecycleEntryGate(
        risk_engine=RiskCapacityEngine(),
        exchange_port=_make_stub_port(),
        simple_attached_enabled=True,
    )
    from src.runtime_v2.lifecycle.models import ControlMode
    result = gate.process_signal(enriched, [], "NONE")
    assert result.trade_chain is not None
    plan = json.loads(result.trade_chain.plan_state_json)
    assert plan["plan_version"] == 1
    assert plan["rebuild_policy"] == "NONE"
    assert plan["legs"][0]["client_order_id"] == "place_entry_attached:10:leg1"
```

(Add `_make_stub_port()` helper that returns a mock port — see existing test_entry_gate.py for the pattern.)

- [x] **Step 3.2: Run test — expect failure**

```
pytest tests/runtime_v2/lifecycle/test_execution_plan.py::test_entry_gate_populates_plan_state_json -v
```
Expected: FAIL — `plan_state_json` is `"{}"` not populated.

- [x] **Step 3.3: Add ExecutionPlanBuilder call in entry_gate.py process_signal**

In `src/runtime_v2/lifecycle/entry_gate.py`, add import at top:

```python
from src.runtime_v2.lifecycle.execution_plan import ExecutionPlanBuilder
```

In `process_signal()`, just before `chain = TradeChain(...)`, add:

```python
plan_state = ExecutionPlanBuilder.build(
    eid,
    signal.entries,
    signal.take_profits,
    decision.risk_snapshot,
)
```

In the `TradeChain(...)` constructor call, add:

```python
plan_state_json=plan_state,
```

- [x] **Step 3.4: Run tests**

```
pytest tests/runtime_v2/lifecycle/ -v
```
Expected: all pass including the new integration test.

- [ ] **Step 3.5: Commit**

```
git add src/runtime_v2/lifecycle/entry_gate.py tests/runtime_v2/lifecycle/test_execution_plan.py
git commit -m "feat(lifecycle): populate plan_state_json shadow on TradeChain at signal time"
```

---

## Phase 2 — Unified entry command factory

### Task 4: EntryCommandFactory

**Files:**
- Create: `src/runtime_v2/lifecycle/entry_command_factory.py`
- Create: `tests/runtime_v2/lifecycle/test_entry_command_factory.py`

- [x] **Step 4.1: Write tests for unified rule**

```python
# tests/runtime_v2/lifecycle/test_entry_command_factory.py
from __future__ import annotations
import json
import pytest


def _make_leg_snap(seq: int, etype: str, price: float | None, risk: float, qty: float | None, mode: str, weight: float) -> dict:
    return {"sequence": seq, "entry_type": etype, "price": price, "risk_amount": risk, "qty": qty, "qty_mode": mode, "weight": weight}


def _make_tp(seq: int, price: float):
    from src.parser_v2.contracts.entities import Price, TakeProfit
    return TakeProfit(sequence=seq, price=Price(raw=str(price), value=price))


def _make_entry_leg(seq: int, etype: str, price: float | None, weight: float):
    from src.parser_v2.contracts.entities import Price
    from src.runtime_v2.signal_enrichment.models import EnrichedEntryLeg
    p = Price(raw=str(price), value=price) if price is not None else None
    return EnrichedEntryLeg(sequence=seq, entry_type=etype, price=p, weight=weight)


def _factory():
    from src.runtime_v2.lifecycle.entry_command_factory import EntryCommandFactory
    return EntryCommandFactory()


def _cmds(eid, entries, tps, risk_snap, symbol="BTC/USDT", side="LONG",
          leverage=10, hedge_mode=False, position_idx=0, sl_price=49000.0):
    f = _factory()
    return f.build_entry_commands(
        enrichment_id=eid,
        symbol=symbol, side=side,
        entries=entries, take_profits=tps,
        sl_price=sl_price,
        leverage=leverage, hedge_mode=hedge_mode, position_idx=position_idx,
        risk_snapshot=risk_snap,
    )


# ── Unified rule: sequence=1 always gets PLACE_ENTRY_WITH_ATTACHED_TPSL ──────
def test_case_1a_single_limit_1tp_uses_attached():
    """1 LIMIT + 1 TP → PLACE_ENTRY_WITH_ATTACHED_TPSL, tpsl_mode=FULL"""
    entries = [_make_entry_leg(1, "LIMIT", 50000.0, 1.0)]
    tps = [_make_tp(1, 51000.0)]
    snap = {"legs": [_make_leg_snap(1, "LIMIT", 50000.0, 100.0, 0.01, "fixed", 1.0)]}
    cmds = _cmds(1, entries, tps, snap)
    assert len(cmds) == 1
    cmd = cmds[0]
    assert cmd.command_type == "PLACE_ENTRY_WITH_ATTACHED_TPSL"
    payload = json.loads(cmd.payload_json)
    assert payload["attached_tpsl"]["mode"] == "FULL"
    assert payload["attached_tpsl"]["take_profit"] == 51000.0
    assert payload["attached_tpsl"]["stop_loss"] == 49000.0
    assert payload["qty"] == pytest.approx(0.01)
    assert cmd.idempotency_key == "place_entry_attached:1:leg1"


def test_case_1b_single_limit_multi_tp_uses_final_tp_as_attached():
    """1 LIMIT + 2 TP → PLACE_ENTRY_WITH_ATTACHED_TPSL with FINAL TP only (no intermediate at signal time)"""
    entries = [_make_entry_leg(1, "LIMIT", 50000.0, 1.0)]
    tps = [_make_tp(1, 51000.0), _make_tp(2, 52000.0)]
    snap = {"legs": [_make_leg_snap(1, "LIMIT", 50000.0, 100.0, 0.01, "fixed", 1.0)]}
    cmds = _cmds(2, entries, tps, snap)
    assert len(cmds) == 1
    payload = json.loads(cmds[0].payload_json)
    assert payload["attached_tpsl"]["take_profit"] == 52000.0  # final TP
    assert cmds[0].command_type == "PLACE_ENTRY_WITH_ATTACHED_TPSL"


def test_case_2a_multi_limit_1tp_leg1_attached_leg2_plain():
    """N LIMIT + 1 TP → leg1 ATTACHED, legs2+ plain PLACE_ENTRY"""
    entries = [_make_entry_leg(1, "LIMIT", 50000.0, 0.5), _make_entry_leg(2, "LIMIT", 48000.0, 0.5)]
    tps = [_make_tp(1, 51000.0)]
    snap = {"legs": [
        _make_leg_snap(1, "LIMIT", 50000.0, 50.0, 0.005, "fixed", 0.5),
        _make_leg_snap(2, "LIMIT", 48000.0, 50.0, 0.0167, "fixed", 0.5),
    ]}
    cmds = _cmds(3, entries, tps, snap)
    assert len(cmds) == 2
    assert cmds[0].command_type == "PLACE_ENTRY_WITH_ATTACHED_TPSL"
    assert cmds[1].command_type == "PLACE_ENTRY"
    p2 = json.loads(cmds[1].payload_json)
    assert "attached_tpsl" not in p2
    assert cmds[1].idempotency_key == "place_entry:3:leg2"


def test_case_2b_multi_limit_multi_tp_leg1_full_attached_legs2_plain():
    """N LIMIT + N TP → leg1 FULL attached (final TP), legs2+ plain PLACE_ENTRY"""
    entries = [_make_entry_leg(1, "LIMIT", 50000.0, 0.5), _make_entry_leg(2, "LIMIT", 48000.0, 0.5)]
    tps = [_make_tp(1, 51000.0), _make_tp(2, 52000.0)]
    snap = {"legs": [
        _make_leg_snap(1, "LIMIT", 50000.0, 50.0, 0.005, "fixed", 0.5),
        _make_leg_snap(2, "LIMIT", 48000.0, 50.0, 0.0167, "fixed", 0.5),
    ]}
    cmds = _cmds(4, entries, tps, snap)
    assert cmds[0].command_type == "PLACE_ENTRY_WITH_ATTACHED_TPSL"
    p0 = json.loads(cmds[0].payload_json)
    assert p0["attached_tpsl"]["take_profit"] == 52000.0  # final TP
    assert cmds[1].command_type == "PLACE_ENTRY"


def test_case_3a_market_single_tp_deferred():
    """1 MARKET (deferred) + 1 TP → PLACE_ENTRY_WITH_ATTACHED_TPSL with qty_mode=deferred_market"""
    entries = [_make_entry_leg(1, "MARKET", None, 1.0)]
    tps = [_make_tp(1, 51000.0)]
    snap = {"legs": [_make_leg_snap(1, "MARKET", None, 100.0, None, "deferred_market", 1.0)]}
    cmds = _cmds(5, entries, tps, snap, sl_price=49000.0)
    assert len(cmds) == 1
    assert cmds[0].command_type == "PLACE_ENTRY_WITH_ATTACHED_TPSL"
    payload = json.loads(cmds[0].payload_json)
    assert payload["qty_mode"] == "deferred_market"
    assert payload["attached_tpsl"]["mode"] == "FULL"


def test_case_4b_market_plus_limits_multi_tp():
    """1 MARKET + 1 LIMIT + N TP → leg1=ATTACHED(FULL), leg2=plain"""
    entries = [_make_entry_leg(1, "MARKET", None, 0.5), _make_entry_leg(2, "LIMIT", 48000.0, 0.5)]
    tps = [_make_tp(1, 51000.0), _make_tp(2, 52000.0)]
    snap = {"legs": [
        _make_leg_snap(1, "MARKET", None, 50.0, None, "deferred_market", 0.5),
        _make_leg_snap(2, "LIMIT", 48000.0, 50.0, 0.0167, "fixed", 0.5),
    ]}
    cmds = _cmds(6, entries, tps, snap)
    assert cmds[0].command_type == "PLACE_ENTRY_WITH_ATTACHED_TPSL"
    assert cmds[1].command_type == "PLACE_ENTRY"
    p0 = json.loads(cmds[0].payload_json)
    assert p0["attached_tpsl"]["take_profit"] == 52000.0  # final TP


def test_c_simple_parity_with_old_c_builder():
    """Parity: 1 LIMIT + 1 TP output matches old _build_c_commands behavior."""
    entries = [_make_entry_leg(1, "LIMIT", 50000.0, 1.0)]
    tps = [_make_tp(1, 51000.0)]
    snap = {"legs": [_make_leg_snap(1, "LIMIT", 50000.0, 100.0, 0.01, "fixed", 1.0)]}
    cmds = _cmds(7, entries, tps, snap)
    assert len(cmds) == 1
    payload = json.loads(cmds[0].payload_json)
    assert payload["attached_tpsl"]["mode"] == "FULL"
    assert payload["entry_type"] == "LIMIT"
    assert payload["price"] == 50000.0
    assert payload["qty"] == pytest.approx(0.01)
```

- [x] **Step 4.2: Run tests — expect ImportError**

```
pytest tests/runtime_v2/lifecycle/test_entry_command_factory.py -v
```
Expected: `ImportError: cannot import name 'EntryCommandFactory'`

- [x] **Step 4.3: Implement EntryCommandFactory**

```python
# src/runtime_v2/lifecycle/entry_command_factory.py
from __future__ import annotations

import json

from src.runtime_v2.lifecycle.models import ExecutionCommand


class EntryCommandFactory:
    """
    Unified rule:
      leg sequence=1 → PLACE_ENTRY_WITH_ATTACHED_TPSL (tpsl_mode=FULL, SL + final TP)
      leg sequence>1 → PLACE_ENTRY (no attached TPSL)
    Same for all 8 entry/TP combinations.
    """

    def build_entry_commands(
        self,
        *,
        enrichment_id: int,
        symbol: str,
        side: str,
        entries: list,         # list[EnrichedEntryLeg]
        take_profits: list,    # list[TakeProfit]
        sl_price: float | None,
        leverage: int,
        hedge_mode: bool,
        position_idx: int,
        risk_snapshot: dict,
    ) -> list[ExecutionCommand]:
        legs_snap: dict[int, dict] = {
            s["sequence"]: s for s in risk_snapshot.get("legs", [])
        }

        final_tp_price: float | None = None
        if take_profits:
            sorted_tps = sorted(take_profits, key=lambda t: t.sequence)
            last_tp = sorted_tps[-1]
            if last_tp.price:
                final_tp_price = last_tp.price.value

        commands: list[ExecutionCommand] = []
        for leg in sorted(entries, key=lambda e: e.sequence):
            snap = legs_snap.get(leg.sequence, {})
            is_first = leg.sequence == 1
            is_deferred = snap.get("qty_mode") == "deferred_market"

            if is_first:
                cmd = self._build_attached_cmd(
                    enrichment_id=enrichment_id,
                    symbol=symbol, side=side,
                    leg=leg, snap=snap,
                    sl_price=sl_price,
                    final_tp_price=final_tp_price,
                    leverage=leverage,
                    hedge_mode=hedge_mode,
                    position_idx=position_idx,
                    is_deferred=is_deferred,
                )
            else:
                cmd = self._build_plain_entry_cmd(
                    enrichment_id=enrichment_id,
                    symbol=symbol, side=side,
                    leg=leg, snap=snap,
                    leverage=leverage,
                    hedge_mode=hedge_mode,
                    position_idx=position_idx,
                    is_deferred=is_deferred,
                )
            commands.append(cmd)

        return commands

    def _build_attached_cmd(
        self,
        *,
        enrichment_id: int,
        symbol: str,
        side: str,
        leg,
        snap: dict,
        sl_price: float | None,
        final_tp_price: float | None,
        leverage: int,
        hedge_mode: bool,
        position_idx: int,
        is_deferred: bool,
    ) -> ExecutionCommand:
        attached_tpsl: dict = {
            "mode": "FULL",
            "stop_loss": sl_price,
            "sl_trigger_by": "MarkPrice",
        }
        if final_tp_price is not None:
            attached_tpsl["take_profit"] = final_tp_price
            attached_tpsl["tp_trigger_by"] = "MarkPrice"

        base: dict = {
            "symbol": symbol,
            "side": side,
            "entry_type": leg.entry_type,
            "price": leg.price.value if leg.entry_type == "LIMIT" and leg.price else None,
            "leverage": leverage,
            "hedge_mode": hedge_mode,
            "position_idx": position_idx,
            "attached_tpsl": attached_tpsl,
        }

        if is_deferred:
            payload: dict = {
                **base,
                "qty_mode": "deferred_market",
                "risk_amount": float(snap.get("risk_amount", 0.0)),
                "sl_price": sl_price,
            }
        else:
            qty = float(snap["qty"]) if snap.get("qty") is not None else 0.0
            payload = {**base, "qty": qty}

        return ExecutionCommand(
            trade_chain_id=0,
            command_type="PLACE_ENTRY_WITH_ATTACHED_TPSL",
            status="PENDING",
            payload_json=json.dumps(payload),
            idempotency_key=f"place_entry_attached:{enrichment_id}:leg{leg.sequence}",
        )

    def _build_plain_entry_cmd(
        self,
        *,
        enrichment_id: int,
        symbol: str,
        side: str,
        leg,
        snap: dict,
        leverage: int,
        hedge_mode: bool,
        position_idx: int,
        is_deferred: bool,
    ) -> ExecutionCommand:
        if is_deferred:
            payload: dict = {
                "symbol": symbol,
                "side": side,
                "entry_type": leg.entry_type,
                "price": None,
                "qty_mode": "deferred_market",
                "risk_amount": float(snap.get("risk_amount", 0.0)),
                "leverage": leverage,
                "hedge_mode": hedge_mode,
                "position_idx": position_idx,
                "sequence": leg.sequence,
            }
        else:
            qty = float(snap["qty"]) if snap.get("qty") is not None else 0.0
            payload = {
                "symbol": symbol,
                "side": side,
                "entry_type": leg.entry_type,
                "price": leg.price.value if leg.entry_type == "LIMIT" and leg.price else None,
                "qty": qty,
                "leverage": leverage,
                "hedge_mode": hedge_mode,
                "position_idx": position_idx,
                "sequence": leg.sequence,
            }

        return ExecutionCommand(
            trade_chain_id=0,
            command_type="PLACE_ENTRY",
            status="PENDING",
            payload_json=json.dumps(payload),
            idempotency_key=f"place_entry:{enrichment_id}:leg{leg.sequence}",
        )


__all__ = ["EntryCommandFactory"]
```

- [x] **Step 4.4: Run tests**

```
pytest tests/runtime_v2/lifecycle/test_entry_command_factory.py -v
```
Expected: 8 PASSED

- [ ] **Step 4.5: Commit**

```
git add src/runtime_v2/lifecycle/entry_command_factory.py tests/runtime_v2/lifecycle/test_entry_command_factory.py
git commit -m "feat(lifecycle): add EntryCommandFactory with unified leg-1=FULL-attached, legs-2+=plain rule"
```

---

### Task 5: Replace 4 builders in entry_gate.py with EntryCommandFactory

**Files:**
- Modify: `src/runtime_v2/lifecycle/entry_gate.py`
- Modify: `tests/runtime_v2/lifecycle/test_entry_gate.py`

- [x] **Step 5.1: Run existing entry gate tests to record baseline**

```
pytest tests/runtime_v2/lifecycle/test_entry_gate.py -v 2>&1 | head -50
```
Note which tests cover C/D builder behavior — these will need updating.

- [x] **Step 5.2: Replace builders in entry_gate.py**

Add import at top of `entry_gate.py`:

```python
from src.runtime_v2.lifecycle.entry_command_factory import EntryCommandFactory
```

Replace `_build_entry_commands()` method body (the `# ── New C/D decision matrix ──` block) with:

```python
def _build_entry_commands(
    self,
    enriched: EnrichedCanonicalMessage,
    decision,
) -> list[ExecutionCommand]:
    signal = enriched.enriched_signal
    eid = enriched.enrichment_id

    if self._use_legacy_routing:
        return self._build_legacy_commands(
            signal, enriched.management_plan or ManagementPlanConfig(), eid, decision
        )

    sl_price = (
        signal.stop_loss.price.value
        if signal.stop_loss and signal.stop_loss.price else None
    )

    if not (self._simple_attached_enabled is True and sl_price is not None):
        # Fall back to D_POSITION_TPSL standalone mode (no attached TPSL)
        return self._build_d_commands(
            signal, eid,
            float(decision.size_usdt or 0.0),
            float(decision.risk_snapshot.get("entry_price") or 0.0),
            int(decision.risk_snapshot.get("leverage") or 1),
            bool(decision.risk_snapshot.get("hedge_mode", False)),
            self.resolve_position_idx(signal.side, bool(decision.risk_snapshot.get("hedge_mode", False))),
            sl_price,
            len(signal.take_profits),
            self._get_close_pcts(enriched.management_plan or ManagementPlanConfig(), len(signal.take_profits)),
            decision.risk_snapshot.get("legs", []),
        )

    factory = EntryCommandFactory()
    return factory.build_entry_commands(
        enrichment_id=eid,
        symbol=signal.symbol,
        side=signal.side,
        entries=signal.entries,
        take_profits=signal.take_profits,
        sl_price=sl_price,
        leverage=int(decision.risk_snapshot.get("leverage") or 1),
        hedge_mode=bool(decision.risk_snapshot.get("hedge_mode", False)),
        position_idx=self.resolve_position_idx(
            signal.side, bool(decision.risk_snapshot.get("hedge_mode", False))
        ),
        risk_snapshot=decision.risk_snapshot,
    )
```

Remove methods: `_build_c_commands`, `_build_c_multi_tp_commands`, `_build_d_multi_entry_1tp_commands`, `_build_d_multi_entry_multi_tp_commands`, `_place_entry_attached_cmd`.

Keep: `_build_legacy_commands`, `_build_d_commands` (still used for D_POSITION_TPSL fallback).

Also remove the `chain_execution_mode` selection logic that chose between C_SIMPLE_ATTACHED / C_MULTI_TP / D_MULTI_ENTRY_1TP / D_MULTI_ENTRY_MULTI_TP. Replace with:

```python
if self._simple_attached_enabled is True and sl_price_for_decision is not None:
    chain_execution_mode = "UNIFIED_PLAN"
else:
    chain_execution_mode = "D_POSITION_TPSL"
```

Add `"UNIFIED_PLAN"` to `_ATTACHED_PROTECTION_MODES`:

```python
_ATTACHED_PROTECTION_MODES = frozenset({
    "UNIFIED_PLAN",
    "C_SIMPLE_ATTACHED", "C_MULTI_TP",
    "D_MULTI_ENTRY_1TP", "D_MULTI_ENTRY_MULTI_TP", "D_POSITION_TPSL",
})
```

- [x] **Step 5.3: Update tests for new behavior**

In `tests/runtime_v2/lifecycle/test_entry_gate.py`, update any tests that assert:
- `command_type == "PLACE_ENTRY_WITH_ATTACHED_TPSL"` and check for multiple legs all having it → now only leg 1 has it
- `execution_mode == "C_MULTI_TP"` / `"D_MULTI_ENTRY_1TP"` / `"D_MULTI_ENTRY_MULTI_TP"` → now `"UNIFIED_PLAN"`
- intermediate TPs emitted at signal time → they are no longer emitted at signal time

For each failing test, adjust the assertion to reflect the new unified rule. Example:

```python
# OLD: D_MULTI_ENTRY_1TP emitted all legs with FULL attached
# NEW: only leg 1 attached, leg 2 plain
def test_multi_entry_single_tp_leg1_attached_leg2_plain(tmp_path, ...):
    ...
    cmds = result.execution_commands
    entry_cmds = [c for c in cmds if "ENTRY" in c.command_type]
    assert entry_cmds[0].command_type == "PLACE_ENTRY_WITH_ATTACHED_TPSL"
    assert entry_cmds[1].command_type == "PLACE_ENTRY"
```

- [x] **Step 5.4: Run full lifecycle test suite**

```
pytest tests/runtime_v2/lifecycle/ -v
```
Expected: all pass.

- [ ] **Step 5.5: Commit**

```
git add src/runtime_v2/lifecycle/entry_gate.py tests/runtime_v2/lifecycle/test_entry_gate.py
git commit -m "refactor(lifecycle): replace 4 entry builders with EntryCommandFactory unified rule"
```

---

## Phase 3 — Post-fill TP rebuilder extraction

### Task 6: PostFillProtectionRebuilder

**Files:**
- Create: `src/runtime_v2/lifecycle/post_fill_rebuilder.py`
- Create: `tests/runtime_v2/lifecycle/test_post_fill_rebuilder.py`

- [x] **Step 6.1: Write tests**

```python
# tests/runtime_v2/lifecycle/test_post_fill_rebuilder.py
from __future__ import annotations
import json
import pytest


def _make_chain(
    *,
    chain_id: int = 1,
    side: str = "LONG",
    plan_state_json: str | None = None,
    risk_snap: dict | None = None,
):
    from src.runtime_v2.lifecycle.models import TradeChain
    from src.runtime_v2.signal_enrichment.models import ManagementPlanConfig
    mp = ManagementPlanConfig()
    return TradeChain(
        trade_chain_id=chain_id,
        source_enrichment_id=chain_id,
        canonical_message_id=chain_id * 10,
        raw_message_id=chain_id * 100,
        trader_id="t", account_id="a",
        symbol="BTC/USDT", side=side,
        lifecycle_state="OPEN",
        entry_mode="TWO_STEP",
        management_plan_json=mp.model_dump_json(),
        plan_state_json=plan_state_json or "{}",
        risk_snapshot_json=json.dumps(risk_snap or {}),
    )


def _plan_multi_tp(intermediate_tps: list[float]) -> str:
    return json.dumps({
        "plan_version": 1,
        "rebuild_policy": "ON_EACH_ENTRY_FILL",
        "intermediate_tps": intermediate_tps,
        "final_tp": intermediate_tps[-1] + 1000.0 if intermediate_tps else None,
    })


def _plan_single_tp() -> str:
    return json.dumps({
        "plan_version": 1,
        "rebuild_policy": "NONE",
        "intermediate_tps": [],
        "final_tp": 51000.0,
    })


def _rebuilder():
    from src.runtime_v2.lifecycle.post_fill_rebuilder import PostFillProtectionRebuilder
    return PostFillProtectionRebuilder()


def test_single_tp_rebuild_policy_none_emits_no_commands():
    chain = _make_chain(plan_state_json=_plan_single_tp())
    cmds = _rebuilder().build_after_fill(chain, filled_entry_qty=0.01, exchange_event_id=5)
    assert cmds == []


def test_multi_tp_emits_intermediate_tp_commands():
    chain = _make_chain(plan_state_json=_plan_multi_tp([51000.0, 52000.0]))
    cmds = _rebuilder().build_after_fill(chain, filled_entry_qty=0.02, exchange_event_id=7)
    # intermediate_tps = [51000.0, 52000.0], final is 53000.0 (not included)
    assert len(cmds) == 2
    for cmd in cmds:
        assert cmd.command_type == "SET_POSITION_TPSL_PARTIAL"
    p0 = json.loads(cmds[0].payload_json)
    assert p0["take_profit"] == 51000.0
    assert p0["supersedes_previous"] is True


def test_multi_tp_tp_size_based_on_filled_qty():
    chain = _make_chain(plan_state_json=_plan_multi_tp([51000.0]))
    # 1 intermediate TP at 51000, final is 52000
    # management_plan has default close_distribution (equal split)
    cmds = _rebuilder().build_after_fill(chain, filled_entry_qty=0.10, exchange_event_id=9)
    assert len(cmds) == 1
    p = json.loads(cmds[0].payload_json)
    # close_pct defaults to equal split → with 1 intermediate out of 2 TPs, 50% → 0.05
    assert p["tp_size"] == pytest.approx(0.05)


def test_missing_plan_state_json_emits_nothing():
    chain = _make_chain(plan_state_json="{}")
    cmds = _rebuilder().build_after_fill(chain, filled_entry_qty=0.01, exchange_event_id=10)
    assert cmds == []
```

- [x] **Step 6.2: Run tests — expect ImportError**

```
pytest tests/runtime_v2/lifecycle/test_post_fill_rebuilder.py -v
```

- [x] **Step 6.3: Implement PostFillProtectionRebuilder**

```python
# src/runtime_v2/lifecycle/post_fill_rebuilder.py
from __future__ import annotations

import json

from src.runtime_v2.lifecycle.models import ExecutionCommand, TradeChain


class PostFillProtectionRebuilder:
    """Generates intermediate TP commands after an entry fill for multi-TP plans."""

    def build_after_fill(
        self,
        chain: TradeChain,
        filled_entry_qty: float,
        exchange_event_id: int,
    ) -> list[ExecutionCommand]:
        try:
            plan = json.loads(chain.plan_state_json or "{}")
        except Exception:
            return []

        rebuild_policy = plan.get("rebuild_policy", "NONE")
        if rebuild_policy != "ON_EACH_ENTRY_FILL":
            return []

        intermediate_tps: list[float] = plan.get("intermediate_tps", [])
        if not intermediate_tps:
            return []

        n_total_tps = len(intermediate_tps) + 1  # +1 for final TP
        chain_id = chain.trade_chain_id
        commands: list[ExecutionCommand] = []

        for i, tp_price in enumerate(intermediate_tps):
            close_pct = 100.0 / n_total_tps
            tp_qty = round(filled_entry_qty * close_pct / 100.0, 8)
            payload = {
                "symbol": chain.symbol,
                "side": chain.side,
                "tp_sequence": i + 1,
                "take_profit": tp_price,
                "tp_size": tp_qty,
                "tp_order_type": "Limit",
                "tp_limit_price": tp_price,
                "tp_trigger_by": "MarkPrice",
                "preserve_sl": True,
                "supersedes_previous": True,
            }
            commands.append(ExecutionCommand(
                trade_chain_id=chain_id,
                command_type="SET_POSITION_TPSL_PARTIAL",
                payload_json=json.dumps(payload),
                idempotency_key=(
                    f"tp_partial_fill:{chain_id}:{exchange_event_id}:tp{i + 1}"
                ),
            ))

        return commands


__all__ = ["PostFillProtectionRebuilder"]
```

- [x] **Step 6.4: Run tests**

```
pytest tests/runtime_v2/lifecycle/test_post_fill_rebuilder.py -v
```
Expected: 4 PASSED

- [ ] **Step 6.5: Commit**

```
git add src/runtime_v2/lifecycle/post_fill_rebuilder.py tests/runtime_v2/lifecycle/test_post_fill_rebuilder.py
git commit -m "feat(lifecycle): add PostFillProtectionRebuilder for intermediate TP commands after entry fill"
```

---

### Task 7: Use PostFillProtectionRebuilder in event_processor.py

**Files:**
- Modify: `src/runtime_v2/lifecycle/event_processor.py`
- Modify: `tests/runtime_v2/lifecycle/test_event_processor.py`

- [x] **Step 7.1: Write failing test**

Add to `tests/runtime_v2/lifecycle/test_event_processor.py`:

```python
def test_entry_filled_with_multi_tp_plan_emits_intermediate_tp_commands():
    """ENTRY_FILLED on a chain with rebuild_policy=ON_EACH_ENTRY_FILL emits intermediate TP cmds."""
    import json
    plan_state = json.dumps({
        "plan_version": 1,
        "rebuild_policy": "ON_EACH_ENTRY_FILL",
        "intermediate_tps": [51000.0],
        "final_tp": 52000.0,
    })
    chain = _make_chain(state="WAITING_ENTRY")
    chain = chain.model_copy(update={"plan_state_json": plan_state, "execution_mode": "UNIFIED_PLAN"})
    event = _make_exchange_event(
        event_type="ENTRY_FILLED",
        payload={"fill_price": 50000.0, "filled_qty": 0.01},
    )
    processor = _make_processor()
    result = processor.process(event, chain, [])
    tp_cmds = [c for c in result.execution_commands if c.command_type == "SET_POSITION_TPSL_PARTIAL"]
    assert len(tp_cmds) == 1
    p = json.loads(tp_cmds[0].payload_json)
    assert p["take_profit"] == 51000.0


def test_entry_filled_with_single_tp_plan_emits_no_intermediate_cmds():
    """ENTRY_FILLED on rebuild_policy=NONE emits no intermediate TP commands."""
    plan_state = json.dumps({
        "plan_version": 1, "rebuild_policy": "NONE",
        "intermediate_tps": [], "final_tp": 51000.0,
    })
    chain = _make_chain(state="WAITING_ENTRY")
    chain = chain.model_copy(update={"plan_state_json": plan_state, "execution_mode": "UNIFIED_PLAN"})
    event = _make_exchange_event(
        event_type="ENTRY_FILLED",
        payload={"fill_price": 50000.0, "filled_qty": 0.01},
    )
    result = _make_processor().process(event, chain, [])
    tp_cmds = [c for c in result.execution_commands if c.command_type == "SET_POSITION_TPSL_PARTIAL"]
    assert tp_cmds == []
```

- [x] **Step 7.2: Run test — expect failure**

```
pytest tests/runtime_v2/lifecycle/test_event_processor.py::test_entry_filled_with_multi_tp_plan_emits_intermediate_tp_commands -v
```

- [x] **Step 7.3: Update event_processor.py**

Add import:

```python
from src.runtime_v2.lifecycle.post_fill_rebuilder import PostFillProtectionRebuilder
```

Replace the inline `_build_tp_partial_commands_after_fill()` call in `_process_entry_filled()` with:

```python
commands: list[ExecutionCommand] = []
rebuilder = PostFillProtectionRebuilder()
commands = rebuilder.build_after_fill(chain, new_filled, eid or 0)
```

Remove `_build_tp_partial_commands_after_fill()` method from `LifecycleEventProcessor`.

- [x] **Step 7.4: Run tests**

```
pytest tests/runtime_v2/lifecycle/ -v
```
Expected: all pass.

- [ ] **Step 7.5: Commit**

```
git add src/runtime_v2/lifecycle/event_processor.py tests/runtime_v2/lifecycle/test_event_processor.py
git commit -m "refactor(lifecycle): use PostFillProtectionRebuilder in event_processor, trigger by rebuild_policy"
```

---

## Phase 4 — Risk tracking + diff engine

### Task 8: risk_already_realized tracking

**Files:**
- Create: `db/ops_migrations/005_ops_risk_tracking.sql`
- Modify: `src/runtime_v2/lifecycle/models.py`
- Modify: `src/runtime_v2/lifecycle/repositories.py`
- Modify: `src/runtime_v2/lifecycle/event_processor.py`

- [x] **Step 8.1: Write failing test**

Add to `tests/runtime_v2/lifecycle/test_event_processor.py`:

```python
def test_entry_filled_updates_risk_already_realized():
    """risk_already_realized = fill_qty * abs(fill_price - sl_price) accumulated."""
    chain = _make_chain(state="WAITING_ENTRY")
    chain = chain.model_copy(update={
        "expected_stop_price": 49000.0,
        "risk_snapshot_json": json.dumps({"sl_price": 49000.0}),
    })
    event = _make_exchange_event(
        event_type="ENTRY_FILLED",
        payload={"fill_price": 50000.0, "filled_qty": 0.01},
    )
    result = _make_processor().process(event, chain, [])
    # 0.01 * abs(50000 - 49000) = 10.0
    assert result.new_risk_already_realized == pytest.approx(10.0)
```

- [x] **Step 8.2: Run test — expect AttributeError**

```
pytest tests/runtime_v2/lifecycle/test_event_processor.py::test_entry_filled_updates_risk_already_realized -v
```

- [x] **Step 8.3: Write migration**

```sql
-- db/ops_migrations/005_ops_risk_tracking.sql
ALTER TABLE ops_trade_chains ADD COLUMN risk_already_realized REAL NOT NULL DEFAULT 0;
ALTER TABLE ops_trade_chains ADD COLUMN risk_remaining REAL NOT NULL DEFAULT 0;
```

- [x] **Step 8.4: Add fields to TradeChain**

In `src/runtime_v2/lifecycle/models.py`, add after `execution_mode`:

```python
risk_already_realized: float = 0.0
risk_remaining: float = 0.0
```

- [x] **Step 8.5: Update repositories.py**

Add `risk_already_realized, risk_remaining` to `_CHAIN_COLS`, `_chain_from_row()`, and `save()` in `TradeChainRepository`.

- [x] **Step 8.6: Add new_risk_already_realized to EventProcessorResult**

In `src/runtime_v2/lifecycle/event_processor.py`, add to `EventProcessorResult` dataclass:

```python
new_risk_already_realized: float | None = None
new_risk_remaining: float | None = None
```

- [x] **Step 8.7: Update _process_entry_filled to compute risk_already_realized**

In `_process_entry_filled()`, after computing `new_filled`:

```python
sl_price: float | None = None
try:
    rs = json.loads(chain.risk_snapshot_json or "{}")
    sl_price = rs.get("sl_price")
except Exception:
    pass

new_risk_already_realized: float | None = None
if sl_price is not None:
    fill_risk = fill_qty * abs(fill_price - sl_price)
    new_risk_already_realized = chain.risk_already_realized + fill_risk

    risk_total = float(json.loads(chain.risk_snapshot_json or "{}").get("risk_amount", 0.0))
    new_risk_remaining = max(0.0, risk_total - new_risk_already_realized) if risk_total > 0 else None
else:
    new_risk_remaining = None
```

Return these in `EventProcessorResult`.

- [x] **Step 8.8: Run tests**

```
pytest tests/runtime_v2/lifecycle/ -v
```
Expected: all pass.

- [ ] **Step 8.9: Commit**

```
git add db/ops_migrations/005_ops_risk_tracking.sql src/runtime_v2/lifecycle/models.py src/runtime_v2/lifecycle/repositories.py src/runtime_v2/lifecycle/event_processor.py tests/runtime_v2/lifecycle/test_event_processor.py
git commit -m "feat(lifecycle): track risk_already_realized and risk_remaining on entry fill"
```

---

### Task 9: ExecutionPlanDiffEngine

**Files:**
- Create: `src/runtime_v2/lifecycle/diff_engine.py`
- Create: `tests/runtime_v2/lifecycle/test_diff_engine.py`

- [x] **Step 9.1: Write tests**

```python
# tests/runtime_v2/lifecycle/test_diff_engine.py
from __future__ import annotations
import json
import pytest


def _pending_plan(legs: list[dict], rebuild_policy: str = "NONE") -> str:
    return json.dumps({
        "plan_version": 1,
        "rebuild_policy": rebuild_policy,
        "protection_policy": "TPSL_ATTACHED_FIRST_LEG",
        "risk_policy": "REBALANCE_REMAINING_RISK_ON_REPLAN",
        "stop_loss": 49000.0,
        "final_tp": 51000.0,
        "intermediate_tps": [],
        "legs": legs,
    })


def _pending_leg(seq: int, etype: str, price: float | None, risk: float, qty: float | None) -> dict:
    return {
        "leg_id": f"leg_{seq}", "sequence": seq, "entry_type": etype,
        "price": price, "risk_budget": risk, "qty": qty,
        "qty_mode": "fixed" if qty is not None else "deferred_market",
        "status": "PENDING", "client_order_id": f"place_entry_attached:1:leg{seq}",
    }


def _filled_leg(seq: int, etype: str, price: float | None, risk: float, qty: float | None) -> dict:
    leg = _pending_leg(seq, etype, price, risk, qty)
    leg["status"] = "FILLED"
    return leg


def _engine():
    from src.runtime_v2.lifecycle.diff_engine import ExecutionPlanDiffEngine
    return ExecutionPlanDiffEngine()


# ── Case A: ONE_SHOT LIMIT → MARKET (cancel + recreate) ──────────────────────
def test_case_a_limit_to_market_emits_cancel_and_replace():
    current = _pending_plan([_pending_leg(1, "LIMIT", 50000.0, 100.0, 0.01)])
    target = _pending_plan([_pending_leg(1, "MARKET", None, 100.0, None)])
    actions = _engine().diff(current, target, risk_remaining=100.0, sl_price=49000.0)
    types = [a["action"] for a in actions]
    assert "cancel_pending_entry" in types
    assert "replace_entry_leg" in types


def test_case_a_limit_to_market_replace_has_new_qty_from_risk():
    """qty on the new MARKET leg = risk_remaining / abs(market_price - sl_price)"""
    current = _pending_plan([_pending_leg(1, "LIMIT", 50000.0, 100.0, 0.01)])
    target = _pending_plan([_pending_leg(1, "MARKET", None, 100.0, None)])
    actions = _engine().diff(current, target, risk_remaining=100.0, sl_price=49000.0,
                             current_market_price=50000.0)
    replace = next(a for a in actions if a["action"] == "replace_entry_leg")
    # 100 / abs(50000 - 49000) = 0.1
    assert replace["new_qty"] == pytest.approx(0.1)


# ── Filled leg is never modified ──────────────────────────────────────────────
def test_filled_leg_is_kept_unchanged():
    current = _pending_plan([
        _filled_leg(1, "LIMIT", 50000.0, 50.0, 0.005),
        _pending_leg(2, "LIMIT", 48000.0, 50.0, 0.0167),
    ])
    target = _pending_plan([
        _filled_leg(1, "LIMIT", 50000.0, 50.0, 0.005),
        _pending_leg(2, "MARKET", None, 50.0, None),
    ])
    actions = _engine().diff(current, target, risk_remaining=50.0, sl_price=49000.0)
    filled_leg_actions = [a for a in actions if a.get("sequence") == 1]
    assert all(a["action"] == "keep_entry_leg" for a in filled_leg_actions)


# ── Zero-risk-distance rejection ──────────────────────────────────────────────
def test_zero_risk_distance_is_rejected():
    current = _pending_plan([_pending_leg(1, "LIMIT", 50000.0, 100.0, 0.01)])
    target = _pending_plan([_pending_leg(1, "LIMIT", 49000.0, 100.0, 0.0)])
    with pytest.raises(ValueError, match="zero_risk_distance"):
        _engine().diff(current, target, risk_remaining=100.0, sl_price=49000.0)


# ── keep_remaining policy: unfilled legs keep budget ─────────────────────────
def test_keep_remaining_policy_preserves_unfilled_leg_budgets():
    current = _pending_plan([
        _filled_leg(1, "MARKET", None, 50.0, 0.01),
        _pending_leg(2, "LIMIT", 48000.0, 50.0, 0.0167),
    ])
    target = current  # no changes needed
    actions = _engine().diff(current, target, risk_remaining=50.0, sl_price=49000.0)
    # leg 2 unchanged → keep action
    leg2_action = next((a for a in actions if a.get("sequence") == 2), None)
    assert leg2_action is not None
    assert leg2_action["action"] == "keep_entry_leg"
```

- [x] **Step 9.2: Run tests — expect ImportError**

```
pytest tests/runtime_v2/lifecycle/test_diff_engine.py -v
```

- [x] **Step 9.3: Implement ExecutionPlanDiffEngine**

```python
# src/runtime_v2/lifecycle/diff_engine.py
from __future__ import annotations

import json


class ExecutionPlanDiffEngine:
    """
    Compares current plan_state_json with a target plan and produces diff actions.
    Never modifies FILLED legs.
    Recomputes qty for replacement legs from risk_remaining.
    """

    def diff(
        self,
        current_plan_json: str,
        target_plan_json: str,
        *,
        risk_remaining: float,
        sl_price: float,
        current_market_price: float | None = None,
        consolidation_policy: str = "keep_remaining",
    ) -> list[dict]:
        current = json.loads(current_plan_json)
        target = json.loads(target_plan_json)

        current_by_seq: dict[int, dict] = {l["sequence"]: l for l in current.get("legs", [])}
        target_by_seq: dict[int, dict] = {l["sequence"]: l for l in target.get("legs", [])}

        actions: list[dict] = []

        for seq, target_leg in sorted(target_by_seq.items()):
            current_leg = current_by_seq.get(seq)

            if current_leg and current_leg["status"] == "FILLED":
                actions.append({"action": "keep_entry_leg", "sequence": seq, "reason": "already_filled"})
                continue

            if current_leg is None:
                actions.append({"action": "add_entry_leg", "sequence": seq, "leg": target_leg})
                continue

            legs_differ = (
                current_leg.get("entry_type") != target_leg.get("entry_type")
                or current_leg.get("price") != target_leg.get("price")
            )

            if not legs_differ:
                actions.append({"action": "keep_entry_leg", "sequence": seq})
                continue

            # Validate no zero-risk-distance
            ref_price = (
                target_leg.get("price") or current_market_price
            )
            if ref_price is not None and sl_price is not None:
                risk_dist = abs(ref_price - sl_price)
                if risk_dist == 0:
                    raise ValueError(f"zero_risk_distance for leg sequence={seq}")

            # Compute new qty from risk_remaining
            new_qty: float | None = None
            if ref_price is not None and sl_price is not None:
                risk_dist = abs(ref_price - sl_price)
                if risk_dist > 0:
                    leg_risk = float(target_leg.get("risk_budget", 0.0))
                    new_qty = leg_risk / risk_dist

            actions.append({
                "action": "replace_entry_leg",
                "sequence": seq,
                "old_client_order_id": current_leg.get("client_order_id"),
                "new_entry_type": target_leg["entry_type"],
                "new_price": target_leg.get("price"),
                "new_qty": new_qty,
            })
            actions.append({
                "action": "cancel_pending_entry",
                "sequence": seq,
                "client_order_id": current_leg.get("client_order_id"),
            })

        # Legs in current but not in target → cancel
        for seq, current_leg in current_by_seq.items():
            if seq not in target_by_seq and current_leg["status"] == "PENDING":
                actions.append({
                    "action": "cancel_pending_entry",
                    "sequence": seq,
                    "client_order_id": current_leg.get("client_order_id"),
                })

        return actions


__all__ = ["ExecutionPlanDiffEngine"]
```

- [x] **Step 9.4: Run tests**

```
pytest tests/runtime_v2/lifecycle/test_diff_engine.py -v
```
Expected: all PASSED

- [ ] **Step 9.5: Commit**

```
git add src/runtime_v2/lifecycle/diff_engine.py tests/runtime_v2/lifecycle/test_diff_engine.py
git commit -m "feat(lifecycle): add ExecutionPlanDiffEngine for entry-changing update replan"
```

---

### Task 10: Entry-changing updates in entry_gate.py

**Files:**
- Modify: `src/runtime_v2/lifecycle/entry_gate.py`
- Modify: `tests/runtime_v2/lifecycle/test_entry_gate.py`

- [x] **Step 10.1: Write failing test**

Add to `tests/runtime_v2/lifecycle/test_entry_gate.py`:

```python
def test_entry_changing_update_limit_to_market_emits_cancel_and_new_entry(tmp_path):
    """
    Caso A: ONE_SHOT chain with LIMIT pending → update says 'enter now' (MARKET).
    Expected: CANCEL_PENDING_ENTRY + PLACE_ENTRY_WITH_ATTACHED_TPSL (MARKET) commands.
    """
    import json
    from src.runtime_v2.lifecycle.models import TradeChain, ExecutionCommand

    plan_state = json.dumps({
        "plan_version": 1,
        "rebuild_policy": "NONE",
        "protection_policy": "TPSL_ATTACHED_FIRST_LEG",
        "stop_loss": 49000.0, "final_tp": 51000.0, "intermediate_tps": [],
        "legs": [{
            "leg_id": "leg_1", "sequence": 1, "entry_type": "LIMIT",
            "price": 50000.0, "risk_budget": 100.0, "qty": 0.01,
            "qty_mode": "fixed", "status": "PENDING",
            "client_order_id": "place_entry_attached:1:leg1",
        }],
    })
    chain = _make_chain_with_plan(plan_state_json=plan_state, expected_stop_price=49000.0)
    active_cmds = [_make_pending_cmd("PLACE_ENTRY_WITH_ATTACHED_TPSL", "place_entry_attached:1:leg1")]

    gate = _make_gate(simple_attached_enabled=True)
    enriched = _make_limit_to_market_update(enrichment_id=99, symbol=chain.symbol, side=chain.side)

    result = gate.process_update(enriched, [chain], {chain.trade_chain_id: active_cmds})
    all_cmds = [c for cr in result.chain_results for c in cr.execution_commands]
    cmd_types = {c.command_type for c in all_cmds}
    assert "CANCEL_PENDING_ENTRY" in cmd_types
    assert "PLACE_ENTRY_WITH_ATTACHED_TPSL" in cmd_types
```

- [x] **Step 10.2: Run test — expect failure (action type not handled)**

```
pytest tests/runtime_v2/lifecycle/test_entry_gate.py::test_entry_changing_update_limit_to_market_emits_cancel_and_new_entry -v
```

- [x] **Step 10.3: Add REPLACE_ENTRY action handling in _apply_action_to_chain**

In `entry_gate.py`, in `_apply_action_to_chain()`:

```python
if action_type == "REPLACE_ENTRY":
    op = action.replace_entry
    if op:
        return self._apply_replace_entry(enriched, chain, action, active_commands)
    return self._review_chain(enriched, chain, "replace_entry_missing_payload")
```

Add method `_apply_replace_entry()`:

```python
def _apply_replace_entry(
    self,
    enriched: EnrichedCanonicalMessage,
    chain: TradeChain,
    action,
    active_commands: list[ExecutionCommand],
) -> UpdateChainResult:
    from src.runtime_v2.lifecycle.diff_engine import ExecutionPlanDiffEngine
    chain_id = chain.trade_chain_id
    cmid = enriched.canonical_message_id

    try:
        risk_snap = json.loads(chain.risk_snapshot_json or "{}")
        sl_price = float(risk_snap.get("sl_price") or risk_snap.get("expected_stop_price") or 0.0)
        risk_total = float(risk_snap.get("risk_amount", 0.0))
        risk_remaining = max(0.0, risk_total - chain.risk_already_realized)
    except Exception:
        return self._review_chain(enriched, chain, "replace_entry_invalid_risk_snapshot")

    # Build target plan from the action payload
    try:
        target_plan_json = self._build_target_plan_from_action(chain, action)
    except Exception:
        return self._review_chain(enriched, chain, "replace_entry_plan_build_failed")

    try:
        engine = ExecutionPlanDiffEngine()
        diff_actions = engine.diff(
            chain.plan_state_json,
            target_plan_json,
            risk_remaining=risk_remaining,
            sl_price=sl_price,
        )
    except ValueError as e:
        return self._review_chain(enriched, chain, f"replace_entry_diff_error:{e}")

    commands: list[ExecutionCommand] = []
    for diff_action in diff_actions:
        if diff_action["action"] == "cancel_pending_entry":
            coid = diff_action.get("client_order_id")
            commands.append(ExecutionCommand(
                trade_chain_id=chain_id,
                command_type="CANCEL_PENDING_ENTRY",
                payload_json=json.dumps({
                    "symbol": chain.symbol, "side": chain.side,
                    "entry_client_order_id": coid,
                }),
                idempotency_key=f"cancel_entry:{chain_id}:{cmid}:seq{diff_action['sequence']}",
            ))
        elif diff_action["action"] == "replace_entry_leg":
            seq = diff_action["sequence"]
            new_qty = diff_action.get("new_qty")
            new_type = diff_action["new_entry_type"]
            # Rebuild entry command for this leg using EntryCommandFactory
            try:
                rs = json.loads(chain.risk_snapshot_json or "{}")
                leg_snap = next(
                    (l for l in rs.get("legs", []) if l["sequence"] == seq), {}
                )
                if new_qty is not None:
                    leg_snap = {**leg_snap, "qty": new_qty, "qty_mode": "fixed"}
                factory = EntryCommandFactory()
                # Build a minimal single-leg command set
                from src.runtime_v2.signal_enrichment.models import EnrichedEntryLeg
                new_price = diff_action.get("new_price")
                from src.parser_v2.contracts.entities import Price
                leg_price = Price(raw=str(new_price), value=new_price) if new_price else None
                temp_leg = EnrichedEntryLeg(sequence=1, entry_type=new_type, price=leg_price, weight=1.0)
                single_snap = {"legs": [{**leg_snap, "sequence": 1}]}
                final_tp = json.loads(chain.plan_state_json or "{}").get("final_tp")
                tp_list = []
                if final_tp:
                    from src.parser_v2.contracts.entities import TakeProfit
                    tp_list = [TakeProfit(sequence=1, price=Price(raw=str(final_tp), value=final_tp))]
                hedge = bool(rs.get("hedge_mode", False))
                cmds_for_leg = factory.build_entry_commands(
                    enrichment_id=cmid,
                    symbol=chain.symbol, side=chain.side,
                    entries=[temp_leg], take_profits=tp_list,
                    sl_price=sl_price,
                    leverage=int(rs.get("leverage", 1)),
                    hedge_mode=hedge,
                    position_idx=self.resolve_position_idx(chain.side, hedge),
                    risk_snapshot=single_snap,
                )
                commands.extend(cmds_for_leg)
            except Exception:
                return self._review_chain(enriched, chain, "replace_entry_cmd_build_failed")

    event = LifecycleEvent(
        trade_chain_id=chain_id,
        event_type="TELEGRAM_UPDATE_ACCEPTED",
        source_type="telegram_update",
        source_id=str(cmid),
        payload_json=json.dumps({"action": "REPLACE_ENTRY"}),
        idempotency_key=f"update_replace_entry:{chain_id}:{cmid}",
    )
    return UpdateChainResult(
        trade_chain_id=chain_id,
        new_lifecycle_state=None,
        new_be_protection_status=None,
        lifecycle_events=[event],
        execution_commands=commands,
    )


def _build_target_plan_from_action(self, chain: TradeChain, action) -> str:
    """Build target plan_state_json representing the desired new state."""
    import json as _json
    current = _json.loads(chain.plan_state_json or "{}")
    replace_op = action.replace_entry
    # Clone current plan, update the target leg
    target_legs = []
    for leg in current.get("legs", []):
        if leg["sequence"] == 1 and replace_op:
            new_leg = {
                **leg,
                "entry_type": replace_op.new_entry_type or leg["entry_type"],
                "price": replace_op.new_price if replace_op.new_price is not None else leg.get("price"),
                "qty_mode": "deferred_market" if (replace_op.new_entry_type or "").upper() == "MARKET" else "fixed",
                "qty": None if (replace_op.new_entry_type or "").upper() == "MARKET" else leg.get("qty"),
            }
            target_legs.append(new_leg)
        else:
            target_legs.append(leg)
    target = {**current, "legs": target_legs}
    return _json.dumps(target)
```

- [x] **Step 10.4: Run tests**

```
pytest tests/runtime_v2/lifecycle/test_entry_gate.py -v
```
Expected: new test passes, existing tests pass.

- [ ] **Step 10.5: Commit**

```
git add src/runtime_v2/lifecycle/entry_gate.py tests/runtime_v2/lifecycle/test_entry_gate.py
git commit -m "feat(lifecycle): handle REPLACE_ENTRY update via ExecutionPlanDiffEngine for Caso A/B"
```

---

## Phase 5 — Remove legacy routing

### Task 11: Remove execution_mode-based builder dispatch

**Files:**
- Modify: `src/runtime_v2/lifecycle/entry_gate.py`
- Modify: `src/runtime_v2/lifecycle/event_processor.py`
- Modify: `tests/runtime_v2/lifecycle/test_entry_gate.py`

- [x] **Step 11.1: Verify no tests rely on legacy mode builders**

```
pytest tests/runtime_v2/lifecycle/ -v --co 2>&1 | grep -i "legacy\|a_sequential\|b_entry\|c_native"
```
If any tests use these modes: update or delete them first (they test removed behavior).

- [x] **Step 11.2: Remove from entry_gate.py**

Remove:
- `_LEGACY_EXECUTION_MODES` frozenset
- `_use_legacy_routing` property
- `_build_legacy_commands()` method
- The `if self._use_legacy_routing:` branch in `_build_entry_commands()`
- The `simple_attached_enabled: bool | None = None` parameter → replace with `simple_attached_enabled: bool = True`
- The `if not (self._simple_attached_enabled is True and sl_price is not None):` fallback to `_build_d_commands` (keep `_build_d_commands` callable for D_POSITION_TPSL explicit config)

Remove `execution_mode` parameter from `__init__`:

```python
def __init__(
    self,
    risk_engine: RiskCapacityEngine,
    exchange_port: ExchangeDataPort,
    simple_attached_enabled: bool = True,
) -> None:
    self._risk = risk_engine
    self._port = exchange_port
    self._simple_attached_enabled = simple_attached_enabled
```

In `process_signal()`, `chain_execution_mode` becomes:

```python
chain_execution_mode = "UNIFIED_PLAN" if sl_price_for_decision is not None else "D_POSITION_TPSL"
```

- [x] **Step 11.3: Remove execution_mode branch in event_processor.py**

In `_process_entry_filled()`, the `rebuild_policy` from `plan_state_json` already drives the rebuilder (Phase 3). Remove any remaining `if chain.execution_mode == "D_MULTI_ENTRY_MULTI_TP":` checks if they exist.

- [x] **Step 11.4: Run full test suite**

```
pytest tests/runtime_v2/ -v
```
Expected: all pass.

- [x] **Step 11.5: Update callers of LifecycleEntryGate**

In `src/runtime_v2/lifecycle/workers.py`, remove `execution_mode` parameter from `LifecycleEntryGate` constructor call if present.

In `src/runtime_v2/lifecycle/entry_gate.py` `LifecycleGateWorker`, verify the gate is instantiated without the removed parameter.

- [x] **Step 11.6: Run full suite again**

```
pytest tests/runtime_v2/ -v
```
Expected: all pass.

- [ ] **Step 11.7: Commit**

```
git add src/runtime_v2/lifecycle/entry_gate.py src/runtime_v2/lifecycle/event_processor.py src/runtime_v2/lifecycle/workers.py tests/runtime_v2/lifecycle/test_entry_gate.py
git commit -m "refactor(lifecycle): remove legacy execution_mode routing, execution_mode is diagnostic label only"
```

---

## Self-Review Checklist

### Spec coverage
| Requirement | Task |
|---|---|
| All 8 cases → single ExecutionPlan | Task 1, 4 |
| Single TP rule (NONE) | Task 4, 6 |
| Multi TP rule (ON_EACH_ENTRY_FILL) | Task 4, 6, 7 |
| Entry-changing updates via replan+diff | Task 9, 10 |
| risk_remaining drives qty on non-filled legs | Task 8, 9 |
| MARKET deferred with FULL attached | Task 4 (test_case_3a) |
| Bybit BE move uses trading_stop_move_sl | Existing adapter code — no change needed |
| leg sequence>1 = plain PLACE_ENTRY | Task 4, 5 |
| plan_state_json is diff engine source | Task 1, 2, 9 |
| Backward compat during migration | Phases 1-2: shadow before replacing |

### Notes on behavioral delta vs current code
- **D_MULTI_ENTRY_1TP**: old = all legs with FULL attached; new = leg 1 FULL, legs 2+ plain PLACE_ENTRY
- **D_MULTI_ENTRY_MULTI_TP**: old = all legs with PARTIAL_TP attached; new = leg 1 FULL, legs 2+ plain PLACE_ENTRY; intermediate TPs after fill not at signal time
- **C_MULTI_TP**: old = intermediate TPs emitted at signal time (WAITING_POSITION); new = emitted after fill
- **C_SIMPLE_ATTACHED**: no behavioral change (parity)
- **BE move testnet verification**: AC #7 requires testnet validation that `trading_stop_move_sl` preserves TP — this is an operational test outside the code plan

### Acceptance criteria verification
1. ✅ All 8 cases use ExecutionPlanBuilder + EntryCommandFactory → Tasks 1, 4
2. ✅ One TP rule per 1/N TP → rebuild_policy field in plan_state_json
3. ✅ Entry-changing updates via DiffEngine → Task 9, 10
4. ✅ Non-filled leg qty from risk_remaining → DiffEngine.diff() computes new_qty
5. ✅ MARKET+N LIMIT+N TP same architecture → Task 4 test_case_4b
6. ✅ Deferred MARKET supports FULL attached → _build_attached_cmd with qty_mode=deferred_market
7. ⚠️ BE move testnet verification → out of scope for this plan, must be done operationally
