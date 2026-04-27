"""Validation of parsing_rules.json files against the common schema.

Validation is implemented in pure Python (no jsonschema dependency) to keep
the dependency footprint minimal. The companion rules_schema.json serves as
a reference document for the intended shape.
"""

from __future__ import annotations

from src.parser.trader_profiles.shared.intent_taxonomy import (
    LEGACY_ALIASES,
    _OFFICIAL_SET,
)

_KNOWN_INTENT_KEYS: frozenset[str] = _OFFICIAL_SET | frozenset(LEGACY_ALIASES.keys())


class RulesValidationError(ValueError):
    """Raised by validate_rules(..., strict=True) when there are validation errors."""


def validate_rules(data: dict, *, strict: bool = False) -> list[str]:
    """Validate a parsed parsing_rules.json dict against the common schema.

    Returns a list of human-readable error strings.
    If *strict* is True, raises RulesValidationError on the first error instead.
    """
    errors: list[str] = []

    def _add(msg: str) -> None:
        if strict:
            raise RulesValidationError(msg)
        errors.append(msg)

    if not isinstance(data, dict):
        _add("Root must be a JSON object (dict)")
        return errors

    # --- Required fields ---
    if "classification_markers" not in data:
        _add("Missing required key: 'classification_markers'")
    else:
        cm = data["classification_markers"]
        if not isinstance(cm, dict):
            _add("'classification_markers' must be an object")
        elif "new_signal" not in cm:
            _add("'classification_markers' must contain 'new_signal'")

    # --- intent_markers shape ---
    if "intent_markers" in data:
        im = data["intent_markers"]
        if not isinstance(im, dict):
            _add("'intent_markers' must be an object")
        else:
            for key, value in im.items():
                if key not in _KNOWN_INTENT_KEYS:
                    _add(
                        f"'intent_markers' contains unknown key {key!r}. "
                        "Must be an official intent or a known legacy alias."
                    )
                # Accept both flat list (legacy shape) and strong/weak dict (new shape).
                # Flat list is tolerated during FASE 1–3; FASE 4 migration will tighten this.
                if isinstance(value, list):
                    pass  # legacy flat list — accepted during transition
                elif isinstance(value, dict):
                    for sub in ("strong", "weak"):
                        if sub not in value:
                            _add(f"'intent_markers[{key!r}]' missing required key '{sub}'")
                        elif not isinstance(value[sub], list):
                            _add(
                                f"'intent_markers[{key!r}][{sub!r}]' must be a list, "
                                f"got {type(value[sub]).__name__}"
                            )
                else:
                    _add(
                        f"'intent_markers[{key!r}]' must be a list or an object with 'strong'/'weak' lists, "
                        f"got {type(value).__name__}"
                    )

    return errors
