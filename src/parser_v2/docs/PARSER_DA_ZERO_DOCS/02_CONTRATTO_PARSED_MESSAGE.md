# Contratto `ParsedMessage`

## Scopo

`ParsedMessage` è l'output interno del parser. Non è ancora il messaggio operativo finale. Serve a rappresentare ciò che il parser ha capito dal testo.

Deve essere abbastanza ricco per il debug e abbastanza semplice da non diventare un secondo `CanonicalMessage`.

---

## Modello proposto

```python
class ParsedMessage(BaseModel):
    schema_version: str = "parsed_message_v2"
    parser_profile: str

    primary_class: MessageClass
    parse_status: ParseStatus
    confidence: float

    signal: SignalDraft | None = None

    intents: list[ParsedIntent] = []
    primary_intent: IntentType | None = None

    evidence_status: EvidenceStatus = "RESOLVED"

    target_hints: TargetHints | None = None

    warnings: list[str] = []
    diagnostics: dict[str, Any] = {}

    raw_context: RawContext
```

---

## Campi

### `parser_profile`

Esempio:

```text
trader_a
```

Serve per sapere quale profilo ha generato il parsing.

### `primary_class`

Valori:

```text
SIGNAL
UPDATE
REPORT
INFO
```

Non usare `NEW_SIGNAL`, `SETUP_INCOMPLETE`, `UNCLASSIFIED` come classi primarie — sono stati o sottotipi.

### `parse_status`

Valori:

```text
PARSED
PARTIAL
UNCLASSIFIED
ERROR
```

| Valore | Significato |
|--------|-------------|
| `PARSED` | messaggio interpretato in modo sufficiente |
| `PARTIAL` | messaggio riconosciuto ma mancano campi essenziali |
| `UNCLASSIFIED` | nessuna struttura utile |
| `ERROR` | errore tecnico o schema invalidabile |

### `confidence`

Float `0.0 - 1.0`. **Diagnostico, non decisionale**.

#### Formula

Recuperata dal parser attuale ([src/parser/rules_engine.py:25](src/parser/rules_engine.py:25)):

```text
STRONG_WEIGHT = 1.0
WEAK_WEIGHT   = 0.4

confidence_per_category(cat) =
    sum(STRONG_WEIGHT for marker in matched_strong[cat])
  + sum(WEAK_WEIGHT   for marker in matched_weak[cat])
  + sum(rule_boost    for rule  in matched_combination_rules where rule.then == cat)

raw_confidence = max over categories of confidence_per_category
confidence     = min(1.0, raw_confidence)
```

Esempio:

```text
Testo: "стоп в бу"
- MOVE_STOP_TO_BE strong "стоп в бу" -> 1.0
- (MOVE_STOP_TO_BE weak "бу"  soppresso da rule 1)
- (EXIT_BE weak "бу"          soppresso da cross-intent rule)

confidence = 1.0
```

#### Quando confidence < 1.0

```text
- solo weak marker      → confidence ≈ 0.4 per intent
- weak su più categorie → confidence ≈ max(0.4, 0.8 con boost)
- nessun marker         → confidence = 0.0, parse_status = UNCLASSIFIED
```

> ⚠️ `confidence` non deve essere il criterio principale per decidere se eseguire un comando. Quella è responsabilità del runtime post-parser.

### `signal`

Presente solo se il messaggio contiene struttura segnale.

```python
SignalDraft | None
```

Se `primary_class = SIGNAL`, `signal` deve essere presente.

### `intents`

Lista di intenti risolti localmente (dopo `MarkerEvidenceResolver` e `LocalDisambiguator`).

Non deve includere intenti weak già soppressi.

Esempio corretto:

```json
[
  {"type": "MOVE_STOP_TO_BE", "category": "UPDATE", "status": "RESOLVED"}
]
```

Esempio sbagliato (per input `стоп в бу`):

```json
[
  {"type": "MOVE_STOP_TO_BE", "category": "UPDATE"},
  {"type": "EXIT_BE", "category": "REPORT"}
]
```

### `primary_intent`

Intento principale dopo precedence/disambiguation locale.

Se il messaggio è composito, `primary_intent` indica solo l'intento dominante, non elimina gli altri intenti compatibili.

### `evidence_status`

Valori:

```text
RESOLVED
AMBIGUOUS
LOW_CONFIDENCE
```

#### Formula

```text
RESOLVED:
  - tutti gli intenti finali hanno strength="strong"
    OR esattamente uno strong vince con disambiguazione
  - confidence >= 0.8

AMBIGUOUS:
  - più intenti compatibili dopo disambiguazione, nessuno dominante
    (es. MOVE_STOP_TO_BE e EXIT_BE entrambi strong nello stesso messaggio
     senza regola che li disambigui)

LOW_CONFIDENCE:
  - tutti gli intenti finali sono solo "weak"
    OR confidence < 0.5

Default: RESOLVED quando in dubbio (per non bloccare il flusso).
```

> ⚠️ Non usare `VALIDATED` qui — il termine "validated" implica validazione DB/storica, che vive nel runtime post-parser.

### `target_hints`

Hint linguistici sul target. Per dettagli vedi [05_CANONICAL_MESSAGE.md](05_CANONICAL_MESSAGE.md#targethints) e [08_MULTI_REF_TARGETED_ACTIONS.md](08_MULTI_REF_TARGETED_ACTIONS.md).

### `warnings`

Esempi:

```text
partial_signal:missing=take_profits
ambiguous_intents:MOVE_STOP_TO_BE,EXIT_BE
weak_only_intent:REPORT_RESULT
update_without_target_hint
move_stop_without_level
multi_ref_mixed_intents_not_supported
```

### `diagnostics`

Informazioni utili per debug.

```json
{
  "matched_markers": [...],
  "suppressed_markers": [...],
  "applied_marker_rules": [...],
  "applied_disambiguation_rules": [...],
  "signal_missing_fields": [...],
  "signal_entry_count": 0,
  "signal_tp_count": 0,
  "category_scores": {"UPDATE": 1.0, "REPORT": 0.4}
}
```

---

## RawContext

```python
class RawContext(BaseModel):
    raw_text: str
    normalized_text: str | None = None

    message_id: int | None = None
    reply_to_message_id: int | None = None

    source_chat_id: str | None = None
    source_topic_id: int | None = None

    extracted_links: list[str] = []
    hashtags: list[str] = []
```

`RawContext` non deve contenere target risolti.

---

## Cosa non deve stare in `ParsedMessage`

```text
- target_signal_id risolto
- position_id
- order_id
- lifecycle_state
- applicabilità comando
- execution action finale
- stato DB
```

Questi appartengono a runtime successivo.

---

## Versione compatta

Esempio realistico:

```python
ParsedMessage(
    parser_profile="trader_a",
    primary_class="UPDATE",
    parse_status="PARSED",
    confidence=1.0,
    signal=None,
    intents=[
        ParsedIntent(
            type="MOVE_STOP_TO_BE",
            category="UPDATE",
            status="RESOLVED",
            confidence=1.0,
            entities=MoveStopToBEEntities(),
            raw_fragment="стоп в бу",
        )
    ],
    primary_intent="MOVE_STOP_TO_BE",
    evidence_status="RESOLVED",
    target_hints=None,
    warnings=["update_without_target_hint"],
    diagnostics={
        "matched_markers": ["MOVE_STOP_TO_BE/strong:стоп в бу"],
        "suppressed_markers": ["EXIT_BE/weak:бу"],
        "category_scores": {"UPDATE": 1.0},
    },
    raw_context=RawContext(raw_text="Стоп в БУ", normalized_text="стоп в бу"),
)
```
