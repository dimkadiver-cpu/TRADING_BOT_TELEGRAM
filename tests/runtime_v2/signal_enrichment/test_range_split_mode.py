# tests/runtime_v2/signal_enrichment/test_range_split_mode.py
from __future__ import annotations

import pytest

from src.runtime_v2.signal_enrichment.models import EnrichedEntryLeg
from src.runtime_v2.signal_enrichment.processor import SignalEnrichmentProcessor
from src.parser_v2.contracts.entities import Price


def _make_range_legs(price1: float, price2: float) -> list[EnrichedEntryLeg]:
    return [
        EnrichedEntryLeg(
            sequence=1,
            entry_type="LIMIT",
            price=Price(raw=str(price1), value=price1),
            weight=0.5,
        ),
        EnrichedEntryLeg(
            sequence=2,
            entry_type="LIMIT",
            price=Price(raw=str(price2), value=price2),
            weight=0.5,
        ),
    ]


class TestApplyRangeSplit:
    """Unit tests for SignalEnrichmentProcessor._apply_range_split."""

    def test_endpoints_preserves_original_prices(self):
        legs = _make_range_legs(64_000.0, 65_000.0)
        result = SignalEnrichmentProcessor._apply_range_split(legs, "endpoints")
        assert result[0].price.value == 64_000.0
        assert result[1].price.value == 65_000.0

    def test_endpoints_returns_same_list(self):
        legs = _make_range_legs(64_000.0, 65_000.0)
        result = SignalEnrichmentProcessor._apply_range_split(legs, "endpoints")
        assert result is legs

    def test_firstpoint_sets_both_legs_to_min(self):
        legs = _make_range_legs(64_000.0, 65_000.0)
        result = SignalEnrichmentProcessor._apply_range_split(legs, "firstpoint")
        assert result[0].price.value == 64_000.0
        assert result[1].price.value == 64_000.0

    def test_lastpoint_sets_both_legs_to_max(self):
        legs = _make_range_legs(64_000.0, 65_000.0)
        result = SignalEnrichmentProcessor._apply_range_split(legs, "lastpoint")
        assert result[0].price.value == 65_000.0
        assert result[1].price.value == 65_000.0

    def test_midpoint_sets_both_legs_to_midpoint(self):
        legs = _make_range_legs(64_000.0, 65_000.0)
        result = SignalEnrichmentProcessor._apply_range_split(legs, "midpoint")
        assert result[0].price.value == 64_500.0
        assert result[1].price.value == 64_500.0

    def test_midpoint_odd_value_rounded(self):
        legs = _make_range_legs(64_000.0, 65_001.0)
        result = SignalEnrichmentProcessor._apply_range_split(legs, "midpoint")
        expected = round((64_000.0 + 65_001.0) / 2, 8)
        assert result[0].price.value == expected
        assert result[1].price.value == expected

    def test_price_raw_updated_consistently(self):
        legs = _make_range_legs(64_000.0, 65_000.0)
        result = SignalEnrichmentProcessor._apply_range_split(legs, "firstpoint")
        assert result[0].price.raw == "64000.0"
        assert result[1].price.raw == "64000.0"

    def test_weights_preserved_after_split(self):
        legs = _make_range_legs(64_000.0, 65_000.0)
        legs[0] = legs[0].model_copy(update={"weight": 0.6})
        legs[1] = legs[1].model_copy(update={"weight": 0.4})
        result = SignalEnrichmentProcessor._apply_range_split(legs, "midpoint")
        assert result[0].weight == 0.6
        assert result[1].weight == 0.4

    def test_sequence_preserved_after_split(self):
        legs = _make_range_legs(64_000.0, 65_000.0)
        result = SignalEnrichmentProcessor._apply_range_split(legs, "lastpoint")
        assert result[0].sequence == 1
        assert result[1].sequence == 2

    def test_unknown_split_mode_returns_unchanged(self):
        legs = _make_range_legs(64_000.0, 65_000.0)
        result = SignalEnrichmentProcessor._apply_range_split(legs, "unknown_mode")
        assert result[0].price.value == 64_000.0
        assert result[1].price.value == 65_000.0

    def test_single_leg_returns_unchanged(self):
        legs = _make_range_legs(64_000.0, 65_000.0)[:1]
        result = SignalEnrichmentProcessor._apply_range_split(legs, "firstpoint")
        assert result is legs

    def test_legs_with_no_price_skipped(self):
        legs = [
            EnrichedEntryLeg(sequence=1, entry_type="MARKET", price=None, weight=0.5),
            EnrichedEntryLeg(
                sequence=2, entry_type="LIMIT",
                price=Price(raw="65000", value=65_000.0), weight=0.5
            ),
        ]
        result = SignalEnrichmentProcessor._apply_range_split(legs, "firstpoint")
        assert result[0].price is None
        assert result[1].price.value == 65_000.0

    def test_all_legs_no_price_returns_unchanged(self):
        legs = [
            EnrichedEntryLeg(sequence=1, entry_type="MARKET", price=None, weight=0.5),
            EnrichedEntryLeg(sequence=2, entry_type="MARKET", price=None, weight=0.5),
        ]
        result = SignalEnrichmentProcessor._apply_range_split(legs, "midpoint")
        assert result is legs


