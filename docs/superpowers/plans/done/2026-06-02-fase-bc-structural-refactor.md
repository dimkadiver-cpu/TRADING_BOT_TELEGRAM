# Fase B+C — Structural Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce a typed `ExchangeEventPayload` contract for both WS and REST ingest paths (Fase B), fix Loop 1/3 scheduling, add startup catch-up, and make `project_clean_log_for_chain` O(1) per new event instead of O(n total events) (Fase C).

**Architecture:** Fase B creates a Pydantic model that acts as the single contract between the exchange ingest paths and the lifecycle processor/notifier; the processor switches to typed attribute access. Fase C adds a `last_projected_event_id` cursor on `ops_trade_chains` so only new lifecycle events are projected on each call.

**Tech Stack:** Python 3.12, Pydantic v2, SQLite, asyncio, pytest

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `src/runtime_v2/execution_gateway/event_ingest/payload.py` | **CREATE** | `ExchangeEventPayload` Pydantic model |
| `src/runtime_v2/execution_gateway/models.py` | **MODIFY** | Extend `RawAdapterOrder` (5 fields) + `WebsocketConfig` (1 field) + `ExecutionRuntime` note (see main.py) |
| `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/status_mapper.py` | **MODIFY** | Capture `exec_fee`, `exec_value`, `exchange_time`, `leaves_qty`, `cum_exec_qty` from `order.info` |
| `src/runtime_v2/execution_gateway/repositories.py` | **MODIFY** | WS ingest path uses `ExchangeEventPayload` |
| `src/runtime_v2/execution_gateway/event_sync.py` | **MODIFY** | REST path uses `ExchangeEventPayload`; remove `is_final` |
| `src/runtime_v2/lifecycle/event_processor.py` | **MODIFY** | Typed attribute access for fill payloads |
| `src/runtime_v2/control_plane/outbox_writer.py` | **MODIFY** | Add `fee_rate`/`exec_value` to TP payload; incremental projection (Fase C) |
| `src/runtime_v2/control_plane/formatters/clean_log.py` | **MODIFY** | Display `fee_rate` and `exec_value` in TP notifications |
| `main.py` | **MODIFY** | Scheduling fixes (Loop 1 + Loop 3) + startup catch-up; `ExecutionRuntime` new fields |
| `db/ops_migrations/012_ops_incremental_projection.sql` | **CREATE** | `ALTER TABLE ops_trade_chains ADD COLUMN last_projected_event_id` |
| `tests/runtime_v2/execution_gateway/test_exchange_event_payload.py` | **CREATE** | Unit tests for `ExchangeEventPayload` |
| `tests/runtime_v2/execution_gateway/test_bybit_status_mapper.py` | **MODIFY** | Add tests for new `RawAdapterOrder` fields |
| `tests/runtime_v2/control_plane/test_outbox_incremental_projection.py` | **CREATE** | Fase C unit tests |
| `tests/runtime_v2/test_main_runtime_bootstrap.py` | **MODIFY** | Scheduling / startup catch-up tests |

---

## Task 1 — ExchangeEventPayload model

**Files:**
- Create: `src/runtime_v2/execution_gateway/event_ingest/payload.py`
- Create: `tests/runtime_v2/execution_gateway/test_exchange_event_payload.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/runtime_v2/execution_gateway/test_exchange_event_payload.py
from __future__ import annotations
import json
from src.runtime_v2.execution_gateway.event_ingest.payload import ExchangeEventPayload


def test_ws_path_all_fields():
    """WS path: all fields present → validation OK."""
    payload = ExchangeEventPayload(
        fill_price=50000.0,
        filled_qty=0.01,
        closed_size=0.01,
        exec_fee=0.275,
        fee_rate=0.00055,
        exec_value=500.0,
        pos_qty=0.0,
        leaves_qty=0.0,
        cum_exec_qty=0.01,
        exchange_event_id="exec123",
        order_id="ord456",
        order_link_id="tsb:1:2:tp:1",
        exchange_time="2026-06-01T12:00:00+00:00",
        tp_level=1,
        command_id=42,
        source="watch_my_trades",
    )
    assert payload.fill_price == 50000.0
    assert payload.fee_rate == 0.00055
    assert payload.closed_size == 0.01
    assert payload.source == "watch_my_trades"


def test_rest_path_ws_only_fields_none():
    """REST path: WS-only fields absent → None, validation OK."""
    payload = ExchangeEventPayload(
        fill_price=50000.0,
        filled_qty=0.01,
        exec_fee=0.55,
        exec_value=500.0,
        exchange_time="2026-06-01T12:00:00+00:00",
        order_id="ord456",
        order_link_id="tsb:1:2:entry:1",
        source="rest_reconciliation",
    )
    assert payload.closed_size is None
    assert payload.fee_rate is None
    assert payload.pos_qty is None


def test_extra_fields_allowed():
    """extra='allow' — unknown fields are preserved, not rejected."""
    raw = {"fill_price": 100.0, "filled_qty": 1.0, "legacy_field": "x"}
    payload = ExchangeEventPayload.model_validate(raw)
    assert payload.fill_price == 100.0


def test_roundtrip_json():
    """model_validate_json(model_dump_json()) is stable."""
    payload = ExchangeEventPayload(
        fill_price=100.0,
        filled_qty=1.0,
        source="watch_my_trades",
    )
    json_str = payload.model_dump_json()
    restored = ExchangeEventPayload.model_validate_json(json_str)
    assert restored.fill_price == 100.0
    assert restored.source == "watch_my_trades"
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/runtime_v2/execution_gateway/test_exchange_event_payload.py -v
```

Expected: `ModuleNotFoundError` — `payload.py` does not exist yet.

- [ ] **Step 3: Create `payload.py`**

```python
# src/runtime_v2/execution_gateway/event_ingest/payload.py
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ExchangeEventPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    # Fill core (guaranteed by both WS and REST paths)
    fill_price: float | None = None
    filled_qty: float | None = None
    exec_fee: float | None = None
    exec_value: float | None = None
    exchange_time: str | None = None
    leaves_qty: float | None = None
    cum_exec_qty: float | None = None

    # WS-only — None on REST path (Bybit API limitation, not ours)
    closed_size: float | None = None
    fee_rate: float | None = None
    pos_qty: float | None = None

    # Identifiers
    exchange_event_id: str | None = None
    order_id: str | None = None
    order_link_id: str | None = None

    # Routing / classification
    tp_level: int | None = None
    command_id: int | None = None
    source: str | None = None


__all__ = ["ExchangeEventPayload"]
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/runtime_v2/execution_gateway/test_exchange_event_payload.py -v
```

