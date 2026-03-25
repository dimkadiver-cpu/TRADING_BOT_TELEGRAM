# Fase 4 — Sequenza prompt di avvio

Usa questi prompt in ordine. Non iniziare uno step prima che il precedente sia completato e testato.
Prima di ogni prompt, verifica che i test dello step precedente passino tutti.

---

## Step 12 — Migration + Pydantic models

```
Leggi docs/PRD_FASE_4.md sezione "Step 12".

Fai queste tre cose in ordine:

1. Crea db/migrations/011_operational_signals.sql con lo schema della tabella
   operational_signals esattamente come definito nel PRD.

2. In src/parser/models/new_signal.py aggiungi il validator
   check_entry_magnitude_consistency: se ci sono 2+ entries e il rapporto
   max/min supera 3.0, aggiunge un warning alla lista warnings del modello.
   Non blocca il parsing. Solo entries — non toccare TP e SL.

3. Crea src/parser/models/operational.py con i modelli:
   - OperationalSignal (composizione: contiene parse_result: TraderParseResult)
   - ResolvedTarget (dataclass slots=True)
   - ResolvedSignal (composizione: contiene operational + resolved_target)
   Segui esattamente le definizioni nel PRD.

4. Scrivi test per i nuovi modelli in src/parser/models/tests/.

Verifica finale: tutti i test esistenti continuano a passare (smoke suite + full suite).
```

---

## Step 13 — Operation Rules Engine

```
Leggi docs/PRD_FASE_4.md sezione "Layer 4 — Operation Rules Engine" e "Step 13".

Prerequisito: Step 12 completato e testato.

1. Crea config/operation_rules.yaml con lo schema completo definito nel PRD
   (global_hard_caps, global_defaults, Set A entry_split, Set B position_management).
   Usa valori placeholder per i trader — i valori reali verranno aggiunti dopo.

2. Crea la directory config/trader_rules/ con un file esempio
   config/trader_rules/trader_3.yaml (valori da definire con l'utente,
   per ora usa i global_defaults come base).

3. Crea src/operation_rules/__init__.py (vuoto).

4. Crea src/operation_rules/loader.py:
   - Carica operation_rules.yaml e il file trader specifico
   - Merge a 4 livelli: global_hard_caps > trader_on_off > trader_specific > global_defaults
   - I global_hard_caps non sono overridabili
   - Restituisce un oggetto MergedRules typed (Pydantic)

5. Crea src/operation_rules/risk_calculator.py:
   - compute_exposure(parse_result, rules) → float (% portfolio)
   - sum_exposure(trader_id, db_path) → float (somma esposizioni aperte per trader)
   - sum_exposure_global(db_path) → float (somma tutte le esposizioni aperte)
   - Usa signals WHERE status != 'CLOSED' AND sl IS NOT NULL

6. Crea src/operation_rules/engine.py:
   - apply(parse_result, trader_id, db_path) → OperationalSignal
   - Segui esattamente la logica gate del PRD (8 step in ordine)
   - Per NEW_SIGNAL: gate check → sizing → entry_split → snapshot Set B → blocked/ok
   - Per UPDATE: solo snapshot Set B, passthrough

7. Scrivi test:
   - src/operation_rules/tests/test_loader.py (merge regole, precedenza hard caps)
   - src/operation_rules/tests/test_risk_calculator.py (calcolo esposizione)
   - src/operation_rules/tests/test_engine.py (gate block, sizing, split, UPDATE passthrough)

Verifica finale: tutti i test esistenti continuano a passare.
```

---

## Step 14 — Target Resolver

