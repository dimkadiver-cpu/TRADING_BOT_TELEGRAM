# Notification Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate notification delays (TP immediati, UPDATE immediati) e arricchire i messaggi UPDATE con dati old/new + riassuntivo multi-chain con link.

**Architecture:** Rimozione dell'AggregationWorker; ogni notifica va in outbox senza delay; i payload degli eventi `TELEGRAM_UPDATE_ACCEPTED` vengono arricchiti in `entry_gate._apply_*`; `_persist_update` scrive un `MULTI_CHAIN_SUMMARY` con 3s delay quando ≥2 chain sono colpite; il dispatcher arricchisce i link al momento dell'invio leggendo `ops_clean_log_tracking`.

**Tech Stack:** Python 3.12, SQLite, pytest, pydantic v2

---

## File Map

| File | Modifica |
|---|---|
| `src/runtime_v2/control_plane/outbox_writer.py` | Rimuovi delay TP/UPDATE; aggiungi delay 3s per MULTI_CHAIN_SUMMARY; rimuovi `_agg_group` |
| `src/runtime_v2/control_plane/clean_log.py` | Rimuovi `_tp_batch_filled`; aggiungi `_multi_chain_summary` |
| `src/runtime_v2/control_plane/aggregation_worker.py` | **Rimosso** |
| `src/runtime_v2/control_plane/bootstrap.py` | Rimuovi AggregationWorker |
| `src/runtime_v2/control_plane/notification_dispatcher.py` | Aggiungi `_get_clean_log_last`; arricchisci link MULTI_CHAIN_SUMMARY |
| `src/runtime_v2/lifecycle/entry_gate.py` | Arricchisci `_apply_move_to_be`, `_apply_cancel_pending`, `_apply_close_partial`; aggiorna `_write_update_clean_log`; aggiungi `_write_multi_chain_summary`; chiama da `_persist_update` |
| `tests/runtime_v2/control_plane/test_aggregation_worker.py` | **Rimosso** |
| `tests/runtime_v2/control_plane/test_outbox_writer.py` | Aggiorna test delay |
| `tests/runtime_v2/control_plane/test_clean_log_formatter.py` | Aggiungi test MULTI_CHAIN_SUMMARY |
| `tests/runtime_v2/lifecycle/test_entry_gate.py` | Aggiungi test payload arricchiti e multi_chain_summary |

---

## Task 1: Rimuovi TP delay e TP_BATCH_FILLED

**Files:**
- Modify: `src/runtime_v2/control_plane/outbox_writer.py`
- Modify: `src/runtime_v2/control_plane/clean_log.py`
- Modify: `src/runtime_v2/control_plane/aggregation_worker.py`
- Modify: `tests/runtime_v2/control_plane/test_outbox_writer.py`

- [ ] **Step 1: Scrivi il test failing per assenza delay TP**

In `tests/runtime_v2/control_plane/test_outbox_writer.py`, aggiungi alla fine:

```python
def test_tp_filled_has_no_send_after_delay():
    from src.runtime_v2.control_plane.outbox_writer import _send_after_for
    from datetime import datetime, timezone
    result = _send_after_for("TP_FILLED")
    now = datetime.now(timezone.utc).isoformat()
    # deve essere within 1 secondo dal now, non 30s nel futuro
    assert result <= now or result[:19] == now[:19]


def test_tp_filled_final_has_no_send_after_delay():
    from src.runtime_v2.control_plane.outbox_writer import _send_after_for
    from datetime import datetime, timezone
    result = _send_after_for("TP_FILLED_FINAL")
    now = datetime.now(timezone.utc).isoformat()
    assert result <= now or result[:19] == now[:19]
```

- [ ] **Step 2: Verifica che il test fallisce**

```
pytest tests/runtime_v2/control_plane/test_outbox_writer.py::test_tp_filled_has_no_send_after_delay -v
```

Expected: FAIL

- [ ] **Step 3: Rimuovi il delay TP da `_send_after_for` in `outbox_writer.py`**

Attuale (`outbox_writer.py:100-105`):
```python
def _send_after_for(notification_type: str) -> str:
    if notification_type in {"UPDATE_DONE", "UPDATE_PARTIAL", "UPDATE_REJECTED"}:
        return _iso_after(20)
    if notification_type in {"TP_FILLED", "TP_FILLED_FINAL"}:
        return _iso_after(30)
    return _now()
```

Nuovo:
```python
def _send_after_for(notification_type: str) -> str:
    if notification_type in {"UPDATE_DONE", "UPDATE_PARTIAL", "UPDATE_REJECTED"}:
        return _iso_after(20)
    return _now()
```

- [ ] **Step 4: Rimuovi TP da `_agg_group` in `outbox_writer.py`**

Attuale (`outbox_writer.py:108-115`):
```python
def _agg_group(notification_type: str, chain_id: int | None, payload: dict) -> str | None:
    if chain_id is None:
        return None
    if notification_type in {"TP_FILLED", "TP_FILLED_FINAL"}:
        return f"{chain_id}:tp_batch"
    if notification_type in {"UPDATE_DONE", "UPDATE_PARTIAL", "UPDATE_REJECTED"}:
        return f"{chain_id}:{payload.get('source_message_id') or 'update_batch'}"
    return None
```

Nuovo:
```python
def _agg_group(notification_type: str, chain_id: int | None, payload: dict) -> str | None:
    if chain_id is None:
        return None
    if notification_type in {"UPDATE_DONE", "UPDATE_PARTIAL", "UPDATE_REJECTED"}:
        return f"{chain_id}:{payload.get('source_message_id') or 'update_batch'}"
    return None
```

