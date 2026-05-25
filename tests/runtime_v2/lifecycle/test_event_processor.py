# tests/runtime_v2/lifecycle/test_event_processor.py
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest


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
    command = next(c for c in result.execution_commands if c.command_type == "MOVE_STOP_TO_BREAKEVEN")
    payload = json.loads(command.payload_json)
    assert payload["new_stop_price"] == 50000.0
    assert payload["is_breakeven"] is True
    assert "be_buffer_pct" not in payload


def test_tp_filled_be_trigger_fee_correction_disabled_keeps_pure_entry_breakeven():
    proc = _make_processor()
    event = _make_exchange_event(
        event_type="TP_FILLED",
        payload={"tp_level": 1, "is_final": False},
    )
    chain = _make_chain(state="OPEN", be_trigger="tp1")
    chain = chain.model_copy(update={
        "management_plan_json": json.dumps({
            "be_trigger": "tp1",
            "be_fee_correction_enabled": False,
            "be_fee_fallback_profile": "bybit_linear",
        }),
        "open_position_qty": 0.01,
        "risk_snapshot_json": json.dumps({
            "open_fee_residual": 4.0,
            "fee_profile": {"standalone_order": 0.0004},
        }),
    })

    result = proc.process(event, chain, [])

    command = next(c for c in result.execution_commands if c.command_type == "MOVE_STOP_TO_BREAKEVEN")
    payload = json.loads(command.payload_json)
    assert payload["new_stop_price"] == 50000.0


def test_tp_filled_be_trigger_payload_contains_protection_style_standalone_for_sequential():
    """Automatic BE trigger on a_sequential chain → protection_style='standalone_order'."""
    proc = _make_processor()
    event = _make_exchange_event(event_type="TP_FILLED",
                                  payload={"tp_level": 1, "is_final": False})
    chain = _make_chain(state="OPEN", be_trigger="tp1")
    chain = chain.model_copy(update={
        "execution_mode": "a_sequential",
        "risk_snapshot_json": '{"hedge_mode": false}',
    })
    result = proc.process(event, chain, [])
    command = next(c for c in result.execution_commands if c.command_type == "MOVE_STOP_TO_BREAKEVEN")
    payload = json.loads(command.payload_json)
    assert payload["protection_style"] == "standalone_order"
    assert "position_idx" in payload


def test_tp_filled_be_trigger_payload_contains_protection_style_attached_for_unified_plan():
    """Automatic BE trigger on UNIFIED_PLAN chain -> protection_style='attached_full'."""
    proc = _make_processor()
    event = _make_exchange_event(event_type="TP_FILLED",
                                  payload={"tp_level": 1, "is_final": False})
    chain = _make_chain(state="OPEN", be_trigger="tp1")
    chain = chain.model_copy(update={
        "execution_mode": "UNIFIED_PLAN",
        "risk_snapshot_json": '{"hedge_mode": false}',
    })
    result = proc.process(event, chain, [])
    command = next(c for c in result.execution_commands if c.command_type == "MOVE_STOP_TO_BREAKEVEN")
    payload = json.loads(command.payload_json)
    assert payload["protection_style"] == "attached_full"
    assert "position_idx" in payload


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
        management_plan_json='{"be_trigger": "tp1", "close_distribution": {"mode": "table", "table": {}}}',
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
        management_plan_json='{"be_trigger": "tp1", "close_distribution": {"mode": "table", "table": {}}}',
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


def test_entry_filled_with_multi_tp_plan_emits_intermediate_tp_commands():
    plan_state = json.dumps({
        "plan_version": 1,
        "rebuild_policy": "ON_EACH_ENTRY_FILL",
        "intermediate_tps": [51000.0],
        "final_tp": 52000.0,
    })
    chain = _make_chain(state="WAITING_ENTRY")
    chain = chain.model_copy(update={
        "plan_state_json": plan_state,
        "execution_mode": "UNIFIED_PLAN",
    })
    event = _make_exchange_event(
        event_type="ENTRY_FILLED",
        payload={"fill_price": 50000.0, "filled_qty": 0.01},
    )
    result = _make_processor().process(event, chain, [])
    tp_cmds = [
        c for c in result.execution_commands
        if c.command_type == "SET_POSITION_TPSL_PARTIAL"
    ]
    assert len(tp_cmds) == 1
    payload = json.loads(tp_cmds[0].payload_json)
    assert payload["take_profit"] == 51000.0


