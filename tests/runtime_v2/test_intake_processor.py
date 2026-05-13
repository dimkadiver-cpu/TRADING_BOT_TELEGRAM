from __future__ import annotations
import pytest
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.runtime_v2.intake.models import IntakeConfig, RawIngestItem
from src.runtime_v2.intake.processor import RuntimeV2IntakeProcessor
from src.runtime_v2.persistence.raw_messages import RawMessageRepository
from src.runtime_v2.trader_resolution.channel_config_resolver import (
    ChannelConfigResolver,
    ChannelEntry,
)
from src.runtime_v2.trader_resolution.models import ResolvedTraderContext

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


@pytest.fixture
def repo(db_path):
    return RawMessageRepository(db_path=db_path)


def _make_item(
    text: str = "BUY BTC 45000 SL 44000 TP 47000",
    chat_id: str = "-100123",
    msg_id: int = 1,
    has_media: bool = False,
    reply_id: int | None = None,
) -> RawIngestItem:
    return RawIngestItem(
        source_chat_id=chat_id,
        source_chat_title="Test",
        source_type="channel",
        source_topic_id=None,
        telegram_message_id=msg_id,
        reply_to_message_id=reply_id,
        raw_text=text,
        message_ts=_TS,
        acquisition_mode="live",
        has_media=has_media,
        media_kind=None,
        media_mime_type=None,
        media_filename=None,
    )


def _make_resolved(trader_id: str | None, method: str, is_ambiguous: bool = False) -> ResolvedTraderContext:
    return ResolvedTraderContext(
        raw_message_id=0,
        trader_id=trader_id,
        method=method,
        detail=None,
        is_ambiguous=is_ambiguous,
        resolved_at=_TS,
    )


def _build_processor(repo, trader_id="trader_a", profiles=("trader_a",), globally_blacklisted=False):
    """Builds a RuntimeV2IntakeProcessor with mocked dependencies.
    Does NOT patch list_parser_v2_profiles — individual tests do that."""
    channel_config = MagicMock(spec=ChannelConfigResolver)
    channel_config.is_globally_blacklisted.return_value = globally_blacklisted
    channel_config.lookup.return_value = ChannelEntry(
        chat_id="-100123",
        topic_id=None,
        label="Test",
        active=True,
        trader_id=trader_id,
        parser_profile=trader_id,
        blacklist=[],
    )
    resolver = MagicMock()
    resolved = _make_resolved(trader_id, "source_chat_id")
    resolver.resolve.return_value = resolved

    eligibility = MagicMock()
    eligibility.check.return_value = MagicMock(eligible=True, review_reason=None)

    return RuntimeV2IntakeProcessor(
        repo=repo,
        eligibility=eligibility,
        resolver=resolver,
        channel_config=channel_config,
        config=IntakeConfig(),
    ), channel_config, resolver, eligibility


@patch("src.runtime_v2.intake.processor.list_parser_v2_profiles", return_value=["trader_a"])
def test_happy_path_returns_candidate(mock_profiles, repo):
    processor, _, _, _ = _build_processor(repo)
    candidate = processor.process(_make_item())
    assert candidate is not None
    assert candidate.parser_profile == "trader_a"
    assert candidate.resolved_trader.trader_id == "trader_a"
    env = repo.get_by_id(candidate.raw_message.raw_message_id)
    assert env.processing_status == "done"
    assert env.acquisition_status == "ACQUIRED"


@patch("src.runtime_v2.intake.processor.list_parser_v2_profiles", return_value=["trader_a"])
def test_dedup_same_message_id(mock_profiles, repo):
    processor, _, _, _ = _build_processor(repo)
    c1 = processor.process(_make_item(msg_id=1))
    c2 = processor.process(_make_item(msg_id=1))
    assert c1.raw_message.raw_message_id == c2.raw_message.raw_message_id


def test_globally_blacklisted_returns_none(repo):
    processor, _, _, _ = _build_processor(repo, globally_blacklisted=True)
    candidate = processor.process(_make_item(text="#admin"))
    assert candidate is None
    conn = sqlite3.connect(repo._db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT acquisition_status, processing_status FROM raw_messages LIMIT 1").fetchone()
    conn.close()
    assert row["acquisition_status"] == "BLACKLISTED"
    assert row["processing_status"] == "blacklisted"


def test_media_only_no_text_returns_none(repo):
    processor, _, _, _ = _build_processor(repo)
    item = _make_item(text=None, has_media=True)
    candidate = processor.process(item)
    assert candidate is None
    conn = sqlite3.connect(repo._db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT acquisition_status, processing_status FROM raw_messages LIMIT 1").fetchone()
    conn.close()
    assert row["acquisition_status"] == "MEDIA_ONLY_SKIPPED"


def test_eligibility_review_returns_none(repo):
    processor, channel_config, resolver, eligibility = _build_processor(repo)
    eligibility.check.return_value = MagicMock(
        eligible=False, review_reason="short_update_without_strong_link"
    )
    candidate = processor.process(_make_item(text="ok"))
    assert candidate is None
    env = repo.get_by_id(1)
    assert env.processing_status == "review"
    assert env.acquisition_status == "ACQUIRED"


@patch("src.runtime_v2.intake.processor.list_parser_v2_profiles", return_value=["trader_a"])
def test_unresolved_trader_returns_none(mock_profiles, repo):
    processor, channel_config, resolver, eligibility = _build_processor(repo)
    resolver.resolve.return_value = _make_resolved(None, "unresolved")
    candidate = processor.process(_make_item())
    assert candidate is None
    env = repo.get_by_id(1)
    assert env.processing_status == "review"


@patch("src.runtime_v2.intake.processor.list_parser_v2_profiles", return_value=["trader_b"])
def test_no_parser_profile_returns_none(mock_profiles, repo):
    processor, _, _, _ = _build_processor(repo, trader_id="trader_a")
    candidate = processor.process(_make_item())
    assert candidate is None
    env = repo.get_by_id(1)
    assert env.processing_status == "review"


def test_acquisition_status_immutable_after_blacklist(repo):
    processor, _, _, _ = _build_processor(repo, globally_blacklisted=True)
    processor.process(_make_item(text="#admin"))
    env = repo.get_by_id(1)
    assert env.acquisition_status == "BLACKLISTED"
    repo.update_processing_status(env.raw_message_id, "pending")
    assert repo.get_by_id(env.raw_message_id).acquisition_status == "BLACKLISTED"
