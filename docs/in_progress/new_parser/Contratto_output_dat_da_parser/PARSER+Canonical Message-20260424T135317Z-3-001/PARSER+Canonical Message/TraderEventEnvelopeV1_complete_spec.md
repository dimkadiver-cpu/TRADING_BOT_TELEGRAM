# TraderEventEnvelopeV1 — contratto previsto completo

## Stato del contratto

`TraderEventEnvelopeV1` è una **shape parser-side/intermedia prevista** dal piano di migrazione del nuovo parser.

Obiettivo del contratto:

- ricevere in input i dati provenienti dai parser legacy (`TraderParseResult`)
- unificare il parser-side in una sola shape coerente
- dare al normalizer un input stabile
- separare:
  - estrazione parser/trader-specifica
  - normalizzazione canonica downstream

Pipeline target prevista:

```text
TraderParseResult
→ TraderEventEnvelopeV1
→ CanonicalMessage
```

---

# 1. Scopo

`TraderEventEnvelopeV1` non è il modello finale business/canonico.  
È il **contratto intermedio ricco lato parser**, pensato per:

- conservare bene i dettagli estratti
- non perdere frammenti utili
- rappresentare insieme:
  - signal payload
  - update payload
  - report payload
- permettere messaggi compositi, per esempio:
  - `UPDATE + REPORT`

---

# 2. Struttura top-level prevista

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

---

# 3. Tipi top-level

| Campo | Tipo previsto | Obbl. | Note |
|---|---|---:|---|
| `schema_version` | `str` | sì | Consigliato: `"trader_event_envelope_v1"` |
| `message_type_hint` | `str \| None` | no | Hint ereditato dal legacy |
| `intents_detected` | `list[str]` | sì | Lista intenti trovati dal parser |
| `primary_intent_hint` | `str \| None` | no | Intent principale parser-side |
| `instrument` | `InstrumentRaw` | sì | Dati strumento/lato mercato |
| `signal_payload_raw` | `SignalPayloadRaw` | sì | Blocchi segnale |
| `update_payload_raw` | `UpdatePayloadRaw` | sì | Blocchi update |
| `report_payload_raw` | `ReportPayloadRaw` | sì | Blocchi report |
| `targets_raw` | `list[TargetRefRaw]` | sì | Target raw |
| `warnings` | `list[str]` | sì | Warning parser-side |
| `confidence` | `float` | sì | Confidenza globale |
| `diagnostics` | `dict[str, Any]` | sì | Diagnostica, residui legacy, note di migrazione |

---

# 4. Classificazione degli intent previsti

## 4.1 Principio generale

Nel contratto `TraderEventEnvelopeV1`, gli intenti restano **visibili al top-level** in `intents_detected`, ma la semantica operativa viene proiettata nei payload raw:

- `signal_payload_raw`
- `update_payload_raw`
- `report_payload_raw`

Quindi gli intenti non sono il modello finale: sono **marker parser-side**.

---

## 4.2 Famiglie di intent

## A. Intent di creazione segnale

| Intent | Significato | Destinazione tipica |
|---|---|---|
| `NS_CREATE_SIGNAL` | nuovo segnale | `signal_payload_raw` |   

---

## B. Intent di update operativo

| Intent | Significato | Operazione envelope tipica |
|---|---|---|
| `U_MOVE_STOP` | sposta stop | `SET_STOP` |
| `U_MOVE_STOP_TO_BE` | stop a breakeven/entry | `SET_STOP` |
| `U_CLOSE_FULL` | chiusura totale | `CLOSE` |
| `U_CLOSE_PARTIAL` | chiusura parziale | `CLOSE` |
| `U_CANCEL_PENDING_ORDERS` | cancella pending orders | `CANCEL_PENDING` |
| `U_REMOVE_PENDING_ENTRY` | rimuovi una pending entry | `CANCEL_PENDING` |
| `U_INVALIDATE_SETUP` | setup non più valido | `CANCEL_PENDING` |
| `U_REENTER` | rientro | `MODIFY_ENTRIES` |                              // `CANCEL_PENDING` se esistano + SIGNAL (riferimento di ST TP raleti a segnale vecchio)
| `U_ADD_ENTRY` | aggiunta entry | `MODIFY_ENTRIES` |
| `U_UPDATE_PENDING_ENTRY` | modifica entry pending | `MODIFY_ENTRIES` |
| `U_UPDATE_TAKE_PROFITS` | modifica TP | `MODIFY_TARGETS` |

