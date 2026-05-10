# Design: Unified Marker Context Exclusion (`marker_context_exclusions`)

**Date:** 2026-05-10  
**Status:** Draft  
**Scope:** `src/parser_v2/contracts/rules.py`, `src/parser_v2/core/marker_evidence_resolver.py`, `src/parser_v2/profiles/trader_a/rules.json`

---

## Problema

Il sistema attuale di esclusione contestuale dei marker (`weak_context_exclusions`) opera esclusivamente su marker di forza `"weak"`. Non esiste un meccanismo equivalente per sopprimere marker `"strong"` in contesti testuali che ne invalidano il significato.

Caso reale (raw_message_id 1862): il marker `ALL_SHORT/strong` viene estratto dalla frase informativa del postscript "у вас прибыль по шортам будет больше" e propagato come `scope_hint: ALL_SHORT` sull'azione `CLOSE_FULL`, che punta invece a 4 signal specifici via telegram link. Il marker è corretto sintatticamente ma sbagliato semanticamente in quel contesto.

---

## Soluzione: `marker_context_exclusions`

Sostituisce `weak_context_exclusions` con un campo unificato che supporta sia `"weak"` che `"strong"` tramite il parametro `strength`.

---

## Contratto — `MarkerContextExclusionRule`

```python
class MarkerContextExclusionRule(RulesModel):
    name: str
    strength: MarkerStrength                        # "weak" | "strong"
    marker_name: str | list[str]                    # nome/i del marker (match su match.name)
    markers: Union[list[str], dict[str, str]]       # filtro sul testo del marker
    scope: Literal["same_sentence", "same_line", "window", "whole_message"]
    window_chars: int | None = None
    if_contains_any: list[str] = Field(default_factory=list)
    if_regex_any: list[str] = Field(default_factory=list)
    unless_contains_any: list[str] = Field(default_factory=list)
    reason: str | None = None
```

### Campo `markers` — due modalità

| Valore | Comportamento |
|---|---|
| `{"source": "semantic"}` | Pesca da `semantic_markers` tutti i testi del `marker_name` con la `strength` della regola. Comportamento dipende da `match.kind` a runtime: `intent_markers`, `target_hint_markers`, `side_markers`, ecc. |
| `["testo1", "testo2"]` | Lista esplicita: la regola scatta solo se `match.marker` è uno di questi testi specifici. |

**Backward compat:** `{"source": "intent_weak"}` viene accettato come alias di `{"source": "semantic"}` con `strength: "weak"` e `kind: "intent"`. Zero migration obbligatoria sulle regole esistenti.

### Campo `marker_name` — singolo o lista

```json
"marker_name": "EXIT_BE"
"marker_name": ["EXIT_BE", "MOVE_STOP_TO_BE"]
```

Quando è una lista, la regola si applica se `match.name` è uno qualsiasi dei nomi listati. Consente di accorpare regole identiche per intent correlati.

---

## `MarkerResolutionRules` aggiornato

```python
class MarkerResolutionRules(RulesModel):
    suppress_weak_inside_strong_same_intent: bool = False
    cross_intent_suppression: list[CrossIntentSuppressionRule] = Field(default_factory=list)
    weak_context_exclusions: list[MarkerContextExclusionRule] = Field(default_factory=list)   # deprecato, alias
    marker_context_exclusions: list[MarkerContextExclusionRule] = Field(default_factory=list) # nuovo
```

`weak_context_exclusions` rimane accettato e viene unito a `marker_context_exclusions` durante la validazione (o direttamente nel resolver), per backward compat.

---

## Logica resolver — `marker_evidence_resolver.py`

Il passo 2 attuale:

```python
# 2. weak_context_exclusions — ATTUALE
for weak_index, weak_match in enumerate(matches):
    if weak_match.kind != "intent" or weak_match.strength != "weak":
        continue
    for rule in marker_resolution.weak_context_exclusions:
        ...
```

Diventa:

```python
# 2. marker_context_exclusions — NUOVO
all_exclusions = marker_resolution.marker_context_exclusions + marker_resolution.weak_context_exclusions
for idx, match in enumerate(matches):
    if idx in suppressed:
        continue
    if match.strength not in ("weak", "strong"):
        continue
    for rule in all_exclusions:
        target_names = rule.marker_name if isinstance(rule.marker_name, list) else [rule.marker_name]
        if match.name not in target_names:
            continue
        if match.strength != rule.strength:
            continue
        if not _rule_markers_match(rule, match, semantic_markers):
            continue
        context_text = _extract_context(text, match.start, rule)
        if _should_suppress_by_context(rule, context_text):
            suppressed[idx] = _suppressed_evidence(match, suppressed_by=rule.name, reason=rule.reason or "marker_context_exclusion")
            _append_once(applied_rules, rule.name)
            break
```

### `_rule_markers_match` aggiornato

```python
def _rule_markers_match(rule, match, semantic_markers):
    markers = rule.markers
    if isinstance(markers, dict):
        source = markers.get("source")
        if source in ("semantic", "intent_weak", "intent_strong"):
            # "intent_weak" / "intent_strong" = alias backward compat
            kind_map = {
                "intent": semantic_markers.intent_markers,
                "target_hint": semantic_markers.target_hint_markers,
                "side": semantic_markers.side_markers,
                "entry_type": semantic_markers.entry_type_markers,
            }
            marker_dict = kind_map.get(match.kind, {})
            marker_set = marker_dict.get(match.name)
            if marker_set is None:
                return True  # fallback: applica la regola
            pool = marker_set.strong if rule.strength == "strong" else marker_set.weak
            return match.marker in pool
    return match.marker in markers
```

---

## Migration delle regole esistenti

Le regole in `trader_a/rules.json` non richiedono modifica: `weak_context_exclusions` resta valido.

Per migrarle al nuovo formato (opzionale, graduale):

```json
// Prima
{
  "name": "exit_be_weak_context",
  "intent": "EXIT_BE",
  "markers": {"source": "intent_weak"},
  "scope": "same_sentence",
  "if_contains_any": ["в бу закрылись"]
}

// Dopo
{
  "name": "exit_be_weak_context",
  "strength": "weak",
  "marker_name": "EXIT_BE",
  "markers": {"source": "semantic"},
  "scope": "same_sentence",
  "if_contains_any": ["в бу закрылись"]
}
```

---

## Nuova regola per caso 1862

```json
{
  "name": "all_short_in_ps_informational_context",
  "strength": "strong",
  "marker_name": "ALL_SHORT",
  "markers": {"source": "semantic"},
  "scope": "same_sentence",
  "if_contains_any": ["p.s.", "у вас прибыль по шортам"],
  "reason": "scope_hint_in_postscript_not_actionable"
}
```

---

## Testing

1. Test unitari su `MarkerEvidenceResolver` per strong context exclusion
2. Test di regressione: tutte le regole `weak_context_exclusions` esistenti devono continuare a produrre lo stesso output
3. Test caso 1862: `scope_hint` non deve essere `ALL_SHORT` dopo la nuova regola

---

## File toccati

| File | Modifica |
|---|---|
| `src/parser_v2/contracts/rules.py` | Nuova classe `MarkerContextExclusionRule`, aggiornamento `MarkerResolutionRules` |
| `src/parser_v2/core/marker_evidence_resolver.py` | Passo 2 generalizzato, `_rule_markers_match` aggiornato |
| `src/parser_v2/profiles/trader_a/rules.json` | Aggiunta regola `all_short_in_ps_informational_context` in `marker_context_exclusions` |
| `src/parser_v2/tests/test_marker_evidence_resolver_weak_context.py` | Nuovi test per strong exclusion |
