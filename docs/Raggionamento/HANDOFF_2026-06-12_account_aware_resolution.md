# Handoff — sessione 2026-06-12 / risoluzione chain account-aware

---

## Cosa è stato fatto

Fix dell'ambiguità multi-chain cross-account nella risoluzione symbol+side, emersa analizzando l'interazione tra funding e routing `per_trader_subaccount`.

### Problema

La risoluzione chain (`resolve_chain_for_fill` e affini) filtrava solo per symbol+side, ignorando `account_id`. Con due account attivi:

1. **Funding/fill scartati senza motivo**: chain LONG sullo stesso simbolo su `main` e `account_nuovo` → ogni watcher riceve il proprio evento (attribuzione determinabile), ma la query trova 2 chain → "ambiguo" → scartato.
2. **Chiusure spurie (il rischio peggiore)**: `run_position_reconciliation` del worker `main` vedeva anche le chain di `account_nuovo`, chiedeva la posizione al *proprio* adapter → qty=0 → avrebbe sintetizzato `CLOSE_FULL_FILLED` su una chain la cui posizione vive sull'altro subaccount.
3. **Trade-based/protective reconciliation**: stessi problemi di attribuzione cross-account.

### Fix

Filtro `account_id` opzionale (None = legacy) lungo tutta la catena:

- `GatewayCommandRepository`: `get_open_chains_for_symbol(symbol, side, account_id=None)`, `resolve_chain_for_fill(..., account_id=None)`, `get_open_chains_with_tps(account_id=None)`.
- `BybitWsFillWatcher`: nuovo param `account_id`, usato nella risoluzione TP/SL e funding (e nei WARNING).
- `ExchangeEventSyncWorker`: `_get_open_chains` filtra per `self._execution_account_id`; trade-based, protective e funding reconciliation scoped allo stesso modo.
- `main.py` / `main_linux_server.py`: ogni watcher per-account riceve `account_id=account_id` (= `execution_account_id` del routing).

**Mappatura verificata**: `execution_account_id` in `config/execution.yaml` = `ops_trade_chains.account_id` (`main`, `account_nuovo`) — confermato su config e DB live.

**Caso irriducibile rimasto**: due segnali dello stesso trader su stesso simbolo+lato+account — Bybit netta la posizione, un solo evento funding, ambiguità genuina → skip con WARNING. Ripartizione pro-quota (`exec_fee × qty_i/Σqty`) implementabile in futuro se i log mostrano il caso.

---

## File toccati

```
src/runtime_v2/execution_gateway/repositories.py       ← filtro account su 3 metodi
src/runtime_v2/execution_gateway/adapters/ccxt_bybit/ws_fill_watcher.py ← param account_id
src/runtime_v2/execution_gateway/event_sync.py          ← riconciliazioni scoped per account
main.py, main_linux_server.py                           ← account_id al watcher
tests/runtime_v2/execution_gateway/test_event_sync.py   ← +4 test (repo, funding cross-acc,
                                                           position recon, trade-based) + helper account_id
tests/runtime_v2/execution_gateway/test_bybit_ws_fill_watcher.py ← +1 test watcher account-aware
tests/runtime_v2/execution_gateway/test_fill_identity_dedupe.py  ← fixture: colonna account_id
                                                           (schema artigianale era driftato)
tests/runtime_v2/execution_gateway/test_ws_fill_watcher.py ← mock assertion aggiornata (3° arg)
docs/AUDIT.md                                           ← nuova entry
```

---

## Stato attuale

```
pytest tests/runtime_v2/ -q
→ 1241 passed, 4 failed, 6 skipped
```

I 4 falliti sono gli stessi pre-esistenti delle sessioni precedenti (live trading gate ×3, `test_known_symbol_passes_check` BTCUSDT vs BTC/USDT). **Zero regressioni.** TDD: 5 test rossi prima dell'implementazione, tutti verdi dopo.

---

## Rischi aperti

- **Drift schema fixture test**: `test_fill_identity_dedupe.py` crea tabelle a mano invece di usare `db/ops_migrations` — il drift aveva già perso la colonna `account_id`. Candidato a refactor (usare `_apply_migrations` come gli altri file).
- **Ambiguità same-account** irriducibile (vedi sopra) — monitorare i WARNING `not attributable`.
- **Watcher costruito senza `account_id`** → risoluzione account-blind legacy (intenzionale per retrocompatibilità).
- **4 test pre-esistenti rossi**, fuori scope.

---

## Prossimo prompt suggerito

> Riavvia il bot e verifica nei log che i watcher partano con il proprio account_id e che la riconciliazione startup non produca eventi spuri. Quando `account_nuovo` avrà credenziali reali, testa il caso due chain stesso simbolo+lato su account diversi: il funding deve finire sulla chain giusta di ciascun account. Valuta poi il refactor delle fixture di test_fill_identity_dedupe.py verso le migration reali e i 4 test pre-esistenti rossi.
