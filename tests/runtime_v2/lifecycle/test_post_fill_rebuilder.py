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
        trader_id="t",
        account_id="a",
        symbol="BTC/USDT",
        side=side,
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


def test_multi_tp_emits_single_rebuild_partial_tps_command():
    chain = _make_chain(plan_state_json=_plan_multi_tp([51000.0, 52000.0]))
    cmds = _rebuilder().build_after_fill(chain, filled_entry_qty=0.02, exchange_event_id=7)
    assert len(cmds) == 1
    assert cmds[0].command_type == "REBUILD_PARTIAL_TPS"
    payload = json.loads(cmds[0].payload_json)
    assert payload["symbol"] == "BTC/USDT"
    assert payload["side"] == "LONG"
    assert payload["tps"] == [
        {
            "sequence": 1,
            "price": 51000.0,
            "qty": pytest.approx(0.00666667),
            "order_type": "Limit",
            "limit_price": 51000.0,
            "trigger_by": "MarkPrice",
        },
        {
            "sequence": 2,
            "price": 52000.0,
            "qty": pytest.approx(0.00666667),
            "order_type": "Limit",
            "limit_price": 52000.0,
            "trigger_by": "MarkPrice",
        },
    ]


def test_multi_tp_qty_derived_from_filled_qty_and_total_tps():
    chain = _make_chain(plan_state_json=_plan_multi_tp([51000.0, 52000.0, 53000.0]))
    cmds = _rebuilder().build_after_fill(chain, filled_entry_qty=0.10, exchange_event_id=9)
    assert len(cmds) == 1
    payload = json.loads(cmds[0].payload_json)
    assert payload["tps"][0]["qty"] == pytest.approx(0.025)
    assert payload["tps"][1]["qty"] == pytest.approx(0.025)
    assert payload["tps"][2]["qty"] == pytest.approx(0.025)


def test_multi_tp_two_level_equal_qty_case():
    chain = _make_chain(plan_state_json=_plan_multi_tp([51000.0, 52000.0]))
    cmds = _rebuilder().build_after_fill(chain, filled_entry_qty=0.06, exchange_event_id=10)
    assert len(cmds) == 1
    payload = json.loads(cmds[0].payload_json)
    assert payload["tps"][0]["qty"] == pytest.approx(0.02)
    assert payload["tps"][1]["qty"] == pytest.approx(0.02)


def test_multi_tp_carries_hedge_mode_and_position_idx_in_rebuild_command():
    chain = _make_chain(
        side="SHORT",
        plan_state_json=_plan_multi_tp([51000.0]),
        risk_snap={"hedge_mode": True},
    )
    cmds = _rebuilder().build_after_fill(chain, filled_entry_qty=0.10, exchange_event_id=11)
    assert len(cmds) == 1
    payload = json.loads(cmds[0].payload_json)
    assert payload["hedge_mode"] is True
    assert payload["position_idx"] == 2


def test_multi_tp_idempotency_uses_exchange_event_id():
    chain = _make_chain(plan_state_json=_plan_multi_tp([51000.0]))
    cmds = _rebuilder().build_after_fill(chain, filled_entry_qty=0.10, exchange_event_id=42)
    assert len(cmds) == 1
    assert cmds[0].idempotency_key == "rebuild_partial_tps:1:42"


def test_multi_tp_preserves_sl_and_full_tp():
    chain = _make_chain(plan_state_json=_plan_multi_tp([51000.0]))
    cmds = _rebuilder().build_after_fill(chain, filled_entry_qty=0.10, exchange_event_id=12)
    assert len(cmds) == 1
    payload = json.loads(cmds[0].payload_json)
    assert payload["preserve_sl"] is True
    assert payload["preserve_full_tp"] is True


def test_empty_intermediate_tps_emit_no_command():
    empty_multi_tp_plan = json.dumps({
        "plan_version": 1,
        "rebuild_policy": "ON_EACH_ENTRY_FILL",
        "intermediate_tps": [],
        "final_tp": 51000.0,
    })
    chain = _make_chain(plan_state_json=empty_multi_tp_plan)
    cmds = _rebuilder().build_after_fill(chain, filled_entry_qty=0.01, exchange_event_id=13)
    assert cmds == []


def test_malformed_plan_state_json_emits_nothing():
    chain = _make_chain(plan_state_json="{not-json")
    cmds = _rebuilder().build_after_fill(chain, filled_entry_qty=0.01, exchange_event_id=14)
    assert cmds == []


def test_missing_plan_state_json_emits_nothing():
    chain = _make_chain(plan_state_json="{}")
    cmds = _rebuilder().build_after_fill(chain, filled_entry_qty=0.01, exchange_event_id=10)
    assert cmds == []


def test_execution_command_accepts_rebuild_partial_tps_command_type():
    from src.runtime_v2.lifecycle.models import ExecutionCommand

    cmd = ExecutionCommand(
        trade_chain_id=1,
        command_type="REBUILD_PARTIAL_TPS",
        payload_json="{}",
        idempotency_key="rebuild_partial_tps:1:1",
    )

    assert cmd.command_type == "REBUILD_PARTIAL_TPS"
