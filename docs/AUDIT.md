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
```

### REWRITE — da riscrivere con nuova architettura

```
src/telegram/listener.py            → ✓ MIGRATO Step 8-MIGRATE/2 — usa get_profile_parser(), ParseResultRecord diretto; asyncio.Queue, recovery, hot reload da fare in Step 9
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
docs/AUDIT.md                                   → questo file
config/channels.yaml                            → ✗ non esiste (config/ ha altri file ma non channels.yaml)
src/parser/models/__init__.py                   → ✓ CREATA (Step 1 completo)
src/parser/models/canonical.py                  → ✓ CREATA (Step 1 completo)
src/parser/models/new_signal.py                 → ✓ CREATA (Step 1 completo)
src/parser/models/update.py                     → ✓ CREATA (Step 1 completo)
src/parser/rules_engine.py                      → ✓ IMPLEMENTATO (Step 2 completo)
src/parser/trader_profiles/shared/russian_trading.json → ✗ non esiste
src/parser/trader_profiles/shared/english_trading.json → ✗ non esiste
src/telegram/router.py                          → ✗ non esiste (Fase 3)
src/storage/review_queue.py                     → ✗ non esiste (Fase 3)
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

## Test coverage — stato al 2026-03-22

Comando: `pytest src/parser/trader_profiles/ parser_test/tests/ -q`

| Scope | Test totali | PASSED | FAILED | Note |
|---|---|---|---|---|
| trader_3 | 12 | 12 | 0 | ✓ Tutti pass |
| trader_a | 100 | 100 | 0 | ✓ Tutti pass — Step 7 completo |
| trader_b | 76 | 76 | 0 | ✓ 38 profilo + 38 RulesEngine — Step 5 completo |
| trader_c | 68 | 68 | 0 | ✓ 20 profilo + 48 RulesEngine — Step 6a completo |
| trader_d | 90 | 90 | 0 | ✓ 39 profilo + 51 RulesEngine — Step 6b completo |
| parser_test/tests/ | 34 | 34 | 0 | ✓ (Step 8-SAFE + Step 8-DELETE: test_debug_report_smoke rimosso) |
| src/execution/ | 11 | 11 | 0 | ✓ update_planner + update_applier test |
| **TOTALE** | **391** | **391** | **0** | |

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
- **`pipeline.py` e le sue dipendenze dirette sono ora orfane** — niente nel flusso live o di test le chiama più

**Opzione A ✓ (2026-03-22):**
- `src/execution/update_planner.py`: rimosso `from src.parser.normalization import ParseResultNormalized`
- Signature `build_update_plan` semplificata: `Mapping[str, Any]` (era `ParseResultNormalized | Mapping`)
- Branch `isinstance(value, ParseResultNormalized)` rimosso da `_as_mapping()`
- 393/393 test passano (382 profili/parser_test + 11 execution)
- **Il cluster legacy (`pipeline.py`, `normalization.py`, ecc.) è ora privo di caller esterni** — pronto per batch DELETE

**Prossimo: Step 8-DELETE (batch)**
Eliminare in un'unica operazione:
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
src/parser/intent_action_map.py
src/parser/trader_profiles/common_utils.py (se presente)
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

1. **`src/parser/models/` non esiste** — Step 1 non ancora avviato. I profili esistenti usano `base.py::TraderParseResult` (dataclass con dict raw), non Pydantic. La nuova architettura richiede Pydantic v2 con modelli tipizzati. Nessun profilo è ancora migrato alla nuova struttura.

2. **`rules_engine.py` è solo un placeholder** — Step 2 non avviato. I profili attuali fanno classificazione internamente in `profile.py`. Il design prevede che la classificazione passi per `RulesEngine`.

3. **`TraderDProfileParser` è rotto** — Eredita `_resolve_global_target_scope` da `TraderB` con signature incompatibile. Tutti i test falliscono. Da fixare immediatamente prima di qualsiasi lavoro su trader_d.

4. **`src/execution/` e `src/exchange/` esistono ma non sono pianificati** — Questi moduli sono stati creati anticipatamente rispetto all'ordine di sviluppo (Fase 5+). Non integrati con il parser. Non testati nel contesto del flusso completo. Non modificare.

5. **`trader_b_da_contollare_/`** — Cartella WIP con nome non canonico, non registrata in `registry.py`, non nel CLAUDE.md. Probabilmente lavori in corso su trader_b. Va deciso se integrare, rinominare o eliminare.

6. **`canonical_schema.py` carica da CSV** — `schema_consigliato_finale_parser.csv` è marcato come DELETE nel git status (`D schema_consigliato_finale_parser.csv`). Se il file viene eliminato, `canonical_schema.py` restituisce `{}` silenziosamente. Controllare prima di procedere.

---

## Rischi di regressione durante migrazione

1. **pipeline.py legacy coesiste con nuova architettura** — durante la migrazione esistono entrambi. I profili migrati usano i nuovi modelli Pydantic, i profili non ancora migrati usano pipeline.py. Non mescolare mai i due path.

2. **Test esistenti** — i test in `trader_profiles/trader_a/tests/` sono legati alla vecchia architettura. Alcuni falliranno dopo la riscrittura del profilo — è atteso. Vanno aggiornati contestualmente alla migrazione del profilo.

3. **parse_result_normalized_json** — il campo nel DB contiene output della vecchia architettura. Dopo la migrazione produrrà output del nuovo TraderParseResult. Il DB test è separato — nessun rischio sul DB live.

4. **Registry.py** — aggiornare quando si migra ogni profilo per puntare alla nuova classe.

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
[ ] Step 9 — Listener robusto (asyncio.Queue, recovery, hot reload)
[ ] Step 10 — Router / Pre-parser
[ ] Step 11+ — Validazione coerenza, operation rules, target resolver
[ ] Step 12+ — freqtrade signal bridge (Sistema 1)
[ ] Step 13+ — Backtesting (Sistema 2)
```

**Regola: non iniziare uno step prima che il precedente sia testato e funzionante.**

---

## Note per Claude Code

- Leggi sempre `CLAUDE.md` prima di qualsiasi sessione
- Il documento autorevole per il parser è `docs/PRD_parser.md` — non i vecchi DOCS/
- I vecchi DOCS/ in `DOCS/` sono archivio storico — non seguire le istruzioni che contengono
- `TRADE_STATE_MACHINE.md`, `RISK_ENGINE.md`, `BOT_COMMANDS.md` sono target design futuro — non implementare ora
- Aggiorna questo file `AUDIT.md` quando completi ogni step
- **Prima di qualsiasi sessione su trader_d**: il profilo è completamente rotto (37/37 fail). Non iniziare nessun lavoro su trader_d senza fixare prima il BUG CRITICO di signature.
- **`src/execution/` e `src/exchange/`** esistono ma non sono nel piano di sviluppo attuale. Non toccare.

---

*Aggiornato: 2026-03-22 — Step 0b, Step 1 (Pydantic models), Step 2 (RulesEngine), Step 3 (trader_3 parsing_rules.json), Step 4 (watch mode + CSV debug), Step 5 (trader_b migrazione), Step 6a (trader_c migrazione), Step 6b (trader_d migrazione), Step 7 (trader_a migrazione 100/100), Step 8 COMPLETO (8-SAFE + 8-MIGRATE/1+2 + Opzione-A + 8-DELETE, 391/391 test) completati*
