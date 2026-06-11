from __future__ import annotations

import re
from pathlib import Path

from src.parser_v2.contracts.context import ParserContext, TargetExtractionResult
from src.parser_v2.contracts.markers import MarkerEvidence, NormalizedText
from src.parser_v2.contracts.parsed_message import ParsedIntent, SignalDraft
from src.parser_v2.contracts.rules import ParserRules, SemanticMarkers
from src.parser_v2.core.symbol_normalizer import normalize_symbol
from src.parser_v2.core.target_hints_extractor import TargetHintsExtractor
from src.parser_v2.profiles.strategy_parser.intent_entity_extractor import IntentEntityExtractor
from src.parser_v2.profiles.strategy_parser.signal_extractor import SignalExtractor

_PROFILE_DIR = Path(__file__).parent

# "по HYPE", "по SUI", "по H" — symbol after Russian preposition "по"
_PO_SYMBOL_RE = re.compile(r"\bпо\s+(?P<symbol>[A-Z][A-Z0-9]{0,19})\b", re.IGNORECASE)

_STRONG_SOURCES = frozenset({
    "LOCAL_TEXT_LINK",
    "LOCAL_EXPLICIT_ID",
    "MESSAGE_TEXT_LINK",
    "MESSAGE_EXPLICIT_ID",
    "REPLY",
})


class StrategyParserProfile:
    trader_code = "strategy_parser"

    def __init__(
        self,
        *,
        signal_extractor: SignalExtractor | None = None,
        intent_entity_extractor: IntentEntityExtractor | None = None,
    ) -> None:
        self._signal_extractor = signal_extractor or SignalExtractor()
        self._intent_entity_extractor = intent_entity_extractor or IntentEntityExtractor()

    def load_markers(self) -> SemanticMarkers:
        return SemanticMarkers.model_validate_json(
            (_PROFILE_DIR / "semantic_markers.json").read_text(encoding="utf-8")
        )

    def load_rules(self) -> ParserRules:
        return ParserRules.model_validate_json(
            (_PROFILE_DIR / "rules.json").read_text(encoding="utf-8")
        )

    def extract_signal(
        self,
        text: NormalizedText,
        context: ParserContext,
        evidence: list[MarkerEvidence],
    ) -> SignalDraft | None:
        return self._signal_extractor.extract(text, market_hint=False)

    def extract_intent_entities(
        self,
        text: NormalizedText,
        context: ParserContext,
        evidence: list[MarkerEvidence],
    ) -> list[ParsedIntent]:
        return self._intent_entity_extractor.extract(text, evidence)

    def extract_target_hints(
        self,
        normalized: NormalizedText,
        context: ParserContext,
        markers: SemanticMarkers,
    ) -> TargetExtractionResult:
        base = TargetHintsExtractor().extract(normalized, context, markers)

        # Strong refs (reply, links, explicit IDs) always take priority.
        if base.message_target_hints.target_source in _STRONG_SOURCES:
            return base

        # Try "по SYMBOL" — works for bare tickers without USDT/USDC suffix.
        match = _PO_SYMBOL_RE.search(normalized.raw_text)
        if match is None:
            return base

        raw = match.group("symbol").upper()
        symbol = normalize_symbol(raw) or raw

        updated = base.message_target_hints.model_copy(update={
            "target_source": "SYMBOL",
            "symbols": [symbol],
        })
        return TargetExtractionResult(
            message_target_hints=updated,
            candidates=base.candidates,
        )


__all__ = ["StrategyParserProfile"]
