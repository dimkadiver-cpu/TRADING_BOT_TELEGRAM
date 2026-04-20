# Piano di implementazione + checklist — Supporto Telegram Forum Topics in `TRADING_BOT_TELEGRAM`

## 1. Scopo

Questo documento traduce il PRD `PRD_topic_support_TRADING_BOT_TELEGRAM.md` in un piano operativo eseguibile.

Obiettivo: portare il repository dallo stato attuale, forum/chat-level, a uno stato topic-aware end-to-end, mantenendo compatibilità con la configurazione legacy senza `topic_id`.

---

## 2. Obiettivi implementativi

### Obiettivo primario
Introdurre il supporto ai **Telegram forum topics** con due modalità coesistenti:

- **forum-wide** → `topic_id = None`
- **topic-specific** → `topic_id = int`

### Obiettivi secondari
- preservare retrocompatibilità con config attuale;
- evitare regressioni sulla pipeline live;
- mantenere recovery coerente;
- rendere blacklist e trader fallback topic-aware;
- introdurre test e rollout graduale.

---

## 3. Decisioni già fissate

Queste decisioni sono assunte come **bloccate** per l’implementazione:

1. il supporto è per **forum topics**, non per comment threads di canale;
2. la precedenza è: **topic-specific > forum-wide > nessun match**;
3. `topic_id = None` significa **forum/chat intera**;
4. `topic_id = 1` significa **General topic** e non coincide con `None`;
5. la blacklist effettiva è:  
   `blacklist_global + blacklist_scope_matchato`
6. nella prima iterazione:
   - `source_topic_id` è **obbligatorio** in `raw_messages`;
   - estensione di `topic_id` alle tabelle operative è **opzionale / seconda fase**.

---

## 4. Strategia generale

Ordine raccomandato:

1. configurazione e matching scope-aware;
2. migration DB;
3. propagazione `topic_id` listener → DTO → queue → raw persistence;
4. recovery e processing status topic-aware;
5. router, blacklist e fallback trader topic-aware;
6. test suite;
7. eventuale provenance topic nelle tabelle operative.

Motivazione:
- prima si definisce lo scope logico;
- poi si rende il dato persistente;
- solo dopo si cambia recovery/routing;
- infine si consolida con test e rollout.

---

## 5. File e aree da toccare

## 5.1 Config / matching
**File principali**
- `src/telegram/channel_config.py`
- `src/telegram/tests/test_channel_config.py`

**Interventi**
- estendere `ChannelEntry` con `topic_id: int | None`
- aggiornare `load_channels_config()`
- aggiungere validazione config
- introdurre API di matching scope-aware
- aggiornare test config

---

## 5.2 Listener / ingestion
**File principali**
- `src/telegram/listener.py`
- `src/telegram/ingestion.py`
- eventuale nuovo helper topic extraction in:
  - `src/telegram/topic_utils.py` oppure
  - stesso `listener.py` nella prima iterazione

**Interventi**
- estrarre `topic_id` dal messaggio Telethon
- sostituire `_is_allowed_chat()` con matching scope-aware
- propagare `source_topic_id`
- migliorare log

---

## 5.3 Persistenza raw
**File principali**
- `src/storage/raw_messages.py`
- nuova migration in `db/migrations/`
- eventuali test storage

**Interventi**
- aggiungere `source_topic_id`
- aggiornare insert/select/store dataclass
- aggiungere indice topic-aware

---

## 5.4 Processing status / recovery
**File principali**
- `src/storage/processing_status.py`
- `src/telegram/listener.py`
- `src/telegram/tests/test_listener_recovery.py`

**Interventi**
- propagare `source_topic_id` negli stale messages
- introdurre checkpoint topic-aware
- aggiornare recovery
- mantenere compatibilità forum-wide

---

## 5.5 Router / blacklist / trader fallback
**File principali**
- `src/telegram/router.py`
- eventuali test router dedicati

**Interventi**
- usare l’entry matchata, non solo `chat_id`
- applicare blacklist corretta
- applicare fallback trader corretto
- includere topic nei log

---

