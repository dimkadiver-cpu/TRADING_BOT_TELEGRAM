"""Tests for rules_schema.json: validates parsing_rules.json files against common schema."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.parser.trader_profiles.shared.rules_schema import (
    validate_rules,
    validate_profile_rules,
    validate_semantic_markers,
    RulesValidationError,
)

_TRADER_A_SEMANTIC_MARKERS = Path("src/parser/trader_profiles/trader_a/semantic_markers.json")
_TRADER_A_PROFILE_RULES = Path("src/parser/trader_profiles/trader_a/rules.json")
_TEMPLATE_RULES = Path(
    "docs/in_progress/new_parser/organizzazione_comune/parsing_rules.template.treader_a.jsonc"
)


def _load_jsonc(path: Path) -> dict:
    """Load JSON with comments (strip // lines)."""
    lines = path.read_text(encoding="utf-8").splitlines()
    clean = "\n".join(
        line for line in lines if not line.strip().startswith("//")
    )
    return json.loads(clean)


class TestValidateTraderARules:
    def test_trader_a_split_rules_files_exist(self) -> None:
        assert _TRADER_A_SEMANTIC_MARKERS.exists(), f"Missing {_TRADER_A_SEMANTIC_MARKERS}"
        assert _TRADER_A_PROFILE_RULES.exists(), f"Missing {_TRADER_A_PROFILE_RULES}"

    def test_trader_a_split_rules_are_valid_json(self) -> None:
        semantic_markers = json.loads(_TRADER_A_SEMANTIC_MARKERS.read_text(encoding="utf-8"))
        profile_rules = json.loads(_TRADER_A_PROFILE_RULES.read_text(encoding="utf-8"))
        assert isinstance(semantic_markers, dict)
        assert isinstance(profile_rules, dict)

    def test_trader_a_rules_is_clean_post_migration(self) -> None:
        """trader_a now validates as split semantic markers + profile rules."""
        semantic_markers = json.loads(_TRADER_A_SEMANTIC_MARKERS.read_text(encoding="utf-8"))
        profile_rules = json.loads(_TRADER_A_PROFILE_RULES.read_text(encoding="utf-8"))
        errors = validate_semantic_markers(semantic_markers) + validate_profile_rules(profile_rules)
        assert not errors, f"Unexpected violations after migration: {errors}"


class TestValidateTemplateRules:
    def test_template_file_exists(self) -> None:
        assert _TEMPLATE_RULES.exists(), f"Missing {_TEMPLATE_RULES}"

    def test_template_validates_against_schema(self) -> None:
        data = _load_jsonc(_TEMPLATE_RULES)
        errors = validate_rules(data)
        assert errors == [], f"Validation errors: {errors}"


class TestSchemaRejectsInvalidRules:
    def test_missing_classification_markers_fails(self) -> None:
        data = json.loads(_TRADER_A_SEMANTIC_MARKERS.read_text(encoding="utf-8"))
        del data["classification_markers"]
        errors = validate_semantic_markers(data)
        assert len(errors) > 0

    def test_classification_markers_missing_new_signal_fails(self) -> None:
        data = json.loads(_TRADER_A_SEMANTIC_MARKERS.read_text(encoding="utf-8"))
        del data["classification_markers"]["new_signal"]
        errors = validate_semantic_markers(data)
        assert len(errors) > 0

    def test_intent_markers_with_unknown_key_fails(self) -> None:
        data = json.loads(_TRADER_A_SEMANTIC_MARKERS.read_text(encoding="utf-8"))
        if "intent_markers" not in data:
            data["intent_markers"] = {}
        data["intent_markers"]["TOTALLY_UNKNOWN_INTENT"] = {"strong": [], "weak": []}
        errors = validate_semantic_markers(data)
        assert len(errors) > 0

    def test_flat_intent_markers_list_is_rejected_after_migration(self) -> None:
        data = json.loads(_TRADER_A_SEMANTIC_MARKERS.read_text(encoding="utf-8"))
        data["intent_markers"] = {"SL_HIT": ["some marker"]}
        errors = validate_semantic_markers(data)
        assert len(errors) > 0

    def test_non_list_non_dict_intent_marker_value_fails(self) -> None:
        """A string value for an intent marker is invalid."""
        data = json.loads(_TRADER_A_SEMANTIC_MARKERS.read_text(encoding="utf-8"))
        data["intent_markers"] = {"SL_HIT": "not a list or dict"}
        errors = validate_semantic_markers(data)
        assert len(errors) > 0


class TestRulesValidationError:
    def test_raises_on_critical_schema_error(self) -> None:
        with pytest.raises(RulesValidationError):
            validate_rules({}, strict=True)
