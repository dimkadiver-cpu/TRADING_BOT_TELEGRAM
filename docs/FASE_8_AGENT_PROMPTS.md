# Fase 8 — Agent Prompts

Prompts per agente da eseguire in sequenza.
Ogni prompt è autonomo — include tutto il contesto necessario.
Leggi PRD_FASE_8.md prima di iniziare qualsiasi step.

---

## Step 24 — ScenarioConditions v2

```
Leggi questi file prima di scrivere codice:
- docs/PRD_FASE_8.md (Step 24)
- src/backtesting/scenario.py (stato attuale)
- src/backtesting/models.py
- src/backtesting/tests/test_scenario.py

Obiettivo: aggiornare src/backtesting/scenario.py con il nuovo modello ScenarioConditions v2.

Cosa fare:

1. Aggiungi i modelli Pydantic:
   - ChainFollowConfig: flag booleani per ogni ACTION intent (U_MOVE_STOP, U_CLOSE_FULL,
     U_CLOSE_PARTIAL, U_CANCEL_PENDING, U_REENTER, U_ADD_ENTRY, U_UPDATE_TAKE_PROFITS)
   - EntryConfig: selection (all/first_only/last_only), price_mode (exact/average/extreme_min/extreme_max),
     split (list[float] | None)
   - TpConfig: count (int | None), close_scheme (list[float] | None)
   - ManagementConfig: sl_to_be_after_tp (int | None)
   - ScenarioConditions v2: chain_follow, entry, tp, management, risk_pct, gate_mode

2. Aggiungi model_validator in EntryConfig:
   - se split è fornito, la somma deve essere ~1.0 (tolleranza 0.001)
   - se split ha lunghezza diversa dalle entry selezionate, viene normalizzato o errore

3. Aggiungi model_validator in TpConfig:
   - se close_scheme è fornito, la somma deve essere ~1.0 (tolleranza 0.001)

4. Aggiungi model_validator di migrazione in ScenarioConditions:
   - mappa i vecchi campi (follow_full_chain, signals_only, sl_to_be_after_tp2,
     vary_entry_pct, risk_pct_variant, gate_mode_variant) al nuovo modello
   - questo garantisce che i YAML di Fase 7 continuino a funzionare

5. Aggiorna ScenarioApplier.apply() per usare il nuovo modello:
   - Fase 1: filtra applied_updates usando chain_follow (includi UPDATE solo se
     il loro intent principale è True in chain_follow)
   - Fase 2: applica entry.selection alla lista entry_prices
   - Fase 3: applica entry.price_mode (exact=invariato, average=media aritmetica,
     extreme_min=min(entry_prices), extreme_max=max(entry_prices))
   - Fase 4: normalizza entry.split sul numero di entry selezionate
     (None = split uguale, es. 3 entry → [0.333, 0.333, 0.334])
   - Fase 5: tronca effective_tp_prices a tp.count se non None
   - Fase 6: salva tp.close_scheme in BacktestReadyChain (nuovo campo)
   - Fase 7: imposta effective_sl_to_be_tp = management.sl_to_be_after_tp in BacktestReadyChain
   - Fase 8: ricalcola position_size_usdt da risk_pct × capital_base_usdt

6. Aggiorna BacktestReadyChain in models.py:
   - aggiungi: tp_close_scheme (list[float] | None)
   - aggiungi: sl_to_be_after_tp (int | None)
   - rinomina: effective_risk_pct → risk_pct (ora sempre presente, default 1.0)

7. Aggiorna src/backtesting/tests/test_scenario.py:
   - mantieni tutti i test esistenti (adattali ai nuovi nomi se necessario)
   - aggiungi test per ogni nuovo comportamento:
     - entry.selection: first_only, last_only, all
     - entry.price_mode: average, extreme_min, extreme_max
     - entry.split: normalizzazione, errore se somma != 1
     - tp.count: troncamento lista TP
     - tp.close_scheme: validazione somma
     - management.sl_to_be_after_tp: 1, 2, None
     - chain_follow: solo U_CLOSE_FULL=True → altri UPDATE esclusi
     - migrazione backward: vecchio YAML Fase 7 → nuovo modello

Vincoli:
- Non toccare chain_builder.py
- Non toccare runner.py
- Non rompere i test esistenti
- from __future__ import annotations in ogni file
- Pydantic v2
```

