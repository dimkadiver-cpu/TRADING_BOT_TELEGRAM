from __future__ import annotations
import pytest
from pydantic import ValidationError
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
    with pytest.raises(ValidationError):
        ActionItem(
            action_type="SET_STOP",
            close=CloseOperation(close_scope="FULL"),
            source_intent="MOVE_STOP_TO_BE",
        )


def test_action_item_rejects_missing_payload():
    from src.parser_v2.contracts.canonical_message import CancelPendingOperation
    with pytest.raises(ValidationError):
        ActionItem(
            action_type="CANCEL_PENDING",
            source_intent="CANCEL_PENDING",
        )


def test_action_item_rejects_dual_payload():
    from src.parser_v2.contracts.canonical_message import CancelPendingOperation
    with pytest.raises(ValidationError):
        ActionItem(
            action_type="CANCEL_PENDING",
            cancel_pending=CancelPendingOperation(cancel_scope_hint="UNKNOWN"),
            set_stop=SetStopOperation(target_type="ENTRY"),
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
    with pytest.raises(ValidationError):
        TargetActionGroup(
            targeting=TargetHints(reply_to_message_id=100),
            actions=[],
        )


def test_canonical_message_update_uses_target_action_groups():
    from src.parser_v2.contracts.canonical_message import CanonicalMessage
    from src.parser_v2.contracts.context import RawContext
    msg = CanonicalMessage(
        parser_profile="test",
        primary_class="UPDATE",
        parse_status="PARSED",
        confidence=0.9,
        target_action_groups=[
            TargetActionGroup(
                targeting=TargetHints(reply_to_message_id=100),
                actions=[
                    ActionItem(
                        action_type="SET_STOP",
                        set_stop=SetStopOperation(target_type="ENTRY"),
                        source_intent="MOVE_STOP_TO_BE",
                    )
                ],
            )
        ],
        raw_context=RawContext(raw_text="стоп в бу"),
    )
    assert len(msg.target_action_groups) == 1
    assert msg.target_action_groups[0].targeting.reply_to_message_id == 100


def test_canonical_message_update_requires_target_action_groups_when_parsed():
    from src.parser_v2.contracts.canonical_message import CanonicalMessage
    from src.parser_v2.contracts.context import RawContext
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        CanonicalMessage(
            parser_profile="test",
            primary_class="UPDATE",
            parse_status="PARSED",
            confidence=0.9,
            target_action_groups=[],
            raw_context=RawContext(raw_text="test"),
        )


def test_canonical_message_signal_forbids_target_action_groups():
    from src.parser_v2.contracts.canonical_message import CanonicalMessage, SignalPayload
    from src.parser_v2.contracts.context import RawContext
    from src.parser_v2.contracts.entities import EntryLeg, StopLoss, TakeProfit, Price
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        CanonicalMessage(
            parser_profile="test",
            primary_class="SIGNAL",
            parse_status="PARSED",
            confidence=0.9,
            signal=SignalPayload(
                symbol="BTCUSDT", side="LONG",
                entry_structure="ONE_SHOT",
                entries=[EntryLeg(sequence=1, entry_type="LIMIT", price=Price(raw="100", value=100.0))],
                stop_loss=StopLoss(price=Price(raw="90", value=90.0)),
                take_profits=[TakeProfit(sequence=1, price=Price(raw="110", value=110.0))],
            ),
            target_action_groups=[
                TargetActionGroup(
                    targeting=TargetHints(reply_to_message_id=1),
                    actions=[ActionItem(
                        action_type="SET_STOP",
                        set_stop=SetStopOperation(target_type="ENTRY"),
                        source_intent="MOVE_STOP_TO_BE",
                    )],
                )
            ],
            raw_context=RawContext(raw_text="test"),
        )
