# DESIGN — parser_v2: occurrence identity, weak context exclusions, target binding

**Data:** 2026-05-07
**Sostituisce:** PRD_PARSER_V2_MARKER_RESOLUTION_OCCURRENCES_UPDATED.md + PRD_PARSER_V2_TARGET_SCOPE_MULTI_INTENT.md
**Stato:** decisioni approvate, pronto per implementazione

---

## 1. Scopo

Questo documento consolida i due PRD precedenti e risolve le loro contraddizioni tramite 11 decisioni esplicite. Descrive le modifiche necessarie a contratti, componenti e pipeline di `parser_v2` per supportare:

1. Più occorrenze dello stesso `IntentType` nello stesso messaggio
2. Esclusione contestuale di marker deboli (`weak_context_exclusions`)
3. Target binding per occorrenza (riga-level)
4. Gestione corretta di multi-intent su target globale
5. Priorità target esplicita (link > reply)

---

## 2. Decisioni

| # | Decisione |
|---|-----------|
| D1 | Target binding per occorrenza → `TargetBindingResolver` separato nel runtime |
| D2 | Mixed ops su target globale → una `TargetedAction` per operation, niente PARTIAL |
| D3 | `LocalDisambiguator` → campo `scope` nelle regole, default `whole_message` |
| D4 | `TargetHints.target_source: TargetSource = "UNKNOWN"` con default |
| D5 | `TargetHintsExtractor` → doppio output: `message_target_hints` + `candidates[]` |
| D6 | `weak_context_exclusions` → `raw_text` opzionale al `MarkerEvidenceResolver` |
| D7 | Warning → rinomina immediata a `ambiguous_target_intent_binding` |
| D8 | `CanonicalMessage.intents` → deduplicated, lista di tipi presenti |
| D9 | `intent_id` / `occurrence_index` → assegnati dal `ParsedMessageBuilder` |
| D10 | Priorità target → link nel testo vince sul reply, reply ignorato in diagnostics |
| D11 | Binding ambiguo → `N_links != N_intents` sulla stessa riga |

---

## 3. Modifiche contrattuali

### 3.1 `contracts/enums.py` — aggiungere `TargetSource`

```python
TargetSource = Literal[
    "LOCAL_TEXT_LINK",      # link nella stessa riga dell'intent
    "LOCAL_EXPLICIT_ID",    # explicit id nella stessa riga dell'intent
    "MESSAGE_TEXT_LINK",    # link nel testo, scope globale messaggio
    "MESSAGE_EXPLICIT_ID",  # explicit id nel testo, scope globale messaggio
    "REPLY",                # reply_to_message_id
    "SYMBOL",               # symbol nel testo
    "GLOBAL_SCOPE",         # scope hint (ALL_LONG, ALL_OPEN, ecc.)
    "UNKNOWN",
]
```

### 3.2 `contracts/context.py` — estendere `TargetHints`

Aggiungere:

```python
class TargetHints(ContextModel):
    target_source: TargetSource = "UNKNOWN"   # nuovo (D4)
    reply_to_message_id: int | None = None
    telegram_message_ids: list[int] = Field(default_factory=list)
    telegram_links: list[str] = Field(default_factory=list)
    explicit_ids: list[str] = Field(default_factory=list)
    symbols: list[str] = Field(default_factory=list)
    scope_hint: ScopeHint = "UNKNOWN"
```

### 3.3 `contracts/parsed_message.py` — estendere `ParsedIntent`

Aggiungere tre campi (D9 + D1):

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
    intent_id: str | None = None               # nuovo (D9) — es. "MOVE_STOP_TO_BE#0"
    occurrence_index: int | None = None        # nuovo (D9) — indice per tipo
    target_hints: TargetHints | None = None    # nuovo (D1) — target locale per occorrenza
```

### 3.4 `contracts/canonical_message.py` — aggiungere `source_intent_id`

Su `UpdateOperation`:

```python
class UpdateOperation(CanonicalModel):
    # ... campi esistenti ...
    source_intent: IntentType
    source_intent_id: str | None = None   # nuovo — es. "MOVE_STOP_TO_BE#1"
```

Su `TargetedAction`:

```python
class TargetedAction(CanonicalModel):
    action_type: UpdateOperationType
    params: dict[str, Any] = Field(default_factory=dict)
    target_hints: TargetHints
    source_intent: IntentType
    source_intent_id: str | None = None   # nuovo
    raw_fragment: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
