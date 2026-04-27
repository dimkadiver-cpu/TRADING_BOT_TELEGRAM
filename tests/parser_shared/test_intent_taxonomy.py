"""Tests for shared intent taxonomy: official intents, aliases, precedences, mutual exclusions."""

from __future__ import annotations

import pytest

from src.parser.trader_profiles.shared.intent_taxonomy import (
    OFFICIAL_INTENTS,
    LEGACY_ALIASES,
    PRIMARY_INTENT_PRECEDENCE,
    MUTUAL_EXCLUSIONS,
    COMPATIBLE_MULTI_INTENT,
    resolve_alias,
    normalize_intents,
    select_primary_intent,
)


class TestOfficialIntents:
    def test_all_expected_intents_present(self) -> None:
        expected = {
            "NEW_SETUP",
            "MOVE_STOP_TO_BE",
            "MOVE_STOP",
            "CLOSE_FULL",
            "CLOSE_PARTIAL",
            "CANCEL_PENDING_ORDERS",
            "INVALIDATE_SETUP",
            "REENTER",
            "ADD_ENTRY",
            "UPDATE_TAKE_PROFITS",
            "ENTRY_FILLED",
            "TP_HIT",
            "SL_HIT",
            "EXIT_BE",
            "REPORT_FINAL_RESULT",
            "REPORT_PARTIAL_RESULT",
            "INFO_ONLY",
        }
        assert expected == set(OFFICIAL_INTENTS)

    def test_no_duplicates_in_official_intents(self) -> None:
        assert len(OFFICIAL_INTENTS) == len(set(OFFICIAL_INTENTS))

    def test_no_legacy_prefixes_in_official_intents(self) -> None:
        for intent in OFFICIAL_INTENTS:
            assert not intent.startswith("U_"), f"{intent} has legacy U_ prefix"
            assert not intent.startswith("NS_"), f"{intent} has legacy NS_ prefix"


class TestLegacyAliases:
    def test_legacy_ns_create_signal_maps_to_new_setup(self) -> None:
        assert LEGACY_ALIASES["NS_CREATE_SIGNAL"] == "NEW_SETUP"

    def test_legacy_u_stop_hit_maps_to_sl_hit(self) -> None:
        assert LEGACY_ALIASES["U_STOP_HIT"] == "SL_HIT"

    def test_legacy_u_tp_hit_maps_to_tp_hit(self) -> None:
        assert LEGACY_ALIASES["U_TP_HIT"] == "TP_HIT"

    def test_legacy_u_mark_filled_maps_to_entry_filled(self) -> None:
        assert LEGACY_ALIASES["U_MARK_FILLED"] == "ENTRY_FILLED"

    def test_legacy_u_move_stop_to_be_maps_correctly(self) -> None:
        assert LEGACY_ALIASES["U_MOVE_STOP_TO_BE"] == "MOVE_STOP_TO_BE"

    def test_legacy_u_move_stop_maps_correctly(self) -> None:
        assert LEGACY_ALIASES["U_MOVE_STOP"] == "MOVE_STOP"

    def test_legacy_u_close_full_maps_correctly(self) -> None:
        assert LEGACY_ALIASES["U_CLOSE_FULL"] == "CLOSE_FULL"

    def test_legacy_u_close_partial_maps_correctly(self) -> None:
        assert LEGACY_ALIASES["U_CLOSE_PARTIAL"] == "CLOSE_PARTIAL"

    def test_legacy_u_cancel_pending_orders_maps_correctly(self) -> None:
        assert LEGACY_ALIASES["U_CANCEL_PENDING_ORDERS"] == "CANCEL_PENDING_ORDERS"

    def test_legacy_u_invalidate_setup_maps_correctly(self) -> None:
        assert LEGACY_ALIASES["U_INVALIDATE_SETUP"] == "INVALIDATE_SETUP"

    def test_legacy_u_update_take_profits_maps_correctly(self) -> None:
        assert LEGACY_ALIASES["U_UPDATE_TAKE_PROFITS"] == "UPDATE_TAKE_PROFITS"

    def test_legacy_u_exit_be_maps_correctly(self) -> None:
        assert LEGACY_ALIASES["U_EXIT_BE"] == "EXIT_BE"

    def test_legacy_u_report_final_result_maps_correctly(self) -> None:
        assert LEGACY_ALIASES["U_REPORT_FINAL_RESULT"] == "REPORT_FINAL_RESULT"

    def test_all_alias_targets_are_official_intents(self) -> None:
        for legacy, official in LEGACY_ALIASES.items():
            assert official in OFFICIAL_INTENTS, (
                f"Alias {legacy!r} -> {official!r} but {official!r} is not in OFFICIAL_INTENTS"
            )

    def test_no_official_intent_is_aliased_to_itself_as_legacy(self) -> None:
        for legacy in LEGACY_ALIASES:
            assert legacy not in OFFICIAL_INTENTS, (
                f"{legacy!r} appears as both a legacy alias key and an official intent"
            )

    def test_u_risk_note_not_in_taxonomy(self) -> None:
        assert "U_RISK_NOTE" not in LEGACY_ALIASES
        assert "U_RISK_NOTE" not in OFFICIAL_INTENTS

    def test_u_reverse_signal_not_in_taxonomy(self) -> None:
        assert "U_REVERSE_SIGNAL" not in LEGACY_ALIASES
        assert "U_REVERSE_SIGNAL" not in OFFICIAL_INTENTS


