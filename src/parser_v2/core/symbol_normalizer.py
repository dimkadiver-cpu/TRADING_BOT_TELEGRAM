from __future__ import annotations


def normalize_symbol(symbol: str | None) -> str | None:
    if symbol is None:
        return None

    normalized = symbol.strip().upper()
    if not normalized:
        return None

    if normalized.endswith(".P"):
        return normalized[:-2]

    return normalized
