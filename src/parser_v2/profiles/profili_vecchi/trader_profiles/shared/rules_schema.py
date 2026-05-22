"""Validation of parsing_rules.json files against the common schema.

Validation is implemented in pure Python (no jsonschema dependency) to keep
the dependency footprint minimal. The companion rules_schema.json serves as
a reference document for the intended shape.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.parser.intent_types import IntentType
from src.parser.shared.disambiguation_rules_schema import DisambiguationRulesBlock
from src.parser.trader_profiles.shared.intent_taxonomy import (
    LEGACY_ALIASES,
    _OFFICIAL_SET,
)

_KNOWN_INTENT_KEYS: frozenset[str] = (
    _OFFICIAL_SET
    | frozenset(LEGACY_ALIASES.keys())
    | frozenset(intent.value for intent in IntentType)
)
_RULES_DIR = Path(__file__).resolve().parent


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


def validate_semantic_markers(data: dict, *, strict: bool = False) -> list[str]:
    errors: list[str] = []

    def _add(msg: str) -> None:
        if strict:
            raise RulesValidationError(msg)
        errors.append(msg)

    if not isinstance(data, dict):
        _add("Root must be a JSON object (dict)")
        return errors

    classification = data.get("classification_markers")
    if not isinstance(classification, dict):
        _add("Missing or invalid 'classification_markers'")
    else:
        for required in ("new_signal", "update", "info_only"):
            if required not in classification:
                _add(f"'classification_markers' missing required key {required!r}")
            else:
                _validate_marker_group(classification[required], f"classification_markers.{required}", _add)

    for section_name in ("field_markers", "extraction_markers"):
        section = data.get(section_name, {})
        if section is not None and not isinstance(section, dict):
            _add(f"'{section_name}' must be an object")
        elif isinstance(section, dict):
            for key, value in section.items():
                _validate_marker_group(value, f"{section_name}.{key}", _add)

    intent_markers = data.get("intent_markers", {})
    if intent_markers is not None and not isinstance(intent_markers, dict):
        _add("'intent_markers' must be an object")
    elif isinstance(intent_markers, dict):
        for key, value in intent_markers.items():
            if key not in _KNOWN_INTENT_KEYS:
                _add(f"'intent_markers' contains unknown key {key!r}")
            _validate_marker_group(value, f"intent_markers.{key}", _add)

    for section_name in ("side_markers", "entry_type_markers", "target_markers", "global_target_markers"):
        section = data.get(section_name)
        if section is not None and not isinstance(section, dict):
            _add(f"'{section_name}' must be an object")

    return errors


def validate_profile_rules(data: dict, *, strict: bool = False) -> list[str]:
    errors: list[str] = []

    def _add(msg: str) -> None:
        if strict:
            raise RulesValidationError(msg)
        errors.append(msg)

    if not isinstance(data, dict):
        _add("Root must be a JSON object (dict)")
        return errors

    classification_rules = data.get("classification_rules", [])
    if classification_rules is not None and not isinstance(classification_rules, list):
        _add("'classification_rules' must be a list")
    elif isinstance(classification_rules, list):
        for idx, rule in enumerate(classification_rules):
            if not isinstance(rule, dict):
                _add(f"classification_rules[{idx}] must be an object")
                continue
            for required in ("name", "when_all_fields_present", "then"):
                if required not in rule:
                    _add(f"classification_rules[{idx}] missing required key {required!r}")

    if "primary_intent_precedence" in data:
        precedence = data["primary_intent_precedence"]
        if not isinstance(precedence, list):
            _add("'primary_intent_precedence' must be a list")
        else:
            for item in precedence:
                if not isinstance(item, str) or item not in _KNOWN_INTENT_KEYS:
                    _add(f"'primary_intent_precedence' contains unknown intent {item!r}")

    if "disambiguation_rules" in data:
        try:
            DisambiguationRulesBlock.model_validate(data["disambiguation_rules"])
        except Exception as exc:
            _add(f"Invalid 'disambiguation_rules': {exc}")

    if "action_scope_groups" in data and not isinstance(data["action_scope_groups"], dict):
        _add("'action_scope_groups' must be an object")

    return errors


def load_reference_schema(name: str) -> dict:
    return json.loads((_RULES_DIR / name).read_text(encoding="utf-8"))


def _validate_marker_group(value: object, path: str, add_error) -> None:
    if not isinstance(value, dict):
        add_error(f"'{path}' must be an object with 'strong'/'weak' lists")
        return
    for key in ("strong", "weak"):
        if key not in value:
            add_error(f"'{path}' missing required key {key!r}")
        elif not isinstance(value[key], list):
            add_error(f"'{path}.{key}' must be a list")
