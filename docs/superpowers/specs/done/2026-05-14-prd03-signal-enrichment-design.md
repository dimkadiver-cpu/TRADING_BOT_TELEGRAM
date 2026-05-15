# PRD 03 — Signal Enrichment Layer

**Stato:** spec approvata per implementazione  
**Data:** 2026-05-14  
**Deriva da:** `docs/Raggionamento/documento_madre_riprogettazione_trading_bot_telegram_v_0_1.md` v0.2  
**Ambito:** Gate 1 stateless post-parser — arricchimento, normalizzazione, symbol blacklist, update admission, management plan, DB separation  
**Fuori ambito:** risk calculation, capacity check, trade_chain creation, lifecycle state machine, execution adapter

---

## 1. Scopo

Questo PRD definisce il primo gate operativo del nuovo runtime, posizionato immediatamente dopo il parser pipeline:

```
canonical_messages (parser.sqlite3)
        ↓
SignalEnrichmentProcessor   ← Gate 1 — stateless
        ↓
enriched_canonical_messages (parser.sqlite3)
  BLOCK / REVIEW → fine, audit only
  PASS ↓
        ↓
Lifecycle Entry Gate        ← Gate 2 — stateful (PRD 04)
```

Il Signal Enrichment Layer non ha bisogno di sapere quante posizioni sono aperte, non calcola size e non accede a `ops.sqlite3`. Produce un `EnrichedCanonicalMessage` che il Lifecycle Engine (PRD 04) consumerà direttamente.

---

## 2. Contesto

### 2.1 Stato attuale dopo PRD 2.c

- `runtime_v2` è lo stack primario attivo.
- `raw_messages` e `canonical_messages` vivono in `db/tele_signal_bot.sqlite3`.
- Il parser pipeline produce `CanonicalMessage v2` e lo persiste in `canonical_messages`.
- Non esiste ancora nessun gate operativo: dopo il parser il messaggio non viene processato ulteriormente.

### 2.2 Cosa introduce PRD 03

- Separazione fisica dei DB: `db/parser.sqlite3` e `db/ops.sqlite3`.
- Config operativa: `config/operation_config.yaml` + `config/traders/<id>.yaml`.
- `SignalEnrichmentProcessor`: Gate 1 stateless.
- Tabella `enriched_canonical_messages` in `parser.sqlite3`.

---

## 3. Decisioni di design

### 3.1 Gate 1 stateless, Gate 2 stateful

Il Signal Enrichment Layer (PRD 03) esegue solo controlli che non richiedono stato operativo:

| Gate 1 — Enrichment (PRD 03) | Gate 2 — Lifecycle Entry (PRD 04) |
|---|---|
| Symbol blacklist | Capacity check (posizioni aperte) |
| Trader registrato | Risk calculation (size, risk%) |
| SL presente se richiesto | Target resolution per UPDATE |
| Entry structure accettata | Max concurrent trades |
| TP trim + weights entry split | Freed capacity da BE |
| Update admission (policy) | Update target exists |
| Account routing | Trade chain creation |
| Management plan build | State machine entry |

### 3.2 Un'unica classe con metodi interni

`SignalEnrichmentProcessor` è una singola classe con metodi privati per ogni check. Non ci sono 3 classi separate. Il routing per `primary_class` (SIGNAL/UPDATE/REPORT/INFO) avviene internamente.

### 3.3 Update admission su source_intent

Il check di ammissione degli update usa `ActionItem.source_intent` (IntentType di parser_v2), non `action_type` (UpdateOperationType). Questo permette di distinguere `MOVE_STOP` (price esplicito) da `MOVE_STOP_TO_BE` (target=ENTRY) anche se entrambi producono `action_type=SET_STOP`.

### 3.4 DB separati da PRD 03

La separazione fisica `parser_db` / `ops_db` parte da questo PRD. PRD 03 include la migrazione che:
- Rinomina `db/tele_signal_bot.sqlite3` → `db/parser.sqlite3`
- Aggiunge `enriched_canonical_messages` a `parser.sqlite3`
- Crea `db/ops.sqlite3` vuoto (pronto per PRD 04)

### 3.5 Config: file centrale + file per trader

