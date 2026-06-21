# src/runtime_v2/control_plane/dashboard_manager.py
from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.runtime_v2.control_plane.scope_resolver import QueryScope, ScopeResolver
    from src.runtime_v2.control_plane.status_queries import StatusQueries

logger = logging.getLogger(__name__)

_THROTTLE_SECONDS = 5.0
_DEFAULT_VIEW = "active"
_DEFAULT_PAGE = 0
_PAGE_SIZE = 5


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _matches_scope(
    scope_account_id: str | None,
    scope_trader_id: str | None,
    account_id: str,
    trader_id: str,
) -> bool:
    # Scope globale → si aggiorna sempre
    if scope_account_id is None:
        return True
    if scope_account_id != account_id:
        return False
    if scope_trader_id is None:
        return True  # account-level dashboard covers all traders
    return scope_trader_id == trader_id


def _parse_view(current_view: str) -> tuple[str, int]:
    """Parse 'view_name:page' → (view_name, page). Defaults to ('attivi', 0)."""
    if ":" in current_view:
        view_part, page_part = current_view.rsplit(":", 1)
        try:
            return view_part, int(page_part)
        except ValueError:
            return view_part, 0
    return current_view, 0


def _encode_view(view_name: str, page: int) -> str:
    return f"{view_name}:{page}"


