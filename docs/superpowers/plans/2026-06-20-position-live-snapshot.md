# Position Live Snapshot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace per-chain REST position polling (N calls / tick) with a single bulk `fetch_positions()` per account every 60s, writing mark_price + UPL + cum_realized_pnl into `ops_position_snapshots`, and expose those values in the dashboard "Attivi" view.

**Architecture:** One new `fetch_all_positions()` adapter method (Bybit bulk, Fake injectable) feeds `run_bulk_position_sync()` in `ExchangeEventSyncWorker`, which upserts into a migrated `ops_position_snapshots` table (PK: account_id, symbol, side). `StatusQueries.get_open_trades()` reads from that table instead of `ops_market_snapshots`, populating the new `TradeRow.cum_realized_pnl` field and passing it to the dashboard formatter.

**Tech Stack:** Python 3.11, SQLite (via sqlite3), ccxt, pydantic v2, pytest

## Global Constraints

- SQLite schema changes use rename-and-copy pattern (no ALTER COLUMN). Never hand-write migration SQL that is not idempotent.
- All new ops DB schema changes go into a new file in `db/ops_migrations/` (next number after `018`).
- `WebsocketConfig` uses pydantic `ConfigDict(extra="forbid")` — renaming a field requires removing the old one.
- No new production dependencies.
- `run_trade_based_reconciliation()` and `run_protective_orders_reconciliation()` remain unchanged and are still called in the same timer.
- `run_position_reconciliation()` is removed from `ExchangeEventSyncWorker` entirely after Task 3.
- `ops_market_snapshots` is NOT modified — still written by entry gate; just no longer read by dashboard.
- Template visual redesign is out of scope — `cum_realized_pnl` appears minimally (only if `!= 0` and not `None`).
- Tests use `db/ops_migrations/` via `apply_migrations()` — any new migration SQL must be compatible with the test helper `_create_ops_db()` pattern in existing tests.

---

### Task 1: DB migration + repository upsert method

**Files:**
- Create: `db/ops_migrations/019_ops_position_snapshots_upsert.sql`
- Modify: `src/runtime_v2/execution_gateway/repositories.py` (add `upsert_position_snapshot()`, remove `insert_position_snapshot()`)
- Test: `tests/runtime_v2/execution_gateway/test_repositories_position_snapshot.py` (new file)

**Interfaces:**
- Produces: `GatewayCommandRepository.upsert_position_snapshot(*, account_id: str, symbol: str, side: str, qty: float | None, mark_price: float | None, unrealized_pnl: float | None, cum_realized_pnl: float | None, source: str, captured_at: str) -> None`

- [ ] **Step 1: Write failing tests**

Create `tests/runtime_v2/execution_gateway/test_repositories_position_snapshot.py`:

```python
import sqlite3
import tempfile
import os
import pytest

from src.runtime_v2.db_migrations import apply_migrations
from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository


def _make_db() -> str:
    tf = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
    tf.close()
    apply_migrations(db_path=tf.name, migrations_dir="db/ops_migrations")
    return tf.name


def test_upsert_position_snapshot_creates_row():
    db = _make_db()
    repo = GatewayCommandRepository(db)
    repo.upsert_position_snapshot(
        account_id="demo_1",
        symbol="BTC/USDT:USDT",
        side="LONG",
        qty=0.01,
        mark_price=65000.0,
        unrealized_pnl=50.0,
        cum_realized_pnl=10.0,
        source="bulk_position_sync",
        captured_at="2026-06-20T10:00:00+00:00",
    )
    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT qty, mark_price, unrealized_pnl, cum_realized_pnl, source "
        "FROM ops_position_snapshots WHERE account_id=? AND symbol=? AND side=?",
        ("demo_1", "BTC/USDT:USDT", "LONG"),
    ).fetchone()
    conn.close()
    os.unlink(db)
    assert row is not None
    assert row[0] == pytest.approx(0.01)
    assert row[1] == pytest.approx(65000.0)
    assert row[2] == pytest.approx(50.0)
    assert row[3] == pytest.approx(10.0)
    assert row[4] == "bulk_position_sync"


def test_upsert_position_snapshot_overwrites_existing():
    db = _make_db()
    repo = GatewayCommandRepository(db)
    kwargs = dict(
        account_id="demo_1",
        symbol="ETH/USDT:USDT",
        side="SHORT",
        qty=1.0,
        mark_price=3000.0,
        unrealized_pnl=-20.0,
        cum_realized_pnl=5.0,
        source="bulk_position_sync",
        captured_at="2026-06-20T10:00:00+00:00",
    )
    repo.upsert_position_snapshot(**kwargs)
    repo.upsert_position_snapshot(**{**kwargs, "mark_price": 3100.0, "captured_at": "2026-06-20T10:01:00+00:00"})

    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT COUNT(*), mark_price FROM ops_position_snapshots WHERE account_id=? AND symbol=? AND side=?",
        ("demo_1", "ETH/USDT:USDT", "SHORT"),
    ).fetchone()
    conn.close()
    os.unlink(db)
    assert rows[0] == 1  # only one row (upsert, not insert)
    assert rows[1] == pytest.approx(3100.0)


def test_new_schema_has_no_snapshot_id_column():
    db = _make_db()
    conn = sqlite3.connect(db)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(ops_position_snapshots)").fetchall()]
    conn.close()
    os.unlink(db)
    assert "snapshot_id" not in cols
    assert "account_id" in cols
    assert "cum_realized_pnl" in cols
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/runtime_v2/execution_gateway/test_repositories_position_snapshot.py -v
```
Expected: FAIL — `upsert_position_snapshot` not defined, `cum_realized_pnl` column missing.

