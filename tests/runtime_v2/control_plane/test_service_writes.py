from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.runtime_v2.control_plane.service import RuntimeControlService
from src.runtime_v2.lifecycle.repositories import ControlStateRepository


def _apply_migrations(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    for migration in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(migration.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


@pytest.fixture
def ops_db(tmp_path):
    db_path = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db_path)
    return db_path


def _active_block_count(ops_db) -> int:
    conn = sqlite3.connect(ops_db)
    count = conn.execute(
        "SELECT COUNT(*) FROM ops_control_state "
        "WHERE active=1 AND execution_pause_mode='BLOCK_NEW_ENTRIES'"
    ).fetchone()[0]
    conn.close()
    return count


def test_pause_global_blocks_gate(ops_db):
    svc = RuntimeControlService(ops_db_path=ops_db)
    svc.pause(scope_value=None, created_by="42")
    mode = ControlStateRepository(ops_db).get_effective_mode(
        "main",
        "trader_a",
        "BTC/USDT",
        "LONG",
    )
    assert mode == "BLOCK_NEW_ENTRIES"


def test_pause_per_trader_uses_trader_scope(ops_db):
    svc = RuntimeControlService(ops_db_path=ops_db)
    svc.pause(scope_value="trader_a", created_by="42")
    repo = ControlStateRepository(ops_db)
    assert repo.get_effective_mode("main", "trader_a", "BTC/USDT", "LONG") == "BLOCK_NEW_ENTRIES"
    assert repo.get_effective_mode("main", "trader_b", "BTC/USDT", "LONG") == "NONE"


def test_pause_is_idempotent(ops_db):
    svc = RuntimeControlService(ops_db_path=ops_db)
    first = svc.pause(scope_value=None, created_by="42")
    second = svc.pause(scope_value=None, created_by="42")
    assert first.already_active is False
    assert second.already_active is True
    assert _active_block_count(ops_db) == 1


def test_resume_global(ops_db):
    svc = RuntimeControlService(ops_db_path=ops_db)
    svc.pause(scope_value=None, created_by="42")
    result = svc.resume(scope_value=None)
    assert result.had_block is True
    mode = ControlStateRepository(ops_db).get_effective_mode(
        "main",
        "trader_a",
        "BTC/USDT",
        "LONG",
    )
    assert mode == "NONE"


def test_resume_per_trader_only_that_trader(ops_db):
    svc = RuntimeControlService(ops_db_path=ops_db)
    svc.pause(scope_value="trader_a", created_by="42")
    svc.pause(scope_value="trader_b", created_by="42")
    svc.resume(scope_value="trader_a")
    repo = ControlStateRepository(ops_db)
    assert repo.get_effective_mode("main", "trader_a", "BTC/USDT", "LONG") == "NONE"
    assert repo.get_effective_mode("main", "trader_b", "BTC/USDT", "LONG") == "BLOCK_NEW_ENTRIES"


def test_resume_when_no_block(ops_db):
    svc = RuntimeControlService(ops_db_path=ops_db)
    result = svc.resume(scope_value=None)
    assert result.had_block is False


def test_start_clears_global_block(ops_db):
    svc = RuntimeControlService(ops_db_path=ops_db)
    svc.pause(scope_value=None, created_by="42")
    svc.start()
    assert _active_block_count(ops_db) == 0


def test_block_and_unblock_symbol_visible_in_control(ops_db):
    svc = RuntimeControlService(ops_db_path=ops_db)
    res = svc.block_symbol(scope_value=None, symbol="btcusdt", created_by="42")
    assert "BTCUSDT" in res.blacklist
    assert "BTCUSDT" in svc.get_control().blacklist_global
    res2 = svc.unblock_symbol(scope_value=None, symbol="BTCUSDT")
    assert "BTCUSDT" not in res2.blacklist
    assert "BTCUSDT" not in svc.get_control().blacklist_global


def test_block_symbol_per_trader(ops_db):
    svc = RuntimeControlService(ops_db_path=ops_db)
    svc.block_symbol(scope_value="trader_a", symbol="SOLUSDT", created_by="42")
    assert svc.get_control().blacklist_per_trader.get("trader_a") == ["SOLUSDT"]


def test_block_symbol_normalizes_slash_and_case_input(ops_db):
    svc = RuntimeControlService(ops_db_path=ops_db)
    res = svc.block_symbol(scope_value=None, symbol=" btc/usdt ", created_by="42")
    assert res.symbol == "BTCUSDT"
    assert res.blacklist == ["BTCUSDT"]
    assert svc.get_control().blacklist_global == ["BTCUSDT"]


def test_unblock_symbol_normalizes_equivalent_input_forms(ops_db):
    svc = RuntimeControlService(ops_db_path=ops_db)
    svc.block_symbol(scope_value=None, symbol="BTCUSDT", created_by="42")
    res = svc.unblock_symbol(scope_value=None, symbol="btc/usdt")
    assert res.symbol == "BTCUSDT"
    assert res.blacklist == []
    assert svc.get_control().blacklist_global == []


def test_block_symbol_base_asset_form_matches_equivalent_unblock_input(ops_db):
    svc = RuntimeControlService(ops_db_path=ops_db)
    res = svc.block_symbol(scope_value=None, symbol="btc", created_by="42")
    assert res.symbol == "BTC"
    assert svc.get_control().blacklist_global == ["BTC"]

    res2 = svc.unblock_symbol(scope_value=None, symbol="BTC/USDT")
    assert res2.symbol == "BTCUSDT"
    assert res2.blacklist == []
    assert svc.get_control().blacklist_global == []
