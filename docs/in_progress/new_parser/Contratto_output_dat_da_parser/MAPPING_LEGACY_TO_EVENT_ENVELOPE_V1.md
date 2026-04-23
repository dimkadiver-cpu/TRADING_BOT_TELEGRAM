# Mapping Legacy To Event Envelope V1

> Data: 2026-04-22
> Stato: proposta operativa
> Scopo: definire come i campi legacy di `TraderParseResult` attuale devono essere convertiti in `TraderEventEnvelopeV1`.

---

## Obiettivo

Questo documento serve a evitare mapping impliciti e fallback sparsi.

Il principio e semplice:

- i parser legacy continuano temporaneamente a produrre `TraderParseResult`
- un adapter centrale converte quell'output in `TraderEventEnvelopeV1`
- il normalizer legge il nuovo envelope, non il legacy grezzo

---

## Sorgente legacy

Shape attuale:

```python
TraderParseResult(
    message_type: str,
    intents: list[str],
    entities: dict[str, Any],
    target_refs: list[dict[str, Any]],
    reported_results: list[dict[str, Any]],
    warnings: list[str],
    confidence: float,
    primary_intent: str | None,
    actions_structured: list[dict[str, Any]],
    target_scope: dict[str, Any],
    linking: dict[str, Any],
    diagnostics: dict[str, Any],
)
```

Destination:

```python
TraderEventEnvelopeV1(
    schema_version: str,
    message_type_hint: str | None,
    intents_detected: list[str],
    primary_intent_hint: str | None,
    instrument: InstrumentRaw,
    signal_payload_raw: SignalPayloadRaw,
    update_payload_raw: UpdatePayloadRaw,
    report_payload_raw: ReportPayloadRaw,
    targets_raw: list[TargetRefRaw],
    warnings: list[str],
    confidence: float,
    diagnostics: dict[str, Any],
)
```

---

## Top-level mapping

| Legacy source | Envelope target | Regola |
|---|---|---|
| `message_type` | `message_type_hint` | Copia diretta |
| `intents` | `intents_detected` | Copia diretta |
| `primary_intent` | `primary_intent_hint` | Copia diretta |
| `warnings` | `warnings` | Copia diretta |
| `confidence` | `confidence` | Copia diretta |
| `diagnostics` | `diagnostics` | Copia diretta, arricchibile |
| costante | `schema_version` | Sempre `"trader_event_envelope_v1"` |

### Note

- `actions_structured`, `target_scope`, `linking` non sono piu source of truth.
- possono essere copiati in `diagnostics` durante la fase di migrazione.

---

## Instrument mapping

| Legacy source | Envelope target | Regola |
|---|---|---|
| `entities.symbol` | `instrument.symbol` | Copia diretta |
| `entities.side` | `instrument.side` | Copia diretta |
| `entities.direction` | `instrument.side` | Fallback se `side` manca |
| `entities.market_type` | `instrument.market_type` | Copia diretta |

### Regola di precedenza

1. `entities.side`
2. `entities.direction`
3. `None`

### Regola di normalizzazione

- valori ammessi finali: `LONG`, `SHORT`
- alias legacy vanno normalizzati prima di scrivere nel target

---

## Signal payload raw mapping

Questo blocco va popolato quando il legacy contiene dati di segnale, anche se il `message_type` non e perfetto.

### Entry structure

| Legacy source | Envelope target | Regola |
|---|---|---|
| `entities.entry_structure` | `signal_payload_raw.entry_structure` | Copia diretta normalizzata |

### Entry legs

Ordine di precedenza consigliato:

1. `entities.entry_plan_entries`
2. `entities.entries`
3. `entities.entry`

| Legacy source | Envelope target | Regola |
|---|---|---|
| `entities.entry_plan_entries[*]` | `signal_payload_raw.entries[*]` | Fonte preferita |
| `entities.entries[*]` | `signal_payload_raw.entries[*]` | Fallback strutturato |
| `entities.entry[*]` | `signal_payload_raw.entries[*]` | Ultimo fallback da lista prezzi |

