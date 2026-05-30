from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from src.runtime_v2.control_plane.models import RuntimeSnapshot, StartupMode

_BLOCKING_CONTROL_MODES = frozenset({"BLOCK_NEW_ENTRIES", "FULL_STOP"})


@dataclass(frozen=True)
class StartupPlan:
    mode: str
    apply_global_block: bool
    fell_back: bool = False
    message: str = ""


def _is_snapshot_stale(snapshot_at: datetime, *, max_age_seconds: int) -> bool:
    reference = snapshot_at
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    age_seconds = (
        datetime.now(timezone.utc) - reference.astimezone(timezone.utc)
    ).total_seconds()
    return age_seconds > max_age_seconds


def resolve_startup(
    *,
    mode: StartupMode,
    restore_max_age_seconds: int,
    latest_snapshot: RuntimeSnapshot | None,
) -> StartupPlan:
    if mode == "standby":
        return StartupPlan(
            mode="standby",
            apply_global_block=True,
            message="Startup mode standby: global block requested.",
        )

    if mode == "restore":
        if latest_snapshot is None:
            return StartupPlan(
                mode="auto",
                apply_global_block=False,
                fell_back=True,
                message="Restore requested but no runtime snapshot is available.",
            )
        if _is_snapshot_stale(
            latest_snapshot.snapshot_at,
            max_age_seconds=restore_max_age_seconds,
        ):
            return StartupPlan(
                mode="auto",
                apply_global_block=False,
                fell_back=True,
                message="Restore snapshot is stale; falling back to auto startup.",
            )
        return StartupPlan(
            mode="restore",
            apply_global_block=latest_snapshot.control_mode in _BLOCKING_CONTROL_MODES,
            message="Restore snapshot accepted.",
        )

    return StartupPlan(
        mode="auto",
        apply_global_block=False,
        message="Startup mode auto.",
    )


__all__ = ["StartupPlan", "resolve_startup"]
