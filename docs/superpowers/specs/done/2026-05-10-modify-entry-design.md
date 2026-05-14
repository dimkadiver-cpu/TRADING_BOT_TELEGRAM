# Design Spec — parser_v2: MODIFY_ENTRY Robusto

**Data:** 2026-05-10
**Scope:** `src/parser_v2/` — solo profilo `trader_a`
**PRD di riferimento:** `src/parser_v2/docs/PARSER_DA_ZERO_DOCS/PRD_parser_v2_modify_entry_final_corrected.md`

---

## 1. Obiettivo

Migliorare la gestione di `MODIFY_ENTRY` nel parser_v2 per:

- Riconoscere più varianti realistiche di testo russo
- Produrre `mode`, `entry_selector`, `entry_structure` precisi
- Unificare il rilevamento mode attraverso l'evidence list (rimuovere regex paralleli)
- Wirare `entry_selector` come `MarkerKind` di primo livello nel sistema marker
- Non rompere nessun path esistente (`REMOVE` legacy, `ADD_ENTRY`, `REENTER`)

---

## 2. Decisioni architetturali

### 2.1 entry_selector via MarkerMatcher (non solo regex)

Il selector (`PRIMARY`, `AVERAGING`) entra nel sistema marker come `kind="entry_selector"`, analogo a `modify_entry_mode`. Il `MarkerMatcher` produce `MarkerEvidence` per i selector marker; l'extractor li legge dall'evidence list.

### 2.2 mode detection via evidence, non regex

I regex `_RE_MARKET_NOW` e `_RE_REMOVE` in `intent_entity_extractor.py` vengono rimossi. Il mode viene rilevato da evidence con `kind="modify_entry_mode"` sovrapposta o adiacente al marker intent.

### 2.3 Dispatch speciale per MODIFY_ENTRY

Il builder `_modify_entry_entities` riceve l'intera `evidence` list come terzo argomento. Tutti gli altri builder mantengono la firma `(ev, normalized)` invariata.

### 2.4 Context window = fino al prossimo intent

La finestra di estrazione prezzi va da `ev.start` fino allo `start` del prossimo evidence con `kind="intent"`, o fine testo. Previene cross-intent contamination.

### 2.5 REMOVE legacy invariato

`REMOVE` resta in `ModifyEntryMode` e `ModifyEntriesOperationKind`. Non viene esteso. Il marker `"убираем вход"` produce `mode="REMOVE"` tramite evidence `modify_entry_mode`, non via regex.

---

## 3. Contratti

### 3.1 `enums.py`

```python
MarkerKind = Literal[
    "intent", "field", "side", "entry_type",
    "modify_entry_mode", "entry_selector",   # entry_selector nuovo
    "info", "target_hint",
]

ModifyEntryMode = Literal[
    "MARKET_NOW", "UPDATE_PRICE", "UPDATE_RANGE", "REPLACE_ENTRY",
    "REMOVE", "UNKNOWN",
]

ModifyEntriesOperationKind = Literal[
    "ADD", "REENTER",
    "MARKET_NOW", "UPDATE_PRICE", "UPDATE_RANGE", "REPLACE_ENTRY",
    "REMOVE", "UNKNOWN",
]
```

### 3.2 `entities.py`

```python
class EntrySelector(ContractModel):
    role: EntryRole | None = None
    sequence: int | None = Field(default=None, ge=1)
    label: str | None = None
    raw: str | None = None


class ModifyEntryEntities(IntentEntities):
    mode: ModifyEntryMode = "UNKNOWN"
    entry_selector: EntrySelector | None = None
    entries: list[EntryLeg] = Field(default_factory=list)
    entry_structure: EntryStructure | None = None
    raw_mode_marker: str | None = None
    raw_selector_marker: str | None = None
```

### 3.3 `canonical_message.py`

`entry_structure` già esiste. Aggiungere solo `entry_selector`:

```python
class ModifyEntriesOperation(CanonicalModel):
    kind: ModifyEntriesOperationKind
    entries: list[EntryLeg] = Field(default_factory=list)
    entry_structure: EntryStructure | None = None
    entry_selector: EntrySelector | None = None   # nuovo
```

`EntrySelector` va importato da `entities.py`.

---

## 4. Infrastruttura marker

### 4.1 `rules.py`

```python
class SemanticMarkers(BaseModel):
    intent_markers: dict[str, MarkerSet]
    field_markers: dict[str, MarkerSet]
    side_markers: dict[str, MarkerSet]
    entry_type_markers: dict[str, MarkerSet]
    modify_entry_mode_markers: dict[str, MarkerSet]
    entry_selector_markers: dict[str, MarkerSet]   # nuovo
    info_markers: dict[str, MarkerSet]
    target_hint_markers: dict[str, MarkerSet]
```

