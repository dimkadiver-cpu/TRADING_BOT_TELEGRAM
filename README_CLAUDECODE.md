# README_CLAUDECODE.md

## Scopo

Questo file descrive come usare Claude Code nel progetto TeleSignalBot.

## Documentazione da leggere prima di iniziare

Claude Code legge sempre in questo ordine all'inizio di ogni sessione:

1. `CLAUDE.md` — contesto generale del progetto
2. `docs/AUDIT.md` — stato attuale, file da toccare/non toccare
3. `docs/PRD_*.md` — PRD del layer su cui si sta lavorando
4. `skills/[skill-pertinente]/SKILL.md` — convenzioni specifiche del task

## Ruoli logici — sessioni separate

Ogni sessione ha un obiettivo singolo e ben definito. Non cercare di fare tutto in una sessione.

| Ruolo | Quando usarlo | Skill da leggere |
|---|---|---|
| `parser-engineer` | implementare o migrare un profilo trader | `build-parser-profile`, `pydantic-models`, `rules-engine` |
| `debug-engineer` | migliorare CSV debug e watch mode | `debug-csv-watchmode` |
| `architecture-reviewer` | capire struttura prima di intervenire | `map-trading-bot`, `project-architecture` |
| `ingestion-engineer` | lavorare su listener o router | `telegram-ingestion` |
| `qa-reviewer` | verificare regressioni prima di commit | `qa-parser-regression` |
| `docs-handoff` | aggiornare documentazione a fine sessione | `handoff-trading-bot` |

## Sessione 0 — Audit iniziale (fare una volta sola)

```
Leggi CLAUDE.md.
Leggi docs/AUDIT.md.
Leggi docs/PRD_generale.md.

Fai un audit del progetto:
1. Mappa struttura cartelle esistente
2. Classifica ogni file in src/parser/ come KEEP | REWRITE | DELETE | NEW
3. Verifica quali test esistono e passano con: pytest src/parser/trader_profiles/ -v
4. Identifica conflitti tra architettura attuale e nuova
5. Aggiorna docs/AUDIT.md con stato aggiornato

Non modificare nessun file di codice in questa sessione.
Solo analisi e aggiornamento AUDIT.md.
Al termine usa la skill handoff-trading-bot.
```

## Template prompt — inizio sessione standard

```
Leggi CLAUDE.md.
Leggi docs/AUDIT.md.
Leggi docs/PRD_parser.md.
Leggi skills/[skill-pertinente]/SKILL.md.

Obiettivo di questa sessione: [descrizione chiara e singola]

File da creare/modificare:
- [lista esplicita]

File da NON toccare:
- src/parser/pipeline.py (legacy in produzione)
- src/storage/
- src/core/
- db/migrations/

Al termine:
- aggiorna docs/AUDIT.md con step completato
- usa la skill handoff-trading-bot
```

## Prompt per ogni step del parser

### Step 1 — Pydantic models

```
Leggi CLAUDE.md, docs/PRD_parser.md, skills/pydantic-models/SKILL.md.

Implementa i modelli Pydantic canonici del parser.

File da creare:
- src/parser/models/__init__.py
- src/parser/models/canonical.py  (Price, Intent, TargetRef, TraderParseResult)
- src/parser/models/new_signal.py (NewSignalEntities, EntryLevel, StopLoss, TakeProfit)
- src/parser/models/update.py     (UpdateEntities)

Requisiti:
- Price normalizza raw string → float secondo number_format del profilo
- Price preserva sempre raw originale per audit
- TraderParseResult ha entities come Union[NewSignalEntities, UpdateEntities, None]
- tutti i campi opzionali usano None come default, mai stringa vuota

Non toccare nessun file esistente.
Aggiungi test unit per normalizzazione Price con tutti i formati numerici.
Al termine usa handoff-trading-bot.
```

### Step 2 — RulesEngine

```
Leggi CLAUDE.md, docs/PRD_parser.md, skills/rules-engine/SKILL.md.

Implementa il RulesEngine.

File da creare/modificare:
- src/parser/rules_engine.py
- src/parser/trader_profiles/shared/russian_trading.json
- src/parser/trader_profiles/shared/english_trading.json

Requisiti:
- RulesEngine.load(path) carica parsing_rules.json e merge con shared vocabulary
- RulesEngine.classify(text, context) → ClassificationResult
- ClassificationResult ha: message_type, confidence, matched_markers, target_ref_hints
- logica confidence: strong * 1.0 + weak * 0.4 + context boost (reply +0.4)
- UNCLASSIFIED se max score < 0.3

Non toccare pipeline.py o normalization.py.
Al termine usa handoff-trading-bot.
```

