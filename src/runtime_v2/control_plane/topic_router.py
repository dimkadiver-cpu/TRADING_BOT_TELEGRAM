# src/runtime_v2/control_plane/topic_router.py
from __future__ import annotations

from src.runtime_v2.control_plane.models import ControlPlaneConfig, Destination


class TopicRouter:
    """Maps destination -> (chat_id, thread_id). thread_id=None in private_bot mode."""

    def __init__(self, config: ControlPlaneConfig) -> None:
        self._chat_id = config.chat_id
        self._delivery_mode = config.delivery_mode
        self._thread_map: dict[str, int | None] = {
            "CLEAN_LOG": config.topics.clean_log.thread_id,
            "TECH_LOG": config.topics.tech_log.thread_id,
            "COMMANDS_REPLY": config.topics.commands.thread_id,
        }

    def route(self, destination: str) -> tuple[int, int | None]:
        """Return (chat_id, thread_id). thread_id is None in private_bot mode."""
        if destination not in self._thread_map:
            raise ValueError(f"Unknown notification destination: {destination}")
        if self._delivery_mode == "private_bot":
            return (self._chat_id, None)
        return (self._chat_id, self._thread_map[destination])


__all__ = ["TopicRouter"]
