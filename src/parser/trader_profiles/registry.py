"""Registry for trader-specific parser profiles."""

from __future__ import annotations

from typing import Callable

from src.parser.trader_profiles.base import TraderProfileParser
from src.parser.trader_profiles.trader_a import TraderAProfileParser
from src.parser.trader_profiles.trader_b import TraderBProfileParser

_CANONICAL_TRADER_CODE = "trader_a"
_TRADER_ALIASES: dict[str, str] = {
    "ta": _CANONICAL_TRADER_CODE,
    "a": _CANONICAL_TRADER_CODE,
    "trader_a": _CANONICAL_TRADER_CODE,
    "tb": "trader_b",
    "b": "trader_b",
    "trader_b": "trader_b",
}

_PARSER_FACTORIES: dict[str, Callable[[], TraderProfileParser]] = {
    _CANONICAL_TRADER_CODE: TraderAProfileParser,
    "trader_b": TraderBProfileParser,
}


def canonicalize_trader_code(trader_code: str | None) -> str | None:
    if not isinstance(trader_code, str):
        return None
    key = trader_code.strip().lower()
    if not key:
        return None
    return _TRADER_ALIASES.get(key, key)


def get_profile_parser(trader_code: str) -> TraderProfileParser | None:
    canonical = canonicalize_trader_code(trader_code)
    if canonical is None:
        return None
    factory = _PARSER_FACTORIES.get(canonical)
    if factory is None:
        return None
    return factory()
