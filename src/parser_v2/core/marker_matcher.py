from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

from src.parser_v2.contracts.enums import MarkerKind, MarkerStrength
from src.parser_v2.contracts.markers import MarkerMatch, NormalizedText
from src.parser_v2.contracts.rules import MarkerSet, SemanticMarkers


class MarkerMatcher:
    def match(self, normalized: NormalizedText, markers: SemanticMarkers) -> list[MarkerMatch]:
        text = normalized.normalized_text
        if not text:
            return []

        indexed_matches: list[tuple[int, MarkerMatch]] = []
        sequence = 0

        for kind, marker_groups in _iter_marker_groups(markers):
            for name, marker_set in marker_groups.items():
                # literal scan — invariato
                for strength, marker_values in _iter_strengths(marker_set):
                    for marker in marker_values:
                        if not marker:
                            continue
                        for start in _find_all(text, marker):
                            indexed_matches.append((
                                sequence,
                                MarkerMatch(
                                    name=name,
                                    kind=kind,
                                    strength=strength,
                                    marker=marker,
                                    start=start,
                                    end=start + len(marker),
                                ),
                            ))
                            sequence += 1

                # pattern scan — nuovo
                for strength, compiled in _iter_pattern_strengths(marker_set):
                    for pattern in compiled:
                        for m in pattern.finditer(text):
                            indexed_matches.append((
                                sequence,
                                MarkerMatch(
                                    name=name,
                                    kind=kind,
                                    strength=strength,
                                    marker=m.group(0),
                                    start=m.start(),
                                    end=m.end(),
                                ),
                            ))
                            sequence += 1

        indexed_matches.sort(key=lambda item: (item[1].start, item[1].end, item[0]))

        # dedup: stesso (start, end, name, kind, strength, marker) → tieni il primo (literal precede per sequence)
        seen: set[tuple] = set()
        result: list[MarkerMatch] = []
        for _, match in indexed_matches:
            key = (match.start, match.end, match.name, match.kind, match.strength, match.marker)
            if key not in seen:
                seen.add(key)
                result.append(match)

        return result


def _iter_marker_groups(
    markers: SemanticMarkers,
) -> Iterable[tuple[MarkerKind, Mapping[str, MarkerSet]]]:
    return (
        ("intent", markers.intent_markers),
        ("field", markers.field_markers),
        ("side", markers.side_markers),
        ("entry_type", markers.entry_type_markers),
        ("modify_entry_mode", markers.modify_entry_mode_markers),
        ("entry_selector", markers.entry_selector_markers),
        ("info", markers.info_markers),
        ("target_hint", markers.target_hint_markers),
    )


def _iter_strengths(marker_set: MarkerSet) -> Iterable[tuple[MarkerStrength, list[str]]]:
    return (
        ("strong", marker_set.strong),
        ("weak", marker_set.weak),
    )


def _iter_pattern_strengths(
    marker_set: MarkerSet,
) -> Iterable[tuple[MarkerStrength, list[re.Pattern[str]]]]:
    return (
        ("strong", marker_set._strong_compiled),
        ("weak", marker_set._weak_compiled),
    )


def _find_all(text: str, marker: str) -> Iterable[int]:
    start = 0
    while True:
        found = text.find(marker, start)
        if found == -1:
            return
        yield found
        start = found + 1
