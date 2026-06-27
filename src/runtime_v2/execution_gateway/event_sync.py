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


def _pick_trades_for_qty(trades: list, target_qty: float) -> list:
    """Return the most-recent prefix of trades whose cumulative qty reaches target_qty.

    Trades are assumed to be sorted descending by time (most recent first).
    Stops accumulating once we have enough qty to explain the target close size,
    so that unrelated historical trades don't distort the weighted-average price.
    """
    if not trades or target_qty <= _PARTIAL_CLOSE_MIN_DELTA:
        return trades
    picked: list = []
    cumulative = 0.0
    for t in trades:
        picked.append(t)
        cumulative += float(t.amount)
        if cumulative >= target_qty - _PARTIAL_CLOSE_MIN_DELTA:
            break
    return picked


_POSITION_ZERO_CONFIRM_REQUIRED = 2  # consecutive REST zeros before synthetic close
_PARTIAL_CLOSE_MIN_DELTA = 1e-8     # minimum qty difference to trigger partial-close detection


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
        # chain_id → consecutive REST reads returning qty=0 without a confirmed reduce trade
        self._position_zero_count: dict[int, int] = {}

    def _wake(self) -> None:
        if self._wake_callback is not None:
            self._wake_callback()

    def run_once(self) -> int:
        return self.run_reconciliation()

    def bootstrap_zero_counts(self) -> None:
        """Seed _position_zero_count so one run_bulk_position_sync call at startup is enough.

        The consecutive-zero confirmation counter starts at zero on every boot. Calling this
        once before the startup bulk sync pre-seeds the counter to REQUIRED-1, so the first
        (and only) startup sync call crosses the threshold instead of requiring two calls.
        """
        for chain_id, _, _, _ in self._get_open_chains():
            self._position_zero_count.setdefault(chain_id, _POSITION_ZERO_CONFIRM_REQUIRED - 1)

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

    def run_bulk_position_sync(self) -> int:
        """Sync live positions in bulk and detect externally closed chains."""
        positions = self._adapter.fetch_all_positions(self._execution_account_id)
        if positions is None:
            return 0

        captured_at = datetime.now(timezone.utc).isoformat()
        live_by_symbol_side: dict[tuple[str, str], object] = {}
        for pos in positions:
            self._repo.upsert_position_snapshot(
                account_id=self._execution_account_id,
                symbol=pos.symbol,
                side=pos.side,
                qty=pos.qty,
                mark_price=pos.mark_price,
                unrealized_pnl=pos.unrealized_pnl,
                cum_realized_pnl=pos.cum_realized_pnl,
                source="bulk_position_sync",
                captured_at=captured_at,
            )
            live_by_symbol_side[(pos.symbol, pos.side)] = pos

        chains = self._get_open_chains()
        processed = 0

        # Batch-fetch reduce trades for partial-close candidates (one REST call per
        # unique symbol+side instead of one per chain) — Fix #6.
        reduce_trades_cache = self._prefetch_reduce_trades_for_partial_closes(
            chains, live_by_symbol_side
        )

        for chain_id, symbol, side, open_qty in chains:
            try:
                live_pos = live_by_symbol_side.get((symbol, side))
                qty = 0.0 if live_pos is None else float(live_pos.qty)
                if qty > 0.0:
                    self._position_zero_count.pop(chain_id, None)
                    # Detect partial close that happened during bot downtime:
                    # live qty is smaller than DB-tracked qty but position still open.
                    if open_qty > 0.0 and (open_qty - qty) > _PARTIAL_CLOSE_MIN_DELTA:
                        cached = reduce_trades_cache.get((symbol, side))
                        if self._generate_synthetic_partial_close(
                            chain_id=chain_id,
                            symbol=symbol,
                            side=side,
                            open_qty=open_qty,
                            live_qty=qty,
                            cached_trades=cached,
                        ):
                            processed += 1
                    continue
                if open_qty <= 0.0:
                    self._position_zero_count.pop(chain_id, None)
                    continue
                if qty == 0.0:
                    # Skip synthetic close if a real fill event already exists.
                    # The lifecycle will close the chain from the WS/REST fill path.
                    if self._repo.real_close_fill_exists(chain_id):
                        self._position_zero_count.pop(chain_id, None)
                        continue

                    fill_price, exec_fee, fee_rate = self._resolve_reduce_trade_stats(
                        symbol=symbol, side=side, target_qty=open_qty
                    )

                    # If no reduce trade confirms the close, require consecutive zero reads
                    # to guard against transient REST API returning empty positions (false zero).
                    if fill_price is None:
                        count = self._position_zero_count.get(chain_id, 0) + 1
                        self._position_zero_count[chain_id] = count
                        if count < _POSITION_ZERO_CONFIRM_REQUIRED:
                            logger.warning(
                                "position qty=0 from REST but no reduce trade found: "
                                "chain=%s %s %s (zero_count=%d/%d) — deferring synthetic close",
                                chain_id, symbol, side, count, _POSITION_ZERO_CONFIRM_REQUIRED,
                            )
                            continue
                        logger.warning(
                            "position qty=0 confirmed %d consecutive times without reduce trade: "
                            "chain=%s %s %s — generating synthetic close",
                            count, chain_id, symbol, side,
                        )

                    self._position_zero_count.pop(chain_id, None)
                    idem_key = f"CLOSE_FULL_FILLED:ext:{chain_id}"
                    # filled_qty=None: let the lifecycle use chain.open_position_qty at
                    # processing time so that a preceding CLOSE_PARTIAL_FILLED event (which
                    # reduces open_position_qty) prevents double-counting — Fix #4.
                    payload = json.dumps({
                        "filled_qty": None,
                        "fill_price": fill_price,
                        "exec_fee": exec_fee,
                        "fee_rate": fee_rate,
                        "source": "bulk_position_sync",
                    })
                    inserted = self._repo.insert_exchange_event(
                        chain_id, "CLOSE_FULL_FILLED", payload, idem_key
                    )
                    if inserted:
                        logger.info(
                            "externally closed position detected from bulk sync: "
                            "chain=%s %s %s qty=%s fill_price=%s",
                            chain_id, symbol, side, open_qty, fill_price,
                        )
                        self._wake()
                        processed += 1
            except Exception:
                logger.exception("bulk position sync error for chain %s", chain_id)
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
        """Poll recent funding executions via REST — safety net for FUNDING_SETTLED events
        lost during WS downtime.

        For each open chain's symbol, fetches recent funding executions from the adapter and
        inserts a FUNDING_SETTLED event using the exchange exec_id as idempotency key
        (``fill:{exec_id}``), so WS and REST paths deduplicate automatically.
        Skips zero-fee executions and ambiguous attributions (multiple open chains on the
        same symbol+side for this account).
        """
        if not hasattr(self._adapter, "fetch_recent_funding_executions"):
            return 0

        open_chains = self._get_open_chains()
        if not open_chains:
            return 0

        symbols: set[str] = {symbol for _, symbol, _, _ in open_chains}
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
            if not executions:
                continue

            for exec in executions:
                if exec.exec_fee == 0.0:
                    continue

                bybit_side = (exec.side or "").lower()
                if bybit_side == "buy":
                    position_side = "LONG"
                elif bybit_side == "sell":
                    position_side = "SHORT"
                else:
                    logger.warning(
                        "funding reconciliation: unrecognised side '%s' for %s exec %s — skipping",
                        exec.side, symbol, exec.exec_id,
                    )
                    continue

                open_chains = self._repo.get_open_chains_for_symbol(
                    symbol, position_side, account_id=self._execution_account_id
                )
                if len(open_chains) == 0:
                    logger.debug(
                        "funding reconciliation: no open chain for %s %s "
                        "— skipping exec %s (likely historical from closed position)",
                        symbol, position_side, exec.exec_id,
                    )
                    continue
                if len(open_chains) > 1:
                    logger.warning(
                        "funding reconciliation: ambiguous chain for %s %s "
                        "(%d open chains) — skipping exec %s",
                        symbol, position_side, len(open_chains), exec.exec_id,
                    )
                    continue
                chain_id = open_chains[0]

                idem_key = f"fill:{exec.exec_id}"
                payload = json.dumps({
                    "exec_fee": exec.exec_fee,
                    "exchange_event_id": exec.exec_id,
                    "exchange_time": exec.exchange_time,
                    "source": "funding_reconciliation",
                })
                inserted = self._repo.insert_exchange_event(
                    chain_id, "FUNDING_SETTLED", payload, idem_key
                )
                if inserted:
                    logger.info(
                        "FUNDING_SETTLED from funding reconciliation: chain=%s %s exec_id=%s fee=%s",
                        chain_id, symbol, exec.exec_id, exec.exec_fee,
                    )
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

    def _resolve_reduce_trade_stats(
        self,
        symbol: str,
        side: str,
        target_qty: float,
        cached_trades: list | None = None,
    ) -> tuple[float | None, float | None, float | None]:
        """Fetch and aggregate reduce-trade stats for a position close.

        Uses cached_trades when provided; otherwise fetches from the adapter.
        Filters to the most-recent trades that cumulatively explain target_qty,
        preventing unrelated historical closes from distorting the fill price.
        """
        if cached_trades is None:
            if not hasattr(self._adapter, "fetch_recent_reduce_trades"):
                return None, None, None
            try:
                cached_trades = self._adapter.fetch_recent_reduce_trades(
                    symbol=symbol,
                    side=side,
                    execution_account_id=self._execution_account_id,
                    limit=50,
                )
            except Exception:
                logger.warning(
                    "could not fetch reduce trades for %s %s", symbol, side
                )
                return None, None, None
        if not cached_trades:
            return None, None, None
        return _reduce_trade_stats(_pick_trades_for_qty(cached_trades, target_qty))

    def _prefetch_reduce_trades_for_partial_closes(
        self,
        chains: list[tuple[int, str, str, float]],
        live_by_symbol_side: dict,
    ) -> dict[tuple[str, str], list]:
        """Batch-fetch reduce trades for all partial-close candidates.

        Returns a dict keyed by (symbol, side) so each chain can look up
        its trades without making a separate REST call per chain.
        """
        if not hasattr(self._adapter, "fetch_recent_reduce_trades"):
            return {}

        needed: set[tuple[str, str]] = set()
        for chain_id, symbol, side, open_qty in chains:
            live_pos = live_by_symbol_side.get((symbol, side))
            qty = 0.0 if live_pos is None else float(live_pos.qty)  # type: ignore[union-attr]
            if qty > 0.0 and open_qty > 0.0 and (open_qty - qty) > _PARTIAL_CLOSE_MIN_DELTA:
                needed.add((symbol, side))

        cache: dict[tuple[str, str], list] = {}
        for symbol, side in needed:
            try:
                trades = self._adapter.fetch_recent_reduce_trades(
                    symbol=symbol,
                    side=side,
                    execution_account_id=self._execution_account_id,
                    limit=50,
                )
                cache[(symbol, side)] = trades or []
            except Exception:
                logger.warning(
                    "could not prefetch reduce trades for partial close: %s %s", symbol, side
                )
                cache[(symbol, side)] = []
        return cache

    def _generate_synthetic_partial_close(
        self,
        *,
        chain_id: int,
        symbol: str,
        side: str,
        open_qty: float,
        live_qty: float,
        cached_trades: list | None = None,
    ) -> bool:
        """Emit a synthetic CLOSE_PARTIAL_FILLED when the live position is smaller than DB qty.

        The idempotency key encodes the exact qty transition so re-runs are safe.
        After the lifecycle processes this event and updates open_position_qty, subsequent
        runs will see open_qty == live_qty and stop generating new events.
        cached_trades: pre-fetched reduce trades from the batch call in run_bulk_position_sync.
        """
        closed_qty = open_qty - live_qty
        idem_key = (
            f"CLOSE_PARTIAL_FILLED:ext:{chain_id}:"
            f"from:{open_qty:.8g}:to:{live_qty:.8g}"
        )

        fill_price, exec_fee, fee_rate = self._resolve_reduce_trade_stats(
            symbol=symbol,
            side=side,
            target_qty=closed_qty,
            cached_trades=cached_trades,
        )

        payload = json.dumps({
            "filled_qty": closed_qty,
            "closed_size": closed_qty,
            "fill_price": fill_price,
            "exec_fee": exec_fee,
            "fee_rate": fee_rate,
            "source": "bulk_position_sync_partial",
        })
        inserted = self._repo.insert_exchange_event(
            chain_id, "CLOSE_PARTIAL_FILLED", payload, idem_key
        )
        if inserted:
            logger.info(
                "partial close detected from bulk sync: "
                "chain=%s %s %s open_qty=%s live_qty=%s closed_qty=%s fill_price=%s",
                chain_id, symbol, side, open_qty, live_qty, closed_qty, fill_price,
            )
            self._wake()
        return inserted

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

        source_message_link: str | None = None
        if coid.command_id:
            command_source = self._repo.get_command_source(coid.trade_chain_id, coid.command_id)
            fill_source = command_source or "manual_command"
            source_message_link = self._repo.get_command_source_link(
                coid.trade_chain_id, coid.command_id
            )
        else:
            fill_source = "rest_reconciliation"

        ep_kwargs: dict = dict(
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
        if source_message_link is not None:
            ep_kwargs["source_message_link"] = source_message_link
        ep = ExchangeEventPayload(**ep_kwargs)

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
        """Returns (chain_id, symbol, side, open_qty) for OPEN/PARTIALLY_CLOSED chains belonging to this account."""
        conn = sqlite3.connect(self._ops_db)
        try:
            rows = conn.execute(
                "SELECT trade_chain_id, symbol, side, open_position_qty "
                "FROM ops_trade_chains "
                "WHERE lifecycle_state IN ('OPEN', 'PARTIALLY_CLOSED') "
                "AND account_id = ?",
                (self._execution_account_id,),
            ).fetchall()
            return [(int(r[0]), str(r[1]), str(r[2]), float(r[3] or 0.0)) for r in rows]
        finally:
            conn.close()


__all__ = ["ExchangeEventSyncWorker"]