### Mapping di ogni leg

| Legacy source | Envelope target | Regola |
|---|---|---|
| `sequence` | `sequence` | Copia diretta, fallback indice+1 |
| `order_type` | `entry_type` | `MARKET -> MARKET`, altro -> `LIMIT` |
| `price` | `price` | Copia diretta float |
| `role` | `role` | Copia se compatibile, altrimenti `UNKNOWN` |
| `size_hint` | `size_hint` | Copia diretta |
| `is_optional` | `is_optional` | Copia diretta |

### Nota importante

Nel legacy attuale `entities.entries` contiene spesso piu informazione di `entities.entry`.

Quindi:

- `entities.entries` deve essere considerato autorevole rispetto a `entities.entry`
- `entities.entry` va usato solo come fallback finale

Questo punto e fondamentale per chiudere i casi `TWO_STEP` residui di `trader_c`.

### Stop loss

| Legacy source | Envelope target | Regola |
|---|---|---|
| `entities.stop_loss` numero | `signal_payload_raw.stop_loss.price` | Copia float |
| `entities.stop_text_raw` | `signal_payload_raw.stop_loss.raw` | Copia opzionale |

### Take profits

| Legacy source | Envelope target | Regola |
|---|---|---|
| `entities.take_profits[*]` | `signal_payload_raw.take_profits[*]` | Mappa in ordine `sequence=i+1` |

### Risk hint

| Legacy source | Envelope target | Regola |
|---|---|---|
| `entities.risk_value_normalized` | `signal_payload_raw.risk_hint.value` | Copia float |
| `entities.risk_value_raw` | `signal_payload_raw.risk_hint.raw` | Copia string |
| unit derivata | `signal_payload_raw.risk_hint.unit` | Se da `% dep` -> `PERCENT`, altrimenti `UNKNOWN` |
| `entities.risk_percent` | `signal_payload_raw.risk_hint.value` | Fallback se presente |

### Raw fragments

| Legacy source | Envelope target | Regola |
|---|---|---|
| `entities.entry_text_raw` | `signal_payload_raw.raw_fragments["entry"]` | Copia opzionale |
| `entities.stop_text_raw` | `signal_payload_raw.raw_fragments["stop"]` | Copia opzionale |
| `entities.take_profits_text_raw` | `signal_payload_raw.raw_fragments["take_profits"]` | Copia opzionale |

---

## Update payload raw mapping

Il blocco `update_payload_raw.operations` non va derivato da `actions_structured`.
Va derivato dagli `intents` + `entities`.

Ogni intent mappabile genera una `UpdateOperationRaw`.

### Mapping intent -> operation

| Legacy intent | Envelope target |
|---|---|
| `U_MOVE_STOP` | `SET_STOP` |
| `U_MOVE_STOP_TO_BE` | `SET_STOP` |
| `U_CLOSE_FULL` | `CLOSE` |
| `U_CLOSE_PARTIAL` | `CLOSE` |
| `U_CANCEL_PENDING_ORDERS` | `CANCEL_PENDING` |
| `U_INVALIDATE_SETUP` | `CANCEL_PENDING` |
| `U_REENTER` | `MODIFY_ENTRIES` |
| `U_ADD_ENTRY` | `MODIFY_ENTRIES` |
| `U_UPDATE_TAKE_PROFITS` | `MODIFY_TARGETS` |

### SET_STOP

| Legacy source | Envelope target | Regola |
|---|---|---|
| `entities.new_stop_level` | `set_stop.target_type/value` | Parser semantico: `ENTRY`, `TP1`, prezzo |
| `entities.new_stop_price` | `set_stop.value` | Fallback per target `PRICE` |

#### Regole

- `"ENTRY"`, `"BE"`, `"BREAKEVEN"` -> `target_type="ENTRY"`
- `"TP1"`, `"TP2"` -> `target_type="TP_LEVEL"`, `value=1/2`
- numero -> `target_type="PRICE"`, `value=float(...)`

