# PRD Fase 7 — Sistema 2: Backtesting

**Stato:** DESIGN REVIEW COMPLETATO — 2026-03-28
**Data originale:** 2026-03-27
**Dipendenze:** Fase 1–6 complete

---

## Context

Fase 1–6 complete (427+ test green, SignalBridgeStrategy live validata in dry-run, exchange_manager mode validata con riconciliazione e TP ladder).
Fase 7 costruisce **Sistema 2**: replay dei segnali storici contro OHLCV reale in freqtrade backtesting mode, con matrice di scenari configurabili e report comparativo.

**Obiettivi:**
- Validare parser (i segnali parsati hanno senso finanziario?)
- Ottimizzare operation rules (quale config performa meglio per trader?)
- Calcolare P&L storico (win rate, drawdown, Sharpe per trader)

**Vincolo principale:** il backtesting usa un **DB dedicato** (`db/backtest.sqlite3`), separato dal DB live. Il DB live non viene mai letto né scritto dal sistema di backtest.

**Preparazione dati:** prima del backtest, un flusso di preparazione popola il DB dedicato:
1. `import_history.py` — scarica messaggi da Telegram → `raw_messages`
2. `replay_parser.py` — parsa i messaggi → `parse_results`
3. `replay_operation_rules.py` (nuovo) — applica operation rules → `operational_signals` + `signals`

---

## Flusso generale

```
Telegram source ID
      ↓
import_history.py --db-path db/backtest.sqlite3        [GIA' ESISTE]
      ↓
raw_messages (backtest DB)
      ↓
replay_parser.py --db-path db/backtest.sqlite3         [GIA' ESISTE]
      ↓
parse_results (backtest DB)
      ↓
replay_operation_rules.py --db-path db/backtest.sqlite3  [NUOVO — Step 18b]
      ↓
operational_signals + signals (backtest DB)
      ↓
SignalChainBuilder        ricostruisce chain: NEW_SIGNAL + UPDATE via resolved_target_ids
      ↓
ScenarioApplier           applica condizioni (follow_full_chain, sl_to_be, vary_entry_pct, …)
      ↓
BacktestRunner            scarica OHLCV via freqtrade download-data + scrive sidecar JSON
      ↓
SignalBridgeBacktestStrategy (IStrategy)  — mappa chain su DataFrame OHLCV
      ↓
freqtrade results JSON
      ↓
ReportGenerator           metrics, comparison table CSV/HTML + freqtrade plot-profit charts
```

---

## Step 16 — Signal Chain Builder

**Deliverable:** `src/backtesting/models.py` + `src/backtesting/chain_builder.py`

### Modelli Pydantic

```python
class ChainedMessage(BaseModel):
    """Un messaggio della chain (NEW_SIGNAL o UPDATE)."""
    raw_message_id: int
    parse_result_id: int
    telegram_message_id: int
    message_ts: datetime                    # UTC — critico per realismo BT
    message_type: Literal["NEW_SIGNAL", "UPDATE"]
    intents: list[str]
    entities: NewSignalEntities | UpdateEntities | None   # tipizzato, deserializzato da parse_result_normalized_json
    op_signal_id: int | None                # da operational_signals
    attempt_key: str | None                 # da signals (solo NEW_SIGNAL)
    is_blocked: bool
    block_reason: str | None
    risk_budget_usdt: float | None
    position_size_usdt: float | None        # storico; ScenarioApplier sovrascrive se risk_pct_variant
    entry_split: dict[str, float] | None
    management_rules: dict[str, Any] | None


class SignalChain(BaseModel):
    """NEW_SIGNAL + tutti i suoi UPDATE ordinati per timestamp."""
    chain_id: str                           # f"{trader_id}:{attempt_key}"
    trader_id: str
    symbol: str
    side: Literal["BUY", "SELL"]
    new_signal: ChainedMessage
    updates: list[ChainedMessage]           # ordinati per message_ts ASC
    entry_prices: list[float]
    sl_price: float
    tp_prices: list[float]
    open_ts: datetime
    close_ts: datetime | None               # ts di U_CLOSE_FULL / U_SL_HIT se presente
```

