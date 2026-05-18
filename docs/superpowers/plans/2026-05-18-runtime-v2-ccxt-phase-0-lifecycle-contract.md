# Runtime V2 CCXT Phase 0 — Lifecycle Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the runtime_v2 lifecycle contract to be internally coherent — quantity tracking, execution modes A/B/C, BE state separation, correct client_order_id roles — so Fase 1 can introduce CcxtBybitAdapter without fixing semantics.

**Architecture:** TDD-first, no CCXT real, no live trading. All changes are internal contract corrections and DB schema additions. FakeAdapter drives all tests. Fake adapter auto-responds PROTECTIVE_ORDERS_SYNCED to SYNC_PROTECTIVE_ORDERS.

**Tech Stack:** Python 3.12, Pydantic v2, SQLite (aiosqlite + raw sqlite3), pytest, pytest-asyncio.

---

## File Map

| Action | File | Purpose |
|--------|------|---------|
| Create | `db/ops_migrations/003_ops_quantity_runtime.sql` | Add qty runtime columns + execution_mode |
| Modify | `src/runtime_v2/lifecycle/models.py` | TradeChain fields, CommandType, LifecycleEventType, LEGACY_BE_STATES |
| Modify | `src/runtime_v2/lifecycle/entry_gate.py` | Gate constructor, _build_entry_commands A/B/C, _apply_move_to_be, _apply_cancel_pending, _persist_signal INSERT |
| Modify | `src/runtime_v2/lifecycle/event_processor.py` | EventProcessorResult qty fields, all handlers |
| Modify | `src/runtime_v2/lifecycle/workers.py` | _persist_result qty, WAITING_POSITION release, LEGACY_BE skip |
| Modify | `src/runtime_v2/execution_gateway/client_order_id.py` | Add roles: exit_partial, exit_full, sync |
| Modify | `src/runtime_v2/execution_gateway/gateway.py` | _ROLE_MAP + _CAPABILITY_MAP for new commands |
| Modify | `src/runtime_v2/execution_gateway/event_sync.py` | New role handlers for exit_partial/exit_full/sync |
| Modify | `src/runtime_v2/execution_gateway/adapters/fake.py` | SYNC_PROTECTIVE_ORDERS → immediate fill |
| Modify | `src/runtime_v2/execution_gateway/models.py` | AdapterCapabilities: sync_protective_orders |

---

## Task 1: Migration 003 + TradeChain model new fields

**Files:**
- Create: `db/ops_migrations/003_ops_quantity_runtime.sql`
- Modify: `src/runtime_v2/lifecycle/models.py` (TradeChain class)
- Test: `tests/runtime_v2/lifecycle/test_models.py`

- [ ] **Step 1.1: Write failing test — TradeChain round-trip with new fields**

```python
# tests/runtime_v2/lifecycle/test_models.py
def test_trade_chain_has_qty_runtime_fields():
    chain = TradeChain(
        source_enrichment_id=1, canonical_message_id=2, raw_message_id=3,
        trader_id="t1", account_id="acc1", symbol="BTC/USDT", side="LONG",
        lifecycle_state="WAITING_ENTRY", entry_mode="ONE_SHOT",
        management_plan_json="{}",
        planned_entry_qty=0.01,
        filled_entry_qty=0.005,
        open_position_qty=0.005,
        closed_position_qty=0.0,
        last_position_sync_at=None,
        execution_mode="a_sequential",
    )
    assert chain.planned_entry_qty == 0.01
    assert chain.filled_entry_qty == 0.005
    assert chain.open_position_qty == 0.005
    assert chain.closed_position_qty == 0.0
    assert chain.last_position_sync_at is None
    assert chain.execution_mode == "a_sequential"

def test_trade_chain_qty_defaults_to_zero():
    chain = TradeChain(
        source_enrichment_id=1, canonical_message_id=2, raw_message_id=3,
        trader_id="t1", account_id="acc1", symbol="BTC/USDT", side="LONG",
        lifecycle_state="WAITING_ENTRY", entry_mode="ONE_SHOT",
        management_plan_json="{}",
    )
    assert chain.planned_entry_qty == 0.0
    assert chain.filled_entry_qty == 0.0
    assert chain.open_position_qty == 0.0
    assert chain.closed_position_qty == 0.0
    assert chain.execution_mode == "a_sequential"
```

- [ ] **Step 1.2: Run test to verify it fails**

```
.venv\Scripts\python.exe -m pytest tests\runtime_v2\lifecycle\test_models.py::test_trade_chain_has_qty_runtime_fields tests\runtime_v2\lifecycle\test_models.py::test_trade_chain_qty_defaults_to_zero -v
```

Expected: FAIL — `TradeChain.__init__() got unexpected keyword argument 'planned_entry_qty'`

- [ ] **Step 1.3: Create migration file**

```sql
-- db/ops_migrations/003_ops_quantity_runtime.sql
ALTER TABLE ops_trade_chains ADD COLUMN planned_entry_qty REAL NOT NULL DEFAULT 0;
ALTER TABLE ops_trade_chains ADD COLUMN filled_entry_qty REAL NOT NULL DEFAULT 0;
ALTER TABLE ops_trade_chains ADD COLUMN open_position_qty REAL NOT NULL DEFAULT 0;
ALTER TABLE ops_trade_chains ADD COLUMN closed_position_qty REAL NOT NULL DEFAULT 0;
ALTER TABLE ops_trade_chains ADD COLUMN last_position_sync_at TEXT;
ALTER TABLE ops_trade_chains ADD COLUMN execution_mode TEXT NOT NULL DEFAULT 'a_sequential';
```

- [ ] **Step 1.4: Add fields to TradeChain in `src/runtime_v2/lifecycle/models.py`**

Add after `risk_snapshot_json: str = "{}"`:
```python
    planned_entry_qty: float = 0.0
    filled_entry_qty: float = 0.0
    open_position_qty: float = 0.0
    closed_position_qty: float = 0.0
    last_position_sync_at: datetime | None = None
    execution_mode: str = "a_sequential"
```

- [ ] **Step 1.5: Run test to verify it passes**

```
.venv\Scripts\python.exe -m pytest tests\runtime_v2\lifecycle\test_models.py::test_trade_chain_has_qty_runtime_fields tests\runtime_v2\lifecycle\test_models.py::test_trade_chain_qty_defaults_to_zero -v
```

Expected: PASS

- [ ] **Step 1.6: Write failing test — migration creates columns in real DB**

```python
# tests/runtime_v2/lifecycle/test_models.py  (add to same file)
import sqlite3, os, tempfile

def test_migration_003_creates_qty_columns():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        conn = sqlite3.connect(db_path)
        # Minimal table to allow ALTER
        conn.execute("""
            CREATE TABLE ops_trade_chains (
                trade_chain_id INTEGER PRIMARY KEY,
                source_enrichment_id INTEGER NOT NULL,
                lifecycle_state TEXT NOT NULL,
                management_plan_json TEXT NOT NULL DEFAULT '{}',
                risk_snapshot_json TEXT NOT NULL DEFAULT '{}'
            )
        """)
        conn.commit()
        migration = open("db/ops_migrations/003_ops_quantity_runtime.sql").read()
        for stmt in migration.split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(stmt)
        conn.commit()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(ops_trade_chains)")}
        assert "planned_entry_qty" in cols
        assert "filled_entry_qty" in cols
        assert "open_position_qty" in cols
        assert "closed_position_qty" in cols
        assert "last_position_sync_at" in cols
        assert "execution_mode" in cols
        conn.close()
    finally:
        os.unlink(db_path)
```

- [ ] **Step 1.7: Run test to verify it passes** (migration file already created)

```
.venv\Scripts\python.exe -m pytest tests\runtime_v2\lifecycle\test_models.py::test_migration_003_creates_qty_columns -v
```

Expected: PASS

- [ ] **Step 1.8: Commit**

```
git add db/ops_migrations/003_ops_quantity_runtime.sql src/runtime_v2/lifecycle/models.py tests/runtime_v2/lifecycle/test_models.py
git commit -m "feat(lifecycle): add qty runtime fields and execution_mode to TradeChain + migration 003"
```

---

## Task 2: Contract additions — CommandType, LifecycleEventType, ExchangeEventType

**Files:**
- Modify: `src/runtime_v2/lifecycle/models.py`
- Test: `tests/runtime_v2/lifecycle/test_models.py`

- [ ] **Step 2.1: Write failing tests — new types exist**

```python
# tests/runtime_v2/lifecycle/test_models.py (add)
from src.runtime_v2.lifecycle.models import (
    CommandType, LifecycleEventType, ExchangeEventType, LEGACY_BE_STATES,
)

def test_sync_protective_orders_in_command_type():
    cmd = ExecutionCommand(
        trade_chain_id=1,
        command_type="SYNC_PROTECTIVE_ORDERS",
        idempotency_key="k1",
    )
    assert cmd.command_type == "SYNC_PROTECTIVE_ORDERS"

def test_new_lifecycle_event_types_exist():
    from src.runtime_v2.lifecycle.models import LifecycleEvent
    ev = LifecycleEvent(
        event_type="POSITION_SIZE_UPDATED",
        source_type="exchange_event",
        idempotency_key="k2",
    )
    assert ev.event_type == "POSITION_SIZE_UPDATED"

def test_exchange_event_type_literals():
    # ExchangeEventType must include all spec exchange events
    import typing
    args = typing.get_args(ExchangeEventType)
    required = {
        "ENTRY_FILLED", "TP_FILLED", "SL_FILLED",
        "CLOSE_PARTIAL_FILLED", "CLOSE_FULL_FILLED",
        "STOP_MOVED_CONFIRMED", "PENDING_ENTRY_CANCELLED_CONFIRMED",
        "PROTECTIVE_ORDERS_SYNCED", "ORDER_REJECTED", "ORDER_CANCELLED",
    }
    assert required.issubset(set(args))

def test_legacy_be_states_constant():
    assert "BE_MOVE_PENDING" in LEGACY_BE_STATES
    assert "PROTECTED_BE" in LEGACY_BE_STATES
```

- [ ] **Step 2.2: Run tests to verify they fail**

```
.venv\Scripts\python.exe -m pytest tests\runtime_v2\lifecycle\test_models.py::test_sync_protective_orders_in_command_type tests\runtime_v2\lifecycle\test_models.py::test_new_lifecycle_event_types_exist tests\runtime_v2\lifecycle\test_models.py::test_exchange_event_type_literals tests\runtime_v2\lifecycle\test_models.py::test_legacy_be_states_constant -v
```

Expected: FAIL

- [ ] **Step 2.3: Update `src/runtime_v2/lifecycle/models.py`**

Replace `CommandType`:
```python
CommandType = Literal[
    "PLACE_ENTRY", "PLACE_PROTECTIVE_STOP", "PLACE_TAKE_PROFIT",
    "MOVE_STOP_TO_BREAKEVEN", "MOVE_STOP", "CANCEL_PENDING_ENTRY",
    "CLOSE_PARTIAL", "CLOSE_FULL", "SYNC_PROTECTIVE_ORDERS",
]
```