### CLOSE

| Legacy source | Envelope target | Regola |
|---|---|---|
| `entities.close_fraction` | `close.close_fraction` | Copia float |
| `entities.partial_close_percent` | `close.close_fraction` | Se espresso in percentuale, convertire in frazione |
| `entities.close_price` | `close.close_price` | Copia float |
| `entities.close_scope` | `close.close_scope` | Copia diretta |

#### Regole

- `U_CLOSE_FULL` -> se scope mancante, usare `close_scope="FULL"`
- `U_CLOSE_PARTIAL` -> se fraction presente, usare `close_scope="PARTIAL"`
- `close_fraction` e semantica minima da preservare

### CANCEL_PENDING

| Legacy source | Envelope target | Regola |
|---|---|---|
| `entities.cancel_scope` | `cancel_pending.cancel_scope` | Copia diretta |

#### Regole

- se `U_INVALIDATE_SETUP` e scope assente, usare fallback `ALL_PENDING_ENTRIES`

### MODIFY_ENTRIES

| Legacy source | Envelope target | Regola |
|---|---|---|
| `U_REENTER` | `modify_entries.mode="REENTER"` | Modalita fissa |
| `U_ADD_ENTRY` | `modify_entries.mode="ADD"` | Modalita fissa |
| `entities.entry_plan_entries` | `modify_entries.entries` | Fonte preferita |
| `entities.entries` | `modify_entries.entries` | Fallback strutturato |
| `entities.new_entry_price` | `modify_entries.entries[0].price` | Fallback specifico `ADD` |

### MODIFY_TARGETS

| Legacy source | Envelope target | Regola |
|---|---|---|
| `U_UPDATE_TAKE_PROFITS` | `modify_targets.mode="REPLACE_ALL"` | Modalita default |
| `entities.take_profits[*]` | `modify_targets.take_profits[*]` | Sequence `i+1` |
| `entities.hit_target` | `modify_targets.target_tp_level` | Solo se serve su update puntuale futuro |

---

## Report payload raw mapping

Anche il blocco report va derivato dagli `intents` + `reported_results`.

### Mapping intent -> report event

| Legacy intent | Envelope target |
|---|---|
| `U_ACTIVATION` | `ENTRY_FILLED` |
| `U_MARK_FILLED` | `ENTRY_FILLED` |
| `U_TP_HIT` | `TP_HIT` |
| `U_STOP_HIT` | `STOP_HIT` |
| `U_EXIT_BE` | `BREAKEVEN_EXIT` |
| `U_REPORT_FINAL_RESULT` | `FINAL_RESULT` |

### TP_HIT

| Legacy source | Envelope target | Regola |
|---|---|---|
| `entities.hit_target` | `events[*].level` | Parse `TP1`, `TP2`, ... |
| `entities.close_price` | `events[*].price` | Se disponibile |
| `reported_results[0]` | `events[*].result` | Se presente |

### STOP_HIT

| Legacy source | Envelope target | Regola |
|---|---|---|
| `entities.close_price` | `events[*].price` | Se disponibile |
| `reported_results[0]` | `events[*].result` | Se disponibile |

### FINAL_RESULT

| Legacy source | Envelope target | Regola |
|---|---|---|
| `reported_results[0]` | `report_payload_raw.reported_result` | Fonte primaria |
| `reported_results[0]` | `events[*].result` | Facoltativo per evento `FINAL_RESULT` |

### ReportedResult mapping

| Legacy source | Envelope target | Regola |
|---|---|---|
| `reported_results[0].value` | `value` | Copia float |
| `reported_results[0].unit` | `unit` | Normalizza in `R`, `PERCENT`, `TEXT`, `UNKNOWN` |
| `reported_results[0].text` | `text` | Copia string |

### Nota

`reported_results` non va buttato in `diagnostics`.
Va assorbito formalmente in `report_payload_raw.reported_result`.