```
config/
├── operation_config.yaml       ← globale: safety, account, blacklist, defaults
└── traders/
    ├── trader_a.yaml
    ├── trader_b.yaml
    ├── trader_c.yaml
    ├── trader_d.yaml
    └── trader_3.yaml
```

Merge priority: `account.max_capital_at_risk_pct` / `account.hard_max_per_signal_risk_pct` > `defaults` + `traders/<id>.yaml`

- `account_mode: single` → un unico account condiviso tra tutti i trader; i cap definiti in `account:` sono non-overridabili.
- `account_mode: per_trader_subaccount` → ogni `traders/<id>.yaml` definisce il proprio blocco `account:` con cap specifici.
- `global_safety` non è mai overridabile in nessuna modalità.

### 3.6 Risk e capacity check: PRD 04

Risk calculation, capacity check (`max_concurrent_trades`, `max_capital_at_risk_pct`) e `risk_freed_by_be` sono letti dal Lifecycle Entry Gate in PRD 04. La sezione `risk:` nella config viene definita qui ma non consumata da PRD 03.

---

## 4. Struttura config

### 4.1 `config/operation_config.yaml`

```yaml
# ── Sicurezza assoluta (non overridabile) ─────────────────────────────────────
global_safety:
  allow_unprotected_positions: false

# ── Account routing ───────────────────────────────────────────────────────────
account_mode: single                    # single | per_trader_subaccount

# single: unico account condiviso — max_capital_at_risk_pct e hard_max_per_signal_risk_pct
#         sono hard cap non overridabili.
# per_trader_subaccount: ogni config/traders/<id>.yaml definisce il proprio blocco account:
account:
  id: "main"
  capital_base_usdt: 1000.0
  max_leverage: 5
  max_capital_at_risk_pct: 10.0         # % portfolio totale — hard cap
  hard_max_per_signal_risk_pct: 2.0     # rischio max per singolo segnale — hard cap

# ── Trader autorizzati ────────────────────────────────────────────────────────
# BLOCK con reason=trader_not_registered se trader non in lista.
registered_traders:
  - trader_3
  - trader_a
  - trader_b
  - trader_c
  - trader_d

# ── Symbol blacklist ──────────────────────────────────────────────────────────
symbol_blacklist:
  global: []
  per_trader: {}

# ── Defaults (overridabili in config/traders/<id>.yaml) ───────────────────────
defaults:
  enabled: true
  gate_mode: block                      # block | warn
  hedge_mode: false                     # true = consente posizioni opposte sullo stesso symbol (letto da PRD 04)

  # ── Signal Policy (Signal Enrichment — stateless) ─────────────────────────
  signal_policy:
    accepted_entry_structures:
      - ONE_SHOT
      - TWO_STEP
      - RANGE
      - LADDER

    # Politica MARKET entry (non è un hard cap — overridabile per trader)
    market_execution:
      mode: tolerance                   # tolerance | free
      tolerance_pct: 0.5
      range_tolerance_pct: 0.2

    # MARKET.range non esiste: RANGE richiede leg LIMIT per contratto canonico.
    # Il loader deve sollevare errore esplicito se trovata tale combinazione nella config.
    entry_split:
      LIMIT:
        single:
          weights: {E1: 1.0}
        range:
          split_mode: endpoints         # endpoints | firstpoint | lastpoint | midpoint
          weights: {E1: 0.50, E2: 0.50}
        averaging:
          weights: {E1: 0.70, E2: 0.30}
        ladder:
          weights: {E1: 0.50, E2: 0.30, E3: 0.20}
      MARKET:
        single:
          weights: {E1: 1.0}
        averaging:
          weights: {E1: 0.70, E2: 0.30}

    tp:
      use_tp_count: null                # null = tutti | N = primi N

    sl:
      use_original_sl: true
      require_sl: true

    price_corrections:
      enabled: false
      round_to_tick: false
      clamp_to_exchange_precision: false

    price_sanity:
      enabled: false
      symbol_ranges: {}

  # Ammissione update Telegram (usa source_intent da parser_v2, senza prefisso U_)
  update_admission:
    MOVE_STOP: true
    MOVE_STOP_TO_BE: false
    CLOSE_FULL: true
    CLOSE_PARTIAL: true
    CANCEL_PENDING: true
    ADD_ENTRY: false
    REENTER: false
    MODIFY_ENTRY: false
    MODIFY_TARGETS: false
    INVALIDATE_SETUP: false

  # ── Management Plan (embedded nella trade_chain — letto da Lifecycle PRD 04) ─
  management_plan:
    be_trigger: null                    # null | tp1 | tp2 | tp3 | tp4
    be_buffer_pct: 0.0                  # SL a BE + offset% per commissioni

    close_distribution:
      mode: table                       # table | equal
      table:
        1: [100]
        2: [50, 50]
        3: [30, 30, 40]
        4: [25, 25, 25, 25]
        5: [20, 20, 20, 20, 20]
        6: [20, 20, 20, 20, 10, 10]

    cancel_pending_by_engine: true      # master switch logiche cancel TP-triggered
    cancel_pending_on_timeout: true
    pending_timeout_hours: 24
    cancel_averaging_pending_after: null  # null | tp1 | tp2
    cancel_unfilled_pending_after: null   # null | tp1 | tp2
    risk_freed_by_be: true
    protective_sl_mode: exchange_native_first  # exchange_native_first | bot_managed

  # ── Risk (letto dal Lifecycle Entry Gate — PRD 04) ─────────────────────────
  risk:
    mode: risk_pct_of_capital           # risk_pct_of_capital | risk_usdt_fixed
    risk_pct_of_capital: 1.0
    risk_usdt_fixed: 10.0
    capital_base_mode: static_config    # static_config | live_equity
    capital_base_usdt: 1000.0
    leverage: 1
    use_trader_risk_hint: false
    max_capital_at_risk_per_trader_pct: 5.0
    max_concurrent_trades: 5
    max_concurrent_same_symbol: 1
```

