"""Applies StateUpdatePlan changes to the DB in a conservative, traceable way."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import sqlite3
from typing import Any, Mapping

from src.core.timeutils import utc_now_iso
from src.execution.update_planner import StateUpdatePlan


@dataclass(slots=True)
class UpdateApplyResult:
    target_attempt_keys: list[str] = field(default_factory=list)
    applied_signal_updates: list[dict[str, Any]] = field(default_factory=list)
    applied_order_updates: list[dict[str, Any]] = field(default_factory=list)
    applied_position_updates: list[dict[str, Any]] = field(default_factory=list)
    applied_result_updates: list[dict[str, Any]] = field(default_factory=list)
    applied_events: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "target_attempt_keys": self.target_attempt_keys,
            "applied_signal_updates": self.applied_signal_updates,
            "applied_order_updates": self.applied_order_updates,
            "applied_position_updates": self.applied_position_updates,
            "applied_result_updates": self.applied_result_updates,
            "applied_events": self.applied_events,
            "warnings": self.warnings,
            "errors": self.errors,
        }


def apply_update_plan(
    plan: StateUpdatePlan | Mapping[str, Any],
    db_path: str,
    *,
    env: str = "T",
    channel_id: str = "system",
    telegram_msg_id: str = "0",
    trader_id: str | None = None,
    trader_prefix: str | None = None,
    target_attempt_keys: list[str] | None = None,
) -> UpdateApplyResult:
    state_plan = _coerce_plan(plan)
    result = UpdateApplyResult()
    result.warnings.extend(state_plan.warnings)
    now = utc_now_iso()

    try:
        with sqlite3.connect(db_path) as conn:
            resolved_attempt_keys = target_attempt_keys or _resolve_attempt_keys(conn, env=env, target_refs=state_plan.target_refs)
            result.target_attempt_keys = resolved_attempt_keys
            if not resolved_attempt_keys:
                result.warnings.append("apply_missing_target_attempt_keys")

            symbol_by_attempt = _load_signal_symbols(conn, env=env, attempt_keys=resolved_attempt_keys)
            entry_by_attempt = _load_signal_entry_prices(conn, env=env, attempt_keys=resolved_attempt_keys)

            for action in state_plan.actions:
                if action == "ACT_MOVE_STOP_LOSS":
                    _apply_move_stop(
                        conn=conn,
                        result=result,
                        attempt_keys=resolved_attempt_keys,
                        entry_by_attempt=entry_by_attempt,
                        state_plan=state_plan,
                        env=env,
                        now=now,
                    )
                elif action == "ACT_CLOSE_PARTIAL":
                    _apply_trade_state(
                        conn=conn,
                        result=result,
                        attempt_keys=resolved_attempt_keys,
                        env=env,
                        state="PARTIAL_CLOSE_REQUESTED",
                        now=now,
                        close_fraction=_find_close_fraction(state_plan.position_updates),
                    )
                elif action == "ACT_CLOSE_FULL":
                    _apply_trade_state(
                        conn=conn,
                        result=result,
                        attempt_keys=resolved_attempt_keys,
                        env=env,
                        state="CLOSED",
                        now=now,
                        close_reason="FULL_CLOSE_REQUESTED",
                    )
                elif action == "ACT_CANCEL_ALL_PENDING_ENTRIES":
                    _apply_order_status(
                        conn=conn,
                        result=result,
                        attempt_keys=resolved_attempt_keys,
                        env=env,
                        status="CANCELLED",
                        purpose="ENTRY",
                        now=now,
                    )
                elif action == "ACT_MARK_ORDER_FILLED":
                    _apply_order_status(
                        conn=conn,
                        result=result,
                        attempt_keys=resolved_attempt_keys,
                        env=env,
                        status="FILLED",
                        purpose="ENTRY",
                        now=now,
                    )
                elif action == "ACT_MARK_TP_HIT":
                    _apply_order_status(
                        conn=conn,
                        result=result,
                        attempt_keys=resolved_attempt_keys,
                        env=env,
                        status="FILLED",
                        purpose="TP",
                        now=now,
                    )
                elif action == "ACT_MARK_STOP_HIT":
                    _apply_order_status(
                        conn=conn,
                        result=result,
                        attempt_keys=resolved_attempt_keys,
                        env=env,
                        status="FILLED",
                        purpose="SL",
                        now=now,
                    )
                    _apply_trade_state(
                        conn=conn,
                        result=result,
                        attempt_keys=resolved_attempt_keys,
                        env=env,
                        state="CLOSED",
                        now=now,
                        close_reason="STOP_HIT",
                    )
                elif action == "ACT_MARK_SIGNAL_INVALID":
                    _apply_signal_status(
                        conn=conn,
                        result=result,
                        attempt_keys=resolved_attempt_keys,
                        env=env,
                        status="INVALID",
                        now=now,
                    )
                elif action == "ACT_MARK_POSITION_CLOSED":
                    _apply_trade_state(
                        conn=conn,
                        result=result,
                        attempt_keys=resolved_attempt_keys,
                        env=env,
                        state="CLOSED",
                        now=now,
                        close_reason="POSITION_CLOSED",
                    )
                    _apply_positions_closed(
                        conn=conn,
                        result=result,
                        attempt_keys=resolved_attempt_keys,
                        symbol_by_attempt=symbol_by_attempt,
                        env=env,
                        now=now,
                    )
                elif action == "ACT_ATTACH_RESULT":
                    _apply_attach_result(
                        conn=conn,
                        result=result,
                        attempt_keys=resolved_attempt_keys,
                        env=env,
                        now=now,
                        position_updates=state_plan.result_updates,
                    )
                else:
                    result.warnings.append(f"apply_unsupported_action:{action}")

            for event_code in state_plan.events:
                if not resolved_attempt_keys:
                    continue
                for attempt_key in resolved_attempt_keys:
                    payload = {"event": event_code, "actions": state_plan.actions}
                    conn.execute(
                        """
                        INSERT INTO events(
                          env, channel_id, telegram_msg_id, trader_id, trader_prefix,
                          attempt_key, event_type, payload_json, confidence, created_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            env,
                            channel_id,
                            telegram_msg_id,
                            trader_id,
                            trader_prefix,
                            attempt_key,
                            event_code,
                            json.dumps(payload, ensure_ascii=False, sort_keys=True),
                            1.0,
                            now,
                        ),
                    )
                    result.applied_events.append({"attempt_key": attempt_key, "event_type": event_code})

            for warning in result.warnings:
                conn.execute(
                    """
                    INSERT INTO warnings(env, attempt_key, trader_id, code, severity, detail_json, created_at)
                    VALUES (?, ?, ?, ?, 'WARN', ?, ?)
                    """,
                    (
                        env,
                        resolved_attempt_keys[0] if resolved_attempt_keys else None,
                        trader_id,
                        warning,
                        json.dumps({"source": "update_applier"}, ensure_ascii=False, sort_keys=True),
                        now,
                    ),
                )

            conn.commit()
    except sqlite3.DatabaseError as exc:
        result.errors.append(f"apply_db_error:{exc}")

    return result


