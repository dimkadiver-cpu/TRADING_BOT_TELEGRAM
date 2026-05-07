from __future__ import annotations

from typing import Any, Protocol

from src.parser_v2.contracts.canonical_message import CanonicalMessage
from src.parser_v2.contracts.context import ParserContext, RawContext, TargetExtractionResult, TargetHints
from src.parser_v2.contracts.markers import MarkerEvidence, NormalizedText
from src.parser_v2.contracts.parsed_message import ParsedIntent, ParsedMessage, SignalDraft
from src.parser_v2.contracts.rules import ParserRules, SemanticMarkers
from src.parser_v2.core.local_disambiguator import LocalDisambiguator
from src.parser_v2.core.marker_evidence_resolver import MarkerEvidenceResolver
from src.parser_v2.core.marker_matcher import MarkerMatcher
from src.parser_v2.core.parsed_message_builder import ParsedMessageBuilder
from src.parser_v2.core.target_binding_resolver import TargetBindingResolver
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
        target_binding_resolver: TargetBindingResolver | None = None,
        parsed_message_builder: ParsedMessageBuilder | None = None,
        canonical_translator: CanonicalTranslator | None = None,
    ) -> None:
        self._text_normalizer = text_normalizer or TextNormalizer()
        self._marker_matcher = marker_matcher or MarkerMatcher()
        self._marker_evidence_resolver = marker_evidence_resolver or MarkerEvidenceResolver()
        self._local_disambiguator = local_disambiguator or LocalDisambiguator()
        self._target_hints_extractor = target_hints_extractor or TargetHintsExtractor()
        self._target_binding_resolver = target_binding_resolver or TargetBindingResolver()
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
        evidence_resolution = self._marker_evidence_resolver.resolve(
            marker_matches, rules, raw_text=normalized.raw_text, semantic_markers=markers
        )

        if _has_info_marker(evidence_resolution.evidence):
            parsed = _build_info_parsed_message(
                parser_profile=profile.trader_code,
                normalized=normalized,
                context=context,
                matched_markers=marker_matches,
                suppressed_markers=evidence_resolution.suppressed_markers,
                applied_marker_rules=evidence_resolution.diagnostics.get("applied_marker_rules", []),
            )
            return self._canonical_translator.translate(parsed)

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
        extraction = self._extract_target_hints(normalized, context, profile, markers)
        binding = self._target_binding_resolver.bind(
            disambiguation.intents,
            extraction,
        )

        build_warnings = _warnings_from_disambiguation(disambiguation.diagnostics)
        build_warnings = [*build_warnings, *binding.warnings]

        build_diagnostics: dict[str, Any] = {
            "suppressed_intents": disambiguation.diagnostics.get("suppressed_intents", []),
            **binding.diagnostics,
        }

        parsed = self._parsed_message_builder.build(
            parser_profile=profile.trader_code,
            normalized=normalized,
            context=context,
            signal=signal,
            intents=binding.intents,
            primary_intent=disambiguation.primary_intent,
            target_hints=binding.message_target_hints,
            matched_markers=marker_matches,
            suppressed_markers=evidence_resolution.suppressed_markers,
            applied_marker_rules=evidence_resolution.diagnostics.get("applied_marker_rules", []),
            applied_disambiguation_rules=disambiguation.diagnostics.get(
                "applied_disambiguation_rules", []
            ),
            warnings=build_warnings,
            diagnostics=build_diagnostics,
        )

        return self._canonical_translator.translate(parsed)

    def _extract_target_hints(
        self,
        normalized: NormalizedText,
        context: ParserContext,
        profile: TraderParserProfile,
        markers: SemanticMarkers,
    ) -> TargetExtractionResult:
        custom_extractor = getattr(profile, "extract_target_hints", None)
        if callable(custom_extractor):
            custom_hints = custom_extractor(normalized, context, markers)
            if custom_hints is not None:
                if isinstance(custom_hints, TargetHints):
                    return TargetExtractionResult(message_target_hints=custom_hints)
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


def _raw_context(normalized: NormalizedText, context: ParserContext) -> RawContext:
    if context.raw_context is not None:
        raw = context.raw_context.model_copy(deep=True)
        if raw.normalized_text is None:
            raw.normalized_text = normalized.normalized_text
        return raw

    return RawContext(
        raw_text=normalized.raw_text,
        normalized_text=normalized.normalized_text,
        message_id=context.message_id,
        reply_to_message_id=context.reply_to_message_id,
        source_chat_id=context.source_chat_id,
        source_topic_id=context.source_topic_id,
    )


def _format_markers(markers: list[MarkerEvidence]) -> list[str]:
    return [
        f"{marker.name}/{marker.strength}:{marker.marker}@{marker.start}:{marker.end}"
        for marker in markers
    ]


def _build_info_parsed_message(
    *,
    parser_profile: str,
    normalized: NormalizedText,
    context: ParserContext,
    matched_markers: list[MarkerEvidence],
    suppressed_markers: list[MarkerEvidence],
    applied_marker_rules: list[str],
) -> ParsedMessage:
    return ParsedMessage(
        parser_profile=parser_profile,
        primary_class="INFO",
        parse_status="PARSED",
        confidence=1.0,
        signal=None,
        intents=[],
        primary_intent=None,
        evidence_status="RESOLVED",
        target_hints=None,
        warnings=[],
        diagnostics={
            "matched_markers": _format_markers(matched_markers),
            "suppressed_markers": _format_markers(suppressed_markers),
            "applied_marker_rules": list(applied_marker_rules),
            "applied_disambiguation_rules": [],
            "applied_rules": list(dict.fromkeys(applied_marker_rules)),
            "category_scores": {},
        },
        raw_context=_raw_context(normalized, context),
    )


def _has_info_marker(evidence: list[MarkerEvidence]) -> bool:
    return any(marker.kind == "info" for marker in evidence)


__all__ = ["TraderParserProfile", "UniversalParserRuntime", "parse"]
