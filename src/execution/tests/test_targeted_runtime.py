"""Tests for Fase 4 — build_targeted_plan() and apply_targeted_plan().

Covers:
  - build_targeted_plan: struttura corretta del piano da MultiRefResolvedResult
  - Caso 1: SET_STOP comune su 2 posizioni → apply aggiorna entrambe le signals.sl
  - Caso 2: CLOSE + 4 report → apply chiude posizioni e persiste 4 result
  - Caso 3: 2 SET_STOP eterogenei → apply aggiorna 4+1 posizioni con prezzi diversi
  - NOT_FOUND: skip silenzioso, nessuna eccezione
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.core.migrations import apply_migrations
from src.execution.targeted_applier import TargetedApplyResult, apply_targeted_plan
from src.execution.targeted_planner import (
    TargetedActionPlanItem,
    TargetedReportPlanItem,
    TargetedStateUpdatePlan,
    build_plan,
)
from src.parser.canonical_v1.models import (
    CanonicalMessage,
    RawContext,
    TargetedAction,
    TargetedActionTargeting,
    TargetedReport,
    TargetedReportResult,
    TargetedReportTargeting,
    UpdatePayload,
)
from src.target_resolver.models import (
    MultiRefResolvedResult,
    ResolvedActionItem,
    ResolvedReportItem,
)


# ---------------------------------------------------------------------------
# Helpers DB
# ---------------------------------------------------------------------------


def _make_db(tmp_path: Path) -> str:
    db_path = str(tmp_path / "targeted_runtime_test.sqlite3")
    apply_migrations(db_path=db_path, migrations_dir=str(Path("db/migrations").resolve()))
    return db_path


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


def _insert_signal(
    db_path: str,
    *,
    attempt_key: str,
    trader_id: str = "tr_a",
    symbol: str = "BTCUSDT",
    side: str = "BUY",
    status: str = "ACTIVE",
    root_telegram_id: str,
    sl: float = 55000.0,
) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO signals
               (attempt_key, env, channel_id, root_telegram_id, trader_id, trader_prefix,
                symbol, side, entry_json, sl, tp_json, status, confidence, raw_text,
                created_at, updated_at, trader_signal_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                attempt_key, "T", "100", root_telegram_id, trader_id, "TP",
                symbol, side, "[]", sl, "[]", status, 0.9, "test",
                "2026-01-01", "2026-01-01", None,
            ),
        )
        conn.commit()


def _insert_op_signal(
    db_path: str,
    *,
    parse_result_id: int,
    attempt_key: str,
    trader_id: str = "tr_a",
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


def _make_canonical_update(
    targeted_actions: list[TargetedAction] | None = None,
    targeted_reports: list[TargetedReport] | None = None,
) -> CanonicalMessage:
    return CanonicalMessage(
        parser_profile="trader_a",
        primary_class="UPDATE",
        parse_status="PARTIAL",
        confidence=0.9,
        raw_context=RawContext(raw_text="test"),
        update=UpdatePayload(),
        targeted_actions=targeted_actions or [],
        targeted_reports=targeted_reports or [],
    )


# ---------------------------------------------------------------------------
# Tests — build_targeted_plan
# ---------------------------------------------------------------------------


class TestBuildTargetedPlan:
    """build_plan() costruisce il piano corretto senza accedere al DB."""

    def test_single_action_two_positions(self) -> None:
        """1 azione ELIGIBLE con 2 position_ids → 1 TargetedActionPlanItem con params corretti."""
        canonical = _make_canonical_update(
            targeted_actions=[
                TargetedAction(
                    action_type="CLOSE",
                    params={"close_scope": "FULL"},
                    targeting=TargetedActionTargeting(mode="TARGET_GROUP", targets=[10, 20]),
                )
            ],
        )
        resolved = MultiRefResolvedResult(
            resolved_actions=[
                ResolvedActionItem(
                    action_index=0,
                    action_type="CLOSE",
                    resolved_position_ids=[1, 2],
                    resolved_attempt_keys=["T_tr_a_10", "T_tr_a_20"],
                    eligibility="ELIGIBLE",
                )
            ],
        )

        plan = build_plan(resolved, canonical)

        assert len(plan.action_plans) == 1
        item = plan.action_plans[0]
        assert item.action_type == "CLOSE"
        assert item.target_attempt_keys == ["T_tr_a_10", "T_tr_a_20"]
        assert item.eligibility == "ELIGIBLE"
        assert item.params == {"close_scope": "FULL"}
        assert plan.report_plans == []

    def test_not_found_propagated_to_plan(self) -> None:
        """ResolvedActionItem NOT_FOUND → TargetedActionPlanItem con eligibility=NOT_FOUND."""
        canonical = _make_canonical_update(
            targeted_actions=[
                TargetedAction(
                    action_type="SET_STOP",
                    params={"target_type": "PRICE", "price": 45000.0},
                    targeting=TargetedActionTargeting(mode="TARGET_GROUP", targets=[99]),
                )
            ],
        )
        resolved = MultiRefResolvedResult(
            resolved_actions=[
                ResolvedActionItem(
                    action_index=0,
                    action_type="SET_STOP",
                    resolved_position_ids=[],
                    resolved_attempt_keys=[],
                    eligibility="NOT_FOUND",
                    reason="target_99_not_found",
                )
            ],
        )

        plan = build_plan(resolved, canonical)

        assert len(plan.action_plans) == 1
        assert plan.action_plans[0].eligibility == "NOT_FOUND"
        assert plan.action_plans[0].target_attempt_keys == []


# ---------------------------------------------------------------------------
# Tests — apply_targeted_plan (Fase 4.5)
# ---------------------------------------------------------------------------


class TestApplyTargetedPlanCaso1:
    """Caso 1: SET_STOP comune su 2 posizioni → signals.sl aggiornato su entrambe."""

    def test_set_stop_on_two_positions(self, tmp_path: Path) -> None:
        db_path = _make_db(tmp_path)
        pr1 = _insert_parse_result(db_path, raw_message_id=1)
        pr2 = _insert_parse_result(db_path, raw_message_id=2)
        _insert_signal(db_path, attempt_key="T_a_10", root_telegram_id="10")
        _insert_signal(db_path, attempt_key="T_a_20", root_telegram_id="20")
        op_id_1 = _insert_op_signal(db_path, parse_result_id=pr1, attempt_key="T_a_10")
        op_id_2 = _insert_op_signal(db_path, parse_result_id=pr2, attempt_key="T_a_20")

        plan = TargetedStateUpdatePlan(
            action_plans=[
                TargetedActionPlanItem(
                    action_type="SET_STOP",
                    target_attempt_keys=["T_a_10", "T_a_20"],
                    params={"target_type": "PRICE", "price": 44000.0},
                    eligibility="ELIGIBLE",
                )
            ],
            report_plans=[],
        )

        result = apply_targeted_plan(plan, db_path=db_path)

        assert result.errors == []
        assert len(result.applied_action_results) == 1
        assert result.applied_action_results[0]["action_type"] == "SET_STOP"
        assert set(result.applied_action_results[0]["attempt_keys"]) == {"T_a_10", "T_a_20"}

        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                "SELECT attempt_key, sl FROM signals WHERE attempt_key IN ('T_a_10','T_a_20')"
            ).fetchall()
        sl_map = {r[0]: r[1] for r in rows}
        assert sl_map["T_a_10"] == 44000.0
        assert sl_map["T_a_20"] == 44000.0


class TestApplyTargetedPlanCaso2:
    """Caso 2: CLOSE su 4 posizioni + 4 report → apply chiude e persiste 4 result."""

    def test_close_and_persist_four_reports(self, tmp_path: Path) -> None:
        db_path = _make_db(tmp_path)
        attempt_keys = ["T_a_10", "T_a_20", "T_a_30", "T_a_40"]
        op_ids: list[int] = []
        for i, ak in enumerate(attempt_keys):
            pr_id = _insert_parse_result(db_path, raw_message_id=i + 1)
            _insert_signal(db_path, attempt_key=ak, root_telegram_id=str(10 + i * 10))
            op_ids.append(_insert_op_signal(db_path, parse_result_id=pr_id, attempt_key=ak))

        plan = TargetedStateUpdatePlan(
            action_plans=[
                TargetedActionPlanItem(
                    action_type="CLOSE",
                    target_attempt_keys=attempt_keys,
                    params={"close_scope": "FULL"},
                    eligibility="ELIGIBLE",
                )
            ],
            report_plans=[
                TargetedReportPlanItem(
                    event_type="FINAL_RESULT",
                    target_attempt_keys=[attempt_keys[i]],
                    result={"value": float(i + 1), "unit": "R", "text": None},
                    eligibility="ELIGIBLE",
                )
                for i, op_id in enumerate(op_ids)
            ],
        )

        result = apply_targeted_plan(plan, db_path=db_path)

        assert result.errors == []
        assert len(result.applied_action_results) == 1
        assert result.applied_action_results[0]["action_type"] == "CLOSE"
        assert len(result.applied_report_results) == 4


class TestApplyTargetedPlanCaso3:
    """Caso 3: 2 SET_STOP eterogenei → 4 posizioni a 43000 + 1 a 47000."""

    def test_two_heterogeneous_set_stop(self, tmp_path: Path) -> None:
        db_path = _make_db(tmp_path)
        entry_keys = ["T_a_10", "T_a_20", "T_a_30", "T_a_40"]
        tp_key = "T_a_50"
        entry_op_ids: list[int] = []
        for i, ak in enumerate(entry_keys):
            pr_id = _insert_parse_result(db_path, raw_message_id=i + 1)
            _insert_signal(db_path, attempt_key=ak, root_telegram_id=str(10 + i * 10))
            entry_op_ids.append(_insert_op_signal(db_path, parse_result_id=pr_id, attempt_key=ak))

        pr_tp = _insert_parse_result(db_path, raw_message_id=5)
        _insert_signal(db_path, attempt_key=tp_key, root_telegram_id="50")
        tp_op_id = _insert_op_signal(db_path, parse_result_id=pr_tp, attempt_key=tp_key)

        plan = TargetedStateUpdatePlan(
            action_plans=[
                TargetedActionPlanItem(
                    action_type="SET_STOP",
                    target_attempt_keys=entry_keys,
                    params={"target_type": "PRICE", "price": 43000.0},
                    eligibility="ELIGIBLE",
                ),
                TargetedActionPlanItem(
                    action_type="SET_STOP",
                    target_attempt_keys=[tp_key],
                    params={"target_type": "PRICE", "price": 47000.0},
                    eligibility="ELIGIBLE",
                ),
            ],
            report_plans=[],
        )

        result = apply_targeted_plan(plan, db_path=db_path)

        assert result.errors == []
        assert len(result.applied_action_results) == 2

        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                "SELECT attempt_key, sl FROM signals WHERE attempt_key IN (?,?,?,?,?)",
                (*entry_keys, tp_key),
            ).fetchall()
        sl_map = {r[0]: r[1] for r in rows}
        for ak in entry_keys:
            assert sl_map[ak] == 43000.0, f"{ak} expected 43000.0, got {sl_map.get(ak)}"
        assert sl_map[tp_key] == 47000.0


class TestApplyTargetedPlanNotFound:
    """Piano con eligibility=NOT_FOUND → skip silenzioso, nessuna eccezione."""

    def test_not_found_skip_silent(self, tmp_path: Path) -> None:
        db_path = _make_db(tmp_path)

        plan = TargetedStateUpdatePlan(
            action_plans=[
                TargetedActionPlanItem(
                    action_type="SET_STOP",
                    target_attempt_keys=[],
                    params={"target_type": "PRICE", "price": 45000.0},
                    eligibility="NOT_FOUND",
                )
            ],
            report_plans=[],
        )

        result = apply_targeted_plan(plan, db_path=db_path)

        assert result.errors == []
        assert result.applied_action_results == []
        assert any("skipped_not_found" in w for w in result.warnings)
