from __future__ import annotations

from datetime import datetime, timezone

from src.runtime_v2.lifecycle.ports import (
    AccountStateSnapshot, ExchangeDataPort, OrderSnapshot,
    PositionSnapshot, SymbolMarketSnapshot,
)


class StaticExchangeDataPort(ExchangeDataPort):
    def __init__(
        self,
        account_snapshot: AccountStateSnapshot | None = None,
        market_snapshots: dict[str, SymbolMarketSnapshot] | None = None,
        orders: list[OrderSnapshot] | None = None,
        positions: list[PositionSnapshot] | None = None,
        known_symbols: frozenset[str] | None = None,
    ) -> None:
        self._account = account_snapshot
        self._markets: dict[str, SymbolMarketSnapshot] = market_snapshots or {}
        self._orders: list[OrderSnapshot] = orders or []
        self._positions: list[PositionSnapshot] = positions or []
        self._known_symbols = known_symbols

    def get_account_state(self, account_id: str) -> AccountStateSnapshot:
        if self._account is not None:
            return self._account
        return AccountStateSnapshot(
            account_id=account_id,
            captured_at=datetime.now(timezone.utc),
            source="static_default",
        )

    def get_symbol_market_state(self, account_id: str, symbol: str) -> SymbolMarketSnapshot:
        if symbol in self._markets:
            return self._markets[symbol]
        return SymbolMarketSnapshot(
            symbol=symbol,
            captured_at=datetime.now(timezone.utc),
            source="static_default",
        )

    def get_open_orders(self, account_id: str, symbol: str | None = None) -> list[OrderSnapshot]:
        if symbol is None:
            return list(self._orders)
        return [o for o in self._orders if o.symbol == symbol]

    def get_open_position(self, account_id: str, symbol: str, side: str) -> PositionSnapshot | None:
        for p in self._positions:
            if p.symbol == symbol and p.side == side:
                return p
        return None

    def symbol_exists(self, account_id: str, symbol: str) -> bool:
        if self._known_symbols is None:
            return True  # fail-open: no symbol list loaded → don't block signals
        return symbol in self._known_symbols


__all__ = ["StaticExchangeDataPort"]
