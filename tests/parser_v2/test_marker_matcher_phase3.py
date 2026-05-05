from __future__ import annotations

from src.parser_v2.contracts.markers import MarkerMatch
from src.parser_v2.contracts.rules import MarkerSet, SemanticMarkers
from src.parser_v2.core.marker_matcher import MarkerMatcher
from src.parser_v2.core.text_normalizer import TextNormalizer


STOP_TO_BE_RAW = "\u0421\u0442\u043e\u043f \u0432 \u0411\u0423"
STOP_TO_BE = "\u0441\u0442\u043e\u043f \u0432 \u0431\u0443"
BE = "\u0431\u0443"


def test_matches_strong_intent_with_span_in_normalized_text() -> None:
    normalized = TextNormalizer().normalize(STOP_TO_BE_RAW)
    markers = SemanticMarkers(
        intent_markers={
            "MOVE_STOP_TO_BE": MarkerSet(strong=[STOP_TO_BE]),
        }
    )

    matches = MarkerMatcher().match(normalized, markers)

    assert matches == [
        MarkerMatch(
            name="MOVE_STOP_TO_BE",
            kind="intent",
            strength="strong",
            marker=STOP_TO_BE,
            start=0,
            end=9,
        )
    ]


def test_matches_weak_inside_strong_without_suppression() -> None:
    normalized = TextNormalizer().normalize(STOP_TO_BE_RAW)
    markers = SemanticMarkers(
        intent_markers={
            "MOVE_STOP_TO_BE": MarkerSet(
                strong=[STOP_TO_BE],
                weak=[BE],
            ),
            "EXIT_BE": MarkerSet(weak=[BE]),
        }
    )

    matches = MarkerMatcher().match(normalized, markers)

    assert matches == [
        MarkerMatch(
            name="MOVE_STOP_TO_BE",
            kind="intent",
            strength="strong",
            marker=STOP_TO_BE,
            start=0,
            end=9,
        ),
        MarkerMatch(
            name="MOVE_STOP_TO_BE",
            kind="intent",
            strength="weak",
            marker=BE,
            start=7,
            end=9,
        ),
        MarkerMatch(
            name="EXIT_BE",
            kind="intent",
            strength="weak",
            marker=BE,
            start=7,
            end=9,
        ),
    ]


def test_supports_multiple_occurrences() -> None:
    normalized = TextNormalizer().normalize(f"{BE} \u0438 \u0441\u043d\u043e\u0432\u0430 {BE}")
    markers = SemanticMarkers(
        intent_markers={
            "EXIT_BE": MarkerSet(weak=[BE]),
        }
    )

    matches = MarkerMatcher().match(normalized, markers)

    assert matches == [
        MarkerMatch(
            name="EXIT_BE",
            kind="intent",
            strength="weak",
            marker=BE,
            start=0,
            end=2,
        ),
        MarkerMatch(
            name="EXIT_BE",
            kind="intent",
            strength="weak",
            marker=BE,
            start=11,
            end=13,
        ),
    ]
