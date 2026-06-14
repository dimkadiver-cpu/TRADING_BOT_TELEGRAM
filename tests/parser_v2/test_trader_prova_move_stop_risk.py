from __future__ import annotations

import pytest

from src.parser_v2.contracts.context import ParserContext
from src.parser_v2.core.runtime import parse
from src.parser_v2.profiles.trader_prova.profile import TraderProvaProfile


def _parse(text: str):
    return parse(text, ParserContext(), TraderProvaProfile())


def test_trader_prova_move_stop_risk_percent_translates_to_risk_target() -> None:
    canonical = _parse("сокращаем риск до 0.4%")

    assert canonical.primary_class == "UPDATE"
    assert canonical.primary_intent == "MOVE_STOP"
    action = canonical.target_action_groups[0].actions[0]
    assert action.action_type == "SET_STOP"
    assert action.set_stop is not None
    assert action.set_stop.target_type == "RISK_TARGET"
    assert action.set_stop.risk_reduction_target is not None
    assert action.set_stop.risk_reduction_target.unit == "PERCENT_OF_INITIAL_RISK"
    assert action.set_stop.risk_reduction_target.value == pytest.approx(0.4)


def test_trader_prova_move_stop_risk_rr_translates_to_risk_target() -> None:
    canonical = _parse("снижаем риск до 0,4RR")

    assert canonical.primary_class == "UPDATE"
    assert canonical.primary_intent == "MOVE_STOP"
    action = canonical.target_action_groups[0].actions[0]
    assert action.action_type == "SET_STOP"
    assert action.set_stop is not None
    assert action.set_stop.target_type == "RISK_TARGET"
    assert action.set_stop.risk_reduction_target is not None
    assert action.set_stop.risk_reduction_target.unit == "R_MULTIPLE"
    assert action.set_stop.risk_reduction_target.value == pytest.approx(0.4)


def test_trader_prova_move_stop_price_still_uses_price_target() -> None:
    canonical = _parse("стоп на 2140")

    action = canonical.target_action_groups[0].actions[0]
    assert action.action_type == "SET_STOP"
    assert action.set_stop is not None
    assert action.set_stop.target_type == "PRICE"
    assert action.set_stop.price is not None
    assert action.set_stop.price.value == pytest.approx(2140.0)


def test_trader_prova_move_stop_tp_level_still_uses_tp_target() -> None:
    canonical = _parse("стоп на 1 тейк")

    action = canonical.target_action_groups[0].actions[0]
    assert action.action_type == "SET_STOP"
    assert action.set_stop is not None
    assert action.set_stop.target_type == "TP_LEVEL"
    assert action.set_stop.tp_level == 1
