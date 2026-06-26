import pytest
from src.runtime_v2.signal_enrichment.reshaping.reshape_validator import validate_reshape


# --- entries invariants ---

def test_valid_long_setup():
    # LONG: entries above SL, TPs above anchor
    assert validate_reshape(
        operative_prices=[98.0, 96.0],
        stop_loss_price=94.0,
        take_profits=[100.0, 102.0, 106.0, 110.0],
        side="LONG",
        anchor=97.4,
    ) is None


def test_valid_short_setup():
    # SHORT: entries below SL, TPs below anchor
    assert validate_reshape(
        operative_prices=[100.0, 102.0],
        stop_loss_price=104.0,
        take_profits=[98.0, 96.0, 94.0],
        side="SHORT",
        anchor=101.0,
    ) is None


def test_no_operative_entries():
    assert validate_reshape(
        operative_prices=[],
        stop_loss_price=94.0,
        take_profits=[100.0],
        side="LONG",
        anchor=97.4,
    ) == "reshape_no_operative_entry"


def test_no_take_profits():
    assert validate_reshape(
        operative_prices=[98.0, 96.0],
        stop_loss_price=94.0,
        take_profits=[],
        side="LONG",
        anchor=97.4,
    ) == "reshape_no_take_profit"


# --- stop_loss invariants ---

def test_stop_wrong_side_long():
    # LONG: SL must be < min(entries)
    assert validate_reshape(
        operative_prices=[98.0, 96.0],
        stop_loss_price=99.0,  # above entries → wrong side
        take_profits=[100.0],
        side="LONG",
        anchor=97.4,
    ) == "reshape_stop_wrong_side"


def test_stop_wrong_side_short():
    # SHORT: SL must be > max(entries)
    assert validate_reshape(
        operative_prices=[100.0, 102.0],
        stop_loss_price=99.0,  # below entries → wrong side
        take_profits=[98.0],
        side="SHORT",
        anchor=101.0,
    ) == "reshape_stop_wrong_side"


def test_stop_equals_entry():
    assert validate_reshape(
        operative_prices=[98.0, 96.0],
        stop_loss_price=96.0,  # equals one of the entries
        take_profits=[100.0],
        side="LONG",
        anchor=97.4,
    ) == "reshape_stop_equals_entry"


def test_zero_risk_distance():
    # anchor == stop → R = 0
    assert validate_reshape(
        operative_prices=[97.4],
        stop_loss_price=97.4,
        take_profits=[100.0],
        side="LONG",
        anchor=97.4,
    ) == "reshape_zero_risk_distance"


# --- take_profits invariants ---

def test_tp_not_profitable_long():
    # LONG: TP must be > anchor
    assert validate_reshape(
        operative_prices=[98.0, 96.0],
        stop_loss_price=94.0,
        take_profits=[97.0],  # below anchor
        side="LONG",
        anchor=97.4,
    ) == "reshape_tp_not_profitable"


def test_tp_not_profitable_short():
    # SHORT: TP must be < anchor
    assert validate_reshape(
        operative_prices=[100.0, 102.0],
        stop_loss_price=104.0,
        take_profits=[102.0],  # above anchor
        side="SHORT",
        anchor=101.0,
    ) == "reshape_tp_not_profitable"


def test_tp_not_monotonic_long():
    # LONG: TPs must be strictly ascending
    assert validate_reshape(
        operative_prices=[98.0, 96.0],
        stop_loss_price=94.0,
        take_profits=[100.0, 106.0, 102.0],  # 106 before 102 → not monotonic
        side="LONG",
        anchor=97.4,
    ) == "reshape_tp_not_monotonic"


def test_tp_not_monotonic_short():
    # SHORT: TPs must be strictly descending
    assert validate_reshape(
        operative_prices=[100.0, 102.0],
        stop_loss_price=104.0,
        take_profits=[98.0, 96.0, 97.0],  # not descending
        side="SHORT",
        anchor=101.0,
    ) == "reshape_tp_not_monotonic"
