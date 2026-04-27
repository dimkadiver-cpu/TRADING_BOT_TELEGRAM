# parser_event_envelope_v1

Data: 2026-04-24  
Stato: specifica operativa allineata al codice attuale

## Scopo

`parser_event_envelope_v1` e il contratto parser-side unico che fa da ponte tra:

- output legacy dei profili trader: `TraderParseResult`
- normalizzazione canonica centralizzata: `CanonicalMessage v1`

La shape reale e in produzione in:

- `src/parser/event_envelope_v1.py`
- `src/parser/adapters/legacy_to_event_envelope_v1.py`

## Risposta breve alla domanda principale

Si, oggi l'envelope e unico per tutti i trader, ma non e ancora emesso nativamente da tutti i profili.

In pratica:

- ogni profilo trader continua a produrre `TraderParseResult`
- un adapter centrale unico converte quel risultato in `TraderEventEnvelopeV1`
- il normalizer legge l'envelope, non il parsing trader-specifico grezzo

Quindi l'unicita oggi e garantita nel layer di adapter, non nel fatto che ogni parser trader produca gia direttamente questo schema.

## Acceptance Contract

Done significa:

- chiarire se l'envelope e shared oppure trader-specifico
- distinguere classificazione di `intents` da estrazione di `entities`
- elencare i campi legacy che alimentano ogni blocco dell'envelope
- mostrare JSON validi per ogni tipo di payload/evento
- indicare i valori ammessi per i campi variabili

Primary signal:

- un lettore del parser capisce da dove arrivano `intents`, `entities` e come finiscono in `TraderEventEnvelopeV1`

Secondary signals:

- allineamento con `src/parser/event_envelope_v1.py`
- allineamento con `src/parser/adapters/legacy_to_event_envelope_v1.py`
- allineamento con `src/parser/canonical_v1/normalizer.py`
- coerenza con `src/parser/canonical_v1/tests/test_legacy_event_envelope_adapter.py`

## Dove nasce la classificazione

### 1. I profili trader classificano il messaggio

Ogni profilo produce un `TraderParseResult` con:

- `message_type`
- `intents`
- `primary_intent`
- `entities`
- `target_refs`
- `reported_results`

Qui avviene la parte trader-specifica:

- riconoscimento marker testuali
- estrazione prezzi, target, stop, risk, riferimenti target
- costruzione delle `entities`

### 2. L'adapter non riclassifica il trader

L'adapter copia e normalizza:

- `message_type -> message_type_hint`
- `intents -> intents_detected`
- `primary_intent -> primary_intent_hint`

Poi costruisce tre blocchi possibili in parallelo:

- `signal_payload_raw`
- `update_payload_raw`
- `report_payload_raw`

Questo e importante: un singolo messaggio puo produrre sia `update_payload_raw` sia `report_payload_raw`.

### 3. Il normalizer decide la classe canonica finale

Il normalizer bucketizza gli intent in:

- `SIGNAL`: `NS_CREATE_SIGNAL`
- `UPDATE`: intent operativi come `U_MOVE_STOP`, `U_CLOSE_PARTIAL`, `U_REENTER`
- `REPORT`: intent di esito come `U_TP_HIT`, `U_STOP_HIT`, `U_REPORT_FINAL_RESULT`
- `INFO`: casi come `U_RISK_NOTE` senza update/report reali

## Intent realmente osservati nel codice

Intent business reali usati dai profili o gestiti dal bridge:

- `NS_CREATE_SIGNAL`
- `U_ACTIVATION`          // eliminare 
- `U_CANCEL_PENDING_ORDERS`
- `U_CLOSE_FULL`
- `U_CLOSE_PARTIAL`
- `U_EXIT_BE`
- `U_INVALIDATE_SETUP`
- `U_MARK_FILLED`        // sostituire con ENTRY_FILLED
- `U_MOVE_STOP`
- `U_MOVE_STOP_TO_BE`
- `U_REENTER`
- `U_REMOVE_PENDING_ENTRY`
- `U_REPORT_FINAL_RESULT`
- `U_REVERSE_SIGNAL`      // eliminare
- `U_RISK_NOTE`           // eliminare
- `U_STOP_HIT`
- `U_TP_HIT`    
- `U_UPDATE_STOP`         // sostituire con U_MOVE_STOP`
- `U_UPDATE_TAKE_PROFITS`

