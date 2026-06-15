# src/runtime_v2/execution_gateway/repositories.py
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from src.runtime_v2.execution_gateway import client_order_id as coid_mod
from src.runtime_v2.execution_gateway.event_ingest.payload import ExchangeEventPayload
from src.runtime_v2.lifecycle.models import ExecutionCommand

if TYPE_CHECKING:
    from src.runtime_v2.execution_gateway.event_ingest.models import ClassifiedEvent


_EXCHANGE_IDENTITY_TYPES = frozenset({
    "TP_FILLED", "SL_FILLED", "MANUAL_CLOSE_FULL", "MANUAL_CLOSE_PARTIAL",
    "LIQUIDATION_FILLED", "CLOSE_PARTIAL_FILLED", "CLOSE_FULL_FILLED", "FUNDING_SETTLED",
})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cmd_from_row(row: tuple) -> ExecutionCommand:
    (command_id, trade_chain_id, command_type, status, payload_json,
     idempotency_key, created_at, updated_at) = row[:8]
    return ExecutionCommand(
        command_id=command_id,
        trade_chain_id=trade_chain_id,
        command_type=command_type,
        status=status,
        payload_json=payload_json or "{}",
        idempotency_key=idempotency_key,
        created_at=datetime.fromisoformat(created_at) if created_at else None,
        updated_at=datetime.fromisoformat(updated_at) if updated_at else None,
    )


_BASE_COLS = (
    "command_id, trade_chain_id, command_type, status, payload_json, "
    "idempotency_key, created_at, updated_at"
)


