# Exchange-Centric Event System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace heuristic ±1% price matching with deterministic Bybit field classification, add manual event detection, and persist a full audit trail in `exchange_raw_events` for the future Telegram logger.

**Architecture:** Three pure classes (`EventNormalizer`, `EventClassifier`, `GatewayCommandRepository`) handle all classification and persistence. `BybitWsFillWatcher` runs 3 WS streams and delegates to these classes. `ExchangeEventSyncWorker` uses the same classes as a REST safety net. Both paths write atomically to `exchange_raw_events` + `ops_exchange_events` via `INSERT OR IGNORE`.

**Tech Stack:** Python 3.12, SQLite (aiosqlite-free — ops DB is sync), Pydantic-free (dataclasses), ccxt.pro for WS streams, pytest for all tests.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `db/ops_migrations/006_ops_exchange_raw_events.sql` | CREATE | Raw events table + indexes |
| `src/runtime_v2/execution_gateway/event_ingest/__init__.py` | CREATE | Package marker |
| `src/runtime_v2/execution_gateway/event_ingest/models.py` | CREATE | `ExchangeRawEvent`, `ClassifiedEvent`, type aliases |
| `src/runtime_v2/execution_gateway/event_ingest/normalizer.py` | CREATE | CCXT dict → `ExchangeRawEvent` |
| `src/runtime_v2/execution_gateway/event_ingest/classifier.py` | CREATE | `ExchangeRawEvent` → `ClassifiedEvent` (deterministic logic) |
| `src/runtime_v2/execution_gateway/repositories.py` | EXTEND | +5 methods, zero changes to existing |
| `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/ws_fill_watcher.py` | REFACTOR | 3-stream WS, zero matching |
| `src/runtime_v2/execution_gateway/event_sync.py` | REFACTOR | REST safety net, uses normalizer+classifier |
| `tests/runtime_v2/execution_gateway/test_event_ingest_normalizer.py` | CREATE | Normalizer unit tests |
| `tests/runtime_v2/execution_gateway/test_event_ingest_classifier.py` | CREATE | Classifier unit tests |
| `tests/runtime_v2/execution_gateway/test_repositories_raw.py` | CREATE | Repository new-methods tests |
| `tests/runtime_v2/execution_gateway/test_ws_fill_watcher.py` | EXTEND | Add 3-stream + position stream tests |
| `tests/runtime_v2/execution_gateway/test_event_sync.py` | EXTEND | Update for new classifier path |

---

## Task 1: DB Migration — `exchange_raw_events`

**Files:**
- Create: `db/ops_migrations/006_ops_exchange_raw_events.sql`

- [ ] **Step 1.1: Write migration file**

```sql
-- db/ops_migrations/006_ops_exchange_raw_events.sql

CREATE TABLE IF NOT EXISTS exchange_raw_events (
    raw_event_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    exchange_event_id       TEXT NOT NULL,
    source_stream           TEXT NOT NULL,
    symbol                  TEXT NOT NULL,
    side                    TEXT NOT NULL,
    create_type             TEXT,
    stop_order_type         TEXT,
    exec_type               TEXT,
    order_status            TEXT,
    order_link_id           TEXT,
    order_id                TEXT,
    seq                     INTEGER,
    exec_price              REAL,
    exec_qty                REAL,
    closed_size             REAL,
    leaves_qty              REAL,
    pos_qty                 REAL,
    exec_value              REAL,
    exec_fee                REAL,
    fee_rate                REAL,
    cum_exec_qty            REAL,
    position_take_profit    REAL,
    position_stop_loss      REAL,
    classified_event_type   TEXT,
    classified_source       TEXT,
    trade_chain_id          INTEGER,
    tp_level                INTEGER,
    forwarded_to_lifecycle  INTEGER NOT NULL DEFAULT 0,
    forwarded_at            TEXT,
    raw_info_json           TEXT NOT NULL DEFAULT '{}',
    exchange_time           TEXT,
    received_at             TEXT NOT NULL,
    idempotency_key         TEXT NOT NULL,
    UNIQUE(idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_ere_chain_type
    ON exchange_raw_events (trade_chain_id, classified_event_type);

CREATE INDEX IF NOT EXISTS idx_ere_symbol_side
    ON exchange_raw_events (symbol, side, received_at DESC);

CREATE INDEX IF NOT EXISTS idx_ere_not_forwarded
    ON exchange_raw_events (forwarded_to_lifecycle)
    WHERE forwarded_to_lifecycle = 0;

CREATE INDEX IF NOT EXISTS idx_ere_stream
    ON exchange_raw_events (source_stream, received_at DESC);
```

- [ ] **Step 1.2: Verify migration applies cleanly**

```bash
python - <<'EOF'
import sqlite3
from pathlib import Path
conn = sqlite3.connect(":memory:")
for f in sorted(Path("db/ops_migrations").glob("*.sql")):
    conn.executescript(f.read_text(encoding="utf-8"))
tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
print([t[0] for t in tables])
assert any("exchange_raw_events" in t[0] for t in tables), "table missing!"
print("OK")
EOF
```

Expected output includes `exchange_raw_events`.

- [ ] **Step 1.3: Commit**

```bash
git add db/ops_migrations/006_ops_exchange_raw_events.sql
git commit -m "feat(db): add exchange_raw_events migration for audit trail"
```

---

## Task 2: Data Models (`event_ingest/models.py`)

**Files:**
- Create: `src/runtime_v2/execution_gateway/event_ingest/__init__.py`
- Create: `src/runtime_v2/execution_gateway/event_ingest/models.py`

- [ ] **Step 2.1: Create package marker**

```python
# src/runtime_v2/execution_gateway/event_ingest/__init__.py
```

(empty file)

- [ ] **Step 2.2: Write models**

```python
# src/runtime_v2/execution_gateway/event_ingest/models.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

SourceStream = Literal[
    "watch_my_trades",
    "watch_orders",
    "watch_positions",
    "fetch_my_trades",
    "fetch_open_orders",
    "fetch_positions",
]

EventSource = Literal[
    "bot_command",
    "exchange_auto",
    "exchange_manual",
    "reconciliation_inferred",
]

ExchangeEventType = Literal[
    "ENTRY_FILLED",
    "TP_FILLED",
    "SL_FILLED",
    "CLOSE_PARTIAL_FILLED",
    "CLOSE_FULL_FILLED",
    "MANUAL_CLOSE_PARTIAL",
    "MANUAL_CLOSE_FULL",
    "LIQUIDATION_FILLED",
    "PENDING_ENTRY_CANCELLED",
    "STANDALONE_PROTECTIVE_CANCELLED",
    "PROTECTIVE_ORDER_CANCELLED",
    "STOP_MOVED_CONFIRMED",
    "PROTECTIVE_ORDERS_SYNCED",
    "UNKNOWN",
]


@dataclass
class ExchangeRawEvent:
    source_stream:        SourceStream
    exchange_event_id:    str
    idempotency_key:      str
    symbol:               str
    side:                 str
    create_type:          str | None
    stop_order_type:      str | None
    exec_type:            str | None
    order_status:         str | None
    order_link_id:        str | None
    order_id:             str | None
    seq:                  int | None
    exec_price:           float | None
    exec_qty:             float | None
    closed_size:          float | None
    leaves_qty:           float | None
    pos_qty:              float | None
    exec_value:           float | None
    exec_fee:             float | None
    fee_rate:             float | None
    cum_exec_qty:         float | None
    position_take_profit: float | None = None
    position_stop_loss:   float | None = None
    exchange_time:        str | None = None
    received_at:          str = ""
    raw_info:             dict = field(default_factory=dict)


@dataclass
class ClassifiedEvent:
    raw:            ExchangeRawEvent
    event_type:     ExchangeEventType
    source:         EventSource
    trade_chain_id: int | None = None
    tp_level:       int | None = None
    is_actionable:  bool = True

    @property
    def should_forward_to_lifecycle(self) -> bool:
        return (
            self.is_actionable
            and self.trade_chain_id is not None
            and self.event_type != "UNKNOWN"
        )
```

- [ ] **Step 2.3: Smoke test models import**

```bash
python -c "
from src.runtime_v2.execution_gateway.event_ingest.models import (
    ExchangeRawEvent, ClassifiedEvent
)
raw = ExchangeRawEvent(
    source_stream='watch_my_trades', exchange_event_id='e1',
    idempotency_key='exec:e1', symbol='BTCUSDT', side='Sell',
    create_type='CreateByTakeProfit', stop_order_type='TakeProfit',
    exec_type='Trade', order_status=None, order_link_id=None,
    order_id=None, seq=None, exec_price=45000.0, exec_qty=0.01,
    closed_size=0.01, leaves_qty=0.0, pos_qty=0.0,
    exec_value=450.0, exec_fee=0.18, fee_rate=0.0004, cum_exec_qty=0.01,
)
c = ClassifiedEvent(raw=raw, event_type='TP_FILLED', source='exchange_auto',
                    trade_chain_id=None)
assert not c.should_forward_to_lifecycle
c.trade_chain_id = 42
assert c.should_forward_to_lifecycle
print('OK')
"
```

Expected: `OK`

- [ ] **Step 2.4: Commit**

```bash
git add src/runtime_v2/execution_gateway/event_ingest/
git commit -m "feat(event_ingest): add ExchangeRawEvent and ClassifiedEvent models"
```

---

## Task 3: `EventNormalizer`

**Files:**
- Create: `src/runtime_v2/execution_gateway/event_ingest/normalizer.py`
- Create: `tests/runtime_v2/execution_gateway/test_event_ingest_normalizer.py`

- [ ] **Step 3.1: Write failing tests**

