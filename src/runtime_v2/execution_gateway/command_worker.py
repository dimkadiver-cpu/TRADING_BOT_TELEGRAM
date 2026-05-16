# src/runtime_v2/execution_gateway/command_worker.py
from __future__ import annotations

import logging
import sqlite3

from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

logger = logging.getLogger(__name__)


class ExecutionCommandWorker:
    def __init__(
        self,
        ops_db_path: str,
        gateway: ExecutionGateway,
        repo: GatewayCommandRepository,
        batch_size: int = 100,
    ) -> None:
        self._ops_db = ops_db_path
        self._gw = gateway
        self._repo = repo
        self._batch_size = batch_size

    def run_once(self) -> int:
        processed = 0

        # Query 1: PENDING
        for cmd in self._repo.get_pending_batch(self._batch_size):
            account_id = self._get_account_id(cmd.trade_chain_id)
            if account_id is None:
                logger.warning("no account_id for chain %s", cmd.trade_chain_id)
                continue
            try:
                self._gw.process(cmd, account_id=account_id)
                processed += 1
            except Exception:
                logger.exception("gateway error for command %s", cmd.command_id)

        # Query 2: retry (SENT with expired next_retry_at)
        for cmd in self._repo.get_retry_batch(self._batch_size):
            account_id = self._get_account_id(cmd.trade_chain_id)
            if account_id is None:
                continue
            try:
                self._gw.process(cmd, account_id=account_id)
                processed += 1
            except Exception:
                logger.exception("gateway retry error for command %s", cmd.command_id)

        # Query 3: WAITING_POSITION on OPEN chains → reset to PENDING then process
        waiting = self._repo.get_waiting_on_open_chains(self._batch_size)
        for cmd in waiting:
            self._repo.reset_waiting_to_pending(cmd.command_id)
            account_id = self._get_account_id(cmd.trade_chain_id)
            if account_id is None:
                continue
            try:
                fresh = self._repo.get_by_id(cmd.command_id)
                if fresh is not None:
                    self._gw.process(fresh, account_id=account_id)
                    processed += 1
            except Exception:
                logger.exception("gateway waiting error for command %s", cmd.command_id)

        return processed

    def _get_account_id(self, trade_chain_id: int) -> str | None:
        conn = sqlite3.connect(self._ops_db)
        try:
            row = conn.execute(
                "SELECT account_id FROM ops_trade_chains WHERE trade_chain_id=?",
                (trade_chain_id,),
            ).fetchone()
            return row[0] if row else None
        finally:
            conn.close()


__all__ = ["ExecutionCommandWorker"]