Expected: 4 PASSED

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/execution_gateway/event_ingest/payload.py tests/runtime_v2/execution_gateway/test_exchange_event_payload.py
git commit -m "feat(payload): introduce ExchangeEventPayload typed contract"
```

---

## Task 2 — Extend RawAdapterOrder and WebsocketConfig

**Files:**
- Modify: `src/runtime_v2/execution_gateway/models.py`

- [ ] **Step 1: Add fields to `RawAdapterOrder`**

In `models.py`, find `RawAdapterOrder` (line ~86). After `cancel_reason`, add:

```python
class RawAdapterOrder(BaseModel):
    model_config = ConfigDict(extra="ignore")
    client_order_id: str
    exchange_order_id: str | None = None
    adapter_order_id: str | None = None
    status: str
    filled_qty: float = 0.0
    average_price: float | None = None
    cancel_reason: str | None = None
    # REST info fields — from order.info in Bybit response
    exec_fee: float | None = None
    exec_value: float | None = None
    exchange_time: str | None = None
    leaves_qty: float | None = None
    cum_exec_qty: float | None = None
```

- [ ] **Step 2: Add field to `WebsocketConfig`**

In `models.py`, find `WebsocketConfig` (line ~19). After `poll_fallback_period_seconds`, add:

```python
class WebsocketConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    poll_fallback_enabled: bool = True
    poll_fallback_period_seconds: int = 60
    position_reconciliation_interval_seconds: int = 600
```

- [ ] **Step 3: Update `__all__`**

The existing `__all__` already includes `RawAdapterOrder` — no change needed.

- [ ] **Step 4: Run existing model-related tests to confirm no breakage**

```
pytest tests/runtime_v2/execution_gateway/test_adapter_config_ccxt.py tests/runtime_v2/execution_gateway/test_config_loader.py -v
```

Expected: all PASSED

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/execution_gateway/models.py
git commit -m "feat(models): extend RawAdapterOrder and WebsocketConfig with new fields"
```

---

## Task 3 — Update StatusMapper to capture REST info fields

**Files:**
- Modify: `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/status_mapper.py`
- Modify: `tests/runtime_v2/execution_gateway/test_bybit_status_mapper.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/runtime_v2/execution_gateway/test_bybit_status_mapper.py`:

```python
from datetime import timezone
import json


def _order_with_info(status: str, filled: float, average: float | None, info: dict) -> dict:
    return {"id": "ord789", "status": status, "filled": filled, "average": average, "info": info}


def test_status_mapper_captures_exec_fee():
    order = _order_with_info("closed", 0.01, 50000.0, {"cumExecFee": "0.275"})
    raw = StatusMapper.map(order, client_order_id="tsb:1:2:entry:1")
    assert raw.exec_fee == 0.275


def test_status_mapper_captures_exec_value():
    order = _order_with_info("closed", 0.01, 50000.0, {"cumExecValue": "500.0"})
    raw = StatusMapper.map(order, client_order_id="tsb:1:2:entry:1")
    assert raw.exec_value == 500.0


def test_status_mapper_captures_exchange_time():
    # updatedTime is milliseconds since epoch as string
    order = _order_with_info("closed", 0.01, 50000.0, {"updatedTime": "1748764800000"})
    raw = StatusMapper.map(order, client_order_id="tsb:1:2:entry:1")
    assert raw.exchange_time is not None
    assert "2026" in raw.exchange_time  # sanity: year is present


def test_status_mapper_captures_leaves_qty():
    order = _order_with_info("closed", 0.01, 50000.0, {"leavesQty": "0.0"})
    raw = StatusMapper.map(order, client_order_id="tsb:1:2:entry:1")
    assert raw.leaves_qty == 0.0


def test_status_mapper_captures_cum_exec_qty():
    order = _order_with_info("closed", 0.01, 50000.0, {"cumExecQty": "0.01"})
    raw = StatusMapper.map(order, client_order_id="tsb:1:2:entry:1")
    assert raw.cum_exec_qty == 0.01


def test_status_mapper_none_when_info_missing():
    """All new fields default to None when info dict is absent."""
    raw = StatusMapper.map({"id": "x", "status": "closed", "filled": 0.01}, client_order_id="coid")
    assert raw.exec_fee is None
    assert raw.exec_value is None
    assert raw.exchange_time is None
    assert raw.leaves_qty is None
    assert raw.cum_exec_qty is None
```

- [ ] **Step 2: Run to verify failures**

```
pytest tests/runtime_v2/execution_gateway/test_bybit_status_mapper.py -v -k "exec_fee or exec_value or exchange_time or leaves_qty or cum_exec_qty or none_when"
```

Expected: 6 FAILED

- [ ] **Step 3: Update `status_mapper.py`**

