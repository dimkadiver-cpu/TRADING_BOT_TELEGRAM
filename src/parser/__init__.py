from __future__ import annotations

from typing import Any, Protocol

from src.intent_translator import IntentTranslator as CanonicalIntentTranslator
from src.parser.canonical_v1.models import CanonicalMessage
from src.parser.intent_validator import HistoryBackedIntentValidator
from src.parser.parsed_message import ParsedMessage
from src.parser.shared.disambiguation import apply_disambiguation_rules
from src.parser.trader_profiles.base import ParserContext


class IntentValidator(Protocol):
    def validate(self, parsed: ParsedMessage) -> ParsedMessage: ...


class IntentTranslator(Protocol):
    def translate(self, parsed: ParsedMessage) -> CanonicalMessage: ...


class DisambiguationEngine(Protocol):
    def apply(self, parsed: ParsedMessage, *, profile: Any) -> ParsedMessage: ...


class PassthroughIntentValidator:
    """Conservative Fasa 4.5 bridge.

    The real history-backed validator is implemented in Fasa 5. Until then we
    keep the ParsedMessage untouched and persist its current validation state.
    """

    def validate(self, parsed: ParsedMessage) -> ParsedMessage:
        return parsed


class ProfileRulesDisambiguationEngine:
    def apply(self, parsed: ParsedMessage, *, profile: Any) -> ParsedMessage:
        rules = _extract_disambiguation_rules(profile)
        if not rules:
            return parsed
        return apply_disambiguation_rules(parsed_message=parsed, rules=rules).parsed_message


class ProfileCanonicalMessageTranslator(CanonicalIntentTranslator):
    """Backward-compatible alias for the dedicated Fasa 6 translator."""


def parse_message(
    text: str,
    context: ParserContext,
    profile: Any,
    validator: IntentValidator,
    translator: IntentTranslator,
    disambiguation_engine: DisambiguationEngine,
) -> tuple[ParsedMessage, CanonicalMessage]:
    parse_profile = getattr(profile, "parse", None)
    if not callable(parse_profile):
        raise TypeError("profile does not expose parse() for ParsedMessage flow")

    parsed = parse_profile(text, context)
    parsed = validator.validate(parsed)
    parsed = disambiguation_engine.apply(parsed, profile=profile)
    canonical = translator.translate(parsed)
    return parsed, canonical


def _extract_disambiguation_rules(profile: Any) -> list[dict[str, Any]]:
    rules_engine = getattr(profile, "_phase4_rules_engine", None)
    if rules_engine is not None:
        raw_rules = getattr(rules_engine, "raw_rules", {})
        disambiguation_rules = raw_rules.get("disambiguation_rules", {})
        if isinstance(disambiguation_rules, dict):
            nested = disambiguation_rules.get("rules", [])
            if isinstance(nested, list):
                return nested
        if isinstance(disambiguation_rules, list):
            return disambiguation_rules
    return []


__all__ = [
    "DisambiguationEngine",
    "HistoryBackedIntentValidator",
    "IntentTranslator",
    "IntentValidator",
    "PassthroughIntentValidator",
    "ProfileCanonicalMessageTranslator",
    "ProfileRulesDisambiguationEngine",
    "parse_message",
]
