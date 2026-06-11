from __future__ import annotations
import pytest
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from src.runtime_v2.persistence.raw_messages import RawMessageRepository, ChainNode
from src.runtime_v2.intake.models import RawIngestItem
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


def _make_item(chat_id: str = "-100123", msg_id: int = 456, mode: str = "live") -> RawIngestItem:
    return RawIngestItem(
        source_chat_id=chat_id,
        source_chat_title="Test",
        source_type="channel",
        source_topic_id=3,
        telegram_message_id=msg_id,
        reply_to_message_id=None,
        raw_text="BUY BTC",
        message_ts=_TS,
        acquisition_mode=mode,
        has_media=False,
        media_kind=None,
        media_mime_type=None,
        media_filename=None,
    )


def test_save_raw_returns_envelope(repo):
    env = repo.save_raw(_make_item())
    assert env.raw_message_id > 0
    assert env.source_chat_id == "-100123"
    assert env.acquisition_status == "ACQUIRED"
    assert env.processing_status == "pending"
    assert env.acquisition_mode == "live"


def test_save_raw_dedup_same_id(repo):
    env1 = repo.save_raw(_make_item())
    env2 = repo.save_raw(_make_item())
    assert env1.raw_message_id == env2.raw_message_id


def test_save_raw_catchup_mode(repo):
    env = repo.save_raw(_make_item(mode="catchup"))
    assert env.acquisition_mode == "catchup"


def test_set_blacklisted(repo):
    env = repo.save_raw(_make_item())
    repo.set_blacklisted(env.raw_message_id)
    updated = repo.get_by_id(env.raw_message_id)
    assert updated.acquisition_status == "BLACKLISTED"
    assert updated.processing_status == "blacklisted"


def test_set_media_only_skipped(repo):
    env = repo.save_raw(_make_item())
    repo.set_media_only_skipped(env.raw_message_id)
    updated = repo.get_by_id(env.raw_message_id)
    assert updated.acquisition_status == "MEDIA_ONLY_SKIPPED"
    assert updated.processing_status == "skipped"


def test_update_processing_status(repo):
    env = repo.save_raw(_make_item())
    repo.update_processing_status(env.raw_message_id, "review")
    updated = repo.get_by_id(env.raw_message_id)
    assert updated.processing_status == "review"


def test_update_trader_resolution(repo):
    env = repo.save_raw(_make_item())
    ctx = ResolvedTraderContext(
        raw_message_id=env.raw_message_id,
        trader_id="trader_a",
        method="source_chat_id",
        detail=None,
        is_ambiguous=False,
        resolved_at=_TS,
    )
    repo.update_trader_resolution(env.raw_message_id, ctx)
    updated = repo.get_by_id(env.raw_message_id)
    assert updated.resolved_trader_id == "trader_a"
    assert updated.resolution_method == "source_chat_id"
    assert updated.resolution_detail is None


def test_get_id_and_text_returns_none_for_unknown(repo):
    assert repo.get_id_and_text("-100123", 9999) is None


def test_get_id_and_text_returns_row(repo):
    env = repo.save_raw(_make_item(chat_id="-100123", msg_id=456))
    result = repo.get_id_and_text("-100123", 456)
    assert result is not None
    raw_message_id, raw_text = result
    assert raw_message_id == env.raw_message_id
    assert raw_text == "BUY BTC"


def test_update_raw_text(repo):
    env = repo.save_raw(_make_item(chat_id="-100123", msg_id=456))
    repo.update_raw_text(env.raw_message_id, "BUY ETH")
    updated = repo.get_by_id(env.raw_message_id)
    assert updated.raw_text == "BUY ETH"


def test_get_chain_node_returns_none_for_unknown(repo):
    result = repo.get_chain_node("-100123", 9999)
    assert result is None


def test_get_chain_node_returns_node(repo):
    item = _make_item(chat_id="-100123", msg_id=100)
    env = repo.save_raw(item)
    conn = __import__("sqlite3").connect(repo._db_path)
    conn.execute(
        "UPDATE raw_messages SET resolved_trader_id=? WHERE raw_message_id=?",
        ("trader_a", env.raw_message_id),
    )
    conn.commit()
    conn.close()
    node = repo.get_chain_node("-100123", 100)
    assert node is not None
    assert node.resolved_trader_id == "trader_a"
    assert node.source_trader_id is None
    assert node.reply_to_message_id is None


def test_get_chain_node_source_trader_id(repo):
    conn = __import__("sqlite3").connect(repo._db_path)
    conn.execute(
        "INSERT INTO raw_messages (source_chat_id, telegram_message_id, source_trader_id, "
        "raw_text, reply_to_message_id, message_ts, acquired_at, acquisition_status) "
        "VALUES (?,?,?,?,?,?,?,?)",
        ("-100123", 200, "trader_b", "some text", 150,
         "2026-01-01T00:00:00", "2026-01-01T00:00:00", "ACQUIRED"),
    )
    conn.commit()
    conn.close()
    node = repo.get_chain_node("-100123", 200)
    assert node is not None
    assert node.source_trader_id == "trader_b"
    assert node.raw_text == "some text"
    assert node.reply_to_message_id == 150
