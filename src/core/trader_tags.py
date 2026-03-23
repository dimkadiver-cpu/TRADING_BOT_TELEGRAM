"""Trader tag extraction and normalization helpers."""

from __future__ import annotations

import re

_CONTAINER_RE = re.compile(r"^\[\s*(.*?)\s*\]$")
_SPACE_AROUND_HASH_RE = re.compile(r"\s*#\s*")
_BRACKETED_TRADER_TAG_RE = re.compile(r"(?i)\btrade[rt]\s*\[\s*#\s*([A-Za-z0-9\u0400-\u04FF]+)\s*\]")
_TRADER_WORD_TYPO_RE = re.compile(r"(?i)\btradet(?=\s*#)")
_TRADER_TAG_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:\[\s*)?trade[rt](?:\s*\[\s*)?\s*#\s*([A-Za-z0-9\u0400-\u04FF]+)(?:\s*\])?(?![A-Za-z0-9_])",
    re.IGNORECASE,
)

_CONFUSABLES = str.maketrans(
    {
        "А": "a",
        "а": "a",
        "В": "b",
        "в": "b",
        "С": "c",
        "с": "c",
        "Д": "d",
        "д": "d",
    }
)


def normalize_trader_tag(tag: str | None) -> str | None:
    """Normalize trader tag to canonical form: trader#x."""
    if tag is None:
        return None

    normalized = tag.strip()
    if not normalized:
        return None

    container_match = _CONTAINER_RE.match(normalized)
    if container_match:
        normalized = container_match.group(1).strip()

    normalized = normalized.lower().translate(_CONFUSABLES)
    normalized = _BRACKETED_TRADER_TAG_RE.sub(r"trader#\1", normalized)
    normalized = _TRADER_WORD_TYPO_RE.sub("trader", normalized)
    normalized = _SPACE_AROUND_HASH_RE.sub("#", normalized)
    normalized = normalized.strip()
    return normalized or None


def find_normalized_trader_tags(text: str | None) -> list[str]:
    """Find all trader tags in text and return normalized forms."""
    if not text:
        return []

    found: list[str] = []
    for match in _TRADER_TAG_RE.finditer(text):
        normalized = normalize_trader_tag(f"trader#{match.group(1)}")
        if normalized:
            found.append(normalized)
    return found


def first_normalized_trader_tag(text: str | None) -> str | None:
    tags = find_normalized_trader_tags(text)
    return tags[0] if tags else None


def normalize_trader_aliases(aliases: dict[str, str]) -> dict[str, str]:
    """Normalize alias keys with the same trader-tag normalization logic."""
    normalized_aliases: dict[str, str] = {}
    for alias, trader_id in aliases.items():
        normalized_alias = normalize_trader_tag(alias)
        if not normalized_alias:
            continue
        normalized_aliases[normalized_alias] = trader_id.strip()
    return normalized_aliases
