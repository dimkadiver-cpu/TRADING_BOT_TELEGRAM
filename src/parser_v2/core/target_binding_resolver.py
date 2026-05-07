from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.parser_v2.contracts.context import (
    TargetCandidate,
    TargetExtractionResult,
    TargetHints,
)
from src.parser_v2.contracts.parsed_message import ParsedIntent


@dataclass
class TargetBindingResult:
    intents: list[ParsedIntent]
    message_target_hints: TargetHints
    warnings: list[str] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)


class TargetBindingResolver:
    def bind(
        self,
        intents: list[ParsedIntent],
        extraction: TargetExtractionResult,
    ) -> TargetBindingResult:
        warnings: list[str] = []
        diagnostics: dict[str, Any] = {}

        message_hints = extraction.message_target_hints

        if (
            message_hints.target_source == "MESSAGE_TEXT_LINK"
            and message_hints.reply_to_message_id is not None
        ):
            diagnostics["ignored_reply_to_message_id"] = message_hints.reply_to_message_id

        positional = [c for c in extraction.candidates if c.line_index is not None]

        bound_intents, line_warnings = _bind_line_level(intents, positional)
        warnings.extend(line_warnings)

        return TargetBindingResult(
            intents=bound_intents,
            message_target_hints=message_hints,
            warnings=warnings,
            diagnostics=diagnostics,
        )


def _bind_line_level(
    intents: list[ParsedIntent],
    positional_candidates: list[TargetCandidate],
) -> tuple[list[ParsedIntent], list[str]]:
    warnings: list[str] = []

    candidates_by_line: dict[int, list[TargetCandidate]] = {}
    for c in positional_candidates:
        if c.line_index is not None:
            candidates_by_line.setdefault(c.line_index, []).append(c)

    intents_by_line: dict[int, list[int]] = {}
    for idx, intent in enumerate(intents):
        if intent.line_index is not None:
            intents_by_line.setdefault(intent.line_index, []).append(idx)

    updated = list(intents)

    for line_idx, intent_indices in intents_by_line.items():
        line_candidates = candidates_by_line.get(line_idx, [])
        n_cands = len(line_candidates)
        n_intents = len(intent_indices)

        if n_cands == 0:
            continue

        if n_cands == 1:
            hints = _hints_from_candidate(line_candidates[0])
            for i in intent_indices:
                updated[i] = updated[i].model_copy(update={"target_hints": hints})

        elif n_cands == n_intents:
            # Only bind one-to-one when all intents on this line are distinguishable
            # (unique occurrence_index). If any two share the same occurrence_index,
            # we cannot determine which link belongs to which intent → ambiguous.
            occurrence_indices = [updated[i].occurrence_index for i in intent_indices]
            has_duplicates = len(occurrence_indices) != len(set(occurrence_indices))
            if has_duplicates:
                warnings.append("ambiguous_target_intent_binding")
            else:
                sorted_cands = sorted(line_candidates, key=lambda c: c.start or 0)
                for i, cand in zip(intent_indices, sorted_cands):
                    hints = _hints_from_candidate(cand)
                    updated[i] = updated[i].model_copy(update={"target_hints": hints})

        elif n_intents == 1:
            all_ids = [c.value for c in line_candidates if isinstance(c.value, int)]
            hints = TargetHints(
                target_source="LOCAL_TEXT_LINK",
                telegram_message_ids=all_ids,
            )
            updated[intent_indices[0]] = updated[intent_indices[0]].model_copy(
                update={"target_hints": hints}
            )

        else:
            # N_cands != N_intents, both > 1 → ambiguous (D11)
            warnings.append("ambiguous_target_intent_binding")

    return updated, warnings


def _hints_from_candidate(candidate: TargetCandidate) -> TargetHints:
    source = candidate.source
    local_source = (
        "LOCAL_TEXT_LINK" if source == "MESSAGE_TEXT_LINK"
        else "LOCAL_EXPLICIT_ID" if source == "MESSAGE_EXPLICIT_ID"
        else source
    )
    if isinstance(candidate.value, int) and source in ("MESSAGE_TEXT_LINK", "LOCAL_TEXT_LINK"):
        return TargetHints(
            target_source=local_source,
            telegram_message_ids=[candidate.value],
        )
    if isinstance(candidate.value, str) and source in ("MESSAGE_EXPLICIT_ID", "LOCAL_EXPLICIT_ID"):
        return TargetHints(
            target_source=local_source,
            explicit_ids=[candidate.value],
        )
    return TargetHints(target_source=local_source)
