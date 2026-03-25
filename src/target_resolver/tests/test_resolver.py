"""Tests for src/target_resolver/resolver.py and src/storage/signals_query.py.

Coverage:
  signals_query:
    - count_open: empty DB → 0; rows present → correct count
    - get_by_root_telegram_id: found / not found
    - get_by_trader_signal_id: found / not found
    - get_open_by_symbol: multiple rows; CLOSED excluded
    - get_open_by_trader: side=None, BUY, SELL

  resolver:
    - target_ref=None → returns None
    - STRONG/REPLY → finds by root_telegram_id, correct position_ids
    - STRONG/EXPLICIT_ID → finds by trader_signal_id
    - STRONG/TELEGRAM_LINK → UNRESOLVED (not implemented)
    - STRONG/REPLY not found → UNRESOLVED
    - SYMBOL → finds open positions by symbol
    - GLOBAL all_long → BUY positions only
    - GLOBAL all_short → SELL positions only
    - GLOBAL all_positions → all open positions
    - Eligibility: U_CLOSE_FULL on PENDING → WARN
    - Eligibility: U_CLOSE_FULL on ACTIVE → ELIGIBLE
    - Eligibility: any ACTION intent on CLOSED → INELIGIBLE
    - Eligibility: U_CANCEL_PENDING on PENDING → ELIGIBLE
    - Eligibility: U_TP_HIT (CONTEXT) only → ELIGIBLE (INFO_ONLY)
    - Eligibility: no intents → ELIGIBLE
    - position_ids from operational_signals correctly populated
    - position_ids empty when no operational_signals present
"""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from src.parser.models.canonical import Intent, Price, TargetRef, TraderParseResult
from src.parser.models.new_signal import EntryLevel, NewSignalEntities, StopLoss, TakeProfit
from src.parser.models.operational import OperationalSignal
from src.storage.signals_query import (
    count_open,
    get_by_root_telegram_id,
    get_by_trader_signal_id,
    get_open_by_symbol,
    get_open_by_trader,
)
from src.target_resolver.models import ResolvedTarget
from src.target_resolver.resolver import _check_eligibility, resolve


# ---------------------------------------------------------------------------
# DB setup helpers
# ---------------------------------------------------------------------------

_SIGNALS_DDL = """
    CREATE TABLE IF NOT EXISTS signals (
        attempt_key        TEXT PRIMARY KEY,
        env                TEXT NOT NULL DEFAULT 'T',
        channel_id         TEXT NOT NULL DEFAULT 'ch1',
        root_telegram_id   TEXT NOT NULL,
        trader_id          TEXT NOT NULL,
        trader_prefix      TEXT NOT NULL DEFAULT 'T',
        trader_signal_id   INTEGER,
        symbol             TEXT,
        side               TEXT,
        entry_json         TEXT,
        sl                 REAL,
        tp_json            TEXT,
        status             TEXT NOT NULL DEFAULT 'PENDING',
        confidence         REAL NOT NULL DEFAULT 0.0,
        raw_text           TEXT NOT NULL DEFAULT '',
        created_at         TEXT NOT NULL DEFAULT '2026-01-01T00:00:00',
        updated_at         TEXT NOT NULL DEFAULT '2026-01-01T00:00:00'
    );
"""

_OP_SIGNALS_DDL = """
    CREATE TABLE IF NOT EXISTS operational_signals (
        op_signal_id       INTEGER PRIMARY KEY AUTOINCREMENT,
        parse_result_id    INTEGER NOT NULL DEFAULT 1,
        attempt_key        TEXT,
        trader_id          TEXT NOT NULL,
        message_type       TEXT NOT NULL DEFAULT 'NEW_SIGNAL',
        is_blocked         INTEGER NOT NULL DEFAULT 0,
        position_size_pct  REAL,
        leverage           INTEGER,
        created_at         TEXT NOT NULL DEFAULT '2026-01-01T00:00:00'
    );
"""


async def _make_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(_SIGNALS_DDL + _OP_SIGNALS_DDL)
        await db.commit()
    return db_path


