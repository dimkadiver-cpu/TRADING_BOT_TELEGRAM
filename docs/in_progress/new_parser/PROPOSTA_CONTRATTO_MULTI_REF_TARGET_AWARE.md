# Proposta: Contratto Target-Aware Multi-Ref

## Obiettivo

Definire un contratto parser formale che supporti in modo esplicito:

- piu target refs nello stesso messaggio;
- una singola azione comune su piu refs;
- azioni diverse su refs diversi;
- report o risultati individuali associati a specifici refs;
- migrazione graduale senza rompere il contratto esistente.

La proposta e pensata per essere:

- retrocompatibile;
- leggera;
- eseguibile dal runtime phase4/phase5 con una migrazione incrementale.

## Problema Attuale

Oggi il sistema supporta bene:

- `targeting.refs` multipli;
- `target_refs` multipli nei parse result legacy.

Ma non supporta end-to-end:

- il binding esplicito `azione -> ref`;
- il binding esplicito `report -> ref`;
- la risoluzione runtime di piu azioni target-aware nello stesso messaggio.

Il problema strutturale e che il modello attuale ha:

- `targeting` come lista piatta di refs;
- `update.operations` come lista piatta di operazioni;
- `report.events` come lista piatta di eventi;

ma non ha un blocco che dica:

- questa operazione vale per questi refs;
- questo report vale per questo ref.

## Principio di Design

Separare quattro livelli:

1. inventario target del messaggio;
2. semantica del messaggio o del singolo item;
3. binding tra semantica e target;
4. risoluzione runtime target -> posizione concreta.

La semantica deve essere risolta prima del binding.

Ordine corretto:

1. detection intent
2. `intent_compatibility`
3. `disambiguation_rules`
4. `context_resolution_rules`
5. costruzione `targeted_actions` / `targeted_reports`
6. target resolution runtime
7. apply runtime

## Decisione Architetturale

Mantenere il contratto attuale per i casi semplici:

- `targeting`
- `update.operations`
- `report.events`

Aggiungere due nuovi blocchi opzionali per i casi multi-ref target-aware:

- `targeted_actions`
- `targeted_reports`

Gerarchia di utilizzo consigliata:

1. se presenti `targeted_actions` / `targeted_reports`, questi diventano la source of truth operativa;
2. se assenti, il sistema continua a usare `update.operations` / `report.events` legacy.

## Resolution Unit

La semantica puo essere risolta su due unita diverse:

- `MESSAGE_WIDE`
- `TARGET_ITEM_WIDE`

### `MESSAGE_WIDE`

Si usa quando il messaggio ha una semantica unica condivisa da tutti i refs.

Esempi:

- piu refs + unica azione `MOVE_STOP_TO_BE`;
- piu refs + unica azione `CLOSE_FULL`.

Output tipico:

- un solo `targeted_action` con `TARGET_GROUP`.

### `TARGET_ITEM_WIDE`

Si usa quando il messaggio contiene righe o frammenti con semantica diversa per ref diversi.

Esempi:

- quattro righe `stop in be` e una riga `stop on tp1`;
- righe con risultati diversi per ogni target.

Output tipico:

- piu `targeted_actions` o `targeted_reports`, eventualmente raggruppati per firma semantica uguale.

## Schema Campi Proposto

## 1. `targeting`

Resta il catalogo dei target trovati nel messaggio.

Shape:

```json
{
  "targeting": {
    "refs": [
      { "ref_type": "TELEGRAM_LINK", "value": "https://t.me/c/3171748254/1725" },
      { "ref_type": "MESSAGE_ID", "value": 1725 },
      { "ref_type": "TELEGRAM_LINK", "value": "https://t.me/c/3171748254/1726" },
      { "ref_type": "MESSAGE_ID", "value": 1726 }
    ],
    "scope": {
      "kind": "SINGLE_SIGNAL",
      "value": null,
      "side_filter": null,
      "applies_to_all": false
    },
    "strategy": "REPLY_OR_LINK",
    "targeted": true
  }
}
```

Vincoli:

