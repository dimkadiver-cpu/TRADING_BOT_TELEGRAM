# cancel_unfilled_pending_after Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `cancel_unfilled_pending_after` — a periodic worker that cancels a trade chain's pending entry orders when the market price has already crossed the configured TP level without any fill ever occurring.

**Architecture:** A new `UnfilledPriceWatcher` async worker runs every N seconds (default 60). It queries chains in `WAITING_ENTRY` with no filled legs and `cancel_unfilled_pending_after` set, groups them by symbol to minimise API calls, fetches mark price once per symbol, and emits `UNFILLED_TP_CANCEL` + `CANCEL_PENDING_ENTRY` when the price has crossed the TP threshold. The chain transitions to `EXPIRED`. Reconciliation on restart is free: the first tick behaves identically to a normal tick.

**Tech Stack:** Python asyncio, SQLite (sqlite3), existing `fetch_mark_price` adapter method, existing `cancel_expander` fan-out pattern, existing clean-log template block system.

## Global Constraints

- Never amend existing commits — always create new ones.
- Do not add production dependencies.
- All DB writes use `INSERT OR IGNORE` + idempotency keys (existing pattern).
- `cancel_pending_by_engine = false` must suppress the worker (existing gate).
- Follow the `AccountSnapshotWorker` pattern for the async worker structure.
- Run tests with: `python -m pytest <test_file> -v` from repo root.

---

### Task 1: Event type, outbox map, and clean-log template

**Files:**
- Modify: `src/runtime_v2/lifecycle/models.py:36-49`
- Modify: `src/runtime_v2/control_plane/outbox_writer.py:10-31` and `outbox_writer.py:645-650`
- Modify: `src/runtime_v2/control_plane/formatters/templates/clean_log.py:540-576` and `:684-694`
- Create: `src/runtime_v2/lifecycle/tests/__init__.py` (empty)
- Create: `src/runtime_v2/lifecycle/tests/test_unfilled_watcher.py`

**Interfaces:**
- Produces: `"UNFILLED_TP_CANCEL"` in `LifecycleEventType`, `"ENTRY_CANCELLED_TP_REACHED"` in `_CLEAN_LOG_EVENT_MAP`, `_UNFILLED_TP_CANCEL_BLOCKS` list in clean_log templates dict.

- [ ] **Step 1: Add `UNFILLED_TP_CANCEL` to `LifecycleEventType` in `models.py`**

In `src/runtime_v2/lifecycle/models.py`, find the `LifecycleEventType` Literal (line ~36). Add `"UNFILLED_TP_CANCEL"` to the list:

```python
LifecycleEventType = Literal[
    "SIGNAL_ACCEPTED", "SIGNAL_REJECTED", "SIGNAL_SKIPPED", "TRADE_CHAIN_CREATED", "ENTRY_COMMAND_CREATED",
    "ENTRY_FILLED", "ENTRY_UPDATED", "TP_FILLED", "SL_FILLED", "PENDING_TIMEOUT",
    "TELEGRAM_UPDATE_ACCEPTED", "BE_MOVE_REQUESTED",
    "NOOP_ALREADY_PROTECTED_BE", "NOOP_DUPLICATE_COMMAND",
    "NOOP_ALREADY_CLOSED", "NOOP_NOT_PENDING", "NOOP_NO_APPLICABLE_TARGET",
    "NOOP_CANCEL_CONFIRMED_POSITION_UNRESOLVED",
    "REVIEW_REQUIRED",
    "POSITION_SIZE_UPDATED", "ENTRY_AVG_PRICE_UPDATED",
    "PROTECTIVE_SYNC_REQUESTED", "STOP_MOVE_CONFIRMED", "PENDING_ENTRY_CANCELLED",
    "CLOSE_FULL_FILLED", "CLOSE_PARTIAL_FILLED",
    "AUTO_CANCEL_AVERAGING_REQUESTED",
    "ENGINE_RULE_UPDATE_ACCEPTED",
    "UNFILLED_TP_CANCEL",
]
```

- [ ] **Step 2: Add outbox mapping in `outbox_writer.py`**

In `src/runtime_v2/control_plane/outbox_writer.py`, add to `_CLEAN_LOG_EVENT_MAP`:

```python
"UNFILLED_TP_CANCEL": "ENTRY_CANCELLED_TP_REACHED",
```

Then add a `_build_payload` branch after the `"PENDING_ENTRY_EXPIRED"` block (around line 645):