### SignalChainBuilder

`SignalChainBuilder.build_all(db_path, trader_id=None, date_from=None, date_to=None)`

1. **Pass NEW_SIGNAL:** JOIN `signals` + `operational_signals` + `parse_results` + `raw_messages`
2. **Pass UPDATE linkage:** per ogni UPDATE in `operational_signals`, leggi `resolved_target_ids` (JSON list di op_signal_id già risolti dal router in fase di acquisizione). Fallback: `reply_to_message_id` solo se `resolved_target_ids IS NULL`.
3. **Assembla** `SignalChain` per ogni attempt_key
4. **Deserializza entities:** discrimina su `message_type` → `NewSignalEntities` o `UpdateEntities` da `parse_result_normalized_json`

**Riusa:** `src/storage/signals_query.py`, `src/storage/raw_messages.py`

**Test:** 10–12 test in `src/backtesting/tests/test_chain_builder.py` con DB in-memory

---

## Step 17 — Scenario Condition System

**Deliverable:** `src/backtesting/scenario.py` + `config/backtest_scenarios.yaml`

### Schema YAML

```yaml
scenarios:

  - name: follow_full_chain
    description: "Applica tutti gli UPDATE in ordine (SL moves, partial closes, ecc.)"
    conditions:
      follow_full_chain: true
      signals_only: false
      sl_to_be_after_tp2: false
      vary_entry_pct: null
      risk_pct_variant: null
      gate_mode_variant: null

  - name: signals_only
    description: "Solo apertura/chiusura, ignora UPDATE chain"
    conditions:
      follow_full_chain: false
      signals_only: true
      sl_to_be_after_tp2: false

  - name: sl_to_be_after_tp2
    description: "Sposta SL a breakeven automaticamente quando TP2 viene raggiunto"
    conditions:
      follow_full_chain: true
      sl_to_be_after_tp2: true

  - name: aggressive_averaging
    description: "50% allocation su tutte le entry averaging"
    conditions:
      follow_full_chain: true
      vary_entry_pct: 0.50

  - name: double_risk
    description: "Test con 2% risk per segnale invece del default"
    conditions:
      follow_full_chain: true
      risk_pct_variant: 2.0

  - name: gate_warn
    description: "gate_mode=warn — include anche segnali bloccati"
    conditions:
      follow_full_chain: true
      gate_mode_variant: warn

backtest_settings:
  trader_filter: null             # null = tutti; o "trader_3"
  date_from: null                 # YYYY-MM-DD
  date_to: null
  ohlcv_source: bybit_api        # bybit_api | local_files
  timeframe: 5m
  capital_base_usdt: 1000.0
  exchange: bybit
  max_open_trades: 10
```

### ScenarioApplier

`ScenarioApplier.apply(chain: SignalChain, scenario: BacktestScenario) → BacktestReadyChain`

| Condizione | Comportamento |
|------------|---------------|
| `signals_only=True` | `applied_updates = []`, ignora tutti gli UPDATE. Exit via SL + TP originali. |
| `follow_full_chain=True` | Include tutti gli UPDATE in ordine cronologico. Per AVERAGING: usa chain per determinare se E2/E3 furono fillate (U_CANCEL_PENDING o U_TP_HIT prima del prezzo E2 = non fillata). |
| `sl_to_be_after_tp2=True` | Scansiona UPDATE per `U_TP_HIT` con tp≥2 → `effective_sl_price = entry_prices[0]` |
| `vary_entry_pct=0.5` | Ridistribuisce `entry_split` con il peso specificato |
| `risk_pct_variant=2.0` | Override `risk_pct_of_capital` + **ricalcola `position_size_usdt`** via `risk_calculator.compute_position_size_from_risk()`. Default storico se assente. |
| `gate_mode_variant=warn` | Include segnali bloccati (cambia filtro) |