```python
# tests/runtime_v2/execution_gateway/test_event_ingest_normalizer.py
from __future__ import annotations
import pytest


def test_from_trade_tp_position_level():
    """watchMyTrades TP fill: createType and stopOrderType extracted."""
    from src.runtime_v2.execution_gateway.event_ingest.normalizer import EventNormalizer
    trade = {
        "id": "exec-001",
        "symbol": "BTC/USDT:USDT",
        "side": "sell",
        "price": 45000.0,
        "amount": 0.01,
        "info": {
            "execId": "exec-001",
            "symbol": "BTCUSDT",
            "side": "Sell",
            "createType": "CreateByTakeProfit",
            "stopOrderType": "TakeProfit",
            "execType": "Trade",
            "closedSize": "0.01",
            "posQty": "0",
            "orderLinkId": "",
            "orderId": "ord-001",
            "seq": "12345",
            "execPrice": "45000",
            "execQty": "0.01",
            "execValue": "450",
            "execFee": "0.18",
            "feeRate": "0.0004",
            "cumExecQty": "0.01",
            "execTime": "1716800000000",
        },
    }
    n = EventNormalizer()
    raw = n.from_trade(trade)
    assert raw is not None
    assert raw.source_stream == "watch_my_trades"
    assert raw.symbol == "BTCUSDT"
    assert raw.side == "Sell"
    assert raw.create_type == "CreateByTakeProfit"
    assert raw.stop_order_type == "TakeProfit"
    assert raw.exec_type == "Trade"
    assert raw.closed_size == 0.01
    assert raw.pos_qty == 0.0
    assert raw.order_link_id == ""
    assert raw.seq == 12345
    assert raw.exec_price == 45000.0
    assert raw.exec_fee == 0.18
    assert raw.idempotency_key == "exec:exec-001"
    assert raw.exchange_time is not None


def test_from_trade_entry_with_order_link_id():
    """watchMyTrades entry fill: our clientOrderId present."""
    from src.runtime_v2.execution_gateway.event_ingest.normalizer import EventNormalizer
    trade = {
        "id": "exec-002",
        "symbol": "PHA/USDT:USDT",
        "side": "buy",
        "price": 0.15,
        "amount": 100.0,
        "info": {
            "execId": "exec-002",
            "symbol": "PHAUSDT",
            "side": "Buy",
            "createType": "CreateByUser",
            "stopOrderType": "",
            "execType": "Trade",
            "closedSize": "0",
            "posQty": "100",
            "orderLinkId": "tsb:10:5001:entry:1",
            "orderId": "ord-002",
            "seq": "99",
            "execPrice": "0.15",
            "execQty": "100",
            "execValue": "15",
            "execFee": "0.006",
            "feeRate": "0.0004",
            "cumExecQty": "100",
            "execTime": "1716800001000",
        },
    }
    n = EventNormalizer()
    raw = n.from_trade(trade)
    assert raw is not None
    assert raw.symbol == "PHAUSDT"
    assert raw.create_type == "CreateByUser"
    assert raw.stop_order_type == ""
    assert raw.closed_size == 0.0
    assert raw.order_link_id == "tsb:10:5001:entry:1"


def test_from_trade_returns_none_on_missing_id():
    """No execId → return None (skip gracefully)."""
    from src.runtime_v2.execution_gateway.event_ingest.normalizer import EventNormalizer
    n = EventNormalizer()
    assert n.from_trade({"symbol": "BTC/USDT:USDT", "side": "buy", "info": {}}) is None


def test_from_order_cancelled():
    """watchOrders cancelled entry order."""
    from src.runtime_v2.execution_gateway.event_ingest.normalizer import EventNormalizer
    order = {
        "id": "ord-cancel-1",
        "symbol": "BTC/USDT:USDT",
        "side": "buy",
        "status": "canceled",
        "average": None,
        "filled": 0.0,
        "clientOrderId": "tsb:42:999:entry:1",
        "info": {
            "orderId": "ord-cancel-1",
            "orderLinkId": "tsb:42:999:entry:1",
            "orderStatus": "Cancelled",
            "createType": "CreateByUser",
            "stopOrderType": "",
            "side": "Buy",
            "symbol": "BTCUSDT",
            "cumExecQty": "0",
            "leavesQty": "0",
            "updatedTime": "1716800002000",
        },
    }
    n = EventNormalizer()
    raw = n.from_order(order)
    assert raw is not None
    assert raw.source_stream == "watch_orders"
    assert raw.order_status == "Cancelled"
    assert raw.order_link_id == "tsb:42:999:entry:1"
    assert raw.idempotency_key == "order:ord-cancel-1:Cancelled"


def test_from_position_tp_removed():
    """watchPositions: takeProfit field set to 0."""
    from src.runtime_v2.execution_gateway.event_ingest.normalizer import EventNormalizer
    position = {
        "symbol": "BTC/USDT:USDT",
        "side": "long",
        "contracts": 0.01,
        "info": {
            "symbol": "BTCUSDT",
            "side": "Buy",
            "size": "0.01",
            "takeProfit": "0",
            "stopLoss": "42000",
            "seq": "55555",
            "updatedTime": "1716800003000",
        },
    }
    n = EventNormalizer()
    raw = n.from_position(position)
    assert raw is not None
    assert raw.source_stream == "watch_positions"
    assert raw.position_take_profit == 0.0
    assert raw.position_stop_loss == 42000.0
    assert raw.seq == 55555
    assert raw.idempotency_key == "pos:BTCUSDT:Buy:55555"


def test_from_rest_trade_different_idempotency():
    """from_rest_trade uses rest_exec: prefix to coexist with WS in DB."""
    from src.runtime_v2.execution_gateway.event_ingest.normalizer import EventNormalizer
    trade = {
        "id": "exec-003",
        "symbol": "BTC/USDT:USDT",
        "side": "sell",
        "price": 45000.0,
        "amount": 0.01,
        "info": {
            "execId": "exec-003",
            "symbol": "BTCUSDT",
            "side": "Sell",
            "createType": "CreateByTakeProfit",
            "stopOrderType": "TakeProfit",
            "execType": "Trade",
            "closedSize": "0.01",
            "posQty": "0",
            "orderLinkId": "",
            "orderId": "ord-003",
            "seq": "777",
            "execPrice": "45000",
            "execQty": "0.01",
            "execValue": "450",
            "execFee": "0.18",
            "feeRate": "0.0004",
            "cumExecQty": "0.01",
        },
    }
    n = EventNormalizer()
    raw = n.from_rest_trade(trade)
    assert raw is not None
    assert raw.source_stream == "fetch_my_trades"
    assert raw.idempotency_key == "rest_exec:exec-003"


def test_ccxt_symbol_conversion():
    """PHA/USDT:USDT → PHAUSDT, BTCUSDT stays BTCUSDT."""
    from src.runtime_v2.execution_gateway.event_ingest.normalizer import _ccxt_symbol_to_raw
    assert _ccxt_symbol_to_raw("PHA/USDT:USDT") == "PHAUSDT"
    assert _ccxt_symbol_to_raw("BTC/USDT:USDT") == "BTCUSDT"
    assert _ccxt_symbol_to_raw("BTCUSDT") == "BTCUSDT"
```

- [ ] **Step 3.2: Run tests — verify all FAIL**

```bash
pytest tests/runtime_v2/execution_gateway/test_event_ingest_normalizer.py -v
```

Expected: `ModuleNotFoundError: No module named 'src.runtime_v2.execution_gateway.event_ingest.normalizer'`

- [ ] **Step 3.3: Write normalizer**

```python
# src/runtime_v2/execution_gateway/event_ingest/normalizer.py
from __future__ import annotations

from datetime import datetime, timezone

from src.runtime_v2.execution_gateway.event_ingest.models import ExchangeRawEvent


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _f(val: object) -> float | None:
    try:
        return float(val) if val is not None else None  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _i(val: object) -> int | None:
    try:
        return int(val) if val is not None else None  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _s(val: object) -> str | None:
    return str(val) if val is not None else None


def _ccxt_symbol_to_raw(symbol: str) -> str:
    """'PHA/USDT:USDT' → 'PHAUSDT'"""
    if "/" not in symbol:
        return symbol
    base, rest = symbol.split("/", 1)
    return base + rest.split(":")[0]


class EventNormalizer:
    """Converts raw CCXT dicts to ExchangeRawEvent. Zero business logic, zero DB."""

    def from_trade(self, trade: dict) -> ExchangeRawEvent | None:
        """From watchMyTrades / fetchMyTrades."""
        info = trade.get("info") or {}
        exec_id = _s(trade.get("id") or info.get("execId"))
        if not exec_id:
            return None
        symbol = _ccxt_symbol_to_raw(trade.get("symbol") or info.get("symbol") or "")
        side = _s(trade.get("side") or info.get("side") or "")
        if not symbol or not side:
            return None

        exec_time_ms = info.get("execTime")
        exchange_time = (
            datetime.fromtimestamp(int(exec_time_ms) / 1000, tz=timezone.utc).isoformat()
            if exec_time_ms else None
        )

        return ExchangeRawEvent(
            source_stream     = "watch_my_trades",
            exchange_event_id = exec_id,
            idempotency_key   = f"exec:{exec_id}",
            symbol            = symbol,
            side              = side,
            create_type       = _s(info.get("createType")),
            stop_order_type   = _s(info.get("stopOrderType")),
            exec_type         = _s(info.get("execType") or trade.get("type")),
            order_status      = None,
            order_link_id     = _s(info.get("orderLinkId") or trade.get("clientOrderId")),
            order_id          = _s(info.get("orderId") or trade.get("order")),
            seq               = _i(info.get("seq")),
            exec_price        = _f(info.get("execPrice") or trade.get("price")),
            exec_qty          = _f(info.get("execQty") or trade.get("amount")),
            closed_size       = _f(info.get("closedSize")),
            leaves_qty        = _f(info.get("leavesQty")),
            pos_qty           = _f(info.get("posQty")),
            exec_value        = _f(info.get("execValue")),
            exec_fee          = _f(info.get("execFee") or (trade.get("fee") or {}).get("cost")),
            fee_rate          = _f(info.get("feeRate")),
            cum_exec_qty      = _f(info.get("cumExecQty")),
            exchange_time     = exchange_time,
            received_at       = _now(),
            raw_info          = dict(info),
        )

    def from_order(self, order: dict) -> ExchangeRawEvent | None:
        """From watchOrders / fetchOpenOrders."""
        info = order.get("info") or {}
        order_id = _s(order.get("id") or info.get("orderId"))
        if not order_id:
            return None
        order_status = _s(
            order.get("status") or info.get("orderStatus") or ""
        )
        # Normalise ccxt status strings: "canceled" → "Cancelled"
        if order_status and order_status.lower() == "canceled":
            order_status = "Cancelled"
        symbol = _ccxt_symbol_to_raw(order.get("symbol") or info.get("symbol") or "")
        side = _s(order.get("side") or info.get("side") or "")
        if not symbol or not side:
            return None

        updated_time_ms = info.get("updatedTime")
        exchange_time = (
            datetime.fromtimestamp(int(updated_time_ms) / 1000, tz=timezone.utc).isoformat()
            if updated_time_ms else None
        )

        return ExchangeRawEvent(
            source_stream     = "watch_orders",
            exchange_event_id = order_id,
            idempotency_key   = f"order:{order_id}:{order_status}",
            symbol            = symbol,
            side              = side,
            create_type       = _s(info.get("createType")),
            stop_order_type   = _s(info.get("stopOrderType")),
            exec_type         = None,
            order_status      = order_status,
            order_link_id     = _s(info.get("orderLinkId") or order.get("clientOrderId")),
            order_id          = order_id,
            seq               = None,
            exec_price        = _f(order.get("average") or info.get("avgPrice")),
            exec_qty          = _f(order.get("filled") or info.get("cumExecQty")),
            closed_size       = _f(info.get("closedSize")),
            leaves_qty        = _f(info.get("leavesQty")),
            pos_qty           = None,
            exec_value        = _f(info.get("cumExecValue")),
            exec_fee          = _f(info.get("cumExecFee")),
            fee_rate          = None,
            cum_exec_qty      = _f(info.get("cumExecQty")),
            exchange_time     = exchange_time,
            received_at       = _now(),
            raw_info          = dict(info),
        )

    def from_position(self, position: dict) -> ExchangeRawEvent | None:
        """From watchPositions. Detects TP/SL field changes."""
        info = position.get("info") or {}
        symbol = _ccxt_symbol_to_raw(
            position.get("symbol") or info.get("symbol") or ""
        )
        side = _s(info.get("side") or position.get("side") or "")
        if not symbol or not side:
            return None

        seq = _i(info.get("seq"))
        updated_time_ms = info.get("updatedTime")
        exchange_time = (
            datetime.fromtimestamp(int(updated_time_ms) / 1000, tz=timezone.utc).isoformat()
            if updated_time_ms else None
        )
        seq_key = seq if seq is not None else (updated_time_ms or _now())

        return ExchangeRawEvent(
            source_stream        = "watch_positions",
            exchange_event_id    = f"pos:{symbol}:{side}:{seq_key}",
            idempotency_key      = f"pos:{symbol}:{side}:{seq_key}",
            symbol               = symbol,
            side                 = side,
            create_type          = None,
            stop_order_type      = None,
            exec_type            = None,
            order_status         = _s(info.get("positionStatus")),
            order_link_id        = None,
            order_id             = None,
            seq                  = seq,
            exec_price           = None,
            exec_qty             = None,
            closed_size          = None,
            leaves_qty           = None,
            pos_qty              = _f(info.get("size") or position.get("contracts")),
            exec_value           = None,
            exec_fee             = None,
            fee_rate             = None,
            cum_exec_qty         = None,
            position_take_profit = _f(info.get("takeProfit")),
            position_stop_loss   = _f(info.get("stopLoss")),
            exchange_time        = exchange_time,
            received_at          = _now(),
            raw_info             = dict(info),
        )

    def from_rest_trade(self, trade: dict) -> ExchangeRawEvent | None:
        """From fetchMyTrades (REST). Same as from_trade, different source/key."""
        raw = self.from_trade(trade)
        if raw is None:
            return None
        raw.source_stream   = "fetch_my_trades"
        raw.idempotency_key = f"rest_exec:{raw.exchange_event_id}"
        return raw
```

