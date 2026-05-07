# Occurrence Identity + Target Binding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Aggiungere identity delle occorrenze intent, weak context exclusions e target binding per riga a `parser_v2`, risolvendo le contraddizioni tra i due PRD precedenti.

**Architecture:** I contratti vengono estesi prima senza breaking changes (Task 1-4); i componenti core vengono modificati uno alla volta con TDD; un nuovo `TargetBindingResolver` viene inserito nel runtime tra `ParsedMessageBuilder` e `CanonicalTranslator`; il translator viene aggiornato per produrre una `TargetedAction` per ogni operation invece di PARTIAL.

**Tech Stack:** Python 3.12, Pydantic v2, pytest — nessuna nuova dipendenza.

**Spec:** `src/parser_v2/docs/PARSER_DA_ZERO_DOCS/DESIGN_OCCURRENCES_TARGET_BINDING.md`

---

## File map

| File | Azione | Responsabilità |
|------|--------|----------------|
| `src/parser_v2/contracts/enums.py` | Modifica | Aggiunge `TargetSource` |
| `src/parser_v2/contracts/context.py` | Modifica | Aggiunge `target_source` a `TargetHints`, aggiunge `TargetCandidate`, `TargetExtractionResult` |
| `src/parser_v2/contracts/parsed_message.py` | Modifica | Aggiunge `intent_id`, `occurrence_index`, `target_hints` a `ParsedIntent` |
| `src/parser_v2/contracts/canonical_message.py` | Modifica | Aggiunge `source_intent_id`, rinomina warning nel validator |
| `src/parser_v2/contracts/rules.py` | Modifica | Aggiunge `WeakContextExclusionRule`, campo in `MarkerResolutionRules` |
| `src/parser_v2/core/marker_evidence_resolver.py` | Modifica | Supporto `weak_context_exclusions` con `raw_text` |
| `src/parser_v2/core/local_disambiguator.py` | Modifica | Supporto campo `scope` nelle regole |
| `src/parser_v2/core/target_hints_extractor.py` | Modifica | Ritorna `TargetExtractionResult` con candidati posizionali |
| `src/parser_v2/core/parsed_message_builder.py` | Modifica | Aggiunge `_assign_occurrence_ids` |
| `src/parser_v2/core/target_binding_resolver.py` | Crea | Nuovo componente: binding riga-target-intent |
| `src/parser_v2/translation/canonical_translator.py` | Modifica | Multi-op su target globale, source_intent_id, dedup intents |
| `src/parser_v2/core/runtime.py` | Modifica | Integra `TargetBindingResolver` nel pipeline |

---

## Task 1: Contratti — TargetSource, TargetHints, TargetCandidate, TargetExtractionResult

**Files:**
- Modify: `src/parser_v2/contracts/enums.py`
- Modify: `src/parser_v2/contracts/context.py`
- Test: `src/parser_v2/tests/test_contracts_target.py`

- [ ] **Step 1: Scrivi il test che fallisce**

```python
# src/parser_v2/tests/test_contracts_target.py
from __future__ import annotations
import pytest
from src.parser_v2.contracts.enums import TargetSource
from src.parser_v2.contracts.context import TargetHints, TargetCandidate, TargetExtractionResult


def test_target_source_values():
    valid: list[TargetSource] = [
        "LOCAL_TEXT_LINK", "LOCAL_EXPLICIT_ID",
        "MESSAGE_TEXT_LINK", "MESSAGE_EXPLICIT_ID",
        "REPLY", "SYMBOL", "GLOBAL_SCOPE", "UNKNOWN",
    ]
    assert len(valid) == 8


def test_target_hints_has_target_source_default():
    hints = TargetHints()
    assert hints.target_source == "UNKNOWN"


def test_target_hints_target_source_persists():
    hints = TargetHints(target_source="REPLY", reply_to_message_id=42)
    assert hints.target_source == "REPLY"
    assert hints.reply_to_message_id == 42


def test_target_candidate_fields():
    candidate = TargetCandidate(
        source="MESSAGE_TEXT_LINK",
        value=111,
        start=5,
        end=30,
        line_index=0,
    )
    assert candidate.source == "MESSAGE_TEXT_LINK"
    assert candidate.value == 111
    assert candidate.line_index == 0


def test_target_extraction_result_structure():
    hints = TargetHints()
    result = TargetExtractionResult(message_target_hints=hints)
    assert result.candidates == []


def test_target_extraction_result_with_candidates():
    candidate = TargetCandidate(source="REPLY", value=100)
    result = TargetExtractionResult(
        message_target_hints=TargetHints(target_source="REPLY", reply_to_message_id=100),
        candidates=[candidate],
    )
    assert len(result.candidates) == 1
    assert result.candidates[0].source == "REPLY"
```

- [ ] **Step 2: Verifica che il test fallisca**

```
pytest src/parser_v2/tests/test_contracts_target.py -v
```
Atteso: `ImportError` o `AttributeError` — `TargetSource`, `TargetCandidate`, `TargetExtractionResult` non esistono.

- [ ] **Step 3: Aggiungi `TargetSource` a `enums.py`**

Nel file `src/parser_v2/contracts/enums.py`, dopo la riga `MarkerKind = Literal[...]`, aggiungere:

```python
TargetSource = Literal[
    "LOCAL_TEXT_LINK",
    "LOCAL_EXPLICIT_ID",
    "MESSAGE_TEXT_LINK",
    "MESSAGE_EXPLICIT_ID",
    "REPLY",
    "SYMBOL",
    "GLOBAL_SCOPE",
    "UNKNOWN",
]
```

- [ ] **Step 4: Estendi `context.py`**

In `src/parser_v2/contracts/context.py`:

1. Aggiungere import in cima:
```python
from typing import Any
```

2. Aggiungere import da enums:
```python
from .enums import ScopeHint, TargetSource
```
(sostituisce la riga `from .enums import ScopeHint`)

3. Aggiungere `target_source` a `TargetHints`:
```python
class TargetHints(ContextModel):
    target_source: TargetSource = "UNKNOWN"
    reply_to_message_id: int | None = None
    telegram_message_ids: list[int] = Field(default_factory=list)
    telegram_links: list[str] = Field(default_factory=list)
    explicit_ids: list[str] = Field(default_factory=list)
    symbols: list[str] = Field(default_factory=list)
    scope_hint: ScopeHint = "UNKNOWN"
```

4. Aggiungere dopo la classe `TargetHints`:
```python
class TargetCandidate(ContextModel):
    source: TargetSource
    value: Any
    start: int | None = None
    end: int | None = None
    line_index: int | None = None


class TargetExtractionResult(ContextModel):
    message_target_hints: TargetHints
    candidates: list[TargetCandidate] = Field(default_factory=list)
```

- [ ] **Step 5: Verifica che i test passino**

```
pytest src/parser_v2/tests/test_contracts_target.py -v
```
Atteso: tutti PASS.

- [ ] **Step 6: Commit**

```
git add src/parser_v2/contracts/enums.py src/parser_v2/contracts/context.py src/parser_v2/tests/test_contracts_target.py
git commit -m "feat(parser-v2): add TargetSource, TargetCandidate, TargetExtractionResult contracts"
```

---

## Task 2: Contratti — estendi `ParsedIntent`

**Files:**
- Modify: `src/parser_v2/contracts/parsed_message.py`
- Test: `src/parser_v2/tests/test_contracts_parsed_intent.py`

- [ ] **Step 1: Scrivi il test che fallisce**

```python
# src/parser_v2/tests/test_contracts_parsed_intent.py
from __future__ import annotations
import pytest
from src.parser_v2.contracts.context import TargetHints
from src.parser_v2.contracts.parsed_message import ParsedIntent


def _make_intent(**kwargs) -> ParsedIntent:
    defaults = {
        "type": "MOVE_STOP_TO_BE",
        "category": "UPDATE",
        "confidence": 0.9,
    }
    return ParsedIntent(**{**defaults, **kwargs})


def test_parsed_intent_has_intent_id():
    intent = _make_intent()
    assert intent.intent_id is None


def test_parsed_intent_has_occurrence_index():
    intent = _make_intent()
    assert intent.occurrence_index is None


def test_parsed_intent_has_target_hints():
    intent = _make_intent()
    assert intent.target_hints is None


def test_parsed_intent_stores_intent_id():
    intent = _make_intent(intent_id="MOVE_STOP_TO_BE#0", occurrence_index=0)
    assert intent.intent_id == "MOVE_STOP_TO_BE#0"
    assert intent.occurrence_index == 0


def test_parsed_intent_stores_target_hints():
    hints = TargetHints(target_source="LOCAL_TEXT_LINK", telegram_message_ids=[111])
    intent = _make_intent(target_hints=hints)
    assert intent.target_hints is not None
    assert intent.target_hints.telegram_message_ids == [111]
    assert intent.target_hints.target_source == "LOCAL_TEXT_LINK"
```

- [ ] **Step 2: Verifica che il test fallisca**

```
pytest src/parser_v2/tests/test_contracts_parsed_intent.py -v
```
Atteso: `ValidationError` o `AttributeError` — i tre nuovi campi non esistono.

- [ ] **Step 3: Aggiungi i campi a `ParsedIntent`**

In `src/parser_v2/contracts/parsed_message.py`, prima aggiungere l'import di `TargetHints`:

```python
from .context import RawContext, TargetHints
```
(sostituisce `from .context import RawContext, TargetHints` — già presente, verificare)

Poi modificare la classe `ParsedIntent` aggiungendo i tre campi dopo `span_end`:

```python
class ParsedIntent(ParsedModel):
    type: IntentType
    category: IntentCategory
    status: EvidenceStatus = "RESOLVED"
    confidence: float = Field(ge=0.0, le=1.0)
    entities: IntentEntities = Field(default_factory=IntentEntities)
    evidence: list[MarkerEvidence] = Field(default_factory=list)
    raw_fragment: str | None = None
    line_index: int | None = Field(default=None, ge=0)
    span_start: int | None = Field(default=None, ge=0)
    span_end: int | None = Field(default=None, ge=0)
    intent_id: str | None = None
    occurrence_index: int | None = None
    target_hints: TargetHints | None = None
```

- [ ] **Step 4: Verifica che i test passino**

```
pytest src/parser_v2/tests/test_contracts_parsed_intent.py -v
```
Atteso: tutti PASS.

- [ ] **Step 5: Commit**

```
git add src/parser_v2/contracts/parsed_message.py src/parser_v2/tests/test_contracts_parsed_intent.py
git commit -m "feat(parser-v2): add intent_id, occurrence_index, target_hints to ParsedIntent"
```

---

## Task 3: Contratti — `source_intent_id` e rename warning

**Files:**
- Modify: `src/parser_v2/contracts/canonical_message.py`
- Test: `src/parser_v2/tests/test_contracts_canonical.py`

- [ ] **Step 1: Scrivi il test che fallisce**

