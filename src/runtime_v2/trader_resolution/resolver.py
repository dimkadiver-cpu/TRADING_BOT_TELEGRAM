from __future__ import annotations

from datetime import datetime, timezone

from src.telegram.effective_trader import EffectiveTraderResolver, EffectiveTraderContext
from src.runtime_v2.trader_resolution.channel_config_resolver import ChannelConfigResolver
from src.runtime_v2.trader_resolution.models import ResolvedTraderContext, ResolutionMethod
from src.runtime_v2.intake.models import RawMessageEnvelope

# Maps EffectiveTraderResolver method strings to ResolutionMethod literals.
_METHOD_MAP: dict[str, ResolutionMethod] = {
    "content_alias": "content_alias",
    "content_alias_ambiguous": "content_alias_ambiguous",
    "reply_chain": "reply_chain",
    "reply_chain_alias": "reply_chain_alias",
    "source_chat_id": "source_chat_id",
    "source_chat_username": "source_chat_username",
    "source_chat_title": "source_chat_title",
    "unresolved": "unresolved",
}


class RuntimeV2TraderResolver:
    """Resolves effective trader using config-first strategy.

    Step 1: channels.yaml lookup by (source_chat_id, source_topic_id).
           Returns immediately if entry is active and has trader_id.
    Step 2: EffectiveTraderResolver (text alias priority → reply-chain).

    Note: EffectiveTraderResolver currently has a hardcoded reply-chain depth (10).
    IntakeConfig.reply_chain_depth_limit declares the intended contract (default 5).
    Enforcement requires a future update to EffectiveTraderResolver to accept max_depth.
    """

    def __init__(
        self,
        channel_config_resolver: ChannelConfigResolver,
        effective_trader_resolver: EffectiveTraderResolver,
    ) -> None:
        self._channel_config = channel_config_resolver
        self._effective = effective_trader_resolver

    def resolve(self, envelope: RawMessageEnvelope) -> ResolvedTraderContext:
        now = datetime.now(timezone.utc)

        # Step 1: config-driven via channels.yaml
        entry = self._channel_config.lookup(envelope.source_chat_id, envelope.source_topic_id)
        if entry is not None and entry.active and entry.trader_id:
            method: ResolutionMethod = (
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

        # Step 2: EffectiveTraderResolver (text → reply-chain)
        ctx = EffectiveTraderContext(
            source_chat_id=envelope.source_chat_id,
            source_chat_username=None,
            source_chat_title=envelope.source_chat_title,
            raw_text=envelope.raw_text,
            reply_to_message_id=envelope.reply_to_message_id,
        )
        result = self._effective.resolve(ctx)
        mapped_method: ResolutionMethod = _METHOD_MAP.get(result.method, "unresolved")
        return ResolvedTraderContext(
            raw_message_id=envelope.raw_message_id,
            trader_id=result.trader_id,
            method=mapped_method,
            detail=result.detail,
            is_ambiguous=(result.method == "content_alias_ambiguous"),
            resolved_at=now,
        )
