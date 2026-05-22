# tests/runtime_v2/lifecycle/test_event_processor.py
from __future__ import annotations

import json
from datetime import datetime, timezone


def _now():
    return datetime.now(timezone.utc)


def _make_exchange_event(
    *,
    event_id: int = 1,
    trade_chain_id: int = 1,
    event_type: str = "TP_FILLED",
    payload: dict | None = None,
):
    from src.runtime_v2.lifecycle.models import ExchangeEvent
    return ExchangeEvent(
        exchange_event_id=event_id,
        trade_chain_id=trade_chain_id,
        event_type=event_type,
        payload_json=json.dumps(payload or {}),
        idempotency_key=f"{event_type}:{trade_chain_id}:{event_id}",
        received_at=_now(),
    )


def _make_chain(
    *,
    trade_chain_id: int = 1,
    state: str = "OPEN",
    side: str = "LONG",
    entry_avg_price: float = 50000.0,
    current_stop_price: float = 49000.0,
    be_status: str = "NOT_PROTECTED",
    be_trigger: str | None = None,
):
    from src.runtime_v2.lifecycle.models import TradeChain
    from src.runtime_v2.signal_enrichment.models import ManagementPlanConfig
    mp = ManagementPlanConfig(be_trigger=be_trigger)
    return TradeChain(
        trade_chain_id=trade_chain_id,
        source_enrichment_id=trade_chain_id,
        canonical_message_id=trade_chain_id * 10,
        raw_message_id=trade_chain_id * 100,
        trader_id="trader_a", account_id="acc_1",
        symbol="BTC/USDT", side=side, lifecycle_state=state,
        entry_mode="ONE_SHOT", management_plan_json=mp.model_dump_json(),
        entry_avg_price=entry_avg_price,
        current_stop_price=current_stop_price,
        be_protection_status=be_status,
    )


def _make_processor():
    from src.runtime_v2.lifecycle.event_processor import LifecycleEventProcessor
    return LifecycleEventProcessor()


def test_entry_filled_transitions_to_open():
    proc = _make_processor()
    event = _make_exchange_event(event_type="ENTRY_FILLED",
                                  payload={"fill_price": 50100.0})
    chain = _make_chain(state="WAITING_ENTRY")
    result = proc.process(event, chain, [])
    assert result.new_lifecycle_state == "OPEN"
    assert result.entry_avg_price == 50100.0
    assert any(e.event_type == "ENTRY_FILLED" for e in result.lifecycle_events)


def test_tp_filled_not_final_transitions_to_partially_closed():
    proc = _make_processor()
    event = _make_exchange_event(event_type="TP_FILLED",
                                  payload={"tp_level": 1, "is_final": False})
    chain = _make_chain(state="OPEN")
    result = proc.process(event, chain, [])
    assert result.new_lifecycle_state == "PARTIALLY_CLOSED"
    assert any(e.event_type == "TP_FILLED" for e in result.lifecycle_events)


def test_tp_filled_final_transitions_to_closed():
    proc = _make_processor()
    event = _make_exchange_event(event_type="TP_FILLED",
                                  payload={"tp_level": 3, "is_final": True})
    chain = _make_chain(state="PARTIALLY_CLOSED")
    result = proc.process(event, chain, [])
    assert result.new_lifecycle_state == "CLOSED"


def test_tp_filled_be_trigger_creates_be_command():
    proc = _make_processor()
    event = _make_exchange_event(event_type="TP_FILLED",
                                  payload={"tp_level": 1, "is_final": False})
    chain = _make_chain(state="OPEN", be_trigger="tp1")
    result = proc.process(event, chain, [])
    assert any(c.command_type == "MOVE_STOP_TO_BREAKEVEN" for c in result.execution_commands)
    assert result.new_be_protection_status == "BE_MOVE_PENDING"


def test_tp_filled_be_trigger_already_protected_noop():
    proc = _make_processor()
    event = _make_exchange_event(event_type="TP_FILLED",
                                  payload={"tp_level": 1, "is_final": False})
    chain = _make_chain(state="OPEN", be_trigger="tp1", be_status="PROTECTED")
    result = proc.process(event, chain, [])
    assert not any(c.command_type == "MOVE_STOP_TO_BREAKEVEN" for c in result.execution_commands)
    assert any(e.event_type == "NOOP_ALREADY_PROTECTED_BE" for e in result.lifecycle_events)


