# AUDIT — Stato progetto e allineamento nuova architettura

Questo documento viene prodotto dalla Sessione 0 di Claude Code e aggiornato ad ogni sessione importante.

I vecchi documenti sono archiviati in docs/old/ —
non seguire le istruzioni che contengono.
Riferimento storico only.

---

## Verifica corrente

Data verifica: 2026-04-06

- mixed suite su parser, telegram, validation, operation rules, target resolver, execution, backtesting e parser_test: `657 passed, 20 failed, 1 skipped`
- cluster di failure corrente:
  - `src/backtesting/tests/test_scenario_loader.py` e `config/backtest_scenarios_v2.yaml` non sono ancora allineati al modello atteso dal loader
  - `src/backtesting/tests/test_runner.py::TestWindowsCommandDetection::test_win32_uses_python_module`
  - `src/telegram/tests/test_listener_recovery.py::test_catchup_skips_channel_with_no_last_id`
- stato architetturale confermato: listener, router, parser, validation, operation rules, target resolver, live execution e backtesting base sono presenti nel repository

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
  
```

**Nota Step 8-DELETE:** `common_utils.py` e `intent_action_map.py` erano stati inclusi
erroneamente nella lista DELETE — sono ancora usati dai profili migrati. Ripristinati.
`common_utils.py` → usato da trader_3, trader_a, trader_b, trader_c
`intent_action_map.py` → usato da trader_a e trader_d (intent_policy_for_intent)

### NEW — backtesting (Fase 7)

```
src/backtesting/__init__.py                     → ✓ CREATO Step 16
src/backtesting/models.py                       → ✓ CREATO Step 16 — ChainedMessage, SignalChain, BacktestReadyChain (Pydantic v2)
src/backtesting/chain_builder.py                → ✓ CREATO Step 16 — SignalChainBuilder.build_all_async + sync wrapper
src/backtesting/tests/__init__.py               → ✓ CREATO Step 16
src/backtesting/tests/conftest.py               → ✓ CREATO Step 16 — in-memory schema, insert helpers, JSON factories
src/backtesting/tests/test_chain_builder.py     → ✓ CREATO Step 16 — 12 test, tutti verdi
src/backtesting/scenario.py                     → ✓ CREATO Step 17 — ScenarioConditions, BacktestScenario, BacktestSettings, ScenarioConfig, ScenarioLoader, ScenarioApplier
config/backtest_scenarios.yaml                  → ✓ CREATO Step 17 — 6 scenari predefiniti + backtest_settings
src/backtesting/tests/test_scenario.py          → ✓ CREATO Step 17 — 12 test, tutti verdi
db/migrations/015_backtest_runs.sql             → ✓ CREATO Step 21 — tabella backtest_runs
db/migrations/016_backtest_trades.sql           → ✓ CREATO Step 21 — tabella backtest_trades + indici
src/backtesting/storage.py                      → ✓ CREATO Step 21 — BacktestRunStore, BacktestTradeStore (aiosqlite)
src/backtesting/tests/test_storage.py           → ✓ CREATO Step 21 — 9 test, tutti verdi
src/backtesting/tests/conftest.py               → ✓ AGGIORNATO Step 21 — schema esteso con backtest_runs e backtest_trades
parser_test/scripts/replay_operation_rules.py   → ✓ CREATO Step 18b — batch script operation rules replay, CLI completa, safety guard
parser_test/scripts/tests/__init__.py           → ✓ CREATO Step 18b
parser_test/scripts/tests/test_replay_operation_rules.py → ✓ CREATO Step 18b — 6 test, tutti verdi
freqtrade/user_data/strategies/SignalBridgeBacktestStrategy.py → ✓ CREATO Step 19 — IStrategy backtest, separata da live, normalize_pair, 6 hook
src/backtesting/tests/test_strategy_signal_bridge.py → ✓ CREATO Step 19 — 17 test, tutti verdi
```

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
src/telegram/router.py                          → ✓ AGGIORNATO (Step 15) — Layer 4+5 integrati dopo VALID: engine.apply(), resolver.resolve(), INSERT signals + operational_signals
src/storage/review_queue.py                     → ✓ IMPLEMENTATO (Step 10) — ReviewQueueStore, ReviewQueueEntry, insert/resolve/get_pending
parser_test/scripts/watch_parser.py             → ✓ CREATO (Step 4 completo)
config/operation_rules.yaml                     → ✓ AGGIORNATO (Step 13+align) — risk-first model: risk_mode, risk_pct_of_capital, capital_base_usdt, tp_handling; rinominato max_per_signal_pct → hard_max_per_signal_risk_pct
config/trader_rules/trader_3.yaml               → ✓ AGGIORNATO (Step 13+align) — usa nuovi campi rischio
src/operation_rules/__init__.py                 → ✓ CREATO (Step 13)
src/operation_rules/loader.py                   → ✓ AGGIORNATO (align) — HardCaps.hard_max_per_signal_risk_pct; EffectiveRules: risk_mode, risk_pct_of_capital, risk_usdt_fixed, capital_base_mode, capital_base_usdt, tp_handling
src/operation_rules/risk_calculator.py          → ✓ AGGIORNATO (align) — compute_risk_pct, compute_risk_budget_usdt, compute_position_size_from_risk; DB queries leggono risk_budget_usdt/capital_base_usdt
src/operation_rules/engine.py                   → ✓ AGGIORNATO (align) — nuovi gate hard-block (missing_entry, missing_stop_loss, zero_sl_distance, invalid_leverage); size calcolata da rischio; nuovi campi OperationalSignal
src/operation_rules/tests/                      → ✓ AGGIORNATO (align) — 45 test passano (era 28)
src/storage/signals_query.py                    → ✓ CREATO (Step 14) — read-only accessor signals
src/storage/signals_store.py                    → ✓ CREATO (Step 14) — INSERT signals
src/storage/operational_signals_store.py        → ✓ AGGIORNATO (align) — OperationalSignalRecord e INSERT con nuovi campi risk-first
src/parser/models/operational.py               → ✓ AGGIORNATO (align) — OperationalSignal: +risk_mode, +risk_pct_of_capital, +risk_usdt_fixed, +capital_base_usdt, +risk_budget_usdt, +sl_distance_pct; position_size_usdt/pct ora derivati
db/migrations/012_operational_signals_risk.sql  → ✓ CREATO (align) — ALTER TABLE: 6 nuove colonne risk-first
src/target_resolver/__init__.py                 → ✓ CREATO (Step 14)
src/target_resolver/resolver.py                 → ✓ CREATO (Step 14) — TargetResolver per kind/method + eligibility
src/target_resolver/tests/                      → ✓ CREATO (Step 14) — 14 test resolver
src/parser/models/operational.py               → ✓ AGGIORNATO (Step 12) — OperationalSignal con trader_id, arbitrary_types_allowed; ResolvedTarget; ResolvedSignal
src/telegram/tests/test_router_phase4.py        → ✓ AGGIORNATO (Step 15 + GAP-01) — +2 test: unresolved UPDATE → review_queue
```

