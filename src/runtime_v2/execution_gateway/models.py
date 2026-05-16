# src/runtime_v2/execution_gateway/models.py
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, field_validator


class AdapterCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")
    place_entry: bool = True
    protective_stop_native: bool = False
    take_profit_native: bool = False
    bracket_order: bool = False
    move_stop: bool = False
    close_partial: bool = False
    close_full: bool = False
    executor_position: bool = False


class RetryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_attempts: int = 3
    backoff_seconds: list[int] = [30, 90, 300]


class TakeProfitConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    min_order_policy: str = "review"
    residual_policy: str = "assign_to_last_tp"


class PositionManagementConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    same_symbol_same_side_policy: str = "block"
    same_symbol_opposite_side_policy: str = "allow_if_hedge_mode"
    require_client_order_id_correlation: bool = True


class LiveSafetyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allow_live_trading: bool = False


class EntryExecutionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str = "b_entry_stop_then_tp"


class AdapterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str
    mode: str
    base_url: str
    connector: str
    leverage: int = 1
    secret: str | None = None          # Bearer token for execution adapter auth
    entry_execution: EntryExecutionConfig = EntryExecutionConfig()

    @field_validator("secret", mode="before")
    @classmethod
    def _coerce_empty_secret(cls, v: object) -> object:
        if v == "":
            return None
        return v
    retry: RetryConfig = RetryConfig()
    capabilities: AdapterCapabilities = AdapterCapabilities()
    take_profit: TakeProfitConfig = TakeProfitConfig()
    position_management: PositionManagementConfig = PositionManagementConfig()
    live_safety: LiveSafetyConfig = LiveSafetyConfig()


class AccountRoutingEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    adapter: str
    execution_account_id: str


class ExecutionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    default_adapter: str
    account_routing: dict[str, AccountRoutingEntry]
    adapters: dict[str, AdapterConfig]

    def resolve_routing(self, account_id: str) -> tuple[AccountRoutingEntry, AdapterConfig]:
        routing = self.account_routing.get(account_id) or self.account_routing["default"]
        adapter_cfg = self.adapters[routing.adapter]
        return routing, adapter_cfg


class RawAdapterOrder(BaseModel):
    model_config = ConfigDict(extra="ignore")
    client_order_id: str
    exchange_order_id: str | None = None
    adapter_order_id: str | None = None
    status: str  # OPEN | FILLED | CANCELLED | FAILED
    filled_qty: float = 0.0
    average_price: float | None = None

    @property
    def is_filled(self) -> bool:
        return self.status == "FILLED"


class AdapterResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    success: bool
    adapter_order_id: str | None = None
    exchange_order_id: str | None = None
    error: str | None = None
    reason: str | None = None
    warnings: list[str] = []


__all__ = [
    "AdapterCapabilities", "RetryConfig", "TakeProfitConfig",
    "PositionManagementConfig", "LiveSafetyConfig", "EntryExecutionConfig",
    "AdapterConfig", "AccountRoutingEntry", "ExecutionConfig",
    "RawAdapterOrder", "AdapterResult",
]
