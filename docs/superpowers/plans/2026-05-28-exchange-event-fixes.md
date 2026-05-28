# Exchange Event Processing Fixes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix three bugs in exchange event processing: TP fills not attributed to trade chains, position reconciliation losing fill price, and pending entry orders not cancelled when a chain closes via reconciliation.

**Architecture:** Three independent surgical fixes across the event ingest layer (WS watcher + repository), the reconciliation worker, and the lifecycle event processor. Each fix is isolated to one or two files and verified by a single new test.

**Tech Stack:** Python 3.12, SQLite (sqlite3), pytest, dataclasses.replace, existing FakeAdapter

---

## File Map

| File | Change |
|------|--------|
| `src/runtime_v2/execution_gateway/repositories.py` | Add `resolve_chain_for_fill(symbol, side)` method |
| `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/ws_fill_watcher.py` | Add post-classification enrichment for TP/SL fills |
| `src/runtime_v2/execution_gateway/event_sync.py` | Fetch fill price via REST before writing CLOSE_FULL_FILLED |
| `src/runtime_v2/lifecycle/event_processor.py` | Emit CANCEL_PENDING_ENTRY in `_process_close_full_filled` |
| `tests/runtime_v2/execution_gateway/test_repository_extensions.py` | New test: `resolve_chain_for_fill` |
| `tests/runtime_v2/execution_gateway/test_ws_fill_watcher.py` | New test: TP fill enrichment |
| `tests/runtime_v2/execution_gateway/test_event_sync.py` | New test: fill_price from REST |
| `tests/runtime_v2/lifecycle/test_event_processor.py` | New test: CANCEL_PENDING_ENTRY on close |

---

## Task 1 — `resolve_chain_for_fill` in GatewayCommandRepository

**Files:**
- Modify: `src/runtime_v2/execution_gateway/repositories.py` (append after `get_open_chains_for_symbol`)
- Modify: `tests/runtime_v2/execution_gateway/test_repository_extensions.py` (append test)

### Background

`get_open_chains_for_symbol(symbol, side)` already exists and returns `list[int]`.
`resolve_chain_for_fill` wraps it: returns the single chain_id if exactly one open chain exists, otherwise `None` (avoids ambiguous attribution when multiple chains run on the same symbol).

`side` here is the **position side** (LONG/SHORT), not the fill order side (Buy/Sell). The caller is responsible for mapping fill side → position side.

- [ ] **Step 1: Write the failing test**

Append to `tests/runtime_v2/execution_gateway/test_repository_extensions.py`:

```python
# ── resolve_chain_for_fill ────────────────────────────────────────────────────

def test_resolve_chain_for_fill_returns_chain_id_when_exactly_one(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE ops_trade_chains (
            trade_chain_id INTEGER PRIMARY KEY,
            symbol TEXT,
            side TEXT,
            lifecycle_state TEXT
        );
    """)
    conn.execute(
        "INSERT INTO ops_trade_chains VALUES (7, 'BTCUSDT', 'LONG', 'OPEN')"
    )
    conn.commit()
    conn.close()

    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository
    repo = GatewayCommandRepository(db_path)
    assert repo.resolve_chain_for_fill("BTCUSDT", "LONG") == 7


def test_resolve_chain_for_fill_returns_none_when_no_open_chain(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE ops_trade_chains (
            trade_chain_id INTEGER PRIMARY KEY,
            symbol TEXT,
            side TEXT,
            lifecycle_state TEXT
        );
    """)
    conn.execute(
        "INSERT INTO ops_trade_chains VALUES (7, 'BTCUSDT', 'LONG', 'CLOSED')"
    )
    conn.commit()
    conn.close()

    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository
    repo = GatewayCommandRepository(db_path)
    assert repo.resolve_chain_for_fill("BTCUSDT", "LONG") is None


def test_resolve_chain_for_fill_returns_none_when_multiple_open_chains(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE ops_trade_chains (
            trade_chain_id INTEGER PRIMARY KEY,
            symbol TEXT,
            side TEXT,
            lifecycle_state TEXT
        );
    """)
    conn.executemany(
        "INSERT INTO ops_trade_chains VALUES (?,?,?,?)",
        [(7, "BTCUSDT", "LONG", "OPEN"), (8, "BTCUSDT", "LONG", "OPEN")],
    )
    conn.commit()
    conn.close()

    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository
    repo = GatewayCommandRepository(db_path)
    assert repo.resolve_chain_for_fill("BTCUSDT", "LONG") is None
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/runtime_v2/execution_gateway/test_repository_extensions.py -k "resolve_chain_for_fill" -v
```

