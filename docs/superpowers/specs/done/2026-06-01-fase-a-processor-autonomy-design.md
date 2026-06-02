# Fase A — Processor Autonomy Design

**Data:** 2026-06-01
**Scope:** Fix operativo + solidità del lifecycle event processor
**Rischio:** Basso — nessun cambio schema, nessun cambio DB
**Dipendenze:** Nessuna — deployabile standalone

---

## Problema

`LifecycleEventProcessor` dipende da flag nel payload di `ops_exchange_events` che vengono
computati in modo diverso nei due path di ingest:

- **Path REST** (`event_sync._save_fill_event`): calcola `is_final = count_active_tps <= 1`
- **Path WS** (`repositories.insert_raw_and_classified`): non include `is_final`

Il processor legge `payload.get("is_final", False)` — quindi per qualsiasi fill WS,
`is_final` è sempre `False`, con queste conseguenze:

1. Chain rimane `PARTIALLY_CLOSED` anche quando la posizione è 0
2. La notifica è sempre `TP_FILLED` (mai `TP_FILLED_FINAL`)
3. Il blocco `final_result` (PnL netto totale, fee totali) non viene mai mostrato
4. `cumulative_gross_pnl` e `cumulative_fees` accumulati correttamente nel DB
   ma non raggiungono mai l'utente

---

## Principio del fix

Il processor sa già tutto il necessario per derivare `is_final` dalla chain state.
Non deve fidarsi di un flag esterno che dipende dal path di ingest.

**Regola:** `is_final = (open_position_qty - fill_qty) <= 0`

Questo vale sempre, indipendentemente da WS o REST, e rispecchia la semantica reale:
"la posizione è chiusa quando la quantità residua è zero."

---

## Modifiche

### `src/runtime_v2/lifecycle/event_processor.py`

**`_process_tp_filled()`** — unica modifica sostanziale:

```python
# PRIMA
is_final = bool(payload.get("is_final", False))
new_open = 0.0 if is_final else max(chain.open_position_qty - fill_qty, 0.0)
new_state: LifecycleState = "CLOSED" if is_final else "PARTIALLY_CLOSED"

# DOPO
fill_qty = float(payload.get("filled_qty") or 0.0)
new_open = max(chain.open_position_qty - fill_qty, 0.0)
is_final = new_open <= 0
new_state: LifecycleState = "CLOSED" if is_final else "PARTIALLY_CLOSED"
```

Il valore `is_final` calcolato viene scritto nel payload del lifecycle event
(`"is_final": is_final`) — questo è già il comportamento attuale, quindi
`outbox_writer.project_clean_log_for_chain` vede il valore corretto senza modifiche.

**Altri handler da verificare** (nessuna modifica prevista, solo verifica):

- `_process_sl_filled`: usa `chain.open_position_qty` direttamente — OK
- `_process_close_full_filled`: chiude sempre → `CLOSED` — OK
- `_process_close_partial_filled`: calcola `new_open = max(open - fill, 0)` e poi
  `"CLOSED" if new_open <= 0 else "PARTIALLY_CLOSED"` — già corretto ✅
- `_process_pending_entry_cancelled_confirmed`: usa `chain.open_position_qty > 0` — OK

### `src/runtime_v2/execution_gateway/repositories.py`

`insert_raw_and_classified()` — nessuna modifica necessaria.

Il payload WS continua a non includere `is_final`. Non è più necessario perché
il processor lo deriva autonomamente. Lasciarlo assente è corretto: documenta che
il path WS non computa questo campo.

### `src/runtime_v2/execution_gateway/event_sync.py`

`_save_fill_event()` — il campo `is_final` nel payload REST resta, ma diventa
un hint opzionale che il processor ignora. Può essere rimosso in Fase B quando
il contratto payload viene formalizzato. Per ora: nessuna modifica.

---

## Cascade effect atteso

Dopo la modifica, per un TP finale via WS:

```
WS TP_FILLED event (is_final assente nel payload)
  → _process_tp_filled()
    → fill_qty = X
    → new_open = chain.open_position_qty - X = 0.0
    → is_final = True          ← derivato dalla chain
    → new_state = "CLOSED"
    → lifecycle event: {tp_level, is_final: True, fill_price, filled_qty, exec_fee, ...}
  → _persist_result()
    → ops_trade_chains.lifecycle_state = "CLOSED"
    → ops_lifecycle_events: TP_FILLED con is_final=True
    → _accumulate_pnl_for_events() → cumulative_gross_pnl e cumulative_fees aggiornati
    → project_clean_log_for_chain()
      → ev.get("is_final") = True
      → notification_type = "TP_FILLED_FINAL"
      → _build_payload(..., notification_type="TP_FILLED_FINAL")
        → final_result = _final_result(gross_pnl=cumulative_gross_pnl, fees=cumulative_fees, ...)
      → notifica con PnL netto totale e fee totali ✅
```

---

## Cosa non cambia

- Schema DB: nessun cambio
- Payload `ops_exchange_events`: nessun cambio
- Path REST: `is_final` continua a essere calcolato ma non è più usato dal processor
- `outbox_writer.py`: nessun cambio
- `workers.py`: nessun cambio
- Tutti gli altri handler del processor: nessun cambio

---

## Test

**Test da aggiungere** in `tests/` (unit, nessun DB reale necessario):

1. `TP_FILLED` WS su chain con `open_position_qty = fill_qty` →
   `new_lifecycle_state = "CLOSED"`, `is_final = True` nel lifecycle event
2. `TP_FILLED` WS su chain con `open_position_qty > fill_qty` →
   `new_lifecycle_state = "PARTIALLY_CLOSED"`, `is_final = False`
3. `TP_FILLED` REST con `is_final=False` nel payload ma `new_open = 0` →
   processor overrides → `is_final = True` (regression guard)

**Test di regressione** da eseguire prima del deploy:
```
pytest tests/
```

---

## Effort stimato

2-3 ore. Una modifica di 3 righe in `event_processor.py` + test.
