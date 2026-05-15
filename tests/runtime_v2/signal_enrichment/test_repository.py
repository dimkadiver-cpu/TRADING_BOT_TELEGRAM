# tests/runtime_v2/signal_enrichment/test_repository.py
from __future__ import annotations

import sqlite3
import pytest
from pathlib import Path


def _apply_migrations(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    for f in sorted(Path("db/migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "test.db")
    _apply_migrations(path)
    return path


def _make_block_msg(canonical_message_id: int = 1) -> object:
    from src.runtime_v2.signal_enrichment.models import EnrichedCanonicalMessage
    return EnrichedCanonicalMessage(
        canonical_message_id=canonical_message_id,
        raw_message_id=10,
        trader_id="trader_a",
        account_id="main",
        primary_class="SIGNAL",
        enrichment_decision="BLOCK",
        reason_code="missing_stop_loss",
        policy_version="sha256:abc",
        lifecycle_processed=True,
    )


def test_save_returns_enrichment_id(db_path):
    from src.runtime_v2.signal_enrichment.repository import EnrichedCanonicalMessageRepository
    repo = EnrichedCanonicalMessageRepository(db_path)
    saved = repo.save(_make_block_msg())
    assert saved.enrichment_id is not None
    assert saved.enrichment_id > 0


def test_get_by_canonical_message_id_returns_saved(db_path):
    from src.runtime_v2.signal_enrichment.repository import EnrichedCanonicalMessageRepository
    repo = EnrichedCanonicalMessageRepository(db_path)
    repo.save(_make_block_msg(canonical_message_id=42))
    retrieved = repo.get_by_canonical_message_id(42)
    assert retrieved is not None
    assert retrieved.trader_id == "trader_a"
    assert retrieved.enrichment_decision == "BLOCK"
    assert retrieved.reason_code == "missing_stop_loss"
    assert retrieved.lifecycle_processed is True


def test_get_by_canonical_message_id_missing_returns_none(db_path):
    from src.runtime_v2.signal_enrichment.repository import EnrichedCanonicalMessageRepository
    repo = EnrichedCanonicalMessageRepository(db_path)
    assert repo.get_by_canonical_message_id(999) is None


def test_save_idempotent_unique_constraint(db_path):
    from src.runtime_v2.signal_enrichment.repository import EnrichedCanonicalMessageRepository
    repo = EnrichedCanonicalMessageRepository(db_path)
    saved1 = repo.save(_make_block_msg(canonical_message_id=5))
    saved2 = repo.save(_make_block_msg(canonical_message_id=5))
    assert saved1.enrichment_id == saved2.enrichment_id


def test_save_pass_with_enrichment_log(db_path):
    from src.runtime_v2.signal_enrichment.repository import EnrichedCanonicalMessageRepository
    from src.runtime_v2.signal_enrichment.models import EnrichedCanonicalMessage, EnrichmentLogEntry
    repo = EnrichedCanonicalMessageRepository(db_path)
    msg = EnrichedCanonicalMessage(
        canonical_message_id=7,
        raw_message_id=70,
        trader_id="trader_a",
        account_id="main",
        primary_class="SIGNAL",
        enrichment_decision="PASS",
        policy_version="sha256:abc",
        lifecycle_processed=False,
        enrichment_log=[
            EnrichmentLogEntry(check="tp_count_trimmed", original="5", result="2"),
        ],
    )
    saved = repo.save(msg)
    retrieved = repo.get_by_canonical_message_id(7)
    assert retrieved is not None
    assert len(retrieved.enrichment_log) == 1
    assert retrieved.enrichment_log[0].check == "tp_count_trimmed"
    assert retrieved.enrichment_log[0].original == "5"
    assert retrieved.lifecycle_processed is False
