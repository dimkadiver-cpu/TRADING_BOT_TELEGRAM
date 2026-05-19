# PRD-05 Execution Gateway — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implementare l'Execution Gateway neutro che legge comandi PRD-04, li invia a Hummingbot API paper tramite adapter, e normalizza i fill in eventi lifecycle.

**Architecture:** ExecutionCommandWorker legge `ops_execution_commands` con tre query (PENDING, retry, WAITING_POSITION su chain OPEN), passa al ExecutionGateway che risolve adapter via config e genera client_order_id deterministico, l'adapter traduce in REST Hummingbot. ExchangeEventSyncWorker fa polling smart su ordini attivi e normalizza fill nel vocabolario PRD-04.

**Tech Stack:** Python 3.12+, Pydantic v2, sqlite3, httpx (HTTP client), pytest

**Spec di riferimento:** `docs/superpowers/specs/2026-05-16-prd05-definitive-design.md`

---

## File map

```
NUOVI:
db/ops_migrations/002_ops_execution_gateway.sql
config/execution.yaml
src/runtime_v2/execution_gateway/__init__.py
src/runtime_v2/execution_gateway/models.py
src/runtime_v2/execution_gateway/config_loader.py
src/runtime_v2/execution_gateway/client_order_id.py
src/runtime_v2/execution_gateway/repositories.py
src/runtime_v2/execution_gateway/gateway.py
src/runtime_v2/execution_gateway/command_worker.py
src/runtime_v2/execution_gateway/event_sync.py
src/runtime_v2/execution_gateway/adapters/__init__.py
src/runtime_v2/execution_gateway/adapters/base.py
src/runtime_v2/execution_gateway/adapters/fake.py
src/runtime_v2/execution_gateway/adapters/hummingbot_api_paper.py
hummingbot_scripts/__init__.py
hummingbot_scripts/fill_bridge.py
tests/runtime_v2/execution_gateway/__init__.py
tests/runtime_v2/execution_gateway/test_config_loader.py
tests/runtime_v2/execution_gateway/test_client_order_id.py
tests/runtime_v2/execution_gateway/test_gateway.py
tests/runtime_v2/execution_gateway/test_command_worker.py
tests/runtime_v2/execution_gateway/test_event_sync.py
tests/runtime_v2/execution_gateway/test_hummingbot_adapter.py
tests/runtime_v2/execution_gateway/test_integration.py

MODIFICATI:
src/runtime_v2/lifecycle/models.py  ← aggiunge WAITING_POSITION, REVIEW_REQUIRED a CommandStatus
```

---

## Task 1: DB Migration

**Files:**
- Create: `db/ops_migrations/002_ops_execution_gateway.sql`
- Test: verifica che la migration si applichi senza errori

- [ ] **Step 1: Crea la migration**

```sql
-- db/ops_migrations/002_ops_execution_gateway.sql

ALTER TABLE ops_execution_commands ADD COLUMN adapter TEXT;
ALTER TABLE ops_execution_commands ADD COLUMN execution_account_id TEXT;
ALTER TABLE ops_execution_commands ADD COLUMN client_order_id TEXT;
ALTER TABLE ops_execution_commands ADD COLUMN result_payload_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE ops_execution_commands ADD COLUMN sent_at TEXT;
ALTER TABLE ops_execution_commands ADD COLUMN acknowledged_at TEXT;
ALTER TABLE ops_execution_commands ADD COLUMN completed_at TEXT;
ALTER TABLE ops_execution_commands ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE ops_execution_commands ADD COLUMN next_retry_at TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_oec_client_order_id
    ON ops_execution_commands(client_order_id)
    WHERE client_order_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_oec_retry
    ON ops_execution_commands(status, next_retry_at)
    WHERE status = 'SENT' AND next_retry_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_oec_waiting
    ON ops_execution_commands(status)
    WHERE status = 'WAITING_POSITION';
```

- [ ] **Step 2: Verifica applicazione**

```bash
python -c "
import sqlite3, pathlib
conn = sqlite3.connect(':memory:')
for f in sorted(pathlib.Path('db/ops_migrations').glob('*.sql')):
    conn.executescript(f.read_text(encoding='utf-8'))
cols = [r[1] for r in conn.execute('PRAGMA table_info(ops_execution_commands)').fetchall()]
assert 'client_order_id' in cols
assert 'retry_count' in cols
print('OK:', cols)
"
```

Expected: stampa lista colonne con `client_order_id` e `retry_count`.

- [ ] **Step 3: Commit**

```bash
git add db/ops_migrations/002_ops_execution_gateway.sql
git commit -m "feat(prd05): migration ops_execution_gateway — estende ops_execution_commands"
```

---

## Task 2: Estendi CommandStatus in lifecycle/models.py

**Files:**
- Modify: `src/runtime_v2/lifecycle/models.py:21`

- [ ] **Step 1: Scrivi il test di verifica**

Aggiungi in `tests/runtime_v2/lifecycle/test_models.py`:

```python
def test_command_status_includes_prd05_states():
    from src.runtime_v2.lifecycle.models import CommandStatus
    import typing
    args = typing.get_args(CommandStatus)
    assert "WAITING_POSITION" in args
    assert "REVIEW_REQUIRED" in args
```

- [ ] **Step 2: Esegui il test per verificare che fallisce**

```bash
pytest tests/runtime_v2/lifecycle/test_models.py::test_command_status_includes_prd05_states -v
```

Expected: FAILED — `assert "WAITING_POSITION" in args`

- [ ] **Step 3: Modifica lifecycle/models.py**

Sostituisci la riga 21:

```python
# PRIMA:
CommandStatus = Literal["PENDING", "SENT", "ACK", "DONE", "FAILED", "CANCELLED"]

# DOPO:
CommandStatus = Literal[
    "PENDING",           # creato da PRD-04, non ancora inviato
    "SENT",              # richiesta inviata all'adapter
    "ACK",               # exchange ha accettato l'ordine
    "WAITING_POSITION",  # attende fill reale (TP prima di entry fill)
    "DONE",              # ordine completato
    "FAILED",            # errore terminale
    "REVIEW_REQUIRED",   # richiede intervento manuale
    "CANCELLED",         # annullato da lifecycle o sostituito
]
```

- [ ] **Step 4: Esegui test lifecycle esistenti**

```bash
pytest tests/runtime_v2/lifecycle/ -v
```

Expected: tutti PASS (la modifica è additiva, non rimuove valori esistenti).

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/lifecycle/models.py tests/runtime_v2/lifecycle/test_models.py
git commit -m "feat(prd05): estende CommandStatus con WAITING_POSITION e REVIEW_REQUIRED"
```

---

## Task 3: Config models Pydantic + execution.yaml

**Files:**
- Create: `config/execution.yaml`
- Create: `src/runtime_v2/execution_gateway/models.py`
- Create: `tests/runtime_v2/execution_gateway/__init__.py`
- Create: `tests/runtime_v2/execution_gateway/test_config_loader.py` (stub, completato nel Task 4)

- [ ] **Step 1: Crea la struttura directory**

```bash
mkdir -p src/runtime_v2/execution_gateway/adapters
mkdir -p tests/runtime_v2/execution_gateway
touch src/runtime_v2/execution_gateway/__init__.py
touch src/runtime_v2/execution_gateway/adapters/__init__.py
touch tests/runtime_v2/execution_gateway/__init__.py
```

- [ ] **Step 2: Crea config/execution.yaml**

```yaml
# config/execution.yaml
execution:
  default_adapter: hummingbot_api_paper

  account_routing:
    default:
      adapter: hummingbot_api_paper
      execution_account_id: bybit_paper_main

  adapters:
    hummingbot_api_paper:
      type: hummingbot_api
      mode: paper
      base_url: http://localhost:8000
      connector: bybit_perpetual_paper_trade
      leverage: 1

      entry_execution:
        mode: b_entry_stop_then_tp

      retry:
        max_attempts: 3
        backoff_seconds: [30, 90, 300]

      capabilities:
        place_entry: true
        protective_stop_native: true
        take_profit_native: true
        bracket_order: false
        move_stop: true
        close_partial: true
        close_full: true
        executor_position: false

      take_profit:
        min_order_policy: review
        residual_policy: assign_to_last_tp

      position_management:
        same_symbol_same_side_policy: block
        same_symbol_opposite_side_policy: allow_if_hedge_mode
        require_client_order_id_correlation: true

      live_safety:
        allow_live_trading: false
```

- [ ] **Step 3: Crea src/runtime_v2/execution_gateway/models.py**

```python
# src/runtime_v2/execution_gateway/models.py
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class AdapterCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")
    place_entry: bool = True
    protective_stop_native: bool = False
    take_profit_native: bool = False
    bracket_order: bool = False
    move_stop: bool = False
    close_partial: bool = False
    close_full: bool = False
    executor_position: bool = False


class RetryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_attempts: int = 3
    backoff_seconds: list[int] = [30, 90, 300]


class TakeProfitConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    min_order_policy: str = "review"
    residual_policy: str = "assign_to_last_tp"


class PositionManagementConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    same_symbol_same_side_policy: str = "block"
    same_symbol_opposite_side_policy: str = "allow_if_hedge_mode"
    require_client_order_id_correlation: bool = True


class LiveSafetyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allow_live_trading: bool = False


class EntryExecutionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str = "b_entry_stop_then_tp"


class AdapterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str
    mode: str
    base_url: str
    connector: str
    leverage: int = 1
    entry_execution: EntryExecutionConfig = EntryExecutionConfig()
    retry: RetryConfig = RetryConfig()
    capabilities: AdapterCapabilities = AdapterCapabilities()
    take_profit: TakeProfitConfig = TakeProfitConfig()
    position_management: PositionManagementConfig = PositionManagementConfig()
    live_safety: LiveSafetyConfig = LiveSafetyConfig()


class AccountRoutingEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    adapter: str
    execution_account_id: str


class ExecutionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    default_adapter: str
    account_routing: dict[str, AccountRoutingEntry]
    adapters: dict[str, AdapterConfig]

    def resolve_routing(self, account_id: str) -> tuple[AccountRoutingEntry, AdapterConfig]:
        routing = self.account_routing.get(account_id) or self.account_routing["default"]
        adapter_cfg = self.adapters[routing.adapter]
        return routing, adapter_cfg


