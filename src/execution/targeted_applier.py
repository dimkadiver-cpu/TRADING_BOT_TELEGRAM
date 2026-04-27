"""Targeted update applier — Fase 4.

Applies TargetedStateUpdatePlan to the DB.
Each action_plan item is applied to its resolved_position_ids via
attempt_key reverse lookup (operational_signals → signals).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from src.core.timeutils import utc_now_iso
from src.execution.targeted_planner import TargetedStateUpdatePlan

_log = logging.getLogger(__name__)


@dataclass
class TargetedApplyResult:
    """Risultato dell'applicazione del piano targeted."""

    applied_action_results: list[dict[str, Any]] = field(default_factory=list)
    applied_report_results: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def apply_plan(
    plan: TargetedStateUpdatePlan,
    *,
    db_path: str,
) -> TargetedApplyResult:
    """Applica il piano targeted al DB.

    Per ogni action_plan con eligibility != ELIGIBLE: log + skip.
    Per ogni action_plan ELIGIBLE: risolve attempt_keys e applica l'azione.
    Per ogni report_plan ELIGIBLE: persiste il risultato individuale per-posizione.
    """
    result = TargetedApplyResult()
    now = utc_now_iso()

    try:
        with sqlite3.connect(db_path) as conn:
            for action_plan in plan.action_plans:
                if action_plan.eligibility != "ELIGIBLE":
                    msg = f"skipped_not_found:{action_plan.action_type}"
                    result.warnings.append(msg)
                    _log.info(
                        "targeted_apply skip | action_type=%s eligibility=%s reason=%s",
                        action_plan.action_type,
                        action_plan.eligibility,
                        action_plan.reason,
                    )
                    continue

                attempt_keys = list(action_plan.target_attempt_keys)
                if not attempt_keys:
                    result.warnings.append(
                        f"targeted_apply_missing_attempt_keys:{action_plan.action_type}"
                    )
                    continue

                applied = _apply_action(
                    conn=conn,
                    action_type=action_plan.action_type,
                    params=action_plan.params,
                    attempt_keys=attempt_keys,
                    now=now,
                )
                result.applied_action_results.append(
                    {
                        "action_type": action_plan.action_type,
                        "attempt_keys": attempt_keys,
                        "rows_affected": applied,
                    }
                )

            for report_plan in plan.report_plans:
                if report_plan.eligibility != "ELIGIBLE":
                    result.warnings.append(
                        f"skipped_not_found_report:{report_plan.event_type}"
                    )
                    continue

                attempt_keys = list(report_plan.target_attempt_keys)
                _persist_report(
                    conn=conn,
                    event_type=report_plan.event_type,
                    result_payload=report_plan.result,
                    attempt_keys=attempt_keys,
                    now=now,
                )
                result.applied_report_results.append(
                    {
                        "event_type": report_plan.event_type,
                        "attempt_keys": attempt_keys,
                        "result": report_plan.result,
                    }
                )

            conn.commit()

    except sqlite3.DatabaseError as exc:
        result.errors.append(f"targeted_apply_db_error:{exc}")

    return result


def apply_targeted_plan(
    plan: TargetedStateUpdatePlan,
    *,
    db_path: str,
) -> TargetedApplyResult:
    """Backward-compatible alias kept for existing callers."""
    return apply_plan(plan, db_path=db_path)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _get_attempt_keys_for_op_ids(
    conn: sqlite3.Connection, op_signal_ids: list[int]
) -> list[str]:
    """Reverse lookup: op_signal_id → attempt_key via operational_signals table."""
    if not op_signal_ids:
        return []
    placeholders = ",".join("?" for _ in op_signal_ids)
    rows = conn.execute(
        f"SELECT attempt_key FROM operational_signals WHERE op_signal_id IN ({placeholders})",
        op_signal_ids,
    ).fetchall()
    return [str(r[0]) for r in rows if r and r[0]]


def _apply_action(
    *,
    conn: sqlite3.Connection,
    action_type: str,
    params: dict[str, Any],
    attempt_keys: list[str],
    now: str,
) -> int:
    """Dispatch action to the appropriate handler. Returns total rows affected."""
    if action_type == "SET_STOP":
        return _apply_set_stop(conn=conn, params=params, attempt_keys=attempt_keys, now=now)
    if action_type == "CLOSE":
        return _apply_close(conn=conn, params=params, attempt_keys=attempt_keys, now=now)
    if action_type == "CANCEL_PENDING":
        return _apply_cancel_pending(conn=conn, attempt_keys=attempt_keys, now=now)
    _log.warning("targeted_apply unknown action_type=%s", action_type)
    return 0


def _apply_set_stop(
    *,
    conn: sqlite3.Connection,
    params: dict[str, Any],
    attempt_keys: list[str],
    now: str,
) -> int:
    target_type = str(params.get("target_type", "")).upper()
    if target_type != "PRICE":
        _log.warning("targeted_apply SET_STOP unsupported target_type=%s", target_type)
        return 0
    price = params.get("price")
    if not isinstance(price, (int, float)):
        _log.warning("targeted_apply SET_STOP missing numeric price params=%s", params)
        return 0
    total = 0
    for ak in attempt_keys:
        cursor = conn.execute(
            "UPDATE signals SET sl = ?, updated_at = ? WHERE attempt_key = ?",
            (float(price), now, ak),
        )
        total += cursor.rowcount
    return total


def _apply_close(
    *,
    conn: sqlite3.Connection,
    params: dict[str, Any],
    attempt_keys: list[str],
    now: str,
) -> int:
    total = 0
    for ak in attempt_keys:
        cursor = conn.execute(
            "UPDATE signals SET status = 'CLOSED', updated_at = ? WHERE attempt_key = ?",
            (now, ak),
        )
        total += cursor.rowcount
    return total


def _apply_cancel_pending(
    *,
    conn: sqlite3.Connection,
    attempt_keys: list[str],
    now: str,
) -> int:
    total = 0
    for ak in attempt_keys:
        cursor = conn.execute(
            """UPDATE orders SET status = 'CANCELLED', updated_at = ?
               WHERE attempt_key = ? AND purpose = 'ENTRY'
                 AND status NOT IN ('FILLED','CANCELLED','REJECTED')""",
            (now, ak),
        )
        total += cursor.rowcount
    return total


def _persist_report(
    *,
    conn: sqlite3.Connection,
    event_type: str,
    result_payload: dict[str, Any] | None,
    attempt_keys: list[str],
    now: str,
) -> None:
    """Persiste il risultato per-posizione in trades.meta_json."""
    for ak in attempt_keys:
        row = conn.execute(
            "SELECT meta_json FROM trades WHERE attempt_key = ?", (ak,)
        ).fetchone()
        if row is None:
            continue
        try:
            meta = json.loads(row[0]) if row[0] else {}
        except (TypeError, ValueError):
            meta = {}
        if not isinstance(meta, dict):
            meta = {}
        reported = meta.get("targeted_results", [])
        if not isinstance(reported, list):
            reported = []
        reported.append({"event_type": event_type, "result": result_payload, "at": now})
        meta["targeted_results"] = reported
        conn.execute(
            "UPDATE trades SET meta_json = ?, updated_at = ? WHERE attempt_key = ?",
            (json.dumps(meta, ensure_ascii=False, sort_keys=True), now, ak),
        )