async def _insert_signal(
    db: aiosqlite.Connection,
    *,
    attempt_key: str,
    root_telegram_id: str = "100",
    trader_id: str = "trader_3",
    trader_signal_id: int | None = None,
    symbol: str = "BTCUSDT",
    side: str | None = "BUY",
    status: str = "ACTIVE",
) -> None:
    await db.execute(
        "INSERT INTO signals"
        "(attempt_key, root_telegram_id, trader_id, trader_signal_id, "
        " symbol, side, status)"
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (attempt_key, root_telegram_id, trader_id, trader_signal_id,
         symbol, side, status),
    )


async def _insert_op_signal(
    db: aiosqlite.Connection,
    *,
    attempt_key: str,
    trader_id: str = "trader_3",
) -> int:
    cur = await db.execute(
        "INSERT INTO operational_signals(attempt_key, trader_id) VALUES (?, ?)",
        (attempt_key, trader_id),
    )
    return cur.lastrowid  # type: ignore[return-value]


def _make_operational(
    trader_id: str = "trader_3",
    message_type: str = "UPDATE",
    intents: list[Intent] | None = None,
    target_ref: TargetRef | None = None,
) -> OperationalSignal:
    if message_type == "NEW_SIGNAL":
        pr = TraderParseResult(
            message_type="NEW_SIGNAL",
            completeness="COMPLETE",
            trader_id=trader_id,
            raw_text="x",
            intents=intents or [],
            target_ref=target_ref,
        )
    else:
        pr = TraderParseResult(
            message_type=message_type,
            trader_id=trader_id,
            raw_text="x",
            intents=intents or [],
            target_ref=target_ref,
        )
    return OperationalSignal(parse_result=pr)


# ---------------------------------------------------------------------------
# signals_query — count_open
# ---------------------------------------------------------------------------

