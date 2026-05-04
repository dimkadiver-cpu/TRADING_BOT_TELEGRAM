# PRD — Validazione Intent Multilivello e Gestione Multi‑Target per Nuovo Parser

**Repository:** `dimkadiver-cpu/TRADING_BOT_TELEGRAM`  
**Area:** nuovo parser / `ParsedMessage` / `trader_a` in migrazione  
**Versione:** v1.0  
**Data:** 2026-04-30  
**Stato:** proposta tecnica dettagliata

---

## 1. Contesto

Il parser attuale è in fase di migrazione da un sistema legacy basato su:

```text
TraderParseResult
message_type
intents legacy U_*
parse_results
```

a un sistema nuovo basato su:

```text
ParsedMessage
primary_class
IntentResult
CanonicalMessage
parsed_messages
parse_results_v1
```

Nel nuovo modello, ogni intent può essere rappresentato con:

```json
{
  "type": "MOVE_STOP_TO_BE",
  "category": "UPDATE",
  "status": "CANDIDATE",
  "valid_refs": [],
  "invalid_refs": [],
  "invalid_reason": null
}
```

Il modello dati è già parzialmente pronto, ma la pipeline di validazione non è ancora sufficiente per debug manuale affidabile e per gestione robusta di messaggi multi-target.

Il problema non è solo “trovare un marker nel testo”. Il parser deve distinguere:

```text
intent rilevato
intent con entità sufficienti
intent con target valido
intent compatibile con altri intent nello stesso messaggio
intent coerente con lo storico del segnale
intent realmente usabile downstream
```

---

## 2. Obiettivo del PRD

Definire una pipeline di validazione intent a più livelli per il nuovo parser, con particolare attenzione a:

1. Validazione degli intent candidate.
2. Validazione delle entità minime richieste per ogni intent.
3. Validazione del targeting.
4. Disambiguazione tra intent concorrenti.
5. Validazione dello storico del segnale.
6. Gestione di messaggi con più target/reply/link.
7. Output diagnostico leggibile nel nuovo report debug.
8. Separazione netta tra detection, extraction, validation, disambiguation e translation.

---

## 3. Non obiettivi

Questo PRD **non** copre:

```text
- esecuzione ordini su exchange
- gestione lifecycle downstream nel trading engine
- PnL / backtesting
- refactoring completo di tutti i trader
- eliminazione immediata del parser legacy
- UI finale del bot
```

Il focus è solo sul nuovo parser e sulla validazione degli intent prima della traduzione in `CanonicalMessage`.

---

## 4. Problemi attuali

### 4.1 Extractor troppo permissivo

Oggi l’extractor può rilevare intent sulla base di marker forti/deboli, ma non deve decidere se sono realmente usabili.

Regola desiderata:

```text
Extractor = produce CANDIDATE
Validator = decide CONFIRMED / INVALID
```

### 4.2 Validator history-based non sufficiente

Il validator attuale verifica soprattutto se lo storico del segnale consente l’intent.

Esempio:

```json
{
  "intent": "TP_HIT",
  "requires_all_history": ["NEW_SIGNAL"],
  "excludes_any_history": ["CLOSE_FULL", "EXIT_BE", "INVALIDATE_SETUP", "SL_HIT"],
  "invalid_reason": "no_open_signal"
}
```

Limiti:

```text
- non valida le entità obbligatorie
- non valida bene tutti i tipi di target
- conferma automaticamente intent senza regola
- conferma automaticamente intent senza refs in alcuni casi
- supporta solo alcuni intent
- non produce diagnostics abbastanza dettagliati
```

### 4.3 Targeting incompleto

Il validator attuale tratta correttamente solo ref normalizzati come `MESSAGE_ID`.

Ma nel parser possono esistere target come:

```text
REPLY
TELEGRAM_LINK
MESSAGE_ID
EXPLICIT_ID
GLOBAL_SCOPE
SELECTOR
SYMBOL_SCOPE
```

Il validator deve normalizzare questi riferimenti e validarli per target.

### 4.4 Messaggi multi-target

Esempio:

```text
стоп в бу:
https://t.me/c/123/2110
https://t.me/c/123/2111
https://t.me/c/123/2112
```

Non basta dire:

```json
{
  "intent": "MOVE_STOP_TO_BE",
  "status": "CONFIRMED"
}
```

Serve sapere:

```text
quali target sono validi
quali target sono invalidi
quale azione si applica a quale target
```

### 4.5 Messaggi con azioni diverse per target diversi

Esempio:

```text
https://t.me/c/123/2110 стоп в бу
https://t.me/c/123/2111 закрываю по текущим
https://t.me/c/123/2112 тейк взят
```

Qui serve mapping per riga:

```text
target 2110 -> MOVE_STOP_TO_BE
target 2111 -> CLOSE_FULL
target 2112 -> TP_HIT
```

Non basta un targeting globale.

### 4.6 Disambiguation che rimuove intent

La disambiguation attuale, in alcuni casi, rimuove intent dalla lista.

Per debug manuale questo è debole: l’utente deve vedere anche cosa è stato scartato e perché.

