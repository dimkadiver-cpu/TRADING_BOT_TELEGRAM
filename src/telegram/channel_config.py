"""Channel configuration loader with hot-reload support."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from pathlib import Path
import threading
import time
from typing import Callable

import yaml


@dataclass(slots=True)
class ChannelEntry:
    chat_id: int
    label: str
    active: bool
    trader_id: str | None
    topic_id: int | None = None
    blacklist: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ChannelsConfig:
    recovery_max_hours: int
    blacklist_global: list[str]
    channels: list[ChannelEntry]

    @property
    def active_channels(self) -> list[ChannelEntry]:
        return [channel for channel in self.channels if channel.active]

    @property
    def active_chat_ids(self) -> set[int]:
        return {channel.chat_id for channel in self.active_channels}

    def entries_for_chat(self, chat_id: int) -> list[ChannelEntry]:
        """Return all entries (active or not) for a given chat_id."""
        return [e for e in self.channels if e.chat_id == chat_id]

    def match_entry(self, chat_id: int, topic_id: int | None) -> ChannelEntry | None:
        """Return the best matching entry using precedence: topic-specific > forum-wide.

        - If topic_id is not None: exact (chat_id, topic_id) first, then (chat_id, None).
        - If topic_id is None: only forum-wide (chat_id, None).
        """
        topic_specific: ChannelEntry | None = None
        forum_wide: ChannelEntry | None = None
        for entry in self.channels:
            if entry.chat_id != chat_id:
                continue
            if topic_id is not None and entry.topic_id == topic_id:
                topic_specific = entry
            elif entry.topic_id is None:
                forum_wide = entry
        return topic_specific if topic_specific is not None else forum_wide

    def channel_for(self, chat_id: int | None) -> ChannelEntry | None:
        """Return first entry matching chat_id (legacy; ambiguous for multi-topic chats)."""
        if chat_id is None:
            return None
        for channel in self.channels:
            if channel.chat_id == chat_id:
                return channel
        return None


def _parse_topic_id(value: object) -> int | None:
    if value is None:
        return None
    topic_id = int(value)
    if topic_id <= 0:
        raise ValueError(f"topic_id must be a positive integer, got {topic_id!r}")
    return topic_id


def load_channels_config(path: str) -> ChannelsConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    recovery = raw.get("recovery") or {}
    channels_raw = raw.get("channels") or []

    seen_scopes: set[tuple[int, int | None]] = set()
    channels: list[ChannelEntry] = []
    for channel in channels_raw:
        chat_id = int(channel["chat_id"])
        topic_id = _parse_topic_id(channel.get("topic_id"))
        scope = (chat_id, topic_id)
        if scope in seen_scopes:
            raise ValueError(
                f"Duplicate scope in channels config: chat_id={chat_id}, topic_id={topic_id}"
            )
        seen_scopes.add(scope)
        channels.append(
            ChannelEntry(
                chat_id=chat_id,
                label=str(channel.get("label", "")),
                active=bool(channel.get("active", True)),
                trader_id=channel.get("trader_id") or None,
                topic_id=topic_id,
                blacklist=[str(tag) for tag in (channel.get("blacklist") or [])],
            )
        )
    return ChannelsConfig(
        recovery_max_hours=int(recovery.get("max_hours", 4)),
        blacklist_global=[str(tag) for tag in (raw.get("blacklist_global") or [])],
        channels=channels,
    )


class ChannelConfigWatcher:
    """Monitors channels.yaml and calls on_reload(new_config) on file change."""

    def __init__(
        self,
        path: str,
        on_reload: Callable[[ChannelsConfig], None],
        logger: logging.Logger | None = None,
        poll_interval_seconds: float = 0.1,
    ) -> None:
        self._path = Path(path).resolve()
        self._on_reload = on_reload
        self._logger = logger or logging.getLogger(__name__)
        self._poll_interval_seconds = poll_interval_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_signature: tuple[int, int] | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._last_signature = self._current_signature()
        self._thread = threading.Thread(target=self._run, name="channels-config-watcher", daemon=True)
        self._thread.start()
        self._logger.info("channel config watcher started | path=%s", self._path)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _run(self) -> None:
        while not self._stop_event.wait(self._poll_interval_seconds):
            signature = self._current_signature()
            if signature is None or signature == self._last_signature:
                continue
            self._last_signature = signature
            try:
                config = load_channels_config(str(self._path))
                self._on_reload(config)
                self._logger.info(
                    "channels.yaml reloaded | channels=%d active=%d",
                    len(config.channels),
                    len(config.active_channels),
                )
            except Exception:
                self._logger.exception("failed to reload channels.yaml | path=%s", self._path)

    def _current_signature(self) -> tuple[int, int] | None:
        try:
            stat = self._path.stat()
        except FileNotFoundError:
            return None
        return (stat.st_mtime_ns, stat.st_size)
