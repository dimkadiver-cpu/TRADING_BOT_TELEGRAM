# Piano migrazione â€” operation_rules â†’ CanonicalMessage v1

**Obiettivo:** riscrivere il layer `src/operation_rules/` per accettare `CanonicalMessage`
in input (invece di `TraderParseResult` legacy) e allinearlo al nuovo template
`config/new_operation_tempate.yaml`.

**Non toccare:** `risk_calculator.py` â€” autonomo, senza dipendenze legacy, riusabile as-is.

---

## Decisioni prese

| Domanda | Decisione |
|---|---|
| Sezioni `tp/sl/updates/pending` | Dentro `global_defaults` â†’ overridabili per trader |
| Solo `global_hard_caps` | Non overridabile (include `market_execution`) |
| `position_management` legacy | Eliminata completamente |
| `OperationalSignal.parse_result` | Rinominato `canonical_message: CanonicalMessage` |
| MARKET entry senza prezzo | `sizing_deferred=True` â€” size calcolata da Sistema 1 al momento dell'ordine |
| Politica esecuzione MARKET | `market_execution` in `global_hard_caps`: mode `tolerance\|free`, `tolerance_pct`, `range_tolerance_pct` |
| `max_entries_to_use` | Commentato â€” ignorato per ora |
| `log_reports` / `log_only_intents` | Commentati â€” ignorati per ora |

---

## Checklist implementazione

### STEP 0 â€” Preparazione config
- [x] **0.1** Copiare `config/new_operation_tempate.yaml` â†’ `config/operation_rules.yaml`
      (sostituisce il file esistente)
- [x] **0.2** Verificare i YAML trader in `config/trader_rules/*.yaml`:
      
**Stato STEP 0:** COMPLETATO

---

### STEP 1 â€” `loader.py` â€” `EffectiveRules` e merge

**File:** `src/operation_rules/loader.py`

- [x] **1.1** Aggiungere a `HardCaps`:
  ```python
  market_execution: dict[str, Any]
  # {mode: "tolerance"|"free", tolerance_pct: float, range_tolerance_pct: float}
  ```
- [x] **1.2** Aggiungere a `EffectiveRules` le nuove sezioni:
  ```python
  tp: dict[str, Any]       # {use_tp_count, close_distribution}
  sl: dict[str, Any]       # {use_original_sl, be_trigger}
  updates: dict[str, Any]  # {apply_move_stop, apply_close_partial, ...}
  pending: dict[str, Any]  # {cancel_pending_by_engine, pending_timeout_hours, ...}
  ```
- [x] **1.3** Rimuovere da `EffectiveRules`:
  - campo `tp_handling` (rimpiazzato da `tp`)
  - campo `position_management` (rimpiazzato da `updates` + `pending`)
- [x] **1.4** Aggiornare `load_effective_rules()`:
  - leggere `hard_caps_raw.get("market_execution", {})` â†’ `HardCaps.market_execution`
  - leggere `merged.get("tp", {})` â†’ `EffectiveRules.tp`
  - leggere `merged.get("sl", {})` â†’ `EffectiveRules.sl`
  - leggere `merged.get("updates", {})` â†’ `EffectiveRules.updates`
  - leggere `merged.get("pending", {})` â†’ `EffectiveRules.pending`
  - rimuovere lettura di `tp_handling` e `position_management`
- [x] **1.5** Rimuovere `_normalize_position_management()` e `_validate_position_management_config()`
- [x] **1.6** Aggiungere validazione per le nuove sezioni:
  - `market_execution.mode` in `{"tolerance", "free"}`
  - `sl.be_trigger` in `{null, "tp1", "tp2", "tp3", "tp4"}`
  - `pending.cancel_averaging_pending_after` in `{null, "tp1", "tp2", "tp3", "tp4"}`
  - `pending.cancel_unfilled_pending_after` in `{null, "tp1", "tp2", "tp3", "tp4"}`
  - `tp.close_distribution.mode` in `{"equal", "table"}`
- [x] **1.7** Aggiornare `_validate_entry_split_config()`: 
  - rimuovere validazione blocco `ZONE` (deprecato)
  - **nota stato attuale:** e ancora presente validazione/compatibilita legacy sul blocco AVERAGING (con warning di deprecazione)

**Stato STEP 1:** COMPLETATO

---

### STEP 2 â€” `operational.py` â€” nuovo `OperationalSignal`

**File:** `src/parser/models/operational.py`

- [x] **2.1** Rinominare `parse_result: Any` â†’ `canonical_message: CanonicalMessage`
      con import da `src.parser.canonical_v1.models`
- [x] **2.2** Aggiungere campo `sizing_deferred: bool = False`
      (True quando `primary_class=SIGNAL` + MARKET entry senza prezzo indicativo)
