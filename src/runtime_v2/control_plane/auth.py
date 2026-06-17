from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from src.runtime_v2.control_plane.models import ControlPlaneConfig

AuthDecision = Literal["OK", "IGNORE", "REJECT_UNAUTHORIZED"]


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

    def validate(
        self, chat_id: int, thread_id: int | None, user_id: int
    ) -> AuthResult:
        if chat_id != self._chat_id:
            return AuthResult("IGNORE", "wrong_chat")
        # In private_bot mode thread_id is not sent by Telegram and is ignored here.
        if self._delivery_mode == "supergroup_topics":
            if thread_id != self._commands_thread_id:
                return AuthResult("IGNORE", "wrong_topic")
        if user_id not in self._authorized_users:
            return AuthResult("REJECT_UNAUTHORIZED", "unauthorized_user")
        return AuthResult("OK")


__all__ = ["AuthDecision", "AuthResult", "AuthValidator"]
