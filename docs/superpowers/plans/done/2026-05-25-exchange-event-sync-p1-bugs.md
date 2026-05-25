# Exchange Event Sync — P1 Bug Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fixare i 3 bug P1 che compongono la catena deferred BE: payload incompleto su cancel, fallback match per sequence nel processor, expand CANCEL_PENDING_ENTRY in workers._persist_result.

**Architecture:** BUG-1 aggiunge `cancelled_order_ids` e `sequence` al payload in `event_sync.py`. BUG-3 estrae `expand_cancel_pending_commands` da `entry_gate.py` in `cancel_expander.py` e lo chiama da `workers.py::_persist_result`. BUG-2 aggiunge il fallback `_mark_entry_leg_status_by_sequence` in `event_processor.py` e aggiorna i test con payload production-realistico.

**Tech Stack:** Python 3.12, pytest, sqlite3 inline, Pydantic v2

---

## File Map

| File | Azione |
|------|--------|
| `src/runtime_v2/execution_gateway/event_sync.py` | Modifica — BUG-1: aggiungere `cancelled_order_ids` e `sequence` al payload di `_handle_cancelled_order` |
| `src/runtime_v2/lifecycle/cancel_expander.py` | **NUOVO** — BUG-3: `expand_cancel_pending_commands` + `load_pending_entry_client_order_ids` |
| `src/runtime_v2/lifecycle/entry_gate.py` | Refactor — BUG-3: sostituire funzioni private bottom con import da `cancel_expander` |
| `src/runtime_v2/lifecycle/workers.py` | Modifica — BUG-3: import da `cancel_expander`, rimuovere duplicato locale, aggiornare `_persist_result` |
| `src/runtime_v2/lifecycle/event_processor.py` | Modifica — BUG-2: aggiungere `_mark_entry_leg_status_by_sequence` + fallback nel handler |
| `tests/runtime_v2/execution_gateway/test_event_sync.py` | Aggiornamento — BUG-1: aggiungere assert su `cancelled_order_ids` e `sequence` nel test esistente |
| `tests/runtime_v2/lifecycle/test_event_processor.py` | Aggiornamento — BUG-2: aggiornare test deferred BE con payload production-realistico + aggiungere test fallback-by-sequence |
| `tests/runtime_v2/lifecycle/test_cancel_expander.py` | **NUOVO** — BUG-3: test unitari di `expand_cancel_pending_commands` |

---

## Task 1: BUG-1 — Aggiungere `cancelled_order_ids` e `sequence` al payload di `_handle_cancelled_order`

**Files:**
- Modify: `src/runtime_v2/execution_gateway/event_sync.py:273-277`
- Test: `tests/runtime_v2/execution_gateway/test_event_sync.py`

### Contesto

In `_handle_cancelled_order`, il payload attuale è:
```python
payload = json.dumps({
    "command_id":            coid.command_id,
    "position_already_open": position_already_open,
    "cancel_reason":         raw.cancel_reason,
})
```

Il processor downstream (`_process_pending_entry_cancelled_confirmed`) ha bisogno di:
- `cancelled_order_ids`: lista con il `client_order_id` reale (formato `tsb:chain:cmd:role:seq`)
- `sequence`: numero di leg (`coid.sequence`) per il fallback match nel processor

`coid` è già parsato da `coid_mod.parse(client_order_id)` con formato `tsb:{chain_id}:{cmd_id}:{role}:{seq}:{nonce}`, quindi `coid.sequence` è disponibile senza overhead.

- [ ] **Step 1.1: Scrivere il test che fallisce**

Aggiungere a `tests/runtime_v2/execution_gateway/test_event_sync.py` dopo il test `test_cancelled_entry_partial_fill_sets_position_already_open`:

```python
def test_cancelled_entry_payload_includes_cancelled_order_ids_and_sequence(ops_db):
    """_handle_cancelled_order deve includere cancelled_order_ids e sequence nel payload."""
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.models import RawAdapterOrder
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    # client_order_id in formato tsb:chain:cmd:role:seq
    client_order_id = "tsb:10:5010:entry:2"
    _insert_sent_cmd(ops_db, 5010, 10, "PLACE_ENTRY", client_order_id)

    adapter = MagicMock()
    adapter.get_order_status.return_value = RawAdapterOrder(
        client_order_id=client_order_id,
        exchange_order_id="bybit-ex-0001",
        status="CANCELLED",
        filled_qty=0.0,
        average_price=None,
        cancel_reason="user_cancel",
    )
    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(
        ops_db_path=ops_db, adapter=adapter,
        repo=repo, execution_account_id="main",
    )

    worker.run_reconciliation()

    conn = sqlite3.connect(ops_db)
    row = conn.execute(
        "SELECT payload_json FROM ops_exchange_events "
        "WHERE trade_chain_id=10 AND event_type='PENDING_ENTRY_CANCELLED_CONFIRMED'"
    ).fetchone()
    conn.close()
    assert row is not None, "Evento non trovato"
    payload = json.loads(row[0])
    # BUG-1: questi campi devono essere presenti
    assert "cancelled_order_ids" in payload, "cancelled_order_ids assente dal payload"
    assert payload["cancelled_order_ids"] == [client_order_id]
    assert "sequence" in payload, "sequence assente dal payload"
    assert payload["sequence"] == 2  # seq estratto da tsb:10:5010:entry:2
```

- [ ] **Step 1.2: Eseguire il test per verificare che fallisce**

```bash
pytest tests/runtime_v2/execution_gateway/test_event_sync.py::test_cancelled_entry_payload_includes_cancelled_order_ids_and_sequence -v
```

Expected: `FAILED` — KeyError o AssertionError su `cancelled_order_ids`

