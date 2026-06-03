from __future__ import annotations

from pathlib import Path

from src.parser_v2.contracts.context import ParserContext
from src.parser_v2.contracts.markers import MarkerEvidence, NormalizedText
from src.parser_v2.contracts.parsed_message import ParsedIntent, SignalDraft
from src.parser_v2.contracts.rules import (
    ParserRules,
    SemanticMarkers,
)
from src.parser_v2.profiles.trader_c.intent_entity_extractor import IntentEntityExtractor
from src.parser_v2.profiles.trader_c.signal_extractor import SignalExtractor

_PROFILE_DIR = Path(__file__).parent


class TraderDProfile:
    trader_code = "trader_d"

    def __init__(
        self,
        *,
        signal_extractor: SignalExtractor | None = None,
        intent_entity_extractor: IntentEntityExtractor | None = None,
    ) -> None:
        if signal_extractor is None:
            rules = self.load_rules()
            em = rules.extraction_markers
            signal_extractor = SignalExtractor(
                risk_prefixes=em["risk_prefix"].strong or None if "risk_prefix" in em else None,
                risk_suffixes=em["risk_suffix"].strong or None if "risk_suffix" in em else None,
            )
        self._signal_extractor = signal_extractor
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
        market_hint = any(
            e.kind == "entry_type" and e.name == "MARKET" and not e.suppressed
            for e in evidence
        )
        return self._signal_extractor.extract(text, market_hint=market_hint)

    def extract_intent_entities(
        self,
        text: NormalizedText,
        context: ParserContext,
        evidence: list[MarkerEvidence],
    ) -> list[ParsedIntent]:
        return self._intent_entity_extractor.extract(text, evidence)


__all__ = ["TraderCProfile"]
