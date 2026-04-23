"""Factory functions for canonical v1 test fixtures.

Each function returns a valid CanonicalMessage instance representing a specific
message type / entry structure combination. Used by test_canonical_v1_schema.py.
"""

from __future__ import annotations

from src.parser.canonical_v1.models import (
    CancelPendingOperation,
    CanonicalMessage,
    CloseOperation,
    EntryLeg,
    ModifyEntriesOperation,
    ModifyTargetsOperation,
    Price,
    RawContext,
    ReportEvent,
    ReportPayload,
    ReportedResult,
    SignalPayload,
    StopLoss,
    StopTarget,
    TakeProfit,
    Targeting,
    TargetRef,
    TargetScope,
    UpdateOperation,
    UpdatePayload,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _raw(text: str = "raw message text") -> RawContext:
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


def _targeting_reply(msg_id: int = 100) -> Targeting:
    return Targeting(
        refs=[TargetRef(ref_type="REPLY", value=msg_id)],
        scope=TargetScope(kind="SINGLE_SIGNAL"),
        strategy="REPLY_OR_LINK",
        targeted=True,
    )


# ---------------------------------------------------------------------------
# SIGNAL fixtures
# ---------------------------------------------------------------------------

def signal_one_shot() -> CanonicalMessage:
    """SIGNAL ONE_SHOT — MARKET entry, stop, one TP. Fully PARSED."""
    return CanonicalMessage(
        parser_profile="trader_a",
        primary_class="SIGNAL",
        parse_status="PARSED",
        confidence=0.95,
        intents=["NS_CREATE_SIGNAL"],
        primary_intent="NS_CREATE_SIGNAL",
        raw_context=_raw("BTC LONG market sl 44000 tp 46000"),
        signal=SignalPayload(
            symbol="BTC/USDT",
            side="LONG",
            entry_structure="ONE_SHOT",
            entries=[_market_leg(1)],
            stop_loss=_stop("44000", 44000.0),
            take_profits=[_tp(1, "46000", 46000.0)],
            completeness="COMPLETE",
        ),
    )


def signal_two_step() -> CanonicalMessage:
    """SIGNAL TWO_STEP — two LIMIT entries."""
    return CanonicalMessage(
        parser_profile="trader_a",
        primary_class="SIGNAL",
        parse_status="PARSED",
        confidence=0.90,
        intents=["NS_CREATE_SIGNAL"],
        primary_intent="NS_CREATE_SIGNAL",
        raw_context=_raw("ETH SHORT 2000 2050 sl 2100 tp 1900 1800"),
        signal=SignalPayload(
            symbol="ETH/USDT",
            side="SHORT",
            entry_structure="TWO_STEP",
            entries=[
                _limit_leg(1, "2000", 2000.0),
                _limit_leg(2, "2050", 2050.0),
            ],
            stop_loss=_stop("2100", 2100.0),
            take_profits=[
                _tp(1, "1900", 1900.0),
                _tp(2, "1800", 1800.0),
            ],
            completeness="COMPLETE",
        ),
    )


def signal_range() -> CanonicalMessage:
    """SIGNAL RANGE — two prices as min/max zone."""
    return CanonicalMessage(
        parser_profile="trader_b",
        primary_class="SIGNAL",
        parse_status="PARSED",
        confidence=0.85,
        intents=["NS_CREATE_SIGNAL"],
        primary_intent="NS_CREATE_SIGNAL",
        raw_context=_raw("SOL LONG zone 90-95 sl 85 tp 105"),
        signal=SignalPayload(
            symbol="SOL/USDT",
            side="LONG",
            entry_structure="RANGE",
            entries=[
                EntryLeg(sequence=1, entry_type="LIMIT", price=_price("90", 90.0), role="PRIMARY"),
                EntryLeg(sequence=2, entry_type="LIMIT", price=_price("95", 95.0), role="PRIMARY"),
            ],
            stop_loss=_stop("85", 85.0),
            take_profits=[_tp(1, "105", 105.0)],
            completeness="COMPLETE",
        ),
    )


def signal_ladder() -> CanonicalMessage:
    """SIGNAL LADDER — 3 LIMIT entries."""
    return CanonicalMessage(
        parser_profile="trader_b",
        primary_class="SIGNAL",
        parse_status="PARSED",
        confidence=0.88,
        intents=["NS_CREATE_SIGNAL"],
        primary_intent="NS_CREATE_SIGNAL",
        raw_context=_raw("AVAX LONG 30 28 26 sl 24 tp 35 38"),
        signal=SignalPayload(
            symbol="AVAX/USDT",
            side="LONG",
            entry_structure="LADDER",
            entries=[
                _limit_leg(1, "30", 30.0),
                _limit_leg(2, "28", 28.0),
                _limit_leg(3, "26", 26.0),
            ],
            stop_loss=_stop("24", 24.0),
            take_profits=[_tp(1, "35", 35.0), _tp(2, "38", 38.0)],
            completeness="COMPLETE",
        ),
    )


def signal_partial() -> CanonicalMessage:
    """SIGNAL PARTIAL — missing side and stop, parse_status=PARTIAL."""
    return CanonicalMessage(
        parser_profile="trader_c",
        primary_class="SIGNAL",
        parse_status="PARTIAL",
        confidence=0.45,
        intents=["NS_CREATE_SIGNAL"],
        primary_intent="NS_CREATE_SIGNAL",
        raw_context=_raw("BNB entry 300 tp 320"),
        signal=SignalPayload(
            symbol="BNB/USDT",
            side=None,
            entry_structure="ONE_SHOT",
            entries=[_limit_leg(1, "300", 300.0)],
            stop_loss=None,
            take_profits=[_tp(1, "320", 320.0)],
            completeness="INCOMPLETE",
            missing_fields=["side", "stop_loss"],
        ),
    )


# ---------------------------------------------------------------------------
# UPDATE fixtures
# ---------------------------------------------------------------------------

def update_set_stop_price() -> CanonicalMessage:
    """UPDATE SET_STOP — move stop to explicit price."""
    return CanonicalMessage(
        parser_profile="trader_a",
        primary_class="UPDATE",
        parse_status="PARSED",
        confidence=0.92,
        intents=["U_MOVE_STOP"],
        primary_intent="U_MOVE_STOP",
        targeting=_targeting_reply(101),
        raw_context=_raw("move sl to 43500"),
        update=UpdatePayload(
            operations=[
                UpdateOperation(
                    op_type="SET_STOP",
                    set_stop=StopTarget(target_type="PRICE", value=43500.0),
                )
            ]
        ),
    )


def update_set_stop_entry() -> CanonicalMessage:
    """UPDATE SET_STOP — move stop to ENTRY (breakeven)."""
    return CanonicalMessage(
        parser_profile="trader_a",
        primary_class="UPDATE",
        parse_status="PARSED",
        confidence=0.95,
        intents=["U_MOVE_STOP_TO_BE"],
        primary_intent="U_MOVE_STOP_TO_BE",
        targeting=_targeting_reply(102),
        raw_context=_raw("sl to entry"),
        update=UpdatePayload(
            operations=[
                UpdateOperation(
                    op_type="SET_STOP",
                    set_stop=StopTarget(target_type="ENTRY", value=None),
                )
            ]
        ),
    )


def update_set_stop_tp_level() -> CanonicalMessage:
    """UPDATE SET_STOP — move stop to TP level 1."""
    return CanonicalMessage(
        parser_profile="trader_a",
        primary_class="UPDATE",
        parse_status="PARSED",
        confidence=0.90,
        intents=["U_MOVE_STOP"],
        primary_intent="U_MOVE_STOP",
        targeting=_targeting_reply(103),
        raw_context=_raw("move sl to tp1"),
        update=UpdatePayload(
            operations=[
                UpdateOperation(
                    op_type="SET_STOP",
                    set_stop=StopTarget(target_type="TP_LEVEL", value=1),
                )
            ]
        ),
    )


def update_close_full() -> CanonicalMessage:
    """UPDATE CLOSE — close full position."""
    return CanonicalMessage(
        parser_profile="trader_a",
        primary_class="UPDATE",
        parse_status="PARSED",
        confidence=0.98,
        intents=["U_CLOSE_FULL"],
        primary_intent="U_CLOSE_FULL",
        targeting=_targeting_reply(104),
        raw_context=_raw("close all"),
        update=UpdatePayload(
            operations=[
                UpdateOperation(
                    op_type="CLOSE",
                    close=CloseOperation(close_scope="FULL"),
                )
            ]
        ),
    )


def update_close_partial() -> CanonicalMessage:
    """UPDATE CLOSE — close 50% of position."""
    return CanonicalMessage(
        parser_profile="trader_a",
        primary_class="UPDATE",
        parse_status="PARSED",
        confidence=0.92,
        intents=["U_CLOSE_PARTIAL"],
        primary_intent="U_CLOSE_PARTIAL",
        targeting=_targeting_reply(105),
        raw_context=_raw("close 50%"),
        update=UpdatePayload(
            operations=[
                UpdateOperation(
                    op_type="CLOSE",
                    close=CloseOperation(close_fraction=0.50),
                )
            ]
        ),
    )


def update_cancel_pending() -> CanonicalMessage:
    """UPDATE CANCEL_PENDING — cancel all pending orders."""
    return CanonicalMessage(
        parser_profile="trader_a",
        primary_class="UPDATE",
        parse_status="PARSED",
        confidence=0.95,
        intents=["U_CANCEL_PENDING_ORDERS"],
        primary_intent="U_CANCEL_PENDING_ORDERS",
        targeting=_targeting_reply(106),
        raw_context=_raw("cancel pending"),
        update=UpdatePayload(
            operations=[
                UpdateOperation(
                    op_type="CANCEL_PENDING",
                    cancel_pending=CancelPendingOperation(cancel_scope="ALL"),
                )
            ]
        ),
    )


def update_modify_entries_add() -> CanonicalMessage:
    """UPDATE MODIFY_ENTRIES ADD — add one entry leg."""
    return CanonicalMessage(
        parser_profile="trader_a",
        primary_class="UPDATE",
        parse_status="PARSED",
        confidence=0.88,
        intents=["U_ADD_ENTRY"],
        primary_intent="U_ADD_ENTRY",
        targeting=_targeting_reply(107),
        raw_context=_raw("add entry at 43000"),
        update=UpdatePayload(
            operations=[
                UpdateOperation(
                    op_type="MODIFY_ENTRIES",
                    modify_entries=ModifyEntriesOperation(
                        mode="ADD",
                        entries=[_limit_leg(1, "43000", 43000.0)],
                    ),
                )
            ]
        ),
    )


def update_modify_targets_replace_all() -> CanonicalMessage:
    """UPDATE MODIFY_TARGETS REPLACE_ALL — replace all TPs."""
    return CanonicalMessage(
        parser_profile="trader_a",
        primary_class="UPDATE",
        parse_status="PARSED",
        confidence=0.90,
        intents=["U_UPDATE_TAKE_PROFITS"],
        primary_intent="U_UPDATE_TAKE_PROFITS",
        targeting=_targeting_reply(108),
        raw_context=_raw("new targets 47000 49000"),
        update=UpdatePayload(
            operations=[
                UpdateOperation(
                    op_type="MODIFY_TARGETS",
                    modify_targets=ModifyTargetsOperation(
                        mode="REPLACE_ALL",
                        take_profits=[
                            _tp(1, "47000", 47000.0),
                            _tp(2, "49000", 49000.0),
                        ],
                    ),
                )
            ]
        ),
    )


# ---------------------------------------------------------------------------
# REPORT fixtures
# ---------------------------------------------------------------------------

def report_tp_hit() -> CanonicalMessage:
    """REPORT TP_HIT — TP level 1 hit."""
    return CanonicalMessage(
        parser_profile="trader_a",
        primary_class="REPORT",
        parse_status="PARSED",
        confidence=0.99,
        intents=["U_TP_HIT"],
        primary_intent="U_TP_HIT",
        targeting=_targeting_reply(109),
        raw_context=_raw("TP1 hit at 46000"),
        report=ReportPayload(
            events=[
                ReportEvent(
                    event_type="TP_HIT",
                    level=1,
                    price=_price("46000", 46000.0),
                )
            ]
        ),
    )


def report_stop_hit() -> CanonicalMessage:
    """REPORT STOP_HIT — stop triggered."""
    return CanonicalMessage(
        parser_profile="trader_a",
        primary_class="REPORT",
        parse_status="PARSED",
        confidence=0.99,
        intents=["U_STOP_HIT"],
        primary_intent="U_STOP_HIT",
        targeting=_targeting_reply(110),
        raw_context=_raw("sl hit"),
        report=ReportPayload(
            events=[
                ReportEvent(event_type="STOP_HIT", price=_price("44000", 44000.0))
            ]
        ),
    )


def report_final_result() -> CanonicalMessage:
    """REPORT FINAL_RESULT — trade closed with +2R result."""
    return CanonicalMessage(
        parser_profile="trader_a",
        primary_class="REPORT",
        parse_status="PARSED",
        confidence=0.95,
        intents=["U_REPORT_FINAL_RESULT"],
        primary_intent="U_REPORT_FINAL_RESULT",
        targeting=_targeting_reply(111),
        raw_context=_raw("trade closed +2R"),
        report=ReportPayload(
            events=[ReportEvent(event_type="FINAL_RESULT")],
            reported_result=ReportedResult(value=2.0, unit="R"),
        ),
    )


# ---------------------------------------------------------------------------
# Composite: UPDATE + REPORT
# ---------------------------------------------------------------------------

def update_plus_report() -> CanonicalMessage:
    """UPDATE SET_STOP + REPORT TP_HIT — composite message."""
    return CanonicalMessage(
        parser_profile="trader_a",
        primary_class="UPDATE",
        parse_status="PARSED",
        confidence=0.88,
        intents=["U_TP_HIT", "U_MOVE_STOP_TO_BE"],
        primary_intent="U_MOVE_STOP_TO_BE",
        targeting=_targeting_reply(112),
        raw_context=_raw("tp1 hit, moving sl to entry"),
        update=UpdatePayload(
            operations=[
                UpdateOperation(
                    op_type="SET_STOP",
                    set_stop=StopTarget(target_type="ENTRY", value=None),
                )
            ]
        ),
        report=ReportPayload(
            events=[ReportEvent(event_type="TP_HIT", level=1)]
        ),
    )


# ---------------------------------------------------------------------------
# INFO fixture
# ---------------------------------------------------------------------------

def info_pure() -> CanonicalMessage:
    """INFO — no operational payload, just informational text."""
    return CanonicalMessage(
        parser_profile="trader_a",
        primary_class="INFO",
        parse_status="PARSED",
        confidence=0.70,
        intents=["U_RISK_NOTE"],
        primary_intent="U_RISK_NOTE",
        raw_context=_raw("market is volatile, be careful"),
    )


# ---------------------------------------------------------------------------
# All valid fixtures as a list (for parametrize)
# ---------------------------------------------------------------------------

ALL_VALID: list[tuple[str, object]] = [
    ("signal_one_shot", signal_one_shot),
    ("signal_two_step", signal_two_step),
    ("signal_range", signal_range),
    ("signal_ladder", signal_ladder),
    ("signal_partial", signal_partial),
    ("update_set_stop_price", update_set_stop_price),
    ("update_set_stop_entry", update_set_stop_entry),
    ("update_set_stop_tp_level", update_set_stop_tp_level),
    ("update_close_full", update_close_full),
    ("update_close_partial", update_close_partial),
    ("update_cancel_pending", update_cancel_pending),
    ("update_modify_entries_add", update_modify_entries_add),
    ("update_modify_targets_replace_all", update_modify_targets_replace_all),
    ("report_tp_hit", report_tp_hit),
    ("report_stop_hit", report_stop_hit),
    ("report_final_result", report_final_result),
    ("update_plus_report", update_plus_report),
    ("info_pure", info_pure),
]
