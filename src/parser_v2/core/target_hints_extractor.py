from __future__ import annotations

import re
from collections.abc import Iterable
from typing import TypeVar

from src.parser_v2.contracts.context import (
    ParserContext,
    TargetCandidate,
    TargetExtractionResult,
    TargetHints,
)
from src.parser_v2.contracts.enums import ScopeHint, TargetSource
from src.parser_v2.contracts.markers import NormalizedText
from src.parser_v2.contracts.rules import MarkerSet, SemanticMarkers
from src.parser_v2.core.symbol_normalizer import normalize_symbol

_T = TypeVar("_T")


TELEGRAM_LINK_RE = re.compile(
    r"\b(?:https?://)?t\.me/(?:c/\d+|[a-zA-Z0-9_]+)/\d+\b",
    re.IGNORECASE,
)
TOKEN_RE = re.compile(r"#?[a-z0-9][a-z0-9._-]*", re.IGNORECASE)
TRAILING_LINK_CHARS = ".,;:!?)]}\"'"
_EXPLICIT_ID_SAMPLE_SUFFIX_RE = re.compile(r"(#?(?:[a-z]|0))$", re.IGNORECASE)
SCOPE_HINTS: set[str] = {
    "SINGLE_SIGNAL", "SYMBOL", "ALL_LONG", "ALL_SHORT",
    "ALL_POSITIONS", "ALL_OPEN", "ALL_REMAINING",
}

_SOURCE_PRIORITY: dict[str, int] = {
    "LOCAL_TEXT_LINK": 0,
    "LOCAL_EXPLICIT_ID": 1,
    "MESSAGE_TEXT_LINK": 2,
    "MESSAGE_EXPLICIT_ID": 3,
    "REPLY": 4,
    "SYMBOL": 5,
    "GLOBAL_SCOPE": 6,
    "UNKNOWN": 7,
}


class TargetHintsExtractor:
    def extract(
        self,
        normalized: NormalizedText,
        context: ParserContext,
        markers: SemanticMarkers,
    ) -> TargetExtractionResult:
        candidates: list[TargetCandidate] = []

        raw_text = normalized.raw_text
        link_matches = list(TELEGRAM_LINK_RE.finditer(raw_text))
        links: list[str] = []
        message_ids: list[int] = []
        for match in link_matches:
            link = match.group(0).rstrip(TRAILING_LINK_CHARS)
            if link in links:
                continue
            links.append(link)
            msg_id = _message_id_from_link(link)
            if msg_id is not None:
                message_ids.append(msg_id)
                line_idx = raw_text.count("\n", 0, match.start())
                candidates.append(TargetCandidate(
                    source="MESSAGE_TEXT_LINK",
                    value=msg_id,
                    start=match.start(),
                    end=match.end(),
                    line_index=line_idx,
                ))

        reply_id = _reply_to_message_id(context)
        if reply_id is not None:
            candidates.append(TargetCandidate(source="REPLY", value=reply_id))

        explicit_ids = _dedup(_extract_explicit_ids(normalized.normalized_text, markers))
        for eid in explicit_ids:
            candidates.append(TargetCandidate(source="MESSAGE_EXPLICIT_ID", value=eid))

        symbols = _dedup(_extract_symbols(normalized.normalized_text, markers))
        for sym in symbols:
            candidates.append(TargetCandidate(source="SYMBOL", value=sym))

        scope_hint = _extract_scope_hint(normalized.normalized_text, markers)

        # Se il messaggio punta già a signal specifici via link, lo scope_hint testuale
        # (es. "по шортам" in un p.s.) è informativo e non deve diventare scope dell'azione.
        effective_scope_hint: ScopeHint = scope_hint
        if message_ids and scope_hint not in ("UNKNOWN", "SINGLE_SIGNAL"):
            effective_scope_hint = "UNKNOWN"

        target_source: TargetSource = "UNKNOWN"
        if message_ids:
            target_source = "MESSAGE_TEXT_LINK"
        elif explicit_ids:
            target_source = "MESSAGE_EXPLICIT_ID"
        elif reply_id is not None:
            target_source = "REPLY"
        elif symbols:
            target_source = "SYMBOL"
        elif effective_scope_hint not in ("UNKNOWN", "SINGLE_SIGNAL"):
            target_source = "GLOBAL_SCOPE"

        message_target_hints = TargetHints(
            target_source=target_source,
            reply_to_message_id=reply_id,
            telegram_links=links,
            telegram_message_ids=message_ids,
            explicit_ids=explicit_ids,
            symbols=symbols,
            scope_hint=effective_scope_hint,
        )

        return TargetExtractionResult(
            message_target_hints=message_target_hints,
            candidates=candidates,
        )


