from __future__ import annotations

import logging
from dataclasses import dataclass

from src.runtime_v2.control_plane.audit_store import CommandAuditStore
from src.runtime_v2.control_plane.auth import AuthValidator
from src.runtime_v2.control_plane.formatters.block import format_block, format_unblock
from src.runtime_v2.control_plane.formatters.control import format_control
from src.runtime_v2.control_plane.formatters.health import format_health
from src.runtime_v2.control_plane.formatters.pause import (
    format_pause, format_resume, format_start,
)
from src.runtime_v2.control_plane.formatters.reviews import format_reviews
from src.runtime_v2.control_plane.formatters.status import format_status
from src.runtime_v2.control_plane.formatters.trade_detail import format_trade_detail
from src.runtime_v2.control_plane.formatters.trades import format_trades
from src.runtime_v2.control_plane.models import ControlPlaneConfig
from src.runtime_v2.control_plane.service import RuntimeControlService

logger = logging.getLogger(__name__)

_HELP_TEXT = """COMANDI DISPONIBILI
----------------
Informativi:
/status    - salute bot e conteggi
/trades    - trade aperti
/trade #id - dettaglio singola chain
/health    - stato workers
/control   - blocchi operativi
/reviews   - casi da controllare
/version   - versione runtime
/help      - questo messaggio

Controllo:
/pause [trader]
/resume [trader]
/start
/block <symbol>
/block <trader> <symbol>
/unblock <symbol>
/unblock <trader> <symbol>"""


@dataclass
class RouteResult:
    decision: str
    reply_text: str | None


_READONLY_COMMANDS = frozenset(
    {"help", "status", "trades", "trade", "health", "control", "reviews", "version"}
)
_CONTROL_COMMANDS = frozenset({"pause", "resume", "start", "block", "unblock"})
_ALLOWED_COMMANDS = _READONLY_COMMANDS | _CONTROL_COMMANDS


def _parse(command_text: str) -> tuple[str | None, list[str]]:
    parts = command_text.strip().split()
    if not parts or not parts[0].startswith("/"):
        return None, []
    name = parts[0][1:].split("@", 1)[0].lower()
    return name, parts[1:]