Intent trovati nei profili ma non ancora shape business dedicate nell'envelope:

- `U_TP_HIT_EXPLICIT` // sostitire da U_TP_HIT`

Note:

- `U_UPDATE_STOP` converge nello stesso mapping di `U_MOVE_STOP`
- `U_ACTIVATION` e `U_MARK_FILLED` convergono in `ENTRY_FILLED`
- `U_INVALIDATE_SETUP` converge in `CANCEL_PENDING`
- `U_REVERSE_SIGNAL` oggi viene degradato a `CLOSE` con warning
- `U_RISK_NOTE` non produce `update_payload_raw` o `report_payload_raw`

## Shape top-level

```json
{
  "schema_version": "trader_event_envelope_v1",
  "message_type_hint": "NEW_SIGNAL",
  "intents_detected": ["NS_CREATE_SIGNAL"],
  "primary_intent_hint": "NS_CREATE_SIGNAL",
  "instrument": {
    "symbol": "BTCUSDT",
    "side": "SHORT",
    "market_type": "FUTURES"
  },
  "signal_payload_raw": {
    "entry_structure": "TWO_STEP",
    "entries": [],
    "stop_loss": null,
    "take_profits": [],
    "risk_hint": null,
    "raw_fragments": {}
  },
  "update_payload_raw": {
    "operations": []
  },
  "report_payload_raw": {
    "events": [],
    "reported_result": null,
    "notes": []
  },
  "targets_raw": [],
  "warnings": [],
  "confidence": 0.96,
  "diagnostics": {}
}
```

## Valori ammessi

### Top-level

- `schema_version`: sempre `trader_event_envelope_v1`
- `message_type_hint`: `NEW_SIGNAL | UPDATE | INFO_ONLY | UNCLASSIFIED | null`

### Instrument

- `side`: `LONG | SHORT | null`
- `market_type`: `SPOT | FUTURES | UNKNOWN | null`

### Signal

- `entry_structure`: `ONE_SHOT | TWO_STEP | RANGE | LADDER | null`
- `entry_type`: `MARKET | LIMIT | null`
- `role`: `PRIMARY | AVERAGING | RANGE_LOW | RANGE_HIGH | REENTRY | UNKNOWN`
- `risk_hint.unit`: `PERCENT | ABSOLUTE | UNKNOWN`

### Update

- `op_type`: `SET_STOP | CLOSE | CANCEL_PENDING | MODIFY_ENTRIES | MODIFY_TARGETS`
- `set_stop.target_type`: `PRICE | ENTRY | TP_LEVEL`
- `modify_entries.mode`: `ADD | REENTER | UPDATE | REMOVE | REPLACE_ALL`
- `modify_targets.mode`: `REPLACE_ALL | ADD | UPDATE_ONE | REMOVE_ONE`

### Report

- `event_type`: `ENTRY_FILLED | TP_HIT | STOP_HIT | BREAKEVEN_EXIT | FINAL_RESULT`
- `reported_result.unit`: `R | PERCENT | TEXT | UNKNOWN`

### Targets

- `targets_raw.kind`: `REPLY | TELEGRAM_LINK | MESSAGE_ID | EXPLICIT_ID | SYMBOL | UNKNOWN`

## Classificazione intent -> blocchi envelope

### SIGNAL

| Intent | Blocco popolato | Note |
|---|---|---|
| `NS_CREATE_SIGNAL` | `signal_payload_raw` | segnale nuovo o incompleto |

### UPDATE

| Intent | `op_type` | Note |
|---|---|---|
| `U_MOVE_STOP_TO_BE` | `SET_STOP` | target `ENTRY` |
| `U_MOVE_STOP` | `SET_STOP` | target da `new_stop_level` o `new_stop_price` |
| `U_UPDATE_STOP` | `SET_STOP` | alias di fatto di `U_MOVE_STOP` |
| `U_CLOSE_FULL` | `CLOSE` | fallback `close_scope=FULL` |
| `U_CLOSE_PARTIAL` | `CLOSE` | usa `close_fraction` o `partial_close_percent` |
| `U_CANCEL_PENDING_ORDERS` | `CANCEL_PENDING` | usa `cancel_scope` se presente |
| `U_INVALIDATE_SETUP` | `CANCEL_PENDING` | fallback `ALL_PENDING_ENTRIES` |
| `U_REMOVE_PENDING_ENTRY` | `CANCEL_PENDING` | fallback `REMOVE_PENDING_ENTRY` |
| `U_REENTER` | `MODIFY_ENTRIES` | `mode=REENTER` |
| `U_ADD_ENTRY` | `MODIFY_ENTRIES` | `mode=ADD` |
| `U_UPDATE_TAKE_PROFITS` | `MODIFY_TARGETS` | `mode=REPLACE_ALL` |
| `U_REVERSE_SIGNAL` | `CLOSE` | mappatura degradata, parte new signal ignorata |

