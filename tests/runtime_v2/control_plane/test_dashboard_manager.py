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
from src.runtime_v2.control_plane.status_queries import StatusQueries


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
            scope_account_id  TEXT,            -- NULL = scope globale
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


def _make_global_scope() -> QueryScope:
    return QueryScope(account_id=None, trader_ids=None)


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
    assert row[3] == "active:0"  # English view name after migration


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

    await manager.handle_callback(fake_query, "view:closed")

    # Verify DB updated
    conn = sqlite3.connect(manager._db)
    row = conn.execute(
        "SELECT current_view FROM ops_dashboard_messages WHERE chat_id=400 AND thread_id=0"
    ).fetchone()
    conn.close()

    assert row is not None
    assert row[0] == "closed:0", f"Expected 'closed:0', got {row[0]!r}"


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


def test_matches_scope_global_always_true():
    """scope_account_id=None matches any account and any trader."""
    assert _matches_scope(None, None, "account_A", "trader_a") is True
    assert _matches_scope(None, None, "account_B", "trader_x") is True
    assert _matches_scope(None, None, "demo_2", "trader_devos_crypto") is True


def test_matches_scope_specific_account_still_works():
    """Non-global scopes remain filtered as before."""
    assert _matches_scope("acc1", None, "acc1", "any_trader") is True
    assert _matches_scope("acc1", None, "acc2", "any_trader") is False
    assert _matches_scope("acc1", "t_a", "acc1", "t_a") is True
    assert _matches_scope("acc1", "t_a", "acc1", "t_b") is False


# ---------------------------------------------------------------------------
# Test 6: create() with global scope saves NULL account_id
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_global_scope_saves_null_account_id(tmp_path):
    """Dashboard with global scope saves scope_account_id=NULL in DB."""
    manager = _make_manager(tmp_path, scope=_make_global_scope())
    _patch_render_view(manager)

    await manager.create(
        scope=QueryScope(account_id=None, trader_ids=None),
        chat_id=-100,
        thread_id=4,
    )

    conn = sqlite3.connect(manager._db)
    row = conn.execute(
        "SELECT scope_account_id, scope_trader_id FROM ops_dashboard_messages "
        "WHERE chat_id=? AND thread_id=?",
        (-100, 4),
    ).fetchone()
    conn.close()

    assert row is not None
    assert row[0] is None   # scope_account_id = NULL
    assert row[1] is None   # scope_trader_id = NULL


# ---------------------------------------------------------------------------
# Test 7: on_trade_event triggers global dashboard for any account/trader
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_on_trade_event_triggers_global_dashboard(tmp_path):
    """A global dashboard is refreshed for any account/trader trade event."""
    manager = _make_manager(tmp_path, scope=_make_global_scope())
    fake_keyboard = _patch_render_view(manager)

    # Manually insert a global dashboard row
    conn = sqlite3.connect(manager._db)
    conn.execute(
        "INSERT INTO ops_dashboard_messages "
        "(chat_id, thread_id, message_id, scope_account_id, scope_trader_id, current_view) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (-100, 4, 99, None, None, "active:0"),
    )
    conn.commit()
    conn.close()

    # Trade event from a completely different account — global dashboard must refresh
    await manager.on_trade_event(account_id="account_Z", trader_id="trader_z")

    manager._bot.edit_message_text.assert_awaited_once()


# ---------------------------------------------------------------------------
# Test 8: DashboardManager migrates Italian view names to English on boot
# ---------------------------------------------------------------------------