```python
# src/runtime_v2/execution_gateway/adapters/ccxt_bybit/status_mapper.py
from __future__ import annotations

import logging
from datetime import datetime, timezone

from src.runtime_v2.execution_gateway.models import RawAdapterOrder

logger = logging.getLogger(__name__)


def _ms_to_iso(ms_str: str | int | None) -> str | None:
    if not ms_str:
        return None
    try:
        ts = int(ms_str) / 1000
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except (ValueError, TypeError):
        return None


class StatusMapper:
    _STATUS_MAP = {
        "open": "OPEN",
        "partially_filled": "OPEN",
        "closed": "FILLED",
        "canceled": "CANCELLED",
        "cancelled": "CANCELLED",
        "expired": "CANCELLED",
        "rejected": "FAILED",
    }

    @staticmethod
    def map(ccxt_order: dict, *, client_order_id: str = "") -> RawAdapterOrder:
        raw_status = str(ccxt_order.get("status") or "open").lower()
        avg = ccxt_order.get("average")
        mapped_status = StatusMapper._STATUS_MAP.get(raw_status, "OPEN")

        cancel_reason: str | None = None
        if mapped_status == "CANCELLED":
            info = ccxt_order.get("info") or {}
            cancel_type = str(info.get("cancelType") or "").strip()
            reject_reason = str(info.get("rejectReason") or "").strip()
            parts = [p for p in (cancel_type, reject_reason) if p and p != "UNKNOWN"]
            cancel_reason = "|".join(parts) if parts else None
            logger.warning(
                "order CANCELLED coid=%s cancelType=%r rejectReason=%r",
                client_order_id or ccxt_order.get("clientOrderId"),
                cancel_type or None,
                reject_reason or None,
            )

        info = ccxt_order.get("info") or {}
        return RawAdapterOrder(
            client_order_id=client_order_id or str(ccxt_order.get("clientOrderId") or ""),
            exchange_order_id=str(ccxt_order.get("id") or ""),
            status=mapped_status,
            filled_qty=float(ccxt_order.get("filled") or 0.0),
            average_price=float(avg) if avg else None,
            cancel_reason=cancel_reason,
            exec_fee=float(info["cumExecFee"]) if info.get("cumExecFee") else None,
            exec_value=float(info["cumExecValue"]) if info.get("cumExecValue") else None,
            exchange_time=_ms_to_iso(info.get("updatedTime")),
            leaves_qty=float(info["leavesQty"]) if info.get("leavesQty") else None,
            cum_exec_qty=float(info["cumExecQty"]) if info.get("cumExecQty") else None,
        )


__all__ = ["StatusMapper"]
```

- [ ] **Step 4: Run all StatusMapper tests to verify pass**

```
pytest tests/runtime_v2/execution_gateway/test_bybit_status_mapper.py -v
```

Expected: all PASSED (existing + 6 new)

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/execution_gateway/adapters/ccxt_bybit/status_mapper.py tests/runtime_v2/execution_gateway/test_bybit_status_mapper.py
git commit -m "feat(status-mapper): capture exec_fee, exec_value, exchange_time, leaves_qty, cum_exec_qty from info"
```

---

## Task 4 — WS ingest path uses ExchangeEventPayload

**Files:**
- Modify: `src/runtime_v2/execution_gateway/repositories.py`

- [ ] **Step 1: Update the `payload` dict in `insert_raw_and_classified`**

In `repositories.py`, find `insert_raw_and_classified` (line ~540). Replace the `payload` dict construction:

Old code (lines ~566–577):
```python
payload = {
    "fill_price": raw.exec_price,
    "filled_qty": raw.exec_qty,
    "closed_size": raw.closed_size,
    "exec_fee": raw.exec_fee,
    "pos_qty": raw.pos_qty,
    "symbol": raw.symbol,
    "side": raw.side,
    "source": classified.source,
    "tp_level": classified.tp_level,
    "exchange_event_id": raw.exchange_event_id,
}
```

New code — build a typed payload and serialize it:

```python
from src.runtime_v2.execution_gateway.event_ingest.payload import ExchangeEventPayload

# (add the import at the top of the file)

ep = ExchangeEventPayload(
    fill_price=raw.exec_price,
    filled_qty=raw.exec_qty,
    closed_size=raw.closed_size,
    exec_fee=raw.exec_fee,
    fee_rate=raw.fee_rate,
    exec_value=raw.exec_value,
    pos_qty=raw.pos_qty,
    leaves_qty=raw.leaves_qty,
    cum_exec_qty=raw.cum_exec_qty,
    exchange_event_id=raw.exchange_event_id,
    order_id=raw.order_id,
    order_link_id=raw.order_link_id,
    exchange_time=raw.exchange_time,
    tp_level=classified.tp_level,
    source=classified.source,
)
payload_json_str = ep.model_dump_json()
```

Then in the `conn.execute` for `ops_exchange_events`, replace `json.dumps(payload)` with `payload_json_str`:
```python
conn.execute(
    "INSERT OR IGNORE INTO ops_exchange_events "
    "(trade_chain_id, event_type, payload_json, processing_status, idempotency_key, received_at) "
    "VALUES (?,?,?,?,?,?)",
    (
        classified.trade_chain_id,
        classified.event_type,
        payload_json_str,
        "NEW",
        ops_idem_key,
        raw.received_at or now,
    ),
)
```

Also add the import at the top of `repositories.py`:
```python
from src.runtime_v2.execution_gateway.event_ingest.payload import ExchangeEventPayload
```

- [ ] **Step 2: Run existing repository and integration tests**

```
pytest tests/runtime_v2/execution_gateway/test_repository_extensions.py tests/runtime_v2/execution_gateway/test_event_ingest_integration.py -v
```

Expected: all PASSED

- [ ] **Step 3: Commit**

```bash
git add src/runtime_v2/execution_gateway/repositories.py
git commit -m "feat(repositories): WS ingest path serializes ExchangeEventPayload"
```

---

## Task 5 — REST sync path uses ExchangeEventPayload + remove is_final

**Files:**
- Modify: `src/runtime_v2/execution_gateway/event_sync.py`

- [ ] **Step 1: Update `_save_fill_event` in `event_sync.py`**

At the top of `event_sync.py`, add the import:
```python
from src.runtime_v2.execution_gateway.event_ingest.payload import ExchangeEventPayload
```

Find `_save_fill_event` (line ~251). Replace the `payload` dict construction. 

Old code (lines ~276–290):
```python
_CLOSE_FILL_TYPES = {"TP_FILLED", "SL_FILLED", "CLOSE_PARTIAL_FILLED", "CLOSE_FULL_FILLED"}
payload: dict = {
    "fill_price": raw.average_price,
    "filled_qty": raw.filled_qty,
    "exec_fee": getattr(raw, "fee", None),
    "closed_size": raw.filled_qty if event_type in _CLOSE_FILL_TYPES else None,
    "command_id": coid.command_id,
}
if coid.role == "tp":
    payload["tp_level"] = coid.sequence
    payload["is_final"] = self._repo.count_active_tps(coid.trade_chain_id) <= 1
    idem_key = f"TP_FILLED:{coid.trade_chain_id}:level:{coid.sequence}"
else:
    idem_key = f"{event_type}:{coid.trade_chain_id}:{exchange_order_id}"