---

## Step 25 — Configuratore Scenari v2

```
Leggi questi file prima di scrivere codice:
- docs/PRD_FASE_8.md (Step 25)
- src/backtesting/scenario.py (dopo Step 24)
- config/backtest_scenarios.yaml (formato attuale)

Obiettivo: implementare il configuratore scenari avanzato con sweep, matrice e preset.

Cosa fare:

1. Aggiorna ScenarioLoader in src/backtesting/scenario.py:

   load(path) deve supportare quattro modalità nel YAML:

   a) scenarios: lista di scenari espliciti (esiste già — mantieni compatibilità)

   b) sweep: lista di sweep config, ognuna con:
      - name: prefisso nome scenario
      - description: descrizione base
      - base_conditions: dict con tutte le condizioni fisse
      - sweep_variable: path puntato della variabile (es. "risk_pct", "entry.selection")
      - sweep_values: lista valori
      Genera scenari con nome f"{name}_{value}"

   c) matrix: lista di matrix config, ognuna con:
      - name: prefisso
      - description: descrizione base
      - variables: dict {path_variabile: [lista_valori]}
      Genera prodotto cartesiano. Nome scenario: f"{name}_{'_'.join(str(v) for v in combo)}"
      Emetti WARNING nel log se il totale scenari generati supera 50.

   d) presets: dict nome→condizioni base
      Ogni scenario in scenarios può avere "extends: nome_preset" e "overrides: dict"
      Override viene applicato con deep merge (non sostituisce l'intero sotto-oggetto)

2. Metodo interno _resolve_path(conditions_dict, dot_path, value):
   - dot_path come "entry.selection" → imposta conditions["entry"]["selection"] = value
   - supporta un solo livello di annidamento (non serve ricorsione profonda)

3. Metodo interno _deep_merge(base, override) → dict:
   - merge ricorsivo: i valori scalari di override sovrascrivono base
   - i dict vengono mergiati ricorsivamente

4. Dopo espansione, ogni scenario viene validato via ScenarioConditions.model_validate()
   Se la validazione fallisce, lancia ValueError con nome scenario e dettaglio errore.

5. Crea config/backtest_scenarios_v2.yaml con esempi di tutte le modalità:
   - 2 scenari espliciti (con preset)
   - 1 sweep su risk_pct: [0.5, 1.0, 2.0]
   - 1 sweep su entry.selection: [first_only, all]
   - 1 matrice entry.price_mode × tp.count: [exact, average] × [1, 2, null]
   - preset "base" riutilizzato dagli scenari espliciti

6. Crea src/backtesting/tests/test_scenario_loader.py:
   - test caricamento scenario esplicito
   - test preset + override (verifica deep merge)
   - test sweep genera N scenari con nomi corretti
   - test matrice genera prodotto cartesiano
   - test warning > 50 scenari
   - test errore se sweep_variable non esiste in ScenarioConditions
   - test errore se scenario risultante non valido

Vincoli:
- Non modificare ScenarioConditions o ScenarioApplier (già fatto in Step 24)
- Il vecchio config/backtest_scenarios.yaml deve continuare a caricarsi senza errori
```

---

## Step 26 — Chain Builder: separazione ACTION vs CONTEXT

