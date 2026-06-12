"""Single-entry trader resolution for all channel types.

Priority order:
  1. Config static (channels.yaml trader_id)         → source_chat_id / source_topic_config
  2. Alias in current message text (per-topic)        → content_alias
  3. Pattern extractors (hardcoded fallback)          → content_alias
  4. Reply chain (reply_to_message_id)                → reply_chain / reply_chain_alias
  5. Single t.me link in text                         → link
  6. Multiple t.me links — concordant/discordant      → link_multi / content_alias_ambiguous
  7. No signal                                        → unresolved
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from src.core.trader_tags import find_normalized_trader_tags
from src.runtime_v2.intake.models import RawMessageEnvelope
from src.runtime_v2.persistence.raw_messages import RawMessageRepository
from src.runtime_v2.trader_resolution.channel_config_resolver import ChannelConfigResolver
from src.runtime_v2.trader_resolution.models import ResolvedTraderContext
from src.telegram.pattern_extractors import TextPatternCatalog

_TELEGRAM_LINK_RE = re.compile(
    r"(?:https?://)?t\.me/(?:c/\d+|[A-Za-z0-9_]+)/(\d+)",
    re.IGNORECASE,
)


class TraderResolver:
    def __init__(
        self,
        channel_config: ChannelConfigResolver,
        raw_repo: RawMessageRepository,
        pattern_catalog: TextPatternCatalog | None = None,
    ) -> None:
        self._config = channel_config
        self._raw_repo = raw_repo
        self._pattern_catalog = pattern_catalog or TextPatternCatalog()

    def resolve(self, envelope: RawMessageEnvelope) -> ResolvedTraderContext:
        now = datetime.now(timezone.utc)

        # Step 1: config statico
        entry = self._config.lookup(envelope.source_chat_id, envelope.source_topic_id)
        if entry is not None and entry.active and entry.trader_id:
            method = (
                "source_topic_config"
                if envelope.source_topic_id is not None and entry.topic_id is not None
                else "source_chat_id"
            )
            return ResolvedTraderContext(
                raw_message_id=envelope.raw_message_id,
                trader_id=entry.trader_id,
                method=method,
                detail=None,
                is_ambiguous=False,
                resolved_at=now,
            )

        aliases = entry.aliases if entry is not None else {}
        max_depth = entry.resolution_max_depth if entry is not None else 5
        pattern_group = entry.pattern_group if entry is not None else None
        resolution_mode = entry.resolution_mode if entry is not None else "default"

        # Step 2: alias + pattern nel testo corrente
        trader_id, is_ambiguous = self._from_text(
            envelope.raw_text,
            aliases,
            pattern_group,
            allow_aliases=(resolution_mode != "patterns_only"),
        )
        if is_ambiguous:
            return ResolvedTraderContext(
                raw_message_id=envelope.raw_message_id,
                trader_id=None,
                method="content_alias_ambiguous",
                detail=None,
                is_ambiguous=True,
                resolved_at=now,
            )
        if trader_id is not None:
            return ResolvedTraderContext(
                raw_message_id=envelope.raw_message_id,
                trader_id=trader_id,
                method="content_alias",
                detail=None,
                is_ambiguous=False,
                resolved_at=now,
            )

        if resolution_mode == "patterns_only":
            return ResolvedTraderContext(
                raw_message_id=envelope.raw_message_id,
                trader_id=None,
                method="unresolved",
                detail=None,
                is_ambiguous=False,
                resolved_at=now,
            )

        # Step 3-4: reply chain
        if envelope.reply_to_message_id is not None:
            chain = self._resolve_chain(
                envelope.source_chat_id, envelope.reply_to_message_id,
                aliases, pattern_group, max_depth,
            )
            if chain is not None:
                return ResolvedTraderContext(
                    raw_message_id=envelope.raw_message_id,
                    trader_id=chain[0],
                    method=chain[1],
                    detail=chain[2],
                    is_ambiguous=False,
                    resolved_at=now,
                )

        # Step 5-6: link nel testo
        links = _extract_links(envelope.raw_text)
        if len(links) == 1:
            chain = self._resolve_chain(
                envelope.source_chat_id, links[0], aliases, pattern_group, max_depth,
            )
            if chain is not None:
                return ResolvedTraderContext(
                    raw_message_id=envelope.raw_message_id,
                    trader_id=chain[0],
                    method="link",
                    detail=chain[2],
                    is_ambiguous=False,
                    resolved_at=now,
                )
        elif len(links) > 1:
            traders: set[str] = set()
            for link_msg_id in links:
                chain = self._resolve_chain(
                    envelope.source_chat_id, link_msg_id, aliases, pattern_group, max_depth,
                )
                if chain is not None:
                    traders.add(chain[0])
            if len(traders) == 1:
                return ResolvedTraderContext(
                    raw_message_id=envelope.raw_message_id,
                    trader_id=traders.pop(),
                    method="link_multi",
                    detail=None,
                    is_ambiguous=False,
                    resolved_at=now,
                )
            if len(traders) > 1:
                return ResolvedTraderContext(
                    raw_message_id=envelope.raw_message_id,
                    trader_id=None,
                    method="content_alias_ambiguous",
                    detail=None,
                    is_ambiguous=True,
                    resolved_at=now,
                )

        return ResolvedTraderContext(
            raw_message_id=envelope.raw_message_id,
            trader_id=None,
            method="unresolved",
            detail=None,
            is_ambiguous=False,
            resolved_at=now,
        )

    def _from_text(
        self,
        raw_text: str | None,
        aliases: dict[str, str],
        pattern_group: str | None,
        *,
        allow_aliases: bool = True,
    ) -> tuple[str | None, bool]:
        """Returns (trader_id, is_ambiguous). None+False means no match."""
        if not raw_text:
            return None, False
        if allow_aliases and aliases:
            tags = find_normalized_trader_tags(raw_text)
            found = {aliases[tag] for tag in tags if tag in aliases}
            if len(found) == 1:
                return found.pop(), False
            if len(found) > 1:
                return None, True
        pattern_result = self._pattern_catalog.resolve(pattern_group, raw_text)
        if pattern_result.is_ambiguous:
            return None, True
        if pattern_result.trader_id is not None:
            return pattern_result.trader_id, False
        return None, False

    def _resolve_chain(
        self,
        source_chat_id: str,
        start_msg_id: int,
        aliases: dict[str, str],
        pattern_group: str | None,
        max_depth: int,
    ) -> tuple[str, str, str] | None:
        """Returns (trader_id, method, detail) or None if not resolved."""
        visited: set[int] = set()
        current_id: int | None = start_msg_id
        depth = 0

        while current_id is not None and depth < max_depth:
            if current_id in visited:
                break
            visited.add(current_id)

            node = self._raw_repo.get_chain_node(source_chat_id, current_id)
            if node is None:
                break

            resolved = node.resolved_trader_id or node.source_trader_id
            if resolved:
                return resolved, "reply_chain", str(current_id)

            text_trader, _ = self._from_text(node.raw_text, aliases, pattern_group)
            if text_trader:
                return text_trader, "reply_chain_alias", str(current_id)

            current_id = node.reply_to_message_id
            depth += 1

        return None


def _extract_links(raw_text: str | None) -> list[int]:
    if not raw_text:
        return []
    return [int(m.group(1)) for m in _TELEGRAM_LINK_RE.finditer(raw_text)]