---

## C. Intent di report / contesto / lifecycle

| Intent | Significato | Destinazione tipica |
|---|---|---|
| `U_ACTIVATION` | attivazione/setup attivo | `report_payload_raw.events` |   // DA ELIMINARE
| `U_MARK_FILLED` | entry eseguita | `report_payload_raw.events` |
| `U_TP_HIT` | take profit colpito | `report_payload_raw.events` |
| `U_STOP_HIT` | stop colpito | `report_payload_raw.events` |
| `U_SL_HIT` | alias/variante di stop hit | `report_payload_raw.events` |
| `U_EXIT_BE` | uscita a breakeven | `report_payload_raw.events` | 
| `U_REPORT_FINAL_RESULT` | risultato finale | `report_payload_raw.reported_result` e/o `events` |
| `U_MANUAL_CLOSE` | chiusura manuale | report e/o update, a seconda del parser |

| `U_REVERSE_SIGNAL` | inversione logica/segnale opposto | diagnostica o mapping dedicato | // DA ELIMINARE
| `U_RISK_NOTE` | nota di rischio | diagnostica / eventuale note payload |                   // DA ELIMINARE

---

## D. Alias legacy osservati / previsti

| Alias legacy | Intent normalizzato |
|---|---|
| `U_TP_HIT_EXPLICIT` | `U_TP_HIT` |  // da collasaare in `U_TP_HIT` 
| `U_UPDATE_STOP` | `U_MOVE_STOP` | // da collasaare in `U_MOVE_STOP` 

---

## 4.3 Esempi di classificazione

### Caso 1 — nuovo segnale puro

```json
{
  "message_type_hint": "NEW_SIGNAL",
  "intents_detected": ["NS_CREATE_SIGNAL"],
  "primary_intent_hint": "NS_CREATE_SIGNAL"
}
```

### Caso 2 — update puro

```json
{
  "message_type_hint": "UPDATE",
  "intents_detected": ["U_MOVE_STOP_TO_BE"],
  "primary_intent_hint": "U_MOVE_STOP_TO_BE"
}
```

### Caso 3 — update + report composito

```json
{
  "message_type_hint": "UPDATE",
  "intents_detected": ["U_CLOSE_PARTIAL", "U_TP_HIT"],
  "primary_intent_hint": "U_TP_HIT"
}
```

---

# 5. Sottotipi previsti

## 5.1 `InstrumentRaw`

```text
InstrumentRaw
├─ symbol: str | None
├─ side: "LONG" | "SHORT" | None
└─ market_type: str | None
```

## JSON esempio

```json
{
  "symbol": "BTCUSDT",
  "side": "SHORT",
  "market_type": "FUTURES"
}
```

---

## 5.2 `SignalPayloadRaw`

```text
SignalPayloadRaw
├─ entry_structure: "ONE_SHOT" | "TWO_STEP" | "RANGE" | "LADDER" | None
├─ entries: list[EntryLegRaw]
├─ stop_loss: StopLossRaw | None
├─ take_profits: list[TakeProfitRaw]
├─ leverage_hint: float | None
├─ risk_hint: RiskHintRaw | None
├─ invalidation_rule: str | None
├─ conditions: str | None
└─ raw_fragments: dict[str, str | None]
```

---

## 5.2.1 `EntryLegRaw`