class RawAdapterOrder(BaseModel):
    model_config = ConfigDict(extra="ignore")
    client_order_id: str
    exchange_order_id: str | None = None
    adapter_order_id: str | None = None
    status: str  # OPEN | FILLED | CANCELLED | FAILED
    filled_qty: float = 0.0
    average_price: float | None = None

    @property
    def is_filled(self) -> bool:
        return self.status == "FILLED"


class AdapterResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    success: bool
    adapter_order_id: str | None = None
    exchange_order_id: str | None = None
    error: str | None = None
    reason: str | None = None
    warnings: list[str] = []


__all__ = [
    "AdapterCapabilities", "RetryConfig", "TakeProfitConfig",
    "PositionManagementConfig", "LiveSafetyConfig", "EntryExecutionConfig",
    "AdapterConfig", "AccountRoutingEntry", "ExecutionConfig",
    "RawAdapterOrder", "AdapterResult",
]
```

- [ ] **Step 4: Commit**

```bash
git add config/execution.yaml src/runtime_v2/execution_gateway/ tests/runtime_v2/execution_gateway/__init__.py
git commit -m "feat(prd05): execution_gateway package skeleton + config models + execution.yaml"
```

---

## Task 4: ExecutionConfigLoader

**Files:**
- Create: `src/runtime_v2/execution_gateway/config_loader.py`
- Create: `tests/runtime_v2/execution_gateway/test_config_loader.py`

- [ ] **Step 1: Scrivi i test**

```python
# tests/runtime_v2/execution_gateway/test_config_loader.py
from __future__ import annotations

import pytest
import yaml
from pathlib import Path


@pytest.fixture
def minimal_config(tmp_path) -> Path:
    cfg = {
        "execution": {
            "default_adapter": "fake",
            "account_routing": {
                "default": {"adapter": "fake", "execution_account_id": "acc_main"}
            },
            "adapters": {
                "fake": {
                    "type": "fake", "mode": "paper",
                    "base_url": "http://localhost:9999",
                    "connector": "fake_connector",
                }
            },
        }
    }
    p = tmp_path / "execution.yaml"
    p.write_text(yaml.dump(cfg))
    return p


def test_load_valid_config(minimal_config):
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    loader = ExecutionConfigLoader(str(minimal_config))
    config = loader.load()
    assert config.default_adapter == "fake"
    assert "default" in config.account_routing


def test_resolve_routing_default(minimal_config):
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    config = ExecutionConfigLoader(str(minimal_config)).load()
    routing, adapter = config.resolve_routing("acc_unknown")
    assert routing.execution_account_id == "acc_main"
    assert adapter.type == "fake"


def test_resolve_routing_specific(tmp_path):
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    cfg = {
        "execution": {
            "default_adapter": "fake",
            "account_routing": {
                "default": {"adapter": "fake", "execution_account_id": "acc_main"},
                "acc_trader_a": {"adapter": "fake", "execution_account_id": "acc_a"},
            },
            "adapters": {
                "fake": {
                    "type": "fake", "mode": "paper",
                    "base_url": "http://localhost:9999",
                    "connector": "fake_connector",
                }
            },
        }
    }
    p = tmp_path / "execution.yaml"
    p.write_text(yaml.dump(cfg))
    config = ExecutionConfigLoader(str(p)).load()
    routing, _ = config.resolve_routing("acc_trader_a")
    assert routing.execution_account_id == "acc_a"


def test_missing_default_routing_raises(tmp_path):
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    cfg = {
        "execution": {
            "default_adapter": "fake",
            "account_routing": {},
            "adapters": {
                "fake": {
                    "type": "fake", "mode": "paper",
                    "base_url": "http://localhost:9999",
                    "connector": "fake_connector",
                }
            },
        }
    }
    p = tmp_path / "execution.yaml"
    p.write_text(yaml.dump(cfg))
    config = ExecutionConfigLoader(str(p)).load()
    with pytest.raises(KeyError):
        config.resolve_routing("acc_x")
```

- [ ] **Step 2: Esegui i test per verificare che falliscono**

```bash
pytest tests/runtime_v2/execution_gateway/test_config_loader.py -v
```

Expected: FAILED — `ModuleNotFoundError: No module named 'src.runtime_v2.execution_gateway.config_loader'`

- [ ] **Step 3: Implementa ExecutionConfigLoader**

```python
# src/runtime_v2/execution_gateway/config_loader.py
from __future__ import annotations

import yaml

from src.runtime_v2.execution_gateway.models import ExecutionConfig