- `refs` puo contenere piu refs;
- i duplicati vanno deduplicati dal normalizer;
- `targeting` non definisce l'azione, solo i target disponibili.

## 2. `targeted_actions`

Nuovo blocco opzionale per le azioni target-aware.

Shape top-level:

```json
{
  "targeted_actions": [
    {
      "action_type": "SET_STOP",
      "params": {
        "target_type": "ENTRY"
      },
      "targeting": {
        "mode": "TARGET_GROUP",
        "targets": [1725, 1726]
      },
      "raw_fragment": "пора перенести стоп в бу",
      "confidence": 0.92
    }
  ]
}
```

### Campi supportati

- `action_type`: enum, obbligatorio
- `params`: object, obbligatorio
- `targeting`: object, obbligatorio
- `raw_fragment`: string, opzionale
- `confidence`: float `0.0-1.0`, opzionale
- `diagnostics`: object, opzionale

### Valori ammessi per `action_type`

- `SET_STOP`
- `CLOSE`
- `CANCEL_PENDING`
- `MODIFY_ENTRIES`
- `MODIFY_TARGETS`

### Shape `targeting`

```json
{
  "mode": "EXPLICIT_TARGETS | TARGET_GROUP | SELECTOR",
  "targets": [1725, 1726],
  "selector": {
    "side": "SHORT",
    "status": "OPEN"
  }
}
```

### Vincoli `targeting`

- `mode = EXPLICIT_TARGETS` -> `targets` obbligatorio, non vuoto
- `mode = TARGET_GROUP` -> `targets` obbligatorio, non vuoto
- `mode = SELECTOR` -> `selector` obbligatorio
- `targets` contiene ids parser-level, non ancora op_signal_ids runtime

### Shape `params` per action type

#### `SET_STOP`

```json
{
  "target_type": "PRICE | ENTRY | TP_LEVEL",
  "value": 1,
  "price": 1.245
}
```

Vincoli:

- `ENTRY` -> nessun `price` richiesto
- `TP_LEVEL` -> `value` intero obbligatorio
- `PRICE` -> `price` numerico obbligatorio

#### `CLOSE`

```json
{
  "close_scope": "FULL | PARTIAL",
  "close_fraction": 0.5,
  "close_price": 1.245
}
```

Vincoli:

- `FULL` -> `close_fraction` non richiesto
- `PARTIAL` -> almeno uno tra `close_fraction` e `close_price`

#### `CANCEL_PENDING`

```json
{
  "cancel_scope": "TARGETED | ALL_PENDING_ENTRIES | ALL_LONG | ALL_SHORT | ALL_ALL"
}
```

#### `MODIFY_ENTRIES`

```json
{
  "mode": "ADD | REENTER | UPDATE",
  "entries": [
    {
      "sequence": 1,
      "entry_type": "LIMIT",
      "price": 1.245
    }
  ]
}
```

#### `MODIFY_TARGETS`

```json
{
  "mode": "REPLACE_ALL | ADD | UPDATE_ONE | REMOVE_ONE",
  "target_tp_level": 1,
  "take_profits": [
    {
      "sequence": 1,
      "price": 1.400
    }
  ]
}
```

## 3. `targeted_reports`

Nuovo blocco opzionale per eventi/report associati a target specifici.

Shape top-level:

```json
{
  "targeted_reports": [
    {
      "event_type": "FINAL_RESULT",
      "result": {
        "value": 3.94,
        "unit": "PERCENT",
        "text": null
      },
      "level": null,
      "targeting": {
        "mode": "EXPLICIT_TARGETS",
        "targets": [822]
      },
      "instrument_hint": "XRP",
      "raw_fragment": "XRP - https://t.me/c/3171748254/822 3.94% прибыли",
      "confidence": 0.91
    }
  ]
}
```

### Campi supportati

- `event_type`: enum, obbligatorio
- `result`: object, opzionale ma fortemente consigliato per final result
- `level`: int, opzionale
- `targeting`: object, obbligatorio
- `instrument_hint`: string, opzionale
- `raw_fragment`: string, opzionale
- `confidence`: float `0.0-1.0`, opzionale
- `diagnostics`: object, opzionale

