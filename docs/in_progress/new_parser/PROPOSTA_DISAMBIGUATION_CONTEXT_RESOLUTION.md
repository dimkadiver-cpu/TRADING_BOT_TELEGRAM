# Proposta: Disambiguation Rules e Context Resolution Rules

## Obiettivo

Definire un sistema deterministico, leggero e manutenibile per:

- risolvere intent multipli non compatibili o parzialmente sovrapposti;
- distinguere intent generici e intent specifici;
- validare intent che dipendono dalla storia del target;
- migliorare la classificazione `UPDATE` vs `INFO_ONLY` usando il contesto reale del messaggio.

## Decisione Architetturale

Entrambi i blocchi vanno mantenuti:

- `disambiguation_rules`
- `context_resolution_rules`

In piu, conviene introdurre un livello centrale shared:

- `intent_compatibility`

Non sono ridondanti se hanno responsabilita distinte.

Regola di ownership:

1. `disambiguation_rules` gestisce solo conflitti risolvibili dal testo corrente.
2. `context_resolution_rules` gestisce solo risoluzioni che richiedono `target_ref` o `target_history`.
3. `intent_compatibility` definisce quali combinazioni sono ammesse, quali richiedono risoluzione e quali richiedono validazione contestuale.
4. La stessa trasformazione non deve essere definita in entrambi i layer.

Regola pratica:

- se basta leggere il testo, usare `disambiguation_rules`;
- se serve sapere a quale segnale si riferisce il messaggio o cosa era successo prima, usare `context_resolution_rules`.

## Pipeline Consigliata

1. `detect_intents_with_evidence(text)` produce candidati con `strong/weak`.
2. `intent_compatibility` classifica il set di intent rilevati.
3. `disambiguation_rules` risolve conflitti locali solo se richiesto dalla compatibilita.
4. `context_resolution_rules` valida e corregge gli intent stateful solo se richiesto dalla compatibilita o dal tipo di intent.
5. `select_primary_intent()` sceglie il primario finale.
6. build envelope + diagnostics.

## Resolution Unit

Con i messaggi multi-ref, il resolver semantico non lavora sempre sulla stessa unita logica.

Servono due modalita esplicite:

- `MESSAGE_WIDE`
- `TARGET_ITEM_WIDE`

### `MESSAGE_WIDE`

Si usa quando il messaggio ha una semantica unica condivisa da tutti i target citati.

Esempi:

- piu link e una sola azione comune `MOVE_STOP_TO_BE`
- piu link e una sola azione comune `CLOSE_FULL`

In questo caso:

1. si estraggono tutti i target del messaggio;
2. si rilevano gli intent una sola volta a livello messaggio;
3. si applicano `intent_compatibility`, `disambiguation_rules` e `context_resolution_rules` una sola volta;
4. solo dopo si costruisce il binding finale `azione -> gruppo target`.

### `TARGET_ITEM_WIDE`

Si usa quando il messaggio contiene righe o blocchi con semantica diversa per target diversi.

Esempi:

- quattro righe con `stop in be` e una riga con `stop on tp1`
- righe con risultati individuali diversi per ciascun ref

In questo caso:

1. il messaggio viene prima spezzato in item target-aware;
2. ogni item esegue la propria mini pipeline semantica;
3. per ogni item si applicano `intent_compatibility`, `disambiguation_rules` e `context_resolution_rules`;
4. alla fine si raggruppano gli item che hanno stessa firma semantica.

### Regola di scelta

- se il testo contiene una sola istruzione o evento comune a tutti i refs, usare `MESSAGE_WIDE`;
- se il testo contiene piu righe o frammenti con ref e semantica differente, usare `TARGET_ITEM_WIDE`.

### Effetto sul contratto target-aware

Le regole di disambiguazione e risoluzione contestuale vanno sempre applicate prima di costruire:

- `targeted_actions`
- `targeted_reports`

Quindi:

1. prima si decide il significato semantico corretto;
2. poi si associa quel significato ai target corretti.

Questo evita di costruire binding `azione -> ref` su intent ancora ambigui o non validati.

## `intent_compatibility`