- [ ] **Step 3.4: Run tests — verify all PASS**

```bash
pytest tests/runtime_v2/execution_gateway/test_event_ingest_normalizer.py -v
```

Expected: 7 tests PASSED.

- [ ] **Step 3.5: Commit**

```bash
git add src/runtime_v2/execution_gateway/event_ingest/normalizer.py \
        tests/runtime_v2/execution_gateway/test_event_ingest_normalizer.py
git commit -m "feat(event_ingest): add EventNormalizer with tests"
```

---

## Task 4: `EventClassifier`

**Files:**
- Create: `src/runtime_v2/execution_gateway/event_ingest/classifier.py`
- Create: `tests/runtime_v2/execution_gateway/test_event_ingest_classifier.py`

- [ ] **Step 4.1: Write failing tests**

```python
# tests/runtime_v2/execution_gateway/test_event_ingest_classifier.py
from __future__ import annotations
import pytest
from src.runtime_v2.execution_gateway.event_ingest.models import ExchangeRawEvent


def _raw(
    source_stream="watch_my_trades",
    exchange_event_id="e1",
    idempotency_key="exec:e1",
    symbol="BTCUSDT",
    side="Sell",
    create_type=None,
    stop_order_type=None,
    exec_type="Trade",
    order_status=None,
    order_link_id=None,
    order_id=None,
    seq=None,
    exec_price=45000.0,
    exec_qty=0.01,
    closed_size=0.01,
    leaves_qty=0.0,
    pos_qty=0.0,
    exec_value=450.0,
    exec_fee=0.18,
    fee_rate=0.0004,
    cum_exec_qty=0.01,
    position_take_profit=None,
    position_stop_loss=None,
) -> ExchangeRawEvent:
    return ExchangeRawEvent(
        source_stream=source_stream, exchange_event_id=exchange_event_id,
        idempotency_key=idempotency_key, symbol=symbol, side=side,
        create_type=create_type, stop_order_type=stop_order_type,
        exec_type=exec_type, order_status=order_status,
        order_link_id=order_link_id, order_id=order_id, seq=seq,
        exec_price=exec_price, exec_qty=exec_qty, closed_size=closed_size,
        leaves_qty=leaves_qty, pos_qty=pos_qty, exec_value=exec_value,
        exec_fee=exec_fee, fee_rate=fee_rate, cum_exec_qty=cum_exec_qty,
        position_take_profit=position_take_profit,
        position_stop_loss=position_stop_loss,
    )


def _classifier(known=None):
    from src.runtime_v2.execution_gateway.event_ingest.classifier import EventClassifier
    return EventClassifier(
        known_order_link_ids=known or {},
        open_chain_tp_prices={},
    )


# ── TP fill tests ─────────────────────────────────────────────────────────────

def test_tp_fill_position_level_deterministic():
    """createType=CreateByTakeProfit → TP_FILLED, source=exchange_auto."""
    raw = _raw(create_type="CreateByTakeProfit", stop_order_type="TakeProfit")
    c = _classifier().classify(raw)
    assert c.event_type == "TP_FILLED"
    assert c.source == "exchange_auto"
    assert c.trade_chain_id is None   # no orderLinkId → unresolved


def test_tp_fill_partial_create_type():
    """createType=CreateByPartialTakeProfit also → TP_FILLED."""
    raw = _raw(create_type="CreateByPartialTakeProfit", stop_order_type="PartialTakeProfit")
    c = _classifier().classify(raw)
    assert c.event_type == "TP_FILLED"
    assert c.source == "exchange_auto"


def test_tp_fill_bot_order_with_link_id():
    """createType=CreateByTakeProfit + known orderLinkId → TP_FILLED, bot_command resolved."""
    known = {"tsb:42:101:tp:2": (42, "tp", 2)}
    raw = _raw(
        create_type="CreateByTakeProfit",
        stop_order_type="TakeProfit",
        order_link_id="tsb:42:101:tp:2",
    )
    c = _classifier(known).classify(raw)
    assert c.event_type == "TP_FILLED"
    assert c.source == "exchange_auto"   # createType wins on source
    assert c.trade_chain_id == 42
    assert c.tp_level == 2


# ── SL fill tests ─────────────────────────────────────────────────────────────

def test_sl_fill_position_level_deterministic():
    """createType=CreateByStopLoss → SL_FILLED, exchange_auto."""
    raw = _raw(
        create_type="CreateByStopLoss", stop_order_type="StopLoss",
        side="Sell", closed_size=0.01,
    )
    c = _classifier().classify(raw)
    assert c.event_type == "SL_FILLED"
    assert c.source == "exchange_auto"


def test_sl_fill_bot_standalone():
    """Known orderLinkId with role=sl → SL_FILLED, bot_command."""
    known = {"tsb:10:50:sl:1": (10, "sl", 1)}
    raw = _raw(
        create_type="CreateByUser", stop_order_type="",
        order_link_id="tsb:10:50:sl:1", closed_size=0.01,
    )
    c = _classifier(known).classify(raw)
    assert c.event_type == "SL_FILLED"
    assert c.source == "bot_command"
    assert c.trade_chain_id == 10


# ── Entry fill tests ──────────────────────────────────────────────────────────

def test_entry_fill_bot():
    """Known orderLinkId role=entry, closedSize=0 → ENTRY_FILLED, bot_command."""
    known = {"tsb:7:200:entry:1": (7, "entry", 1)}
    raw = _raw(
        create_type="CreateByUser", stop_order_type="",
        order_link_id="tsb:7:200:entry:1",
        closed_size=0.0, pos_qty=0.01, side="Buy",
    )
    c = _classifier(known).classify(raw)
    assert c.event_type == "ENTRY_FILLED"
    assert c.source == "bot_command"
    assert c.trade_chain_id == 7


# ── Manual close tests ────────────────────────────────────────────────────────

def test_manual_close_full():
    """closedSize > 0, pos_qty == 0, no known orderLinkId → MANUAL_CLOSE_FULL."""
    raw = _raw(
        create_type="CreateByUser", stop_order_type="",
        order_link_id=None, closed_size=0.01, pos_qty=0.0,
    )
    c = _classifier().classify(raw)
    assert c.event_type == "MANUAL_CLOSE_FULL"
    assert c.source == "exchange_manual"


def test_manual_close_partial():
    """closedSize > 0, pos_qty > 0, no known orderLinkId → MANUAL_CLOSE_PARTIAL."""
    raw = _raw(
        create_type="CreateByUser", stop_order_type="",
        order_link_id=None, closed_size=0.005, pos_qty=0.005,
    )
    c = _classifier().classify(raw)
    assert c.event_type == "MANUAL_CLOSE_PARTIAL"
    assert c.source == "exchange_manual"


# ── Liquidation tests ─────────────────────────────────────────────────────────

def test_liquidation_filled():
    """createType=CreateByLiq → LIQUIDATION_FILLED."""
    raw = _raw(create_type="CreateByLiq", stop_order_type="", exec_type="BustTrade")
    c = _classifier().classify(raw)
    assert c.event_type == "LIQUIDATION_FILLED"
    assert c.source == "exchange_auto"


# ── Funding test ──────────────────────────────────────────────────────────────

def test_funding_not_actionable():
    """execType=Funding → UNKNOWN, not actionable."""
    raw = _raw(exec_type="Funding", create_type=None, closed_size=0.0)
    c = _classifier().classify(raw)
    assert c.event_type == "UNKNOWN"
    assert not c.is_actionable


# ── Order cancel tests ────────────────────────────────────────────────────────

def test_pending_entry_cancelled():
    """Cancelled order with known entry orderLinkId → PENDING_ENTRY_CANCELLED."""
    known = {"tsb:5:300:entry:1": (5, "entry", 1)}
    raw = _raw(
        source_stream="watch_orders",
        create_type="CreateByUser", stop_order_type="",
        order_status="Cancelled",
        order_link_id="tsb:5:300:entry:1",
        closed_size=0.0, exec_price=None, exec_qty=0.0,
    )
    c = _classifier(known).classify(raw)
    assert c.event_type == "PENDING_ENTRY_CANCELLED"
    assert c.source == "bot_command"
    assert c.trade_chain_id == 5


# ── Position update tests ─────────────────────────────────────────────────────

def test_protective_order_cancelled_tp_removed():
    """watchPositions takeProfit→0 → PROTECTIVE_ORDER_CANCELLED."""
    raw = _raw(
        source_stream="watch_positions",
        create_type=None, exec_type=None,
        closed_size=None, pos_qty=0.01,
        position_take_profit=0.0,
        position_stop_loss=42000.0,
        exec_price=None, exec_qty=None,
    )
    c = _classifier().classify(raw)
    assert c.event_type == "PROTECTIVE_ORDER_CANCELLED"
    assert c.source == "exchange_manual"


def test_position_update_no_tp_change_not_actionable():
    """watchPositions with non-zero TP/SL → UNKNOWN, not actionable."""
    raw = _raw(
        source_stream="watch_positions",
        create_type=None, exec_type=None,
        closed_size=None, pos_qty=0.01,
        position_take_profit=45000.0,
        position_stop_loss=42000.0,
        exec_price=None, exec_qty=None,
    )
    c = _classifier().classify(raw)
    assert c.event_type == "UNKNOWN"
    assert not c.is_actionable


# ── should_forward_to_lifecycle tests ────────────────────────────────────────

def test_should_forward_requires_chain_id():
    """Event with trade_chain_id=None is not forwarded to lifecycle."""
    raw = _raw(create_type="CreateByTakeProfit", stop_order_type="TakeProfit")
    c = _classifier().classify(raw)
    assert c.trade_chain_id is None
    assert not c.should_forward_to_lifecycle


def test_should_forward_with_chain_id():
    """Event with trade_chain_id set IS forwarded."""
    known = {"tsb:42:101:tp:1": (42, "tp", 1)}
    raw = _raw(
        create_type="CreateByTakeProfit",
        stop_order_type="TakeProfit",
        order_link_id="tsb:42:101:tp:1",
    )
    c = _classifier(known).classify(raw)
    assert c.trade_chain_id == 42
    assert c.should_forward_to_lifecycle
```

