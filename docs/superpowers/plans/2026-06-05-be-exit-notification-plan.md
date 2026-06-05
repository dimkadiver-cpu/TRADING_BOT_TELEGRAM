# BE Exit Notification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rendere `SL_FILLED` su chain `PROTECTED` visibile come `BE EXIT` nel CLEAN_LOG, mantenendo invariati classifier exchange e lifecycle event types.

**Architecture:** La soluzione resta confinata nel control-plane notifiche. `project_clean_log_for_chain()` decide un `close_reason` canonico per i terminal stop (`STOP_LOSS` vs `BREAKEVEN_AFTER_TP`) e il formatter CLEAN_LOG usa quel campo come sorgente di verita' per il rendering.

**Tech Stack:** Python, SQLite, pytest, control-plane runtime_v2

---

## File Structure

- Modify: `src/runtime_v2/control_plane/outbox_writer.py`
  - Responsabilita': proiezione degli eventi lifecycle in notifiche CLEAN_LOG con payload arricchito.
- Modify: `src/runtime_v2/control_plane/formatters/clean_log.py`
  - Responsabilita': rendering testuale finale del payload CLEAN_LOG.
- Modify: `tests/runtime_v2/control_plane/test_outbox_writer.py`
  - Responsabilita': verificare la proiezione `SL_FILLED` non protetto vs protetto.
- Modify: `tests/runtime_v2/control_plane/test_clean_log_formatter.py`
  - Responsabilita': verificare che `SL_FILLED` continui a renderizzare come `STOP_LOSS` quando non protetto.
- Modify: `tests/runtime_v2/control_plane/test_clean_log_formatter_full.py`
  - Responsabilita': verificare che il payload BE canonicalizzato venga renderizzato come `BE EXIT`.

## Task 1: Canonicalizzare `close_reason` per `SL_FILLED` in outbox projection

**Files:**
- Modify: `tests/runtime_v2/control_plane/test_outbox_writer.py`
- Modify: `src/runtime_v2/control_plane/outbox_writer.py`

- [ ] **Step 1: Aggiungere il test che conferma `SL_FILLED` non protetto -> `STOP_LOSS`**

Inserire in `tests/runtime_v2/control_plane/test_outbox_writer.py` subito dopo `test_close_full_filled_on_protected_chain_projects_be_exit`:

```python
def test_sl_filled_on_unprotected_chain_projects_stop_loss(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _seed_chain(conn, 901)
        conn.execute(
            "UPDATE ops_trade_chains SET be_protection_status='NOT_PROTECTED', "
            "entry_avg_price=65000.0, cumulative_gross_pnl=-12.0, "
            "cumulative_fees=1.80 WHERE trade_chain_id=?",
            (901,),
        )
        _seed_event(conn, 901, "SL_FILLED", "sl_filled:901:1", {
            "fill_price": 64880.0,
            "filled_qty": 0.01,
            "exec_fee": 0.90,
            "closed_size": 0.01,
        })
        project_clean_log_for_chain(conn, 901)
    row = conn.execute(
        "SELECT notification_type, payload_json "
        "FROM ops_notification_outbox"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "SL_FILLED"
    payload = json.loads(row[1])
    assert payload["close_reason"] == "STOP_LOSS"
    assert payload["sl_price"] == 64880.0
```

