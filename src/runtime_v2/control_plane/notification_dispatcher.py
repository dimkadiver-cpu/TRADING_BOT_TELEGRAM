# src/runtime_v2/control_plane/notification_dispatcher.py
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Protocol

from src.runtime_v2.control_plane.formatters.clean_log import format_clean_log
from src.runtime_v2.control_plane.models import ControlPlaneConfig
from src.runtime_v2.control_plane.topic_router import TopicRouter

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 3


class NotificationSender(Protocol):
    async def send(
        self, *, chat_id: int, thread_id: int | None, text: str, silent: bool = False
    ) -> None: ...


class TelegramBotSender:
    """Real sender backed by python-telegram-bot's Bot."""

    def __init__(self, bot) -> None:
        self._bot = bot

    async def send(self, *, chat_id: int, thread_id: int | None, text: str, silent: bool = False) -> None:
        kwargs: dict = {
            "chat_id": chat_id,
            "text": text,
            "disable_notification": silent,
        }
        if thread_id is not None:
            kwargs["message_thread_id"] = thread_id
        await self._bot.send_message(**kwargs)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TelegramNotificationDispatcher:
    def __init__(
        self,
        *,
        config: ControlPlaneConfig,
        ops_db_path: str,
        topic_router: TopicRouter,
        sender: NotificationSender,
        poll_interval_seconds: float = 2.0,
        batch_size: int = 50,
    ) -> None:
        self._config = config
        self._ops_db = ops_db_path
        self._router = topic_router
        self._sender = sender
        self._poll = poll_interval_seconds
        self._batch = batch_size

    def _claim_pending(self) -> list[tuple]:
        conn = sqlite3.connect(self._ops_db, isolation_level=None)
        try:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """
                SELECT notification_id, notification_type, destination, payload_json, attempts
                FROM ops_notification_outbox
                WHERE status='PENDING'
                ORDER BY CASE priority WHEN 'HIGH' THEN 0 WHEN 'MEDIUM' THEN 1 ELSE 2 END,
                         created_at, notification_id
                LIMIT ?
                """,
                (self._batch,),
            ).fetchall()
            conn.execute("COMMIT")
            return rows
        finally:
            conn.close()

    def _mark_sent(self, notification_id: int) -> None:
        conn = sqlite3.connect(self._ops_db)
        try:
            conn.execute(
                "UPDATE ops_notification_outbox SET status='SENT', sent_at=? WHERE notification_id=?",
                (_now(), notification_id),
            )
            conn.commit()
        finally:
            conn.close()

    def _mark_failure(self, notification_id: int, attempts: int, error: str) -> None:
        new_attempts = attempts + 1
        status = "FAILED" if new_attempts >= _MAX_ATTEMPTS else "PENDING"
        conn = sqlite3.connect(self._ops_db)
        try:
            conn.execute(
                "UPDATE ops_notification_outbox "
                "SET attempts=?, last_error=?, status=? WHERE notification_id=?",
                (new_attempts, error[:500], status, notification_id),
            )
            conn.commit()
        finally:
            conn.close()

    def _render(self, destination: str, notification_type: str, payload: dict) -> str:
        if destination == "CLEAN_LOG":
            return format_clean_log(notification_type, payload)
        # TECH_LOG / COMMANDS_REPLY formatters arrive in later parts; safe fallback.
        return payload.get("text") or f"{notification_type}"

    def _is_silent(self, notification_type: str) -> bool:
        key_map = {
            "ENTRY_OPENED": "entry_filled",
            "TP_FILLED": "tp_filled",
            "TP_FILLED_FINAL": "tp_filled",
            "SL_FILLED": "sl_filled",
            "POSITION_CLOSED": "close_full_filled",
        }
        pref = self._config.notifications.get(key_map.get(notification_type, ""), "on")
        return pref == "silent"

    async def drain_once(self) -> int:
        rows = self._claim_pending()
        sent = 0
        for notification_id, notification_type, destination, payload_json, attempts in rows:
            try:
                payload = json.loads(payload_json or "{}")
            except Exception:
                payload = {}
            try:
                chat_id, thread_id = self._router.route(destination)
                text = self._render(destination, notification_type, payload)
                silent = self._is_silent(notification_type)
                await self._sender.send(
                    chat_id=chat_id, thread_id=thread_id, text=text, silent=silent
                )
                self._mark_sent(notification_id)
                sent += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("notification %s send failed: %s", notification_id, exc)
                self._mark_failure(notification_id, attempts, str(exc))
        return sent

    async def run(self) -> None:
        while True:
            try:
                await self.drain_once()
            except Exception:
                logger.exception("dispatcher drain error")
            await asyncio.sleep(self._poll)

    async def shutdown(self) -> None:
        return None


__all__ = [
    "TelegramNotificationDispatcher",
    "NotificationSender",
    "TelegramBotSender",
]