```python
if notification_type == "ENTRY_CANCELLED_TP_REACHED":
    return {
        **base,
        "tp_level": ev.get("tp_level"),
        "threshold_price": ev.get("threshold_price"),
        "mark_price": ev.get("mark_price"),
        "source": ev.get("source", "unfilled_price_watcher"),
        "link": ev.get("source_message_link"),
    }
```

- [ ] **Step 3: Add clean-log template in `clean_log.py`**

In `src/runtime_v2/control_plane/formatters/templates/clean_log.py`, add after `_PENDING_TIMEOUT_BLOCKS` (around line 544):

```python
_UNFILLED_TP_CANCEL_BLOCKS: list = [
    HeaderBlock(emoji="⛔", event_label="SETUP CANCELLED"),
    DerivedBlock(text_fn=lambda p:
        f"Entry never filled. Price already crossed TP{str(p.get('tp_level', '?')).lstrip('tp').upper()}."
    ),
    FieldBlock("Threshold", key="threshold_price", fmt=num),
    FieldBlock("Mark price", key="mark_price",     fmt=num),
    FooterBlock(default_source="unfilled_price_watcher"),
]
```

Then register it in the `TEMPLATE_MAP` dict (find the block where `"PENDING_ENTRY_EXPIRED"` is registered, around line 689):

```python
"ENTRY_CANCELLED_TP_REACHED": TemplateConfig(_UNFILLED_TP_CANCEL_BLOCKS),
```

- [ ] **Step 4: Create test file and write failing tests**

Create `src/runtime_v2/lifecycle/tests/__init__.py` (empty file).

Create `src/runtime_v2/lifecycle/tests/test_unfilled_watcher.py`:

```python
"""Tests for Task 1: verify plumbing — event type, outbox map, template."""
from src.runtime_v2.lifecycle.models import LifecycleEventType
from src.runtime_v2.control_plane.outbox_writer import _CLEAN_LOG_EVENT_MAP
from src.runtime_v2.control_plane.formatters.templates.clean_log import TEMPLATE_MAP


def test_unfilled_tp_cancel_in_lifecycle_event_type():
    # LifecycleEventType is a Literal — check its args
    import typing
    args = typing.get_args(LifecycleEventType)
    assert "UNFILLED_TP_CANCEL" in args


def test_outbox_map_has_entry_cancelled_tp_reached():
    assert _CLEAN_LOG_EVENT_MAP.get("UNFILLED_TP_CANCEL") == "ENTRY_CANCELLED_TP_REACHED"


def test_template_map_has_entry_cancelled_tp_reached():
    assert "ENTRY_CANCELLED_TP_REACHED" in TEMPLATE_MAP
```

- [ ] **Step 5: Run tests — expect FAIL**

```
python -m pytest src/runtime_v2/lifecycle/tests/test_unfilled_watcher.py -v
```

Expected: 3 FAILED (names not yet defined / not in map).

- [ ] **Step 6: Apply changes from steps 1–3, run tests — expect PASS**

```
python -m pytest src/runtime_v2/lifecycle/tests/test_unfilled_watcher.py -v
```

Expected: 3 PASSED.

- [ ] **Step 7: Commit**

```bash
git add src/runtime_v2/lifecycle/models.py \
        src/runtime_v2/control_plane/outbox_writer.py \
        src/runtime_v2/control_plane/formatters/templates/clean_log.py \
        src/runtime_v2/lifecycle/tests/__init__.py \
        src/runtime_v2/lifecycle/tests/test_unfilled_watcher.py
git commit -m "feat: add UNFILLED_TP_CANCEL event type, outbox map, and clean-log template"
```

---

### Task 2: Repository query — fetch unfilled WAITING_ENTRY chains

**Files:**
- Modify: `src/runtime_v2/lifecycle/repositories.py` — add method to `TradeChainRepository`
- Modify: `src/runtime_v2/lifecycle/tests/test_unfilled_watcher.py` — add DB tests

**Interfaces:**
- Produces: `TradeChainRepository.get_waiting_entry_with_unfilled_cancel_config(batch_size: int) -> list[TradeChain]`
  Returns chains where `lifecycle_state = 'WAITING_ENTRY'` and `management_plan_json` contains `cancel_unfilled_pending_after` ≠ null, with no FILLED legs in `plan_state_json`.

- [ ] **Step 1: Write failing tests for the new repository method**

Append to `src/runtime_v2/lifecycle/tests/test_unfilled_watcher.py`:

```python
import json
import sqlite3
import pytest
from src.runtime_v2.lifecycle.repositories import TradeChainRepository


def _make_ops_db(path: str) -> sqlite3.Connection:
    """Minimal ops DB schema for chain queries."""
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE ops_trade_chains (
            trade_chain_id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_enrichment_id INTEGER, canonical_message_id INTEGER,
            raw_message_id INTEGER, trader_id TEXT, account_id TEXT,
            symbol TEXT, side TEXT, lifecycle_state TEXT, entry_mode TEXT,
            entry_avg_price REAL, current_stop_price REAL,
            expected_stop_price REAL, be_protection_status TEXT,
            entry_timeout_at TEXT, management_plan_json TEXT,
            risk_snapshot_json TEXT, planned_entry_qty REAL,
            filled_entry_qty REAL, open_position_qty REAL,
            closed_position_qty REAL, last_position_sync_at TEXT,
            execution_mode TEXT, risk_already_realized REAL,
            risk_remaining REAL, plan_state_json TEXT,
            source_chat_id INTEGER, telegram_message_id INTEGER,
            external_signal_id TEXT, cumulative_gross_pnl REAL,
            cumulative_fees REAL, cumulative_funding REAL,
            allocated_margin REAL, initial_risk_amount REAL,
            peak_margin_used REAL, created_at TEXT, updated_at TEXT,
            last_projected_event_id INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    return conn


def _insert_chain(conn, *, symbol="BTC/USDT", side="LONG",
                  lifecycle_state="WAITING_ENTRY",
                  cancel_unfilled_pending_after=None,
                  cancel_pending_by_engine=True,
                  plan_legs=None):
    mp = {
        "cancel_unfilled_pending_after": cancel_unfilled_pending_after,
        "cancel_pending_by_engine": cancel_pending_by_engine,
    }
    legs = plan_legs or [{"sequence": 1, "status": "PENDING", "price": 100.0}]
    plan = {"legs": legs}
    conn.execute(
        """INSERT INTO ops_trade_chains
           (symbol, side, lifecycle_state, management_plan_json, plan_state_json,
            risk_snapshot_json, execution_mode, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (symbol, side, lifecycle_state,
         json.dumps(mp), json.dumps(plan), "{}", "D_POSITION_TPSL",
         "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def test_get_waiting_entry_unfilled_returns_eligible(tmp_path):
    db_path = str(tmp_path / "ops.db")
    conn = _make_ops_db(db_path)
    _insert_chain(conn, cancel_unfilled_pending_after="tp1")
    conn.close()

    repo = TradeChainRepository(db_path)
    result = repo.get_waiting_entry_with_unfilled_cancel_config()
    assert len(result) == 1
    assert result[0].symbol == "BTC/USDT"


def test_get_waiting_entry_unfilled_skips_null_config(tmp_path):
    db_path = str(tmp_path / "ops.db")
    conn = _make_ops_db(db_path)
    _insert_chain(conn, cancel_unfilled_pending_after=None)
    conn.close()

    repo = TradeChainRepository(db_path)
    result = repo.get_waiting_entry_with_unfilled_cancel_config()
    assert result == []


def test_get_waiting_entry_unfilled_skips_non_waiting(tmp_path):
    db_path = str(tmp_path / "ops.db")
    conn = _make_ops_db(db_path)
    _insert_chain(conn, lifecycle_state="OPEN", cancel_unfilled_pending_after="tp1")
    conn.close()

    repo = TradeChainRepository(db_path)
    result = repo.get_waiting_entry_with_unfilled_cancel_config()
    assert result == []


def test_get_waiting_entry_unfilled_skips_filled_legs(tmp_path):
    db_path = str(tmp_path / "ops.db")
    conn = _make_ops_db(db_path)
    _insert_chain(
        conn,
        cancel_unfilled_pending_after="tp1",
        plan_legs=[{"sequence": 1, "status": "FILLED", "price": 100.0}],
    )
    conn.close()

    repo = TradeChainRepository(db_path)
    result = repo.get_waiting_entry_with_unfilled_cancel_config()
    assert result == []
```

- [ ] **Step 2: Run tests — expect FAIL**

```
python -m pytest src/runtime_v2/lifecycle/tests/test_unfilled_watcher.py::test_get_waiting_entry_unfilled_returns_eligible -v
```

Expected: FAIL — `AttributeError: 'TradeChainRepository' object has no attribute 'get_waiting_entry_with_unfilled_cancel_config'`.

