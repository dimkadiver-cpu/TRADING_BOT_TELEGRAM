# TraderEventEnvelopeV1 - specifica corrente

## Stato del contratto

`TraderEventEnvelopeV1` e il contratto parser-side corrente da usare come output unico dei parser trader-specifici.

Pipeline target:

```text
text + ParserContext
  -> profile parser trader-specifico
  -> TraderEventEnvelopeV1
  -> normalizer centrale
  -> CanonicalMessageV1
```

Regola chiave:

- `TraderEventEnvelopeV1` descrive cosa il parser ha capito
- non descrive ancora cosa il sistema dovra eseguire
- la trasformazione `intent -> operazioni/canonical semantics` appartiene ai layer successivi

---

## 1. Scopo

`TraderEventEnvelopeV1` serve a:

- unificare tutti i parser in una sola shape
- conservare la semantica parser-side utile
- supportare messaggi compositi senza forzare una riduzione prematura
- dare al normalizer un input stabile e prevedibile

Non serve a:

- rappresentare il modello business finale
- esprimere comandi di esecuzione
- collassare tutta la semantica in una sola action

---

## 2. Struttura top-level

```text
TraderEventEnvelopeV1
|- schema_version: str
|- message_type_hint: str | None
|- intents_detected: list[str]
|- primary_intent_hint: str | None
|- instrument: InstrumentRaw
|- signal_payload_raw: SignalPayloadRaw
|- update_payload_raw: UpdatePayloadRaw
|- report_payload_raw: ReportPayloadRaw
|- targets_raw: list[TargetRefRaw]
|- warnings: list[str]
|- confidence: float
`- diagnostics: dict[str, Any]
```

### Regole top-level

- tutti i blocchi top-level esistono sempre
- quando un blocco non e pertinente, resta vuoto (`null`, `[]`, `{}`)
- nessun parser aggiunge campi top-level fuori contratto
- `message_type_hint` e un hint parser-side, non una decisione business finale
- `primary_intent_hint` e un hint parser-side, non un comando di esecuzione

---

## 3. Campi top-level

| Campo | Tipo | Obbl. | Note |
|---|---|---:|---|
| `schema_version` | `str` | si | Consigliato: `"trader_event_envelope_v1"` |
| `message_type_hint` | `str \| None` | no | Valori attesi: `NEW_SIGNAL`, `UPDATE`, `REPORT`, `INFO_ONLY`, `UNCLASSIFIED` |
| `intents_detected` | `list[str]` | si | Lista completa degli intent compatibili rilevati |
| `primary_intent_hint` | `str \| None` | no | Hint di priorita semantica locale |
| `instrument` | `InstrumentRaw` | si | Dati strumento/lato/mercato |
| `signal_payload_raw` | `SignalPayloadRaw` | si | Dati raw di nuovo setup |
| `update_payload_raw` | `UpdatePayloadRaw` | si | Dati raw di update |
| `report_payload_raw` | `ReportPayloadRaw` | si | Dati raw di report/lifecycle |
| `targets_raw` | `list[TargetRefRaw]` | si | Target raw e riferimenti al messaggio/segnale |
| `warnings` | `list[str]` | si | Warning parser-side |
| `confidence` | `float` | si | Confidenza parser-side globale |
| `diagnostics` | `dict[str, Any]` | si | Diagnostica, residui legacy, note di migrazione |

---

## 4. Message type

Valori attesi di `message_type_hint`:

- `NEW_SIGNAL`
- `UPDATE`
- `REPORT`
- `INFO_ONLY`
- `UNCLASSIFIED`

### Regole

- `NEW_SIGNAL` indica un messaggio di apertura setup
- `UPDATE` indica un messaggio che modifica, chiude, invalida o aggiorna un setup/posizione esistente
- `REPORT` indica un messaggio che riporta esiti, hit, risultati o lifecycle events
- `INFO_ONLY` indica un messaggio informativo senza semantica operativa diretta
- `UNCLASSIFIED` resta ammesso come fallback parser-side

---

## 5. Tassonomia intent corrente

Set canonico parser-side:

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

### Regole

- i parser possono emettere solo intent appartenenti a questa tassonomia
- alias legacy tipo `NS_CREATE_SIGNAL`, `U_MOVE_STOP`, `U_MARK_FILLED`, `U_REPORT_FINAL_RESULT` non fanno parte del contratto corrente
- `NEW_SETUP` e implicito quando `message_type_hint = NEW_SIGNAL`
- `INFO_ONLY` e implicito quando `message_type_hint = INFO_ONLY`
- `primary_intent_hint` puo essere assente se il parser non ha un hint affidabile

### Copertura esempi nella spec

| Intent | Sezione esempio principale |
|---|---|
| `NEW_SETUP` | `8.1` / `12.1` |
| `MOVE_STOP_TO_BE` | `9.1` / `12.2` |
| `MOVE_STOP` | `9.2` / `12.4` |
| `CLOSE_FULL` | `9.3` / `12.5` |
| `CLOSE_PARTIAL` | `9.4` / `12.4` / `12.7` |
| `CANCEL_PENDING_ORDERS` | `9.5` / `12.7` |
| `INVALIDATE_SETUP` | `9.5` / `12.7` |
| `REENTER` | `9.6` / `12.8` |
| `ADD_ENTRY` | `9.6` / `12.9` |
| `UPDATE_TAKE_PROFITS` | `9.7` / `12.10` |
| `ENTRY_FILLED` | `10.1` / `12.11` |
| `TP_HIT` | `10.2` / `12.12` |
| `SL_HIT` | `10.3` / `12.13` |
| `EXIT_BE` | `10.4` / `12.14` |
| `REPORT_PARTIAL_RESULT` | `10.5` / `12.15` |
| `REPORT_FINAL_RESULT` | `10.6` / `12.3` / `12.16` |
| `INFO_ONLY` | `12.17` |

---

## 6. Policy multi-intent

Un singolo messaggio puo contenere piu intent compatibili.

### Regole

- `intents_detected` puo contenere piu intent
- il parser non deve ridurre artificialmente a un solo intent se il testo contiene piu semantiche compatibili
- `primary_intent_hint` serve solo a esprimere una priorita semantica parser-side
- `primary_intent_hint` non deve essere interpretato come action command
- la risoluzione finale `intent -> CanonicalMessageV1` appartiene al normalizer

### Esempi

#### Nuovo segnale puro

```json
{
  "message_type_hint": "NEW_SIGNAL",
  "intents_detected": ["NEW_SETUP"],
  "primary_intent_hint": "NEW_SETUP"
}
```

#### Update puro

```json
{
  "message_type_hint": "UPDATE",
  "intents_detected": ["MOVE_STOP_TO_BE"],
  "primary_intent_hint": "MOVE_STOP_TO_BE"
}
```

#### Update multi-intent

```json
{
  "message_type_hint": "UPDATE",
  "intents_detected": ["CLOSE_PARTIAL", "MOVE_STOP"],
  "primary_intent_hint": "CLOSE_PARTIAL"
}
```

#### Caso misto update/report

```json
{
  "message_type_hint": "UPDATE",
  "intents_detected": ["CLOSE_PARTIAL", "TP_HIT"],
  "primary_intent_hint": "CLOSE_PARTIAL"
}
```

---

## 7. InstrumentRaw

```text
InstrumentRaw
|- symbol: str | None
|- side: "LONG" | "SHORT" | None
`- market_type: str | None
```