Replace `LifecycleEventType`:
```python
LifecycleEventType = Literal[
    "SIGNAL_ACCEPTED", "TRADE_CHAIN_CREATED", "ENTRY_COMMAND_CREATED",
    "ENTRY_FILLED", "TP_FILLED", "SL_FILLED", "TIMEOUT_REACHED",
    "TELEGRAM_UPDATE_ACCEPTED", "BE_MOVE_REQUESTED",
    "NOOP_ALREADY_PROTECTED_BE", "NOOP_DUPLICATE_COMMAND",
    "NOOP_ALREADY_CLOSED", "NOOP_NOT_PENDING", "NOOP_NO_APPLICABLE_TARGET",
    "REVIEW_REQUIRED",
    "POSITION_SIZE_UPDATED", "ENTRY_AVG_PRICE_UPDATED",
    "PROTECTIVE_SYNC_REQUESTED", "STOP_MOVE_CONFIRMED", "PENDING_ENTRY_CANCELLED",
]
```

Add after `LifecycleEventType`:
```python
ExchangeEventType = Literal[
    "ENTRY_FILLED", "TP_FILLED", "SL_FILLED",
    "CLOSE_PARTIAL_FILLED", "CLOSE_FULL_FILLED",
    "STOP_MOVED_CONFIRMED", "PENDING_ENTRY_CANCELLED_CONFIRMED",
    "PROTECTIVE_ORDERS_SYNCED", "ORDER_REJECTED", "ORDER_CANCELLED",
]

LEGACY_BE_STATES: frozenset[str] = frozenset({"BE_MOVE_PENDING", "PROTECTED_BE"})
```

Add `ExchangeEventType`, `LEGACY_BE_STATES` to `__all__`.

- [ ] **Step 2.4: Run tests to verify they pass**

```
.venv\Scripts\python.exe -m pytest tests\runtime_v2\lifecycle\test_models.py -v
```

Expected: all PASS

- [ ] **Step 2.5: Commit**

```
git add src/runtime_v2/lifecycle/models.py tests/runtime_v2/lifecycle/test_models.py
git commit -m "feat(lifecycle): add SYNC_PROTECTIVE_ORDERS, ExchangeEventType, LEGACY_BE_STATES to contract"
```

---

## Task 3: BE state separation — gate + processor + worker skip

**Files:**
- Modify: `src/runtime_v2/lifecycle/entry_gate.py`
- Modify: `src/runtime_v2/lifecycle/event_processor.py`
- Modify: `src/runtime_v2/lifecycle/workers.py`
- Test: `tests/runtime_v2/lifecycle/test_entry_gate.py`
- Test: `tests/runtime_v2/lifecycle/test_event_processor.py`

- [ ] **Step 3.1: Write failing tests**

```python
# tests/runtime_v2/lifecycle/test_entry_gate.py (add or create)
from src.runtime_v2.lifecycle.entry_gate import LifecycleEntryGate
from src.runtime_v2.lifecycle.models import TradeChain

def _make_chain(state="OPEN") -> TradeChain:
    return TradeChain(
        trade_chain_id=10, source_enrichment_id=1, canonical_message_id=2,
        raw_message_id=3, trader_id="t1", account_id="acc1",
        symbol="BTC/USDT", side="LONG", lifecycle_state=state,
        entry_mode="ONE_SHOT", management_plan_json='{"be_trigger": null, "be_buffer_pct": 0.0}',
        entry_avg_price=50000.0,
    )

def _make_enriched_update(action_type="SET_STOP"):
    """Minimal EnrichedCanonicalMessage for update testing."""
    from unittest.mock import MagicMock
    enriched = MagicMock()
    enriched.enrichment_id = 99
    enriched.canonical_message_id = 55
    enriched.trader_id = "t1"
    action = MagicMock()
    action.action_type = action_type
    action.set_stop = MagicMock()
    action.set_stop.target_type = "ENTRY"
    tag = MagicMock()
    tag.actions = [action]
    tag.targeting.scope_hint = "SYMBOL"
    tag.targeting.symbols = {"BTC/USDT"}
    tag.targeting.explicit_ids = None
    enriched.enriched_actions = [tag]
    return enriched

def test_move_to_be_does_not_set_lifecycle_state():
    from src.runtime_v2.lifecycle.risk_capacity import RiskCapacityEngine
    from src.runtime_v2.lifecycle.static_exchange_data_port import StaticExchangeDataPort
    gate = LifecycleEntryGate(
        risk_engine=RiskCapacityEngine({}),
        exchange_port=StaticExchangeDataPort(),
    )
    chain = _make_chain("OPEN")
    enriched = _make_enriched_update("SET_STOP")
    result = gate.process_update(enriched, [chain], {10: []})
    assert len(result.chain_results) == 1
    cr = result.chain_results[0]
    # Must NOT set lifecycle_state to a BE state
    assert cr.new_lifecycle_state is None
    # Must set be_protection_status
    assert cr.new_be_protection_status == "BE_MOVE_PENDING"
```

```python
# tests/runtime_v2/lifecycle/test_event_processor.py (add or create)
from src.runtime_v2.lifecycle.event_processor import LifecycleEventProcessor
from src.runtime_v2.lifecycle.models import ExchangeEvent, TradeChain
import json

def _make_chain_open() -> TradeChain:
    return TradeChain(
        trade_chain_id=10, source_enrichment_id=1, canonical_message_id=2,
        raw_message_id=3, trader_id="t1", account_id="acc1",
        symbol="BTC/USDT", side="LONG", lifecycle_state="OPEN",
        entry_mode="ONE_SHOT",
        management_plan_json='{"be_trigger": "tp1", "be_buffer_pct": 0.0, "close_distribution": {"mode": "table", "table": {}}}',
        entry_avg_price=50000.0,
        open_position_qty=0.01,
        filled_entry_qty=0.01,
    )

def _make_tp_event(chain_id: int, tp_level: int = 1, is_final: bool = False, fill_qty: float = 0.005) -> ExchangeEvent:
    return ExchangeEvent(
        exchange_event_id=1,
        trade_chain_id=chain_id,
        event_type="TP_FILLED",
        payload_json=json.dumps({
            "tp_level": tp_level, "is_final": is_final,
            "fill_price": 51000.0, "filled_qty": fill_qty,
        }),
        idempotency_key="tp_filled:10:1",
    )

def test_tp_filled_with_be_trigger_does_not_set_lifecycle_state_to_be():
    proc = LifecycleEventProcessor()
    chain = _make_chain_open()
    ev = _make_tp_event(chain.trade_chain_id, tp_level=1, is_final=False, fill_qty=0.005)
    result = proc.process(ev, chain, [])
    # lifecycle_state must be PARTIALLY_CLOSED, never BE_MOVE_PENDING
    assert result.new_lifecycle_state == "PARTIALLY_CLOSED"
    assert result.new_be_protection_status == "BE_MOVE_PENDING"
```

- [ ] **Step 3.2: Run tests to verify they fail**

```
.venv\Scripts\python.exe -m pytest tests\runtime_v2\lifecycle\test_entry_gate.py::test_move_to_be_does_not_set_lifecycle_state tests\runtime_v2\lifecycle\test_event_processor.py::test_tp_filled_with_be_trigger_does_not_set_lifecycle_state_to_be -v
```

Expected: FAIL

- [ ] **Step 3.3: Fix `_apply_move_to_be` in `src/runtime_v2/lifecycle/entry_gate.py`**

In `_apply_move_to_be`, change the return of the success path from:
```python
return UpdateChainResult(
    trade_chain_id=chain_id,
    new_lifecycle_state="BE_MOVE_PENDING",   # ← REMOVE THIS
    new_be_protection_status="BE_MOVE_PENDING",
    ...
)
```
to:
```python
return UpdateChainResult(
    trade_chain_id=chain_id,
    new_lifecycle_state=None,                 # ← NO STATE CHANGE
    new_be_protection_status="BE_MOVE_PENDING",
    ...
)
```

Also update the LifecycleEvent in that path — change `next_state="BE_MOVE_PENDING"` to `next_state=None`.

- [ ] **Step 3.4: Fix `_process_tp_filled` in `src/runtime_v2/lifecycle/event_processor.py`**

Remove the line that sets `new_state = "BE_MOVE_PENDING"` inside the BE trigger block. The lifecycle state must remain `PARTIALLY_CLOSED` when BE is requested. Only `new_be` changes:

```python
# BEFORE (remove this line):
new_state = "BE_MOVE_PENDING"
new_be = "BE_MOVE_PENDING"

# AFTER (keep only):
new_be = "BE_MOVE_PENDING"
# new_state stays "PARTIALLY_CLOSED"
```

- [ ] **Step 3.5: Add LEGACY_BE_STATES skip to `src/runtime_v2/lifecycle/workers.py`**

In `LifecycleEventWorker.run_once`, update the early-exit condition:

```python
from src.runtime_v2.lifecycle.models import TERMINAL_STATES, LEGACY_BE_STATES, ExecutionCommand, LifecycleEvent

# In run_once, change:
if chain is None or chain.lifecycle_state in TERMINAL_STATES:

# To:
if chain is None or chain.lifecycle_state in TERMINAL_STATES or chain.lifecycle_state in LEGACY_BE_STATES:
```

- [ ] **Step 3.6: Run tests to verify they pass**

```
.venv\Scripts\python.exe -m pytest tests\runtime_v2\lifecycle\test_entry_gate.py::test_move_to_be_does_not_set_lifecycle_state tests\runtime_v2\lifecycle\test_event_processor.py::test_tp_filled_with_be_trigger_does_not_set_lifecycle_state_to_be -v
```

Expected: PASS

- [ ] **Step 3.7: Commit**

```
git add src/runtime_v2/lifecycle/entry_gate.py src/runtime_v2/lifecycle/event_processor.py src/runtime_v2/lifecycle/workers.py tests/runtime_v2/lifecycle/test_entry_gate.py tests/runtime_v2/lifecycle/test_event_processor.py
git commit -m "fix(lifecycle): separate BE state from lifecycle_state — gate/processor never produce BE_MOVE_PENDING on chain"
```

---

## Task 4: execution_mode in gate — Mode A/B/C command status

**Files:**
- Modify: `src/runtime_v2/lifecycle/entry_gate.py`
- Test: `tests/runtime_v2/lifecycle/test_entry_gate.py`

- [ ] **Step 4.1: Write failing tests — Mode A/B/C initial command statuses**

