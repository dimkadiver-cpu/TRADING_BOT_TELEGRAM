"""Tests for canonical v1 schema — positive cases + required negative cases.

Exit criteria (FASE 2):
  - All positive fixtures instantiate without ValidationError.
  - All negative cases raise ValidationError with the expected constraint.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.parser.canonical_v1.models import (
    CancelPendingOperation,
    CanonicalMessage,
    CloseOperation,
    EntryLeg,
    ModifyEntriesOperation,
    ModifyTargetsOperation,
    Price,
    RawContext,
    ReportPayload,
    SignalPayload,
    StopLoss,
    StopTarget,
    TakeProfit,
    Targeting,
    TargetRef,
    TargetScope,
    UpdateOperation,
    UpdatePayload,
    normalize_price,
)
from tests.parser_canonical_v1.fixtures import ALL_VALID


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _raw(text: str = "msg") -> RawContext:
    return RawContext(raw_text=text)


def _price(raw: str, value: float) -> Price:
    return Price(raw=raw, value=value)


def _tp(seq: int, raw: str, value: float) -> TakeProfit:
    return TakeProfit(sequence=seq, price=_price(raw, value))


def _limit_leg(seq: int, raw: str, value: float) -> EntryLeg:
    return EntryLeg(sequence=seq, entry_type="LIMIT", price=_price(raw, value))


def _market_leg(seq: int) -> EntryLeg:
    return EntryLeg(sequence=seq, entry_type="MARKET")


def _stop(raw: str, value: float) -> StopLoss:
    return StopLoss(price=_price(raw, value))


def _signal_base(**kwargs) -> dict:
    """Minimum valid PARSED SIGNAL ONE_SHOT kwargs."""
    return dict(
        parser_profile="test",
        primary_class="SIGNAL",
        parse_status="PARSED",
        confidence=0.9,
        raw_context=_raw(),
        signal=SignalPayload(
            symbol="BTC/USDT",
            side="LONG",
            entry_structure="ONE_SHOT",
            entries=[_market_leg(1)],
            stop_loss=_stop("44000", 44000.0),
            take_profits=[_tp(1, "46000", 46000.0)],
            completeness="COMPLETE",
        ),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# normalize_price unit tests
# ---------------------------------------------------------------------------

class TestNormalizePrice:
    def test_simple_float(self):
        assert normalize_price("0.1772") == pytest.approx(0.1772)

    def test_space_thousands(self):
        assert normalize_price("90 000.5") == pytest.approx(90000.5)

    def test_comma_thousands(self):
        assert normalize_price("90,000.5") == pytest.approx(90000.5)

    def test_european_format(self):
        assert normalize_price("90.000,5", decimal_separator=",") == pytest.approx(90000.5)

    def test_european_space_thousands(self):
        assert normalize_price("1 234,56", decimal_separator=",") == pytest.approx(1234.56)

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            normalize_price("")

    def test_non_numeric_raises(self):
        with pytest.raises(ValueError):
            normalize_price("abc")


# ---------------------------------------------------------------------------
# Price model tests
# ---------------------------------------------------------------------------

class TestPrice:
    def test_from_raw(self):
        p = Price.from_raw("44 000")
        assert p.value == pytest.approx(44000.0)
        assert p.raw == "44 000"

    def test_from_float(self):
        p = Price.from_float(1.5)
        assert p.value == pytest.approx(1.5)
        assert p.raw == "1.5"

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            Price(raw="1", value=1.0, extra_field="bad")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Positive cases — all fixtures must instantiate cleanly
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,factory", ALL_VALID, ids=[name for name, _ in ALL_VALID])
def test_positive_fixture(name: str, factory) -> None:
    msg = factory()
    assert isinstance(msg, CanonicalMessage)
    assert msg.parse_status in ("PARSED", "PARTIAL", "UNCLASSIFIED", "ERROR")


# ---------------------------------------------------------------------------
# Negative cases — must all raise ValidationError
# ---------------------------------------------------------------------------

class TestNegativeCases:

    def test_signal_without_signal_payload(self):
        """SIGNAL primary_class requires signal payload."""
        with pytest.raises(ValidationError, match="signal payload"):
            CanonicalMessage(
                parser_profile="test",
                primary_class="SIGNAL",
                parse_status="PARTIAL",
                confidence=0.5,
                raw_context=_raw(),
                # signal=None intentionally omitted
            )

    def test_signal_with_update_present(self):
        """SIGNAL primary_class forbids update payload."""
        with pytest.raises(ValidationError, match="forbids update"):
            CanonicalMessage(
                parser_profile="test",
                primary_class="SIGNAL",
                parse_status="PARTIAL",
                confidence=0.5,
                raw_context=_raw(),
                signal=SignalPayload(
                    symbol="X",
                    side="LONG",
                    entry_structure="ONE_SHOT",
                    entries=[_market_leg(1)],
                    completeness="INCOMPLETE",
                    missing_fields=["stop_loss", "take_profits"],
                ),
                update=UpdatePayload(
                    operations=[
                        UpdateOperation(
                            op_type="CANCEL_PENDING",
                            cancel_pending=CancelPendingOperation(),
                        )
                    ]
                ),
            )

    def test_one_shot_with_zero_entries(self):
        """ONE_SHOT with 0 entry legs → ValidationError from SignalPayload."""
        with pytest.raises(ValidationError, match="exactly 1 entry leg"):
            SignalPayload(
                symbol="BTC/USDT",
                side="LONG",
                entry_structure="ONE_SHOT",
                entries=[],
                completeness="INCOMPLETE",
                missing_fields=["entries"],
            )

    def test_two_step_with_one_leg(self):
        """TWO_STEP requires exactly 2 legs."""
        with pytest.raises(ValidationError, match="exactly 2 entry legs"):
            SignalPayload(
                symbol="ETH/USDT",
                side="LONG",
                entry_structure="TWO_STEP",
                entries=[_limit_leg(1, "2000", 2000.0)],
                completeness="INCOMPLETE",
                missing_fields=["entries"],
            )

    def test_ladder_with_two_legs(self):
        """LADDER requires at least 3 legs."""
        with pytest.raises(ValidationError, match="at least 3 entry legs"):
            SignalPayload(
                symbol="SOL/USDT",
                side="SHORT",
                entry_structure="LADDER",
                entries=[
                    _limit_leg(1, "90", 90.0),
                    _limit_leg(2, "85", 85.0),
                ],
                completeness="INCOMPLETE",
                missing_fields=["entries"],
            )

    def test_limit_leg_without_price(self):
        """LIMIT entry leg requires price."""
        with pytest.raises(ValidationError, match="LIMIT entry leg requires price"):
            EntryLeg(sequence=1, entry_type="LIMIT", price=None)

    def test_set_stop_with_two_subfields(self):
        """SET_STOP op requires only set_stop to be populated."""
        with pytest.raises(ValidationError, match="requires only"):
            UpdateOperation(
                op_type="SET_STOP",
                set_stop=StopTarget(target_type="ENTRY", value=None),
                close=CloseOperation(close_scope="FULL"),
            )

    def test_info_with_signal_present(self):
        """INFO primary_class forbids signal/update/report payloads."""
        with pytest.raises(ValidationError, match="signal/update/report to be absent"):
            CanonicalMessage(
                parser_profile="test",
                primary_class="INFO",
                parse_status="PARSED",
                confidence=0.7,
                raw_context=_raw(),
                signal=SignalPayload(
                    symbol="BTC/USDT",
                    side="LONG",
                    entry_structure="ONE_SHOT",
                    entries=[_market_leg(1)],
                    completeness="INCOMPLETE",
                    missing_fields=["stop_loss", "take_profits"],
                ),
            )

    def test_update_without_update_payload(self):
        """UPDATE primary_class requires update payload."""
        with pytest.raises(ValidationError, match="update payload"):
            CanonicalMessage(
                parser_profile="test",
                primary_class="UPDATE",
                parse_status="PARTIAL",
                confidence=0.5,
                raw_context=_raw(),
            )

    def test_report_without_report_payload(self):
        """REPORT primary_class requires report payload."""
        with pytest.raises(ValidationError, match="report payload"):
            CanonicalMessage(
                parser_profile="test",
                primary_class="REPORT",
                parse_status="PARTIAL",
                confidence=0.5,
                raw_context=_raw(),
            )

    def test_parsed_signal_missing_symbol(self):
        """PARSED SIGNAL must have signal.symbol."""
        with pytest.raises(ValidationError, match="signal.symbol"):
            CanonicalMessage(
                parser_profile="test",
                primary_class="SIGNAL",
                parse_status="PARSED",
                confidence=0.9,
                raw_context=_raw(),
                signal=SignalPayload(
                    symbol=None,
                    side="LONG",
                    entry_structure="ONE_SHOT",
                    entries=[_market_leg(1)],
                    stop_loss=_stop("100", 100.0),
                    take_profits=[_tp(1, "110", 110.0)],
                    completeness="COMPLETE",
                ),
            )

    def test_parsed_update_requires_operations(self):
        """PARSED UPDATE must have at least one operation."""
        with pytest.raises(ValidationError, match="at least one operation"):
            CanonicalMessage(
                parser_profile="test",
                primary_class="UPDATE",
                parse_status="PARSED",
                confidence=0.9,
                raw_context=_raw(),
                update=UpdatePayload(operations=[]),
            )

    def test_modify_entries_requires_non_empty_entries(self):
        """MODIFY_ENTRIES operation must have at least one entry."""
        with pytest.raises(ValidationError, match="non-empty entries"):
            UpdateOperation(
                op_type="MODIFY_ENTRIES",
                modify_entries=ModifyEntriesOperation(mode="ADD", entries=[]),
            )

    def test_modify_targets_requires_non_empty_take_profits(self):
        """MODIFY_TARGETS operation must have at least one take_profit."""
        with pytest.raises(ValidationError, match="non-empty take_profits"):
            UpdateOperation(
                op_type="MODIFY_TARGETS",
                modify_targets=ModifyTargetsOperation(mode="REPLACE_ALL", take_profits=[]),
            )

    def test_close_operation_all_none_raises(self):
        """CLOSE without any of fraction/price/scope → ValidationError."""
        with pytest.raises(ValidationError, match="at least one of"):
            CloseOperation()

    def test_stop_target_price_without_value(self):
        """PRICE stop target requires numeric value."""
        with pytest.raises(ValidationError, match="numeric value"):
            StopTarget(target_type="PRICE", value=None)

    def test_stop_target_tp_level_with_float(self):
        """TP_LEVEL stop target requires integer level."""
        with pytest.raises(ValidationError, match="integer level"):
            StopTarget(target_type="TP_LEVEL", value=1.5)

    def test_extra_field_on_canonical_message_forbidden(self):
        """extra='forbid' must reject unknown fields on CanonicalMessage."""
        with pytest.raises(ValidationError):
            CanonicalMessage(**_signal_base(unknown_field="bad"))  # type: ignore[arg-type]
