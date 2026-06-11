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

    def _update_groups(self, text: str, reply_id: int | None = None):
        result = self._run(text, reply_id=reply_id)
        assert result.primary_class == "UPDATE", f"expected UPDATE, got {result.primary_class}"
        assert result.target_action_groups, "expected target_action_groups"
        return result.target_action_groups

    # --- custom extract_target_hints: bare ticker via "по SYMBOL" ---

    def test_close_bare_ticker_hype(self):
        text = (
            "Стратегия «RSI(2) Коннора» закрыла ЛОНГ по H · интрадей (1H) — поймала стоп\n\n"
            "Результат: −1.0R  (вход 0.1851 → выход 0.16289)"
        )
        groups = self._update_groups(text)
        group = groups[0]
        assert group.targeting.target_source == "SYMBOL"
        assert group.targeting.scope_hint == "SYMBOL"
        assert "H" in group.targeting.symbols

    def test_close_bare_ticker_sui(self):
        text = (
            "Стратегия «Supertrend» закрыла ШОРТ по SUI · интрадей (1H) — вышла по обратному сигналу\n\n"
            "Результат: −0.7R  (вход 0.7326 → выход 0.7551)"
        )
        groups = self._update_groups(text)
        group = groups[0]
        assert group.targeting.target_source == "SYMBOL"
        assert group.targeting.scope_hint == "SYMBOL"
        assert "SUI" in group.targeting.symbols

    def test_close_usdt_ticker_still_works(self):
        text = "закрыла по BTCUSDT → выход 45000"
        groups = self._update_groups(text)
        group = groups[0]
        assert group.targeting.target_source == "SYMBOL"
        assert group.targeting.scope_hint == "SYMBOL"
        assert "BTCUSDT" in group.targeting.symbols

    def test_reply_takes_priority_over_po_symbol(self):
        text = "закрыла по SUI → выход 0.7551"
        result = self._run(text, reply_id=1234)
        assert result.primary_class == "UPDATE"
        if result.target_action_groups:
            group = result.target_action_groups[0]
            assert group.targeting.target_source == "REPLY"
            assert group.targeting.reply_to_message_id == 1234

    def test_close_without_symbol_or_reply_produces_unknown_targeting(self):
        result = self._run("закрыла → выход 0.162")
        if result.primary_class == "UPDATE" and result.target_action_groups:
            group = result.target_action_groups[0]
            assert group.targeting.target_source == "UNKNOWN"

    # --- signal message classification ---

    def test_signal_with_virtual_trade_marker_still_produces_signal(self):
        # strategy_parser has no info_markers section → INFO short-circuit never fires.
        # Messages with Вход/стоп/цель are classified as SIGNAL regardless of
        # INFO_ONLY intent markers (those are in intent_markers, not info_markers).
        text = (
            "Стратегия «RSI(2) Коннора» открыла ЛОНГ по HYPE · интрадей (1H)\n\n"
            "Вход 54.69, стоп 53.32, цель 59.46 — риск к прибыли 1 к 3.5\n\n"
            "Это виртуальная сделка в открытом тесте, реальных денег нет."
        )
        result = self._run(text)
        assert result.primary_class == "SIGNAL"

    # --- extract_target_hints unit-level ---

    def test_extract_target_hints_po_symbol_no_reply(self):
        from src.parser_v2.profiles.strategy_parser.profile import StrategyParserProfile
        profile = StrategyParserProfile()
        markers = profile.load_markers()
        normalized = NormalizedText(
            raw_text="закрыла по SUI → выход 0.75",
            normalized_text="закрыла по sui → выход 0.75",
        )
        context = ParserContext()
        result = profile.extract_target_hints(normalized, context, markers)
        assert result.message_target_hints.target_source == "SYMBOL"
        assert "SUI" in result.message_target_hints.symbols

    def test_extract_target_hints_reply_overrides_po_symbol(self):
        from src.parser_v2.profiles.strategy_parser.profile import StrategyParserProfile
        profile = StrategyParserProfile()
        markers = profile.load_markers()
        raw_ctx = RawContext(raw_text="закрыла по SUI → выход 0.75", reply_to_message_id=999)
        normalized = NormalizedText(
            raw_text="закрыла по SUI → выход 0.75",
            normalized_text="закрыла по sui → выход 0.75",
        )
        context = ParserContext(raw_context=raw_ctx, reply_to_message_id=999)
        result = profile.extract_target_hints(normalized, context, markers)
        assert result.message_target_hints.target_source == "REPLY"
        assert result.message_target_hints.reply_to_message_id == 999
