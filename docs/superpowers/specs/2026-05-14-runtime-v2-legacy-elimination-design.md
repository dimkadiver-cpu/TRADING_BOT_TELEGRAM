# PRD 2.c — Runtime V2: Eliminazione Legacy e Promozione a Stack Primario

**Data:** 2026-05-14
**Aggiornato:** 2026-06-04
**Stato:** PARZIALMENTE COMPLETATO — vedi §8 per il delta residuo
**Deriva da:** revisione stato runtime_v2 post PRD 2.b
**Precondizione:** PRD 2.b chiuso — `canonical_messages` funzionante, 74/74 test verdi
**Sblocca:** PRD 03 (Operation Rules Engine V2)

---

## 1. Scopo

Eliminare il router legacy e tutto il suo albero di dipendenze. Dopo questo PRD, `main.py` avvia il listener che processa messaggi Telegram attraverso il solo stack runtime_v2, produce `CanonicalMessage` in `canonical_messages`, e logga il risultato. Nessun layer downstream attivo.

**Output finale verificabile:** `main.py` parte → listener riceve messaggio → riga in `canonical_messages` → log con `primary_class` e `parse_status`.

---

## 2. Stato di partenza

### Flusso attuale (al momento della stesura del PRD)

```
main.py
  ├── MessageRouter (1250 righe) — PRIMARY
  │     ├── OperationRulesEngine
  │     ├── TargetResolver
  │     ├── SignalsStore / OperationalSignalsStore
  │     ├── ParseResultStore / ParseResultV1Store / ParsedMessageStore
  │     ├── HistoryBackedIntentValidator
  │     ├── DynamicPairlistManager
  │     ├── TelegramSourceTraderMapper
  │     └── scrive su: parse_results, parse_results_v1, parsed_messages,
  │                    operational_signals, review_queue, signals
  │
  └── RuntimeV2ListenerSidecar (shadow, USE_RUNTIME_V2=1)
        └── RawMessageRepository → ParserPipelineProcessor → canonical_messages
```

### Flusso target (raggiunto)

```
main.py (semplificato)
  └── TelegramListener
        └── RuntimeV2IntakeProcessor → ParserPipelineProcessor → canonical_messages
                                                                  + log
```

---

## 3. Modifiche

### 3.1 `main.py` — riscrittura ✅ COMPLETATO

**Rimosso:** `MessageRouter`, `OperationRulesEngine`, `TargetResolver`, `SignalsStore`,
`OperationalSignalsStore`, `ParseResultStore`, `ParseResultV1Store`, `ParsedMessageStore`,
`HistoryBackedIntentValidator`, `DynamicPairlistManager`, `TelegramSourceTraderMapper`,
`validate_operation_rules_config`, `RuntimeV2ListenerSidecar`, `_configure_shadow_mode`,
env `USE_RUNTIME_V2`, `PARSER_V1_SHADOW_MODE`.

**Costruito:** stack runtime_v2 diretto (vedi main.py attuale).

---

### 3.2 `src/telegram/listener.py` — worker ✅ COMPLETATO

Worker chiama direttamente intake + pipeline. `router` e `sidecar` rimossi come parametri.

---

### 3.3 `src/runtime_v2/listener_sidecar.py` — eliminato ✅ COMPLETATO

File rimosso.

---

### 3.4 Migration `025_drop_legacy_tables.sql` ✅ COMPLETATO

```sql
DROP TABLE IF EXISTS parse_results;
DROP TABLE IF EXISTS parse_results_v1;
DROP TABLE IF EXISTS parsed_messages;
DROP TABLE IF EXISTS review_queue;
DROP TABLE IF EXISTS operational_signals;
DROP TABLE IF EXISTS signals;
DROP TABLE IF EXISTS events;
DROP TABLE IF EXISTS warnings;
DROP TABLE IF EXISTS trades;
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS fills;
DROP TABLE IF EXISTS positions;
DROP TABLE IF EXISTS exchange_events;
DROP TABLE IF EXISTS backtest_runs;
DROP TABLE IF EXISTS backtest_trades;
DROP TABLE IF EXISTS protective_orders_mode;
```

**Tabelle che rimangono:**

| Tabella | Motivo |
|---|---|
| `schema_migrations` | infrastruttura |
| `raw_messages` | condivisa, usata da runtime_v2 |
| `canonical_messages` | output v2 — source of truth |