class ExecutionConfigLoader:
    def __init__(self, config_path: str = "config/execution.yaml") -> None:
        self._path = config_path

    def load(self) -> ExecutionConfig:
        with open(self._path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        return ExecutionConfig.model_validate(raw["execution"])


__all__ = ["ExecutionConfigLoader"]
```

- [ ] **Step 4: Installa pyyaml se non presente**

```bash
pip show pyyaml || pip install pyyaml
```

- [ ] **Step 5: Esegui i test**

```bash
pytest tests/runtime_v2/execution_gateway/test_config_loader.py -v
```

Expected: tutti PASS.

- [ ] **Step 6: Commit**

```bash
git add src/runtime_v2/execution_gateway/config_loader.py tests/runtime_v2/execution_gateway/test_config_loader.py
git commit -m "feat(prd05): ExecutionConfigLoader — carica e valida execution.yaml"
```

---

## Task 5: client_order_id builder/parser

**Files:**
- Create: `src/runtime_v2/execution_gateway/client_order_id.py`
- Create: `tests/runtime_v2/execution_gateway/test_client_order_id.py`

- [ ] **Step 1: Scrivi i test**

```python
# tests/runtime_v2/execution_gateway/test_client_order_id.py
from __future__ import annotations

import pytest


def test_build_entry():
    from src.runtime_v2.execution_gateway.client_order_id import build, parse
    coid = build(trade_chain_id=42, command_id=1001, role="entry", sequence=1)
    assert coid == "tsb:42:1001:entry:1"
    parsed = parse(coid)
    assert parsed.trade_chain_id == 42
    assert parsed.command_id == 1001
    assert parsed.role == "entry"
    assert parsed.sequence == 1


def test_build_tp():
    from src.runtime_v2.execution_gateway.client_order_id import build, parse
    coid = build(trade_chain_id=42, command_id=1004, role="tp", sequence=3)
    assert coid == "tsb:42:1004:tp:3"
    parsed = parse(coid)
    assert parsed.role == "tp"
    assert parsed.sequence == 3


def test_roundtrip(  ):
    from src.runtime_v2.execution_gateway.client_order_id import build, parse
    for role in ("entry", "sl", "tp"):
        coid = build(1, 2, role, 1)
        parsed = parse(coid)
        assert build(parsed.trade_chain_id, parsed.command_id, parsed.role, parsed.sequence) == coid


def test_parse_invalid_raises():
    from src.runtime_v2.execution_gateway.client_order_id import parse
    with pytest.raises(ValueError):
        parse("not-a-tsb-id")


def test_parse_wrong_prefix_raises():
    from src.runtime_v2.execution_gateway.client_order_id import parse
    with pytest.raises(ValueError):
        parse("other:42:1001:entry:1")
```

- [ ] **Step 2: Esegui i test per verificare che falliscono**

```bash
pytest tests/runtime_v2/execution_gateway/test_client_order_id.py -v
```

Expected: FAILED — ImportError.

- [ ] **Step 3: Implementa client_order_id.py**

```python
# src/runtime_v2/execution_gateway/client_order_id.py
from __future__ import annotations

from dataclasses import dataclass

_PREFIX = "tsb"
_VALID_ROLES = frozenset({"entry", "sl", "tp"})


@dataclass(frozen=True)
class ClientOrderId:
    trade_chain_id: int
    command_id: int
    role: str
    sequence: int

    def __str__(self) -> str:
        return f"{_PREFIX}:{self.trade_chain_id}:{self.command_id}:{self.role}:{self.sequence}"


def build(trade_chain_id: int, command_id: int, role: str, sequence: int) -> str:
    if role not in _VALID_ROLES:
        raise ValueError(f"Invalid role '{role}'. Must be one of {_VALID_ROLES}")
    return str(ClientOrderId(trade_chain_id, command_id, role, sequence))


def parse(client_order_id: str) -> ClientOrderId:
    parts = client_order_id.split(":")
    if len(parts) != 5 or parts[0] != _PREFIX:
        raise ValueError(f"Invalid client_order_id format: '{client_order_id}'")
    try:
        return ClientOrderId(
            trade_chain_id=int(parts[1]),
            command_id=int(parts[2]),
            role=parts[3],
            sequence=int(parts[4]),
        )
    except (ValueError, IndexError) as e:
        raise ValueError(f"Cannot parse client_order_id '{client_order_id}': {e}") from e


__all__ = ["ClientOrderId", "build", "parse"]
```

- [ ] **Step 4: Esegui i test**

```bash
pytest tests/runtime_v2/execution_gateway/test_client_order_id.py -v
```

Expected: tutti PASS.

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/execution_gateway/client_order_id.py tests/runtime_v2/execution_gateway/test_client_order_id.py
git commit -m "feat(prd05): client_order_id builder/parser — tsb:<chain>:<cmd>:<role>:<seq>"
```

---

## Task 6: ExecutionAdapter ABC + FakeAdapter

**Files:**
- Create: `src/runtime_v2/execution_gateway/adapters/base.py`
- Create: `src/runtime_v2/execution_gateway/adapters/fake.py`

- [ ] **Step 1: Crea adapters/base.py**

```python
# src/runtime_v2/execution_gateway/adapters/base.py
from __future__ import annotations

from abc import ABC, abstractmethod

from src.runtime_v2.execution_gateway.models import AdapterCapabilities, AdapterResult, RawAdapterOrder


class ExecutionAdapter(ABC):
    @abstractmethod
    def get_capabilities(self) -> AdapterCapabilities: ...

    @abstractmethod
    def set_leverage(
        self, symbol: str, leverage: int, execution_account_id: str
    ) -> None: ...

    @abstractmethod
    def place_order(
        self,
        *,
        command_type: str,
        payload: dict,
        client_order_id: str,
        execution_account_id: str,
        connector: str,
    ) -> AdapterResult: ...

    @abstractmethod
    def cancel_order(
        self,
        *,
        client_order_id: str,
        execution_account_id: str,
        connector: str,
    ) -> AdapterResult: ...

    @abstractmethod
    def get_order_status(
        self,
        *,
        client_order_id: str,
        execution_account_id: str,
    ) -> RawAdapterOrder | None: ...

    @abstractmethod
    def get_position_qty(
        self,
        *,
        symbol: str,
        side: str,
        execution_account_id: str,
    ) -> float | None: ...


__all__ = ["ExecutionAdapter"]
```

- [ ] **Step 2: Crea adapters/fake.py**

```python
# src/runtime_v2/execution_gateway/adapters/fake.py
from __future__ import annotations

from src.runtime_v2.execution_gateway.adapters.base import ExecutionAdapter
from src.runtime_v2.execution_gateway.models import AdapterCapabilities, AdapterResult, RawAdapterOrder


class FakeAdapter(ExecutionAdapter):
    """Adapter deterministico per test. Simula ACK immediato e fill controllabile."""

    def __init__(
        self,
        *,
        capabilities: AdapterCapabilities | None = None,
        fail_on: set[str] | None = None,          # command_type che falliscono
        simulate_timeout: bool = False,
        positions: dict[str, float] | None = None, # symbol+side -> qty
    ) -> None:
        self._capabilities = capabilities or AdapterCapabilities(
            place_entry=True,
            protective_stop_native=True,
            take_profit_native=True,
            bracket_order=False,
            move_stop=True,
            close_partial=True,
            close_full=True,
            executor_position=False,
        )
        self._fail_on = fail_on or set()
        self._simulate_timeout = simulate_timeout
        self._positions = positions or {}
        self._orders: dict[str, RawAdapterOrder] = {}
        self.calls: list[dict] = []  # audit per test

    def get_capabilities(self) -> AdapterCapabilities:
        return self._capabilities

    def set_leverage(self, symbol: str, leverage: int, execution_account_id: str) -> None:
        self.calls.append({"action": "set_leverage", "symbol": symbol, "leverage": leverage})

    def place_order(
        self,
        *,
        command_type: str,
        payload: dict,
        client_order_id: str,
        execution_account_id: str,
        connector: str,
    ) -> AdapterResult:
        self.calls.append({"action": "place_order", "command_type": command_type,
                           "client_order_id": client_order_id})
        if self._simulate_timeout:
            raise TimeoutError("fake timeout")
        if command_type in self._fail_on:
            return AdapterResult(success=False, error="fake_error",
                                 reason=f"command {command_type} set to fail")
        order = RawAdapterOrder(
            client_order_id=client_order_id,
            exchange_order_id=f"exch_{client_order_id}",
            adapter_order_id=f"hb_{client_order_id}",
            status="OPEN",
        )
        self._orders[client_order_id] = order
        return AdapterResult(
            success=True,
            adapter_order_id=order.adapter_order_id,
            exchange_order_id=order.exchange_order_id,
        )

    def cancel_order(
        self,
        *,
        client_order_id: str,
        execution_account_id: str,
        connector: str,
    ) -> AdapterResult:
        self.calls.append({"action": "cancel_order", "client_order_id": client_order_id})
        if client_order_id in self._orders:
            self._orders[client_order_id] = self._orders[client_order_id].model_copy(
                update={"status": "CANCELLED"}
            )
        return AdapterResult(success=True)

    def get_order_status(
        self,
        *,
        client_order_id: str,
        execution_account_id: str,
    ) -> RawAdapterOrder | None:
        return self._orders.get(client_order_id)

    def simulate_fill(self, client_order_id: str, price: float, qty: float) -> None:
        """Chiamato dai test per simulare un fill."""
        if client_order_id not in self._orders:
            raise KeyError(f"Order {client_order_id} not found in fake adapter")
        self._orders[client_order_id] = self._orders[client_order_id].model_copy(
            update={"status": "FILLED", "average_price": price, "filled_qty": qty}
        )

    def get_position_qty(
        self,
        *,
        symbol: str,
        side: str,
        execution_account_id: str,
    ) -> float | None:
        return self._positions.get(f"{symbol}:{side}")


__all__ = ["FakeAdapter"]
```

- [ ] **Step 3: Verifica sintassi**

```bash
python -c "from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter; print(FakeAdapter())"
```

Expected: nessun errore, stampa l'oggetto.

- [ ] **Step 4: Commit**

```bash
git add src/runtime_v2/execution_gateway/adapters/
git commit -m "feat(prd05): ExecutionAdapter ABC + FakeAdapter per test"
```

---

## Task 7: ExecutionCommandRepository esteso

**Files:**
- Create: `src/runtime_v2/execution_gateway/repositories.py`

- [ ] **Step 1: Crea repositories.py**

```python
# src/runtime_v2/execution_gateway/repositories.py
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from src.runtime_v2.lifecycle.models import ExecutionCommand


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cmd_from_row(row: tuple) -> ExecutionCommand:
    (command_id, trade_chain_id, command_type, status, payload_json,
     idempotency_key, created_at, updated_at) = row[:8]
    return ExecutionCommand(
        command_id=command_id,
        trade_chain_id=trade_chain_id,
        command_type=command_type,
        status=status,
        payload_json=payload_json or "{}",
        idempotency_key=idempotency_key,
        created_at=datetime.fromisoformat(created_at) if created_at else None,
        updated_at=datetime.fromisoformat(updated_at) if updated_at else None,
    )


_BASE_COLS = (
    "command_id, trade_chain_id, command_type, status, payload_json, "
    "idempotency_key, created_at, updated_at"
)


class GatewayCommandRepository:
    def __init__(self, db_path: str) -> None:
        self._db = db_path

    def get_pending_batch(self, limit: int = 100) -> list[ExecutionCommand]:
        conn = sqlite3.connect(self._db)
        try:
            rows = conn.execute(
                f"SELECT {_BASE_COLS} FROM ops_execution_commands "
                "WHERE status='PENDING' ORDER BY created_at LIMIT ?", (limit,)
            ).fetchall()
            return [_cmd_from_row(r) for r in rows]
        finally:
            conn.close()

    def get_retry_batch(self, limit: int = 100) -> list[ExecutionCommand]:
        now = _now()
        conn = sqlite3.connect(self._db)
        try:
            rows = conn.execute(
                f"SELECT {_BASE_COLS} FROM ops_execution_commands "
                "WHERE status='SENT' AND next_retry_at IS NOT NULL "
                "AND next_retry_at <= ? ORDER BY next_retry_at LIMIT ?",
                (now, limit),
            ).fetchall()
            return [_cmd_from_row(r) for r in rows]
        finally:
            conn.close()

    def get_waiting_on_open_chains(self, limit: int = 100) -> list[ExecutionCommand]:
        conn = sqlite3.connect(self._db)
        try:
            rows = conn.execute(
                f"SELECT c.{_BASE_COLS.replace(', ', ', c.')} "
                "FROM ops_execution_commands c "
                "JOIN ops_trade_chains t ON c.trade_chain_id = t.trade_chain_id "
                "WHERE c.status='WAITING_POSITION' AND t.lifecycle_state='OPEN' "
                "ORDER BY c.created_at LIMIT ?",
                (limit,),
            ).fetchall()
            return [_cmd_from_row(r) for r in rows]
        finally:
            conn.close()

    def get_sent_or_ack(self, limit: int = 500) -> list[tuple[ExecutionCommand, str | None]]:
        """Ritorna (cmd, client_order_id) per tutti i comandi SENT o ACK."""
        conn = sqlite3.connect(self._db)
        try:
            rows = conn.execute(
                f"SELECT {_BASE_COLS}, client_order_id FROM ops_execution_commands "
                "WHERE status IN ('SENT','ACK') AND client_order_id IS NOT NULL "
                "ORDER BY created_at LIMIT ?",
                (limit,),
            ).fetchall()
            return [(_cmd_from_row(r[:8]), r[8]) for r in rows]
        finally:
            conn.close()

    def count_active_tps(self, trade_chain_id: int) -> int:
        conn = sqlite3.connect(self._db)
        try:
            return conn.execute(
                "SELECT COUNT(*) FROM ops_execution_commands "
                "WHERE trade_chain_id=? AND command_type='PLACE_TAKE_PROFIT' "
                "AND status IN ('PENDING','SENT','ACK','WAITING_POSITION')",
                (trade_chain_id,),
            ).fetchone()[0]
        finally:
            conn.close()

    def mark_sent(
        self,
        command_id: int,
        *,
        client_order_id: str,
        adapter: str,
        execution_account_id: str,
        adapter_order_id: str | None = None,
        exchange_order_id: str | None = None,
    ) -> None:
        now = _now()
        result = {"adapter_order_id": adapter_order_id,
                  "exchange_order_id": exchange_order_id, "error": None,
                  "reason": None, "warnings": []}
        conn = sqlite3.connect(self._db)
        try:
            conn.execute(
                "UPDATE ops_execution_commands SET status='SENT', adapter=?, "
                "execution_account_id=?, client_order_id=?, result_payload_json=?, "
                "sent_at=?, updated_at=? WHERE command_id=?",
                (adapter, execution_account_id, client_order_id,
                 json.dumps(result), now, now, command_id),
            )
            conn.commit()
        finally:
            conn.close()

    def mark_ack(self, command_id: int) -> None:
        now = _now()
        conn = sqlite3.connect(self._db)
        try:
            conn.execute(
                "UPDATE ops_execution_commands SET status='ACK', "
                "acknowledged_at=?, updated_at=? WHERE command_id=?",
                (now, now, command_id),
            )
            conn.commit()
        finally:
            conn.close()

    def mark_done(self, command_id: int, result: dict | None = None) -> None:
        now = _now()
        conn = sqlite3.connect(self._db)
        try:
            conn.execute(
                "UPDATE ops_execution_commands SET status='DONE', "
                "result_payload_json=?, completed_at=?, updated_at=? WHERE command_id=?",
                (json.dumps(result or {}), now, now, command_id),
            )
            conn.commit()
        finally:
            conn.close()

    def mark_failed(self, command_id: int, *, reason: str) -> None:
        now = _now()
        result = {"error": reason, "reason": reason}
        conn = sqlite3.connect(self._db)
        try:
            conn.execute(
                "UPDATE ops_execution_commands SET status='FAILED', "
                "result_payload_json=?, updated_at=? WHERE command_id=?",
                (json.dumps(result), now, command_id),
            )
            conn.commit()
        finally:
            conn.close()

    def mark_review_required(self, command_id: int, *, reason: str) -> None:
        now = _now()
        result = {"error": None, "reason": reason}
        conn = sqlite3.connect(self._db)
        try:
            conn.execute(
                "UPDATE ops_execution_commands SET status='REVIEW_REQUIRED', "
                "result_payload_json=?, updated_at=? WHERE command_id=?",
                (json.dumps(result), now, command_id),
            )
            conn.commit()
        finally:
            conn.close()

    def mark_waiting_position(self, command_id: int) -> None:
        now = _now()
        conn = sqlite3.connect(self._db)
        try:
            conn.execute(
                "UPDATE ops_execution_commands SET status='WAITING_POSITION', "
                "updated_at=? WHERE command_id=?",
                (now, command_id),
            )
            conn.commit()
        finally:
            conn.close()

    def mark_retry(self, command_id: int, *, retry_count: int, next_retry_at: str) -> None:
        now = _now()
        conn = sqlite3.connect(self._db)
        try:
            conn.execute(
                "UPDATE ops_execution_commands SET retry_count=?, "
                "next_retry_at=?, updated_at=? WHERE command_id=?",
                (retry_count, next_retry_at, now, command_id),
            )
            conn.commit()
        finally:
            conn.close()

    def get_entry_client_order_id(self, trade_chain_id: int) -> str | None:
        conn = sqlite3.connect(self._db)
        try:
            row = conn.execute(
                "SELECT client_order_id FROM ops_execution_commands "
                "WHERE trade_chain_id=? AND command_type='PLACE_ENTRY' "
                "AND client_order_id IS NOT NULL LIMIT 1",
                (trade_chain_id,),
            ).fetchone()
            return row[0] if row else None
        finally:
            conn.close()


__all__ = ["GatewayCommandRepository"]
```

- [ ] **Step 2: Verifica sintassi**

```bash
python -c "from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add src/runtime_v2/execution_gateway/repositories.py
git commit -m "feat(prd05): GatewayCommandRepository — query PENDING/retry/WAITING + mark_* methods"
```

---

## Task 8: ExecutionGateway

**Files:**
- Create: `src/runtime_v2/execution_gateway/gateway.py`
- Create: `tests/runtime_v2/execution_gateway/test_gateway.py`

- [ ] **Step 1: Scrivi i test**

```python
# tests/runtime_v2/execution_gateway/test_gateway.py
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


def _insert_chain(db_path: str, chain_id: int = 1, account_id: str = "acc_1") -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_trade_chains (trade_chain_id, source_enrichment_id, "
        "canonical_message_id, raw_message_id, trader_id, account_id, symbol, side, "
        "lifecycle_state, entry_mode, management_plan_json, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))",
        (chain_id, 1, 10, 100, "trader_a", account_id,
         "BTC/USDT", "LONG", "WAITING_ENTRY", "ONE_SHOT", "{}"),
    )
    conn.commit()
    conn.close()


def _insert_cmd(db_path: str, cmd_id: int, chain_id: int = 1,
                cmd_type: str = "PLACE_ENTRY",
                payload: dict | None = None) -> None:
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_execution_commands "
        "(command_id, trade_chain_id, command_type, status, payload_json, "
        "idempotency_key, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (cmd_id, chain_id, cmd_type, "PENDING",
         json.dumps(payload or {}), f"idem:{cmd_id}", now, now),
    )
    conn.commit()
    conn.close()


@pytest.fixture
def ops_db(tmp_path):
    db = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db)
    _insert_chain(db)
    return db


def test_place_entry_pending_to_sent(ops_db):
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_cmd(ops_db, 1001, payload={
        "symbol": "BTC/USDT", "side": "LONG",
        "entry_type": "LIMIT", "price": 50000.0, "qty": 0.02, "sequence": 1,
    })
    repo = GatewayCommandRepository(ops_db)
    gw = ExecutionGateway(
        config=ExecutionConfigLoader("config/execution.yaml").load(),
        adapter_registry={"hummingbot_api_paper": FakeAdapter()},
        repo=repo,
    )
    cmd = repo.get_pending_batch()[0]
    gw.process(cmd, account_id="acc_1")

    conn = sqlite3.connect(ops_db)
    status, coid = conn.execute(
        "SELECT status, client_order_id FROM ops_execution_commands WHERE command_id=1001"
    ).fetchone()
    conn.close()
    assert status == "SENT"
    assert coid == "tsb:1:1001:entry:1"


def test_capability_missing_produces_review_required(ops_db):
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.models import AdapterCapabilities
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_cmd(ops_db, 1002, cmd_type="PLACE_PROTECTIVE_STOP", payload={
        "symbol": "BTC/USDT", "side": "LONG",
        "stop_price": 49000.0, "qty": 0.02, "reduce_only": True,
    })
    repo = GatewayCommandRepository(ops_db)
    gw = ExecutionGateway(
        config=ExecutionConfigLoader("config/execution.yaml").load(),
        adapter_registry={"hummingbot_api_paper": FakeAdapter(
            capabilities=AdapterCapabilities(protective_stop_native=False)
        )},
        repo=repo,
    )
    cmd = repo.get_pending_batch()[0]
    gw.process(cmd, account_id="acc_1")

    conn = sqlite3.connect(ops_db)
    status = conn.execute(
        "SELECT status FROM ops_execution_commands WHERE command_id=1002"
    ).fetchone()[0]
    conn.close()
    assert status == "REVIEW_REQUIRED"


def test_adapter_error_sets_retry(ops_db):
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_cmd(ops_db, 1003, payload={
        "symbol": "BTC/USDT", "side": "LONG",
        "entry_type": "LIMIT", "price": 50000.0, "qty": 0.02, "sequence": 1,
    })
    repo = GatewayCommandRepository(ops_db)
    gw = ExecutionGateway(
        config=ExecutionConfigLoader("config/execution.yaml").load(),
        adapter_registry={"hummingbot_api_paper": FakeAdapter(simulate_timeout=True)},
        repo=repo,
    )
    cmd = repo.get_pending_batch()[0]
    gw.process(cmd, account_id="acc_1")

    conn = sqlite3.connect(ops_db)
    retry_count = conn.execute(
        "SELECT retry_count FROM ops_execution_commands WHERE command_id=1003"
    ).fetchone()[0]
    conn.close()
    assert retry_count == 1


def test_live_trading_blocked(ops_db):
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository
    import yaml

    # Crea config con live=true per testare il blocco
    raw = yaml.safe_load(open("config/execution.yaml").read())
    raw["execution"]["adapters"]["hummingbot_api_paper"]["live_safety"]["allow_live_trading"] = True
    raw["execution"]["adapters"]["hummingbot_api_paper"]["mode"] = "live"

    from src.runtime_v2.execution_gateway.models import ExecutionConfig
    config = ExecutionConfig.model_validate(raw["execution"])

    _insert_cmd(ops_db, 1004, payload={
        "symbol": "BTC/USDT", "side": "LONG",
        "entry_type": "LIMIT", "price": 50000.0, "qty": 0.02, "sequence": 1,
    })
    repo = GatewayCommandRepository(ops_db)
    gw = ExecutionGateway(
        config=config,
        adapter_registry={"hummingbot_api_paper": FakeAdapter()},
        repo=repo,
    )
    cmd = repo.get_pending_batch()[0]
    gw.process(cmd, account_id="acc_1")

    conn = sqlite3.connect(ops_db)
    status = conn.execute(
        "SELECT status FROM ops_execution_commands WHERE command_id=1004"
    ).fetchone()[0]
    conn.close()
    assert status == "REVIEW_REQUIRED"
```

- [ ] **Step 2: Esegui i test per verificare che falliscono**

```bash
pytest tests/runtime_v2/execution_gateway/test_gateway.py -v
```

Expected: FAILED — ImportError.

- [ ] **Step 3: Implementa gateway.py**

```python
# src/runtime_v2/execution_gateway/gateway.py
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from src.runtime_v2.execution_gateway import client_order_id as coid_mod
from src.runtime_v2.execution_gateway.adapters.base import ExecutionAdapter
from src.runtime_v2.execution_gateway.models import ExecutionConfig
from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository
from src.runtime_v2.lifecycle.models import ExecutionCommand

logger = logging.getLogger(__name__)

_CAPABILITY_MAP: dict[str, str] = {
    "PLACE_ENTRY": "place_entry",
    "PLACE_PROTECTIVE_STOP": "protective_stop_native",
    "PLACE_TAKE_PROFIT": "take_profit_native",
    "MOVE_STOP_TO_BREAKEVEN": "move_stop",
    "MOVE_STOP": "move_stop",
    "CANCEL_PENDING_ENTRY": "place_entry",
    "CLOSE_PARTIAL": "close_partial",
    "CLOSE_FULL": "close_full",
}

_ROLE_MAP: dict[str, str] = {
    "PLACE_ENTRY": "entry",
    "PLACE_PROTECTIVE_STOP": "sl",
    "PLACE_TAKE_PROFIT": "tp",
    "MOVE_STOP_TO_BREAKEVEN": "sl",
    "MOVE_STOP": "sl",
    "CANCEL_PENDING_ENTRY": "entry",
    "CLOSE_PARTIAL": "entry",
    "CLOSE_FULL": "entry",
}


class ExecutionGateway:
    def __init__(
        self,
        config: ExecutionConfig,
        adapter_registry: dict[str, ExecutionAdapter],
        repo: GatewayCommandRepository,
    ) -> None:
        self._config = config
        self._adapters = adapter_registry
        self._repo = repo
        self._leverage_set: set[str] = set()  # "execution_account_id:symbol"

    def process(self, cmd: ExecutionCommand, *, account_id: str) -> None:
        routing, adapter_cfg = self._config.resolve_routing(account_id)
        adapter = self._adapters.get(routing.adapter)
        if adapter is None:
            self._repo.mark_review_required(
                cmd.command_id, reason=f"adapter_not_found:{routing.adapter}"
            )
            return

        # Live safety check — mai in MVP
        if adapter_cfg.mode == "live" and not adapter_cfg.live_safety.allow_live_trading:
            self._repo.mark_review_required(
                cmd.command_id, reason="live_trading_not_allowed"
            )
            return

        # Capability check
        cap_field = _CAPABILITY_MAP.get(cmd.command_type)
        if cap_field and not getattr(adapter.get_capabilities(), cap_field, False):
            self._repo.mark_review_required(
                cmd.command_id,
                reason=f"capability_missing:{cap_field}",
            )
            return

        payload = json.loads(cmd.payload_json)
        symbol = payload.get("symbol", "")

        # Set leverage (una volta per account+symbol)
        leverage_key = f"{routing.execution_account_id}:{symbol}"
        if leverage_key not in self._leverage_set and adapter_cfg.leverage > 1:
            try:
                adapter.set_leverage(symbol, adapter_cfg.leverage, routing.execution_account_id)
                self._leverage_set.add(leverage_key)
            except Exception as e:
                logger.warning("set_leverage failed for %s: %s", leverage_key, e)

        # Genera client_order_id
        role = _ROLE_MAP.get(cmd.command_type, "entry")
        sequence = payload.get("sequence", payload.get("tp_sequence", 1))
        client_order_id = coid_mod.build(
            trade_chain_id=cmd.trade_chain_id,
            command_id=cmd.command_id,
            role=role,
            sequence=sequence,
        )

        # Idempotenza — controlla se già inviato
        existing = adapter.get_order_status(
            client_order_id=client_order_id,
            execution_account_id=routing.execution_account_id,
        )
        if existing is not None:
            logger.info("command %s already sent, recovering state", cmd.command_id)
            self._repo.mark_ack(cmd.command_id)
            return

        # Invia all'adapter
        try:
            result = adapter.place_order(
                command_type=cmd.command_type,
                payload=payload,
                client_order_id=client_order_id,
                execution_account_id=routing.execution_account_id,
                connector=adapter_cfg.connector,
            )
        except Exception as e:
            self._handle_error(cmd, adapter_cfg, str(e))
            return

        if not result.success:
            self._repo.mark_failed(cmd.command_id, reason=result.reason or result.error or "unknown")
            return

        self._repo.mark_sent(
            cmd.command_id,
            client_order_id=client_order_id,
            adapter=routing.adapter,
            execution_account_id=routing.execution_account_id,
            adapter_order_id=result.adapter_order_id,
            exchange_order_id=result.exchange_order_id,
        )

    def _handle_error(
        self, cmd: ExecutionCommand, adapter_cfg, error_str: str
    ) -> None:
        retry_cfg = adapter_cfg.retry
        current_retry = 0  # sarà letto dal DB in una versione più avanzata
        conn_import = __import__("sqlite3")
        # Leggi retry_count corrente
        conn = conn_import.connect(self._repo._db)
        row = conn.execute(
            "SELECT retry_count FROM ops_execution_commands WHERE command_id=?",
            (cmd.command_id,),
        ).fetchone()
        conn.close()
        current_retry = row[0] if row else 0

        if current_retry >= retry_cfg.max_attempts:
            self._repo.mark_failed(cmd.command_id, reason=error_str)
            return

        backoff = retry_cfg.backoff_seconds[min(current_retry, len(retry_cfg.backoff_seconds) - 1)]
        next_retry = (datetime.now(timezone.utc) + timedelta(seconds=backoff)).isoformat()
        self._repo.mark_retry(
            cmd.command_id,
            retry_count=current_retry + 1,
            next_retry_at=next_retry,
        )


__all__ = ["ExecutionGateway"]
```

- [ ] **Step 4: Esegui i test**

```bash
pytest tests/runtime_v2/execution_gateway/test_gateway.py -v
```

Expected: tutti PASS.

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/execution_gateway/gateway.py tests/runtime_v2/execution_gateway/test_gateway.py
git commit -m "feat(prd05): ExecutionGateway — capability check, client_order_id, retry, live safety"
```

---

## Task 9: ExecutionCommandWorker

**Files:**
- Create: `src/runtime_v2/execution_gateway/command_worker.py`
- Create: `tests/runtime_v2/execution_gateway/test_command_worker.py`

- [ ] **Step 1: Scrivi i test**

```python
# tests/runtime_v2/execution_gateway/test_command_worker.py
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


@pytest.fixture
def ops_db(tmp_path):
    db = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db)
    return db


