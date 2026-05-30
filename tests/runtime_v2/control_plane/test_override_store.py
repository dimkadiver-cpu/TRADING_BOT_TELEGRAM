from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.runtime_v2.control_plane.override_store import OverrideStore


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


def test_add_global_symbol(ops_db):
    store = OverrideStore(ops_db)
    result = store.add_symbol(
        scope_type="GLOBAL",
        scope_value=None,
        symbol="BTCUSDT",
        created_by="42",
    )
    assert result == ["BTCUSDT"]
    assert store.get_blacklist("GLOBAL", None) == ["BTCUSDT"]


def test_add_is_idempotent(ops_db):
    store = OverrideStore(ops_db)
    store.add_symbol(
        scope_type="GLOBAL",
        scope_value=None,
        symbol="BTCUSDT",
        created_by="42",
    )
    result = store.add_symbol(
        scope_type="GLOBAL",
        scope_value=None,
        symbol="BTCUSDT",
        created_by="42",
    )
    assert result == ["BTCUSDT"]


def test_add_multiple_and_per_trader(ops_db):
    store = OverrideStore(ops_db)
    store.add_symbol(
        scope_type="GLOBAL",
        scope_value=None,
        symbol="BTCUSDT",
        created_by="42",
    )
    store.add_symbol(
        scope_type="GLOBAL",
        scope_value=None,
        symbol="ETHUSDT",
        created_by="42",
    )
    store.add_symbol(
        scope_type="PER_TRADER",
        scope_value="trader_a",
        symbol="SOLUSDT",
        created_by="42",
    )
    assert set(store.get_blacklist("GLOBAL", None)) == {"BTCUSDT", "ETHUSDT"}
    assert store.get_blacklist("PER_TRADER", "trader_a") == ["SOLUSDT"]


def test_remove_symbol(ops_db):
    store = OverrideStore(ops_db)
    store.add_symbol(
        scope_type="GLOBAL",
        scope_value=None,
        symbol="BTCUSDT",
        created_by="42",
    )
    store.add_symbol(
        scope_type="GLOBAL",
        scope_value=None,
        symbol="ETHUSDT",
        created_by="42",
    )
    result = store.remove_symbol(
        scope_type="GLOBAL",
        scope_value=None,
        symbol="BTCUSDT",
    )
    assert result == ["ETHUSDT"]


def test_remove_missing_symbol_is_noop(ops_db):
    store = OverrideStore(ops_db)
    result = store.remove_symbol(
        scope_type="GLOBAL",
        scope_value=None,
        symbol="NOPEUSDT",
    )
    assert result == []
