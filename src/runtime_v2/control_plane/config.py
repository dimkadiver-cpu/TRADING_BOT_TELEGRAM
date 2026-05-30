from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from src.runtime_v2.control_plane.models import ControlPlaneConfig

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class ControlPlaneConfigError(Exception):
    pass


def _substitute_env(value: Any) -> Any:
    if isinstance(value, str):
        def _replace(match: re.Match[str]) -> str:
            env_name = match.group(1)
            env_value = os.environ.get(env_name)
            if not env_value:
                raise ControlPlaneConfigError(
                    f"Environment variable {env_name} referenced in telegram_control.yaml is not set"
                )
            return env_value

        return _ENV_PATTERN.sub(_replace, value)
    if isinstance(value, dict):
        return {key: _substitute_env(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_substitute_env(item) for item in value]
    return value


def load_control_plane_config(path: str) -> ControlPlaneConfig:
    config_path = Path(path)
    if not config_path.exists():
        raise ControlPlaneConfigError(f"Config file not found: {path}")

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ControlPlaneConfigError(f"Invalid YAML in {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ControlPlaneConfigError(
            f"Invalid telegram_control config: top-level YAML must be a mapping, got {type(raw).__name__}"
        )

    raw = _substitute_env(raw)

    token = raw.get("token")
    if not token:
        token_env = raw.get("token_env")
        if not token_env:
            raise ControlPlaneConfigError("Missing 'token' or 'token_env' in config")
        token = os.environ.get(token_env)
        if not token:
            raise ControlPlaneConfigError(
                f"Environment variable {token_env} (token_env) is not set"
            )
    raw["token"] = token

    if raw.get("delivery_mode") == "private_bot" and "topics" not in raw:
        raw["topics"] = {
            "commands": {"thread_id": None},
            "tech_log":  {"thread_id": None},
            "clean_log": {"thread_id": None},
        }

    try:
        return ControlPlaneConfig.model_validate(raw)
    except ValidationError as exc:
        raise ControlPlaneConfigError(f"Invalid telegram_control config: {exc}") from exc


__all__ = ["ControlPlaneConfigError", "load_control_plane_config"]
