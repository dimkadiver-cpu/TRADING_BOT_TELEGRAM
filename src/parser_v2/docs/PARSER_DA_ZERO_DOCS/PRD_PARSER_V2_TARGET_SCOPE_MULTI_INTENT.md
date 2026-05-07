# PRD — parser_v2 Target Scope, riferimenti multipli e multi-intent nello stesso messaggio

## 1. Scopo

Questo PRD definisce come `parser_v2` deve gestire:

- messaggi in reply;
- riferimenti espliciti nel testo tramite link Telegram o ID;
- elenco di riferimenti nello stesso messaggio;
- scope globali (`ALL_LONG`, `ALL_SHORT`, `ALL_OPEN`, ecc.);
- più intent nello stesso messaggio;
- più operazioni applicate allo stesso target o allo stesso gruppo di target.

Il parser definitivo è `src/parser_v2/`.

Il parser deve arrivare fino a `CanonicalMessage`. Non deve risolvere DB lifecycle, position_id, order_id o validazione operativa.

---

## 2. Problema

Oggi `parser_v2` riconosce più intent nello stesso messaggio, ma la gestione dei target è ancora troppo grezza.

Esempio semplice:

```text
reply al signal 100

стоп в бу
лимитки убираем
```

Semantica desiderata:

```text
Sul target indicato dal reply:
- MOVE_STOP_TO_BE
- CANCEL_PENDING
```

Esempio con ref multipli globali:

```text
link 111
link 222

стоп в бу
лимитки убираем
```

Semantica desiderata:

```text
Su entrambi i target 111 e 222:
- MOVE_STOP_TO_BE
- CANCEL_PENDING
```

Esempio con ref per riga:

```text
link 111 стоп в бу
link 222 закрываю
link 333 лимитки убираем
```

Semantica desiderata:

```text
111 -> MOVE_STOP_TO_BE
222 -> CLOSE_FULL
333 -> CANCEL_PENDING
```

Il parser attuale non distingue abbastanza bene questi tre casi.

---

## 3. Stato attuale verificato

### 3.1 `TargetHintsExtractor`

Oggi estrae:

```python
reply_to_message_id
telegram_links
telegram_message_ids
explicit_ids
symbols
scope_hint
```

Tutti questi campi finiscono nello stesso `TargetHints` globale di messaggio.

Non esiste ancora una priorità esplicita:

```text
ref nel testo > reply
```

Quindi un messaggio in reply che contiene anche un link conserva entrambi:

```json
{
  "reply_to_message_id": 100,
  "telegram_message_ids": [222]
}
```

ma non indica che `222` deve vincere su `100`.

---

### 3.2 `TargetHints` è globale

`TargetHints` oggi è associato a `ParsedMessage`, non a singolo `ParsedIntent`.

Questo è sufficiente per:

```text
link 111
link 222
стоп в бу
```

perché esiste una sola azione applicata a più ref.

Non è sufficiente per:

```text
link 111 стоп в бу
link 222 закрываю
```

perché ogni riga contiene un target diverso e un intent diverso.

---

### 3.3 `CanonicalTranslator`

Oggi il translator:

- crea più `UpdateOperation` se ci sono più intent update;
- crea una `TargetedAction` se ci sono `telegram_message_ids`, `telegram_links`, `explicit_ids` o scope globale;
- però accetta il targeted mode solo se tutte le operation hanno la stessa firma.

Se ci sono più operation diverse e target espliciti/globali, produce:

```text
PARTIAL + multi_ref_mixed_intents_not_supported
```

Questo è troppo conservativo.

---

## 4. Obiettivo funzionale

Il parser deve distinguere tre casi:

### Caso A — target unico implicito da reply

```text
reply a 100
стоп в бу
лимитки убираем
```

Output desiderato:

```json
{
  "target_scope": {
    "source": "REPLY",
    "reply_to_message_id": 100
  },
  "operations": [
    {"op_type": "SET_STOP", "source_intent": "MOVE_STOP_TO_BE"},
    {"op_type": "CANCEL_PENDING", "source_intent": "CANCEL_PENDING"}
  ]
}
```

Interpretazione:

```text
Tutte le operations si applicano allo stesso target del reply.
```

---

### Caso B — elenco di ref globale + più operazioni comuni