Preferibile:

```json
{
  "type": "MOVE_STOP",
  "status": "INVALID",
  "invalid_reason": "suppressed_by_rule:prefer_move_stop_to_be_over_move_stop"
}
```

---

## 5. Principi architetturali

### 5.1 Separazione responsabilità

```text
Markers / rules detection
  -> trovano possibili intent

Extractors
  -> estraggono entità raw e payload

Entity Validator
  -> controlla dati minimi

Target Validator
  -> controlla target e refs

Disambiguation Engine
  -> risolve conflitti fra intent

History Validator
  -> controlla stato/storico del segnale

Primary Intent Resolver
  -> sceglie l’intent principale

Translator
  -> traduce solo intent confermati
```

### 5.2 L’extractor non conferma

Ogni intent prodotto dall’extractor deve uscire così:

```json
{
  "status": "CANDIDATE"
}
```

Non così:

```json
{
  "status": "CONFIRMED"
}
```

### 5.3 Non perdere informazioni

Intent scartati o soppressi non devono sparire, salvo modalità compat legacy.

Devono diventare:

```json
{
  "status": "INVALID",
  "invalid_reason": "..."
}
```

### 5.4 Validazione per target

Con più target, la validazione deve avvenire per target, non solo per intent.

Output minimo:

```json
{
  "valid_refs": [2110, 2112],
  "invalid_refs": [2111],
  "invalid_reason": "some_targets_invalid:no_open_signal"
}
```

### 5.5 Downstream solo su `valid_refs`

Il translator e l’engine downstream devono applicare azioni solo ai target validi.

---

## 6. Pipeline proposta

```text
Raw Telegram Message
  ↓
Profile Detection / RulesEngine
  ↓
Trader-specific Extractors
  ↓
IntentResult[] status=CANDIDATE
  ↓
Entity Validation
  ↓
Target Extraction + Target Normalization
  ↓
Target Validation
  ↓
Lexical / Semantic Disambiguation
  ↓
History Validation
  ↓
Primary Intent Precedence
  ↓
ParsedMessage final
  ↓
IntentTranslator
  ↓
CanonicalMessage
```

---

## 7. Stati intent

### 7.1 Stati supportati oggi

```text
CANDIDATE
CONFIRMED
INVALID
```

### 7.2 Regola per multi-target parzialmente valido

Non introdurre subito `PARTIALLY_CONFIRMED`.

Usare:

```json
{
  "status": "CONFIRMED",
  "valid_refs": [2110, 2112],
  "invalid_refs": [2111],
  "invalid_reason": "some_targets_invalid:no_open_signal"
}
```

Motivo: è compatibile con l’enum attuale e permette già execution solo su `valid_refs`.

### 7.3 Possibile evoluzione futura

In futuro si può aggiungere:

```text
PARTIALLY_CONFIRMED
```

ma richiede aggiornamento di:

```text
ParsedMessage
report debug
translator
test
DB/export
```

Non è necessario nella prima fase.

---

## 8. Livelli di validazione

## 8.1 Livello 1 — Detection Validation

### Scopo

Verificare che l’intent candidate abbia una base rilevabile:

```text
marker forte
marker debole
regex match
fallback esplicito
```

### Output diagnostico

```json
{
  "detection": {
    "result": "MATCHED",
    "strength": "strong",
    "marker": "стоп в бу",
    "source": "intent_markers"
  }
}
```

### Regola

Il detection layer non invalida quasi mai da solo, ma deve lasciare traccia del motivo della rilevazione.

---

## 8.2 Livello 2 — Entity Validation

### Scopo

Verificare che l’intent abbia le entità minime per essere usabile.

### Esempi

```json
{
  "MOVE_STOP": {
    "requires_any_entity": ["new_stop_price", "stop_to_tp_level"]
  },
  "CLOSE_PARTIAL": {
    "requires_any_entity": ["fraction", "close_price"]
  },
  "ADD_ENTRY": {
    "requires_all_entities": ["entry_price"]
  },
  "UPDATE_TAKE_PROFITS": {
    "requires_any_entity": ["new_take_profits", "target_tp_level", "mode"]
  }
}
```

### Output se fallisce

```json
{
  "type": "MOVE_STOP",
  "status": "INVALID",
  "invalid_reason": "missing_required_entity:new_stop_price|stop_to_tp_level"
}
```

### Regole specifiche consigliate

| Intent | Requisito minimo |
|---|---|
| `MOVE_STOP_TO_BE` | nessuna entità obbligatoria |
| `MOVE_STOP` | `new_stop_price` oppure `stop_to_tp_level` |
| `CLOSE_FULL` | nessuna entità obbligatoria |
| `CLOSE_PARTIAL` | `fraction` oppure `close_price` |
| `CANCEL_PENDING` | opzionale `scope` |
| `INVALIDATE_SETUP` | nessuna entità obbligatoria |
| `REENTER` | almeno una entry |
| `ADD_ENTRY` | `entry_price` |
| `UPDATE_TAKE_PROFITS` | nuovi TP oppure mode/target |
| `TP_HIT` | opzionale `level`, opzionale `price`, opzionale `result` |
| `SL_HIT` | opzionale `price`, opzionale `result` |
| `EXIT_BE` | opzionale `price` |
| `REPORT_FINAL_RESULT` | `result` consigliato, ma non sempre obbligatorio |
| `REPORT_PARTIAL_RESULT` | `result` consigliato |

