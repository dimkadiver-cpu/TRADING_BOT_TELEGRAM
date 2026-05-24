# Lifecycle Verification Gaps e TP Sync — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminare 3 gap nel sistema di verifica lifecycle: (1) MOVE_STOP/SYNC_PROTECTIVE_ORDERS non emettono eventi lifecycle, (2) TP_FILLED ha idempotency key divergente tra WS e polling, (3) il rilevamento TP Mode C dipende da heuristica polling a 60s invece di WebSocket real-time.

**Architecture:** Il gateway emette eventi lifecycle direttamente per le operazioni fire-and-forget sincrone (retCode=0 = conferma); una unified idempotency key `TP_FILLED:{chain_id}:level:{tp_level}` elimina i duplicati tra tutte le sorgenti; `BybitWsFillWatcher` aggiunge un secondo task asincrono `_watch_trades_forever()` parallelo a `_watch_orders_forever()` che fa matching fill↔chain per i TP position-level.

**Tech Stack:** Python 3.12, SQLite (`aiosqlite`/`sqlite3`), ccxt.pro (`watchMyTrades`), pytest, Pydantic v2.

---

## File Map

| File | Azione | Responsabilità |
|------|--------|----------------|
| `src/runtime_v2/execution_gateway/repositories.py` | Modifica | `count_active_tps()`, `insert_exchange_event()`, `get_active_tp_commands()`, `get_open_chains_for_symbol()` |
| `src/runtime_v2/execution_gateway/gateway.py` | Modifica | `_FIRE_AND_FORGET_EVENTS`, `_emit_confirmed_event()`, `_build_confirmed_event_payload()`, aggiornamento `process()` |
| `src/runtime_v2/execution_gateway/event_sync.py` | Modifica | Rimozione dead code `role="sync"`, nuova idempotency key TP |
| `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/ws_fill_watcher.py` | Modifica | Nuova idempotency key TP, ristrutturazione task management, `_watch_trades_forever()`, `_process_trade_batch()`, `_match_and_save_tp_fill()`, `_save_tp_fill_from_trade()` |
| `tests/runtime_v2/execution_gateway/test_gateway.py` | Modifica | 5 nuovi test fire-and-forget |
| `tests/runtime_v2/execution_gateway/test_event_sync.py` | Modifica | 1 nuovo test idempotency unificata |
| `tests/runtime_v2/execution_gateway/test_ws_fill_watcher.py` | Crea | Test unitari `_save_fill()`, `_save_tp_fill_from_trade()`, matching, idempotency |

---

## Task 1 — Prerequisito: `count_active_tps()` in repositories

`count_active_tps()` è chiamata in `event_sync._normalize_and_save()` e `ws_fill_watcher._save_fill()` ma NON è definita in `repositories.py`. Qualsiasi fill di TP standalone (Mode A/B con clientOrderId) causerebbe `AttributeError` a runtime.

**Files:**
- Modify: `src/runtime_v2/execution_gateway/repositories.py`

- [ ] **Step 1: Aggiungi `count_active_tps()` a `GatewayCommandRepository`**

Apri `src/runtime_v2/execution_gateway/repositories.py`. Dopo il metodo `cancel_tp_partial_commands()` (linea 300), aggiungi prima di `__all__`:

```python
def count_active_tps(self, trade_chain_id: int) -> int:
    """Conta i comandi SET_POSITION_TPSL_* SENT/DONE per la chain (TP attivi)."""
    conn = sqlite3.connect(self._db)
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM ops_execution_commands "
            "WHERE trade_chain_id=? "
            "AND command_type IN ('SET_POSITION_TPSL_PARTIAL', 'SET_POSITION_TPSL_FULL') "
            "AND status IN ('SENT', 'DONE')",
            (trade_chain_id,),
        ).fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()
```

- [ ] **Step 2: Verifica che il metodo sia importabile**

```
pytest tests/runtime_v2/execution_gateway/test_gateway.py -v -x
```

Expected: tutti i test esistenti passano (la nuova funzione non tocca test esistenti).

- [ ] **Step 3: Commit**

```bash
git add src/runtime_v2/execution_gateway/repositories.py
git commit -m "fix(repositories): add missing count_active_tps method"
```

---

## Task 2 — Fix idempotency key TP_FILLED + rimozione dead code

### Problema
Due chiavi diverse per lo stesso evento:
- `event_sync._save_tp_fill()` (polling): `TP_FILLED:reconciliation:{chain_id}:{tp_level}`
- `event_sync._normalize_and_save()` role=tp: `TP_FILLED:{chain_id}:{exchange_order_id}`
- `ws_fill_watcher._save_fill()` role=tp: `TP_FILLED:{chain_id}:{exchange_order_id}`

→ WS e polling possono inserire due righe per lo stesso TP hit.

### Dead code
`event_sync._normalize_and_save()` ha un branch `coid.role == "sync"` che emette `PROTECTIVE_ORDERS_SYNCED`. Ma `SYNC_PROTECTIVE_ORDERS` è in `_FIRE_AND_FORGET`, quindi non finisce mai in `get_sent_or_ack()`, e questo branch è irraggiungibile. Va rimosso.

**Files:**
- Modify: `src/runtime_v2/execution_gateway/event_sync.py`
- Modify: `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/ws_fill_watcher.py`
- Test: `tests/runtime_v2/execution_gateway/test_event_sync.py`

- [ ] **Step 1: Scrivi il test di idempotency unificata (prima che passi)**

In `tests/runtime_v2/execution_gateway/test_event_sync.py`, aggiungi dopo i test esistenti:

```python
def _insert_open_chain_with_tp(db_path, chain_id, symbol="BTC/USDT:USDT", side="LONG"):
    """Inserisce chain OPEN con un SET_POSITION_TPSL_PARTIAL DONE."""
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_trade_chains "
        "(trade_chain_id, source_enrichment_id, canonical_message_id, raw_message_id, "
        "trader_id, account_id, symbol, side, lifecycle_state, entry_mode, "
        "management_plan_json, open_position_qty, filled_entry_qty, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (chain_id, chain_id, chain_id, chain_id, "t1", "acc",
         symbol, side, "OPEN", "ONE_SHOT", "{}", 0.01, 0.01, now, now),
    )
    conn.execute(
        "INSERT INTO ops_execution_commands "
        "(command_id, trade_chain_id, command_type, status, payload_json, "
        "idempotency_key, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (chain_id * 100, chain_id, "SET_POSITION_TPSL_PARTIAL", "DONE",
         '{"take_profit": 70000.0, "tp_size": 0.005, "tp_sequence": 1, "symbol": "BTC/USDT:USDT", "side": "LONG"}',
         f"idem_tp:{chain_id}", now, now),
    )
    conn.commit()
    conn.close()


def test_tp_filled_ws_and_polling_unified_key_no_duplicate(ops_db):
    """WS inserisce TP_FILLED con chiave level:N; poi run_tp_reconciliation()
    trova INSERT OR IGNORE → esattamente 1 riga."""
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_open_chain_with_tp(ops_db, chain_id=30)

    # Simula: WS ha già inserito TP_FILLED con la nuova chiave unified
    conn = sqlite3.connect(ops_db)
    conn.execute(
        "INSERT INTO ops_exchange_events "
        "(trade_chain_id, event_type, payload_json, processing_status, idempotency_key, received_at) "
        "VALUES (?,?,?,?,?,datetime('now'))",
        (30, "TP_FILLED",
         '{"tp_level": 1, "is_final": false, "fill_price": 70000.0, "filled_qty": 0.005, "source": "watch_my_trades"}',
         "NEW", "TP_FILLED:30:level:1"),
    )
    conn.commit()
    conn.close()

    # Polling tenta di inserire lo stesso evento
    adapter = FakeAdapter(positions={"BTC/USDT:USDT:LONG": 0.005})
    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(
        ops_db_path=ops_db, adapter=adapter,
        repo=repo, execution_account_id="acc",
    )
    worker.run_tp_reconciliation()

    conn = sqlite3.connect(ops_db)
    count = conn.execute(
        "SELECT COUNT(*) FROM ops_exchange_events WHERE trade_chain_id=30 AND event_type='TP_FILLED'"
    ).fetchone()[0]
    conn.close()
    assert count == 1, f"Expected 1 TP_FILLED event, got {count}"
```

- [ ] **Step 2: Esegui il test per verificare che fallisce**

```
pytest tests/runtime_v2/execution_gateway/test_event_sync.py::test_tp_filled_ws_and_polling_unified_key_no_duplicate -v
```

Expected: FAIL — il polling usa ancora `TP_FILLED:reconciliation:30:1` (chiave diversa), quindi inserisce una seconda riga → count == 2.

- [ ] **Step 3: Aggiorna `event_sync._save_tp_fill()` con nuova chiave**

In `src/runtime_v2/execution_gateway/event_sync.py`, trova il metodo `_save_tp_fill()` (linea ~191) e sostituisci:

```python
idempotency_key = f"TP_FILLED:reconciliation:{trade_chain_id}:{tp_level}"
```

con:

```python
idempotency_key = f"TP_FILLED:{trade_chain_id}:level:{tp_level}"
```

- [ ] **Step 4: Aggiorna `event_sync._normalize_and_save()` — chiave TP e rimozione dead code**

In `src/runtime_v2/execution_gateway/event_sync.py`, trova il metodo `_normalize_and_save()`.

**4a — Rimuovi il branch dead code `role="sync"`** (linee ~339-343):

```python
elif coid.role == "sync":
    event_type = "PROTECTIVE_ORDERS_SYNCED"
    payload = {
        "command_id": coid.command_id,
    }
```

Rimuovi queste 5 righe. Il branch precedente (`elif coid.role == "exit_full"`) deve ora cadere direttamente nel `else`.

**4b — Aggiorna la idempotency key** (linea ~348). Attualmente:

```python
idempotency_key = f"{event_type}:{coid.trade_chain_id}:{exchange_order_id}"
```

Sostituisci con:

```python
if coid.role == "tp":
    idempotency_key = f"TP_FILLED:{coid.trade_chain_id}:level:{coid.sequence}"
else:
    idempotency_key = f"{event_type}:{coid.trade_chain_id}:{exchange_order_id}"
```

- [ ] **Step 5: Aggiorna `ws_fill_watcher._save_fill()` — chiave TP**

In `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/ws_fill_watcher.py`, nel metodo `_save_fill()`, trova il blocco `conn.execute(...)` alla fine del metodo (linea ~181-198). L'idempotency key è:

```python
f"{event_type}:{coid.trade_chain_id}:{exchange_order_id}",
```

Sostituisci questo blocco (tutto l'interno del `try:` dopo la definizione di `payload`) con:

```python
        if event_type == "TP_FILLED":
            idempotency_key = f"TP_FILLED:{coid.trade_chain_id}:level:{coid.sequence}"
        else:
            idempotency_key = f"{event_type}:{coid.trade_chain_id}:{exchange_order_id}"

        conn = sqlite3.connect(self._ops_db_path)
        try:
            conn.execute(
                "INSERT OR IGNORE INTO ops_exchange_events "
                "(trade_chain_id, event_type, payload_json, processing_status, "
                "idempotency_key, received_at) VALUES (?,?,?,?,?,?)",
                (
                    coid.trade_chain_id,
                    event_type,
                    json.dumps(payload),
                    "NEW",
                    idempotency_key,
                    _now(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
```

- [ ] **Step 6: Esegui il test**

```
pytest tests/runtime_v2/execution_gateway/test_event_sync.py::test_tp_filled_ws_and_polling_unified_key_no_duplicate -v
```

Expected: PASS

- [ ] **Step 7: Verifica nessuna regressione**

```
pytest tests/runtime_v2/execution_gateway/test_event_sync.py tests/runtime_v2/execution_gateway/test_gateway.py -v
```

Expected: tutti i test esistenti passano.

- [ ] **Step 8: Commit**

```bash
git add src/runtime_v2/execution_gateway/event_sync.py \
        src/runtime_v2/execution_gateway/adapters/ccxt_bybit/ws_fill_watcher.py \
        tests/runtime_v2/execution_gateway/test_event_sync.py
git commit -m "fix(event_sync): unified TP_FILLED idempotency key, remove dead code role=sync"
```

---

## Task 3 — `insert_exchange_event()` in repositories

Prima di modificare il gateway, il repository deve esporre il metodo che il gateway chiamerà.

**Files:**
- Modify: `src/runtime_v2/execution_gateway/repositories.py`

- [ ] **Step 1: Aggiungi `insert_exchange_event()` a `GatewayCommandRepository`**

In `src/runtime_v2/execution_gateway/repositories.py`, dopo `count_active_tps()` (aggiunto nel Task 1), aggiungi:

```python
def insert_exchange_event(
    self,
    trade_chain_id: int,
    event_type: str,
    payload_json: str,
    idempotency_key: str,
) -> None:
    """INSERT OR IGNORE in ops_exchange_events. Idempotente."""
    now = _now()
    conn = sqlite3.connect(self._db)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO ops_exchange_events "
            "(trade_chain_id, event_type, payload_json, processing_status, "
            "idempotency_key, received_at) VALUES (?,?,?,?,?,?)",
            (trade_chain_id, event_type, payload_json, "NEW", idempotency_key, now),
        )
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 2: Verifica che i test esistenti non siano rotti**

```
pytest tests/runtime_v2/execution_gateway/ -v -x
```

Expected: tutti i test esistenti passano.

- [ ] **Step 3: Commit**

```bash
git add src/runtime_v2/execution_gateway/repositories.py
git commit -m "feat(repositories): add insert_exchange_event method"
```

---

## Task 4 — Gateway: emetti eventi lifecycle per fire-and-forget

Dopo `retCode=0`, il gateway inserisce `STOP_MOVED_CONFIRMED` o `PROTECTIVE_ORDERS_SYNCED` in `ops_exchange_events` prima di chiamare `mark_done()`.

**Files:**
- Modify: `src/runtime_v2/execution_gateway/gateway.py`
- Test: `tests/runtime_v2/execution_gateway/test_gateway.py`

- [ ] **Step 1: Scrivi i test (prima che passino)**

In `tests/runtime_v2/execution_gateway/test_gateway.py`, aggiungi alla fine del file:

```python
# ── Fire-and-forget lifecycle events ─────────────────────────────────────────

def _get_exchange_events(db_path: str, chain_id: int) -> list[tuple[str, dict]]:
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT event_type, payload_json FROM ops_exchange_events WHERE trade_chain_id=?",
        (chain_id,),
    ).fetchall()
    conn.close()
    return [(r[0], json.loads(r[1])) for r in rows]