- [ ] **Step 4.2: Run tests — verify all FAIL**

```bash
pytest tests/runtime_v2/execution_gateway/test_event_ingest_classifier.py -v
```

Expected: `ModuleNotFoundError: No module named '...classifier'`

- [ ] **Step 4.3: Write classifier**

```python
# src/runtime_v2/execution_gateway/event_ingest/classifier.py
from __future__ import annotations

import logging

from src.runtime_v2.execution_gateway.event_ingest.models import (
    ClassifiedEvent,
    EventSource,
    ExchangeRawEvent,
)

logger = logging.getLogger(__name__)

_CREATE_TYPE_TP  = frozenset({"CreateByTakeProfit", "CreateByPartialTakeProfit"})
_CREATE_TYPE_SL  = frozenset({"CreateByStopLoss", "CreateByPartialStopLoss"})
_CREATE_TYPE_LIQ = frozenset({
    "CreateByLiq", "CreateByTakeOver_PassThrough", "CreateByAdl_PassThrough",
})
_STOP_TYPE_TP = frozenset({"TakeProfit", "PartialTakeProfit"})
_STOP_TYPE_SL = frozenset({"StopLoss", "PartialStopLoss"})

_ROLE_TO_EVENT: dict[str, str] = {
    "entry":        "ENTRY_FILLED",
    "sl":           "SL_FILLED",
    "tp":           "TP_FILLED",
    "exit_partial": "CLOSE_PARTIAL_FILLED",
    "exit_full":    "CLOSE_FULL_FILLED",
}


class EventClassifier:
    """
    Pure classifier. No I/O, no DB.
    known_order_link_ids: orderLinkId → (trade_chain_id, role, sequence)
    """

    def __init__(
        self,
        known_order_link_ids: dict[str, tuple[int, str, int]],
        open_chain_tp_prices: dict,  # reserved, unused
    ) -> None:
        self._known = known_order_link_ids

    def classify(self, raw: ExchangeRawEvent) -> ClassifiedEvent:
        if raw.source_stream == "watch_positions":
            return self._classify_position_update(raw)
        if raw.source_stream in ("watch_orders", "fetch_open_orders"):
            return self._classify_order_event(raw)
        return self._classify_execution(raw)

    # ── Execution (watchMyTrades / fetchMyTrades) ─────────────────────────────

    def _classify_execution(self, raw: ExchangeRawEvent) -> ClassifiedEvent:
        exec_type       = (raw.exec_type or "").strip()
        create_type     = (raw.create_type or "").strip()
        stop_order_type = (raw.stop_order_type or "").strip()
        closed_size     = raw.closed_size or 0.0
        order_link_id   = (raw.order_link_id or "").strip()

        # Funding — not actionable
        if exec_type == "Funding":
            return self._make(raw, "UNKNOWN", "exchange_auto", is_actionable=False)

        # Priority 1: deterministic raw fields
        if create_type in _CREATE_TYPE_LIQ or exec_type == "BustTrade":
            chain_id = self._chain_id(order_link_id)
            return self._make(raw, "LIQUIDATION_FILLED", "exchange_auto",
                              trade_chain_id=chain_id)

        if create_type in _CREATE_TYPE_TP or stop_order_type in _STOP_TYPE_TP:
            chain_id, tp_level = self._resolve_tp(raw, order_link_id)
            return self._make(raw, "TP_FILLED", "exchange_auto",
                              trade_chain_id=chain_id, tp_level=tp_level)

        if create_type in _CREATE_TYPE_SL or stop_order_type in _STOP_TYPE_SL:
            chain_id = self._chain_id(order_link_id)
            return self._make(raw, "SL_FILLED", "exchange_auto",
                              trade_chain_id=chain_id)

        # Priority 2: bot orderLinkId correlation
        if order_link_id and order_link_id in self._known:
            return self._classify_bot_execution(raw, order_link_id)

        # Priority 3: structural inference (manual)
        if closed_size > 0.0:
            pos_qty = raw.pos_qty or 0.0
            if pos_qty == 0.0:
                return self._make(raw, "MANUAL_CLOSE_FULL", "exchange_manual")
            return self._make(raw, "MANUAL_CLOSE_PARTIAL", "exchange_manual")

        logger.debug(
            "unclassified execution: symbol=%s create_type=%s link_id=%s",
            raw.symbol, create_type, order_link_id,
        )
        return self._make(raw, "UNKNOWN", "exchange_manual", is_actionable=False)

    def _classify_bot_execution(
        self, raw: ExchangeRawEvent, order_link_id: str
    ) -> ClassifiedEvent:
        chain_id, role, sequence = self._known[order_link_id]
        event_type = _ROLE_TO_EVENT.get(role)
        if event_type is None:
            logger.warning("unknown role '%s' in %s", role, order_link_id)
            return self._make(raw, "UNKNOWN", "bot_command",
                              trade_chain_id=chain_id, is_actionable=False)
        tp_level = sequence if role == "tp" else None
        return self._make(raw, event_type, "bot_command",  # type: ignore[arg-type]
                          trade_chain_id=chain_id, tp_level=tp_level)

    # ── Order events ──────────────────────────────────────────────────────────

    def _classify_order_event(self, raw: ExchangeRawEvent) -> ClassifiedEvent:
        order_status    = (raw.order_status or "").strip()
        order_link_id   = (raw.order_link_id or "").strip()
        stop_order_type = (raw.stop_order_type or "").strip()

        if order_status != "Cancelled":
            return self._make(raw, "UNKNOWN", "exchange_auto", is_actionable=False)

        chain_id = self._chain_id(order_link_id)

        if order_link_id and order_link_id in self._known:
            _, role, sequence = self._known[order_link_id]
            if role == "entry":
                return self._make(raw, "PENDING_ENTRY_CANCELLED", "bot_command",
                                  trade_chain_id=chain_id)
            if role in ("tp", "sl"):
                tp_level = sequence if role == "tp" else None
                return self._make(raw, "STANDALONE_PROTECTIVE_CANCELLED", "bot_command",
                                  trade_chain_id=chain_id, tp_level=tp_level)

        if stop_order_type in (_STOP_TYPE_TP | _STOP_TYPE_SL):
            return self._make(raw, "STANDALONE_PROTECTIVE_CANCELLED", "exchange_manual",
                              trade_chain_id=chain_id)

        return self._make(raw, "UNKNOWN", "exchange_manual", is_actionable=False)

    # ── Position update ───────────────────────────────────────────────────────

    def _classify_position_update(self, raw: ExchangeRawEvent) -> ClassifiedEvent:
        tp = raw.position_take_profit
        sl = raw.position_stop_loss

        if tp is None and sl is None:
            return self._make(raw, "UNKNOWN", "exchange_auto", is_actionable=False)

        if (tp is not None and tp == 0.0) or (sl is not None and sl == 0.0):
            return self._make(raw, "PROTECTIVE_ORDER_CANCELLED", "exchange_manual")

        return self._make(raw, "UNKNOWN", "exchange_auto", is_actionable=False)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _resolve_tp(
        self, raw: ExchangeRawEvent, order_link_id: str
    ) -> tuple[int | None, int | None]:
        if order_link_id and order_link_id in self._known:
            chain_id, role, sequence = self._known[order_link_id]
            if role == "tp":
                return chain_id, sequence
        return None, None

    def _chain_id(self, order_link_id: str) -> int | None:
        if order_link_id and order_link_id in self._known:
            return self._known[order_link_id][0]
        return None

    @staticmethod
    def _make(
        raw: ExchangeRawEvent,
        event_type: str,
        source: EventSource,
        *,
        trade_chain_id: int | None = None,
        tp_level: int | None = None,
        is_actionable: bool = True,
    ) -> ClassifiedEvent:
        return ClassifiedEvent(
            raw            = raw,
            event_type     = event_type,  # type: ignore[arg-type]
            source         = source,
            trade_chain_id = trade_chain_id,
            tp_level       = tp_level,
            is_actionable  = is_actionable,
        )
```

- [ ] **Step 4.4: Run tests — verify all PASS**

```bash
pytest tests/runtime_v2/execution_gateway/test_event_ingest_classifier.py -v
```

Expected: 15 tests PASSED.

- [ ] **Step 4.5: Commit**

```bash
git add src/runtime_v2/execution_gateway/event_ingest/classifier.py \
        tests/runtime_v2/execution_gateway/test_event_ingest_classifier.py
git commit -m "feat(event_ingest): add EventClassifier with deterministic Bybit field logic"
```

---

## Task 5: Repository Extensions

**Files:**
- Modify: `src/runtime_v2/execution_gateway/repositories.py`
- Create: `tests/runtime_v2/execution_gateway/test_repositories_raw.py`

- [ ] **Step 5.1: Write failing tests**

