# Fase A Processor Autonomy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rendere `LifecycleEventProcessor` autonomo nel determinare quando un `TP_FILLED` chiude davvero la posizione, così che il risultato lifecycle e le notifiche finali non dipendano più dal flag `is_final` scritto nei payload exchange.

**Architecture:** Il delta reale è localizzato in `src/runtime_v2/lifecycle/event_processor.py`. I producer exchange (`event_sync.py`, `repositories.py`) già propagano `fill_price`, `filled_qty`, `exec_fee` e, sul path REST TP, anche `is_final`; il bug residuo è che `_process_tp_filled()` continua a fidarsi di `payload["is_final"]` invece di derivare il risultato dallo stato chain (`open_position_qty - filled_qty`). La correzione è quindi owner-layer nel processor, con regressioni focalizzate nei test lifecycle.

**Tech Stack:** Python 3.12, sqlite3, pytest, runtime_v2 lifecycle processor

---

## Repository Grounding

Stato verificato nel codice attuale:

- `src/runtime_v2/execution_gateway/event_sync.py` già salva `fill_price`, `filled_qty`, `exec_fee`, `closed_size`; per i TP REST aggiunge anche `is_final`.
- `src/runtime_v2/execution_gateway/repositories.py` già inoltra nel path WS `fill_price`, `filled_qty`, `closed_size`, `exec_fee`, `tp_level`.
- `src/runtime_v2/lifecycle/event_processor.py` ha già `_normalized_fill_payload()` e normalizza correttamente i payload fill, ma `_process_tp_filled()` usa ancora:
  - `is_final = bool(payload.get("is_final", False))`
  - `new_state = "CLOSED" if is_final else "PARTIALLY_CLOSED"`
  - `new_open = 0.0 if is_final else max(chain.open_position_qty - fill_qty, 0.0)`
- `tests/runtime_v2/lifecycle/test_event_processor.py` copre già la preservazione di `fill_price` / `filled_qty` / `exec_fee`, ma non protegge ancora il caso chiave: payload `is_final=False` con chiusura reale della posizione.

Conclusione: la spec di design è più ampia dello stato reale necessario oggi. Il piano va ristretto al delta effettivo.

---

## File Map

| File | Responsibility |
|---|---|
| `src/runtime_v2/lifecycle/event_processor.py` | Derivare `is_final` da `chain.open_position_qty` e `filled_qty` invece che dal payload exchange. |
| `tests/runtime_v2/lifecycle/test_event_processor.py` | Copertura regressione per WS, override del hint REST e guardia sul caso parziale. |

---

## Acceptance Contract

**Done means:**
- un `TP_FILLED` che porta `open_position_qty` a zero chiude la chain anche se il payload arriva con `is_final=False` o senza `is_final`;
- il lifecycle event `TP_FILLED` persistito dal processor espone `is_final=True` quando la chiusura è reale;
- un `TP_FILLED` parziale continua a produrre `PARTIALLY_CLOSED` e `is_final=False`;
- la logica BE / auto-cancel già esistente non cambia comportamento fuori da questa decisione;
- nessun cambio schema, nessun cambio migration, nessun cambio producer-side obbligatorio.

**Primary signal:**
- `C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest tests\runtime_v2\lifecycle\test_event_processor.py -q`

**Secondary signals:**
- `C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest tests\runtime_v2\control_plane\test_outbox_writer.py -q`
- review statica dei punti che consumano `ev.get("is_final")` nel projection layer CLEAN_LOG

---

## Task 1: Blindare il bug con test red mirati

**Files:**
- Modify: `tests/runtime_v2/lifecycle/test_event_processor.py`

- [ ] **Step 1: Add failing test for WS-like final TP without `is_final`**

Append a test that simulates the websocket path contract: no `is_final` in payload, but full remaining quantity closed.

```python
def test_tp_filled_without_is_final_closes_when_fill_consumes_open_position():
    chain = _make_chain(entry_avg_price=65000.0)
    chain = chain.model_copy(update={
        "open_position_qty": 0.002,
        "closed_position_qty": 0.0,
        "lifecycle_state": "OPEN",
    })
    event = _make_exchange_event(
        event_type="TP_FILLED",
        payload={
            "tp_level": 2,
            "fill_price": 68000.0,
            "filled_qty": 0.002,
            "exec_fee": 1.10,
        },
    )

    result = _make_processor().process(event, chain, [])

    assert result.new_lifecycle_state == "CLOSED"
    assert result.new_open_position_qty == 0.0
    tp_event = next(e for e in result.lifecycle_events if e.event_type == "TP_FILLED")
    payload = json.loads(tp_event.payload_json)
    assert payload["is_final"] is True
```

- [ ] **Step 2: Add failing test for REST hint override**

Protect against stale or wrong upstream hint:

```python
def test_tp_filled_overrides_false_is_final_hint_when_position_is_actually_closed():
    chain = _make_chain(entry_avg_price=65000.0)
    chain = chain.model_copy(update={
        "open_position_qty": 0.002,
        "closed_position_qty": 0.0,
        "lifecycle_state": "PARTIALLY_CLOSED",
    })
    event = _make_exchange_event(
        event_type="TP_FILLED",
        payload={
            "tp_level": 2,
            "is_final": False,
            "fill_price": 68000.0,
            "filled_qty": 0.002,
        },
    )

    result = _make_processor().process(event, chain, [])

    assert result.new_lifecycle_state == "CLOSED"
    tp_event = next(e for e in result.lifecycle_events if e.event_type == "TP_FILLED")
    payload = json.loads(tp_event.payload_json)
    assert payload["is_final"] is True
```

