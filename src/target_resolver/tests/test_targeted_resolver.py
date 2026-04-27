"""Tests for Fase 3 — resolve_targeted() in TargetResolver.

Covers:
  - TARGET_GROUP: tutti i target trovati → ELIGIBLE con tutti i position_ids
  - TARGET_GROUP: un target non trovato → NOT_FOUND
  - EXPLICIT_TARGETS: firme diverse → ResolvedActionItem separati
  - SELECTOR: filtra per side SHORT → solo posizioni SELL
  - CanonicalMessage senza targeted_actions → risultato vuoto, nessun errore
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.core.migrations import apply_migrations
from src.parser.canonical_v1.models import (
    CanonicalMessage,
    RawContext,
    TargetedAction,
    TargetedActionTargeting,
    UpdatePayload,
)
from src.target_resolver.resolver import resolve_targeted


# ---------------------------------------------------------------------------
# DB helpers (riusati da test_resolver.py)
# ---------------------------------------------------------------------------


def _make_db(tmp_path: Path) -> str:
    db_path = str(tmp_path / "targeted_resolver_test.sqlite3")
    apply_migrations(db_path=db_path, migrations_dir=str(Path("db/migrations").resolve()))
    return db_path


def _insert_signal(
    db_path: str,
    *,
    attempt_key: str,
    trader_id: str,
    symbol: str = "BTCUSDT",
    side: str = "BUY",
    status: str = "ACTIVE",
    root_telegram_id: str,
) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO signals
               (attempt_key, env, channel_id, root_telegram_id, trader_id, trader_prefix,
                symbol, side, entry_json, sl, tp_json, status, confidence, raw_text,
                created_at, updated_at, trader_signal_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (attempt_key, "T", "100", root_telegram_id, trader_id, "TP",
             symbol, side, "[]", 55000.0, "[]", status, 0.9, "test",
             "2026-01-01", "2026-01-01", None),
        )
        conn.commit()


def _insert_op_signal(
    db_path: str, *, parse_result_id: int, attempt_key: str, trader_id: str
) -> int:
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """INSERT INTO operational_signals
               (parse_result_id, attempt_key, trader_id, message_type, is_blocked, created_at)
               VALUES (?,?,?,?,?,?)""",
            (parse_result_id, attempt_key, trader_id, "UPDATE", 0, "2026-01-01"),
        )
        conn.commit()
        return int(cur.lastrowid)


def _insert_parse_result(db_path: str, *, raw_message_id: int = 1) -> int:
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """INSERT INTO parse_results
               (raw_message_id,eligibility_status,eligibility_reason,
                resolved_trader_id,trader_resolution_method,message_type,
                parse_status,completeness,is_executable,risky_flag,created_at,updated_at)
               VALUES (?,'OK','ok','tr_a','direct','UPDATE','PARSED','COMPLETE',1,0,
                       '2026-01-01','2026-01-01')""",
            (raw_message_id,),
        )
        conn.commit()
        return int(cur.lastrowid)


def _make_canonical_update(
    targeted_actions: list[TargetedAction] | None = None,
) -> CanonicalMessage:
    return CanonicalMessage(
        parser_profile="trader_a",
        primary_class="UPDATE",
        parse_status="PARTIAL",  # PARTIAL skips operation-count validation
        confidence=0.9,
        raw_context=RawContext(raw_text="test"),
        update=UpdatePayload(),
        targeted_actions=targeted_actions or [],
    )


def _make_targeted_action(
    action_type: str,
    mode: str,
    targets: list[int] | None = None,
    selector: dict | None = None,
) -> TargetedAction:
    if mode == "SELECTOR":
        targeting = TargetedActionTargeting(mode="SELECTOR", selector=selector or {})
    else:
        targeting = TargetedActionTargeting(mode=mode, targets=targets or [])  # type: ignore[arg-type]
    return TargetedAction(
        action_type=action_type,  # type: ignore[arg-type]
        params={},
        targeting=targeting,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTargetGroupBothFound:
    """TARGET_GROUP con due target entrambi trovati → ELIGIBLE + due position_ids."""

    def test_target_group_both_found_eligible(self, tmp_path: Path) -> None:
        db_path = _make_db(tmp_path)
        pr1 = _insert_parse_result(db_path, raw_message_id=1)
        pr2 = _insert_parse_result(db_path, raw_message_id=2)
        _insert_signal(db_path, attempt_key="T_100_10_tr_a", trader_id="tr_a",
                       root_telegram_id="10", status="ACTIVE")
        _insert_signal(db_path, attempt_key="T_100_20_tr_a", trader_id="tr_a",
                       root_telegram_id="20", status="ACTIVE")
        op_id_1 = _insert_op_signal(db_path, parse_result_id=pr1,
                                    attempt_key="T_100_10_tr_a", trader_id="tr_a")
        op_id_2 = _insert_op_signal(db_path, parse_result_id=pr2,
                                    attempt_key="T_100_20_tr_a", trader_id="tr_a")

        canonical = _make_canonical_update([
            _make_targeted_action("CLOSE", "TARGET_GROUP", targets=[10, 20]),
        ])

        result = resolve_targeted(canonical, trader_id="tr_a", db_path=db_path)

        assert len(result.resolved_actions) == 1
        item = result.resolved_actions[0]
        assert item.action_index == 0
        assert item.action_type == "CLOSE"
        assert item.eligibility == "ELIGIBLE"
        assert set(item.resolved_position_ids) == {op_id_1, op_id_2}


class TestTargetGroupOneNotFound:
    """TARGET_GROUP con un target non trovato → NOT_FOUND."""

    def test_target_group_one_missing_not_found(self, tmp_path: Path) -> None:
        db_path = _make_db(tmp_path)
        pr1 = _insert_parse_result(db_path, raw_message_id=1)
        _insert_signal(db_path, attempt_key="T_100_10_tr_a", trader_id="tr_a",
                       root_telegram_id="10", status="ACTIVE")
        _insert_op_signal(db_path, parse_result_id=pr1,
                          attempt_key="T_100_10_tr_a", trader_id="tr_a")
        # target 20 non inserito in DB

        canonical = _make_canonical_update([
            _make_targeted_action("CLOSE", "TARGET_GROUP", targets=[10, 20]),
        ])

        result = resolve_targeted(canonical, trader_id="tr_a", db_path=db_path)

        assert len(result.resolved_actions) == 1
        item = result.resolved_actions[0]
        assert item.eligibility == "NOT_FOUND"
        assert item.resolved_position_ids == []


class TestExplicitTargetsDistinctSignatures:
    """EXPLICIT_TARGETS con firme diverse → due ResolvedActionItem separati."""

    def test_two_actions_produce_two_items(self, tmp_path: Path) -> None:
        db_path = _make_db(tmp_path)
        pr1 = _insert_parse_result(db_path, raw_message_id=1)
        pr2 = _insert_parse_result(db_path, raw_message_id=2)
        _insert_signal(db_path, attempt_key="T_100_10_tr_a", trader_id="tr_a",
                       root_telegram_id="10", status="ACTIVE")
        _insert_signal(db_path, attempt_key="T_100_20_tr_a", trader_id="tr_a",
                       root_telegram_id="20", status="ACTIVE")
        op_id_1 = _insert_op_signal(db_path, parse_result_id=pr1,
                                    attempt_key="T_100_10_tr_a", trader_id="tr_a")
        op_id_2 = _insert_op_signal(db_path, parse_result_id=pr2,
                                    attempt_key="T_100_20_tr_a", trader_id="tr_a")

        canonical = _make_canonical_update([
            _make_targeted_action("CLOSE", "EXPLICIT_TARGETS", targets=[10]),
            _make_targeted_action("SET_STOP", "EXPLICIT_TARGETS", targets=[20]),
        ])

        result = resolve_targeted(canonical, trader_id="tr_a", db_path=db_path)

        assert len(result.resolved_actions) == 2
        assert result.resolved_actions[0].action_index == 0
        assert result.resolved_actions[0].action_type == "CLOSE"
        assert result.resolved_actions[0].resolved_position_ids == [op_id_1]
        assert result.resolved_actions[0].eligibility == "ELIGIBLE"
        assert result.resolved_actions[1].action_index == 1
        assert result.resolved_actions[1].action_type == "SET_STOP"
        assert result.resolved_actions[1].resolved_position_ids == [op_id_2]
        assert result.resolved_actions[1].eligibility == "ELIGIBLE"


class TestSelectorSideShort:
    """SELECTOR con side=SHORT → filtra solo posizioni SELL."""

    def test_selector_short_returns_only_sell(self, tmp_path: Path) -> None:
        db_path = _make_db(tmp_path)
        pr1 = _insert_parse_result(db_path, raw_message_id=1)
        pr2 = _insert_parse_result(db_path, raw_message_id=2)
        _insert_signal(db_path, attempt_key="T_100_1_tr_a", trader_id="tr_a",
                       root_telegram_id="1", side="BUY", status="ACTIVE")
        _insert_signal(db_path, attempt_key="T_100_2_tr_a", trader_id="tr_a",
                       root_telegram_id="2", side="SELL", status="ACTIVE")
        _insert_op_signal(db_path, parse_result_id=pr1,
                          attempt_key="T_100_1_tr_a", trader_id="tr_a")
        op_id_sell = _insert_op_signal(db_path, parse_result_id=pr2,
                                       attempt_key="T_100_2_tr_a", trader_id="tr_a")

        canonical = _make_canonical_update([
            _make_targeted_action("CLOSE", "SELECTOR",
                                  selector={"side": "SHORT", "status": "OPEN"}),
        ])

        result = resolve_targeted(canonical, trader_id="tr_a", db_path=db_path)

        assert len(result.resolved_actions) == 1
        item = result.resolved_actions[0]
        assert item.eligibility == "ELIGIBLE"
        assert item.resolved_position_ids == [op_id_sell]


class TestNoTargetedActions:
    """CanonicalMessage senza targeted_actions → risultato vuoto, nessun errore."""

    def test_empty_targeted_actions_returns_empty_result(self, tmp_path: Path) -> None:
        db_path = _make_db(tmp_path)
        canonical = _make_canonical_update(targeted_actions=[])

        result = resolve_targeted(canonical, trader_id="tr_a", db_path=db_path)

        assert result.resolved_actions == []
        assert result.resolved_reports == []