def test_tp_filled_be_trigger_duplicate_command_noop():
    from src.runtime_v2.lifecycle.models import ExecutionCommand
    proc = _make_processor()
    event = _make_exchange_event(event_type="TP_FILLED",
                                  payload={"tp_level": 1, "is_final": False})
    chain = _make_chain(state="OPEN", be_trigger="tp1")
    existing = ExecutionCommand(
        trade_chain_id=1, command_type="MOVE_STOP_TO_BREAKEVEN",
        payload_json="{}", idempotency_key="move_be:1:old", status="PENDING",
    )
    result = proc.process(event, chain, [existing])
    assert not any(c.command_type == "MOVE_STOP_TO_BREAKEVEN" for c in result.execution_commands)
    assert any(e.event_type == "NOOP_DUPLICATE_COMMAND" for e in result.lifecycle_events)


def test_double_tp_filled_same_event_idempotency():
    proc = _make_processor()
    event = _make_exchange_event(event_id=5, event_type="TP_FILLED",
                                  payload={"tp_level": 1, "is_final": False})
    chain = _make_chain(state="OPEN", be_trigger="tp1")
    result1 = proc.process(event, chain, [])
    result2 = proc.process(event, chain, [])
    keys1 = {e.idempotency_key for e in result1.lifecycle_events}
    keys2 = {e.idempotency_key for e in result2.lifecycle_events}
    assert keys1 == keys2


def test_sl_filled_transitions_to_closed():
    proc = _make_processor()
    event = _make_exchange_event(event_type="SL_FILLED", payload={"fill_price": 48900.0})
    chain = _make_chain(state="OPEN")
    result = proc.process(event, chain, [])
    assert result.new_lifecycle_state == "CLOSED"
    assert any(e.event_type == "SL_FILLED" for e in result.lifecycle_events)


def test_tp_filled_with_be_trigger_does_not_set_lifecycle_state_to_be():
    from src.runtime_v2.lifecycle.event_processor import LifecycleEventProcessor
    from src.runtime_v2.lifecycle.models import ExchangeEvent, TradeChain
    proc = LifecycleEventProcessor()
    chain = TradeChain(
        trade_chain_id=10, source_enrichment_id=1, canonical_message_id=2,
        raw_message_id=3, trader_id="t1", account_id="acc1",
        symbol="BTC/USDT", side="LONG", lifecycle_state="OPEN",
        entry_mode="ONE_SHOT",
        management_plan_json='{"be_trigger": "tp1", "be_buffer_pct": 0.0, "close_distribution": {"mode": "table", "table": {}}}',
        entry_avg_price=50000.0,
        open_position_qty=0.01,
        filled_entry_qty=0.01,
    )
    import json as _json
    ev = ExchangeEvent(
        exchange_event_id=1,
        trade_chain_id=chain.trade_chain_id,
        event_type="TP_FILLED",
        payload_json=_json.dumps({
            "tp_level": 1, "is_final": False,
            "fill_price": 51000.0, "filled_qty": 0.005,
        }),
        idempotency_key="tp_filled:10:1",
    )
    result = proc.process(ev, chain, [])
    # lifecycle_state must be PARTIALLY_CLOSED, never BE_MOVE_PENDING
    assert result.new_lifecycle_state == "PARTIALLY_CLOSED"
    assert result.new_be_protection_status == "BE_MOVE_PENDING"


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


# --- Task 6: ENTRY_FILLED qty tracking + weighted avg + WAITING_POSITION release ---

def _make_entry_event(chain_id: int, fill_price: float, filled_qty: float,
                      order_fully_filled: bool = True) -> "ExchangeEvent":
    from src.runtime_v2.lifecycle.models import ExchangeEvent
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


def _make_chain_waiting() -> "TradeChain":
    from src.runtime_v2.lifecycle.models import TradeChain
    return TradeChain(
        trade_chain_id=10, source_enrichment_id=1, canonical_message_id=2,
        raw_message_id=3, trader_id="t1", account_id="acc1",
        symbol="BTC/USDT", side="LONG", lifecycle_state="WAITING_ENTRY",
        entry_mode="ONE_SHOT", management_plan_json='{}',
        planned_entry_qty=0.01,
    )


def _make_chain_open_filled() -> "TradeChain":
    from src.runtime_v2.lifecycle.models import TradeChain
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