```

### 3.5 `contracts/rules.py` — aggiungere `WeakContextExclusionRule`

```python
class WeakContextExclusionRule(RulesModel):
    name: str
    intent: IntentType
    markers: list[str] | dict[str, str]     # lista esplicita o {"source": "intent_weak"}
    scope: Literal["same_sentence", "same_line", "window", "whole_message"]
    window_chars: int | None = None          # solo per scope="window"
    if_contains_any: list[str] = Field(default_factory=list)
    if_regex_any: list[str] = Field(default_factory=list)
    unless_contains_any: list[str] = Field(default_factory=list)
    reason: str | None = None

    @model_validator(mode="after")
    def _validate_at_least_one_condition(self) -> WeakContextExclusionRule:
        if not self.if_contains_any and not self.if_regex_any:
            raise ValueError("WeakContextExclusionRule requires if_contains_any or if_regex_any")
        return self


class MarkerResolutionRules(RulesModel):
    suppress_weak_inside_strong_same_intent: bool = False
    weak_context_exclusions: list[WeakContextExclusionRule] = Field(default_factory=list)  # nuovo (D6)
    cross_intent_suppression: list[CrossIntentSuppressionRule] = Field(default_factory=list)
```

Il campo `scope` nelle regole di disambiguazione (D3) resta come `dict[str, Any]` nel JSON — il `LocalDisambiguator` legge il campo `"scope"` con default `"whole_message"`.

### 3.6 `contracts/canonical_message.py` — validator `_validate_primary_class_payloads`

Sostituire `"multi_ref_mixed_intents_not_supported"` con `"ambiguous_target_intent_binding"` (D7):

```python
if (
    self.parse_status == "PARTIAL"
    and not has_update_work
    and "ambiguous_target_intent_binding" not in self.warnings
):
    raise ValueError(...)
```

---

## 4. Nuovo componente: `TargetCandidate` e `TargetExtractionResult`

File: `contracts/context.py` (oppure `contracts/target_candidates.py`)

```python
class TargetCandidate(ContextModel):
    source: TargetSource
    value: Any                    # int per message_id, str per link/explicit_id/symbol/scope
    start: int | None = None      # posizione nel testo normalizzato
    end: int | None = None
    line_index: int | None = None


class TargetExtractionResult(ContextModel):
    message_target_hints: TargetHints         # target globale messaggio (retrocompatibile)
    candidates: list[TargetCandidate] = Field(default_factory=list)
```

---

## 5. Modifiche ai componenti esistenti

### 5.1 `core/target_hints_extractor.py` (D5)

`extract()` ritorna `TargetExtractionResult` invece di `TargetHints`.

Logica aggiuntiva:
- Per ogni link Telegram trovato: creare un `TargetCandidate` con `source="MESSAGE_TEXT_LINK"`, `start`, `end`, `line_index`
- Per ogni explicit ID trovato: `source="MESSAGE_EXPLICIT_ID"`, con posizione
- `reply_to_message_id`: `source="REPLY"`, `start=None`, `end=None`, `line_index=None`
- `scope_hint`: `source="GLOBAL_SCOPE"`
- `message_target_hints` si costruisce come oggi (retrocompatibile)

### 5.2 `core/marker_evidence_resolver.py` (D6)

Nuova firma:

```python
def resolve(
    self,
    matches: list[MarkerMatch],
    rules: ParserRules,
    *,
    raw_text: str | None = None,
    semantic_markers: SemanticMarkers | None = None,
) -> MarkerEvidenceResolution:
```

Ordine di applicazione:

```
1. suppress_weak_inside_strong_same_intent
2. weak_context_exclusions  (nuovo — richiede raw_text)
3. cross_intent_suppression
```

Logica `weak_context_exclusions`:
- Per ogni regola: trova i marker deboli dell'intent specificato
- Estrai il contesto testuale attorno al marker secondo lo `scope` della regola
- Se il contesto contiene `if_contains_any` o matcha `if_regex_any` → candidato a soppressione
- Se il contesto contiene `unless_contains_any` → non sopprimere
- La soppressione è marker-level (singola occorrenza), mai type-level

Se `raw_text is None` e ci sono `weak_context_exclusions`, aggiungere diagnostica `"weak_context_exclusions_skipped_no_text"`.

### 5.3 `core/local_disambiguator.py` (D3)

Il `LocalDisambiguator` legge il campo `"scope"` da ogni regola di disambiguazione. Default: `"whole_message"`.

Mapping scope → comportamento:
- `"whole_message"` — comportamento attuale (rimuove tutti gli intent del tipo)
- `"same_span"` — rimuove solo intent con span sovrapposto all'intent preferito
- `"same_line"` — rimuove solo intent sulla stessa `line_index`
- `"same_sentence"` — future, per ora trattato come `same_line`
- `"same_target_group"` — future, per ora trattato come `same_line`

### 5.4 `core/parsed_message_builder.py` (D9)

Dopo disambiguazione, assegnare `occurrence_index` e `intent_id` a tutti gli intent:

```python
def _assign_occurrence_ids(intents: list[ParsedIntent]) -> list[ParsedIntent]:
    counters: dict[str, int] = {}
    result = []
    for intent in intents:
        idx = counters.get(intent.type, 0)
        counters[intent.type] = idx + 1
        result.append(intent.model_copy(update={
            "occurrence_index": idx,
            "intent_id": f"{intent.type}#{idx}",
        }))
    return result
