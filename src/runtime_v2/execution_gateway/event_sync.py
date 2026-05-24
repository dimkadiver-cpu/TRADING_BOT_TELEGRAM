# src/runtime_v2/execution_gateway/event_sync.py
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone

from src.runtime_v2.execution_gateway import client_order_id as coid_mod
from src.runtime_v2.execution_gateway.adapters.base import ExecutionAdapter
from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ExchangeEventSyncWorker:
    def __init__(
        self,
        ops_db_path: str,
        adapter: ExecutionAdapter,
        repo: GatewayCommandRepository,
        execution_account_id: str,
    ) -> None:
        self._ops_db = ops_db_path
        self._adapter = adapter
        self._repo = repo
        self._execution_account_id = execution_account_id

    def run_once(self) -> int:
        return self.run_reconciliation()

    def run_reconciliation(self) -> int:
        active = self._repo.get_sent_or_ack()
        processed = 0

        for cmd, client_order_id in active:
            if not client_order_id:
                continue
            try:
                raw = self._adapter.get_order_status(
                    client_order_id=client_order_id,
                    execution_account_id=self._execution_account_id,
                )
                if raw and raw.is_filled:
                    saved = self._normalize_and_save(client_order_id, raw)
                    if saved:
                        self._repo.mark_done(cmd.command_id)
                        processed += 1
                elif raw and raw.status == "CANCELLED":
                    saved = self._handle_cancelled_order(client_order_id, raw)
                    if saved:
                        self._repo.mark_done(cmd.command_id)
                        processed += 1
            except Exception:
                logger.exception("reconciliation error for %s", client_order_id)

        return processed

    def run_position_reconciliation(self) -> int:
        """Detect positions closed externally on the exchange (manual close)."""
        chains = self._get_open_chains()
        processed = 0
        for chain_id, symbol, side, open_qty in chains:
            try:
                qty = self._adapter.get_position_qty(
                    symbol=symbol,
                    side=side,
                    execution_account_id=self._execution_account_id,
                )
                if qty is None:
                    continue
                if qty == 0.0 and open_qty > 0.0:
                    saved = self._save_externally_closed(chain_id, symbol, side, open_qty)
                    if saved:
                        logger.info(
                            "externally closed position detected: chain=%s %s %s qty=%s",
                            chain_id, symbol, side, open_qty,
                        )
                        processed += 1
            except Exception:
                logger.exception("position reconciliation error for chain %s", chain_id)
        return processed

    def run_tp_reconciliation(self) -> int:
        """Detect TP partial closes by checking if position has decreased."""
        sent_tp_cmds = self._get_sent_tp_commands()
        processed = 0
        for cmd_id, trade_chain_id, tp_size, tp_level, symbol, side in sent_tp_cmds:
            try:
                # Check if TP event already exists
                if self._tp_fill_event_exists(trade_chain_id, tp_level):
                    # Already recorded, nothing to do
                    continue

                # Get current position qty
                qty = self._adapter.get_position_qty(
                    symbol=symbol,
                    side=side,
                    execution_account_id=self._execution_account_id,
                )
                if qty is None:
                    continue

                # Get filled entry qty at time of TP command (approximation: current filled_entry_qty)
                filled_qty = self._get_filled_entry_qty(trade_chain_id)
                if filled_qty is None:
                    continue

                # If position has decreased by at least tp_size, TP was likely hit
                # Check with some tolerance for slippage
                expected_after_tp = filled_qty - tp_size
                if qty < expected_after_tp * 0.95:  # 5% tolerance for slippage
                    saved = self._save_tp_fill(trade_chain_id, tp_level, cmd_id, qty)
                    if saved:
                        logger.info(
                            "TP partial fill detected: chain=%s level=%s qty_closed=%s",
                            trade_chain_id, tp_level, tp_size,
                        )
                        processed += 1
            except Exception:
                logger.exception("TP reconciliation error for command %s", cmd_id)
        return processed

    def _get_sent_tp_commands(self) -> list[tuple[int, int, float, int, str, str]]:
        """Get all placed TP commands for open chains.

        SET_POSITION_TPSL_PARTIAL/FULL are fire-and-forget (DONE immediately after
        being sent to the exchange).  We therefore query DONE commands here — not SENT
        — and restrict to chains that are still actively open so we don't re-check
        commands belonging to already-closed or terminal chains.
        """
        conn = sqlite3.connect(self._ops_db)
        try:
            rows = conn.execute(
                "SELECT c.command_id, c.trade_chain_id, c.payload_json, t.symbol, t.side "
                "FROM ops_execution_commands c "
                "JOIN ops_trade_chains t ON c.trade_chain_id = t.trade_chain_id "
                "WHERE c.command_type IN ('SET_POSITION_TPSL_PARTIAL', 'SET_POSITION_TPSL_FULL') "
                "AND c.status IN ('SENT', 'DONE') "
                "AND t.lifecycle_state IN ('OPEN', 'PARTIALLY_CLOSED')"
            ).fetchall()
            result = []
            for cmd_id, chain_id, payload_json, symbol, side in rows:
                try:
                    payload = json.loads(payload_json)
                    tp_size = float(payload.get("tp_size", 0))
                    tp_level = int(payload.get("tp_sequence", 1))
                    result.append((cmd_id, chain_id, tp_size, tp_level, symbol, side))
                except Exception:
                    pass
            return result
        finally:
            conn.close()

    def _tp_fill_event_exists(self, trade_chain_id: int, tp_level: int) -> bool:
        """Check if TP_FILLED event already exists for this TP level."""
        conn = sqlite3.connect(self._ops_db)
        try:
            rows = conn.execute(
                "SELECT payload_json FROM ops_exchange_events "
                "WHERE trade_chain_id = ? AND event_type = 'TP_FILLED'",
                (trade_chain_id,)
            ).fetchall()
            for row in rows:
                try:
                    payload = json.loads(row[0])
                    if payload.get("tp_level") == tp_level:
                        return True
                except Exception:
                    pass
            return False
        finally:
            conn.close()

    def _get_filled_entry_qty(self, trade_chain_id: int) -> float | None:
        """Get current filled entry qty for a trade chain."""
        conn = sqlite3.connect(self._ops_db)
        try:
            row = conn.execute(
                "SELECT filled_entry_qty FROM ops_trade_chains WHERE trade_chain_id = ?",
                (trade_chain_id,)
            ).fetchone()
            return float(row[0]) if row else None
        finally:
            conn.close()

    def _save_tp_fill(self, trade_chain_id: int, tp_level: int, command_id: int, current_qty: float) -> bool:
        """Save TP_FILLED exchange event."""
        idempotency_key = f"TP_FILLED:{trade_chain_id}:level:{tp_level}"
        payload = json.dumps({
            "tp_level": tp_level,
            "is_final": current_qty == 0.0,
            "fill_price": None,  # Unknown without trade details
            "filled_qty": None,  # Unknown without trade details
            "command_id": command_id,
        })
        now = _now()
        conn = sqlite3.connect(self._ops_db)
        try:
            conn.execute(
                "INSERT OR IGNORE INTO ops_exchange_events "
                "(trade_chain_id, event_type, payload_json, processing_status, "
                "idempotency_key, received_at) VALUES (?,?,?,?,?,?)",
                (trade_chain_id, "TP_FILLED", payload, "NEW", idempotency_key, now),
            )
            conn.commit()
            return True
        finally:
            conn.close()

    def _get_open_chains(self) -> list[tuple[int, str, str, float]]:
        conn = sqlite3.connect(self._ops_db)
        try:
            rows = conn.execute(
                "SELECT trade_chain_id, symbol, side, open_position_qty "
                "FROM ops_trade_chains "
                "WHERE lifecycle_state IN ('OPEN', 'PARTIALLY_CLOSED')"
            ).fetchall()
            return [(int(r[0]), str(r[1]), str(r[2]), float(r[3] or 0.0)) for r in rows]
        finally:
            conn.close()

    def _save_externally_closed(
        self, chain_id: int, symbol: str, side: str, open_qty: float
    ) -> bool:
        idempotency_key = f"CLOSE_FULL_FILLED:ext:{chain_id}"
        payload = json.dumps({"filled_qty": open_qty, "fill_price": None, "source": "position_reconciliation"})
        now = _now()
        conn = sqlite3.connect(self._ops_db)
        try:
            conn.execute(
                "INSERT OR IGNORE INTO ops_exchange_events "
                "(trade_chain_id, event_type, payload_json, processing_status, "
                "idempotency_key, received_at) VALUES (?,?,?,?,?,?)",
                (chain_id, "CLOSE_FULL_FILLED", payload, "NEW", idempotency_key, now),
            )
            conn.commit()
            return True
        finally:
            conn.close()

    def _handle_cancelled_order(self, client_order_id: str, raw) -> bool:
        try:
            coid = coid_mod.parse(client_order_id)
        except ValueError:
            logger.warning("cannot parse client_order_id: %s", client_order_id)
            return False

        if coid.role != "entry" and raw.filled_qty > 0.0:
            logger.warning(
                "cancelled non-entry order has fill: coid=%s status=%s filled_qty=%s reason=%s",
                client_order_id, raw.status, raw.filled_qty, raw.cancel_reason,
            )
            return self._normalize_and_save(client_order_id, raw)

        if coid.role != "entry":
            # Non-entry orders cancelled externally: stop polling, no lifecycle event needed.
            logger.warning("cancelled non-entry order detected: %s — marking done", client_order_id)
            return True

        exchange_order_id = raw.exchange_order_id or client_order_id
        position_already_open = raw.filled_qty > 0.0
        if raw.cancel_reason:
            logger.warning(
                "entry order cancelled by exchange: coid=%s reason=%s",
                client_order_id, raw.cancel_reason,
            )
        idempotency_key = f"PENDING_ENTRY_CANCELLED_CONFIRMED:{coid.trade_chain_id}:{exchange_order_id}"
        payload = json.dumps({
            "command_id": coid.command_id,
            "position_already_open": position_already_open,
            "cancel_reason": raw.cancel_reason,
        })
        now = _now()
        conn = sqlite3.connect(self._ops_db)
        try:
            conn.execute(
                "INSERT OR IGNORE INTO ops_exchange_events "
                "(trade_chain_id, event_type, payload_json, processing_status, "
                "idempotency_key, received_at) VALUES (?,?,?,?,?,?)",
                (coid.trade_chain_id, "PENDING_ENTRY_CANCELLED_CONFIRMED", payload,
                 "NEW", idempotency_key, now),
            )
            conn.commit()
            return True
        finally:
            conn.close()

    def _normalize_and_save(self, client_order_id: str, raw) -> bool:
        try:
            coid = coid_mod.parse(client_order_id)
        except ValueError:
            logger.warning("cannot parse client_order_id: %s", client_order_id)
            return False

        exchange_order_id = raw.exchange_order_id or client_order_id

        if coid.role == "entry":
            event_type = "ENTRY_FILLED"
            payload = {
                "fill_price": raw.average_price,
                "filled_qty": raw.filled_qty,
                "command_id": coid.command_id,
            }
        elif coid.role == "sl":
            event_type = "SL_FILLED"
            payload = {
                "fill_price": raw.average_price,
                "filled_qty": raw.filled_qty,
                "command_id": coid.command_id,
            }
        elif coid.role == "exit_partial":
            event_type = "CLOSE_PARTIAL_FILLED"
            payload = {
                "fill_price": raw.average_price,
                "filled_qty": raw.filled_qty,
                "command_id": coid.command_id,
            }
        elif coid.role == "exit_full":
            event_type = "CLOSE_FULL_FILLED"
            payload = {
                "fill_price": raw.average_price,
                "filled_qty": raw.filled_qty,
                "command_id": coid.command_id,
            }
        elif coid.role == "tp":
            event_type = "TP_FILLED"
            payload = {
                "tp_level": coid.sequence,
                "is_final": self._repo.count_active_tps(coid.trade_chain_id) <= 1,
                "fill_price": raw.average_price,
                "filled_qty": raw.filled_qty,
                "command_id": coid.command_id,
            }
        else:
            logger.warning("unknown role '%s' in %s — skipping mark_done", coid.role, client_order_id)
            return False

        if coid.role == "tp":
            idempotency_key = f"TP_FILLED:{coid.trade_chain_id}:level:{coid.sequence}"
        else:
            idempotency_key = f"{event_type}:{coid.trade_chain_id}:{exchange_order_id}"
        now = _now()
        conn = sqlite3.connect(self._ops_db)
        try:
            conn.execute(
                "INSERT OR IGNORE INTO ops_exchange_events "
                "(trade_chain_id, event_type, payload_json, processing_status, "
                "idempotency_key, received_at) VALUES (?,?,?,?,?,?)",
                (coid.trade_chain_id, event_type, json.dumps(payload),
                 "NEW", idempotency_key, now),
            )
            conn.commit()
            return True
        finally:
            conn.close()


__all__ = ["ExchangeEventSyncWorker"]
