from __future__ import annotations

import re
from collections.abc import Iterable
from typing import TypeVar, cast

_T = TypeVar("_T")

from src.parser_v2.contracts.context import ParserContext, TargetHints
from src.parser_v2.contracts.enums import ScopeHint
from src.parser_v2.contracts.markers import NormalizedText
from src.parser_v2.contracts.rules import MarkerSet, SemanticMarkers


TELEGRAM_LINK_RE = re.compile(
    r"\b(?:https?://)?t\.me/(?:c/\d+|[a-zA-Z0-9_]+)/\d+\b",
    re.IGNORECASE,
)
EXPLICIT_ID_PATTERNS = (
    re.compile(r"\bsignal\s+id\s*:?\s*([a-z0-9_-]+)", re.IGNORECASE),
    re.compile(r"\bсигнал\s+id\s*:?\s*([a-z0-9_-]+)", re.IGNORECASE),
    re.compile(r"\bid\s+сигнала\s*:?\s*([a-z0-9_-]+)", re.IGNORECASE),
)
TOKEN_RE = re.compile(r"#?[a-z0-9][a-z0-9._-]*", re.IGNORECASE)
TRAILING_LINK_CHARS = ".,;:!?)]}\"'"
SCOPE_HINTS: set[str] = {
    "SINGLE_SIGNAL",
    "SYMBOL",
    "ALL_LONG",
    "ALL_SHORT",
    "ALL_POSITIONS",
    "ALL_OPEN",
    "ALL_REMAINING",
}


class TargetHintsExtractor:
    def extract(
        self,
        normalized: NormalizedText,
        context: ParserContext,
        markers: SemanticMarkers,
    ) -> TargetHints:
        links = _dedup(_extract_telegram_links(normalized.raw_text))

        return TargetHints(
            reply_to_message_id=_reply_to_message_id(context),
            telegram_links=links,
            telegram_message_ids=_dedup(_message_ids_from_links(links)),
            explicit_ids=_dedup(_extract_explicit_ids(normalized.normalized_text)),
            symbols=_dedup(_extract_symbols(normalized.normalized_text, markers)),
            scope_hint=_extract_scope_hint(normalized.normalized_text, markers),
        )


def _reply_to_message_id(context: ParserContext) -> int | None:
    if context.reply_to_message_id is not None:
        return context.reply_to_message_id
    if context.raw_context is not None:
        return context.raw_context.reply_to_message_id
    return None


def _extract_telegram_links(text: str) -> Iterable[str]:
    for match in TELEGRAM_LINK_RE.finditer(text):
        yield match.group(0).rstrip(TRAILING_LINK_CHARS)


def _message_ids_from_links(links: Iterable[str]) -> Iterable[int]:
    for link in links:
        tail = link.rstrip("/").rsplit("/", 1)[-1]
        if tail.isdigit():
            yield int(tail)


def _extract_explicit_ids(text: str) -> Iterable[str]:
    for pattern in EXPLICIT_ID_PATTERNS:
        for match in pattern.finditer(text):
            yield match.group(1)


def _extract_symbols(text: str, markers: SemanticMarkers) -> Iterable[str]:
    symbol_markers = _target_marker_set(markers, "symbol")
    if symbol_markers is None:
        symbol_markers = _target_marker_set(markers, "SYMBOL")
    if symbol_markers is None:
        return []

    marker_values = [marker.lower() for marker in _marker_values(symbol_markers) if marker]
    if not marker_values:
        return []

    symbols: list[str] = []
    for match in TOKEN_RE.finditer(text):
        token = match.group(0).lstrip("#").strip(".,;:!?()[]{}")
        if not token:
            continue
        if any(marker in token and len(token) > len(marker) for marker in marker_values):
            symbols.append(token.upper())
    return symbols


def _extract_scope_hint(text: str, markers: SemanticMarkers) -> ScopeHint:
    candidates: list[tuple[int, int, str]] = []
    for name, marker_set in markers.target_hint_markers.items():
        if name not in SCOPE_HINTS or name == "UNKNOWN":
            continue
        for strength_rank, marker_values in enumerate((marker_set.strong, marker_set.weak)):
            start = _first_marker_position(text, marker_values)
            if start is not None:
                candidates.append((strength_rank, start, name))
                break

    if not candidates:
        return "UNKNOWN"

    candidates.sort(key=lambda item: (item[0], item[1]))
    return cast(ScopeHint, candidates[0][2])


def _first_marker_position(text: str, marker_values: Iterable[str]) -> int | None:
    positions = [text.find(marker) for marker in marker_values if marker and marker in text]
    if not positions:
        return None
    return min(positions)


def _target_marker_set(markers: SemanticMarkers, key: str) -> MarkerSet | None:
    return markers.target_hint_markers.get(key)


def _marker_values(marker_set: MarkerSet) -> Iterable[str]:
    yield from marker_set.strong
    yield from marker_set.weak


def _dedup(values: Iterable[_T]) -> list[_T]:
    seen: set[T] = set()
    result: list[T] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