```python
# src/parser_v2/tests/test_contracts_canonical.py
from __future__ import annotations
import pytest
from src.parser_v2.contracts.canonical_message import UpdateOperation, TargetedAction
from src.parser_v2.contracts.context import TargetHints


def test_update_operation_has_source_intent_id():
    op = UpdateOperation(
        op_type="SET_STOP",
        set_stop={"target_type": "ENTRY"},
        source_intent="MOVE_STOP_TO_BE",
    )
    assert op.source_intent_id is None


def test_update_operation_stores_source_intent_id():
    from src.parser_v2.contracts.canonical_message import SetStopOperation
    op = UpdateOperation(
        op_type="SET_STOP",
        set_stop=SetStopOperation(target_type="ENTRY"),
        source_intent="MOVE_STOP_TO_BE",
        source_intent_id="MOVE_STOP_TO_BE#1",
    )
    assert op.source_intent_id == "MOVE_STOP_TO_BE#1"


def test_targeted_action_has_source_intent_id():
    action = TargetedAction(
        action_type="SET_STOP",
        target_hints=TargetHints(reply_to_message_id=100),
        source_intent="MOVE_STOP_TO_BE",
    )
    assert action.source_intent_id is None


def test_targeted_action_stores_source_intent_id():
    action = TargetedAction(
        action_type="SET_STOP",
        target_hints=TargetHints(reply_to_message_id=100),
        source_intent="MOVE_STOP_TO_BE",
        source_intent_id="MOVE_STOP_TO_BE#0",
    )
    assert action.source_intent_id == "MOVE_STOP_TO_BE#0"


def test_canonical_message_validator_uses_new_warning():
    from src.parser_v2.contracts.canonical_message import CanonicalMessage
    from src.parser_v2.contracts.context import RawContext
    # PARTIAL UPDATE senza payload deve accettare il nuovo warning
    msg = CanonicalMessage(
        parser_profile="test",
        primary_class="UPDATE",
        parse_status="PARTIAL",
        confidence=0.5,
        warnings=["ambiguous_target_intent_binding"],
        raw_context=RawContext(raw_text="test"),
    )
    assert "ambiguous_target_intent_binding" in msg.warnings


def test_canonical_message_validator_rejects_old_warning():
    from src.parser_v2.contracts.canonical_message import CanonicalMessage
    from src.parser_v2.contracts.context import RawContext
    # Il vecchio warning non deve più essere valido per UPDATE/PARTIAL senza payload
    with pytest.raises(Exception):
        CanonicalMessage(
            parser_profile="test",
            primary_class="UPDATE",
            parse_status="PARTIAL",
            confidence=0.5,
            warnings=["multi_ref_mixed_intents_not_supported"],
            raw_context=RawContext(raw_text="test"),
        )
```

- [ ] **Step 2: Verifica che il test fallisca**

```
pytest src/parser_v2/tests/test_contracts_canonical.py -v
```
Atteso: `AttributeError` su `source_intent_id` e l'ultimo test passa quando non dovrebbe (il vecchio warning è ancora accettato).

- [ ] **Step 3: Aggiungi `source_intent_id` a `UpdateOperation` e `TargetedAction`**

In `src/parser_v2/contracts/canonical_message.py`:

Modifica `UpdateOperation` aggiungendo dopo `source_intent`:
```python
source_intent_id: str | None = None
```

Modifica `TargetedAction` aggiungendo dopo `source_intent`:
```python
source_intent_id: str | None = None
```

- [ ] **Step 4: Rinomina il warning nel validator di `CanonicalMessage`**

Nel metodo `_validate_primary_class_payloads`, trovare:
```python
and "multi_ref_mixed_intents_not_supported" not in self.warnings
```
Sostituire con:
```python
and "ambiguous_target_intent_binding" not in self.warnings
```

- [ ] **Step 5: Verifica che i test passino**

```
pytest src/parser_v2/tests/test_contracts_canonical.py -v
```
Atteso: tutti PASS.

- [ ] **Step 6: Verifica che i test esistenti non siano rotti**

```
pytest src/parser_v2/ -v --tb=short
```
Atteso: nessun nuovo fallimento.

- [ ] **Step 7: Commit**

```
git add src/parser_v2/contracts/canonical_message.py src/parser_v2/tests/test_contracts_canonical.py
git commit -m "feat(parser-v2): add source_intent_id, rename warning to ambiguous_target_intent_binding"
```

---

## Task 4: Contratti — `WeakContextExclusionRule` in `rules.py`

**Files:**
- Modify: `src/parser_v2/contracts/rules.py`
- Test: `src/parser_v2/tests/test_contracts_rules.py`

- [ ] **Step 1: Scrivi il test che fallisce**

```python
# src/parser_v2/tests/test_contracts_rules.py
from __future__ import annotations
import pytest
from src.parser_v2.contracts.rules import WeakContextExclusionRule, MarkerResolutionRules


def test_weak_context_exclusion_rule_basic():
    rule = WeakContextExclusionRule(
        name="tp_historical",
        intent="TP_HIT",
        markers=["тейк", "тейка"],
        scope="same_sentence",
        if_contains_any=["после 1 тейка"],
    )
    assert rule.name == "tp_historical"
    assert rule.scope == "same_sentence"
    assert rule.markers == ["тейк", "тейка"]


def test_weak_context_exclusion_rule_with_source():
    rule = WeakContextExclusionRule(
        name="tp_future",
        intent="TP_HIT",
        markers={"source": "intent_weak"},
        scope="same_sentence",
        if_regex_any=["дойд[её]т\\s+до\\s+тейк"],
    )
    assert rule.markers == {"source": "intent_weak"}


def test_weak_context_exclusion_requires_condition():
    with pytest.raises(Exception):
        WeakContextExclusionRule(
            name="invalid",
            intent="TP_HIT",
            markers=["тейк"],
            scope="same_sentence",
            # niente if_contains_any né if_regex_any
        )


def test_marker_resolution_rules_has_weak_context_exclusions():
    rules = MarkerResolutionRules()
    assert rules.weak_context_exclusions == []


def test_marker_resolution_rules_with_exclusion():
    rule = WeakContextExclusionRule(
        name="tp_historical",
        intent="TP_HIT",
        markers=["тейк"],
        scope="same_sentence",
        if_contains_any=["после 1 тейка"],
    )
    rules = MarkerResolutionRules(weak_context_exclusions=[rule])
    assert len(rules.weak_context_exclusions) == 1
```

- [ ] **Step 2: Verifica che il test fallisca**

```
pytest src/parser_v2/tests/test_contracts_rules.py -v
```
Atteso: `ImportError` — `WeakContextExclusionRule` non esiste.

- [ ] **Step 3: Implementa `WeakContextExclusionRule` in `rules.py`**

In `src/parser_v2/contracts/rules.py`, aggiungere gli import necessari:
```python
from typing import Any, Union
from pydantic import BaseModel, ConfigDict, Field, model_validator
```
(aggiornare gli import esistenti)

Aggiungere la classe prima di `MarkerResolutionRules`:

```python
class WeakContextExclusionRule(RulesModel):
    name: str
    intent: IntentType
    markers: Union[list[str], dict[str, str]]
    scope: Literal["same_sentence", "same_line", "window", "whole_message"]
    window_chars: int | None = None
    if_contains_any: list[str] = Field(default_factory=list)
    if_regex_any: list[str] = Field(default_factory=list)
    unless_contains_any: list[str] = Field(default_factory=list)
    reason: str | None = None

    @model_validator(mode="after")
    def _require_at_least_one_condition(self) -> WeakContextExclusionRule:
        if not self.if_contains_any and not self.if_regex_any:
            raise ValueError(
                f"WeakContextExclusionRule '{self.name}' requires if_contains_any or if_regex_any"
            )
        return self
```

Aggiungere import `Literal` se non presente:
```python
from typing import Any, Literal, Union
```

Aggiungere `weak_context_exclusions` a `MarkerResolutionRules`:

```python
class MarkerResolutionRules(RulesModel):
    suppress_weak_inside_strong_same_intent: bool = False
    weak_context_exclusions: list[WeakContextExclusionRule] = Field(default_factory=list)
    cross_intent_suppression: list[CrossIntentSuppressionRule] = Field(default_factory=list)
```

- [ ] **Step 4: Verifica che i test passino**

```
pytest src/parser_v2/tests/test_contracts_rules.py -v
```
Atteso: tutti PASS.

- [ ] **Step 5: Commit**

```
git add src/parser_v2/contracts/rules.py src/parser_v2/tests/test_contracts_rules.py
git commit -m "feat(parser-v2): add WeakContextExclusionRule to MarkerResolutionRules"
```

---

## Task 5: `MarkerEvidenceResolver` — `weak_context_exclusions`

**Files:**
- Modify: `src/parser_v2/core/marker_evidence_resolver.py`
- Test: `src/parser_v2/tests/test_marker_evidence_resolver_weak_context.py`

- [ ] **Step 1: Scrivi il test che fallisce**

```python
# src/parser_v2/tests/test_marker_evidence_resolver_weak_context.py
from __future__ import annotations
import pytest
from src.parser_v2.contracts.markers import MarkerMatch
from src.parser_v2.contracts.rules import MarkerResolutionRules, ParserRules, WeakContextExclusionRule
from src.parser_v2.core.marker_evidence_resolver import MarkerEvidenceResolver


def _make_match(name: str, marker: str, strength: str, start: int, end: int) -> MarkerMatch:
    return MarkerMatch(name=name, kind="intent", strength=strength, marker=marker, start=start, end=end)


def _make_rules(exclusions: list[WeakContextExclusionRule]) -> ParserRules:
    return ParserRules(
        marker_resolution=MarkerResolutionRules(weak_context_exclusions=exclusions)
    )


RAW_TEXT_HISTORICAL = "Закрылась в бу, после 1 тейка, конечно же"


def test_weak_marker_suppressed_by_historical_context():
    # "тейка" appare nel contesto "после 1 тейка" → deve essere soppressa
    text = RAW_TEXT_HISTORICAL
    marker_pos = text.find("тейка")
    matches = [_make_match("TP_HIT", "тейка", "weak", marker_pos, marker_pos + 5)]
    rule = WeakContextExclusionRule(
        name="tp_historical",
        intent="TP_HIT",
        markers=["тейка"],
        scope="same_sentence",
        if_contains_any=["после 1 тейка"],
        reason="historical_context",
    )
    resolver = MarkerEvidenceResolver()
    result = resolver.resolve(matches, _make_rules([rule]), raw_text=text)
    assert len(result.evidence) == 0
    assert len(result.suppressed_markers) == 1
    assert result.suppressed_markers[0].reason == "historical_context"


def test_strong_marker_never_suppressed():
    text = "после 1 тейка второй тейк взят"
    strong_pos = text.find("тейк взят")
    matches = [_make_match("TP_HIT", "тейк взят", "strong", strong_pos, strong_pos + 9)]
    rule = WeakContextExclusionRule(
        name="tp_historical",
        intent="TP_HIT",
        markers=["тейк", "тейк взят"],
        scope="same_sentence",
        if_contains_any=["после 1 тейка"],
    )
    resolver = MarkerEvidenceResolver()
    result = resolver.resolve(matches, _make_rules([rule]), raw_text=text)
    assert len(result.evidence) == 1
    assert len(result.suppressed_markers) == 0


def test_unless_prevents_suppression():
    text = "после 1 тейка тейк взят"
    marker_pos = text.find("тейка")
    matches = [_make_match("TP_HIT", "тейка", "weak", marker_pos, marker_pos + 5)]
    rule = WeakContextExclusionRule(
        name="tp_historical",
        intent="TP_HIT",
        markers=["тейка"],
        scope="same_sentence",
        if_contains_any=["после 1 тейка"],
        unless_contains_any=["тейк взят"],
    )
    resolver = MarkerEvidenceResolver()
    result = resolver.resolve(matches, _make_rules([rule]), raw_text=text)
    assert len(result.evidence) == 1  # non soppresso per "unless"


def test_scope_same_line_only_affects_same_line():
    text = "после 1 тейка закрылась в бу.\n2 тейк взят."
    weak_pos = text.find("тейка")
    strong_pos = text.find("тейк взят")
    matches = [
        _make_match("TP_HIT", "тейка", "weak", weak_pos, weak_pos + 5),
        _make_match("TP_HIT", "тейк взят", "strong", strong_pos, strong_pos + 9),
    ]
    rule = WeakContextExclusionRule(
        name="tp_historical",
        intent="TP_HIT",
        markers=["тейка"],
        scope="same_line",
        if_contains_any=["после 1 тейка"],
    )
    resolver = MarkerEvidenceResolver()
    result = resolver.resolve(matches, _make_rules([rule]), raw_text=text)
    assert len(result.evidence) == 1
    assert result.evidence[0].marker == "тейк взят"


def test_no_raw_text_skips_exclusions_with_diagnostic():
    matches = [_make_match("TP_HIT", "тейка", "weak", 5, 10)]
    rule = WeakContextExclusionRule(
        name="tp_historical",
        intent="TP_HIT",
        markers=["тейка"],
        scope="same_sentence",
        if_contains_any=["после 1 тейка"],
    )
    resolver = MarkerEvidenceResolver()
    result = resolver.resolve(matches, _make_rules([rule]))  # nessun raw_text
    assert len(result.evidence) == 1  # non soppresso
    assert "weak_context_exclusions_skipped_no_text" in result.diagnostics
```

