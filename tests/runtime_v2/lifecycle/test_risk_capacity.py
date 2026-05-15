from __future__ import annotations

from datetime import datetime

import pytest

from src.parser_v2.contracts.entities import Price, StopLoss, TakeProfit
from src.parser_v2.contracts.enums import EntryType
from src.runtime_v2.lifecycle.models import TradeChain
from src.runtime_v2.lifecycle.ports import AccountStateSnapshot, SymbolMarketSnapshot
from src.runtime_v2.lifecycle.risk_capacity import RiskCapacityEngine
from src.runtime_v2.signal_enrichment.models import (
    AccountConfig,
    CloseDistributionConfig,
    EffectiveEnrichmentConfig,
    EnrichedCanonicalMessage,
    EnrichedEntryLeg,
    EnrichedSignalPayload,
    EntrySplitConfig,
    EntryRangeConfig,
    EntryWeightsConfig,
    LimitEntrySplitConfig,
    ManagementPlanConfig,
    MarketEntrySplitConfig,
    MarketExecutionConfig,
    PriceCorrectionsConfig,
    PriceSanityConfig,
    RiskConfig,
    SignalPolicyConfig,
    SlConfig,
    TpConfig,
)


# ── helpers ────────────────────────────────────────────────────────────────────

def _make_price(value: float) -> Price:
    return Price(raw=str(value), value=value)


def _make_policy_snapshot(
    capital_base_usdt: float = 1000.0,
    risk_pct: float = 1.0,
    capital_base_mode: str = "static_config",
    max_concurrent_trades: int = 5,
    max_concurrent_same_symbol: int = 1,
) -> dict:
    entry_weights = EntryWeightsConfig(weights={"1": 1.0})
    entry_range = EntryRangeConfig(weights={"1": 0.5, "2": 0.5})
    config = EffectiveEnrichmentConfig(
        trader_id="trader_a",
        enabled=True,
        gate_mode="block",
        hedge_mode=False,
        account_id="acc1",
        signal_policy=SignalPolicyConfig(
            accepted_entry_structures=["ONE_SHOT", "TWO_STEP", "RANGE", "LADDER"],
            market_execution=MarketExecutionConfig(),
            entry_split=EntrySplitConfig(
                LIMIT=LimitEntrySplitConfig(
                    single=entry_weights,
                    range=entry_range,
                    averaging=entry_weights,
                    ladder=entry_weights,
                ),
                MARKET=MarketEntrySplitConfig(
                    single=entry_weights,
                    averaging=entry_weights,
                ),
            ),
            tp=TpConfig(),
            sl=SlConfig(),
            price_corrections=PriceCorrectionsConfig(),
            price_sanity=PriceSanityConfig(),
        ),
        update_admission={},
        management_plan=ManagementPlanConfig(),
        risk=RiskConfig(
            mode="risk_pct_of_capital",
            risk_pct_of_capital=risk_pct,
            capital_base_mode=capital_base_mode,
            capital_base_usdt=capital_base_usdt,
            leverage=1,
            max_concurrent_trades=max_concurrent_trades,
            max_concurrent_same_symbol=max_concurrent_same_symbol,
        ),
    )
    return config.model_dump()


def _make_enriched(
    *,
    trader_id: str = "trader_a",
    enrichment_id: int = 1,
    symbol: str = "BTC/USDT",
    side: str = "LONG",
    entry_type: str = "LIMIT",
    entry_price: float = 50000.0,
    sl_price: float = 49000.0,
    tp_prices: list[float] | None = None,
    capital_base_usdt: float = 1000.0,
    risk_pct: float = 1.0,
    capital_base_mode: str = "static_config",
    max_concurrent_trades: int = 5,
    max_concurrent_same_symbol: int = 1,
) -> EnrichedCanonicalMessage:
    if tp_prices is None:
        tp_prices = [51000.0]

    entry_leg = EnrichedEntryLeg(
        sequence=1,
        entry_type=entry_type,
        price=_make_price(entry_price) if entry_type == "LIMIT" else None,
        role="PRIMARY",
        weight=1.0,
    )
    take_profits = [
        TakeProfit(sequence=i + 1, price=_make_price(p))
        for i, p in enumerate(tp_prices)
    ]
    stop_loss = StopLoss(price=_make_price(sl_price)) if sl_price is not None else None

    enriched_signal = EnrichedSignalPayload(
        symbol=symbol,
        side=side,
        entry_structure="ONE_SHOT",
        entries=[entry_leg],
        take_profits=take_profits,
        stop_loss=stop_loss,
    )

    policy_snapshot = _make_policy_snapshot(
        capital_base_usdt=capital_base_usdt,
        risk_pct=risk_pct,
        capital_base_mode=capital_base_mode,
        max_concurrent_trades=max_concurrent_trades,
        max_concurrent_same_symbol=max_concurrent_same_symbol,
    )

    return EnrichedCanonicalMessage(
        enrichment_id=enrichment_id,
        canonical_message_id=100,
        raw_message_id=200,
        trader_id=trader_id,
        account_id="acc1",
        primary_class="SIGNAL",
        enrichment_decision="PASS",
        enriched_signal=enriched_signal,
        policy_snapshot=policy_snapshot,
        management_plan=ManagementPlanConfig(),
    )