### Step 3 — Trader 3 profilo

```
Leggi CLAUDE.md, docs/PRD_parser.md, skills/build-parser-profile/SKILL.md.

Implementa il profilo Trader 3 con la nuova architettura.
Trader 3 è il profilo di riferimento — deve essere completo e ben testato.

File da creare/modificare:
- src/parser/trader_profiles/trader_3/parsing_rules.json (riscrivere)
- src/parser/trader_profiles/trader_3/profile.py (riscrivere)
- src/parser/trader_profiles/trader_3/tests/test_profile_real_cases.py

Caratteristiche Trader 3:
- lingua: inglese
- formato strutturato con SIGNAL ID esplicito (#1997 ecc.)
- target_ref STRONG via EXPLICIT_ID
- ENTRY come zona (due prezzi min-max)
- TARGETS come lista prezzi separati da trattino

Verifica su dati reali: python parser_test/scripts/replay_parser.py --trader trader_3
Al termine aggiorna AUDIT.md e usa handoff-trading-bot.
```

### Step 4 — Watch mode + CSV debug

```
Leggi CLAUDE.md, docs/PRD_parser.md, skills/debug-csv-watchmode/SKILL.md.

Implementa il sistema di debug con watch mode.

File da creare/modificare:
- parser_test/scripts/watch_parser.py     (NUOVO)
- parser_test/reporting/report_schema.py  (aggiornare colonne)
- parser_test/reporting/flatteners.py     (adattare a nuovo output)
- parser_test/scripts/replay_parser.py   (aggiungere logging errori)

Requisiti watch mode:
- watchdog monitora parsing_rules.json e profile.py del trader attivo
- debounce 2 secondi
- su modifica → replay automatico → CSV aggiornato

Colonne CSV UPDATE richieste:
- intents_context, intents_action, intents_missing
- target_ref_kind, target_ref_method, target_ref_value
- entities_ok, entities_missing

Separatore liste: | — Encoding: UTF-8-sig
Al termine usa handoff-trading-bot.
```

### Step 5+ — Migrazione profili

```
Leggi CLAUDE.md, docs/PRD_parser.md, skills/build-parser-profile/SKILL.md.
Leggi anche src/parser/trader_profiles/trader_3/ come profilo di riferimento.

Migra il profilo [trader_X] alla nuova architettura.

File da creare/modificare:
- src/parser/trader_profiles/trader_X/parsing_rules.json
- src/parser/trader_profiles/trader_X/profile.py
- src/parser/trader_profiles/trader_X/tests/test_profile_real_cases.py

Verifica che i test esistenti passino ancora.
Verifica su dati reali: python parser_test/scripts/replay_parser.py --trader trader_X
Al termine aggiorna AUDIT.md e usa handoff-trading-bot.
```

## Skills disponibili

```
skills/project-architecture/     → confini architetturali
skills/telegram-ingestion/        → listener e router
skills/build-parser-profile/      → costruire profili trader
skills/pydantic-models/           → modelli canonici Pydantic
skills/rules-engine/              → RulesEngine e parsing_rules.json
skills/debug-csv-watchmode/       → sistema debug CSV e watch mode
skills/generate-trader-parser-from-dataset/  → da dataset a parser
skills/qa-parser-regression/      → verifica regressioni
skills/handoff-trading-bot/       → documentazione fine sessione
skills/map-trading-bot/           → mappatura codebase
skills/position-lifecycle/        → ciclo vita posizioni (Fase 5+)
```

## Regole operative

- **Un obiettivo per sessione** — non fare due step in una sessione
- **Leggi prima** — sempre CLAUDE.md + AUDIT.md + PRD + skill pertinente
- **Handoff alla fine** — sempre skill handoff-trading-bot a fine sessione
- **Non toccare legacy** — pipeline.py e normalization.py restano intatti durante migrazione
- **Aggiorna AUDIT.md** — segna ogni step completato

## Fine sessione — checklist

- [ ] obiettivo completato e testato
- [ ] AUDIT.md aggiornato con step completato
- [ ] handoff-trading-bot eseguita
- [ ] nessun file legacy toccato
- [ ] test passano: `pytest src/parser/trader_profiles/ -v`