---

## Targets raw mapping

`target_refs` legacy converge in `targets_raw`.

| Legacy source | Envelope target | Regola |
|---|---|---|
| `target_refs[*].kind` | `targets_raw[*].kind` | Uppercase/normalizzazione |
| `target_refs[*].ref` | `targets_raw[*].value` | Copia diretta |

### Mapping kind

| Legacy kind | Envelope kind |
|---|---|
| `reply` | `REPLY` |
| `telegram_link` | `TELEGRAM_LINK` |
| `message_id` | `MESSAGE_ID` |
| `explicit_id` | `EXPLICIT_ID` |
| `symbol` | `SYMBOL` |
| altro / mancante | `UNKNOWN` |

### Nota

`target_scope` e `linking` non vanno convertiti in campi top-level dell'envelope.

Durante la migrazione:

- si copiano in `diagnostics["legacy_target_scope"]`
- si copiano in `diagnostics["legacy_linking"]`

La costruzione del targeting finale resta responsabilita del normalizer.

---

## Campi legacy da copiare in diagnostics

Questi campi non sono source of truth del nuovo envelope, ma possono essere preservati in `diagnostics` durante la transizione:

- `actions_structured`
- `target_scope`
- `linking`
- `entities.entry`
- `entities.entry_order_type`
- `entities.entry_plan_type`
- `entities.new_stop_price` se gia riassorbito in `set_stop`
- `entities.take_profits_text_raw`
- qualsiasi frammento non ancora mappato

---

## Regole di precedenza obbligatorie

### Entry

1. `entry_plan_entries`
2. `entries`
3. `entry`

### Side

1. `entities.side`
2. `entities.direction`

### Stop update

1. `entities.new_stop_level`
2. `entities.new_stop_price`

### Report result

1. `reported_results[0]`
2. fallback da entity sparse solo se strettamente necessario

---

## Regole operative dell'adapter

Queste regole non introducono nuova semantica. Servono a fare in modo che tutti i trader passino nello stesso punto di conversione con comportamento stabile.

### 1. L'adapter non riclassifica il messaggio

- `message_type_hint` deriva da `message_type`
- `intents_detected` deriva da `intents`
- `primary_intent_hint` deriva da `primary_intent`

L'adapter puo costruire piu blocchi contemporaneamente:

- `signal_payload_raw`
- `update_payload_raw`
- `report_payload_raw`

Quindi un `UPDATE` con `U_TP_HIT` puo avere sia `update_payload_raw.operations` sia `report_payload_raw.events`.

### 2. L'adapter preferisce shape strutturate a campi piatti

Ordine generale:

1. liste/oggetti gia strutturati
2. campi semantici espliciti
3. fallback piatti derivati da singoli numeri o stringhe

Esempio pratico:

- per le entry: `entry_plan_entries` > `entries` > `entry`
- per stop update: `new_stop_level` > `new_stop_price`
- per result: `reported_results[0]` > fallback sparsi

### 3. I blocchi vuoti restano vuoti

Non bisogna creare operazioni o eventi sintetici se il legacy non contiene abbastanza informazione minima.

Esempi:

- non creare `MODIFY_ENTRIES` se non esistono entry utilizzabili
- non creare `MODIFY_TARGETS` se `take_profits` e vuoto
- non creare `FINAL_RESULT` se non c'e nessun `reported_results[0]` e nessun intent coerente

### 4. I residui legacy vanno preservati ma isolati

Se un campo non entra nella shape minima, non va perso durante la migrazione iniziale.

Va copiato in `diagnostics`, con chiave esplicita `legacy_*`, per esempio:

- `diagnostics["legacy_actions_structured"]`
- `diagnostics["legacy_target_scope"]`
- `diagnostics["legacy_linking"]`
- `diagnostics["legacy_entities_entry"]`

---

## Regole di normalizzazione minime

