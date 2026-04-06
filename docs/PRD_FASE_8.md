# PRD Fase 8 — Scenario Engine v2: Ottimizzazione Backtest

**Stato:** DESIGN — 2026-03-30
**Dipendenze:** Fase 7 completa (Step 16–23)

Nota di verifica 2026-04-06: lo schema v2 e il loader avanzato sono il target documentale corrente, ma nel workspace attuale non sono ancora affidabili quanto il config Fase 7. Prima di usarli come base operativa, verificare `src/backtesting/tests/test_scenario_loader.py`.

---

## Context

Fase 7 ha implementato il sistema di backtest base con scenari fissi definiti manualmente in YAML.
Fase 8 estende il sistema in tre direzioni:

1. **Variabili di scenario più granulari** — ogni dimensione del trade è configurabile indipendentemente
2. **Configuratore scenari avanzato** — matrice di variabili, sweep controllato, preset + overrides
3. **Output in percentuale** — report basati su % del capitale, non USDT assoluti

**Obiettivo:** trovare la combinazione ottimale di variabili testando sistematicamente lo spazio delle configurazioni possibili sui segnali storici di ogni trader.

---

## Visione utente

```
Dati storici (messaggi + OHLCV)
      ↓
Parser → segnali + catena (solo ACTION update)
      ↓
Per ogni trader × Per ogni scenario generato
      ↓
Report comparativo (%, non USDT)
```

Ogni scenario è una combinazione di:

```
scenario
  ├── chain_follow      → quali UPDATE del trader applicare (granulare per intent)
  └── personal_rules    → regole di esecuzione indipendenti dalla catena
```

---

## Gap rispetto a Fase 7

| Feature | Fase 7 | Fase 8 |
|---|---|---|
| Filtro UPDATE per intent | tutto o niente | per singolo intent |
| Selezione entry | peso E1 (`vary_entry_pct`) | selezione + prezzo + split flessibile |
| Gestione TP | tutti i TP fissi | count + schema chiusura |
| SL management | solo BE dopo TP2 | BE dopo TPn configurabile |
| Configuratore scenari | YAML manuale | matrice + sweep + preset |
| Output report | USDT assoluti | % del capitale |

---

## Step 24 — ScenarioConditions v2

**Deliverable:** aggiornamento `src/backtesting/scenario.py`

### Nuovo modello ScenarioConditions

```python
class ChainFollowConfig(BaseModel):
    """Quali UPDATE del trader applicare. Default: nessuno (signals_only)."""
    U_MOVE_STOP: bool = False
    U_CLOSE_FULL: bool = False
    U_CLOSE_PARTIAL: bool = False
    U_CANCEL_PENDING: bool = False
    U_REENTER: bool = False
    U_ADD_ENTRY: bool = False
    U_UPDATE_TAKE_PROFITS: bool = False
    # U_TP_HIT e U_SL_HIT sono CONTEXT — non applicabili come azioni


class EntryConfig(BaseModel):
    """Strategia di ingresso."""
    selection: Literal["all", "first_only", "last_only"] = "all"
    price_mode: Literal["exact", "average", "extreme_min", "extreme_max"] = "exact"
    split: list[float] | None = None
    # None = split uguale tra le entry selezionate
    # [0.7, 0.3] = 70% E1, 30% E2
    # [1.0] con selection=first_only = tutto sulla prima


class TpConfig(BaseModel):
    """Gestione take profit."""
    count: int | None = None          # None = tutti i TP indicati
    close_scheme: list[float] | None = None
    # None = chiude tutto all'ultimo TP
    # [0.5, 0.5] = 50% a TP1, 50% a TP2
    # [0.33, 0.33, 0.34] = distribuzione uniforme su 3 TP


class ManagementConfig(BaseModel):
    """Regole di gestione posizione personali."""
    sl_to_be_after_tp: int | None = None
    # None = mai, 1 = dopo TP1, 2 = dopo TP2, ecc.


class ScenarioConditions(BaseModel):
    """Set completo di variabili per uno scenario."""

    chain_follow: ChainFollowConfig = ChainFollowConfig()
    """Quali UPDATE del trader seguire."""

    entry: EntryConfig = EntryConfig()
    """Come gestire entry prezzi e split."""

    tp: TpConfig = TpConfig()
    """Come gestire i take profit."""

    management: ManagementConfig = ManagementConfig()
    """Regole personali di gestione posizione."""

    risk_pct: float = 1.0
    """Rischio per trade come % del capitale."""

    gate_mode: Literal["strict", "warn"] = "strict"
    """strict = esclude chain bloccate, warn = include con flag."""
```

### Compatibilità con Fase 7

