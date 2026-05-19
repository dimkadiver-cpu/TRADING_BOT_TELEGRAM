# AUDIT — Stato migrazione CCXT / eliminazione Hummingbot

**Data:** 2026-05-19  
**Riferimento PRD:** `PRD_runtime_v2_passaggio_hummingbot_a_ccxt_lifecycle_rev2.md`  
**Branch verificato:** `main` (HEAD `367aee5`)

---

## Sintesi

La migrazione da Hummingbot a CCXT è **sostanzialmente completata** per le Fasi 0–4.  
Reconciliation periodica (Fase 5) e pulizia Hummingbot (Fase 6) sono le uniche parti rimaste.  
I piani non sono stati marcati `done/` ma il codice è in main e i test passano.

---

## Stato per fase

| Fase PRD | Descrizione | Stato | Commit chiave |
|---|---|---|---|
| Fase 0 | Refactor lifecycle contract | ✅ Completata | `d6326bf`, `1237d19`, `91286de`, `105ea97` |
| Fase 1 | CcxtBybitAdapter REST | ✅ Completata | `c11fb67`, `685697c`, `9484b26` (merge) |
| Fase 2 | CCXT Pro WebSocket event stream | ✅ Completata | `070cbc1` (`ws_fill_watcher.py`, hedge_mode, run_reconciliation) |
| Fase 3 | Lifecycle processor corretto | ✅ Completata | `8464e78`, `cdb77bf`, `33cfd59`, `276728b` |
| Fase 4 | Protective sync (adapter side) | ✅ Completata | `f0acd5c` (`_handle_amend_sl_qty`, `amend_sl_qty` in order_builder) |
| Fase 5 | Recovery e reconciliation robusta | ⚠️ Parziale | `run_reconciliation()` esiste ma non periodica |
| Fase 6 | Pulizia Hummingbot | ❌ Non completata | Hummingbot ancora presente |

---

## Cosa è implementato (Fasi 0–4)

### Fase 0 — Lifecycle contract

- `db/ops_migrations/003_ops_quantity_runtime.sql` — nuovi campi `filled_entry_qty`, `open_position_qty`, `closed_position_qty`, `planned_entry_qty`, `execution_mode`
- `src/runtime_v2/lifecycle/models.py` — `TradeChain` con qty runtime, `be_protection_status` separato da `lifecycle_state`, `SYNC_PROTECTIVE_ORDERS` in `CommandType`, `LEGACY_BE_STATES`
- `src/runtime_v2/execution_gateway/client_order_id.py` — ruoli `exit_partial`, `exit_full`, `sync`

### Fase 1 — CcxtBybitAdapter REST

```
src/runtime_v2/execution_gateway/adapters/ccxt_bybit/
├── adapter.py          CcxtBybitAdapter — place, cancel, get_status, move_stop, close
├── order_builder.py    BybitOrderBuilder — traduce CommandType → params CCXT
└── status_mapper.py    Mappa risposta CCXT → RawAdapterOrder
```

Factory wired per `ccxt_bybit` con `hedge_mode`, `testnet`, `api_key`.

### Fase 2 — WebSocket + hedge mode

- `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/ws_fill_watcher.py` — `BybitWsFillWatcher` (ccxt.pro `watchOrders` → `ops_exchange_events`)
- `AdapterConfig.hedge_mode` + `WebsocketConfig` in `models.py`
- `BybitOrderBuilder.build()` — aggiunge `positionIdx` in hedge mode
- `CcxtBybitAdapter` — passa `hedge_mode` al builder, `set_leverage` con `positionIdx=0`
- `run_reconciliation()` in `ExchangeEventSyncWorker` — polling REST startup/post-WS-error

### Fase 3 — Lifecycle processor corretto

- `ENTRY_FILLED` handler — aggiorna `filled_entry_qty`, `open_position_qty`, avg ponderata, sblocca SL/TP da `WAITING_POSITION`
- `TP_FILLED` / `SL_FILLED` handlers — riducono `open_position_qty`, aggiornano `lifecycle_state`, emettono `SYNC_PROTECTIVE_ORDERS` se non finale
- `CLOSE_FULL_FILLED` / `CLOSE_PARTIAL_FILLED` — eventi dedicati (non `ENTRY_FILLED`)
- `STOP_MOVED_CONFIRMED` / `PENDING_ENTRY_CANCELLED_CONFIRMED` — handlers dedicati
- `CANCEL_PENDING_ENTRY` su chain già `OPEN` — cancella solo entry residue, emette `SYNC_PROTECTIVE_ORDERS`
- `be_protection_status` aggiornato senza toccare `lifecycle_state`

### Fase 4 — Protective sync (adapter)

- `BybitOrderBuilder` — `SYNC_PROTECTIVE_ORDERS` produce `action="amend_sl_qty"`
- `CcxtBybitAdapter._handle_amend_sl_qty()` — Mode B (edit SL reduce-only) + Mode C (trading_stop attached) + qty=0 (cancel residui)
- `repositories.py` — `get_active_client_order_ids()`, `get_payload_by_client_order_id()`
- Lifecycle emette `SYNC_PROTECTIVE_ORDERS` su TP non finale, SL parziale, close parziale

