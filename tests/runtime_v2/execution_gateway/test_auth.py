# tests/runtime_v2/execution_gateway/test_auth.py
from __future__ import annotations

import httpx
import pytest


def test_adapter_sends_bearer_header(respx_mock):
    """Con secret configurato, ogni richiesta porta Authorization: Bearer <secret>."""
    from src.runtime_v2.execution_gateway.adapters.hummingbot_api_paper import HummingbotApiPaperAdapter

    respx_mock.post("http://localhost:8000/trading/orders").mock(
        return_value=httpx.Response(200, json={"id": "abc", "exchange_order_id": "exch_1"})
    )

    adapter = HummingbotApiPaperAdapter(
        base_url="http://localhost:8000",
        connector="bybit_perpetual_paper_trade",
        secret="my_secret_123",
    )
    result = adapter.place_order(
        command_type="PLACE_ENTRY",
        payload={"symbol": "BTC/USDT", "side": "LONG",
                 "entry_type": "LIMIT", "price": 50000.0, "qty": 0.02, "sequence": 1},
        client_order_id="tsb:1:1:entry:1",
        execution_account_id="acc_main",
        connector="bybit_perpetual_paper_trade",
    )
    assert result.success
    sent_request = respx_mock.calls[0].request
    assert sent_request.headers.get("authorization") == "Bearer my_secret_123"


def test_adapter_no_auth_header_when_no_secret(respx_mock):
    """Senza secret, nessun header Authorization."""
    from src.runtime_v2.execution_gateway.adapters.hummingbot_api_paper import HummingbotApiPaperAdapter

    respx_mock.post("http://localhost:8000/trading/orders").mock(
        return_value=httpx.Response(200, json={"id": "abc", "exchange_order_id": "exch_1"})
    )

    adapter = HummingbotApiPaperAdapter(
        base_url="http://localhost:8000",
        connector="bybit_perpetual_paper_trade",
        secret=None,
    )
    adapter.place_order(
        command_type="PLACE_ENTRY",
        payload={"symbol": "BTC/USDT", "side": "LONG",
                 "entry_type": "LIMIT", "price": 50000.0, "qty": 0.02, "sequence": 1},
        client_order_id="tsb:1:2:entry:1",
        execution_account_id="acc_main",
        connector="bybit_perpetual_paper_trade",
    )
    sent_request = respx_mock.calls[0].request
    assert "authorization" not in sent_request.headers
