from __future__ import annotations

from src.parser_v2.profiles.trader_a.profile import TraderAProfile

_PROFILE_FACTORIES: dict[str, type] = {
    "trader_a": TraderAProfile,
    "ta": TraderAProfile,
    "a": TraderAProfile,
}

_CANONICAL_NAMES: frozenset[str] = frozenset({"trader_a"})


def canonicalize_trader_v2(value: str | None) -> str | None:
    if value is None:
        return None
    key = value.strip().lower()
    factory = _PROFILE_FACTORIES.get(key)
    if factory is None:
        return None
    for canonical in _CANONICAL_NAMES:
        if _PROFILE_FACTORIES.get(canonical) is factory:
            return canonical
    return None


def get_parser_v2_profile(trader_id: str):
    key = trader_id.strip().lower()
    factory = _PROFILE_FACTORIES.get(key)
    if factory is None:
        raise KeyError(f"Unknown parser_v2 trader: {trader_id!r}")
    return factory()


def list_parser_v2_profiles() -> list[str]:
    return sorted(_CANONICAL_NAMES)


__all__ = ["canonicalize_trader_v2", "get_parser_v2_profile", "list_parser_v2_profiles"]
