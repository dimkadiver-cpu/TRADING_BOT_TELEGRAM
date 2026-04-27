# TRADING_BOT_TELEGRAM — layer reali ricostruiti dal codice attuale

Data analisi: 2026-04-24

## Metodo usato

Questa ricostruzione **non** è basata sul README come fonte primaria.  
È stata ricavata leggendo i file runtime reali del repository, in particolare:

- `main.py`
- `src/telegram/listener.py`
- `src/telegram/router.py`
- `src/telegram/effective_trader.py`
- `src/telegram/eligibility.py`
- `src/telegram/ingestion.py`
- `src/parser/trader_profiles/*`
- `src/parser/intent_action_map.py`
- `src/validation/coherence.py`
- `src/operation_rules/engine.py`
- `src/target_resolver/resolver.py`
- `src/storage/*`
- `src/execution/*`

---

## 1. Obiettivo di questo documento

Questo file serve a descrivere **la struttura reale del codice oggi**, con focus su:

- layer runtime effettivi
- input/output di ogni layer
- tabelle DB toccate
- classificazioni e stati usati dal sistema
- eventi che il sistema registra
- differenza tra flusso live principale e sottosistemi paralleli

Non è una specifica ideale/futura.  
È una **ricostruzione aderente al codice attuale**.

---

## 2. Vista ad alto livello reale

Nel repository attuale si vedono chiaramente questi blocchi principali:

- `main.py` → entrypoint runtime del bot
- `config/` → config operative, mapping, rules
- `db/` → migrazioni/schema DB
- `src/core/` → bootstrap, config loader, logging, utilità
- `src/telegram/` → acquisizione Telegram, filtraggio, queue, routing, risoluzione trader
- `src/parser/` → parser per trader, modelli, canonical, intent/action mapping
- `src/validation/` → validazione/coerenza del parse result
- `src/operation_rules/` → regole operative / sizing / gate risk-first
- `src/target_resolver/` → risoluzione target di UPDATE
- `src/storage/` → persistenza su SQLite
- `src/execution/` → bridge runtime verso execution/exchange/Freqtrade + update runtime
- `src/backtesting/` → sottosistema parallelo, non parte del path live principale
- `parser_test/` → harness/test parser separato dal runtime live

Quindi il progetto **oggi non è solo** “Telegram parser”.  
È già una pipeline più ampia:

```text
Telegram ingest
  -> raw persistence
  -> routing
  -> trader resolution
  -> eligibility
  -> parser trader-specific
  -> validation
  -> operation rules
  -> signals / operational_signals
  -> target resolution
  -> runtime updates DB
  -> execution bridge separato
```

---

## 3. Entry point reale

## `main.py`

`main.py` è il vero punto di ingresso del processo live.

Fa queste cose:

1. carica `.env`
2. applica migrazioni DB
3. carica `channels.yaml`
4. valida la config di operation rules
5. carica config trader / alias
6. costruisce `TelegramSourceTraderMapper`
7. costruisce `DynamicPairlistManager`
8. costruisce i service/store principali
9. costruisce `MessageRouter`
10. costruisce `TelegramListener`
11. avvia `ChannelConfigWatcher`
12. avvia `TelegramClient` (Telethon)
13. registra handler live
14. esegue recovery/catchup
15. lancia il worker queue-based

Quindi **il wiring centrale del sistema è in `main.py`**.

---

## 4. Mappa sintetica layer → input/output

| Layer | Nome | Input principale | Output principale | Scritture principali |
|---|---|---|---|---|
| L0 | Bootstrap & wiring | env, config, db path | componenti inizializzati | migrazioni DB |
| L1 | Telegram acquisition | evento Telethon / catchup | messaggio Telegram normalizzato + enqueue | nessuna logica business |
| L2 | Raw ingestion & queue state | `TelegramIncomingMessage` | `raw_message_id`, item in coda | `raw_messages` |
| L3 | Router orchestration / pre-parser control | `QueueItem` | decisione di routing, esito tecnico del messaggio, invocazione dei sottopassi interni | `processing_status`, `review_queue`, `parse_results` |
| L4 | Sottopasso interno di L3: effective trader resolution | testo, reply chain, source mapping | `EffectiveTraderResult` | indirette su `review_queue` |
| L5 | Sottopasso interno di L3: eligibility & linking | raw text + reply/link/ref | `EligibilityResult` | persistito poi in `parse_results` |
| L6 | Trader parsing | `text` + `ParserContext` | `TraderParseResult` | nessuna diretta |
| L7 | Canonical + validation | `TraderParseResult` | `ParseResultRecord`, `ParseResultV1Record`, `ValidationResult` | `parse_results`, `parse_results_v1` |
| L8 | Operation rules | parse valido + regole trader | `OperationalSignal` | nessuna diretta |
| L9 | Signal persistence & target resolution | `OperationalSignal`, target refs | `signals`, `ResolvedTarget`, `operational_signals` | `signals`, `operational_signals`, pairlist JSON |
| L10 | Runtime DB updates | `StateUpdatePlan` / callback exchange | stato operativo aggiornato | `signals`, `orders`, `trades`, `positions`, `events`, `warnings` |
| L11 | Execution bridge separato | `signals` PENDING / exchange callbacks | ordini e fill runtime | `orders`, `trades`, `positions`, `events`, `warnings` |

---

## 5. Layer reali del flusso live, con input/output

# L0 — Bootstrap / Wiring / Startup

**File principali**
- `main.py`
- `src/core/config_loader.py`
- `src/core/logger.py`
- `src/core/migrations.py`
- `src/telegram/channel_config.py`

**Responsabilità**
- inizializzare ambiente
- applicare migrazioni
- caricare configurazioni
- costruire i componenti
- avviare client Telegram e watcher config