- [ ] **Step 2: Verifica che il test fallisca**

```
pytest src/parser_v2/tests/test_marker_evidence_resolver_weak_context.py -v
```
Atteso: `TypeError` — `resolve()` non accetta `raw_text`.

- [ ] **Step 3: Implementa `weak_context_exclusions` nel resolver**

Sostituire il contenuto di `src/parser_v2/core/marker_evidence_resolver.py` con:

```python
from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.parser_v2.contracts.markers import MarkerEvidence, MarkerMatch
from src.parser_v2.contracts.rules import ParserRules, WeakContextExclusionRule
from src.parser_v2.contracts.rules import SemanticMarkers


@dataclass(frozen=True)
class MarkerEvidenceResolution:
    evidence: list[MarkerEvidence]
    suppressed_markers: list[MarkerEvidence]
    diagnostics: dict[str, list[str]]


class MarkerEvidenceResolver:
    def resolve(
        self,
        matches: list[MarkerMatch],
        rules: ParserRules,
        *,
        raw_text: str | None = None,
        semantic_markers: SemanticMarkers | None = None,
    ) -> MarkerEvidenceResolution:
        suppressed: dict[int, MarkerEvidence] = {}
        applied_rules: list[str] = []
        diagnostics_extra: dict[str, list[str]] = {}

        marker_resolution = rules.marker_resolution

        # 1. suppress_weak_inside_strong_same_intent
        if marker_resolution.suppress_weak_inside_strong_same_intent:
            for weak_index, weak_match in enumerate(matches):
                if weak_match.kind != "intent" or weak_match.strength != "weak":
                    continue
                for strong_match in _iter_strong_intents(matches):
                    if (
                        weak_match.name == strong_match.name
                        and _contains(strong_match, weak_match)
                    ):
                        suppressed[weak_index] = _suppressed_evidence(
                            weak_match,
                            suppressed_by=strong_match.name,
                            reason="weak_inside_strong_same_intent",
                        )
                        _append_once(applied_rules, "weak_inside_strong_same_intent")
                        break

        # 2. weak_context_exclusions
        if marker_resolution.weak_context_exclusions:
            if raw_text is None:
                diagnostics_extra["weak_context_exclusions_skipped_no_text"] = [
                    r.name for r in marker_resolution.weak_context_exclusions
                ]
            else:
                for weak_index, weak_match in enumerate(matches):
                    if weak_index in suppressed:
                        continue
                    if weak_match.kind != "intent" or weak_match.strength != "weak":
                        continue
                    for rule in marker_resolution.weak_context_exclusions:
                        if rule.intent != weak_match.name:
                            continue
                        if not _rule_markers_match(rule, weak_match, semantic_markers):
                            continue
                        context_text = _extract_context(raw_text, weak_match.start, rule)
                        if _should_suppress_by_context(rule, context_text):
                            suppressed[weak_index] = _suppressed_evidence(
                                weak_match,
                                suppressed_by=rule.name,
                                reason=rule.reason or "weak_context_exclusion",
                            )
                            _append_once(applied_rules, rule.name)
                            break

        # 3. cross_intent_suppression
        for rule in marker_resolution.cross_intent_suppression:
            triggering_strong_matches = [
                match
                for match in _iter_strong_intents(matches)
                if match.name == rule.if_strong
            ]
            if not triggering_strong_matches:
                continue

            for weak_index, weak_match in enumerate(matches):
                if (
                    weak_index in suppressed
                    or weak_match.kind != "intent"
                    or weak_match.strength != "weak"
                    or weak_match.name not in rule.suppress_weak
                ):
                    continue

                for strong_match in triggering_strong_matches:
                    if _contains(strong_match, weak_match):
                        reason = rule.reason or "cross_intent_suppression"
                        suppressed[weak_index] = _suppressed_evidence(
                            weak_match,
                            suppressed_by=strong_match.name,
                            reason=reason,
                        )
                        _append_once(applied_rules, reason)
                        break

        evidence = [
            _clean_evidence(match)
            for index, match in enumerate(matches)
            if index not in suppressed
        ]
        suppressed_markers = [
            suppressed[index]
            for index in range(len(matches))
            if index in suppressed
        ]

        diagnostics: dict[str, list[str]] = {
            "suppressed_markers": [
                _format_marker(marker) for marker in suppressed_markers
            ],
            "applied_marker_rules": applied_rules,
            **diagnostics_extra,
        }

        return MarkerEvidenceResolution(
            evidence=evidence,
            suppressed_markers=suppressed_markers,
            diagnostics=diagnostics,
        )


def _rule_markers_match(
    rule: WeakContextExclusionRule,
    match: MarkerMatch,
    semantic_markers: SemanticMarkers | None,
) -> bool:
    markers = rule.markers
    if isinstance(markers, dict) and markers.get("source") == "intent_weak":
        if semantic_markers is None:
            return True  # fallback: applica la regola
        intent_marker_set = semantic_markers.intent_markers.get(match.name)
        if intent_marker_set is None:
            return False
        return match.marker in intent_marker_set.weak
    return match.marker in markers


def _extract_context(text: str, marker_start: int, rule: WeakContextExclusionRule) -> str:
    scope = rule.scope
    if scope == "whole_message":
        return text
    if scope == "same_line":
        line_start = text.rfind("\n", 0, marker_start)
        line_start = 0 if line_start == -1 else line_start + 1
        line_end = text.find("\n", marker_start)
        line_end = len(text) if line_end == -1 else line_end
        return text[line_start:line_end]
    if scope == "same_sentence":
        sentence_start = max(
            text.rfind(".", 0, marker_start),
            text.rfind("!", 0, marker_start),
            text.rfind("?", 0, marker_start),
            text.rfind("\n", 0, marker_start),
        )
        sentence_start = 0 if sentence_start == -1 else sentence_start + 1
        sentence_end_candidates = [
            pos for pos in [
                text.find(".", marker_start),
                text.find("!", marker_start),
                text.find("?", marker_start),
                text.find("\n", marker_start),
            ]
            if pos != -1
        ]
        sentence_end = min(sentence_end_candidates) if sentence_end_candidates else len(text)
        return text[sentence_start:sentence_end]
    if scope == "window":
        chars = rule.window_chars or 50
        return text[max(0, marker_start - chars): marker_start + chars]
    return text


def _should_suppress_by_context(rule: WeakContextExclusionRule, context: str) -> bool:
    condition_met = False
    if rule.if_contains_any:
        condition_met = any(phrase in context for phrase in rule.if_contains_any)
    if not condition_met and rule.if_regex_any:
        condition_met = any(re.search(pattern, context) for pattern in rule.if_regex_any)
    if not condition_met:
        return False
    if rule.unless_contains_any:
        if any(phrase in context for phrase in rule.unless_contains_any):
            return False
    return True


def _iter_strong_intents(matches: list[MarkerMatch]) -> list[MarkerMatch]:
    return [
        match
        for match in matches
        if match.kind == "intent" and match.strength == "strong"
    ]


def _contains(container: MarkerMatch, contained: MarkerMatch) -> bool:
    return container.start <= contained.start and contained.end <= container.end


def _clean_evidence(match: MarkerMatch) -> MarkerEvidence:
    return MarkerEvidence(
        name=match.name,
        kind=match.kind,
        strength=match.strength,
        marker=match.marker,
        start=match.start,
        end=match.end,
    )


def _suppressed_evidence(
    match: MarkerMatch,
    *,
    suppressed_by: str,
    reason: str,
) -> MarkerEvidence:
    return MarkerEvidence(
        name=match.name,
        kind=match.kind,
        strength=match.strength,
        marker=match.marker,
        start=match.start,
        end=match.end,
        suppressed=True,
        suppressed_by=suppressed_by,
        reason=reason,
    )


def _format_marker(marker: MarkerEvidence) -> str:
    return f"{marker.name}/{marker.strength}:{marker.marker}@{marker.start}:{marker.end}"


def _append_once(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)
```

- [ ] **Step 4: Verifica che i test passino**

```
pytest src/parser_v2/tests/test_marker_evidence_resolver_weak_context.py -v
```
Atteso: tutti PASS.

- [ ] **Step 5: Verifica suite completa**

```
pytest src/parser_v2/ -v --tb=short
```
Atteso: nessun nuovo fallimento.

- [ ] **Step 6: Commit**

```
git add src/parser_v2/core/marker_evidence_resolver.py src/parser_v2/tests/test_marker_evidence_resolver_weak_context.py
git commit -m "feat(parser-v2): implement weak_context_exclusions in MarkerEvidenceResolver"
```

---

## Task 6: `LocalDisambiguator` — campo `scope`

**Files:**
- Modify: `src/parser_v2/core/local_disambiguator.py`
- Test: `src/parser_v2/tests/test_local_disambiguator_scope.py`

- [ ] **Step 1: Scrivi il test che fallisce**

