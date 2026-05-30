from __future__ import annotations

from datetime import datetime, timezone

_SEP = "----------------"


def _fmt_duration(duration_seconds: int) -> str:
    if duration_seconds % 3600 == 0:
        return f"{duration_seconds // 3600}h"
    if duration_seconds % 60 == 0:
        return f"{duration_seconds // 60}m"
    return f"{duration_seconds}s"


def _fmt_ts(value: datetime) -> str:
    current = value
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def format_debug_on(*, duration_seconds: int, expires_at: datetime) -> str:
    return "\n".join(
        [
            "DEBUG MODE ATTIVATO",
            _SEP,
            f"Durata: {_fmt_duration(duration_seconds)}",
            f"Scade: {_fmt_ts(expires_at)}",
        ]
    )


def format_debug_off() -> str:
    return "\n".join(
        [
            "DEBUG MODE DISATTIVATO",
            _SEP,
            "Il runtime torna alla policy standard dei log tecnici.",
        ]
    )


__all__ = ["format_debug_off", "format_debug_on"]
