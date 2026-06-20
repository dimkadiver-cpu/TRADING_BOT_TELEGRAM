# tests/runtime_v2/control_plane/test_dashboard_manager.py
from __future__ import annotations

import asyncio
import sqlite3
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.runtime_v2.control_plane.dashboard_manager import DashboardManager, _matches_scope
from src.runtime_v2.control_plane.scope_resolver import QueryScope


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db(tmp_path) -> str:
    db_path = str(tmp_path / "test_ops.db")
    # Apply the ops_dashboard_messages migration
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ops_dashboard_messages (
            chat_id           INTEGER NOT NULL,
            thread_id         INTEGER NOT NULL DEFAULT 0,
            message_id        INTEGER NOT NULL,
            scope_account_id  TEXT NOT NULL,
            scope_trader_id   TEXT,
            current_view      TEXT NOT NULL DEFAULT 'attivi:0',
            updated_at        TEXT,
            PRIMARY KEY (chat_id, thread_id)
        )
        """
    )
    conn.commit()
    conn.close()
    return db_path


def _make_scope(account_id: str = "acc1", trader_ids: list[str] | None = None) -> QueryScope:
    return QueryScope(account_id=account_id, trader_ids=trader_ids)


def _make_mock_bot(message_id: int = 42) -> MagicMock:
    bot = MagicMock()
    sent_msg = MagicMock()
    sent_msg.message_id = message_id
    bot.send_message = AsyncMock(return_value=sent_msg)
    bot.edit_message_text = AsyncMock(return_value=None)
    return bot


def _make_queries_mock() -> MagicMock:
    """Create a minimal StatusQueries mock that returns safe empty views."""
    from src.runtime_v2.control_plane.status_queries import (
        TradesView,
        ClosedTradesView,
        BlockedTradesView,
    )
    queries = MagicMock()
    queries.get_open_trades.return_value = TradesView(
        updated_at="12:00:00", total=0, rows=[], mark_snapshot_max_age_seconds=None
    )
    queries.get_closed_trades.return_value = ClosedTradesView(
        updated_at="12:00:00", rows=[], total_count=0, page=0, page_size=5
    )
    queries.get_blocked_trades.return_value = BlockedTradesView(
        updated_at="12:00:00", rows=[]
    )
    return queries


def _make_scope_resolver_mock(scope: QueryScope) -> MagicMock:
    resolver = MagicMock()
    resolver.resolve.return_value = scope
    return resolver


def _make_manager(tmp_path, bot=None, queries=None, scope=None) -> DashboardManager:
    db_path = _make_db(tmp_path)
    if bot is None:
        bot = _make_mock_bot()
    if queries is None:
        queries = _make_queries_mock()
    if scope is None:
        scope = _make_scope()
    resolver = _make_scope_resolver_mock(scope)

    # Patch format_dashboard_view and build_dashboard_keyboard to avoid telegram dependency
    return DashboardManager(
        ops_db_path=db_path,
        scope_resolver=resolver,
        queries=queries,
        bot=bot,
    )


def _patch_render():
    """Patch render to avoid needing telegram installed."""
    from unittest.mock import patch as _patch

    fake_keyboard = MagicMock()

    def fake_format_dashboard_view(view_name, scope, queries, page=0, page_size=5):
        return (f"[{view_name} page={page}]", 0)

    def fake_build_keyboard(current_view, page, total_count, page_size=5):
        return fake_keyboard

    return (
        _patch(
            "src.runtime_v2.control_plane.dashboard_manager.DashboardManager._render_view",
            side_effect=lambda self_arg, scope, view, page: (f"[{view} page={page}]", fake_keyboard)
            if False
            else None,
        ),
        fake_keyboard,
    )


# Use a simpler approach: patch _render_view on the instance
def _patch_render_view(manager: DashboardManager):
    fake_keyboard = MagicMock()

    async def _noop(*args, **kwargs):
        pass

    def patched(scope, view, page):
        return (f"[{view} page={page}]", fake_keyboard)

    manager._render_view = patched  # type: ignore[method-assign]
    return fake_keyboard


# ---------------------------------------------------------------------------
# Test 1: create() saves row to DB
# ---------------------------------------------------------------------------

async def test_create_saves_to_db(tmp_path):
    bot = _make_mock_bot(message_id=99)
    manager = _make_manager(tmp_path, bot=bot)
    _patch_render_view(manager)

    scope = _make_scope("acc1", trader_ids=None)
    await manager.create(scope=scope, chat_id=123, thread_id=7)

    # Verify DB row
    conn = sqlite3.connect(manager._db)
    row = conn.execute(
        "SELECT message_id, scope_account_id, scope_trader_id, current_view "
        "FROM ops_dashboard_messages WHERE chat_id=123 AND thread_id=7"
    ).fetchone()
    conn.close()

    assert row is not None, "Row should be saved"
    assert row[0] == 99, f"message_id should be 99, got {row[0]}"
    assert row[1] == "acc1"
    assert row[2] is None  # account-level scope
    assert row[3] == "attivi:0"


# ---------------------------------------------------------------------------
# Test 2: on_trade_event updates account-level dashboard
# ---------------------------------------------------------------------------

async def test_on_trade_event_matches_account_scope(tmp_path):
    bot = _make_mock_bot(message_id=55)
    manager = _make_manager(tmp_path, bot=bot)
    _patch_render_view(manager)

    # Create dashboard with account-level scope (scope_trader_id=None)
    scope = _make_scope("acc1", trader_ids=None)
    await manager.create(scope=scope, chat_id=100, thread_id=0)

    # Reset edit mock call count
    bot.edit_message_text.reset_mock()
    # Clear last_edit time so it's not throttled
    manager._last_edit.clear()

    # Simulate trade event from any trader in the account
    await manager.on_trade_event(account_id="acc1", trader_id="trader_x")

    # Should have called edit_message_text
    assert bot.edit_message_text.call_count == 1, (
        f"Expected 1 edit call, got {bot.edit_message_text.call_count}"
    )


# ---------------------------------------------------------------------------
# Test 3: on_trade_event respects trader scope
# ---------------------------------------------------------------------------

async def test_on_trade_event_respects_trader_scope(tmp_path):
    bot = _make_mock_bot(message_id=66)
    manager = _make_manager(tmp_path, bot=bot)
    _patch_render_view(manager)

    # Create dashboard scoped to trader_a only
    scope = _make_scope("acc1", trader_ids=["trader_a"])
    await manager.create(scope=scope, chat_id=200, thread_id=0)

    bot.edit_message_text.reset_mock()
    manager._last_edit.clear()

    # Trade event from trader_b — should NOT trigger refresh
    await manager.on_trade_event(account_id="acc1", trader_id="trader_b")
    assert bot.edit_message_text.call_count == 0, "trader_b should not trigger trader_a dashboard"

    # Trade event from trader_a — SHOULD trigger refresh
    await manager.on_trade_event(account_id="acc1", trader_id="trader_a")
    assert bot.edit_message_text.call_count == 1, "trader_a should trigger refresh"


# ---------------------------------------------------------------------------
# Test 4: throttle 5s — second event within 5s is scheduled, not discarded
# ---------------------------------------------------------------------------

async def test_throttle_5s(tmp_path):
    bot = _make_mock_bot(message_id=77)
    manager = _make_manager(tmp_path, bot=bot)
    _patch_render_view(manager)

    scope = _make_scope("acc1", trader_ids=None)
    await manager.create(scope=scope, chat_id=300, thread_id=0)

    bot.edit_message_text.reset_mock()
    # Simulate that last edit was just now (0s elapsed)
    manager._last_edit[(300, 0)] = time.monotonic()

    # First event within throttle window
    await manager.on_trade_event(account_id="acc1", trader_id="t1")

    # Should not have edited immediately (throttled)
    assert bot.edit_message_text.call_count == 0, "Should be throttled"

    # But a pending task should exist
    assert (300, 0) in manager._pending_tasks, "A deferred task should have been scheduled"
    task = manager._pending_tasks[(300, 0)]
    assert not task.done(), "Task should still be pending"

    # Second event within throttle — should NOT create a duplicate task
    await manager.on_trade_event(account_id="acc1", trader_id="t2")
    assert manager._pending_tasks[(300, 0)] is task, "Should reuse existing pending task"

    # Cancel the task so it doesn't actually sleep
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass


# ---------------------------------------------------------------------------
# Test 5: handle_callback "view:chiusi" updates current_view in DB
# ---------------------------------------------------------------------------

async def test_handle_callback_view(tmp_path):
    bot = _make_mock_bot(message_id=88)
    manager = _make_manager(tmp_path, bot=bot)
    _patch_render_view(manager)

    scope = _make_scope("acc1", trader_ids=None)
    await manager.create(scope=scope, chat_id=400, thread_id=0)

    # Build a fake CallbackQuery
    fake_message = MagicMock()
    fake_message.chat_id = 400
    fake_message.message_thread_id = None  # → thread_id=0
    fake_message.message_id = 88

    fake_query = MagicMock()
    fake_query.message = fake_message

    await manager.handle_callback(fake_query, "view:chiusi")

    # Verify DB updated
    conn = sqlite3.connect(manager._db)
    row = conn.execute(
        "SELECT current_view FROM ops_dashboard_messages WHERE chat_id=400 AND thread_id=0"
    ).fetchone()
    conn.close()

    assert row is not None
    assert row[0] == "chiusi:0", f"Expected 'chiusi:0', got {row[0]!r}"


# ---------------------------------------------------------------------------
# Test _matches_scope logic directly
# ---------------------------------------------------------------------------

def test_matches_scope_account_level():
    assert _matches_scope("acc1", None, "acc1", "any_trader") is True
    assert _matches_scope("acc1", None, "acc2", "any_trader") is False


def test_matches_scope_trader_level():
    assert _matches_scope("acc1", "trader_a", "acc1", "trader_a") is True
    assert _matches_scope("acc1", "trader_a", "acc1", "trader_b") is False
    assert _matches_scope("acc1", "trader_a", "acc2", "trader_a") is False