### 4.2 `config/traders/trader_a.yaml` (esempio)

```yaml
enabled: true
gate_mode: block

# Solo in account_mode: per_trader_subaccount — definisce il subaccount del trader.
# In account_mode: single questo blocco viene ignorato.
account:
  id: "trader_a_sub"
  capital_base_usdt: 300.0
  max_leverage: 3
  max_capital_at_risk_pct: 8.0
  hard_max_per_signal_risk_pct: 2.0

signal_policy:
  tp:
    use_tp_count: 2

update_admission:
  MOVE_STOP_TO_BE: true

management_plan:
  be_trigger: tp2
  be_buffer_pct: 0.05

risk:
  risk_pct_of_capital: 0.5
  max_concurrent_trades: 3
```

---

## 5. Contratti

### 5.1 `EffectiveEnrichmentConfig`

Output del loader dopo merge `defaults` + `traders/<id>.yaml`:

```python
class EffectiveEnrichmentConfig:
    trader_id: str
    enabled: bool
    gate_mode: Literal["block", "warn"]
    hedge_mode: bool                    # letto da PRD 04, non da PRD 03
    account_id: str                     # da account.id (single) o traders/<id>.yaml account.id
    signal_policy: SignalPolicyConfig
    update_admission: dict[str, bool]   # source_intent → bool (senza prefisso U_)
    management_plan: ManagementPlanConfig
    risk: RiskConfig                    # letto da PRD 04, non da PRD 03
```

### 5.2 `EnrichedCanonicalMessage`

```python
class EnrichedCanonicalMessage:
    enrichment_id: int
    canonical_message_id: int
    raw_message_id: int
    trader_id: str
    account_id: str
    primary_class: Literal["SIGNAL", "UPDATE", "REPORT", "INFO"]
    enrichment_decision: Literal["PASS", "BLOCK", "REVIEW"]
    reason_code: str | None
    enriched_signal: EnrichedSignalPayload | None   # solo SIGNAL PASS
    enriched_actions: list[EnrichedTargetActionGroup] | None  # solo UPDATE PASS
    management_plan: ManagementPlanConfig | None    # solo SIGNAL PASS
    enrichment_log: list[EnrichmentLogEntry]
    policy_snapshot: dict
    policy_version: str
    lifecycle_processed: bool   # True = già consumato da PRD 04 o non eleggibile
    created_at: datetime
```

### 5.3 `EnrichmentLogEntry`

