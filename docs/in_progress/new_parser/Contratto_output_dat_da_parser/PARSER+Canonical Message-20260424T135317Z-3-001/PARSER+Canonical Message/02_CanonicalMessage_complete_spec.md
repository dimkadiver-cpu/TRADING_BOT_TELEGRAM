# CanonicalMessage — contratto completo

## Stato del contratto

`CanonicalMessage` è il **modello canonico finale reale** definito nel repository attuale.

È la shape downstream che rappresenta il messaggio parserizzato in forma:

- più rigorosa
- più validata
- più semantica
- meno dipendente dal trader specifico

---

# 1. Scopo

`CanonicalMessage` serve a rappresentare un messaggio parserizzato in forma canonica e stabile.

Funzioni principali:

- classificare il messaggio in una classe primaria
- esprimere lo stato di parsing
- contenere payload business distinti:
  - `signal`
  - `update`
  - `report`
- contenere targeting già normalizzato
- mantenere il `raw_context`

---

# 2. Struttura top-level

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

---

# 3. Tipi top-level

| Campo | Tipo | Obbl. | Note |
|---|---|---:|---|
| `schema_version` | `str` | sì | Default `1.0` |
| `parser_profile` | `str` | sì | Profilo parser che ha prodotto il messaggio |
| `primary_class` | `"SIGNAL" \| "UPDATE" \| "REPORT" \| "INFO"` | sì | Classe business principale |
| `parse_status` | `"PARSED" \| "PARTIAL" \| "UNCLASSIFIED" \| "ERROR"` | sì | Stato parsing canonico |
| `confidence` | `float` | sì | Range `0..1` |
| `intents` | `list[str]` | sì | Intenti canonici |
| `primary_intent` | `str \| None` | no | Intent principale |
| `targeting` | `Targeting \| None` | no | Targeting normalizzato |
| `signal` | `SignalPayload \| None` | no | Presente per messaggi `SIGNAL` |
| `update` | `UpdatePayload \| None` | no | Presente per messaggi `UPDATE` |
| `report` | `ReportPayload \| None` | no | Presente per messaggi `REPORT` |
| `warnings` | `list[str]` | sì | Warning canonici |
| `diagnostics` | `dict[str, Any]` | sì | Diagnostica |
| `raw_context` | `RawContext` | sì | Contesto raw di origine |

---

# 4. Literal e sottotipi principali

## 4.1 `MessageClass`

```text
"SIGNAL" | "UPDATE" | "REPORT" | "INFO"
```

## 4.2 `ParseStatus`

```text
"PARSED" | "PARTIAL" | "UNCLASSIFIED" | "ERROR"
```

## 4.3 `Side`

```text
"LONG" | "SHORT"
```

## 4.4 `EntryType`

```text
"MARKET" | "LIMIT"
```

## 4.5 `EntryStructure`

```text
"ONE_SHOT" | "TWO_STEP" | "RANGE" | "LADDER"
```

## 4.6 `TargetingStrategy`

```text
"REPLY_OR_LINK" | "SYMBOL_MATCH" | "GLOBAL_SCOPE" | "UNRESOLVED"
```

## 4.7 `TargetScopeKind`

```text
"SINGLE_SIGNAL" | "SYMBOL" | "PORTFOLIO_SIDE" | "ALL_OPEN" | "UNKNOWN"
```

## 4.8 `TargetRefType`

```text
"REPLY" | "TELEGRAM_LINK" | "MESSAGE_ID" | "EXPLICIT_ID" | "SYMBOL"
```

## 4.9 `UpdateOperationType`

```text
"SET_STOP" | "CLOSE" | "CANCEL_PENDING" | "MODIFY_ENTRIES" | "MODIFY_TARGETS"
```

## 4.10 `StopTargetType`

```text
"PRICE" | "ENTRY" | "TP_LEVEL"
```

## 4.11 `ModifyEntriesMode`

```text
"ADD" | "REENTER" | "UPDATE"
```

## 4.12 `ModifyTargetsMode`

```text
"REPLACE_ALL" | "ADD" | "UPDATE_ONE" | "REMOVE_ONE"
```

## 4.13 `ReportEventType`

```text
"ENTRY_FILLED" | "TP_HIT" | "STOP_HIT" | "BREAKEVEN_EXIT" | "FINAL_RESULT"
```

