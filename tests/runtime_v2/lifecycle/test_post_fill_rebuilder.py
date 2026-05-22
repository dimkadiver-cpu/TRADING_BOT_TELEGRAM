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


def test_multi_tp_emits_intermediate_tp_commands():
    chain = _make_chain(plan_state_json=_plan_multi_tp([51000.0, 52000.0]))
    cmds = _rebuilder().build_after_fill(chain, filled_entry_qty=0.02, exchange_event_id=7)
    assert len(cmds) == 2
    for cmd in cmds:
        assert cmd.command_type == "SET_POSITION_TPSL_PARTIAL"
    p0 = json.loads(cmds[0].payload_json)
    assert p0["take_profit"] == 51000.0
    assert p0["supersedes_previous"] is True


def test_multi_tp_tp_size_based_on_filled_qty():
    chain = _make_chain(plan_state_json=_plan_multi_tp([51000.0]))
    cmds = _rebuilder().build_after_fill(chain, filled_entry_qty=0.10, exchange_event_id=9)
    assert len(cmds) == 1
    p = json.loads(cmds[0].payload_json)
    assert p["tp_size"] == pytest.approx(0.05)


def test_missing_plan_state_json_emits_nothing():
    chain = _make_chain(plan_state_json="{}")
    cmds = _rebuilder().build_after_fill(chain, filled_entry_qty=0.01, exchange_event_id=10)
    assert cmds == []