```python
# tests/runtime_v2/lifecycle/test_entry_gate.py (add)
from src.runtime_v2.lifecycle.risk_capacity import RiskCapacityEngine
from src.runtime_v2.lifecycle.static_exchange_data_port import StaticExchangeDataPort
from src.runtime_v2.signal_enrichment.models import (
    EnrichedCanonicalMessage, EnrichedSignalPayload, ManagementPlanConfig,
)
from unittest.mock import MagicMock
import json

def _make_risk_decision(size_usdt=500.0, entry_price=50000.0):
    from src.runtime_v2.lifecycle.risk_capacity import RiskDecision
    return RiskDecision(
        passed=True,
        reason=None,
        size_usdt=size_usdt,
        leverage=10,
        risk_snapshot={"entry_price": entry_price, "size_usdt": size_usdt},
    )

def _make_enriched_signal(tp_count: int = 2) -> EnrichedCanonicalMessage:
    from src.runtime_v2.signal_enrichment.models import EnrichedEntryLeg, EnrichedTakeProfit, EnrichedStopLoss
    from src.parser.canonical_v1.models import Price
    entries = [EnrichedEntryLeg(sequence=1, entry_type="LIMIT", price=Price(value=50000.0), weight=1.0)]
    tps = [
        EnrichedTakeProfit(sequence=i+1, price=Price(value=51000.0 + i * 1000))
        for i in range(tp_count)
    ]
    sl = EnrichedStopLoss(price=Price(value=49000.0))
    signal = EnrichedSignalPayload(
        symbol="BTC/USDT", side="LONG", entry_structure="ONE_SHOT",
        entries=entries, take_profits=tps, stop_loss=sl,
    )
    mp = ManagementPlanConfig()
    return EnrichedCanonicalMessage(
        enrichment_id=1, canonical_message_id=2, raw_message_id=3,
        trader_id="t1", account_id="acc1",
        primary_class="SIGNAL", enrichment_decision="PASS",
        enriched_signal=signal, management_plan=mp,
    )

def _make_gate(execution_mode: str) -> LifecycleEntryGate:
    return LifecycleEntryGate(
        risk_engine=RiskCapacityEngine({}),
        exchange_port=StaticExchangeDataPort(),
        execution_mode=execution_mode,
    )

def test_mode_a_sl_and_tp_are_waiting_position():
    gate = _make_gate("a_sequential")
    # Patch risk to return a valid decision
    gate._risk.validate = lambda *a, **kw: _make_risk_decision()
    gate._port.get_account_state = lambda *a: MagicMock(equity_usdt=1000.0, available_balance_usdt=1000.0, total_open_risk_usdt=0.0, total_margin_used_usdt=0.0, source="static", captured_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc))
    gate._port.get_symbol_market_state = lambda *a: MagicMock(symbol="BTC/USDT", mark_price=50000.0, bid=49990.0, ask=50010.0, min_order_size=0.001, price_precision=2, qty_precision=4, source="static", captured_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc))
    enriched = _make_enriched_signal(tp_count=2)
    result = gate.process_signal(enriched, [], "NONE")
    cmds = {c.command_type: c for c in result.execution_commands}
    assert cmds["PLACE_ENTRY"].status == "PENDING"
    assert cmds["PLACE_PROTECTIVE_STOP"].status == "WAITING_POSITION"
    assert all(
        c.status == "WAITING_POSITION"
        for c in result.execution_commands
        if c.command_type == "PLACE_TAKE_PROFIT"
    )

def test_mode_b_sl_pending_tp_waiting_position():
    gate = _make_gate("b_entry_stop_then_tp")
    gate._risk.validate = lambda *a, **kw: _make_risk_decision()
    gate._port.get_account_state = lambda *a: MagicMock(equity_usdt=1000.0, available_balance_usdt=1000.0, total_open_risk_usdt=0.0, total_margin_used_usdt=0.0, source="static", captured_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc))
    gate._port.get_symbol_market_state = lambda *a: MagicMock(symbol="BTC/USDT", mark_price=50000.0, bid=49990.0, ask=50010.0, min_order_size=0.001, price_precision=2, qty_precision=4, source="static", captured_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc))
    enriched = _make_enriched_signal(tp_count=2)
    result = gate.process_signal(enriched, [], "NONE")
    cmds_by_type = {}
    for c in result.execution_commands:
        cmds_by_type.setdefault(c.command_type, []).append(c)
    assert cmds_by_type["PLACE_ENTRY"][0].status == "PENDING"
    assert cmds_by_type["PLACE_PROTECTIVE_STOP"][0].status == "PENDING"
    assert all(c.status == "WAITING_POSITION" for c in cmds_by_type["PLACE_TAKE_PROFIT"])

def test_mode_c_entry_has_native_tpsl_no_sl_command():
    gate = _make_gate("c_native_attached_tpsl")
    gate._risk.validate = lambda *a, **kw: _make_risk_decision()
    gate._port.get_account_state = lambda *a: MagicMock(equity_usdt=1000.0, available_balance_usdt=1000.0, total_open_risk_usdt=0.0, total_margin_used_usdt=0.0, source="static", captured_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc))
    gate._port.get_symbol_market_state = lambda *a: MagicMock(symbol="BTC/USDT", mark_price=50000.0, bid=49990.0, ask=50010.0, min_order_size=0.001, price_precision=2, qty_precision=4, source="static", captured_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc))
    enriched = _make_enriched_signal(tp_count=2)
    result = gate.process_signal(enriched, [], "NONE")
    cmd_types = [c.command_type for c in result.execution_commands]
    # No separate SL command
    assert "PLACE_PROTECTIVE_STOP" not in cmd_types
    # Entry has native_attached_tpsl
    entry_cmd = next(c for c in result.execution_commands if c.command_type == "PLACE_ENTRY")
    entry_payload = json.loads(entry_cmd.payload_json)
    assert entry_payload["native_attached_tpsl"] is True
    assert "attached_stop_loss" in entry_payload
    assert "attached_take_profit" in entry_payload
    # TP intermediates (all but last) are WAITING_POSITION
    tp_cmds = [c for c in result.execution_commands if c.command_type == "PLACE_TAKE_PROFIT"]
    assert len(tp_cmds) == 1  # 2 TPs: last attached, first as WAITING_POSITION
    assert tp_cmds[0].status == "WAITING_POSITION"

def test_mode_c_single_tp_no_intermediate_commands():
    gate = _make_gate("c_native_attached_tpsl")
    gate._risk.validate = lambda *a, **kw: _make_risk_decision()
    gate._port.get_account_state = lambda *a: MagicMock(equity_usdt=1000.0, available_balance_usdt=1000.0, total_open_risk_usdt=0.0, total_margin_used_usdt=0.0, source="static", captured_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc))
    gate._port.get_symbol_market_state = lambda *a: MagicMock(symbol="BTC/USDT", mark_price=50000.0, bid=49990.0, ask=50010.0, min_order_size=0.001, price_precision=2, qty_precision=4, source="static", captured_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc))
    enriched = _make_enriched_signal(tp_count=1)
    result = gate.process_signal(enriched, [], "NONE")
    cmd_types = [c.command_type for c in result.execution_commands]
    assert "PLACE_TAKE_PROFIT" not in cmd_types  # only 1 TP → attached, no separate
    assert "PLACE_PROTECTIVE_STOP" not in cmd_types

def test_process_signal_writes_execution_mode_to_chain():
    gate = _make_gate("b_entry_stop_then_tp")
    gate._risk.validate = lambda *a, **kw: _make_risk_decision()
    gate._port.get_account_state = lambda *a: MagicMock(equity_usdt=1000.0, available_balance_usdt=1000.0, total_open_risk_usdt=0.0, total_margin_used_usdt=0.0, source="static", captured_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc))
    gate._port.get_symbol_market_state = lambda *a: MagicMock(symbol="BTC/USDT", mark_price=50000.0, bid=49990.0, ask=50010.0, min_order_size=0.001, price_precision=2, qty_precision=4, source="static", captured_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc))
    enriched = _make_enriched_signal()
    result = gate.process_signal(enriched, [], "NONE")
    assert result.trade_chain is not None
    assert result.trade_chain.execution_mode == "b_entry_stop_then_tp"
```

- [ ] **Step 4.2: Run tests to verify they fail**

```
.venv\Scripts\python.exe -m pytest tests\runtime_v2\lifecycle\test_entry_gate.py::test_mode_a_sl_and_tp_are_waiting_position tests\runtime_v2\lifecycle\test_entry_gate.py::test_mode_b_sl_pending_tp_waiting_position tests\runtime_v2\lifecycle\test_entry_gate.py::test_mode_c_entry_has_native_tpsl_no_sl_command tests\runtime_v2\lifecycle\test_entry_gate.py::test_mode_c_single_tp_no_intermediate_commands tests\runtime_v2\lifecycle\test_entry_gate.py::test_process_signal_writes_execution_mode_to_chain -v
```

Expected: FAIL

- [ ] **Step 4.3: Add `execution_mode` to `LifecycleEntryGate.__init__`**

```python
class LifecycleEntryGate:
    def __init__(
        self,
        risk_engine: RiskCapacityEngine,
        exchange_port: ExchangeDataPort,
        execution_mode: str = "a_sequential",
    ) -> None:
        self._risk = risk_engine
        self._port = exchange_port
        self._execution_mode = execution_mode
```

- [ ] **Step 4.4: Write `execution_mode` to chain in `process_signal`**

In `process_signal`, update `chain = TradeChain(...)` to include:
```python
        size_usdt = float(decision.size_usdt or 0.0)
        fallback_entry_price = float(decision.risk_snapshot.get("entry_price") or 1.0)
        planned_qty = size_usdt / fallback_entry_price if fallback_entry_price > 0 else 0.0

        chain = TradeChain(
            # ... existing fields ...
            planned_entry_qty=planned_qty,
            execution_mode=self._execution_mode,
        )
```

- [ ] **Step 4.5: Rewrite `_build_entry_commands` for Mode A/B/C**

Replace the existing `_build_entry_commands` method:

```python
def _build_entry_commands(
    self,
    enriched: EnrichedCanonicalMessage,
    decision,
) -> list[ExecutionCommand]:
    signal = enriched.enriched_signal
    management_plan = enriched.management_plan or ManagementPlanConfig()
    eid = enriched.enrichment_id
    mode = self._execution_mode

    commands: list[ExecutionCommand] = []

    tp_count = len(signal.take_profits)
    close_pcts = self._get_close_pcts(management_plan, tp_count)
    size_usdt = float(decision.size_usdt or 0.0)
    fallback_entry_price = float(decision.risk_snapshot.get("entry_price") or 0.0)
    total_qty = self._qty_from_notional(size_usdt, fallback_entry_price)

    last_tp = signal.take_profits[-1] if signal.take_profits else None
    sl_price = signal.stop_loss.price.value if signal.stop_loss and signal.stop_loss.price else None

    # ── ENTRY LEGS ──────────────────────────────────────────────
    for leg in signal.entries:
        leg_price = leg.price.value if leg.price else fallback_entry_price
        leg_notional = size_usdt * float(leg.weight or 0.0)
        payload: dict = {
            "symbol": signal.symbol,
            "side": signal.side,
            "entry_type": leg.entry_type,
            "price": leg.price.value if leg.price else None,
            "qty": self._qty_from_notional(leg_notional, leg_price),
            "weight": leg.weight,
            "sequence": leg.sequence,
        }
        if mode == "c_native_attached_tpsl":
            payload["native_attached_tpsl"] = True
            payload["attached_stop_loss"] = sl_price
            if last_tp and last_tp.price:
                payload["attached_take_profit"] = last_tp.price.value
                payload["attached_take_profit_sequence"] = last_tp.sequence
        commands.append(ExecutionCommand(
            trade_chain_id=0,
            command_type="PLACE_ENTRY",
            status="PENDING",
            payload_json=json.dumps(payload),
            idempotency_key=f"place_entry:{eid}:leg{leg.sequence}",
        ))

    # ── PROTECTIVE STOP ─────────────────────────────────────────
    if sl_price and mode != "c_native_attached_tpsl":
        sl_status: CommandStatus = "WAITING_POSITION" if mode == "a_sequential" else "PENDING"
        commands.append(ExecutionCommand(
            trade_chain_id=0,
            command_type="PLACE_PROTECTIVE_STOP",
            status=sl_status,
            payload_json=json.dumps({
                "symbol": signal.symbol, "side": signal.side,
                "stop_price": sl_price, "qty": total_qty, "reduce_only": True,
            }),
            idempotency_key=f"place_stop:{eid}",
        ))

    # ── TAKE PROFITS ────────────────────────────────────────────
    for i, tp in enumerate(signal.take_profits):
        is_last = (i == len(signal.take_profits) - 1)
        if mode == "c_native_attached_tpsl" and is_last:
            continue  # last TP is attached to entry
        close_pct = close_pcts[i] if i < len(close_pcts) else (100.0 / tp_count)
        price = tp.price.value if tp.price else None
        commands.append(ExecutionCommand(
            trade_chain_id=0,
            command_type="PLACE_TAKE_PROFIT",
            status="WAITING_POSITION",
            payload_json=json.dumps({
                "symbol": signal.symbol, "side": signal.side,
                "price": price, "tp_price": price,
                "sequence": tp.sequence,
                "close_pct": close_pct,
                "qty": total_qty * float(close_pct) / 100.0,
                "reduce_only": True,
            }),
            idempotency_key=f"place_tp:{eid}:tp{tp.sequence}",
        ))

    return commands
```