**Multi-entry per scenari non-chain** (signals_only e altri): E2/E3 vengono simulati via `adjust_trade_position` nella BacktestStrategy se il prezzo OHLCV li raggiunge.

**Riusa:** `src/operation_rules/loader.py` per leggere i default, `src/operation_rules/risk_calculator.py` per ricalcolo sizing

**Test:** 8–10 test in `src/backtesting/tests/test_scenario.py` — no DB necessario

---

## Step 18a — Data Preparation: OHLCV (prerequisito operativo)

**Deliverable:** nessun file di codice — step operativo da eseguire prima del backtest.

I dati OHLCV vengono scaricati tramite il comando nativo di freqtrade, che produce dati in formato nativo compatibile con `freqtrade backtesting`. Non serve implementare un layer intermedio.

### Comando

```bash
cd freqtrade
.venv-freqtrade\Scripts\Activate.ps1  # Windows
freqtrade download-data \
  --config user_data/config.json \
  --pairs BTC/USDT:USDT ETH/USDT:USDT SOL/USDT:USDT \
  --timeframe 5m \
  --days 365 \
  --exchange bybit \
  --trading-mode futures
```

### Output

```
freqtrade/user_data/data/bybit/futures/
  BTC_USDT_USDT-5m.json
  ETH_USDT_USDT-5m.json
  SOL_USDT_USDT-5m.json
  ...
```

`BacktestRunner` configura `datadir` nel `freqtrade_config.json` generato puntando a questa directory. Nessuna conversione di formato necessaria.

**Note:** i pair devono essere normalizzati nel formato freqtrade (`BTC/USDT:USDT`) prima del download. Usa normalizzazione locale nel runner (Step 20).

---

## Step 18b — Data Preparation: Backtest DB

**Deliverable:** `parser_test/scripts/replay_operation_rules.py`

Il backtesting usa un DB dedicato (`db/backtest.sqlite3`), separato dal DB live. Il flusso di preparazione è:

### Flusso completo

```bash
# 1. Scarica messaggi storici da Telegram (GIA' ESISTE)
python parser_test/scripts/import_history.py \
  --db-path db/backtest.sqlite3 \
  --chat-id <CHAT_ID> \
  --from-date 2025-06-01 \
  --to-date 2026-03-01

# 2. Parsa i messaggi (GIA' ESISTE)
python parser_test/scripts/replay_parser.py \
  --db-path db/backtest.sqlite3

# 3. Applica operation rules (NUOVO)
python parser_test/scripts/replay_operation_rules.py \
  --db-path db/backtest.sqlite3 \
  --rules-dir config
```

### replay_operation_rules.py

Script batch che processa tutti i `parse_results` tramite `OperationRulesEngine` e `TargetResolver`.

**Logica:**
1. Applica migrazioni 011-016 al DB se mancanti (via `apply_migrations`)
2. Leggi tutti i `parse_results` con `is_executable=1` (o filtro opzionale `--trader`, `--from-date`, `--to-date`)
3. Per ogni parse_result:
   a. Ricostruisci `TraderParseResult` da `parse_result_normalized_json`
   b. Chiama `OperationRulesEngine.apply(parse_result, trader_id, db_path)` → `OperationalSignal`
   c. Se `message_type='NEW_SIGNAL'` e non bloccato: inserisci in `signals` tramite `SignalsStore`
   d. Se UPDATE: chiama `TargetResolver.resolve()` per linkare al NEW_SIGNAL
   e. Inserisci in `operational_signals` tramite `OperationalSignalsStore`
4. Stampa statistiche: processati, bloccati, errori, chain linkate

