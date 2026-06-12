# src/runtime_v2/execution_gateway/adapters/base.py
from __future__ import annotations

from abc import ABC, abstractmethod

from src.runtime_v2.execution_gateway.models import (
    AdapterCapabilities,
    AdapterResult,
    RawAccountSnapshot,
    RawAdapterOrder,
    RawMarketSnapshot,
)


class ExecutionAdapter(ABC):
    @abstractmethod
    def get_capabilities(self) -> AdapterCapabilities: ...

    @abstractmethod
    def set_leverage(
        self, symbol: str, leverage: int, execution_account_id: str
    ) -> None: ...

    @abstractmethod
    def place_order(
        self,
        *,
        command_type: str,
        payload: dict,
        client_order_id: str,
        execution_account_id: str,
        connector: str,
    ) -> AdapterResult: ...

    @abstractmethod
    def cancel_order(
        self,
        *,
        client_order_id: str,
        execution_account_id: str,
        connector: str,
    ) -> AdapterResult: ...

    @abstractmethod
    def get_order_status(
        self,
        *,
        client_order_id: str,
        execution_account_id: str,
    ) -> RawAdapterOrder | None: ...

    @abstractmethod
    def get_position_qty(
        self,
        *,
        symbol: str,
        side: str,
        execution_account_id: str,
    ) -> float | None: ...

    @abstractmethod
    def fetch_mark_price(
        self,
        symbol: str,
        execution_account_id: str,
    ) -> float | None: ...

    def load_known_symbols(self) -> frozenset[str] | None:
        """Return the set of symbols tradeable on this exchange, or None if unavailable.

        Called once at startup to populate the symbol whitelist used by the entry gate.
        Returns None when the exchange cannot be reached or the adapter doesn't support it —
        the entry gate will then skip symbol validation (fail-open).
        """
        return None

    def fetch_max_order_qty(
        self,
        symbol: str,
        execution_account_id: str,
    ) -> float | None:
        """Return the max entry quantity accepted by the exchange for this symbol, or None."""
        return None

    def fetch_account_snapshot(
        self,
        execution_account_id: str,
    ) -> RawAccountSnapshot | None:
        """Return a normalized account snapshot for lifecycle risk/audit, or None if unavailable."""
        return None

    def fetch_market_snapshot(
        self,
        symbol: str,
        execution_account_id: str,
    ) -> RawMarketSnapshot | None:
        """Return a normalized market snapshot for lifecycle risk/audit, or None if unavailable."""
        return None


__all__ = ["ExecutionAdapter"]
