import pytest
from src.runtime_v2.signal_enrichment.models import (
    ReshapeAudit,
    ReshapeRejectionInfo,
    ReshapeTemplateConfig,
    ReshapeMatchConfig,
    ReshapeEntriesConfig,
    ReshapeStopLossConfig,
    ReshapeTakeProfitsConfig,
)
from src.runtime_v2.signal_enrichment.reshaping.setup_reshaper import apply_reshape


def _ladder_4_aggressive() -> ReshapeTemplateConfig:
    return ReshapeTemplateConfig(
        id="ladder_4_aggressive",
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
    )


LADDER_ENTRIES = [("E1", 100.0), ("E2", 98.0), ("E3", 96.0), ("E4", 94.0)]
LADDER_WEIGHTS = {"E1": 0.70, "E2": 0.30, "E3": 0.20, "E4": 0.10}
LADDER_SL = 92.0
LADDER_TPS = [98.0, 100.0, 102.0, 104.0, 106.0, 108.0, 110.0, 112.0]


def test_full_reshape_spec_example():
    """Replicates spec §5 end-to-end example."""
    result = apply_reshape(
        signal_entries=LADDER_ENTRIES,
        signal_sl_price=LADDER_SL,
        signal_tp_prices=LADDER_TPS,
        signal_entry_structure="LADDER",
        signal_side="LONG",
        template=_ladder_4_aggressive(),
        weights_map=LADDER_WEIGHTS,
    )
    assert isinstance(result, ReshapeAudit)
    assert result.rule_id == "ladder_4_aggressive"
    # E1 discarded
    assert len(result.discarded_entries) == 1
    assert result.discarded_entries[0].source == "E1"
    assert result.discarded_entries[0].price == 100.0
    # E2, E3 operative
    operative_prices = [e.price for e in result.operative_entries]
    assert operative_prices == [98.0, 96.0]
    # E4 → SL
    assert result.stop_loss.source == "E4"
    assert result.stop_loss.price == 94.0
    assert result.stop_loss.replaced_original == 92.0
    # Anchor and R
    assert result.rr is not None
    assert result.rr.anchor == pytest.approx(97.4)
    assert result.rr.r_unit == pytest.approx(3.4)
    # TPs selected
    selected_prices = [t.price for t in result.tp_selection.selected]
    assert selected_prices == [100.0, 102.0, 106.0, 110.0]


def test_no_match_wrong_structure_is_rejected():
    result = apply_reshape(
        signal_entries=LADDER_ENTRIES,
        signal_sl_price=LADDER_SL,
        signal_tp_prices=LADDER_TPS,
        signal_entry_structure="RANGE",  # doesn't match LADDER
        signal_side="LONG",
        template=_ladder_4_aggressive(),
        weights_map=LADDER_WEIGHTS,
    )
    assert isinstance(result, ReshapeRejectionInfo)
    assert result.phase == "no_match"


def test_no_match_wrong_entry_count():
    entries_3 = [("E1", 100.0), ("E2", 98.0), ("E3", 96.0)]
    weights_3 = {"E1": 0.50, "E2": 0.30, "E3": 0.20}
    result = apply_reshape(
        signal_entries=entries_3,
        signal_sl_price=94.0,
        signal_tp_prices=LADDER_TPS,
        signal_entry_structure="LADDER",
        signal_side="LONG",
        template=_ladder_4_aggressive(),  # requires normalized_entry_count=4
        weights_map=weights_3,
    )
    assert isinstance(result, ReshapeRejectionInfo)
    assert result.phase == "no_match"


def test_no_match_insufficient_tp_count():
    tps_7 = LADDER_TPS[:7]  # only 7, template requires min_tp_count=8
    result = apply_reshape(
        signal_entries=LADDER_ENTRIES,
        signal_sl_price=LADDER_SL,
        signal_tp_prices=tps_7,
        signal_entry_structure="LADDER",
        signal_side="LONG",
        template=_ladder_4_aggressive(),
        weights_map=LADDER_WEIGHTS,
    )
    assert isinstance(result, ReshapeRejectionInfo)
    assert result.phase == "no_match"