**CLI args:**
- `--db-path` (obbligatorio)
- `--rules-dir` (default: `config`)
- `--trader` (opzionale: filtra per trader_id)
- `--from-date`, `--to-date` (opzionale)
- `--dry-run` (opzionale: processa senza scrivere nel DB)

**Riusa:** `src/operation_rules/engine.py`, `src/target_resolver/resolver.py`, `src/storage/operational_signals_store.py`, `src/storage/signals_store.py`

**Safety:** rifiuta di eseguire su `tele_signal_bot.sqlite3` (stessa guardia di `import_history.py`)

**Test:** 4–6 test in `parser_test/scripts/tests/test_replay_operation_rules.py`

---

## Step 19 — freqtrade IStrategy

**Deliverable:** `freqtrade/user_data/strategies/SignalBridgeBacktestStrategy.py`

### Interfaccia

```python
class SignalBridgeBacktestStrategy(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "5m"
    can_short = True
    use_custom_stoploss = True
    minimal_roi = {"0": 100.0}      # disabilitato — usiamo custom_exit
    stoploss = -0.99                 # disabilitato — usiamo custom_stoploss
```

### Handoff Protocol

Il runner scrive `signal_chains.json` (lista `BacktestReadyChain` serializzata) **prima** di invocare freqtrade. La strategy lo legge in `bot_start()` via `config["strategy_params"]["signal_chains_path"]`.

**Nota architetturale:** file completamente separato da `SignalBridgeStrategy.py` (live). Nessun import condiviso. `order_filled_callback` non è chiamato in backtesting mode — la gestione TP ladder è implementata tramite `custom_exit`.

### Hook implementati

| Hook | Comportamento |
|------|---------------|
| `populate_entry_trend()` | Segnala entry alla prima candela ≥ `open_ts` del chain. `enter_tag = chain_id` |
| `custom_entry_price()` | Forza `entry_prices[0]` per limit order realistico |
| `adjust_trade_position()` | Per scenari non-chain (signals_only ecc.): aggiunge size se OHLCV raggiunge E2/E3 (AVERAGING). Per follow_full_chain: applica U_REENTER / U_ADD_ENTRY dalla chain. |
| `custom_stoploss()` | Applica `U_MOVE_STOP` dal chain UPDATE al timestamp corretto (+1 candela per realismo). Attiva `sl_to_be_after_tp2` dopo TP2. |
| `custom_exit()` | TP ladder: ritorna `partial_exit_ratio` quando OHLCV raggiunge TP1/TP2/TP3 (ratio da `tp_close_distribution`). Attiva `U_CLOSE_FULL`, `U_CLOSE_PARTIAL` dalla chain. |

### Realismo

- Entry price = `entry_prices[0]`, non il prezzo di apertura candela
- SL update applicato alla candela successiva al timestamp dell'UPDATE (+1 candela delay)
- TP chiusure parziali via `custom_exit` con `partial_exit_ratio` (es. 0.3 per TP1, 0.3 per TP2, 1.0 per TP3)
- `sl_to_be_after_tp2` scatta dopo la chiusura della candela TP2, non istantaneamente
- `signals_only`: nessun UPDATE applicato, trade gestita puramente da SL e TP originali

**Simboli:** normalizzazione interna tramite logica locale (no import da live strategy) — `BTCUSDT → BTC/USDT:USDT`

**Test:** 8–10 test con DataFrame fixture e mock freqtrade trade object

---

## Step 20 — Backtest Runner

**Deliverable:** `src/backtesting/runner.py` + `src/backtesting/run_backtest.py` (CLI)

### Flusso

```python
BacktestRunner.run(scenario_config_path, db_path) → list[BacktestRunResult]
```

Per ogni scenario:
1. `SignalChainBuilder.build_all()` → lista `SignalChain`
2. `ScenarioApplier.apply_all()` → lista `BacktestReadyChain`
3. Scrive `backtest_reports/run_{scenario}_{ts}/signal_chains.json`
4. Genera `freqtrade_config.json` minimale per lo scenario
5. Chiama `freqtrade backtesting --config ... --strategy SignalBridgeBacktestStrategy`
6. Raccoglie `BacktestRunResult`

