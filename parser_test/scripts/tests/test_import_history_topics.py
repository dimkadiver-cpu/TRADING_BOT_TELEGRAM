from __future__ import annotations

import asyncio
import sqlite3
from types import SimpleNamespace

from parser_test.db.schema import apply_parser_test_schema
from parser_test.scripts.import_history import (
    _extract_import_reply_and_topic,
    _format_reply_metadata_debug_line,
    _iter_target_messages,
    _persist_import_trader_resolution,
    _resolve_import_trader_fields,
)


class _FakeClient:
    def __init__(self, replies: list[object], root: object | None) -> None:
        self._replies = replies
        self._root = root

    async def get_messages(self, entity: object, ids: int):
        return self._root if getattr(self._root, "id", None) == ids else None

    async def iter_messages(self, entity: object, limit=None, reverse=False, reply_to=None):
        yielded = 0
        for item in self._replies:
            if limit is not None and yielded >= limit:
                break
            yielded += 1
            yield item


def test_iter_target_messages_for_topic_yields_root_then_replies_when_reverse() -> None:
    root = SimpleNamespace(id=175)
    replies = [SimpleNamespace(id=176), SimpleNamespace(id=177)]
    client = _FakeClient(replies=replies, root=root)

    async def _collect():
        return [msg.id async for msg in _iter_target_messages(client=client, entity=object(), limit=None, reverse=True, topic_id=175)]

    assert asyncio.run(_collect()) == [175, 176, 177]


def test_iter_target_messages_for_topic_appends_root_when_not_reverse() -> None:
    root = SimpleNamespace(id=175)
    replies = [SimpleNamespace(id=176), SimpleNamespace(id=177)]
    client = _FakeClient(replies=replies, root=root)

    async def _collect():
        return [msg.id async for msg in _iter_target_messages(client=client, entity=object(), limit=None, reverse=False, topic_id=175)]

    assert asyncio.run(_collect()) == [176, 177, 175]


def test_iter_target_messages_topic_respects_limit_with_root() -> None:
    root = SimpleNamespace(id=175)
    replies = [SimpleNamespace(id=176), SimpleNamespace(id=177)]
    client = _FakeClient(replies=replies, root=root)

    async def _collect():
        return [msg.id async for msg in _iter_target_messages(client=client, entity=object(), limit=1, reverse=True, topic_id=175)]

    assert asyncio.run(_collect()) == [175]


def test_parse_args_default_source_trader_present():
    import sys
    from unittest.mock import patch
    with patch.object(sys, "argv", ["prog", "--chat-id", "-123", "--default-source-trader", "trader_a"]):
        from parser_test.scripts.import_history import parse_args
        args = parse_args()
    assert args.default_source_trader == "trader_a"


def test_parse_args_default_source_trader_absent():
    import sys
    from unittest.mock import patch
    with patch.object(sys, "argv", ["prog", "--chat-id", "-123"]):
        from parser_test.scripts.import_history import parse_args
        args = parse_args()
    assert args.default_source_trader is None


def test_parse_args_debug_reply_metadata_flags() -> None:
    import sys
    from unittest.mock import patch
    with patch.object(
        sys,
        "argv",
        ["prog", "--chat-id", "-123", "--debug-reply-metadata", "--debug-reply-metadata-limit", "7"],
    ):
        from parser_test.scripts.import_history import parse_args
        args = parse_args()
    assert args.debug_reply_metadata is True
    assert args.debug_reply_metadata_limit == 7


def test_format_reply_metadata_debug_line_includes_reply_fields() -> None:
    reply_to = SimpleNamespace(
        forum_topic=True,
        reply_to_msg_id=300,
        reply_to_top_id=200,
    )
    message = SimpleNamespace(
        id=999,
        message="hello\nworld",
        reply_to=reply_to,
    )

    line = _format_reply_metadata_debug_line(message=message, selected_topic_id=200)

    assert "msg_id=999" in line
    assert "selected_topic_id=200" in line
    assert "forum_topic=True" in line
    assert "reply_to_msg_id=300" in line
    assert "reply_to_top_id=200" in line
    assert "hello world" in line