def test_keep_last_n_entries():
    template = ReshapeTemplateConfig(
        id="ladder_4_keep_sl",
        enabled=True,
        match=ReshapeMatchConfig(entry_structure="LADDER", normalized_entry_count=4, min_tp_count=8),
        entries=ReshapeEntriesConfig(mode="keep_last", n=2),
        stop_loss=ReshapeStopLossConfig(mode="original"),
        take_profits=ReshapeTakeProfitsConfig(mode="count", n=4),
    )
    result = apply_reshape(
        signal_entries=LADDER_ENTRIES,
        signal_sl_price=LADDER_SL,
        signal_tp_prices=LADDER_TPS,
        signal_entry_structure="LADDER",
        signal_side="LONG",
        template=template,
        weights_map=LADDER_WEIGHTS,
    )
    assert isinstance(result, ReshapeAudit)
    operative_prices = [e.price for e in result.operative_entries]
    assert operative_prices == [96.0, 94.0]  # E3, E4 (last 2)
    assert result.stop_loss.price == LADDER_SL  # original SL preserved
    assert result.stop_loss.replaced_original is None
    selected_prices = [t.price for t in result.tp_selection.selected]
    assert selected_prices == [98.0, 100.0, 102.0, 104.0]  # first 4


def test_keep_only_specific_entries():
    template = ReshapeTemplateConfig(
        id="keep_e2_e3",
        enabled=True,
        match=ReshapeMatchConfig(entry_structure="LADDER", normalized_entry_count=4, min_tp_count=8),
        entries=ReshapeEntriesConfig(mode="keep_only", indexes=["E2", "E3"]),
        stop_loss=ReshapeStopLossConfig(mode="original"),
        take_profits=ReshapeTakeProfitsConfig(mode="keep_all"),
    )
    result = apply_reshape(
        signal_entries=LADDER_ENTRIES,
        signal_sl_price=LADDER_SL,
        signal_tp_prices=LADDER_TPS,
        signal_entry_structure="LADDER",
        signal_side="LONG",
        template=template,
        weights_map=LADDER_WEIGHTS,
    )
    assert isinstance(result, ReshapeAudit)
    operative_prices = [e.price for e in result.operative_entries]
    assert operative_prices == [98.0, 96.0]  # E2, E3


def test_invalid_output_rejected():
    # drop all entries → validator should reject
    template = ReshapeTemplateConfig(
        id="drop_all",
        enabled=True,
        match=ReshapeMatchConfig(entry_structure="LADDER", normalized_entry_count=4, min_tp_count=8),
        entries=ReshapeEntriesConfig(mode="drop", indexes=["E1", "E2", "E3", "E4"]),
        stop_loss=ReshapeStopLossConfig(mode="original"),
        take_profits=ReshapeTakeProfitsConfig(mode="keep_all"),
    )
    result = apply_reshape(
        signal_entries=LADDER_ENTRIES,
        signal_sl_price=LADDER_SL,
        signal_tp_prices=LADDER_TPS,
        signal_entry_structure="LADDER",
        signal_side="LONG",
        template=template,
        weights_map=LADDER_WEIGHTS,
    )
    assert isinstance(result, ReshapeRejectionInfo)
    assert result.phase == "invalid_output"
    assert result.reason_code == "reshape_no_operative_entry"


def test_tp_drop_by_indexes():
    template = ReshapeTemplateConfig(
        id="drop_tps",
        enabled=True,
        match=ReshapeMatchConfig(entry_structure="LADDER", normalized_entry_count=4, min_tp_count=8),
        entries=ReshapeEntriesConfig(mode="keep"),
        stop_loss=ReshapeStopLossConfig(mode="original"),
        take_profits=ReshapeTakeProfitsConfig(mode="drop", indexes=[1, 2, 4]),
    )
    result = apply_reshape(
        signal_entries=LADDER_ENTRIES,
        signal_sl_price=LADDER_SL,
        signal_tp_prices=[98.0, 100.0, 102.0, 104.0, 106.0, 108.0, 110.0, 112.0],
        signal_entry_structure="LADDER",
        signal_side="LONG",
        template=template,
        weights_map=LADDER_WEIGHTS,
    )
    assert isinstance(result, ReshapeAudit)
    # Dropped indexes 1,2,4 (1-based): 98, 100, 104 removed; kept: 102, 106, 108, 110, 112
    selected_prices = [t.price for t in result.tp_selection.selected]
    assert selected_prices == [102.0, 106.0, 108.0, 110.0, 112.0]


def test_disabled_template_no_match():
    template = _ladder_4_aggressive()
    template = template.model_copy(update={"enabled": False})
    result = apply_reshape(
        signal_entries=LADDER_ENTRIES,
        signal_sl_price=LADDER_SL,
        signal_tp_prices=LADDER_TPS,
        signal_entry_structure="LADDER",
        signal_side="LONG",
        template=template,
        weights_map=LADDER_WEIGHTS,
    )
    assert isinstance(result, ReshapeRejectionInfo)
    assert result.phase == "no_match"
