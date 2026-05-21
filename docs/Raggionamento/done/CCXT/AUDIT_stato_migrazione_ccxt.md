# AUDIT - Stato migrazione CCXT / eliminazione Hummingbot

**Data:** 2026-05-19  
**Riferimento PRD:** `PRD_runtime_v2_passaggio_hummingbot_a_ccxt_lifecycle_rev2.md`  
**Branch verificato:** `main`

---

## Sintesi

La migrazione da Hummingbot a CCXT e' operativamente completata per le Fasi 0-4 e per il wiring runtime della Fase 5.

Verifica eseguita oggi:

- `main.py` non usa piu' `hb_adapter`
- il bootstrap runtime centralizza adapter, worker, watcher WS e cleanup in `ExecutionRuntime`
- `BybitWsFillWatcher` viene avviato se `config/execution.yaml` ha `websocket.enabled: true`
- la reconciliation periodica viene schedulata con `websocket.poll_fallback_period_seconds` quando `poll_fallback_enabled: true`
- la suite `execution_gateway + lifecycle` e' verde sul perimetro verificato

Restano aperti:

- validazione end-to-end reale su Bybit Demo
- rafforzamento del recovery con cross-check posizione lifecycle-side
- pulizia finale dei documenti storici Hummingbot

---

## Stato per fase

| Fase PRD | Descrizione | Stato | Nota |
|---|---|---|---|
| Fase 0 | Refactor lifecycle contract | Completata | presente in main |
| Fase 1 | CcxtBybitAdapter REST | Completata | presente in main |
| Fase 2 | CCXT Pro WebSocket event stream | Completata | watcher disponibile e ora wired dal bootstrap |
| Fase 3 | Lifecycle processor corretto | Completata | suite lifecycle verde |
| Fase 4 | Protective sync adapter-side | Completata | presente in main |
| Fase 5 | Recovery e reconciliation robusta | Parziale | wiring runtime chiuso, recovery ancora da rafforzare |
| Fase 6 | Pulizia finale Hummingbot | Parziale | runtime pulito, docs storiche ancora da riallineare |

---

## Cosa e' implementato

### Fase 0 - Lifecycle contract

- `db/ops_migrations/003_ops_quantity_runtime.sql`
- `src/runtime_v2/lifecycle/models.py`
- `src/runtime_v2/execution_gateway/client_order_id.py`

### Fase 1 - Adapter REST Bybit via CCXT

File principali:

```text
src/runtime_v2/execution_gateway/adapters/ccxt_bybit/
  adapter.py
  order_builder.py
  status_mapper.py
```

### Fase 2 - WebSocket + hedge mode

- `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/ws_fill_watcher.py`
- `AdapterConfig.websocket`
- `AdapterConfig.hedge_mode`
- wiring bootstrap in `main.py`

### Fase 3 - Lifecycle processor corretto

- gestione `ENTRY_FILLED`, `TP_FILLED`, `SL_FILLED`
- gestione `CLOSE_PARTIAL_FILLED`, `CLOSE_FULL_FILLED`
- gestione `STOP_MOVED_CONFIRMED`
- gestione `PENDING_ENTRY_CANCELLED_CONFIRMED`
- `SYNC_PROTECTIVE_ORDERS` su eventi parziali

### Fase 4 - Protective sync

- `BybitOrderBuilder` genera `amend_sl_qty`
- `CcxtBybitAdapter._handle_amend_sl_qty()`
- repository gateway con lookup payload/client order id

### Fase 5 - Wiring runtime verificato oggi

- `ExecutionRuntime` in `main.py`
- `_build_execution_runtime()`
- `_close_execution_runtime()`
- `_run_reconciliation_periodically()`
- start opzionale di `BybitWsFillWatcher`
- stop corretto del watcher e dell'adapter in shutdown

---

## Gap residui Fase 5

| Deliverable | Stato |
|---|---|
| Recovery on boot (`run_reconciliation`) | Implementato |
| Callback di reconciliation dopo errore WS | Implementato |
| Reconciliation periodica | Implementata |
| Cross-check posizione nel path lifecycle | Parziale |
| Deduplica eventi | Implementata |