### Valori ammessi per `event_type`

- `ENTRY_FILLED`
- `TP_HIT`
- `STOP_HIT`
- `BREAKEVEN_EXIT`
- `FINAL_RESULT`

### Shape `result`

```json
{
  "value": 3.94,
  "unit": "R | PERCENT | TEXT | UNKNOWN",
  "text": null
}
```

Vincoli:

- `FINAL_RESULT` normalmente dovrebbe avere `result`
- `TP_HIT` puo avere `level` e opzionalmente `result`
- `STOP_HIT` e `ENTRY_FILLED` possono non avere `result`

## 4. Campi Diagnostici Consigliati

Per ogni record target-aware e utile poter salvare:

```json
{
  "diagnostics": {
    "resolution_unit": "MESSAGE_WIDE",
    "semantic_signature": "SET_STOP:ENTRY",
    "applied_disambiguation_rules": ["prefer_be_over_move_stop"],
    "applied_context_rules": []
  }
}
```

Campi consigliati:

- `resolution_unit`
- `semantic_signature`
- `applied_disambiguation_rules`
- `applied_context_rules`
- `grouping_reason`

## Esempi JSON Completi

## Caso 1

Input concettuale:

- due refs specifici
- stessa azione comune `stop in be`

JSON completo proposto:

```json
{
  "schema_version": "1.1",
  "parser_profile": "trader_a",
  "primary_class": "UPDATE",
  "parse_status": "PARSED",
  "confidence": 0.92,
  "intents": ["MOVE_STOP_TO_BE"],
  "primary_intent": "MOVE_STOP_TO_BE",
  "targeting": {
    "refs": [
      { "ref_type": "TELEGRAM_LINK", "value": "https://t.me/c/3171748254/1725" },
      { "ref_type": "MESSAGE_ID", "value": 1725 },
      { "ref_type": "TELEGRAM_LINK", "value": "https://t.me/c/3171748254/1726" },
      { "ref_type": "MESSAGE_ID", "value": 1726 }
    ],
    "scope": {
      "kind": "SINGLE_SIGNAL",
      "value": null,
      "side_filter": null,
      "applies_to_all": false
    },
    "strategy": "REPLY_OR_LINK",
    "targeted": true
  },
  "update": {
    "operations": [
      {
        "op_type": "SET_STOP",
        "set_stop": {
          "target_type": "ENTRY",
          "value": null
        },
        "raw_fragment": "пора перенести стоп в бу",
        "confidence": 0.92
      }
    ]
  },
  "targeted_actions": [
    {
      "action_type": "SET_STOP",
      "params": {
        "target_type": "ENTRY"
      },
      "targeting": {
        "mode": "TARGET_GROUP",
        "targets": [1725, 1726]
      },
      "raw_fragment": "пора перенести стоп в бу",
      "confidence": 0.92,
      "diagnostics": {
        "resolution_unit": "MESSAGE_WIDE",
        "semantic_signature": "SET_STOP:ENTRY"
      }
    }
  ],
  "report": null,
  "targeted_reports": [],
  "warnings": [],
  "diagnostics": {
    "multi_ref_mode": true
  },
  "raw_context": {
    "raw_text": "https://t.me/c/3171748254/1725\nhttps://t.me/c/3171748254/1726\n\nпора перенести стоп в бу\nкто желает может фиксировать прибыль",
    "reply_to_message_id": null,
    "extracted_links": [
      "https://t.me/c/3171748254/1725",
      "https://t.me/c/3171748254/1726"
    ],
    "hashtags": [],
    "source_chat_id": "3171748254",
    "source_topic_id": null,
    "acquisition_mode": "live"
  }
}
```

## Caso 2

Input concettuale:

- piu refs
- azione comune `close full`
- risultato individuale per ogni ref

JSON completo proposto:

```json
{
  "schema_version": "1.1",
  "parser_profile": "trader_a",
  "primary_class": "UPDATE",
  "parse_status": "PARSED",
  "confidence": 0.94,
  "intents": ["CLOSE_FULL", "REPORT_FINAL_RESULT"],
  "primary_intent": "CLOSE_FULL",
  "targeting": {
    "refs": [
      { "ref_type": "TELEGRAM_LINK", "value": "https://t.me/c/3171748254/822" },
      { "ref_type": "MESSAGE_ID", "value": 822 },
      { "ref_type": "TELEGRAM_LINK", "value": "https://t.me/c/3171748254/856" },
      { "ref_type": "MESSAGE_ID", "value": 856 },
      { "ref_type": "TELEGRAM_LINK", "value": "https://t.me/c/3171748254/861" },
      { "ref_type": "MESSAGE_ID", "value": 861 },
      { "ref_type": "TELEGRAM_LINK", "value": "https://t.me/c/3171748254/870" },
      { "ref_type": "MESSAGE_ID", "value": 870 }
    ],
    "scope": {
      "kind": "SINGLE_SIGNAL",
      "value": null,
      "side_filter": null,
      "applies_to_all": false
    },
    "strategy": "REPLY_OR_LINK",
    "targeted": true
  },
  "update": {
    "operations": [
      {
        "op_type": "CLOSE",
        "close": {
          "close_scope": "FULL",
          "close_fraction": null,
          "close_price": null
        },
        "raw_fragment": "Эти монеты закрываю по текущим, так как нет времени за ними следить",
        "confidence": 0.94
      }
    ]
  },
  "targeted_actions": [
    {
      "action_type": "CLOSE",
      "params": {
        "close_scope": "FULL"
      },
      "targeting": {
        "mode": "TARGET_GROUP",
        "targets": [822, 856, 861, 870]
      },
      "raw_fragment": "Эти монеты закрываю по текущим, так как нет времени за ними следить",
      "confidence": 0.94,
      "diagnostics": {
        "resolution_unit": "MESSAGE_WIDE",
        "semantic_signature": "CLOSE:FULL"
      }
    }
  ],
  "report": {
    "events": [],
    "reported_result": null,
    "notes": []
  },
  "targeted_reports": [
    {
      "event_type": "FINAL_RESULT",
      "result": {
        "value": 3.94,
        "unit": "PERCENT",
        "text": null
      },
      "level": null,
      "targeting": {
        "mode": "EXPLICIT_TARGETS",
        "targets": [822]
      },
      "instrument_hint": "XRP",
      "raw_fragment": "XRP - https://t.me/c/3171748254/822 3.94% прибыли",
      "confidence": 0.93,
      "diagnostics": {
        "resolution_unit": "TARGET_ITEM_WIDE",
        "semantic_signature": "FINAL_RESULT:PERCENT"
      }
    },
    {
      "event_type": "FINAL_RESULT",
      "result": {
        "value": -9.32,
        "unit": "PERCENT",
        "text": null
      },
      "level": null,
      "targeting": {
        "mode": "EXPLICIT_TARGETS",
        "targets": [856]
      },
      "instrument_hint": "ENA",
      "raw_fragment": "ENA - https://t.me/c/3171748254/856 убыток 9.32",
      "confidence": 0.93,
      "diagnostics": {
        "resolution_unit": "TARGET_ITEM_WIDE",
        "semantic_signature": "FINAL_RESULT:PERCENT"
      }
    },
    {
      "event_type": "FINAL_RESULT",
      "result": {
        "value": 4.2,
        "unit": "PERCENT",
        "text": null
      },
      "level": null,
      "targeting": {
        "mode": "EXPLICIT_TARGETS",
        "targets": [861]
      },
      "instrument_hint": "LDO",
      "raw_fragment": "LDO - https://t.me/c/3171748254/861 прибыль 4.2%",
      "confidence": 0.93,
      "diagnostics": {
        "resolution_unit": "TARGET_ITEM_WIDE",
        "semantic_signature": "FINAL_RESULT:PERCENT"
      }
    },
    {
      "event_type": "FINAL_RESULT",
      "result": {
        "value": 3.4,
        "unit": "PERCENT",
        "text": null
      },
      "level": null,
      "targeting": {
        "mode": "EXPLICIT_TARGETS",
        "targets": [870]
      },
      "instrument_hint": "SHIB",
      "raw_fragment": "SHIB - https://t.me/c/3171748254/870 прибыль 3.4%",
      "confidence": 0.93,
      "diagnostics": {
        "resolution_unit": "TARGET_ITEM_WIDE",
        "semantic_signature": "FINAL_RESULT:PERCENT"
      }
    }
  ],
  "warnings": [],
  "diagnostics": {
    "multi_ref_mode": true,
    "mixed_common_action_and_per_target_reports": true
  },
  "raw_context": {
    "raw_text": "XRP - https://t.me/c/3171748254/822 3.94% прибыли\nENA - https://t.me/c/3171748254/856 убыток 9.32\nLDO - https://t.me/c/3171748254/861 прибыль 4.2%\nSHIB - https://t.me/c/3171748254/870 прибыль 3.4%\n\nЭти монеты закрываю по текущим, так как нет времени за ними следить",
    "reply_to_message_id": null,
    "extracted_links": [
      "https://t.me/c/3171748254/822",
      "https://t.me/c/3171748254/856",
      "https://t.me/c/3171748254/861",
      "https://t.me/c/3171748254/870"
    ],
    "hashtags": [],
    "source_chat_id": "3171748254",
    "source_topic_id": null,
    "acquisition_mode": "live"
  }
}
```