### KEEP — execution layer (Fase 5–6, stabile)

```
src/execution/
  freqtrade_normalizer.py           → ✓ canonical → FreqtradeSignalContext; Step A-E allineamento
  freqtrade_callback.py             → ✓ callback writer con retry SQLite
  exchange_gateway.py               → ✓ gateway exchange-backed (protocol + wrapper)
  exchange_order_manager.py         → ✓ manager SL/TP exchange-backed + update lifecycle
  order_reconciliation.py           → ✓ bootstrap sync + watchdog leggero (Step 24)
  freqtrade_exchange_backend.py     → ✓ CREATO GAP-03 — adapter FreqtradeExchangeBackend (ExchangeGatewayBackend)
  machine_event.py                  → ✓ CREATO GAP-04 — rule engine machine_event (evaluate_rules, MachineEventAction)
  protective_orders_mode.py         → ✓ feature flag strategy_managed / exchange_manager
  dynamic_pairlist.py               → ✓ auto-popola dynamic_pairs.json per freqtrade
  risk_gate.py                      → ✓ risk gating
  update_applier.py                 → ✓ applica update su stato DB
  update_planner.py                 → ✓ pianifica update da intents
  test_update_applier.py            → ✓ test
  test_update_planner.py            → ✓ test
  tests/test_freqtrade_bridge.py    → ✓ 82 pass (normalizer + SignalBridgeStrategy + plot_config)
  tests/test_freqtrade_callback.py  → ✓ test callback writer
  tests/test_exchange_order_manager.py → ✓ test Step 22-23
  tests/test_order_reconciliation.py   → ✓ test Step 24
  tests/test_freqtrade_exchange_backend.py → ✓ CREATO GAP-03 — 19 test (field mapping, error handling, gateway integration)
  tests/test_machine_event.py             → ✓ CREATO GAP-04 — 18 test (unit rule engine + integrazione TP2→BE→EXIT_BE)
  tests/test_phase6_e2e.py             → ✓ evidenza e2e Fase 6 (60 pass)
  tests/test_dynamic_pairlist.py       → ✓ test pairlist dinamica
  tests/test_protective_orders_mode.py → ✓ test feature flag
freqtrade/user_data/strategies/
  SignalBridgeStrategy.py           → ✓ bridge IStrategy entry + exit + stoploss + sizing + plot_config + bot_start() watchdog (GAP-03)
db/migrations/
  013_protective_orders_mode.sql    → ✓ migration feature flag mode per trade
docs/
  GAP_ANALYSIS.md                   → ✓ AGGIORNATO 2026-03-28 — GAP-01, GAP-02, GAP-03, GAP-04 chiusi
  FASE_6_ALLINEAMENTO_AGENTE.md     → ✓ discrepanze operation_rules vs runtime (Step A-E risolte)
  FIX_FREQUI_MARKERS.md             → ✓ guida chart FreqUI + limiti strutturali documentati
  FIX_FREQUI_MARKERS_AGENTE.md      → handoff agente per fix marker/chart
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
- `src/parser/models/tests/` — 79 test (Price, Intent, TargetRef, TraderParseResult, entities, operational models)
- `src/parser/tests/` — 62 test (RulesEngine: load, classify, intents, blacklist, merge)
- `src/telegram/tests/` — 63 test (channel config, blacklist, media, router, reply chain, recovery, router_integration, **router_phase4** +13)
- `src/validation/tests/` — 25 test (CoherenceChecker)
- `src/operation_rules/tests/` — 28 test (loader, engine, risk_calculator)
- `src/target_resolver/tests/` — 14 test (resolver: SYMBOL, STRONG, GLOBAL, eligibility)

Nota: `test_listener_recovery::test_catchup_skips_channel_with_no_last_id` risulta FAILED anche sul commit base (pre-Step 15) — regressione preesistente, non introdotta da Step 15.

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

3. **Bridge freqtrade Step 20** — catena documentata fino all'operatività: `channels.yaml` reale presente (`PifSignal`), runbook minimo per listener/freqtrade/FreqUI/Telegram bot pronto, parser e operation rules verificati per i trader attesi del canale multi-trader. L'osservabilità end-to-end live con messaggio Telegram reale non è ancora stata osservata in questo workspace.

4. **Canali multi-trader** — `telegram_source_map.json` può e deve marcare i chat id multi-trader, ma questo non elimina gli `UNRESOLVED` per update brevi senza alias; serve contesto reply-chain robusto.

5. **`canonical_schema.py` carica da CSV** — `schema_consigliato_finale_parser.csv` è marcato come DELETE nel git status (`D schema_consigliato_finale_parser.csv`). Se il file viene eliminato, `canonical_schema.py` restituisce `{}` silenziosamente. Controllare prima di procedere.

6. **Gap aperti documentati in `docs/GAP_ANALYSIS.md`** — Analisi 2026-03-28 aggiornata. Stato gap:
   - **GAP-01** ✅ CHIUSO (2026-03-28) — UPDATE UNRESOLVED ora instradato in review_queue; eligibility e conflict detection erano già corretti
   - **GAP-02** ✅ CHIUSO (2026-03-28) — `EntryPricePolicy` già integrata in `confirm_trade_entry()` di SignalBridgeStrategy (file era untracked al momento dell'analisi)
   - **GAP-03** Watchdog ordini orfani: riconciliazione solo a bootstrap, nessun polling periodico — step 24 Fase 6
   - **GAP-04** `machine_event.rules` dichiarato `NOT_SUPPORTED` con sentinel — non eseguito, step 23 Fase 6
   - **GAP-05** Update Applier frammentato — U_CLOSE_PARTIAL, U_ADD_ENTRY non hanno handler — step 23 Fase 6
   - **GAP-06** `price_corrections` futura feature, rimossa dalla lista gap attivi (out-of-scope)
   - **GAP-07** `live_equity` capital sizing non implementato (solo `static_config`) — bassa priorità
   Vedere `docs/GAP_ANALYSIS.md` per dettaglio completo con impatto e priorità.

---

## Rischi di regressione durante migrazione

1. **Ambiente di test non sempre allineato all'ambiente di progetto** — fuori dalla `.venv` i test possono fallire già in collection per dipendenze mancanti (es. `pydantic`). Valutare sempre lo stato con l'interprete del progetto.

2. **Working tree non pulito su alcuni profili trader** — sono presenti modifiche locali in corso su `trader_c` e `trader_d`; il quadro documentale aggiornato riflette il ramo di lavoro corrente, ma non equivale a stato consolidato o pronto al commit.

3. **parse_result_normalized_json** — il campo nel DB contiene output della vecchia architettura. Dopo la migrazione produrrà output del nuovo TraderParseResult. Il DB test è separato — nessun rischio sul DB live.

4. **Configurazione live ancora parziale** — `config/channels.yaml` ora include il canale reale `PifSignal`, ma in questo workspace mancano ancora runtime Telegram live e runtime freqtrade reale per osservare la catena completa in esercizio.

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
[✓] Step 12 — Migration 011 + OperationalSignal/ResolvedSignal/ResolvedTarget models
[✓] Step 13 — Operation Rules Engine: loader, risk_calculator, engine + config YAML — 28 test
[✓] Step 14 — Target Resolver + signals_query/signals_store/op_signals_store — 14 test
[✓] Step 15 — Integrazione nel Router (Layer 4+5 dopo VALID) — 13 test router_phase4
[✓] Step 16 — Eliminazione stub exchange/execution incompatibili + SignalBridgeStrategy scheletro
[✓] Step 17 — freqtrade_callback.py + populate_exit + custom_stoploss
[✓] Step 18 — UPDATE intents su freqtrade (U_MOVE_STOP, U_CLOSE_FULL, U_CLOSE_PARTIAL, U_CANCEL_PENDING)
[✓] Step 19 — Smoke test dry_run Bybit (template + bootstrap pronti, limite ambiente documentato)
[✓] Step 20 — Configurazione canali live + monitoring operativo (limiti ambiente documentati)
[✓] Step 21 — Feature flag `exchange_manager` + contratto dati protettivi
[✓] Step 22 — `exchange_gateway.py` + `exchange_order_manager.py` (SL + TP reali dopo fill)
[✓] Step 23 — Update management ordini aperti (U_MOVE_STOP, U_CLOSE_FULL, U_CLOSE_PARTIAL, U_CANCEL_PENDING)
[✓] Step 24 — `order_reconciliation.py` bootstrap sync + watchdog leggero
[✓] Step A  — Router preserva `order_type` reale; normalizer espone `entry_prices` + `entry_split`
[✓] Step B  — Policy single-entry `first_in_plan`; `custom_entry_price()` usa E1 come prezzo LIMIT
[✓] Step C  — `EntryPricePolicy` + `check_entry_rate()` + `confirm_trade_entry()` rigetta fill fuori tolleranza
[✓] Step D  — `MACHINE_EVENT_RULES_NOT_SUPPORTED` sentinel; `allowed_update_directives` connette trader_hint al runtime
[✓] Step E  — `PRICE_CORRECTIONS_NOT_SUPPORTED` sentinel; tabella contratto runtime in `FREQTRADE_CONFIG.md`
[✓] FreqUI  — `plot_config` + subplot "Bridge Events" in `SignalBridgeStrategy.py`
[✓] Fase 7 Step 21 — DB migrations (015_backtest_runs.sql, 016_backtest_trades.sql) + storage asincrono (BacktestRunStore, BacktestTradeStore) — 9 test
[✓] Fase 7 Step 18b — replay_operation_rules.py: batch script + safety guard + ReplayStats — 6 test
[✓] Fase 7 Step 19 — SignalBridgeBacktestStrategy.py: IStrategy completa backtesting (tutti gli hook) — 17 test
[✓] Fase 7 Step 20 — BacktestRunner (runner.py) + CLI (run_backtest.py) + test_runner.py — 20 test
[✓] Fase 7 Step 22 — ReportGenerator (report.py) + CLI (run_report.py) + test_report.py — 27 test
[✓] Fase 7 Step EXE-1 — Fix capital gates replay + entity normalization + freqtrade config → primo run end-to-end completato (2026-03-29)
```

