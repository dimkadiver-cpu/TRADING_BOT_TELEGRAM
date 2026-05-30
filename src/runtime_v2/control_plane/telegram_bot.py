# src/runtime_v2/control_plane/telegram_bot.py
from __future__ import annotations

import logging
from dataclasses import dataclass

from src.runtime_v2.control_plane.audit_store import CommandAuditStore
from src.runtime_v2.control_plane.auth import AuthValidator
from src.runtime_v2.control_plane.formatters.control import format_control
from src.runtime_v2.control_plane.formatters.health import format_health
from src.runtime_v2.control_plane.formatters.reviews import format_reviews
from src.runtime_v2.control_plane.formatters.status import format_status
from src.runtime_v2.control_plane.formatters.trade_detail import format_trade_detail
from src.runtime_v2.control_plane.formatters.trades import format_trades
from src.runtime_v2.control_plane.models import ControlPlaneConfig
from src.runtime_v2.control_plane.service import RuntimeControlService

logger = logging.getLogger(__name__)

_HELP_TEXT = """COMANDI DISPONIBILI
────────────────
Informativi:
/status    — salute bot e conteggi
/trades    — trade aperti
/trade #id — dettaglio singola chain
/health    — stato workers
/control   — blocchi operativi
/reviews   — casi da controllare
/version   — versione runtime
/help      — questo messaggio"""


@dataclass
class RouteResult:
    decision: str            # OK | IGNORE | REJECT_UNAUTHORIZED | REJECTED | EXECUTED | FAILED
    reply_text: str | None   # None = do not reply


# Read-only command set for Part 3. Part 4/5 extend this set.
_READONLY_COMMANDS = frozenset({
    "help", "status", "trades", "trade", "health", "control", "reviews", "version", "start",
})


def _parse(command_text: str) -> tuple[str | None, list[str]]:
    parts = command_text.strip().split()
    if not parts or not parts[0].startswith("/"):
        return None, []
    name = parts[0][1:].split("@", 1)[0].lower()   # strip leading "/" and @botname
    return name, parts[1:]


class CommandRouter:
    def __init__(
        self,
        *,
        config: ControlPlaneConfig,
        auth: AuthValidator,
        audit: CommandAuditStore,
        service: RuntimeControlService,
    ) -> None:
        self._config = config
        self._auth = auth
        self._audit = audit
        self._service = service

    def route(
        self,
        *,
        command_text: str,
        message_id: int,
        chat_id: int,
        thread_id: int | None,
        user_id: int,
        username: str | None,
    ) -> RouteResult:
        request_id = f"{chat_id}:{message_id}"
        auth_result = self._auth.validate(
            chat_id=chat_id, thread_id=thread_id, user_id=user_id
        )

        if auth_result.decision == "IGNORE":
            if auth_result.reason == "wrong_topic":
                self._record(request_id, chat_id, thread_id, user_id, username,
                             command_text, None, "IGNORED", reject_reason="wrong_topic")
            return RouteResult("IGNORE", None)

        if auth_result.decision == "REJECT_UNAUTHORIZED":
            self._record(request_id, chat_id, thread_id, user_id, username,
                         command_text, None, "REJECTED", reject_reason="unauthorized_user")
            return RouteResult("REJECT_UNAUTHORIZED", None)

        command_name, args = _parse(command_text)
        if command_name not in self._allowed_commands():
            self._record(request_id, chat_id, thread_id, user_id, username,
                         command_text, command_name, "REJECTED",
                         reject_reason="unknown_command")
            return RouteResult("REJECTED", "Comando non riconosciuto.")

        self._record(request_id, chat_id, thread_id, user_id, username,
                     command_text, command_name, "ACCEPTED")
        try:
            reply = self._dispatch(command_name, args)
            self._audit.update_status(request_id, status="EXECUTED")
            return RouteResult("EXECUTED", reply)
        except Exception:
            logger.exception("command handler failed: %s", command_text)
            self._audit.update_status(request_id, status="FAILED")
            return RouteResult("FAILED", "Errore interno durante l'esecuzione del comando.")

    # ── overridable in later parts ────────────────────────────────────────────
    def _allowed_commands(self) -> frozenset[str]:
        return _READONLY_COMMANDS

    def _dispatch(self, command_name: str, args: list[str]) -> str:
        if command_name in ("help", "start"):
            return _HELP_TEXT
        if command_name == "status":
            return format_status(self._service.get_status())
        if command_name == "trades":
            return format_trades(self._service.get_open_trades())
        if command_name == "trade":
            if not args or not args[0].lstrip("#").isdigit():
                return "Usage: /trade <chain_id>"
            chain_id = int(args[0].lstrip("#"))
            return format_trade_detail(self._service.get_trade(chain_id))
        if command_name == "health":
            return format_health(self._service.get_health())
        if command_name == "control":
            return format_control(self._service.get_control())
        if command_name == "reviews":
            return format_reviews(self._service.get_reviews())
        if command_name == "version":
            v = self._service.get_version()
            return (
                "VERSION\n────────────────\n"
                f"Runtime: {v.runtime}\nCommit: {v.commit}\n"
                f"Branch: {v.branch}\nUptime: {v.uptime_seconds}s"
            )
        return "Comando non riconosciuto."

    def _record(self, request_id, chat_id, thread_id, user_id, username,
                command_text, command_name, status, reject_reason=None) -> None:
        self._audit.record(
            command_request_id=request_id,
            chat_id=str(chat_id),
            message_thread_id=str(thread_id),
            telegram_user_id=str(user_id),
            telegram_username=username,
            command_text=command_text,
            command_name=command_name,
            status=status,
            reject_reason=reject_reason,
        )


