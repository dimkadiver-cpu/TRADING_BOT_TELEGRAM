"""Tests for shared warnings module: codes, uniqueness, importability."""

from __future__ import annotations

from src.parser.trader_profiles.shared.warnings import (
    WarningCode,
    ALL_WARNING_CODES,
    MISSING_TARGET,
    CONFLICTING_INTENTS,
    PARTIAL_SIGNAL,
    UNCLASSIFIED_WITH_MARKERS,
    UNKNOWN_INTENT_DETECTED,
)


class TestWarningCodes:
    def test_all_warning_codes_are_strings(self) -> None:
        for code in ALL_WARNING_CODES:
            assert isinstance(code, str), f"{code!r} is not a string"

    def test_no_duplicate_codes(self) -> None:
        assert len(ALL_WARNING_CODES) == len(set(ALL_WARNING_CODES))

    def test_known_codes_are_in_all_codes(self) -> None:
        assert MISSING_TARGET in ALL_WARNING_CODES
        assert CONFLICTING_INTENTS in ALL_WARNING_CODES
        assert PARTIAL_SIGNAL in ALL_WARNING_CODES
        assert UNCLASSIFIED_WITH_MARKERS in ALL_WARNING_CODES
        assert UNKNOWN_INTENT_DETECTED in ALL_WARNING_CODES

    def test_codes_use_snake_case(self) -> None:
        for code in ALL_WARNING_CODES:
            assert code == code.lower(), f"{code!r} is not snake_case"
            assert " " not in code, f"{code!r} contains spaces"

    def test_warning_code_type_is_str(self) -> None:
        assert WarningCode is str or WarningCode == str
