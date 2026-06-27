from src.runtime_v2.signal_enrichment.reshaping.tp_rr_selector import select_tps_by_rr


def test_select_tps_by_rr_short_returns_descending_prices():
    result = select_tps_by_rr(
        tp_prices=[17.8901, 17.8002, 17.2608, 16.7214],
        desired_rr=[1.0, 1.5, 2.5, 3.5],
        anchor=18.58732,
        r_unit=0.51748,
        strategy="nearest_unique",
        max_rr_deviation_abs=0.35,
        on_missing_target="REJECT",
    )

    assert result == [17.8901, 17.8002, 17.2608, 16.7214]