---

## Fase 5 — Gap residuo

| Deliverable PRD | Stato |
|---|---|
| Recovery on boot (`run_reconciliation`) | ✅ Implementato |
| Post-WS-error reconciliation callback | ✅ In `ws_fill_watcher.py` |
| Reconciliation **periodica** (ogni 30–60s) | ❌ Non implementata |
| Cross-check posizione via `fetchPositions` | ⚠️ In `_handle_amend_sl_qty`, non in path lifecycle |
| Deduplica eventi (idempotency_key UNIQUE) | ✅ In `ops_exchange_events` schema |

---

## File Hummingbot ancora presenti (Fase 6)

```
src/runtime_v2/execution_gateway/adapters/
├── hummingbot_api.py
└── hummingbot_api_paper.py

tests/runtime_v2/execution_gateway/
├── test_hummingbot_adapter.py
├── test_hummingbot_api_neutral.py
└── test_hummingbot_demo_gated.py

hummingbot_conf/hummingbot_logs.yml
hummingbot_logs/logs_hummingbot.log
docs/runtime_v2/execution_gateway/hummingbot_setup.md
```

Factory (`factory.py`) supporta ancora `hummingbot_api` come tipo valido.

---

## Test

| Suite | Risultato |
|---|---|
| `tests/runtime_v2/execution_gateway/` | 175 passed, 16 skipped |
| `tests/runtime_v2/lifecycle/` | 118 passed |
| Skipped | `@pytest.mark.bybit_testnet` — richiedono API key Bybit reale |

---

## Analisi gap vs acceptance criteria PRD

| Criterio PRD | Stato |
|---|---|
| 1. Runtime esegue ordini Bybit Demo senza Hummingbot | ✅ Configurando `ccxt_bybit` in `execution.yaml` |
| 2. Adapter configurabile per Bybit Live con safety gate | ✅ `allow_live_trading` in config |
| 3. Nuovo segnale → chain `WAITING_ENTRY`, SL/TP `WAITING_POSITION` | ✅ Gate Mode A/B/C implementato |
| 4. Primo fill entry → chain aperta, qty aggiornata, avg corretta | ✅ `ENTRY_FILLED` handler con weighted avg |
| 5. TP fill → size ridotta, BE generato, protective sync | ✅ `TP_FILLED` handler completo |
| 6. SL fill → chiusura/riduzione corretta | ✅ `SL_FILLED` handler |
| 7. `CLOSE_PARTIAL` / `CLOSE_FULL` con eventi dedicati | ✅ `CLOSE_PARTIAL_FILLED` / `CLOSE_FULL_FILLED` |
| 8. `MOVE_STOP_TO_BREAKEVEN` → `PROTECTED` solo dopo conferma | ✅ `be_protection_status` separato |
| 9. `CANCEL_PENDING_ENTRY` funziona su chain già `OPEN` | ✅ Emette `SYNC_PROTECTIVE_ORDERS` |
| 10. `client_order_id` come chiave di correlazione | ✅ |
| 11. Fill parziali non persi per idempotency key grossolana | ✅ Chiave `event_type:chain_id:exchange_order_id` |
| 12. Reconciliation recupera eventi persi dopo downtime | ⚠️ On-boot OK; reconciliation periodica mancante |
| 13. Hummingbot non più necessario al runtime finale | ⚠️ Non più necessario tecnicamente, ma ancora nel codice |
| 14. Multi-entry o supportato o bloccato esplicitamente | ✅ Supportato via `SYNC_PROTECTIVE_ORDERS` |

---

## Cosa manca per poter eliminare Hummingbot

Il sistema è già funzionale su CCXT. Per eseguire la Fase 6 (pulizia formale) mancano solo:

1. **Reconciliation periodica** — schedulare `run_reconciliation()` ogni 30–60s in `main.py` (o nel worker)
2. **Wiring `ws_fill_watcher` in `main.py`** — il file esiste ma non è avviato
3. **Validazione end-to-end su Bybit Demo** — almeno un ciclo completo (entry → SL/TP → close) testato live

Solo dopo questi tre punti ha senso eseguire la Fase 6 (rimuovere hummingbot_api.py, i test Hummingbot, i config/log, e aggiornare i docs).

---

## Debito tecnico residuo

| Item | Priorità |
|---|---|
| `ws_fill_watcher` non avviato in `main.py` | Alta |
| `run_reconciliation()` non chiamato periodicamente | Alta |
| Piani CCXT (Fase 0–2) non marcati `done/` in `docs/superpowers/plans/` | Bassa |
| `hummingbot_setup.md` e doc Hummingbot nei docs | Bassa (Fase 6) |
| Env vars `HUMMINGBOT_BASE_URL` / `HUMMINGBOT_SECRET` non rimosse da config di esempio | Bassa (Fase 6) |