class GatewayCommandRepository:
    def __init__(self, db_path: str) -> None:
        self._db = db_path

    def get_pending_batch(self, limit: int = 100) -> list[ExecutionCommand]:
        conn = sqlite3.connect(self._db)
        try:
            rows = conn.execute(
                f"SELECT {_BASE_COLS} FROM ops_execution_commands "
                "WHERE status='PENDING' ORDER BY created_at LIMIT ?", (limit,)
            ).fetchall()
            return [_cmd_from_row(r) for r in rows]
        finally:
            conn.close()

    def get_retry_batch(self, limit: int = 100) -> list[ExecutionCommand]:
        now = _now()
        conn = sqlite3.connect(self._db)
        try:
            rows = conn.execute(
                f"SELECT {_BASE_COLS} FROM ops_execution_commands "
                "WHERE status='SENT' AND next_retry_at IS NOT NULL "
                "AND next_retry_at <= ? ORDER BY next_retry_at LIMIT ?",
                (now, limit),
            ).fetchall()
            return [_cmd_from_row(r) for r in rows]
        finally:
            conn.close()

    def get_waiting_on_open_chains(self, limit: int = 100) -> list[ExecutionCommand]:
        conn = sqlite3.connect(self._db)
        try:
            rows = conn.execute(
                "SELECT c.command_id, c.trade_chain_id, c.command_type, c.status, "
                "c.payload_json, c.idempotency_key, c.created_at, c.updated_at "
                "FROM ops_execution_commands c "
                "JOIN ops_trade_chains t ON c.trade_chain_id = t.trade_chain_id "
                "WHERE c.status='WAITING_POSITION' AND t.lifecycle_state='OPEN' "
                "ORDER BY c.created_at LIMIT ?",
                (limit,),
            ).fetchall()
            return [_cmd_from_row(r) for r in rows]
        finally:
            conn.close()

    def get_sent_or_ack(self, limit: int = 500) -> list[tuple[ExecutionCommand, str | None]]:
        conn = sqlite3.connect(self._db)
        try:
            rows = conn.execute(
                f"SELECT {_BASE_COLS}, client_order_id FROM ops_execution_commands "
                "WHERE status IN ('SENT','ACK') AND client_order_id IS NOT NULL "
                "ORDER BY created_at LIMIT ?",
                (limit,),
            ).fetchall()
            return [(_cmd_from_row(r[:8]), r[8]) for r in rows]
        finally:
            conn.close()

    def get_active_client_order_ids(self) -> set[str]:
        conn = sqlite3.connect(self._db)
        try:
            rows = conn.execute(
                "SELECT client_order_id FROM ops_execution_commands "
                "WHERE status IN ('SENT','ACK') AND client_order_id IS NOT NULL"
            ).fetchall()
            return {row[0] for row in rows}
        finally:
            conn.close()

    def mark_sent(
        self,
        command_id: int,
        *,
        client_order_id: str,
        adapter: str,
        execution_account_id: str,
        adapter_order_id: str | None = None,
        exchange_order_id: str | None = None,
    ) -> None:
        now = _now()
        result = {"adapter_order_id": adapter_order_id,
                  "exchange_order_id": exchange_order_id, "error": None,
                  "reason": None, "warnings": []}
        conn = sqlite3.connect(self._db)
        try:
            conn.execute(
                "UPDATE ops_execution_commands SET status='SENT', adapter=?, "
                "execution_account_id=?, client_order_id=?, result_payload_json=?, "
                "sent_at=?, updated_at=? WHERE command_id=?",
                (adapter, execution_account_id, client_order_id,
                 json.dumps(result), now, now, command_id),
            )
            conn.commit()
        finally:
            conn.close()

    def mark_ack(self, command_id: int) -> None:
        now = _now()
        conn = sqlite3.connect(self._db)
        try:
            conn.execute(
                "UPDATE ops_execution_commands SET status='ACK', "
                "acknowledged_at=?, updated_at=? WHERE command_id=?",
                (now, now, command_id),
            )
            conn.commit()
        finally:
            conn.close()

    def mark_done(self, command_id: int, result: dict | None = None) -> None:
        now = _now()
        conn = sqlite3.connect(self._db)
        try:
            conn.execute(
                "UPDATE ops_execution_commands SET status='DONE', "
                "result_payload_json=?, completed_at=?, updated_at=? WHERE command_id=?",
                (json.dumps(result or {}), now, now, command_id),
            )
            conn.commit()
        finally:
            conn.close()

    def mark_failed(self, command_id: int, *, reason: str) -> None:
        now = _now()
        result = {"error": reason, "reason": reason}
        conn = sqlite3.connect(self._db)
        try:
            conn.execute(
                "UPDATE ops_execution_commands SET status='FAILED', "
                "result_payload_json=?, updated_at=? WHERE command_id=?",
                (json.dumps(result), now, command_id),
            )
            conn.commit()
        finally:
            conn.close()

    def reject_entry_as_signal(
        self,
        command_id: int,
        *,
        reason: str,
        result_payload: dict | None = None,
    ) -> bool:
        """Convert a pre-fill entry execution failure into a final SIGNAL_REJECTED outcome."""
        from src.runtime_v2.control_plane.outbox_writer import project_clean_log_for_chain

        now = _now()
        conn = sqlite3.connect(self._db)
        try:
            with conn:
                cmd_row = conn.execute(
                    "SELECT trade_chain_id, command_type FROM ops_execution_commands WHERE command_id=?",
                    (command_id,),
                ).fetchone()
                if not cmd_row:
                    return False
                trade_chain_id, command_type = int(cmd_row[0]), str(cmd_row[1])
                if command_type not in ("PLACE_ENTRY", "PLACE_ENTRY_WITH_ATTACHED_TPSL"):
                    return False

                result = {"error": reason, "reason": reason, **(result_payload or {})}
                conn.execute(
                    "UPDATE ops_execution_commands SET status='FAILED', "
                    "result_payload_json=?, updated_at=? WHERE command_id=?",
                    (json.dumps(result), now, command_id),
                )

                chain_row = conn.execute(
                    "SELECT lifecycle_state FROM ops_trade_chains WHERE trade_chain_id=?",
                    (trade_chain_id,),
                ).fetchone()
                previous_state = chain_row[0] if chain_row else None
                conn.execute(
                    "UPDATE ops_trade_chains SET lifecycle_state='CANCELLED', updated_at=? "
                    "WHERE trade_chain_id=?",
                    (now, trade_chain_id),
                )

                pending_signal_ids: list[int] = []
                for notification_id, payload_json in conn.execute(
                    "SELECT notification_id, payload_json FROM ops_notification_outbox "
                    "WHERE notification_type='SIGNAL_ACCEPTED' AND status='PENDING'"
                ).fetchall():
                    try:
                        payload = json.loads(payload_json or "{}")
                    except Exception:
                        continue
                    if int(payload.get("chain_id") or -1) == trade_chain_id:
                        pending_signal_ids.append(int(notification_id))
                for notification_id in pending_signal_ids:
                    conn.execute(
                        "UPDATE ops_notification_outbox SET status='SUPPRESSED' WHERE notification_id=?",
                        (notification_id,),
                    )

                conn.execute(
                    """
                    INSERT OR IGNORE INTO ops_lifecycle_events (
                        trade_chain_id, event_type, source_type,
                        previous_state, next_state, payload_json, idempotency_key, created_at
                    ) VALUES (?,?,?,?,?,?,?,?)
                    """,
                    (
                        trade_chain_id,
                        "SIGNAL_REJECTED",
                        "execution_gateway",
                        previous_state,
                        "CANCELLED",
                        json.dumps({"reason": reason, "source": "runtime", **(result_payload or {})}),
                        f"signal_rejected:{command_id}",
                        now,
                    ),
                )
                project_clean_log_for_chain(conn, trade_chain_id)
            return True
        finally:
            conn.close()

    def write_command_failed_tech_log(
        self,
        command_id: int,
        trade_chain_id: int,
        command_type: str,
        *,
        reason: str,
    ) -> None:
        """Write a TECH_LOG GATEWAY_COMMAND_FAILED notification for any failed non-entry command.

        Called after mark_failed for commands like MOVE_STOP_TO_BREAKEVEN, MOVE_STOP, etc.
        Entry failures are handled separately via cancel_chain_if_all_entries_failed.
        """
        from src.runtime_v2.control_plane.outbox_writer import write_tech_log_event

        conn = sqlite3.connect(self._db)
        try:
            with conn:
                chain_row = conn.execute(
                    "SELECT symbol, side FROM ops_trade_chains WHERE trade_chain_id=?",
                    (trade_chain_id,),
                ).fetchone()
                write_tech_log_event(
                    conn,
                    notification_type="GATEWAY_COMMAND_FAILED",
                    payload={
                        "level": "ERROR",
                        "category": "Gateway",
                        "title": "command_failed",
                        "description": f"Comando {command_type} fallito.",
                        "context": {
                            "command_id": command_id,
                            "command_type": command_type,
                            "chain_id": trade_chain_id,
                            "symbol": chain_row[0] if chain_row else None,
                            "side": chain_row[1] if chain_row else None,
                            "reason": reason,
                        },
                        "action": "verificare il motivo e intervenire manualmente se necessario",
                        "source": "execution_gateway",
                    },
                    dedupe_key=f"gw_cmd_failed:{command_id}",
                    priority="HIGH",
                )
        finally:
            conn.close()

    def write_cancel_entry_failed_lifecycle(
        self, command_id: int, trade_chain_id: int, *, attempts: int
    ) -> None:
        """Write ENTRY_CANCEL_FAILED lifecycle event after CANCEL_PENDING_ENTRY exhausts retries."""
        now = _now()
        conn = sqlite3.connect(self._db)
        try:
            with conn:
                cmd_row = conn.execute(
                    "SELECT payload_json, retry_count FROM ops_execution_commands WHERE command_id=?",
                    (command_id,),
                ).fetchone()
                payload = json.loads(cmd_row[0] or "{}") if cmd_row else {}
                entry_ref = payload.get("entry_client_order_id")

                conn.execute(
                    """
                    INSERT OR IGNORE INTO ops_lifecycle_events (
                        trade_chain_id, event_type, source_type,
                        previous_state, next_state, payload_json, idempotency_key, created_at
                    ) VALUES (?,?,?,?,?,?,?,?)
                    """,
                    (
                        trade_chain_id, "ENTRY_CANCEL_FAILED", "execution_gateway",
                        None, None,
                        json.dumps({"entry_ref": entry_ref, "attempts": attempts, "source": "execution_gateway"}),
                        f"cancel_entry_failed:{command_id}",
                        now,
                    ),
                )
        finally:
            conn.close()

    def cancel_chain_if_all_entries_failed(
        self, trade_chain_id: int, command_type: str, *, reason: str
    ) -> bool:
        """After an entry command is marked FAILED, cancel the chain if all entry commands are now failed.

        Only acts on PLACE_ENTRY / PLACE_ENTRY_WITH_ATTACHED_TPSL command types.
        Checks atomically: if no entry command remains in an active state, transitions
        the chain from WAITING_ENTRY/CREATED to CANCELLED and writes a lifecycle event.
        Returns True if the chain was cancelled.
        """
        _ENTRY_TYPES = ("PLACE_ENTRY", "PLACE_ENTRY_WITH_ATTACHED_TPSL")
        if command_type not in _ENTRY_TYPES:
            return False

        now = _now()
        conn = sqlite3.connect(self._db)
        try:
            with conn:
                chain_row = conn.execute(
                    "SELECT lifecycle_state, symbol, side FROM ops_trade_chains WHERE trade_chain_id=?",
                    (trade_chain_id,),
                ).fetchone()
                if not chain_row or chain_row[0] not in ("WAITING_ENTRY", "CREATED"):
                    return False

                active_row = conn.execute(
                    "SELECT COUNT(*) FROM ops_execution_commands "
                    "WHERE trade_chain_id=? "
                    "  AND command_type IN ('PLACE_ENTRY','PLACE_ENTRY_WITH_ATTACHED_TPSL') "
                    "  AND status NOT IN ('FAILED','CANCELLED','SUPERSEDED','REVIEW_REQUIRED')",
                    (trade_chain_id,),
                ).fetchone()
                if active_row and active_row[0] > 0:
                    return False

                conn.execute(
                    "UPDATE ops_trade_chains SET lifecycle_state='CANCELLED', updated_at=? "
                    "WHERE trade_chain_id=?",
                    (now, trade_chain_id),
                )
                conn.execute(
                    """
                    INSERT OR IGNORE INTO ops_lifecycle_events (
                        trade_chain_id, event_type, source_type,
                        previous_state, next_state, payload_json, idempotency_key, created_at
                    ) VALUES (?,?,?,?,?,?,?,?)
                    """,
                    (
                        trade_chain_id, "PENDING_ENTRY_CANCELLED", "entry_failure_handler",
                        chain_row[0], "CANCELLED",
                        json.dumps({"reason": reason}),
                        f"entry_all_failed:{trade_chain_id}",
                        now,
                    ),
                )
                from src.runtime_v2.control_plane.outbox_writer import write_tech_log_event
                write_tech_log_event(
                    conn,
                    notification_type="GATEWAY_ENTRY_ALL_FAILED",
                    payload={
                        "level": "ERROR",
                        "category": "Gateway",
                        "title": "entry_all_failed",
                        "description": "Tutti i comandi PLACE_ENTRY falliti. Catena cancellata.",
                        "context": {
                            "chain_id": trade_chain_id,
                            "symbol": chain_row[1],
                            "side": chain_row[2],
                            "reason": reason,
                        },
                        "action": "intervento manuale richiesto",
                        "source": "execution_gateway",
                    },
                    dedupe_key=f"gw_all_failed:{trade_chain_id}",
                    priority="HIGH",
                )
            return True
        finally:
            conn.close()

    def mark_review_required(self, command_id: int, *, reason: str) -> None:
        from src.runtime_v2.control_plane.outbox_writer import write_tech_log_event
        now = _now()
        result = {"error": None, "reason": reason}
        conn = sqlite3.connect(self._db)
        try:
            with conn:
                conn.execute(
                    "UPDATE ops_execution_commands SET status='REVIEW_REQUIRED', "
                    "result_payload_json=?, updated_at=? WHERE command_id=?",
                    (json.dumps(result), now, command_id),
                )
                cmd_row = conn.execute(
                    "SELECT trade_chain_id, command_type FROM ops_execution_commands "
                    "WHERE command_id=?",
                    (command_id,),
                ).fetchone()
                write_tech_log_event(
                    conn,
                    notification_type="GATEWAY_REVIEW_REQUIRED",
                    payload={
                        "level": "WARNING",
                        "category": "Gateway",
                        "title": "command_blocked",
                        "description": "Comando bloccato in REVIEW_REQUIRED.",
                        "context": {
                            "command_id": command_id,
                            "command_type": cmd_row[1] if cmd_row else None,
                            "chain_id": cmd_row[0] if cmd_row else None,
                            "reason": reason,
                        },
                        "action": "intervento manuale richiesto",
                        "source": "execution_gateway",
                    },
                    dedupe_key=f"gw_review:{command_id}",
                    priority="HIGH",
                )
        finally:
            conn.close()

    def mark_waiting_position(self, command_id: int) -> None:
        now = _now()
        conn = sqlite3.connect(self._db)
        try:
            conn.execute(
                "UPDATE ops_execution_commands SET status='WAITING_POSITION', "
                "updated_at=? WHERE command_id=?",
                (now, command_id),
            )
            conn.commit()
        finally:
            conn.close()

    def mark_retry(self, command_id: int, *, retry_count: int, next_retry_at: str) -> None:
        now = _now()
        conn = sqlite3.connect(self._db)
        try:
            conn.execute(
                "UPDATE ops_execution_commands SET status='SENT', retry_count=?, "
                "next_retry_at=?, updated_at=? WHERE command_id=?",
                (retry_count, next_retry_at, now, command_id),
            )
            conn.commit()
        finally:
            conn.close()

    def get_retry_count(self, command_id: int) -> int:
        conn = sqlite3.connect(self._db)
        try:
            row = conn.execute(
                "SELECT retry_count FROM ops_execution_commands WHERE command_id=?",
                (command_id,),
            ).fetchone()
            return row[0] if row else 0
        finally:
            conn.close()

    def reset_waiting_to_pending(self, command_id: int) -> None:
        now = _now()
        conn = sqlite3.connect(self._db)
        try:
            conn.execute(
                "UPDATE ops_execution_commands SET status='PENDING', updated_at=? "
                "WHERE command_id=?", (now, command_id)
            )
            conn.commit()
        finally:
            conn.close()

    def get_by_id(self, command_id: int) -> "ExecutionCommand | None":
        conn = sqlite3.connect(self._db)
        try:
            row = conn.execute(
                f"SELECT {_BASE_COLS} FROM ops_execution_commands WHERE command_id=?",
                (command_id,),
            ).fetchone()
            return _cmd_from_row(row) if row else None
        finally:
            conn.close()

    def get_entry_client_order_id(self, trade_chain_id: int) -> str | None:
        conn = sqlite3.connect(self._db)
        try:
            row = conn.execute(
                "SELECT client_order_id FROM ops_execution_commands "
                "WHERE trade_chain_id=? "
                "AND command_type IN ('PLACE_ENTRY', 'PLACE_ENTRY_WITH_ATTACHED_TPSL') "
                "AND client_order_id IS NOT NULL LIMIT 1",
                (trade_chain_id,),
            ).fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    def get_payload_by_client_order_id(self, client_order_id: str) -> dict | None:
        conn = sqlite3.connect(self._db)
        try:
            row = conn.execute(
                "SELECT payload_json FROM ops_execution_commands "
                "WHERE client_order_id=? LIMIT 1",
                (client_order_id,),
            ).fetchone()
            if not row:
                return None
            return json.loads(row[0] or "{}")
        finally:
            conn.close()

    def get_cancel_trigger_metadata(
        self,
        trade_chain_id: int | None,
        entry_client_order_id: str | None,
    ) -> dict:
        """Resolve metadata from the CANCEL_PENDING_ENTRY command that targeted this entry order.

        Exchange cancel confirmations refer to the cancelled order's orderLinkId, which belongs
        to the original PLACE_ENTRY command. For notification and lifecycle semantics we also
        need the trigger that caused the cancellation (trader_update, timeout_worker, etc.).
        """
        if trade_chain_id is None or not entry_client_order_id:
            return {}

        conn = sqlite3.connect(self._db)
        try:
            rows = conn.execute(
                "SELECT command_id, payload_json FROM ops_execution_commands "
                "WHERE trade_chain_id=? AND command_type='CANCEL_PENDING_ENTRY' "
                "ORDER BY command_id DESC",
                (trade_chain_id,),
            ).fetchall()
        finally:
            conn.close()

        placeholder_fallback: tuple[int, dict] | None = None
        for command_id, payload_json in rows:
            try:
                payload = json.loads(payload_json or "{}")
            except Exception:
                continue
            stored_coid = payload.get("entry_client_order_id")
            if stored_coid == entry_client_order_id:
                result = {"cancel_command_id": int(command_id)}
                if payload.get("cancel_origin") is not None:
                    result["cancel_origin"] = payload.get("cancel_origin")
                if payload.get("cancel_reason") is not None:
                    result["cancel_reason"] = payload.get("cancel_reason")
                return result
            # Fallback candidate: command stored with unresolved plan placeholder.
            # expand_cancel_pending_commands may have failed to resolve the real coid
            # at persist time (PLACE_ENTRY not yet ACKed), so the cancel was stored with
            # the plan placeholder.  We use it only if no exact match is found.
            if placeholder_fallback is None and isinstance(stored_coid, str) and (
                stored_coid.startswith("place_entry:") or stored_coid.startswith("place_entry_attached:")
            ):
                placeholder_fallback = (int(command_id), payload)

        if placeholder_fallback is not None:
            command_id, payload = placeholder_fallback
            result: dict = {"cancel_command_id": command_id}
            if payload.get("cancel_origin") is not None:
                result["cancel_origin"] = payload.get("cancel_origin")
            if payload.get("cancel_reason") is not None:
                result["cancel_reason"] = payload.get("cancel_reason")
            return result
        return {}

    def get_command_source(self, trade_chain_id: int, command_id: int) -> str | None:
        """Return command_source from a CLOSE_FULL/CLOSE_PARTIAL command payload."""
        conn = sqlite3.connect(self._db)
        try:
            row = conn.execute(
                "SELECT payload_json FROM ops_execution_commands "
                "WHERE trade_chain_id=? AND command_id=? LIMIT 1",
                (trade_chain_id, command_id),
            ).fetchone()
        finally:
            conn.close()
        if not row:
            return None
        try:
            return json.loads(row[0] or "{}").get("command_source")
        except Exception:
            return None

    def get_chain_filled_entry_qty(self, trade_chain_id: int) -> float | None:
        conn = sqlite3.connect(self._db)
        try:
            row = conn.execute(
                "SELECT filled_entry_qty FROM ops_trade_chains WHERE trade_chain_id=?",
                (trade_chain_id,),
            ).fetchone()
            if not row or row[0] is None:
                return None
            return float(row[0])
        finally:
            conn.close()

    def get_chain_open_position_qty(self, trade_chain_id: int) -> float | None:
        conn = sqlite3.connect(self._db)
        try:
            row = conn.execute(
                "SELECT open_position_qty FROM ops_trade_chains WHERE trade_chain_id=?",
                (trade_chain_id,),
            ).fetchone()
            if not row or row[0] is None:
                return None
            return float(row[0])
        finally:
            conn.close()

    def supersede_tp_partial_commands(
        self,
        trade_chain_id: int,
        exclude_command_id: int,
        *,
        statuses: tuple[str, ...],
    ) -> None:
        """Marks SUPERSEDED matching SET_POSITION_TPSL_PARTIAL commands except the current one."""
        now = _now()
        placeholders = ",".join("?" for _ in statuses)
        conn = sqlite3.connect(self._db)
        try:
            conn.execute(
                "UPDATE ops_execution_commands SET status='SUPERSEDED', updated_at=? "
                "WHERE trade_chain_id=? AND command_type='SET_POSITION_TPSL_PARTIAL' "
                f"AND status IN ({placeholders}) AND command_id != ?",
                (now, trade_chain_id, *statuses, exclude_command_id),
            )
            conn.commit()
        finally:
            conn.close()

    def supersede_rebuild_commands(
        self,
        trade_chain_id: int,
        exclude_command_id: int,
        *,
        statuses: tuple[str, ...],
    ) -> None:
        now = _now()
        placeholders = ",".join("?" for _ in statuses)
        conn = sqlite3.connect(self._db)
        try:
            conn.execute(
                "UPDATE ops_execution_commands SET status='SUPERSEDED', updated_at=? "
                "WHERE trade_chain_id=? AND command_type='REBUILD_PARTIAL_TPS' "
                f"AND status IN ({placeholders}) AND command_id != ?",
                (now, trade_chain_id, *statuses, exclude_command_id),
            )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _expand_active_tp_payload(command_type: str, payload: dict) -> list[dict]:
        if command_type == "REBUILD_PARTIAL_TPS":
            tp_items = payload.get("tps")
            if not isinstance(tp_items, list):
                return []
            expanded: list[dict] = []
            for tp_item in tp_items:
                if not isinstance(tp_item, dict):
                    continue
                try:
                    expanded.append({
                        "tp_sequence": int(tp_item["sequence"]),
                        "take_profit": float(tp_item["price"]),
                        "tp_size": float(tp_item["qty"]),
                        "tp_order_type": tp_item.get("order_type", "Limit"),
                        "tp_limit_price": tp_item.get("limit_price"),
                        "tp_trigger_by": tp_item.get("trigger_by", "MarkPrice"),
                    })
                except (KeyError, TypeError, ValueError):
                    continue
            return expanded

        if command_type in {"SET_POSITION_TPSL_PARTIAL", "SET_POSITION_TPSL_FULL"}:
            return [payload]

        return []

    def count_active_tps(self, trade_chain_id: int) -> int:
        """Counts active TP levels (not just rows) for the chain."""
        conn = sqlite3.connect(self._db)
        try:
            rows = conn.execute(
                "SELECT command_type, payload_json FROM ops_execution_commands "
                "WHERE trade_chain_id=? "
                "AND command_type IN ('SET_POSITION_TPSL_PARTIAL', 'SET_POSITION_TPSL_FULL', 'REBUILD_PARTIAL_TPS') "
                "AND status IN ('SENT', 'DONE')",
                (trade_chain_id,),
            ).fetchall()
            total = 0
            for command_type, payload_json in rows:
                try:
                    payload = json.loads(payload_json or "{}")
                except Exception:
                    continue
                total += len(self._expand_active_tp_payload(command_type, payload))
            return total
        finally:
            conn.close()

    def insert_exchange_event(
        self,
        trade_chain_id: int,
        event_type: str,
        payload_json: str,
        idempotency_key: str,
    ) -> bool:
        """INSERT OR IGNORE in ops_exchange_events. Idempotente. Ritorna True se la riga è stata inserita."""
        now = _now()
        conn = sqlite3.connect(self._db)
        try:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO ops_exchange_events "
                "(trade_chain_id, event_type, payload_json, processing_status, "
                "idempotency_key, received_at) VALUES (?,?,?,?,?,?)",
                (trade_chain_id, event_type, payload_json, "NEW", idempotency_key, now),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    def has_exchange_event_for_order(
        self,
        trade_chain_id: int,
        event_type: str,
        order_id: str | None,
    ) -> bool:
        """True se esiste già un evento per lo stesso ordine (stesso chain+tipo+order_id).

        Le chiavi di idempotenza WS (fill:{execId}) e REST ({event_type}:{chain}:{order_id})
        divergono: questo check evita che la reconciliation REST reinserisca un fill
        già registrato dal WebSocket.
        """
        if not order_id:
            return False
        conn = sqlite3.connect(self._db)
        try:
            row = conn.execute(
                "SELECT 1 FROM ops_exchange_events "
                "WHERE trade_chain_id=? AND event_type=? "
                "AND json_extract(payload_json, '$.order_id')=? LIMIT 1",
                (trade_chain_id, event_type, order_id),
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    def get_active_tp_commands(self, trade_chain_id: int) -> list[dict]:
        """TP attivi SENT/DONE per chain OPEN/PARTIALLY_CLOSED.

        Usato da watchMyTrades per confrontare il fill price con i TP attivi.
        """
        conn = sqlite3.connect(self._db)
        try:
            rows = conn.execute(
                "SELECT c.command_type, c.payload_json "
                "FROM ops_execution_commands c "
                "JOIN ops_trade_chains t ON c.trade_chain_id = t.trade_chain_id "
                "WHERE c.trade_chain_id = ? "
                "AND c.command_type IN ('SET_POSITION_TPSL_PARTIAL', 'SET_POSITION_TPSL_FULL', 'REBUILD_PARTIAL_TPS') "
                "AND c.status IN ('SENT', 'DONE') "
                "AND t.lifecycle_state IN ('OPEN', 'PARTIALLY_CLOSED')",
                (trade_chain_id,),
            ).fetchall()
            result = []
            for command_type, payload_json in rows:
                try:
                    payload = json.loads(payload_json or "{}")
                except Exception:
                    continue
                result.extend(self._expand_active_tp_payload(command_type, payload))
            return result
        finally:
            conn.close()

    def get_open_chains_for_symbol(
        self, symbol: str, side: str, account_id: str | None = None
    ) -> list[int]:
        """Lista di trade_chain_id OPEN/PARTIALLY_CLOSED per symbol+side.

        Usato da watchMyTrades per trovare le chain candidate per un fill TP.
        `side` è il lato della posizione (LONG/SHORT), non il lato del fill.
        Con `account_id` la ricerca è ristretta alle chain di quell'account —
        necessario in per_trader_subaccount, dove symbol+side può essere aperto
        su più account contemporaneamente.
        """
        sql = (
            "SELECT trade_chain_id FROM ops_trade_chains "
            "WHERE symbol=? AND side=? "
            "AND lifecycle_state IN ('OPEN', 'PARTIALLY_CLOSED')"
        )
        params: list = [symbol, side]
        if account_id is not None:
            sql += " AND account_id=?"
            params.append(account_id)
        conn = sqlite3.connect(self._db)
        try:
            rows = conn.execute(sql, params).fetchall()
            return [int(r[0]) for r in rows]
        finally:
            conn.close()

    def resolve_chain_for_fill(
        self, symbol: str, side: str, account_id: str | None = None
    ) -> int | None:
        """Return the unique open chain_id for symbol+side, or None if 0 or >1.

        Used to attribute TP/SL fills and funding executions that lack an
        orderLinkId (Bybit position-level orders never carry orderLinkId).
        Returns None when attribution is ambiguous (multiple open chains on the
        same symbol+side within the account scope) to avoid mis-routing.

        `side` must be the position side: 'LONG' or 'SHORT'.
        `account_id` scopes resolution to one account (per_trader_subaccount).
        """
        chains = self.get_open_chains_for_symbol(symbol, side, account_id)
        return chains[0] if len(chains) == 1 else None

    # ------------------------------------------------------------------
    # New methods: exchange-centric event ingest
    # ------------------------------------------------------------------

    def insert_raw_and_classified(self, classified: "ClassifiedEvent") -> bool:
        """Insert into exchange_raw_events (audit) and ops_exchange_events (lifecycle).

        Returns True if the exchange_raw_events row was actually inserted (not a duplicate).
        Both inserts are done inside a single transaction using INSERT OR IGNORE for idempotency.
        ops_exchange_events is only written when classified.should_forward_to_lifecycle is True.
        """
        raw = classified.raw
        now = _now()

        # Build ops_exchange_events idempotency key
        # Fill events from execution streams are deduplicated by exchange identity (execId),
        # not by semantic classification. This allows two TP fills for the same chain
        # (e.g. TP1 partial + TP final, both tp_level=None) to coexist without collision.
        if classified.event_type in _EXCHANGE_IDENTITY_TYPES and raw.exchange_event_id:
            ops_idem_key = f"fill:{raw.exchange_event_id}"
        elif classified.event_type == "ENTRY_FILLED":
            # ENTRY_FILLED uses order_id for WS/REST convergence
            _order_anchor = raw.order_id or raw.exchange_event_id
            ops_idem_key = f"ENTRY_FILLED:{classified.trade_chain_id}:{_order_anchor}"
        else:
            # Non-fill events (order snapshots, position snapshots, reconciliation-inferred)
            # and fill events where exchange_event_id is absent use semantic keys.
            ops_idem_key = f"{classified.event_type}:{classified.trade_chain_id}"

        # Se l'ordine appartiene a un comando (orderLinkId tsb:...), il source del
        # payload riflette l'origine del comando (es. trader_update) — stessa
        # attribuzione del path REST (event_sync._save_fill_event). Il classifier
        # non può saperlo: conosce solo i campi exchange.
        effective_source = classified.source
        command_id: int | None = None
        if raw.order_link_id:
            try:
                coid = coid_mod.parse(raw.order_link_id)
            except ValueError:
                coid = None
            if coid is not None:
                command_id = coid.command_id
                cmd_source = self.get_command_source(coid.trade_chain_id, coid.command_id)
                if cmd_source:
                    effective_source = cmd_source

        ep = ExchangeEventPayload(
            fill_price=raw.exec_price,
            filled_qty=raw.exec_qty,
            closed_size=raw.closed_size,
            exec_fee=raw.exec_fee,
            fee_rate=raw.fee_rate,
            exec_value=raw.exec_value,
            pos_qty=raw.pos_qty,
            leaves_qty=raw.leaves_qty,
            cum_exec_qty=raw.cum_exec_qty,
            exchange_event_id=raw.exchange_event_id,
            order_id=raw.order_id,
            order_link_id=raw.order_link_id,
            exchange_time=raw.exchange_time,
            tp_level=classified.tp_level,
            command_id=command_id,
            source=effective_source,
        )
        payload = ep.model_dump()
        if classified.event_type == "PENDING_ENTRY_CANCELLED":
            payload.update(
                self.get_cancel_trigger_metadata(
                    classified.trade_chain_id,
                    raw.order_link_id,
                )
            )
        payload_json_str = json.dumps(payload)

        conn = sqlite3.connect(self._db)
        try:
            cursor = conn.execute(
                """INSERT OR IGNORE INTO exchange_raw_events (
                    exchange_event_id, source_stream, symbol, side,
                    create_type, stop_order_type, exec_type, order_status,
                    order_link_id, order_id, seq, exec_price, exec_qty,
                    closed_size, leaves_qty, pos_qty, exec_value, exec_fee,
                    fee_rate, cum_exec_qty, position_take_profit, position_stop_loss,
                    classified_event_type, classified_source, trade_chain_id, tp_level,
                    forwarded_to_lifecycle, raw_info_json, exchange_time, received_at,
                    idempotency_key
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    raw.exchange_event_id, raw.source_stream, raw.symbol, raw.side,
                    raw.create_type, raw.stop_order_type, raw.exec_type, raw.order_status,
                    raw.order_link_id, raw.order_id, raw.seq, raw.exec_price, raw.exec_qty,
                    raw.closed_size, raw.leaves_qty, raw.pos_qty, raw.exec_value, raw.exec_fee,
                    raw.fee_rate, raw.cum_exec_qty, raw.position_take_profit, raw.position_stop_loss,
                    classified.event_type, classified.source, classified.trade_chain_id, classified.tp_level,
                    1 if classified.should_forward_to_lifecycle else 0,
                    json.dumps(raw.raw_info),
                    raw.exchange_time,
                    raw.received_at or now,
                    raw.idempotency_key,
                ),
            )
            rowcount = cursor.rowcount

            if classified.should_forward_to_lifecycle:
                conn.execute(
                    "INSERT OR IGNORE INTO ops_exchange_events "
                    "(trade_chain_id, event_type, payload_json, processing_status, idempotency_key, received_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (
                        classified.trade_chain_id,
                        classified.event_type,
                        payload_json_str,
                        "NEW",
                        ops_idem_key,
                        raw.received_at or now,
                    ),
                )

            conn.commit()
            return rowcount > 0
        finally:
            conn.close()

    def get_known_order_link_ids(self) -> dict[str, tuple[int, str, int]]:
        """Returns mapping orderLinkId → (trade_chain_id, role, sequence) for the classifier."""
        _role_map: dict[str, str] = {
            "PLACE_ENTRY": "entry",
            "PLACE_ENTRY_WITH_ATTACHED_TPSL": "entry",
            "SET_POSITION_TPSL_PARTIAL": "tp_1",
            "SET_POSITION_TPSL_FULL": "tp_1",
            "REBUILD_PARTIAL_TPS": "tp_multi",
            "SET_STOP_LOSS": "sl",
            "CLOSE_PARTIAL": "exit_partial",
            "CLOSE_FULL": "exit_full",
        }
        conn = sqlite3.connect(self._db)
        try:
            rows = conn.execute(
                "SELECT client_order_id, trade_chain_id, command_type, command_id "
                "FROM ops_execution_commands "
                "WHERE status IN ('SENT', 'ACK', 'DONE') "
                "  AND client_order_id IS NOT NULL "
                "  AND client_order_id != '' "
                "ORDER BY command_id ASC"
            ).fetchall()
            result: dict[str, tuple[int, str, int]] = {}
            for client_order_id, trade_chain_id, command_type, command_id in rows:
                role = _role_map.get(command_type, "unknown")
                result[client_order_id] = (int(trade_chain_id), role, int(command_id))
            return result
        finally:
            conn.close()

    def get_open_chains_with_tps(self, account_id: str | None = None) -> list[dict]:
        """Returns open chains that have active TP commands. Used by run_trade_based_reconciliation.

        With `account_id`, only that account's chains are returned — each sync
        worker polls its own adapter and must not attribute fills to chains
        whose position lives on a different subaccount.
        """
        sql = (
            "SELECT DISTINCT t.trade_chain_id, t.symbol, t.side "
            "FROM ops_trade_chains t "
            "JOIN ops_execution_commands c ON c.trade_chain_id = t.trade_chain_id "
            "WHERE t.lifecycle_state IN ('OPEN', 'PARTIALLY_CLOSED') "
            "  AND c.command_type IN ('SET_POSITION_TPSL_PARTIAL', 'SET_POSITION_TPSL_FULL', 'REBUILD_PARTIAL_TPS') "
            "  AND c.status IN ('SENT', 'DONE')"
        )
        params: list = []
        if account_id is not None:
            sql += " AND t.account_id=?"
            params.append(account_id)
        conn = sqlite3.connect(self._db)
        try:
            rows = conn.execute(sql, params).fetchall()
            return [{"trade_chain_id": int(r[0]), "symbol": r[1], "side": r[2]} for r in rows]
        finally:
            conn.close()

    def tp_fill_exists(self, trade_chain_id: int, tp_level: int | None = None) -> bool:
        """Checks if any TP_FILLED event exists for this chain.

        tp_level is accepted but ignored: with identity-based dedupe keys, two fills
        for the same chain have distinct keys regardless of tp_level.
        """
        conn = sqlite3.connect(self._db)
        try:
            row = conn.execute(
                "SELECT 1 FROM ops_exchange_events "
                "WHERE trade_chain_id = ? AND event_type = 'TP_FILLED' LIMIT 1",
                (trade_chain_id,),
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    def real_close_fill_exists(self, trade_chain_id: int) -> bool:
        """Returns True if a real exchange fill that closes the chain exists.

        Used by position reconciliation to avoid inserting a synthetic CLOSE_FULL_FILLED
        when the WS or REST path has already recorded the actual fill.
        """
        conn = sqlite3.connect(self._db)
        try:
            row = conn.execute(
                "SELECT 1 FROM ops_exchange_events "
                "WHERE trade_chain_id = ? "
                "  AND event_type IN ("
                "    'TP_FILLED', 'SL_FILLED', 'MANUAL_CLOSE_FULL', 'LIQUIDATION_FILLED'"
                "  ) LIMIT 1",
                (trade_chain_id,),
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    def protective_cancelled_exists(self, trade_chain_id: int) -> bool:
        """Checks if a PROTECTIVE_ORDER_CANCELLED event already exists for this chain."""
        conn = sqlite3.connect(self._db)
        try:
            row = conn.execute(
                "SELECT 1 FROM ops_exchange_events "
                "WHERE trade_chain_id = ? "
                "  AND event_type = 'PROTECTIVE_ORDER_CANCELLED' "
                "LIMIT 1",
                (trade_chain_id,),
            ).fetchone()
            return row is not None
        finally:
            conn.close()


__all__ = ["GatewayCommandRepository"]