```

Chiamare questo passaggio prima di passare al `TargetBindingResolver`.

---

## 6. Nuovo componente: `TargetBindingResolver` (D1)

File: `core/target_binding_resolver.py`

```python
@dataclass(frozen=True)
class TargetBindingResult:
    intents: list[ParsedIntent]              # con target_hints locali dove possibile
    message_target_hints: TargetHints        # target globale finale (con priorità applicata)
    diagnostics: dict[str, Any]


class TargetBindingResolver:
    def bind(
        self,
        intents: list[ParsedIntent],
        extraction: TargetExtractionResult,
    ) -> TargetBindingResult:
        ...
```

### 6.1 Algoritmo di binding

**Step 1 — Priorità target globale (D10)**

Costruire `message_target_hints` finale applicando la catena di priorità:
```
1. link Telegram nel testo   → target_source = "MESSAGE_TEXT_LINK"
2. explicit id nel testo     → target_source = "MESSAGE_EXPLICIT_ID"
3. reply_to_message_id       → target_source = "REPLY"
4. symbol                    → target_source = "SYMBOL"
5. scope_hint globale        → target_source = "GLOBAL_SCOPE"
6. nessuno                   → target_source = "UNKNOWN"
```

Se viene selezionata una fonte, le fonti di priorità inferiore vanno in `diagnostics["ignored_lower_priority_targets"]`. Il reply ignorato va in `diagnostics["ignored_reply_to_message_id"]`.

**Step 2 — Line-level binding**

Per ogni `ParsedIntent` con `line_index` noto:
- Trovare i `TargetCandidate` con `line_index` uguale
- Se `N_candidates == 1` e `N_intents_on_line == 1` → assegnare il candidate a `intent.target_hints` con `target_source` = `LOCAL_TEXT_LINK` o `LOCAL_EXPLICIT_ID`
- Se `N_candidates == N_intents_on_line > 1` → binding 1:1 per posizione (D11)
- Se `N_candidates != N_intents_on_line` e entrambi > 1 → `ambiguous_target_intent_binding` (D11)
- Se `N_candidates > 1` e `N_intents_on_line == 1` → tutti i candidati vanno su quell'intent (stesso intent su più target locali)

**Step 3 — Intent senza target locale**

Gli intent senza `target_hints` locale ereditano il `message_target_hints` globale al momento della traduzione (non viene copiato qui — viene usato come fallback nel translator).

### 6.2 Casi canonici

| Pattern messaggio | Risultato |
|-------------------|-----------|
| `reply + нет link` | `message_target_hints.target_source = "REPLY"` |
| `reply + link nel testo` | `message_target_hints = link`, reply in diagnostics |
| `link111\nlink222\nstоп` | `message_target_hints = [111,222]`, nessun binding locale |
| `link111 стоп\nlink222 закрываю` | intent[0].target_hints=[111], intent[1].target_hints=[222] |
| `link111 link222 стоп закрываю` | PARTIAL + `ambiguous_target_intent_binding` |
| `scope ALL_OPEN\nстоп` | `message_target_hints.scope_hint = ALL_OPEN` |

---

## 7. Modifiche a `CanonicalTranslator` (D2, D7, D8, D10)

### 7.1 Priorità target per operation

Per ogni intent/operation:
```
1. usa ParsedIntent.target_hints se presente
2. altrimenti usa ParsedMessage.target_hints
3. altrimenti operation non targettizzata
```

### 7.2 Rimozione check signatures (D2)

Eliminare `_operation_signature` e il controllo `len(signatures) != 1`.

Nuova logica per `_should_use_targeted_actions`:
- Aggiungere `reply_to_message_id` come fonte valida per targeted actions

Nuova logica `_targeted_actions_from_operations`:
```python
# Se tutti gli intent hanno target_hints locale → usa quello per ciascuno
# Se nessuno ha target_hints locale → usa message_target_hints per tutti (stesso target)
# Misto → usa locale dove disponibile, globale come fallback
```

Non tornare mai `PARTIAL` basandosi sull'eterogeneità delle operations — `PARTIAL` viene solo dal `TargetBindingResolver` nei casi ambigui.

### 7.3 `source_intent_id` (D9)

Propagare `intent.intent_id` come `source_intent_id` su `UpdateOperation` e `TargetedAction`.

### 7.4 `CanonicalMessage.intents` deduplicated (D8)

```python
intents = list(dict.fromkeys(intent.type for intent in parsed.intents))
```

---

## 8. Regola `rules.json` — struttura aggiornata

```json
{
  "marker_resolution": {
    "suppress_weak_inside_strong_same_intent": true,
    "weak_context_exclusions": [
      {
        "name": "tp_after_first_tp_context",
        "intent": "TP_HIT",
        "markers": ["тейк", "тейка", "tp", "tp1"],
        "scope": "same_sentence",
        "if_contains_any": ["после 1 тейка", "после первого тейка"],
        "unless_contains_any": ["тейк взят", "взяли тейк"],
        "reason": "historical_context"
      }
    ],
    "cross_intent_suppression": []
  },
  "disambiguation": [
    {
      "name": "prefer_move_stop_to_be_over_move_stop",
      "scope": "same_span",
      "when_all_detected": ["MOVE_STOP_TO_BE", "MOVE_STOP"],
      "prefer": "MOVE_STOP_TO_BE",
      "over": ["MOVE_STOP"]
    }
  ]
}
```

Il campo `"markers"` in `weak_context_exclusions` supporta anche:
```json
"markers": {"source": "intent_weak"}
```
che usa tutti i marker weak dell'intent definiti in `semantic_markers.json`.

---

## 9. Pipeline aggiornata

```
testo + ParserContext
      ↓