I vecchi campi (`follow_full_chain`, `signals_only`, `sl_to_be_after_tp2`, `vary_entry_pct`, `risk_pct_variant`) vengono mappati al nuovo modello tramite un `model_validator` di migrazione. Nessun YAML esistente si rompe.

### ScenarioApplier aggiornato

`ScenarioApplier.apply(chain, scenario)` → `BacktestReadyChain`

| Fase | Operazione |
|---|---|
| 1. Filtra UPDATE | Includi solo UPDATE il cui intent principale è in `chain_follow` |
| 2. Seleziona entry | Applica `entry.selection` alla lista `entry_prices` |
| 3. Calcola prezzo | Applica `entry.price_mode` (exact / average / min / max) |
| 4. Calcola split | Normalizza `entry.split` sul numero di entry selezionate |
| 5. Limita TP | Tronca `tp_prices` a `tp.count` se definito |
| 6. Calcola close scheme | Normalizza `tp.close_scheme` sul numero di TP effettivi |
| 7. SL management | Imposta `effective_sl_to_be_tp = management.sl_to_be_after_tp` |
| 8. Risk sizing | Ricalcola `position_size_usdt` da `risk_pct` × `capital_base` |

**Test:** aggiorna `src/backtesting/tests/test_scenario.py` — 15–20 test

---

## Step 25 — Configuratore Scenari v2

**Deliverable:** nuovo `config/backtest_scenarios_v2.yaml` + aggiornamento `ScenarioLoader`

### Tre modalità di configurazione

#### Modalità 1: Scenario esplicito (compatibile Fase 7)

```yaml
scenarios:
  - name: follow_close_only
    description: "Segue solo chiusura totale + annulla pending, BE dopo TP1"
    conditions:
      chain_follow:
        U_CLOSE_FULL: true
        U_CANCEL_PENDING: true
      management:
        sl_to_be_after_tp: 1
      risk_pct: 1.0
```

#### Modalità 2: Sweep (una variabile alla volta, le altre fisse)

```yaml
sweep:
  - name: risk_sweep
    description: "Varia solo il rischio per trade"
    base_conditions:
      entry:
        selection: all
        price_mode: exact
      risk_pct: 1.0                 # valore base
    sweep_variable: risk_pct
    sweep_values: [0.5, 1.0, 2.0]  # genera 3 scenari
```

Genera automaticamente scenari: `risk_sweep_0.5`, `risk_sweep_1.0`, `risk_sweep_2.0`

#### Modalità 3: Matrice (prodotto cartesiano di variabili)

```yaml
matrix:
  - name: entry_x_tp
    description: "Incrocia strategia entry con schema TP"
    variables:
      entry.selection: [first_only, all]
      entry.price_mode: [exact, average]
      tp.count: [1, 2, null]
    # genera 2 × 2 × 3 = 12 scenari
```

#### Preset + override

```yaml
presets:
  base:
    risk_pct: 1.0
    entry:
      selection: all
      price_mode: exact
    management:
      sl_to_be_after_tp: null

scenarios:
  - name: conservative
    extends: base
    overrides:
      risk_pct: 0.5
      management:
        sl_to_be_after_tp: 1
```

### ScenarioLoader v2

`ScenarioLoader.load(path)` → `ScenarioConfig`

1. Risolve tutti i `presets` e applica `overrides`
2. Espande ogni `sweep` → lista scenari espliciti
3. Espande ogni `matrix` → prodotto cartesiano → lista scenari
4. Valida ogni scenario risultante via `ScenarioConditions`
5. Restituisce `ScenarioConfig` con lista piatta di `BacktestScenario`

**Nota:** il prodotto cartesiano può esplodere. `ScenarioLoader` emette un warning se il totale supera 50 scenari. Nessun limite hard — è responsabilità dell'utente.

**Test:** `src/backtesting/tests/test_scenario_loader.py` — 10–12 test

---

## Step 26 — Chain Builder: filtro ACTION intents

**Deliverable:** aggiornamento `src/backtesting/chain_builder.py`

### Problema attuale

`SignalChainBuilder` include tutti gli UPDATE senza distinzione. Gli intent CONTEXT (`U_TP_HIT`, `U_SL_HIT`) non sono azioni — non hanno prezzi da eseguire, servono solo come trigger per regole personali (es. BE dopo TP1).

### Modifica

`SignalChain.updates` continua a contenere **tutti** gli UPDATE (inclusi CONTEXT) per permettere a `ScenarioApplier` di rilevare i trigger (es. U_TP_HIT).

`ScenarioApplier.apply()` filtra gli UPDATE da applicare come azioni usando `chain_follow`:
- un UPDATE viene "applicato" solo se il suo intent principale è in `chain_follow` e vale `True`
- gli UPDATE CONTEXT (`U_TP_HIT`, `U_SL_HIT`) restano nella chain ma non vengono mai applicati come azioni — vengono letti solo per rilevare trigger (es. `sl_to_be_after_tp`)

