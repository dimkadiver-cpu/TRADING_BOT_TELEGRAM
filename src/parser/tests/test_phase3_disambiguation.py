from __future__ import annotations

from src.parser.canonical_v1.models import Price, RawContext, TargetRef, TargetScope, Targeting
from src.parser.parsed_message import (
    MoveStopEntities,
    MoveStopToBEEntities,
    ParsedMessage,
    TpHitEntities,
    IntentResult,
)
from src.parser.shared.disambiguation import DisambiguationResult, apply_disambiguation_rules


def _intent(
    *,
    intent_type: str,
    category: str,
    entities: object,
    strength: str = "weak",
    status: str = "CONFIRMED",
) -> IntentResult:
    return IntentResult(
        type=intent_type,
        category=category,
        entities=entities,
        confidence=0.8,
        detection_strength=strength,
        status=status,
    )


def _parsed_message(*, composite: bool = False, has_targeting: bool = False) -> ParsedMessage:
    targeting = None
    if has_targeting:
        targeting = Targeting(
            refs=[TargetRef(ref_type="REPLY", value=10)],
            scope=TargetScope(kind="SINGLE_SIGNAL"),
            strategy="REPLY_OR_LINK",
            targeted=True,
        )
    return ParsedMessage(
        parser_profile="trader_test",
        primary_class="UPDATE",
        parse_status="PARSED",
        confidence=0.9,
        composite=composite,
        intents=[
            _intent(
                intent_type="MOVE_STOP_TO_BE",
                category="UPDATE",
                entities=MoveStopToBEEntities(),
                strength="strong",
            ),
            _intent(
                intent_type="MOVE_STOP",
                category="UPDATE",
                entities=MoveStopEntities(new_stop_price=Price(raw="43000", value=43000.0)),
                strength="weak",
            ),
            _intent(
                intent_type="TP_HIT",
                category="REPORT",
                entities=TpHitEntities(level=1),
                strength="strong",
            ),
        ],
        primary_intent="MOVE_STOP",
        targeting=targeting,
        raw_context=RawContext(raw_text="breakeven after tp1"),
    )


def test_phase3_disambiguation_applies_nested_prefer_rule() -> None:
    parsed = _parsed_message(composite=True, has_targeting=True)

    result = apply_disambiguation_rules(
        parsed_message=parsed,
        rules=[
            {
                "name": "prefer_be_when_tp1",
                "action": "prefer",
                "priority": 10,
                "conditions": {
                    "intents": {
                        "strong": ["MOVE_STOP_TO_BE", "TP_HIT"],
                        "weak": ["MOVE_STOP"],
                    },
                    "text": {"any": ["breakeven"], "none": ["cancel"]},
                    "message": {"composite": True, "has_targeting": True},
                    "entities": {
                        "present": ["TP_HIT.level"],
                        "absent": ["MOVE_STOP.stop_to_tp_level"],
                    },
                },
                "prefer": "MOVE_STOP_TO_BE",
                "over": ["MOVE_STOP"],
            }
        ],
    )

    assert isinstance(result, DisambiguationResult)
    assert [intent.type for intent in result.parsed_message.intents] == [
        "MOVE_STOP_TO_BE",
        "TP_HIT",
    ]
    assert result.applied_rules == ["prefer_be_when_tp1"]
    assert result.parsed_message.primary_intent != "MOVE_STOP"


def test_phase3_disambiguation_normalizes_flat_rule_shape() -> None:
    parsed = _parsed_message()

    result = apply_disambiguation_rules(
        parsed_message=parsed,
        rules=[
            {
                "name": "prefer_be_legacy",
                "action": "prefer",
                "when_all_detected": ["MOVE_STOP_TO_BE", "MOVE_STOP"],
                "if_contains_any": ["breakeven"],
                "prefer": "MOVE_STOP_TO_BE",
            }
        ],
    )

    assert [intent.type for intent in result.parsed_message.intents] == [
        "MOVE_STOP_TO_BE",
        "TP_HIT",
    ]
    assert result.applied_rules == ["prefer_be_legacy"]