```

New code (remove `is_final`, use ExchangeEventPayload):
```python
_CLOSE_FILL_TYPES = {"TP_FILLED", "SL_FILLED", "CLOSE_PARTIAL_FILLED", "CLOSE_FULL_FILLED"}
if coid.role == "tp":
    tp_level: int | None = coid.sequence
    idem_key = f"TP_FILLED:{coid.trade_chain_id}:level:{coid.sequence}"
else:
    tp_level = None
    idem_key = f"{event_type}:{coid.trade_chain_id}:{exchange_order_id}"

ep = ExchangeEventPayload(
    fill_price=raw.average_price,
    filled_qty=raw.filled_qty,
    closed_size=raw.filled_qty if event_type in _CLOSE_FILL_TYPES else None,
    exec_fee=raw.exec_fee,
    exec_value=raw.exec_value,
    exchange_time=raw.exchange_time,
    leaves_qty=raw.leaves_qty,
    cum_exec_qty=raw.cum_exec_qty,
    order_id=raw.exchange_order_id,
    order_link_id=raw.client_order_id,
    tp_level=tp_level,
    command_id=coid.command_id,
    source="rest_reconciliation",
)
```

Then replace `json.dumps(payload)` in the `return self._repo.insert_exchange_event(...)` call:
```python
return self._repo.insert_exchange_event(
    coid.trade_chain_id, event_type, ep.model_dump_json(), idem_key
)
```

- [ ] **Step 2: Run existing event_sync tests**

```
pytest tests/runtime_v2/execution_gateway/ -v -k "sync"
```

Expected: all PASSED

- [ ] **Step 3: Commit**

```bash
git add src/runtime_v2/execution_gateway/event_sync.py
git commit -m "feat(event-sync): REST path uses ExchangeEventPayload, remove is_final from exchange event"
```

---

## Task 6 — Update event_processor.py typed access

**Files:**
- Modify: `src/runtime_v2/lifecycle/event_processor.py`

The processor uses `json.loads(exchange_event.payload_json)` + `.get()` in fill event handlers. Switch to `ExchangeEventPayload.model_validate_json()` + typed attributes. The lifecycle logic does not change — only the deserialization and field access.

- [ ] **Step 1: Add import at top of event_processor.py**

After the existing imports (around line 16), add:
```python
from src.runtime_v2.execution_gateway.event_ingest.payload import ExchangeEventPayload
```

- [ ] **Step 2: Update `_normalized_fill_payload` to accept the payload model**

The helper currently takes a `dict`. We will pass `payload.model_dump()` (which includes both declared and extra fields) from call sites. No signature change needed.

- [ ] **Step 3: Update `_process_entry_filled`**

Find `_process_entry_filled` (line ~138). Change:
```python
payload = json.loads(exchange_event.payload_json)
fill_price = float(payload.get("fill_price") or 0.0)
fill_qty = float(payload.get("filled_qty") or 0.0)
filled_client_order_id = payload.get("entry_client_order_id")
filled_command_payload = payload.get("entry_command_payload")
```

To:
```python
ep = ExchangeEventPayload.model_validate_json(exchange_event.payload_json)
fill_price = float(ep.fill_price or 0.0)
fill_qty = float(ep.filled_qty or 0.0)
filled_client_order_id = getattr(ep, "entry_client_order_id", None)
filled_command_payload = getattr(ep, "entry_command_payload", None)
```

Also find where `exec_fee` is accessed in `_process_entry_filled` (line ~191):
```python
"exec_fee": payload.get("exec_fee"),
```
Change to:
```python
"exec_fee": ep.exec_fee,
```

- [ ] **Step 4: Update `_process_tp_filled`**

Find `_process_tp_filled` (line ~446). Change:
```python
payload = json.loads(exchange_event.payload_json)
tp_level = int(payload.get("tp_level") or 1)
fill_qty = float(payload.get("filled_qty") or 0.0)
```

To:
```python
ep = ExchangeEventPayload.model_validate_json(exchange_event.payload_json)
tp_level = int(ep.tp_level or 1)
fill_qty = float(ep.filled_qty or 0.0)
```

Find the call to `_normalized_fill_payload` (line ~564):
```python
fill_payload = _normalized_fill_payload(payload, default_qty=fill_qty)
```

Change to:
```python
fill_payload = _normalized_fill_payload(ep.model_dump(), default_qty=fill_qty)
```

- [ ] **Step 5: Update `_process_sl_filled`, `_process_close_full_filled`, `_process_close_partial_filled`**

Each of these methods starts with:
```python
payload = json.loads(exchange_event.payload_json)
```
And calls `_normalized_fill_payload(payload, ...)` and/or accesses `payload.get("filled_qty")`, `payload.get("fill_price")`.

Apply the same pattern: `ep = ExchangeEventPayload.model_validate_json(exchange_event.payload_json)`, then pass `ep.model_dump()` to `_normalized_fill_payload`.

For `_process_sl_filled` (line ~589):
```python
# OLD
payload = json.loads(exchange_event.payload_json)
fill_qty = float(payload.get("filled_qty") or chain.open_position_qty)
...
fill_payload = _normalized_fill_payload(payload, default_qty=fill_qty)

# NEW
ep = ExchangeEventPayload.model_validate_json(exchange_event.payload_json)
fill_qty = float(ep.filled_qty or chain.open_position_qty)
...
fill_payload = _normalized_fill_payload(ep.model_dump(), default_qty=fill_qty)
```

For `_process_close_full_filled` (line ~621) and `_process_close_partial_filled` (line ~658): same pattern.

Leave `_process_stop_moved_confirmed` and `_process_pending_entry_cancelled_confirmed` unchanged — they use `json.loads()` + `.get()` for their non-fill payloads, which is correct.

- [ ] **Step 6: Run event_processor tests**

```
pytest tests/runtime_v2/lifecycle/test_event_processor.py -v
```

Expected: all PASSED

- [ ] **Step 7: Write integration test verifying WS payload deserializes correctly**

Append to `tests/runtime_v2/lifecycle/test_event_processor.py`:

```python
def test_processor_with_full_ws_payload():
    """Processor processes a TP_FILLED event with full ExchangeEventPayload (WS shape)."""
    import json
    from src.runtime_v2.execution_gateway.event_ingest.payload import ExchangeEventPayload

    ep = ExchangeEventPayload(
        fill_price=55000.0,
        filled_qty=0.01,
        closed_size=0.01,
        exec_fee=0.275,
        fee_rate=0.00055,
        exec_value=550.0,
        pos_qty=0.0,
        tp_level=1,
        source="watch_my_trades",
    )
    event = _make_exchange_event(
        event_type="TP_FILLED",
        payload=json.loads(ep.model_dump_json()),
    )
    chain = _make_chain(state="OPEN")
    from src.runtime_v2.lifecycle.event_processor import LifecycleEventProcessor
    processor = LifecycleEventProcessor()
    result = processor.process(event, chain, active_commands=[])
    assert result.new_lifecycle_state == "CLOSED"
    # Verify exec_fee propagated into lifecycle event payload
    lc_payload = json.loads(result.lifecycle_events[0].payload_json)
    assert lc_payload["exec_fee"] == 0.275
