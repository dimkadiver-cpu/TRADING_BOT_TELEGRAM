from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AccountStateSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: str
    equity_usdt: float | None = None
    available_balance_usdt: float | None = None
    total_open_risk_usdt: float | None = None
    total_margin_used_usdt: float | None = None
    captured_at: datetime
    source: str


class SymbolMarketSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    mark_price: float | None = None
    bid: float | None = None
    ask: float | None = None
    min_order_size: float | None = None
    price_precision: int | None = None
    qty_precision: int | None = None
    captured_at: datetime
    source: str


class OrderSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    side: str
    order_role: str
    status: str
    price: float | None = None
    qty: float | None = None
    filled_qty: float | None = None
    source_order_id: str | None = None


class PositionSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    side: str
    status: str
    entry_avg_price: float | None = None
    qty_open: float | None = None
    current_stop_price: float | None = None
    unrealized_pnl: float | None = None


class ExchangeDataPort(ABC):
    @abstractmethod
    def get_account_state(self, account_id: str) -> AccountStateSnapshot: ...

    @abstractmethod
    def get_symbol_market_state(self, account_id: str, symbol: str) -> SymbolMarketSnapshot: ...

    @abstractmethod
    def get_open_orders(self, account_id: str, symbol: str | None = None) -> list[OrderSnapshot]: ...

    @abstractmethod
    def get_open_position(self, account_id: str, symbol: str, side: str) -> PositionSnapshot | None: ...

    @abstractmethod
    def symbol_exists(self, account_id: str, symbol: str) -> bool:
        """Return False if the symbol is definitively unknown on this exchange.

        When the known-symbol list is unavailable (e.g. no exchange connection at startup),
        implementations must return True (fail-open) so signals are not incorrectly rejected.
        """
        ...

    def resolve_symbol(self, account_id: str, symbol: str) -> str:
        """Return the canonical exchange symbol (e.g. 'WLD' → 'WLDUSDT').

        Default: return symbol unchanged. Override in implementations that have
        a known-symbol list and can perform the bare-ticker → USDT mapping.
        """
        return symbol


__all__ = [
    "AccountStateSnapshot", "SymbolMarketSnapshot",
    "OrderSnapshot", "PositionSnapshot", "ExchangeDataPort",
]
