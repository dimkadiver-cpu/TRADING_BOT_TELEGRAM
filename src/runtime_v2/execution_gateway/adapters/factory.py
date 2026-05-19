# src/runtime_v2/execution_gateway/adapters/factory.py
from __future__ import annotations

import logging
import os

from src.runtime_v2.execution_gateway.adapters.base import ExecutionAdapter
from src.runtime_v2.execution_gateway.models import AdapterConfig

logger = logging.getLogger(__name__)


def build_adapter(adapter_name: str, cfg: AdapterConfig) -> ExecutionAdapter:
    logger.debug("build_adapter: type=%s name=%s", cfg.type, adapter_name)
    if cfg.type == "ccxt_bybit":
        from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.adapter import CcxtBybitAdapter
        api_secret = os.environ.get(f"BYBIT_API_SECRET_{adapter_name.upper()}")
        # repo is not injected here — must be wired by ExecutionCommandWorker to enable
        # the OD-F1-2 get_order_status fallback (Mode C attached SL/TP).
        return CcxtBybitAdapter(
            api_key=cfg.api_key or "",
            api_secret=api_secret or "",
            testnet=cfg.testnet,
            connector=cfg.connector,
            mode=cfg.mode,
            capabilities=cfg.capabilities,
            hedge_mode=cfg.hedge_mode,
        )
    raise ValueError(f"Unknown adapter type '{cfg.type}' for adapter '{adapter_name}'")


__all__ = ["build_adapter"]
