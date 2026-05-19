# Hummingbot Demo Parallel Stack — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy a parallel Hummingbot demo stack with connector `bybit_perpetual_demo`, connect it to Runtime V2 via a renamed neutral `HummingbotApiAdapter`, and validate end-to-end on Bybit Main Demo.

**Architecture:** `HummingbotApiPaperAdapter` is replaced by a neutral `HummingbotApiAdapter` that accepts capabilities from config. An adapter factory builds adapter instances from `AdapterConfig`. The demo Docker stack runs on port `8001` with a volume-mounted Python constants patch that adds the `https://api-demo.bybit.com` URL to the bybit_perpetual connector. Changing environment is a one-line config change.

**Tech Stack:** Python 3.12, Pydantic v2, httpx, Docker Compose, Hummingbot (pinned tag), pytest

---

## File Map

| Action | Path |
|---|---|
| Create | `src/runtime_v2/execution_gateway/adapters/hummingbot_api.py` |
| Modify | `src/runtime_v2/execution_gateway/adapters/hummingbot_api_paper.py` |
| Create | `src/runtime_v2/execution_gateway/adapters/factory.py` |
| Modify | `src/runtime_v2/execution_gateway/gateway.py` |
| Modify | `config/execution.yaml` |
| Create | `tests/runtime_v2/execution_gateway/test_hummingbot_api_neutral.py` |
| Create | `tests/runtime_v2/execution_gateway/test_adapter_factory.py` |
| Create | `tests/runtime_v2/execution_gateway/test_live_safety_gate.py` |
| Modify | `tests/runtime_v2/execution_gateway/test_gateway.py` |
| Modify | `tests/runtime_v2/execution_gateway/test_config_loader.py` |
| Create | `hummingbot_demo_patch/bybit_perpetual_demo_constants_patch.py` |
| Create | `hummingbot_demo_patch/SETUP.md` |
| Create | `docker-compose.demo.yml` |
| Create | `.env.demo.example` |
| Modify | `.gitignore` |
| Create | `tests/runtime_v2/execution_gateway/test_hummingbot_demo_gated.py` |

---

## Task 1: Create HummingbotApiAdapter (neutral)

**Files:**
- Create: `src/runtime_v2/execution_gateway/adapters/hummingbot_api.py`
- Modify: `src/runtime_v2/execution_gateway/adapters/hummingbot_api_paper.py`

- [ ] **Step 1.1: Write tests for the neutral adapter**

Create `tests/runtime_v2/execution_gateway/test_hummingbot_api_neutral.py`:

```python
# tests/runtime_v2/execution_gateway/test_hummingbot_api_neutral.py
from __future__ import annotations

import pytest
from src.runtime_v2.execution_gateway.adapters.hummingbot_api import HummingbotApiAdapter
from src.runtime_v2.execution_gateway.models import AdapterCapabilities


def test_adapter_uses_capabilities_from_config():
    caps = AdapterCapabilities(
        place_entry=True,
        protective_stop_native=False,
        take_profit_native=False,
        bracket_order=False,
        move_stop=False,
        close_partial=True,
        close_full=True,
        executor_position=False,
    )
    adapter = HummingbotApiAdapter(
        base_url="http://localhost:8001",
        connector="bybit_perpetual_demo",
        capabilities=caps,
    )
    assert adapter.get_capabilities().protective_stop_native is False
    assert adapter.get_capabilities().close_full is True


def test_adapter_default_capabilities_when_none_passed():
    adapter = HummingbotApiAdapter(
        base_url="http://localhost:8000",
        connector="bybit_perpetual_testnet",
    )
    assert adapter.get_capabilities().place_entry is True
    assert adapter.get_capabilities().protective_stop_native is True


def test_auth_headers_no_secret():
    headers = HummingbotApiAdapter._auth_headers(None)
    assert headers == {}


def test_auth_headers_bearer():
    headers = HummingbotApiAdapter._auth_headers("mytoken")
    assert headers == {"Authorization": "Bearer mytoken"}


def test_auth_headers_basic():
    headers = HummingbotApiAdapter._auth_headers("user:pass")
    assert "Authorization" in headers
    assert headers["Authorization"].startswith("Basic ")


def test_build_order_body_place_entry_limit():
    adapter = HummingbotApiAdapter(
        base_url="http://localhost:8001",
        connector="bybit_perpetual_demo",
    )
    body = adapter._build_order_body(
        "PLACE_ENTRY",
        {"symbol": "BTC/USDT", "side": "LONG", "entry_type": "LIMIT", "price": 50000.0, "qty": 0.01},
        client_order_id="tsb:1:1:entry:1",
        execution_account_id="master_account",
    )
    assert body["connector_name"] == "bybit_perpetual_demo"
    assert body["trade_type"] == "BUY"
    assert body["order_type"] == "LIMIT"
    assert body["price"] == 50000.0
    assert body["position_action"] == "OPEN"


def test_build_order_body_place_entry_market():
    adapter = HummingbotApiAdapter(
        base_url="http://localhost:8001",
        connector="bybit_perpetual_demo",
    )
    body = adapter._build_order_body(
        "PLACE_ENTRY",
        {"symbol": "ETH/USDT", "side": "SHORT", "entry_type": "MARKET", "qty": 0.1},
        client_order_id="tsb:2:2:entry:1",
        execution_account_id="master_account",
    )
    assert body["trade_type"] == "SELL"
    assert body["order_type"] == "MARKET"
    assert "price" not in body


def test_build_order_body_close_full():
    adapter = HummingbotApiAdapter(
        base_url="http://localhost:8001",
        connector="bybit_perpetual_demo",
    )
    body = adapter._build_order_body(
        "CLOSE_FULL",
        {"symbol": "BTC/USDT", "side": "LONG", "qty": 0.01},
        client_order_id="tsb:1:5:entry:1",
        execution_account_id="master_account",
    )
    assert body["trade_type"] == "SELL"
    assert body["order_type"] == "MARKET"
    assert body["reduce_only"] is True
    assert body["position_action"] == "CLOSE"


def test_backward_compat_alias():
    from src.runtime_v2.execution_gateway.adapters.hummingbot_api_paper import HummingbotApiPaperAdapter
    assert HummingbotApiPaperAdapter is HummingbotApiAdapter
```