```python
# tests/runtime_v2/execution_gateway/test_repositories_raw.py
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
import datetime as dt
import pytest


def _apply_migrations(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    for f in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


@pytest.fixture
def ops_db(tmp_path):
    db = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db)
    return db


def _insert_chain(db_path, chain_id, symbol="BTCUSDT", side="LONG"):
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_trade_chains "
        "(trade_chain_id, source_enrichment_id, canonical_message_id, raw_message_id, "
        "trader_id, account_id, symbol, side, lifecycle_state, entry_mode, "
        "management_plan_json, open_position_qty, filled_entry_qty, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (chain_id, chain_id, chain_id, chain_id,
         "t1", "acc", symbol, side, "OPEN", "ONE_SHOT", "{}", 0.01, 0.01, now, now),
    )
    conn.commit()
    conn.close()


def _insert_cmd(db_path, cmd_id, chain_id, cmd_type, coid, status="SENT", payload=None):
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_execution_commands "
        "(command_id, trade_chain_id, command_type, status, payload_json, "
        "idempotency_key, client_order_id, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (cmd_id, chain_id, cmd_type, status,
         json.dumps(payload or {"sequence": 1}),
         f"idem:{cmd_id}", coid, now, now),
    )
    conn.commit()
    conn.close()


def _make_classified(chain_id=42, event_type="TP_FILLED", source="exchange_auto",
                     tp_level=1, trade_chain_id=None):
    from src.runtime_v2.execution_gateway.event_ingest.models import (
        ClassifiedEvent, ExchangeRawEvent,
    )
    raw = ExchangeRawEvent(
        source_stream="watch_my_trades", exchange_event_id="exec-test-1",
        idempotency_key="exec:exec-test-1",
        symbol="BTCUSDT", side="Sell",
        create_type="CreateByTakeProfit", stop_order_type="TakeProfit",
        exec_type="Trade", order_status=None, order_link_id=None,
        order_id=None, seq=None,
        exec_price=45000.0, exec_qty=0.01, closed_size=0.01,
        leaves_qty=0.0, pos_qty=0.0, exec_value=450.0,
        exec_fee=0.18, fee_rate=0.0004, cum_exec_qty=0.01,
        received_at="2026-01-01T00:00:00+00:00",
    )
    return ClassifiedEvent(
        raw=raw,
        event_type=event_type,
        source=source,
        trade_chain_id=trade_chain_id if trade_chain_id is not None else chain_id,
        tp_level=tp_level,
        is_actionable=True,
    )


def test_insert_raw_and_classified_writes_both_tables(ops_db):
    """insert_raw_and_classified writes exchange_raw_events and ops_exchange_events."""
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository
    repo = GatewayCommandRepository(ops_db)
    _insert_chain(ops_db, 42)

    classified = _make_classified(chain_id=42, event_type="TP_FILLED", tp_level=1)
    inserted = repo.insert_raw_and_classified(classified)
    assert inserted is True

    conn = sqlite3.connect(ops_db)
    raw_rows = conn.execute(
        "SELECT exchange_event_id, classified_event_type, forwarded_to_lifecycle "
        "FROM exchange_raw_events"
    ).fetchall()
    ops_rows = conn.execute(
        "SELECT event_type FROM ops_exchange_events WHERE trade_chain_id=42"
    ).fetchall()
    conn.close()

    assert len(raw_rows) == 1
    assert raw_rows[0][1] == "TP_FILLED"
    assert raw_rows[0][2] == 1   # forwarded
    assert len(ops_rows) == 1
    assert ops_rows[0][0] == "TP_FILLED"


def test_insert_raw_and_classified_idempotent(ops_db):
    """Second call with same idempotency_key is a no-op, returns False."""
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository
    repo = GatewayCommandRepository(ops_db)
    _insert_chain(ops_db, 42)

    c = _make_classified(42)
    assert repo.insert_raw_and_classified(c) is True
    assert repo.insert_raw_and_classified(c) is False  # duplicate


def test_insert_raw_no_chain_id_not_forwarded(ops_db):
    """trade_chain_id=None: raw written, ops NOT written, returns False."""
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository
    repo = GatewayCommandRepository(ops_db)

    c = _make_classified(chain_id=None, trade_chain_id=None)
    inserted = repo.insert_raw_and_classified(c)
    assert inserted is False

    conn = sqlite3.connect(ops_db)
    raw_count = conn.execute("SELECT COUNT(*) FROM exchange_raw_events").fetchone()[0]
    ops_count = conn.execute("SELECT COUNT(*) FROM ops_exchange_events").fetchone()[0]
    conn.close()
    assert raw_count == 1
    assert ops_count == 0


def test_get_known_order_link_ids(ops_db):
    """Returns dict of clientOrderId → (chain_id, role, sequence)."""
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository
    _insert_chain(ops_db, 10)
    _insert_cmd(ops_db, 1, 10, "PLACE_ENTRY", "tsb:10:1:entry:1",
                payload={"sequence": 1})
    _insert_cmd(ops_db, 2, 10, "SET_POSITION_TPSL_PARTIAL", "tsb:10:2:tp:1",
                payload={"tp_sequence": 1, "take_profit": 45000.0})

    repo = GatewayCommandRepository(ops_db)
    known = repo.get_known_order_link_ids()

    assert "tsb:10:1:entry:1" in known
    assert known["tsb:10:1:entry:1"] == (10, "entry", 1)
    assert "tsb:10:2:tp:1" in known
    assert known["tsb:10:2:tp:1"][1] == "tp"


def test_tp_fill_exists(ops_db):
    """tp_fill_exists returns True only after TP_FILLED event written."""
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository
    repo = GatewayCommandRepository(ops_db)
    _insert_chain(ops_db, 42)
    assert not repo.tp_fill_exists(42, 1)

    c = _make_classified(42, event_type="TP_FILLED", tp_level=1)
    repo.insert_raw_and_classified(c)
    assert repo.tp_fill_exists(42, 1)
    assert not repo.tp_fill_exists(42, 2)   # different level


def test_protective_cancelled_exists(ops_db):
    """protective_cancelled_exists returns True after PROTECTIVE_ORDER_CANCELLED."""
    from src.runtime_v2.execution_gateway.event_ingest.models import (
        ClassifiedEvent, ExchangeRawEvent,
    )
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository
    repo = GatewayCommandRepository(ops_db)
    _insert_chain(ops_db, 42)
    assert not repo.protective_cancelled_exists(42, 1)

    raw = ExchangeRawEvent(
        source_stream="watch_positions", exchange_event_id="pos:BTCUSDT:Buy:99",
        idempotency_key="pos:BTCUSDT:Buy:99",
        symbol="BTCUSDT", side="Buy",
        create_type=None, stop_order_type=None, exec_type=None,
        order_status=None, order_link_id=None, order_id=None, seq=99,
        exec_price=None, exec_qty=None, closed_size=None,
        leaves_qty=None, pos_qty=0.01, exec_value=None, exec_fee=None,
        fee_rate=None, cum_exec_qty=None,
        position_take_profit=0.0,
        received_at="2026-01-01T00:00:00+00:00",
        raw_info={"expected_tp": 45000.0, "tp_level": 1},
    )
    c = ClassifiedEvent(
        raw=raw, event_type="PROTECTIVE_ORDER_CANCELLED",
        source="exchange_manual", trade_chain_id=42, tp_level=1,
    )
    repo.insert_raw_and_classified(c)
    assert repo.protective_cancelled_exists(42, 1)
```

- [ ] **Step 5.2: Run tests — verify all FAIL**

```bash
pytest tests/runtime_v2/execution_gateway/test_repositories_raw.py -v
```

Expected: `AttributeError: 'GatewayCommandRepository' object has no attribute 'insert_raw_and_classified'`

- [ ] **Step 5.3: Add 5 new methods to `repositories.py`**

Append these methods to the `GatewayCommandRepository` class in `src/runtime_v2/execution_gateway/repositories.py`. No new imports needed at file top — the methods use inline imports to avoid circular dependency risks.

```python
    # ── New methods for exchange-centric event system ─────────────────────────

    def insert_raw_and_classified(self, classified: "ClassifiedEvent") -> bool:
        """
        Atomically writes exchange_raw_events + ops_exchange_events.
        INSERT OR IGNORE on both — idempotent.
        Returns True if ops_exchange_events row was inserted.
        """
        from src.runtime_v2.execution_gateway.event_ingest.models import ClassifiedEvent as CE
        import json as _json

        raw = classified.raw
        now = _now()
        raw.received_at = raw.received_at or now

        conn = sqlite3.connect(self._db)
        ops_inserted = False
        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO exchange_raw_events (
                    exchange_event_id, source_stream, symbol, side,
                    create_type, stop_order_type, exec_type, order_status,
                    order_link_id, order_id, seq,
                    exec_price, exec_qty, closed_size, leaves_qty, pos_qty,
                    exec_value, exec_fee, fee_rate, cum_exec_qty,
                    position_take_profit, position_stop_loss,
                    classified_event_type, classified_source,
                    trade_chain_id, tp_level,
                    forwarded_to_lifecycle,
                    raw_info_json, exchange_time, received_at, idempotency_key
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    raw.exchange_event_id,   raw.source_stream,
                    raw.symbol,              raw.side,
                    raw.create_type,         raw.stop_order_type,
                    raw.exec_type,           raw.order_status,
                    raw.order_link_id,       raw.order_id,
                    raw.seq,
                    raw.exec_price,          raw.exec_qty,
                    raw.closed_size,         raw.leaves_qty,
                    raw.pos_qty,             raw.exec_value,
                    raw.exec_fee,            raw.fee_rate,
                    raw.cum_exec_qty,
                    raw.position_take_profit, raw.position_stop_loss,
                    classified.event_type,   classified.source,
                    classified.trade_chain_id, classified.tp_level,
                    0,
                    _json.dumps(raw.raw_info),
                    raw.exchange_time,       raw.received_at,
                    raw.idempotency_key,
                ),
            )

            if classified.should_forward_to_lifecycle:
                payload = _build_lifecycle_payload(classified)
                ops_key = _lifecycle_idempotency_key(classified)
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO ops_exchange_events (
                        trade_chain_id, event_type, payload_json,
                        processing_status, idempotency_key, received_at
                    ) VALUES (?,?,?,?,?,?)
                    """,
                    (
                        classified.trade_chain_id,
                        classified.event_type,
                        _json.dumps(payload),
                        "NEW",
                        ops_key,
                        now,
                    ),
                )
                ops_inserted = cursor.rowcount > 0
                if ops_inserted:
                    conn.execute(
                        "UPDATE exchange_raw_events "
                        "SET forwarded_to_lifecycle=1, forwarded_at=? "
                        "WHERE idempotency_key=?",
                        (now, raw.idempotency_key),
                    )

            conn.commit()
        finally:
            conn.close()

        return ops_inserted

    def get_known_order_link_ids(self) -> dict[str, tuple[int, str, int]]:
        """Returns clientOrderId → (trade_chain_id, role, sequence) for active commands."""
        _ROLE_MAP_LOCAL: dict[str, str] = {
            "PLACE_ENTRY":               "entry",
            "MOVE_STOP_TO_BREAKEVEN":    "sl",
            "MOVE_STOP":                 "sl",
            "MOVE_POSITION_STOP":        "sl",
            "CANCEL_PENDING_ENTRY":      "entry",
            "CLOSE_PARTIAL":             "exit_partial",
            "CLOSE_FULL":                "exit_full",
            "SET_POSITION_TPSL_PARTIAL": "tp",
            "SET_POSITION_TPSL_FULL":    "tp",
            "REBUILD_PARTIAL_TPS":       "tp",
        }
        conn = sqlite3.connect(self._db)
        try:
            rows = conn.execute(
                "SELECT client_order_id, trade_chain_id, command_type, payload_json "
                "FROM ops_execution_commands "
                "WHERE status IN ('SENT','ACK','DONE') "
                "AND client_order_id IS NOT NULL"
            ).fetchall()
        finally:
            conn.close()

        result: dict[str, tuple[int, str, int]] = {}
        for coid, chain_id, cmd_type, payload_json in rows:
            role = _ROLE_MAP_LOCAL.get(cmd_type, "entry")
            sequence = 1
            try:
                p = json.loads(payload_json or "{}")
                sequence = int(p.get("sequence") or p.get("tp_sequence") or 1)
            except Exception:
                pass
            result[coid] = (int(chain_id), role, sequence)
        return result

    def get_open_chains_with_tps(self) -> list[dict]:
        """Open chains with active TP commands — used by trade-based reconciliation."""
        conn = sqlite3.connect(self._db)
        try:
            rows = conn.execute(
                "SELECT c.command_id, c.trade_chain_id, c.command_type, "
                "c.payload_json, t.symbol, t.side, t.created_at "
                "FROM ops_execution_commands c "
                "JOIN ops_trade_chains t ON c.trade_chain_id = t.trade_chain_id "
                "WHERE c.command_type IN "
                "('SET_POSITION_TPSL_PARTIAL','REBUILD_PARTIAL_TPS') "
                "AND c.status IN ('SENT','DONE') "
                "AND t.lifecycle_state IN ('OPEN','PARTIALLY_CLOSED')"
            ).fetchall()
        finally:
            conn.close()

        result = []
        for cmd_id, chain_id, cmd_type, payload_json, symbol, side, opened_at in rows:
            try:
                payload = json.loads(payload_json or "{}")
            except Exception:
                continue
            for entry in self._expand_active_tp_payload(cmd_type, payload):
                result.append({
                    "cmd_id":          cmd_id,
                    "chain_id":        int(chain_id),
                    "tp_level":        int(entry.get("tp_sequence", 1)),
                    "tp_price":        float(entry.get("take_profit", 0)),
                    "symbol":          symbol,
                    "side":            side,
                    "opened_at":       opened_at or "",
                    "latest_tp_level": int(entry.get("tp_sequence", 1)),
                })
        return result

    def tp_fill_exists(self, chain_id: int, tp_level: int) -> bool:
        """True if TP_FILLED event already exists for this chain+level in ops_exchange_events."""
        conn = sqlite3.connect(self._db)
        try:
            rows = conn.execute(
                "SELECT payload_json FROM ops_exchange_events "
                "WHERE trade_chain_id=? AND event_type='TP_FILLED'",
                (chain_id,),
            ).fetchall()
            for (pj,) in rows:
                try:
                    if json.loads(pj).get("tp_level") == tp_level:
                        return True
                except Exception:
                    pass
            return False
        finally:
            conn.close()

    def protective_cancelled_exists(self, chain_id: int, tp_level: int) -> bool:
        """True if PROTECTIVE_ORDER_CANCELLED already exists for this chain+level."""
        conn = sqlite3.connect(self._db)
        try:
            rows = conn.execute(
                "SELECT payload_json FROM ops_exchange_events "
                "WHERE trade_chain_id=? AND event_type='PROTECTIVE_ORDER_CANCELLED'",
                (chain_id,),
            ).fetchall()
            for (pj,) in rows:
                try:
                    if json.loads(pj).get("tp_level") == tp_level:
                        return True
                except Exception:
                    pass
            return False
        finally:
            conn.close()
```

