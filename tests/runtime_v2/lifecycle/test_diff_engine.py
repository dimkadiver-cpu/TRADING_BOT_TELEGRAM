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


def _pending_leg(
    seq: int,
    etype: str,
    price: float | None,
    risk: float,
    qty: float | None,
) -> dict:
    return {
        "leg_id": f"leg_{seq}",
        "sequence": seq,
        "entry_type": etype,
        "price": price,
        "risk_budget": risk,
        "qty": qty,
        "qty_mode": "fixed" if qty is not None else "deferred_market",
        "status": "PENDING",
        "client_order_id": f"place_entry_attached:1:leg{seq}",
    }


def _filled_leg(
    seq: int,
    etype: str,
    price: float | None,
    risk: float,
    qty: float | None,
) -> dict:
    leg = _pending_leg(seq, etype, price, risk, qty)
    leg["status"] = "FILLED"
    return leg


def _engine():
    from src.runtime_v2.lifecycle.diff_engine import ExecutionPlanDiffEngine

    return ExecutionPlanDiffEngine()


def test_case_a_limit_to_market_emits_cancel_and_replace():
    current = _pending_plan([_pending_leg(1, "LIMIT", 50000.0, 100.0, 0.01)])
    target = _pending_plan([_pending_leg(1, "MARKET", None, 100.0, None)])
    actions = _engine().diff(current, target, risk_remaining=100.0, sl_price=49000.0)
    types = [a["action"] for a in actions]
    assert "cancel_pending_entry" in types
    assert "replace_entry_leg" in types
    assert types.index("cancel_pending_entry") < types.index("replace_entry_leg")


def test_case_a_limit_to_market_replace_has_new_qty_from_risk():
    current = _pending_plan([_pending_leg(1, "LIMIT", 50000.0, 100.0, 0.01)])
    target = _pending_plan([_pending_leg(1, "MARKET", None, 100.0, None)])
    actions = _engine().diff(
        current,
        target,
        risk_remaining=100.0,
        sl_price=49000.0,
        current_market_price=50000.0,
    )
    replace = next(a for a in actions if a["action"] == "replace_entry_leg")
    assert replace["new_qty"] == pytest.approx(0.1)


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


def test_zero_risk_distance_is_rejected():
    current = _pending_plan([_pending_leg(1, "LIMIT", 50000.0, 100.0, 0.01)])
    target = _pending_plan([_pending_leg(1, "LIMIT", 49000.0, 100.0, 0.0)])
    with pytest.raises(ValueError, match="zero_risk_distance"):
        _engine().diff(current, target, risk_remaining=100.0, sl_price=49000.0)


def test_keep_remaining_policy_preserves_unfilled_leg_budgets():
    current = _pending_plan([
        _filled_leg(1, "MARKET", None, 50.0, 0.01),
        _pending_leg(2, "LIMIT", 48000.0, 50.0, 0.0167),
    ])
    actions = _engine().diff(current, current, risk_remaining=50.0, sl_price=49000.0)
    leg2_action = next((a for a in actions if a.get("sequence") == 2), None)
    assert leg2_action is not None
    assert leg2_action["action"] == "keep_entry_leg"