def test_entry_filled_first_fill_transitions_to_open():
    from src.runtime_v2.lifecycle.event_processor import LifecycleEventProcessor
    proc = LifecycleEventProcessor()
    chain = _make_chain_waiting()
    ev = _make_entry_event(chain.trade_chain_id, fill_price=50000.0, filled_qty=0.01)
    result = proc.process(ev, chain, [])
    assert result.new_lifecycle_state == "OPEN"


def test_entry_filled_subsequent_fill_keeps_open():
    from src.runtime_v2.lifecycle.event_processor import LifecycleEventProcessor
    proc = LifecycleEventProcessor()
    chain = _make_chain_open_filled()
    ev = _make_entry_event(chain.trade_chain_id, fill_price=50000.0, filled_qty=0.005)
    result = proc.process(ev, chain, [])
    assert result.new_lifecycle_state is None


def test_entry_filled_updates_qty():
    from src.runtime_v2.lifecycle.event_processor import LifecycleEventProcessor
    proc = LifecycleEventProcessor()
    chain = _make_chain_waiting()
    ev = _make_entry_event(chain.trade_chain_id, fill_price=50000.0, filled_qty=0.01)
    result = proc.process(ev, chain, [])
    assert result.new_filled_entry_qty == 0.01
    assert result.new_open_position_qty == 0.01


def test_entry_filled_weighted_average():
    from src.runtime_v2.lifecycle.event_processor import LifecycleEventProcessor
    from src.runtime_v2.lifecycle.models import TradeChain
    proc = LifecycleEventProcessor()
    chain = TradeChain(
        trade_chain_id=10, source_enrichment_id=1, canonical_message_id=2,
        raw_message_id=3, trader_id="t1", account_id="acc1",
        symbol="BTC/USDT", side="LONG", lifecycle_state="OPEN",
        entry_mode="ONE_SHOT", management_plan_json='{}',
        filled_entry_qty=0.006, open_position_qty=0.006,
        entry_avg_price=50000.0,
    )
    ev = _make_entry_event(chain.trade_chain_id, fill_price=52000.0, filled_qty=0.004)
    result = proc.process(ev, chain, [])
    expected_avg = (50000.0 * 0.006 + 52000.0 * 0.004) / 0.010
    assert abs(result.entry_avg_price - expected_avg) < 0.01
    assert result.new_filled_entry_qty == 0.010
    assert result.new_open_position_qty == 0.010


def test_entry_filled_first_fill_releases_waiting_position():
    from src.runtime_v2.lifecycle.event_processor import LifecycleEventProcessor
    proc = LifecycleEventProcessor()
    chain = _make_chain_waiting()
    ev = _make_entry_event(chain.trade_chain_id, fill_price=50000.0, filled_qty=0.01)
    result = proc.process(ev, chain, [])
    assert result.release_waiting_position is True


def test_entry_filled_subsequent_fill_does_not_release_waiting_position():
    from src.runtime_v2.lifecycle.event_processor import LifecycleEventProcessor
    proc = LifecycleEventProcessor()
    chain = _make_chain_open_filled()
    ev = _make_entry_event(chain.trade_chain_id, fill_price=50000.0, filled_qty=0.005)
    result = proc.process(ev, chain, [])
    assert result.release_waiting_position is False


def test_entry_filled_emits_position_size_updated_event():
    from src.runtime_v2.lifecycle.event_processor import LifecycleEventProcessor
    proc = LifecycleEventProcessor()
    chain = _make_chain_waiting()
    ev = _make_entry_event(chain.trade_chain_id, fill_price=50000.0, filled_qty=0.01)
    result = proc.process(ev, chain, [])
    event_types = [e.event_type for e in result.lifecycle_events]
    assert "POSITION_SIZE_UPDATED" in event_types
    assert "ENTRY_AVG_PRICE_UPDATED" in event_types


# --- Task 7: TP/SL/Close fill qty tracking + SYNC_PROTECTIVE_ORDERS ---

def _make_tp_event(chain_id: int, tp_level: int, is_final: bool, fill_qty: float) -> "ExchangeEvent":
    from src.runtime_v2.lifecycle.models import ExchangeEvent
    return ExchangeEvent(
        exchange_event_id=5,
        trade_chain_id=chain_id,
        event_type="TP_FILLED",
        payload_json=json.dumps({
            "fill_price": 51000.0, "filled_qty": fill_qty,
            "tp_level": tp_level, "is_final": is_final,
        }),
        idempotency_key=f"tp_filled:{chain_id}:5",
    )


