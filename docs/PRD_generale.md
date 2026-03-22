# PRD Generale — TeleTrader

## Visione

Sistema di trading automatico che acquisisce segnali da canali Telegram di terzi, li parsa in formato canonico, li esegue su exchange tramite freqtrade (Sistema 1), e li replica in backtesting con configurazioni multiple (Sistema 2).

## Architettura generale

```
Telegram channels
      ↓
Listener (Telethon)
      ↓
Raw messages DB (SQLite)
      ↓
Router / Pre-parser
      ↓
Parser pipeline
      ↓
Parse results DB
      ↓
Validazione coerenza
      ↓
Operation rules
      ↓
Target resolver
      ↓
Sistema 1                    Sistema 2
(live execution)             (backtesting)
freqtrade                    freqtrade BT
      ↓                            ↓
Exchange (ccxt)              Report / analisi
      ↓
FreqUI + Telegram bot
```

## Layer e responsabilità

**Layer 0 — Listener**
Acquisisce messaggi Telegram, gestisce recovery al restart, scrive in `raw_messages` con status `pending`. Usa asyncio.Queue per latenza minima.

**Layer 1 — Router / Pre-parser**
Blacklist check, risoluzione trader, filtro attivi/inattivi, review queue per non risolti, costruisce ParserContext completo.

**Layer 2 — Parser**
Profilo per trader, RulesEngine + profile.py, produce TraderParseResult canonico validato da Pydantic.

**Layer 3 — Validazione coerenza**
Controlla strutturale (entità presenti per intent) e semantica (almeno un ACTION per UPDATE). Produce `validation_status`.

**Layer 4 — Operation rules**
Applica regole operative esterne al segnale — come trattare ZONE, size per AVERAGING, limiti globali. Configurabile per trader e globale via YAML.

**Layer 5 — Target resolver**
Risolve `target_ref` in posizioni concrete dal DB. STRONG → diretto, SYMBOL → cerca posizioni aperte, GLOBAL → scope trader-wide.

**Sistema 1 — Live execution**
freqtrade con IStrategy custom (signal bridge), gestione ciclo di vita posizioni, FreqUI, Telegram bot controllo.

**Sistema 2 — Backtesting**
Replay `parse_results` storici in freqtrade backtesting mode, config matrix runner, report comparativi.

## Tecnologie

| Componente | Tecnologia | Motivazione |
|---|---|---|
| Listener Telegram | Telethon | unico accesso canali terzi |
| Coda messaggi | asyncio.Queue + SQLite processing_status | latenza zero + persistenza |
| Parser | Python + Pydantic v2 | validazione e normalizzazione |
| Profili trader | profile.py + parsing_rules.json | separazione logica/vocabolario |
| DB principale | SQLite → Postgres in produzione | freqtrade supporta entrambi |
| Execution | freqtrade + ccxt | gestione ciclo vita ordini |
| UI controllo | FreqUI | niente UI custom |
| Bot controllo | Telegram bot freqtrade | comandi rapidi |
| File watching | watchdog | hot reload config e debug |
| LLM hook | openai / anthropic / ollama | per trader configurato |

## Principi trasversali

- **Niente si butta** — tutto salvato in DB per audit e miglioramento
- **Parser non decide** — classifica e estrae, le decisioni vengono dopo
- **Config per trader** — ogni trader ha il suo profilo indipendente
- **Migrazione graduale** — un layer alla volta, test prima di passare al successivo
- **Un percorso solo** — niente layer paralleli che si riconciliano

## Stato attuale

```
✓ Listener base (Telethon, scraping storico)
✓ Parser pipeline (da riprogettare)
✓ Parser test harness (da migliorare)
✗ Listener live robusto con asyncio.Queue e recovery
✗ Router / Pre-parser
✗ Pydantic models canonici
✗ RulesEngine
✗ Profili trader nuova architettura
✗ Validazione coerenza
✗ Operation rules
✗ Target resolver
✗ Signal bridge freqtrade
✗ Sistema 2
```

## Ordine di sviluppo

```
Fase 1 — Parser (NOW)
  Step 1  Pydantic models
  Step 2  RulesEngine
  Step 3  Trader 3 profilo (riferimento)
  Step 4  Sistema debug CSV + watch mode
  Step 5  Migrazione altri profili
  Step 6  Eliminazione pipeline.py legacy

Fase 2 — Listener robusto
  Listener live con asyncio.Queue
  Recovery al restart
  Hot reload channels.yaml

Fase 3 — Router / Pre-parser
  Blacklist check
  Trader resolution
  Review queue

Fase 4 — Validazione + Operation rules + Target resolver

Fase 5 — Sistema 1 (freqtrade live)

Fase 6 — Sistema 2 (backtesting)
```
