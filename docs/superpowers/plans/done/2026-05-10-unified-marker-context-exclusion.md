# Unified Marker Context Exclusion (`marker_context_exclusions`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Aggiungere `marker_context_exclusions` come meccanismo unificato di esclusione contestuale per marker `weak` e `strong`, e correggere il `scope_hint` spurio nel `target_hints_extractor` (caso raw_message_id 1862: `ALL_SHORT` estratto dal p.s. invece che dall'azione principale).

**Architecture:** Il `MarkerEvidenceResolver` riceve una nuova lista `marker_context_exclusions` in `MarkerResolutionRules` che opera sia su marker weak che strong. Il vecchio `weak_context_exclusions` rimane invariato per backward compat. Il `TargetHintsExtractor` riceve logica separata per non propagare `scope_hint` dal testo quando strong telegram link targets sono già presenti.

**Tech Stack:** Python 3.12, Pydantic v2, pytest

---

## Nota architetturale importante

`marker_evidence_resolver.py` e `target_hints_extractor.py` sono due pipeline **indipendenti** in `runtime.py`. Sopprimere `ALL_SHORT` nel resolver NON influenza il `scope_hint` — quest'ultimo è prodotto da `_extract_scope_hint()` che scansiona il testo grezzo autonomamente. Servono due fix distinti: Tasks 1-3 per il contratto/resolver, Task 4 per il scope_hint.

---

## File map

| File | Azione |
|---|---|
| `src/parser_v2/contracts/rules.py` | Nuova classe `MarkerContextExclusionRule`, campo in `MarkerResolutionRules` |
| `src/parser_v2/core/marker_evidence_resolver.py` | Nuovo passo 2b + `_rule_markers_match_ctx` |
| `src/parser_v2/core/target_hints_extractor.py` | Guard: se telegram_message_ids presenti, scope_hint da GLOBAL_SCOPE non sovrascrive |
| `src/parser_v2/profiles/trader_a/rules.json` | Nuova sezione `marker_context_exclusions` |
| `src/parser_v2/tests/test_contracts_rules.py` | Test per `MarkerContextExclusionRule` |
| `src/parser_v2/tests/test_marker_evidence_resolver_weak_context.py` | Test strong exclusion + list marker_name + backward compat |
| `src/parser_v2/tests/test_target_hints_extractor_v2.py` | Test scope_hint non propagato se links presenti |

---

## Task 1: Aggiungere `MarkerContextExclusionRule` a `rules.py`

**Files:**
- Modify: `src/parser_v2/contracts/rules.py`
- Test: `src/parser_v2/tests/test_contracts_rules.py`

- [ ] **Step 1: Scrivi i test failing**

Aggiungi alla fine di `src/parser_v2/tests/test_contracts_rules.py`:

```python
from src.parser_v2.contracts.rules import MarkerContextExclusionRule  # aggiunge all'import esistente


def test_marker_context_exclusion_rule_strong_basic():
    rule = MarkerContextExclusionRule(
        name="all_short_ps",
        strength="strong",
        marker_name="ALL_SHORT",
        markers={"source": "semantic"},
        scope="same_sentence",
        if_contains_any=["p.s."],
        reason="scope_hint_in_postscript",
    )
    assert rule.strength == "strong"
    assert rule.marker_name == "ALL_SHORT"
    assert rule.reason == "scope_hint_in_postscript"


def test_marker_context_exclusion_rule_list_marker_name():
    rule = MarkerContextExclusionRule(
        name="be_context",
        strength="weak",
        marker_name=["EXIT_BE", "MOVE_STOP_TO_BE"],
        markers=["бу"],
        scope="same_sentence",
        if_contains_any=["фактически в бу"],
    )
    assert rule.marker_name == ["EXIT_BE", "MOVE_STOP_TO_BE"]


def test_marker_context_exclusion_rule_requires_condition():
    with pytest.raises(Exception):
        MarkerContextExclusionRule(
            name="invalid",
            strength="weak",
            marker_name="EXIT_BE",
            markers=["бу"],
            scope="same_sentence",
            # nessun if_contains_any né if_regex_any
        )


def test_marker_resolution_rules_has_marker_context_exclusions():
    rules = MarkerResolutionRules()
    assert rules.marker_context_exclusions == []


def test_marker_resolution_rules_accepts_marker_context_exclusion():
    rule = MarkerContextExclusionRule(
        name="test",
        strength="strong",
        marker_name="ALL_SHORT",
        markers=["по шортам"],
        scope="same_sentence",
        if_contains_any=["p.s."],
    )
    rules = MarkerResolutionRules(marker_context_exclusions=[rule])
    assert len(rules.marker_context_exclusions) == 1
```

