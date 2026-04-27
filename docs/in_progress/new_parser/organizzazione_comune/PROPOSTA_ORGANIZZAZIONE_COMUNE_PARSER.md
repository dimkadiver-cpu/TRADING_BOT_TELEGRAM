# Proposta Organizzazione Comune Parser

Data: 2026-04-25  
Stato: proposta architetturale operativa

## Obiettivo

Portare tutti i parser trader a:

- una sola forma di output parser-side
- una sola tassonomia di intent
- un solo vocabolario di entity raw
- una sola responsabilita per ogni layer

Anche se non tutti i trader valorizzano tutti i campi, la shape deve restare fissa.

## Risposta sintetica

La struttura comune da usare per tutti non dovrebbe essere `TraderParseResult`.

La struttura comune giusta e:

- `TraderEventEnvelopeV1` come output unico parser-side
- `CanonicalMessage` come output unico business/downstream

Quindi la direzione proposta e:

```text
text + ParserContext
  -> profile parser trader-specifico
  -> TraderEventEnvelopeV1
  -> normalizer centrale
  -> CanonicalMessage
```

## Stato attuale reale

Oggi il codice e coerente solo a valle:

- tutti i profili espongono `parse_message(...)`
- quasi tutti costruiscono anche `parse_canonical(...)`
- esiste un adapter centrale verso `TraderEventEnvelopeV1`
- esiste un normalizer centrale verso `CanonicalMessage`

Ma a monte i profili divergono in modo strutturale:

- JSON di regole con shape e responsabilita non uniformi
- chiavi `entities` diverse per concetti simili
- logica duplicata per `warnings`, `primary_intent`, `actions_structured`, `target_scope`, `linking`
- parte della semantica business vive nei profili invece che nel layer shared

## Root Cause

Il contratto unico oggi sta troppo tardi nel flusso.

Hai:

1. profili che emettono shape semi-libere
2. bridge che prova a normalizzare
3. canonical finale che ricompone altra semantica

Questo crea drift fra:

- `profile.py`
- `parsing_rules.json`
- `parse_canonical(...)`
- `actions_structured`
- `target_scope` / `linking`

## Decisione architetturale proposta

### Decisione 1

Congelare `TraderEventEnvelopeV1` come unico output parser-side ufficiale.

### Decisione 2

Centralizzare la costruzione di `CanonicalMessage` fuori dai profili.

### Decisione 3

Ridurre `profile.py` a orchestration + extraction locale, non business mapping.

### Decisione 4

Usare un vocabolario unico di intent e un vocabolario unico di entity raw.

### Decisione 5

Trattare `signal_payload_raw`, `update_payload_raw` e `report_payload_raw` come blocchi strutturati con sottoshape stabili, non come semplici dizionari flat di key/value.

## Struttura target dei layer

### Layer 1: Profile parser trader-specifico

Responsabilita:

- preprocess testo
- classify messaggio
- detect intent raw
- extract dati raw dal testo
- estrarre target refs
- warnings locali minime

Non deve fare:

- costruzione di `CanonicalMessage`
- semantica operativa finale
- targeting finale business
- action mapping definitivo
- logica duplicata rispetto al normalizer

### Layer 2: Envelope builder shared

Responsabilita:

- costruire `TraderEventEnvelopeV1`
- applicare shape comune
- applicare precedenze comuni
- normalizzare side, market type, entry shape, result unit
- assemblare sottoshape stabili come `EntryLegRaw`, `StopUpdateRaw`, `CloseUpdateRaw`, `ReportEventRaw`
- isolare i residui trader-specifici in `diagnostics`

### Layer 3: Normalizer shared

Responsabilita:

- tradurre envelope in `CanonicalMessage`
- classificare `primary_class`
- riempire signal/update/report finali
- costruire targeting finale

Regola:

- il normalizer legge blocchi raw gia strutturati; non deve dipendere da chiavi flat legacy come contratto target

### Layer 4: Downstream business

Responsabilita:

- operation rules
- target resolver
- execution

Downstream deve leggere solo `CanonicalMessage`.

## Contratto comune consigliato

### Output parser-side unico

Tutti i profili devono emettere sempre questa shape:

```text
TraderEventEnvelopeV1
  schema_version
  message_type_hint
  intents_detected
  primary_intent_hint
  instrument
  signal_payload_raw
  update_payload_raw
  report_payload_raw
  targets_raw
  warnings
  confidence
  diagnostics
```