def test_dashboard_naming_migration(tmp_path):
    """DashboardManager migrates 'attivi:0' → 'active:0' on boot (_ensure_table)."""
    db_path = str(tmp_path / "ops.sqlite3")
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE ops_dashboard_messages (
            chat_id INTEGER NOT NULL,
            thread_id INTEGER NOT NULL DEFAULT 0,
            message_id INTEGER NOT NULL,
            scope_account_id TEXT,
            scope_trader_id TEXT,
            current_view TEXT NOT NULL DEFAULT 'attivi:0',
            updated_at TEXT,
            PRIMARY KEY (chat_id, thread_id)
        )
    """)
    conn.execute("INSERT INTO ops_dashboard_messages VALUES (1,0,42,NULL,NULL,'attivi:0',NULL)")
    conn.execute("INSERT INTO ops_dashboard_messages VALUES (2,0,43,NULL,NULL,'chiusi:2',NULL)")
    conn.execute("INSERT INTO ops_dashboard_messages VALUES (3,0,44,NULL,NULL,'bloccati:0',NULL)")
    conn.commit()
    conn.close()

    mgr = DashboardManager(
        ops_db_path=db_path,
        scope_resolver=MagicMock(),
        queries=MagicMock(spec=StatusQueries),
        bot=None,
    )
    conn2 = sqlite3.connect(db_path)
    rows = conn2.execute(
        "SELECT chat_id, current_view FROM ops_dashboard_messages ORDER BY chat_id"
    ).fetchall()
    conn2.close()
    assert rows[0][1] == "active:0"
    assert rows[1][1] == "closed:2"
    assert rows[2][1] == "blocked:0"


# ---------------------------------------------------------------------------
# Task 5: filters_json column and filter helpers
# ---------------------------------------------------------------------------

def test_filters_json_column_exists(tmp_path):
    """DashboardManager creates ops_dashboard_messages with filters_json column."""
    db_path = _make_db(tmp_path)
    DashboardManager(
        ops_db_path=db_path,
        scope_resolver=MagicMock(),
        queries=MagicMock(),
        bot=None,
    )
    conn = sqlite3.connect(db_path)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(ops_dashboard_messages)")}
    conn.close()
    assert "filters_json" in columns


def test_filters_json_column_added_to_existing_table(tmp_path):
    """DashboardManager adds filters_json via ALTER TABLE when table exists without it."""
    db_path = _make_db(tmp_path)  # creates table without filters_json
    # Verify it doesn't already have filters_json
    conn = sqlite3.connect(db_path)
    columns_before = {row[1] for row in conn.execute("PRAGMA table_info(ops_dashboard_messages)")}
    conn.close()
    assert "filters_json" not in columns_before

    # Boot DashboardManager — should add the column
    DashboardManager(
        ops_db_path=db_path,
        scope_resolver=MagicMock(),
        queries=MagicMock(),
        bot=None,
    )
    conn = sqlite3.connect(db_path)
    columns_after = {row[1] for row in conn.execute("PRAGMA table_info(ops_dashboard_messages)")}
    conn.close()
    assert "filters_json" in columns_after


def test_clear_callback_resets_filters(tmp_path):
    """_clear_filters sets filters_json to NULL."""
    db_path = _make_db(tmp_path)
    mgr = DashboardManager(
        ops_db_path=db_path,
        scope_resolver=MagicMock(),
        queries=MagicMock(),
        bot=None,
    )
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_dashboard_messages VALUES (1,0,42,NULL,NULL,'active:0','{}',NULL)"
    )
    conn.commit()
    conn.close()

    # Set then clear filters
    mgr._update_filters_json(1, 0, '{"trader": "trader_a"}')
    mgr._clear_filters(1, 0)

    row = mgr._get_dashboard_row(1, 0)
    assert row is not None
    filters_json = mgr._get_filters_json(1, 0)
    assert filters_json is None


def test_selector_callback_sets_filter(tmp_path):
    """_update_filters_json + _get_filters_json roundtrip."""
    db_path = _make_db(tmp_path)
    mgr = DashboardManager(
        ops_db_path=db_path,
        scope_resolver=MagicMock(),
        queries=MagicMock(),
        bot=None,
    )
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_dashboard_messages VALUES (1,0,42,NULL,NULL,'active:0','{}',NULL)"
    )
    conn.commit()
    conn.close()

    mgr._update_filters_json(1, 0, '{"trader": "trader_a"}')
    result = mgr._get_filters_json(1, 0)
    assert result == '{"trader": "trader_a"}'


@pytest.mark.asyncio
async def test_filters_callback_shows_panel(tmp_path):
    """handle_callback('filters') calls bot.edit_message_text with filter panel text."""
    db_path = _make_db(tmp_path)
    bot = _make_mock_bot(message_id=88)
    mgr = DashboardManager(
        ops_db_path=db_path,
        scope_resolver=MagicMock(),
        queries=MagicMock(),
        bot=bot,
    )
    _patch_render_view(mgr)

    # Insert a dashboard row so handle_callback finds it
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_dashboard_messages VALUES (100,0,88,NULL,NULL,'active:0','{}',NULL)"
    )
    conn.commit()
    conn.close()

    fake_message = MagicMock()
    fake_message.chat_id = 100
    fake_message.message_thread_id = None
    fake_message.message_id = 88

    fake_query = MagicMock()
    fake_query.message = fake_message

    await mgr.handle_callback(fake_query, "filters")

    # bot.edit_message_text should be called with text containing "🔎 Filters"
    bot.edit_message_text.assert_awaited_once()
    call_kwargs = bot.edit_message_text.call_args
    text_arg = call_kwargs.kwargs.get("text") or (call_kwargs.args[0] if call_kwargs.args else "")
    assert "🔎 Filters" in text_arg


@pytest.mark.asyncio
async def test_clear_callback_resets_filters_and_rerenders(tmp_path):
    """handle_callback('clear') resets filters_json and re-renders the dashboard."""
    db_path = _make_db(tmp_path)
    bot = _make_mock_bot(message_id=77)
    mgr = DashboardManager(
        ops_db_path=db_path,
        scope_resolver=MagicMock(),
        queries=MagicMock(),
        bot=bot,
    )
    _patch_render_view(mgr)

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_dashboard_messages VALUES (200,0,77,NULL,NULL,'active:0','{}','2024-01-01')"
    )
    conn.commit()
    conn.close()

    # Set some filters first
    mgr._update_filters_json(200, 0, '{"trader": "trader_a"}')

    fake_message = MagicMock()
    fake_message.chat_id = 200
    fake_message.message_thread_id = None
    fake_message.message_id = 77

    fake_query = MagicMock()
    fake_query.message = fake_message

    await mgr.handle_callback(fake_query, "clear")

    # filters_json should be cleared
    assert mgr._get_filters_json(200, 0) is None
    # Dashboard should be re-rendered (edit_message_text called)
    bot.edit_message_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_selector_back_callback_rerenders(tmp_path):
    """handle_callback('selector:back') re-renders without changing view or page."""
    db_path = _make_db(tmp_path)
    bot = _make_mock_bot(message_id=55)
    mgr = DashboardManager(
        ops_db_path=db_path,
        scope_resolver=MagicMock(),
        queries=MagicMock(),
        bot=bot,
    )
    _patch_render_view(mgr)

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_dashboard_messages VALUES (300,0,55,NULL,NULL,'closed:1','{}','2024-01-01')"
    )
    conn.commit()
    conn.close()

    fake_message = MagicMock()
    fake_message.chat_id = 300
    fake_message.message_thread_id = None

    fake_query = MagicMock()
    fake_query.message = fake_message

    await mgr.handle_callback(fake_query, "selector:back")

    # view should remain closed:1
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT current_view FROM ops_dashboard_messages WHERE chat_id=300"
    ).fetchone()
    conn.close()
    assert row[0] == "closed:1"
    bot.edit_message_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_selector_set_filter_callback(tmp_path):
    """handle_callback('selector:trader:trader_a') stores the filter and re-renders."""
    db_path = _make_db(tmp_path)
    bot = _make_mock_bot(message_id=66)
    mgr = DashboardManager(
        ops_db_path=db_path,
        scope_resolver=MagicMock(),
        queries=MagicMock(),
        bot=bot,
    )
    _patch_render_view(mgr)

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_dashboard_messages VALUES (400,0,66,NULL,NULL,'active:0','{}','2024-01-01')"
    )
    conn.commit()
    conn.close()

    fake_message = MagicMock()
    fake_message.chat_id = 400
    fake_message.message_thread_id = None

    fake_query = MagicMock()
    fake_query.message = fake_message

    await mgr.handle_callback(fake_query, "selector:trader:trader_a")

    import json
    raw = mgr._get_filters_json(400, 0)
    assert raw is not None
    filters = json.loads(raw)
    assert filters.get("trader") == "trader_a"
    bot.edit_message_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_selector_all_removes_filter(tmp_path):
    """handle_callback('selector:trader:all') removes the trader filter."""
    db_path = _make_db(tmp_path)
    bot = _make_mock_bot(message_id=44)
    mgr = DashboardManager(
        ops_db_path=db_path,
        scope_resolver=MagicMock(),
        queries=MagicMock(),
        bot=bot,
    )
    _patch_render_view(mgr)

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_dashboard_messages VALUES (500,0,44,NULL,NULL,'active:0','{}','2024-01-01')"
    )
    conn.commit()
    conn.close()

    mgr._update_filters_json(500, 0, '{"trader": "trader_a"}')

    fake_message = MagicMock()
    fake_message.chat_id = 500
    fake_message.message_thread_id = None

    fake_query = MagicMock()
    fake_query.message = fake_message

    await mgr.handle_callback(fake_query, "selector:trader:all")

    raw = mgr._get_filters_json(500, 0)
    # Either None or empty dict — trader key must be gone
    if raw:
        import json
        filters = json.loads(raw)
        assert "trader" not in filters
    # else None is fine