```python
# src/parser_v2/tests/test_local_disambiguator_scope.py
from __future__ import annotations
import pytest
from src.parser_v2.contracts.parsed_message import ParsedIntent
from src.parser_v2.contracts.rules import ParserRules
from src.parser_v2.core.local_disambiguator import LocalDisambiguator


def _make_intent(type_: str, span_start: int = 0, span_end: int = 10, line_index: int = 0, occurrence_index: int = 0) -> ParsedIntent:
    return ParsedIntent(
        type=type_,
        category="UPDATE",
        confidence=0.9,
        span_start=span_start,
        span_end=span_end,
        line_index=line_index,
        occurrence_index=occurrence_index,
        intent_id=f"{type_}#{occurrence_index}",
    )


def _make_rules(rules_list: list[dict]) -> ParserRules:
    return ParserRules(disambiguation=rules_list)


def test_scope_whole_message_suppresses_all_occurrences():
    # Comportamento attuale: whole_message rimuove tutti gli MOVE_STOP
    intents = [
        _make_intent("MOVE_STOP_TO_BE", span_start=0, span_end=5, line_index=0, occurrence_index=0),
        _make_intent("MOVE_STOP_TO_BE", span_start=10, span_end=15, line_index=1, occurrence_index=1),
        _make_intent("MOVE_STOP", span_start=0, span_end=5, line_index=0, occurrence_index=0),
    ]
    rule = {
        "name": "prefer_be_over_stop",
        "scope": "whole_message",
        "when_all_detected": ["MOVE_STOP_TO_BE", "MOVE_STOP"],
        "prefer": "MOVE_STOP_TO_BE",
        "over": ["MOVE_STOP"],
    }
    disambiguator = LocalDisambiguator()
    result = disambiguator.resolve(intents, _make_rules([rule]))
    types = [i.type for i in result.intents]
    assert "MOVE_STOP" not in types
    assert types.count("MOVE_STOP_TO_BE") == 2


def test_scope_same_span_only_suppresses_overlapping():
    # same_span: sopprime MOVE_STOP solo se sovrapposto a MOVE_STOP_TO_BE
    intents = [
        _make_intent("MOVE_STOP_TO_BE", span_start=0, span_end=10, line_index=0, occurrence_index=0),
        _make_intent("MOVE_STOP", span_start=2, span_end=8, line_index=0, occurrence_index=0),  # sovrapposto
        _make_intent("MOVE_STOP", span_start=20, span_end=30, line_index=1, occurrence_index=1),  # separato
    ]
    rule = {
        "name": "prefer_be_over_stop",
        "scope": "same_span",
        "when_all_detected": ["MOVE_STOP_TO_BE", "MOVE_STOP"],
        "prefer": "MOVE_STOP_TO_BE",
        "over": ["MOVE_STOP"],
    }
    disambiguator = LocalDisambiguator()
    result = disambiguator.resolve(intents, _make_rules([rule]))
    types = [i.type for i in result.intents]
    # Il MOVE_STOP separato (riga 1) deve restare
    assert types.count("MOVE_STOP") == 1
    assert result.intents[-1].line_index == 1


def test_scope_same_line_only_suppresses_same_line():
    intents = [
        _make_intent("MOVE_STOP_TO_BE", span_start=0, span_end=10, line_index=0, occurrence_index=0),
        _make_intent("MOVE_STOP", span_start=2, span_end=8, line_index=0, occurrence_index=0),  # stessa riga
        _make_intent("MOVE_STOP", span_start=20, span_end=30, line_index=1, occurrence_index=1),  # altra riga
    ]
    rule = {
        "name": "prefer_be_over_stop",
        "scope": "same_line",
        "when_all_detected": ["MOVE_STOP_TO_BE", "MOVE_STOP"],
        "prefer": "MOVE_STOP_TO_BE",
        "over": ["MOVE_STOP"],
    }
    disambiguator = LocalDisambiguator()
    result = disambiguator.resolve(intents, _make_rules([rule]))
    types = [i.type for i in result.intents]
    assert types.count("MOVE_STOP") == 1
    assert result.intents[-1].line_index == 1


def test_default_scope_is_whole_message():
    # Regola senza scope → comportamento esistente (whole_message)
    intents = [
        _make_intent("MOVE_STOP_TO_BE", line_index=0),
        _make_intent("MOVE_STOP", line_index=1),
    ]
    rule = {
        "name": "prefer_be_over_stop",
        # nessun campo scope
        "when_all_detected": ["MOVE_STOP_TO_BE", "MOVE_STOP"],
        "prefer": "MOVE_STOP_TO_BE",
        "over": ["MOVE_STOP"],
    }
    disambiguator = LocalDisambiguator()
    result = disambiguator.resolve(intents, _make_rules([rule]))
    types = [i.type for i in result.intents]
    assert "MOVE_STOP" not in types
```

- [ ] **Step 2: Verifica che il test fallisca**

```
pytest src/parser_v2/tests/test_local_disambiguator_scope.py -v
```
Atteso: `test_scope_same_span_only_suppresses_overlapping` e `test_scope_same_line_only_suppresses_same_line` falliscono (il disambiguator ignora lo scope e rimuove tutto).

- [ ] **Step 3: Aggiungi supporto `scope` al `LocalDisambiguator`**

In `src/parser_v2/core/local_disambiguator.py`, sostituire la funzione `_apply_prefer_suppress_rule`:

```python
def _apply_prefer_suppress_rule(
    active: list[ParsedIntent],
    suppressed: list[ParsedIntent],
    rule: dict[str, Any],
) -> bool:
    scope = rule.get("scope", "whole_message")
    remove_types: set[str] = set()
    action = _rule_action(rule)

    if action == "suppress":
        remove_types.update(rule.get("suppress") or [])
    else:
        prefer = rule.get("prefer")
        if prefer is None:
            return False
        over = rule.get("over")
        if over is None:
            over = [
                intent_type
                for intent_type in rule.get("when_all_detected", [])
                if intent_type != prefer
            ]
        remove_types.update(over)

    if scope == "whole_message":
        return _remove_types(active, suppressed, remove_types)

    # Trova gli intent "preferiti" per determinare il contesto
    prefer_type = rule.get("prefer")
    preferred_intents = [i for i in active if i.type == prefer_type] if prefer_type else []

    if not preferred_intents:
        return _remove_types(active, suppressed, remove_types)

    removed_any = False
    for preferred in preferred_intents:
        to_remove = [
            intent for intent in active
            if intent.type in remove_types
            and _scope_matches(preferred, intent, scope)
        ]
        for intent in to_remove:
            active.remove(intent)
            suppressed.append(intent)
            removed_any = True
    return removed_any


def _scope_matches(preferred: ParsedIntent, candidate: ParsedIntent, scope: str) -> bool:
    if scope == "same_span":
        if preferred.span_start is None or preferred.span_end is None:
            return False
        if candidate.span_start is None or candidate.span_end is None:
            return False
        # overlapping se uno contiene l'altro o si sovrappongono
        return not (candidate.span_end <= preferred.span_start or candidate.span_start >= preferred.span_end)
    if scope in ("same_line", "same_sentence", "same_target_group"):
        return preferred.line_index is not None and preferred.line_index == candidate.line_index
    return True  # fallback whole_message
```

- [ ] **Step 4: Verifica che i test passino**

```
pytest src/parser_v2/tests/test_local_disambiguator_scope.py -v
```
Atteso: tutti PASS.

- [ ] **Step 5: Verifica suite completa**

```
pytest src/parser_v2/ -v --tb=short
```

- [ ] **Step 6: Commit**

```
git add src/parser_v2/core/local_disambiguator.py src/parser_v2/tests/test_local_disambiguator_scope.py
git commit -m "feat(parser-v2): add scope support to LocalDisambiguator (same_span, same_line)"
```

---

## Task 7: `TargetHintsExtractor` — ritorna `TargetExtractionResult`

**Files:**
- Modify: `src/parser_v2/core/target_hints_extractor.py`
- Test: `src/parser_v2/tests/test_target_hints_extractor_v2.py`

- [ ] **Step 1: Scrivi il test che fallisce**

```python
# src/parser_v2/tests/test_target_hints_extractor_v2.py
from __future__ import annotations
import pytest
from src.parser_v2.contracts.context import ParserContext, RawContext, TargetExtractionResult
from src.parser_v2.contracts.markers import NormalizedText
from src.parser_v2.contracts.rules import SemanticMarkers
from src.parser_v2.core.target_hints_extractor import TargetHintsExtractor


def _extract(text: str, reply_id: int | None = None) -> TargetExtractionResult:
    raw_ctx = RawContext(raw_text=text, reply_to_message_id=reply_id)
    context = ParserContext(raw_context=raw_ctx, reply_to_message_id=reply_id)
    normalized = NormalizedText(raw_text=text, normalized_text=text.lower())
    return TargetHintsExtractor().extract(normalized, context, SemanticMarkers())


def test_extract_returns_extraction_result():
    result = _extract("стоп в бу")
    assert isinstance(result, TargetExtractionResult)


def test_extract_message_target_hints_preserved():
    result = _extract("стоп в бу", reply_id=100)
    assert result.message_target_hints.reply_to_message_id == 100


def test_extract_telegram_link_becomes_candidate_with_position():
    text = "https://t.me/c/777/111 стоп в бу"
    result = _extract(text)
    assert len(result.candidates) >= 1
    link_candidate = next(
        (c for c in result.candidates if c.source == "MESSAGE_TEXT_LINK"), None
    )
    assert link_candidate is not None
    assert link_candidate.value == 111
    assert link_candidate.start == 0
    assert link_candidate.line_index == 0


def test_extract_reply_becomes_candidate():
    result = _extract("стоп в бу", reply_id=100)
    reply_candidate = next(
        (c for c in result.candidates if c.source == "REPLY"), None
    )
    assert reply_candidate is not None
    assert reply_candidate.value == 100


def test_extract_multiline_links_have_correct_line_index():
    text = "https://t.me/c/777/111 стоп\nhttps://t.me/c/777/222 закрываю"
    result = _extract(text)
    link_candidates = [c for c in result.candidates if c.source == "MESSAGE_TEXT_LINK"]
    assert len(link_candidates) == 2
    line_indices = {c.value: c.line_index for c in link_candidates}
    assert line_indices[111] == 0
    assert line_indices[222] == 1


def test_extract_target_source_set_on_message_hints():
    text = "https://t.me/c/777/111 стоп"
    result = _extract(text)
    assert result.message_target_hints.target_source == "MESSAGE_TEXT_LINK"


def test_extract_reply_target_source_when_no_link():
    result = _extract("стоп в бу", reply_id=100)
    assert result.message_target_hints.target_source == "REPLY"
```

- [ ] **Step 2: Verifica che il test fallisca**

```
pytest src/parser_v2/tests/test_target_hints_extractor_v2.py -v
```
Atteso: `TypeError` — `extract()` ritorna `TargetHints`, non `TargetExtractionResult`.

- [ ] **Step 3: Riscrivi `target_hints_extractor.py`**

