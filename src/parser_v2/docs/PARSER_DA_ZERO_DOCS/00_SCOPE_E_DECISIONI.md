# Parser da zero — Scope e decisioni architetturali

## Stato attuale del codice

```text
src/parser_v2/    → contiene SOLO docs/. Nessun codice Python ancora scritto.
src/parser/       → parser corrente in produzione (legacy, sarà sostituito).
```

I documenti in `docs/PARSER_DA_ZERO_DOCS/` definiscono il design del nuovo parser. L'implementazione partirà dalla Fase 1 di [07_PIANO_IMPLEMENTAZIONE.md](07_PIANO_IMPLEMENTAZIONE.md).

> Il `CLAUDE.md` di progetto descrive lo stato della migrazione `canonical_v1` (parser legacy). È **separato** da questo design — il nuovo parser_v2 non deriva da quella migrazione, è una riscrittura indipendente.

---

## Versionamento schema

```text
parsed_message_v2     → output interno parser_v2
canonical_message_v2  → output finale parser_v2
```

Differenze rispetto a `canonical_message_v1` (legacy in `src/parser/canonical_v1/`):

```text
- rimosso validation_status (era validazione DB dentro al parser)
- rimossi valid_refs / invalid_refs / invalid_reason
- Targeting → TargetHints + nuovo top-level targeted_actions
- intents: list[IntentType] invece di list[IntentResult] (dettaglio in operations/events)
- nomi intent canonici, niente U_* legacy
- info_type rimosso (solo raw_fragment)
- ReportResult ridotto a raw_fragment (no result_value/percent/currency)
```

---

## Scopo

Questa proposta definisce un parser nuovo, riscritto da zero, limitato a:

```text
Raw Telegram message
↓
ParsedMessage
↓
CanonicalMessage
```

Fuori scope:

```text
TargetResolver
ApplicabilityValidator
ExecutionPlanner
ExecutionApplier
DB lifecycle validation
operation_rules        # sarà riscritto, non riusato
target_resolver        # sarà riscritto, non riusato
backtesting integration
```

> ⚠️ Nessun adapter `CanonicalMessage → TraderParseResult` legacy. I layer downstream (`operation_rules`, `target_resolver`) saranno riscritti per consumare `CanonicalMessage` direttamente, non adattati.

Il parser non deve decidere se un comando è eseguibile. Deve solo produrre una rappresentazione canonica di ciò che il messaggio dice.

---

## Decisione principale

Il parser termina con `CanonicalMessage`.

```text
Parser = comprensione linguistica e normalizzazione semantica
Runtime operativo = targeting reale, validazione stato, esecuzione
```

Quindi il parser può indicare che un messaggio contiene riferimenti testuali o contestuali, ma non deve risolverli in posizioni/ordini reali.

Esempio:

```text
"стоп в бу"
```

Il parser può produrre:

```json
{
  "primary_class": "UPDATE",
  "primary_intent": "MOVE_STOP_TO_BE",
  "update": {
    "operations": [
      {
        "op_type": "SET_STOP",
        "set_stop": {
          "target_type": "ENTRY"
        }
      }
    ]
  }
}
```

Non deve decidere:

```text
- a quale posizione applicarlo
- se la posizione è ancora aperta
- se lo stop è già stato spostato
- se il comando è applicabile
```

---

## Cosa tenere

Da mantenere come concetti:

| Concetto | Stato |
|---|---|
| `ParsedMessage` | utile come output intermedio del parser |
| `CanonicalMessage` | utile come output finale verso runtime |
| `SignalPayload` | utile, ma va semplificato |
| `UpdatePayload` | utile |
| `ReportPayload` | utile, ma va ridotto |
| `RawContext` | utile |
| marker strong/weak | utili, ma servono match con span |
| rules JSON | utile se separa logica da vocabolario |

---

## Cosa eliminare

Da non portare nella riscrittura:

| Da eliminare | Motivo |
|---|---|
| `TraderParseResult` come output principale | è legacy e duplica `ParsedMessage` |
| `parse_message()` legacy | crea doppia fonte di verità |
| `parse_canonical()` nei profili | il translator deve essere unico |
| validazione DB dentro parser | confonde linguaggio e applicabilità |
| `validation_status=VALIDATED` richiesto dal translator | nome e semantica sbagliati per un parser puro |
| nomi intent legacy `U_*` | aumentano alias e bug |
| `REPORT_FINAL_RESULT` / `REPORT_PARTIAL_RESULT` | troppo specifici, meglio `REPORT_RESULT` |
| fallback hardcoded sparsi | meglio centralizzare in marker/rules |
| doppia disambiguazione | una sola fase locale basta |

---

## Architettura proposta

```text
RawMessage
↓
MessageContextBuilder
↓
TextNormalizer
↓
MarkerMatcher
↓
MarkerEvidenceResolver
↓
SignalExtractor
↓
IntentEntityExtractor
↓
MessageClassificationResolver
↓
ParsedMessage
↓
CanonicalTranslator
↓
CanonicalMessage
```

Dopo:

```text
CanonicalMessage
↓
TargetResolver
↓
ApplicabilityValidator
↓
ExecutionPlanner
↓
ExecutionApplier
```

---

## Principio guida

Ogni componente deve avere una sola responsabilità.

```text
MarkerMatcher       = trova evidenze lessicali
EvidenceResolver    = pulisce strong/weak e conflitti locali
SignalExtractor     = estrae struttura segnale
IntentExtractor     = estrae intenti + entità
Classifier          = decide SIGNAL / UPDATE / REPORT / INFO
Translator          = costruisce CanonicalMessage
```

Niente DB.  
Niente lifecycle.  
Niente esecuzione.

---

## Verdetto

Questa architettura è più semplice dell’attuale perché elimina il dual-stack e separa nettamente:

```text
cosa dice il messaggio
```

da:

```text
cosa posso fare operativamente
```
