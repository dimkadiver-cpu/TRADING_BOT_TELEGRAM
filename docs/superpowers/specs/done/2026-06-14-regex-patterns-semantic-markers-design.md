# Design — Regex Patterns in `semantic_markers.json`

**Data:** 2026-06-14  
**Scope:** `parser_v2` — estensione del matcher per supportare regex globali nei marker

---

## Problema

I file `semantic_markers.json` accumulano decine di literal quasi identici per coprire varianti minime di una stessa parola/frase. Esempio reale in `trader_prova`:

```json
"strong": ["тп1:", "тп2:", "тп3:", "тп4:", "тп5:",
           "тп 1:", "тп 2:", "тп 3:", "тп 4:", "тп 5:",
           "TP1:", "TP2:", "TP3:", "TP4:", "TP5:",
           "TP 1:", "TP 2:", "TP 3:", "TP 4:", "TP 5:", ...]
```

Una singola regex `(?i)тп\s*[1-5]:` copre tutte queste varianti.

---

## Obiettivo

Permettere di esprimere marker come regex grezze nel JSON, **senza cambiare il comportamento del sistema**. Literal e regex sono equivalenti: stessa forza (strong/weak), stesse rules di soppressione, stesso formato nei diagnostics.

---

## Decisioni di design

| Decisione | Scelta |
|-----------|--------|
| Formato JSON | `strong_patterns` / `weak_patterns` come array di regex grezze (`str`) |
| Precedenza literal vs regex | Nessuna — stesse regole di matching attuale |
| Tie-break su stesso span | Dedup per `(start, end, name, kind, strength, marker)` — preferisce literal |
| Diagnostics `matched_markers` | Mostra il testo effettivamente matchato, non il pattern |
| Compilazione | Al caricamento del profilo (`MarkerSet.__post_init__`), fail fast su regex invalida |
| Scope regex | Scope 1 solo: marker globali. Scope 2 (regex nelle rules di soppressione) è fuori scope |

---

## Formato JSON

Ogni sezione che oggi ha `strong` / `weak` può avere opzionalmente `strong_patterns` / `weak_patterns`.

```json
"take_profit": {
  "strong": ["тейки", "цели", "таргеты"],
  "strong_patterns": [
    "(?i)тп\\s*[1-5]:",
    "(?i)tp\\s*[1-5]:",
    "(?i)тейк\\s*[1-5]:",
    "(?i)цель\\s*[1-5]:",
    "(?i)[1-5]\\s*тейк:"
  ],
  "weak": [],
  "weak_patterns": []
}
```

I campi `*_patterns` sono **opzionali**. Assenza equivale a lista vuota — comportamento invariato.

Si applica a tutte le sezioni marker:
- `field_markers`
- `side_markers`
- `entry_type_markers`
- `modify_entry_mode_markers`
- `entry_selector_markers`
- `intent_markers`
- `info_markers`
- `target_hint_markers`

---

## Architettura del matcher

### Contratto `MarkerSet` (aggiornato)

```python
@dataclass
class MarkerSet:
    strong: list[str] = field(default_factory=list)
    weak: list[str] = field(default_factory=list)
    strong_patterns: list[str] = field(default_factory=list)
    weak_patterns: list[str] = field(default_factory=list)

    # compilati al caricamento — non serializzati
    _strong_compiled: list[re.Pattern] = field(default_factory=list, init=False, repr=False)
    _weak_compiled: list[re.Pattern] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self):
        self._strong_compiled = _compile_patterns(self.strong_patterns, "strong")
        self._weak_compiled = _compile_patterns(self.weak_patterns, "weak")
```

Errore di compilazione → `ValueError` con indicazione del pattern invalido.

### Pipeline `MarkerMatcher.match()` (aggiornata)

```
1. Scan literal   → MarkerMatch per ogni occorrenza (invariato)
2. Scan pattern   → MarkerMatch per ogni re.finditer hit (nuovo)
3. Merge          → lista unica ordinata per (start, end, sequence)
4. Dedup          → rimuovi duplicati esatti su (start, end, name, kind, strength, marker)
5. Return         → lista finale invariata per le rules downstream
```

Il campo `marker` di `MarkerMatch` contiene il **testo matchato** (`match.group(0)`), non il pattern. Il formato è identico ai match da literal.

### Dedup

Caso in cui un literal e un pattern producono lo stesso match (stesso testo, stessa posizione):

```python
seen = set()
result = []
for m in merged:
    key = (m.start, m.end, m.name, m.kind, m.strength, m.marker)
    if key not in seen:
        seen.add(key)
        result.append(m)
```

In pratica non accade se i literal coperti da un pattern vengono rimossi dal JSON — il dedup è una rete di sicurezza.

---

## Diagnostics

`matched_markers` è invariato — mostra il testo matchato a prescindere dalla sorgente:

```json
"matched_markers": [
  "field/take_profit/strong:тп1:@5:9",
  "field/take_profit/strong:тейки@40:46",
  "intent/MOVE_STOP_TO_BE/strong:стоп в бу@60:70"
]
```

Nessun campo aggiuntivo per indicare se il match viene da literal o pattern.

---

## Cosa non cambia

- `MarkerEvidenceResolver` — invariato
- `LocalDisambiguator` — invariato
- `rules.json` e tutte le rules di soppressione — invariate
- Formato output `ParsedMessage` — invariato
- Tutti i test esistenti — devono passare senza modifiche

---

## File coinvolti

| File | Modifica |
|------|----------|
| `src/parser_v2/contracts/rules.py` | Aggiungere `strong_patterns`, `weak_patterns` a `MarkerSet`; compilazione in `__post_init__` |
| `src/parser_v2/core/marker_matcher.py` | Aggiungere scan pattern in `match()`; dedup step |
| `src/parser_v2/profiles/*/semantic_markers.json` | Aggiungere `*_patterns` dove utile (opzionale, per profilo) |
| `src/parser_v2/tests/test_marker_matcher_patterns.py` | Nuovi test (vedi sezione Testing) |

---

## Testing

Casi da coprire:

1. **Pattern matcha, literal assente** — `strong_patterns: ["(?i)тп\\s*[1-5]:"]`, testo `"тп1:"` → produce `MarkerMatch` con `marker="тп1:"`
2. **Pattern e literal stesso span** — entrambi presenti nel JSON, stesso testo → dedup produce un solo match
3. **Pattern non matcha** — nessun `MarkerMatch` prodotto
4. **Regex invalida** — `__post_init__` solleva `ValueError` con nome del pattern
5. **Due pattern, due occorrenze diverse** — entrambi i match presenti in output, ordinati per posizione
6. **`weak_patterns`** — stesso comportamento di `strong_patterns` ma con `strength="weak"`
7. **Rules downstream invariate** — `suppress_weak_inside_strong_same_intent` funziona su match da pattern esattamente come su literal

---

## Fuori scope (Scope 2)

Uso di regex nelle rules di soppressione/contesto (`if_regex_any` in `rules.json`). Questo promemoria riguarda solo i marker globali nel matcher.
