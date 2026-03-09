# TeleSignalBot

Repository base allineata per sviluppo assistito con Codex.

## Scopo

TeleSignalBot è un motore che riceve segnali da Telegram, li normalizza in un formato interno unico, applica regole di rischio, costruisce un piano operativo ed esegue gli ordini su exchange.

In questa fase il repository non è ancora un prodotto finito. È una base di progetto pensata per:

- mantenere documentazione chiara
- separare bene parser, rischio, esecuzione ed exchange
- ridurre ambiguità prima dello sviluppo massivo
- permettere a Codex di lavorare su file con ruoli chiari

## Flusso logico

1. Il listener riceve un messaggio Telegram.
2. Il sistema riconosce il trader e il tipo di messaggio.
3. Il parser estrae le informazioni utili e costruisce un segnale interno unico.
4. Il risk gate verifica se il trade è consentito.
5. Il planner traduce il segnale in piano operativo.
6. Il precision engine adatta quantità e prezzi alle regole dell'exchange.
7. L'adapter exchange invia gli ordini.
8. La state machine aggiorna il ciclo di vita della posizione.
9. Tutti gli eventi vengono salvati nel database.

## Struttura repository

- `docs/` documentazione di progetto e guida per Codex
- `config/` regole globali del sistema
- `traders/` regole specifiche per singolo trader
- `src/parser/` comprensione e normalizzazione dei messaggi
- `src/execution/` rischio, piano ordini e stati
- `src/exchange/` comunicazione con exchange e riconciliazione
- `src/telegram/` ingest messaggi e bot comandi
- `src/core/` utility condivise
- `db/` database e migrazioni
- `logs/` log runtime

## Punto di partenza documentale

Ordine consigliato di lettura:

1. `docs/README.md`
2. `docs/MASTER_PLAN.md`
3. `docs/SYSTEM_ARCHITECTURE.md`
4. `docs/PARSER_FLOW.md`
5. documenti specifici di supporto

## Stato attuale

Il repository è pronto come base ordinata di sviluppo.

Non tutto è implementato. Molti moduli sono ancora placeholder intenzionali. La priorità adesso è sviluppare un flusso minimo end-to-end pulito, senza mischiare responsabilità tra parser, execution ed exchange.