Il punto ancora aperto non e' il wiring, ma la robustezza del recovery: serve una verifica lifecycle-side piu' esplicita dello stato posizione exchange.

---

## Stato reale Hummingbot nel runtime verificato

Verifica repository del 2026-05-19:

```text
src/runtime_v2/execution_gateway/adapters/
  base.py
  factory.py
  fake.py
  ccxt_bybit/
```

Nel path runtime verificato non risultano adapter Hummingbot attivi.

Inoltre:

- il test lifecycle che referenziava ancora Hummingbot e' stato riallineato al builder CCXT
- gli eventuali riferimenti Hummingbot residui sono da considerare documentali/storici, non parte del runtime attivo verificato oggi

---

## Test eseguiti

Comandi:

```bash
.\.venv\Scripts\python.exe -m pytest tests\runtime_v2\test_main_runtime_bootstrap.py -q --tb=short
.\.venv\Scripts\python.exe -m pytest tests\runtime_v2\lifecycle\test_integration.py -q --tb=short
.\.venv\Scripts\python.exe -m pytest tests\runtime_v2\execution_gateway tests\runtime_v2\lifecycle -q --tb=short
```

Risultati:

| Suite | Risultato |
|---|---|
| `tests/runtime_v2/test_main_runtime_bootstrap.py` | 3 passed |
| `tests/runtime_v2/lifecycle/test_integration.py` | 10 passed |
| `tests/runtime_v2/execution_gateway + tests/runtime_v2/lifecycle` | 273 passed, 6 skipped |

Gli `skipped` residui sono test gated `bybit_testnet` che richiedono credenziali reali.

---

## Analisi gap vs acceptance criteria

| Criterio | Stato |
|---|---|
| Runtime esegue ordini Bybit Demo senza Hummingbot | Si, lato wiring/runtime |
| Adapter configurabile per live con safety gate | Si |
| Segnale nuovo genera chain e comandi coerenti | Si |
| Fill entry aggiorna qty e stato | Si |
| TP fill riduce size e sincronizza protezioni | Si |
| SL fill chiude o riduce correttamente | Si |
| `CLOSE_PARTIAL` / `CLOSE_FULL` hanno eventi dedicati | Si |
| `MOVE_STOP_TO_BREAKEVEN` conferma protezione senza sporcare lifecycle state | Si |
| `CANCEL_PENDING_ENTRY` funziona anche su chain OPEN | Si |
| `client_order_id` resta chiave di correlazione | Si |
| Eventi parziali non vengono persi per chiave idempotenza grossolana | Si |
| Recovery dopo downtime | Parzialmente validato |
| Hummingbot non e' piu' richiesto dal runtime finale verificato | Si |

---

## Cosa manca prima di considerare chiusa la migrazione

1. Eseguire un ciclo reale Bybit Demo: entry -> TP/SL -> close.
2. Rafforzare il recovery con cross-check posizione lifecycle-side.
3. Ripulire i documenti storici che parlano ancora di Hummingbot come runtime attivo.

---

## Debito tecnico residuo

| Item | Priorita' |
|---|---|
| Validazione live Bybit Demo non eseguita in questa sessione | Alta |
| Cross-check posizione recovery/lifecycle non ancora esplicito | Media |
| Documenti storici Hummingbot da riallineare | Bassa |
| Variabili/env obsolete nei docs operativi | Bassa |

---

## Verifica 2026-05-19 - note di sessione

Modifiche introdotte in questa sessione:

- fix bootstrap/shutdown `main.py`
- wiring opzionale `BybitWsFillWatcher`
- scheduling reconciliation periodica
- test di regressione su bootstrap runtime
- riallineamento test lifecycle AC3B da Hummingbot a CCXT

Primary signal di questa sessione:

- bootstrap runtime CCXT verificato
- suite gateway+lifecycle verde sul perimetro locale verificato

Secondary signal:

- nessuna prova live Bybit Demo eseguita in questa sessione