```python
# src/parser_v2/core/target_hints_extractor.py
from __future__ import annotations

import re
from collections.abc import Iterable
from typing import TypeVar

_T = TypeVar("_T")

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
    "SINGLE_SIGNAL", "SYMBOL", "ALL_LONG", "ALL_SHORT",
    "ALL_POSITIONS", "ALL_OPEN", "ALL_REMAINING",
}

# Priorità per target_source (indice minore = priorità maggiore)
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

        # Link Telegram nel testo con posizione
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

        # Reply
        reply_id = _reply_to_message_id(context)
        if reply_id is not None:
            candidates.append(TargetCandidate(source="REPLY", value=reply_id))

        # Explicit IDs
        explicit_ids = _dedup(_extract_explicit_ids(normalized.normalized_text))
        for eid in explicit_ids:
            candidates.append(TargetCandidate(source="MESSAGE_EXPLICIT_ID", value=eid))

        # Symbols
        symbols = _dedup(_extract_symbols(normalized.normalized_text, markers))
        for sym in symbols:
            candidates.append(TargetCandidate(source="SYMBOL", value=sym))

        # Scope hint
        scope_hint = _extract_scope_hint(normalized.normalized_text, markers)

        # Determina target_source del messaggio (priorità)
        target_source: TargetSource = "UNKNOWN"
        if message_ids:
            target_source = "MESSAGE_TEXT_LINK"
        elif explicit_ids:
            target_source = "MESSAGE_EXPLICIT_ID"
        elif reply_id is not None:
            target_source = "REPLY"
        elif symbols:
            target_source = "SYMBOL"
        elif scope_hint not in ("UNKNOWN", "SINGLE_SIGNAL"):
            target_source = "GLOBAL_SCOPE"

        message_target_hints = TargetHints(
            target_source=target_source,
            reply_to_message_id=reply_id,
            telegram_links=links,
            telegram_message_ids=message_ids,
            explicit_ids=explicit_ids,
            symbols=symbols,
            scope_hint=scope_hint,
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


def _extract_explicit_ids(text: str) -> Iterable[str]:
    for pattern in EXPLICIT_ID_PATTERNS:
        for match in pattern.finditer(text):
            yield match.group(1)


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
```

- [ ] **Step 4: Verifica che i test passino**

```
pytest src/parser_v2/tests/test_target_hints_extractor_v2.py -v
```
Atteso: tutti PASS.

- [ ] **Step 5: Verifica suite completa**

```
pytest src/parser_v2/ -v --tb=short
```

- [ ] **Step 6: Commit**

```
git add src/parser_v2/core/target_hints_extractor.py src/parser_v2/tests/test_target_hints_extractor_v2.py
git commit -m "feat(parser-v2): TargetHintsExtractor returns TargetExtractionResult with positional candidates"
```

---

## Task 8: `ParsedMessageBuilder` — assegna `occurrence_index` e `intent_id`

**Files:**
- Modify: `src/parser_v2/core/parsed_message_builder.py`
- Test: `src/parser_v2/tests/test_parsed_message_builder_occurrence.py`

- [ ] **Step 1: Scrivi il test che fallisce**

```python
# src/parser_v2/tests/test_parsed_message_builder_occurrence.py
from __future__ import annotations
import pytest
from src.parser_v2.contracts.context import ParserContext, RawContext
from src.parser_v2.contracts.parsed_message import ParsedIntent
from src.parser_v2.contracts.markers import NormalizedText
from src.parser_v2.core.parsed_message_builder import ParsedMessageBuilder


def _make_intent(type_: str) -> ParsedIntent:
    return ParsedIntent(type=type_, category="UPDATE", confidence=0.9)


def _build(intents: list[ParsedIntent]) -> list[ParsedIntent]:
    normalized = NormalizedText(raw_text="test", normalized_text="test")
    context = ParserContext(raw_context=RawContext(raw_text="test"))
    msg = ParsedMessageBuilder().build(
        parser_profile="test",
        normalized=normalized,
        context=context,
        intents=intents,
    )
    return msg.intents


def test_single_intent_gets_occurrence_index_zero():
    intents = _build([_make_intent("MOVE_STOP_TO_BE")])
    assert intents[0].occurrence_index == 0
    assert intents[0].intent_id == "MOVE_STOP_TO_BE#0"


def test_two_same_type_get_sequential_indices():
    intents = _build([
        _make_intent("MOVE_STOP_TO_BE"),
        _make_intent("MOVE_STOP_TO_BE"),
    ])
    assert intents[0].occurrence_index == 0
    assert intents[0].intent_id == "MOVE_STOP_TO_BE#0"
    assert intents[1].occurrence_index == 1
    assert intents[1].intent_id == "MOVE_STOP_TO_BE#1"


def test_different_types_each_start_at_zero():
    intents = _build([
        _make_intent("MOVE_STOP_TO_BE"),
        _make_intent("CANCEL_PENDING"),
        _make_intent("MOVE_STOP_TO_BE"),
    ])
    assert intents[0].intent_id == "MOVE_STOP_TO_BE#0"
    assert intents[1].intent_id == "CANCEL_PENDING#0"
    assert intents[2].intent_id == "MOVE_STOP_TO_BE#1"


def test_empty_intents_ok():
    intents = _build([])
    assert intents == []
```

- [ ] **Step 2: Verifica che il test fallisca**

```
pytest src/parser_v2/tests/test_parsed_message_builder_occurrence.py -v
```
Atteso: `AssertionError` — `occurrence_index` e `intent_id` sono `None`.

- [ ] **Step 3: Aggiungi `_assign_occurrence_ids` al builder**

In `src/parser_v2/core/parsed_message_builder.py`, aggiungere la funzione privata alla fine del file:

```python
def _assign_occurrence_ids(intents: list[ParsedIntent]) -> list[ParsedIntent]:
    counters: dict[str, int] = {}
    result: list[ParsedIntent] = []
    for intent in intents:
        idx = counters.get(intent.type, 0)
        counters[intent.type] = idx + 1
        result.append(intent.model_copy(update={
            "occurrence_index": idx,
            "intent_id": f"{intent.type}#{idx}",
        }))
    return result
```

Modificare il metodo `build()` — cambiare la riga:
```python
final_intents = intents or []
```
con:
```python
final_intents = _assign_occurrence_ids(intents or [])
```

- [ ] **Step 4: Verifica che i test passino**

```
pytest src/parser_v2/tests/test_parsed_message_builder_occurrence.py -v
```
Atteso: tutti PASS.

- [ ] **Step 5: Verifica suite completa**

```
pytest src/parser_v2/ -v --tb=short
```

- [ ] **Step 6: Commit**

```
git add src/parser_v2/core/parsed_message_builder.py src/parser_v2/tests/test_parsed_message_builder_occurrence.py
git commit -m "feat(parser-v2): ParsedMessageBuilder assigns intent_id and occurrence_index"
```

---

## Task 9: Crea `TargetBindingResolver`

**Files:**
- Create: `src/parser_v2/core/target_binding_resolver.py`
- Test: `src/parser_v2/tests/test_target_binding_resolver.py`

- [ ] **Step 1: Scrivi il test che fallisce**

```python
# src/parser_v2/tests/test_target_binding_resolver.py
from __future__ import annotations
import pytest
from src.parser_v2.contracts.context import (
    TargetCandidate, TargetExtractionResult, TargetHints,
)
from src.parser_v2.contracts.parsed_message import ParsedIntent
from src.parser_v2.core.target_binding_resolver import TargetBindingResolver


def _make_intent(type_: str, line_index: int = 0, occurrence_index: int = 0) -> ParsedIntent:
    return ParsedIntent(
        type=type_,
        category="UPDATE",
        confidence=0.9,
        line_index=line_index,
        occurrence_index=occurrence_index,
        intent_id=f"{type_}#{occurrence_index}",
    )


def _make_link_candidate(msg_id: int, line_index: int, start: int = 0) -> TargetCandidate:
    return TargetCandidate(
        source="MESSAGE_TEXT_LINK",
        value=msg_id,
        start=start,
        end=start + 25,
        line_index=line_index,
    )


def _make_extraction(
    candidates: list[TargetCandidate],
    reply_id: int | None = None,
    msg_ids: list[int] | None = None,
) -> TargetExtractionResult:
    target_source = "UNKNOWN"
    if msg_ids:
        target_source = "MESSAGE_TEXT_LINK"
    elif reply_id:
        target_source = "REPLY"
    hints = TargetHints(
        target_source=target_source,
        reply_to_message_id=reply_id,
        telegram_message_ids=msg_ids or [],
    )
    return TargetExtractionResult(message_target_hints=hints, candidates=candidates)


# --- Caso A: reply, nessun link nel testo ---

def test_reply_no_local_binding():
    intents = [_make_intent("MOVE_STOP_TO_BE", line_index=0)]
    extraction = _make_extraction(
        candidates=[TargetCandidate(source="REPLY", value=100)],
        reply_id=100,
    )
    result = TargetBindingResolver().bind(intents, extraction)
    assert result.message_target_hints.reply_to_message_id == 100
    assert result.message_target_hints.target_source == "REPLY"
    assert result.intents[0].target_hints is None


# --- Caso B: link globali (separati dagli intents) ---

def test_global_links_no_local_binding():
    # link su righe 0-1, intent su righe 2-3 → nessun binding locale
    intents = [
        _make_intent("MOVE_STOP_TO_BE", line_index=2, occurrence_index=0),
        _make_intent("CANCEL_PENDING", line_index=3, occurrence_index=0),
    ]
    extraction = _make_extraction(
        candidates=[
            _make_link_candidate(111, line_index=0),
            _make_link_candidate(222, line_index=1),
        ],
        msg_ids=[111, 222],
    )
    result = TargetBindingResolver().bind(intents, extraction)
    assert result.intents[0].target_hints is None
    assert result.intents[1].target_hints is None
    assert result.message_target_hints.telegram_message_ids == [111, 222]


# --- Caso C: link per riga con intent diversi ---

def test_line_level_one_to_one_binding():
    intents = [
        _make_intent("MOVE_STOP_TO_BE", line_index=0, occurrence_index=0),
        _make_intent("CLOSE_FULL", line_index=1, occurrence_index=0),
        _make_intent("CANCEL_PENDING", line_index=2, occurrence_index=0),
    ]
    extraction = _make_extraction(
        candidates=[
            _make_link_candidate(111, line_index=0),
            _make_link_candidate(222, line_index=1),
            _make_link_candidate(333, line_index=2),
        ],
        msg_ids=[111, 222, 333],
    )
    result = TargetBindingResolver().bind(intents, extraction)
    assert result.intents[0].target_hints.telegram_message_ids == [111]
    assert result.intents[0].target_hints.target_source == "LOCAL_TEXT_LINK"
    assert result.intents[1].target_hints.telegram_message_ids == [222]
    assert result.intents[2].target_hints.telegram_message_ids == [333]


# --- Caso D: stesso intent stesso link ---

def test_two_same_intents_two_links_binds_one_to_one():
    intents = [
        _make_intent("MOVE_STOP_TO_BE", line_index=0, occurrence_index=0),
        _make_intent("MOVE_STOP_TO_BE", line_index=1, occurrence_index=1),
    ]
    extraction = _make_extraction(
        candidates=[
            _make_link_candidate(111, line_index=0),
            _make_link_candidate(222, line_index=1),
        ],
        msg_ids=[111, 222],
    )
    result = TargetBindingResolver().bind(intents, extraction)
    assert result.intents[0].target_hints.telegram_message_ids == [111]
    assert result.intents[1].target_hints.telegram_message_ids == [222]


# --- Caso E: ambiguo ---

def test_ambiguous_binding_produces_partial_warning():
    intents = [
        _make_intent("MOVE_STOP_TO_BE", line_index=0, occurrence_index=0),
        _make_intent("CLOSE_FULL", line_index=0, occurrence_index=0),
    ]
    extraction = _make_extraction(
        candidates=[
            _make_link_candidate(111, line_index=0, start=0),
            _make_link_candidate(222, line_index=0, start=30),
        ],
        msg_ids=[111, 222],
    )
    result = TargetBindingResolver().bind(intents, extraction)
    assert "ambiguous_target_intent_binding" in result.warnings


# --- Caso F: link nel testo batte reply (priorità già nel TargetHintsExtractor) ---

def test_text_link_wins_over_reply():
    intents = [_make_intent("MOVE_STOP_TO_BE", line_index=0)]
    extraction = TargetExtractionResult(
        message_target_hints=TargetHints(
            target_source="MESSAGE_TEXT_LINK",  # il resolver ha già scelto
            reply_to_message_id=100,
            telegram_message_ids=[222],
        ),
        candidates=[
            TargetCandidate(source="REPLY", value=100),
            TargetCandidate(source="MESSAGE_TEXT_LINK", value=222, start=0, end=25, line_index=0),
        ],
    )
    result = TargetBindingResolver().bind(intents, extraction)
    # target globale deve essere il link, non il reply
    assert result.message_target_hints.target_source == "MESSAGE_TEXT_LINK"
    assert result.diagnostics.get("ignored_reply_to_message_id") == 100
```