```
Leggi questi file prima di scrivere codice:
- docs/PRD_FASE_8.md (Step 26)
- src/backtesting/chain_builder.py
- src/backtesting/scenario.py (dopo Step 24)
- CLAUDE.md sezione "Intents UPDATE"

Obiettivo: rendere esplicita la distinzione ACTION vs CONTEXT negli UPDATE della chain.

Contesto:
- Intent CONTEXT: U_TP_HIT, U_SL_HIT — notifiche informative, non azioni da eseguire
- Intent ACTION: U_MOVE_STOP, U_CLOSE_FULL, U_CLOSE_PARTIAL, U_CANCEL_PENDING,
  U_REENTER, U_ADD_ENTRY, U_MODIFY_ENTRY, U_UPDATE_TAKE_PROFITS

Il chain_builder NON deve cambiare — continua a includere tutti gli UPDATE in SignalChain.updates.
La separazione avviene in ScenarioApplier (già implementata in Step 24 via chain_follow).

Cosa fare:

1. Aggiungi una costante in src/backtesting/scenario.py:

   ACTION_INTENTS = frozenset({
       "U_MOVE_STOP", "U_CLOSE_FULL", "U_CLOSE_PARTIAL",
       "U_CANCEL_PENDING", "U_REENTER", "U_ADD_ENTRY",
       "U_MODIFY_ENTRY", "U_UPDATE_TAKE_PROFITS",
   })

   CONTEXT_INTENTS = frozenset({"U_TP_HIT", "U_SL_HIT"})

2. Aggiungi metodo helper in ScenarioApplier:
   _get_primary_intent(update: ChainedMessage) → str | None
   - restituisce il primo intent ACTION trovato in update.intents
   - se solo CONTEXT intents → restituisce None

3. Verifica che ScenarioApplier.apply() (Step 24) usi _get_primary_intent
   per il filtraggio chain_follow. Gli UPDATE con solo CONTEXT intents
   non vengono mai inclusi in applied_updates (ma rimangono in chain.updates
   per il rilevamento trigger).

4. Aggiungi test in test_scenario.py:
   - UPDATE con solo U_TP_HIT → non incluso in applied_updates anche se chain_follow non è vuoto
   - UPDATE con U_CLOSE_FULL + U_TP_HIT → incluso se U_CLOSE_FULL=True in chain_follow
   - UPDATE con U_MOVE_STOP → incluso solo se U_MOVE_STOP=True in chain_follow
   - trigger sl_to_be_after_tp rileva U_TP_HIT in chain.updates anche se non è in applied_updates

Vincoli:
- Non modificare chain_builder.py
- Non modificare models.py (solo scenario.py)
```

---

## Step 27 — Report v2: metriche in percentuale

```
Leggi questi file prima di scrivere codice:
- docs/PRD_FASE_8.md (Step 27)
- src/backtesting/report.py
- src/backtesting/storage.py
- src/backtesting/tests/test_report.py

Obiettivo: aggiornare il report generator per produrre output in % invece di USDT.

Cosa fare:

1. Aggiorna ScenarioMetrics in report.py:
   Rimuovi qualsiasi metrica in USDT assoluti.
   Struttura finale:
   - total_trades: int
   - win_rate_pct: float
   - total_profit_pct: float
   - avg_profit_per_trade_pct: float   ← NUOVO
   - max_drawdown_pct: float
   - profit_factor: float
   - sharpe_ratio: float
   - avg_trade_duration_hours: float
   - sl_moved_to_be_count: int
   - chains_blocked_count: int

2. Aggiorna ReportGenerator._compute_metrics():
   - total_profit_pct: somma di profit_pct di tutti i trade
   - avg_profit_per_trade_pct: media di profit_pct
   - max_drawdown_pct: calcola su equity curve % (non USDT)
   - profit_factor: gross_profit_pct / abs(gross_loss_pct)
   - sharpe_ratio: calcola su rendimenti % per trade

3. Aggiorna comparison_table.csv:
   Colonne: scenario | trades | win_rate | profit_pct | avg_trade_pct | max_dd | profit_factor
   Nessuna colonna in USDT.

4. Aggiorna comparison_table_monthly.csv:
   Colonne: scenario | month | trades | win_rate_pct | profit_pct | max_dd_pct

5. Aggiorna equity_curve.csv per scenario:
   Colonne: date | cumulative_profit_pct (non USDT)

6. Aggiorna src/backtesting/tests/test_report.py:
   - verifica che nessuna metrica USDT appaia nell'output
   - verifica avg_profit_per_trade_pct = total_profit_pct / total_trades
   - verifica equity curve in %
   - verifica comparison table senza colonne USDT

Vincoli:
- capital_base_usdt rimane in BacktestSettings ma non appare nei report
- Non modificare runner.py, storage.py, scenario.py
```

