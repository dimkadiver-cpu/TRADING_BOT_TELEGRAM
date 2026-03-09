# AGENTS.md - Trading Bot

## Mission
Questo progetto trasforma messaggi Telegram di trader diversi in eventi canonici di trading,
li collega al segnale corretto e aggiorna lo stato della posizione in modo coerente e verificabile.

## Working style
- Preferire cambi piccoli e localizzati
- Non fare refactor ampi senza richiesta esplicita
- Separare chiaramente ingestione, parsing, linking, lifecycle, storage e backtest
- Ogni modifica deve essere spiegabile e testabile
- Quando il dato è ambiguo, segnalarlo invece di inventarlo

## Canonical event types
I tipi evento ammessi sono:

- NEW_SIGNAL
- UPDATE
- CANCEL_PENDING
- MOVE_STOP
- TAKE_PROFIT
- CLOSE_POSITION
- INFO_ONLY
- SETUP_INCOMPLETE
- INVALID

Non introdurre nuove categorie senza necessità reale.

## Canonical event schema
Ogni parser e ogni fase di normalizzazione deve convergere su un output canonico unico.
I campi minimi attesi sono:

- event_type
- trader_id
- source_chat_id
- source_message_id
- raw_text
- parser_mode
- confidence
- instrument
- side
- market_type
- entries
- stop_loss
- take_profits
- root_ref
- status

## Parsing principles
- Separare classificazione messaggio da estrazione entità
- Separare logica trader-specific da utility condivise
- Preferire parser deterministici e pattern chiari
- Usare fallback LLM solo se il progetto lo prevede esplicitamente
- Se un campo non è certo, marcarlo come ambiguo o incompleto
- Non inferire prezzi o livelli mancanti senza evidenziarlo

## Linking priorities
Quando un messaggio deve essere collegato a un segnale originario, usare questa priorità:

1. reply_to_message_id
2. explicit Telegram message link
3. heuristics per trader + strumento + finestra temporale

Se il linking è debole:
- non forzare silent success
- abbassare confidence
- marcare il caso come ambiguo

## Lifecycle principles
- Nessuna transizione implicita
- Ogni update va applicato solo a uno stato compatibile
- Cancellare ordini pendenti non significa chiudere una posizione
- Chiusure parziali e totali devono restare distinte
- Break-even, trailing stop e partial TP devono essere eventi separati se il dominio li distingue

## DB and audit
- Conservare sempre raw_text e riferimenti al messaggio sorgente
- Conservare parser_mode e confidence
- Conservare audit trail degli eventi applicati
- Evitare mutazioni distruttive senza storico

## Backtest principles
- Il backtest deve basarsi su eventi canonici, non sul testo grezzo
- La latenza va modellata esplicitamente se disponibile
- Le assunzioni sul fill model devono essere dichiarate
- Le metriche finali devono distinguere segnali validi, incompleti, invalidi e non eseguiti

## What to return after each task
Quando completi un task, restituisci sempre:

1. cosa hai cambiato
2. file toccati
3. rischi residui
4. test eseguiti o mancanti
5. eventuali follow-up consigliati

## What not to do
- Non mischiare parsing, lifecycle e reporting nello stesso modulo senza motivo
- Non rinominare grandi porzioni di progetto solo per gusto personale
- Non aggiungere dipendenze pesanti senza giustificazione
- Non cancellare codice legacy se prima non è chiaro se serve ancora