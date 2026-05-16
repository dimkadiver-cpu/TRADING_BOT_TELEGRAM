# src/runtime_v2/execution_gateway/adapters/hummingbot_api_paper.py
from __future__ import annotations

import logging

import httpx

from src.runtime_v2.execution_gateway.adapters.base import ExecutionAdapter
from src.runtime_v2.execution_gateway.models import AdapterCapabilities, AdapterResult, RawAdapterOrder

logger = logging.getLogger(__name__)

_SIDE_MAP = {"LONG": "BUY", "SHORT": "SELL"}
_CLOSE_SIDE_MAP = {"LONG": "SELL", "SHORT": "BUY"}


class HummingbotApiPaperAdapter(ExecutionAdapter):
    def __init__(self, base_url: str, connector: str, timeout: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._connector = connector
        self._client = httpx.Client(base_url=self._base_url, timeout=timeout)

    def get_capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            place_entry=True,
            protective_stop_native=True,
            take_profit_native=True,
            bracket_order=False,
            move_stop=True,
            close_partial=True,
            close_full=True,
            executor_position=False,
        )

    def set_leverage(self, symbol: str, leverage: int, execution_account_id: str) -> None:
        trading_pair = symbol.replace("/", "-")
        self._client.post("/trading/leverage", json={
            "account_name": execution_account_id,
            "connector_name": self._connector,
            "trading_pair": trading_pair,
            "leverage": leverage,
        }).raise_for_status()

    def place_order(
        self,
        *,
        command_type: str,
        payload: dict,
        client_order_id: str,
        execution_account_id: str,
        connector: str,
    ) -> AdapterResult:
        try:
            body = self._build_order_body(
                command_type, payload, client_order_id, execution_account_id
            )
            resp = self._client.post("/trading/orders", json=body)
            resp.raise_for_status()
            data = resp.json()
            return AdapterResult(
                success=True,
                adapter_order_id=str(data.get("id", "")),
                exchange_order_id=str(data.get("exchange_order_id", "")),
            )
        except httpx.HTTPStatusError as e:
            return AdapterResult(success=False, error=str(e), reason="exchange_rejected")
        except Exception:
            raise  # timeout and connection errors go to gateway retry

    def cancel_order(
        self,
        *,
        client_order_id: str,
        execution_account_id: str,
        connector: str,
    ) -> AdapterResult:
        try:
            resp = self._client.post(
                f"/trading/{execution_account_id}/{connector}/orders/{client_order_id}/cancel"
            )
            resp.raise_for_status()
            return AdapterResult(success=True)
        except httpx.HTTPStatusError as e:
            return AdapterResult(success=False, error=str(e))

    def get_order_status(
        self,
        *,
        client_order_id: str,
        execution_account_id: str,
    ) -> RawAdapterOrder | None:
        try:
            resp = self._client.post("/trading/orders/search", json={
                "client_order_id": client_order_id,
                "account_name": execution_account_id,
            })
            resp.raise_for_status()
            data = resp.json()
            orders = data.get("data") or (data if isinstance(data, list) else [])
            if not orders:
                return None
            o = orders[0]
            status = "FILLED" if o.get("is_done") else "OPEN"
            return RawAdapterOrder(
                client_order_id=client_order_id,
                exchange_order_id=str(o.get("exchange_order_id", "")),
                adapter_order_id=str(o.get("id", "")),
                status=status,
                filled_qty=float(o.get("executed_amount_base", 0)),
                average_price=float(o.get("average_executed_price", 0)) or None,
            )
        except Exception:
            logger.warning("get_order_status failed for %s", client_order_id)
            return None

    def get_position_qty(
        self,
        *,
        symbol: str,
        side: str,
        execution_account_id: str,
    ) -> float | None:
        try:
            trading_pair = symbol.replace("/", "-")
            resp = self._client.get(f"/trading/positions/{execution_account_id}")
            resp.raise_for_status()
            positions = resp.json()
            for p in positions:
                if p.get("trading_pair") == trading_pair and p.get("side") == side:
                    return float(p.get("amount", 0))
            return None
        except Exception:
            logger.warning("get_position_qty failed for %s %s", symbol, side)
            return None

    def _build_order_body(
        self, command_type: str, payload: dict,
        client_order_id: str, execution_account_id: str
    ) -> dict:
        symbol = payload["symbol"]
        side = payload["side"]
        trading_pair = symbol.replace("/", "-")

        base: dict = {
            "account_name": execution_account_id,
            "connector_name": self._connector,
            "trading_pair": trading_pair,
            "client_order_id": client_order_id,
        }

        if command_type == "PLACE_ENTRY":
            entry_type = payload["entry_type"]
            base.update({
                "trade_type": _SIDE_MAP[side],
                "order_type": entry_type,
                "amount": payload["qty"],
                "position_action": "OPEN",
            })
            if entry_type == "LIMIT":
                base["price"] = payload["price"]

        elif command_type == "PLACE_PROTECTIVE_STOP":
            base.update({
                "trade_type": _CLOSE_SIDE_MAP[side],
                "order_type": "STOP_LOSS",
                "price": payload["stop_price"],
                "amount": payload["qty"],
                "position_action": "CLOSE",
                "reduce_only": True,
            })

        elif command_type == "PLACE_TAKE_PROFIT":
            base.update({
                "trade_type": _CLOSE_SIDE_MAP[side],
                "order_type": "LIMIT",
                "price": payload["price"],
                "amount": payload.get("qty", 0),
                "position_action": "CLOSE",
                "reduce_only": True,
            })

        elif command_type in ("CLOSE_PARTIAL", "CLOSE_FULL"):
            base.update({
                "trade_type": _CLOSE_SIDE_MAP[side],
                "order_type": "MARKET",
                "amount": payload.get("qty", 0),
                "position_action": "CLOSE",
                "reduce_only": True,
            })

        return base


__all__ = ["HummingbotApiPaperAdapter"]