```text
link 111
link 222

стоп в бу
лимитки убираем
```

Output desiderato:

```json
{
  "targeted_actions": [
    {
      "action_type": "SET_STOP",
      "target_hints": {
        "telegram_message_ids": [111, 222]
      }
    },
    {
      "action_type": "CANCEL_PENDING",
      "target_hints": {
        "telegram_message_ids": [111, 222]
      }
    }
  ]
}
```

Interpretazione:

```text
Tutte le operations si applicano allo stesso gruppo di target.
```

Non deve diventare `PARTIAL` solo perché ci sono due operation diverse.

---

### Caso C — ref per riga + intent diversi

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

Interpretazione:

```text
Ogni intent ha il proprio target.
```

---

## 5. Non-obiettivi

Questo PRD non richiede:

- risoluzione DB del target reale;
- controllo se il target esiste;
- controllo lifecycle;
- validazione se l’operazione è applicabile;
- conversione a order_id / position_id;
- esecuzione;
- fallback LLM.

Queste responsabilità appartengono a layer successivi.

---

## 6. Regola di priorità target

Quando più fonti target sono presenti nello stesso messaggio, il parser deve selezionare la fonte più esplicita come target operativo.

Priorità consigliata:

```text
1. target locale per intent/riga
2. link Telegram nel testo
3. explicit id nel testo
4. reply_to_message_id
5. symbol
6. global scope
7. UNKNOWN
```

### 6.1 Ref nel testo vince sul reply

Esempio:

```text
reply a 100

https://t.me/c/777/222 стоп в бу
```

Target operativo:

```json
{
  "target_source": "TEXT_LINK",
  "telegram_message_ids": [222]
}
```

Il reply deve essere conservato solo in diagnostica:

```json
{
  "diagnostics": {
    "target_priority": {
      "selected_source": "TEXT_LINK",
      "ignored_reply_to_message_id": 100,
      "reason": "explicit_text_ref_overrides_reply"
    }
  }
}
```

---

## 7. Nuovi concetti contrattuali

### 7.1 `TargetSource`

Aggiungere enum:

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

---

### 7.2 Estensione `TargetHints`

Aggiungere:

```python
class TargetHints:
    target_source: TargetSource = "UNKNOWN"
```

Gli ignored targets vanno preferibilmente in diagnostica, non dentro `TargetHints`.

Esempio diagnostica:

```json
{
  "ignored_lower_priority_targets": {
    "reply_to_message_id": 100
  }
}
```

---

### 7.3 `ParsedIntent.target_hints`

Aggiungere a `ParsedIntent`:

```python
target_hints: TargetHints | None = None
```

Serve per i casi riga-target-azione.

Esempio:

```json
{
  "intent_id": "MOVE_STOP_TO_BE#0",
  "type": "MOVE_STOP_TO_BE",
  "raw_fragment": "link 111 стоп в бу",
  "target_hints": {
    "target_source": "LOCAL_TEXT_LINK",
    "telegram_message_ids": [111]
  }
}
```

---

### 7.4 `source_intent_id`

Aggiungere a `UpdateOperation`:

```python
source_intent_id: str | None = None
```

Aggiungere a `TargetedAction`:

```python
source_intent_id: str | None = None
```

Serve per audit e per distinguere più occorrenze dello stesso intent.

---

## 8. Classificazione dei target

### 8.1 Target globale di messaggio

Un target è globale di messaggio quando i riferimenti non sono legati a una riga specifica.

Esempio:

```text
link 111
link 222

стоп в бу
лимитки убираем
```

In questo caso:

```text
target_hints sta su ParsedMessage
ParsedIntent.target_hints = None
```

Tutte le operation usano il target globale.

---

### 8.2 Target locale per intent

Un target è locale quando il ref appare nella stessa riga/frase del comando.

Esempio:

```text
link 111 стоп в бу
link 222 закрываю
```

In questo caso:

```text
target_hints sta su ParsedIntent
ParsedMessage.target_hints può restare vuoto o diagnostico
```

---

### 8.3 Target ambiguo

Un target è ambiguo quando:

- ci sono più ref;
- ci sono più intent;
- non è possibile stabilire se i ref sono globali o locali;
- non c’è separazione chiara per riga/frase.

Esempio ambiguo:

```text
link 111 link 222 стоп в бу закрываю
```

Risultato:

```json
{
  "parse_status": "PARTIAL",
  "warnings": ["ambiguous_target_intent_binding"]
}
```

---

## 9. Regole per più intent

### 9.1 Più intent sullo stesso target sono validi

Questo deve essere valido:

```text
reply a 100
стоп в бу
лимитки убираем
```

Output:

```text
target = reply 100
operations = SET_STOP + CANCEL_PENDING
```

---

### 9.2 Più intent sullo stesso elenco di ref sono validi

Questo deve essere valido:

```text
link 111
link 222
стоп в бу
лимитки убираем
```

Output:

```text
targets = [111, 222]
operations = SET_STOP + CANCEL_PENDING
```

Non deve generare automaticamente:

```text
multi_ref_mixed_intents_not_supported
```

---

### 9.3 Più intent con ref per riga sono validi

Questo deve essere valido:

```text
link 111 стоп в бу
link 222 закрываю
```

Output:

```text
111 -> SET_STOP
222 -> CLOSE
```

---

### 9.4 Multi-ref mixed diventa PARTIAL solo se il binding è impossibile

`multi_ref_mixed_intents_not_supported` deve essere usato solo quando il parser non può stabilire il mapping ref-intent.

Non deve essere usato quando:

```text
tutti i ref sono globali
```

oppure quando:

```text
ogni intent ha target locale chiaro
```

---

## 10. Modifica logica `CanonicalTranslator`

### 10.1 Problema attuale

Oggi, se `target_hints` globale esiste e ci sono più operation con firme diverse, il translator produce `PARTIAL`.

Questo è troppo restrittivo.

---

### 10.2 Nuova regola

Se esiste un `TargetHints` globale valido:

```text
crea una TargetedAction per ogni operation
usando lo stesso target_hints globale
```

Esempio:

```json
{
  "operations": [
    {"op_type": "SET_STOP"},
    {"op_type": "CANCEL_PENDING"}
  ],
  "target_hints": {
    "telegram_message_ids": [111, 222]
  }
}
```

Output:

```json
{
  "targeted_actions": [
    {
      "action_type": "SET_STOP",
      "target_hints": {"telegram_message_ids": [111, 222]}
    },
    {
      "action_type": "CANCEL_PENDING",
      "target_hints": {"telegram_message_ids": [111, 222]}
    }
  ]
}
```

---

### 10.3 Priorità target nel translator

Per ogni intent/operation:

```text
1. usa ParsedIntent.target_hints se presente
2. altrimenti usa ParsedMessage.target_hints
3. altrimenti lascia operation non targettizzata
```

---

### 10.4 Reply come target esplicito

Scelta consigliata:

```text
creare targeted_actions anche da reply
```

Motivo:

```text
Il target binding diventa esplicito e uniforme per reply, link, explicit id e scope.
```

Esempio:

```json
{
  "targeted_actions": [
    {
      "action_type": "SET_STOP",
      "target_hints": {"reply_to_message_id": 100}
    },
    {
      "action_type": "CANCEL_PENDING",
      "target_hints": {"reply_to_message_id": 100}
    }
  ]
}
```

---

## 11. Modifica `TargetHintsExtractor`

### 11.1 Output raw vs output resolved

L’estrazione deve distinguere:

```text
raw target candidates
resolved target hints
```

Proposta:

```python
class TargetCandidate:
    source: TargetSource
    value: Any
    start: int | None
    end: int | None
    line_index: int | None
```

Il resolver target decide poi quale candidate usare.

---

### 11.2 Candidate da estrarre

```text
- reply_to_message_id da ParserContext
- telegram links nel testo
- telegram message ids derivati dai link
- explicit ids nel testo
- symbols
- scope hints
```

Per i target nel testo servono `start/end/line_index`.

---

### 11.3 Line-level target binding

Per ogni `ParsedIntent`, cercare target candidates nella stessa riga.

Regola:

```text
se target candidate e intent stanno sulla stessa line_index:
    assegnare candidate a ParsedIntent.target_hints
```

Se più target nella stessa riga:

```text
se tutti sono compatibili -> assegnarli come gruppo
se sono ambigui -> warning ambiguous_line_target_binding
```

---

## 12. Warning e diagnostica

