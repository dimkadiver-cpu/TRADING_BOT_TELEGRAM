"""Tests for operational models (OperationalSignal, ResolvedTarget, ResolvedSignal)
and the NewSignalEntities.check_entry_magnitude_consistency validator.

Coverage:
  - OperationalSignal: construction, defaults, blocked state, entry_split
  - ResolvedTarget: dataclass slots, all eligibility values
  - ResolvedSignal: composition, is_ready semantics, arbitrary_types_allowed
  - check_entry_magnitude_consistency: ratio > 3x → warning; ratio <= 3x → no warning;
    < 2 entries → no check; MARKET entries with None prices skipped
"""

from __future__ import annotations

import pytest

from src.parser.canonical_v1.models import CanonicalMessage, RawContext
from src.parser.models.canonical import Price, TraderParseResult
from src.parser.models.new_signal import EntryLevel, NewSignalEntities, StopLoss, TakeProfit
from src.parser.models.operational import OperationalSignal, ResolvedSignal, ResolvedTarget


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_result(message_type: str = "NEW_SIGNAL") -> TraderParseResult:
    if message_type == "NEW_SIGNAL":
        return TraderParseResult(
            message_type="NEW_SIGNAL",
            completeness="COMPLETE",
            trader_id="trader_3",
            raw_text="BTCUSDT LONG\nSL 90000\nTP 95000",
        )
    return TraderParseResult(
        message_type="UPDATE",
        trader_id="trader_3",
        raw_text="move SL to BE",
    )


def _entry(value: float) -> EntryLevel:
    return EntryLevel(price=Price.from_float(value), order_type="LIMIT")


def _canonical_message(primary_class: str = "INFO") -> CanonicalMessage:
    return CanonicalMessage(
        parser_profile="trader_3",
        primary_class=primary_class,  # type: ignore[arg-type]
        parse_status="PARSED",
        confidence=0.9,
        raw_context=RawContext(raw_text="test"),
    )


# ---------------------------------------------------------------------------
# check_entry_magnitude_consistency
# ---------------------------------------------------------------------------

class TestEntryMagnitudeConsistency:
    def test_no_entries_no_warning(self) -> None:
        e = NewSignalEntities(entry_type="MARKET")
        assert e.warnings == []

    def test_single_entry_no_warning(self) -> None:
        e = NewSignalEntities(
            entry_type="LIMIT",
            entries=[_entry(90000.0)],
        )
        assert e.warnings == []

    def test_ratio_exactly_3_no_warning(self) -> None:
        """Ratio == 3.0 is not > 3.0, so no warning."""
        e = NewSignalEntities(
            entry_type="AVERAGING",
            entries=[_entry(10000.0), _entry(30000.0)],
        )
        assert e.warnings == []

    def test_ratio_just_above_3_adds_warning(self) -> None:
        """Ratio 30001/10000 > 3.0 → warning."""
        e = NewSignalEntities(
            entry_type="AVERAGING",
            entries=[_entry(10000.0), _entry(30001.0)],
        )
        assert len(e.warnings) == 1
        assert "entry_magnitude_inconsistent" in e.warnings[0]
        assert "ratio=" in e.warnings[0]

    def test_ratio_large_adds_warning_with_formatted_ratio(self) -> None:
        # 95000 / 9500 = 10.0
        e = NewSignalEntities(
            entry_type="AVERAGING",
            entries=[_entry(9500.0), _entry(95000.0)],
        )
        assert len(e.warnings) == 1
        assert "10.0" in e.warnings[0]

    def test_three_entries_high_ratio_warning(self) -> None:
        # max=90000, min=10000, ratio=9
        e = NewSignalEntities(
            entry_type="AVERAGING",
            entries=[_entry(10000.0), _entry(50000.0), _entry(90000.0)],
        )
        assert len(e.warnings) == 1

    def test_three_entries_normal_ratio_no_warning(self) -> None:
        # max=92000, min=88000, ratio ~1.045
        e = NewSignalEntities(
            entry_type="ZONE",
            entries=[_entry(88000.0), _entry(90000.0), _entry(92000.0)],
        )
        assert e.warnings == []

    def test_market_entries_with_none_price_skipped(self) -> None:
        """MARKET entries without a price should not cause a ZeroDivisionError
        or false warning — None prices are excluded from the ratio check."""
        e = NewSignalEntities(
            entry_type="MARKET",
            entries=[
                EntryLevel(price=None, order_type="MARKET"),
                EntryLevel(price=None, order_type="MARKET"),
            ],
        )
        assert e.warnings == []

    def test_mixed_none_and_priced_entries_only_one_priced_no_warning(self) -> None:
        """When only one entry has a price, ratio check is skipped."""
        e = NewSignalEntities(
            entry_type="AVERAGING",
            entries=[
                EntryLevel(price=None, order_type="MARKET"),
                _entry(90000.0),
            ],
        )
        assert e.warnings == []

    def test_warning_does_not_prevent_construction(self) -> None:
        """Entry magnitude warning must never raise — only appends."""
        e = NewSignalEntities(
            entry_type="AVERAGING",
            entries=[_entry(1.0), _entry(100.0)],  # ratio=100 >> 3
        )
        assert e.entry_type == "AVERAGING"
        assert len(e.warnings) == 1

    def test_tp_and_sl_not_affected(self) -> None:
        """TP and SL values — even extreme ones — must not trigger the check."""
        sl = StopLoss(price=Price.from_float(1.0))
        tp = TakeProfit(price=Price.from_float(100000.0))
        e = NewSignalEntities(
            entry_type="MARKET",
            stop_loss=sl,
            take_profits=[tp],
        )
        assert e.warnings == []