**Regola: non iniziare uno step prima che il precedente sia testato e funzionante.**

---

## Fase 7 — Sessione esecuzione end-to-end (2026-03-29)

### Obiettivo
Prima run completa del pipeline backtesting: replay segnali storici → download OHLCV → run freqtrade → import trade results.

### Fix applicati

#### EXE-1 — Capital gates bypass in replay mode
**File:** `src/operation_rules/engine.py`

Diagnosi: 98% dei segnali veniva bloccato in `replay_operation_rules.py` perché `sum_global_exposure()` / `sum_trader_exposure()` leggono la tabella `signals` live — in replay le posizioni non si chiudono mai, quindi il budget si esaurisce dopo i primi signal.

Fix: aggiunto parametro `skip_capital_gates: bool = False` a `engine.apply()`. Gates 5, 7, 8 wrappati in guard; se `skip_capital_gates=True` il gate viene saltato e annotato con `gate_X:skipped(replay)`.

Risultato: da 10 segnali inseriti → **537 segnali** inseriti nel backtest DB.

**File:** `parser_test/scripts/replay_operation_rules.py` — default `skip_capital_gates=True`; flag `--no-skip-capital-gates` per tornare al comportamento originale.

**File:** `parser_test/scripts/replay_operation_rules_orig.py` — backup del file originale prima del fix.

