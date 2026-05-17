# tests/runtime_v2/execution_gateway/test_config_loader.py
from __future__ import annotations

import pytest
import yaml
from pathlib import Path


@pytest.fixture
def minimal_config(tmp_path) -> Path:
    cfg = {
        "execution": {
            "default_adapter": "fake",
            "account_routing": {
                "default": {"adapter": "fake", "execution_account_id": "acc_main"}
            },
            "adapters": {
                "fake": {
                    "type": "fake", "mode": "paper",
                    "base_url": "http://localhost:9999",
                    "connector": "fake_connector",
                }
            },
        }
    }
    p = tmp_path / "execution.yaml"
    p.write_text(yaml.dump(cfg))
    return p


def test_load_valid_config(minimal_config):
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    loader = ExecutionConfigLoader(str(minimal_config))
    config = loader.load()
    assert config.default_adapter == "fake"
    assert "default" in config.account_routing


def test_resolve_routing_default(minimal_config):
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    config = ExecutionConfigLoader(str(minimal_config)).load()
    routing, adapter = config.resolve_routing("acc_unknown")
    assert routing.execution_account_id == "acc_main"
    assert adapter.type == "fake"


def test_resolve_routing_specific(tmp_path):
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    cfg = {
        "execution": {
            "default_adapter": "fake",
            "account_routing": {
                "default": {"adapter": "fake", "execution_account_id": "acc_main"},
                "acc_trader_a": {"adapter": "fake", "execution_account_id": "acc_a"},
            },
            "adapters": {
                "fake": {
                    "type": "fake", "mode": "paper",
                    "base_url": "http://localhost:9999",
                    "connector": "fake_connector",
                }
            },
        }
    }
    p = tmp_path / "execution.yaml"
    p.write_text(yaml.dump(cfg))
    config = ExecutionConfigLoader(str(p)).load()
    routing, _ = config.resolve_routing("acc_trader_a")
    assert routing.execution_account_id == "acc_a"


def test_missing_default_routing_raises(tmp_path):
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    cfg = {
        "execution": {
            "default_adapter": "fake",
            "account_routing": {},
            "adapters": {
                "fake": {
                    "type": "fake", "mode": "paper",
                    "base_url": "http://localhost:9999",
                    "connector": "fake_connector",
                }
            },
        }
    }
    p = tmp_path / "execution.yaml"
    p.write_text(yaml.dump(cfg))
    config = ExecutionConfigLoader(str(p)).load()
    with pytest.raises(KeyError):
        config.resolve_routing("acc_x")


def test_load_multi_adapter_config(tmp_path):
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    import yaml
    cfg = {
        "execution": {
            "default_adapter": "hummingbot_api_demo",
            "account_routing": {
                "default": {"adapter": "hummingbot_api_demo", "execution_account_id": "master_account"}
            },
            "adapters": {
                "hummingbot_api_paper": {
                    "type": "hummingbot_api",
                    "mode": "paper",
                    "base_url": "http://localhost:8000",
                    "connector": "bybit_perpetual_testnet",
                },
                "hummingbot_api_demo": {
                    "type": "hummingbot_api",
                    "mode": "demo",
                    "base_url": "http://localhost:8001",
                    "connector": "bybit_perpetual_demo",
                },
            },
        }
    }
    p = tmp_path / "execution.yaml"
    p.write_text(yaml.dump(cfg))
    config = ExecutionConfigLoader(str(p)).load()
    assert config.default_adapter == "hummingbot_api_demo"
    assert "hummingbot_api_paper" in config.adapters
    assert "hummingbot_api_demo" in config.adapters
    assert config.adapters["hummingbot_api_demo"].connector == "bybit_perpetual_demo"
    assert config.adapters["hummingbot_api_demo"].mode == "demo"


def test_demo_adapter_capabilities_parse():
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    config = ExecutionConfigLoader("config/execution.yaml").load()
    demo_caps = config.adapters["hummingbot_api_demo"].capabilities
    assert demo_caps.place_entry is True
    assert demo_caps.protective_stop_native is False
    assert demo_caps.take_profit_native is False
    assert demo_caps.close_full is True


def test_demo_adapter_live_safety_false():
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    config = ExecutionConfigLoader("config/execution.yaml").load()
    assert config.adapters["hummingbot_api_demo"].live_safety.allow_live_trading is False
