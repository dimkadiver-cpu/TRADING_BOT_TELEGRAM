# README_CODEX.md

## Scopo
Questo file descrive come usare Codex App nel progetto trading bot.

## Obiettivo operativo
Usare Codex come team di agenti specializzati per:
- capire il codebase
- costruire parser trader-specific
- gestire lifecycle della posizione
- verificare regressioni
- mantenere handoff e documentazione

## Agenti consigliati
Usare questi ruoli logici come thread separati:

1. architect-trading
2. cartographer-trading
3. parser-engineer
4. lifecycle-engineer
5. qa-backtest-reviewer
6. docs-handoff

## Thread consigliati in Codex App
- Local: architect-trading
- Worktree A: cartographer-trading
- Worktree B: parser-engineer
- Worktree C: lifecycle-engineer
- Worktree D: qa-backtest-reviewer
- Local o Worktree leggero: docs-handoff

## Skills disponibili
Le skill del progetto si trovano in:
- skills/map-trading-bot
- skills/build-parser-profile
- skills/position-lifecycle
- skills/qa-parser-regression
- skills/handoff-trading-bot

## Regole pratiche
- Prima di cambiare codice, usare map-trading-bot se il contesto non è chiaro
- Per ogni nuovo trader, usare build-parser-profile
- Per update su stato posizione, usare position-lifecycle
- Prima del merge o commit, usare qa-parser-regression
- Dopo task significativi, usare handoff-trading-bot

## Prompt iniziali consigliati

### 1. Analisi progetto
Usa la skill map-trading-bot.
Analizza questo codebase e restituisci:
- mappa moduli
- entry points
- pipeline dati
- file sospetti o obsoleti
- punti sicuri di intervento per parser e lifecycle.

### 2. Nuovo parser trader
Usa la skill build-parser-profile.
Costruisci o aggiorna il parser del trader indicato.
Devi:
- classificare il messaggio
- estrarre entità
- definire regole di linking
- produrre output canonico
- elencare casi ambigui e test minimi.

### 3. Stato posizione
Usa la skill position-lifecycle.
Analizza come gli eventi canonici aggiornano lo stato posizione.
Restituisci:
- stati ammessi
- transizioni
- validazioni
- edge case
- test necessari.

### 4. QA
Usa la skill qa-parser-regression.
Verifica regressioni nel parser, linking e lifecycle.
Produci report con:
- problemi
- severità
- file coinvolti
- test mancanti
- fix suggeriti.

### 5. Handoff
Usa la skill handoff-trading-bot.
Aggiorna la documentazione operativa del task appena completato con:
- changelog sintetico
- file toccati
- comportamento nuovo
- rischi aperti
- prompt per la prossima sessione.

## Convenzioni consigliate
- Ogni nuovo trader deve avere modulo dedicato
- Le shared utils vanno separate dai pattern trader-specific
- Il DB deve conservare raw message e metadata di parsing
- Le metriche backtest devono dipendere dagli eventi canonici

## Nota su local environments
Configurare nell’app:
- setup script per installazione dipendenze
- azioni comuni per test parser, lifecycle e backtest
- eventuale comando listener

## Nota su automations
Automazioni utili:
- parser regression giornaliera
- controllo coerenza backtest settimanale
- handoff report periodico