### CLI

```bash
python -m src.backtesting.run_backtest \
  --scenario-config config/backtest_scenarios.yaml \
  --db-path db/tele_signal_bot.sqlite3 \
  --trader trader_3 \
  --output backtest_reports/
```

**Windows:** runner rileva `sys.platform == "win32"` e usa `python -m freqtrade`.

**Test:** 6–8 test con `subprocess.run` mockato

---

## Step 21 — DB Migrations + Storage Layer

**Deliverable:** `db/migrations/015_backtest_runs.sql` + `db/migrations/016_backtest_trades.sql` + `src/backtesting/storage.py`

**Nota:** 013 è occupata da `013_protective_orders_mode.sql` (Fase 6). 014 riservata per eventuali estensioni Fase 6.

### Schema

```sql
-- 015_backtest_runs.sql
CREATE TABLE IF NOT EXISTS backtest_runs (
    run_id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario_name             TEXT NOT NULL,
    scenario_conditions_json  TEXT NOT NULL,
    trader_filter             TEXT,
    date_from                 TEXT,
    date_to                   TEXT,
    chains_count              INTEGER NOT NULL DEFAULT 0,
    chains_blocked            INTEGER NOT NULL DEFAULT 0,
    run_ts                    TEXT NOT NULL,
    status                    TEXT NOT NULL,   -- RUNNING | COMPLETED | FAILED
    error                     TEXT,
    output_dir                TEXT NOT NULL
);

-- 016_backtest_trades.sql
CREATE TABLE IF NOT EXISTS backtest_trades (
    bt_trade_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id             INTEGER NOT NULL REFERENCES backtest_runs(run_id),
    chain_id           TEXT NOT NULL,
    trader_id          TEXT NOT NULL,
    pair               TEXT NOT NULL,
    side               TEXT NOT NULL,
    open_date          TEXT NOT NULL,
    close_date         TEXT,
    entry_price        REAL NOT NULL,
    close_price        REAL,
    profit_usdt        REAL,
    profit_pct         REAL,
    exit_reason        TEXT,
    max_drawdown_pct   REAL,
    duration_seconds   INTEGER,
    sl_moved_to_be     INTEGER NOT NULL DEFAULT 0,
    raw_freqtrade_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_bt_trades_run ON backtest_trades(run_id);
CREATE INDEX IF NOT EXISTS idx_bt_trades_chain ON backtest_trades(chain_id);
```

`BacktestTradeStore.import_from_freqtrade_json(run_id, results_path)` — parsa il JSON nativo di freqtrade e inserisce righe in `backtest_trades`.

**Test:** 5–6 test su DB in-memory

---

## Step 22 — Report Generator

**Deliverable:** `src/backtesting/report.py` + `src/backtesting/run_report.py` (CLI)

### Output per run

```
backtest_reports/run_{ts}/
  summary.json                  ← BacktestSummaryReport (machine-readable)
  comparison_table.csv          ← scenari in riga, metriche in colonna (aggregato)
  comparison_table.html         ← stessa tabella stilizzata
  comparison_table_monthly.csv  ← breakdown mensile: scenario | month | trades | win_rate_pct | profit_pct | max_dd_pct
  per_scenario/{name}/
    trades.csv
    equity_curve.csv
    freqtrade_charts/           ← output di freqtrade plot-profit (HTML Plotly)
  parser_quality/
    signal_coverage.csv         ← % chain con dati completi per trader
    update_chain_stats.csv      ← avg update per chain, intent breakdown
```

### Metriche per scenario

