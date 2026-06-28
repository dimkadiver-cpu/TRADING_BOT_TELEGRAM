from __future__ import annotations

import asyncio
import logging

from src.runtime_v2.symbols import to_raw_symbol

logger = logging.getLogger(__name__)

_USDT_SUFFIX = "USDT"


class SymbolRegistry:
    """Single shared symbol whitelist, refreshed periodically in background.

    One REST call regardless of how many adapters are configured — all Bybit
    adapters expose the same market catalog.  Starts fail-open (symbols=None)
    so the bot is never blocked waiting for the first load to complete.
    """

    def __init__(
        self,
        adapter,
        refresh_interval_seconds: int = 6 * 3600,
    ) -> None:
        self._adapter = adapter
        self._refresh_interval = refresh_interval_seconds
        self._symbols: frozenset[str] | None = None

    # ------------------------------------------------------------------
    # Public query API (thread-safe: frozenset assignment is atomic in CPython)
    # ------------------------------------------------------------------

    def symbol_exists(self, symbol: str) -> bool:
        if self._symbols is None:
            return True  # fail-open until first load
        lookup = to_raw_symbol(symbol) or symbol
        if lookup in self._symbols:
            return True
        return (lookup + _USDT_SUFFIX) in self._symbols

    def resolve_symbol(self, symbol: str) -> str:
        if self._symbols is None:
            return symbol
        lookup = to_raw_symbol(symbol) or symbol
        if lookup in self._symbols:
            return lookup
        usdt_form = lookup + _USDT_SUFFIX
        if usdt_form in self._symbols:
            return usdt_form
        return lookup

    @property
    def loaded(self) -> bool:
        return self._symbols is not None

    @property
    def symbol_count(self) -> int | None:
        s = self._symbols
        return len(s) if s is not None else None

    # ------------------------------------------------------------------
    # Background refresh task
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Asyncio task: load immediately, then refresh every interval."""
        while True:
            await self._refresh()
            await asyncio.sleep(self._refresh_interval)

    async def _refresh(self) -> None:
        try:
            symbols = await asyncio.to_thread(self._adapter.load_known_symbols)
        except Exception:
            logger.warning("symbol registry: refresh failed — keeping previous list", exc_info=True)
            return
        if symbols is None:
            logger.warning("symbol registry: adapter returned None — keeping previous list")
            return
        self._symbols = symbols
        logger.info("symbol registry: loaded %d symbols", len(symbols))


__all__ = ["SymbolRegistry"]