- [ ] **Step 5: Rimuovi `_tp_batch_filled` e il suo case da `clean_log.py`**

Rimuovi la funzione `_tp_batch_filled` (righe 421-442) e il case in `format_clean_log`:
```python
# rimuovi questa riga:
    if notification_type == "TP_BATCH_FILLED":
        return _tp_batch_filled(payload)
```

- [ ] **Step 6: Rimuovi `_aggregate_tp_batches` da `aggregation_worker.py`**

In `aggregation_worker.py`, rimuovi il metodo `_aggregate_tp_batches` (righe 34-134) e la sua chiamata in `run_once`:
```python
# rimuovi questa riga da run_once:
            created += self._aggregate_tp_batches(conn)
```

- [ ] **Step 7: Rimuovi i test TP batch da `test_aggregation_worker.py`**

Elimina tutti i test che iniziano con `test_tp_batch` dal file `tests/runtime_v2/control_plane/test_aggregation_worker.py`.

- [ ] **Step 8: Esegui i test e verifica che passano**

```
pytest tests/runtime_v2/control_plane/test_outbox_writer.py tests/runtime_v2/control_plane/test_aggregation_worker.py -v
```

Expected: tutti PASS

- [ ] **Step 9: Commit**

```bash
git add src/runtime_v2/control_plane/outbox_writer.py \
        src/runtime_v2/control_plane/clean_log.py \
        src/runtime_v2/control_plane/aggregation_worker.py \
        tests/runtime_v2/control_plane/test_outbox_writer.py \
        tests/runtime_v2/control_plane/test_aggregation_worker.py
git commit -m "feat: remove TP_BATCH_FILLED aggregation — TP fills are now immediate"
```

---

## Task 2: Arricchisci `_apply_move_to_be` in entry_gate

**Files:**
- Modify: `src/runtime_v2/lifecycle/entry_gate.py:1037-1045`
- Test: `tests/runtime_v2/lifecycle/test_entry_gate.py`

- [ ] **Step 1: Scrivi il test failing**

Aggiungi in `tests/runtime_v2/lifecycle/test_entry_gate.py`:

```python
def test_apply_move_to_be_emits_telegram_update_accepted_with_prices(ops_db, parser_db):
    """_apply_move_to_be deve emettere TELEGRAM_UPDATE_ACCEPTED (non BE_MOVE_REQUESTED)
    con old_sl_price, new_sl_price e is_breakeven=True nel payload."""
    import json
    from src.runtime_v2.lifecycle.entry_gate import LifecycleEntryGate
    from tests.runtime_v2.lifecycle.test_entry_gate import (
        _make_enriched_update, _seed_open_chain,
    )

    chain = _seed_open_chain(
        ops_db,
        chain_id=99,
        entry_avg_price=50000.0,
        current_stop_price=49000.0,
    )
    enriched = _make_enriched_update(action_type="SET_STOP", target_type="ENTRY")
    gate = _make_gate(ops_db)

    result = gate._apply_move_to_be(enriched, chain, active_commands=[])

    accepted = [e for e in result.lifecycle_events if e.event_type == "TELEGRAM_UPDATE_ACCEPTED"]
    assert len(accepted) == 1, "deve emettere esattamente 1 TELEGRAM_UPDATE_ACCEPTED"
    p = json.loads(accepted[0].payload_json)
    assert p.get("is_breakeven") is True
    assert p.get("old_sl_price") is not None
    assert p.get("new_sl_price") is not None
    assert p.get("action") == "MOVE_SL_TO_BE"
```

> Nota: `_make_enriched_update`, `_seed_open_chain`, `_make_gate` sono helper già presenti o da aggiungere al file di test (vedi Step 2).

- [ ] **Step 2: Aggiungi gli helper di test se mancanti**

Verifica in `tests/runtime_v2/lifecycle/test_entry_gate.py` che esistano helper per creare una chain OPEN con `entry_avg_price` e `current_stop_price`. Se assenti, aggiungi:

```python
def _seed_open_chain(ops_db, *, chain_id: int, entry_avg_price: float, current_stop_price: float):
    """Inserisce una chain in stato OPEN con avg price e stop price."""
    import sqlite3
    from pathlib import Path
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(ops_db)
    risk = json.dumps({"sl_price": current_stop_price, "fee_profile": None})
    plan = json.dumps({"legs": [], "stop_loss": current_stop_price})
    conn.execute(
        "INSERT INTO ops_trade_chains "
        "(trade_chain_id, source_enrichment_id, canonical_message_id, raw_message_id, "
        " trader_id, account_id, symbol, side, lifecycle_state, entry_mode, "
        " management_plan_json, risk_snapshot_json, plan_state_json, "
        " entry_avg_price, current_stop_price, open_position_qty, "
        " execution_mode, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (chain_id, chain_id, chain_id, chain_id, "trader_a", "acc_1",
         "BTC/USDT", "LONG", "OPEN", "ONE_SHOT",
         '{"be_fee_correction_enabled": false}', risk, plan,
         entry_avg_price, current_stop_price, 0.01,
         "UNIFIED_PLAN", now, now),
    )
    conn.commit()
    conn.close()
    from src.runtime_v2.lifecycle.models import TradeChain
    return TradeChain(
        trade_chain_id=chain_id, source_enrichment_id=chain_id,
        canonical_message_id=chain_id, raw_message_id=chain_id,
        trader_id="trader_a", account_id="acc_1",
        symbol="BTC/USDT", side="LONG",
        lifecycle_state="OPEN", entry_mode="ONE_SHOT",
        management_plan_json='{"be_fee_correction_enabled": false}',
        risk_snapshot_json=risk, plan_state_json=plan,
        entry_avg_price=entry_avg_price,
        current_stop_price=current_stop_price,
        open_position_qty=0.01,
        execution_mode="UNIFIED_PLAN",
    )
```

