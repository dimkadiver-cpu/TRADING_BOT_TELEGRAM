from __future__ import annotations

from dataclasses import dataclass

from src.parser_v2.contracts.context import TargetHints
from src.parser_v2.contracts.enums import INTENT_CATEGORY_BY_TYPE, MessageClass, ParseStatus
from src.parser_v2.contracts.parsed_message import ParsedIntent, SignalDraft


UPDATE_WITHOUT_TARGET_HINT = "update_without_target_hint"
SIGNAL_LIKE_UPDATE_FORCED = "signal_like_update_forced_to_update"

_UPDATE_INTENT_TYPES = frozenset({
    "MODIFY_ENTRY", "MOVE_STOP", "MOVE_STOP_TO_BE", "CANCEL_PENDING",
    "CLOSE_FULL", "CLOSE_PARTIAL", "MODIFY_TARGETS", "INVALIDATE_SETUP",
    "REENTER", "ADD_ENTRY",
})


@dataclass(frozen=True)
class ClassificationResult:
    primary_class: MessageClass
    parse_status: ParseStatus
    warnings: list[str]


class ClassificationResolver:
    def resolve(
        self,
        *,
        signal: SignalDraft | None,
        intents: list[ParsedIntent],
        target_hints: TargetHints | None = None,
    ) -> ClassificationResult:
        if signal is not None:
            parse_status = _signal_parse_status(signal)
            if parse_status == "PARTIAL" and _looks_like_targeted_update(intents, target_hints):
                return ClassificationResult(
                    primary_class="UPDATE",
                    parse_status="PARSED",
                    warnings=[SIGNAL_LIKE_UPDATE_FORCED],
                )
            return ClassificationResult(
                primary_class="SIGNAL",
                parse_status=parse_status,
                warnings=[],
            )

        categories = [_intent_category(intent) for intent in intents]

        if "UPDATE" in categories:
            warnings = []
            if not _has_target_hint(target_hints):
                warnings.append(UPDATE_WITHOUT_TARGET_HINT)
            return ClassificationResult(
                primary_class="UPDATE",
                parse_status="PARSED",
                warnings=warnings,
            )

        if "REPORT" in categories:
            return ClassificationResult(
                primary_class="REPORT",
                parse_status="PARSED",
                warnings=[],
            )

        if "INFO" in categories:
            return ClassificationResult(
                primary_class="INFO",
                parse_status="PARSED",
                warnings=[],
            )

        return ClassificationResult(
            primary_class="INFO",
            parse_status="UNCLASSIFIED",
            warnings=[],
        )


def _signal_parse_status(signal: SignalDraft) -> ParseStatus:
    if signal.completeness == "COMPLETE" and not signal.missing_fields:
        return "PARSED"
    return "PARTIAL"


def _intent_category(intent: ParsedIntent) -> str:
    return INTENT_CATEGORY_BY_TYPE.get(intent.type, intent.category)


def _has_target_hint(target_hints: TargetHints | None) -> bool:
    if target_hints is None:
        return False

    return any(
        [
            target_hints.reply_to_message_id is not None,
            bool(target_hints.telegram_message_ids),
            bool(target_hints.telegram_links),
            bool(target_hints.explicit_ids),
            bool(target_hints.symbols),
            target_hints.scope_hint != "UNKNOWN",
        ]
    )


def _looks_like_targeted_update(
    intents: list[ParsedIntent],
    target_hints: TargetHints | None,
) -> bool:
    has_update_intent = any(i.type in _UPDATE_INTENT_TYPES for i in intents)
    return has_update_intent and _has_target_hint(target_hints)
