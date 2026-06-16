from __future__ import annotations

from src.parser_v2.contracts.markers import NormalizedText
from src.parser_v2.profiles.Legacy.trader_a_legacy.signal_extractor import SignalExtractor


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


def test_market_hint_no_price_produces_market_leg_price_none() -> None:
    """Case 533: 'Вход: по текущим' — no numeric price at all.
    market_hint=True from evidence must produce EntryLeg(MARKET, price=None)."""
    text = "$ETHUSDT - Лонг\nВход: по текущим\nТейк профит: 2160\nСтоп лосс: 1972"
    signal = _extract(text, market_hint=True)
    assert signal is not None
    assert len(signal.entries) == 1
    leg = signal.entries[0]
    assert leg.entry_type == "MARKET"
    assert leg.price is None


def test_no_market_hint_no_price_produces_no_entry() -> None:
    """Without market_hint, 'Вход: по текущим' yields no entry (no number to parse)."""
    text = "$ETHUSDT - Лонг\nВход: по текущим\nТейк профит: 2160\nСтоп лосс: 1972"
    signal = _extract(text, market_hint=False)
    # SL and TP still parsed, so signal is not None but entries is empty
    assert signal is not None
    assert signal.entries == []


def test_close_message_with_market_hint_does_not_produce_entry() -> None:
    """Cases 77/73/66/62: 'Закрываем по текущим' has no entry keyword.
    market_hint=True from 'по текущим' evidence must NOT emit a false EntryLeg."""
    texts = [
        "Закрываем по текущим в +2%, пока выглядит не уверенно",
        "Закрываем по текущим на точке входа в БУ, топчется на месте",
        "Закрываем по текущим в небольшой минус (-0.3%)",
        "Закрываем по текущим в +2%",
    ]
    for text in texts:
        signal = _extract(text, market_hint=True)
        if signal is not None:
            assert signal.entries == [], f"False entry leg for: {text!r}"


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


def test_range_entry_format_produces_range_structure() -> None:
    """entry: N-M deve produrre entry_structure=RANGE con 2 leg LIMIT."""
    text = (
        "BTCUSDT Лонг\n"
        "Вход: 64000-66000\n"
        "SL: 62000\n"
        "TP1: 70000\n"
    )
    signal = _extract(text)
    assert signal is not None
    assert signal.entry_structure == "RANGE"
    assert len(signal.entries) == 2
    assert signal.entries[0].entry_type == "LIMIT"
    assert signal.entries[1].entry_type == "LIMIT"
    assert signal.entries[0].price.value == 64000.0
    assert signal.entries[1].price.value == 66000.0
    assert signal.entries[0].sequence == 1
    assert signal.entries[1].sequence == 2


def test_two_discrete_entries_produce_two_step_not_range() -> None:
    """Due entry separate devono produrre TWO_STEP, non RANGE."""
    text = (
        "BTCUSDT Лонг\n"
        "Вход A: 64000\n"
        "Вход B: 66000\n"
        "SL: 62000\n"
        "TP1: 70000\n"
    )
    signal = _extract(text)
    assert signal is not None
    assert signal.entry_structure == "TWO_STEP"
    assert len(signal.entries) == 2


def test_range_entry_english_format() -> None:
    """entry: N-M in formato inglese deve produrre RANGE."""
    text = "ETHUSDT Long\nentry: 2000-2100\nSL: 1900\nTP1: 2300\n"
    signal = _extract(text)
    assert signal is not None
    assert signal.entry_structure == "RANGE"
    assert signal.entries[0].price.value == 2000.0
    assert signal.entries[1].price.value == 2100.0