def _insert_chain(db_path, chain_id=1, state="WAITING_ENTRY", account_id="acc_1"):
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_trade_chains (trade_chain_id, source_enrichment_id, "
        "canonical_message_id, raw_message_id, trader_id, account_id, symbol, side, "
        "lifecycle_state, entry_mode, management_plan_json, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (chain_id, chain_id, chain_id * 10, chain_id * 100, "trader_a",
         account_id, "BTC/USDT", "LONG", state, "ONE_SHOT", "{}", now, now),
    )
    conn.commit()
    conn.close()


def _insert_cmd(db_path, cmd_id, chain_id=1, cmd_type="PLACE_ENTRY",
                status="PENDING", payload=None):
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_execution_commands "
        "(command_id, trade_chain_id, command_type, status, payload_json, "
        "idempotency_key, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (cmd_id, chain_id, cmd_type, status,
         json.dumps(payload or {"symbol": "BTC/USDT", "side": "LONG",
                                "entry_type": "LIMIT", "price": 50000.0,
                                "qty": 0.02, "sequence": 1}),
         f"idem:{cmd_id}", now, now),
    )
    conn.commit()
    conn.close()


def _make_worker(ops_db):
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.command_worker import ExecutionCommandWorker
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    repo = GatewayCommandRepository(ops_db)
    adapter = FakeAdapter()
    gw = ExecutionGateway(
        config=ExecutionConfigLoader("config/execution.yaml").load(),
        adapter_registry={"hummingbot_api_paper": adapter},
        repo=repo,
    )
    return ExecutionCommandWorker(ops_db_path=ops_db, gateway=gw, repo=repo), adapter


