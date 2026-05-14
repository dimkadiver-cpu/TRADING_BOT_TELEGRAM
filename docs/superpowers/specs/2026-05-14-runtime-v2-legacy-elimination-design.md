# PRD 2.c — Runtime V2: Eliminazione Legacy e Promozione a Stack Primario

**Data:** 2026-05-14
**Stato:** approved
**Deriva da:** revisione stato runtime_v2 post PRD 2.b
**Precondizione:** PRD 2.b chiuso — `canonical_messages` funzionante, 74/74 test verdi
**Sblocca:** PRD 03 (Operation Rules Engine V2)

---

## 1. Scopo

Eliminare il router legacy e tutto il suo albero di dipendenze. Dopo questo PRD, `main.py` avvia il listener che processa messaggi Telegram attraverso il solo stack runtime_v2, produce `CanonicalMessage` in `canonical_messages`, e logga il risultato. Nessun layer downstream attivo.

**Output finale verificabile:** `main.py` parte → listener riceve messaggio → riga in `canonical_messages` → log con `primary_class` e `parse_status`.

---

## 2. Stato di partenza

### Flusso attuale

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

### Flusso target

```
main.py (semplificato)
  └── TelegramListener
        └── RuntimeV2IntakeProcessor → ParserPipelineProcessor → canonical_messages
                                                                  + log
```

---

## 3. Modifiche

### 3.1 `main.py` — riscrittura

**Rimuovere completamente:**

| Import / costruzione | Note |
|---|---|
| `MessageRouter` | 1250 righe, nessuna funzione migrata |
| `OperationRulesEngine` | PRD 03 lo riprogetta da zero |
| `TargetResolver` | PRD futuro |
| `SignalsStore`, `OperationalSignalsStore` | layer execution legacy |
| `ParseResultStore`, `ParseResultV1Store`, `ParsedMessageStore` | tabelle droppate |
| `HistoryBackedIntentValidator` | non esiste in v2 |
| `DynamicPairlistManager` | layer execution legacy |
| `TelegramSourceTraderMapper` | risoluzione trader delegata a `ChannelConfigResolver` |
| `validate_operation_rules_config` | config legacy |
| `RuntimeV2ListenerSidecar` | sostituito da pipeline diretta |
| `_configure_shadow_mode` | shadow mode eliminato |
| env `USE_RUNTIME_V2`, `PARSER_V1_SHADOW_MODE` | non più necessarie |

**Costruire:**

```python
repo       = RawMessageRepository(db_path=db_path)
eligibility = IntakeEligibilityCheck(channels_config=channel_config)
resolver   = RuntimeV2TraderResolver(channel_config=channel_config)
intake     = RuntimeV2IntakeProcessor(repo, eligibility, resolver, channel_config, config)
pipeline   = ParserPipelineProcessor(canonical_repo=CanonicalMessageRepository(db_path))

listener = TelegramListener(
    ingestion_service=ingestion_service,
    processing_status_store=processing_status_store,
    intake_processor=intake,
    parser_pipeline=pipeline,
    logger=logger,
    channels_config=channels_config,
    fallback_allowed_chat_ids=fallback_ids,
)
```

---

### 3.2 `src/telegram/listener.py` — worker

Il worker attuale chiama `self._router.route(item)` poi `self._sidecar.process_queue_item(item)`.

**Target:** il worker chiama direttamente:

```python
candidate = self._intake.process(item)
if candidate is not None:
    result = self._pipeline.process(candidate)
    self._log_result(result)
```

- `router` rimosso come parametro (non opzionale — eliminato)
- `sidecar` rimosso come parametro
- Aggiunti: `intake_processor: RuntimeV2IntakeProcessor`, `parser_pipeline: ParserPipelineProcessor`
- Tutto il codice del worker che gestisce routing legacy, parse results, review queue viene rimosso

---

### 3.3 `src/runtime_v2/listener_sidecar.py` — eliminato

Il sidecar era un adapter semplificato per lo shadow mode. Con il router eliminato il sidecar non ha più ragione di esistere. File rimosso.

---

### 3.4 Migration `025_drop_legacy_tables.sql`

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

| File | Tipo |
|---|---|
| `main.py` | Riscrittura |
| `src/telegram/listener.py` | Modifica worker + parametri |
| `src/runtime_v2/listener_sidecar.py` | Eliminato |
| `db/migrations/025_drop_legacy_tables.sql` | Nuovo |

**Non toccati:** `src/telegram/router.py` (resta nel repo ma non istanziato), `src/storage/`, `src/execution/`, `src/parser/` legacy, `src/runtime_v2/` (invariato).

**Nota:** `router.py` e `src/storage/` legacy rimangono nel filesystem — non vengono eliminati in questo PRD. Verranno rimossi quando i layer che li dipendono (operation rules, execution) saranno migrati a v2.

---

## 5. Acceptance criteria

PRD 2.c è done quando:

1. `python main.py` parte senza errori di import relativi a moduli legacy rimossi dalla costruzione.

2. Un messaggio ricevuto dal listener produce una riga in `canonical_messages` con `parse_status` in `{PARSED, PARTIAL, UNCLASSIFIED}` e zero eccezioni non gestite.

3. `main.py` non istanzia `MessageRouter`, `OperationRulesEngine`, `TargetResolver`, `SignalsStore`, `OperationalSignalsStore`.

4. Migration `025` applicata — le tabelle legacy non esistono più nel DB live.

5. Test esistenti runtime_v2 restano verdi (74/74).

6. I test di `src/telegram/tests/` che dipendono dal router vengono aggiornati o rimossi se testano comportamento legacy non più presente.

**Segnale primario:** `main.py` gira, arriva un messaggio reale, compare una riga in `canonical_messages`.

---

## 6. Fuori scope

- Operation rules engine → PRD 03
- Target resolver v2 → PRD futuro
- Execution layer v2 → PRD futuro
- Rimozione fisica di `router.py` e `src/storage/` legacy → dopo migrazione layer downstream
- Replay / backtesting su `canonical_messages` → separato

---

## 7. Rischi

| Rischio | Mitigazione |
|---|---|
| Test `src/telegram/tests/` che mockano il router rompono la suite | Aggiornare o rimuovere i test che testano comportamento puramente legacy |
| `listener.py` ha logica di recovery che passa per il router | Verificare `run_recovery()` — se usa router, adattare al nuovo path |
| DB live con dati nelle tabelle droppate | DROP IF EXISTS — idempotente. I dati storici vanno persi: accettato esplicitamente |
| `channels.yaml` non copre tutti i trader attivi | `ChannelConfigResolver` deve coprire gli stessi canali che il vecchio `TelegramSourceTraderMapper` gestiva |