- [ ] **Step 3: Write migration SQL**

Create `db/ops_migrations/019_ops_position_snapshots_upsert.sql`:

```sql
-- Migrate ops_position_snapshots from append-log (snapshot_id PK) to upsert-live
-- (account_id, symbol, side composite PK). Historical rows are discarded — no
-- functional consumer uses them.
CREATE TABLE IF NOT EXISTS ops_position_snapshots_new (
    account_id        TEXT NOT NULL,
    symbol            TEXT NOT NULL,
    side              TEXT NOT NULL,
    qty               REAL,
    mark_price        REAL,
    unrealized_pnl    REAL,
    cum_realized_pnl  REAL,
    source            TEXT,
    captured_at       TEXT NOT NULL,
    PRIMARY KEY (account_id, symbol, side)
);

INSERT OR IGNORE INTO ops_position_snapshots_new (account_id, symbol, side, captured_at, source)
SELECT account_id, symbol, side, captured_at, source
FROM ops_position_snapshots
WHERE true
ON CONFLICT DO NOTHING;

DROP TABLE ops_position_snapshots;
ALTER TABLE ops_position_snapshots_new RENAME TO ops_position_snapshots;
```

- [ ] **Step 4: Add `upsert_position_snapshot()` and remove `insert_position_snapshot()`**

In `src/runtime_v2/execution_gateway/repositories.py`, replace the `insert_position_snapshot` method (lines ~1023-1044) with:

```python
def upsert_position_snapshot(
    self,
    *,
    account_id: str,
    symbol: str,
    side: str,
    qty: float | None,
    mark_price: float | None,
    unrealized_pnl: float | None,
    cum_realized_pnl: float | None,
    source: str,
    captured_at: str,
) -> None:
    conn = sqlite3.connect(self._db)
    try:
        conn.execute(
            "INSERT INTO ops_position_snapshots "
            "(account_id, symbol, side, qty, mark_price, unrealized_pnl, "
            " cum_realized_pnl, source, captured_at) "
            "VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(account_id, symbol, side) DO UPDATE SET "
            "qty=excluded.qty, mark_price=excluded.mark_price, "
            "unrealized_pnl=excluded.unrealized_pnl, "
            "cum_realized_pnl=excluded.cum_realized_pnl, "
            "source=excluded.source, captured_at=excluded.captured_at",
            (account_id, symbol, side, qty, mark_price, unrealized_pnl,
             cum_realized_pnl, source, captured_at),
        )
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 5: Run tests to verify they pass**

```
pytest tests/runtime_v2/execution_gateway/test_repositories_position_snapshot.py -v
```
Expected: 3 PASS.

- [ ] **Step 6: Commit**

```bash
git add db/ops_migrations/019_ops_position_snapshots_upsert.sql \
        src/runtime_v2/execution_gateway/repositories.py \
        tests/runtime_v2/execution_gateway/test_repositories_position_snapshot.py
git commit -m "feat(ops-db): migrate ops_position_snapshots to upsert schema with PnL fields"
```

---

### Task 2: Adapter layer — RawPositionLive + fetch_all_positions()

**Files:**
- Modify: `src/runtime_v2/execution_gateway/adapters/base.py` (add `RawPositionLive`, add `fetch_all_positions()` default)
- Modify: `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/adapter.py` (implement `fetch_all_positions()`)
- Modify: `src/runtime_v2/execution_gateway/adapters/fake.py` (add `set_position_live()` + `fetch_all_positions()`)
- Test: `tests/runtime_v2/execution_gateway/test_adapter_position_live.py` (new file)

**Interfaces:**
- Consumes: nothing from Task 1 (independent adapter layer)
- Produces:
  - `RawPositionLive` dataclass in `adapters/base.py`
  - `ExecutionAdapter.fetch_all_positions(execution_account_id: str) -> list[RawPositionLive] | None` (default: `return None`)
  - `FakeAdapter.set_position_live(positions: list[RawPositionLive]) -> None`
  - `FakeAdapter.fetch_all_positions(execution_account_id: str) -> list[RawPositionLive]`

- [ ] **Step 1: Write failing tests**

Create `tests/runtime_v2/execution_gateway/test_adapter_position_live.py`:

```python
from src.runtime_v2.execution_gateway.adapters.base import RawPositionLive, ExecutionAdapter
from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter


def test_raw_position_live_fields():
    pos = RawPositionLive(
        symbol="BTC/USDT:USDT",
        side="LONG",
        qty=0.1,
        mark_price=65000.0,
        unrealized_pnl=50.0,
        cum_realized_pnl=20.0,
    )
    assert pos.symbol == "BTC/USDT:USDT"
    assert pos.side == "LONG"
    assert pos.qty == 0.1
    assert pos.mark_price == 65000.0
    assert pos.unrealized_pnl == 50.0
    assert pos.cum_realized_pnl == 20.0