---

## 8.3 Livello 3 — Target Validation

### Scopo

Verificare se l’intent ha un target sufficiente.

### Tipi target ammessi

```text
REPLY
MESSAGE_ID
TELEGRAM_LINK
EXPLICIT_ID
TARGET_GROUP
GLOBAL_SCOPE
SELECTOR
SYMBOL_SCOPE
```

### Regole generali

| Classe intent | Target richiesto |
|---|---|
| `UPDATE` operativo | sì |
| `REPORT` collegato a trade | sì, salvo report aggregato esplicito |
| `SIGNAL` | no |
| `INFO` | no |
| Global command | sì, ma può essere `GLOBAL_SCOPE` |
| Portfolio-wide command | sì, ma può essere `SELECTOR` o `GLOBAL_SCOPE` |

### Esempi

#### Reply singola

```json
{
  "strategy": "REPLY_OR_LINK",
  "refs": [
    {
      "ref_type": "REPLY",
      "value": 2110
    }
  ],
  "scope": {
    "kind": "SINGLE_SIGNAL"
  }
}
```

#### Multi-link

```json
{
  "strategy": "REPLY_OR_LINK",
  "refs": [
    {
      "ref_type": "TELEGRAM_LINK",
      "value": 2110
    },
    {
      "ref_type": "TELEGRAM_LINK",
      "value": 2111
    }
  ],
  "scope": {
    "kind": "TARGET_GROUP"
  }
}
```

#### Global scope

```json
{
  "strategy": "GLOBAL_SCOPE",
  "refs": [],
  "scope": {
    "kind": "PORTFOLIO_SIDE",
    "side_filter": "LONG",
    "applies_to_all": true
  }
}
```

### Output se target mancante

```json
{
  "status": "INVALID",
  "invalid_reason": "missing_target"
}
```

---

## 8.4 Livello 4 — Disambiguation

### Scopo

Risolvere conflitti tra intent rilevati nello stesso messaggio.

### Azioni supportate

```text
prefer
suppress
keep_multi
```

### Azioni consigliate future

```text
mark_invalid
downgrade_to_candidate
```

### Regole tipiche

```json
{
  "name": "prefer_move_stop_to_be_over_move_stop",
  "action": "prefer",
  "when_all_detected": ["MOVE_STOP_TO_BE", "MOVE_STOP"],
  "prefer": "MOVE_STOP_TO_BE",
  "over": ["MOVE_STOP"]
}
```

Output desiderato:

```json
{
  "intents": [
    {
      "type": "MOVE_STOP_TO_BE",
      "status": "CONFIRMED"
    },
    {
      "type": "MOVE_STOP",
      "status": "INVALID",
      "invalid_reason": "suppressed_by_rule:prefer_move_stop_to_be_over_move_stop"
    }
  ]
}
```

### Regole consigliate iniziali

```json
{
  "disambiguation_rules": {
    "rules": [
      {
        "name": "prefer_move_stop_to_be_over_move_stop",
        "action": "prefer",
        "priority": 100,
        "when_all_detected": ["MOVE_STOP_TO_BE", "MOVE_STOP"],
        "prefer": "MOVE_STOP_TO_BE",
        "over": ["MOVE_STOP"]
      },
      {
        "name": "prefer_exit_be_over_close_full",
        "action": "prefer",
        "priority": 90,
        "when_all_detected": ["EXIT_BE", "CLOSE_FULL"],
        "if_contains_any": ["ушел в бу", "закрылся в бу", "закрылась в безубыток", "breakeven", "безубыток"],
        "prefer": "EXIT_BE",
        "over": ["CLOSE_FULL"]
      },
      {
        "name": "suppress_close_full_if_close_partial",
        "action": "suppress",
        "priority": 80,
        "when_all_detected": ["CLOSE_FULL", "CLOSE_PARTIAL"],
        "suppress": ["CLOSE_FULL"]
      },
      {
        "name": "keep_tp_hit_and_move_stop_to_be",
        "action": "keep_multi",
        "priority": 70,
        "when_all_detected": ["TP_HIT", "MOVE_STOP_TO_BE"],
        "keep": ["TP_HIT", "MOVE_STOP_TO_BE"]
      },
      {
        "name": "keep_tp_hit_and_report_final_result",
        "action": "keep_multi",
        "priority": 60,
        "when_all_detected": ["TP_HIT", "REPORT_FINAL_RESULT"],
        "keep": ["TP_HIT", "REPORT_FINAL_RESULT"]
      },
      {
        "name": "suppress_close_full_if_sl_hit_without_explicit_close",
        "action": "suppress",
        "priority": 50,
        "when_all_detected": ["SL_HIT", "CLOSE_FULL"],
        "unless_contains_any": ["закрываю", "закрываем", "close"],
        "suppress": ["CLOSE_FULL"]
      }
    ]
  }
}
```