**Input**
- variabili env (`DB_PATH`, `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, ecc.)
- file sotto `config/`
- file migrazioni sotto `db/migrations/`

**Output**
- logger pronto
- DB migrato
- `ChannelsConfig`
- `TelegramSourceTraderMapper`
- `DynamicPairlistManager`
- store e service runtime
- `MessageRouter`
- `TelegramListener`

**Nota**
`channels.yaml` è fonte primaria per canali/topic attivi; `TELEGRAM_ALLOWED_CHAT_IDS` resta fallback temporaneo.

---

# L1 — Acquisizione Telegram

**File principali**
- `src/telegram/listener.py`
- `src/telegram/topic_utils.py`
- `src/telegram/channel_config.py`

**Responsabilità**
- ricevere `NewMessage` da Telethon
- estrarre `chat_id`, `topic_id`, metadata chat
- gestire live mode e catchup/recovery
- filtrare chat/topic non ammessi
- saltare messaggi media-only
- supportare hot reload della config canali

**Input**
- `events.NewMessage.Event`
- messaggi storici da `client.get_messages(...)` nel recovery

**Output**
- chiamata a `_ingest_and_enqueue(...)`
- `QueueItem` inserito nella coda worker

**Decisioni importanti**
- `chat_id/topic_id` devono essere ammessi dalla config
- i messaggi `media_only` vengono scartati
- il listener **non parsea**: acquisisce e delega

**Classificazioni osservate in questo layer**
- modalità acquisizione:
  - `live`
  - `catchup`

---

# L2 — Raw ingestion / Persistenza iniziale / Queue lifecycle

**File principali**
- `src/telegram/ingestion.py`
- `src/storage/raw_messages.py`
- `src/storage/processing_status.py`

**Responsabilità**
- convertire il messaggio Telegram in `TelegramIncomingMessage`
- salvare su `raw_messages`
- assegnare `processing_status`
- rilevare duplicati
- re-enqueue dei messaggi rimasti `pending/processing` dopo restart

**Input**
- `TelegramIncomingMessage` con:
  - `source_chat_id`
  - `telegram_message_id`
  - `message_ts`
  - `raw_text`
  - `source_chat_title` 	// vedo docs in progress "note"
  - `source_type`       	// vedo docs in progress "note"
  - `source_trader_id`
  - `reply_to_message_id`
  - `acquisition_status`
  - `source_topic_id`
  - eventuali campi media

**Output**
- `IngestionResult(saved, raw_message_id)`
- record in `raw_messages`
- item queueable dal listener/router

**Tabelle chiave**
- `raw_messages`

**Campi importanti in `raw_messages`**
- contesto sorgente:
  - `source_chat_id`
  - `source_chat_title`
  - `source_type`
  - `source_trader_id`
  - `source_topic_id`
- messaggio:
  - `telegram_message_id`
  - `reply_to_message_id`
  - `raw_text`
  - `message_ts`
  - `acquired_at`
- acquisizione:
  - `acquisition_status`
  - `processing_status`
- media:
  - `has_media`
  - `media_kind`
  - `media_mime_type`
  - `media_filename`
  - `media_blob`

### Stati `processing_status` osservati
- `pending`
- `processing`
- `done`
- `failed`
- `blacklisted`
- `review`

### Stati `acquisition_status` osservati o assegnati nel codice
- `ACQUIRED_ELIGIBLE`
- `BLACKLISTED`
- `ACQUIRED_UNKNOWN_TRADER`
- `ACQUIRED_REVIEW_ONLY`

**Nota importante**
Il layer raw è già abbastanza ricco.  
Non salva solo testo, ma anche metadati di routing e contesto operativo.

**Chiarimento**
Qui il trader **non viene ancora risolto davvero** nel path normale.  
Il listener salva il raw e mette il messaggio in coda; la risoluzione effettiva del trader avviene nel router (L3/L4).

---

# L3 — Routing e pre-parser orchestration

**File principale**
- `src/telegram/router.py`

Questo è il **cuore orchestrativo** del runtime.

**Nota correttiva importante**
Nel flusso reale **L3 e L4 non sono due script separati che risolvono due volte il trader**.  
La risoluzione del trader avviene una sola volta, **dentro il router**, tramite il sottopasso L4.  
Quindi:

- **L3** = layer orchestrativo contenitore
- **L4** = sottoprocesso interno chiamato dal router
- non esiste una seconda risoluzione trader autonoma nel listener/ingestion

**Responsabilità reali**
- aggiornare `processing_status=processing`
- applicare blacklist
- chiamare il sottopasso di risoluzione trader effettivo
- chiamare il sottopasso di eligibility del messaggio
- scegliere il parser corretto
- costruire `ParserContext`
- invocare il parser di profilo trader
- avviare canonical v1 shadow/native
- validare il risultato
- persistere `parse_results`
- se valido, avviare il blocco Phase 4+
- chiudere con `processing_status=done`

**Input**
- `QueueItem`:
  - `raw_message_id`
  - `source_chat_id`
  - `telegram_message_id`
  - `raw_text`
  - `source_trader_id`
  - `reply_to_message_id`
  - `acquisition_mode`
  - `source_topic_id`

**Output**
- messaggio fermato con stato `blacklisted`, `review`, `failed`, `done`
- `ParseResultRecord`
- eventuale `ParseResultV1Record`
- eventuale `SignalRecord`
- eventuale `OperationalSignalRecord`
- eventuali update runtime DB

**Perché è un layer centrale**
`MessageRouter` non è un semplice dispatcher.  
È il vero **application service centrale** della pipeline live.

---

# L4 — Sottopasso interno di L3: risoluzione trader effettivo

**File principali**
- `src/telegram/effective_trader.py`
- `src/telegram/trader_mapping.py`
- `src/core/trader_tags.py` (indiretto)
- `src/telegram/channel_config.py`

**Responsabilità**									// Modificare il ordine

Determinare il `trader_id` effettivo usando più strategie, in ordine.

Questo passaggio è **chiamato dal router**.  
Non è un secondo layer esterno che gira prima o dopo il router: è una sua sotto-funzione logica.

1. **tag/alias nel testo**
2. **eredità dalla reply chain**
3. **mapping sorgente Telegram** (`chat_id`, `chat_username`, `chat_title`)
4. fallback `channels.yaml` nel router


Nuovo ordine: 

1. fallback `channels.yaml` nel router			
2. **tag/alias nel testo** 
3. **eredità dalla reply chain** 
4. **mapping sorgente Telegram**

**Input**
- `EffectiveTraderContext`
  - `source_chat_id`
  - `source_chat_username`
  - `source_chat_title`
  - `raw_text`
  - `reply_to_message_id`

**Output**
- `EffectiveTraderResult`
  - `trader_id`
  - `method`
  - `detail`

### Metodi di risoluzione osservati
- `content_alias`
- `content_alias_ambiguous`
- `content_alias_missing`
- `reply_chain`
- `reply_chain_alias`
- `source_chat_id`
- `source_chat_username`
- `source_chat_title`
- `channels_yaml`
- `unresolved`

**Comportamento reale**
Se il trader non viene risolto:
- il messaggio entra in `review_queue`
- `processing_status` diventa `review`
- il flusso si ferma

Quindi questo layer è distinto dal parser:
serve a capire **quale parser usare** e quale trader attribuire al messaggio.

---

# L5 — Sottopasso interno di L3: eligibility / linking preliminare

**File principale**
- `src/telegram/eligibility.py`

**Responsabilità**
Determinare se un messaggio è abbastanza “forte” da passare al parsing operativo.

**Input**
- `source_chat_id`
- `raw_text`
- `reply_to_message_id`

**Output**
- `EligibilityResult`
  - `is_eligible`
  - `status`
  - `reason`
  - `strong_link_method`
  - `referenced_message_id`

### Strong link supportati
- reply diretta
- link Telegram
- riferimento esplicito
- hashtag reference

### Metodi `strong_link_method` osservati
- `direct_reply`
- `telegram_link`
- `explicit_reference`

### Logica reale
Se il messaggio:
- sembra un **short update**
- ma **non** ha strong link

allora viene marcato:
- `ACQUIRED_REVIEW_ONLY`
- reason: `short_update_without_strong_link`

Se invece passa:
- `ACQUIRED_ELIGIBLE`

Quindi questo layer serve come **guardrail pre-parser** per evitare update ambigui senza target forte.

---

# L6 — Trader parsing

**File principali**
- `src/parser/trader_profiles/base.py`
- `src/parser/trader_profiles/registry.py`
- `src/parser/models/`
- `src/parser/intent_action_map.py`
- `src/parser/action_builders/`
- `src/parser/trader_profiles/`

**Responsabilità**
Trasformare il messaggio raw in un output strutturato trader-specifico.

## Modello reale di input parser

Il parser riceve:

- `text`
- `ParserContext`

`ParserContext` include già:

- `trader_code`
- `message_id`
- `reply_to_message_id`
- `channel_id`
- `raw_text`
- `reply_raw_text`
- `extracted_links`
- `hashtags`

## Output reale parser

Il risultato standard è `TraderParseResult`, che contiene: ????????????????? UNIRE????

- `message_type`
- `intents`
- `entities`
- `target_refs`
- `reported_results`
- `warnings`
- `confidence`

e anche il livello semantico v2: ????????????????? UNIRE??

- `primary_intent`
- `actions_structured`
- `target_scope`
- `linking`
- `diagnostics`

## Registry reale

Il registry attuale collega questi parser profilo:

- `trader_a`
- `trader_b`
- `trader_c`
- `trader_d`
- `trader_3`

Quindi il layer parser reale è già multi-trader e factory-based.

### `message_type` osservati nel codice
- `NEW_SIGNAL`
- `UPDATE`
- `INFO_ONLY`
- `SETUP_INCOMPLETE`
- `UNCLASSIFIED`

### Classificazione intent osservata in validazione

**Intent action-oriented**
- `U_MOVE_STOP`    // se non è estraibile o asente il valore colossare a MOVE_STOP_TO_BE`
- `U_MOVE_STOP_TO_BE`
- `U_CLOSE_FULL`
- `U_CLOSE_PARTIAL`
- `U_CANCEL_PENDING`
- `U_CANCEL_PENDING_ORDERS` //// ????? SERVE????
- `U_REENTER`     /// cancella ordine essitente, prende di riferimento per segnale da cui è stato originato
- `U_ADD_ENTRY`
- `U_MODIFY_ENTRY`
- `U_UPDATE_TAKE_PROFITS`
- `U_INVALIDATE_SETUP`   // non serve? 
- `NS_CREATE_SIGNAL`

**Intent informativi / context**
- `U_TP_HIT`
- `U_TP_HIT_EXPLICIT` // cosa serve? se ce  "U_TP_HIT"
- `U_SL_HIT`
- `U_STOP_HIT`
- `U_REPORT_FINAL_RESULT`

### Mappatura intent → action osservata  // forese solo dipo canonical message? 
Dal codice si vedono almeno queste action operative:

- `ACT_CREATE_SIGNAL`
- `ACT_MOVE_STOP_LOSS`
- `ACT_CANCEL_ALL_PENDING_ENTRIES`
- `ACT_REMOVE_PENDING_ENTRY`
- `ACT_CLOSE_PARTIAL`
- `ACT_CLOSE_FULL`
- `ACT_REENTER_POSITION`
- `ACT_MARK_SIGNAL_INVALID`

---

# L7 — Canonicalizzazione v1 + validazione + parse_results

**File principali**
- `src/storage/parse_results.py`
- `src/storage/parse_results_v1.py`
- `src/validation/coherence.py`
- logica router in `src/telegram/router.py`

**Responsabilità**
Persistenza dell’output parser e verifica di coerenza strutturale.

## 7A. Canonical v1

Se è disponibile `ParseResultV1Store`, il router esegue:

- `_native_canonical_v1()` se il profilo supporta `parse_canonical()`
- `_shadow_normalize()` altrimenti

Questa parte è parallela e non blocca il flusso principale.

### Output `parse_results_v1`
`ParseResultV1Record` contiene:
- `raw_message_id`
- `trader_id`
- `primary_class`
- `parse_status`
- `confidence`
- `canonical_json`
- `normalizer_error`
- `created_at`

### Classificazioni osservate direttamente nel codice
- `primary_class` inizializzato con fallback `INFO`
- `parse_status` inizializzato con fallback `UNCLASSIFIED`

## 7B. Validazione coerenza

`validation.coherence` produce `ValidationResult`.

### Stati di validazione osservati
- `VALID`
- `INFO_ONLY`
- `STRUCTURAL_ERROR`

### Regole principali osservate
- `INFO_ONLY` e `UNCLASSIFIED` → non actionable
- `SETUP_INCOMPLETE` → `INFO_ONLY` con warning `setup_incomplete`
- `NEW_SIGNAL` richiede almeno:
  - `symbol`
  - `side/direction`
  - entry
  - stop loss
  - take profits
- `UPDATE` senza action intent → `INFO_ONLY`
- `UPDATE` con action intent ma entità mancanti → `STRUCTURAL_ERROR`

## 7C. Persistenza `parse_results`

`ParseResultStore` salva un record ponte molto ricco:

- `eligibility_status`
- `eligibility_reason`
- `declared_trader_tag`
- `resolved_trader_id`
- `trader_resolution_method`
- `message_type`
- `parse_status`
- `completeness`
- `is_executable`
- `symbol`
- `direction`
- `entry_raw`
- `stop_raw`
- `target_raw_list`
- `leverage_hint`
- `risk_hint`
- `risky_flag`
- `linkage_method`
- `linkage_status`
- `warning_text`
- `notes`
- `parse_result_normalized_json`
- `created_at`
- `updated_at`

Quindi `parse_results` è la tabella ponte tra parser e fase operativa.




## Layer 5 — Canonicalizzazione v1 + validazione + parse_results

### File principali
- `src/storage/parse_results.py`
- `src/storage/parse_results_v1.py`
- `src/validation/coherence.py`
- logica router in `src/telegram/router.py`

### Responsabilità
Persistenza dell’output parser e verifica di coerenza strutturale.

### Cosa avviene davvero
Dopo il parsing il router fa due cose separate:

#### 5A. Canonical v1
Se è disponibile `ParseResultV1Store`, il router esegue:

- `_native_canonical_v1()` se il profilo supporta `parse_canonical()`
- `_shadow_normalize()` altrimenti

Questa parte è parallela e non blocca il flusso principale.

### Chiarimento importante: `TraderParseResult` vs `TraderEventEnvelopeV1` vs `CanonicalMessage`

Nel progetto attuale convivono o sono citati **tre livelli concettuali diversi** di output parser.

#### 1. `TraderParseResult`
È l'**output parser reale oggi usato nel runtime principale**.
È il contratto restituito dai parser trader-specific (`trader_a`, `trader_b`, `trader_c`, `trader_d`, `trader_3`) e contiene:

- `message_type`
- `intents`
- `entities`
- `target_refs`
- `reported_results`
- `warnings`
- `confidence`
- più estensioni semantiche v2 (`primary_intent`, `actions_structured`, `target_scope`, `linking`, `diagnostics`)

In pratica è il **formato legacy/attuale di uscita parser**.

#### 2. `TraderEventEnvelopeV1` / `parser_event_envelope_v1`
Questo **non risulta oggi come contratto runtime consolidato del path principale**.
Nel repository compare nelle **doc di migrazione del nuovo parser** come shape intermedia proposta.

L'idea architetturale descritta nelle doc è:

```text
TraderParseResult (legacy)
  -> adapter centrale
  -> TraderEventEnvelopeV1
  -> normalizer
  -> CanonicalMessage v1
```

Quindi `parser_event_envelope_v1` / `TraderEventEnvelopeV1` va letto come:

- **contratto parser-side/intermedio proposto**
- shape unificata prima del normalizer
- meccanismo per evitare fallback sparsi e mapping diversi per ogni trader

È utile per la migrazione architetturale, ma **non è il centro del runtime live attuale** come invece lo è `TraderParseResult`.

#### 3. `CanonicalMessage` (`canonical parser model`)
Questo invece esiste davvero nel codice attuale in `src/parser/canonical_v1/models.py`.
È il **modello canonico finale** del parser, basato su Pydantic.

Le caratteristiche chiave sono:

- `primary_class`: `SIGNAL | UPDATE | REPORT | INFO`
- `parse_status`: `PARSED | PARTIAL | UNCLASSIFIED | ERROR`
- `intents`, `primary_intent`
- `targeting`
- payload separati:
  - `signal`
  - `update`
  - `report`
- `warnings`, `diagnostics`, `raw_context`

Quindi il `canonical parser model` è il **formato canonico finale downstream**, non il formato raw prodotto direttamente da tutti i parser legacy.

#### Sintesi pratica

| Livello | Ruolo | Stato nel repo attuale |
|---|---|---|
| `TraderParseResult` | output parser reale attuale | **attivo nel runtime principale** |
| `TraderEventEnvelopeV1` / `parser_event_envelope_v1` | envelope intermedio proposto | **shape di migrazione / adapter-side** |
| `CanonicalMessage` | modello canonico finale | **presente nel codice e usato dal percorso canonical v1** |

#### Regola mentale corretta

- se stai guardando **il runtime live di oggi**, pensa soprattutto a:
  - `TraderParseResult`
  - `parse_results`
  - `parse_results_v1`

- se stai guardando **la direzione architetturale del nuovo parser**, allora la catena voluta è:

```text
TraderParseResult
  -> TraderEventEnvelopeV1
  -> CanonicalMessage
```

Questo evita di confondere:

- **output parser legacy attuale**
- **envelope intermedio di migrazione**
- **modello canonico finale**








---

# L8 — Operation Rules / OperationalSignal

**File principali**
- `src/operation_rules/engine.py`
- `src/operation_rules/loader.py`
- `src/operation_rules/risk_calculator.py`

**Responsabilità**
Trasformare un parse valido in un segnale operativo con gate, rischio, size e snapshot regole.

**Input**
- `TraderParseResult`
- `trader_id`
- `db_path`
- config regole trader/globali

**Output**
- `OperationalSignal`

## Cosa contiene `OperationalSignal` in pratica
Dal codice si vede che può contenere almeno:

- `parse_result`
- `trader_id`
- `is_blocked`
- `block_reason`
- `risk_mode`
- `risk_pct_of_capital`
- `risk_usdt_fixed`
- `capital_base_usdt`
- `risk_budget_usdt`
- `sl_distance_pct`
- `position_size_usdt`
- `position_size_pct`
- `entry_split`
- `leverage`
- `risk_hint_used`
- `management_rules`
- `applied_rules`
- `warnings`

## Comportamento per tipo messaggio

### Per `UPDATE`
- non fa sizing di apertura
- fa passthrough con snapshot management rules

### Per `NEW_SIGNAL`
- carica regole trader-specifiche
- applica gate di abilitazione
- verifica entry e stop
- verifica leva
- controlla limiti su stesso simbolo
- applica `risk_hint` se previsto
- calcola budget rischio
- calcola size
- costruisce `entry_split`
- fotografa le management rules

### Blocchi / gate osservati nel codice
Tra i motivi di block si vedono almeno:

- `trader_not_registered`
- `trader_disabled`
- `missing_entry`
- `missing_stop_loss`
- `invalid_leverage`
- `max_concurrent_same_symbol`
- `per_signal_cap_exceeded`
- `trader_capital_at_risk_exceeded`
- `global_capital_at_risk_exceeded`
- `price_out_of_static_range`
- `zero_sl_distance`

Quindi l’output non è solo “segnale valido”:  
è già un oggetto operativo pronto per persistenza o scarto.

---

# L9 — Persistenza segnali, target resolution, operational signals

**File principali**
- `src/storage/signals_store.py`
- `src/execution/dynamic_pairlist.py`
- `src/target_resolver/resolver.py`
- `src/storage/operational_signals_store.py`
- builder interni in `src/telegram/router.py`

## 9A. Signal persistence

### Quando avviene
Dentro `_apply_phase4()` del router:

1. applica operation rules
2. se `NEW_SIGNAL` e non bloccato:
   - costruisce `attempt_key`
   - costruisce `SignalRecord`
   - inserisce in `signals`
   - aggiorna il file pairlist dinamico

### Input
- `OperationalSignal`
- `QueueItem`
- `trader_id`
- `now_ts`

### Output
- riga in `signals`
- eventuale update di pairlist JSON

### Dati concretamente scritti in `signals`
- `attempt_key`
- `env`
- `channel_id`
- `root_telegram_id`
- `trader_id`
- `trader_prefix`
- `symbol`
- `side`
- `entry_json`
- `sl`
- `tp_json`
- `status`
- `confidence`
- `raw_text`
- `created_at`
- `updated_at`
- `source_topic_id`

### Stati `signals.status` osservati nel codice
- `PENDING`
- `ACTIVE`
- `CLOSED`
- `CANCELLED`
- `INVALID`

**Nota**
Non tutti sono assegnati in un singolo file, ma sono visibili tra router, callback e update applier.

## 9B. Pairlist dinamica

`DynamicPairlistManager` mantiene un JSON locale con:

- `pairs`
- `refresh_period`

Serve a far refreshare a Freqtrade una `RemotePairList`.

## 9C. Target Resolver

**Responsabilità**
Risolvere gli `UPDATE` verso i target concreti.

**Input**
- `OperationalSignal`
- `db_path`

**Output**
- `ResolvedTarget`
  - `kind`
  - `position_ids`
  - `eligibility`
  - `reason`

### Strategie reali supportate
- `STRONG / REPLY`
- `STRONG / TELEGRAM_LINK`
- `STRONG / EXPLICIT_ID`
- `SYMBOL`
- `GLOBAL`
  - `all_long`
  - `all_short`
  - `all_positions`

### `target_eligibility` osservati
- `ELIGIBLE`
- `WARN`
- `INELIGIBLE`
- `UNRESOLVED`

**Nota importante**
Questo layer non si limita a “trovare il target”.  
Valuta anche se il target è operativamente coerente con gli intent.

## 9D. `operational_signals`

Dopo target resolution, il router:

1. ricava `parse_result_id`
2. costruisce `OperationalSignalRecord`
3. inserisce in `operational_signals`

### Campi chiave di `OperationalSignalRecord`
- `parse_result_id`
- `attempt_key`
- `trader_id`
- `message_type`
- `is_blocked`
- `block_reason`
- campi sizing/risk
- `management_rules_json`
- `applied_rules_json`
- `warnings_json`
- `resolved_target_ids`
- `target_eligibility`
- `target_reason`
- `created_at`
- `source_topic_id`

Quindi `operational_signals` è il log strutturato della decisione operativa, non solo dei segnali aperti.

---

# L10 — Runtime DB updates da UPDATE Telegram

**File principali**
- `src/execution/update_planner.py`
- `src/execution/update_applier.py`
- logica router in `_apply_update_runtime()`

**Responsabilità**
Applicare al DB operativo gli update Telegram che hanno target eligibile.

## 10A. Planner

`build_update_plan(...)` costruisce uno `StateUpdatePlan`.

### Input planner
- mappa normalizzata con:
  - `message_type`
  - `intents`
  - `actions`
  - `entities`
  - `reported_results`
  - `target_refs`

### Output planner
- `StateUpdatePlan`
  - `message_type`
  - `intents`
  - `actions`
  - `target_refs`
  - `signal_updates`
  - `order_updates`
  - `position_updates`
  - `result_updates`
  - `events`
  - `warnings`

### Eventi di piano osservati
- `STOP_MOVED_TO_BE`
- `STOP_MOVED`
- `PARTIAL_CLOSE_REQUESTED`
- `FULL_CLOSE_REQUESTED`
- `PENDING_ENTRIES_CANCELLED`
- `PENDING_ENTRY_REMOVED`
- `ENTRY_FILLED`
- `TP_HIT`
- `STOP_HIT`
- `SIGNAL_INVALIDATED`
- `POSITION_CLOSED`
- `RESULT_ATTACHED`

## 10B. Applier

`apply_update_plan(...)` applica il piano sulle tabelle runtime.

### Input applier
- `StateUpdatePlan`
- `db_path`
- `env`
- `channel_id`
- `telegram_msg_id`
- `trader_id`
- `trader_prefix`
- `target_attempt_keys`

### Output applier
- `UpdateApplyResult`
  - `target_attempt_keys`
  - `applied_signal_updates`
  - `applied_order_updates`
  - `applied_position_updates`
  - `applied_result_updates`
  - `applied_events`
  - `warnings`
  - `errors`

### Tabelle toccate dall’applier
- `signals`
- `orders`
- `trades`
- `positions`
- `events`
- `warnings`

### Effetti runtime osservati
- move stop → aggiorna `signals.sl` e ordini SL
- close partial/full → aggiorna `trades.state`
- cancel pending entries → aggiorna `orders.status`
- mark stop/position closed → chiude trade, signal, position
- attach result → aggiorna `trades.meta_json`
- inserisce `events`
- inserisce `warnings`

Quindi il runtime DB non vive solo di callback exchange:  
anche gli update Telegram modificano direttamente lo stato operativo.

---

# L11 — Bridge di execution / Freqtrade / exchange-backed runtime

**File principali**
- `src/execution/market_entry_dispatcher.py`
- `src/execution/exchange_gateway.py`
- `src/execution/freqtrade_exchange_backend.py`
- `src/execution/freqtrade_callback.py`
- `src/execution/exchange_order_manager.py`
- `src/execution/order_reconciliation.py`
- `src/execution/risk_gate.py`
- `src/execution/protective_orders_mode.py`
- `src/execution/freqtrade_ui_mirror.py`
- `src/execution/freqtrade_normalizer.py`

**Responsabilità**
Questo è il sottosistema di esecuzione live/bridge verso exchange e Freqtrade.

## 11A. Dispatch entry market

`MarketEntryDispatcher`:

- legge i `signals` con `status='PENDING'`
- carica il contesto normalizzato
- verifica se il primo leg è `MARKET`
- usa `ExchangeGateway`
- invia l’ordine
- registra evento audit
- chiama `order_filled_callback()` per allineare il DB

### Input principali
- `signals`
- `FreqtradeSignalContext`
- `ExchangeGateway`

### Output principali
- ordine exchange
- evento audit
- callback runtime su DB

## 11B. Gateway canonico exchange

`ExchangeGateway` è il wrapper unificato sopra un backend exchange.

### API osservate
- `create_entry_market_order`
- `create_reduce_only_market_order`
- `create_reduce_only_limit_order`
- `create_reduce_only_stop_order`
- `cancel_order`
- `fetch_open_orders`
- `fetch_position`
- `fetch_current_price`

### Oggetti principali
- `ExchangeOrder`
- `ExchangePosition`

### Stati ordine normalizzati osservati
- `NEW`
- `OPEN`
- `PARTIALLY_FILLED`
- `FILLED`
- `CANCELLED`
- `REJECTED`
- `EXPIRED`

## 11C. Adapter Freqtrade

`FreqtradeExchangeBackend` adatta un oggetto exchange di Freqtrade/ccxt al protocollo del gateway.

Serve per convertire:
- symbol/pair
- tipo ordine
- status ordine
- fetch di open orders, ticker, position

## 11D. Callback runtime

`freqtrade_callback.py` persiste eventi e stati di runtime per:

- entry fill
- entry order opened
- partial exit
- full exit
- stoploss hit

e aggiorna le tabelle operative:

- `signals`
- `orders`
- `trades`
- `positions`
- `events`
- `warnings`

### Eventi osservati chiaramente in callback / dispatcher
- `ENTRY_FILLED`
- `ENTRY_ORDER_OPENED`
- `PARTIAL_CLOSE_FILLED`
- `MARKET_ENTRY_DISPATCHED`
- `MARKET_ENTRY_DISPATCH_FAILED`
- `PROTECTIVE_ORDER_MANAGER_MISSING`
- `PROTECTIVE_ORDER_SYNC_FAILED`

### Stati trade/position osservati nel codice
**Trade state**
- `OPEN`
- `CLOSED`
- `PARTIAL_CLOSE_REQUESTED`

**Close reason osservati**
- `FULL_CLOSE_REQUESTED`
- `POSITION_CLOSED`
- `STOP_HIT`
- `PARTIAL_CLOSE_FILLED`

**Signal fillability osservata**
- `PENDING`
- `ACTIVE`

### Osservazione architetturale importante
Questo layer di execution **esiste davvero nel codice**, ma **non parte direttamente da `main.py`**.  
`main.py` oggi orchestra fino a:

- ingestione
- parsing
- operation rules
- target resolution
- update runtime
- pairlist dinamica

mentre il bridge di esecuzione è un sottosistema separato già presente nel repository.

---

## 6. Tipi di eventi registrati dal sistema

Questa sezione elenca i tipi di evento **osservati direttamente nei file letti**.  
È utile trattarla come **lista sicuramente incompleta ma reale**, non come inventario assoluto del repository intero.

## 6.1 Eventi da planner/applier update Telegram

Questi sono eventi semantici di dominio, usati quando un UPDATE Telegram produce cambiamenti runtime:

- `STOP_MOVED_TO_BE`
- `STOP_MOVED`
- `PARTIAL_CLOSE_REQUESTED`
- `FULL_CLOSE_REQUESTED`
- `PENDING_ENTRIES_CANCELLED`
- `PENDING_ENTRY_REMOVED`
- `ENTRY_FILLED`
- `TP_HIT`
- `STOP_HIT`
- `SIGNAL_INVALIDATED`
- `POSITION_CLOSED`
- `RESULT_ATTACHED`

## 6.2 Eventi da execution/callback/runtime exchange

Questi sono eventi tecnici/runtime osservati nel bridge di esecuzione:

- `ENTRY_FILLED`
- `ENTRY_ORDER_OPENED`
- `PARTIAL_CLOSE_FILLED`
- `MARKET_ENTRY_DISPATCHED`
- `MARKET_ENTRY_DISPATCH_FAILED`
- `PROTECTIVE_ORDER_MANAGER_MISSING`
- `PROTECTIVE_ORDER_SYNC_FAILED`

## 6.3 Come classificarli praticamente

### A. Eventi di apertura/attivazione
- `ENTRY_ORDER_OPENED`
- `ENTRY_FILLED`
- `MARKET_ENTRY_DISPATCHED`

### B. Eventi di modifica gestione
- `STOP_MOVED`
- `STOP_MOVED_TO_BE`

### C. Eventi di chiusura parziale/totale
- `PARTIAL_CLOSE_REQUESTED`
- `PARTIAL_CLOSE_FILLED`
- `FULL_CLOSE_REQUESTED`
- `POSITION_CLOSED`
- `STOP_HIT`
- `TP_HIT`

### D. Eventi di invalidazione/cancellazione
- `PENDING_ENTRIES_CANCELLED`
- `PENDING_ENTRY_REMOVED`
- `SIGNAL_INVALIDATED`

### E. Eventi di audit/errore tecnico
- `MARKET_ENTRY_DISPATCH_FAILED`
- `PROTECTIVE_ORDER_MANAGER_MISSING`
- `PROTECTIVE_ORDER_SYNC_FAILED`

---

## 7. Classificazioni e stati usati dal sistema

## 7.1 Classificazione messaggio parser

### `message_type`
- `NEW_SIGNAL`
- `UPDATE`
- `INFO_ONLY`
- `SETUP_INCOMPLETE`
- `UNCLASSIFIED`

## 7.2 Classificazione intent
### Action intents
- producono action downstream
- passano in validation/action mapping/update planner

### Context intents
- informativi
- possono essere persistiti/audited ma non necessariamente cambiano stato

## 7.3 Stato acquisizione raw
### `acquisition_status`
- `ACQUIRED_ELIGIBLE`
- `BLACKLISTED`
- `ACQUIRED_UNKNOWN_TRADER`
- `ACQUIRED_REVIEW_ONLY`

## 7.4 Stato lavorazione queue
### `processing_status`
- `pending`
- `processing`
- `done`
- `failed`
- `blacklisted`
- `review`

## 7.5 Stato validazione parser
### `validation.status`
- `VALID`
- `INFO_ONLY`
- `STRUCTURAL_ERROR`

## 7.6 Stato target resolution
### `target_eligibility`
- `ELIGIBLE`
- `WARN`
- `INELIGIBLE`
- `UNRESOLVED`

## 7.7 Stato ordini exchange normalizzati
- `NEW`
- `OPEN`
- `PARTIALLY_FILLED`
- `FILLED`
- `CANCELLED`
- `REJECTED`
- `EXPIRED`

## 7.8 Stato segnali/trade osservati
### `signals.status`
- `PENDING`
- `ACTIVE`
- `CLOSED`
- `CANCELLED`
- `INVALID`

### `trades.state`
- `OPEN`
- `CLOSED`
- `PARTIAL_CLOSE_REQUESTED`

---

## 8. Tabelle DB e loro ruolo nel flusso

| Tabella | Ruolo reale |
|---|---|
| `raw_messages` | inbox raw Telegram + metadata sorgente + queue lifecycle |
| `review_queue` | messaggi da revisione manuale |
| `parse_results` | output parser persistito e arricchito |
| `parse_results_v1` | shadow/native canonical v1 |
| `signals` | segnali candidati/aperti dal flusso NEW_SIGNAL |
| `operational_signals` | audit strutturato della decisione operativa |
| `orders` | ordini runtime/exchange |
| `trades` | stato trade aggregato |
| `positions` | stato posizione runtime |
| `events` | timeline eventi di dominio e runtime |
| `warnings` | warning runtime / apply / execution |

---

## 9. Sequenza reale end-to-end

## Flusso live principale reale

```text
main.py
  ↓
load env / migrations / config / channels.yaml / source map
  ↓
build services + router + listener + watcher
  ↓
Telethon client
  ↓
TelegramListener
  ↓
RawMessageIngestionService
  ↓
raw_messages (+ processing_status)
  ↓
queue worker
  ↓
MessageRouter
  ↓
blacklist check
  ↓
EffectiveTraderResolver
  ↓
MessageEligibilityEvaluator
  ↓
select trader profile parser
  ↓
ParserContext
  ↓
TraderProfileParser
  ↓
TraderParseResult
  ↓
canonical v1 shadow/native
  ↓
validation.coherence
  ↓
parse_results
  ↓
OperationRulesEngine
  ↓
OperationalSignal
  ↓
(if NEW_SIGNAL and not blocked)
signals
  ↓
DynamicPairlistManager
  ↓
TargetResolver
  ↓
operational_signals
  ↓
(if UPDATE eligible)
build_update_plan
  ↓
apply_update_plan
  ↓
signals / orders / trades / positions / events / warnings
```

## Flusso execution separato ma presente

```text
signals (PENDING)
  ↓
MarketEntryDispatcher
  ↓
ExchangeGateway
  ↓
FreqtradeExchangeBackend (o altro backend)
  ↓
freqtrade_callback.py
  ↓
orders / trades / positions / events / warnings / signals
```

---

## 10. Sottosistemi paralleli / non nel flusso live principale

## A. Backtesting

### Package
- `src/backtesting/`

### File presenti
- `chain_builder.py`
- `models.py`
- `report.py`
- `run_backtest.py`
- `run_report.py`
- `runner.py`
- `scenario.py`
- `storage.py`

### Significato architetturale
Il backtesting è un sottosistema separato dal live runtime.  
Non fa parte del loop principale di `main.py`, ma nel repository è un package reale e consistente.

## B. parser_test

### Directory
- `parser_test/`

### Significato
È un harness separato di replay/report/test parser, non parte del loop live.

## C. Telegram bot controller

`src/telegram/bot.py` è presente ma al momento è solo stub/TODO.  
Quindi il “bot Telegram comandi utente” non è un layer runtime sostanziale oggi.

---

## 11. Layer logici finali consigliati, ricostruiti dal codice

Se devo ricostruire i layer “veri” oggi, li descriverei così:

### L0. Bootstrap & wiring
`main.py`, config, migrations, watcher, mapper, pairlist manager

### L1. Telegram acquisition
`TelegramListener`, topic filter, recovery, live handler

### L2. Raw ingestion & queue state
`RawMessageIngestionService`, `RawMessageStore`, `ProcessingStatusStore`

### L3. Routing / pre-parser orchestration
`MessageRouter`, blacklist, review routing, orchestrazione dei sottopassi interni

### L4. Sottopasso interno di L3: effective trader resolution
`EffectiveTraderResolver`, source mapping, reply inheritance

### L5. Sottopasso interno di L3: eligibility / strong-link evaluation
`MessageEligibilityEvaluator`

### L6. Trader parsing
registry parser + profile parser + `TraderParseResult`

### L7. Canonicalization & validation
canonical v1 shadow/native + `validation.coherence` + `parse_results`

### L8. Operationalization
`OperationRulesEngine` -> `OperationalSignal`

### L9. Signal persistence + target resolution
`signals`, `DynamicPairlistManager`, `TargetResolver`, `operational_signals`

### L10. Runtime state updates
`update_planner` + `update_applier`

### L11. Execution bridge / exchange sync
dispatcher, gateway, Freqtrade backend, callbacks, reconciliation

### L12. Parallel analytics subsystem
`src/backtesting`, `parser_test`

---

## 12. Cose importanti emerse dalla revisione

### 1. Il router è il vero orchestratore centrale
Non è solo “instradamento”.  
Oggi è il punto in cui convergono parser, validazione, operation rules, target resolver e update runtime.

### 2. `EffectiveTraderResolver` e “eligibility” sono due layer distinti
- `EffectiveTraderResolver` decide **chi** è il trader effettivo
- `MessageEligibilityEvaluator` decide se il messaggio è **abbastanza collegato/forte** per procedere

Quindi non sono la stessa cosa.

### 3. Il progetto non finisce a `parse_results`
Nel codice attuale il flusso live arriva già fino a:

- `signals`
- `operational_signals`
- applicazione update su stato operativo

### 4. Il bridge execution esiste ma è parzialmente separato
La parte Freqtrade/exchange-backed non è finta o solo documentale:  
i file ci sono e implementano callback, gateway, dispatcher, normalizer, backend.

### 5. `src/exchange` non è il vero execution layer
Il vero layer exchange/runtime oggi è `src/execution`.

### 6. Gli eventi non sono solo “audit”
`events` viene usata sia come:
- audit trail tecnico
- timeline di dominio
- effetto secondario del planner/applier
- specchio di callback exchange/runtime

---

## 13. Risposta sintetica alla domanda “quali sono i layer reali?”

La ricostruzione più fedele del codice attuale è:

```text
main.py / bootstrap
  ↓
Telegram listener + channel/topic filter + recovery
  ↓
raw ingestion + queue lifecycle
  ↓
router / pre-parser orchestration
  ↓
effective trader resolution
  ↓
eligibility / strong-link evaluation
  ↓
trader-specific parsing
  ↓
canonical v1 + validation
  ↓
parse_results
  ↓
operation rules
  ↓
signals
  ↓
target resolver
  ↓
operational_signals
  ↓
update planner / update applier
  ↓
orders / trades / positions / events / warnings
  ↓
execution bridge separato (dispatcher / gateway / freqtrade callbacks)
```

---

## 14. Conclusione

Il repository oggi non ha più una struttura “semplice” tipo listener -> parser -> DB.

La struttura reale è già quella di una pipeline multi-layer con:

- acquisizione Telegram robusta
- persistenza raw con recovery
- risoluzione trader e eligibility indipendenti
- parser multi-profili
- doppio output parser (`parse_results` + `parse_results_v1`)
- motore regole operative
- persistenza segnali
- target resolution
- update runtime sullo stato operativo
- bridge di esecuzione separato ma concreto
- modulo backtesting parallelo

In altre parole:

**il parser è solo una parte del sistema**;  
il codice attuale implementa già un piccolo motore operativo stateful attorno ai segnali Telegram.