def test_execution_adapter_default_returns_none():
    # The default implementation returns None (graceful no-op for adapters that don't support it)
    # Use FakeAdapter which does NOT have set_position_live called yet
    adapter = FakeAdapter()
    # Reset any injectable state
    adapter._position_lives = []
    result = adapter.fetch_all_positions("demo_1")
    assert result == []


def test_fake_adapter_injectable_positions():
    adapter = FakeAdapter()
    positions = [
        RawPositionLive("BTC/USDT:USDT", "LONG", 0.1, 65000.0, 50.0, 20.0),
        RawPositionLive("ETH/USDT:USDT", "SHORT", 1.0, 3000.0, -10.0, 5.0),
    ]
    adapter.set_position_live(positions)
    result = adapter.fetch_all_positions("demo_1")
    assert len(result) == 2
    assert result[0].symbol == "BTC/USDT:USDT"
    assert result[1].side == "SHORT"


def test_fake_adapter_position_live_none_fields():
    adapter = FakeAdapter()
    adapter.set_position_live([
        RawPositionLive("BTC/USDT:USDT", "LONG", 0.0, None, None, None),
    ])
    result = adapter.fetch_all_positions("any_account")
    assert result[0].mark_price is None
    assert result[0].cum_realized_pnl is None
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/runtime_v2/execution_gateway/test_adapter_position_live.py -v
```
Expected: FAIL — `RawPositionLive` not defined.

- [ ] **Step 3: Add `RawPositionLive` and `fetch_all_positions()` to `adapters/base.py`**

In `src/runtime_v2/execution_gateway/adapters/base.py`, add after existing dataclass imports (find the existing `RawPositionDetails` or similar dataclasses area):

```python
@dataclass
class RawPositionLive:
    symbol: str
    side: str              # 'LONG' | 'SHORT'
    qty: float
    mark_price: float | None
    unrealized_pnl: float | None
    cum_realized_pnl: float | None
```

In `ExecutionAdapter` ABC, add the optional method (same pattern as `fetch_market_snapshot`):

```python
def fetch_all_positions(self, execution_account_id: str) -> list[RawPositionLive] | None:
    return None
```

- [ ] **Step 4: Implement `fetch_all_positions()` in `CcxtBybitAdapter`**

In `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/adapter.py`, add:

```python
def fetch_all_positions(self, execution_account_id: str) -> list[RawPositionLive] | None:
    try:
        positions = self._exchange.fetch_positions()
        result = []
        for pos in positions:
            side_raw = str(pos.get("side") or "").upper()
            if side_raw not in ("LONG", "SHORT"):
                continue
            info = pos.get("info") or {}
            result.append(RawPositionLive(
                symbol=pos.get("symbol") or "",
                side=side_raw,
                qty=float(pos.get("contracts") or 0.0),
                mark_price=_safe_float(pos.get("markPrice")),
                unrealized_pnl=_safe_float(pos.get("unrealizedPnl")),
                cum_realized_pnl=_safe_float(info.get("cumRealisedPnl")),
            ))
        return result
    except Exception as exc:
        logger.warning("fetch_all_positions failed: %s", exc)
        return None
```

Add the import at the top of the adapter file (with other base imports):
```python
from src.runtime_v2.execution_gateway.adapters.base import RawPositionLive
```

- [ ] **Step 5: Add `set_position_live()` and `fetch_all_positions()` to `FakeAdapter`**

In `src/runtime_v2/execution_gateway/adapters/fake.py`:

In the `FakeAdapter.__init__()`, add:
```python
self._position_lives: list[RawPositionLive] = []
```

Add two methods to `FakeAdapter`:
```python
def set_position_live(self, positions: list[RawPositionLive]) -> None:
    self._position_lives = list(positions)

def fetch_all_positions(self, execution_account_id: str) -> list[RawPositionLive]:
    return list(self._position_lives)
```

Add the import at the top of `fake.py`:
```python
from src.runtime_v2.execution_gateway.adapters.base import RawPositionLive
```

- [ ] **Step 6: Run tests to verify they pass**

```
pytest tests/runtime_v2/execution_gateway/test_adapter_position_live.py -v
```
Expected: 4 PASS.

- [ ] **Step 7: Run existing adapter tests to verify no regressions**

```
pytest tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py -v
```
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add src/runtime_v2/execution_gateway/adapters/base.py \
        src/runtime_v2/execution_gateway/adapters/ccxt_bybit/adapter.py \
        src/runtime_v2/execution_gateway/adapters/fake.py \
        tests/runtime_v2/execution_gateway/test_adapter_position_live.py
git commit -m "feat(adapter): add RawPositionLive + fetch_all_positions() to base, ccxt_bybit, fake"
```

---

### Task 3: ExchangeEventSyncWorker.run_bulk_position_sync()

**Files:**
- Modify: `src/runtime_v2/execution_gateway/event_sync.py` (add `run_bulk_position_sync()`, remove `run_position_reconciliation()`)
- Test: `tests/runtime_v2/execution_gateway/test_event_sync.py` (add new cases, update callers of removed method)