def _coerce_plan(plan: StateUpdatePlan | Mapping[str, Any]) -> StateUpdatePlan:
    if isinstance(plan, StateUpdatePlan):
        return plan
    if not isinstance(plan, Mapping):
        return StateUpdatePlan(message_type=None, intents=[], actions=[], target_refs=[])
    return StateUpdatePlan(
        message_type=plan.get("message_type") if isinstance(plan.get("message_type"), str) else None,
        intents=[value for value in plan.get("intents", []) if isinstance(value, str)],
        actions=[value for value in plan.get("actions", []) if isinstance(value, str)],
        target_refs=[value for value in plan.get("target_refs", []) if isinstance(value, int)],
        signal_updates=[value for value in plan.get("signal_updates", []) if isinstance(value, dict)],
        order_updates=[value for value in plan.get("order_updates", []) if isinstance(value, dict)],
        position_updates=[value for value in plan.get("position_updates", []) if isinstance(value, dict)],
        result_updates=[value for value in plan.get("result_updates", []) if isinstance(value, dict)],
        events=[value for value in plan.get("events", []) if isinstance(value, str)],
        warnings=[value for value in plan.get("warnings", []) if isinstance(value, str)],
    )


def _resolve_attempt_keys(conn: sqlite3.Connection, *, env: str, target_refs: list[int]) -> list[str]:
    if not target_refs:
        return []
    rows = conn.execute(
        """
        SELECT attempt_key
        FROM signals
        WHERE env = ?
          AND (root_telegram_id IN ({placeholders}) OR trader_signal_id IN ({placeholders}))
        """.format(placeholders=",".join("?" for _ in target_refs)),
        [env, *[str(value) for value in target_refs], *target_refs],
    ).fetchall()
    return [str(row[0]) for row in rows if row and row[0]]