def test_move_stop_to_be_emits_stop_moved_confirmed(ops_db):
    """MOVE_STOP_TO_BREAKEVEN con retCode=0 → STOP_MOVED_CONFIRMED con is_breakeven=True."""
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_cmd(ops_db, 5001, cmd_type="MOVE_STOP_TO_BREAKEVEN", payload={
        "symbol": "BTC/USDT", "side": "LONG", "new_stop_price": 50000.0,
    })
    repo = GatewayCommandRepository(ops_db)
    gw = ExecutionGateway(
        config=ExecutionConfigLoader("config/execution.yaml").load(),
        adapter_registry={"bybit_demo": FakeAdapter()},
        repo=repo,
    )
    cmd = repo.get_pending_batch()[0]
    gw.process(cmd, account_id="acc_1")

    events = _get_exchange_events(ops_db, chain_id=1)
    assert len(events) == 1
    assert events[0][0] == "STOP_MOVED_CONFIRMED"
    assert events[0][1]["is_breakeven"] is True
    assert events[0][1]["new_stop_price"] == 50000.0
    assert events[0][1]["command_id"] == 5001

    conn = sqlite3.connect(ops_db)
    status = conn.execute(
        "SELECT status FROM ops_execution_commands WHERE command_id=5001"
    ).fetchone()[0]
    conn.close()
    assert status == "DONE"


def test_move_stop_emits_stop_moved_confirmed_not_breakeven(ops_db):
    """MOVE_STOP con retCode=0 → STOP_MOVED_CONFIRMED con is_breakeven=False."""
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_cmd(ops_db, 5002, cmd_type="MOVE_STOP", payload={
        "symbol": "BTC/USDT", "side": "LONG", "new_stop_price": 48000.0,
    })
    repo = GatewayCommandRepository(ops_db)
    gw = ExecutionGateway(
        config=ExecutionConfigLoader("config/execution.yaml").load(),
        adapter_registry={"bybit_demo": FakeAdapter()},
        repo=repo,
    )
    cmd = repo.get_pending_batch()[0]
    gw.process(cmd, account_id="acc_1")

    events = _get_exchange_events(ops_db, chain_id=1)
    assert len(events) == 1
    assert events[0][0] == "STOP_MOVED_CONFIRMED"
    assert events[0][1]["is_breakeven"] is False
    assert events[0][1]["new_stop_price"] == 48000.0


def test_sync_protective_orders_emits_protective_orders_synced(ops_db):
    """SYNC_PROTECTIVE_ORDERS con retCode=0 → PROTECTIVE_ORDERS_SYNCED."""
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_cmd(ops_db, 5003, cmd_type="SYNC_PROTECTIVE_ORDERS", payload={
        "symbol": "BTC/USDT", "side": "LONG",
    })
    repo = GatewayCommandRepository(ops_db)
    gw = ExecutionGateway(
        config=ExecutionConfigLoader("config/execution.yaml").load(),
        adapter_registry={"bybit_demo": FakeAdapter()},
        repo=repo,
    )
    cmd = repo.get_pending_batch()[0]
    gw.process(cmd, account_id="acc_1")

    events = _get_exchange_events(ops_db, chain_id=1)
    assert len(events) == 1
    assert events[0][0] == "PROTECTIVE_ORDERS_SYNCED"
    assert events[0][1]["command_id"] == 5003


def test_fire_and_forget_failed_does_not_emit_event(ops_db):
    """Se place_order() fallisce, nessun evento lifecycle viene inserito."""
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_cmd(ops_db, 5004, cmd_type="MOVE_STOP_TO_BREAKEVEN", payload={
        "symbol": "BTC/USDT", "side": "LONG", "new_stop_price": 50000.0,
    })
    repo = GatewayCommandRepository(ops_db)
    gw = ExecutionGateway(
        config=ExecutionConfigLoader("config/execution.yaml").load(),
        adapter_registry={"bybit_demo": FakeAdapter(fail_on={"MOVE_STOP_TO_BREAKEVEN"})},
        repo=repo,
    )
    cmd = repo.get_pending_batch()[0]
    gw.process(cmd, account_id="acc_1")

    events = _get_exchange_events(ops_db, chain_id=1)
    assert events == [], f"Expected no events on failure, got {events}"

    conn = sqlite3.connect(ops_db)
    status = conn.execute(
        "SELECT status FROM ops_execution_commands WHERE command_id=5004"
    ).fetchone()[0]
    conn.close()
    assert status == "FAILED"