```text
EntryLegRaw
├─ sequence: int
├─ entry_type: "MARKET" | "LIMIT" | None
├─ price: float | None
├─ role: "PRIMARY" | "AVERAGING" | "UNKNOWN" | None
├─ size_hint: str | None
├─ note: str | None
└─ is_optional: bool | None
```

### JSON esempio

```json
{
  "sequence": 1,
  "entry_type": "LIMIT",
  "price": 88650.0,
  "role": "PRIMARY",
  "size_hint": "1/3",
  "note": null,
  "is_optional": false
}
```

---

## 5.2.2 `StopLossRaw`

```text
StopLossRaw
└─ price: float | None
```

### JSON esempio

```json
{
  "price": 89450.0
}
```

---

## 5.2.3 `TakeProfitRaw`

```text
TakeProfitRaw
├─ sequence: int
├─ price: float
├─ label: str | None
└─ close_fraction: float | None
```

### JSON esempio

```json
{
  "sequence": 1,
  "price": 87500.0,
  "label": "TP1",
  "close_fraction": null
}
```

---

## 5.2.4 `RiskHintRaw`

```text
RiskHintRaw
├─ raw: str | None
├─ value: float | None
└─ unit: "PERCENT" | "ABSOLUTE" | "UNKNOWN"
```

### JSON esempio

```json
{
  "raw": "1% dep",
  "value": 1.0,
  "unit": "PERCENT"
}
```

---

# 6. Casi `SignalPayloadRaw` previsti

## 6.1 ONE_SHOT

```json
{
  "entry_structure": "ONE_SHOT",
  "entries": [
    {
      "sequence": 1,
      "entry_type": "LIMIT",
      "price": 2410.0,
      "role": "PRIMARY",
      "size_hint": null,
      "note": null,
      "is_optional": false
    }
  ],
  "stop_loss": {
    "price": 2450.0
  },
  "take_profits": [
    { "sequence": 1, "price": 2380.0, "label": "TP1", "close_fraction": null },
    { "sequence": 2, "price": 2350.0, "label": "TP2", "close_fraction": null }
  ],
  "leverage_hint": 5.0,
  "risk_hint": null,
  "invalidation_rule": null,
  "conditions": null,
  "raw_fragments": {}
}
```

## 6.2 TWO_STEP

```json
{
  "entry_structure": "TWO_STEP",
  "entries": [
    {
      "sequence": 1,
      "entry_type": "LIMIT",
      "price": 88650.0,
      "role": "PRIMARY",
      "size_hint": "1/3",
      "note": null,
      "is_optional": false
    },
    {
      "sequence": 2,
      "entry_type": "LIMIT",
      "price": 89100.0,
      "role": "AVERAGING",
      "size_hint": "2/3",
      "note": null,
      "is_optional": false
    }
  ],
  "stop_loss": { "price": 89450.0 },
  "take_profits": [
    { "sequence": 1, "price": 87500.0, "label": "TP1", "close_fraction": null },
    { "sequence": 2, "price": 86800.0, "label": "TP2", "close_fraction": null },
    { "sequence": 3, "price": 85800.0, "label": "TP3", "close_fraction": null }
  ],
  "leverage_hint": 3.0,
  "risk_hint": {
    "raw": "1% dep",
    "value": 1.0,
    "unit": "PERCENT"
  },
  "invalidation_rule": null,
  "conditions": null,
  "raw_fragments": {}
}
```

## 6.3 RANGE

```json
{
  "entry_structure": "RANGE",
  "entries": [
    {
      "sequence": 1,
      "entry_type": "LIMIT",
      "price": 2114.0,
      "role": "PRIMARY",
      "size_hint": null,
      "note": "range_low",
      "is_optional": false
    },
    {
      "sequence": 2,
      "entry_type": "LIMIT",
      "price": 2122.0,
      "role": "PRIMARY",
      "size_hint": null,
      "note": "range_high",
      "is_optional": false
    }
  ],
  "stop_loss": { "price": 2100.0 },
  "take_profits": [
    { "sequence": 1, "price": 2128.0, "label": "TP1", "close_fraction": null },
    { "sequence": 2, "price": 2141.0, "label": "TP2", "close_fraction": null },
    { "sequence": 3, "price": 2160.0, "label": "TP3", "close_fraction": null }
  ],
  "leverage_hint": null,
  "risk_hint": {
    "raw": "0.3% dep",
    "value": 0.3,
    "unit": "PERCENT"
  },
  "invalidation_rule": null,
  "conditions": null,
  "raw_fragments": {}
}
```

