from __future__ import annotations

from types import SimpleNamespace

import pytest


def test_compute_breakeven_price_long_uses_open_fee_and_close_fee():
    from src.runtime_v2.lifecycle.breakeven_pricing import compute_breakeven_price

    result = compute_breakeven_price(
        side="LONG",
        entry_avg_price=100.0,
        open_position_qty=2.0,
        open_fee_residual=0.2,
        close_fee_rate=0.001,
    )

    net = (
        ((result.new_stop_price - 100.0) * 2.0)
        - 0.2
        - (result.new_stop_price * 2.0 * 0.001)
    )
    assert abs(net) < 1e-9
    assert result.open_fee_residual == 0.2
    assert result.close_fee_rate == 0.001
    assert result.close_fee_source == "chain"


def test_compute_breakeven_price_short_uses_open_fee_and_close_fee():
    from src.runtime_v2.lifecycle.breakeven_pricing import compute_breakeven_price

    result = compute_breakeven_price(
        side="SHORT",
        entry_avg_price=100.0,
        open_position_qty=2.0,
        open_fee_residual=0.2,
        close_fee_rate=0.001,
    )

    net = (
        ((100.0 - result.new_stop_price) * 2.0)
        - 0.2
        - (result.new_stop_price * 2.0 * 0.001)
    )
    assert abs(net) < 1e-9
    assert result.open_fee_residual == 0.2
    assert result.close_fee_rate == 0.001
    assert result.close_fee_source == "chain"


def test_resolve_close_fee_source_falls_back_when_chain_has_no_specific_fee():
    from src.runtime_v2.lifecycle.breakeven_pricing import resolve_close_fee_rate

    rate, source = resolve_close_fee_rate(
        protection_style="attached_full",
        chain_fee_profile={"standalone_order": 0.001},
        fallback_profile={"attached_full": 0.0006, "standalone_order": 0.001},
    )

    assert rate == 0.0006
    assert source == "fallback"


def test_compute_breakeven_price_rejects_invalid_side():
    from src.runtime_v2.lifecycle.breakeven_pricing import compute_breakeven_price

    with pytest.raises(ValueError, match="unsupported side"):
        compute_breakeven_price(
            side="FLAT",
            entry_avg_price=100.0,
            open_position_qty=2.0,
            open_fee_residual=0.2,
            close_fee_rate=0.001,
        )


@pytest.mark.parametrize("quantity", [0.0, -1.0])
def test_compute_breakeven_price_rejects_non_positive_quantity(quantity: float):
    from src.runtime_v2.lifecycle.breakeven_pricing import compute_breakeven_price

    with pytest.raises(ValueError, match="open_position_qty must be > 0"):
        compute_breakeven_price(
            side="LONG",
            entry_avg_price=100.0,
            open_position_qty=quantity,
            open_fee_residual=0.2,
            close_fee_rate=0.001,
        )


@pytest.mark.parametrize(
    ("side", "fee_rate"),
    [
        ("LONG", 1.0),
        ("LONG", 1.5),
        ("SHORT", -1.0),
        ("SHORT", -1.5),
    ],
)
def test_compute_breakeven_price_rejects_invalid_fee_rates(side: str, fee_rate: float):
    from src.runtime_v2.lifecycle.breakeven_pricing import compute_breakeven_price

    with pytest.raises(ValueError, match="invalid close_fee_rate"):
        compute_breakeven_price(
            side=side,
            entry_avg_price=100.0,
            open_position_qty=2.0,
            open_fee_residual=0.2,
            close_fee_rate=fee_rate,
        )


def test_resolve_close_fee_source_uses_chain_profile_when_present():
    from src.runtime_v2.lifecycle.breakeven_pricing import resolve_close_fee_rate

    rate, source = resolve_close_fee_rate(
        protection_style="attached_full",
        chain_fee_profile={"attached_full": 0.0004, "standalone_order": 0.001},
        fallback_profile={"attached_full": 0.0006, "standalone_order": 0.0012},
    )

    assert rate == 0.0004
    assert source == "chain"


def test_resolve_close_fee_source_raises_when_profile_is_missing():
    from src.runtime_v2.lifecycle.breakeven_pricing import resolve_close_fee_rate

    with pytest.raises(KeyError):
        resolve_close_fee_rate(
            protection_style="attached_full",
            chain_fee_profile=None,
            fallback_profile={"standalone_order": 0.001},
        )


def test_resolve_be_stop_price_returns_entry_price_when_fee_correction_disabled():
    from src.runtime_v2.lifecycle.be_move_resolver import resolve_be_stop_price
    from src.runtime_v2.signal_enrichment.models import ManagementPlanConfig

    chain = SimpleNamespace(
        entry_avg_price=50000.0,
        open_position_qty=0.01,
        side="LONG",
        risk_snapshot_json='{"open_fee_residual": 4.0, "fee_profile": {"standalone_order": 0.0004}}',
    )
    management_plan = ManagementPlanConfig(
        be_fee_correction_enabled=False,
        be_fee_fallback_profile="bybit_linear",
    )

    assert (
        resolve_be_stop_price(
            chain,
            management_plan,
            protection_style="standalone_order",
        )
        == 50000.0
    )


def test_resolve_be_stop_price_uses_fallback_when_chain_fee_is_missing():
    from src.runtime_v2.lifecycle.be_move_resolver import resolve_be_stop_price
    from src.runtime_v2.signal_enrichment.models import ManagementPlanConfig

    chain = SimpleNamespace(
        entry_avg_price=50000.0,
        open_position_qty=0.01,
        side="LONG",
        risk_snapshot_json='{"open_fee_residual": 4.0}',
    )
    management_plan = ManagementPlanConfig(
        be_fee_correction_enabled=True,
        be_fee_fallback_profile="bybit_linear",
    )

    resolved = resolve_be_stop_price(
        chain,
        management_plan,
        protection_style="attached_full",
    )

    expected = (50000.0 * 0.01 + 4.0) / (0.01 * (1 - 0.0006))
    assert resolved == pytest.approx(expected)
