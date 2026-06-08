# Fill Identity Dedupe — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the WS/REST fill identity collision that causes a second TP fill with `tp_level=None` to be silently dropped, leaving the chain unclosed until position reconciliation synthesizes a `CLOSE_FULL_FILLED` with no fill price.

**Architecture:** Swap the `ops_exchange_events` idempotency key for fill-type events from a semantic key (`TP_FILLED:<chain>`) to an identity key (`fill:<exchange_event_id>`). Update `tp_fill_exists()` to query by event_type (not key). Update `run_trade_based_reconciliation()` to use the same identity key. Add a guard in `run_position_reconciliation()` to skip the synthetic close when a real fill already exists.

**Tech Stack:** Python 3.12, SQLite, pytest. No new dependencies.

---

## File Map

| File | Action | What changes |
|------|--------|--------------|
| `src/runtime_v2/execution_gateway/repositories.py` | Modify | `insert_raw_and_classified()` key logic; `tp_fill_exists()` semantics; add `real_close_fill_exists()` |
| `src/runtime_v2/execution_gateway/event_sync.py` | Modify | `run_trade_based_reconciliation()` idem_key; `run_position_reconciliation()` guard |
| `tests/runtime_v2/execution_gateway/test_repository_extensions.py` | Modify | Update `test_tp_fill_exists_and_protective_cancelled_exists` to reflect new semantics |
| `tests/runtime_v2/execution_gateway/test_fill_identity_dedupe.py` | Create | New test file for core bug fix scenarios |

---

## Task 1: Fix `insert_raw_and_classified()` — identity-based dedupe key

**Files:**
- Modify: `src/runtime_v2/execution_gateway/repositories.py:779-878`
- Create: `tests/runtime_v2/execution_gateway/test_fill_identity_dedupe.py`

- [ ] **Step 1: Write failing test — two TP fills, tp_level=None, different execIds → both inserted**

Create `tests/runtime_v2/execution_gateway/test_fill_identity_dedupe.py`:

```python
# tests/runtime_v2/execution_gateway/test_fill_identity_dedupe.py
from __future__ import annotations

import sqlite3

from src.runtime_v2.execution_gateway.event_ingest.models import ClassifiedEvent, ExchangeRawEvent
from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository


def _make_db(tmp_path) -> str:
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE exchange_raw_events (
            raw_event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            exchange_event_id TEXT NOT NULL,
            source_stream TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            create_type TEXT, stop_order_type TEXT, exec_type TEXT, order_status TEXT,
            order_link_id TEXT, order_id TEXT, seq INTEGER,
            exec_price REAL, exec_qty REAL, closed_size REAL, leaves_qty REAL,
            pos_qty REAL, exec_value REAL, exec_fee REAL, fee_rate REAL, cum_exec_qty REAL,
            position_take_profit REAL, position_stop_loss REAL,
            classified_event_type TEXT, classified_source TEXT,
            trade_chain_id INTEGER, tp_level INTEGER,
            forwarded_to_lifecycle INTEGER DEFAULT 0, forwarded_at TEXT,
            raw_info_json TEXT NOT NULL DEFAULT '{}',
            exchange_time TEXT, received_at TEXT NOT NULL,
            idempotency_key TEXT UNIQUE NOT NULL
        );
        CREATE TABLE ops_exchange_events (
            exchange_event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_chain_id INTEGER, event_type TEXT, payload_json TEXT,
            processing_status TEXT DEFAULT 'NEW',
            idempotency_key TEXT UNIQUE, received_at TEXT
        );
        CREATE TABLE ops_execution_commands (
            command_id INTEGER PRIMARY KEY, trade_chain_id INTEGER,
            command_type TEXT, status TEXT, payload_json TEXT DEFAULT '{}',
            idempotency_key TEXT, client_order_id TEXT,
            created_at TEXT, updated_at TEXT
        );
        CREATE TABLE ops_trade_chains (
            trade_chain_id INTEGER PRIMARY KEY,
            symbol TEXT, side TEXT, lifecycle_state TEXT, updated_at TEXT
        );
        CREATE TABLE ops_lifecycle_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_chain_id INTEGER, event_type TEXT NOT NULL, source_type TEXT NOT NULL,
            source_id TEXT, previous_state TEXT, next_state TEXT,
            payload_json TEXT NOT NULL DEFAULT '{}',
            idempotency_key TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL
        );
        CREATE TABLE ops_notification_outbox (
            notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
            notification_type TEXT NOT NULL, destination TEXT NOT NULL,
            payload_json TEXT NOT NULL, priority TEXT NOT NULL DEFAULT 'MEDIUM',
            status TEXT NOT NULL DEFAULT 'PENDING', dedupe_key TEXT NOT NULL UNIQUE,
            attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT, created_at TEXT NOT NULL,
            sent_at TEXT, send_after TEXT, aggregation_group TEXT, source_message_id TEXT
        );
    """)
    conn.commit()
    conn.close()
    return db_path


def _make_tp_fill(exec_id: str, idem_key: str, exec_qty: float = 7070.0) -> ClassifiedEvent:
    raw = ExchangeRawEvent(
        source_stream="watch_my_trades",
        exchange_event_id=exec_id,
        idempotency_key=idem_key,
        symbol="ASTERUSDT",
        side="Sell",
        create_type="CreateByTakeProfit",
        stop_order_type="TakeProfit",
        exec_type="Trade",
        order_status=None,
        order_link_id="",
        order_id=f"order-{exec_id}",
        seq=1000,
        exec_price=0.6358,
        exec_qty=exec_qty,
        closed_size=exec_qty,
        leaves_qty=0.0,
        pos_qty=None,
        exec_value=exec_qty * 0.6358,
        exec_fee=0.002,
        fee_rate=0.00055,
        cum_exec_qty=None,
        position_take_profit=None,
        position_stop_loss=None,
        exchange_time="2026-06-07T22:14:19Z",
        received_at="2026-06-07T22:14:20Z",
        raw_info={},
    )
    return ClassifiedEvent(
        raw=raw,
        event_type="TP_FILLED",
        source="exchange_auto",
        trade_chain_id=1,
        tp_level=None,
        is_actionable=True,
    )


def test_two_tp_fills_no_tp_level_both_inserted(tmp_path):
    """Regression: TP1 parziale e TP finale, entrambi tp_level=None, exchange_event_id diversi.
    Prima del fix il secondo veniva droppato da INSERT OR IGNORE sulla stessa chiave semantica."""
    db_path = _make_db(tmp_path)
    repo = GatewayCommandRepository(db_path)

    tp1 = _make_tp_fill("exec-aaa-001", "exec:exec-aaa-001", exec_qty=7070.0)
    tp2 = _make_tp_fill("exec-bbb-002", "exec:exec-bbb-002", exec_qty=7071.0)

    inserted1 = repo.insert_raw_and_classified(tp1)
    inserted2 = repo.insert_raw_and_classified(tp2)

    assert inserted1 is True, "first TP fill should be inserted"
    assert inserted2 is True, "second TP fill must also be inserted — different exchange_event_id"

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT idempotency_key FROM ops_exchange_events "
        "WHERE event_type='TP_FILLED' ORDER BY exchange_event_id"
    ).fetchall()
    conn.close()

    assert len(rows) == 2, f"expected 2 TP_FILLED rows, got {len(rows)}"
    keys = {r[0] for r in rows}
    assert keys == {"fill:exec-aaa-001", "fill:exec-bbb-002"}


def test_same_tp_fill_twice_is_idempotent(tmp_path):
    """Stesso execId visto due volte (WS duplicate) — inserito una sola volta."""
    db_path = _make_db(tmp_path)
    repo = GatewayCommandRepository(db_path)

    tp = _make_tp_fill("exec-ccc-003", "exec:exec-ccc-003")

    inserted1 = repo.insert_raw_and_classified(tp)
    inserted2 = repo.insert_raw_and_classified(tp)

    assert inserted1 is True
    assert inserted2 is False

    conn = sqlite3.connect(db_path)
    cnt = conn.execute("SELECT COUNT(*) FROM ops_exchange_events").fetchone()[0]
    conn.close()
    assert cnt == 1
```

- [ ] **Step 2: Run to confirm tests fail**

```
pytest tests/runtime_v2/execution_gateway/test_fill_identity_dedupe.py -v
```

Expected: FAIL — `test_two_tp_fills_no_tp_level_both_inserted` fails because both TP fills share key `TP_FILLED:1` and the second is dropped.

- [ ] **Step 3: Fix `insert_raw_and_classified()` in `repositories.py`**

Locate the key-building block starting at line ~789 and replace it:

```python
        # Old block to REMOVE:
        # if classified.event_type == "TP_FILLED":
        #     if classified.tp_level is not None:
        #         ops_idem_key = f"TP_FILLED:{classified.trade_chain_id}:level:{classified.tp_level}"
        #     else:
        #         ops_idem_key = f"TP_FILLED:{classified.trade_chain_id}"
        # elif classified.event_type == "ENTRY_FILLED":
        #     _order_anchor = raw.order_id or raw.exchange_event_id
        #     ops_idem_key = f"ENTRY_FILLED:{classified.trade_chain_id}:{_order_anchor}"
        # else:
        #     ops_idem_key = f"{classified.event_type}:{classified.trade_chain_id}"
```

Replace with:

```python
        # Fill events from execution streams are deduplicated by exchange identity (execId),
        # not by semantic classification. This allows two TP fills for the same chain
        # (e.g. TP1 partial + TP final, both tp_level=None) to coexist without collision.
        _EXCHANGE_IDENTITY_TYPES = frozenset({
            "TP_FILLED", "SL_FILLED", "MANUAL_CLOSE_FULL", "MANUAL_CLOSE_PARTIAL",
            "LIQUIDATION_FILLED", "CLOSE_PARTIAL_FILLED", "CLOSE_FULL_FILLED", "FUNDING_SETTLED",
        })
        if classified.event_type in _EXCHANGE_IDENTITY_TYPES and raw.exchange_event_id:
            ops_idem_key = f"fill:{raw.exchange_event_id}"
        elif classified.event_type == "ENTRY_FILLED":
            # ENTRY_FILLED uses order_id for WS/REST convergence
            # (REST path in event_sync._save_fill_event uses "{event_type}:{chain}:{order_id}")
            _order_anchor = raw.order_id or raw.exchange_event_id
            ops_idem_key = f"ENTRY_FILLED:{classified.trade_chain_id}:{_order_anchor}"
        else:
            ops_idem_key = f"{classified.event_type}:{classified.trade_chain_id}"
```

- [ ] **Step 4: Run tests to confirm they pass**

```
pytest tests/runtime_v2/execution_gateway/test_fill_identity_dedupe.py -v
```

Expected: PASS — both tests green.

- [ ] **Step 5: Run full gateway test suite to check for regressions**

```
pytest tests/runtime_v2/execution_gateway/ -v
```

Expected: all tests pass except `test_tp_fill_exists_and_protective_cancelled_exists` which will fail because it was checking level-specific keys. That test is fixed in Task 2.

- [ ] **Step 6: Commit**

```
git add src/runtime_v2/execution_gateway/repositories.py
git add tests/runtime_v2/execution_gateway/test_fill_identity_dedupe.py
git commit -m "fix: use exchange_event_id as ops_exchange_events dedupe key for fill events"
```

---

## Task 2: Fix `tp_fill_exists()` — query by event_type, not by semantic key

**Files:**
- Modify: `src/runtime_v2/execution_gateway/repositories.py:927-945`
- Modify: `tests/runtime_v2/execution_gateway/test_repository_extensions.py:315-344`

- [ ] **Step 1: Write failing test — tp_fill_exists works after identity-based insert**

Add to `tests/runtime_v2/execution_gateway/test_fill_identity_dedupe.py`:

```python
def test_tp_fill_exists_after_identity_insert(tmp_path):
    """tp_fill_exists deve trovare un TP_FILLED inserito con chiave identity-based."""
    db_path = _make_db(tmp_path)
    repo = GatewayCommandRepository(db_path)

    tp = _make_tp_fill("exec-ddd-004", "exec:exec-ddd-004")
    repo.insert_raw_and_classified(tp)

    assert repo.tp_fill_exists(1) is True
    assert repo.tp_fill_exists(99) is False  # wrong chain


def test_tp_fill_exists_false_when_no_tp_in_chain(tmp_path):
    """tp_fill_exists false se non ci sono TP_FILLED per quella chain."""
    db_path = _make_db(tmp_path)
    repo = GatewayCommandRepository(db_path)

    assert repo.tp_fill_exists(1) is False
```

- [ ] **Step 2: Run to confirm new tests fail (signature mismatch: current `tp_fill_exists` requires 2 args)**

```
pytest tests/runtime_v2/execution_gateway/test_fill_identity_dedupe.py::test_tp_fill_exists_after_identity_insert tests/runtime_v2/execution_gateway/test_fill_identity_dedupe.py::test_tp_fill_exists_false_when_no_tp_in_chain -v
```

Expected: FAIL — `tp_fill_exists(1)` raises TypeError (missing required arg `tp_level`).

- [ ] **Step 3: Rewrite `tp_fill_exists()` in `repositories.py`**

Find the method (around line 927) and replace it entirely:

```python
    def tp_fill_exists(self, trade_chain_id: int, tp_level: int | None = None) -> bool:
        """Checks if any TP_FILLED event exists for this chain.

        tp_level is accepted but ignored: with identity-based dedupe keys, two fills
        for the same chain have distinct keys regardless of tp_level.
        """
        conn = sqlite3.connect(self._db)
        try:
            row = conn.execute(
                "SELECT 1 FROM ops_exchange_events "
                "WHERE trade_chain_id = ? AND event_type = 'TP_FILLED' LIMIT 1",
                (trade_chain_id,),
            ).fetchone()
            return row is not None
        finally:
            conn.close()
```

- [ ] **Step 4: Update the existing test that checks level-specific behaviour**

In `tests/runtime_v2/execution_gateway/test_repository_extensions.py`, find `test_tp_fill_exists_and_protective_cancelled_exists` and replace the `tp_fill_exists` assertions:

```python
def test_tp_fill_exists_and_protective_cancelled_exists(tmp_path):
    db_path = make_db(tmp_path)
    conn = sqlite3.connect(db_path)
    # Insert TP_FILLED for chain=1 with identity-based key (new format)
    conn.execute(
        "INSERT INTO ops_exchange_events "
        "(trade_chain_id, event_type, payload_json, processing_status, idempotency_key, received_at) "
        "VALUES (?,?,?,?,?,?)",
        (1, "TP_FILLED", "{}", "NEW", "fill:exec-abc-001", "2026-05-27T10:00:00Z"),
    )
    conn.commit()
    conn.close()

    repo = GatewayCommandRepository(db_path)

    # tp_fill_exists checks by chain+event_type only — level distinction removed
    assert repo.tp_fill_exists(1) is True
    assert repo.tp_fill_exists(1, tp_level=2) is True   # tp_level ignored
    assert repo.tp_fill_exists(99) is False              # wrong chain

    # Insert PROTECTIVE_ORDER_CANCELLED for chain=1
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_exchange_events "
        "(trade_chain_id, event_type, payload_json, processing_status, idempotency_key, received_at) "
        "VALUES (?,?,?,?,?,?)",
        (1, "PROTECTIVE_ORDER_CANCELLED", "{}", "NEW", "PROTECTIVE_ORDER_CANCELLED:1:unique", "2026-05-27T10:00:00Z"),
    )
    conn.commit()
    conn.close()

    assert repo.protective_cancelled_exists(1) is True
    assert repo.protective_cancelled_exists(99) is False
```

- [ ] **Step 5: Run tests to confirm they pass**

```
pytest tests/runtime_v2/execution_gateway/test_fill_identity_dedupe.py tests/runtime_v2/execution_gateway/test_repository_extensions.py -v
```

Expected: all pass.

- [ ] **Step 6: Run full gateway suite**

```
pytest tests/runtime_v2/execution_gateway/ -v
```

Expected: all pass.

- [ ] **Step 7: Commit**

```
git add src/runtime_v2/execution_gateway/repositories.py
git add tests/runtime_v2/execution_gateway/test_repository_extensions.py
git add tests/runtime_v2/execution_gateway/test_fill_identity_dedupe.py
git commit -m "fix: tp_fill_exists queries by event_type, removes stale level-based key lookup"
```

---

## Task 3: Fix `run_trade_based_reconciliation()` — identity key for REST-recovered fills

**Files:**
- Modify: `src/runtime_v2/execution_gateway/event_sync.py:143-213`
- Modify: `tests/runtime_v2/execution_gateway/test_fill_identity_dedupe.py`

- [ ] **Step 1: Write failing test — REST reconciliation uses `fill:<trade_id>` key**

Add to `tests/runtime_v2/execution_gateway/test_fill_identity_dedupe.py`:

```python
import json


def _make_rest_reconciliation_db(tmp_path) -> str:
    """DB with open chain + active TP command, no existing TP_FILLED."""
    db_path = _make_db(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_trade_chains (trade_chain_id, symbol, side, lifecycle_state) "
        "VALUES (1, 'ASTERUSDT', 'LONG', 'OPEN')"
    )
    conn.execute(
        "INSERT INTO ops_execution_commands "
        "(command_id, trade_chain_id, command_type, status, payload_json, idempotency_key, created_at, updated_at) "
        "VALUES (1, 1, 'SET_POSITION_TPSL_PARTIAL', 'SENT', '{}', 'idem:1', '2026-06-07T00:00:00Z', '2026-06-07T00:00:00Z')"
    )
    conn.commit()
    conn.close()
    return db_path


class _FakeTrade:
    def __init__(self, trade_id: str, price: float, amount: float):
        self.trade_id = trade_id
        self.price = price
        self.amount = amount
        self.fee = 0.0


class _FakeReconciliationAdapter:
    def __init__(self, trades: list):
        self._trades = trades

    def fetch_recent_reduce_trades(self, symbol, side, execution_account_id, limit=50):
        return self._trades

    def get_order_status(self, *a, **kw):
        return None

    def get_position_qty(self, *a, **kw):
        return None

    def get_capabilities(self):
        from src.runtime_v2.execution_gateway.models import AdapterCapabilities
        return AdapterCapabilities(
            place_entry=False, protective_stop_native=False, take_profit_native=False,
            bracket_order=False, move_stop=False, close_partial=False, close_full=False,
            executor_position=False, sync_protective_orders=False,
        )


def test_trade_based_reconciliation_uses_fill_identity_key(tmp_path):
    """REST reconciliation deve inserire il fill con chiave fill:<trade_id>, non TP_FILLED:<chain>."""
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    db_path = _make_rest_reconciliation_db(tmp_path)
    repo = GatewayCommandRepository(db_path)
    adapter = _FakeReconciliationAdapter([_FakeTrade("exec-rest-999", 0.6393, 7071.0)])
    worker = ExchangeEventSyncWorker(
        ops_db_path=db_path,
        adapter=adapter,
        repo=repo,
        execution_account_id="test_account",
    )

    inserted_count = worker.run_trade_based_reconciliation()

    assert inserted_count == 1

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT idempotency_key, event_type FROM ops_exchange_events WHERE event_type='TP_FILLED'"
    ).fetchone()
    conn.close()

    assert row is not None
    assert row[0] == "fill:exec-rest-999", f"expected fill:exec-rest-999, got {row[0]!r}"


def test_trade_based_reconciliation_skips_when_ws_fill_already_present(tmp_path):
    """Se il WS ha già inserito il fill, la reconciliation REST non deve inserire un duplicato."""
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    db_path = _make_rest_reconciliation_db(tmp_path)
    repo = GatewayCommandRepository(db_path)

    # Simulate WS having already inserted the fill with identity key
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_exchange_events "
        "(trade_chain_id, event_type, payload_json, processing_status, idempotency_key, received_at) "
        "VALUES (1, 'TP_FILLED', '{}', 'NEW', 'fill:exec-rest-999', '2026-06-07T22:14:20Z')"
    )
    conn.commit()
    conn.close()

    adapter = _FakeReconciliationAdapter([_FakeTrade("exec-rest-999", 0.6393, 7071.0)])
    worker = ExchangeEventSyncWorker(
        ops_db_path=db_path,
        adapter=adapter,
        repo=repo,
        execution_account_id="test_account",
    )

    inserted_count = worker.run_trade_based_reconciliation()

    assert inserted_count == 0

    conn = sqlite3.connect(db_path)
    cnt = conn.execute("SELECT COUNT(*) FROM ops_exchange_events WHERE event_type='TP_FILLED'").fetchone()[0]
    conn.close()
    assert cnt == 1  # still just the one from WS
```

- [ ] **Step 2: Run to confirm tests fail**

```
pytest tests/runtime_v2/execution_gateway/test_fill_identity_dedupe.py::test_trade_based_reconciliation_uses_fill_identity_key tests/runtime_v2/execution_gateway/test_fill_identity_dedupe.py::test_trade_based_reconciliation_skips_when_ws_fill_already_present -v
```

Expected: first test FAILS (key is `TP_FILLED:1` not `fill:exec-rest-999`); second test FAILS (idem key mismatch means deduplication fails to detect the WS fill).

- [ ] **Step 3: Fix `run_trade_based_reconciliation()` in `event_sync.py`**

Find the block that builds `idem_key` and inserts the TP_FILLED event (around line 186-204). Replace:

```python
            # tp_level=None for position-level TPs (no standalone orderLinkId)
            tp_level: int | None = None
            if self._repo.tp_fill_exists(chain_id, tp_level):
                continue  # already recorded (by WS or previous REST run)

            trade = trades[0]
            idem_key = (
                f"TP_FILLED:{chain_id}:level:{tp_level}"
                if tp_level is not None
                else f"TP_FILLED:{chain_id}"
            )
            payload = json.dumps({
                "tp_level": tp_level,
                "fill_price": trade.price,
                "filled_qty": trade.amount,
                "source": "trade_based_reconciliation",
                "exchange_trade_id": trade.trade_id,
            })
            inserted = self._repo.insert_exchange_event(chain_id, "TP_FILLED", payload, idem_key)
```