## 4.14 `ResultUnit`

```text
"R" | "PERCENT" | "TEXT" | "UNKNOWN"
```

## 4.15 `RiskHintUnit`

```text
"PERCENT" | "ABSOLUTE" | "UNKNOWN"
```

---

# 5. `RawContext`

```text
RawContext
├─ raw_text: str
├─ reply_to_message_id: int | None
├─ extracted_links: list[str]
├─ hashtags: list[str]
├─ source_chat_id: str | None
├─ source_topic_id: int | None
└─ acquisition_mode: "live" | "catchup" | None
```

## JSON esempio

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

---

# 6. `Targeting`

```text
Targeting
├─ refs: list[TargetRef]
├─ scope: TargetScope
├─ strategy: TargetingStrategy
└─ targeted: bool
```

## 6.1 `TargetRef`

```text
TargetRef
├─ ref_type: TargetRefType
└─ value: str | int
```

## 6.2 `TargetScope`

```text
TargetScope
├─ kind: TargetScopeKind
├─ value: str | None
├─ side_filter: Side | None
└─ applies_to_all: bool
```

---

# 7. Casi `Targeting` previsti

## 7.1 Reply / link targeting

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

## 7.2 Symbol match

```json
{
  "refs": [
    {
      "ref_type": "SYMBOL",
      "value": "BTCUSDT"
    }
  ],
  "scope": {
    "kind": "SYMBOL",
    "value": "BTCUSDT",
    "side_filter": null,
    "applies_to_all": false
  },
  "strategy": "SYMBOL_MATCH",
  "targeted": true
}
```

## 7.3 Global scope — all long

```json
{
  "refs": [],
  "scope": {
    "kind": "PORTFOLIO_SIDE",
    "value": "all_long",
    "side_filter": "LONG",
    "applies_to_all": true
  },
  "strategy": "GLOBAL_SCOPE",
  "targeted": true
}
```

## 7.4 Global scope — all open

```json
{
  "refs": [],
  "scope": {
    "kind": "ALL_OPEN",
    "value": "all_positions",
    "side_filter": null,
    "applies_to_all": true
  },
  "strategy": "GLOBAL_SCOPE",
  "targeted": true
}
```

## 7.5 Unresolved

```json
{
  "refs": [],
  "scope": {
    "kind": "UNKNOWN",
    "value": null,
    "side_filter": null,
    "applies_to_all": false
  },
  "strategy": "UNRESOLVED",
  "targeted": false
}
```

---

# 8. `Price`

```text
Price
├─ raw: str
└─ value: float
```

## JSON esempio

```json
{
  "raw": "2114",
  "value": 2114.0
}
```

---

# 9. `SignalPayload`

```text
SignalPayload
├─ symbol: str | None
├─ side: Side | None
├─ entry_structure: EntryStructure | None
├─ entries: list[EntryLeg]
├─ stop_loss: StopLoss | None
├─ take_profits: list[TakeProfit]
├─ leverage_hint: float | None
├─ risk_hint: RiskHint | None
├─ invalidation_rule: str | None
├─ conditions: str | None
├─ completeness: "COMPLETE" | "INCOMPLETE" | None
├─ missing_fields: list[str]
└─ raw_fragments: dict[str, str | None]
```

## 9.1 `EntryLeg`

```text
EntryLeg
├─ sequence: int
├─ entry_type: EntryType
├─ price: Price | None
├─ role: "PRIMARY" | "AVERAGING" | "UNKNOWN"
├─ size_hint: str | None
├─ note: str | None
└─ is_optional: bool
```

## 9.2 `StopLoss`

```text
StopLoss
└─ price: Price | None
```

## 9.3 `TakeProfit`

```text
TakeProfit
├─ sequence: int
├─ price: Price
├─ label: str | None
└─ close_fraction: float | None
```

## 9.4 `RiskHint`

```text
RiskHint
├─ raw: str | None
├─ value: float | None
└─ unit: RiskHintUnit
```

---

# 10. Casi `SignalPayload` previsti

## 10.1 ONE_SHOT