# ---------------------------------------------------------------------------
# OperationalSignal
# ---------------------------------------------------------------------------

class TestOperationalSignal:
    def test_minimal_construction(self) -> None:
        pr = _parse_result()
        op = OperationalSignal(parse_result=pr)
        assert op.parse_result is pr
        assert op.canonical_message.primary_class == "SIGNAL"
        assert op.is_blocked is False
        assert op.block_reason is None
        assert op.position_size_pct is None
        assert op.position_size_usdt is None
        assert op.entry_split is None
        assert op.leverage is None
        assert op.risk_hint_used is False
        assert op.sizing_deferred is False
        assert op.management_rules is None
        assert op.applied_rules == []
        assert op.warnings == []

    def test_accepts_canonical_message_directly(self) -> None:
        msg = _canonical_message()
        op = OperationalSignal(canonical_message=msg, sizing_deferred=True)
        assert op.canonical_message is msg
        assert op.parse_result is msg
        assert op.sizing_deferred is True

    def test_blocked_state(self) -> None:
        pr = _parse_result()
        op = OperationalSignal(
            parse_result=pr,
            is_blocked=True,
            block_reason="trader_disabled",
        )
        assert op.is_blocked is True
        assert op.block_reason == "trader_disabled"

    def test_new_signal_with_sizing_params(self) -> None:
        pr = _parse_result()
        op = OperationalSignal(
            parse_result=pr,
            position_size_pct=1.0,
            position_size_usdt=500.0,
            entry_split={"E1": 0.5, "E2": 0.5},
            leverage=10,
            risk_hint_used=True,
        )
        assert op.position_size_pct == 1.0
        assert op.position_size_usdt == 500.0
        assert op.entry_split == {"E1": 0.5, "E2": 0.5}
        assert op.leverage == 10
        assert op.risk_hint_used is True

    def test_management_rules_snapshot(self) -> None:
        rules = {
            "tp": {"use_tp_count": 2},
            "sl": {"be_trigger": "tp1"},
            "updates": {"apply_move_stop": True},
            "pending": {"cancel_pending_by_engine": True},
        }
        pr = _parse_result()
        op = OperationalSignal(parse_result=pr, management_rules=rules)
        assert op.management_rules == rules

    def test_applied_rules_and_warnings(self) -> None:
        pr = _parse_result()
        op = OperationalSignal(
            parse_result=pr,
            applied_rules=["global_defaults", "trader_override"],
            warnings=["price_sanity_skipped"],
        )
        assert op.applied_rules == ["global_defaults", "trader_override"]
        assert op.warnings == ["price_sanity_skipped"]

    def test_update_type_passes_through(self) -> None:
        pr = _parse_result("UPDATE")
        op = OperationalSignal(parse_result=pr, management_rules={"auto_apply": ["U_MOVE_STOP"]})
        assert op.parse_result.message_type == "UPDATE"
        assert op.management_rules is not None


# ---------------------------------------------------------------------------
# ResolvedTarget
# ---------------------------------------------------------------------------

