# Proposta Schema Unico Parser Eventi V1

> Data: 2026-04-22
> Stato: proposta concreta
> Scopo: definire un contratto dati intermedio unico per tutti i parser trader-specifici, valido per tutti gli eventi (`SIGNAL`, `UPDATE`, `REPORT`, `INFO`) e per tutti i blocchi semantici.

---

## Esito del riesame

Dopo confronto con `canonical_parser_model_v1.py`, questa proposta viene adattata verso una versione piu minimale e piu vicina al canonical finale.

### Direzione scelta

- niente doppio albero troppo largo
- niente contenitori ridondanti se non portano valore reale
- pochi blocchi autorevoli
- shape parser-side compatta
- adapter verso `CanonicalMessage v1` il piu sottile possibile

### Decisione pratica

Il parser-side uniforme deve avere solo:

- metadati minimi top-level
- un solo blocco `instrument`
- un solo blocco `signal_payload_raw`
- un solo blocco `update_payload_raw`
- un solo blocco `report_payload_raw`
- un solo blocco `targets_raw`

Tutto il resto deve essere:

- o assorbito dentro questi blocchi
- o lasciato in `diagnostics`

Questa e la versione raccomandata come base implementativa.

### Correzione importante

Struttura minima non significa perdere informazione utile.

I seguenti dati devono essere preservati nel contratto minimo quando disponibili:

- `risk %` / `risk_hint`
- `size_hint` per singola entry
- `role` della entry (`PRIMARY`, `AVERAGING`, ecc.)
- `close_fraction` per `CLOSE PARTIAL`
- `close_price` quando presente
- `new_stop_level` / `new_stop_price` come semantica unificata di update stop
- `reported_result` per `FINAL_RESULT`
- `price` / `level` nei report come `TP_HIT` e `STOP_HIT`

La semplificazione deve eliminare duplicazioni e contenitori inutili, non i campi utili al dominio.

---

## Obiettivo

Eliminare la variabilita di shape tra parser trader-specifici.

Oggi parser diversi popolano campi diversi o con significati leggermente diversi:

- `entry` vs `entries` vs `entry_plan_entries`
- `take_profits` vs `reported_results`
- `new_stop_level` vs `new_stop_price`
- `actions_structured` con payload diversi a seconda del profilo

Questo obbliga il normalizer a fare bridge impliciti e fallback multipli.

La proposta introduce un solo contratto intermedio repository-wide:

- stessa shape per tutti i parser
- stessi nomi campo
- stessi blocchi semantici
- campi opzionali ammessi, ma forma sempre identica

Il parser trader-specifico non deve piu decidere la forma finale del messaggio. Deve solo riempire uno schema unico di estrazione.

---

## Principio guida

Differenze tra trader devono stare nei valori, non nella struttura.

Esempi:

- trader A puo popolare `entry_plan.legs`
- trader B puo lasciare `entry_plan.legs = []`
- trader C puo valorizzare `report_payload.events`

Tutti pero devono restituire sempre gli stessi blocchi top-level.

---

## Livelli del flusso

### Livello 1: parser trader-specifico

Produce un oggetto intermedio uniforme:

- `TraderEventEnvelopeV1`

Questo oggetto contiene:

- hint di classificazione
- intent rilevati
- blocchi raw standardizzati
- target raw
- warning
- diagnostica

### Livello 2: canonical normalizer

Converte `TraderEventEnvelopeV1` in:

- `CanonicalMessage v1`

Tutta la semantica finale resta qui:

- decisione `SIGNAL / UPDATE / REPORT / INFO`
- costruzione payload canonici
- strict validation

### Livello 3: router / persistence / operation_rules

Leggono solo il canonical finale.

---

## Contratto top-level proposto

Ogni parser deve emettere sempre questo envelope:

```python
TraderEventEnvelopeV1 = {
    "schema_version": "trader_event_envelope_v1",
    "message_type_hint": str | None,
    "intents_detected": list[str],
    "primary_intent_hint": str | None,
    "raw_event": dict,
    "raw_targets": list[dict],
    "raw_report_events": list[dict],
    "warnings": list[str],
    "confidence": float,
    "diagnostics": dict,
}
```

## Contratto minimale raccomandato

Per ridurre variabili e distanza dal canonical finale, la shape consigliata e questa:

```python
TraderEventEnvelopeV1 = {
    "schema_version": "trader_event_envelope_v1",
    "message_type_hint": str | None,
    "intents_detected": list[str],
    "primary_intent_hint": str | None,
    "instrument": dict,
    "signal_payload_raw": dict,
    "update_payload_raw": dict,
    "report_payload_raw": dict,
    "targets_raw": list[dict],
    "warnings": list[str],
    "confidence": float,
    "diagnostics": dict,
}
```

### Perche questa shape e preferibile

- e piu vicina al `CanonicalMessage v1`
- evita un contenitore generico `raw_event` troppo largo
- evita blocchi non essenziali come `position_context` e `freeform_notes` come first-class field
- riduce la tentazione di aggiungere micro-campi ad hoc
- rende l'adapter quasi meccanico

### Regola forte

Se un'informazione non entra bene in:

- `instrument`
- `signal_payload_raw`
- `update_payload_raw`
- `report_payload_raw`
- `targets_raw`

allora deve andare in `diagnostics`, non in un nuovo blocco top-level.

### Regole

- `schema_version`: obbligatorio, fisso.
- `message_type_hint`: hint prodotto dal parser (`NEW_SIGNAL`, `UPDATE`, `INFO_ONLY`, `UNCLASSIFIED`), non ancora source of truth finale.
- `intents_detected`: lista intent raw rilevati dal parser.
- `primary_intent_hint`: eventuale intent primario suggerito dal parser.
- `raw_event`: blocco principale uniforme.
- `raw_targets`: riferimenti raw verso il segnale target.
- `raw_report_events`: eventi report raw se presenti.
- `warnings`: warning parser-specifici.
- `confidence`: score parser-specifico.
- `diagnostics`: tracing, marker matchati, versioni parser.

---

## Blocco `raw_event`

`raw_event` e il contenitore unico dei dati semantici grezzi.

```python
raw_event = {
    "instrument": {...},
    "entry_plan": {...},
    "stop_plan": {...},
    "take_profit_plan": {...},
    "update_plan": {...},
    "report_payload": {...},
    "risk_plan": {...},
    "position_context": {...},
    "freeform_notes": {...},
}
```

Tutti i sotto-blocchi devono esistere sempre.
Se non applicabili:

- dict vuoto con shape prevista
- liste vuote
- campi `None`

Non si devono usare shape alternative per singolo trader.

## Struttura minima efficace consigliata

La proposta larga sopra resta utile come inventario concettuale, ma per implementazione pratica si raccomanda di usare questa struttura minima:

```python
instrument = {
    "symbol": str | None,
    "side": str | None,
    "market_type": str | None,
}

signal_payload_raw = {
    "entry_structure": str | None,
    "entries": list[dict],
    "stop_loss": dict | None,
    "take_profits": list[dict],
    "risk_hint": dict | None,
    "raw_fragments": dict,
}

update_payload_raw = {
    "operations": list[dict],
}

report_payload_raw = {
    "events": list[dict],
    "reported_result": dict | None,
    "notes": list[str],
}

targets_raw = [
    {
        "kind": str,
        "value": str | int | None,
    }
]
```

### Campi minimi da preservare

Nella struttura minima, ogni blocco deve comunque preservare questi campi:

```python
signal_payload_raw = {
    "entry_structure": str | None,
    "entries": [
        {
            "sequence": int,
            "entry_type": str | None,
            "price": float | None,
            "role": str | None,
            "size_hint": str | None,
            "is_optional": bool | None,
        }
    ],
    "stop_loss": {
        "price": float | None,
    } | None,
    "take_profits": [
        {
            "sequence": int,
            "price": float | None,
        }
    ],
    "risk_hint": {
        "value": float | None,
        "unit": str | None,
        "raw": str | None,
    } | None,
    "raw_fragments": dict,
}

update_payload_raw = {
    "operations": [
        {
            "op_type": str,
            "set_stop": {
                "target_type": str | None,
                "value": float | int | None,
            } | None,
            "close": {
                "close_fraction": float | None,
                "close_price": float | None,
                "close_scope": str | None,
            } | None,
            "cancel_pending": {
                "cancel_scope": str | None,
            } | None,
            "modify_entries": {
                "mode": str | None,
                "entries": list[dict],
            } | None,
            "modify_targets": {
                "mode": str | None,
                "take_profits": list[dict],
                "target_tp_level": int | None,
            } | None,
        }
    ]
}

report_payload_raw = {
    "events": [
        {
            "event_type": str,
            "level": int | None,
            "price": float | None,
            "result": {
                "value": float | None,
                "unit": str | None,
                "text": str | None,
            } | None,
        }
    ],
    "reported_result": {
        "value": float | None,
        "unit": str | None,
        "text": str | None,
    } | None,
    "notes": list[str],
}
```

