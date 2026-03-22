---
name: project-architecture
description: Usa questa skill per capire o preservare l'architettura del trading bot, i confini tra i moduli e i punti di inserimento sicuri per nuove funzionalità.
---

# Obiettivo

Mantenere chiara l'architettura reale del progetto e impedire modifiche che mischiano livelli diversi del sistema.

# Quando usarla

- prima di introdurre nuove feature
- prima di refactor strutturali
- quando serve capire dove agganciare un nuovo modulo
- quando si vuole evitare coupling errato tra layer

# Architettura attuale — flusso completo

```
Telegram channels
      ↓
Listener (Telethon)
  - sessione MTProto esistente
  - config canali da channels.yaml (hot reload watchdog)
  - media ignorati, solo testo
  - recovery al restart da last_message_id per canale
  - acquisition_mode: live | catchup
      ↓
raw_messages (SQLite)
  processing_status: pending → processing → done | failed | blacklisted | review
      ↓
Router / Pre-parser
  - blacklist check
  - risoluzione trader (effective_trader.py)
  - filtro trader attivi/inattivi
  - review_queue per trader non risolti
  - costruisce ParserContext completo
      ↓
Parser
  - RulesEngine legge parsing_rules.json → classificazione
  - profile.py estrae entità + intents
  - Pydantic models normalizzano e validano
  - output: TraderParseResult canonico
      ↓
parse_results (SQLite)
      ↓
Validazione coerenza         [DA IMPLEMENTARE]
      ↓
Operation rules              [DA IMPLEMENTARE]
      ↓
Target resolver              [DA IMPLEMENTARE]
      ↓
Sistema 1 (freqtrade live)   [DA IMPLEMENTARE]
Sistema 2 (backtesting)      [DA IMPLEMENTARE]
```

# Confini architetturali

## Listener
Responsabile di:
- ricezione messaggi Telegram in real-time
- hot reload config canali
- persistenza in raw_messages con processing_status
- recovery messaggi persi al restart
- asyncio.Queue per latenza minima verso il worker

Non deve contenere:
- logica di classificazione messaggi
- logica di execution o exchange
- parsing del testo grezzo

## Router / Pre-parser
Responsabile di:
- blacklist check su raw_text
- risoluzione trader effettivo
- filtro trader attivi/inattivi
- costruzione ParserContext completo (incluso reply_raw_text)
- routing verso profilo parser corretto

Non deve contenere:
- estrazione entità
- logica di execution

## Parser
Responsabile di:
- classificazione messaggio (RulesEngine + parsing_rules.json)
- estrazione entità e intents (profile.py)
- normalizzazione output (Pydantic models)
- produzione TraderParseResult canonico

Non deve contenere:
- logica di execution
- accesso diretto all'exchange
- decisioni su stato operativo del trade

## Storage
Responsabile di:
- persistenza raw_messages
- persistenza parse_results
- audit trail immutabile

Non deve contenere business logic.

## Validazione coerenza
Responsabile di:
- verifica strutturale (entità presenti per intent)
- verifica semantica (almeno un ACTION per UPDATE)
- produzione validation_status: VALID | VALID_WITH_WARNINGS | NEEDS_REVIEW

## Operation rules
Responsabile di:
- regole operative esterne al segnale
- come trattare ZONE, size per AVERAGING
- limiti globali e per trader
- configurabile via YAML senza toccare codice

## Target resolver
Responsabile di:
- risoluzione target_ref in posizioni concrete
- STRONG → ricerca diretta per message_id
- SYMBOL → ricerca posizioni aperte con quel symbol
- GLOBAL → scope trader-wide

## Sistema 1 — freqtrade live
Responsabile di:
- signal bridge (IStrategy custom)
- gestione ciclo vita posizioni
- execution su exchange via ccxt
- FreqUI + Telegram bot controllo

## Sistema 2 — backtesting
Responsabile di:
- replay parse_results storici
- freqtrade backtesting mode
- config matrix runner
- report comparativi

# File da non toccare mai

```
src/storage/          → storage layer stabile
src/core/             → utilities condivise
db/migrations/        → schema DB
src/parser/pipeline.py      → LEGACY in produzione
src/parser/normalization.py → LEGACY in produzione
```

`pipeline.py` e `normalization.py` vanno eliminati SOLO dopo che tutti i profili sono migrati alla nuova architettura e i test passano.

# Punti di inserimento sicuri

- nuovi modelli canonici: `src/parser/models/`
- nuova logica classificazione: `src/parser/rules_engine.py`
- nuovo profilo trader: `src/parser/trader_profiles/trader_X/`
- vocabolario condiviso: `src/parser/trader_profiles/shared/`
- watch mode debug: `parser_test/scripts/`

# Rischi architetturali noti

- pipeline.py legacy coesiste con nuova architettura durante migrazione
- profili vecchi usano output canonico diverso dai nuovi modelli Pydantic
- non mischiare i due sistemi: i profili migrati usano solo i nuovi modelli
- execution e backtesting ancora placeholder — non anticipare interfacce

# Output richiesto

Quando usi questa skill, restituisci sempre:
- boundary coinvolti
- punto migliore di inserimento
- file da toccare
- file da NON toccare
- rischi architetturali
