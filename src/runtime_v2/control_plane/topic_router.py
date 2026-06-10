# src/runtime_v2/control_plane/topic_router.py
from __future__ import annotations

import logging

from src.runtime_v2.control_plane.models import ControlPlaneConfig

logger = logging.getLogger(__name__)


class TopicRouter:
    """Maps destination -> (chat_id, thread_id). thread_id=None in private_bot mode.

    Per-trader CLEAN_LOG routing: if config.topics.clean_log.per_trader contains a
    trader_id key, that thread_id overrides the global clean_log thread.
    Traders not listed fall back to the global clean_log.thread_id.
    """

    def __init__(
        self,
        config: ControlPlaneConfig,
        known_trader_ids: set[str] | None = None,
    ) -> None:
        self._chat_id = config.chat_id
        self._delivery_mode = config.delivery_mode
        self._thread_map: dict[str, int | None] = {
            "CLEAN_LOG": config.topics.clean_log.thread_id,
            "TECH_LOG": config.topics.tech_log.thread_id,
            "COMMANDS_REPLY": config.topics.commands.thread_id,
        }
        self._per_trader: dict[str, int | None] = dict(config.topics.clean_log.per_trader)
        if known_trader_ids is not None:
            for tid in self._per_trader:
                if tid not in known_trader_ids:
                    logger.warning(
                        "topic_router: per_trader key %r not found in channels.yaml — entry may be stale",
                        tid,
                    )

    def route(self, destination: str, trader_id: str | None = None) -> tuple[int, int | None]:
        """Return (chat_id, thread_id). thread_id is None in private_bot mode.

        For CLEAN_LOG destinations, trader_id is used to look up a per-trader thread override.
        """
        if destination not in self._thread_map:
            raise ValueError(f"Unknown notification destination: {destination}")
        if self._delivery_mode == "private_bot":
            return (self._chat_id, None)
        if destination == "CLEAN_LOG" and trader_id and trader_id in self._per_trader:
            return (self._chat_id, self._per_trader[trader_id])
        return (self._chat_id, self._thread_map[destination])


__all__ = ["TopicRouter"]
