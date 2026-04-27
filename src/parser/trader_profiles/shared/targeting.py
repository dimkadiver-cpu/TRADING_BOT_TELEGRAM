"""Shared parser-side targeting: extract target refs from message context.

Produces TargetRefRaw objects (envelope-compatible) from reply IDs,
Telegram links, explicit message IDs, symbol refs, and global scopes.
No business logic: downstream normalizer derives final canonical TargetRef.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_LINK_RE = re.compile(
    r"(?:https?://)?t\.me/(?:c/\d+|[A-Za-z0-9_]+)/(?P<id>\d+)",
    re.IGNORECASE,
)

KNOWN_GLOBAL_SCOPES: frozenset[str] = frozenset({
    "ALL_POSITIONS",
    "ALL_OPEN",
    "ALL_REMAINING",
    "ALL_LONGS",
    "ALL_SHORTS",
})


@dataclass(frozen=True)
class TargetRefRaw:
    kind: str  # REPLY | TELEGRAM_LINK | MESSAGE_ID | EXPLICIT_ID | SYMBOL | UNKNOWN
    value: str | int | None = None


def build_reply_ref(message_id: int) -> TargetRefRaw:
    return TargetRefRaw(kind="REPLY", value=message_id)


def build_telegram_link_ref(url: str) -> TargetRefRaw:
    return TargetRefRaw(kind="TELEGRAM_LINK", value=url)


def build_explicit_id_ref(message_id: int) -> TargetRefRaw:
    return TargetRefRaw(kind="MESSAGE_ID", value=message_id)


def build_symbol_ref(symbol: str) -> TargetRefRaw:
    return TargetRefRaw(kind="SYMBOL", value=symbol)


def build_global_scope_ref(scope: str) -> TargetRefRaw:
    # Global scope uses UNKNOWN kind at parser level; normalizer maps to canonical TargetScope.
    return TargetRefRaw(kind="UNKNOWN", value=scope)


def extract_targets(
    *,
    reply_to_message_id: int | None,
    text: str,
    extracted_links: list[str],
) -> list[TargetRefRaw]:
    """Extract all target refs from message context.

    Order: reply > telegram links (with embedded message_id) > explicit IDs from text.
    Returns empty list when no targeting signal is found.
    """
    refs: list[TargetRefRaw] = []

    if reply_to_message_id is not None:
        refs.append(build_reply_ref(reply_to_message_id))

    for url in extracted_links:
        refs.append(build_telegram_link_ref(url))
        m = _LINK_RE.search(url)
        if m:
            refs.append(build_explicit_id_ref(int(m.group("id"))))

    return refs
