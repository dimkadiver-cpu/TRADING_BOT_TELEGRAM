# src/runtime_v2/execution_gateway/adapters/factory.py
from __future__ import annotations

import os

from src.runtime_v2.execution_gateway.adapters.base import ExecutionAdapter
from src.runtime_v2.execution_gateway.adapters.hummingbot_api import HummingbotApiAdapter
from src.runtime_v2.execution_gateway.models import AdapterConfig


def build_adapter(adapter_name: str, cfg: AdapterConfig) -> ExecutionAdapter:
    if cfg.type == "hummingbot_api":
        secret = cfg.secret or os.environ.get("HUMMINGBOT_SECRET")
        return HummingbotApiAdapter(
            base_url=cfg.base_url,
            connector=cfg.connector,
            capabilities=cfg.capabilities,
            secret=secret,
        )
    raise ValueError(f"Unknown adapter type '{cfg.type}' for adapter '{adapter_name}'")


__all__ = ["build_adapter"]