- [x] **2.3** `management_rules: dict[str, Any]` â€” ora snapshot di `{tp, sl, updates, pending}`
- [x] **2.4** Verificare `ResolvedSignal` â€” usa `operational: OperationalSignal` per composizione,
      non accede a `parse_result` direttamente â†’ nessuna modifica necessaria

**Stato STEP 2:** COMPLETATO

---

### STEP 3 â€” `engine.py` â€” input e discriminazione

**File:** `src/operation_rules/engine.py`

- [ ] **3.1** Cambiare firma di `apply()`:
  ```python
  def apply(self, message: CanonicalMessage, trader_id: str, *, db_path: str,
            skip_capital_gates: bool = False) -> OperationalSignal
  ```
- [ ] **3.2** Rimuovere `_coerce_entities()`, `_extract_entry_prices()`,
      `_extract_sl_price()`, `_extract_symbol()`, `_extract_entry_plan_entries()`,
      `_parse_first_float()`, `_parse_all_floats()` â€” tutti sostituiti da accesso diretto tipizzato
- [ ] **3.3** Aggiornare la discriminazione `primary_class`:
  ```python
  # SIGNAL  â†’ gate completi + calcolo size (o sizing_deferred se MARKET puro)
  # UPDATE  â†’ passthrough + snapshot management_rules
  # REPORT  â†’ passthrough, nessun gate
  # INFO    â†’ passthrough, nessun gate
  ```
- [ ] **3.4** Aggiornare `_make_blocked()` â€” `parse_result=` â†’ `canonical_message=`
- [ ] **3.5** Aggiornare `_snapshot_management_rules()`:
  ```python
  # PRIMA: rules.position_management + rules.tp_handling
  # DOPO:  rules.tp + rules.sl + rules.updates + rules.pending
  ```
- [ ] **3.6** Aggiornare gate `risk_hint` (solo per SIGNAL):
  ```python
  # PRIMA: entities.get("risk_hint")
  # DOPO:  message.signal.risk_hint.value if message.signal and message.signal.risk_hint else None
  ```

---

### STEP 4 â€” `engine.py` â€” gate SIGNAL con CanonicalMessage

**File:** `src/operation_rules/engine.py`

- [ ] **4.1** Gate 2 â€” entry prezzi:
  - legge `[leg.price.value for leg in signal.entries if leg.price is not None]`
  - se lista vuota MA `entry_structure == ONE_SHOT` e `entries[0].entry_type == MARKET`
    â†’ **non blocca**, setta `sizing_deferred=True`, salta calcolo size (vai a STEP 5)
  - se lista vuota per altri motivi â†’ blocca `missing_entry`
- [ ] **4.2** Gate 3 â€” SL:
  - legge `signal.stop_loss.price.value if signal.stop_loss and signal.stop_loss.price else None`
  - se None â†’ blocca `missing_stop_loss`
- [ ] **4.3** Gate 5 â€” symbol: legge `signal.symbol` (stringa diretta, no estrazione)
- [ ] **4.4** Gate capital (6-8) con `sizing_deferred=True`:
  - usa `risk_pct_of_capital` direttamente da config (senza `sl_distance_pct`)
  - `position_size_usdt=None`, `sl_distance_pct=None` nell'output

---

### STEP 5 â€” `engine.py` â€” `_compute_entry_split()` riscrittura

**File:** `src/operation_rules/engine.py`

- [ ] **5.1** Nuova firma:
  ```python
  def _compute_entry_split(signal: SignalPayload, rules: EffectiveRules) -> dict[str, float]
  ```
- [ ] **5.2** Dispatch pulito da `entry_structure` + `entries[0].entry_type`:
  ```
  ONE_SHOT + MARKET  â†’ MARKET.single
  ONE_SHOT + LIMIT   â†’ LIMIT.single
  TWO_STEP + MARKET  â†’ MARKET.averaging   (entries[0].entry_type == "MARKET")
  TWO_STEP + LIMIT   â†’ LIMIT.averaging    (entries[0].entry_type == "LIMIT")
  RANGE              â†’ LIMIT.range
  LADDER             â†’ LIMIT.ladder
  ```
