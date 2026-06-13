from __future__ import annotations

import re
from pathlib import Path

from src.parser_v2.contracts.context import ParserContext, TargetExtractionResult
from src.parser_v2.contracts.markers import MarkerEvidence, NormalizedText
from src.parser_v2.contracts.parsed_message import ParsedIntent, SignalDraft
from src.parser_v2.contracts.rules import ParserRules, SemanticMarkers
from src.parser_v2.core.parsing_utils import extract_side_from_text, resolve_market_hint
from src.parser_v2.core.profile_assets import load_markers_cached, load_rules_cached
from src.parser_v2.core.symbol_normalizer import normalize_symbol
from src.parser_v2.core.target_hints_extractor import TargetHintsExtractor
from src.parser_v2.profiles.strategy_parser.intent_entity_extractor import IntentEntityExtractor
from src.parser_v2.profiles.strategy_parser.signal_extractor import SignalExtractor

_PROFILE_DIR = Path(__file__).parent

# "по HYPE", "по SUI", "по H", "по 1000PEPE" — symbol after Russian preposition "по".
# Optional leading numeric multiplier (e.g. 1000PEPE/1000BONK) is preserved; at least one letter required.
_PO_SYMBOL_RE = re.compile(r"\bпо\s+(?P<symbol>\d{0,7}[A-Z][A-Z0-9]{0,19})\b", re.IGNORECASE)

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
        rules = self.load_rules()
        self._default_entry_type = rules.default_entry_type
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
        market_hint = resolve_market_hint(evidence, self._default_entry_type)
        return self._signal_extractor.extract(text, market_hint=market_hint)

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
        side = extract_side_from_text(normalized.normalized_text or normalized.raw_text)

        updated = base.message_target_hints.model_copy(update={
            "target_source": "SYMBOL",
            "symbols": [symbol],
            "side": side,
        })
        return TargetExtractionResult(
            message_target_hints=updated,
            candidates=base.candidates,
        )


__all__ = ["StrategyParserProfile"]
