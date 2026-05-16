# tests/runtime_v2/signal_enrichment/test_integration.py
"""
Integration tests: verifica il flusso end-to-end dal CanonicalParseResult
fino alla persistenza in DB con lifecycle_processed corretto.
"""
from __future__ import annotations

import sqlite3
import pytest
import yaml
from pathlib import Path
from datetime import datetime, timezone


def _apply_migrations(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    for f in sorted(Path("db/migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


def _make_signal_result(
    *,
    trader_id: str = "trader_a",
    canonical_message_id: int = 1,
    symbol: str = "BTC/USDT",
    has_sl: bool = True,
    tp_count: int = 2,
):
    from src.parser_v2.contracts.canonical_message import CanonicalMessage, SignalPayload
    from src.parser_v2.contracts.entities import EntryLeg, Price, TakeProfit, StopLoss
    from src.parser_v2.contracts.context import RawContext
    from src.runtime_v2.parser_pipeline.models import CanonicalParseResult

    entries = [EntryLeg(sequence=1, entry_type="LIMIT", price=Price(raw="50000", value=50000.0))]
    tps = [TakeProfit(sequence=i+1, price=Price(raw=str(51000+i*500), value=51000.0+i*500)) for i in range(tp_count)]
    sl = StopLoss(price=Price(raw="49000", value=49000.0)) if has_sl else None

    signal_kwargs = dict(symbol=symbol, side="LONG", entry_structure="ONE_SHOT",
                         entries=entries, take_profits=tps, stop_loss=sl)
    try:
        signal = SignalPayload(completeness="COMPLETE", **signal_kwargs)
    except Exception:
        signal = SignalPayload(**signal_kwargs)

    canonical = CanonicalMessage(parser_profile=trader_id, primary_class="SIGNAL",
                                  parse_status="PARSED", confidence=1.0,
                                  signal=signal, raw_context=RawContext(raw_text="BUY BTC"))
    return CanonicalParseResult(
        raw_message_id=canonical_message_id * 10, canonical_message_id=canonical_message_id,
        parser_profile=trader_id, primary_class="SIGNAL", parse_status="PARSED",
        canonical_message=canonical, warnings=[], parsed_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def setup(tmp_path):
    from src.runtime_v2.signal_enrichment.config_loader import OperationConfigLoader
    from src.runtime_v2.signal_enrichment.repository import EnrichedCanonicalMessageRepository
    from src.runtime_v2.signal_enrichment.processor import SignalEnrichmentProcessor

    op_config_path = Path("config/operation_config.yaml")
    if not op_config_path.exists():
        pytest.skip("config/operation_config.yaml non trovato — esegui Task 2 prima")

    config_dir = str(op_config_path.parent)
    db_path = str(tmp_path / "test.db")
    _apply_migrations(db_path)
    loader = OperationConfigLoader(config_dir)
    repo = EnrichedCanonicalMessageRepository(db_path)
    proc = SignalEnrichmentProcessor(config_loader=loader, repository=repo)
    return proc, repo


def test_signal_pass_persisted_with_lifecycle_zero(setup):
    proc, repo = setup
    result = _make_signal_result(canonical_message_id=100)
    enriched = proc.process(result)
    assert enriched.enrichment_decision == "PASS"
    assert enriched.enrichment_id is not None
    assert enriched.lifecycle_processed is False

    from_db = repo.get_by_canonical_message_id(100)
    assert from_db is not None
    assert from_db.enrichment_decision == "PASS"
    assert from_db.lifecycle_processed is False
    assert from_db.enriched_signal is not None
    assert from_db.management_plan is not None
    assert from_db.policy_version.startswith("sha256:")


def test_signal_block_persisted_with_lifecycle_one(setup):
    proc, repo = setup
    result = _make_signal_result(canonical_message_id=101, has_sl=False)
    enriched = proc.process(result)
    assert enriched.enrichment_decision == "BLOCK"

    from_db = repo.get_by_canonical_message_id(101)
    assert from_db is not None
    assert from_db.lifecycle_processed is True
    assert from_db.enriched_signal is None
    assert from_db.management_plan is None


def test_idempotency_no_duplicate_row(setup):
    proc, repo = setup
    result = _make_signal_result(canonical_message_id=102, has_sl=False)
    e1 = proc.process(result)
    e2 = proc.process(result)
    assert e1.enrichment_id == e2.enrichment_id

    conn = sqlite3.connect(repo._db_path)
    count = conn.execute(
        "SELECT COUNT(*) FROM enriched_canonical_messages WHERE canonical_message_id = 102"
    ).fetchone()[0]
    conn.close()
    assert count == 1


def test_policy_snapshot_is_auditabile(setup):
    proc, repo = setup
    result = _make_signal_result(canonical_message_id=103)
    enriched = proc.process(result)
    from_db = repo.get_by_canonical_message_id(103)
    assert isinstance(from_db.policy_snapshot, dict)
    assert "signal_policy" in from_db.policy_snapshot


def test_parser_db_has_no_ops_tables(setup):
    _, repo = setup
    conn = sqlite3.connect(repo._db_path)
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'ops_%'"
    )]
    conn.close()
    assert tables == []