def _parse_scope_symbol(args: list[str]) -> tuple[str | None, str | None]:
    if len(args) == 1:
        return None, args[0]
    if len(args) == 2:
        return args[0], args[1]
    return None, None


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
            chat_id=chat_id,
            thread_id=thread_id,
            user_id=user_id,
        )

        if auth_result.decision == "IGNORE":
            if auth_result.reason == "wrong_topic":
                self._record(
                    request_id=request_id,
                    chat_id=chat_id,
                    thread_id=thread_id,
                    user_id=user_id,
                    username=username,
                    command_text=command_text,
                    command_name=None,
                    status="IGNORED",
                    reject_reason="wrong_topic",
                )
            return RouteResult("IGNORE", None)

        if auth_result.decision == "REJECT_UNAUTHORIZED":
            self._record(
                request_id=request_id,
                chat_id=chat_id,
                thread_id=thread_id,
                user_id=user_id,
                username=username,
                command_text=command_text,
                command_name=None,
                status="REJECTED",
                reject_reason="unauthorized_user",
            )
            return RouteResult("REJECT_UNAUTHORIZED", None)

        command_name, args = _parse(command_text)
        if command_name not in self._allowed_commands():
            self._record(
                request_id=request_id,
                chat_id=chat_id,
                thread_id=thread_id,
                user_id=user_id,
                username=username,
                command_text=command_text,
                command_name=command_name,
                status="REJECTED",
                reject_reason="unknown_command",
            )
            return RouteResult("REJECTED", "Comando non riconosciuto.")

        self._record(
            request_id=request_id,
            chat_id=chat_id,
            thread_id=thread_id,
            user_id=user_id,
            username=username,
            command_text=command_text,
            command_name=command_name,
            status="ACCEPTED",
        )
        try:
            reply = self._dispatch(command_name, args, created_by=str(user_id))
            self._audit.update_status(request_id, status="EXECUTED")
            return RouteResult("EXECUTED", reply)
        except Exception:
            logger.exception("command handler failed: %s", command_text)
            self._audit.update_status(request_id, status="FAILED")
            return RouteResult("FAILED", "Errore interno durante l'esecuzione del comando.")

    def _allowed_commands(self) -> frozenset[str]:
        return _ALLOWED_COMMANDS

    def _dispatch(self, command_name: str, args: list[str], *, created_by: str) -> str:
        if command_name == "help":
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
            version = self._service.get_version()
            return (
                "VERSION\n----------------\n"
                f"Runtime: {version.runtime}\n"
                f"Commit: {version.commit}\n"
                f"Branch: {version.branch}\n"
                f"Uptime: {version.uptime_seconds}s"
            )
        if command_name == "pause":
            if len(args) > 1:
                return "Usage: /pause  oppure  /pause <trader>"
            scope = args[0] if args else None
            return format_pause(self._service.pause(scope_value=scope, created_by=created_by))
        if command_name == "resume":
            if len(args) > 1:
                return "Usage: /resume  oppure  /resume <trader>"
            scope = args[0] if args else None
            return format_resume(self._service.resume(scope_value=scope))
        if command_name == "start":
            return format_start(self._service.start())
        if command_name == "block":
            scope, symbol = _parse_scope_symbol(args)
            if symbol is None:
                return "Usage: /block <symbol>  oppure  /block <trader> <symbol>"
            return format_block(
                self._service.block_symbol(
                    scope_value=scope,
                    symbol=symbol,
                    created_by=created_by,
                )
            )
        if command_name == "unblock":
            scope, symbol = _parse_scope_symbol(args)
            if symbol is None:
                return "Usage: /unblock <symbol>  oppure  /unblock <trader> <symbol>"
            return format_unblock(
                self._service.unblock_symbol(
                    scope_value=scope,
                    symbol=symbol,
                )
            )
        return "Comando non riconosciuto."

    def _record(
        self,
        *,
        request_id: str,
        chat_id: int,
        thread_id: int | None,
        user_id: int,
        username: str | None,
        command_text: str,
        command_name: str | None,
        status: str,
        reject_reason: str | None = None,
    ) -> None:
        self._audit.record(
            command_request_id=request_id,
            chat_id=str(chat_id),
            message_thread_id=str(thread_id) if thread_id is not None else "",
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
        self._keyboard_users: set[int] = set()

    def _build_app(self):
        from telegram.ext import Application, MessageHandler, filters

        app = Application.builder().token(self._config.token).build()
        app.add_handler(MessageHandler(filters.COMMAND, self._on_command))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_text_message))
        return app

    async def _send_reply_keyboard(
        self,
        update,
        *,
        user_id: int | None = None,
        force: bool = False,
    ) -> None:
        if self._config.delivery_mode != "private_bot":
            return
        if not self._config.keyboard:
            return
        if not force and user_id is not None and user_id in self._keyboard_users:
            return
        from telegram import ReplyKeyboardMarkup

        markup = ReplyKeyboardMarkup(
            self._config.keyboard,
            resize_keyboard=True,
            is_persistent=True,
        )
        await update.message.reply_text("Control Plane attivo.", reply_markup=markup)
        if user_id is not None:
            self._keyboard_users.add(user_id)

    async def _on_text_message(self, update, context) -> None:
        del context
        message = update.effective_message
        user = update.effective_user
        if message is None or user is None:
            return
        auth_result = self._router._auth.validate(
            chat_id=message.chat_id,
            thread_id=message.message_thread_id,
            user_id=user.id,
        )
        if auth_result.decision != "OK":
            return
        await self._send_reply_keyboard(update, user_id=user.id)

    async def _on_command(self, update, context) -> None:
        message = update.effective_message
        user = update.effective_user
        if message is None or user is None:
            return
        result = self._router.route(
            command_text=message.text or "",
            message_id=message.message_id,
            chat_id=message.chat_id,
            thread_id=message.message_thread_id,
            user_id=user.id,
            username=user.username,
        )
        if result.reply_text is None:
            return

        command_name, _ = _parse(message.text or "")
        if command_name == "start" and result.decision == "EXECUTED":
            await self._send_reply_keyboard(update, user_id=user.id, force=True)

        thread_id = self._config.topics.commands.thread_id
        send_kwargs: dict[str, object] = {
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
