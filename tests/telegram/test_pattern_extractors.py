from __future__ import annotations

from pathlib import Path

import pytest

from src.telegram.pattern_extractors import TextPatternCatalog

_INTRADAY_MSG = (
    "Стратегия «RSI(2) Коннора» открыла ЛОНГ по XLM · интрадей (1H)\n"
    "Вход 0.18581, стоп 0.18054, цель 0.19772"
)
_SWING_MSG = (
    "Стратегия «RSI(2) Коннора» открыла ЛОНГ по TON · свинг (4H)\n"
    "Вход 1.66, стоп 1.60, цель 1.81"
)
_UNRELATED_MSG = "BUY BTC at 45000 sl 44000 tp 47000"
_AMBIGUOUS_MSG = "RSI(2) Коннора интрадей и RSI(2) Коннора свинг"


@pytest.fixture
def catalog_path(tmp_path: Path) -> Path:
    path = tmp_path / "text_patterns.yaml"
    path.write_text(
        """
groups:
  multi_strategy_ru:
    patterns:
      - trader_id: rsi_intraday
        all_of:
          - "RSI(2) Коннора"
          - "интрадей"
      - trader_id: rsi_swing
        all_of:
          - "RSI(2) Коннора"
          - "свинг"
""",
        encoding="utf-8",
    )
    return path


def test_rsi_intraday_recognized(catalog_path):
    catalog = TextPatternCatalog(catalog_path)
    match = catalog.resolve("multi_strategy_ru", _INTRADAY_MSG)
    assert match.trader_id == "rsi_intraday"
    assert match.is_ambiguous is False


def test_rsi_swing_recognized(catalog_path):
    catalog = TextPatternCatalog(catalog_path)
    match = catalog.resolve("multi_strategy_ru", _SWING_MSG)
    assert match.trader_id == "rsi_swing"
    assert match.is_ambiguous is False


def test_unrelated_message_returns_none(catalog_path):
    catalog = TextPatternCatalog(catalog_path)
    match = catalog.resolve("multi_strategy_ru", _UNRELATED_MSG)
    assert match.trader_id is None
    assert match.is_ambiguous is False


def test_unknown_group_returns_none(catalog_path):
    catalog = TextPatternCatalog(catalog_path)
    match = catalog.resolve("missing_group", _INTRADAY_MSG)
    assert match.trader_id is None
    assert match.is_ambiguous is False


def test_empty_text_returns_none(catalog_path):
    catalog = TextPatternCatalog(catalog_path)
    match = catalog.resolve("multi_strategy_ru", "")
    assert match.trader_id is None
    assert match.is_ambiguous is False


def test_ambiguous_patterns_report_ambiguity(catalog_path):
    catalog = TextPatternCatalog(catalog_path)
    match = catalog.resolve("multi_strategy_ru", _AMBIGUOUS_MSG)
    assert match.trader_id is None
    assert match.is_ambiguous is True