### Scopo

Definire a livello shared le relazioni tra intent, senza duplicare la stessa conoscenza nei profili.

Questo blocco non risolve direttamente i conflitti.
Dice solo:

- se due intent possono coesistere;
- se sono in conflitto;
- se uno e piu specifico dell'altro;
- se per decidere serve il contesto;
- se il parser deve emettere warning quando non riesce a risolvere.

### Shape proposta

```json
{
  "intent_compatibility": {
    "pairs": [
      {
        "intents": ["MOVE_STOP_TO_BE", "MOVE_STOP"],
        "relation": "specific_vs_generic",
        "preferred": "MOVE_STOP_TO_BE",
        "requires_resolution": true
      },
      {
        "intents": ["EXIT_BE", "CLOSE_FULL"],
        "relation": "specific_vs_generic",
        "preferred": "EXIT_BE",
        "requires_resolution": true,
        "requires_context_validation": true
      },
      {
        "intents": ["TP_HIT", "REPORT_FINAL_RESULT"],
        "relation": "compatible",
        "requires_resolution": false
      },
      {
        "intents": ["SL_HIT", "CLOSE_FULL"],
        "relation": "exclusive",
        "requires_resolution": true
      }
    ]
  }
}
```

### Campi supportati

- `intents`: `list[string]`, obbligatorio, lunghezza 2
- `relation`: enum, obbligatorio
- `preferred`: `string`, opzionale
- `requires_resolution`: `true | false`, obbligatorio
- `requires_context_validation`: `true | false`, opzionale
- `warning_if_unresolved`: `true | false`, opzionale, default `true`

### Valori ammessi per `relation`

- `compatible`
- `exclusive`
- `specific_vs_generic`
- `stateful_requires_context`

### Semantica delle relazioni

#### `compatible`

I due intent possono passare insieme senza ulteriore intervento.

Esempio:

- `TP_HIT` + `REPORT_FINAL_RESULT`

#### `exclusive`

I due intent non dovrebbero convivere come output finale senza una risoluzione esplicita.

Esempio:

- `SL_HIT` + `CLOSE_FULL`

#### `specific_vs_generic`

Uno dei due intent e piu specifico dell'altro. In presenza di entrambi, normalmente si preferisce quello piu specifico.

Esempi:

- `MOVE_STOP_TO_BE` vs `MOVE_STOP`
- `EXIT_BE` vs `CLOSE_FULL`

#### `stateful_requires_context`

La coppia non e risolvibile bene senza `target_ref` o `target_history`.

Esempio:

- `EXIT_BE` con intent concorrenti o ambigui che richiedono storia del segnale

## Come il parser decide quale layer applicare

Il parser non deve decidere in anticipo se usare oppure no `disambiguation_rules`.

Regola semplice:

1. Il parser esegue prima `intent_compatibility`.
2. Se tutte le coppie sono `compatible`, non serve nessuna risoluzione locale.
3. Se una coppia ha `requires_resolution = true`, il parser prova `disambiguation_rules`.
4. Se la compatibilita indica `requires_context_validation = true`, il parser prova anche `context_resolution_rules`.
5. Se nessuna regola risolve il caso, il parser non deve fingere certezza: emette warning diagnostico.

### Quando siamo "di fronte a un conflitto"

Nel sistema, c'e conflitto quando `intent_compatibility` dice che almeno una coppia richiede risoluzione.

Questo accade tipicamente quando:

- due intent incompatibili sono presenti insieme;
- un intent generico e uno piu specifico sono presenti insieme;
- una coppia di intent e classificata come `exclusive`, `specific_vs_generic` o `stateful_requires_context`.

Esempi:

- `MOVE_STOP_TO_BE` + `MOVE_STOP`
- `EXIT_BE` + `CLOSE_FULL`
- `CLOSE_PARTIAL` + `CLOSE_FULL`

### Quando usare `disambiguation_rules`

`disambiguation_rules` interviene solo se `intent_compatibility` segnala che il set rilevato richiede risoluzione locale.

Se nessuna regola di disambiguazione matcha:

- il parser non altera gli intent;
- registra warning se la coppia richiedeva risoluzione.

### Quando usare `context_resolution_rules`

`context_resolution_rules` non risolve il testo locale.

Si usa quando per validare o correggere un intent serve sapere:

- se il messaggio punta a un target reale;
- se quel target ha una storia coerente;
- se un intent ambiguo puo essere confermato oppure no.

Esempi:

- `EXIT_BE` valido solo se esiste segnale originario e storia coerente;
- `UPDATE` valido solo se il messaggio ha `target_ref`, altrimenti `INFO_ONLY`.

### Pseudocodice

```python
intent_candidates = detect_intents_with_evidence(text)
detected_intents = [c.intent for c in intent_candidates]

compatibility = evaluate_intent_compatibility(detected_intents)

resolved_local = current_state(
    intent_candidates=intent_candidates,
    detected_intents=detected_intents,
)

if compatibility.requires_local_resolution:
    resolved_local = apply_disambiguation_rules(
        text_normalized=text_normalized,
        intent_candidates=resolved_local.intent_candidates,
        detected_intents=resolved_local.detected_intents,
    )

resolved_context = resolved_local
if compatibility.requires_context_validation:
    resolved_context = apply_context_resolution_rules(
        intent_candidates=resolved_local.intent_candidates,
        detected_intents=resolved_local.detected_intents,
        has_target_ref=has_target_ref,
        target_history_intents=target_history_intents,
        message_type_hint=message_type_hint,
    )

if compatibility.requires_resolution and not compatibility.resolved:
    add_warning("unresolved_intent_conflict")

primary_intent = select_primary_intent(resolved_context.detected_intents)
```

### Pseudocodice per messaggi multi-ref

```python
if resolution_unit == "MESSAGE_WIDE":
    semantic_result = resolve_semantics_for_message(text, context)
    targeted_actions = bind_message_semantics_to_targets(
        semantic_result=semantic_result,
        target_refs=target_refs,
    )

elif resolution_unit == "TARGET_ITEM_WIDE":
    items = extract_targeted_items(text, target_refs)
    resolved_items = []
    for item in items:
        resolved_items.append(
            resolve_semantics_for_item(
                item_text=item.text,
                item_target_ref=item.target_ref,
                target_history=item.target_history,
            )
        )
    targeted_actions = group_resolved_items(resolved_items)
```

### Sintesi decisionale

- se la matrice shared dice `compatible`, non si risolve nulla;
- se la matrice shared dice `requires_resolution`, prova `disambiguation_rules`;
- se la matrice shared dice `requires_context_validation`, prova anche `context_resolution_rules`;
- se il conflitto resta aperto, emetti warning.

## Input Minimo del Resolver

### Input locali

- `text_normalized`: testo lowercase normalizzato
- `intent_candidates`: lista dei candidati con intensita di evidenza
- `detected_intents`: lista piatta degli intent candidati
- `strong_intents`: intent con marker strong
- `weak_intents`: intent con marker weak

Shape consigliata per `intent_candidates`:

```json
[
  {
    "intent": "EXIT_BE",
    "strength": "strong",
    "evidence": ["marker: закрыта в бу"]
  },
  {
    "intent": "CLOSE_FULL",
    "strength": "weak",
    "evidence": ["marker: закрыта"]
  }
]
```

### Input contestuali

- `has_target_ref`: `true | false`
- `target_ref_kind`: `reply_id | telegram_link | explicit_id | global_scope | unknown`
- `target_exists`: `true | false`
- `target_history_intents`: lista intent storici del target
- `message_type_hint`: classificazione corrente pre-context

## Intent Ammessi Nelle Regole

Gli intent usati nelle regole devono essere intent ufficiali o alias legacy risolti dal taxonomy layer.

Intent ufficiali correnti:

- `NEW_SETUP`
- `MOVE_STOP_TO_BE`
- `MOVE_STOP`
- `CLOSE_FULL`
- `CLOSE_PARTIAL`
- `CANCEL_PENDING_ORDERS`
- `INVALIDATE_SETUP`
- `REENTER`
- `ADD_ENTRY`
- `UPDATE_TAKE_PROFITS`
- `ENTRY_FILLED`
- `TP_HIT`
- `SL_HIT`
- `EXIT_BE`
- `REPORT_FINAL_RESULT`
- `REPORT_PARTIAL_RESULT`
- `INFO_ONLY`

