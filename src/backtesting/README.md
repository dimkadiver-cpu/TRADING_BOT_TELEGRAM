# src/backtesting — Guida all'uso

Modulo di backtesting storico (Sistema 2). Testa combinazioni di variabili di scenario sui segnali storici di ogni trader e produce report comparativi in percentuale.

> Stato verificato il 2026-04-06: il modulo base e presente, ma nel workspace corrente il loader scenari v2 non e ancora allineato del tutto con i test e il comando Windows del runner ha una regressione aperta. Finche `test_scenario_loader.py` non torna verde, usa `config/backtest_scenarios.yaml` come percorso piu affidabile.

---

## Prerequisiti

1. **DB operativo** con dati in `operational_signals` (prodotto dal parser).
2. **freqtrade installato** nel venv dedicato `.venv-freqtrade/` (Windows) o disponibile come `freqtrade` nel PATH (Linux/macOS).
3. **OHLCV data** raggiungibile da freqtrade (Bybit API o file locali).

---

## Comandi principali

> Tutti i comandi vanno eseguiti dalla root del progetto: `C:\TeleSignalBot\`

### Run completo (tutti i trader, tutti gli scenari)

```powershell
.venv\Scripts\python.exe -m src.backtesting.run_backtest `
  --scenario-config config/backtest_scenarios_v2.yaml `
  --db-path db/backtest.sqlite3 `
  --all-traders `
  --output backtest_reports/
```

### Run su un singolo trader

```powershell
.venv\Scripts\python.exe -m src.backtesting.run_backtest `
  --scenario-config config/backtest_scenarios_v2.yaml `
  --db-path db/backtest.sqlite3 `
  --trader trader_3 `
  --output backtest_reports/trader_3/
```

### Run con il config Fase 7 (backward compatible)

```powershell
.venv\Scripts\python.exe -m src.backtesting.run_backtest `
  --scenario-config config/backtest_scenarios.yaml `
  --db-path db/backtest.sqlite3 `
  --output backtest_reports/
```

### Rigenera solo i report (senza rieseguire freqtrade)

Utile se vuoi cambiare la formattazione o aggiungere metriche senza ripetere il run.

```powershell
.venv\Scripts\python.exe -m src.backtesting.run_report `
  --db-path db/backtest.sqlite3 `
  --output backtest_reports/latest/
```

```powershell
# Solo per run ID specifici
.venv\Scripts\python.exe -m src.backtesting.run_report `
  --db-path db/backtest.sqlite3 `
  --run-ids 1 3 5 `
  --output backtest_reports/custom/
```

---

## Configuratore scenari (v2)

Il file YAML supporta quattro modalità, combinabili nello stesso file.

### 1. Scenario esplicito

```yaml
scenarios:
  - name: conservative
    description: "Rischio 0.5%, BE dopo TP1"
    conditions:
      risk_pct: 0.5
      management:
        sl_to_be_after_tp: 1
```

### 2. Scenario con preset (extends + overrides)

```yaml
presets:
  base:
    risk_pct: 1.0
    gate_mode: strict
    entry:
      selection: all
      price_mode: exact

scenarios:
  - name: conservative
    extends: base
    overrides:
      risk_pct: 0.5
      management:
        sl_to_be_after_tp: 1
```

### 3. Sweep (una variabile alla volta)

Genera uno scenario per ogni valore. Nome automatico: `{name}_{valore}`.

```yaml
sweep:
  - name: risk_sweep
    base_conditions:
      risk_pct: 1.0
      entry:
        selection: all
        price_mode: exact
    sweep_variable: risk_pct
    sweep_values: [0.5, 1.0, 2.0]
    # → risk_sweep_0.5, risk_sweep_1.0, risk_sweep_2.0
```

Variabili nested supportate con dot-notation: `entry.selection`, `entry.price_mode`, `tp.count`, ecc.

### 4. Matrix (prodotto cartesiano)

Genera uno scenario per ogni combinazione. Nome automatico: `{name}_{v1}_{v2}_...`.

```yaml
matrix:
  - name: entry_x_tp
    variables:
      entry.price_mode: [exact, average]
      tp.count: [1, 2, null]
    # 2 × 3 = 6 scenari:
    # entry_x_tp_exact_1, entry_x_tp_exact_2, entry_x_tp_exact_None
    # entry_x_tp_average_1, entry_x_tp_average_2, entry_x_tp_average_None