---

## 8.5 Livello 5 — History Validation

### Scopo

Verificare se l’intent è coerente con lo storico del segnale target.

### Regole base

```json
{
  "intent": "TP_HIT",
  "requires_all_history": ["NEW_SIGNAL"],
  "excludes_any_history": ["CLOSE_FULL", "EXIT_BE", "INVALIDATE_SETUP", "SL_HIT"],
  "invalid_reason": "no_open_signal"
}
```

### Logica

Per ogni target:

```text
1. risolvi ref -> telegram_message_id
2. ricostruisci history
3. applica validation_rules
4. aggiungi a valid_refs o invalid_refs
```

### Output

```json
{
  "type": "TP_HIT",
  "status": "CONFIRMED",
  "valid_refs": [2110],
  "invalid_refs": [],
  "invalid_reason": null
}
```

oppure:

```json
{
  "type": "TP_HIT",
  "status": "INVALID",
  "valid_refs": [],
  "invalid_refs": [2110],
  "invalid_reason": "no_open_signal"
}
```

oppure multi-target parziale:

```json
{
  "type": "MOVE_STOP_TO_BE",
  "status": "CONFIRMED",
  "valid_refs": [2110, 2112],
  "invalid_refs": [2111],
  "invalid_reason": "some_targets_invalid:no_open_signal"
}
```

---

## 9. Gestione messaggi multi-target

## 9.1 Definizioni

### Single target

```text
un intent
un target
```

Esempio:

```text
reply a 2110: стоп в бу
```

### Message-wide multi-target

```text
una stessa azione
più target
```

Esempio:

```text
стоп в бу:
https://t.me/c/123/2110
https://t.me/c/123/2111
https://t.me/c/123/2112
```

### Target-item-wide

```text
ogni riga ha target e azione/report propri
```

Esempio:

```text
https://t.me/c/123/2110 стоп в бу
https://t.me/c/123/2111 закрываю по текущим
https://t.me/c/123/2112 тейк взят
```

### Mixed targeted

```text
messaggio contiene più target e più intent
ma non è chiaro quale intent appartiene a quale target
```

Esempio:

```text
2110 2111 2112
стоп в бу, закрываю частично
```

---

## 9.2 Campo diagnostico proposto

Aggiungere a `ParsedMessage.diagnostics`:

```json
{
  "targeting_analysis": {
    "mode": "SINGLE_TARGET | MESSAGE_WIDE | TARGET_ITEM_WIDE | MIXED_TARGETED",
    "target_count": 3,
    "target_refs": [2110, 2111, 2112],
    "mapping_confidence": 0.92
  }
}
```

---

## 9.3 Message-wide multi-target

Input:

```text
стоп в бу:
https://t.me/c/123/2110
https://t.me/c/123/2111
https://t.me/c/123/2112
```

Output `ParsedMessage`:

```json
{
  "primary_class": "UPDATE",
  "composite": false,
  "intents": [
    {
      "type": "MOVE_STOP_TO_BE",
      "category": "UPDATE",
      "status": "CONFIRMED",
      "valid_refs": [2110, 2111, 2112],
      "invalid_refs": [],
      "invalid_reason": null
    }
  ],
  "targeting": {
    "strategy": "REPLY_OR_LINK",
    "scope": {
      "kind": "TARGET_GROUP"
    },
    "refs": [
      {
        "ref_type": "TELEGRAM_LINK",
        "value": 2110
      },
      {
        "ref_type": "TELEGRAM_LINK",
        "value": 2111
      },
      {
        "ref_type": "TELEGRAM_LINK",
        "value": 2112
      }
    ],
    "targeted": true
  },
  "diagnostics": {
    "targeting_analysis": {
      "mode": "MESSAGE_WIDE",
      "target_count": 3,
      "target_refs": [2110, 2111, 2112]
    }
  }
}
```

Output `CanonicalMessage`:

```json
{
  "primary_class": "UPDATE",
  "targeted_actions": [
    {
      "action_type": "SET_STOP",
      "params": {
        "target_type": "ENTRY"
      },
      "targeting": {
        "mode": "TARGET_GROUP",
        "targets": [2110, 2111, 2112]
      },
      "diagnostics": {
        "resolution_unit": "MESSAGE_WIDE",
        "semantic_signature": "SET_STOP:ENTRY",
        "grouping_reason": "same_action_multiple_targets"
      }
    }
  ]
}
```

---

## 9.4 Target-item-wide

Input:

```text
https://t.me/c/123/2110 стоп в бу
https://t.me/c/123/2111 закрываю по текущим
https://t.me/c/123/2112 тейк взят
```

Output `ParsedMessage`:

```json
{
  "primary_class": "UPDATE",
  "composite": true,
  "intents": [
    {
      "type": "MOVE_STOP_TO_BE",
      "category": "UPDATE",
      "status": "CONFIRMED",
      "valid_refs": [2110],
      "raw_fragment": "https://t.me/c/123/2110 стоп в бу"
    },
    {
      "type": "CLOSE_FULL",
      "category": "UPDATE",
      "status": "CONFIRMED",
      "valid_refs": [2111],
      "raw_fragment": "https://t.me/c/123/2111 закрываю по текущим"
    },
    {
      "type": "TP_HIT",
      "category": "REPORT",
      "status": "CONFIRMED",
      "valid_refs": [2112],
      "raw_fragment": "https://t.me/c/123/2112 тейк взят"
    }
  ],
  "diagnostics": {
    "targeting_analysis": {
      "mode": "TARGET_ITEM_WIDE",
      "target_count": 3,
      "target_refs": [2110, 2111, 2112],
      "mapping_confidence": 0.95
    }
  }
}
```

Output `CanonicalMessage`:

```json
{
  "targeted_actions": [
    {
      "action_type": "SET_STOP",
      "params": {
        "target_type": "ENTRY"
      },
      "targeting": {
        "mode": "EXPLICIT_TARGETS",
        "targets": [2110]
      }
    },
    {
      "action_type": "CLOSE",
      "params": {
        "close_scope": "FULL"
      },
      "targeting": {
        "mode": "EXPLICIT_TARGETS",
        "targets": [2111]
      }
    }
  ],
  "targeted_reports": [
    {
      "event_type": "TP_HIT",
      "targeting": {
        "mode": "EXPLICIT_TARGETS",
        "targets": [2112]
      }
    }
  ]
}
```

---

## 9.5 Mixed targeted

Input:

```text
2110 2111 2112
стоп в бу, закрываю частично
```

Problema:

```text
non è chiaro se entrambe le azioni valgono per tutti
o se una azione vale per alcuni target
```

Regola proposta:

```text
se più intent operativi + più target
e non esiste mapping per riga
allora:
- non applicare direttamente
- status intent = CANDIDATE oppure INVALID
- parse_status = PARTIAL
- warning = ambiguous_multi_target_mapping
- mandare a review/debug
```

Output:

```json
{
  "primary_class": "UPDATE",
  "parse_status": "PARTIAL",
  "composite": true,
  "warnings": ["ambiguous_multi_target_mapping"],
  "diagnostics": {
    "targeting_analysis": {
      "mode": "MIXED_TARGETED",
      "target_count": 3,
      "target_refs": [2110, 2111, 2112],
      "mapping_confidence": 0.35
    }
  }
}
```

---

## 10. `validation_rules.json` proposto

```json
{
  "rules": [
    {
      "intent": "TP_HIT",
      "category": "REPORT",
      "requires_target": true,
      "requires_all_history": ["NEW_SIGNAL"],
      "excludes_any_history": ["CLOSE_FULL", "EXIT_BE", "INVALIDATE_SETUP", "SL_HIT"],
      "invalid_reason": "no_open_signal"
    },
    {
      "intent": "SL_HIT",
      "category": "REPORT",
      "requires_target": true,
      "requires_all_history": ["NEW_SIGNAL"],
      "excludes_any_history": ["CLOSE_FULL", "EXIT_BE", "INVALIDATE_SETUP", "SL_HIT"],
      "invalid_reason": "no_open_signal"
    },
    {
      "intent": "EXIT_BE",
      "category": "REPORT",
      "requires_target": true,
      "requires_all_history": ["NEW_SIGNAL"],
      "requires_any_history": ["MOVE_STOP", "MOVE_STOP_TO_BE"],
      "excludes_any_history": ["CLOSE_FULL", "EXIT_BE", "INVALIDATE_SETUP", "SL_HIT"],
      "invalid_reason": "no_open_signal_or_no_stop_moved"
    },
    {
      "intent": "MOVE_STOP_TO_BE",
      "category": "UPDATE",
      "requires_target": true,
      "requires_all_history": ["NEW_SIGNAL"],
      "excludes_any_history": ["CLOSE_FULL", "EXIT_BE", "INVALIDATE_SETUP", "SL_HIT"],
      "invalid_reason": "no_open_signal"
    },
    {
      "intent": "MOVE_STOP",
      "category": "UPDATE",
      "requires_target": true,
      "requires_any_entity": ["new_stop_price", "stop_to_tp_level"],
      "requires_all_history": ["NEW_SIGNAL"],
      "excludes_any_history": ["CLOSE_FULL", "EXIT_BE", "INVALIDATE_SETUP", "SL_HIT"],
      "invalid_reason": "no_open_signal_or_missing_stop_target"
    },
    {
      "intent": "CLOSE_FULL",
      "category": "UPDATE",
      "requires_target": true,
      "requires_all_history": ["NEW_SIGNAL"],
      "excludes_any_history": ["CLOSE_FULL", "EXIT_BE", "INVALIDATE_SETUP", "SL_HIT"],
      "invalid_reason": "no_open_signal"
    },
    {
      "intent": "CLOSE_PARTIAL",
      "category": "UPDATE",
      "requires_target": true,
      "requires_any_entity": ["fraction", "close_price"],
      "requires_all_history": ["NEW_SIGNAL"],
      "excludes_any_history": ["CLOSE_FULL", "EXIT_BE", "INVALIDATE_SETUP", "SL_HIT"],
      "invalid_reason": "no_open_signal_or_missing_partial_close_data"
    },
    {
      "intent": "CANCEL_PENDING",
      "category": "UPDATE",
      "requires_target": true,
      "requires_all_history": ["NEW_SIGNAL"],
      "excludes_any_history": ["CLOSE_FULL", "EXIT_BE", "INVALIDATE_SETUP", "SL_HIT"],
      "invalid_reason": "no_open_signal"
    },
    {
      "intent": "INVALIDATE_SETUP",
      "category": "UPDATE",
      "requires_target": true,
      "requires_all_history": ["NEW_SIGNAL"],
      "excludes_any_history": ["CLOSE_FULL", "EXIT_BE", "INVALIDATE_SETUP", "SL_HIT"],
      "invalid_reason": "no_open_signal"
    },
    {
      "intent": "ENTRY_FILLED",
      "category": "REPORT",
      "requires_target": true,
      "requires_all_history": ["NEW_SIGNAL"],
      "excludes_any_history": ["CLOSE_FULL", "EXIT_BE", "INVALIDATE_SETUP", "SL_HIT"],
      "invalid_reason": "no_open_signal"
    },
    {
      "intent": "REPORT_FINAL_RESULT",
      "category": "REPORT",
      "requires_target": false,
      "invalid_reason": "invalid_final_result_context"
    }
  ]
}
```

