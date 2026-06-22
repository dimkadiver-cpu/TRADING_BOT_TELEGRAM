from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.runtime_v2.control_plane.topic_cleanup import TopicCleanupService


class _FloodWaitError(Exception):
    def __init__(self, seconds: int) -> None:
        super().__init__(f"Flood wait: {seconds}")
        self.seconds = seconds


def _msg(
    mid: int,
    *,
    top_id: int | None = None,
    forum_topic: bool = True,
    reply_to_msg_id: int | None = None,
) -> SimpleNamespace:
    reply_to = SimpleNamespace(
        reply_to_top_id=top_id,
        forum_topic=forum_topic,
        reply_to_msg_id=reply_to_msg_id,
    )
    return SimpleNamespace(id=mid, reply_to=reply_to)


def _aiter(messages: Iterable[object]) -> AsyncIterator[object]:
    async def _gen() -> AsyncIterator[object]:
        for message in messages:
            yield message

    return _gen()


class _Client:
    def __init__(self, *, topic_messages: dict[int, list[object]] | None = None, all_messages: list[object] | None = None) -> None:
        self._topic_messages = topic_messages or {}
        self._all_messages = all_messages or []
        self.delete_messages = AsyncMock()
        self.iter_calls: list[tuple[int, int | None]] = []

    def iter_messages(self, chat_id: int, reply_to: int | None = None) -> AsyncIterator[object]:
        self.iter_calls.append((chat_id, reply_to))
        if reply_to is None:
            return _aiter(self._all_messages)
        return _aiter(self._topic_messages.get(reply_to, []))


@pytest.mark.asyncio
async def test_clear_topic_collects_current_topic_skips_root_and_keeps_command_preview() -> None:
    client = _Client(
        topic_messages={
            10: [
                _msg(30, top_id=10),
                _msg(10, top_id=10),
                _msg(31, top_id=10),
            ]
        }
    )
    service = TopicCleanupService(client)

    started = await service.clear_topic(
        chat_id=-100999,
        topic_id=10,
        command_message_id=40,
        preview_message_id=41,
    )

    assert started is True
    client.delete_messages.assert_awaited_once_with(-100999, [30, 31, 40, 41])


@pytest.mark.asyncio
async def test_clear_topic_deletes_in_batches_of_100() -> None:
    client = _Client(topic_messages={10: [_msg(i, top_id=10) for i in range(11, 216)]})
    service = TopicCleanupService(client)

    started = await service.clear_topic(
        chat_id=-100999,
        topic_id=10,
        command_message_id=400,
        preview_message_id=401,
    )

    assert started is True
    assert client.delete_messages.await_count == 3
    batches = [call.args[1] for call in client.delete_messages.await_args_list]
    assert [len(batch) for batch in batches] == [100, 100, 7]


@pytest.mark.asyncio
async def test_clear_topic_returns_false_when_chat_lock_exists() -> None:
    client = _Client()
    service = TopicCleanupService(client)
    assert await service._acquire_chat_lock(-100999) is True

    started = await service.clear_topic(
        chat_id=-100999,
        topic_id=10,
        command_message_id=40,
        preview_message_id=41,
    )

    assert started is False
    client.delete_messages.assert_not_awaited()


@pytest.mark.asyncio
async def test_clear_topic_returns_false_when_same_topic_lock_exists() -> None:
    client = _Client()
    service = TopicCleanupService(client)
    assert await service._acquire_topic_lock(-100999, 10) is True

    started = await service.clear_topic(
        chat_id=-100999,
        topic_id=10,
        command_message_id=40,
        preview_message_id=41,
    )

    assert started is False
    client.delete_messages.assert_not_awaited()


@pytest.mark.asyncio
async def test_clear_all_topics_returns_false_when_chat_lock_exists() -> None:
    client = _Client()
    service = TopicCleanupService(client)
    assert await service._acquire_chat_lock(-100999) is True

    started = await service.clear_all_topics(
        chat_id=-100999,
        origin_topic_id=10,
        command_message_id=40,
        preview_message_id=41,
    )

    assert started is False
    client.delete_messages.assert_not_awaited()


@pytest.mark.asyncio
async def test_clear_all_topics_returns_false_when_topic_lock_exists_in_same_chat() -> None:
    client = _Client()
    service = TopicCleanupService(client)
    assert await service._acquire_topic_lock(-100999, 10) is True

    started = await service.clear_all_topics(
        chat_id=-100999,
        origin_topic_id=20,
        command_message_id=40,
        preview_message_id=41,
    )

    assert started is False
    client.delete_messages.assert_not_awaited()