- [ ] **Step 4.6: Update `LifecycleGateWorker._persist_signal` INSERT to include new fields**

In `_persist_signal`, find the `INSERT OR IGNORE INTO ops_trade_chains` statement and update it:

```python
cursor = conn.execute(
    """
    INSERT OR IGNORE INTO ops_trade_chains (
        source_enrichment_id, canonical_message_id, raw_message_id,
        trader_id, account_id, symbol, side, lifecycle_state, entry_mode,
        entry_avg_price, current_stop_price, expected_stop_price,
        be_protection_status, entry_timeout_at, management_plan_json,
        risk_snapshot_json, planned_entry_qty, execution_mode,
        created_at, updated_at
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """,
    (
        c.source_enrichment_id, c.canonical_message_id, c.raw_message_id,
        c.trader_id, c.account_id, c.symbol, c.side,
        c.lifecycle_state, c.entry_mode,
        c.entry_avg_price, c.current_stop_price, c.expected_stop_price,
        c.be_protection_status,
        c.entry_timeout_at.isoformat() if c.entry_timeout_at else None,
        c.management_plan_json, c.risk_snapshot_json,
        c.planned_entry_qty, c.execution_mode,
        now, now,
    ),
)
```

- [ ] **Step 4.7: Run tests**

```
.venv\Scripts\python.exe -m pytest tests\runtime_v2\lifecycle\test_entry_gate.py -v
```

Expected: all new tests PASS

- [ ] **Step 4.8: Commit**

```
git add src/runtime_v2/lifecycle/entry_gate.py tests/runtime_v2/lifecycle/test_entry_gate.py
git commit -m "feat(lifecycle): execution_mode in gate — Mode A/B/C command status and Mode C native TPSL"
```

---

## Task 5: EventProcessorResult — qty fields + LifecycleEventWorker persist

**Files:**
- Modify: `src/runtime_v2/lifecycle/event_processor.py`
- Modify: `src/runtime_v2/lifecycle/workers.py`
- Test: `tests/runtime_v2/lifecycle/test_event_processor.py`
- Test: `tests/runtime_v2/lifecycle/test_workers.py`

- [ ] **Step 5.1: Write failing tests — EventProcessorResult has qty fields**

```python
# tests/runtime_v2/lifecycle/test_event_processor.py (add)
from src.runtime_v2.lifecycle.event_processor import EventProcessorResult

def test_event_processor_result_has_qty_fields():
    r = EventProcessorResult(
        new_lifecycle_state=None,
        new_be_protection_status=None,
        entry_avg_price=None,
        current_stop_price=None,
        lifecycle_events=[],
        execution_commands=[],
        new_filled_entry_qty=0.01,
        new_open_position_qty=0.01,
        new_closed_position_qty=0.0,
        release_waiting_position=True,
    )
    assert r.new_filled_entry_qty == 0.01
    assert r.new_open_position_qty == 0.01
    assert r.new_closed_position_qty == 0.0
    assert r.release_waiting_position is True

def test_event_processor_result_qty_defaults_to_none():
    r = EventProcessorResult(
        new_lifecycle_state=None,
        new_be_protection_status=None,
        entry_avg_price=None,
        current_stop_price=None,
        lifecycle_events=[],
        execution_commands=[],
    )
    assert r.new_filled_entry_qty is None
    assert r.new_open_position_qty is None
    assert r.new_closed_position_qty is None
    assert r.release_waiting_position is False
```

- [ ] **Step 5.2: Run tests to verify they fail**

```
.venv\Scripts\python.exe -m pytest tests\runtime_v2\lifecycle\test_event_processor.py::test_event_processor_result_has_qty_fields tests\runtime_v2\lifecycle\test_event_processor.py::test_event_processor_result_qty_defaults_to_none -v
```

Expected: FAIL

- [ ] **Step 5.3: Add qty fields to `EventProcessorResult` in `src/runtime_v2/lifecycle/event_processor.py`**

```python
@dataclass
class EventProcessorResult:
    new_lifecycle_state: LifecycleState | None
    new_be_protection_status: BeProtectionStatus | None
    entry_avg_price: float | None
    current_stop_price: float | None
    lifecycle_events: list[LifecycleEvent]
    execution_commands: list[ExecutionCommand]
    new_filled_entry_qty: float | None = None
    new_open_position_qty: float | None = None
    new_closed_position_qty: float | None = None
    release_waiting_position: bool = False
```

- [ ] **Step 5.4: Update `LifecycleEventWorker._persist_result` to persist qty and release WAITING_POSITION**

In `src/runtime_v2/lifecycle/workers.py`, update `_persist_result`:

```python
def _persist_result(self, chain_id: int, result: EventProcessorResult) -> None:
    now = _now()
    conn = sqlite3.connect(self._ops_db)
    try:
        with conn:
            has_chain_update = (
                result.new_lifecycle_state is not None
                or result.new_be_protection_status is not None
                or result.entry_avg_price is not None
                or result.current_stop_price is not None
                or result.new_filled_entry_qty is not None
                or result.new_open_position_qty is not None
                or result.new_closed_position_qty is not None
            )
            if has_chain_update:
                fields = ["updated_at=?"]
                vals: list = [now]
                if result.new_lifecycle_state is not None:
                    fields.append("lifecycle_state=?")
                    vals.append(result.new_lifecycle_state)
                if result.new_be_protection_status is not None:
                    fields.append("be_protection_status=?")
                    vals.append(result.new_be_protection_status)
                if result.entry_avg_price is not None:
                    fields.append("entry_avg_price=?")
                    vals.append(result.entry_avg_price)
                if result.current_stop_price is not None:
                    fields.append("current_stop_price=?")
                    vals.append(result.current_stop_price)
                if result.new_filled_entry_qty is not None:
                    fields.append("filled_entry_qty=?")
                    vals.append(result.new_filled_entry_qty)
                if result.new_open_position_qty is not None:
                    fields.append("open_position_qty=?")
                    vals.append(result.new_open_position_qty)
                if result.new_closed_position_qty is not None:
                    fields.append("closed_position_qty=?")
                    vals.append(result.new_closed_position_qty)
                vals.append(chain_id)
                conn.execute(
                    f"UPDATE ops_trade_chains SET {', '.join(fields)} WHERE trade_chain_id=?",
                    vals,
                )

            if result.release_waiting_position:
                conn.execute(
                    "UPDATE ops_execution_commands SET status='PENDING', updated_at=? "
                    "WHERE trade_chain_id=? AND status='WAITING_POSITION'",
                    (now, chain_id),
                )

            for event in result.lifecycle_events:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO ops_lifecycle_events (
                        trade_chain_id, event_type, source_type, source_id,
                        previous_state, next_state, payload_json, idempotency_key, created_at
                    ) VALUES (?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        chain_id, event.event_type, event.source_type, event.source_id,
                        event.previous_state, event.next_state, event.payload_json,
                        event.idempotency_key, now,
                    ),
                )

            for cmd in result.execution_commands:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO ops_execution_commands (
                        trade_chain_id, command_type, status, payload_json,
                        idempotency_key, created_at, updated_at
                    ) VALUES (?,?,?,?,?,?,?)
                    """,
                    (chain_id, cmd.command_type, cmd.status, cmd.payload_json,
                     cmd.idempotency_key, now, now),
                )
    finally:
        conn.close()
```

- [ ] **Step 5.5: Run tests to verify they pass**

```
.venv\Scripts\python.exe -m pytest tests\runtime_v2\lifecycle\test_event_processor.py::test_event_processor_result_has_qty_fields tests\runtime_v2\lifecycle\test_event_processor.py::test_event_processor_result_qty_defaults_to_none -v
```

Expected: PASS

- [ ] **Step 5.6: Commit**

```
git add src/runtime_v2/lifecycle/event_processor.py src/runtime_v2/lifecycle/workers.py tests/runtime_v2/lifecycle/test_event_processor.py
git commit -m "feat(lifecycle): EventProcessorResult qty fields + worker persist qty + release WAITING_POSITION"
```

---

## Task 6: ENTRY_FILLED — qty tracking + weighted average + WAITING_POSITION release

**Files:**
- Modify: `src/runtime_v2/lifecycle/event_processor.py`
- Test: `tests/runtime_v2/lifecycle/test_event_processor.py`

- [ ] **Step 6.1: Write failing tests**

```python
# tests/runtime_v2/lifecycle/test_event_processor.py (add)
def _make_entry_event(chain_id: int, fill_price: float, filled_qty: float,
                      order_fully_filled: bool = True) -> ExchangeEvent:
    return ExchangeEvent(
        exchange_event_id=42,
        trade_chain_id=chain_id,
        event_type="ENTRY_FILLED",
        payload_json=json.dumps({
            "fill_price": fill_price,
            "filled_qty": filled_qty,
            "order_fully_filled": order_fully_filled,
        }),
        idempotency_key=f"entry_filled:{chain_id}:42",
    )

def _make_chain_waiting() -> TradeChain:
    return TradeChain(
        trade_chain_id=10, source_enrichment_id=1, canonical_message_id=2,
        raw_message_id=3, trader_id="t1", account_id="acc1",
        symbol="BTC/USDT", side="LONG", lifecycle_state="WAITING_ENTRY",
        entry_mode="ONE_SHOT", management_plan_json='{}',
        planned_entry_qty=0.01,
    )

def test_entry_filled_first_fill_transitions_to_open():
    proc = LifecycleEventProcessor()
    chain = _make_chain_waiting()
    ev = _make_entry_event(chain.trade_chain_id, fill_price=50000.0, filled_qty=0.01)
    result = proc.process(ev, chain, [])
    assert result.new_lifecycle_state == "OPEN"

def test_entry_filled_subsequent_fill_keeps_open():
    proc = LifecycleEventProcessor()
    chain = _make_chain_open()  # already OPEN
    ev = _make_entry_event(chain.trade_chain_id, fill_price=50000.0, filled_qty=0.005)
    result = proc.process(ev, chain, [])
    assert result.new_lifecycle_state is None  # no state change

def test_entry_filled_updates_qty():
    proc = LifecycleEventProcessor()
    chain = _make_chain_waiting()
    ev = _make_entry_event(chain.trade_chain_id, fill_price=50000.0, filled_qty=0.01)
    result = proc.process(ev, chain, [])
    assert result.new_filled_entry_qty == 0.01
    assert result.new_open_position_qty == 0.01

def test_entry_filled_weighted_average():
    proc = LifecycleEventProcessor()
    # First fill at 50000 for 0.006
    chain = TradeChain(
        trade_chain_id=10, source_enrichment_id=1, canonical_message_id=2,
        raw_message_id=3, trader_id="t1", account_id="acc1",
        symbol="BTC/USDT", side="LONG", lifecycle_state="OPEN",
        entry_mode="ONE_SHOT", management_plan_json='{}',
        filled_entry_qty=0.006, open_position_qty=0.006,
        entry_avg_price=50000.0,
    )
    # Second fill at 52000 for 0.004
    ev = _make_entry_event(chain.trade_chain_id, fill_price=52000.0, filled_qty=0.004)
    result = proc.process(ev, chain, [])
    expected_avg = (50000.0 * 0.006 + 52000.0 * 0.004) / 0.010
    assert abs(result.entry_avg_price - expected_avg) < 0.01
    assert result.new_filled_entry_qty == 0.010
    assert result.new_open_position_qty == 0.010

def test_entry_filled_first_fill_releases_waiting_position():
    proc = LifecycleEventProcessor()
    chain = _make_chain_waiting()
    ev = _make_entry_event(chain.trade_chain_id, fill_price=50000.0, filled_qty=0.01)
    result = proc.process(ev, chain, [])
    assert result.release_waiting_position is True

def test_entry_filled_subsequent_fill_does_not_release_waiting_position():
    proc = LifecycleEventProcessor()
    chain = _make_chain_open()  # already OPEN
    ev = _make_entry_event(chain.trade_chain_id, fill_price=50000.0, filled_qty=0.005)
    result = proc.process(ev, chain, [])
    assert result.release_waiting_position is False

def test_entry_filled_emits_position_size_updated_event():
    proc = LifecycleEventProcessor()
    chain = _make_chain_waiting()
    ev = _make_entry_event(chain.trade_chain_id, fill_price=50000.0, filled_qty=0.01)
    result = proc.process(ev, chain, [])
    event_types = [e.event_type for e in result.lifecycle_events]
    assert "POSITION_SIZE_UPDATED" in event_types
    assert "ENTRY_AVG_PRICE_UPDATED" in event_types
```

