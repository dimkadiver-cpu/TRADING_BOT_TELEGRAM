from __future__ import annotations

from src.parser_v2.core.runtime import TraderParserProfile
from src.parser_v2.profiles.trader_a.profile import TraderAProfile

_PROFILE_FACTORIES: dict[str, type] = {
    "trader_a": TraderAProfile,
}

_ALIASES: dict[str, str] = {
    "ta": "trader_a",
    "a": "trader_a",
    "trader_a": "trader_a",
}


def canonicalize_trader_v2(value: str | None) -> str | None:
    if value is None:
        return None
    return _ALIASES.get(value.strip().lower())


def get_parser_v2_profile(trader_id: str) -> TraderParserProfile:
    canonical = canonicalize_trader_v2(trader_id)
    if canonical is None or canonical not in _PROFILE_FACTORIES:
        raise KeyError(f"Unknown parser_v2 trader: {trader_id!r}")
    return _PROFILE_FACTORIES[canonical]()


def list_parser_v2_profiles() -> list[str]:
    return sorted(_PROFILE_FACTORIES.keys())


__all__ = ["canonicalize_trader_v2", "get_parser_v2_profile", "list_parser_v2_profiles"]