def test_pending_command_gets_sent(ops_db):
    _insert_chain(ops_db)
    _insert_cmd(ops_db, 1001)
    worker, _ = _make_worker(ops_db)
    processed = worker.run_once()
    assert processed == 1
    conn = sqlite3.connect(ops_db)
    status = conn.execute(
        "SELECT status FROM ops_execution_commands WHERE command_id=1001"
    ).fetchone()[0]
    conn.close()
    assert status == "SENT"


def test_double_run_does_not_resend(ops_db):
    _insert_chain(ops_db)
    _insert_cmd(ops_db, 1002)
    worker, adapter = _make_worker(ops_db)
    worker.run_once()
    worker.run_once()
    place_calls = [c for c in adapter.calls if c["action"] == "place_order"]
    assert len(place_calls) == 1


def test_waiting_position_on_open_chain_becomes_pending(ops_db):
    _insert_chain(ops_db, state="OPEN")
    _insert_cmd(ops_db, 1003, cmd_type="PLACE_TAKE_PROFIT", status="WAITING_POSITION",
                payload={"symbol": "BTC/USDT", "side": "LONG",
                         "tp_sequence": 1, "price": 51000.0,
                         "close_pct": 100.0, "reduce_only": True})
    worker, _ = _make_worker(ops_db)
    worker.run_once()
    conn = sqlite3.connect(ops_db)
    status = conn.execute(
        "SELECT status FROM ops_execution_commands WHERE command_id=1003"
    ).fetchone()[0]
    conn.close()
    assert status == "SENT"
```

- [ ] **Step 2: Esegui i test per verificare che falliscono**

```bash
pytest tests/runtime_v2/execution_gateway/test_command_worker.py -v
```

Expected: FAILED — ImportError.

- [ ] **Step 3: Implementa command_worker.py**

```python
# src/runtime_v2/execution_gateway/command_worker.py
from __future__ import annotations

import logging
import sqlite3

from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

logger = logging.getLogger(__name__)


class ExecutionCommandWorker:
    def __init__(
        self,
        ops_db_path: str,
        gateway: ExecutionGateway,
        repo: GatewayCommandRepository,
        batch_size: int = 100,
    ) -> None:
        self._ops_db = ops_db_path
        self._gw = gateway
        self._repo = repo
        self._batch_size = batch_size

    def run_once(self) -> int:
        processed = 0

        # Query 1: PENDING
        for cmd in self._repo.get_pending_batch(self._batch_size):
            account_id = self._get_account_id(cmd.trade_chain_id)
            if account_id is None:
                logger.warning("no account_id for chain %s", cmd.trade_chain_id)
                continue
            try:
                self._gw.process(cmd, account_id=account_id)
                processed += 1
            except Exception:
                logger.exception("gateway error for command %s", cmd.command_id)

        # Query 2: retry (SENT con next_retry_at scaduto)
        for cmd in self._repo.get_retry_batch(self._batch_size):
            account_id = self._get_account_id(cmd.trade_chain_id)
            if account_id is None:
                continue
            try:
                self._gw.process(cmd, account_id=account_id)
                processed += 1
            except Exception:
                logger.exception("gateway retry error for command %s", cmd.command_id)

        # Query 3: WAITING_POSITION su chain OPEN → riattiva come PENDING
        waiting = self._repo.get_waiting_on_open_chains(self._batch_size)
        for cmd in waiting:
            self._repo.mark_sent.__func__  # verifica che il metodo esista
            # Riporta a PENDING e poi processa
            conn = sqlite3.connect(self._ops_db)
            try:
                conn.execute(
                    "UPDATE ops_execution_commands SET status='PENDING', updated_at=datetime('now') "
                    "WHERE command_id=?", (cmd.command_id,)
                )
                conn.commit()
            finally:
                conn.close()
            account_id = self._get_account_id(cmd.trade_chain_id)
            if account_id is None:
                continue
            try:
                fresh = self._repo.get_pending_batch(1)
                if fresh and fresh[0].command_id == cmd.command_id:
                    self._gw.process(fresh[0], account_id=account_id)
                    processed += 1
            except Exception:
                logger.exception("gateway waiting error for command %s", cmd.command_id)

        return processed

    def _get_account_id(self, trade_chain_id: int) -> str | None:
        conn = sqlite3.connect(self._ops_db)
        try:
            row = conn.execute(
                "SELECT account_id FROM ops_trade_chains WHERE trade_chain_id=?",
                (trade_chain_id,),
            ).fetchone()
            return row[0] if row else None
        finally:
            conn.close()


