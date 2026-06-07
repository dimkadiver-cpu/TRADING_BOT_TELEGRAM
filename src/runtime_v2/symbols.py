from __future__ import annotations


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
