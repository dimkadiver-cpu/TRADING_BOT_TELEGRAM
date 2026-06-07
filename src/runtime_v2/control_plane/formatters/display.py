from __future__ import annotations


_KNOWN_QUOTES: tuple[str, ...] = (
    "USDT",
    "USDC",
    "BTC",
    "ETH",
)


def display_symbol(symbol: str | None) -> str:
    if symbol is None:
        return ""
    if not symbol:
        return symbol
    if "/" in symbol:
        return symbol
    upper = symbol.upper()
    for quote in _KNOWN_QUOTES:
        if upper.endswith(quote) and len(upper) > len(quote):
            base = upper[: -len(quote)]
            return f"{base}/{quote}"
    return symbol


def display_symbol_list(symbols: list[str] | tuple[str, ...]) -> list[str]:
    return [display_symbol(symbol) for symbol in symbols]


__all__ = ["display_symbol", "display_symbol_list"]