**Interfaces:**
- Consumes: `RawPositionLive` from Task 2, `upsert_position_snapshot()` from Task 1
- Produces: `ExchangeEventSyncWorker.run_bulk_position_sync() -> int`

- [ ] **Step 1: Read the existing test file to understand fixture setup**

```
# Read first 100 lines of tests/runtime_v2/execution_gateway/test_event_sync.py
# to understand how _make_worker() and the DB fixture are built
```

(Do this before writing tests — the fixture pattern must match the existing file.)

- [ ] **Step 2: Write failing tests for run_bulk_position_sync()**

In `tests/runtime_v2/execution_gateway/test_event_sync.py`, add at the end:

```python
# --- run_bulk_position_sync tests ---

from src.runtime_v2.execution_gateway.adapters.base import RawPositionLive


def test_run_bulk_position_sync_writes_snapshot(tmp_path):
    """Bulk sync upserts a row into ops_position_snapshots for each live position."""
    db = str(tmp_path / "ops.sqlite3")
    apply_migrations(db_path=db, migrations_dir="db/ops_migrations")
    adapter = FakeAdapter()
    repo = GatewayCommandRepository(db)
    worker = ExchangeEventSyncWorker(
        ops_db_path=db,
        adapter=adapter,
        repo=repo,
        execution_account_id="demo_1",
    )
    adapter.set_position_live([
        RawPositionLive("BTC/USDT:USDT", "LONG", 0.1, 65000.0, 50.0, 20.0),
    ])

    worker.run_bulk_position_sync()

    import sqlite3
    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT qty, mark_price, unrealized_pnl, cum_realized_pnl "
        "FROM ops_position_snapshots WHERE account_id=? AND symbol=? AND side=?",
        ("demo_1", "BTC/USDT:USDT", "LONG"),
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == pytest.approx(0.1)
    assert row[1] == pytest.approx(65000.0)
    assert row[2] == pytest.approx(50.0)
    assert row[3] == pytest.approx(20.0)


def test_run_bulk_position_sync_returns_zero_when_adapter_returns_none(tmp_path):
    """If fetch_all_positions returns None (error), sync is a no-op."""
    db = str(tmp_path / "ops2.sqlite3")
    apply_migrations(db_path=db, migrations_dir="db/ops_migrations")
    adapter = FakeAdapter()
    adapter._position_lives = None  # force None return
    repo = GatewayCommandRepository(db)
    worker = ExchangeEventSyncWorker(
        ops_db_path=db,
        adapter=adapter,
        repo=repo,
        execution_account_id="demo_1",
    )

    # Override fetch_all_positions to return None
    adapter.fetch_all_positions = lambda _: None
    result = worker.run_bulk_position_sync()
    assert result == 0


def test_run_bulk_position_sync_detects_zero_qty_close(tmp_path):
    """Position with qty=0 from bulk that has a confirmed reduce trade triggers synthetic close."""
    # This test follows the same pattern as existing run_position_reconciliation tests.
    # Set up an OPEN trade chain, mock fetch_recent_reduce_trades to return a trade,
    # set bulk position live to return qty=0 for that symbol/side.
    # Verify that insert_exchange_event is called with CLOSE_FULL_FILLED.
    db = str(tmp_path / "ops3.sqlite3")
    apply_migrations(db_path=db, migrations_dir="db/ops_migrations")
    adapter = FakeAdapter()
    repo = GatewayCommandRepository(db)

    # Insert a fake open chain
    import sqlite3 as _sqlite3
    conn = _sqlite3.connect(db)
    conn.execute(
        "INSERT INTO ops_trade_chains "
        "(trade_chain_id, symbol, side, lifecycle_state, account_id, "
        " trader_id, open_position_qty) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (42, "ETH/USDT:USDT", "LONG", "OPEN", "demo_1", "trader_a", 1.0),
    )
    conn.commit()
    conn.close()

    # No live position for that symbol/side → qty=0
    adapter.set_position_live([])

    # Fake a reduce trade so fill_price is found (skip consecutive-zero logic)
    class _FakeTrade:
        price = 3000.0
        amount = 1.0
        fee = 3.0

    adapter.fetch_recent_reduce_trades = lambda **_: [_FakeTrade()]

    worker = ExchangeEventSyncWorker(
        ops_db_path=db,
        adapter=adapter,
        repo=repo,
        execution_account_id="demo_1",
    )
    result = worker.run_bulk_position_sync()

    assert result == 1
    conn = _sqlite3.connect(db)
    row = conn.execute(
        "SELECT event_type FROM ops_exchange_events WHERE trade_chain_id=42"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "CLOSE_FULL_FILLED"
```

- [ ] **Step 3: Run tests to verify they fail**

```
pytest tests/runtime_v2/execution_gateway/test_event_sync.py::test_run_bulk_position_sync_writes_snapshot -v
```
Expected: FAIL — `run_bulk_position_sync` not defined.

- [ ] **Step 4: Implement `run_bulk_position_sync()` in `event_sync.py`**

