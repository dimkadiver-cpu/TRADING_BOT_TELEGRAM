# src/runtime_v2/execution_gateway/adapters/fake.py
from __future__ import annotations

from src.runtime_v2.execution_gateway.adapters.base import ExecutionAdapter
from src.runtime_v2.execution_gateway.models import AdapterCapabilities, AdapterResult, RawAdapterOrder


class FakeAdapter(ExecutionAdapter):
    """Adapter deterministico per test. Simula ACK immediato e fill controllabile."""

    def __init__(
        self,
        *,
        capabilities: AdapterCapabilities | None = None,
        fail_on: set[str] | None = None,
        simulate_timeout: bool = False,
        positions: dict[str, float] | None = None,
    ) -> None:
        self._capabilities = capabilities or AdapterCapabilities(
            place_entry=True,
            protective_stop_native=True,
            take_profit_native=True,
            bracket_order=False,
            move_stop=True,
            close_partial=True,
            close_full=True,
            executor_position=False,
            sync_protective_orders=True,
        )
        self._fail_on = fail_on or set()
        self._simulate_timeout = simulate_timeout
        self._positions = positions or {}
        self._orders: dict[str, RawAdapterOrder] = {}
        self.calls: list[dict] = []

    def get_capabilities(self) -> AdapterCapabilities:
        return self._capabilities

    def set_leverage(self, symbol: str, leverage: int, execution_account_id: str) -> None:
        self.calls.append({"action": "set_leverage", "symbol": symbol, "leverage": leverage})

    def place_order(
        self,
        *,
        command_type: str,
        payload: dict,
        client_order_id: str,
        execution_account_id: str,
        connector: str,
    ) -> AdapterResult:
        self.calls.append({"action": "place_order", "command_type": command_type,
                           "client_order_id": client_order_id})
        if self._simulate_timeout:
            raise TimeoutError("fake timeout")
        if command_type in self._fail_on:
            return AdapterResult(success=False, error="fake_error",
                                 reason=f"command {command_type} set to fail")
        order = RawAdapterOrder(
            client_order_id=client_order_id,
            exchange_order_id=f"exch_{client_order_id}",
            adapter_order_id=f"hb_{client_order_id}",
            status="OPEN",
        )
        if command_type == "SYNC_PROTECTIVE_ORDERS":
            order = order.model_copy(update={"status": "FILLED", "filled_qty": 1.0, "average_price": 0.0})
        self._orders[client_order_id] = order
        return AdapterResult(
            success=True,
            adapter_order_id=order.adapter_order_id,
            exchange_order_id=order.exchange_order_id,
        )

    def cancel_order(
        self,
        *,
        client_order_id: str,
        execution_account_id: str,
        connector: str,
    ) -> AdapterResult:
        self.calls.append({"action": "cancel_order", "client_order_id": client_order_id})
        if client_order_id in self._orders:
            self._orders[client_order_id] = self._orders[client_order_id].model_copy(
                update={"status": "CANCELLED"}
            )
        return AdapterResult(success=True)

    def get_order_status(
        self,
        *,
        client_order_id: str,
        execution_account_id: str,
    ) -> RawAdapterOrder | None:
        return self._orders.get(client_order_id)

    def simulate_fill(self, client_order_id: str, price: float, qty: float) -> None:
        if client_order_id not in self._orders:
            raise KeyError(f"Order {client_order_id} not found in fake adapter")
        self._orders[client_order_id] = self._orders[client_order_id].model_copy(
            update={"status": "FILLED", "average_price": price, "filled_qty": qty}
        )

    def get_position_qty(
        self,
        *,
        symbol: str,
        side: str,
        execution_account_id: str,
    ) -> float | None:
        return self._positions.get(f"{symbol}:{side}")


__all__ = ["FakeAdapter"]