### REPORT

| Intent | `event_type` | Note |
|---|---|---|
| `U_ACTIVATION` | `ENTRY_FILLED` | |
| `U_MARK_FILLED` | `ENTRY_FILLED` | |
| `U_TP_HIT` | `TP_HIT` | livello da `hit_target` |
| `U_STOP_HIT` | `STOP_HIT` | |
| `U_EXIT_BE` | `BREAKEVEN_EXIT` | |
| `U_REPORT_FINAL_RESULT` | `FINAL_RESULT` | usa `reported_results[0]` se presente |

### INFO

| Intent | Blocco popolato | Note |
|---|---|---|
| `U_RISK_NOTE` | nessun payload business obbligatorio | viene trattato come informativo dal normalizer |

## Entities legacy che alimentano l'envelope

Le `entities` non sono standardizzate dal trader in origine, ma l'adapter usa un set di chiavi ricorrente abbastanza stabile.

### Instrument

| Entity legacy | Target envelope |
|---|---|
| `symbol` | `instrument.symbol` |
| `side` | `instrument.side` |
| `direction` | fallback `instrument.side` |
| `market_type` | `instrument.market_type` |

### Signal payload

| Entity legacy | Target envelope |
|---|---|
| `entry_structure` | `signal_payload_raw.entry_structure` |
| `entry_plan_entries` | fonte preferita per `signal_payload_raw.entries` |
| `entries` | fallback strutturato per `signal_payload_raw.entries` |
| `entry` | fallback piatto per `signal_payload_raw.entries` |
| `stop_loss` | `signal_payload_raw.stop_loss.price` |
| `stop_text_raw` | `signal_payload_raw.stop_loss.raw` e `raw_fragments.stop` |
| `take_profits` | `signal_payload_raw.take_profits` |
| `risk_value_normalized` | `signal_payload_raw.risk_hint.value` |
| `risk_percent` | fallback `signal_payload_raw.risk_hint.value` |
| `risk_value_raw` | `signal_payload_raw.risk_hint.raw` |
| `entry_text_raw` | `raw_fragments.entry` |
| `take_profits_text_raw` | `raw_fragments.take_profits` |

### Update payload

| Entity legacy | Uso |
|---|---|
| `new_stop_level` | `SET_STOP` primario |
| `new_stop_price` | fallback `SET_STOP` |
| `close_fraction` | `CLOSE.close_fraction` |
| `partial_close_percent` | fallback `CLOSE.close_fraction` |
| `close_price` | `CLOSE.close_price` e alcuni report |
| `close_scope` | `CLOSE.close_scope` |
| `cancel_scope` | `CANCEL_PENDING.cancel_scope` |
| `new_entry_price` | fallback minimo per `ADD` |
| `entries` / `entry_plan_entries` | `MODIFY_ENTRIES.entries` |
| `take_profits` | `MODIFY_TARGETS.take_profits` |

### Report payload

| Entity legacy | Uso |
|---|---|
| `hit_target` | livello `TP_HIT` |
| `close_price` | `ReportEventRaw.price` |
| `reported_results[0]` | `report_payload_raw.reported_result` e opzionalmente `event.result` |

### Targeting

| Sorgente legacy | Target envelope |
|---|---|
| `target_refs[*].kind` | `targets_raw[*].kind` |
| `target_refs[*].ref` | `targets_raw[*].value` |

### Residui legacy preservati in diagnostics

Non sono source of truth dell'envelope, ma oggi vengono copiati in `diagnostics`:

- `legacy_actions_structured`
- `legacy_target_scope`
- `legacy_linking`
- `legacy_entities_entry`
- `legacy_entities_entry_order_type`
- `legacy_entities_entry_plan_type`

