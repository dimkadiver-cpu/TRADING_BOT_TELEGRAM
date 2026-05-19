from __future__ import annotations

import pytest
from src.runtime_v2.execution_gateway.adapters.factory import build_adapter
from src.runtime_v2.execution_gateway.models import AdapterConfig


def test_adapter_config_ccxt_bybit_type_accepted():
    cfg = AdapterConfig.model_validate({
        "type": "ccxt_bybit",
        "mode": "paper",
        "connector": "bybit",
    })
    assert cfg.type == "ccxt_bybit"


def test_adapter_config_api_key_field():
    cfg = AdapterConfig.model_validate({
        "type": "ccxt_bybit",
        "mode": "paper",
        "connector": "bybit",
        "api_key": "abc123",
    })
    assert cfg.api_key == "abc123"


def test_adapter_config_testnet_field():
    cfg = AdapterConfig.model_validate({
        "type": "ccxt_bybit",
        "mode": "paper",
        "connector": "bybit",
        "testnet": True,
    })
    assert cfg.testnet is True


def test_adapter_config_testnet_defaults_false():
    cfg = AdapterConfig.model_validate({
        "type": "ccxt_bybit",
        "mode": "paper",
        "connector": "bybit",
    })
    assert cfg.testnet is False


def test_adapter_config_api_key_defaults_none():
    cfg = AdapterConfig.model_validate({
        "type": "ccxt_bybit",
        "mode": "paper",
        "connector": "bybit",
    })
    assert cfg.api_key is None


def test_adapter_config_base_url_optional_no_default_required():
    # ccxt_bybit doesn't use base_url - must work without it
    cfg = AdapterConfig.model_validate({
        "type": "ccxt_bybit",
        "mode": "paper",
        "connector": "bybit",
    })
    assert cfg.base_url == ""


def test_adapter_config_base_url_still_accepted_when_provided():
    cfg = AdapterConfig.model_validate({
        "type": "hummingbot_api",
        "mode": "demo",
        "connector": "bybit_perpetual_demo",
        "base_url": "http://localhost:8001",
    })
    assert cfg.base_url == "http://localhost:8001"


def test_adapter_config_hummingbot_api_without_base_url_fails_validation():
    with pytest.raises(ValueError, match="base_url is required"):
        AdapterConfig.model_validate({
            "type": "hummingbot_api",
            "mode": "demo",
            "connector": "bybit_perpetual_demo",
        })


def test_adapter_config_hummingbot_api_empty_base_url_fails_validation():
    with pytest.raises(ValueError, match="base_url is required"):
        AdapterConfig.model_validate({
            "type": "hummingbot_api",
            "mode": "demo",
            "connector": "bybit_perpetual_demo",
            "base_url": "",
        })


def test_adapter_config_hummingbot_api_blank_base_url_fails_validation():
    with pytest.raises(ValueError, match="base_url is required"):
        AdapterConfig.model_validate({
            "type": "hummingbot_api",
            "mode": "demo",
            "connector": "bybit_perpetual_demo",
            "base_url": "   ",
        })


def test_adapter_config_hedge_mode_defaults_false():
    cfg = AdapterConfig.model_validate({
        "type": "ccxt_bybit",
        "mode": "paper",
        "connector": "bybit",
    })
    assert cfg.hedge_mode is False


def test_adapter_config_hedge_mode_true():
    cfg = AdapterConfig.model_validate({
        "type": "ccxt_bybit",
        "mode": "paper",
        "connector": "bybit",
        "hedge_mode": True,
    })
    assert cfg.hedge_mode is True


def test_adapter_config_websocket_defaults():
    cfg = AdapterConfig.model_validate({
        "type": "ccxt_bybit",
        "mode": "paper",
        "connector": "bybit",
    })
    assert cfg.websocket.enabled is False
    assert cfg.websocket.poll_fallback_enabled is True
    assert cfg.websocket.poll_fallback_period_seconds == 60


def test_adapter_config_websocket_custom():
    cfg = AdapterConfig.model_validate({
        "type": "ccxt_bybit",
        "mode": "paper",
        "connector": "bybit",
        "websocket": {
            "enabled": True,
            "poll_fallback_period_seconds": 30,
        },
    })
    assert cfg.websocket.enabled is True
    assert cfg.websocket.poll_fallback_period_seconds == 30