With:

```python
            # tp_level=None: position-level TPs on Bybit have no standalone orderLinkId
            tp_level: int | None = None
            if self._repo.tp_fill_exists(chain_id):
                continue  # already recorded by WS or previous REST run

            trade = trades[0]
            # Use exchange trade_id as identity key — matches execId from WS watch_my_trades
            idem_key = f"fill:{trade.trade_id}"
            payload = json.dumps({
                "tp_level": tp_level,
                "fill_price": trade.price,
                "filled_qty": trade.amount,
                "source": "trade_based_reconciliation",
                "exchange_trade_id": trade.trade_id,
            })
            inserted = self._repo.insert_exchange_event(chain_id, "TP_FILLED", payload, idem_key)
```

- [ ] **Step 4: Run tests to confirm they pass**

```
pytest tests/runtime_v2/execution_gateway/test_fill_identity_dedupe.py -v
```

Expected: all pass.

- [ ] **Step 5: Run full gateway suite**

```
pytest tests/runtime_v2/execution_gateway/ -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```
git add src/runtime_v2/execution_gateway/event_sync.py
git add tests/runtime_v2/execution_gateway/test_fill_identity_dedupe.py
git commit -m "fix: trade_based_reconciliation uses fill identity key, converges with WS"
```

---

## Task 4: Guard in `run_position_reconciliation()` — skip synthetic close when real fill exists

**Files:**
- Modify: `src/runtime_v2/execution_gateway/repositories.py` (add `real_close_fill_exists()`)
- Modify: `src/runtime_v2/execution_gateway/event_sync.py:88-141`
- Modify: `tests/runtime_v2/execution_gateway/test_fill_identity_dedupe.py`

- [ ] **Step 1: Write failing test — position reconciliation skips when TP_FILLED already present**

Add to `tests/runtime_v2/execution_gateway/test_fill_identity_dedupe.py`:

```python
def _make_position_reconciliation_db(tmp_path, insert_tp_fill: bool) -> str:
    """DB with open chain, qty > 0. Optionally pre-inserts a real TP_FILLED."""
    db_path = _make_db(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_trade_chains "
        "(trade_chain_id, symbol, side, lifecycle_state) "
        "VALUES (1, 'ASTERUSDT', 'LONG', 'PARTIALLY_CLOSED')"
    )
    if insert_tp_fill:
        conn.execute(
            "INSERT INTO ops_exchange_events "
            "(trade_chain_id, event_type, payload_json, processing_status, idempotency_key, received_at) "
            "VALUES (1, 'TP_FILLED', '{}', 'DONE', 'fill:exec-ws-111', '2026-06-07T22:14:20Z')"
        )
    conn.commit()
    conn.close()
    return db_path


class _FakePositionAdapter:
    """Returns qty=0 for any position (simulates closed position on exchange)."""
    def get_position_qty(self, symbol, side, execution_account_id):
        return 0.0

    def get_order_status(self, *a, **kw):
        return None

    def get_capabilities(self):
        from src.runtime_v2.execution_gateway.models import AdapterCapabilities
        return AdapterCapabilities(
            place_entry=False, protective_stop_native=False, take_profit_native=False,
            bracket_order=False, move_stop=False, close_partial=False, close_full=False,
            executor_position=False, sync_protective_orders=False,
        )


def test_position_reconciliation_skips_when_tp_fill_exists(tmp_path):
    """Se un TP_FILLED reale esiste già in ops_exchange_events, la position reconciliation
    non deve inserire un CLOSE_FULL_FILLED sintetico."""
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    db_path = _make_position_reconciliation_db(tmp_path, insert_tp_fill=True)
    repo = GatewayCommandRepository(db_path)
    adapter = _FakePositionAdapter()
    worker = ExchangeEventSyncWorker(
        ops_db_path=db_path,
        adapter=adapter,
        repo=repo,
        execution_account_id="test_account",
    )

    inserted_count = worker.run_position_reconciliation()

    assert inserted_count == 0, "should not insert synthetic close when TP_FILLED is already present"

    conn = sqlite3.connect(db_path)
    synth = conn.execute(
        "SELECT COUNT(*) FROM ops_exchange_events WHERE event_type='CLOSE_FULL_FILLED'"
    ).fetchone()[0]
    conn.close()
    assert synth == 0