```python
class EnrichmentLogEntry:
    check: str              # es. "tp_count_trimmed", "symbol_blacklisted_global"
    original: str | None    # valore prima della trasformazione
    result: str             # valore dopo, o "BLOCKED"
    detail: str | None
```

### 5.4 `ManagementPlanConfig`

```python
class ManagementPlanConfig:
    be_trigger: Literal["tp1", "tp2", "tp3"] | None
    be_buffer_pct: float
    close_distribution: CloseDistributionConfig
    cancel_pending_by_engine: bool
    cancel_pending_on_timeout: bool
    pending_timeout_hours: int
    cancel_averaging_pending_after: Literal["tp1", "tp2"] | None
    cancel_unfilled_pending_after: Literal["tp1", "tp2"] | None
    risk_freed_by_be: bool
    protective_sl_mode: Literal["exchange_native_first", "bot_managed"]
```

---

## 6. Logica interna del processor

`SignalEnrichmentProcessor._process(canonical_message)` esegue in sequenza:

### 6.1 Routing per primary_class

```
SIGNAL  → _check_signal_gate() → _enrich_signal() → _build_management_plan()
UPDATE  → _check_update_admission()
REPORT  → PASS diretto, nessun enrichment, lifecycle_processed=1 (audit only, mai al lifecycle)
INFO    → PASS diretto (o REVIEW se gate_mode=warn), lifecycle_processed=1 (audit only, mai al lifecycle)
```

REPORT e INFO non entrano mai nel Lifecycle Entry Gate. Il campo `lifecycle_processed` viene impostato a `1` al momento della persistenza per escluderli automaticamente dalla query del worker di PRD 04, senza richiedere logica extra nel consumer.

### 6.2 Check SIGNAL (in ordine)

1. `trader_id` in `registered_traders` → BLOCK `trader_not_registered`
2. symbol in `symbol_blacklist.global` → BLOCK `symbol_blacklisted_global`
3. symbol in `symbol_blacklist.per_trader[trader_id]` → BLOCK `symbol_blacklisted_trader`
4. `signal.entry_structure` in `accepted_entry_structures` → BLOCK `unsupported_entry_structure`
5. SL assente + `require_sl: true` → BLOCK `missing_stop_loss`
6. TP trim: se `use_tp_count` non null e TP > N → tronca, log `tp_count_trimmed:<from>→<to>`
7. Entry split weights applicati all'`EnrichedSignalPayload`
8. `price_corrections` se enabled → log ogni trasformazione
9. `price_sanity` se enabled → BLOCK `price_out_of_range` se fuori range
10. `management_plan` costruito dalla config effettiva

### 6.3 Check UPDATE

Per ogni `TargetActionGroup` e ogni `ActionItem`:
- Legge `action_item.source_intent`
- Controlla `update_admission[source_intent]`
- Se `false`:
  - `gate_mode: block` → BLOCK dell'intero UPDATE, reason `action_type_disabled:<intent>`
  - `gate_mode: warn` → REVIEW, reason `action_type_warned:<intent>`
- Se tutti gli ActionItem sono ammessi → PASS

### 6.4 gate_mode: warn

Con `gate_mode: warn`, nessun check produce BLOCK — produce REVIEW. Utile per debug o per trader in fase di test. L'audit log registra comunque ogni violazione.

---

## 7. Persistenza

### 7.1 Separazione DB

```
db/
├── parser.sqlite3    ← rinominato da tele_signal_bot.sqlite3 (migrazione 026)
│     raw_messages
│     canonical_messages
│     enriched_canonical_messages    ← aggiunto da PRD 03
│
└── ops.sqlite3       ← creato da PRD 03, vuoto fino a PRD 04
```

Config path:
```
PARSER_DB_PATH=db/parser.sqlite3
OPS_DB_PATH=db/ops.sqlite3
```

### 7.2 Schema `enriched_canonical_messages`