def test_trader_d_extracts_cyrillic_tp_lines() -> None:
    from src.parser_v2.profiles.trader_d.signal_extractor import SignalExtractor as TraderDExtractor

    extractor = TraderDExtractor()
    text = (
        "[trader #d] Signal ID: #d8\n\n"
        "#WLFIUSDT \u0428\u043e\u0440\u0442\n\n"
        "\u0412\u0445\u043e\u0434: 0.06203 \u0440\u044b\u043d\u043e\u043a\n\n"
        "SL: 0.06331\n\n"
        "\u0422\u041f1: 0.0592\n"
        "\u0422\u041f2: 0.0492\n"
        "\u0422\u041f3: 0.0392\n\n"
        "\u0420\u0438\u0441\u043a \u043d\u0430 \u0441\u0434\u0435\u043b\u043a\u0443 1%\n"
    )
    normalized = NormalizedText(raw_text=text, normalized_text=text.lower(), lines=text.splitlines())

    signal = extractor.extract(normalized, market_hint=True)

    assert signal is not None
    assert signal.side == "SHORT"
    assert [tp.price.value for tp in signal.take_profits] == [0.0592, 0.0492, 0.0392]
    assert signal.risk_hint is not None
    assert signal.risk_hint.value == 1.0


def test_trader_d_profile_extracts_cyrillic_tp_lines() -> None:
    from src.parser_v2.contracts.context import ParserContext
    from src.parser_v2.core.runtime import UniversalParserRuntime
    from src.parser_v2.profiles.trader_d.profile import TraderDProfile

    text = (
        "[trader #d] Signal ID: #d8\n\n"
        "#WLFIUSDT \u0428\u043e\u0440\u0442\n\n"
        "\u0412\u0445\u043e\u0434: 0.06203 \u0440\u044b\u043d\u043e\u043a\n\n"
        "SL: 0.06331\n\n"
        "\u0422\u041f1: 0.0592\n"
        "\u0422\u041f2: 0.0492\n"
        "\u0422\u041f3: 0.0392\n\n"
        "\u0420\u0438\u0441\u043a \u043d\u0430 \u0441\u0434\u0435\u043b\u043a\u0443 1%\n"
    )

    result = UniversalParserRuntime().parse(text, ParserContext(), TraderDProfile())

    assert [tp.price.value for tp in result.signal.take_profits] == [0.0592, 0.0492, 0.0392]


def test_trader_b_range_entry_produces_range_structure() -> None:
    from src.parser_v2.profiles.Legacy.trader_b_legacy.signal_extractor import SignalExtractor as TraderBExtractor

    extractor = TraderBExtractor()
    text = "ETHUSDT.P Лонг\nВход: 2000-2100\nSL: 1900\nTP1: 2300\n"
    normalized = NormalizedText(raw_text=text, normalized_text=text.lower(), lines=text.splitlines())
    signal = extractor.extract(normalized)
    assert signal is not None
    assert signal.entry_structure == "RANGE"
    assert len(signal.entries) == 2
    assert signal.entries[0].price.value == 2000.0
    assert signal.entries[1].price.value == 2100.0


def test_prova_extracts_cyrillic_тп1_inline() -> None:
    """тп1: price (trader_d format) must be recognised by trader_prova extractor."""
    from src.parser_v2.profiles.trader_prova.signal_extractor import SignalExtractor as ProvaExtractor

    text = "$WLFIUSDT Шорт\nВход: 0.062 рынок\nSL: 0.0633\nТП1: 0.0592\nТП2: 0.0492\nТП3: 0.0392\n"
    normalized = NormalizedText(raw_text=text, normalized_text=text.lower(), lines=text.splitlines())
    signal = ProvaExtractor().extract(normalized, market_hint=True)

    assert signal is not None
    assert [tp.price.value for tp in signal.take_profits] == [0.0592, 0.0492, 0.0392]
    assert signal.completeness == "COMPLETE"


def test_prova_extracts_тейк_профит_inline() -> None:
    """'Тейк профит: price' inline (trader_b format) must be recognised."""
    from src.parser_v2.profiles.trader_prova.signal_extractor import SignalExtractor as ProvaExtractor

    text = "$BTCUSDT Лонг\nВход: по текущим (≈63150)\nТейк профит: 65050\nСтоп лосс: 62790\n"
    normalized = NormalizedText(raw_text=text, normalized_text=text.lower(), lines=text.splitlines())
    signal = ProvaExtractor().extract(normalized, market_hint=True)

    assert signal is not None
    assert len(signal.take_profits) == 1
    assert signal.take_profits[0].price.value == 65050.0


