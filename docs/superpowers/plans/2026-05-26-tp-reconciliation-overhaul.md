# TP Reconciliation Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace qty-comparison TP detection with `fetch_my_trades()` REST polling, fix the symbol mismatch that makes `watchMyTrades` completely blind, and add protective-orders-missing detection via `fetch_positions()`.

**Architecture:** Three independent layers — (1) WS real-time via `watchMyTrades` (already exists, but symbol was broken), (2) REST poll via `fetch_my_trades()` as fallback that also recovers events missed during offline/WS-gap, (3) `fetch_positions()` check for TP/SL cancellations that produce no fill. The qty-comparison fallback (`run_tp_reconciliation`) is deleted: it was both wrong (formula inverted) and superseded by the trade-based approach.

**Tech Stack:** Python 3.12, Pydantic v2, ccxt, SQLite, existing `ExchangeEventSyncWorker` + `BybitWsFillWatcher` + `ExecutionAdapter` patterns.

---

## Context / Invariants

- DB stores symbols in **Bybit raw format**: `PHAUSDT`, `BTCUSDT`
- ccxt.pro WS (`watchMyTrades`) delivers trades with **ccxt unified format**: `PHA/USDT:USDT`
- ccxt REST (`fetch_my_trades`) also delivers ccxt format
- Conversion: `"PHA/USDT:USDT"` → split on `/` → `"PHA"` + `"USDT:USDT"` → take quote before `:` → `"PHAUSDT"`
- Idempotency key for TP fills: `"TP_FILLED:{chain_id}:level:{tp_level}"` — both WS and REST use this key so INSERT OR IGNORE deduplicates them
- All test files use `_apply_migrations(db_path)` with `db/ops_migrations/*.sql`
- Run tests with: `pytest tests/runtime_v2/execution_gateway/ -q`

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/runtime_v2/execution_gateway/models.py` | Modify | Add `RawAdapterTrade`, `RawPositionDetails` models |
| `src/runtime_v2/execution_gateway/adapters/fake.py` | Modify | Add `fetch_recent_reduce_trades()`, `fetch_position_details()`, `simulate_reduce_trade()` |
| `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/adapter.py` | Modify | Add `fetch_recent_reduce_trades()`, `fetch_position_details()` |
| `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/ws_fill_watcher.py` | Modify | Add `_ccxt_symbol_to_raw()`, apply in `_match_and_save_tp_fill()` |
| `src/runtime_v2/execution_gateway/event_sync.py` | Modify | Delete `run_tp_reconciliation()`, `_get_sent_tp_commands()`, `_save_tp_fill()`. Add `run_trade_based_reconciliation()`, `run_protective_orders_reconciliation()`, `_get_tp_reconciliation_entries()`, `_save_tp_fill_from_trade()` |
| `main.py` | Modify | Replace `run_tp_reconciliation()` with `run_trade_based_reconciliation()` + `run_protective_orders_reconciliation()` |
| `tests/runtime_v2/execution_gateway/test_ws_fill_watcher.py` | Modify | Add test for symbol normalization (production format: raw in DB, ccxt in trade) |
| `tests/runtime_v2/execution_gateway/test_event_sync.py` | Modify | Replace `test_tp_filled_ws_and_polling_unified_key_no_duplicate`, add trade-based and protective-orders tests |

---

## Task 1: Add `RawAdapterTrade` and `RawPositionDetails` to models

**Files:**
- Modify: `src/runtime_v2/execution_gateway/models.py`

- [ ] **Step 1: Write failing import test**

```python
# tests/runtime_v2/execution_gateway/test_event_sync.py
# Add at top of file (new standalone test)
def test_raw_adapter_trade_model():
    from src.runtime_v2.execution_gateway.models import RawAdapterTrade, RawPositionDetails
    t = RawAdapterTrade(trade_id="t1", symbol="PHAUSDT", price=0.05754, amount=3871.5)
    assert t.reduce_only is True  # default
    pos = RawPositionDetails(symbol="PHAUSDT", side="SHORT", qty=3871.5, take_profit=0.05373)
    assert pos.stop_loss is None
```

- [ ] **Step 2: Run to verify it fails**

```
pytest tests/runtime_v2/execution_gateway/test_event_sync.py::test_raw_adapter_trade_model -v
```
Expected: `ImportError: cannot import name 'RawAdapterTrade'`

- [ ] **Step 3: Add models to `models.py`**

Append before the final `__all__` in `src/runtime_v2/execution_gateway/models.py`:

```python
class RawAdapterTrade(BaseModel):
    """A single reduceOnly fill returned by fetch_recent_reduce_trades()."""
    model_config = ConfigDict(extra="ignore")
    trade_id: str
    symbol: str          # Bybit raw format: PHAUSDT
    price: float
    amount: float
    reduce_only: bool = True


class RawPositionDetails(BaseModel):
    """Position snapshot from fetch_position_details()."""
    model_config = ConfigDict(extra="ignore")
    symbol: str          # Bybit raw format
    side: str            # LONG | SHORT
    qty: float
    take_profit: float | None = None   # None = field unavailable; 0.0 = not set on exchange
    stop_loss: float | None = None
```

Also update `__all__`:
```python
__all__ = [
    "RetryConfig", "LiveSafetyConfig", "WebsocketConfig",
    "ExecutionStrategyConfig",
    "AdapterConfig", "AccountRoutingEntry", "ExecutionConfig",
    "RawAdapterOrder", "RawAdapterTrade", "RawPositionDetails", "AdapterResult",
]
```

- [ ] **Step 4: Run test to verify it passes**

```
pytest tests/runtime_v2/execution_gateway/test_event_sync.py::test_raw_adapter_trade_model -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```
git add src/runtime_v2/execution_gateway/models.py tests/runtime_v2/execution_gateway/test_event_sync.py
git commit -m "feat(models): add RawAdapterTrade and RawPositionDetails"
```

---

## Task 2: Fix symbol mismatch in `ws_fill_watcher._match_and_save_tp_fill()`

**Files:**
- Modify: `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/ws_fill_watcher.py`
- Modify: `tests/runtime_v2/execution_gateway/test_ws_fill_watcher.py`

**Bug:** `watchMyTrades` delivers `trade["symbol"] = "PHA/USDT:USDT"` (ccxt format).
`get_open_chains_for_symbol()` queries `WHERE symbol = ?` using that value.
DB stores `"PHAUSDT"` (raw). Zero rows returned → every TP fill is silently dropped.

