from __future__ import annotations

from typing import Protocol

from src.parser_v2.contracts.canonical_message import CanonicalMessage
from src.parser_v2.contracts.context import ParserContext, TargetHints
from src.parser_v2.contracts.markers import MarkerEvidence, NormalizedText
from src.parser_v2.contracts.parsed_message import ParsedIntent, SignalDraft
from src.parser_v2.contracts.rules import ParserRules, SemanticMarkers
from src.parser_v2.core.local_disambiguator import LocalDisambiguator
from src.parser_v2.core.marker_evidence_resolver import MarkerEvidenceResolver
from src.parser_v2.core.marker_matcher import MarkerMatcher
from src.parser_v2.core.parsed_message_builder import ParsedMessageBuilder
from src.parser_v2.core.target_hints_extractor import TargetHintsExtractor
from src.parser_v2.core.text_normalizer import TextNormalizer
from src.parser_v2.translation.canonical_translator import CanonicalTranslator


class TraderParserProfile(Protocol):
    trader_code: str

    def load_markers(self) -> SemanticMarkers:
        ...

    def load_rules(self) -> ParserRules:
        ...

    def extract_signal(
        self,
        text: NormalizedText,
        context: ParserContext,
        evidence: list[MarkerEvidence],
    ) -> SignalDraft | None:
        ...

    def extract_intent_entities(
        self,
        text: NormalizedText,
        context: ParserContext,
        evidence: list[MarkerEvidence],
    ) -> list[ParsedIntent]:
        ...


class UniversalParserRuntime:
    def __init__(
        self,
        *,
        text_normalizer: TextNormalizer | None = None,
        marker_matcher: MarkerMatcher | None = None,
        marker_evidence_resolver: MarkerEvidenceResolver | None = None,
        local_disambiguator: LocalDisambiguator | None = None,
        target_hints_extractor: TargetHintsExtractor | None = None,
        parsed_message_builder: ParsedMessageBuilder | None = None,
        canonical_translator: CanonicalTranslator | None = None,
    ) -> None:
        self._text_normalizer = text_normalizer or TextNormalizer()
        self._marker_matcher = marker_matcher or MarkerMatcher()
        self._marker_evidence_resolver = marker_evidence_resolver or MarkerEvidenceResolver()
        self._local_disambiguator = local_disambiguator or LocalDisambiguator()
        self._target_hints_extractor = target_hints_extractor or TargetHintsExtractor()
        self._parsed_message_builder = parsed_message_builder or ParsedMessageBuilder()
        self._canonical_translator = canonical_translator or CanonicalTranslator()

    def parse(
        self,
        text: str,
        context: ParserContext,
        profile: TraderParserProfile,
    ) -> CanonicalMessage:
        markers = profile.load_markers()
        rules = profile.load_rules()
        normalized = self._text_normalizer.normalize(text)

        marker_matches = self._marker_matcher.match(normalized, markers)
        evidence_resolution = self._marker_evidence_resolver.resolve(marker_matches, rules)

        signal = profile.extract_signal(normalized, context, evidence_resolution.evidence)
        extracted_intents = profile.extract_intent_entities(
            normalized,
            context,
            evidence_resolution.evidence,
        )
        disambiguation = self._local_disambiguator.resolve(
            extracted_intents,
            rules,
            signal=signal,
            normalized=normalized,
        )
        target_hints = self._extract_target_hints(normalized, context, profile, markers)

        parsed = self._parsed_message_builder.build(
            parser_profile=profile.trader_code,
            normalized=normalized,
            context=context,
            signal=signal,
            intents=disambiguation.intents,
            primary_intent=disambiguation.primary_intent,
            target_hints=target_hints,
            matched_markers=marker_matches,
            suppressed_markers=evidence_resolution.suppressed_markers,
            applied_marker_rules=evidence_resolution.diagnostics.get("applied_marker_rules", []),
            applied_disambiguation_rules=disambiguation.diagnostics.get(
                "applied_disambiguation_rules",
                [],
            ),
            warnings=_warnings_from_disambiguation(disambiguation.diagnostics),
            diagnostics={
                "suppressed_intents": disambiguation.diagnostics.get(
                    "suppressed_intents",
                    [],
                ),
            },
        )

        return self._canonical_translator.translate(parsed)

    def _extract_target_hints(
        self,
        normalized: NormalizedText,
        context: ParserContext,
        profile: TraderParserProfile,
        markers: SemanticMarkers,
    ) -> TargetHints:
        custom_extractor = getattr(profile, "extract_target_hints", None)
        if callable(custom_extractor):
            custom_hints = custom_extractor(normalized, context, markers)
            if custom_hints is not None:
                return custom_hints

        return self._target_hints_extractor.extract(normalized, context, markers)


def parse(
    text: str,
    context: ParserContext,
    profile: TraderParserProfile,
) -> CanonicalMessage:
    return UniversalParserRuntime().parse(text, context, profile)


def _warnings_from_disambiguation(diagnostics: dict[str, list[str]]) -> list[str]:
    applied_rules = diagnostics.get("applied_disambiguation_rules", [])
    warnings: list[str] = []
    if "close_full_redundant_with_sl_hit" in applied_rules:
        warnings.append("close_full_redundant_with_sl_hit")
    return warnings


__all__ = ["TraderParserProfile", "UniversalParserRuntime", "parse"]
