from __future__ import annotations

import re
from pathlib import Path

from src.parser_v2.contracts.context import ParserContext, TargetExtractionResult
from src.parser_v2.contracts.markers import MarkerEvidence, NormalizedText
from src.parser_v2.contracts.parsed_message import ParsedIntent, SignalDraft
from src.parser_v2.contracts.rules import ParserRules, SemanticMarkers
from src.parser_v2.core.profile_assets import load_markers_cached, load_rules_cached
from src.parser_v2.core.symbol_normalizer import normalize_symbol
from src.parser_v2.core.target_hints_extractor import TargetHintsExtractor
from src.parser_v2.profiles.trader_3_1.intent_entity_extractor import IntentEntityExtractor
from src.parser_v2.profiles.trader_3_1.signal_extractor import SignalExtractor

_PROFILE_DIR = Path(__file__).parent
_STRONG_SOURCES = frozenset({
    "LOCAL_TEXT_LINK",
    "LOCAL_EXPLICIT_ID",
    "MESSAGE_TEXT_LINK",
    "MESSAGE_EXPLICIT_ID",
    "REPLY",
})
_HASHTAG_SYMBOL_RE = re.compile(
    r"#(?P<symbol>[A-Z0-9]{2,20})(?:/(?P<quote>USDT|USDC|USD|BTC|ETH))?\b",
    re.IGNORECASE,
)


class Trader31Profile:
    trader_code = "trader_3_1"

    def __init__(
        self,
        *,
        signal_extractor: SignalExtractor | None = None,
        intent_entity_extractor: IntentEntityExtractor | None = None,
    ) -> None:
        self._signal_extractor = signal_extractor or SignalExtractor()
        self._intent_entity_extractor = intent_entity_extractor or IntentEntityExtractor()

    def load_markers(self) -> SemanticMarkers:
        return load_markers_cached(_PROFILE_DIR)

    def load_rules(self) -> ParserRules:
        return load_rules_cached(_PROFILE_DIR)

    def extract_signal(
        self,
        text: NormalizedText,
        context: ParserContext,
        evidence: list[MarkerEvidence],
    ) -> SignalDraft | None:
        return self._signal_extractor.extract(text)

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
        if base.message_target_hints.target_source in _STRONG_SOURCES:
            return base

        match = _HASHTAG_SYMBOL_RE.search(normalized.raw_text)
        if match is None:
            return base

        symbol_token = match.group("symbol").upper()
        if symbol_token == "SIGNAL":
            return base

        quote = (match.group("quote") or "USDT").upper()
        symbol = normalize_symbol(f"{symbol_token}{quote}")
        if symbol is None:
            return base

        updated = base.message_target_hints.model_copy(update={
            "target_source": "SYMBOL",
            "symbols": [symbol],
        })
        return TargetExtractionResult(
            message_target_hints=updated,
            candidates=base.candidates,
        )


__all__ = ["Trader31Profile"]