class TestRangeSplitIntegration:
    """Integration tests: split_mode applied through full _apply_entry_weights."""

    def _make_signal(self, split_mode: str, price1: float, price2: float):
        """Build a minimal signal object with RANGE structure."""
        from src.parser_v2.contracts.entities import EntryLeg, Price, StopLoss, TakeProfit
        from types import SimpleNamespace

        entries = [
            EntryLeg(sequence=1, entry_type="LIMIT", price=Price(raw=str(price1), value=price1)),
            EntryLeg(sequence=2, entry_type="LIMIT", price=Price(raw=str(price2), value=price2)),
        ]
        signal = SimpleNamespace(
            entry_structure="RANGE",
            entries=entries,
            stop_loss=StopLoss(price=Price(raw="62000", value=62_000.0)),
            take_profits=[TakeProfit(sequence=1, price=Price(raw="68000", value=68_000.0))],
            symbol="BTCUSDT",
            side="LONG",
        )
        return signal

    def _make_config(self, split_mode: str):
        from src.runtime_v2.signal_enrichment.models import (
            EffectiveEnrichmentConfig, SignalPolicyConfig,
            MarketExecutionConfig, EntrySplitConfig,
            LimitEntrySplitConfig, MarketEntrySplitConfig,
            EntryWeightsConfig, EntryRangeConfig,
            TpConfig, SlConfig, PriceCorrectionsConfig, PriceSanityConfig,
            ManagementPlanConfig, RiskConfig, CloseDistributionConfig,
        )
        entry_split = EntrySplitConfig(
            LIMIT=LimitEntrySplitConfig(
                single=EntryWeightsConfig(weights={"E1": 1.0}),
                range=EntryRangeConfig(split_mode=split_mode, weights={"E1": 0.5, "E2": 0.5}),
                averaging=EntryWeightsConfig(weights={"E1": 0.7, "E2": 0.3}),
                ladder=EntryWeightsConfig(weights={"E1": 0.5, "E2": 0.3, "E3": 0.2}),
            ),
            MARKET=MarketEntrySplitConfig(
                single=EntryWeightsConfig(weights={"E1": 1.0}),
                averaging=EntryWeightsConfig(weights={"E1": 0.7, "E2": 0.3}),
            ),
        )
        policy = SignalPolicyConfig(
            accepted_entry_structures=["RANGE"],
            market_execution=MarketExecutionConfig(),
            entry_split=entry_split,
            tp=TpConfig(),
            sl=SlConfig(),
            price_corrections=PriceCorrectionsConfig(),
            price_sanity=PriceSanityConfig(),
        )
        from src.runtime_v2.signal_enrichment.models import AccountConfig
        account = AccountConfig(
            id="main", capital_base_usdt=10000.0,
            max_leverage=5, max_capital_at_risk_pct=100.0,
            hard_max_per_signal_risk_pct=2.0,
        )
        return EffectiveEnrichmentConfig(
            trader_id="trader_a",
            enabled=True,
            gate_mode="block",
            hedge_mode=False,
            account_id="main",
            signal_policy=policy,
            update_admission={},
            management_plan=ManagementPlanConfig(
                close_distribution=CloseDistributionConfig(table={1: [100]}),
            ),
            risk=RiskConfig(),
            account=account,
        )

    @pytest.mark.parametrize("split_mode,price1,price2,expected_e1,expected_e2", [
        ("endpoints",   64_000.0, 65_000.0, 64_000.0, 65_000.0),
        ("firstpoint",  64_000.0, 65_000.0, 64_000.0, 64_000.0),
        ("lastpoint",   64_000.0, 65_000.0, 65_000.0, 65_000.0),
        ("midpoint",    64_000.0, 65_000.0, 64_500.0, 64_500.0),
    ])
    def test_split_mode_via_apply_entry_weights(
        self, split_mode, price1, price2, expected_e1, expected_e2
    ):
        from unittest.mock import MagicMock
        processor = SignalEnrichmentProcessor(
            config_loader=MagicMock(),
            repository=MagicMock(),
        )
        signal = self._make_signal(split_mode, price1, price2)
        config = self._make_config(split_mode)
        result = processor._apply_entry_weights(signal, config)

        assert len(result) == 2
        assert result[0].price.value == expected_e1, f"{split_mode}: E1 expected {expected_e1}"
        assert result[1].price.value == expected_e2, f"{split_mode}: E2 expected {expected_e2}"

    def test_non_range_structure_not_affected(self):
        """ONE_SHOT LIMIT should never go through _apply_range_split."""
        from unittest.mock import MagicMock
        from src.parser_v2.contracts.entities import EntryLeg, Price
        from types import SimpleNamespace

        processor = SignalEnrichmentProcessor(
            config_loader=MagicMock(),
            repository=MagicMock(),
        )
        signal = SimpleNamespace(
            entry_structure="ONE_SHOT",
            entries=[EntryLeg(sequence=1, entry_type="LIMIT",
                              price=Price(raw="65000", value=65_000.0))],
        )
        config = self._make_config("firstpoint")
        result = processor._apply_entry_weights(signal, config)
        assert result[0].price.value == 65_000.0