In `src/runtime_v2/execution_gateway/event_sync.py`, add imports at top:

```python
from datetime import datetime, timezone
```
(Check if already imported — if so skip.)

Add `run_bulk_position_sync()` method to `ExchangeEventSyncWorker` (insert before `_get_open_chains()`):

```python
def run_bulk_position_sync(self) -> int:
    """Bulk fetch all positions for this account, upsert into ops_position_snapshots,
    and detect externally-closed chains (replaces run_position_reconciliation)."""
    positions = self._adapter.fetch_all_positions(self._execution_account_id)
    if positions is None:
        return 0

    now = datetime.now(timezone.utc).isoformat()
    for pos in positions:
        self._repo.upsert_position_snapshot(
            account_id=self._execution_account_id,
            symbol=pos.symbol,
            side=pos.side,
            qty=pos.qty,
            mark_price=pos.mark_price,
            unrealized_pnl=pos.unrealized_pnl,
            cum_realized_pnl=pos.cum_realized_pnl,
            source="bulk_position_sync",
            captured_at=now,
        )

    live_index = {(p.symbol, p.side): p.qty for p in positions}
    open_chains = self._get_open_chains()
    processed = 0
    for chain_id, symbol, side, open_qty in open_chains:
        try:
            qty = live_index.get((symbol, side.upper()), 0.0)
            if qty > 0.0:
                self._position_zero_count.pop(chain_id, None)
                continue
            if open_qty <= 0.0:
                continue

            if self._repo.real_close_fill_exists(chain_id):
                self._position_zero_count.pop(chain_id, None)
                continue

            fill_price: float | None = None
            exec_fee: float | None = None
            fee_rate: float | None = None
            if hasattr(self._adapter, "fetch_recent_reduce_trades"):
                try:
                    trades = self._adapter.fetch_recent_reduce_trades(
                        symbol=symbol,
                        side=side,
                        execution_account_id=self._execution_account_id,
                        limit=50,
                    )
                    fill_price, exec_fee, fee_rate = _reduce_trade_stats(trades)
                except Exception:
                    logger.warning(
                        "could not fetch fill price for bulk sync close: chain=%s", chain_id
                    )

            if fill_price is None:
                count = self._position_zero_count.get(chain_id, 0) + 1
                self._position_zero_count[chain_id] = count
                if count < _POSITION_ZERO_CONFIRM_REQUIRED:
                    logger.warning(
                        "position qty=0 from bulk but no reduce trade: "
                        "chain=%s %s %s (zero_count=%d/%d) — deferring synthetic close",
                        chain_id, symbol, side, count, _POSITION_ZERO_CONFIRM_REQUIRED,
                    )
                    continue
                logger.warning(
                    "position qty=0 confirmed %d consecutive times without reduce trade: "
                    "chain=%s %s %s — generating synthetic close",
                    count, chain_id, symbol, side,
                )

            self._position_zero_count.pop(chain_id, None)
            idem_key = f"CLOSE_FULL_FILLED:ext:{chain_id}"
            payload = json.dumps({
                "filled_qty": open_qty,
                "fill_price": fill_price,
                "exec_fee": exec_fee,
                "fee_rate": fee_rate,
                "source": "bulk_position_sync",
            })
            inserted = self._repo.insert_exchange_event(
                chain_id, "CLOSE_FULL_FILLED", payload, idem_key
            )
            if inserted:
                logger.info(
                    "externally closed position detected via bulk sync: chain=%s %s %s qty=%s fill_price=%s",
                    chain_id, symbol, side, open_qty, fill_price,
                )
                self._wake()
                processed += 1
        except Exception:
            logger.exception("bulk position sync error for chain %s", chain_id)
    return processed
```

- [ ] **Step 5: Remove `run_position_reconciliation()` from `ExchangeEventSyncWorker`**

Delete the entire `run_position_reconciliation()` method (lines ~93–190 in `event_sync.py`).

- [ ] **Step 6: Run new tests to verify they pass**

```
pytest tests/runtime_v2/execution_gateway/test_event_sync.py -v -k "bulk"
```
Expected: 3 PASS.

- [ ] **Step 7: Run full event_sync test suite**

```
pytest tests/runtime_v2/execution_gateway/test_event_sync.py -v
```
Fix any test that called `run_position_reconciliation()` directly — update those calls to `run_bulk_position_sync()`.

- [ ] **Step 8: Commit**

```bash
git add src/runtime_v2/execution_gateway/event_sync.py \
        tests/runtime_v2/execution_gateway/test_event_sync.py
git commit -m "feat(event-sync): add run_bulk_position_sync(), remove run_position_reconciliation()"
```

---

### Task 4: Config + wiring

**Files:**
- Modify: `src/runtime_v2/execution_gateway/models.py` (rename field in `WebsocketConfig`)
- Modify: `config/execution.yaml` (add new field, remove old for all 3 adapters)
- Modify: `main.py` (update `_make_pos_recon()` and `AdapterExecutionContext` instantiation + startup reconciliation call)
- Modify: `main_linux_server.py` (same changes as `main.py`)
- Test: `tests/runtime_v2/execution_gateway/test_adapter_context.py` (update model usage if needed); verify integration test bootstrap with new config field

