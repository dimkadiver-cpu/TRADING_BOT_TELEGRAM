from __future__ import annotations

import sqlite3
import pytest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from src.parser_v2.contracts.context import ParserContext, RawContext
from src.runtime_v2.intake.models import RawMessageEnvelope
from src.runtime_v2.trader_resolution.models import ResolvedTraderContext, ParserDispatchCandidate
from src.runtime_v2.parser_pipeline.models import CanonicalParseResult, ParserJobStatus


_TS = datetime(2026, 5, 13, 12, 0, 0, tzinfo=timezone.utc)

_TRADER_A_SIGNAL = (
    "[trader#A]\n"
    "BTCUSDT Лонг\n"
    "Вход: 65000\n"
    "SL: 62000\n"
    "TP1: 70000\n"
)

_TRADER_A_INFO = "#admin Технические работы"


def _apply_migrations(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    for f in sorted(Path("db/migrations").glob("*.sql")):
        conn.executescript(f.read_text())
    conn.commit()
    conn.close()


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "test.db")
    _apply_migrations(path)
    return path


def _make_envelope(raw_message_id: int = 1, text: str | None = _TRADER_A_SIGNAL) -> RawMessageEnvelope:
    return RawMessageEnvelope(
        raw_message_id=raw_message_id,
        source_chat_id="-100123",
        source_chat_title="Test Channel",
        source_type="channel",
        source_topic_id=None,
        telegram_message_id=raw_message_id,
        reply_to_message_id=None,
        raw_text=text,
        message_ts=_TS,
        acquired_at=_TS,
        acquisition_mode="live",
        acquisition_status="ACQUIRED",
        processing_status="processing",
        source_trader_id=None,
        resolved_trader_id="trader_a",
        resolution_method="source_chat_id",
        resolution_detail=None,
        has_media=False,
        media_kind=None,
        media_mime_type=None,
        media_filename=None,
    )


def _make_candidate(raw_message_id: int = 1, text: str | None = _TRADER_A_SIGNAL) -> ParserDispatchCandidate:
    envelope = _make_envelope(raw_message_id, text)
    resolved = ResolvedTraderContext(
        raw_message_id=raw_message_id,
        trader_id="trader_a",
        method="source_chat_id",
        detail=None,
        is_ambiguous=False,
        resolved_at=_TS,
    )
    context = ParserContext(
        raw_context=RawContext(raw_text=text or ""),
        message_id=raw_message_id,
        reply_to_message_id=None,
        source_chat_id="-100123",
        source_topic_id=None,
    )
    return ParserDispatchCandidate(
        raw_message=envelope,
        resolved_trader=resolved,
        parser_profile="trader_a",
        parser_context=context,
    )


def test_process_signal_returns_canonical_parse_result(db_path):
    from src.runtime_v2.parser_pipeline.processor import ParserPipelineProcessor
    from src.runtime_v2.persistence.canonical_messages import CanonicalMessageRepository

    repo = CanonicalMessageRepository(db_path)
    processor = ParserPipelineProcessor(canonical_repo=repo)
    candidate = _make_candidate()

    result = processor.process(candidate)

    assert isinstance(result, CanonicalParseResult)
    assert result.raw_message_id == 1
    assert result.primary_class == "SIGNAL"
    assert result.parse_status == "PARSED"
    assert result.canonical_message_id > 0


def test_process_info_message_returns_canonical_parse_result(db_path):
    from src.runtime_v2.parser_pipeline.processor import ParserPipelineProcessor
    from src.runtime_v2.persistence.canonical_messages import CanonicalMessageRepository

    repo = CanonicalMessageRepository(db_path)
    processor = ParserPipelineProcessor(canonical_repo=repo)
    candidate = _make_candidate(raw_message_id=2, text=_TRADER_A_INFO)

    result = processor.process(candidate)

    assert isinstance(result, CanonicalParseResult)
    assert result.primary_class == "INFO"


def test_process_empty_text_returns_canonical_not_failed(db_path):
    from src.runtime_v2.parser_pipeline.processor import ParserPipelineProcessor
    from src.runtime_v2.persistence.canonical_messages import CanonicalMessageRepository

    repo = CanonicalMessageRepository(db_path)
    processor = ParserPipelineProcessor(canonical_repo=repo)
    candidate = _make_candidate(raw_message_id=3, text="")
    # Set raw_text to None via model_copy
    candidate = candidate.model_copy(
        update={"raw_message": candidate.raw_message.model_copy(update={"raw_text": None})}
    )

    result = processor.process(candidate)

    assert isinstance(result, CanonicalParseResult)
    assert result.parse_status in {"UNCLASSIFIED", "PARTIAL", "PARSED"}


def test_process_idempotent_same_raw_message(db_path):
    from src.runtime_v2.parser_pipeline.processor import ParserPipelineProcessor
    from src.runtime_v2.persistence.canonical_messages import CanonicalMessageRepository

    repo = CanonicalMessageRepository(db_path)
    processor = ParserPipelineProcessor(canonical_repo=repo)
    candidate = _make_candidate()

    result1 = processor.process(candidate)
    result2 = processor.process(candidate)

    assert isinstance(result1, CanonicalParseResult)
    assert isinstance(result2, CanonicalParseResult)
    assert result1.canonical_message_id == result2.canonical_message_id


def test_process_unknown_profile_returns_job_status_failed(db_path):
    from src.runtime_v2.parser_pipeline.processor import ParserPipelineProcessor
    from src.runtime_v2.persistence.canonical_messages import CanonicalMessageRepository

    repo = CanonicalMessageRepository(db_path)
    processor = ParserPipelineProcessor(canonical_repo=repo)
    candidate = _make_candidate()
    candidate = candidate.model_copy(update={"parser_profile": "trader_unknown"})

    result = processor.process(candidate)

    assert isinstance(result, ParserJobStatus)
    assert result.status == "failed"
    assert result.reason == "unknown_parser_profile"


def test_process_does_not_import_router() -> None:
    import importlib
    import sys
    importlib.import_module("src.runtime_v2.parser_pipeline.processor")
    assert "src.telegram.router" not in sys.modules
