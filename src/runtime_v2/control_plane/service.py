# src/runtime_v2/control_plane/service.py
from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass

from src.runtime_v2.control_plane.status_queries import (
    ControlView, HealthView, ReviewsView, StatusView, StatusQueries,
    TradeDetail, TradesView,
)

_START_TIME = time.time()


@dataclass
class VersionInfo:
    runtime: str
    commit: str
    branch: str
    uptime_seconds: int


def _git(args: list[str]) -> str:
    try:
        out = subprocess.run(
            ["git", *args], capture_output=True, text=True, timeout=5, check=False
        )
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


class RuntimeControlService:
    """Single entry point for the bot to read/write ops state.

    Part 3 implements read methods; Part 4 adds pause/resume/block/unblock/start.
    """

    def __init__(self, *, ops_db_path: str) -> None:
        self._ops_db = ops_db_path
        self._queries = StatusQueries(ops_db_path)

    # ── reads ───────────────────────────────────────────────────────────────
    def get_status(self) -> StatusView:
        return self._queries.get_status()

    def get_open_trades(self) -> TradesView:
        return self._queries.get_open_trades()

    def get_trade(self, chain_id: int) -> TradeDetail | None:
        return self._queries.get_trade(chain_id)

    def get_health(self) -> HealthView:
        return self._queries.get_health()

    def get_control(self) -> ControlView:
        return self._queries.get_control()

    def get_reviews(self) -> ReviewsView:
        return self._queries.get_reviews()

    def get_version(self) -> VersionInfo:
        return VersionInfo(
            runtime="v2",
            commit=_git(["rev-parse", "--short", "HEAD"]),
            branch=_git(["rev-parse", "--abbrev-ref", "HEAD"]),
            uptime_seconds=int(time.time() - _START_TIME),
        )


__all__ = ["RuntimeControlService", "VersionInfo"]
