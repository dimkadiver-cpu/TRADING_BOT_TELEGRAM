"""Tests for BacktestRunStore and BacktestTradeStore.

Covers:
- Insert and retrieve a backtest run
- Update run status (RUNNING → COMPLETED)
- Import trades from a freqtrade JSON fixture
- get_trades_by_run and get_trades_by_chain
- FK constraint violation on invalid run_id
- get_all_runs: empty and populated
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from src.backtesting.storage import BacktestRunStore, BacktestTradeStore
from src.backtesting.tests.conftest import _CREATE_SCHEMA

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path: Path) -> str:
    """Temporary on-disk SQLite DB with the full schema applied."""
    path = str(tmp_path / "test_bt.sqlite3")
    with sqlite3.connect(path) as conn:
        conn.executescript(_CREATE_SCHEMA)
        conn.commit()
    return path


@pytest.fixture
def freqtrade_json_path(tmp_path: Path) -> str:
    """A minimal freqtrade backtesting results JSON file."""
    data = {
        "trades": [
            {
                "pair": "BTC/USDT:USDT",
                "is_short": False,
                "open_date": "2025-01-01T10:00:00",
                "close_date": "2025-01-02T12:00:00",
                "open_rate": 90000.0,
                "close_rate": 95000.0,
                "profit_abs": 50.0,
                "profit_ratio": 0.0556,
                "exit_reason": "roi",
                "max_drawdown": 0.02,
                "trade_duration": 3600,
                "enter_tag": "trader_3:trader_3:12345",
            },
            {
                "pair": "ETH/USDT:USDT",
                "is_short": True,
                "open_date": "2025-01-03T08:00:00",
                "close_date": "2025-01-03T18:00:00",
                "open_rate": 3200.0,
                "close_rate": 3100.0,
                "profit_abs": 20.0,
                "profit_ratio": 0.0313,
                "exit_reason": "roi",
                "max_drawdown": 0.01,
                "trade_duration": 1800,
                "enter_tag": "trader_3:trader_3:12346",
            },
        ]
    }
    path = tmp_path / "freqtrade_results.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


# ---------------------------------------------------------------------------
# BacktestRunStore tests
# ---------------------------------------------------------------------------

async def test_insert_and_get_run(db_path: str) -> None:
    store = BacktestRunStore(db_path)
    run_id = await store.insert_run(
        scenario_name="follow_full_chain",
        scenario_conditions_json='{"follow_full_chain": true}',
        trader_filter="trader_3",
        date_from="2025-01-01",
        date_to="2025-12-31",
        chains_count=10,
        chains_blocked=2,
        output_dir="backtest_reports/run_001",
    )

    assert run_id == 1

    row = await store.get_run(run_id)
    assert row is not None
    assert row["scenario_name"] == "follow_full_chain"
    assert row["trader_filter"] == "trader_3"
    assert row["chains_count"] == 10
    assert row["chains_blocked"] == 2
    assert row["status"] == "RUNNING"
    assert row["output_dir"] == "backtest_reports/run_001"


async def test_update_run_status(db_path: str) -> None:
    store = BacktestRunStore(db_path)
    run_id = await store.insert_run(
        scenario_name="signals_only",
        scenario_conditions_json="{}",
        trader_filter=None,
        date_from=None,
        date_to=None,
        chains_count=5,
        chains_blocked=0,
        output_dir="out/",
    )

    await store.update_status(run_id, "COMPLETED")

    row = await store.get_run(run_id)
    assert row is not None
    assert row["status"] == "COMPLETED"
    assert row["error"] is None


async def test_update_run_status_with_error(db_path: str) -> None:
    store = BacktestRunStore(db_path)
    run_id = await store.insert_run(
        scenario_name="aggressive",
        scenario_conditions_json="{}",
        trader_filter=None,
        date_from=None,
        date_to=None,
        chains_count=0,
        chains_blocked=0,
        output_dir="out/",
    )

    await store.update_status(run_id, "FAILED", error="freqtrade subprocess returned exit code 1")

    row = await store.get_run(run_id)
    assert row is not None
    assert row["status"] == "FAILED"
    assert "exit code 1" in row["error"]


async def test_get_all_runs_empty_and_populated(db_path: str) -> None:
    store = BacktestRunStore(db_path)

    # Empty
    runs = await store.get_all_runs()
    assert runs == []

    # Insert two runs
    await store.insert_run(
        scenario_name="scenario_a",
        scenario_conditions_json="{}",
        trader_filter=None,
        date_from=None,
        date_to=None,
        chains_count=1,
        chains_blocked=0,
        output_dir="out/a",
    )
    await store.insert_run(
        scenario_name="scenario_b",
        scenario_conditions_json="{}",
        trader_filter=None,
        date_from=None,
        date_to=None,
        chains_count=2,
        chains_blocked=1,
        output_dir="out/b",
    )

    runs = await store.get_all_runs()
    assert len(runs) == 2
    assert runs[0]["scenario_name"] == "scenario_a"
    assert runs[1]["scenario_name"] == "scenario_b"


async def test_get_run_not_found_returns_none(db_path: str) -> None:
    store = BacktestRunStore(db_path)
    result = await store.get_run(9999)
    assert result is None


# ---------------------------------------------------------------------------
# BacktestTradeStore tests
# ---------------------------------------------------------------------------

async def test_import_trades_from_freqtrade_json(
    db_path: str,
    freqtrade_json_path: str,
) -> None:
    run_store = BacktestRunStore(db_path)
    run_id = await run_store.insert_run(
        scenario_name="test",
        scenario_conditions_json="{}",
        trader_filter=None,
        date_from=None,
        date_to=None,
        chains_count=2,
        chains_blocked=0,
        output_dir="out/",
    )

    trade_store = BacktestTradeStore(db_path)
    count = await trade_store.import_from_freqtrade_json(run_id, freqtrade_json_path)

    assert count == 2

    trades = await trade_store.get_trades_by_run(run_id)
    assert len(trades) == 2

    btc_trade = trades[0]
    assert btc_trade["pair"] == "BTC/USDT:USDT"
    assert btc_trade["side"] == "LONG"
    assert btc_trade["entry_price"] == 90000.0
    assert btc_trade["close_price"] == 95000.0
    assert btc_trade["profit_usdt"] == 50.0
    assert btc_trade["exit_reason"] == "roi"
    assert btc_trade["chain_id"] == "trader_3:trader_3:12345"
    assert btc_trade["trader_id"] == "trader_3"

    eth_trade = trades[1]
    assert eth_trade["pair"] == "ETH/USDT:USDT"
    assert eth_trade["side"] == "SHORT"
    assert eth_trade["chain_id"] == "trader_3:trader_3:12346"


async def test_get_trades_by_run(db_path: str, freqtrade_json_path: str) -> None:
    run_store = BacktestRunStore(db_path)
    run_id_a = await run_store.insert_run(
        scenario_name="run_a",
        scenario_conditions_json="{}",
        trader_filter=None,
        date_from=None,
        date_to=None,
        chains_count=2,
        chains_blocked=0,
        output_dir="out/a",
    )
    run_id_b = await run_store.insert_run(
        scenario_name="run_b",
        scenario_conditions_json="{}",
        trader_filter=None,
        date_from=None,
        date_to=None,
        chains_count=0,
        chains_blocked=0,
        output_dir="out/b",
    )

    trade_store = BacktestTradeStore(db_path)
    await trade_store.import_from_freqtrade_json(run_id_a, freqtrade_json_path)

    trades_a = await trade_store.get_trades_by_run(run_id_a)
    trades_b = await trade_store.get_trades_by_run(run_id_b)

    assert len(trades_a) == 2
    assert len(trades_b) == 0


async def test_get_trades_by_chain(db_path: str, freqtrade_json_path: str) -> None:
    run_store = BacktestRunStore(db_path)
    run_id = await run_store.insert_run(
        scenario_name="test",
        scenario_conditions_json="{}",
        trader_filter=None,
        date_from=None,
        date_to=None,
        chains_count=2,
        chains_blocked=0,
        output_dir="out/",
    )

    trade_store = BacktestTradeStore(db_path)
    await trade_store.import_from_freqtrade_json(run_id, freqtrade_json_path)

    trades = await trade_store.get_trades_by_chain("trader_3:trader_3:12345")
    assert len(trades) == 1
    assert trades[0]["pair"] == "BTC/USDT:USDT"

    no_trades = await trade_store.get_trades_by_chain("nonexistent_chain")
    assert no_trades == []


async def test_fk_constraint_on_invalid_run_id(
    db_path: str,
    freqtrade_json_path: str,
) -> None:
    """BacktestTradeStore enables FK enforcement; inserting with a non-existent
    run_id must raise an IntegrityError."""
    import aiosqlite

    trade_store = BacktestTradeStore(db_path)
    with pytest.raises(Exception):
        await trade_store.import_from_freqtrade_json(
            run_id=9999,  # does not exist
            results_path=freqtrade_json_path,
        )