- [ ] **Step 3: Implement the repository method**

In `src/runtime_v2/lifecycle/repositories.py`, add after `get_timed_out_waiting_entry` (find that method and add below it):

```python
def get_waiting_entry_with_unfilled_cancel_config(self, batch_size: int = 200) -> list[TradeChain]:
    """Return WAITING_ENTRY chains that have cancel_unfilled_pending_after set
    and no FILLED legs in plan_state_json."""
    conn = sqlite3.connect(self._db_path)
    try:
        rows = conn.execute(
            f"""
            SELECT {_CHAIN_COLS}
            FROM ops_trade_chains
            WHERE lifecycle_state = 'WAITING_ENTRY'
              AND json_extract(management_plan_json, '$.cancel_unfilled_pending_after') IS NOT NULL
            LIMIT ?
            """,
            (batch_size,),
        ).fetchall()
    finally:
        conn.close()

    result = []
    for row in rows:
        chain = _chain_from_row(row)
        try:
            plan = json.loads(chain.plan_state_json or "{}")
            legs = plan.get("legs", [])
            if any(leg.get("status") == "FILLED" for leg in legs):
                continue
        except Exception:
            continue
        result.append(chain)
    return result
```

- [ ] **Step 4: Run all repository tests — expect PASS**

```
python -m pytest src/runtime_v2/lifecycle/tests/test_unfilled_watcher.py -v -k "waiting_entry"
```

Expected: 4 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/lifecycle/repositories.py \
        src/runtime_v2/lifecycle/tests/test_unfilled_watcher.py
git commit -m "feat: add get_waiting_entry_with_unfilled_cancel_config to TradeChainRepository"
```

---

### Task 3: Core worker — `UnfilledPriceWatcher`

**Files:**
- Create: `src/runtime_v2/lifecycle/unfilled_price_watcher.py`
- Modify: `src/runtime_v2/lifecycle/tests/test_unfilled_watcher.py` — add worker tests

**Interfaces:**
- Consumes:
  - `TradeChainRepository.get_waiting_entry_with_unfilled_cancel_config(batch_size) -> list[TradeChain]`
  - `adapter.fetch_mark_price(symbol: str, execution_account_id: str) -> float | None`
- Produces:
  - `UnfilledPriceWatcher(ops_db_path, chain_repo, adapter, execution_account_id, interval_seconds)`
  - `UnfilledPriceWatcher.run_once() -> int` — processes one tick, returns count of chains cancelled
  - `UnfilledPriceWatcher.run() -> None` — async loop

- [ ] **Step 1: Write failing tests for the worker**

Append to `src/runtime_v2/lifecycle/tests/test_unfilled_watcher.py`:

```python
from unittest.mock import MagicMock
from src.runtime_v2.lifecycle.unfilled_price_watcher import UnfilledPriceWatcher, resolve_tp_threshold


# ── Pure function: resolve_tp_threshold ──────────────────────────────────────

def test_resolve_threshold_tp1_from_intermediate():
    plan = {"intermediate_tps": [110.0, 120.0], "final_tp": 130.0}
    assert resolve_tp_threshold(plan, "tp1") == 110.0


def test_resolve_threshold_tp2_from_intermediate():
    plan = {"intermediate_tps": [110.0, 120.0], "final_tp": 130.0}
    assert resolve_tp_threshold(plan, "tp2") == 120.0


def test_resolve_threshold_tp1_fallback_to_final():
    plan = {"intermediate_tps": [], "final_tp": 115.0}
    assert resolve_tp_threshold(plan, "tp1") == 115.0


def test_resolve_threshold_tp2_fallback_to_final_when_only_one_intermediate():
    plan = {"intermediate_tps": [110.0], "final_tp": 130.0}
    assert resolve_tp_threshold(plan, "tp2") == 130.0


def test_resolve_threshold_returns_none_when_no_tps():
    plan = {"intermediate_tps": [], "final_tp": None}
    assert resolve_tp_threshold(plan, "tp1") is None


# ── Worker: run_once ──────────────────────────────────────────────────────────