- [ ] **Step 1.3: Applicare il fix a `event_sync.py`**

Aprire `src/runtime_v2/execution_gateway/event_sync.py`.

Trovare (linee 273-277):
```python
        payload = json.dumps({
            "command_id": coid.command_id,
            "position_already_open": position_already_open,
            "cancel_reason": raw.cancel_reason,
        })
```

Sostituire con:
```python
        payload = json.dumps({
            "command_id":            coid.command_id,
            "position_already_open": position_already_open,
            "cancel_reason":         raw.cancel_reason,
            "cancelled_order_ids":   [client_order_id],
            "sequence":              coid.sequence,
        })
```

- [ ] **Step 1.4: Eseguire il test per verificare che passa**

```bash
pytest tests/runtime_v2/execution_gateway/test_event_sync.py::test_cancelled_entry_payload_includes_cancelled_order_ids_and_sequence -v
```

Expected: `PASSED`

- [ ] **Step 1.5: Eseguire la suite completa del file per regressione**

```bash
pytest tests/runtime_v2/execution_gateway/test_event_sync.py -v
```

Expected: tutti `PASSED`

- [ ] **Step 1.6: Commit**

```bash
git add src/runtime_v2/execution_gateway/event_sync.py tests/runtime_v2/execution_gateway/test_event_sync.py
git commit -m "fix(event-sync): add cancelled_order_ids and sequence to PENDING_ENTRY_CANCELLED_CONFIRMED payload (BUG-1)"
```

---

## Task 2: BUG-3 — Creare `cancel_expander.py` e chiamarlo in `workers._persist_result`

**Files:**
- Create: `src/runtime_v2/lifecycle/cancel_expander.py`
- Modify: `src/runtime_v2/lifecycle/entry_gate.py` (importa da cancel_expander, rimuove funzioni locali)
- Modify: `src/runtime_v2/lifecycle/workers.py` (importa da cancel_expander, aggiorna `_persist_result`)
- Create: `tests/runtime_v2/lifecycle/test_cancel_expander.py`

### Contesto

`workers.py::_persist_result` scrive i comandi risultanti dal processor direttamente in DB **senza** espandere `CANCEL_PENDING_ENTRY` nelle sue varianti per-ordine. Questo significa che quando il processor emette un `CANCEL_PENDING_ENTRY` (es. da auto-cancel averaging), il gateway riceve un comando con `entry_client_order_id` placeholder invece dell'ID exchange reale.

`entry_gate.py` ha già `_expand_cancel_pending_commands` e `_load_pending_entry_client_order_ids` (linee 1431-1476) usate in `_persist_update`. Dobbiamo:
1. Estrarre in `cancel_expander.py` rendendole pubbliche
2. Aggiornare `entry_gate.py` per importare da lì
3. Aggiornare `workers.py` per importare e usare

`workers.py` ha già una propria copia privata di `_load_pending_entry_client_order_ids` (linee 25-38) che diventa ridondante.

- [ ] **Step 2.1: Scrivere i test unitari per `cancel_expander` (falliscono perché il file non esiste)**

Creare `tests/runtime_v2/lifecycle/test_cancel_expander.py`:

```python
# tests/runtime_v2/lifecycle/test_cancel_expander.py
from __future__ import annotations

import json
import sqlite3
from pathlib import Path


def _apply_migrations(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    for f in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


def _insert_place_entry_cmd(conn, cmd_id, chain_id, client_order_id, status="SENT"):
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO ops_execution_commands "
        "(command_id, trade_chain_id, command_type, status, payload_json, "
        "idempotency_key, client_order_id, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (cmd_id, chain_id, "PLACE_ENTRY", status, "{}",
         f"place_entry:{chain_id}:leg{cmd_id}", client_order_id, now, now),
    )


def test_expand_non_cancel_command_returns_original(tmp_path):
    """Comandi non CANCEL_PENDING_ENTRY vengono restituiti invariati."""
    from src.runtime_v2.lifecycle.cancel_expander import expand_cancel_pending_commands
    db = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db)
    conn = sqlite3.connect(db)
    results = expand_cancel_pending_commands(
        conn,
        trade_chain_id=1,
        command_type="SYNC_PROTECTIVE_ORDERS",
        payload_json='{"symbol": "BTC/USDT"}',
        idempotency_key="sync:1:42",
    )
    conn.close()
    assert results == [('{"symbol": "BTC/USDT"}', "sync:1:42")]


def test_expand_cancel_with_no_pending_entry_commands_returns_original(tmp_path):
    """Se non ci sono PLACE_ENTRY attivi, ritorna il comando originale invariato."""
    from src.runtime_v2.lifecycle.cancel_expander import expand_cancel_pending_commands
    db = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db)
    conn = sqlite3.connect(db)
    # Nessun PLACE_ENTRY inserito → lista vuota → fallback al comando originale
    results = expand_cancel_pending_commands(
        conn,
        trade_chain_id=99,
        command_type="CANCEL_PENDING_ENTRY",
        payload_json='{"symbol": "BTC/USDT", "side": "LONG"}',
        idempotency_key="auto_cancel:99:1:legX",
    )
    conn.close()
    assert len(results) == 1
    assert results[0][1] == "auto_cancel:99:1:legX"


def test_expand_cancel_with_two_pending_entries_expands_to_two(tmp_path):
    """Con 2 PLACE_ENTRY attivi, il comando CANCEL viene espanso in 2 comandi."""
    from src.runtime_v2.lifecycle.cancel_expander import expand_cancel_pending_commands
    db = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db)
    conn = sqlite3.connect(db)
    _insert_place_entry_cmd(conn, 1, 10, "tsb:10:1:entry:1", status="SENT")
    _insert_place_entry_cmd(conn, 2, 10, "tsb:10:2:entry:2", status="ACK")
    conn.commit()

    results = expand_cancel_pending_commands(
        conn,
        trade_chain_id=10,
        command_type="CANCEL_PENDING_ENTRY",
        payload_json='{"symbol": "BTC/USDT", "side": "LONG"}',
        idempotency_key="auto_cancel:10:5:legX",
    )
    conn.close()

    assert len(results) == 2
    payloads = [json.loads(p) for p, _ in results]
    keys = [k for _, k in results]

    assert payloads[0]["entry_client_order_id"] == "tsb:10:1:entry:1"
    assert payloads[1]["entry_client_order_id"] == "tsb:10:2:entry:2"
    assert "tsb:10:1:entry:1" in keys[0]
    assert "tsb:10:2:entry:2" in keys[1]


def test_expand_cancel_does_not_include_done_commands(tmp_path):
    """Comandi PLACE_ENTRY con status DONE non vengono inclusi nell'espansione."""
    from src.runtime_v2.lifecycle.cancel_expander import expand_cancel_pending_commands
    db = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db)
    conn = sqlite3.connect(db)
    _insert_place_entry_cmd(conn, 1, 10, "tsb:10:1:entry:1", status="DONE")  # DONE → skip
    _insert_place_entry_cmd(conn, 2, 10, "tsb:10:2:entry:2", status="SENT")  # SENT → incluso
    conn.commit()

    results = expand_cancel_pending_commands(
        conn,
        trade_chain_id=10,
        command_type="CANCEL_PENDING_ENTRY",
        payload_json='{"symbol": "BTC/USDT"}',
        idempotency_key="idem:10",
    )
    conn.close()

    assert len(results) == 1
    assert json.loads(results[0][0])["entry_client_order_id"] == "tsb:10:2:entry:2"


def test_load_pending_entry_client_order_ids_returns_tsb_ids(tmp_path):
    """load_pending_entry_client_order_ids ritorna solo i client_order_id dei comandi attivi."""
    from src.runtime_v2.lifecycle.cancel_expander import load_pending_entry_client_order_ids
    db = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db)
    conn = sqlite3.connect(db)
    _insert_place_entry_cmd(conn, 10, 5, "tsb:5:10:entry:1", status="PENDING")
    _insert_place_entry_cmd(conn, 11, 5, "tsb:5:11:entry:2", status="SENT")
    _insert_place_entry_cmd(conn, 12, 5, "tsb:5:12:entry:3", status="DONE")  # escluso
    conn.commit()

    ids = load_pending_entry_client_order_ids(conn, 5)
    conn.close()

    assert ids == ["tsb:5:10:entry:1", "tsb:5:11:entry:2"]
```

- [ ] **Step 2.2: Eseguire per verificare che fallisce (modulo non esiste)**

```bash
pytest tests/runtime_v2/lifecycle/test_cancel_expander.py -v
```

Expected: `ERROR` — `ModuleNotFoundError: No module named 'src.runtime_v2.lifecycle.cancel_expander'`

- [ ] **Step 2.3: Creare `cancel_expander.py`**

Creare `src/runtime_v2/lifecycle/cancel_expander.py`:

```python
# src/runtime_v2/lifecycle/cancel_expander.py
"""
Shared logic for expanding CANCEL_PENDING_ENTRY commands.

Used by both entry_gate (UPDATE path) and workers (_persist_result path),
so that auto-cancel averaging emitted by the lifecycle processor reaches
the gateway with the real exchange client_order_id instead of a plan placeholder.
"""
from __future__ import annotations

import json
import sqlite3


def expand_cancel_pending_commands(
    conn: sqlite3.Connection,
    *,
    trade_chain_id: int,
    command_type: str,
    payload_json: str,
    idempotency_key: str,
) -> list[tuple[str, str]]:
    """Espande CANCEL_PENDING_ENTRY in un comando per ogni ordine pending reale.

    Ritorna lista di (payload_json, idempotency_key) da inserire in DB.
    Per tutti gli altri tipi di comando ritorna il comando originale invariato
    come lista con un solo elemento.

    La funzione legge i client_order_id reali (tsb:...) dai comandi PLACE_ENTRY
    ancora attivi (PENDING/SENT/ACK) per la chain indicata. Se non ce ne sono,
    ritorna il comando originale (il gateway gestirà il no-op).
    """
    if command_type != "CANCEL_PENDING_ENTRY":
        return [(payload_json, idempotency_key)]

    entry_client_order_ids = load_pending_entry_client_order_ids(conn, trade_chain_id)
    if not entry_client_order_ids:
        return [(payload_json, idempotency_key)]

    payload = json.loads(payload_json or "{}")
    expanded: list[tuple[str, str]] = []
    for entry_client_order_id in entry_client_order_ids:
        item = dict(payload)
        item["entry_client_order_id"] = entry_client_order_id
        expanded.append(
            (
                json.dumps(item),
                f"{idempotency_key}:{entry_client_order_id}",
            )
        )
    return expanded


def load_pending_entry_client_order_ids(
    conn: sqlite3.Connection,
    trade_chain_id: int,
) -> list[str]:
    """Legge i client_order_id reali (tsb:...) dei comandi PLACE_ENTRY ancora attivi."""
    rows = conn.execute(
        """
        SELECT client_order_id
        FROM ops_execution_commands
        WHERE trade_chain_id = ?
          AND command_type IN ('PLACE_ENTRY', 'PLACE_ENTRY_WITH_ATTACHED_TPSL')
          AND status IN ('PENDING', 'SENT', 'ACK')
          AND client_order_id IS NOT NULL
        ORDER BY command_id
        """,
        (trade_chain_id,),
    ).fetchall()
    return [str(row[0]) for row in rows if row and row[0]]


__all__ = ["expand_cancel_pending_commands", "load_pending_entry_client_order_ids"]
```

