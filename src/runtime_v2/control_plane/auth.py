from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from src.runtime_v2.control_plane.models import ControlPlaneConfig

AuthDecision = Literal["OK", "IGNORE", "REJECT_UNAUTHORIZED"]

_DASHBOARD_ALLOWED_FROM_CLEAN_LOG = frozenset({"dashboard"})
_DASH_ACTION_RE = re.compile(r'^(trade|cancel|close)_\d+$')


@dataclass(frozen=True)
class AuthResult:
    decision: AuthDecision
    reason: str | None = None


class AuthValidator:
    """Stateless per-update authorization."""

    def __init__(self, config: ControlPlaneConfig) -> None:
        default_acc = config.get_account(None)
        self._chat_id = default_acc.chat_id
        self._commands_thread_id = default_acc.topics.commands.thread_id
        self._authorized_users = frozenset(config.authorized_users)
        self._delivery_mode = config.delivery_mode

        # All clean_log thread ids across all accounts — /dashboard only.
        clean_log_tids: set[int] = set()
        for acc in config.per_account.values():
            cl = acc.topics.clean_log
            if cl.thread_id is not None:
                clean_log_tids.add(cl.thread_id)
            for tid in cl.per_trader.values():
                if tid is not None:
                    clean_log_tids.add(tid)
        self._clean_log_thread_ids = frozenset(clean_log_tids)

    def validate(
        self,
        chat_id: int,
        thread_id: int | None,
        user_id: int,
        command_name: str | None = None,
    ) -> AuthResult:
        if chat_id != self._chat_id:
            return AuthResult("IGNORE", "wrong_chat")

        # In private_bot mode thread_id is not sent by Telegram and is ignored here.
        if self._delivery_mode == "supergroup_topics":
            if thread_id == self._commands_thread_id:
                pass  # all commands allowed from commands topic
            elif thread_id in self._clean_log_thread_ids and (
                command_name in _DASHBOARD_ALLOWED_FROM_CLEAN_LOG
                or _DASH_ACTION_RE.match(command_name or "")
            ):
                pass  # /dashboard and trade/cancel/close_N allowed from any clean_log topic
            else:
                return AuthResult("IGNORE", "wrong_topic")

        if user_id not in self._authorized_users:
            return AuthResult("REJECT_UNAUTHORIZED", "unauthorized_user")
        return AuthResult("OK")

    def is_authorized_user(self, user_id: int) -> bool:
        return user_id in self._authorized_users

    def is_authorized_chat(self, chat_id: int) -> bool:
        return chat_id == self._chat_id


__all__ = ["AuthDecision", "AuthResult", "AuthValidator"]