- [ ] **Step 1.2: Run tests to verify they fail**

```
pytest tests/runtime_v2/execution_gateway/test_hummingbot_api_neutral.py -v
```

Expected: ImportError or AttributeError — `HummingbotApiAdapter` does not exist yet.

- [ ] **Step 1.3: Create HummingbotApiAdapter**

Create `src/runtime_v2/execution_gateway/adapters/hummingbot_api.py`:

```python
# src/runtime_v2/execution_gateway/adapters/hummingbot_api.py
from __future__ import annotations

import logging
from base64 import b64encode

import httpx

from src.runtime_v2.execution_gateway.adapters.base import ExecutionAdapter
from src.runtime_v2.execution_gateway.models import AdapterCapabilities, AdapterResult, RawAdapterOrder

logger = logging.getLogger(__name__)

_SIDE_MAP = {"LONG": "BUY", "SHORT": "SELL"}
_CLOSE_SIDE_MAP = {"LONG": "SELL", "SHORT": "BUY"}

_DEFAULT_CAPABILITIES = AdapterCapabilities(
    place_entry=True,
    protective_stop_native=True,
    take_profit_native=True,
    bracket_order=False,
    move_stop=True,
    close_partial=True,
    close_full=True,
    executor_position=False,
)


class HummingbotApiAdapter(ExecutionAdapter):
    def __init__(
        self,
        base_url: str,
        connector: str,
        capabilities: AdapterCapabilities | None = None,
        timeout: float = 10.0,
        secret: str | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._connector = connector
        self._capabilities = capabilities or _DEFAULT_CAPABILITIES
        headers = self._auth_headers(secret)
        self._client = httpx.Client(
            base_url=self._base_url,
            timeout=timeout,
            headers=headers,
        )

    @staticmethod
    def _auth_headers(secret: str | None) -> dict[str, str]:
        if not secret:
            return {}
        if ":" in secret:
            token = b64encode(secret.encode("utf-8")).decode("ascii")
            return {"Authorization": f"Basic {token}"}
        return {"Authorization": f"Bearer {secret}"}

    def close(self) -> None:
        self._client.close()

    def get_capabilities(self) -> AdapterCapabilities:
        return self._capabilities

    def set_leverage(self, symbol: str, leverage: int, execution_account_id: str) -> None:
        trading_pair = symbol.replace("/", "-")
        self._client.post(
            f"/trading/{execution_account_id}/{self._connector}/leverage",
            json={"trading_pair": trading_pair, "leverage": leverage},
        ).raise_for_status()

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
            body = self._build_order_body(command_type, payload, client_order_id, execution_account_id)
            resp = self._client.post("/trading/orders", json=body)
            resp.raise_for_status()
            data = resp.json()
            return AdapterResult(
                success=True,
                adapter_order_id=str(data.get("id") or data.get("order_id") or ""),
                exchange_order_id=str(data.get("exchange_order_id") or data.get("order_id") or ""),
            )
        except httpx.HTTPStatusError as e:
            return AdapterResult(success=False, error=str(e), reason="exchange_rejected")
        except Exception:
            raise

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
                "account_names": [execution_account_id],
                "connector_names": [self._connector],
                "limit": 100,
            })
            resp.raise_for_status()
            data = resp.json()
            orders = data.get("data") or (data if isinstance(data, list) else [])
            orders = [
                o for o in orders
                if o.get("client_order_id") == client_order_id
                or o.get("order_id") == client_order_id
                or o.get("id") == client_order_id
            ]
            if not orders:
                return None
            o = orders[0]
            status = str(o.get("status") or ("FILLED" if o.get("is_done") else "OPEN")).upper()
            return RawAdapterOrder(
                client_order_id=client_order_id,
                exchange_order_id=str(o.get("exchange_order_id") or o.get("order_id") or ""),
                adapter_order_id=str(o.get("id") or o.get("order_id") or ""),
                status=status,
                filled_qty=float(o.get("executed_amount_base", 0)),
                average_price=float(o.get("average_executed_price", 0)) or None,
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            logger.warning("get_order_status HTTP error for %s: %s", client_order_id, e)
            raise
        except Exception:
            raise

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
        client_order_id: str, execution_account_id: str,
    ) -> dict:
        symbol = payload["symbol"]
        side = payload["side"]
        trading_pair = symbol.replace("/", "-")

        base: dict = {
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


__all__ = ["HummingbotApiAdapter"]
```

- [ ] **Step 1.4: Update hummingbot_api_paper.py to alias**

Replace the entire content of `src/runtime_v2/execution_gateway/adapters/hummingbot_api_paper.py`:

```python
# src/runtime_v2/execution_gateway/adapters/hummingbot_api_paper.py
from __future__ import annotations

from src.runtime_v2.execution_gateway.adapters.hummingbot_api import HummingbotApiAdapter

HummingbotApiPaperAdapter = HummingbotApiAdapter

__all__ = ["HummingbotApiPaperAdapter"]
```