---

## 11. `rules.json` ruolo proposto

`rules.json` non deve contenere regex o marker principali.

Deve contenere:

```text
disambiguation_rules
primary_intent_precedence
action_scope_groups
```

### Esempio

```json
{
  "primary_intent_precedence": [
    "CLOSE_FULL",
    "CLOSE_PARTIAL",
    "MOVE_STOP_TO_BE",
    "MOVE_STOP",
    "CANCEL_PENDING",
    "INVALIDATE_SETUP",
    "REENTER",
    "ADD_ENTRY",
    "UPDATE_TAKE_PROFITS",
    "SL_HIT",
    "EXIT_BE",
    "TP_HIT",
    "ENTRY_FILLED",
    "REPORT_FINAL_RESULT",
    "REPORT_PARTIAL_RESULT",
    "INFO_ONLY"
  ],
  "action_scope_groups": {
    "all_positions": ["ALL_POSITIONS", "ALL_OPEN", "ALL_REMAINING"],
    "all_long": ["ALL_LONGS"],
    "all_short": ["ALL_SHORTS"]
  }
}
```

---

## 12. Impatto su `ParsedMessage`

### 12.1 Nessuna modifica obbligatoria immediata

Si può usare il modello esistente:

```text
status = CANDIDATE | CONFIRMED | INVALID
valid_refs
invalid_refs
invalid_reason
diagnostics
```

### 12.2 Modifiche future opzionali

Aggiungere:

```text
PARTIALLY_CONFIRMED
```

Aggiungere per-intent diagnostics strutturato:

```json
{
  "validation": {
    "entities": {},
    "target": {},
    "history": {},
    "disambiguation": {}
  }
}
```

Per ora è sufficiente mettere i dettagli in `ParsedMessage.diagnostics`.

---

## 13. Impatto su `CanonicalMessage`

Il translator deve usare solo:

```text
intent.status == CONFIRMED
```

e solo:

```text
intent.valid_refs
```

per azioni targeted.

Se un intent ha:

```json
{
  "valid_refs": [2110, 2112],
  "invalid_refs": [2111]
}
```

il canonical deve generare azioni solo per:

```text
2110
2112
```

e lasciare `2111` nei diagnostics/report.

---

## 14. Impatto sul report debug nuovo

Il report deve mostrare:

### 14.1 Per messaggio

```text
raw_message_id
trader_id
primary_class
parse_status
validation_status
composite
primary_intent
confirmed_intents
candidate_intents
invalid_intents
targeting_mode
target_count
warnings
```

### 14.2 Per intent

```text
raw_message_id
intent_type
category
status
detection_strength
confidence
valid_refs
invalid_refs
invalid_reason
raw_fragment
entities_json
```

### 14.3 Per target validation

```text
raw_message_id
intent_type
target_ref
target_status
target_invalid_reason
history
```

### 14.4 Casi evidenziati

```text
- invalid intent
- candidate non confermati
- multi-target con invalid_refs
- mixed targeted ambiguous
- parse_status PARTIAL
- missing_target
- missing_required_entity
```

---

## 15. Piano di implementazione

## Fase 1 — Entity validation

### Obiettivo

Aggiungere validazione entità minime.

### File probabili

```text
src/parser/intent_validator/validator.py
src/parser/intent_validator/validation_rules.json
src/parser/tests/test_phase5_intent_validator.py
```

### Task

```text
1. Estendere loader rules per leggere:
   - requires_any_entity
   - requires_all_entities
   - requires_target

2. Implementare helper:
   - _entity_present(intent, field)
   - _validate_required_entities(intent, rule)

3. Se mancano entità:
   - status INVALID
   - invalid_reason missing_required_entity:...

4. Aggiungere tests.
```

### Acceptance criteria

