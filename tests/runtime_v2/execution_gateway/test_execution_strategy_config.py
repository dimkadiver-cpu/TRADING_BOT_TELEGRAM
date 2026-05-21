from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.runtime_v2.execution_gateway.models import AdapterConfig, ExecutionStrategyConfig


def test_strategy_config_defaults():
    s = ExecutionStrategyConfig()
    assert s.default_mode == "D_POSITION_TPSL"
    assert s.simple_attached_enabled is True
    assert s.trigger_by == "MarkPrice"
    assert s.one_tp_mode == "FULL"
    assert s.multi_tp_mode == "PARTIAL"


def test_strategy_config_invalid_mode():
    with pytest.raises(ValidationError):
        ExecutionStrategyConfig(default_mode="X_UNKNOWN")


def test_adapter_config_new_format_accepted():
    cfg = AdapterConfig.model_validate({
        "type": "ccxt_bybit",
        "mode": "demo",
        "connector": "bybit",
        "api_key_env": "BYBIT_API_KEY_DEMO",
        "api_secret_env": "BYBIT_API_SECRET_DEMO",
    })
    assert cfg.api_key_env == "BYBIT_API_KEY_DEMO"
    assert cfg.api_secret_env == "BYBIT_API_SECRET_DEMO"
    assert cfg.strategy.default_mode == "D_POSITION_TPSL"


def test_adapter_config_strategy_block_accepted():
    cfg = AdapterConfig.model_validate({
        "type": "ccxt_bybit",
        "mode": "demo",
        "connector": "bybit",
        "strategy": {
            "default_mode": "C_SIMPLE_ATTACHED",
            "simple_attached_enabled": False,
        },
    })
    assert cfg.strategy.default_mode == "C_SIMPLE_ATTACHED"
    assert cfg.strategy.simple_attached_enabled is False


def test_adapter_config_deprecated_leverage_raises():
    with pytest.raises(ValidationError):
        AdapterConfig.model_validate({
            "type": "ccxt_bybit",
            "mode": "demo",
            "connector": "bybit",
            "leverage": 10,
        })


def test_adapter_config_deprecated_hedge_mode_raises():
    with pytest.raises(ValidationError):
        AdapterConfig.model_validate({
            "type": "ccxt_bybit",
            "mode": "demo",
            "connector": "bybit",
            "hedge_mode": True,
        })


def test_adapter_config_deprecated_entry_execution_raises():
    with pytest.raises(ValidationError):
        AdapterConfig.model_validate({
            "type": "ccxt_bybit",
            "mode": "demo",
            "connector": "bybit",
            "entry_execution": {"mode": "b_entry_stop_then_tp"},
        })