Expected: `AttributeError: 'GatewayCommandRepository' object has no attribute 'resolve_chain_for_fill'`

- [ ] **Step 3: Add method to `repositories.py`**

In `src/runtime_v2/execution_gateway/repositories.py`, append this method to the `GatewayCommandRepository` class, right after `get_open_chains_for_symbol`:

```python
def resolve_chain_for_fill(self, symbol: str, side: str) -> int | None:
    """Return the unique open chain_id for symbol+side, or None if 0 or >1.

    Used to attribute TP/SL fills that lack an orderLinkId (Bybit position-level
    orders never carry orderLinkId). Returns None when attribution is ambiguous
    (multiple open chains on the same symbol) to avoid mis-routing.

    `side` must be the position side: 'LONG' or 'SHORT'.
    """
    chains = self.get_open_chains_for_symbol(symbol, side)
    return chains[0] if len(chains) == 1 else None
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/runtime_v2/execution_gateway/test_repository_extensions.py -k "resolve_chain_for_fill" -v
```

Expected: 3 PASSED

- [ ] **Step 5: Commit**

```
git add src/runtime_v2/execution_gateway/repositories.py tests/runtime_v2/execution_gateway/test_repository_extensions.py
git commit -m "feat(repositories): add resolve_chain_for_fill for TP/SL attribution without orderLinkId"
```

---

## Task 2 — TP/SL fill enrichment in `_process_batch`

**Files:**
- Modify: `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/ws_fill_watcher.py`
- Modify: `tests/runtime_v2/execution_gateway/test_ws_fill_watcher.py`

### Background

After `EventClassifier.classify()` returns a `ClassifiedEvent`, if `event_type` is `"TP_FILLED"` or `"SL_FILLED"` and `trade_chain_id is None`, the event cannot be forwarded to lifecycle (`should_forward_to_lifecycle` returns False). This happens because Bybit never populates `orderLinkId` on position-level TP/SL orders.

Fix: after classification, check if TP/SL fill lacks chain attribution → call `repo.resolve_chain_for_fill(symbol, position_side)` → if found, replace the classified event's `trade_chain_id`.

Position side mapping: Bybit fill side "Sell" reduces a LONG → position side is "LONG"; "Buy" reduces a SHORT → position side is "SHORT".

`ClassifiedEvent` is a `@dataclass`, so use `dataclasses.replace()`.

- [ ] **Step 1: Write the failing test**

Append to `tests/runtime_v2/execution_gateway/test_ws_fill_watcher.py`:

```python
import dataclasses


def test_process_batch_enriches_tp_fill_with_chain_id_when_no_link_id():
    """TP_FILLED with trade_chain_id=None gets enriched via resolve_chain_for_fill."""
    from src.runtime_v2.execution_gateway.event_ingest.models import (
        ClassifiedEvent, ExchangeRawEvent,
    )

    mock_repo = MagicMock()
    mock_repo.get_known_order_link_ids.return_value = {}
    mock_repo.insert_raw_and_classified.return_value = True
    mock_repo.resolve_chain_for_fill.return_value = 42  # one open chain found

    watcher = BybitWsFillWatcher(
        api_key="k", api_secret="s", testnet=False,
        ops_db_path=":memory:",
        repo=mock_repo,
        normalizer=MagicMock(),
        classifier=MagicMock(),
    )

    # Raw event: TP fill, Sell side (closing a LONG), no orderLinkId
    mock_raw = MagicMock(spec=ExchangeRawEvent)
    mock_raw.side = "Sell"
    mock_raw.symbol = "BTCUSDT"
    mock_raw.order_link_id = ""

    # Classifier returns TP_FILLED with no chain attribution
    unlinked = ClassifiedEvent(
        raw=mock_raw,
        event_type="TP_FILLED",
        source="exchange_auto",
        trade_chain_id=None,
        tp_level=None,
        is_actionable=True,
    )

    normalize_fn = MagicMock(return_value=mock_raw)

    with patch(
        "src.runtime_v2.execution_gateway.adapters.ccxt_bybit.ws_fill_watcher.EventClassifier"
    ) as MockClassifier:
        MockClassifier.return_value.classify.return_value = unlinked
        watcher._process_batch([{"id": "tp-trade-1"}], normalize_fn)

    # resolve_chain_for_fill called with BTCUSDT and LONG (Sell fill closes LONG)
    mock_repo.resolve_chain_for_fill.assert_called_once_with("BTCUSDT", "LONG")

    # insert_raw_and_classified received enriched event with trade_chain_id=42
    inserted_event = mock_repo.insert_raw_and_classified.call_args[0][0]
    assert inserted_event.trade_chain_id == 42
    assert inserted_event.event_type == "TP_FILLED"


def test_process_batch_does_not_enrich_tp_fill_when_multiple_chains(no_side_effect=None):
    """TP_FILLED stays unlinked when resolve_chain_for_fill returns None (ambiguous)."""
    from src.runtime_v2.execution_gateway.event_ingest.models import (
        ClassifiedEvent, ExchangeRawEvent,
    )

    mock_repo = MagicMock()
    mock_repo.get_known_order_link_ids.return_value = {}
    mock_repo.insert_raw_and_classified.return_value = True
    mock_repo.resolve_chain_for_fill.return_value = None  # ambiguous

    watcher = BybitWsFillWatcher(
        api_key="k", api_secret="s", testnet=False,
        ops_db_path=":memory:",
        repo=mock_repo,
        normalizer=MagicMock(),
        classifier=MagicMock(),
    )

    mock_raw = MagicMock(spec=ExchangeRawEvent)
    mock_raw.side = "Sell"
    mock_raw.symbol = "BTCUSDT"
    mock_raw.order_link_id = ""

    unlinked = ClassifiedEvent(
        raw=mock_raw,
        event_type="TP_FILLED",
        source="exchange_auto",
        trade_chain_id=None,
        tp_level=None,
        is_actionable=True,
    )

    normalize_fn = MagicMock(return_value=mock_raw)

    with patch(
        "src.runtime_v2.execution_gateway.adapters.ccxt_bybit.ws_fill_watcher.EventClassifier"
    ) as MockClassifier:
        MockClassifier.return_value.classify.return_value = unlinked
        watcher._process_batch([{"id": "tp-trade-2"}], normalize_fn)

    inserted_event = mock_repo.insert_raw_and_classified.call_args[0][0]
    assert inserted_event.trade_chain_id is None  # still unlinked
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/runtime_v2/execution_gateway/test_ws_fill_watcher.py -k "enrich" -v
```

Expected: `AssertionError` — `resolve_chain_for_fill` not called, `trade_chain_id` still None.

- [ ] **Step 3: Implement enrichment in `ws_fill_watcher.py`**

Add `import dataclasses` at the top of the file (after existing imports):

```python
import dataclasses
```

Replace the `_process_batch` method body in `ws_fill_watcher.py` with:

```python
def _process_batch(
    self,
    items: list[dict] | None,
    normalize_fn,  # callable: dict → ExchangeRawEvent | None
) -> None:
    """Generic batch processor: normalize → classify → enrich → persist."""
    if not items:
        return
    # Refresh known_order_link_ids once per batch for efficiency
    known = self._repo.get_known_order_link_ids()
    # Update classifier's known map (re-create classifier with fresh data)
    classifier = EventClassifier(known_order_link_ids=known)
    for item in items:
        try:
            raw = normalize_fn(item)
            if raw is None:
                continue
            classified = classifier.classify(raw)

            # Post-classification enrichment: attribute TP/SL fills that Bybit
            # does not tag with orderLinkId (position-level attached orders).
            # "Sell" fill closes a LONG; "Buy" fill closes a SHORT.
            if (
                classified.event_type in ("TP_FILLED", "SL_FILLED")
                and classified.trade_chain_id is None
            ):
                fill_side = (raw.side or "").strip()
                position_side = "LONG" if fill_side.lower() == "sell" else "SHORT"
                chain_id = self._repo.resolve_chain_for_fill(raw.symbol, position_side)
                if chain_id is not None:
                    classified = dataclasses.replace(classified, trade_chain_id=chain_id)

            inserted = self._repo.insert_raw_and_classified(classified)
            if inserted and classified.should_forward_to_lifecycle and self._wake_callback:
                self._wake_callback()
        except Exception:
            item_id = item.get("id", repr(item)) if isinstance(item, dict) else repr(item)
            logger.exception("error processing item %s", item_id)
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/runtime_v2/execution_gateway/test_ws_fill_watcher.py -v
```

Expected: all PASSED (including pre-existing tests)

- [ ] **Step 5: Commit**

```
git add src/runtime_v2/execution_gateway/adapters/ccxt_bybit/ws_fill_watcher.py tests/runtime_v2/execution_gateway/test_ws_fill_watcher.py
git commit -m "feat(ws_fill_watcher): enrich TP/SL fills with chain_id via symbol+side lookup when orderLinkId absent"
```

---

## Task 3 — Fill price from REST in `run_position_reconciliation`

**Files:**
- Modify: `src/runtime_v2/execution_gateway/event_sync.py`
- Modify: `tests/runtime_v2/execution_gateway/test_event_sync.py`

### Background

`run_position_reconciliation` detects when a position went to 0 while the chain still shows `open_position_qty > 0`. It inserts a `CLOSE_FULL_FILLED` event. Currently `fill_price` is always `None`.

Fix: before inserting, call `adapter.fetch_recent_reduce_trades()` (if available) and compute a weighted-average fill price across all returned trades. Wrapped in try/except — if it fails, fall back to `None` (current behaviour, no regression).

Weighted average: `sum(t.price * t.amount for t in trades) / sum(t.amount for t in trades)`.

- [ ] **Step 1: Write the failing test**

Append to `tests/runtime_v2/execution_gateway/test_event_sync.py`:

```python
def _insert_open_chain(db_path, chain_id, symbol, side, open_qty):
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_trade_chains "
        "(trade_chain_id, source_enrichment_id, canonical_message_id, raw_message_id, "
        " trader_id, account_id, symbol, side, lifecycle_state, entry_mode, "
        " management_plan_json, risk_snapshot_json, plan_state_json, "
        " open_position_qty, filled_entry_qty, closed_position_qty, "
        " be_protection_status, execution_mode, "
        " risk_already_realized, risk_remaining, created_at, updated_at) "
        "VALUES (?,1,1,1,'t','acc',?,?,'OPEN','ONE_SHOT','{}','{}','{}',?,?,0.0,'NOT_PROTECTED','UNIFIED_PLAN',0.0,0.0,?,?)",
        (chain_id, symbol, side, open_qty, open_qty, now, now),
    )
    conn.commit()
    conn.close()


def test_position_reconciliation_records_fill_price_from_rest(ops_db):
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_open_chain(ops_db, chain_id=99, symbol="BTCUSDT", side="LONG", open_qty=0.131)

    adapter = FakeAdapter()
    # Simulate position already closed on exchange
    adapter.set_position_qty("BTCUSDT", "LONG", 0.0)
    # Register the reduce trade the REST call will return
    adapter.simulate_reduce_trade("BTCUSDT", "LONG", price=73345.8, amount=0.131, trade_id="t1")

    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(
        ops_db_path=ops_db,
        adapter=adapter,
        repo=repo,
        execution_account_id="acc",
    )
    n = worker.run_position_reconciliation()
    assert n == 1

    conn = sqlite3.connect(ops_db)
    row = conn.execute(
        "SELECT payload_json FROM ops_exchange_events "
        "WHERE trade_chain_id=99 AND event_type='CLOSE_FULL_FILLED'"
    ).fetchone()
    conn.close()

    assert row is not None
    payload = json.loads(row[0])
    assert payload["fill_price"] == pytest.approx(73345.8)
    assert payload["source"] == "position_reconciliation"


def test_position_reconciliation_falls_back_to_none_when_no_reduce_trades(ops_db):
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_open_chain(ops_db, chain_id=100, symbol="ETHUSDT", side="LONG", open_qty=1.0)

    adapter = FakeAdapter()
    adapter.set_position_qty("ETHUSDT", "LONG", 0.0)
    # No reduce trades registered → fill_price should stay None

    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(
        ops_db_path=ops_db,
        adapter=adapter,
        repo=repo,
        execution_account_id="acc",
    )
    n = worker.run_position_reconciliation()
    assert n == 1

    conn = sqlite3.connect(ops_db)
    row = conn.execute(
        "SELECT payload_json FROM ops_exchange_events "
        "WHERE trade_chain_id=100 AND event_type='CLOSE_FULL_FILLED'"
    ).fetchone()
    conn.close()

    payload = json.loads(row[0])
    assert payload["fill_price"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/runtime_v2/execution_gateway/test_event_sync.py -k "position_reconciliation_records_fill_price or falls_back_to_none" -v
```

Expected: `AssertionError` — `fill_price` is `None` in both cases.

- [ ] **Step 3: Check what `FakeAdapter.set_position_qty` looks like**

Look at `src/runtime_v2/execution_gateway/adapters/fake.py` to confirm `set_position_qty` and `get_position_qty` exist. If `set_position_qty` is not there, use `adapter._position_qtys["BTCUSDT:LONG"] = 0.0` directly (check the internal dict name in the FakeAdapter).

Actually, open `src/runtime_v2/execution_gateway/adapters/fake.py` and search for `get_position_qty`. Use whatever setup method the FakeAdapter exposes.

- [ ] **Step 4: Implement fill price fetch in `event_sync.py`**

Add a private helper at module level (after the logger line, before the class):

```python
def _weighted_avg_price(trades: list) -> float | None:
    """Weighted average price across a list of RawAdapterTrade objects."""
    total_qty = sum(t.amount for t in trades)
    if total_qty <= 0:
        return None
    return sum(t.price * t.amount for t in trades) / total_qty
```

Replace the body of `run_position_reconciliation` in `event_sync.py` with:

```python
def run_position_reconciliation(self) -> int:
    """Detect positions closed externally on the exchange (manual close or missed TP/SL)."""
    chains = self._get_open_chains()
    processed = 0
    for chain_id, symbol, side, open_qty in chains:
        try:
            qty = self._adapter.get_position_qty(
                symbol=symbol,
                side=side,
                execution_account_id=self._execution_account_id,
            )
            if qty is None:
                continue
            if qty == 0.0 and open_qty > 0.0:
                # Attempt to recover fill price from recent reduce trades (REST safety net)
                fill_price: float | None = None
                if hasattr(self._adapter, "fetch_recent_reduce_trades"):
                    try:
                        trades = self._adapter.fetch_recent_reduce_trades(
                            symbol=symbol,
                            side=side,
                            execution_account_id=self._execution_account_id,
                            limit=50,
                        )
                        fill_price = _weighted_avg_price(trades)
                    except Exception:
                        logger.warning(
                            "could not fetch fill price for reconciliation close: chain=%s",
                            chain_id,
                        )

                idem_key = f"CLOSE_FULL_FILLED:ext:{chain_id}"
                payload = json.dumps({
                    "filled_qty": open_qty,
                    "fill_price": fill_price,
                    "source": "position_reconciliation",
                })
                inserted = self._repo.insert_exchange_event(
                    chain_id, "CLOSE_FULL_FILLED", payload, idem_key
                )
                if inserted:
                    logger.info(
                        "externally closed position detected: chain=%s %s %s qty=%s fill_price=%s",
                        chain_id, symbol, side, open_qty, fill_price,
                    )
                    self._wake()
                    processed += 1
        except Exception:
            logger.exception("position reconciliation error for chain %s", chain_id)
    return processed
```