Regola:

- tutti i blocchi esistono sempre
- i campi non disponibili restano `null`, `[]` o `{}`
- nessun profilo aggiunge nuovi campi top-level fuori dal contratto

### Policy multi-intent nel parser envelope

Il parser puo rilevare piu intent compatibili nello stesso messaggio.

Regole:

- `intents_detected` puo contenere piu intent
- `primary_intent_hint` e solo un hint di priorita, non una decisione esecutiva finale
- il parser non deve forzare artificialmente un solo intent se il messaggio ne contiene piu di uno
- la compatibilita tra intent, le esclusioni mutue e le precedenze per `primary_intent_hint` devono vivere in una source of truth shared unica
- la source of truth proposta e `shared/intent_taxonomy.py`, non i singoli `parsing_rules.json`
- il parser non produce comandi o azioni di esecuzione
- la trasformazione `intent -> operazioni` non appartiene al parser, ma a layer successivi
- `parser_event_envelope_v1` deve restare un contratto semantico di parsing, non un contratto di execution

Esempio:

```json
{
  "message_type_hint": "UPDATE",
  "intents_detected": ["CLOSE_PARTIAL", "MOVE_STOP"],
  "primary_intent_hint": "CLOSE_PARTIAL",
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
  }
}
```

### Output business unico

Tutti i casi downstream devono usare:

- `CanonicalMessage`

## Tassonomia comune degli intent

Set base comune:

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

Regole:

- gli intent devono vivere in un file shared ufficiale
- i profili possono usare solo intent presenti in quella tassonomia
- nuovi intent si aggiungono solo nel layer shared, non localmente nel singolo profilo
- alias come `U_TP_HIT_EXPLICIT` vanno eliminati o ricondotti a intent ufficiali
- `NEW_SETUP` non va estratto con marker propri: e implicito quando `message_type = NEW_SIGNAL`
- `INFO_ONLY` non va estratto con marker propri: e implicito quando `message_type = INFO_ONLY`
- `intent_markers` deve restare riservato agli intent operativi reali, in pratica soprattutto ai messaggi `UPDATE`
- `classification_markers` e `intent_markers` non sono la stessa cosa
- `intents_detected` puo contenere piu intent compatibili nello stesso messaggio
- `classification_markers.new_signal` risponde a "questo messaggio sembra un nuovo segnale?"
- `intent_markers.MOVE_STOP_TO_BE` risponde a "questo messaggio contiene questa specifica azione/update?"
- il `primary_intent` va derivato in modo shared:
- se `message_type = NEW_SIGNAL` -> `primary_intent = NEW_SETUP`
- se `message_type = INFO_ONLY` -> `primary_intent = INFO_ONLY`
- se `message_type = UPDATE` -> `primary_intent` si risolve da `intent_markers` + disambiguation + context rules
- `primary_intent` in parser/envelope e solo un hint di priorita semantica, non un action command

## Vocabolario comune delle entity raw

I profili possono valorizzare solo chiavi appartenenti al vocabolario comune.  
Il vocabolario corrente non e piu flat: e organizzato per sottostrutture stabili dentro `signal_payload_raw`, `update_payload_raw` e `report_payload_raw`.

### Instrument raw

- `symbol`
- `side`
- `market_type`

### Signal raw

- `entry_structure`
- `entries`
- `stop_loss`
- `take_profits`
- `leverage_hint`
- `risk_hint`
- `invalidation_rule`
- `conditions`
- `raw_fragments`

#### Signal sub-shapes

- `EntryLegRaw`
- `SizeHintRaw`
- `StopLossRaw`
- `TakeProfitRaw`
- `RiskHintRaw`
- `SignalRawFragments`

#### Signal raw fragments

- `entry_text_raw`
- `stop_text_raw`
- `take_profits_text_raw`

### Update raw

- `stop_update`
- `close_update`
- `cancel_update`
- `entry_update`
- `targets_update`
- `raw_fragments`

#### Update sub-shapes

- `StopUpdateRaw`
- `CloseUpdateRaw`
- `CancelUpdateRaw`
- `EntryUpdateRaw`
- `TargetsUpdateRaw`
- `UpdateRawFragments`

#### Stop update

- `mode`
- `price`
- `reference_level`
- `raw`

#### Close update

- `close_fraction`
- `close_percent`
- `close_price`
- `close_scope`
- `raw`

#### Cancel update

- `cancel_scope`
- `raw`