def _load_signal_symbols(conn: sqlite3.Connection, *, env: str, attempt_keys: list[str]) -> dict[str, str]:
    if not attempt_keys:
        return {}
    rows = conn.execute(
        """
        SELECT attempt_key, symbol
        FROM signals
        WHERE env = ?
          AND attempt_key IN ({placeholders})
        """.format(placeholders=",".join("?" for _ in attempt_keys)),
        [env, *attempt_keys],
    ).fetchall()
    return {str(row[0]): str(row[1]) for row in rows if row and row[0] and row[1]}


def _load_signal_entry_prices(conn: sqlite3.Connection, *, env: str, attempt_keys: list[str]) -> dict[str, float]:
    if not attempt_keys:
        return {}
    rows = conn.execute(
        """
        SELECT attempt_key, entry_json
        FROM signals
        WHERE env = ?
          AND attempt_key IN ({placeholders})
        """.format(placeholders=",".join("?" for _ in attempt_keys)),
        [env, *attempt_keys],
    ).fetchall()
    out: dict[str, float] = {}
    for attempt_key, entry_json in rows:
        if not attempt_key or not entry_json:
            continue
        try:
            entries = json.loads(entry_json)
        except (TypeError, ValueError):
            continue
        if isinstance(entries, list) and entries and isinstance(entries[0], (int, float)):
            out[str(attempt_key)] = float(entries[0])
        elif isinstance(entries, list) and entries and isinstance(entries[0], dict):
            price = entries[0].get("price")
            if isinstance(price, (int, float)):
                out[str(attempt_key)] = float(price)
    return out


def _apply_signal_status(
    *,
    conn: sqlite3.Connection,
    result: UpdateApplyResult,
    attempt_keys: list[str],
    env: str,
    status: str,
    now: str,
) -> None:
    if not attempt_keys:
        result.warnings.append("apply_signal_status_missing_target")
        return
    for attempt_key in attempt_keys:
        cursor = conn.execute(
            "UPDATE signals SET status = ?, updated_at = ? WHERE env = ? AND attempt_key = ?",
            (status, now, env, attempt_key),
        )
        if cursor.rowcount:
            result.applied_signal_updates.append({"attempt_key": attempt_key, "status": status})


def _apply_order_status(
    *,
    conn: sqlite3.Connection,
    result: UpdateApplyResult,
    attempt_keys: list[str],
    env: str,
    status: str,
    purpose: str,
    now: str,
) -> None:
    if not attempt_keys:
        result.warnings.append("apply_order_status_missing_target")
        return
    purpose_values = {"ENTRY": ["ENTRY"], "TP": ["TP", "TAKE_PROFIT"], "SL": ["SL", "STOP_LOSS"]}.get(purpose, [purpose])
    for attempt_key in attempt_keys:
        cursor = conn.execute(
            """
            UPDATE orders
            SET status = ?, updated_at = ?
            WHERE env = ?
              AND attempt_key = ?
              AND purpose IN ({placeholders})
            """.format(placeholders=",".join("?" for _ in purpose_values)),
            [status, now, env, attempt_key, *purpose_values],
        )
        if cursor.rowcount:
            result.applied_order_updates.append(
                {
                    "attempt_key": attempt_key,
                    "purpose": purpose,
                    "status": status,
                    "rows": cursor.rowcount,
                }
            )


def _apply_move_stop(
    *,
    conn: sqlite3.Connection,
    result: UpdateApplyResult,
    attempt_keys: list[str],
    entry_by_attempt: dict[str, float],
    state_plan: StateUpdatePlan,
    env: str,
    now: str,
) -> None:
    if not attempt_keys:
        result.warnings.append("apply_move_stop_missing_target")
        return
    stop_update = _find_position_update(state_plan.position_updates, field="stop_loss")
    if stop_update is None:
        result.warnings.append("apply_move_stop_missing_stop_payload")
        return
    for attempt_key in attempt_keys:
        stop_value = stop_update.get("value")
        if stop_update.get("op") == "SET_FROM_ENTRY":
            stop_value = entry_by_attempt.get(attempt_key)
        if not isinstance(stop_value, (int, float)):
            result.warnings.append(f"apply_move_stop_missing_numeric_value:{attempt_key}")
            continue
        cursor = conn.execute(
            "UPDATE signals SET sl = ?, updated_at = ? WHERE env = ? AND attempt_key = ?",
            (float(stop_value), now, env, attempt_key),
        )
        if cursor.rowcount:
            result.applied_position_updates.append({"attempt_key": attempt_key, "stop_loss": float(stop_value)})
        order_cursor = conn.execute(
            """
            UPDATE orders
            SET trigger_price = ?, updated_at = ?
            WHERE env = ?
              AND attempt_key = ?
              AND purpose IN ('SL', 'STOP_LOSS')
            """,
            (float(stop_value), now, env, attempt_key),
        )
        if order_cursor.rowcount:
            result.applied_order_updates.append(
                {
                    "attempt_key": attempt_key,
                    "purpose": "SL",
                    "trigger_price": float(stop_value),
                    "rows": order_cursor.rowcount,
                }
            )


