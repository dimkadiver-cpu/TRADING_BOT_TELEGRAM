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
        api_key = os.environ.get(cfg.api_key_env) if cfg.api_key_env else ""
        api_secret = os.environ.get(cfg.api_secret_env) if cfg.api_secret_env else ""
        return CcxtBybitAdapter(
            api_key=api_key or "",
            api_secret=api_secret or "",
            connector=cfg.connector,
            mode=cfg.mode,
            adjust_for_time_difference=cfg.adjust_for_time_difference,
            recv_window_ms=cfg.recv_window_ms,
            time_sync_on_startup=cfg.time_sync_on_startup,
        )
    raise ValueError(f"Unknown adapter type '{cfg.type}' for adapter '{adapter_name}'")


__all__ = ["build_adapter"]
