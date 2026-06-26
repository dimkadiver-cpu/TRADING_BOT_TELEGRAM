from src.runtime_v2.signal_enrichment.models import (
    ReshapeTemplateConfig,
    ReshapeEntriesConfig,
    ReshapeStopLossConfig,
    ReshapeTakeProfitsConfig,
    ReshapeMatchConfig,
    ReshapeAudit,
    ReshapeAuditRr,
    ReshapeAuditTpSelection,
    ReshapeAuditEntry,
    ReshapeAuditDiscarded,
    ReshapeAuditStopLoss,
    ReshapeAuditTpSelected,
    ReshapeRejectionInfo,
    EnrichedSignalPayload,
    EffectiveEnrichmentConfig,
)


def _make_template():
    return ReshapeTemplateConfig(
        id="test_template",
        enabled=True,
        match=ReshapeMatchConfig(entry_structure="LADDER", normalized_entry_count=4, min_tp_count=8),
        entries=ReshapeEntriesConfig(mode="drop", indexes=["E1"]),
        stop_loss=ReshapeStopLossConfig(mode="from_entry", entry="E4"),
        take_profits=ReshapeTakeProfitsConfig(
            mode="by_rr",
            desired_rr=[1.0, 1.5, 2.5, 3.5],
            strategy="nearest_unique",
            max_rr_deviation_abs=0.35,
            on_missing_target="REJECT",
        ),
        on_failure="REJECT",
    )


def test_template_config_round_trips():
    t = _make_template()
    assert t.id == "test_template"
    assert t.match.entry_structure == "LADDER"
    assert t.match.normalized_entry_count == 4
    assert t.match.min_tp_count == 8
    assert t.entries.mode == "drop"
    assert t.entries.indexes == ["E1"]
    assert t.stop_loss.mode == "from_entry"
    assert t.stop_loss.entry == "E4"
    assert t.take_profits.mode == "by_rr"
    assert t.take_profits.desired_rr == [1.0, 1.5, 2.5, 3.5]


def test_reshape_audit_model():
    audit = ReshapeAudit(
        rule_id="test_template",
        discarded_entries=[ReshapeAuditDiscarded(source="E1", price=100.0, reason="initial_entry_skipped")],
        operative_entries=[ReshapeAuditEntry(source="E2", price=98.0), ReshapeAuditEntry(source="E3", price=96.0)],
        stop_loss=ReshapeAuditStopLoss(source="E4", price=94.0, replaced_original=92.0),
        rr=ReshapeAuditRr(anchor=97.4, stop=94.0, r_unit=3.4),
        tp_selection=ReshapeAuditTpSelection(
            mode="by_rr",
            selected=[ReshapeAuditTpSelected(price=100.0, rr=0.76)],
            discarded=[98.0, 104.0],
        ),
    )
    assert audit.rule_id == "test_template"
    assert audit.rr.anchor == 97.4


def test_reshape_rejection_info():
    rej = ReshapeRejectionInfo(rule_id="test_template", phase="no_match", reason_code="reshape_no_match")
    assert rej.phase == "no_match"


def test_effective_config_has_setup_mode(tmp_path):
    # EffectiveEnrichmentConfig default setup_mode is passthrough
    from src.runtime_v2.signal_enrichment.models import (
        SignalPolicyConfig, EntrySplitConfig, LimitEntrySplitConfig,
        MarketEntrySplitConfig, EntryWeightsConfig, EntryRangeConfig,
        TpConfig, SlConfig, PriceCorrectionsConfig, PriceSanityConfig,
        ManagementPlanConfig, CloseDistributionConfig, RiskConfig, MarketExecutionConfig,
    )
    cfg = EffectiveEnrichmentConfig(
        trader_id="t1",
        enabled=True,
        gate_mode="block",
        hedge_mode=False,
        account_id="main",
        signal_policy=SignalPolicyConfig(
            accepted_entry_structures=["LADDER"],
            market_execution=MarketExecutionConfig(),
            entry_split=EntrySplitConfig(
                LIMIT=LimitEntrySplitConfig(
                    single=EntryWeightsConfig(weights={"E1": 1.0}),
                    range=EntryRangeConfig(weights={"E1": 0.5, "E2": 0.5}),
                    averaging=EntryWeightsConfig(weights={"E1": 0.7, "E2": 0.3}),
                    ladder=EntryWeightsConfig(weights={"E1": 0.4, "E2": 0.3, "E3": 0.2, "E4": 0.1}),
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
        ),
        update_admission={},
        management_plan=ManagementPlanConfig(close_distribution=CloseDistributionConfig()),
        risk=RiskConfig(),
    )
    assert cfg.setup_mode == "passthrough"
    assert cfg.setup_reshape_template is None