class TestResolvedTarget:
    def test_eligible(self) -> None:
        rt = ResolvedTarget(kind="SYMBOL", position_ids=[1, 2], eligibility="ELIGIBLE", reason=None)
        assert rt.kind == "SYMBOL"
        assert rt.position_ids == [1, 2]
        assert rt.eligibility == "ELIGIBLE"
        assert rt.reason is None

    def test_ineligible_with_reason(self) -> None:
        rt = ResolvedTarget(
            kind="STRONG",
            position_ids=[],
            eligibility="INELIGIBLE",
            reason="position already closed",
        )
        assert rt.eligibility == "INELIGIBLE"
        assert rt.reason == "position already closed"

    def test_warn(self) -> None:
        rt = ResolvedTarget(kind="SYMBOL", position_ids=[5], eligibility="WARN", reason="pending order")
        assert rt.eligibility == "WARN"

    def test_unresolved(self) -> None:
        rt = ResolvedTarget(kind="STRONG", position_ids=[], eligibility="UNRESOLVED", reason=None)
        assert rt.eligibility == "UNRESOLVED"
        assert rt.position_ids == []

    def test_global_kind(self) -> None:
        rt = ResolvedTarget(kind="GLOBAL", position_ids=[1, 3, 7], eligibility="ELIGIBLE", reason=None)
        assert rt.kind == "GLOBAL"

    def test_is_dataclass_with_slots(self) -> None:
        """Verify slots=True is effective — __dict__ should not exist."""
        rt = ResolvedTarget(kind="SYMBOL", position_ids=[], eligibility="ELIGIBLE", reason=None)
        assert not hasattr(rt, "__dict__")

    def test_mutable_position_ids(self) -> None:
        """ResolvedTarget is a plain dataclass — fields are mutable."""
        rt = ResolvedTarget(kind="SYMBOL", position_ids=[1], eligibility="ELIGIBLE", reason=None)
        rt.position_ids.append(2)
        assert rt.position_ids == [1, 2]


# ---------------------------------------------------------------------------
# ResolvedSignal
# ---------------------------------------------------------------------------

class TestResolvedSignal:
    def _make_op(self, blocked: bool = False) -> OperationalSignal:
        return OperationalSignal(
            parse_result=_parse_result(),
            is_blocked=blocked,
            block_reason="trader_disabled" if blocked else None,
        )

    def test_new_signal_ready_no_target(self) -> None:
        op = self._make_op()
        rs = ResolvedSignal(operational=op, resolved_target=None, is_ready=True)
        assert rs.is_ready is True
        assert rs.resolved_target is None

    def test_blocked_not_ready(self) -> None:
        op = self._make_op(blocked=True)
        rs = ResolvedSignal(operational=op, resolved_target=None, is_ready=False)
        assert rs.is_ready is False
        assert rs.operational.is_blocked is True

    def test_with_eligible_target_ready(self) -> None:
        op = self._make_op()
        rt = ResolvedTarget(kind="SYMBOL", position_ids=[10], eligibility="ELIGIBLE", reason=None)
        rs = ResolvedSignal(operational=op, resolved_target=rt, is_ready=True)
        assert rs.resolved_target is not None
        assert rs.resolved_target.eligibility == "ELIGIBLE"
        assert rs.is_ready is True

    def test_with_warn_target_ready(self) -> None:
        """WARN is not a hard blocker — is_ready can be True."""
        op = self._make_op()
        rt = ResolvedTarget(kind="SYMBOL", position_ids=[3], eligibility="WARN", reason="pending")
        rs = ResolvedSignal(operational=op, resolved_target=rt, is_ready=True)
        assert rs.is_ready is True

    def test_with_ineligible_target_not_ready(self) -> None:
        op = self._make_op()
        rt = ResolvedTarget(kind="STRONG", position_ids=[], eligibility="INELIGIBLE", reason="closed")
        rs = ResolvedSignal(operational=op, resolved_target=rt, is_ready=False)
        assert rs.is_ready is False

    def test_with_unresolved_target_not_ready(self) -> None:
        op = self._make_op()
        rt = ResolvedTarget(kind="STRONG", position_ids=[], eligibility="UNRESOLVED", reason=None)
        rs = ResolvedSignal(operational=op, resolved_target=rt, is_ready=False)
        assert rs.is_ready is False

    def test_composition_parse_result_accessible(self) -> None:
        """Verify deep composition: resolved_signal → operational → parse_result."""
        op = self._make_op()
        rs = ResolvedSignal(operational=op, resolved_target=None, is_ready=True)
        assert rs.operational.parse_result.trader_id == "trader_3"
        assert rs.operational.canonical_message.primary_class == "SIGNAL"

    def test_update_message_type(self) -> None:
        pr = _parse_result("UPDATE")
        op = OperationalSignal(parse_result=pr)
        rt = ResolvedTarget(kind="SYMBOL", position_ids=[7], eligibility="ELIGIBLE", reason=None)
        rs = ResolvedSignal(operational=op, resolved_target=rt, is_ready=True)
        assert rs.operational.parse_result.message_type == "UPDATE"