## 6.4 LADDER

```json
{
  "entry_structure": "LADDER",
  "entries": [
    { "sequence": 1, "entry_type": "LIMIT", "price": 100.0, "role": "PRIMARY", "size_hint": "25%", "note": null, "is_optional": false },
    { "sequence": 2, "entry_type": "LIMIT", "price": 102.0, "role": "AVERAGING", "size_hint": "25%", "note": null, "is_optional": false },
    { "sequence": 3, "entry_type": "LIMIT", "price": 104.0, "role": "AVERAGING", "size_hint": "25%", "note": null, "is_optional": false },
    { "sequence": 4, "entry_type": "LIMIT", "price": 106.0, "role": "AVERAGING", "size_hint": "25%", "note": null, "is_optional": false }
  ],
  "stop_loss": { "price": 110.0 },
  "take_profits": [
    { "sequence": 1, "price": 98.0, "label": "TP1", "close_fraction": 0.25 },
    { "sequence": 2, "price": 95.0, "label": "TP2", "close_fraction": 0.25 },
    { "sequence": 3, "price": 92.0, "label": "TP3", "close_fraction": 0.25 },
    { "sequence": 4, "price": 88.0, "label": "TP4", "close_fraction": 0.25 }
  ],
  "leverage_hint": 10.0,
  "risk_hint": null,
  "invalidation_rule": null,
  "conditions": null,
  "raw_fragments": {}
}
```

---

# 7. `UpdatePayloadRaw`

```text
UpdatePayloadRaw
└─ operations: list[UpdateOperationRaw]
```

## 7.1 `UpdateOperationRaw`

```text
UpdateOperationRaw
├─ op_type: "SET_STOP" | "CLOSE" | "CANCEL_PENDING" | "MODIFY_ENTRIES" | "MODIFY_TARGETS"
├─ set_stop: SetStopRaw | None
├─ close: CloseOperationRaw | None
├─ cancel_pending: CancelPendingRaw | None
├─ modify_entries: ModifyEntriesRaw | None
├─ modify_targets: ModifyTargetsRaw | None
└─ source_intent: str | None
```

---

## 7.1.1 `SetStopRaw`

```text
SetStopRaw
├─ target_type: "PRICE" | "ENTRY" | "TP_LEVEL"
└─ value: float | int | None
```

### Caso A — stop a prezzo

```json
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
  "source_intent": "U_MOVE_STOP"
}
```

### Caso B — stop a entry / breakeven

```json
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
```

### Caso C — stop a livello TP

```json
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
```

---

## 7.1.2 `CloseOperationRaw`

```text
CloseOperationRaw
├─ close_fraction: float | None
├─ close_price: float | None
└─ close_scope: str | None
```

### Caso A — close partial

```json
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
```

### Caso B — close full

```json
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
  "source_intent": "U_CLOSE_FULL"
}
```

---

## 7.1.3 `CancelPendingRaw`

```text
CancelPendingRaw
└─ cancel_scope: str | None
```

### Caso JSON

```json
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
```

---

## 7.1.4 `ModifyEntriesRaw`

```text
ModifyEntriesRaw
├─ mode: "ADD" | "REENTER" | "UPDATE"
└─ entries: list[EntryLegRaw]
```

### Caso A — ADD

```json
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
        "price": 2050.0,
        "role": "AVERAGING",
        "size_hint": null,
        "note": null,
        "is_optional": false
      }
    ]
  },
  "modify_targets": null,
  "source_intent": "U_ADD_ENTRY"
}
```

