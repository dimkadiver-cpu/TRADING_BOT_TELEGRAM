# Audit modulo `operation_rules` e config associati

Data audit: 2026-03-27
Ambito:
- `src/operation_rules/engine.py`
- `src/operation_rules/loader.py`
- `src/operation_rules/risk_calculator.py`
- `config/operation_rules.yaml`
- `config/trader_rules/trader_3.yaml`

## Executive summary

- **Stato generale**: buono, test suite del modulo verde (`54 passed`).
- **Rischi principali**:
  1. **HIGH** — `risk_hint` viene applicato dopo i gate di rischio/cap senza rieseguire i controlli.
  2. **MEDIUM** — conteggio posizioni aperte su stesso simbolo include anche segnali bloccati.
  3. **MEDIUM** — validazione config incompleta su campi critici (`risk_mode`, `gate_mode`).
  4. **LOW** — fallback silenziosi su errori DB possono nascondere problemi operativi.

## Evidenze tecniche

### 1) HIGH — Bypass dei cap quando `use_trader_risk_hint=true`

Nel flow di `OperationRulesEngine.apply()` i gate `per_signal`, `trader_cap` e `global_cap` sono calcolati usando `new_risk_pct` derivato dalla config base.
Successivamente, se `use_trader_risk_hint` è attivo, il codice sovrascrive `effective_risk_pct`, ricalcola budget/size, ma **non riesegue i gate**.

**Impatto**: un `risk_hint` elevato può produrre size/rischio superiori ai limiti hard senza blocco del segnale.

**Raccomandazione**:
- Se `risk_hint` è applicato, ricalcolare `new_risk_pct` e rieseguire gate 6/7/8 prima del return finale.
- In alternativa, applicare clamp al massimo consentito e loggare warning esplicito.

### 2) MEDIUM — `max_concurrent_same_symbol` conta anche segnali bloccati

`count_open_same_symbol()` legge solo la tabella `signals` filtrando `status NOT IN ('CLOSED','CANCELLED')`.
Non viene filtrato `operational_signals.is_blocked`.

**Impatto**: segnali bloccati ma non chiusi possono saturare il contatore e bloccare ingressi validi successivi.

**Raccomandazione**:
- Allineare la query a `sum_trader_exposure/sum_global_exposure` con join su `operational_signals` e filtro `is_blocked = 0`.

### 3) MEDIUM — Validazione config incompleta

In `load_effective_rules()`:
- `operation_rules` è validato (`override|global`), ma
- `gate_mode` non è validato (`block|warn`) e
- `risk_mode` non è validato (`risk_pct_of_capital|risk_usdt_fixed`).

Con typo, il sistema entra in fallback impliciti (es. `risk_mode` invalido trattato come percentuale), con comportamento non intenzionale.

**Raccomandazione**:
- Validazione fail-fast dei campi enumerati (`gate_mode`, `risk_mode`, `capital_base_mode`, ecc.) in loader.

### 4) LOW — Fallback DB silenziosi sui gate di esposizione

`sum_trader_exposure()`, `sum_global_exposure()` e `count_open_same_symbol()` catturano `sqlite3.OperationalError` e ritornano `0`.

**Impatto**: in caso di schema rotto/tabella assente, i gate possono diventare permissivi senza alert.

**Raccomandazione**:
- Mantenere compatibilità ma introdurre logging strutturato (warning/error) con contesto query/db.
- Opzionale: feature flag per modalità strict in produzione (errore bloccante).

## Config review

### `config/operation_rules.yaml`
- Struttura coerente con il loader (hard caps separati, defaults completi).
- `position_management` è coerente con il normalizer v2 (`mode`, `trader_hint`, `machine_event`).
- `gate_mode: block` a default è una scelta prudente.

### `config/trader_rules/trader_3.yaml`
- Override coerente e completo per i parametri principali.
- `gate_mode: warn` è utile in fase di tuning, ma in produzione può ridurre la protezione operativa.

## Verifiche eseguite

1. Esecuzione test modulo:
   - `pytest -q src/operation_rules/tests`
   - Risultato: `54 passed in 3.72s`

2. Validazione config runtime:
   - Invocazione `validate_operation_rules_config(rules_dir='config')`
   - Risultato: `ok`

## Priorità di remediation suggerita

1. **P1 (immediata)**: fix bypass cap con `risk_hint` (HIGH).
2. **P2**: correzione query `count_open_same_symbol` (MEDIUM).
3. **P3**: validazione enum config in loader (MEDIUM).
4. **P4**: logging su fallback DB (LOW).

