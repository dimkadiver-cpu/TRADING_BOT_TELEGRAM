---
name: project-architecture
description: Usa questa skill per capire o preservare l’architettura del trading bot, i confini tra i moduli e i punti di inserimento sicuri per nuove funzionalità.
---

# Obiettivo
Mantenere chiara l’architettura reale del progetto e impedire modifiche che mischiano livelli diversi del sistema.

# Quando usarla
- prima di introdurre nuove feature
- prima di refactor strutturali
- quando serve capire dove agganciare un nuovo modulo
- quando si vuole evitare coupling errato tra parser, lifecycle, execution e storage

# Architettura attuale
Il flusso runtime attuale è:

Telegram
↓
raw_messages
↓
parser
↓
parse_results

Entrypoint runtime:
- main.py
- src/telegram/listener.py

Moduli attivi principali:
- src/telegram/
- src/parser/pipeline.py
- src/parser/trader_profiles/
- src/storage/raw_messages.py
- src/storage/parse_results.py
- src/core/

# Architettura target
Il progetto è pensato per evolvere verso un modello event-driven:

Telegram
↓
raw_messages
↓
parser
↓
parse_results
↓
lifecycle
↓
execution planning
↓
exchange integration

# Confini architetturali
## Telegram ingestion
Responsabile di:
- ricezione messaggi
- filtro chat ammesse
- trader resolution
- eligibility
- chiamata al parser
- persistenza iniziale

Non deve contenere logica di execution o parser trader-specific complesso.

## Parser
Responsabile di:
- classificazione messaggio
- estrazione campi
- normalizzazione output
- routing trader-specific

Non deve:
- eseguire ordini
- decidere stato operativo del trade
- accedere direttamente all’exchange

## Storage
Responsabile di:
- persistenza raw_messages
- persistenza parse_results
- audit persistente

Non deve contenere business logic.

## Lifecycle
Responsabile di:
- interpretare eventi parser
- applicare stati e transizioni
- tenere audit trail
- verificare applicabilità degli update

Non deve contenere parsing del testo grezzo.

## Execution
Responsabile di:
- trasformare stati/eventi in intenzioni operative
- applicare risk rules
- costruire ordini

Non deve leggere Telegram direttamente.

## Exchange
Responsabile di:
- invio ordini
- stato ordini
- riconciliazione
- integrazione API exchange

Non deve contenere parsing o lifecycle.

# Regole
- non mischiare parsing e execution
- non inserire logica exchange dentro listener o parser
- non spostare business logic nello storage
- preferire nuovi moduli o servizi dedicati invece di gonfiare main.py o listener.py
- ogni nuovo step operativo dopo parse_results deve stare dietro un boundary chiaro

# Punti di inserimento sicuri
- trader-specific parser router: src/parser/pipeline.py
- lifecycle orchestration: subito dopo parse_results_store.upsert(...) in src/telegram/listener.py, delegando a un service separato
- execution integration: dietro un servizio dedicato, non direttamente nel parser
- reporting progetto: modulo read-only sopra storage attuale

# Rischi architetturali noti
- coesistenza tra schema legacy e flow runtime attuale
- parser minimale e stack parser futuro potenzialmente divergenti
- execution e exchange ancora placeholder
- rischio di inserire business logic nel posto sbagliato per velocità

# Output richiesto
Quando usi questa skill, restituisci sempre:
- boundary coinvolti
- punto migliore di inserimento
- file da toccare
- rischi architetturali
- alternativa minima e alternativa più pulita