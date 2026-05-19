# tests/runtime_v2/execution_gateway/test_adapter_factory.py
from __future__ import annotations

import pytest
import yaml
from pathlib import Path

from src.runtime_v2.execution_gateway.adapters.factory import build_adapter
from src.runtime_v2.execution_gateway.adapters.hummingbot_api import HummingbotApiAdapter
from src.runtime_v2.execution_gateway.models import AdapterCapabilities, AdapterConfig


def _make_cfg(**kwargs) -> AdapterConfig:
    defaults = {
        "type": "hummingbot_api",
        "mode": "demo",
        "base_url": "http://localhost:8001",
        "connector": "bybit_perpetual_demo",
    }
    defaults.update(kwargs)
    return AdapterConfig.model_validate(defaults)


def test_build_hummingbot_api_adapter():
    cfg = _make_cfg()
    adapter = build_adapter("hummingbot_api_demo", cfg)
    assert isinstance(adapter, HummingbotApiAdapter)


def test_build_adapter_passes_capabilities():
    caps = AdapterCapabilities(
        place_entry=True,
        protective_stop_native=False,
        take_profit_native=False,
        bracket_order=False,
        move_stop=False,
        close_partial=True,
        close_full=True,
        executor_position=False,
    )
    cfg = _make_cfg(capabilities=caps.model_dump())
    adapter = build_adapter("hummingbot_api_demo", cfg)
    assert adapter.get_capabilities().protective_stop_native is False
    assert adapter.get_capabilities().close_full is True


def test_build_adapter_unknown_type_raises():
    cfg = _make_cfg(type="unknown_type")
    with pytest.raises(ValueError, match="Unknown adapter type"):
        build_adapter("bad_adapter", cfg)


def test_build_adapter_testnet_mode():
    cfg = _make_cfg(
        mode="testnet",
        base_url="http://localhost:8000",
        connector="bybit_perpetual_testnet",
    )
    adapter = build_adapter("hummingbot_api_testnet", cfg)
    assert isinstance(adapter, HummingbotApiAdapter)


def test_build_ccxt_bybit_adapter(monkeypatch):
    from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.adapter import CcxtBybitAdapter
    monkeypatch.setenv("BYBIT_API_SECRET_BYBIT_TESTNET", "test_secret")
    cfg = AdapterConfig.model_validate({
        "type": "ccxt_bybit",
        "mode": "paper",
        "connector": "bybit",
        "api_key": "test_key",
        "testnet": True,
    })
    adapter = build_adapter("bybit_testnet", cfg)
    assert isinstance(adapter, CcxtBybitAdapter)
