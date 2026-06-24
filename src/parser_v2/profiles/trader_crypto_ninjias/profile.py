from __future__ import annotations

from pathlib import Path

from src.parser_v2.contracts.context import ParserContext
from src.parser_v2.contracts.markers import MarkerEvidence, NormalizedText
from src.parser_v2.contracts.parsed_message import ParsedIntent, SignalDraft
from src.parser_v2.contracts.rules import ParserRules, SemanticMarkers
from src.parser_v2.core.profile_assets import load_markers_cached, load_rules_cached

from .intent_entity_extractor import IntentEntityExtractor
from .signal_extractor import SignalExtractor

_PROFILE_DIR = Path(__file__).parent


class TraderCryptoNinjiasProfile:
    trader_code = "trader_crypto_ninjias"

    def __init__(self) -> None:
        self._signal_extractor = SignalExtractor()
        self._intent_entity_extractor = IntentEntityExtractor()

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
        return self._signal_extractor.extract(text=text, context=context, evidence=evidence)

    def extract_intent_entities(
        self,
        text: NormalizedText,
        context: ParserContext,
        evidence: list[MarkerEvidence],
    ) -> list[ParsedIntent]:
        return self._intent_entity_extractor.extract(text=text, context=context, evidence=evidence)