```json
{
  "symbol": "ETHUSDT",
  "side": "LONG",
  "entry_structure": "ONE_SHOT",
  "entries": [
    {
      "sequence": 1,
      "entry_type": "LIMIT",
      "price": { "raw": "2114", "value": 2114.0 },
      "role": "PRIMARY",
      "size_hint": null,
      "note": null,
      "is_optional": false
    }
  ],
  "stop_loss": {
    "price": { "raw": "2100", "value": 2100.0 }
  },
  "take_profits": [
    {
      "sequence": 1,
      "price": { "raw": "2128", "value": 2128.0 },
      "label": "TP1",
      "close_fraction": null
    },
    {
      "sequence": 2,
      "price": { "raw": "2141", "value": 2141.0 },
      "label": "TP2",
      "close_fraction": null
    }
  ],
  "leverage_hint": null,
  "risk_hint": {
    "raw": "0.3% dep",
    "value": 0.3,
    "unit": "PERCENT"
  },
  "invalidation_rule": null,
  "conditions": null,
  "completeness": "COMPLETE",
  "missing_fields": [],
  "raw_fragments": {}
}
```

## 10.2 TWO_STEP

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
    },
    {
      "sequence": 2,
      "price": { "raw": "86800", "value": 86800.0 },
      "label": "TP2",
      "close_fraction": null
    }
  ],
  "leverage_hint": 3.0,
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

## 10.3 RANGE

```json
{
  "symbol": "ETHUSDT",
  "side": "LONG",
  "entry_structure": "RANGE",
  "entries": [
    {
      "sequence": 1,
      "entry_type": "LIMIT",
      "price": { "raw": "2114", "value": 2114.0 },
      "role": "PRIMARY",
      "size_hint": null,
      "note": "range_low",
      "is_optional": false
    },
    {
      "sequence": 2,
      "entry_type": "LIMIT",
      "price": { "raw": "2122", "value": 2122.0 },
      "role": "PRIMARY",
      "size_hint": null,
      "note": "range_high",
      "is_optional": false
    }
  ],
  "stop_loss": {
    "price": { "raw": "2100", "value": 2100.0 }
  },
  "take_profits": [
    {
      "sequence": 1,
      "price": { "raw": "2128", "value": 2128.0 },
      "label": "TP1",
      "close_fraction": null
    },
    {
      "sequence": 2,
      "price": { "raw": "2141", "value": 2141.0 },
      "label": "TP2",
      "close_fraction": null
    }
  ],
  "leverage_hint": null,
  "risk_hint": {
    "raw": "0.3% dep",
    "value": 0.3,
    "unit": "PERCENT"
  },
  "invalidation_rule": null,
  "conditions": null,
  "completeness": "COMPLETE",
  "missing_fields": [],
  "raw_fragments": {}
}
```

## 10.4 LADDER

```json
{
  "symbol": "XRPUSDT",
  "side": "LONG",
  "entry_structure": "LADDER",
  "entries": [
    {
      "sequence": 1,
      "entry_type": "LIMIT",
      "price": { "raw": "0.50", "value": 0.5 },
      "role": "PRIMARY",
      "size_hint": "25%",
      "note": null,
      "is_optional": false
    },
    {
      "sequence": 2,
      "entry_type": "LIMIT",
      "price": { "raw": "0.49", "value": 0.49 },
      "role": "AVERAGING",
      "size_hint": "25%",
      "note": null,
      "is_optional": false
    },
    {
      "sequence": 3,
      "entry_type": "LIMIT",
      "price": { "raw": "0.48", "value": 0.48 },
      "role": "AVERAGING",
      "size_hint": "25%",
      "note": null,
      "is_optional": false
    }
  ],
  "stop_loss": {
    "price": { "raw": "0.46", "value": 0.46 }
  },
  "take_profits": [
    {
      "sequence": 1,
      "price": { "raw": "0.52", "value": 0.52 },
      "label": "TP1",
      "close_fraction": 0.33
    },
    {
      "sequence": 2,
      "price": { "raw": "0.54", "value": 0.54 },
      "label": "TP2",
      "close_fraction": 0.33
    },
    {
      "sequence": 3,
      "price": { "raw": "0.57", "value": 0.57 },
      "label": "TP3",
      "close_fraction": 0.34
    }
  ],
  "leverage_hint": 5.0,
  "risk_hint": null,
  "invalidation_rule": null,
  "conditions": null,
  "completeness": "COMPLETE",
  "missing_fields": [],
  "raw_fragments": {}
}
```

