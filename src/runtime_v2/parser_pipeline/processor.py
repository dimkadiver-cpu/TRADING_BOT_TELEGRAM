from __future__ import annotations

import logging
from datetime import datetime, timezone

from src.parser_v2.core.runtime import UniversalParserRuntime
from src.parser_v2.profiles.registry import get_parser_v2_profile
from src.runtime_v2.parser_pipeline.models import CanonicalParseResult, ParserJobStatus
from src.runtime_v2.persistence.canonical_messages import CanonicalMessageRepository
from src.runtime_v2.trader_resolution.models import ParserDispatchCandidate

logger = logging.getLogger(__name__)


class ParserPipelineProcessor:
    def __init__(
        self,
        *,
        runtime: UniversalParserRuntime | None = None,
        canonical_repo: CanonicalMessageRepository,
    ) -> None:
        self._runtime = runtime or UniversalParserRuntime()
        self._canonical_repo = canonical_repo

    def process(
        self,
        candidate: ParserDispatchCandidate,
        run_context: str = "live",
    ) -> CanonicalParseResult | ParserJobStatus:
        raw_message_id = candidate.raw_message.raw_message_id
        raw_text = candidate.raw_message.raw_text or ""

        try:
            profile = get_parser_v2_profile(candidate.parser_profile)
        except KeyError:
            logger.error(
                "Unknown parser profile %r for raw_message_id=%d",
                candidate.parser_profile,
                raw_message_id,
            )
            return ParserJobStatus(
                raw_message_id=raw_message_id,
                status="failed",
                reason="unknown_parser_profile",
            )

        try:
            canonical = self._runtime.parse(raw_text, candidate.parser_context, profile)
        except Exception:
            logger.exception(
                "Parser runtime error for raw_message_id=%d profile=%r",
                raw_message_id,
                candidate.parser_profile,
            )
            return ParserJobStatus(
                raw_message_id=raw_message_id,
                status="failed",
                reason="parser_runtime_error",
            )

        try:
            canonical_message_id = self._canonical_repo.save(
                raw_message_id, canonical, run_context
            )
        except Exception:
            logger.exception("Persistence error for raw_message_id=%d", raw_message_id)
            return ParserJobStatus(
                raw_message_id=raw_message_id,
                status="failed",
                reason="persistence_error",
            )

        return CanonicalParseResult(
            raw_message_id=raw_message_id,
            canonical_message_id=canonical_message_id,
            parser_profile=canonical.parser_profile,
            primary_class=canonical.primary_class,
            parse_status=canonical.parse_status,
            canonical_message=canonical,
            warnings=canonical.warnings,
            parsed_at=datetime.now(timezone.utc),
        )


__all__ = ["ParserPipelineProcessor"]
