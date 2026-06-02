from __future__ import annotations
from src.runtime_v2.execution_gateway.event_ingest.payload import ExchangeEventPayload


def test_ws_path_all_fields():
    """WS path: all fields present → validation OK."""
    payload = ExchangeEventPayload(
        fill_price=50000.0,
        filled_qty=0.01,
        closed_size=0.01,
        exec_fee=0.275,
        fee_rate=0.00055,
        exec_value=500.0,
        pos_qty=0.0,
        leaves_qty=0.0,
        cum_exec_qty=0.01,
        exchange_event_id="exec123",
        order_id="ord456",
        order_link_id="tsb:1:2:tp:1",
        exchange_time="2026-06-01T12:00:00+00:00",
        tp_level=1,
        command_id=42,
        source="watch_my_trades",
    )
    assert payload.fill_price == 50000.0
    assert payload.fee_rate == 0.00055
    assert payload.closed_size == 0.01
    assert payload.source == "watch_my_trades"


def test_rest_path_ws_only_fields_none():
    """REST path: WS-only fields absent → None, validation OK."""
    payload = ExchangeEventPayload(
        fill_price=50000.0,
        filled_qty=0.01,
        exec_fee=0.55,
        exec_value=500.0,
        exchange_time="2026-06-01T12:00:00+00:00",
        order_id="ord456",
        order_link_id="tsb:1:2:entry:1",
        source="rest_reconciliation",
    )
    assert payload.closed_size is None
    assert payload.fee_rate is None
    assert payload.pos_qty is None


def test_extra_fields_allowed():
    """extra='allow' — unknown fields are preserved, not rejected."""
    raw = {"fill_price": 100.0, "filled_qty": 1.0, "legacy_field": "x"}
    payload = ExchangeEventPayload.model_validate(raw)
    assert payload.fill_price == 100.0
    assert payload.model_extra == {"legacy_field": "x"}


def test_roundtrip_json():
    """model_validate_json(model_dump_json()) is stable."""
    payload = ExchangeEventPayload(
        fill_price=100.0,
        filled_qty=1.0,
        source="watch_my_trades",
    )
    json_str = payload.model_dump_json()
    restored = ExchangeEventPayload.model_validate_json(json_str)
    assert restored.model_dump() == payload.model_dump()