def _make_close_event(chain_id: int, event_type: str, fill_qty: float) -> "ExchangeEvent":
    from src.runtime_v2.lifecycle.models import ExchangeEvent
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
    from src.runtime_v2.lifecycle.event_processor import LifecycleEventProcessor
    proc = LifecycleEventProcessor()
    chain = _make_chain_open_filled()  # open_position_qty=0.01
    ev = _make_tp_event(chain.trade_chain_id, tp_level=1, is_final=False, fill_qty=0.005)
    result = proc.process(ev, chain, [])
    assert result.new_open_position_qty == 0.005
    assert result.new_closed_position_qty == 0.005


def test_tp_filled_final_closes_chain():
    from src.runtime_v2.lifecycle.event_processor import LifecycleEventProcessor
    proc = LifecycleEventProcessor()
    chain = _make_chain_open_filled()
    ev = _make_tp_event(chain.trade_chain_id, tp_level=2, is_final=True, fill_qty=0.01)
    result = proc.process(ev, chain, [])
    assert result.new_lifecycle_state == "CLOSED"
    assert result.new_open_position_qty == 0.0


def test_tp_filled_non_final_generates_sync_protective_orders():
    from src.runtime_v2.lifecycle.event_processor import LifecycleEventProcessor
    proc = LifecycleEventProcessor()
    chain = _make_chain_open_filled()
    ev = _make_tp_event(chain.trade_chain_id, tp_level=1, is_final=False, fill_qty=0.005)
    result = proc.process(ev, chain, [])
    sync_cmds = [c for c in result.execution_commands if c.command_type == "SYNC_PROTECTIVE_ORDERS"]
    assert len(sync_cmds) == 1


def test_tp_filled_final_no_sync_protective_orders():
    from src.runtime_v2.lifecycle.event_processor import LifecycleEventProcessor
    proc = LifecycleEventProcessor()
    chain = _make_chain_open_filled()
    ev = _make_tp_event(chain.trade_chain_id, tp_level=2, is_final=True, fill_qty=0.01)
    result = proc.process(ev, chain, [])
    sync_cmds = [c for c in result.execution_commands if c.command_type == "SYNC_PROTECTIVE_ORDERS"]
    assert len(sync_cmds) == 0


def test_sl_filled_closes_chain_and_zeroes_open_qty():
    from src.runtime_v2.lifecycle.event_processor import LifecycleEventProcessor
    from src.runtime_v2.lifecycle.models import ExchangeEvent
    proc = LifecycleEventProcessor()
    chain = _make_chain_open_filled()
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
    from src.runtime_v2.lifecycle.event_processor import LifecycleEventProcessor
    from src.runtime_v2.lifecycle.models import ExchangeEvent
    proc = LifecycleEventProcessor()
    chain = _make_chain_open_filled()
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
    from src.runtime_v2.lifecycle.event_processor import LifecycleEventProcessor
    from src.runtime_v2.lifecycle.models import ExchangeEvent
    proc = LifecycleEventProcessor()
    chain = _make_chain_open_filled()
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


# --- Task 8: STOP_MOVED_CONFIRMED and PENDING_ENTRY_CANCELLED_CONFIRMED ---

def test_stop_moved_confirmed_updates_be_protection_and_stop_price():
    from src.runtime_v2.lifecycle.event_processor import LifecycleEventProcessor
    from src.runtime_v2.lifecycle.models import ExchangeEvent, TradeChain
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
    assert result.new_lifecycle_state is None
    assert result.new_be_protection_status == "PROTECTED"
    assert result.current_stop_price == 50000.0


def test_pending_entry_cancelled_confirmed_no_position():
    from src.runtime_v2.lifecycle.event_processor import LifecycleEventProcessor
    from src.runtime_v2.lifecycle.models import ExchangeEvent, TradeChain
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
    from src.runtime_v2.lifecycle.event_processor import LifecycleEventProcessor
    from src.runtime_v2.lifecycle.models import ExchangeEvent, TradeChain
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
    assert result.new_lifecycle_state is None
    sync_cmds = [c for c in result.execution_commands if c.command_type == "SYNC_PROTECTIVE_ORDERS"]
    assert len(sync_cmds) == 1


# ── D_MULTI_ENTRY_MULTI_TP post-fill ─────────────────────────────────────────

import json as _json