```sql
CREATE TABLE enriched_canonical_messages (
    enrichment_id            INTEGER PRIMARY KEY,
    canonical_message_id     INTEGER NOT NULL UNIQUE,
    raw_message_id           INTEGER NOT NULL,
    trader_id                TEXT NOT NULL,
    account_id               TEXT NOT NULL,
    primary_class            TEXT NOT NULL,   -- SIGNAL | UPDATE | REPORT | INFO
    enrichment_decision      TEXT NOT NULL,   -- PASS | BLOCK | REVIEW
    reason_code              TEXT,
    enriched_signal_json     TEXT,
    enriched_actions_json    TEXT,
    management_plan_json     TEXT,
    enrichment_log_json      TEXT NOT NULL,
    policy_snapshot_json     TEXT NOT NULL,
    policy_version           TEXT NOT NULL,
    lifecycle_processed      INTEGER NOT NULL DEFAULT 0,  -- 1 = già consumato o non eleggibile
    created_at               TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_ecm_trader_id  ON enriched_canonical_messages(trader_id);
CREATE INDEX idx_ecm_decision   ON enriched_canonical_messages(enrichment_decision);
CREATE INDEX idx_ecm_lifecycle  ON enriched_canonical_messages(lifecycle_processed, enrichment_decision, primary_class);
CREATE INDEX idx_ecm_created    ON enriched_canonical_messages(created_at);
```

**Idempotenza:** `UNIQUE(canonical_message_id)` — se enrichment esiste già, viene restituito senza rieseguire.

**`lifecycle_processed`:** impostato a `1` direttamente alla persistenza per `primary_class IN ('REPORT', 'INFO')`. Per SIGNAL/UPDATE con decisione BLOCK o REVIEW viene anch'esso impostato a `1` (non eleggibili al lifecycle). Solo SIGNAL/UPDATE con PASS hanno `lifecycle_processed=0` al momento della scrittura e vengono consumati dal worker di PRD 04.

### 7.3 Migrazioni

- `026_parser_db_separation.sql` — rinomina file, crea `ops.sqlite3`
- `027_enriched_canonical_messages.sql` — aggiunge tabella in `parser.sqlite3`

---

## 8. Package structure

```
src/runtime_v2/
└── signal_enrichment/
    ├── __init__.py
    ├── models.py              ← EnrichedCanonicalMessage, EnrichmentLogEntry,
    │                             ManagementPlanConfig, EnrichedSignalPayload,
    │                             EnrichedTargetActionGroup
    ├── config_loader.py       ← OperationConfigLoader: carica YAML, merge, hot-reload
    ├── processor.py           ← SignalEnrichmentProcessor
    └── repository.py          ← EnrichedCanonicalMessageRepository (parser.sqlite3)
```

**Regole di import:**
- `signal_enrichment/` non importa nulla da `src/runtime_v2/` oltre `parser_pipeline/models.py`
- Non importa nessun modulo lifecycle, risk, execution
- Non accede a `ops.sqlite3`

**Wiring in `main.py`:**

```python
enrichment_config   = OperationConfigLoader(config_dir="config")
enriched_repo       = EnrichedCanonicalMessageRepository(db_path=parser_db_path)
enrichment_proc     = SignalEnrichmentProcessor(
    config_loader=enrichment_config,
    repository=enriched_repo,
)
# ParserPipelineProcessor chiama enrichment_proc dopo aver persistito il canonical message
```

---

## 9. Flusso live aggiornato

Il handoff tra PRD 03 e PRD 04 avviene tramite DB (architettura disaccoppiata):

```
TelegramListener
    ↓
_process_item()
    ↓
ChannelConfigResolver + RawMessageRepository
    ↓
ParserPipelineProcessor
    ↓  CanonicalParseResult
SignalEnrichmentProcessor
    ↓  persiste EnrichedCanonicalMessage in parser.sqlite3
    │     REPORT / INFO          → lifecycle_processed=1  (audit only, stop)
    │     SIGNAL/UPDATE BLOCK    → lifecycle_processed=1  (audit only, stop)
    │     SIGNAL/UPDATE REVIEW   → lifecycle_processed=1  (audit only, stop)
    └──── SIGNAL/UPDATE PASS     → lifecycle_processed=0  (eleggibile al lifecycle)

                        ↓  (processo separato — PRD 04)

LifecycleGateWorker
    → polling su parser.sqlite3:
      SELECT * FROM enriched_canonical_messages
       WHERE lifecycle_processed = 0
         AND enrichment_decision = 'PASS'
         AND primary_class IN ('SIGNAL', 'UPDATE')
       ORDER BY created_at ASC
    → per ogni record: LifecycleEntryGate.process(enriched_msg)
    → crea TradeChain in ops.sqlite3
    → UPDATE enriched_canonical_messages SET lifecycle_processed=1
```

