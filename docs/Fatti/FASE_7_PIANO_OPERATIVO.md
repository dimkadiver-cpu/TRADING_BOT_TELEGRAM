# Fase 7 — Piano Operativo

**Data:** 2026-03-29
**Basato su:** PRD_FASE_7.md (design review completato 2026-03-28)
**Prerequisiti:** Fase 1–6 complete, 427+ test green

---

## Decisione architetturale: DB dedicato

Il backtesting usa un DB dedicato (`db/backtest.sqlite3`), separato dal DB live.
Questo garantisce isolamento totale e permette di ri-processare i dati senza rischi.

Flusso di preparazione dati:
```
import_history.py --db-path db/backtest.sqlite3     ← GIA' ESISTE (scarica da Telegram)
replay_parser.py --db-path db/backtest.sqlite3      ← GIA' ESISTE (parsa messaggi)
replay_operation_rules.py --db-path db/backtest.sqlite3  ← NUOVO Step 18b
```

## Ordine di esecuzione

```
Step 16   Models + ChainBuilder          ─┐
Step 17   Scenario + YAML               ─┤── paralleli (Batch 1)
Step 21   DB Migrations + Storage        ─┤
Step 18b  replay_operation_rules.py      ─┘
          │
Step 18a  OHLCV download                ← operativo, nessun codice
          │
Step 19   BacktestStrategy              ← dipende da 16, 17 (Batch 2)
          │
Step 20   Runner                        ← dipende da 16, 17, 19, 21 (Batch 3)
          │
Step 22   Report Generator              ← dipende da 20, 21 (Batch 4)
          │
Step 23   Integration Test              ← dipende da tutto (Batch 5)
```

**Nota:** Step 18b (replay_operation_rules.py) è parallelizzabile con 16/17/21
perché non ha dipendenze di codice dagli altri step — usa solo moduli esistenti.

---

## Step 16 — Signal Chain Builder

**File da creare:**
- `src/backtesting/__init__.py`
- `src/backtesting/models.py`
- `src/backtesting/chain_builder.py`
- `src/backtesting/tests/__init__.py`
- `src/backtesting/tests/conftest.py`
- `src/backtesting/tests/test_chain_builder.py`

**Modelli Pydantic (models.py):**
- `ChainedMessage` — wrapper di un messaggio nella chain (NEW_SIGNAL o UPDATE)
  - Campi critici: `raw_message_id`, `parse_result_id`, `telegram_message_id`, `message_ts` (UTC), `message_type`, `intents`, `entities` (NewSignalEntities | UpdateEntities | None), `op_signal_id`, `attempt_key`, `is_blocked`, `block_reason`, `risk_budget_usdt`, `position_size_usdt`, `entry_split`, `management_rules`
- `SignalChain` — NEW_SIGNAL + lista UPDATE ordinati per timestamp
  - Campi: `chain_id` (f"{trader_id}:{attempt_key}"), `trader_id`, `symbol`, `side`, `new_signal`, `updates`, `entry_prices`, `sl_price`, `tp_prices`, `open_ts`, `close_ts`
- `BacktestReadyChain` — chain dopo applicazione scenario (creato in Step 17, definito qui)
  - Campi aggiuntivi rispetto a SignalChain: `scenario_name`, `applied_updates`, `effective_sl_price`, `effective_tp_prices`, `effective_entry_split`, `effective_position_size_usdt`, `effective_risk_pct`

**ChainBuilder (chain_builder.py):**
- `SignalChainBuilder.build_all(db_path, trader_id=None, date_from=None, date_to=None) → list[SignalChain]`
- Pass 1: JOIN `operational_signals` + `parse_results` + `raw_messages` WHERE message_type='NEW_SIGNAL'
- Pass 2: per ogni UPDATE, leggi `resolved_target_ids` (JSON list di op_signal_id). Fallback: `reply_to_message_id` se NULL
- Deserializza `parse_result_normalized_json` → NewSignalEntities o UpdateEntities
- Assembla chain per attempt_key, ordina updates per message_ts ASC

**Dipendenze da moduli esistenti:**
- `src/parser/models/new_signal.py` → NewSignalEntities
- `src/parser/models/update.py` → UpdateEntities
- `src/parser/models/canonical.py` → Price, Intent, TargetRef

