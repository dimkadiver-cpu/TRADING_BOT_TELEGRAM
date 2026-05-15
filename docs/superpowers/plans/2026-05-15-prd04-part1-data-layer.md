# PRD-04 Part 1 — Data Layer + Risk Engine

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Creare il data layer completo di PRD-04: migrazione DB, modelli Pydantic, ExchangeDataPort, repositories, RiskCapacityEngine.

**Architecture:** Tutto sincrono (sqlite3 standard, no async). Pydantic v2 strict per ogni modello. I repositories espongono metodi di lettura e scrittura singola; le scritture transazionali multi-tabella sono responsabilità del worker (Piano 2).

**Tech Stack:** Python 3.12+, Pydantic v2, sqlite3, pytest

---

## File prodotti da questo piano

| File | Responsabilità |
|------|---------------|
| `db/migrations/028_ops_lifecycle_core.sql` | Schema ops.sqlite3 |
| `src/runtime_v2/lifecycle/__init__.py` | Package marker |
| `src/runtime_v2/lifecycle/models.py` | Tipi Literal, modelli Pydantic (TradeChain, LifecycleEvent, ExecutionCommand, ControlState, ExchangeEvent) |
| `src/runtime_v2/lifecycle/ports.py` | ExchangeDataPort ABC + snapshot models |
| `src/runtime_v2/lifecycle/static_exchange_data_port.py` | StaticExchangeDataPort per test |
| `src/runtime_v2/lifecycle/repositories.py` | TradeChainRepository, LifecycleEventRepository, ExecutionCommandRepository, ControlStateRepository, SnapshotRepository, ExchangeEventRepository |
| `src/runtime_v2/lifecycle/risk_capacity.py` | RiskCapacityEngine + RiskDecision |
| `tests/runtime_v2/lifecycle/__init__.py` | Package marker |
| `tests/runtime_v2/lifecycle/test_models.py` | Test modelli |
| `tests/runtime_v2/lifecycle/test_ports.py` | Test StaticExchangeDataPort |
| `tests/runtime_v2/lifecycle/test_repositories.py` | Test repositories + idempotenza |
| `tests/runtime_v2/lifecycle/test_risk_capacity.py` | Test RiskCapacityEngine |

---

## Task 1: DB Migration

**Files:**
- Create: `db/migrations/028_ops_lifecycle_core.sql`
- Test: `tests/runtime_v2/lifecycle/test_repositories.py` (primo test usa la migrazione)

- [ ] **Step 1: Crea il file di migrazione**

```sql
-- db/migrations/028_ops_lifecycle_core.sql

CREATE TABLE IF NOT EXISTS ops_trade_chains (
    trade_chain_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_enrichment_id    INTEGER NOT NULL UNIQUE,
    canonical_message_id    INTEGER NOT NULL,
    raw_message_id          INTEGER NOT NULL,
    trader_id               TEXT NOT NULL,
    account_id              TEXT NOT NULL,
    symbol                  TEXT NOT NULL,
    side                    TEXT NOT NULL,
    lifecycle_state         TEXT NOT NULL,
    entry_mode              TEXT NOT NULL,
    entry_avg_price         REAL,
    current_stop_price      REAL,
    expected_stop_price     REAL,
    be_protection_status    TEXT NOT NULL DEFAULT 'NOT_PROTECTED',
    entry_timeout_at        TEXT,
    management_plan_json    TEXT NOT NULL DEFAULT '{}',
    risk_snapshot_json      TEXT NOT NULL DEFAULT '{}',
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ops_lifecycle_events (
    event_id                INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_chain_id          INTEGER,
    event_type              TEXT NOT NULL,
    source_type             TEXT NOT NULL,
    source_id               TEXT,
    previous_state          TEXT,
    next_state              TEXT,
    payload_json            TEXT NOT NULL DEFAULT '{}',
    idempotency_key         TEXT NOT NULL UNIQUE,
    created_at              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ops_execution_commands (
    command_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_chain_id          INTEGER NOT NULL,
    command_type            TEXT NOT NULL,
    status                  TEXT NOT NULL DEFAULT 'PENDING',
    payload_json            TEXT NOT NULL DEFAULT '{}',
    idempotency_key         TEXT NOT NULL UNIQUE,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ops_account_snapshots (
    snapshot_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id              TEXT NOT NULL,
    equity_usdt             REAL,
    available_balance_usdt  REAL,
    total_open_risk_usdt    REAL,
    total_margin_used_usdt  REAL,
    source                  TEXT NOT NULL,
    captured_at             TEXT NOT NULL,
    payload_json            TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS ops_market_snapshots (
    snapshot_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id              TEXT NOT NULL,
    symbol                  TEXT NOT NULL,
    mark_price              REAL,
    bid                     REAL,
    ask                     REAL,
    min_order_size          REAL,
    price_precision         INTEGER,
    qty_precision           INTEGER,
    source                  TEXT NOT NULL,
    captured_at             TEXT NOT NULL,
    payload_json            TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS ops_order_snapshots (
    snapshot_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id              TEXT NOT NULL,
    symbol                  TEXT,
    payload_json            TEXT NOT NULL,
    source                  TEXT NOT NULL,
    captured_at             TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ops_position_snapshots (
    snapshot_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id              TEXT NOT NULL,
    symbol                  TEXT NOT NULL,
    side                    TEXT NOT NULL,
    payload_json            TEXT NOT NULL,
    source                  TEXT NOT NULL,
    captured_at             TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ops_exchange_events (
    exchange_event_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_chain_id          INTEGER,
    event_type              TEXT NOT NULL,
    payload_json            TEXT NOT NULL DEFAULT '{}',
    processing_status       TEXT NOT NULL DEFAULT 'NEW',
    idempotency_key         TEXT NOT NULL UNIQUE,
    received_at             TEXT NOT NULL,
    processed_at            TEXT
);

CREATE TABLE IF NOT EXISTS ops_control_state (
    control_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_type              TEXT NOT NULL,
    scope_value             TEXT,
    execution_pause_mode    TEXT NOT NULL DEFAULT 'NONE',
    emergency_action        TEXT,
    reason                  TEXT,
    created_by              TEXT,
    active                  INTEGER NOT NULL DEFAULT 1,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_otc_trader_state
    ON ops_trade_chains(trader_id, lifecycle_state);
CREATE INDEX IF NOT EXISTS idx_otc_symbol_state
    ON ops_trade_chains(symbol, lifecycle_state);
CREATE INDEX IF NOT EXISTS idx_ole_chain
    ON ops_lifecycle_events(trade_chain_id);
CREATE INDEX IF NOT EXISTS idx_oec_chain_status
    ON ops_execution_commands(trade_chain_id, status);
CREATE INDEX IF NOT EXISTS idx_oee_status
    ON ops_exchange_events(processing_status);
CREATE INDEX IF NOT EXISTS idx_ocs_active
    ON ops_control_state(active, scope_type);

CREATE VIEW IF NOT EXISTS view_active_trade_chains AS
SELECT * FROM ops_trade_chains
WHERE lifecycle_state NOT IN ('CLOSED', 'CANCELLED', 'EXPIRED');
```

- [ ] **Step 2: Scrivi il test di smoke per la migrazione**