Queste normalizzazioni vanno fatte dentro l'adapter, non lasciate ai singoli parser trader.

### Side

| Input legacy | Output envelope |
|---|---|
| `buy`, `long`, `LONG` | `LONG` |
| `sell`, `short`, `SHORT` | `SHORT` |
| altro / ambiguo | `None` |

### Market type

| Input legacy | Output envelope |
|---|---|
| `spot`, `SPOT` | `SPOT` |
| `futures`, `future`, `perp`, `PERPETUAL` | `FUTURES` |
| altro / mancante | `UNKNOWN` oppure `None` se il dato non esiste davvero |

### Entry type

| Input legacy | Output envelope |
|---|---|
| `MARKET` | `MARKET` |
| `LIMIT`, `RANGE`, altro ordine di ingresso con prezzo | `LIMIT` |
| mancante con `price` valorizzato | `LIMIT` |
| mancante senza `price` | `None` |

### Result unit

| Input legacy | Output envelope |
|---|---|
| `R`, `RR` | `R` |
| `%`, `PERCENT`, `PCT` | `PERCENT` |
| testo libero non numerico | `TEXT` |
| altro / ambiguo | `UNKNOWN` |

---

## Casi che il mapping deve coprire subito

### SIGNAL

- `ONE_SHOT`
- `TWO_STEP`
- `RANGE`
- `LADDER`
- `risk %`
- `size_hint`

### UPDATE

- move stop a prezzo
- move stop a entry
- move stop a TP level
- close full
- close partial con `close_fraction`
- cancel pending
- reenter
- update take profits

### REPORT

- entry filled
- tp hit
- stop hit
- breakeven exit
- final result

### COMPOSITI

- `UPDATE + REPORT`

---

## Esempi concreti di mapping

### Esempio A: new signal TWO_STEP

Input legacy:

```python
TraderParseResult(
    message_type="NEW_SIGNAL",
    intents=[],
    primary_intent=None,
    entities={
        "symbol": "BTCUSDT",
        "side": "SHORT",
        "entry_structure": "TWO_STEP",
        "entries": [
            {"sequence": 1, "price": 88650.0, "size_hint": "1/3"},
            {"sequence": 2, "price": 89100.0, "size_hint": "2/3"},
        ],
        "stop_loss": 89450.0,
        "take_profits": [
            {"price": 87500.0},
            {"price": 86800.0},
            {"price": 85800.0},
        ],
        "risk_value_raw": "1% dep",
        "risk_percent": 1.0,
    },
    target_refs=[],
    reported_results=[],
    warnings=[],
    confidence=0.96,
    actions_structured=[],
    target_scope={},
    linking={},
    diagnostics={},
)
```

Output envelope atteso:

```python
TraderEventEnvelopeV1(
    message_type_hint="NEW_SIGNAL",
    intents_detected=[],
    primary_intent_hint=None,
    instrument={
        "symbol": "BTCUSDT",
        "side": "SHORT",
        "market_type": None,
    },
    signal_payload_raw={
        "entry_structure": "TWO_STEP",
        "entries": [
            {"sequence": 1, "entry_type": "LIMIT", "price": 88650.0, "size_hint": "1/3"},
            {"sequence": 2, "entry_type": "LIMIT", "price": 89100.0, "size_hint": "2/3"},
        ],
        "stop_loss": {"price": 89450.0},
        "take_profits": [
            {"sequence": 1, "price": 87500.0},
            {"sequence": 2, "price": 86800.0},
            {"sequence": 3, "price": 85800.0},
        ],
        "risk_hint": {"value": 1.0, "unit": "PERCENT", "raw": "1% dep"},
    },
)
```

Nota: questo e il caso che deve evitare il bug attuale dove `entities.entry` vince impropriamente su `entities.entries`.

### Esempio B: update move stop to breakeven

Input legacy:

```python
TraderParseResult(
    message_type="UPDATE",
    intents=["U_MOVE_STOP_TO_BE"],
    primary_intent="U_MOVE_STOP_TO_BE",
    entities={
        "new_stop_level": "BE",
    },
    target_refs=[{"kind": "reply", "ref": 1701}],
    reported_results=[],
    warnings=[],
    confidence=0.91,
    actions_structured=[],
    target_scope={},
    linking={},
    diagnostics={},
)
```

Output envelope atteso:

```python
TraderEventEnvelopeV1(
    message_type_hint="UPDATE",
    intents_detected=["U_MOVE_STOP_TO_BE"],
    primary_intent_hint="U_MOVE_STOP_TO_BE",
    update_payload_raw={
        "operations": [
            {
                "op_type": "SET_STOP",
                "set_stop": {"target_type": "ENTRY", "value": None},
                "source_intent": "U_MOVE_STOP_TO_BE",
            }
        ]
    },
    targets_raw=[
        {"kind": "REPLY", "value": 1701},
    ],
)
```

Nota: il parser puo continuare a emettere `U_MOVE_STOP_TO_BE`, ma il contratto operativo downstream vede `SET_STOP`.

### Esempio C: update + report con take profit hit

Input legacy:

```python
TraderParseResult(
    message_type="UPDATE",
    intents=["U_TP_HIT", "U_CLOSE_PARTIAL"],
    primary_intent="U_TP_HIT",
    entities={
        "hit_target": "TP1",
        "close_fraction": 0.5,
        "close_price": 87500.0,
    },
    target_refs=[{"kind": "reply", "ref": 1701}],
    reported_results=[{"value": 2.0, "unit": "R", "text": "+2R"}],
    warnings=[],
    confidence=0.94,
    actions_structured=[],
    target_scope={},
    linking={},
    diagnostics={},
)
```

Output envelope atteso:

```python
TraderEventEnvelopeV1(
    message_type_hint="UPDATE",
    intents_detected=["U_TP_HIT", "U_CLOSE_PARTIAL"],
    primary_intent_hint="U_TP_HIT",
    update_payload_raw={
        "operations": [
            {
                "op_type": "CLOSE",
                "close": {
                    "close_fraction": 0.5,
                    "close_price": 87500.0,
                    "close_scope": "PARTIAL",
                },
                "source_intent": "U_CLOSE_PARTIAL",
            }
        ]
    },
    report_payload_raw={
        "events": [
            {
                "event_type": "TP_HIT",
                "level": 1,
                "price": 87500.0,
                "result": {"value": 2.0, "unit": "R", "text": "+2R"},
            }
        ],
        "reported_result": {"value": 2.0, "unit": "R", "text": "+2R"},
    },
)
```

---

## Primo adapter operativo consigliato

Funzione da introdurre:

```python
def adapt_legacy_parse_result_to_event_envelope(
    result: TraderParseResult,
) -> TraderEventEnvelopeV1:
    ...
```

### Requisiti minimi

1. nessuna logica trader-specifica
2. usa solo `message_type`, `intents`, `entities`, `target_refs`, `reported_results`
3. applica le precedenze definite in questo documento
4. copia i residui legacy in `diagnostics`

### Checklist di accettazione per l'implementazione

- un caso con `entities.entries` a 2 leg produce `signal_payload_raw.entries` con 2 leg
- `U_MOVE_STOP` e `U_MOVE_STOP_TO_BE` convergono entrambi in `SET_STOP`
- `U_CLOSE_PARTIAL` preserva `close_fraction` quando presente
- `reported_results[0]` confluisce in `report_payload_raw.reported_result`
- `target_refs` legacy confluisce in `targets_raw` senza perdita di `kind/value`

---

## Messaggio architetturale finale

Il mapping legacy -> envelope deve vivere in un solo posto.

Non dobbiamo avere:

- parser che fanno meta-normalizzazione locale
- normalizer che fa fallback diversi per ogni trader
- report tool che ricostruisce shape diverse in modo indipendente

Un solo adapter centrale, una sola precedenza, una sola shape parser-side.
