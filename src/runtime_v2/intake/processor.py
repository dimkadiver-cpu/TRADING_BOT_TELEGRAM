from __future__ import annotations

# MUST be at module level for test patching
from src.parser_v2.profiles.registry import list_parser_v2_profiles

from src.parser_v2.contracts.context import ParserContext, RawContext
from src.runtime_v2.intake.eligibility import IntakeEligibilityCheck
from src.runtime_v2.intake.models import IntakeConfig, RawIngestItem
from src.runtime_v2.persistence.raw_messages import RawMessageRepository
from src.runtime_v2.trader_resolution.channel_config_resolver import ChannelConfigResolver
from src.runtime_v2.trader_resolution.models import ParserDispatchCandidate, ResolvedTraderContext
from src.runtime_v2.trader_resolution.resolver import RuntimeV2TraderResolver


class RuntimeV2IntakeProcessor:
    """Processes a RawIngestItem through the full intake pipeline.

    Steps:
    1. Persist (dedup-idempotent)
    2. Global blacklist check
    3. Media-only skip
    4. Eligibility check
    5. Mark as processing
    6. Trader resolution
    7. Ambiguous / unresolved check
    8. Persist resolution
    9. Determine parser_profile
    10. Validate parser_profile exists in parser_v2
    11. Build ParserContext
    12. Mark done
    13. Return ParserDispatchCandidate
    """

    def __init__(
        self,
        repo: RawMessageRepository,
        eligibility: IntakeEligibilityCheck,
        resolver: RuntimeV2TraderResolver,
        channel_config: ChannelConfigResolver,
        config: IntakeConfig,
    ) -> None:
        self._repo = repo
        self._eligibility = eligibility
        self._resolver = resolver
        self._channel_config = channel_config
        self._config = config

    def process(self, item: RawIngestItem) -> ParserDispatchCandidate | None:
        env = self._repo.save_raw(item)

        text_for_blacklist = item.raw_text or ""
        if self._channel_config.is_globally_blacklisted(text_for_blacklist):
            self._repo.set_blacklisted(env.raw_message_id)
            return None

        if item.has_media and not item.raw_text:
            self._repo.set_media_only_skipped(env.raw_message_id)
            return None

        outcome = self._eligibility.check(env)
        if not outcome.eligible:
            self._repo.update_processing_status(env.raw_message_id, "review")
            return None

        self._repo.update_processing_status(env.raw_message_id, "processing")

        # Resolver may assign raw_message_id=0; fix it to match the persisted message.
        resolved: ResolvedTraderContext = self._resolver.resolve(env)
        resolved = resolved.model_copy(update={"raw_message_id": env.raw_message_id})

        if resolved.is_ambiguous or resolved.trader_id is None:
            self._repo.update_processing_status(env.raw_message_id, "review")
            return None

        self._repo.update_trader_resolution(env.raw_message_id, resolved)

        entry = self._channel_config.lookup(env.source_chat_id, env.source_topic_id)
        parser_profile = entry.parser_profile if entry is not None else resolved.trader_id

        if parser_profile not in list_parser_v2_profiles():
            self._repo.update_processing_status(env.raw_message_id, "review")
            return None

        raw_context = RawContext(
            raw_text=env.raw_text or "",
            message_id=env.telegram_message_id,
            reply_to_message_id=env.reply_to_message_id,
            source_chat_id=env.source_chat_id,
            source_topic_id=env.source_topic_id,
        )
        parser_context = ParserContext(
            raw_context=raw_context,
            message_id=env.telegram_message_id,
            reply_to_message_id=env.reply_to_message_id,
            source_chat_id=env.source_chat_id,
            source_topic_id=env.source_topic_id,
        )

        self._repo.update_processing_status(env.raw_message_id, "done")

        return ParserDispatchCandidate(
            raw_message=env,
            resolved_trader=resolved,
            parser_profile=parser_profile,
            parser_context=parser_context,
        )