- [ ] **Step 1.5: Run tests to verify they pass**

```
pytest tests/runtime_v2/execution_gateway/test_hummingbot_api_neutral.py -v
```

Expected: all 8 tests PASS.

- [ ] **Step 1.6: Verify existing adapter tests still pass**

```
pytest tests/runtime_v2/execution_gateway/test_auth.py tests/runtime_v2/execution_gateway/test_hummingbot_adapter.py -v
```

Expected: all PASS (test_hummingbot_adapter.py is gated and will SKIP unless env var set — that is correct).

- [ ] **Step 1.7: Commit**

```
git add src/runtime_v2/execution_gateway/adapters/hummingbot_api.py \
        src/runtime_v2/execution_gateway/adapters/hummingbot_api_paper.py \
        tests/runtime_v2/execution_gateway/test_hummingbot_api_neutral.py
git commit -m "feat(execution): rename HummingbotApiPaperAdapter to neutral HummingbotApiAdapter with configurable capabilities"
```

---

## Task 2: Create adapter factory

**Files:**
- Create: `src/runtime_v2/execution_gateway/adapters/factory.py`
- Create: `tests/runtime_v2/execution_gateway/test_adapter_factory.py`

- [ ] **Step 2.1: Write factory tests**

Create `tests/runtime_v2/execution_gateway/test_adapter_factory.py`:

```python
# tests/runtime_v2/execution_gateway/test_adapter_factory.py
from __future__ import annotations

import pytest
import yaml
from pathlib import Path

from src.runtime_v2.execution_gateway.adapters.factory import build_adapter
from src.runtime_v2.execution_gateway.adapters.hummingbot_api import HummingbotApiAdapter
from src.runtime_v2.execution_gateway.models import AdapterCapabilities, AdapterConfig


def _make_cfg(**kwargs) -> AdapterConfig:
    defaults = {
        "type": "hummingbot_api",
        "mode": "demo",
        "base_url": "http://localhost:8001",
        "connector": "bybit_perpetual_demo",
    }
    defaults.update(kwargs)
    return AdapterConfig.model_validate(defaults)


def test_build_hummingbot_api_adapter():
    cfg = _make_cfg()
    adapter = build_adapter("hummingbot_api_demo", cfg)
    assert isinstance(adapter, HummingbotApiAdapter)


def test_build_adapter_passes_capabilities():
    caps = AdapterCapabilities(
        place_entry=True,
        protective_stop_native=False,
        take_profit_native=False,
        bracket_order=False,
        move_stop=False,
        close_partial=True,
        close_full=True,
        executor_position=False,
    )
    cfg = _make_cfg(capabilities=caps.model_dump())
    adapter = build_adapter("hummingbot_api_demo", cfg)
    assert adapter.get_capabilities().protective_stop_native is False
    assert adapter.get_capabilities().close_full is True


def test_build_adapter_unknown_type_raises():
    cfg = _make_cfg(type="unknown_type")
    with pytest.raises(ValueError, match="Unknown adapter type"):
        build_adapter("bad_adapter", cfg)


def test_build_adapter_testnet_mode():
    cfg = _make_cfg(
        mode="testnet",
        base_url="http://localhost:8000",
        connector="bybit_perpetual_testnet",
    )
    adapter = build_adapter("hummingbot_api_testnet", cfg)
    assert isinstance(adapter, HummingbotApiAdapter)
```

- [ ] **Step 2.2: Run tests to verify they fail**

```
pytest tests/runtime_v2/execution_gateway/test_adapter_factory.py -v
```

Expected: ImportError — `factory` module does not exist.

- [ ] **Step 2.3: Create factory**

Create `src/runtime_v2/execution_gateway/adapters/factory.py`:

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
    raise ValueError(f"Unknown adapter type '{cfg.type}' for adapter '{adapter_name}'")


__all__ = ["build_adapter"]
```

- [ ] **Step 2.4: Run tests to verify they pass**

```
pytest tests/runtime_v2/execution_gateway/test_adapter_factory.py -v
```

Expected: all 4 tests PASS.

- [ ] **Step 2.5: Commit**

```
git add src/runtime_v2/execution_gateway/adapters/factory.py \
        tests/runtime_v2/execution_gateway/test_adapter_factory.py
git commit -m "feat(execution): add adapter factory build_adapter(name, cfg)"
```

---

## Task 3: Update execution.yaml with demo adapter

**Files:**
- Modify: `config/execution.yaml`

- [ ] **Step 3.1: Add hummingbot_api_demo to execution.yaml**

Replace the entire content of `config/execution.yaml`:

```yaml
# config/execution.yaml
execution:
  default_adapter: hummingbot_api_demo

  account_routing:
    default:
      adapter: hummingbot_api_demo
      execution_account_id: master_account

  adapters:
    hummingbot_api_paper:
      type: hummingbot_api
      mode: paper
      base_url: http://localhost:8000
      connector: bybit_perpetual_testnet
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

    hummingbot_api_demo:
      type: hummingbot_api
      mode: demo
      base_url: http://localhost:8001
      connector: bybit_perpetual_demo
      leverage: 1

      entry_execution:
        mode: b_entry_stop_then_tp

      retry:
        max_attempts: 3
        backoff_seconds: [30, 90, 300]

      capabilities:
        place_entry: true
        protective_stop_native: false
        take_profit_native: false
        bracket_order: false
        move_stop: false
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

- [ ] **Step 3.2: Verify config loads without error**

```
python -c "from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader; c = ExecutionConfigLoader().load(); print('adapters:', list(c.adapters.keys())); print('default:', c.default_adapter)"
```

