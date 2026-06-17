# src/runtime_v2/control_plane/topic_router.py
from __future__ import annotations

import logging

from src.runtime_v2.control_plane.models import ControlPlaneConfig

logger = logging.getLogger(__name__)


class TopicRouter:
    """Maps (account_id, destination, trader_id) -> (chat_id, thread_id).

    Routing priority for CLEAN_LOG:
      1. per_account[account_id].topics.clean_log.per_trader[trader_id]
      2. per_account[account_id].topics.clean_log.thread_id
    For TECH_LOG and COMMANDS_REPLY: per_account[account_id].topics.<dest>.thread_id
    Unknown account_id falls back to default_account.
    In private_bot mode all thread_ids are None.
    """

    def __init__(
        self,
        config: ControlPlaneConfig,
        known_trader_ids: set[str] | None = None,
    ) -> None:
        self._config = config
        self._delivery_mode = config.delivery_mode

        # Warn about stale per_trader keys across all accounts
        if known_trader_ids is not None:
            for account_id, acc in config.per_account.items():
                for tid in acc.topics.clean_log.per_trader:
                    if tid not in known_trader_ids:
                        logger.warning(
                            "topic_router: per_trader key %r in account %r not found in channels.yaml — may be stale",
                            tid, account_id,
                        )

    def route(
        self,
        destination: str,
        account_id: str | None = None,
        trader_id: str | None = None,
    ) -> tuple[int, int | None]:
        """Return (chat_id, thread_id).

        account_id: the execution account. Falls back to default_account if unknown/None.
        trader_id: used only for CLEAN_LOG per-trader overrides.
        """
        if destination not in ("CLEAN_LOG", "TECH_LOG", "COMMANDS_REPLY"):
            raise ValueError(f"Unknown notification destination: {destination}")

        acc = self._config.get_account(account_id)

        if self._delivery_mode == "private_bot":
            return (acc.chat_id, None)

        if destination == "CLEAN_LOG":
            per_trader = acc.topics.clean_log.per_trader
            if trader_id and trader_id in per_trader:
                return (acc.chat_id, per_trader[trader_id])
            return (acc.chat_id, acc.topics.clean_log.thread_id)

        if destination == "TECH_LOG":
            return (acc.chat_id, acc.topics.tech_log.thread_id)

        # COMMANDS_REPLY
        return (acc.chat_id, acc.topics.commands.thread_id)


__all__ = ["TopicRouter"]
