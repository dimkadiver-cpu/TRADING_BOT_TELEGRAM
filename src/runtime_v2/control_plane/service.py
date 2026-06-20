# src/runtime_v2/control_plane/service.py
from __future__ import annotations

import sqlite3
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from src.runtime_v2.control_plane.debug_controller import DebugModeController
from src.runtime_v2.control_plane.emergency_close import EmergencyCloseService
from src.runtime_v2.control_plane.override_store import OverrideStore
from src.runtime_v2.control_plane.scope_resolver import QueryScope
from src.runtime_v2.control_plane.status_queries import (
    BlockedTradesView, ClosedTradesView, ControlView, HealthView,
    ReviewsView, StatusView, StatusQueries, PnlView, StatsView,
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

    def __init__(
        self,
        *,
        ops_db_path: str,
        log_path: str | None = None,
        debug_controller: DebugModeController | None = None,
    ) -> None:
        self._ops_db = ops_db_path
        self._queries = StatusQueries(ops_db_path)
        self._overrides = OverrideStore(ops_db_path)
        self._emergency = EmergencyCloseService(ops_db_path)
        self._start_time = time.time()
        self._log_path = log_path
        self._debug_controller = debug_controller

    # ── reads ───────────────────────────────────────────────────────────────
    def get_status(self, scope: QueryScope | None = None) -> StatusView:
        return self._queries.get_status(scope)

    def get_open_trades(self, scope: QueryScope | None = None) -> TradesView:
        return self._queries.get_open_trades(scope)

    def get_trade(self, chain_id: int) -> TradeDetail | None:
        return self._queries.get_trade(chain_id)

    def get_health(self) -> HealthView:
        return self._queries.get_health()

    def get_control(self, scope: QueryScope | None = None) -> ControlView:
        return self._queries.get_control(scope)

    def get_reviews(self, scope: QueryScope | None = None) -> ReviewsView:
        return self._queries.get_reviews(scope)

    def get_pnl(self, scope: QueryScope | None = None) -> PnlView:
        return self._queries.get_pnl(scope)

    def get_stats(self, scope: QueryScope) -> StatsView:
        return self._queries.get_stats(scope)

    def get_closed_trades(
        self,
        scope: QueryScope,
        page: int = 0,
        page_size: int = 5,
    ) -> ClosedTradesView:
        return self._queries.get_closed_trades(scope, page=page, page_size=page_size)

    def get_blocked_trades(self, scope: QueryScope) -> BlockedTradesView:
        return self._queries.get_blocked_trades(scope)

    def get_open_for_close(self, scope: QueryScope) -> list:
        return self._queries.get_open_for_close(scope)

    def get_waiting_for_cancel(self, scope: QueryScope) -> list:
        return self._queries.get_waiting_for_cancel(scope)

    def get_open_count_excluding_waiting(self, scope: QueryScope) -> int:
        return self._queries.get_open_count_excluding_waiting(scope)

    def execute_close(self, candidates: list, created_by: str) -> int:
        return self._emergency.execute_close(candidates, created_by)

    def execute_cancel(self, candidates: list, created_by: str) -> int:
        return self._emergency.execute_cancel(candidates, created_by)

    def get_logs(self, n: int = 20) -> list[str]:
        import os
        log_path = self._log_path or os.getenv("LOG_PATH", "logs/bot.log")
        try:
            p = Path(log_path)
            if not p.exists():
                return [f"Log file not found: {log_path}"]
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
            return lines[-n:] if len(lines) > n else lines
        except Exception as exc:
            return [f"Cannot read log: {exc}"]

    def enable_debug(self, *, duration_seconds: int) -> datetime:
        controller = self._debug_controller
        if controller is None:
            controller = DebugModeController(max_seconds=max(3600, duration_seconds, 1))
            self._debug_controller = controller
        return controller.enable(duration_seconds=duration_seconds)

    def disable_debug(self) -> None:
        if self._debug_controller is None:
            return
        self._debug_controller.disable()

    def debug_status(self) -> bool:
        if self._debug_controller is None:
            return False
        return self._debug_controller.is_active()

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

    def send_startup_notification(self) -> None:
        """Write TECH_LOG startup notification to outbox."""
        from src.runtime_v2.control_plane.outbox_writer import write_tech_log_event

        conn = sqlite3.connect(self._ops_db)
        try:
            with conn:
                write_tech_log_event(
                    conn,
                    notification_type="RUNTIME_STARTUP",
                    payload={
                        "level": "INFO",
                        "started_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                        "source": "runtime_main",
                    },
                    dedupe_key=f"startup:{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
                    priority="MEDIUM",
                )
        finally:
            conn.close()

    def send_shutdown_notification(self, *, reason: str = "SIGTERM") -> None:
        """Write TECH_LOG shutdown notification to outbox."""
        from src.runtime_v2.control_plane.outbox_writer import write_tech_log_event

        conn = sqlite3.connect(self._ops_db)
        try:
            with conn:
                open_chains = conn.execute(
                    "SELECT COUNT(*) FROM ops_trade_chains "
                    "WHERE lifecycle_state IN ('OPEN','PARTIALLY_CLOSED','WAITING_ENTRY')"
                ).fetchone()[0]
                pending_cmds = conn.execute(
                    "SELECT COUNT(*) FROM ops_execution_commands WHERE status='PENDING'"
                ).fetchone()[0]
                write_tech_log_event(
                    conn,
                    notification_type="RUNTIME_SHUTDOWN",
                    payload={
                        "level": "INFO",
                        "reason": reason,
                        "open_chains": open_chains,
                        "pending_commands": pending_cmds,
                        "source": "runtime_main",
                    },
                    dedupe_key=f"shutdown:{_now()}",
                    priority="HIGH",
                )
        finally:
            conn.close()


__all__ = [
    "BlockResult",
    "PauseResult",
    "ResumeResult",
    "RuntimeControlService",
    "UnblockResult",
    "VersionInfo",
    "QueryScope",
]
