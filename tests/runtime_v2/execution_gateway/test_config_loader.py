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
    assert routing.position_mode == "hedge"


def test_resolve_routing_reads_position_mode(tmp_path):
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    cfg = {
        "execution": {
            "default_adapter": "fake",
            "account_routing": {
                "default": {
                    "adapter": "fake",
                    "execution_account_id": "acc_main",
                    "position_mode": "one_way",
                }
            },
            "adapters": {
                "fake": {
                    "type": "fake", "mode": "paper",
                    "connector": "fake_connector",
                }
            },
        }
    }
    p = tmp_path / "execution.yaml"
    p.write_text(yaml.dump(cfg))
    config = ExecutionConfigLoader(str(p)).load()
    routing, _ = config.resolve_routing("acc_unknown")
    assert routing.position_mode == "one_way"


def test_missing_default_routing_raises(tmp_path):
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    cfg = {
        "execution": {
            "default_adapter": "fake",
            "account_routing": {},
            "adapters": {
                "fake": {
                    "type": "fake", "mode": "paper",
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
    cfg = {
        "execution": {
            "default_adapter": "bybit_demo",
            "account_routing": {
                "default": {"adapter": "bybit_demo", "execution_account_id": "master_account"}
            },
            "adapters": {
                "bybit_paper": {
                    "type": "ccxt_bybit",
                    "mode": "paper",
                    "connector": "bybit",
                },
                "bybit_demo": {
                    "type": "ccxt_bybit",
                    "mode": "demo",
                    "connector": "bybit",
                },
            },
        }
    }
    p = tmp_path / "execution.yaml"
    p.write_text(yaml.dump(cfg))
    config = ExecutionConfigLoader(str(p)).load()
    assert config.default_adapter == "bybit_demo"
    assert "bybit_paper" in config.adapters
    assert "bybit_demo" in config.adapters
    assert config.adapters["bybit_demo"].connector == "bybit"
    assert config.adapters["bybit_demo"].mode == "demo"


def test_real_execution_yaml_loads():
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    config = ExecutionConfigLoader("config/execution.yaml").load()
    assert config.default_adapter == "bybit_demo"
    assert config.adapters["bybit_demo"].strategy.simple_attached_enabled is True
    assert config.adapters["bybit_demo"].live_safety.allow_live_trading is False


def test_real_execution_yaml_no_deprecated_fields():
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    config = ExecutionConfigLoader("config/execution.yaml").load()
    demo = config.adapters["bybit_demo"]
    assert not hasattr(demo, "leverage")
    assert not hasattr(demo, "hedge_mode")
    assert not hasattr(demo, "capabilities")
    assert not hasattr(demo, "entry_execution")


