# MODIFY_ENTRY Robust Handling — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migliorare `MODIFY_ENTRY` in parser_v2: più marker, mode detection via evidence list, entry_selector come MarkerKind di primo livello, estrazione range/ladder, propagazione completa nel canonical output.

**Architecture:** Tutto il rilevamento di mode e selector passa dall'evidence list prodotta da `MarkerMatcher` — si rimuovono i regex paralleli `_RE_MARKET_NOW`/`_RE_REMOVE`. `_modify_entry_entities()` riceve la lista completa di evidence come terzo argomento via dispatch speciale, senza toccare gli altri builder.

**Tech Stack:** Python 3.12, Pydantic v2, pytest. Nessuna dipendenza nuova.

---

## File Map

| File | Operazione |
|---|---|
| `src/parser_v2/contracts/enums.py` | Modifica: +`entry_selector` in `MarkerKind`, +`UPDATE_RANGE`/`REPLACE_ENTRY` in mode/kind |
| `src/parser_v2/contracts/entities.py` | Modifica: +`EntrySelector`, aggiorna `ModifyEntryEntities` |
| `src/parser_v2/contracts/canonical_message.py` | Modifica: +`entry_selector` in `ModifyEntriesOperation`, +import |
| `src/parser_v2/contracts/rules.py` | Modifica: +`entry_selector_markers` in `SemanticMarkers` |
| `src/parser_v2/core/marker_matcher.py` | Modifica: +1 riga in `_iter_marker_groups` |
| `src/parser_v2/profiles/trader_a/semantic_markers.json` | Modifica: espande MODIFY_ENTRY, completa mode markers, aggiunge selector markers |
| `src/parser_v2/profiles/trader_a/intent_entity_extractor.py` | Modifica: refactor `_modify_entry_entities`, rimuove `_RE_MARKET_NOW`/`_RE_REMOVE` |
| `src/parser_v2/translation/canonical_translator.py` | Modifica: propaga `entry_structure`/`entry_selector` nel ramo MODIFY_ENTRY |
| `src/parser_v2/tests/test_modify_entry_extractor.py` | Crea: test completi per il nuovo extractor |

---

## Task 1: Aggiorna `enums.py`

**Files:**
- Modify: `src/parser_v2/contracts/enums.py:34-42,70-79`

- [ ] **Step 1: Sostituisci i tre Literal in enums.py**

Apri `src/parser_v2/contracts/enums.py`. Sostituisci le righe 34-42 (ModifyEntryMode + ModifyEntriesOperationKind) e 70-79 (MarkerKind) con:

```python
# riga 34
ModifyEntryMode = Literal[
    "MARKET_NOW",
    "UPDATE_PRICE",
    "UPDATE_RANGE",
    "REPLACE_ENTRY",
    "REMOVE",
    "UNKNOWN",
]
ModifyEntriesOperationKind = Literal[
    "ADD",
    "REENTER",
    "MARKET_NOW",
    "UPDATE_PRICE",
    "UPDATE_RANGE",
    "REPLACE_ENTRY",
    "REMOVE",
    "UNKNOWN",
]
```

```python
# riga 70 (MarkerKind)
MarkerKind = Literal[
    "intent",
    "field",
    "side",
    "entry_type",
    "modify_entry_mode",
    "entry_selector",
    "info",
    "target_hint",
]
```

- [ ] **Step 2: Verifica che i test esistenti passino ancora**

```
pytest src/parser_v2/tests/ -v -x -q
```

Atteso: tutti PASS (nessun test usa ancora UPDATE_RANGE/REPLACE_ENTRY/entry_selector).

- [ ] **Step 3: Commit**

```bash
git add src/parser_v2/contracts/enums.py
git commit -m "feat(parser_v2): add UPDATE_RANGE, REPLACE_ENTRY, entry_selector to enums"
```

---

## Task 2: Aggiorna `entities.py` — aggiungi `EntrySelector`, estendi `ModifyEntryEntities`

**Files:**
- Modify: `src/parser_v2/contracts/entities.py`

- [ ] **Step 1: Aggiungi `EntrySelector` e aggiorna `ModifyEntryEntities`**

In `src/parser_v2/contracts/entities.py`, dopo la classe `AddEntryEntities` (attualmente riga ~141) e prima di `ModifyEntryEntities`, aggiungi:

```python
class EntrySelector(ContractModel):
    role: EntryRole | None = None
    sequence: int | None = Field(default=None, ge=1)
    label: str | None = None
    raw: str | None = None
```

Poi sostituisci `ModifyEntryEntities` (attualmente righe 146-149):

```python
class ModifyEntryEntities(IntentEntities):
    mode: ModifyEntryMode = "UNKNOWN"
    entry_selector: EntrySelector | None = None
    entries: list[EntryLeg] = Field(default_factory=list)
    entry_structure: EntryStructure | None = None
    raw_mode_marker: str | None = None
    raw_selector_marker: str | None = None
```

- [ ] **Step 2: Scrivi test per i nuovi modelli**

Crea file temporaneo o aggiungi a `src/parser_v2/tests/test_contracts_parsed_intent.py` (esiste già):

