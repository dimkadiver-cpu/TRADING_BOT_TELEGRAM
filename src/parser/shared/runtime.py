from __future__ import annotations

from typing import Any, Protocol

from src.parser.canonical_v1.models import RawContext, TargetRef, TargetScope, Targeting
from src.parser.intent_types import IntentType
from src.parser.parsed_message import InfoOnlyEntities, IntentResult, ParsedMessage
from src.parser.rules_engine import RulesEngine
from src.parser.trader_profiles.base import ParserContext
from src.parser.trader_profiles.shared.targeting import extract_targets

_REPORT_INTENTS = {
    IntentType.ENTRY_FILLED,
    IntentType.TP_HIT,
    IntentType.SL_HIT,
    IntentType.EXIT_BE,
    IntentType.REPORT_PARTIAL_RESULT,
    IntentType.REPORT_FINAL_RESULT,
}


class ExtractorProtocol(Protocol):
    def extract(self, text: str, context: ParserContext, rules: RulesEngine) -> dict[str, Any]:
        ...


def parse(
    *,
    trader_code: str,
    text: str,
    context: ParserContext,
    rules: RulesEngine,
    extractors: ExtractorProtocol,
) -> ParsedMessage:
    classification = rules.classify(text)
    detections = {match.intent: match for match in rules.detect_intents_with_evidence(text)}
    extracted = extractors.extract(text, context, rules) or {}

    intents = _build_intents(extracted.get("intents") or [], detections)
    if not intents and classification.message_type == "INFO_ONLY":
        intents = [
            IntentResult(
                type=IntentType.INFO_ONLY,
                category="INFO",
                entities=InfoOnlyEntities(),
                confidence=classification.confidence,
            )
        ]

    parsed = ParsedMessage(
        parser_profile=trader_code,
        primary_class=_select_primary_class(classification.message_type, intents, extracted.get("signal")),
        parse_status=_select_parse_status(
            classification.message_type,
            extracted.get("signal"),
            intents,
            extracted.get("parse_status"),
        ),
        confidence=classification.confidence,
        composite=len({intent.category for intent in intents}) > 1,
        signal=extracted.get("signal"),
        intents=intents,
        primary_intent=_select_primary_intent(intents, rules),
        targeting=extracted.get("targeting") or _build_message_targeting(text, context),
        warnings=list(extracted.get("warnings") or []),
        diagnostics={
            "trader_code": trader_code,
            "resolution_unit": _resolution_unit(intents),
            **(extracted.get("diagnostics") or {}),
        },
        raw_context=_build_raw_context(context),
    )
    return parsed


def _build_intents(raw_intents: list[Any], detections: dict[str, Any]) -> list[IntentResult]:
    intents: list[IntentResult] = []
    for item in raw_intents:
        if isinstance(item, IntentResult):
            intent = item.model_copy(deep=True)
        else:
            payload = dict(item)
            intent_type = IntentType(payload["type"])
            payload.setdefault("category", _category_for_intent(intent_type))
            payload.setdefault("confidence", 0.0)
            intent = IntentResult.model_validate(payload)
        detected = detections.get(intent.type.value)
        if detected is not None:
            intent = intent.model_copy(
                update={
                    "detection_strength": detected.strength,
                    "category": _category_for_intent(intent.type),
                }
            )
        else:
            intent = intent.model_copy(update={"category": _category_for_intent(intent.type)})
        intents.append(intent)
    return intents


def _category_for_intent(intent_type: IntentType) -> str:
    if intent_type in _REPORT_INTENTS:
        return "REPORT"
    if intent_type == IntentType.INFO_ONLY:
        return "INFO"
    return "UPDATE"


def _select_primary_class(message_type: str, intents: list[IntentResult], signal: Any) -> str:
    if signal is not None or message_type == "NEW_SIGNAL":
        return "SIGNAL"
    categories = {intent.category for intent in intents}
    if "UPDATE" in categories:
        return "UPDATE"
    if "REPORT" in categories:
        return "REPORT"
    return "INFO"


def _select_parse_status(
    message_type: str,
    signal: Any,
    intents: list[IntentResult],
    requested: str | None,
) -> str:
    if requested is not None:
        return requested
    if message_type == "UNCLASSIFIED" and signal is None and not intents:
        return "UNCLASSIFIED"
    return "PARSED"


def _select_primary_intent(intents: list[IntentResult], rules: RulesEngine) -> IntentType | None:
    if not intents:
        return None
    precedence = rules.raw_rules.get("primary_intent_precedence")
    if isinstance(precedence, list):
        by_name = {intent.type.value: intent.type for intent in intents}
        for name in precedence:
            if name in by_name:
                return by_name[name]
    return intents[0].type


def _build_message_targeting(text: str, context: ParserContext) -> Targeting | None:
    raw_refs = extract_targets(
        reply_to_message_id=context.reply_to_message_id,
        text=text,
        extracted_links=context.extracted_links,
    )
    refs = [
        TargetRef(ref_type=raw_ref.kind, value=raw_ref.value)
        for raw_ref in raw_refs
        if raw_ref.kind in {"REPLY", "TELEGRAM_LINK", "MESSAGE_ID", "EXPLICIT_ID", "SYMBOL"}
        and raw_ref.value is not None
    ]
    if not refs:
        return None
    return Targeting(
        refs=refs,
        scope=TargetScope(kind="SINGLE_SIGNAL"),
        strategy="REPLY_OR_LINK",
        targeted=True,
    )


def _resolution_unit(intents: list[IntentResult]) -> str:
    if any(intent.targeting_override is not None for intent in intents):
        return "TARGET_ITEM_WIDE"
    return "MESSAGE_WIDE"


def _build_raw_context(context: ParserContext) -> RawContext:
    return RawContext(
        raw_text=context.raw_text,
        reply_to_message_id=context.reply_to_message_id,
        extracted_links=list(context.extracted_links),
        hashtags=list(context.hashtags),
        source_chat_id=context.channel_id,
    )