## `disambiguation_rules`

### Scopo

Risolvere conflitti locali tra intent rilevati nello stesso messaggio, senza leggere stato o storico del target.

### Shape proposta

```json
{
  "disambiguation_rules": {
    "rules": [
      {
        "name": "prefer_be_over_move_stop",
        "action": "prefer",
        "when_all_detected": ["MOVE_STOP_TO_BE", "MOVE_STOP"],
        "prefer": "MOVE_STOP_TO_BE",
        "if_contains_any": ["bu", "breakeven"]
      }
    ]
  }
}
```

### Campi supportati

- `name`: `string`, obbligatorio, identificatore diagnostico
- `action`: `prefer | suppress | keep_multi`, obbligatorio
- `when_all_detected`: `list[string]`, opzionale
- `when_any_detected`: `list[string]`, opzionale
- `if_contains_any`: `list[string]`, opzionale
- `unless_contains_any`: `list[string]`, opzionale

Campi specifici per azione:

- `prefer`: `string`, obbligatorio con `action = prefer`
- `suppress`: `list[string]`, obbligatorio con `action = suppress`
- `keep`: `list[string]`, opzionale con `action = keep_multi`

### Vincoli

1. Almeno una tra `when_all_detected` e `when_any_detected` deve essere presente.
2. `prefer` deve appartenere al set matchato.
3. Il match testuale di `if_contains_any` e `unless_contains_any` e substring semplice su `text_normalized`.
4. Questo blocco non puo leggere `target_ref` o `target_history`.
5. Questo blocco interviene solo se `intent_compatibility` segnala una coppia o gruppo da risolvere.

### Azioni supportate

#### `prefer`

Mantiene un intent preferito e sopprime gli altri intent del gruppo matchato.

Uso tipico:

- `MOVE_STOP_TO_BE` vs `MOVE_STOP`
- `EXIT_BE` vs `CLOSE_FULL` quando il testo gia chiarisce la specificita

#### `suppress`

Rimuove intent specifici se la condizione matcha.

Uso tipico:

- sopprimere `CLOSE_FULL` se il testo indica chiaramente `CLOSE_PARTIAL`

#### `keep_multi`

Dichiara che gli intent matchati possono convivere nel layer locale e non vanno soppressi automaticamente.

Uso tipico:

- `SL_HIT` + `CLOSE_FULL`
- `TP_HIT` + `REPORT_FINAL_RESULT`

## `context_resolution_rules`

### Scopo

Validare, promuovere o correggere intent che dipendono dal target reale del messaggio e dalla sua storia.

### Shape proposta

```json
{
  "context_resolution_rules": [
    {
      "name": "exit_be_requires_history",
      "action": "resolve_as",
      "when": {
        "has_weak_intent": "EXIT_BE",
        "has_target_ref": true
      },
      "if_target_history_has_any": ["NEW_SETUP", "MOVE_STOP_TO_BE", "MOVE_STOP"],
      "resolve_as": "EXIT_BE",
      "otherwise_resolve_as": "INFO_ONLY"
    }
  ]
}
```

### Campi supportati

- `name`: `string`, obbligatorio
- `action`: `promote | resolve_as | set_primary | suppress`, obbligatorio
- `when`: `object`, obbligatorio
- `if_target_history_has_any`: `list[string]`, opzionale
- `if_target_history_lacks_all`: `list[string]`, opzionale
- `if_target_exists`: `true | false`, opzionale

Campi ammessi dentro `when`:

- `has_weak_intent`: `string`, opzionale
- `has_strong_intent`: `string`, opzionale
- `has_any_intent`: `list[string]`, opzionale
- `has_target_ref`: `true | false`, opzionale
- `message_type_hint_in`: `list[string]`, opzionale

Campi specifici per azione:

- `intent`: `string`, obbligatorio con `action = promote`
- `resolve_as`: `string`, obbligatorio con `action = resolve_as`
- `primary`: `string`, obbligatorio con `action = set_primary`
- `suppress`: `list[string]`, obbligatorio con `action = suppress`
- `otherwise_resolve_as`: `string`, opzionale solo con `action = resolve_as`

### Vincoli

1. Almeno un segnale tra `has_weak_intent`, `has_strong_intent`, `has_any_intent` deve essere presente.
2. Il blocco puo usare solo dati gia risolti dal parser: `target_ref`, `target_history`, `message_type_hint`.
3. Non deve inventare intent senza evidenza testuale minima.
4. `otherwise_resolve_as` non deve forzare automaticamente intent operativi se il testo e narrativo; il fallback corretto puo essere `INFO_ONLY`.
5. Questo blocco interviene solo se richiesto dalla compatibilita oppure da intent noti come stateful.

### Azioni supportate

#### `promote`

Promuove un candidato weak a intent finale valido.

Uso tipico:

- un `EXIT_BE` weak diventa valido solo se il target ha storia coerente

#### `resolve_as`

Risolve un candidato ambiguo o debole come un intent finale specifico.

Uso tipico:

- un riferimento ambiguo a `BE` viene risolto come `EXIT_BE` solo se il target e coerente

#### `set_primary`

Forza il `primary_intent_hint` senza cancellare necessariamente gli altri intent compatibili.

Uso tipico:

- `SL_HIT` e `CLOSE_FULL` convivono, ma `SL_HIT` deve essere primario

#### `suppress`

Elimina intent che il contesto rende incoerenti.

Uso tipico:

- sopprimere `TP_HIT` se il target risulta gia chiuso prima del messaggio

## Intent Stateful Light

Non tutti gli intent hanno lo stesso livello di dipendenza dalla storia.

### Intent fortemente stateful

- `EXIT_BE`
- `TP_HIT`
- `SL_HIT`
- `CLOSE_FULL`
- `CLOSE_PARTIAL`

### Regola forte per `EXIT_BE`

`EXIT_BE` e valido solo se:

1. il messaggio risolve un target reale;
2. esiste un segnale originario;
3. lo storico del target contiene almeno uno tra:
- `MOVE_STOP_TO_BE`
- `MOVE_STOP`
- `NEW_SETUP`

Fallback consigliato:

- se il testo e istruttivo, fallback possibile `MOVE_STOP_TO_BE`
- se il testo e narrativo e manca storia coerente, fallback corretto `INFO_ONLY`

Questo evita di trasformare messaggi come `закрыта в бу` in una falsa istruzione operativa.

Nota di design:

- non introdurre `target_state` come asse obbligatorio in questa fase;
- usare solo `target_ref + target_history_intents`;
- se il contesto non basta, preferire fallback conservativi invece di aggiungere stato semantico complesso.

## Casi Guida

### `MOVE_STOP_TO_BE` vs `MOVE_STOP`

- layer: `disambiguation_rules`
- azione: `prefer`
- esito: `MOVE_STOP_TO_BE`
- motivo: intent piu specifico del generico `MOVE_STOP`

### `CLOSE_FULL` vs `EXIT_BE` nel testo `закрыта в бу`

- layer 1: `disambiguation_rules`
- azione: `prefer EXIT_BE` se il marker forte indica chiusura in BE
- layer 2: `context_resolution_rules`
- validazione: `EXIT_BE` resta valido solo con storia target coerente

### `SL_HIT` vs `CLOSE_FULL`

- layer: `disambiguation_rules`
- azione: `keep_multi`
- primary finale: `SL_HIT`
- motivo: `CLOSE_FULL` puo essere conseguenza, ma il segnale principale e lo stop hit

### `TP_HIT` vs `CLOSE_FULL`

- solo take colpito: `TP_HIT`
- take colpito + chiusura finale esplicita: `TP_HIT` compatibile con `CLOSE_FULL` o `REPORT_FINAL_RESULT`

### `UPDATE` vs `INFO_ONLY`

