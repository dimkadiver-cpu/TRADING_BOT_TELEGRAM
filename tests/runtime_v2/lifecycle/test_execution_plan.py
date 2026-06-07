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
    return {"sl_price": sl_price, "legs": legs}


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


def _build(enrichment_id: int, entries, tps, risk_snap: dict, extra: dict | None = None) -> dict:
    from src.runtime_v2.lifecycle.execution_plan import ExecutionPlanBuilder
    plan_json = ExecutionPlanBuilder.build(enrichment_id, entries, tps, risk_snap, extra)
    return json.loads(plan_json)


def test_case_1a_single_limit_single_tp():
    entries = _make_entries([(1, "LIMIT", 50000.0, 1.0)])
    tps = [_make_tp(1, 51000.0)]
    risk_snap = _make_risk_snap(sl_price=49000.0)
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


def test_case_1b_single_limit_multi_tp():
    entries = _make_entries([(1, "LIMIT", 50000.0, 1.0)])
    tps = [_make_tp(1, 51000.0), _make_tp(2, 52000.0)]
    risk_snap = _make_risk_snap(sl_price=49000.0)
    plan = _build(2, entries, tps, risk_snap)
    assert plan["rebuild_policy"] == "ON_EACH_ENTRY_FILL"
    assert plan["final_tp"] == 52000.0
    assert plan["intermediate_tps"] == [51000.0]


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


def test_final_tp_is_last_tp():
    entries = _make_entries([(1, "LIMIT", 50000.0, 1.0)])
    tps = [_make_tp(1, 51000.0), _make_tp(2, 52000.0), _make_tp(3, 53000.0)]
    risk_snap = _make_risk_snap(sl_price=49000.0)
    plan = _build(5, entries, tps, risk_snap)
    assert plan["final_tp"] == 53000.0
    assert plan["intermediate_tps"] == [51000.0, 52000.0]


def test_entry_gate_populates_plan_state_json():
    """Integration: process_signal must set plan_state_json on the returned TradeChain."""
    from src.runtime_v2.lifecycle.entry_gate import LifecycleEntryGate
    from src.runtime_v2.lifecycle.risk_capacity import RiskCapacityEngine
    from src.runtime_v2.lifecycle.static_exchange_data_port import StaticExchangeDataPort
    from tests.runtime_v2.lifecycle.test_entry_gate import _make_enriched_signal

    enriched = _make_enriched_signal(
        enrichment_id=10,
        entry_type="LIMIT",
        entry_price=50000.0,
        sl_price=49000.0,
        tp_prices=[51000.0],
        capital_base_usdt=1000.0,
    )
    gate = LifecycleEntryGate(
        risk_engine=RiskCapacityEngine(),
        exchange_port=StaticExchangeDataPort(),
        simple_attached_enabled=True,
    )
    result = gate.process_signal(enriched, [], "NONE")
    assert result.trade_chain is not None
    plan = json.loads(result.trade_chain.plan_state_json)
    assert plan["plan_version"] == 1
    assert plan["rebuild_policy"] == "NONE"
    assert plan["legs"][0]["client_order_id"] == "place_entry_attached:10:leg1"


def test_get_pending_averaging_legs_returns_sequence_gt_1():
    from src.runtime_v2.lifecycle.execution_plan import ExecutionPlanBuilder

    plan = {
        "legs": [
            {"leg_id": "leg_1", "sequence": 1, "status": "FILLED", "client_order_id": "cid_1"},
            {"leg_id": "leg_2", "sequence": 2, "status": "PENDING", "client_order_id": "cid_2"},
            {"leg_id": "leg_3", "sequence": 3, "status": "CANCELLED", "client_order_id": "cid_3"},
        ]
    }
    result = ExecutionPlanBuilder.get_pending_averaging_legs(json.dumps(plan))
    assert len(result) == 1
    assert result[0]["leg_id"] == "leg_2"
    assert result[0]["client_order_id"] == "cid_2"


def test_get_pending_averaging_legs_empty_when_all_filled():
    from src.runtime_v2.lifecycle.execution_plan import ExecutionPlanBuilder

    plan = {
        "legs": [
            {"leg_id": "leg_1", "sequence": 1, "status": "FILLED"},
            {"leg_id": "leg_2", "sequence": 2, "status": "FILLED"},
        ]
    }
    result = ExecutionPlanBuilder.get_pending_averaging_legs(json.dumps(plan))
    assert result == []


def test_get_pending_averaging_legs_returns_empty_on_bad_json():
    from src.runtime_v2.lifecycle.execution_plan import ExecutionPlanBuilder
    result = ExecutionPlanBuilder.get_pending_averaging_legs("not-json")
    assert result == []


def test_build_includes_extra_plan_metadata_keys():
    entries = _make_entries([(1, "LIMIT", 50000.0, 1.0)])
    tps = [_make_tp(1, 51000.0)]
    risk_snap = _make_risk_snap()
    extra = {
        "risk_hint_applied": {
            "hint_used": True,
            "hint_raw": "1%",
            "hint_effective_pct": 1.0,
            "configured_risk_pct": 2.0,
            "effective_risk_pct": 1.0,
        }
    }
    plan = _build(1, entries, tps, risk_snap, extra)
    assert "risk_hint_applied" in plan
    assert plan["risk_hint_applied"]["hint_raw"] == "1%"
    assert plan["risk_hint_applied"]["hint_effective_pct"] == 1.0


def test_build_without_extra_metadata_has_no_hint_key():
    entries = _make_entries([(1, "LIMIT", 50000.0, 1.0)])
    tps = [_make_tp(1, 51000.0)]
    risk_snap = _make_risk_snap()
    plan = _build(1, entries, tps, risk_snap)
    assert "risk_hint_applied" not in plan


def test_build_with_none_extra_metadata_has_no_hint_key():
    entries = _make_entries([(1, "LIMIT", 50000.0, 1.0)])
    tps = [_make_tp(1, 51000.0)]
    risk_snap = _make_risk_snap()
    plan = _build(1, entries, tps, risk_snap, None)
    assert "risk_hint_applied" not in plan


def test_build_extra_metadata_does_not_overwrite_plan_version():
    entries = _make_entries([(1, "LIMIT", 50000.0, 1.0)])
    tps = [_make_tp(1, 51000.0)]
    risk_snap = _make_risk_snap()
    extra = {"range_derivation": {"derived_from_range": True, "split_mode": "midpoint",
                                   "original_min_price": 63000.0, "original_max_price": 65000.0}}
    plan = _build(1, entries, tps, risk_snap, extra)
    assert plan["plan_version"] == 1
    assert "range_derivation" in plan
    assert plan["range_derivation"]["split_mode"] == "midpoint"
