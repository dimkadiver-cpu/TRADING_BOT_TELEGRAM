from __future__ import annotations

import pytest

from src.parser_v2.contracts.markers import NormalizedText
from src.parser_v2.profiles.Legacy.trader_a_legacy.signal_extractor import SignalExtractor


@pytest.mark.parametrize(
    ("separator", "expected_raw"),
    [
        ("-", "Вход не более 1-2%"),
        ("–", "Вход не более 1–2%"),
    ],
)
def test_risk_hint_accepts_dash_range_separators(separator: str, expected_raw: str) -> None:
    extractor = SignalExtractor()
    text = (
        "[trader#A]\n\n"
        "#CAKEUSDT 🐻 Шорт (вход с текущих)\n\n"
        "Вход (2-фазный):\n"
        "— Вход с текущих: 2.363\n"
        "— Усреднение: 2.493\n\n"
        "Стоп:\n"
        "— SL: 2.573 🛡\n\n"
        "Тейки (среднесрок, 3 цели):\n"
        "— TP1: 2.175 🎯\n"
        "— TP2: 1.983 🎯\n"
        "— TP3: 1.783 🎯\n\n"
        f"Вход не более 1{separator}2% от депозита\n"
    )
    normalized = NormalizedText(raw_text=text, normalized_text=text.lower(), lines=text.splitlines())

    signal = extractor.extract(normalized)

    assert signal is not None
    assert signal.risk_hint is not None
    assert signal.risk_hint.raw == expected_raw
    assert signal.risk_hint.min_value == 1.0
    assert signal.risk_hint.max_value == 2.0
