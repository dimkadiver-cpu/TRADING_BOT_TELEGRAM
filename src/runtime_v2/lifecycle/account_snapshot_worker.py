# src/runtime_v2/lifecycle/account_snapshot_worker.py
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_DEFAULT_INTERVAL = 60
_DEFAULT_STALE_AFTER = 180


class AccountSnapshotWorker:
    def __init__(
        self,
        *,
        port,
        repository,
        account_ids: list[str],
        interval_seconds: int = _DEFAULT_INTERVAL,
        stale_after_seconds: int = _DEFAULT_STALE_AFTER,
    ) -> None:
        self._port = port
        self._repository = repository
        self._account_ids = list(account_ids)
        self._interval = interval_seconds
        self._stale_after = stale_after_seconds
        self._pending_refresh: set[str] = set()
        self._in_flight: set[str] = set()

    async def run(self) -> None:
        await self._fetch_all()
        while True:
            await asyncio.sleep(self._interval)
            await self._fetch_all()

    def trigger(self, account_id: str) -> None:
        if account_id in self._in_flight:
            self._pending_refresh.add(account_id)
        else:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._fetch_one(account_id))
            except RuntimeError:
                pass  # nessun loop attivo — ignorare (bootstrap non ancora avviato)

    async def _fetch_all(self) -> None:
        for account_id in self._account_ids:
            await self._fetch_one(account_id)

    async def _fetch_one(self, account_id: str) -> None:
        if account_id in self._in_flight:
            self._pending_refresh.add(account_id)
            return
        self._in_flight.add(account_id)
        try:
            snap = await asyncio.get_running_loop().run_in_executor(
                None, self._port.get_account_state, account_id
            )
            self._repository.save_account(snap, account_id)
        except Exception as exc:
            logger.warning("AccountSnapshotWorker: failed for %s: %s", account_id, exc)
            from src.runtime_v2.lifecycle.ports import AccountStateSnapshot
            failed_snap = AccountStateSnapshot(
                account_id=account_id,
                captured_at=datetime.now(timezone.utc),
                source="unknown",
                snapshot_status="FAILED",
                error_code=type(exc).__name__,
            )
            try:
                self._repository.save_account(failed_snap, account_id)
            except Exception:
                pass
        finally:
            self._in_flight.discard(account_id)
            if account_id in self._pending_refresh:
                self._pending_refresh.discard(account_id)
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(self._fetch_one(account_id))
                except RuntimeError:
                    pass


__all__ = ["AccountSnapshotWorker"]