- [ ] **Step 2.4: Eseguire i test del cancel_expander**

```bash
pytest tests/runtime_v2/lifecycle/test_cancel_expander.py -v
```

Expected: tutti `PASSED`

- [ ] **Step 2.5: Aggiornare `entry_gate.py` per importare da `cancel_expander`**

In `src/runtime_v2/lifecycle/entry_gate.py` ci sono due funzioni private in fondo al file (linee ~1431-1476). Vanno rimosse e sostituite con import.

**Trovare e rimuovere** le definizioni private (dal boundary `import sqlite3 as _sqlite3` fino alla fine del file, escludendo `__all__`):

Trovare questo blocco in fondo al file (dopo `class LifecycleGateWorker`):
```python
def _expand_cancel_pending_commands(
    conn: _sqlite3.Connection,
    *,
    trade_chain_id: int,
    command_type: str,
    payload_json: str,
    idempotency_key: str,
) -> list[tuple[str, str]]:
    if command_type != "CANCEL_PENDING_ENTRY":
        return [(payload_json, idempotency_key)]

    entry_client_order_ids = _load_pending_entry_client_order_ids(conn, trade_chain_id)
    if not entry_client_order_ids:
        return [(payload_json, idempotency_key)]

    payload = json.loads(payload_json or "{}")
    expanded: list[tuple[str, str]] = []
    for entry_client_order_id in entry_client_order_ids:
        item = dict(payload)
        item["entry_client_order_id"] = entry_client_order_id
        expanded.append(
            (
                json.dumps(item),
                f"{idempotency_key}:{entry_client_order_id}",
            )
        )
    return expanded


def _load_pending_entry_client_order_ids(
    conn: _sqlite3.Connection,
    trade_chain_id: int,
) -> list[str]:
    rows = conn.execute(
        """
        SELECT client_order_id
        FROM ops_execution_commands
        WHERE trade_chain_id = ?
          AND command_type IN ('PLACE_ENTRY', 'PLACE_ENTRY_WITH_ATTACHED_TPSL')
          AND status IN ('PENDING','SENT','ACK')
          AND client_order_id IS NOT NULL
        ORDER BY command_id
        """,
        (trade_chain_id,),
    ).fetchall()
    return [str(row[0]) for row in rows if row and row[0]]
```

**Sostituire** con una sola riga di import (vicino agli altri import in cima al file):

Aggiungere ai top-level imports di `entry_gate.py` (dopo gli import esistenti, prima della prima def):
```python
from src.runtime_v2.lifecycle.cancel_expander import (
    expand_cancel_pending_commands as _expand_cancel_pending_commands,
    load_pending_entry_client_order_ids as _load_pending_entry_client_order_ids,
)
```

E rimuovere le due definizioni private dal fondo del file.

> Nota: gli alias con underscore mantengono il codice interno identico — `_expand_cancel_pending_commands(conn, ...)` in `_persist_update` funziona senza modifiche.

- [ ] **Step 2.6: Verificare che i test di entry_gate passano ancora**

```bash
pytest tests/runtime_v2/lifecycle/test_entry_gate.py -v
```

Expected: tutti `PASSED` (in particolare `test_lifecycle_gate_worker_expands_cancel_pending_for_each_active_entry_leg`)

- [ ] **Step 2.7: Aggiornare `workers.py` per chiamare `expand_cancel_pending_commands` in `_persist_result`**

In `src/runtime_v2/lifecycle/workers.py`:

**A) Rimuovere la funzione privata duplicata** (linee 25-38):
```python
def _load_pending_entry_client_order_ids(conn: sqlite3.Connection, chain_id: int) -> list[str]:
    rows = conn.execute(
        """
        SELECT client_order_id
        FROM ops_execution_commands
        WHERE trade_chain_id = ?
          AND command_type IN ('PLACE_ENTRY', 'PLACE_ENTRY_WITH_ATTACHED_TPSL')
          AND status IN ('PENDING','SENT','ACK')
          AND client_order_id IS NOT NULL
        ORDER BY command_id
        """,
        (chain_id,),
    ).fetchall()
    return [str(row[0]) for row in rows if row and row[0]]
```
(Nota: questa funzione è ancora referenziata da `TimeoutWorker._process_timeout` — sostituirla con l'import)

**B) Aggiungere import** in cima al file dopo gli import esistenti:
```python
from src.runtime_v2.lifecycle.cancel_expander import expand_cancel_pending_commands
```

**C) Aggiornare `TimeoutWorker._process_timeout`** per usare `load_pending_entry_client_order_ids` da cancel_expander invece della funzione locale rimossa. Trovare il blocco (linee ~143-163):
```python
                entry_client_order_ids = _load_pending_entry_client_order_ids(conn, chain_id)
```
Sostituire con (import già aggiunto sopra):
```python
from src.runtime_v2.lifecycle.cancel_expander import load_pending_entry_client_order_ids
# ... (oppure aggiungere load_pending_entry_client_order_ids all'import in cima)
                entry_client_order_ids = load_pending_entry_client_order_ids(conn, chain_id)
```

Meglio: aggiungere `load_pending_entry_client_order_ids` all'import in cima:
```python
from src.runtime_v2.lifecycle.cancel_expander import (
    expand_cancel_pending_commands,
    load_pending_entry_client_order_ids,
)
```

**D) Aggiornare `_persist_result`** — il loop comandi (linee ~292-302):

Trovare:
```python
                for cmd in result.execution_commands:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO ops_execution_commands (
                            trade_chain_id, command_type, status, payload_json,
                            idempotency_key, created_at, updated_at
                        ) VALUES (?,?,?,?,?,?,?)
                        """,
                        (chain_id, cmd.command_type, cmd.status, cmd.payload_json,
                         cmd.idempotency_key, now, now),
                    )
```