```

- [ ] **Step 8: Run the new test**

```
pytest tests/runtime_v2/lifecycle/test_event_processor.py::test_processor_with_full_ws_payload -v
```

Expected: PASSED

- [ ] **Step 9: Commit**

```bash
git add src/runtime_v2/lifecycle/event_processor.py tests/runtime_v2/lifecycle/test_event_processor.py
git commit -m "feat(processor): typed ExchangeEventPayload access in fill event handlers"
```

---

## Task 7 — Notification: fee_rate and exec_value in TP notifications

**Files:**
- Modify: `src/runtime_v2/control_plane/outbox_writer.py`
- Modify: `src/runtime_v2/control_plane/formatters/clean_log.py`

- [ ] **Step 1: Write failing formatter tests**

Append to `tests/runtime_v2/control_plane/test_readonly_formatters.py` (or create `tests/runtime_v2/control_plane/test_tp_filled_new_fields.py`):

```python
# tests/runtime_v2/control_plane/test_tp_filled_new_fields.py
from __future__ import annotations
from src.runtime_v2.control_plane.formatters.clean_log import format_notification


def _tp_payload(fee_rate=None, exec_value=None, include_fee_rate=True, include_exec_value=True):
    p = {
        "chain_id": 1,
        "symbol": "BTCUSDT",
        "side": "LONG",
        "tp_level": 1,
        "tp_price": 55000.0,
        "fill_price": 55000.0,
        "closed_pct": 50.0,
        "pnl": 5.0,
        "fee": 0.275,
        "remaining_pct": 50.0,
        "sl_current": 49000.0,
        "source": "watch_my_trades",
    }
    if include_fee_rate:
        p["fee_rate"] = fee_rate
    if include_exec_value:
        p["exec_value"] = exec_value
    return p


def test_tp_filled_shows_fee_rate_when_present():
    p = _tp_payload(fee_rate=0.00055, exec_value=550.0)
    text = format_notification("TP_FILLED", p)
    assert "Fee rate:" in text
    assert "0.055%" in text


def test_tp_filled_shows_exec_value_when_present():
    p = _tp_payload(fee_rate=0.00055, exec_value=550.0)
    text = format_notification("TP_FILLED", p)
    assert "Value:" in text
    assert "550" in text


def test_tp_filled_shows_na_fee_rate_for_rest_path():
    """REST path: fee_rate is None → show 'n/a'."""
    p = _tp_payload(fee_rate=None, exec_value=500.0)
    text = format_notification("TP_FILLED", p)
    assert "Fee rate: n/a" in text


def test_tp_filled_shows_na_exec_value_for_rest_path():
    """REST path: exec_value is None → show 'n/a'."""
    p = _tp_payload(fee_rate=None, exec_value=None)
    text = format_notification("TP_FILLED", p)
    assert "Value: n/a" in text
```

- [ ] **Step 2: Run to verify failures**

```
pytest tests/runtime_v2/control_plane/test_tp_filled_new_fields.py -v
```

Expected: 4 FAILED (`fee_rate`, `exec_value` not yet in payloads)

Note: if `format_notification` is not directly importable, check `clean_log.py`'s public API and adjust the import accordingly. The formatter function is typically `format_notification(notification_type, payload)`.

- [ ] **Step 3: Update `_build_payload` in outbox_writer.py**

Find the `TP_FILLED` / `TP_FILLED_FINAL` block in `_build_payload` (line ~245). Add `fee_rate` and `exec_value` to the returned dict:

```python
if notification_type in ("TP_FILLED", "TP_FILLED_FINAL"):
    tp_level = ev.get("tp_level")
    tp_price = tps[tp_level - 1] if tp_level and 1 <= tp_level <= len(tps) else None
    closed_qty = ev.get("closed_size", ev.get("filled_qty"))
    fill_price = ev.get("fill_price")
    final_result_data = None
    if notification_type == "TP_FILLED_FINAL":
        final_result_data = _final_result(
            gross_pnl=cumulative_gross_pnl,
            fees=cumulative_fees,
            funding=cumulative_funding,
            allocated_margin=allocated_margin,
            close_reason="TAKE_PROFIT",
        )
    return {
        **base,
        "tp_level": tp_level,
        "tp_price": tp_price,
        "fill_price": fill_price,
        "closed_pct": _closed_pct(closed_qty, filled_entry_qty),
        "pnl": _side_pnl(side, entry_avg_price, fill_price, closed_qty),
        "fee": ev.get("exec_fee"),
        "fee_rate": ev.get("fee_rate"),          # NEW: float (WS) or None (REST)
        "exec_value": ev.get("exec_value"),      # NEW: float or None
        "remaining_pct": _remaining_pct(open_position_qty, filled_entry_qty),
        "sl_current": current_stop_price,
        "be_protection_status": be_protection_status,
        "final_result": final_result_data,
        "source": ev.get("source", "exchange"),
    }
```

- [ ] **Step 4: Update `_tp_filled` in clean_log.py**

Find `_tp_filled` (line ~181). After the `if p.get("fee") is not None:` block, add:

```python
if p.get("fee") is not None:
    lines.append(f"Fee: {_fmt_money(p['fee'])}")
if "fee_rate" in p:
    fr = p.get("fee_rate")
    if fr is not None:
        lines.append(f"Fee rate: {float(fr) * 100:.3f}%")
    else:
        lines.append("Fee rate: n/a")
if "exec_value" in p:
    lines.append(f"Value: {_fmt_money(p.get('exec_value'))}")