def test_set_tpsl_does_not_emit_direct_event(ops_db):
    """SET_POSITION_TPSL_PARTIAL non emette eventi lifecycle diretti.
    Il suo hit è rilevato separatamente da watchMyTrades/polling."""
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    conn = sqlite3.connect(ops_db)
    conn.execute("UPDATE ops_trade_chains SET filled_entry_qty=0.01 WHERE trade_chain_id=1")
    conn.commit()
    conn.close()

    _insert_cmd(ops_db, 5005, cmd_type="SET_POSITION_TPSL_PARTIAL", payload={
        "symbol": "BTC/USDT", "side": "LONG",
        "take_profit": 70000.0, "tp_size": 0.005,
        "tp_sequence": 1, "tp_order_type": "Limit",
        "tp_limit_price": 70000.0, "tp_trigger_by": "MarkPrice",
        "preserve_sl": True,
    })
    repo = GatewayCommandRepository(ops_db)
    gw = ExecutionGateway(
        config=ExecutionConfigLoader("config/execution.yaml").load(),
        adapter_registry={"bybit_demo": FakeAdapter()},
        repo=repo,
    )
    cmd = repo.get_pending_batch()[0]
    gw.process(cmd, account_id="acc_1")

    events = _get_exchange_events(ops_db, chain_id=1)
    assert events == [], f"SET_POSITION_TPSL_PARTIAL non deve emettere eventi diretti, got {events}"

    conn = sqlite3.connect(ops_db)
    status = conn.execute(
        "SELECT status FROM ops_execution_commands WHERE command_id=5005"
    ).fetchone()[0]
    conn.close()
    assert status == "DONE"
```

- [ ] **Step 2: Esegui per verificare che falliscono**

```
pytest tests/runtime_v2/execution_gateway/test_gateway.py::test_move_stop_to_be_emits_stop_moved_confirmed tests/runtime_v2/execution_gateway/test_gateway.py::test_sync_protective_orders_emits_protective_orders_synced -v
```

Expected: FAIL — `ExecutionGateway` non ha ancora `_FIRE_AND_FORGET_EVENTS` né `_emit_confirmed_event()`.

- [ ] **Step 3: Aggiungi `_FIRE_AND_FORGET_EVENTS` in `gateway.py`**

In `src/runtime_v2/execution_gateway/gateway.py`, dopo la definizione di `_FIRE_AND_FORGET` (linea ~46-54), aggiungi:

```python
# Mappa comando fire-and-forget → evento lifecycle da emettere dopo retCode=0.
# SET_POSITION_TPSL_* esclusi: il loro hit viene rilevato da watchMyTrades/polling.
# CANCEL_PENDING_ENTRY escluso: conferma arriva via PENDING_ENTRY_CANCELLED_CONFIRMED.
_FIRE_AND_FORGET_EVENTS: dict[str, str] = {
    "MOVE_STOP_TO_BREAKEVEN": "STOP_MOVED_CONFIRMED",
    "MOVE_STOP":               "STOP_MOVED_CONFIRMED",
    "SYNC_PROTECTIVE_ORDERS":  "PROTECTIVE_ORDERS_SYNCED",
}
```

- [ ] **Step 4: Aggiungi `_build_confirmed_event_payload()` e `_emit_confirmed_event()` in `ExecutionGateway`**

In `src/runtime_v2/execution_gateway/gateway.py`, dentro la classe `ExecutionGateway`, dopo `__init__()` e prima di `process()`, aggiungi i due metodi:

```python
def _build_confirmed_event_payload(
    self, cmd: ExecutionCommand, event_type: str, payload: dict
) -> dict:
    """Costruisce il payload dell'evento lifecycle per operazioni fire-and-forget sincrone."""
    if event_type == "STOP_MOVED_CONFIRMED":
        return {
            "new_stop_price": payload.get("new_stop_price"),
            "is_breakeven": cmd.command_type == "MOVE_STOP_TO_BREAKEVEN",
            "command_id": cmd.command_id,
        }
    # PROTECTIVE_ORDERS_SYNCED (e future estensioni)
    return {"command_id": cmd.command_id}

def _emit_confirmed_event(
    self, cmd: ExecutionCommand, event_type: str, payload: dict
) -> None:
    """INSERT OR IGNORE in ops_exchange_events. Chiave: {event_type}:{chain_id}:{command_id}."""
    idempotency_key = f"{event_type}:{cmd.trade_chain_id}:{cmd.command_id}"
    self._repo.insert_exchange_event(
        trade_chain_id=cmd.trade_chain_id,
        event_type=event_type,
        payload_json=json.dumps(payload),
        idempotency_key=idempotency_key,
    )
```

- [ ] **Step 5: Aggiorna il blocco fire-and-forget in `process()`**

In `src/runtime_v2/execution_gateway/gateway.py`, trova le linee (circa 253-254):

```python
        if cmd.command_type in _FIRE_AND_FORGET:
            self._repo.mark_done(cmd.command_id)
```

Sostituisci con:

```python
        if cmd.command_type in _FIRE_AND_FORGET:
            event_type = _FIRE_AND_FORGET_EVENTS.get(cmd.command_type)
            if event_type:
                event_payload = self._build_confirmed_event_payload(cmd, event_type, payload)
                self._emit_confirmed_event(cmd, event_type, event_payload)
            self._repo.mark_done(cmd.command_id)
```

- [ ] **Step 6: Esegui i nuovi test gateway**

```
pytest tests/runtime_v2/execution_gateway/test_gateway.py -k "fire_and_forget or move_stop or sync_protective or set_tpsl" -v
```

Expected: tutti e 5 i nuovi test PASS.

- [ ] **Step 7: Verifica nessuna regressione**

```
pytest tests/runtime_v2/execution_gateway/test_gateway.py -v
```

Expected: tutti i test passano.

- [ ] **Step 8: Commit**

```bash
git add src/runtime_v2/execution_gateway/gateway.py \
        tests/runtime_v2/execution_gateway/test_gateway.py