- [ ] **Step 3: Verifica che il test fallisce**

```
pytest tests/runtime_v2/lifecycle/test_entry_gate.py::test_apply_move_to_be_emits_telegram_update_accepted_with_prices -v
```

Expected: FAIL (event_type è ancora BE_MOVE_REQUESTED)

- [ ] **Step 4: Modifica `_apply_move_to_be` in `entry_gate.py` (riga 1037-1045)**

Sostituisci il blocco `event = LifecycleEvent(...)` alla fine di `_apply_move_to_be`:

```python
        # Prima: BE_MOVE_REQUESTED — ora: TELEGRAM_UPDATE_ACCEPTED visibile in UPDATE_DONE
        old_sl_price = chain.current_stop_price
        if old_sl_price is None:
            try:
                old_sl_price = float(json.loads(chain.risk_snapshot_json or "{}").get("sl_price") or 0) or None
            except Exception:
                pass
        event = LifecycleEvent(
            trade_chain_id=chain_id,
            event_type="TELEGRAM_UPDATE_ACCEPTED",
            source_type="telegram_update",
            source_id=str(cmid),
            payload_json=json.dumps({
                "action": "MOVE_SL_TO_BE",
                "old_sl_price": old_sl_price,
                "new_sl_price": new_stop_price,
                "is_breakeven": True,
            }),
            idempotency_key=f"update_be:{chain_id}:{cmid}",
        )
```

- [ ] **Step 5: Esegui il test**

```
pytest tests/runtime_v2/lifecycle/test_entry_gate.py::test_apply_move_to_be_emits_telegram_update_accepted_with_prices -v
```

Expected: PASS

- [ ] **Step 6: Esegui la suite lifecycle completa**

```
pytest tests/runtime_v2/lifecycle/ -v
```

Expected: tutti PASS (nessuna regressione)

- [ ] **Step 7: Commit**

```bash
git add src/runtime_v2/lifecycle/entry_gate.py \
        tests/runtime_v2/lifecycle/test_entry_gate.py
git commit -m "feat: _apply_move_to_be emits TELEGRAM_UPDATE_ACCEPTED with old/new SL prices"
```

---

## Task 3: Arricchisci `_apply_cancel_pending` e `_apply_close_partial`

**Files:**
- Modify: `src/runtime_v2/lifecycle/entry_gate.py:1132-1138` (close_partial)
- Modify: `src/runtime_v2/lifecycle/entry_gate.py:1177-1184` (cancel_pending)
- Test: `tests/runtime_v2/lifecycle/test_entry_gate.py`

- [ ] **Step 1: Scrivi i test failing**

Aggiungi in `tests/runtime_v2/lifecycle/test_entry_gate.py`:

```python
def test_apply_cancel_pending_includes_cancelled_entries_in_payload():
    """_apply_cancel_pending deve includere cancelled_entries (sequence, price, entry_type)."""
    import json
    plan = json.dumps({"legs": [
        {"leg_id": "1", "sequence": 2, "status": "PENDING", "entry_type": "LIMIT", "price": 92500.0},
        {"leg_id": "2", "sequence": 3, "status": "PENDING", "entry_type": "LIMIT", "price": 91000.0},
    ]})
    chain = _make_chain(lifecycle_state="OPEN", plan_state_json=plan)
    enriched = _make_enriched_update_cancel_pending()
    gate = _make_gate_no_db()

    result = gate._apply_cancel_pending(enriched, chain)

    accepted = [e for e in result.lifecycle_events if e.event_type == "TELEGRAM_UPDATE_ACCEPTED"]
    assert len(accepted) == 1
    p = json.loads(accepted[0].payload_json)
    assert p["action"] == "CANCEL_PENDING"
    entries = p.get("cancelled_entries", [])
    assert len(entries) == 2
    sequences = {e["sequence"] for e in entries}
    assert sequences == {2, 3}


def test_apply_close_partial_includes_close_pct():
    """_apply_close_partial deve includere close_pct nel payload."""
    import json
    chain = _make_chain(lifecycle_state="OPEN", open_position_qty=1.0)
    enriched = _make_enriched_update_close_partial(fraction=0.5)
    gate = _make_gate_no_db()

    result = gate._apply_close_partial(enriched, chain, op=_make_close_partial_op(fraction=0.5))

    accepted = [e for e in result.lifecycle_events if e.event_type == "TELEGRAM_UPDATE_ACCEPTED"]
    assert len(accepted) == 1
    p = json.loads(accepted[0].payload_json)
    assert p["action"] == "CLOSE_PARTIAL"
    assert p.get("close_pct") == 50.0
```

> Usa helper `_make_chain`, `_make_enriched_update_cancel_pending`, ecc. già presenti nel file o da aggiungere come semplici builder con defaults.

- [ ] **Step 2: Verifica che i test falliscono**

```
pytest tests/runtime_v2/lifecycle/test_entry_gate.py::test_apply_cancel_pending_includes_cancelled_entries_in_payload tests/runtime_v2/lifecycle/test_entry_gate.py::test_apply_close_partial_includes_close_pct -v
```

Expected: FAIL

- [ ] **Step 3: Modifica `_apply_cancel_pending` in `entry_gate.py` (riga 1177-1184)**