__all__ = ["ExecutionCommandWorker"]
```

- [ ] **Step 4: Esegui i test**

```bash
pytest tests/runtime_v2/execution_gateway/test_command_worker.py -v
```

Expected: tutti PASS.

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/execution_gateway/command_worker.py tests/runtime_v2/execution_gateway/test_command_worker.py
git commit -m "feat(prd05): ExecutionCommandWorker — 3 query paths PENDING/retry/WAITING_POSITION"
```

---

## Task 10: ExchangeEventSyncWorker

**Files:**
- Create: `src/runtime_v2/execution_gateway/event_sync.py`
- Create: `tests/runtime_v2/execution_gateway/test_event_sync.py`

- [ ] **Step 1: Scrivi i test**

```python
# tests/runtime_v2/execution_gateway/test_event_sync.py
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


@pytest.fixture
def ops_db(tmp_path):
    db = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db)
    return db


def _insert_sent_cmd(db_path, cmd_id, chain_id, cmd_type, client_order_id):
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_execution_commands "
        "(command_id, trade_chain_id, command_type, status, payload_json, "
        "idempotency_key, client_order_id, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (cmd_id, chain_id, cmd_type, "SENT", "{}",
         f"idem:{cmd_id}", client_order_id, now, now),
    )
    conn.commit()
    conn.close()


def test_entry_fill_writes_entry_filled_event(ops_db):
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_sent_cmd(ops_db, 1001, 42, "PLACE_ENTRY", "tsb:42:1001:entry:1")
    adapter = FakeAdapter()
    # Simula fill entry
    adapter.place_order(
        command_type="PLACE_ENTRY",
        payload={}, client_order_id="tsb:42:1001:entry:1",
        execution_account_id="acc", connector="c",
    )
    adapter.simulate_fill("tsb:42:1001:entry:1", price=50050.0, qty=0.02)

    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(
        ops_db_path=ops_db,
        adapter=adapter,
        repo=repo,
        execution_account_id="acc",
    )
    worker.run_once()

    conn = sqlite3.connect(ops_db)
    events = conn.execute(
        "SELECT event_type, payload_json FROM ops_exchange_events"
    ).fetchall()
    conn.close()
    assert len(events) == 1
    assert events[0][0] == "ENTRY_FILLED"
    payload = json.loads(events[0][1])
    assert payload["fill_price"] == 50050.0
    assert payload["filled_qty"] == 0.02


def test_tp_fill_last_writes_is_final_true(ops_db):
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_sent_cmd(ops_db, 2001, 42, "PLACE_TAKE_PROFIT", "tsb:42:2001:tp:1")
    adapter = FakeAdapter()
    adapter.place_order(
        command_type="PLACE_TAKE_PROFIT",
        payload={}, client_order_id="tsb:42:2001:tp:1",
        execution_account_id="acc", connector="c",
    )
    adapter.simulate_fill("tsb:42:2001:tp:1", price=51000.0, qty=0.02)

    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(ops_db_path=ops_db, adapter=adapter,
                                     repo=repo, execution_account_id="acc")
    worker.run_once()

    conn = sqlite3.connect(ops_db)
    payload = json.loads(conn.execute(
        "SELECT payload_json FROM ops_exchange_events WHERE event_type='TP_FILLED'"
    ).fetchone()[0])
    conn.close()
    assert payload["is_final"] is True
    assert payload["tp_level"] == 1


def test_idempotency_no_duplicate_events(ops_db):
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_sent_cmd(ops_db, 3001, 42, "PLACE_ENTRY", "tsb:42:3001:entry:1")
    adapter = FakeAdapter()
    adapter.place_order(
        command_type="PLACE_ENTRY",
        payload={}, client_order_id="tsb:42:3001:entry:1",
        execution_account_id="acc", connector="c",
    )
    adapter.simulate_fill("tsb:42:3001:entry:1", price=50000.0, qty=0.01)

    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(ops_db_path=ops_db, adapter=adapter,
                                     repo=repo, execution_account_id="acc")
    worker.run_once()
    worker.run_once()

    conn = sqlite3.connect(ops_db)
    count = conn.execute("SELECT COUNT(*) FROM ops_exchange_events").fetchone()[0]
    conn.close()
    assert count == 1
```

- [ ] **Step 2: Esegui i test per verificare che falliscono**

```bash
pytest tests/runtime_v2/execution_gateway/test_event_sync.py -v
```

Expected: FAILED — ImportError.

- [ ] **Step 3: Implementa event_sync.py**

```python
# src/runtime_v2/execution_gateway/event_sync.py
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone

from src.runtime_v2.execution_gateway import client_order_id as coid_mod
from src.runtime_v2.execution_gateway.adapters.base import ExecutionAdapter
from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ExchangeEventSyncWorker:
    def __init__(
        self,
        ops_db_path: str,
        adapter: ExecutionAdapter,
        repo: GatewayCommandRepository,
        execution_account_id: str,
    ) -> None:
        self._ops_db = ops_db_path
        self._adapter = adapter
        self._repo = repo
        self._execution_account_id = execution_account_id

    def run_once(self) -> int:
        active = self._repo.get_sent_or_ack()
        processed = 0

        for cmd, client_order_id in active:
            if not client_order_id:
                continue
            try:
                raw = self._adapter.get_order_status(
                    client_order_id=client_order_id,
                    execution_account_id=self._execution_account_id,
                )
                if raw and raw.is_filled:
                    self._normalize_and_save(client_order_id, raw)
                    self._repo.mark_done(cmd.command_id)
                    processed += 1
            except Exception:
                logger.exception("sync error for %s", client_order_id)

        return processed

    def _normalize_and_save(self, client_order_id: str, raw) -> None:
        try:
            coid = coid_mod.parse(client_order_id)
        except ValueError:
            logger.warning("cannot parse client_order_id: %s", client_order_id)
            return

        exchange_order_id = raw.exchange_order_id or client_order_id

        if coid.role == "entry":
            event_type = "ENTRY_FILLED"
            payload = {
                "fill_price": raw.average_price,
                "filled_qty": raw.filled_qty,
                "command_id": coid.command_id,
            }
        elif coid.role == "sl":
            event_type = "SL_FILLED"
            payload = {
                "fill_price": raw.average_price,
                "filled_qty": raw.filled_qty,
                "command_id": coid.command_id,
            }
        elif coid.role == "tp":
            remaining = self._repo.count_active_tps(coid.trade_chain_id)
            is_final = remaining <= 1
            event_type = "TP_FILLED"
            payload = {
                "tp_level": coid.sequence,
                "is_final": is_final,
                "fill_price": raw.average_price,
                "filled_qty": raw.filled_qty,
                "command_id": coid.command_id,
            }
        else:
            logger.warning("unknown role '%s' in %s", coid.role, client_order_id)
            return

        idempotency_key = f"{event_type}:{coid.trade_chain_id}:{exchange_order_id}"
        now = _now()
        conn = sqlite3.connect(self._ops_db)
        try:
            conn.execute(
                "INSERT OR IGNORE INTO ops_exchange_events "
                "(trade_chain_id, event_type, payload_json, processing_status, "
                "idempotency_key, received_at) VALUES (?,?,?,?,?,?)",
                (coid.trade_chain_id, event_type, json.dumps(payload),
                 "NEW", idempotency_key, now),
            )
            conn.commit()
        finally:
            conn.close()


__all__ = ["ExchangeEventSyncWorker"]
```

- [ ] **Step 4: Esegui i test**

```bash
pytest tests/runtime_v2/execution_gateway/test_event_sync.py -v
```

Expected: tutti PASS.

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/execution_gateway/event_sync.py tests/runtime_v2/execution_gateway/test_event_sync.py
git commit -m "feat(prd05): ExchangeEventSyncWorker — polling smart, normalizzazione role→event_type"
```

---

## Task 11: HummingbotApiPaperAdapter (gated)

**Files:**
- Create: `src/runtime_v2/execution_gateway/adapters/hummingbot_api_paper.py`
- Create: `tests/runtime_v2/execution_gateway/test_hummingbot_adapter.py`

- [ ] **Step 1: Implementa l'adapter**

```python
# src/runtime_v2/execution_gateway/adapters/hummingbot_api_paper.py
from __future__ import annotations

import logging

import httpx

from src.runtime_v2.execution_gateway.adapters.base import ExecutionAdapter
from src.runtime_v2.execution_gateway.models import AdapterCapabilities, AdapterResult, RawAdapterOrder

logger = logging.getLogger(__name__)

_SIDE_MAP = {"LONG": "BUY", "SHORT": "SELL"}
_CLOSE_SIDE_MAP = {"LONG": "SELL", "SHORT": "BUY"}