```

- [ ] **Step 5: Run the new tests**

```
pytest tests/runtime_v2/control_plane/test_tp_filled_new_fields.py -v
```

Expected: 4 PASSED

- [ ] **Step 6: Run full control_plane test suite to verify no regressions**

```
pytest tests/runtime_v2/control_plane/ -v
```

Expected: all PASSED

- [ ] **Step 7: Commit**

```bash
git add src/runtime_v2/control_plane/outbox_writer.py src/runtime_v2/control_plane/formatters/clean_log.py tests/runtime_v2/control_plane/test_tp_filled_new_fields.py
git commit -m "feat(notifications): add fee_rate and exec_value to TP notification payload and formatter"
```

---

## Task 8 — main.py scheduling fixes + ExecutionRuntime new fields

**Files:**
- Modify: `main.py`

Three changes: (1) Loop 1 gated on `poll_fallback_enabled`, (2) Loop 3 interval from config, (3) startup catch-up.

- [ ] **Step 1: Write failing / behavioral tests**

Append to `tests/runtime_v2/test_main_runtime_bootstrap.py`:

```python
def test_execution_runtime_has_position_reconciliation_interval():
    """ExecutionRuntime exposes position_reconciliation_interval_seconds."""
    from main import ExecutionRuntime
    from unittest.mock import MagicMock
    rt = ExecutionRuntime(
        adapter=MagicMock(),
        execution_worker=MagicMock(),
        sync_worker=MagicMock(),
        ws_watcher=None,
        reconciliation_interval_seconds=None,
        position_reconciliation_interval_seconds=120,
        poll_fallback_enabled=False,
    )
    assert rt.position_reconciliation_interval_seconds == 120
    assert rt.poll_fallback_enabled is False


def test_websocket_config_exposes_position_reconciliation_interval():
    from src.runtime_v2.execution_gateway.models import WebsocketConfig
    cfg = WebsocketConfig(position_reconciliation_interval_seconds=120)
    assert cfg.position_reconciliation_interval_seconds == 120


def test_websocket_config_default_position_reconciliation_interval():
    from src.runtime_v2.execution_gateway.models import WebsocketConfig
    cfg = WebsocketConfig()
    assert cfg.position_reconciliation_interval_seconds == 600
```

- [ ] **Step 2: Run to verify `ExecutionRuntime` field failures**

```
pytest tests/runtime_v2/test_main_runtime_bootstrap.py -v -k "position_reconciliation or poll_fallback"
```

Expected: failures (fields not yet present on `ExecutionRuntime`)

- [ ] **Step 3: Update `ExecutionRuntime` dataclass in `main.py`**

Find the `ExecutionRuntime` dataclass (line ~67):

```python
@dataclass
class ExecutionRuntime:
    adapter: object
    execution_worker: ExecutionCommandWorker
    sync_worker: ExchangeEventSyncWorker
    ws_watcher: BybitWsFillWatcher | None
    reconciliation_interval_seconds: int | None
    position_reconciliation_interval_seconds: int = 600
    poll_fallback_enabled: bool = True
```

- [ ] **Step 4: Update `_build_execution_runtime` to populate new fields**

Find the `return ExecutionRuntime(...)` near the end of `_build_execution_runtime` (line ~163):

```python
return ExecutionRuntime(
    adapter=adapter,
    execution_worker=execution_worker,
    sync_worker=sync_worker,
    ws_watcher=ws_watcher,
    reconciliation_interval_seconds=reconciliation_interval_seconds,
    position_reconciliation_interval_seconds=adapter_cfg.websocket.position_reconciliation_interval_seconds,
    poll_fallback_enabled=adapter_cfg.websocket.poll_fallback_enabled,
)
```

- [ ] **Step 5: Gate Loop 1 on `poll_fallback_enabled`**

In `_async_main`, find the sync_task creation (line ~481):

```python
# OLD
sync_task = None
if execution_runtime is not None:
    sync_task = asyncio.create_task(
        _run_sync_worker(
            sync_worker=execution_runtime.sync_worker,
            logger=logger,
        )
    )

# NEW
sync_task = None
if execution_runtime is not None and execution_runtime.poll_fallback_enabled:
    sync_task = asyncio.create_task(
        _run_sync_worker(
            sync_worker=execution_runtime.sync_worker,
            logger=logger,
        )
    )
```

- [ ] **Step 6: Fix Loop 3 to use interval from config**

Find the `position_reconciliation_task` creation (line ~503):

```python
# OLD
if execution_runtime is not None:
    position_reconciliation_task = asyncio.create_task(
        _run_position_reconciliation_periodically(
            sync_worker=execution_runtime.sync_worker,
            interval_seconds=60,
            logger=logger,
        )
    )

# NEW
if execution_runtime is not None:
    position_reconciliation_task = asyncio.create_task(
        _run_position_reconciliation_periodically(
            sync_worker=execution_runtime.sync_worker,
            interval_seconds=execution_runtime.position_reconciliation_interval_seconds,
            logger=logger,
        )
    )
```

- [ ] **Step 7: Add startup catch-up**

Find the block right after `cp_service.send_startup_notification()` (around line 479) and before `sync_task = None`. Add the catch-up call:

```python
if cp_service is not None:
    try:
        cp_service.send_startup_notification()
    except Exception:
        logger.warning("startup notification failed (non-critical)")

# Startup catch-up: reconcile fills that arrived while bot was down
if execution_runtime is not None:
    try:
        execution_runtime.sync_worker.run_reconciliation()
        execution_runtime.sync_worker.run_position_reconciliation()
    except Exception:
        logger.warning("startup reconciliation failed (non-critical)")

sync_task = None
```

- [ ] **Step 8: Run tests**

```
pytest tests/runtime_v2/test_main_runtime_bootstrap.py -v
```

Expected: all PASSED

- [ ] **Step 9: Run full test suite smoke check**

```
pytest tests/runtime_v2/ -v --tb=short -q
```

Expected: all PASSED

- [ ] **Step 10: Commit**

```bash
git add main.py tests/runtime_v2/test_main_runtime_bootstrap.py
git commit -m "feat(main): gate Loop 1 on poll_fallback_enabled, Loop 3 interval from config, startup catch-up"
```

---

## Task 9 — Fase C: Incremental projection

**Files:**
- Create: `db/ops_migrations/012_ops_incremental_projection.sql`
- Modify: `src/runtime_v2/control_plane/outbox_writer.py`
- Create: `tests/runtime_v2/control_plane/test_outbox_incremental_projection.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/runtime_v2/control_plane/test_outbox_incremental_projection.py
from __future__ import annotations