Expected output:
```
adapters: ['hummingbot_api_paper', 'hummingbot_api_demo']
default: hummingbot_api_demo
```

- [ ] **Step 3.3: Commit**

```
git add config/execution.yaml
git commit -m "config(execution): add hummingbot_api_demo adapter, set as default"
```

---

## Task 4: Strengthen live safety gate in gateway

**Files:**
- Modify: `src/runtime_v2/execution_gateway/gateway.py`

- [ ] **Step 4.1: Write tests for the strengthened gate**

Create `tests/runtime_v2/execution_gateway/test_live_safety_gate.py`:

```python
# tests/runtime_v2/execution_gateway/test_live_safety_gate.py
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest
import yaml


def _apply_migrations(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    for f in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


def _insert_chain(db_path: str) -> None:
    import datetime as dt
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_trade_chains (trade_chain_id, source_enrichment_id, "
        "canonical_message_id, raw_message_id, trader_id, account_id, symbol, side, "
        "lifecycle_state, entry_mode, management_plan_json, created_at, updated_at) "
        "VALUES (1,1,10,100,'trader_a','acc_1','BTC/USDT','LONG','WAITING_ENTRY','ONE_SHOT','{}',datetime('now'),datetime('now'))"
    )
    conn.commit()
    conn.close()


def _insert_cmd(db_path: str, cmd_id: int) -> None:
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_execution_commands "
        "(command_id, trade_chain_id, command_type, status, payload_json, "
        "idempotency_key, created_at, updated_at) VALUES (?,1,'PLACE_ENTRY','PENDING',?,?,?,?)",
        (cmd_id,
         json.dumps({"symbol": "BTC/USDT", "side": "LONG", "entry_type": "LIMIT",
                     "price": 50000.0, "qty": 0.01, "sequence": 1}),
         f"idem:{cmd_id}", now, now),
    )
    conn.commit()
    conn.close()


def _live_config(allow_live_trading: bool) -> "ExecutionConfig":
    from src.runtime_v2.execution_gateway.models import ExecutionConfig
    raw = {
        "default_adapter": "live_adapter",
        "account_routing": {"default": {"adapter": "live_adapter", "execution_account_id": "acc_1"}},
        "adapters": {
            "live_adapter": {
                "type": "hummingbot_api",
                "mode": "live",
                "base_url": "http://localhost:8002",
                "connector": "bybit_perpetual_main",
                "live_safety": {"allow_live_trading": allow_live_trading},
            }
        },
    }
    return ExecutionConfig.model_validate(raw)


@pytest.fixture
def ops_db(tmp_path):
    db = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db)
    _insert_chain(db)
    return db


def test_live_mode_without_env_var_blocked(ops_db, monkeypatch):
    monkeypatch.delenv("TSB_ALLOW_LIVE_TRADING", raising=False)
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_cmd(ops_db, 2001)
    repo = GatewayCommandRepository(ops_db)
    gw = ExecutionGateway(
        config=_live_config(allow_live_trading=True),
        adapter_registry={"live_adapter": FakeAdapter()},
        repo=repo,
    )
    cmd = repo.get_pending_batch()[0]
    gw.process(cmd, account_id="acc_1")

    conn = sqlite3.connect(ops_db)
    status = conn.execute("SELECT status FROM ops_execution_commands WHERE command_id=2001").fetchone()[0]
    conn.close()
    assert status == "REVIEW_REQUIRED"


def test_live_mode_with_env_var_but_config_false_blocked(ops_db, monkeypatch):
    monkeypatch.setenv("TSB_ALLOW_LIVE_TRADING", "YES_I_UNDERSTAND")
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_cmd(ops_db, 2002)
    repo = GatewayCommandRepository(ops_db)
    gw = ExecutionGateway(
        config=_live_config(allow_live_trading=False),
        adapter_registry={"live_adapter": FakeAdapter()},
        repo=repo,
    )
    cmd = repo.get_pending_batch()[0]
    gw.process(cmd, account_id="acc_1")

    conn = sqlite3.connect(ops_db)
    status = conn.execute("SELECT status FROM ops_execution_commands WHERE command_id=2002").fetchone()[0]
    conn.close()
    assert status == "REVIEW_REQUIRED"


def test_demo_mode_is_not_blocked_by_live_gate(ops_db):
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_cmd(ops_db, 2003)
    repo = GatewayCommandRepository(ops_db)
    gw = ExecutionGateway(
        config=ExecutionConfigLoader("config/execution.yaml").load(),
        adapter_registry={"hummingbot_api_demo": FakeAdapter()},
        repo=repo,
    )
    cmd = repo.get_pending_batch()[0]
    gw.process(cmd, account_id="acc_1")

    conn = sqlite3.connect(ops_db)
    status = conn.execute("SELECT status FROM ops_execution_commands WHERE command_id=2003").fetchone()[0]
    conn.close()
    assert status == "SENT"
```

- [ ] **Step 4.2: Run tests to verify they fail**

```
pytest tests/runtime_v2/execution_gateway/test_live_safety_gate.py -v
```

Expected: `test_live_mode_without_env_var_blocked` FAIL (current gateway does not check env var), `test_demo_mode_is_not_blocked_by_live_gate` FAIL (adapter key mismatch after config change in Task 3).

- [ ] **Step 4.3: Update gateway live safety gate**

In `src/runtime_v2/execution_gateway/gateway.py`, add `import os` at the top and replace the live safety block (lines 61-65):