Also add these two module-level helper functions at the **bottom** of `repositories.py` (outside the class):

```python
def _build_lifecycle_payload(classified: "ClassifiedEvent") -> dict:
    """Build payload dict for ops_exchange_events compatible with event_processor.py."""
    import json as _j
    raw = classified.raw
    base: dict = {
        "fill_price": raw.exec_price,
        "filled_qty": raw.exec_qty or raw.closed_size,
        "source":     classified.source,
    }
    if classified.event_type == "TP_FILLED":
        base["tp_level"] = classified.tp_level or 1
        base["is_final"] = (raw.pos_qty == 0.0) if raw.pos_qty is not None else False
    if classified.event_type == "PROTECTIVE_ORDER_CANCELLED":
        base["tp_level"]    = classified.tp_level
        base["expected_tp"] = raw.raw_info.get("expected_tp")
        base["fill_price"]  = None
        base["filled_qty"]  = None
    return base


def _lifecycle_idempotency_key(classified: "ClassifiedEvent") -> str:
    raw = classified.raw
    if classified.event_type == "TP_FILLED":
        return f"TP_FILLED:{classified.trade_chain_id}:level:{classified.tp_level}"
    if classified.event_type == "PROTECTIVE_ORDER_CANCELLED":
        return f"PROTECTIVE_ORDER_CANCELLED:{classified.trade_chain_id}:tp:{classified.tp_level}"
    exchange_id = raw.order_id or raw.exchange_event_id
    return f"{classified.event_type}:{classified.trade_chain_id}:{exchange_id}"
```

- [ ] **Step 5.4: Run tests — verify all PASS**

```bash
pytest tests/runtime_v2/execution_gateway/test_repositories_raw.py -v
```

Expected: 6 tests PASSED.

- [ ] **Step 5.5: Run full existing gateway tests — no regressions**

```bash
pytest tests/runtime_v2/execution_gateway/ -v --ignore=tests/runtime_v2/execution_gateway/test_event_ingest_normalizer.py --ignore=tests/runtime_v2/execution_gateway/test_event_ingest_classifier.py --ignore=tests/runtime_v2/execution_gateway/test_repositories_raw.py
```

Expected: all previously passing tests still PASS.

- [ ] **Step 5.6: Commit**

```bash
git add src/runtime_v2/execution_gateway/repositories.py \
        tests/runtime_v2/execution_gateway/test_repositories_raw.py
git commit -m "feat(repositories): add insert_raw_and_classified and 4 support methods"
```

---

## Task 6: Refactor `BybitWsFillWatcher`

**Files:**
- Modify: `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/ws_fill_watcher.py`
- Modify: `tests/runtime_v2/execution_gateway/test_ws_fill_watcher.py`

- [ ] **Step 6.1: Write new tests for 3-stream behaviour**

Add these test functions to `tests/runtime_v2/execution_gateway/test_ws_fill_watcher.py`:

```python
# Add to existing test_ws_fill_watcher.py

def test_process_batch_trade_tp_position_level(tmp_path):
    """_process_batch with a watchMyTrades TP fill writes TP_FILLED via classifier."""
    import datetime as dt
    from unittest.mock import MagicMock, patch
    from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.ws_fill_watcher import (
        BybitWsFillWatcher,
    )
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    db = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db)
    _insert_open_chain(db, chain_id=42, symbol="BTCUSDT", side="LONG")

    repo = GatewayCommandRepository(db)
    watcher = BybitWsFillWatcher(
        api_key="k", api_secret="s", testnet=True,
        ops_db_path=db, repo=repo,
    )

    trade = {
        "id": "exec-tp-001",
        "symbol": "BTC/USDT:USDT",
        "side": "sell",
        "price": 45000.0,
        "amount": 0.01,
        "info": {
            "execId": "exec-tp-001",
            "symbol": "BTCUSDT",
            "side": "Sell",
            "createType": "CreateByTakeProfit",
            "stopOrderType": "TakeProfit",
            "execType": "Trade",
            "closedSize": "0.01",
            "posQty": "0",
            "orderLinkId": "",
            "orderId": "ord-tp-001",
            "seq": "1001",
            "execPrice": "45000",
            "execQty": "0.01",
            "execValue": "450",
            "execFee": "0.18",
            "feeRate": "0.0004",
            "cumExecQty": "0.01",
        },
    }
    from src.runtime_v2.execution_gateway.event_ingest.normalizer import EventNormalizer
    watcher._process_batch([trade], watcher._normalizer.from_trade)

    conn = sqlite3.connect(db)
    raw_rows = conn.execute(
        "SELECT classified_event_type, classified_source, create_type "
        "FROM exchange_raw_events"
    ).fetchall()
    conn.close()

    assert len(raw_rows) == 1
    assert raw_rows[0][0] == "TP_FILLED"
    assert raw_rows[0][1] == "exchange_auto"
    assert raw_rows[0][2] == "CreateByTakeProfit"


def test_process_batch_position_tp_removed(tmp_path):
    """_process_batch with watchPositions TP→0 writes PROTECTIVE_ORDER_CANCELLED."""
    from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.ws_fill_watcher import (
        BybitWsFillWatcher,
    )
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    db = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db)
    _insert_open_chain(db, chain_id=55, symbol="BTCUSDT", side="LONG")

    repo = GatewayCommandRepository(db)
    watcher = BybitWsFillWatcher(
        api_key="k", api_secret="s", testnet=True,
        ops_db_path=db, repo=repo,
    )

    position = {
        "symbol": "BTC/USDT:USDT",
        "side": "long",
        "contracts": 0.01,
        "info": {
            "symbol": "BTCUSDT",
            "side": "Buy",
            "size": "0.01",
            "takeProfit": "0",
            "stopLoss": "42000",
            "seq": "9999",
            "updatedTime": "1716800003000",
        },
    }
    watcher._process_batch([position], watcher._normalizer.from_position)

    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT classified_event_type, classified_source "
        "FROM exchange_raw_events LIMIT 1"
    ).fetchone()
    conn.close()

    assert row is not None
    assert row[0] == "PROTECTIVE_ORDER_CANCELLED"
    assert row[1] == "exchange_manual"


def test_process_batch_skips_none_from_normalizer(tmp_path):
    """Items that normalizer returns None for are silently skipped."""
    from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.ws_fill_watcher import (
        BybitWsFillWatcher,
    )
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    db = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db)
    repo = GatewayCommandRepository(db)
    watcher = BybitWsFillWatcher(
        api_key="k", api_secret="s", testnet=True,
        ops_db_path=db, repo=repo,
    )
    # Empty info dict → normalizer returns None
    bad_trade = {"symbol": "BTC/USDT:USDT", "side": "buy", "info": {}}
    watcher._process_batch([bad_trade], watcher._normalizer.from_trade)

    conn = sqlite3.connect(db)
    count = conn.execute("SELECT COUNT(*) FROM exchange_raw_events").fetchone()[0]
    conn.close()
    assert count == 0
```

- [ ] **Step 6.2: Run new tests — verify FAIL**

```bash
pytest tests/runtime_v2/execution_gateway/test_ws_fill_watcher.py::test_process_batch_trade_tp_position_level tests/runtime_v2/execution_gateway/test_ws_fill_watcher.py::test_process_batch_position_tp_removed tests/runtime_v2/execution_gateway/test_ws_fill_watcher.py::test_process_batch_skips_none_from_normalizer -v
```

Expected: `AttributeError: 'BybitWsFillWatcher' object has no attribute '_normalizer'` or similar.

- [ ] **Step 6.3: Replace `ws_fill_watcher.py`**

Replace entire file content:

```python
# src/runtime_v2/execution_gateway/adapters/ccxt_bybit/ws_fill_watcher.py
from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable

try:
    import ccxt.pro as ccxtpro
except ModuleNotFoundError:
    ccxtpro = None

from src.runtime_v2.execution_gateway.event_ingest.classifier import EventClassifier
from src.runtime_v2.execution_gateway.event_ingest.normalizer import EventNormalizer
from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

logger = logging.getLogger(__name__)


class BybitWsFillWatcher:
    """
    3 WebSocket streams in parallel.
    Single responsibility: receive events, normalize, classify, persist.
    Zero business logic, zero price matching.
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        testnet: bool,
        ops_db_path: str,
        repo: GatewayCommandRepository,
        reconciliation_callback: Callable | None = None,
        mode: str = "live",
        wake_callback: Callable[[], None] | None = None,
    ) -> None:
        self._api_key    = api_key
        self._api_secret = api_secret
        self._testnet    = testnet
        self._mode       = mode
        self._ops_db     = ops_db_path
        self._repo       = repo
        self._reconciliation_callback = reconciliation_callback
        self._wake_callback           = wake_callback
        self._normalizer              = EventNormalizer()

        self._stop_event  = threading.Event()
        self._loop_ready  = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop:   asyncio.AbstractEventLoop | None = None
        self._tasks:  list[asyncio.Task] = []

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_in_thread,
            name="bybit-ws-fill-watcher",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._loop_ready.wait(timeout=2)
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(self._cancel_all_tasks)
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None

    def _run_in_thread(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        self._tasks = [
            loop.create_task(self._watch_trades_forever()),
            loop.create_task(self._watch_orders_forever()),
            loop.create_task(self._watch_positions_forever()),
        ]
        self._loop_ready.set()
        try:
            loop.run_until_complete(
                asyncio.gather(*self._tasks, return_exceptions=True)
            )
        except asyncio.CancelledError:
            pass
        finally:
            pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
            for t in pending:
                t.cancel()
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            self._tasks = []
            self._loop  = None
            loop.close()

    def _cancel_all_tasks(self) -> None:
        for task in self._tasks:
            if not task.done():
                task.cancel()

    # ── Stream 1: watch_my_trades ─────────────────────────────────────────────

    async def _watch_trades_forever(self) -> None:
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
                    logger.exception("watch_my_trades failed")
                    await self._run_reconciliation_callback()
                    await asyncio.sleep(5)
                    continue
                self._process_batch(trades, self._normalizer.from_trade)
        finally:
            await exchange.close()

    # ── Stream 2: watch_orders ────────────────────────────────────────────────

    async def _watch_orders_forever(self) -> None:
        exchange = self._build_exchange()
        try:
            while not self._stop_event.is_set():
                try:
                    orders = await exchange.watch_orders()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    if self._stop_event.is_set():
                        break
                    logger.exception("watch_orders failed")
                    await asyncio.sleep(5)
                    continue
                self._process_batch(orders, self._normalizer.from_order)
        finally:
            await exchange.close()

    # ── Stream 3: watch_positions ─────────────────────────────────────────────

    async def _watch_positions_forever(self) -> None:
        exchange = self._build_exchange()
        try:
            while not self._stop_event.is_set():
                try:
                    positions = await exchange.watch_positions()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    if self._stop_event.is_set():
                        break
                    logger.exception("watch_positions failed")
                    await asyncio.sleep(5)
                    continue
                self._process_batch(positions, self._normalizer.from_position)
        finally:
            await exchange.close()

    # ── Core processing pipeline ──────────────────────────────────────────────

    def _process_batch(self, items: list[dict] | None, normalize_fn) -> None:
        if not items:
            return
        known = self._repo.get_known_order_link_ids()
        classifier = EventClassifier(
            known_order_link_ids=known,
            open_chain_tp_prices={},
        )
        for item in items:
            try:
                raw = normalize_fn(item)
                if raw is None:
                    continue
                classified = classifier.classify(raw)
                inserted = self._repo.insert_raw_and_classified(classified)
                if inserted and self._wake_callback:
                    self._wake_callback()
            except Exception:
                logger.exception("error in _process_batch")

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _run_reconciliation_callback(self) -> None:
        if self._reconciliation_callback is None:
            return
        result = self._reconciliation_callback()
        if asyncio.iscoroutine(result):
            await result

    def _build_exchange(self):
        if ccxtpro is None:
            raise RuntimeError("ccxt.pro is not installed")
        exchange = ccxtpro.bybit({
            "apiKey": self._api_key,
            "secret": self._api_secret,
            "options": {"defaultType": "linear"},
        })
        if self._mode == "demo":
            exchange.enable_demo_trading(True)
        elif self._testnet:
            exchange.set_sandbox_mode(True)
        return exchange


__all__ = ["BybitWsFillWatcher"]
```

- [ ] **Step 6.4: Run all ws_fill_watcher tests**

```bash
pytest tests/runtime_v2/execution_gateway/test_ws_fill_watcher.py -v
```

Expected: all tests PASS (old tests adapted, new 3 tests PASS).

> **Note:** Some existing tests that relied on `_match_and_save_tp_fill`, `_process_order_batch`, `_save_fill` will need updating — those private methods no longer exist. Replace them with tests that call `_process_batch` with the appropriate normalizer function (as shown in the new tests above). Remove any test that tested the ±1% price matching logic directly.

- [ ] **Step 6.5: Commit**

```bash
git add src/runtime_v2/execution_gateway/adapters/ccxt_bybit/ws_fill_watcher.py \
        tests/runtime_v2/execution_gateway/test_ws_fill_watcher.py
git commit -m "refactor(ws_fill_watcher): 3-stream WS using EventNormalizer+EventClassifier, remove price matching"
```

---

## Task 7: Refactor `ExchangeEventSyncWorker`

**Files:**
- Modify: `src/runtime_v2/execution_gateway/event_sync.py`
- Modify: `tests/runtime_v2/execution_gateway/test_event_sync.py`

- [ ] **Step 7.1: Replace `event_sync.py`**

Replace entire file content:

```python
# src/runtime_v2/execution_gateway/event_sync.py
from __future__ import annotations

import logging

from src.runtime_v2.execution_gateway.adapters.base import ExecutionAdapter
from src.runtime_v2.execution_gateway.event_ingest.classifier import EventClassifier
from src.runtime_v2.execution_gateway.event_ingest.models import (
    ClassifiedEvent,
    ExchangeRawEvent,
)
from src.runtime_v2.execution_gateway.event_ingest.normalizer import EventNormalizer
from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

logger = logging.getLogger(__name__)


class ExchangeEventSyncWorker:
    """
    REST safety net. Recovers events missed by WS.
    Uses same EventNormalizer + EventClassifier as the WS path.
    """

    def __init__(
        self,
        ops_db_path: str,
        adapter: ExecutionAdapter,
        repo: GatewayCommandRepository,
        execution_account_id: str,
    ) -> None:
        self._ops_db     = ops_db_path
        self._adapter    = adapter
        self._repo       = repo
        self._account_id = execution_account_id
        self._normalizer = EventNormalizer()

    def _make_classifier(self) -> EventClassifier:
        return EventClassifier(
            known_order_link_ids=self._repo.get_known_order_link_ids(),
            open_chain_tp_prices={},
        )

    def run_once(self) -> int:
        return self.run_reconciliation()

    # ── 1. Orders SENT/ACK not yet confirmed ─────────────────────────────────

    def run_reconciliation(self) -> int:
        """Poll orders with active clientOrderId. Recovery for fills/cancels missed by WS."""
        active     = self._repo.get_sent_or_ack()
        classifier = self._make_classifier()
        processed  = 0

        for cmd, client_order_id in active:
            if not client_order_id:
                continue
            try:
                raw_adapter = self._adapter.get_order_status(
                    client_order_id=client_order_id,
                    execution_account_id=self._account_id,
                )
                if raw_adapter is None:
                    continue

                order_dict = _adapter_order_to_ccxt_dict(raw_adapter, client_order_id)
                raw = self._normalizer.from_order(order_dict)
                if raw is None:
                    continue
                raw.source_stream   = "fetch_open_orders"
                raw.idempotency_key = f"rest_order:{raw.exchange_event_id}:{raw.order_status}"

                classified = classifier.classify(raw)
                inserted   = self._repo.insert_raw_and_classified(classified)
                if inserted and classified.should_forward_to_lifecycle:
                    self._repo.mark_done(cmd.command_id)
                    processed += 1

            except Exception:
                logger.exception("reconciliation error for %s", client_order_id)

        return processed

    # ── 2. Recent reduceOnly fills (TP missed by WS) ─────────────────────────

    def run_trade_based_reconciliation(self) -> int:
        """
        Poll fetchMyTrades for open chain symbols.
        Finds TP_FILLED position-level events with trade_chain_id=None and correlates them.
        No price matching: createType=CreateByTakeProfit is already deterministic.
        """
        if not hasattr(self._adapter, "fetch_recent_reduce_trades"):
            return 0

        chains = self._repo.get_open_chains_with_tps()
        if not chains:
            return 0

        classifier = self._make_classifier()

        by_symbol: dict[tuple[str, str], list[dict]] = {}
        for chain in chains:
            key = (chain["symbol"], chain["side"])
            by_symbol.setdefault(key, []).append(chain)

        processed = 0
        for (symbol, side), group in by_symbol.items():
            try:
                trades = list(self._adapter.fetch_recent_reduce_trades(
                    symbol=symbol,
                    side=side,
                    execution_account_id=self._account_id,
                    limit=50,
                ))
            except Exception:
                logger.exception("fetch_recent_reduce_trades error %s %s", symbol, side)
                continue

            for trade_dict in trades:
                raw = self._normalizer.from_rest_trade(trade_dict)
                if raw is None:
                    continue

                if raw.create_type in (
                    "CreateByTakeProfit", "CreateByPartialTakeProfit",
                    "CreateByStopLoss", "CreateByPartialStopLoss",
                ):
                    chain_id, tp_level = _correlate_tp_to_chain(raw, group)
                    if chain_id is None:
                        continue
                    event_type = (
                        "TP_FILLED"
                        if raw.create_type in ("CreateByTakeProfit", "CreateByPartialTakeProfit")
                        else "SL_FILLED"
                    )
                    classified = ClassifiedEvent(
                        raw=raw,
                        event_type=event_type,  # type: ignore[arg-type]
                        source="exchange_auto",
                        trade_chain_id=chain_id,
                        tp_level=tp_level if event_type == "TP_FILLED" else None,
                        is_actionable=True,
                    )
                else:
                    classified = classifier.classify(raw)

                inserted = self._repo.insert_raw_and_classified(classified)
                if inserted:
                    processed += 1

        return processed

    # ── 3. Externally closed positions ───────────────────────────────────────

    def run_position_reconciliation(self) -> int:
        """Detect positions closed manually (MANUAL_CLOSE_FULL not seen by WS)."""
        chains    = self._repo.get_open_chains()
        processed = 0

        for chain_id, symbol, side, open_qty in chains:
            try:
                qty = self._adapter.get_position_qty(
                    symbol=symbol,
                    side=side,
                    execution_account_id=self._account_id,
                )
                if qty is None or not (qty == 0.0 and open_qty > 0.0):
                    continue

                raw = ExchangeRawEvent(
                    source_stream="fetch_positions",
                    exchange_event_id=f"pos_closed:{chain_id}",
                    idempotency_key=f"rest_pos_closed:{chain_id}",
                    symbol=symbol, side=side,
                    create_type=None, stop_order_type=None, exec_type=None,
                    order_status=None, order_link_id=None, order_id=None,
                    seq=None,
                    exec_price=None, exec_qty=open_qty, closed_size=open_qty,
                    leaves_qty=None, pos_qty=0.0, exec_value=None,
                    exec_fee=None, fee_rate=None, cum_exec_qty=None,
                    received_at="",
                    raw_info={},
                )
                classified = ClassifiedEvent(
                    raw=raw,
                    event_type="MANUAL_CLOSE_FULL",
                    source="reconciliation_inferred",
                    trade_chain_id=chain_id,
                    is_actionable=True,
                )
                inserted = self._repo.insert_raw_and_classified(classified)
                if inserted:
                    logger.info(
                        "externally closed position: chain=%s %s %s qty=%s",
                        chain_id, symbol, side, open_qty,
                    )
                    processed += 1

            except Exception:
                logger.exception("position reconciliation error chain=%s", chain_id)

        return processed

    # ── 4. Protective orders removed without fill ─────────────────────────────

    def run_protective_orders_reconciliation(self) -> int:
        """Fallback for watchPositions: detects TP/SL removed during WS downtime."""
        if not hasattr(self._adapter, "fetch_position_details"):
            return 0

        chains = self._repo.get_open_chains_with_tps()
        if not chains:
            return 0

        latest_per_chain: dict[int, dict] = {}
        for entry in chains:
            cid = entry["chain_id"]
            if cid not in latest_per_chain or \
               entry["cmd_id"] > latest_per_chain[cid]["cmd_id"]:
                latest_per_chain[cid] = entry

        processed = 0
        for chain_id, entry in latest_per_chain.items():
            expected_tp = entry["tp_price"]
            tp_level    = entry["tp_level"]
            symbol      = entry["symbol"]
            side        = entry["side"]

            if expected_tp <= 0:
                continue
            if self._repo.tp_fill_exists(chain_id, tp_level):
                continue
            if self._repo.protective_cancelled_exists(chain_id, tp_level):
                continue

            try:
                pos = self._adapter.fetch_position_details(
                    symbol=symbol,
                    side=side,
                    execution_account_id=self._account_id,
                )
            except Exception:
                logger.exception("fetch_position_details error chain=%s", chain_id)
                continue

            if pos is None or pos.take_profit is None or pos.take_profit != 0.0:
                continue

            raw = ExchangeRawEvent(
                source_stream="fetch_positions",
                exchange_event_id=f"pos_tp_removed:{chain_id}:tp:{tp_level}",
                idempotency_key=f"rest_tp_removed:{chain_id}:tp:{tp_level}",
                symbol=symbol, side=side,
                create_type=None, stop_order_type=None, exec_type=None,
                order_status=None, order_link_id=None, order_id=None,
                seq=None,
                exec_price=None, exec_qty=None, closed_size=None,
                leaves_qty=None, pos_qty=None, exec_value=None,
                exec_fee=None, fee_rate=None, cum_exec_qty=None,
                position_take_profit=0.0,
                received_at="",
                raw_info={"expected_tp": expected_tp, "tp_level": tp_level},
            )
            classified = ClassifiedEvent(
                raw=raw,
                event_type="PROTECTIVE_ORDER_CANCELLED",
                source="reconciliation_inferred",
                trade_chain_id=chain_id,
                tp_level=tp_level,
                is_actionable=True,
            )
            inserted = self._repo.insert_raw_and_classified(classified)
            if inserted:
                logger.warning(
                    "TP removed externally (REST fallback): chain=%s tp_level=%s",
                    chain_id, tp_level,
                )
                processed += 1

        return processed


# ── Module helpers ─────────────────────────────────────────────────────────────

def _adapter_order_to_ccxt_dict(raw_adapter, client_order_id: str) -> dict:
    """Convert RawAdapterOrder → CCXT-like dict for EventNormalizer.from_order."""
    status = raw_adapter.status or ""
    if status.lower() == "canceled":
        status = "Cancelled"
    return {
        "id":            raw_adapter.exchange_order_id or client_order_id,
        "clientOrderId": client_order_id,
        "status":        status,
        "side":          "",
        "symbol":        "",
        "average":       raw_adapter.average_price,
        "filled":        raw_adapter.filled_qty,
        "info": {
            "orderId":     raw_adapter.exchange_order_id,
            "orderLinkId": client_order_id,
            "orderStatus": status,
            "cancelType":  getattr(raw_adapter, "cancel_reason", None),
            "cumExecQty":  raw_adapter.filled_qty,
            "side":        "",
            "symbol":      "",
        },
    }


def _correlate_tp_to_chain(
    raw: ExchangeRawEvent,
    chains: list[dict],
) -> tuple[int | None, int | None]:
    """
    Correlate a deterministically-classified TP/SL fill to a chain.
    No price matching. Uses: single chain (certain), orderLinkId, most-recent fallback.
    """
    if len(chains) == 1:
        chain = chains[0]
        return chain["chain_id"], chain.get("latest_tp_level", 1)

    order_link_id = (raw.order_link_id or "").strip()
    if order_link_id:
        for chain in chains:
            if order_link_id in chain.get("order_link_ids", []):
                return chain["chain_id"], chain.get("latest_tp_level", 1)

    logger.warning(
        "ambiguous TP/SL fill: symbol=%s, %d open chains — using most recent",
        raw.symbol, len(chains),
    )
    chain = max(chains, key=lambda c: c.get("opened_at", ""))
    return chain["chain_id"], chain.get("latest_tp_level", 1)


__all__ = ["ExchangeEventSyncWorker"]
```