def _make_chain_multi_tp(
    *,
    trade_chain_id: int = 10,
    state: str = "WAITING_ENTRY",
    filled_entry_qty: float = 0.0,
    open_position_qty: float = 0.0,
):
    from src.runtime_v2.lifecycle.models import TradeChain
    from src.runtime_v2.signal_enrichment.models import ManagementPlanConfig
    mp = ManagementPlanConfig()
    risk_snap = {
        "tp_rebuild": {
            "levels": [
                {"sequence": 1, "price": 0.52, "close_pct": 50.0},
                {"sequence": 2, "price": 0.55, "close_pct": 50.0},
            ]
        }
    }
    return TradeChain(
        trade_chain_id=trade_chain_id,
        source_enrichment_id=trade_chain_id,
        canonical_message_id=trade_chain_id * 10,
        raw_message_id=trade_chain_id * 100,
        trader_id="trader_a", account_id="acc_1",
        symbol="TOKEN/USDT", side="LONG",
        lifecycle_state=state,
        entry_mode="TWO_STEP",
        management_plan_json=mp.model_dump_json(),
        risk_snapshot_json=_json.dumps(risk_snap),
        execution_mode="D_MULTI_ENTRY_MULTI_TP",
        filled_entry_qty=filled_entry_qty,
        open_position_qty=open_position_qty,
    )


def test_d_multi_entry_multi_tp_first_fill_emits_tp_partial_commands():
    """Primo fill: emette SET_POSITION_TPSL_PARTIAL solo per i TP intermedi (non l'ultimo)."""
    proc = _make_processor()
    chain = _make_chain_multi_tp(state="WAITING_ENTRY")
    event = _make_exchange_event(
        event_type="ENTRY_FILLED",
        payload={"fill_price": 0.50, "filled_qty": 0.7},
    )
    result = proc.process(event, chain, [])
    tp_cmds = [c for c in result.execution_commands if c.command_type == "SET_POSITION_TPSL_PARTIAL"]
    # 2 levels in tp_rebuild, last is attached at entry → only 1 intermediate emitted
    assert len(tp_cmds) == 1
    p = _json.loads(tp_cmds[0].payload_json)
    assert abs(float(p["tp_size"]) - 0.35) < 1e-6   # 0.7 * 50%
    assert p["take_profit"] == 0.52                   # TP1 price (intermediate)


def test_d_multi_entry_multi_tp_second_fill_emits_supersedes_previous():
    """Secondo fill: il TP intermedio aggiornato ha supersedes_previous=True e qty ricalcolata."""
    proc = _make_processor()
    chain = _make_chain_multi_tp(
        state="OPEN",
        filled_entry_qty=0.7,
        open_position_qty=0.7,
    )
    event = _make_exchange_event(
        event_type="ENTRY_FILLED",
        payload={"fill_price": 0.48, "filled_qty": 0.3},
    )
    result = proc.process(event, chain, [])
    tp_cmds = [c for c in result.execution_commands if c.command_type == "SET_POSITION_TPSL_PARTIAL"]
    # Only 1 intermediate TP, last is attached
    assert len(tp_cmds) == 1
    p = _json.loads(tp_cmds[0].payload_json)
    assert p.get("supersedes_previous") is True
    assert abs(float(p["tp_size"]) - 0.5) < 1e-6   # 1.0 * 50%


def test_d_multi_entry_multi_tp_tp_prices_match_tp_rebuild_levels():
    """Solo il prezzo del TP intermedio (TP1=0.52) viene emesso; TP2 (0.55) e' attached e non viene rebuild."""
    proc = _make_processor()
    chain = _make_chain_multi_tp(state="WAITING_ENTRY")
    event = _make_exchange_event(
        event_type="ENTRY_FILLED",
        payload={"fill_price": 0.50, "filled_qty": 1.0},
    )
    result = proc.process(event, chain, [])
    tp_cmds = [c for c in result.execution_commands if c.command_type == "SET_POSITION_TPSL_PARTIAL"]
    prices = {_json.loads(c.payload_json)["take_profit"] for c in tp_cmds}
    assert prices == {0.52}      # TP1 only; TP2 (0.55) stays attached


def test_non_multi_entry_multi_tp_entry_fill_emits_no_tp_commands():
    """Chain con execution_mode diverso da D_MULTI_ENTRY_MULTI_TP: nessun TP command al fill."""
    proc = _make_processor()
    chain = _make_chain(state="WAITING_ENTRY")  # execution_mode default = "a_sequential"
    event = _make_exchange_event(
        event_type="ENTRY_FILLED",
        payload={"fill_price": 50000.0, "filled_qty": 0.1},
    )
    result = proc.process(event, chain, [])
    assert result.execution_commands == []