```text
MOVE_STOP senza new_stop_price e senza stop_to_tp_level => INVALID
CLOSE_PARTIAL senza fraction/close_price => INVALID
MOVE_STOP_TO_BE senza entità => può essere CONFIRMED
```

---

## Fase 2 — Target normalization

### Obiettivo

Normalizzare target refs in forma validabile.

### File probabili

```text
src/parser/intent_validator/validator.py
src/parser/intent_validator/history_provider.py
src/parser/shared/runtime.py
```

### Task

```text
1. Supportare REPLY come ref storico.
2. Supportare TELEGRAM_LINK estraendo message_id.
3. Supportare MESSAGE_ID.
4. Non confermare automaticamente intent operativo senza target.
5. Gestire GLOBAL_SCOPE separatamente.
```

### Acceptance criteria

```text
REPLY 2110 validato contro history
TELEGRAM_LINK /2110 validato contro history
UPDATE senza target => INVALID missing_target
GLOBAL_SCOPE non richiede refs singole, ma richiede scope valido
```

---

## Fase 3 — History validation per target multipli

### Obiettivo

Validare ogni target separatamente.

### Task

```text
1. Per ogni ref:
   - caricare history
   - applicare rule
   - aggiungere a valid_refs o invalid_refs

2. Se valid_refs non vuoto:
   - status CONFIRMED
   - invalid_reason valorizzato solo se ci sono invalid_refs

3. Se valid_refs vuoto e invalid_refs non vuoto:
   - status INVALID
```

### Acceptance criteria

```text
target 2110 valido, 2111 invalido:
status CONFIRMED
valid_refs [2110]
invalid_refs [2111]
invalid_reason some_targets_invalid:...
```

---

## Fase 4 — Disambiguation conservativa

### Obiettivo

Non rimuovere intent scartati; marcarli invalidi.

### File probabili

```text
src/parser/shared/disambiguation.py
src/parser/shared/disambiguation_rules_schema.py
```

### Task

```text
1. Per prefer:
   - intent preferito resta
   - intent over => INVALID

2. Per suppress:
   - intent suppressi => INVALID

3. Aggiungere invalid_reason:
   - suppressed_by_rule:<rule_name>
   - preferred_other_intent:<rule_name>

4. Diagnostics:
   - applied_disambiguation_rules
   - suppressed_intents
   - invalidated_intents
```

### Acceptance criteria

```text
MOVE_STOP_TO_BE + MOVE_STOP:
MOVE_STOP_TO_BE CONFIRMED/CANDIDATE
MOVE_STOP INVALID suppressed_by_rule:...
```

---

## Fase 5 — Multi-target message analysis

### Obiettivo

Classificare targeting mode:

```text
SINGLE_TARGET
MESSAGE_WIDE
TARGET_ITEM_WIDE
MIXED_TARGETED
```

### Task

```text
1. Analizzare righe del messaggio.
2. Se una azione + più link => MESSAGE_WIDE.
3. Se ogni riga ha link + intent => TARGET_ITEM_WIDE.
4. Se più intent + più target senza mapping => MIXED_TARGETED.
5. In MIXED_TARGETED:
   - parse_status PARTIAL
   - warnings ambiguous_multi_target_mapping
   - non applicare downstream
```

### Acceptance criteria

```text
3 link + "стоп в бу" => MESSAGE_WIDE
3 righe con 3 azioni diverse => TARGET_ITEM_WIDE
3 target + 2 azioni non mappate => PARTIAL + warning
```

---

## Fase 6 — Report debug

### Obiettivo

Rendere visibile tutto il percorso di validazione.

### File nuovi suggeriti

```text
parser_test/reporting_new_parser/
  export.py
  flatten.py
  html.py
  schema.py

parser_test/scripts/generate_new_parser_debug_report.py
```

### Output

```text
messages.csv
intents.csv
target_validation.csv
warnings.csv
raw_debug.jsonl
index.html
```

### Acceptance criteria

```text
Per ogni intent si vede:
- status
- valid_refs
- invalid_refs
- invalid_reason
- raw_fragment
- target validation
- history validation
```

---

## 16. Test richiesti

## 16.1 Entity validation

```text
test_move_stop_missing_target_price_invalid
test_close_partial_missing_fraction_invalid
test_move_stop_to_be_no_entity_ok
```

## 16.2 Target validation

```text
test_update_without_target_invalid
test_reply_target_validated
test_telegram_link_target_validated
test_global_scope_target_valid
```

## 16.3 History validation

```text
test_tp_hit_without_new_signal_invalid
test_tp_hit_after_closed_signal_invalid
test_move_stop_to_be_on_open_signal_confirmed
test_exit_be_without_stop_moved_invalid
```

## 16.4 Multi-target

```text
test_message_wide_multi_target_all_valid
test_message_wide_multi_target_partial_valid
test_target_item_wide_per_line_mapping
test_mixed_targeted_ambiguous_goes_partial
```

## 16.5 Disambiguation

```text
test_prefer_move_stop_to_be_marks_move_stop_invalid
test_exit_be_over_close_full
test_keep_tp_hit_and_move_stop_to_be
test_suppress_close_full_if_sl_hit_without_explicit_close
```

