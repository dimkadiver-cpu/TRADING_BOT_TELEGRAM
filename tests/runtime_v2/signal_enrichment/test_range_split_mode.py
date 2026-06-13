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
        result, structure, derivation, logs = SignalEnrichmentProcessor._apply_range_split(legs, "endpoints")
        assert result[0].price.value == 64_000.0
        assert result[1].price.value == 65_000.0
        assert structure == "TWO_STEP"
        assert derivation is not None
        assert logs[0].check == "range_endpoints_retained"

    def test_endpoints_returns_same_list(self):
        legs = _make_range_legs(64_000.0, 65_000.0)
        result, _, _, _ = SignalEnrichmentProcessor._apply_range_split(legs, "endpoints")
        assert result is legs

    def test_endpoints_orders_long_from_higher_to_lower_price(self):
        legs = _make_range_legs(64_000.0, 65_000.0)
        result, structure, derivation, logs = SignalEnrichmentProcessor._apply_range_split(
            legs, "endpoints", side="LONG"
        )
        assert [leg.price.value for leg in result] == [65_000.0, 64_000.0]
        assert [leg.sequence for leg in result] == [1, 2]
        assert structure == "TWO_STEP"
        assert derivation is not None
        assert logs[0].check == "range_endpoints_retained"

    def test_endpoints_orders_short_from_lower_to_higher_price(self):
        legs = _make_range_legs(64_000.0, 65_000.0)
        result, structure, derivation, logs = SignalEnrichmentProcessor._apply_range_split(
            legs, "endpoints", side="SHORT"
        )
        assert [leg.price.value for leg in result] == [64_000.0, 65_000.0]
        assert [leg.sequence for leg in result] == [1, 2]
        assert structure == "TWO_STEP"
        assert derivation is not None
        assert logs[0].check == "range_endpoints_retained"

    def test_firstpoint_collapses_to_single_leg(self):
        legs = _make_range_legs(64_000.0, 65_000.0)
        result, structure, derivation, logs = SignalEnrichmentProcessor._apply_range_split(legs, "firstpoint")
        assert len(result) == 1
        assert result[0].price.value == 64_000.0
        assert result[0].sequence == 1
        assert result[0].weight == 1.0
        assert structure == "ONE_SHOT"
        assert derivation is not None
        assert logs[0].detail == "firstpoint"

    def test_lastpoint_collapses_to_single_leg(self):
        legs = _make_range_legs(64_000.0, 65_000.0)
        result, structure, derivation, logs = SignalEnrichmentProcessor._apply_range_split(legs, "lastpoint")
        assert len(result) == 1
        assert result[0].price.value == 65_000.0
        assert structure == "ONE_SHOT"
        assert derivation is not None
        assert logs[0].detail == "lastpoint"

    def test_midpoint_collapses_to_single_leg(self):
        legs = _make_range_legs(64_000.0, 65_000.0)
        result, structure, derivation, logs = SignalEnrichmentProcessor._apply_range_split(legs, "midpoint")
        assert len(result) == 1
        assert result[0].price.value == 64_500.0
        assert result[0].sequence == 1
        assert result[0].weight == 1.0
        assert structure == "ONE_SHOT"
        assert derivation is not None
        assert logs[0].detail == "midpoint"

    def test_firstpoint_uses_first_authored_leg_price_for_reversed_range(self):
        legs = _make_range_legs(65_000.0, 64_000.0)
        result, structure, derivation, logs = SignalEnrichmentProcessor._apply_range_split(legs, "firstpoint")
        assert len(result) == 1
        assert result[0].price.value == 65_000.0
        assert structure == "ONE_SHOT"
        assert derivation is not None
        assert derivation.original_min_price == 64_000.0
        assert derivation.original_max_price == 65_000.0
        assert logs[0].detail == "firstpoint"

    def test_lastpoint_uses_last_authored_leg_price_for_reversed_range(self):
        legs = _make_range_legs(65_000.0, 64_000.0)
        result, structure, derivation, logs = SignalEnrichmentProcessor._apply_range_split(legs, "lastpoint")
        assert len(result) == 1
        assert result[0].price.value == 64_000.0
        assert structure == "ONE_SHOT"
        assert derivation is not None
        assert derivation.original_min_price == 64_000.0
        assert derivation.original_max_price == 65_000.0
        assert logs[0].detail == "lastpoint"

    def test_midpoint_uses_min_max_bounds_for_reversed_range(self):
        legs = _make_range_legs(65_000.0, 64_000.0)
        result, structure, derivation, logs = SignalEnrichmentProcessor._apply_range_split(legs, "midpoint")
        assert len(result) == 1
        assert result[0].price.value == 64_500.0
        assert structure == "ONE_SHOT"
        assert derivation is not None
        assert derivation.original_min_price == 64_000.0
        assert derivation.original_max_price == 65_000.0
        assert logs[0].detail == "midpoint"

    def test_midpoint_odd_value_rounded(self):
        legs = _make_range_legs(64_000.0, 65_001.0)
        result, _, _, _ = SignalEnrichmentProcessor._apply_range_split(legs, "midpoint")
        expected = round((64_000.0 + 65_001.0) / 2, 8)
        assert result[0].price.value == expected

    def test_price_raw_updated_consistently(self):
        legs = _make_range_legs(64_000.0, 65_000.0)
        result, _, _, _ = SignalEnrichmentProcessor._apply_range_split(legs, "firstpoint")
        assert result[0].price.raw == "64000.0"

    def test_collapsed_modes_reset_weight_to_full_allocation(self):
        legs = _make_range_legs(64_000.0, 65_000.0)
        legs[0] = legs[0].model_copy(update={"weight": 0.6})
        legs[1] = legs[1].model_copy(update={"weight": 0.4})
        result, _, _, _ = SignalEnrichmentProcessor._apply_range_split(legs, "midpoint")
        assert result[0].weight == 1.0

    def test_collapsed_modes_keep_first_sequence(self):
        legs = _make_range_legs(64_000.0, 65_000.0)
        result, _, _, _ = SignalEnrichmentProcessor._apply_range_split(legs, "lastpoint")
        assert result[0].sequence == 1

    def test_unknown_split_mode_returns_unchanged(self):
        legs = _make_range_legs(64_000.0, 65_000.0)
        result, structure, derivation, logs = SignalEnrichmentProcessor._apply_range_split(legs, "unknown_mode")
        assert result[0].price.value == 64_000.0
        assert result[1].price.value == 65_000.0
        assert structure == "RANGE"
        assert derivation is None
        assert logs == []

    def test_single_leg_returns_unchanged(self):
        legs = _make_range_legs(64_000.0, 65_000.0)[:1]
        result, structure, derivation, logs = SignalEnrichmentProcessor._apply_range_split(legs, "firstpoint")
        assert result is legs
        assert structure == "ONE_SHOT"
        assert derivation is None
        assert logs == []

    def test_single_priced_leg_preserves_range_anomaly_without_derivation(self):
        legs = [
            EnrichedEntryLeg(sequence=1, entry_type="LIMIT", price=None, weight=0.5),
            EnrichedEntryLeg(
                sequence=2, entry_type="LIMIT",
                price=Price(raw="65000", value=65_000.0), weight=0.5
            ),
        ]
        result, structure, derivation, logs = SignalEnrichmentProcessor._apply_range_split(legs, "firstpoint")
        assert result is legs
        assert result[0].price is None
        assert result[1].price.value == 65_000.0
        assert structure == "RANGE"
        assert derivation is None
        assert logs == []

    def test_all_legs_no_price_returns_unchanged(self):
        legs = [
            EnrichedEntryLeg(sequence=1, entry_type="MARKET", price=None, weight=0.5),
            EnrichedEntryLeg(sequence=2, entry_type="MARKET", price=None, weight=0.5),
        ]
        result, structure, derivation, logs = SignalEnrichmentProcessor._apply_range_split(legs, "midpoint")
        assert result is legs
        assert structure == "RANGE"
        assert derivation is None
        assert logs == []