### 4.2 `marker_matcher.py`

```python
def _iter_marker_groups(markers):
    return (
        ("intent",            markers.intent_markers),
        ("field",             markers.field_markers),
        ("side",              markers.side_markers),
        ("entry_type",        markers.entry_type_markers),
        ("modify_entry_mode", markers.modify_entry_mode_markers),
        ("entry_selector",    markers.entry_selector_markers),   # nuovo
        ("info",              markers.info_markers),
        ("target_hint",       markers.target_hint_markers),
    )
```

### 4.3 `semantic_markers.json`

**`intent_markers.MODIFY_ENTRY`** — espansione da 3 a 13 strong marker:

```json
"MODIFY_ENTRY": {
  "strong": [
    "входим по рынку", "вход по рынку",
    "новый вход", "новая точка входа",
    "вход теперь", "вход меняем", "меняем вход",
    "переносим вход", "вход переносим",
    "лимитку входа переносим",
    "основной вход переносим",
    "усреднение переносим",
    "убираем вход"
  ],
  "weak": ["точка входа"]
}
```

**`modify_entry_mode_markers`** — completati (erano parziali, mancavano UPDATE_RANGE e REPLACE_ENTRY):

```json
"modify_entry_mode_markers": {
  "MARKET_NOW": {
    "strong": ["входим по рынку", "вход по рынку", "по текущим", "с текущих"],
    "weak": []
  },
  "UPDATE_PRICE": {
    "strong": [
      "новый вход", "новая точка входа",
      "вход теперь", "вход меняем", "меняем вход",
      "переносим вход", "вход переносим",
      "лимитку входа переносим",
      "основной вход переносим",
      "усреднение переносим"
    ],
    "weak": []
  },
  "UPDATE_RANGE": {
    "strong": ["диапазон входа", "вход в диапазон"],
    "weak": []
  },
  "REPLACE_ENTRY": {
    "strong": ["заменяем вход", "полностью меняем вход"],
    "weak": []
  },
  "REMOVE": {
    "strong": ["убираем вход"],
    "weak": []
  }
}
```

**`entry_selector_markers`** — nuova sezione (il contenuto finale è rimandato alla revisione marker dell'utente):

```json
"entry_selector_markers": {
  "PRIMARY": {
    "strong": ["основной вход", "первый вход", "вход a", "entry a"],
    "weak": []
  },
  "AVERAGING": {
    "strong": ["усреднение", "лимитка на усреднение", "вход b", "entry b"],
    "weak": []
  }
}
```

> **Nota:** il contenuto di `entry_selector_markers` e `modify_entry_mode_markers` è soggetto a revisione separata da parte dell'utente prima dell'implementazione.

---

## 5. Extractor refactor

### 5.1 Dispatch in `extract()`

```python
if ev.name == "MODIFY_ENTRY":
    entities = _modify_entry_entities(ev, normalized, evidence)
else:
    builder = _ENTITY_BUILDERS.get(ev.name)
    if builder is None:
        continue
    entities = builder(ev, normalized)
```

### 5.2 `_modify_entry_entities()`

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

    return ModifyEntryEntities(
        mode=mode,
        entry_selector=selector,
        entries=entries,
        entry_structure=entry_structure,
        raw_mode_marker=raw_mode_marker,
        raw_selector_marker=selector.raw if selector else None,
    )
```

### 5.3 `_detect_modify_entry_mode()`

```python
def _detect_modify_entry_mode(
    ev: MarkerEvidence,
    all_evidence: list[MarkerEvidence],
) -> tuple[ModifyEntryMode, str | None]:
    for e in all_evidence:
        if e.kind == "modify_entry_mode" and not e.suppressed:
            if _spans_overlap_or_adjacent(e, ev):
                return e.name, e.marker  # type: ignore[return-value]
    return "UNKNOWN", ev.marker
```

### 5.4 `_detect_entry_selector()`

```python
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
```

### 5.5 `_extract_modify_entry_prices()`

```python
_RANGE_RE = re.compile(r"(?P<p1>\d[\d.,]*) *- *(?P<p2>\d[\d.,]*)")

def _extract_modify_entry_prices(
    window: str,
    mode: ModifyEntryMode,
) -> tuple[list[EntryLeg], EntryStructure | None]:
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
                [EntryLeg(sequence=1, entry_type="LIMIT", price=p1),
                 EntryLeg(sequence=2, entry_type="LIMIT", price=p2)],
                "RANGE",
            )

    prices = _prices_in_window(window)
    if not prices:
        return [], None
    legs = [
        EntryLeg(sequence=i, entry_type="LIMIT", price=p)
        for i, p in enumerate(prices, 1)
    ]
    structure: EntryStructure = "ONE_SHOT" if len(legs) == 1 else "LADDER"
    return legs, structure