- [ ] **Step 3: Add guard test for genuine partial fill**

Keep the non-final path honest:

```python
def test_tp_filled_remains_partial_when_open_position_stays_positive():
    chain = _make_chain(entry_avg_price=65000.0)
    chain = chain.model_copy(update={
        "open_position_qty": 0.010,
        "closed_position_qty": 0.0,
        "lifecycle_state": "OPEN",
    })
    event = _make_exchange_event(
        event_type="TP_FILLED",
        payload={
            "tp_level": 1,
            "is_final": True,
            "fill_price": 68000.0,
            "filled_qty": 0.002,
        },
    )

    result = _make_processor().process(event, chain, [])

    assert result.new_lifecycle_state == "PARTIALLY_CLOSED"
    assert result.new_open_position_qty == pytest.approx(0.008)
    tp_event = next(e for e in result.lifecycle_events if e.event_type == "TP_FILLED")
    payload = json.loads(tp_event.payload_json)
    assert payload["is_final"] is False
```

- [ ] **Step 4: Run the focused tests and verify failure**

Run:

```powershell
C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest tests\runtime_v2\lifecycle\test_event_processor.py -k "without_is_final_closes or overrides_false_is_final_hint or remains_partial_when_open_position_stays_positive" -q
```

Expected: FAIL on current code because `_process_tp_filled()` trusts payload `is_final`.

---

## Task 2: Spostare la decisione `is_final` nel layer owner

**Files:**
- Modify: `src/runtime_v2/lifecycle/event_processor.py`

- [ ] **Step 1: Replace payload-driven finality with derived finality**

Inside `_process_tp_filled()`, change the computation order so `filled_qty` is read first, then remaining position is derived before `is_final`.

Target shape:

```python
fill_qty = float(payload.get("filled_qty") or 0.0)
new_open = max(chain.open_position_qty - fill_qty, 0.0)
is_final = new_open <= 0.0
new_state: LifecycleState = "CLOSED" if is_final else "PARTIALLY_CLOSED"
```

Delete the current dependency on:

```python
is_final = bool(payload.get("is_final", False))
new_open = 0.0 if is_final else ...
```

- [ ] **Step 2: Keep lifecycle payload projection aligned**

Do not change the outward event shape. The emitted `TP_FILLED` lifecycle event must continue to include:

```python
{
    "tp_level": tp_level,
    "is_final": is_final,
    **fill_payload,
}
```

The only change is that `is_final` is now authoritative because it is processor-derived.

- [ ] **Step 3: Check adjacent handlers for consistency**

Re-read, but do not modify unless a real inconsistency is found:

- `_process_sl_filled()` already closes unconditionally.
- `_process_close_full_filled()` already closes unconditionally.
- `_process_close_partial_filled()` already derives `new_open` then closes only when `new_open <= 0`.
- `_process_pending_entry_cancelled_confirmed()` is unrelated to TP finality.

Expected outcome: no extra code changes required.

---

## Task 3: Re-run lifecycle and projection validation

**Files:**
- No code changes expected if Task 2 is correct

- [ ] **Step 1: Run full lifecycle processor suite**

```powershell
C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest tests\runtime_v2\lifecycle\test_event_processor.py -q
```

Expected: PASS.

- [ ] **Step 2: Run outbox writer regression smoke**

`CLEAN_LOG` uses lifecycle payloads, so verify the downstream projection still accepts the event contract:

```powershell
C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest tests\runtime_v2\control_plane\test_outbox_writer.py -q
```

Expected: PASS.

- [ ] **Step 3: Optional targeted grep review**

Search downstream consumers of `is_final` and confirm they benefit from the fix without further edits:

```powershell
rg -n "is_final" src\runtime_v2\control_plane src\runtime_v2\lifecycle tests\runtime_v2
```

Expected: consumers read the lifecycle event payload; no producer contract expansion required.

---

## Explicit Non-Scope

These were mentioned in the design spec but are not the active delta in the repository snapshot I verified:

- changing `src/runtime_v2/execution_gateway/repositories.py` to add fill payload fields;
- changing `src/runtime_v2/execution_gateway/event_sync.py` to add `exec_fee` / `closed_size`;
- introducing new migrations, schema changes, or outbox changes;
- modifying `outbox_writer.py`, `workers.py`, or `notification_dispatcher.py`.

If any of those areas fail validation during execution, that is new evidence and should trigger a follow-up plan, not silent scope creep inside this one.

---

## End-of-plan verification

- [ ] `C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest tests\runtime_v2\lifecycle\test_event_processor.py -q`
- [ ] `C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest tests\runtime_v2\control_plane\test_outbox_writer.py -q`
- [ ] `rg -n "is_final" src\runtime_v2 tests\runtime_v2`

---

## Self-Review

**Spec coverage:** questo piano copre il principio centrale della spec Fase A: il processor non deve fidarsi di flag esterni per determinare la chiusura finale del TP. Le parti di enrichment payload citate nella spec risultano già implementate nel repository e sono quindi escluse per evitare lavoro duplicato.

**Owner-layer discipline:** il fix è nel layer corretto. Non aggiunge fallback nei formatter o nei worker e non compensa il bug a valle.

**Minimal sufficient change:** un solo file di produzione, un solo file di test, nessun cambio schema, nessuna nuova astrazione.