#### Entry update

- `mode`
- `entries`
- `raw`

#### Targets update

- `mode`
- `target_level`
- `take_profits`
- `raw`

#### Update raw fragments

- `stop_text_raw`
- `close_text_raw`
- `cancel_text_raw`
- `entry_text_raw`
- `targets_text_raw`

### Report raw

- `events`
- `reported_results`
- `notes`
- `summary_text_raw`

#### Report sub-shapes

- `ReportEventRaw`
- `ReportedResultRaw`

#### Report event

- `event_type`
- `level`
- `price`
- `result`
- `raw_fragment`

#### Report result

- `value`
- `unit`
- `text`

### Target raw

- `kind`
- `value`

### Meta residuale

Tutto quello che non rientra nel vocabolario comune:

- non va in top-level entity raw
- non apre nuove chiavi locali nel payload comune
- va in `diagnostics.legacy_*`

## Regole di precedenza comuni

### Entry

1. `entry_structure`
2. `entries`

### Side

1. `side`

### Stop update

1. `stop_update.mode`
2. `stop_update.price`
3. `stop_update.reference_level`

### Close update

1. `close_update.close_scope`
2. `close_update.close_fraction`
3. `close_update.close_percent`
4. `close_update.close_price`

### Cancel update

1. `cancel_update.cancel_scope`

### Targets update

1. `targets_update.mode`
2. `targets_update.take_profits`

### Report result

1. `reported_results[0]`
2. `events[*].result`
3. nessun altro fallback implicito forte

## Struttura comune dei file

Target consigliato:

```text
src/parser/
  event_envelope_v1.py
  adapters/
    legacy_to_event_envelope_v1.py
  canonical_v1/
    models.py
    normalizer.py
  trader_profiles/
    base.py
    shared/
      intent_taxonomy.py
      entity_keys.py
      envelope_builder.py
      profile_runtime.py
      targeting.py
      warnings.py
      rules_schema.json
    trader_a/
      profile.py
      extractors.py
      parsing_rules.json
      tests/
    trader_b/
      profile.py
      extractors.py
      parsing_rules.json
      tests/
    trader_c/
      profile.py
      extractors.py
      parsing_rules.json
      tests/
    trader_d/
      profile.py
      extractors.py
      parsing_rules.json
      tests/
    trader_3/
      profile.py
      extractors.py
      parsing_rules.json
      tests/
```

## Organizzazione comune di `profile.py`

Ogni profilo dovrebbe avere lo stesso scheletro:

```python
class TraderXProfileParser:
    trader_code = "trader_x"

    def parse_message(self, text: str, context: ParserContext) -> TraderEventEnvelopeV1:
        prepared = self._preprocess(text=text, context=context)
        classification = self._classify(prepared=prepared)
        intents = self._detect_intents(prepared=prepared, classification=classification)
        entities = self._extract_entities(prepared=prepared, classification=classification, intents=intents)
        target_refs = self._extract_targets(prepared=prepared, context=context, entities=entities)
        warnings = self._build_warnings(prepared=prepared, classification=classification, intents=intents, entities=entities, target_refs=target_refs)
        return build_event_envelope_v1(
            trader_code=self.trader_code,
            classification=classification,
            intents=intents,
            entities=entities,
            target_refs=target_refs,
            warnings=warnings,
            diagnostics=self._build_diagnostics(prepared=prepared, entities=entities),
        )
```

Regola importante:

- `parse_canonical()` non deve piu stare nei profili

## Organizzazione comune di `extractors.py`

`extractors.py` deve contenere solo:

- regex trader-specifiche
- parsing testuale locale
- helper locali per estrarre pezzi di dato grezzo
- costruzione di frammenti strutturati parser-side, ad esempio `EntryLegRaw`, `TakeProfitRaw`, `ReportEventRaw`
- parsing risultati (`R`, `%`, `x`, summary blocks)
- parsing entity non banali o multi-step
- marker di supporto all'estrazione quando servono fallback locali, salvo quelli che si vogliono rendere configurabili nel `parsing_rules.json`

Non deve contenere:

- mapping intent -> operation
- costruzione `CanonicalMessage`
- logica di targeting business
- contratto comune delle entity

Regola:

- l'output di `extractors.py` puo essere ancora incompleto, ma deve tendere a produrre blocchi raw gia tipizzati/strutturati, non solo campi flat sparsi

## Ownership strutturata dei moduli shared

