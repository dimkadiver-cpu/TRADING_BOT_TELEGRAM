from __future__ import annotations
"""Verifica i criteri di accettazione PRD-01 §11.2."""

import sqlite3
import pytest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.runtime_v2.intake.models import IntakeConfig, RawIngestItem
from src.runtime_v2.intake.processor import RuntimeV2IntakeProcessor
from src.runtime_v2.intake.eligibility import IntakeEligibilityCheck
from src.runtime_v2.persistence.raw_messages import RawMessageRepository
from src.runtime_v2.trader_resolution.channel_config_resolver import (
    ChannelConfigResolver,
    ChannelEntry,
)
from src.runtime_v2.trader_resolution.resolver import RuntimeV2TraderResolver
from src.runtime_v2.trader_resolution.models import ResolvedTraderContext, ParserDispatchCandidate

_TS = datetime(2026, 5, 13, 12, 0, 0, tzinfo=timezone.utc)


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


def _make_item(
    chat_id: str = "-100123",
    msg_id: int = 1,
    text: str | None = "BUY BTC SL 44000 TP 47000",
    has_media: bool = False,
    reply_id: int | None = None,
    topic_id: int | None = None,
    mode: str = "live",
) -> RawIngestItem:
    return RawIngestItem(
        source_chat_id=chat_id,
        source_chat_title="Test",
        source_type="channel",
        source_topic_id=topic_id,
        telegram_message_id=msg_id,
        reply_to_message_id=reply_id,
        raw_text=text,
        message_ts=_TS,
        acquisition_mode=mode,
        has_media=has_media,
        media_kind=None,
        media_mime_type=None,
        media_filename=None,
    )


def _make_resolved(
    trader_id: str | None,
    method: str,
    is_ambiguous: bool = False,
) -> ResolvedTraderContext:
    """Helper to create ResolvedTraderContext with consistent defaults."""
    return ResolvedTraderContext(
        raw_message_id=0,
        trader_id=trader_id,
        method=method,
        detail=None,
        is_ambiguous=is_ambiguous,
        resolved_at=_TS,
    )


def _build(
    db_path: str,
    *,
    trader_id: str | None = "trader_a",
    topic_id: int | None = None,
    active: bool = True,
    globally_blacklisted: bool = False,
    eligible: bool = True,
    review_reason: str | None = None,
    resolved_trader_id: str | None = "trader_a",
    resolved_method: str = "source_chat_id",
    is_ambiguous: bool = False,
):
    """Builds RuntimeV2IntakeProcessor with mocked dependencies.
    Does NOT patch list_parser_v2_profiles — each test that needs it applies @patch."""
    repo = RawMessageRepository(db_path=db_path)

    channel_config = MagicMock(spec=ChannelConfigResolver)
    channel_config.is_globally_blacklisted.return_value = globally_blacklisted
    channel_config.lookup.return_value = (
        ChannelEntry(
            chat_id="-100123",
            topic_id=topic_id,
            label="Test",
            active=active,
            trader_id=trader_id,
            parser_profile=trader_id,
            blacklist=[],
        )
        if trader_id
        else None
    )

    resolver = MagicMock(spec=RuntimeV2TraderResolver)
    resolver.resolve.return_value = _make_resolved(
        resolved_trader_id, resolved_method, is_ambiguous
    )

    _review_reason = review_reason if review_reason is not None else (
        "short_update_without_strong_link" if not eligible else None
    )

    eligibility = MagicMock(spec=IntakeEligibilityCheck)
    eligibility.check.return_value = MagicMock(
        eligible=eligible,
        review_reason=_review_reason,
    )

    processor = RuntimeV2IntakeProcessor(
        repo=repo,
        eligibility=eligibility,
        resolver=resolver,
        channel_config=channel_config,
        config=IntakeConfig(),
    )
    return processor, repo


# Criterion 1: dedup — same (source_chat_id, telegram_message_id) → same raw_message_id
@patch("src.runtime_v2.intake.processor.list_parser_v2_profiles", return_value=["trader_a"])
def test_criterion_1_dedup(mock_profiles, db_path):
    p, repo = _build(db_path)
    c1 = p.process(_make_item(msg_id=1))
    c2 = p.process(_make_item(msg_id=1))
    assert c1.raw_message.raw_message_id == c2.raw_message.raw_message_id


# Criterion 2: source_topic_id preserved in RawMessageEnvelope
@patch("src.runtime_v2.intake.processor.list_parser_v2_profiles", return_value=["trader_a"])
def test_criterion_2_topic_preserved(mock_profiles, db_path):
    p, repo = _build(db_path, topic_id=3)
    c = p.process(_make_item(topic_id=3))
    assert c is not None
    assert c.raw_message.source_topic_id == 3


