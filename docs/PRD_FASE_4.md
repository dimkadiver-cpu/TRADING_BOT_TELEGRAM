# PRD — Fase 4: Operation Rules + Target Resolver

> **Stato:** DEFINITIVO — approvato in brainstorm 2026-03-25
> **Prerequisiti:** Step 11 completato (Validazione coerenza, 25 test pass)
> **Prossimo:** Step 12 → implementazione

---

## Obiettivo

Fase 4 è il ponte tra parser/validazione (Fase 1–3) e Sistema 1 / freqtrade (Fase 5).

Riceve un `TraderParseResult` con `validation_status=VALID` e produce un `ResolvedSignal` pronto per l'esecuzione, contenente:
- parametri operativi calcolati (size, leverage, entry split)
- regole di gestione posizione (snapshot al momento del segnale)
- target concreti risolti (quali posizioni aperte sono coinvolte)

**Fase 4 non esegue nulla sull'exchange.** Prepara e persiste il pacchetto di istruzioni.

---

## Flusso completo

```
TraderParseResult (validated, status=VALID)
      ↓
Layer 4 — Operation Rules Engine
  → gate check (trader enabled? capital at risk ok? same symbol?)
  → sizing (position_size_pct, leverage, entry_split)
  → risk_hint: legge parse_results.risk_hint se use_trader_risk_hint=true
  → snapshot management rules (Set B)
  → INSERT signals (status=PENDING)         ← bridge per Sistema 1
  → INSERT operational_signals              ← parametri + gate result
      ↓
OperationalSignal
      ↓
Layer 5 — Target Resolver
  → risolve target_ref in position IDs concreti
  → controlla eligibilità intent-aware
  → UPDATE operational_signals (resolved_target_ids, target_eligibility)
      ↓
ResolvedSignal  →  pronto per Sistema 1
```

---

## DB — tabelle coinvolte

### Tabelle esistenti usate

| Tabella | Operazione | Note |
|---|---|---|
| `parse_results` | READ | source input; ha già `risk_hint`, `risky_flag` |
| `signals` | WRITE (NEW_SIGNAL) | popola status=PENDING; bridge a Fase 5 |
| `signals` | READ (UPDATE) | Target Resolver cerca posizioni aperte |
| `raw_messages` | READ | per costruire `attempt_key` |

### Tabella nuova — `operational_signals`

**Migration:** `db/migrations/011_operational_signals.sql`

```sql
CREATE TABLE IF NOT EXISTS operational_signals (
  op_signal_id           INTEGER PRIMARY KEY AUTOINCREMENT,
  parse_result_id        INTEGER NOT NULL
                           REFERENCES parse_results(parse_result_id),
  attempt_key            TEXT REFERENCES signals(attempt_key),  -- NULL per UPDATE
  trader_id              TEXT NOT NULL,
  message_type           TEXT NOT NULL,   -- NEW_SIGNAL | UPDATE

  -- Gate result
  is_blocked             INTEGER NOT NULL DEFAULT 0,
  block_reason           TEXT,            -- es. "trader_disabled", "global_cap_exceeded"

  -- Set A — parametri apertura (solo NEW_SIGNAL)
  position_size_pct      REAL,
  position_size_usdt     REAL,
  entry_split_json       TEXT,            -- {"E1": 0.3, "E2": 0.7} o {"E1":0.33,"E2":0.34,"E3":0.33}
  leverage               INTEGER,
  risk_hint_used         INTEGER NOT NULL DEFAULT 0,

  -- Set B — regole gestione (snapshot config al momento del segnale)
  management_rules_json  TEXT,

  -- Price corrections hook (implementazione futura)
  price_corrections_json TEXT,            -- NULL finché non implementato

  -- Audit
  applied_rules_json     TEXT,            -- list[str] regole applicate
  warnings_json          TEXT,            -- list[str]

  -- Target resolution
  resolved_target_ids    TEXT,            -- JSON list[int] di op_signal_id risolti
  target_eligibility     TEXT,            -- ELIGIBLE | INELIGIBLE | WARN | UNRESOLVED
  target_reason          TEXT,            -- motivo se non ELIGIBLE

  created_at             TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_op_signals_parse_result
ON operational_signals(parse_result_id);

CREATE INDEX IF NOT EXISTS idx_op_signals_trader_type
ON operational_signals(trader_id, message_type);
```