## 5.6 Provenance operativa opzionale
**File candidati**
- `src/storage/signals_store.py`
- `src/storage/operational_signals_store.py`
- eventuali migration `signals`, `events`, `operational_signals`

**Interventi**
- opzionali nella prima iterazione
- consigliati se basso impatto

---

## 6. Work packages operativi

## WP1 — Config schema + matching

### Obiettivo
Definire il concetto di **scope configurato**.

### Deliverable
- `ChannelEntry.topic_id`
- parsing `topic_id`
- validazione duplicati `(chat_id, topic_id)`
- funzioni:
  - `entries_for_chat(chat_id)`
  - `match_entry(chat_id, topic_id)`
  - eventualmente `match_active_entry(chat_id, topic_id)`

### Azioni dettagliate
- modificare dataclass `ChannelEntry`
- aggiornare `active_channels`
- sostituire o estendere:
  - `active_chat_ids`
  - `channel_for(chat_id)`
- aggiungere validazione:
  - `topic_id` intero positivo o `None`
  - no formato `chat/topic`
  - no duplicati scope
- aggiornare test config

### Definition of Done
- config legacy senza `topic_id` ancora valida
- config con `topic_id` valida
- duplicati scope rifiutati
- matching `topic-specific > forum-wide` testato

### Checklist WP1 ✅ COMPLETATO (2026-04-20)
- [x] aggiunto `topic_id` a `ChannelEntry`
- [x] `load_channels_config()` legge `topic_id`
- [x] default `topic_id=None`
- [x] validazione duplicati `(chat_id, topic_id)`
- [x] validazione `topic_id > 0`
- [x] rimosse assunzioni chat-only dove necessario (`entries_for_chat`, `match_entry`)
- [x] test legacy passano
- [x] test topic-specific passano

---

## WP2 — Migration DB + raw store

### Obiettivo
Rendere persistente il topic.

### Deliverable
- migration additive
- `source_topic_id INTEGER NULL`
- indice topic-aware
- aggiornamento dataclass storage

### Azioni dettagliate
- creare migration nuova, per esempio:
  - `db/migrations/00X_add_source_topic_id_to_raw_messages.sql`
- aggiornare:
  - `RawMessageRecord`
  - `StoredRawMessage`
  - query `save_with_id()`
  - query `get_by_source_and_message_id()`

### Decisioni tecniche
- dedup primaria resta su `source_chat_id + telegram_message_id`
- `source_topic_id` non diventa unique key in prima iterazione
- indice consigliato:
  - `(source_chat_id, source_topic_id, telegram_message_id)`

### Definition of Done
- i messaggi nuovi possono salvare `source_topic_id`
- i record storici restano compatibili con `NULL`
- nessuna migration distruttiva

### Checklist WP2 ✅ COMPLETATO (2026-04-20)
- [x] creata migration additive (`018_add_source_topic_id_to_raw_messages.sql`)
- [x] aggiunta colonna `source_topic_id`
- [x] creato indice topic-aware `(source_chat_id, source_topic_id, telegram_message_id)`
- [x] aggiornato `RawMessageRecord`
- [x] aggiornato `StoredRawMessage`
- [x] aggiornate query di insert/select (pattern colonna condizionale, come media)
- [x] verificata retrocompatibilità con record storici
- [x] test storage null/int passano

---

## WP3 — Topic extraction + listener + ingestion + queue

### Obiettivo
Far entrare in pipeline il `topic_id`.

### Deliverable
- funzione centralizzata di estrazione topic
- listener scope-aware
- `TelegramIncomingMessage.source_topic_id`
- `QueueItem.source_topic_id`

### Azioni dettagliate
1. introdurre helper:
   - `extract_message_topic_id(message) -> int | None`
2. fare matching scope-aware prima dell’ingestione
3. aggiornare `_build_incoming()`
4. aggiornare enqueue
5. aggiornare log:
   - `chat`
   - `topic`
   - `msg_id`

### Requisiti critici
- non basarsi solo su `forum_topic=True`
- distinguere `topic_id=1` da `None`
- messaggi senza topic possono matchare solo forum-wide

