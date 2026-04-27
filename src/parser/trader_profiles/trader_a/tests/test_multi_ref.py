"""Phase 2 — test_multi_ref.py

Tests for targeted_actions / targeted_reports in CanonicalMessage
produced by trader_a parse_canonical() on multi-ref messages.

Cases from PIANO_INCREMENTAZIONE_MULTI_REF.md:
  Caso 1: two refs + common action → one targeted_action (TARGET_GROUP)
  Caso 2: four refs + common action + per-ref result → targeted_action + four targeted_report
  Caso 3: five refs with heterogeneous actions → two targeted_action (distinct targets)
  Extra: single-ref → no targeted_actions / targeted_reports
  Extra: ambiguous price-stop → "targeted_binding_ambiguous" warning, empty targeted_actions
"""

from __future__ import annotations

import pytest

from src.parser.trader_profiles.base import ParserContext
from src.parser.trader_profiles.trader_a.profile import TraderAProfileParser


def _ctx(text: str) -> ParserContext:
    return ParserContext(
        trader_code="trader_a",
        message_id=9000,
        reply_to_message_id=None,
        channel_id="3171748254",
        raw_text=text,
        extracted_links=[],
        hashtags=[],
    )


CHANNEL = "c/3171748254"


# ---------------------------------------------------------------------------
# Caso 1: common close on two refs → single TARGET_GROUP targeted_action
# ---------------------------------------------------------------------------

CASE1_TEXT = (
    f"XRP - https://t.me/{CHANNEL}/1015\n"
    f"ADA - https://t.me/{CHANNEL}/1017\n\n"
    "А давайте их прикроем, пока они рядом с ТВХ"
)


def test_caso1_two_refs_common_close_produces_single_targeted_action() -> None:
    parser = TraderAProfileParser()
    msg = parser.parse_canonical(CASE1_TEXT, _ctx(CASE1_TEXT))

    assert msg.primary_class == "UPDATE"
    assert len(msg.targeted_actions) == 1

    action = msg.targeted_actions[0]
    assert action.action_type == "CLOSE"
    assert action.targeting.mode == "TARGET_GROUP"
    assert sorted(action.targeting.targets) == [1015, 1017]


# ---------------------------------------------------------------------------
# Caso 2: four refs + common action + per-ref result → targeted_action + 4 targeted_report
# ---------------------------------------------------------------------------

CASE2_TEXT = (
    f"XRP - https://t.me/{CHANNEL}/1015 +3R\n"
    f"ADA - https://t.me/{CHANNEL}/1017 +2R\n"
    f"BTC - https://t.me/{CHANNEL}/1020 -1.5R\n"
    f"ETH - https://t.me/{CHANNEL}/1022 +1R\n\n"
    "давайте прикроем"
)


def test_caso2_four_refs_with_per_ref_results_produces_action_and_reports() -> None:
    parser = TraderAProfileParser()
    msg = parser.parse_canonical(CASE2_TEXT, _ctx(CASE2_TEXT))

    assert msg.primary_class == "UPDATE"
    # Exactly one shared targeted_action
    assert len(msg.targeted_actions) == 1
    action = msg.targeted_actions[0]
    assert action.action_type == "CLOSE"
    assert action.targeting.mode == "TARGET_GROUP"
    assert sorted(action.targeting.targets) == [1015, 1017, 1020, 1022]

    # Four targeted_report (one per ref)
    assert len(msg.targeted_reports) == 4
    message_ids = sorted(r.targeting.targets[0] for r in msg.targeted_reports)
    assert message_ids == [1015, 1017, 1020, 1022]

    # All results have a numeric value
    for report in msg.targeted_reports:
        assert report.result is not None
        assert report.result.value is not None
        assert report.result.unit in ("R", "PERCENT")


# ---------------------------------------------------------------------------
# Caso 3: five refs with heterogeneous stops → two targeted_action (distinct targets)
# ---------------------------------------------------------------------------

CASE3_TEXT = (
    f"[trader#A]\n\n"
    f"LINK - https://t.me/{CHANNEL}/978 - стоп в бу\n"
    f"ALGO - https://t.me/{CHANNEL}/1002 стоп в бу\n"
    f"ARKM - https://t.me/{CHANNEL}/1003 стоп в бу\n"
    f"FART - https://t.me/{CHANNEL}/1005 стоп на 1 тейк\n"
    f"UNI - https://t.me/{CHANNEL}/1018 стоп в бу"
)


def test_caso3_five_refs_heterogeneous_stops_produces_two_targeted_actions() -> None:
    parser = TraderAProfileParser()
    msg = parser.parse_canonical(CASE3_TEXT, _ctx(CASE3_TEXT))

    assert msg.primary_class == "UPDATE"
    assert len(msg.targeted_actions) == 2

    by_sig = {a.diagnostics.semantic_signature: a for a in msg.targeted_actions if a.diagnostics}
    entry_action = by_sig.get("SET_STOP:ENTRY")
    tp1_action = by_sig.get("SET_STOP:TP1")

    assert entry_action is not None, "Missing SET_STOP:ENTRY action"
    assert tp1_action is not None, "Missing SET_STOP:TP1 action"

    assert sorted(entry_action.targeting.targets) == [978, 1002, 1003, 1018]
    assert tp1_action.targeting.targets == [1005]


# ---------------------------------------------------------------------------
# Single-ref: no targeted_actions / targeted_reports
# ---------------------------------------------------------------------------

SINGLE_REF_TEXT = (
    "BTCUSDT Лонг\n"
    "Вход с текущих: 45000\n"
    "SL: 44000\n"
    "TP1: 47000"
)


def test_single_ref_message_produces_no_targeted_actions() -> None:
    parser = TraderAProfileParser()
    msg = parser.parse_canonical(SINGLE_REF_TEXT, _ctx(SINGLE_REF_TEXT))

    assert msg.targeted_actions == []
    assert msg.targeted_reports == []


# ---------------------------------------------------------------------------
# Ambiguous: explicit-price stop → targeted_binding_ambiguous warning
# ---------------------------------------------------------------------------

AMBIGUOUS_TEXT = f"DOT - https://t.me/{CHANNEL}/2001 стоп переставляем на 1.2450"


def test_ambiguous_price_stop_emits_warning_and_empty_targeted_actions() -> None:
    parser = TraderAProfileParser()
    msg = parser.parse_canonical(AMBIGUOUS_TEXT, _ctx(AMBIGUOUS_TEXT))

    assert msg.targeted_actions == []
    assert "targeted_binding_ambiguous" in msg.warnings
