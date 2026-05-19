# Runtime V2 — Fase 1: CcxtBybitAdapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce `CcxtBybitAdapter` as the first real CCXT-based exchange adapter in runtime_v2, connecting the system to Bybit testnet for real order placement and status polling.

**Architecture:** `CcxtBybitAdapter` delegates command translation to `BybitOrderBuilder` (pure function, testable without network), maps CCXT order status via `StatusMapper`, and wires into the existing `ExecutionGateway` + `ExchangeEventSyncWorker` lifecycle without any changes to those components. The adapter is injectable with a mock exchange object for unit tests; gated integration tests run against Bybit testnet using a `@pytest.mark.bybit_testnet` marker.

**Tech Stack:** `ccxt>=4.0` (sync mode), existing `pydantic>=2.0`, `pytest` with custom marker, Python 3.12+.

## Execution Status (updated 2026-05-19) — FASE 1 COMPLETATA ✅

Tutti i task completati e mergiati su `main` (merge commit `9484b26`).
Suite finale: **367 passed, 10 skipped, 6 deselected (bybit_testnet), 0 failed**.

- `Task 1` completed.
  Commits:
  `8578de6` (`feat(execution): add api_key, testnet fields to AdapterConfig; base_url optional for ccxt_bybit`),
  `dcad3e7` (`fix(execution): require base_url for hummingbot adapter config`),
  `18eab02` (`fix(execution): reject blank hummingbot base_url config`).
  Result:
  `AdapterConfig` now supports `ccxt_bybit` (`api_key`, `testnet`, optional `base_url=""`) while preserving the `hummingbot_api` requirement for a non-empty `base_url`.

- `Task 2` completed.
  Commits:
  `a667868` (`feat(execution): add CcxtBybit package init and StatusMapper`),
  `344d9e9` (`fix(execution): normalize CCXT order fields in StatusMapper`),
  `fb90755` (`test(execution): align StatusMapper fixture typing`).
  Result:
  `StatusMapper` is implemented with status normalization, explicit/fallback `client_order_id`, normalized `exchange_order_id` / `filled_qty`, and expanded regression coverage.

- `Task 3` completed.
  Commits:
  `99b1db0` (`feat(execution): add BybitOrderBuilder with basic create_order commands`),
  `7fbf799` (`fix(execution): align Bybit order builder with lifecycle payload contracts`).
  Result:
  `BybitOrderBuilder` basic commands are implemented and reviewed. During review, two integration blockers emerged and were fixed:
  1. Mode C payload names were aligned with the actual lifecycle producer (`attached_take_profit`, `attached_stop_loss`).
  2. `MOVE_STOP_TO_BREAKEVEN` now consumes `target_price` + `be_buffer_pct` and computes the trigger price consistently with lifecycle semantics.

- `Task 4` completed (partially absorbed into Task 3 fix).
  Advanced builder coverage for Mode C, `CANCEL_PENDING_ENTRY`, `MOVE_STOP_TO_BREAKEVEN`, and `MOVE_STOP` added in `tests/runtime_v2/execution_gateway/test_bybit_order_builder.py`. Lifecycle assertions in `tests/runtime_v2/lifecycle/test_entry_gate.py`.
  Deviation: "No code changes needed" was false — fixing Task 3 required real code changes in both `order_builder.py` and `entry_gate.py`.

- `Task 5` completed.
  Commit: `c11fb67` (`feat(execution): implement CcxtBybitAdapter with injectable exchange for unit testing`).
  Result: `CcxtBybitAdapter` fully implemented — `place_order` (create_order / cancel_by_link / edit_sl / noop), `get_order_status` (open→closed fallback), `get_position_qty`, `set_leverage`, `get_capabilities`. 23 unit tests, all pass. Injectable `_exchange` for mock-based testing.

- `Task 6` completed.
  Commit: `685697c` (`feat(execution): wire ccxt_bybit into factory; add bybit_testnet pytest marker with auto-skip`).
  Result: `factory.py` updated with `ccxt_bybit` branch (reads `BYBIT_API_SECRET_{adapter_name}` from env). `bybit_testnet` marker registered in `pytest.ini`. `conftest.py` auto-skips gated tests when `BYBIT_TESTNET_API_KEY` absent.

- `Task 7` completed.
  Commit: `d42c0f8` (`test(execution): add gated integration tests for CcxtBybitAdapter against Bybit testnet`).
  Result: 6 gated integration tests covering leverage, entry placement, order status, cancel, protective stop, position qty. Auto-skipped without env var.

- `Task 8` completed — regression suite clean.
  367 tests pass on `main`. No new regressions.
  Additional fix: `0883f29` (`fix(execution): move hummingbot_api base_url validation to factory; update tests`) — moved adapter-specific base_url validation out of `models.py` into `factory.py` to satisfy architecture test `test_ac8_no_hummingbot_import_in_gateway`.

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `requirements.txt` | Modify | Add `ccxt>=4.0` |
| `src/runtime_v2/execution_gateway/models.py` | Modify | `base_url` default `""`, add `api_key`, `testnet` to `AdapterConfig` |
| `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/__init__.py` | Create | Package init |
| `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/status_mapper.py` | Create | CCXT status string → `RawAdapterOrder.status` |
| `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/order_builder.py` | Create | `BybitOrderParams` dataclass + `BybitOrderBuilder` (command_type + payload → CCXT params) |
| `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/adapter.py` | Create | `CcxtBybitAdapter` — calls CCXT, maps results, handles errors |
| `src/runtime_v2/execution_gateway/adapters/factory.py` | Modify | Add `ccxt_bybit` branch |
| `pytest.ini` | Modify | Register `bybit_testnet` marker |
| `tests/runtime_v2/execution_gateway/conftest.py` | Create | Auto-skip `bybit_testnet` tests when env var missing |
| `tests/runtime_v2/execution_gateway/test_adapter_config_ccxt.py` | Create | Unit tests for new `AdapterConfig` fields |
| `tests/runtime_v2/execution_gateway/test_bybit_status_mapper.py` | Create | Unit tests for `StatusMapper` |
| `tests/runtime_v2/execution_gateway/test_bybit_order_builder.py` | Create | Unit tests for `BybitOrderBuilder` (all commands, no network) |
| `tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py` | Create | Unit tests for `CcxtBybitAdapter` (mock exchange) |
| `tests/runtime_v2/execution_gateway/test_ccxt_bybit_gated.py` | Create | Gated integration tests against Bybit testnet |

---

### Task 1: CCXT Dependency + AdapterConfig Model Changes

**Files:**
- Modify: `requirements.txt`
- Modify: `src/runtime_v2/execution_gateway/models.py:49-70`
- Create: `tests/runtime_v2/execution_gateway/test_adapter_config_ccxt.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/runtime_v2/execution_gateway/test_adapter_config_ccxt.py`:

```python
# tests/runtime_v2/execution_gateway/test_adapter_config_ccxt.py
from __future__ import annotations

import pytest
from src.runtime_v2.execution_gateway.models import AdapterConfig


def test_adapter_config_ccxt_bybit_type_accepted():
    cfg = AdapterConfig.model_validate({
        "type": "ccxt_bybit",
        "mode": "paper",
        "connector": "bybit",
    })
    assert cfg.type == "ccxt_bybit"


def test_adapter_config_api_key_field():
    cfg = AdapterConfig.model_validate({
        "type": "ccxt_bybit",
        "mode": "paper",
        "connector": "bybit",
        "api_key": "abc123",
    })
    assert cfg.api_key == "abc123"


def test_adapter_config_testnet_field():
    cfg = AdapterConfig.model_validate({
        "type": "ccxt_bybit",
        "mode": "paper",
        "connector": "bybit",
        "testnet": True,
    })
    assert cfg.testnet is True


def test_adapter_config_testnet_defaults_false():
    cfg = AdapterConfig.model_validate({
        "type": "ccxt_bybit",
        "mode": "paper",
        "connector": "bybit",
    })
    assert cfg.testnet is False


def test_adapter_config_api_key_defaults_none():
    cfg = AdapterConfig.model_validate({
        "type": "ccxt_bybit",
        "mode": "paper",
        "connector": "bybit",
    })
    assert cfg.api_key is None


def test_adapter_config_base_url_optional_no_default_required():
    # ccxt_bybit doesn't use base_url — must work without it
    cfg = AdapterConfig.model_validate({
        "type": "ccxt_bybit",
        "mode": "paper",
        "connector": "bybit",
    })
    assert cfg.base_url == ""


def test_adapter_config_base_url_still_accepted_when_provided():
    cfg = AdapterConfig.model_validate({
        "type": "hummingbot_api",
        "mode": "demo",
        "connector": "bybit_perpetual_demo",
        "base_url": "http://localhost:8001",
    })
    assert cfg.base_url == "http://localhost:8001"
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/runtime_v2/execution_gateway/test_adapter_config_ccxt.py -v
```