---

## 4. File toccati

| File | Tipo | Stato |
|---|---|---|
| `main.py` | Riscrittura | ✅ DONE |
| `src/telegram/listener.py` | Modifica worker + parametri | ✅ DONE |
| `src/runtime_v2/listener_sidecar.py` | Eliminato | ✅ DONE |
| `db/migrations/025_drop_legacy_tables.sql` | Creato e applicato | ✅ DONE |

---

## 5. Acceptance criteria

PRD 2.c è done quando:

1. ✅ `python main.py` parte senza errori di import relativi a moduli legacy rimossi dalla costruzione.

2. ✅ Un messaggio ricevuto dal listener produce una riga in `canonical_messages` con `parse_status` in `{PARSED, PARTIAL, UNCLASSIFIED}` e zero eccezioni non gestite.

3. ✅ `main.py` non istanzia `MessageRouter`, `OperationRulesEngine`, `TargetResolver`, `SignalsStore`, `OperationalSignalsStore`.

4. ✅ Migration `025` applicata — le tabelle legacy non esistono più nel DB live.

5. ✅ Test esistenti runtime_v2 restano verdi (74/74).

6. ✅ I test di `src/telegram/tests/` che dipendono dal router sono stati aggiornati o rimossi.

**Segnale primario:** `main.py` gira, arriva un messaggio reale, compare una riga in `canonical_messages`.

---

## 6. Fuori scope (originale)

- Operation rules engine → PRD 03
- Target resolver v2 → PRD futuro
- Execution layer v2 → PRD futuro
- ~~Rimozione fisica di `router.py` e `src/storage/` legacy~~ → vedi §8 (sblocco anticipato)
- Replay / backtesting su `canonical_messages` → separato

---

## 7. Rischi (originali — risolti)

| Rischio | Stato |
|---|---|
| Test `src/telegram/tests/` che mockano il router rompono la suite | Risolto |
| `listener.py` ha logica di recovery che passa per il router | Risolto |
| DB live con dati nelle tabelle droppate | Migration 025 applicata |
| `channels.yaml` non copre tutti i trader attivi | `ChannelConfigResolver` in uso |

---

## 8. Delta residuo — eliminazione fisica file Python legacy ⚠️ DA FARE

Audit 2026-06-04: i file Python corrispondenti alle tabelle droppate sono ancora presenti
nel filesystem ma **completamente orfani** — nessun import in tutto il progetto (verificato).
Le tabelle sono già droppate, i file non servono più.

### File da eliminare

| File | Motivo |
|---|---|
| `src/storage/parse_results.py` | Tabella `parse_results` droppata in 025; zero import |
| `src/storage/parse_results_v1.py` | Tabella `parse_results_v1` droppata in 025; zero import |
| `src/storage/parsed_messages.py` | Tabella `parsed_messages` droppata in 025; zero import |
| `src/storage/review_queue.py` | Tabella `review_queue` droppata in 025; zero import |
| `src/storage/signals_query.py` | Zero import esterno; nessuna tabella attiva |
| `src/storage/operational_signals_store.py` | Tabella `operational_signals` droppata in 025; importato solo da `src/storage/tests/test_provenance_topic.py` (test legacy) |
| `src/storage/tests/test_provenance_topic.py` | Test del file sopra — eliminare insieme |
| `src/telegram/bot.py` | Stub vuoto (solo docstring + TODO); zero import |
| `src/parser_v2/profiles/profili_vecchi/` | Directory intera (~50 file); zero import dall'esterno; sostituita dai profili attivi in `src/parser_v2/profiles/trader_*/` |

### Procedura

1. Rimuovere i file elencati
2. Verificare che `pytest` resti verde (nessun test li importa direttamente)
3. Commit con messaggio: `chore: remove orphaned legacy Python files (PRD 2.c delta)`

### Note

- `src/storage/operational_signals_store.py` — contiene SQL reference a `parse_results` in un commento/docstring, non un import Python; da verificare se il file stesso è ancora importato da qualcosa prima di eliminarlo
- `src/parser/` (canonical_v1) e `src/parser/models/` legacy — **non toccare** ancora; bloccati dalla migrazione PRD 03 (operation_rules) e PRD futuro (target_resolver)