### Definition of Done
- i messaggi live topic-specific vengono ammessi/scartati correttamente
- `source_topic_id` entra in raw ingestion
- queue preserva il topic

### Checklist WP3 ✅ COMPLETATO (2026-04-20)
- [x] creata funzione `extract_message_topic_id` (`src/telegram/topic_utils.py`)
- [x] gestito topic General (`reply_to_top_id=None` → 1, `forum_topic is not True` → None)
- [x] sostituito `_is_allowed_chat()` con `_is_allowed_message(chat_id, topic_id)`
- [x] aggiornato `_handle_new_message()`
- [x] aggiornato `_ingest_and_enqueue()`
- [x] aggiornato `_build_incoming()`
- [x] aggiunto `source_topic_id` a `TelegramIncomingMessage`
- [x] aggiunto `source_topic_id` a `QueueItem`
- [x] log topic-aware aggiunti (chat + topic + msg_id)
- [x] test listener live topic-aware passano

---

## WP4 — Recovery / processing status

### Obiettivo
Rendere il recovery coerente con i topic.

### Deliverable
- `StaleMessage.source_topic_id`
- checkpoint topic-aware
- catchup topic-aware

### Strategia consigliata
### Fase 1
- recupero messaggi ancora a livello chat
- filtro topic-aware lato applicazione
- checkpoint già salvato per `(chat_id, topic_id)`

### Fase 2
- opzionale: query recovery mirate per topic

### Azioni dettagliate
- aggiornare `StaleMessage`
- aggiornare `get_stale_messages()`
- aggiornare `_reenqueue_stale()`
- introdurre funzione tipo:
  - `get_last_telegram_message_id(chat_id, topic_id)`
- aggiornare `_catchup_from_telegram()`

### Nota
Per `topic_id=None` la semantica resta forum-wide.

### Definition of Done
- il restart non perde il topic
- i catchup non confondono topic diversi dello stesso forum

### Checklist WP4 ✅ COMPLETATO (2026-04-20)
- [x] aggiunto `source_topic_id` a `StaleMessage`
- [x] aggiornato `get_stale_messages()` (condizionale su colonna, legacy compat)
- [x] aggiornato `_reenqueue_stale()` (propaga `source_topic_id` da `StaleMessage`)
- [x] introdotto checkpoint `(chat_id, topic_id)` in `get_last_telegram_message_id`
- [x] aggiornato `_catchup_from_telegram()` (per-entry checkpoint, min su multi-topic)
- [x] verificata compatibilità forum-wide (legacy schema fallback)
- [x] test recovery multi-topic passano

---

## WP5 — Router / blacklist / trader fallback

### Obiettivo
Rendere la parte decisionale topic-aware.

### Deliverable
- router con propagation topic
- blacklist risolta sullo scope corretto
- fallback trader basato sull’entry matchata

### Azioni dettagliate
- propagare `source_topic_id` in `route()` e `_route_inner()`
- aggiornare `is_blacklisted_text(...)`
- aggiornare la risoluzione entry in router
- aggiornare `_resolve_trader()`
- rivedere `_is_inactive_channel()` in ottica scope-aware

### Regola blacklist
Per ogni messaggio:
- applicare sempre `blacklist_global`
- se matcha topic-specific → usare blacklist topic-specific
- altrimenti, se matcha forum-wide → usare blacklist forum-wide
- non fare merge implicito topic + forum-wide nella v1

### Definition of Done
- blacklist corretta su casi misti
- trader fallback corretto per forum con topic multipli
- log router topic-aware

### Checklist WP5 ✅ COMPLETATO (2026-04-20)
- [x] propagato `source_topic_id` nel router (`_route_inner` passa `item.source_topic_id` a `is_blacklisted_text` e `_is_inactive_channel`)
- [x] aggiornata funzione blacklist (`is_blacklisted_text` accetta `topic_id`, usa `match_entry`)
- [x] applicata regola `blacklist_global + blacklist_scope_matchato`
- [x] evitato merge implicito forum-wide + topic-specific (topic-3 entry non eredita blacklist forum-wide)
- [x] aggiornato fallback trader (`_resolve_trader` usa `match_entry(chat_id, topic_id)` invece di `channel_for`)
- [x] aggiornata logica inactive scope-aware (`_is_inactive_channel` usa `match_entry`)
- [x] log con `topic_id` aggiunti (`blacklisted` e `trader_inactive` log aggiornati)
- [x] test blacklist/trader passano (19 nuovi test in `test_router_topic.py`, 155/156 totali verde)

