from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import yaml

from src.core.trader_tags import normalize_trader_aliases


@dataclass(slots=True, frozen=True)
class ChannelEntry:
    chat_id: str
    topic_id: int | None
    label: str | None
    active: bool
    trader_id: str | None
    parser_profile: str  # defaults to trader_id when not overridden in yaml
    blacklist: list[str]
    aliases: dict[str, str]          # normalized tag → trader_id; empty for single-trader
    resolution_max_depth: int        # default 5; used only when trader_id is None


class ChannelConfigResolver:
    """Loads channels.yaml and provides O(1) lookup by (source_chat_id, topic_id).

    Call reload() to refresh after a file change. Watchdog hot-reload is
    the caller's responsibility — this class only manages the in-memory index.
    """

    def __init__(self, config_path: str) -> None:
        self._config_path = config_path
        self._index: dict[tuple[str, int | None], ChannelEntry] = {}
        self._global_blacklist: list[str] = []
        self.reload()

    def reload(self) -> None:
        with open(self._config_path, encoding="utf-8") as f:
            data: dict[str, Any] = yaml.safe_load(f)
        index: dict[tuple[str, int | None], ChannelEntry] = {}
        for raw in data.get("channels", []):
            chat_id = str(raw["chat_id"])
            topic_id: int | None = raw.get("topic_id")
            trader_id: str | None = raw.get("trader_id")
            parser_profile: str = raw.get("parser_profile") or trader_id or ""
            resolution = raw.get("resolution") or {}
            aliases_raw: dict[str, str] = resolution.get("aliases") or {}
            aliases = normalize_trader_aliases(aliases_raw)
            max_depth = max(1, int(resolution.get("max_depth", 5)))
            entry = ChannelEntry(
                chat_id=chat_id,
                topic_id=topic_id,
                label=raw.get("label"),
                active=bool(raw.get("active", False)),
                trader_id=trader_id,
                parser_profile=parser_profile,
                blacklist=list(raw.get("blacklist", [])),
                aliases=aliases,
                resolution_max_depth=max_depth,
            )
            index[(chat_id, topic_id)] = entry
        self._index = index
        self._global_blacklist = list(data.get("blacklist_global", []))

    def lookup(self, source_chat_id: str, topic_id: int | None) -> ChannelEntry | None:
        """Returns ChannelEntry or None if not configured.

        Lookup order:
        1. Exact match on (source_chat_id, topic_id)
        2. If topic_id is not None, fallback to (source_chat_id, None)
        Caller is responsible for checking entry.active.
        """
        entry = self._index.get((source_chat_id, topic_id))
        if entry is not None:
            return entry
        if topic_id is not None:
            return self._index.get((source_chat_id, None))
        return None

    def is_globally_blacklisted(self, text: str) -> bool:
        return any(phrase in text for phrase in self._global_blacklist)