```python
def test_entry_selector_instantiation():
    from src.parser_v2.contracts.entities import EntrySelector
    s = EntrySelector(role="PRIMARY", sequence=1, raw="основной вход")
    assert s.role == "PRIMARY"
    assert s.sequence == 1
    assert s.label is None

def test_entry_selector_empty():
    from src.parser_v2.contracts.entities import EntrySelector
    s = EntrySelector()
    assert s.role is None
    assert s.sequence is None

def test_modify_entry_entities_new_fields():
    from src.parser_v2.contracts.entities import EntrySelector, ModifyEntryEntities
    e = ModifyEntryEntities(
        mode="UPDATE_RANGE",
        entry_structure="RANGE",
        entry_selector=EntrySelector(role="PRIMARY", sequence=1, raw="основной вход"),
        raw_selector_marker="основной вход",
    )
    assert e.mode == "UPDATE_RANGE"
    assert e.entry_selector.role == "PRIMARY"
    assert e.entry_structure == "RANGE"
    assert e.raw_selector_marker == "основной вход"
```

- [ ] **Step 3: Esegui i test**

```
pytest src/parser_v2/tests/ -v -x -q
```

Atteso: tutti PASS.

- [ ] **Step 4: Commit**

```bash
git add src/parser_v2/contracts/entities.py src/parser_v2/tests/test_contracts_parsed_intent.py
git commit -m "feat(parser_v2): add EntrySelector model and extend ModifyEntryEntities"
```

---

## Task 3: Aggiorna `canonical_message.py` — aggiungi `entry_selector` a `ModifyEntriesOperation`

**Files:**
- Modify: `src/parser_v2/contracts/canonical_message.py`

- [ ] **Step 1: Aggiungi import e campo**

In `src/parser_v2/contracts/canonical_message.py`:

1. Aggiorna l'import da `entities.py` (attualmente riga 8):

```python
from .entities import EntryLeg, EntrySelector, Price, RiskHint, SignalFields, StopLoss, TakeProfit
```

2. Aggiorna `ModifyEntriesOperation` (attualmente righe 59-63):

```python
class ModifyEntriesOperation(CanonicalModel):
    kind: ModifyEntriesOperationKind
    entries: list[EntryLeg] = Field(default_factory=list)
    entry_structure: EntryStructure | None = None
    entry_selector: EntrySelector | None = None
```

- [ ] **Step 2: Verifica test esistenti**

```
pytest src/parser_v2/tests/test_contracts_canonical.py -v -x -q
```

Atteso: tutti PASS (il campo è opzionale, nessun test esistente lo usa).

- [ ] **Step 3: Commit**

```bash
git add src/parser_v2/contracts/canonical_message.py
git commit -m "feat(parser_v2): add entry_selector to ModifyEntriesOperation"
```

---

## Task 4: Wira `entry_selector_markers` in `rules.py` + `marker_matcher.py`

**Files:**
- Modify: `src/parser_v2/contracts/rules.py:19-29`
- Modify: `src/parser_v2/core/marker_matcher.py:43-54`

- [ ] **Step 1: Aggiungi il campo in `SemanticMarkers`**

In `src/parser_v2/contracts/rules.py`, aggiorna la classe `SemanticMarkers` (righe 19-29):

```python
class SemanticMarkers(RulesModel):
    model_config = ConfigDict(extra="ignore")
    language: str | None = None
    intent_markers: dict[IntentType, MarkerSet] = Field(default_factory=dict)
    field_markers: dict[str, MarkerSet] = Field(default_factory=dict)
    side_markers: dict[Side, MarkerSet] = Field(default_factory=dict)
    entry_type_markers: dict[EntryType, MarkerSet] = Field(default_factory=dict)
    modify_entry_mode_markers: dict[ModifyEntryMode, MarkerSet] = Field(default_factory=dict)
    entry_selector_markers: dict[str, MarkerSet] = Field(default_factory=dict)
    info_markers: dict[str, MarkerSet] = Field(default_factory=dict)
    target_hint_markers: dict[str, MarkerSet] = Field(default_factory=dict)
    ignore_markers: list[str] = Field(default_factory=list)
```

- [ ] **Step 2: Wira in `_iter_marker_groups`**

In `src/parser_v2/core/marker_matcher.py`, aggiorna `_iter_marker_groups` (righe 43-54):

```python
def _iter_marker_groups(
    markers: SemanticMarkers,
) -> Iterable[tuple[MarkerKind, Mapping[str, MarkerSet]]]:
    return (
        ("intent",            markers.intent_markers),
        ("field",             markers.field_markers),
        ("side",              markers.side_markers),
        ("entry_type",        markers.entry_type_markers),
        ("modify_entry_mode", markers.modify_entry_mode_markers),
        ("entry_selector",    markers.entry_selector_markers),
        ("info",              markers.info_markers),
        ("target_hint",       markers.target_hint_markers),
    )
```

- [ ] **Step 3: Scrivi test per il nuovo MarkerKind**