**Test (10–12):**
- Chain con 0, 1, 3 UPDATE
- UPDATE linkato via resolved_target_ids
- UPDATE linkato via reply_to_message_id (fallback)
- UPDATE orfano (non linkabile → log warning, skip)
- Filtro per trader_id e date_from/date_to
- Deserializzazione entities (NewSignalEntities, UpdateEntities)
- Chain con segnale bloccato (is_blocked=True)
- Ordine cronologico updates

---

## Step 17 — Scenario Condition System

**File da creare:**
- `src/backtesting/scenario.py`
- `config/backtest_scenarios.yaml`
- `src/backtesting/tests/test_scenario.py`

**Schema YAML (backtest_scenarios.yaml):**
- 6 scenari predefiniti: follow_full_chain, signals_only, sl_to_be_after_tp2, aggressive_averaging, double_risk, gate_warn
- Sezione `backtest_settings`: trader_filter, date_from, date_to, ohlcv_source, timeframe, capital_base_usdt, exchange, max_open_trades

**Modelli (scenario.py):**
- `ScenarioConditions` — Pydantic model dei flag scenario
- `BacktestScenario` — name + description + conditions
- `BacktestSettings` — settings globali del run
- `ScenarioConfig` — lista scenari + settings (da YAML)
- `ScenarioLoader.load(path) → ScenarioConfig`

**ScenarioApplier:**
- `ScenarioApplier.apply(chain: SignalChain, scenario: BacktestScenario) → BacktestReadyChain`
- `ScenarioApplier.apply_all(chains, scenario) → list[BacktestReadyChain]`
- Logica per condizione:
  - `signals_only=True` → applied_updates=[], exit via SL+TP originali
  - `follow_full_chain=True` → include tutti gli UPDATE
  - `sl_to_be_after_tp2=True` → dopo U_TP_HIT con tp≥2, effective_sl=entry_prices[0]
  - `vary_entry_pct=0.5` → ridistribuisce entry_split
  - `risk_pct_variant=2.0` → ricalcola position_size via risk_calculator.compute_position_size_from_risk()
  - `gate_mode_variant=warn` → include segnali bloccati

**Dipendenze:**
- `src/operation_rules/risk_calculator.py` → compute_position_size_from_risk()
- `src/backtesting/models.py` → SignalChain, BacktestReadyChain

**Test (8–10):**
- Ogni condizione isolata
- Combinazione sl_to_be_after_tp2 + follow_full_chain
- Ricalcolo sizing con risk_pct_variant
- Chain bloccata inclusa con gate_mode_variant=warn
- ScenarioLoader.load() da YAML

---

## Step 21 — DB Migrations + Storage (anticipato)

**File da creare:**
- `db/migrations/015_backtest_runs.sql`
- `db/migrations/016_backtest_trades.sql`
- `src/backtesting/storage.py`
- `src/backtesting/tests/test_storage.py`

**Tabelle:**
- `backtest_runs` — run_id, scenario_name, scenario_conditions_json, trader_filter, date_from, date_to, chains_count, chains_blocked, run_ts, status (RUNNING|COMPLETED|FAILED), error, output_dir
- `backtest_trades` — bt_trade_id, run_id (FK), chain_id, trader_id, pair, side, open_date, close_date, entry_price, close_price, profit_usdt, profit_pct, exit_reason, max_drawdown_pct, duration_seconds, sl_moved_to_be, raw_freqtrade_json
- Indici: idx_bt_trades_run, idx_bt_trades_chain

**Storage layer (storage.py):**
- `BacktestRunStore` — insert_run(), update_status(), get_run()
- `BacktestTradeStore` — import_from_freqtrade_json(run_id, results_path), get_trades_by_run()

**Test (5–6):**
- Insert + get run
- Update status RUNNING → COMPLETED
- Import trades da JSON fixture
- FK constraint (trade con run_id inesistente)

---

## Step 18b — replay_operation_rules.py (NUOVO)

**File da creare:**
- `parser_test/scripts/replay_operation_rules.py`
- `parser_test/scripts/tests/test_replay_operation_rules.py`