import json
import sqlite3
import tempfile
import os
import pytest


def _make_db(migrations: list[str]) -> sqlite3.Connection:
    """Build an in-memory-like temp DB by applying a list of SQL strings."""
    conn = sqlite3.connect(":memory:")
    for sql in migrations:
        conn.executescript(sql)
    return conn


def _base_schema() -> str:
    return """
    CREATE TABLE IF NOT EXISTS ops_trade_chains (
        trade_chain_id INTEGER PRIMARY KEY,
        symbol TEXT,
        side TEXT,
        entry_mode TEXT,
        trader_id TEXT,
        plan_state_json TEXT DEFAULT '{}',
        risk_snapshot_json TEXT DEFAULT '{}',
        entry_avg_price REAL,
        current_stop_price REAL,
        source_chat_id TEXT,
        telegram_message_id TEXT,
        cumulative_gross_pnl REAL,
        cumulative_fees REAL,
        cumulative_funding REAL,
        allocated_margin REAL,
        filled_entry_qty REAL,
        open_position_qty REAL,
        be_protection_status TEXT DEFAULT 'NOT_PROTECTED',
        last_projected_event_id INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS ops_lifecycle_events (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_chain_id INTEGER,
        event_type TEXT,
        source_type TEXT,
        previous_state TEXT,
        next_state TEXT,
        source_id TEXT,
        payload_json TEXT DEFAULT '{}',
        idempotency_key TEXT UNIQUE,
        created_at TEXT
    );
    CREATE TABLE IF NOT EXISTS ops_notification_outbox (
        outbox_id INTEGER PRIMARY KEY AUTOINCREMENT,
        notification_type TEXT,
        destination TEXT,
        payload_json TEXT,
        priority TEXT,
        status TEXT DEFAULT 'PENDING',
        dedupe_key TEXT UNIQUE,
        attempts INTEGER DEFAULT 0,
        created_at TEXT,
        send_after TEXT,
        aggregation_group TEXT,
        source_message_id TEXT
    );
    """


def _insert_chain(conn: sqlite3.Connection, chain_id: int) -> None:
    conn.execute(
        "INSERT INTO ops_trade_chains (trade_chain_id, symbol, side) VALUES (?,?,?)",
        (chain_id, "BTCUSDT", "LONG"),
    )
    conn.commit()


def _insert_lifecycle_event(
    conn: sqlite3.Connection,
    chain_id: int,
    event_type: str,
    idem: str,
    payload: dict | None = None,
) -> int:
    cursor = conn.execute(
        "INSERT INTO ops_lifecycle_events (trade_chain_id, event_type, source_type, "
        "payload_json, idempotency_key, created_at) VALUES (?,?,?,?,?,datetime('now'))",
        (chain_id, event_type, "test", json.dumps(payload or {}), idem),
    )
    conn.commit()
    return cursor.lastrowid


def test_projection_updates_last_projected_event_id():
    """After projecting 3 events, last_projected_event_id = max(event_ids)."""
    from src.runtime_v2.control_plane.outbox_writer import project_clean_log_for_chain
    conn = _make_db([_base_schema()])
    _insert_chain(conn, 1)
    id1 = _insert_lifecycle_event(conn, 1, "SIGNAL_ACCEPTED", "sig:1",
                                  {"source": "msg", "entries": [], "sl": None, "tps": []})
    id2 = _insert_lifecycle_event(conn, 1, "ENTRY_FILLED", "entry:1",
                                  {"fill_price": 50000.0, "filled_qty": 0.01, "source": "exchange"})
    id3 = _insert_lifecycle_event(conn, 1, "TP_FILLED", "tp:1",
                                  {"tp_level": 1, "fill_price": 55000.0, "filled_qty": 0.01,
                                   "is_final": True, "exec_fee": 0.3, "closed_size": 0.01,
                                   "source": "exchange"})
    project_clean_log_for_chain(conn, 1)
    row = conn.execute(
        "SELECT last_projected_event_id FROM ops_trade_chains WHERE trade_chain_id=1"
    ).fetchone()
    assert row[0] == max(id1, id2, id3)


def test_incremental_projection_only_processes_new_events():
    """After initial projection, re-projecting only processes events with id > cursor."""
    from src.runtime_v2.control_plane.outbox_writer import project_clean_log_for_chain
    conn = _make_db([_base_schema()])
    _insert_chain(conn, 2)
    for i in range(10):
        _insert_lifecycle_event(conn, 2, "SIGNAL_ACCEPTED", f"sig:2:{i}",
                                {"source": "msg", "entries": [], "sl": None, "tps": []})
    # First projection
    project_clean_log_for_chain(conn, 2)
    cursor_after_first = conn.execute(
        "SELECT last_projected_event_id FROM ops_trade_chains WHERE trade_chain_id=2"
    ).fetchone()[0]

    # Add event 11
    id11 = _insert_lifecycle_event(conn, 2, "SIGNAL_ACCEPTED", "sig:2:10",
                                   {"source": "msg", "entries": [], "sl": None, "tps": []})
    # Second projection — should only process event 11
    count_before = conn.execute("SELECT COUNT(*) FROM ops_notification_outbox").fetchone()[0]
    written = project_clean_log_for_chain(conn, 2)
    count_after = conn.execute("SELECT COUNT(*) FROM ops_notification_outbox").fetchone()[0]
    assert written == 1
    assert count_after == count_before + 1
    new_cursor = conn.execute(
        "SELECT last_projected_event_id FROM ops_trade_chains WHERE trade_chain_id=2"
    ).fetchone()[0]
    assert new_cursor == id11


