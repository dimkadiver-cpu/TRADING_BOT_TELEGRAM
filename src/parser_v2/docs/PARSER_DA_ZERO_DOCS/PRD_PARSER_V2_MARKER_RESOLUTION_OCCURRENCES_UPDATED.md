# PRD — parser_v2: marker_resolution, weak_context_exclusions e intent occurrences

## 1. Scopo

Questo documento aggiorna il PRD per `parser_v2` con due correzioni:

1. `weak_context_exclusions` deve stare dentro `marker_resolution`, non come blocco root-level.
2. Il parser deve preservare più occorrenze dello stesso `IntentType` nello stesso messaggio.

Il parser definitivo è `src/parser_v2/`. Nessun riferimento operativo al parser legacy.

---

## 2. Problema A — weak marker ambigui

Esempio reale:

```text
Закрылась в бу, после 1 тейка, конечно же

стоп, но сделка в итоге точно дойдет до тейка

ставить другой стоп, значит идти против торговой системы
```

Marker deboli problematici:

```text
тейк
тейка
стоп
бу
```

Il problema non è la presenza della parola, ma il contesto:

```text
после 1 тейка        -> contesto storico, non TP_HIT operativo
дойдет до тейка      -> previsione, non TP_HIT operativo
ставить другой стоп  -> discussione, non SL_HIT operativo
```

---

## 3. Problema B — più intent dello stesso tipo

Nello stesso messaggio possono comparire più occorrenze operative dello stesso intent.

Esempio:

```text
link 111 стоп в бу
link 222 стоп в бу
link 333 стоп в бу
```

Non significa:

```json
{
  "detected_intents": ["MOVE_STOP_TO_BE"]
}
```

Significa:

```json
{
  "intents": [
    {"type": "MOVE_STOP_TO_BE", "raw_fragment": "link 111 стоп в бу"},
    {"type": "MOVE_STOP_TO_BE", "raw_fragment": "link 222 стоп в бу"},
    {"type": "MOVE_STOP_TO_BE", "raw_fragment": "link 333 стоп в бу"}
  ]
}
```

Regola concettuale:

```text
IntentType   = categoria semantica
ParsedIntent = occorrenza concreta nel messaggio
```

---

## 4. Stato verificato della codebase parser_v2

### 4.1 `MarkerMatcher`

Stato: buono.

Il matcher supporta già occorrenze multiple dello stesso marker e conserva `start/end`.

Requisito:

```text
Mantenere questa proprietà.
Non deduplicare per marker.
Non deduplicare per IntentType.
```

---

### 4.2 `MarkerEvidenceResolver`

Stato: parzialmente buono.

Il resolver lavora già su indice del match, quindi può sopprimere singole occorrenze.

Oggi supporta:

```json
{
  "marker_resolution": {
    "suppress_weak_inside_strong_same_intent": true,
    "cross_intent_suppression": []
  }
}
```

Manca:

```json
{
  "weak_context_exclusions": []
}
```

---

### 4.3 `ParsedIntent`

Stato: quasi corretto.

`ParsedMessage` contiene già:

```python
intents: list[ParsedIntent]
```

e `ParsedIntent` contiene già:

```python
type
raw_fragment
line_index
span_start
span_end
evidence
```

Mancano campi utili per audit e tracciamento:

```python
intent_id
occurrence_index
target_hints
```

---

### 4.4 `IntentEntityExtractor`

Stato: parzialmente corretto.

Produce un `ParsedIntent` per ogni `MarkerEvidence`.

La deduplicazione interna per overlap/containment è accettabile.

È vietata la deduplicazione per solo `IntentType`.

---

### 4.5 `LocalDisambiguator`

Stato: punto fragile.

La logica attuale ragiona troppo per tipo:

```python
detected = {intent.type for intent in active}
```

e sopprime per tipo:

```python
removed = [intent for intent in active if intent.type in remove_types]
```

Rischio:

```text
una regola locale può sopprimere tutte le occorrenze di un intent,
anche se solo una occorrenza era ambigua.
```

Requisito:

```text
Le regole di disambiguazione devono poter lavorare occurrence-level.
```

---

### 4.6 `TargetHints`

Stato: globale.

Oggi `TargetHints` è associato al messaggio, non alla singola occorrenza.

Va bene per:

```text
link 111
link 222
стоп в бу
```

perché è una singola azione su più target.

Non basta per:

```text
link 111 стоп в бу
link 222 закрываю
link 333 лимитки убираем
```

perché ogni riga ha azione diversa e target diverso.

---

### 4.7 `CanonicalTranslator`