Sostituire con:
```python
                for cmd in result.execution_commands:
                    for payload_json_exp, idempotency_key_exp in expand_cancel_pending_commands(
                        conn,
                        trade_chain_id=chain_id,
                        command_type=cmd.command_type,
                        payload_json=cmd.payload_json,
                        idempotency_key=cmd.idempotency_key,
                    ):
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO ops_execution_commands (
                                trade_chain_id, command_type, status, payload_json,
                                idempotency_key, created_at, updated_at
                            ) VALUES (?,?,?,?,?,?,?)
                            """,
                            (chain_id, cmd.command_type, cmd.status, payload_json_exp,
                             idempotency_key_exp, now, now),
                        )
```

- [ ] **Step 2.8: Verificare che i test di workers passano**

```bash
pytest tests/runtime_v2/lifecycle/test_workers.py -v
```

Expected: tutti `PASSED`

- [ ] **Step 2.9: Aggiungere test di integrazione per la nuova logica in workers**

Aggiungere a `tests/runtime_v2/lifecycle/test_workers.py` un test che verifica l'espansione di `CANCEL_PENDING_ENTRY` in `_persist_result`:

```python
def test_persist_result_expands_cancel_pending_entry_to_per_order_commands(tmp_path):
    """_persist_result deve espandere CANCEL_PENDING_ENTRY con ID exchange reali."""
    import json as _json
    import sqlite3 as _sqlite3
    from datetime import datetime, timezone
    from pathlib import Path

    # Setup DB
    db = str(tmp_path / "ops.sqlite3")
    conn = _sqlite3.connect(db)
    for f in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit()

    now_str = datetime.now(timezone.utc).isoformat()
    chain_id = 42

    # Insert una trade chain
    conn.execute(
        "INSERT INTO ops_trade_chains "
        "(trade_chain_id, source_enrichment_id, canonical_message_id, raw_message_id, "
        "trader_id, account_id, symbol, side, lifecycle_state, entry_mode, "
        "be_protection_status, management_plan_json, plan_state_json, "
        "created_at, updated_at) "
        "VALUES (?,1,1,1,'t1','acc1','BTC/USDT','LONG','OPEN','ONE_SHOT','NOT_PROTECTED','{}','{}',?,?)",
        (chain_id, now_str, now_str),
    )

    # Inserire 2 PLACE_ENTRY commands attivi con client_order_id reali
    for cmd_id, seq in [(100, 2), (101, 3)]:
        coid = f"tsb:{chain_id}:{cmd_id}:entry:{seq}"
        conn.execute(
            "INSERT INTO ops_execution_commands "
            "(command_id, trade_chain_id, command_type, status, payload_json, "
            "idempotency_key, client_order_id, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (cmd_id, chain_id, "PLACE_ENTRY", "SENT", "{}",
             f"place_entry:{chain_id}:leg{seq}", coid, now_str, now_str),
        )
    conn.commit()
    conn.close()

    # Costruire un result con un CANCEL_PENDING_ENTRY (come emesso da auto-cancel averaging)
    from src.runtime_v2.lifecycle.event_processor import EventProcessorResult
    from src.runtime_v2.lifecycle.models import ExecutionCommand
    from src.runtime_v2.lifecycle.workers import LifecycleEventWorker
    from src.runtime_v2.lifecycle.repositories import (
        ExecutionCommandRepository, ExchangeEventRepository,
        LifecycleEventRepository, TradeChainRepository,
    )
    from unittest.mock import MagicMock

    cancel_cmd = ExecutionCommand(
        trade_chain_id=chain_id,
        command_type="CANCEL_PENDING_ENTRY",
        status="PENDING",
        payload_json=_json.dumps({"symbol": "BTC/USDT", "side": "LONG"}),
        idempotency_key=f"auto_cancel:{chain_id}:5:legX",
    )
    result = EventProcessorResult(
        new_lifecycle_state=None,
        new_be_protection_status=None,
        entry_avg_price=None,
        current_stop_price=None,
        lifecycle_events=[],
        execution_commands=[cancel_cmd],
    )

    worker = LifecycleEventWorker(
        ops_db_path=db,
        processor=MagicMock(),
        chain_repo=TradeChainRepository(db),
        event_repo=LifecycleEventRepository(db),
        command_repo=ExecutionCommandRepository(db),
        exchange_event_repo=MagicMock(),
    )
    worker._persist_result(chain_id, result)

    # Verificare che siano stati inseriti 2 comandi espansi, ognuno con entry_client_order_id reale
    conn2 = _sqlite3.connect(db)
    rows = conn2.execute(
        "SELECT payload_json, idempotency_key FROM ops_execution_commands "
        "WHERE command_type='CANCEL_PENDING_ENTRY' ORDER BY command_id"
    ).fetchall()
    conn2.close()

    assert len(rows) == 2, f"Attesi 2 comandi espansi, trovati {len(rows)}"
    coids_in_payload = [_json.loads(r[0]).get("entry_client_order_id") for r in rows]
    assert f"tsb:{chain_id}:100:entry:2" in coids_in_payload
    assert f"tsb:{chain_id}:101:entry:3" in coids_in_payload
```

- [ ] **Step 2.10: Eseguire per verificare che passa**

```bash
pytest tests/runtime_v2/lifecycle/test_workers.py::test_persist_result_expands_cancel_pending_entry_to_per_order_commands -v
```

Expected: `PASSED`

- [ ] **Step 2.11: Suite regressione completa**

```bash
pytest tests/runtime_v2/lifecycle/ -v --tb=short
```