class TestResolveAlias:
    def test_legacy_intent_resolves_to_official(self) -> None:
        assert resolve_alias("U_STOP_HIT") == "SL_HIT"

    def test_official_intent_is_identity(self) -> None:
        assert resolve_alias("SL_HIT") == "SL_HIT"
        assert resolve_alias("NEW_SETUP") == "NEW_SETUP"

    def test_unknown_intent_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="UNKNOWN_INTENT"):
            resolve_alias("UNKNOWN_INTENT")

    def test_resolve_alias_ns_create_signal(self) -> None:
        assert resolve_alias("NS_CREATE_SIGNAL") == "NEW_SETUP"

    def test_u_risk_note_raises(self) -> None:
        with pytest.raises(ValueError, match="U_RISK_NOTE"):
            resolve_alias("U_RISK_NOTE")

    def test_u_reverse_signal_raises(self) -> None:
        with pytest.raises(ValueError, match="U_REVERSE_SIGNAL"):
            resolve_alias("U_REVERSE_SIGNAL")


class TestNormalizeIntents:
    def test_normalizes_list_of_legacy_intents(self) -> None:
        result = normalize_intents(["U_STOP_HIT", "U_MARK_FILLED"])
        assert result == ["SL_HIT", "ENTRY_FILLED"]

    def test_already_official_intents_pass_through(self) -> None:
        result = normalize_intents(["SL_HIT", "TP_HIT"])
        assert result == ["SL_HIT", "TP_HIT"]

    def test_deduplicates_intents(self) -> None:
        result = normalize_intents(["U_STOP_HIT", "SL_HIT"])
        assert result.count("SL_HIT") == 1

    def test_preserves_order_after_dedup(self) -> None:
        result = normalize_intents(["U_MARK_FILLED", "U_STOP_HIT"])
        assert result == ["ENTRY_FILLED", "SL_HIT"]

    def test_empty_list_returns_empty(self) -> None:
        assert normalize_intents([]) == []

    def test_unknown_intent_raises(self) -> None:
        with pytest.raises(ValueError):
            normalize_intents(["TOTALLY_UNKNOWN"])


class TestPrimaryIntentPrecedence:
    def test_precedence_list_contains_only_official_intents(self) -> None:
        for intent in PRIMARY_INTENT_PRECEDENCE:
            assert intent in OFFICIAL_INTENTS, f"{intent!r} not in OFFICIAL_INTENTS"

    def test_sl_hit_outranks_move_stop(self) -> None:
        sl_rank = PRIMARY_INTENT_PRECEDENCE.index("SL_HIT")
        ms_rank = PRIMARY_INTENT_PRECEDENCE.index("MOVE_STOP")
        assert sl_rank < ms_rank, "SL_HIT should have higher precedence than MOVE_STOP"

    def test_new_setup_outranks_info_only(self) -> None:
        ns_rank = PRIMARY_INTENT_PRECEDENCE.index("NEW_SETUP")
        io_rank = PRIMARY_INTENT_PRECEDENCE.index("INFO_ONLY")
        assert ns_rank < io_rank

    def test_close_full_outranks_close_partial(self) -> None:
        cf_rank = PRIMARY_INTENT_PRECEDENCE.index("CLOSE_FULL")
        cp_rank = PRIMARY_INTENT_PRECEDENCE.index("CLOSE_PARTIAL")
        assert cf_rank < cp_rank

    def test_report_final_outranks_report_partial(self) -> None:
        rf_rank = PRIMARY_INTENT_PRECEDENCE.index("REPORT_FINAL_RESULT")
        rp_rank = PRIMARY_INTENT_PRECEDENCE.index("REPORT_PARTIAL_RESULT")
        assert rf_rank < rp_rank


class TestSelectPrimaryIntent:
    def test_selects_highest_precedence_intent(self) -> None:
        result = select_primary_intent(["MOVE_STOP", "SL_HIT"])
        assert result == "SL_HIT"

    def test_single_intent_returns_that_intent(self) -> None:
        assert select_primary_intent(["CLOSE_FULL"]) == "CLOSE_FULL"

    def test_empty_list_returns_none(self) -> None:
        assert select_primary_intent([]) is None

    def test_all_inputs_must_be_official(self) -> None:
        with pytest.raises(ValueError):
            select_primary_intent(["U_STOP_HIT"])  # legacy, not normalized


class TestMutualExclusions:
    def test_new_setup_excluded_from_action_intents(self) -> None:
        excluded_with_new_setup = MUTUAL_EXCLUSIONS.get("NEW_SETUP", set())
        assert "SL_HIT" in excluded_with_new_setup or "CLOSE_FULL" in excluded_with_new_setup

    def test_mutual_exclusion_keys_are_official_intents(self) -> None:
        for intent in MUTUAL_EXCLUSIONS:
            assert intent in OFFICIAL_INTENTS

    def test_mutual_exclusion_values_are_official_intents(self) -> None:
        for intent, excluded_set in MUTUAL_EXCLUSIONS.items():
            for ex in excluded_set:
                assert ex in OFFICIAL_INTENTS, f"Exclusion value {ex!r} for {intent!r} not official"


class TestCompatibleMultiIntent:
    def test_compatible_multi_intent_keys_are_official(self) -> None:
        for intent in COMPATIBLE_MULTI_INTENT:
            assert intent in OFFICIAL_INTENTS

    def test_compatible_multi_intent_values_are_official(self) -> None:
        for intent, compatible_set in COMPATIBLE_MULTI_INTENT.items():
            for c in compatible_set:
                assert c in OFFICIAL_INTENTS, f"{c!r} not in OFFICIAL_INTENTS"

    def test_sl_hit_compatible_with_close_full(self) -> None:
        compatible = COMPATIBLE_MULTI_INTENT.get("SL_HIT", set())
        assert "CLOSE_FULL" in compatible
