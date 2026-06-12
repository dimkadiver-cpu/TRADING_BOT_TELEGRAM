# Handoff — sessione 2026-06-12 / riconciliazione REST funding + warning + fix `_f`

---

## Cosa è stato fatto

Seguito della sessione "fix funding mai registrato": chiusura delle lacune residue della pipeline funding, dopo verifica che il fix WS funziona in live.

### 0. Verifica live del fix WS (superata)

In `db/ops.sqlite3`: 8 raw events `exec_type='Funding'` (primi alle 08:00 UTC), tutti classificati `FUNDING_SETTLED`, attribuiti alla chain corretta e processati (`DONE`). `cumulative_funding` coincide esattamente con la somma degli `exec_fee`:
- chain 10 ONDOUSDT: `0.01865106 + 0.01912086 + 0.01875024 − 0.03825135 = 0.01827081` ✓ (incluso funding **ricevuto** negativo)
- chain 13 REZUSDT: `0.30452098` ✓

Chain 22 BTCUSDT con funding 0.0 è attesa: aperta alle 19:50 UTC, primo settlement BTC alle 00:00 del 13/06.

### 1. Riconciliazione REST del funding (lacuna principale chiusa)

L'handoff precedente affermava che il percorso REST avrebbe ripreso il funding — **era falso**: `fetch_recent_reduce_trades` filtra `reduceOnly=True` che esclude le funding executions, e nessun percorso REST creava `FUNDING_SETTLED`. Implementato:

- `CcxtBybitAdapter.fetch_recent_funding_executions(symbol, ...)`: REST `fetch_my_trades` con `params={"execType": "Funding"}`, finestra 24h, ritorna `RawFundingExecution` (nuovo modello in `models.py`).
- `ExchangeEventSyncWorker.run_funding_reconciliation()`: per i simboli delle chain OPEN/PARTIALLY_CLOSED, risolve la chain con la stessa logica del WS (side Bybit = lato posizione: Buy→LONG; `resolve_chain_for_fill`, ambiguità → skip con WARNING) e inserisce `FUNDING_SETTLED` con chiave **`fill:{execId}`, identica al WS → dedup automatico** tra i due percorsi.
- Agganciato in `main.py` e `main_linux_server.py`: loop periodico `_run_position_reconciliation_periodically` + riconciliazione di startup (`worker.run_funding_reconciliation()` dopo `run_position_reconciliation()`).

### 2. Warning su funding scartato

- `workers.py::_handle_funding_settled`: WARNING se `trade_chain_id is None` (prima: return silenzioso, importo perso senza traccia).
- `ws_fill_watcher.py`: WARNING se `resolve_chain_for_fill` non risolve la chain per una funding execution (0 o >1 chain aperte).

### 3. Fix bug latente `NameError: _f` (scoperto durante il lavoro)

Commit `509ae2e` aveva aggiunto `fee=_f(...)` in `fetch_recent_reduce_trades` **senza importare `_f`** → `NameError` su ogni trade, inghiottito dal `except Exception` → la funzione ritornava sempre `[]` in produzione (fill price recovery della position reconciliation rotto). Fix: import `_f` e `_ms_to_iso` da `status_mapper`. Un test pre-esistente rosso (`test_fetch_recent_reduce_trades_returns_reduce_only_fills`) è tornato verde.

---

## File toccati

```
src/runtime_v2/execution_gateway/event_sync.py                       ← run_funding_reconciliation()
src/runtime_v2/execution_gateway/adapters/ccxt_bybit/adapter.py      ← fix import _f; fetch_recent_funding_executions()
src/runtime_v2/execution_gateway/models.py                           ← RawFundingExecution
src/runtime_v2/execution_gateway/adapters/fake.py                    ← simulate/fetch funding executions
src/runtime_v2/lifecycle/workers.py                                  ← WARNING funding senza chain
src/runtime_v2/execution_gateway/adapters/ccxt_bybit/ws_fill_watcher.py ← WARNING funding non attribuibile
main.py, main_linux_server.py                                        ← wiring periodico + startup
tests/runtime_v2/lifecycle/test_workers.py                           ← +1 test warning
tests/runtime_v2/execution_gateway/test_bybit_ws_fill_watcher.py     ← +1 test warning
tests/runtime_v2/execution_gateway/test_event_sync.py                ← +6 test run_funding_reconciliation
tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py   ← +3 test (regressione _f + funding fetch)
docs/AUDIT.md                                                        ← nuova entry 2026-06-12
```

---

## Stato attuale

```
pytest tests/runtime_v2/ -q
→ 1230 passed, 4 failed, 6 skipped
```

I 4 falliti sono pre-esistenti e non correlati: `test_live_trading_blocked`, `test_live_trading_gate_does_not_cancel_chain`, `test_ac7_live_trading_blocked` (live trading gate), `test_known_symbol_passes_check` (canonicalizzazione BTCUSDT vs BTC/USDT). **Zero regressioni; un test pre-esistente in più ora passa grazie al fix `_f`.**

TDD seguito: 12 test scritti prima (rossi), implementazione, verdi.

---

## Rischi aperti

- **Finestra REST 24h**: downtime >24h perde il funding più vecchio della finestra.
- **Ambiguità multi-chain** (stesso symbol+side): funding non attribuito, ora visibile nei log. Ripartizione pro-quota solo se i log mostrano che accade.
- **Verifica live del percorso REST mancante**: validato a unit level; conferma al prossimo restart con posizione aperta attraverso un timestamp di funding (controllare log `FUNDING_SETTLED from funding reconciliation` o assenza di duplicati).
- **4 test pre-esistenti rossi** (live gate + canonicalizzazione), fuori scope.

---

## Prossimo prompt suggerito

> Riavvia il bot e verifica nei log la riconciliazione funding di startup (nessun duplicato in `ops_exchange_events` grazie alla chiave `fill:{execId}`). Alla prossima chiusura di posizione di ONDO o REZ controlla che il report POSITION CLOSED mostri la riga `Funding` valorizzata e il Net PnL coerente. Valuta poi i 4 test pre-esistenti rossi (live trading gate, canonicalizzazione simboli).
