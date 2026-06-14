from __future__ import annotations

import re
import pytest

from src.parser_v2.contracts.rules import MarkerSet, SemanticMarkers
from src.parser_v2.contracts.markers import NormalizedText
from src.parser_v2.core.marker_matcher import MarkerMatcher


# ── helpers ─────────────────────────────────────────────────────────────────

def _text(s: str) -> NormalizedText:
    return NormalizedText(raw_text=s, normalized_text=s)


def _field_markers(**kwargs) -> SemanticMarkers:
    return SemanticMarkers(field_markers={"take_profit": MarkerSet(**kwargs)})


# ── Task 1: MarkerSet compila i pattern ─────────────────────────────────────

def test_markerset_compiles_strong_patterns():
    ms = MarkerSet(strong_patterns=["(?i)тп\\s*[1-5]:"])
    assert len(ms._strong_compiled) == 1
    assert isinstance(ms._strong_compiled[0], re.Pattern)


def test_markerset_compiles_weak_patterns():
    ms = MarkerSet(weak_patterns=["риск\\s*%"])
    assert len(ms._weak_compiled) == 1
    assert isinstance(ms._weak_compiled[0], re.Pattern)


def test_markerset_empty_patterns_by_default():
    ms = MarkerSet(strong=["тейки"])
    assert ms.strong_patterns == []
    assert ms._strong_compiled == []
    assert ms._weak_compiled == []


def test_markerset_invalid_pattern_raises():
    with pytest.raises(ValueError, match="strong_patterns"):
        MarkerSet(strong_patterns=["[invalid"])
