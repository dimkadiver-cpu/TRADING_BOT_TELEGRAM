"""Tests for SignalChainBuilder.

Covers:
- Chains with 0, 1, or multiple UPDATEs
- UPDATE linkage via resolved_target_ids and reply_to_message_id fallback
- Orphan UPDATE handling (warning + skip)
- Filters: trader_id and date_from/date_to
- Entity deserialization (NewSignalEntities, UpdateEntities)
- Blocked signal chain
- Chronological ordering of updates
- close_ts derived from U_CLOSE_FULL
"""

from __future__ import annotations

import json
import logging

import aiosqlite
import pytest

from src.backtesting.chain_builder import SignalChainBuilder
from src.parser.models.new_signal import NewSignalEntities
from src.parser.models.update import UpdateEntities

from src.backtesting.tests.conftest import (
    insert_raw_message,
    insert_parse_result,
    insert_signal,
    insert_operational_signal,
    make_new_signal_json,
    make_update_json,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _setup_new_signal(
    db: aiosqlite.Connection,
    *,
    trader_id: str = "trader_3",
    symbol: str = "BTCUSDT",
    side: str = "BUY",
    attempt_key: str = "T:chat_001:tg100:trader_3",
    tg_msg_id: int = 100,
    message_ts: str = "2025-06-01T10:00:00",
    source_chat_id: str = "chat_001",
    sl_price: float = 85000.0,
    entry_price: float = 90000.0,
    tp_prices: list[float] | None = None,
    is_blocked: bool = False,
    block_reason: str | None = None,
) -> int:
    """Insert a complete NEW_SIGNAL chain (raw_message → parse_result → signal → op_signal).
    Returns op_signal_id.
    """
    normalized = make_new_signal_json(
        symbol=symbol,
        entry_price=entry_price,
        sl_price=sl_price,
        tp_prices=tp_prices or [95000.0, 100000.0],
    )
    rm_id = await insert_raw_message(
        db,
        source_chat_id=source_chat_id,
        telegram_message_id=tg_msg_id,
        message_ts=message_ts,
        source_trader_id=trader_id,
    )
    pr_id = await insert_parse_result(
        db,
        raw_message_id=rm_id,
        message_type="NEW_SIGNAL",
        normalized_json=normalized,
        symbol=symbol,
        direction="LONG" if side == "BUY" else "SHORT",
    )
    await insert_signal(
        db,
        attempt_key=attempt_key,
        trader_id=trader_id,
        symbol=symbol,
        side=side,
        channel_id=source_chat_id,
    )
    op_id = await insert_operational_signal(
        db,
        parse_result_id=pr_id,
        trader_id=trader_id,
        message_type="NEW_SIGNAL",
        attempt_key=attempt_key,
        is_blocked=is_blocked,
        block_reason=block_reason,
    )
    return op_id


async def _setup_update(
    db: aiosqlite.Connection,
    *,
    trader_id: str = "trader_3",
    tg_msg_id: int,
    message_ts: str = "2025-06-01T11:00:00",
    source_chat_id: str = "chat_001",
    reply_to_message_id: int | None = None,
    intents: list[dict] | None = None,
    resolved_target_ids: list[int] | None = None,
    new_sl_level: float | None = None,
    close_pct: float | None = None,
    close_price: float | None = None,
) -> int:
    """Insert a complete UPDATE row. Returns op_signal_id."""
    if intents is None:
        intents = [{"name": "U_MOVE_STOP", "kind": "ACTION"}]
    normalized = make_update_json(
        intents=intents,
        new_sl_level=new_sl_level,
        close_pct=close_pct,
        close_price=close_price,
    )
    rm_id = await insert_raw_message(
        db,
        source_chat_id=source_chat_id,
        telegram_message_id=tg_msg_id,
        message_ts=message_ts,
        reply_to_message_id=reply_to_message_id,
        source_trader_id=trader_id,
    )
    pr_id = await insert_parse_result(
        db,
        raw_message_id=rm_id,
        message_type="UPDATE",
        normalized_json=normalized,
    )
    resolved_json = json.dumps(resolved_target_ids) if resolved_target_ids is not None else None
    op_id = await insert_operational_signal(
        db,
        parse_result_id=pr_id,
        trader_id=trader_id,
        message_type="UPDATE",
        attempt_key=None,
        resolved_target_ids=resolved_json,
    )
    return op_id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_build_single_chain_no_updates(test_db_path: str) -> None:
    """A single NEW_SIGNAL with no UPDATEs produces a chain with empty updates list."""
    async with aiosqlite.connect(test_db_path) as db:
        db.row_factory = aiosqlite.Row
        await _setup_new_signal(db, tg_msg_id=100, message_ts="2025-06-01T10:00:00")

    chains = await SignalChainBuilder.build_all_async(test_db_path)

    assert len(chains) == 1
    chain = chains[0]
    assert chain.updates == []
    assert chain.close_ts is None
    assert chain.symbol == "BTCUSDT"
    assert chain.side == "BUY"
    assert chain.sl_price == 85000.0
    assert chain.entry_prices == [90000.0]
    assert chain.tp_prices == [95000.0, 100000.0]


async def test_build_chain_with_one_update(test_db_path: str) -> None:
    """Chain with one UPDATE linked via resolved_target_ids."""
    async with aiosqlite.connect(test_db_path) as db:
        db.row_factory = aiosqlite.Row
        op_id = await _setup_new_signal(db, tg_msg_id=100, message_ts="2025-06-01T10:00:00")
        await _setup_update(
            db,
            tg_msg_id=101,
            message_ts="2025-06-01T11:00:00",
            resolved_target_ids=[op_id],
            new_sl_level=87000.0,
        )

    chains = await SignalChainBuilder.build_all_async(test_db_path)

    assert len(chains) == 1
    chain = chains[0]
    assert len(chain.updates) == 1
    assert chain.close_ts is None


async def test_build_chain_with_multiple_updates_ordered(test_db_path: str) -> None:
    """Updates are sorted by message_ts ASC regardless of insertion order."""
    async with aiosqlite.connect(test_db_path) as db:
        db.row_factory = aiosqlite.Row
        op_id = await _setup_new_signal(db, tg_msg_id=100, message_ts="2025-06-01T10:00:00")
        # Insert out of order
        await _setup_update(db, tg_msg_id=103, message_ts="2025-06-01T13:00:00", resolved_target_ids=[op_id])
        await _setup_update(db, tg_msg_id=101, message_ts="2025-06-01T11:00:00", resolved_target_ids=[op_id])
        await _setup_update(db, tg_msg_id=102, message_ts="2025-06-01T12:00:00", resolved_target_ids=[op_id])

    chains = await SignalChainBuilder.build_all_async(test_db_path)

    assert len(chains) == 1
    chain = chains[0]
    assert len(chain.updates) == 3

    timestamps = [u.message_ts for u in chain.updates]
    assert timestamps == sorted(timestamps)


async def test_update_linked_via_resolved_target_ids(test_db_path: str) -> None:
    """UPDATE linked via resolved_target_ids JSON list."""
    async with aiosqlite.connect(test_db_path) as db:
        db.row_factory = aiosqlite.Row
        op_id = await _setup_new_signal(db, tg_msg_id=100)
        await _setup_update(
            db,
            tg_msg_id=101,
            message_ts="2025-06-01T11:00:00",
            resolved_target_ids=[op_id],
        )

    chains = await SignalChainBuilder.build_all_async(test_db_path)

    assert len(chains) == 1
    assert len(chains[0].updates) == 1
    assert chains[0].updates[0].message_type == "UPDATE"


async def test_update_linked_via_reply_to_message_id_fallback(test_db_path: str) -> None:
    """UPDATE with no resolved_target_ids falls back to reply_to_message_id."""
    async with aiosqlite.connect(test_db_path) as db:
        db.row_factory = aiosqlite.Row
        # NEW_SIGNAL has tg_msg_id=100 in chat_001
        await _setup_new_signal(db, tg_msg_id=100, source_chat_id="chat_001")
        # UPDATE replies to tg_msg_id=100 in the same chat, no resolved_target_ids
        await _setup_update(
            db,
            tg_msg_id=101,
            message_ts="2025-06-01T11:00:00",
            source_chat_id="chat_001",
            reply_to_message_id=100,
            resolved_target_ids=None,
        )

    chains = await SignalChainBuilder.build_all_async(test_db_path)

    assert len(chains) == 1
    assert len(chains[0].updates) == 1


async def test_update_orphan_skipped_with_warning(test_db_path: str, caplog: pytest.LogCaptureFixture) -> None:
    """UPDATE with no resolvable link is skipped with a warning log."""
    async with aiosqlite.connect(test_db_path) as db:
        db.row_factory = aiosqlite.Row
        await _setup_new_signal(db, tg_msg_id=100)
        # reply_to points to non-existent tg_msg_id=999
        await _setup_update(
            db,
            tg_msg_id=201,
            message_ts="2025-06-01T11:00:00",
            reply_to_message_id=999,
            resolved_target_ids=None,
        )

    with caplog.at_level(logging.WARNING, logger="src.backtesting.chain_builder"):
        chains = await SignalChainBuilder.build_all_async(test_db_path)

    assert len(chains) == 1
    assert len(chains[0].updates) == 0
    assert any("could not be linked" in r.message for r in caplog.records)


async def test_filter_by_trader_id(test_db_path: str) -> None:
    """build_all_async with trader_id filters out chains from other traders."""
    async with aiosqlite.connect(test_db_path) as db:
        db.row_factory = aiosqlite.Row
        await _setup_new_signal(
            db,
            trader_id="trader_3",
            tg_msg_id=100,
            attempt_key="T:chat_001:tg100:trader_3",
        )
        await _setup_new_signal(
            db,
            trader_id="trader_a",
            tg_msg_id=200,
            attempt_key="T:chat_002:tg200:trader_a",
            source_chat_id="chat_002",
        )

    chains_3 = await SignalChainBuilder.build_all_async(test_db_path, trader_id="trader_3")
    chains_a = await SignalChainBuilder.build_all_async(test_db_path, trader_id="trader_a")
    all_chains = await SignalChainBuilder.build_all_async(test_db_path)

    assert len(chains_3) == 1
    assert chains_3[0].trader_id == "trader_3"
    assert len(chains_a) == 1
    assert chains_a[0].trader_id == "trader_a"
    assert len(all_chains) == 2


async def test_filter_by_date_range(test_db_path: str) -> None:
    """build_all_async with date_from/date_to filters out out-of-range signals."""
    async with aiosqlite.connect(test_db_path) as db:
        db.row_factory = aiosqlite.Row
        await _setup_new_signal(
            db, tg_msg_id=100, message_ts="2025-01-15T10:00:00",
            attempt_key="T:chat_001:tg100:trader_3",
        )
        await _setup_new_signal(
            db, tg_msg_id=200, message_ts="2025-06-15T10:00:00",
            attempt_key="T:chat_001:tg200:trader_3",
        )
        await _setup_new_signal(
            db, tg_msg_id=300, message_ts="2025-12-15T10:00:00",
            attempt_key="T:chat_001:tg300:trader_3",
        )

    chains = await SignalChainBuilder.build_all_async(
        test_db_path, date_from="2025-06-01", date_to="2025-07-01"
    )

    assert len(chains) == 1
    assert chains[0].open_ts.month == 6


async def test_entities_deserialization_new_signal(test_db_path: str) -> None:
    """NewSignalEntities is correctly deserialized from parse_result_normalized_json."""
    async with aiosqlite.connect(test_db_path) as db:
        db.row_factory = aiosqlite.Row
        await _setup_new_signal(
            db,
            tg_msg_id=100,
            symbol="ETHUSDT",
            entry_price=3000.0,
            sl_price=2800.0,
            tp_prices=[3200.0, 3400.0],
        )

    chains = await SignalChainBuilder.build_all_async(test_db_path)

    assert len(chains) == 1
    chain = chains[0]
    entities = chain.new_signal.entities

    assert isinstance(entities, NewSignalEntities)
    assert entities.symbol == "ETHUSDT"
    assert entities.stop_loss is not None
    assert entities.stop_loss.price.value == 2800.0
    assert len(entities.take_profits) == 2
    assert chain.sl_price == 2800.0
    assert chain.entry_prices == [3000.0]
    assert chain.tp_prices == [3200.0, 3400.0]


async def test_entities_deserialization_update(test_db_path: str) -> None:
    """UpdateEntities is correctly deserialized from parse_result_normalized_json."""
    async with aiosqlite.connect(test_db_path) as db:
        db.row_factory = aiosqlite.Row
        op_id = await _setup_new_signal(db, tg_msg_id=100)
        await _setup_update(
            db,
            tg_msg_id=101,
            message_ts="2025-06-01T11:00:00",
            resolved_target_ids=[op_id],
            intents=[{"name": "U_MOVE_STOP", "kind": "ACTION"}],
            new_sl_level=87500.0,
        )

    chains = await SignalChainBuilder.build_all_async(test_db_path)

    assert len(chains) == 1
    update = chains[0].updates[0]
    entities = update.entities

    assert isinstance(entities, UpdateEntities)
    assert entities.new_sl_level is not None
    assert entities.new_sl_level.value == 87500.0
    assert "U_MOVE_STOP" in update.intents


async def test_chain_with_blocked_signal(test_db_path: str) -> None:
    """Blocked NEW_SIGNAL chains are included but flagged (for analysis/audit)."""
    async with aiosqlite.connect(test_db_path) as db:
        db.row_factory = aiosqlite.Row
        await _setup_new_signal(
            db,
            tg_msg_id=100,
            is_blocked=True,
            block_reason="global_cap_exceeded",
        )

    chains = await SignalChainBuilder.build_all_async(test_db_path)

    assert len(chains) == 1
    chain = chains[0]
    assert chain.new_signal.is_blocked is True
    assert chain.new_signal.block_reason == "global_cap_exceeded"


async def test_close_ts_from_close_full_update(test_db_path: str) -> None:
    """close_ts is set to the timestamp of the U_CLOSE_FULL update."""
    async with aiosqlite.connect(test_db_path) as db:
        db.row_factory = aiosqlite.Row
        op_id = await _setup_new_signal(db, tg_msg_id=100, message_ts="2025-06-01T10:00:00")
        await _setup_update(
            db,
            tg_msg_id=101,
            message_ts="2025-06-01T11:00:00",
            resolved_target_ids=[op_id],
            intents=[{"name": "U_TP_HIT", "kind": "CONTEXT"}],
        )
        await _setup_update(
            db,
            tg_msg_id=102,
            message_ts="2025-06-02T09:00:00",
            resolved_target_ids=[op_id],
            intents=[{"name": "U_CLOSE_FULL", "kind": "ACTION"}],
        )

    chains = await SignalChainBuilder.build_all_async(test_db_path)

    assert len(chains) == 1
    chain = chains[0]
    assert chain.close_ts is not None
    assert chain.close_ts.day == 2
    assert chain.close_ts.hour == 9
    assert len(chain.updates) == 2