def test_position_reconciliation_inserts_when_no_real_fill(tmp_path):
    """Se non c'è nessun fill reale, la position reconciliation deve produrre il close sintetico."""
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    db_path = _make_position_reconciliation_db(tmp_path, insert_tp_fill=False)
    repo = GatewayCommandRepository(db_path)
    adapter = _FakePositionAdapter()
    worker = ExchangeEventSyncWorker(
        ops_db_path=db_path,
        adapter=adapter,
        repo=repo,
        execution_account_id="test_account",
    )

    inserted_count = worker.run_position_reconciliation()

    assert inserted_count == 1

    conn = sqlite3.connect(db_path)
    synth = conn.execute(
        "SELECT COUNT(*) FROM ops_exchange_events WHERE event_type='CLOSE_FULL_FILLED' "
        "AND idempotency_key='CLOSE_FULL_FILLED:ext:1'"
    ).fetchone()[0]
    conn.close()
    assert synth == 1
```

- [ ] **Step 2: Run to confirm tests fail**

```
pytest tests/runtime_v2/execution_gateway/test_fill_identity_dedupe.py::test_position_reconciliation_skips_when_tp_fill_exists tests/runtime_v2/execution_gateway/test_fill_identity_dedupe.py::test_position_reconciliation_inserts_when_no_real_fill -v
```

Expected: first test FAILS — synthetic close is inserted even when TP_FILLED exists. Second test may pass already.

- [ ] **Step 3: Add `real_close_fill_exists()` to `GatewayCommandRepository` in `repositories.py`**

Add this method after `tp_fill_exists()`:

```python
    def real_close_fill_exists(self, trade_chain_id: int) -> bool:
        """Returns True if a real exchange fill event that closes the chain exists.

        Used by position reconciliation to avoid inserting a synthetic CLOSE_FULL_FILLED
        when the WS or REST path has already recorded the actual fill.
        """
        conn = sqlite3.connect(self._db)
        try:
            row = conn.execute(
                "SELECT 1 FROM ops_exchange_events "
                "WHERE trade_chain_id = ? "
                "  AND event_type IN ("
                "    'TP_FILLED', 'SL_FILLED', 'MANUAL_CLOSE_FULL', 'LIQUIDATION_FILLED'"
                "  ) LIMIT 1",
                (trade_chain_id,),
            ).fetchone()
            return row is not None
        finally:
            conn.close()
```

- [ ] **Step 4: Add guard in `run_position_reconciliation()` in `event_sync.py`**

Inside the `if qty == 0.0 and open_qty > 0.0:` block (around line 101), add the guard as the first thing:

```python
                if qty == 0.0 and open_qty > 0.0:
                    # Skip synthetic close if a real fill event already exists.
                    # The lifecycle will close the chain from the WS/REST fill path.
                    if self._repo.real_close_fill_exists(chain_id):
                        continue

                    # Attempt to recover fill price from recent reduce trades (REST safety net)
                    fill_price: float | None = None
                    # ... rest of existing code unchanged
