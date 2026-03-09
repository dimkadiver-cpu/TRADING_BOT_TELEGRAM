Stiamo lavorando sul progetto TeleSignalBot e voglio continuare da uno stato già avanzato, senza ripartire da zero.

## Stato generale
Abbiamo già fatto analisi, riallineamento repo e documentazione, poi implementazione progressiva con Codex.

## Stato roadmap attuale
- Fase 0: COMPLETATA
- Fase 1: COMPLETATA
- Fase 2: IN CORSO
- Fase 3: VALIDATA
- Fase 4: IMPLEMENTATA a livello minimo, ma da VALIDARE con test reali
- Fase 5+: non ancora implementate

## Architettura chiave già decisa
Il progetto riceve messaggi Telegram e li trasforma in oggetti interni per trading.

Pipeline logica attuale:
1. raw ingestion
2. eligibility filter
3. trader resolution
4. intent classification
5. entity extraction
6. linkage resolution
7. parse_result persistence
8. poi in futuro policy / planner / state machine / exchange

## Realtà importante del progetto
C’è almeno un canale Telegram multi-trader:
- una sola sorgente Telegram
- più trader dentro lo stesso canale
- i trader sono identificati nel testo del messaggio con tag tipo:
  - [trader#A]
  - [trader#B]
  - [trader#3]

Quindi:
- `source_chat_id` NON identifica da solo il trader
- il trader effettivo va risolto da:
  1. tag nel messaggio
  2. reply inheritance dal parent
  3. source fallback solo se davvero mono-trader

## Decisioni chiave già prese
### Eligibility filter
Serve per escludere messaggi non operativi, per esempio:
- admin
- statistiche
- recap
- service

Tutti i messaggi vengono comunque salvati come raw.

### Trader resolution
Campi logici importanti:
- `source_chat_id`
- `declared_trader_tag`
- `resolved_trader_id`
- `trader_resolution_method`

Metodi:
- `DIRECT_TAG`
- `REPLY_INHERIT`
- `SOURCE_DEFAULT`
- `UNRESOLVED`

### Linkage policy
Per update brevi tipo:
- cancel
- close
- breakeven
- move sl

auto-applicazione consentita solo con strong link:
- `REPLY`
- `MESSAGE_LINK`
- `EXPLICIT_MESSAGE_ID`

Non con contesto debole.

### Validazione nuovi segnali
Un `NEW_SIGNAL` è completo solo se ha:
- symbol
- direction
- entry
- stop
- almeno un target

Altrimenti diventa `SETUP_INCOMPLETE`.

## Fase 3 implementata e validata
Codex ha implementato:
- listener Telegram
- persistenza `raw_messages`
- deduplica minima
- filtro `TELEGRAM_ALLOWED_CHAT_IDS`
- mapping sorgente
- supporto chat multi-trader
- .env runtime funzionante

Abbiamo già verificato live:
- filtro canale ok
- raw ingestion ok
- deduplica ok

## Fase 4 implementata da Codex
Codex ha implementato:
- schema DB `parse_results`
- persistenza `parse_results`
- parser minimo
- classificazione minima:
  - `NEW_SIGNAL`
  - `SETUP_INCOMPLETE`
  - `UPDATE`
  - `INFO_ONLY`
  - `UNCLASSIFIED`
- estrazione campi base:
  - symbol
  - direction
  - entry
  - stop
  - targets
  - leverage hint
  - risk hint
  - risky flag
- validazione minima:
  - `NEW_SIGNAL` solo se completo
  - altrimenti `SETUP_INCOMPLETE`
- reply breve senza tag:
  - trader ereditato dal parent
  - classificato `UPDATE`
  - non eseguibile
- admin message con trader tag:
  - non operativo

## File/logica che sono stati riallineati nei docs
Abbiamo già sistemato documentazione come:
- `docs/MASTER_PLAN.md`
- `docs/PARSER_FLOW.md`
- `docs/DB_SCHEMA.md`
- `docs/TASKS.md`
- `docs/CONFIG_SCHEMA.md`
- `docs/ROADMAP.md`
- `docs/TRADE_STATE_MACHINE.md`
- `docs/SYSTEM_ARCHITECTURE.md`

## Stato attuale reale
Siamo nella fase di TEST REALE della Fase 4.
Stiamo aspettando messaggi reali dal canale per verificare che il parser minimo funzioni davvero.

## Casi che vogliamo validare ora
1. segnale completo con tag trader
   atteso:
   - `NEW_SIGNAL`
   - `resolved_trader_id` corretto
   - `is_executable = true`

2. setup incompleto
   atteso:
   - `SETUP_INCOMPLETE`
   - `is_executable = false`

3. reply breve senza tag
   atteso:
   - trader ereditato
   - `UPDATE`
   - non eseguibile
   - linkage valorizzato

4. admin/statistiche
   atteso:
   - non operativo
   - no promozione a segnale

5. messaggio ambiguo
   atteso:
   - `INFO_ONLY` o `UNCLASSIFIED`
   - non operativo

## Quando rispondi
Non ricominciare da zero.
Parti da questo stato e aiutami a:
- validare i casi reali
- interpretare i record in `parse_results`
- decidere se Fase 4 è `VALIDATA`
- poi eventualmente preparare la Fase 5 (matching update -> trade)