class TestCountOpen:
    @pytest.mark.asyncio
    async def test_empty_db(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        assert await count_open("trader_3", "BTCUSDT", db) == 0

    @pytest.mark.asyncio
    async def test_none_symbol_returns_zero(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        assert await count_open("trader_3", None, db) == 0

    @pytest.mark.asyncio
    async def test_counts_non_closed(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        async with aiosqlite.connect(db) as conn:
            await _insert_signal(conn, attempt_key="k1", symbol="BTCUSDT", status="ACTIVE")
            await _insert_signal(conn, attempt_key="k2", symbol="BTCUSDT", status="PENDING")
            await _insert_signal(conn, attempt_key="k3", symbol="BTCUSDT", status="CLOSED")
            await conn.commit()
        assert await count_open("trader_3", "BTCUSDT", db) == 2

    @pytest.mark.asyncio
    async def test_scoped_to_trader(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        async with aiosqlite.connect(db) as conn:
            await _insert_signal(conn, attempt_key="k1", trader_id="trader_a", symbol="BTCUSDT")
            await _insert_signal(conn, attempt_key="k2", trader_id="trader_b", symbol="BTCUSDT")
            await conn.commit()
        assert await count_open("trader_a", "BTCUSDT", db) == 1
        assert await count_open("trader_b", "BTCUSDT", db) == 1

    @pytest.mark.asyncio
    async def test_fresh_db_no_table(self, tmp_path: Path) -> None:
        db_path = tmp_path / "fresh.db"
        async with aiosqlite.connect(db_path) as db:
            await db.commit()
        assert await count_open("trader_3", "BTCUSDT", db_path) == 0


# ---------------------------------------------------------------------------
# signals_query — get_by_root_telegram_id
# ---------------------------------------------------------------------------

class TestGetByRootTelegramId:
    @pytest.mark.asyncio
    async def test_found(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        async with aiosqlite.connect(db) as conn:
            await _insert_signal(conn, attempt_key="k1", root_telegram_id="999")
            await conn.commit()
        row = await get_by_root_telegram_id(999, "trader_3", db)
        assert row is not None
        assert row.attempt_key == "k1"
        assert row.root_telegram_id == "999"

    @pytest.mark.asyncio
    async def test_not_found(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        row = await get_by_root_telegram_id(42, "trader_3", db)
        assert row is None

    @pytest.mark.asyncio
    async def test_scoped_to_trader(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        async with aiosqlite.connect(db) as conn:
            await _insert_signal(conn, attempt_key="k1", trader_id="trader_a",
                                  root_telegram_id="7")
            await conn.commit()
        assert await get_by_root_telegram_id(7, "trader_b", db) is None
        assert await get_by_root_telegram_id(7, "trader_a", db) is not None

    @pytest.mark.asyncio
    async def test_string_ref_also_works(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        async with aiosqlite.connect(db) as conn:
            await _insert_signal(conn, attempt_key="k1", root_telegram_id="555")
            await conn.commit()
        row = await get_by_root_telegram_id("555", "trader_3", db)
        assert row is not None


# ---------------------------------------------------------------------------
# signals_query — get_by_trader_signal_id
# ---------------------------------------------------------------------------

class TestGetByTraderSignalId:
    @pytest.mark.asyncio
    async def test_found(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        async with aiosqlite.connect(db) as conn:
            await _insert_signal(conn, attempt_key="k1", trader_signal_id=42)
            await conn.commit()
        row = await get_by_trader_signal_id(42, "trader_3", db)
        assert row is not None
        assert row.trader_signal_id == 42

    @pytest.mark.asyncio
    async def test_not_found(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        row = await get_by_trader_signal_id(99, "trader_3", db)
        assert row is None

    @pytest.mark.asyncio
    async def test_scoped_to_trader(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        async with aiosqlite.connect(db) as conn:
            await _insert_signal(conn, attempt_key="k1", trader_id="trader_a",
                                  trader_signal_id=1)
            await conn.commit()
        assert await get_by_trader_signal_id(1, "trader_b", db) is None
        assert await get_by_trader_signal_id(1, "trader_a", db) is not None


# ---------------------------------------------------------------------------
# signals_query — get_open_by_symbol
# ---------------------------------------------------------------------------

class TestGetOpenBySymbol:
    @pytest.mark.asyncio
    async def test_returns_open_only(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        async with aiosqlite.connect(db) as conn:
            await _insert_signal(conn, attempt_key="k1", symbol="BTCUSDT", status="ACTIVE")
            await _insert_signal(conn, attempt_key="k2", symbol="BTCUSDT", status="PENDING")
            await _insert_signal(conn, attempt_key="k3", symbol="BTCUSDT", status="CLOSED")
            await conn.commit()
        rows = await get_open_by_symbol("trader_3", "BTCUSDT", db)
        keys = {r.attempt_key for r in rows}
        assert keys == {"k1", "k2"}

    @pytest.mark.asyncio
    async def test_symbol_filter(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        async with aiosqlite.connect(db) as conn:
            await _insert_signal(conn, attempt_key="k1", symbol="BTCUSDT")
            await _insert_signal(conn, attempt_key="k2", symbol="ETHUSDT")
            await conn.commit()
        rows = await get_open_by_symbol("trader_3", "BTCUSDT", db)
        assert len(rows) == 1
        assert rows[0].symbol == "BTCUSDT"

    @pytest.mark.asyncio
    async def test_empty(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        rows = await get_open_by_symbol("trader_3", "BTCUSDT", db)
        assert rows == []


# ---------------------------------------------------------------------------
# signals_query — get_open_by_trader
# ---------------------------------------------------------------------------

class TestGetOpenByTrader:
    @pytest.mark.asyncio
    async def test_all_positions_no_side_filter(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        async with aiosqlite.connect(db) as conn:
            await _insert_signal(conn, attempt_key="k1", side="BUY",  symbol="BTCUSDT")
            await _insert_signal(conn, attempt_key="k2", side="SELL", symbol="ETHUSDT")
            await conn.commit()
        rows = await get_open_by_trader("trader_3", None, db)
        assert len(rows) == 2

    @pytest.mark.asyncio
    async def test_buy_only(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        async with aiosqlite.connect(db) as conn:
            await _insert_signal(conn, attempt_key="k1", side="BUY",  symbol="BTCUSDT")
            await _insert_signal(conn, attempt_key="k2", side="SELL", symbol="ETHUSDT")
            await conn.commit()
        rows = await get_open_by_trader("trader_3", "BUY", db)
        assert len(rows) == 1
        assert rows[0].side == "BUY"

    @pytest.mark.asyncio
    async def test_sell_only(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        async with aiosqlite.connect(db) as conn:
            await _insert_signal(conn, attempt_key="k1", side="BUY",  symbol="BTCUSDT")
            await _insert_signal(conn, attempt_key="k2", side="SELL", symbol="ETHUSDT")
            await conn.commit()
        rows = await get_open_by_trader("trader_3", "SELL", db)
        assert len(rows) == 1
        assert rows[0].side == "SELL"

    @pytest.mark.asyncio
    async def test_closed_excluded(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        async with aiosqlite.connect(db) as conn:
            await _insert_signal(conn, attempt_key="k1", side="BUY", status="CLOSED")
            await conn.commit()
        rows = await get_open_by_trader("trader_3", None, db)
        assert rows == []


# ---------------------------------------------------------------------------
# resolve — target_ref=None
# ---------------------------------------------------------------------------

class TestResolveNoTarget:
    @pytest.mark.asyncio
    async def test_none_target_ref_returns_none(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        op = _make_operational(target_ref=None)
        result = await resolve(op, db)
        assert result is None

    @pytest.mark.asyncio
    async def test_new_signal_no_target_returns_none(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        op = _make_operational(message_type="NEW_SIGNAL", target_ref=None)
        result = await resolve(op, db)
        assert result is None


# ---------------------------------------------------------------------------
# resolve — STRONG / REPLY
# ---------------------------------------------------------------------------

class TestResolveStrongReply:
    @pytest.mark.asyncio
    async def test_found_eligible(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        async with aiosqlite.connect(db) as conn:
            await _insert_signal(conn, attempt_key="k1", root_telegram_id="42",
                                  status="ACTIVE")
            op_id = await _insert_op_signal(conn, attempt_key="k1")
            await conn.commit()

        op = _make_operational(
            intents=[Intent(name="U_MOVE_STOP", kind="ACTION")],
            target_ref=TargetRef(kind="STRONG", method="REPLY", ref=42),
        )
        result = await resolve(op, db)
        assert result is not None
        assert result.kind == "STRONG"
        assert result.eligibility == "ELIGIBLE"
        assert op_id in result.position_ids

    @pytest.mark.asyncio
    async def test_not_found_unresolved(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        op = _make_operational(
            intents=[Intent(name="U_MOVE_STOP", kind="ACTION")],
            target_ref=TargetRef(kind="STRONG", method="REPLY", ref=999),
        )
        result = await resolve(op, db)
        assert result is not None
        assert result.eligibility == "UNRESOLVED"
        assert result.position_ids == []

    @pytest.mark.asyncio
    async def test_pending_status_move_stop_warn(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        async with aiosqlite.connect(db) as conn:
            await _insert_signal(conn, attempt_key="k1", root_telegram_id="10",
                                  status="PENDING")
            await _insert_op_signal(conn, attempt_key="k1")
            await conn.commit()

        op = _make_operational(
            intents=[Intent(name="U_MOVE_STOP", kind="ACTION")],
            target_ref=TargetRef(kind="STRONG", method="REPLY", ref=10),
        )
        result = await resolve(op, db)
        assert result is not None
        assert result.eligibility == "WARN"
        assert result.reason is not None

    @pytest.mark.asyncio
    async def test_closed_status_ineligible(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        async with aiosqlite.connect(db) as conn:
            await _insert_signal(conn, attempt_key="k1", root_telegram_id="20",
                                  status="CLOSED")
            await _insert_op_signal(conn, attempt_key="k1")
            await conn.commit()

        op = _make_operational(
            intents=[Intent(name="U_CLOSE_FULL", kind="ACTION")],
            target_ref=TargetRef(kind="STRONG", method="REPLY", ref=20),
        )
        result = await resolve(op, db)
        assert result is not None
        assert result.eligibility == "INELIGIBLE"


# ---------------------------------------------------------------------------
# resolve — STRONG / EXPLICIT_ID
# ---------------------------------------------------------------------------

class TestResolveStrongExplicitId:
    @pytest.mark.asyncio
    async def test_found_eligible(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        async with aiosqlite.connect(db) as conn:
            await _insert_signal(conn, attempt_key="k1", trader_signal_id=7,
                                  status="ACTIVE")
            op_id = await _insert_op_signal(conn, attempt_key="k1")
            await conn.commit()

        op = _make_operational(
            intents=[Intent(name="U_CLOSE_FULL", kind="ACTION")],
            target_ref=TargetRef(kind="STRONG", method="EXPLICIT_ID", ref=7),
        )
        result = await resolve(op, db)
        assert result is not None
        assert result.eligibility == "ELIGIBLE"
        assert op_id in result.position_ids

    @pytest.mark.asyncio
    async def test_not_found_unresolved(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        op = _make_operational(
            intents=[Intent(name="U_CLOSE_FULL", kind="ACTION")],
            target_ref=TargetRef(kind="STRONG", method="EXPLICIT_ID", ref=99),
        )
        result = await resolve(op, db)
        assert result is not None
        assert result.eligibility == "UNRESOLVED"


# ---------------------------------------------------------------------------
# resolve — STRONG / TELEGRAM_LINK
# ---------------------------------------------------------------------------

class TestResolveStrongTelegramLink:
    @pytest.mark.asyncio
    async def test_not_implemented_returns_unresolved(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        op = _make_operational(
            intents=[Intent(name="U_MOVE_STOP", kind="ACTION")],
            target_ref=TargetRef(
                kind="STRONG", method="TELEGRAM_LINK",
                ref="https://t.me/c/123/456"
            ),
        )
        result = await resolve(op, db)
        assert result is not None
        assert result.eligibility == "UNRESOLVED"
        assert result.reason is not None
        assert "TELEGRAM_LINK" in result.reason


# ---------------------------------------------------------------------------
# resolve — SYMBOL
# ---------------------------------------------------------------------------

class TestResolveSymbol:
    @pytest.mark.asyncio
    async def test_finds_open_positions(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        async with aiosqlite.connect(db) as conn:
            await _insert_signal(conn, attempt_key="k1", symbol="BTCUSDT", status="ACTIVE")
            await _insert_signal(conn, attempt_key="k2", symbol="BTCUSDT", status="PENDING")
            await _insert_signal(conn, attempt_key="k3", symbol="BTCUSDT", status="CLOSED")
            op1 = await _insert_op_signal(conn, attempt_key="k1")
            op2 = await _insert_op_signal(conn, attempt_key="k2")
            await conn.commit()

        op = _make_operational(
            intents=[Intent(name="U_CANCEL_PENDING", kind="ACTION")],
            target_ref=TargetRef(kind="SYMBOL", symbol="BTCUSDT"),
        )
        result = await resolve(op, db)
        assert result is not None
        assert result.kind == "SYMBOL"
        # k3 is CLOSED so not in rows → only k1, k2
        assert set(result.position_ids) == {op1, op2}

    @pytest.mark.asyncio
    async def test_no_open_positions_unresolved(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        op = _make_operational(
            intents=[Intent(name="U_CLOSE_FULL", kind="ACTION")],
            target_ref=TargetRef(kind="SYMBOL", symbol="BTCUSDT"),
        )
        result = await resolve(op, db)
        assert result is not None
        assert result.eligibility == "UNRESOLVED"

    @pytest.mark.asyncio
    async def test_cancel_pending_on_pending_eligible(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        async with aiosqlite.connect(db) as conn:
            await _insert_signal(conn, attempt_key="k1", symbol="BTCUSDT",
                                  status="PENDING")
            await _insert_op_signal(conn, attempt_key="k1")
            await conn.commit()

        op = _make_operational(
            intents=[Intent(name="U_CANCEL_PENDING", kind="ACTION")],
            target_ref=TargetRef(kind="SYMBOL", symbol="BTCUSDT"),
        )
        result = await resolve(op, db)
        assert result is not None
        assert result.eligibility == "ELIGIBLE"


# ---------------------------------------------------------------------------
# resolve — GLOBAL
# ---------------------------------------------------------------------------

class TestResolveGlobal:
    @pytest.mark.asyncio
    async def test_all_long(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        async with aiosqlite.connect(db) as conn:
            await _insert_signal(conn, attempt_key="k1", side="BUY",  symbol="BTCUSDT")
            await _insert_signal(conn, attempt_key="k2", side="SELL", symbol="ETHUSDT")
            await _insert_op_signal(conn, attempt_key="k1")
            await conn.commit()

        op = _make_operational(
            intents=[Intent(name="U_CLOSE_FULL", kind="ACTION")],
            target_ref=TargetRef(kind="GLOBAL", scope="all_long"),
        )
        result = await resolve(op, db)
        assert result is not None
        assert result.kind == "GLOBAL"
        assert len(result.position_ids) == 1

    @pytest.mark.asyncio
    async def test_all_short(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        async with aiosqlite.connect(db) as conn:
            await _insert_signal(conn, attempt_key="k1", side="BUY",  symbol="BTCUSDT")
            await _insert_signal(conn, attempt_key="k2", side="SELL", symbol="ETHUSDT")
            op_id = await _insert_op_signal(conn, attempt_key="k2")
            await conn.commit()

        op = _make_operational(
            intents=[Intent(name="U_CLOSE_FULL", kind="ACTION")],
            target_ref=TargetRef(kind="GLOBAL", scope="all_short"),
        )
        result = await resolve(op, db)
        assert result is not None
        assert result.position_ids == [op_id]

    @pytest.mark.asyncio
    async def test_all_positions(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        async with aiosqlite.connect(db) as conn:
            await _insert_signal(conn, attempt_key="k1", side="BUY",  symbol="BTCUSDT")
            await _insert_signal(conn, attempt_key="k2", side="SELL", symbol="ETHUSDT")
            op1 = await _insert_op_signal(conn, attempt_key="k1")
            op2 = await _insert_op_signal(conn, attempt_key="k2")
            await conn.commit()

        op = _make_operational(
            intents=[Intent(name="U_CLOSE_FULL", kind="ACTION")],
            target_ref=TargetRef(kind="GLOBAL", scope="all_positions"),
        )
        result = await resolve(op, db)
        assert result is not None
        assert set(result.position_ids) == {op1, op2}

    @pytest.mark.asyncio
    async def test_all_positions_empty_unresolved(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        op = _make_operational(
            intents=[Intent(name="U_CLOSE_FULL", kind="ACTION")],
            target_ref=TargetRef(kind="GLOBAL", scope="all_positions"),
        )
        result = await resolve(op, db)
        assert result is not None
        assert result.eligibility == "UNRESOLVED"


# ---------------------------------------------------------------------------
# Eligibility — _check_eligibility unit tests
# ---------------------------------------------------------------------------

class TestCheckEligibility:
    def _row(self, status: str, attempt_key: str = "k1") -> object:
        """Minimal mock object for eligibility check."""
        from src.storage.signals_query import SignalRow
        return SignalRow(
            attempt_key=attempt_key, env="T", channel_id="ch1",
            root_telegram_id="1", trader_id="t", trader_prefix="T",
            trader_signal_id=None, symbol="BTCUSDT", side="BUY",
            entry_json=None, sl=None, tp_json=None,
            status=status, confidence=0.0, raw_text="",
            created_at="", updated_at="",
        )

    def test_no_intents_eligible(self) -> None:
        rows = [self._row("ACTIVE")]
        elig, reason = _check_eligibility(rows, [])
        assert elig == "ELIGIBLE"
        assert reason is None

    def test_context_only_eligible(self) -> None:
        rows = [self._row("CLOSED")]
        intents = [Intent(name="U_TP_HIT", kind="CONTEXT")]
        elig, reason = _check_eligibility(rows, intents)
        assert elig == "ELIGIBLE"

    def test_close_full_active_eligible(self) -> None:
        rows = [self._row("ACTIVE")]
        intents = [Intent(name="U_CLOSE_FULL", kind="ACTION")]
        elig, _ = _check_eligibility(rows, intents)
        assert elig == "ELIGIBLE"

    def test_close_full_pending_warn(self) -> None:
        rows = [self._row("PENDING")]
        intents = [Intent(name="U_CLOSE_FULL", kind="ACTION")]
        elig, reason = _check_eligibility(rows, intents)
        assert elig == "WARN"
        assert reason is not None

    def test_close_full_closed_ineligible(self) -> None:
        rows = [self._row("CLOSED")]
        intents = [Intent(name="U_CLOSE_FULL", kind="ACTION")]
        elig, _ = _check_eligibility(rows, intents)
        assert elig == "INELIGIBLE"

    def test_cancel_pending_on_pending_eligible(self) -> None:
        rows = [self._row("PENDING")]
        intents = [Intent(name="U_CANCEL_PENDING", kind="ACTION")]
        elig, _ = _check_eligibility(rows, intents)
        assert elig == "ELIGIBLE"

    def test_reenter_closed_ineligible(self) -> None:
        rows = [self._row("CLOSED")]
        intents = [Intent(name="U_REENTER", kind="ACTION")]
        elig, _ = _check_eligibility(rows, intents)
        assert elig == "INELIGIBLE"

    def test_move_stop_pending_warn(self) -> None:
        rows = [self._row("PENDING")]
        intents = [Intent(name="U_MOVE_STOP", kind="ACTION")]
        elig, _ = _check_eligibility(rows, intents)
        assert elig == "WARN"

    def test_worst_case_multiple_rows(self) -> None:
        """One ACTIVE, one CLOSED → INELIGIBLE wins."""
        rows = [self._row("ACTIVE", "k1"), self._row("CLOSED", "k2")]
        intents = [Intent(name="U_CLOSE_FULL", kind="ACTION")]
        elig, _ = _check_eligibility(rows, intents)
        assert elig == "INELIGIBLE"

    def test_worst_case_warn_beats_eligible(self) -> None:
        """One ACTIVE (ELIGIBLE), one PENDING (WARN) → WARN wins."""
        rows = [self._row("ACTIVE", "k1"), self._row("PENDING", "k2")]
        intents = [Intent(name="U_CLOSE_FULL", kind="ACTION")]
        elig, _ = _check_eligibility(rows, intents)
        assert elig == "WARN"

    def test_unknown_intent_treated_as_eligible(self) -> None:
        rows = [self._row("CLOSED")]
        intents = [Intent(name="U_UNKNOWN_FUTURE", kind="ACTION")]
        elig, _ = _check_eligibility(rows, intents)
        assert elig == "ELIGIBLE"

    def test_sl_hit_context_on_closed_eligible(self) -> None:
        rows = [self._row("CLOSED")]
        intents = [Intent(name="U_SL_HIT", kind="CONTEXT")]
        elig, _ = _check_eligibility(rows, intents)
        assert elig == "ELIGIBLE"


# ---------------------------------------------------------------------------
# resolve — position_ids from operational_signals
# ---------------------------------------------------------------------------

class TestPositionIds:
    @pytest.mark.asyncio
    async def test_position_ids_populated_when_op_signal_exists(self, tmp_path: Path) -> None:
        db = await _make_db(tmp_path)
        async with aiosqlite.connect(db) as conn:
            await _insert_signal(conn, attempt_key="k1", root_telegram_id="77",
                                  status="ACTIVE")
            op_id = await _insert_op_signal(conn, attempt_key="k1")
            await conn.commit()

        op = _make_operational(
            intents=[Intent(name="U_CLOSE_FULL", kind="ACTION")],
            target_ref=TargetRef(kind="STRONG", method="REPLY", ref=77),
        )
        result = await resolve(op, db)
        assert result is not None
        assert result.position_ids == [op_id]

    @pytest.mark.asyncio
    async def test_position_ids_empty_when_no_op_signal(self, tmp_path: Path) -> None:
        """Signal found but no corresponding operational_signal yet."""
        db = await _make_db(tmp_path)
        async with aiosqlite.connect(db) as conn:
            await _insert_signal(conn, attempt_key="k1", root_telegram_id="88",
                                  status="ACTIVE")
            # No operational_signal inserted
            await conn.commit()

        op = _make_operational(
            intents=[Intent(name="U_CLOSE_FULL", kind="ACTION")],
            target_ref=TargetRef(kind="STRONG", method="REPLY", ref=88),
        )
        result = await resolve(op, db)
        assert result is not None
        # Signal was found → eligibility is computed
        assert result.eligibility == "ELIGIBLE"
        # But no operational_signal → position_ids is empty
        assert result.position_ids == []