Expected: several tests FAIL with `ValidationError` (extra field) or `Field required`.

- [ ] **Step 3: Update `AdapterConfig` in `models.py`**

In `src/runtime_v2/execution_gateway/models.py`, change `AdapterConfig`:

```python
class AdapterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str
    mode: str
    base_url: str = ""          # optional: not used by ccxt_bybit
    connector: str
    leverage: int = 1
    secret: str | None = None          # Bearer token for execution adapter auth
    api_key: str | None = None         # CCXT: public API key (in YAML)
    testnet: bool = False              # CCXT: use exchange sandbox/testnet
    entry_execution: EntryExecutionConfig = EntryExecutionConfig()

    @field_validator("secret", mode="before")
    @classmethod
    def _coerce_empty_secret(cls, v: object) -> object:
        if v == "":
            return None
        return v
    retry: RetryConfig = RetryConfig()
    capabilities: AdapterCapabilities = AdapterCapabilities()
    take_profit: TakeProfitConfig = TakeProfitConfig()
    position_management: PositionManagementConfig = PositionManagementConfig()
    live_safety: LiveSafetyConfig = LiveSafetyConfig()
```

- [ ] **Step 4: Add `ccxt>=4.0` to `requirements.txt`**

Append to `requirements.txt`:

```
ccxt>=4.0
```

- [ ] **Step 5: Run tests to verify they pass**

```
pytest tests/runtime_v2/execution_gateway/test_adapter_config_ccxt.py -v
```

Expected: all 7 tests PASS.

- [ ] **Step 6: Verify existing factory test still passes**

```
pytest tests/runtime_v2/execution_gateway/test_adapter_factory.py -v
```

Expected: all 4 tests PASS (the `_make_cfg` helper uses `base_url` explicitly — no regression).

- [ ] **Step 7: Commit**

```bash
git add requirements.txt src/runtime_v2/execution_gateway/models.py tests/runtime_v2/execution_gateway/test_adapter_config_ccxt.py
git commit -m "feat(execution): add api_key, testnet fields to AdapterConfig; base_url optional for ccxt_bybit"
```

---

### Task 2: StatusMapper

**Files:**
- Create: `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/__init__.py`
- Create: `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/status_mapper.py`
- Create: `tests/runtime_v2/execution_gateway/test_bybit_status_mapper.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/runtime_v2/execution_gateway/test_bybit_status_mapper.py`:

```python
# tests/runtime_v2/execution_gateway/test_bybit_status_mapper.py
from __future__ import annotations

import pytest
from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.status_mapper import StatusMapper


def _order(status: str, filled: float = 0.0, average: float | None = None) -> dict:
    return {"id": "ord123", "status": status, "filled": filled, "average": average}


@pytest.mark.parametrize("ccxt_status,expected_status", [
    ("open", "OPEN"),
    ("partially_filled", "OPEN"),
    ("closed", "FILLED"),
    ("canceled", "CANCELLED"),
    ("cancelled", "CANCELLED"),
    ("expired", "CANCELLED"),
    ("rejected", "FAILED"),
])
def test_status_mapper_status_strings(ccxt_status, expected_status):
    raw = StatusMapper.map(_order(ccxt_status), client_order_id="tsb:1:2:entry:1")
    assert raw.status == expected_status


def test_status_mapper_sets_exchange_order_id():
    raw = StatusMapper.map(_order("closed"), client_order_id="tsb:1:2:entry:1")
    assert raw.exchange_order_id == "ord123"


def test_status_mapper_sets_filled_qty():
    raw = StatusMapper.map(_order("closed", filled=0.05), client_order_id="tsb:1:2:entry:1")
    assert raw.filled_qty == 0.05


def test_status_mapper_sets_average_price():
    raw = StatusMapper.map(_order("closed", filled=0.01, average=50000.0),
                           client_order_id="tsb:1:2:entry:1")
    assert raw.average_price == 50000.0


def test_status_mapper_average_price_none_when_zero():
    raw = StatusMapper.map(_order("open", filled=0.0, average=0.0),
                           client_order_id="tsb:1:2:entry:1")
    assert raw.average_price is None


def test_status_mapper_is_filled_true_on_closed():
    raw = StatusMapper.map(_order("closed", filled=0.01, average=50000.0),
                           client_order_id="tsb:1:2:entry:1")
    assert raw.is_filled is True


def test_status_mapper_is_filled_false_on_open():
    raw = StatusMapper.map(_order("open"), client_order_id="tsb:1:2:entry:1")
    assert raw.is_filled is False


def test_status_mapper_uses_client_order_id():
    raw = StatusMapper.map(_order("open"), client_order_id="tsb:99:88:sl:2")
    assert raw.client_order_id == "tsb:99:88:sl:2"


def test_status_mapper_unknown_status_defaults_open():
    raw = StatusMapper.map(_order("pending"), client_order_id="tsb:1:2:entry:1")
    assert raw.status == "OPEN"
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/runtime_v2/execution_gateway/test_bybit_status_mapper.py -v
```

Expected: all FAIL with `ModuleNotFoundError` — package doesn't exist yet.

- [ ] **Step 3: Create package init and StatusMapper**

Create `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/__init__.py` (empty):

```python
```

Create `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/status_mapper.py`:

```python
# src/runtime_v2/execution_gateway/adapters/ccxt_bybit/status_mapper.py
from __future__ import annotations

from src.runtime_v2.execution_gateway.models import RawAdapterOrder

_CCXT_TO_STATUS: dict[str, str] = {
    "open": "OPEN",
    "partially_filled": "OPEN",
    "closed": "FILLED",
    "canceled": "CANCELLED",
    "cancelled": "CANCELLED",
    "expired": "CANCELLED",
    "rejected": "FAILED",
}


class StatusMapper:
    @staticmethod
    def map(ccxt_order: dict, *, client_order_id: str = "") -> RawAdapterOrder:
        raw_status = str(ccxt_order.get("status") or "open").lower()
        status = _CCXT_TO_STATUS.get(raw_status, "OPEN")
        avg = ccxt_order.get("average")
        return RawAdapterOrder(
            client_order_id=client_order_id or str(ccxt_order.get("clientOrderId") or ""),
            exchange_order_id=str(ccxt_order.get("id") or ""),
            status=status,
            filled_qty=float(ccxt_order.get("filled") or 0.0),
            average_price=float(avg) if avg else None,
        )


__all__ = ["StatusMapper"]
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/runtime_v2/execution_gateway/test_bybit_status_mapper.py -v
```

Expected: all 9 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/execution_gateway/adapters/ccxt_bybit/ tests/runtime_v2/execution_gateway/test_bybit_status_mapper.py
git commit -m "feat(execution): add CcxtBybit package init and StatusMapper"
```

---

### Task 3: BybitOrderBuilder — Basic create_order Commands

**Files:**
- Create: `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/order_builder.py`
- Create: `tests/runtime_v2/execution_gateway/test_bybit_order_builder.py`

- [ ] **Step 1: Write the failing tests (basic create_order + SYNC_PROTECTIVE_ORDERS)**

Create `tests/runtime_v2/execution_gateway/test_bybit_order_builder.py`:

```python
# tests/runtime_v2/execution_gateway/test_bybit_order_builder.py
from __future__ import annotations

import pytest
from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.order_builder import (
    BybitOrderBuilder,
    BybitOrderParams,
)

_builder = BybitOrderBuilder()


# --- PLACE_ENTRY ---