Con la shape corrente del contratto parser-side, i moduli shared vanno intesi cosi:

### `shared/intent_taxonomy.py`

Responsabilita:

- definire la tassonomia canonica degli intent parser-side
- definire gli alias legacy -> intent canonici, quando servono in migrazione
- definire la matrice di compatibilita tra intent
- definire gli intent mutuamente esclusivi
- definire le precedenze usate per valorizzare `primary_intent_hint`

Nota:

- `allow_multi_intent` nei `parsing_rules.json` abilita solo la conservazione di piu intent
- non decide quali intent sono compatibili tra loro
- quella decisione deve vivere in `shared/intent_taxonomy.py`

### `shared/entity_keys.py`

Responsabilita:

- definire il vocabolario ufficiale delle sottoshape raw
- documentare i blocchi ammessi dentro `signal_payload_raw`, `update_payload_raw`, `report_payload_raw`
- definire i nomi canonici dei campi interni, non solo un elenco flat di chiavi

Esempi:

- `EntryLegRaw`
- `SizeHintRaw`
- `StopLossRaw`
- `TakeProfitRaw`
- `StopUpdateRaw`
- `CloseUpdateRaw`
- `CancelUpdateRaw`
- `EntryUpdateRaw`
- `TargetsUpdateRaw`
- `ReportEventRaw`
- `ReportedResultRaw`

### `shared/envelope_builder.py`

Responsabilita:

- assemblare i blocchi top-level dell'envelope
- comporre le sottoshape raw a partire dai frammenti estratti dal profilo
- garantire che `signal_payload_raw`, `update_payload_raw` e `report_payload_raw` abbiano sempre la shape congelata
- spostare in `diagnostics` tutto quello che non entra nel contratto comune

Non deve fare:

- action mapping
- business execution semantics
- interpretazioni downstream specifiche del `CanonicalMessage`

### `shared/profile_runtime.py`

Responsabilita:

- orchestrare il flusso parser-side comune
- coordinare classificazione, intent detection, extraction, targeting e warnings
- passare all'envelope builder frammenti raw strutturabili per blocco, non un contenitore semi-libero
- invocare il mini engine shared di risoluzione contestuale dopo detection e disambiguazione lessicale

Regola:

- il runtime shared deve ragionare per blocchi (`instrument`, `signal`, `update`, `report`, `targets`) e non come semplice accumulatore di entity flat

## Mini engine di risoluzione contestuale

Per usare davvero `context_resolution_rules` in modo coerente serve un mini engine shared, piccolo e deterministico.

Non e un layer di execution.
E un resolver parser-side che rifinisce classificazione e intent quando il testo puro non basta.

### Posizionamento nel flusso

Ordine consigliato:

1. `classification_markers` e `field_markers`
2. `classification_rules` e `combination_rules`
3. `intent_markers`
4. `disambiguation_rules`
5. mini engine `context_resolution_rules`
6. `intents_detected`, `primary_intent_hint`, payload raw finale

### Input minimi

Il mini engine deve ricevere almeno:

- `message_type_hint` candidato
- intent candidati con forza `strong` o `weak`
- marker lessicali trovati
- `target_ref` risolto o meno
- `target_markers` e scope globale, se presenti
- storico minimo del target, se disponibile
- ultimo stato semantico noto del segnale target, se disponibile

### Operazioni supportate

Il mini engine deve supportare operazioni semplici e dichiarative:

- `resolve_as`
- `prefer`
- `suppress`
- `promote_weak_to_final`
- `set_primary`

### Ruolo di `context_resolution_rules`

`context_resolution_rules` non serve solo come fallback in assenza di marker `strong`.

Serve anche per:

- promuovere un intent weak quando il contesto lo rende affidabile
- risolvere ambiguita tra intent gia candidati
- sopprimere un intent che il contesto contraddice
- aiutare nei casi borderline tra `UPDATE` e `REPORT`
- completare la classificazione quando esistono solo segnali weak coerenti

### Vincoli

Regole:

- il mini engine non deve inventare intent senza alcun segnale testuale minimo
- non deve contenere logica di execution
- non deve sostituire il motore base di detection lessicale
- deve partire da candidati gia emersi dal testo o da evidenza strutturale minima

### Shape target di una regola contestuale

```json
{
  "name": "weak_be_becomes_exit_be",
  "when": {
    "has_weak_intent": "EXIT_BE",
    "has_target_ref": true,
    "has_no_strong_intent": "MOVE_STOP"
  },
  "if_target_history_has_any": ["NEW_SETUP", "MOVE_STOP_TO_BE"],
  "resolve_as": "EXIT_BE"
}
```