```python
# tests/runtime_v2/lifecycle/test_repositories.py
from __future__ import annotations

import sqlite3
from pathlib import Path
import pytest


def _apply_migrations(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    for f in sorted(Path("db/migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


@pytest.fixture
def ops_db(tmp_path):
    db_path = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db_path)
    return db_path


def test_migration_creates_ops_tables(ops_db):
    conn = sqlite3.connect(ops_db)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    conn.close()
    assert "ops_trade_chains" in tables
    assert "ops_lifecycle_events" in tables
    assert "ops_execution_commands" in tables
    assert "ops_exchange_events" in tables
    assert "ops_control_state" in tables
```

- [ ] **Step 3: Esegui il test — deve passare**

```
pytest tests/runtime_v2/lifecycle/test_repositories.py::test_migration_creates_ops_tables -v
```

Expected: PASS

- [ ] **Step 4: Crea i package marker**

```python
# src/runtime_v2/lifecycle/__init__.py
```

```python
# tests/runtime_v2/lifecycle/__init__.py
```

- [ ] **Step 5: Commit**

```bash
git add db/migrations/028_ops_lifecycle_core.sql src/runtime_v2/lifecycle/__init__.py tests/runtime_v2/lifecycle/__init__.py tests/runtime_v2/lifecycle/test_repositories.py
git commit -m "feat(prd04): add DB migration 028 + lifecycle package skeleton"
```

---

## Task 2: Core Models

**Files:**
- Create: `src/runtime_v2/lifecycle/models.py`
- Test: `tests/runtime_v2/lifecycle/test_models.py`

- [ ] **Step 1: Scrivi i test dei modelli**

```python
# tests/runtime_v2/lifecycle/test_models.py
from __future__ import annotations

import pytest
from pydantic import ValidationError


def test_trade_chain_requires_mandatory_fields():
    from src.runtime_v2.lifecycle.models import TradeChain
    with pytest.raises(ValidationError):
        TradeChain()  # mancano campi obbligatori


def test_trade_chain_valid():
    from src.runtime_v2.lifecycle.models import TradeChain
    chain = TradeChain(
        source_enrichment_id=1,
        canonical_message_id=10,
        raw_message_id=100,
        trader_id="trader_a",
        account_id="acc_1",
        symbol="BTC/USDT",
        side="LONG",
        lifecycle_state="WAITING_ENTRY",
        entry_mode="ONE_SHOT",
        management_plan_json="{}",
    )
    assert chain.be_protection_status == "NOT_PROTECTED"
    assert chain.trade_chain_id is None


def test_lifecycle_event_valid():
    from src.runtime_v2.lifecycle.models import LifecycleEvent
    event = LifecycleEvent(
        event_type="SIGNAL_ACCEPTED",
        source_type="enrichment",
        idempotency_key="sig_accepted:1",
    )
    assert event.trade_chain_id is None
    assert event.payload_json == "{}"


def test_execution_command_valid():
    from src.runtime_v2.lifecycle.models import ExecutionCommand
    cmd = ExecutionCommand(
        trade_chain_id=1,
        command_type="PLACE_ENTRY",
        payload_json='{"symbol": "BTC/USDT"}',
        idempotency_key="place_entry:1:1",
    )
    assert cmd.status == "PENDING"


def test_terminal_states():
    from src.runtime_v2.lifecycle.models import TERMINAL_STATES
    assert "CLOSED" in TERMINAL_STATES
    assert "CANCELLED" in TERMINAL_STATES
    assert "EXPIRED" in TERMINAL_STATES
    assert "OPEN" not in TERMINAL_STATES
```

- [ ] **Step 2: Esegui i test — devono fallire**

```
pytest tests/runtime_v2/lifecycle/test_models.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implementa models.py**

```python
# src/runtime_v2/lifecycle/models.py
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

LifecycleState = Literal[
    "CREATED", "WAITING_ENTRY", "OPEN", "PARTIALLY_CLOSED",
    "BE_MOVE_PENDING", "PROTECTED_BE", "CLOSED", "CANCELLED",
    "EXPIRED", "REVIEW_REQUIRED", "ERROR",
]
TERMINAL_STATES: frozenset[str] = frozenset({"CLOSED", "CANCELLED", "EXPIRED"})

CommandType = Literal[
    "PLACE_ENTRY", "PLACE_PROTECTIVE_STOP", "PLACE_TAKE_PROFIT",
    "MOVE_STOP_TO_BREAKEVEN", "MOVE_STOP", "CANCEL_PENDING_ENTRY",
    "CLOSE_PARTIAL", "CLOSE_FULL",
]
CommandStatus = Literal["PENDING", "SENT", "ACK", "DONE", "FAILED", "CANCELLED"]

LifecycleEventType = Literal[
    "SIGNAL_ACCEPTED", "TRADE_CHAIN_CREATED", "ENTRY_COMMAND_CREATED",
    "ENTRY_FILLED", "TP_FILLED", "SL_FILLED", "TIMEOUT_REACHED",
    "TELEGRAM_UPDATE_ACCEPTED", "BE_MOVE_REQUESTED",
    "NOOP_ALREADY_PROTECTED_BE", "NOOP_DUPLICATE_COMMAND",
    "NOOP_ALREADY_CLOSED", "NOOP_NOT_PENDING", "NOOP_NO_APPLICABLE_TARGET",
    "REVIEW_REQUIRED",
]
ControlMode = Literal["NONE", "BLOCK_NEW_ENTRIES", "FULL_STOP"]
BeProtectionStatus = Literal["NOT_PROTECTED", "BE_MOVE_PENDING", "PROTECTED"]