**Scopo:** batch script che processa parse_results → operational_signals + signals nel DB di backtest.

**CLI:**
```bash
python parser_test/scripts/replay_operation_rules.py \
  --db-path db/backtest.sqlite3 \
  --rules-dir config \
  --trader trader_3
```

**Args:** `--db-path` (obbligatorio), `--rules-dir` (default: config), `--trader` (opzionale), `--from-date`, `--to-date`, `--dry-run`

**Logica:**
1. Safety check: rifiuta esecuzione su `tele_signal_bot.sqlite3` (stessa guardia di import_history.py)
2. Applica migrazioni al DB se mancanti (via apply_migrations)
3. Leggi parse_results con is_executable=1 (+ filtri opzionali)
4. Per ogni parse_result:
   a. Ricostruisci TraderParseResult da parse_result_normalized_json
   b. `OperationRulesEngine.apply(parse_result, trader_id, db_path)` → OperationalSignal
   c. Se NEW_SIGNAL e non bloccato → inserisci in `signals`
   d. Se UPDATE → `TargetResolver.resolve()` per linkare al NEW_SIGNAL
   e. Inserisci in `operational_signals`
5. Stampa statistiche: processati, bloccati, errori, chain linkate

**Dipendenze (moduli esistenti):**
- `src/operation_rules/engine.py` → OperationRulesEngine
- `src/target_resolver/resolver.py` → TargetResolver
- `src/storage/operational_signals_store.py` → OperationalSignalsStore
- `src/core/migrations.py` → apply_migrations

**Test (4–6):**
- Safety check rifiuta DB live
- Processa NEW_SIGNAL → inserisce in signals + operational_signals
- Processa UPDATE → risolve target e linka
- Segnale bloccato → is_blocked=True in operational_signals
- Dry-run non scrive nel DB
- Filtro per trader_id

---

## Step 18a — Data Preparation: OHLCV (operativo)

**Nessun file di codice.** Step operativo pre-backtest.

```bash
cd freqtrade
.venv-freqtrade\Scripts\Activate.ps1
freqtrade download-data \
  --config user_data/config.json \
  --pairs BTC/USDT:USDT ETH/USDT:USDT SOL/USDT:USDT \
  --timeframe 5m --days 365 --exchange bybit --trading-mode futures
```

Output in: `freqtrade/user_data/data/bybit/futures/`

---

## Step 19 — BacktestStrategy (IStrategy)

**File da creare:**
- `freqtrade/user_data/strategies/SignalBridgeBacktestStrategy.py`
- `src/backtesting/tests/test_strategy_signal_bridge.py`

**Classe:** `SignalBridgeBacktestStrategy(IStrategy)`
- `INTERFACE_VERSION = 3`, `timeframe = "5m"`, `can_short = True`
- `use_custom_stoploss = True`, `minimal_roi = {"0": 100.0}`, `stoploss = -0.99`

**Handoff:** legge `signal_chains.json` (lista BacktestReadyChain) in `bot_start()` via `config["strategy_params"]["signal_chains_path"]`

**Hook implementati:**
1. `populate_entry_trend()` — segnala entry alla prima candela ≥ open_ts. enter_tag = chain_id
2. `custom_entry_price()` — forza entry_prices[0]
3. `adjust_trade_position()` — per non-chain: aggiunge size se OHLCV raggiunge E2/E3. Per follow_full_chain: applica U_REENTER/U_ADD_ENTRY
4. `custom_stoploss()` — applica U_MOVE_STOP al timestamp corretto (+1 candela). Attiva sl_to_be_after_tp2 dopo TP2
5. `custom_exit()` — TP ladder con partial_exit_ratio. Attiva U_CLOSE_FULL, U_CLOSE_PARTIAL dalla chain

**Vincoli architetturali:**
- File separato da SignalBridgeStrategy.py (live). NESSUN import condiviso
- Normalizzazione simboli locale (no import da src/execution/)
- order_filled_callback NON è chiamato in backtesting → TP ladder via custom_exit

**Test (8–10):**
- Entry signal alla candela corretta
- Custom entry price forzato
- SL move con delay +1 candela
- TP ladder partial close (30%/30%/40%)
- sl_to_be_after_tp2 dopo TP2 hit
- signals_only: nessun UPDATE applicato
- adjust_trade_position per averaging E2/E3