**File:** `parser_test/scripts/tests/test_replay_operation_rules.py` — `fake_apply()` aggiornato con `**kwargs` per accettare il nuovo parametro.

#### EXE-2 — Entity normalization: formato legacy DB → NewSignalEntities
**File:** `src/backtesting/chain_builder.py`

Il DB storico salva le entità in formato flat legacy:
- `stop_loss: 104103.0` (float grezzo)
- `take_profits: [109600.0, ...]` (lista di float)
- `entries: [{"sequence": 1, "price": float}]` (dict senza `order_type`)

`NewSignalEntities` richiede nested Pydantic: `StopLoss{price: Price{raw: str, value: float}}`, ecc.

Fix: aggiunte due funzioni di normalizzazione:
- `_normalize_new_signal_entities(raw: dict) -> dict` — normalizza SL, TP, entries
- `_normalize_update_entities(raw: dict) -> dict` — normalizza `new_sl_level`, `close_price`
- Helper `_price_obj(v: float) -> dict` — costruisce `{"raw": str(v), "value": float}`

Entrambe invocate da `_deserialize_entities()` prima di `model_validate()`.

#### EXE-3 — Freqtrade subprocess: path eseguibile
**File:** `src/backtesting/runner.py`

Il runner usava `["python", "-m", "freqtrade"]` che puntava al Python di sistema senza freqtrade installato.

