from __future__ import annotations

from src.parser.trader_profiles.shared.rules_schema import (
    validate_profile_rules,
    validate_semantic_markers,
)


def test_phase3_semantic_markers_fixture_validates() -> None:
    payload = {
        "language": "ru",
        "number_format": {
            "decimal_separator": ".",
            "thousands_separator": " ",
        },
        "blacklist": [],
        "classification_markers": {
            "new_signal": {"strong": ["entry"], "weak": []},
            "update": {"strong": [], "weak": []},
            "info_only": {"strong": [], "weak": []},
        },
        "field_markers": {
            "entry": {"strong": ["entry"], "weak": []},
        },
        "intent_markers": {
            "MOVE_STOP_TO_BE": {"strong": ["breakeven"], "weak": []},
            "INFO_ONLY": {"strong": ["market update"], "weak": []},
        },
        "side_markers": {"long": ["long"], "short": ["short"]},
        "entry_type_markers": {"market": ["market"], "limit": ["limit"]},
        "target_markers": {
            "telegram_link": [],
            "explicit_id": [],
            "pronouns": [],
        },
        "global_target_markers": {
            "ALL_LONGS": [],
            "ALL_SHORTS": [],
            "ALL_POSITIONS": [],
            "ALL_OPEN": [],
            "ALL_REMAINING": [],
        },
        "symbol_aliases": {},
        "extraction_markers": {
            "risk_prefix": {"strong": [], "weak": []},
            "risk_suffix": {"strong": [], "weak": []},
            "leverage_prefix": {"strong": [], "weak": []},
        },
    }

    assert validate_semantic_markers(payload) == []


def test_phase3_rules_fixture_with_nested_disambiguation_validates() -> None:
    payload = {
        "classification_rules": [
            {
                "name": "signal_when_fields",
                "when_all_fields_present": ["entry", "stop_loss", "take_profit"],
                "then": "new_signal",
                "score": 1.0,
            }
        ],
        "combination_rules": [
            {
                "name": "boost_complete_signal",
                "when_all_fields_present": ["entry", "stop_loss"],
                "then": "complete_signal",
                "confidence_boost": 0.2,
            }
        ],
        "primary_intent_precedence": ["MOVE_STOP_TO_BE", "MOVE_STOP", "INFO_ONLY"],
        "disambiguation_rules": {
            "rules": [
                {
                    "name": "prefer_be",
                    "action": "prefer",
                    "priority": 1,
                    "conditions": {
                        "intents": {"strong": ["MOVE_STOP_TO_BE"], "weak": ["MOVE_STOP"]},
                        "text": {"any": ["breakeven"], "none": []},
                        "message": {"composite": False, "has_targeting": None},
                        "entities": {"present": [], "absent": []},
                    },
                    "prefer": "MOVE_STOP_TO_BE",
                    "over": ["MOVE_STOP"],
                }
            ]
        },
        "action_scope_groups": {
            "ALL_POSITIONS": ["ALL_POSITIONS", "ALL_OPEN"],
        },
    }

    assert validate_profile_rules(payload) == []