## 16.6 Report

```text
test_new_parser_report_contains_candidate_confirmed_invalid
test_report_contains_valid_refs_invalid_refs
test_report_contains_target_validation_rows
```

---

## 17. Acceptance criteria globale

La feature è accettata quando:

```text
1. Gli intent escono sempre come CANDIDATE dall’extractor.
2. Entity validation invalida intent con dati insufficienti.
3. Target validation invalida UPDATE senza target.
4. REPLY e TELEGRAM_LINK vengono validati contro lo storico.
5. Multi-target produce valid_refs e invalid_refs separati.
6. Disambiguation non cancella intent senza lasciarne traccia.
7. Translator usa solo intent CONFIRMED e valid_refs.
8. Report debug mostra ogni passaggio.
9. Nessun nome legacy U_* compare nel nuovo ParsedMessage.
10. Il parser legacy continua a funzionare.
```

---

## 18. Rischi

### 18.1 Troppa severità

Se il validator diventa troppo severo, molti messaggi reali finiscono `INVALID` o `PARTIAL`.

Mitigazione:

```text
- iniziare con warning e diagnostics
- rendere alcune regole soft
- mantenere CANDIDATE per casi ambigui
```

### 18.2 Target globali

Comandi tipo:

```text
закрываю все лонги
```

non hanno refs singoli.

Mitigazione:

```text
- trattare GLOBAL_SCOPE come target valido
- non richiedere history per ogni singolo ref
- demandare al TargetResolver downstream la risoluzione concreta
```

### 18.3 Messaggi misti complessi

Messaggi con più target e più intent senza mapping chiaro sono rischiosi.

Mitigazione:

```text
- parse_status PARTIAL
- warning ambiguous_multi_target_mapping
- review manuale
```

### 18.4 Compatibilità legacy

Il router oggi salva ancora legacy.

Mitigazione:

```text
- non cambiare parse_message(...)
- introdurre feature flag per nuovo validator
- testare dual-stack
```

---

## 19. Decisioni consigliate

### Decisione 1

Usare validazione multilivello.

```text
APPROVATA
```

### Decisione 2

Non aggiungere subito `PARTIALLY_CONFIRMED`.

```text
APPROVATA
```

Usare:

```text
status CONFIRMED + valid_refs/invalid_refs
```

### Decisione 3

Non eliminare intent soppressi dalla disambiguation.

```text
APPROVATA
```

Marcarli:

```text
INVALID
```

### Decisione 4

Messaggi multi-target ambigui non devono andare downstream.

```text
APPROVATA
```

Devono diventare:

```text
parse_status PARTIAL
warning ambiguous_multi_target_mapping
```

### Decisione 5

Il nuovo report deve leggere solo `parsed_messages.parsed_json`.

```text
APPROVATA
```

Non deve usare `parse_results.parse_result_normalized_json`.

---

## 20. Roadmap breve

```text
Step 1 — Entity validation
Step 2 — Target normalization
Step 3 — History validation per REPLY/TELEGRAM_LINK
Step 4 — Multi-target validation
Step 5 — Disambiguation conservativa
Step 6 — Report debug nuovo
Step 7 — Cablaggio stabile nel router / parser_test
```

---

## 21. Prompt operativo per Codex

```text
Agisci come TDD Mentor & Developer.

Leggi il PRD:
docs/in_progress/new_parser/PRD_INTENT_VALIDATION_MULTILAYER_AND_MULTI_TARGET.md

Obiettivo della fase:
Implementare la Fase 1: Entity validation nel nuovo intent validator, senza modificare il parser legacy.

Vincoli:
- Non modificare parse_message legacy.
- Non introdurre intent legacy U_* nel nuovo ParsedMessage.
- Gli extractor devono continuare a produrre intent candidate.
- Il validator deve trasformare candidate in CONFIRMED o INVALID.
- Aggiungi test prima del codice.

File probabili:
- src/parser/intent_validator/validator.py
- src/parser/intent_validator/validation_rules.json
- src/parser/tests/test_phase5_intent_validator.py

Casi minimi:
1. MOVE_STOP senza new_stop_price e senza stop_to_tp_level => INVALID.
2. MOVE_STOP con stop_to_tp_level => può proseguire.
3. CLOSE_PARTIAL senza fraction/close_price => INVALID.
4. MOVE_STOP_TO_BE senza entità => non invalidare per entity validation.

Alla fine:
- aggiorna diagnostics con entity_validation
- aggiorna/aggiungi test
- scrivi nel PRD una nota "Fase 1 completata" con file modificati
```

---

## 22. Sintesi

La direzione corretta è:

```text
non un validator unico,
ma una pipeline di validazione a più livelli.
```

Il parser deve produrre candidate ricchi, poi il sistema deve validare:

```text
entità
target
compatibilità
storico
```

Solo dopo si può parlare di intent confermati e di traduzione operativa.

La gestione multi-target è parte centrale della validazione: non è un caso raro, ma un requisito del dominio perché i trader possono aggiornare più segnali nello stesso messaggio.