- [ ] **Step 2: Eseguire il test nuovo e verificare che fallisca**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\runtime_v2\control_plane\test_outbox_writer.py::test_sl_filled_on_unprotected_chain_projects_stop_loss -q
```

Expected: `FAIL` con `KeyError: 'close_reason'` oppure assertion sul payload privo di `close_reason`.

- [ ] **Step 3: Aggiungere il test che conferma `SL_FILLED` protetto -> `BREAKEVEN_AFTER_TP`**

Inserire nello stesso file subito dopo il test precedente:

```python
def test_sl_filled_on_protected_chain_projects_be_close_reason(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _seed_chain(conn, 902)
        conn.execute(
            "UPDATE ops_trade_chains SET be_protection_status='PROTECTED', "
            "entry_avg_price=65000.0, cumulative_gross_pnl=-0.20, "
            "cumulative_fees=1.70 WHERE trade_chain_id=?",
            (902,),
        )
        _seed_event(conn, 902, "SL_FILLED", "sl_filled:902:1", {
            "fill_price": 65000.0,
            "filled_qty": 0.01,
            "exec_fee": 1.70,
            "closed_size": 0.01,
        })
        project_clean_log_for_chain(conn, 902)
    row = conn.execute(
        "SELECT notification_type, payload_json "
        "FROM ops_notification_outbox"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "SL_FILLED"
    payload = json.loads(row[1])
    assert payload["close_reason"] == "BREAKEVEN_AFTER_TP"
    assert payload["sl_price"] == 65000.0
```

- [ ] **Step 4: Eseguire i due test outbox e verificare che il secondo fallisca**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\runtime_v2\control_plane\test_outbox_writer.py::test_sl_filled_on_unprotected_chain_projects_stop_loss tests\runtime_v2\control_plane\test_outbox_writer.py::test_sl_filled_on_protected_chain_projects_be_close_reason -q
```

Expected: il caso non protetto puo' gia' passare o fallire per `close_reason` mancante; il caso protetto deve fallire per `close_reason` ancora assente o uguale a `STOP_LOSS`.

- [ ] **Step 5: Implementare la canonicalizzazione del `close_reason` in `outbox_writer.py`**

Nel ramo `if notification_type == "SL_FILLED":` sostituire il return con:

```python
    if notification_type == "SL_FILLED":
        closed_qty = ev.get("closed_size", ev.get("filled_qty"))
        fill_price = ev.get("fill_price")
        close_reason = (
            "BREAKEVEN_AFTER_TP"
            if be_protection_status == "PROTECTED"
            else "STOP_LOSS"
        )
        return {
            **base,
            "fill_price": fill_price,
            "sl_price": fill_price,
            "closed_pct": _closed_pct(closed_qty, filled_entry_qty),
            "pnl": _side_pnl(side, entry_avg_price, fill_price, closed_qty),
            "fee": ev.get("exec_fee"),
            "close_reason": close_reason,
            "final_result": _final_result(
                gross_pnl=cumulative_gross_pnl,
                fees=cumulative_fees,
                funding=cumulative_funding,
                allocated_margin=allocated_margin,
                close_reason=close_reason,
            ),
            "source": ev.get("source", "exchange"),
        }
```

- [ ] **Step 6: Rieseguire i test outbox e verificare che passino**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\runtime_v2\control_plane\test_outbox_writer.py::test_sl_filled_on_unprotected_chain_projects_stop_loss tests\runtime_v2\control_plane\test_outbox_writer.py::test_sl_filled_on_protected_chain_projects_be_close_reason tests\runtime_v2\control_plane\test_outbox_writer.py::test_close_full_filled_on_protected_chain_projects_be_exit -q
```

Expected: `3 passed`

- [ ] **Step 7: Non fare commit automatici**

Questo repository richiede di non fare `git add` / `git commit` senza richiesta esplicita dell'utente. Fermarsi ai file modificati e alla validazione verde.

## Task 2: Renderizzare `SL_FILLED` protetto come `BE EXIT`

**Files:**
- Modify: `tests/runtime_v2/control_plane/test_clean_log_formatter.py`
- Modify: `tests/runtime_v2/control_plane/test_clean_log_formatter_full.py`
- Modify: `src/runtime_v2/control_plane/formatters/clean_log.py`

- [ ] **Step 1: Aggiungere il test che preserva il rendering `STOP_LOSS` per `SL_FILLED` non protetto**

In `tests/runtime_v2/control_plane/test_clean_log_formatter.py`, subito dopo `test_sl_filled_side_always_correct`, aggiungere:

```python
def test_sl_filled_with_stop_loss_reason_renders_position_closed():
    text = format_clean_log("SL_FILLED", {
        "chain_id": 145,
        "symbol": "BTC/USDT",
        "side": "LONG",
        "fill_price": 62000.0,
        "close_reason": "STOP_LOSS",
        "source": "exchange",
    })
    assert "POSITION CLOSED" in text
    assert "STOP_LOSS" in text
    assert "BE EXIT" not in text
```

- [ ] **Step 2: Eseguire il test di preservazione**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\runtime_v2\control_plane\test_clean_log_formatter.py::test_sl_filled_with_stop_loss_reason_renders_position_closed -q
```

Expected: `PASS` oppure `FAIL` solo per mismatch testuale minimo, mai per eccezioni.

- [ ] **Step 3: Aggiungere il test che rende il payload BE canonicalizzato come `BE EXIT`**

In `tests/runtime_v2/control_plane/test_clean_log_formatter_full.py`, subito dopo `test_be_exit_formatter_renders_exit_and_final_result`, aggiungere:

```python
def test_sl_filled_with_be_close_reason_renders_be_exit():
    text = format_clean_log("SL_FILLED", {
        "chain_id": 146,
        "symbol": "BTC/USDT",
        "side": "LONG",
        "fill_price": 65000.0,
        "sl_price": 65000.0,
        "close_reason": "BREAKEVEN_AFTER_TP",
        "pnl": -0.20,
        "fee": 1.70,
        "final_result": {
            "roi_net_pct": None,
            "total_pnl_net": -1.90,
            "gross_pnl": -0.20,
            "fees": -1.70,
            "funding": 0.0,
            "close_reason": "BREAKEVEN_AFTER_TP",
        },
        "source": "exchange",
    })
    assert "BE EXIT" in text
    assert "Exit: 65,000 BE" in text
    assert "Close reason: BREAKEVEN_AFTER_TP" in text
    assert "POSITION CLOSED" not in text
```

- [ ] **Step 4: Eseguire il test nuovo e verificare che fallisca**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\runtime_v2\control_plane\test_clean_log_formatter_full.py::test_sl_filled_with_be_close_reason_renders_be_exit -q
```

Expected: `FAIL` perche' oggi `SL_FILLED` passa sempre da `_sl_filled()` e stampa `POSITION CLOSED` / `STOP_LOSS`.

- [ ] **Step 5: Implementare il routing del formatter basato su `close_reason`**

In `src/runtime_v2/control_plane/formatters/clean_log.py`, aggiornare `format_clean_log()` sostituendo il ramo `SL_FILLED` con:

```python
    if notification_type == "SL_FILLED":
        if payload.get("close_reason") == "BREAKEVEN_AFTER_TP":
            be_payload = {
                **payload,
                "exit_price": payload.get("sl_price", payload.get("fill_price")),
            }
            return _be_exit(be_payload)
        return _sl_filled(payload)
```

- [ ] **Step 6: Rieseguire i test formatter mirati**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\runtime_v2\control_plane\test_clean_log_formatter.py::test_sl_filled_with_stop_loss_reason_renders_position_closed tests\runtime_v2\control_plane\test_clean_log_formatter_full.py::test_sl_filled_with_be_close_reason_renders_be_exit tests\runtime_v2\control_plane\test_clean_log_formatter_full.py::test_be_exit_formatter_renders_exit_and_final_result -q
```

Expected: `3 passed`

- [ ] **Step 7: Eseguire la suite control-plane minima di regressione**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\runtime_v2\control_plane\test_outbox_writer.py tests\runtime_v2\control_plane\test_clean_log_formatter.py tests\runtime_v2\control_plane\test_clean_log_formatter_full.py -q
```

Expected: suite verde senza regressioni sui casi esistenti `SL_FILLED`, `BE_EXIT`, `POSITION_CLOSED`.

- [ ] **Step 8: Non fare commit automatici**

Questo repository richiede di non fare `git add` / `git commit` senza richiesta esplicita dell'utente. Concludere con report di file toccati e test eseguiti.

## Self-Review

- Copertura spec: il Task 1 implementa la regola canonica `close_reason` nel projection layer; il Task 2 implementa il rendering user-facing guidato dal payload.
- Nessun placeholder: ogni modifica ha file, snippet, comando e aspettativa espliciti.
- Coerenza tipi: i valori canonici usati in tutto il piano sono `STOP_LOSS` e `BREAKEVEN_AFTER_TP`; il rendering `BE EXIT` dipende solo da `close_reason`.
