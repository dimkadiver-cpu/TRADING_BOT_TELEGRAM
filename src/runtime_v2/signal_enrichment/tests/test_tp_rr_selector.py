import pytest
from src.runtime_v2.signal_enrichment.reshaping.tp_rr_selector import (
    compute_anchor,
    select_tps_by_rr,
)


def test_anchor_single_leg():
    # Single leg degenerates to its price
    result = compute_anchor([(98.0, 1.0)])
    assert result == pytest.approx(98.0)


def test_anchor_two_legs_weighted():
    # anchor = 98*0.70 + 96*0.30 = 97.4
    result = compute_anchor([(98.0, 0.70), (96.0, 0.30)])
    assert result == pytest.approx(97.4)


def test_anchor_normalizes_weights():
    # Weights don't need to sum to 1 — function normalizes
    result = compute_anchor([(98.0, 7.0), (96.0, 3.0)])
    assert result == pytest.approx(97.4)


def test_select_tps_by_rr_example_from_spec():
    # Example from spec §5.4: anchor=97.4, stop=94, R=3.4
    tp_prices = [98.0, 100.0, 102.0, 104.0, 106.0, 108.0, 110.0, 112.0]
    result = select_tps_by_rr(
        tp_prices=tp_prices,
        desired_rr=[1.0, 1.5, 2.5, 3.5],
        anchor=97.4,
        r_unit=3.4,
        strategy="nearest_unique",
        max_rr_deviation_abs=0.35,
        on_missing_target="REJECT",
    )
    assert result == [100.0, 102.0, 106.0, 110.0]


def test_select_tps_nearest_unique_no_duplicate():
    # Two targets cannot select the same source TP
    # anchor=97.4, r=3.4: 1.0R=100(0.76), 1.1R also closest to 100
    # Second target must pick next nearest
    tp_prices = [100.0, 106.0]
    result = select_tps_by_rr(
        tp_prices=tp_prices,
        desired_rr=[1.0, 1.1],
        anchor=97.4,
        r_unit=3.4,
        strategy="nearest_unique",
        max_rr_deviation_abs=0.35,
        on_missing_target="REJECT",
    )
    # 1.0R closest to 100 (dev 0.24✓); 1.1R = 101.14, next closest is 106 (dev|2.53-1.1|=1.43 > 0.35) → no match
    assert result is None


def test_select_tps_target_no_match_in_tolerance_reject():
    # No TP within max_rr_deviation_abs of a target → REJECT
    tp_prices = [98.0, 112.0]
    result = select_tps_by_rr(
        tp_prices=tp_prices,
        desired_rr=[1.0],
        anchor=97.4,
        r_unit=3.4,
        strategy="nearest_unique",
        max_rr_deviation_abs=0.10,  # tight
        on_missing_target="REJECT",
    )
    # 1.0R = ~100.8; 98 has rr=0.18 (dev 0.82>0.10); 112 has rr=4.29 (dev 3.29>0.10)
    assert result is None


def test_select_tps_result_in_ascending_order():
    # Result must be in ascending order (for LONG)
    tp_prices = [110.0, 106.0, 102.0, 100.0]
    result = select_tps_by_rr(
        tp_prices=tp_prices,
        desired_rr=[1.0, 1.5, 2.5, 3.5],
        anchor=97.4,
        r_unit=3.4,
        strategy="nearest_unique",
        max_rr_deviation_abs=0.35,
        on_missing_target="REJECT",
    )
    assert result == [100.0, 102.0, 106.0, 110.0]


def test_select_tps_single_target():
    tp_prices = [98.0, 100.0, 102.0]
    result = select_tps_by_rr(
        tp_prices=tp_prices,
        desired_rr=[1.0],
        anchor=97.4,
        r_unit=3.4,
        strategy="nearest_unique",
        max_rr_deviation_abs=0.35,
        on_missing_target="REJECT",
    )
    assert result == [100.0]