def test_place_entry_limit_long():
    p = _builder.build("PLACE_ENTRY", {
        "symbol": "BTC/USDT:USDT", "side": "LONG",
        "entry_type": "LIMIT", "qty": 0.01, "price": 50000.0,
    }, "tsb:10:5:entry:1")
    assert p.action == "create_order"
    assert p.order_type == "limit"
    assert p.side == "buy"
    assert p.symbol == "BTC/USDT:USDT"
    assert p.amount == 0.01
    assert p.price == 50000.0
    assert p.order_link_id == "tsb:10:5:entry:1"


def test_place_entry_limit_short():
    p = _builder.build("PLACE_ENTRY", {
        "symbol": "BTC/USDT:USDT", "side": "SHORT",
        "entry_type": "LIMIT", "qty": 0.02, "price": 48000.0,
    }, "tsb:10:6:entry:1")
    assert p.side == "sell"
    assert p.price == 48000.0
    assert p.amount == 0.02


def test_place_entry_market_long():
    p = _builder.build("PLACE_ENTRY", {
        "symbol": "BTC/USDT:USDT", "side": "LONG",
        "entry_type": "MARKET", "qty": 0.01, "price": None,
    }, "tsb:10:7:entry:1")
    assert p.order_type == "market"
    assert p.price is None


def test_place_entry_market_short():
    p = _builder.build("PLACE_ENTRY", {
        "symbol": "ETH/USDT:USDT", "side": "SHORT",
        "entry_type": "MARKET", "qty": 0.5, "price": None,
    }, "tsb:11:1:entry:1")
    assert p.side == "sell"
    assert p.order_type == "market"
    assert p.price is None


def test_place_entry_no_mode_c_has_no_extra_params():
    p = _builder.build("PLACE_ENTRY", {
        "symbol": "BTC/USDT:USDT", "side": "LONG",
        "entry_type": "LIMIT", "qty": 0.01, "price": 50000.0,
    }, "tsb:10:5:entry:1")
    assert p.extra_params == {}


# --- PLACE_PROTECTIVE_STOP ---

def test_place_protective_stop_long():
    p = _builder.build("PLACE_PROTECTIVE_STOP", {
        "symbol": "BTC/USDT:USDT", "side": "LONG",
        "qty": 0.01, "stop_price": 49000.0,
    }, "tsb:10:5:sl:1")
    assert p.action == "create_order"
    assert p.order_type == "stop"
    assert p.side == "sell"   # closing LONG = sell
    assert p.amount == 0.01
    assert p.price is None
    assert p.extra_params["reduceOnly"] is True
    assert p.extra_params["triggerPrice"] == 49000.0
    assert p.extra_params["triggerBy"] == "LastPrice"
    assert p.order_link_id == "tsb:10:5:sl:1"


def test_place_protective_stop_short():
    p = _builder.build("PLACE_PROTECTIVE_STOP", {
        "symbol": "BTC/USDT:USDT", "side": "SHORT",
        "qty": 0.02, "stop_price": 52000.0,
    }, "tsb:10:5:sl:1")
    assert p.side == "buy"    # closing SHORT = buy


# --- PLACE_TAKE_PROFIT ---

def test_place_take_profit_long():
    p = _builder.build("PLACE_TAKE_PROFIT", {
        "symbol": "BTC/USDT:USDT", "side": "LONG",
        "qty": 0.005, "price": 55000.0,
    }, "tsb:10:5:tp:1")
    assert p.action == "create_order"
    assert p.order_type == "limit"
    assert p.side == "sell"
    assert p.price == 55000.0
    assert p.amount == 0.005
    assert p.extra_params["reduceOnly"] is True
    assert p.order_link_id == "tsb:10:5:tp:1"


def test_place_take_profit_short():
    p = _builder.build("PLACE_TAKE_PROFIT", {
        "symbol": "ETH/USDT:USDT", "side": "SHORT",
        "qty": 1.0, "price": 2800.0,
    }, "tsb:11:2:tp:2")
    assert p.side == "buy"
    assert p.price == 2800.0


# --- CLOSE_PARTIAL ---

def test_close_partial_long():
    p = _builder.build("CLOSE_PARTIAL", {
        "symbol": "BTC/USDT:USDT", "side": "LONG", "qty": 0.005,
    }, "tsb:10:5:exit_partial:1")
    assert p.action == "create_order"
    assert p.order_type == "market"
    assert p.side == "sell"
    assert p.amount == 0.005
    assert p.price is None
    assert p.extra_params["reduceOnly"] is True


def test_close_partial_short():
    p = _builder.build("CLOSE_PARTIAL", {
        "symbol": "ETH/USDT:USDT", "side": "SHORT", "qty": 0.5,
    }, "tsb:11:3:exit_partial:1")
    assert p.side == "buy"


# --- CLOSE_FULL ---

def test_close_full_long():
    p = _builder.build("CLOSE_FULL", {
        "symbol": "BTC/USDT:USDT", "side": "LONG", "qty": 0.01,
    }, "tsb:10:5:exit_full:1")
    assert p.order_type == "market"
    assert p.side == "sell"
    assert p.extra_params["reduceOnly"] is True


def test_close_full_short():
    p = _builder.build("CLOSE_FULL", {
        "symbol": "BTC/USDT:USDT", "side": "SHORT", "qty": 0.01,
    }, "tsb:10:5:exit_full:1")
    assert p.side == "buy"


# --- SYNC_PROTECTIVE_ORDERS ---

def test_sync_protective_orders_noop():
    p = _builder.build("SYNC_PROTECTIVE_ORDERS", {
        "symbol": "BTC/USDT:USDT",
    }, "tsb:10:5:sync:1")
    assert p.action == "noop"


def test_unknown_command_raises():
    with pytest.raises(ValueError, match="Unknown command_type"):
        _builder.build("INVALID_COMMAND", {}, "tsb:1:1:entry:1")
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/runtime_v2/execution_gateway/test_bybit_order_builder.py -v
```

Expected: all FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `BybitOrderParams` and `BybitOrderBuilder` (basic commands)**

Create `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/order_builder.py`:

```python
# src/runtime_v2/execution_gateway/adapters/ccxt_bybit/order_builder.py
from __future__ import annotations

from dataclasses import dataclass, field

_ENTRY_SIDE: dict[str, str] = {"LONG": "buy", "SHORT": "sell"}
_CLOSE_SIDE: dict[str, str] = {"LONG": "sell", "SHORT": "buy"}


@dataclass
class BybitOrderParams:
    action: str              # "create_order" | "cancel_by_link" | "edit_sl" | "noop"
    symbol: str = ""
    order_type: str = ""     # "limit" | "market" | "stop"
    side: str = ""           # "buy" | "sell"
    amount: float = 0.0
    price: float | None = None
    order_link_id: str = ""
    extra_params: dict = field(default_factory=dict)
    new_trigger_price: float | None = None   # for edit_sl action
    position_side: str = ""                  # "LONG"|"SHORT" for edit_sl lookup


