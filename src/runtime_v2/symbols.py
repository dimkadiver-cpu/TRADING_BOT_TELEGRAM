from __future__ import annotations


_KNOWN_QUOTES: tuple[str, ...] = (
    "USDT",
    "USDC",
    "BTC",
    "ETH",
)


def to_raw_symbol(symbol: str | None) -> str | None:
    if symbol is None:
        return None

    normalized = symbol.strip().upper()
    if not normalized:
        return None
    if ":" in normalized:
        normalized = normalized.split(":", 1)[0]
    normalized = normalized.replace("/", "")
    return normalized or None


def symbol_base_asset(symbol: str | None) -> str | None:
    raw = to_raw_symbol(symbol)
    if raw is None:
        return None
    for quote in _KNOWN_QUOTES:
        if raw.endswith(quote) and len(raw) > len(quote):
            return raw[: -len(quote)]
    return raw


def symbols_equivalent(left: str | None, right: str | None) -> bool:
    raw_left = to_raw_symbol(left)
    raw_right = to_raw_symbol(right)
    if raw_left is None or raw_right is None:
        return False
    if raw_left == raw_right:
        return True
    return symbol_base_asset(raw_left) == symbol_base_asset(raw_right)


def symbol_matches_policy(configured_symbol: str | None, signal_symbol: str | None) -> bool:
    raw_configured = to_raw_symbol(configured_symbol)
    raw_signal = to_raw_symbol(signal_symbol)
    if raw_configured is None or raw_signal is None:
        return False
    if raw_configured == raw_signal:
        return True
    base_configured = symbol_base_asset(raw_configured)
    base_signal = symbol_base_asset(raw_signal)
    if base_configured is None or base_signal is None:
        return False
    if base_configured != base_signal:
        return False
    return raw_configured == base_configured or raw_signal == base_signal
