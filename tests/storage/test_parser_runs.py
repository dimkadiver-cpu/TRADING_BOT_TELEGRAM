from __future__ import annotations

import sqlite3

from parser_test.db.schema import apply_parser_test_schema
from src.storage.parser_runs import ParserRunRecord, ParserRunStore


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    apply_parser_test_schema(conn)
    return conn


def test_create_run_returns_positive_int():
    store = ParserRunStore(_conn())
    run_id = store.create_run()
    assert isinstance(run_id, int)
    assert run_id >= 1


def test_create_run_stores_parser_system():
    conn = _conn()
    store = ParserRunStore(conn)
    run_id = store.create_run(parser_system="parser_v2")
    row = conn.execute("SELECT parser_system FROM parser_runs WHERE run_id=?", (run_id,)).fetchone()
    assert row[0] == "parser_v2"


def test_complete_run_sets_completed_at():
    conn = _conn()
    store = ParserRunStore(conn)
    run_id = store.create_run()
    store.complete_run(run_id)
    row = conn.execute("SELECT completed_at FROM parser_runs WHERE run_id=?", (run_id,)).fetchone()
    assert row[0] is not None


def test_get_latest_run_returns_most_recent_completed():
    conn = _conn()
    store = ParserRunStore(conn)
    run_id1 = store.create_run(trader_filter="trader_a")
    store.complete_run(run_id1)
    run_id2 = store.create_run(trader_filter="trader_a")
    store.complete_run(run_id2)
    latest = store.get_latest_run(trader_filter="trader_a")
    assert latest is not None
    assert latest.run_id == run_id2


def test_get_latest_run_returns_none_when_no_completed():
    conn = _conn()
    store = ParserRunStore(conn)
    store.create_run()  # not completed
    result = store.get_latest_run()
    assert result is None


def test_get_latest_run_filters_by_trader():
    conn = _conn()
    store = ParserRunStore(conn)
    run_a = store.create_run(trader_filter="trader_a")
    store.complete_run(run_a)
    run_b = store.create_run(trader_filter="trader_b")
    store.complete_run(run_b)
    latest_a = store.get_latest_run(trader_filter="trader_a")
    assert latest_a is not None
    assert latest_a.run_id == run_a


def test_get_latest_run_returns_parser_run_record():
    conn = _conn()
    store = ParserRunStore(conn)
    run_id = store.create_run(trader_filter="trader_a", force_reparse=True)
    store.complete_run(run_id)
    record = store.get_latest_run(trader_filter="trader_a")
    assert isinstance(record, ParserRunRecord)
    assert record.force_reparse is True
    assert record.trader_filter == "trader_a"