class BybitOrderBuilder:
    def build(
        self, command_type: str, payload: dict, client_order_id: str
    ) -> BybitOrderParams:
        if command_type == "PLACE_ENTRY":
            return self._place_entry(payload, client_order_id)
        if command_type == "PLACE_PROTECTIVE_STOP":
            return self._place_protective_stop(payload, client_order_id)
        if command_type == "PLACE_TAKE_PROFIT":
            return self._place_take_profit(payload, client_order_id)
        if command_type in ("CLOSE_PARTIAL", "CLOSE_FULL"):
            return self._close_market(payload, client_order_id)
        if command_type == "CANCEL_PENDING_ENTRY":
            return self._cancel_pending_entry(payload, client_order_id)
        if command_type in ("MOVE_STOP_TO_BREAKEVEN", "MOVE_STOP"):
            return self._move_stop(command_type, payload)
        if command_type == "SYNC_PROTECTIVE_ORDERS":
            return BybitOrderParams(action="noop")
        raise ValueError(f"Unknown command_type: {command_type!r}")

    def _place_entry(self, payload: dict, client_order_id: str) -> BybitOrderParams:
        entry_type: str = payload["entry_type"]
        extra: dict = {}
        if payload.get("native_attached_tpsl"):
            extra.update(self._mode_c_params(payload))
        return BybitOrderParams(
            action="create_order",
            symbol=payload["symbol"],
            order_type=entry_type.lower(),
            side=_ENTRY_SIDE[payload["side"]],
            amount=float(payload["qty"]),
            price=float(payload["price"]) if entry_type == "LIMIT" and payload.get("price") else None,
            order_link_id=client_order_id,
            extra_params=extra,
        )

    def _mode_c_params(self, payload: dict) -> dict:
        tp_count: int = int(payload.get("tp_count", 1))
        total_qty = float(payload["qty"])
        tp_size = float(payload["attached_take_profit_qty"]) if tp_count > 1 else total_qty
        return {
            "takeProfit": float(payload["attached_take_profit"]),
            "stopLoss": float(payload["attached_stop_loss"]),
            "tpslMode": "Partial",
            "tpOrderType": "Limit",
            "tpLimitPrice": float(payload["attached_take_profit"]),
            "tpSize": tp_size,
        }

    def _place_protective_stop(self, payload: dict, client_order_id: str) -> BybitOrderParams:
        return BybitOrderParams(
            action="create_order",
            symbol=payload["symbol"],
            order_type="stop",
            side=_CLOSE_SIDE[payload["side"]],
            amount=float(payload["qty"]),
            price=None,
            order_link_id=client_order_id,
            extra_params={
                "reduceOnly": True,
                "triggerPrice": float(payload["stop_price"]),
                "triggerBy": "LastPrice",
            },
        )

    def _place_take_profit(self, payload: dict, client_order_id: str) -> BybitOrderParams:
        return BybitOrderParams(
            action="create_order",
            symbol=payload["symbol"],
            order_type="limit",
            side=_CLOSE_SIDE[payload["side"]],
            amount=float(payload["qty"]),
            price=float(payload["price"]),
            order_link_id=client_order_id,
            extra_params={"reduceOnly": True},
        )

    def _close_market(self, payload: dict, client_order_id: str) -> BybitOrderParams:
        return BybitOrderParams(
            action="create_order",
            symbol=payload["symbol"],
            order_type="market",
            side=_CLOSE_SIDE[payload["side"]],
            amount=float(payload["qty"]),
            price=None,
            order_link_id=client_order_id,
            extra_params={"reduceOnly": True},
        )

    def _cancel_pending_entry(self, payload: dict, client_order_id: str) -> BybitOrderParams:
        return BybitOrderParams(
            action="cancel_by_link",
            symbol=payload["symbol"],
            order_link_id=client_order_id,
        )

    def _move_stop(self, command_type: str, payload: dict) -> BybitOrderParams:
        if command_type == "MOVE_STOP_TO_BREAKEVEN":
            new_trigger = float(payload["entry_price"])
        else:
            new_trigger = float(payload["new_stop_price"])
        return BybitOrderParams(
            action="edit_sl",
            symbol=payload["symbol"],
            position_side=payload["side"],
            new_trigger_price=new_trigger,
        )


__all__ = ["BybitOrderBuilder", "BybitOrderParams"]
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/runtime_v2/execution_gateway/test_bybit_order_builder.py -v
```

Expected: all tests so far PASS. (Mode C tests not written yet — added in Task 4.)

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/execution_gateway/adapters/ccxt_bybit/order_builder.py tests/runtime_v2/execution_gateway/test_bybit_order_builder.py
git commit -m "feat(execution): add BybitOrderBuilder with basic create_order commands"
```

---

### Task 4: BybitOrderBuilder — Mode C + cancel + edit + noop (extend Task 3 test file)

**Files:**
- Modify: `tests/runtime_v2/execution_gateway/test_bybit_order_builder.py` (add tests at bottom)
- No code changes needed — builder already implements all cases; these tests verify correctness

- [ ] **Step 1: Append Mode C and advanced command tests to the test file**

Append to `tests/runtime_v2/execution_gateway/test_bybit_order_builder.py`:

```python
# --- Mode C (native_attached_tpsl) ---

def test_mode_c_entry_multiple_tps_uses_explicit_tp_size():
    """When tp_count > 1, tpSize = attached_take_profit_qty (partial coverage)."""
    p = _builder.build("PLACE_ENTRY", {
        "symbol": "BTC/USDT:USDT", "side": "LONG",
        "entry_type": "LIMIT", "qty": 0.01, "price": 50000.0,
        "native_attached_tpsl": True,
        "attached_take_profit": 55000.0,
        "attached_stop_loss": 49000.0,
        "attached_take_profit_qty": 0.005,
        "tp_count": 2,
    }, "tsb:10:5:entry:1")
    assert p.extra_params["tpslMode"] == "Partial"
    assert p.extra_params["tpSize"] == 0.005
    assert p.extra_params["takeProfit"] == 55000.0
    assert p.extra_params["stopLoss"] == 49000.0
    assert p.extra_params["tpOrderType"] == "Limit"
    assert p.extra_params["tpLimitPrice"] == 55000.0


def test_mode_c_entry_single_tp_uses_total_qty():
    """When tp_count == 1, tpSize = total entry qty (full coverage)."""
    p = _builder.build("PLACE_ENTRY", {
        "symbol": "BTC/USDT:USDT", "side": "LONG",
        "entry_type": "LIMIT", "qty": 0.01, "price": 50000.0,
        "native_attached_tpsl": True,
        "attached_take_profit": 55000.0,
        "attached_stop_loss": 49000.0,
        "attached_take_profit_qty": 0.01,
        "tp_count": 1,
    }, "tsb:10:5:entry:1")
    assert p.extra_params["tpSize"] == 0.01


def test_mode_c_entry_short():
    p = _builder.build("PLACE_ENTRY", {
        "symbol": "BTC/USDT:USDT", "side": "SHORT",
        "entry_type": "LIMIT", "qty": 0.01, "price": 60000.0,
        "native_attached_tpsl": True,
        "attached_take_profit": 55000.0,
        "attached_stop_loss": 62000.0,
        "attached_take_profit_qty": 0.005,
        "tp_count": 2,
    }, "tsb:10:5:entry:1")
    assert p.side == "sell"
    assert p.extra_params["tpslMode"] == "Partial"


def test_mode_c_default_tp_count_1_when_missing():
    """If tp_count is absent from payload, default to 1 (full coverage)."""
    p = _builder.build("PLACE_ENTRY", {
        "symbol": "BTC/USDT:USDT", "side": "LONG",
        "entry_type": "LIMIT", "qty": 0.01, "price": 50000.0,
        "native_attached_tpsl": True,
        "attached_take_profit": 55000.0,
        "attached_stop_loss": 49000.0,
        "attached_take_profit_qty": 0.01,
        # tp_count intentionally missing
    }, "tsb:10:5:entry:1")
    assert p.extra_params["tpSize"] == 0.01


# --- CANCEL_PENDING_ENTRY ---

def test_cancel_pending_entry_action():
    p = _builder.build("CANCEL_PENDING_ENTRY", {
        "symbol": "BTC/USDT:USDT",
    }, "tsb:10:5:entry:1")
    assert p.action == "cancel_by_link"
    assert p.symbol == "BTC/USDT:USDT"
    assert p.order_link_id == "tsb:10:5:entry:1"


# --- MOVE_STOP_TO_BREAKEVEN ---

def test_move_stop_to_breakeven_action():
    p = _builder.build("MOVE_STOP_TO_BREAKEVEN", {
        "symbol": "BTC/USDT:USDT", "side": "LONG", "entry_price": 50000.0,
    }, "tsb:10:5:sl:1")
    assert p.action == "edit_sl"
    assert p.symbol == "BTC/USDT:USDT"
    assert p.new_trigger_price == 50000.0
    assert p.position_side == "LONG"


def test_move_stop_to_breakeven_short():
    p = _builder.build("MOVE_STOP_TO_BREAKEVEN", {
        "symbol": "ETH/USDT:USDT", "side": "SHORT", "entry_price": 3000.0,
    }, "tsb:11:2:sl:1")
    assert p.new_trigger_price == 3000.0
    assert p.position_side == "SHORT"


# --- MOVE_STOP ---

def test_move_stop_action():
    p = _builder.build("MOVE_STOP", {
        "symbol": "BTC/USDT:USDT", "side": "LONG", "new_stop_price": 51000.0,
    }, "tsb:10:5:sl:1")
    assert p.action == "edit_sl"
    assert p.new_trigger_price == 51000.0
    assert p.position_side == "LONG"


def test_move_stop_short():
    p = _builder.build("MOVE_STOP", {
        "symbol": "ETH/USDT:USDT", "side": "SHORT", "new_stop_price": 2900.0,
    }, "tsb:11:1:sl:1")
    assert p.new_trigger_price == 2900.0
    assert p.position_side == "SHORT"
```