### Nota

Questa struttura minima e quella raccomandata.

I blocchi piu ampi descritti dopo:

- `risk_plan`
- `position_context`
- `freeform_notes`
- dettagli extra di `instrument`

devono essere considerati opzionali di secondo livello e non necessari per la prima implementazione.

---

## Blocco `instrument`

Informazioni di base sul setup o sull'evento.

```python
instrument = {
    "symbol": str | None,
    "base_asset": str | None,
    "quote_asset": str | None,
    "side": str | None,              # LONG | SHORT
    "market_type": str | None,       # SPOT | FUTURES | UNKNOWN
    "exchange_hint": str | None,
}
```

### Regole

- `symbol` unico campo autorevole per lo strumento.
- `side` unico campo autorevole per la direzione.
- eventuali alias legacy devono essere convertiti qui.
- `base_asset`, `quote_asset`, `exchange_hint` non sono necessari nella prima implementazione.

---

## Blocco `entry_plan`

Questo e il blocco piu importante da unificare. Deve sostituire logicamente:

- `entry`
- `entries`
- `entry_plan_entries`
- `entry_plan_type`
- `entry_structure`
- `entry_order_type`
- `has_averaging_plan`

### Shape proposta

```python
entry_plan = {
    "plan_status": str | None,       # PRESENT | ABSENT | UNKNOWN
    "order_type": str | None,        # MARKET | LIMIT | RANGE | CURRENT | UNKNOWN
    "structure": str | None,         # ONE_SHOT | TWO_STEP | RANGE | LADDER | UNKNOWN
    "has_averaging_plan": bool | None,
    "source_text": str | None,
    "legs": [
        {
            "sequence": int,
            "role": str | None,      # PRIMARY | AVERAGING | RANGE_LOW | RANGE_HIGH | REENTRY | UNKNOWN
            "order_type": str | None,
            "price": float | None,
            "price_text": str | None,
            "size_hint": str | None,
            "fraction_hint": float | None,
            "is_optional": bool | None,
            "activation_hint": str | None,
        }
    ],
}
```

### Regole

- `entry_plan.legs` e l'unica fonte autorevole per le entry legs.
- `structure` deve essere coerente con `legs`.
- `entry` e `entries` non devono piu essere usati come shape primarie.
- `entry`, `entries`, `entry_plan_entries` diventano solo campi legacy di compatibilita durante la migrazione.
- per implementazione minima, basta:
  - `structure`
  - `legs`
  - `has_averaging_plan`

### Versione minima raccomandata

```python
entry_plan = {
    "structure": str | None,
    "has_averaging_plan": bool | None,
    "legs": [
        {
            "sequence": int,
            "role": str | None,
            "entry_type": str | None,
            "price": float | None,
            "size_hint": str | None,
            "is_optional": bool | None,
        }
    ],
}
```

### Campi che non devono essere persi

Anche nella versione minima devono restare:

- `size_hint`
- `role`
- `is_optional`

Questi campi sono utili per:

- distinguere `PRIMARY` vs `AVERAGING`
- preservare piani `1/3`, `2/3`, `3/3`
- supportare audit e normalizzazione futura piu ricca

### Mapping legacy

| Legacy | Nuovo |
|---|---|
| `entry` | `entry_plan.legs[*].price` solo come fallback |
| `entries` | `entry_plan.legs` |
| `entry_plan_entries` | `entry_plan.legs` |
| `entry_structure` | `entry_plan.structure` |
| `entry_order_type` | `entry_plan.order_type` |
| `has_averaging_plan` | `entry_plan.has_averaging_plan` |

---

## Blocco `stop_plan`

Unifica stop iniziale, stop update e stop target.

```python
stop_plan = {
    "initial_stop": {
        "price": float | None,
        "price_text": str | None,
        "kind": str | None,          # HARD_PRICE | UNKNOWN
    },
    "update_stop": {
        "target_type": str | None,   # ENTRY | BREAKEVEN | TP_LEVEL | PRICE | UNKNOWN
        "target_value": float | int | str | None,
        "price": float | None,
        "price_text": str | None,
    },
}
```

### Regole

- lo stop di un `SIGNAL` vive in `initial_stop`
- lo stop di un `UPDATE` vive in `update_stop`
- campi legacy tipo `new_stop_level`, `new_stop_price`, `stop_loss` vanno assorbiti qui
- per implementazione minima non serve distinguere due alberi complessi: basta un formato coerente per segnale e uno per update

