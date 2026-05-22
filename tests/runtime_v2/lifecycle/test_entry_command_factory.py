from __future__ import annotations
import json
import pytest


def _snap(seq, etype, price, risk, qty, mode, weight):
    return {"sequence": seq, "entry_type": etype, "price": price,
            "risk_amount": risk, "qty": qty, "qty_mode": mode, "weight": weight}


def _tp(seq, price):
    from src.parser_v2.contracts.entities import Price, TakeProfit
    return TakeProfit(sequence=seq, price=Price(raw=str(price), value=price))


def _leg(seq, etype, price, weight):
    from src.parser_v2.contracts.entities import Price
    from src.runtime_v2.signal_enrichment.models import EnrichedEntryLeg
    p = Price(raw=str(price), value=price) if price is not None else None
    return EnrichedEntryLeg(sequence=seq, entry_type=etype, price=p, weight=weight)


def _cmds(eid, entries, tps, snaps, sl=49000.0, symbol="BTC/USDT", side="LONG"):
    from src.runtime_v2.lifecycle.entry_command_factory import EntryCommandFactory
    f = EntryCommandFactory()
    return f.build_entry_commands(
        enrichment_id=eid, symbol=symbol, side=side,
        entries=entries, take_profits=tps, sl_price=sl,
        leverage=10, hedge_mode=False, position_idx=0,
        risk_snapshot={"legs": snaps},
    )


def test_1a_single_limit_1tp_produces_one_attached_cmd():
    cmds = _cmds(1, [_leg(1, "LIMIT", 50000.0, 1.0)], [_tp(1, 51000.0)],
                 [_snap(1, "LIMIT", 50000.0, 100.0, 0.01, "fixed", 1.0)])
    assert len(cmds) == 1
    assert cmds[0].command_type == "PLACE_ENTRY_WITH_ATTACHED_TPSL"
    p = json.loads(cmds[0].payload_json)
    assert p["attached_tpsl"]["mode"] == "FULL"
    assert p["attached_tpsl"]["take_profit"] == 51000.0
    assert p["attached_tpsl"]["stop_loss"] == 49000.0
    assert p["qty"] == pytest.approx(0.01)
    assert cmds[0].idempotency_key == "place_entry_attached:1:leg1"


def test_1b_single_limit_multi_tp_attached_uses_final_tp_only():
    """No intermediate TPs emitted here — only final TP in attached."""
    cmds = _cmds(2, [_leg(1, "LIMIT", 50000.0, 1.0)],
                 [_tp(1, 51000.0), _tp(2, 52000.0)],
                 [_snap(1, "LIMIT", 50000.0, 100.0, 0.01, "fixed", 1.0)])
    assert len(cmds) == 1
    p = json.loads(cmds[0].payload_json)
    assert p["attached_tpsl"]["take_profit"] == 52000.0   # final TP only


def test_2a_multi_limit_1tp_leg1_attached_leg2_plain():
    cmds = _cmds(3,
                 [_leg(1, "LIMIT", 50000.0, 0.5), _leg(2, "LIMIT", 48000.0, 0.5)],
                 [_tp(1, 51000.0)],
                 [_snap(1, "LIMIT", 50000.0, 50.0, 0.005, "fixed", 0.5),
                  _snap(2, "LIMIT", 48000.0, 50.0, 0.0167, "fixed", 0.5)])
    assert len(cmds) == 2
    assert cmds[0].command_type == "PLACE_ENTRY_WITH_ATTACHED_TPSL"
    assert cmds[1].command_type == "PLACE_ENTRY"
    p0 = json.loads(cmds[0].payload_json)
    p2 = json.loads(cmds[1].payload_json)
    assert p0["sequence"] == 1
    assert p2["sequence"] == 2
    assert "attached_tpsl" not in p2
    assert cmds[1].idempotency_key == "place_entry:3:leg2"


def test_2b_multi_limit_multi_tp_leg1_full_attached_legs2_plain():
    cmds = _cmds(4,
                 [_leg(1, "LIMIT", 50000.0, 0.5), _leg(2, "LIMIT", 48000.0, 0.5)],
                 [_tp(1, 51000.0), _tp(2, 52000.0)],
                 [_snap(1, "LIMIT", 50000.0, 50.0, 0.005, "fixed", 0.5),
                  _snap(2, "LIMIT", 48000.0, 50.0, 0.0167, "fixed", 0.5)])
    assert cmds[0].command_type == "PLACE_ENTRY_WITH_ATTACHED_TPSL"
    p0 = json.loads(cmds[0].payload_json)
    assert p0["attached_tpsl"]["take_profit"] == 52000.0  # final TP
    assert cmds[1].command_type == "PLACE_ENTRY"


def test_3a_market_deferred_uses_qty_mode_deferred():
    cmds = _cmds(5, [_leg(1, "MARKET", None, 1.0)], [_tp(1, 51000.0)],
                 [_snap(1, "MARKET", None, 100.0, None, "deferred_market", 1.0)])
    assert len(cmds) == 1
    assert cmds[0].command_type == "PLACE_ENTRY_WITH_ATTACHED_TPSL"
    p = json.loads(cmds[0].payload_json)
    assert p["qty_mode"] == "deferred_market"
    assert p["risk_amount"] == pytest.approx(100.0)
    assert p["sl_price"] == 49000.0
    assert "qty" not in p
    assert "attached_tpsl" in p
    assert p["attached_tpsl"]["stop_loss"] == 49000.0
    assert p["attached_tpsl"]["sl_trigger_by"] == "MarkPrice"


