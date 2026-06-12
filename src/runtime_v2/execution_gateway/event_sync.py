from __future__ import annotations

import json
import logging
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone

from src.runtime_v2.execution_gateway import client_order_id as coid_mod
from src.runtime_v2.execution_gateway.adapters.base import ExecutionAdapter
from src.runtime_v2.execution_gateway.event_ingest.payload import ExchangeEventPayload
from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

logger = logging.getLogger(__name__)


def _weighted_avg_price(trades: list) -> float | None:
    """Weighted average price across a list of RawAdapterTrade objects."""
    total_qty = sum(t.amount for t in trades)
    if total_qty <= 0:
        return None
    return sum(t.price * t.amount for t in trades) / total_qty


def _reduce_trade_stats(trades: list) -> tuple[float | None, float | None, float | None]:
    """Aggregate recent reduce trades into fill price, total fee, and effective fee rate."""
    if not trades:
        return None, None, None
    fill_price = _weighted_avg_price(trades)
    total_notional = sum(float(t.price) * float(t.amount) for t in trades)
    fees = [float(t.fee) for t in trades if t.fee is not None]
    total_fee = sum(fees) if fees else None
    fee_rate = None
    if total_fee is not None and total_notional > 0.0:
        fee_rate = total_fee / total_notional
    return fill_price, total_fee, fee_rate