Stato: parzialmente corretto.

Produce più `UpdateOperation` da più intent.

Limite attuale:

```text
multi-ref + operazioni miste -> PARTIAL + multi_ref_mixed_intents_not_supported
```

Questo è accettabile come comportamento temporaneo, ma il PRD deve documentare il limite e il comportamento desiderato.

---

## 5. Contratto JSON corretto

In `rules.json` la struttura deve essere:

```json
{
  "marker_resolution": {
    "suppress_weak_inside_strong_same_intent": true,
    "weak_context_exclusions": [],
    "cross_intent_suppression": []
  }
}
```

Non usare:

```json
{
  "weak_exclusion_rules": []
}
```

Motivo:

```text
La responsabilità è del MarkerEvidenceResolver.
```

---

## 6. `weak_context_exclusions`

### 6.1 Forma base

```json
{
  "marker_resolution": {
    "weak_context_exclusions": [
      {
        "name": "tp_after_first_tp_context",
        "intent": "TP_HIT",
        "markers": ["тейк", "тейка", "tp"],
        "scope": "same_sentence",
        "if_contains_any": [
          "после 1 тейка",
          "после первого тейка",
          "после tp1"
        ],
        "unless_contains_any": [
          "тейк взят",
          "взяли тейк",
          "tp hit"
        ],
        "reason": "historical_context"
      }
    ]
  }
}
```

---

## 7. Campo `markers`

Il campo `markers` deve supportare due modalità.

### 7.1 Marker specifici

```json
"markers": ["тейк", "тейка", "tp"]
```

Significato:

```text
La regola si applica solo ai weak marker indicati.
```

### 7.2 Intera lista weak dell’intent

```json
"markers": {
  "source": "intent_weak"
}
```

Significato:

```text
La regola usa tutti i marker definiti in semantic_markers.intent_markers.<intent>.weak.
```

Esempio:

```json
{
  "intent_markers": {
    "TP_HIT": {
      "strong": ["тейк взят", "взяли тейк"],
      "weak": ["тейк", "тейка", "tp", "tp1"]
    }
  }
}
```

Con:

```json
"markers": {
  "source": "intent_weak"
}
```

la regola si applica a:

```json
["тейк", "тейка", "tp", "tp1"]
```

---

## 8. Campi supportati da `weak_context_exclusions`

| Campo | Tipo | Obbligatorio | Descrizione |
|---|---:|---:|---|
| `name` | string | sì | Nome tecnico della regola |
| `intent` | IntentType | sì | Intent a cui si applica |
| `markers` | array oppure object | sì | Marker specifici o `{"source": "intent_weak"}` |
| `scope` | string | sì | Area testuale in cui verificare il contesto |
| `if_contains_any` | array[string] | no | Frasi che attivano l’esclusione |
| `if_regex_any` | array[string] | no | Regex che attivano l’esclusione |
| `unless_contains_any` | array[string] | no | Frasi che impediscono l’esclusione |
| `reason` | string | no | Ragione diagnostica |

Almeno uno tra questi campi deve essere presente:

```text
if_contains_any
if_regex_any
```

---

## 9. Scope supportati

### 9.1 `same_sentence`

Default consigliato.

La regola si applica alla frase che contiene il marker weak.

### 9.2 `same_line`

La regola si applica alla riga che contiene il marker weak.

### 9.3 `window`

La regola si applica a una finestra locale attorno al marker.

```json
{
  "scope": "window",
  "window_chars": 50
}
```

### 9.4 `whole_message`

La regola si applica all’intero messaggio.

Da usare raramente.

Rischio:

```text
Può sopprimere marker validi in altre frasi o righe.
```

---

## 10. Regole di comportamento

### 10.1 Strong non viene soppresso

Una `weak_context_exclusion` non deve mai sopprimere marker `strong`.

Esempio:

```text
после 1 тейка второй тейк взят
```

Output corretto:

```json
{
  "intents": [
    {
      "type": "TP_HIT",
      "strength": "strong",
      "matched_markers": ["тейк взят"]
    }
  ]
}
```

### 10.2 Soppressione marker-level

Corretto:

```text
sopprimi TP_HIT/weak:тейка@start:end
```

Errato:

```text
sopprimi tutti i TP_HIT nel messaggio
```

### 10.3 Applicazione locale

Input:

```text
после 1 тейка закрылась в бу.
2 тейк взят.
```

Risultato corretto:

```text
- sopprimere il weak marker "тейка" nella prima frase
- mantenere il TP_HIT valido nella seconda frase
```

---

