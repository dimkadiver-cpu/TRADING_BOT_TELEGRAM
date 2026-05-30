from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.runtime_v2.control_plane.audit_store import CommandAuditStore
from src.runtime_v2.control_plane.auth import AuthValidator
from src.runtime_v2.control_plane.debug_controller import DebugModeController
from src.runtime_v2.control_plane.models import (
    CleanLogConfig, ControlPlaneConfig, TechLogConfig, TopicConfig, TopicsConfig,
)
from src.runtime_v2.control_plane.service import RuntimeControlService
from src.runtime_v2.control_plane.telegram_bot import CommandRouter


def _apply_migrations(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    for f in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def ops_db(tmp_path):
    db_path = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db_path)
    return db_path


def _config():
    return ControlPlaneConfig(
        token="t",
        chat_id=-100999,
        topics=TopicsConfig(
            commands=TopicConfig(thread_id=101),
            tech_log=TechLogConfig(thread_id=102),
            clean_log=CleanLogConfig(thread_id=103),
        ),
        authorized_users=[42],
    )


def _service(ops_db: str) -> RuntimeControlService:
    return RuntimeControlService(
        ops_db_path=ops_db,
        debug_controller=DebugModeController(max_seconds=3600),
    )


def _router(ops_db: str, *, service: RuntimeControlService | None = None) -> CommandRouter:
    cfg = _config()
    return CommandRouter(
        config=cfg,
        auth=AuthValidator(cfg),
        audit=CommandAuditStore(ops_db),
        service=service or _service(ops_db),
    )


def _add_chain(conn: sqlite3.Connection, cid: int, state: str) -> None:
    now = _now()
    conn.execute(
        "INSERT INTO ops_trade_chains "
        "(trade_chain_id, source_enrichment_id, canonical_message_id, raw_message_id, "
        " trader_id, account_id, symbol, side, lifecycle_state, entry_mode, "
        " management_plan_json, risk_snapshot_json, plan_state_json, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (cid, cid, cid, cid, "trader_a", "main", "BTC/USDT", "LONG", state, "ONE_SHOT", "{}", "{}", "{}", now, now),
    )


def test_pnl_command_returns_structured_reply(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _add_chain(conn, 1, "OPEN")
        conn.execute(
            "INSERT INTO ops_account_snapshots "
            "(account_id, equity_usdt, available_balance_usdt, total_open_risk_usdt, "
            " total_margin_used_usdt, source, captured_at, payload_json) "
            "VALUES ('main', 1250.5, 980.25, 40.0, 120.0, 'sync', ?, '{}')",
            (_now(),),
        )
    conn.close()

    router = _router(ops_db)
    res = router.route(
        command_text="/pnl",
        message_id=30,
        chat_id=-100999,
        thread_id=101,
        user_id=42,
        username="op",
    )
    assert res.decision == "EXECUTED"
    assert "PNL" in res.reply_text.upper()
    assert "1250.5" in res.reply_text
    assert "n/a" in res.reply_text.lower()


def test_debug_on_activates_controller(ops_db):
    service = _service(ops_db)
    router = _router(ops_db, service=service)

    res = router.route(
        command_text="/debug_on 5m",
        message_id=31,
        chat_id=-100999,
        thread_id=101,
        user_id=42,
        username="op",
    )

    assert res.decision == "EXECUTED"
    assert "DEBUG MODE ATTIVATO" in res.reply_text
    assert service.debug_status() is True


def test_debug_off_disables_controller(ops_db):
    service = _service(ops_db)
    service.enable_debug(duration_seconds=300)
    router = _router(ops_db, service=service)

    res = router.route(
        command_text="/debug_off",
        message_id=32,
        chat_id=-100999,
        thread_id=101,
        user_id=42,
        username="op",
    )

    assert res.decision == "EXECUTED"
    assert "DEBUG MODE DISATTIVATO" in res.reply_text
    assert service.debug_status() is False


def test_debug_on_rejects_invalid_duration_argument(ops_db):
    router = _router(ops_db)

    res = router.route(
        command_text="/debug_on foo",
        message_id=34,
        chat_id=-100999,
        thread_id=101,
        user_id=42,
        username="op",
    )

    assert res.decision == "REJECTED"
    assert res.reply_text == "Usage: /debug_on [5m|30m|1h]"


def test_debug_on_rejects_extra_arguments(ops_db):
    router = _router(ops_db)

    res = router.route(
        command_text="/debug_on 5m extra",
        message_id=35,
        chat_id=-100999,
        thread_id=101,
        user_id=42,
        username="op",
    )

    assert res.decision == "REJECTED"
    assert res.reply_text == "Usage: /debug_on [5m|30m|1h]"


def test_service_debug_without_injected_controller_uses_default_cap(ops_db):
    service = RuntimeControlService(ops_db_path=ops_db)

    service.enable_debug(duration_seconds=300)
    expires_at = service.enable_debug(duration_seconds=1800)

    now = datetime.now(timezone.utc)
    assert int((expires_at - now).total_seconds()) > 1500


def test_logs_command_clamps_requested_lines(ops_db, tmp_path):
    log_path = tmp_path / "runtime.log"
    log_path.write_text("\n".join(f"line {idx}" for idx in range(1, 6)), encoding="utf-8")
    service = RuntimeControlService(
        ops_db_path=ops_db,
        log_path=str(log_path),
        debug_controller=DebugModeController(max_seconds=3600),
    )
    router = _router(ops_db, service=service)

    res = router.route(
        command_text="/logs 2",
        message_id=33,
        chat_id=-100999,
        thread_id=101,
        user_id=42,
        username="op",
    )

    assert res.decision == "EXECUTED"
    assert "line 4" in res.reply_text
    assert "line 5" in res.reply_text
    assert "line 1" not in res.reply_text
