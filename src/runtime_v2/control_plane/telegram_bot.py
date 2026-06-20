from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Literal

from src.runtime_v2.control_plane.audit_store import CommandAuditStore
from src.runtime_v2.control_plane.auth import AuthValidator
from src.runtime_v2.control_plane.debug_controller import (
    is_valid_duration_arg,
    parse_duration,
)
from src.runtime_v2.control_plane.formatters._blocks import render_template
from src.runtime_v2.control_plane.formatters.block import format_block, format_unblock
from src.runtime_v2.control_plane.formatters.control import format_control
from src.runtime_v2.control_plane.formatters.debug import format_debug_off, format_debug_on
from src.runtime_v2.control_plane.formatters.health import format_health
from src.runtime_v2.control_plane.formatters.pause import (
    format_pause, format_resume, format_start,
)
from src.runtime_v2.control_plane.formatters.pnl import format_pnl
from src.runtime_v2.control_plane.formatters.reviews import format_reviews
from src.runtime_v2.control_plane.formatters.status import format_status
from src.runtime_v2.control_plane.formatters.trade_detail import format_trade_detail
from src.runtime_v2.control_plane.formatters.trades import format_trades
from src.runtime_v2.control_plane.formatters.templates.emergency import EMERGENCY_REGISTRY
from src.runtime_v2.control_plane.models import ControlPlaneConfig
from src.runtime_v2.control_plane.notification_dispatcher import build_telegram_request
from src.runtime_v2.control_plane.service import RuntimeControlService
from src.runtime_v2.control_plane.status_queries import CloseCandidate

logger = logging.getLogger(__name__)
_COMMAND_SEND_TIMEOUT_SECONDS = 8.0
_PENDING_TTL = 300  # 5 minuti


@dataclass
class _PendingAction:
    kind: Literal["close_all", "close_single", "cancel_all"]
    scope: "QueryScope"  # type: ignore[name-defined]  # forward ref from scope_resolver
    candidates: list[CloseCandidate]
    chains_payload: list[dict]
    scope_label: str
    open_count: int
    created_at: float = field(default_factory=time.time)

    def is_expired(self) -> bool:
        return time.time() - self.created_at > _PENDING_TTL


@dataclass
class CallbackResult:
    reply_text: str
    delete_message: bool = False
    answer_text: str = ""

_HELP_TEXT = """COMANDI DISPONIBILI
────────────────
Informativi:
/status    - salute bot e conteggi
/trades    - trade aperti
/trade #id - dettaglio singola chain
/health    - stato workers
/control   - blocchi operativi
/reviews   - casi da controllare
/pnl       - ultimo snapshot account persistito
/logs [n]  - ultime N righe log (default: 20)
/debug_on [<duration>]
/debug_off
/version   - versione runtime
/help      - questo messaggio

Controllo:
/pause [trader]
/resume [trader]
/start
/block <symbol>
/block <trader> <symbol>
/unblock <symbol>
/unblock <trader> <symbol>

Emergenza (⚠️ destructivi — chiedono conferma):
/close_all [trader]       - chiude tutte le posizioni aperte
/close [trader] <symbol>  - chiude posizioni su un simbolo
/cancel_all [trader]      - cancella tutti gli ordini WAITING_ENTRY

Dashboard:
/stats     - statistiche trades
/dashboard - pannello di controllo"""


@dataclass
class RouteResult:
    decision: str
    reply_text: str | None
    keyboard: object | None = None  # InlineKeyboardMarkup | None


@dataclass(frozen=True)
class _DispatchResult:
    reply_text: str
    decision: str = "EXECUTED"
    reject_reason: str | None = None
    keyboard: object | None = None  # InlineKeyboardMarkup | None