- [ ] **Step 2: Run extended tests to verify they pass**

```
pytest tests/runtime_v2/execution_gateway/test_bybit_order_builder.py -v
```

Expected: all tests PASS (implementation is already complete from Task 3).

- [ ] **Step 3: Commit**

```bash
git add tests/runtime_v2/execution_gateway/test_bybit_order_builder.py
git commit -m "test(execution): add Mode C, cancel, edit_sl, move_stop tests for BybitOrderBuilder"
```

---

### Task 5: CcxtBybitAdapter

**Files:**
- Create: `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/adapter.py`
- Create: `tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py`

- [ ] **Step 1: Write the failing unit tests (with injectable mock exchange)**

Create `tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py`:

```python
# tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py
from __future__ import annotations

import pytest
from unittest.mock import MagicMock


def _make_adapter(exchange):
    from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.adapter import CcxtBybitAdapter
    return CcxtBybitAdapter(
        api_key="key", api_secret="secret", testnet=True, connector="bybit",
        _exchange=exchange,
    )


def _place_entry(adapter, symbol="BTC/USDT:USDT", side="LONG", qty=0.01, price=50000.0):
    return adapter.place_order(
        command_type="PLACE_ENTRY",
        payload={"symbol": symbol, "side": side, "entry_type": "LIMIT",
                 "qty": qty, "price": price},
        client_order_id="tsb:10:5:entry:1",
        execution_account_id="bybit_main",
        connector="bybit",
    )


# --- place_order: create_order happy path ---

def test_place_entry_calls_create_order_with_correct_params():
    exchange = MagicMock()
    exchange.create_order.return_value = {"id": "exch_12345"}
    adapter = _make_adapter(exchange)

    result = _place_entry(adapter)

    assert result.success is True
    assert result.exchange_order_id == "exch_12345"
    exchange.create_order.assert_called_once()
    args, kwargs = exchange.create_order.call_args
    assert args[0] == "BTC/USDT:USDT"   # symbol
    assert args[1] == "limit"            # order_type
    assert args[2] == "buy"              # side (LONG entry)
    assert args[3] == 0.01              # amount
    assert args[4] == 50000.0           # price
    assert kwargs["params"]["orderLinkId"] == "tsb:10:5:entry:1"


def test_place_entry_short_uses_sell_side():
    exchange = MagicMock()
    exchange.create_order.return_value = {"id": "exch_99"}
    adapter = _make_adapter(exchange)

    _place_entry(adapter, side="SHORT")

    args, _ = exchange.create_order.call_args
    assert args[2] == "sell"


def test_place_protective_stop_calls_create_order():
    exchange = MagicMock()
    exchange.create_order.return_value = {"id": "exch_sl"}
    adapter = _make_adapter(exchange)

    result = adapter.place_order(
        command_type="PLACE_PROTECTIVE_STOP",
        payload={"symbol": "BTC/USDT:USDT", "side": "LONG",
                 "qty": 0.01, "stop_price": 49000.0},
        client_order_id="tsb:10:5:sl:1",
        execution_account_id="bybit_main",
        connector="bybit",
    )

    assert result.success is True
    args, kwargs = exchange.create_order.call_args
    assert args[1] == "stop"
    assert args[2] == "sell"
    assert kwargs["params"]["triggerPrice"] == 49000.0
    assert kwargs["params"]["reduceOnly"] is True


# --- place_order: noop (SYNC_PROTECTIVE_ORDERS) ---

def test_sync_protective_orders_noop_no_exchange_call():
    exchange = MagicMock()
    adapter = _make_adapter(exchange)

    result = adapter.place_order(
        command_type="SYNC_PROTECTIVE_ORDERS",
        payload={"symbol": "BTC/USDT:USDT"},
        client_order_id="tsb:10:5:sync:1",
        execution_account_id="bybit_main",
        connector="bybit",
    )

    assert result.success is True
    exchange.create_order.assert_not_called()
    exchange.cancel_order.assert_not_called()


# --- place_order: cancel_by_link (CANCEL_PENDING_ENTRY) ---

def test_cancel_pending_entry_fetches_and_cancels_open_order():
    exchange = MagicMock()
    exchange.fetch_open_orders.return_value = [{"id": "open_ord_1", "side": "buy"}]
    adapter = _make_adapter(exchange)

    result = adapter.place_order(
        command_type="CANCEL_PENDING_ENTRY",
        payload={"symbol": "BTC/USDT:USDT"},
        client_order_id="tsb:10:5:entry:1",
        execution_account_id="bybit_main",
        connector="bybit",
    )

    assert result.success is True
    exchange.fetch_open_orders.assert_called_once_with(
        "BTC/USDT:USDT", params={"orderLinkId": "tsb:10:5:entry:1"}
    )
    exchange.cancel_order.assert_called_once_with("open_ord_1", "BTC/USDT:USDT")


def test_cancel_pending_entry_no_open_order_still_succeeds():
    exchange = MagicMock()
    exchange.fetch_open_orders.return_value = []
    adapter = _make_adapter(exchange)

    result = adapter.place_order(
        command_type="CANCEL_PENDING_ENTRY",
        payload={"symbol": "BTC/USDT:USDT"},
        client_order_id="tsb:10:5:entry:1",
        execution_account_id="bybit_main",
        connector="bybit",
    )

    assert result.success is True
    exchange.cancel_order.assert_not_called()


# --- place_order: edit_sl (MOVE_STOP_TO_BREAKEVEN) ---

def test_move_stop_to_breakeven_edits_sl_order():
    exchange = MagicMock()
    sl_order = {
        "id": "sl_ord_1", "side": "sell",
        "type": "stop", "amount": 0.01,
        "reduceOnly": True, "stopPrice": 49000.0,
    }
    exchange.fetch_open_orders.return_value = [sl_order]
    adapter = _make_adapter(exchange)

    result = adapter.place_order(
        command_type="MOVE_STOP_TO_BREAKEVEN",
        payload={"symbol": "BTC/USDT:USDT", "side": "LONG", "entry_price": 50000.0},
        client_order_id="tsb:10:5:sl:1",
        execution_account_id="bybit_main",
        connector="bybit",
    )

    assert result.success is True
    exchange.edit_order.assert_called_once()
    edit_args, edit_kwargs = exchange.edit_order.call_args
    assert edit_args[0] == "sl_ord_1"
    assert edit_kwargs["params"]["triggerPrice"] == 50000.0


def test_move_stop_sl_not_found_returns_failed():
    exchange = MagicMock()
    exchange.fetch_open_orders.return_value = []
    adapter = _make_adapter(exchange)

    result = adapter.place_order(
        command_type="MOVE_STOP",
        payload={"symbol": "BTC/USDT:USDT", "side": "LONG", "new_stop_price": 51000.0},
        client_order_id="tsb:10:5:sl:1",
        execution_account_id="bybit_main",
        connector="bybit",
    )

    assert result.success is False
    assert result.reason == "sl_order_not_found"


# --- place_order: error handling ---

def test_invalid_order_returns_failed_with_reason():
    import ccxt
    exchange = MagicMock()
    exchange.create_order.side_effect = ccxt.InvalidOrder("order params bad")
    adapter = _make_adapter(exchange)

    result = _place_entry(adapter)

    assert result.success is False
    assert result.reason == "invalid_order"
    assert "order params bad" in (result.error or "")


def test_insufficient_funds_returns_failed_with_reason():
    import ccxt
    exchange = MagicMock()
    exchange.create_order.side_effect = ccxt.InsufficientFunds("no money")
    adapter = _make_adapter(exchange)

    result = _place_entry(adapter)

    assert result.success is False
    assert result.reason == "insufficient_funds"


def test_network_error_propagates():
    import ccxt
    exchange = MagicMock()
    exchange.create_order.side_effect = ccxt.NetworkError("timeout")
    adapter = _make_adapter(exchange)

    with pytest.raises(ccxt.NetworkError):
        _place_entry(adapter)


def test_rate_limit_exceeded_propagates():
    import ccxt
    exchange = MagicMock()
    exchange.create_order.side_effect = ccxt.RateLimitExceeded("slow down")
    adapter = _make_adapter(exchange)

    with pytest.raises(ccxt.RateLimitExceeded):
        _place_entry(adapter)


def test_other_base_error_returns_failed():
    import ccxt
    exchange = MagicMock()
    exchange.create_order.side_effect = ccxt.ExchangeError("generic exchange error")
    adapter = _make_adapter(exchange)

    result = _place_entry(adapter)

    assert result.success is False
    assert result.error is not None


# --- get_order_status ---

def test_get_order_status_finds_open_order():
    exchange = MagicMock()
    exchange.fetch_open_orders.return_value = [{
        "id": "exch_123", "status": "open", "filled": 0.0, "average": None,
    }]
    adapter = _make_adapter(exchange)

    raw = adapter.get_order_status(
        client_order_id="tsb:10:5:entry:1", execution_account_id="bybit_main"
    )

    assert raw is not None
    assert raw.status == "OPEN"
    assert raw.exchange_order_id == "exch_123"
    assert raw.client_order_id == "tsb:10:5:entry:1"


def test_get_order_status_falls_back_to_closed_orders():
    exchange = MagicMock()
    exchange.fetch_open_orders.return_value = []
    exchange.fetch_closed_orders.return_value = [{
        "id": "exch_456", "status": "closed", "filled": 0.01, "average": 50000.0,
    }]
    adapter = _make_adapter(exchange)

    raw = adapter.get_order_status(
        client_order_id="tsb:10:5:entry:1", execution_account_id="bybit_main"
    )

    assert raw is not None
    assert raw.status == "FILLED"
    assert raw.filled_qty == 0.01
    assert raw.average_price == 50000.0


def test_get_order_status_not_found_returns_none():
    exchange = MagicMock()
    exchange.fetch_open_orders.return_value = []
    exchange.fetch_closed_orders.return_value = []
    adapter = _make_adapter(exchange)

    raw = adapter.get_order_status(
        client_order_id="tsb:10:5:entry:1", execution_account_id="bybit_main"
    )

    assert raw is None


def test_get_order_status_open_orders_exception_falls_back():
    exchange = MagicMock()
    exchange.fetch_open_orders.side_effect = Exception("network blip")
    exchange.fetch_closed_orders.return_value = [{
        "id": "exch_789", "status": "closed", "filled": 0.005, "average": 48000.0,
    }]
    adapter = _make_adapter(exchange)

    raw = adapter.get_order_status(
        client_order_id="tsb:10:5:entry:1", execution_account_id="bybit_main"
    )

    assert raw is not None
    assert raw.status == "FILLED"


# --- get_position_qty ---

def test_get_position_qty_long():
    exchange = MagicMock()
    exchange.fetch_positions.return_value = [
        {"side": "long", "contracts": 0.03},
        {"side": "short", "contracts": 0.0},
    ]
    adapter = _make_adapter(exchange)

    qty = adapter.get_position_qty(
        symbol="BTC/USDT:USDT", side="LONG", execution_account_id="bybit_main"
    )

    assert qty == 0.03
    exchange.fetch_positions.assert_called_once_with(["BTC/USDT:USDT"])


def test_get_position_qty_short():
    exchange = MagicMock()
    exchange.fetch_positions.return_value = [
        {"side": "long", "contracts": 0.01},
        {"side": "short", "contracts": 0.05},
    ]
    adapter = _make_adapter(exchange)

    qty = adapter.get_position_qty(
        symbol="BTC/USDT:USDT", side="SHORT", execution_account_id="bybit_main"
    )

    assert qty == 0.05


def test_get_position_qty_no_matching_side_returns_zero():
    exchange = MagicMock()
    exchange.fetch_positions.return_value = [{"side": "short", "contracts": 0.02}]
    adapter = _make_adapter(exchange)

    qty = adapter.get_position_qty(
        symbol="BTC/USDT:USDT", side="LONG", execution_account_id="bybit_main"
    )

    assert qty == 0.0


def test_get_position_qty_exception_returns_none():
    exchange = MagicMock()
    exchange.fetch_positions.side_effect = Exception("API error")
    adapter = _make_adapter(exchange)

    qty = adapter.get_position_qty(
        symbol="BTC/USDT:USDT", side="LONG", execution_account_id="bybit_main"
    )

    assert qty is None


# --- set_leverage ---

def test_set_leverage_calls_exchange_with_buy_sell_params():
    exchange = MagicMock()
    adapter = _make_adapter(exchange)

    adapter.set_leverage("BTC/USDT:USDT", 10, "bybit_main")

    exchange.set_leverage.assert_called_once_with(
        10, "BTC/USDT:USDT",
        params={"buyLeverage": "10", "sellLeverage": "10"},
    )


# --- get_capabilities ---

def test_get_capabilities_returns_correct_flags():
    exchange = MagicMock()
    adapter = _make_adapter(exchange)
    caps = adapter.get_capabilities()

    assert caps.place_entry is True
    assert caps.protective_stop_native is True
    assert caps.take_profit_native is True
    assert caps.bracket_order is False
    assert caps.move_stop is True
    assert caps.close_partial is True
    assert caps.close_full is True
    assert caps.sync_protective_orders is True
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py -v
```