- [ ] **Step 1: Write failing test (production format: raw in DB, ccxt in trade)**

Append to `tests/runtime_v2/execution_gateway/test_ws_fill_watcher.py`:

```python
def test_process_trade_batch_symbol_mismatch_raw_db_ccxt_trade(ops_db):
    """Production scenario: DB stores raw 'PHAUSDT', trade arrives as 'PHA/USDT:USDT'.
    Without normalization get_open_chains_for_symbol returns [] → miss.
    With normalization → TP_FILLED inserted correctly."""
    # Insert chain with RAW symbol (as production does)
    _insert_open_chain(ops_db, 99, symbol="PHAUSDT", side="SHORT", open_qty=7743.0)
    _insert_tp_command(ops_db, 99, 9901,
                       tp_price=0.05754, tp_level=1, tp_size=3871.5)

    watcher = _make_watcher(ops_db)
    # Trade arrives with CCXT unified format (as Bybit WS delivers)
    trades = [{
        "symbol": "PHA/USDT:USDT",
        "side": "buy",           # buy = close SHORT
        "price": 0.05757,        # within ±1% of 0.05754
        "amount": 3871.5,
        "reduceOnly": True,
        "id": "trade-pha-001",
        "info": {"posQty": "3871.5"},
    }]
    watcher._process_trade_batch(trades)

    conn = sqlite3.connect(ops_db)
    rows = conn.execute(
        "SELECT event_type, payload_json FROM ops_exchange_events WHERE trade_chain_id=99"
    ).fetchall()
    conn.close()
    assert len(rows) == 1, f"Expected 1 TP_FILLED, got {len(rows)}"
    assert rows[0][0] == "TP_FILLED"
    p = json.loads(rows[0][1])
    assert p["fill_price"] == 0.05757
```

- [ ] **Step 2: Run to verify it fails**

```
pytest tests/runtime_v2/execution_gateway/test_ws_fill_watcher.py::test_process_trade_batch_symbol_mismatch_raw_db_ccxt_trade -v
```
Expected: FAIL — `assert len(rows) == 1` → `0 != 1` (symbol mismatch drops the fill)

- [ ] **Step 3: Add `_ccxt_symbol_to_raw()` and apply in `_match_and_save_tp_fill()`**

In `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/ws_fill_watcher.py`:

After the `logger = logging.getLogger(__name__)` line, add the utility function:

```python
def _ccxt_symbol_to_raw(symbol: str) -> str:
    """Convert ccxt unified format to Bybit raw format.

    Examples:
        "PHA/USDT:USDT"  →  "PHAUSDT"
        "BTC/USDT:USDT"  →  "BTCUSDT"
        "PHAUSDT"        →  "PHAUSDT"   (pass-through, already raw)
    """
    if "/" not in symbol:
        return symbol
    base, rest = symbol.split("/", 1)
    quote = rest.split(":")[0]
    return base + quote
```

In `_match_and_save_tp_fill()`, change the line that reads the symbol:

```python
# BEFORE (line ~212):
symbol = trade.get("symbol", "")

# AFTER:
symbol = _ccxt_symbol_to_raw(trade.get("symbol", ""))
```

The rest of the function is unchanged — it already uses `symbol` correctly for the DB query.

- [ ] **Step 4: Run test to verify it passes**

```
pytest tests/runtime_v2/execution_gateway/test_ws_fill_watcher.py::test_process_trade_batch_symbol_mismatch_raw_db_ccxt_trade -v
```
Expected: PASS

- [ ] **Step 5: Run full ws_fill_watcher suite to verify no regressions**