## Caso 3

Input concettuale:

- 5 refs
- 4 con `stop in be`
- 1 con `stop on tp1`

JSON completo proposto:

```json
{
  "schema_version": "1.1",
  "parser_profile": "trader_a",
  "primary_class": "UPDATE",
  "parse_status": "PARSED",
  "confidence": 0.95,
  "intents": ["MOVE_STOP_TO_BE", "MOVE_STOP"],
  "primary_intent": "MOVE_STOP_TO_BE",
  "targeting": {
    "refs": [
      { "ref_type": "TELEGRAM_LINK", "value": "https://t.me/c/3171748254/978" },
      { "ref_type": "MESSAGE_ID", "value": 978 },
      { "ref_type": "TELEGRAM_LINK", "value": "https://t.me/c/3171748254/1002" },
      { "ref_type": "MESSAGE_ID", "value": 1002 },
      { "ref_type": "TELEGRAM_LINK", "value": "https://t.me/c/3171748254/1003" },
      { "ref_type": "MESSAGE_ID", "value": 1003 },
      { "ref_type": "TELEGRAM_LINK", "value": "https://t.me/c/3171748254/1005" },
      { "ref_type": "MESSAGE_ID", "value": 1005 },
      { "ref_type": "TELEGRAM_LINK", "value": "https://t.me/c/3171748254/1018" },
      { "ref_type": "MESSAGE_ID", "value": 1018 }
    ],
    "scope": {
      "kind": "SINGLE_SIGNAL",
      "value": null,
      "side_filter": null,
      "applies_to_all": false
    },
    "strategy": "REPLY_OR_LINK",
    "targeted": true
  },
  "update": {
    "operations": [
      {
        "op_type": "SET_STOP",
        "set_stop": {
          "target_type": "ENTRY",
          "value": null
        },
        "raw_fragment": "стоп в бу",
        "confidence": 0.94
      },
      {
        "op_type": "SET_STOP",
        "set_stop": {
          "target_type": "TP_LEVEL",
          "value": 1
        },
        "raw_fragment": "стоп на 1 тейк",
        "confidence": 0.93
      }
    ]
  },
  "targeted_actions": [
    {
      "action_type": "SET_STOP",
      "params": {
        "target_type": "ENTRY"
      },
      "targeting": {
        "mode": "EXPLICIT_TARGETS",
        "targets": [978, 1002, 1003, 1018]
      },
      "raw_fragment": "LINK - https://t.me/c/3171748254/978 - стоп в бу\nALGO - https://t.me/c/3171748254/1002 стоп в бу\nARKM - https://t.me/c/3171748254/1003 стоп в бу\nUNI - https://t.me/c/3171748254/1018 стоп в бу",
      "confidence": 0.94,
      "diagnostics": {
        "resolution_unit": "TARGET_ITEM_WIDE",
        "semantic_signature": "SET_STOP:ENTRY",
        "grouping_reason": "same_action_same_params"
      }
    },
    {
      "action_type": "SET_STOP",
      "params": {
        "target_type": "TP_LEVEL",
        "value": 1
      },
      "targeting": {
        "mode": "EXPLICIT_TARGETS",
        "targets": [1005]
      },
      "raw_fragment": "FART - https://t.me/c/3171748254/1005 стоп на 1 тейк",
      "confidence": 0.93,
      "diagnostics": {
        "resolution_unit": "TARGET_ITEM_WIDE",
        "semantic_signature": "SET_STOP:TP_LEVEL:1"
      }
    }
  ],
  "report": null,
  "targeted_reports": [],
  "warnings": [],
  "diagnostics": {
    "multi_ref_mode": true,
    "mixed_target_item_actions": true
  },
  "raw_context": {
    "raw_text": "LINK - https://t.me/c/3171748254/978 - стоп в бу\nALGO - https://t.me/c/3171748254/1002 стоп в бу\nARKM - https://t.me/c/3171748254/1003 стоп в бу\nFART - https://t.me/c/3171748254/1005 стоп на 1 тейк\nUNI - https://t.me/c/3171748254/1018 стоп в бу",
    "reply_to_message_id": null,
    "extracted_links": [
      "https://t.me/c/3171748254/978",
      "https://t.me/c/3171748254/1002",
      "https://t.me/c/3171748254/1003",
      "https://t.me/c/3171748254/1005",
      "https://t.me/c/3171748254/1018"
    ],
    "hashtags": [],
    "source_chat_id": "3171748254",
    "source_topic_id": null,
    "acquisition_mode": "live"
  }
}
```