Expected: tutti `PASSED`

- [ ] **Step 2.12: Commit**

```bash
git add src/runtime_v2/lifecycle/cancel_expander.py src/runtime_v2/lifecycle/entry_gate.py src/runtime_v2/lifecycle/workers.py tests/runtime_v2/lifecycle/test_cancel_expander.py tests/runtime_v2/lifecycle/test_workers.py
git commit -m "fix(lifecycle): extract cancel_expander module, expand CANCEL_PENDING_ENTRY in workers._persist_result (BUG-3)"
```

---

## Task 3: BUG-2 — Fallback match per `sequence` in `_process_pending_entry_cancelled_confirmed`

**Files:**
- Modify: `src/runtime_v2/lifecycle/event_processor.py`
- Modify: `tests/runtime_v2/lifecycle/test_event_processor.py`

### Contesto

Il piano di esecuzione costruito da `ExecutionPlanBuilder.build` contiene `client_order_id` placeholder:
- leg 1: `place_entry_attached:{eid}:leg1`
- leg N: `place_entry:{eid}:legN`

Quando `_handle_cancelled_order` (dopo BUG-1) invia `cancelled_order_ids: ["tsb:10:7001:entry:2"]`, il match in `_mark_entry_leg_status` fallisce perché `"tsb:10:7001:entry:2"` non è presente nel piano. Risultato: `new_plan_state_json = None`, la leg resta `PENDING`, il deferred BE non scatta mai.

Fix: aggiungere `_mark_entry_leg_status_by_sequence` e usarlo come fallback nel handler quando il match per ID restituisce `None`.

- [ ] **Step 3.1: Scrivere i test che falliscono**

Aggiungere in `tests/runtime_v2/lifecycle/test_event_processor.py` dopo i test deferred BE esistenti:

```python
# ── BUG-2: fallback match per sequence ─────────────────────────────────────

def test_deferred_be_emitted_with_production_payload_and_placeholder_plan():
    """Riproduce il comportamento production: piano ha placeholder ID, payload ha ID exchange reale.
    Il fallback per sequence deve marcare la leg e scattare il deferred BE."""
    proc = _make_processor()

    # Piano con placeholder client_order_id (come buildato da ExecutionPlanBuilder)
    plan_with_deferred = {
        "plan_version": 1,
        "legs": [
            {"leg_id": "leg_1", "sequence": 1, "status": "FILLED",
             "client_order_id": "place_entry_attached:99:leg1"},
            {"leg_id": "leg_2", "sequence": 2, "status": "CANCELLED",
             "client_order_id": "place_entry:99:leg2"},
            {"leg_id": "leg_3", "sequence": 3, "status": "PENDING",
             "client_order_id": "place_entry:99:leg3"},  # placeholder
        ],
        "_be_deferred_by_auto_cancel": {"tp_level": 1, "averaging_legs_pending": 1},
    }
    chain = _make_chain_with_plan(
        be_trigger="tp1",
        cancel_averaging_pending_after="tp1",
        plan_legs=[],
        entry_avg_price=50000.0,
        open_position_qty=1.0,
    )
    chain = chain.model_copy(update={"plan_state_json": json.dumps(plan_with_deferred)})

    # Payload reale da _handle_cancelled_order: ID exchange, non nel piano
    event = _make_exchange_event(
        event_type="PENDING_ENTRY_CANCELLED_CONFIRMED",
        payload={
            "cancelled_order_ids": ["tsb:1:7001:entry:3"],  # ID exchange, NON corrisponde a placeholder
            "sequence": 3,                                   # numero leg del coid
            "position_already_open": False,
        },
    )

    result = proc.process(event, chain, [])

    # Il fallback per sequence deve aver trovato leg_3 e marcato come CANCELLED
    assert result.new_plan_state_json is not None, "Piano deve essere aggiornato via fallback sequence"
    final_plan = json.loads(result.new_plan_state_json)
    leg_3 = next(l for l in final_plan["legs"] if l["sequence"] == 3)
    assert leg_3["status"] == "CANCELLED", "Leg 3 deve essere CANCELLED dopo fallback"

    # Il deferred BE deve essere emesso (leg_3 era l'ultima pending)
    be_cmds = [c for c in result.execution_commands if c.command_type == "MOVE_STOP_TO_BREAKEVEN"]
    assert len(be_cmds) == 1, "Il deferred BE deve essere emesso"
    be_payload = json.loads(be_cmds[0].payload_json)
    assert be_payload["is_breakeven"] is True
    assert "_be_deferred_by_auto_cancel" not in final_plan, "Flag deve essere rimosso"


def test_deferred_be_not_emitted_with_production_payload_when_other_legs_still_pending():
    """Con payload production: se ci sono ancora altre leg pending, il BE non viene emesso."""
    proc = _make_processor()

    plan_with_deferred = {
        "plan_version": 1,
        "legs": [
            {"leg_id": "leg_1", "sequence": 1, "status": "FILLED",
             "client_order_id": "place_entry_attached:99:leg1"},
            {"leg_id": "leg_2", "sequence": 2, "status": "PENDING",
             "client_order_id": "place_entry:99:leg2"},  # ancora pending
            {"leg_id": "leg_3", "sequence": 3, "status": "PENDING",
             "client_order_id": "place_entry:99:leg3"},
        ],
        "_be_deferred_by_auto_cancel": {"tp_level": 1, "averaging_legs_pending": 2},
    }
    chain = _make_chain_with_plan(plan_legs=[], entry_avg_price=50000.0)
    chain = chain.model_copy(update={"plan_state_json": json.dumps(plan_with_deferred)})

    # Payload production: ID exchange per leg 3 con sequence
    event = _make_exchange_event(
        event_type="PENDING_ENTRY_CANCELLED_CONFIRMED",
        payload={
            "cancelled_order_ids": ["tsb:1:7001:entry:3"],
            "sequence": 3,
            "position_already_open": False,
        },
    )

    result = proc.process(event, chain, [])

    be_cmds = [c for c in result.execution_commands if c.command_type == "MOVE_STOP_TO_BREAKEVEN"]
    assert len(be_cmds) == 0, "Leg 2 ancora pending → no BE"


def test_cancel_confirmed_without_deferred_be_config():
    """Path non-configurato: cancel senza deferred BE.
    PENDING_ENTRY_CANCELLED_CONFIRMED arriva su una chain senza _be_deferred_by_auto_cancel.
    La leg viene marcata via fallback sequence, nessun BE emesso."""
    proc = _make_processor()

    # Piano senza flag deferred BE, con placeholder ID
    plan_no_deferred = {
        "plan_version": 1,
        "legs": [
            {"leg_id": "leg_1", "sequence": 1, "status": "FILLED",
             "client_order_id": "place_entry_attached:99:leg1"},
            {"leg_id": "leg_2", "sequence": 2, "status": "PENDING",
             "client_order_id": "place_entry:99:leg2"},
        ],
        # Nessun _be_deferred_by_auto_cancel
    }
    chain = _make_chain_with_plan(
        plan_legs=[],
        open_position_qty=0.5,
        entry_avg_price=50000.0,
    )
    chain = chain.model_copy(update={
        "plan_state_json": json.dumps(plan_no_deferred),
        "lifecycle_state": "OPEN",
    })

    # Payload production per leg 2
    event = _make_exchange_event(
        event_type="PENDING_ENTRY_CANCELLED_CONFIRMED",
        payload={
            "cancelled_order_ids": ["tsb:1:8001:entry:2"],
            "sequence": 2,
            "position_already_open": True,
        },
    )

    result = proc.process(event, chain, [])

    # La leg deve essere marcata CANCELLED via fallback
    assert result.new_plan_state_json is not None
    final_plan = json.loads(result.new_plan_state_json)
    leg_2 = next(l for l in final_plan["legs"] if l["sequence"] == 2)
    assert leg_2["status"] == "CANCELLED"

    # Nessun BE emesso (non configurato)
    be_cmds = [c for c in result.execution_commands if c.command_type == "MOVE_STOP_TO_BREAKEVEN"]
    assert len(be_cmds) == 0

    # Nessun cambio lifecycle state (posizione aperta, nessun deferred)
    assert result.new_lifecycle_state is None


def test_fallback_sequence_not_triggered_if_primary_match_succeeds():
    """Se il match primario per client_order_id funziona, il fallback per sequence non viene usato.
    Verifica il path legacy (piano con ID reali corrispondenti al payload)."""
    proc = _make_processor()

    # Piano con ID reali (scenario legacy dove il piano è stato aggiornato)
    plan = {
        "plan_version": 1,
        "legs": [
            {"leg_id": "leg_2", "sequence": 2, "status": "PENDING",
             "client_order_id": "tsb:1:7001:entry:2"},  # ID reale nel piano
        ],
    }
    chain = _make_chain_with_plan(plan_legs=[], open_position_qty=0.5, entry_avg_price=50000.0)
    chain = chain.model_copy(update={
        "plan_state_json": json.dumps(plan),
        "lifecycle_state": "OPEN",
    })

    # Payload con stesso ID (match primario funziona)
    event = _make_exchange_event(
        event_type="PENDING_ENTRY_CANCELLED_CONFIRMED",
        payload={
            "cancelled_order_ids": ["tsb:1:7001:entry:2"],
            "sequence": 2,
            "position_already_open": True,
        },
    )

    result = proc.process(event, chain, [])

    assert result.new_plan_state_json is not None
    final_plan = json.loads(result.new_plan_state_json)
    leg_2 = next(l for l in final_plan["legs"] if l["sequence"] == 2)
    assert leg_2["status"] == "CANCELLED"
```

- [ ] **Step 3.2: Eseguire per verificare che i nuovi test falliscono**

```bash
pytest tests/runtime_v2/lifecycle/test_event_processor.py::test_deferred_be_emitted_with_production_payload_and_placeholder_plan tests/runtime_v2/lifecycle/test_event_processor.py::test_deferred_be_not_emitted_with_production_payload_when_other_legs_still_pending tests/runtime_v2/lifecycle/test_event_processor.py::test_cancel_confirmed_without_deferred_be_config tests/runtime_v2/lifecycle/test_event_processor.py::test_fallback_sequence_not_triggered_if_primary_match_succeeds -v
```

Expected: i primi 3 `FAILED` (il fallback non esiste), l'ultimo `PASSED` (usa match primario)

- [ ] **Step 3.3: Aggiungere `_mark_entry_leg_status_by_sequence` a `event_processor.py`**

In `src/runtime_v2/lifecycle/event_processor.py`, aggiungere il metodo dopo `_mark_entry_leg_status` (dopo la riga 278):

```python
    def _mark_entry_leg_status_by_sequence(
        self,
        plan_state_json: str,
        *,
        sequence: int,
        new_status: str,
    ) -> str | None:
        """Fallback: cerca le leg con leg["sequence"] == sequence e status PENDING.
        Usato quando il match per client_order_id fallisce (piano ha placeholder ID)."""
        try:
            plan = json.loads(plan_state_json or "{}")
        except Exception:
            return None
        legs = plan.get("legs", [])
        target_legs = [
            leg for leg in legs
            if leg.get("sequence") == sequence and leg.get("status") == "PENDING"
        ]
        if not target_legs:
            return None
        updated = plan_state_json
        for leg in target_legs:
            updated = ExecutionPlanBuilder.update_leg_status(
                updated,
                str(leg.get("leg_id")),
                new_status,
            )
        return updated
```

