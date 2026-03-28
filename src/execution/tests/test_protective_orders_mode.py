from __future__ import annotations

import sqlite3
from pathlib import Path

from src.core.migrations import apply_migrations
from src.execution.protective_orders_mode import (
    ProtectiveOrderOwner,
    ProtectiveOrdersMode,
    resolve_protective_order_ownership,
    resolve_protective_orders_mode,
)


def _make_db(tmp_path: Path) -> str:
    db_path = str(tmp_path / "protective_orders_mode.sqlite3")
    apply_migrations(db_path=db_path, migrations_dir=str(Path("db/migrations").resolve()))
    return db_path


def test_resolve_protective_orders_mode_defaults_to_strategy_managed() -> None:
    assert resolve_protective_orders_mode() is ProtectiveOrdersMode.STRATEGY_MANAGED


def test_resolve_protective_orders_mode_reads_nested_execution_config() -> None:
    mode = resolve_protective_orders_mode(
        config={"execution": {"protective_orders_mode": "exchange_manager"}}
    )

    assert mode is ProtectiveOrdersMode.EXCHANGE_MANAGER


def test_resolve_protective_order_ownership_switches_to_exchange_manager() -> None:
    ownership = resolve_protective_order_ownership(
        config={"execution": {"protective_orders_mode": "exchange_manager"}}
    )

    assert ownership.mode is ProtectiveOrdersMode.EXCHANGE_MANAGER
    assert ownership.stoploss_owner is ProtectiveOrderOwner.EXCHANGE_MANAGER
    assert ownership.take_profit_owner is ProtectiveOrderOwner.EXCHANGE_MANAGER


def test_resolve_protective_order_ownership_is_backward_compatible_without_flag() -> None:
    ownership = resolve_protective_order_ownership(config={})

    assert ownership.mode is ProtectiveOrdersMode.STRATEGY_MANAGED
    assert ownership.stoploss_owner is ProtectiveOrderOwner.STRATEGY
    assert ownership.take_profit_owner is ProtectiveOrderOwner.STRATEGY


def test_phase6_migration_adds_minimal_reconciliation_columns_and_indexes(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)

    with sqlite3.connect(db_path) as conn:
        trade_columns = {
            str(row[1]): str(row[4])
            for row in conn.execute("PRAGMA table_info(trades)")
        }
        order_columns = {
            str(row[1]): str(row[2])
            for row in conn.execute("PRAGMA table_info(orders)")
        }
        order_indexes = {
            str(row[1])
            for row in conn.execute("PRAGMA index_list(orders)")
        }

    assert trade_columns["protective_orders_mode"] == "'strategy_managed'"
    assert order_columns["venue_status_raw"] == "TEXT"
    assert order_columns["last_exchange_sync_at"] == "TEXT"
    assert "idx_orders_exchange_order_id" in order_indexes
    assert "idx_orders_attempt_purpose_idx" in order_indexes