## Regole di Costruzione

### 1. Stessa azione su piu refs

Se il messaggio ha una sola semantica condivisa:

- usare `resolution_unit = MESSAGE_WIDE`
- produrre un solo `targeted_action`
- usare `TARGET_GROUP`

### 2. Azioni diverse su refs diversi

Se il messaggio ha righe o blocchi eterogenei:

- usare `resolution_unit = TARGET_ITEM_WIDE`
- risolvere semantica per item
- raggruppare gli item con stessa firma semantica in un unico record

### 3. Report individuali per ref

Ogni riga o frammento con result/event indipendente genera un `targeted_report` dedicato.

### 4. Ambiguita non risolta

Se il parser non riesce a fare binding affidabile:

- non deve inventare il mapping;
- deve degradare a contratto legacy;
- deve emettere warning:

```json
{
  "warnings": [
    "targeted_binding_ambiguous"
  ]
}
```

## Piano Migrazione Parser -> Resolver -> Runtime

## Fase 1 - Parser Contract

### Obiettivo

Introdurre il nuovo contratto senza rompere il vecchio.

### Modifiche

1. Estendere `CanonicalMessage` con:
- `targeted_actions: list[TargetedAction] = []`
- `targeted_reports: list[TargetedReport] = []`

2. Aggiungere nuovi modelli Pydantic in `canonical_v1/models.py`:
- `TargetedAction`
- `TargetedActionTargeting`
- `TargetedReport`
- `TargetedReportTargeting`

3. Non toccare il significato di:
- `targeting`
- `update.operations`
- `report.events`

### Esito atteso

- i profili possono iniziare a emettere shape target-aware;
- i consumer esistenti continuano a funzionare.

## Fase 2 - Parser Builder / Normalizer

### Obiettivo

Permettere ai profili di produrre `targeted_actions` e `targeted_reports`.

### Modifiche

1. Formalizzare una shape shared intermedia per i profili legacy.
2. Portare la logica gia presente in `trader_a` dentro un builder shared.
3. Aggiungere supporto al concetto di:
- `MESSAGE_WIDE`
- `TARGET_ITEM_WIDE`