### JSON esempio

```json
{
  "symbol": "BTCUSDT",
  "side": "SHORT",
  "market_type": "FUTURES"
}
```

---

## 8. SignalPayloadRaw

Il payload `signal_payload_raw` contiene solo dati raw di setup.

### Shape consigliata

```text
SignalPayloadRaw
|- entry_structure: "ONE_SHOT" | "TWO_STEP" | "RANGE" | "LADDER" | "UNKNOWN" | None
|- entries: list[EntryLegRaw]
|- stop_loss: StopLossRaw | None
|- take_profits: list[TakeProfitRaw]
|- leverage_hint: float | None
|- risk_hint: RiskHintRaw | None
|- invalidation_rule: str | None
|- conditions: str | None
`- raw_fragments: SignalRawFragments
```

### EntryLegRaw

```text
EntryLegRaw
|- sequence: int
|- entry_type: "MARKET" | "LIMIT" | "UNKNOWN" | None
|- price: float | None
|- role: "PRIMARY" | "AVERAGING" | "UNKNOWN" | None
|- size_hint: SizeHintRaw | None
|- note: str | None
`- is_optional: bool | None
```

### SizeHintRaw

```text
SizeHintRaw
|- value: float | None
|- unit: "PERCENT" | "FRACTION" | "TEXT" | "UNKNOWN"
`- raw: str | None
```

### StopLossRaw

```text
StopLossRaw
|- price: float | None
`- raw: str | None
```

### TakeProfitRaw

```text
TakeProfitRaw
|- sequence: int
|- price: float | None
|- label: str | None
|- close_fraction: float | None
`- raw: str | None
```

### RiskHintRaw

```text
RiskHintRaw
|- raw: str | None
|- value: float | None
`- unit: "PERCENT" | "ABSOLUTE" | "UNKNOWN"
```

### SignalRawFragments

