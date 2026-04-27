# Contratti previsti: `TraderParseResult`, `TraderEventEnvelopeV1`, `CanonicalMessage`

## Scopo

Questo documento distingue tre shape diverse del parser:

1. `TraderParseResult` — output attuale/legacy dei parser trader-specifici
2. `TraderEventEnvelopeV1` — contratto intermedio proposto per uniformare il parser-side
3. `CanonicalMessage` — modello canonico finale downstream

---

## 1) `TraderParseResult`

## Stato
**Usato oggi nel runtime** come output dei parser trader-specifici.

## Ruolo
Serve come shape legacy/attuale prodotta dai parser per trader (`trader_a`, `trader_b`, ecc.).  
È già abbastanza strutturata, ma resta ancora vicina al mondo parser-specifico.

## Contratto previsto

```text
TraderParseResult
├─ message_type: str
├─ intents: list[str]
├─ entities: dict[str, Any]
├─ target_refs: list[dict[str, Any]]
├─ reported_results: list[dict[str, Any]]
├─ warnings: list[str]
├─ confidence: float
├─ primary_intent: str | None
├─ actions_structured: list[dict[str, Any]]
├─ target_scope: dict[str, Any]
├─ linking: dict[str, Any]
└─ diagnostics: dict[str, Any]
```

## Tipi campo per campo

| Campo | Tipo | Obbl. | Note |
|---|---|---:|---|
| `message_type` | `str` | sì | Tipicamente: `NEW_SIGNAL`, `UPDATE`, `INFO_ONLY`, `SETUP_INCOMPLETE`, `UNCLASSIFIED` |
| `intents` | `list[str]` | sì | Lista intenti rilevati |
| `entities` | `dict[str, Any]` | sì | Payload legacy/semi-strutturato |
| `target_refs` | `list[dict[str, Any]]` | sì | Riferimenti target (reply, link, symbol, ecc.) |
| `reported_results` | `list[dict[str, Any]]` | sì | Risultati riportati dal trader |
| `warnings` | `list[str]` | sì | Warning parser |
| `confidence` | `float` | sì | Confidenza parser |
| `primary_intent` | `str \| None` | no | Intent principale |
| `actions_structured` | `list[dict[str, Any]]` | sì | Payload v2 strutturato, ma non ancora canonico finale |
| `target_scope` | `dict[str, Any]` | sì | Ambito target già arricchito |
| `linking` | `dict[str, Any]` | sì | Informazioni di linking |
| `diagnostics` | `dict[str, Any]` | sì | Diagnostica/add-on |

## JSON esempio

```json
{
  "message_type": "UPDATE",
  "intents": ["U_MOVE_STOP_TO_BE", "U_TP_HIT"],
  "entities": {
    "symbol": "BTCUSDT",
    "new_stop_level": "ENTRY",
    "hit_target": "TP1",
    "close_fraction": 0.5
  },
  "target_refs": [
    {
      "kind": "REPLY",
      "ref": 1701
    }
  ],
  "reported_results": [
    {
      "value": 2.0,
      "unit": "R",
      "text": "+2R"
    }
  ],
  "warnings": [],
  "confidence": 0.93,
  "primary_intent": "U_MOVE_STOP_TO_BE",
  "actions_structured": [
    {
      "action_type": "MOVE_STOP_TO_BE",
      "confidence": 0.7,
      "applies_to": {
        "scope_type": "reply",
        "scope_value": 1701
      }
    }
  ],
  "target_scope": {
    "scope_type": "reply",
    "scope_value": 1701
  },
  "linking": {
    "reply_to_message_id": 1701
  },
  "diagnostics": {}
}
```

---

## 2) `TraderEventEnvelopeV1`

## Stato
**Contratto intermedio proposto** nelle doc di migrazione.  
Nel branch `main` è una shape architetturale/documentale; non è ancora il centro consolidato del runtime.

## Ruolo
Serve come **adapter target** tra il legacy `TraderParseResult` e il modello canonico finale `CanonicalMessage`.

Obiettivo:
- togliere fallback sparsi
- avere un solo punto di conversione
- dare al normalizer un input parser-side uniforme

## Contratto previsto

```text
TraderEventEnvelopeV1
├─ schema_version: str
├─ message_type_hint: str | None
├─ intents_detected: list[str]
├─ primary_intent_hint: str | None
├─ instrument: InstrumentRaw
├─ signal_payload_raw: SignalPayloadRaw
├─ update_payload_raw: UpdatePayloadRaw
├─ report_payload_raw: ReportPayloadRaw
├─ targets_raw: list[TargetRefRaw]
├─ warnings: list[str]
├─ confidence: float
└─ diagnostics: dict[str, Any]
```

## Tipi top-level