class ExchangeEventSyncWorker:
    def __init__(
        self,
        ops_db_path: str,
        adapter: ExecutionAdapter,
        repo: GatewayCommandRepository,
        execution_account_id: str,
        wake_callback=None,
    ) -> None:
        self._ops_db = ops_db_path
        self._adapter = adapter
        self._repo = repo
        self._execution_account_id = execution_account_id
        self._wake_callback = wake_callback

    def _wake(self) -> None:
        if self._wake_callback is not None:
            self._wake_callback()

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
                    self._save_fill_event(client_order_id, raw)
                    self._repo.mark_done(cmd.command_id)
                    self._wake()
                    processed += 1
                elif raw and raw.status == "CANCELLED":
                    self._save_cancelled_event(client_order_id, raw)
                    self._repo.mark_done(cmd.command_id)
                    self._wake()
                    processed += 1
            except Exception:
                logger.exception("reconciliation error for %s", client_order_id)

        return processed

    def run_position_reconciliation(self) -> int:
        """Detect positions closed externally on the exchange (manual close or missed TP/SL)."""
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
                    # Skip synthetic close if a real fill event already exists.
                    # The lifecycle will close the chain from the WS/REST fill path.
                    if self._repo.real_close_fill_exists(chain_id):
                        continue

                    # Attempt to recover fill price from recent reduce trades (REST safety net)
                    fill_price: float | None = None
                    exec_fee: float | None = None
                    fee_rate: float | None = None
                    if hasattr(self._adapter, "fetch_recent_reduce_trades"):
                        try:
                            trades = self._adapter.fetch_recent_reduce_trades(
                                symbol=symbol,
                                side=side,
                                execution_account_id=self._execution_account_id,
                                limit=50,
                            )
                            fill_price, exec_fee, fee_rate = _reduce_trade_stats(trades)
                        except Exception:
                            logger.warning(
                                "could not fetch fill price for reconciliation close: chain=%s",
                                chain_id,
                            )

                    idem_key = f"CLOSE_FULL_FILLED:ext:{chain_id}"
                    payload = json.dumps({
                        "filled_qty": open_qty,
                        "fill_price": fill_price,
                        "exec_fee": exec_fee,
                        "fee_rate": fee_rate,
                        "source": "position_reconciliation",
                    })
                    inserted = self._repo.insert_exchange_event(
                        chain_id, "CLOSE_FULL_FILLED", payload, idem_key
                    )
                    if inserted:
                        logger.info(
                            "externally closed position detected: chain=%s %s %s qty=%s fill_price=%s",
                            chain_id, symbol, side, open_qty, fill_price,
                        )
                        self._wake()
                        processed += 1
            except Exception:
                logger.exception("position reconciliation error for chain %s", chain_id)
        return processed

    def run_trade_based_reconciliation(self) -> int:
        """Poll recent fills via REST — safety net for TP fills lost during WS downtime.

        Uses symbol+side correlation (no price matching). For each chain with an active TP:
        - If a recent reduce trade exists for the chain's symbol+side → INSERT TP_FILLED
        - Multiple chains for same symbol+side → attribute to most recent (highest id)
        """
        if not hasattr(self._adapter, "fetch_recent_reduce_trades"):
            return 0

        open_chains = self._repo.get_open_chains_with_tps(self._execution_account_id)
        if not open_chains:
            return 0

        processed = 0
        by_symbol_side: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for chain in open_chains:
            key = (chain["symbol"], chain["side"])
            by_symbol_side[key].append(chain)

        for (symbol, side), chains in by_symbol_side.items():
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
            if not trades:
                continue

            if len(chains) == 1:
                chain_id = chains[0]["trade_chain_id"]
            else:
                chain_id = max(c["trade_chain_id"] for c in chains)
                logger.warning(
                    "multiple open chains for %s %s — attributing to most recent chain %s",
                    symbol, side, chain_id,
                )

            # tp_level=None: position-level TPs on Bybit have no standalone orderLinkId
            tp_level: int | None = None
            if self._repo.tp_fill_exists(chain_id):
                continue  # already recorded by WS or previous REST run

            trade = trades[0]
            # Use exchange trade_id as identity key — matches execId from WS watch_my_trades
            idem_key = f"fill:{trade.trade_id}"
            payload = json.dumps({
                "tp_level": tp_level,
                "fill_price": trade.price,
                "filled_qty": trade.amount,
                "source": "trade_based_reconciliation",
                "exchange_trade_id": trade.trade_id,
            })
            inserted = self._repo.insert_exchange_event(chain_id, "TP_FILLED", payload, idem_key)
            if inserted:
                logger.info(
                    "TP_FILLED from trade-based reconciliation: chain=%s %s %s",
                    chain_id, symbol, side,
                )
                self._wake()
                processed += 1

        return processed

    def run_funding_reconciliation(self) -> int:
        """Poll recent funding executions via REST — safety net for funding settled
        during WS downtime.

        Uses the same idempotency key format as the WS path (fill:{execId}), so
        executions already recorded by watch_my_trades dedupe automatically.
        Chain attribution mirrors the WS path: Bybit funding `side` is the position
        side (Buy = LONG, Sell = SHORT), resolved via symbol+side; ambiguous
        attributions (0 or >1 open chains) are skipped with a warning.
        """
        if not hasattr(self._adapter, "fetch_recent_funding_executions"):
            return 0

        chains = self._get_open_chains()
        if not chains:
            return 0

        symbols = sorted({symbol for _, symbol, _, _ in chains})
        processed = 0
        for symbol in symbols:
            try:
                executions = self._adapter.fetch_recent_funding_executions(
                    symbol=symbol,
                    execution_account_id=self._execution_account_id,
                    limit=50,
                )
            except Exception:
                logger.exception("fetch_recent_funding_executions error for %s", symbol)
                continue

            for fe in executions:
                if not fe.exec_fee:
                    continue
                funding_side = (fe.side or "").strip()
                position_side = "LONG" if funding_side.lower() in ("buy", "long") else "SHORT"
                chain_id = self._repo.resolve_chain_for_fill(
                    fe.symbol, position_side, self._execution_account_id
                )
                if chain_id is None:
                    logger.warning(
                        "funding execution %s (%s %s account=%s, exec_fee=%s) not attributable "
                        "via REST: 0 or >1 open chains for symbol+side",
                        fe.exec_id, fe.symbol, position_side,
                        self._execution_account_id, fe.exec_fee,
                    )
                    continue

                idem_key = f"fill:{fe.exec_id}"  # same key the WS path generates
                payload = json.dumps({
                    "exec_fee": fe.exec_fee,
                    "exchange_time": fe.exchange_time,
                    "exchange_event_id": fe.exec_id,
                    "source": "funding_reconciliation",
                })
                inserted = self._repo.insert_exchange_event(
                    chain_id, "FUNDING_SETTLED", payload, idem_key
                )
                if inserted:
                    logger.info(
                        "FUNDING_SETTLED from funding reconciliation: chain=%s %s exec_fee=%s",
                        chain_id, fe.symbol, fe.exec_fee,
                    )
                    self._wake()
                    processed += 1

        return processed

    def run_protective_orders_reconciliation(self) -> int:
        """Detect when a position-level TP was externally cancelled (no fill occurred)."""
        if not hasattr(self._adapter, "fetch_position_details"):
            return 0

        open_chains = self._repo.get_open_chains_with_tps(self._execution_account_id)
        if not open_chains:
            return 0

        processed = 0
        for chain in open_chains:
            chain_id = chain["trade_chain_id"]
            symbol = chain["symbol"]
            side = chain["side"]

            if self._repo.protective_cancelled_exists(chain_id):
                continue
            if self._repo.tp_fill_exists(chain_id, None):
                continue  # TP was filled, not cancelled

            try:
                pos = self._adapter.fetch_position_details(
                    symbol=symbol,
                    side=side,
                    execution_account_id=self._execution_account_id,
                )
            except Exception:
                logger.exception("fetch_position_details error for chain %s", chain_id)
                continue

            if pos is None or pos.take_profit is None or pos.take_profit != 0.0:
                continue

            idem_key = f"PROTECTIVE_ORDER_CANCELLED:{chain_id}"
            payload = json.dumps({
                "reason": "tp_removed_externally",
                "source": "protective_orders_reconciliation",
            })
            inserted = self._repo.insert_exchange_event(
                chain_id, "PROTECTIVE_ORDER_CANCELLED", payload, idem_key
            )
            if inserted:
                logger.warning(
                    "PROTECTIVE_ORDER_CANCELLED detected: chain=%s %s %s",
                    chain_id, symbol, side,
                )
                self._wake()
                processed += 1

        return processed

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _save_fill_event(self, client_order_id: str, raw) -> bool:
        """Build fill event and delegate to repo.insert_exchange_event() — no inline SQLite."""
        try:
            coid = coid_mod.parse(client_order_id)
        except ValueError:
            logger.warning("cannot parse client_order_id: %s", client_order_id)
            return False

        exchange_order_id = raw.exchange_order_id or client_order_id

        role_to_event: dict[str, str] = {
            "entry": "ENTRY_FILLED",
            "sl": "SL_FILLED",
            "exit_partial": "CLOSE_PARTIAL_FILLED",
            "exit_full": "CLOSE_FULL_FILLED",
            "tp": "TP_FILLED",
        }
        event_type = role_to_event.get(coid.role)
        if event_type is None:
            logger.warning(
                "unknown role '%s' in %s — skipping", coid.role, client_order_id
            )
            return False

        _CLOSE_FILL_TYPES = {"TP_FILLED", "SL_FILLED", "CLOSE_PARTIAL_FILLED", "CLOSE_FULL_FILLED"}
        if coid.role == "tp":
            tp_level: int | None = coid.sequence
            idem_key = f"TP_FILLED:{coid.trade_chain_id}:level:{coid.sequence}"
        else:
            tp_level = None
            idem_key = f"{event_type}:{coid.trade_chain_id}:{exchange_order_id}"

        # Il WS può aver già registrato questo fill con chiave fill:{execId}:
        # le chiavi WS/REST divergono, quindi il dedup va fatto sull'ordine.
        if self._repo.has_exchange_event_for_order(coid.trade_chain_id, event_type, exchange_order_id):
            logger.info(
                "fill already recorded (likely via WS) for order %s — skipping duplicate",
                exchange_order_id,
            )
            return True

        if coid.command_id:
            command_source = self._repo.get_command_source(coid.trade_chain_id, coid.command_id)
            fill_source = command_source or "manual_command"
        else:
            fill_source = "rest_reconciliation"

        ep = ExchangeEventPayload(
            fill_price=raw.average_price,
            filled_qty=raw.filled_qty,
            closed_size=raw.filled_qty if event_type in _CLOSE_FILL_TYPES else None,
            exec_fee=raw.exec_fee,
            exec_value=raw.exec_value,
            exchange_time=raw.exchange_time,
            leaves_qty=raw.leaves_qty,
            cum_exec_qty=raw.cum_exec_qty,
            order_id=raw.exchange_order_id,
            order_link_id=raw.client_order_id,
            tp_level=tp_level,
            command_id=coid.command_id,
            source=fill_source,
        )

        return self._repo.insert_exchange_event(
            coid.trade_chain_id, event_type, ep.model_dump_json(), idem_key
        )

    def _save_cancelled_event(self, client_order_id: str, raw) -> bool:
        """Handle cancelled orders — delegates to repo.insert_exchange_event()."""
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
            return self._save_fill_event(client_order_id, raw)

        if coid.role != "entry":
            logger.warning(
                "cancelled non-entry order detected: %s - marking done", client_order_id
            )
            return True

        exchange_order_id = raw.exchange_order_id or client_order_id
        if raw.cancel_reason:
            logger.warning(
                "entry order cancelled by exchange: coid=%s reason=%s",
                client_order_id, raw.cancel_reason,
            )
        idem_key = f"PENDING_ENTRY_CANCELLED_CONFIRMED:{coid.trade_chain_id}:{exchange_order_id}"
        cancel_meta = self._repo.get_cancel_trigger_metadata(
            coid.trade_chain_id,
            client_order_id,
        )
        payload_dict = {
            "command_id": coid.command_id,
            "position_already_open": raw.filled_qty > 0.0,
            "cancel_reason": cancel_meta.get("cancel_reason", raw.cancel_reason),
            "cancelled_order_ids": [client_order_id],
            "sequence": coid.sequence,
            "cancel_origin": cancel_meta.get("cancel_origin"),
        }
        if cancel_meta.get("cancel_command_id") is not None:
            payload_dict["cancel_command_id"] = cancel_meta["cancel_command_id"]
        payload = json.dumps(payload_dict)
        return self._repo.insert_exchange_event(
            coid.trade_chain_id, "PENDING_ENTRY_CANCELLED_CONFIRMED", payload, idem_key
        )

    def _get_open_chains(self) -> list[tuple[int, str, str, float]]:
        """Returns (chain_id, symbol, side, open_qty) for OPEN/PARTIALLY_CLOSED chains
        belonging to this worker's account.

        The account filter is essential in per_trader_subaccount mode: this worker
        polls its own adapter, and checking another account's chains against it
        would report qty=0 and synthesize a spurious close.
        """
        conn = sqlite3.connect(self._ops_db)
        try:
            rows = conn.execute(
                "SELECT trade_chain_id, symbol, side, open_position_qty "
                "FROM ops_trade_chains "
                "WHERE lifecycle_state IN ('OPEN', 'PARTIALLY_CLOSED') "
                "AND account_id=?",
                (self._execution_account_id,),
            ).fetchall()
            return [(int(r[0]), str(r[1]), str(r[2]), float(r[3] or 0.0)) for r in rows]
        finally:
            conn.close()


__all__ = ["ExchangeEventSyncWorker"]