| Metrica | Descrizione |
|---------|-------------|
| `total_trades` | Numero totale trade eseguiti |
| `win_rate_pct` | % trade in profitto |
| `total_profit_pct` | Profitto totale % sul capitale |
| `max_drawdown_pct` | Massimo drawdown % |
| `profit_factor` | gross_profit / gross_loss |
| `sharpe_ratio` | Risk-adjusted return |
| `avg_trade_duration_hours` | Durata media trade |
| `sl_moved_to_be_count` | Trade con SL spostato a BE |
| `chains_blocked_count` | Chain bloccate dai gate |

Ogni metrica viene calcolata anche con **granularità mensile** (`comparison_table_monthly.csv`), raggruppando le trade per `strftime('%Y-%m', open_date)`. Questo permette di identificare trend stagionali e periodi di drawdown.

### Comparison table (esempio)

| scenario | trades | win_rate | profit_pct | max_dd | profit_factor | sl_moved |
|----------|--------|----------|------------|--------|---------------|----------|
| follow_full_chain | 45 | 62.2% | +18.4% | -8.1% | 1.82 | 0 |
| signals_only | 45 | 55.6% | +11.2% | -12.3% | 1.41 | 0 |
| sl_to_be_after_tp2 | 45 | 64.4% | +19.1% | -6.2% | 2.01 | 28 |
| aggressive_averaging | 45 | 62.2% | +17.8% | -9.4% | 1.79 | 0 |

### freqtrade charts

Chiama `freqtrade plot-profit --export-filename {results_json}` → HTML Plotly con candlestick + marker entry/exit/TP/SL.

**Test:** 8–10 test

---

## Step 23 — Integration Test

**Deliverable:** `src/backtesting/tests/test_integration.py`

Test end-to-end con:
- DB fixture con 5 signal chain predefiniti
- OHLCVSource mock con candele pre-calcolate
- freqtrade subprocess mockato

Verifica:
1. Build chains → apply 2 scenari → scrivi sidecar
2. Directory run create correttamente
3. `BacktestSummaryReport` parsabile
4. Comparison table CSV con 2 righe scenario

---

## Struttura moduli finale

```
src/backtesting/
  __init__.py
  models.py              ← SignalChain, ChainedMessage, BacktestReadyChain
  chain_builder.py       ← SignalChainBuilder
  scenario.py            ← ScenarioConditions, ScenarioApplier, ScenarioLoader
  runner.py              ← BacktestRunner, BacktestRunResult
  storage.py             ← BacktestRunStore, BacktestTradeStore
  report.py              ← ReportGenerator, ScenarioMetrics, BacktestSummaryReport
  run_backtest.py        ← CLI entry point (include step download-data se dati mancanti)
  run_report.py          ← CLI report da run esistenti
  tests/
    conftest.py          ← fixtures condivise (DB in-memory, chain fixture)
    test_chain_builder.py
    test_scenario.py
    test_strategy_signal_bridge.py
    test_runner.py
    test_storage.py
    test_report.py
    test_integration.py

freqtrade/user_data/strategies/
  SignalBridgeStrategy.py           ← LIVE (non toccare)
  SignalBridgeBacktestStrategy.py   ← BACKTEST (nuovo, nessun import condiviso con live)

parser_test/scripts/
  import_history.py              ← GIA' ESISTE — scarica messaggi Telegram
  replay_parser.py               ← GIA' ESISTE — parsa raw_messages
  replay_operation_rules.py      ← NUOVO (Step 18b) — applica operation rules

config/
  backtest_scenarios.yaml

db/
  backtest.sqlite3               ← DB DEDICATO backtesting (gitignored)
  tele_signal_bot.sqlite3        ← DB LIVE (non toccato dal backtesting)

db/migrations/
  015_backtest_runs.sql
  016_backtest_trades.sql

backtest_reports/                ← gitignored, output root
```

---

## Dipendenze tra step