### Tabelle NON toccate in Fase 4

- `positions` — stato exchange, aggiornato da Sistema 1 via WebSocket
- `trades` — creato da Sistema 1 in Fase 5
- `orders` — creato da Sistema 1 in Fase 5
- `fills` — aggiornato da Sistema 1

### `signals.attempt_key` — formato

```
attempt_key = f"{env}_{channel_id}_{telegram_msg_id}_{trader_id}"
```

Dati disponibili da `parse_results` + `raw_messages`. Stesso formato usato da `update_applier.py`.

---

## Layer 4 — Operation Rules Engine

### Responsabilità

Non modifica il parsing. Aggiunge parametri operativi e verifica gate di rischio.

> "Il parser dice cosa dice il trader. Le operation rules dicono come noi eseguiamo."

### Config YAML — struttura

```
config/operation_rules.yaml            # hard caps globali + defaults
config/trader_rules/{trader_id}.yaml   # override per trader
```

**Merge**: `global_hard_caps` > `trader_on_off` > `trader_specific` > `global_defaults`

I `global_hard_caps` non sono overridabili da nessun trader.

### `config/operation_rules.yaml` — schema completo

```yaml
# ── Hard caps globali (non overridabili) ──────────────────────────────────
global_hard_caps:
  max_capital_at_risk_pct: 10.0       # % portfolio totale su tutti i trader
  max_per_signal_pct: 2.0             # cap per singolo segnale

# ── Defaults per trader (overridabili in trader_rules/) ───────────────────
global_defaults:
  enabled: true
  gate_mode: block                    # block | warn

  # Set A — apertura posizione
  use_trader_risk_hint: false         # usa risk_hint estratto dal parser
  position_size_pct: 1.0             # % portfolio per posizione
  leverage: 1
  max_capital_at_risk_per_trader_pct: 5.0
  max_concurrent_same_symbol: 1

  entry_split:
    ZONE:
      split_mode: endpoints           # endpoints | midpoint | three_way
      weights: {E1: 0.50, E2: 0.50}  # usato solo con split_mode: three_way
    AVERAGING:
      distribution: equal             # equal | decreasing
      # decreasing: schema definito per trader in trader_rules/
    LIMIT:
      weights: {E1: 1.0}
    MARKET:
      weights: {E1: 1.0}

  # Price corrections hook (implementazione futura)
  price_corrections:
    enabled: false
    method: null                      # "number_theory" | "round_to_tick" | null

  # Range statici per sanity check (opzionale, senza dati live)
  price_sanity:
    enabled: false
    symbol_ranges: {}                 # es. BTCUSDT: {min: 10000, max: 500000}

  # Set B — snapshot regole gestione posizione (letto da Sistema 1)
  position_management:
    on_tp_hit:
      - {tp_level: 1, action: close_partial, close_pct: 50}
      - {tp_level: 2, action: move_to_be}
      - {tp_level: 3, action: close_full}
    auto_apply_intents:               # UPDATE intents che Sistema 1 esegue automaticamente
      - U_MOVE_STOP
      - U_CLOSE_FULL
      - U_CLOSE_PARTIAL
      - U_CANCEL_PENDING
    log_only_intents:                 # UPDATE intents solo informativi
      - U_TP_HIT
      - U_SL_HIT
```

### `config/trader_rules/{trader_id}.yaml` — esempio

```yaml
# config/trader_rules/trader_a.yaml
enabled: true
gate_mode: warn                            # solo warning in test

use_trader_risk_hint: true
position_size_pct: 0.5
leverage: 10
max_capital_at_risk_per_trader_pct: 3.0
max_concurrent_same_symbol: 2

entry_split:
  ZONE:
    split_mode: three_way
    weights: {E1: 0.30, E2: 0.40, E3: 0.30}
  AVERAGING:
    distribution: decreasing
    weights: {E1: 0.40, E2: 0.30, E3: 0.20, E4: 0.10}   # 4 entries max

price_sanity:
  enabled: true
  symbol_ranges:
    BTCUSDT: {min: 10000, max: 500000}
    ETHUSDT: {min: 500, max: 50000}

position_management:
  on_tp_hit:
    - {tp_level: 1, action: close_partial, close_pct: 30}   # override
    - {tp_level: 2, action: move_to_be}
    # tp_level 3: eredita close_full da global_defaults
  # auto_apply_intents: eredita da global_defaults
```