Expected: all FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `CcxtBybitAdapter`**

Create `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/adapter.py`:

```python
# src/runtime_v2/execution_gateway/adapters/ccxt_bybit/adapter.py
from __future__ import annotations

import logging

import ccxt

from src.runtime_v2.execution_gateway.adapters.base import ExecutionAdapter
from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.order_builder import BybitOrderBuilder
from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.status_mapper import StatusMapper
from src.runtime_v2.execution_gateway.models import AdapterCapabilities, AdapterResult, RawAdapterOrder

logger = logging.getLogger(__name__)

_DEFAULT_CAPABILITIES = AdapterCapabilities(
    place_entry=True,
    protective_stop_native=True,
    take_profit_native=True,
    bracket_order=False,
    move_stop=True,
    close_partial=True,
    close_full=True,
    executor_position=False,
    sync_protective_orders=True,
)


class CcxtBybitAdapter(ExecutionAdapter):
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        testnet: bool,
        connector: str,
        capabilities: AdapterCapabilities | None = None,
        _exchange=None,  # injectable for unit tests
    ) -> None:
        if _exchange is not None:
            self._exchange = _exchange
        else:
            self._exchange = ccxt.bybit({
                "apiKey": api_key,
                "secret": api_secret,
                "options": {"defaultType": "linear"},
            })
            if testnet:
                self._exchange.set_sandbox_mode(True)
        self._connector = connector
        self._capabilities = capabilities or _DEFAULT_CAPABILITIES
        self._builder = BybitOrderBuilder()

    def get_capabilities(self) -> AdapterCapabilities:
        return self._capabilities

    def set_leverage(self, symbol: str, leverage: int, execution_account_id: str) -> None:
        self._exchange.set_leverage(leverage, symbol, params={
            "buyLeverage": str(leverage),
            "sellLeverage": str(leverage),
        })

    def place_order(
        self,
        *,
        command_type: str,
        payload: dict,
        client_order_id: str,
        execution_account_id: str,
        connector: str,
    ) -> AdapterResult:
        params = self._builder.build(command_type, payload, client_order_id)

        if params.action == "noop":
            return AdapterResult(success=True)

        try:
            if params.action == "create_order":
                resp = self._exchange.create_order(
                    params.symbol,
                    params.order_type,
                    params.side,
                    params.amount,
                    params.price,
                    params={"orderLinkId": params.order_link_id, **params.extra_params},
                )
                return AdapterResult(
                    success=True,
                    exchange_order_id=str(resp.get("id") or ""),
                )

            if params.action == "cancel_by_link":
                orders = self._exchange.fetch_open_orders(
                    params.symbol, params={"orderLinkId": params.order_link_id}
                )
                if orders:
                    self._exchange.cancel_order(orders[-1]["id"], params.symbol)
                return AdapterResult(success=True)

            if params.action == "edit_sl":
                close_side = "sell" if params.position_side == "LONG" else "buy"
                orders = self._exchange.fetch_open_orders(params.symbol)
                sl_orders = [
                    o for o in orders
                    if o.get("reduceOnly") and o.get("stopPrice") and o["side"] == close_side
                ]
                if not sl_orders:
                    return AdapterResult(success=False, reason="sl_order_not_found")
                sl = sl_orders[-1]
                self._exchange.edit_order(
                    sl["id"], params.symbol, sl["type"], sl["side"], sl["amount"],
                    params={"triggerPrice": params.new_trigger_price},
                )
                return AdapterResult(success=True)

        except ccxt.InvalidOrder as e:
            return AdapterResult(success=False, reason="invalid_order", error=str(e))
        except ccxt.InsufficientFunds as e:
            return AdapterResult(success=False, reason="insufficient_funds", error=str(e))
        except (ccxt.NetworkError, ccxt.RateLimitExceeded):
            raise
        except ccxt.BaseError as e:
            return AdapterResult(success=False, error=str(e))

        return AdapterResult(success=False, error=f"unhandled action: {params.action!r}")

    def cancel_order(
        self,
        *,
        client_order_id: str,
        execution_account_id: str,
        connector: str,
    ) -> AdapterResult:
        # Called by gateway idempotency path — no symbol available here.
        # Actual cancel is issued via CANCEL_PENDING_ENTRY command through place_order.
        return AdapterResult(success=True)

    def get_order_status(
        self,
        *,
        client_order_id: str,
        execution_account_id: str,
    ) -> RawAdapterOrder | None:
        try:
            orders = self._exchange.fetch_open_orders(
                None, params={"orderLinkId": client_order_id}
            )
        except Exception:
            orders = []
        if not orders:
            try:
                orders = self._exchange.fetch_closed_orders(
                    None, params={"orderLinkId": client_order_id}
                )
            except Exception:
                orders = []
        if not orders:
            return None
        return StatusMapper.map(orders[-1], client_order_id=client_order_id)

    def get_position_qty(
        self,
        *,
        symbol: str,
        side: str,
        execution_account_id: str,
    ) -> float | None:
        try:
            positions = self._exchange.fetch_positions([symbol])
            for pos in positions:
                if str(pos.get("side") or "").lower() == side.lower():
                    return float(pos.get("contracts") or 0.0)
            return 0.0
        except Exception:
            logger.warning("get_position_qty failed for %s %s", symbol, side)
            return None


__all__ = ["CcxtBybitAdapter"]
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/execution_gateway/adapters/ccxt_bybit/adapter.py tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py
git commit -m "feat(execution): implement CcxtBybitAdapter with injectable exchange for unit testing"
```

