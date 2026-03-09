"""Resolve effective trader from content, reply inheritance, then source fallback."""

from __future__ import annotations

from dataclasses import dataclass

from src.core.trader_tags import find_normalized_trader_tags, normalize_trader_aliases
from src.storage.raw_messages import RawMessageStore
from src.telegram.trader_mapping import TelegramSource, TelegramSourceTraderMapper


@dataclass(slots=True)
class EffectiveTraderContext:
    source_chat_id: str
    source_chat_username: str | None
    source_chat_title: str | None
    raw_text: str | None
    reply_to_message_id: int | None


@dataclass(slots=True)
class EffectiveTraderResult:
    trader_id: str | None
    method: str
    detail: str | None = None


class EffectiveTraderResolver:
    def __init__(
        self,
        source_mapper: TelegramSourceTraderMapper,
        raw_store: RawMessageStore,
        trader_aliases: dict[str, str],
        known_trader_ids: set[str],
    ) -> None:
        self._source_mapper = source_mapper
        self._raw_store = raw_store
        self._known_trader_ids = known_trader_ids
        self._alias_to_trader: dict[str, str] = {}
        for alias, trader_id in normalize_trader_aliases(trader_aliases).items():
            if trader_id not in known_trader_ids:
                continue
            self._alias_to_trader[alias] = trader_id

    def resolve(self, ctx: EffectiveTraderContext) -> EffectiveTraderResult:
        text_result = self._from_text(ctx.raw_text)
        if text_result.trader_id or text_result.method == "content_alias_ambiguous":
            return text_result

        if ctx.reply_to_message_id is not None:
            parent = self._raw_store.get_by_source_and_message_id(
                source_chat_id=ctx.source_chat_id,
                telegram_message_id=ctx.reply_to_message_id,
            )
            if parent and parent.source_trader_id:
                return EffectiveTraderResult(
                    trader_id=parent.source_trader_id,
                    method="reply_parent",
                    detail=str(parent.telegram_message_id),
                )

        source_result = self._source_mapper.resolve(
            TelegramSource(
                chat_id=ctx.source_chat_id,
                chat_username=ctx.source_chat_username,
                chat_title=ctx.source_chat_title,
            )
        )
        if source_result.trader_id:
            return EffectiveTraderResult(
                trader_id=source_result.trader_id,
                method=f"source_{source_result.matched_by}",
                detail=source_result.matched_value,
            )

        return EffectiveTraderResult(trader_id=None, method="unresolved")

    def _from_text(self, raw_text: str | None) -> EffectiveTraderResult:
        if not raw_text:
            return EffectiveTraderResult(trader_id=None, method="content_alias_missing")

        found: list[str] = []
        for alias in find_normalized_trader_tags(raw_text):
            trader_id = self._alias_to_trader.get(alias)
            if trader_id:
                found.append(trader_id)

        unique = sorted(set(found))
        if len(unique) == 1:
            return EffectiveTraderResult(trader_id=unique[0], method="content_alias")
        if len(unique) > 1:
            return EffectiveTraderResult(trader_id=None, method="content_alias_ambiguous")
        return EffectiveTraderResult(trader_id=None, method="content_alias_missing")