## Regole di precedenza

### Entries

1. `entry_plan_entries`
2. `entries`
3. `entry`

### Side

1. `side`
2. `direction`

### Stop update

1. `new_stop_level`
2. `new_stop_price`

### Reported result

1. `reported_results[0]`
2. nessun fallback forte strutturale oltre a quello gia estratto dal trader

## Esempi JSON per tipo di payload/evento

### 1. New signal completo

```json
{
  "schema_version": "trader_event_envelope_v1",
  "message_type_hint": "NEW_SIGNAL",
  "intents_detected": ["NS_CREATE_SIGNAL"],
  "primary_intent_hint": "NS_CREATE_SIGNAL",
  "instrument": {
    "symbol": "BTCUSDT",
    "side": "SHORT",
    "market_type": "FUTURES"
  },
  "signal_payload_raw": {
    "entry_structure": "TWO_STEP",
    "entries": [
      {
        "sequence": 1,
        "entry_type": "LIMIT",
        "price": 88650.0,
        "role": "PRIMARY",
        "size_hint": "1/3",
        "is_optional": false
      },
      {
        "sequence": 2,
        "entry_type": "LIMIT",
        "price": 89100.0,
        "role": "AVERAGING",
        "size_hint": "2/3",
        "is_optional": false
      }
    ],
    "stop_loss": {
      "price": 89450.0,
      "raw": "SL 89450"
    },
    "take_profits": [
      {
        "sequence": 1,
        "price": 87500.0,
        "label": "TP1",
        "close_fraction": 0.3
      },
      {
        "sequence": 2,
        "price": 86800.0,
        "label": "TP2",
        "close_fraction": 0.3
      },
      {
        "sequence": 3,
        "price": 85800.0,
        "label": "TP3",
        "close_fraction": 0.4
      }
    ],
    "risk_hint": {
      "value": 1.0,
      "unit": "PERCENT",
      "raw": "1% dep"
    },
    "raw_fragments": {
      "entry": "88650 / 89100",
      "stop": "89450",
      "take_profits": "87500, 86800, 85800"
    }
  },
  "update_payload_raw": {
    "operations": []
  },
  "report_payload_raw": {
    "events": [],
    "reported_result": null,
    "notes": []
  },
  "targets_raw": [],
  "warnings": [],
  "confidence": 0.96,
  "diagnostics": {}
}
```

Variabili ammissibili qui:

- `entry_structure`: `ONE_SHOT | TWO_STEP | RANGE | LADDER`
- `entry_type`: `MARKET | LIMIT | null`
- `role`: `PRIMARY | AVERAGING | RANGE_LOW | RANGE_HIGH | REENTRY | UNKNOWN`
- `risk_hint.unit`: `PERCENT | ABSOLUTE | UNKNOWN`

### 2. Update `SET_STOP` a breakeven

```json
{
  "message_type_hint": "UPDATE",
  "intents_detected": ["U_MOVE_STOP_TO_BE"],
  "primary_intent_hint": "U_MOVE_STOP_TO_BE",
  "instrument": {
    "symbol": null,
    "side": null,
    "market_type": null
  },
  "signal_payload_raw": {
    "entry_structure": null,
    "entries": [],
    "stop_loss": null,
    "take_profits": [],
    "risk_hint": null,
    "raw_fragments": {}
  },
  "update_payload_raw": {
    "operations": [
      {
        "op_type": "SET_STOP",
        "set_stop": {
          "target_type": "ENTRY",
          "value": null
        },
        "close": null,
        "cancel_pending": null,
        "modify_entries": null,
        "modify_targets": null,
        "source_intent": "U_MOVE_STOP_TO_BE"
      }
    ]
  },
  "report_payload_raw": {
    "events": [],
    "reported_result": null,
    "notes": []
  },
  "targets_raw": [
    {
      "kind": "REPLY",
      "value": 1701
    }
  ],
  "warnings": [],
  "confidence": 0.91,
  "diagnostics": {}
}
```

Variabili ammissibili per `SET_STOP`:

- `target_type="ENTRY"` con `value=null`
- `target_type="PRICE"` con `value` numerico
- `target_type="TP_LEVEL"` con `value` intero

### 3. Update `SET_STOP` a prezzo