Aggiungi a `src/parser_v2/tests/test_contracts_rules.py` (esiste già):

```python
def test_semantic_markers_accepts_entry_selector_markers():
    from src.parser_v2.contracts.rules import SemanticMarkers, MarkerSet
    sm = SemanticMarkers(
        intent_markers={},
        entry_selector_markers={
            "PRIMARY": MarkerSet(strong=["основной вход", "первый вход"], weak=[]),
            "AVERAGING": MarkerSet(strong=["усреднение"], weak=[]),
        },
    )
    assert "PRIMARY" in sm.entry_selector_markers
    assert sm.entry_selector_markers["PRIMARY"].strong == ["основной вход", "первый вход"]
```

Aggiungi a `src/parser_v2/tests/test_marker_evidence_resolver_weak_context.py` oppure crea inline:

```python
def test_marker_matcher_produces_entry_selector_evidence():
    from src.parser_v2.core.marker_matcher import MarkerMatcher
    from src.parser_v2.contracts.markers import NormalizedText
    from src.parser_v2.contracts.rules import SemanticMarkers, MarkerSet

    sm = SemanticMarkers(
        intent_markers={},
        entry_selector_markers={
            "PRIMARY": MarkerSet(strong=["основной вход"], weak=[]),
        },
    )
    normalized = NormalizedText(
        raw_text="основной вход переносим на 2114",
        normalized_text="основной вход переносим на 2114",
        lines=["основной вход переносим на 2114"],
    )
    matches = MarkerMatcher().match(normalized, sm)
    selector_matches = [m for m in matches if m.kind == "entry_selector"]
    assert len(selector_matches) == 1
    assert selector_matches[0].name == "PRIMARY"
    assert selector_matches[0].marker == "основной вход"
```

- [ ] **Step 4: Esegui i test**

```
pytest src/parser_v2/tests/ -v -x -q
```

Atteso: tutti PASS.

- [ ] **Step 5: Commit**

```bash
git add src/parser_v2/contracts/rules.py src/parser_v2/core/marker_matcher.py src/parser_v2/tests/test_contracts_rules.py
git commit -m "feat(parser_v2): wire entry_selector_markers through SemanticMarkers and MarkerMatcher"
```

---

## Task 5: Aggiorna `semantic_markers.json`

**Files:**
- Modify: `src/parser_v2/profiles/trader_a/semantic_markers.json`

- [ ] **Step 1: Espandi `intent_markers.MODIFY_ENTRY`**

Nel file JSON, trova la sezione `intent_markers` e sostituisci il blocco `MODIFY_ENTRY`:

```json
"MODIFY_ENTRY": {
  "strong": [
    "входим по рынку",
    "вход по рынку",
    "новый вход",
    "новая точка входа",
    "вход теперь",
    "вход меняем",
    "меняем вход",
    "переносим вход",
    "вход переносим",
    "лимитку входа переносим",
    "основной вход переносим",
    "усреднение переносим",
    "убираем вход"
  ],
  "weak": ["точка входа"]
}
```

- [ ] **Step 2: Sostituisci `modify_entry_mode_markers` completa**

Sostituisci l'intera sezione `modify_entry_mode_markers` con:

```json
"modify_entry_mode_markers": {
  "MARKET_NOW": {
    "strong": [
      "входим по рынку",
      "вход по рынку",
      "по текущим",
      "с текущих"
    ],
    "weak": []
  },
  "UPDATE_PRICE": {
    "strong": [
      "новый вход",
      "новая точка входа",
      "вход теперь",
      "вход меняем",
      "меняем вход",
      "переносим вход",
      "вход переносим",
      "лимитку входа переносим",
      "основной вход переносим",
      "усреднение переносим"
    ],
    "weak": []
  },
  "UPDATE_RANGE": {
    "strong": [
      "диапазон входа",
      "вход в диапазон"
    ],
    "weak": []
  },
  "REPLACE_ENTRY": {
    "strong": [
      "заменяем вход",
      "полностью меняем вход"
    ],
    "weak": []
  },
  "REMOVE": {
    "strong": [
      "убираем вход"
    ],
    "weak": []
  }
}
```

- [ ] **Step 3: Aggiungi sezione `entry_selector_markers`**

Dopo `modify_entry_mode_markers`, aggiungi la nuova sezione (a livello radice del JSON, non dentro `intent_markers`):

```json
"entry_selector_markers": {
  "PRIMARY": {
    "strong": [
      "основной вход",
      "первый вход",
      "вход a",
      "entry a"
    ],
    "weak": []
  },
  "AVERAGING": {
    "strong": [
      "усреднение",
      "лимитка на усреднение",
      "вход b",
      "entry b"
    ],
    "weak": []
  }
}
```

> **Nota:** il contenuto di questi marker è soggetto a revisione separata dall'utente. Modifica le liste strong/weak secondo i dati reali prima della messa in produzione.

- [ ] **Step 4: Verifica che il JSON sia valido e i test esistenti passino**

```
python -c "import json; json.load(open('src/parser_v2/profiles/trader_a/semantic_markers.json'))"
pytest src/parser_v2/tests/ -v -x -q
```