def test_prova_entry_price_inside_parens_after_market_text() -> None:
    """'Вход: по текущим (≈63150)' — price in parens after market desc (trader_b format)."""
    from src.parser_v2.profiles.trader_prova.signal_extractor import SignalExtractor as ProvaExtractor

    text = "$BTCUSDT Лонг (сделка на споте)\nВход: по текущим (≈63150)\nТейк профит: 65050\nСтоп лосс: 62790\n"
    normalized = NormalizedText(raw_text=text, normalized_text=text.lower(), lines=text.splitlines())
    signal = ProvaExtractor().extract(normalized, market_hint=True)

    assert signal is not None
    assert signal.entries[0].entry_type == "MARKET"
    assert signal.entries[0].price is not None
    assert signal.entries[0].price.value == 63150.0


def test_prova_caso_a_cyrillic_market_loanword_with_price() -> None:
    """'Вход по маркету: 0,06023' — Cyrillic loanword 'маркету' must be recognised as MARKET entry."""
    from src.parser_v2.profiles.trader_prova.signal_extractor import SignalExtractor as ProvaExtractor

    text = (
        "[trader #A] Signal #a1\n"
        "#WLFIUSDT LONG\n"
        "Риск 1%\n"
        "Вход по маркету: 0,06023\n"
        "TP1: 0.06489\n"
        "Tp2: 0.0732\n"
        "Tp3: 0.0804\n"
        "Стоп: 0.05609\n"
    )
    normalized = NormalizedText(raw_text=text, normalized_text=text.lower(), lines=text.splitlines())
    signal = ProvaExtractor().extract(normalized, market_hint=False)

    assert signal is not None
    assert len(signal.entries) == 1
    assert signal.entries[0].entry_type == "MARKET"
    assert signal.entries[0].price is not None
    assert abs(signal.entries[0].price.value - 0.06023) < 1e-9


def test_prova_caso_c_market_entry_no_price() -> None:
    """'Вход по рынку' with no explicit price → MARKET entry with price=None (no market_hint needed)."""
    from src.parser_v2.profiles.trader_prova.signal_extractor import SignalExtractor as ProvaExtractor

    text = (
        "Signal ID: #c6  #ZECUSDT SHORT\n"
        "Вход по рынку\n"
        "TP: 244\n"
        "SL: 482,47\n"
        "Без плеча, риск на сделку 1%\n"
    )
    normalized = NormalizedText(raw_text=text, normalized_text=text.lower(), lines=text.splitlines())
    signal = ProvaExtractor().extract(normalized, market_hint=False)

    assert signal is not None
    assert len(signal.entries) == 1
    assert signal.entries[0].entry_type == "MARKET"
    assert signal.entries[0].price is None


def test_trader_c_numbered_tp_list_with_latin_T_header() -> None:
    """trader_c real format: Latin T in 'Tейк-профит:' + numbered list '1) price (RR)'."""
    from src.parser_v2.profiles.trader_prova.signal_extractor import SignalExtractor as ProvaExtractor

    text = (
        "[trader#С]\n\n"
        "$BTCUSDT - SHORT \n\n"
        "Вход с текущих (88000-87900) \n\n"
        "Stop 88450.\xa0 1% деп\n\n"
        "Tейк-профит:\n"
        "1) 87100 (RR - 1:2)\n\n"
        "2) 86500 (RR - 1:3)\n\n"
        "3) 86000"
    )
    normalized = NormalizedText(raw_text=text, normalized_text=text.lower(), lines=text.splitlines())
    signal = ProvaExtractor().extract(normalized, market_hint=True)

    assert signal is not None
    assert signal.side == "SHORT"
    assert [tp.price.value for tp in signal.take_profits] == [87100.0, 86500.0, 86000.0]
    assert signal.stop_loss is not None
    assert signal.stop_loss.price.value == 88450.0
    assert "take_profits" not in signal.missing_fields
    assert signal.completeness == "COMPLETE"


def test_trader_c_cyrillic_T_header_still_works() -> None:
    """Regression: full-Cyrillic 'Тейк-профит:' must still work after the Latin-T fix."""
    from src.parser_v2.profiles.trader_prova.signal_extractor import SignalExtractor as ProvaExtractor

    text = (
        "$ETHUSDT SHORT\n"
        "Вход: 2500\n"
        "Stop 2600\n"
        "Тейк-профит:\n"
        "1) 2400\n"
        "2) 2300\n"
    )
    normalized = NormalizedText(raw_text=text, normalized_text=text.lower(), lines=text.splitlines())
    signal = ProvaExtractor().extract(normalized)

    assert signal is not None
    assert [tp.price.value for tp in signal.take_profits] == [2400.0, 2300.0]