- [ ] **Step 6.2: Run tests to verify they fail**

```
.venv\Scripts\python.exe -m pytest tests\runtime_v2\lifecycle\test_event_processor.py -k "entry_filled" -v
```

Expected: FAIL

- [ ] **Step 6.3: Rewrite `_process_entry_filled` in `src/runtime_v2/lifecycle/event_processor.py`**

```python
def _process_entry_filled(
    self, exchange_event: ExchangeEvent, chain: TradeChain
) -> EventProcessorResult:
    payload = json.loads(exchange_event.payload_json)
    fill_price = float(payload.get("fill_price") or 0.0)
    fill_qty = float(payload.get("filled_qty") or 0.0)
    eid = exchange_event.exchange_event_id
    chain_id = chain.trade_chain_id

    old_filled = chain.filled_entry_qty
    old_avg = chain.entry_avg_price or 0.0
    new_filled = old_filled + fill_qty
    if new_filled > 0:
        new_avg = ((old_avg * old_filled) + (fill_price * fill_qty)) / new_filled
    else:
        new_avg = fill_price
    new_open = chain.open_position_qty + fill_qty

    is_first_fill = chain.lifecycle_state == "WAITING_ENTRY"
    new_state: LifecycleState | None = "OPEN" if is_first_fill else None

    events: list[LifecycleEvent] = [
        LifecycleEvent(
            trade_chain_id=chain_id,
            event_type="ENTRY_FILLED",
            source_type="exchange_event",
            source_id=str(eid),
            previous_state=chain.lifecycle_state,
            next_state=new_state or chain.lifecycle_state,
            payload_json=json.dumps({"fill_price": fill_price, "filled_qty": fill_qty}),
            idempotency_key=f"entry_filled:{chain_id}:{eid}",
        ),
        LifecycleEvent(
            trade_chain_id=chain_id,
            event_type="POSITION_SIZE_UPDATED",
            source_type="exchange_event",
            source_id=str(eid),
            payload_json=json.dumps({"filled_entry_qty": new_filled, "open_position_qty": new_open}),
            idempotency_key=f"pos_size_updated:{chain_id}:{eid}",
        ),
        LifecycleEvent(
            trade_chain_id=chain_id,
            event_type="ENTRY_AVG_PRICE_UPDATED",
            source_type="exchange_event",
            source_id=str(eid),
            payload_json=json.dumps({"entry_avg_price": new_avg}),
            idempotency_key=f"avg_price_updated:{chain_id}:{eid}",
        ),
    ]

    return EventProcessorResult(
        new_lifecycle_state=new_state,
        new_be_protection_status=None,
        entry_avg_price=new_avg,
        current_stop_price=None,
        lifecycle_events=events,
        execution_commands=[],
        new_filled_entry_qty=new_filled,
        new_open_position_qty=new_open,
        release_waiting_position=is_first_fill,
    )
```

- [ ] **Step 6.4: Run tests to verify they pass**

```
.venv\Scripts\python.exe -m pytest tests\runtime_v2\lifecycle\test_event_processor.py -k "entry_filled" -v
```

Expected: PASS

- [ ] **Step 6.5: Commit**

```
git add src/runtime_v2/lifecycle/event_processor.py tests/runtime_v2/lifecycle/test_event_processor.py
git commit -m "feat(lifecycle): ENTRY_FILLED qty tracking + weighted avg + WAITING_POSITION release"
```

---

## Task 7: TP/SL/Close fills — qty tracking + SYNC_PROTECTIVE_ORDERS

**Files:**
- Modify: `src/runtime_v2/lifecycle/event_processor.py`
- Test: `tests/runtime_v2/lifecycle/test_event_processor.py`

- [ ] **Step 7.1: Write failing tests**

```python
# tests/runtime_v2/lifecycle/test_event_processor.py (add)
def _make_close_event(chain_id: int, event_type: str, fill_qty: float) -> ExchangeEvent:
    return ExchangeEvent(
        exchange_event_id=5,
        trade_chain_id=chain_id,
        event_type=event_type,
        payload_json=json.dumps({
            "fill_price": 51000.0, "filled_qty": fill_qty,
            "tp_level": 1, "is_final": False,
        }),
        idempotency_key=f"{event_type}:{chain_id}:5",
    )

def test_tp_filled_reduces_open_qty():
    proc = LifecycleEventProcessor()
    chain = _make_chain_open()  # open_position_qty=0.01
    ev = _make_tp_event(chain.trade_chain_id, tp_level=1, is_final=False, fill_qty=0.005)
    result = proc.process(ev, chain, [])
    assert result.new_open_position_qty == 0.005
    assert result.new_closed_position_qty == 0.005

def test_tp_filled_final_closes_chain():
    proc = LifecycleEventProcessor()
    chain = _make_chain_open()
    ev = _make_tp_event(chain.trade_chain_id, tp_level=2, is_final=True, fill_qty=0.01)
    result = proc.process(ev, chain, [])
    assert result.new_lifecycle_state == "CLOSED"
    assert result.new_open_position_qty == 0.0

def test_tp_filled_non_final_generates_sync_protective_orders():
    proc = LifecycleEventProcessor()
    chain = _make_chain_open()
    ev = _make_tp_event(chain.trade_chain_id, tp_level=1, is_final=False, fill_qty=0.005)
    result = proc.process(ev, chain, [])
    sync_cmds = [c for c in result.execution_commands if c.command_type == "SYNC_PROTECTIVE_ORDERS"]
    assert len(sync_cmds) == 1

def test_tp_filled_final_no_sync_protective_orders():
    proc = LifecycleEventProcessor()
    chain = _make_chain_open()
    ev = _make_tp_event(chain.trade_chain_id, tp_level=2, is_final=True, fill_qty=0.01)
    result = proc.process(ev, chain, [])
    sync_cmds = [c for c in result.execution_commands if c.command_type == "SYNC_PROTECTIVE_ORDERS"]
    assert len(sync_cmds) == 0

def test_sl_filled_closes_chain_and_zeroes_open_qty():
    proc = LifecycleEventProcessor()
    chain = _make_chain_open()
    ev = ExchangeEvent(
        exchange_event_id=7, trade_chain_id=chain.trade_chain_id,
        event_type="SL_FILLED",
        payload_json=json.dumps({"fill_price": 49000.0, "filled_qty": 0.01}),
        idempotency_key="sl_filled:10:7",
    )
    result = proc.process(ev, chain, [])
    assert result.new_lifecycle_state == "CLOSED"
    assert result.new_open_position_qty == 0.0
    assert result.new_closed_position_qty == 0.01

def test_close_full_filled_closes_chain():
    proc = LifecycleEventProcessor()
    chain = _make_chain_open()
    ev = ExchangeEvent(
        exchange_event_id=8, trade_chain_id=chain.trade_chain_id,
        event_type="CLOSE_FULL_FILLED",
        payload_json=json.dumps({"fill_price": 51000.0, "filled_qty": 0.01}),
        idempotency_key="close_full_filled:10:8",
    )
    result = proc.process(ev, chain, [])
    assert result.new_lifecycle_state == "CLOSED"
    assert result.new_open_position_qty == 0.0

def test_close_partial_filled_partially_closes_chain():
    proc = LifecycleEventProcessor()
    chain = _make_chain_open()
    ev = ExchangeEvent(
        exchange_event_id=9, trade_chain_id=chain.trade_chain_id,
        event_type="CLOSE_PARTIAL_FILLED",
        payload_json=json.dumps({"fill_price": 51000.0, "filled_qty": 0.005}),
        idempotency_key="close_partial_filled:10:9",
    )
    result = proc.process(ev, chain, [])
    assert result.new_lifecycle_state == "PARTIALLY_CLOSED"
    assert result.new_open_position_qty == 0.005
    sync_cmds = [c for c in result.execution_commands if c.command_type == "SYNC_PROTECTIVE_ORDERS"]
    assert len(sync_cmds) == 1
```

- [ ] **Step 7.2: Run tests to verify they fail**

```
.venv\Scripts\python.exe -m pytest tests\runtime_v2\lifecycle\test_event_processor.py -k "tp_filled or sl_filled or close" -v
```

Expected: FAIL

- [ ] **Step 7.3: Update `_process_tp_filled` in `src/runtime_v2/lifecycle/event_processor.py`**

Add qty tracking and SYNC generation. Replace the method:

