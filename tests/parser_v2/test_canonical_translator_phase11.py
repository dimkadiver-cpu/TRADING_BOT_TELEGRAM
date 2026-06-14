from __future__ import annotations

import pytest

from pydantic import ValidationError

from src.parser_v2.contracts.canonical_message import SetStopOperation
from src.parser_v2.contracts.context import RawContext
from src.parser_v2.contracts.entities import MoveStopEntities, Price, RiskReductionTarget
from src.parser_v2.contracts.parsed_message import ParsedIntent, ParsedMessage
from src.parser_v2.translation.canonical_translator import CanonicalTranslator


def _raw_context(raw_text: str = "raw") -> RawContext:
    return RawContext(raw_text=raw_text, normalized_text=raw_text.lower())


def _intent(entities: MoveStopEntities) -> ParsedIntent:
    return ParsedIntent(
        type="MOVE_STOP",
        category="UPDATE",
        confidence=0.9,
        entities=entities,
        raw_fragment="move stop",
    )


def _parsed_update(intent: ParsedIntent) -> ParsedMessage:
    return ParsedMessage(
        parser_profile="trader_a",
        primary_class="UPDATE",
        parse_status="PARSED",
        confidence=0.9,
        intents=[intent],
        primary_intent=intent.type,
        raw_context=_raw_context(),
    )


def _price(raw: str) -> Price:
    return Price(raw=raw, value=float(raw))


def test_move_stop_risk_percent_translates_to_risk_target() -> None:
    parsed = _parsed_update(
        _intent(
            MoveStopEntities(
                risk_reduction_target=RiskReductionTarget(
                    unit="PERCENT_OF_INITIAL_RISK",
                    value=0.4,
                )
            )
        )
    )

    canonical = CanonicalTranslator().translate(parsed)

    action = canonical.target_action_groups[0].actions[0]
    assert action.set_stop is not None
    assert action.set_stop.target_type == "RISK_TARGET"
    assert action.set_stop.risk_reduction_target is not None
    assert action.set_stop.risk_reduction_target.unit == "PERCENT_OF_INITIAL_RISK"
    assert action.set_stop.risk_reduction_target.value == 0.4


def test_move_stop_risk_r_multiple_translates_to_risk_target() -> None:
    parsed = _parsed_update(
        _intent(
            MoveStopEntities(
                risk_reduction_target=RiskReductionTarget(
                    unit="R_MULTIPLE",
                    value=0.4,
                )
            )
        )
    )

    canonical = CanonicalTranslator().translate(parsed)

    action = canonical.target_action_groups[0].actions[0]
    assert action.set_stop is not None
    assert action.set_stop.target_type == "RISK_TARGET"
    assert action.set_stop.risk_reduction_target is not None
    assert action.set_stop.risk_reduction_target.unit == "R_MULTIPLE"
    assert action.set_stop.risk_reduction_target.value == 0.4


def test_move_stop_entities_rejects_price_and_risk_target_together() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        MoveStopEntities(
            new_stop_price=_price("123"),
            risk_reduction_target=RiskReductionTarget(
                unit="R_MULTIPLE",
                value=0.4,
            ),
        )


def test_move_stop_entities_rejects_tp_level_and_risk_target_together() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        MoveStopEntities(
            stop_to_tp_level=1,
            risk_reduction_target=RiskReductionTarget(
                unit="PERCENT_OF_INITIAL_RISK",
                value=0.4,
            ),
        )


def test_set_stop_risk_target_requires_payload() -> None:
    with pytest.raises(ValidationError, match="requires risk_reduction_target"):
        SetStopOperation(target_type="RISK_TARGET")


def test_set_stop_risk_target_rejects_price_and_tp_level() -> None:
    target = RiskReductionTarget(unit="R_MULTIPLE", value=0.4)

    with pytest.raises(ValidationError, match="forbids price/tp_level"):
        SetStopOperation(
            target_type="RISK_TARGET",
            risk_reduction_target=target,
            price=_price("123"),
        )

    with pytest.raises(ValidationError, match="forbids price/tp_level"):
        SetStopOperation(
            target_type="RISK_TARGET",
            risk_reduction_target=target,
            tp_level=1,
        )


def test_move_stop_without_target_keeps_entry_fallback() -> None:
    parsed = _parsed_update(_intent(MoveStopEntities()))

    canonical = CanonicalTranslator().translate(parsed)

    action = canonical.target_action_groups[0].actions[0]
    assert action.set_stop is not None
    assert action.set_stop.target_type == "ENTRY"
    assert action.set_stop.price is None
    assert action.set_stop.tp_level is None
    assert action.set_stop.risk_reduction_target is None
    assert "move_stop_no_price_defaulted_to_be" in canonical.warnings