Questo non richiede modifiche al chain builder — solo a `ScenarioApplier`.

---

## Step 27 — Report v2: output in percentuale

**Deliverable:** aggiornamento `src/backtesting/report.py`

### Metriche in output

Tutte le metriche monetarie diventano percentuali. `capital_base_usdt` è solo un parametro di scala interno — non appare nei report.

| Metrica | Tipo | Descrizione |
|---|---|---|
| `total_trades` | int | Numero trade |
| `win_rate_pct` | float | % trade in profitto |
| `total_profit_pct` | float | Rendimento totale % |
| `avg_profit_per_trade_pct` | float | Media % per trade |
| `max_drawdown_pct` | float | Max drawdown % |
| `profit_factor` | float | gross_profit / gross_loss |
| `sharpe_ratio` | float | Risk-adjusted return |
| `avg_trade_duration_hours` | float | Durata media |
| `sl_moved_to_be_count` | int | Trade con BE attivato |
| `chains_blocked_count` | int | Chain bloccate |

**Rimosso:** nessuna metrica in USDT assoluti nel report principale.
**Aggiunto:** `avg_profit_per_trade_pct` come metrica primaria per confronto scenari.

### Comparison table aggiornata

```
scenario | trades | win_rate | profit_pct | avg_trade_pct | max_dd | profit_factor
```

### Breakdown mensile

Invariato nella struttura, aggiornato nelle metriche (solo %).

---

## Step 28 — Integrazione per-trader

**Deliverable:** aggiornamento `src/backtesting/run_backtest.py`

### Flusso per-trader

```bash
# Un run per trader
python -m src.backtesting.run_backtest \
  --scenario-config config/backtest_scenarios_v2.yaml \
  --db-path db/backtest.sqlite3 \
  --trader trader_3 \
  --output backtest_reports/trader_3/
```

### Regole

- `BacktestSettings.capital_base_usdt` rimane ma è usato solo per calcolo interno sizing
- Il report non mostra USDT — mostra solo %
- Ogni trader ha la sua directory di output: `backtest_reports/{trader_id}/run_{ts}/`
- La comparison table finale confronta scenari dello stesso trader

### Multi-trader in sequenza (helper CLI)

```bash
# Lancia un run per ogni trader trovato nel DB
python -m src.backtesting.run_backtest \
  --scenario-config config/backtest_scenarios_v2.yaml \
  --db-path db/backtest.sqlite3 \
  --all-traders \
  --output backtest_reports/
```

`--all-traders` enumera i `trader_id` distinti in `operational_signals` e lancia un run separato per ciascuno.

---

## Struttura file modificati/nuovi

```
src/backtesting/
  scenario.py              ← AGGIORNATO (ScenarioConditions v2, ScenarioApplier v2)
  report.py                ← AGGIORNATO (metriche in %)
  run_backtest.py          ← AGGIORNATO (--all-traders)
  tests/
    test_scenario.py       ← AGGIORNATO
    test_scenario_loader.py ← NUOVO
    test_report.py         ← AGGIORNATO

config/
  backtest_scenarios_v2.yaml  ← NUOVO (configuratore avanzato)
```

---

## Dipendenze tra step

```
Step 24 (ScenarioConditions v2)
      ↓
Step 25 (Configuratore scenari v2)    ← dipende da 24
Step 26 (Chain filter)                ← dipende da 24
Step 27 (Report %)                    ← parallelo a 24/25/26
      ↓
Step 28 (Integrazione per-trader)     ← dipende da 24, 25, 26, 27
```

---

## Metodo di analisi progressivo

```
Fase C — Sweep singola variabile
  → capisce quale variabile impatta di più (tenendo le altre fisse)

Fase B — Gruppi di variabili rilevanti
  → combina le variabili che hanno dimostrato impatto

Fase A — Matrice completa
  → ottimizzazione finale sullo spazio ridotto
```

---

## Rischi

| Rischio | Impatto | Mitigazione |
|---|---|---|
| Matrice genera troppi scenari | MEDIO | Warning > 50 scenari; approccio progressivo C→B→A |
| `split` non normalizzato (somma ≠ 1) | BASSO | Validazione in `EntryConfig` via `model_validator` |
| `close_scheme` non normalizzato | BASSO | Validazione in `TpConfig` via `model_validator` |
| Compatibilità backward YAML Fase 7 | BASSO | `model_validator` di migrazione in `ScenarioConditions` |
| `price_mode: average` con entry singola | BASSO | Fallback a `exact` se n_entries == 1 |
