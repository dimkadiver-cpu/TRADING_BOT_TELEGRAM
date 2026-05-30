from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.runtime_v2.control_plane.debug_controller import (
    DebugModeController,
    parse_duration,
)


def test_debug_controller_expires() -> None:
    ctrl = DebugModeController(max_seconds=600)
    now = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)

    ctrl.enable(duration_seconds=300, now=now)

    assert ctrl.is_active(now=now) is True
    assert ctrl.is_active(now=now + timedelta(seconds=301)) is False


def test_parse_duration_caps_to_max_seconds() -> None:
    assert parse_duration("20m", max_seconds=600) == 600
    assert parse_duration("5m", max_seconds=600) == 300
