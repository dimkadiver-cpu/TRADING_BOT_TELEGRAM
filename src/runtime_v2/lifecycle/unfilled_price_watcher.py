# src/runtime_v2/lifecycle/unfilled_price_watcher.py
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_DEFAULT_INTERVAL = 60


def resolve_tp_threshold(plan: dict, tp_level: str) -> float | None:
    """Return the price threshold for the given tp_level from a plan dict.

    tp_level: "tp1" or "tp2".
    Returns None when no price is resolvable (no tps in plan).
    """
    intermediate: list = plan.get("intermediate_tps") or []
    final_tp = plan.get("final_tp")

    if tp_level == "tp1":
        return float(intermediate[0]) if intermediate else (float(final_tp) if final_tp is not None else None)
    if tp_level == "tp2":
        if len(intermediate) >= 2:
            return float(intermediate[1])
        return float(final_tp) if final_tp is not None else None
    return None


def _is_triggered(mark_price: float, threshold: float, side: str) -> bool:
    if side == "LONG":
        return mark_price >= threshold
    return mark_price <= threshold   # SHORT


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class UnfilledPriceWatcher:
    """Periodic worker: cancel chains whose price has crossed TP without any fill."""

    def __init__(
        self,
        *,
        ops_db_path: str,
        chain_repo,
        adapter,
        execution_account_id: str,
        interval_seconds: int = _DEFAULT_INTERVAL,
    ) -> None:
        self._ops_db = ops_db_path
        self._chain_repo = chain_repo
        self._adapter = adapter
        self._execution_account_id = execution_account_id
        self._interval = interval_seconds

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self.run_once)
        while True:
            await asyncio.sleep(self._interval)
            await loop.run_in_executor(None, self.run_once)

    def run_once(self) -> int:
        chains = self._chain_repo.get_waiting_entry_with_unfilled_cancel_config()
        if not chains:
            return 0

        # Group by symbol — 1 fetch_mark_price per symbol
        symbol_to_price: dict[str, float | None] = {}
        for chain in chains:
            if chain.symbol not in symbol_to_price:
                try:
                    price = self._adapter.fetch_mark_price(
                        chain.symbol, self._execution_account_id
                    )
                    symbol_to_price[chain.symbol] = price
                except Exception as exc:
                    logger.warning("fetch_mark_price failed for %s: %s", chain.symbol, exc)
                    symbol_to_price[chain.symbol] = None

        cancelled = 0
        for chain in chains:
            try:
                if self._process_chain(chain, symbol_to_price.get(chain.symbol)):
                    cancelled += 1
            except Exception:
                logger.exception("unfilled_price_watcher error for chain %s", chain.trade_chain_id)
        return cancelled

    def _process_chain(self, chain, mark_price: float | None) -> bool:
        chain_id = chain.trade_chain_id

        try:
            mp = json.loads(chain.management_plan_json or "{}")
        except Exception:
            return False

        if not mp.get("cancel_pending_by_engine", True):
            return False

        tp_level: str | None = mp.get("cancel_unfilled_pending_after")
        if not tp_level:
            return False

        if mark_price is None:
            logger.debug("no mark price for chain %s symbol %s — skip", chain_id, chain.symbol)
            return False

        try:
            plan = json.loads(chain.plan_state_json or "{}")
        except Exception:
            return False

        threshold = resolve_tp_threshold(plan, tp_level)
        if threshold is None:
            logger.warning("chain %s has no resolvable TP threshold — skip", chain_id)
            return False

        if not _is_triggered(mark_price, threshold, chain.side):
            return False

        self._emit_cancel(chain_id, chain, tp_level, threshold, mark_price)
        return True

    def _emit_cancel(
        self,
        chain_id: int,
        chain,
        tp_level: str,
        threshold: float,
        mark_price: float,
    ) -> None:
        now = _now()
        idem_event = f"unfilled_tp_cancel:{chain_id}"
        payload = json.dumps({
            "tp_level": tp_level,
            "threshold_price": threshold,
            "mark_price": mark_price,
            "cancel_reason": "unfilled_tp_reached",
            "symbol": chain.symbol,
            "side": chain.side,
        })

        conn = sqlite3.connect(self._ops_db)
        try:
            with conn:
                conn.execute(
                    "UPDATE ops_trade_chains SET lifecycle_state='EXPIRED', updated_at=? "
                    "WHERE trade_chain_id=?",
                    (now, chain_id),
                )
                conn.execute(
                    """
                    INSERT OR IGNORE INTO ops_lifecycle_events (
                        trade_chain_id, event_type, source_type,
                        previous_state, next_state, payload_json, idempotency_key, created_at
                    ) VALUES (?,?,?,?,?,?,?,?)
                    """,
                    (chain_id, "UNFILLED_TP_CANCEL", "unfilled_price_watcher",
                     "WAITING_ENTRY", "EXPIRED", payload, idem_event, now),
                )
                # CANCEL_PENDING_ENTRY — fan-out to real orders (same as timeout worker)
                from src.runtime_v2.lifecycle.cancel_expander import load_pending_entry_client_order_ids
                entry_coids = load_pending_entry_client_order_ids(conn, chain_id)
                if not entry_coids:
                    entry_coids = [""]
                for coid in entry_coids:
                    cmd_payload = {
                        "symbol": chain.symbol,
                        "side": chain.side,
                        "cancel_origin": "unfilled_price_watcher",
                        "cancel_reason": "unfilled_tp_reached",
                    }
                    idem_cmd = f"cancel_unfilled_tp:{chain_id}"
                    if coid:
                        cmd_payload["entry_client_order_id"] = coid
                        idem_cmd = f"{idem_cmd}:{coid}"
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO ops_execution_commands (
                            trade_chain_id, command_type, status, payload_json,
                            idempotency_key, created_at, updated_at
                        ) VALUES (?,?,?,?,?,?,?)
                        """,
                        (chain_id, "CANCEL_PENDING_ENTRY", "PENDING",
                         json.dumps(cmd_payload), idem_cmd, now, now),
                    )
        finally:
            conn.close()

        logger.info(
            "unfilled_tp_cancel: chain=%s symbol=%s side=%s tp_level=%s "
            "threshold=%.4f mark=%.4f",
            chain_id, chain.symbol, chain.side, tp_level, threshold, mark_price,
        )


__all__ = ["UnfilledPriceWatcher", "resolve_tp_threshold"]