## 11. Esempi di regole

### 11.1 TP storico

```json
{
  "name": "tp_after_first_tp_context",
  "intent": "TP_HIT",
  "markers": ["тейк", "тейка", "tp", "tp1"],
  "scope": "same_sentence",
  "if_contains_any": [
    "после 1 тейка",
    "после первого тейка",
    "после tp1"
  ],
  "unless_contains_any": [
    "тейк взят",
    "взяли тейк",
    "tp hit"
  ],
  "reason": "historical_context"
}
```

### 11.2 TP previsione futura

```json
{
  "name": "tp_future_prediction_context",
  "intent": "TP_HIT",
  "markers": {
    "source": "intent_weak"
  },
  "scope": "same_sentence",
  "if_regex_any": [
    "дойд[её]т\s+до\s+тейк",
    "точно\s+дойд[её]т\s+до\s+тейк"
  ],
  "unless_contains_any": [
    "тейк взят",
    "взяли тейк",
    "take profit hit"
  ],
  "reason": "future_prediction"
}
```

### 11.3 Stop come discussione, non SL_HIT

```json
{
  "name": "sl_discussion_not_stop_hit",
  "intent": "SL_HIT",
  "markers": {
    "source": "intent_weak"
  },
  "scope": "same_sentence",
  "if_contains_any": [
    "ставить другой стоп",
    "другой стоп",
    "против торговой системы"
  ],
  "unless_contains_any": [
    "стоп сработал",
    "выбило по стопу",
    "закрыло по стопу"
  ],
  "reason": "discussion_not_execution"
}
```

---

## 12. Multiple same-type intents

### 12.1 Regola principale

Il parser deve preservare più `ParsedIntent` con lo stesso `IntentType`.

Esempio:

```text
link 111 стоп в бу
link 222 стоп в бу
```

Output corretto:

```json
{
  "intents": [
    {
      "intent_id": "MOVE_STOP_TO_BE#0",
      "occurrence_index": 0,
      "type": "MOVE_STOP_TO_BE",
      "line_index": 0,
      "raw_fragment": "link 111 стоп в бу"
    },
    {
      "intent_id": "MOVE_STOP_TO_BE#1",
      "occurrence_index": 1,
      "type": "MOVE_STOP_TO_BE",
      "line_index": 1,
      "raw_fragment": "link 222 стоп в бу"
    }
  ]
}
```

---

## 13. Dedup

### 13.1 Vietata

Vietata la deduplica per solo `IntentType`.

Errato:

```python
set(intent.type for intent in intents)
```

Errato:

```text
rimuovere tutte le occorrenze di TP_HIT perché una occorrenza è ambigua
```

### 13.2 Ammessa

Ammessa solo per occorrenze equivalenti o sovrapposte.

Condizione minima:

```text
same type
+ overlapping/contained span
+ same raw_fragment or same marker evidence
```

Esempio ammesso:

```text
weak "тейк" dentro strong "тейк взят"
```

---

## 14. Modifiche contrattuali consigliate

### 14.1 `ParsedIntent`

Aggiungere:

```python
intent_id: str | None = None
occurrence_index: int | None = None
target_hints: TargetHints | None = None
```

Esempio:

```json
{
  "intent_id": "CLOSE_FULL#0",
  "occurrence_index": 0,
  "type": "CLOSE_FULL",
  "target_hints": {
    "telegram_message_ids": [111]
  }
}
```

### 14.2 `UpdateOperation`

Aggiungere:

```python
source_intent_id: str | None = None
```

Esempio:

```json
{
  "op_type": "SET_STOP",
  "source_intent": "MOVE_STOP_TO_BE",
  "source_intent_id": "MOVE_STOP_TO_BE#1",
  "raw_fragment": "link 222 стоп в бу"
}
```

### 14.3 `TargetedAction`

Aggiungere:

```python
source_intent_id: str | None = None
```

---

## 15. Modifiche a `LocalDisambiguator`

### 15.1 Problema

Le regole attuali lavorano per tipo.

Questo è rischioso nei messaggi multi-intent.

### 15.2 Requisito

Le regole devono poter essere limitate a:

```text
same_span
same_line
same_sentence
same_target_group
whole_message
```

Default consigliato per nuove regole:

```text
same_span / same_line
```

Non `whole_message`.

### 15.3 Forma estesa per regole di disambiguazione

```json
{
  "name": "prefer_move_stop_to_be_over_move_stop",
  "scope": "same_span",
  "when_all_detected": ["MOVE_STOP_TO_BE", "MOVE_STOP"],
  "prefer": "MOVE_STOP_TO_BE",
  "over": ["MOVE_STOP"]
}
```

