# src/runtime_v2/execution_gateway/adapters/factory.py
from __future__ import annotations

import logging
import os

from src.runtime_v2.execution_gateway.adapters.base import ExecutionAdapter
from src.runtime_v2.execution_gateway.adapters.hummingbot_api import HummingbotApiAdapter
from src.runtime_v2.execution_gateway.models import AdapterConfig

logger = logging.getLogger(__name__)


def build_adapter(adapter_name: str, cfg: AdapterConfig) -> ExecutionAdapter:
    logger.debug("build_adapter: type=%s name=%s", cfg.type, adapter_name)
    if cfg.type == "hummingbot_api":
        secret = cfg.secret or os.environ.get("HUMMINGBOT_SECRET")
        return HummingbotApiAdapter(
            base_url=cfg.base_url,
            connector=cfg.connector,
            capabilities=cfg.capabilities,
            secret=secret,
        )
    if cfg.type == "ccxt_bybit":
        from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.adapter import CcxtBybitAdapter
        api_secret = os.environ.get(f"BYBIT_API_SECRET_{adapter_name.upper()}")
        return CcxtBybitAdapter(
            api_key=cfg.api_key or "",
            api_secret=api_secret or "",
            testnet=cfg.testnet,
            connector=cfg.connector,
            capabilities=cfg.capabilities,
        )
    raise ValueError(f"Unknown adapter type '{cfg.type}' for adapter '{adapter_name}'")


__all__ = ["build_adapter"]