class HummingbotApiPaperAdapter(ExecutionAdapter):
    def __init__(self, base_url: str, connector: str, timeout: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._connector = connector
        self._client = httpx.Client(base_url=self._base_url, timeout=timeout)

    def get_capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            place_entry=True,
            protective_stop_native=True,
            take_profit_native=True,
            bracket_order=False,
            move_stop=True,
            close_partial=True,
            close_full=True,
            executor_position=False,
        )

    def set_leverage(self, symbol: str, leverage: int, execution_account_id: str) -> None:
        trading_pair = symbol.replace("/", "-")
        self._client.post("/trading/leverage", json={
            "account_name": execution_account_id,
            "connector_name": self._connector,
            "trading_pair": trading_pair,
            "leverage": leverage,
        }).raise_for_status()

    def place_order(
        self,
        *,
        command_type: str,
        payload: dict,
        client_order_id: str,
        execution_account_id: str,
        connector: str,
    ) -> AdapterResult:
        try:
            body = self._build_order_body(
                command_type, payload, client_order_id, execution_account_id
            )
            resp = self._client.post("/trading/orders", json=body)
            resp.raise_for_status()
            data = resp.json()
            return AdapterResult(
                success=True,
                adapter_order_id=str(data.get("id", "")),
                exchange_order_id=str(data.get("exchange_order_id", "")),
            )
        except httpx.HTTPStatusError as e:
            return AdapterResult(success=False, error=str(e), reason="exchange_rejected")
        except Exception as e:
            raise  # timeout e connection errors vanno al retry del gateway

    def cancel_order(
        self,
        *,
        client_order_id: str,
        execution_account_id: str,
        connector: str,
    ) -> AdapterResult:
        try:
            resp = self._client.post(
                f"/trading/{execution_account_id}/{connector}/orders/{client_order_id}/cancel"
            )
            resp.raise_for_status()
            return AdapterResult(success=True)
        except httpx.HTTPStatusError as e:
            return AdapterResult(success=False, error=str(e))

    def get_order_status(
        self,
        *,
        client_order_id: str,
        execution_account_id: str,
    ) -> RawAdapterOrder | None:
        try:
            resp = self._client.post("/trading/orders/search", json={
                "client_order_id": client_order_id,
                "account_name": execution_account_id,
            })
            resp.raise_for_status()
            data = resp.json()
            orders = data.get("data") or data if isinstance(data, list) else []
            if not orders:
                return None
            o = orders[0]
            status = "FILLED" if o.get("is_done") else "OPEN"
            return RawAdapterOrder(
                client_order_id=client_order_id,
                exchange_order_id=str(o.get("exchange_order_id", "")),
                adapter_order_id=str(o.get("id", "")),
                status=status,
                filled_qty=float(o.get("executed_amount_base", 0)),
                average_price=float(o.get("average_executed_price", 0)) or None,
            )
        except Exception:
            logger.warning("get_order_status failed for %s", client_order_id)
            return None

    def get_position_qty(
        self,
        *,
        symbol: str,
        side: str,
        execution_account_id: str,
    ) -> float | None:
        try:
            trading_pair = symbol.replace("/", "-")
            resp = self._client.get(f"/trading/positions/{execution_account_id}")
            resp.raise_for_status()
            positions = resp.json()
            for p in positions:
                if p.get("trading_pair") == trading_pair and p.get("side") == side:
                    return float(p.get("amount", 0))
            return None
        except Exception:
            logger.warning("get_position_qty failed for %s %s", symbol, side)
            return None

    def _build_order_body(
        self, command_type: str, payload: dict,
        client_order_id: str, execution_account_id: str
    ) -> dict:
        symbol = payload["symbol"]
        side = payload["side"]
        trading_pair = symbol.replace("/", "-")

        base = {
            "account_name": execution_account_id,
            "connector_name": self._connector,
            "trading_pair": trading_pair,
            "client_order_id": client_order_id,
        }

        if command_type == "PLACE_ENTRY":
            entry_type = payload["entry_type"]
            base.update({
                "trade_type": _SIDE_MAP[side],
                "order_type": entry_type,
                "amount": payload["qty"],
                "position_action": "OPEN",
            })
            if entry_type == "LIMIT":
                base["price"] = payload["price"]

        elif command_type == "PLACE_PROTECTIVE_STOP":
            base.update({
                "trade_type": _CLOSE_SIDE_MAP[side],
                "order_type": "STOP_LOSS",
                "price": payload["stop_price"],
                "amount": payload["qty"],
                "position_action": "CLOSE",
                "reduce_only": True,
            })

        elif command_type == "PLACE_TAKE_PROFIT":
            base.update({
                "trade_type": _CLOSE_SIDE_MAP[side],
                "order_type": "LIMIT",
                "price": payload["price"],
                "amount": payload.get("qty", 0),
                "position_action": "CLOSE",
                "reduce_only": True,
            })

        elif command_type in ("CLOSE_PARTIAL", "CLOSE_FULL"):
            base.update({
                "trade_type": _CLOSE_SIDE_MAP[side],
                "order_type": "MARKET",
                "amount": payload.get("qty", 0),
                "position_action": "CLOSE",
                "reduce_only": True,
            })

        return base


__all__ = ["HummingbotApiPaperAdapter"]
```

- [ ] **Step 2: Crea test gated**

```python
# tests/runtime_v2/execution_gateway/test_hummingbot_adapter.py
"""
Test gated — girano solo con Hummingbot API attiva.
Eseguire con: RUN_HUMMINGBOT_API_TESTS=1 HUMMINGBOT_API_URL=http://localhost:8000 pytest
"""
from __future__ import annotations

import os
import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("RUN_HUMMINGBOT_API_TESTS"),
    reason="Set RUN_HUMMINGBOT_API_TESTS=1 to run",
)

HUMMINGBOT_URL = os.environ.get("HUMMINGBOT_API_URL", "http://localhost:8000")
CONNECTOR = os.environ.get("HUMMINGBOT_CONNECTOR", "bybit_perpetual_paper_trade")
ACCOUNT = os.environ.get("HUMMINGBOT_ACCOUNT", "bybit_paper_main")


@pytest.fixture
def adapter():
    from src.runtime_v2.execution_gateway.adapters.hummingbot_api_paper import HummingbotApiPaperAdapter
    return HummingbotApiPaperAdapter(base_url=HUMMINGBOT_URL, connector=CONNECTOR)


def test_api_reachable(adapter):
    import httpx
    resp = httpx.get(f"{HUMMINGBOT_URL}/health", timeout=5)
    assert resp.status_code == 200


def test_capabilities_declared(adapter):
    caps = adapter.get_capabilities()
    assert caps.place_entry is True
    assert caps.executor_position is False


def test_place_and_query_order(adapter):
    result = adapter.place_order(
        command_type="PLACE_ENTRY",
        payload={"symbol": "BTC/USDT", "side": "LONG",
                 "entry_type": "LIMIT", "price": 1.0, "qty": 0.001, "sequence": 1},
        client_order_id="tsb:9999:9999:entry:1",
        execution_account_id=ACCOUNT,
        connector=CONNECTOR,
    )
    assert result.success
    status = adapter.get_order_status(
        client_order_id="tsb:9999:9999:entry:1",
        execution_account_id=ACCOUNT,
    )
    assert status is not None
    # Cancella per pulizia
    adapter.cancel_order(
        client_order_id="tsb:9999:9999:entry:1",
        execution_account_id=ACCOUNT,
        connector=CONNECTOR,
    )
```

- [ ] **Step 3: Verifica sintassi adapter**

```bash
python -c "from src.runtime_v2.execution_gateway.adapters.hummingbot_api_paper import HummingbotApiPaperAdapter; print('OK')"
```

- [ ] **Step 4: Commit**

```bash
git add src/runtime_v2/execution_gateway/adapters/hummingbot_api_paper.py \
        tests/runtime_v2/execution_gateway/test_hummingbot_adapter.py
