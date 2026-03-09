"""Resolve internal trader id from Telegram source metadata."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from src.core.trader_tags import normalize_trader_aliases, normalize_trader_tag


@dataclass(slots=True)
class TelegramSource:
    chat_id: str
    chat_username: str | None = None
    chat_title: str | None = None


@dataclass(slots=True)
class TraderResolution:
    trader_id: str | None
    matched_by: str | None = None
    matched_value: str | None = None


class TelegramSourceTraderMapper:
    def __init__(
        self,
        by_chat_id: dict[str, str],
        by_chat_username: dict[str, str],
        by_chat_title: dict[str, str],
        multi_trader_chat_ids: set[str],
        trader_aliases: dict[str, str],
        known_trader_ids: set[str],
    ) -> None:
        self._by_chat_id = by_chat_id
        self._by_chat_username = {self._normalize_username(k): v for k, v in by_chat_username.items()}
        self._by_chat_title = {self._normalize_title(k): v for k, v in by_chat_title.items()}
        self._multi_trader_chat_ids = multi_trader_chat_ids
        self._trader_aliases = normalize_trader_aliases(trader_aliases)
        self._known_trader_ids = known_trader_ids

    @classmethod
    def from_json_file(
        cls,
        file_path: str,
        trader_aliases: dict[str, str] | None = None,
        known_trader_ids: set[str] | None = None,
    ) -> "TelegramSourceTraderMapper":
        path = Path(file_path)
        if not path.exists():
            raw: dict[str, dict[str, str]] = {}
        else:
            raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            by_chat_id=raw.get("chat_id_to_trader", {}),
            by_chat_username=raw.get("chat_username_to_trader", {}),
            by_chat_title=raw.get("chat_title_to_trader", {}),
            multi_trader_chat_ids=set(raw.get("multi_trader_chat_ids", [])),
            trader_aliases=trader_aliases or {},
            known_trader_ids=known_trader_ids or set(),
        )

    def resolve(self, source: TelegramSource) -> TraderResolution:
        if source.chat_id in self._multi_trader_chat_ids:
            return TraderResolution(trader_id=None, matched_by="multi_trader_chat", matched_value=source.chat_id)

        by_id = self._by_chat_id.get(source.chat_id)
        if by_id:
            trader = self._normalize_trader_id(by_id)
            if trader:
                return TraderResolution(trader_id=trader, matched_by="chat_id", matched_value=source.chat_id)

        if source.chat_username:
            username = self._normalize_username(source.chat_username)
            by_username = self._by_chat_username.get(username)
            if by_username:
                trader = self._normalize_trader_id(by_username)
                if trader:
                    return TraderResolution(trader_id=trader, matched_by="chat_username", matched_value=username)

        if source.chat_title:
            title = self._normalize_title(source.chat_title)
            by_title = self._by_chat_title.get(title)
            if by_title:
                trader = self._normalize_trader_id(by_title)
                if trader:
                    return TraderResolution(trader_id=trader, matched_by="chat_title", matched_value=title)

        return TraderResolution(trader_id=None)

    def _normalize_trader_id(self, candidate: str) -> str | None:
        if not candidate:
            return None
        direct = candidate.strip()
        if direct in self._known_trader_ids:
            return direct

        alias = self._trader_aliases.get(normalize_trader_tag(direct) or direct)
        if alias and alias in self._known_trader_ids:
            return alias
        return None

    @staticmethod
    def _normalize_username(value: str) -> str:
        return value.strip().lstrip("@").lower()

    @staticmethod
    def _normalize_title(value: str) -> str:
        return value.strip().lower()