### Ownership

- `parsing_rules.json` contiene le regole dichiarative
- `shared/intent_taxonomy.py` definisce compatibilita, esclusioni e precedenze
- `shared/profile_runtime.py` invoca il resolver nel punto giusto del flusso
- l'implementazione del mini engine puo stare in `shared/profile_runtime.py` o in un modulo dedicato come `shared/context_resolution.py`

## Schema comune per `parsing_rules.json`

Tutti i profili devono usare lo stesso schema dati:

```json
{
  "profile_meta": {
    "trader_code": "trader_x",
    "language": "ru",
    "schema_version": "parser_profile_rules_v1"
  },
  "number_format": {
    "decimal_separator": ".",
    "thousands_separator": " "
  },
  "classification_markers": {
    "new_signal": { "strong": [], "weak": [] },
    "update": { "strong": [], "weak": [] },
    "info_only": { "strong": [], "weak": [] }
  },
  "field_markers": {
    "entry": { "strong": [], "weak": [] },
    "stop_loss": { "strong": [], "weak": [] },
    "take_profit": { "strong": [], "weak": [] }
  },
  "classification_rules": [],
  "combination_rules": [],
  "intent_markers": {
    "MOVE_STOP_TO_BE": { "strong": [], "weak": [] },
    "MOVE_STOP": { "strong": [], "weak": [] },
    "CLOSE_FULL": { "strong": [], "weak": [] },
    "CLOSE_PARTIAL": { "strong": [], "weak": [] },
    "CANCEL_PENDING_ORDERS": { "strong": [], "weak": [] },
    "INVALIDATE_SETUP": { "strong": [], "weak": [] },
    "REENTER": { "strong": [], "weak": [] },
    "ADD_ENTRY": { "strong": [], "weak": [] },
    "UPDATE_TAKE_PROFITS": { "strong": [], "weak": [] },
    "ENTRY_FILLED": { "strong": [], "weak": [] },
    "TP_HIT": { "strong": [], "weak": [] },
    "SL_HIT": { "strong": [], "weak": [] },
    "EXIT_BE": { "strong": [], "weak": [] },
    "REPORT_FINAL_RESULT": { "strong": [], "weak": [] },
    "REPORT_PARTIAL_RESULT": { "strong": [], "weak": [] }
  },
  "side_markers": { "long": [], "short": [] },
  "market_context_markers": { "spot": [], "futures": [], "margin": [] },
  "entry_order_markers": { "market": [], "limit": [] },
  "target_markers": {
    "strong": {
      "telegram_link": [],
      "explicit_id": [],
      "global_scope": {
        "ALL_LONGS": [],
        "ALL_SHORTS": [],
        "ALL_POSITIONS": [],
        "ALL_OPEN": [],
        "ALL_REMAINING": []
      }
    },
    "weak": {
      "pronouns": []
    }
  },
  "target_ref_markers": {
    "strong": {
      "telegram_link": "t\\.me/",
      "explicit_id": []
    },
    "weak": {
      "pronouns": []
    }
  },
  "entity_hints": {
    "market_type_default": "UNKNOWN",
    "entry_type_default": "LIMIT"
  },
  "symbol_aliases": {},
  "ignore_markers": [],
  "global_target_markers": {
    "ALL_LONGS": [],
    "ALL_SHORTS": [],
    "ALL_POSITIONS": [],
    "ALL_OPEN": [],
    "ALL_REMAINING": []
  },
  "action_scope_groups": {
    "all_positions": ["ALL_POSITIONS", "ALL_OPEN", "ALL_REMAINING"],
    "all_long": ["ALL_LONGS"],
    "all_short": ["ALL_SHORTS"]
  },
  "partial_exit_markers": [],
  "final_exit_markers": [],
  "result_markers": [],
  "disambiguation_rules": { },
  "context_resolution_rules": [],
  "blacklist": []
}
```

Regole:

- niente shape custom per singolo profilo
- niente intent non ufficiali
- niente chiavi business nuove senza allineamento shared
- la shape di `target_markers` e congelata nella forma `strong/weak`, con `global_scope` sotto `strong`
- `target_ref_markers` resta separato come compat/bridge legacy
- `NEW_SETUP` e `INFO_ONLY` restano nella tassonomia finale, ma sono intent derivati dal `message_type`, non intent da estrarre via marker
- `intent_markers` non deve duplicare la classificazione del messaggio
- `parsing_rules.json` deve contenere marker, vocabolari, hint e regole dichiarative
- regex di estrazione complesse e parsing risultati multi-step stanno meglio in `extractors.py`
- `entity_patterns` e `result_patterns` non fanno parte della shape congelata del `parsing_rules.json`
- marker lessicali trader-specifici come `partial_exit_markers` / `final_exit_markers` / `result_markers` possono restare nel `parsing_rules.json`
- `action_scope_groups` puo restare nel `parsing_rules.json` se rappresenta un grouping dichiarativo che puo variare per profilo; se diventa uguale per tutti, allora va spostato nel layer shared

## Responsabilita da centralizzare

Queste responsabilita oggi sono sparse e vanno centralizzate:

- `primary_intent`
- `actions_structured`
- `target_scope`
- `linking`
- mapping `intent -> update/report operation`
- `parse_canonical`

### Ownership proposta

- `primary_intent`: shared helper
- `actions_structured`: derivato shared o rimosso
- `target_scope`: normalizer / targeting shared
- `linking`: normalizer / targeting shared
- `parse_canonical`: rimosso dai profili, sostituito da normalizer centrale

## Due opzioni

### Opzione A

Tenere `TraderParseResult` come output comune e irrigidirlo.

Pro:

- diff iniziale piu piccolo

Contro:

- mantieni un contenitore ambiguo
- continui ad avere doppia semantica con envelope e canonical
- il drift rimane probabile

### Opzione B

Promuovere `TraderEventEnvelopeV1` a unico output parser-side.

Pro:

- forma fissa
- bridge gia esistente
- tollera campi non valorizzati
- piu vicino al modello finale
- elimina una grossa parte della liberta attuale

Contro:

- richiede migrazione controllata dei profili

Raccomandazione:

- scegliere Opzione B

## Piano di migrazione pragmatico

### Fase 1

Congelare la shape e la tassonomia shared:

- `TraderEventEnvelopeV1`
- lista ufficiale intent
- vocabolario ufficiale entity raw

### Fase 2

Introdurre helper shared:

- `intent_taxonomy.py`
  Source of truth per tassonomia intent, compatibilita, esclusioni e precedenze
- `entity_keys.py`
- `envelope_builder.py`
- `profile_runtime.py`

### Fase 3

Portare tutti i profili a una stessa interfaccia:

- `parse_message(...) -> TraderEventEnvelopeV1`

### Fase 4

Spostare fuori dai profili:

- `parse_canonical`
- `actions_structured`
- `target_scope`
- `linking`

### Fase 5

Tenere un compat layer temporaneo:

- se serve, convertire envelope -> vecchio payload per il router legacy

### Fase 6

Aggiornare router e downstream per usare solo:

- envelope parser-side
- canonical downstream

## Implicazioni sui profili attuali

### trader_a

- va alleggerito molto
- contiene troppa semantica duplicata

### trader_b

- e il miglior candidato per diventare base runtime shared
- oggi e gia pseudo-base class per altri

### trader_c

- deve perdere la doppia ownership su update/report canonical

### trader_d

- deve smettere di postprocessare shape business oltre l’estrazione

### trader_3

- ha forma messaggio diversa, ma puo usare la stessa envelope shape senza problemi

## Acceptance Contract

La migrazione e da considerare chiusa quando:

- tutti i profili emettono `TraderEventEnvelopeV1`
- nessun profilo costruisce `CanonicalMessage` direttamente
- tutti i `parsing_rules.json` validano contro lo stesso schema
- nessun intent locale esiste fuori dalla tassonomia shared
- i test cross-profile confrontano la stessa shape envelope

## Primary signal

Un messaggio parsato da qualsiasi trader produce sempre lo stesso scheletro top-level.

## Secondary signals

- test schema `parsing_rules.json`
- test envelope per profilo
- test normalizer cross-profile
- audit sui campi `diagnostics.legacy_*`

## Conclusione

Se il tuo obiettivo e "unica struttura per tutti, anche con campi vuoti", la scelta corretta e:

- forma unica parser-side: `TraderEventEnvelopeV1`
- forma unica business: `CanonicalMessage`
- profili ridotti a extraction layer
- niente logica canonical dentro `profile.py`

Questa e la soluzione con il footprint piu pulito e con meno drift futuro.