```python
# replace:
        # Live safety check — always block live mode in MVP
        if adapter_cfg.mode == "live":
            self._repo.mark_review_required(
                cmd.command_id, reason="live_trading_blocked_mvp"
            )
            return

# with:
        if adapter_cfg.mode == "live":
            if not adapter_cfg.live_safety.allow_live_trading:
                self._repo.mark_review_required(
                    cmd.command_id, reason="live_trading_not_allowed_in_config"
                )
                return
            if os.environ.get("TSB_ALLOW_LIVE_TRADING") != "YES_I_UNDERSTAND":
                self._repo.mark_review_required(
                    cmd.command_id, reason="live_trading_env_gate_not_set"
                )
                return
```

- [ ] **Step 4.4: Run live gate tests to verify they pass**

```
pytest tests/runtime_v2/execution_gateway/test_live_safety_gate.py -v
```

Expected: all 3 tests PASS.

- [ ] **Step 4.5: Commit**

```
git add src/runtime_v2/execution_gateway/gateway.py \
        tests/runtime_v2/execution_gateway/test_live_safety_gate.py
git commit -m "feat(execution): strengthen live safety gate — require allow_live_trading config + TSB_ALLOW_LIVE_TRADING env var"
```

---

## Task 5: Fix existing gateway tests after config change

The gateway tests still pass `{"hummingbot_api_paper": FakeAdapter()}` but `execution.yaml` now has `default_adapter: hummingbot_api_demo`.

**Files:**
- Modify: `tests/runtime_v2/execution_gateway/test_gateway.py`

- [ ] **Step 5.1: Run existing gateway tests to confirm they now fail**

```
pytest tests/runtime_v2/execution_gateway/test_gateway.py -v
```

Expected: `test_place_entry_pending_to_sent`, `test_adapter_error_sets_retry` FAIL — command lands in REVIEW_REQUIRED because `hummingbot_api_demo` not in registry.

- [ ] **Step 5.2: Update adapter key in test_gateway.py**

In `tests/runtime_v2/execution_gateway/test_gateway.py`, replace all occurrences of `"hummingbot_api_paper"` in `adapter_registry=` arguments with `"hummingbot_api_demo"`:

- Line 72: `adapter_registry={"hummingbot_api_paper": FakeAdapter()}` → `adapter_registry={"hummingbot_api_demo": FakeAdapter()}`
- Line 100: same replacement
- Line 130: same replacement
- Line 160: line 160 uses a custom config (live mode) and `"hummingbot_api_paper"` key — change to match that custom config's adapter name. The existing test builds config from yaml and overrides mode to `live` while still using `"hummingbot_api_paper"` as the adapter name. Keep it as `"hummingbot_api_paper"` since that test builds its own config that still references `hummingbot_api_paper`.

The four replacements in full context:

```python
# test_place_entry_pending_to_sent (line ~69)
    gw = ExecutionGateway(
        config=ExecutionConfigLoader("config/execution.yaml").load(),
        adapter_registry={"hummingbot_api_demo": FakeAdapter()},
        repo=repo,
    )

# test_capability_missing_produces_review_required (line ~97)
    gw = ExecutionGateway(
        config=ExecutionConfigLoader("config/execution.yaml").load(),
        adapter_registry={"hummingbot_api_demo": FakeAdapter(
            capabilities=AdapterCapabilities(protective_stop_native=False)
        )},
        repo=repo,
    )

# test_adapter_error_sets_retry (line ~127)
    gw = ExecutionGateway(
        config=ExecutionConfigLoader("config/execution.yaml").load(),
        adapter_registry={"hummingbot_api_demo": FakeAdapter(simulate_timeout=True)},
        repo=repo,
    )

# test_live_trading_blocked — keep "hummingbot_api_paper" because it builds its own config from yaml
# that modifies hummingbot_api_paper, not hummingbot_api_demo — no change needed there
```

- [ ] **Step 5.3: Run all gateway tests**

```
pytest tests/runtime_v2/execution_gateway/test_gateway.py tests/runtime_v2/execution_gateway/test_live_safety_gate.py -v
```

Expected: all tests PASS.

- [ ] **Step 5.4: Commit**

```
git add tests/runtime_v2/execution_gateway/test_gateway.py
git commit -m "test(execution): update gateway tests to use hummingbot_api_demo adapter key"
```

---

## Task 6: Add multi-adapter config loader tests

**Files:**
- Modify: `tests/runtime_v2/execution_gateway/test_config_loader.py`

- [ ] **Step 6.1: Add multi-adapter tests to test_config_loader.py**

Append to `tests/runtime_v2/execution_gateway/test_config_loader.py`:

```python
def test_load_multi_adapter_config(tmp_path):
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    cfg = {
        "execution": {
            "default_adapter": "hummingbot_api_demo",
            "account_routing": {
                "default": {"adapter": "hummingbot_api_demo", "execution_account_id": "master_account"}
            },
            "adapters": {
                "hummingbot_api_paper": {
                    "type": "hummingbot_api",
                    "mode": "paper",
                    "base_url": "http://localhost:8000",
                    "connector": "bybit_perpetual_testnet",
                },
                "hummingbot_api_demo": {
                    "type": "hummingbot_api",
                    "mode": "demo",
                    "base_url": "http://localhost:8001",
                    "connector": "bybit_perpetual_demo",
                },
            },
        }
    }
    import yaml
    p = tmp_path / "execution.yaml"
    p.write_text(yaml.dump(cfg))
    config = ExecutionConfigLoader(str(p)).load()
    assert config.default_adapter == "hummingbot_api_demo"
    assert "hummingbot_api_paper" in config.adapters
    assert "hummingbot_api_demo" in config.adapters
    assert config.adapters["hummingbot_api_demo"].connector == "bybit_perpetual_demo"
    assert config.adapters["hummingbot_api_demo"].mode == "demo"


def test_demo_adapter_capabilities_parse():
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    config = ExecutionConfigLoader("config/execution.yaml").load()
    demo_caps = config.adapters["hummingbot_api_demo"].capabilities
    assert demo_caps.place_entry is True
    assert demo_caps.protective_stop_native is False
    assert demo_caps.take_profit_native is False
    assert demo_caps.close_full is True


def test_demo_adapter_live_safety_false():
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    config = ExecutionConfigLoader("config/execution.yaml").load()
    assert config.adapters["hummingbot_api_demo"].live_safety.allow_live_trading is False
```

