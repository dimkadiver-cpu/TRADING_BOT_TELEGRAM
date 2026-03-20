from __future__ import annotations

from src.parser.trader_profiles.base import ParserContext
from src.parser.trader_profiles.trader_a.profile import TraderAProfileParser


def _context(raw_text: str, reply_to_message_id: int | None = None) -> ParserContext:
    return ParserContext(
        trader_code="trader_a",
        message_id=1393,
        reply_to_message_id=reply_to_message_id,
        channel_id="3171748254",
        raw_text=raw_text,
        extracted_links=[],
        hashtags=[],
    )


def test_multilink_mixed_stop_updates_are_grouped_by_signature() -> None:
    parser = TraderAProfileParser()
    text = (
        "[trader#A]\n\n"
        "LINK - https://t.me/c/3171748254/978 - стоп в бу\n"
        "ALGO - https://t.me/c/3171748254/1002 стоп в бу\n"
        "ARKM - https://t.me/c/3171748254/1003 стоп в бу\n"
        "FART - https://t.me/c/3171748254/1005 стоп на 1 тейк\n"
        "UNI - https://t.me/c/3171748254/1018 стоп в бу"
    )

    result = parser.parse_message(text=text, context=_context(text))

    assert result.message_type == "UPDATE"
    message_ids = sorted([item["ref"] for item in result.target_refs if item.get("kind") == "message_id"])
    assert message_ids == [978, 1002, 1003, 1005, 1018]
    assert len(result.actions_structured) == 2

    entry_group = next(item for item in result.actions_structured if item.get("new_stop_level") == "ENTRY")
    assert entry_group["targeting"]["mode"] == "TARGET_GROUP"
    assert entry_group["targeting"]["targets"] == [978, 1002, 1003, 1018]

    tp1_group = next(item for item in result.actions_structured if item.get("new_stop_level") == "TP1")
    assert tp1_group["targeting"]["mode"] == "EXPLICIT_TARGETS"
    assert tp1_group["targeting"]["targets"] == [1005]


def test_close_shared_on_multiple_links_builds_target_group_action() -> None:
    parser = TraderAProfileParser()
    text = (
        "XRP - https://t.me/c/3171748254/1015\n"
        "ADA - https://t.me/c/3171748254/1017\n\n"
        "А давайте их прикроем, пока они рядом с ТВХ"
    )

    result = parser.parse_message(text=text, context=_context(text))

    assert result.message_type == "UPDATE"
    assert len(result.actions_structured) == 1
    action = result.actions_structured[0]
    assert action.get("action") == "CLOSE_POSITION"
    assert action.get("targeting", {}).get("mode") == "TARGET_GROUP"
    assert action.get("targeting", {}).get("targets") == [1015, 1017]


def test_close_all_shorts_builds_selector_targeting() -> None:
    parser = TraderAProfileParser()
    text = "принимаю решение зафиксировать все шорты"

    result = parser.parse_message(text=text, context=_context(text))

    assert result.message_type == "UPDATE"
    assert len(result.actions_structured) == 1
    action = result.actions_structured[0]
    assert action.get("targeting", {}).get("mode") == "SELECTOR"
    assert action.get("targeting", {}).get("selector", {}).get("side") == "SHORT"
    assert action.get("targeting", {}).get("selector", {}).get("status") == "OPEN"


def test_targeted_builder_ambiguous_case_falls_back_to_legacy() -> None:
    parser = TraderAProfileParser()
    text = "DOT - https://t.me/c/3171748254/2001 стоп переставляем на 1.2450"

    result = parser.parse_message(text=text, context=_context(text))

    assert result.message_type == "UPDATE"
    assert result.actions_structured
    assert all("targeting" not in item for item in result.actions_structured)