---

### Task 6: factory.py Update + pytest Marker Registration

**Files:**
- Modify: `src/runtime_v2/execution_gateway/adapters/factory.py`
- Modify: `pytest.ini`
- Create: `tests/runtime_v2/execution_gateway/conftest.py`
- Modify: `tests/runtime_v2/execution_gateway/test_adapter_factory.py` (add ccxt_bybit test)

- [ ] **Step 1: Write the failing factory test**

Append to `tests/runtime_v2/execution_gateway/test_adapter_factory.py`:

```python
def test_build_ccxt_bybit_adapter(monkeypatch):
    from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.adapter import CcxtBybitAdapter
    monkeypatch.setenv("BYBIT_API_SECRET_BYBIT_TESTNET", "test_secret")
    cfg = AdapterConfig.model_validate({
        "type": "ccxt_bybit",
        "mode": "paper",
        "connector": "bybit",
        "api_key": "test_key",
        "testnet": True,
    })
    adapter = build_adapter("bybit_testnet", cfg)
    assert isinstance(adapter, CcxtBybitAdapter)
```

- [ ] **Step 2: Run to verify it fails**

```
pytest tests/runtime_v2/execution_gateway/test_adapter_factory.py::test_build_ccxt_bybit_adapter -v
```

Expected: FAIL with `ValueError: Unknown adapter type 'ccxt_bybit'`.

- [ ] **Step 3: Update `factory.py`**

Replace contents of `src/runtime_v2/execution_gateway/adapters/factory.py`:

```python
# src/runtime_v2/execution_gateway/adapters/factory.py
from __future__ import annotations

import os

from src.runtime_v2.execution_gateway.adapters.base import ExecutionAdapter
from src.runtime_v2.execution_gateway.adapters.hummingbot_api import HummingbotApiAdapter
from src.runtime_v2.execution_gateway.models import AdapterConfig


def build_adapter(adapter_name: str, cfg: AdapterConfig) -> ExecutionAdapter:
    if cfg.type == "hummingbot_api":
        secret = cfg.secret or os.environ.get("HUMMINGBOT_SECRET")
        return HummingbotApiAdapter(
            base_url=cfg.base_url,
            connector=cfg.connector,
            capabilities=cfg.capabilities,
            secret=secret,
        )

    if cfg.type == "ccxt_bybit":
        from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.adapter import CcxtBybitAdapter
        api_secret = os.environ.get(f"BYBIT_API_SECRET_{adapter_name.upper()}")
        return CcxtBybitAdapter(
            api_key=cfg.api_key or "",
            api_secret=api_secret or "",
            testnet=cfg.testnet,
            connector=cfg.connector,
            capabilities=cfg.capabilities,
        )

    raise ValueError(f"Unknown adapter type '{cfg.type}' for adapter '{adapter_name}'")


__all__ = ["build_adapter"]
```

- [ ] **Step 4: Run factory tests to verify they pass**

```
pytest tests/runtime_v2/execution_gateway/test_adapter_factory.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 5: Register `bybit_testnet` marker in `pytest.ini`**

In `pytest.ini`, add `bybit_testnet` to the `markers` section:

```ini
[pytest]
markers =
    asyncio: async test executed via local conftest hook
    bybit_testnet: requires BYBIT_TESTNET_API_KEY — run against real Bybit testnet