- [ ] **Step 2: Esegui per verificare il fallimento**

```
pytest src/parser_v2/tests/test_contracts_rules.py -v -k "marker_context"
```

Expected: `ImportError: cannot import name 'MarkerContextExclusionRule'`

- [ ] **Step 3: Implementa `MarkerContextExclusionRule` in `rules.py`**

In `src/parser_v2/contracts/rules.py`, dopo la classe `WeakContextExclusionRule` (riga 56), aggiungi:

```python
class MarkerContextExclusionRule(RulesModel):
    name: str
    strength: MarkerStrength
    marker_name: Union[str, list[str]]
    markers: Union[list[str], dict[str, str]]
    scope: Literal["same_sentence", "same_line", "window", "whole_message"]
    window_chars: int | None = None
    if_contains_any: list[str] = Field(default_factory=list)
    if_regex_any: list[str] = Field(default_factory=list)
    unless_contains_any: list[str] = Field(default_factory=list)
    reason: str | None = None

    @model_validator(mode="after")
    def _require_at_least_one_condition(self) -> MarkerContextExclusionRule:
        if not self.if_contains_any and not self.if_regex_any:
            raise ValueError(
                f"MarkerContextExclusionRule '{self.name}' requires if_contains_any or if_regex_any"
            )
        return self
```

Poi aggiorna `MarkerResolutionRules` aggiungendo il nuovo campo:

```python
class MarkerResolutionRules(RulesModel):
    suppress_weak_inside_strong_same_intent: bool = False
    cross_intent_suppression: list[CrossIntentSuppressionRule] = Field(default_factory=list)
    weak_context_exclusions: list[WeakContextExclusionRule] = Field(default_factory=list)
    marker_context_exclusions: list[MarkerContextExclusionRule] = Field(default_factory=list)
```