```json
{
  "message_type_hint": "UPDATE",
  "intents_detected": ["U_MOVE_STOP"],
  "primary_intent_hint": "U_MOVE_STOP",
  "update_payload_raw": {
    "operations": [
      {
        "op_type": "SET_STOP",
        "set_stop": {
          "target_type": "PRICE",
          "value": 89950.0
        },
        "close": null,
        "cancel_pending": null,
        "modify_entries": null,
        "modify_targets": null,
        "source_intent": "U_MOVE_STOP"
      }
    ]
  }
}
```

### 4. Update `SET_STOP` a TP level

```json
{
  "message_type_hint": "UPDATE",
  "intents_detected": ["U_MOVE_STOP"],
  "primary_intent_hint": "U_MOVE_STOP",
  "update_payload_raw": {
    "operations": [
      {
        "op_type": "SET_STOP",
        "set_stop": {
          "target_type": "TP_LEVEL",
          "value": 1
        },
        "close": null,
        "cancel_pending": null,
        "modify_entries": null,
        "modify_targets": null,
        "source_intent": "U_MOVE_STOP"
      }
    ]
  }
}
```

### 5. Update `CLOSE` full

```json
{
  "message_type_hint": "UPDATE",
  "intents_detected": ["U_CLOSE_FULL"],
  "primary_intent_hint": "U_CLOSE_FULL",
  "update_payload_raw": {
    "operations": [
      {
        "op_type": "CLOSE",
        "set_stop": null,
        "close": {
          "close_fraction": null,
          "close_price": 87200.0,
          "close_scope": "FULL"
        },
        "cancel_pending": null,
        "modify_entries": null,
        "modify_targets": null,
        "source_intent": "U_CLOSE_FULL"
      }
    ]
  }
}
```

Variabili ammissibili per `CLOSE`:

- `close_fraction`: `0.0..1.0` oppure `null`
- `close_price`: numero oppure `null`
- `close_scope`: stringa libera, esempi reali `FULL`, `PARTIAL`, `ALL_LONGS`, `ALL_SHORTS`

### 6. Update `CLOSE` partial

```json
{
  "message_type_hint": "UPDATE",
  "intents_detected": ["U_CLOSE_PARTIAL"],
  "primary_intent_hint": "U_CLOSE_PARTIAL",
  "update_payload_raw": {
    "operations": [
      {
        "op_type": "CLOSE",
        "set_stop": null,
        "close": {
          "close_fraction": 0.5,
          "close_price": 87500.0,
          "close_scope": "PARTIAL"
        },
        "cancel_pending": null,
        "modify_entries": null,
        "modify_targets": null,
        "source_intent": "U_CLOSE_PARTIAL"
      }
    ]
  }
}
```

### 7. Update `CANCEL_PENDING`

```json
{
  "message_type_hint": "UPDATE",
  "intents_detected": ["U_CANCEL_PENDING_ORDERS"],
  "primary_intent_hint": "U_CANCEL_PENDING_ORDERS",
  "update_payload_raw": {
    "operations": [
      {
        "op_type": "CANCEL_PENDING",
        "set_stop": null,
        "close": null,
        "cancel_pending": {
          "cancel_scope": "ALL_PENDING_ENTRIES"
        },
        "modify_entries": null,
        "modify_targets": null,
        "source_intent": "U_CANCEL_PENDING_ORDERS"
      }
    ]
  }
}
```

Variabili ammissibili per `cancel_scope`:

- stringa libera
- esempi reali: `ALL_PENDING_ENTRIES`, `REMOVE_PENDING_ENTRY`

### 8. Update `MODIFY_ENTRIES` add

```json
{
  "message_type_hint": "UPDATE",
  "intents_detected": ["U_ADD_ENTRY"],
  "primary_intent_hint": "U_ADD_ENTRY",
  "update_payload_raw": {
    "operations": [
      {
        "op_type": "MODIFY_ENTRIES",
        "set_stop": null,
        "close": null,
        "cancel_pending": null,
        "modify_entries": {
          "mode": "ADD",
          "entries": [
            {
              "sequence": 1,
              "entry_type": "LIMIT",
              "price": 86400.0,
              "role": "UNKNOWN",
              "size_hint": null,
              "is_optional": null
            }
          ]
        },
        "modify_targets": null,
        "source_intent": "U_ADD_ENTRY"
      }
    ]
  }
}
```

Variabili ammissibili per `modify_entries.mode`:

- `ADD`
- `REENTER`
- `UPDATE`
- `REMOVE`
- `REPLACE_ALL`

Nota: il bridge attuale usa soprattutto `ADD` e `REENTER`.

### 9. Update `MODIFY_ENTRIES` reenter

```json
{
  "message_type_hint": "UPDATE",
  "intents_detected": ["U_REENTER"],
  "primary_intent_hint": "U_REENTER",
  "update_payload_raw": {
    "operations": [
      {
        "op_type": "MODIFY_ENTRIES",
        "set_stop": null,
        "close": null,
        "cancel_pending": null,
        "modify_entries": {
          "mode": "REENTER",
          "entries": [
            {
              "sequence": 1,
              "entry_type": "LIMIT",
              "price": 88650.0,
              "role": "REENTRY",
              "size_hint": null,
              "is_optional": false
            }
          ]
        },
        "modify_targets": null,
        "source_intent": "U_REENTER"
      }
    ]
  }
}
```

### 10. Update `MODIFY_TARGETS`

```json
{
  "message_type_hint": "UPDATE",
  "intents_detected": ["U_UPDATE_TAKE_PROFITS"],
  "primary_intent_hint": "U_UPDATE_TAKE_PROFITS",
  "update_payload_raw": {
    "operations": [
      {
        "op_type": "MODIFY_TARGETS",
        "set_stop": null,
        "close": null,
        "cancel_pending": null,
        "modify_entries": null,
        "modify_targets": {
          "mode": "REPLACE_ALL",
          "take_profits": [
            {
              "sequence": 1,
              "price": 87500.0,
              "label": "TP1",
              "close_fraction": null
            },
            {
              "sequence": 2,
              "price": 86800.0,
              "label": "TP2",
              "close_fraction": null
            }
          ],
          "target_tp_level": null
        },
        "source_intent": "U_UPDATE_TAKE_PROFITS"
      }
    ]
  }
}
```

Variabili ammissibili per `modify_targets.mode`:

- `REPLACE_ALL`
- `ADD`
- `UPDATE_ONE`
- `REMOVE_ONE`

### 11. Report `ENTRY_FILLED`

```json
{
  "message_type_hint": "UPDATE",
  "intents_detected": ["U_MARK_FILLED"],
  "primary_intent_hint": "U_MARK_FILLED",
  "report_payload_raw": {
    "events": [
      {
        "event_type": "ENTRY_FILLED",
        "level": null,
        "price": 88650.0,
        "result": null
      }
    ],
    "reported_result": null,
    "notes": []
  }
}
```

### 12. Report `TP_HIT`

```json
{
  "message_type_hint": "UPDATE",
  "intents_detected": ["U_TP_HIT"],
  "primary_intent_hint": "U_TP_HIT",
  "report_payload_raw": {
    "events": [
      {
        "event_type": "TP_HIT",
        "level": 1,
        "price": 87500.0,
        "result": {
          "value": 2.0,
          "unit": "R",
          "text": "+2R"
        }
      }
    ],
    "reported_result": {
      "value": 2.0,
      "unit": "R",
      "text": "+2R"
    },
    "notes": []
  }
}
```

Variabili ammissibili per `reported_result.unit`:

- `R`
- `PERCENT`
- `TEXT`
- `UNKNOWN`

### 13. Report `STOP_HIT`

```json
{
  "message_type_hint": "UPDATE",
  "intents_detected": ["U_STOP_HIT"],
  "primary_intent_hint": "U_STOP_HIT",
  "report_payload_raw": {
    "events": [
      {
        "event_type": "STOP_HIT",
        "level": null,
        "price": 89450.0,
        "result": {
          "value": -1.0,
          "unit": "R",
          "text": "-1R"
        }
      }
    ],
    "reported_result": {
      "value": -1.0,
      "unit": "R",
      "text": "-1R"
    },
    "notes": []
  }
}
```

### 14. Report `BREAKEVEN_EXIT`

```json
{
  "message_type_hint": "UPDATE",
  "intents_detected": ["U_EXIT_BE"],
  "primary_intent_hint": "U_EXIT_BE",
  "report_payload_raw": {
    "events": [
      {
        "event_type": "BREAKEVEN_EXIT",
        "level": null,
        "price": 0.0,
        "result": {
          "value": 0.0,
          "unit": "R",
          "text": "BE"
        }
      }
    ],
    "reported_result": {
      "value": 0.0,
      "unit": "R",
      "text": "BE"
    },
    "notes": []
  }
}
```

