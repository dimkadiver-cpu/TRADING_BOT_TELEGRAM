"""Tests for shared entity key vocabulary."""

from __future__ import annotations

from src.parser.trader_profiles.shared.entity_keys import (
    SIGNAL_KEYS,
    UPDATE_KEYS,
    REPORT_KEYS,
    INSTRUMENT_KEYS,
    ALL_RAW_BLOCK_KEYS,
)


class TestSignalKeys:
    def test_required_signal_keys_present(self) -> None:
        assert "entry_structure" in SIGNAL_KEYS
        assert "entries" in SIGNAL_KEYS
        assert "stop_loss" in SIGNAL_KEYS
        assert "take_profits" in SIGNAL_KEYS

    def test_signal_keys_has_no_duplicates(self) -> None:
        assert len(SIGNAL_KEYS) == len(set(SIGNAL_KEYS))


class TestUpdateKeys:
    def test_required_update_sub_blocks_present(self) -> None:
        assert "stop_update" in UPDATE_KEYS
        assert "close_update" in UPDATE_KEYS
        assert "cancel_update" in UPDATE_KEYS
        assert "entry_update" in UPDATE_KEYS
        assert "targets_update" in UPDATE_KEYS

    def test_update_keys_has_no_duplicates(self) -> None:
        assert len(UPDATE_KEYS) == len(set(UPDATE_KEYS))


class TestReportKeys:
    def test_required_report_keys_present(self) -> None:
        assert "events" in REPORT_KEYS
        assert "reported_results" in REPORT_KEYS

    def test_report_keys_has_no_duplicates(self) -> None:
        assert len(REPORT_KEYS) == len(set(REPORT_KEYS))


class TestInstrumentKeys:
    def test_required_instrument_keys_present(self) -> None:
        assert "symbol" in INSTRUMENT_KEYS
        assert "side" in INSTRUMENT_KEYS
        assert "market_type" in INSTRUMENT_KEYS

    def test_instrument_keys_has_no_duplicates(self) -> None:
        assert len(INSTRUMENT_KEYS) == len(set(INSTRUMENT_KEYS))


class TestAllRawBlockKeys:
    def test_includes_all_sections(self) -> None:
        for key in SIGNAL_KEYS:
            assert key in ALL_RAW_BLOCK_KEYS
        for key in UPDATE_KEYS:
            assert key in ALL_RAW_BLOCK_KEYS
        for key in REPORT_KEYS:
            assert key in ALL_RAW_BLOCK_KEYS
        for key in INSTRUMENT_KEYS:
            assert key in ALL_RAW_BLOCK_KEYS

    def test_no_duplicates(self) -> None:
        assert len(ALL_RAW_BLOCK_KEYS) == len(set(ALL_RAW_BLOCK_KEYS))
