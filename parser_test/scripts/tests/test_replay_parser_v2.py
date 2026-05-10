from __future__ import annotations

import csv
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from parser_test.db.schema import apply_parser_test_schema
from parser_test.scripts.replay_parser_v2 import run_replay


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
    raw_text: str = "BUY BTC/USDT",
    resolved_trader_id: str | None = None,
) -> None:
    conn.execute(
        """INSERT INTO raw_messages
        (raw_message_id, source_chat_id, telegram_message_id, source_trader_id,
         raw_text, message_ts, acquired_at, resolved_trader_id)
        VALUES (?, ?, ?, ?, ?, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', ?)""",
        (raw_message_id, source_chat_id, telegram_message_id, source_trader_id,
         raw_text, resolved_trader_id),
    )
    conn.commit()


def _make_mock_canonical(trader: str = "trader_a") -> MagicMock:
    m = MagicMock()
    m.parser_profile = trader
    m.primary_class = "SIGNAL"
    m.parse_status = "PARSED"
    m.primary_intent = None
    m.confidence = 0.9
    m.model_dump_json.return_value = "{}"
    m.warnings = []
    m.diagnostics = {}
    return m


def test_trader_filter_excludes_other_trader():
    conn = _make_db()
    _insert_raw(conn, raw_message_id=1, telegram_message_id=1, resolved_trader_id="trader_a")
    _insert_raw(conn, raw_message_id=2, telegram_message_id=2, resolved_trader_id="trader_b")
    with patch("parser_test.scripts.replay_parser_v2.get_parser_v2_profile", return_value=MagicMock()):
        with patch("parser_test.scripts.replay_parser_v2.UniversalParserRuntime") as rt:
            rt.return_value.parse.return_value = _make_mock_canonical()
            run_replay(
                conn,
                trader_filter="trader_a",
                parser_profile="trader_a",
                allow_cross_profile_parse=True,
            )
    rows = conn.execute(
        "SELECT trader_id FROM parser_results_v2 WHERE error_status='OK'"
    ).fetchall()
    assert rows == [("trader_a",)]


def test_no_trader_filter_processes_all():
    conn = _make_db()
    _insert_raw(conn, raw_message_id=1, telegram_message_id=1, resolved_trader_id="trader_a")
    _insert_raw(conn, raw_message_id=2, telegram_message_id=2, resolved_trader_id="trader_a")
    with patch("parser_test.scripts.replay_parser_v2.get_parser_v2_profile", return_value=MagicMock()):
        with patch("parser_test.scripts.replay_parser_v2.UniversalParserRuntime") as rt:
            rt.return_value.parse.return_value = _make_mock_canonical()
            run_replay(conn, parser_profile="auto")
    count = conn.execute(
        "SELECT COUNT(*) FROM parser_results_v2 WHERE error_status='OK'"
    ).fetchone()[0]
    assert count == 2


def test_unresolved_trader_not_written_to_db():
    conn = _make_db()
    _insert_raw(conn, resolved_trader_id=None)
    run_replay(conn, assume_trader=None)
    count = conn.execute("SELECT COUNT(*) FROM parser_results_v2").fetchone()[0]
    assert count == 0


def test_replay_does_not_use_assume_trader_when_resolved_is_null():
    conn = _make_db()
    _insert_raw(conn, resolved_trader_id=None)
    with patch("parser_test.scripts.replay_parser_v2.get_parser_v2_profile", return_value=MagicMock()):
        with patch("parser_test.scripts.replay_parser_v2.UniversalParserRuntime") as rt:
            rt.return_value.parse.return_value = _make_mock_canonical()
            run_replay(conn, assume_trader="trader_a", parser_profile="auto")
    count = conn.execute("SELECT COUNT(*) FROM parser_results_v2").fetchone()[0]
    assert count == 0


def test_replay_does_not_use_source_trader_id_when_resolved_is_null():
    conn = _make_db()
    _insert_raw(conn, source_trader_id="trader_a", resolved_trader_id=None)
    with patch("parser_test.scripts.replay_parser_v2.get_parser_v2_profile", return_value=MagicMock()):
        with patch("parser_test.scripts.replay_parser_v2.UniversalParserRuntime") as rt:
            rt.return_value.parse.return_value = _make_mock_canonical()
            run_replay(conn, parser_profile="auto")
    count = conn.execute("SELECT COUNT(*) FROM parser_results_v2").fetchone()[0]
    assert count == 0


def test_parser_profile_auto_uses_resolved_trader():
    conn = _make_db()
    _insert_raw(conn, resolved_trader_id="trader_a")
    captured: list[str] = []

    def capture_profile(name: str) -> MagicMock:
        captured.append(name)
        return MagicMock()

    with patch("parser_test.scripts.replay_parser_v2.get_parser_v2_profile", side_effect=capture_profile):
        with patch("parser_test.scripts.replay_parser_v2.UniversalParserRuntime") as rt:
            rt.return_value.parse.return_value = _make_mock_canonical()
            run_replay(conn, parser_profile="auto")
    assert captured == ["trader_a"]