### Strategia consigliata

1. usare `trader_a` come primo profilo pilota;
2. mappare `actions_structured` legacy in `targeted_actions`;
3. aggiungere parsing per `targeted_reports` per i casi di result per-ref.

### Esito atteso

- il parser salva sia il contratto legacy sia quello target-aware;
- il JSON v1 in `parse_results_v1` contiene gia i nuovi campi.

## Fase 3 - Target Resolver

### Obiettivo

Smettere di restituire un singolo `ResolvedTarget` per l'intero messaggio.

### Problema attuale

Il resolver attuale:

- riceve piu `target_refs`;
- ritorna al primo match utile;
- non conosce `azione -> ref`.

### Modifiche

Introdurre un nuovo output, per esempio:

```json
{
  "resolved_actions": [
    {
      "action_index": 0,
      "action_type": "SET_STOP",
      "resolved_position_ids": [101, 102],
      "eligibility": "ELIGIBLE",
      "reason": null
    }
  ],
  "resolved_reports": [
    {
      "report_index": 0,
      "event_type": "FINAL_RESULT",
      "resolved_position_ids": [101],
      "eligibility": "ELIGIBLE",
      "reason": null
    }
  ]
}
```

### Regole runtime

- `TARGET_GROUP` -> risolvere ogni target del gruppo
- `EXPLICIT_TARGETS` -> risolvere ogni target esplicito
- `SELECTOR` -> risolvere il set filtrato dal selector

### Esito atteso

- il resolver diventa multi-target e multi-action aware.

## Fase 4 - Router / Update Planner / Runtime

### Obiettivo

Far consumare al runtime il contratto target-aware.

### Problema attuale

Il runtime usa:

- `actions: list[str]`
- `target_refs: list[int]`

quindi perde il binding fine.

### Modifiche

1. nel router, se presenti `targeted_actions`, usarli come source of truth;
2. introdurre un planner target-aware, per esempio:
- `TargetedStateUpdatePlan`
- `TargetedActionPlanItem`

3. applicare il piano per item risolto, non per messaggio piatto.

Shape possibile:

```json
{
  "action_plans": [
    {
      "action_type": "SET_STOP",
      "target_attempt_keys": ["T_..._1725", "T_..._1726"],
      "params": {
        "target_type": "ENTRY"
      }
    }
  ],
  "report_plans": [
    {
      "event_type": "FINAL_RESULT",
      "target_attempt_keys": ["T_..._822"],
      "result": {
        "value": 3.94,
        "unit": "PERCENT"
      }
    }
  ]
}
```

### Esito atteso

- il runtime applica davvero piu azioni o report su target diversi nello stesso messaggio.

## Fase 5 - Fallback e Backward Compatibility

### Regola di precedenza

1. se esistono `targeted_actions` / `targeted_reports`, usare quelli;
2. altrimenti usare il percorso legacy.

### Vantaggi

- migrazione graduale;
- nessuna rottura immediata dei profili non migrati;
- possibilita di rollout per trader.

## Criteri di Accettazione

1. Il contratto puo rappresentare piu refs nello stesso messaggio.
2. Il contratto puo rappresentare una singola azione comune su piu refs.
3. Il contratto puo rappresentare azioni diverse su refs diversi.
4. Il contratto puo rappresentare report individuali per-ref.
5. Il resolver non ritorna piu solo il primo target valido quando e presente shape target-aware.
6. Il runtime applica le azioni usando il binding reale `azione -> target`.
7. In assenza di shape target-aware, il percorso legacy continua a funzionare.
8. I casi ambigui emettono warning invece di inventare binding non affidabili.

## Raccomandazione Finale

La scelta piu pragmatica e:

1. aggiungere subito il contratto `targeted_actions` / `targeted_reports`;
2. usare `trader_a` come profilo pilota;
3. migrare poi resolver e runtime per consumarlo davvero;
4. mantenere il contratto legacy fino a chiusura della migrazione.

Questo e il percorso con il miglior rapporto tra valore, rischio e complessita.