- [ ] **5.3** Per MARKET entry (`sizing_deferred=True`): restituisce `{"E1": 1.0}` senza
      accedere ai weights (non c'Ã¨ prezzo, non ha senso splittare)
- [ ] **5.4** Rimuovere codice legacy: `plan_type`, `order_types`, `entry_mode`,
      `entry_plan_entries` dict grezzi, `ZONE` check, fallback `AVERAGING`

---

### STEP 6 â€” Test

**File:** `src/operation_rules/tests/`

- [ ] **6.1** Riscrivere `test_engine.py`:
  - rimuovere import legacy (`TraderParseResult`, `NewSignalEntities`, `Price` da `models/canonical`)
  - costruire `CanonicalMessage` di test direttamente
  - coprire tutti i gate (1-9)
  - coprire i 6 dispatch `entry_structure` (ONE_SHOTÃ—2, TWO_STEPÃ—2, RANGE, LADDER)
  - aggiungere test `sizing_deferred=True` per MARKET entry senza prezzo
- [ ] **6.2** Riscrivere `test_loader.py`:
  - testare nuove sezioni `tp`, `sl`, `updates`, `pending` nel merge
  - testare override per trader
  - testare validazione `be_trigger`, `cancel_*_after`, `market_execution.mode`
  - rimuovere test su `position_management` e `tp_handling` legacy
- [ ] **6.3** `test_risk_calculator.py` â€” invariato, nessuna modifica
- [ ] **6.4** `pytest src/operation_rules/tests/ -v` â€” tutti verdi

---

### STEP 7 â€” Cleanup finale

- [ ] **7.1** Verificare `src/target_resolver/resolver.py`: se accede a `operational.parse_result`
      â†’ aggiornare a `operational.canonical_message`
- [ ] **7.2** Verificare `src/telegram/router.py`: se chiama `engine.apply(parse_result=...)`
      â†’ aggiornare a `engine.apply(message=canonical_message, ...)`
- [ ] **7.3** Rimuovere import `TraderParseResult` da `engine.py`
- [ ] **7.4** Aggiornare CLAUDE.md â€” Step B completato

---

## Ordine di esecuzione

```
STEP 0 â†’ STEP 1 â†’ STEP 2 â†’ STEP 3 â†’ STEP 4 â†’ STEP 5 â†’ STEP 6 â†’ STEP 7
```

Tutti gli step sono sequenziali (ogni step dipende dal precedente).

---

## File toccati

| File | Operazione |
|---|---|
| `config/new_operation_tempate.yaml` | âœ… template finalizzato |
| `config/operation_rules.yaml` | sostituzione con template (STEP 0) |
| `config/trader_rules/*.yaml` | pulizia blocchi legacy (STEP 0) |
| `src/operation_rules/loader.py` | riscrittura parziale (STEP 1) |
| `src/parser/models/operational.py` | rename + nuovo campo (STEP 2) |
| `src/operation_rules/engine.py` | riscrittura logica completa (STEP 3-5) |
| `src/operation_rules/tests/test_engine.py` | riscrittura (STEP 6) |
| `src/operation_rules/tests/test_loader.py` | riscrittura (STEP 6) |
| `src/target_resolver/resolver.py` | verifica + eventuale fix (STEP 7) |
| `src/telegram/router.py` | verifica + eventuale fix (STEP 7) |

## File NON toccati

| File | Motivo |
|---|---|
| `src/operation_rules/risk_calculator.py` | autonomo, zero dipendenze legacy |
| `src/operation_rules/tests/test_risk_calculator.py` | invariato |
| `src/parser/canonical_v1/models.py` | contratto canonico, non toccare |

---

## Aggiornamento avanzamento lavori

**Data aggiornamento:** 2026-04-23

### Lavoro fatto (verificato su repository)

- STEP 0 completato:
  - `config/operation_rules.yaml` allineato al template nuovo (`new_operation_tempate.yaml`).
  - `config/trader_rules/trader_3.yaml` e `config/trader_rules/trader_a.yaml` senza blocchi legacy `position_management` / `tp_handling`.

- STEP 1 completato:
  - `HardCaps.market_execution` presente.
  - `EffectiveRules` usa sezioni nuove: `tp`, `sl`, `updates`, `pending`.
  - vecchi campi `tp_handling` / `position_management` rimossi dal loader.
  - `load_effective_rules()` legge le nuove sezioni e valida enum/campi critici.
  - validazioni nuove presenti (`market_execution.mode`, `sl.be_trigger`, `pending.cancel_*_after`, `tp.close_distribution.mode`).

- STEP 2 completato:
  - `OperationalSignal` usa `canonical_message: CanonicalMessage` come payload primario.
  - aggiunto `sizing_deferred: bool = False`.
  - `management_rules` documentato e verificato come snapshot di `{tp, sl, updates, pending}`.
  - introdotto bridge temporaneo: input legacy via `parse_result` ancora accettato e normalizzato a `CanonicalMessage`, cosi i caller non migrati non si rompono prima dello STEP 3.
  - `ResolvedSignal` verificato: continua a comporre `OperationalSignal`, nessun accesso diretto interno a `parse_result`.
  - test mirati verdi:
    - `.venv\\Scripts\\python.exe -m pytest src\\parser\\models\\tests\\test_operational_models.py -q`
    - `.venv\\Scripts\\python.exe -m pytest src\\parser\\models\\tests\\test_operational_models.py src\\target_resolver\\tests\\test_resolver.py -q`

### Residuo per chiudere STEP 2

- Nessuno (STEP 2 completato).


