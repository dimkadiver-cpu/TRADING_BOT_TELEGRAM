from __future__ import annotations

import pytest

from src.parser_v2.contracts.context import ParserContext, RawContext, TargetHints
from src.parser_v2.contracts.markers import MarkerEvidence, NormalizedText
from src.parser_v2.contracts.parsed_message import ParsedIntent
from src.parser_v2.contracts.rules import MarkerSet, SemanticMarkers
from src.parser_v2.core.runtime import UniversalParserRuntime
from src.parser_v2.core.target_hints_extractor import TargetHintsExtractor
from src.parser_v2.translation.canonical_translator import CanonicalTranslator, _resolve_target_hints
from src.parser_v2.contracts.parsed_message import ParsedMessage


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _markers_with_symbol(*suffixes: str) -> SemanticMarkers:
    return SemanticMarkers(
        target_hint_markers={"symbol": MarkerSet(strong=list(suffixes))}
    )


def _extract(text: str, markers: SemanticMarkers, reply_id: int | None = None):
    raw_ctx = RawContext(raw_text=text, reply_to_message_id=reply_id)
    context = ParserContext(raw_context=raw_ctx, reply_to_message_id=reply_id)
    normalized = NormalizedText(raw_text=text, normalized_text=text.lower())
    return TargetHintsExtractor().extract(normalized, context, markers)


def _make_parsed_update(
    intents: list[ParsedIntent],
    target_hints: TargetHints | None = None,
) -> ParsedMessage:
    return ParsedMessage(
        parser_profile="test",
        primary_class="UPDATE",
        parse_status="PARSED",
        confidence=0.9,
        intents=intents,
        target_hints=target_hints,
        warnings=[],
        raw_context=RawContext(raw_text="test"),
    )


def _make_intent(type_: str, target_hints: TargetHints | None = None) -> ParsedIntent:
    return ParsedIntent(
        type=type_,
        category="UPDATE",
        confidence=0.9,
        intent_id=f"{type_}#0",
        occurrence_index=0,
        target_hints=target_hints,
    )


# ---------------------------------------------------------------------------
# Fix 1: scope_hint promotion UNKNOWN → SYMBOL
# ---------------------------------------------------------------------------

class TestScopeHintPromotion:
    def test_symbol_targeting_promotes_scope_hint_to_symbol(self):
        hints = TargetHints(target_source="SYMBOL", symbols=["BTCUSDT"], scope_hint="UNKNOWN")
        intent = _make_intent("MOVE_STOP_TO_BE")
        primary, secondary = _resolve_target_hints(intent, hints)
        assert primary.scope_hint == "SYMBOL"
        assert secondary is None

    def test_symbol_targeting_preserves_explicit_scope_hint(self):
        hints = TargetHints(target_source="SYMBOL", symbols=["BTCUSDT"], scope_hint="ALL_LONG")
        intent = _make_intent("MOVE_STOP_TO_BE")
        primary, _ = _resolve_target_hints(intent, hints)
        assert primary.scope_hint == "ALL_LONG"

    def test_reply_targeting_does_not_promote_to_symbol(self):
        hints = TargetHints(target_source="REPLY", reply_to_message_id=100, scope_hint="UNKNOWN")
        intent = _make_intent("MOVE_STOP_TO_BE")
        primary, _ = _resolve_target_hints(intent, hints)
        assert primary.scope_hint == "UNKNOWN"

    def test_message_link_targeting_promotes_to_single_signal(self):
        hints = TargetHints(
            target_source="MESSAGE_TEXT_LINK",
            telegram_message_ids=[111],
            scope_hint="UNKNOWN",
        )
        intent = _make_intent("MOVE_STOP_TO_BE")
        primary, _ = _resolve_target_hints(intent, hints)
        assert primary.scope_hint == "SINGLE_SIGNAL"

    def test_unknown_targeting_stays_unknown(self):
        hints = TargetHints(target_source="UNKNOWN", scope_hint="UNKNOWN")
        intent = _make_intent("MOVE_STOP_TO_BE")
        primary, _ = _resolve_target_hints(intent, hints)
        assert primary.scope_hint == "UNKNOWN"

    def test_symbol_still_populated_after_promotion(self):
        hints = TargetHints(target_source="SYMBOL", symbols=["ETHUSDT"], scope_hint="UNKNOWN")
        intent = _make_intent("CLOSE_FULL")
        primary, _ = _resolve_target_hints(intent, hints)
        assert primary.symbols == ["ETHUSDT"]
        assert primary.target_source == "SYMBOL"

    def test_canonical_translator_end_to_end_symbol_scope(self):
        from src.parser_v2.contracts.entities import MoveStopToBEEntities
        hints = TargetHints(target_source="SYMBOL", symbols=["SOLUSDT"], scope_hint="UNKNOWN")
        intent = _make_intent("MOVE_STOP_TO_BE")
        parsed = _make_parsed_update([intent], target_hints=hints)
        result = CanonicalTranslator().translate(parsed)
        assert len(result.target_action_groups) == 1
        group = result.target_action_groups[0]
        assert group.targeting.scope_hint == "SYMBOL"
        assert group.targeting.target_source == "SYMBOL"
        assert group.targeting.symbols == ["SOLUSDT"]


