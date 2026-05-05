from __future__ import annotations

from src.parser_v2.core.text_normalizer import TextNormalizer
from src.parser_v2.profiles.trader_a.signal_extractor import SignalExtractor


def _extract(text: str):
    normalized = TextNormalizer().normalize(text)
    return SignalExtractor().extract(normalized)


def test_complete_signal_extracts_required_fields() -> None:
    signal = _extract("BTCUSDT long entry 62000 sl 61000 tp1 63000 tp2 64000")

    assert signal is not None
    assert signal.symbol == "BTCUSDT"
    assert signal.side == "LONG"
    assert signal.entry_structure == "ONE_SHOT"
    assert signal.entries[0].entry_type == "LIMIT"
    assert signal.entries[0].role == "PRIMARY"
    assert signal.entries[0].price is not None
    assert signal.entries[0].price.value == 62000.0
    assert signal.stop_loss is not None
    assert signal.stop_loss.price is not None
    assert signal.stop_loss.price.value == 61000.0
    assert [tp.price.value for tp in signal.take_profits] == [63000.0, 64000.0]
    assert signal.missing_fields == []
    assert signal.completeness == "COMPLETE"


def test_partial_signal_without_take_profits_reports_missing_field() -> None:
    signal = _extract("ETHUSDT short entry 3450 sl 3520")

    assert signal is not None
    assert signal.completeness == "INCOMPLETE"
    assert signal.missing_fields == ["take_profits"]


def test_signal_with_averaging_entry_marks_roles_and_two_step_structure() -> None:
    signal = _extract(
        "BNBUSDT LONG\n"
        "entry 591.59\n"
        "averaging 585.10\n"
        "sl 580.00\n"
        "tp1 602.00"
    )

    assert signal is not None
    assert signal.entry_structure == "TWO_STEP"
    assert [(entry.sequence, entry.role, entry.is_optional) for entry in signal.entries] == [
        (1, "PRIMARY", False),
        (2, "AVERAGING", True),
    ]
    assert [entry.price.value for entry in signal.entries if entry.price is not None] == [591.59, 585.1]


def test_russian_signal_markers_and_comma_prices_are_supported() -> None:
    signal = _extract(
        "ARBUSDT \u0428\u043e\u0440\u0442\n"
        "\u0412\u0445\u043e\u0434 \u043b\u0438\u043c\u0438\u0442\u043a\u043e\u0439: 0,10380\n"
        "\u0423\u0441\u0440\u0435\u0434\u043d\u0435\u043d\u0438\u0435: 0,10110\n"
        "\u0421\u0442\u043e\u043f: 0,10612\n"
        "TP1: 0,1016\n"
        "TP2: 0,1005"
    )

    assert signal is not None
    assert signal.side == "SHORT"
    assert signal.entry_structure == "TWO_STEP"
    assert [entry.role for entry in signal.entries] == ["PRIMARY", "AVERAGING"]
    assert [entry.price.value for entry in signal.entries if entry.price is not None] == [0.1038, 0.1011]
    assert signal.stop_loss is not None
    assert signal.stop_loss.price is not None
    assert signal.stop_loss.price.value == 0.10612
    assert [tp.price.value for tp in signal.take_profits] == [0.1016, 0.1005]
    assert signal.completeness == "COMPLETE"


def test_risk_hint_is_optional_and_extracted_when_present() -> None:
    signal = _extract("SOLUSDT long entry 120 sl 114 tp1 130 risk 1.5%")

    assert signal is not None
    assert signal.risk_hint is not None
    assert signal.risk_hint.raw == "risk 1.5%"
    assert signal.risk_hint.value == 1.5


def test_non_signal_text_returns_none() -> None:
    assert _extract("move stop to be now") is None