git commit -m "feat(gateway): emit STOP_MOVED_CONFIRMED and PROTECTIVE_ORDERS_SYNCED after retCode=0"
```

---

## Task 5 — Repository: metodi per watchMyTrades

`BybitWsFillWatcher` ha bisogno di due nuovi metodi nel repository per fare matching fill↔chain.

**Files:**
- Modify: `src/runtime_v2/execution_gateway/repositories.py`

- [ ] **Step 1: Aggiungi `get_active_tp_commands()`**

In `src/runtime_v2/execution_gateway/repositories.py`, dopo `insert_exchange_event()`, aggiungi:

```python
def get_active_tp_commands(self, trade_chain_id: int) -> list[dict]:
    """Payload dei SET_POSITION_TPSL_* SENT/DONE per chain OPEN/PARTIALLY_CLOSED.

    Usato da watchMyTrades per confrontare il fill price con i TP attivi.
    """
    conn = sqlite3.connect(self._db)
    try:
        rows = conn.execute(
            "SELECT c.payload_json "
            "FROM ops_execution_commands c "
            "JOIN ops_trade_chains t ON c.trade_chain_id = t.trade_chain_id "
            "WHERE c.trade_chain_id = ? "
            "AND c.command_type IN ('SET_POSITION_TPSL_PARTIAL', 'SET_POSITION_TPSL_FULL') "
            "AND c.status IN ('SENT', 'DONE') "
            "AND t.lifecycle_state IN ('OPEN', 'PARTIALLY_CLOSED')",
            (trade_chain_id,),
        ).fetchall()
        result = []
        for (payload_json,) in rows:
            try:
                result.append(json.loads(payload_json))
            except Exception:
                pass
        return result
    finally:
        conn.close()
```

- [ ] **Step 2: Aggiungi `get_open_chains_for_symbol()`**

Subito dopo `get_active_tp_commands()`, aggiungi:

```python
def get_open_chains_for_symbol(self, symbol: str, side: str) -> list[int]:
    """Lista di trade_chain_id OPEN/PARTIALLY_CLOSED per symbol+side.

    Usato da watchMyTrades per trovare le chain candidate per un fill TP.
    `side` è il lato della posizione (LONG/SHORT), non il lato del fill.
    """
    conn = sqlite3.connect(self._db)
    try:
        rows = conn.execute(
            "SELECT trade_chain_id FROM ops_trade_chains "
            "WHERE symbol=? AND side=? "
            "AND lifecycle_state IN ('OPEN', 'PARTIALLY_CLOSED')",
            (symbol, side),
        ).fetchall()
        return [int(r[0]) for r in rows]
    finally:
        conn.close()
```

- [ ] **Step 3: Esegui tutti i test per verificare nessuna regressione**

```
pytest tests/runtime_v2/execution_gateway/ -v
```

Expected: tutti i test passano.

- [ ] **Step 4: Commit**

```bash
git add src/runtime_v2/execution_gateway/repositories.py
git commit -m "feat(repositories): add get_active_tp_commands and get_open_chains_for_symbol"
```

---

## Task 6 — watchMyTrades: ristrutturazione task management

Prima di aggiungere `_watch_trades_forever()`, bisogna ristrutturare `_run_in_thread()` per gestire due task asincroni paralleli.

**Files:**
- Modify: `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/ws_fill_watcher.py`

- [ ] **Step 1: Aggiorna `__init__()` — sostituisci `_watch_task` con due task separati**

In `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/ws_fill_watcher.py`, nel metodo `__init__()`, trova:

```python
        self._watch_task: asyncio.Task | None = None
```

Sostituisci con:

```python
        self._watch_orders_task: asyncio.Task | None = None
        self._watch_trades_task: asyncio.Task | None = None
```

- [ ] **Step 2: Aggiorna `_run_in_thread()` per `asyncio.gather()` su due task**

Sostituisci il corpo di `_run_in_thread()`:

**Prima:**
```python
    def _run_in_thread(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        self._loop_ready.set()
        self._watch_task = loop.create_task(self._watch_orders_forever())
        try:
            loop.run_until_complete(self._watch_task)
        except asyncio.CancelledError:
            pass
        finally:
            pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self._watch_task = None
            self._loop = None
            loop.close()
```

**Dopo:**
```python
    def _run_in_thread(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        self._watch_orders_task = loop.create_task(self._watch_orders_forever())
        self._watch_trades_task = loop.create_task(self._watch_trades_forever())
        self._loop_ready.set()
        try:
            loop.run_until_complete(
                asyncio.gather(
                    self._watch_orders_task,
                    self._watch_trades_task,
                    return_exceptions=True,
                )
            )
        except asyncio.CancelledError:
            pass
        finally:
            pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self._watch_orders_task = None
            self._watch_trades_task = None
            self._loop = None
            loop.close()
```

- [ ] **Step 3: Aggiorna `_cancel_watch_task()` per cancellare entrambi i task**

**Prima:**
```python
    def _cancel_watch_task(self) -> None:
        if self._watch_task is not None and not self._watch_task.done():
            self._watch_task.cancel()
```

**Dopo:**
```python
    def _cancel_watch_task(self) -> None:
        for task in (self._watch_orders_task, self._watch_trades_task):
            if task is not None and not task.done():
                task.cancel()
```

- [ ] **Step 4: Aggiungi `_watch_trades_forever()` come stub che non fa nulla per ora**

Questo step mette in produzione la struttura senza la logica di matching. Dopo `_watch_orders_forever()`, aggiungi:

```python
    async def _watch_trades_forever(self) -> None:
        """Ascolta watchMyTrades per rilevare fill TP position-level (Mode C).

        I TP impostati via SET_POSITION_TPSL_* non hanno clientOrderId e non
        sono visibili in watchOrders. watchMyTrades riceve tutti i fill inclusi
        quelli position-level.
        """
        exchange = self._build_exchange()
        try:
            while not self._stop_event.is_set():
                try:
                    trades = await exchange.watch_my_trades()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    if self._stop_event.is_set():
                        break
                    logger.exception("bybit watch_my_trades failed")
                    await asyncio.sleep(5)
                    continue
                self._process_trade_batch(trades)
        finally:
            await exchange.close()

    def _process_trade_batch(self, trades: list[dict] | None) -> None:
        """Stub: implementazione completa in Task 7."""
        pass
```

- [ ] **Step 5: Verifica che i test esistenti passino ancora**

```
pytest tests/runtime_v2/execution_gateway/ -v
```

Expected: tutti i test passano (lo stub non rompe nulla).

- [ ] **Step 6: Commit**

```bash
git add src/runtime_v2/execution_gateway/adapters/ccxt_bybit/ws_fill_watcher.py
git commit -m "refactor(ws_fill_watcher): two parallel async tasks, add watch_trades_forever stub"
```

---

## Task 7 — watchMyTrades: matching e `_save_tp_fill_from_trade()`

Implementa la logica di matching fill→chain e il salvataggio dell'evento `TP_FILLED` con dati reali.

**Files:**
- Modify: `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/ws_fill_watcher.py`
- Create: `tests/runtime_v2/execution_gateway/test_ws_fill_watcher.py`

- [ ] **Step 1: Crea il file di test `test_ws_fill_watcher.py`**

Crea `tests/runtime_v2/execution_gateway/test_ws_fill_watcher.py`:

```python
# tests/runtime_v2/execution_gateway/test_ws_fill_watcher.py
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest


def _apply_migrations(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    for f in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


def _insert_open_chain(
    db_path: str,
    chain_id: int,
    symbol: str = "BTC/USDT:USDT",
    side: str = "LONG",
    open_qty: float = 0.01,
) -> None:
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_trade_chains "
        "(trade_chain_id, source_enrichment_id, canonical_message_id, raw_message_id, "
        "trader_id, account_id, symbol, side, lifecycle_state, entry_mode, "
        "management_plan_json, open_position_qty, filled_entry_qty, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (chain_id, chain_id, chain_id, chain_id, "t1", "acc",
         symbol, side, "OPEN", "ONE_SHOT", "{}", open_qty, open_qty, now, now),
    )
    conn.commit()
    conn.close()


def _insert_tp_command(
    db_path: str,
    chain_id: int,
    cmd_id: int,
    tp_price: float,
    tp_level: int = 1,
    tp_size: float = 0.005,
) -> None:
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    payload = json.dumps({
        "symbol": "BTC/USDT:USDT",
        "side": "LONG",
        "take_profit": tp_price,
        "tp_size": tp_size,
        "tp_sequence": tp_level,
        "tp_order_type": "Limit",
        "tp_limit_price": tp_price,
        "tp_trigger_by": "MarkPrice",
        "preserve_sl": True,
    })
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_execution_commands "
        "(command_id, trade_chain_id, command_type, status, payload_json, "
        "idempotency_key, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (cmd_id, chain_id, "SET_POSITION_TPSL_PARTIAL", "DONE",
         payload, f"idem_tp:{cmd_id}", now, now),
    )
    conn.commit()
    conn.close()