## 10.5 Segnale incompleto / PARTIAL

```json
{
  "symbol": "SOLUSDT",
  "side": "LONG",
  "entry_structure": "ONE_SHOT",
  "entries": [
    {
      "sequence": 1,
      "entry_type": "LIMIT",
      "price": { "raw": "150", "value": 150.0 },
      "role": "PRIMARY",
      "size_hint": null,
      "note": null,
      "is_optional": false
    }
  ],
  "stop_loss": null,
  "take_profits": [],
  "leverage_hint": null,
  "risk_hint": null,
  "invalidation_rule": null,
  "conditions": null,
  "completeness": "INCOMPLETE",
  "missing_fields": ["stop_loss", "take_profits"],
  "raw_fragments": {}
}
```

---

# 11. `UpdatePayload`

```text
UpdatePayload
└─ operations: list[UpdateOperation]
```

## 11.1 `UpdateOperation`

```text
UpdateOperation
├─ op_type: UpdateOperationType
├─ set_stop: StopTarget | None
├─ close: CloseOperation | None
├─ cancel_pending: CancelPendingOperation | None
├─ modify_entries: ModifyEntriesOperation | None
├─ modify_targets: ModifyTargetsOperation | None
├─ raw_fragment: str | None
└─ confidence: float | None
```

## 11.2 `StopTarget`

```text
StopTarget
├─ target_type: StopTargetType
└─ value: float | int | None
```

## 11.3 `CloseOperation`

```text
CloseOperation
├─ close_fraction: float | None
├─ close_price: Price | None
└─ close_scope: str | None
```

## 11.4 `CancelPendingOperation`

```text
CancelPendingOperation
└─ cancel_scope: str | None
```

## 11.5 `ModifyEntriesOperation`

```text
ModifyEntriesOperation
├─ mode: ModifyEntriesMode
└─ entries: list[EntryLeg]
```

## 11.6 `ModifyTargetsOperation`

```text
ModifyTargetsOperation
├─ mode: ModifyTargetsMode
├─ take_profits: list[TakeProfit]
└─ target_tp_level: int | None
```

---

# 12. Casi `UpdatePayload` previsti

## 12.1 SET_STOP — prezzo

```json
{
  "operations": [
    {
      "op_type": "SET_STOP",
      "set_stop": {
        "target_type": "PRICE",
        "value": 2110.0
      },
      "close": null,
      "cancel_pending": null,
      "modify_entries": null,
      "modify_targets": null,
      "raw_fragment": "move stop 2110",
      "confidence": 0.92
    }
  ]
}
```

