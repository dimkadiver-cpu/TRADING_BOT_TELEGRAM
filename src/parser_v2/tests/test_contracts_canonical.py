from __future__ import annotations
import pytest
from src.parser_v2.contracts.canonical_message import UpdateOperation, TargetedAction
from src.parser_v2.contracts.context import TargetHints


def test_update_operation_has_source_intent_id():
    op = UpdateOperation(
        op_type="SET_STOP",
        set_stop={"target_type": "ENTRY"},
        source_intent="MOVE_STOP_TO_BE",
    )
    assert op.source_intent_id is None


def test_update_operation_stores_source_intent_id():
    from src.parser_v2.contracts.canonical_message import SetStopOperation
    op = UpdateOperation(
        op_type="SET_STOP",
        set_stop=SetStopOperation(target_type="ENTRY"),
        source_intent="MOVE_STOP_TO_BE",
        source_intent_id="MOVE_STOP_TO_BE#1",
    )
    assert op.source_intent_id == "MOVE_STOP_TO_BE#1"


def test_targeted_action_has_source_intent_id():
    action = TargetedAction(
        action_type="SET_STOP",
        target_hints=TargetHints(reply_to_message_id=100),
        source_intent="MOVE_STOP_TO_BE",
    )
    assert action.source_intent_id is None


def test_targeted_action_stores_source_intent_id():
    action = TargetedAction(
        action_type="SET_STOP",
        target_hints=TargetHints(reply_to_message_id=100),
        source_intent="MOVE_STOP_TO_BE",
        source_intent_id="MOVE_STOP_TO_BE#0",
    )
    assert action.source_intent_id == "MOVE_STOP_TO_BE#0"


def test_canonical_message_validator_uses_new_warning():
    from src.parser_v2.contracts.canonical_message import CanonicalMessage
    from src.parser_v2.contracts.context import RawContext
    msg = CanonicalMessage(
        parser_profile="test",
        primary_class="UPDATE",
        parse_status="PARTIAL",
        confidence=0.5,
        warnings=["ambiguous_target_intent_binding"],
        raw_context=RawContext(raw_text="test"),
    )
    assert "ambiguous_target_intent_binding" in msg.warnings


def test_canonical_message_validator_rejects_old_warning():
    from src.parser_v2.contracts.canonical_message import CanonicalMessage
    from src.parser_v2.contracts.context import RawContext
    with pytest.raises(Exception):
        CanonicalMessage(
            parser_profile="test",
            primary_class="UPDATE",
            parse_status="PARTIAL",
            confidence=0.5,
            warnings=["multi_ref_mixed_intents_not_supported"],
            raw_context=RawContext(raw_text="test"),
        )
