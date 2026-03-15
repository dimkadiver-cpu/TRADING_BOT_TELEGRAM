"""Shared helpers for trader profile parsing."""

from __future__ import annotations

import re

_LINK_RE = re.compile(r"(?:https?://)?t\.me/(?:c/\d+|[A-Za-z0-9_]+)/\d+", re.IGNORECASE)
_HASHTAG_RE = re.compile(r"#([A-Za-z0-9_]{2,64})")


def normalize_text(text: str) -> str:
    return (text or "").strip().lower()


def split_lines(text: str) -> list[str]:
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def extract_telegram_links(text: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for match in _LINK_RE.finditer(text or ""):
        value = match.group(0)
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def extract_hashtags(text: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for match in _HASHTAG_RE.finditer(text or ""):
        value = match.group(1)
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out
