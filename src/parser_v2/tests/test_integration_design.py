from __future__ import annotations
import pytest
from src.parser_v2.contracts.context import ParserContext, RawContext
from src.parser_v2.contracts.parsed_message import ParsedIntent
from src.parser_v2.contracts.rules import (
    ParserRules, MarkerResolutionRules, SemanticMarkers,
    WeakContextExclusionRule,
)
from src.parser_v2.core.runtime import UniversalParserRuntime


class _SimpleProfile:
    def __init__(self, intents: list[ParsedIntent], rules: ParserRules | None = None):
        self.trader_code = "test"
        self._intents = intents
        self._rules = rules or ParserRules()

    def load_markers(self) -> SemanticMarkers:
        return SemanticMarkers()

    def load_rules(self) -> ParserRules:
        return self._rules

    def extract_signal(self, text, context, evidence):
        return None

    def extract_intent_entities(self, text, context, evidence):
        return self._intents


def _run(text: str, profile: _SimpleProfile, reply_id: int | None = None):
    raw_ctx = RawContext(raw_text=text, reply_to_message_id=reply_id)
    context = ParserContext(raw_context=raw_ctx, reply_to_message_id=reply_id)
    return UniversalParserRuntime().parse(text, context, profile)


def test_B1_two_same_intents_preserved():
    """Two occurrences of same IntentType must be preserved."""
    intents = [
        ParsedIntent(type="MOVE_STOP_TO_BE", category="UPDATE", confidence=0.9),
        ParsedIntent(type="MOVE_STOP_TO_BE", category="UPDATE", confidence=0.9),
    ]
    result = _run("стоп в бу\nстоп в бу", _SimpleProfile(intents))
    all_actions = [a for g in result.target_action_groups for a in g.actions]
    ids = [a.source_intent_id for a in all_actions]
    assert len(set(ids)) == 2


def test_B1_intents_in_canonical_deduplicated():
    """CanonicalMessage.intents must not contain duplicates."""
    intents = [
        ParsedIntent(type="MOVE_STOP_TO_BE", category="UPDATE", confidence=0.9),
        ParsedIntent(type="MOVE_STOP_TO_BE", category="UPDATE", confidence=0.9),
    ]
    result = _run("стоп в бу\nстоп в бу", _SimpleProfile(intents))
    assert result.intents.count("MOVE_STOP_TO_BE") == 1


def test_C1_reply_applies_to_multiple_operations():
    """Reply + two intents → both operations on the reply target."""
    intents = [
        ParsedIntent(type="MOVE_STOP_TO_BE", category="UPDATE", confidence=0.9),
        ParsedIntent(type="CANCEL_PENDING", category="UPDATE", confidence=0.9),
    ]
    result = _run("стоп в бу\nлимитки убираем", _SimpleProfile(intents), reply_id=100)
    assert len(result.target_action_groups) == 1
    group = result.target_action_groups[0]
    assert group.targeting.reply_to_message_id == 100
    assert len(group.actions) == 2


def test_C3_global_ref_list_multiple_ops_not_partial():
    """Global link list + multiple ops → PARSED, 1 group with 2 actions."""
    intents = [
        ParsedIntent(type="MOVE_STOP_TO_BE", category="UPDATE", confidence=0.9),
        ParsedIntent(type="CANCEL_PENDING", category="UPDATE", confidence=0.9),
    ]
    text = "https://t.me/c/777/111\nhttps://t.me/c/777/222\nстоп в бу\nлимитки убираем"
    result = _run(text, _SimpleProfile(intents))
    assert result.parse_status == "PARSED"
    assert len(result.target_action_groups) == 1
    group = result.target_action_groups[0]
    assert 111 in group.targeting.telegram_message_ids
    assert 222 in group.targeting.telegram_message_ids
    assert len(group.actions) == 2


def test_D2_different_types_not_deduplicated():
    """MOVE_STOP_TO_BE + CANCEL_PENDING → both in intents."""
    intents = [
        ParsedIntent(type="MOVE_STOP_TO_BE", category="UPDATE", confidence=0.9),
        ParsedIntent(type="CANCEL_PENDING", category="UPDATE", confidence=0.9),
    ]
    result = _run("стоп в бу\nлимитки убираем", _SimpleProfile(intents))
    assert "MOVE_STOP_TO_BE" in result.intents
    assert "CANCEL_PENDING" in result.intents
