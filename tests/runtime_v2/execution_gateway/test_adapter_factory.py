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


def test_build_ccxt_bybit_adapter(monkeypatch):
    from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.adapter import CcxtBybitAdapter
    monkeypatch.setenv("BYBIT_API_KEY_DEMO", "test_key")
    cfg = _make_ccxt_cfg(api_key_env="BYBIT_API_KEY_DEMO")
    adapter = build_adapter("bybit_demo", cfg)
    assert isinstance(adapter, CcxtBybitAdapter)


def test_build_adapter_passes_capabilities(monkeypatch):
    from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.adapter import CcxtBybitAdapter
    cfg = _make_ccxt_cfg()
    adapter = build_adapter("bybit_main", cfg)
    assert isinstance(adapter, CcxtBybitAdapter)
    assert adapter.get_capabilities().close_full is True
    assert adapter.get_capabilities().protective_stop_native is True


def test_build_adapter_unknown_type_raises():
    cfg = AdapterConfig.model_validate({
        "type": "unknown_type",
        "mode": "paper",
        "connector": "bybit",
    })
    with pytest.raises(ValueError, match="Unknown adapter type"):
        build_adapter("bad_adapter", cfg)


def test_build_ccxt_bybit_reads_api_key_from_env(monkeypatch):
    from src.runtime_v2.execution_gateway.adapters.factory import build_adapter
    from src.runtime_v2.execution_gateway.models import AdapterConfig
    monkeypatch.setenv("MY_API_KEY_ENV", "key123")
    monkeypatch.setenv("MY_API_SECRET_ENV", "secret456")
    cfg = AdapterConfig.model_validate({
        "type": "ccxt_bybit",
        "mode": "paper",
        "connector": "bybit",
        "api_key_env": "MY_API_KEY_ENV",
        "api_secret_env": "MY_API_SECRET_ENV",
    })
    captured = {}
    class FakeAdapter:
        def __init__(self, api_key, api_secret, **kw):
            captured["api_key"] = api_key
            captured["api_secret"] = api_secret
    import src.runtime_v2.execution_gateway.adapters.ccxt_bybit.adapter as amod
    monkeypatch.setattr(amod, "CcxtBybitAdapter", FakeAdapter)
    build_adapter("demo", cfg)
    assert captured["api_key"] == "key123"
    assert captured["api_secret"] == "secret456"


def test_build_ccxt_bybit_no_env_gives_empty_string(monkeypatch):
    from src.runtime_v2.execution_gateway.adapters.factory import build_adapter
    from src.runtime_v2.execution_gateway.models import AdapterConfig
    cfg = AdapterConfig.model_validate({
        "type": "ccxt_bybit", "mode": "paper", "connector": "bybit",
    })
    captured = {}
    class FakeAdapter:
        def __init__(self, api_key, api_secret, **kw):
            captured["api_key"] = api_key
            captured["api_secret"] = api_secret
    import src.runtime_v2.execution_gateway.adapters.ccxt_bybit.adapter as amod
    monkeypatch.setattr(amod, "CcxtBybitAdapter", FakeAdapter)
    build_adapter("demo", cfg)
    assert captured["api_key"] == ""
    assert captured["api_secret"] == ""
