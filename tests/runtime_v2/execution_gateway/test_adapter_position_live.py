from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.runtime_v2.execution_gateway.adapters.base import (
    ExecutionAdapter,
    RawPositionLive,
)
from src.runtime_v2.execution_gateway.models import AdapterCapabilities, AdapterResult


class _DummyAdapter(ExecutionAdapter):
    def get_capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities()

    def set_leverage(
        self,
        symbol: str,
        leverage: int,
        execution_account_id: str,
    ) -> None:
        return None

    def place_order(
        self,
        *,
        command_type: str,
        payload: dict,
        client_order_id: str,
        execution_account_id: str,
        connector: str,
    ) -> AdapterResult:
        return AdapterResult(success=True)

    def cancel_order(
        self,
        *,
        client_order_id: str,
        execution_account_id: str,
        connector: str,
    ) -> AdapterResult:
        return AdapterResult(success=True)

    def get_order_status(
        self,
        *,
        client_order_id: str,
        execution_account_id: str,
    ):
        return None

    def get_position_qty(
        self,
        *,
        symbol: str,
        side: str,
        execution_account_id: str,
    ) -> float | None:
        return None

    def fetch_mark_price(
        self,
        symbol: str,
        execution_account_id: str,
    ) -> float | None:
        return None


def test_execution_adapter_fetch_all_positions_defaults_to_none():
    adapter = _DummyAdapter()

    assert adapter.fetch_all_positions("acc1") is None


def test_fake_adapter_fetch_all_positions_returns_injected_positions():
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter

    adapter = FakeAdapter()
    adapter.set_position_live(
        [
            RawPositionLive(
                symbol="BTCUSDT",
                side="LONG",
                qty=0.25,
                mark_price=65000.5,
                unrealized_pnl=125.0,
                cum_realized_pnl=12.5,
            ),
            RawPositionLive(
                symbol="ETHUSDT",
                side="SHORT",
                qty=1.5,
                mark_price=3500.0,
                unrealized_pnl=-33.25,
                cum_realized_pnl=9.0,
            ),
        ]
    )

    assert adapter.fetch_all_positions("acc1") == [
        RawPositionLive(
            symbol="BTCUSDT",
            side="LONG",
            qty=0.25,
            mark_price=65000.5,
            unrealized_pnl=125.0,
            cum_realized_pnl=12.5,
        ),
        RawPositionLive(
            symbol="ETHUSDT",
            side="SHORT",
            qty=1.5,
            mark_price=3500.0,
            unrealized_pnl=-33.25,
            cum_realized_pnl=9.0,
        ),
    ]


def test_ccxt_bybit_fetch_all_positions_maps_only_long_and_short_rows():
    from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.adapter import CcxtBybitAdapter

    exchange = MagicMock()
    exchange.fetch_positions.return_value = [
        {
            "symbol": "BTC/USDT:USDT",
            "side": "long",
            "contracts": 0.25,
            "markPrice": 65000.5,
            "unrealizedPnl": 125.0,
            "info": {
                "symbol": "BTCUSDT",
                "cumRealisedPnl": "12.5",
            },
        },
        {
            "symbol": "ETH/USDT:USDT",
            "side": "short",
            "contracts": 1.5,
            "markPrice": 3500.0,
            "unrealizedPnl": "-33.25",
            "info": {
                "symbol": "ETHUSDT",
                "cumRealisedPnl": "9.0",
            },
        },
        {
            "symbol": "XRP/USDT:USDT",
            "side": "",
            "contracts": 1000.0,
            "markPrice": 0.55,
            "unrealizedPnl": 0.0,
            "info": {"symbol": "XRPUSDT", "cumRealisedPnl": "0"},
        },
    ]
    adapter = CcxtBybitAdapter(api_key="k", api_secret="s", connector="c", _exchange=exchange)

    assert adapter.fetch_all_positions("acc1") == [
        RawPositionLive(
            symbol="BTCUSDT",
            side="LONG",
            qty=0.25,
            mark_price=65000.5,
            unrealized_pnl=125.0,
            cum_realized_pnl=12.5,
        ),
        RawPositionLive(
            symbol="ETHUSDT",
            side="SHORT",
            qty=1.5,
            mark_price=3500.0,
            unrealized_pnl=-33.25,
            cum_realized_pnl=9.0,
        ),
    ]
    exchange.fetch_positions.assert_called_once_with(params={"category": "linear"})