### Versione minima raccomandata

```python
stop_plan = {
    "price": float | None,
    "target_type": str | None,
    "target_value": float | int | None,
}
```

### Casi da coprire esplicitamente

- stop iniziale del `SIGNAL`
- move stop a prezzo
- move stop a `ENTRY`
- move stop a `TP_LEVEL`

---

## Blocco `take_profit_plan`

Unifica target di segnale e update target.

```python
take_profit_plan = {
    "levels": [
        {
            "sequence": int,
            "price": float | None,
            "price_text": str | None,
            "role": str | None,      # PRIMARY | EXTENDED | UNKNOWN
            "rr_hint": float | None,
        }
    ],
    "update_mode": str | None,       # REPLACE_ALL | ADD | REMOVE | UNKNOWN
    "source_text": str | None,
}
```

### Regole

- `levels` e l'unica fonte autorevole dei take profit.
- `take_profits` legacy va convertito qui.
- update target non deve vivere in campi sparsi.

### Versione minima raccomandata

```python
take_profit_plan = {
    "levels": [
        {
            "sequence": int,
            "price": float | None,
        }
    ]
}
```

### Casi da coprire esplicitamente

- TP di segnale
- replace all target
- add target
- remove target puntuale
- update target puntuale se necessario dal trader legacy

---

## Blocco `update_plan`

Unifica tutte le operazioni update.

```python
update_plan = {
    "operations": [
        {
            "type": str,             # SET_STOP | CLOSE | CANCEL_PENDING | MODIFY_ENTRIES | MODIFY_TARGETS | MARK_FILLED | UNKNOWN
            "payload": dict,
            "source_intent": str | None,
        }
    ]
}
```

### Payload consigliati

#### `SET_STOP`

```python
{
    "target_type": "ENTRY | BREAKEVEN | TP_LEVEL | PRICE | UNKNOWN",
    "price": float | None,
    "tp_level": int | None,
}
```

#### `CLOSE`

```python
{
    "close_scope": "FULL | PARTIAL | UNKNOWN",
    "fraction": float | None,
    "price": float | None,
    "reason_hint": str | None,
}
```

#### `CANCEL_PENDING`

```python
{
    "cancel_scope": "ALL | ENTRY | TARGET | UNKNOWN",
    "target_leg_sequence": int | None,
}
```

#### `MODIFY_ENTRIES`

```python
{
    "mode": "ADD | REMOVE | REENTER | REPLACE_ALL | UNKNOWN",
    "legs": [],
}
```

#### `MODIFY_TARGETS`

```python
{
    "mode": "ADD | REMOVE | REPLACE_ALL | UNKNOWN",
    "levels": [],
}
```

### Regole

- `update_plan.operations` e l'unica fonte autorevole per update operativi.
- `actions_structured` non deve piu essere usato come contratto primario.
- `actions_structured` puo essere derivato, non prodotto nativamente dai parser.
- per implementazione minima, ogni operation deve essere gia vicina a `UpdateOperation` del canonical finale

### Versione minima raccomandata

```python
update_plan = {
    "operations": [
        {
            "op_type": str,
            "set_stop": dict | None,
            "close": dict | None,
            "cancel_pending": dict | None,
            "modify_entries": dict | None,
            "modify_targets": dict | None,
        }
    ]
}
```

### Eventi update che il contratto minimo deve coprire

- `U_MOVE_STOP`
- `U_MOVE_STOP_TO_BE`
- `U_CLOSE_FULL`
- `U_CLOSE_PARTIAL`
- `U_CANCEL_PENDING_ORDERS`
- `U_REMOVE_PENDING_ENTRY`
- `U_REENTER`
- `U_UPDATE_TAKE_PROFITS`

### Nota su `CLOSE PARTIAL`

`close_fraction` e un campo minimo da preservare.

Non va spostato in `diagnostics`, perche ha semantica operativa diretta e nel canonical finale mappa su:

- `UpdateOperation(op_type="CLOSE", close.close_fraction=...)`

---

## Blocco `report_payload`

Unifica eventi report e risultati finali.