```python
def _process_tp_filled(
    self,
    exchange_event: ExchangeEvent,
    chain: TradeChain,
    active_commands: list[ExecutionCommand],
) -> EventProcessorResult:
    payload = json.loads(exchange_event.payload_json)
    tp_level = int(payload.get("tp_level", 1))
    is_final = bool(payload.get("is_final", False))
    fill_qty = float(payload.get("filled_qty") or 0.0)
    eid = exchange_event.exchange_event_id
    chain_id = chain.trade_chain_id

    new_open = max(chain.open_position_qty - fill_qty, 0.0)
    new_closed = chain.closed_position_qty + fill_qty
    new_state: LifecycleState = "CLOSED" if (is_final or new_open <= 0) else "PARTIALLY_CLOSED"

    events: list[LifecycleEvent] = []
    commands: list[ExecutionCommand] = []
    new_be: BeProtectionStatus | None = None

    # BE trigger logic (unchanged, but never sets new_state to BE_MOVE_PENDING)
    if not is_final and new_state == "PARTIALLY_CLOSED":
        try:
            mp = ManagementPlanConfig.model_validate_json(chain.management_plan_json)
        except Exception:
            mp = ManagementPlanConfig()
        be_trigger = mp.be_trigger
        if be_trigger and be_trigger == f"tp{tp_level}":
            if chain.be_protection_status == "PROTECTED":
                events.append(LifecycleEvent(
                    trade_chain_id=chain_id, event_type="NOOP_ALREADY_PROTECTED_BE",
                    source_type="exchange_event", source_id=str(eid),
                    idempotency_key=f"noop_already_be_tp:{chain_id}:{eid}",
                ))
            else:
                active_be = [
                    c for c in active_commands
                    if c.command_type == "MOVE_STOP_TO_BREAKEVEN"
                    and c.status in ("PENDING", "SENT", "ACK")
                ]
                if active_be:
                    events.append(LifecycleEvent(
                        trade_chain_id=chain_id, event_type="NOOP_DUPLICATE_COMMAND",
                        source_type="exchange_event", source_id=str(eid),
                        idempotency_key=f"noop_dup_be_tp:{chain_id}:{eid}",
                    ))
                else:
                    cmd_payload = {
                        "symbol": chain.symbol, "side": chain.side,
                        "target_price": chain.entry_avg_price,
                        "be_buffer_pct": mp.be_buffer_pct,
                    }
                    commands.append(ExecutionCommand(
                        trade_chain_id=chain_id, command_type="MOVE_STOP_TO_BREAKEVEN",
                        payload_json=json.dumps(cmd_payload),
                        idempotency_key=f"move_be_tp:{chain_id}:{eid}",
                    ))
                    events.append(LifecycleEvent(
                        trade_chain_id=chain_id, event_type="BE_MOVE_REQUESTED",
                        source_type="exchange_event", source_id=str(eid),
                        idempotency_key=f"be_req_tp:{chain_id}:{eid}",
                    ))
                    new_be = "BE_MOVE_PENDING"

        # SYNC_PROTECTIVE_ORDERS on non-final TP
        commands.append(ExecutionCommand(
            trade_chain_id=chain_id,
            command_type="SYNC_PROTECTIVE_ORDERS",
            payload_json=json.dumps({"symbol": chain.symbol, "side": chain.side}),
            idempotency_key=f"sync_after_tp:{chain_id}:{eid}",
        ))

    tp_event = LifecycleEvent(
        trade_chain_id=chain_id, event_type="TP_FILLED",
        source_type="exchange_event", source_id=str(eid),
        previous_state=chain.lifecycle_state, next_state=new_state,
        payload_json=json.dumps({"tp_level": tp_level, "is_final": is_final, "filled_qty": fill_qty}),
        idempotency_key=f"tp_filled:{chain_id}:{eid}",
    )
    events.insert(0, tp_event)

    return EventProcessorResult(
        new_lifecycle_state=new_state,
        new_be_protection_status=new_be,
        entry_avg_price=None,
        current_stop_price=None,
        lifecycle_events=events,
        execution_commands=commands,
        new_open_position_qty=new_open,
        new_closed_position_qty=new_closed,
    )
```

- [ ] **Step 7.4: Update `_process_sl_filled` in `src/runtime_v2/lifecycle/event_processor.py`**

```python
def _process_sl_filled(
    self, exchange_event: ExchangeEvent, chain: TradeChain
) -> EventProcessorResult:
    payload = json.loads(exchange_event.payload_json)
    fill_qty = float(payload.get("filled_qty") or chain.open_position_qty)
    eid = exchange_event.exchange_event_id
    chain_id = chain.trade_chain_id
    return EventProcessorResult(
        new_lifecycle_state="CLOSED",
        new_be_protection_status=None,
        entry_avg_price=None,
        current_stop_price=None,
        lifecycle_events=[LifecycleEvent(
            trade_chain_id=chain_id, event_type="SL_FILLED",
            source_type="exchange_event", source_id=str(eid),
            previous_state=chain.lifecycle_state, next_state="CLOSED",
            payload_json=exchange_event.payload_json,
            idempotency_key=f"sl_filled:{chain_id}:{eid}",
        )],
        execution_commands=[],
        new_open_position_qty=0.0,
        new_closed_position_qty=chain.closed_position_qty + fill_qty,
    )
```

- [ ] **Step 7.5: Add `_process_close_partial_filled` and `_process_close_full_filled` handlers**

Add to `LifecycleEventProcessor`:

```python
def _process_close_full_filled(
    self, exchange_event: ExchangeEvent, chain: TradeChain
) -> EventProcessorResult:
    payload = json.loads(exchange_event.payload_json)
    fill_qty = float(payload.get("filled_qty") or chain.open_position_qty)
    eid = exchange_event.exchange_event_id
    chain_id = chain.trade_chain_id
    return EventProcessorResult(
        new_lifecycle_state="CLOSED",
        new_be_protection_status=None,
        entry_avg_price=None,
        current_stop_price=None,
        lifecycle_events=[LifecycleEvent(
            trade_chain_id=chain_id, event_type="CLOSE_FULL_FILLED",
            source_type="exchange_event", source_id=str(eid),
            previous_state=chain.lifecycle_state, next_state="CLOSED",
            payload_json=exchange_event.payload_json,
            idempotency_key=f"close_full_filled:{chain_id}:{eid}",
        )],
        execution_commands=[],
        new_open_position_qty=0.0,
        new_closed_position_qty=chain.closed_position_qty + fill_qty,
    )

def _process_close_partial_filled(
    self, exchange_event: ExchangeEvent, chain: TradeChain
) -> EventProcessorResult:
    payload = json.loads(exchange_event.payload_json)
    fill_qty = float(payload.get("filled_qty") or 0.0)
    eid = exchange_event.exchange_event_id
    chain_id = chain.trade_chain_id
    new_open = max(chain.open_position_qty - fill_qty, 0.0)
    new_closed = chain.closed_position_qty + fill_qty
    new_state: LifecycleState = "CLOSED" if new_open <= 0 else "PARTIALLY_CLOSED"
    commands: list[ExecutionCommand] = []
    if new_state == "PARTIALLY_CLOSED":
        commands.append(ExecutionCommand(
            trade_chain_id=chain_id, command_type="SYNC_PROTECTIVE_ORDERS",
            payload_json=json.dumps({"symbol": chain.symbol, "side": chain.side}),
            idempotency_key=f"sync_after_close_partial:{chain_id}:{eid}",
        ))
    return EventProcessorResult(
        new_lifecycle_state=new_state,
        new_be_protection_status=None,
        entry_avg_price=None,
        current_stop_price=None,
        lifecycle_events=[LifecycleEvent(
            trade_chain_id=chain_id, event_type="CLOSE_PARTIAL_FILLED",
            source_type="exchange_event", source_id=str(eid),
            previous_state=chain.lifecycle_state, next_state=new_state,
            payload_json=exchange_event.payload_json,
            idempotency_key=f"close_partial_filled:{chain_id}:{eid}",
        )],
        execution_commands=commands,
        new_open_position_qty=new_open,
        new_closed_position_qty=new_closed,
    )
```

- [ ] **Step 7.6: Register new event types in `process()`**

In `LifecycleEventProcessor.process`, add:
```python
if etype == "CLOSE_FULL_FILLED":
    return self._process_close_full_filled(exchange_event, chain)
if etype == "CLOSE_PARTIAL_FILLED":
    return self._process_close_partial_filled(exchange_event, chain)
```

- [ ] **Step 7.7: Run tests to verify they pass**

```
.venv\Scripts\python.exe -m pytest tests\runtime_v2\lifecycle\test_event_processor.py -v
```

Expected: PASS

- [ ] **Step 7.8: Commit**

```
git add src/runtime_v2/lifecycle/event_processor.py tests/runtime_v2/lifecycle/test_event_processor.py
git commit -m "feat(lifecycle): TP/SL/Close fill qty tracking + SYNC_PROTECTIVE_ORDERS trigger"
```

---

## Task 8: STOP_MOVED_CONFIRMED + PENDING_ENTRY_CANCELLED_CONFIRMED handlers

**Files:**
- Modify: `src/runtime_v2/lifecycle/event_processor.py`
- Test: `tests/runtime_v2/lifecycle/test_event_processor.py`

- [ ] **Step 8.1: Write failing tests**

```python
# tests/runtime_v2/lifecycle/test_event_processor.py (add)
def test_stop_moved_confirmed_updates_be_protection_and_stop_price():
    proc = LifecycleEventProcessor()
    chain = TradeChain(
        trade_chain_id=10, source_enrichment_id=1, canonical_message_id=2,
        raw_message_id=3, trader_id="t1", account_id="acc1",
        symbol="BTC/USDT", side="LONG", lifecycle_state="OPEN",
        entry_mode="ONE_SHOT", management_plan_json='{}',
        be_protection_status="BE_MOVE_PENDING",
        entry_avg_price=50000.0, open_position_qty=0.01,
    )
    ev = ExchangeEvent(
        exchange_event_id=20, trade_chain_id=10,
        event_type="STOP_MOVED_CONFIRMED",
        payload_json=json.dumps({"new_stop_price": 50000.0, "is_breakeven": True}),
        idempotency_key="stop_moved:10:20",
    )
    result = proc.process(ev, chain, [])
    assert result.new_lifecycle_state is None  # no state change
    assert result.new_be_protection_status == "PROTECTED"
    assert result.current_stop_price == 50000.0

def test_pending_entry_cancelled_confirmed_no_position():
    proc = LifecycleEventProcessor()
    chain = TradeChain(
        trade_chain_id=10, source_enrichment_id=1, canonical_message_id=2,
        raw_message_id=3, trader_id="t1", account_id="acc1",
        symbol="BTC/USDT", side="LONG", lifecycle_state="WAITING_ENTRY",
        entry_mode="ONE_SHOT", management_plan_json='{}',
    )
    ev = ExchangeEvent(
        exchange_event_id=21, trade_chain_id=10,
        event_type="PENDING_ENTRY_CANCELLED_CONFIRMED",
        payload_json=json.dumps({
            "cancelled_order_ids": ["tsb:10:1:entry:1"],
            "cancelled_pending_qty": 0.01,
            "position_already_open": False,
        }),
        idempotency_key="cancel_confirmed:10:21",
    )
    result = proc.process(ev, chain, [])
    assert result.new_lifecycle_state == "CANCELLED"

def test_pending_entry_cancelled_confirmed_with_position_open():
    proc = LifecycleEventProcessor()
    chain = TradeChain(
        trade_chain_id=10, source_enrichment_id=1, canonical_message_id=2,
        raw_message_id=3, trader_id="t1", account_id="acc1",
        symbol="BTC/USDT", side="LONG", lifecycle_state="OPEN",
        entry_mode="ONE_SHOT", management_plan_json='{}',
        open_position_qty=0.005,
    )
    ev = ExchangeEvent(
        exchange_event_id=22, trade_chain_id=10,
        event_type="PENDING_ENTRY_CANCELLED_CONFIRMED",
        payload_json=json.dumps({
            "cancelled_order_ids": ["tsb:10:2:entry:2"],
            "cancelled_pending_qty": 0.005,
            "position_already_open": True,
        }),
        idempotency_key="cancel_confirmed:10:22",
    )
    result = proc.process(ev, chain, [])
    assert result.new_lifecycle_state is None  # position stays OPEN
    sync_cmds = [c for c in result.execution_commands if c.command_type == "SYNC_PROTECTIVE_ORDERS"]
    assert len(sync_cmds) == 1
```

- [ ] **Step 8.2: Run tests to verify they fail**

```
.venv\Scripts\python.exe -m pytest tests\runtime_v2\lifecycle\test_event_processor.py -k "stop_moved or cancelled_confirmed" -v
```

Expected: FAIL

- [ ] **Step 8.3: Add handlers to `LifecycleEventProcessor`**

