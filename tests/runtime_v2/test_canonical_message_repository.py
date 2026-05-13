from __future__ import annotations

import sqlite3
import pytest
from pathlib import Path

from src.parser_v2.contracts.context import RawContext
from src.parser_v2.contracts.canonical_message import CanonicalMessage, InfoPayload


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


def _make_info_canonical(profile: str = "trader_a") -> CanonicalMessage:
    return CanonicalMessage(
        parser_profile=profile,
        primary_class="INFO",
        parse_status="UNCLASSIFIED",
        confidence=1.0,
        info=InfoPayload(raw_fragment=None),
        raw_context=RawContext(raw_text="test message"),
    )


def test_save_returns_canonical_message_id(db_path):
    from src.runtime_v2.persistence.canonical_messages import CanonicalMessageRepository

    repo = CanonicalMessageRepository(db_path)
    canonical = _make_info_canonical()
    cid = repo.save(raw_message_id=1, canonical=canonical)
    assert isinstance(cid, int)
    assert cid > 0


def test_save_idempotent_same_raw_and_context(db_path):
    from src.runtime_v2.persistence.canonical_messages import CanonicalMessageRepository

    repo = CanonicalMessageRepository(db_path)
    canonical = _make_info_canonical()
    id1 = repo.save(raw_message_id=1, canonical=canonical)
    id2 = repo.save(raw_message_id=1, canonical=canonical)
    assert id1 == id2


def test_save_different_run_contexts_produce_different_rows(db_path):
    from src.runtime_v2.persistence.canonical_messages import CanonicalMessageRepository

    repo = CanonicalMessageRepository(db_path)
    canonical = _make_info_canonical()
    id1 = repo.save(raw_message_id=1, canonical=canonical, run_context="live")
    id2 = repo.save(raw_message_id=1, canonical=canonical, run_context="reparse_20260513")
    assert id1 != id2


def test_get_by_raw_message_id_returns_canonical(db_path):
    from src.runtime_v2.persistence.canonical_messages import CanonicalMessageRepository

    repo = CanonicalMessageRepository(db_path)
    canonical = _make_info_canonical()
    repo.save(raw_message_id=42, canonical=canonical)
    retrieved = repo.get_by_raw_message_id(raw_message_id=42)
    assert retrieved is not None
    assert retrieved.primary_class == "INFO"
    assert retrieved.parser_profile == "trader_a"


def test_get_by_raw_message_id_missing_returns_none(db_path):
    from src.runtime_v2.persistence.canonical_messages import CanonicalMessageRepository

    repo = CanonicalMessageRepository(db_path)
    result = repo.get_by_raw_message_id(raw_message_id=999)
    assert result is None