- [ ] **Step 6.2: Run config loader tests**

```
pytest tests/runtime_v2/execution_gateway/test_config_loader.py -v
```

Expected: all tests PASS including 3 new ones.

- [ ] **Step 6.3: Commit**

```
git add tests/runtime_v2/execution_gateway/test_config_loader.py
git commit -m "test(execution): add multi-adapter config loader tests for demo adapter"
```

---

## Task 7: Create Hummingbot connector patch files

**Files:**
- Create: `hummingbot_demo_patch/bybit_perpetual_demo_constants_patch.py`
- Create: `hummingbot_demo_patch/SETUP.md`

The connector patch must be verified against the exact pinned Hummingbot image before mounting. This task creates the patch files and the verification procedure.

- [ ] **Step 7.1: Find the constants file in the pinned image**

Run this command to inspect the bybit_perpetual connector structure inside the stock image:

```bash
docker run --rm hummingbot/hummingbot:2.3.0 \
  find /hummingbot_src/hummingbot/connector/derivative/bybit_perpetual \
  -name "*.py" | sort
```

Expected output should include a file like:
```
/hummingbot_src/hummingbot/connector/derivative/bybit_perpetual/bybit_perpetual_constants.py
```

If the path or version differs, update the volume mount path in `docker-compose.demo.yml` accordingly.

- [ ] **Step 7.2: Inspect the REST_URLS dict in the constants file**

```bash
docker run --rm hummingbot/hummingbot:2.3.0 \
  grep -n "REST_URLS\|api-testnet\|api\.bybit" \
  /hummingbot_src/hummingbot/connector/derivative/bybit_perpetual/bybit_perpetual_constants.py
```

Confirm the existing URL dict pattern. It should look like:

```python
REST_URLS = {
    "bybit_perpetual_main": "https://api.bybit.com",
    "bybit_perpetual_testnet": "https://api-testnet.bybit.com",
}
```

If the structure is different, adapt the patch file accordingly.

- [ ] **Step 7.3: Copy the constants file out of the image for patching**

```bash
docker create --name hb_tmp hummingbot/hummingbot:2.3.0
docker cp hb_tmp:/hummingbot_src/hummingbot/connector/derivative/bybit_perpetual/bybit_perpetual_constants.py \
  hummingbot_demo_patch/bybit_perpetual_constants.py
docker rm hb_tmp
```

This gives you the exact original file. Verify it before modifying.

- [ ] **Step 7.4: Create the patched constants file**

Edit `hummingbot_demo_patch/bybit_perpetual_constants.py` (the copy from Step 7.3) to add the demo URL. Find the `REST_URLS` dict and add:

```python
"bybit_perpetual_demo": "https://api-demo.bybit.com",
```

Also add the WebSocket URL if the constants file defines WS URLs per domain. If WS demo URL is not documented by Bybit, add a comment and reuse the main URL as placeholder (polling fallback applies):

```python
# bybit_perpetual_demo WS: same host as REST demo — verify before enabling WS
"bybit_perpetual_demo": "wss://stream.bybit.com",  # placeholder, replace if Bybit publishes demo WS
```

- [ ] **Step 7.5: Create SETUP.md with verification instructions**

Create `hummingbot_demo_patch/SETUP.md`:

```markdown
# Hummingbot Demo Patch — Setup and Verification

## What this patch does

Adds `bybit_perpetual_demo` domain to `bybit_perpetual_constants.py`, pointing to
`https://api-demo.bybit.com`. Mounted via volume into the Hummingbot demo container.

## Pinned image

`hummingbot/hummingbot:2.3.0` — do not change without re-running Step 7.1 verification.

## Mount path

```yaml
volumes:
  - ./hummingbot_demo_patch/bybit_perpetual_constants.py:/hummingbot_src/hummingbot/connector/derivative/bybit_perpetual/bybit_perpetual_constants.py
```

## Verify mount is active

```bash
docker exec hummingbot-demo grep -c "bybit_perpetual_demo" \
  /hummingbot_src/hummingbot/connector/derivative/bybit_perpetual/bybit_perpetual_constants.py
```

Expected output: `1` or more (number of matches).

## Verify connector is recognized

```bash
docker exec hummingbot-demo python -c \
  "from hummingbot.connector.derivative.bybit_perpetual.bybit_perpetual_constants import REST_URLS; \
   print(REST_URLS.get('bybit_perpetual_demo'))"
```

Expected output: `https://api-demo.bybit.com`

## If Hummingbot image is updated

1. Run Step 7.1 to get new file list.
2. Copy new constants file: `docker cp ...` (Step 7.3).
3. Re-apply the demo URL addition.
4. Re-run verification.
```

- [ ] **Step 7.6: Commit**

```
git add hummingbot_demo_patch/
git commit -m "feat(docker): add bybit_perpetual_demo connector patch for Hummingbot demo stack"
```

---

## Task 8: Create Docker demo stack

**Files:**
- Create: `docker-compose.demo.yml`
- Create: `.env.demo.example`
- Modify: `.gitignore`

- [ ] **Step 8.1: Update .gitignore**

Add to `.gitignore`:

```
# Hummingbot demo stack — runtime dirs (no secrets, no logs)
hummingbot_demo_conf/
hummingbot_demo_logs/
hummingbot_demo_data/
.env.demo
```

- [ ] **Step 8.2: Create .env.demo.example**

Create `.env.demo.example`:

```env
# .env.demo.example — copy to .env.demo and fill in values
# Never commit .env.demo