_READONLY_COMMANDS = frozenset(
    {"help", "status", "trades", "trade", "health", "control", "reviews",
     "version", "stats", "dashboard"}
)
_CONTROL_COMMANDS = frozenset({"pause", "resume", "start", "block", "unblock"})
_EMERGENCY_COMMANDS = frozenset({"close_all", "close", "cancel_all"})
_ADVANCED_COMMANDS = frozenset({"pnl", "logs", "debug_on", "debug_off"})
_ALLOWED_COMMANDS = _READONLY_COMMANDS | _CONTROL_COMMANDS | _EMERGENCY_COMMANDS | _ADVANCED_COMMANDS



def _make_token() -> str:
    return secrets.token_hex(4)  # 8 hex chars, < 64 bytes with prefix


def _emergency_keyboard(kind: str, token: str):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Conferma", callback_data=f"{kind}:confirm:{token}"),
        InlineKeyboardButton("❌ Annulla", callback_data=f"{kind}:cancel:{token}"),
    ]])


def _now_hms() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def _scope_label_from_scope(scope: "QueryScope") -> str:  # type: ignore[name-defined]
    """Format a QueryScope as a human-readable label."""
    if scope.trader_ids is None:
        return scope.account_id.upper()
    if len(scope.trader_ids) == 1:
        return scope.trader_ids[0]
    return ", ".join(scope.trader_ids)


def _candidates_to_payload(candidates: list[CloseCandidate]) -> list[dict]:
    from src.runtime_v2.control_plane.formatters.display import display_symbol
    return [
        {
            "chain_id": c.chain_id,
            "symbol": display_symbol(c.symbol),
            "side": c.side,
            "state": c.state,
            "entry_price": None,
            "pnl": None,
        }
        for c in candidates
    ]


