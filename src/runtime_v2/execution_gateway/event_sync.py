# src/runtime_v2/execution_gateway/event_sync.py
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone

from src.runtime_v2.execution_gateway import client_order_id as coid_mod
from src.runtime_v2.execution_gateway.adapters.base import ExecutionAdapter
from src.runtime_v2.execution_gateway.models import RawAdapterTrade
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

    def run_trade_based_reconciliation(self) -> int:
        """Poll recent reduceOnly fills via REST and match against active TP commands.

        Replaces run_tp_reconciliation(). Uses real fill prices (not qty comparison).
        Shares the same idempotency key format as watchMyTrades so INSERT OR IGNORE
        prevents duplicates when both paths run.

        Returns count of new TP_FILLED events inserted.
        """
        if not hasattr(self._adapter, "fetch_recent_reduce_trades"):
            return 0

        entries = self._get_tp_reconciliation_entries()
        if not entries:
            return 0

        # Group by (symbol, side) to minimise API calls
        from collections import defaultdict
        by_key: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for e in entries:
            by_key[(e["symbol"], e["side"])].append(e)

        processed = 0
        for (symbol, side), group in by_key.items():
            try:
                trades = self._adapter.fetch_recent_reduce_trades(
                    symbol=symbol,
                    side=side,
                    execution_account_id=self._execution_account_id,
                    limit=50,
                )
            except Exception:
                logger.exception("fetch_recent_reduce_trades error for %s %s", symbol, side)
                continue

            for entry in group:
                chain_id = entry["chain_id"]
                tp_level = entry["tp_level"]
                tp_price = entry["tp_price"]

                if self._tp_fill_event_exists(chain_id, tp_level):
                    continue  # already recorded (e.g. by WS)

                if tp_price <= 0:
                    continue  # no valid TP price in command payload

                for trade in trades:
                    if abs(trade.price - tp_price) / tp_price <= 0.01:  # ±1% tolerance
                        if self._save_tp_fill_from_trade(chain_id, tp_level, trade):
                            processed += 1
                        break  # one trade per TP level

        return processed

    def _get_tp_reconciliation_entries(self) -> list[dict]:
        """Return active TP commands for open chains with price, level, and symbol."""
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
            result: list[dict] = []
            for cmd_id, chain_id, payload_json, symbol, side in rows:
                try:
                    payload = json.loads(payload_json)
                    result.append({
                        "cmd_id":   cmd_id,
                        "chain_id": chain_id,
                        "tp_level": int(payload.get("tp_sequence", 1)),
                        "tp_price": float(payload.get("take_profit", 0)),
                        "tp_size":  float(payload.get("tp_size", 0)),
                        "symbol":   symbol,
                        "side":     side,
                    })
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

    def _save_tp_fill_from_trade(
        self,
        trade_chain_id: int,
        tp_level: int,
        trade: RawAdapterTrade,
    ) -> bool:
        """INSERT OR IGNORE TP_FILLED event with real fill price from trade REST data."""
        idempotency_key = f"TP_FILLED:{trade_chain_id}:level:{tp_level}"
        payload = json.dumps({
            "tp_level":          tp_level,
            "is_final":          False,   # conservative; lifecycle uses position qty to decide
            "fill_price":        trade.price,
            "filled_qty":        trade.amount,
            "source":            "trade_based_reconciliation",
            "exchange_trade_id": trade.trade_id,
        })
        now = _now()
        conn = sqlite3.connect(self._ops_db)
        try:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO ops_exchange_events "
                "(trade_chain_id, event_type, payload_json, processing_status, "
                "idempotency_key, received_at) VALUES (?,?,?,?,?,?)",
                (trade_chain_id, "TP_FILLED", payload, "NEW", idempotency_key, now),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    def run_protective_orders_reconciliation(self) -> int:
        """Detect when a position-level TP was externally cancelled (no fill occurred).

        Logic:
          1. For each open chain with an active SET_POSITION_TPSL_* command:
             - Fetch the live position from the exchange
             - If exchange takeProfit == 0.0 (cleared) but we expected a TP:
                 - And no TP_FILLED event is recorded (= it didn't trigger, it was cancelled)
                 - → Insert PROTECTIVE_ORDERS_MISSING event

        The lifecycle event processor currently logs a warning for unhandled event types.
        A full automated response (re-placing the TP) is a future enhancement.

        Returns count of new PROTECTIVE_ORDERS_MISSING events inserted.
        """
        if not hasattr(self._adapter, "fetch_position_details"):
            return 0

        entries = self._get_tp_reconciliation_entries()
        if not entries:
            return 0

        # Use the most recent TP command per chain (highest cmd_id)
        latest_per_chain: dict[int, dict] = {}
        for e in entries:
            chain_id = e["chain_id"]
            if chain_id not in latest_per_chain or e["cmd_id"] > latest_per_chain[chain_id]["cmd_id"]:
                latest_per_chain[chain_id] = e

        processed = 0
        for chain_id, entry in latest_per_chain.items():
            expected_tp = entry["tp_price"]
            tp_level    = entry["tp_level"]
            symbol      = entry["symbol"]
            side        = entry["side"]

            if expected_tp <= 0:
                continue

            if self._tp_fill_event_exists(chain_id, tp_level):
                continue  # TP triggered normally — not a cancellation

            try:
                pos = self._adapter.fetch_position_details(
                    symbol=symbol,
                    side=side,
                    execution_account_id=self._execution_account_id,
                )
            except Exception:
                logger.exception("fetch_position_details error for chain %s", chain_id)
                continue

            if pos is None:
                continue  # position not found — can't determine state

            if pos.take_profit is None:
                continue  # exchange doesn't expose TP field for this adapter

            if pos.take_profit != 0.0:
                continue  # TP still active on exchange

            # TP is 0.0 on exchange, no TP_FILLED recorded → externally cancelled
            idempotency_key = f"PROTECTIVE_ORDERS_MISSING:{chain_id}:tp:{tp_level}"
            self._save_protective_orders_missing(
                chain_id=chain_id,
                idempotency_key=idempotency_key,
                payload={
                    "expected_tp": expected_tp,
                    "tp_level":    tp_level,
                    "reason":      "tp_removed_externally",
                },
            )
            logger.warning(
                "protective orders missing: chain=%s tp_level=%s expected_tp=%s — "
                "TP was removed externally without a fill",
                chain_id, tp_level, expected_tp,
            )
            processed += 1

        return processed

    def _save_protective_orders_missing(
        self,
        chain_id: int,
        idempotency_key: str,
        payload: dict,
    ) -> None:
        now = _now()
        conn = sqlite3.connect(self._ops_db)
        try:
            conn.execute(
                "INSERT OR IGNORE INTO ops_exchange_events "
                "(trade_chain_id, event_type, payload_json, processing_status, "
                "idempotency_key, received_at) VALUES (?,?,?,?,?,?)",
                (chain_id, "PROTECTIVE_ORDERS_MISSING",
                 json.dumps(payload), "NEW", idempotency_key, now),
            )
            conn.commit()
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
            "command_id":            coid.command_id,
            "position_already_open": position_already_open,
            "cancel_reason":         raw.cancel_reason,
            "cancelled_order_ids":   [client_order_id],
            "sequence":              coid.sequence,
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
