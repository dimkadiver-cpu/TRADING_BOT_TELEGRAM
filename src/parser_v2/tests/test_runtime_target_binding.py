from __future__ import annotations
import json
import pytest
from src.parser_v2.contracts.context import ParserContext, RawContext
from src.parser_v2.contracts.markers import NormalizedText, MarkerEvidence
from src.parser_v2.contracts.parsed_message import ParsedIntent, SignalDraft
from src.parser_v2.contracts.rules import ParserRules, SemanticMarkers
from src.parser_v2.core.runtime import UniversalParserRuntime, TraderParserProfile


class _MockProfile:
    trader_code = "mock"

    def load_markers(self) -> SemanticMarkers:
        return SemanticMarkers()

    def load_rules(self) -> ParserRules:
        return ParserRules()

    def extract_signal(self, text, context, evidence) -> None:
        return None

    def extract_intent_entities(self, text, context, evidence) -> list[ParsedIntent]:
        return self._intents

    def set_intents(self, intents: list[ParsedIntent]) -> None:
        self._intents = intents


def _run(text: str, profile: _MockProfile, reply_id: int | None = None):
    raw_ctx = RawContext(raw_text=text, reply_to_message_id=reply_id)
    context = ParserContext(raw_context=raw_ctx, reply_to_message_id=reply_id)
    return UniversalParserRuntime().parse(text, context, profile)


def test_runtime_passes_raw_text_to_resolver():
    profile = _MockProfile()
    profile.set_intents([])
    result = _run("test", profile)
    assert result is not None


def test_runtime_assigns_occurrence_ids():
    profile = _MockProfile()
    profile.set_intents([
        ParsedIntent(type="MOVE_STOP_TO_BE", category="UPDATE", confidence=0.9),
        ParsedIntent(type="MOVE_STOP_TO_BE", category="UPDATE", confidence=0.9),
    ])
    result = _run("стоп в бу\nстоп в бу", profile)
    all_actions = [a for g in result.target_action_groups for a in g.actions]
    ids = [a.source_intent_id for a in all_actions]
    assert len(ids) == 2
    assert ids[0] != ids[1]


def test_runtime_with_reply_produces_target_action_group():
    profile = _MockProfile()
    profile.set_intents([
        ParsedIntent(type="MOVE_STOP_TO_BE", category="UPDATE", confidence=0.9),
    ])
    result = _run("стоп в бу", profile, reply_id=100)
    assert len(result.target_action_groups) == 1
    assert result.target_action_groups[0].targeting.reply_to_message_id == 100


def test_runtime_global_refs_two_ops_not_partial():
    profile = _MockProfile()
    profile.set_intents([
        ParsedIntent(type="MOVE_STOP_TO_BE", category="UPDATE", confidence=0.9),
        ParsedIntent(type="CANCEL_PENDING", category="UPDATE", confidence=0.9),
    ])
    text = "https://t.me/c/777/111\nhttps://t.me/c/777/222\nстоп в бу\nлимитки убираем"
    result = _run(text, profile)
    assert result.parse_status == "PARSED"
    all_actions = [a for g in result.target_action_groups for a in g.actions]
    assert len(all_actions) == 2