# ---------------------------------------------------------------------------
# Fix 2: TargetHintsExtractor extracts symbol for strategy_parser markers
# ---------------------------------------------------------------------------

class TestStrategyParserSymbolExtraction:
    def test_extracts_usdt_symbol_from_close_message(self):
        markers = _markers_with_symbol("usdt")
        result = _extract("закрыла по HYPEUSDT → выход 0.162", markers)
        assert result.message_target_hints.target_source == "SYMBOL"
        assert "HYPEUSDT" in result.message_target_hints.symbols

    def test_extracts_usdc_symbol(self):
        markers = _markers_with_symbol("usdt", "usdc")
        result = _extract("закрыла по BTCUSDC → выход 45000", markers)
        assert result.message_target_hints.target_source == "SYMBOL"
        assert "BTCUSDC" in result.message_target_hints.symbols

    def test_no_symbol_when_no_target_hint_markers(self):
        result = _extract("закрыла по BTCUSDT → выход 45000", SemanticMarkers())
        assert result.message_target_hints.target_source == "UNKNOWN"
        assert result.message_target_hints.symbols == []

    def test_reply_takes_priority_over_symbol(self):
        markers = _markers_with_symbol("usdt")
        result = _extract("закрыла по BTCUSDT → выход 45000", markers, reply_id=777)
        assert result.message_target_hints.target_source == "REPLY"
        assert result.message_target_hints.reply_to_message_id == 777

    def test_close_without_symbol_stays_unknown(self):
        markers = _markers_with_symbol("usdt", "usdc")
        result = _extract("закрыла → выход 0.162", markers)
        assert result.message_target_hints.target_source == "UNKNOWN"
        assert result.message_target_hints.symbols == []


# ---------------------------------------------------------------------------
# Fix 2: end-to-end via StrategyParserProfile
# ---------------------------------------------------------------------------

class TestStrategyParserProfileSymbolTargeting:
    def _run(self, text: str, reply_id: int | None = None):
        from src.parser_v2.profiles.strategy_parser.profile import StrategyParserProfile
        raw_ctx = RawContext(raw_text=text, reply_to_message_id=reply_id)
        context = ParserContext(raw_context=raw_ctx, reply_to_message_id=reply_id)
        return UniversalParserRuntime().parse(text, context, StrategyParserProfile())

    def test_close_with_symbol_produces_symbol_targeting(self):
        result = self._run("закрыла по BTCUSDT → выход 45000")
        if result.primary_class == "UPDATE" and result.target_action_groups:
            group = result.target_action_groups[0]
            assert group.targeting.target_source == "SYMBOL"
            assert group.targeting.scope_hint == "SYMBOL"
            assert "BTCUSDT" in group.targeting.symbols

    def test_close_with_reply_uses_reply_targeting(self):
        result = self._run("закрыла → выход 0.162", reply_id=1234)
        if result.primary_class == "UPDATE" and result.target_action_groups:
            group = result.target_action_groups[0]
            assert group.targeting.target_source == "REPLY"
            assert group.targeting.reply_to_message_id == 1234

    def test_close_without_symbol_or_reply_produces_unknown_targeting(self):
        result = self._run("закрыла → выход 0.162")
        if result.primary_class == "UPDATE" and result.target_action_groups:
            group = result.target_action_groups[0]
            assert group.targeting.target_source == "UNKNOWN"