class TestRangeSplitIntegration:
    """Integration tests: split_mode applied through full _apply_entry_weights."""

    def _make_signal(self, split_mode: str, price1: float, price2: float, *, side: str = "LONG"):
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
            side=side,
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

    @pytest.mark.parametrize("split_mode,price1,price2,expected_structure,expected_prices", [
        ("endpoints", 64_000.0, 65_000.0, "TWO_STEP", [65_000.0, 64_000.0]),
        ("firstpoint", 64_000.0, 65_000.0, "ONE_SHOT", [64_000.0]),
        ("lastpoint", 64_000.0, 65_000.0, "ONE_SHOT", [65_000.0]),
        ("midpoint", 64_000.0, 65_000.0, "ONE_SHOT", [64_500.0]),
    ])
    def test_split_mode_via_apply_entry_weights(
        self, split_mode, price1, price2, expected_structure, expected_prices
    ):
        from unittest.mock import MagicMock
        processor = SignalEnrichmentProcessor(
            config_loader=MagicMock(),
            repository=MagicMock(),
        )
        signal = self._make_signal(split_mode, price1, price2)
        config = self._make_config(split_mode)
        result, structure, derivation, logs = processor._apply_entry_weights(signal, config)

        assert structure == expected_structure
        assert [leg.price.value for leg in result] == expected_prices
        assert derivation is not None
        assert derivation.split_mode == split_mode
        assert logs

    @pytest.mark.parametrize(
        "side, expected_prices",
        [
            ("LONG", [65_000.0, 64_000.0]),
            ("SHORT", [64_000.0, 65_000.0]),
        ],
    )
    def test_endpoints_via_apply_entry_weights_orders_by_side(self, side, expected_prices):
        from unittest.mock import MagicMock

        processor = SignalEnrichmentProcessor(
            config_loader=MagicMock(),
            repository=MagicMock(),
        )
        signal = self._make_signal("endpoints", 64_000.0, 65_000.0, side=side)
        config = self._make_config("endpoints")

        result, structure, derivation, logs = processor._apply_entry_weights(signal, config)

        assert structure == "TWO_STEP"
        assert [leg.price.value for leg in result] == expected_prices
        assert [leg.sequence for leg in result] == [1, 2]
        assert derivation is not None
        assert derivation.split_mode == "endpoints"
        assert logs

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
        result, structure, derivation, logs = processor._apply_entry_weights(signal, config)
        assert result[0].price.value == 65_000.0
        assert structure == "ONE_SHOT"
        assert derivation is None
        assert logs == []

    @pytest.mark.parametrize(
        "split_mode, price1, price2, expected_price",
        [
            ("midpoint", 64_000.0, 65_000.0, 64_500.0),
            ("firstpoint", 65_000.0, 64_000.0, 65_000.0),
            ("lastpoint", 65_000.0, 64_000.0, 64_000.0),
        ],
    )
    def test_process_signal_normalizes_range_collapsed_modes_to_one_shot(
        self,
        split_mode,
        price1,
        price2,
        expected_price,
    ):
        import datetime
        from unittest.mock import MagicMock

        from src.parser_v2.contracts.canonical_message import CanonicalMessage, SignalPayload
        from src.parser_v2.contracts.context import RawContext
        from src.parser_v2.contracts.entities import EntryLeg, Price, StopLoss, TakeProfit
        from src.runtime_v2.parser_pipeline.models import CanonicalParseResult

        processor = SignalEnrichmentProcessor(config_loader=MagicMock(), repository=MagicMock())
        config = self._make_config(split_mode)
        processor._config.get_effective_config.return_value = config
        processor._config.get_symbol_blacklist_global.return_value = set()
        processor._config.get_symbol_blacklist_for_trader.return_value = set()
        processor._config.get_policy_version.return_value = "test"
        processor._repo.get_by_canonical_message_id.return_value = None
        processor._repo.save.side_effect = lambda enriched: enriched.model_copy(update={"enrichment_id": 1})

        signal = SignalPayload(
            completeness="COMPLETE",
            symbol="BTCUSDT",
            side="LONG",
            entry_structure="RANGE",
            entries=[
                EntryLeg(sequence=1, entry_type="LIMIT", price=Price(raw=str(price1), value=price1)),
                EntryLeg(sequence=2, entry_type="LIMIT", price=Price(raw=str(price2), value=price2)),
            ],
            stop_loss=StopLoss(price=Price(raw="62000", value=62000.0)),
            take_profits=[TakeProfit(sequence=1, price=Price(raw="68000", value=68000.0))],
        )
        canonical_message = CanonicalMessage(
            parser_profile="trader_a",
            primary_class="SIGNAL",
            parse_status="PARSED",
            confidence=1.0,
            signal=signal,
            raw_context=RawContext(raw_text="test"),
        )
        parse_result = CanonicalParseResult(
            canonical_message_id=1,
            raw_message_id=1,
            parser_profile="trader_a",
            primary_class="SIGNAL",
            parse_status="PARSED",
            canonical_message=canonical_message,
            warnings=[],
            parsed_at=datetime.datetime.now(datetime.timezone.utc),
        )

        enriched = processor.process(parse_result)

        assert enriched.enriched_signal is not None
        assert enriched.enriched_signal.entry_structure == "ONE_SHOT"
        assert len(enriched.enriched_signal.entries) == 1
        assert enriched.enriched_signal.entries[0].price.value == expected_price
        assert enriched.enriched_signal.range_derivation is not None
        assert enriched.enriched_signal.range_derivation.split_mode == split_mode
        assert enriched.enriched_signal.range_derivation.original_min_price == 64_000.0
        assert enriched.enriched_signal.range_derivation.original_max_price == 65_000.0
        assert any(
            entry.check == "range_price_derived" and entry.detail == split_mode
            for entry in enriched.enrichment_log
        )

    def test_process_signal_normalizes_range_endpoints_to_two_step(self):
        import datetime
        from unittest.mock import MagicMock

        from src.parser_v2.contracts.canonical_message import CanonicalMessage, SignalPayload
        from src.parser_v2.contracts.context import RawContext
        from src.parser_v2.contracts.entities import EntryLeg, Price, StopLoss, TakeProfit
        from src.runtime_v2.parser_pipeline.models import CanonicalParseResult

        processor = SignalEnrichmentProcessor(config_loader=MagicMock(), repository=MagicMock())
        config = self._make_config("endpoints")
        processor._config.get_effective_config.return_value = config
        processor._config.get_symbol_blacklist_global.return_value = set()
        processor._config.get_symbol_blacklist_for_trader.return_value = set()
        processor._config.get_policy_version.return_value = "test"
        processor._repo.get_by_canonical_message_id.return_value = None
        processor._repo.save.side_effect = lambda enriched: enriched.model_copy(update={"enrichment_id": 2})

        signal = SignalPayload(
            completeness="COMPLETE",
            symbol="BTCUSDT",
            side="LONG",
            entry_structure="RANGE",
            entries=[
                EntryLeg(sequence=1, entry_type="LIMIT", price=Price(raw="64000", value=64000.0)),
                EntryLeg(sequence=2, entry_type="LIMIT", price=Price(raw="65000", value=65000.0)),
            ],
            stop_loss=StopLoss(price=Price(raw="62000", value=62000.0)),
            take_profits=[TakeProfit(sequence=1, price=Price(raw="68000", value=68000.0))],
        )
        canonical_message = CanonicalMessage(
            parser_profile="trader_a",
            primary_class="SIGNAL",
            parse_status="PARSED",
            confidence=1.0,
            signal=signal,
            raw_context=RawContext(raw_text="test"),
        )
        parse_result = CanonicalParseResult(
            canonical_message_id=2,
            raw_message_id=2,
            parser_profile="trader_a",
            primary_class="SIGNAL",
            parse_status="PARSED",
            canonical_message=canonical_message,
            warnings=[],
            parsed_at=datetime.datetime.now(datetime.timezone.utc),
        )

        enriched = processor.process(parse_result)

        assert enriched.enriched_signal is not None
        assert enriched.enriched_signal.entry_structure == "TWO_STEP"
        assert len(enriched.enriched_signal.entries) == 2
        assert [leg.price.value for leg in enriched.enriched_signal.entries] == [65_000.0, 64_000.0]
        assert enriched.enriched_signal.range_derivation is not None
        assert enriched.enriched_signal.range_derivation.split_mode == "endpoints"
        assert any(
            entry.check == "range_endpoints_retained" and entry.detail == "endpoints"
            for entry in enriched.enrichment_log
        )
