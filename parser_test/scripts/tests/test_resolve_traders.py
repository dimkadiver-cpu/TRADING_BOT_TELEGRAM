from __future__ import annotations

import sqlite3
from collections import Counter
from unittest.mock import MagicMock

import pytest

from parser_test.db.schema import apply_parser_test_schema
from parser_test.scripts.resolve_traders import resolve_all
from src.telegram.effective_trader import EffectiveTraderResult


def _make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    apply_parser_test_schema(conn)
    return conn


def _insert_raw(
    conn: sqlite3.Connection,
    *,
    raw_message_id: int = 1,
    source_chat_id: str = "chat1",
    telegram_message_id: int = 100,
    source_trader_id: str | None = None,
    raw_text: str | None = "hello",
    reply_to_message_id: int | None = None,
    resolved_trader_id: str | None = None,
) -> None:
    conn.execute(
        """INSERT INTO raw_messages
        (raw_message_id, source_chat_id, telegram_message_id, source_trader_id,
         raw_text, reply_to_message_id, message_ts, acquired_at, resolved_trader_id)
        VALUES (?, ?, ?, ?, ?, ?, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', ?)""",
        (raw_message_id, source_chat_id, telegram_message_id, source_trader_id,
         raw_text, reply_to_message_id, resolved_trader_id),
    )
    conn.commit()


def _get_resolved(conn: sqlite3.Connection, raw_message_id: int = 1) -> tuple[str | None, str | None]:
    row = conn.execute(
        "SELECT resolved_trader_id, resolution_method FROM raw_messages WHERE raw_message_id=?",
        (raw_message_id,),
    ).fetchone()
    return row[0], row[1]


def _mock_resolver(trader_id: str | None, method: str = "source_chat_id") -> MagicMock:
    r = MagicMock()
    r.resolve.return_value = EffectiveTraderResult(trader_id=trader_id, method=method)
    return r


def test_priority1_source_trader_id_used_directly():
    conn = _make_db()
    _insert_raw(conn, source_trader_id="trader_a")
    mock = _mock_resolver("trader_b")
    resolve_all(conn, resolver=mock)
    resolved, method = _get_resolved(conn)
    assert resolved == "trader_a"
    assert method == "source_trader_id"
    mock.resolve.assert_not_called()


def test_priority2_live_resolver_used_when_no_source_trader():
    conn = _make_db()
    _insert_raw(conn)
    mock = _mock_resolver("trader_a", method="content_alias")
    resolve_all(conn, resolver=mock)
    resolved, method = _get_resolved(conn)
    assert resolved == "trader_a"
    assert method == "content_alias"


def test_priority3_assume_trader_fallback():
    conn = _make_db()
    _insert_raw(conn)
    mock = _mock_resolver(None, method="unresolved")
    resolve_all(conn, resolver=mock, assume_trader="trader_a")
    resolved, method = _get_resolved(conn)
    assert resolved == "trader_a"
    assert method == "assume_trader"


def test_priority4_unresolved_when_nothing_works():
    conn = _make_db()
    _insert_raw(conn)
    mock = _mock_resolver(None)
    resolve_all(conn, resolver=mock)
    resolved, method = _get_resolved(conn)
    assert resolved is None
    assert method == "unresolved"


def test_already_resolved_skipped_by_default():
    conn = _make_db()
    _insert_raw(conn, resolved_trader_id="trader_a")
    mock = _mock_resolver("trader_b")
    counts = resolve_all(conn, resolver=mock, force_re_resolve=False)
    mock.resolve.assert_not_called()
    assert counts["skipped_already_resolved"] == 1
    resolved, _ = _get_resolved(conn)
    assert resolved == "trader_a"


def test_force_re_resolve_overwrites_existing():
    conn = _make_db()
    _insert_raw(conn, resolved_trader_id="trader_a", source_trader_id="trader_b")
    mock = _mock_resolver("ignored")
    counts = resolve_all(conn, resolver=mock, force_re_resolve=True)
    resolved, method = _get_resolved(conn)
    assert resolved == "trader_b"
    assert method == "source_trader_id"


def test_counts_returned_correctly():
    conn = _make_db()
    _insert_raw(conn, raw_message_id=1, source_trader_id="trader_a", telegram_message_id=1)
    _insert_raw(conn, raw_message_id=2, telegram_message_id=2)
    _insert_raw(conn, raw_message_id=3, telegram_message_id=3, resolved_trader_id="trader_a")
    mock = _mock_resolver(None)
    counts = resolve_all(conn, resolver=mock, assume_trader="trader_a")
    assert counts["source_trader_id"] == 1
    assert counts["assume_trader"] == 1
    assert counts["skipped_already_resolved"] == 1


def test_source_trader_id_alias_normalized():
    conn = _make_db()
    _insert_raw(conn, source_trader_id="ta")
    mock = _mock_resolver(None)
    resolve_all(conn, resolver=mock)
    resolved, _ = _get_resolved(conn)
    assert resolved == "trader_a"