- [ ] **Step 2: Verifica che il test fallisca**

```
pytest src/parser_v2/tests/test_target_binding_resolver.py -v
```
Atteso: `ImportError` — `TargetBindingResolver` non esiste.

- [ ] **Step 3: Crea `target_binding_resolver.py`**

```python
# src/parser_v2/core/target_binding_resolver.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.parser_v2.contracts.context import (
    TargetCandidate,
    TargetExtractionResult,
    TargetHints,
)
from src.parser_v2.contracts.parsed_message import ParsedIntent


@dataclass
class TargetBindingResult:
    intents: list[ParsedIntent]
    message_target_hints: TargetHints
    warnings: list[str] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)


class TargetBindingResolver:
    def bind(
        self,
        intents: list[ParsedIntent],
        extraction: TargetExtractionResult,
    ) -> TargetBindingResult:
        warnings: list[str] = []
        diagnostics: dict[str, Any] = {}

        message_hints = extraction.message_target_hints

        # Diagnostica: reply ignorato se link presente
        if (
            message_hints.target_source == "MESSAGE_TEXT_LINK"
            and message_hints.reply_to_message_id is not None
        ):
            diagnostics["ignored_reply_to_message_id"] = message_hints.reply_to_message_id

        # Candidati con posizione (solo quelli con line_index)
        positional = [c for c in extraction.candidates if c.line_index is not None]

        # Line-level binding
        bound_intents, line_warnings = _bind_line_level(intents, positional)
        warnings.extend(line_warnings)

        return TargetBindingResult(
            intents=bound_intents,
            message_target_hints=message_hints,
            warnings=warnings,
            diagnostics=diagnostics,
        )


def _bind_line_level(
    intents: list[ParsedIntent],
    positional_candidates: list[TargetCandidate],
) -> tuple[list[ParsedIntent], list[str]]:
    warnings: list[str] = []

    # Raggruppa per line_index
    candidates_by_line: dict[int, list[TargetCandidate]] = {}
    for c in positional_candidates:
        if c.line_index is not None:
            candidates_by_line.setdefault(c.line_index, []).append(c)

    intents_by_line: dict[int, list[int]] = {}  # line → [indici in intents]
    for idx, intent in enumerate(intents):
        if intent.line_index is not None:
            intents_by_line.setdefault(intent.line_index, []).append(idx)

    updated = list(intents)

    for line_idx, intent_indices in intents_by_line.items():
        line_candidates = candidates_by_line.get(line_idx, [])
        n_cands = len(line_candidates)
        n_intents = len(intent_indices)

        if n_cands == 0:
            continue

        if n_cands == 1:
            # Un solo candidato → si applica a tutti gli intent della riga
            hints = _hints_from_candidate(line_candidates[0])
            for i in intent_indices:
                updated[i] = updated[i].model_copy(update={"target_hints": hints})

        elif n_cands == n_intents:
            # N candidati, N intent → binding 1:1 per posizione (ordinati per start)
            sorted_cands = sorted(line_candidates, key=lambda c: c.start or 0)
            for i, cand in zip(intent_indices, sorted_cands):
                hints = _hints_from_candidate(cand)
                updated[i] = updated[i].model_copy(update={"target_hints": hints})

        elif n_intents == 1:
            # Più candidati, un solo intent → tutti i candidati al singolo intent
            all_ids = [c.value for c in line_candidates if isinstance(c.value, int)]
            hints = TargetHints(
                target_source="LOCAL_TEXT_LINK",
                telegram_message_ids=all_ids,
            )
            updated[intent_indices[0]] = updated[intent_indices[0]].model_copy(
                update={"target_hints": hints}
            )

        else:
            # N_cands != N_intents, entrambi > 1 → ambiguo (D11)
            warnings.append("ambiguous_target_intent_binding")

    return updated, warnings


def _hints_from_candidate(candidate: TargetCandidate) -> TargetHints:
    source = candidate.source
    local_source = (
        "LOCAL_TEXT_LINK" if source == "MESSAGE_TEXT_LINK"
        else "LOCAL_EXPLICIT_ID" if source == "MESSAGE_EXPLICIT_ID"
        else source
    )
    if isinstance(candidate.value, int) and source in ("MESSAGE_TEXT_LINK", "LOCAL_TEXT_LINK"):
        return TargetHints(
            target_source=local_source,
            telegram_message_ids=[candidate.value],
        )
    if isinstance(candidate.value, str) and source in ("MESSAGE_EXPLICIT_ID", "LOCAL_EXPLICIT_ID"):
        return TargetHints(
            target_source=local_source,
            explicit_ids=[candidate.value],
        )
    return TargetHints(target_source=local_source)
```

- [ ] **Step 4: Verifica che i test passino**

```
pytest src/parser_v2/tests/test_target_binding_resolver.py -v
```
Atteso: tutti PASS.

- [ ] **Step 5: Commit**

```
git add src/parser_v2/core/target_binding_resolver.py src/parser_v2/tests/test_target_binding_resolver.py
git commit -m "feat(parser-v2): add TargetBindingResolver with line-level intent-target binding"
```

---

## Task 10: `CanonicalTranslator` — logica aggiornata

**Files:**
- Modify: `src/parser_v2/translation/canonical_translator.py`
- Test: `src/parser_v2/tests/test_canonical_translator_v2.py`

- [ ] **Step 1: Scrivi il test che fallisce**

```python
# src/parser_v2/tests/test_canonical_translator_v2.py
from __future__ import annotations
import pytest
from src.parser_v2.contracts.context import ParserContext, RawContext, TargetHints
from src.parser_v2.contracts.markers import NormalizedText
from src.parser_v2.contracts.parsed_message import ParsedIntent, ParsedMessage
from src.parser_v2.contracts.entities import MoveStopToBEEntities, CancelPendingEntities
from src.parser_v2.translation.canonical_translator import CanonicalTranslator


def _raw_ctx() -> RawContext:
    return RawContext(raw_text="test")


def _make_parsed(
    intents: list[ParsedIntent],
    target_hints: TargetHints | None = None,
    parse_status: str = "PARSED",
    warnings: list[str] | None = None,
) -> ParsedMessage:
    return ParsedMessage(
        parser_profile="test",
        primary_class="UPDATE",
        parse_status=parse_status,
        confidence=0.9,
        intents=intents,
        target_hints=target_hints,
        warnings=warnings or [],
        raw_context=_raw_ctx(),
    )


def _make_intent(type_: str, occurrence_index: int = 0, target_hints: TargetHints | None = None) -> ParsedIntent:
    return ParsedIntent(
        type=type_,
        category="UPDATE",
        confidence=0.9,
        intent_id=f"{type_}#{occurrence_index}",
        occurrence_index=occurrence_index,
        target_hints=target_hints,
    )


# --- Caso B: multi-op su target globale → NON deve essere PARTIAL ---

def test_mixed_ops_on_global_target_produces_targeted_actions():
    intents = [
        _make_intent("MOVE_STOP_TO_BE", occurrence_index=0),
        _make_intent("CANCEL_PENDING", occurrence_index=0),
    ]
    global_hints = TargetHints(
        target_source="MESSAGE_TEXT_LINK",
        telegram_message_ids=[111, 222],
    )
    parsed = _make_parsed(intents, target_hints=global_hints)
    result = CanonicalTranslator().translate(parsed)

    assert result.parse_status == "PARSED"
    assert len(result.targeted_actions) == 2
    action_types = {a.action_type for a in result.targeted_actions}
    assert "SET_STOP" in action_types
    assert "CANCEL_PENDING" in action_types
    for action in result.targeted_actions:
        assert action.target_hints.telegram_message_ids == [111, 222]


def test_mixed_ops_no_partial_warning():
    intents = [
        _make_intent("MOVE_STOP_TO_BE", occurrence_index=0),
        _make_intent("CANCEL_PENDING", occurrence_index=0),
    ]
    parsed = _make_parsed(intents, target_hints=TargetHints(telegram_message_ids=[111]))
    result = CanonicalTranslator().translate(parsed)
    assert "multi_ref_mixed_intents_not_supported" not in result.warnings
    assert "ambiguous_target_intent_binding" not in result.warnings


# --- source_intent_id propagato ---

def test_source_intent_id_propagated():
    intents = [_make_intent("MOVE_STOP_TO_BE", occurrence_index=1)]
    parsed = _make_parsed(intents, target_hints=TargetHints(telegram_message_ids=[111]))
    result = CanonicalTranslator().translate(parsed)
    assert result.targeted_actions[0].source_intent_id == "MOVE_STOP_TO_BE#1"


# --- reply genera targeted_actions ---

def test_reply_generates_targeted_actions():
    intents = [_make_intent("MOVE_STOP_TO_BE", occurrence_index=0)]
    hints = TargetHints(target_source="REPLY", reply_to_message_id=100)
    parsed = _make_parsed(intents, target_hints=hints)
    result = CanonicalTranslator().translate(parsed)
    assert len(result.targeted_actions) == 1
    assert result.targeted_actions[0].target_hints.reply_to_message_id == 100


# --- target locale per intent ha priorità su globale ---

def test_per_intent_target_hints_override_global():
    local_hints = TargetHints(target_source="LOCAL_TEXT_LINK", telegram_message_ids=[111])
    global_hints = TargetHints(target_source="MESSAGE_TEXT_LINK", telegram_message_ids=[111, 222])
    intents = [_make_intent("MOVE_STOP_TO_BE", occurrence_index=0, target_hints=local_hints)]
    parsed = _make_parsed(intents, target_hints=global_hints)
    result = CanonicalTranslator().translate(parsed)
    assert result.targeted_actions[0].target_hints.telegram_message_ids == [111]


# --- CanonicalMessage.intents deduplicated ---

def test_intents_deduplicated_in_canonical():
    intents = [
        _make_intent("MOVE_STOP_TO_BE", occurrence_index=0),
        _make_intent("MOVE_STOP_TO_BE", occurrence_index=1),
    ]
    parsed = _make_parsed(intents, target_hints=TargetHints(telegram_message_ids=[111, 222]))
    result = CanonicalTranslator().translate(parsed)
    assert result.intents.count("MOVE_STOP_TO_BE") == 1


# --- Caso C: intent con target locali diversi ---

def test_line_level_intents_each_get_own_target():
    intents = [
        _make_intent("MOVE_STOP_TO_BE", occurrence_index=0,
                     target_hints=TargetHints(target_source="LOCAL_TEXT_LINK", telegram_message_ids=[111])),
        _make_intent("CLOSE_FULL", occurrence_index=0,
                     target_hints=TargetHints(target_source="LOCAL_TEXT_LINK", telegram_message_ids=[222])),
    ]
    parsed = _make_parsed(intents, target_hints=None)
    result = CanonicalTranslator().translate(parsed)
    assert len(result.targeted_actions) == 2
    ids = {a.action_type: a.target_hints.telegram_message_ids for a in result.targeted_actions}
    assert ids["SET_STOP"] == [111]
    assert ids["CLOSE"] == [222]
```