def _make_chain_mock(
    chain_id=1,
    symbol="BTC/USDT",
    side="LONG",
    account_id="acc1",
    cancel_after="tp1",
    cancel_by_engine=True,
    intermediate_tps=None,
    final_tp=120.0,
    legs=None,
):
    chain = MagicMock()
    chain.trade_chain_id = chain_id
    chain.symbol = symbol
    chain.side = side
    chain.account_id = account_id
    mp = {
        "cancel_unfilled_pending_after": cancel_after,
        "cancel_pending_by_engine": cancel_by_engine,
    }
    plan = {
        "intermediate_tps": intermediate_tps or [],
        "final_tp": final_tp,
        "legs": legs or [{"sequence": 1, "status": "PENDING", "price": 100.0,
                          "client_order_id": "place_entry_attached:1:leg1"}],
    }
    chain.management_plan_json = json.dumps(mp)
    chain.plan_state_json = json.dumps(plan)
    return chain


def _make_worker(tmp_path, chains, mark_price):
    db_path = str(tmp_path / "ops.db")
    conn = _make_ops_db(db_path)
    # Insert the chain rows into DB so the worker can write events
    for ch in chains:
        conn.execute(
            """INSERT INTO ops_trade_chains
               (trade_chain_id, symbol, side, lifecycle_state,
                management_plan_json, plan_state_json, risk_snapshot_json,
                execution_mode, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (ch.trade_chain_id, ch.symbol, ch.side, "WAITING_ENTRY",
             ch.management_plan_json, ch.plan_state_json, "{}",
             "D_POSITION_TPSL",
             "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
        )
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ops_lifecycle_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_chain_id INTEGER, event_type TEXT, source_type TEXT,
            previous_state TEXT, next_state TEXT, payload_json TEXT,
            idempotency_key TEXT UNIQUE, created_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ops_execution_commands (
            command_id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_chain_id INTEGER, command_type TEXT, status TEXT,
            payload_json TEXT, idempotency_key TEXT UNIQUE,
            created_at TEXT, updated_at TEXT
        )
    """)
    conn.commit()
    conn.close()

    repo = MagicMock()
    repo.get_waiting_entry_with_unfilled_cancel_config.return_value = chains

    adapter = MagicMock()
    adapter.fetch_mark_price.return_value = mark_price

    worker = UnfilledPriceWatcher(
        ops_db_path=db_path,
        chain_repo=repo,
        adapter=adapter,
        execution_account_id="acc1",
        interval_seconds=60,
    )
    return worker, db_path


def test_run_once_cancels_long_chain_when_price_above_tp(tmp_path):
    chain = _make_chain_mock(side="LONG", final_tp=120.0)
    worker, db_path = _make_worker(tmp_path, [chain], mark_price=125.0)

    count = worker.run_once()

    assert count == 1
    conn = sqlite3.connect(db_path)
    events = conn.execute(
        "SELECT event_type FROM ops_lifecycle_events WHERE trade_chain_id=1"
    ).fetchall()
    states = conn.execute(
        "SELECT lifecycle_state FROM ops_trade_chains WHERE trade_chain_id=1"
    ).fetchone()
    conn.close()
    assert any(e[0] == "UNFILLED_TP_CANCEL" for e in events)
    assert states[0] == "EXPIRED"


def test_run_once_does_not_cancel_long_chain_when_price_below_tp(tmp_path):
    chain = _make_chain_mock(side="LONG", final_tp=120.0)
    worker, db_path = _make_worker(tmp_path, [chain], mark_price=115.0)

    count = worker.run_once()

    assert count == 0
    conn = sqlite3.connect(db_path)
    state = conn.execute(
        "SELECT lifecycle_state FROM ops_trade_chains WHERE trade_chain_id=1"
    ).fetchone()[0]
    conn.close()
    assert state == "WAITING_ENTRY"


def test_run_once_cancels_short_chain_when_price_below_tp(tmp_path):
    chain = _make_chain_mock(side="SHORT", final_tp=80.0)
    worker, db_path = _make_worker(tmp_path, [chain], mark_price=75.0)

    count = worker.run_once()
    assert count == 1


def test_run_once_skips_when_cancel_pending_by_engine_false(tmp_path):
    chain = _make_chain_mock(final_tp=120.0, cancel_by_engine=False)
    worker, db_path = _make_worker(tmp_path, [chain], mark_price=125.0)

    count = worker.run_once()
    assert count == 0


def test_run_once_skips_when_threshold_is_none(tmp_path):
    chain = _make_chain_mock(final_tp=None)
    worker, db_path = _make_worker(tmp_path, [chain], mark_price=125.0)

    count = worker.run_once()
    assert count == 0