- layer: `context_resolution_rules`
- regola: un messaggio con lessico update-like ma senza target risolvibile degrada a `INFO_ONLY`

## Applicazione Nei Casi Multi-Ref

### Caso A: piu ref, stessa azione comune

Esempio:

- `1725`
- `1726`
- testo comune `пора перенести стоп в бу`

Applicazione:

1. `resolution_unit = MESSAGE_WIDE`
2. detect intent comune `MOVE_STOP_TO_BE`
3. `intent_compatibility`: nessun conflitto o conflitto locale semplice
4. `disambiguation_rules`: eventuale `prefer MOVE_STOP_TO_BE` su `MOVE_STOP`
5. `context_resolution_rules`: valida che sia davvero un update targettizzato
6. output finale: una sola azione comune bindata a piu refs

### Caso B: azione comune + report individuali

Esempio:

- piu righe con ref e risultato individuale
- una frase finale comune `chiudo tutto`

Applicazione:

1. ramo comune `MESSAGE_WIDE` per l'azione condivisa `CLOSE_FULL`
2. ramo `TARGET_ITEM_WIDE` per i risultati individuali per-ref
3. le regole semantiche si applicano separatamente ai due rami
4. output finale:
- un `targeted_action` comune
- piu `targeted_reports` individuali

### Caso C: azioni diverse per ref diversi

Esempio:

- quattro righe `stop in be`
- una riga `stop on tp1`

Applicazione:

1. `resolution_unit = TARGET_ITEM_WIDE`
2. una mini pipeline semantica per ogni riga
3. `disambiguation_rules` per riga:
- `stop in be` -> `MOVE_STOP_TO_BE`
- `stop on tp1` -> `MOVE_STOP`
4. `context_resolution_rules` per riga solo se l'intent lo richiede
5. grouping finale per firma:
- `SET_STOP ENTRY` su gruppo di refs
- `SET_STOP TP_LEVEL 1` su altro gruppo

## Perimetro Minimalista

Per mantenere il sistema leggero:

1. niente `target_state` nel contratto iniziale;
2. niente nuovo asse `COMMAND | EVENT | REPORT` come campo obbligatorio;
3. centralizzare le relazioni intent in `intent_compatibility`;
4. usare solo:
- `intent_candidates`
- `target_ref`
- `target_history_intents`
- `message_type_hint`
5. fallback conservativo a `INFO_ONLY` quando il contesto non basta.

Questo copre i casi ad alto valore senza irrigidire troppo il modello.

## Diagnostica Obbligatoria

Il resolver deve lasciare traccia esplicita in `diagnostics`.

Campi minimi:

- `intents_before_disambiguation`
- `intents_after_disambiguation`
- `intents_after_context_resolution`
- `applied_disambiguation_rules`
- `applied_context_rules`
- `primary_intent_reason`

Questo rende ogni decisione auditabile.

## Piano Implementativo

1. Estendere `disambiguation_rules` con `rules[]`, `action`, `prefer`, `suppress`, `keep_multi`.
2. Introdurre `intent_compatibility` shared come matrice centrale.
3. Introdurre `intent_candidates` con `strength` ed `evidence`.
4. Implementare `shared/context_resolution.py` usando solo `target_ref + target_history_intents`.
5. Abilitare `context_resolution_rules` nel runtime condiviso.
6. Aggiungere test tabellari per compatibilita, conflitti e regole stateful light.

## Criteri di Accettazione

1. Nessuna regola duplicata tra layer locale e layer contestuale.
2. A parita di input, l'output degli intent e deterministico.
3. Gli intent stateful non vengono emessi senza storia coerente.
4. Ogni risoluzione non banale e spiegata in `diagnostics`.
5. Regressioni coperte per i casi chiave:
- `MOVE_STOP_TO_BE` vs `MOVE_STOP`
- `CLOSE_FULL` vs `EXIT_BE`
- `SL_HIT` vs `CLOSE_FULL`
- `TP_HIT` vs `CLOSE_FULL`
- `UPDATE` vs `INFO_ONLY`
6. Le combinazioni che richiedono risoluzione ma non trovano regola generano warning esplicito.
