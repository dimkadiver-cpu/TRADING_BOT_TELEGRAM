# Handoff — trader_devos_crypto anomaly sweep

**Data:** 2026-06-23  
**Branch:** feat/account-snapshots-pnl-integrity  

---

## Cosa fatto

### 1. Fix double-cancel `CANCEL_PENDING_ENTRY` (bug `invalid_order`)

**Root cause:** Path A (`entry_gate._apply_close_full`) e Path B (`event_processor._process_close_full_filled`) emettono entrambi `CANCEL_PENDING_ENTRY` per la stessa entry, con idempotency key **diverse** → il DB non blocca il duplicato → la seconda chiamata a Bybit riceve `invalid_order`.

**Fix:** `src/runtime_v2/lifecycle/cancel_expander.py` — `load_pending_entry_client_order_ids` ora filtra via NOT IN subquery le entry già puntate da un CANCEL_PENDING_ENTRY con `status IN ('PENDING','SENT','ACK','DONE')`.

**Test:** `tests/runtime_v2/lifecycle/test_cancel_expander.py` — +5 test, 12/12 pass.

### 2. Fix floating point residuo TP final — chain 9 INTCUSDT SHORT

**Root cause:** Sottrazioni incrementali `max(open_qty - 0.4, 0.0)` × 8 lasciano residuo `5.55e-16 > 0` → `is_final = new_open <= 0.0` mai True → catena bloccata in PARTIALLY_CLOSED.

**Fix:** `src/runtime_v2/lifecycle/event_processor.py` ≈ line 542: `is_final = new_open < 1e-9`; quando `is_final=True`, `new_open_position_qty` forzato a `0.0`.

**Test:** `tests/runtime_v2/lifecycle/test_event_processor.py` — +1 test residuo, 75/75 pass.

---

## File toccati

| File | Tipo |
|------|------|
| `src/runtime_v2/lifecycle/cancel_expander.py` | Fix produzione |
| `tests/runtime_v2/lifecycle/test_cancel_expander.py` | Test |
| `src/runtime_v2/lifecycle/event_processor.py` | Fix produzione |
| `tests/runtime_v2/lifecycle/test_event_processor.py` | Test |
| `docs/AUDIT.md` | Doc aggiornata |

---

## Stato attuale

Tutti i test passano. I due bug critici sono fixati.

**Non fixato (identificato ma non richiesto dall'utente):**

- **Chain 38 BTCUSDT REBUILD_PARTIAL_TPS FAILED** — `PostFillProtectionRebuilder` divide `filled_entry_qty / n_total_tps` senza verificare `tp_qty >= min_order_size`. Con fill 0.002 BTC ÷ 8 TPs = 0.00025 BTC < minimo Bybit 0.001 BTC → errore `10001`.
- **`min_order_size = 1e-06` per BTCUSDT (demo)** — Bybit demo restituisce `lotSizeFilter.qtyStep = "0.000001"` (`basePrecision`) invece del vero minimo `0.001`. La catena di fallback `minOrderQty or qtyStep` pesca il valore sbagliato. Il vero minimo è 0.001 BTC (confermato dall'errore Bybit 10001 in chain 38).

---

## Rischi aperti

1. **REBUILD_PARTIAL_TPS senza guardia `min_order_size`** — può fallire per qualsiasi simbolo con fill piccoli. Fix da implementare: in `PostFillProtectionRebuilder.build()`, verificare `tp_qty >= min_order_size` e saltare/loggare se sotto soglia.

2. **`min_order_size` Bybit demo inaffidabile per BTCUSDT** — il valore in DB (`1e-06`) non riflette il reale minimo exchange (`0.001`). La fonte è `ccxt_bybit:demo`, server Bybit demo. In produzione questo valore sarebbe corretto.

3. **Idempotency key Path A vs Path B ancora eterogenea** — il fix attuale è difensivo (filtra a monte le entry già coperte). Se in futuro si aggiunge un terzo percorso di cancel, serve allineare le key o estendere il filtro.

---

## Prossimo prompt suggerito

```
Implementa la guardia min_order_size nel PostFillProtectionRebuilder:
prima di emettere i comandi TP in REBUILD_PARTIAL_TPS, verifica che
tp_qty >= min_order_size (da ops_market_snapshots). Se sotto soglia,
logga un warning e salta l'emissione (o consolida in meno TPs).
Usa TDD.
```
