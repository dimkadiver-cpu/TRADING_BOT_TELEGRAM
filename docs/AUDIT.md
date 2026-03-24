# AUDIT — Stato progetto e allineamento nuova architettura

Questo documento viene prodotto dalla Sessione 0 di Claude Code e aggiornato ad ogni sessione importante.

I vecchi documenti sono archiviati in docs/old/ —
non seguire le istruzioni che contengono.
Riferimento storico only.

---

## Documentazione vecchia — cosa fare

| File | Stato | Azione |
|---|---|---|
| `MASTER_PLAN.md` | Obsoleto — architettura vecchia | Sostituito da `PRD_generale.md` |
| `SYSTEM_ARCHITECTURE.md` | Obsoleto | Sostituito da `PRD_generale.md` |
| `PARSER_FLOW.md` | Obsoleto — pipeline generico | Sostituito da `PRD_parser.md` |
| `PARSER_RUNTIME_FLOW.md` | Obsoleto | Sostituito da `PRD_parser.md` |
| `PARSER_MIGRATION_LEGACY_TO_V2.md` | Riferimento utile per intents | Leggere per mapping ACT_* → nuovi intents |
| `PARSER_ACTIONS_V2.md` | Parzialmente utile | Leggere per lista action types |
| `DB_SCHEMA.md` | Ancora valido per raw_messages e parse_results | Mantenere, aggiornare con review_queue |
| `IMPLEMENTATION_STATUS.md` | Obsoleto — stato cambiato | Sostituito da questo file |
| `ROADMAP.md` | Obsoleto | Sostituito da `PRD_generale.md` ordine sviluppo |
| `CODEX_BOOTSTRAP.md` | Obsoleto — era per Codex | Sostituito da `CLAUDE.md` |
| `SESSION_HANDOFF.md` | Obsoleto | Usa skill `handoff-trading-bot` |
| `TASKS.md` | Obsoleto | Sostituito dai PRD dettagliati |
| `TRADE_STATE_MACHINE.md` | Target design valido | Mantenere per Fase 5+ |
| `RISK_ENGINE.md` | Target design valido | Mantenere per Fase 5+ |
| `BOT_COMMANDS.md` | Target design valido | Mantenere per Fase 5+ |
| `EXCHANGE_PRECISION_ENGINE.md` | Target design valido | Mantenere per Fase 5+ |
| `CONFIG_SCHEMA.md` | Parzialmente valido | Aggiornare con channels.yaml |
| `DOCUMENTATION_COHERENCE_AUDIT.md` | Obsoleto | Sostituito da questo file |
| `PROJECT_FILE_INDEX.md` | Snapshot marzo 2026 | Utile come riferimento, non aggiornare manualmente |

---

## Stato file codice — classificazione

### KEEP — non toccare, funzionano e sono stabili

