"""Config-driven text pattern matching for multi-trader channels."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

import yaml

_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_text(value: str) -> str:
    lowered = value.lower()
    collapsed = _WHITESPACE_RE.sub(" ", lowered)
    return collapsed.strip()


@dataclass(slots=True, frozen=True)
class TextPatternMatch:
    trader_id: str | None
    is_ambiguous: bool


@dataclass(slots=True, frozen=True)
class _PatternRule:
    trader_id: str
    all_of: tuple[str, ...]


class TextPatternCatalog:
    def __init__(self, config_path: str | Path | None = None) -> None:
        self._groups: dict[str, tuple[_PatternRule, ...]] = {}
        if config_path is None:
            return
        self.reload(config_path)

    def reload(self, config_path: str | Path) -> None:
        path = Path(config_path)
        if not path.exists():
            self._groups = {}
            return
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        groups_raw = raw.get("groups") or {}
        groups: dict[str, tuple[_PatternRule, ...]] = {}
        for group_name, group_value in groups_raw.items():
            group = group_value if isinstance(group_value, dict) else {}
            patterns_raw = group.get("patterns") or []
            patterns: list[_PatternRule] = []
            for item in patterns_raw:
                if not isinstance(item, dict):
                    continue
                trader_id = str(item.get("trader_id") or "").strip()
                all_of_raw = item.get("all_of") or []
                all_of = tuple(
                    _normalize_text(str(token))
                    for token in all_of_raw
                    if str(token).strip()
                )
                if trader_id and all_of:
                    patterns.append(_PatternRule(trader_id=trader_id, all_of=all_of))
            groups[str(group_name)] = tuple(patterns)
        self._groups = groups

    def resolve(self, pattern_group: str | None, text: str | None) -> TextPatternMatch:
        if not pattern_group or not text:
            return TextPatternMatch(trader_id=None, is_ambiguous=False)
        patterns = self._groups.get(pattern_group)
        if not patterns:
            return TextPatternMatch(trader_id=None, is_ambiguous=False)
        normalized = _normalize_text(text)
        matches = sorted({
            rule.trader_id
            for rule in patterns
            if all(token in normalized for token in rule.all_of)
        })
        if len(matches) == 1:
            return TextPatternMatch(trader_id=matches[0], is_ambiguous=False)
        if len(matches) > 1:
            return TextPatternMatch(trader_id=None, is_ambiguous=True)
        return TextPatternMatch(trader_id=None, is_ambiguous=False)

    def has_group(self, pattern_group: str) -> bool:
        return pattern_group in self._groups

    def trader_ids_for_group(self, pattern_group: str) -> set[str]:
        patterns = self._groups.get(pattern_group) or ()
        return {rule.trader_id for rule in patterns}

    @property
    def groups(self) -> set[str]:
        return set(self._groups)

    @property
    def all_trader_ids(self) -> set[str]:
        trader_ids: set[str] = set()
        for group_name in self._groups:
            trader_ids.update(self.trader_ids_for_group(group_name))
        return trader_ids


__all__ = ["TextPatternCatalog", "TextPatternMatch"]