Fix: uso path assoluto `.venv-freqtrade/Scripts/freqtrade.exe` (Windows). Fallback a `["python", "-m", "freqtrade"]` se l'exe non esiste.

#### EXE-4 — Freqtrade config: campi obbligatori mancanti
**File:** `src/backtesting/runner.py` — `_generate_freqtrade_config()`

Config generata mancava di:
- `datadir` / `user_data_dir` come path assoluti
- `pairlists` (required property)
- `entry_pricing` / `exit_pricing` (KeyError a runtime)

Fix: aggiunti tutti i campi. `_project_root` calcolato da `Path(__file__).resolve().parents[2]`.

#### EXE-5 — Strategy: `No module named 'src'`
**File:** `freqtrade/user_data/strategies/SignalBridgeBacktestStrategy.py`

`_PROJECT_ROOT = Path(__file__).resolve().parents[2]` puntava a `freqtrade/` invece che a `TeleSignalBot/`.

Il file si trova a `freqtrade/user_data/strategies/` → `parents[3]` = project root.

Fix: cambiato `parents[2]` → `parents[3]`.

### Risultati operativi

| Metrica | Valore |
|---|---|
| Segnali inseriti nel backtest DB | 537 |
| Coppie OHLCV scaricate | 95 pairs × 5m × 165 days |
| File `.feather` generati | 281 |
| Chain processate dal runner | 537 |
| Trade generati da freqtrade | 10 |
| Profitto totale | -430.636 USDT (-43.06%) |
| Win rate | 0% (tutti force_exit a fine backtest) |

### Rischio aperto: strategia genera troppo pochi trade

**Problema:** su 537 chain processate, solo 10 trade vengono aperti da freqtrade. Tutti i trade terminano con `force_exit` (fine finestra backtest), nessun SL/TP reale.

**Causa sospetta:** `populate_entry_trend()` in `SignalBridgeBacktestStrategy` genera `enter_long=1` o `enter_short=1` solo per la candela corrispondente all'`entry_time` del segnale. Se l'OHLCV 5m non ha una candela esatta in quell'istante (o se il prezzo LIMIT non viene raggiunto), nessun trade viene aperto.

**Prossimo step:** investigare la logica `populate_entry_trend()` — verificare che il lookup candele sia corretto, che i segnali MARKET vengano gestiti diversamente dai LIMIT, e che `custom_stoploss()` / `custom_exit()` siano raggiungibili.

---

## Note per Claude Code

- Leggi sempre `CLAUDE.md` prima di qualsiasi sessione
- Il documento autorevole per il parser è `docs/PRD_parser.md` — non i vecchi DOCS/
- I vecchi DOCS/ in `DOCS/` sono archivio storico — non seguire le istruzioni che contengono
- `TRADE_STATE_MACHINE.md`, `RISK_ENGINE.md`, `BOT_COMMANDS.md` sono target design futuro — non implementare ora
- Aggiorna questo file `AUDIT.md` quando completi ogni step
- `trader_d` risulta migrato e con suite dedicata presente; eventuali nuovi interventi vanno valutati sul working tree corrente, non sul vecchio stato pre-fix.
- `src/exchange/` stub legacy rimossi in Step 16; i nuovi punti di integrazione Fase 5 sono `src/execution/freqtrade_normalizer.py` e `freqtrade/user_data/strategies/SignalBridgeStrategy.py`.

---

*Aggiornato: 2026-03-25 (Step 15) — Phase 4 completa: Operation Rules Engine + Target Resolver integrati nel Router. Steps 12-15 ✓. Tutti i test della full suite passano (427/427). Smoke suite 298/299 (1 failure preesistente su test_listener_recovery non introdotto da Step 15).*