```text
SignalRawFragments
|- entry_text_raw: str | None
|- stop_text_raw: str | None
`- take_profits_text_raw: str | None
```

### Regole

- il contratto e parser-side: i campi possono essere incompleti
- `entries`, `stop_loss` e `take_profits` usano shape esplicite e stabili
- se un trader ha frammenti extra non allineati al vocabolario comune, vanno in `diagnostics`

### JSON esempi

#### 8.1 `ONE_SHOT`

```json
{
  "entry_structure": "ONE_SHOT",
  "entries": [
    {
      "sequence": 1,
      "entry_type": "MARKET",
      "price": null,
      "role": "PRIMARY",
      "size_hint": null,
      "note": "enter now",
      "is_optional": false
    }
  ],
  "stop_loss": {
    "price": 62450.0,
    "raw": "SL 62450"
  },
  "take_profits": [
    {
      "sequence": 1,
      "price": 63700.0,
      "label": "TP1",
      "close_fraction": null,
      "raw": "TP1 63700"
    }
  ],
  "leverage_hint": 5.0,
  "risk_hint": null,
  "invalidation_rule": "cancel if candle closes below 62450",
  "conditions": null,
  "raw_fragments": {
    "entry_text_raw": "market now",
    "stop_text_raw": "SL 62450",
    "take_profits_text_raw": "TP1 63700"
  }
}
```

#### 8.2 `TWO_STEP`

```json
{
  "entry_structure": "TWO_STEP",
  "entries": [
    {
      "sequence": 1,
      "entry_type": "LIMIT",
      "price": 88650.0,
      "role": "PRIMARY",
      "size_hint": {
        "value": 0.33,
        "unit": "FRACTION",
        "raw": "1/3"
      },
      "note": null,
      "is_optional": false
    },
    {
      "sequence": 2,
      "entry_type": "LIMIT",
      "price": 89100.0,
      "role": "AVERAGING",
      "size_hint": {
        "value": 0.67,
        "unit": "FRACTION",
        "raw": "2/3"
      },
      "note": null,
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
      "close_fraction": null,
      "raw": "TP1 87500"
    },
    {
      "sequence": 2,
      "price": 86800.0,
      "label": "TP2",
      "close_fraction": null,
      "raw": "TP2 86800"
    },
    {
      "sequence": 3,
      "price": 85800.0,
      "label": "TP3",
      "close_fraction": null,
      "raw": "TP3 85800"
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
  "raw_fragments": {
    "entry_text_raw": "Entry A 88650 / Entry B 89100",
    "stop_text_raw": "SL 89450",
    "take_profits_text_raw": "TP1 87500 TP2 86800 TP3 85800"
  }
}
```

#### 8.3 `RANGE`

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
  "stop_loss": {
    "price": 2100.0,
    "raw": "SL 2100"
  },
  "take_profits": [
    {
      "sequence": 1,
      "price": 2128.0,
      "label": "TP1",
      "close_fraction": null,
      "raw": "TP1 2128"
    },
    {
      "sequence": 2,
      "price": 2141.0,
      "label": "TP2",
      "close_fraction": null,
      "raw": "TP2 2141"
    },
    {
      "sequence": 3,
      "price": 2160.0,
      "label": "TP3",
      "close_fraction": null,
      "raw": "TP3 2160"
    }
  ],
  "leverage_hint": null,
  "risk_hint": {
    "raw": "0.3% dep",
    "value": 0.3,
    "unit": "PERCENT"
  },
  "invalidation_rule": null,
  "conditions": "only if range holds for 15m close",
  "raw_fragments": {
    "entry_text_raw": "buy zone 2114-2122",
    "stop_text_raw": "SL 2100",
    "take_profits_text_raw": "TP1 2128 TP2 2141 TP3 2160"
  }
}
```

#### 8.4 `LADDER`

```json
{
  "entry_structure": "LADDER",
  "entries": [
    {
      "sequence": 1,
      "entry_type": "LIMIT",
      "price": 100.0,
      "role": "PRIMARY",
      "size_hint": {
        "value": 25.0,
        "unit": "PERCENT",
        "raw": "25%"
      },
      "note": null,
      "is_optional": false
    },
    {
      "sequence": 2,
      "entry_type": "LIMIT",
      "price": 102.0,
      "role": "AVERAGING",
      "size_hint": {
        "value": 25.0,
        "unit": "PERCENT",
        "raw": "25%"
      },
      "note": null,
      "is_optional": false
    },
    {
      "sequence": 3,
      "entry_type": "LIMIT",
      "price": 104.0,
      "role": "AVERAGING",
      "size_hint": {
        "value": 25.0,
        "unit": "PERCENT",
        "raw": "25%"
      },
      "note": null,
      "is_optional": false
    },
    {
      "sequence": 4,
      "entry_type": "LIMIT",
      "price": 106.0,
      "role": "AVERAGING",
      "size_hint": {
        "value": 25.0,
        "unit": "PERCENT",
        "raw": "25%"
      },
      "note": null,
      "is_optional": false
    }
  ],
  "stop_loss": {
    "price": 110.0,
    "raw": "SL 110"
  },
  "take_profits": [
    {
      "sequence": 1,
      "price": 98.0,
      "label": "TP1",
      "close_fraction": 0.25,
      "raw": "TP1 98"
    },
    {
      "sequence": 2,
      "price": 95.0,
      "label": "TP2",
      "close_fraction": 0.25,
      "raw": "TP2 95"
    },
    {
      "sequence": 3,
      "price": 92.0,
      "label": "TP3",
      "close_fraction": 0.25,
      "raw": "TP3 92"
    },
    {
      "sequence": 4,
      "price": 88.0,
      "label": "TP4",
      "close_fraction": 0.25,
      "raw": "TP4 88"
    }
  ],
  "leverage_hint": 10.0,
  "risk_hint": null,
  "invalidation_rule": null,
  "conditions": null,
  "raw_fragments": {
    "entry_text_raw": "ladder 100 / 102 / 104 / 106",
    "stop_text_raw": "SL 110",
    "take_profits_text_raw": "TP1 98 TP2 95 TP3 92 TP4 88"
  }
}
```
---

## 9. UpdatePayloadRaw

Il payload `update_payload_raw` contiene solo dati raw di update.

### Shape consigliata

```text
UpdatePayloadRaw
|- stop_update: StopUpdateRaw | None
|- close_update: CloseUpdateRaw | None
|- cancel_update: CancelUpdateRaw | None
|- entry_update: EntryUpdateRaw | None
|- targets_update: TargetsUpdateRaw | None
`- raw_fragments: UpdateRawFragments
```

### StopUpdateRaw

```text
StopUpdateRaw
|- mode: "TO_PRICE" | "TO_ENTRY" | "TO_TP_LEVEL" | "UNKNOWN" | None
|- price: float | None
|- reference_level: int | None
`- raw: str | None
```

### CloseUpdateRaw

```text
CloseUpdateRaw
|- close_fraction: float | None
|- close_percent: float | None
|- close_price: float | None
|- close_scope: "PARTIAL" | "FULL" | "UNKNOWN" | None
`- raw: str | None
```

### CancelUpdateRaw

```text
CancelUpdateRaw
|- cancel_scope: "ALL_POSITIONS" | "ALL_OPEN" | "ALL_REMAINING" | "ALL_LONGS" | "ALL_SHORTS" | "UNKNOWN" | None
`- raw: str | None
```

### EntryUpdateRaw

```text
EntryUpdateRaw
|- mode: "REENTER" | "ADD_ENTRY" | "UPDATE_ENTRY" | "UNKNOWN" | None
|- entries: list[EntryLegRaw]
`- raw: str | None
```

### TargetsUpdateRaw

```text
TargetsUpdateRaw
|- mode: "REPLACE_ALL" | "ADD" | "UPDATE_ONE" | "REMOVE_ONE" | "UNKNOWN" | None
|- target_level: int | None
|- take_profits: list[TakeProfitRaw]
`- raw: str | None
```

### UpdateRawFragments

```text
UpdateRawFragments
|- stop_text_raw: str | None
|- close_text_raw: str | None
|- cancel_text_raw: str | None
|- entry_text_raw: str | None
`- targets_text_raw: str | None
```

### Regole

- `update_payload_raw` non contiene una lista di operations
- il parser non deve produrre `op_type`, `SET_STOP`, `CLOSE`, `CANCEL_PENDING`, ecc.
- le shape interne sono strutturate ma restano parser-side, non execution-side
- se il messaggio contiene piu intent di update compatibili, il payload puo valorizzare piu sottoblocchi contemporaneamente

### JSON esempi

#### 9.1 `MOVE_STOP_TO_BE`

```json
{
  "stop_update": {
    "mode": "TO_ENTRY",
    "price": null,
    "reference_level": null,
    "raw": "move stop to breakeven"
  },
  "close_update": null,
  "cancel_update": null,
  "entry_update": null,
  "targets_update": null,
  "raw_fragments": {
    "stop_text_raw": "move stop to breakeven",
    "close_text_raw": null,
    "cancel_text_raw": null,
    "entry_text_raw": null,
    "targets_text_raw": null
  }
}
```

#### 9.2 `MOVE_STOP`

```json
{
  "stop_update": {
    "mode": "TO_PRICE",
    "price": 62580.0,
    "reference_level": null,
    "raw": "move stop to 62580"
  },
  "close_update": null,
  "cancel_update": null,
  "entry_update": null,
  "targets_update": null,
  "raw_fragments": {
    "stop_text_raw": "move stop to 62580",
    "close_text_raw": null,
    "cancel_text_raw": null,
    "entry_text_raw": null,
    "targets_text_raw": null
  }
}
```

#### 9.3 `CLOSE_FULL`

```json
{
  "stop_update": null,
  "close_update": {
    "close_fraction": 1.0,
    "close_percent": 100.0,
    "close_price": 62420.0,
    "close_scope": "FULL",
    "raw": "close full at 62420"
  },
  "cancel_update": null,
  "entry_update": null,
  "targets_update": null,
  "raw_fragments": {
    "stop_text_raw": null,
    "close_text_raw": "close full at 62420",
    "cancel_text_raw": null,
    "entry_text_raw": null,
    "targets_text_raw": null
  }
}
```

#### 9.4 `CLOSE_PARTIAL`

```json
{
  "stop_update": null,
  "close_update": {
    "close_fraction": 0.5,
    "close_percent": 50.0,
    "close_price": 2128.0,
    "close_scope": "PARTIAL",
    "raw": "close 50% at 2128"
  },
  "cancel_update": null,
  "entry_update": null,
  "targets_update": null,
  "raw_fragments": {
    "stop_text_raw": null,
    "close_text_raw": "close 50% at 2128",
    "cancel_text_raw": null,
    "entry_text_raw": null,
    "targets_text_raw": null
  }
}
```

#### 9.5 `CANCEL_PENDING_ORDERS` / `INVALIDATE_SETUP`

```json
{
  "stop_update": null,
  "close_update": null,
  "cancel_update": {
    "cancel_scope": "ALL_OPEN",
    "raw": "cancel all open orders"
  },
  "entry_update": null,
  "targets_update": null,
  "raw_fragments": {
    "stop_text_raw": null,
    "close_text_raw": null,
    "cancel_text_raw": "cancel all open orders",
    "entry_text_raw": null,
    "targets_text_raw": null
  }
}
```

#### 9.6 `REENTER` / `ADD_ENTRY`

```json
{
  "stop_update": null,
  "close_update": null,
  "cancel_update": null,
  "entry_update": {
    "mode": "ADD_ENTRY",
    "entries": [
      {
        "sequence": 1,
        "entry_type": "LIMIT",
        "price": 61980.0,
        "role": "AVERAGING",
        "size_hint": {
          "value": 25.0,
          "unit": "PERCENT",
          "raw": "25%"
        },
        "note": "extra fill zone",
        "is_optional": false
      }
    ],
    "raw": "add entry 61980"
  },
  "targets_update": null,
  "raw_fragments": {
    "stop_text_raw": null,
    "close_text_raw": null,
    "cancel_text_raw": null,
    "entry_text_raw": "add entry 61980",
    "targets_text_raw": null
  }
}
```

#### 9.7 `UPDATE_TAKE_PROFITS`

```json
{
  "stop_update": null,
  "close_update": null,
  "cancel_update": null,
  "entry_update": null,
  "targets_update": {
    "mode": "REPLACE_ALL",
    "target_level": null,
    "take_profits": [
      {
        "sequence": 1,
        "price": 63120.0,
        "label": "TP1",
        "close_fraction": null,
        "raw": "TP1 63120"
      },
      {
        "sequence": 2,
        "price": 63500.0,
        "label": "TP2",
        "close_fraction": null,
        "raw": "TP2 63500"
      }
    ],
    "raw": "new targets 63120 / 63500"
  },
  "raw_fragments": {
    "stop_text_raw": null,
    "close_text_raw": null,
    "cancel_text_raw": null,
    "entry_text_raw": null,
    "targets_text_raw": "new targets 63120 / 63500"
  }
}
```

#### 9.8 Update composito

```json
{
  "stop_update": {
    "mode": "TO_ENTRY",
    "price": null,
    "reference_level": null,
    "raw": "move stop to breakeven"
  },
  "close_update": {
    "close_fraction": 0.5,
    "close_percent": 50.0,
    "close_price": null,
    "close_scope": "PARTIAL",
    "raw": "close 50%"
  },
  "cancel_update": null,
  "entry_update": null,
  "targets_update": null,
  "raw_fragments": {
    "stop_text_raw": "move stop to breakeven",
    "close_text_raw": "close 50%",
    "cancel_text_raw": null,
    "entry_text_raw": null,
    "targets_text_raw": null
  }
}
```

---

## 10. ReportPayloadRaw

Il payload `report_payload_raw` contiene dati raw di report/lifecycle.

### Shape consigliata

```text
ReportPayloadRaw
|- events: list[ReportEventRaw]
|- reported_results: list[ReportedResultRaw]
|- notes: list[str]
`- summary_text_raw: str | None
```

### ReportEventRaw

```text
ReportEventRaw
|- event_type: "ENTRY_FILLED" | "TP_HIT" | "SL_HIT" | "EXIT_BE" | "FINAL_RESULT" | "PARTIAL_RESULT" | "UNKNOWN"
|- level: int | None
|- price: float | None
|- result: ReportedResultRaw | None
`- raw_fragment: str | None
```

### ReportedResultRaw

```text
ReportedResultRaw
|- value: float | None
|- unit: "R" | "PERCENT" | "TEXT" | "UNKNOWN"
`- text: str | None
```

### JSON esempi

#### 10.1 `ENTRY_FILLED`

```json
{
  "events": [
    {
      "event_type": "ENTRY_FILLED",
      "level": 1,
      "price": 62510.0,
      "result": null,
      "raw_fragment": "entry filled 62510"
    }
  ],
  "reported_results": [],
  "notes": [],
  "summary_text_raw": "entry filled 62510"
}
```

#### 10.2 `TP_HIT`

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
  "reported_results": [
    {
      "value": 1.0,
      "unit": "R",
      "text": "+1R"
    }
  ],
  "notes": [],
  "summary_text_raw": "tp1 hit +1R"
}
```

#### 10.3 `SL_HIT`

```json
{
  "events": [
    {
      "event_type": "SL_HIT",
      "level": null,
      "price": 62450.0,
      "result": {
        "value": -1.0,
        "unit": "R",
        "text": "-1R"
      },
      "raw_fragment": "stopped out"
    }
  ],
  "reported_results": [
    {
      "value": -1.0,
      "unit": "R",
      "text": "-1R"
    }
  ],
  "notes": [],
  "summary_text_raw": "stopped out -1R"
}
```

#### 10.4 `EXIT_BE`

```json
{
  "events": [
    {
      "event_type": "EXIT_BE",
      "level": null,
      "price": 62510.0,
      "result": {
        "value": 0.0,
        "unit": "R",
        "text": "BE"
      },
      "raw_fragment": "closed at breakeven"
    }
  ],
  "reported_results": [
    {
      "value": 0.0,
      "unit": "R",
      "text": "BE"
    }
  ],
  "notes": [],
  "summary_text_raw": "closed at breakeven"
}
```

#### 10.5 `REPORT_PARTIAL_RESULT`

```json
{
  "events": [
    {
      "event_type": "PARTIAL_RESULT",
      "level": 1,
      "price": null,
      "result": {
        "value": 2.4,
        "unit": "PERCENT",
        "text": "+2.4%"
      },
      "raw_fragment": "partial result +2.4%"
    }
  ],
  "reported_results": [
    {
      "value": 2.4,
      "unit": "PERCENT",
      "text": "+2.4%"
    }
  ],
  "notes": ["partial summary only"],
  "summary_text_raw": "partial result +2.4%"
}
```

#### 10.6 `REPORT_FINAL_RESULT`

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
  "reported_results": [
    {
      "value": 3.2,
      "unit": "R",
      "text": "+3.2R final"
    }
  ],
  "notes": [],
  "summary_text_raw": "final result +3.2R"
}
```

---

## 11. TargetRefRaw

```text
TargetRefRaw
|- kind: "REPLY" | "TELEGRAM_LINK" | "MESSAGE_ID" | "EXPLICIT_ID" | "SYMBOL" | "UNKNOWN"
`- value: str | int | None
```

### JSON esempi

```json
{
  "kind": "REPLY",
  "value": 1701
}
```

```json
{
  "kind": "TELEGRAM_LINK",
  "value": "https://t.me/c/12345/1701"
}
```

```json
{
  "kind": "EXPLICIT_ID",
  "value": "2110"
}
```

```json
{
  "kind": "MESSAGE_ID",
  "value": 2110
}
```

```json
{
  "kind": "SYMBOL",
  "value": "BTCUSDT"
}
```

---

## 12. Esempi completi

### 12.1 SIGNAL puro (`NEW_SETUP`)

```json
{
  "schema_version": "trader_event_envelope_v1",
  "message_type_hint": "NEW_SIGNAL",
  "intents_detected": ["NEW_SETUP"],
  "primary_intent_hint": "NEW_SETUP",
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
        "size_hint": {
          "value": 0.33,
          "unit": "PERCENT",
          "raw": "1/3"
        },
        "note": null,
        "is_optional": false
      },
      {
        "sequence": 2,
        "entry_type": "LIMIT",
        "price": 89100.0,
        "role": "AVERAGING",
        "size_hint": {
          "value": 0.67,
          "unit": "PERCENT",
          "raw": "2/3"
        },
        "note": null,
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
        "close_fraction": null,
        "raw": "TP1 87500"
      },
      {
        "sequence": 2,
        "price": 86800.0,
        "label": "TP2",
        "close_fraction": null,
        "raw": "TP2 86800"
      },
      {
        "sequence": 3,
        "price": 85800.0,
        "label": "TP3",
        "close_fraction": null,
        "raw": "TP3 85800"
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
    "raw_fragments": {
      "entry_text_raw": "Entry A 88650 / Entry B 89100",
      "stop_text_raw": "SL 89450",
      "take_profits_text_raw": "TP1 87500 TP2 86800 TP3 85800"
    }
  },
  "update_payload_raw": {},
  "report_payload_raw": {},
  "targets_raw": [],
  "warnings": [],
  "confidence": 0.96,
  "diagnostics": {}
}
```

### 12.2 UPDATE puro (`MOVE_STOP_TO_BE`)

```json
{
  "schema_version": "trader_event_envelope_v1",
  "message_type_hint": "UPDATE",
  "intents_detected": ["MOVE_STOP_TO_BE"],
  "primary_intent_hint": "MOVE_STOP_TO_BE",
  "instrument": {
    "symbol": "BTCUSDT",
    "side": "SHORT",
    "market_type": null
  },
  "signal_payload_raw": {},
  "update_payload_raw": {
    "stop_update": {
      "mode": "TO_ENTRY",
      "price": null,
      "reference_level": null,
      "raw": "move stop to breakeven"
    },
    "close_update": null,
    "cancel_update": null,
    "entry_update": null,
    "targets_update": null,
    "raw_fragments": {
      "stop_text_raw": "move stop to breakeven",
      "close_text_raw": null,
      "cancel_text_raw": null,
      "entry_text_raw": null,
      "targets_text_raw": null
    }
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

### 12.3 REPORT puro (`REPORT_FINAL_RESULT`)

```json
{
  "schema_version": "trader_event_envelope_v1",
  "message_type_hint": "REPORT",
  "intents_detected": ["REPORT_FINAL_RESULT"],
  "primary_intent_hint": "REPORT_FINAL_RESULT",
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
    "reported_results": [
      {
        "value": 3.2,
        "unit": "R",
        "text": "+3.2R final"
      }
    ],
    "notes": [],
    "summary_text_raw": "final result +3.2R"
  },
  "targets_raw": [],
  "warnings": [],
  "confidence": 0.95,
  "diagnostics": {}
}
```

### 12.4 UPDATE multi-intent (`CLOSE_PARTIAL` + `MOVE_STOP`)

```json
{
  "schema_version": "trader_event_envelope_v1",
  "message_type_hint": "UPDATE",
  "intents_detected": ["CLOSE_PARTIAL", "MOVE_STOP"],
  "primary_intent_hint": "CLOSE_PARTIAL",
  "instrument": {
    "symbol": "ETHUSDT",
    "side": "LONG",
    "market_type": "FUTURES"
  },
  "signal_payload_raw": {},
  "update_payload_raw": {
    "stop_update": {
      "mode": "TO_ENTRY",
      "price": null,
      "reference_level": null,
      "raw": "move stop to entry"
    },
    "close_update": {
      "close_fraction": 0.5,
      "close_percent": 50.0,
      "close_price": 2128.0,
      "close_scope": "PARTIAL",
      "raw": "close 50% at 2128"
    },
    "cancel_update": null,
    "entry_update": null,
    "targets_update": null,
    "raw_fragments": {
      "stop_text_raw": "move stop to entry",
      "close_text_raw": "close 50% at 2128",
      "cancel_text_raw": null,
      "entry_text_raw": null,
      "targets_text_raw": null
    }
  },
  "report_payload_raw": {},
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

### 12.5 UPDATE puro (`CLOSE_FULL`)

```json
{
  "schema_version": "trader_event_envelope_v1",
  "message_type_hint": "UPDATE",
  "intents_detected": ["CLOSE_FULL"],
  "primary_intent_hint": "CLOSE_FULL",
  "instrument": {
    "symbol": "BTCUSDT",
    "side": "LONG",
    "market_type": "FUTURES"
  },
  "signal_payload_raw": {},
  "update_payload_raw": {
    "stop_update": null,
    "close_update": {
      "close_fraction": 1.0,
      "close_percent": 100.0,
      "close_price": 62420.0,
      "close_scope": "FULL",
      "raw": "close full at 62420"
    },
    "cancel_update": null,
    "entry_update": null,
    "targets_update": null,
    "raw_fragments": {
      "stop_text_raw": null,
      "close_text_raw": "close full at 62420",
      "cancel_text_raw": null,
      "entry_text_raw": null,
      "targets_text_raw": null
    }
  },
  "report_payload_raw": {},
  "targets_raw": [
    {
      "kind": "REPLY",
      "value": 1701
    }
  ],
  "warnings": [],
  "confidence": 0.92,
  "diagnostics": {}
}
```

### 12.6 UPDATE puro (`CANCEL_PENDING_ORDERS` / `INVALIDATE_SETUP`)

```json
{
  "schema_version": "trader_event_envelope_v1",
  "message_type_hint": "UPDATE",
  "intents_detected": ["CANCEL_PENDING_ORDERS", "INVALIDATE_SETUP"],
  "primary_intent_hint": "CANCEL_PENDING_ORDERS",
  "instrument": {
    "symbol": "ETHUSDT",
    "side": "SHORT",
    "market_type": null
  },
  "signal_payload_raw": {},
  "update_payload_raw": {
    "stop_update": null,
    "close_update": null,
    "cancel_update": {
      "cancel_scope": "ALL_OPEN",
      "raw": "cancel all open orders"
    },
    "entry_update": null,
    "targets_update": null,
    "raw_fragments": {
      "stop_text_raw": null,
      "close_text_raw": null,
      "cancel_text_raw": "cancel all open orders",
      "entry_text_raw": null,
      "targets_text_raw": null
    }
  },
  "report_payload_raw": {},
  "targets_raw": [
    {
      "kind": "SYMBOL",
      "value": "ETHUSDT"
    }
  ],
  "warnings": [],
  "confidence": 0.9,
  "diagnostics": {}
}
```

### 12.7 Caso misto UPDATE + REPORT (`CLOSE_PARTIAL` + `TP_HIT`)

```json
{
  "schema_version": "trader_event_envelope_v1",
  "message_type_hint": "UPDATE",
  "intents_detected": ["CLOSE_PARTIAL", "TP_HIT"],
  "primary_intent_hint": "CLOSE_PARTIAL",
  "instrument": {
    "symbol": "ETHUSDT",
    "side": "LONG",
    "market_type": "FUTURES"
  },
  "signal_payload_raw": {},
  "update_payload_raw": {
    "stop_update": null,
    "close_update": {
      "close_fraction": 0.5,
      "close_percent": 50.0,
      "close_price": 2128.0,
      "close_scope": "PARTIAL",
      "raw": "close 50% at 2128"
    },
    "cancel_update": null,
    "entry_update": null,
    "targets_update": null,
    "raw_fragments": {
      "stop_text_raw": null,
      "close_text_raw": "close 50% at 2128",
      "cancel_text_raw": null,
      "entry_text_raw": null,
      "targets_text_raw": null
    }
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
    "reported_results": [
      {
        "value": 1.0,
        "unit": "R",
        "text": "+1R"
      }
    ],
    "notes": [],
    "summary_text_raw": "tp1 hit +1R"
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

### 12.8 UPDATE puro (`REENTER`)

```json
{
  "schema_version": "trader_event_envelope_v1",
  "message_type_hint": "UPDATE",
  "intents_detected": ["REENTER"],
  "primary_intent_hint": "REENTER",
  "instrument": {
    "symbol": "SOLUSDT",
    "side": "LONG",
    "market_type": "FUTURES"
  },
  "signal_payload_raw": {},
  "update_payload_raw": {
    "stop_update": null,
    "close_update": null,
    "cancel_update": null,
    "entry_update": {
      "mode": "REENTER",
      "entries": [
        {
          "sequence": 1,
          "entry_type": "LIMIT",
          "price": 142.5,
          "role": "PRIMARY",
          "size_hint": null,
          "note": "reentry zone",
          "is_optional": false
        }
      ],
      "raw": "reenter 142.5"
    },
    "targets_update": null,
    "raw_fragments": {
      "stop_text_raw": null,
      "close_text_raw": null,
      "cancel_text_raw": null,
      "entry_text_raw": "reenter 142.5",
      "targets_text_raw": null
    }
  },
  "report_payload_raw": {},
  "targets_raw": [
    {
      "kind": "EXPLICIT_ID",
      "value": "2110"
    }
  ],
  "warnings": [],
  "confidence": 0.88,
  "diagnostics": {}
}
```

### 12.9 UPDATE puro (`ADD_ENTRY`)

```json
{
  "schema_version": "trader_event_envelope_v1",
  "message_type_hint": "UPDATE",
  "intents_detected": ["ADD_ENTRY"],
  "primary_intent_hint": "ADD_ENTRY",
  "instrument": {
    "symbol": "SOLUSDT",
    "side": "LONG",
    "market_type": "FUTURES"
  },
  "signal_payload_raw": {},
  "update_payload_raw": {
    "stop_update": null,
    "close_update": null,
    "cancel_update": null,
    "entry_update": {
      "mode": "ADD_ENTRY",
      "entries": [
        {
          "sequence": 1,
          "entry_type": "LIMIT",
          "price": 141.2,
          "role": "AVERAGING",
          "size_hint": {
            "value": 20.0,
            "unit": "PERCENT",
            "raw": "20%"
          },
          "note": "add on dip",
          "is_optional": false
        }
      ],
      "raw": "add entry 141.2"
    },
    "targets_update": null,
    "raw_fragments": {
      "stop_text_raw": null,
      "close_text_raw": null,
      "cancel_text_raw": null,
      "entry_text_raw": "add entry 141.2",
      "targets_text_raw": null
    }
  },
  "report_payload_raw": {},
  "targets_raw": [
    {
      "kind": "REPLY",
      "value": 2110
    }
  ],
  "warnings": [],
  "confidence": 0.89,
  "diagnostics": {}
}
```

### 12.10 UPDATE puro (`UPDATE_TAKE_PROFITS`)

```json
{
  "schema_version": "trader_event_envelope_v1",
  "message_type_hint": "UPDATE",
  "intents_detected": ["UPDATE_TAKE_PROFITS"],
  "primary_intent_hint": "UPDATE_TAKE_PROFITS",
  "instrument": {
    "symbol": "BTCUSDT",
    "side": "LONG",
    "market_type": "FUTURES"
  },
  "signal_payload_raw": {},
  "update_payload_raw": {
    "stop_update": null,
    "close_update": null,
    "cancel_update": null,
    "entry_update": null,
    "targets_update": {
      "mode": "REPLACE_ALL",
      "target_level": null,
      "take_profits": [
        {
          "sequence": 1,
          "price": 63120.0,
          "label": "TP1",
          "close_fraction": null,
          "raw": "TP1 63120"
        },
        {
          "sequence": 2,
          "price": 63500.0,
          "label": "TP2",
          "close_fraction": null,
          "raw": "TP2 63500"
        }
      ],
      "raw": "new targets 63120 / 63500"
    },
    "raw_fragments": {
      "stop_text_raw": null,
      "close_text_raw": null,
      "cancel_text_raw": null,
      "entry_text_raw": null,
      "targets_text_raw": "new targets 63120 / 63500"
    }
  },
  "report_payload_raw": {},
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

### 12.11 REPORT puro (`ENTRY_FILLED`)

```json
{
  "schema_version": "trader_event_envelope_v1",
  "message_type_hint": "REPORT",
  "intents_detected": ["ENTRY_FILLED"],
  "primary_intent_hint": "ENTRY_FILLED",
  "instrument": {
    "symbol": "BTCUSDT",
    "side": "LONG",
    "market_type": "FUTURES"
  },
  "signal_payload_raw": {},
  "update_payload_raw": {},
  "report_payload_raw": {
    "events": [
      {
        "event_type": "ENTRY_FILLED",
        "level": 1,
        "price": 62510.0,
        "result": null,
        "raw_fragment": "entry filled 62510"
      }
    ],
    "reported_results": [],
    "notes": [],
    "summary_text_raw": "entry filled 62510"
  },
  "targets_raw": [
    {
      "kind": "REPLY",
      "value": 1701
    }
  ],
  "warnings": [],
  "confidence": 0.9,
  "diagnostics": {}
}
```

### 12.12 REPORT puro (`TP_HIT`)

```json
{
  "schema_version": "trader_event_envelope_v1",
  "message_type_hint": "REPORT",
  "intents_detected": ["TP_HIT"],
  "primary_intent_hint": "TP_HIT",
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
    "reported_results": [
      {
        "value": 1.0,
        "unit": "R",
        "text": "+1R"
      }
    ],
    "notes": [],
    "summary_text_raw": "tp1 hit +1R"
  },
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

### 12.13 REPORT puro (`SL_HIT`)

```json
{
  "schema_version": "trader_event_envelope_v1",
  "message_type_hint": "REPORT",
  "intents_detected": ["SL_HIT"],
  "primary_intent_hint": "SL_HIT",
  "instrument": {
    "symbol": "BTCUSDT",
    "side": "SHORT",
    "market_type": "FUTURES"
  },
  "signal_payload_raw": {},
  "update_payload_raw": {},
  "report_payload_raw": {
    "events": [
      {
        "event_type": "SL_HIT",
        "level": null,
        "price": 89450.0,
        "result": {
          "value": -1.0,
          "unit": "R",
          "text": "-1R"
        },
        "raw_fragment": "sl hit -1R"
      }
    ],
    "reported_results": [
      {
        "value": -1.0,
        "unit": "R",
        "text": "-1R"
      }
    ],
    "notes": [],
    "summary_text_raw": "sl hit -1R"
  },
  "targets_raw": [
    {
      "kind": "REPLY",
      "value": 1701
    }
  ],
  "warnings": [],
  "confidence": 0.92,
  "diagnostics": {}
}
```

### 12.14 REPORT puro (`EXIT_BE`)

```json
{
  "schema_version": "trader_event_envelope_v1",
  "message_type_hint": "REPORT",
  "intents_detected": ["EXIT_BE"],
  "primary_intent_hint": "EXIT_BE",
  "instrument": {
    "symbol": "BTCUSDT",
    "side": "LONG",
    "market_type": "FUTURES"
  },
  "signal_payload_raw": {},
  "update_payload_raw": {},
  "report_payload_raw": {
    "events": [
      {
        "event_type": "EXIT_BE",
        "level": null,
        "price": 62510.0,
        "result": {
          "value": 0.0,
          "unit": "R",
          "text": "BE"
        },
        "raw_fragment": "closed at breakeven"
      }
    ],
    "reported_results": [
      {
        "value": 0.0,
        "unit": "R",
        "text": "BE"
      }
    ],
    "notes": [],
    "summary_text_raw": "closed at breakeven"
  },
  "targets_raw": [
    {
      "kind": "REPLY",
      "value": 1701
    }
  ],
  "warnings": [],
  "confidence": 0.9,
  "diagnostics": {}
}
```

### 12.15 REPORT puro (`REPORT_PARTIAL_RESULT`)

```json
{
  "schema_version": "trader_event_envelope_v1",
  "message_type_hint": "REPORT",
  "intents_detected": ["REPORT_PARTIAL_RESULT"],
  "primary_intent_hint": "REPORT_PARTIAL_RESULT",
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
        "event_type": "PARTIAL_RESULT",
        "level": 1,
        "price": null,
        "result": {
          "value": 2.4,
          "unit": "PERCENT",
          "text": "+2.4%"
        },
        "raw_fragment": "partial result +2.4%"
      }
    ],
    "reported_results": [
      {
        "value": 2.4,
        "unit": "PERCENT",
        "text": "+2.4%"
      }
    ],
    "notes": ["partial summary only"],
    "summary_text_raw": "partial result +2.4%"
  },
  "targets_raw": [],
  "warnings": [],
  "confidence": 0.9,
  "diagnostics": {}
}
```

### 12.16 REPORT puro (`REPORT_FINAL_RESULT`)

```json
{
  "schema_version": "trader_event_envelope_v1",
  "message_type_hint": "REPORT",
  "intents_detected": ["REPORT_FINAL_RESULT"],
  "primary_intent_hint": "REPORT_FINAL_RESULT",
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
    "reported_results": [
      {
        "value": 3.2,
        "unit": "R",
        "text": "+3.2R final"
      }
    ],
    "notes": [],
    "summary_text_raw": "final result +3.2R"
  },
  "targets_raw": [],
  "warnings": [],
  "confidence": 0.95,
  "diagnostics": {}
}
```

### 12.17 INFO_ONLY

```json
{
  "schema_version": "trader_event_envelope_v1",
  "message_type_hint": "INFO_ONLY",
  "intents_detected": ["INFO_ONLY"],
  "primary_intent_hint": "INFO_ONLY",
  "instrument": {
    "symbol": null,
    "side": null,
    "market_type": null
  },
  "signal_payload_raw": {},
  "update_payload_raw": {},
  "report_payload_raw": {},
  "targets_raw": [],
  "warnings": [],
  "confidence": 0.72,
  "diagnostics": {
    "note_type": "informational"
  }
}
```

---

## 13. Regole operative del contratto

### 13.1 Blocchi vuoti

Se un blocco non e pertinente, puo essere lasciato come oggetto vuoto `{}` oppure struttura vuota equivalente, senza inventare campi.

### 13.2 Nessuna semantica di execution nel parser envelope

`TraderEventEnvelopeV1` non deve contenere:

- action lists
- operation commands
- `op_type`
- mapping esplicito `intent -> command`

### 13.3 Compositi ammessi

A livello envelope sono ammessi casi compositi:

- `UPDATE + REPORT`
- `UPDATE` con piu intent compatibili
- opzionalmente `INFO_ONLY + REPORT` se il parser ha entrambi i segnali

Il caso `NEW_SIGNAL + UPDATE` resta eccezionale e va trattato con warning/diagnostics.

### 13.4 Adapter e bridge

Se esiste ancora un adapter legacy, deve:

- preservare il piu possibile `message_type_hint`
- preservare `intents_detected`
- mappare i dati nei payload raw senza introdurre semantica di execution

---

## 14. Sintesi finale

`TraderEventEnvelopeV1` e:

- l'uscita parser-side uniforme
- un contratto semantico di parsing
- piu ricco di una semplice classificazione
- ancora non canonico finale
- ancora non execution-oriented
- il ponte stabile verso `CanonicalMessageV1`