### ZONE split_mode

| Modalità | Entries prodotte | Descrizione |
|---|---|---|
| `endpoints` | 2: LOW + HIGH | Split ai bordi della zona |
| `midpoint` | 1: (LOW+HIGH)/2 | Solo punto centrale |
| `three_way` | 3: LOW + MID + HIGH | Tre livelli con pesi configurabili |

### Calcolo rischio

```
esposizione_per_segnale = position_size_pct × (|entry - SL| / entry) × leverage
```

**Nota:** Usa SL originale estratto dal parser (conservativo — upper bound).
`is_breakeven` è gestito da Sistema 1 e non è disponibile in Fase 4.

```python
# Somme per gate check
trader_exposure = SUM(signals.sl_distance_pct WHERE trader_id=? AND status!='CLOSED')
global_exposure = SUM(signals.sl_distance_pct WHERE status!='CLOSED')
```

### Logica gate — ordine di valutazione

```python
def apply_rules(parse_result, trader_id) -> OperationalSignal:

    # 1. Trader abilitato?
    if not rules.enabled:
        return blocked("trader_disabled")

    # 2. NEW_SIGNAL: controlli specifici apertura
    if parse_result.message_type == "NEW_SIGNAL":

        # 3. Stesso symbol già aperto?
        open_same = signals_query.count_open(trader_id, symbol)
        if open_same >= rules.max_concurrent_same_symbol:
            return blocked("max_concurrent_same_symbol")

        # 4. Calcola esposizione nuovo segnale
        new_exp = compute_exposure(parse_result, rules)

        # 5. Cap hard per singolo segnale (global, non overridabile)
        if new_exp > hard_caps.max_per_signal_pct:
            return blocked("per_signal_cap_exceeded")

        # 6. Cap per trader
        trader_exp = signals_query.sum_exposure(trader_id)
        if trader_exp + new_exp > rules.max_capital_at_risk_per_trader_pct:
            return blocked("trader_capital_at_risk_exceeded")

        # 7. Cap globale (global, non overridabile)
        global_exp = signals_query.sum_exposure_global()
        if global_exp + new_exp > hard_caps.max_capital_at_risk_pct:
            return blocked("global_capital_at_risk_exceeded")

        # 8. Price sanity statica (se abilitata)
        if rules.price_sanity.enabled:
            check_price_ranges(parse_result, rules.price_sanity)

        # 9. Calcola parametri
        size_pct = resolve_size(parse_result, rules)
        split = compute_entry_split(parse_result, rules)

        # 10. Snapshot management rules
        mgmt_rules = snapshot_management_rules(rules)

    # UPDATE: passthrough con snapshot rules
    if parse_result.message_type == "UPDATE":
        mgmt_rules = snapshot_management_rules(rules)

    return OperationalSignal(...)
```

### Modello `OperationalSignal`

```python
class OperationalSignal(BaseModel):
    """TraderParseResult + parametri esecutivi."""

    # composizione — non copia
    parse_result: TraderParseResult

    # parametri apertura (solo NEW_SIGNAL)
    position_size_pct: float | None = None
    position_size_usdt: float | None = None
    entry_split: dict[str, float] | None = None   # {"E1": 0.3, "E2": 0.7}
    leverage: int | None = None
    risk_hint_used: bool = False

    # snapshot regole gestione (Set B)
    management_rules: dict[str, Any] | None = None

    # gate
    is_blocked: bool = False
    block_reason: str | None = None

    # audit
    applied_rules: list[str] = []
    warnings: list[str] = []
```

---

## Layer 5 — Target Resolver

### Responsabilità

Risolve `target_ref` in `op_signal_id` concreti. Non inventa nulla — solo risolve.

### Logica per kind/method