def test_run_once_idempotent_second_tick(tmp_path):
    chain = _make_chain_mock(side="LONG", final_tp=120.0)
    worker, db_path = _make_worker(tmp_path, [chain], mark_price=125.0)

    worker.run_once()
    # Second tick with same chain still in repo mock
    count2 = worker.run_once()
    # Idempotency key deduplicates — no new events
    conn = sqlite3.connect(db_path)
    event_count = conn.execute(
        "SELECT COUNT(*) FROM ops_lifecycle_events WHERE event_type='UNFILLED_TP_CANCEL'"
    ).fetchone()[0]
    conn.close()
    assert event_count == 1  # still exactly 1, not 2
```

- [ ] **Step 2: Run tests — expect FAIL**

```
python -m pytest src/runtime_v2/lifecycle/tests/test_unfilled_watcher.py -v -k "resolve_threshold or run_once"
```

Expected: FAIL — `ModuleNotFoundError: No module named 'src.runtime_v2.lifecycle.unfilled_price_watcher'`.

- [ ] **Step 3: Create `unfilled_price_watcher.py`**

Create `src/runtime_v2/lifecycle/unfilled_price_watcher.py`:

```python
# src/runtime_v2/lifecycle/unfilled_price_watcher.py
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_DEFAULT_INTERVAL = 60


def resolve_tp_threshold(plan: dict, tp_level: str) -> float | None:
    """Return the price threshold for the given tp_level from a plan dict.

    tp_level: "tp1" or "tp2".
    Returns None when no price is resolvable (no tps in plan).
    """
    intermediate: list = plan.get("intermediate_tps") or []
    final_tp = plan.get("final_tp")

    if tp_level == "tp1":
        return float(intermediate[0]) if intermediate else (float(final_tp) if final_tp is not None else None)
    if tp_level == "tp2":
        if len(intermediate) >= 2:
            return float(intermediate[1])
        return float(final_tp) if final_tp is not None else None
    return None


