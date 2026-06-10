from __future__ import annotations
import pytest
from src.telegram.pattern_extractors import extract_trader_by_pattern

_INTRADAY_MSG = (
    "Стратегия «RSI(2) Коннора» открыла ЛОНГ по XLM · интрадей (1H)\n"
    "Вход 0.18581, стоп 0.18054, цель 0.19772"
)
_SWING_MSG = (
    "Стратегия «RSI(2) Коннора» открыла ЛОНГ по TON · свинг (4H)\n"
    "Вход 1.66, стоп 1.60, цель 1.81"
)
_UNRELATED_MSG = "BUY BTC at 45000 sl 44000 tp 47000"


def test_rsi_intraday_recognized(rsi_topic_id):
    assert extract_trader_by_pattern(rsi_topic_id, _INTRADAY_MSG) == "trader_rsi_intraday"


def test_rsi_swing_recognized(rsi_topic_id):
    assert extract_trader_by_pattern(rsi_topic_id, _SWING_MSG) == "trader_rsi_swing"


def test_unrelated_message_returns_none(rsi_topic_id):
    assert extract_trader_by_pattern(rsi_topic_id, _UNRELATED_MSG) is None


def test_unknown_topic_returns_none():
    assert extract_trader_by_pattern(9999, _INTRADAY_MSG) is None


def test_empty_text_returns_none(rsi_topic_id):
    assert extract_trader_by_pattern(rsi_topic_id, "") is None


@pytest.fixture
def rsi_topic_id():
    from src.telegram.pattern_extractors import RSI_TOPIC_ID
    return RSI_TOPIC_ID