**Interfaces:**
- Consumes: `run_bulk_position_sync()` from Task 3
- Produces: `WebsocketConfig.position_live_snapshot_interval_seconds: int = 60` (replaces `position_reconciliation_interval_seconds`)

- [ ] **Step 1: Update `WebsocketConfig` in `models.py`**

In `src/runtime_v2/execution_gateway/models.py`, in `WebsocketConfig`, replace:
```python
position_reconciliation_interval_seconds: int = 600
```
with:
```python
position_live_snapshot_interval_seconds: int = 60
```

- [ ] **Step 2: Update `config/execution.yaml`**

For all three adapter blocks (`bybit_demo_1`, `bybit_demo_2`, `bybit_demo_3`), in the `websocket:` section, add:
```yaml
position_live_snapshot_interval_seconds: 60
```

Remove `position_reconciliation_interval_seconds` if it appears (it is not currently in the yaml — the model default was used).

- [ ] **Step 3: Update `main.py`**

In `main.py`:

a) In `_make_pos_recon()` (lines ~236–242), change `w.run_position_reconciliation()` to `w.run_bulk_position_sync()`:
```python
def _make_pos_recon(ws=workers):
    def _pos_recon():
        for w in ws:
            w.run_bulk_position_sync()
            w.run_trade_based_reconciliation()
            w.run_protective_orders_reconciliation()
    return _pos_recon
```

b) In `AdapterExecutionContext` instantiation (lines ~244–253), change:
```python
position_reconciliation_interval_seconds=float(
    adp_cfg.websocket.position_reconciliation_interval_seconds
),
```
to:
```python
position_reconciliation_interval_seconds=float(
    adp_cfg.websocket.position_live_snapshot_interval_seconds
),
```

c) In the startup reconciliation block (lines ~690–694), change:
```python
worker.run_position_reconciliation()
```
to:
```python
worker.run_bulk_position_sync()
```

d) In the `ExecutionRuntimeState` dataclass (lines ~80–89), update the field `position_reconciliation_interval_seconds` if it references the old model field — grep for this and update to `position_live_snapshot_interval_seconds` as needed.

- [ ] **Step 4: Update `main_linux_server.py` (same three changes)**

Mirror the exact changes from Step 3 in `main_linux_server.py`.

- [ ] **Step 5: Run config-level tests**

```
pytest tests/runtime_v2/execution_gateway/test_adapter_context.py -v
pytest tests/runtime_v2/test_main_runtime_bootstrap.py -v
```
Expected: all PASS. Fix any reference to the old field name.

- [ ] **Step 6: Commit**

```bash
git add src/runtime_v2/execution_gateway/models.py \
        config/execution.yaml \
        main.py \
        main_linux_server.py
git commit -m "feat(config): rename position_reconciliation_interval_seconds → position_live_snapshot_interval_seconds (60s default)"
```

---

### Task 5: StatusQueries — TradeRow + get_open_trades()

**Files:**
- Modify: `src/runtime_v2/control_plane/status_queries.py` (`TradeRow` dataclass + `get_open_trades()`)
- Test: `tests/runtime_v2/control_plane/test_status_queries.py` (add new cases)

**Interfaces:**
- Consumes: `ops_position_snapshots` new schema from Task 1
- Produces:
  - `TradeRow.cum_realized_pnl: float | None = None`
  - `get_open_trades()` reads mark_price, unrealized_pnl, cum_realized_pnl from `ops_position_snapshots` indexed by `(account_id, symbol, side)`

- [ ] **Step 1: Write failing tests**

In `tests/runtime_v2/control_plane/test_status_queries.py`, add:

```python
def test_get_open_trades_reads_from_position_snapshots(ops_db):
    """get_open_trades() returns mark_price and cum_realized_pnl from ops_position_snapshots."""
    import sqlite3
    # Insert an open trade chain
    conn = sqlite3.connect(ops_db)
    conn.execute(
        "INSERT INTO ops_trade_chains "
        "(trade_chain_id, symbol, side, lifecycle_state, account_id, trader_id, "
        " entry_avg_price, open_position_qty) VALUES (?,?,?,?,?,?,?,?)",
        (99, "BTC/USDT:USDT", "LONG", "OPEN", "demo_1", "trader_a", 60000.0, 0.1),
    )
    # Insert a live snapshot
    conn.execute(
        "INSERT INTO ops_position_snapshots "
        "(account_id, symbol, side, qty, mark_price, unrealized_pnl, "
        " cum_realized_pnl, source, captured_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        ("demo_1", "BTC/USDT:USDT", "LONG", 0.1, 65000.0, 500.0, 25.0,
         "bulk_position_sync", "2026-06-20T10:00:00+00:00"),
    )
    conn.commit()
    conn.close()

    from src.runtime_v2.control_plane.status_queries import StatusQueries
    from src.runtime_v2.control_plane.scope_resolver import QueryScope
    sq = StatusQueries(ops_db)
    view = sq.get_open_trades(scope=QueryScope(account_id="demo_1", trader_ids=None))

    assert len(view.rows) == 1
    row = view.rows[0]
    assert row.mark_price == pytest.approx(65000.0)
    assert row.unrealized_pnl == pytest.approx(500.0)
    assert row.cum_realized_pnl == pytest.approx(25.0)


def test_get_open_trades_falls_back_to_internal_upl_when_no_snapshot(ops_db):
    """Without a position snapshot, UPL falls back to internal (mark-entry)*qty*dir calculation."""
    import sqlite3
    conn = sqlite3.connect(ops_db)
    conn.execute(
        "INSERT INTO ops_trade_chains "
        "(trade_chain_id, symbol, side, lifecycle_state, account_id, trader_id, "
        " entry_avg_price, open_position_qty) VALUES (?,?,?,?,?,?,?,?)",
        (100, "ETH/USDT:USDT", "SHORT", "OPEN", "demo_1", "trader_a", 3100.0, 1.0),
    )
    # Insert market snapshot only (old path — no position snapshot)
    if _table_exists_helper(ops_db, "ops_market_snapshots"):
        conn.execute(
            "INSERT INTO ops_market_snapshots "
            "(account_id, symbol, mark_price, captured_at) VALUES (?,?,?,?)",
            ("demo_1", "ETH/USDT:USDT", 3000.0, "2026-06-20T09:00:00+00:00"),
        )
    conn.commit()
    conn.close()

    from src.runtime_v2.control_plane.status_queries import StatusQueries
    from src.runtime_v2.control_plane.scope_resolver import QueryScope
    sq = StatusQueries(ops_db)
    view = sq.get_open_trades(scope=QueryScope(account_id="demo_1", trader_ids=None))

    row = next((r for r in view.rows if r.chain_id == 100), None)
    assert row is not None
    assert row.cum_realized_pnl is None
    # mark_price comes from market snapshot if available, otherwise None
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/runtime_v2/control_plane/test_status_queries.py -v -k "position_snapshot"
```
Expected: FAIL — `TradeRow` has no `cum_realized_pnl`, `get_open_trades` reads wrong table.

- [ ] **Step 3: Add `cum_realized_pnl` to `TradeRow`**

In `src/runtime_v2/control_plane/status_queries.py`, in the `TradeRow` dataclass, add:
```python
cum_realized_pnl: float | None = None
```
(After `mark_captured_at: str | None = None`)

- [ ] **Step 4: Update `get_open_trades()` to read from `ops_position_snapshots`**

Replace the market-snapshot query block in `get_open_trades()` (lines ~391–420):

```python
# Fetch live position snapshot per (account_id, symbol, side) if table exists
pos_snapshots: dict[tuple[str, str], tuple[float | None, float | None, float | None, str]] = {}
if _table_exists(conn, "ops_position_snapshots"):
    account_id_filter = scope.account_id if scope else None
    if account_id_filter:
        snap_rows = conn.execute(
            "SELECT symbol, side, mark_price, unrealized_pnl, cum_realized_pnl, captured_at "
            "FROM ops_position_snapshots "
            "WHERE account_id=?",
            (account_id_filter,),
        ).fetchall()
    else:
        snap_rows = conn.execute(
            "SELECT symbol, side, mark_price, unrealized_pnl, cum_realized_pnl, captured_at "
            "FROM ops_position_snapshots",
        ).fetchall()
    for sym, side_snap, mp, upl, crpnl, cap in snap_rows:
        pos_snapshots[(sym, side_snap)] = (mp, upl, crpnl, cap)
```

Then replace the per-row snap lookup block (lines ~430–457 in the `for r in rows` loop):

```python
mark_price: float | None = None
mark_captured_at: str | None = None
unrealized_pnl: float | None = None
cum_realized_pnl: float | None = None

snap = pos_snapshots.get((symbol, side))
if snap is not None:
    mark_price, bybit_upl, cum_realized_pnl, mark_captured_at = snap
    if bybit_upl is not None:
        unrealized_pnl = bybit_upl
    elif (
        mark_price is not None
        and entry_avg_price is not None
        and open_position_qty is not None
        and open_position_qty != 0
    ):
        direction = 1.0 if side == "LONG" else -1.0
        unrealized_pnl = (mark_price - entry_avg_price) * open_position_qty * direction
```

Also update `TradeRow(...)` construction to include the new field:
```python
trade_rows.append(TradeRow(
    chain_id=chain_id,
    symbol=symbol,
    side=side,
    state=state,
    has_sl=sl_price is not None,
    has_be=be_status == "PROTECTED",
    entry_avg_price=entry_avg_price,
    open_position_qty=open_position_qty,
    unrealized_pnl=unrealized_pnl,
    mark_price=mark_price,
    mark_captured_at=mark_captured_at,
    cum_realized_pnl=cum_realized_pnl,
))
```

Remove the old `mark_prices` variable and `ops_market_snapshots` query entirely from `get_open_trades()`.

**Note:** The `mark_snapshot_max_age_seconds` field in `TradesView` still uses `mark_captured_at` from `pos_snapshots` — the stale check logic continues to work from the `captured_at` of `ops_position_snapshots`.

- [ ] **Step 5: Run new tests**

```
pytest tests/runtime_v2/control_plane/test_status_queries.py -v
```
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/runtime_v2/control_plane/status_queries.py \
        tests/runtime_v2/control_plane/test_status_queries.py