*Aggiornato: 2026-03-27 (Step 16) — Rimossi gli stub incompatibili (`src/exchange/adapter.py`, `bybit_rest.py`, `bybit_ws.py`, `reconcile.py`, `src/execution/planner.py`, `state_machine.py`). Creati `src/execution/freqtrade_normalizer.py`, `freqtrade/user_data/strategies/SignalBridgeStrategy.py` e i test unitari del bridge. Lo smoke test dry-run freqtrade resta bloccato finché non esiste un venv freqtrade dedicato disponibile.*

*Aggiornato: 2026-03-27 (Step 17) — Estesi `SignalBridgeStrategy.py` con `custom_stoploss()`, `populate_exit_trend()`, `custom_stake_amount()` e `leverage()`. Creato `src/execution/freqtrade_callback.py` con callback writer minimo e retry su `SQLITE_BUSY`. Aggiunti test unitari per stoploss, UPDATE `U_MOVE_STOP`, exit `U_CLOSE_FULL`, fill entry e close piena. Suite completa: 783 pass, 1 failure preesistente su `test_listener_recovery`.*

*Aggiornato: 2026-03-27 (Step 18) — Esteso il normalizer con UPDATE targettizzati normalizzati (`U_MOVE_STOP`, `U_CLOSE_FULL`, `U_CLOSE_PARTIAL`, `U_CANCEL_PENDING`) e metadata trade per evitare partial exit ripetute. `SignalBridgeStrategy.py` ora usa `check_entry_timeout()` per cancel pending e `adjust_trade_position()` per partial exits, mantenendo il confine di normalizzazione. `freqtrade_callback.py` persiste partial close, audit dedicato e protegge dal race `cancel-before-fill`. Test nuovi: partial close, cancel pending, close full da UPDATE, move stop a breakeven e race condition. Suite completa: 787 pass, 1 failure preesistente su `test_listener_recovery`.*

*Aggiornato: 2026-03-27 (Step 19) — Creato il template sicuro `freqtrade/user_data/config.template.json` per Bybit futures in `dry_run`, con whitelist ampia ma controllata, configurazione Telegram e `api_server`/FreqUI. Aggiunta la guida `docs/FREQTRADE_CONFIG.md` con bootstrap del venv freqtrade, copia template, validazione config, avvio dry-run e troubleshooting minimo (`pair_whitelist`, symbol non mappabile, `SQLITE_BUSY`). Il file locale `freqtrade/user_data/config.json` è ora ignorato da git. Smoke test end-to-end non eseguibile in questo workspace perché il modulo reale `freqtrade.strategy` non è installato nel venv del progetto.*

*Aggiornato: 2026-03-27 (Step 20) — Popolato `config/channels.yaml` con il canale reale `PifSignal` e documentato il suo uso multi-trader (`trader_a`, `trader_b`, `trader_c`, `trader_d`, `trader_3`). Aggiunto `docs/FREQTRADE_RUNBOOK.md` con avvio listener, avvio freqtrade, check FreqUI, check Telegram bot, query DB essenziali e comandi operativi base. Verificati localmente: loader `channels.yaml`, parser profile disponibili per tutti i trader attesi, operation rules caricabili tramite fallback globale e test router/channel config verdi. Smoke test con messaggio reale e passaggio effettivo a freqtrade non eseguibile in questo workspace per assenza di runtime Telegram live e modulo reale `freqtrade.strategy`; Fase 5 è quindi pronta sul piano di configurazione e monitoraggio, ma non ancora osservata end-to-end dal vivo in questo ambiente.*

*Aggiornato: 2026-03-27 (Fase 5 runtime dry_run) - Bootstrappato `.venv-freqtrade` con `freqtrade 2026.2` e creato `freqtrade/user_data/config.json` locale. Validati nel runtime freqtrade reale in `dry_run` con DB condiviso: `NEW_SIGNAL`, `U_MOVE_STOP`, `U_CLOSE_FULL`, `U_CLOSE_PARTIAL`, `U_CANCEL_PENDING`. Il bridge aggiorna correttamente il DB del bot e gli UPDATE principali sono stati osservati end-to-end. FreqUI/API server locale riattivata con pin `starlette<1.0.0` nel venv freqtrade; verificati `http://127.0.0.1:8080/` e `/docs` con risposta `200`. Resta non validato solo il listener Telegram live in questo workspace.*

*Aggiornato: 2026-03-27 (Fase 5 pairlist dinamica + docs) - Aggiunto `src/execution/dynamic_pairlist.py` e collegato il router per auto-popolare `freqtrade/user_data/dynamic_pairs.json` quando arriva un `NEW_SIGNAL` valido e mappabile. Corrette due regressioni emerse dal test end-to-end del bridge: lettura di `stop_loss` numerico in `OperationRulesEngine` e costruzione incompleta di `OperationalSignalRecord` nel router. Verificate le suite mirate: `src/telegram/tests/test_router_phase4.py`, `src/operation_rules/tests/test_engine.py`, `src/telegram/tests/test_router_integration.py`, `src/execution/tests`. Aggiornata la documentazione operativa e aggiunto `docs/COMANDI.md`. Resta aperta solo l'osservazione di un messaggio Telegram reale nello stesso ambiente.*