```python
        # Raccoglie le entry pending da cancellare per arricchire la notifica
        cancelled_entries: list[dict] = []
        try:
            plan_data = json.loads(chain.plan_state_json or "{}")
            cancelled_entries = [
                {
                    "sequence": leg["sequence"],
                    "price": leg.get("price"),
                    "entry_type": leg.get("entry_type", "LIMIT"),
                }
                for leg in plan_data.get("legs", [])
                if leg.get("status") == "PENDING"
            ]
        except Exception:
            pass

        event = LifecycleEvent(
            trade_chain_id=chain_id,
            event_type="TELEGRAM_UPDATE_ACCEPTED",
            source_type="telegram_update",
            source_id=str(cmid),
            payload_json=json.dumps({
                "action": "CANCEL_PENDING",
                "cancelled_entries": cancelled_entries,
            }),
            idempotency_key=f"update_cancel:{chain_id}:{cmid}",
        )
```

- [ ] **Step 4: Modifica `_apply_close_partial` in `entry_gate.py` (riga 1132-1138)**

```python
        event = LifecycleEvent(
            trade_chain_id=chain_id,
            event_type="TELEGRAM_UPDATE_ACCEPTED",
            source_type="telegram_update",
            source_id=str(cmid),
            payload_json=json.dumps({
                "action": "CLOSE_PARTIAL",
                "fraction": fraction,
                "close_pct": round(fraction * 100, 2),
            }),
            idempotency_key=f"update_close_partial:{chain_id}:{cmid}",
        )
```

- [ ] **Step 5: Esegui i test**

```
pytest tests/runtime_v2/lifecycle/test_entry_gate.py::test_apply_cancel_pending_includes_cancelled_entries_in_payload tests/runtime_v2/lifecycle/test_entry_gate.py::test_apply_close_partial_includes_close_pct -v
```

Expected: PASS

- [ ] **Step 6: Suite lifecycle completa**

```
pytest tests/runtime_v2/lifecycle/ -v
```

Expected: tutti PASS

- [ ] **Step 7: Commit**

```bash
git add src/runtime_v2/lifecycle/entry_gate.py \
        tests/runtime_v2/lifecycle/test_entry_gate.py
git commit -m "feat: enrich cancel_pending and close_partial events with price data"
```

---

## Task 4: Popola `changed` in `_write_update_clean_log` + rimuovi UPDATE delay

**Files:**
- Modify: `src/runtime_v2/lifecycle/entry_gate.py:100-156`
- Modify: `src/runtime_v2/control_plane/outbox_writer.py`
- Test: `tests/runtime_v2/control_plane/test_outbox_writer.py`

- [ ] **Step 1: Scrivi il test failing per assenza delay UPDATE**

Aggiungi in `tests/runtime_v2/control_plane/test_outbox_writer.py`:

```python
def test_update_done_has_no_send_after_delay():
    from src.runtime_v2.control_plane.outbox_writer import _send_after_for
    from datetime import datetime, timezone
    result = _send_after_for("UPDATE_DONE")
    now = datetime.now(timezone.utc).isoformat()
    assert result <= now or result[:19] == now[:19]
```

- [ ] **Step 2: Scrivi test per campo `changed` nell'UPDATE_DONE**

In `tests/runtime_v2/control_plane/test_outbox_writer.py`:

```python
def test_update_clean_log_includes_changed_field_for_be_move(ops_db):
    """_write_update_clean_log deve produrre UPDATE_DONE con campo changed popolato
    quando il payload dell'evento contiene is_breakeven=True."""
    import json, sqlite3
    from src.runtime_v2.lifecycle.entry_gate import _write_update_clean_log
    from src.runtime_v2.lifecycle.entry_gate import UpdateChainResult
    from src.runtime_v2.lifecycle.models import LifecycleEvent

    conn = sqlite3.connect(ops_db)
    _seed_chain(conn, chain_id=77, symbol="ETH/USDT", side="LONG")
    conn.commit()

    event = LifecycleEvent(
        trade_chain_id=77,
        event_type="TELEGRAM_UPDATE_ACCEPTED",
        source_type="telegram_update",
        source_id="1",
        payload_json=json.dumps({
            "action": "MOVE_SL_TO_BE",
            "old_sl_price": 3100.0,
            "new_sl_price": 3340.0,
            "is_breakeven": True,
        }),
        idempotency_key="be_test:77:1",
    )
    cr = UpdateChainResult(
        trade_chain_id=77,
        new_lifecycle_state=None,
        new_be_protection_status=None,
        lifecycle_events=[event],
        execution_commands=[],
    )

    with conn:
        _write_update_clean_log(conn, cr, canonical_message_id=1, link=None)

    row = conn.execute(
        "SELECT payload_json FROM ops_notification_outbox WHERE notification_type='UPDATE_DONE'"
    ).fetchone()
    conn.close()
    assert row is not None
    p = json.loads(row[0])
    changed = p.get("changed", [])
    assert any(c.get("field") == "SL" and c.get("note") == "BE" for c in changed), \
        f"campo changed atteso con SL BE, trovato: {changed}"
```

- [ ] **Step 3: Verifica che i test falliscono**

```
pytest tests/runtime_v2/control_plane/test_outbox_writer.py::test_update_done_has_no_send_after_delay tests/runtime_v2/control_plane/test_outbox_writer.py::test_update_clean_log_includes_changed_field_for_be_move -v
```

Expected: FAIL

- [ ] **Step 4: Rimuovi UPDATE delay da `_send_after_for` in `outbox_writer.py`**

Attuale:
```python
def _send_after_for(notification_type: str) -> str:
    if notification_type in {"UPDATE_DONE", "UPDATE_PARTIAL", "UPDATE_REJECTED"}:
        return _iso_after(20)
    return _now()
```

Nuovo:
```python
def _send_after_for(notification_type: str) -> str:
    if notification_type == "MULTI_CHAIN_SUMMARY":
        return _iso_after(3)
    return _now()
```

