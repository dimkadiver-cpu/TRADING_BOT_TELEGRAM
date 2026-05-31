# src/runtime_v2/execution_gateway/repositories.py
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from src.runtime_v2.lifecycle.models import ExecutionCommand

if TYPE_CHECKING:
    from src.runtime_v2.execution_gateway.event_ingest.models import ClassifiedEvent


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
                    "SELECT lifecycle_state FROM ops_trade_chains WHERE trade_chain_id=?",
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
            return True
        finally:
            conn.close()

    def mark_review_required(self, command_id: int, *, reason: str) -> None:
        now = _now()
        result = {"error": None, "reason": reason}
        conn = sqlite3.connect(self._db)
        try:
            conn.execute(
                "UPDATE ops_execution_commands SET status='REVIEW_REQUIRED', "
                "result_payload_json=?, updated_at=? WHERE command_id=?",
                (json.dumps(result), now, command_id),
            )
            conn.commit()
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

    def get_open_chains_for_symbol(self, symbol: str, side: str) -> list[int]:
        """Lista di trade_chain_id OPEN/PARTIALLY_CLOSED per symbol+side.

        Usato da watchMyTrades per trovare le chain candidate per un fill TP.
        `side` è il lato della posizione (LONG/SHORT), non il lato del fill.
        """
        conn = sqlite3.connect(self._db)
        try:
            rows = conn.execute(
                "SELECT trade_chain_id FROM ops_trade_chains "
                "WHERE symbol=? AND side=? "
                "AND lifecycle_state IN ('OPEN', 'PARTIALLY_CLOSED')",
                (symbol, side),
            ).fetchall()
            return [int(r[0]) for r in rows]
        finally:
            conn.close()

    def resolve_chain_for_fill(self, symbol: str, side: str) -> int | None:
        """Return the unique open chain_id for symbol+side, or None if 0 or >1.

        Used to attribute TP/SL fills that lack an orderLinkId (Bybit position-level
        orders never carry orderLinkId). Returns None when attribution is ambiguous
        (multiple open chains on the same symbol) to avoid mis-routing.

        `side` must be the position side: 'LONG' or 'SHORT'.
        """
        chains = self.get_open_chains_for_symbol(symbol, side)
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
        if classified.event_type == "TP_FILLED":
            if classified.tp_level is not None:
                ops_idem_key = f"TP_FILLED:{classified.trade_chain_id}:level:{classified.tp_level}"
            else:
                ops_idem_key = f"TP_FILLED:{classified.trade_chain_id}"
        elif classified.event_type == "ENTRY_FILLED":
            # Include order_id so the key matches the REST reconciliation path
            # (event_sync._save_fill_event uses "{event_type}:{chain_id}:{exchange_order_id}").
            # raw.order_id is the exchange orderId from watch_my_trades — same UUID used by REST.
            # Fallback to exchange_event_id (execId) if order_id is absent.
            _order_anchor = raw.order_id or raw.exchange_event_id
            ops_idem_key = f"ENTRY_FILLED:{classified.trade_chain_id}:{_order_anchor}"
        else:
            ops_idem_key = f"{classified.event_type}:{classified.trade_chain_id}"

        payload = {
            "fill_price": raw.exec_price,
            "filled_qty": raw.exec_qty,
            "closed_size": raw.closed_size,
            "exec_fee": raw.exec_fee,
            "pos_qty": raw.pos_qty,
            "symbol": raw.symbol,
            "side": raw.side,
            "source": classified.source,
            "tp_level": classified.tp_level,
            "exchange_event_id": raw.exchange_event_id,
        }

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
                        json.dumps(payload),
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

    def get_open_chains_with_tps(self) -> list[dict]:
        """Returns open chains that have active TP commands. Used by run_trade_based_reconciliation."""
        conn = sqlite3.connect(self._db)
        try:
            rows = conn.execute(
                "SELECT DISTINCT t.trade_chain_id, t.symbol, t.side "
                "FROM ops_trade_chains t "
                "JOIN ops_execution_commands c ON c.trade_chain_id = t.trade_chain_id "
                "WHERE t.lifecycle_state IN ('OPEN', 'PARTIALLY_CLOSED') "
                "  AND c.command_type IN ('SET_POSITION_TPSL_PARTIAL', 'SET_POSITION_TPSL_FULL', 'REBUILD_PARTIAL_TPS') "
                "  AND c.status IN ('SENT', 'DONE')"
            ).fetchall()
            return [{"trade_chain_id": int(r[0]), "symbol": r[1], "side": r[2]} for r in rows]
        finally:
            conn.close()

    def tp_fill_exists(self, trade_chain_id: int, tp_level: int | None) -> bool:
        """Checks if a TP_FILLED event already exists for this chain/level. Idempotency check."""
        if tp_level is not None:
            idem_key = f"TP_FILLED:{trade_chain_id}:level:{tp_level}"
        else:
            idem_key = f"TP_FILLED:{trade_chain_id}"
        conn = sqlite3.connect(self._db)
        try:
            row = conn.execute(
                "SELECT 1 FROM ops_exchange_events "
                "WHERE trade_chain_id = ? "
                "  AND event_type = 'TP_FILLED' "
                "  AND idempotency_key = ? "
                "LIMIT 1",
                (trade_chain_id, idem_key),
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
