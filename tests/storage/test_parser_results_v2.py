from __future__ import annotations

import sqlite3

from parser_test.db.schema import apply_parser_test_schema
from src.storage.parser_results_v2 import ParserResultV2Record, ParserResultV2Store
from src.storage.parser_runs import ParserRunStore


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    apply_parser_test_schema(conn)
    return conn


def _insert_raw(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        "INSERT INTO raw_messages (source_chat_id, telegram_message_id, message_ts, acquired_at) "
        "VALUES ('chat1', 42, '2026-05-01T10:00:00', '2026-05-01T10:00:00')"
    )
    conn.commit()
    return cur.lastrowid


def _make_record(run_id: int, raw_message_id: int, **kwargs) -> ParserResultV2Record:
    defaults = dict(
        run_id=run_id,
        raw_message_id=raw_message_id,
        trader_id="trader_a",
        parser_profile="trader_a",
        primary_class="SIGNAL",
        parse_status="PARSED",
        primary_intent="NEW_SIGNAL",
        confidence=0.9,
        canonical_json='{"primary_class":"SIGNAL"}',
        warnings_json=None,
        diagnostics_json=None,
        error_status="OK",
        error_message=None,
        created_at="2026-05-01T10:00:00",
    )
    defaults.update(kwargs)
    return ParserResultV2Record(**defaults)


def test_insert_and_fetch_ok_result():
    conn = _conn()
    raw_id = _insert_raw(conn)
    run_id = ParserRunStore(conn).create_run()
    store = ParserResultV2Store(conn)
    store.insert_result(_make_record(run_id, raw_id))
    results = store.fetch_by_run(run_id)
    assert len(results) == 1
    assert results[0].primary_class == "SIGNAL"
    assert results[0].error_status == "OK"


def test_insert_error_result():
    conn = _conn()
    raw_id = _insert_raw(conn)
    run_id = ParserRunStore(conn).create_run()
    store = ParserResultV2Store(conn)
    store.insert_result(_make_record(
        run_id, raw_id,
        canonical_json=None,
        error_status="PARSER_ERROR",
        error_message="boom",
    ))
    results = store.fetch_by_run(run_id)
    assert results[0].error_status == "PARSER_ERROR"
    assert results[0].error_message == "boom"


def test_fetch_by_run_filters_by_trader():
    conn = _conn()
    raw_id = _insert_raw(conn)
    run_id = ParserRunStore(conn).create_run()
    store = ParserResultV2Store(conn)
    store.insert_result(_make_record(run_id, raw_id, trader_id="trader_a"))
    results_a = store.fetch_by_run(run_id, trader="trader_a")
    results_b = store.fetch_by_run(run_id, trader="trader_b")
    assert len(results_a) == 1
    assert len(results_b) == 0


def test_fetch_latest_run_results():
    conn = _conn()
    raw_id = _insert_raw(conn)
    run_store = ParserRunStore(conn)
    run1 = run_store.create_run()
    store = ParserResultV2Store(conn)
    store.insert_result(_make_record(run1, raw_id))
    results = store.fetch_latest_run_results()
    assert len(results) == 1
    assert results[0].run_id == run1


def test_insert_upserts_on_conflict():
    conn = _conn()
    raw_id = _insert_raw(conn)
    run_id = ParserRunStore(conn).create_run()
    store = ParserResultV2Store(conn)
    store.insert_result(_make_record(run_id, raw_id, parse_status="PARSED"))
    store.insert_result(_make_record(run_id, raw_id, parse_status="PARTIAL"))
    results = store.fetch_by_run(run_id)
    assert len(results) == 1
    assert results[0].parse_status == "PARTIAL"
