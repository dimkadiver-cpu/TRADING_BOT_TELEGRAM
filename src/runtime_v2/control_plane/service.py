# src/runtime_v2/control_plane/service.py
from __future__ import annotations

import sqlite3
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from src.runtime_v2.control_plane.override_store import OverrideStore
from src.runtime_v2.control_plane.status_queries import (
    ControlView, HealthView, ReviewsView, StatusView, StatusQueries,
    TradeDetail, TradesView,
)

@dataclass
class VersionInfo:
    runtime: str
    commit: str
    branch: str
    uptime_seconds: int


@dataclass
class PauseResult:
    scope_type: str
    scope_value: str | None
    mode: str
    already_active: bool


@dataclass
class ResumeResult:
    scope_type: str
    scope_value: str | None
    had_block: bool


@dataclass
class BlockResult:
    scope_type: str
    scope_value: str | None
    symbol: str
    blacklist: list[str]


@dataclass
class UnblockResult:
    scope_type: str
    scope_value: str | None
    symbol: str
    blacklist: list[str]


def _git(args: list[str]) -> str:
    try:
        out = subprocess.run(
            ["git", *args], capture_output=True, text=True, timeout=5, check=False
        )
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RuntimeControlService:
    """Single entry point for the bot to read/write ops state.

    Part 3 implements read methods; Part 4 adds pause/resume/block/unblock/start.
    """

    def __init__(self, *, ops_db_path: str) -> None:
        self._ops_db = ops_db_path
        self._queries = StatusQueries(ops_db_path)
        self._overrides = OverrideStore(ops_db_path)
        self._start_time = time.time()

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

    def get_logs(self, n: int = 20) -> list[str]:
        import os
        from pathlib import Path
        log_path = os.getenv("LOG_PATH", "logs/bot.log")
        try:
            p = Path(log_path)
            if not p.exists():
                return [f"Log file not found: {log_path}"]
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
            return lines[-n:] if len(lines) > n else lines
        except Exception as exc:
            return [f"Cannot read log: {exc}"]

    def get_version(self) -> VersionInfo:
        return VersionInfo(
            runtime="v2",
            commit=_git(["rev-parse", "--short", "HEAD"]),
            branch=_git(["rev-parse", "--abbrev-ref", "HEAD"]),
            uptime_seconds=int(time.time() - self._start_time),
        )

    def pause(self, *, scope_value: str | None, created_by: str) -> PauseResult:
        scope_type = "GLOBAL" if scope_value is None else "TRADER"
        now = _now()
        conn = sqlite3.connect(self._ops_db)
        try:
            with conn:
                if scope_value is None:
                    existing = conn.execute(
                        "SELECT 1 FROM ops_control_state WHERE active=1 "
                        "AND scope_type='GLOBAL' AND scope_value IS NULL "
                        "AND execution_pause_mode='BLOCK_NEW_ENTRIES'"
                    ).fetchone()
                else:
                    existing = conn.execute(
                        "SELECT 1 FROM ops_control_state WHERE active=1 "
                        "AND scope_type='TRADER' AND scope_value=? "
                        "AND execution_pause_mode='BLOCK_NEW_ENTRIES'",
                        (scope_value,),
                    ).fetchone()
                already_active = existing is not None
                if not already_active:
                    conn.execute(
                        "INSERT INTO ops_control_state "
                        "(scope_type, scope_value, execution_pause_mode, reason, "
                        "created_by, active, created_at, updated_at) "
                        "VALUES (?, ?, 'BLOCK_NEW_ENTRIES', 'telegram:/pause', ?, 1, ?, ?)",
                        (scope_type, scope_value, created_by, now, now),
                    )
        finally:
            conn.close()
        return PauseResult(
            scope_type=scope_type,
            scope_value=scope_value,
            mode="BLOCK_NEW_ENTRIES",
            already_active=already_active,
        )

    def resume(self, *, scope_value: str | None) -> ResumeResult:
        scope_type = "GLOBAL" if scope_value is None else "TRADER"
        now = _now()
        conn = sqlite3.connect(self._ops_db)
        try:
            with conn:
                if scope_value is None:
                    cur = conn.execute(
                        "UPDATE ops_control_state SET active=0, updated_at=? "
                        "WHERE active=1 AND scope_type='GLOBAL' AND scope_value IS NULL "
                        "AND execution_pause_mode IN ('BLOCK_NEW_ENTRIES','FULL_STOP')",
                        (now,),
                    )
                else:
                    cur = conn.execute(
                        "UPDATE ops_control_state SET active=0, updated_at=? "
                        "WHERE active=1 AND scope_type='TRADER' AND scope_value=? "
                        "AND execution_pause_mode IN ('BLOCK_NEW_ENTRIES','FULL_STOP')",
                        (now, scope_value),
                    )
                had_block = cur.rowcount > 0
        finally:
            conn.close()
        return ResumeResult(
            scope_type=scope_type,
            scope_value=scope_value,
            had_block=had_block,
        )

    def start(self) -> ResumeResult:
        return self.resume(scope_value=None)

    def block_symbol(
        self, *, scope_value: str | None, symbol: str, created_by: str
    ) -> BlockResult:
        scope_type = "GLOBAL" if scope_value is None else "PER_TRADER"
        blacklist = self._overrides.add_symbol(
            scope_type=scope_type,
            scope_value=scope_value,
            symbol=symbol,
            created_by=created_by,
        )
        return BlockResult(
            scope_type=scope_type,
            scope_value=scope_value,
            symbol=symbol.upper(),
            blacklist=blacklist,
        )

    def unblock_symbol(self, *, scope_value: str | None, symbol: str) -> UnblockResult:
        scope_type = "GLOBAL" if scope_value is None else "PER_TRADER"
        blacklist = self._overrides.remove_symbol(
            scope_type=scope_type,
            scope_value=scope_value,
            symbol=symbol,
        )
        return UnblockResult(
            scope_type=scope_type,
            scope_value=scope_value,
            symbol=symbol.upper(),
            blacklist=blacklist,
        )


__all__ = [
    "BlockResult",
    "PauseResult",
    "ResumeResult",
    "RuntimeControlService",
    "UnblockResult",
    "VersionInfo",
]