### Caso B — REENTER

```json
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
        "price": 2010.0,
        "role": "PRIMARY",
        "size_hint": null,
        "note": "reentry",
        "is_optional": false
      }
    ]
  },
  "modify_targets": null,
  "source_intent": "U_REENTER"
}
```

### Caso C — UPDATE

```json
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
        "price": 1995.0,
        "role": "PRIMARY",
        "size_hint": null,
        "note": "updated_pending_entry",
        "is_optional": false
      }
    ]
  },
  "modify_targets": null,
  "source_intent": "U_UPDATE_PENDING_ENTRY"
}
```

---

## 7.1.5 `ModifyTargetsRaw`

```text
ModifyTargetsRaw
├─ mode: "REPLACE_ALL" | "ADD" | "UPDATE_ONE" | "REMOVE_ONE"
├─ take_profits: list[TakeProfitRaw]
└─ target_tp_level: int | None
```

### Caso A — REPLACE_ALL

```json
{
  "op_type": "MODIFY_TARGETS",
  "set_stop": null,
  "close": null,
  "cancel_pending": null,
  "modify_entries": null,
  "modify_targets": {
    "mode": "REPLACE_ALL",
    "take_profits": [
      { "sequence": 1, "price": 2128.0, "label": "TP1", "close_fraction": null },
      { "sequence": 2, "price": 2141.0, "label": "TP2", "close_fraction": null }
    ],
    "target_tp_level": null
  },
  "source_intent": "U_UPDATE_TAKE_PROFITS"
}
```

### Caso B — ADD

```json
{
  "op_type": "MODIFY_TARGETS",
  "set_stop": null,
  "close": null,
  "cancel_pending": null,
  "modify_entries": null,
  "modify_targets": {
    "mode": "ADD",
    "take_profits": [
      { "sequence": 4, "price": 2200.0, "label": "TP4", "close_fraction": null }
    ],
    "target_tp_level": null
  },
  "source_intent": "U_UPDATE_TAKE_PROFITS"
}
```

### Caso C — UPDATE_ONE

```json
{
  "op_type": "MODIFY_TARGETS",
  "set_stop": null,
  "close": null,
  "cancel_pending": null,
  "modify_entries": null,
  "modify_targets": {
    "mode": "UPDATE_ONE",
    "take_profits": [
      { "sequence": 2, "price": 2145.0, "label": "TP2", "close_fraction": null }
    ],
    "target_tp_level": 2
  },
  "source_intent": "U_UPDATE_TAKE_PROFITS"
}
```

### Caso D — REMOVE_ONE

```json
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
  "source_intent": "U_UPDATE_TAKE_PROFITS"
}
```

---

# 8. `ReportPayloadRaw`

```text
ReportPayloadRaw
├─ events: list[ReportEventRaw]
├─ reported_result: ReportedResultRaw | None
└─ notes: list[str]
```

## 8.1 `ReportEventRaw`

```text
ReportEventRaw
├─ event_type: "ENTRY_FILLED" | "TP_HIT" | "STOP_HIT" | "BREAKEVEN_EXIT" | "FINAL_RESULT"
├─ level: int | None
├─ price: float | None
├─ result: ReportedResultRaw | None
└─ raw_fragment: str | None
```

## 8.2 `ReportedResultRaw`

```text
ReportedResultRaw
├─ value: float | None
├─ unit: "R" | "PERCENT" | "TEXT" | "UNKNOWN"
└─ text: str | None
```

---

# 9. Casi `ReportPayloadRaw` previsti

## 9.1 ENTRY_FILLED

```json
{
  "events": [
    {
      "event_type": "ENTRY_FILLED",
      "level": null,
      "price": 2114.0,
      "result": null,
      "raw_fragment": "entry filled 2114"
    }
  ],
  "reported_result": null,
  "notes": []
}
```

## 9.2 TP_HIT