*Aggiornato: 2026-03-28 (Fase 6 completata in dry-run avanzato) - Implementati Step 21-24: feature flag `exchange_manager`, `exchange_gateway.py`, `exchange_order_manager.py`, update lifecycle (`U_MOVE_STOP`, `U_CLOSE_FULL`, `U_CLOSE_PARTIAL`, `U_CANCEL_PENDING`), `TP fill`/`SL fill`, `order_reconciliation.py` con bootstrap sync e watchdog leggero. Aggiunti test dedicati per manager, reconciliation e scenario end-to-end `test_phase6_e2e.py`. Evidenza concreta raccolta in `docs/FASE_6_COMPLETAMENTO.md`: entry fill, `SL` reale exchange-backed, ladder `TP` reale, stop update applicato, restart con riconciliazione riuscita, nessun doppio owner e nessun ordine duplicato aperto. Suite `src/execution` verde: 60 pass.*

*Aggiornato: 2026-03-28 (Fase 6 allineamento contratto — Step A–E) — Cinque step di allineamento tra `operation_rules` e runtime freqtrade completati:*
- *Step A: `router.py` preserva `order_type` reale per entry (non più sempre "LIMIT"); `freqtrade_normalizer.py` espone `entry_prices` e `entry_split` in `FreqtradeSignalContext`.*
- *Step B: Policy single-entry `first_in_plan` esplicita. `FreqtradeSignalContext` aggiunge `first_entry_price` / `first_entry_order_type`. `SignalBridgeStrategy.custom_entry_price()` usa E1 come prezzo LIMIT; fallback a `proposed_rate` per MARKET.*
- *Step C: `EntryPricePolicy`, `resolve_entry_price_policy()`, `check_entry_rate()`, `persist_entry_price_rejected_event()` nel normalizer. `confirm_trade_entry()` nella strategy rigetta fill fuori tolleranza e persiste evento `ENTRY_PRICE_REJECTED` nel DB.*
- *Step D: `MACHINE_EVENT_RULES_NOT_SUPPORTED = True` sentinel; `resolve_allowed_update_intents()` connette `trader_hint.auto_apply_intents` al runtime; `is_machine_event_mode()` forza fallback permissivo. `allowed_update_directives` property su `FreqtradeSignalContext` sostituisce `update_directives` nei metodi `close_full_requested`, `cancel_pending_requested`, `latest_partial_close`.*
- *Step E: `PRICE_CORRECTIONS_NOT_SUPPORTED = True` sentinel dichiarato in normalizer. Aggiunto docstring su scope `EntryPricePolicy` vs `price_sanity` (parse-time vs runtime). Aggiornati `FREQTRADE_CONFIG.md` e `FREQTRADE_RUNBOOK.md` con tabella contratto runtime (supportato/non supportato/garanzie). Aggiunti 7 test di alignment contract in `test_freqtrade_bridge.py` (pillars 1–4 + note price_sanity). Suite `src/execution`: 76 pass. Suite globale: 807 pass + 2 failure preesistenti invariati.*

*Aggiornato: 2026-03-28 (Fix FreqUI Markers) — Aggiunto `plot_config` e logica di plotting read-only a `SignalBridgeStrategy.py`. Main plot: linee SL (`bridge_sl`), TP1-3 (`bridge_tp1`..`bridge_tp3`), entry price (`bridge_entry_price`). Subplot "Bridge Events": barre per entry fill, partial exit, TP hit, SL hit, close completa, lette dalla tabella `events` del DB e mappate sulla candela più vicina. Nessuna modifica alla logica di trading. Aggiunti 7 test di plotting in `test_freqtrade_bridge.py`. Aggiornato `docs/FIX_FREQUI_MARKERS.md` con guida alla lettura del chart e limiti strutturali di FreqUI documentati. Suite `src/execution/tests/test_freqtrade_bridge.py`: 82 pass, 1 skip (pandas non disponibile nel test env).*

*Aggiornato: 2026-03-28 (Analisi gap pipeline) — Revisione completa del flusso parser→execution. Creato `docs/GAP_ANALYSIS.md` con elenco classificato (critici/medi/bassi) di 8 gap aperti. Aggiornate le sezioni AUDIT: ordine di sviluppo (Steps 21-24 + A-E + FreqUI marcati completi), file execution spostati in KEEP, conflitti architetturali integrati con i gap residui. Stato globale confermato: flusso end-to-end funzionante per `exchange_manager` mode; gap aperti documentati ma non bloccanti per operatività base.*