@pytest.fixture
def ops_db(tmp_path):
    db = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db)
    return db


def _make_watcher(ops_db: str):
    from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.ws_fill_watcher import BybitWsFillWatcher
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository
    repo = GatewayCommandRepository(ops_db)
    return BybitWsFillWatcher(
        api_key="k", api_secret="s", testnet=False,
        ops_db_path=ops_db, repo=repo,
    )


# ── _save_tp_fill_from_trade ──────────────────────────────────────────────────

def test_save_tp_fill_from_trade_inserts_event(ops_db):
    """_save_tp_fill_from_trade() inserisce TP_FILLED con dati reali e chiave level:N."""
    watcher = _make_watcher(ops_db)
    watcher._save_tp_fill_from_trade(
        chain_id=1, tp_level=1,
        fill_price=67350.5, filled_qty=0.01,
        is_final=True, exchange_trade_id="trade-xyz",
    )

    conn = sqlite3.connect(ops_db)
    rows = conn.execute(
        "SELECT event_type, payload_json, idempotency_key FROM ops_exchange_events"
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    event_type, payload_json, ikey = rows[0]
    assert event_type == "TP_FILLED"
    assert ikey == "TP_FILLED:1:level:1"
    p = json.loads(payload_json)
    assert p["fill_price"] == 67350.5
    assert p["filled_qty"] == 0.01
    assert p["is_final"] is True
    assert p["tp_level"] == 1
    assert p["source"] == "watch_my_trades"
    assert p["exchange_trade_id"] == "trade-xyz"


def test_save_tp_fill_from_trade_idempotent(ops_db):
    """Due chiamate con stesso chain+level → esattamente 1 riga (INSERT OR IGNORE)."""
    watcher = _make_watcher(ops_db)
    watcher._save_tp_fill_from_trade(1, 1, 67350.5, 0.01, True, "t1")
    watcher._save_tp_fill_from_trade(1, 1, 67360.0, 0.01, True, "t2")  # stesso livello

    conn = sqlite3.connect(ops_db)
    count = conn.execute("SELECT COUNT(*) FROM ops_exchange_events").fetchone()[0]
    conn.close()
    assert count == 1


# ── _save_fill() idempotency key per TP standalone ───────────────────────────

def test_save_fill_tp_uses_unified_key(ops_db):
    """_save_fill() per role=tp usa 'TP_FILLED:{chain}:level:{seq}' non exchange_order_id."""
    from src.runtime_v2.execution_gateway.models import RawAdapterOrder
    watcher = _make_watcher(ops_db)

    raw = RawAdapterOrder(
        client_order_id="tsb:42:999:tp:2",
        exchange_order_id="exch-order-123",
        status="FILLED",
        filled_qty=0.005,
        average_price=70000.0,
    )
    # Necessario per count_active_tps — aggiunge un TP DONE
    _insert_open_chain(ops_db, 42)
    _insert_tp_command(ops_db, 42, 9990, tp_price=70000.0, tp_level=2)

    watcher._save_fill("tsb:42:999:tp:2", raw)

    conn = sqlite3.connect(ops_db)
    rows = conn.execute(
        "SELECT idempotency_key FROM ops_exchange_events WHERE trade_chain_id=42"
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0][0] == "TP_FILLED:42:level:2"


# ── _process_trade_batch matching ────────────────────────────────────────────

def test_process_trade_batch_matched_tp_inserts_event(ops_db):
    """Trade reduceOnly con price che matcha TP attivo → TP_FILLED inserito."""
    _insert_open_chain(ops_db, 10, symbol="BTC/USDT:USDT", side="LONG")
    _insert_tp_command(ops_db, 10, 1001, tp_price=67000.0, tp_level=1)

    watcher = _make_watcher(ops_db)
    # Fill sell (close LONG): price 67005.0 è entro ±1% di 67000.0
    trades = [{
        "symbol": "BTC/USDT:USDT",
        "side": "sell",
        "price": 67005.0,
        "amount": 0.005,
        "reduceOnly": True,
        "id": "trade-001",
        "info": {"posQty": "0.005"},  # posizione residua non zero
    }]
    watcher._process_trade_batch(trades)

    conn = sqlite3.connect(ops_db)
    rows = conn.execute(
        "SELECT event_type, payload_json, idempotency_key FROM ops_exchange_events WHERE trade_chain_id=10"
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0][0] == "TP_FILLED"
    assert rows[0][2] == "TP_FILLED:10:level:1"
    p = json.loads(rows[0][1])
    assert p["fill_price"] == 67005.0
    assert p["filled_qty"] == 0.005
    assert p["is_final"] is False  # posQty=0.005 > 0
    assert p["source"] == "watch_my_trades"


def test_process_trade_batch_is_final_true_when_pos_qty_zero(ops_db):
    """`posQty=0` nel trade → is_final=True."""
    _insert_open_chain(ops_db, 11, symbol="ETH/USDT:USDT", side="LONG")
    _insert_tp_command(ops_db, 11, 1101, tp_price=3200.0, tp_level=1)

    watcher = _make_watcher(ops_db)
    trades = [{
        "symbol": "ETH/USDT:USDT",
        "side": "sell",
        "price": 3201.0,
        "amount": 0.1,
        "reduceOnly": True,
        "id": "t-eth-001",
        "info": {"posQty": "0"},  # posizione azzerata
    }]
    watcher._process_trade_batch(trades)

    conn = sqlite3.connect(ops_db)
    p = json.loads(conn.execute(
        "SELECT payload_json FROM ops_exchange_events WHERE trade_chain_id=11"
    ).fetchone()[0])
    conn.close()
    assert p["is_final"] is True


def test_process_trade_batch_is_final_false_fallback_when_no_pos_qty(ops_db):
    """`posQty` assente → is_final=False (conservativo)."""
    _insert_open_chain(ops_db, 12, symbol="SOL/USDT:USDT", side="LONG")
    _insert_tp_command(ops_db, 12, 1201, tp_price=160.0, tp_level=1)

    watcher = _make_watcher(ops_db)
    trades = [{
        "symbol": "SOL/USDT:USDT",
        "side": "sell",
        "price": 160.5,
        "amount": 5.0,
        "reduceOnly": True,
        "id": "t-sol-001",
        "info": {},  # posQty non presente
    }]
    watcher._process_trade_batch(trades)

    conn = sqlite3.connect(ops_db)
    p = json.loads(conn.execute(
        "SELECT payload_json FROM ops_exchange_events WHERE trade_chain_id=12"
    ).fetchone()[0])
    conn.close()
    assert p["is_final"] is False


def test_process_trade_batch_ignores_non_reduce_only(ops_db):
    """Fill NON reduceOnly (entry, SL) → ignorato, nessun evento inserito."""
    _insert_open_chain(ops_db, 13)
    _insert_tp_command(ops_db, 13, 1301, tp_price=70000.0)

    watcher = _make_watcher(ops_db)
    trades = [{
        "symbol": "BTC/USDT:USDT",
        "side": "buy",
        "price": 70000.0,
        "amount": 0.01,
        "reduceOnly": False,  # ← entry fill, non TP
        "id": "t-entry",
        "info": {},
    }]
    watcher._process_trade_batch(trades)

    conn = sqlite3.connect(ops_db)
    count = conn.execute("SELECT COUNT(*) FROM ops_exchange_events").fetchone()[0]
    conn.close()
    assert count == 0


def test_process_trade_batch_ambiguous_skipped(ops_db):
    """2 chain con TP a prezzi simili (entro ±1%) → skip silenzioso, nessun INSERT."""
    # Chain 20 con TP a 70000
    _insert_open_chain(ops_db, 20, symbol="BTC/USDT:USDT", side="LONG")
    _insert_tp_command(ops_db, 20, 2001, tp_price=70000.0, tp_level=1)
    # Chain 21 con TP a 70050 (entro 1% da 70100)
    _insert_open_chain(ops_db, 21, symbol="BTC/USDT:USDT", side="LONG")
    _insert_tp_command(ops_db, 21, 2101, tp_price=70050.0, tp_level=1)

    watcher = _make_watcher(ops_db)
    # fill a 70100: entro 1% sia da 70000 (0.14%) che da 70050 (0.07%) → ambiguo
    trades = [{
        "symbol": "BTC/USDT:USDT",
        "side": "sell",
        "price": 70100.0,
        "amount": 0.005,
        "reduceOnly": True,
        "id": "t-ambig",
        "info": {"posQty": "0.005"},
    }]
    watcher._process_trade_batch(trades)

    conn = sqlite3.connect(ops_db)
    count = conn.execute("SELECT COUNT(*) FROM ops_exchange_events").fetchone()[0]
    conn.close()
    assert count == 0, "Match ambiguo: nessun evento deve essere inserito"
```

- [ ] **Step 2: Esegui i test per verificare che falliscono**

```
pytest tests/runtime_v2/execution_gateway/test_ws_fill_watcher.py -v
```

Expected: FAIL su `_save_tp_fill_from_trade`, `_process_trade_batch` — metodi non ancora implementati (stub).

- [ ] **Step 3: Implementa `_save_tp_fill_from_trade()`**

In `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/ws_fill_watcher.py`, dopo `_base_fill_payload()`, aggiungi:

```python
    def _save_tp_fill_from_trade(
        self,
        chain_id: int,
        tp_level: int,
        fill_price: float,
        filled_qty: float,
        is_final: bool,
        exchange_trade_id: str,
    ) -> None:
        """INSERT OR IGNORE di TP_FILLED con dati reali da watchMyTrades."""
        idempotency_key = f"TP_FILLED:{chain_id}:level:{tp_level}"
        payload = json.dumps({
            "tp_level": tp_level,
            "is_final": is_final,
            "fill_price": fill_price,
            "filled_qty": filled_qty,
            "source": "watch_my_trades",
            "exchange_trade_id": exchange_trade_id,
        })
        conn = sqlite3.connect(self._ops_db_path)
        try:
            conn.execute(
                "INSERT OR IGNORE INTO ops_exchange_events "
                "(trade_chain_id, event_type, payload_json, processing_status, "
                "idempotency_key, received_at) VALUES (?,?,?,?,?,?)",
                (chain_id, "TP_FILLED", payload, "NEW", idempotency_key, _now()),
            )
            conn.commit()
        finally:
            conn.close()
```

- [ ] **Step 4: Esegui i test `_save_tp_fill_from_trade`**

```
pytest tests/runtime_v2/execution_gateway/test_ws_fill_watcher.py::test_save_tp_fill_from_trade_inserts_event tests/runtime_v2/execution_gateway/test_ws_fill_watcher.py::test_save_tp_fill_from_trade_idempotent -v
```

Expected: PASS

- [ ] **Step 5: Implementa `_process_trade_batch()` con matching completo**

Sostituisci lo stub `_process_trade_batch()` con l'implementazione completa:

```python
    def _process_trade_batch(self, trades: list[dict] | None) -> None:
        """Elabora batch di trade da watchMyTrades. Inserisce TP_FILLED per fill position-level.

        Logica:
        1. Filtra su reduceOnly=True (solo chiusure di posizione, non entry/SL)
        2. Determina il lato della posizione (fill sell → posizione LONG)
        3. Cerca chain aperte con stesso symbol+side
        4. Confronta fill price con TP attivi (tolerance ±1%)
        5. Match univoco → INSERT TP_FILLED. Match ambiguo → skip.
        """
        if not trades:
            return
        for trade in trades:
            try:
                self._match_and_save_tp_fill(trade)
            except Exception:
                logger.exception("error processing trade %s", trade.get("id"))

    def _match_and_save_tp_fill(self, trade: dict) -> None:
        """Matching singolo trade → chain + tp_level. Inserisce TP_FILLED se match univoco."""
        # Solo fill che chiudono posizione (TP chiude, entry/SL apre o aggiusta)
        if not trade.get("reduceOnly", False):
            return

        symbol = trade.get("symbol", "")
        side = trade.get("side", "")  # "sell" per close LONG, "buy" per close SHORT
        fill_price_raw = trade.get("price")
        filled_qty_raw = trade.get("amount")
        if not symbol or not side or fill_price_raw is None:
            return

        fill_price = float(fill_price_raw)
        filled_qty = float(filled_qty_raw or 0.0)
        if fill_price <= 0.0:
            return

        # Lato della posizione (opposto al lato del fill di chiusura)
        chain_side = "LONG" if side.lower() == "sell" else "SHORT"

        # Trova chain aperte con stesso symbol+side
        open_chain_ids = self._repo.get_open_chains_for_symbol(symbol, chain_side)
        if not open_chain_ids:
            return

        # Confronta fill price con TP attivi di ogni chain (tolerance ±1%)
        matches: list[tuple[int, int]] = []  # (chain_id, tp_level)
        for chain_id in open_chain_ids:
            tp_commands = self._repo.get_active_tp_commands(chain_id)
            for cmd_payload in tp_commands:
                tp_price_raw = cmd_payload.get("take_profit")
                if tp_price_raw is None:
                    continue
                tp_price = float(tp_price_raw)
                if tp_price <= 0.0:
                    continue
                if abs(fill_price - tp_price) / tp_price <= 0.01:  # ±1% tolerance
                    tp_level = int(cmd_payload.get("tp_sequence", 1))
                    matches.append((chain_id, tp_level))

        if len(matches) != 1:
            if len(matches) > 1:
                logger.warning(
                    "ambiguous TP fill match: symbol=%s price=%.4f matches=%s — skipping",
                    symbol, fill_price, matches,
                )
            return

        chain_id, tp_level = matches[0]

        # is_final da posQty Bybit se disponibile, fallback False (conservativo)
        pos_qty_raw = trade.get("info", {}).get("posQty")
        if pos_qty_raw is not None:
            try:
                is_final = float(pos_qty_raw) == 0.0
            except (ValueError, TypeError):
                is_final = False
        else:
            is_final = False

        exchange_trade_id = str(trade.get("id") or "")
        self._save_tp_fill_from_trade(
            chain_id=chain_id,
            tp_level=tp_level,
            fill_price=fill_price,
            filled_qty=filled_qty,
            is_final=is_final,
            exchange_trade_id=exchange_trade_id,
        )
```

- [ ] **Step 6: Esegui tutti i test del file**

```
pytest tests/runtime_v2/execution_gateway/test_ws_fill_watcher.py -v
```

Expected: tutti i test PASS.

- [ ] **Step 7: Verifica nessuna regressione su tutta la suite**

```
pytest tests/runtime_v2/ -v
```

Expected: tutti i test passano. Annota il numero totale (baseline: ≥273 passed).

- [ ] **Step 8: Commit**

```bash
git add src/runtime_v2/execution_gateway/adapters/ccxt_bybit/ws_fill_watcher.py \
        tests/runtime_v2/execution_gateway/test_ws_fill_watcher.py
git commit -m "feat(ws_fill_watcher): add watchMyTrades TP fill detection for Mode C"
```

---

## Task 8 — Verifica finale acceptance criteria

- [ ] **Step 1: Esegui la suite completa runtime_v2**

```
pytest tests/runtime_v2/ -v --tb=short
```

Expected: tutti i test passano, 0 failures.

- [ ] **Step 2: Verifica AC1-AC4 sui test automatici**

| AC | Test | Verifica |
|----|------|---------|
| AC1 | `test_move_stop_to_be_emits_stop_moved_confirmed` | PASS |
| AC2 | Non coperto da test unitari — richiede test integrazione lifecycle (fuori scope) | N/A |
| AC3 | `test_sync_protective_orders_emits_protective_orders_synced` | PASS |
| AC4 | `test_tp_filled_ws_and_polling_unified_key_no_duplicate` | PASS |
| AC6 | `test_process_trade_batch_ambiguous_skipped` | PASS |
| AC7 | Tutti i test runtime_v2 | PASS |

- [ ] **Step 3: Verifica AC5 (live demo — opzionale in CI)**

Connetti a Bybit Demo. Apri una posizione. Imposta TP via `SET_POSITION_TPSL_PARTIAL`. Attendi il fill del TP. Verifica nei log che `TP_FILLED` appaia con `source=watch_my_trades` e `fill_price` reale entro ~1s.

- [ ] **Step 4: Commit finale se tutti i test passano**

```bash
git add -A
git commit -m "feat: lifecycle verification gaps — STOP_MOVED, TP sync, watchMyTrades"
```

---

## Riepilogo acceptance criteria

| # | Criterio | Come verificare |
|---|----------|----------------|
| AC1 | Dopo `MOVE_STOP_TO_BREAKEVEN` retCode=0: `ops_exchange_events` ha `STOP_MOVED_CONFIRMED` senza chiamate aggiuntive | `test_move_stop_to_be_emits_stop_moved_confirmed` |
| AC3 | Dopo `SYNC_PROTECTIVE_ORDERS` retCode=0: `ops_exchange_events` ha `PROTECTIVE_ORDERS_SYNCED` | `test_sync_protective_orders_emits_protective_orders_synced` |
| AC4 | WS e polling usano stessa idempotency key TP → 1 sola riga | `test_tp_filled_ws_and_polling_unified_key_no_duplicate` |
| AC5 | watchMyTrades inserisce `TP_FILLED` con dati reali entro ~1s | verifica live demo |
| AC6 | Match ambiguo → skip silenzioso | `test_process_trade_batch_ambiguous_skipped` |
| AC7 | Nessuna regressione | `pytest tests/runtime_v2/` |