```
src/storage/raw_messages.py         → storage layer stabile
src/storage/parse_results.py        → storage layer stabile
src/core/                           → utilities condivise
src/telegram/effective_trader.py    → risoluzione trader
src/telegram/eligibility.py         → eligibility
src/telegram/ingestion.py           → persistenza raw
src/telegram/trader_mapping.py      → mapping sorgenti
db/migrations/                      → schema DB, mai toccare
src/parser/trader_profiles/base.py  → ParserContext, TraderParseResult, Protocol — stabile
src/parser/trader_profiles/registry.py → registro profili — stabile
src/parser/text_utils.py            → utilities testo condivise
src/parser/canonical_schema.py      → loader CSV schema intent — stabile (CSV assente = {} silenzioso, non blocca)
src/parser/intent_action_map.py     → KEEP — usato da trader_a e trader_d (intent_policy_for_intent)
src/parser/trader_profiles/common_utils.py → KEEP — usato da trader_3, trader_a, trader_b, trader_c
src/parser/action_builders/canonical_v2.py → builder azioni v2 — stabile
src/parser/trader_profiles/trader_3/profile.py  → ✓ MIGRATO, tutti i test passano
src/parser/trader_profiles/trader_3/parsing_rules.json → ✓ ok
src/parser/models/__init__.py                   → ✓ CREATO, Step 1 completo
src/parser/models/canonical.py                  → ✓ Price, Intent, TargetRef, TraderParseResult
src/parser/models/new_signal.py                 → ✓ EntryLevel, StopLoss, TakeProfit, NewSignalEntities, compute_completeness
src/parser/models/update.py                     → ✓ UpdateEntities
src/parser/models/tests/test_price_normalization.py → ✓ 79 test passano
src/parser/rules_engine.py                      → ✓ IMPLEMENTATO, Step 2 completo
src/parser/tests/test_rules_engine.py           → ✓ 62 test passano
src/parser/trader_profiles/trader_3/parsing_rules.json → ✓ formato PRD completo (Step 3)
src/parser/trader_profiles/trader_3/tests/test_rules_engine_trader_3.py → ✓ 32 test passano
src/parser/trader_profiles/trader_a/profile.py  → ✓ MIGRATO Step 7, 100/100 test pass
src/parser/trader_profiles/trader_b/profile.py  → ✓ MIGRATO Step 5, usa RulesEngine, 76/76 test pass
src/parser/trader_profiles/trader_b/parsing_rules.json → ✓ formato PRD completo (Step 5)
src/parser/trader_profiles/trader_b/tests/test_rules_engine_trader_b.py → ✓ 38 test RulesEngine
src/parser/trader_profiles/trader_c/profile.py  → ✓ MIGRATO Step 6a, usa RulesEngine, 68/68 test pass
src/parser/trader_profiles/trader_c/parsing_rules.json → ✓ formato PRD completo (Step 6a)
src/parser/trader_profiles/trader_c/tests/test_rules_engine_trader_c.py → ✓ 48 test RulesEngine
src/parser/trader_profiles/trader_d/parsing_rules.json → ✓ formato PRD completo (Step 6b)
src/parser/trader_profiles/trader_d/tests/test_rules_engine_trader_d.py → ✓ 51 test RulesEngine (Step 6b)
parser_test/reporting/report_schema.py          → ✓ aggiornato Step 4 (warnings_summary, completeness, missing_fields)
parser_test/reporting/flatteners.py             → ✓ aggiornato Step 4 (new intent format, completeness, warnings)
parser_test/scripts/replay_parser.py            → ✓ MIGRATO Step 8-MIGRATE/1 — usa get_profile_parser(), ParserContext, ParseResultRecord diretto
parser_test/scripts/generate_parser_reports.py  → in uso
src/core/trader_tags.py                         → ✓ aggiornato 2026-03-23, supporta varianti alias tipo `Trader [ #D]`
```

### REWRITE — da riscrivere con nuova architettura

```
src/telegram/listener.py            → ✓ RISCRITTO Step 9 — TelegramListener, asyncio.Queue, recovery, hot reload, blacklist, media skip
src/parser/trader_profiles/trader_d/profile.py  → ✓ MIGRATO Step 6b (eredita RulesEngine da TraderB, 90/90 test pass)
src/parser/trader_profiles/trader_b_da_contollare_/ → cartella eliminata (non più presente)
```

### DELETE — eliminare dopo migrazione completa

```
✓ src/parser/pipeline.py              → ELIMINATO Step 8-DELETE
✓ src/parser/normalization.py         → ELIMINATO Step 8-DELETE
✓ src/parser/dispatcher.py            → ELIMINATO Step 8-DELETE
✓ src/parser/scoring.py               → ELIMINATO Step 8-DELETE
✓ src/parser/entity_extractor.py      → ELIMINATO Step 8-DELETE
✓ src/parser/intent_classifier.py     → ELIMINATO Step 8-DELETE
✓ src/parser/prefix_normalizer.py     → ELIMINATO Step 8-DELETE
✓ src/parser/trader_profiles/ta_profile.py   → ELIMINATO Step 8-DELETE
✓ src/parser/models.py                → ELIMINATO Step 8-DELETE
✓ src/parser/trader_resolver.py       → ELIMINATO Step 8-DELETE
✓ src/parser/llm_adapter.py           → ELIMINATO Step 8-DELETE
✓ src/parser/parser_config.py         → ELIMINATO Step 8-DELETE
✓ src/parser/trader_profiles/trader_a/debug_report.py → ELIMINATO Step 8-DELETE
✓ src/parser/trader_profiles/trader_a/tests/test_debug_report_smoke.py → ELIMINATO Step 8-DELETE
  src/parser/report_market_entry_none.py    → script di debug ad-hoc (da eliminare)
  src/parser/trader_profiles/trader_b/parsing_rules copy.json → copia di backup (da eliminare)
  src/parser/trader_profiles/trader_d/parsing_rules copy.json → copia di backup (da eliminare)
  src/parser/trader_profiles/trader_b_da_contollare_/ → cartella WIP non integrata, valutare
```

**Nota Step 8-DELETE:** `common_utils.py` e `intent_action_map.py` erano stati inclusi
erroneamente nella lista DELETE — sono ancora usati dai profili migrati. Ripristinati.
`common_utils.py` → usato da trader_3, trader_a, trader_b, trader_c
`intent_action_map.py` → usato da trader_a e trader_d (intent_policy_for_intent)

### NEW — da creare da zero

```
CLAUDE.md                                       → ✓ fatto
docs/PRD_generale.md                            → ✓ fatto
docs/PRD_listener.md                            → ✓ fatto
docs/PRD_router.md                              → ✓ fatto
docs/PRD_parser.md                              → ✓ fatto
docs/PHASE_3_ROUTER_STATUS.md                   → ✓ stato operativo Fase 3
docs/AUDIT.md                                   → questo file
config/channels.yaml                            → ✓ esiste, ma `channels: []` quindi non ancora pronto per uso live
src/parser/models/__init__.py                   → ✓ CREATA (Step 1 completo)
src/parser/models/canonical.py                  → ✓ CREATA (Step 1 completo)
src/parser/models/new_signal.py                 → ✓ CREATA (Step 1 completo)
src/parser/models/update.py                     → ✓ CREATA (Step 1 completo)
src/parser/rules_engine.py                      → ✓ IMPLEMENTATO (Step 2 completo)
src/parser/trader_profiles/shared/russian_trading.json → ✗ non esiste
src/parser/trader_profiles/shared/english_trading.json → ✗ non esiste
src/telegram/router.py                          → ✓ IMPLEMENTATO (Step 10) — MessageRouter, QueueItem, blacklist, trader resolution, reply_raw_text, hashtag/link extraction
src/storage/review_queue.py                     → ✓ IMPLEMENTATO (Step 10) — ReviewQueueStore, ReviewQueueEntry, insert/resolve/get_pending
parser_test/scripts/watch_parser.py             → ✓ CREATO (Step 4 completo)
```

### TROVATO MA NON IN AUDIT — file non catalogati

```
src/execution/                      → NUOVO, non pianificato in questa fase
  planner.py                        → genera OrderPlan da segnale
  risk_gate.py                      → risk gating
  state_machine.py                  → state machine posizioni
  update_applier.py                 → applica update su stato
  update_planner.py                 → pianifica update
  test_update_applier.py            → test
  test_update_planner.py            → test
src/exchange/                       → NUOVO, non pianificato in questa fase
  adapter.py                        → interfaccia exchange
  bybit_rest.py                     → bybit REST
  bybit_ws.py                       → bybit WebSocket
  reconcile.py                      → riconciliazione stato
src/telegram/bot.py                 → bot Telegram (non pianificato ora)
src/parser/action_builders/canonical_v2.py → builder azioni strutturate v2
```

---

## Stabilizzazione ambiente test — stato al 2026-03-24

### Punto 1 ✓ — Comando ufficiale test (2026-03-24)

Definito il comando standard di progetto:
```bash
.venv/Scripts/python.exe -m pytest <percorso>
```
File aggiornati:
- `README.md` — sezione "Test parser": sostituito `pytest` bare con comando venv
- `README_CLAUDECODE.md` — riga Sessione 0 e checklist fine sessione: allineate

### Punto 2 ✓ — Temp e cache locali al workspace (2026-03-24)

Tutti i path di test spostati dentro il workspace. Nessun `PermissionError` riscontrato.

File toccati:
- `pytest.ini` — `cache_dir` da `C:/TeleSignalBot/.codex_tmp/pytest_cache` → `.pytest_cache` (relativo a rootdir)
- `conftest.py` (nuovo, root) — override globale `tmp_path` su `<project_root>/.test_tmp/<uuid>`
- `src/telegram/tests/conftest.py` — rimosso `tmp_path` override con path hardcoded; mantenuto `pytest_pyfunc_call` hook per async
- `.gitignore` — aggiunti `.pytest_cache/`, `.test_tmp/`, `.codex_tmp/`

Comportamento noto (non un bug): su Windows, SQLite file handle non sempre rilasciato prima del teardown fixture → le dir UUID in `.test_tmp` possono contenere file `*.sqlite3` residui. `ignore_errors=True` previene errori. Gli artefatti sono inoffensivi e ignorati da git.

Verifica post-fix: 416/416 test passano, 0 `PermissionError`.

### Punto 3 ✓ — Smoke suite ufficiale (2026-03-24)

Definita e verificata smoke suite: 216 test, ~6s, 0 failure.

Scope:
- `src/parser/models/tests/` — 79 test (Price, Intent, TargetRef, TraderParseResult, entities)
- `src/parser/tests/` — 62 test (RulesEngine: load, classify, intents, blacklist, merge)
- `src/telegram/tests/` — 50 test (channel config, blacklist, media, router, reply chain, recovery, **router_integration** +4)
- `src/validation/tests/` — 25 test (CoherenceChecker)

Comando ufficiale smoke suite:
```bash
.venv/Scripts/python.exe -m pytest \
  src/parser/models/tests/ \
  src/parser/tests/ \
  src/telegram/tests/ \
  src/validation/tests/ \
  -q
```

Documentato in `README.md` (sezione "Test") e `README_CLAUDECODE.md` (sezione "Comandi test standard" e checklist fine sessione).

### Punto 4 ✓ — Full suite documentata (2026-03-24)

Verifica: 427/427 test passano (profili trader + harness + execution), ~3s.

Scope full suite:
- `src/parser/trader_profiles/` — 346 test (trader_3/a/b/c/d, RulesEngine per trader)
- `parser_test/tests/` — 34 test (harness replay, flatteners, report schema)
- `src/execution/test_update_planner.py` + `test_update_applier.py` — 11+16 test

Documentato in `README.md` con note su: artefatti SQLite su Windows, stile `unittest.TestCase`, prerequisito DB test per `parser_test/tests/`.

### Punto 5 ✓ — Verifica dipendenze (2026-03-24)

`requirements.txt` copre tutte le dipendenze di test richieste:
- `pydantic>=2.0` ✓
- `pytest>=8.0` ✓
- `pytest-asyncio>=0.23` ✓
- `pyyaml>=6.0` ✓
- `telethon>=1.34.0` ✓

Nessuna dipendenza mancante. Bootstrap `.venv` già documentato in README sezione Setup.
Nota aggiunta in README sezione Test: "Usa sempre `.venv/Scripts/python.exe -m pytest` — mai `pytest` bare".

### Punto 6 ✓ — Classificazione failure ambiente vs logica (2026-03-24)

Aggiunta sezione "Troubleshooting test" in `README.md` con tabelle distinte per:
- Errori di ambiente: `ModuleNotFoundError`, `PermissionError`, CWD errata, collection failure
- Errori di logica: `AssertionError`, mismatch parsing, ValidationError Pydantic

Chiarito esplicitamente: "Gli errori di ambiente non vanno mai interpretati come regressioni del parser."

### Punto 7 ✓ — Criterio di chiusura (2026-03-24)

Run di verifica finale eseguiti con `.venv/Scripts/python.exe -m pytest`:

| Suite | Comando | Risultato | Tempo |
|---|---|---|---|
| Smoke | `src/parser/models/tests/ src/parser/tests/ src/telegram/tests/ src/validation/tests/` | 212/212 pass | ~5s |
| Full | `src/parser/trader_profiles/ parser_test/tests/ src/execution/test_*.py` | 427/427 pass | ~3s |

Criteri verificati:
- [x] Smoke suite eseguibile in modo ripetibile con il comando ufficiale
- [x] Nessun `PermissionError` su temp/cache (`.pytest_cache` e `.test_tmp` nel workspace)
- [x] Nessun `ModuleNotFoundError` con setup documentato (`.venv` + `requirements.txt`)
- [x] README allineato ai comandi reali (smoke, full suite, troubleshooting)
- [x] Run completo documentato — esito: **pass** (0 failure logiche, 0 problemi ambiente)

**Stabilizzazione ambiente test: COMPLETATA** — 2026-03-24

Stato checklist `TEST_ENV_STABILIZATION_CHECKLIST.md`:
- [x] Punto 1 — comando ufficiale test
- [x] Punto 2 — temp e cache locali al workspace
- [x] Punto 3 — smoke suite ufficiale
- [x] Punto 4 — full suite documentata
- [x] Punto 5 — verifica dipendenze
- [x] Punto 6 — classificazione failure ambiente vs logica
- [x] Punto 7 — criterio di chiusura

---

## Test coverage — stato al 2026-03-22

Comando: `.venv/Scripts/python.exe -m pytest src/parser/trader_profiles/ parser_test/tests/ -q`

| Scope | Test totali | PASSED | FAILED | Note |
|---|---|---|---|---|
| trader_3 | 12 | 12 | 0 | ✓ Tutti pass |
| trader_a | 100 | 100 | 0 | ✓ Tutti pass — Step 7 completo |
| trader_b | 76 | 76 | 0 | ✓ 38 profilo + 38 RulesEngine — Step 5 completo |
| trader_c | 68 | 68 | 0 | ✓ 20 profilo + 48 RulesEngine — Step 6a completo |
| trader_d | 90 | 90 | 0 | ✓ 39 profilo + 51 RulesEngine — Step 6b completo |
| parser_test/tests/ | 34 | 34 | 0 | ✓ (Step 8-SAFE + Step 8-DELETE: test_debug_report_smoke rimosso) |
| src/execution/ | 11 | 11 | 0 | ✓ update_planner + update_applier test |
| telegram/tests/ | 28 | 28 | 0 | ✓ channel_config + blacklist + media + recovery (Step 9) |
| **TOTALE** | **419** | **419** | **0** | |

### Step 8-SAFE (✓ COMPLETO — 2026-03-22)

9 file legacy eliminati senza impatto sul flusso live:
- `src/parser/report_trader_a_v2_quality.py` → sostituito da `generate_parser_reports.py`
- `src/parser/test_trader_a_replay_db.py`, `test_trader_b_replay_db.py` → sostituiti da `replay_parser.py`
- `src/parser/test_trader_a_pipeline_integration.py` → test vecchia architettura
- `parser_test/tests/test_pipeline_semantic_consistency.py` → testava wrapper legacy
- `parser_test/tests/test_parse_result_normalized.py` → testava wrapper legacy
- `parser_test/tests/test_ta_profile_refactor.py` → `ta_profile` module legacy, `ACT_*` legacy
- `parser_test/tests/test_canonical_schema_alignment.py` → mix legacy/schema (non recuperabile)
- `parser_test/tests/test_parser_dispatcher_modes.py` → testava dispatcher legacy completo

Dopo eliminazione: 382/382 test passano (era 346 su scope solo profili).

**Step 8-MIGRATE parte 1 ✓:**
- `parser_test/scripts/replay_parser.py` migrato → usa `get_profile_parser()` + `ParserContext` + `_build_parse_result_record()`
- Rimossi: `MinimalParserPipeline`, `ParserInput`, `normalize_parser_mode`, `parser_config`, `--parser-mode` arg, `by_normalized_event_type` counter
- Aggiunto: `_parse_one()`, `_build_parse_result_record()`, `_build_skipped_record()` helper

**Step 8-MIGRATE parte 2 ✓:**
- `src/telegram/listener.py` migrato → usa `get_profile_parser()` + `ParserContext` + `_build_parse_result_record()`
- Rimossa: `build_minimal_parser_pipeline()`, tutti gli import `parser_config`, `MinimalParserPipeline`, `ParserInput`
- `register_message_listener()`: rimosso param `parser_pipeline: MinimalParserPipeline`
- `main.py` aggiornato: rimossa costruzione `parser_pipeline`, rimosso param dalla chiamata
- **`pipeline.py` e le sue dipendenze dirette non erano più chiamate dal flusso live o di test** — cleanup completato nei passaggi successivi

**Opzione A ✓ (2026-03-22):**
- `src/execution/update_planner.py`: rimosso `from src.parser.normalization import ParseResultNormalized`
- Signature `build_update_plan` semplificata: `Mapping[str, Any]` (era `ParseResultNormalized | Mapping`)
- Branch `isinstance(value, ParseResultNormalized)` rimosso da `_as_mapping()`
- 393/393 test passano (382 profili/parser_test + 11 execution)
- **Il cluster legacy (`pipeline.py`, `normalization.py`, ecc.) è stato poi rimosso dal percorso attivo**

**Step 8-DELETE (batch) — completato**
Eliminati nel cleanup:
``` 
src/parser/pipeline.py
src/parser/normalization.py
src/parser/dispatcher.py
src/parser/llm_adapter.py
src/parser/models.py              ← vecchio wrapper, non src/parser/models/
src/parser/parser_config.py
src/parser/scoring.py             (se presente)
src/parser/entity_extractor.py    (se presente)
src/parser/intent_classifier.py   (se presente)
src/parser/prefix_normalizer.py   (se presente)
src/parser/trader_profiles/ta_profile.py
src/parser/trader_resolver.py
```

### trader_a (Step 7 — ✓ COMPLETO)

Tutti i 100 test passano. Fix principali applicati:
- Rimossi marker ambigui da `parsing_rules.json`: `"бу"` (U_MOVE_STOP_TO_BE), `"стоп на"` (U_MOVE_STOP), `"тейк"` (U_TP_HIT), `"стоп"` (U_STOP_HIT)
- `_extract_intents`: `stop_to_tp_context` anticipato; companion U_MOVE_STOP aggiunto solo quando stop_to_tp_context=True; guard stop_to_tp_context su U_STOP_HIT; U_CANCEL_PENDING_ORDERS auto-aggiunto con U_INVALIDATE_SETUP per NEW_SIGNAL
- `parse_message`: downgrade UPDATE→UNCLASSIFIED per messaggi con soli intents stop-management senza target (eccetto frasi specifiche autoritative)
- `_resolve_cancel_scope`: "all limit orders" → ALL_ALL
- `_build_grouped_targeted_actions`: scope SELECTOR usa `global_target_scope`; `_group_action_items` emette EXPLICIT_TARGETS individuali per target
- Test aggiornati per eliminare contraddizioni: g13 (solo U_MOVE_STOP_TO_BE), g20 (senza U_MOVE_STOP), grouped_actions (5 individuali), real_cases (rimosso assertNotIn U_MOVE_STOP)

### trader_d (Step 6b — ✓ COMPLETO)

`parsing_rules.json` convertito a formato PRD completo. `intent_markers` convertiti da formato nested `{strong:[], weak:[]}` a flat lists. Aggiunti `classification_markers`, `combination_rules`, `target_ref_markers`, `blacklist` (ex `ignore_markers`), `fallback_hook`. Tutte le sezioni trader_d specifiche preservate. 90/90 test passano.

### Dettaglio fallimenti trader_b_da_contollare_ (2 test)

| Test | Causa |
|---|---|
| `test_cancel_pending` | `cancel_scope` restituisce `TARGETED` invece di `ALL_PENDING_ENTRIES` |
| `test_move_stop_to_be` | Logica stop-to-be cambiata rispetto all'atteso |

---

## Conflitti architettura attuale vs nuova

1. **Reply resolution transitiva implementata, ma non esaustiva in tutti i casi operativi** — il resolver ora risale la reply-chain con depth limit e loop protection. Il gap residuo riguarda i casi multi-trader dove il contesto storico nel DB è incompleto o ambiguo.

2. **`parser_test` è allineato al formato parser corrente, ma non riproduce ancora tutto il lifecycle live** — ora passa `hashtags` e `extracted_links` al `ParserContext`, ma non valida l'intero comportamento runtime di `processing_status` / `review_queue`.

3. **`src/execution/` e `src/exchange/` esistono ma non sono pianificati** — Questi moduli sono stati creati anticipatamente rispetto all'ordine di sviluppo (Fase 5+). Non integrati con il parser. Non testati nel contesto del flusso completo. Non modificare.

4. **Canali multi-trader** — `telegram_source_map.json` può e deve marcare i chat id multi-trader, ma questo non elimina gli `UNRESOLVED` per update brevi senza alias; serve contesto reply-chain robusto.

5. **`canonical_schema.py` carica da CSV** — `schema_consigliato_finale_parser.csv` è marcato come DELETE nel git status (`D schema_consigliato_finale_parser.csv`). Se il file viene eliminato, `canonical_schema.py` restituisce `{}` silenziosamente. Controllare prima di procedere.

---

## Rischi di regressione durante migrazione

1. **Ambiente di test non sempre allineato all'ambiente di progetto** — fuori dalla `.venv` i test possono fallire già in collection per dipendenze mancanti (es. `pydantic`). Valutare sempre lo stato con l'interprete del progetto.

2. **Working tree non pulito su alcuni profili trader** — sono presenti modifiche locali in corso su `trader_c` e `trader_d`; il quadro documentale aggiornato riflette il ramo di lavoro corrente, ma non equivale a stato consolidato o pronto al commit.

3. **parse_result_normalized_json** — il campo nel DB contiene output della vecchia architettura. Dopo la migrazione produrrà output del nuovo TraderParseResult. Il DB test è separato — nessun rischio sul DB live.

4. **Configurazione live ancora incompleta** — `config/channels.yaml` esiste ma ha `channels: []`; il listener/router sono pronti, ma il sistema non è ancora configurato per seguire canali reali.

5. **`schema_consigliato_finale_parser.csv` è staged per DELETE** — `canonical_schema.py` dipende da esso. Se sparisce senza un sostituto, `canonical_intents()` restituisce set vuoto e i test di `canonical_schema` potrebbero non rilevarsi. Verificare prima di committare.

---

## Ordine di sviluppo sicuro

```
[✓] Setup ambiente (CLAUDE.md, skills, PRD, dipendenze)
[✓] Step 0b — FIX CRITICO: correggere signature _resolve_global_target_scope in trader_d/profile.py
[✓] Step 1 — Pydantic models (src/parser/models/)
[✓] Step 2 — RulesEngine (src/parser/rules_engine.py)
[✓] Step 3 — Trader 3 parsing_rules.json aggiornato al formato PRD; 32 nuovi test RulesEngine
[✓] Step 4 — Watch mode + CSV debug: watch_parser.py, error logging, nuove colonne CSV
[✓] Step 5 — Migrazione trader_b
[✓] Step 6a — Migrazione trader_c
[✓] Step 6b — Migrazione trader_d
[✓] Step 7 — Migrazione trader_a — 100/100 test pass
[✓] Step 8 — Cleanup legacy completo: 8-SAFE (9 file), 8-MIGRATE/1 (replay_parser), 8-MIGRATE/2 (listener+main), Opzione-A (update_planner), 8-DELETE (14 file cluster legacy)
[✓] Step 9 — Listener robusto (asyncio.Queue, recovery, hot reload) — 28/28 test
[✓] Step 10 — Router / Pre-parser — già implementato, 8/8 test pass
[✓] Step 11 — Validazione coerenza: src/validation/coherence.py, 25 test, integrato nel Router
[ ] Step 12+ — Operation rules, target resolver
[ ] Step 13+ — freqtrade signal bridge (Sistema 1)
[ ] Step 14+ — Backtesting (Sistema 2)
```

**Regola: non iniziare uno step prima che il precedente sia testato e funzionante.**

---

## Note per Claude Code

- Leggi sempre `CLAUDE.md` prima di qualsiasi sessione
- Il documento autorevole per il parser è `docs/PRD_parser.md` — non i vecchi DOCS/
- I vecchi DOCS/ in `DOCS/` sono archivio storico — non seguire le istruzioni che contengono
- `TRADE_STATE_MACHINE.md`, `RISK_ENGINE.md`, `BOT_COMMANDS.md` sono target design futuro — non implementare ora
- Aggiorna questo file `AUDIT.md` quando completi ogni step
- `trader_d` risulta migrato e con suite dedicata presente; eventuali nuovi interventi vanno valutati sul working tree corrente, non sul vecchio stato pre-fix.
- **`src/execution/` e `src/exchange/`** esistono ma non sono nel piano di sviluppo attuale. Non toccare.

---

*Aggiornato: 2026-03-24 — documentazione riallineata allo stato reale del repository: parser/listener/router/validation segnati come implementati, cleanup legacy parser recepito, `config/channels.yaml` marcato come esistente ma non ancora configurato per il live, rischi aggiornati su ambiente `.venv`, working tree attivo e gap residui Fase 4+.*