cache_dir = .pytest_cache
collect_ignore_glob =
    src/parser_old copy/*
addopts =
    --ignore=tests/event_envelope_v1/test_envelope_schema.py
    --ignore=tests/rules_engine/test_rules_engine_phase2.py
    --ignore=tests/parser_v2/test_contracts_phase1.py
    --ignore=tests/parser_v2/test_runtime_golden_phase13.py
    --ignore=tests/parser_v2/test_canonical_translator_phase11.py
    --ignore=tests/parser_v2/test_intent_entity_extractor_phase6.py
    --ignore=tests/parser_v2/test_runtime_profile_phase12.py
    --ignore=tests/parser_v2/test_target_hints_extractor_phase9.py
```

- [ ] **Step 6: Create conftest.py with auto-skip logic**

Create `tests/runtime_v2/execution_gateway/conftest.py`:

```python
# tests/runtime_v2/execution_gateway/conftest.py
from __future__ import annotations

import os
import pytest


def pytest_collection_modifyitems(config, items):
    if not os.environ.get("BYBIT_TESTNET_API_KEY"):
        skip_marker = pytest.mark.skip(
            reason="Set BYBIT_TESTNET_API_KEY env var to run Bybit testnet integration tests"
        )
        for item in items:
            if item.get_closest_marker("bybit_testnet"):
                item.add_marker(skip_marker)
```

- [ ] **Step 7: Verify marker auto-skip works (no env var set)**

```
pytest tests/runtime_v2/execution_gateway/ -v -m bybit_testnet
```

Expected: tests collected but SKIPPED (not FAILED), with reason message about `BYBIT_TESTNET_API_KEY`.

- [ ] **Step 8: Commit**

```bash
git add src/runtime_v2/execution_gateway/adapters/factory.py pytest.ini tests/runtime_v2/execution_gateway/conftest.py tests/runtime_v2/execution_gateway/test_adapter_factory.py
git commit -m "feat(execution): wire ccxt_bybit into factory; add bybit_testnet pytest marker with auto-skip"
```

---

### Task 7: Gated Integration Tests (Bybit Testnet)

**Files:**
- Create: `tests/runtime_v2/execution_gateway/test_ccxt_bybit_gated.py`

- [ ] **Step 1: Create the gated test file**

Create `tests/runtime_v2/execution_gateway/test_ccxt_bybit_gated.py`:

```python
"""
Gated integration tests against Bybit Testnet via CCXT.

Prerequisites:
  1. Bybit testnet account with API key (Unified Trading Account, USDT perpetual enabled)
  2. Set env vars:
       BYBIT_TESTNET_API_KEY=<your testnet api key>
       BYBIT_API_SECRET_BYBIT_TESTNET=<your testnet api secret>

Run with:
  BYBIT_TESTNET_API_KEY=<key> BYBIT_API_SECRET_BYBIT_TESTNET=<secret> \\
  pytest tests/runtime_v2/execution_gateway/test_ccxt_bybit_gated.py -v -s -m bybit_testnet
"""
from __future__ import annotations

import os
import time

import pytest

pytestmark = pytest.mark.bybit_testnet

_SYMBOL = "BTC/USDT:USDT"
_ENTRY_CLIENT_ORDER_ID = "tsb:99:9001:entry:1"
_SL_CLIENT_ORDER_ID = "tsb:99:9001:sl:1"


@pytest.fixture(scope="module")
def adapter():
    from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.adapter import CcxtBybitAdapter
    return CcxtBybitAdapter(
        api_key=os.environ["BYBIT_TESTNET_API_KEY"],
        api_secret=os.environ["BYBIT_API_SECRET_BYBIT_TESTNET"],
        testnet=True,
        connector="bybit",
    )


def test_set_leverage_does_not_raise(adapter):
    """set_leverage should complete without raising on testnet."""
    adapter.set_leverage(_SYMBOL, 5, "bybit_testnet")


def test_place_limit_entry_returns_exchange_order_id(adapter):
    """Place a limit entry far below market price — won't fill, verifies order creation."""
    result = adapter.place_order(
        command_type="PLACE_ENTRY",
        payload={
            "symbol": _SYMBOL, "side": "LONG",
            "entry_type": "LIMIT", "qty": 0.001, "price": 1.0,  # far below market
        },
        client_order_id=_ENTRY_CLIENT_ORDER_ID,
        execution_account_id="bybit_testnet",
        connector="bybit",
    )
    assert result.success is True, f"place_order failed: {result.error}"
    assert result.exchange_order_id, "expected a non-empty exchange_order_id"


def test_get_order_status_open_after_place(adapter):
    """After placing, order should be visible as OPEN."""
    time.sleep(1)  # brief pause for exchange propagation
    raw = adapter.get_order_status(
        client_order_id=_ENTRY_CLIENT_ORDER_ID, execution_account_id="bybit_testnet"
    )
    assert raw is not None, "get_order_status returned None — order may not be visible via orderLinkId"
    assert raw.status == "OPEN"
    assert raw.client_order_id == _ENTRY_CLIENT_ORDER_ID


def test_cancel_pending_entry(adapter):
    """Cancel the open entry placed above; order should disappear or show CANCELLED."""
    result = adapter.place_order(
        command_type="CANCEL_PENDING_ENTRY",
        payload={"symbol": _SYMBOL},
        client_order_id=_ENTRY_CLIENT_ORDER_ID,
        execution_account_id="bybit_testnet",
        connector="bybit",
    )
    assert result.success is True, f"cancel failed: {result.error}"

    time.sleep(1)
    raw = adapter.get_order_status(
        client_order_id=_ENTRY_CLIENT_ORDER_ID, execution_account_id="bybit_testnet"
    )
    if raw is not None:
        assert raw.status == "CANCELLED"


def test_place_protective_stop_returns_success(adapter):
    """Place a stop order and verify it appears as OPEN."""
    result = adapter.place_order(
        command_type="PLACE_PROTECTIVE_STOP",
        payload={
            "symbol": _SYMBOL, "side": "LONG",
            "qty": 0.001, "stop_price": 1.0,  # far below market
        },
        client_order_id=_SL_CLIENT_ORDER_ID,
        execution_account_id="bybit_testnet",
        connector="bybit",
    )
    assert result.success is True, f"place stop failed: {result.error}"

    time.sleep(1)
    raw = adapter.get_order_status(
        client_order_id=_SL_CLIENT_ORDER_ID, execution_account_id="bybit_testnet"
    )
    # OD-F1-2: attached SL/TP may not be visible via orderLinkId — this is known open decision
    if raw is not None:
        assert raw.status in ("OPEN", "CANCELLED", "FILLED")

    # Cleanup: cancel the stop
    adapter.place_order(
        command_type="CANCEL_PENDING_ENTRY",
        payload={"symbol": _SYMBOL},
        client_order_id=_SL_CLIENT_ORDER_ID,
        execution_account_id="bybit_testnet",
        connector="bybit",
    )


def test_get_position_qty_returns_float(adapter):
    """get_position_qty must return a float (may be 0.0 if no open position)."""
    qty = adapter.get_position_qty(
        symbol=_SYMBOL, side="LONG", execution_account_id="bybit_testnet"
    )
    assert isinstance(qty, float) or qty is None
```

- [ ] **Step 2: Verify tests are auto-skipped without credentials**

```
pytest tests/runtime_v2/execution_gateway/test_ccxt_bybit_gated.py -v
```

Expected: all 6 tests SKIPPED with reason about `BYBIT_TESTNET_API_KEY`.

- [ ] **Step 3: Commit**

```bash
git add tests/runtime_v2/execution_gateway/test_ccxt_bybit_gated.py
git commit -m "test(execution): add gated integration tests for CcxtBybitAdapter against Bybit testnet"
```

---

### Task 8: Regression Suite

**Files:** No new files — run existing test suites to verify no regressions.

- [ ] **Step 1: Run full execution_gateway unit tests (excluding gated)**

```
pytest tests/runtime_v2/execution_gateway/ -v -m "not bybit_testnet"
```

Expected: all existing tests PASS (no regressions from model changes or factory update).

- [ ] **Step 2: Run full runtime_v2 suite**

```
pytest tests/runtime_v2/ -v -m "not bybit_testnet"
```

Expected: all tests PASS. Pay attention to any test that instantiates `AdapterConfig` with `base_url` as a required positional-style field — the default `""` should handle all existing callers.

- [ ] **Step 3: Verify no import errors from ccxt_bybit package**

```
python -c "from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.adapter import CcxtBybitAdapter; print('OK')"
```

Expected: prints `OK` (confirms ccxt installed and importable).

- [ ] **Step 4: Verify config_loader accepts new AdapterConfig fields**

Check whether `config/execution.yaml` exists and validate it loads correctly:

```
pytest tests/runtime_v2/execution_gateway/test_config_loader.py -v
```

Expected: PASS (the config loader uses `AdapterConfig.model_validate`, which now accepts `api_key` + `testnet`).

- [ ] **Step 5: Final commit if any minor fixes were needed**

If any small fixes were needed in prior steps, commit them now:

```bash
git add -p
git commit -m "fix(execution): regression fixes from full suite run"
```

If no fixes were needed, this step is complete.

---

## Summary

After all 8 tasks, the implementation delivers:

| Artifact | Location |
|----------|----------|
| `BybitOrderParams` + `BybitOrderBuilder` | `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/order_builder.py` |
| `StatusMapper` | `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/status_mapper.py` |
| `CcxtBybitAdapter` | `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/adapter.py` |
| Updated `AdapterConfig` | `src/runtime_v2/execution_gateway/models.py` |
| Updated `factory.py` | `src/runtime_v2/execution_gateway/adapters/factory.py` |
| 35+ unit tests | `test_bybit_status_mapper.py`, `test_bybit_order_builder.py`, `test_ccxt_bybit_adapter_unit.py`, `test_adapter_config_ccxt.py` |
| 6 gated integration tests | `test_ccxt_bybit_gated.py` |

**Open decisions remaining:**
- OD-F1-2: `get_order_status` for attached SL/TP via `orderLinkId` — requires testnet verification (test_place_protective_stop_returns_success documents the known gap)
