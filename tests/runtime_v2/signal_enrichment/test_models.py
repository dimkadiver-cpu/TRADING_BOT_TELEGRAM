from __future__ import annotations
import pytest
from pydantic import ValidationError


def test_enrichment_log_entry_rejects_extra_fields():
    from src.runtime_v2.signal_enrichment.models import EnrichmentLogEntry
    with pytest.raises(ValidationError):
        EnrichmentLogEntry(check="x", result="y", unknown_field="z")


def test_management_plan_config_defaults():
    from src.runtime_v2.signal_enrichment.models import ManagementPlanConfig, CloseDistributionConfig
    plan = ManagementPlanConfig(close_distribution=CloseDistributionConfig())
    assert plan.be_trigger is None
    assert plan.be_fee_correction_enabled is False
    assert plan.be_fee_fallback_profile is None
    assert plan.cancel_pending_by_engine is True
    assert plan.pending_timeout_hours == 24
    assert plan.risk_freed_by_be is True
    assert plan.protective_sl_mode == "exchange_native_first"


def test_management_plan_config_ignores_legacy_be_buffer_field_in_json():
    from src.runtime_v2.signal_enrichment.models import ManagementPlanConfig

    plan = ManagementPlanConfig.model_validate_json(
        '{"be_trigger":"tp1","be_buffer_pct":0.05}'
    )

    assert plan.be_trigger == "tp1"
    assert plan.be_fee_correction_enabled is False
    assert plan.be_fee_fallback_profile is None


def test_effective_enrichment_config_fields():
    from src.runtime_v2.signal_enrichment.models import (
        EffectiveEnrichmentConfig, SignalPolicyConfig, ManagementPlanConfig,
        CloseDistributionConfig, RiskConfig, MarketExecutionConfig,
        EntrySplitConfig, LimitEntrySplitConfig, MarketEntrySplitConfig,
        EntryWeightsConfig, EntryRangeConfig, TpConfig, SlConfig,
        PriceCorrectionsConfig, PriceSanityConfig,
    )
    signal_policy = SignalPolicyConfig(
        accepted_entry_structures=["ONE_SHOT"],
        market_execution=MarketExecutionConfig(),
        entry_split=EntrySplitConfig(
            LIMIT=LimitEntrySplitConfig(
                single=EntryWeightsConfig(weights={"E1": 1.0}),
                range=EntryRangeConfig(weights={"E1": 0.5, "E2": 0.5}),
                averaging=EntryWeightsConfig(weights={"E1": 0.7, "E2": 0.3}),
                ladder=EntryWeightsConfig(weights={"E1": 0.5, "E2": 0.3, "E3": 0.2}),
            ),
            MARKET=MarketEntrySplitConfig(
                single=EntryWeightsConfig(weights={"E1": 1.0}),
                averaging=EntryWeightsConfig(weights={"E1": 0.7, "E2": 0.3}),
            ),
        ),
        tp=TpConfig(),
        sl=SlConfig(),
        price_corrections=PriceCorrectionsConfig(),
        price_sanity=PriceSanityConfig(),
    )
    cfg = EffectiveEnrichmentConfig(
        trader_id="trader_a",
        enabled=True,
        gate_mode="block",
        hedge_mode=False,
        account_id="main",
        signal_policy=signal_policy,
        update_admission={"MOVE_STOP": True},
        management_plan=ManagementPlanConfig(close_distribution=CloseDistributionConfig()),
        risk=RiskConfig(),
    )
    assert cfg.trader_id == "trader_a"
    assert cfg.account_id == "main"
    assert cfg.hedge_mode is False


def test_enriched_canonical_message_defaults():
    from src.runtime_v2.signal_enrichment.models import EnrichedCanonicalMessage
    msg = EnrichedCanonicalMessage(
        canonical_message_id=1,
        raw_message_id=10,
        trader_id="trader_a",
        account_id="main",
        primary_class="SIGNAL",
        enrichment_decision="BLOCK",
        reason_code="missing_stop_loss",
        policy_version="sha256:abc",
    )
    assert msg.enriched_signal is None
    assert msg.lifecycle_processed is False
    assert msg.enrichment_log == []