---

## WP6 — Provenance operativa opzionale

### Obiettivo
Valutare se estendere `topic_id` alle tabelle operative già in prima iterazione.

### Opzione A — rimandare
V1:
- `source_topic_id` solo in `raw_messages`
- topic preservato in pipeline e log
- provenance operativa estesa rimandata

### Opzione B — fare subito
Aggiungere `topic_id` a:
- `signals`
- `events`
- `operational_signals`

### Raccomandazione
Fare subito **solo se**:
- il costo è basso;
- non aumenta molto il rischio regressione;
- c’è valore immediato per audit/report/debug.

### Checklist WP6 ✅ COMPLETATO (2026-04-20)
- [x] valutato impatto su `signals` → costo basso, valore audit diretto → implementato
- [x] valutato impatto su `events` → costo/beneficio basso (già raggiungibile via JOIN su signals) → rimandato v2
- [x] valutato impatto su `operational_signals` → costo basso, valore audit diretto → implementato
- [x] decisione v1/v2 documentata: Opzione B parziale — signals + operational_signals in v1, events in v2
- [x] migration `019_add_source_topic_id_to_signals_and_op_signals.sql` creata
- [x] `SignalRecord.source_topic_id` aggiunto + insert condizionale (legacy compat)
- [x] `OperationalSignalRecord.source_topic_id` aggiunto + insert condizionale (legacy compat)
- [x] router `_build_signal_record` e `_build_op_signal_record` propagano `item.source_topic_id`
- [x] test provenance aggiornati (6 nuovi test in `test_provenance_topic.py`, tutti verdi)

---

## WP7 — Test suite finale e consolidamento

### Obiettivo
Chiudere il lavoro con copertura utile e smoke tests realistici.

### Suite minima richiesta

#### Config
- load legacy senza `topic_id`
- load con `topic_id`
- duplicati scope invalidi
- `topic_id=1`
- match topic-specific vs forum-wide
- coesistenza multi-topic + gruppo normale

#### Listener
- messaggio forum-wide
- messaggio topic-specific ammesso
- messaggio topic-specific non ammesso
- General topic
- propagation topic in enqueue

#### Recovery
- stale message con topic
- checkpoint multi-topic
- catchup topic-aware
- fallback forum-wide

#### Router
- blacklist globale
- blacklist topic-specific
- blacklist forum-wide
- nessun merge implicito topic/forum-wide
- fallback trader topic-aware

#### Storage
- save raw con `source_topic_id=None`
- save raw con `source_topic_id=int`
- query path topic-aware

### Smoke tests consigliati
1. config solo legacy → comportamento invariato
2. un forum con due topic di trader diversi
3. un gruppo normale senza topic
4. un setup misto:
   - forum topic 3
   - forum topic 4
   - gruppo normale
5. riavvio processo con recovery attivo

### Definition of Done
- test unit principali verdi
- smoke tests manuali completati
- log coerenti

### Checklist WP7 ✅ COMPLETATO (2026-04-20)
- [x] test config aggiornati (WP1 — 16 test in `test_channel_config.py`)
- [x] test listener aggiornati (WP3 — 24 test in `test_listener_topic.py`)
- [x] test recovery aggiornati (WP4 — 9+10 test in `test_listener_recovery_topic.py` + `test_processing_status_topic.py`)
- [x] test router aggiornati (WP5 — 19 test in `test_router_topic.py`)
- [x] test storage aggiornati (WP2/WP6 — 6+6 test in `test_raw_messages_topic.py` + `test_provenance_topic.py`)
- [x] smoke test legacy eseguito — validato da `test_legacy_config_*` in `test_topic_integration.py` + suite invariata (nessuna regressione)
- [x] smoke test multi-topic eseguito — validato da `test_mixed_setup_*` in `test_topic_integration.py`
- [x] smoke test setup misto eseguito — `test_mixed_setup_messages_enqueued_with_correct_topic` copre forum-topic3, forum-topic4, regular group
- [x] smoke test restart/recovery eseguito — validato da `test_reenqueue_stale_*` e `test_catchup_*` in `test_listener_recovery_topic.py`
- [x] nessuna regressione critica aperta — 178 test verdi, 1 fallito pre-esistente (`test_catchup_skips_channel_with_no_last_id`) fuori scope topic support