def test_tp_numbered_list_with_blank_lines_between_entries() -> None:
    """All 3 TPs must be extracted even when blank lines separate them."""
    from src.parser_v2.profiles.trader_prova.signal_extractor import SignalExtractor as ProvaExtractor

    text = (
        "$LINKUSDT LONG\n"
        "Вход: 14\n"
        "Stop 12\n"
        "Tейк-профит:\n"
        "1) 16 (RR - 1:2)\n\n"
        "2) 18 (RR - 1:4)\n\n"
        "3) 20"
    )
    normalized = NormalizedText(raw_text=text, normalized_text=text.lower(), lines=text.splitlines())
    signal = ProvaExtractor().extract(normalized)

    assert signal is not None
    assert len(signal.take_profits) == 3
    assert signal.take_profits[2].price.value == 20.0


def test_tp_numbered_list_stops_at_non_price_line() -> None:
    """Numbered TP block must stop when a non-price line follows the list."""
    from src.parser_v2.profiles.trader_prova.signal_extractor import SignalExtractor as ProvaExtractor

    text = (
        "$BTCUSDT SHORT\n"
        "Вход: 90000\n"
        "Stop 92000\n"
        "Tейк-профит:\n"
        "1) 88000\n"
        "2) 86000\n"
        "Комментарий: держим позицию\n"
        "3) 84000\n"
    )
    normalized = NormalizedText(raw_text=text, normalized_text=text.lower(), lines=text.splitlines())
    signal = ProvaExtractor().extract(normalized)

    assert signal is not None
    assert len(signal.take_profits) == 2
    assert [tp.price.value for tp in signal.take_profits] == [88000.0, 86000.0]


def test_existing_bullet_tp_format_not_broken() -> None:
    """Regression: dash-bullet format 'TPs: — 5.86' must still work."""
    from src.parser_v2.profiles.trader_prova.signal_extractor import SignalExtractor as ProvaExtractor

    text = (
        "ORDIUSDT.P — ЛОНГ (вход с текущих)\n"
        "• Вход: 5.0113\n"
        "• Стоп: 4.4913\n"
        "• TPs:\n"
        "— 5.8613 (+17.0%)\n"
        "— 6.3269 (+26.3%)\n"
        "— 7.2469 (+44.6%)\n"
    )
    normalized = NormalizedText(raw_text=text, normalized_text=text.lower(), lines=text.splitlines())
    signal = ProvaExtractor().extract(normalized)

    assert signal is not None
    assert [tp.price.value for tp in signal.take_profits] == [5.8613, 6.3269, 7.2469]


def test_trader_b_market_entry_price_inside_current_parentheses() -> None:
    from src.parser_v2.contracts.markers import NormalizedText
    from src.parser_v2.profiles.Legacy.trader_b_legacy.signal_extractor import SignalExtractor as TraderBExtractor

    text = (
        "[trader#b] Signal ID: #b43\n\n"
        "$BTCUSDT - Лонг (сделка на споте)\n\n"
        "Вход: по текущим (≈63150)\n\n"
        "Тейк профит: 65050\n\n"
        "Стоп лосс: 62790\n\n"
        "Риск на сделку 0.6%\n"
        "Потенциальная прибыль 3%"
    )

    normalized = NormalizedText(
        raw_text=text,
        normalized_text=text.lower(),
        lines=text.splitlines(),
    )

    signal = TraderBExtractor().extract(normalized, market_hint=True)

    assert signal is not None
    assert signal.completeness == "COMPLETE"
    assert len(signal.entries) == 1
    assert signal.entries[0].entry_type == "MARKET"
    assert signal.entries[0].price is not None
    assert signal.entries[0].price.value == 63150.0
    assert signal.stop_loss.price.value == 62790.0
    assert signal.take_profits[0].price.value == 65050.0
    assert signal.risk_hint.value == 0.6
