from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import yaml

from parser_test.db.schema import apply_parser_test_schema
from parser_test.scripts.resolve_traders import resolve_all


def _create_test_db(tmp_path: Path) -> tuple[str, sqlite3.Connection]:
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    apply_parser_test_schema(conn)
    return db_path, conn


def _insert_raw(
    conn: sqlite3.Connection,
    chat_id: str,
    msg_id: int,
    text: str,
    source_trader_id: str | None = None,
) -> None:
    conn.execute(
        """INSERT INTO raw_messages
           (source_chat_id, telegram_message_id, raw_text, message_ts, acquired_at, source_trader_id)
           VALUES (?, ?, ?, '2026-01-01', '2026-01-01', ?)""",
        (chat_id, msg_id, text, source_trader_id),
    )
    conn.commit()


def _get_resolution(conn: sqlite3.Connection, msg_id: int) -> tuple[str | None, str | None]:
    row = conn.execute(
        "SELECT resolved_trader_id, resolution_method FROM raw_messages WHERE telegram_message_id=?",
        (msg_id,),
    ).fetchone()
    return row[0], row[1]


def _make_channels_yaml(tmp_path: Path, trader_id: str, chat_id: str) -> str:
    data = {
        "blacklist_global": [],
        "channels": [
            {
                "chat_id": int(chat_id),
                "active": True,
                "trader_id": trader_id,
                "blacklist": [],
            }
        ],
    }
    p = tmp_path / "channels.yaml"
    p.write_text(yaml.dump(data))
    return str(p)


def test_source_trader_id_takes_priority(tmp_path: Path) -> None:
    _db_path, conn = _create_test_db(tmp_path)
    _insert_raw(conn, "-100123", 1, "BUY BTC", source_trader_id="trader_x")
    channels_yaml = _make_channels_yaml(tmp_path, "trader_from_config", "-100123")
    resolve_all(conn, channels_yaml=channels_yaml)
    tid, method = _get_resolution(conn, 1)
    assert tid == "trader_x"
    assert method == "source_trader_id"


def test_config_driven_resolves_from_channels_yaml(tmp_path: Path) -> None:
    _db_path, conn = _create_test_db(tmp_path)
    _insert_raw(conn, "-100555", 2, "BUY ETH")
    channels_yaml = _make_channels_yaml(tmp_path, "trader_y", "-100555")
    resolve_all(conn, channels_yaml=channels_yaml)
    tid, method = _get_resolution(conn, 2)
    assert tid == "trader_y"
    assert method in ("source_topic_config", "source_chat_id")


def test_assume_trader_fallback(tmp_path: Path) -> None:
    _db_path, conn = _create_test_db(tmp_path)
    _insert_raw(conn, "-100999", 3, "BUY XRP")
    resolve_all(conn, channels_yaml=None, assume_trader="trader_fallback")
    tid, method = _get_resolution(conn, 3)
    assert tid == "trader_fallback"
    assert method == "assume_trader"


def test_config_driven_with_topic_id(tmp_path: Path) -> None:
    _db_path, conn = _create_test_db(tmp_path)
    # Insert raw message with source_topic_id=5
    conn.execute(
        """INSERT INTO raw_messages
           (source_chat_id, source_topic_id, telegram_message_id, raw_text, message_ts, acquired_at, acquisition_status)
           VALUES (?, ?, ?, ?, '2026-01-01', '2026-01-01', 'ACQUIRED')""",
        ("-100777", 5, 10, "BUY SOL"),
    )
    conn.commit()

    # channels.yaml with topic_id=5
    data = {
        "blacklist_global": [],
        "channels": [{"chat_id": -100777, "topic_id": 5, "active": True, "trader_id": "trader_topic", "blacklist": []}],
    }
    p = tmp_path / "channels_topic.yaml"
    p.write_text(yaml.dump(data))

    resolve_all(conn, channels_yaml=str(p))
    tid, method = _get_resolution(conn, 10)
    assert tid == "trader_topic"
    assert method == "source_topic_config"
