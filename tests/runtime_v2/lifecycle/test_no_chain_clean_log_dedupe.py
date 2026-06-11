"""Dedupe per contenuto delle notifiche SIGNAL_REJECTED senza chain.

Le revisioni editate di uno stesso messaggio producono enrichment_id distinti:
se simbolo/side/ragione non cambiano, la notifica Telegram non deve ripetersi.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from src.runtime_v2.lifecycle.entry_gate import _write_no_chain_signal_clean_log
from src.runtime_v2.lifecycle.models import LifecycleEvent
from src.runtime_v2.signal_enrichment.models import (
    EnrichedCanonicalMessage,
    EnrichedSignalPayload,
)


def _apply_ops_migrations(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    for f in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


@pytest.fixture
def ops_conn(tmp_path):
    db_path = str(tmp_path / "ops.sqlite3")
    _apply_ops_migrations(db_path)
    conn = sqlite3.connect(db_path)
    yield conn
    conn.close()


def _enriched(enrichment_id: int, *, raw_message_id: int = 85, symbol: str = "BTCUSTD") -> EnrichedCanonicalMessage:
    return EnrichedCanonicalMessage(
        enrichment_id=enrichment_id,
        canonical_message_id=enrichment_id * 10,
        raw_message_id=raw_message_id,
        trader_id="trader_a",
        account_id="main",
        primary_class="SIGNAL",
        enrichment_decision="PASS",
        enriched_signal=EnrichedSignalPayload(
            symbol=symbol,
            side="LONG",
            entry_structure="ONE_SHOT",
            entries=[],
            take_profits=[],
            stop_loss=None,
        ),
    )


def _rejected_event(eid: int, reason: str = "unknown_symbol") -> LifecycleEvent:
    return LifecycleEvent(
        event_type="SIGNAL_REJECTED",
        source_type="enrichment",
        source_id=str(eid),
        payload_json=json.dumps({"reason": reason, "source": "trader_signal"}),
        idempotency_key=f"signal_rejected:{eid}",
    )


def _count_rows(conn) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM ops_notification_outbox WHERE notification_type='SIGNAL_REJECTED'"
    ).fetchone()[0]


def test_same_rejection_across_revisions_notifies_once(ops_conn):
    for eid in (1, 2, 3):
        _write_no_chain_signal_clean_log(
            ops_conn, _enriched(eid), [_rejected_event(eid)],
            src_chat_id="-100123", tg_msg_id=7306,
        )
    ops_conn.commit()
    assert _count_rows(ops_conn) == 1


def test_corrected_symbol_notifies_again(ops_conn):
    _write_no_chain_signal_clean_log(
        ops_conn, _enriched(1, symbol="BTCUSTD"), [_rejected_event(1)],
        src_chat_id="-100123", tg_msg_id=7306,
    )
    _write_no_chain_signal_clean_log(
        ops_conn, _enriched(2, symbol="BTCUSDT"), [_rejected_event(2)],
        src_chat_id="-100123", tg_msg_id=7306,
    )
    ops_conn.commit()
    assert _count_rows(ops_conn) == 2


def test_different_reason_notifies_again(ops_conn):
    _write_no_chain_signal_clean_log(
        ops_conn, _enriched(1), [_rejected_event(1, "unknown_symbol")],
        src_chat_id="-100123", tg_msg_id=7306,
    )
    _write_no_chain_signal_clean_log(
        ops_conn, _enriched(2), [_rejected_event(2, "missing_stop_loss_for_risk_calc")],
        src_chat_id="-100123", tg_msg_id=7306,
    )
    ops_conn.commit()
    assert _count_rows(ops_conn) == 2


def test_different_message_notifies_again(ops_conn):
    _write_no_chain_signal_clean_log(
        ops_conn, _enriched(1, raw_message_id=85), [_rejected_event(1)],
        src_chat_id="-100123", tg_msg_id=7306,
    )
    _write_no_chain_signal_clean_log(
        ops_conn, _enriched(2, raw_message_id=86), [_rejected_event(2)],
        src_chat_id="-100123", tg_msg_id=7307,
    )
    ops_conn.commit()
    assert _count_rows(ops_conn) == 2