```
pytest tests/runtime_v2/execution_gateway/test_ws_fill_watcher.py -q
```
Expected: all tests pass (existing tests use ccxt format in DB too — `_ccxt_symbol_to_raw("BTC/USDT:USDT")` → `"BTCUSDT"` which doesn't match chain symbol `"BTC/USDT:USDT"` in their DB fixtures... 

**IMPORTANT:** existing tests store chains with `symbol="BTC/USDT:USDT"` (ccxt format). After the fix, `_ccxt_symbol_to_raw("BTC/USDT:USDT")` returns `"BTCUSDT"`, so those tests will break.

Fix existing test helpers: change `_insert_open_chain` default to raw format AND update `_insert_tp_command` payload symbol. Do NOT change the trade format (that stays ccxt). Specifically, in the test file update the `_insert_open_chain` default:

```python
def _insert_open_chain(
    db_path: str,
    chain_id: int,
    symbol: str = "BTCUSDT",      # ← was "BTC/USDT:USDT"
    side: str = "LONG",
    open_qty: float = 0.01,
) -> None:
    ...  # body unchanged
```

And update `_insert_tp_command` payload symbol:
```python
def _insert_tp_command(...) -> None:
    payload = json.dumps({
        "symbol": "BTCUSDT",   # ← was "BTC/USDT:USDT"
        ...
    })
```

Also update every call in existing tests that passes `symbol="ETH/USDT:USDT"`, `symbol="SOL/USDT:USDT"` etc. to use raw format:
- `"ETH/USDT:USDT"` → `"ETHUSDT"`
- `"SOL/USDT:USDT"` → `"SOLUSDT"`

And in the same tests, the trade `trade["symbol"]` stays as ccxt: `"BTC/USDT:USDT"`, `"ETH/USDT:USDT"`, etc.

- [ ] **Step 6: Run full suite again after fixture updates**

```
pytest tests/runtime_v2/execution_gateway/test_ws_fill_watcher.py -q
```
Expected: all pass.

- [ ] **Step 7: Commit**

```
git add src/runtime_v2/execution_gateway/adapters/ccxt_bybit/ws_fill_watcher.py \
        tests/runtime_v2/execution_gateway/test_ws_fill_watcher.py
git commit -m "fix(ws_fill_watcher): normalize ccxt symbol to raw before DB lookup"
```

---

## Task 3: Add `fetch_recent_reduce_trades()` and `fetch_position_details()` to `FakeAdapter`

**Files:**
- Modify: `src/runtime_v2/execution_gateway/adapters/fake.py`
- Modify: `tests/runtime_v2/execution_gateway/test_fake_adapter.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/runtime_v2/execution_gateway/test_fake_adapter.py`:

```python
def test_fake_fetch_recent_reduce_trades_empty_by_default():
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    a = FakeAdapter()
    trades = a.fetch_recent_reduce_trades(
        symbol="PHAUSDT", side="SHORT", execution_account_id="acc"
    )
    assert trades == []


def test_fake_simulate_reduce_trade_returned_by_fetch():
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    a = FakeAdapter()
    a.simulate_reduce_trade(
        symbol="PHAUSDT", side="SHORT",
        price=0.05754, amount=3871.5, trade_id="t-001",
    )
    trades = a.fetch_recent_reduce_trades(
        symbol="PHAUSDT", side="SHORT", execution_account_id="acc"
    )
    assert len(trades) == 1
    assert trades[0].trade_id == "t-001"
    assert trades[0].price == 0.05754
    assert trades[0].amount == 3871.5
    assert trades[0].reduce_only is True


def test_fake_simulate_reduce_trade_isolated_by_symbol_side():
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    a = FakeAdapter()
    a.simulate_reduce_trade("PHAUSDT", "SHORT", 0.05, 100.0, "t-pha")
    a.simulate_reduce_trade("BTCUSDT", "LONG",  50000.0, 0.01, "t-btc")
    assert len(a.fetch_recent_reduce_trades(symbol="PHAUSDT", side="SHORT", execution_account_id="acc")) == 1
    assert len(a.fetch_recent_reduce_trades(symbol="BTCUSDT", side="LONG",  execution_account_id="acc")) == 1
    assert len(a.fetch_recent_reduce_trades(symbol="ETHUSDT", side="LONG",  execution_account_id="acc")) == 0


def test_fake_fetch_position_details_none_by_default():
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    a = FakeAdapter()
    result = a.fetch_position_details(
        symbol="PHAUSDT", side="SHORT", execution_account_id="acc"
    )
    assert result is None


def test_fake_fetch_position_details_returns_preset():
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.models import RawPositionDetails
    a = FakeAdapter()
    a.set_position_details("PHAUSDT", "SHORT", RawPositionDetails(
        symbol="PHAUSDT", side="SHORT", qty=3871.5,
        take_profit=0.05373, stop_loss=0.06908,
    ))
    pos = a.fetch_position_details(symbol="PHAUSDT", side="SHORT", execution_account_id="acc")
    assert pos is not None
    assert pos.take_profit == 0.05373
    assert pos.qty == 3871.5
```

- [ ] **Step 2: Run to verify failures**

```
pytest tests/runtime_v2/execution_gateway/test_fake_adapter.py -k "reduce_trade or position_details" -v
```
Expected: all 5 new tests FAIL with AttributeError.

- [ ] **Step 3: Implement in `fake.py`**

In `src/runtime_v2/execution_gateway/adapters/fake.py`, add the import at top:

```python
from src.runtime_v2.execution_gateway.models import (
    AdapterCapabilities, AdapterResult, RawAdapterOrder,
    RawAdapterTrade, RawPositionDetails,
)
```

In `FakeAdapter.__init__`, add after `self._mark_prices`:

```python
self._reduce_trades: dict[str, list[RawAdapterTrade]] = {}
self._position_details: dict[str, RawPositionDetails] = {}
```

Add these methods to `FakeAdapter`:

```python
def simulate_reduce_trade(
    self,
    symbol: str,
    side: str,
    price: float,
    amount: float,
    trade_id: str,
) -> None:
    """Register a reduceOnly fill for fetch_recent_reduce_trades() to return."""
    key = f"{symbol}:{side}"
    self._reduce_trades.setdefault(key, []).append(
        RawAdapterTrade(trade_id=trade_id, symbol=symbol, price=price, amount=amount)
    )

def fetch_recent_reduce_trades(
    self,
    *,
    symbol: str,
    side: str,
    execution_account_id: str,
    limit: int = 50,
) -> list[RawAdapterTrade]:
    key = f"{symbol}:{side}"
    return list(self._reduce_trades.get(key, []))[:limit]

def set_position_details(
    self,
    symbol: str,
    side: str,
    details: RawPositionDetails,
) -> None:
    """Preset what fetch_position_details() returns for symbol+side."""
    self._position_details[f"{symbol}:{side}"] = details

def fetch_position_details(
    self,
    *,
    symbol: str,
    side: str,
    execution_account_id: str,
) -> RawPositionDetails | None:
    return self._position_details.get(f"{symbol}:{side}")
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/runtime_v2/execution_gateway/test_fake_adapter.py -k "reduce_trade or position_details" -v
```
Expected: all 5 PASS.

- [ ] **Step 5: Run full fake adapter suite for regressions**

```
pytest tests/runtime_v2/execution_gateway/test_fake_adapter.py -q
```

- [ ] **Step 6: Commit**

```
git add src/runtime_v2/execution_gateway/adapters/fake.py \
        tests/runtime_v2/execution_gateway/test_fake_adapter.py
git commit -m "feat(fake_adapter): add fetch_recent_reduce_trades and fetch_position_details"
```

---

## Task 4: Implement `fetch_recent_reduce_trades()` and `fetch_position_details()` on `CcxtBybitAdapter`

**Files:**
- Modify: `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/adapter.py`
- Modify: `tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py`:

```python
def test_fetch_recent_reduce_trades_returns_reduce_only_fills():
    """fetch_recent_reduce_trades filters to reduceOnly trades, normalizes symbol to raw."""
    from unittest.mock import MagicMock
    from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.adapter import CcxtBybitAdapter

    exchange = MagicMock()
    exchange.fetch_my_trades.return_value = [
        {
            "id": "trade-001",
            "symbol": "PHA/USDT:USDT",
            "side": "buy",
            "price": 0.05754,
            "amount": 3871.5,
            "info": {"reduceOnly": True},
        },
        {
            "id": "trade-002",
            "symbol": "PHA/USDT:USDT",
            "side": "sell",
            "price": 0.06000,
            "amount": 7743.0,
            "info": {"reduceOnly": False},  # entry fill → excluded
        },
    ]
    adapter = CcxtBybitAdapter(
        api_key="k", api_secret="s", connector="c", _exchange=exchange
    )
    trades = adapter.fetch_recent_reduce_trades(
        symbol="PHAUSDT", side="SHORT", execution_account_id="acc"
    )
    assert len(trades) == 1
    assert trades[0].trade_id == "trade-001"
    assert trades[0].symbol == "PHAUSDT"   # raw format
    assert trades[0].price == 0.05754
    assert trades[0].amount == 3871.5
    assert trades[0].reduce_only is True


def test_fetch_recent_reduce_trades_returns_empty_on_exception():
    from unittest.mock import MagicMock
    from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.adapter import CcxtBybitAdapter

    exchange = MagicMock()
    exchange.fetch_my_trades.side_effect = RuntimeError("network error")
    adapter = CcxtBybitAdapter(api_key="k", api_secret="s", connector="c", _exchange=exchange)
    trades = adapter.fetch_recent_reduce_trades(
        symbol="PHAUSDT", side="SHORT", execution_account_id="acc"
    )
    assert trades == []


def test_fetch_position_details_returns_tp_sl_from_info():
    from unittest.mock import MagicMock
    from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.adapter import CcxtBybitAdapter

    exchange = MagicMock()
    exchange.fetch_positions.return_value = [
        {
            "symbol": "PHA/USDT:USDT",
            "side": "short",
            "contracts": 3871.5,
            "info": {
                "symbol": "PHAUSDT",
                "side": "Sell",
                "takeProfit": "0.05373",
                "stopLoss": "0.06908",
            },
        }
    ]
    adapter = CcxtBybitAdapter(api_key="k", api_secret="s", connector="c", _exchange=exchange)
    pos = adapter.fetch_position_details(
        symbol="PHAUSDT", side="SHORT", execution_account_id="acc"
    )
    assert pos is not None
    assert pos.symbol == "PHAUSDT"
    assert pos.qty == 3871.5
    assert pos.take_profit == 0.05373
    assert pos.stop_loss == 0.06908


def test_fetch_position_details_tp_zero_when_empty_string():
    """Bybit sets takeProfit='' when not configured; should map to 0.0."""
    from unittest.mock import MagicMock
    from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.adapter import CcxtBybitAdapter

    exchange = MagicMock()
    exchange.fetch_positions.return_value = [
        {
            "symbol": "PHA/USDT:USDT",
            "side": "short",
            "contracts": 7743.0,
            "info": {"takeProfit": "", "stopLoss": "0.06908"},
        }
    ]
    adapter = CcxtBybitAdapter(api_key="k", api_secret="s", connector="c", _exchange=exchange)
    pos = adapter.fetch_position_details(
        symbol="PHAUSDT", side="SHORT", execution_account_id="acc"
    )
    assert pos is not None
    assert pos.take_profit == 0.0   # empty string → 0.0


def test_fetch_position_details_returns_none_when_not_found():
    from unittest.mock import MagicMock
    from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.adapter import CcxtBybitAdapter

    exchange = MagicMock()
    exchange.fetch_positions.return_value = []
    adapter = CcxtBybitAdapter(api_key="k", api_secret="s", connector="c", _exchange=exchange)
    pos = adapter.fetch_position_details(
        symbol="PHAUSDT", side="SHORT", execution_account_id="acc"
    )
    assert pos is None
```

- [ ] **Step 2: Run to verify failures**

```
pytest tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py -k "fetch_recent or fetch_position_details" -v
```
Expected: all 5 FAIL with AttributeError.

- [ ] **Step 3: Implement in `adapter.py`**

Add import at top of `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/adapter.py`:

```python
import time
from src.runtime_v2.execution_gateway.models import (
    AdapterCapabilities, AdapterResult, RawAdapterOrder,
    RawAdapterTrade, RawPositionDetails,
)
```

(Remove duplicate `AdapterCapabilities, AdapterResult, RawAdapterOrder` from existing import.)

Add these two methods to `CcxtBybitAdapter`, after `fetch_mark_price()`:

```python
def fetch_recent_reduce_trades(
    self,
    *,
    symbol: str,
    side: str,
    execution_account_id: str,
    limit: int = 50,
) -> list[RawAdapterTrade]:
    """Return recent position-closing fills (reduceOnly=True) for symbol+side.

    Uses REST fetch_my_trades() — catches fills missed by WS or during bot downtime.
    symbol: Bybit raw format (e.g. PHAUSDT). ccxt accepts raw format for linear futures.
    """
    since_ms = int((time.time() - 86400) * 1000)  # last 24h
    try:
        raw_trades = self._exchange.fetch_my_trades(symbol, since=since_ms, limit=limit)
    except Exception as exc:
        logger.warning("fetch_my_trades failed for %s: %s", symbol, exc)
        return []

    result: list[RawAdapterTrade] = []
    for t in raw_trades:
        info = t.get("info") or {}
        reduce_only = bool(info.get("reduceOnly", False))
        if not reduce_only:
            continue
        trade_symbol = self._normalize_bybit_symbol(t.get("symbol") or symbol)
        try:
            result.append(RawAdapterTrade(
                trade_id=str(t["id"]),
                symbol=trade_symbol,
                price=float(t["price"]),
                amount=float(t["amount"]),
                reduce_only=True,
            ))
        except Exception:
            logger.debug("skipping malformed trade %s", t.get("id"))
    return result

def fetch_position_details(
    self,
    *,
    symbol: str,
    side: str,
    execution_account_id: str,
) -> RawPositionDetails | None:
    """Return TP/SL levels currently set on the exchange for symbol+side.

    Uses fetch_positions() — detects if protective orders were externally cancelled.
    Returns None if position not found or on error.
    """
    try:
        positions = self._exchange.fetch_positions([symbol])
    except Exception as exc:
        logger.warning("fetch_position_details failed for %s %s: %s", symbol, side, exc)
        return None

    for pos in positions:
        if str(pos.get("side") or "").lower() != side.lower():
            continue
        info = pos.get("info") or {}
        raw_symbol = info.get("symbol") or self._normalize_bybit_symbol(
            pos.get("symbol") or symbol
        )

        def _parse_price(val: object) -> float | None:
            if val is None:
                return None
            s = str(val).strip()
            if s == "":
                return 0.0
            try:
                return float(s)
            except ValueError:
                return None

        return RawPositionDetails(
            symbol=raw_symbol,
            side=side.upper(),
            qty=float(pos.get("contracts") or 0.0),
            take_profit=_parse_price(info.get("takeProfit")),
            stop_loss=_parse_price(info.get("stopLoss")),
        )
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py -k "fetch_recent or fetch_position_details" -v
```
Expected: all 5 PASS.

- [ ] **Step 5: Run full adapter unit tests**

```
pytest tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py -q
```

- [ ] **Step 6: Commit**

```
git add src/runtime_v2/execution_gateway/adapters/ccxt_bybit/adapter.py \
        tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py
git commit -m "feat(ccxt_adapter): add fetch_recent_reduce_trades and fetch_position_details"
```

---

## Task 5: Replace `run_tp_reconciliation()` with `run_trade_based_reconciliation()` in `event_sync.py`

**Files:**
- Modify: `src/runtime_v2/execution_gateway/event_sync.py`
- Modify: `tests/runtime_v2/execution_gateway/test_event_sync.py`

**What gets deleted:** `run_tp_reconciliation()`, `_get_sent_tp_commands()`, `_save_tp_fill()`.
**What gets added:** `run_trade_based_reconciliation()`, `_get_tp_reconciliation_entries()`, `_save_tp_fill_from_trade()`.

- [ ] **Step 1: Write failing tests**

Replace `test_tp_filled_ws_and_polling_unified_key_no_duplicate` (which tested the deleted method) and add the new tests. In `tests/runtime_v2/execution_gateway/test_event_sync.py`:

```python
# ── helpers for trade-based reconciliation tests ─────────────────────────────

def _insert_open_chain_with_tp_v2(
    db_path: str,
    chain_id: int,
    symbol: str = "PHAUSDT",
    side: str = "SHORT",
    tp_price: float = 0.05754,
    tp_size: float = 3871.5,
    tp_level: int = 1,
    open_qty: float = 7743.0,
) -> None:
    """Chain OPEN (raw symbol) + active SET_POSITION_TPSL_PARTIAL command."""
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
         symbol, side, "OPEN", "TWO_STEP", "{}", open_qty, open_qty, now, now),
    )
    conn.execute(
        "INSERT INTO ops_execution_commands "
        "(command_id, trade_chain_id, command_type, status, payload_json, "
        "idempotency_key, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (chain_id * 100, chain_id, "SET_POSITION_TPSL_PARTIAL", "DONE",
         json.dumps({
             "symbol": symbol, "side": side,
             "take_profit": tp_price, "tp_size": tp_size, "tp_sequence": tp_level,
         }),
         f"idem_tp:{chain_id}", now, now),
    )
    conn.commit()
    conn.close()


def test_trade_based_reconciliation_inserts_tp_filled_on_matching_trade(ops_db):
    """run_trade_based_reconciliation() detects TP fill via fetch_recent_reduce_trades()."""
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_open_chain_with_tp_v2(ops_db, 50, symbol="PHAUSDT", side="SHORT",
                                  tp_price=0.05754, tp_size=3871.5, open_qty=7743.0)
    adapter = FakeAdapter()
    # Simulate the intermediate TP fill
    adapter.simulate_reduce_trade(
        symbol="PHAUSDT", side="SHORT",
        price=0.05754, amount=3871.5, trade_id="exch-trade-001",
    )
    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(
        ops_db_path=ops_db, adapter=adapter, repo=repo, execution_account_id="acc"
    )
    count = worker.run_trade_based_reconciliation()

    assert count == 1
    conn = sqlite3.connect(ops_db)
    rows = conn.execute(
        "SELECT event_type, payload_json, idempotency_key FROM ops_exchange_events WHERE trade_chain_id=50"
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0][0] == "TP_FILLED"
    assert rows[0][2] == "TP_FILLED:50:level:1"
    p = json.loads(rows[0][1])
    assert p["fill_price"] == 0.05754
    assert p["filled_qty"] == 3871.5
    assert p["exchange_trade_id"] == "exch-trade-001"
    assert p["source"] == "trade_based_reconciliation"


def test_trade_based_reconciliation_idempotent(ops_db):
    """Calling run_trade_based_reconciliation() twice → exactly 1 TP_FILLED event."""
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_open_chain_with_tp_v2(ops_db, 51, tp_price=0.05754)
    adapter = FakeAdapter()
    adapter.simulate_reduce_trade("PHAUSDT", "SHORT", 0.05754, 3871.5, "t-idem")
    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(ops_db_path=ops_db, adapter=adapter, repo=repo, execution_account_id="acc")

    worker.run_trade_based_reconciliation()
    worker.run_trade_based_reconciliation()

    conn = sqlite3.connect(ops_db)
    count = conn.execute(
        "SELECT COUNT(*) FROM ops_exchange_events WHERE trade_chain_id=51 AND event_type='TP_FILLED'"
    ).fetchone()[0]
    conn.close()
    assert count == 1


def test_trade_based_reconciliation_skips_non_matching_price(ops_db):
    """Trade price >1% away from TP → no event inserted."""
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_open_chain_with_tp_v2(ops_db, 52, tp_price=0.05754)
    adapter = FakeAdapter()
    # Price 2% away from TP
    adapter.simulate_reduce_trade("PHAUSDT", "SHORT", 0.0560, 3871.5, "t-miss")
    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(ops_db_path=ops_db, adapter=adapter, repo=repo, execution_account_id="acc")

    count = worker.run_trade_based_reconciliation()

    assert count == 0
    conn = sqlite3.connect(ops_db)
    n = conn.execute("SELECT COUNT(*) FROM ops_exchange_events").fetchone()[0]
    conn.close()
    assert n == 0


def test_trade_based_reconciliation_deduplicates_with_ws_insertion(ops_db):
    """If WS already inserted TP_FILLED with same idempotency key, REST poll is no-op."""
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_open_chain_with_tp_v2(ops_db, 53, tp_price=0.05754)
    # Simulate WS already inserted
    conn = sqlite3.connect(ops_db)
    conn.execute(
        "INSERT INTO ops_exchange_events "
        "(trade_chain_id, event_type, payload_json, processing_status, idempotency_key, received_at) "
        "VALUES (?,?,?,?,?,datetime('now'))",
        (53, "TP_FILLED",
         '{"tp_level":1,"is_final":false,"fill_price":0.05754,"source":"watch_my_trades"}',
         "NEW", "TP_FILLED:53:level:1"),
    )
    conn.commit()
    conn.close()

    adapter = FakeAdapter()
    adapter.simulate_reduce_trade("PHAUSDT", "SHORT", 0.05754, 3871.5, "t-ws-dup")
    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(ops_db_path=ops_db, adapter=adapter, repo=repo, execution_account_id="acc")
    worker.run_trade_based_reconciliation()

    conn = sqlite3.connect(ops_db)
    count = conn.execute(
        "SELECT COUNT(*) FROM ops_exchange_events WHERE trade_chain_id=53"
    ).fetchone()[0]
    conn.close()
    assert count == 1  # still just the one WS-inserted event


def test_trade_based_reconciliation_noop_when_adapter_has_no_method(ops_db):
    """If adapter lacks fetch_recent_reduce_trades → returns 0, no crash."""
    from unittest.mock import MagicMock
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_open_chain_with_tp_v2(ops_db, 54)
    adapter = MagicMock(spec=[])  # spec=[] → hasattr always False
    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(ops_db_path=ops_db, adapter=adapter, repo=repo, execution_account_id="acc")

    count = worker.run_trade_based_reconciliation()
    assert count == 0
```

- [ ] **Step 2: Run to verify failures**

```
pytest tests/runtime_v2/execution_gateway/test_event_sync.py -k "trade_based" -v
```
Expected: all 5 FAIL with `AttributeError: 'ExchangeEventSyncWorker' object has no attribute 'run_trade_based_reconciliation'`.

- [ ] **Step 3: Implement in `event_sync.py`**

Add import at top of `src/runtime_v2/execution_gateway/event_sync.py`:

```python
from src.runtime_v2.execution_gateway.models import RawAdapterTrade
```

**Delete** these methods entirely:
- `run_tp_reconciliation()`
- `_get_sent_tp_commands()`
- `_save_tp_fill()`
- `_get_filled_entry_qty()`  (only used by deleted `run_tp_reconciliation`)
- `_tp_fill_event_exists()` — **keep it**, still used by the new method

**Add** these new methods to `ExchangeEventSyncWorker`:

```python
def run_trade_based_reconciliation(self) -> int:
    """Poll recent reduceOnly fills via REST and match against active TP commands.

    Replaces run_tp_reconciliation(). Uses real fill prices (not qty comparison).
    Shares the same idempotency key format as watchMyTrades so INSERT OR IGNORE
    prevents duplicates when both paths run.

    Returns count of new TP_FILLED events inserted.
    """
    if not hasattr(self._adapter, "fetch_recent_reduce_trades"):
        return 0

    entries = self._get_tp_reconciliation_entries()
    if not entries:
        return 0

    # Group by (symbol, side) to minimise API calls
    from collections import defaultdict
    by_key: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for e in entries:
        by_key[(e["symbol"], e["side"])].append(e)

    processed = 0
    for (symbol, side), group in by_key.items():
        try:
            trades = self._adapter.fetch_recent_reduce_trades(
                symbol=symbol,
                side=side,
                execution_account_id=self._execution_account_id,
                limit=50,
            )
        except Exception:
            logger.exception("fetch_recent_reduce_trades error for %s %s", symbol, side)
            continue

        for entry in group:
            chain_id = entry["chain_id"]
            tp_level = entry["tp_level"]
            tp_price = entry["tp_price"]

            if self._tp_fill_event_exists(chain_id, tp_level):
                continue  # already recorded (e.g. by WS)

            if tp_price <= 0:
                continue  # no valid TP price in command payload

            for trade in trades:
                if abs(trade.price - tp_price) / tp_price <= 0.01:  # ±1% tolerance
                    if self._save_tp_fill_from_trade(chain_id, tp_level, trade):
                        processed += 1
                    break  # one trade per TP level

    return processed

def _get_tp_reconciliation_entries(self) -> list[dict]:
    """Return active TP commands for open chains with price, level, and symbol."""
    conn = sqlite3.connect(self._ops_db)
    try:
        rows = conn.execute(
            "SELECT c.command_id, c.trade_chain_id, c.payload_json, t.symbol, t.side "
            "FROM ops_execution_commands c "
            "JOIN ops_trade_chains t ON c.trade_chain_id = t.trade_chain_id "
            "WHERE c.command_type IN ('SET_POSITION_TPSL_PARTIAL', 'SET_POSITION_TPSL_FULL') "
            "AND c.status IN ('SENT', 'DONE') "
            "AND t.lifecycle_state IN ('OPEN', 'PARTIALLY_CLOSED')"
        ).fetchall()
        result: list[dict] = []
        for cmd_id, chain_id, payload_json, symbol, side in rows:
            try:
                payload = json.loads(payload_json)
                result.append({
                    "cmd_id":   cmd_id,
                    "chain_id": chain_id,
                    "tp_level": int(payload.get("tp_sequence", 1)),
                    "tp_price": float(payload.get("take_profit", 0)),
                    "tp_size":  float(payload.get("tp_size", 0)),
                    "symbol":   symbol,
                    "side":     side,
                })
            except Exception:
                pass
        return result
    finally:
        conn.close()

def _save_tp_fill_from_trade(
    self,
    trade_chain_id: int,
    tp_level: int,
    trade: RawAdapterTrade,
) -> bool:
    """INSERT OR IGNORE TP_FILLED event with real fill price from trade REST data."""
    idempotency_key = f"TP_FILLED:{trade_chain_id}:level:{tp_level}"
    payload = json.dumps({
        "tp_level":        tp_level,
        "is_final":        False,   # conservative; lifecycle uses position qty to decide
        "fill_price":      trade.price,
        "filled_qty":      trade.amount,
        "source":          "trade_based_reconciliation",
        "exchange_trade_id": trade.trade_id,
    })
    now = _now()
    conn = sqlite3.connect(self._ops_db)
    try:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO ops_exchange_events "
            "(trade_chain_id, event_type, payload_json, processing_status, "
            "idempotency_key, received_at) VALUES (?,?,?,?,?,?)",
            (trade_chain_id, "TP_FILLED", payload, "NEW", idempotency_key, now),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()
```

- [ ] **Step 4: Run new tests to verify they pass**

```
pytest tests/runtime_v2/execution_gateway/test_event_sync.py -k "trade_based" -v
```
Expected: all 5 PASS.

- [ ] **Step 5: Remove the now-obsolete existing test that tested `run_tp_reconciliation`**

Delete `test_tp_filled_ws_and_polling_unified_key_no_duplicate` from `test_event_sync.py` (it called the deleted method).

- [ ] **Step 6: Run full event_sync suite**

```
pytest tests/runtime_v2/execution_gateway/test_event_sync.py -q
```
Expected: all remaining tests pass.

- [ ] **Step 7: Commit**

```
git add src/runtime_v2/execution_gateway/event_sync.py \
        tests/runtime_v2/execution_gateway/test_event_sync.py
git commit -m "feat(event_sync): replace run_tp_reconciliation with trade-based REST polling"
```

---

## Task 6: Add `run_protective_orders_reconciliation()` to `event_sync.py`

**Files:**
- Modify: `src/runtime_v2/execution_gateway/event_sync.py`
- Modify: `tests/runtime_v2/execution_gateway/test_event_sync.py`

**What it does:** For each open chain with a known TP command, fetch the live position from the exchange. If the exchange no longer has the TP set (takeProfit=0.0) and no `TP_FILLED` event was recorded → the TP was externally cancelled without triggering. Insert `PROTECTIVE_ORDERS_MISSING` event. The lifecycle event processor will log a warning (existing `unhandled exchange event type` log path) — a full lifecycle handler is a future step.

- [ ] **Step 1: Write failing tests**

Append to `tests/runtime_v2/execution_gateway/test_event_sync.py`:

```python
def test_protective_orders_reconciliation_emits_event_when_tp_removed(ops_db):
    """Exchange TP is 0.0 but bot set 0.05754 and no TP_FILLED exists → PROTECTIVE_ORDERS_MISSING."""
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.models import RawPositionDetails
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_open_chain_with_tp_v2(ops_db, 60, tp_price=0.05754)
    adapter = FakeAdapter()
    adapter.set_position_details("PHAUSDT", "SHORT", RawPositionDetails(
        symbol="PHAUSDT", side="SHORT", qty=7743.0,
        take_profit=0.0,  # ← cleared on exchange (manually cancelled)
        stop_loss=0.06908,
    ))
    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(
        ops_db_path=ops_db, adapter=adapter, repo=repo, execution_account_id="acc"
    )
    count = worker.run_protective_orders_reconciliation()

    assert count == 1
    conn = sqlite3.connect(ops_db)
    rows = conn.execute(
        "SELECT event_type, payload_json FROM ops_exchange_events WHERE trade_chain_id=60"
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0][0] == "PROTECTIVE_ORDERS_MISSING"
    p = json.loads(rows[0][1])
    assert p["expected_tp"] == 0.05754
    assert p["tp_level"] == 1
    assert p["reason"] == "tp_removed_externally"


def test_protective_orders_reconciliation_skips_when_tp_fill_exists(ops_db):
    """If TP_FILLED already recorded → TP triggered normally → skip detection."""
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.models import RawPositionDetails
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_open_chain_with_tp_v2(ops_db, 61, tp_price=0.05754)
    # Existing TP_FILLED event → means it triggered, not cancelled
    conn = sqlite3.connect(ops_db)
    conn.execute(
        "INSERT INTO ops_exchange_events "
        "(trade_chain_id, event_type, payload_json, processing_status, idempotency_key, received_at) "
        "VALUES (?,?,?,?,?,datetime('now'))",
        (61, "TP_FILLED", '{"tp_level":1}', "DONE", "TP_FILLED:61:level:1"),
    )
    conn.commit()
    conn.close()

    adapter = FakeAdapter()
    adapter.set_position_details("PHAUSDT", "SHORT", RawPositionDetails(
        symbol="PHAUSDT", side="SHORT", qty=3871.5, take_profit=0.0
    ))
    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(ops_db_path=ops_db, adapter=adapter, repo=repo, execution_account_id="acc")
    count = worker.run_protective_orders_reconciliation()

    assert count == 0


def test_protective_orders_reconciliation_skips_when_tp_still_active(ops_db):
    """Exchange still has TP at expected level → no event."""
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.models import RawPositionDetails
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_open_chain_with_tp_v2(ops_db, 62, tp_price=0.05754)
    adapter = FakeAdapter()
    adapter.set_position_details("PHAUSDT", "SHORT", RawPositionDetails(
        symbol="PHAUSDT", side="SHORT", qty=7743.0,
        take_profit=0.05754,  # still there
    ))
    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(ops_db_path=ops_db, adapter=adapter, repo=repo, execution_account_id="acc")
    count = worker.run_protective_orders_reconciliation()

    assert count == 0
    conn = sqlite3.connect(ops_db)
    n = conn.execute("SELECT COUNT(*) FROM ops_exchange_events").fetchone()[0]
    conn.close()
    assert n == 0


def test_protective_orders_reconciliation_idempotent(ops_db):
    """Two calls → exactly 1 PROTECTIVE_ORDERS_MISSING event."""
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.models import RawPositionDetails
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_open_chain_with_tp_v2(ops_db, 63, tp_price=0.05754)
    adapter = FakeAdapter()
    adapter.set_position_details("PHAUSDT", "SHORT", RawPositionDetails(
        symbol="PHAUSDT", side="SHORT", qty=7743.0, take_profit=0.0
    ))
    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(ops_db_path=ops_db, adapter=adapter, repo=repo, execution_account_id="acc")
    worker.run_protective_orders_reconciliation()
    worker.run_protective_orders_reconciliation()

    conn = sqlite3.connect(ops_db)
    count = conn.execute(
        "SELECT COUNT(*) FROM ops_exchange_events WHERE event_type='PROTECTIVE_ORDERS_MISSING'"
    ).fetchone()[0]
    conn.close()
    assert count == 1


def test_protective_orders_reconciliation_noop_when_adapter_lacks_method(ops_db):
    from unittest.mock import MagicMock
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_open_chain_with_tp_v2(ops_db, 64)
    adapter = MagicMock(spec=[])  # no methods
    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(ops_db_path=ops_db, adapter=adapter, repo=repo, execution_account_id="acc")
    count = worker.run_protective_orders_reconciliation()
    assert count == 0
```

- [ ] **Step 2: Run to verify failures**

```
pytest tests/runtime_v2/execution_gateway/test_event_sync.py -k "protective_orders" -v
```
Expected: all 5 FAIL with `AttributeError: 'ExchangeEventSyncWorker' ... 'run_protective_orders_reconciliation'`.

- [ ] **Step 3: Implement in `event_sync.py`**

Add these methods to `ExchangeEventSyncWorker`:

```python
def run_protective_orders_reconciliation(self) -> int:
    """Detect when a position-level TP was externally cancelled (no fill occurred).

    Logic:
      1. For each open chain with an active SET_POSITION_TPSL_* command:
         - Fetch the live position from the exchange
         - If exchange takeProfit == 0.0 (cleared) but we expected a TP:
             - And no TP_FILLED event is recorded (= it didn't trigger, it was cancelled)
             - → Insert PROTECTIVE_ORDERS_MISSING event

    The lifecycle event processor currently logs a warning for unhandled event types.
    A full automated response (re-placing the TP) is a future enhancement.

    Returns count of new PROTECTIVE_ORDERS_MISSING events inserted.
    """
    if not hasattr(self._adapter, "fetch_position_details"):
        return 0

    entries = self._get_tp_reconciliation_entries()
    if not entries:
        return 0

    # Use the most recent TP command per chain (highest cmd_id)
    latest_per_chain: dict[int, dict] = {}
    for e in entries:
        chain_id = e["chain_id"]
        if chain_id not in latest_per_chain or e["cmd_id"] > latest_per_chain[chain_id]["cmd_id"]:
            latest_per_chain[chain_id] = e

    processed = 0
    for chain_id, entry in latest_per_chain.items():
        expected_tp = entry["tp_price"]
        tp_level    = entry["tp_level"]
        symbol      = entry["symbol"]
        side        = entry["side"]

        if expected_tp <= 0:
            continue

        if self._tp_fill_event_exists(chain_id, tp_level):
            continue  # TP triggered normally — not a cancellation

        try:
            pos = self._adapter.fetch_position_details(
                symbol=symbol,
                side=side,
                execution_account_id=self._execution_account_id,
            )
        except Exception:
            logger.exception("fetch_position_details error for chain %s", chain_id)
            continue

        if pos is None:
            continue  # position not found — can't determine state

        if pos.take_profit is None:
            continue  # exchange doesn't expose TP field for this adapter

        if pos.take_profit != 0.0:
            continue  # TP still active on exchange

        # TP is 0.0 on exchange, no TP_FILLED recorded → externally cancelled
        idempotency_key = f"PROTECTIVE_ORDERS_MISSING:{chain_id}:tp:{tp_level}"
        self._save_protective_orders_missing(
            chain_id=chain_id,
            idempotency_key=idempotency_key,
            payload={
                "expected_tp": expected_tp,
                "tp_level":    tp_level,
                "reason":      "tp_removed_externally",
            },
        )
        logger.warning(
            "protective orders missing: chain=%s tp_level=%s expected_tp=%s — "
            "TP was removed externally without a fill",
            chain_id, tp_level, expected_tp,
        )
        processed += 1

    return processed

def _save_protective_orders_missing(
    self,
    chain_id: int,
    idempotency_key: str,
    payload: dict,
) -> None:
    now = _now()
    conn = sqlite3.connect(self._ops_db)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO ops_exchange_events "
            "(trade_chain_id, event_type, payload_json, processing_status, "
            "idempotency_key, received_at) VALUES (?,?,?,?,?,?)",
            (chain_id, "PROTECTIVE_ORDERS_MISSING",
             json.dumps(payload), "NEW", idempotency_key, now),
        )
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/runtime_v2/execution_gateway/test_event_sync.py -k "protective_orders" -v
```
Expected: all 5 PASS.

- [ ] **Step 5: Run full event_sync suite**

```
pytest tests/runtime_v2/execution_gateway/test_event_sync.py -q
```

- [ ] **Step 6: Commit**

```
git add src/runtime_v2/execution_gateway/event_sync.py \
        tests/runtime_v2/execution_gateway/test_event_sync.py
git commit -m "feat(event_sync): add run_protective_orders_reconciliation for external TP cancellation"
```

---

## Task 7: Update `main.py` call sites

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Update `_run_position_reconciliation_periodically()`**

In `main.py`, replace:

```python
async def _run_position_reconciliation_periodically(
    *,
    sync_worker: ExchangeEventSyncWorker,
    interval_seconds: int,
    logger,
) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            sync_worker.run_position_reconciliation()
            sync_worker.run_tp_reconciliation()
        except Exception:
            logger.exception("periodic position/tp reconciliation error")
```

With:

```python
async def _run_position_reconciliation_periodically(
    *,
    sync_worker: ExchangeEventSyncWorker,
    interval_seconds: int,
    logger,
) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            sync_worker.run_position_reconciliation()
            sync_worker.run_trade_based_reconciliation()
            sync_worker.run_protective_orders_reconciliation()
        except Exception:
            logger.exception("periodic position/tp reconciliation error")
```

- [ ] **Step 2: Verify no `run_tp_reconciliation` references remain**

```
grep -rn "run_tp_reconciliation" src/ main.py tests/
```
Expected: zero results.

- [ ] **Step 3: Run full execution_gateway test suite**

```
pytest tests/runtime_v2/execution_gateway/ -q
```
Expected: all tests pass (baseline was 227 passed, 6 skipped).

- [ ] **Step 4: Commit**

```
git add main.py
git commit -m "fix(main): replace run_tp_reconciliation with trade-based and protective-orders reconciliation"
```

---

## Final Validation

- [ ] **Run full test suite**

```
pytest tests/runtime_v2/ -q
```
Expected: all pass, count >= baseline.

- [ ] **Verify no dead references to deleted methods**

```
grep -rn "_get_sent_tp_commands\|_save_tp_fill\b\|run_tp_reconciliation\|_get_filled_entry_qty" src/ main.py tests/
```
Expected: zero results.

- [ ] **Final commit summary tag**

```
git tag tp-reconciliation-overhaul-v1
```

---

## Self-Review

**Spec coverage:**
- ✅ Symbol mismatch in watchMyTrades → Task 2
- ✅ Replace qty comparison with fetch_my_trades → Task 5
- ✅ Eliminate qty comparison fallback (deleted) → Task 5
- ✅ fetch_position_details for cancellation detection → Task 6
- ✅ qty==0 safety net kept (run_position_reconciliation untouched) → no task needed
- ✅ RawAdapterTrade / RawPositionDetails models → Task 1
- ✅ FakeAdapter stubs for both new methods → Task 3
- ✅ CcxtBybitAdapter implementations → Task 4
- ✅ main.py updated → Task 7

**Placeholder scan:** no TBD/TODO in code blocks.

**Type consistency:**
- `RawAdapterTrade` defined Task 1, used in Task 5 (`_save_tp_fill_from_trade`)
- `RawPositionDetails` defined Task 1, used in Task 6 tests
- `fetch_recent_reduce_trades()` signature consistent: Task 3 (Fake), Task 4 (Ccxt), Task 5 (event_sync caller)
- `fetch_position_details()` signature consistent: Task 3 (Fake), Task 4 (Ccxt), Task 6 (event_sync caller)
- `_get_tp_reconciliation_entries()` returns `list[dict]` with keys `cmd_id, chain_id, tp_level, tp_price, tp_size, symbol, side` — used consistently in Tasks 5 and 6
- Idempotency key format `"TP_FILLED:{chain_id}:level:{tp_level}"` consistent with ws_fill_watcher (Task 2 unchanged, Task 5 uses same key)