```

> Se il totale degli scenari supera 50, viene emesso un warning. Non c'è un limite hard.

Nota operativa: lo schema v2 e il suo loader sono il target documentale corrente, ma nel workspace attuale il supporto non e ancora affidabile quanto il config Fase 7. Prima di usarlo in una sessione lunga, verifica il risultato di `src/backtesting/tests/test_scenario_loader.py`.

---

## Variabili di scenario (ScenarioConditions)

| Campo | Tipo | Default | Descrizione |
|---|---|---|---|
| `risk_pct` | float | `1.0` | Rischio per trade come % del capitale |
| `gate_mode` | `strict` \| `warn` | `strict` | `strict` esclude chain bloccate; `warn` le include con flag |
| `chain_follow.U_MOVE_STOP` | bool | `false` | Applica aggiornamenti SL del trader |
| `chain_follow.U_CLOSE_FULL` | bool | `false` | Applica chiusure totali |
| `chain_follow.U_CLOSE_PARTIAL` | bool | `false` | Applica chiusure parziali |
| `chain_follow.U_CANCEL_PENDING` | bool | `false` | Applica cancellazione pending |
| `chain_follow.U_REENTER` | bool | `false` | Applica re-entry |
| `chain_follow.U_ADD_ENTRY` | bool | `false` | Applica aggiunta entry |
| `chain_follow.U_UPDATE_TAKE_PROFITS` | bool | `false` | Applica modifica TP |
| `entry.selection` | `all` \| `first_only` \| `last_only` | `all` | Quali entry usare |
| `entry.price_mode` | `exact` \| `average` \| `extreme_min` \| `extreme_max` | `exact` | Come calcolare il prezzo di ingresso |
| `entry.split` | `[0.7, 0.3]` \| `null` | `null` | Allocazione % per entry (somma = 1.0); `null` = split uguale |
| `tp.count` | int \| `null` | `null` | Numero massimo di TP da usare; `null` = tutti |
| `tp.close_scheme` | `[0.5, 0.5]` \| `null` | `null` | % da chiudere a ogni TP (somma = 1.0); `null` = tutto all'ultimo |
| `management.sl_to_be_after_tp` | int \| `null` | `null` | Sposta SL a breakeven dopo TPn; `null` = mai |

Con tutti i `chain_follow.*: false` (default) lo scenario equivale a *signals only*: apre e chiude seguendo solo i segnali NEW, ignorando tutti gli UPDATE del trader.

---

## Output generato

Il processo è in **due fasi separate**:

### Fase 1 — `run_backtest.py` (esegue freqtrade)

Per ogni scenario crea una directory dedicata:

```
# senza --trader
{output}/run_{scenario_name}_{timestamp}/
  freqtrade_config.json
  signal_chains.json
  freqtrade_results.json

# con --trader trader_3
{output}/trader_3/run_{scenario_name}_{timestamp}/
  freqtrade_config.json
  signal_chains.json
  freqtrade_results.json
```

I risultati vengono anche salvati nel DB (`db/backtest.sqlite3`).

### Fase 2 — `run_report.py` (genera i report comparativi)

Legge i run salvati nel DB e scrive in `--output`:

```
{output}/
  comparison_table.csv          # confronto scenari (metriche %)
  comparison_table.html
  comparison_table_monthly.csv  # breakdown mensile per scenario
  summary.json
  per_scenario/{nome}/
    trades.csv
    equity_curve.csv
  parser_quality/
    signal_coverage.csv         # copertura catene per trader
    update_chain_stats.csv      # statistiche UPDATE per trader
```

Flusso tipico:

```powershell
# 1. Esegui il backtest (dalla root C:\TeleSignalBot\)
.venv\Scripts\python.exe -m src.backtesting.run_backtest `
  --scenario-config config/backtest_scenarios_v2.yaml `
  --db-path db/backtest.sqlite3 `
  --trader trader_3 `
  --output backtest_reports/

# 2. Genera i report comparativi
.venv\Scripts\python.exe -m src.backtesting.run_report `
  --db-path db/backtest.sqlite3 `
  --output backtest_reports/report/
```

### Metriche nel report (tutte in %)

| Colonna | Descrizione |
|---|---|
| `trades` | Numero trade |
| `win_rate` | % trade in profitto |
| `profit_pct` | Rendimento totale % |
| `avg_trade_pct` | Media % per trade (metrica primaria) |
| `max_dd` | Max drawdown % |
| `profit_factor` | Gross profit / gross loss |

---

## Test

```powershell
.venv\Scripts\python.exe -m pytest src/backtesting/tests/ -v
```

La suite include copertura per chain builder, scenario loader, strategy bridge, runner, storage, report e integrazione. Nel workspace corrente il blocco scenario loader e il comando Windows del runner hanno regressioni aperte.