### 12.1 `explicit_text_ref_overrides_reply`

Quando un messaggio è reply ma contiene link/ID espliciti:

```text
selected = text ref
ignored = reply
```

Preferenza:

```text
diagnostics, non warning
```

Perché non è errore.

---

### 12.2 `ambiguous_target_intent_binding`

Quando ci sono più ref e più intent ma il binding non è chiaro.

```json
{
  "warnings": ["ambiguous_target_intent_binding"],
  "parse_status": "PARTIAL"
}
```

---

### 12.3 `multi_ref_mixed_intents_not_supported`

Questo warning deve restare solo come fallback temporaneo.

Uso corretto:

```text
feature non ancora implementata
```

Uso futuro preferito:

```text
ambiguous_target_intent_binding
```

---

## 13. Esempi

### 13.1 Reply + due intent

Input:

```text
reply_to_message_id = 100

стоп в бу
лимитки убираем
```

Atteso:

```json
{
  "targeted_actions": [
    {
      "action_type": "SET_STOP",
      "target_hints": {"reply_to_message_id": 100}
    },
    {
      "action_type": "CANCEL_PENDING",
      "target_hints": {"reply_to_message_id": 100}
    }
  ]
}
```

---

### 13.2 Text link batte reply

Input:

```text
reply_to_message_id = 100

https://t.me/c/777/222 стоп в бу
```

Atteso:

```json
{
  "targeted_actions": [
    {
      "action_type": "SET_STOP",
      "target_hints": {
        "target_source": "LOCAL_TEXT_LINK",
        "telegram_message_ids": [222]
      }
    }
  ],
  "diagnostics": {
    "target_priority": {
      "ignored_reply_to_message_id": 100
    }
  }
}
```

---

### 13.3 Elenco ref globale + due intent

Input:

```text
https://t.me/c/777/111
https://t.me/c/777/222

стоп в бу
лимитки убираем
```

Atteso:

```json
{
  "targeted_actions": [
    {
      "action_type": "SET_STOP",
      "target_hints": {
        "telegram_message_ids": [111, 222]
      }
    },
    {
      "action_type": "CANCEL_PENDING",
      "target_hints": {
        "telegram_message_ids": [111, 222]
      }
    }
  ]
}
```

---

### 13.4 Scope globale + due intent

Input:

```text
все открытые
стоп в бу
лимитки убираем
```

Atteso:

```json
{
  "targeted_actions": [
    {
      "action_type": "SET_STOP",
      "target_hints": {
        "scope_hint": "ALL_OPEN"
      }
    },
    {
      "action_type": "CANCEL_PENDING",
      "target_hints": {
        "scope_hint": "ALL_OPEN"
      }
    }
  ]
}
```

---

### 13.5 Ref per riga + intent diversi

Input:

```text
https://t.me/c/777/111 стоп в бу
https://t.me/c/777/222 закрываю
https://t.me/c/777/333 лимитки убираем
```

Atteso:

```json
{
  "targeted_actions": [
    {
      "action_type": "SET_STOP",
      "target_hints": {
        "telegram_message_ids": [111]
      }
    },
    {
      "action_type": "CLOSE",
      "target_hints": {
        "telegram_message_ids": [222]
      }
    },
    {
      "action_type": "CANCEL_PENDING",
      "target_hints": {
        "telegram_message_ids": [333]
      }
    }
  ]
}
```

---

## 14. Test minimi richiesti

### Test 1 — reply + due operations

```python
def test_reply_target_applies_to_multiple_operations():
    text = "стоп в бу\nлимитки убираем"
    context.reply_to_message_id = 100

    assert targeted_actions == [
        SET_STOP target reply 100,
        CANCEL_PENDING target reply 100,
    ]
```

---

### Test 2 — text link overrides reply

```python
def test_text_link_overrides_reply():
    text = "https://t.me/c/777/222 стоп в бу"
    context.reply_to_message_id = 100

    assert selected target == 222
    assert ignored_reply_to_message_id == 100
```

---

### Test 3 — global ref list + mixed operations

```python
def test_global_ref_list_supports_multiple_different_operations():
    text = "link 111\nlink 222\nстоп в бу\nлимитки убираем"

    assert targeted_actions includes:
        SET_STOP on [111, 222]
        CANCEL_PENDING on [111, 222]
    assert parse_status != PARTIAL
```

