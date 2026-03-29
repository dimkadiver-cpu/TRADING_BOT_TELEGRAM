from __future__ import annotations

import asyncio
from types import SimpleNamespace

from parser_test.scripts.import_history import _iter_target_messages


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