```
Leggi docs/PRD_FASE_4.md sezione "Layer 5 — Target Resolver" e "Step 14".

Prerequisito: Step 13 completato e testato.

1. Crea src/storage/signals_query.py con le query accessor:
   - count_open(trader_id, symbol, db_path) → int
   - get_by_root_telegram_id(telegram_msg_id, trader_id, db_path) → Row | None
   - get_by_trader_signal_id(signal_id, trader_id, db_path) → Row | None
   - get_open_by_symbol(trader_id, symbol, db_path) → list[Row]
   - get_open_by_trader(trader_id, side, db_path) → list[Row]
     (side: 'BUY' | 'SELL' | None per all_positions)
   Usa la tabella signals esistente (001_init.sql).

2. Crea src/target_resolver/__init__.py (vuoto).

3. Crea src/target_resolver/models.py con ResolvedTarget se non già in operational.py.

4. Crea src/target_resolver/resolver.py:
   - resolve(operational_signal, db_path) → ResolvedTarget
   - Gestisce i 3 kind (STRONG/SYMBOL/GLOBAL) con i method corrispondenti
   - Dopo risoluzione, applica eligibility check intent-aware (tabella nel PRD)
   - Se target_ref è None (NEW_SIGNAL senza riferimento), restituisce None

5. Scrivi test src/target_resolver/tests/test_resolver.py:
   - STRONG REPLY: trova segnale per root_telegram_id
   - STRONG EXPLICIT_ID: trova segnale per trader_signal_id
   - SYMBOL: trova posizioni aperte per symbol
   - GLOBAL all_long / all_short / all_positions
   - Eligibility: U_CLOSE_FULL su PENDING → WARN
   - Eligibility: qualsiasi intent su CLOSED → INELIGIBLE
   - Target non trovato → UNRESOLVED

Verifica finale: tutti i test esistenti continuano a passare.
```

---

## Step 15 — Integrazione nel Router

```
Leggi docs/PRD_FASE_4.md sezione "Step 15" e il flusso completo in cima.

Prerequisito: Step 14 completato e testato.

1. In src/telegram/router.py, dopo che CoherenceChecker restituisce validation_status=VALID:
   a. Chiama operation_rules.engine.apply(parse_result, trader_id, db_path)
      → produce OperationalSignal
   b. Se NEW_SIGNAL e non is_blocked:
      - INSERT in signals (attempt_key, trader_id, symbol, side, entry_json, sl, tp_json,
        status='PENDING', confidence, raw_text)
   c. Chiama target_resolver.resolver.resolve(operational_signal, db_path)
      → produce ResolvedTarget
   d. INSERT in operational_signals (tutti i campi, incluso resolved_target_ids)
   e. Aggiorna processing_status del raw_message → done

2. Se is_blocked:
   - INSERT operational_signals con is_blocked=1 e block_reason
   - Aggiorna processing_status → done (non failed — il segnale è stato processato,
     solo bloccato dalle regole operative)

3. Aggiungi test di integrazione in src/telegram/tests/:
   - NEW_SIGNAL valido → signals PENDING + operational_signals creati
   - NEW_SIGNAL bloccato (trader disabled) → operational_signals is_blocked=1, no signals
   - UPDATE con target risolto → operational_signals con resolved_target_ids
   - UPDATE con target non trovato → operational_signals target_eligibility=UNRESOLVED

Verifica finale:
- Smoke suite: tutti i test passano (inclusi i nuovi)
- Full suite: tutti i test passano
- Aggiungi Step 12-15 come ✓ in docs/AUDIT.md
```

---

## Note operative

- **Ordine tassativo**: 12 → 13 → 14 → 15. Ogni step dipende dal precedente.
- **Comando test standard**: `.venv/Scripts/python.exe -m pytest <percorso> -q`
- **Smoke suite**: `src/parser/models/tests/ src/parser/tests/ src/telegram/tests/ src/validation/tests/`
- **Valori trader reali** (size, leverage, max_risk): da definire con l'utente durante Step 13
  prima di compilare i file `config/trader_rules/`.
- **Non toccare**: `src/execution/`, `src/exchange/`, `db/migrations/001-010`
- **Riferimento architetturale**: `docs/PRD_FASE_4.md` — documento autoritativo