- [ ] **Step 2: Verifica che il test fallisca**

```
pytest src/parser_v2/tests/test_canonical_translator_v2.py -v
```
Atteso: `test_mixed_ops_on_global_target_produces_targeted_actions` e altri falliscono.

- [ ] **Step 3: Aggiorna `canonical_translator.py`**

Nel blocco `if parsed.primary_class == "UPDATE":` di `translate()`, sostituire tutto con:

```python
        if parsed.primary_class == "UPDATE":
            intent_op_pairs = [
                (intent, _operation_from_intent(intent))
                for intent in parsed.intents
                if intent.type in UPDATE_INTENTS
            ]
            intent_op_pairs = [(i, op) for i, op in intent_op_pairs if op is not None]

            has_any_local_target = any(
                i.target_hints is not None for i, _ in intent_op_pairs
            )
            use_targeted = (
                _should_use_targeted_actions(parsed.target_hints) or has_any_local_target
            )

            targeted_actions: list[TargetedAction] = []
            plain_operations: list[UpdateOperation] = []

            if use_targeted and intent_op_pairs:
                targeted_actions = [
                    _make_targeted_action(intent, op, parsed.target_hints)
                    for intent, op in intent_op_pairs
                ]
            else:
                plain_operations = [op for _, op in intent_op_pairs]

            if (
                not plain_operations
                and not targeted_actions
                and parse_status in {"PARSED", "PARTIAL"}
                and "ambiguous_target_intent_binding" not in warnings
            ):
                parse_status = "ERROR"
                warnings = _append_once(warnings, "canonical_translation_without_update_operation")

            return CanonicalMessage(
                parser_profile=parsed.parser_profile,
                primary_class=parsed.primary_class,
                parse_status=parse_status,
                confidence=parsed.confidence,
                primary_intent=parsed.primary_intent,
                intents=list(dict.fromkeys(intents)),
                update=UpdatePayload(operations=plain_operations),
                report=_report_payload(parsed.intents),
                targeted_actions=targeted_actions,
                target_hints=parsed.target_hints,
                warnings=warnings,
                diagnostics=parsed.diagnostics,
                raw_context=parsed.raw_context,
            )
```

Aggiungere la funzione `_make_targeted_action`:

```python
def _make_targeted_action(
    intent: ParsedIntent,
    op: UpdateOperation,
    message_target_hints: TargetHints | None,
) -> TargetedAction:
    resolved_hints = intent.target_hints or message_target_hints
    if resolved_hints is None:
        resolved_hints = TargetHints(scope_hint="SINGLE_SIGNAL")
    elif (
        resolved_hints.scope_hint == "UNKNOWN"
        and (resolved_hints.telegram_message_ids or resolved_hints.telegram_links or resolved_hints.explicit_ids)
    ):
        resolved_hints = resolved_hints.model_copy(update={"scope_hint": "SINGLE_SIGNAL"})

    return TargetedAction(
        action_type=op.op_type,
        params=_operation_params(op),
        target_hints=resolved_hints,
        source_intent=op.source_intent,
        source_intent_id=intent.intent_id,
        raw_fragment=op.raw_fragment,
        confidence=op.confidence,
    )
```

Aggiungere `reply_to_message_id` al check in `_should_use_targeted_actions`:

```python
def _should_use_targeted_actions(target_hints: TargetHints | None) -> bool:
    if target_hints is None:
        return False
    return bool(
        target_hints.telegram_message_ids
        or target_hints.telegram_links
        or target_hints.explicit_ids
        or target_hints.reply_to_message_id
        or target_hints.scope_hint in GLOBAL_SCOPE_HINTS
    )
```

Aggiornare `_operation_from_intent` per settare `source_intent_id` — aggiungere `source_intent_id=intent.intent_id` a ogni chiamata `UpdateOperation(...)`. Esempio per il primo caso:

```python
    if intent.type == "MOVE_STOP_TO_BE" and isinstance(entities, MoveStopToBEEntities):
        return UpdateOperation(
            op_type="SET_STOP",
            set_stop=SetStopOperation(target_type="ENTRY"),
            source_intent=intent.type,
            source_intent_id=intent.intent_id,
            confidence=intent.confidence,
            raw_fragment=intent.raw_fragment,
        )
```
(Ripetere per tutti i casi in `_operation_from_intent`.)

Aggiornare anche il blocco SIGNAL e REPORT per deduplica `intents`:
```python
intents=list(dict.fromkeys(intents)),
```
(sostituire `intents=intents` in tutti e tre i return finali del translator)

Rimuovere le funzioni `_targeted_actions_from_operations` e `_operation_signature` (non più necessarie).

- [ ] **Step 4: Verifica che i test passino**

```
pytest src/parser_v2/tests/test_canonical_translator_v2.py -v
```
Atteso: tutti PASS.

- [ ] **Step 5: Verifica suite completa**

```
pytest src/parser_v2/ -v --tb=short
```

- [ ] **Step 6: Commit**

```
git add src/parser_v2/translation/canonical_translator.py src/parser_v2/tests/test_canonical_translator_v2.py
git commit -m "feat(parser-v2): CanonicalTranslator supports multi-op global target, per-intent target, source_intent_id"
```

---

## Task 11: Runtime — integrazione `TargetBindingResolver`

**Files:**
- Modify: `src/parser_v2/core/runtime.py`
- Test: `src/parser_v2/tests/test_runtime_target_binding.py`

- [ ] **Step 1: Scrivi il test che fallisce**

```python
# src/parser_v2/tests/test_runtime_target_binding.py
from __future__ import annotations
import json
import pytest
from src.parser_v2.contracts.context import ParserContext, RawContext
from src.parser_v2.contracts.markers import NormalizedText, MarkerEvidence
from src.parser_v2.contracts.parsed_message import ParsedIntent, SignalDraft
from src.parser_v2.contracts.rules import ParserRules, SemanticMarkers
from src.parser_v2.core.runtime import UniversalParserRuntime, TraderParserProfile


class _MockProfile:
    trader_code = "mock"

    def load_markers(self) -> SemanticMarkers:
        return SemanticMarkers()

    def load_rules(self) -> ParserRules:
        return ParserRules()

    def extract_signal(self, text, context, evidence) -> None:
        return None

    def extract_intent_entities(self, text, context, evidence) -> list[ParsedIntent]:
        return self._intents

    def set_intents(self, intents: list[ParsedIntent]) -> None:
        self._intents = intents


def _run(text: str, profile: _MockProfile, reply_id: int | None = None):
    raw_ctx = RawContext(raw_text=text, reply_to_message_id=reply_id)
    context = ParserContext(raw_context=raw_ctx, reply_to_message_id=reply_id)
    return UniversalParserRuntime().parse(text, context, profile)


def test_runtime_passes_raw_text_to_resolver():
    # Il runtime deve passare il testo al MarkerEvidenceResolver
    # Verifica indiretto: nessun crash con raw_text
    profile = _MockProfile()
    profile.set_intents([])
    result = _run("test", profile)
    assert result is not None


def test_runtime_assigns_occurrence_ids():
    profile = _MockProfile()
    profile.set_intents([
        ParsedIntent(type="MOVE_STOP_TO_BE", category="UPDATE", confidence=0.9),
        ParsedIntent(type="MOVE_STOP_TO_BE", category="UPDATE", confidence=0.9),
    ])
    result = _run("стоп в бу\nстоп в бу", profile)
    # Le occorrenze devono avere intent_id distinti
    if result.update and result.update.operations:
        ids = [op.source_intent_id for op in result.update.operations]
        assert ids[0] != ids[1]


def test_runtime_with_reply_produces_targeted_actions():
    profile = _MockProfile()
    profile.set_intents([
        ParsedIntent(type="MOVE_STOP_TO_BE", category="UPDATE", confidence=0.9),
    ])
    result = _run("стоп в бу", profile, reply_id=100)
    assert len(result.targeted_actions) == 1
    assert result.targeted_actions[0].target_hints.reply_to_message_id == 100


def test_runtime_global_refs_two_ops_not_partial():
    profile = _MockProfile()
    profile.set_intents([
        ParsedIntent(type="MOVE_STOP_TO_BE", category="UPDATE", confidence=0.9),
        ParsedIntent(type="CANCEL_PENDING", category="UPDATE", confidence=0.9),
    ])
    text = "https://t.me/c/777/111\nhttps://t.me/c/777/222\nстоп в бу\nлимитки убираем"
    result = _run(text, profile)
    assert result.parse_status != "PARTIAL"
    assert len(result.targeted_actions) == 2
```

- [ ] **Step 2: Verifica che i test falliscano**

```
pytest src/parser_v2/tests/test_runtime_target_binding.py -v
```
Atteso: almeno `test_runtime_with_reply_produces_targeted_actions` e `test_runtime_global_refs_two_ops_not_partial` falliscono.

- [ ] **Step 3: Aggiorna `runtime.py`**

1. Aggiungere import:
```python
from src.parser_v2.contracts.context import TargetExtractionResult
from src.parser_v2.core.target_binding_resolver import TargetBindingResolver
```