Per retrocompatibilità interna a `parser_v2`, se `scope` manca:

```text
default = whole_message
```

Ma per nuove regole va preferito:

```text
same_span
same_line
```

---

## 16. TargetHints occurrence-level

### 16.1 Caso già gestibile con target globale

```text
link 111
link 222
стоп в бу
```

Significa:

```text
stessa azione su più target
```

Può restare gestito con `ParsedMessage.target_hints`.

### 16.2 Caso da supportare con target per intent

```text
link 111 стоп в бу
link 222 закрываю
link 333 лимитки убираем
```

Significa:

```text
azione diversa per target diverso
```

Serve associare `TargetHints` alla singola occorrenza:

```json
{
  "intents": [
    {
      "type": "MOVE_STOP_TO_BE",
      "target_hints": {
        "telegram_message_ids": [111]
      }
    },
    {
      "type": "CLOSE_FULL",
      "target_hints": {
        "telegram_message_ids": [222]
      }
    },
    {
      "type": "CANCEL_PENDING",
      "target_hints": {
        "telegram_message_ids": [333]
      }
    }
  ]
}
```

---

## 17. CanonicalTranslator

### 17.1 Requisito

Quando `ParsedIntent.target_hints` è presente, il translator deve usare quello invece del `ParsedMessage.target_hints` globale.

Priorità:

```text
1. ParsedIntent.target_hints
2. ParsedMessage.target_hints
3. None
```

### 17.2 Caso multi-ref omogeneo

Input:

```text
link 111
link 222
стоп в бу
```

Output ammesso:

```json
{
  "targeted_actions": [
    {
      "action_type": "SET_STOP",
      "target_hints": {
        "telegram_message_ids": [111, 222]
      }
    }
  ]
}
```

### 17.3 Caso multi-ref misto

Input:

```text
link 111 стоп в бу
link 222 закрываю
link 333 лимитки убираем
```

Output desiderato:

```json
{
  "targeted_actions": [
    {
      "action_type": "SET_STOP",
      "source_intent_id": "MOVE_STOP_TO_BE#0",
      "target_hints": {
        "telegram_message_ids": [111]
      }
    },
    {
      "action_type": "CLOSE",
      "source_intent_id": "CLOSE_FULL#0",
      "target_hints": {
        "telegram_message_ids": [222]
      }
    },
    {
      "action_type": "CANCEL_PENDING",
      "source_intent_id": "CANCEL_PENDING#0",
      "target_hints": {
        "telegram_message_ids": [333]
      }
    }
  ]
}
```

---

## 18. Diagnostica

### 18.1 Marker weak soppressi

```json
{
  "suppressed_markers": [
    {
      "intent": "TP_HIT",
      "marker": "тейка",
      "span": [18, 23],
      "rule": "tp_after_first_tp_context",
      "reason": "historical_context",
      "scope": "same_sentence",
      "text_span": "закрылась в бу, после 1 тейка"
    }
  ]
}
```

### 18.2 Intent soppressi

```json
{
  "suppressed_intents": [
    {
      "intent_id": "MOVE_STOP#0",
      "type": "MOVE_STOP",
      "suppressed_by": "MOVE_STOP_TO_BE#0",
      "rule": "prefer_move_stop_to_be_over_move_stop"
    }
  ]
}
```

### 18.3 Occorrenze preservate

```json
{
  "intent_occurrences": [
    "MOVE_STOP_TO_BE#0@line:0",
    "MOVE_STOP_TO_BE#1@line:1",
    "CLOSE_FULL#0@line:2"
  ]
}
```

---

## 19. Test minimi richiesti

### Test 1 — weak TP storico escluso

Input:

```text
Закрылась в бу, после 1 тейка
```

Atteso:

```json
{
  "detected": ["EXIT_BE"],
  "not_detected": ["TP_HIT"],
  "suppressed_markers": ["TP_HIT/weak:тейка"]
}
```

### Test 2 — strong TP non escluso

Input:

```text
после 1 тейка второй тейк взят
```

Atteso:

```json
{
  "detected": ["TP_HIT"],
  "strength": "strong"
}
```

### Test 3 — previsione TP esclusa

Input:

```text
сделка точно дойдет до тейка
```

Atteso:

```json
{
  "not_detected": ["TP_HIT"],
  "suppressed_markers": ["TP_HIT/weak:тейка"]
}
```

### Test 4 — più occorrenze stesso intent preservate

Input:

```text
стоп в бу
стоп в бу
```

