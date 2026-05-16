# src/runtime_v2/execution_gateway/config_loader.py
from __future__ import annotations

import yaml

from src.runtime_v2.execution_gateway.models import ExecutionConfig


class ExecutionConfigLoader:
    def __init__(self, config_path: str = "config/execution.yaml") -> None:
        self._path = config_path

    def load(self) -> ExecutionConfig:
        with open(self._path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        return ExecutionConfig.model_validate(raw["execution"])


__all__ = ["ExecutionConfigLoader"]
