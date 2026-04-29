from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.parser.canonical_v1.models import Targeting
from src.parser.intent_validator.history_provider import (
    HistoryProvider,
    SQLiteHistoryProvider,
)
from src.parser.parsed_message import IntentResult, ParsedMessage


class HistoryBackedIntentValidator:
    def __init__(
        self,
        db_path: str | None = None,
        rules_path: Path | None = None,
        history_provider: HistoryProvider | None = None,
    ) -> None:
        self._rules = _load_rules(rules_path or Path(__file__).with_name("validation_rules.json"))
        if history_provider is not None:
            self._history_provider = history_provider
        else:
            if not db_path:
                raise ValueError("db_path is required when history_provider is not provided")
            self._history_provider = SQLiteHistoryProvider(db_path=db_path)

    def validate(self, parsed: ParsedMessage) -> ParsedMessage:
        validated_intents = [self._validate_intent(parsed, intent) for intent in parsed.intents]
        return parsed.model_copy(
            update={
                "intents": validated_intents,
                "validation_status": "VALIDATED",
            }
        )

    def _validate_intent(self, parsed: ParsedMessage, intent: IntentResult) -> IntentResult:
        rule = self._rules.get(intent.type.value)
        targeting = intent.targeting_override or parsed.targeting
        refs = _extract_message_refs(targeting)
        scope_kind = targeting.scope.kind if targeting is not None else None

        if rule is None:
            return _confirmed_intent(intent, refs if scope_kind == "SINGLE_SIGNAL" else [])

        if not refs or scope_kind != "SINGLE_SIGNAL":
            return _confirmed_intent(intent, [])

        valid_refs: list[int] = []
        invalid_refs: list[int] = []
        for ref in refs:
            lifecycle = self._history_provider.get_signal_lifecycle(
                ref_message_id=ref,
                source_chat_id=parsed.raw_context.source_chat_id,
            )
            if _rule_matches(rule, lifecycle.ordered_history):
                valid_refs.append(ref)
            else:
                invalid_refs.append(ref)

        if valid_refs:
            return intent.model_copy(
                update={
                    "status": "CONFIRMED",
                    "valid_refs": valid_refs,
                    "invalid_refs": invalid_refs,
                    "invalid_reason": rule["invalid_reason"] if invalid_refs else None,
                }
            )

        return intent.model_copy(
            update={
                "status": "INVALID",
                "valid_refs": [],
                "invalid_refs": invalid_refs,
                "invalid_reason": rule["invalid_reason"],
            }
        )


def _load_rules(path: Path) -> dict[str, dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rules = data.get("rules", [])
    return {
        str(rule["intent"]): rule
        for rule in rules
        if isinstance(rule, dict) and "intent" in rule and "invalid_reason" in rule
    }


def _extract_message_refs(targeting: Targeting | None) -> list[int]:
    if targeting is None:
        return []
    refs: list[int] = []
    for ref in targeting.refs:
        if ref.ref_type != "MESSAGE_ID":
            continue
        try:
            refs.append(int(ref.value))
        except (TypeError, ValueError):
            continue
    return refs


def _rule_matches(rule: dict[str, Any], history: list[str]) -> bool:
    history_set = set(history)

    requires_all = {str(item) for item in rule.get("requires_all_history", [])}
    if requires_all and not requires_all.issubset(history_set):
        return False

    requires_any = {str(item) for item in rule.get("requires_any_history", [])}
    if requires_any and history_set.isdisjoint(requires_any):
        return False

    excludes_any = {str(item) for item in rule.get("excludes_any_history", [])}
    if excludes_any and not history_set.isdisjoint(excludes_any):
        return False

    excludes_all = {str(item) for item in rule.get("excludes_all_history", [])}
    if excludes_all and excludes_all.issubset(history_set):
        return False

    return True


def _confirmed_intent(intent: IntentResult, valid_refs: list[int]) -> IntentResult:
    return intent.model_copy(
        update={
            "status": "CONFIRMED",
            "valid_refs": valid_refs,
            "invalid_refs": [],
            "invalid_reason": None,
        }
    )