## 12.2 SET_STOP — entry / breakeven

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
      "raw_fragment": "stop to breakeven",
      "confidence": 0.94
    }
  ]
}
```

## 12.3 SET_STOP — livello TP

```json
{
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
      "raw_fragment": "stop on tp1",
      "confidence": 0.89
    }
  ]
}
```

## 12.4 CLOSE — partial

```json
{
  "operations": [
    {
      "op_type": "CLOSE",
      "set_stop": null,
      "close": {
        "close_fraction": 0.5,
        "close_price": {
          "raw": "2128",
          "value": 2128.0
        },
        "close_scope": "PARTIAL"
      },
      "cancel_pending": null,
      "modify_entries": null,
      "modify_targets": null,
      "raw_fragment": "close 50%",
      "confidence": 0.95
    }
  ]
}
```

## 12.5 CLOSE — full

```json
{
  "operations": [
    {
      "op_type": "CLOSE",
      "set_stop": null,
      "close": {
        "close_fraction": 1.0,
        "close_price": null,
        "close_scope": "FULL"
      },
      "cancel_pending": null,
      "modify_entries": null,
      "modify_targets": null,
      "raw_fragment": "close full",
      "confidence": 0.95
    }
  ]
}
```

## 12.6 CANCEL_PENDING

```json
{
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
      "raw_fragment": "cancel pending orders",
      "confidence": 0.9
    }
  ]
}
```

## 12.7 MODIFY_ENTRIES — ADD

```json
{
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
            "price": {
              "raw": "2050",
              "value": 2050.0
            },
            "role": "AVERAGING",
            "size_hint": null,
            "note": null,
            "is_optional": false
          }
        ]
      },
      "modify_targets": null,
      "raw_fragment": "add entry 2050",
      "confidence": 0.86
    }
  ]
}
```

## 12.8 MODIFY_ENTRIES — REENTER

```json
{
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
            "price": {
              "raw": "2010",
              "value": 2010.0
            },
            "role": "PRIMARY",
            "size_hint": null,
            "note": "reentry",
            "is_optional": false
          }
        ]
      },
      "modify_targets": null,
      "raw_fragment": "reenter 2010",
      "confidence": 0.83
    }
  ]
}
```

## 12.9 MODIFY_ENTRIES — UPDATE

```json
{
  "operations": [
    {
      "op_type": "MODIFY_ENTRIES",
      "set_stop": null,
      "close": null,
      "cancel_pending": null,
      "modify_entries": {
        "mode": "UPDATE",
        "entries": [
          {
            "sequence": 1,
            "entry_type": "LIMIT",
            "price": {
              "raw": "1995",
              "value": 1995.0
            },
            "role": "PRIMARY",
            "size_hint": null,
            "note": "updated_pending_entry",
            "is_optional": false
          }
        ]
      },
      "modify_targets": null,
      "raw_fragment": "update pending entry 1995",
      "confidence": 0.81
    }
  ]
}
```

## 12.10 MODIFY_TARGETS — REPLACE_ALL

```json
{
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
            "price": { "raw": "2128", "value": 2128.0 },
            "label": "TP1",
            "close_fraction": null
          },
          {
            "sequence": 2,
            "price": { "raw": "2141", "value": 2141.0 },
            "label": "TP2",
            "close_fraction": null
          }
        ],
        "target_tp_level": null
      },
      "raw_fragment": "new tps 2128 2141",
      "confidence": 0.88
    }
  ]
}
```

## 12.11 MODIFY_TARGETS — ADD

```json
{
  "operations": [
    {
      "op_type": "MODIFY_TARGETS",
      "set_stop": null,
      "close": null,
      "cancel_pending": null,
      "modify_entries": null,
      "modify_targets": {
        "mode": "ADD",
        "take_profits": [
          {
            "sequence": 4,
            "price": { "raw": "2200", "value": 2200.0 },
            "label": "TP4",
            "close_fraction": null
          }
        ],
        "target_tp_level": null
      },
      "raw_fragment": "add tp4 2200",
      "confidence": 0.79
    }
  ]
}
```

## 12.12 MODIFY_TARGETS — UPDATE_ONE

```json
{
  "operations": [
    {
      "op_type": "MODIFY_TARGETS",
      "set_stop": null,
      "close": null,
      "cancel_pending": null,
      "modify_entries": null,
      "modify_targets": {
        "mode": "UPDATE_ONE",
        "take_profits": [
          {
            "sequence": 2,
            "price": { "raw": "2145", "value": 2145.0 },
            "label": "TP2",
            "close_fraction": null
          }
        ],
        "target_tp_level": 2
      },
      "raw_fragment": "update tp2 2145",
      "confidence": 0.78
    }
  ]
}
```

## 12.13 MODIFY_TARGETS — REMOVE_ONE

```json
{
  "operations": [
    {
      "op_type": "MODIFY_TARGETS",
      "set_stop": null,
      "close": null,
      "cancel_pending": null,
      "modify_entries": null,
      "modify_targets": {
        "mode": "REMOVE_ONE",
        "take_profits": [],
        "target_tp_level": 3
      },
      "raw_fragment": "remove tp3",
      "confidence": 0.74
    }
  ]
}
```

---

# 13. `ReportPayload`

```text
ReportPayload
├─ events: list[ReportEvent]
├─ reported_result: ReportedResult | None
└─ notes: list[str]
```

## 13.1 `ReportEvent`

```text
ReportEvent
├─ event_type: ReportEventType
├─ level: int | None
├─ price: Price | None
├─ result: ReportedResult | None
├─ raw_fragment: str | None
└─ confidence: float | None
```

## 13.2 `ReportedResult`

```text
ReportedResult
├─ value: float | None
├─ unit: ResultUnit
└─ text: str | None
```

---

# 14. Casi `ReportPayload` previsti

## 14.1 ENTRY_FILLED

```json
{
  "events": [
    {
      "event_type": "ENTRY_FILLED",
      "level": null,
      "price": { "raw": "2114", "value": 2114.0 },
      "result": null,
      "raw_fragment": "entry filled 2114",
      "confidence": 0.92
    }
  ],
  "reported_result": null,
  "notes": []
}
```

## 14.2 TP_HIT

```json
{
  "events": [
    {
      "event_type": "TP_HIT",
      "level": 1,
      "price": { "raw": "2128", "value": 2128.0 },
      "result": {
        "value": 1.0,
        "unit": "R",
        "text": "+1R"
      },
      "raw_fragment": "tp1 hit",
      "confidence": 0.92
    }
  ],
  "reported_result": null,
  "notes": []
}
```

## 14.3 STOP_HIT

```json
{
  "events": [
    {
      "event_type": "STOP_HIT",
      "level": null,
      "price": { "raw": "2100", "value": 2100.0 },
      "result": {
        "value": -1.0,
        "unit": "R",
        "text": "-1R"
      },
      "raw_fragment": "stop hit",
      "confidence": 0.9
    }
  ],
  "reported_result": null,
  "notes": []
}
```

## 14.4 BREAKEVEN_EXIT

```json
{
  "events": [
    {
      "event_type": "BREAKEVEN_EXIT",
      "level": null,
      "price": { "raw": "2114", "value": 2114.0 },
      "result": {
        "value": 0.0,
        "unit": "R",
        "text": "BE"
      },
      "raw_fragment": "exit at breakeven",
      "confidence": 0.89
    }
  ],
  "reported_result": null,
  "notes": []
}
```

## 14.5 FINAL_RESULT

```json
{
  "events": [
    {
      "event_type": "FINAL_RESULT",
      "level": null,
      "price": null,
      "result": {
        "value": 3.2,
        "unit": "R",
        "text": "+3.2R final"
      },
      "raw_fragment": "final result +3.2R",
      "confidence": 0.95
    }
  ],
  "reported_result": {
    "value": 3.2,
    "unit": "R",
    "text": "+3.2R final"
  },
  "notes": []
}
```

---

# 15. Regole top-level di coerenza previste

## 15.1 `primary_class = SIGNAL`
- `signal` deve essere presente
- `update` non deve essere presente
- `report` non deve essere presente

## 15.2 `primary_class = UPDATE`
- `update` deve essere presente
- `signal` non deve essere presente

## 15.3 `primary_class = REPORT`
- `report` deve essere presente
- `signal` e `update` non devono essere presenti

## 15.4 `primary_class = INFO`
- `signal`, `update`, `report` devono essere assenti

## 15.5 `parse_status = PARSED`
In generale:
- il payload principale deve essere coerente e valorizzato
- per `SIGNAL`, devono esistere almeno simbolo, lato, struttura entry, stop e TP
- per `UPDATE`, almeno una operation
- per `REPORT`, almeno un evento o un risultato finale

---

# 16. Esempi completi di `CanonicalMessage`

## 16.1 SIGNAL completo

```json
{
  "schema_version": "1.0",
  "parser_profile": "trader_c",
  "primary_class": "SIGNAL",
  "parse_status": "PARSED",
  "confidence": 0.96,
  "intents": ["NS_CREATE_SIGNAL"],
  "primary_intent": "NS_CREATE_SIGNAL",
  "targeting": null,
  "signal": {
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
      },
      {
        "sequence": 2,
        "price": { "raw": "86800", "value": 86800.0 },
        "label": "TP2",
        "close_fraction": null
      }
    ],
    "leverage_hint": 3.0,
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
  },
  "update": null,
  "report": null,
  "warnings": [],
  "diagnostics": {},
  "raw_context": {
    "raw_text": "BTCUSDT short ...",
    "reply_to_message_id": null,
    "extracted_links": [],
    "hashtags": [],
    "source_chat_id": "-1001234567890",
    "source_topic_id": 42,
    "acquisition_mode": "live"
  }
}
```

## 16.2 UPDATE completo

```json
{
  "schema_version": "1.0",
  "parser_profile": "trader_b",
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
    "source_topic_id": null,
    "acquisition_mode": "live"
  }
}
```

## 16.3 REPORT completo

```json
{
  "schema_version": "1.0",
  "parser_profile": "trader_a",
  "primary_class": "REPORT",
  "parse_status": "PARSED",
  "confidence": 0.95,
  "intents": ["U_REPORT_FINAL_RESULT"],
  "primary_intent": "U_REPORT_FINAL_RESULT",
  "targeting": null,
  "signal": null,
  "update": null,
  "report": {
    "events": [
      {
        "event_type": "FINAL_RESULT",
        "level": null,
        "price": null,
        "result": {
          "value": 3.2,
          "unit": "R",
          "text": "+3.2R final"
        },
        "raw_fragment": "final result +3.2R",
        "confidence": 0.95
      }
    ],
    "reported_result": {
      "value": 3.2,
      "unit": "R",
      "text": "+3.2R final"
    },
    "notes": []
  },
  "warnings": [],
  "diagnostics": {},
  "raw_context": {
    "raw_text": "final result +3.2R",
    "reply_to_message_id": null,
    "extracted_links": [],
    "hashtags": [],
    "source_chat_id": "-1001234567890",
    "source_topic_id": null,
    "acquisition_mode": "live"
  }
}
```

## 16.4 INFO

```json
{
  "schema_version": "1.0",
  "parser_profile": "trader_d",
  "primary_class": "INFO",
  "parse_status": "UNCLASSIFIED",
  "confidence": 0.42,
  "intents": [],
  "primary_intent": null,
  "targeting": null,
  "signal": null,
  "update": null,
  "report": null,
  "warnings": ["informational_only"],
  "diagnostics": {
    "reason": "no_actionable_structure"
  },
  "raw_context": {
    "raw_text": "market looks weak today",
    "reply_to_message_id": null,
    "extracted_links": [],
    "hashtags": [],
    "source_chat_id": "-1001234567890",
    "source_topic_id": null,
    "acquisition_mode": "catchup"
  }
}
```

## 16.5 SIGNAL parziale

```json
{
  "schema_version": "1.0",
  "parser_profile": "trader_c",
  "primary_class": "SIGNAL",
  "parse_status": "PARTIAL",
  "confidence": 0.64,
  "intents": ["NS_CREATE_SIGNAL"],
  "primary_intent": "NS_CREATE_SIGNAL",
  "targeting": null,
  "signal": {
    "symbol": "SOLUSDT",
    "side": "LONG",
    "entry_structure": "ONE_SHOT",
    "entries": [
      {
        "sequence": 1,
        "entry_type": "LIMIT",
        "price": { "raw": "150", "value": 150.0 },
        "role": "PRIMARY",
        "size_hint": null,
        "note": null,
        "is_optional": false
      }
    ],
    "stop_loss": null,
    "take_profits": [],
    "leverage_hint": null,
    "risk_hint": null,
    "invalidation_rule": null,
    "conditions": null,
    "completeness": "INCOMPLETE",
    "missing_fields": ["stop_loss", "take_profits"],
    "raw_fragments": {}
  },
  "update": null,
  "report": null,
  "warnings": ["missing_stop_loss", "missing_take_profits"],
  "diagnostics": {},
  "raw_context": {
    "raw_text": "SOL long entry 150",
    "reply_to_message_id": null,
    "extracted_links": [],
    "hashtags": [],
    "source_chat_id": "-1001234567890",
    "source_topic_id": null,
    "acquisition_mode": "live"
  }
}
```

## 16.6 ERROR

```json
{
  "schema_version": "1.0",
  "parser_profile": "trader_x",
  "primary_class": "INFO",
  "parse_status": "ERROR",
  "confidence": 0.0,
  "intents": [],
  "primary_intent": null,
  "targeting": null,
  "signal": null,
  "update": null,
  "report": null,
  "warnings": ["parser_failed"],
  "diagnostics": {
    "exception": "ValueError: malformed payload"
  },
  "raw_context": {
    "raw_text": "broken input",
    "reply_to_message_id": null,
    "extracted_links": [],
    "hashtags": [],
    "source_chat_id": "-1001234567890",
    "source_topic_id": null,
    "acquisition_mode": "live"
  }
}
```

---

# 17. Sintesi finale

`CanonicalMessage` è il contratto finale:

- più pulito
- più validato
- meno dipendente dai parser trader-specifici
- adatto ai layer downstream

Differenza sintetica rispetto a `TraderEventEnvelopeV1`:

- `TraderEventEnvelopeV1` = parser-side più ricco/raw
- `CanonicalMessage` = modello finale canonico/business