---

## Step 28 — Integrazione per-trader + CLI --all-traders

```
Leggi questi file prima di scrivere codice:
- docs/PRD_FASE_8.md (Step 28)
- src/backtesting/run_backtest.py
- src/backtesting/runner.py

Obiettivo: aggiungere supporto per run per-trader con output separato e flag --all-traders.

Cosa fare:

1. Aggiorna src/backtesting/run_backtest.py:
   - aggiungi argomento --all-traders (store_true)
   - se --all-traders: enumera trader_id distinti da operational_signals nel DB
     e lancia un BacktestRunner.run() separato per ognuno
   - output dir: backtest_reports/{trader_id}/run_{scenario}_{ts}/
   - se --trader è specificato insieme a --all-traders: errore chiaro

2. Aggiorna BacktestRunner.run() in runner.py:
   - accetta trader_id opzionale (già esiste come trader_filter in BacktestSettings)
   - output_dir include trader_id come subfolder: {output_base}/{trader_id}/

3. Helper in run_backtest.py:
   _list_traders(db_path) → list[str]
   Query: SELECT DISTINCT trader_id FROM operational_signals WHERE trader_id IS NOT NULL

4. Stampa riepilogo finale dopo --all-traders:
   Trader | Scenari | Trade totali | Best scenario (profit_pct)
   (legge i summary.json prodotti da ogni run)

5. Aggiorna src/backtesting/tests/test_runner.py:
   - test --all-traders con 2 trader mockati
   - verifica output dir separata per trader
   - test _list_traders con DB in-memory

Vincoli:
- Non modificare scenario.py, report.py, chain_builder.py
- capital_base_usdt fisso a 1000.0 per default — non è una variabile di ottimizzazione
- Ogni run per trader è completamente indipendente (nessun capitale condiviso)
```

---

## Verifica finale (dopo tutti gli step)

```
Leggi questi file prima di verificare:
- docs/PRD_FASE_8.md
- config/backtest_scenarios_v2.yaml
- src/backtesting/scenario.py
- src/backtesting/report.py
- src/backtesting/run_backtest.py

Obiettivo: verifica che l'implementazione Fase 8 sia completa e coerente.

Cosa fare:

1. Esegui tutti i test:
   pytest src/backtesting/tests/ -v
   Devono passare tutti. Nota eventuali failure.

2. Verifica backward compatibility:
   - Carica config/backtest_scenarios.yaml (Fase 7) con ScenarioLoader
   - Deve funzionare senza errori
   - Verifica che i 6 scenari originali producano BacktestReadyChain valide

3. Verifica il nuovo configuratore:
   - Carica config/backtest_scenarios_v2.yaml
   - Conta il numero di scenari generati
   - Verifica che sweep e matrix abbiano i nomi attesi

4. Verifica i report:
   - Controlla che ScenarioMetrics non abbia campi USDT
   - Controlla che comparison_table.csv non abbia colonne USDT

5. Segnala qualsiasi gap rispetto a PRD_FASE_8.md:
   - step non implementati
   - test mancanti
   - comportamenti non coperti

Non modificare nulla — solo verifica e riporta.
```