def test_ccxt_bybit_fetch_all_positions_returns_none_and_warns_on_failure(caplog):
    from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.adapter import CcxtBybitAdapter

    exchange = MagicMock()
    exchange.fetch_positions.side_effect = RuntimeError("boom")
    adapter = CcxtBybitAdapter(api_key="k", api_secret="s", connector="c", _exchange=exchange)

    with caplog.at_level("WARNING"):
        result = adapter.fetch_all_positions("acc1")

    assert result is None
    assert "fetch_all_positions failed" in caplog.text
    assert "boom" in caplog.text


def test_ccxt_bybit_fetch_all_positions_falls_back_to_info_mark_price():
    from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.adapter import CcxtBybitAdapter

    exchange = MagicMock()
    exchange.fetch_positions.return_value = [
        {
            "symbol": "BTC/USDT:USDT",
            "side": "long",
            "contracts": 0.25,
            "markPrice": None,
            "unrealizedPnl": 125.0,
            "info": {
                "symbol": "BTCUSDT",
                "markPrice": "65010.5",
                "cumRealisedPnl": "12.5",
            },
        }
    ]
    adapter = CcxtBybitAdapter(api_key="k", api_secret="s", connector="c", _exchange=exchange)

    result = adapter.fetch_all_positions("acc1")

    assert result == [
        RawPositionLive(
            symbol="BTCUSDT",
            side="LONG",
            qty=0.25,
            mark_price=65010.5,
            unrealized_pnl=125.0,
            cum_realized_pnl=12.5,
        )
    ]


def test_ccxt_bybit_fetch_all_positions_falls_back_to_info_unrealised_pnl():
    from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.adapter import CcxtBybitAdapter

    exchange = MagicMock()
    exchange.fetch_positions.return_value = [
        {
            "symbol": "ETH/USDT:USDT",
            "side": "short",
            "contracts": 1.5,
            "markPrice": 3500.0,
            "unrealizedPnl": None,
            "info": {
                "symbol": "ETHUSDT",
                "unrealisedPnl": "-33.25",
                "cumRealisedPnl": "9.0",
            },
        }
    ]
    adapter = CcxtBybitAdapter(api_key="k", api_secret="s", connector="c", _exchange=exchange)

    result = adapter.fetch_all_positions("acc1")

    assert result == [
        RawPositionLive(
            symbol="ETHUSDT",
            side="SHORT",
            qty=1.5,
            mark_price=3500.0,
            unrealized_pnl=-33.25,
            cum_realized_pnl=9.0,
        )
    ]


def test_ccxt_bybit_fetch_all_positions_falls_back_to_normalized_symbol():
    from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.adapter import CcxtBybitAdapter

    exchange = MagicMock()
    exchange.fetch_positions.return_value = [
        {
            "symbol": "SOL/USDT:USDT",
            "side": "long",
            "contracts": 12.0,
            "markPrice": 155.25,
            "unrealizedPnl": 18.0,
            "info": {
                "cumRealisedPnl": "4.5",
            },
        }
    ]
    adapter = CcxtBybitAdapter(api_key="k", api_secret="s", connector="c", _exchange=exchange)

    result = adapter.fetch_all_positions("acc1")

    assert result == [
        RawPositionLive(
            symbol="SOLUSDT",
            side="LONG",
            qty=12.0,
            mark_price=155.25,
            unrealized_pnl=18.0,
            cum_realized_pnl=4.5,
        )
    ]


def test_ccxt_bybit_fetch_all_positions_returns_none_and_warns_on_malformed_row(caplog):
    from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.adapter import CcxtBybitAdapter

    exchange = MagicMock()
    exchange.fetch_positions.return_value = [
        {
            "symbol": "BTC/USDT:USDT",
            "side": "long",
            "contracts": "bad",
            "markPrice": 65000.5,
            "unrealizedPnl": 125.0,
            "info": {
                "symbol": "BTCUSDT",
                "cumRealisedPnl": "12.5",
            },
        }
    ]
    adapter = CcxtBybitAdapter(api_key="k", api_secret="s", connector="c", _exchange=exchange)

    with caplog.at_level("WARNING"):
        result = adapter.fetch_all_positions("acc1")

    assert result is None
    assert "fetch_all_positions failed" in caplog.text