# Bybit Main Demo API keys
BYBIT_DEMO_API_KEY=your_demo_api_key_here
BYBIT_DEMO_API_SECRET=your_demo_api_secret_here

# Hummingbot demo stack secrets
HUMMINGBOT_DEMO_CONFIG_PASSWORD=choose_a_password
HUMMINGBOT_DEMO_DB_PASSWORD=choose_a_db_password

# Optional: Hummingbot Backend API auth token (leave empty for no auth)
HUMMINGBOT_SECRET=
```

- [ ] **Step 8.3: Create docker-compose.demo.yml**

Create `docker-compose.demo.yml`:

```yaml
# docker-compose.demo.yml
# Hummingbot demo stack — parallel to existing stack, isolated resources
# Start: docker compose -f docker-compose.demo.yml --env-file .env.demo up -d
# Stop:  docker compose -f docker-compose.demo.yml --env-file .env.demo down

services:
  hummingbot-demo:
    image: hummingbot/hummingbot:2.3.0
    container_name: hummingbot-demo
    volumes:
      - ./hummingbot_demo_patch/bybit_perpetual_constants.py:/hummingbot_src/hummingbot/connector/derivative/bybit_perpetual/bybit_perpetual_constants.py:ro
      - ./hummingbot_demo_conf:/conf
      - ./hummingbot_demo_logs:/logs
      - ./hummingbot_demo_data:/data
    environment:
      - CONFIG_PASSWORD=${HUMMINGBOT_DEMO_CONFIG_PASSWORD}
    networks:
      - hummingbot-demo-net
    depends_on:
      - hummingbot-demo-broker
      - hummingbot-demo-postgres
    restart: unless-stopped

  hummingbot-demo-backend-api:
    image: hummingbot/backend-api:latest
    container_name: hummingbot-demo-backend-api
    ports:
      - "8001:8000"
    environment:
      - BROKER_HOST=hummingbot-demo-broker
      - BROKER_PORT=1883
      - DATABASE_URL=postgresql://hummingbot:${HUMMINGBOT_DEMO_DB_PASSWORD}@hummingbot-demo-postgres/hummingbot_demo
    networks:
      - hummingbot-demo-net
    depends_on:
      - hummingbot-demo-broker
      - hummingbot-demo-postgres
    restart: unless-stopped

  hummingbot-demo-broker:
    image: eclipse-mosquitto:2.0
    container_name: hummingbot-demo-broker
    networks:
      - hummingbot-demo-net
    restart: unless-stopped

  hummingbot-demo-postgres:
    image: postgres:15
    container_name: hummingbot-demo-postgres
    volumes:
      - hummingbot_demo_postgres:/var/lib/postgresql/data
    environment:
      - POSTGRES_DB=hummingbot_demo
      - POSTGRES_USER=hummingbot
      - POSTGRES_PASSWORD=${HUMMINGBOT_DEMO_DB_PASSWORD}
    networks:
      - hummingbot-demo-net
    restart: unless-stopped

networks:
  hummingbot-demo-net:
    driver: bridge

volumes:
  hummingbot_demo_postgres:
```

- [ ] **Step 8.4: Create the runtime directories**

```bash
mkdir -p hummingbot_demo_conf hummingbot_demo_logs hummingbot_demo_data
```

- [ ] **Step 8.5: Verify compose file parses**

```bash
docker compose -f docker-compose.demo.yml config --quiet
```

Expected: no errors.

- [ ] **Step 8.6: Commit**

```
git add docker-compose.demo.yml .env.demo.example .gitignore
git commit -m "feat(docker): add docker-compose.demo.yml for Hummingbot parallel demo stack"
```

---

## Task 9: Gated demo integration tests

**Files:**
- Create: `tests/runtime_v2/execution_gateway/test_hummingbot_demo_gated.py`

These tests run only when the demo stack is live. They verify the actual Hummingbot → Bybit Demo path.

- [ ] **Step 9.1: Create gated test file**

Create `tests/runtime_v2/execution_gateway/test_hummingbot_demo_gated.py`:

```python
# tests/runtime_v2/execution_gateway/test_hummingbot_demo_gated.py
"""
Gated integration tests against live Hummingbot demo stack + Bybit Main Demo.

Run with:
  RUN_HUMMINGBOT_DEMO_TESTS=1 \
  HUMMINGBOT_DEMO_API_URL=http://localhost:8001 \
  HUMMINGBOT_DEMO_CONNECTOR=bybit_perpetual_demo \
  HUMMINGBOT_DEMO_ACCOUNT=master_account \
  pytest tests/runtime_v2/execution_gateway/test_hummingbot_demo_gated.py -v -s

Requirements before running:
  1. docker compose -f docker-compose.demo.yml --env-file .env.demo up -d
  2. Hummingbot demo configured with Bybit Demo API keys
  3. bybit_perpetual_demo connector active in Hummingbot demo container
"""
from __future__ import annotations

import os
import time

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("RUN_HUMMINGBOT_DEMO_TESTS"),
    reason="Set RUN_HUMMINGBOT_DEMO_TESTS=1 to run",
)

