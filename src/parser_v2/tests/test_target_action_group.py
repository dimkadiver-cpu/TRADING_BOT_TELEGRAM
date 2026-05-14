from __future__ import annotations
import pytest
from src.parser_v2.contracts.canonical_message import ActionItem, TargetActionGroup, SetStopOperation
from src.parser_v2.contracts.context import TargetHints


def test_action_item_set_stop_valid():
    item = ActionItem(
        action_type="SET_STOP",
        set_stop=SetStopOperation(target_type="ENTRY"),
        source_intent="MOVE_STOP_TO_BE",
    )
    assert item.action_type == "SET_STOP"
    assert item.set_stop.target_type == "ENTRY"


def test_action_item_rejects_wrong_payload():
    from src.parser_v2.contracts.canonical_message import CloseOperation
    with pytest.raises(Exception):
        ActionItem(
            action_type="SET_STOP",
            close=CloseOperation(close_scope="FULL"),
            source_intent="MOVE_STOP_TO_BE",
        )


def test_action_item_requires_exactly_one_payload():
    from src.parser_v2.contracts.canonical_message import CancelPendingOperation
    with pytest.raises(Exception):
        ActionItem(
            action_type="CANCEL_PENDING",
            source_intent="CANCEL_PENDING",
        )


def test_target_action_group_valid():
    group = TargetActionGroup(
        targeting=TargetHints(reply_to_message_id=100),
        actions=[
            ActionItem(
                action_type="SET_STOP",
                set_stop=SetStopOperation(target_type="ENTRY"),
                source_intent="MOVE_STOP_TO_BE",
            )
        ],
    )
    assert len(group.actions) == 1
    assert group.secondary_targeting is None


def test_target_action_group_with_secondary_targeting():
    group = TargetActionGroup(
        targeting=TargetHints(telegram_message_ids=[2712], scope_hint="SINGLE_SIGNAL"),
        secondary_targeting=TargetHints(reply_to_message_id=100),
        actions=[
            ActionItem(
                action_type="SET_STOP",
                set_stop=SetStopOperation(target_type="ENTRY"),
                source_intent="MOVE_STOP_TO_BE",
            )
        ],
    )
    assert group.secondary_targeting.reply_to_message_id == 100
    assert group.targeting.telegram_message_ids == [2712]


def test_target_action_group_rejects_empty_actions():
    with pytest.raises(Exception):
        TargetActionGroup(
            targeting=TargetHints(reply_to_message_id=100),
            actions=[],
        )
