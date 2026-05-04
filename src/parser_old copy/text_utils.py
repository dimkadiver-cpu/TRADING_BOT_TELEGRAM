"""Shared text helpers for parser components."""

from __future__ import annotations

import re


def normalize_text(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())