Atteso: JSON valido, tutti i test PASS.

- [ ] **Step 5: Commit**

```bash
git add src/parser_v2/profiles/trader_a/semantic_markers.json
git commit -m "feat(trader_a): expand MODIFY_ENTRY markers, add UPDATE_RANGE/REPLACE_ENTRY mode markers, add entry_selector_markers"
```

---

## Task 6: Scrivi i test fallenti per il nuovo extractor

**Files:**
- Create: `src/parser_v2/tests/test_modify_entry_extractor.py`

- [ ] **Step 1: Crea il file di test con tutti i casi**

```python
# src/parser_v2/tests/test_modify_entry_extractor.py
from __future__ import annotations

import pytest

from src.parser_v2.contracts.entities import ModifyEntryEntities
from src.parser_v2.contracts.markers import MarkerEvidence, NormalizedText
from src.parser_v2.profiles.trader_a.intent_entity_extractor import IntentEntityExtractor


def _ev(
    name: str,
    kind: str,
    marker: str,
    start: int,
    strength: str = "strong",
    suppressed: bool = False,
) -> MarkerEvidence:
    return MarkerEvidence(
        name=name,
        kind=kind,  # type: ignore[arg-type]
        strength=strength,  # type: ignore[arg-type]
        marker=marker,
        start=start,
        end=start + len(marker),
        suppressed=suppressed,
    )


def _normalized(text: str) -> NormalizedText:
    return NormalizedText(
        raw_text=text,
        normalized_text=text,
        lines=text.splitlines() or [text],
    )


_extractor = IntentEntityExtractor()


def _extract_first(text: str, evidence: list[MarkerEvidence]) -> ModifyEntryEntities:
    intents = _extractor.extract(_normalized(text), evidence)
    assert len(intents) == 1, f"Expected 1 intent, got {len(intents)}: {intents}"
    e = intents[0].entities
    assert isinstance(e, ModifyEntryEntities), f"Expected ModifyEntryEntities, got {type(e)}"
    return e


# ---------------------------------------------------------------------------
# UPDATE_PRICE — prezzo singolo
# ---------------------------------------------------------------------------

def test_update_price_single_new_entry():
    """новый вход 2114 → UPDATE_PRICE / ONE_SHOT / LIMIT 2114"""
    text = "новый вход 2114"
    marker = "новый вход"
    evidence = [
        _ev("MODIFY_ENTRY", "intent", marker, 0),
        _ev("UPDATE_PRICE", "modify_entry_mode", marker, 0),
    ]
    e = _extract_first(text, evidence)
    assert e.mode == "UPDATE_PRICE"
    assert e.entry_structure == "ONE_SHOT"
    assert len(e.entries) == 1
    assert e.entries[0].entry_type == "LIMIT"
    assert e.entries[0].price.value == 2114.0
    assert e.entry_selector is None


def test_update_price_variant_vhod_teper():
    """вход теперь 2114 → UPDATE_PRICE / ONE_SHOT / LIMIT 2114"""
    text = "вход теперь 2114"
    marker = "вход теперь"
    evidence = [
        _ev("MODIFY_ENTRY", "intent", marker, 0),
        _ev("UPDATE_PRICE", "modify_entry_mode", marker, 0),
    ]
    e = _extract_first(text, evidence)
    assert e.mode == "UPDATE_PRICE"
    assert e.entry_structure == "ONE_SHOT"
    assert e.entries[0].price.value == 2114.0


# ---------------------------------------------------------------------------
# UPDATE_RANGE
# ---------------------------------------------------------------------------

def test_update_range_dash_separated():
    """вход теперь 2114-2120 → UPDATE_RANGE / RANGE / [LIMIT 2114, LIMIT 2120]"""
    text = "вход теперь 2114-2120"
    marker = "вход теперь"
    evidence = [
        _ev("MODIFY_ENTRY", "intent", marker, 0),
        _ev("UPDATE_PRICE", "modify_entry_mode", marker, 0),
    ]
    e = _extract_first(text, evidence)
    assert e.mode == "UPDATE_RANGE"
    assert e.entry_structure == "RANGE"
    assert len(e.entries) == 2
    assert e.entries[0].price.value == 2114.0
    assert e.entries[1].price.value == 2120.0


# ---------------------------------------------------------------------------
# LADDER
# ---------------------------------------------------------------------------

def test_ladder_three_prices():
    """вход теперь 2114 2100 2080 → UPDATE_PRICE / LADDER / 3 legs"""
    text = "вход теперь 2114 2100 2080"
    marker = "вход теперь"
    evidence = [
        _ev("MODIFY_ENTRY", "intent", marker, 0),
        _ev("UPDATE_PRICE", "modify_entry_mode", marker, 0),
    ]
    e = _extract_first(text, evidence)
    assert e.mode == "UPDATE_PRICE"
    assert e.entry_structure == "LADDER"
    assert len(e.entries) == 3
    assert [leg.price.value for leg in e.entries] == [2114.0, 2100.0, 2080.0]


# ---------------------------------------------------------------------------
# MARKET_NOW
# ---------------------------------------------------------------------------

def test_market_now():
    """входим по рынку → MARKET_NOW / ONE_SHOT / MARKET leg"""
    text = "входим по рынку"
    marker = "входим по рынку"
    evidence = [
        _ev("MODIFY_ENTRY", "intent", marker, 0),
        _ev("MARKET_NOW", "modify_entry_mode", marker, 0),
    ]
    e = _extract_first(text, evidence)
    assert e.mode == "MARKET_NOW"
    assert e.entry_structure == "ONE_SHOT"
    assert len(e.entries) == 1
    assert e.entries[0].entry_type == "MARKET"
    assert e.entries[0].price is None


# ---------------------------------------------------------------------------
# REMOVE legacy
# ---------------------------------------------------------------------------

def test_remove_legacy():
    """убираем вход → REMOVE / no entries / no Pydantic error"""
    text = "убираем вход"
    marker = "убираем вход"
    evidence = [
        _ev("MODIFY_ENTRY", "intent", marker, 0),
        _ev("REMOVE", "modify_entry_mode", marker, 0),
    ]
    e = _extract_first(text, evidence)
    assert e.mode == "REMOVE"
    assert e.entries == []
    assert e.entry_structure is None


# ---------------------------------------------------------------------------
# Entry selector
# ---------------------------------------------------------------------------

def test_selector_primary():
    """основной вход переносим на 2114 → selector=PRIMARY / seq=1"""
    text = "основной вход переносим на 2114"
    intent_marker = "основной вход переносим"
    mode_marker = "основной вход переносим"
    selector_marker = "основной вход"
    evidence = [
        _ev("MODIFY_ENTRY", "intent", intent_marker, 0),
        _ev("UPDATE_PRICE", "modify_entry_mode", mode_marker, 0),
        _ev("PRIMARY", "entry_selector", selector_marker, 0),
    ]
    e = _extract_first(text, evidence)
    assert e.mode == "UPDATE_PRICE"
    assert e.entry_selector is not None
    assert e.entry_selector.role == "PRIMARY"
    assert e.entry_selector.sequence == 1
    assert e.entry_selector.raw == selector_marker
    assert e.entries[0].price.value == 2114.0


def test_selector_averaging():
    """усреднение переносим на 2114 → selector=AVERAGING"""
    text = "усреднение переносим на 2114"
    intent_marker = "усреднение переносим"
    mode_marker = "усреднение переносим"
    selector_marker = "усреднение"
    evidence = [
        _ev("MODIFY_ENTRY", "intent", intent_marker, 0),
        _ev("UPDATE_PRICE", "modify_entry_mode", mode_marker, 0),
        _ev("AVERAGING", "entry_selector", selector_marker, 0),
    ]
    e = _extract_first(text, evidence)
    assert e.entry_selector is not None
    assert e.entry_selector.role == "AVERAGING"
    assert e.entry_selector.sequence is None
    assert e.entries[0].price.value == 2114.0


def test_no_selector_when_absent():
    """новый вход 2114 (senza selector evidence) → entry_selector=None"""
    text = "новый вход 2114"
    marker = "новый вход"
    evidence = [
        _ev("MODIFY_ENTRY", "intent", marker, 0),
        _ev("UPDATE_PRICE", "modify_entry_mode", marker, 0),
    ]
    e = _extract_first(text, evidence)
    assert e.entry_selector is None


# ---------------------------------------------------------------------------
# Anti-collision: ADD_ENTRY e REENTER restano separati
# ---------------------------------------------------------------------------

def test_add_entry_not_modify_entry():
    """добавляю вход 2114 → intent ADD_ENTRY, non MODIFY_ENTRY"""
    text = "добавляю вход 2114"
    marker = "добавляю вход"
    evidence = [
        _ev("ADD_ENTRY", "intent", marker, 0),
    ]
    intents = _extractor.extract(_normalized(text), evidence)
    assert len(intents) == 1
    assert intents[0].type == "ADD_ENTRY"


def test_reenter_not_modify_entry():
    """перезаходим 2114 → intent REENTER, non MODIFY_ENTRY"""
    text = "перезаходим 2114"
    marker = "перезаходим"
    evidence = [
        _ev("REENTER", "intent", marker, 0),
    ]
    intents = _extractor.extract(_normalized(text), evidence)
    assert len(intents) == 1
    assert intents[0].type == "REENTER"


# ---------------------------------------------------------------------------
# Context window: prezzi di altro intent non contaminano MODIFY_ENTRY
# ---------------------------------------------------------------------------

def test_context_window_stops_at_next_intent():
    """MODIFY_ENTRY seguito da TP_HIT: i prezzi del TP non finiscono nelle entries"""
    # "новый вход 2114 тп 2200" — 2200 appartiene a TP_HIT
    text = "новый вход 2114 тп 2200"
    modify_marker = "новый вход"
    tp_marker = "тп"
    tp_start = text.index("тп")
    evidence = [
        _ev("MODIFY_ENTRY", "intent", modify_marker, 0),
        _ev("UPDATE_PRICE", "modify_entry_mode", modify_marker, 0),
        _ev("TP_HIT", "intent", tp_marker, tp_start),
    ]
    e = _extract_first(text, evidence)
    assert len(e.entries) == 1
    assert e.entries[0].price.value == 2114.0


# ---------------------------------------------------------------------------
# UNKNOWN mode (nessun mode evidence)
# ---------------------------------------------------------------------------

def test_unknown_mode_with_no_mode_evidence_but_price():
    """marker intent senza mode evidence + prezzo → mode=UPDATE_PRICE inferito"""
    text = "точка входа 2114"
    marker = "точка входа"
    evidence = [
        _ev("MODIFY_ENTRY", "intent", marker, 0, strength="weak"),
        # nessuna mode evidence
    ]
    e = _extract_first(text, evidence)
    assert e.mode == "UPDATE_PRICE"
    assert e.entries[0].price.value == 2114.0


def test_unknown_mode_with_no_price_stays_unknown():
    """marker intent senza mode evidence e senza prezzo → mode=UNKNOWN"""
    text = "точка входа"
    marker = "точка входа"
    evidence = [
        _ev("MODIFY_ENTRY", "intent", marker, 0, strength="weak"),
    ]
    e = _extract_first(text, evidence)
    assert e.mode == "UNKNOWN"
    assert e.entries == []
```