Aggiungi `MarkerContextExclusionRule` agli import in testa al file (già presente `Union` — aggiungi solo la classe all'`__all__` se esiste, altrimenti basta la definizione).

- [ ] **Step 4: Esegui i test**

```
pytest src/parser_v2/tests/test_contracts_rules.py -v -k "marker_context"
```

Expected: tutti PASS

- [ ] **Step 5: Verifica regressione**

```
pytest src/parser_v2/tests/test_contracts_rules.py -v
```

Expected: tutti PASS (nessuna regressione su `weak_context_exclusions`)

- [ ] **Step 6: Commit**

```bash
git add src/parser_v2/contracts/rules.py src/parser_v2/tests/test_contracts_rules.py
git commit -m "feat(parser_v2): add MarkerContextExclusionRule for unified weak/strong context exclusion"
```

---

## Task 2: Aggiornare `marker_evidence_resolver.py`

**Files:**
- Modify: `src/parser_v2/core/marker_evidence_resolver.py`
- Test: `src/parser_v2/tests/test_marker_evidence_resolver_weak_context.py`

- [ ] **Step 1: Scrivi i test failing**

Aggiungi alla fine di `src/parser_v2/tests/test_marker_evidence_resolver_weak_context.py`:

```python
from src.parser_v2.contracts.rules import MarkerContextExclusionRule  # aggiunge all'import


def _make_match_kind(
    name: str, marker: str, strength: str, start: int, end: int, kind: str = "intent"
) -> MarkerMatch:
    return MarkerMatch(name=name, kind=kind, strength=strength, marker=marker, start=start, end=end)


def _make_rules_ctx(exclusions: list[MarkerContextExclusionRule]) -> ParserRules:
    return ParserRules(
        marker_resolution=MarkerResolutionRules(marker_context_exclusions=exclusions)
    )


def test_strong_marker_suppressed_by_context():
    text = "закрываю по текущим. p.s. у вас прибыль по шортам будет больше"
    marker_text = "по шортам"
    pos = text.find(marker_text)
    matches = [_make_match_kind("ALL_SHORT", marker_text, "strong", pos, pos + len(marker_text), kind="target_hint")]
    rule = MarkerContextExclusionRule(
        name="all_short_ps",
        strength="strong",
        marker_name="ALL_SHORT",
        markers=[marker_text],
        scope="same_sentence",
        if_contains_any=["p.s."],
        reason="scope_hint_in_postscript",
    )
    resolver = MarkerEvidenceResolver()
    result = resolver.resolve(matches, _make_rules_ctx([rule]), text=text)
    assert len(result.evidence) == 0
    assert len(result.suppressed_markers) == 1
    assert result.suppressed_markers[0].reason == "scope_hint_in_postscript"


def test_strong_marker_not_suppressed_if_condition_not_met():
    text = "закрываю по шортам по текущим"
    marker_text = "по шортам"
    pos = text.find(marker_text)
    matches = [_make_match_kind("ALL_SHORT", marker_text, "strong", pos, pos + len(marker_text), kind="target_hint")]
    rule = MarkerContextExclusionRule(
        name="all_short_ps",
        strength="strong",
        marker_name="ALL_SHORT",
        markers=[marker_text],
        scope="same_sentence",
        if_contains_any=["p.s."],
    )
    resolver = MarkerEvidenceResolver()
    result = resolver.resolve(matches, _make_rules_ctx([rule]), text=text)
    assert len(result.evidence) == 1
    assert len(result.suppressed_markers) == 0


def test_list_marker_name_suppresses_all_matching():
    text = "фактически в бу закрылись"
    pos = text.find("бу")
    matches = [
        _make_match("EXIT_BE", "бу", "weak", pos, pos + 2),
        _make_match("MOVE_STOP_TO_BE", "бу", "weak", pos, pos + 2),
    ]
    rule = MarkerContextExclusionRule(
        name="be_context",
        strength="weak",
        marker_name=["EXIT_BE", "MOVE_STOP_TO_BE"],
        markers=["бу"],
        scope="same_sentence",
        if_contains_any=["фактически в бу"],
        reason="be_false_positive",
    )
    resolver = MarkerEvidenceResolver()
    result = resolver.resolve(matches, _make_rules_ctx([rule]), text=text)
    assert len(result.evidence) == 0
    assert len(result.suppressed_markers) == 2
    assert all(m.reason == "be_false_positive" for m in result.suppressed_markers)


def test_marker_context_exclusion_does_not_affect_wrong_strength():
    text = "фактически в бу закрылись"
    pos = text.find("бу")
    matches = [_make_match("EXIT_BE", "бу", "strong", pos, pos + 2)]  # strong, non weak
    rule = MarkerContextExclusionRule(
        name="be_context_weak_only",
        strength="weak",  # regola per weak
        marker_name="EXIT_BE",
        markers=["бу"],
        scope="same_sentence",
        if_contains_any=["фактически в бу"],
    )
    resolver = MarkerEvidenceResolver()
    result = resolver.resolve(matches, _make_rules_ctx([rule]), text=text)
    assert len(result.evidence) == 1  # strong non soppresso


def test_backward_compat_weak_context_exclusions_unchanged():
    text = "после 1 тейка закрылась в бу"
    pos = text.find("тейка")
    matches = [_make_match("TP_HIT", "тейка", "weak", pos, pos + 5)]
    rule = WeakContextExclusionRule(
        name="tp_historical",
        intent="TP_HIT",
        markers=["тейка"],
        scope="same_sentence",
        if_contains_any=["после 1 тейка"],
        reason="historical_context",
    )
    rules = ParserRules(marker_resolution=MarkerResolutionRules(weak_context_exclusions=[rule]))
    resolver = MarkerEvidenceResolver()
    result = resolver.resolve(matches, rules, text=text)
    assert len(result.evidence) == 0
    assert result.suppressed_markers[0].reason == "historical_context"
```

- [ ] **Step 2: Esegui per verificare il fallimento**

```
pytest src/parser_v2/tests/test_marker_evidence_resolver_weak_context.py -v -k "strong_marker or list_marker or wrong_strength or backward_compat"
```

Expected: `AttributeError: 'MarkerResolutionRules' object has no attribute 'marker_context_exclusions'` o simile

- [ ] **Step 3: Aggiungi import e passo 2b nel resolver**

In `src/parser_v2/core/marker_evidence_resolver.py`:

**Import** — aggiorna la riga import da `rules`:
```python
from src.parser_v2.contracts.rules import ParserRules, WeakContextExclusionRule, MarkerContextExclusionRule
```

**Dopo il passo 2 esistente** (riga ~76, dopo il `break` del loop weak_context_exclusions), aggiungi il passo 2b:

```python
        # 2b. marker_context_exclusions (unified: weak + strong)
        if marker_resolution.marker_context_exclusions:
            if text is None:
                diagnostics_extra["marker_context_exclusions_skipped_no_text"] = [
                    r.name for r in marker_resolution.marker_context_exclusions
                ]
            else:
                for idx, match in enumerate(matches):
                    if idx in suppressed:
                        continue
                    for rule in marker_resolution.marker_context_exclusions:
                        target_names = (
                            rule.marker_name
                            if isinstance(rule.marker_name, list)
                            else [rule.marker_name]
                        )
                        if match.name not in target_names:
                            continue
                        if match.strength != rule.strength:
                            continue
                        if not _rule_markers_match_ctx(rule, match, semantic_markers):
                            continue
                        context_text = _extract_context(text, match.start, rule)
                        if _should_suppress_by_context(rule, context_text):
                            suppressed[idx] = _suppressed_evidence(
                                match,
                                suppressed_by=rule.name,
                                reason=rule.reason or "marker_context_exclusion",
                            )
                            _append_once(applied_rules, rule.name)
                            break
```

**Aggiungi funzione** `_rule_markers_match_ctx` dopo `_rule_markers_match` esistente:

```python
def _rule_markers_match_ctx(
    rule: MarkerContextExclusionRule,
    match: MarkerMatch,
    semantic_markers: SemanticMarkers | None,
) -> bool:
    markers = rule.markers
    if isinstance(markers, dict):
        source = markers.get("source")
        if source in ("semantic", "intent_weak", "intent_strong"):
            if semantic_markers is None:
                return True  # fallback: applica la regola
            kind_map: dict[str, dict] = {
                "intent": semantic_markers.intent_markers,
                "target_hint": semantic_markers.target_hint_markers,
                "side": semantic_markers.side_markers,
                "entry_type": semantic_markers.entry_type_markers,
            }
            marker_dict = kind_map.get(str(match.kind), {})
            marker_set = marker_dict.get(match.name)
            if marker_set is None:
                return True  # fallback: nessuna lista → applica
            pool = marker_set.strong if rule.strength == "strong" else marker_set.weak
            return match.marker in pool
    return match.marker in markers
```

Nota: `_extract_context` e `_should_suppress_by_context` esistenti funzionano già per duck typing — `MarkerContextExclusionRule` ha gli stessi campi `scope`, `window_chars`, `if_contains_any`, `if_regex_any`, `unless_contains_any`.

- [ ] **Step 4: Esegui i test nuovi**

```
pytest src/parser_v2/tests/test_marker_evidence_resolver_weak_context.py -v -k "strong_marker or list_marker or wrong_strength or backward_compat"
```

Expected: tutti PASS

- [ ] **Step 5: Verifica regressione completa**

```
pytest src/parser_v2/tests/test_marker_evidence_resolver_weak_context.py -v
```

Expected: tutti PASS

- [ ] **Step 6: Commit**

```bash
git add src/parser_v2/core/marker_evidence_resolver.py src/parser_v2/tests/test_marker_evidence_resolver_weak_context.py
git commit -m "feat(parser_v2): implement marker_context_exclusions in MarkerEvidenceResolver"
```

---

## Task 3: Aggiungere regola `all_short_in_ps_informational_context` a `trader_a/rules.json`

**Files:**
- Modify: `src/parser_v2/profiles/trader_a/rules.json`

- [ ] **Step 1: Aggiungi sezione `marker_context_exclusions`**

In `src/parser_v2/profiles/trader_a/rules.json`, aggiungi dopo `weak_context_exclusions` (prima di `"cross_intent_suppression"`):

```json
"marker_context_exclusions": [
  {
    "name": "all_short_in_ps_informational_context",
    "strength": "strong",
    "marker_name": "ALL_SHORT",
    "markers": {"source": "semantic"},
    "scope": "same_sentence",
    "if_contains_any": ["p.s.", "у вас прибыль по шортам"],
    "reason": "scope_hint_in_postscript_not_actionable"
  }
],
```

- [ ] **Step 2: Verifica che il profilo carichi senza errori**

```
python -c "from src.parser_v2.profiles.trader_a.profile import TraderAProfile; p = TraderAProfile(); r = p.load_rules(); print('marker_context_exclusions:', len(r.marker_resolution.marker_context_exclusions))"
```

Expected: `marker_context_exclusions: 1`

- [ ] **Step 3: Commit**

```bash
git add src/parser_v2/profiles/trader_a/rules.json
git commit -m "feat(trader_a): add strong context exclusion for ALL_SHORT in postscript context"
```

---

## Task 4: Fix `scope_hint` spurio nel `target_hints_extractor` (caso 1862)

**Contesto:** `_extract_scope_hint` in `target_hints_extractor.py` scansiona il testo completo indipendentemente dal `marker_evidence_resolver`. Quando il messaggio ha già `telegram_message_ids` forti (link espliciti a 4 signal), il `scope_hint` da testo è irrilevante e fuorviante. Fix: se `telegram_message_ids` è non vuoto, lo `scope_hint` da scansione testuale viene ignorato (rimane `UNKNOWN`).

**Files:**
- Modify: `src/parser_v2/core/target_hints_extractor.py`
- Test: `src/parser_v2/tests/test_target_hints_extractor_v2.py`

- [ ] **Step 1: Scrivi il test failing**

Cerca il file `src/parser_v2/tests/test_target_hints_extractor_v2.py` e aggiungi in fondo:

```python
def test_scope_hint_ignored_when_telegram_message_ids_present():
    """
    Se il messaggio ha telegram_message_ids (link forti a signal specifici),
    il scope_hint da testo non deve sovrascriverlo con ALL_SHORT o simili.
    """
    from src.parser_v2.contracts.context import ParserContext, RawContext
    from src.parser_v2.core.runtime import UniversalParserRuntime
    from src.parser_v2.profiles.trader_a.profile import TraderAProfile

    text = (
        "[trader#A]\n\n"
        "XRP - https://t.me/c/3171748254/822 3.94% прибыли\n"
        "ENA - https://t.me/c/3171748254/856 убыток 9.32\n"
        "LDO - https://t.me/c/3171748254/861 прибыль 4.2%\n"
        "SHIB - https://t.me/c/3171748254/870 прибыль 3.4%\n\n"
        "Эти монеты закрываю по текущим, так как нет времени за ними следить\n\n"
        "p.s. проценты указал без учета усреднения. "
        "кто выставлял лимитки на усреднение - у вас прибыль по шортам будет больше"
    )
    context = ParserContext(raw_context=RawContext(raw_text=text))
    result = UniversalParserRuntime().parse(text, context, TraderAProfile())

    # Deve avere 4 telegram_message_ids dai link
    assert result.target_hints is not None
    assert len(result.target_hints.telegram_message_ids) == 4

    # scope_hint NON deve essere ALL_SHORT — è un'informazione del p.s. non dell'azione
    assert result.target_hints.scope_hint != "ALL_SHORT"
```

- [ ] **Step 2: Esegui per verificare il fallimento**

```
pytest src/parser_v2/tests/test_target_hints_extractor_v2.py -v -k "scope_hint_ignored"
```

Expected: FAIL — `assert result.target_hints.scope_hint != "ALL_SHORT"` perché ora è `"ALL_SHORT"`

- [ ] **Step 3: Implementa il guard in `target_hints_extractor.py`**

In `src/parser_v2/core/target_hints_extractor.py`, righe 87-109. Sostituisci il blocco esistente:

```python
        scope_hint = _extract_scope_hint(normalized.normalized_text, markers)

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
```

Con:

```python
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
```

- [ ] **Step 4: Esegui il test**

```
pytest src/parser_v2/tests/test_target_hints_extractor_v2.py -v -k "scope_hint_ignored"
```

Expected: PASS

- [ ] **Step 5: Verifica regressione**

```
pytest src/parser_v2/tests/test_target_hints_extractor_v2.py -v
pytest src/parser_v2/tests/ -v --tb=short
```

Expected: tutti PASS

- [ ] **Step 6: Commit**

```bash
git add src/parser_v2/core/target_hints_extractor.py src/parser_v2/tests/test_target_hints_extractor_v2.py
git commit -m "fix(target_hints): do not propagate textual scope_hint when telegram link targets are present"
```

---

## Task 5: Run finale e verifica

- [ ] **Step 1: Esegui tutta la suite parser_v2**

```
pytest src/parser_v2/tests/ -v --tb=short
```

Expected: tutti PASS

- [ ] **Step 2: Esegui i test trader_a specifici**

```
pytest src/parser_v2/tests/test_trader_a_weak_context_rules.py -v
```

Expected: tutti PASS (nessuna regressione sulle regole esistenti)

- [ ] **Step 3: Commit finale di pulizia se necessario**

Se nessuna modifica residua:
```bash
git status  # verifica clean
```