PRD 03 e PRD 04 sono processi/worker indipendenti. `parser.sqlite3` è il punto di handoff. Il worker di PRD 04 non importa codice di `signal_enrichment/` — legge solo dalla tabella.

---

## 10. Acceptance contract

### 10.1 Done significa

Un `CanonicalMessage v2` da `canonical_messages` produce un `EnrichedCanonicalMessage` persistito in `enriched_canonical_messages` con decisione `PASS/BLOCK/REVIEW`, `enrichment_log` tracciabile e `policy_snapshot` — senza importare codice lifecycle, risk o execution. I DB sono fisicamente separati in `parser.sqlite3` e `ops.sqlite3`.

Il handoff verso PRD 04 avviene tramite DB: solo record con `enrichment_decision=PASS` e `primary_class IN ('SIGNAL','UPDATE')` hanno `lifecycle_processed=0` e sono visibili al `LifecycleGateWorker`. REPORT, INFO e qualsiasi BLOCK/REVIEW hanno `lifecycle_processed=1` già al momento della persistenza.

### 10.2 Criteri pass/fail

| # | Caso | Risultato atteso |
|---|---|---|
| 1 | SIGNAL, symbol in `symbol_blacklist.global` | BLOCK, `symbol_blacklisted_global` |
| 2 | SIGNAL, trader non in `registered_traders` | BLOCK, `trader_not_registered` |
| 3 | SIGNAL senza SL + `require_sl: true` | BLOCK, `missing_stop_loss` |
| 4 | SIGNAL con entry_structure non accettata | BLOCK, `unsupported_entry_structure` |
| 5 | SIGNAL con 5 TP + `use_tp_count: 3` | PASS, 3 TP, log `tp_count_trimmed:5→3` |
| 6 | SIGNAL, symbol in `symbol_blacklist.per_trader.trader_a` | BLOCK, `symbol_blacklisted_trader` |
| 7 | UPDATE con `source_intent=MOVE_STOP_TO_BE`, admission `false` | BLOCK, `action_type_disabled:MOVE_STOP_TO_BE` |
| 8 | UPDATE con `source_intent=MOVE_STOP`, admission `true` | PASS |
| 9 | UPDATE con tutte actions ammesse | PASS, `enriched_actions` popolato |
| 10 | UPDATE con mix ammesse/bloccate, `gate_mode: block` | BLOCK |
| 11 | UPDATE con mix ammesse/bloccate, `gate_mode: warn` | REVIEW |
| 12 | REPORT | PASS, nessun enriched_signal/actions, nessun management_plan, lifecycle_processed=1 |
| 13 | Override trader: `trader_a.use_tp_count: 2`, 3 TP | PASS, log `tp_count_trimmed:3→2` |
| 14 | `account_id` assegnato da `account.id` (single) o `traders/<id>.yaml account.id` (per_trader_subaccount) | corretto in output |
| 15 | `management_plan` costruito da config effettiva | tutti i campi valorizzati |
| 16 | `policy_snapshot` in DB | auditabile |
| 17 | Stessa `canonical_message_id` rielaborata | enrichment esistente restituito, no duplicato |
| 18 | Nessun import lifecycle/risk/execution | verificato da test di importazione |
| 19 | DB `parser.sqlite3` e `ops.sqlite3` separati | due file fisici distinti |
| 20 | `ops.sqlite3` creato ma vuoto | pronto per PRD 04 |
| 21 | REPORT PASS → `lifecycle_processed=1` in DB | non consumato da PRD 04 |
| 22 | INFO PASS → `lifecycle_processed=1` in DB | non consumato da PRD 04 |
| 23 | SIGNAL BLOCK → `lifecycle_processed=1` in DB | non consumato da PRD 04 |
| 24 | SIGNAL PASS → `lifecycle_processed=0` in DB | eleggibile al worker PRD 04 |

---

## 11. Test minimi

### 11.1 Unit