git commit -m "feat(prd05): HummingbotApiPaperAdapter — REST raw_order_mode, gated test"
```

---

## Task 12: FillBridge script

**Files:**
- Create: `hummingbot_scripts/__init__.py`
- Create: `hummingbot_scripts/fill_bridge.py`

- [ ] **Step 1: Crea lo script**

```python
# hummingbot_scripts/fill_bridge.py
"""
FillBridge — script opzionale che gira dentro Hummingbot come ScriptStrategyBase.
Scrive fill direttamente in ops_exchange_events via SQLite appena Hummingbot
riceve un fill dall'exchange. Zero latency, nessun polling.

Deploy: copiare in scripts/ di Hummingbot e configurare OPS_DB_PATH.
Upgrade dal polling smart — non richiede modifiche al gateway o al DB.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone

# Import Hummingbot — disponibili solo dentro il processo Hummingbot
try:
    from hummingbot.strategy.script_strategy_base import ScriptStrategyBase
    from hummingbot.core.event.events import OrderFilledEvent
    HUMMINGBOT_AVAILABLE = True
except ImportError:
    HUMMINGBOT_AVAILABLE = False
    # Stub per permettere import e test fuori da Hummingbot
    class ScriptStrategyBase:  # type: ignore[no-redef]
        pass

OPS_DB_PATH = os.environ.get("OPS_DB_PATH", "ops.sqlite3")
_PREFIX = "tsb"

_ROLE_EVENT_MAP = {
    "entry": "ENTRY_FILLED",
    "sl": "SL_FILLED",
    "tp": "TP_FILLED",
}


def _parse_chain_id(client_order_id: str) -> int | None:
    parts = client_order_id.split(":")
    if len(parts) == 5 and parts[0] == _PREFIX:
        try:
            return int(parts[1])
        except ValueError:
            pass
    return None


def _parse_role(client_order_id: str) -> str | None:
    parts = client_order_id.split(":")
    return parts[3] if len(parts) == 5 else None


def _parse_sequence(client_order_id: str) -> int:
    parts = client_order_id.split(":")
    try:
        return int(parts[4]) if len(parts) == 5 else 1
    except ValueError:
        return 1


class FillBridge(ScriptStrategyBase):
    def on_order_filled(self, event: "OrderFilledEvent") -> None:  # type: ignore[override]
        coid = getattr(event, "client_order_id", None) or ""
        if not coid.startswith(_PREFIX + ":"):
            return

        chain_id = _parse_chain_id(coid)
        role = _parse_role(coid)
        if chain_id is None or role is None:
            return

        event_type = _ROLE_EVENT_MAP.get(role)
        if event_type is None:
            return

        price = float(getattr(event, "price", 0) or 0)
        qty = float(getattr(event, "amount", 0) or 0)
        exchange_order_id = str(getattr(event, "exchange_order_id", coid))
        sequence = _parse_sequence(coid)

        if event_type == "TP_FILLED":
            payload = {
                "tp_level": sequence,
                "is_final": False,  # verrà aggiornato dal sync worker se necessario
                "fill_price": price,
                "filled_qty": qty,
            }
        else:
            payload = {"fill_price": price, "filled_qty": qty}

        idempotency_key = f"{event_type}:{chain_id}:{exchange_order_id}"
        now = datetime.now(timezone.utc).isoformat()

        try:
            conn = sqlite3.connect(OPS_DB_PATH)
            conn.execute(
                "INSERT OR IGNORE INTO ops_exchange_events "
                "(trade_chain_id, event_type, payload_json, processing_status, "
                "idempotency_key, received_at) VALUES (?,?,?,?,?,?)",
                (chain_id, event_type, json.dumps(payload), "NEW", idempotency_key, now),
            )
            conn.commit()
            conn.close()
        except Exception as exc:
            # Non far crashare Hummingbot per un errore di bridge
            import logging
            logging.getLogger(__name__).error("FillBridge write error: %s", exc)
```

- [ ] **Step 2: Crea `__init__.py`**

```bash
touch hummingbot_scripts/__init__.py
```

- [ ] **Step 3: Verifica import fuori da Hummingbot**

```bash
python -c "from hummingbot_scripts.fill_bridge import FillBridge, _parse_chain_id; assert _parse_chain_id('tsb:42:1001:entry:1') == 42; print('OK')"
```

- [ ] **Step 4: Commit**

```bash
git add hummingbot_scripts/
git commit -m "feat(prd05): FillBridge script — event bridge real-time dentro Hummingbot (upgrade da polling)"
```

---

## Task 13: Integration tests end-to-end

**Files:**
- Create: `tests/runtime_v2/execution_gateway/test_integration.py`

- [ ] **Step 1: Scrivi i test di integrazione**

```python
# tests/runtime_v2/execution_gateway/test_integration.py
"""
Acceptance contract per PRD-05.
Verifica i criteri pass/fail del design definitivo usando FakeAdapter.
"""
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


@pytest.fixture
def ops_db(tmp_path):
    db = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db)
    return db


def _insert_chain(db_path, chain_id=1, state="WAITING_ENTRY", account_id="acc_1"):
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_trade_chains (trade_chain_id, source_enrichment_id, "
        "canonical_message_id, raw_message_id, trader_id, account_id, symbol, side, "
        "lifecycle_state, entry_mode, management_plan_json, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (chain_id, chain_id, chain_id * 10, chain_id * 100, "trader_a",
         account_id, "BTC/USDT", "LONG", state, "ONE_SHOT", "{}", now, now),
    )
    conn.commit()
    conn.close()


def _insert_cmd(db_path, cmd_id, chain_id=1, cmd_type="PLACE_ENTRY",
                status="PENDING", payload=None):
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    default_payload = {
        "symbol": "BTC/USDT", "side": "LONG",
        "entry_type": "LIMIT", "price": 50000.0, "qty": 0.02, "sequence": 1,
    }
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_execution_commands "
        "(command_id, trade_chain_id, command_type, status, payload_json, "
        "idempotency_key, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (cmd_id, chain_id, cmd_type, status,
         json.dumps(payload or default_payload), f"idem:{cmd_id}", now, now),
    )
    conn.commit()
    conn.close()


def _make_stack(ops_db, adapter=None):
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.command_worker import ExecutionCommandWorker
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    adapter = adapter or FakeAdapter()
    config = ExecutionConfigLoader("config/execution.yaml").load()
    repo = GatewayCommandRepository(ops_db)
    gw = ExecutionGateway(
        config=config,
        adapter_registry={"hummingbot_api_paper": adapter},
        repo=repo,
    )
    worker = ExecutionCommandWorker(ops_db_path=ops_db, gateway=gw, repo=repo)
    sync = ExchangeEventSyncWorker(
        ops_db_path=ops_db, adapter=adapter, repo=repo,
        execution_account_id="bybit_paper_main",
    )
    return worker, sync, adapter


# AC1: PLACE_ENTRY passa PENDING → SENT
def test_ac1_place_entry_pending_to_sent(ops_db):
    _insert_chain(ops_db)
    _insert_cmd(ops_db, 1001)
    worker, _, _ = _make_stack(ops_db)
    worker.run_once()
    conn = sqlite3.connect(ops_db)
    status = conn.execute(
        "SELECT status FROM ops_execution_commands WHERE command_id=1001"
    ).fetchone()[0]
    conn.close()
    assert status == "SENT"


# AC2: capability mancante → REVIEW_REQUIRED
def test_ac2_capability_missing_review_required(ops_db):
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.models import AdapterCapabilities
    _insert_chain(ops_db)
    _insert_cmd(ops_db, 1002, cmd_type="PLACE_PROTECTIVE_STOP", payload={
        "symbol": "BTC/USDT", "side": "LONG",
        "stop_price": 49000.0, "qty": 0.02, "reduce_only": True,
    })
    adapter = FakeAdapter(capabilities=AdapterCapabilities(protective_stop_native=False))
    worker, _, _ = _make_stack(ops_db, adapter)
    worker.run_once()
    conn = sqlite3.connect(ops_db)
    status = conn.execute(
        "SELECT status FROM ops_execution_commands WHERE command_id=1002"
    ).fetchone()[0]
    conn.close()
    assert status == "REVIEW_REQUIRED"


# AC3: doppio run non reinvia (idempotenza)
def test_ac3_double_run_no_resend(ops_db):
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    _insert_chain(ops_db)
    _insert_cmd(ops_db, 1003)
    adapter = FakeAdapter()
    worker, _, _ = _make_stack(ops_db, adapter)
    worker.run_once()
    worker.run_once()
    place_calls = [c for c in adapter.calls if c["action"] == "place_order"]
    assert len(place_calls) == 1


# AC4: adapter timeout → retry
def test_ac4_timeout_sets_retry(ops_db):
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    _insert_chain(ops_db)
    _insert_cmd(ops_db, 1004)
    adapter = FakeAdapter(simulate_timeout=True)
    worker, _, _ = _make_stack(ops_db, adapter)
    worker.run_once()
    conn = sqlite3.connect(ops_db)
    retry_count = conn.execute(
        "SELECT retry_count FROM ops_execution_commands WHERE command_id=1004"
    ).fetchone()[0]
    conn.close()
    assert retry_count == 1


# AC5: fill entry → ops_exchange_events ENTRY_FILLED
def test_ac5_fill_produces_entry_filled_event(ops_db):
    _insert_chain(ops_db)
    _insert_cmd(ops_db, 1005)
    worker, sync, adapter = _make_stack(ops_db)
    worker.run_once()
    adapter.simulate_fill("tsb:1:1005:entry:1", price=50050.0, qty=0.02)
    sync.run_once()
    conn = sqlite3.connect(ops_db)
    events = conn.execute(
        "SELECT event_type FROM ops_exchange_events"
    ).fetchall()
    conn.close()
    assert any(e[0] == "ENTRY_FILLED" for e in events)


# AC6: TP multipli WAITING_POSITION prima del fill
def test_ac6_tp_waiting_position_before_fill(ops_db):
    _insert_chain(ops_db)
    _insert_cmd(ops_db, 2001, cmd_type="PLACE_TAKE_PROFIT", status="WAITING_POSITION",
                payload={"symbol": "BTC/USDT", "side": "LONG",
                         "tp_sequence": 1, "price": 51000.0,
                         "close_pct": 50.0, "reduce_only": True})
    # chain è WAITING_ENTRY (non OPEN) → WAITING_POSITION non deve diventare SENT
    worker, _, _ = _make_stack(ops_db)
    worker.run_once()
    conn = sqlite3.connect(ops_db)
    status = conn.execute(
        "SELECT status FROM ops_execution_commands WHERE command_id=2001"
    ).fetchone()[0]
    conn.close()
    assert status == "WAITING_POSITION"


# AC7: live trading bloccato
def test_ac7_live_trading_blocked(ops_db):
    import yaml
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.command_worker import ExecutionCommandWorker
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.models import ExecutionConfig
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    raw = yaml.safe_load(open("config/execution.yaml").read())
    raw["execution"]["adapters"]["hummingbot_api_paper"]["mode"] = "live"
    raw["execution"]["adapters"]["hummingbot_api_paper"]["live_safety"]["allow_live_trading"] = True
    config = ExecutionConfig.model_validate(raw["execution"])

    _insert_chain(ops_db)
    _insert_cmd(ops_db, 3001)
    adapter = FakeAdapter()
    repo = GatewayCommandRepository(ops_db)
    gw = ExecutionGateway(config=config,
                          adapter_registry={"hummingbot_api_paper": adapter}, repo=repo)
    worker = ExecutionCommandWorker(ops_db_path=ops_db, gateway=gw, repo=repo)
    worker.run_once()

    conn = sqlite3.connect(ops_db)
    status = conn.execute(
        "SELECT status FROM ops_execution_commands WHERE command_id=3001"
    ).fetchone()[0]
    conn.close()
    assert status == "REVIEW_REQUIRED"


# AC8: nessun import Hummingbot nel package execution_gateway
def test_ac8_no_hummingbot_import_in_gateway():
    import importlib
    import pkgutil
    import src.runtime_v2.execution_gateway as pkg
    for _, name, _ in pkgutil.walk_packages(pkg.__path__, prefix=pkg.__name__ + "."):
        if "hummingbot_api_paper" in name:
            continue  # l'adapter stesso può importare httpx, non Hummingbot
        try:
            mod = importlib.import_module(name)
            src_file = getattr(mod, "__file__", "") or ""
            src_code = open(src_file).read() if src_file else ""
            assert "hummingbot" not in src_code.lower() or "hummingbot_scripts" in src_file, \
                f"{src_file} contains hummingbot import"
        except ImportError:
            pass
```

- [ ] **Step 2: Esegui i test di integrazione**

```bash
pytest tests/runtime_v2/execution_gateway/test_integration.py -v
```

Expected: tutti PASS.

- [ ] **Step 3: Esegui la suite completa**

```bash
pytest tests/runtime_v2/ -v
```

Expected: tutti PASS, inclusi i test lifecycle PRD-04 esistenti.

- [ ] **Step 4: Commit finale**

```bash
git add tests/runtime_v2/execution_gateway/test_integration.py
git commit -m "test(prd05): acceptance contract integration — AC1-AC8 con FakeAdapter"
```

---

## Checklist finale

```bash
# Tutti i test PRD-05
pytest tests/runtime_v2/execution_gateway/ -v

# Tutti i test lifecycle PRD-04 (non devono regredire)
pytest tests/runtime_v2/lifecycle/ -v

# Verifica import puliti
python -c "import src.runtime_v2.execution_gateway; print('OK')"

# Verifica migration
python -c "
import sqlite3, pathlib
conn = sqlite3.connect(':memory:')
for f in sorted(pathlib.Path('db/ops_migrations').glob('*.sql')):
    conn.executescript(f.read_text(encoding='utf-8'))
print('migration OK')
"
```