```python
def _process_stop_moved_confirmed(
    self, exchange_event: ExchangeEvent, chain: TradeChain
) -> EventProcessorResult:
    payload = json.loads(exchange_event.payload_json)
    new_stop_price = float(payload.get("new_stop_price") or 0.0)
    is_breakeven = bool(payload.get("is_breakeven", False))
    eid = exchange_event.exchange_event_id
    chain_id = chain.trade_chain_id
    new_be: BeProtectionStatus | None = "PROTECTED" if is_breakeven else None
    return EventProcessorResult(
        new_lifecycle_state=None,
        new_be_protection_status=new_be,
        entry_avg_price=None,
        current_stop_price=new_stop_price if new_stop_price > 0 else None,
        lifecycle_events=[LifecycleEvent(
            trade_chain_id=chain_id, event_type="STOP_MOVE_CONFIRMED",
            source_type="exchange_event", source_id=str(eid),
            payload_json=json.dumps({"new_stop_price": new_stop_price, "is_breakeven": is_breakeven}),
            idempotency_key=f"stop_moved:{chain_id}:{eid}",
        )],
        execution_commands=[],
    )

def _process_pending_entry_cancelled_confirmed(
    self, exchange_event: ExchangeEvent, chain: TradeChain
) -> EventProcessorResult:
    payload = json.loads(exchange_event.payload_json)
    position_already_open = bool(payload.get("position_already_open", False))
    eid = exchange_event.exchange_event_id
    chain_id = chain.trade_chain_id
    commands: list[ExecutionCommand] = []
    new_state: LifecycleState | None = None
    if position_already_open:
        commands.append(ExecutionCommand(
            trade_chain_id=chain_id, command_type="SYNC_PROTECTIVE_ORDERS",
            payload_json=json.dumps({"symbol": chain.symbol, "side": chain.side}),
            idempotency_key=f"sync_after_cancel:{chain_id}:{eid}",
        ))
    else:
        new_state = "CANCELLED"
    return EventProcessorResult(
        new_lifecycle_state=new_state,
        new_be_protection_status=None,
        entry_avg_price=None,
        current_stop_price=None,
        lifecycle_events=[LifecycleEvent(
            trade_chain_id=chain_id, event_type="PENDING_ENTRY_CANCELLED",
            source_type="exchange_event", source_id=str(eid),
            previous_state=chain.lifecycle_state,
            next_state=new_state,
            payload_json=exchange_event.payload_json,
            idempotency_key=f"pending_cancelled:{chain_id}:{eid}",
        )],
        execution_commands=commands,
    )
```

Register in `process()`:
```python
if etype == "STOP_MOVED_CONFIRMED":
    return self._process_stop_moved_confirmed(exchange_event, chain)
if etype == "PENDING_ENTRY_CANCELLED_CONFIRMED":
    return self._process_pending_entry_cancelled_confirmed(exchange_event, chain)
```

- [ ] **Step 8.4: Run tests to verify they pass**

```
.venv\Scripts\python.exe -m pytest tests\runtime_v2\lifecycle\test_event_processor.py -v
```

Expected: all PASS

- [ ] **Step 8.5: Commit**

```
git add src/runtime_v2/lifecycle/event_processor.py tests/runtime_v2/lifecycle/test_event_processor.py
git commit -m "feat(lifecycle): STOP_MOVED_CONFIRMED and PENDING_ENTRY_CANCELLED_CONFIRMED handlers"
```

---

## Task 9: Cancel pending on OPEN chain — SYNC_PROTECTIVE_ORDERS

**Files:**
- Modify: `src/runtime_v2/lifecycle/entry_gate.py`
- Test: `tests/runtime_v2/lifecycle/test_entry_gate.py`

- [ ] **Step 9.1: Write failing tests**

```python
# tests/runtime_v2/lifecycle/test_entry_gate.py (add)
def _make_gate_default() -> LifecycleEntryGate:
    from src.runtime_v2.lifecycle.risk_capacity import RiskCapacityEngine
    from src.runtime_v2.lifecycle.static_exchange_data_port import StaticExchangeDataPort
    return LifecycleEntryGate(
        risk_engine=RiskCapacityEngine({}),
        exchange_port=StaticExchangeDataPort(),
    )

def _make_enriched_cancel() -> "EnrichedCanonicalMessage":
    from unittest.mock import MagicMock
    enriched = MagicMock()
    enriched.enrichment_id = 99
    enriched.canonical_message_id = 55
    enriched.trader_id = "t1"
    action = MagicMock()
    action.action_type = "CANCEL_PENDING"
    tag = MagicMock()
    tag.actions = [action]
    tag.targeting.scope_hint = "SYMBOL"
    tag.targeting.symbols = {"BTC/USDT"}
    tag.targeting.explicit_ids = None
    enriched.enriched_actions = [tag]
    return enriched

def test_cancel_pending_on_waiting_entry_becomes_cancelled():
    gate = _make_gate_default()
    chain = _make_chain("WAITING_ENTRY")
    enriched = _make_enriched_cancel()
    result = gate.process_update(enriched, [chain], {10: []})
    assert len(result.chain_results) == 1
    cr = result.chain_results[0]
    assert cr.new_lifecycle_state == "CANCELLED"
    cmd_types = [c.command_type for c in cr.execution_commands]
    assert "CANCEL_PENDING_ENTRY" in cmd_types
    assert "SYNC_PROTECTIVE_ORDERS" not in cmd_types

def test_cancel_pending_on_open_emits_sync_not_cancelled():
    gate = _make_gate_default()
    chain = _make_chain("OPEN")
    chain = chain.model_copy(update={"open_position_qty": 0.005})
    enriched = _make_enriched_cancel()
    result = gate.process_update(enriched, [chain], {10: []})
    assert len(result.chain_results) == 1
    cr = result.chain_results[0]
    # Chain stays OPEN — no lifecycle state change
    assert cr.new_lifecycle_state is None
    cmd_types = [c.command_type for c in cr.execution_commands]
    assert "CANCEL_PENDING_ENTRY" in cmd_types
    assert "SYNC_PROTECTIVE_ORDERS" in cmd_types

def test_cancel_pending_on_partially_closed_emits_sync():
    gate = _make_gate_default()
    chain = _make_chain("PARTIALLY_CLOSED")
    chain = chain.model_copy(update={"open_position_qty": 0.005})
    enriched = _make_enriched_cancel()
    result = gate.process_update(enriched, [chain], {10: []})
    cr = result.chain_results[0]
    assert cr.new_lifecycle_state is None
    cmd_types = [c.command_type for c in cr.execution_commands]
    assert "SYNC_PROTECTIVE_ORDERS" in cmd_types
```

- [ ] **Step 9.2: Run tests to verify they fail**

```
.venv\Scripts\python.exe -m pytest tests\runtime_v2\lifecycle\test_entry_gate.py -k "cancel_pending" -v
```

Expected: FAIL (cancel on OPEN currently returns NOOP_NOT_PENDING)

- [ ] **Step 9.3: Rewrite `_apply_cancel_pending` in `src/runtime_v2/lifecycle/entry_gate.py`**

```python
def _apply_cancel_pending(
    self, enriched: EnrichedCanonicalMessage, chain: TradeChain
) -> UpdateChainResult:
    chain_id = chain.trade_chain_id
    cmid = enriched.canonical_message_id
    state = chain.lifecycle_state

    if state not in ("WAITING_ENTRY", "OPEN", "PARTIALLY_CLOSED"):
        return UpdateChainResult(
            trade_chain_id=chain_id,
            new_lifecycle_state=None,
            new_be_protection_status=None,
            lifecycle_events=[LifecycleEvent(
                trade_chain_id=chain_id, event_type="NOOP_NOT_PENDING",
                source_type="telegram_update", source_id=str(cmid),
                idempotency_key=f"noop_not_pending:{chain_id}:{cmid}",
            )],
            execution_commands=[],
        )

    commands: list[ExecutionCommand] = [ExecutionCommand(
        trade_chain_id=chain_id,
        command_type="CANCEL_PENDING_ENTRY",
        payload_json=json.dumps({"symbol": chain.symbol, "side": chain.side}),
        idempotency_key=f"cancel_pending:{chain_id}:{cmid}",
    )]

    event = LifecycleEvent(
        trade_chain_id=chain_id, event_type="TELEGRAM_UPDATE_ACCEPTED",
        source_type="telegram_update", source_id=str(cmid),
        payload_json=json.dumps({"action": "CANCEL_PENDING"}),
        idempotency_key=f"update_cancel:{chain_id}:{cmid}",
    )

    if state == "WAITING_ENTRY":
        # No position open — chain terminates as CANCELLED
        return UpdateChainResult(
            trade_chain_id=chain_id,
            new_lifecycle_state="CANCELLED",
            new_be_protection_status=None,
            lifecycle_events=[event],
            execution_commands=commands,
        )

    # OPEN or PARTIALLY_CLOSED — position exists, cancel residual entries only
    commands.append(ExecutionCommand(
        trade_chain_id=chain_id,
        command_type="SYNC_PROTECTIVE_ORDERS",
        payload_json=json.dumps({"symbol": chain.symbol, "side": chain.side}),
        idempotency_key=f"sync_after_cancel_pending:{chain_id}:{cmid}",
    ))
    return UpdateChainResult(
        trade_chain_id=chain_id,
        new_lifecycle_state=None,  # position stays open
        new_be_protection_status=None,
        lifecycle_events=[event],
        execution_commands=commands,
    )
```

- [ ] **Step 9.4: Run tests to verify they pass**

```
.venv\Scripts\python.exe -m pytest tests\runtime_v2\lifecycle\test_entry_gate.py -v
```

Expected: PASS

- [ ] **Step 9.5: Commit**

```
git add src/runtime_v2/lifecycle/entry_gate.py tests/runtime_v2/lifecycle/test_entry_gate.py
git commit -m "feat(lifecycle): CANCEL_PENDING_ENTRY on OPEN chain emits SYNC_PROTECTIVE_ORDERS"
```

---

## Task 10: Client order ID roles + gateway mapping + event_sync handlers

**Files:**
- Modify: `src/runtime_v2/execution_gateway/client_order_id.py`
- Modify: `src/runtime_v2/execution_gateway/gateway.py`
- Modify: `src/runtime_v2/execution_gateway/event_sync.py`
- Modify: `src/runtime_v2/execution_gateway/models.py`
- Test: `tests/runtime_v2/execution_gateway/test_client_order_id.py`
- Test: `tests/runtime_v2/execution_gateway/test_gateway.py`
- Test: `tests/runtime_v2/execution_gateway/test_event_sync.py`

- [ ] **Step 10.1: Write failing tests**

```python
# tests/runtime_v2/execution_gateway/test_client_order_id.py (add)
from src.runtime_v2.execution_gateway.client_order_id import build, parse

def test_exit_partial_role_is_valid():
    coid = build(trade_chain_id=10, command_id=5, role="exit_partial", sequence=1)
    assert coid == "tsb:10:5:exit_partial:1"

def test_exit_full_role_is_valid():
    coid = build(trade_chain_id=10, command_id=6, role="exit_full", sequence=1)
    assert coid == "tsb:10:6:exit_full:1"

def test_sync_role_is_valid():
    coid = build(trade_chain_id=10, command_id=7, role="sync", sequence=1)
    assert coid == "tsb:10:7:sync:1"

def test_invalid_role_raises():
    import pytest
    with pytest.raises(ValueError, match="Invalid role"):
        build(trade_chain_id=10, command_id=8, role="entry_old", sequence=1)
```

