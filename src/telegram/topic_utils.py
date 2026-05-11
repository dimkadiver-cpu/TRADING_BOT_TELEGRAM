"""Utilities for extracting Telegram forum topic and reply references."""

from __future__ import annotations


def extract_message_topic_id(
    message: object,
    *,
    known_topic_ids: set[int] | None = None,
) -> int | None:
    """Return the forum topic ID of a Telethon message, or None if not in a topic.

    Semantics:
    - Returns None: regular group/channel, no forum topic.
    - Returns 1: General topic when Telethon exposes no top/root topic ID.
    - Returns >1: named topic from reply_to_top_id, or a configured root topic sentinel.

    Telethon may represent a topic-root message as forum_topic=True with
    reply_to_top_id=None and reply_to_msg_id equal to the topic root ID. We only
    treat that as a named topic when the caller can confirm the ID is configured.
    """
    reply_to = getattr(message, "reply_to", None)
    if reply_to is None:
        return None
    if getattr(reply_to, "forum_topic", None) is not True:
        return None

    top_id = _as_int(getattr(reply_to, "reply_to_top_id", None))
    if top_id is not None:
        return top_id

    reply_to_msg_id = _as_int(getattr(reply_to, "reply_to_msg_id", None))
    if reply_to_msg_id is not None and known_topic_ids and reply_to_msg_id in known_topic_ids:
        return reply_to_msg_id

    return 1


def extract_real_reply_to_message_id(
    message: object,
    *,
    source_topic_id: int | None,
) -> int | None:
    """Return only a real Telegram reply target, excluding topic-root sentinels."""
    reply_to = getattr(message, "reply_to", None)
    if reply_to is None:
        return None

    reply_to_msg_id = _as_int(getattr(reply_to, "reply_to_msg_id", None))
    if reply_to_msg_id is None:
        return None

    reply_to_top_id = _as_int(getattr(reply_to, "reply_to_top_id", None))
    if (
        getattr(reply_to, "forum_topic", None) is True
        and source_topic_id is not None
        and reply_to_msg_id == source_topic_id
        and reply_to_top_id is None
    ):
        return None

    return reply_to_msg_id


def _as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None