- [ ] **Step 5: Rimuovi UPDATE da `_agg_group` in `outbox_writer.py`**

Attuale:
```python
def _agg_group(notification_type: str, chain_id: int | None, payload: dict) -> str | None:
    if chain_id is None:
        return None
    if notification_type in {"UPDATE_DONE", "UPDATE_PARTIAL", "UPDATE_REJECTED"}:
        return f"{chain_id}:{payload.get('source_message_id') or 'update_batch'}"
    return None
```

Nuovo:
```python
def _agg_group(notification_type: str, chain_id: int | None, payload: dict) -> str | None:
    return None
```

- [ ] **Step 6: Modifica `_write_update_clean_log` in `entry_gate.py` (righe 100-156)**

Sostituisci la funzione completa:

```python
def _write_update_clean_log(
    conn,
    cr: "UpdateChainResult",
    canonical_message_id: int,
    link: str | None,
) -> None:
    """Synthesize one UPDATE_DONE/PARTIAL/REJECTED CLEAN_LOG row from UpdateChainResult events."""
    accepted = [e for e in cr.lifecycle_events if e.event_type == "TELEGRAM_UPDATE_ACCEPTED"]
    noops = [e for e in cr.lifecycle_events if e.event_type.startswith("NOOP_")]
    if not accepted and not noops:
        return

    if accepted and not noops:
        notif_type = "UPDATE_DONE"
    elif accepted and noops:
        notif_type = "UPDATE_PARTIAL"
    else:
        notif_type = "UPDATE_REJECTED"

    applied_actions: list[str] = []
    rejected_actions: list[str] = [e.event_type for e in noops]
    changed: list[dict] = []

    for e in accepted:
        try:
            p = json.loads(e.payload_json or "{}")
        except Exception:
            p = {}
        action = p.get("action", "")
        if action:
            applied_actions.append(action)

        if p.get("is_breakeven"):
            changed.append({
                "field": "SL",
                "old": p.get("old_sl_price"),
                "new": p.get("new_sl_price"),
                "note": "BE",
            })
        elif action == "CANCEL_PENDING":
            for entry in p.get("cancelled_entries", []):
                changed.append({
                    "field": f"Entry_{entry.get('sequence', '?')}",
                    "old": entry.get("price"),
                    "new": "cancelled",
                })
        elif action == "CLOSE_PARTIAL":
            close_pct = p.get("close_pct")
            if close_pct is not None:
                changed.append({
                    "field": "Position",
                    "old": "open",
                    "new": f"closed {close_pct}%",
                })
        elif action == "MODIFY_ENTRIES":
            for ce in p.get("changed_entries", []):
                changed.append({
                    "field": f"Entry_{ce.get('sequence', '?')}",
                    "old": ce.get("old_price"),
                    "new": ce.get("new_price"),
                })

    first = (accepted or noops)[0]
    source = _SOURCE_TYPE_TO_CLEAN_LOG_SOURCE.get(first.source_type, "runtime")

    chain_row = conn.execute(
        "SELECT symbol, side FROM ops_trade_chains WHERE trade_chain_id=?",
        (cr.trade_chain_id,),
    ).fetchone()
    symbol = chain_row[0] if chain_row else None
    side = chain_row[1] if chain_row else None

    payload = {
        "chain_id": cr.trade_chain_id,
        "symbol": symbol,
        "side": side,
        "applied_actions": applied_actions,
        "rejected_actions": rejected_actions,
        "changed": changed,
        "source": source,
        "link": link,
    }
    write_clean_log_event(
        conn,
        notification_type=notif_type,
        chain_id=cr.trade_chain_id,
        payload=payload,
        dedupe_key=f"clean:update:{canonical_message_id}:{cr.trade_chain_id}",
    )
```

- [ ] **Step 7: Esegui i test**

```
pytest tests/runtime_v2/control_plane/test_outbox_writer.py tests/runtime_v2/lifecycle/test_entry_gate.py -v
```

Expected: tutti PASS

- [ ] **Step 8: Commit**

```bash
git add src/runtime_v2/lifecycle/entry_gate.py \
        src/runtime_v2/control_plane/outbox_writer.py \
        tests/runtime_v2/control_plane/test_outbox_writer.py
git commit -m "feat: populate changed field in UPDATE_DONE — remove UPDATE delay"
```

---

## Task 5: Aggiungi MULTI_CHAIN_SUMMARY

**Files:**
- Modify: `src/runtime_v2/control_plane/clean_log.py`
- Modify: `src/runtime_v2/lifecycle/entry_gate.py` (aggiungi `_write_multi_chain_summary`, chiama da `_persist_update`)
- Modify: `src/runtime_v2/control_plane/notification_dispatcher.py`
- Test: `tests/runtime_v2/control_plane/test_clean_log_formatter.py`
- Test: `tests/runtime_v2/lifecycle/test_entry_gate.py`

- [ ] **Step 1: Scrivi test per il formatter**

In `tests/runtime_v2/control_plane/test_clean_log_formatter.py`, aggiungi:

```python
def test_multi_chain_summary_all_done():
    from src.runtime_v2.control_plane.clean_log import format_clean_log
    payload = {
        "operations": ["Move SL to BE", "Cancel pending"],
        "chains": [
            {"chain_id": 42, "symbol": "BTC/USDT", "side": "LONG",  "status": "DONE",    "link": "https://t.me/c/xxx/101"},
            {"chain_id": 43, "symbol": "ETH/USDT", "side": "LONG",  "status": "DONE",    "link": "https://t.me/c/xxx/102"},
        ],
        "source": "trader_update",
    }
    result = format_clean_log("MULTI_CHAIN_SUMMARY", payload)
    assert "UPDATE APPLICATO — 2 chain" in result
    assert "✅" in result
    assert "DONE" in result
    assert "t.me/c/xxx/101" in result
    assert "Done: 2" in result


def test_multi_chain_summary_with_partial_shows_warning_emoji():
    from src.runtime_v2.control_plane.clean_log import format_clean_log
    payload = {
        "operations": ["Move SL to BE"],
        "chains": [
            {"chain_id": 42, "symbol": "BTC/USDT", "side": "LONG",  "status": "DONE"},
            {"chain_id": 43, "symbol": "ETH/USDT", "side": "LONG",  "status": "PARTIAL"},
            {"chain_id": 44, "symbol": "SOL/USDT", "side": "SHORT", "status": "SKIPPED"},
        ],
        "source": "trader_update",
    }
    result = format_clean_log("MULTI_CHAIN_SUMMARY", payload)
    assert "⚠️" in result
    assert "Partial: 1" in result
    assert "Skipped: 1" in result
```

- [ ] **Step 2: Verifica che i test falliscono**

```
pytest tests/runtime_v2/control_plane/test_clean_log_formatter.py::test_multi_chain_summary_all_done tests/runtime_v2/control_plane/test_clean_log_formatter.py::test_multi_chain_summary_with_partial_shows_warning_emoji -v
```

Expected: FAIL (MULTI_CHAIN_SUMMARY non gestito)

- [ ] **Step 3: Aggiungi `_multi_chain_summary` in `clean_log.py`**

Aggiungi la funzione prima di `_fallback`:

```python
def _multi_chain_summary(p: dict) -> str:
    chains = p.get("chains") or []
    statuses = {c.get("status") for c in chains}
    has_issues = bool(statuses & {"PARTIAL", "SKIPPED"})
    emoji = "⚠️" if has_issues else "✅"
    n = len(chains)
    lines: list[str] = [f"{emoji} UPDATE APPLICATO — {n} chain", _SEP]
    operations = p.get("operations") or []
    if operations:
        lines.append("Operation:")
        for op in operations:
            lines.append(f"{_BULLET} {op}")
    lines.append(_SEP)
    for chain in chains:
        status = chain.get("status", "DONE")
        link = chain.get("link")
        side_e = _side_emoji(chain.get("side"))
        label = f"#{chain.get('chain_id')} {chain.get('symbol', '?')} {side_e}  {status}"
        if link:
            label += f"  → {link}"
        lines.append(label)
    lines.append(_SEP)
    done = sum(1 for c in chains if c.get("status") == "DONE")
    partial = sum(1 for c in chains if c.get("status") == "PARTIAL")
    skipped = sum(1 for c in chains if c.get("status") == "SKIPPED")
    summary_parts = [f"Done: {done}"]
    if partial:
        summary_parts.append(f"Partial: {partial}")
    if skipped:
        summary_parts.append(f"Skipped: {skipped}")
    lines.append("   ".join(summary_parts))
    lines += _footer(p.get("source", "trader_update"))
    return _finalize(lines)
```

Aggiungi il case in `format_clean_log`:
```python
    if notification_type == "MULTI_CHAIN_SUMMARY":
        return _multi_chain_summary(payload)
```

- [ ] **Step 4: Esegui i test del formatter**

```
pytest tests/runtime_v2/control_plane/test_clean_log_formatter.py -v
```

Expected: tutti PASS

- [ ] **Step 5: Scrivi test per `_write_multi_chain_summary` in `entry_gate`**

Aggiungi in `tests/runtime_v2/lifecycle/test_entry_gate.py`:

```python
def test_persist_update_writes_multi_chain_summary_for_two_chains(ops_db, parser_db):
    """_persist_update deve scrivere MULTI_CHAIN_SUMMARY quando 2+ chain sono colpite."""
    import json, sqlite3
    from src.runtime_v2.lifecycle.entry_gate import (
        LifecycleGateWorker, _write_multi_chain_summary, UpdateChainResult,
    )
    from src.runtime_v2.lifecycle.models import LifecycleEvent

    conn = sqlite3.connect(ops_db)
    _seed_chain_ops(conn, chain_id=10, symbol="BTC/USDT", side="LONG")
    _seed_chain_ops(conn, chain_id=11, symbol="ETH/USDT", side="LONG")
    conn.commit()

    def _make_cr(chain_id: int) -> UpdateChainResult:
        return UpdateChainResult(
            trade_chain_id=chain_id,
            new_lifecycle_state=None,
            new_be_protection_status=None,
            lifecycle_events=[LifecycleEvent(
                trade_chain_id=chain_id,
                event_type="TELEGRAM_UPDATE_ACCEPTED",
                source_type="telegram_update",
                source_id="5",
                payload_json=json.dumps({"action": "MOVE_SL_TO_BE", "is_breakeven": True,
                                         "old_sl_price": 49000.0, "new_sl_price": 50100.0}),
                idempotency_key=f"be:{chain_id}:5",
            )],
            execution_commands=[],
        )

    with conn:
        _write_multi_chain_summary(conn, [_make_cr(10), _make_cr(11)], canonical_message_id=5)

    row = conn.execute(
        "SELECT payload_json FROM ops_notification_outbox WHERE notification_type='MULTI_CHAIN_SUMMARY'"
    ).fetchone()
    conn.close()
    assert row is not None
    p = json.loads(row[0])
    chain_ids = [c["chain_id"] for c in p["chains"]]
    assert set(chain_ids) == {10, 11}
    assert all(c["status"] == "DONE" for c in p["chains"])
```

- [ ] **Step 6: Verifica che il test fallisce**