- [ ] **Step 7.2: Run existing event_sync tests**

```bash
pytest tests/runtime_v2/execution_gateway/test_event_sync.py -v
```

Expected: all previously passing tests PASS. Tests that checked `PROTECTIVE_ORDERS_MISSING` event type should be updated to `PROTECTIVE_ORDER_CANCELLED`.

- [ ] **Step 7.3: Run full gateway test suite**

```bash
pytest tests/runtime_v2/execution_gateway/ -v
```

Expected: all tests PASS.

- [ ] **Step 7.4: Commit**

```bash
git add src/runtime_v2/execution_gateway/event_sync.py \
        tests/runtime_v2/execution_gateway/test_event_sync.py
git commit -m "refactor(event_sync): REST safety net using EventNormalizer+EventClassifier, remove price matching"
```

---

## Task 8: Integration Test + Full Suite Verification

**Files:**
- Modify: `tests/runtime_v2/execution_gateway/test_integration.py`

- [ ] **Step 8.1: Add integration test covering full pipeline**

Add this test to `tests/runtime_v2/execution_gateway/test_integration.py`:

```python
def test_ws_and_rest_same_event_no_duplicate(tmp_path):
    """
    WS and REST finding the same TP fill produce only 1 ops_exchange_events row.
    exchange_raw_events gets 2 rows (one per source), ops gets 1 (idempotency_key).
    """
    import sqlite3, json, datetime as dt
    from pathlib import Path
    from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.ws_fill_watcher import (
        BybitWsFillWatcher,
    )
    from src.runtime_v2.execution_gateway.event_ingest.normalizer import EventNormalizer
    from src.runtime_v2.execution_gateway.event_ingest.classifier import EventClassifier
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    # Setup DB
    db = str(tmp_path / "ops.sqlite3")
    conn = sqlite3.connect(db)
    for f in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()

    # Insert open chain + TP command
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO ops_trade_chains "
        "(trade_chain_id, source_enrichment_id, canonical_message_id, raw_message_id, "
        "trader_id, account_id, symbol, side, lifecycle_state, entry_mode, "
        "management_plan_json, open_position_qty, filled_entry_qty, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (99, 99, 99, 99, "t1", "acc", "BTCUSDT", "LONG", "OPEN", "ONE_SHOT",
         "{}", 0.01, 0.01, now, now),
    )
    conn.execute(
        "INSERT INTO ops_execution_commands "
        "(command_id, trade_chain_id, command_type, status, payload_json, "
        "idempotency_key, client_order_id, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (200, 99, "SET_POSITION_TPSL_PARTIAL", "SENT",
         json.dumps({"take_profit": 45000.0, "tp_sequence": 1, "tp_size": 0.01}),
         "idem:200", "tsb:99:200:tp:1", now, now),
    )
    conn.commit()
    conn.close()

    repo = GatewayCommandRepository(db)
    normalizer = EventNormalizer()

    # Simulate WS path (watch_my_trades)
    ws_trade = {
        "id": "exec-shared-001",
        "symbol": "BTC/USDT:USDT", "side": "sell", "price": 45000.0, "amount": 0.01,
        "info": {
            "execId": "exec-shared-001", "symbol": "BTCUSDT", "side": "Sell",
            "createType": "CreateByTakeProfit", "stopOrderType": "TakeProfit",
            "execType": "Trade", "closedSize": "0.01", "posQty": "0",
            "orderLinkId": "tsb:99:200:tp:1", "orderId": "ord-001", "seq": "5001",
            "execPrice": "45000", "execQty": "0.01", "execValue": "450",
            "execFee": "0.18", "feeRate": "0.0004", "cumExecQty": "0.01",
        },
    }
    watcher = BybitWsFillWatcher(
        api_key="k", api_secret="s", testnet=True, ops_db_path=db, repo=repo,
    )
    watcher._process_batch([ws_trade], normalizer.from_trade)

    # Simulate REST path (fetch_my_trades, same execId)
    rest_raw = normalizer.from_rest_trade(ws_trade)
    assert rest_raw is not None
    known = repo.get_known_order_link_ids()
    classifier = EventClassifier(known_order_link_ids=known, open_chain_tp_prices={})
    classified = classifier.classify(rest_raw)
    repo.insert_raw_and_classified(classified)

    # Verify: 2 raw rows (one WS, one REST), 1 ops row
    conn = sqlite3.connect(db)
    raw_count = conn.execute("SELECT COUNT(*) FROM exchange_raw_events").fetchone()[0]
    ops_count = conn.execute(
        "SELECT COUNT(*) FROM ops_exchange_events WHERE event_type='TP_FILLED'"
    ).fetchone()[0]
    conn.close()

    assert raw_count == 2, f"expected 2 raw rows, got {raw_count}"
    assert ops_count == 1, f"expected 1 ops row (idempotent), got {ops_count}"
```

- [ ] **Step 8.2: Run integration test**

```bash
pytest tests/runtime_v2/execution_gateway/test_integration.py::test_ws_and_rest_same_event_no_duplicate -v
```

Expected: PASS.

- [ ] **Step 8.3: Run complete test suite**

```bash
pytest tests/ -v --tb=short 2>&1 | tail -30
```

Expected: all tests PASS, no regressions across the full suite.

- [ ] **Step 8.4: Final commit**

```bash
git add tests/runtime_v2/execution_gateway/test_integration.py
git commit -m "test(integration): verify WS+REST idempotency on same TP fill event"
```

---

## Summary

| Task | What it delivers |
|---|---|
| 1 — DB Migration | `exchange_raw_events` table with indexes |
| 2 — Models | `ExchangeRawEvent`, `ClassifiedEvent`, type aliases |
| 3 — Normalizer | CCXT dict → raw event, 3 stream methods, tested |
| 4 — Classifier | Deterministic classification, 15 test cases |
| 5 — Repository | 5 new methods, atomic write, idempotency |
| 6 — WsFillWatcher | 3-stream WS, zero matching, ~160 lines |
| 7 — EventSyncWorker | REST safety net, shared classifier, ~260 lines |
| 8 — Integration | WS+REST dedup verified end-to-end |

**Total new/changed code:** ~900 lines. **Total tests:** ~40 new test cases.  
**Removed:** `_match_and_save_tp_fill`, `_save_tp_fill_from_trade`, all ±1% price matching logic.
