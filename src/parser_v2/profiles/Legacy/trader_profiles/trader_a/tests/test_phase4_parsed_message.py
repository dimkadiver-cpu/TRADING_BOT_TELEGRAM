from __future__ import annotations

import json
from pathlib import Path

from src.parser.parsed_message import ParsedMessage
from src.parser.trader_profiles.base import ParserContext
from src.parser.trader_profiles.shared.rules_schema import (
    validate_profile_rules,
    validate_semantic_markers,
)
from src.parser.trader_profiles.trader_a.profile import TraderAProfileParser


_PROFILE_DIR = Path(__file__).resolve().parents[1]
_SEMANTIC_MARKERS_PATH = _PROFILE_DIR / "semantic_markers.json"
_RULES_PATH = _PROFILE_DIR / "rules.json"


def _context(*, text: str, reply_to: int | None = None) -> ParserContext:
    return ParserContext(
        trader_code="trader_a",
        message_id=9001,
        reply_to_message_id=reply_to,
        channel_id="-1001",
        raw_text=text,
        extracted_links=[],
        hashtags=[],
    )


def test_phase4_trader_a_split_rules_files_validate_against_new_schema() -> None:
    semantic_markers = json.loads(_SEMANTIC_MARKERS_PATH.read_text(encoding="utf-8"))
    rules = json.loads(_RULES_PATH.read_text(encoding="utf-8"))

    assert validate_semantic_markers(semantic_markers) == []
    assert validate_profile_rules(rules) == []


def test_phase4_trader_a_parse_signal_returns_parsed_message() -> None:
    parser = TraderAProfileParser()
    text = "BTCUSDT long\nentry 62000\nsl: 61000\ntp1: 63000\ntp2: 64000"

    parsed = parser.parse(text, _context(text=text))

    assert isinstance(parsed, ParsedMessage)
    assert parsed.primary_class == "SIGNAL"
    assert parsed.parse_status == "PARSED"
    assert parsed.signal is not None
    assert parsed.signal.symbol == "BTCUSDT"
    assert parsed.signal.side == "LONG"
    assert [leg.price.value if leg.price else None for leg in parsed.signal.entries] == [62000.0]
    assert parsed.signal.stop_loss is not None
    assert parsed.signal.stop_loss.price is not None
    assert parsed.signal.stop_loss.price.value == 61000.0
    assert [tp.price.value for tp in parsed.signal.take_profits] == [63000.0, 64000.0]


def test_phase4_trader_a_parse_update_preserves_detection_strength_and_reply_targeting() -> None:
    parser = TraderAProfileParser()
    text = "стоп в бу"

    parsed = parser.parse(text, _context(text=text, reply_to=77))

    assert parsed.primary_class == "UPDATE"
    assert parsed.primary_intent == "MOVE_STOP_TO_BE"
    assert len(parsed.intents) == 1
    assert parsed.intents[0].type == "MOVE_STOP_TO_BE"
    assert parsed.intents[0].detection_strength == "strong"
    assert parsed.targeting is not None
    assert parsed.targeting.strategy == "REPLY_OR_LINK"
    assert parsed.targeting.refs[0].ref_type == "REPLY"
    assert parsed.targeting.refs[0].value == 77


def test_phase4_trader_a_parse_report_final_result_can_be_weak_detection() -> None:
    parser = TraderAProfileParser()
    text = "итого 1.2R"

    parsed = parser.parse(text, _context(text=text))

    assert parsed.primary_class == "REPORT"
    assert parsed.primary_intent == "REPORT_FINAL_RESULT"
    assert len(parsed.intents) == 1
    assert parsed.intents[0].type == "REPORT_FINAL_RESULT"
    assert parsed.intents[0].detection_strength == "weak"
    assert parsed.intents[0].entities.result is not None
    assert parsed.intents[0].entities.result.unit == "R"
    assert parsed.intents[0].entities.result.value == 1.2