def test_4b_market_plus_limits_multi_tp():
    cmds = _cmds(6,
                 [_leg(1, "MARKET", None, 0.5), _leg(2, "LIMIT", 48000.0, 0.5)],
                 [_tp(1, 51000.0), _tp(2, 52000.0)],
                 [_snap(1, "MARKET", None, 50.0, None, "deferred_market", 0.5),
                  _snap(2, "LIMIT", 48000.0, 50.0, 0.0167, "fixed", 0.5)])
    assert cmds[0].command_type == "PLACE_ENTRY_WITH_ATTACHED_TPSL"
    p0 = json.loads(cmds[0].payload_json)
    assert p0["qty_mode"] == "deferred_market"
    assert p0["attached_tpsl"]["take_profit"] == 52000.0
    assert cmds[1].command_type == "PLACE_ENTRY"


def test_entries_sorted_by_sequence_regardless_of_input_order():
    """leg with sequence=2 arrives first in list — leg1 must still be ATTACHED."""
    cmds = _cmds(7,
                 [_leg(2, "LIMIT", 48000.0, 0.5), _leg(1, "LIMIT", 50000.0, 0.5)],
                 [_tp(1, 51000.0)],
                 [_snap(1, "LIMIT", 50000.0, 50.0, 0.005, "fixed", 0.5),
                  _snap(2, "LIMIT", 48000.0, 50.0, 0.0167, "fixed", 0.5)])
    assert cmds[0].command_type == "PLACE_ENTRY_WITH_ATTACHED_TPSL"
    assert cmds[1].command_type == "PLACE_ENTRY"


def test_no_tps_attached_tpsl_has_no_take_profit_key():
    cmds = _cmds(8, [_leg(1, "LIMIT", 50000.0, 1.0)], [],
                 [_snap(1, "LIMIT", 50000.0, 100.0, 0.01, "fixed", 1.0)])
    p = json.loads(cmds[0].payload_json)
    assert "take_profit" not in p["attached_tpsl"]


def test_deferred_leg2_carries_sl_price():
    """Non-attached deferred legs must include sl_price for adapter qty computation."""
    cmds = _cmds(9,
                 [_leg(1, "LIMIT", 50000.0, 0.5), _leg(2, "MARKET", None, 0.5)],
                 [_tp(1, 51000.0)],
                 [_snap(1, "LIMIT", 50000.0, 50.0, 0.005, "fixed", 0.5),
                  _snap(2, "MARKET", None, 50.0, None, "deferred_market", 0.5)])
    assert cmds[1].command_type == "PLACE_ENTRY"
    p2 = json.loads(cmds[1].payload_json)
    assert p2["qty_mode"] == "deferred_market"
    assert p2["sl_price"] == 49000.0  # default sl in _cmds helper
    assert "qty" not in p2


def test_trade_chain_id_is_zero():
    cmds = _cmds(10, [_leg(1, "LIMIT", 50000.0, 1.0)], [_tp(1, 51000.0)],
                 [_snap(1, "LIMIT", 50000.0, 100.0, 0.01, "fixed", 1.0)])
    assert cmds[0].trade_chain_id == 0


def test_attached_block_trigger_fields():
    cmds = _cmds(11, [_leg(1, "LIMIT", 50000.0, 1.0)], [_tp(1, 51000.0)],
                 [_snap(1, "LIMIT", 50000.0, 100.0, 0.01, "fixed", 1.0)])
    p = json.loads(cmds[0].payload_json)
    assert p["attached_tpsl"]["sl_trigger_by"] == "MarkPrice"
    assert p["attached_tpsl"]["tp_trigger_by"] == "MarkPrice"


def test_deferred_leg_raises_if_sl_none():
    # seq=1 MARKET → hits attached guard first ("sl_price required for attached TPSL")
    from src.runtime_v2.lifecycle.entry_command_factory import EntryCommandFactory
    with pytest.raises(ValueError, match="sl_price required for attached"):
        EntryCommandFactory().build_entry_commands(
            enrichment_id=99, symbol="BTC/USDT", side="LONG",
            entries=[_leg(1, "MARKET", None, 1.0)],
            take_profits=[],
            sl_price=None,
            leverage=10, hedge_mode=False, position_idx=0,
            risk_snapshot={"legs": [_snap(1, "MARKET", None, 100.0, None, "deferred_market", 1.0)]},
        )


def test_attached_block_raises_if_sl_none():
    from src.runtime_v2.lifecycle.entry_command_factory import EntryCommandFactory
    with pytest.raises(ValueError, match="sl_price required for attached"):
        EntryCommandFactory().build_entry_commands(
            enrichment_id=99, symbol="BTC/USDT", side="LONG",
            entries=[_leg(1, "LIMIT", 50000.0, 1.0)],
            take_profits=[],
            sl_price=None,
            leverage=10, hedge_mode=False, position_idx=0,
            risk_snapshot={"legs": [_snap(1, "LIMIT", 50000.0, 100.0, 0.01, "fixed", 1.0)]},
        )
