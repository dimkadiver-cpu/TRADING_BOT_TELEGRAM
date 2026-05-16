# src/runtime_v2/execution_gateway/adapters/base.py
from __future__ import annotations

from abc import ABC, abstractmethod

from src.runtime_v2.execution_gateway.models import AdapterCapabilities, AdapterResult, RawAdapterOrder


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


__all__ = ["ExecutionAdapter"]