### 15. Report `FINAL_RESULT`

```json
{
  "message_type_hint": "INFO_ONLY",
  "intents_detected": ["U_REPORT_FINAL_RESULT"],
  "primary_intent_hint": "U_REPORT_FINAL_RESULT",
  "report_payload_raw": {
    "events": [
      {
        "event_type": "FINAL_RESULT",
        "level": null,
        "price": null,
        "result": {
          "value": 6.5,
          "unit": "PERCENT",
          "text": "+6.5%"
        }
      }
    ],
    "reported_result": {
      "value": 6.5,
      "unit": "PERCENT",
      "text": "+6.5%"
    },
    "notes": []
  }
}
```

### 16. Messaggio composito `UPDATE + REPORT`

Questo e un caso supportato davvero dal bridge attuale.

```json
{
  "message_type_hint": "UPDATE",
  "intents_detected": ["U_TP_HIT", "U_CLOSE_PARTIAL"],
  "primary_intent_hint": "U_TP_HIT",
  "instrument": {
    "symbol": "BTCUSDT",
    "side": "SHORT",
    "market_type": null
  },
  "signal_payload_raw": {
    "entry_structure": null,
    "entries": [],
    "stop_loss": null,
    "take_profits": [],
    "risk_hint": null,
    "raw_fragments": {}
  },
  "update_payload_raw": {
    "operations": [
      {
        "op_type": "CLOSE",
        "set_stop": null,
        "close": {
          "close_fraction": 0.5,
          "close_price": 87500.0,
          "close_scope": "PARTIAL"
        },
        "cancel_pending": null,
        "modify_entries": null,
        "modify_targets": null,
        "source_intent": "U_CLOSE_PARTIAL"
      }
    ]
  },
  "report_payload_raw": {
    "events": [
      {
        "event_type": "TP_HIT",
        "level": 1,
        "price": 87500.0,
        "result": {
          "value": 2.0,
          "unit": "R",
          "text": "+2R"
        }
      }
    ],
    "reported_result": {
      "value": 2.0,
      "unit": "R",
      "text": "+2R"
    },
    "notes": []
  },
  "targets_raw": [
    {
      "kind": "REPLY",
      "value": 1701
    }
  ],
  "warnings": [],
  "confidence": 0.94,
  "diagnostics": {}
}
```

### 17. Messaggio informativo puro

```json
{
  "message_type_hint": "INFO_ONLY",
  "intents_detected": ["U_RISK_NOTE"],
  "primary_intent_hint": "U_RISK_NOTE",
  "instrument": {
    "symbol": "BTCUSDT",
    "side": null,
    "market_type": null
  },
  "signal_payload_raw": {
    "entry_structure": null,
    "entries": [],
    "stop_loss": null,
    "take_profits": [],
    "risk_hint": {
      "value": 1.0,
      "unit": "PERCENT",
      "raw": "risk 1%"
    },
    "raw_fragments": {}
  },
  "update_payload_raw": {
    "operations": []
  },
  "report_payload_raw": {
    "events": [],
    "reported_result": null,
    "notes": []
  },
  "targets_raw": [],
  "warnings": [],
  "confidence": 0.7,
  "diagnostics": {}
}
```

## Limiti attuali

- l'envelope e unico come contratto di bridge, ma i trader non lo emettono ancora nativamente
- alcuni intent esistono nei profili ma non hanno ancora una business shape dedicata oltre al fallback corrente
- `U_REVERSE_SIGNAL` oggi perde la componente di nuovo segnale e viene tradotto solo in `CLOSE`
- `U_TP_HIT_EXPLICIT` non ha ancora un mapping distinto nell'envelope
- `target_scope` e `linking` non sono campi top-level dell'envelope: restano in `diagnostics`

## Conclusione operativa

`TraderEventEnvelopeV1` va considerato:

- unico per tutti i profili
- minimale
- parser-side
- derivato da `intents + entities + target_refs + reported_results`
- intermedio rispetto a `CanonicalMessage`

Il punto importante non e uniformare subito tutti i parser trader, ma far passare tutti dallo stesso adapter centrale con le stesse precedenze e la stessa shape.
