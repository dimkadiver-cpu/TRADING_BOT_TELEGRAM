from __future__ import annotations

from src.parser_v2.contracts.markers import NormalizedText
from src.parser_v2.profiles.trader_a.signal_extractor import SignalExtractor


def _extract(text: str, market_hint: bool = False):
    extractor = SignalExtractor()
    normalized = NormalizedText(raw_text=text, normalized_text=text.lower(), lines=text.splitlines())
    return extractor.extract(normalized, market_hint=market_hint)


def test_extracts_ab_entries_from_unicode_bullets() -> None:
    text = (
        "[trader#A]\n\n"
        "#BTCUSDT Шорт вход с текущих\n\n"
        "— Вход (A): 87600\n"
        "— Вход (B): -\n\n"
        "— SL: 93558\n"
        "— TP: 76201\n"
    )

    signal = _extract(text)

    assert signal is not None
    assert signal.completeness == "COMPLETE"
    assert len(signal.entries) == 1
    assert signal.entries[0].price.value == 87600.0


def test_extracts_stop_loss_without_crossing_lines() -> None:
    text = (
        "[trader#A]\n\n"
        "COAIUSDT Шорт (вход с текущих)\n\n"
        "Вход (2-фазный):\n"
        "— Вход с текущих\n"
        "— Усреднение: 1.1843\n\n"
        "Стоп:\n"
        "— SL: 1.2769\n\n"
        "4. Тейки:\n"
        "— TP1: 0.8627\n"
    )

    signal = _extract(text)

    assert signal is not None
    assert signal.stop_loss is not None
    assert signal.stop_loss.price.value == 1.2769


def test_extracts_spot_entry_and_infers_long_side() -> None:
    text = (
        "[trader#A]\n\n"
        "MLNUSDT\n\n"
        "Вход (spot): 5.83000000\n"
        "Стоп: 5.22821429\n\n"
        "Тейки:\n"
        "— TP1: 7.28750000\n\n"
        "Исключительно спот\n"
    )

    signal = _extract(text)

    assert signal is not None
    assert signal.side == "LONG"
    assert signal.completeness == "COMPLETE"
    assert signal.entries[0].price.value == 5.83


def test_entry_paren_qualifier_market_produces_market_entry() -> None:
    """Real trader_b format: 'Вход: price (по текущим)' must yield MARKET, not LIMIT."""
    text = "ETHUSDT.P — Лонг\nВход: 2035 (по текущим)\nSL: 1900\nTP1: 2200"
    signal = _extract(text)
    assert signal is not None
    assert len(signal.entries) == 1
    assert signal.entries[0].entry_type == "MARKET"
    assert signal.entries[0].price is not None
    assert signal.entries[0].price.value == 2035.0


def test_market_hint_overrides_limit_when_regex_misses() -> None:
    """market_hint=True from evidence should set MARKET even when only generic _ENTRY_RE matches."""
    text = "BTCUSDT Лонг\nВход: 90000\nSL: 89000\nTP1: 93000"
    signal_no_hint = _extract(text, market_hint=False)
    signal_with_hint = _extract(text, market_hint=True)
    assert signal_no_hint is not None
    assert signal_no_hint.entries[0].entry_type == "LIMIT"
    assert signal_with_hint is not None
    assert signal_with_hint.entries[0].entry_type == "MARKET"
    assert signal_with_hint.entries[0].price.value == 90000.0


def test_entry_paren_qualifier_rynok_produces_market_entry() -> None:
    """Variant with 'рынок' keyword in parens."""
    text = "BTCUSDT Лонг\nВход: 90000 (по рынку)\nSL: 89000\nTP1: 93000"
    signal = _extract(text)
    assert signal is not None
    assert signal.entries[0].entry_type == "MARKET"


def test_extracts_bare_take_profit_lines_under_tps_header() -> None:
    text = (
        "[trader#A]\n\n"
        "ORDIUSDT.P — ЛОНГ (вход с текущих)\n"
        "• Вход: 5.0113\n"
        "• Усреднение: 4.7291\n"
        "• Стоп: 4.4913\n"
        "• TPs:\n"
        "— 5.8613 (+17.0%)\n"
        "— 6.3269 (+26.3%)\n"
        "— 7.2469 (+44.6%)\n"
    )

    signal = _extract(text)

    assert signal is not None
    assert signal.completeness == "COMPLETE"
    assert [tp.price.value for tp in signal.take_profits] == [5.8613, 6.3269, 7.2469]
