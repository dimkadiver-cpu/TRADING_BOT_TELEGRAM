from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from src.parser_v2.core.runtime import UniversalParserRuntime
from src.parser_v2.profiles.registry import get_parser_v2_profile
from src.runtime_v2.parser_pipeline.models import CanonicalParseResult, ParserJobStatus
from src.runtime_v2.persistence.canonical_messages import CanonicalMessageRepository
from src.runtime_v2.trader_resolution.models import ParserDispatchCandidate
from src.storage.parser_results_v2 import ParserResultV2Record, ParserResultV2Store

logger = logging.getLogger(__name__)


class ParserPipelineProcessor:
    def __init__(
        self,
        *,
        runtime: UniversalParserRuntime | None = None,
        canonical_repo: CanonicalMessageRepository,
        result_v2_store: ParserResultV2Store | None = None,
        live_run_id: int | None = None,
    ) -> None:
        self._runtime = runtime or UniversalParserRuntime()
        self._canonical_repo = canonical_repo
        self._result_v2_store = result_v2_store
        self._live_run_id = live_run_id

    def process(
        self,
        candidate: ParserDispatchCandidate,
        run_context: str = "live",
    ) -> CanonicalParseResult | ParserJobStatus:
        raw_message_id = candidate.raw_message.raw_message_id
        raw_text = candidate.raw_message.raw_text or ""
        trader_id = candidate.resolved_trader.trader_id
        now_iso = datetime.now(timezone.utc).isoformat()

        try:
            profile = get_parser_v2_profile(candidate.parser_profile)
        except KeyError:
            logger.error(
                "Unknown parser profile %r for raw_message_id=%d",
                candidate.parser_profile,
                raw_message_id,
            )
            self._save_v2_error(raw_message_id, trader_id, candidate.parser_profile,
                                "unknown_parser_profile", now_iso)
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
            self._save_v2_error(raw_message_id, trader_id, candidate.parser_profile,
                                "parser_runtime_error", now_iso)
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
            self._save_v2_error(raw_message_id, trader_id, canonical.parser_profile,
                                "persistence_error", now_iso)
            return ParserJobStatus(
                raw_message_id=raw_message_id,
                status="failed",
                reason="persistence_error",
            )

        self._save_v2_ok(raw_message_id, trader_id, canonical, now_iso)

        return CanonicalParseResult(
            raw_message_id=raw_message_id,
            canonical_message_id=canonical_message_id,
            parser_profile=canonical.parser_profile,
            resolved_trader_id=trader_id,
            primary_class=canonical.primary_class,
            parse_status=canonical.parse_status,
            canonical_message=canonical,
            warnings=canonical.warnings,
            parsed_at=datetime.now(timezone.utc),
        )

    def _save_v2_ok(
        self,
        raw_message_id: int,
        trader_id: str | None,
        canonical: object,
        now_iso: str,
    ) -> None:
        if self._result_v2_store is None or self._live_run_id is None:
            return
        try:
            self._result_v2_store.insert_result(ParserResultV2Record(
                run_id=self._live_run_id,
                raw_message_id=raw_message_id,
                trader_id=trader_id,
                parser_profile=canonical.parser_profile,
                primary_class=canonical.primary_class,
                parse_status=canonical.parse_status,
                primary_intent=canonical.primary_intent,
                confidence=canonical.confidence,
                canonical_json=canonical.model_dump_json(),
                warnings_json=json.dumps(canonical.warnings),
                diagnostics_json=json.dumps(canonical.diagnostics),
                error_status="OK",
                error_message=None,
                created_at=now_iso,
            ))
        except Exception:
            logger.exception("parser_results_v2 save error raw_message_id=%d", raw_message_id)

    def _save_v2_error(
        self,
        raw_message_id: int,
        trader_id: str | None,
        parser_profile: str | None,
        reason: str,
        now_iso: str,
    ) -> None:
        if self._result_v2_store is None or self._live_run_id is None:
            return
        try:
            self._result_v2_store.insert_result(ParserResultV2Record(
                run_id=self._live_run_id,
                raw_message_id=raw_message_id,
                trader_id=trader_id,
                parser_profile=parser_profile,
                primary_class=None,
                parse_status=None,
                primary_intent=None,
                confidence=None,
                canonical_json=None,
                warnings_json=None,
                diagnostics_json=None,
                error_status="PARSER_ERROR",
                error_message=reason,
                created_at=now_iso,
            ))
        except Exception:
            logger.exception("parser_results_v2 error-save failed raw_message_id=%d", raw_message_id)


__all__ = ["ParserPipelineProcessor"]