- [ ] **Step 3.4: Aggiungere il fallback in `_process_pending_entry_cancelled_confirmed`**

In `src/runtime_v2/lifecycle/event_processor.py`, trovare il blocco nella method `_process_pending_entry_cancelled_confirmed` (linee ~650-656):

```python
        # ── Marca leg come CANCELLED nel piano ────────────────────────────────
        new_plan_state_json = self._mark_entry_leg_status(
            chain.plan_state_json,
            client_order_ids=cancelled_order_ids,
            command_payload=None,
            new_status="CANCELLED",
        )
        effective_plan_json = new_plan_state_json or chain.plan_state_json or "{}"
```

Sostituire con:

```python
        # ── Marca leg come CANCELLED nel piano ────────────────────────────────
        new_plan_state_json = self._mark_entry_leg_status(
            chain.plan_state_json,
            client_order_ids=cancelled_order_ids,
            command_payload=None,
            new_status="CANCELLED",
        )

        # Fallback: match per sequence (piano ha placeholder ID, ID exchange non corrisponde)
        if new_plan_state_json is None:
            sequence = payload.get("sequence")
            if sequence is not None:
                new_plan_state_json = self._mark_entry_leg_status_by_sequence(
                    chain.plan_state_json,
                    sequence=int(sequence),
                    new_status="CANCELLED",
                )

        effective_plan_json = new_plan_state_json or chain.plan_state_json or "{}"
```

- [ ] **Step 3.5: Eseguire i nuovi test per verificare che passano**

```bash
pytest tests/runtime_v2/lifecycle/test_event_processor.py::test_deferred_be_emitted_with_production_payload_and_placeholder_plan tests/runtime_v2/lifecycle/test_event_processor.py::test_deferred_be_not_emitted_with_production_payload_when_other_legs_still_pending tests/runtime_v2/lifecycle/test_event_processor.py::test_cancel_confirmed_without_deferred_be_config tests/runtime_v2/lifecycle/test_event_processor.py::test_fallback_sequence_not_triggered_if_primary_match_succeeds -v
```

Expected: tutti `PASSED`

- [ ] **Step 3.6: Suite regressione completa del file**

```bash
pytest tests/runtime_v2/lifecycle/test_event_processor.py -v
```

Expected: tutti `PASSED` — i test esistenti con `"cid_leg3"` continuano a funzionare (path primario intatto)

- [ ] **Step 3.7: Suite regressione completa runtime_v2**

```bash
pytest tests/runtime_v2/ -v --tb=short
```

Expected: tutti `PASSED`

- [ ] **Step 3.8: Commit**

```bash
git add src/runtime_v2/lifecycle/event_processor.py tests/runtime_v2/lifecycle/test_event_processor.py
git commit -m "fix(lifecycle): add sequence fallback in _process_pending_entry_cancelled_confirmed, update tests to production payload format (BUG-2)"
```

---

## Task 4: Verifica finale e tag P1 complete

- [ ] **Step 4.1: Eseguire la suite completa P1**

```bash
pytest tests/runtime_v2/ -v --tb=short 2>&1 | tail -30
```

Expected: tutti `PASSED`, 0 failures

- [ ] **Step 4.2: Verificare che la catena deferred BE sia documentata nel codice**

Aprire `src/runtime_v2/execution_gateway/event_sync.py` e verificare che il payload di `_handle_cancelled_order` ora include `cancelled_order_ids` e `sequence`.

Aprire `src/runtime_v2/lifecycle/event_processor.py` e verificare che `_process_pending_entry_cancelled_confirmed` ha il blocco fallback con commento `# Fallback: match per sequence`.

Aprire `src/runtime_v2/lifecycle/workers.py` e verificare che `_persist_result` usa `expand_cancel_pending_commands` in loop.

- [ ] **Step 4.3: Commit finale riepilogo**

```bash
git commit --allow-empty -m "chore: P1 bugs complete — deferred BE chain fixed (BUG-1 + BUG-2 + BUG-3)"
```

---

## Self-Review

### Spec coverage check

| Requisito spec | Task che lo implementa |
|---|---|
| BUG-1: `cancelled_order_ids` e `sequence` nel payload | Task 1, Step 1.3 |
| BUG-2: fallback match per sequence | Task 3, Step 3.3-3.4 |
| BUG-3: `_persist_result` espande `CANCEL_PENDING_ENTRY` | Task 2, Step 2.7 |
| BUG-3: `cancel_expander.py` nuovo modulo | Task 2, Step 2.3 |
| BUG-3: `entry_gate.py` importa da `cancel_expander` | Task 2, Step 2.5 |
| Test aggiornati con payload production | Task 3, Step 3.1 |
| Test path non-configurato (cancel senza deferred BE) | Task 3, `test_cancel_confirmed_without_deferred_be_config` |
| Test fallback per sequence end-to-end | Task 3, `test_deferred_be_emitted_with_production_payload_and_placeholder_plan` |

### Type consistency check

- `_mark_entry_leg_status_by_sequence(plan_state_json: str, *, sequence: int, new_status: str) -> str | None` — usato in Task 3.4 con `int(sequence)` ✓
- `expand_cancel_pending_commands(conn, *, trade_chain_id, command_type, payload_json, idempotency_key) -> list[tuple[str, str]]` — firma identica in cancel_expander.py, entry_gate.py (import alias), workers.py ✓
- `load_pending_entry_client_order_ids(conn, trade_chain_id) -> list[str]` — usato in TimeoutWorker dopo refactor ✓

### Placeholder check

Nessun placeholder presente — ogni step include il codice completo.

---