---

### Test 4 — global scope + mixed operations

```python
def test_global_scope_supports_multiple_different_operations():
    text = "все открытые\nстоп в бу\nлимитки убираем"

    assert targeted_actions includes:
        SET_STOP on ALL_OPEN
        CANCEL_PENDING on ALL_OPEN
```

---

### Test 5 — line-level refs bind to line-level intents

```python
def test_line_level_refs_bind_to_line_level_intents():
    text = (
        "https://t.me/c/777/111 стоп в бу\n"
        "https://t.me/c/777/222 закрываю\n"
        "https://t.me/c/777/333 лимитки убираем"
    )

    assert 111 -> SET_STOP
    assert 222 -> CLOSE
    assert 333 -> CANCEL_PENDING
```

---

### Test 6 — ambiguous binding

```python
def test_ambiguous_ref_intent_binding_becomes_partial():
    text = "link 111 link 222 стоп в бу закрываю"

    assert parse_status == "PARTIAL"
    assert "ambiguous_target_intent_binding" in warnings
```

---

## 15. Piano implementazione

### Fase 1 — estendere contratti

File:

```text
src/parser_v2/contracts/context.py
src/parser_v2/contracts/parsed_message.py
src/parser_v2/contracts/canonical_message.py
src/parser_v2/contracts/enums.py
```

Aggiungere:

```text
TargetSource
TargetHints.target_source
ParsedIntent.target_hints
ParsedIntent.intent_id
ParsedIntent.occurrence_index
UpdateOperation.source_intent_id
TargetedAction.source_intent_id
```

---

### Fase 2 — target candidates

File:

```text
src/parser_v2/core/target_hints_extractor.py
```

Separare:

```text
extract raw candidates
resolve message-level target hints
resolve line-level target hints
```

---

### Fase 3 — line/fragment binding

Nuovo componente consigliato:

```text
src/parser_v2/core/target_binding_resolver.py
```

Contratto:

```python
class TargetBindingResolver:
    def bind(
        intents: list[ParsedIntent],
        target_candidates: list[TargetCandidate],
        message_target_hints: TargetHints,
    ) -> TargetBindingResult:
        ...
```

Output:

```text
- intents con target_hints locali quando possibile
- target_hints globale quando valido
- warnings se ambiguo
- diagnostics
```

---

### Fase 4 — translator

File:

```text
src/parser_v2/translation/canonical_translator.py
```

Modificare:

```text
- non degradare a PARTIAL quando ci sono più operation diverse sullo stesso target globale
- creare una TargetedAction per ogni operation
- usare ParsedIntent.target_hints prima di ParsedMessage.target_hints
- supportare reply come target esplicito
```

---

### Fase 5 — test

Aggiungere test in:

```text
tests/parser_v2/test_target_hints_extractor_phase9.py
tests/parser_v2/test_canonical_translator_phase11.py
tests/parser_v2/test_runtime_phase12.py
```

Nuovi test:

```text
test_text_link_overrides_reply
test_reply_target_applies_to_multiple_operations
test_global_ref_list_supports_multiple_different_operations
test_global_scope_supports_multiple_different_operations
test_line_level_refs_bind_to_line_level_intents
test_ambiguous_ref_intent_binding_becomes_partial
```

---

## 16. Decisione finale

Regole finali:

```text
1. Un ref nel testo è più esplicito del reply.
2. Più intent possono condividere lo stesso target.
3. Più operations diverse sullo stesso target globale sono valide.
4. Ref per riga devono essere associati agli intent della stessa riga.
5. PARTIAL va usato solo quando il binding ref-intent è ambiguo o non implementabile.
6. TargetHints globale non basta: serve anche target_hints per ParsedIntent.
7. CanonicalTranslator deve preservare source_intent_id e target binding.
```

Comportamento desiderato:

```text
reply + più intent                  -> tutte le operations sul reply
elenco ref globale + più intent      -> ogni operation sul gruppo ref
scope globale + più intent           -> ogni operation sullo scope globale
ref per riga + intent diversi        -> ogni operation sul proprio ref
ref nel testo + reply                -> ref nel testo vince
binding ambiguo                      -> PARTIAL + ambiguous_target_intent_binding
```