```

### 5.6 Helper geometrico e context window

```python
def _spans_overlap_or_adjacent(
    a: MarkerEvidence, b: MarkerEvidence, gap: int = 5
) -> bool:
    return a.start <= b.end + gap and b.start <= a.end + gap


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
```

### 5.7 `_prices_in_window()` (nuovo helper locale)

Sostituisce `_prices_after(text, ev.end)` per l'uso in MODIFY_ENTRY. Riceve già la window pre-calcolata invece di testo completo + offset.

```python
def _prices_in_window(window: str) -> list[Price]:
    return [p for m in _PRICE_RE.finditer(window) if (p := _price_from_raw(m.group(0)))]
```

`_prices_after` rimane per gli altri builder che lo usano (es. `_reenter_entities`, `_modify_targets_entities`).

### 5.8 Rimozioni

- `_RE_MARKET_NOW` — rimosso
- `_RE_REMOVE` — rimosso

---

## 6. Translator

`canonical_translator.py`, ramo `MODIFY_ENTRY`:

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

Import aggiuntivo: `EntrySelector` da `contracts/entities.py` — non da `canonical_message.py`. Il `canonical_message.py` importa `EntrySelector` da `entities.py`, stesso pattern già usato per `EntryLeg`.

---

## 7. Test plan

**Nuovo file:** `src/parser_v2/tests/test_modify_entry_extractor.py`

| Input | Mode | Structure | Selector role | Entries |
|---|---|---|---|---|
| `новый вход 2114` | UPDATE_PRICE | ONE_SHOT | None | [LIMIT 2114] |
| `вход теперь 2114` | UPDATE_PRICE | ONE_SHOT | None | [LIMIT 2114] |
| `вход теперь 2114-2120` | UPDATE_RANGE | RANGE | None | [LIMIT 2114, LIMIT 2120] |
| `вход теперь 2114 2100 2080` | UPDATE_PRICE | LADDER | None | [LIMIT 2114, 2100, 2080] |
| `основной вход переносим на 2114` | UPDATE_PRICE | ONE_SHOT | PRIMARY (seq=1) | [LIMIT 2114] |
| `усреднение переносим на 2114` | UPDATE_PRICE | ONE_SHOT | AVERAGING | [LIMIT 2114] |
| `входим по рынку` | MARKET_NOW | ONE_SHOT | None | [MARKET] |
| `убираем вход` | REMOVE | None | None | [] |
| `добавляю вход 2114` | intent=ADD_ENTRY (non MODIFY_ENTRY) | — | — | — |
| `перезаходим 2114` | intent=REENTER (non MODIFY_ENTRY) | — | — | — |
| `ETHUSDT LONG\nвход с текущих\nSL 2100\nTP 2200` | primary_class=SIGNAL (non UPDATE) | — | — | — |

**Integrazioni esistenti da verificare:** `test_canonical_translator_v2.py` — aggiungere assertion su `entry_selector` e `entry_structure` propagati nel `ModifyEntriesOperation`.

---

## 8. File toccati

| File | Tipo modifica |
|---|---|
| `contracts/enums.py` | +`entry_selector` a `MarkerKind`, +`UPDATE_RANGE`/`REPLACE_ENTRY` a mode/kind |
| `contracts/entities.py` | +`EntrySelector`, estende `ModifyEntryEntities` |
| `contracts/canonical_message.py` | +`entry_selector` a `ModifyEntriesOperation` |
| `contracts/rules.py` | +`entry_selector_markers` a `SemanticMarkers` |
| `core/marker_matcher.py` | +1 riga in `_iter_marker_groups` |
| `profiles/trader_a/semantic_markers.json` | espande MODIFY_ENTRY, completa mode markers, aggiunge selector markers |
| `profiles/trader_a/intent_entity_extractor.py` | refactor `_modify_entry_entities`, rimuove `_RE_MARKET_NOW`/`_RE_REMOVE` |
| `translation/canonical_translator.py` | propaga `entry_structure`/`entry_selector` |
| `tests/test_modify_entry_extractor.py` | nuovo file test |

9 file. Nessuna modifica al DB, al parser legacy, agli altri profili trader.

---

## 9. Invarianti garantiti

- `ADD_ENTRY` e `REENTER` non cambiano semanticamente
- `REMOVE` legacy resta compatibile, non viene esteso
- `primary_class=SIGNAL` non diventa UPDATE per marker ambigui (gestito dal classification layer, invariato)
- Tutti i builder esistenti mantengono firma `(ev, normalized)` — zero regressioni
