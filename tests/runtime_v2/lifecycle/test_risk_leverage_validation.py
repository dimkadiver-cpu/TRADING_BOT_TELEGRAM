from __future__ import annotations

import pytest

from src.runtime_v2.signal_enrichment.models import (
    AccountConfig,
    CloseDistributionConfig,
    EffectiveEnrichmentConfig,
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


def _make_signal_policy() -> SignalPolicyConfig:
    w = EntryWeightsConfig(weights={"E1": 1.0})
    r = EntryRangeConfig(weights={"E1": 0.5, "E2": 0.5})
    return SignalPolicyConfig(
        accepted_entry_structures=["ONE_SHOT", "TWO_STEP", "RANGE", "LADDER"],
        market_execution=MarketExecutionConfig(),
        entry_split=EntrySplitConfig(
            LIMIT=LimitEntrySplitConfig(single=w, range=r, averaging=w, ladder=w),
            MARKET=MarketEntrySplitConfig(single=w, averaging=w),
        ),
        tp=TpConfig(),
        sl=SlConfig(),
        price_corrections=PriceCorrectionsConfig(),
        price_sanity=PriceSanityConfig(),
    )


def _make_mgmt() -> ManagementPlanConfig:
    return ManagementPlanConfig()


def _make_risk(leverage: int = 5) -> RiskConfig:
    return RiskConfig(leverage=leverage, capital_base_usdt=1000.0, risk_pct_of_capital=1.0)


def test_effective_enrichment_config_has_account_field():
    cfg = EffectiveEnrichmentConfig(
        trader_id="t1",
        enabled=True,
        gate_mode="block",
        hedge_mode=False,
        account_id="main",
        signal_policy=_make_signal_policy(),
        update_admission={},
        management_plan=_make_mgmt(),
        risk=_make_risk(),
        account=AccountConfig(
            id="main",
            capital_base_usdt=1000.0,
            max_leverage=5,
            max_capital_at_risk_pct=10.0,
            hard_max_per_signal_risk_pct=2.0,
        ),
    )
    assert cfg.account is not None
    assert cfg.account.max_leverage == 5


def test_effective_enrichment_config_account_defaults_none():
    cfg = EffectiveEnrichmentConfig(
        trader_id="t1",
        enabled=True,
        gate_mode="block",
        hedge_mode=False,
        account_id="main",
        signal_policy=_make_signal_policy(),
        update_admission={},
        management_plan=_make_mgmt(),
        risk=_make_risk(),
    )
    assert cfg.account is None


from src.runtime_v2.lifecycle.ports import AccountStateSnapshot, SymbolMarketSnapshot
from src.runtime_v2.lifecycle.risk_capacity import RiskCapacityEngine
from src.runtime_v2.signal_enrichment.models import (
    EnrichedCanonicalMessage,
    EnrichedEntryLeg,
    EnrichedSignalPayload,
)
from src.parser_v2.contracts.entities import Price, StopLoss, TakeProfit
from datetime import datetime, timezone


def _make_enriched(
    leverage: int = 5,
    max_leverage: int = 5,
    hedge_mode: bool = False,
    leverage_hint: float | None = None,
    use_trader_leverage_hint: bool = False,
):
    entries = [
        EnrichedEntryLeg(
            sequence=1,
            entry_type="LIMIT",
            price=Price(raw="65000", value=65000.0),
            weight=1.0,
        )
    ]
    signal = EnrichedSignalPayload(
        symbol="BTC/USDT:USDT",
        side="LONG",
        entry_structure="ONE_SHOT",
        entries=entries,
        take_profits=[TakeProfit(price=Price(raw="70000", value=70000.0), sequence=1)],
        stop_loss=StopLoss(price=Price(raw="63000", value=63000.0)),
        leverage_hint=leverage_hint,
    )
    account = AccountConfig(
        id="main",
        capital_base_usdt=1000.0,
        max_leverage=max_leverage,
        max_capital_at_risk_pct=10.0,
        hard_max_per_signal_risk_pct=2.0,
    )
    risk = _make_risk(leverage=leverage)
    risk.use_trader_leverage_hint = use_trader_leverage_hint
    cfg = EffectiveEnrichmentConfig(
        trader_id="t1",
        enabled=True,
        gate_mode="block",
        hedge_mode=hedge_mode,
        account_id="main",
        signal_policy=_make_signal_policy(),
        update_admission={},
        management_plan=_make_mgmt(),
        risk=risk,
        account=account,
    )
    return EnrichedCanonicalMessage(
        enrichment_id=1,
        canonical_message_id=10,
        raw_message_id=5,
        trader_id="t1",
        account_id="main",
        primary_class="SIGNAL",
        enrichment_decision="PASS",
        enriched_signal=signal,
        enriched_actions=None,
        management_plan=ManagementPlanConfig(),
        policy_snapshot=cfg.model_dump(),
    )


def _make_market_snapshot():
    return SymbolMarketSnapshot(
        symbol="BTC/USDT:USDT",
        mark_price=65000.0,
        bid=64990.0,
        ask=65010.0,
        min_order_size=0.001,
        price_precision=1,
        qty_precision=3,
        source="test",
        captured_at=datetime.now(timezone.utc),
    )


def test_risk_leverage_within_max_passes():
    engine = RiskCapacityEngine()
    enriched = _make_enriched(leverage=5, max_leverage=5)
    result = engine.validate(enriched, [], None, _make_market_snapshot())
    assert result.passed is True
    assert result.risk_snapshot["leverage"] == 5


def test_risk_leverage_exceeds_max_blocked():
    engine = RiskCapacityEngine()
    enriched = _make_enriched(leverage=10, max_leverage=5)
    result = engine.validate(enriched, [], None, _make_market_snapshot())
    assert result.passed is False
    assert result.reason == "risk_leverage_exceeds_account_max_leverage"


def test_risk_snapshot_includes_hedge_mode():
    engine = RiskCapacityEngine()
    enriched = _make_enriched(leverage=3, max_leverage=5, hedge_mode=True)
    result = engine.validate(enriched, [], None, _make_market_snapshot())
    assert result.passed is True
    assert result.risk_snapshot["hedge_mode"] is True


def test_risk_snapshot_hedge_mode_false_by_default():
    engine = RiskCapacityEngine()
    enriched = _make_enriched(leverage=3, max_leverage=5, hedge_mode=False)
    result = engine.validate(enriched, [], None, _make_market_snapshot())
    assert result.passed is True
    assert result.risk_snapshot["hedge_mode"] is False


def test_leverage_hint_present_but_flag_false_uses_configured_leverage():
    engine = RiskCapacityEngine()
    enriched = _make_enriched(
        leverage=5,
        max_leverage=10,
        leverage_hint=3.0,
        use_trader_leverage_hint=False,
    )

    result = engine.validate(enriched, [], None, _make_market_snapshot())

    assert result.passed is True
    assert result.leverage == 5
    assert result.risk_snapshot["leverage"] == 5
    assert result.leverage_hint_applied is None


def test_absent_leverage_hint_falls_back_to_configured_leverage():
    engine = RiskCapacityEngine()
    enriched = _make_enriched(leverage=4, max_leverage=10, leverage_hint=None)

    result = engine.validate(enriched, [], None, _make_market_snapshot())

    assert result.passed is True
    assert result.leverage == 4
    assert result.risk_snapshot["leverage"] == 4
    assert result.leverage_hint_applied is None


def test_leverage_hint_present_and_flag_true_overrides_configured_leverage():
    engine = RiskCapacityEngine()
    enriched = _make_enriched(
        leverage=5,
        max_leverage=10,
        leverage_hint=3.0,
        use_trader_leverage_hint=True,
    )

    result = engine.validate(enriched, [], None, _make_market_snapshot())

    assert result.passed is True
    assert result.leverage == 3
    assert result.risk_snapshot["leverage"] == 3
    assert result.leverage_hint_applied == {
        "hint_used": True,
        "hint_raw": "3.0",
        "hint_effective": 3,
        "configured_leverage": 5,
        "effective_leverage": 3,
    }


def test_leverage_hint_above_account_max_with_flag_true_blocks_with_dedicated_reason():
    engine = RiskCapacityEngine()
    enriched = _make_enriched(
        leverage=3,
        max_leverage=5,
        leverage_hint=6.0,
        use_trader_leverage_hint=True,
    )

    result = engine.validate(enriched, [], None, _make_market_snapshot())

    assert result.passed is False
    assert result.reason == "signal_leverage_hint_exceeds_account_max_leverage"
    assert result.leverage_hint_applied == {
        "hint_used": True,
        "hint_raw": "6.0",
        "hint_effective": 6,
        "configured_leverage": 3,
        "effective_leverage": 6,
    }


def test_fractional_leverage_hint_above_account_max_blocks_before_int_coercion():
    engine = RiskCapacityEngine()
    enriched = _make_enriched(
        leverage=3,
        max_leverage=5,
        leverage_hint=5.9,
        use_trader_leverage_hint=True,
    )

    result = engine.validate(enriched, [], None, _make_market_snapshot())

    assert result.passed is False
    assert result.reason == "signal_leverage_hint_exceeds_account_max_leverage"
    assert result.leverage_hint_applied == {
        "hint_used": True,
        "hint_raw": "5.9",
        "hint_effective": 5,
        "configured_leverage": 3,
        "effective_leverage": 5,
    }