- [ ] **Step 2: Esegui i test per verificare che FALLISCANO**

```
pytest src/parser_v2/tests/test_modify_entry_extractor.py -v
```

Atteso: la maggior parte FAIL (il codice attuale non ha `entry_selector`, non fa range detection, ecc.). Alcuni potrebbero passare per caso (es. `test_add_entry_not_modify_entry`).

- [ ] **Step 3: Commit dei test**

```bash
git add src/parser_v2/tests/test_modify_entry_extractor.py
git commit -m "test(parser_v2): add failing tests for MODIFY_ENTRY extractor refactor"
```

---

## Task 7: Refactor `intent_entity_extractor.py`

**Files:**
- Modify: `src/parser_v2/profiles/trader_a/intent_entity_extractor.py`

- [ ] **Step 1: Aggiorna gli import all'inizio del file**

Sostituisci la sezione import corrente con:

```python
from __future__ import annotations

import re
from collections.abc import Callable

from src.parser_v2.contracts.entities import (
    AddEntryEntities,
    CancelPendingEntities,
    CloseFullEntities,
    ClosePartialEntities,
    EntryFilledEntities,
    EntryLeg,
    EntrySelector,
    ExitBeEntities,
    InfoOnlyEntities,
    InvalidateSetupEntities,
    ModifyEntryEntities,
    ModifyTargetsEntities,
    MoveStopEntities,
    MoveStopToBEEntities,
    Price,
    ReenterEntities,
    ReportResultEntities,
    SlHitEntities,
    TpHitEntities,
)
from src.parser_v2.contracts.enums import INTENT_CATEGORY_BY_TYPE, ModifyEntryMode, STRONG_WEIGHT, WEAK_WEIGHT
from src.parser_v2.contracts.markers import MarkerEvidence, NormalizedText
from src.parser_v2.contracts.parsed_message import ParsedIntent
```

