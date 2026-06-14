# Regex Patterns in `semantic_markers.json` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Aggiungere `strong_patterns` / `weak_patterns` (regex grezze) a `MarkerSet` in modo che `MarkerMatcher` li usi esattamente come i literal attuali, senza cambiare nulla nel flusso downstream.

**Architecture:** `MarkerSet` (Pydantic model) acquisisce due nuovi campi opzionali `strong_patterns`/`weak_patterns` e due `PrivateAttr` con i pattern compilati. `MarkerMatcher.match()` esegue un secondo scan con `re.finditer` dopo il literal scan, poi deduplica per `(start, end, name, kind, strength, marker)`. Tutto ciò che sta dopo `MarkerMatcher` rimane invariato.

**Tech Stack:** Python 3.11+, Pydantic v2, `re` stdlib, pytest

---

## File coinvolti

| File | Azione |
|------|--------|
| `src/parser_v2/contracts/rules.py` | Modifica — aggiungere campi e compilazione a `MarkerSet` |
| `src/parser_v2/core/marker_matcher.py` | Modifica — aggiungere pattern scan e dedup step |
| `src/parser_v2/tests/test_marker_matcher_patterns.py` | Nuovo — test TDD per entrambe le modifiche |

---

## Task 1: Aggiornare `MarkerSet` con `strong_patterns` / `weak_patterns`

**Files:**
- Modify: `src/parser_v2/contracts/rules.py`
- Test: `src/parser_v2/tests/test_marker_matcher_patterns.py`

### Contesto

`MarkerSet` è un Pydantic `BaseModel`. Per compilare i pattern al caricamento si usano `PrivateAttr` (non serializzati) e un `model_validator(mode="after")`.

`rules.py` attuale:
```python
class MarkerSet(RulesModel):
    strong: list[str] = Field(default_factory=list)
    weak: list[str] = Field(default_factory=list)
```

- [ ] **Step 1.1: Scrivere il test per pattern valido compilato**

Creare `src/parser_v2/tests/test_marker_matcher_patterns.py`:

```python
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
```

- [ ] **Step 1.2: Eseguire i test — devono fallire**

```bash
python -m pytest src/parser_v2/tests/test_marker_matcher_patterns.py -v
```

Atteso: `FAILED` — `MarkerSet` non ha ancora `strong_patterns`, `_strong_compiled`, ecc.

- [ ] **Step 1.3: Implementare le modifiche a `MarkerSet`**

In `src/parser_v2/contracts/rules.py` aggiungere in cima agli import:

```python
import re
```

E aggiungere dopo gli import esistenti questa funzione:

```python
def _compile_pattern_list(patterns: list[str], label: str) -> list[re.Pattern]:
    compiled = []
    for p in patterns:
        try:
            compiled.append(re.compile(p))
        except re.error as exc:
            raise ValueError(
                f"Invalid regex in {label}: {p!r} — {exc}"
            ) from exc
    return compiled
```

Poi sostituire `MarkerSet`:

```python
class MarkerSet(RulesModel):
    strong: list[str] = Field(default_factory=list)
    weak: list[str] = Field(default_factory=list)
    strong_patterns: list[str] = Field(default_factory=list)
    weak_patterns: list[str] = Field(default_factory=list)

    _strong_compiled: list[re.Pattern] = PrivateAttr(default_factory=list)
    _weak_compiled: list[re.Pattern] = PrivateAttr(default_factory=list)

    @model_validator(mode="after")
    def _compile_patterns(self) -> "MarkerSet":
        self._strong_compiled = _compile_pattern_list(self.strong_patterns, "strong_patterns")
        self._weak_compiled = _compile_pattern_list(self.weak_patterns, "weak_patterns")
        return self
```

Aggiungere `PrivateAttr` e `model_validator` agli import da pydantic (già presenti `Field`, `model_validator` — verificare):

```python
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator
```

- [ ] **Step 1.4: Eseguire i test — devono passare**

```bash
python -m pytest src/parser_v2/tests/test_marker_matcher_patterns.py::test_markerset_compiles_strong_patterns src/parser_v2/tests/test_marker_matcher_patterns.py::test_markerset_compiles_weak_patterns src/parser_v2/tests/test_marker_matcher_patterns.py::test_markerset_empty_patterns_by_default src/parser_v2/tests/test_marker_matcher_patterns.py::test_markerset_invalid_pattern_raises -v
```

Atteso: 4 `PASSED`.

- [ ] **Step 1.5: Verificare che i test esistenti passino ancora**

```bash
python -m pytest src/parser_v2/tests/ -q
```

Atteso: tutti `PASSED`, nessuna regressione.

- [ ] **Step 1.6: Commit**

```bash
git add src/parser_v2/contracts/rules.py src/parser_v2/tests/test_marker_matcher_patterns.py
git commit -m "feat(parser_v2): add strong_patterns/weak_patterns to MarkerSet with compile-time validation"
```

---

## Task 2: Aggiornare `MarkerMatcher` con pattern scan e dedup

**Files:**
- Modify: `src/parser_v2/core/marker_matcher.py`
- Test: `src/parser_v2/tests/test_marker_matcher_patterns.py`

### Contesto

`MarkerMatcher.match()` attuale fa un solo scan sui literal. Bisogna aggiungere un secondo scan sui pattern compilati e un dedup step finale.

- [ ] **Step 2.1: Aggiungere i test per il pattern scan**

Appendere a `src/parser_v2/tests/test_marker_matcher_patterns.py`:

```python
# ── Task 2: MarkerMatcher usa i pattern ─────────────────────────────────────

def test_pattern_produces_marker_match_with_matched_text():
    matcher = MarkerMatcher()
    result = matcher.match(_text("тп1: 100"), _field_markers(strong_patterns=["(?i)тп\\s*[1-5]:"]))
    assert len(result) == 1
    assert result[0].name == "take_profit"
    assert result[0].kind == "field"
    assert result[0].strength == "strong"
    assert result[0].marker == "тп1:"
    assert result[0].start == 0
    assert result[0].end == 4


def test_pattern_case_insensitive():
    matcher = MarkerMatcher()
    result = matcher.match(_text("ТП1: 100"), _field_markers(strong_patterns=["(?i)тп\\s*[1-5]:"]))
    assert len(result) == 1
    assert result[0].marker == "ТП1:"


def test_pattern_no_match_returns_empty():
    matcher = MarkerMatcher()
    result = matcher.match(_text("нет тейков здесь"), _field_markers(strong_patterns=["(?i)тп\\s*[1-5]:"]))
    assert result == []


def test_two_patterns_two_occurrences_ordered_by_position():
    matcher = MarkerMatcher()
    result = matcher.match(
        _text("тп1: 100\nцель2: 200"),
        _field_markers(strong_patterns=["(?i)тп\\s*[1-5]:", "(?i)цель\\s*[1-5]:"]),
    )
    assert len(result) == 2
    assert result[0].marker == "тп1:"
    assert result[1].marker == "цель2:"


def test_weak_pattern_produces_weak_strength():
    matcher = MarkerMatcher()
    result = matcher.match(_text("риск% небольшой"), _field_markers(weak_patterns=["риск%"]))
    assert len(result) == 1
    assert result[0].strength == "weak"
    assert result[0].marker == "риск%"


def test_literal_and_pattern_same_span_deduped_to_one():
    matcher = MarkerMatcher()
    # literal "тп1:" e pattern "(?i)тп\s*[1-5]:" matchano lo stesso span
    result = matcher.match(
        _text("тп1: 100"),
        _field_markers(strong=["тп1:"], strong_patterns=["(?i)тп\\s*[1-5]:"]),
    )
    assert len(result) == 1
    assert result[0].marker == "тп1:"


def test_literal_and_pattern_different_spans_both_kept():
    matcher = MarkerMatcher()
    result = matcher.match(
        _text("тейки: 100\nтп1: 200"),
        _field_markers(strong=["тейки:"], strong_patterns=["(?i)тп\\s*[1-5]:"]),
    )
    assert len(result) == 2


def test_pattern_feeds_into_marker_evidence_resolver():
    from src.parser_v2.contracts.rules import MarkerResolutionRules, ParserRules
    from src.parser_v2.core.marker_evidence_resolver import MarkerEvidenceResolver

    matcher = MarkerMatcher()
    text = "стоп в безубыток и бу"
    markers = SemanticMarkers(
        intent_markers={
            "MOVE_STOP_TO_BE": MarkerSet(
                strong_patterns=["стоп в безубыток"],
                weak=["бу"],
            )
        }
    )
    matches = matcher.match(_text(text), markers)
    assert len(matches) == 2  # strong pattern + weak literal

    rules = ParserRules(
        marker_resolution=MarkerResolutionRules(suppress_weak_inside_strong_same_intent=True)
    )
    result = MarkerEvidenceResolver().resolve(matches, rules, text=text)
    assert len(result.evidence) == 1
    assert result.evidence[0].strength == "strong"
    assert len(result.suppressed_markers) == 1
```

- [ ] **Step 2.2: Eseguire i test — devono fallire**

```bash
python -m pytest src/parser_v2/tests/test_marker_matcher_patterns.py -v -k "pattern or dedup or weak_pattern"
```

Atteso: `FAILED` — `MarkerMatcher` non fa ancora scan dei pattern.

- [ ] **Step 2.3: Aggiornare `MarkerMatcher`**

Sostituire il contenuto di `src/parser_v2/core/marker_matcher.py` con:

```python
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
) -> Iterable[tuple[MarkerStrength, list[re.Pattern]]]:
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
```

- [ ] **Step 2.4: Eseguire tutti i test del file**

```bash
python -m pytest src/parser_v2/tests/test_marker_matcher_patterns.py -v
```

Atteso: tutti `PASSED`.

- [ ] **Step 2.5: Verificare che nessun test esistente sia rotto**

```bash
python -m pytest src/parser_v2/tests/ -q
```

Atteso: tutti `PASSED`.

- [ ] **Step 2.6: Commit**

```bash
git add src/parser_v2/core/marker_matcher.py src/parser_v2/tests/test_marker_matcher_patterns.py
git commit -m "feat(parser_v2): extend MarkerMatcher with regex pattern scan and dedup"
```

---

## Verifica finale

- [ ] **Run completo della suite**

```bash
python -m pytest src/parser_v2/ -q
```

Atteso: tutti `PASSED`, nessuna regressione.

- [ ] **Smoke test manuale — profilo trader_prova**

Verifica che il profilo esistente carichi senza errori (i campi `*_patterns` sono opzionali e assenti nel JSON attuale — devono defaultare a lista vuota):

```bash
python -c "
from src.parser_v2.profiles.trader_prova import load_profile
p = load_profile()
print('Profile loaded OK')
print('strong_patterns sample:', p.markers.field_markers.get('take_profit').strong_patterns)
"
```

Atteso: `Profile loaded OK` e `strong_patterns sample: []`

> **Nota:** se `load_profile` non esiste con quel nome, adattare il comando al loader effettivo del profilo.
