"""Utilities for extracting Telegram forum topic IDs from messages."""

from __future__ import annotations


def extract_message_topic_id(message: object) -> int | None:
    """Return the forum topic ID of a Telethon message, or None if not in a topic.

    Semantics:
    - Returns None  → regular group/channel, no forum topic
    - Returns 1     → General topic (forum_topic flag set, no reply_to_top_id)
    - Returns >1    → named topic (reply_to_top_id)

    Safe against MagicMock and partial message objects: uses getattr with defaults
    and checks forum_topic via identity (is not True) to avoid truthy MagicMock traps.
    """
    reply_to = getattr(message, "reply_to", None)
    if reply_to is None:
        return None
    if getattr(reply_to, "forum_topic", None) is not True:
        return None
    top_id = getattr(reply_to, "reply_to_top_id", None)
    if isinstance(top_id, int):
        return top_id
    # forum_topic=True with no int reply_to_top_id → General topic
    return 1