# Criterion 3: config-driven mono-trader resolution via channels.yaml
@patch("src.runtime_v2.intake.processor.list_parser_v2_profiles", return_value=["trader_a"])
def test_criterion_3_mono_trader_from_config(mock_profiles, db_path):
    p, repo = _build(db_path, trader_id="trader_a", resolved_method="source_chat_id")
    c = p.process(_make_item())
    assert c is not None
    assert c.resolved_trader.method == "source_chat_id"
    assert c.resolved_trader.trader_id == "trader_a"


# Criterion 6: short update without strong link → processing_status=review, acquisition_status=ACQUIRED
def test_criterion_6_short_update_review(db_path):
    p, repo = _build(
        db_path,
        eligible=False,
        review_reason="short_update_without_strong_link",
    )
    result = p.process(_make_item(text="ok"))
    assert result is None
    env = repo.get_by_id(1)
    assert env.processing_status == "review"
    assert env.acquisition_status == "ACQUIRED"


# Criterion 8: ambiguous alias → review
def test_criterion_8_ambiguous_alias_review(db_path):
    p, repo = _build(
        db_path,
        trader_id=None,
        resolved_trader_id=None,
        is_ambiguous=True,
        resolved_method="content_alias_ambiguous",
    )
    with patch("src.runtime_v2.intake.processor.list_parser_v2_profiles", return_value=["trader_a"]):
        result = p.process(_make_item())
    assert result is None
    env = repo.get_by_id(1)
    assert env.processing_status == "review"


# Criterion 10: no import of src.telegram.router in any runtime_v2 module
def test_criterion_10_no_router_import():
    import src.runtime_v2.intake.processor as mod
    import src.runtime_v2.trader_resolution.resolver as res_mod
    import src.runtime_v2.persistence.raw_messages as pers_mod

    for m in (mod, res_mod, pers_mod):
        content = Path(m.__file__).read_text()
        assert "src.telegram.router" not in content, (
            f"{m.__file__} imports src.telegram.router"
        )


# Criterion 11: successful process() returns ParserDispatchCandidate with parser_context
@patch("src.runtime_v2.intake.processor.list_parser_v2_profiles", return_value=["trader_a"])
def test_criterion_11_result_type(mock_profiles, db_path):
    p, _ = _build(db_path)
    c = p.process(_make_item())
    assert isinstance(c, ParserDispatchCandidate)
    assert c.parser_context is not None


# Criterion 12: no_parser_profile → review
@patch("src.runtime_v2.intake.processor.list_parser_v2_profiles", return_value=["trader_b"])
def test_criterion_12_no_parser_profile_review(mock_profiles, db_path):
    p, repo = _build(db_path, resolved_trader_id="trader_a")
    result = p.process(_make_item())
    assert result is None
    env = repo.get_by_id(1)
    assert env.processing_status == "review"


# Criterion 13: acquisition_status immutable — cannot be changed after initial set
def test_criterion_13_acquisition_status_immutable(db_path):
    p, repo = _build(db_path, globally_blacklisted=True)
    p.process(_make_item(text="#admin"))
    env = repo.get_by_id(1)
    assert env.acquisition_status == "BLACKLISTED"
    # Changing processing_status must not affect acquisition_status
    repo.update_processing_status(env.raw_message_id, "review")
    assert repo.get_by_id(env.raw_message_id).acquisition_status == "BLACKLISTED"


# ---------------------------------------------------------------------------
# PRD 2.b — Acceptance: slice end-to-end PRD 01 → parser_pipeline
# ---------------------------------------------------------------------------

_SIGNAL_TEXT_PRD2B = (
    "[trader#A]\n"
    "BTCUSDT Лонг\n"
    "Вход: 65000\n"
    "SL: 62000\n"
    "TP1: 70000\n"
)
_INFO_TEXT_PRD2B = "#admin Технические работы на сервере"
_PARTIAL_TEXT_PRD2B = "BTCUSDT Лонг\nВход: 65000"  # missing SL/TP → PARTIAL or UNCLASSIFIED


