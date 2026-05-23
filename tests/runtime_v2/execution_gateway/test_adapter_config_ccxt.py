from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.runtime_v2.execution_gateway.models import AdapterConfig


def test_adapter_config_ccxt_bybit_type_accepted():
    cfg = AdapterConfig.model_validate({
        "type": "ccxt_bybit",
        "mode": "paper",
        "connector": "bybit",
    })
    assert cfg.type == "ccxt_bybit"


def test_adapter_config_api_key_env_field():
    cfg = AdapterConfig.model_validate({
        "type": "ccxt_bybit",
        "mode": "paper",
        "connector": "bybit",
        "api_key_env": "MY_KEY_ENV",
    })
    assert cfg.api_key_env == "MY_KEY_ENV"


def test_adapter_config_api_key_env_defaults_none():
    cfg = AdapterConfig.model_validate({
        "type": "ccxt_bybit",
        "mode": "paper",
        "connector": "bybit",
    })
    assert cfg.api_key_env is None
    assert cfg.api_secret_env is None


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
        "websocket": {"enabled": True, "poll_fallback_period_seconds": 30},
    })
    assert cfg.websocket.enabled is True
    assert cfg.websocket.poll_fallback_period_seconds == 30


def test_adapter_config_strategy_defaults():
    cfg = AdapterConfig.model_validate({
        "type": "ccxt_bybit",
        "mode": "paper",
        "connector": "bybit",
    })
    assert cfg.strategy.simple_attached_enabled is True


def test_adapter_config_deprecated_fields_rejected():
    for field in ("leverage", "hedge_mode", "entry_execution", "capabilities", "testnet", "api_key"):
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            AdapterConfig.model_validate({
                "type": "ccxt_bybit", "mode": "paper", "connector": "bybit",
                field: "anything",
            })