Atteso:

```json
{
  "intents": [
    {"type": "MOVE_STOP_TO_BE"},
    {"type": "MOVE_STOP_TO_BE"}
  ]
}
```

### Test 5 — due target, stesso intent

Input:

```text
link 111 стоп в бу
link 222 стоп в бу
```

Atteso:

```json
{
  "intents": [
    {
      "type": "MOVE_STOP_TO_BE",
      "target_hints": {"telegram_message_ids": [111]}
    },
    {
      "type": "MOVE_STOP_TO_BE",
      "target_hints": {"telegram_message_ids": [222]}
    }
  ]
}
```

### Test 6 — multi-ref mixed intents

Input:

```text
link 111 стоп в бу
link 222 закрываю
link 333 лимитки убираем
```

Atteso desiderato:

```json
{
  "targeted_actions": [
    {"action_type": "SET_STOP", "target": 111},
    {"action_type": "CLOSE", "target": 222},
    {"action_type": "CANCEL_PENDING", "target": 333}
  ]
}
```

Comportamento temporaneo ammesso se non ancora implementato:

```json
{
  "parse_status": "PARTIAL",
  "warnings": ["multi_ref_mixed_intents_not_supported"]
}
```

### Test 7 — disambiguazione locale, non globale

Input:

```text
после 1 тейка закрылась в бу.
2 тейк взят.
```

Atteso:

```json
{
  "detected": ["EXIT_BE", "TP_HIT"],
  "suppressed_markers": [
    "TP_HIT/weak:тейка nella prima frase"
  ]
}
```

Non deve sopprimere il `TP_HIT` valido della seconda frase.

---

## 20. Piano implementazione minimo

### Fase A — rules contract

File:

```text
src/parser_v2/contracts/rules.py
```

Aggiungere:

```python
class WeakContextExclusionRule(...)
```

e:

```python
class MarkerResolutionRules:
    weak_context_exclusions: list[WeakContextExclusionRule]
```

---

### Fase B — MarkerEvidenceResolver

File:

```text
src/parser_v2/core/marker_evidence_resolver.py
```

Ordine di applicazione consigliato:

```text
suppress_weak_inside_strong_same_intent
↓
weak_context_exclusions
↓
cross_intent_suppression
```

---

### Fase C — occurrence identity

File:

```text
src/parser_v2/contracts/parsed_message.py
```

Aggiungere a `ParsedIntent`:

```python
intent_id: str | None = None
occurrence_index: int | None = None
target_hints: TargetHints | None = None
```

---

### Fase D — IntentEntityExtractor

Assegnare `occurrence_index` e `intent_id`.

Regola:

```text
occurrence_index cresce per ogni IntentType.
intent_id = f"{IntentType}#{occurrence_index}"
```

Esempio:

```text
MOVE_STOP_TO_BE#0
MOVE_STOP_TO_BE#1
CLOSE_FULL#0
```

---

### Fase E — LocalDisambiguator

Evitare soppressione globale per tipo quando lo scope non lo giustifica.

Aggiungere supporto a:

```text
same_span
same_line
same_sentence
same_target_group
whole_message
```

---

### Fase F — target hints per intent

Implementare estrazione locale opzionale:

```text
line-level target hints
```

per associare link/ID alla singola occorrenza.

---

### Fase G — CanonicalTranslator

Aggiungere:

```python
source_intent_id
```

su `UpdateOperation` e `TargetedAction`.

Usare `ParsedIntent.target_hints` quando presente.

---

## 21. Decisione finale

La struttura corretta è:

```json
{
  "marker_resolution": {
    "suppress_weak_inside_strong_same_intent": true,
    "weak_context_exclusions": [],
    "cross_intent_suppression": []
  }
}
```

Ma questo non basta.

Il parser deve anche preservare l’identità delle occorrenze:

```text
IntentType non è un ID.
ParsedIntent è una occorrenza.
```

Requisiti finali:

```text
- MarkerMatcher: multiple occurrences già supportate, da mantenere.
- MarkerEvidenceResolver: soppressione marker-level.
- weak_context_exclusions: dentro marker_resolution.
- IntentEntityExtractor: più ParsedIntent dello stesso tipo ammessi.
- LocalDisambiguator: evitare soppressione globale per type.
- TargetHints: globali o per singolo ParsedIntent.
- CanonicalTranslator: source_intent_id e target_hints occurrence-level.
```

Senza questi vincoli, il parser può riconoscere “quale intent esiste”, ma perdere:

```text
quante volte
su quale riga
con quale target
con quale frammento operativo
```
