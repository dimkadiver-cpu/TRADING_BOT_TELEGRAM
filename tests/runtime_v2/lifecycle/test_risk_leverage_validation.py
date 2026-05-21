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