TextNormalizer
      ↓
MarkerMatcher           → list[MarkerMatch] con start/end (multiple occurrences preservate)
      ↓
MarkerEvidenceResolver  → applica weak_context_exclusions (con raw_text)
                          applica suppress_weak_inside_strong
                          applica cross_intent_suppression
      ↓
IntentEntityExtractor   → list[ParsedIntent] (nessuna dedup per IntentType)
      ↓
LocalDisambiguator      → soppressione con scope (same_span / same_line / whole_message)
      ↓
TargetHintsExtractor    → TargetExtractionResult (message_target_hints + candidates[])
      ↓
ParsedMessageBuilder    → assegna intent_id / occurrence_index
      ↓
TargetBindingResolver   → assegna target_hints locale agli intent
                          risolve priorità target globale
                          emette ambiguous_target_intent_binding se necessario
      ↓
ParsedMessage
      ↓
CanonicalTranslator     → crea TargetedAction per ogni operation (con target locale o globale)
                          propaga source_intent_id
                          deduplica CanonicalMessage.intents
      ↓
CanonicalMessage
```

---

## 10. Invarianti da preservare

1. `MarkerMatcher` non deduplica per marker né per IntentType — ogni match è una occorrenza distinta
2. `weak_context_exclusions` non sopprime mai marker `strong`
3. Soppressione è sempre marker-level o occurrence-level, mai type-level
4. `ParsedIntent.intent_id` è unico nel messaggio: `f"{type}#{occurrence_index}"`
5. `CanonicalMessage.intents` non contiene duplicati (lista di tipi presenti)
6. `PARTIAL + ambiguous_target_intent_binding` è l'unico motivo per UPDATE/PARTIAL senza payload
7. `multi_ref_mixed_intents_not_supported` non esiste più — rimosso ovunque
8. Reply ignorato da link → va in `diagnostics`, non in `warnings`

---

## 11. Test minimi richiesti

### Gruppo A — weak_context_exclusions

| Test | Input | Atteso |
|------|-------|--------|
| A1 | `после 1 тейка закрылась в бу` | `TP_HIT` non rilevato, marker `тейка` soppresso |
| A2 | `после 1 тейка второй тейк взят` | `TP_HIT` strong rilevato (strong non soppresso) |
| A3 | `сделка точно дойдет до тейка` | `TP_HIT` non rilevato |
| A4 | `после 1 тейка закрылась в бу.\n2 тейк взят.` | `EXIT_BE` + `TP_HIT`, solo weak в prima frase soppresso |

### Gruppo B — multiple occurrences

| Test | Input | Atteso |
|------|-------|--------|
| B1 | `стоп в бу\nстоп в бу` | 2 intent `MOVE_STOP_TO_BE` con occurrence_index 0 e 1 |
| B2 | `link 111 стоп в бу\nlink 222 стоп в бу` | 2 intent con target_hints locali [111] e [222] |

### Gruppo C — target binding

| Test | Input | Atteso |
|------|-------|--------|
| C1 | reply=100, `стоп в бу\nлимитки убираем` | 2 targeted_actions entrambe su reply=100 |
| C2 | reply=100, `https://t.me/c/777/222 стоп в бу` | targeted_action su 222, reply in diagnostics |
| C3 | `link 111\nlink 222\nстоп в бу\nлимитки убираем` | SET_STOP su [111,222] + CANCEL_PENDING su [111,222], parse_status=PARSED |
| C4 | scope `все открытые\nстоп в бу\nлимитки убираем` | SET_STOP + CANCEL_PENDING su ALL_OPEN |
| C5 | `link 111 стоп в бу\nlink 222 закрываю\nlink 333 лимитки убираем` | 3 targeted_actions, ognuna con target diverso |
| C6 | `link 111 link 222 стоп в бу закрываю` | PARTIAL + `ambiguous_target_intent_binding` |

