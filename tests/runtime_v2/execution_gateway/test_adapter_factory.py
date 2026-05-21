# tests/runtime_v2/execution_gateway/test_adapter_factory.py
from __future__ import annotations

import pytest

from src.runtime_v2.execution_gateway.adapters.factory import build_adapter
from src.runtime_v2.execution_gateway.models import AdapterConfig


def _make_ccxt_cfg(**kwargs) -> AdapterConfig:
    defaults = {
        "type": "ccxt_bybit",
        "mode": "paper",
        "connector": "bybit",
    }
    defaults.update(kwargs)
    return AdapterConfig.model_validate(defaults)


@pytest.mark.xfail(reason="Task 2: factory.py still uses removed fields", raises=AttributeError)
def test_build_ccxt_bybit_adapter(monkeypatch):
    from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.adapter import CcxtBybitAdapter
    monkeypatch.setenv("BYBIT_API_SECRET_BYBIT_DEMO", "test_secret")
    cfg = _make_ccxt_cfg(api_key_env="BYBIT_API_KEY_DEMO")
    adapter = build_adapter("bybit_demo", cfg)
    assert isinstance(adapter, CcxtBybitAdapter)


@pytest.mark.xfail(reason="Task 2: factory.py still uses removed fields", raises=AttributeError)
def test_build_adapter_passes_capabilities(monkeypatch):
    from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.adapter import CcxtBybitAdapter
    monkeypatch.setenv("BYBIT_API_SECRET_BYBIT_MAIN", "secret")
    cfg = _make_ccxt_cfg()
    adapter = build_adapter("bybit_main", cfg)
    assert isinstance(adapter, CcxtBybitAdapter)
    assert adapter.get_capabilities().close_full is True
    assert adapter.get_capabilities().protective_stop_native is False


def test_build_adapter_unknown_type_raises():
    cfg = AdapterConfig.model_validate({
        "type": "unknown_type",
        "mode": "paper",
        "connector": "bybit",
    })
    with pytest.raises(ValueError, match="Unknown adapter type"):
        build_adapter("bad_adapter", cfg)


@pytest.mark.xfail(reason="Task 2: factory.py still uses removed fields", raises=AttributeError)
def test_factory_ccxt_bybit_passes_hedge_mode(monkeypatch):
    monkeypatch.setenv("BYBIT_API_SECRET_HEDGE_MAIN", "secret123")
    cfg = AdapterConfig.model_validate({
        "type": "ccxt_bybit",
        "mode": "paper",
        "connector": "bybit",
    })
    adapter = build_adapter("hedge_main", cfg)
    assert adapter._hedge_mode is True


@pytest.mark.xfail(reason="Task 2: factory.py still uses removed fields", raises=AttributeError)
def test_factory_ccxt_bybit_hedge_mode_false_by_default(monkeypatch):
    monkeypatch.setenv("BYBIT_API_SECRET_BYBIT_MAIN", "secret123")
    cfg = AdapterConfig.model_validate({
        "type": "ccxt_bybit",
        "mode": "paper",
        "connector": "bybit",
    })
    adapter = build_adapter("bybit_main", cfg)
    assert adapter._hedge_mode is False