| `target_ref.kind` | `method` | Query |
|---|---|---|
| `STRONG` | `REPLY` | `signals` WHERE `root_telegram_id` = reply_to_msg_id del parser |
| `STRONG` | `TELEGRAM_LINK` | `parse_results` WHERE extracted_link corrisponde |
| `STRONG` | `EXPLICIT_ID` | `signals` WHERE `trader_signal_id` = ref |
| `SYMBOL` | — | `signals` WHERE `trader_id=? AND symbol=? AND status!='CLOSED'` |
| `GLOBAL` | `all_long` | `signals` WHERE `trader_id=? AND side='BUY' AND status!='CLOSED'` |
| `GLOBAL` | `all_short` | `signals` WHERE `trader_id=? AND side='SELL' AND status!='CLOSED'` |
| `GLOBAL` | `all_positions` | `signals` WHERE `trader_id=? AND status!='CLOSED'` |

### Eligibilità intent-aware

Il resolver verifica se il target risolto è in uno stato compatibile con l'intent dell'UPDATE.

| Intent | Status PENDING | Status ACTIVE | Status CLOSED |
|---|---|---|---|
| `U_CANCEL_PENDING` | ✓ ELIGIBLE | ✓ ELIGIBLE | ✗ INELIGIBLE |
| `U_CLOSE_FULL` | ⚠ WARN | ✓ ELIGIBLE | ✗ INELIGIBLE |
| `U_CLOSE_PARTIAL` | ⚠ WARN | ✓ ELIGIBLE | ✗ INELIGIBLE |
| `U_MOVE_STOP` | ⚠ WARN | ✓ ELIGIBLE | ✗ INELIGIBLE |
| `U_REENTER` | ✓ ELIGIBLE | ✓ ELIGIBLE | ✗ INELIGIBLE |
| `U_TP_HIT`, `U_SL_HIT` | INFO_ONLY | INFO_ONLY | INFO_ONLY |

**WARN** = segnale registrato, non bloccato, Sistema 1 farà il check exchange prima di eseguire.

### Modelli output

```python
@dataclass(slots=True)
class ResolvedTarget:
    kind: Literal["STRONG", "SYMBOL", "GLOBAL"]
    position_ids: list[int]       # op_signal_id dei segnali originali risolti
    eligibility: Literal["ELIGIBLE", "INELIGIBLE", "WARN", "UNRESOLVED"]
    reason: str | None            # motivo se non ELIGIBLE/UNRESOLVED


class ResolvedSignal(BaseModel):
    """Output finale di Fase 4 — pronto per Sistema 1."""
    operational: OperationalSignal
    resolved_target: ResolvedTarget | None   # None per NEW_SIGNAL senza target_ref
    is_ready: bool                           # True se non bloccato e target resolved
```

---

## Price Sanity — due livelli

### Livello 1 — Pydantic validator in `NewSignalEntities` (entries only)

Controlla consistenza intra-segnale tra entry prices. Non tocca TP e SL.

```python
@model_validator(mode="after")
def check_entry_magnitude_consistency(self) -> Self:
    """Se ci sono 2+ entries, il rapporto max/min non deve superare 3x."""
    if len(self.entries) < 2:
        return self
    prices = [e.value for e in self.entries]
    ratio = max(prices) / min(prices)
    if ratio > 3.0:
        # Non blocca — aggiunge warning al TraderParseResult
        self._warnings.append(
            f"entry_magnitude_inconsistent: ratio={ratio:.1f}"
        )
    return self
```

**Non blocca il parsing.** Aggiunge warning, la decisione spetta a Layer 4.

### Livello 2 — Layer 4 range statici (opzionale, senza dati live)

Configurato in `trader_rules/{trader_id}.yaml` sotto `price_sanity.symbol_ranges`.
Blocca con `block_reason="price_out_of_static_range"` se abilitato e entry fuori range.

### Livello 3 — Sistema 1 pre-execution (Fase 5)

Confronto con prezzo live dal WebSocket exchange.
```
|entry_estratto - market_price| / market_price > max_deviation_from_market_pct → BLOCK
```
Unico livello in grado di rilevare il caso `9500 vs 95000` con certezza.

---

## Confini Fase 4 / Fase 5

