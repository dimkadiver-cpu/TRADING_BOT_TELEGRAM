"""Backtesting storage layer — BacktestRunStore and BacktestTradeStore.

Persists backtest run metadata and per-trade results imported from freqtrade's
output JSON.

Usage:
    from src.backtesting.storage import BacktestRunStore, BacktestTradeStore

    run_store = BacktestRunStore(db_path)
    run_id = await run_store.insert_run(scenario_name="follow_full_chain", ...)
    await run_store.update_status(run_id, "COMPLETED")

    trade_store = BacktestTradeStore(db_path)
    n = await trade_store.import_from_freqtrade_json(run_id, "results.json")
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite


# ---------------------------------------------------------------------------
# BacktestRunStore
# ---------------------------------------------------------------------------


class BacktestRunStore:
    """Accessor for the backtest_runs table."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def insert_run(
        self,
        *,
        scenario_name: str,
        scenario_conditions_json: str,
        trader_filter: str | None,
        date_from: str | None,
        date_to: str | None,
        chains_count: int,
        chains_blocked: int,
        output_dir: str,
        status: str = "RUNNING",
    ) -> int:
        """INSERT a new backtest run and return run_id."""
        run_ts = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                """
                INSERT INTO backtest_runs (
                    scenario_name, scenario_conditions_json,
                    trader_filter, date_from, date_to,
                    chains_count, chains_blocked,
                    run_ts, status, output_dir
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scenario_name,
                    scenario_conditions_json,
                    trader_filter,
                    date_from,
                    date_to,
                    chains_count,
                    chains_blocked,
                    run_ts,
                    status,
                    output_dir,
                ),
            )
            await db.commit()
            return cursor.lastrowid  # type: ignore[return-value]

    async def update_status(
        self,
        run_id: int,
        status: str,
        error: str | None = None,
    ) -> None:
        """Update the status (and optional error) of an existing run."""
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE backtest_runs SET status=?, error=? WHERE run_id=?",
                (status, error, run_id),
            )
            await db.commit()

    async def get_run(self, run_id: int) -> dict[str, Any] | None:
        """Return the run row as a dict, or None if not found."""
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM backtest_runs WHERE run_id=?",
                (run_id,),
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_all_runs(self) -> list[dict[str, Any]]:
        """Return all backtest runs ordered by run_id ASC."""
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM backtest_runs ORDER BY run_id ASC"
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# BacktestTradeStore
# ---------------------------------------------------------------------------


class BacktestTradeStore:
    """Accessor for the backtest_trades table."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def import_from_freqtrade_json(
        self,
        run_id: int,
        results_path: str,
    ) -> int:
        """Import trades from a freqtrade backtesting results JSON file.

        Freqtrade JSON shape:
            {"trades": [{"pair": "BTC/USDT:USDT", "is_short": false, ...}]}

        Field mapping:
            pair                → pair
            is_short            → side  (True→"SHORT", False→"LONG")
            open_date           → open_date
            close_date          → close_date
            open_rate           → entry_price
            close_rate          → close_price
            profit_abs          → profit_usdt
            profit_ratio        → profit_pct
            exit_reason         → exit_reason
            max_drawdown        → max_drawdown_pct
            trade_duration      → duration_seconds
            enter_tag           → chain_id (whole value) + trader_id (part before ":")

        Returns the number of trades imported.
        """
        data = json.loads(Path(results_path).read_text(encoding="utf-8"))
        trades: list[dict[str, Any]] = data.get("trades", [])

        count = 0
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("PRAGMA foreign_keys=ON")
            for trade in trades:
                enter_tag: str = trade.get("enter_tag") or ""
                chain_id = enter_tag
                trader_id = enter_tag.split(":")[0] if enter_tag else ""
                side = "SHORT" if trade.get("is_short") else "LONG"

                await db.execute(
                    """
                    INSERT INTO backtest_trades (
                        run_id, chain_id, trader_id, pair, side,
                        open_date, close_date,
                        entry_price, close_price,
                        profit_usdt, profit_pct,
                        exit_reason, max_drawdown_pct, duration_seconds,
                        sl_moved_to_be, raw_freqtrade_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        chain_id,
                        trader_id,
                        trade.get("pair", ""),
                        side,
                        trade.get("open_date"),
                        trade.get("close_date"),
                        trade.get("open_rate"),
                        trade.get("close_rate"),
                        trade.get("profit_abs"),
                        trade.get("profit_ratio"),
                        trade.get("exit_reason"),
                        trade.get("max_drawdown"),
                        trade.get("trade_duration"),
                        0,
                        json.dumps(trade, ensure_ascii=False),
                    ),
                )
                count += 1

            await db.commit()
        return count

    async def get_trades_by_run(self, run_id: int) -> list[dict[str, Any]]:
        """Return all backtest_trades rows for the given run_id."""
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM backtest_trades WHERE run_id=? ORDER BY bt_trade_id ASC",
                (run_id,),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_trades_by_chain(self, chain_id: str) -> list[dict[str, Any]]:
        """Return all backtest_trades rows for the given chain_id."""
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM backtest_trades WHERE chain_id=? ORDER BY bt_trade_id ASC",
                (chain_id,),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