- [ ] **Step 2: Rimuovi `_RE_MARKET_NOW` e `_RE_REMOVE`, aggiungi `_RANGE_RE`**

Rimuovi le righe:
```python
_RE_MARKET_NOW = re.compile(r"рынк|market", re.IGNORECASE)
_RE_REMOVE = re.compile(r"убира|remove|delete", re.IGNORECASE)
```

Aggiungi al loro posto:
```python
_RANGE_RE = re.compile(r"(?P<p1>\d[\d.,]*) *- *(?P<p2>\d[\d.,]*)")
```

- [ ] **Step 3: Aggiorna il dispatch in `extract()`**

Nel metodo `extract()`, sostituisci il blocco:
```python
builder = _ENTITY_BUILDERS.get(ev.name)
if builder is None:
    continue
confidence = STRONG_WEIGHT if ev.strength == "strong" else WEAK_WEIGHT
intents.append(
    ParsedIntent(
        type=ev.name,
        category=INTENT_CATEGORY_BY_TYPE[ev.name],
        confidence=confidence,
        entities=builder(ev, normalized),
        evidence=[ev],
        raw_fragment=ev.marker,
        span_start=ev.start,
        span_end=ev.end,
    )
)
```

Con:
```python
confidence = STRONG_WEIGHT if ev.strength == "strong" else WEAK_WEIGHT
if ev.name == "MODIFY_ENTRY":
    entities = _modify_entry_entities(ev, normalized, evidence)
else:
    builder = _ENTITY_BUILDERS.get(ev.name)
    if builder is None:
        continue
    entities = builder(ev, normalized)
intents.append(
    ParsedIntent(
        type=ev.name,
        category=INTENT_CATEGORY_BY_TYPE[ev.name],
        confidence=confidence,
        entities=entities,
        evidence=[ev],
        raw_fragment=ev.marker,
        span_start=ev.start,
        span_end=ev.end,
    )
)
```