- [ ] **Step 5: Fix test if `set_position_qty` does not exist on FakeAdapter**

Open `src/runtime_v2/execution_gateway/adapters/fake.py` and find the method that presets the position qty (it might be `set_position_qty` or direct dict access like `adapter._position_qtys[key] = 0.0`). Update the test to use the actual API. Do NOT modify FakeAdapter itself unless `get_position_qty` is missing entirely.

- [ ] **Step 6: Run tests to verify they pass**

```
pytest tests/runtime_v2/execution_gateway/test_event_sync.py -v
```

Expected: all PASSED including pre-existing tests.

- [ ] **Step 7: Commit**

```
git add src/runtime_v2/execution_gateway/event_sync.py tests/runtime_v2/execution_gateway/test_event_sync.py
git commit -m "feat(event_sync): recover fill_price from REST reduce trades in position reconciliation"
```

---

## Task 4 — Emit `CANCEL_PENDING_ENTRY` on `CLOSE_FULL_FILLED`

**Files:**
- Modify: `src/runtime_v2/lifecycle/event_processor.py`
- Modify: `tests/runtime_v2/lifecycle/test_event_processor.py`

### Background

When a chain closes via `CLOSE_FULL_FILLED` (position reconciliation or manual close), any pending entry orders (e.g., averaging leg placed but not yet filled) remain open on the exchange. The gateway does not know to cancel them.

Fix: `_process_close_full_filled` returns a `CANCEL_PENDING_ENTRY` execution command. In `workers.py:_persist_result`, all execution commands go through `expand_cancel_pending_commands`, which:
- Looks up all `ops_execution_commands` with type `PLACE_ENTRY` / `PLACE_ENTRY_WITH_ATTACHED_TPSL` and status `PENDING/SENT/ACK` for the chain
- Expands one cancel command per real `client_order_id`
- If none found (no pending orders), inserts the base command as-is (the gateway command worker will handle it as a no-op)

So the event_processor only needs to emit the intent — the expander handles the details.