def _override_trader(scope: "QueryScope", trader_arg: str | None) -> "QueryScope":  # type: ignore[name-defined]
    """If trader_arg is specified, restrict scope to that trader."""
    from src.runtime_v2.control_plane.scope_resolver import QueryScope
    if trader_arg:
        return QueryScope(account_id=scope.account_id, trader_ids=[trader_arg])
    return scope


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
        self._debug_max_seconds = config.get_account(None).topics.tech_log.debug_max_duration_minutes * 60
        self._pending: dict[str, _PendingAction] = {}

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
        command_name, args = _parse(command_text)
        auth_result = self._auth.validate(
            chat_id=chat_id,
            thread_id=thread_id,
            user_id=user_id,
            command_name=command_name,
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
            dispatch_result = self._dispatch(command_name, args, created_by=str(user_id))
            self._audit.update_status(
                request_id,
                status=dispatch_result.decision,
                reject_reason=dispatch_result.reject_reason,
            )
            return RouteResult(dispatch_result.decision, dispatch_result.reply_text, dispatch_result.keyboard)
        except Exception:
            logger.exception("command handler failed: %s", command_text)
            self._audit.update_status(request_id, status="FAILED")
            return RouteResult("FAILED", "Errore interno durante l'esecuzione del comando.")

    def _allowed_commands(self) -> frozenset[str]:
        return _ALLOWED_COMMANDS

    def _dispatch(
        self,
        command_name: str,
        args: list[str],
        *,
        created_by: str,
    ) -> _DispatchResult:
        if command_name == "help":
            return _DispatchResult(_HELP_TEXT)
        if command_name == "status":
            return _DispatchResult(format_status(self._service.get_status()))
        if command_name == "trades":
            return _DispatchResult(format_trades(self._service.get_open_trades()))
        if command_name == "trade":
            if not args or not args[0].lstrip("#").isdigit():
                return _DispatchResult(
                    "Usage: /trade <chain_id>",
                    decision="REJECTED",
                    reject_reason="invalid_arguments",
                )
            chain_id = int(args[0].lstrip("#"))
            return _DispatchResult(format_trade_detail(self._service.get_trade(chain_id)))
        if command_name == "health":
            return _DispatchResult(format_health(self._service.get_health()))
        if command_name == "control":
            return _DispatchResult(format_control(self._service.get_control()))
        if command_name == "reviews":
            return _DispatchResult(format_reviews(self._service.get_reviews()))
        if command_name == "pnl":
            return _DispatchResult(format_pnl(self._service.get_pnl()))
        if command_name == "version":
            v = self._service.get_version()
            # Format uptime as "Xh Ym" or "Ym Xs" or just "Xs"
            secs = v.uptime_seconds
            h, rem = divmod(secs, 3600)
            m, s = divmod(rem, 60)
            if h > 0:
                uptime_str = f"{h}h {m}m"
            elif m > 0:
                uptime_str = f"{m}m {s}s"
            else:
                uptime_str = f"{s}s"
            return _DispatchResult(
                "📦 VERSION\n────────────────\n"
                f"Runtime: {v.runtime}\n"
                f"Commit: {v.commit}  ({v.commit_date})\n"
                f"Branch: {v.branch}\n"
                f"Uptime: {uptime_str}"
            )
        if command_name == "pause":
            if len(args) > 1:
                return _DispatchResult(
                    "Usage: /pause  oppure  /pause <trader>",
                    decision="REJECTED",
                    reject_reason="invalid_arguments",
                )
            scope = args[0] if args else None
            return _DispatchResult(
                format_pause(self._service.pause(scope_value=scope, created_by=created_by))
            )
        if command_name == "resume":
            if len(args) > 1:
                return _DispatchResult(
                    "Usage: /resume  oppure  /resume <trader>",
                    decision="REJECTED",
                    reject_reason="invalid_arguments",
                )
            scope = args[0] if args else None
            return _DispatchResult(format_resume(self._service.resume(scope_value=scope)))
        if command_name == "start":
            return _DispatchResult(format_start(self._service.start()))
        if command_name == "block":
            scope, symbol = _parse_scope_symbol(args)
            if symbol is None:
                return _DispatchResult(
                    "Usage: /block <symbol>  oppure  /block <trader> <symbol>",
                    decision="REJECTED",
                    reject_reason="invalid_arguments",
                )
            return _DispatchResult(
                format_block(
                    self._service.block_symbol(
                        scope_value=scope,
                        symbol=symbol,
                        created_by=created_by,
                    )
                )
            )
        if command_name == "unblock":
            scope, symbol = _parse_scope_symbol(args)
            if symbol is None:
                return _DispatchResult(
                    "Usage: /unblock <symbol>  oppure  /unblock <trader> <symbol>",
                    decision="REJECTED",
                    reject_reason="invalid_arguments",
                )
            return _DispatchResult(
                format_unblock(
                    self._service.unblock_symbol(
                        scope_value=scope,
                        symbol=symbol,
                    )
                )
            )
        if command_name == "logs":
            try:
                n = int(args[0]) if args else 20
                n = max(1, min(n, 100))
            except ValueError:
                n = 20
            lines = self._service.get_logs(n)
            body = "\n".join(lines) if lines else "(log vuoto)"
            return _DispatchResult(f"📋 LOGS — last {n}\n────────────────\n{body}")

        if command_name == "debug_on":
            if len(args) > 1 or (args and not is_valid_duration_arg(args[0])):
                return _DispatchResult(
                    "Usage: /debug_on [<duration>]",
                    decision="REJECTED",
                    reject_reason="invalid_arguments",
                )
            seconds = parse_duration(
                args[0] if args else None,
                max_seconds=self._debug_max_seconds,
            )
            expires_at = self._service.enable_debug(duration_seconds=seconds)
            return _DispatchResult(
                format_debug_on(duration_seconds=seconds, expires_at=expires_at)
            )

        if command_name == "debug_off":
            self._service.disable_debug()
            return _DispatchResult(format_debug_off())

        if command_name == "dashboard":
            # Signal that dashboard should be sent via DashboardManager.create()
            # in TelegramControlBot._on_command; router marks it valid here.
            return _DispatchResult("__DASHBOARD__", decision="EXECUTED")

        if command_name == "close_all":
            # optional: /close_all [trader]
            from src.runtime_v2.control_plane.scope_resolver import QueryScope as _QS
            default_scope = _QS(account_id=self._config.default_account, trader_ids=None)
            trader_override = args[0] if args else None
            effective_scope = _override_trader(default_scope, trader_override)
            candidates = self._service.get_open_for_close(effective_scope)
            sl = _scope_label_from_scope(effective_scope)
            chains_payload = _candidates_to_payload(candidates)
            cfg = EMERGENCY_REGISTRY["close_all_preview"]
            payload = {"scope_label": sl, "total": len(candidates), "chains": chains_payload}
            text = render_template(cfg.blocks, payload, transform=cfg.payload_transform)
            if not candidates:
                return _DispatchResult(text)
            token = _make_token()
            self._pending[token] = _PendingAction(
                kind="close_all", scope=effective_scope,
                candidates=candidates,
                chains_payload=chains_payload, scope_label=sl, open_count=0,
            )
            return _DispatchResult(text, keyboard=_emergency_keyboard("close_all", token))

        if command_name == "close":
            # /close [trader] <symbol>
            from src.runtime_v2.control_plane.scope_resolver import QueryScope as _QS
            default_scope = _QS(account_id=self._config.default_account, trader_ids=None)
            trader_arg, symbol_arg = _parse_scope_symbol(args)
            if not symbol_arg:
                return _DispatchResult(
                    "Usage: /close <symbol>  o  /close <trader> <symbol>",
                    decision="REJECTED",
                    reject_reason="invalid_arguments",
                )
            effective_scope = _override_trader(default_scope, trader_arg)
            candidates = [
                c for c in self._service.get_open_for_close(effective_scope)
                if c.symbol.upper() == symbol_arg.upper()
            ]
            sl = _scope_label_from_scope(effective_scope)
            chains_payload = _candidates_to_payload(candidates)
            cfg = EMERGENCY_REGISTRY["close_single_preview"]
            payload = {
                "scope_label": sl,
                "total": len(candidates),
                "chains": chains_payload,
                "symbol": symbol_arg.upper(),
            }
            text = render_template(cfg.blocks, payload, transform=cfg.payload_transform)
            if not candidates:
                return _DispatchResult(text)
            token = _make_token()
            self._pending[token] = _PendingAction(
                kind="close_single", scope=effective_scope,
                candidates=candidates,
                chains_payload=chains_payload, scope_label=sl, open_count=0,
            )
            return _DispatchResult(text, keyboard=_emergency_keyboard("close_single", token))

        if command_name == "cancel_all":
            from src.runtime_v2.control_plane.scope_resolver import QueryScope as _QS
            default_scope = _QS(account_id=self._config.default_account, trader_ids=None)
            trader_override = args[0] if args else None
            effective_scope = _override_trader(default_scope, trader_override)
            candidates = self._service.get_waiting_for_cancel(effective_scope)
            open_count = self._service.get_open_count_excluding_waiting(effective_scope)
            sl = _scope_label_from_scope(effective_scope)
            chains_payload = _candidates_to_payload(candidates)
            cfg = EMERGENCY_REGISTRY["cancel_all_preview"]
            payload = {
                "scope_label": sl,
                "total": len(candidates),
                "chains": chains_payload,
                "open_count": open_count,
            }
            text = render_template(cfg.blocks, payload, transform=cfg.payload_transform)
            if not candidates:
                return _DispatchResult(text)
            token = _make_token()
            self._pending[token] = _PendingAction(
                kind="cancel_all", scope=effective_scope,
                candidates=candidates,
                chains_payload=chains_payload, scope_label=sl, open_count=open_count,
            )
            return _DispatchResult(text, keyboard=_emergency_keyboard("cancel_all", token))

        return _DispatchResult("Comando non riconosciuto.", decision="REJECTED")

    def handle_callback(
        self,
        *,
        callback_data: str,
        user_id: int,
        chat_id: int,
        message_id: int,
        thread_id: int | None,
        created_by: str,
    ) -> CallbackResult:
        parts = callback_data.split(":", 2)
        if len(parts) != 3:
            return CallbackResult("Callback non valido.", answer_text="⚠️ Callback non valido")
        kind, action, token = parts
        if action not in ("confirm", "cancel"):
            return CallbackResult("Azione non valida.", answer_text="⚠️")

        pending = self._pending.get(token)
        if pending is None:
            return CallbackResult("", delete_message=False, answer_text="⏱ Azione scaduta — reinvia il comando.")

        if pending.is_expired():
            del self._pending[token]
            return CallbackResult("", delete_message=True, answer_text="⏱ Azione scaduta — reinvia il comando.")

        if kind != pending.kind:
            return CallbackResult("Azione non valida.", answer_text="⚠️")

        del self._pending[token]
        now = _now_hms()

        if action == "cancel":
            result_key = f"{kind}_result_cancelled"
            cfg = EMERGENCY_REGISTRY[result_key]
            payload = {
                "scope_label": pending.scope_label,
                "chains": pending.chains_payload,
                "cancelled_at": now,
                "count": len(pending.candidates),
                "open_count": pending.open_count,
            }
            text = render_template(cfg.blocks, payload, transform=cfg.payload_transform)
            return CallbackResult(text, answer_text="❌ Annullato")

        # confirm
        if kind == "close_all":
            count = self._service.execute_close(pending.candidates, created_by=created_by)
            cfg = EMERGENCY_REGISTRY["close_all_result_ok"]
            payload = {
                "scope_label": pending.scope_label,
                "chains": pending.chains_payload,
                "count": count,
                "executed_at": now,
            }
        elif kind == "close_single":
            count = self._service.execute_close(pending.candidates, created_by=created_by)
            cfg = EMERGENCY_REGISTRY["close_single_result_ok"]
            payload = {
                "scope_label": pending.scope_label,
                "chains": pending.chains_payload,
                "count": count,
                "executed_at": now,
            }
        elif kind == "cancel_all":
            count = self._service.execute_cancel(pending.candidates, created_by=created_by)
            cfg = EMERGENCY_REGISTRY["cancel_all_result_ok"]
            payload = {
                "scope_label": pending.scope_label,
                "chains": pending.chains_payload,
                "count": count,
                "executed_at": now,
                "open_count": pending.open_count,
            }
        else:
            return CallbackResult("Tipo non valido.", answer_text="⚠️")

        text = render_template(cfg.blocks, payload, transform=cfg.payload_transform)
        return CallbackResult(text, answer_text="✅ Eseguito")

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

    def __init__(
        self,
        *,
        config: ControlPlaneConfig,
        router: CommandRouter,
        dashboard_manager=None,  # DashboardManager | None
        scope_resolver=None,     # ScopeResolver | None
    ) -> None:
        self._config = config
        self._router = router
        self._dashboard_manager = dashboard_manager
        self._scope_resolver = scope_resolver
        self._app = None
        self._keyboard_users: set[int] = set()

    def _build_app(self):
        from telegram.ext import Application, CallbackQueryHandler, MessageHandler, filters

        app = (
            Application.builder()
            .token(self._config.token)
            .request(build_telegram_request())
            .build()
        )
        app.add_handler(MessageHandler(filters.COMMAND, self._on_command))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_text_message))
        app.add_handler(CallbackQueryHandler(self._on_callback_query))
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
        # route() is synchronous and performs blocking SQLite I/O (audit + queries).
        # Run it off the event loop so a slow/locked DB cannot freeze polling, the
        # notification dispatcher, or other concurrent sends sharing this loop.
        result = await asyncio.to_thread(
            self._router.route,
            command_text=message.text or "",
            message_id=message.message_id,
            chat_id=message.chat_id,
            thread_id=message.message_thread_id,
            user_id=user.id,
            username=user.username,
        )
        if result.reply_text is None:
            return

        # Dashboard command: delegate to DashboardManager
        if result.reply_text == "__DASHBOARD__":
            if self._dashboard_manager is not None and self._scope_resolver is not None:
                scope = self._scope_resolver.resolve(message.message_thread_id)
                await self._dashboard_manager.create(
                    scope=scope,
                    chat_id=message.chat_id,
                    thread_id=message.message_thread_id or 0,
                )
            return

        command_name, _ = _parse(message.text or "")
        if command_name == "start" and result.decision == "EXECUTED":
            await self._send_reply_keyboard(update, user_id=user.id, force=True)

        send_kwargs: dict[str, object] = {
            "chat_id": message.chat_id,
            "text": result.reply_text,
        }
        if message.message_thread_id is not None:
            send_kwargs["message_thread_id"] = message.message_thread_id
        if result.keyboard is not None:
            send_kwargs["reply_markup"] = result.keyboard
        try:
            await asyncio.wait_for(
                context.bot.send_message(**send_kwargs),
                timeout=_COMMAND_SEND_TIMEOUT_SECONDS,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("control plane command reply send failed: %s", exc)

    async def _on_callback_query(self, update, context) -> None:
        del context
        query = update.callback_query
        user = update.effective_user
        if query is None or user is None:
            return

        # Auth check — reject unauthorized users
        if not self._router._auth.is_authorized_user(user.id):
            await query.answer(text="Unauthorized", show_alert=False)
            return

        # Determine if this is an emergency callback (kind:action:token format)
        # or a dashboard callback.
        callback_data = query.data or ""
        parts = callback_data.split(":", 2)
        is_emergency = (
            len(parts) == 3
            and parts[0] in ("close_all", "close_single", "cancel_all")
            and parts[1] in ("confirm", "cancel")
        )

        if is_emergency:
            result = await asyncio.to_thread(
                self._router.handle_callback,
                callback_data=callback_data,
                user_id=user.id,
                chat_id=query.message.chat_id if query.message else 0,
                message_id=query.message.message_id if query.message else 0,
                thread_id=getattr(query.message, "message_thread_id", None) if query.message else None,
                created_by=str(user.id),
            )
            await query.answer(result.answer_text or "")
            if result.delete_message:
                try:
                    await query.message.delete()
                except Exception:  # noqa: BLE001
                    pass
                return
            if result.reply_text:
                try:
                    await query.message.edit_text(result.reply_text)
                except Exception:  # noqa: BLE001
                    pass
            return

        # Dashboard callbacks
        if self._dashboard_manager is None:
            await query.answer()
            return
        if query.message is None or not self._router._auth.is_authorized_chat(query.message.chat_id):
            await query.answer()
            return
        await query.answer()  # acknowledge immediately
        await self._dashboard_manager.handle_callback(query, callback_data or "noop")

    async def run(self) -> None:
        self._app = self._build_app()
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(drop_pending_updates=True)
        # PTB 20+ name-mangles __polling_task → _Updater__polling_task.
        # Awaiting it means we block until PTB's network_retry_loop exits,
        # which only happens when updater.stop() is called.
        updater = self._app.updater
        polling_task = getattr(updater, "_Updater__polling_task", None)
        try:
            if polling_task is not None:
                await polling_task
            else:
                while updater.running:
                    await asyncio.sleep(1)
        finally:
            with contextlib.suppress(Exception):
                await updater.stop()
            with contextlib.suppress(Exception):
                await self._app.stop()
            with contextlib.suppress(Exception):
                await self._app.shutdown()
            self._app = None

    async def shutdown(self) -> None:
        if self._app is None:
            return
        try:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
        finally:
            self._app = None


__all__ = ["CallbackResult", "CommandRouter", "RouteResult", "TelegramControlBot"]