```python
# tests/runtime_v2/execution_gateway/test_gateway.py (add)
from src.runtime_v2.execution_gateway.gateway import _ROLE_MAP

def test_close_partial_uses_exit_partial_role():
    assert _ROLE_MAP["CLOSE_PARTIAL"] == "exit_partial"

def test_close_full_uses_exit_full_role():
    assert _ROLE_MAP["CLOSE_FULL"] == "exit_full"

def test_sync_protective_orders_uses_sync_role():
    assert _ROLE_MAP["SYNC_PROTECTIVE_ORDERS"] == "sync"
```

```python
# tests/runtime_v2/execution_gateway/test_event_sync.py (add)
def test_exit_partial_role_generates_close_partial_filled():
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.client_order_id import build
    # Verify the role→event_type mapping logic
    # We test _normalize_and_save indirectly via role check
    coid_str = build(10, 5, "exit_partial", 1)
    from src.runtime_v2.execution_gateway import client_order_id as coid_mod
    parsed = coid_mod.parse(coid_str)
    assert parsed.role == "exit_partial"

def test_sync_role_generates_protective_orders_synced():
    from src.runtime_v2.execution_gateway import client_order_id as coid_mod
    coid_str = "tsb:10:7:sync:1"
    parsed = coid_mod.parse(coid_str)
    assert parsed.role == "sync"
```

- [ ] **Step 10.2: Run tests to verify they fail**

```
.venv\Scripts\python.exe -m pytest tests\runtime_v2\execution_gateway\test_client_order_id.py tests\runtime_v2\execution_gateway\test_gateway.py tests\runtime_v2\execution_gateway\test_event_sync.py -k "exit_partial or exit_full or sync" -v
```

Expected: FAIL

- [ ] **Step 10.3: Update `client_order_id.py` — add new roles**

```python
_VALID_ROLES = frozenset({"entry", "sl", "tp", "exit_partial", "exit_full", "sync"})
```

- [ ] **Step 10.4: Update `_ROLE_MAP` and `_CAPABILITY_MAP` in `gateway.py`**

```python
_CAPABILITY_MAP: dict[str, str] = {
    "PLACE_ENTRY": "place_entry",
    "PLACE_PROTECTIVE_STOP": "protective_stop_native",
    "PLACE_TAKE_PROFIT": "take_profit_native",
    "MOVE_STOP_TO_BREAKEVEN": "move_stop",
    "MOVE_STOP": "move_stop",
    "CANCEL_PENDING_ENTRY": "place_entry",
    "CLOSE_PARTIAL": "close_partial",
    "CLOSE_FULL": "close_full",
    "SYNC_PROTECTIVE_ORDERS": "sync_protective_orders",
}

_ROLE_MAP: dict[str, str] = {
    "PLACE_ENTRY": "entry",
    "PLACE_PROTECTIVE_STOP": "sl",
    "PLACE_TAKE_PROFIT": "tp",
    "MOVE_STOP_TO_BREAKEVEN": "sl",
    "MOVE_STOP": "sl",
    "CANCEL_PENDING_ENTRY": "entry",
    "CLOSE_PARTIAL": "exit_partial",
    "CLOSE_FULL": "exit_full",
    "SYNC_PROTECTIVE_ORDERS": "sync",
}
```

- [ ] **Step 10.5: Add `sync_protective_orders` to `AdapterCapabilities` in `models.py`**

```python
class AdapterCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")
    place_entry: bool = True
    protective_stop_native: bool = False
    take_profit_native: bool = False
    bracket_order: bool = False
    move_stop: bool = False
    close_partial: bool = False
    close_full: bool = False
    executor_position: bool = False
    sync_protective_orders: bool = True  # FakeAdapter always supports this
```

- [ ] **Step 10.6: Add new role handlers in `event_sync.py`**

In `_normalize_and_save`, add after the `elif coid.role == "tp":` block:

```python
elif coid.role == "exit_partial":
    event_type = "CLOSE_PARTIAL_FILLED"
    payload = {
        "fill_price": raw.average_price,
        "filled_qty": raw.filled_qty,
        "command_id": coid.command_id,
    }
elif coid.role == "exit_full":
    event_type = "CLOSE_FULL_FILLED"
    payload = {
        "fill_price": raw.average_price,
        "filled_qty": raw.filled_qty,
        "command_id": coid.command_id,
    }
elif coid.role == "sync":
    event_type = "PROTECTIVE_ORDERS_SYNCED"
    payload = {
        "command_id": coid.command_id,
    }
```

- [ ] **Step 10.7: Run tests to verify they pass**

```
.venv\Scripts\python.exe -m pytest tests\runtime_v2\execution_gateway\test_client_order_id.py tests\runtime_v2\execution_gateway\test_gateway.py tests\runtime_v2\execution_gateway\test_event_sync.py -v
```

Expected: PASS

- [ ] **Step 10.8: Commit**

```
git add src/runtime_v2/execution_gateway/client_order_id.py src/runtime_v2/execution_gateway/gateway.py src/runtime_v2/execution_gateway/event_sync.py src/runtime_v2/execution_gateway/models.py tests/runtime_v2/execution_gateway/
git commit -m "feat(gateway): new client_order_id roles + CLOSE_PARTIAL/FULL/SYNC role mapping + event_sync handlers"
```

---

## Task 11: Fake adapter — SYNC_PROTECTIVE_ORDERS auto-fills

**Files:**
- Modify: `src/runtime_v2/execution_gateway/adapters/fake.py`
- Test: `tests/runtime_v2/execution_gateway/test_adapter_factory.py` or new test file

- [ ] **Step 11.1: Write failing test**

```python
# tests/runtime_v2/execution_gateway/test_fake_adapter.py (create if not exists)
from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter

def test_sync_protective_orders_is_immediately_filled():
    adapter = FakeAdapter()
    result = adapter.place_order(
        command_type="SYNC_PROTECTIVE_ORDERS",
        payload={"symbol": "BTC/USDT", "side": "LONG"},
        client_order_id="tsb:10:7:sync:1",
        execution_account_id="acc1",
        connector="fake",
    )
    assert result.success is True
    # Order should be immediately marked as filled
    order = adapter.get_order_status(
        client_order_id="tsb:10:7:sync:1",
        execution_account_id="acc1",
    )
    assert order is not None
    assert order.is_filled is True
```

- [ ] **Step 11.2: Run test to verify it fails**

```
.venv\Scripts\python.exe -m pytest tests\runtime_v2\execution_gateway\test_fake_adapter.py::test_sync_protective_orders_is_immediately_filled -v
```

Expected: FAIL (order is OPEN, not filled)

- [ ] **Step 11.3: Update `FakeAdapter.place_order` in `src/runtime_v2/execution_gateway/adapters/fake.py`**

In `place_order`, after building the order and before the return, add special case:

```python
        order = RawAdapterOrder(
            client_order_id=client_order_id,
            exchange_order_id=f"exch_{client_order_id}",
            adapter_order_id=f"hb_{client_order_id}",
            status="OPEN",
        )
        # SYNC_PROTECTIVE_ORDERS completes immediately — no real orders to track
        if command_type == "SYNC_PROTECTIVE_ORDERS":
            order = order.model_copy(update={"status": "FILLED", "filled_qty": 1.0, "average_price": 0.0})
        self._orders[client_order_id] = order
```

Also verify `RawAdapterOrder.is_filled` property handles `status == "FILLED"`. Check `src/runtime_v2/execution_gateway/models.py` — if `is_filled` is a computed property check it returns True for "FILLED" status. If not, add it or update the condition.

Look for `is_filled` in `RawAdapterOrder`. If it checks `status in ("FILLED", "DONE")`, the above is sufficient. If it uses a different field, align.

- [ ] **Step 11.4: Run test to verify it passes**

```
.venv\Scripts\python.exe -m pytest tests\runtime_v2\execution_gateway\test_fake_adapter.py::test_sync_protective_orders_is_immediately_filled -v
```

Expected: PASS

- [ ] **Step 11.5: Also update FakeAdapter capabilities default**

In `FakeAdapter.__init__`, update default capabilities to include `sync_protective_orders=True`:

```python
self._capabilities = capabilities or AdapterCapabilities(
    place_entry=True,
    protective_stop_native=True,
    take_profit_native=True,
    bracket_order=False,
    move_stop=True,
    close_partial=True,
    close_full=True,
    executor_position=False,
    sync_protective_orders=True,
)
```

- [ ] **Step 11.6: Commit**

```
git add src/runtime_v2/execution_gateway/adapters/fake.py tests/runtime_v2/execution_gateway/test_fake_adapter.py
git commit -m "feat(fake_adapter): SYNC_PROTECTIVE_ORDERS immediately fills to generate PROTECTIVE_ORDERS_SYNCED"
```

---

## Task 12: Regression suite

**Files:**
- Run full runtime_v2 test suite and fix any breakage

- [ ] **Step 12.1: Run full suite**

```
.venv\Scripts\python.exe -m pytest tests\runtime_v2\lifecycle tests\runtime_v2\execution_gateway -q
```

- [ ] **Step 12.2: Fix any broken existing tests**

Common expected breakage:
- Tests that check `lifecycle_state == "BE_MOVE_PENDING"` after a BE move → update to check `lifecycle_state` is unchanged and `be_protection_status == "BE_MOVE_PENDING"`
- Tests that check `_VALID_ROLES` or role validation → update to include new roles
- Tests that check `CLOSE_PARTIAL` or `CLOSE_FULL` produce role `"entry"` → update to `"exit_partial"` / `"exit_full"`
- Tests that create `TradeChain` without new fields → they should still work (all new fields have defaults)
- Tests that INSERT `ops_trade_chains` without new columns → migration adds them with defaults, existing inserts still work

For each broken test, read its assertion, understand why it fails, and update the assertion to match the new contract. Do not change the code under test to satisfy old tests — update the tests.

- [ ] **Step 12.3: Exclude gated tests**

Confirm gated tests that require real services are properly skipped:

```
.venv\Scripts\python.exe -m pytest tests\runtime_v2\lifecycle tests\runtime_v2\execution_gateway -q --ignore=tests\runtime_v2\execution_gateway\test_hummingbot_api.py
```

- [ ] **Step 12.4: Final clean run**

```
.venv\Scripts\python.exe -m pytest tests\runtime_v2\lifecycle tests\runtime_v2\execution_gateway -q
```

Expected: all tests PASS (excluding gated)

- [ ] **Step 12.5: Commit**

```
git add tests\runtime_v2\
git commit -m "test(runtime_v2): fix existing tests for Phase 0 contract changes"
```

---

## Acceptance Verification

Run acceptance criteria from spec §4:

```powershell
.venv\Scripts\python.exe -m pytest tests\runtime_v2\lifecycle tests\runtime_v2\execution_gateway -q -v
```

Verify against spec criteria:
1. LifecycleEntryGate produces correct initial commands for `a_sequential`, `b_entry_stop_then_tp`, `c_native_attached_tpsl` ✓ Task 4
2. `ops_trade_chains` contains qty runtime fields ✓ Task 1
3. `ENTRY_FILLED` updates qty and weighted avg ✓ Task 6
4. `MOVE_STOP_TO_BREAKEVEN` does not change lifecycle_state ✓ Task 3
5. `CLOSE_PARTIAL` / `CLOSE_FULL` produce role `exit_partial` / `exit_full` ✓ Task 10
6. `CANCEL_PENDING_ENTRY` works on `OPEN` chains ✓ Task 9
7. Mode C creates TP intermediates as `WAITING_POSITION` and releases on fill ✓ Tasks 4, 6