def test_entry_filled_with_single_tp_plan_emits_no_intermediate_cmds():
    plan_state = json.dumps({
        "plan_version": 1,
        "rebuild_policy": "NONE",
        "intermediate_tps": [],
        "final_tp": 51000.0,
    })
    chain = _make_chain(state="WAITING_ENTRY")
    chain = chain.model_copy(update={
        "plan_state_json": plan_state,
        "execution_mode": "UNIFIED_PLAN",
    })
    event = _make_exchange_event(
        event_type="ENTRY_FILLED",
        payload={"fill_price": 50000.0, "filled_qty": 0.01},
    )
    result = _make_processor().process(event, chain, [])
    tp_cmds = [
        c for c in result.execution_commands
        if c.command_type == "SET_POSITION_TPSL_PARTIAL"
    ]
    assert tp_cmds == []


def test_entry_filled_updates_risk_already_realized():
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
    assert result.new_risk_already_realized == pytest.approx(10.0)


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
    # D_POSITION_TPSL uses position-level SL — no qty sync needed after cancel_pending
    sync_cmds = [c for c in result.execution_commands if c.command_type == "SYNC_PROTECTIVE_ORDERS"]
    assert len(sync_cmds) == 0


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
    plan_state = {
        "plan_version": 1,
        "rebuild_policy": "ON_EACH_ENTRY_FILL",
        "intermediate_tps": [0.52],
        "final_tp": 0.55,
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
        plan_state_json=_json.dumps(plan_state),
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


# ──────────────────────────────────────────────────────────────────────────────
# Helpers estesi per cancel averaging
# ──────────────────────────────────────────────────────────────────────────────

def _make_chain_with_plan(
    *,
    trade_chain_id: int = 1,
    state: str = "OPEN",
    side: str = "LONG",
    entry_avg_price: float = 50000.0,
    be_trigger: str | None = None,
    cancel_averaging_pending_after: str | None = None,
    cancel_pending_by_engine: bool = True,
    be_status: str = "NOT_PROTECTED",
    plan_legs: list[dict] | None = None,
    open_position_qty: float = 1.0,
):
    from src.runtime_v2.lifecycle.models import TradeChain
    from src.runtime_v2.signal_enrichment.models import ManagementPlanConfig
    mp = ManagementPlanConfig(
        be_trigger=be_trigger,
        cancel_averaging_pending_after=cancel_averaging_pending_after,
        cancel_pending_by_engine=cancel_pending_by_engine,
    )
    legs = plan_legs or []
    plan_state = json.dumps({"plan_version": 1, "legs": legs})
    return TradeChain(
        trade_chain_id=trade_chain_id,
        source_enrichment_id=trade_chain_id,
        canonical_message_id=trade_chain_id * 10,
        raw_message_id=trade_chain_id * 100,
        trader_id="trader_a", account_id="acc_1",
        symbol="BTCUSDT", side=side,
        lifecycle_state=state,
        entry_mode="LADDER",
        management_plan_json=mp.model_dump_json(),
        entry_avg_price=entry_avg_price,
        open_position_qty=open_position_qty,
        be_protection_status=be_status,
        plan_state_json=plan_state,
    )


def _averaging_legs_fixture():
    """Ritorna una lista di leg con leg 1 FILLED e leg 2/3 PENDING."""
    return [
        {"leg_id": "leg_1", "sequence": 1, "status": "FILLED", "client_order_id": "cid_leg1"},
        {"leg_id": "leg_2", "sequence": 2, "status": "PENDING", "client_order_id": "cid_leg2"},
        {"leg_id": "leg_3", "sequence": 3, "status": "PENDING", "client_order_id": "cid_leg3"},
    ]


def test_cancel_averaging_after_tp1_emits_cancel_commands():
    """Quando TP1 scatta e cancel_averaging_pending_after=tp1, emette CANCEL_PENDING_ENTRY per leg 2 e 3."""
    proc = _make_processor()
    chain = _make_chain_with_plan(
        cancel_averaging_pending_after="tp1",
        plan_legs=_averaging_legs_fixture(),
    )
    event = _make_exchange_event(event_type="TP_FILLED", payload={"tp_level": 1, "is_final": False, "filled_qty": 0.5})

    result = proc.process(event, chain, [])

    cancel_cmds = [c for c in result.execution_commands if c.command_type == "CANCEL_PENDING_ENTRY"]
    assert len(cancel_cmds) == 2
    payloads = [json.loads(c.payload_json) for c in cancel_cmds]
    cids = {p["entry_client_order_id"] for p in payloads}
    assert cids == {"cid_leg2", "cid_leg3"}

    assert any(e.event_type == "AUTO_CANCEL_AVERAGING_REQUESTED" for e in result.lifecycle_events)


def test_cancel_averaging_by_engine_false_skips_auto_cancel():
    """Quando cancel_pending_by_engine=False, nessun cancel automatico viene emesso."""
    proc = _make_processor()
    chain = _make_chain_with_plan(
        cancel_averaging_pending_after="tp1",
        cancel_pending_by_engine=False,
        plan_legs=_averaging_legs_fixture(),
    )
    event = _make_exchange_event(event_type="TP_FILLED", payload={"tp_level": 1, "is_final": False, "filled_qty": 0.5})

    result = proc.process(event, chain, [])

    cancel_cmds = [c for c in result.execution_commands if c.command_type == "CANCEL_PENDING_ENTRY"]
    assert len(cancel_cmds) == 0
    assert not any(e.event_type == "AUTO_CANCEL_AVERAGING_REQUESTED" for e in result.lifecycle_events)


def test_cancel_averaging_no_pending_legs_be_emitted_immediately():
    """Quando non ci sono averaging leg pendenti, il BE viene emesso subito."""
    proc = _make_processor()
    all_filled_legs = [
        {"leg_id": "leg_1", "sequence": 1, "status": "FILLED", "client_order_id": "cid_leg1"},
        {"leg_id": "leg_2", "sequence": 2, "status": "FILLED", "client_order_id": "cid_leg2"},
    ]
    chain = _make_chain_with_plan(
        be_trigger="tp1",
        cancel_averaging_pending_after="tp1",
        plan_legs=all_filled_legs,
    )
    event = _make_exchange_event(event_type="TP_FILLED", payload={"tp_level": 1, "is_final": False, "filled_qty": 0.5})

    result = proc.process(event, chain, [])

    be_cmds = [c for c in result.execution_commands if c.command_type == "MOVE_STOP_TO_BREAKEVEN"]
    assert len(be_cmds) == 1  # BE emesso subito
    cancel_cmds = [c for c in result.execution_commands if c.command_type == "CANCEL_PENDING_ENTRY"]
    assert len(cancel_cmds) == 0
    assert not any(e.event_type == "AUTO_CANCEL_AVERAGING_REQUESTED" for e in result.lifecycle_events)


def test_cancel_averaging_with_be_trigger_defers_be():
    """Quando be_trigger e cancel_averaging coincidono su tp1, il BE viene differito (no MOVE_STOP_TO_BREAKEVEN)."""
    proc = _make_processor()
    chain = _make_chain_with_plan(
        be_trigger="tp1",
        cancel_averaging_pending_after="tp1",
        plan_legs=_averaging_legs_fixture(),
    )
    event = _make_exchange_event(event_type="TP_FILLED", payload={"tp_level": 1, "is_final": False, "filled_qty": 0.5})

    result = proc.process(event, chain, [])

    be_cmds = [c for c in result.execution_commands if c.command_type == "MOVE_STOP_TO_BREAKEVEN"]
    assert len(be_cmds) == 0  # BE non emesso ora

    cancel_cmds = [c for c in result.execution_commands if c.command_type == "CANCEL_PENDING_ENTRY"]
    assert len(cancel_cmds) == 2  # cancel emessi

    assert result.new_plan_state_json is not None
    plan = json.loads(result.new_plan_state_json)
    assert "_be_deferred_by_auto_cancel" in plan
    assert plan["_be_deferred_by_auto_cancel"]["tp_level"] == 1
    assert plan["_be_deferred_by_auto_cancel"]["averaging_legs_pending"] == 2


# --- Task 5: Deferred BE + race guard ---

def test_deferred_be_emitted_after_last_cancel_confirmed():
    """Deferred BE viene emesso quando l'ultima averaging leg viene confermata cancelled."""
    proc = _make_processor()

    plan_with_deferred = {
        "plan_version": 1,
        "legs": [
            {"leg_id": "leg_1", "sequence": 1, "status": "FILLED", "client_order_id": "cid_leg1"},
            {"leg_id": "leg_2", "sequence": 2, "status": "CANCELLED", "client_order_id": "cid_leg2"},
            {"leg_id": "leg_3", "sequence": 3, "status": "PENDING", "client_order_id": "cid_leg3"},
        ],
        "_be_deferred_by_auto_cancel": {"tp_level": 1, "averaging_legs_pending": 1},
    }
    chain = _make_chain_with_plan(
        be_trigger="tp1",
        cancel_averaging_pending_after="tp1",
        plan_legs=[],
        entry_avg_price=50000.0,
        open_position_qty=1.0,
    )
    chain = chain.model_copy(update={"plan_state_json": json.dumps(plan_with_deferred)})

    event = _make_exchange_event(
        event_type="PENDING_ENTRY_CANCELLED_CONFIRMED",
        payload={"cancelled_order_ids": ["cid_leg3"]},
    )

    result = proc.process(event, chain, [])

    be_cmds = [c for c in result.execution_commands if c.command_type == "MOVE_STOP_TO_BREAKEVEN"]
    assert len(be_cmds) == 1
    be_payload = json.loads(be_cmds[0].payload_json)
    assert be_payload["new_stop_price"] == 50000.0  # entry_avg_price (no fee correction in default config)
    assert be_payload["is_breakeven"] is True

    assert result.new_plan_state_json is not None
    final_plan = json.loads(result.new_plan_state_json)
    assert "_be_deferred_by_auto_cancel" not in final_plan  # flag rimosso


def test_deferred_be_not_emitted_until_all_legs_confirmed():
    """Deferred BE NON viene emesso se ci sono ancora averaging leg pending."""
    proc = _make_processor()

    plan_with_deferred = {
        "plan_version": 1,
        "legs": [
            {"leg_id": "leg_1", "sequence": 1, "status": "FILLED", "client_order_id": "cid_leg1"},
            {"leg_id": "leg_2", "sequence": 2, "status": "PENDING", "client_order_id": "cid_leg2"},  # ancora pending
            {"leg_id": "leg_3", "sequence": 3, "status": "PENDING", "client_order_id": "cid_leg3"},
        ],
        "_be_deferred_by_auto_cancel": {"tp_level": 1, "averaging_legs_pending": 2},
    }
    chain = _make_chain_with_plan(plan_legs=[], entry_avg_price=50000.0)
    chain = chain.model_copy(update={"plan_state_json": json.dumps(plan_with_deferred)})

    event = _make_exchange_event(
        event_type="PENDING_ENTRY_CANCELLED_CONFIRMED",
        payload={"cancelled_order_ids": ["cid_leg3"]},
    )

    result = proc.process(event, chain, [])

    be_cmds = [c for c in result.execution_commands if c.command_type == "MOVE_STOP_TO_BREAKEVEN"]
    assert len(be_cmds) == 0  # leg 2 ancora pending → no BE


def test_race_guard_cancel_confirmed_before_entry_filled():
    """PENDING_ENTRY_CANCELLED_CONFIRMED arriva prima di ENTRY_FILLED: chain NON va a CANCELLED."""
    from src.runtime_v2.lifecycle.models import ExecutionCommand

    proc = _make_processor()
    chain = _make_chain_with_plan(
        state="WAITING_ENTRY",
        open_position_qty=0.0,
        plan_legs=[
            {"leg_id": "leg_1", "sequence": 1, "status": "PENDING", "client_order_id": "cid_leg1"},
            {"leg_id": "leg_2", "sequence": 2, "status": "PENDING", "client_order_id": "cid_leg2"},
        ],
    )

    # Leg 1 è stata inviata all'exchange (SENT) ma il fill non è ancora arrivato
    active_cmds = [
        ExecutionCommand(
            trade_chain_id=1,
            command_type="PLACE_ENTRY_WITH_ATTACHED_TPSL",
            status="SENT",
            payload_json="{}",
            idempotency_key="place_entry:1:leg1",
        ),
    ]

    # Leg 2 viene confermata cancelled (ma leg 1 potrebbe ancora fillarsi)
    event = _make_exchange_event(
        event_type="PENDING_ENTRY_CANCELLED_CONFIRMED",
        payload={"cancelled_order_ids": ["cid_leg2"]},
    )

    result = proc.process(event, chain, active_cmds)

    assert result.new_lifecycle_state is None  # NON va a CANCELLED
    assert any(e.event_type == "NOOP_CANCEL_CONFIRMED_POSITION_UNRESOLVED" for e in result.lifecycle_events)


def test_race_guard_allows_cancelled_when_no_entries_in_flight():
    """PENDING_ENTRY_CANCELLED_CONFIRMED con nessun PLACE_ENTRY SENT/ACK → CANCELLED corretto."""
    proc = _make_processor()
    chain = _make_chain_with_plan(
        state="WAITING_ENTRY",
        open_position_qty=0.0,
        plan_legs=[
            {"leg_id": "leg_1", "sequence": 1, "status": "PENDING", "client_order_id": "cid_leg1"},
        ],
    )

    # Nessun comando PLACE_ENTRY in SENT/ACK
    event = _make_exchange_event(
        event_type="PENDING_ENTRY_CANCELLED_CONFIRMED",
        payload={"cancelled_order_ids": ["cid_leg1"]},
    )

    result = proc.process(event, chain, [])

    assert result.new_lifecycle_state == "CANCELLED"


def test_race_guard_noop_when_fewer_in_flight_than_cancellations():
    """1 in-flight entry with 2 cancel confirmations — must NOT go to CANCELLED.

    Old condition: len(entry_in_flight) >= len(cancelled_order_ids)
      → 1 >= 2 → False → CANCELLED (wrong, fill still possible)
    New condition: len(entry_in_flight) > 0
      → 1 > 0 → True → NOOP (correct)
    """
    from src.runtime_v2.lifecycle.models import ExecutionCommand

    proc = _make_processor()
    chain = _make_chain_with_plan(
        state="WAITING_ENTRY",
        open_position_qty=0.0,
        plan_legs=[
            {"leg_id": "leg_1", "sequence": 1, "status": "PENDING", "client_order_id": "cid_leg1"},
            {"leg_id": "leg_2", "sequence": 2, "status": "PENDING", "client_order_id": "cid_leg2"},
            {"leg_id": "leg_3", "sequence": 3, "status": "PENDING", "client_order_id": "cid_leg3"},
        ],
    )

    # Only 1 PLACE_ENTRY still in SENT — it could still fill
    active_cmds = [
        ExecutionCommand(
            trade_chain_id=1,
            command_type="PLACE_ENTRY",
            status="SENT",
            payload_json="{}",
            idempotency_key="place_entry:1:leg1",
        ),
    ]

    # 2 legs confirmed cancelled — more cancellations than in-flight entries
    event = _make_exchange_event(
        event_type="PENDING_ENTRY_CANCELLED_CONFIRMED",
        payload={"cancelled_order_ids": ["cid_leg2", "cid_leg3"]},
    )

    result = proc.process(event, chain, active_cmds)

    assert result.new_lifecycle_state is None  # must NOT go to CANCELLED
    assert any(e.event_type == "NOOP_CANCEL_CONFIRMED_POSITION_UNRESOLVED" for e in result.lifecycle_events)


# --- Task 6: Deferred BE emitted on race fill (averaging leg fills instead of cancelling) ---

def test_deferred_be_emitted_on_race_entry_fill():
    """Se una averaging leg si filla prima del cancel (race), il BE viene emesso dall'ENTRY_FILLED handler."""
    import json

    proc = _make_processor()

    plan_with_deferred = {
        "plan_version": 1,
        "legs": [
            {"leg_id": "leg_1", "sequence": 1, "status": "FILLED", "client_order_id": "cid_leg1"},
            {"leg_id": "leg_2", "sequence": 2, "status": "PENDING", "client_order_id": "cid_leg2"},
        ],
        "_be_deferred_by_auto_cancel": {"tp_level": 1, "averaging_legs_pending": 1},
    }
    chain = _make_chain_with_plan(
        state="OPEN",
        plan_legs=[],
        entry_avg_price=50000.0,
        open_position_qty=1.0,
        be_trigger="tp1",
        cancel_averaging_pending_after="tp1",
    )
    chain = chain.model_copy(update={"plan_state_json": json.dumps(plan_with_deferred)})

    # Leg 2 si filla invece di cancellarsi (race)
    event = _make_exchange_event(
        event_type="ENTRY_FILLED",
        payload={
            "fill_price": 49500.0,
            "filled_qty": 0.5,
            "entry_client_order_id": "cid_leg2",
        },
    )

    result = proc.process(event, chain, [])

    be_cmds = [c for c in result.execution_commands if c.command_type == "MOVE_STOP_TO_BREAKEVEN"]
    assert len(be_cmds) == 1
    assert result.new_plan_state_json is not None
    final_plan = json.loads(result.new_plan_state_json)
    assert "_be_deferred_by_auto_cancel" not in final_plan
