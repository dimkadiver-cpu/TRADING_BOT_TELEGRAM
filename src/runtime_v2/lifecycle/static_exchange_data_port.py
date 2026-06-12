from __future__ import annotations

from datetime import datetime, timezone

from src.runtime_v2.lifecycle.ports import (
    AccountStateSnapshot, ExchangeDataPort, OrderSnapshot,
    PositionSnapshot, SymbolMarketSnapshot,
)
from src.runtime_v2.symbols import to_raw_symbol


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
        self._markets: dict[str, SymbolMarketSnapshot] = {}
        for key, snapshot in (market_snapshots or {}).items():
            normalized_symbol = to_raw_symbol(key) or key
            snapshot_symbol = to_raw_symbol(snapshot.symbol) or normalized_symbol
            self._markets[normalized_symbol] = snapshot.model_copy(update={"symbol": snapshot_symbol})
        self._orders: list[OrderSnapshot] = orders or []
        self._positions: list[PositionSnapshot] = positions or []
        self._known_symbols = (
            frozenset((to_raw_symbol(symbol) or symbol) for symbol in known_symbols)
            if known_symbols is not None
            else None
        )

    def get_account_state(self, account_id: str) -> AccountStateSnapshot:
        if self._account is not None:
            return self._account
        return AccountStateSnapshot(
            account_id=account_id,
            captured_at=datetime.now(timezone.utc),
            source="static_default",
            payload_json="{}",
        )

    def get_symbol_market_state(self, account_id: str, symbol: str) -> SymbolMarketSnapshot:
        lookup_symbol = to_raw_symbol(symbol) or symbol
        if lookup_symbol in self._markets:
            return self._markets[lookup_symbol]
        return SymbolMarketSnapshot(
            symbol=lookup_symbol,
            captured_at=datetime.now(timezone.utc),
            source="static_default",
            payload_json="{}",
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
            return True  # fail-open: no symbol list loaded -> don't block signals
        lookup_symbol = to_raw_symbol(symbol) or symbol
        if lookup_symbol in self._known_symbols:
            return True
        # Bare symbols from Telegram messages (e.g. "HYPE") may match USDT-quoted perpetuals ("HYPEUSDT")
        return (lookup_symbol + "USDT") in self._known_symbols

    def resolve_symbol(self, account_id: str, symbol: str) -> str:
        if self._known_symbols is None:
            return symbol
        lookup = to_raw_symbol(symbol) or symbol
        if lookup in self._known_symbols:
            return lookup
        usdt_form = lookup + "USDT"
        if usdt_form in self._known_symbols:
            return usdt_form
        return lookup


__all__ = ["StaticExchangeDataPort"]