def test_re_projection_after_reset_no_duplicates():
    """Reset last_projected_event_id=0 → re-project all events; idempotency key prevents duplicates."""
    from src.runtime_v2.control_plane.outbox_writer import project_clean_log_for_chain
    conn = _make_db([_base_schema()])
    _insert_chain(conn, 3)
    for i in range(3):
        _insert_lifecycle_event(conn, 3, "SIGNAL_ACCEPTED", f"sig:3:{i}",
                                {"source": "msg", "entries": [], "sl": None, "tps": []})

    project_clean_log_for_chain(conn, 3)
    outbox_count_after_first = conn.execute("SELECT COUNT(*) FROM ops_notification_outbox").fetchone()[0]

    # Reset cursor
    conn.execute("UPDATE ops_trade_chains SET last_projected_event_id=0 WHERE trade_chain_id=3")
    conn.commit()

    project_clean_log_for_chain(conn, 3)
    outbox_count_after_second = conn.execute("SELECT COUNT(*) FROM ops_notification_outbox").fetchone()[0]
    # No new rows because dedupe_key prevents duplicates
    assert outbox_count_after_second == outbox_count_after_first
```

- [ ] **Step 2: Run to verify failures**

```
pytest tests/runtime_v2/control_plane/test_outbox_incremental_projection.py -v
```

Expected: all 3 FAILED — `last_projected_event_id` column absent and projection not incremental yet.

- [ ] **Step 3: Create the migration file**

```sql
-- db/ops_migrations/012_ops_incremental_projection.sql
ALTER TABLE ops_trade_chains
ADD COLUMN last_projected_event_id INTEGER NOT NULL DEFAULT 0;
```

- [ ] **Step 4: Update `project_clean_log_for_chain` in outbox_writer.py**

Find the `project_clean_log_for_chain` function (line ~474). Replace the `events` query and add the cursor update at the end.

Old events query (lines ~518–523):
```python
events = conn.execute(
    "SELECT event_type, payload_json, idempotency_key "
    "FROM ops_lifecycle_events "
    "WHERE trade_chain_id=? ORDER BY event_id",
    (chain_id,),
).fetchall()
```

New code — read cursor, filter by it, update after:
```python
last_id = (conn.execute(
    "SELECT last_projected_event_id FROM ops_trade_chains WHERE trade_chain_id=?",
    (chain_id,),
).fetchone() or (0,))[0] or 0

events = conn.execute(
    "SELECT event_id, event_type, payload_json, idempotency_key "
    "FROM ops_lifecycle_events "
    "WHERE trade_chain_id=? AND event_id > ? ORDER BY event_id",
    (chain_id, last_id),
).fetchall()
```

Update the event loop to unpack the new `event_id` column (was 3 cols, now 4):

Old loop:
```python
written = 0
for event_type, payload_json, idem in events:
    ...
```

New loop:
```python
written = 0
max_event_id = last_id
for row_event_id, event_type, payload_json, idem in events:
    max_event_id = max(max_event_id, row_event_id)
    ...
```

Add the cursor update at the very end of the function, right before `return written`:

```python
if events:
    conn.execute(
        "UPDATE ops_trade_chains SET last_projected_event_id=? WHERE trade_chain_id=?",
        (max_event_id, chain_id),
    )
return written
```

- [ ] **Step 5: Run the new tests**

```
pytest tests/runtime_v2/control_plane/test_outbox_incremental_projection.py -v
```

Expected: 3 PASSED

- [ ] **Step 6: Run full control_plane test suite to verify no regressions**

```
pytest tests/runtime_v2/control_plane/ -v
```

Expected: all PASSED (existing tests use in-memory schemas that must include `last_projected_event_id`)

Note: if existing tests fail because their schema doesn't have the new column, update their `conftest.py` or SQL fixtures to include `last_projected_event_id INTEGER NOT NULL DEFAULT 0` in the `ops_trade_chains` DDL.

- [ ] **Step 7: Verify the migration applies cleanly**

```
python -c "
from src.storage.migrations import apply_migrations
result = apply_migrations('db/test_migration_check.db', 'db/ops_migrations')
print('Applied:', result)
import os; os.remove('db/test_migration_check.db')
"
```

Expected: prints `Applied: 1` (migration 012 was the only new one) — or adjust to however the project applies migrations.

- [ ] **Step 8: Commit**

```bash
git add db/ops_migrations/012_ops_incremental_projection.sql src/runtime_v2/control_plane/outbox_writer.py tests/runtime_v2/control_plane/test_outbox_incremental_projection.py
git commit -m "feat(projection): incremental clean_log projection with last_projected_event_id cursor"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Covered by |
|-----------------|-----------|
| ExchangeEventPayload model with all fields | Task 1 |
| RawAdapterOrder extended (5 fields) | Task 2 |
| WebsocketConfig `position_reconciliation_interval_seconds` | Task 2 |
| StatusMapper captures REST info fields | Task 3 |
| WS ingest path uses ExchangeEventPayload | Task 4 |
| REST path uses ExchangeEventPayload, removes `is_final` | Task 5 |
| event_processor typed attribute access | Task 6 |
| TP notifications show `fee_rate` and `exec_value` | Task 7 |
| Loop 1 gated on `poll_fallback_enabled` | Task 8 |
| Loop 3 interval from config | Task 8 |
| Startup catch-up on bot restart | Task 8 |
| `ops_trade_chains.last_projected_event_id` migration | Task 9 |
| `project_clean_log_for_chain` incremental | Task 9 |
| Re-projection after reset remains idempotent | Task 9 |

**Placeholder scan:** None found.

**Type consistency check:**
- `ExchangeEventPayload` defined in Task 1, imported in Tasks 4, 5, 6 — same module path `src.runtime_v2.execution_gateway.event_ingest.payload`
- `RawAdapterOrder` new fields defined in Task 2, consumed by StatusMapper (Task 3) and event_sync (Task 5)
- `WebsocketConfig.position_reconciliation_interval_seconds` defined in Task 2, consumed in Task 8
- `ExecutionRuntime.position_reconciliation_interval_seconds` and `.poll_fallback_enabled` defined in Task 8, used in same file
- `last_projected_event_id` column added in Task 9 migration, accessed in outbox_writer changes in same Task 9

**Schema note for existing tests:** Existing control_plane tests that create `ops_trade_chains` in test fixtures need `last_projected_event_id INTEGER NOT NULL DEFAULT 0` added to their DDL. Check `tests/runtime_v2/control_plane/conftest.py` — if it has a table DDL, update it. If tests apply migrations from disk, the migration file handles it automatically.

---

**Plan complete and saved to `docs/superpowers/plans/2026-06-02-fase-bc-structural-refactor.md`.**

**Two execution options:**

**1. Subagent-Driven (recommended)** — Fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