| Campo | Tipo | Obbl. | Note |
|---|---|---:|---|
| `schema_version` | `str` | sì | Es. `trader_event_envelope_v1` |
| `message_type_hint` | `str \| None` | no | Hint dal legacy |
| `intents_detected` | `list[str]` | sì | Intenti rilevati |
| `primary_intent_hint` | `str \| None` | no | Intent principale parser-side |
| `instrument` | `object` | sì | Dati strumento raw |
| `signal_payload_raw` | `object` | sì | Dati raw segnale |
| `update_payload_raw` | `object` | sì | Dati raw update |
| `report_payload_raw` | `object` | sì | Dati raw report |
| `targets_raw` | `list[object]` | sì | Target raw non ancora normalizzati completamente |
| `warnings` | `list[str]` | sì | Warning conservati |
| `confidence` | `float` | sì | Confidenza parser-side |
| `diagnostics` | `dict[str, Any]` | sì | Diagnostica, compresi eventuali residui legacy |

## Sottotipi previsti

### `InstrumentRaw`

```json
{
  "symbol": "BTCUSDT",
  "side": "SHORT",
  "market_type": "FUTURES"
}
```

| Campo | Tipo |
|---|---|
| `symbol` | `str \| None` |
| `side` | `"LONG" \| "SHORT" \| None` |
| `market_type` | `str \| None` |

### `SignalPayloadRaw`

```json
{
  "entry_structure": "TWO_STEP",
  "entries": [
    {
      "sequence": 1,
      "entry_type": "LIMIT",
      "price": 88650.0,
      "size_hint": "1/3"
    },
    {
      "sequence": 2,
      "entry_type": "LIMIT",
      "price": 89100.0,
      "size_hint": "2/3"
    }
  ],
  "stop_loss": {
    "price": 89450.0
  },
  "take_profits": [
    { "sequence": 1, "price": 87500.0 },
    { "sequence": 2, "price": 86800.0 },
    { "sequence": 3, "price": 85800.0 }
  ],
  "risk_hint": {
    "value": 1.0,
    "unit": "PERCENT",
    "raw": "1% dep"
  },
  "raw_fragments": {
    "entry": "Entry 88650-89100",
    "stop": "SL 89450"
  }
}
```

### `UpdatePayloadRaw`

```json
{
  "operations": [
    {
      "op_type": "SET_STOP",
      "set_stop": {
        "target_type": "ENTRY",
        "value": null
      },
      "source_intent": "U_MOVE_STOP_TO_BE"
    },
    {
      "op_type": "CLOSE",
      "close": {
        "close_fraction": 0.5,
        "close_price": 87500.0,
        "close_scope": "PARTIAL"
      },
      "source_intent": "U_CLOSE_PARTIAL"
    }
  ]
}
```

### `ReportPayloadRaw`

```json
{
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
```

### `TargetRefRaw`

```json
{
  "kind": "REPLY",
  "value": 1701
}
```

| Campo | Tipo |
|---|---|
| `kind` | `str` |
| `value` | `str \| int \| null` |

## JSON esempio completo

```json
{
  "schema_version": "trader_event_envelope_v1",
  "message_type_hint": "UPDATE",
  "intents_detected": ["U_MOVE_STOP_TO_BE", "U_TP_HIT", "U_CLOSE_PARTIAL"],
  "primary_intent_hint": "U_MOVE_STOP_TO_BE",
  "instrument": {
    "symbol": "BTCUSDT",
    "side": "SHORT",
    "market_type": "FUTURES"
  },
  "signal_payload_raw": {},
  "update_payload_raw": {
    "operations": [
      {
        "op_type": "SET_STOP",
        "set_stop": {
          "target_type": "ENTRY",
          "value": null
        },
        "source_intent": "U_MOVE_STOP_TO_BE"
      },
      {
        "op_type": "CLOSE",
        "close": {
          "close_fraction": 0.5,
          "close_price": 87500.0,
          "close_scope": "PARTIAL"
        },
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
  "confidence": 0.93,
  "diagnostics": {
    "legacy_linking": {
      "reply_to_message_id": 1701
    }
  }
}
```

---

## 3) `CanonicalMessage`

## Stato
**Modello canonico reale nel codice attuale**, definito in `src/parser/canonical_v1/models.py`.

## Ruolo
È la **shape finale canonica** che il downstream dovrebbe leggere.

Non è solo “più semplice”: è **più rigorosa e semanticamente pulita**.

## Contratto previsto (top-level)

```text
CanonicalMessage
├─ schema_version: str
├─ parser_profile: str
├─ primary_class: "SIGNAL" | "UPDATE" | "REPORT" | "INFO"
├─ parse_status: "PARSED" | "PARTIAL" | "UNCLASSIFIED" | "ERROR"
├─ confidence: float
├─ intents: list[str]
├─ primary_intent: str | None
├─ targeting: Targeting | None
├─ signal: SignalPayload | None
├─ update: UpdatePayload | None
├─ report: ReportPayload | None
├─ warnings: list[str]
├─ diagnostics: dict[str, Any]
└─ raw_context: RawContext
```

## Tipi top-level

