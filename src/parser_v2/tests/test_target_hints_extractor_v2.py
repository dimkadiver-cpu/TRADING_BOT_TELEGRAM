from __future__ import annotations

from src.parser_v2.contracts.context import ParserContext, RawContext, TargetExtractionResult
from src.parser_v2.contracts.markers import NormalizedText
from src.parser_v2.contracts.rules import MarkerSet, SemanticMarkers
from src.parser_v2.core.target_hints_extractor import TargetHintsExtractor


def _extract(
    text: str,
    reply_id: int | None = None,
    markers: SemanticMarkers | None = None,
) -> TargetExtractionResult:
    raw_ctx = RawContext(raw_text=text, reply_to_message_id=reply_id)
    context = ParserContext(raw_context=raw_ctx, reply_to_message_id=reply_id)
    normalized = NormalizedText(raw_text=text, normalized_text=text.lower())
    return TargetHintsExtractor().extract(normalized, context, markers or SemanticMarkers())


def _markers_with_explicit_id(*values: str) -> SemanticMarkers:
    return SemanticMarkers(
        target_hint_markers={
            "explicit_id": MarkerSet(strong=list(values)),
        }
    )


def test_extract_returns_extraction_result():
    result = _extract("стоп в бу")
    assert isinstance(result, TargetExtractionResult)


def test_extract_message_target_hints_preserved():
    result = _extract("стоп в бу", reply_id=100)
    assert result.message_target_hints.reply_to_message_id == 100


def test_extract_telegram_link_becomes_candidate_with_position():
    text = "https://t.me/c/777/111 стоп в бу"
    result = _extract(text)
    assert len(result.candidates) >= 1
    link_candidate = next(
        (c for c in result.candidates if c.source == "MESSAGE_TEXT_LINK"), None
    )
    assert link_candidate is not None
    assert link_candidate.value == 111
    assert link_candidate.start == 0
    assert link_candidate.line_index == 0


def test_extract_reply_becomes_candidate():
    result = _extract("стоп в бу", reply_id=100)
    reply_candidate = next(
        (c for c in result.candidates if c.source == "REPLY"), None
    )
    assert reply_candidate is not None
    assert reply_candidate.value == 100


def test_extract_multiline_links_have_correct_line_index():
    text = "https://t.me/c/777/111 стоп\nhttps://t.me/c/777/222 закрываю"
    result = _extract(text)
    link_candidates = [c for c in result.candidates if c.source == "MESSAGE_TEXT_LINK"]
    assert len(link_candidates) == 2
    line_indices = {c.value: c.line_index for c in link_candidates}
    assert line_indices[111] == 0
    assert line_indices[222] == 1


def test_extract_target_source_set_on_message_hints():
    text = "https://t.me/c/777/111 стоп"
    result = _extract(text)
    assert result.message_target_hints.target_source == "MESSAGE_TEXT_LINK"


def test_extract_reply_target_source_when_no_link():
    result = _extract("стоп в бу", reply_id=100)
    assert result.message_target_hints.target_source == "REPLY"


def test_extract_signal_id_with_hashtag_becomes_explicit_id():
    result = _extract("Signal ID: #a15", markers=_markers_with_explicit_id("Signal ID: #a"))
    assert result.message_target_hints.explicit_ids == ["a15"]
    assert result.message_target_hints.target_source == "MESSAGE_EXPLICIT_ID"


def test_extract_multiple_signal_ids_with_hashtags():
    result = _extract(
        "Signal ID: #a15 #a16",
        markers=_markers_with_explicit_id("Signal ID: #a"),
    )
    assert result.message_target_hints.explicit_ids == ["a15", "a16"]


def test_extract_multiple_signal_ids_without_hashtags():
    result = _extract(
        "Signal ID: a15 a16",
        markers=_markers_with_explicit_id("Signal ID: a"),
    )
    assert result.message_target_hints.explicit_ids == ["a15", "a16"]


def test_extract_standalone_hashtag_id_becomes_explicit_id():
    result = _extract("#a15", markers=_markers_with_explicit_id("#a"))
    assert result.message_target_hints.explicit_ids == ["a15"]
    assert result.message_target_hints.target_source == "MESSAGE_EXPLICIT_ID"


def test_extract_multiple_standalone_hashtag_ids_become_explicit_ids():
    result = _extract("#b7 #d32", markers=_markers_with_explicit_id("#a"))
    assert result.message_target_hints.explicit_ids == ["b7", "d32"]


def test_extract_explicit_ids_not_emitted_without_explicit_id_markers():
    result = _extract("Signal ID: #a15 #b7")
    assert result.message_target_hints.explicit_ids == []


def test_extract_numeric_signal_id_with_hashtag_becomes_explicit_id():
    result = _extract("Signal ID:#2205", markers=_markers_with_explicit_id("Signal ID:#0"))
    assert result.message_target_hints.explicit_ids == ["2205"]


def test_extract_standalone_numeric_hashtag_id_becomes_explicit_id():
    result = _extract("#2205", markers=_markers_with_explicit_id("#0"))
    assert result.message_target_hints.explicit_ids == ["2205"]


def test_scope_hint_ignored_when_telegram_message_ids_present():
    from src.parser_v2.contracts.context import ParserContext, RawContext
    from src.parser_v2.core.runtime import UniversalParserRuntime
    from src.parser_v2.profiles.Legacy.trader_a_legacy.profile import TraderAProfile

    text = (
        "[trader#A]\n\n"
        "XRP - https://t.me/c/3171748254/822 3.94% прибыли\n"
        "ENA - https://t.me/c/3171748254/856 убыток 9.32\n"
        "LDO - https://t.me/c/3171748254/861 прибыль 4.2%\n"
        "SHIB - https://t.me/c/3171748254/870 прибыль 3.4%\n\n"
        "Эти монеты закрываю по текущим, так как нет времени за ними следить\n\n"
        "p.s. проценты указал без учета усреднения. "
        "кто выставлял лимитки на усреднение - у вас прибыль по шортам будет больше"
    )
    context = ParserContext(raw_context=RawContext(raw_text=text))
    result = UniversalParserRuntime().parse(text, context, TraderAProfile())

    assert len(result.target_action_groups) >= 1
    all_ids = [mid for g in result.target_action_groups for mid in g.targeting.telegram_message_ids]
    assert len(all_ids) == 4
    for g in result.target_action_groups:
        assert g.targeting.scope_hint != "ALL_SHORT"
