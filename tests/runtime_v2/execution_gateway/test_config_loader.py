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
