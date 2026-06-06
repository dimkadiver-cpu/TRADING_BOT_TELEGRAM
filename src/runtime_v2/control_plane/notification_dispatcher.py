# src/runtime_v2/control_plane/notification_dispatcher.py
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Protocol

from src.runtime_v2.control_plane.formatters.clean_log import format_clean_log
from src.runtime_v2.control_plane.formatters.tech_log import format_tech_log
from src.runtime_v2.control_plane.models import ControlPlaneConfig
from src.runtime_v2.control_plane.outbox_writer import try_release_pending_close_full_summaries
from src.runtime_v2.control_plane.topic_router import TopicRouter

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 3


class NotificationSender(Protocol):
    async def send(
        self,
        *,
        chat_id: int,
        thread_id: int | None,
        text: str,
        silent: bool = False,
        reply_to_message_id: str | None = None,
    ) -> str | None: ...


class TelegramBotSender:
    """Real sender backed by python-telegram-bot's Bot."""

    def __init__(self, bot) -> None:
        self._bot = bot

    async def send(
        self,
        *,
        chat_id: int,
        thread_id: int | None,
        text: str,
        silent: bool = False,
        reply_to_message_id: str | None = None,
    ) -> str | None:
        kwargs: dict = {
            "chat_id": chat_id,
            "text": text,
            "disable_notification": silent,
        }
        if thread_id is not None:
            kwargs["message_thread_id"] = thread_id
        if reply_to_message_id is not None:
            kwargs["reply_to_message_id"] = int(reply_to_message_id)
        msg = await self._bot.send_message(**kwargs)
        return str(msg.message_id)


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
        debug_status: Callable[[], bool] | None = None,
    ) -> None:
        self._config = config
        self._ops_db = ops_db_path
        self._router = topic_router
        self._sender = sender
        self._poll = poll_interval_seconds
        self._batch = batch_size
        self._debug_status = debug_status or (lambda: False)
        # TECH_LOG rate limiting state
        self._tech_log_sent_this_minute: int = 0
        self._tech_log_minute_start: float = time.time()
        self._tech_log_rate_limit_warned: bool = False

    def _claim_pending(self) -> list[tuple]:
        conn = sqlite3.connect(self._ops_db, isolation_level=None)
        try:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """
                SELECT notification_id, notification_type, destination, payload_json, attempts
                FROM ops_notification_outbox
                WHERE status='PENDING'
                  AND (send_after IS NULL OR send_after <= ?)
                ORDER BY CASE priority WHEN 'HIGH' THEN 0 WHEN 'MEDIUM' THEN 1 ELSE 2 END,
                         created_at, notification_id
                LIMIT ?
                """,
                (_now(), self._batch),
            ).fetchall()
            if rows:
                ids = [r[0] for r in rows]
                conn.execute(
                    f"UPDATE ops_notification_outbox SET status='SENDING' "
                    f"WHERE notification_id IN ({','.join('?' * len(ids))})",
                    ids,
                )
            conn.execute("COMMIT")
            return rows
        finally:
            conn.close()

    def reset_stale_sending(self) -> int:
        """Reset SENDING rows to PENDING on startup (crash recovery)."""
        conn = sqlite3.connect(self._ops_db)
        try:
            conn.execute(
                "UPDATE ops_notification_outbox SET status='PENDING' WHERE status='SENDING'"
            )
            conn.commit()
            return conn.total_changes
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

    def _should_send_tech_log(self, payload: dict) -> bool:
        """Apply policy gating for TECH_LOG: enabled, min_level, debug, operational_events."""
        cfg = self._config.topics.tech_log
        if not cfg.enabled:
            return False
        level = str(payload.get("level", "INFO")).upper()
        if level == "DEBUG" and not self._debug_status():
            return False
        # operational_events is a secondary veto: INFO is always suppressed unless explicitly enabled,
        # even if min_level would allow it.
        if level == "INFO" and not cfg.operational_events:
            return False
        order = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "WARN": 30, "ERROR": 40, "CRITICAL": 50}
        min_level = order.get(cfg.min_level.upper(), 30)
        current = order.get(level, 0)
        if current == 0:
            logger.debug("_should_send_tech_log: unknown level %r — suppressing", level)
        return current >= min_level

    def _check_tech_log_rate(self) -> bool:
        """Return True if message can be sent, False if rate limit exceeded.

        Resets counter each minute. Sends a single warning when limit is first hit.
        """
        now = time.time()
        if now - self._tech_log_minute_start >= 60:
            self._tech_log_sent_this_minute = 0
            self._tech_log_minute_start = now
            self._tech_log_rate_limit_warned = False

        max_per_min = self._config.topics.tech_log.max_messages_per_minute
        if self._tech_log_sent_this_minute < max_per_min:
            # Optimistic: count the slot before sending. A send failure does not
            # reclaim the slot — callers are expected to be rare failures.
            self._tech_log_sent_this_minute += 1
            return True

        return False

    async def _send_rate_limit_warning(self) -> None:
        warning_text = (
            "[WARN] TECH_LOG: Rate limit raggiunto\n"
            "────────────────\n"
            "Troppi messaggi in TECH_LOG (>20/min).\n"
            "Alcuni messaggi soppressi temporaneamente.\n\n"
            "Controlla il log file per il dettaglio completo.\n"
            "────────────────\n"
            "Source: notification_dispatcher"
        )
        try:
            chat_id, thread_id = self._router.route("TECH_LOG")
            await self._sender.send(
                chat_id=chat_id, thread_id=thread_id,
                text=warning_text, silent=False,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("rate limit warning send failed: %s", exc)

    # ── CLEAN_LOG tracking (root message ID per chain) ──────────────────────

    _SIGNAL_TYPES: frozenset[str] = frozenset({"SIGNAL_ACCEPTED", "SIGNAL_REJECTED", "REVIEW_REQUIRED"})

    def _get_clean_log_root(self, chain_id: int) -> tuple[str | None, str | None]:
        """Return (root_message_id, telegram_chat_id) for chain_id, or (None, None)."""
        conn = sqlite3.connect(self._ops_db)
        try:
            row = conn.execute(
                "SELECT clean_log_root_message_id, telegram_chat_id "
                "FROM ops_clean_log_tracking WHERE trade_chain_id=?",
                (chain_id,),
            ).fetchone()
            if row:
                return str(row[0]) if row[0] else None, str(row[1]) if row[1] else None
            return None, None
        finally:
            conn.close()

    def _get_clean_log_last(self, chain_id: int) -> tuple[str | None, str | None]:
        """Return (last_message_id, telegram_chat_id) for chain_id, or (None, None)."""
        conn = sqlite3.connect(self._ops_db)
        try:
            row = conn.execute(
                "SELECT clean_log_last_message_id, telegram_chat_id "
                "FROM ops_clean_log_tracking WHERE trade_chain_id=?",
                (chain_id,),
            ).fetchone()
            if row:
                return str(row[0]) if row[0] else None, str(row[1]) if row[1] else None
            return None, None
        finally:
            conn.close()

    def _update_clean_log_tracking(
        self,
        chain_id: int | None,
        notification_type: str,
        chat_id: int,
        thread_id: int | None,
        sent_message_id: str | None,
    ) -> None:
        if chain_id is None or sent_message_id is None:
            return
        conn = sqlite3.connect(self._ops_db)
        try:
            now = _now()
            existing = conn.execute(
                "SELECT clean_log_root_message_id FROM ops_clean_log_tracking WHERE trade_chain_id=?",
                (chain_id,),
            ).fetchone()
            if existing is None:
                conn.execute(
                    """INSERT INTO ops_clean_log_tracking
                       (trade_chain_id, clean_log_root_message_id, clean_log_last_message_id,
                        telegram_chat_id, telegram_thread_id, last_clean_log_event_type,
                        last_clean_log_sent_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        chain_id, sent_message_id, sent_message_id,
                        str(chat_id), str(thread_id) if thread_id is not None else None,
                        notification_type, now, now,
                    ),
                )
            else:
                conn.execute(
                    """UPDATE ops_clean_log_tracking
                       SET clean_log_last_message_id=?, last_clean_log_event_type=?,
                           last_clean_log_sent_at=?, updated_at=?
                       WHERE trade_chain_id=?""",
                    (sent_message_id, notification_type, now, now, chain_id),
                )
            conn.commit()
        finally:
            conn.close()

    def _try_release_pending_close_full_summaries(self) -> None:
        conn = sqlite3.connect(self._ops_db)
        try:
            try_release_pending_close_full_summaries(conn)
        except Exception:
            logger.exception("try_release_pending_close_full_summaries failed")
        finally:
            conn.close()

    def _build_signal_link(self, root_message_id: str | None, tracking_chat_id: str | None) -> str | None:
        """Build a t.me/c/ link to the SIGNAL_ACCEPTED clean log message."""
        if not root_message_id or not tracking_chat_id:
            return None
        normalized = str(tracking_chat_id).removeprefix("-100")
        return f"https://t.me/c/{normalized}/{root_message_id}"

    def _render(self, destination: str, notification_type: str, payload: dict) -> str:
        if destination == "CLEAN_LOG":
            return format_clean_log(notification_type, payload)
        if destination == "TECH_LOG":
            return format_tech_log(payload, delivery_mode=self._config.delivery_mode)
        # COMMANDS_REPLY formatter arrives in a later part; safe fallback.
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
        # Release any already-resolvable close-full summaries before claiming work,
        # so stale pending summaries do not wait for an unrelated future POSITION_CLOSED.
        self._try_release_pending_close_full_summaries()
        rows = self._claim_pending()
        sent = 0
        for notification_id, notification_type, destination, payload_json, attempts in rows:
            try:
                payload = json.loads(payload_json or "{}")
            except Exception:
                payload = {}

            # Policy gating — TECH_LOG only
            if destination == "TECH_LOG":
                if not self._should_send_tech_log(payload):
                    self._mark_sent(notification_id)
                    continue

            # Rate limit check — only TECH_LOG is subject to limiting
            if destination == "TECH_LOG" and not self._check_tech_log_rate():
                if not self._tech_log_rate_limit_warned:
                    self._tech_log_rate_limit_warned = True
                    await self._send_rate_limit_warning()
                self._mark_sent(notification_id)
                continue

            try:
                chat_id, thread_id = self._router.route(destination)
                if destination == "CLEAN_LOG" and notification_type not in self._SIGNAL_TYPES:
                    chain_id = payload.get("chain_id")
                    if chain_id is not None:
                        root_msg_id, tracking_chat_id = self._get_clean_log_root(chain_id)
                        link = self._build_signal_link(root_msg_id, tracking_chat_id)
                        if link:
                            payload = {**payload, "signal_link": link}
                if destination == "CLEAN_LOG" and notification_type == "MULTI_CHAIN_SUMMARY":
                    chains = []
                    for chain in payload.get("chains", []):
                        enriched_chain = dict(chain)
                        # Use pre-resolved signal link from payload; fall back to live
                        # tracking only when absent (e.g. chain created before tracking row exists).
                        if not enriched_chain.get("link"):
                            chain_id = enriched_chain.get("chain_id")
                            if chain_id is not None:
                                last_msg_id, tracking_chat_id = self._get_clean_log_last(chain_id)
                                link = self._build_signal_link(last_msg_id, tracking_chat_id)
                                if link:
                                    enriched_chain["link"] = link
                        chains.append(enriched_chain)
                    payload = {**payload, "chains": chains}
                text = self._render(destination, notification_type, payload)
                silent = self._is_silent(notification_type)
                sent_message_id = await self._sender.send(
                    chat_id=chat_id, thread_id=thread_id, text=text, silent=silent
                )
                if destination == "CLEAN_LOG":
                    self._update_clean_log_tracking(
                        payload.get("chain_id"), notification_type, chat_id, thread_id, sent_message_id
                    )
                    if notification_type == "POSITION_CLOSED":
                        self._try_release_pending_close_full_summaries()
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