def _run_intake_prd2b(
    db_path: str,
    text: str,
    msg_id: int,
    trader_id: str = "trader_a",
) -> "ParserDispatchCandidate":
    """Run PRD 01 intake to produce a ParserDispatchCandidate."""
    repo = RawMessageRepository(db_path=db_path)

    channel_entry = ChannelEntry(
        chat_id="-100123",
        topic_id=3,
        label="Test",
        active=True,
        trader_id=trader_id,
        parser_profile=trader_id,
        blacklist=[],
    )
    channel_config = MagicMock(spec=ChannelConfigResolver)
    channel_config.is_globally_blacklisted.return_value = False
    channel_config.lookup.return_value = channel_entry

    resolved_ctx = ResolvedTraderContext(
        raw_message_id=0,
        trader_id=trader_id,
        method="source_chat_id",
        detail=None,
        is_ambiguous=False,
        resolved_at=_TS,
    )
    resolver = MagicMock(spec=RuntimeV2TraderResolver)
    resolver.resolve.return_value = resolved_ctx

    eligibility = MagicMock(spec=IntakeEligibilityCheck)
    eligibility.check.return_value = MagicMock(eligible=True, review_reason=None)

    processor = RuntimeV2IntakeProcessor(
        repo=repo,
        eligibility=eligibility,
        resolver=resolver,
        channel_config=channel_config,
        config=IntakeConfig(),
    )

    item = _make_item(chat_id="-100123", msg_id=msg_id, text=text, topic_id=3)

    with patch("src.runtime_v2.intake.processor.list_parser_v2_profiles", return_value=[trader_id]):
        result = processor.process(item)

    assert result is not None, "Intake produced no ParserDispatchCandidate"
    return result


def test_prd2b_signal_persisted_in_canonical_messages(db_path):
    from src.runtime_v2.parser_pipeline.processor import ParserPipelineProcessor
    from src.runtime_v2.persistence.canonical_messages import CanonicalMessageRepository
    from src.runtime_v2.parser_pipeline.models import CanonicalParseResult

    candidate = _run_intake_prd2b(db_path, _SIGNAL_TEXT_PRD2B, msg_id=100)
    repo = CanonicalMessageRepository(db_path)
    processor = ParserPipelineProcessor(canonical_repo=repo)
    result = processor.process(candidate)

    assert isinstance(result, CanonicalParseResult)
    assert result.primary_class == "SIGNAL"
    assert result.parse_status == "PARSED"
    stored = CanonicalMessageRepository(db_path).get_by_raw_message_id(
        candidate.raw_message.raw_message_id
    )
    assert stored is not None
    assert stored.primary_class == "SIGNAL"


def test_prd2b_info_message_persisted_schema_valid(db_path):
    from src.runtime_v2.parser_pipeline.processor import ParserPipelineProcessor
    from src.runtime_v2.persistence.canonical_messages import CanonicalMessageRepository
    from src.runtime_v2.parser_pipeline.models import CanonicalParseResult

    candidate = _run_intake_prd2b(db_path, _INFO_TEXT_PRD2B, msg_id=101)
    repo = CanonicalMessageRepository(db_path)
    processor = ParserPipelineProcessor(canonical_repo=repo)
    result = processor.process(candidate)

    assert isinstance(result, CanonicalParseResult)
    assert result.primary_class == "INFO"
    assert CanonicalMessageRepository(db_path).get_by_raw_message_id(
        candidate.raw_message.raw_message_id
    ) is not None


def test_prd2b_partial_message_persisted_not_failed(db_path):
    from src.runtime_v2.parser_pipeline.processor import ParserPipelineProcessor
    from src.runtime_v2.persistence.canonical_messages import CanonicalMessageRepository
    from src.runtime_v2.parser_pipeline.models import CanonicalParseResult

    candidate = _run_intake_prd2b(db_path, _PARTIAL_TEXT_PRD2B, msg_id=102)
    repo = CanonicalMessageRepository(db_path)
    processor = ParserPipelineProcessor(canonical_repo=repo)
    result = processor.process(candidate)

    assert isinstance(result, CanonicalParseResult)
    assert result.parse_status in {"PARTIAL", "UNCLASSIFIED", "PARSED"}
    assert CanonicalMessageRepository(db_path).get_by_raw_message_id(
        candidate.raw_message.raw_message_id
    ) is not None


def test_prd2b_idempotent_second_process_same_canonical_id(db_path):
    from src.runtime_v2.parser_pipeline.processor import ParserPipelineProcessor
    from src.runtime_v2.persistence.canonical_messages import CanonicalMessageRepository
    from src.runtime_v2.parser_pipeline.models import CanonicalParseResult

    candidate = _run_intake_prd2b(db_path, _SIGNAL_TEXT_PRD2B, msg_id=103)
    repo = CanonicalMessageRepository(db_path)
    processor = ParserPipelineProcessor(canonical_repo=repo)
    result1 = processor.process(candidate)
    result2 = processor.process(candidate)

    assert isinstance(result1, CanonicalParseResult)
    assert isinstance(result2, CanonicalParseResult)
    assert result1.canonical_message_id == result2.canonical_message_id


def test_prd2b_no_router_import_in_parser_pipeline():
    import importlib
    import sys

    importlib.import_module("src.runtime_v2.parser_pipeline.processor")
    assert "src.telegram.router" not in sys.modules