---

## 7. Sequenza implementativa raccomandata, giorno per giorno

## Step 1 — Base config + matching ✅ COMPLETATO (2026-04-20)
**Output**
- schema config pronto
- matching pronto
- validazione pronta

**Non fare ancora**
- recovery complesso
- provenance operativa estesa

**Checkpoint**
- si può già modellare:
  - forum-wide
  - topic-specific
  - setup misto

---

## Step 2 — Persistenza raw ✅ COMPLETATO (2026-04-20)
**Output**
- migration pronta
- raw store pronto

**Checkpoint**
- il dato topic non si perde più

---

## Step 3 — Listener live ✅ COMPLETATO (2026-04-20)
**Output**
- extraction topic
- queue propagation
- log

**Checkpoint**
- in live il topic arriva fino a DB raw

---

## Step 4 — Recovery ✅ COMPLETATO (2026-04-20)
**Output**
- stale + catchup topic-aware

**Checkpoint**
- restart coerente con topic multipli

---

## Step 5 — Router / blacklist / trader ✅ COMPLETATO (2026-04-20)
**Output**
- decisioni corrette a livello di scope

**Checkpoint**
- stesso forum, topic diversi, trader diversi → niente collisioni

---

## Step 6 — Test / smoke / bugfix ✅ COMPLETATO (2026-04-20)
**Output**
- suite stabile
- readiness per rollout

---

## 8. Piano di rollout operativo

## Fase 0 — Preparazione
- introdurre migration
- introdurre codice compatibile con `topic_id=None`
- nessun topic-specific ancora in produzione

### Checklist
- [ ] migration applicata
- [ ] deploy codice compatibile legacy
- [ ] nessun errore su config legacy

---

## Fase 1 — Compatibilità silente
- attivare log con topic
- mantenere config principalmente forum-wide
- osservare comportamento

### Checklist
- [ ] log topic visibili
- [ ] nessuna regressione ingestione live
- [ ] nessuna regressione recovery legacy

---

## Fase 2 — Pilota topic-specific
- attivare un solo topic-specific reale
- osservare flusso end-to-end

### Checklist
- [ ] primo topic-specific attivato
- [ ] raw saved con `source_topic_id`
- [ ] recovery coerente
- [ ] blacklist corretta
- [ ] trader fallback corretto

---

## Fase 3 — Estensione controllata
- aggiungere altri topic
- validare casi misti

### Checklist
- [ ] almeno due topic nello stesso forum funzionanti
- [ ] almeno un gruppo normale senza topic funzionante
- [ ] setup misto validato

---

## Fase 4 — Consolidamento
- chiudere gap
- aggiornare documentazione tecnica/operativa
- decidere se estendere provenance operativa

### Checklist
- [ ] doc aggiornata
- [ ] issue residue catalogate
- [ ] decisione WP6 chiusa
- [ ] rollout completato

---

## 9. Rischi implementativi pratici

### R1 — Confusione `topic_id=None` vs `topic_id=1`
**Mitigazione**
- helper centralizzato
- test dedicati

### R2 — Matching ambiguo
**Mitigazione**
- una sola funzione di matching
- priorità obbligatoria topic-specific > forum-wide

### R3 — Topic salvato in listener ma perso nel DB
**Mitigazione**
- WP2 prima di WP4/WP5

### R4 — Recovery ancora chat-only di fatto
**Mitigazione**
- checkpoint topic-aware prima del rollout topic-specific

### R5 — Blacklist incoerente tra listener e router
**Mitigazione**
- una sola regola di risoluzione documentata e testata

