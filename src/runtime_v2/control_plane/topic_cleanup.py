from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable

from src.telegram.topic_utils import extract_message_topic_id


def _chunks(values: list[int], size: int) -> Iterable[list[int]]:
    for index in range(0, len(values), size):
        yield values[index:index + size]


class TopicCleanupService:
    def __init__(self, telethon_client) -> None:
        self._client = telethon_client
        self._topic_locks: set[tuple[int, int]] = set()
        self._chat_locks: set[int] = set()
        self._lock = asyncio.Lock()

    async def _acquire_chat_lock(self, chat_id: int) -> bool:
        async with self._lock:
            if chat_id in self._chat_locks:
                return False
            if any(locked_chat_id == chat_id for locked_chat_id, _ in self._topic_locks):
                return False
            self._chat_locks.add(chat_id)
            return True

    async def _acquire_topic_lock(self, chat_id: int, topic_id: int) -> bool:
        async with self._lock:
            key = (chat_id, topic_id)
            if chat_id in self._chat_locks or key in self._topic_locks:
                return False
            self._topic_locks.add(key)
            return True

    async def _release_chat_lock(self, chat_id: int) -> None:
        async with self._lock:
            self._chat_locks.discard(chat_id)

    async def _release_topic_lock(self, chat_id: int, topic_id: int) -> None:
        async with self._lock:
            self._topic_locks.discard((chat_id, topic_id))

    async def _iter_messages(
        self,
        chat_id: int,
        *,
        reply_to: int | None = None,
    ) -> AsyncIterator[object]:
        async for message in self._client.iter_messages(chat_id, reply_to=reply_to):
            yield message

    async def _collect_topic_message_ids(
        self,
        *,
        chat_id: int,
        topic_id: int,
        command_message_id: int | None,
        preview_message_id: int | None,
    ) -> list[int]:
        ids: set[int] = set()
        if command_message_id and command_message_id > 0:
            ids.add(command_message_id)
        if preview_message_id and preview_message_id > 0:
            ids.add(preview_message_id)

        async for message in self._iter_messages(chat_id, reply_to=topic_id):
            message_id = getattr(message, "id", None)
            if not isinstance(message_id, int) or isinstance(message_id, bool):
                continue
            if message_id == topic_id:
                continue
            ids.add(message_id)
        return sorted(ids)

    async def _delete_batch(self, chat_id: int, batch: list[int]) -> bool:
        while True:
            try:
                await self._client.delete_messages(chat_id, batch)
                return True
            except Exception as exc:
                seconds = getattr(exc, "seconds", None)
                if isinstance(seconds, int) and seconds > 0:
                    await asyncio.sleep(seconds)
                    continue
                return False

    async def _delete_ids(self, chat_id: int, message_ids: list[int]) -> bool:
        success = True
        for batch in _chunks(message_ids, 100):
            if batch:
                success = await self._delete_batch(chat_id, batch) and success
        return success

    async def _clear_topic_unlocked(
        self,
        *,
        chat_id: int,
        topic_id: int,
        command_message_id: int,
        preview_message_id: int,
    ) -> bool:
        ids = await self._collect_topic_message_ids(
            chat_id=chat_id,
            topic_id=topic_id,
            command_message_id=command_message_id,
            preview_message_id=preview_message_id,
        )
        return await self._delete_ids(chat_id, ids)

    async def _iter_forum_topic_ids(
        self,
        chat_id: int,
        *,
        known_topic_ids: set[int] | None = None,
    ) -> list[int]:
        topic_ids: set[int] = set(known_topic_ids or ())
        async for message in self._iter_messages(chat_id):
            topic_id = extract_message_topic_id(message, known_topic_ids=topic_ids or None)
            if topic_id is not None:
                topic_ids.add(topic_id)
        return sorted(topic_ids)

    async def clear_topic(
        self,
        *,
        chat_id: int,
        topic_id: int,
        command_message_id: int,
        preview_message_id: int,
    ) -> bool:
        if not await self._acquire_topic_lock(chat_id, topic_id):
            return False
        try:
            return await self._clear_topic_unlocked(
                chat_id=chat_id,
                topic_id=topic_id,
                command_message_id=command_message_id,
                preview_message_id=preview_message_id,
            )
        finally:
            await self._release_topic_lock(chat_id, topic_id)

    async def _clear_all_topics_unlocked(
        self,
        *,
        chat_id: int,
        origin_topic_id: int,
        command_message_id: int,
        preview_message_id: int,
    ) -> bool:
        topic_ids = set(
            await self._iter_forum_topic_ids(
                chat_id,
                known_topic_ids={origin_topic_id},
            )
        )
        success = True
        for topic_id in sorted(topic_ids):
            ids = await self._collect_topic_message_ids(
                chat_id=chat_id,
                topic_id=topic_id,
                command_message_id=command_message_id if topic_id == origin_topic_id else None,
                preview_message_id=preview_message_id if topic_id == origin_topic_id else None,
            )
            success = await self._delete_ids(chat_id, ids) and success
        return success

    async def clear_all_topics(
        self,
        *,
        chat_id: int,
        origin_topic_id: int,
        command_message_id: int,
        preview_message_id: int,
    ) -> bool:
        if not await self._acquire_chat_lock(chat_id):
            return False
        try:
            return await self._clear_all_topics_unlocked(
                chat_id=chat_id,
                origin_topic_id=origin_topic_id,
                command_message_id=command_message_id,
                preview_message_id=preview_message_id,
            )
        finally:
            await self._release_chat_lock(chat_id)

    async def try_clear_topic(
        self,
        *,
        chat_id: int,
        topic_id: int,
        command_message_id: int,
        preview_message_id: int,
    ) -> bool:
        return await self.clear_topic(
            chat_id=chat_id,
            topic_id=topic_id,
            command_message_id=command_message_id,
            preview_message_id=preview_message_id,
        )

    async def try_clear_all_topics(
        self,
        *,
        chat_id: int,
        origin_topic_id: int,
        command_message_id: int,
        preview_message_id: int,
    ) -> bool:
        return await self.clear_all_topics(
            chat_id=chat_id,
            origin_topic_id=origin_topic_id,
            command_message_id=command_message_id,
            preview_message_id=preview_message_id,
        )
