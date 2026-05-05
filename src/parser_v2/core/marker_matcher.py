from __future__ import annotations

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

        indexed_matches.sort(key=lambda item: (item[1].start, item[1].end, item[0]))
        return [match for _, match in indexed_matches]


def _iter_marker_groups(
    markers: SemanticMarkers,
) -> Iterable[tuple[MarkerKind, Mapping[str, MarkerSet]]]:
    return (
        ("intent", markers.intent_markers),
        ("field", markers.field_markers),
        ("side", markers.side_markers),
        ("entry_type", markers.entry_type_markers),
        ("modify_entry_mode", markers.modify_entry_mode_markers),
        ("info", markers.info_markers),
        ("target_hint", markers.target_hint_markers),
    )


def _iter_strengths(marker_set: MarkerSet) -> Iterable[tuple[MarkerStrength, list[str]]]:
    return (
        ("strong", marker_set.strong),
        ("weak", marker_set.weak),
    )


def _find_all(text: str, marker: str) -> Iterable[int]:
    start = 0
    while True:
        found = text.find(marker, start)
        if found == -1:
            return
        yield found
        start = found + 1
