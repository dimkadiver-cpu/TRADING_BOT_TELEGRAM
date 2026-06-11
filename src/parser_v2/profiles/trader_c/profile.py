from __future__ import annotations

from pathlib import Path

from src.parser_v2.contracts.context import ParserContext
from src.parser_v2.contracts.markers import MarkerEvidence, NormalizedText
from src.parser_v2.contracts.parsed_message import ParsedIntent, SignalDraft
from src.parser_v2.contracts.rules import (
    ParserRules,
    SemanticMarkers,
)
from src.parser_v2.core.profile_assets import load_markers_cached, load_rules_cached
from src.parser_v2.profiles.trader_c.intent_entity_extractor import IntentEntityExtractor
from src.parser_v2.profiles.trader_c.signal_extractor import SignalExtractor

_PROFILE_DIR = Path(__file__).parent


class TraderCProfile:
    trader_code = "trader_c"

    def __init__(
        self,
        *,
        signal_extractor: SignalExtractor | None = None,
        intent_entity_extractor: IntentEntityExtractor | None = None,
    ) -> None:
        rules = self.load_rules()
        self._default_entry_type = rules.default_entry_type
        if signal_extractor is None:
            em = rules.extraction_markers
            signal_extractor = SignalExtractor(
                risk_prefixes=em["risk_prefix"].strong or None if "risk_prefix" in em else None,
                risk_suffixes=em["risk_suffix"].strong or None if "risk_suffix" in em else None,
            )
        self._signal_extractor = signal_extractor
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
        has_limit = any(e.kind == "entry_type" and e.name == "LIMIT" and not e.suppressed for e in evidence)
        has_market = any(e.kind == "entry_type" and e.name == "MARKET" and not e.suppressed for e in evidence)
        if has_limit:
            market_hint = False
        elif has_market:
            market_hint = True
        else:
            market_hint = self._default_entry_type == "MARKET"
        return self._signal_extractor.extract(text, market_hint=market_hint)

    def extract_intent_entities(
        self,
        text: NormalizedText,
        context: ParserContext,
        evidence: list[MarkerEvidence],
    ) -> list[ParsedIntent]:
        return self._intent_entity_extractor.extract(text, evidence)


__all__ = ["TraderCProfile"]