```
pytest tests/runtime_v2/lifecycle/test_entry_gate.py::test_persist_update_writes_multi_chain_summary_for_two_chains -v
```

Expected: FAIL

- [ ] **Step 7: Aggiungi `_ACTION_LABELS` e `_write_multi_chain_summary` in `entry_gate.py`**

Aggiungi dopo `_SOURCE_TYPE_TO_CLEAN_LOG_SOURCE` (riga ~96):

```python
_ACTION_LABELS: dict[str, str] = {
    "MOVE_SL_TO_BE": "Move SL to BE",
    "CANCEL_PENDING": "Cancel pending",
    "CLOSE_FULL": "Close full",
    "CLOSE_PARTIAL": "Close partial",
    "MODIFY_ENTRIES": "Modify entries",
    "MARKET_ENTRY_NOW": "Market entry now",
}


def _write_multi_chain_summary(
    conn,
    chain_results: list["UpdateChainResult"],
    canonical_message_id: int,
) -> None:
    """Scrive MULTI_CHAIN_SUMMARY se 2+ chain hanno ricevuto una notifica UPDATE."""
    chains_payload: list[dict] = []
    operations_seen: list[str] = []
    seen_op_set: set[str] = set()

    for cr in chain_results:
        if not cr.trade_chain_id:
            continue
        accepted = [e for e in cr.lifecycle_events if e.event_type == "TELEGRAM_UPDATE_ACCEPTED"]
        noops = [e for e in cr.lifecycle_events if e.event_type.startswith("NOOP_")]
        if not accepted and not noops:
            continue

        if accepted and not noops:
            status = "DONE"
        elif accepted:
            status = "PARTIAL"
        else:
            status = "SKIPPED"

        for e in accepted:
            try:
                action = json.loads(e.payload_json or "{}").get("action", "")
                label = _ACTION_LABELS.get(action, action)
                if label and label not in seen_op_set:
                    seen_op_set.add(label)
                    operations_seen.append(label)
            except Exception:
                pass

        row = conn.execute(
            "SELECT symbol, side FROM ops_trade_chains WHERE trade_chain_id=?",
            (cr.trade_chain_id,),
        ).fetchone()
        chains_payload.append({
            "chain_id": cr.trade_chain_id,
            "symbol": row[0] if row else None,
            "side": row[1] if row else None,
            "status": status,
        })

    if len(chains_payload) < 2:
        return

    write_clean_log_event(
        conn,
        notification_type="MULTI_CHAIN_SUMMARY",
        chain_id=None,
        payload={"operations": operations_seen, "chains": chains_payload},
        dedupe_key=f"clean:multi_summary:{canonical_message_id}",
    )
```

- [ ] **Step 8: Chiama `_write_multi_chain_summary` da `_persist_update` in `entry_gate.py`**

In `_persist_update`, dopo il blocco `for cr in result.chain_results:` che chiama `_write_update_clean_log` (riga ~1810), aggiungi:

```python
                try:
                    _write_multi_chain_summary(
                        conn, result.chain_results, enriched.canonical_message_id,
                    )
                except Exception:
                    logger.exception(
                        "multi_chain_summary failed for canonical_message_id=%s",
                        enriched.canonical_message_id,
                    )
```

- [ ] **Step 9: Esegui il test**

```
pytest tests/runtime_v2/lifecycle/test_entry_gate.py::test_persist_update_writes_multi_chain_summary_for_two_chains -v
```

Expected: PASS

- [ ] **Step 10: Scrivi test per link enrichment nel dispatcher**

Aggiungi in `tests/runtime_v2/control_plane/test_dispatcher.py`:

```python
def test_dispatcher_enriches_multi_chain_summary_with_links(ops_db):
    """drain_once deve iniettare link t.me/ nel payload MULTI_CHAIN_SUMMARY prima del render."""
    import json, sqlite3
    from unittest.mock import AsyncMock, MagicMock
    from src.runtime_v2.control_plane.notification_dispatcher import TelegramNotificationDispatcher
    from src.runtime_v2.control_plane.topic_router import TopicRouter

    conn = sqlite3.connect(ops_db)
    # Inserisci tracking per chain 42
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO ops_clean_log_tracking "
        "(trade_chain_id, clean_log_root_message_id, clean_log_last_message_id, "
        " telegram_chat_id, telegram_thread_id, last_clean_log_event_type, "
        " last_clean_log_sent_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (42, "10", "55", "-10012345", None, "UPDATE_DONE", now, now),
    )
    # Inserisci il MULTI_CHAIN_SUMMARY nell'outbox
    payload = json.dumps({
        "operations": ["Move SL to BE"],
        "chains": [
            {"chain_id": 42, "symbol": "BTC/USDT", "side": "LONG", "status": "DONE"},
        ],
    })
    conn.execute(
        "INSERT INTO ops_notification_outbox "
        "(notification_type, destination, payload_json, priority, status, dedupe_key, attempts, created_at) "
        "VALUES ('MULTI_CHAIN_SUMMARY', 'CLEAN_LOG', ?, 'MEDIUM', 'PENDING', 'test:mcs:1', 0, ?)",
        (payload, now),
    )
    conn.commit()
    conn.close()

    sender = AsyncMock()
    sender.send.return_value = "999"
    router = MagicMock()
    router.route.return_value = (123456, None)
    config = _make_config()

    dispatcher = TelegramNotificationDispatcher(
        config=config, ops_db_path=ops_db, topic_router=router, sender=sender,
    )

    import asyncio
    asyncio.run(dispatcher.drain_once())

    _, kwargs = sender.send.call_args
    assert "t.me/c/" in kwargs["text"], f"link non presente nel testo: {kwargs['text']}"
```