DEMO_URL = os.environ.get("HUMMINGBOT_DEMO_API_URL", "http://localhost:8001")
CONNECTOR = os.environ.get("HUMMINGBOT_DEMO_CONNECTOR", "bybit_perpetual_demo")
ACCOUNT = os.environ.get("HUMMINGBOT_DEMO_ACCOUNT", "master_account")
SECRET = os.environ.get("HUMMINGBOT_SECRET")

_TEST_SYMBOL = "BTC/USDT"
_TEST_CLIENT_ORDER_ID = "tsb:demo:9999:entry:1"


@pytest.fixture(scope="module")
def adapter():
    from src.runtime_v2.execution_gateway.adapters.hummingbot_api import HummingbotApiAdapter
    from src.runtime_v2.execution_gateway.models import AdapterCapabilities
    caps = AdapterCapabilities(
        place_entry=True,
        protective_stop_native=False,
        take_profit_native=False,
        bracket_order=False,
        move_stop=False,
        close_partial=True,
        close_full=True,
        executor_position=False,
    )
    return HummingbotApiAdapter(
        base_url=DEMO_URL,
        connector=CONNECTOR,
        capabilities=caps,
        secret=SECRET,
    )


def test_01_api_reachable():
    import httpx
    resp = httpx.get(f"{DEMO_URL}/docs", timeout=5)
    assert resp.status_code == 200, f"Hummingbot demo API not reachable at {DEMO_URL}"


def test_02_connector_available(adapter):
    caps = adapter.get_capabilities()
    assert caps.place_entry is True
    assert caps.protective_stop_native is False


def test_03_set_leverage(adapter):
    adapter.set_leverage(_TEST_SYMBOL, 1, ACCOUNT)


def test_04_place_entry_limit(adapter):
    result = adapter.place_order(
        command_type="PLACE_ENTRY",
        payload={
            "symbol": _TEST_SYMBOL,
            "side": "LONG",
            "entry_type": "LIMIT",
            "price": 1.0,
            "qty": 0.001,
            "sequence": 1,
        },
        client_order_id=_TEST_CLIENT_ORDER_ID,
        execution_account_id=ACCOUNT,
        connector=CONNECTOR,
    )
    assert result.success, f"place_order failed: {result.error}"


def test_05_get_order_status(adapter):
    time.sleep(1)
    status = adapter.get_order_status(
        client_order_id=_TEST_CLIENT_ORDER_ID,
        execution_account_id=ACCOUNT,
    )
    assert status is not None, "Order not found after place"
    assert status.client_order_id == _TEST_CLIENT_ORDER_ID


def test_06_cancel_order(adapter):
    result = adapter.cancel_order(
        client_order_id=_TEST_CLIENT_ORDER_ID,
        execution_account_id=ACCOUNT,
        connector=CONNECTOR,
    )
    assert result.success, f"cancel_order failed: {result.error}"


def test_07_get_position(adapter):
    qty = adapter.get_position_qty(
        symbol=_TEST_SYMBOL,
        side="LONG",
        execution_account_id=ACCOUNT,
    )
    # qty is None if no open position — both None and 0.0 are valid here
    assert qty is None or qty >= 0.0
```

- [ ] **Step 9.2: Verify the gated tests skip without env var**

```
pytest tests/runtime_v2/execution_gateway/test_hummingbot_demo_gated.py -v
```

Expected: all 7 tests SKIP with message `Set RUN_HUMMINGBOT_DEMO_TESTS=1 to run`.

- [ ] **Step 9.3: Commit**

```
git add tests/runtime_v2/execution_gateway/test_hummingbot_demo_gated.py
git commit -m "test(execution): add gated integration tests for Hummingbot demo stack"
```

---

## Task 10: Full test suite verification

- [ ] **Step 10.1: Run all non-gated execution gateway tests**

```
pytest tests/runtime_v2/execution_gateway/ -v --ignore=tests/runtime_v2/execution_gateway/test_hummingbot_demo_gated.py --ignore=tests/runtime_v2/execution_gateway/test_hummingbot_adapter.py
```

Expected: all tests PASS, gated tests SKIPPED.

- [ ] **Step 10.2: Run full Runtime V2 test suite**

```
pytest tests/runtime_v2/ -v
```

Expected: all non-gated tests PASS.

- [ ] **Step 10.3: Tag completion commit**

```
git commit --allow-empty -m "chore: hummingbot demo parallel stack — code tasks complete, pending gated validation"
```

---

## Operational Runbook (post-code)

After all code tasks are complete, validate the demo stack with real credentials:

```bash
# 1. copy env file and fill in Bybit Demo API keys
cp .env.demo.example .env.demo
# edit .env.demo with real keys

# 2. start the demo stack
docker compose -f docker-compose.demo.yml --env-file .env.demo up -d

# 3. verify connector patch is active (see hummingbot_demo_patch/SETUP.md)
docker exec hummingbot-demo python -c \
  "from hummingbot.connector.derivative.bybit_perpetual.bybit_perpetual_constants import REST_URLS; \
   print(REST_URLS.get('bybit_perpetual_demo'))"
# expected: https://api-demo.bybit.com

# 4. configure bybit_perpetual_demo in Hummingbot with demo API keys
docker attach hummingbot-demo
# inside Hummingbot: connect bybit_perpetual_demo

# 5. run gated tests
RUN_HUMMINGBOT_DEMO_TESTS=1 \
HUMMINGBOT_DEMO_API_URL=http://localhost:8001 \
HUMMINGBOT_DEMO_CONNECTOR=bybit_perpetual_demo \
HUMMINGBOT_DEMO_ACCOUNT=master_account \
pytest tests/runtime_v2/execution_gateway/test_hummingbot_demo_gated.py -v -s
```