---

## Step 20 — Backtest Runner

**File da creare:**
- `src/backtesting/runner.py`
- `src/backtesting/run_backtest.py` (CLI)
- `src/backtesting/tests/test_runner.py`

**Flusso per ogni scenario:**
1. `SignalChainBuilder.build_all()` → list[SignalChain]
2. `ScenarioApplier.apply_all()` → list[BacktestReadyChain]
3. Scrive `signal_chains.json` (sidecar)
4. Genera `freqtrade_config.json` minimale
5. Chiama `freqtrade backtesting --config ... --strategy SignalBridgeBacktestStrategy`
6. Parsa risultati → `BacktestRunResult`
7. Salva in DB via `BacktestRunStore` + `BacktestTradeStore`

**CLI:**
```bash
python -m src.backtesting.run_backtest \
  --scenario-config config/backtest_scenarios.yaml \
  --db-path db/tele_signal_bot.sqlite3 \
  --trader trader_3 \
  --output backtest_reports/
```

**Windows:** rileva `sys.platform == "win32"` → usa `python -m freqtrade`

**Normalizzazione simboli:** funzione locale `_normalize_pair(symbol: str) → str` che converte `BTCUSDT` → `BTC/USDT:USDT`

**Test (6–8):**
- Generazione config freqtrade
- Scrittura sidecar JSON
- subprocess.run mockato
- Parsing risultati freqtrade
- Salvataggio in DB (mock storage)

---

## Step 22 — Report Generator

**File da creare:**
- `src/backtesting/report.py`
- `src/backtesting/run_report.py` (CLI)
- `src/backtesting/tests/test_report.py`

**Output:**
```
backtest_reports/run_{ts}/
  summary.json
  comparison_table.csv              (aggregato)
  comparison_table.html
  comparison_table_monthly.csv      (breakdown mensile)
  per_scenario/{name}/
    trades.csv
    equity_curve.csv
    freqtrade_charts/
  parser_quality/
    signal_coverage.csv
    update_chain_stats.csv
```

**Metriche per scenario:** total_trades, win_rate_pct, total_profit_pct, max_drawdown_pct, profit_factor, sharpe_ratio, avg_trade_duration_hours, sl_moved_to_be_count, chains_blocked_count

**Monthly breakdown:** stesse metriche raggruppate per strftime('%Y-%m', open_date)

**freqtrade charts:** chiama `freqtrade plot-profit --export-filename {results_json}`

**Test (8–10):**
- Calcolo metriche da trades fixture
- Generazione comparison_table.csv
- Generazione comparison_table_monthly.csv
- Generazione HTML
- summary.json parsabile
- signal_coverage.csv
- Cartella per_scenario creata correttamente

---

## Step 23 — Integration Test

**File da creare:**
- `src/backtesting/tests/test_integration.py`

**Setup:**
- DB fixture con 5 signal chain predefiniti
- OHLCV source mock con candele pre-calcolate
- freqtrade subprocess mockato

**Verifica:**
1. Build chains → apply 2 scenari → scrivi sidecar
2. Directory run creata correttamente
3. BacktestSummaryReport parsabile
4. comparison_table.csv con 2 righe scenario
5. comparison_table_monthly.csv presente

---

## Struttura finale

```
src/backtesting/
  __init__.py
  models.py
  chain_builder.py
  scenario.py
  runner.py
  storage.py
  report.py
  run_backtest.py
  run_report.py
  tests/
    __init__.py
    conftest.py
    test_chain_builder.py
    test_scenario.py
    test_strategy_signal_bridge.py
    test_runner.py
    test_storage.py
    test_report.py
    test_integration.py

parser_test/scripts/
  replay_operation_rules.py          ← NUOVO (Step 18b)
  tests/
    test_replay_operation_rules.py   ← NUOVO

freqtrade/user_data/strategies/
  SignalBridgeBacktestStrategy.py

config/
  backtest_scenarios.yaml

db/
  backtest.sqlite3                   ← DB dedicato backtesting (gitignored)

db/migrations/
  015_backtest_runs.sql
  016_backtest_trades.sql
```
