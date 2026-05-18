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