git commit -m "feat(status-queries): TradeRow.cum_realized_pnl, get_open_trades reads ops_position_snapshots"
```

---

### Task 6: Dashboard formatter — expose cum_realized_pnl

**Files:**
- Modify: `src/runtime_v2/control_plane/formatters/dashboard.py` (`_build_attivi_payload()` + template)
- Test: `tests/runtime_v2/control_plane/test_dashboard_formatter.py` (add assertion for new field)

**Interfaces:**
- Consumes: `TradeRow.cum_realized_pnl` from Task 5

- [ ] **Step 1: Write failing test**

In `tests/runtime_v2/control_plane/test_dashboard_formatter.py`, add:

```python
def test_attivi_payload_includes_cum_realized_pnl():
    """row_dicts in attivi payload include cum_realized_pnl field."""
    from src.runtime_v2.control_plane.status_queries import TradeRow, TradesView, StatusView
    from src.runtime_v2.control_plane.formatters.dashboard import _build_attivi_payload
    from src.runtime_v2.control_plane.scope_resolver import QueryScope
    from unittest.mock import MagicMock

    row = TradeRow(
        chain_id=1, symbol="BTC/USDT:USDT", side="LONG", state="OPEN",
        has_sl=True, has_be=False,
        entry_avg_price=60000.0, open_position_qty=0.1,
        unrealized_pnl=500.0, mark_price=65000.0,
        mark_captured_at="2026-06-20T10:00:00+00:00",
        cum_realized_pnl=25.0,
    )
    mock_queries = MagicMock()
    mock_queries.get_open_trades.return_value = TradesView(
        updated_at="2026-06-20T10:00:00", total=1, rows=[row]
    )
    scope = QueryScope(account_id="demo_1", trader_ids=None)

    payload, total = _build_attivi_payload(scope, mock_queries, page=0, page_size=10)

    assert total == 1
    row_dict = payload["rows"][0]
    assert "cum_realized_pnl" in row_dict
    assert row_dict["cum_realized_pnl"] == pytest.approx(25.0)
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/runtime_v2/control_plane/test_dashboard_formatter.py::test_attivi_payload_includes_cum_realized_pnl -v
```
Expected: FAIL — `cum_realized_pnl` not in row_dict.

- [ ] **Step 3: Add `cum_realized_pnl` to `_build_attivi_payload()`**

In `src/runtime_v2/control_plane/formatters/dashboard.py`, in `_build_attivi_payload()`, update `row_dicts`:

```python
row_dicts = [
    {
        "chain_id": r.chain_id,
        "symbol": r.symbol,
        "side": r.side,
        "state": r.state,
        "has_sl": r.has_sl,
        "has_be": r.has_be,
        "entry_avg_price": r.entry_avg_price,
        "open_position_qty": r.open_position_qty,
        "unrealized_pnl": r.unrealized_pnl,
        "cum_realized_pnl": r.cum_realized_pnl,
        "mark_price": r.mark_price,
        "mark_captured_at": r.mark_captured_at,
    }
    for r in page_rows
]
```

- [ ] **Step 4: Update the Jinja/string template for "attivi" to show `cum_realized_pnl`**

Find the template that renders rows in the "attivi" view (look for where `mark_price`, `unrealized_pnl` are formatted). Add a conditional line showing `cum_realized_pnl` only when it is not `None` and not `0`:

In the template string/Jinja block, after the UPL line, add something like:
```
{% if row.cum_realized_pnl and row.cum_realized_pnl != 0 %}  rPnL: {{ row.cum_realized_pnl | pnl_fmt }}{% endif %}
```

(Match the exact template engine and formatting helpers already in use in the file.)

- [ ] **Step 5: Run all formatter tests**

```
pytest tests/runtime_v2/control_plane/test_dashboard_formatter.py -v
```
Expected: all PASS.

- [ ] **Step 6: Run full test suite**

```
pytest -x --tb=short -q
```
Expected: all PASS (or pre-existing failures only, none new).

- [ ] **Step 7: Commit**

```bash
git add src/runtime_v2/control_plane/formatters/dashboard.py \
        tests/runtime_v2/control_plane/test_dashboard_formatter.py
git commit -m "feat(dashboard): expose cum_realized_pnl in attivi payload and template"
```

---

## Post-implementation checklist

- [ ] All 6 task commits are on the branch
- [ ] `pytest -x --tb=short -q` passes (525+ tests)
- [ ] `ops_position_snapshots` migration (`019_...sql`) runs cleanly on a fresh DB
- [ ] `config/execution.yaml` validated: `position_live_snapshot_interval_seconds: 60` present for all 3 adapters
- [ ] `position_reconciliation_interval_seconds` removed from `models.py` and all callers updated
- [ ] `run_position_reconciliation()` fully removed from `event_sync.py` and its callers
- [ ] `TradeRow.cum_realized_pnl` present, `get_open_trades()` no longer reads `ops_market_snapshots`
- [ ] `run_bulk_position_sync()` called in both `main.py` and `main_linux_server.py` `_make_pos_recon()` and startup reconciliation
