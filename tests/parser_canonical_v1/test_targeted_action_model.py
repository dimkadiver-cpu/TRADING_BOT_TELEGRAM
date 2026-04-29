"""Tests for targeted action / report models — Phase 1 exit criteria.

Covers:
- serialisation/deserialisation of TargetedAction for every action_type
- serialisation/deserialisation of TargetedReport for every event_type
- CanonicalMessage with empty targeted_actions / targeted_reports
- CanonicalMessage with the three proposal cases (JSON round-trip)
- Pydantic validators reject non-conforming shapes
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.parser.canonical_v1.models import (
    CancelPendingParams,
    CloseParams,
    ModifyEntriesParams,
    ModifyTargetsParams,
    SetStopParams,
    TargetedAction,
    TargetedActionDiagnostics,
    TargetedActionTargeting,
    TargetedReport,
    TargetedReportResult,
    CanonicalMessage,
    RawContext,
    UpdatePayload,
    UpdateOperation,
    StopTarget,
    CloseOperation,
    ReportPayload,
    Targeting,
    TargetRef,
    TargetScope,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _targeting_group(targets: list[int]) -> TargetedActionTargeting:
    return TargetedActionTargeting(mode="TARGET_GROUP", targets=targets)


def _targeting_explicit(targets: list[int]) -> TargetedActionTargeting:
    return TargetedActionTargeting(mode="EXPLICIT_TARGETS", targets=targets)


def _targeting_selector(selector: dict) -> TargetedActionTargeting:
    return TargetedActionTargeting(mode="SELECTOR", selector=selector)


def _raw(text: str = "msg") -> RawContext:
    return RawContext(raw_text=text)


def _minimal_update_msg(**extra) -> CanonicalMessage:
    return CanonicalMessage(
        parser_profile="trader_x",
        primary_class="UPDATE",
        parse_status="PARSED",
        confidence=0.9,
        update=UpdatePayload(
            operations=[
                UpdateOperation(
                    op_type="SET_STOP",
                    set_stop=StopTarget(target_type="ENTRY"),
                )
            ]
        ),
        raw_context=_raw(),
        **extra,
    )


# ---------------------------------------------------------------------------
# 1. TargetedAction — serialisation / deserialisation for each action_type
# ---------------------------------------------------------------------------

class TestTargetedActionSerialization:

    def test_set_stop_round_trip(self) -> None:
        action = TargetedAction(
            action_type="SET_STOP",
            params={"target_type": "ENTRY"},
            targeting=_targeting_group([1, 2]),
            raw_fragment="stop in be",
            confidence=0.92,
        )
        data = action.model_dump()
        restored = TargetedAction.model_validate(data)
        assert restored.action_type == "SET_STOP"
        assert restored.targeting.targets == [1, 2]

    def test_close_round_trip(self) -> None:
        action = TargetedAction(
            action_type="CLOSE",
            params={"close_scope": "FULL"},
            targeting=_targeting_group([10, 20]),
        )
        data = action.model_dump()
        restored = TargetedAction.model_validate(data)
        assert restored.action_type == "CLOSE"

    def test_cancel_pending_round_trip(self) -> None:
        action = TargetedAction(
            action_type="CANCEL_PENDING",
            params={"cancel_scope": "TARGETED"},
            targeting=_targeting_explicit([5]),
        )
        data = action.model_dump()
        restored = TargetedAction.model_validate(data)
        assert restored.action_type == "CANCEL_PENDING"

    def test_modify_entries_round_trip(self) -> None:
        action = TargetedAction(
            action_type="MODIFY_ENTRIES",
            params={"mode": "ADD", "entries": []},
            targeting=_targeting_explicit([7]),
        )
        data = action.model_dump()
        restored = TargetedAction.model_validate(data)
        assert restored.action_type == "MODIFY_ENTRIES"

    def test_modify_targets_round_trip(self) -> None:
        action = TargetedAction(
            action_type="MODIFY_TARGETS",
            params={"mode": "REPLACE_ALL", "take_profits": []},
            targeting=_targeting_explicit([3]),
        )
        data = action.model_dump()
        restored = TargetedAction.model_validate(data)
        assert restored.action_type == "MODIFY_TARGETS"

    def test_with_diagnostics_round_trip(self) -> None:
        action = TargetedAction(
            action_type="SET_STOP",
            params={"target_type": "ENTRY"},
            targeting=_targeting_group([1, 2]),
            diagnostics=TargetedActionDiagnostics(
                resolution_unit="MESSAGE_WIDE",
                semantic_signature="SET_STOP:ENTRY",
                applied_disambiguation_rules=["prefer_be"],
                grouping_reason="same_action_same_params",
            ),
        )
        data = action.model_dump()
        restored = TargetedAction.model_validate(data)
        assert restored.diagnostics is not None
        assert restored.diagnostics.resolution_unit == "MESSAGE_WIDE"
        assert restored.diagnostics.semantic_signature == "SET_STOP:ENTRY"
        assert restored.diagnostics.applied_disambiguation_rules == ["prefer_be"]


# ---------------------------------------------------------------------------
# 2. TargetedReport — serialisation / deserialisation for each event_type
# ---------------------------------------------------------------------------

class TestTargetedReportSerialization:

    def test_entry_filled_round_trip(self) -> None:
        report = TargetedReport(
            event_type="ENTRY_FILLED",
            targeting=_targeting_explicit([100]),
        )
        data = report.model_dump()
        restored = TargetedReport.model_validate(data)
        assert restored.event_type == "ENTRY_FILLED"

    def test_tp_hit_round_trip(self) -> None:
        report = TargetedReport(
            event_type="TP_HIT",
            level=1,
            result=TargetedReportResult(value=3.5, unit="PERCENT"),
            targeting=_targeting_explicit([100]),
        )
        data = report.model_dump()
        restored = TargetedReport.model_validate(data)
        assert restored.event_type == "TP_HIT"
        assert restored.level == 1
        assert restored.result is not None
        assert restored.result.value == pytest.approx(3.5)

    def test_stop_hit_round_trip(self) -> None:
        report = TargetedReport(
            event_type="STOP_HIT",
            targeting=_targeting_explicit([200]),
            instrument_hint="BTC",
        )
        data = report.model_dump()
        restored = TargetedReport.model_validate(data)
        assert restored.event_type == "STOP_HIT"
        assert restored.instrument_hint == "BTC"

    def test_breakeven_exit_round_trip(self) -> None:
        report = TargetedReport(
            event_type="BREAKEVEN_EXIT",
            targeting=_targeting_explicit([300]),
        )
        data = report.model_dump()
        restored = TargetedReport.model_validate(data)
        assert restored.event_type == "BREAKEVEN_EXIT"

    def test_final_result_round_trip(self) -> None:
        report = TargetedReport(
            event_type="FINAL_RESULT",
            result=TargetedReportResult(value=4.2, unit="PERCENT"),
            targeting=_targeting_explicit([400]),
        )
        data = report.model_dump()
        restored = TargetedReport.model_validate(data)
        assert restored.event_type == "FINAL_RESULT"
        assert restored.result is not None
        assert restored.result.unit == "PERCENT"

    def test_final_result_missing_result_emits_warning(self) -> None:
        with pytest.warns(UserWarning, match="FINAL_RESULT"):
            TargetedReport(
                event_type="FINAL_RESULT",
                result=None,
                targeting=_targeting_explicit([400]),
            )


# ---------------------------------------------------------------------------
# 3. CanonicalMessage with empty targeted lists serialises correctly
# ---------------------------------------------------------------------------

class TestCanonicalMessageEmptyTargeted:

    def test_empty_lists_serialise(self) -> None:
        msg = _minimal_update_msg()
        data = msg.model_dump()
        assert data["targeted_actions"] == []
        assert data["targeted_reports"] == []

    def test_round_trip_with_targeted_actions(self) -> None:
        msg = _minimal_update_msg(
            targeted_actions=[
                TargetedAction(
                    action_type="SET_STOP",
                    params={"target_type": "ENTRY"},
                    targeting=_targeting_group([1, 2]),
                )
            ]
        )
        data = msg.model_dump()
        restored = CanonicalMessage.model_validate(data)
        assert len(restored.targeted_actions) == 1
        assert restored.targeted_actions[0].action_type == "SET_STOP"

    def test_round_trip_with_targeted_reports(self) -> None:
        msg = _minimal_update_msg(
            targeted_reports=[
                TargetedReport(
                    event_type="FINAL_RESULT",
                    result=TargetedReportResult(value=3.5, unit="PERCENT"),
                    targeting=_targeting_explicit([10]),
                )
            ]
        )
        data = msg.model_dump()
        restored = CanonicalMessage.model_validate(data)
        assert len(restored.targeted_reports) == 1
        assert restored.targeted_reports[0].event_type == "FINAL_RESULT"


# ---------------------------------------------------------------------------
# 4. CanonicalMessage with the three proposal case JSONs
# ---------------------------------------------------------------------------

CASO_1 = {
    "schema_version": "1.1",
    "parser_profile": "trader_a",
    "primary_class": "UPDATE",
    "parse_status": "PARSED",
    "confidence": 0.92,
    "intents": ["MOVE_STOP_TO_BE"],
    "primary_intent": "MOVE_STOP_TO_BE",
    "targeting": {
        "refs": [
            {"ref_type": "TELEGRAM_LINK", "value": "https://t.me/c/3171748254/1725"},
            {"ref_type": "MESSAGE_ID", "value": 1725},
            {"ref_type": "TELEGRAM_LINK", "value": "https://t.me/c/3171748254/1726"},
            {"ref_type": "MESSAGE_ID", "value": 1726},
        ],
        "scope": {"kind": "SINGLE_SIGNAL", "value": None, "side_filter": None, "applies_to_all": False},
        "strategy": "REPLY_OR_LINK",
        "targeted": True,
    },
    "update": {
        "operations": [
            {
                "op_type": "SET_STOP",
                "set_stop": {"target_type": "ENTRY", "value": None},
                "raw_fragment": "пора перенести стоп в бу",
                "confidence": 0.92,
            }
        ]
    },
    "targeted_actions": [
        {
            "action_type": "SET_STOP",
            "params": {"target_type": "ENTRY"},
            "targeting": {"mode": "TARGET_GROUP", "targets": [1725, 1726]},
            "raw_fragment": "пора перенести стоп в бу",
            "confidence": 0.92,
            "diagnostics": {
                "resolution_unit": "MESSAGE_WIDE",
                "semantic_signature": "SET_STOP:ENTRY",
            },
        }
    ],
    "targeted_reports": [],
    "warnings": [],
    "diagnostics": {"multi_ref_mode": True},
    "raw_context": {
        "raw_text": "https://t.me/c/3171748254/1725\nhttps://t.me/c/3171748254/1726\n\nпора перенести стоп в бу",
        "reply_to_message_id": None,
        "extracted_links": [
            "https://t.me/c/3171748254/1725",
            "https://t.me/c/3171748254/1726",
        ],
        "hashtags": [],
        "source_chat_id": "3171748254",
        "source_topic_id": None,
        "acquisition_mode": "live",
    },
}

CASO_2 = {
    "schema_version": "1.1",
    "parser_profile": "trader_a",
    "primary_class": "UPDATE",
    "parse_status": "PARSED",
    "confidence": 0.94,
    "intents": ["CLOSE_FULL", "REPORT_FINAL_RESULT"],
    "primary_intent": "CLOSE_FULL",
    "targeting": {
        "refs": [
            {"ref_type": "TELEGRAM_LINK", "value": "https://t.me/c/3171748254/822"},
            {"ref_type": "MESSAGE_ID", "value": 822},
            {"ref_type": "TELEGRAM_LINK", "value": "https://t.me/c/3171748254/856"},
            {"ref_type": "MESSAGE_ID", "value": 856},
        ],
        "scope": {"kind": "SINGLE_SIGNAL", "value": None, "side_filter": None, "applies_to_all": False},
        "strategy": "REPLY_OR_LINK",
        "targeted": True,
    },
    "update": {
        "operations": [
            {
                "op_type": "CLOSE",
                "close": {"close_scope": "FULL", "close_fraction": None, "close_price": None},
                "raw_fragment": "закрываю по текущим",
                "confidence": 0.94,
            }
        ]
    },
    "report": {"events": [], "reported_result": None, "notes": []},
    "targeted_actions": [
        {
            "action_type": "CLOSE",
            "params": {"close_scope": "FULL"},
            "targeting": {"mode": "TARGET_GROUP", "targets": [822, 856]},
            "raw_fragment": "закрываю по текущим",
            "confidence": 0.94,
            "diagnostics": {"resolution_unit": "MESSAGE_WIDE", "semantic_signature": "CLOSE:FULL"},
        }
    ],
    "targeted_reports": [
        {
            "event_type": "FINAL_RESULT",
            "result": {"value": 3.94, "unit": "PERCENT", "text": None},
            "level": None,
            "targeting": {"mode": "EXPLICIT_TARGETS", "targets": [822]},
            "instrument_hint": "XRP",
            "raw_fragment": "XRP 3.94% прибыли",
            "confidence": 0.93,
            "diagnostics": {
                "resolution_unit": "TARGET_ITEM_WIDE",
                "semantic_signature": "FINAL_RESULT:PERCENT",
            },
        },
        {
            "event_type": "FINAL_RESULT",
            "result": {"value": -9.32, "unit": "PERCENT", "text": None},
            "level": None,
            "targeting": {"mode": "EXPLICIT_TARGETS", "targets": [856]},
            "instrument_hint": "ENA",
            "raw_fragment": "ENA убыток 9.32",
            "confidence": 0.93,
            "diagnostics": {
                "resolution_unit": "TARGET_ITEM_WIDE",
                "semantic_signature": "FINAL_RESULT:PERCENT",
            },
        },
    ],
    "warnings": [],
    "diagnostics": {"multi_ref_mode": True},
    "raw_context": {
        "raw_text": "XRP 3.94%\nENA -9.32%\nзакрываю по текущим",
        "reply_to_message_id": None,
        "extracted_links": [],
        "hashtags": [],
        "source_chat_id": "3171748254",
        "source_topic_id": None,
        "acquisition_mode": "live",
    },
}

CASO_3 = {
    "schema_version": "1.1",
    "parser_profile": "trader_a",
    "primary_class": "UPDATE",
    "parse_status": "PARSED",
    "confidence": 0.95,
    "intents": ["MOVE_STOP_TO_BE", "MOVE_STOP"],
    "primary_intent": "MOVE_STOP_TO_BE",
    "targeting": {
        "refs": [
            {"ref_type": "MESSAGE_ID", "value": 978},
            {"ref_type": "MESSAGE_ID", "value": 1002},
            {"ref_type": "MESSAGE_ID", "value": 1003},
            {"ref_type": "MESSAGE_ID", "value": 1005},
            {"ref_type": "MESSAGE_ID", "value": 1018},
        ],
        "scope": {"kind": "SINGLE_SIGNAL", "value": None, "side_filter": None, "applies_to_all": False},
        "strategy": "REPLY_OR_LINK",
        "targeted": True,
    },
    "update": {
        "operations": [
            {
                "op_type": "SET_STOP",
                "set_stop": {"target_type": "ENTRY", "value": None},
                "raw_fragment": "стоп в бу",
                "confidence": 0.94,
            },
            {
                "op_type": "SET_STOP",
                "set_stop": {"target_type": "TP_LEVEL", "value": 1},
                "raw_fragment": "стоп на 1 тейк",
                "confidence": 0.93,
            },
        ]
    },
    "targeted_actions": [
        {
            "action_type": "SET_STOP",
            "params": {"target_type": "ENTRY"},
            "targeting": {"mode": "EXPLICIT_TARGETS", "targets": [978, 1002, 1003, 1018]},
            "raw_fragment": "стоп в бу x4",
            "confidence": 0.94,
            "diagnostics": {
                "resolution_unit": "TARGET_ITEM_WIDE",
                "semantic_signature": "SET_STOP:ENTRY",
                "grouping_reason": "same_action_same_params",
            },
        },
        {
            "action_type": "SET_STOP",
            "params": {"target_type": "TP_LEVEL", "value": 1},
            "targeting": {"mode": "EXPLICIT_TARGETS", "targets": [1005]},
            "raw_fragment": "стоп на 1 тейк",
            "confidence": 0.93,
            "diagnostics": {
                "resolution_unit": "TARGET_ITEM_WIDE",
                "semantic_signature": "SET_STOP:TP_LEVEL:1",
            },
        },
    ],
    "targeted_reports": [],
    "warnings": [],
    "diagnostics": {"multi_ref_mode": True},
    "raw_context": {
        "raw_text": "стоп в бу / стоп на 1 тейк",
        "reply_to_message_id": None,
        "extracted_links": [],
        "hashtags": [],
        "source_chat_id": "3171748254",
        "source_topic_id": None,
        "acquisition_mode": "live",
    },
}


class TestProposalCases:

    def test_caso_1_deserialises(self) -> None:
        msg = CanonicalMessage.model_validate(CASO_1)
        assert msg.primary_class == "UPDATE"
        assert len(msg.targeted_actions) == 1
        assert msg.targeted_actions[0].action_type == "SET_STOP"
        assert msg.targeted_actions[0].targeting.mode == "TARGET_GROUP"
        assert msg.targeted_actions[0].targeting.targets == [1725, 1726]
        assert msg.targeted_reports == []

    def test_caso_2_deserialises(self) -> None:
        msg = CanonicalMessage.model_validate(CASO_2)
        assert msg.primary_class == "UPDATE"
        assert len(msg.targeted_actions) == 1
        assert msg.targeted_actions[0].action_type == "CLOSE"
        assert len(msg.targeted_reports) == 2
        assert msg.targeted_reports[0].event_type == "FINAL_RESULT"
        assert msg.targeted_reports[0].instrument_hint == "XRP"
        assert msg.targeted_reports[1].instrument_hint == "ENA"

    def test_caso_3_deserialises(self) -> None:
        msg = CanonicalMessage.model_validate(CASO_3)
        assert msg.primary_class == "UPDATE"
        assert len(msg.targeted_actions) == 2
        be_action = msg.targeted_actions[0]
        tp1_action = msg.targeted_actions[1]
        assert be_action.params["target_type"] == "ENTRY"
        assert set(be_action.targeting.targets) == {978, 1002, 1003, 1018}
        assert tp1_action.params["target_type"] == "TP_LEVEL"
        assert tp1_action.targeting.targets == [1005]

    def test_caso_1_round_trip(self) -> None:
        msg = CanonicalMessage.model_validate(CASO_1)
        data = msg.model_dump()
        restored = CanonicalMessage.model_validate(data)
        assert restored.targeted_actions[0].targeting.targets == msg.targeted_actions[0].targeting.targets

    def test_caso_2_round_trip(self) -> None:
        msg = CanonicalMessage.model_validate(CASO_2)
        data = msg.model_dump()
        restored = CanonicalMessage.model_validate(data)
        assert len(restored.targeted_reports) == 2

    def test_caso_3_round_trip(self) -> None:
        msg = CanonicalMessage.model_validate(CASO_3)
        data = msg.model_dump()
        restored = CanonicalMessage.model_validate(data)
        assert len(restored.targeted_actions) == 2


# ---------------------------------------------------------------------------
# 5. Validators reject non-conforming shapes
# ---------------------------------------------------------------------------

class TestValidators:

    # TargetedActionTargeting
    def test_explicit_targets_empty_targets_rejected(self) -> None:
        with pytest.raises(ValidationError, match="non-empty targets"):
            TargetedActionTargeting(mode="EXPLICIT_TARGETS", targets=[])

    def test_target_group_empty_targets_rejected(self) -> None:
        with pytest.raises(ValidationError, match="non-empty targets"):
            TargetedActionTargeting(mode="TARGET_GROUP", targets=[])

    def test_selector_without_selector_rejected(self) -> None:
        with pytest.raises(ValidationError, match="requires selector"):
            TargetedActionTargeting(mode="SELECTOR", selector=None)

    def test_selector_with_selector_accepted(self) -> None:
        t = TargetedActionTargeting(mode="SELECTOR", selector={"side": "SHORT", "status": "OPEN"})
        assert t.selector == {"side": "SHORT", "status": "OPEN"}

    # SetStopParams
    def test_set_stop_price_without_price_rejected(self) -> None:
        with pytest.raises(ValidationError, match="requires price"):
            SetStopParams(target_type="PRICE", price=None)

    def test_set_stop_tp_level_without_value_rejected(self) -> None:
        with pytest.raises(ValidationError, match="requires value"):
            SetStopParams(target_type="TP_LEVEL", value=None)

    def test_set_stop_price_with_price_accepted(self) -> None:
        p = SetStopParams(target_type="PRICE", price=1.245)
        assert p.price == pytest.approx(1.245)

    def test_set_stop_tp_level_with_value_accepted(self) -> None:
        p = SetStopParams(target_type="TP_LEVEL", value=1)
        assert p.value == 1

    def test_set_stop_entry_no_extras_accepted(self) -> None:
        p = SetStopParams(target_type="ENTRY")
        assert p.target_type == "ENTRY"
        assert p.price is None
        assert p.value is None

    # CloseParams
    def test_close_partial_without_fraction_or_price_rejected(self) -> None:
        with pytest.raises(ValidationError, match="requires close_fraction or close_price"):
            CloseParams(close_scope="PARTIAL")

    def test_close_partial_with_fraction_accepted(self) -> None:
        p = CloseParams(close_scope="PARTIAL", close_fraction=0.5)
        assert p.close_fraction == pytest.approx(0.5)

    def test_close_partial_with_price_accepted(self) -> None:
        p = CloseParams(close_scope="PARTIAL", close_price=1.234)
        assert p.close_price == pytest.approx(1.234)

    def test_close_full_no_fraction_accepted(self) -> None:
        p = CloseParams(close_scope="FULL")
        assert p.close_scope == "FULL"
        assert p.close_fraction is None

    # CancelPendingParams
    def test_cancel_scope_invalid_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CancelPendingParams(cancel_scope="INVALID_SCOPE")  # type: ignore[arg-type]

    def test_cancel_scope_valid_accepted(self) -> None:
        for scope in ("TARGETED", "ALL_PENDING_ENTRIES", "ALL_LONG", "ALL_SHORT", "ALL_POSITIONS"):
            p = CancelPendingParams(cancel_scope=scope)  # type: ignore[arg-type]
            assert p.cancel_scope == scope

    # TargetedActionDiagnostics
    def test_diagnostics_partial_fields(self) -> None:
        d = TargetedActionDiagnostics(resolution_unit="MESSAGE_WIDE", semantic_signature="SET_STOP:ENTRY")
        assert d.applied_disambiguation_rules == []
        assert d.applied_context_rules == []
        assert d.grouping_reason is None
