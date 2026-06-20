from __future__ import annotations

from dataclasses import dataclass

from src.runtime_v2.control_plane.models import ControlPlaneConfig


@dataclass(frozen=True)
class QueryScope:
    account_id: str | None        # None = tutti gli account (scope globale)
    trader_ids: list[str] | None  # None = tutti i trader dello scope


class ScopeResolver:
    """Reverse lookup: thread_id → QueryScope.

    Built once at boot from ControlPlaneConfig. Commands threads always
    resolve to global scope (account_id=None). Clean-log per-trader threads
    resolve to single-trader scope. Clean-log fallback threads resolve to
    full-account scope.
    """

    def __init__(self, config: ControlPlaneConfig) -> None:
        self._default_account = config.default_account
        # Map thread_id → QueryScope, populated from all accounts
        self._map: dict[int, QueryScope] = {}

        for account_id, acc in config.per_account.items():
            topics = acc.topics

            # commands thread → scope globale (tutti gli account)
            if topics.commands.thread_id is not None:
                self._map[topics.commands.thread_id] = QueryScope(
                    account_id=None, trader_ids=None
                )

            # clean_log fallback → account singolo, tutti i trader
            if topics.clean_log.thread_id is not None:
                self._map[topics.clean_log.thread_id] = QueryScope(
                    account_id=account_id, trader_ids=None
                )

            # clean_log per-trader → trader singolo
            for trader_id, tid in topics.clean_log.per_trader.items():
                if tid is not None:
                    self._map[tid] = QueryScope(
                        account_id=account_id, trader_ids=[trader_id]
                    )

            # tech_log è intenzionalmente omesso — non è mai uno scope comandi

    def resolve(self, thread_id: int | None) -> QueryScope:
        """Return scope for thread_id, falling back to default_account if unknown."""
        if thread_id is not None and thread_id in self._map:
            return self._map[thread_id]
        return QueryScope(account_id=self._default_account, trader_ids=None)


__all__ = ["QueryScope", "ScopeResolver"]