The `ExecutionCommand` model does not require `command_id` at construction (it's auto-assigned by DB).

- [ ] **Step 1: Write the failing test**

Append to `tests/runtime_v2/lifecycle/test_event_processor.py`:

```python
def test_close_full_filled_emits_cancel_pending_entry_command():
    """CLOSE_FULL_FILLED must include a CANCEL_PENDING_ENTRY command to clean up averaging legs."""
    from src.runtime_v2.lifecycle.event_processor import LifecycleEventProcessor

    processor = LifecycleEventProcessor()
    chain = _make_chain(state="OPEN", side="LONG")
    chain = chain.model_copy(update={"symbol": "BTCUSDT", "open_position_qty": 0.262})

    event = _make_exchange_event(
        event_type="CLOSE_FULL_FILLED",
        payload={"filled_qty": 0.262, "fill_price": 73345.8, "source": "position_reconciliation"},
    )

    result = processor.process(event, chain, active_commands=[])

    assert result.new_lifecycle_state == "CLOSED"
    assert result.new_open_position_qty == 0.0

    cancel_cmds = [c for c in result.execution_commands if c.command_type == "CANCEL_PENDING_ENTRY"]
    assert len(cancel_cmds) == 1
    payload = json.loads(cancel_cmds[0].payload_json)
    assert payload["symbol"] == "BTCUSDT"
    assert payload["cancel_reason"] == "position_closed"
    assert cancel_cmds[0].idempotency_key == f"cancel_on_close:{chain.trade_chain_id}"
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/runtime_v2/lifecycle/test_event_processor.py::test_close_full_filled_emits_cancel_pending_entry_command -v
```

Expected: `AssertionError` — `cancel_cmds` is empty.

- [ ] **Step 3: Modify `_process_close_full_filled` in `event_processor.py`**

Replace the entire `_process_close_full_filled` method:

```python
def _process_close_full_filled(
    self, exchange_event: ExchangeEvent, chain: TradeChain
) -> EventProcessorResult:
    payload = json.loads(exchange_event.payload_json)
    fill_qty = float(payload.get("filled_qty") or chain.open_position_qty)
    eid = exchange_event.exchange_event_id
    chain_id = chain.trade_chain_id
    return EventProcessorResult(
        new_lifecycle_state="CLOSED",
        new_be_protection_status=None,
        entry_avg_price=None,
        current_stop_price=None,
        lifecycle_events=[LifecycleEvent(
            trade_chain_id=chain_id,
            event_type="CLOSE_FULL_FILLED",
            source_type="exchange_event",
            source_id=str(eid),
            previous_state=chain.lifecycle_state,
            next_state="CLOSED",
            payload_json=exchange_event.payload_json,
            idempotency_key=f"close_full_filled:{chain_id}:{eid}",
        )],
        execution_commands=[ExecutionCommand(
            trade_chain_id=chain_id,
            command_type="CANCEL_PENDING_ENTRY",
            payload_json=json.dumps({
                "symbol": chain.symbol,
                "side": chain.side,
                "cancel_reason": "position_closed",
            }),
            idempotency_key=f"cancel_on_close:{chain_id}",
        )],
        new_open_position_qty=0.0,
        new_closed_position_qty=chain.closed_position_qty + fill_qty,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/runtime_v2/lifecycle/test_event_processor.py -v
```

Expected: all PASSED including pre-existing tests.

- [ ] **Step 5: Run full test suite to check for regressions**

```
pytest tests/runtime_v2/ -v --tb=short
```

Expected: all PASSED. If any test on `CLOSE_FULL_FILLED` now fails because it doesn't expect `execution_commands`, update that test to also assert the cancel command is present (it's now correct behaviour).

- [ ] **Step 6: Commit**

```
git add src/runtime_v2/lifecycle/event_processor.py tests/runtime_v2/lifecycle/test_event_processor.py
git commit -m "feat(event_processor): emit CANCEL_PENDING_ENTRY on CLOSE_FULL_FILLED to clean up pending averaging orders"
```

---

## Self-Review

**Spec coverage:**
- Fix 1 (resolve_chain_for_fill): ✅ Task 1
- Fix 2 (TP_FILLED attribution): ✅ Task 2
- Fix 3 (fill_price from REST): ✅ Task 3
- Fix 4 (CANCEL_PENDING_ENTRY): ✅ Task 4

**Placeholder scan:**
- Task 3 Step 3 notes to check FakeAdapter API — this is intentional; the step directs the implementor to verify rather than assume. Not a placeholder, it's a guard.

**Type consistency:**
- `resolve_chain_for_fill(symbol: str, side: str) -> int | None` — used identically in Task 1 (definition) and Task 2 (call site).
- `_weighted_avg_price(trades: list) -> float | None` — defined and used in Task 3 only.
- `ClassifiedEvent` uses `dataclasses.replace()` — confirmed `ClassifiedEvent` is a `@dataclass` in `models.py`.
- `ExecutionCommand` constructor without `command_id` — confirmed optional in existing codebase usage patterns.