- [ ] **Step 4: Sostituisci `_modify_entry_entities()` con la nuova implementazione**

Sostituisci la funzione esistente `_modify_entry_entities` con:

```python
def _modify_entry_entities(
    ev: MarkerEvidence,
    normalized: NormalizedText,
    all_evidence: list[MarkerEvidence],
) -> ModifyEntryEntities:
    text = normalized.normalized_text
    window = _modify_entry_context_window(ev, all_evidence, text)

    mode, raw_mode_marker = _detect_modify_entry_mode(ev, all_evidence)
    selector = _detect_entry_selector(ev, all_evidence)
    entries, entry_structure = _extract_modify_entry_prices(window, mode)

    # Upgrade mode dal price structure quando il marker non è esplicito
    if entry_structure == "RANGE" and mode in ("UPDATE_PRICE", "UNKNOWN"):
        mode = "UPDATE_RANGE"
    elif entries and mode == "UNKNOWN":
        mode = "UPDATE_PRICE"

    return ModifyEntryEntities(
        mode=mode,
        entry_selector=selector,
        entries=entries,
        entry_structure=entry_structure,
        raw_mode_marker=raw_mode_marker,
        raw_selector_marker=selector.raw if selector else None,
    )


def _modify_entry_context_window(
    ev: MarkerEvidence,
    all_evidence: list[MarkerEvidence],
    text: str,
) -> str:
    next_intent_start = min(
        (e.start for e in all_evidence if e.kind == "intent" and e.start > ev.end),
        default=len(text),
    )
    return text[ev.start:next_intent_start]


def _detect_modify_entry_mode(
    ev: MarkerEvidence,
    all_evidence: list[MarkerEvidence],
) -> tuple[ModifyEntryMode, str | None]:
    for e in all_evidence:
        if e.kind == "modify_entry_mode" and not e.suppressed:
            if _spans_overlap_or_adjacent(e, ev):
                return e.name, e.marker  # type: ignore[return-value]
    return "UNKNOWN", ev.marker


def _detect_entry_selector(
    ev: MarkerEvidence,
    all_evidence: list[MarkerEvidence],
) -> EntrySelector | None:
    for e in all_evidence:
        if e.kind == "entry_selector" and not e.suppressed:
            if _spans_overlap_or_adjacent(e, ev):
                role = e.name  # "PRIMARY" | "AVERAGING"
                seq = 1 if role == "PRIMARY" else None
                return EntrySelector(role=role, sequence=seq, raw=e.marker)  # type: ignore[arg-type]
    return None


def _extract_modify_entry_prices(
    window: str,
    mode: ModifyEntryMode,
) -> tuple[list[EntryLeg], str | None]:
    if mode == "MARKET_NOW":
        return [EntryLeg(sequence=1, entry_type="MARKET", role="PRIMARY")], "ONE_SHOT"
    if mode == "REMOVE":
        return [], None

    range_match = _RANGE_RE.search(window)
    if range_match:
        p1 = _price_from_raw(range_match.group("p1"))
        p2 = _price_from_raw(range_match.group("p2"))
        if p1 and p2:
            return (
                [
                    EntryLeg(sequence=1, entry_type="LIMIT", price=p1),
                    EntryLeg(sequence=2, entry_type="LIMIT", price=p2),
                ],
                "RANGE",
            )

    prices = _prices_in_window(window)
    if not prices:
        return [], None
    legs = [EntryLeg(sequence=i, entry_type="LIMIT", price=p) for i, p in enumerate(prices, 1)]
    structure = "ONE_SHOT" if len(legs) == 1 else "LADDER"
    return legs, structure


def _spans_overlap_or_adjacent(a: MarkerEvidence, b: MarkerEvidence, gap: int = 5) -> bool:
    return a.start <= b.end + gap and b.start <= a.end + gap


def _prices_in_window(window: str) -> list[Price]:
    return [p for m in _PRICE_RE.finditer(window) if (p := _price_from_raw(m.group(0)))]
```

- [ ] **Step 5: Esegui tutti i test**

```
pytest src/parser_v2/tests/test_modify_entry_extractor.py -v
```

Atteso: tutti PASS.

```
pytest src/parser_v2/tests/ -v -x -q
```

Atteso: tutti PASS (nessuna regressione).

- [ ] **Step 6: Commit**

```bash
git add src/parser_v2/profiles/trader_a/intent_entity_extractor.py
git commit -m "feat(trader_a): refactor _modify_entry_entities — evidence-based mode/selector, range/ladder support"
```

---

## Task 8: Aggiorna `canonical_translator.py` — propaga i nuovi campi

**Files:**
- Modify: `src/parser_v2/translation/canonical_translator.py`
- Modify: `src/parser_v2/tests/test_canonical_translator_v2.py`

- [ ] **Step 1: Scrivi i test fallenti per il translator**

Aggiungi in fondo a `src/parser_v2/tests/test_canonical_translator_v2.py`:

```python
def test_modify_entry_propagates_entry_selector_and_structure():
    from src.parser_v2.contracts.entities import EntryLeg, EntrySelector, ModifyEntryEntities, Price

    selector = EntrySelector(role="PRIMARY", sequence=1, raw="основной вход")
    entities = ModifyEntryEntities(
        mode="UPDATE_PRICE",
        entry_structure="ONE_SHOT",
        entry_selector=selector,
        entries=[EntryLeg(sequence=1, entry_type="LIMIT", price=Price(raw="2114", value=2114.0))],
    )
    intent = ParsedIntent(
        type="MODIFY_ENTRY",
        category="UPDATE",
        confidence=0.9,
        entities=entities,
        intent_id="MODIFY_ENTRY#0",
        occurrence_index=0,
    )
    parsed = _make_parsed([intent])
    result = CanonicalTranslator().translate(parsed)

    assert result.primary_class == "UPDATE"
    ops = result.update.operations
    assert len(ops) == 1
    me = ops[0].modify_entries
    assert me is not None
    assert me.kind == "UPDATE_PRICE"
    assert me.entry_structure == "ONE_SHOT"
    assert me.entry_selector is not None
    assert me.entry_selector.role == "PRIMARY"
    assert me.entry_selector.sequence == 1
    assert len(me.entries) == 1
    assert me.entries[0].price.value == 2114.0


def test_modify_entry_update_range_propagates():
    from src.parser_v2.contracts.entities import EntryLeg, ModifyEntryEntities
    from src.parser_v2.contracts.entities import Price as P

    entities = ModifyEntryEntities(
        mode="UPDATE_RANGE",
        entry_structure="RANGE",
        entries=[
            EntryLeg(sequence=1, entry_type="LIMIT", price=P(raw="2114", value=2114.0)),
            EntryLeg(sequence=2, entry_type="LIMIT", price=P(raw="2120", value=2120.0)),
        ],
    )
    intent = ParsedIntent(
        type="MODIFY_ENTRY",
        category="UPDATE",
        confidence=0.9,
        entities=entities,
        intent_id="MODIFY_ENTRY#0",
        occurrence_index=0,
    )
    parsed = _make_parsed([intent])
    result = CanonicalTranslator().translate(parsed)

    me = result.update.operations[0].modify_entries
    assert me.kind == "UPDATE_RANGE"
    assert me.entry_structure == "RANGE"
    assert len(me.entries) == 2
```

- [ ] **Step 2: Esegui i test per verificare che FALLISCANO**

```
pytest src/parser_v2/tests/test_canonical_translator_v2.py::test_modify_entry_propagates_entry_selector_and_structure -v
pytest src/parser_v2/tests/test_canonical_translator_v2.py::test_modify_entry_update_range_propagates -v
```

Atteso: FAIL — il translator attuale non propaga `entry_selector`.

- [ ] **Step 3: Aggiorna il ramo MODIFY_ENTRY nel translator**

In `src/parser_v2/translation/canonical_translator.py`, sostituisci il ramo `if intent.type == "MODIFY_ENTRY"` (attualmente righe 253-261):

```python
if intent.type == "MODIFY_ENTRY" and isinstance(entities, ModifyEntryEntities):
    return UpdateOperation(
        op_type="MODIFY_ENTRIES",
        modify_entries=ModifyEntriesOperation(
            kind=entities.mode,
            entries=entities.entries,
            entry_structure=entities.entry_structure,
            entry_selector=entities.entry_selector,
        ),
        source_intent=intent.type,
        source_intent_id=intent.intent_id,
        confidence=intent.confidence,
        raw_fragment=intent.raw_fragment,
    )
```

- [ ] **Step 4: Esegui tutti i test**

```
pytest src/parser_v2/tests/ -v -x -q
```

Atteso: tutti PASS.

- [ ] **Step 5: Commit finale**

```bash
git add src/parser_v2/translation/canonical_translator.py src/parser_v2/tests/test_canonical_translator_v2.py
git commit -m "feat(parser_v2): propagate entry_selector and entry_structure in MODIFY_ENTRY canonical translation"
```

---

## Verifica finale

- [ ] **Esegui suite completa**

```
pytest src/parser_v2/tests/ -v --tb=short
```

Atteso: tutti PASS, nessuna regressione.

- [ ] **Controlla che i test della checklist PRD siano coperti**

| Test PRD §18 | Test nel piano |
|---|---|
| `новый вход 2114` | `test_update_price_single_new_entry` |
| `вход теперь 2114` | `test_update_price_variant_vhod_teper` |
| `вход теперь 2114-2120` | `test_update_range_dash_separated` |
| `вход теперь 2114 2100 2080` | `test_ladder_three_prices` |
| `основной вход переносим на 2114` | `test_selector_primary` |
| `усреднение переносим на 2114` | `test_selector_averaging` |
| `входим по рынку` | `test_market_now` |
| `убираем вход` | `test_remove_legacy` |
| `добавляю вход 2114` | `test_add_entry_not_modify_entry` |
| `перезаходим 2114` | `test_reenter_not_modify_entry` |
| Signal con `вход с текущих` | coperto da classification layer (invariato) |