class DashboardManager:
    def __init__(
        self,
        *,
        ops_db_path: str,
        scope_resolver: ScopeResolver,
        queries: StatusQueries,
        bot,  # Bot telegram (lazy — can be None in tests)
    ) -> None:
        self._db = ops_db_path
        self._scope_resolver = scope_resolver
        self._queries = queries
        self._bot = bot

        # Throttle state: (chat_id, thread_id) → last_edit_time
        self._last_edit: dict[tuple[int, int], float] = {}
        # Pending scheduled tasks: (chat_id, thread_id) → asyncio.Task
        self._pending_tasks: dict[tuple[int, int], asyncio.Task] = {}

        self._ensure_table()

    def set_bot(self, bot) -> None:
        """Wire the bot after creation (called from bootstrap after TelegramControlBot is built)."""
        self._bot = bot

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db)

    def _ensure_table(self) -> None:
        """Create ops_dashboard_messages table if not present.

        If the table exists with scope_account_id TEXT NOT NULL (legacy schema),
        recreate it without the NOT NULL constraint so global scope (NULL) can be stored.
        """
        conn = self._connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ops_dashboard_messages (
                    chat_id           INTEGER NOT NULL,
                    thread_id         INTEGER NOT NULL DEFAULT 0,
                    message_id        INTEGER NOT NULL,
                    scope_account_id  TEXT,
                    scope_trader_id   TEXT,
                    current_view      TEXT NOT NULL DEFAULT 'attivi:0',
                    updated_at        TEXT,
                    PRIMARY KEY (chat_id, thread_id)
                )
                """
            )
            conn.commit()

            # Migration: if scope_account_id was created NOT NULL, recreate the table.
            # SQLite does not support ALTER COLUMN, so we use the rename-and-copy pattern.
            row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='ops_dashboard_messages'"
            ).fetchone()
            if row and "scope_account_id  TEXT NOT NULL" in row[0]:
                conn.executescript(
                    """
                    BEGIN;
                    CREATE TABLE ops_dashboard_messages_new (
                        chat_id           INTEGER NOT NULL,
                        thread_id         INTEGER NOT NULL DEFAULT 0,
                        message_id        INTEGER NOT NULL,
                        scope_account_id  TEXT,
                        scope_trader_id   TEXT,
                        current_view      TEXT NOT NULL DEFAULT 'active:0',
                        updated_at        TEXT,
                        PRIMARY KEY (chat_id, thread_id)
                    );
                    INSERT INTO ops_dashboard_messages_new
                        SELECT chat_id, thread_id, message_id, scope_account_id,
                               scope_trader_id, current_view, updated_at
                        FROM ops_dashboard_messages;
                    DROP TABLE ops_dashboard_messages;
                    ALTER TABLE ops_dashboard_messages_new RENAME TO ops_dashboard_messages;
                    COMMIT;
                    """
                )
                logger.info("ops_dashboard_messages migrated: scope_account_id no longer NOT NULL")

            # Migration IT→EN: rename legacy Italian view names to English
            conn.execute(
                """
                UPDATE ops_dashboard_messages
                SET current_view = REPLACE(REPLACE(REPLACE(current_view,
                    'attivi', 'active'),
                    'chiusi', 'closed'),
                    'bloccati', 'blocked')
                WHERE current_view LIKE '%attivi%'
                   OR current_view LIKE '%chiusi%'
                   OR current_view LIKE '%bloccati%'
                """
            )
            conn.commit()

            # Migration: add filters_json column if not present
            columns = {row[1] for row in conn.execute("PRAGMA table_info(ops_dashboard_messages)")}
            if "filters_json" not in columns:
                conn.execute(
                    "ALTER TABLE ops_dashboard_messages ADD COLUMN filters_json TEXT DEFAULT NULL"
                )
                conn.commit()
        finally:
            conn.close()

    def _save_dashboard(
        self,
        chat_id: int,
        thread_id: int,
        message_id: int,
        scope: QueryScope,
        current_view: str,
    ) -> None:
        trader_id: str | None = None
        if scope.trader_ids and len(scope.trader_ids) == 1:
            trader_id = scope.trader_ids[0]

        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO ops_dashboard_messages
                    (chat_id, thread_id, message_id, scope_account_id, scope_trader_id,
                     current_view, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id, thread_id) DO UPDATE SET
                    message_id = excluded.message_id,
                    scope_account_id = excluded.scope_account_id,
                    scope_trader_id = excluded.scope_trader_id,
                    current_view = excluded.current_view,
                    updated_at = excluded.updated_at
                """,
                (
                    chat_id,
                    thread_id,
                    message_id,
                    scope.account_id,
                    trader_id,
                    current_view,
                    _now_iso(),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _update_current_view(
        self,
        chat_id: int,
        thread_id: int,
        current_view: str,
    ) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE ops_dashboard_messages SET current_view=?, updated_at=? "
                "WHERE chat_id=? AND thread_id=?",
                (current_view, _now_iso(), chat_id, thread_id),
            )
            conn.commit()
        finally:
            conn.close()

    def _get_dashboard_row(
        self,
        chat_id: int,
        thread_id: int,
    ) -> tuple[int, str, str | None, str] | None:
        """Return (message_id, scope_account_id, scope_trader_id, current_view) or None."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT message_id, scope_account_id, scope_trader_id, current_view "
                "FROM ops_dashboard_messages WHERE chat_id=? AND thread_id=?",
                (chat_id, thread_id),
            ).fetchone()
            return row  # type: ignore[return-value]
        finally:
            conn.close()

    def _update_filters_json(self, chat_id: int, thread_id: int, filters_json: str | None) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE ops_dashboard_messages SET filters_json=?, updated_at=? "
                "WHERE chat_id=? AND thread_id=?",
                (filters_json, _now_iso(), chat_id, thread_id),
            )
            conn.commit()
        finally:
            conn.close()

    def _get_filters_json(self, chat_id: int, thread_id: int) -> str | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT filters_json FROM ops_dashboard_messages WHERE chat_id=? AND thread_id=?",
                (chat_id, thread_id),
            ).fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    def _clear_filters(self, chat_id: int, thread_id: int) -> None:
        self._update_filters_json(chat_id, thread_id, None)

    def _get_all_dashboards(self) -> list[tuple[int, int, int, str, str | None, str]]:
        """Return all rows: (chat_id, thread_id, message_id, scope_account_id, scope_trader_id, current_view)."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT chat_id, thread_id, message_id, scope_account_id, scope_trader_id, current_view "
                "FROM ops_dashboard_messages"
            ).fetchall()
            return rows  # type: ignore[return-value]
        finally:
            conn.close()

    def _render_view(
        self,
        scope: QueryScope,
        view: str,
        page: int,
    ) -> tuple[str, object]:
        """Call format_dashboard_view + build_dashboard_keyboard."""
        from src.runtime_v2.control_plane.formatters.dashboard import (
            build_dashboard_keyboard,
            format_dashboard_view,
        )

        text, total_count = format_dashboard_view(
            view, scope, self._queries, page=page, page_size=_PAGE_SIZE
        )
        keyboard = build_dashboard_keyboard(view, page, total_count, page_size=_PAGE_SIZE)
        return text, keyboard

    async def create(
        self,
        scope: QueryScope,
        chat_id: int,
        thread_id: int,
    ) -> None:
        """Send initial message (attivi:0 view), save to ops_dashboard_messages.
        If a record already exists for (chat_id, thread_id), overwrite it
        (new message, old message abandoned).
        """
        if self._bot is None:
            logger.warning("DashboardManager.create: bot is None, skipping send")
            return

        text, keyboard = self._render_view(scope, _DEFAULT_VIEW, _DEFAULT_PAGE)
        kwargs: dict = {
            "chat_id": chat_id,
            "text": text,
            "reply_markup": keyboard,
        }
        if thread_id != 0:
            kwargs["message_thread_id"] = thread_id

        msg = await self._bot.send_message(**kwargs)
        message_id: int = msg.message_id

        current_view = _encode_view(_DEFAULT_VIEW, _DEFAULT_PAGE)
        self._save_dashboard(chat_id, thread_id, message_id, scope, current_view)
        self._last_edit[(chat_id, thread_id)] = time.monotonic()
        logger.debug("Dashboard created chat=%s thread=%s msg=%s", chat_id, thread_id, message_id)

    async def handle_callback(
        self,
        callback_query,  # python-telegram-bot CallbackQuery
        callback_data: str,
    ) -> None:
        """Parse callback_data: 'view:{name}', 'page:prev', 'page:next', 'noop', 'refresh'.
        Update current_view in DB, edit message + keyboard.
        """
        message = callback_query.message
        if message is None:
            return

        chat_id: int = message.chat_id
        thread_id: int = message.message_thread_id or 0

        row = self._get_dashboard_row(chat_id, thread_id)
        if row is None:
            logger.debug("handle_callback: no dashboard row for chat=%s thread=%s", chat_id, thread_id)
            return

        stored_message_id, scope_account_id, scope_trader_id, current_view_str = row

        # Reconstruct scope from DB
        from src.runtime_v2.control_plane.scope_resolver import QueryScope

        if scope_account_id is None:
            scope = QueryScope(account_id=None, trader_ids=None)
        elif scope_trader_id is not None:
            scope = QueryScope(account_id=scope_account_id, trader_ids=[scope_trader_id])
        else:
            scope = QueryScope(account_id=scope_account_id, trader_ids=None)

        current_view_name, current_page = _parse_view(current_view_str)

        if callback_data == "noop":
            return

        if callback_data.startswith("view:"):
            new_view = callback_data[5:]
            new_page = 0
        elif callback_data == "page:prev":
            new_view = current_view_name
            new_page = max(0, current_page - 1)
        elif callback_data == "page:next":
            new_view = current_view_name
            new_page = current_page + 1
        elif callback_data == "refresh":
            new_view = current_view_name
            new_page = current_page
        elif callback_data == "filters":
            await self._show_filters_panel(
                callback_query=callback_query,
                chat_id=chat_id,
                thread_id=thread_id,
                stored_message_id=stored_message_id,
                current_view_name=current_view_name,
                scope=scope,
            )
            return
        elif callback_data == "clear":
            self._clear_filters(chat_id, thread_id)
            new_view = current_view_name
            new_page = 0
        elif callback_data == "selector:back":
            new_view = current_view_name
            new_page = current_page
        elif callback_data.startswith("selector:"):
            parts = callback_data.split(":", 2)
            if len(parts) == 3:
                import json as _json
                _, filter_type, filter_value = parts
                raw = self._get_filters_json(chat_id, thread_id)
                try:
                    current_filters = _json.loads(raw) if raw else {}
                except Exception:
                    current_filters = {}
                if filter_value in ("all", ""):
                    current_filters.pop(filter_type, None)
                else:
                    current_filters[filter_type] = filter_value
                new_json = _json.dumps(current_filters) if current_filters else None
                self._update_filters_json(chat_id, thread_id, new_json)
            new_view = current_view_name
            new_page = 0
        else:
            return

        new_current_view = _encode_view(new_view, new_page)
        self._update_current_view(chat_id, thread_id, new_current_view)

        text, keyboard = self._render_view(scope, new_view, new_page)

        if self._bot is None:
            return

        try:
            await self._bot.edit_message_text(
                chat_id=chat_id,
                message_id=stored_message_id,
                text=text,
                reply_markup=keyboard,
            )
            self._last_edit[(chat_id, thread_id)] = time.monotonic()
        except Exception as exc:  # noqa: BLE001
            exc_str = str(exc)
            if "Message is not modified" in exc_str:
                logger.debug("handle_callback: message not modified chat=%s thread=%s", chat_id, thread_id)
            else:
                logger.warning("handle_callback: edit failed chat=%s thread=%s: %s", chat_id, thread_id, exc)

    async def _show_filters_panel(
        self,
        *,
        callback_query,
        chat_id: int,
        thread_id: int,
        stored_message_id: int,
        current_view_name: str,
        scope,
    ) -> None:
        """Edit the dashboard message to show a filter selector panel."""
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup  # noqa: PLC0415

        view_labels = {
            "active": "Active",
            "closed": "Closed",
            "blocked": "Blocked",
            "pnl": "PnL",
            "stats": "Stats",
        }
        label = view_labels.get(current_view_name, current_view_name.capitalize())
        text = f"🔎 Filters — {label}"

        rows: list[list[InlineKeyboardButton]] = []

        if current_view_name in ("active", "closed", "blocked", "pnl", "stats"):
            rows.append([
                InlineKeyboardButton("Account ▸", callback_data="selector_panel:account"),
                InlineKeyboardButton("Trader ▸", callback_data="selector_panel:trader"),
            ])
        if current_view_name == "active":
            rows.append([InlineKeyboardButton("Status ▸", callback_data="selector_panel:status")])
        if current_view_name in ("active", "stats"):
            rows.append([InlineKeyboardButton("Side ▸", callback_data="selector_panel:side")])
        if current_view_name in ("closed", "pnl"):
            rows.append([InlineKeyboardButton("Period ▸", callback_data="selector_panel:period")])

        rows.append([
            InlineKeyboardButton("🧹 Clear view", callback_data="clear"),
            InlineKeyboardButton("← Back", callback_data="selector:back"),
        ])

        keyboard = InlineKeyboardMarkup(rows)
        if self._bot:
            try:
                await self._bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=stored_message_id,
                    text=text,
                    reply_markup=keyboard,
                )
            except Exception:  # noqa: BLE001
                pass

    async def on_trade_event(
        self,
        account_id: str,
        trader_id: str,
    ) -> None:
        """After each CLEAN_LOG sent: find dashboards in scope, update relevant ones.
        Respect 5s throttle per message. Do not change current_view or page.
        """
        rows = self._get_all_dashboards()

        for chat_id, thread_id, message_id, scope_account_id, scope_trader_id, current_view_str in rows:
            if not _matches_scope(scope_account_id, scope_trader_id, account_id, trader_id):
                continue

            key = (chat_id, thread_id)
            now = time.monotonic()
            last = self._last_edit.get(key, 0.0)
            elapsed = now - last

            if elapsed >= _THROTTLE_SECONDS:
                # Can send now
                await self._do_refresh(
                    chat_id=chat_id,
                    thread_id=thread_id,
                    message_id=message_id,
                    scope_account_id=scope_account_id,
                    scope_trader_id=scope_trader_id,
                    current_view_str=current_view_str,
                )
            else:
                # Schedule for after cooldown (don't duplicate)
                if key not in self._pending_tasks or self._pending_tasks[key].done():
                    delay = _THROTTLE_SECONDS - elapsed
                    task = asyncio.get_running_loop().create_task(
                        self._deferred_refresh(
                            delay=delay,
                            chat_id=chat_id,
                            thread_id=thread_id,
                            message_id=message_id,
                            scope_account_id=scope_account_id,
                            scope_trader_id=scope_trader_id,
                            current_view_str=current_view_str,
                        )
                    )
                    self._pending_tasks[key] = task
                # else: already a pending task — it will pick up the latest current_view when it fires

    async def _deferred_refresh(
        self,
        *,
        delay: float,
        chat_id: int,
        thread_id: int,
        message_id: int,
        scope_account_id: str | None,
        scope_trader_id: str | None,
        current_view_str: str,
    ) -> None:
        await asyncio.sleep(delay)
        # Re-read current_view from DB (it may have changed due to user interaction)
        row = self._get_dashboard_row(chat_id, thread_id)
        if row is None:
            return
        stored_message_id, _, _, current_view_str = row
        await self._do_refresh(
            chat_id=chat_id,
            thread_id=thread_id,
            message_id=stored_message_id,
            scope_account_id=scope_account_id,
            scope_trader_id=scope_trader_id,
            current_view_str=current_view_str,
        )

    async def _do_refresh(
        self,
        *,
        chat_id: int,
        thread_id: int,
        message_id: int,
        scope_account_id: str | None,
        scope_trader_id: str | None,
        current_view_str: str,
    ) -> None:
        if self._bot is None:
            return

        from src.runtime_v2.control_plane.scope_resolver import QueryScope

        if scope_account_id is None:
            scope = QueryScope(account_id=None, trader_ids=None)
        elif scope_trader_id is not None:
            scope = QueryScope(account_id=scope_account_id, trader_ids=[scope_trader_id])
        else:
            scope = QueryScope(account_id=scope_account_id, trader_ids=None)

        view_name, page = _parse_view(current_view_str)

        try:
            text, keyboard = self._render_view(scope, view_name, page)
        except Exception:
            logger.exception("_do_refresh: render failed chat=%s thread=%s", chat_id, thread_id)
            return

        try:
            await self._bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=keyboard,
            )
            self._last_edit[(chat_id, thread_id)] = time.monotonic()
        except Exception as exc:  # noqa: BLE001
            exc_str = str(exc)
            if "Message is not modified" in exc_str:
                logger.debug("_do_refresh: message not modified chat=%s thread=%s", chat_id, thread_id)
            else:
                logger.warning("_do_refresh: edit failed chat=%s thread=%s: %s", chat_id, thread_id, exc)


__all__ = ["DashboardManager"]
