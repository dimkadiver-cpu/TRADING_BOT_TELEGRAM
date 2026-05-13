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