```python
report_payload = {
    "events": [
        {
            "event_type": str,       # TP_HIT | STOP_HIT | BREAKEVEN_EXIT | ENTRY_FILLED | FINAL_RESULT | UNKNOWN
            "target_level": int | None,
            "price": float | None,
            "fraction": float | None,
            "value": float | None,
            "unit": str | None,      # RR | PERCENT | USDT | UNKNOWN
            "notes": str | None,
        }
    ],
    "final_result": {
        "value": float | None,
        "unit": str | None,
        "leverage_hint": str | None,
    }
}
```

### Regole

- `report_payload.events` e la fonte autorevole per report event.
- `reported_results` legacy va convertito qui.
- intent tipo `U_TP_HIT`, `U_STOP_HIT`, `U_EXIT_BE` devono essere coerenti con questo blocco.

### Versione minima raccomandata

```python
report_payload = {
    "events": [
        {
            "event_type": str,
            "level": int | None,
            "price": float | None,
            "result": dict | None,
        }
    ],
    "reported_result": dict | None,
}
```

### Eventi report che il contratto minimo deve coprire

- `ENTRY_FILLED`
- `TP_HIT`
- `STOP_HIT`
- `BREAKEVEN_EXIT`
- `FINAL_RESULT`

### Nota su `reported_result`

`reported_result` non va perso nella semplificazione.

Serve per:

- `U_REPORT_FINAL_RESULT`
- casi compositi `UPDATE + REPORT`
- audit dei risultati riportati dal trader

---

## Blocco `risk_plan`

```python
risk_plan = {
    "risk_percent": float | None,
    "risk_text": str | None,
    "leverage_hint": str | None,
    "deposit_percent": float | None,
}
```

### Regole

- tutti i campi rischio devono convergere qui
- niente duplicazione tra `risk_hint`, `risk_value_raw`, `risk_value_normalized`

---

## Blocco `position_context`

Serve per i casi update/report dove il messaggio descrive stato posizione.

```python
position_context = {
    "reply_ref_present": bool | None,
    "activation_status": str | None,     # NOT_FILLED | PARTIAL_FILLED | FILLED | UNKNOWN
    "position_state_hint": str | None,   # OPEN | CLOSED | PARTIAL | UNKNOWN
    "is_reentry_context": bool | None,
}
```

---

## Blocco `freeform_notes`

Per non perdere informazione non ancora strutturata.

```python
freeform_notes = {
    "raw_fragments": list[str],
    "admin_notes": list[str],
    "parser_notes": list[str],
}
```

### Regole

- tutto cio che non e ancora modellato ma puo servire al debug va qui
- non si devono creare nuovi campi ad hoc sparsi nel payload

---

## Blocco `raw_targets`

Unifica targeting e riferimenti.

```python
raw_targets = [
    {
        "kind": str,                   # REPLY | TELEGRAM_LINK | MESSAGE_ID | SYMBOL | UNKNOWN
        "value": str | int | None,
        "strength": str | None,        # STRONG | WEAK
    }
]
```

### Regole

- `target_refs` legacy converge qui
- `target_scope` non deve essere deciso dal parser in forma finale
- il normalizer costruisce poi il `Targeting` canonico

---

## Campi legacy da deprecare

Questi campi non devono piu essere shape autorevoli nel nuovo codice:

- `entities.entry`
- `entities.entries`
- `entities.entry_plan_entries`
- `entities.entry_structure`
- `entities.entry_plan_type`
- `entities.entry_order_type`
- `entities.take_profits`
- `entities.new_stop_level`
- `entities.new_stop_price`
- `reported_results`
- `actions_structured`
- `target_scope`
- `linking`

Possono restare solo durante la migrazione, ma devono essere derivati o adattati verso i nuovi blocchi.

---

## Regole di compatibilita durante la migrazione

Per evitare rotture immediate:

1. il normalizer deve prima leggere il nuovo schema unico;
2. se il nuovo schema non c'e ancora, puo usare un adapter legacy -> nuovo schema;
3. i parser migrati non devono piu popolare i campi legacy come source of truth.

### Ordine di precedenza consigliato

Per ogni blocco:

1. nuovo blocco v1 unico
2. adapter legacy temporaneo
3. nessun fallback addizionale implicito

Esempio entry:

1. `raw_event.entry_plan.legs`
2. adapter da `entry_plan_entries`
3. adapter da `entries`
4. adapter da `entry`

Questa precedenza deve stare in un solo posto, non sparsa nei parser.

---

## Proposta concreta per il dataclass base

### Stato attuale

`TraderParseResult` oggi e:

```python
@dataclass(slots=True)
class TraderParseResult:
    message_type: str
    intents: list[str]
    entities: dict[str, Any]
    target_refs: list[dict[str, Any]]
    reported_results: list[dict[str, Any]]
    warnings: list[str]
    confidence: float
    primary_intent: str | None
    actions_structured: list[dict[str, Any]]
    target_scope: dict[str, Any]
    linking: dict[str, Any]
    diagnostics: dict[str, Any]
```

### Proposta

Introdurre una nuova shape additive:

```python
@dataclass(slots=True)
class TraderParseResultV2:
    schema_version: str
    message_type_hint: str
    intents_detected: list[str]
    primary_intent_hint: str | None
    raw_event: dict[str, Any]
    raw_targets: list[dict[str, Any]]
    raw_report_events: list[dict[str, Any]]
    warnings: list[str]
    confidence: float
    diagnostics: dict[str, Any]
```

### Strategia pragmatica

Per minimizzare il rischio:

1. mantenere `TraderParseResult` attuale
2. aggiungere dentro `entities_v2` oppure `raw_event`
3. aggiornare il normalizer a preferire V2
4. migrare parser trader uno per volta
5. rimuovere legacy quando tutti i trader sono allineati

---

## Criteri di accettazione dello schema unico

La proposta e accettabile se:

1. tutti i parser possono emettere la stessa shape senza campi ad hoc extra;
2. `entry` ha una sola fonte autorevole;
3. `update` ha una sola fonte autorevole;
4. `report` ha una sola fonte autorevole;
5. il normalizer puo leggere il nuovo schema senza fallback trader-specifici;
6. i campi opzionali non costringono parser semplici a inventare dati;
7. il contratto copre `SIGNAL`, `UPDATE`, `REPORT`, `INFO`, `UNCLASSIFIED`.

---

## Primo sotto-scope consigliato

Per ridurre rischio e diff:

### Step 1

Unificare solo questi blocchi:

- `instrument`
- `entry_plan`
- `stop_plan`
- `take_profit_plan`
- usare gia la shape minima vicina al canonical finale
- non introdurre blocchi top-level extra oltre quelli strettamente necessari

### Step 2

Poi:

- `update_plan`
- `report_payload`
- `raw_targets`
- includendo esplicitamente:
  - `close_fraction`
  - `reported_result`
  - `price/level` degli eventi report

### Step 3

Infine:

- pulizia legacy
- rimozione fallback
- persistenza primaria del nuovo envelope

---

## Decisioni raccomandate

### Decisione A

`entry_plan.legs` diventa la sola fonte autorevole per le entry.

### Decisione B

`update_plan.operations` diventa la sola fonte autorevole per update operativi.

### Decisione C

`report_payload.events` diventa la sola fonte autorevole per report ed esiti.

### Decisione D

`raw_targets` sostituisce `target_refs` come contratto parser-side uniforme.

### Decisione E

Il parser trader-specifico produce solo estrazione standardizzata; la semantica finale resta al normalizer.

### Decisione F

La prima implementazione deve usare la struttura minima efficace, non la versione larga.

### Decisione G

Nessun nuovo campo top-level oltre:

- `instrument`
- `signal_payload_raw`
- `update_payload_raw`
- `report_payload_raw`
- `targets_raw`

### Decisione H

La versione minima deve preservare tutti i campi con semantica operativa o di audit reale, anche se opzionali.

### Decisione I

I campi da comprimere sono i contenitori e le duplicazioni, non:

- `risk_hint`
- `size_hint`
- `close_fraction`
- `reported_result`
- `price/level` di stop e report

---

## Implicazioni sul lavoro in corso

Questa proposta non sostituisce `CanonicalMessage v1`.
Lo precede.

Sequenza corretta:

```text
trader profile
    -> TraderEventEnvelopeV1
    -> CanonicalNormalizer
    -> CanonicalMessage v1
```

Quindi:

- `CanonicalMessage v1` resta il contratto downstream
- questo documento definisce il contratto parser-side uniforme upstream

Sono due livelli diversi e complementari.

---

## Messaggio architetturale finale

Il repository ha bisogno di un solo contratto di estrazione parser-side, non di decine di shape legacy compatibili tra loro.

Se non introduciamo questo strato unico:

- continueremo ad aggiungere fallback
- i bug si sposteranno da un punto all'altro
- ogni nuovo trader aumentera il costo del bridge

Se invece lo introduciamo:

- i parser diventano piu semplici
- il normalizer diventa piu affidabile
- il routing e i report diventano stabili
- la migrazione a `CanonicalMessage v1` diventa controllabile