```

- [ ] **Step 5: Run tests to confirm they pass**

```
pytest tests/runtime_v2/execution_gateway/test_fill_identity_dedupe.py -v
```

Expected: all pass.

- [ ] **Step 6: Run full test suite**

```
pytest tests/runtime_v2/execution_gateway/ tests/runtime_v2/lifecycle/ -v
```

Expected: all pass.

- [ ] **Step 7: Commit**

```
git add src/runtime_v2/execution_gateway/repositories.py
git add src/runtime_v2/execution_gateway/event_sync.py
git add tests/runtime_v2/execution_gateway/test_fill_identity_dedupe.py
git commit -m "fix: position reconciliation skips synthetic close when real fill already exists"
```

---

## Task 5: End-to-end scenario test — Bybit position-level TP without orderLinkId

**Files:**
- Modify: `tests/runtime_v2/execution_gateway/test_fill_identity_dedupe.py`

This is the exact scenario from chain 1 in the ops DB: two position-level TPs (no orderLinkId), same chain, tp_level=None, different execIds.

- [ ] **Step 1: Write the end-to-end test**

Add to `tests/runtime_v2/execution_gateway/test_fill_identity_dedupe.py`:

```python
def test_bybit_position_level_tp_full_scenario(tmp_path):
    """Scenario completo: TP1 parziale + TP finale via WS, entrambi tp_level=None,
    nessun orderLinkId. Entrambi devono entrare in ops_exchange_events.
    La position reconciliation non deve aggiungere un CLOSE_FULL_FILLED sintetico."""
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    db_path = _make_db(tmp_path)
    repo = GatewayCommandRepository(db_path)

    # ── Step 1: WS riceve TP1 parziale ────────────────────────────────────────
    tp1 = _make_tp_fill("exec-tp1-aaa", "exec:exec-tp1-aaa", exec_qty=7070.0)
    inserted1 = repo.insert_raw_and_classified(tp1)
    assert inserted1 is True

    # ── Step 2: WS riceve TP finale ───────────────────────────────────────────
    tp2 = _make_tp_fill("exec-tp2-bbb", "exec:exec-tp2-bbb", exec_qty=7071.0)
    inserted2 = repo.insert_raw_and_classified(tp2)
    assert inserted2 is True, "TP finale deve essere inserito — execId diverso da TP1"

    # ── Step 3: ops_exchange_events ha entrambi ───────────────────────────────
    conn = sqlite3.connect(db_path)
    tp_rows = conn.execute(
        "SELECT idempotency_key FROM ops_exchange_events WHERE event_type='TP_FILLED'"
    ).fetchall()
    conn.close()
    assert len(tp_rows) == 2
    keys = {r[0] for r in tp_rows}
    assert "fill:exec-tp1-aaa" in keys
    assert "fill:exec-tp2-bbb" in keys

    # ── Step 4: position reconciliation non inserisce CLOSE_FULL_FILLED ───────
    # (simula: exchange riporta qty=0 per la chain)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_trade_chains "
        "(trade_chain_id, symbol, side, lifecycle_state) "
        "VALUES (1, 'ASTERUSDT', 'LONG', 'PARTIALLY_CLOSED')"
    )
    conn.commit()
    conn.close()

    adapter = _FakePositionAdapter()
    worker = ExchangeEventSyncWorker(
        ops_db_path=db_path,
        adapter=adapter,
        repo=repo,
        execution_account_id="test_account",
    )
    synth_count = worker.run_position_reconciliation()

    assert synth_count == 0, "no synthetic close — the WS fills are sufficient"

    conn = sqlite3.connect(db_path)
    synth = conn.execute(
        "SELECT COUNT(*) FROM ops_exchange_events WHERE event_type='CLOSE_FULL_FILLED'"
    ).fetchone()[0]
    conn.close()
    assert synth == 0
```

- [ ] **Step 2: Run to confirm test passes** (all prior tasks already in place)

```
pytest tests/runtime_v2/execution_gateway/test_fill_identity_dedupe.py::test_bybit_position_level_tp_full_scenario -v
```

Expected: PASS.

- [ ] **Step 3: Run full test suite**

```
pytest tests/ -v --tb=short
```

Expected: all tests pass.

- [ ] **Step 4: Commit**

```
git add tests/runtime_v2/execution_gateway/test_fill_identity_dedupe.py
git commit -m "test: e2e scenario for Bybit position-level TP without orderLinkId"
```

---

## Self-Review

**Spec coverage:**
- ✅ "Due fill distinti della stessa chain non possono collidere solo perché condividono event_type=TP_FILLED e tp_level=None" → Task 1 (identity key), tested in Task 1 + Task 5
- ✅ "Lo stesso fill osservato sia via WS che via REST produce un solo evento logico" → Task 3 (same `fill:<execId>` key)
- ✅ "watch_positions con pos_qty=0 non deve essere la fonte primaria della chiusura" → already correct in classifier (returns UNKNOWN), confirmed untouched
- ✅ "La reconciliation REST deve inserire eventi mancanti solo quando il fill non è già stato acquisito" → Task 3 (`tp_fill_exists`) + Task 4 (guard)
- ✅ "Il caso Bybit TP attached position-level senza orderLinkId: TP singoli, TP multipli, close manuali, downtime" → Task 5 integration test
- ✅ Test unitari su dedupe identity vs classification → Task 1
- ✅ Test integrazione WS + REST sullo stesso fill → Task 3
- ✅ Test sul caso TP multipli con tp_level=None → Task 1 + Task 5
- ✅ Test watch_positions qty=0 senza fill trade → Task 4
- ✅ Lifecycle `_process_tp_filled` uses `new_open <= 0.0` for is_final — already correct, **no changes needed**

**No placeholders found.**

**Type consistency:** `real_close_fill_exists(trade_chain_id: int) -> bool` added in Task 4 Step 3, called in Task 4 Step 4 with same name. `tp_fill_exists(trade_chain_id, tp_level=None)` signature updated in Task 2, callers updated in Task 3 (pass one arg). Consistent throughout.