def _is_triggered(mark_price: float, threshold: float, side: str) -> bool:
    if side == "LONG":
        return mark_price >= threshold
    return mark_price <= threshold   # SHORT


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class UnfilledPriceWatcher:
    """Periodic worker: cancel chains whose price has crossed TP without any fill."""

    def __init__(
        self,
        *,
        ops_db_path: str,
        chain_repo,
        adapter,
        execution_account_id: str,
        interval_seconds: int = _DEFAULT_INTERVAL,
    ) -> None:
        self._ops_db = ops_db_path
        self._chain_repo = chain_repo
        self._adapter = adapter
        self._execution_account_id = execution_account_id
        self._interval = interval_seconds

    async def run(self) -> None:
        self.run_once()
        while True:
            await asyncio.sleep(self._interval)
            self.run_once()

    def run_once(self) -> int:
        chains = self._chain_repo.get_waiting_entry_with_unfilled_cancel_config()
        if not chains:
            return 0

        # Group by symbol — 1 fetch_mark_price per symbol
        symbol_to_price: dict[str, float | None] = {}
        for chain in chains:
            if chain.symbol not in symbol_to_price:
                try:
                    price = self._adapter.fetch_mark_price(
                        chain.symbol, self._execution_account_id
                    )
                    symbol_to_price[chain.symbol] = price
                except Exception as exc:
                    logger.warning("fetch_mark_price failed for %s: %s", chain.symbol, exc)
                    symbol_to_price[chain.symbol] = None

        cancelled = 0
        for chain in chains:
            try:
                if self._process_chain(chain, symbol_to_price.get(chain.symbol)):
                    cancelled += 1
            except Exception:
                logger.exception("unfilled_price_watcher error for chain %s", chain.trade_chain_id)
        return cancelled

    def _process_chain(self, chain, mark_price: float | None) -> bool:
        chain_id = chain.trade_chain_id

        try:
            mp = json.loads(chain.management_plan_json or "{}")
        except Exception:
            return False

        if not mp.get("cancel_pending_by_engine", True):
            return False

        tp_level: str | None = mp.get("cancel_unfilled_pending_after")
        if not tp_level:
            return False

        if mark_price is None:
            logger.debug("no mark price for chain %s symbol %s — skip", chain_id, chain.symbol)
            return False

        try:
            plan = json.loads(chain.plan_state_json or "{}")
        except Exception:
            return False

        threshold = resolve_tp_threshold(plan, tp_level)
        if threshold is None:
            logger.warning("chain %s has no resolvable TP threshold — skip", chain_id)
            return False

        if not _is_triggered(mark_price, threshold, chain.side):
            return False

        self._emit_cancel(chain_id, chain, tp_level, threshold, mark_price)
        return True

    def _emit_cancel(
        self,
        chain_id: int,
        chain,
        tp_level: str,
        threshold: float,
        mark_price: float,
    ) -> None:
        now = _now()
        idem_event = f"unfilled_tp_cancel:{chain_id}"
        payload = json.dumps({
            "tp_level": tp_level,
            "threshold_price": threshold,
            "mark_price": mark_price,
            "cancel_reason": "unfilled_tp_reached",
            "symbol": chain.symbol,
            "side": chain.side,
        })

        conn = sqlite3.connect(self._ops_db)
        try:
            with conn:
                conn.execute(
                    "UPDATE ops_trade_chains SET lifecycle_state='EXPIRED', updated_at=? "
                    "WHERE trade_chain_id=?",
                    (now, chain_id),
                )
                conn.execute(
                    """
                    INSERT OR IGNORE INTO ops_lifecycle_events (
                        trade_chain_id, event_type, source_type,
                        previous_state, next_state, payload_json, idempotency_key, created_at
                    ) VALUES (?,?,?,?,?,?,?,?)
                    """,
                    (chain_id, "UNFILLED_TP_CANCEL", "unfilled_price_watcher",
                     "WAITING_ENTRY", "EXPIRED", payload, idem_event, now),
                )
                # CANCEL_PENDING_ENTRY — fan-out to real orders (same as timeout worker)
                from src.runtime_v2.lifecycle.cancel_expander import load_pending_entry_client_order_ids
                entry_coids = load_pending_entry_client_order_ids(conn, chain_id)
                if not entry_coids:
                    entry_coids = [""]
                for coid in entry_coids:
                    cmd_payload = {
                        "symbol": chain.symbol,
                        "side": chain.side,
                        "cancel_origin": "unfilled_price_watcher",
                        "cancel_reason": "unfilled_tp_reached",
                    }
                    idem_cmd = f"cancel_unfilled_tp:{chain_id}"
                    if coid:
                        cmd_payload["entry_client_order_id"] = coid
                        idem_cmd = f"{idem_cmd}:{coid}"
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO ops_execution_commands (
                            trade_chain_id, command_type, status, payload_json,
                            idempotency_key, created_at, updated_at
                        ) VALUES (?,?,?,?,?,?,?)
                        """,
                        (chain_id, "CANCEL_PENDING_ENTRY", "PENDING",
                         json.dumps(cmd_payload), idem_cmd, now, now),
                    )
        finally:
            conn.close()

        logger.info(
            "unfilled_tp_cancel: chain=%s symbol=%s side=%s tp_level=%s "
            "threshold=%.4f mark=%.4f",
            chain_id, chain.symbol, chain.side, tp_level, threshold, mark_price,
        )


__all__ = ["UnfilledPriceWatcher", "resolve_tp_threshold"]
```

- [ ] **Step 4: Run all worker tests — expect PASS**

```
python -m pytest src/runtime_v2/lifecycle/tests/test_unfilled_watcher.py -v
```

Expected: all tests PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/lifecycle/unfilled_price_watcher.py \
        src/runtime_v2/lifecycle/tests/test_unfilled_watcher.py
git commit -m "feat: implement UnfilledPriceWatcher worker with resolve_tp_threshold"
```

---

### Task 4: Wire into main.py and update config

**Files:**
- Modify: `main.py` — import + instantiate + asyncio task
- Modify: `config/operation_config.yaml` — add interval field, fix comment
- Modify: `src/runtime_v2/signal_enrichment/config_loader.py` — add `get_unfilled_price_check_interval()` helper

**Interfaces:**
- Consumes: `UnfilledPriceWatcher`, `ExecutionRuntime.adapter`, `ExecutionRuntime.adapter_contexts`

- [ ] **Step 1: Add `get_unfilled_price_check_interval` to `OperationConfigLoader`**

In `src/runtime_v2/signal_enrichment/config_loader.py`, add after `get_symbol_blacklist_for_trader`:

```python
def get_unfilled_price_check_interval(self) -> int:
    """Return unfilled_price_check_interval_seconds from global_safety, default 60."""
    return int(
        self._global_raw.get("global_safety", {})
        .get("unfilled_price_check_interval_seconds", 60)
    )
```

- [ ] **Step 2: Update `config/operation_config.yaml`**

Add `unfilled_price_check_interval_seconds` to `global_safety` block and fix the `//da implimentare` comment:

Change:
```yaml
global_safety:
  allow_unprotected_positions: false  # false = richiede protezione SL; true = ammette posizioni senza SL.
```

To:
```yaml
global_safety:
  allow_unprotected_positions: false  # false = richiede protezione SL; true = ammette posizioni senza SL.
  unfilled_price_check_interval_seconds: 60  # Intervallo worker cancel_unfilled_pending_after in secondi.
```

And change:
```yaml
    cancel_unfilled_pending_after: null  # Valori supportati: null | tp1 | tp2. //da implimentare
```
To:
```yaml
    cancel_unfilled_pending_after: null  # Valori supportati: null | tp1 | tp2.
```

- [ ] **Step 3: Wire worker into `main.py`**

Add import at the top with other lifecycle imports (around line 44):
```python
from src.runtime_v2.lifecycle.unfilled_price_watcher import UnfilledPriceWatcher
```

After the `AccountSnapshotWorker` instantiation block (around line 637), add:

```python
    # Unfilled price watcher — cancel setups when price crosses TP without fill
    _unfilled_price_watcher: UnfilledPriceWatcher | None = None
    if execution_runtime is not None and _account_ids:
        _primary_account_id = _account_ids[0]
        _primary_adapter = (execution_runtime.adapters or {}).get(
            list(execution_runtime.adapters.keys())[0]
            if execution_runtime.adapters else "default",
            execution_runtime.adapter,
        )
        _op_config_loader = OperationConfigLoader(config_dir)
        _unfilled_interval = _op_config_loader.get_unfilled_price_check_interval()
        _unfilled_price_watcher = UnfilledPriceWatcher(
            ops_db_path=ops_db_path,
            chain_repo=chain_repo,
            adapter=_primary_adapter,
            execution_account_id=_primary_account_id,
            interval_seconds=_unfilled_interval,
        )
```

Then in the task creation block (where `account_snapshot_task` is created, around line 718), add:

```python
        unfilled_watcher_task = None
        if _unfilled_price_watcher is not None:
            unfilled_watcher_task = asyncio.create_task(_unfilled_price_watcher.run())
            logger.info(
                "unfilled price watcher started | interval=%ds | account=%s",
                _unfilled_interval, _primary_account_id,
            )
```

- [ ] **Step 4: Smoke-check imports**

```
python -c "from src.runtime_v2.lifecycle.unfilled_price_watcher import UnfilledPriceWatcher; print('OK')"
```

Expected: `OK`

- [ ] **Step 5: Run full test suite**

```
python -m pytest src/runtime_v2/lifecycle/tests/test_unfilled_watcher.py -v
```

Expected: all PASSED.

- [ ] **Step 6: Commit**

```bash
git add main.py \
        config/operation_config.yaml \
        src/runtime_v2/signal_enrichment/config_loader.py
git commit -m "feat: wire UnfilledPriceWatcher into main loop and config"
```

---

### Task 5: Update docs

**Files:**
- Modify: `docs/debugging/stato_runtime_v2.md:86-105`

- [ ] **Step 1: Update `stato_runtime_v2.md`**

Find the two references to `cancel_unfilled_pending_after` and update them.

Change the table row (around line 90):
```markdown
| `cancel_unfilled_pending_after` | Cancella entry non fillata se prezzo ha raggiunto livello TP | **Bloccato**: richiede price-watcher non presente nell'architettura. Lasciato nel modello come placeholder. |
```
To:
```markdown
| `cancel_unfilled_pending_after` | Cancella entry non fillata se prezzo ha raggiunto livello TP | Implementato: `UnfilledPriceWatcher` in `src/runtime_v2/lifecycle/unfilled_price_watcher.py`. Worker periodico (default 60s). Evento: `UNFILLED_TP_CANCEL` → outbox `ENTRY_CANCELLED_TP_REACHED`. |
```

Change the TODO list entry (around line 101):
```markdown
- [ ] `cancel_unfilled_pending_after` — **BLOCCATO**: richiede price-watcher
```
To:
```markdown
- [x] `cancel_unfilled_pending_after` — implementato in `UnfilledPriceWatcher` (`unfilled_price_watcher.py`)
```

- [ ] **Step 2: Commit**

```bash
git add docs/debugging/stato_runtime_v2.md
git commit -m "docs: mark cancel_unfilled_pending_after as implemented"
```