def _reply_to_message_id(context: ParserContext) -> int | None:
    if context.reply_to_message_id is not None:
        return context.reply_to_message_id
    if context.raw_context is not None:
        return context.raw_context.reply_to_message_id
    return None


def _message_id_from_link(link: str) -> int | None:
    tail = link.rstrip("/").rsplit("/", 1)[-1]
    return int(tail) if tail.isdigit() else None


def _extract_explicit_ids(text: str, markers: SemanticMarkers) -> Iterable[str]:
    explicit_markers = (
        markers.target_hint_markers.get("explicit_id")
        or markers.target_hint_markers.get("EXPLICIT_ID")
    )
    if explicit_markers is None:
        return []

    for marker in _marker_values(explicit_markers):
        value = marker.strip().lower()
        if not value:
            continue

        suffix_match = _EXPLICIT_ID_SAMPLE_SUFFIX_RE.search(value)
        if suffix_match is None:
            prefix = value
            token_pattern = r"#?(?:[a-z]\d+|\d+)"
        else:
            sample = suffix_match.group(1)
            prefix = value[:-len(sample)]
            sample_body = sample.lstrip("#")
            if sample_body == "0":
                token_pattern = r"#\d+" if sample.startswith("#") else r"\d+"
            else:
                token_pattern = r"#[a-z]\d+" if sample.startswith("#") else r"[a-z]\d+"

        if prefix:
            pattern = re.compile(
                re.escape(prefix) + rf"((?:{token_pattern})(?:\s+(?:{token_pattern}))*)",
                re.IGNORECASE,
            )
            for match in pattern.finditer(text):
                for token in re.findall(token_pattern, match.group(1), re.IGNORECASE):
                    cleaned = token.lstrip("#")
                    if cleaned:
                        yield cleaned
            continue

        standalone_pattern = re.compile(rf"(?<!\w)({token_pattern})\b", re.IGNORECASE)
        for match in standalone_pattern.finditer(text):
            cleaned = match.group(1).lstrip("#")
            if cleaned:
                yield cleaned


def _extract_symbols(text: str, markers: SemanticMarkers) -> Iterable[str]:
    symbol_markers = markers.target_hint_markers.get("symbol") or markers.target_hint_markers.get("SYMBOL")
    if symbol_markers is None:
        return []
    marker_values = [m.lower() for m in _marker_values(symbol_markers) if m]
    if not marker_values:
        return []
    symbols: list[str] = []
    for match in TOKEN_RE.finditer(text):
        token = match.group(0).lstrip("#").strip(".,;:!?()[]{}")
        if not token:
            continue
        if any(marker in token and len(token) > len(marker) for marker in marker_values):
            normalized = normalize_symbol(token)
            if normalized is not None:
                symbols.append(normalized)
    return symbols


def _extract_scope_hint(text: str, markers: SemanticMarkers) -> ScopeHint:
    from typing import cast
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
    return min(positions) if positions else None


def _marker_values(marker_set: MarkerSet) -> Iterable[str]:
    yield from marker_set.strong
    yield from marker_set.weak


def _dedup(values: Iterable[_T]) -> list[_T]:
    seen: set[_T] = set()
    result: list[_T] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