class TelegramControlBot:
    """python-telegram-bot wrapper. Thin: delegates all logic to CommandRouter."""

    def __init__(self, *, config: ControlPlaneConfig, router: CommandRouter) -> None:
        self._config = config
        self._router = router
        self._app = None

    def _build_app(self):
        from telegram.ext import Application, MessageHandler, filters

        app = Application.builder().token(self._config.token).build()
        app.add_handler(MessageHandler(filters.COMMAND, self._on_command))
        return app

    async def _send_reply_keyboard(self, update) -> None:
        """Send ReplyKeyboardMarkup in private_bot mode (Delta Task 5)."""
        if self._config.delivery_mode != "private_bot":
            return
        if not self._config.keyboard:
            return
        from telegram import ReplyKeyboardMarkup
        markup = ReplyKeyboardMarkup(
            self._config.keyboard,
            resize_keyboard=True,
            persistent=True,
        )
        await update.message.reply_text(".", reply_markup=markup)

    async def _on_command(self, update, context) -> None:
        msg = update.effective_message
        if msg is None or update.effective_user is None:
            return
        result = self._router.route(
            command_text=msg.text or "",
            message_id=msg.message_id,
            chat_id=msg.chat_id,
            thread_id=msg.message_thread_id,
            user_id=update.effective_user.id,
            username=update.effective_user.username,
        )
        if result.reply_text is not None:
            command_name, _ = _parse(msg.text or "")
            # Send keyboard on /start or first authorized contact in private_bot mode
            if command_name == "start" and result.decision == "EXECUTED":
                await self._send_reply_keyboard(update)
            thread_id = self._config.topics.commands.thread_id
            send_kwargs: dict = {
                "chat_id": self._config.chat_id,
                "text": result.reply_text,
            }
            if thread_id is not None:
                send_kwargs["message_thread_id"] = thread_id
            await context.bot.send_message(**send_kwargs)

    async def run(self) -> None:
        self._app = self._build_app()
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling()

    async def shutdown(self) -> None:
        if self._app is None:
            return
        try:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
        finally:
            self._app = None


__all__ = ["CommandRouter", "RouteResult", "TelegramControlBot"]
