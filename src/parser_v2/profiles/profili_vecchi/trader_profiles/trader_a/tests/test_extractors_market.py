"""Unit tests for _extract_entries() in trader_a/extractors.py — MARKET entry constraint."""
from __future__ import annotations

import pytest

from src.parser.trader_profiles.trader_a.extractors import _extract_entries


class TestExtractEntriesMarket:
    def test_market_with_price_creates_market_leg(self) -> None:
        entries = _extract_entries("вход с текущих 90000")
        assert len(entries) == 1
        assert entries[0].entry_type == "MARKET"
        assert entries[0].price is not None
        assert entries[0].price.value == 90000.0

    def test_market_without_price_creates_market_leg_price_none(self) -> None:
        """Bug fix: MARKET marker сenza numero non deve cadere nel path LIMIT."""
        entries = _extract_entries("вход с текущих")
        assert len(entries) == 1
        assert entries[0].entry_type == "MARKET"
        assert entries[0].price is None

    def test_market_without_price_in_signal_context(self) -> None:
        """MARKET marker con sl/tp ma senza prezzo entry."""
        entries = _extract_entries("вход с текущих\nSL: 89000\nTP1: 93000")
        assert len(entries) == 1
        assert entries[0].entry_type == "MARKET"
        assert entries[0].price is None

    def test_market_marker_does_not_override_with_limit_detection(self) -> None:
        """Quando MARKET marker è presente, primary_type non deve diventare LIMIT."""
        entries = _extract_entries("вход с текущих sl: 89000")
        assert len(entries) == 1
        assert entries[0].entry_type == "MARKET"

    def test_limit_with_price_creates_limit_leg(self) -> None:
        entries = _extract_entries("entry: 90000")
        assert len(entries) == 1
        assert entries[0].entry_type == "LIMIT"
        assert entries[0].price is not None
        assert entries[0].price.value == 90000.0

    def test_no_entry_marker_returns_empty(self) -> None:
        entries = _extract_entries("SL: 89000 TP1: 93000")
        assert len(entries) == 0

    def test_market_leg_sequence_is_1(self) -> None:
        entries = _extract_entries("вход с текущих")
        assert entries[0].sequence == 1

    def test_market_leg_role_is_primary(self) -> None:
        entries = _extract_entries("вход с текущих")
        assert entries[0].role == "PRIMARY"

    def test_market_leg_is_not_optional(self) -> None:
        entries = _extract_entries("вход с текущих")
        assert entries[0].is_optional is False