```json
{
  "events": [
    {
      "event_type": "TP_HIT",
      "level": 1,
      "price": 2128.0,
      "result": {
        "value": 1.0,
        "unit": "R",
        "text": "+1R"
      },
      "raw_fragment": "tp1 hit"
    }
  ],
  "reported_result": null,
  "notes": []
}
```

## 9.3 STOP_HIT

```json
{
  "events": [
    {
      "event_type": "STOP_HIT",
      "level": null,
      "price": 2100.0,
      "result": {
        "value": -1.0,
        "unit": "R",
        "text": "-1R"
      },
      "raw_fragment": "stop hit"
    }
  ],
  "reported_result": null,
  "notes": []
}
```

## 9.4 BREAKEVEN_EXIT

```json
{
  "events": [
    {
      "event_type": "BREAKEVEN_EXIT",
      "level": null,
      "price": 2114.0,
      "result": {
        "value": 0.0,
        "unit": "R",
        "text": "BE"
      },
      "raw_fragment": "breakeven exit"
    }
  ],
  "reported_result": null,
  "notes": []
}
```

## 9.5 FINAL_RESULT

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
      "raw_fragment": "final result +3.2R"
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

# 10. `TargetRefRaw`

```text
TargetRefRaw
├─ kind: "REPLY" | "TELEGRAM_LINK" | "MESSAGE_ID" | "EXPLICIT_ID" | "SYMBOL" | "UNKNOWN"
└─ value: str | int | None
```

## Casi previsti

### REPLY

```json
{
  "kind": "REPLY",
  "value": 1701
}
```

### TELEGRAM_LINK

```json
{
  "kind": "TELEGRAM_LINK",
  "value": "https://t.me/c/12345/1701"
}
```

### MESSAGE_ID

```json
{
  "kind": "MESSAGE_ID",
  "value": 1701
}
```

### EXPLICIT_ID

```json
{
  "kind": "EXPLICIT_ID",
  "value": "2110"
}
```

### SYMBOL

```json
{
  "kind": "SYMBOL",
  "value": "BTCUSDT"
}
```

### UNKNOWN

```json
{
  "kind": "UNKNOWN",
  "value": null
}
```

---

# 11. Esempi completi di `TraderEventEnvelopeV1`