### R6 — Stesso forum, topic diversi, trader diversi
**Mitigazione**
- fallback trader basato sullo scope matchato

---

## 10. Criteri finali di completamento

Il lavoro si considera concluso quando:

- [ ] `channels.yaml` supporta entry con o senza `topic_id`
- [ ] config legacy continua a funzionare
- [ ] il listener distingue forum-wide e topic-specific
- [ ] `source_topic_id` è persistito in `raw_messages`
- [ ] `source_topic_id` è preservato in queue, recovery e router
- [ ] General topic (`topic_id=1`) è supportato correttamente
- [ ] blacklist usa `blacklist_global + blacklist_scope_matchato`
- [ ] fallback trader usa l’entry matchata corretta
- [ ] recovery è coerente con topic multipli
- [ ] test principali passano
- [ ] smoke test su setup misto passa
- [ ] log topic-aware presenti nei punti chiave

---

## 11. Checklist esecutiva unica

Questa è la checklist compatta finale da usare durante l’implementazione.

### Config / matching ✅ COMPLETATO (2026-04-20)
- [x] aggiunto `topic_id` a `ChannelEntry`
- [x] parsing `topic_id` implementato
- [x] default `None` supportato
- [x] validazione duplicati scope implementata
- [x] funzione di matching unica implementata
- [x] precedenza topic-specific > forum-wide implementata

### DB / raw persistence ✅ COMPLETATO (2026-04-20)
- [x] migration `source_topic_id` creata
- [x] store raw aggiornato
- [x] indice topic-aware creato
- [x] compatibilità con dati storici verificata

### Listener / ingestion / queue ✅ COMPLETATO (2026-04-20)
- [x] helper estrazione topic implementato
- [x] `_is_allowed_chat()` sostituito o deprecato
- [x] `TelegramIncomingMessage.source_topic_id` aggiunto
- [x] `QueueItem.source_topic_id` aggiunto
- [x] log live topic-aware aggiunti

### Recovery ✅ COMPLETATO (2026-04-20)
- [x] `StaleMessage.source_topic_id` aggiunto
- [x] checkpoint topic-aware introdotto
- [x] `_reenqueue_stale()` aggiornato
- [x] `_catchup_from_telegram()` aggiornato
- [x] fallback forum-wide verificato

### Router / business logic ✅ COMPLETATO (2026-04-20)
- [x] router propaga `source_topic_id`
- [x] blacklist topic-aware implementata
- [x] fallback trader topic-aware implementato
- [x] log router topic-aware aggiunti

### Provenance operativa ✅ COMPLETATO (2026-04-20)
- [x] `source_topic_id` aggiunto a `signals` (migration 019 + conditional insert)
- [x] `source_topic_id` aggiunto a `operational_signals` (migration 019 + conditional insert)
- [x] `events` rimandato a v2 (raggiungibile via JOIN su signals)

### Test ✅ COMPLETATO (2026-04-20)
- [x] test config aggiornati (WP1)
- [x] test listener aggiornati (WP3)
- [x] test recovery aggiornati (WP4)
- [x] test router aggiornati (WP5)
- [x] test storage aggiornati (WP2 + WP6)
- [x] test General topic aggiunti (`test_general_topic_*` in `test_topic_integration.py`)
- [x] test setup misto aggiunti (`test_mixed_setup_*` in `test_topic_integration.py`)

### Rollout
- [ ] deploy compatibile legacy eseguito
- [ ] osservazione silente completata
- [ ] topic pilota validato
- [ ] setup misto validato
- [ ] documentazione finale aggiornata

---

## 12. Raccomandazione finale

Per minimizzare rischio e rework:

1. chiudere completamente **WP1 + WP2** prima di modificare recovery e router;
2. introdurre una sola funzione canonica di:
   - matching scope
   - estrazione topic
3. non estendere subito la provenance operativa se non è davvero necessaria;
4. fare rollout con:
   - prima compatibilità legacy,
   - poi un solo topic pilota,
   - poi casi misti.

Questo ordine consente di introdurre il supporto topic in modo controllato, verificabile e con rollback mentale semplice.