def _apply_trade_state(
    *,
    conn: sqlite3.Connection,
    result: UpdateApplyResult,
    attempt_keys: list[str],
    env: str,
    state: str,
    now: str,
    close_reason: str | None = None,
    close_fraction: float | None = None,
) -> None:
    if not attempt_keys:
        result.warnings.append("apply_trade_state_missing_target")
        return
    for attempt_key in attempt_keys:
        meta_json = _load_trade_meta(conn, env=env, attempt_key=attempt_key)
        if close_fraction is not None:
            meta_json["close_fraction"] = close_fraction
        query = "UPDATE trades SET state = ?, updated_at = ?, meta_json = ? WHERE env = ? AND attempt_key = ?"
        params: list[Any] = [state, now, json.dumps(meta_json, ensure_ascii=False, sort_keys=True), env, attempt_key]
        if state == "CLOSED":
            query = "UPDATE trades SET state = ?, close_reason = ?, closed_at = ?, updated_at = ?, meta_json = ? WHERE env = ? AND attempt_key = ?"
            params = [state, close_reason, now, now, json.dumps(meta_json, ensure_ascii=False, sort_keys=True), env, attempt_key]
        cursor = conn.execute(query, params)
        if cursor.rowcount:
            payload: dict[str, Any] = {"attempt_key": attempt_key, "state": state}
            if close_reason:
                payload["close_reason"] = close_reason
            if close_fraction is not None:
                payload["close_fraction"] = close_fraction
            result.applied_position_updates.append(payload)


def _apply_positions_closed(
    *,
    conn: sqlite3.Connection,
    result: UpdateApplyResult,
    attempt_keys: list[str],
    symbol_by_attempt: dict[str, str],
    env: str,
    now: str,
) -> None:
    for attempt_key in attempt_keys:
        symbol = symbol_by_attempt.get(attempt_key)
        if not symbol:
            continue
        cursor = conn.execute(
            "UPDATE positions SET size = 0, updated_at = ? WHERE env = ? AND symbol = ?",
            (now, env, symbol),
        )
        if cursor.rowcount:
            result.applied_position_updates.append({"attempt_key": attempt_key, "symbol": symbol, "size": 0})


def _apply_attach_result(
    *,
    conn: sqlite3.Connection,
    result: UpdateApplyResult,
    attempt_keys: list[str],
    env: str,
    now: str,
    position_updates: list[dict[str, Any]],
) -> None:
    if not attempt_keys:
        result.warnings.append("apply_attach_result_missing_target")
        return
    result_update = _find_result_update(position_updates, field="reported_results")
    if result_update is None:
        result.warnings.append("apply_attach_result_missing_payload")
        return
    for attempt_key in attempt_keys:
        meta_json = _load_trade_meta(conn, env=env, attempt_key=attempt_key)
        meta_json["reported_results"] = result_update.get("value")
        if result_update.get("mode") is not None:
            meta_json["result_mode"] = result_update.get("mode")
        cursor = conn.execute(
            "UPDATE trades SET meta_json = ?, updated_at = ? WHERE env = ? AND attempt_key = ?",
            (json.dumps(meta_json, ensure_ascii=False, sort_keys=True), now, env, attempt_key),
        )
        if cursor.rowcount:
            result.applied_result_updates.append(
                {
                    "attempt_key": attempt_key,
                    "reported_results": result_update.get("value"),
                    "result_mode": result_update.get("mode"),
                }
            )


def _load_trade_meta(conn: sqlite3.Connection, *, env: str, attempt_key: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT meta_json FROM trades WHERE env = ? AND attempt_key = ?",
        (env, attempt_key),
    ).fetchone()
    if not row or not row[0]:
        return {}
    try:
        parsed = json.loads(row[0])
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _find_position_update(updates: list[dict[str, Any]], *, field: str) -> dict[str, Any] | None:
    for update in updates:
        if update.get("field") == field:
            return update
    return None


def _find_result_update(updates: list[dict[str, Any]], *, field: str) -> dict[str, Any] | None:
    for update in updates:
        if update.get("field") == field:
            return update
    return None


def _find_close_fraction(updates: list[dict[str, Any]]) -> float | None:
    for update in updates:
        value = update.get("close_fraction")
        if isinstance(value, (int, float)):
            return float(value)
    return None