@pytest.mark.asyncio
async def test_clear_all_topics_cleans_each_topic_once_and_keeps_origin_preview_messages() -> None:
    client = _Client(
        topic_messages={
            10: [_msg(10, top_id=10), _msg(11, top_id=10)],
            20: [_msg(20, top_id=20), _msg(21, top_id=20)],
        },
        all_messages=[
            _msg(101, top_id=10),
            _msg(102, top_id=20),
            _msg(103, top_id=20),
        ],
    )
    service = TopicCleanupService(client)

    started = await service.clear_all_topics(
        chat_id=-100999,
        origin_topic_id=10,
        command_message_id=40,
        preview_message_id=41,
    )

    assert started is True
    deleted_batches = [call.args[1] for call in client.delete_messages.await_args_list]
    assert deleted_batches == [[11, 40, 41], [21]]


@pytest.mark.asyncio
async def test_clear_topic_returns_false_on_permanent_delete_failure() -> None:
    client = _Client(topic_messages={10: [_msg(11, top_id=10)]})
    client.delete_messages.side_effect = RuntimeError("telegram delete failed")
    service = TopicCleanupService(client)

    started = await service.clear_topic(
        chat_id=-100999,
        topic_id=10,
        command_message_id=40,
        preview_message_id=41,
    )

    assert started is False
    assert client.delete_messages.await_count == 1


@pytest.mark.asyncio
async def test_clear_all_topics_returns_false_on_permanent_delete_failure_but_continues_best_effort() -> None:
    client = _Client(
        topic_messages={
            10: [_msg(10, top_id=10), _msg(11, top_id=10)],
            20: [_msg(20, top_id=20), _msg(21, top_id=20)],
        },
        all_messages=[
            _msg(101, top_id=10),
            _msg(102, top_id=20),
        ],
    )
    client.delete_messages.side_effect = [RuntimeError("first batch failed"), None]
    service = TopicCleanupService(client)

    started = await service.clear_all_topics(
        chat_id=-100999,
        origin_topic_id=10,
        command_message_id=40,
        preview_message_id=41,
    )

    assert started is False
    assert client.delete_messages.await_count == 2


@pytest.mark.asyncio
async def test_clear_all_topics_uses_shared_topic_extraction_semantics_for_general_topic() -> None:
    client = _Client(
        topic_messages={
            1: [_msg(1, top_id=1), _msg(12, top_id=1)],
        },
        all_messages=[
            _msg(201, top_id=None, reply_to_msg_id=999),
        ],
    )
    service = TopicCleanupService(client)

    started = await service.clear_all_topics(
        chat_id=-100999,
        origin_topic_id=1,
        command_message_id=40,
        preview_message_id=41,
    )

    assert started is True
    client.delete_messages.assert_awaited_once_with(-100999, [12, 40, 41])
    assert client.iter_calls == [(-100999, None), (-100999, 1)]


@pytest.mark.asyncio
async def test_clear_all_topics_recognizes_named_topic_root_sentinel_via_known_topic_ids() -> None:
    client = _Client(
        topic_messages={
            7: [_msg(7, top_id=7), _msg(70, top_id=7)],
        },
        all_messages=[
            _msg(301, top_id=7),
            _msg(302, top_id=None, reply_to_msg_id=7),
        ],
    )
    service = TopicCleanupService(client)

    started = await service.clear_all_topics(
        chat_id=-100999,
        origin_topic_id=7,
        command_message_id=40,
        preview_message_id=41,
    )

    assert started is True
    client.delete_messages.assert_awaited_once_with(-100999, [40, 41, 70])
    assert client.iter_calls == [(-100999, None), (-100999, 7)]


@pytest.mark.asyncio
async def test_delete_ids_retries_after_flood_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _Client()
    client.delete_messages.side_effect = [_FloodWaitError(2), None]
    service = TopicCleanupService(client)
    slept: list[int] = []

    async def _sleep(seconds: int) -> None:
        slept.append(seconds)

    monkeypatch.setattr("src.runtime_v2.control_plane.topic_cleanup.asyncio.sleep", _sleep)

    await service._delete_ids(-100999, [1, 2, 3])

    assert slept == [2]
    assert client.delete_messages.await_count == 2


@pytest.mark.asyncio
async def test_delete_ids_is_best_effort_across_batches() -> None:
    client = _Client()
    client.delete_messages.side_effect = [RuntimeError("gone"), None]
    service = TopicCleanupService(client)

    await service._delete_ids(-100999, list(range(1, 102)))

    assert client.delete_messages.await_count == 2
