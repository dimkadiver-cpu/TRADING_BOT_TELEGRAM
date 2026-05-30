from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

_DURATION_RE = re.compile(r"^\s*(\d+)\s*([smhSMH]?)\s*$")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_duration(text: str | None, *, max_seconds: int = 3600) -> int:
    if text is None or not str(text).strip():
        return min(300, max_seconds)

    match = _DURATION_RE.match(text)
    if not match:
        return min(300, max_seconds)

    value = int(match.group(1))
    suffix = match.group(2).lower()
    multiplier = {"": 60, "s": 1, "m": 60, "h": 3600}.get(suffix, 60)
    seconds = max(1, value * multiplier)
    return min(seconds, max_seconds)


def is_valid_duration_arg(text: str | None) -> bool:
    if text is None:
        return True
    return _DURATION_RE.match(str(text)) is not None


class DebugModeController:
    def __init__(self, *, max_seconds: int) -> None:
        self._max_seconds = max_seconds
        self._expires_at: datetime | None = None

    def enable(
        self,
        *,
        duration_seconds: int,
        now: datetime | None = None,
    ) -> datetime:
        current = now or _utcnow()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        duration = min(max(1, duration_seconds), self._max_seconds)
        self._expires_at = current + timedelta(seconds=duration)
        return self._expires_at

    def disable(self) -> None:
        self._expires_at = None

    def is_active(self, *, now: datetime | None = None) -> bool:
        if self._expires_at is None:
            return False
        current = now or _utcnow()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        if current >= self._expires_at:
            self._expires_at = None
            return False
        return True


__all__ = ["DebugModeController", "is_valid_duration_arg", "parse_duration"]
