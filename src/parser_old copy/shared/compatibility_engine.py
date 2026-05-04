from __future__ import annotations

from pydantic import BaseModel, Field

from src.parser.canonical_v1.intent_taxonomy import IntentName
from src.parser.shared.intent_compatibility_schema import IntentCompatibilityPair


class CompatibilityResult(BaseModel):
    requires_local_resolution: bool = False
    requires_context_validation: bool = False
    conflicting_pairs: list[IntentCompatibilityPair] = Field(default_factory=list)
    resolved: bool = False


def evaluate_intent_compatibility(
    detected: list[IntentName],
    pairs: list[IntentCompatibilityPair],
) -> CompatibilityResult:
    detected_set = set(detected)
    conflicting_pairs: list[IntentCompatibilityPair] = []
    requires_local_resolution = False
    requires_context_validation = False

    for pair in pairs:
        if not set(pair.intents).issubset(detected_set):
            continue

        requires_local_resolution = requires_local_resolution or pair.requires_resolution
        requires_context_validation = (
            requires_context_validation or pair.requires_context_validation
        )

        if pair.requires_resolution or pair.requires_context_validation:
            conflicting_pairs.append(pair)

    return CompatibilityResult(
        requires_local_resolution=requires_local_resolution,
        requires_context_validation=requires_context_validation,
        conflicting_pairs=conflicting_pairs,
    )