- [ ] **Step 11: Verifica che il test fallisce**

```
pytest tests/runtime_v2/control_plane/test_dispatcher.py::test_dispatcher_enriches_multi_chain_summary_with_links -v
```

Expected: FAIL

- [ ] **Step 12: Aggiungi `_get_clean_log_last` e arricchimento link nel dispatcher**

In `notification_dispatcher.py`, aggiungi dopo `_get_clean_log_root`:

```python
    def _get_clean_log_last(self, chain_id: int) -> tuple[str | None, str | None]:
        """Return (last_message_id, telegram_chat_id) for chain_id — punta all'ultimo messaggio inviato."""
        conn = sqlite3.connect(self._ops_db)
        try:
            row = conn.execute(
                "SELECT clean_log_last_message_id, telegram_chat_id "
                "FROM ops_clean_log_tracking WHERE trade_chain_id=?",
                (chain_id,),
            ).fetchone()
            if row:
                return str(row[0]) if row[0] else None, str(row[1]) if row[1] else None
            return None, None
        finally:
            conn.close()
```

In `drain_once`, subito prima di `text = self._render(...)`, aggiungi:

```python
                if destination == "CLEAN_LOG" and notification_type == "MULTI_CHAIN_SUMMARY":
                    for chain in payload.get("chains", []):
                        cid = chain.get("chain_id")
                        if cid is not None:
                            last_msg_id, tracking_chat_id = self._get_clean_log_last(cid)
                            chain["link"] = self._build_signal_link(last_msg_id, tracking_chat_id)
```

- [ ] **Step 13: Esegui tutti i test correlati**

```
pytest tests/runtime_v2/control_plane/test_dispatcher.py tests/runtime_v2/control_plane/test_clean_log_formatter.py tests/runtime_v2/lifecycle/test_entry_gate.py -v
```

Expected: tutti PASS

- [ ] **Step 14: Commit**

```bash
git add src/runtime_v2/control_plane/clean_log.py \
        src/runtime_v2/lifecycle/entry_gate.py \
        src/runtime_v2/control_plane/outbox_writer.py \
        src/runtime_v2/control_plane/notification_dispatcher.py \
        tests/runtime_v2/control_plane/test_clean_log_formatter.py \
        tests/runtime_v2/control_plane/test_dispatcher.py \
        tests/runtime_v2/lifecycle/test_entry_gate.py
git commit -m "feat: add MULTI_CHAIN_SUMMARY notification with per-chain links"
```

---

## Task 6: Rimuovi AggregationWorker

**Files:**
- Delete: `src/runtime_v2/control_plane/aggregation_worker.py`
- Modify: `src/runtime_v2/control_plane/bootstrap.py`
- Delete: `tests/runtime_v2/control_plane/test_aggregation_worker.py`

- [ ] **Step 1: Rimuovi `AggregationWorker` da `bootstrap.py`**

In `bootstrap.py`:
- Rimuovi riga 5: `from src.runtime_v2.control_plane.aggregation_worker import AggregationWorker`
- Rimuovi riga 39: `aggregation_worker: AggregationWorker` dal dataclass `ControlPlane`
- Rimuovi riga 85: `aggregation_worker = AggregationWorker(ops_db_path)`
- Rimuovi riga 94: `aggregation_worker=aggregation_worker,` dal `return ControlPlane(...)`

- [ ] **Step 2: Verifica che `startup.py` non usi AggregationWorker**

```
grep -r "aggregation_worker\|AggregationWorker" src/runtime_v2/control_plane/startup.py
```

Expected: nessun match (confermato già dalla ricerca precedente)

- [ ] **Step 3: Elimina il file `aggregation_worker.py`**

```bash
git rm src/runtime_v2/control_plane/aggregation_worker.py
```

- [ ] **Step 4: Elimina `test_aggregation_worker.py`**

```bash
git rm tests/runtime_v2/control_plane/test_aggregation_worker.py
```

- [ ] **Step 5: Esegui la suite completa**

```
pytest tests/runtime_v2/ -v
```

Expected: tutti PASS — nessuna reference a `AggregationWorker`

- [ ] **Step 6: Commit**

```bash
git add src/runtime_v2/control_plane/bootstrap.py
git commit -m "feat: remove AggregationWorker — replaced by entry-gate summary + immediate dispatch"
```

---

## Self-Review

### Copertura spec

| Req spec | Task |
|---|---|
| TP partial immediati, no aggregazione | Task 1 |
| TP_BATCH_FILLED rimosso | Task 1 |
| UPDATE immediati, no delay | Task 4 |
| BE move visibile in UPDATE_DONE con prezzi | Task 2 + Task 4 |
| Cancel pending con entry cancellate | Task 3 + Task 4 |
| Close partial con % | Task 3 + Task 4 |
| MULTI_CHAIN_SUMMARY con link | Task 5 |
| Emoji ⚠️ su PARTIAL/SKIPPED | Task 5 Step 3 |
| AggregationWorker rimosso | Task 1 (parziale) + Task 6 |
| Dispatcher arricchisce link | Task 5 Step 12 |

### Note

- `_apply_modify_entries` non è trattato in Task 3 perché richiede modifiche più profonde al diff_engine per estrarre `old_price`/`new_price`. Il campo `changed_entries` viene letto in `_write_update_clean_log` (Task 4, riga `elif action == "MODIFY_ENTRIES"`) ma `_apply_modify_entries` lascia il payload invariato — può essere esteso come task separato futuro senza bloccare la consegna.
- `LifecycleEventType` in `models.py` non va modificato: `BE_MOVE_REQUESTED` rimane valido perché usato da `event_processor.py` per i BE automatici (engine-driven), non solo da `entry_gate`.
