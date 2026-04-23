"""Tests for CanonicalNormalizer: TraderParseResult → CanonicalMessage v1.

All TraderParseResult objects are constructed by hand, simulating Trader A output.
"""

from __future__ import annotations

import pytest

from src.parser.canonical_v1.models import CanonicalMessage
from src.parser.canonical_v1.normalizer import normalize
from src.parser.trader_profiles.base import ParserContext, TraderParseResult


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _ctx(
    raw_text: str = "test message",
    trader_code: str = "trader_a",
    reply_id: int | None = None,
) -> ParserContext:
    return ParserContext(
        trader_code=trader_code,
        message_id=1001,
        reply_to_message_id=reply_id,
        channel_id="chan_123",
        raw_text=raw_text,
    )


def _result(**kwargs: object) -> TraderParseResult:
    defaults: dict[str, object] = {
        "message_type": "UNCLASSIFIED",
        "intents": [],
        "entities": {},
        "target_refs": [],
        "reported_results": [],
        "warnings": [],
        "confidence": 0.7,
        "primary_intent": None,
        "actions_structured": [],
        "target_scope": {},
        "linking": {},
        "diagnostics": {},
    }
    defaults.update(kwargs)
    return TraderParseResult(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Fixtures: helpers for signal entity shapes
# ---------------------------------------------------------------------------

def _signal_entities_one_shot(symbol: str = "BTCUSDT", side: str = "LONG") -> dict:
    return {
        "symbol": symbol,
        "side": side,
        "entry": [50000.0],
        "stop_loss": 48000.0,
        "take_profits": [52000.0, 54000.0],
        "entry_plan_entries": [
            {"sequence": 1, "role": "PRIMARY", "order_type": "LIMIT", "price": 50000.0, "is_optional": False}
        ],
        "entry_structure": "SINGLE",
        "entry_plan_type": "SINGLE_LIMIT",
        "has_averaging_plan": False,
    }


def _signal_entities_two_step(symbol: str = "ETHUSDT") -> dict:
    return {
        "symbol": symbol,
        "side": "LONG",
        "entry": [2000.0, 1950.0],
        "stop_loss": 1900.0,
        "take_profits": [2100.0, 2200.0],
        "entry_plan_entries": [
            {"sequence": 1, "role": "PRIMARY", "order_type": "LIMIT", "price": 2000.0, "is_optional": False},
            {"sequence": 2, "role": "AVERAGING", "order_type": "LIMIT", "price": 1950.0, "is_optional": True},
        ],
        "entry_structure": "TWO_STEP",
        "entry_plan_type": "LIMIT_WITH_LIMIT_AVERAGING",
        "has_averaging_plan": True,
    }


# ---------------------------------------------------------------------------
# SIGNAL tests
# ---------------------------------------------------------------------------

class TestSignal:
    def test_one_shot_complete(self) -> None:
        result = _result(
            message_type="NEW_SIGNAL",
            intents=["NS_CREATE_SIGNAL"],
            entities=_signal_entities_one_shot(),
            confidence=0.9,
            primary_intent="NS_CREATE_SIGNAL",
        )
        msg = normalize(result, _ctx())
        assert msg.primary_class == "SIGNAL"
        assert msg.parse_status == "PARSED"
        assert msg.signal is not None
        assert msg.signal.symbol == "BTCUSDT"
        assert msg.signal.side == "LONG"
        assert msg.signal.entry_structure == "ONE_SHOT"
        assert len(msg.signal.entries) == 1
        assert msg.signal.entries[0].entry_type == "LIMIT"
        assert msg.signal.entries[0].price is not None
        assert msg.signal.entries[0].price.value == 50000.0
        assert msg.signal.stop_loss is not None
        assert msg.signal.stop_loss.price is not None
        assert msg.signal.stop_loss.price.value == 48000.0
        assert len(msg.signal.take_profits) == 2
        assert msg.signal.take_profits[0].price.value == 52000.0

    def test_two_step_complete(self) -> None:
        result = _result(
            message_type="NEW_SIGNAL",
            intents=["NS_CREATE_SIGNAL"],
            entities=_signal_entities_two_step(),
            confidence=0.85,
            primary_intent="NS_CREATE_SIGNAL",
        )
        msg = normalize(result, _ctx())
        assert msg.primary_class == "SIGNAL"
        assert msg.signal is not None
        assert msg.signal.entry_structure == "TWO_STEP"
        assert len(msg.signal.entries) == 2
        assert msg.signal.entries[1].role == "AVERAGING"

    def test_partial_missing_symbol(self) -> None:
        entities = _signal_entities_one_shot()
        entities["symbol"] = None
        result = _result(
            message_type="NEW_SIGNAL",
            intents=["NS_CREATE_SIGNAL"],
            entities=entities,
        )
        msg = normalize(result, _ctx())
        assert msg.primary_class == "SIGNAL"
        assert msg.parse_status == "PARTIAL"
        assert msg.signal is not None
        assert msg.signal.completeness == "INCOMPLETE"
        assert "symbol" in msg.signal.missing_fields

    def test_partial_missing_stop(self) -> None:
        entities = _signal_entities_one_shot()
        entities["stop_loss"] = None
        result = _result(
            message_type="NEW_SIGNAL",
            intents=["NS_CREATE_SIGNAL"],
            entities=entities,
        )
        msg = normalize(result, _ctx())
        assert msg.parse_status == "PARTIAL"
        assert "stop_loss" in msg.signal.missing_fields  # type: ignore[union-attr]

    def test_no_update_payload_in_signal(self) -> None:
        result = _result(
            message_type="NEW_SIGNAL",
            intents=["NS_CREATE_SIGNAL"],
            entities=_signal_entities_one_shot(),
        )
        msg = normalize(result, _ctx())
        assert msg.update is None

    def test_parser_profile_is_trader_code(self) -> None:
        result = _result(
            message_type="NEW_SIGNAL",
            intents=["NS_CREATE_SIGNAL"],
            entities=_signal_entities_one_shot(),
        )
        msg = normalize(result, _ctx(trader_code="trader_a"))
        assert msg.parser_profile == "trader_a"


# ---------------------------------------------------------------------------
# UPDATE SET_STOP tests
# ---------------------------------------------------------------------------

class TestUpdateSetStop:
    def test_move_stop_to_be(self) -> None:
        result = _result(
            message_type="UPDATE",
            intents=["U_MOVE_STOP_TO_BE"],
            entities={"new_stop_level": "ENTRY"},
            target_refs=[{"kind": "reply", "ref": 999}],
            primary_intent="U_MOVE_STOP_TO_BE",
        )
        msg = normalize(result, _ctx(reply_id=999))
        assert msg.primary_class == "UPDATE"
        assert msg.parse_status == "PARSED"
        assert msg.update is not None
        ops = msg.update.operations
        assert len(ops) == 1
        assert ops[0].op_type == "SET_STOP"
        assert ops[0].set_stop is not None
        assert ops[0].set_stop.target_type == "ENTRY"

    def test_move_stop_to_price(self) -> None:
        result = _result(
            message_type="UPDATE",
            intents=["U_MOVE_STOP"],
            entities={"new_stop_level": 49500.0},
            target_refs=[{"kind": "reply", "ref": 100}],
        )
        msg = normalize(result, _ctx())
        assert msg.update is not None
        ops = msg.update.operations
        assert ops[0].set_stop is not None
        assert ops[0].set_stop.target_type == "PRICE"
        assert ops[0].set_stop.value == 49500.0

    def test_move_stop_to_tp_level(self) -> None:
        result = _result(
            message_type="UPDATE",
            intents=["U_MOVE_STOP"],
            entities={"new_stop_level": "TP1"},
            target_refs=[{"kind": "reply", "ref": 100}],
        )
        msg = normalize(result, _ctx())
        ops = msg.update.operations  # type: ignore[union-attr]
        assert ops[0].set_stop is not None
        assert ops[0].set_stop.target_type == "TP_LEVEL"
        assert ops[0].set_stop.value == 1

    def test_move_stop_missing_level_produces_warning(self) -> None:
        result = _result(
            message_type="UPDATE",
            intents=["U_MOVE_STOP"],
            entities={},
            target_refs=[{"kind": "reply", "ref": 100}],
        )
        msg = normalize(result, _ctx())
        assert any("new_stop_level" in w for w in msg.warnings)


# ---------------------------------------------------------------------------
# UPDATE CLOSE tests
# ---------------------------------------------------------------------------

class TestUpdateClose:
    def test_close_full(self) -> None:
        result = _result(
            message_type="UPDATE",
            intents=["U_CLOSE_FULL"],
            entities={"close_scope": "FULL"},
            target_refs=[{"kind": "reply", "ref": 100}],
        )
        msg = normalize(result, _ctx())
        ops = msg.update.operations  # type: ignore[union-attr]
        assert ops[0].op_type == "CLOSE"
        assert ops[0].close is not None
        assert ops[0].close.close_scope == "FULL"

    def test_close_partial_with_fraction(self) -> None:
        result = _result(
            message_type="UPDATE",
            intents=["U_CLOSE_PARTIAL"],
            entities={"close_scope": "PARTIAL", "close_fraction": 0.5},
            target_refs=[{"kind": "reply", "ref": 100}],
        )
        msg = normalize(result, _ctx())
        ops = msg.update.operations  # type: ignore[union-attr]
        assert ops[0].close is not None
        assert ops[0].close.close_scope == "PARTIAL"
        assert ops[0].close.close_fraction == 0.5

    def test_reverse_signal_maps_to_close_with_warning(self) -> None:
        result = _result(
            message_type="UPDATE",
            intents=["U_REVERSE_SIGNAL"],
            entities={},
        )
        msg = normalize(result, _ctx())
        assert msg.update is not None
        ops = msg.update.operations
        assert ops[0].op_type == "CLOSE"
        assert any("U_REVERSE_SIGNAL" in w for w in msg.warnings)


# ---------------------------------------------------------------------------
# UPDATE CANCEL_PENDING tests
# ---------------------------------------------------------------------------

class TestUpdateCancelPending:
    def test_cancel_pending_orders(self) -> None:
        result = _result(
            message_type="UPDATE",
            intents=["U_CANCEL_PENDING_ORDERS"],
            entities={"cancel_scope": "ALL_PENDING_ENTRIES"},
            target_refs=[{"kind": "reply", "ref": 100}],
        )
        msg = normalize(result, _ctx())
        ops = msg.update.operations  # type: ignore[union-attr]
        assert ops[0].op_type == "CANCEL_PENDING"
        assert ops[0].cancel_pending is not None
        assert ops[0].cancel_pending.cancel_scope == "ALL_PENDING_ENTRIES"

    def test_invalidate_setup_maps_to_cancel_pending(self) -> None:
        result = _result(
            message_type="UPDATE",
            intents=["U_INVALIDATE_SETUP"],
            entities={},
        )
        msg = normalize(result, _ctx())
        ops = msg.update.operations  # type: ignore[union-attr]
        assert ops[0].op_type == "CANCEL_PENDING"


# ---------------------------------------------------------------------------
# UPDATE MODIFY_ENTRIES tests
# ---------------------------------------------------------------------------

class TestUpdateModifyEntries:
    def test_add_entry(self) -> None:
        result = _result(
            message_type="UPDATE",
            intents=["U_ADD_ENTRY"],
            entities={"new_entry_price": 49000.0},
            target_refs=[{"kind": "reply", "ref": 100}],
        )
        msg = normalize(result, _ctx())
        ops = msg.update.operations  # type: ignore[union-attr]
        assert ops[0].op_type == "MODIFY_ENTRIES"
        assert ops[0].modify_entries is not None
        assert ops[0].modify_entries.mode == "ADD"
        assert ops[0].modify_entries.entries[0].price.value == 49000.0  # type: ignore[union-attr]

    def test_reenter(self) -> None:
        result = _result(
            message_type="UPDATE",
            intents=["U_REENTER"],
            entities={
                "entry_plan_entries": [
                    {"sequence": 1, "role": "PRIMARY", "order_type": "LIMIT", "price": 49500.0, "is_optional": False}
                ]
            },
            target_refs=[{"kind": "reply", "ref": 100}],
        )
        msg = normalize(result, _ctx())
        ops = msg.update.operations  # type: ignore[union-attr]
        assert ops[0].op_type == "MODIFY_ENTRIES"
        assert ops[0].modify_entries is not None
        assert ops[0].modify_entries.mode == "REENTER"


# ---------------------------------------------------------------------------
# UPDATE MODIFY_TARGETS tests
# ---------------------------------------------------------------------------

class TestUpdateModifyTargets:
    def test_update_take_profits(self) -> None:
        result = _result(
            message_type="UPDATE",
            intents=["U_UPDATE_TAKE_PROFITS"],
            entities={"take_profits": [52000.0, 55000.0, 58000.0]},
            target_refs=[{"kind": "reply", "ref": 100}],
        )
        msg = normalize(result, _ctx())
        ops = msg.update.operations  # type: ignore[union-attr]
        assert ops[0].op_type == "MODIFY_TARGETS"
        assert ops[0].modify_targets is not None
        assert ops[0].modify_targets.mode == "REPLACE_ALL"
        assert len(ops[0].modify_targets.take_profits) == 3


# ---------------------------------------------------------------------------
# REPORT tests
# ---------------------------------------------------------------------------

class TestReport:
    def test_tp_hit(self) -> None:
        result = _result(
            message_type="UPDATE",
            intents=["U_TP_HIT"],
            entities={"hit_target": "TP1"},
            target_refs=[{"kind": "reply", "ref": 100}],
        )
        msg = normalize(result, _ctx())
        assert msg.primary_class == "REPORT"
        assert msg.report is not None
        events = msg.report.events
        assert events[0].event_type == "TP_HIT"
        assert events[0].level == 1

    def test_stop_hit(self) -> None:
        result = _result(
            message_type="UPDATE",
            intents=["U_STOP_HIT"],
            entities={"hit_target": "STOP"},
            target_refs=[{"kind": "reply", "ref": 100}],
        )
        msg = normalize(result, _ctx())
        assert msg.primary_class == "REPORT"
        events = msg.report.events  # type: ignore[union-attr]
        assert events[0].event_type == "STOP_HIT"

    def test_final_result_with_r_value(self) -> None:
        result = _result(
            message_type="UPDATE",
            intents=["U_REPORT_FINAL_RESULT"],
            entities={},
            reported_results=[{"symbol": "BTCUSDT", "value": 2.5, "unit": "R"}],
        )
        msg = normalize(result, _ctx())
        assert msg.primary_class == "REPORT"
        events = msg.report.events  # type: ignore[union-attr]
        assert events[0].event_type == "FINAL_RESULT"
        assert events[0].result is not None
        assert events[0].result.value == 2.5
        assert events[0].result.unit == "R"

    def test_mark_filled_maps_to_entry_filled(self) -> None:
        result = _result(
            message_type="UPDATE",
            intents=["U_MARK_FILLED"],
            entities={"fill_state": "FILLED"},
        )
        msg = normalize(result, _ctx())
        assert msg.primary_class == "REPORT"
        events = msg.report.events  # type: ignore[union-attr]
        assert events[0].event_type == "ENTRY_FILLED"

    def test_activation_maps_to_entry_filled(self) -> None:
        result = _result(
            message_type="UPDATE",
            intents=["U_ACTIVATION"],
            entities={},
        )
        msg = normalize(result, _ctx())
        assert msg.primary_class == "REPORT"
        events = msg.report.events  # type: ignore[union-attr]
        assert events[0].event_type == "ENTRY_FILLED"

    def test_exit_be_maps_to_breakeven_exit(self) -> None:
        result = _result(
            message_type="UPDATE",
            intents=["U_EXIT_BE"],
            entities={"result_mode": "BREAKEVEN"},
            target_refs=[{"kind": "reply", "ref": 100}],
        )
        msg = normalize(result, _ctx())
        assert msg.primary_class == "REPORT"
        events = msg.report.events  # type: ignore[union-attr]
        assert events[0].event_type == "BREAKEVEN_EXIT"


# ---------------------------------------------------------------------------
# Composite UPDATE + REPORT
# ---------------------------------------------------------------------------

class TestComposite:
    def test_tp_hit_plus_move_stop(self) -> None:
        """TP hit (REPORT) and move stop (UPDATE) in same message."""
        result = _result(
            message_type="UPDATE",
            intents=["U_TP_HIT", "U_MOVE_STOP_TO_BE"],
            entities={"hit_target": "TP1", "new_stop_level": "ENTRY"},
            target_refs=[{"kind": "reply", "ref": 100}],
        )
        msg = normalize(result, _ctx())
        assert msg.primary_class == "UPDATE"
        assert msg.update is not None
        assert msg.report is not None
        assert msg.update.operations[0].op_type == "SET_STOP"
        assert msg.report.events[0].event_type == "TP_HIT"

    def test_final_result_plus_close(self) -> None:
        result = _result(
            message_type="UPDATE",
            intents=["U_CLOSE_FULL", "U_REPORT_FINAL_RESULT"],
            entities={"close_scope": "FULL"},
            reported_results=[{"value": 1.5, "unit": "R"}],
            target_refs=[{"kind": "reply", "ref": 100}],
        )
        msg = normalize(result, _ctx())
        assert msg.primary_class == "UPDATE"
        assert msg.update is not None
        assert msg.report is not None


# ---------------------------------------------------------------------------
# INFO / orphan intents
# ---------------------------------------------------------------------------

class TestInfo:
    def test_risk_note_maps_to_info(self) -> None:
        result = _result(
            message_type="INFO_ONLY",
            intents=["U_RISK_NOTE"],
            entities={},
        )
        msg = normalize(result, _ctx())
        assert msg.primary_class == "INFO"
        assert msg.parse_status == "PARSED"

    def test_unclassified_no_intents_fallback_info(self) -> None:
        result = _result(
            message_type="UNCLASSIFIED",
            intents=[],
            entities={},
        )
        msg = normalize(result, _ctx())
        assert msg.primary_class == "INFO"
        assert msg.parse_status == "UNCLASSIFIED"


# ---------------------------------------------------------------------------
# Targeting tests
# ---------------------------------------------------------------------------

class TestTargeting:
    def test_reply_targeting(self) -> None:
        result = _result(
            message_type="UPDATE",
            intents=["U_CLOSE_FULL"],
            entities={"close_scope": "FULL"},
            target_refs=[{"kind": "reply", "ref": 555}],
            linking={"targeted": True, "reply_to_message_id": 555, "has_global_target_scope": False},
        )
        msg = normalize(result, _ctx(reply_id=555))
        assert msg.targeting is not None
        assert msg.targeting.targeted is True
        assert msg.targeting.strategy == "REPLY_OR_LINK"
        ref_types = [r.ref_type for r in msg.targeting.refs]
        assert "REPLY" in ref_types

    def test_no_targeting_for_signal(self) -> None:
        result = _result(
            message_type="NEW_SIGNAL",
            intents=["NS_CREATE_SIGNAL"],
            entities=_signal_entities_one_shot(),
        )
        msg = normalize(result, _ctx())
        assert msg.targeting is None

    def test_global_scope_targeting(self) -> None:
        result = _result(
            message_type="UPDATE",
            intents=["U_CLOSE_FULL"],
            entities={"close_scope": "ALL_LONGS"},
            target_refs=[],
            linking={"targeted": True, "has_global_target_scope": True},
            target_scope={"kind": "portfolio_side", "scope": "ALL_OPEN_LONGS", "applies_to_all": True},
        )
        msg = normalize(result, _ctx())
        assert msg.targeting is not None
        assert msg.targeting.strategy == "GLOBAL_SCOPE"
        assert msg.targeting.scope.kind == "PORTFOLIO_SIDE"
        assert msg.targeting.scope.side_filter == "LONG"


# ---------------------------------------------------------------------------
# Raw context
# ---------------------------------------------------------------------------

class TestRawContext:
    def test_raw_context_populated(self) -> None:
        ctx = _ctx(raw_text="BTCUSDT long entry: 50000")
        result = _result(
            message_type="NEW_SIGNAL",
            intents=["NS_CREATE_SIGNAL"],
            entities=_signal_entities_one_shot(),
        )
        msg = normalize(result, ctx)
        assert msg.raw_context.raw_text == "BTCUSDT long entry: 50000"
        assert msg.raw_context.source_chat_id == "chan_123"

    def test_reply_id_in_raw_context(self) -> None:
        ctx = _ctx(reply_id=42)
        result = _result(
            message_type="UPDATE",
            intents=["U_MOVE_STOP_TO_BE"],
            entities={"new_stop_level": "ENTRY"},
            target_refs=[{"kind": "reply", "ref": 42}],
        )
        msg = normalize(result, ctx)
        assert msg.raw_context.reply_to_message_id == 42