| Responsabilità | Fase 4 | Fase 5 (Sistema 1) |
|---|---|---|
| Crea `signals` (status=PENDING) | ✓ | Aggiorna → ACTIVE/CLOSED |
| Crea `operational_signals` | ✓ | Legge per size/leverage/rules |
| Applica Set B (management rules) | ✗ — solo snapshot | ✓ |
| Aggiorna `is_breakeven` / SL corrente | ✗ | ✓ |
| Aggiorna `positions` exchange | ✗ | ✓ via WebSocket |
| Crea `trades`, `orders` | ✗ | ✓ |
| Check prezzo live | ✗ | ✓ pre-execution |
| `update_planner.py` / `update_applier.py` | non toccare | riscrivere |

**UPDATE `U_TP_HIT` / `U_SL_HIT` non modificano stato nel DB in Fase 4.** Sono context intents: vengono risolti (target) e passati a Sistema 1 con management_rules_json. Sistema 1 decide cosa fare.

---

## Ordine di sviluppo

### Step 12 — Migration + Pydantic updates

```
db/migrations/011_operational_signals.sql
src/parser/models/new_signal.py     ← aggiungi entry_magnitude_consistency validator
src/parser/models/operational.py    ← OperationalSignal, ResolvedSignal (NUOVO)
src/parser/models/tests/            ← test nuovi modelli
```

### Step 13 — Operation Rules Engine

```
config/operation_rules.yaml                    ← schema completo con defaults
config/trader_rules/trader_3.yaml              ← primo trader (valori da definire)
src/operation_rules/__init__.py
src/operation_rules/loader.py                  ← carica + merge YAML (4 livelli)
src/operation_rules/risk_calculator.py         ← compute_exposure, sum_exposure
src/operation_rules/engine.py                  ← applica regole, produce OperationalSignal
src/operation_rules/tests/test_loader.py
src/operation_rules/tests/test_engine.py
src/operation_rules/tests/test_risk_calculator.py
```

### Step 14 — Target Resolver

```
src/storage/signals_query.py                   ← accessor: open signals, exposure sum
src/target_resolver/__init__.py
src/target_resolver/models.py                  ← ResolvedTarget
src/target_resolver/resolver.py                ← logica per kind/method + eligibility
src/target_resolver/tests/test_resolver.py
```

### Step 15 — Integrazione nel Router

```
src/telegram/router.py                         ← dopo VALID:
                                               →  operation_rules.engine.apply()
                                               →  target_resolver.resolver.resolve()
                                               →  storage.operational_signals.insert()
```

---

## File da creare / modificare

| File | Azione | Note |
|---|---|---|
| `db/migrations/011_operational_signals.sql` | CREA | schema tabella |
| `src/parser/models/new_signal.py` | MODIFICA | aggiungi entry magnitude validator |
| `src/parser/models/operational.py` | CREA | OperationalSignal, ResolvedSignal |
| `src/parser/models/tests/` | MODIFICA | test nuovi modelli |
| `config/operation_rules.yaml` | CREA | global defaults + hard caps |
| `config/trader_rules/` | CREA | directory + un file per trader |
| `src/operation_rules/` | CREA | modulo completo |
| `src/storage/signals_query.py` | CREA | accessor posizioni aperte |
| `src/target_resolver/` | CREA | modulo completo |
| `src/telegram/router.py` | MODIFICA | integrazione Layer 4+5 |

### Non toccare

- `src/storage/` (tranne aggiungere `signals_query.py`)
- `src/execution/update_planner.py` — verrà riscritto in Fase 5
- `src/execution/update_applier.py` — verrà riscritto in Fase 5
- `db/migrations/` (001–010) — schema esistente immutabile

---

## Domande aperte

Nessuna domanda architetturale aperta.

**Valori operativi da compilare** (non bloccanti per l'implementazione del framework):
- `position_size_pct` per ogni trader
- `leverage` per ogni trader
- `max_capital_at_risk_per_trader_pct` per ogni trader
- `entry_split.AVERAGING.weights` per trader che usano AVERAGING
- `price_sanity.symbol_ranges` per ogni trader

Questi vanno nei file `config/trader_rules/{trader_id}.yaml` durante o dopo Step 13.

---

*Documento prodotto in sessione brainstorm 2026-03-25. Sostituisce `docs/DRAFT_FASE_4.md`.*