*Aggiornato: 2026-03-29 (Promemoria parser) - Applicato fix locale in `src/parser/trader_profiles/trader_c/profile.py` per materializzare `entities.new_stop_level = "ENTRY"` quando il parser riconosce `U_MOVE_STOP_TO_BE`, cosi il bridge riceve un payload operativo completo. Resta da rivedere l'architettura parser: alcune semantiche critiche sono ancora duplicate nei profili trader invece che normalizzate nel core centrale. Da pianificare un hardening del layer canonico/validation prima del passaggio ai livelli successivi, con controlli di coerenza del tipo `intent -> campi obbligatori` (es. `U_MOVE_STOP_TO_BE` richiede `new_stop_level`), idealmente su modelli tipizzati/validatori centrali invece che lasciati ai parser trader-specifici.*

*Aggiornato: 2026-03-29 (Parser notes) - Creato `docs/PARSER_NOTES.md` come nota separata per questioni solo parser: separazione core/trader-specifici, validazione semantica centrale, gestione delle ambiguita tra `U_MOVE_STOP` e `U_MOVE_STOP_TO_BE`, e contratto parser -> bot per usare il fill reale come source of truth del vero breakeven.*

*Aggiornato: 2026-03-29 (Entry process notes) - Creato `docs/ENTRY_PROCESS_NOTES.md` per rivedere il processo di entry lato bot/runtime: rapporto tra segnali `PENDING`, ordini `LIMIT` pendenti, `custom_entry_price()` e `confirm_trade_entry()`, con il caso reale `raw 2`/`XLMUSDT` come riferimento.*

*Aggiornato: 2026-03-29 (Dry-run stoploss notes) - Creato `docs/DRYRUN_STOPLOSS_NOTES.md` per documentare il caso BTC con multipli ordini `stoploss` ancora `open` in `dry_run` Freqtrade: SL logico corretto lato bot, ma persistenza/runtime sporchi nel lifecycle `stoploss_on_exchange`. Decisione attuale: non correggere subito e validare prima su caso reale senza ulteriori interventi.*

*Aggiornato: 2026-03-29 (Fase 7 — Step 22 completato) — Report Generator:*
- *`src/backtesting/report.py`: `ScenarioMetrics`, `MonthlyMetrics`, `BacktestSummaryReport` Pydantic models. Funzioni pure esposte per test: `_compute_max_drawdown`, `_compute_sharpe`, `_compute_scenario_metrics`, `_compute_monthly_metrics`, `_generate_html_table`, `_write_comparison_csv`, `_write_comparison_monthly_csv`. `ReportGenerator.generate()` carica trades da DB, calcola metriche, scrive: `summary.json`, `comparison_table.csv`, `comparison_table.html`, `comparison_table_monthly.csv`, `per_scenario/{name}/trades.csv + equity_curve.csv`, `parser_quality/signal_coverage.csv + update_chain_stats.csv`. `_try_plot_profit` best-effort. CSV con UTF-8-sig. Profit factor capato a 999 se no losses.*
- *`src/backtesting/run_report.py`: CLI argparse (`--db-path`, `--run-ids`, `--output`). Carica run COMPLETED da DB, ricostruisce `BacktestRunResult`, chiama `ReportGenerator.generate()`.*
- *`src/backtesting/tests/test_report.py`: 27 test: metrics calcolo, win rate, profit factor edge case (no losses), Sharpe, max drawdown, monthly grouping, CSV encoding UTF-8-sig, HTML structure, generate() integrazione con mock storage.*
- *Suite backtesting totale: 97 test, tutti passano.*

*Aggiornato: 2026-03-29 (Fase 7 — Step 20 completato) — Completato Step 20 del sistema backtesting:*
- *`src/backtesting/runner.py`: BacktestRunResult + BacktestRunner (ScenarioApplier → subprocess → storage). _normalize_pair, _collect_pairs, _generate_freqtrade_config locali. _run_freqtrade usa `python -m freqtrade` su win32. _find_results_file con fallback al JSON più recente.*
- *`src/backtesting/run_backtest.py`: CLI argparse (--scenario-config, --db-path, --trader, --output). Trader override via _run_with_trader_override() senza rompere la firma pubblica di BacktestRunner.run(). Stampa tabella risultati.*
- *`src/backtesting/tests/test_runner.py`: 20 test in 5 classi: TestNormalizePair (7), TestCollectPairs (3), TestGenerateFreqtradeConfig (2), TestBacktestRunnerScenario (3), TestWindowsCommandDetection (2), TestFindResultsFile (3).*
- *Suite backtesting totale: 70 test raccolti, tutti passano (53 su test_runner/test_storage/test_scenario/test_chain_builder + 17 test_strategy_signal_bridge).*