## 11.1 Caso SIGNAL puro

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
      { "sequence": 1, "entry_type": "LIMIT", "price": 88650.0, "role": "PRIMARY", "size_hint": "1/3", "note": null, "is_optional": false },
      { "sequence": 2, "entry_type": "LIMIT", "price": 89100.0, "role": "AVERAGING", "size_hint": "2/3", "note": null, "is_optional": false }
    ],
    "stop_loss": { "price": 89450.0 },
    "take_profits": [
      { "sequence": 1, "price": 87500.0, "label": "TP1", "close_fraction": null },
      { "sequence": 2, "price": 86800.0, "label": "TP2", "close_fraction": null }
    ],
    "leverage_hint": 3.0,
    "risk_hint": { "raw": "1% dep", "value": 1.0, "unit": "PERCENT" },
    "invalidation_rule": null,
    "conditions": null,
    "raw_fragments": {}
  },
  "update_payload_raw": {},
  "report_payload_raw": {},
  "targets_raw": [],
  "warnings": [],
  "confidence": 0.96,
  "diagnostics": {}
}
```

## 11.2 Caso UPDATE puro

```json
{
  "schema_version": "trader_event_envelope_v1",
  "message_type_hint": "UPDATE",
  "intents_detected": ["U_MOVE_STOP_TO_BE"],
  "primary_intent_hint": "U_MOVE_STOP_TO_BE",
  "instrument": {
    "symbol": "BTCUSDT",
    "side": "SHORT",
    "market_type": null
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
        "close": null,
        "cancel_pending": null,
        "modify_entries": null,
        "modify_targets": null,
        "source_intent": "U_MOVE_STOP_TO_BE"
      }
    ]
  },
  "report_payload_raw": {},
  "targets_raw": [
    {
      "kind": "REPLY",
      "value": 1701
    }
  ],
  "warnings": [],
  "confidence": 0.93,
  "diagnostics": {}
}
```

## 11.3 Caso REPORT puro

```json
{
  "schema_version": "trader_event_envelope_v1",
  "message_type_hint": "REPORT",
  "intents_detected": ["U_REPORT_FINAL_RESULT"],
  "primary_intent_hint": "U_REPORT_FINAL_RESULT",
  "instrument": {
    "symbol": "ETHUSDT",
    "side": "LONG",
    "market_type": "FUTURES"
  },
  "signal_payload_raw": {},
  "update_payload_raw": {},
  "report_payload_raw": {
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
        "raw_fragment": "final result +3.2R"
      }
    ],
    "reported_result": {
      "value": 3.2,
      "unit": "R",
      "text": "+3.2R final"
    },
    "notes": []
  },
  "targets_raw": [],
  "warnings": [],
  "confidence": 0.95,
  "diagnostics": {}
}
```

## 11.4 Caso composito UPDATE + REPORT

```json
{
  "schema_version": "trader_event_envelope_v1",
  "message_type_hint": "UPDATE",
  "intents_detected": ["U_CLOSE_PARTIAL", "U_TP_HIT"],
  "primary_intent_hint": "U_TP_HIT",
  "instrument": {
    "symbol": "ETHUSDT",
    "side": "LONG",
    "market_type": "FUTURES"
  },
  "signal_payload_raw": {},
  "update_payload_raw": {
    "operations": [
      {
        "op_type": "CLOSE",
        "set_stop": null,
        "close": {
          "close_fraction": 0.5,
          "close_price": 2128.0,
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
        "price": 2128.0,
        "result": {
          "value": 1.0,
          "unit": "R",
          "text": "+1R"
        },
        "raw_fragment": "tp1 hit"
      }
    ],
    "reported_result": {
      "value": 1.0,
      "unit": "R",
      "text": "+1R"
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

## 11.5 Caso INFO / note-only

```json
{
  "schema_version": "trader_event_envelope_v1",
  "message_type_hint": "INFO_ONLY",
  "intents_detected": ["U_RISK_NOTE"],
  "primary_intent_hint": "U_RISK_NOTE",
  "instrument": {
    "symbol": "BTCUSDT",
    "side": null,
    "market_type": null
  },
  "signal_payload_raw": {},
  "update_payload_raw": {},
  "report_payload_raw": {
    "events": [],
    "reported_result": null,
    "notes": [
      "risk reduced due to volatility"
    ]
  },
  "targets_raw": [],
  "warnings": [],
  "confidence": 0.72,
  "diagnostics": {
    "note_type": "risk_note"
  }
}
```

---

# 12. Regole operative consigliate

## 12.1 Blocchi vuoti
Se un blocco non è pertinente, può essere lasciato come oggetto vuoto `{}` oppure struttura vuota equivalente, ma senza inventare campi.

## 12.2 Nessuna riclassificazione forte nell’adapter
L’adapter verso `TraderEventEnvelopeV1` dovrebbe:
- preservare `message_type_hint`
- preservare gli intenti rilevati
- mappare nei blocchi raw senza già decidere tutta la semantica finale

## 12.3 Compositi ammessi
A livello envelope sono ammessi casi compositi:
- `UPDATE + REPORT`
- opzionalmente `INFO + REPORT`
- il caso `SIGNAL + UPDATE` dovrebbe restare eccezionale e in generale non consigliato

---

# 13. Sintesi finale

`TraderEventEnvelopeV1` è il contratto previsto per diventare:

- l’uscita parser-side uniforme
- più ricco e vicino all’estrazione
- ancora non canonico finale
- ponte stabile verso `CanonicalMessage`



# 14. Regole :
| `U_REENTER` | rientro | `MODIFY_ENTRIES` |   // `CANCEL_PENDING` se esistano + SIGNAL (riferimento di ST TP raleti a segnale vecchio)