| Campo | Tipo | Obbl. | Note |
|---|---|---:|---|
| `schema_version` | `str` | sì | Default `1.0` |
| `parser_profile` | `str` | sì | Profilo parser che ha prodotto il messaggio |
| `primary_class` | `"SIGNAL" \| "UPDATE" \| "REPORT" \| "INFO"` | sì | Classe principale |
| `parse_status` | `"PARSED" \| "PARTIAL" \| "UNCLASSIFIED" \| "ERROR"` | sì | Stato parsing canonico |
| `confidence` | `float` | sì | `0.0..1.0` |
| `intents` | `list[str]` | sì | Intenti canonici |
| `primary_intent` | `str \| None` | no | Intent principale |
| `targeting` | `Targeting \| None` | no | Targeting già normalizzato |
| `signal` | `SignalPayload \| None` | no | Solo per `primary_class=SIGNAL` |
| `update` | `UpdatePayload \| None` | no | Solo per `primary_class=UPDATE` |
| `report` | `ReportPayload \| None` | no | Solo per `primary_class=REPORT` |
| `warnings` | `list[str]` | sì | Warning canonici |
| `diagnostics` | `dict[str, Any]` | sì | Diagnostica |
| `raw_context` | `RawContext` | sì | Contesto messaggio originale |

## Sottotipi principali

### `RawContext`

```json
{
  "raw_text": "move stop to breakeven",
  "reply_to_message_id": 1701,
  "extracted_links": [],
  "hashtags": [],
  "source_chat_id": "-1001234567890",
  "source_topic_id": 42,
  "acquisition_mode": "live"
}
```

### `Targeting`

```json
{
  "refs": [
    {
      "ref_type": "REPLY",
      "value": 1701
    }
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
```

### `SignalPayload`

```json
{
  "symbol": "BTCUSDT",
  "side": "SHORT",
  "entry_structure": "TWO_STEP",
  "entries": [
    {
      "sequence": 1,
      "entry_type": "LIMIT",
      "price": { "raw": "88650", "value": 88650.0 },
      "role": "PRIMARY",
      "size_hint": "1/3",
      "note": null,
      "is_optional": false
    },
    {
      "sequence": 2,
      "entry_type": "LIMIT",
      "price": { "raw": "89100", "value": 89100.0 },
      "role": "AVERAGING",
      "size_hint": "2/3",
      "note": null,
      "is_optional": false
    }
  ],
  "stop_loss": {
    "price": { "raw": "89450", "value": 89450.0 }
  },
  "take_profits": [
    {
      "sequence": 1,
      "price": { "raw": "87500", "value": 87500.0 },
      "label": "TP1",
      "close_fraction": null
    }
  ],
  "leverage_hint": 5.0,
  "risk_hint": {
    "raw": "1% dep",
    "value": 1.0,
    "unit": "PERCENT"
  },
  "invalidation_rule": null,
  "conditions": null,
  "completeness": "COMPLETE",
  "missing_fields": [],
  "raw_fragments": {}
}
```

### `UpdatePayload`

```json
{
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
      "raw_fragment": "stop to entry",
      "confidence": 0.94
    }
  ]
}
```

### `ReportPayload`

```json
{
  "events": [
    {
      "event_type": "TP_HIT",
      "level": 1,
      "price": { "raw": "87500", "value": 87500.0 },
      "result": {
        "value": 2.0,
        "unit": "R",
        "text": "+2R"
      },
      "raw_fragment": "tp1 hit +2R",
      "confidence": 0.92
    }
  ],
  "reported_result": {
    "value": 2.0,
    "unit": "R",
    "text": "+2R"
  },
  "notes": []
}
```

## JSON esempio completo

```json
{
  "schema_version": "1.0",
  "parser_profile": "trader_c",
  "primary_class": "UPDATE",
  "parse_status": "PARSED",
  "confidence": 0.93,
  "intents": ["U_MOVE_STOP_TO_BE"],
  "primary_intent": "U_MOVE_STOP_TO_BE",
  "targeting": {
    "refs": [
      {
        "ref_type": "REPLY",
        "value": 1701
      }
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
  "signal": null,
  "update": {
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
        "raw_fragment": "stop to breakeven",
        "confidence": 0.94
      }
    ]
  },
  "report": null,
  "warnings": [],
  "diagnostics": {},
  "raw_context": {
    "raw_text": "stop to breakeven",
    "reply_to_message_id": 1701,
    "extracted_links": [],
    "hashtags": [],
    "source_chat_id": "-1001234567890",
    "source_topic_id": 42,
    "acquisition_mode": "live"
  }
}
```

---

## 4) Sintesi finale

## Sono la stessa cosa?
**No.**

| Shape | Ruolo | Stato |
|---|---|---|
| `TraderParseResult` | output parser legacy/attuale | usato oggi |
| `TraderEventEnvelopeV1` | contratto intermedio parser-side unificato | target architetturale/documentale |
| `CanonicalMessage` | modello canonico finale downstream | reale nel codice |

## Flusso ideale finale

```text
TraderParseResult
→ TraderEventEnvelopeV1
→ CanonicalMessage
```

## Target architetturale desiderato

Alla fine, l’architettura più pulita sembra essere:

- parser -> `TraderEventEnvelopeV1`
- normalizer -> `CanonicalMessage`

con eliminazione progressiva di `TraderParseResult` come shape stabile pubblica.