- Loader: merge `defaults` + `config/traders/trader_a.yaml` → config effettiva corretta
- Loader: `global_hard_caps` non modificabile da override trader
- Loader: `global_safety` non modificabile
- Loader: hot-reload su modifica file
- Blacklist: symbol globale bloccato
- Blacklist: symbol per-trader bloccato, altri trader non bloccati
- Blacklist: symbol non in lista → PASS
- TP trim: 5 TP + `use_tp_count: 3` → 3 TP, log corretto
- TP trim: `use_tp_count: null` → tutti i TP, nessun log
- `require_sl: true` + SL assente → BLOCK
- `require_sl: false` + SL assente → PASS
- `update_admission`: `MOVE_STOP_TO_BE: false` → BLOCK action
- `update_admission`: `MOVE_STOP: true` → PASS
- `gate_mode: warn` → REVIEW invece di BLOCK per violazioni non hard
- `management_plan` costruito correttamente da defaults + override
- `account_id` assegnato da `account.id` se `account_mode: single`
- `account_id` assegnato da `traders/<id>.yaml account.id` se `account_mode: per_trader_subaccount`
- Loader: `account.max_capital_at_risk_pct` e `hard_max_per_signal_risk_pct` non overridabili in single mode
- Loader: errore esplicito se `entry_split` contiene chiave `MARKET.range`

### 11.2 Integration

- SIGNAL end-to-end: canonical_message → EnrichedCanonicalMessage PASS in parser.sqlite3
- SIGNAL bloccato: enrichment_decision=BLOCK, management_plan=null
- UPDATE end-to-end: TargetActionGroup con actions ammesse → PASS
- UPDATE bloccato: action_type_disabled
- REPORT: PASS diretto, log vuoto
- Idempotenza: stessa canonical_message_id → no duplicato
- DB separation: `parser.sqlite3` esiste, `ops.sqlite3` esiste e vuoto
- Nessun codice di `signal_enrichment/` importa `src/operation_rules/` legacy

### 11.3 Regression

- Tutti i test esistenti di `runtime_v2` passano dopo la migrazione DB
- `parser.sqlite3` contiene `raw_messages` e `canonical_messages` intatti
- `main.py` si avvia senza errori con i nuovi path DB

---

## 12. Rischi e decisioni aperte

### 12.1 Migrazione DB

Il rename `tele_signal_bot.sqlite3` → `parser.sqlite3` è l'operazione più delicata. Va eseguita con migrazione esplicita e backup preventivo. Tutti i path nel codice devono essere aggiornati contestualmente.

### 12.2 Hot-reload config

Il `OperationConfigLoader` fa hot-reload su modifica file. Un errore di YAML in produzione non deve crashare il processo — fallback all'ultima config valida con log di errore.

### 12.3 policy_version

`policy_version` nel record di enrichment deve identificare univocamente la config usata. Approccio consigliato: hash SHA256 del contenuto dei file YAML caricati al momento del processing.

### 12.4 entry_split weights

I weights `E1/E2/E3` devono sommare a 1.0 dopo la normalizzazione. Il loader deve normalizzare e loggare se la somma originale non era 1.0.

### 12.5 Sezione risk nella config

La sezione `risk:` viene definita e caricata dal `OperationConfigLoader` ma non consumata dal `SignalEnrichmentProcessor`. Viene esposta tramite `EffectiveEnrichmentConfig.risk` per uso del Lifecycle Entry Gate (PRD 04).

---

## 13. Output atteso per PRD 04

PRD 04 può partire quando PRD 03 consegna:

```python
EnrichedCanonicalMessage(
    enrichment_id=42,
    canonical_message_id=123,
    trader_id="trader_a",
    account_id="main",
    enrichment_decision="PASS",
    reason_code=None,
    enriched_signal=EnrichedSignalPayload(...),
    management_plan=ManagementPlanConfig(
        be_trigger="tp2",
        be_buffer_pct=0.05,
        pending_timeout_hours=24,
        ...
    ),
    enrichment_log=[
        EnrichmentLogEntry(check="tp_count_trimmed", original="5", result="3"),
    ],
    policy_snapshot={...},
    policy_version="sha256:abc123",
)
```

Il Lifecycle Entry Gate (PRD 04) legge `EnrichedCanonicalMessage.enriched_signal` (o `enriched_actions` per UPDATE) e `management_plan`, esegue capacity check e risk calculation, e crea la `TradeChain` in `ops.sqlite3`.