def _make_open_chain(
    trader_id: str = "trader_a",
    symbol: str = "BTC/USDT",
    side: str = "LONG",
    trade_chain_id: int = 1,
) -> TradeChain:
    return TradeChain(
        trade_chain_id=trade_chain_id,
        source_enrichment_id=1,
        canonical_message_id=10,
        raw_message_id=20,
        trader_id=trader_id,
        account_id="acc1",
        symbol=symbol,
        side=side,
        lifecycle_state="OPEN",
        entry_mode="LIMIT",
        management_plan_json="{}",
        risk_snapshot_json="{}",
    )


def _make_account_snapshot(equity_usdt: float = 2000.0) -> AccountStateSnapshot:
    return AccountStateSnapshot(
        account_id="acc1",
        equity_usdt=equity_usdt,
        captured_at=datetime(2024, 1, 1),
        source="test",
    )


def _make_market_snapshot(mark_price: float = 50000.0) -> SymbolMarketSnapshot:
    return SymbolMarketSnapshot(
        symbol="BTC/USDT",
        mark_price=mark_price,
        captured_at=datetime(2024, 1, 1),
        source="test",
    )


# ── tests ──────────────────────────────────────────────────────────────────────

class TestRiskCapacityEngine:
    def setup_method(self) -> None:
        self.engine = RiskCapacityEngine()

    def test_risk_engine_passes_valid_limit_signal(self) -> None:
        enriched = _make_enriched(entry_type="LIMIT", entry_price=50000.0, sl_price=49000.0)
        decision = self.engine.validate(enriched, [], None, None)
        assert decision.passed is True
        assert decision.size_usdt is not None
        assert decision.size_usdt > 0

    def test_risk_engine_calculates_correct_size(self) -> None:
        # capital=1000, risk_pct=1%, entry=50000, sl=49000
        # risk_amount = 1000 * 1/100 = 10
        # risk_distance = 50000 - 49000 = 1000
        # size_usdt = 10 / 1000 * 50000 = 500
        enriched = _make_enriched(
            entry_type="LIMIT",
            entry_price=50000.0,
            sl_price=49000.0,
            capital_base_usdt=1000.0,
            risk_pct=1.0,
        )
        decision = self.engine.validate(enriched, [], None, None)
        assert decision.passed is True
        assert decision.size_usdt == pytest.approx(500.0, rel=1e-6)

    def test_risk_engine_blocks_market_entry_without_snapshot(self) -> None:
        enriched = _make_enriched(entry_type="MARKET", entry_price=50000.0)
        decision = self.engine.validate(enriched, [], None, None)
        assert decision.passed is False
        assert decision.reason == "missing_market_price_for_market_entry"

    def test_risk_engine_passes_market_entry_with_snapshot(self) -> None:
        enriched = _make_enriched(entry_type="MARKET", sl_price=49000.0)
        market_snapshot = _make_market_snapshot(mark_price=50000.0)
        decision = self.engine.validate(enriched, [], None, market_snapshot)
        assert decision.passed is True
        assert decision.size_usdt is not None
        assert decision.size_usdt > 0

    def test_risk_engine_blocks_live_equity_without_snapshot(self) -> None:
        enriched = _make_enriched(capital_base_mode="live_equity")
        decision = self.engine.validate(enriched, [], None, None)
        assert decision.passed is False
        assert decision.reason == "missing_account_snapshot_for_live_equity"

    def test_risk_engine_uses_live_equity_when_available(self) -> None:
        # capital=2000 (from equity), risk_pct=1%, entry=50000, sl=49000
        # risk_amount = 2000 * 1/100 = 20
        # risk_distance = 1000
        # size_usdt = 20 / 1000 * 50000 = 1000
        enriched = _make_enriched(
            capital_base_mode="live_equity",
            risk_pct=1.0,
            entry_price=50000.0,
            sl_price=49000.0,
        )
        account_snapshot = _make_account_snapshot(equity_usdt=2000.0)
        decision = self.engine.validate(enriched, [], account_snapshot, None)
        assert decision.passed is True
        assert decision.size_usdt == pytest.approx(1000.0, rel=1e-6)

    def test_risk_engine_blocks_max_concurrent_trades(self) -> None:
        enriched = _make_enriched(max_concurrent_trades=2)
        open_chains = [
            _make_open_chain(symbol="ETH/USDT", trade_chain_id=1),
            _make_open_chain(symbol="SOL/USDT", trade_chain_id=2),
        ]
        decision = self.engine.validate(enriched, open_chains, None, None)
        assert decision.passed is False
        assert decision.reason == "max_concurrent_trades_reached"

    def test_risk_engine_blocks_max_same_symbol(self) -> None:
        enriched = _make_enriched(symbol="BTC/USDT", max_concurrent_same_symbol=1)
        open_chains = [
            _make_open_chain(symbol="BTC/USDT", trade_chain_id=1),
        ]
        decision = self.engine.validate(enriched, open_chains, None, None)
        assert decision.passed is False
        assert decision.reason == "max_concurrent_same_symbol_reached"

    def test_risk_engine_blocks_zero_risk_distance(self) -> None:
        # entry == sl → zero risk distance
        enriched = _make_enriched(entry_price=50000.0, sl_price=50000.0)
        decision = self.engine.validate(enriched, [], None, None)
        assert decision.passed is False
        assert decision.reason == "zero_risk_distance"