class TradeChain(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trade_chain_id: int | None = None
    source_enrichment_id: int
    canonical_message_id: int
    raw_message_id: int
    trader_id: str
    account_id: str
    symbol: str
    side: str
    lifecycle_state: LifecycleState
    entry_mode: str
    entry_avg_price: float | None = None
    current_stop_price: float | None = None
    expected_stop_price: float | None = None
    be_protection_status: BeProtectionStatus = "NOT_PROTECTED"
    entry_timeout_at: datetime | None = None
    management_plan_json: str
    risk_snapshot_json: str = "{}"
    created_at: datetime | None = None
    updated_at: datetime | None = None


class LifecycleEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: int | None = None
    trade_chain_id: int | None = None
    event_type: LifecycleEventType
    source_type: str
    source_id: str | None = None
    previous_state: str | None = None
    next_state: str | None = None
    payload_json: str = "{}"
    idempotency_key: str
    created_at: datetime | None = None


class ExecutionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command_id: int | None = None
    trade_chain_id: int
    command_type: CommandType
    status: CommandStatus = "PENDING"
    payload_json: str = "{}"
    idempotency_key: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ControlState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    control_id: int | None = None
    scope_type: str
    scope_value: str | None = None
    execution_pause_mode: ControlMode = "NONE"
    emergency_action: str | None = None
    reason: str | None = None
    created_by: str | None = None
    active: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ExchangeEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    exchange_event_id: int | None = None
    trade_chain_id: int | None = None
    event_type: str
    payload_json: str = "{}"
    processing_status: str = "NEW"
    idempotency_key: str
    received_at: datetime | None = None
    processed_at: datetime | None = None


__all__ = [
    "LifecycleState", "TERMINAL_STATES", "CommandType", "CommandStatus",
    "LifecycleEventType", "ControlMode", "BeProtectionStatus",
    "TradeChain", "LifecycleEvent", "ExecutionCommand",
    "ControlState", "ExchangeEvent",
]
```

- [ ] **Step 4: Esegui i test — devono passare**

```
pytest tests/runtime_v2/lifecycle/test_models.py -v
```

Expected: PASS (5 test)

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/lifecycle/models.py tests/runtime_v2/lifecycle/test_models.py
git commit -m "feat(prd04): add lifecycle core models"
```

---

## Task 3: Exchange Data Port + Static Adapter

**Files:**
- Create: `src/runtime_v2/lifecycle/ports.py`
- Create: `src/runtime_v2/lifecycle/static_exchange_data_port.py`
- Test: `tests/runtime_v2/lifecycle/test_ports.py`

- [ ] **Step 1: Scrivi i test**

```python
# tests/runtime_v2/lifecycle/test_ports.py
from __future__ import annotations

from datetime import datetime, timezone


def _now() -> datetime:
    return datetime.now(timezone.utc)


def test_static_port_returns_default_account():
    from src.runtime_v2.lifecycle.static_exchange_data_port import StaticExchangeDataPort
    port = StaticExchangeDataPort()
    snap = port.get_account_state("acc_1")
    assert snap.account_id == "acc_1"
    assert snap.source == "static_default"


def test_static_port_returns_configured_account():
    from src.runtime_v2.lifecycle.ports import AccountStateSnapshot
    from src.runtime_v2.lifecycle.static_exchange_data_port import StaticExchangeDataPort
    acc = AccountStateSnapshot(
        account_id="acc_1", equity_usdt=10000.0,
        captured_at=_now(), source="static_test",
    )
    port = StaticExchangeDataPort(account_snapshot=acc)
    snap = port.get_account_state("acc_1")
    assert snap.equity_usdt == 10000.0


def test_static_port_returns_configured_market():
    from src.runtime_v2.lifecycle.ports import SymbolMarketSnapshot
    from src.runtime_v2.lifecycle.static_exchange_data_port import StaticExchangeDataPort
    mkt = SymbolMarketSnapshot(
        symbol="BTC/USDT", mark_price=50000.0,
        captured_at=_now(), source="static_test",
    )
    port = StaticExchangeDataPort(market_snapshots={"BTC/USDT": mkt})
    snap = port.get_symbol_market_state("acc_1", "BTC/USDT")
    assert snap.mark_price == 50000.0


def test_static_port_returns_default_market_for_unknown_symbol():
    from src.runtime_v2.lifecycle.static_exchange_data_port import StaticExchangeDataPort
    port = StaticExchangeDataPort()
    snap = port.get_symbol_market_state("acc_1", "ETH/USDT")
    assert snap.symbol == "ETH/USDT"
    assert snap.mark_price is None


def test_static_port_filters_orders_by_symbol():
    from src.runtime_v2.lifecycle.ports import OrderSnapshot
    from src.runtime_v2.lifecycle.static_exchange_data_port import StaticExchangeDataPort
    orders = [
        OrderSnapshot(symbol="BTC/USDT", side="LONG", order_role="ENTRY", status="OPEN"),
        OrderSnapshot(symbol="ETH/USDT", side="LONG", order_role="ENTRY", status="OPEN"),
    ]
    port = StaticExchangeDataPort(orders=orders)
    assert len(port.get_open_orders("acc_1", "BTC/USDT")) == 1
    assert len(port.get_open_orders("acc_1")) == 2


def test_static_port_returns_position():
    from src.runtime_v2.lifecycle.ports import PositionSnapshot
    from src.runtime_v2.lifecycle.static_exchange_data_port import StaticExchangeDataPort
    pos = PositionSnapshot(symbol="BTC/USDT", side="LONG", status="OPEN", qty_open=0.1)
    port = StaticExchangeDataPort(positions=[pos])
    assert port.get_open_position("acc_1", "BTC/USDT", "LONG") is pos
    assert port.get_open_position("acc_1", "BTC/USDT", "SHORT") is None
```

- [ ] **Step 2: Esegui i test — devono fallire**

```
pytest tests/runtime_v2/lifecycle/test_ports.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implementa ports.py**

```python
# src/runtime_v2/lifecycle/ports.py
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AccountStateSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: str
    equity_usdt: float | None = None
    available_balance_usdt: float | None = None
    total_open_risk_usdt: float | None = None
    total_margin_used_usdt: float | None = None
    captured_at: datetime
    source: str


class SymbolMarketSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    mark_price: float | None = None
    bid: float | None = None
    ask: float | None = None
    min_order_size: float | None = None
    price_precision: int | None = None
    qty_precision: int | None = None
    captured_at: datetime
    source: str


class OrderSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    side: str
    order_role: str
    status: str
    price: float | None = None
    qty: float | None = None
    filled_qty: float | None = None
    source_order_id: str | None = None


class PositionSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    side: str
    status: str
    entry_avg_price: float | None = None
    qty_open: float | None = None
    current_stop_price: float | None = None
    unrealized_pnl: float | None = None


class ExchangeDataPort(ABC):
    @abstractmethod
    def get_account_state(self, account_id: str) -> AccountStateSnapshot: ...

    @abstractmethod
    def get_symbol_market_state(self, account_id: str, symbol: str) -> SymbolMarketSnapshot: ...

    @abstractmethod
    def get_open_orders(self, account_id: str, symbol: str | None = None) -> list[OrderSnapshot]: ...

    @abstractmethod
    def get_open_position(self, account_id: str, symbol: str, side: str) -> PositionSnapshot | None: ...


__all__ = [
    "AccountStateSnapshot", "SymbolMarketSnapshot",
    "OrderSnapshot", "PositionSnapshot", "ExchangeDataPort",
]
```

- [ ] **Step 4: Implementa static_exchange_data_port.py**

```python
# src/runtime_v2/lifecycle/static_exchange_data_port.py
from __future__ import annotations

from datetime import datetime, timezone

from src.runtime_v2.lifecycle.ports import (
    AccountStateSnapshot, ExchangeDataPort, OrderSnapshot,
    PositionSnapshot, SymbolMarketSnapshot,
)


class StaticExchangeDataPort(ExchangeDataPort):
    def __init__(
        self,
        account_snapshot: AccountStateSnapshot | None = None,
        market_snapshots: dict[str, SymbolMarketSnapshot] | None = None,
        orders: list[OrderSnapshot] | None = None,
        positions: list[PositionSnapshot] | None = None,
    ) -> None:
        self._account = account_snapshot
        self._markets: dict[str, SymbolMarketSnapshot] = market_snapshots or {}
        self._orders: list[OrderSnapshot] = orders or []
        self._positions: list[PositionSnapshot] = positions or []

    def get_account_state(self, account_id: str) -> AccountStateSnapshot:
        if self._account is not None:
            return self._account
        return AccountStateSnapshot(
            account_id=account_id,
            captured_at=datetime.now(timezone.utc),
            source="static_default",
        )

    def get_symbol_market_state(self, account_id: str, symbol: str) -> SymbolMarketSnapshot:
        if symbol in self._markets:
            return self._markets[symbol]
        return SymbolMarketSnapshot(
            symbol=symbol,
            captured_at=datetime.now(timezone.utc),
            source="static_default",
        )

    def get_open_orders(self, account_id: str, symbol: str | None = None) -> list[OrderSnapshot]:
        if symbol is None:
            return list(self._orders)
        return [o for o in self._orders if o.symbol == symbol]

    def get_open_position(self, account_id: str, symbol: str, side: str) -> PositionSnapshot | None:
        for p in self._positions:
            if p.symbol == symbol and p.side == side:
                return p
        return None


__all__ = ["StaticExchangeDataPort"]
```

- [ ] **Step 5: Esegui i test — devono passare**

```
pytest tests/runtime_v2/lifecycle/test_ports.py -v
```

Expected: PASS (6 test)

- [ ] **Step 6: Commit**

```bash
git add src/runtime_v2/lifecycle/ports.py src/runtime_v2/lifecycle/static_exchange_data_port.py tests/runtime_v2/lifecycle/test_ports.py
git commit -m "feat(prd04): add ExchangeDataPort + StaticExchangeDataPort"
```

---

## Task 4: Repositories

**Files:**
- Create: `src/runtime_v2/lifecycle/repositories.py`
- Modify: `tests/runtime_v2/lifecycle/test_repositories.py`

- [ ] **Step 1: Aggiungi i test ai repositories**

Aggiungi in fondo a `tests/runtime_v2/lifecycle/test_repositories.py`:

```python
# --- TradeChainRepository ---

def test_chain_repo_save_and_get(ops_db):
    from src.runtime_v2.lifecycle.models import TradeChain
    from src.runtime_v2.lifecycle.repositories import TradeChainRepository
    repo = TradeChainRepository(ops_db)
    chain = TradeChain(
        source_enrichment_id=1, canonical_message_id=10, raw_message_id=100,
        trader_id="trader_a", account_id="acc_1", symbol="BTC/USDT", side="LONG",
        lifecycle_state="WAITING_ENTRY", entry_mode="ONE_SHOT", management_plan_json="{}",
    )
    saved = repo.save(chain)
    assert saved.trade_chain_id is not None
    fetched = repo.get_by_id(saved.trade_chain_id)
    assert fetched is not None
    assert fetched.symbol == "BTC/USDT"
    assert fetched.lifecycle_state == "WAITING_ENTRY"


def test_chain_repo_save_idempotent(ops_db):
    from src.runtime_v2.lifecycle.models import TradeChain
    from src.runtime_v2.lifecycle.repositories import TradeChainRepository
    repo = TradeChainRepository(ops_db)
    chain = TradeChain(
        source_enrichment_id=2, canonical_message_id=20, raw_message_id=200,
        trader_id="trader_a", account_id="acc_1", symbol="ETH/USDT", side="LONG",
        lifecycle_state="WAITING_ENTRY", entry_mode="ONE_SHOT", management_plan_json="{}",
    )
    first = repo.save(chain)
    second = repo.save(chain)
    assert first.trade_chain_id == second.trade_chain_id


def test_chain_repo_get_active_by_trader(ops_db):
    from src.runtime_v2.lifecycle.models import TradeChain
    from src.runtime_v2.lifecycle.repositories import TradeChainRepository
    repo = TradeChainRepository(ops_db)
    for i, state in enumerate(["WAITING_ENTRY", "OPEN", "CLOSED"]):
        repo.save(TradeChain(
            source_enrichment_id=10 + i, canonical_message_id=10 + i,
            raw_message_id=100 + i, trader_id="trader_a", account_id="acc_1",
            symbol=f"SYM{i}/USDT", side="LONG", lifecycle_state=state,
            entry_mode="ONE_SHOT", management_plan_json="{}",
        ))
    active = repo.get_active_by_trader("trader_a")
    assert len(active) == 2
    assert all(c.lifecycle_state not in ("CLOSED", "CANCELLED", "EXPIRED") for c in active)


def test_chain_repo_update_state(ops_db):
    from src.runtime_v2.lifecycle.models import TradeChain
    from src.runtime_v2.lifecycle.repositories import TradeChainRepository
    repo = TradeChainRepository(ops_db)
    chain = repo.save(TradeChain(
        source_enrichment_id=99, canonical_message_id=99, raw_message_id=999,
        trader_id="trader_a", account_id="acc_1", symbol="BTC/USDT", side="LONG",
        lifecycle_state="WAITING_ENTRY", entry_mode="ONE_SHOT", management_plan_json="{}",
    ))
    repo.update_state(chain.trade_chain_id, "OPEN", entry_avg_price=49500.0)
    updated = repo.get_by_id(chain.trade_chain_id)
    assert updated.lifecycle_state == "OPEN"
    assert updated.entry_avg_price == 49500.0


# --- LifecycleEventRepository ---

def test_event_repo_save_idempotent(ops_db):
    from src.runtime_v2.lifecycle.models import LifecycleEvent
    from src.runtime_v2.lifecycle.repositories import LifecycleEventRepository
    repo = LifecycleEventRepository(ops_db)
    event = LifecycleEvent(
        event_type="SIGNAL_ACCEPTED", source_type="enrichment",
        idempotency_key="sig_accepted:1",
    )
    first = repo.save(event)
    second = repo.save(event)
    assert first.event_id == second.event_id


# --- ExecutionCommandRepository ---

def test_command_repo_save_and_get_active(ops_db):
    from src.runtime_v2.lifecycle.models import ExecutionCommand
    from src.runtime_v2.lifecycle.repositories import ExecutionCommandRepository
    repo = ExecutionCommandRepository(ops_db)
    cmd = ExecutionCommand(
        trade_chain_id=1, command_type="PLACE_ENTRY",
        payload_json='{"symbol": "BTC/USDT"}',
        idempotency_key="place_entry:1:1",
    )
    saved = repo.save(cmd)
    assert saved.command_id is not None
    active = repo.get_active_for_chain(1)
    assert len(active) == 1
    assert active[0].command_type == "PLACE_ENTRY"


def test_command_repo_save_idempotent(ops_db):
    from src.runtime_v2.lifecycle.models import ExecutionCommand
    from src.runtime_v2.lifecycle.repositories import ExecutionCommandRepository
    repo = ExecutionCommandRepository(ops_db)
    cmd = ExecutionCommand(
        trade_chain_id=2, command_type="PLACE_ENTRY",
        payload_json='{}', idempotency_key="place_entry:2:1",
    )
    first = repo.save(cmd)
    second = repo.save(cmd)
    assert first.command_id == second.command_id


# --- ControlStateRepository ---

def test_control_state_none_by_default(ops_db):
    from src.runtime_v2.lifecycle.repositories import ControlStateRepository
    repo = ControlStateRepository(ops_db)
    mode = repo.get_effective_mode("acc_1", "trader_a", "BTC/USDT", "LONG")
    assert mode == "NONE"


def test_control_state_global_block(ops_db):
    import sqlite3
    from datetime import datetime, timezone
    from src.runtime_v2.lifecycle.repositories import ControlStateRepository
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(ops_db)
    conn.execute(
        "INSERT INTO ops_control_state (scope_type, scope_value, execution_pause_mode, active, created_at, updated_at) VALUES (?,?,?,?,?,?)",
        ("GLOBAL", None, "BLOCK_NEW_ENTRIES", 1, now, now),
    )
    conn.commit()
    conn.close()
    repo = ControlStateRepository(ops_db)
    mode = repo.get_effective_mode("acc_1", "trader_a", "BTC/USDT", "LONG")
    assert mode == "BLOCK_NEW_ENTRIES"


def test_control_state_most_restrictive_wins(ops_db):
    import sqlite3
    from datetime import datetime, timezone
    from src.runtime_v2.lifecycle.repositories import ControlStateRepository
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(ops_db)
    conn.executemany(
        "INSERT INTO ops_control_state (scope_type, scope_value, execution_pause_mode, active, created_at, updated_at) VALUES (?,?,?,?,?,?)",
        [
            ("GLOBAL", None, "BLOCK_NEW_ENTRIES", 1, now, now),
            ("TRADER", "trader_a", "FULL_STOP", 1, now, now),
        ],
    )
    conn.commit()
    conn.close()
    repo = ControlStateRepository(ops_db)
    mode = repo.get_effective_mode("acc_1", "trader_a", "BTC/USDT", "LONG")
    assert mode == "FULL_STOP"


# --- ExchangeEventRepository ---

def test_exchange_event_repo_get_new_and_mark(ops_db):
    import sqlite3
    from datetime import datetime, timezone
    from src.runtime_v2.lifecycle.repositories import ExchangeEventRepository
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(ops_db)
    conn.execute(
        "INSERT INTO ops_exchange_events (trade_chain_id, event_type, payload_json, processing_status, idempotency_key, received_at) VALUES (?,?,?,?,?,?)",
        (1, "TP_FILLED", '{"tp_level": 1, "is_final": false}', "NEW", "tp_filled:1:1", now),
    )
    conn.commit()
    conn.close()
    repo = ExchangeEventRepository(ops_db)
    events = repo.get_new_events(10)
    assert len(events) == 1
    assert events[0].event_type == "TP_FILLED"
    repo.mark_processed(events[0].exchange_event_id)
    assert len(repo.get_new_events(10)) == 0
```

- [ ] **Step 2: Esegui — devono fallire**

```
pytest tests/runtime_v2/lifecycle/test_repositories.py -v
```

Expected: FAIL dopo il primo test (import error su repositories)

- [ ] **Step 3: Implementa repositories.py**

```python
# src/runtime_v2/lifecycle/repositories.py
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from src.runtime_v2.lifecycle.models import (
    ControlMode, ExecutionCommand, ExchangeEvent,
    LifecycleEvent, TradeChain,
)

_CONTROL_MODE_SEVERITY: dict[str, int] = {"NONE": 0, "BLOCK_NEW_ENTRIES": 1, "FULL_STOP": 2}

_CHAIN_COLS = (
    "trade_chain_id, source_enrichment_id, canonical_message_id, raw_message_id, "
    "trader_id, account_id, symbol, side, lifecycle_state, entry_mode, "
    "entry_avg_price, current_stop_price, expected_stop_price, be_protection_status, "
    "entry_timeout_at, management_plan_json, risk_snapshot_json, created_at, updated_at"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _chain_from_row(row: tuple) -> TradeChain:
    (trade_chain_id, source_enrichment_id, canonical_message_id, raw_message_id,
     trader_id, account_id, symbol, side, lifecycle_state, entry_mode,
     entry_avg_price, current_stop_price, expected_stop_price, be_protection_status,
     entry_timeout_at, management_plan_json, risk_snapshot_json, created_at, updated_at) = row
    return TradeChain(
        trade_chain_id=trade_chain_id,
        source_enrichment_id=source_enrichment_id,
        canonical_message_id=canonical_message_id,
        raw_message_id=raw_message_id,
        trader_id=trader_id,
        account_id=account_id,
        symbol=symbol,
        side=side,
        lifecycle_state=lifecycle_state,
        entry_mode=entry_mode,
        entry_avg_price=entry_avg_price,
        current_stop_price=current_stop_price,
        expected_stop_price=expected_stop_price,
        be_protection_status=be_protection_status,
        entry_timeout_at=datetime.fromisoformat(entry_timeout_at) if entry_timeout_at else None,
        management_plan_json=management_plan_json or "{}",
        risk_snapshot_json=risk_snapshot_json or "{}",
        created_at=datetime.fromisoformat(created_at) if created_at else None,
        updated_at=datetime.fromisoformat(updated_at) if updated_at else None,
    )


class TradeChainRepository:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    def save(self, chain: TradeChain) -> TradeChain:
        now = _now()
        conn = sqlite3.connect(self._db_path)
        try:
            cursor = conn.execute(
                f"""
                INSERT OR IGNORE INTO ops_trade_chains (
                    source_enrichment_id, canonical_message_id, raw_message_id,
                    trader_id, account_id, symbol, side, lifecycle_state, entry_mode,
                    entry_avg_price, current_stop_price, expected_stop_price,
                    be_protection_status, entry_timeout_at, management_plan_json,
                    risk_snapshot_json, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    chain.source_enrichment_id, chain.canonical_message_id, chain.raw_message_id,
                    chain.trader_id, chain.account_id, chain.symbol, chain.side,
                    chain.lifecycle_state, chain.entry_mode,
                    chain.entry_avg_price, chain.current_stop_price, chain.expected_stop_price,
                    chain.be_protection_status,
                    chain.entry_timeout_at.isoformat() if chain.entry_timeout_at else None,
                    chain.management_plan_json, chain.risk_snapshot_json, now, now,
                ),
            )
            conn.commit()
            if cursor.lastrowid and cursor.rowcount > 0:
                row_id = cursor.lastrowid
            else:
                row = conn.execute(
                    "SELECT trade_chain_id FROM ops_trade_chains WHERE source_enrichment_id=?",
                    (chain.source_enrichment_id,),
                ).fetchone()
                row_id = row[0]
        finally:
            conn.close()
        return chain.model_copy(update={"trade_chain_id": row_id})

    def get_by_id(self, trade_chain_id: int) -> TradeChain | None:
        conn = sqlite3.connect(self._db_path)
        try:
            row = conn.execute(
                f"SELECT {_CHAIN_COLS} FROM ops_trade_chains WHERE trade_chain_id=?",
                (trade_chain_id,),
            ).fetchone()
            return _chain_from_row(row) if row else None
        finally:
            conn.close()

    def get_active_by_trader(self, trader_id: str) -> list[TradeChain]:
        conn = sqlite3.connect(self._db_path)
        try:
            rows = conn.execute(
                f"""
                SELECT {_CHAIN_COLS} FROM ops_trade_chains
                WHERE trader_id=? AND lifecycle_state NOT IN ('CLOSED','CANCELLED','EXPIRED')
                """,
                (trader_id,),
            ).fetchall()
            return [_chain_from_row(r) for r in rows]
        finally:
            conn.close()

    def get_timed_out_waiting_entry(self, limit: int = 100) -> list[TradeChain]:
        conn = sqlite3.connect(self._db_path)
        try:
            rows = conn.execute(
                f"""
                SELECT {_CHAIN_COLS} FROM ops_trade_chains
                WHERE lifecycle_state='WAITING_ENTRY'
                  AND entry_timeout_at IS NOT NULL
                  AND entry_timeout_at <= ?
                LIMIT ?
                """,
                (_now(), limit),
            ).fetchall()
            return [_chain_from_row(r) for r in rows]
        finally:
            conn.close()

    def update_state(
        self,
        trade_chain_id: int,
        new_state: str,
        *,
        entry_avg_price: float | None = None,
        current_stop_price: float | None = None,
        be_protection_status: str | None = None,
    ) -> None:
        now = _now()
        fields = ["lifecycle_state=?", "updated_at=?"]
        values: list = [new_state, now]
        if entry_avg_price is not None:
            fields.append("entry_avg_price=?")
            values.append(entry_avg_price)
        if current_stop_price is not None:
            fields.append("current_stop_price=?")
            values.append(current_stop_price)
        if be_protection_status is not None:
            fields.append("be_protection_status=?")
            values.append(be_protection_status)
        values.append(trade_chain_id)
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                f"UPDATE ops_trade_chains SET {', '.join(fields)} WHERE trade_chain_id=?",
                values,
            )
            conn.commit()
        finally:
            conn.close()


class LifecycleEventRepository:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    def save(self, event: LifecycleEvent) -> LifecycleEvent:
        now = _now()
        conn = sqlite3.connect(self._db_path)
        try:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO ops_lifecycle_events (
                    trade_chain_id, event_type, source_type, source_id,
                    previous_state, next_state, payload_json, idempotency_key, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    event.trade_chain_id, event.event_type, event.source_type, event.source_id,
                    event.previous_state, event.next_state, event.payload_json,
                    event.idempotency_key, now,
                ),
            )
            conn.commit()
            if cursor.lastrowid and cursor.rowcount > 0:
                eid = cursor.lastrowid
            else:
                row = conn.execute(
                    "SELECT event_id FROM ops_lifecycle_events WHERE idempotency_key=?",
                    (event.idempotency_key,),
                ).fetchone()
                eid = row[0]
        finally:
            conn.close()
        return event.model_copy(update={"event_id": eid})


class ExecutionCommandRepository:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    def save(self, cmd: ExecutionCommand) -> ExecutionCommand:
        now = _now()
        conn = sqlite3.connect(self._db_path)
        try:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO ops_execution_commands (
                    trade_chain_id, command_type, status, payload_json,
                    idempotency_key, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?)
                """,
                (
                    cmd.trade_chain_id, cmd.command_type, cmd.status,
                    cmd.payload_json, cmd.idempotency_key, now, now,
                ),
            )
            conn.commit()
            if cursor.lastrowid and cursor.rowcount > 0:
                cid = cursor.lastrowid
            else:
                row = conn.execute(
                    "SELECT command_id FROM ops_execution_commands WHERE idempotency_key=?",
                    (cmd.idempotency_key,),
                ).fetchone()
                cid = row[0]
        finally:
            conn.close()
        return cmd.model_copy(update={"command_id": cid})

    def get_active_for_chain(self, trade_chain_id: int) -> list[ExecutionCommand]:
        conn = sqlite3.connect(self._db_path)
        try:
            rows = conn.execute(
                """
                SELECT command_id, trade_chain_id, command_type, status, payload_json,
                       idempotency_key, created_at, updated_at
                FROM ops_execution_commands
                WHERE trade_chain_id=? AND status IN ('PENDING','SENT','ACK')
                """,
                (trade_chain_id,),
            ).fetchall()
            return [
                ExecutionCommand(
                    command_id=r[0], trade_chain_id=r[1], command_type=r[2],
                    status=r[3], payload_json=r[4], idempotency_key=r[5],
                    created_at=datetime.fromisoformat(r[6]) if r[6] else None,
                    updated_at=datetime.fromisoformat(r[7]) if r[7] else None,
                )
                for r in rows
            ]
        finally:
            conn.close()


class ControlStateRepository:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    def get_effective_mode(
        self, account_id: str, trader_id: str, symbol: str, side: str
    ) -> ControlMode:
        conn = sqlite3.connect(self._db_path)
        try:
            rows = conn.execute(
                "SELECT scope_type, scope_value, execution_pause_mode FROM ops_control_state WHERE active=1",
            ).fetchall()
        finally:
            conn.close()

        applicable: list[str] = []
        for scope_type, scope_value, mode in rows:
            if scope_type == "GLOBAL":
                applicable.append(mode)
            elif scope_type == "ACCOUNT" and scope_value == account_id:
                applicable.append(mode)
            elif scope_type == "TRADER" and scope_value == trader_id:
                applicable.append(mode)
            elif scope_type == "SYMBOL" and scope_value == symbol:
                applicable.append(mode)
            elif scope_type == "SIDE" and scope_value == side:
                applicable.append(mode)

        if not applicable:
            return "NONE"
        return max(applicable, key=lambda m: _CONTROL_MODE_SEVERITY.get(m, 0))  # type: ignore[return-value]


class SnapshotRepository:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    def save_account(self, snap, account_id: str) -> None:
        from src.runtime_v2.lifecycle.ports import AccountStateSnapshot
        assert isinstance(snap, AccountStateSnapshot)
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                """
                INSERT INTO ops_account_snapshots (
                    account_id, equity_usdt, available_balance_usdt,
                    total_open_risk_usdt, total_margin_used_usdt, source, captured_at, payload_json
                ) VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    account_id, snap.equity_usdt, snap.available_balance_usdt,
                    snap.total_open_risk_usdt, snap.total_margin_used_usdt,
                    snap.source, snap.captured_at.isoformat(), "{}",
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def save_market(self, snap, account_id: str) -> None:
        from src.runtime_v2.lifecycle.ports import SymbolMarketSnapshot
        assert isinstance(snap, SymbolMarketSnapshot)
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                """
                INSERT INTO ops_market_snapshots (
                    account_id, symbol, mark_price, bid, ask,
                    min_order_size, price_precision, qty_precision,
                    source, captured_at, payload_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    account_id, snap.symbol, snap.mark_price, snap.bid, snap.ask,
                    snap.min_order_size, snap.price_precision, snap.qty_precision,
                    snap.source, snap.captured_at.isoformat(), "{}",
                ),
            )
            conn.commit()
        finally:
            conn.close()


class ExchangeEventRepository:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    def get_new_events(self, limit: int = 100) -> list[ExchangeEvent]:
        conn = sqlite3.connect(self._db_path)
        try:
            rows = conn.execute(
                """
                SELECT exchange_event_id, trade_chain_id, event_type, payload_json,
                       processing_status, idempotency_key, received_at, processed_at
                FROM ops_exchange_events
                WHERE processing_status='NEW'
                ORDER BY received_at
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [
                ExchangeEvent(
                    exchange_event_id=r[0], trade_chain_id=r[1], event_type=r[2],
                    payload_json=r[3], processing_status=r[4], idempotency_key=r[5],
                    received_at=datetime.fromisoformat(r[6]) if r[6] else None,
                    processed_at=datetime.fromisoformat(r[7]) if r[7] else None,
                )
                for r in rows
            ]
        finally:
            conn.close()

    def mark_processed(self, exchange_event_id: int) -> None:
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                "UPDATE ops_exchange_events SET processing_status='DONE', processed_at=? WHERE exchange_event_id=?",
                (_now(), exchange_event_id),
            )
            conn.commit()
        finally:
            conn.close()


__all__ = [
    "TradeChainRepository", "LifecycleEventRepository", "ExecutionCommandRepository",
    "ControlStateRepository", "SnapshotRepository", "ExchangeEventRepository",
]
```

- [ ] **Step 4: Esegui i test — devono passare**

```
pytest tests/runtime_v2/lifecycle/test_repositories.py -v
```

Expected: PASS (tutti i test)

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/lifecycle/repositories.py tests/runtime_v2/lifecycle/test_repositories.py
git commit -m "feat(prd04): add lifecycle repositories with idempotency"
```

---

## Task 5: Risk Capacity Engine

**Files:**
- Create: `src/runtime_v2/lifecycle/risk_capacity.py`
- Create: `tests/runtime_v2/lifecycle/test_risk_capacity.py`

- [ ] **Step 1: Scrivi i test**

```python
# tests/runtime_v2/lifecycle/test_risk_capacity.py
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml


def _now():
    return datetime.now(timezone.utc)


def _make_enriched(
    *,
    trader_id: str = "trader_a",
    enrichment_id: int = 1,
    symbol: str = "BTC/USDT",
    side: str = "LONG",
    entry_type: str = "LIMIT",
    entry_price: float = 50000.0,
    sl_price: float = 49000.0,
    tp_prices: list[float] | None = None,
    capital_base_usdt: float = 1000.0,
    risk_pct: float = 1.0,
    capital_base_mode: str = "static_config",
    max_concurrent_trades: int = 5,
    max_concurrent_same_symbol: int = 1,
):
    from src.parser_v2.contracts.entities import EntryLeg, Price, StopLoss, TakeProfit
    from src.parser_v2.contracts.enums import EntryStructure
    from src.runtime_v2.signal_enrichment.models import (
        CloseDistributionConfig, EffectiveEnrichmentConfig, EnrichedCanonicalMessage,
        EnrichedEntryLeg, EnrichedSignalPayload, ManagementPlanConfig,
        MarketExecutionConfig, EntrySplitConfig, EntryWeightsConfig,
        LimitEntrySplitConfig, MarketEntrySplitConfig, EntryRangeConfig,
        RiskConfig, SignalPolicyConfig, TpConfig, SlConfig,
        PriceCorrectionsConfig, PriceSanityConfig,
    )

    entries = [EnrichedEntryLeg(
        sequence=1, entry_type=entry_type,
        price=Price(raw=str(entry_price), value=entry_price) if entry_type == "LIMIT" else None,
        weight=1.0,
    )]
    tps = [
        TakeProfit(sequence=i + 1, price=Price(raw=str(p), value=p))
        for i, p in enumerate(tp_prices or [51000.0])
    ]
    sl = StopLoss(price=Price(raw=str(sl_price), value=sl_price))

    signal = EnrichedSignalPayload(
        symbol=symbol, side=side, entry_structure="ONE_SHOT",
        entries=entries, take_profits=tps, stop_loss=sl,
    )

    weights_single = EntryWeightsConfig(weights={"E1": 1.0})
    weights_range = EntryRangeConfig(weights={"E1": 0.5, "E2": 0.5})
    risk_config = RiskConfig(
        mode="risk_pct_of_capital",
        risk_pct_of_capital=risk_pct,
        capital_base_mode=capital_base_mode,
        capital_base_usdt=capital_base_usdt,
        leverage=1,
        max_capital_at_risk_per_trader_pct=10.0,
        max_concurrent_trades=max_concurrent_trades,
        max_concurrent_same_symbol=max_concurrent_same_symbol,
    )
    policy = EffectiveEnrichmentConfig(
        trader_id=trader_id,
        enabled=True,
        gate_mode="block",
        hedge_mode=False,
        account_id="acc_1",
        signal_policy=SignalPolicyConfig(
            accepted_entry_structures=["ONE_SHOT", "TWO_STEP", "RANGE", "LADDER"],
            market_execution=MarketExecutionConfig(),
            entry_split=EntrySplitConfig(
                LIMIT=LimitEntrySplitConfig(
                    single=weights_single, range=EntryRangeConfig(weights={"E1": 0.5, "E2": 0.5}),
                    averaging=weights_single, ladder=weights_single,
                ),
                MARKET=MarketEntrySplitConfig(single=weights_single, averaging=weights_single),
            ),
            tp=TpConfig(), sl=SlConfig(),
            price_corrections=PriceCorrectionsConfig(),
            price_sanity=PriceSanityConfig(),
        ),
        update_admission={},
        management_plan=ManagementPlanConfig(),
        risk=risk_config,
    )

    return EnrichedCanonicalMessage(
        enrichment_id=enrichment_id,
        canonical_message_id=enrichment_id * 10,
        raw_message_id=enrichment_id * 100,
        trader_id=trader_id,
        account_id="acc_1",
        primary_class="SIGNAL",
        enrichment_decision="PASS",
        enriched_signal=signal,
        management_plan=ManagementPlanConfig(),
        policy_snapshot=policy.model_dump(),
    )


def test_risk_engine_passes_valid_limit_signal():
    from src.runtime_v2.lifecycle.risk_capacity import RiskCapacityEngine
    engine = RiskCapacityEngine()
    enriched = _make_enriched()
    decision = engine.validate(enriched, [], None, None)
    assert decision.passed is True
    assert decision.size_usdt is not None
    assert decision.size_usdt > 0


def test_risk_engine_calculates_correct_size():
    from src.runtime_v2.lifecycle.risk_capacity import RiskCapacityEngine
    engine = RiskCapacityEngine()
    # capital=1000, risk_pct=1% → risk_amount=10
    # entry=50000, sl=49000 → distance=1000
    # size_usdt = 10 / 1000 * 50000 = 500
    enriched = _make_enriched(capital_base_usdt=1000.0, risk_pct=1.0,
                              entry_price=50000.0, sl_price=49000.0)
    decision = engine.validate(enriched, [], None, None)
    assert decision.passed is True
    assert abs(decision.size_usdt - 500.0) < 0.01


def test_risk_engine_blocks_market_entry_without_snapshot():
    from src.runtime_v2.lifecycle.risk_capacity import RiskCapacityEngine
    engine = RiskCapacityEngine()
    enriched = _make_enriched(entry_type="MARKET")
    decision = engine.validate(enriched, [], None, None)
    assert decision.passed is False
    assert decision.reason == "missing_market_price_for_market_entry"


def test_risk_engine_passes_market_entry_with_snapshot():
    from src.runtime_v2.lifecycle.ports import SymbolMarketSnapshot
    from src.runtime_v2.lifecycle.risk_capacity import RiskCapacityEngine
    engine = RiskCapacityEngine()
    enriched = _make_enriched(entry_type="MARKET", sl_price=49000.0)
    mkt = SymbolMarketSnapshot(symbol="BTC/USDT", mark_price=50000.0,
                               captured_at=_now(), source="static")
    decision = engine.validate(enriched, [], None, mkt)
    assert decision.passed is True


def test_risk_engine_blocks_live_equity_without_snapshot():
    from src.runtime_v2.lifecycle.risk_capacity import RiskCapacityEngine
    engine = RiskCapacityEngine()
    enriched = _make_enriched(capital_base_mode="live_equity")
    decision = engine.validate(enriched, [], None, None)
    assert decision.passed is False
    assert decision.reason == "missing_account_snapshot_for_live_equity"


def test_risk_engine_uses_live_equity_when_available():
    from src.runtime_v2.lifecycle.ports import AccountStateSnapshot
    from src.runtime_v2.lifecycle.risk_capacity import RiskCapacityEngine
    engine = RiskCapacityEngine()
    enriched = _make_enriched(capital_base_mode="live_equity")
    acc = AccountStateSnapshot(account_id="acc_1", equity_usdt=2000.0,
                               captured_at=_now(), source="static")
    decision = engine.validate(enriched, [], acc, None)
    assert decision.passed is True
    # capital=2000, risk_pct=1% → risk=20, size=20/1000*50000=1000
    assert abs(decision.size_usdt - 1000.0) < 0.01


def test_risk_engine_blocks_max_concurrent_trades(tmp_path):
    from src.runtime_v2.lifecycle.models import TradeChain
    from src.runtime_v2.lifecycle.risk_capacity import RiskCapacityEngine
    engine = RiskCapacityEngine()
    enriched = _make_enriched(max_concurrent_trades=2)
    open_chains = [
        TradeChain(source_enrichment_id=i, canonical_message_id=i, raw_message_id=i,
                   trader_id="trader_a", account_id="acc_1",
                   symbol=f"SYM{i}/USDT", side="LONG", lifecycle_state="OPEN",
                   entry_mode="ONE_SHOT", management_plan_json="{}", trade_chain_id=i)
        for i in range(1, 3)
    ]
    decision = engine.validate(enriched, open_chains, None, None)
    assert decision.passed is False
    assert decision.reason == "max_concurrent_trades_reached"


def test_risk_engine_blocks_max_same_symbol():
    from src.runtime_v2.lifecycle.models import TradeChain
    from src.runtime_v2.lifecycle.risk_capacity import RiskCapacityEngine
    engine = RiskCapacityEngine()
    enriched = _make_enriched(symbol="BTC/USDT", max_concurrent_same_symbol=1)
    open_chains = [
        TradeChain(source_enrichment_id=1, canonical_message_id=1, raw_message_id=1,
                   trader_id="trader_a", account_id="acc_1",
                   symbol="BTC/USDT", side="SHORT", lifecycle_state="OPEN",
                   entry_mode="ONE_SHOT", management_plan_json="{}", trade_chain_id=1)
    ]
    decision = engine.validate(enriched, open_chains, None, None)
    assert decision.passed is False
    assert decision.reason == "max_concurrent_same_symbol_reached"


def test_risk_engine_blocks_zero_risk_distance():
    from src.runtime_v2.lifecycle.risk_capacity import RiskCapacityEngine
    engine = RiskCapacityEngine()
    enriched = _make_enriched(entry_price=50000.0, sl_price=50000.0)
    decision = engine.validate(enriched, [], None, None)
    assert decision.passed is False
    assert decision.reason == "zero_risk_distance"
```

- [ ] **Step 2: Esegui — devono fallire**

```
pytest tests/runtime_v2/lifecycle/test_risk_capacity.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implementa risk_capacity.py**

```python
# src/runtime_v2/lifecycle/risk_capacity.py
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from src.runtime_v2.lifecycle.models import TradeChain
from src.runtime_v2.lifecycle.ports import AccountStateSnapshot, SymbolMarketSnapshot
from src.runtime_v2.signal_enrichment.models import (
    EffectiveEnrichmentConfig, EnrichedCanonicalMessage,
)

logger = logging.getLogger(__name__)


@dataclass
class RiskDecision:
    passed: bool
    reason: str | None
    size_usdt: float | None = None
    leverage: int | None = None
    risk_snapshot: dict = field(default_factory=dict)


class RiskCapacityEngine:
    def validate(
        self,
        enriched: EnrichedCanonicalMessage,
        open_chains: list[TradeChain],
        account_snapshot: AccountStateSnapshot | None,
        market_snapshot: SymbolMarketSnapshot | None,
    ) -> RiskDecision:
        signal = enriched.enriched_signal
        if signal is None:
            return RiskDecision(passed=False, reason="no_signal_payload")

        try:
            config = EffectiveEnrichmentConfig.model_validate(enriched.policy_snapshot)
        except Exception as exc:
            logger.warning("invalid policy_snapshot: %s", exc)
            return RiskDecision(passed=False, reason="invalid_policy_snapshot")

        risk = config.risk
        symbol = signal.symbol or ""
        side = signal.side or ""

        trader_chains = [c for c in open_chains if c.trader_id == enriched.trader_id]

        if len(trader_chains) >= risk.max_concurrent_trades:
            return RiskDecision(passed=False, reason="max_concurrent_trades_reached")

        same_symbol = [c for c in trader_chains if c.symbol == symbol]
        if len(same_symbol) >= risk.max_concurrent_same_symbol:
            return RiskDecision(passed=False, reason="max_concurrent_same_symbol_reached")

        if any(c.symbol == symbol and c.side == side for c in trader_chains):
            return RiskDecision(passed=False, reason="duplicate_position")

        if not signal.entries:
            return RiskDecision(passed=False, reason="no_entry_legs")

        first_leg = signal.entries[0]

        if first_leg.entry_type == "MARKET":
            if market_snapshot is None or market_snapshot.mark_price is None:
                return RiskDecision(passed=False, reason="missing_market_price_for_market_entry")
            entry_price = market_snapshot.mark_price
        else:
            if first_leg.price is None:
                return RiskDecision(passed=False, reason="missing_limit_price")
            entry_price = first_leg.price.value

        if signal.stop_loss is None or signal.stop_loss.price is None:
            return RiskDecision(passed=False, reason="missing_stop_loss_for_risk_calc")
        sl_price = signal.stop_loss.price.value

        risk_distance = abs(entry_price - sl_price)
        if risk_distance == 0:
            return RiskDecision(passed=False, reason="zero_risk_distance")

        if risk.capital_base_mode == "live_equity":
            if account_snapshot is None or account_snapshot.equity_usdt is None:
                return RiskDecision(passed=False, reason="missing_account_snapshot_for_live_equity")
            capital = account_snapshot.equity_usdt
        else:
            capital = risk.capital_base_usdt

        if risk.mode == "risk_usdt_fixed":
            risk_amount = risk.risk_usdt_fixed
        else:
            risk_amount = capital * risk.risk_pct_of_capital / 100.0

        max_risk = capital * risk.max_capital_at_risk_per_trader_pct / 100.0
        current_open_risk = 0.0
        for c in trader_chains:
            try:
                snap = json.loads(c.risk_snapshot_json)
                current_open_risk += float(snap.get("risk_amount", 0.0))
            except (json.JSONDecodeError, ValueError, TypeError):
                pass
        if current_open_risk + risk_amount > max_risk:
            return RiskDecision(passed=False, reason="max_capital_at_risk_exceeded")

        size_usdt = risk_amount / risk_distance * entry_price
        leverage = risk.leverage

        risk_snapshot = {
            "capital": capital,
            "risk_amount": risk_amount,
            "entry_price": entry_price,
            "sl_price": sl_price,
            "risk_distance": risk_distance,
            "size_usdt": size_usdt,
            "leverage": leverage,
            "capital_base_mode": risk.capital_base_mode,
        }

        return RiskDecision(
            passed=True,
            reason=None,
            size_usdt=size_usdt,
            leverage=leverage,
            risk_snapshot=risk_snapshot,
        )


__all__ = ["RiskCapacityEngine", "RiskDecision"]
```

- [ ] **Step 4: Esegui i test — devono passare**

```
pytest tests/runtime_v2/lifecycle/test_risk_capacity.py -v
```

Expected: PASS (8 test)

- [ ] **Step 5: Esegui tutti i test del piano**

```
pytest tests/runtime_v2/lifecycle/ -v
```

Expected: PASS (tutti)

- [ ] **Step 6: Commit**

```bash
git add src/runtime_v2/lifecycle/risk_capacity.py tests/runtime_v2/lifecycle/test_risk_capacity.py
git commit -m "feat(prd04): add RiskCapacityEngine"
```

---

## Verifica finale Piano 1

```
pytest tests/runtime_v2/lifecycle/ -v --tb=short
```

Expected: tutti i test passano, nessun import a Hummingbot/executor concreto.

```bash
git log --oneline -5
```

Deve mostrare i 4 commit di questo piano.