def test_parser_profile_fixed_uses_fixed_name():
    conn = _make_db()
    _insert_raw(conn, resolved_trader_id="trader_a")
    captured: list[str] = []

    def capture_profile(name: str) -> MagicMock:
        captured.append(name)
        return MagicMock()

    with patch("parser_test.scripts.replay_parser_v2.get_parser_v2_profile", side_effect=capture_profile):
        with patch("parser_test.scripts.replay_parser_v2.UniversalParserRuntime") as rt:
            rt.return_value.parse.return_value = _make_mock_canonical()
            run_replay(conn, parser_profile="trader_a", allow_cross_profile_parse=True)
    assert captured == ["trader_a"]


def test_unsupported_parser_profile_not_written_to_db():
    conn = _make_db()
    _insert_raw(conn, resolved_trader_id="trader_unknown_xyz")
    run_replay(conn, parser_profile="auto")
    count = conn.execute("SELECT COUNT(*) FROM parser_results_v2").fetchone()[0]
    assert count == 0


def test_cross_profile_blocked_by_default():
    conn = _make_db()
    _insert_raw(conn, resolved_trader_id="trader_b_unknown")
    with patch("parser_test.scripts.replay_parser_v2.get_parser_v2_profile", return_value=MagicMock()):
        run_replay(conn, parser_profile="trader_a", allow_cross_profile_parse=False)
    count = conn.execute(
        "SELECT COUNT(*) FROM parser_results_v2 WHERE error_status='OK'"
    ).fetchone()[0]
    assert count == 0


def test_cross_profile_allowed_with_flag():
    conn = _make_db()
    _insert_raw(conn, resolved_trader_id="trader_b_unknown")
    with patch("parser_test.scripts.replay_parser_v2.get_parser_v2_profile", return_value=MagicMock()):
        with patch("parser_test.scripts.replay_parser_v2.UniversalParserRuntime") as rt:
            rt.return_value.parse.return_value = _make_mock_canonical()
            run_replay(conn, parser_profile="trader_a", allow_cross_profile_parse=True)
    count = conn.execute(
        "SELECT COUNT(*) FROM parser_results_v2 WHERE error_status='OK'"
    ).fetchone()[0]
    assert count == 1


def test_resolve_trader_filter_from_args_deprecated_warning(capsys):
    import argparse
    from parser_test.scripts.replay_parser_v2 import _resolve_trader_filter_from_args
    args = argparse.Namespace(trader="trader_a", trader_filter=None)
    result = _resolve_trader_filter_from_args(args)
    assert result == "trader_a"
    captured = capsys.readouterr()
    assert "--trader is deprecated" in captured.err


def test_resolve_trader_filter_from_args_trader_filter_takes_precedence(capsys):
    import argparse
    from parser_test.scripts.replay_parser_v2 import _resolve_trader_filter_from_args
    args = argparse.Namespace(trader="trader_a", trader_filter="trader_b")
    result = _resolve_trader_filter_from_args(args)
    assert result == "trader_b"


def test_resolve_trader_filter_from_args_no_trader_no_warning(capsys):
    import argparse
    from parser_test.scripts.replay_parser_v2 import _resolve_trader_filter_from_args
    args = argparse.Namespace(trader=None, trader_filter="trader_a")
    result = _resolve_trader_filter_from_args(args)
    assert result == "trader_a"
    captured = capsys.readouterr()
    assert "deprecated" not in captured.err


def test_audit_csv_written_when_dir_provided(tmp_path: Path):
    conn = _make_db()
    _insert_raw(conn, resolved_trader_id=None)
    run_replay(conn, audit_csv_dir=tmp_path)
    csv_files = list(tmp_path.glob("audit_run_*.csv"))
    assert len(csv_files) == 1
    content = csv_files[0].read_text(encoding="utf-8-sig")
    assert "UNRESOLVED_TRADER" in content


def test_audit_csv_not_written_when_dir_none():
    conn = _make_db()
    _insert_raw(conn, resolved_trader_id=None)
    run_replay(conn, audit_csv_dir=None)


def test_audit_csv_contains_expected_columns(tmp_path: Path):
    conn = _make_db()
    _insert_raw(conn, resolved_trader_id=None)
    run_replay(conn, audit_csv_dir=tmp_path)
    csv_file = list(tmp_path.glob("audit_run_*.csv"))[0]
    with csv_file.open(encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        columns = reader.fieldnames or []
    expected = {
        "raw_message_id", "source_trader_id", "resolved_trader_id", "parser_profile",
        "error_status", "error_message", "source_chat_id", "source_topic_id",
        "telegram_message_id", "message_ts", "text_preview",
    }
    assert expected.issubset(set(columns))
