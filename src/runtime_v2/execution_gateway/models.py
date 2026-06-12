from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RetryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_attempts: int = 3
    backoff_seconds: list[int] = Field(default_factory=lambda: [30, 90, 300])


class LiveSafetyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allow_live_trading: bool = False


class WebsocketConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    poll_fallback_enabled: bool = True
    poll_fallback_period_seconds: int = 60
    position_reconciliation_interval_seconds: int = 600


class ExecutionStrategyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    simple_attached_enabled: bool = True
    trigger_by: Literal["MarkPrice", "LastPrice", "IndexPrice"] = "MarkPrice"
    one_tp_mode: Literal["FULL"] = "FULL"
    multi_tp_mode: Literal["PARTIAL"] = "PARTIAL"


class AdapterCapabilities(BaseModel):
    """Adapter runtime capability flags. Kept for adapter-layer compatibility."""
    model_config = ConfigDict(extra="forbid")
    place_entry: bool = True
    protective_stop_native: bool = False
    take_profit_native: bool = False
    bracket_order: bool = False
    move_stop: bool = False
    close_partial: bool = False
    close_full: bool = False
    executor_position: bool = False
    sync_protective_orders: bool = True


class AdapterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str
    mode: str
    connector: str
    api_key_env: str | None = None
    api_secret_env: str | None = None
    adjust_for_time_difference: bool = True
    recv_window_ms: int = 10000
    time_sync_on_startup: bool = True
    strategy: ExecutionStrategyConfig = Field(default_factory=ExecutionStrategyConfig)
    websocket: WebsocketConfig = Field(default_factory=WebsocketConfig)
    retry: RetryConfig = Field(default_factory=RetryConfig)
    live_safety: LiveSafetyConfig = Field(default_factory=LiveSafetyConfig)


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
        routing = self.account_routing.get(account_id) or self.account_routing.get("default")
        if routing is None:
            raise KeyError(
                f"No routing for account_id={account_id!r} and no 'default' fallback defined"
            )
        adapter_cfg = self.adapters[routing.adapter]
        return routing, adapter_cfg


class RawAdapterOrder(BaseModel):
    model_config = ConfigDict(extra="ignore")
    client_order_id: str
    exchange_order_id: str | None = None
    adapter_order_id: str | None = None
    status: str
    filled_qty: float = 0.0
    average_price: float | None = None
    cancel_reason: str | None = None
    exec_fee: float | None = None
    exec_value: float | None = None
    exchange_time: str | None = None
    leaves_qty: float | None = None
    cum_exec_qty: float | None = None

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
    warnings: list[str] = Field(default_factory=list)


class RawAdapterTrade(BaseModel):
    """A single reduceOnly fill returned by fetch_recent_reduce_trades()."""
    model_config = ConfigDict(extra="ignore")
    trade_id: str
    symbol: str          # Bybit raw format: PHAUSDT
    price: float
    amount: float
    reduce_only: bool = True
    fee: float | None = None
    fee_rate: float | None = None


class RawFundingExecution(BaseModel):
    """A funding fee execution returned by fetch_recent_funding_executions()."""
    model_config = ConfigDict(extra="ignore")
    exec_id: str
    symbol: str          # Bybit raw format: ONDOUSDT
    side: str            # Bybit position side: "Buy" = LONG, "Sell" = SHORT
    exec_fee: float      # positive = funding paid, negative = funding received
    exchange_time: str | None = None


class RawPositionDetails(BaseModel):
    """Position snapshot from fetch_position_details()."""
    model_config = ConfigDict(extra="ignore")
    symbol: str          # Bybit raw format
    side: str            # LONG | SHORT
    qty: float
    take_profit: float | None = None   # None = field unavailable; 0.0 = not set on exchange
    stop_loss: float | None = None


class RawAccountSnapshot(BaseModel):
    """Normalized account-level snapshot returned by execution adapters."""
    model_config = ConfigDict(extra="ignore")
    equity_usdt: float | None = None
    available_balance_usdt: float | None = None
    total_open_risk_usdt: float | None = None
    total_margin_used_usdt: float | None = None
    payload: dict = Field(default_factory=dict)
    source: str


class RawMarketSnapshot(BaseModel):
    """Normalized symbol market snapshot returned by execution adapters."""
    model_config = ConfigDict(extra="ignore")
    symbol: str
    mark_price: float | None = None
    bid: float | None = None
    ask: float | None = None
    min_order_size: float | None = None
    price_precision: int | None = None
    qty_precision: int | None = None
    payload: dict = Field(default_factory=dict)
    source: str


__all__ = [
    "RetryConfig", "LiveSafetyConfig", "WebsocketConfig",
    "ExecutionStrategyConfig",
    "AdapterConfig", "AccountRoutingEntry", "ExecutionConfig",
    "RawAdapterOrder", "RawAdapterTrade", "RawPositionDetails", "AdapterResult",
    "RawAccountSnapshot", "RawMarketSnapshot",
]
