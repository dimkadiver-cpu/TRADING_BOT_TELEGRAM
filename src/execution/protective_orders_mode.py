"""Runtime resolver for protective-order ownership."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


class ProtectiveOrdersMode(str, Enum):
    STRATEGY_MANAGED = "strategy_managed"
    EXCHANGE_MANAGER = "exchange_manager"


class ProtectiveOrderOwner(str, Enum):
    STRATEGY = "strategy"
    EXCHANGE_MANAGER = "exchange_manager"


@dataclass(frozen=True, slots=True)
class ProtectiveOrderOwnership:
    mode: ProtectiveOrdersMode
    stoploss_owner: ProtectiveOrderOwner
    take_profit_owner: ProtectiveOrderOwner


def resolve_protective_orders_mode(
    *,
    config: Mapping[str, Any] | None = None,
    env: Mapping[str, str] | None = None,
    persisted_mode: str | None = None,
) -> ProtectiveOrdersMode:
    """Resolve the protective-orders mode from persisted state, config, or env."""
    normalized = _normalize_mode(persisted_mode)
    if normalized is not None:
        return normalized

    normalized = _mode_from_config(config)
    if normalized is not None:
        return normalized

    normalized = _normalize_mode((env or os.environ).get("TELESIGNALBOT_PROTECTIVE_ORDERS_MODE"))
    if normalized is not None:
        return normalized

    return ProtectiveOrdersMode.STRATEGY_MANAGED


def resolve_protective_order_ownership(
    *,
    config: Mapping[str, Any] | None = None,
    env: Mapping[str, str] | None = None,
    persisted_mode: str | None = None,
) -> ProtectiveOrderOwnership:
    """Return the single logical owner for SL/TP according to the resolved mode."""
    mode = resolve_protective_orders_mode(config=config, env=env, persisted_mode=persisted_mode)
    owner = (
        ProtectiveOrderOwner.EXCHANGE_MANAGER
        if mode is ProtectiveOrdersMode.EXCHANGE_MANAGER
        else ProtectiveOrderOwner.STRATEGY
    )
    return ProtectiveOrderOwnership(
        mode=mode,
        stoploss_owner=owner,
        take_profit_owner=owner,
    )


def strategy_owns_stoploss(
    *,
    config: Mapping[str, Any] | None = None,
    env: Mapping[str, str] | None = None,
    persisted_mode: str | None = None,
) -> bool:
    ownership = resolve_protective_order_ownership(
        config=config,
        env=env,
        persisted_mode=persisted_mode,
    )
    return ownership.stoploss_owner is ProtectiveOrderOwner.STRATEGY


def strategy_owns_take_profit(
    *,
    config: Mapping[str, Any] | None = None,
    env: Mapping[str, str] | None = None,
    persisted_mode: str | None = None,
) -> bool:
    ownership = resolve_protective_order_ownership(
        config=config,
        env=env,
        persisted_mode=persisted_mode,
    )
    return ownership.take_profit_owner is ProtectiveOrderOwner.STRATEGY


def _mode_from_config(config: Mapping[str, Any] | None) -> ProtectiveOrdersMode | None:
    if not isinstance(config, Mapping):
        return None

    execution = config.get("execution")
    if isinstance(execution, Mapping):
        normalized = _normalize_mode(execution.get("protective_orders_mode"))
        if normalized is not None:
            return normalized

    return _normalize_mode(config.get("protective_orders_mode"))


def _normalize_mode(value: Any) -> ProtectiveOrdersMode | None:
    if isinstance(value, ProtectiveOrdersMode):
        return value
    if not isinstance(value, str):
        return None

    normalized = value.strip().lower()
    if normalized == ProtectiveOrdersMode.STRATEGY_MANAGED.value:
        return ProtectiveOrdersMode.STRATEGY_MANAGED
    if normalized == ProtectiveOrdersMode.EXCHANGE_MANAGER.value:
        return ProtectiveOrdersMode.EXCHANGE_MANAGER
    return None