```
Step 16 (models + chain_builder)  ─┐
Step 17 (scenario + YAML)         ─┤── paralleli (nessuna dipendenza reciproca)
Step 21 (migrations + storage)    ─┘
         │
Step 18a (OHLCV download — ops)  ── operativo, nessun codice
Step 18b (replay_operation_rules) ── script nuovo, parallelo a 16/17/21
         │
Step 19 (BacktestStrategy)        ← dipende da 16, 17
         │
Step 20 (runner)                  ← dipende da 16, 17, 19, 21
         │
Step 22 (report)                  ← dipende da 20, 21
         │
Step 23 (integration test)       ← dipende da tutto
```

---

## Rischi principali

| Rischio | Impatto | Mitigazione |
|---------|---------|-------------|
| `resolved_target_ids` NULL per UPDATE storici (acquisiti prima di Fase 4) | MEDIO | Fallback su `reply_to_message_id`; log UPDATE non linkati nel run |
| `order_filled_callback` non chiamato in backtesting mode | MEDIO | TP ladder gestita via `custom_exit` con `partial_exit_ratio` — implementazione diversa dalla Strategy live |
| freqtrade version compatibility (IStrategy API) | MEDIO | Pin `freqtrade>=2024.1`; `INTERFACE_VERSION=3` |
| OHLCV non disponibile per alcuni simboli/periodi | BASSO | `chains_with_no_ohlcv_count` nel run; skip graceful |
| `freqtrade` command su Windows | BASSO | Rileva `sys.platform`, usa `python -m freqtrade` |
| Formato simboli DB vs freqtrade (`BTCUSDT` vs `BTC/USDT:USDT`) | BASSO | Normalizzazione interna in BacktestReadyChain |

---

## Note architetturali — Autonomia futura

### v1 (attuale): integrata nell'applicazione generale

Fase 7 v1 vive dentro il progetto TeleSignalBot. Usa il DB condiviso (`tele_signal_bot.sqlite3`) in modalità **read-only** e importa moduli condivisi:

| Modulo condiviso | Uso in Fase 7 |
|------------------|---------------|
| `src/storage/signals_query.py` | Query segnali e operational_signals |
| `src/storage/raw_messages.py` | Lettura raw messages per chain builder |
| `src/operation_rules/risk_calculator.py` | Ricalcolo sizing per `risk_pct_variant` |
| `src/execution/freqtrade_normalizer.py` | Normalizzazione simboli DB → freqtrade |

Queste sono le **uniche** dipendenze esterne del modulo `src/backtesting/`. Devono restare esplicite e minimali.

### v2 (futura): modulo autonomo/standalone

In futuro Fase 7 dovrà poter funzionare come sistema standalone con il proprio flusso completo:

```
1. Scarico dati raw dalla fonte (Telegram o export)
2. Applico il parser (esistente o specifico per il contesto)
3. Applico il processo di simulazione (scenario + backtest)
```

Il design v1 non blocca questa evoluzione grazie a:
- DB read-only (nessun accoppiamento bidirezionale)
- Modulo `src/backtesting/` separato con CLI propria
- `SignalChainBuilder` disaccoppiato dalla fonte dati (accetta query results, non dipende dal listener)

**Per la transizione a v2** servirà:
- Internalizzare le query SQL necessarie (o creare un adapter)
- Aggiungere un layer di import/parsing autonomo
- Nessun impatto sul design degli step 16–23

---

## Verifica end-to-end

```bash
# 1. Assicurati che Fase 4 sia completa
pytest src/ -q   # deve passare 427+ test

# 2. Lancia backtest su trader_3
python -m src.backtesting.run_backtest \
  --scenario-config config/backtest_scenarios.yaml \
  --db-path db/tele_signal_bot.sqlite3 \
  --trader trader_3

# 3. Verifica output
ls backtest_reports/run_*/
cat backtest_reports/run_*/comparison_table.csv

# 4. Apri i chart freqtrade
start backtest_reports/run_*/per_scenario/follow_full_chain/freqtrade_charts/*.html
```