def test_extract_import_reply_and_topic_for_topic_root_message() -> None:
    reply_to = SimpleNamespace(
        forum_topic=True,
        reply_to_msg_id=3,
        reply_to_top_id=None,
    )
    message = SimpleNamespace(reply_to=reply_to)

    reply_to_message_id, source_topic_id = _extract_import_reply_and_topic(
        message=message,
        selected_topic_id=3,
    )

    assert reply_to_message_id is None
    assert source_topic_id == 3


def test_extract_import_reply_and_topic_for_real_reply_inside_topic() -> None:
    reply_to = SimpleNamespace(
        forum_topic=True,
        reply_to_msg_id=4894,
        reply_to_top_id=3,
    )
    message = SimpleNamespace(reply_to=reply_to)

    reply_to_message_id, source_topic_id = _extract_import_reply_and_topic(
        message=message,
        selected_topic_id=3,
    )

    assert reply_to_message_id == 4894
    assert source_topic_id == 3


def test_extract_import_reply_and_topic_without_selected_topic_preserves_legacy_behavior() -> None:
    reply_to = SimpleNamespace(
        forum_topic=True,
        reply_to_msg_id=3,
        reply_to_top_id=None,
    )
    message = SimpleNamespace(reply_to=reply_to)

    reply_to_message_id, source_topic_id = _extract_import_reply_and_topic(
        message=message,
        selected_topic_id=None,
    )

    assert reply_to_message_id == 3
    assert source_topic_id is None


def test_resolve_import_trader_fields_for_mono_trader() -> None:
    source_trader_id, resolved_trader_id, resolution_method = _resolve_import_trader_fields("trader_a")
    assert source_trader_id == "trader_a"
    assert resolved_trader_id == "trader_a"
    assert resolution_method == "source_trader_id"


def test_resolve_import_trader_fields_without_default_trader() -> None:
    source_trader_id, resolved_trader_id, resolution_method = _resolve_import_trader_fields(None)
    assert source_trader_id is None
    assert resolved_trader_id is None
    assert resolution_method is None


def test_persist_import_trader_resolution_backfills_source_and_resolved() -> None:
    conn = sqlite3.connect(":memory:")
    apply_parser_test_schema(conn)
    conn.execute(
        """INSERT INTO raw_messages
        (raw_message_id, source_chat_id, telegram_message_id, raw_text, message_ts, acquired_at)
        VALUES (1, 'chat1', 100, 'hello', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"""
    )
    conn.commit()

    _persist_import_trader_resolution(conn, raw_message_id=1, source_trader_id="trader_a")

    row = conn.execute(
        "SELECT source_trader_id, resolved_trader_id, resolution_method FROM raw_messages WHERE raw_message_id = 1"
    ).fetchone()
    assert row == ("trader_a", "trader_a", "source_trader_id")


def test_persist_import_trader_resolution_preserves_existing_resolved_trader() -> None:
    conn = sqlite3.connect(":memory:")
    apply_parser_test_schema(conn)
    conn.execute(
        """INSERT INTO raw_messages
        (raw_message_id, source_chat_id, telegram_message_id, source_trader_id, raw_text, message_ts, acquired_at, resolved_trader_id, resolution_method)
        VALUES (1, 'chat1', 100, NULL, 'hello', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', 'trader_b', 'content_alias')"""
    )
    conn.commit()

    _persist_import_trader_resolution(conn, raw_message_id=1, source_trader_id="trader_a")

    row = conn.execute(
        "SELECT source_trader_id, resolved_trader_id, resolution_method FROM raw_messages WHERE raw_message_id = 1"
    ).fetchone()
    assert row == ("trader_a", "trader_b", "content_alias")
