from __future__ import annotations

from src.parser_v2.contracts.context import TargetHints
from src.parser_v2.contracts.parsed_message import ParsedIntent, ParsedMessage
from src.parser_v2.contracts.rules import ConvergenceRules, ParserRules


class SemanticNormalizer:
    def normalize(self, parsed: ParsedMessage, rules: ParserRules) -> ParsedMessage:
        convergence = rules.convergence
        if not convergence.intent and not convergence.scope_hint:
            return parsed

        new_intents = [_remap_intent(i, convergence) for i in parsed.intents]
        new_primary = (
            convergence.intent.get(parsed.primary_intent, parsed.primary_intent)
            if parsed.primary_intent is not None
            else None
        )
        new_target_hints = _remap_target_hints(parsed.target_hints, convergence.scope_hint)

        return parsed.model_copy(update={
            "intents": new_intents,
            "primary_intent": new_primary,
            "target_hints": new_target_hints,
        })


def _remap_intent(intent: ParsedIntent, convergence: ConvergenceRules) -> ParsedIntent:
    new_type = convergence.intent.get(intent.type, intent.type)
    new_hints = _remap_target_hints(intent.target_hints, convergence.scope_hint)

    updates: dict[str, object] = {}
    if new_type != intent.type:
        updates["type"] = new_type
    if new_hints is not intent.target_hints:
        updates["target_hints"] = new_hints

    if not updates:
        return intent
    return intent.model_copy(update=updates)


def _remap_target_hints(hints: TargetHints | None, scope_map: dict[str, str]) -> TargetHints | None:
    if hints is None or not scope_map:
        return hints
    new_scope = scope_map.get(hints.scope_hint, hints.scope_hint)
    if new_scope == hints.scope_hint:
        return hints
    return hints.model_copy(update={"scope_hint": new_scope})


__all__ = ["SemanticNormalizer"]