2. Aggiungere `target_binding_resolver` all'`__init__` di `UniversalParserRuntime`:
```python
def __init__(
    self,
    *,
    text_normalizer: TextNormalizer | None = None,
    marker_matcher: MarkerMatcher | None = None,
    marker_evidence_resolver: MarkerEvidenceResolver | None = None,
    local_disambiguator: LocalDisambiguator | None = None,
    target_hints_extractor: TargetHintsExtractor | None = None,
    target_binding_resolver: TargetBindingResolver | None = None,
    parsed_message_builder: ParsedMessageBuilder | None = None,
    canonical_translator: CanonicalTranslator | None = None,
) -> None:
    self._text_normalizer = text_normalizer or TextNormalizer()
    self._marker_matcher = marker_matcher or MarkerMatcher()
    self._marker_evidence_resolver = marker_evidence_resolver or MarkerEvidenceResolver()
    self._local_disambiguator = local_disambiguator or LocalDisambiguator()
    self._target_hints_extractor = target_hints_extractor or TargetHintsExtractor()
    self._target_binding_resolver = target_binding_resolver or TargetBindingResolver()
    self._parsed_message_builder = parsed_message_builder or ParsedMessageBuilder()
    self._canonical_translator = canonical_translator or CanonicalTranslator()
```

3. Nel metodo `parse()`, modificare la chiamata al resolver per passare `raw_text`:
```python
evidence_resolution = self._marker_evidence_resolver.resolve(
    marker_matches,
    rules,
    raw_text=normalized.raw_text,
    semantic_markers=markers,
)
```

4. Modificare `_extract_target_hints` per ritornare `TargetExtractionResult`:
```python
def _extract_target_hints(
    self,
    normalized: NormalizedText,
    context: ParserContext,
    profile: TraderParserProfile,
    markers: SemanticMarkers,
) -> TargetExtractionResult:
    custom_extractor = getattr(profile, "extract_target_hints", None)
    if callable(custom_extractor):
        custom_hints = custom_extractor(normalized, context, markers)
        if custom_hints is not None:
            # Backwards compat: se ritorna TargetHints, wrapparlo
            from src.parser_v2.contracts.context import TargetHints
            if isinstance(custom_hints, TargetHints):
                return TargetExtractionResult(message_target_hints=custom_hints)
            return custom_hints

    return self._target_hints_extractor.extract(normalized, context, markers)
```

5. Nel metodo `parse()`, sostituire la sezione dal `target_hints = ...` fino al `parsed = self._parsed_message_builder.build(...)` con:

```python
        extraction = self._extract_target_hints(normalized, context, profile, markers)
        binding = self._target_binding_resolver.bind(
            disambiguation.intents,
            extraction,
        )

        build_warnings = _warnings_from_disambiguation(disambiguation.diagnostics)
        build_warnings = [*build_warnings, *binding.warnings]

        build_diagnostics: dict[str, Any] = {
            "suppressed_intents": disambiguation.diagnostics.get("suppressed_intents", []),
            **binding.diagnostics,
        }

        parsed = self._parsed_message_builder.build(
            parser_profile=profile.trader_code,
            normalized=normalized,
            context=context,
            signal=signal,
            intents=binding.intents,
            primary_intent=disambiguation.primary_intent,
            target_hints=binding.message_target_hints,
            matched_markers=marker_matches,
            suppressed_markers=evidence_resolution.suppressed_markers,
            applied_marker_rules=evidence_resolution.diagnostics.get("applied_marker_rules", []),
            applied_disambiguation_rules=disambiguation.diagnostics.get(
                "applied_disambiguation_rules", []
            ),
            warnings=build_warnings,
            diagnostics=build_diagnostics,
        )
```

Aggiungere `from typing import Any` se non presente.

- [ ] **Step 4: Verifica che i test passino**

```
pytest src/parser_v2/tests/test_runtime_target_binding.py -v
```
Atteso: tutti PASS.

- [ ] **Step 5: Verifica suite completa**

```
pytest src/parser_v2/ -v --tb=short
```
Atteso: tutti PASS.

- [ ] **Step 6: Commit**

```
git add src/parser_v2/core/runtime.py src/parser_v2/tests/test_runtime_target_binding.py
git commit -m "feat(parser-v2): wire TargetBindingResolver into UniversalParserRuntime"
```

---

## Task 12: Test di integrazione end-to-end

**Files:**
- Test: `src/parser_v2/tests/test_integration_design.py`

- [ ] **Step 1: Scrivi i test di integrazione**

```python
# src/parser_v2/tests/test_integration_design.py
"""
Test di integrazione che verificano i casi canonici del design doc.
Gruppi A (weak context), B (multiple occurrences), C (target binding), D (canonical intents).
"""
from __future__ import annotations
import pytest
from src.parser_v2.contracts.context import ParserContext, RawContext
from src.parser_v2.contracts.parsed_message import ParsedIntent
from src.parser_v2.contracts.rules import (
    ParserRules, MarkerResolutionRules, SemanticMarkers,
    WeakContextExclusionRule,
)
from src.parser_v2.core.runtime import UniversalParserRuntime


class _SimpleProfile:
    def __init__(self, intents: list[ParsedIntent], rules: ParserRules | None = None):
        self.trader_code = "test"
        self._intents = intents
        self._rules = rules or ParserRules()

    def load_markers(self) -> SemanticMarkers:
        return SemanticMarkers()

    def load_rules(self) -> ParserRules:
        return self._rules

    def extract_signal(self, text, context, evidence):
        return None

    def extract_intent_entities(self, text, context, evidence):
        return self._intents


def _run(text: str, profile: _SimpleProfile, reply_id: int | None = None):
    raw_ctx = RawContext(raw_text=text, reply_to_message_id=reply_id)
    context = ParserContext(raw_context=raw_ctx, reply_to_message_id=reply_id)
    return UniversalParserRuntime().parse(text, context, profile)


# ──── Gruppo B: multiple occurrences ────────────────────────────────────────

def test_B1_two_same_intents_preserved():
    """Due occorrenze dello stesso IntentType devono essere preservate."""
    intents = [
        ParsedIntent(type="MOVE_STOP_TO_BE", category="UPDATE", confidence=0.9),
        ParsedIntent(type="MOVE_STOP_TO_BE", category="UPDATE", confidence=0.9),
    ]
    result = _run("стоп в бу\nстоп в бу", _SimpleProfile(intents))
    assert result.update is not None or len(result.targeted_actions) >= 1
    # Le occorrenze hanno source_intent_id distinti
    all_ids = (
        [op.source_intent_id for op in result.update.operations]
        if result.update and result.update.operations
        else [a.source_intent_id for a in result.targeted_actions]
    )
    assert len(set(all_ids)) == 2  # due ID distinti


def test_B1_intents_in_canonical_deduplicated():
    """CanonicalMessage.intents non contiene duplicati."""
    intents = [
        ParsedIntent(type="MOVE_STOP_TO_BE", category="UPDATE", confidence=0.9),
        ParsedIntent(type="MOVE_STOP_TO_BE", category="UPDATE", confidence=0.9),
    ]
    result = _run("стоп в бу\nстоп в бу", _SimpleProfile(intents))
    assert result.intents.count("MOVE_STOP_TO_BE") == 1


# ──── Gruppo C: target binding ───────────────────────────────────────────────

def test_C1_reply_applies_to_multiple_operations():
    """Reply + due intent → entrambe le operations sul reply."""
    intents = [
        ParsedIntent(type="MOVE_STOP_TO_BE", category="UPDATE", confidence=0.9),
        ParsedIntent(type="CANCEL_PENDING", category="UPDATE", confidence=0.9),
    ]
    result = _run("стоп в бу\nлимитки убираем", _SimpleProfile(intents), reply_id=100)
    assert len(result.targeted_actions) == 2
    for action in result.targeted_actions:
        assert action.target_hints.reply_to_message_id == 100


def test_C3_global_ref_list_multiple_ops_not_partial():
    """Link globali + ops diverse → PARSED, non PARTIAL."""
    intents = [
        ParsedIntent(type="MOVE_STOP_TO_BE", category="UPDATE", confidence=0.9),
        ParsedIntent(type="CANCEL_PENDING", category="UPDATE", confidence=0.9),
    ]
    text = "https://t.me/c/777/111\nhttps://t.me/c/777/222\nстоп в бу\nлимитки убираем"
    result = _run(text, _SimpleProfile(intents))
    assert result.parse_status == "PARSED"
    assert len(result.targeted_actions) == 2
    for action in result.targeted_actions:
        assert 111 in action.target_hints.telegram_message_ids
        assert 222 in action.target_hints.telegram_message_ids


# ──── Gruppo D: canonical intents ────────────────────────────────────────────

def test_D2_different_types_not_deduplicated():
    """MOVE_STOP_TO_BE + CANCEL_PENDING → entrambi in intents."""
    intents = [
        ParsedIntent(type="MOVE_STOP_TO_BE", category="UPDATE", confidence=0.9),
        ParsedIntent(type="CANCEL_PENDING", category="UPDATE", confidence=0.9),
    ]
    result = _run("стоп в бу\nлимитки убираем", _SimpleProfile(intents))
    assert "MOVE_STOP_TO_BE" in result.intents
    assert "CANCEL_PENDING" in result.intents
```

- [ ] **Step 2: Esegui i test di integrazione**

```
pytest src/parser_v2/tests/test_integration_design.py -v
```
Atteso: tutti PASS.

- [ ] **Step 3: Esegui la suite completa**

```
pytest src/parser_v2/ -v
```
Atteso: tutti PASS, nessuna regressione.

- [ ] **Step 4: Commit finale**

```
git add src/parser_v2/tests/test_integration_design.py
git commit -m "test(parser-v2): add integration tests for occurrence identity and target binding"
```

---

## Self-review

**Spec coverage:**
- ✅ §3.1 TargetSource → Task 1
- ✅ §3.2 TargetHints.target_source → Task 1
- ✅ §3.3 ParsedIntent fields → Task 2
- ✅ §3.4 source_intent_id → Task 3
- ✅ §3.5 WeakContextExclusionRule → Task 4
- ✅ §3.6 validator rename → Task 3
- ✅ §4 TargetCandidate + TargetExtractionResult → Task 1
- ✅ §5.1 TargetHintsExtractor → Task 7
- ✅ §5.2 MarkerEvidenceResolver weak_context → Task 5
- ✅ §5.3 LocalDisambiguator scope → Task 6
- ✅ §5.4 ParsedMessageBuilder occurrence ids → Task 8
- ✅ §6 TargetBindingResolver → Task 9
- ✅ §7 CanonicalTranslator → Task 10
- ✅ §9 Pipeline runtime → Task 11
- ✅ §11 Test gruppi A (parziale), B, C, D → Tasks 5, 12

**Nota:** I test del Gruppo A (weak_context_exclusions end-to-end) richiedono un profilo con marker configurati in `semantic_markers.json`. Sono coperti dai test unitari in Task 5. Un test di integrazione end-to-end completo richiederebbe il profilo `trader_a` con le regole reali — da aggiungere separatamente dopo aver configurato le regole nel profilo.

**Tipo consistency:** `TargetExtractionResult`, `TargetCandidate`, `TargetBindingResult` usano nomi coerenti in tutti i task. `intent_id` è sempre `str | None`, `occurrence_index` è sempre `int | None`. `source_intent_id` è aggiunto sia a `UpdateOperation` che a `TargetedAction`.
