# Codex Bootstrap Guide

## Scopo

Questo file serve a dare a Codex regole di comportamento chiare quando lavora sul repository.

## Prima di scrivere codice

Codex deve leggere in questo ordine:

1. `README.md`
2. `docs/README.md`
3. `docs/MASTER_PLAN.md`
4. `docs/SYSTEM_ARCHITECTURE.md`
5. `docs/PARSER_FLOW.md`
6. documento tecnico relativo al modulo da implementare

## Regole operative

- Non spostare logica di parsing dentro execution.
- Non leggere il testo Telegram fuori dal parser.
- Non mettere logica exchange dentro state machine.
- Non inventare default nascosti se la configurazione manca.
- Ogni nuovo modulo deve avere uno scopo semplice e unico.
- I placeholder vanno sostituiti gradualmente, non con implementazioni monolitiche.

## Ordine di sviluppo consigliato

1. parser core minimo
2. persistenza segnale
3. risk gate minimo
4. planner minimo
5. state machine
6. adapter exchange testnet

## Cosa evitare

- aggiungere feature speculative non documentate
- implementare strategie di trading nuove
- mescolare la logica dei singoli trader con la logica globale del sistema
- creare dipendenze forti tra moduli che devono restare separati