### Gruppo D — CanonicalMessage

| Test | Input | Atteso |
|------|-------|--------|
| D1 | 2x `MOVE_STOP_TO_BE` | `intents = ["MOVE_STOP_TO_BE"]` (deduplicated) |
| D2 | `MOVE_STOP_TO_BE` + `CANCEL_PENDING` global target | `intents = ["MOVE_STOP_TO_BE", "CANCEL_PENDING"]` |

---

## 12. Piano implementazione

### Fase 1 — Contratti (nessun breaking change)

File: `contracts/enums.py`, `contracts/context.py`, `contracts/parsed_message.py`, `contracts/canonical_message.py`, `contracts/rules.py`

- Aggiungere `TargetSource` a `enums.py`
- Aggiungere `target_source` a `TargetHints`
- Aggiungere `intent_id`, `occurrence_index`, `target_hints` a `ParsedIntent`
- Aggiungere `source_intent_id` a `UpdateOperation` e `TargetedAction`
- Aggiungere `WeakContextExclusionRule` e campo in `MarkerResolutionRules`
- Aggiungere `TargetCandidate` e `TargetExtractionResult`
- Rinominare warning nel validator di `CanonicalMessage`

### Fase 2 — `MarkerEvidenceResolver`

File: `core/marker_evidence_resolver.py`

- Aggiungere parametro `raw_text` opzionale
- Implementare logica `weak_context_exclusions` (scope: same_sentence, same_line, window, whole_message)

### Fase 3 — `LocalDisambiguator`

File: `core/local_disambiguator.py`

- Leggere campo `scope` dalle regole
- Implementare soppressione `same_span` e `same_line`
- Default retrocompatibile: `whole_message`

### Fase 4 — `TargetHintsExtractor`

File: `core/target_hints_extractor.py`

- Modificare `extract()` per ritornare `TargetExtractionResult`
- Preservare `start`, `end`, `line_index` per ogni candidato nel testo

### Fase 5 — `ParsedMessageBuilder`

File: `core/parsed_message_builder.py`

- Aggiungere passaggio `_assign_occurrence_ids` dopo disambiguazione

### Fase 6 — `TargetBindingResolver` (nuovo)

File: `core/target_binding_resolver.py`

- Implementare algoritmo di binding descritto in §6
- Implementare logica priorità target (D10)
- Implementare rilevamento ambiguità (D11)

### Fase 7 — `CanonicalTranslator`

File: `translation/canonical_translator.py`

- Rimuovere `_operation_signature` e check signatures
- Aggiungere `reply_to_message_id` come fonte per targeted_actions
- Implementare priorità target per operation (locale → globale)
- Propagare `source_intent_id`
- Deduplica `CanonicalMessage.intents`
- Sostituire `multi_ref_mixed_intents_not_supported` → `ambiguous_target_intent_binding`

### Fase 8 — Test

File: `tests/parser_v2/`

- Test gruppi A, B, C, D descritti in §11
