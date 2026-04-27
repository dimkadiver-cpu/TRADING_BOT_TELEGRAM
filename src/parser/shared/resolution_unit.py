from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from src.parser.canonical_v1.intent_taxonomy import IntentName
from src.parser.text_utils import normalize_text

ResolutionUnit = Literal["MESSAGE_WIDE", "TARGET_ITEM_WIDE"]

_LINK_ID_RE = re.compile(
    r"(?:https?://)?t\.me/(?:c/\d+|[A-Za-z0-9_]+)/(?P<id>\d+)",
    re.IGNORECASE,
)
_REPORT_VALUE_RE = re.compile(r"\b[+-]?\d+(?:[.,]\d+)?\s*(?:R{1,2}|%)\b", re.IGNORECASE)


class TargetedItem(BaseModel):
    text: str
    target_ref: Any
    target_history: list[IntentName] = Field(default_factory=list)


def _split_lines(text: str) -> list[str]:
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def _target_ref_value(target_ref: Any) -> int | str | None:
    if isinstance(target_ref, dict):
        value = target_ref.get("ref")
    else:
        value = target_ref
    if isinstance(value, (int, str)):
        return value
    return None


def _target_history(target_ref: Any) -> list[IntentName]:
    if isinstance(target_ref, dict):
        history = target_ref.get("target_history")
        if isinstance(history, list):
            return [item for item in history if isinstance(item, str)]
    return []


def _line_ref_ids(line: str) -> list[int]:
    ids: list[int] = []
    seen: set[int] = set()
    for match in _LINK_ID_RE.finditer(line):
        value = int(match.group("id"))
        if value in seen:
            continue
        seen.add(value)
        ids.append(value)
    return ids


def _line_signature(line: str) -> str:
    normalized = normalize_text(line)

    if _REPORT_VALUE_RE.search(line):
        return "report"

    if any(token in normalized for token in ("стоп в бу", "stop in be", "stop to be", "breakeven")):
        return "move_stop_to_be"

    if any(token in normalized for token in ("стоп на 1 тейк", "стоп на первый тейк", "стоп на tp1")):
        return "move_stop_tp1"

    if any(token in normalized for token in ("close all", "закрываю", "закрыть", "chiudo")):
        return "close_full"

    return "unknown"


def decide_resolution_unit(text: str, target_refs: list[Any]) -> ResolutionUnit:
    if len(target_refs) <= 1:
        return "MESSAGE_WIDE"

    signatures: set[str] = set()
    for line in _split_lines(text):
        if not _line_ref_ids(line):
            continue
        signatures.add(_line_signature(line))

    if len(signatures) <= 1:
        return "MESSAGE_WIDE"
    return "TARGET_ITEM_WIDE"


def extract_targeted_items(text: str, target_refs: list[Any]) -> list[TargetedItem]:
    if not target_refs:
        return []

    target_by_ref = {
        _target_ref_value(target_ref): target_ref
        for target_ref in target_refs
        if _target_ref_value(target_ref) is not None
    }

    if decide_resolution_unit(text, target_refs) == "MESSAGE_WIDE":
        return [
            TargetedItem(
                text=text,
                target_ref=target_ref,
                target_history=_target_history(target_ref),
            )
            for target_ref in target_refs
        ]

    items: list[TargetedItem] = []
    for line in _split_lines(text):
        ref_ids = _line_ref_ids(line)
        if not ref_ids:
            continue
        for ref_id in ref_ids:
            target_ref = target_by_ref.get(ref_id, {"kind": "message_id", "ref": ref_id})
            items.append(
                TargetedItem(
                    text=line,
                    target_ref=target_ref,
                    target_history=_target_history(target_ref),
                )
            )

    if items:
        return items

    return [
        TargetedItem(
            text=text,
            target_ref=target_ref,
            target_history=_target_history(target_ref),
        )
        for target_ref in target_refs
    ]
