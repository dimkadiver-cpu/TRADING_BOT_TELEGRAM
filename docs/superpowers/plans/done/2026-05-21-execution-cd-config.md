# Execution C/D + Config Separation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement execution modes C_SIMPLE_ATTACHED and D_POSITION_TPSL, rimuovere leverage/hedge_mode da execution.yaml, e spostare quelle policy in operation_config.yaml.

**Architecture:** Ogni segnale viene classificato in C (caso semplice: 1 entry + 1 SL + 1 TP) o D (tutti gli altri). C usa `create_order` con TP/SL attached. D usa `/v5/position/trading-stop` dopo il fill. leverage e hedge_mode viaggiano nel payload del comando, non nella config adapter.

**Tech Stack:** Python 3.12, Pydantic v2, CCXT, SQLite (aiosqlite), pytest

**Spec di riferimento:** `docs/Raggionamento/Esecuzione_piu_config/PRD_execution_C_D_config_unified.md`

---

## File map

| File | Azione | Responsabilità |
|---|---|---|
| `src/runtime_v2/execution_gateway/models.py` | Modifica | Rimuove campi legacy, aggiunge `ExecutionStrategyConfig` |
| `src/runtime_v2/execution_gateway/adapters/factory.py` | Modifica | Usa `api_key_env`/`api_secret_env`, rimuove `hedge_mode` globale |
| `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/adapter.py` | Modifica | Aggiunge handler C/D, rimuove `self._hedge_mode` globale |
| `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/order_builder.py` | Modifica | Aggiunge `BybitOrderParams` action types C/D, nuovi handler |
| `src/runtime_v2/lifecycle/models.py` | Modifica | Aggiunge nuovi `CommandType` literals |
| `src/runtime_v2/lifecycle/entry_gate.py` | Modifica | Decision matrix C/D, payload con leverage/hedge_mode/position_idx |
| `src/runtime_v2/lifecycle/risk_capacity.py` | Modifica | Validazione `risk.leverage <= account.max_leverage`, hedge_mode in snapshot |
| `src/runtime_v2/signal_enrichment/models.py` | Modifica | Aggiunge `account: AccountConfig` a `EffectiveEnrichmentConfig` |
| `src/runtime_v2/signal_enrichment/config_loader.py` | Modifica | Popola `account` in `EffectiveEnrichmentConfig` |
| `config/execution.yaml` | Modifica | Formato minimale senza campi legacy |
| `tests/runtime_v2/execution_gateway/test_adapter_config_ccxt.py` | Modifica | Aggiorna test per nuovo formato `AdapterConfig` |
| `tests/runtime_v2/execution_gateway/test_config_loader.py` | Modifica | Aggiorna test che usano `capabilities`/`testnet` |
| `tests/runtime_v2/execution_gateway/test_execution_strategy_config.py` | Nuovo | Test `ExecutionStrategyConfig` |
| `tests/runtime_v2/execution_gateway/test_bybit_order_builder_cd.py` | Nuovo | Test handler C/D in `BybitOrderBuilder` |
| `tests/runtime_v2/lifecycle/test_entry_gate_cd.py` | Nuovo | Test decision matrix C/D in `LifecycleEntryGate` |
| `tests/runtime_v2/lifecycle/test_risk_leverage_validation.py` | Nuovo | Test validazione max_leverage |

---

## Task 1: `ExecutionStrategyConfig` + cleanup `AdapterConfig`

**Files:**
- Modify: `src/runtime_v2/execution_gateway/models.py`
- Modify: `tests/runtime_v2/execution_gateway/test_adapter_config_ccxt.py`
- Create: `tests/runtime_v2/execution_gateway/test_execution_strategy_config.py`

- [ ] **Step 1: Scrivi i nuovi test per `ExecutionStrategyConfig`**

Crea `tests/runtime_v2/execution_gateway/test_execution_strategy_config.py`:

```python
from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.runtime_v2.execution_gateway.models import AdapterConfig, ExecutionStrategyConfig


def test_strategy_config_defaults():
    s = ExecutionStrategyConfig()
    assert s.default_mode == "D_POSITION_TPSL"
    assert s.simple_attached_enabled is True
    assert s.trigger_by == "MarkPrice"
    assert s.one_tp_mode == "FULL"
    assert s.multi_tp_mode == "PARTIAL"


def test_strategy_config_invalid_mode():
    with pytest.raises(ValidationError):
        ExecutionStrategyConfig(default_mode="X_UNKNOWN")


def test_adapter_config_new_format_accepted():
    cfg = AdapterConfig.model_validate({
        "type": "ccxt_bybit",
        "mode": "demo",
        "connector": "bybit",
        "api_key_env": "BYBIT_API_KEY_DEMO",
        "api_secret_env": "BYBIT_API_SECRET_DEMO",
    })
    assert cfg.api_key_env == "BYBIT_API_KEY_DEMO"
    assert cfg.api_secret_env == "BYBIT_API_SECRET_DEMO"
    assert cfg.strategy.default_mode == "D_POSITION_TPSL"


def test_adapter_config_strategy_block_accepted():
    cfg = AdapterConfig.model_validate({
        "type": "ccxt_bybit",
        "mode": "demo",
        "connector": "bybit",
        "strategy": {
            "default_mode": "C_SIMPLE_ATTACHED",
            "simple_attached_enabled": False,
        },
    })
    assert cfg.strategy.default_mode == "C_SIMPLE_ATTACHED"
    assert cfg.strategy.simple_attached_enabled is False


def test_adapter_config_deprecated_leverage_raises():
    with pytest.raises(ValidationError):
        AdapterConfig.model_validate({
            "type": "ccxt_bybit",
            "mode": "demo",
            "connector": "bybit",
            "leverage": 10,
        })


def test_adapter_config_deprecated_hedge_mode_raises():
    with pytest.raises(ValidationError):
        AdapterConfig.model_validate({
            "type": "ccxt_bybit",
            "mode": "demo",
            "connector": "bybit",
            "hedge_mode": True,
        })


def test_adapter_config_deprecated_entry_execution_raises():
    with pytest.raises(ValidationError):
        AdapterConfig.model_validate({
            "type": "ccxt_bybit",
            "mode": "demo",
            "connector": "bybit",
            "entry_execution": {"mode": "b_entry_stop_then_tp"},
        })
```

- [ ] **Step 2: Esegui i test — devono fallire**

```
pytest tests/runtime_v2/execution_gateway/test_execution_strategy_config.py -v
```

Atteso: FAIL (ImportError su `ExecutionStrategyConfig`)

- [ ] **Step 3: Aggiorna `src/runtime_v2/execution_gateway/models.py`**

Sostituisci l'intero contenuto con:

```python
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RetryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_attempts: int = 3
    backoff_seconds: list[int] = Field(default_factory=lambda: [30, 90, 300])


class LiveSafetyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allow_live_trading: bool = False


class WebsocketConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    poll_fallback_enabled: bool = True
    poll_fallback_period_seconds: int = 60


class ExecutionStrategyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    default_mode: Literal["C_SIMPLE_ATTACHED", "D_POSITION_TPSL"] = "D_POSITION_TPSL"
    simple_attached_enabled: bool = True
    trigger_by: Literal["MarkPrice", "LastPrice", "IndexPrice"] = "MarkPrice"
    one_tp_mode: Literal["FULL"] = "FULL"
    multi_tp_mode: Literal["PARTIAL"] = "PARTIAL"


class AdapterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str
    mode: str
    connector: str
    api_key_env: str | None = None
    api_secret_env: str | None = None
    strategy: ExecutionStrategyConfig = Field(default_factory=ExecutionStrategyConfig)
    websocket: WebsocketConfig = Field(default_factory=WebsocketConfig)
    retry: RetryConfig = Field(default_factory=RetryConfig)
    live_safety: LiveSafetyConfig = Field(default_factory=LiveSafetyConfig)


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
    status: str
    filled_qty: float = 0.0
    average_price: float | None = None
    cancel_reason: str | None = None

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
    warnings: list[str] = Field(default_factory=list)


__all__ = [
    "RetryConfig", "LiveSafetyConfig", "WebsocketConfig",
    "ExecutionStrategyConfig",
    "AdapterConfig", "AccountRoutingEntry", "ExecutionConfig",
    "RawAdapterOrder", "AdapterResult",
]
```

- [ ] **Step 4: Aggiorna `tests/runtime_v2/execution_gateway/test_adapter_config_ccxt.py`**

Rimuovi i test per campi eliminati (`api_key`, `testnet`, `hedge_mode`) e sostituisci con:

```python
from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.runtime_v2.execution_gateway.models import AdapterConfig


def test_adapter_config_ccxt_bybit_type_accepted():
    cfg = AdapterConfig.model_validate({
        "type": "ccxt_bybit",
        "mode": "paper",
        "connector": "bybit",
    })
    assert cfg.type == "ccxt_bybit"


def test_adapter_config_api_key_env_field():
    cfg = AdapterConfig.model_validate({
        "type": "ccxt_bybit",
        "mode": "paper",
        "connector": "bybit",
        "api_key_env": "MY_KEY_ENV",
    })
    assert cfg.api_key_env == "MY_KEY_ENV"


def test_adapter_config_api_key_env_defaults_none():
    cfg = AdapterConfig.model_validate({
        "type": "ccxt_bybit",
        "mode": "paper",
        "connector": "bybit",
    })
    assert cfg.api_key_env is None
    assert cfg.api_secret_env is None


def test_adapter_config_websocket_defaults():
    cfg = AdapterConfig.model_validate({
        "type": "ccxt_bybit",
        "mode": "paper",
        "connector": "bybit",
    })
    assert cfg.websocket.enabled is False
    assert cfg.websocket.poll_fallback_enabled is True
    assert cfg.websocket.poll_fallback_period_seconds == 60


def test_adapter_config_websocket_custom():
    cfg = AdapterConfig.model_validate({
        "type": "ccxt_bybit",
        "mode": "paper",
        "connector": "bybit",
        "websocket": {"enabled": True, "poll_fallback_period_seconds": 30},
    })
    assert cfg.websocket.enabled is True
    assert cfg.websocket.poll_fallback_period_seconds == 30


def test_adapter_config_strategy_defaults():
    cfg = AdapterConfig.model_validate({
        "type": "ccxt_bybit",
        "mode": "paper",
        "connector": "bybit",
    })
    assert cfg.strategy.default_mode == "D_POSITION_TPSL"
    assert cfg.strategy.simple_attached_enabled is True


def test_adapter_config_deprecated_fields_rejected():
    for field in ("leverage", "hedge_mode", "entry_execution", "capabilities", "testnet", "api_key"):
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            AdapterConfig.model_validate({
                "type": "ccxt_bybit", "mode": "paper", "connector": "bybit",
                field: "anything",
            })
```

- [ ] **Step 5: Aggiorna `test_config_loader.py`** — rimuovi test che usano `capabilities` e `testnet` nei fixture inline (i test che usano `config/execution.yaml` reale verranno aggiornati al Task 4)

In `test_load_multi_adapter_config` rimuovi `"testnet": True` dai due adapter:

```python
def test_load_multi_adapter_config(tmp_path):
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    import yaml
    cfg = {
        "execution": {
            "default_adapter": "bybit_demo",
            "account_routing": {
                "default": {"adapter": "bybit_demo", "execution_account_id": "master_account"}
            },
            "adapters": {
                "bybit_paper": {
                    "type": "ccxt_bybit", "mode": "paper", "connector": "bybit",
                },
                "bybit_demo": {
                    "type": "ccxt_bybit", "mode": "demo", "connector": "bybit",
                },
            },
        }
    }
    p = tmp_path / "execution.yaml"
    p.write_text(yaml.dump(cfg))
    config = ExecutionConfigLoader(str(p)).load()
    assert config.default_adapter == "bybit_demo"
    assert "bybit_paper" in config.adapters
    assert config.adapters["bybit_demo"].connector == "bybit"
    assert config.adapters["bybit_demo"].mode == "demo"
```

Rimuovi i due test che usano `config/execution.yaml` reale (verranno aggiornati al Task 4 insieme al file YAML):
- `test_demo_adapter_capabilities_parse`
- `test_demo_adapter_live_safety_false`

- [ ] **Step 6: Esegui i test nuovi**

```
pytest tests/runtime_v2/execution_gateway/test_execution_strategy_config.py tests/runtime_v2/execution_gateway/test_adapter_config_ccxt.py -v
```

Atteso: tutti i test passano

- [ ] **Step 7: Esegui la suite completa per verificare regressioni**

```
pytest tests/runtime_v2/execution_gateway/ -v --tb=short
```

Atteso: verde, eventuali fail solo nei test che usano `config/execution.yaml` reale (verrà sistemato al Task 4)

- [ ] **Step 8: Commit**

```
git add src/runtime_v2/execution_gateway/models.py \
        tests/runtime_v2/execution_gateway/test_execution_strategy_config.py \
        tests/runtime_v2/execution_gateway/test_adapter_config_ccxt.py \
        tests/runtime_v2/execution_gateway/test_config_loader.py
git commit -m "feat(execution): add ExecutionStrategyConfig, remove legacy AdapterConfig fields"
```

---

## Task 2: Aggiorna `factory.py` — `api_key_env`/`api_secret_env`

**Files:**
- Modify: `src/runtime_v2/execution_gateway/adapters/factory.py`
- Modify: `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/adapter.py` (rimuove `self._hedge_mode` globale)

- [ ] **Step 1: Scrivi il test per factory con nuovi env vars**

In `tests/runtime_v2/execution_gateway/test_adapter_factory.py` aggiungi:

```python
def test_build_ccxt_bybit_reads_api_key_from_env(monkeypatch):
    from src.runtime_v2.execution_gateway.adapters.factory import build_adapter
    from src.runtime_v2.execution_gateway.models import AdapterConfig
    monkeypatch.setenv("MY_API_KEY_ENV", "key123")
    monkeypatch.setenv("MY_API_SECRET_ENV", "secret456")
    cfg = AdapterConfig.model_validate({
        "type": "ccxt_bybit",
        "mode": "paper",
        "connector": "bybit",
        "api_key_env": "MY_API_KEY_ENV",
        "api_secret_env": "MY_API_SECRET_ENV",
    })
    # patch CcxtBybitAdapter to capture constructor args
    captured = {}
    class FakeAdapter:
        def __init__(self, api_key, api_secret, **kw):
            captured["api_key"] = api_key
            captured["api_secret"] = api_secret
    import src.runtime_v2.execution_gateway.adapters.factory as fmod
    import src.runtime_v2.execution_gateway.adapters.ccxt_bybit.adapter as amod
    monkeypatch.setattr(amod, "CcxtBybitAdapter", FakeAdapter)
    build_adapter("demo", cfg)
    assert captured["api_key"] == "key123"
    assert captured["api_secret"] == "secret456"


def test_build_ccxt_bybit_no_env_gives_empty_string(monkeypatch):
    from src.runtime_v2.execution_gateway.adapters.factory import build_adapter
    from src.runtime_v2.execution_gateway.models import AdapterConfig
    cfg = AdapterConfig.model_validate({
        "type": "ccxt_bybit", "mode": "paper", "connector": "bybit",
    })
    captured = {}
    class FakeAdapter:
        def __init__(self, api_key, api_secret, **kw):
            captured["api_key"] = api_key
            captured["api_secret"] = api_secret
    import src.runtime_v2.execution_gateway.adapters.ccxt_bybit.adapter as amod
    monkeypatch.setattr(amod, "CcxtBybitAdapter", FakeAdapter)
    build_adapter("demo", cfg)
    assert captured["api_key"] == ""
    assert captured["api_secret"] == ""
```

- [ ] **Step 2: Esegui — deve fallire**

```
pytest tests/runtime_v2/execution_gateway/test_adapter_factory.py::test_build_ccxt_bybit_reads_api_key_from_env -v
```

Atteso: FAIL

- [ ] **Step 3: Aggiorna `src/runtime_v2/execution_gateway/adapters/factory.py`**

```python
from __future__ import annotations

import logging
import os

from src.runtime_v2.execution_gateway.adapters.base import ExecutionAdapter
from src.runtime_v2.execution_gateway.models import AdapterConfig

logger = logging.getLogger(__name__)


def build_adapter(adapter_name: str, cfg: AdapterConfig) -> ExecutionAdapter:
    logger.debug("build_adapter: type=%s name=%s", cfg.type, adapter_name)
    if cfg.type == "ccxt_bybit":
        from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.adapter import CcxtBybitAdapter
        api_key = os.environ.get(cfg.api_key_env or "") if cfg.api_key_env else ""
        api_secret = os.environ.get(cfg.api_secret_env or "") if cfg.api_secret_env else ""
        return CcxtBybitAdapter(
            api_key=api_key,
            api_secret=api_secret,
            connector=cfg.connector,
            mode=cfg.mode,
        )
    raise ValueError(f"Unknown adapter type '{cfg.type}' for adapter '{adapter_name}'")


__all__ = ["build_adapter"]
```

- [ ] **Step 4: Aggiorna `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/adapter.py`**

Rimuovi `self._hedge_mode` dal costruttore e da tutti gli usi. Il costruttore diventa:

```python
def __init__(
    self,
    api_key: str,
    api_secret: str,
    connector: str,
    mode: str = "live",
    repo: GatewayCommandRepository | None = None,
    _exchange=None,
) -> None:
    if _exchange is not None:
        self._exchange = _exchange
    else:
        self._exchange = ccxt.bybit({
            "apiKey": api_key,
            "secret": api_secret,
            "options": {"defaultType": "linear"},
        })
        if mode == "demo":
            self._exchange.enable_demo_trading(True)
    self._connector = connector
    self._repo = repo
    self._builder = BybitOrderBuilder()
```

Nel metodo `place_order`, il `hedge_mode` ora viene letto dal payload:

```python
def place_order(self, *, command_type, payload, client_order_id, execution_account_id, connector):
    if command_type == "MOVE_STOP_TO_BREAKEVEN" and "entry_price" in payload and "target_price" not in payload:
        payload = {**payload, "target_price": payload["entry_price"]}

    hedge_mode = bool(payload.get("hedge_mode", False))

    params = self._builder.build(
        command_type,
        payload,
        client_order_id,
        hedge_mode=hedge_mode,
    )
    # ... resto invariato
```

Nel metodo `set_leverage`, `positionIdx` ora viene dal payload:

```python
def set_leverage(self, symbol: str, leverage: int, execution_account_id: str,
                 *, position_idx: int = 0) -> None:
    extra = {
        "buyLeverage": str(leverage),
        "sellLeverage": str(leverage),
    }
    if position_idx != 0:
        extra["positionIdx"] = position_idx
    try:
        self._exchange.set_leverage(leverage, symbol, params=extra)
    except Exception as e:
        if "110043" in str(e):
            return
        raise
```

- [ ] **Step 5: Esegui i test**

```
pytest tests/runtime_v2/execution_gateway/test_adapter_factory.py -v
pytest tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py -v --tb=short
```

Atteso: verde (i test unit che passano `_exchange` mock non sono impattati)

- [ ] **Step 6: Commit**

```
git add src/runtime_v2/execution_gateway/adapters/factory.py \
        src/runtime_v2/execution_gateway/adapters/ccxt_bybit/adapter.py \
        tests/runtime_v2/execution_gateway/test_adapter_factory.py
git commit -m "feat(execution): factory uses api_key_env/api_secret_env, remove global hedge_mode from adapter"
```

---

## Task 3: Aggiorna `config/execution.yaml` al formato minimale

**Files:**
- Modify: `config/execution.yaml`
- Modify: `tests/runtime_v2/execution_gateway/test_config_loader.py` (ripristina i due test che leggono il file reale)

- [ ] **Step 1: Sostituisci `config/execution.yaml`**

```yaml
# config/execution.yaml
# Env vars richieste:
#   BYBIT_API_KEY_BYBIT_DEMO=<key>
#   BYBIT_API_SECRET_BYBIT_DEMO=<secret>
#   TSB_ALLOW_LIVE_TRADING=YES_I_UNDERSTAND  (solo per mode: live)

execution:
  default_adapter: bybit_demo

  account_routing:
    default:
      adapter: bybit_demo
      execution_account_id: main

  adapters:
    bybit_demo:
      type: ccxt_bybit
      mode: demo
      connector: bybit

      api_key_env: BYBIT_API_KEY_BYBIT_DEMO
      api_secret_env: BYBIT_API_SECRET_BYBIT_DEMO

      strategy:
        default_mode: D_POSITION_TPSL
        simple_attached_enabled: true
        trigger_by: MarkPrice
        one_tp_mode: FULL
        multi_tp_mode: PARTIAL

      websocket:
        enabled: false
        poll_fallback_enabled: true
        poll_fallback_period_seconds: 60

      retry:
        max_attempts: 3
        backoff_seconds: [30, 90, 300]

      live_safety:
        allow_live_trading: false
```

- [ ] **Step 2: Ripristina i test su file reale in `test_config_loader.py`**

Aggiungi in fondo al file:

```python
def test_real_execution_yaml_loads():
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    config = ExecutionConfigLoader("config/execution.yaml").load()
    assert config.default_adapter == "bybit_demo"
    assert config.adapters["bybit_demo"].strategy.default_mode == "D_POSITION_TPSL"
    assert config.adapters["bybit_demo"].strategy.simple_attached_enabled is True
    assert config.adapters["bybit_demo"].live_safety.allow_live_trading is False


def test_real_execution_yaml_no_deprecated_fields():
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    config = ExecutionConfigLoader("config/execution.yaml").load()
    demo = config.adapters["bybit_demo"]
    # questi attributi non devono esistere sul nuovo AdapterConfig
    assert not hasattr(demo, "leverage")
    assert not hasattr(demo, "hedge_mode")
    assert not hasattr(demo, "capabilities")
    assert not hasattr(demo, "entry_execution")
```

- [ ] **Step 3: Esegui i test**

```
pytest tests/runtime_v2/execution_gateway/test_config_loader.py -v
```

Atteso: tutti passano

- [ ] **Step 4: Commit**

```
git add config/execution.yaml \
        tests/runtime_v2/execution_gateway/test_config_loader.py
git commit -m "feat(config): execution.yaml minimale senza campi legacy"
```

---

## Task 4: Aggiungi `account: AccountConfig` a `EffectiveEnrichmentConfig`

**Files:**
- Modify: `src/runtime_v2/signal_enrichment/models.py`
- Modify: `src/runtime_v2/signal_enrichment/config_loader.py`

`AccountConfig` esiste già in `models.py` ma non è in `EffectiveEnrichmentConfig`. Va aggiunta.

- [ ] **Step 1: Scrivi il test**

In `tests/runtime_v2/lifecycle/test_risk_capacity.py` aggiungi in coda (o crea `test_risk_leverage_validation.py`):

```python
# tests/runtime_v2/lifecycle/test_risk_leverage_validation.py
from __future__ import annotations

import pytest

from src.runtime_v2.signal_enrichment.models import AccountConfig, EffectiveEnrichmentConfig


def test_effective_enrichment_config_has_account_field():
    cfg = EffectiveEnrichmentConfig(
        trader_id="t1",
        enabled=True,
        gate_mode="block",
        hedge_mode=False,
        account_id="main",
        signal_policy=_make_signal_policy(),
        update_admission={},
        management_plan=_make_mgmt(),
        risk=_make_risk(),
        account=AccountConfig(
            id="main",
            capital_base_usdt=1000.0,
            max_leverage=5,
            max_capital_at_risk_pct=10.0,
            hard_max_per_signal_risk_pct=2.0,
        ),
    )
    assert cfg.account.max_leverage == 5
```

(Aggiungi le helper `_make_signal_policy`, `_make_mgmt`, `_make_risk` copiandole dal file `test_risk_capacity.py` già esistente)

- [ ] **Step 2: Esegui — deve fallire**

```
pytest tests/runtime_v2/lifecycle/test_risk_leverage_validation.py -v
```

Atteso: FAIL (`EffectiveEnrichmentConfig` non ha `account`)

- [ ] **Step 3: Modifica `src/runtime_v2/signal_enrichment/models.py`**

Aggiungi il campo `account` a `EffectiveEnrichmentConfig` (riga dopo `risk`):

```python
class EffectiveEnrichmentConfig(BaseModel):
    trader_id: str
    enabled: bool
    gate_mode: Literal["block", "warn"]
    hedge_mode: bool
    account_id: str
    signal_policy: SignalPolicyConfig
    update_admission: dict[str, bool]
    management_plan: ManagementPlanConfig
    risk: RiskConfig
    account: AccountConfig | None = None   # ← aggiunto
```

- [ ] **Step 4: Modifica `src/runtime_v2/signal_enrichment/config_loader.py`**

In `_merge`, prima del `return`, aggiungi:

```python
account_raw = global_raw.get("account", {})
account = None
if account_raw:
    try:
        account = AccountConfig(
            id=account_raw.get("id", "main"),
            capital_base_usdt=float(account_raw.get("capital_base_usdt", 1000.0)),
            max_leverage=int(account_raw.get("max_leverage", 10)),
            max_capital_at_risk_pct=float(account_raw.get("max_capital_at_risk_pct", 10.0)),
            hard_max_per_signal_risk_pct=float(account_raw.get("hard_max_per_signal_risk_pct", 2.0)),
        )
    except Exception:
        pass

return EffectiveEnrichmentConfig(
    ...   # tutti i campi invariati
    account=account,
)
```

- [ ] **Step 5: Esegui i test**

```
pytest tests/runtime_v2/lifecycle/test_risk_leverage_validation.py \
       tests/runtime_v2/lifecycle/test_risk_capacity.py -v
```

Atteso: verde

- [ ] **Step 6: Commit**

```
git add src/runtime_v2/signal_enrichment/models.py \
        src/runtime_v2/signal_enrichment/config_loader.py \
        tests/runtime_v2/lifecycle/test_risk_leverage_validation.py
git commit -m "feat(enrichment): aggiungi account: AccountConfig a EffectiveEnrichmentConfig"
```

---

## Task 5: Validazione `max_leverage` in `RiskCapacityEngine`

**Files:**
- Modify: `src/runtime_v2/lifecycle/risk_capacity.py`
- Modify: `tests/runtime_v2/lifecycle/test_risk_leverage_validation.py`

- [ ] **Step 1: Aggiungi test**

In `tests/runtime_v2/lifecycle/test_risk_leverage_validation.py` aggiungi:

```python
from src.runtime_v2.lifecycle.ports import AccountStateSnapshot, SymbolMarketSnapshot
from src.runtime_v2.lifecycle.risk_capacity import RiskCapacityEngine
from src.runtime_v2.lifecycle.models import TradeChain


def _make_enriched(leverage: int = 5, max_leverage: int = 5, hedge_mode: bool = False):
    from src.runtime_v2.signal_enrichment.models import (
        EnrichedCanonicalMessage, EnrichedSignalPayload, EnrichedEntryLeg,
    )
    from src.parser_v2.contracts.entities import Price, StopLoss
    from src.parser_v2.contracts.enums import EntryType

    account = AccountConfig(
        id="main", capital_base_usdt=1000.0, max_leverage=max_leverage,
        max_capital_at_risk_pct=10.0, hard_max_per_signal_risk_pct=2.0,
    )
    risk = RiskConfig(leverage=leverage, capital_base_usdt=1000.0, risk_pct_of_capital=1.0)

    signal = EnrichedSignalPayload(
        symbol="BTC/USDT:USDT",
        side="LONG",
        entry_structure="ONE_SHOT",
        entries=[EnrichedEntryLeg(sequence=1, entry_type=EntryType.LIMIT,
                                  price=Price(raw="65000", value=65000.0), weight=1.0)],
        take_profits=[],
        stop_loss=StopLoss(price=Price(raw="63000", value=63000.0)),
    )
    cfg = EffectiveEnrichmentConfig(
        trader_id="t1", enabled=True, gate_mode="block", hedge_mode=hedge_mode,
        account_id="main", signal_policy=_make_signal_policy(), update_admission={},
        management_plan=_make_mgmt(), risk=risk, account=account,
    )
    return EnrichedCanonicalMessage(
        enrichment_id=1, canonical_message_id=10, raw_message_id=5,
        trader_id="t1", account_id="main", primary_class="SIGNAL",
        enrichment_decision="PASS", enriched_signal=signal, enriched_actions=None,
        management_plan=None, policy_snapshot=cfg.model_dump(),
    )


def test_risk_leverage_within_max_passes():
    engine = RiskCapacityEngine()
    enriched = _make_enriched(leverage=5, max_leverage=5)
    result = engine.validate(enriched, [], None, _make_market_snapshot())
    assert result.passed is True
    assert result.risk_snapshot["leverage"] == 5


def test_risk_leverage_exceeds_max_blocked():
    engine = RiskCapacityEngine()
    enriched = _make_enriched(leverage=10, max_leverage=5)
    result = engine.validate(enriched, [], None, _make_market_snapshot())
    assert result.passed is False
    assert result.reason == "risk_leverage_exceeds_account_max_leverage"


def test_risk_snapshot_includes_hedge_mode():
    engine = RiskCapacityEngine()
    enriched = _make_enriched(leverage=3, max_leverage=5, hedge_mode=True)
    result = engine.validate(enriched, [], None, _make_market_snapshot())
    assert result.passed is True
    assert result.risk_snapshot["hedge_mode"] is True


def _make_market_snapshot():
    from datetime import datetime, timezone
    return SymbolMarketSnapshot(
        symbol="BTC/USDT:USDT", mark_price=65000.0, bid=64990.0, ask=65010.0,
        min_order_size=0.001, price_precision=0.5, qty_precision=0.001,
        source="test", captured_at=datetime.now(timezone.utc),
    )
```

- [ ] **Step 2: Esegui — devono fallire**

```
pytest tests/runtime_v2/lifecycle/test_risk_leverage_validation.py::test_risk_leverage_exceeds_max_blocked -v
```

Atteso: FAIL

- [ ] **Step 3: Aggiorna `src/runtime_v2/lifecycle/risk_capacity.py`**

Dopo la validazione `capital_base_mode` (riga ~88), aggiungi la validazione max_leverage:

```python
# ── max_leverage guard ────────────────────────────────────────────────
if config.account is not None:
    if risk.leverage > config.account.max_leverage:
        return RiskDecision(
            passed=False,
            reason="risk_leverage_exceeds_account_max_leverage",
        )
```

Aggiorna `risk_snapshot` per includere `hedge_mode`:

```python
risk_snapshot = {
    "capital": capital,
    "risk_amount": risk_amount,
    "entry_price": entry_price,
    "sl_price": sl_price,
    "risk_distance": risk_distance,
    "size_usdt": size_usdt,
    "leverage": leverage,
    "hedge_mode": config.hedge_mode,
    "capital_base_mode": risk.capital_base_mode,
}
```

- [ ] **Step 4: Esegui i test**

```
pytest tests/runtime_v2/lifecycle/test_risk_leverage_validation.py \
       tests/runtime_v2/lifecycle/test_risk_capacity.py -v
```

Atteso: verde

- [ ] **Step 5: Commit**

```
git add src/runtime_v2/lifecycle/risk_capacity.py \
        tests/runtime_v2/lifecycle/test_risk_leverage_validation.py
git commit -m "feat(risk): validazione max_leverage, aggiungi hedge_mode al risk_snapshot"
```

---

## Task 6: Nuovi `CommandType` in `lifecycle/models.py`

**Files:**
- Modify: `src/runtime_v2/lifecycle/models.py`

- [ ] **Step 1: Aggiungi test**

In `tests/runtime_v2/lifecycle/test_models.py` aggiungi:

```python
def test_new_command_types_valid():
    from src.runtime_v2.lifecycle.models import ExecutionCommand
    for ct in [
        "PLACE_ENTRY_WITH_ATTACHED_TPSL",
        "SET_POSITION_TPSL_FULL",
        "SET_POSITION_TPSL_PARTIAL",
        "MOVE_POSITION_STOP",
        "CANCEL_POSITION_TPSL",
    ]:
        cmd = ExecutionCommand(
            trade_chain_id=1,
            command_type=ct,
            idempotency_key=f"test:{ct}",
        )
        assert cmd.command_type == ct
```

- [ ] **Step 2: Esegui — deve fallire**

```
pytest tests/runtime_v2/lifecycle/test_models.py::test_new_command_types_valid -v
```

Atteso: FAIL (`ValidationError` per CommandType non valido)

- [ ] **Step 3: Aggiorna `CommandType` in `src/runtime_v2/lifecycle/models.py`**

```python
CommandType = Literal[
    "PLACE_ENTRY", "PLACE_ENTRY_WITH_ATTACHED_TPSL",
    "PLACE_PROTECTIVE_STOP", "PLACE_TAKE_PROFIT",
    "SET_POSITION_TPSL_FULL", "SET_POSITION_TPSL_PARTIAL",
    "MOVE_STOP_TO_BREAKEVEN", "MOVE_STOP", "MOVE_POSITION_STOP",
    "CANCEL_PENDING_ENTRY", "CANCEL_POSITION_TPSL",
    "CLOSE_PARTIAL", "CLOSE_FULL", "SYNC_PROTECTIVE_ORDERS",
]
```

- [ ] **Step 4: Esegui i test**

```
pytest tests/runtime_v2/lifecycle/test_models.py -v
```

Atteso: verde

- [ ] **Step 5: Commit**

```
git add src/runtime_v2/lifecycle/models.py \
        tests/runtime_v2/lifecycle/test_models.py
git commit -m "feat(lifecycle): aggiungi CommandType C/D (PLACE_ENTRY_WITH_ATTACHED_TPSL, SET_POSITION_TPSL_FULL, ecc.)"
```

---

## Task 7: Decision matrix C/D in `LifecycleEntryGate`

**Files:**
- Modify: `src/runtime_v2/lifecycle/entry_gate.py`
- Create: `tests/runtime_v2/lifecycle/test_entry_gate_cd.py`

- [ ] **Step 1: Scrivi i test**

Crea `tests/runtime_v2/lifecycle/test_entry_gate_cd.py`:

```python
from __future__ import annotations

import json
import pytest

from src.runtime_v2.lifecycle.entry_gate import LifecycleEntryGate
from src.runtime_v2.lifecycle.models import TradeChain
from src.runtime_v2.lifecycle.risk_capacity import RiskCapacityEngine
from src.runtime_v2.signal_enrichment.models import (
    AccountConfig, CloseDistributionConfig, EffectiveEnrichmentConfig,
    EnrichedCanonicalMessage, EnrichedEntryLeg, EnrichedSignalPayload,
    EntryRangeConfig, EntrySplitConfig, EntryWeightsConfig, LimitEntrySplitConfig,
    ManagementPlanConfig, MarketEntrySplitConfig, MarketExecutionConfig,
    PriceCorrectionsConfig, PriceSanityConfig, RiskConfig, SignalPolicyConfig,
    SlConfig, TpConfig,
)
from src.parser_v2.contracts.entities import Price, StopLoss, TakeProfit
from src.parser_v2.contracts.enums import EntryType
from src.runtime_v2.lifecycle.ports import AccountStateSnapshot, SymbolMarketSnapshot
from datetime import datetime, timezone


def _make_port():
    from src.runtime_v2.lifecycle.ports import ExchangeDataPort
    class FakePort(ExchangeDataPort):
        def get_account_state(self, account_id):
            return AccountStateSnapshot(
                account_id=account_id, equity_usdt=1000.0, available_balance_usdt=900.0,
                total_open_risk_usdt=0.0, total_margin_used_usdt=0.0,
                source="test", captured_at=datetime.now(timezone.utc),
            )
        def get_symbol_market_state(self, account_id, symbol):
            return SymbolMarketSnapshot(
                symbol=symbol, mark_price=65000.0, bid=64990.0, ask=65010.0,
                min_order_size=0.001, price_precision=0.5, qty_precision=0.001,
                source="test", captured_at=datetime.now(timezone.utc),
            )
    return FakePort()


def _make_enriched_signal(tp_count: int = 1, entry_count: int = 1,
                           leverage: int = 5, hedge_mode: bool = False):
    entries = [
        EnrichedEntryLeg(
            sequence=i + 1, entry_type=EntryType.LIMIT,
            price=Price(raw=str(65000 - i * 100), value=65000.0 - i * 100),
            weight=1.0 / entry_count,
        )
        for i in range(entry_count)
    ]
    take_profits = [
        TakeProfit(price=Price(raw=str(70000 + i * 500), value=70000.0 + i * 500), sequence=i + 1)
        for i in range(tp_count)
    ]
    signal = EnrichedSignalPayload(
        symbol="BTC/USDT:USDT", side="LONG", entry_structure="ONE_SHOT",
        entries=entries, take_profits=take_profits,
        stop_loss=StopLoss(price=Price(raw="63000", value=63000.0)),
    )
    risk = RiskConfig(leverage=leverage, capital_base_usdt=1000.0, risk_pct_of_capital=1.0)
    account = AccountConfig(id="main", capital_base_usdt=1000.0, max_leverage=10,
                            max_capital_at_risk_pct=10.0, hard_max_per_signal_risk_pct=2.0)
    signal_policy = SignalPolicyConfig(
        accepted_entry_structures=["ONE_SHOT", "TWO_STEP", "RANGE", "LADDER"],
        market_execution=MarketExecutionConfig(),
        entry_split=EntrySplitConfig(
            LIMIT=LimitEntrySplitConfig(
                single=EntryWeightsConfig(weights={"E1": 1.0}),
                range=EntryRangeConfig(weights={"E1": 0.5, "E2": 0.5}),
                averaging=EntryWeightsConfig(weights={"E1": 0.7, "E2": 0.3}),
                ladder=EntryWeightsConfig(weights={"E1": 0.5, "E2": 0.3, "E3": 0.2}),
            ),
            MARKET=MarketEntrySplitConfig(
                single=EntryWeightsConfig(weights={"E1": 1.0}),
                averaging=EntryWeightsConfig(weights={"E1": 0.7, "E2": 0.3}),
            ),
        ),
        tp=TpConfig(), sl=SlConfig(),
        price_corrections=PriceCorrectionsConfig(),
        price_sanity=PriceSanityConfig(),
    )
    cfg = EffectiveEnrichmentConfig(
        trader_id="t1", enabled=True, gate_mode="block", hedge_mode=hedge_mode,
        account_id="main", signal_policy=signal_policy, update_admission={},
        management_plan=ManagementPlanConfig(), risk=risk, account=account,
    )
    return EnrichedCanonicalMessage(
        enrichment_id=1, canonical_message_id=10, raw_message_id=5,
        trader_id="t1", account_id="main", primary_class="SIGNAL",
        enrichment_decision="PASS", enriched_signal=signal, enriched_actions=None,
        management_plan=ManagementPlanConfig(), policy_snapshot=cfg.model_dump(),
    )


def _make_gate(simple_attached_enabled: bool = True) -> LifecycleEntryGate:
    return LifecycleEntryGate(
        risk_engine=RiskCapacityEngine(),
        exchange_port=_make_port(),
        simple_attached_enabled=simple_attached_enabled,
    )


# ── C mode tests ───────────────────────────────────────────────────────────────

def test_c_mode_single_entry_single_tp():
    gate = _make_gate(simple_attached_enabled=True)
    enriched = _make_enriched_signal(tp_count=1, entry_count=1)
    result = gate.process_signal(enriched, [], "NONE")
    cmd_types = [c.command_type for c in result.execution_commands]
    assert "PLACE_ENTRY_WITH_ATTACHED_TPSL" in cmd_types
    assert "PLACE_ENTRY" not in cmd_types
    assert "SET_POSITION_TPSL_FULL" not in cmd_types


def test_c_mode_payload_has_leverage_and_position_idx():
    gate = _make_gate(simple_attached_enabled=True)
    enriched = _make_enriched_signal(tp_count=1, entry_count=1, leverage=5, hedge_mode=False)
    result = gate.process_signal(enriched, [], "NONE")
    attached_cmd = next(c for c in result.execution_commands
                        if c.command_type == "PLACE_ENTRY_WITH_ATTACHED_TPSL")
    payload = json.loads(attached_cmd.payload_json)
    assert payload["leverage"] == 5
    assert payload["hedge_mode"] is False
    assert payload["position_idx"] == 0
    assert "attached_tpsl" in payload
    assert payload["attached_tpsl"]["take_profit"] == 70000.0
    assert payload["attached_tpsl"]["stop_loss"] == 63000.0


def test_c_mode_disabled_uses_d_full():
    gate = _make_gate(simple_attached_enabled=False)
    enriched = _make_enriched_signal(tp_count=1, entry_count=1)
    result = gate.process_signal(enriched, [], "NONE")
    cmd_types = [c.command_type for c in result.execution_commands]
    assert "PLACE_ENTRY" in cmd_types
    assert "SET_POSITION_TPSL_FULL" in cmd_types
    assert "PLACE_ENTRY_WITH_ATTACHED_TPSL" not in cmd_types


# ── D Full tests ───────────────────────────────────────────────────────────────

def test_d_full_single_entry_single_tp():
    gate = _make_gate(simple_attached_enabled=False)
    enriched = _make_enriched_signal(tp_count=1, entry_count=1)
    result = gate.process_signal(enriched, [], "NONE")
    cmd_types = [c.command_type for c in result.execution_commands]
    assert "PLACE_ENTRY" in cmd_types
    assert "SET_POSITION_TPSL_FULL" in cmd_types
    tpsl_cmd = next(c for c in result.execution_commands
                    if c.command_type == "SET_POSITION_TPSL_FULL")
    assert tpsl_cmd.status == "WAITING_POSITION"


def test_d_full_payload_has_leverage():
    gate = _make_gate(simple_attached_enabled=False)
    enriched = _make_enriched_signal(tp_count=1, entry_count=1, leverage=5)
    result = gate.process_signal(enriched, [], "NONE")
    tpsl_cmd = next(c for c in result.execution_commands
                    if c.command_type == "SET_POSITION_TPSL_FULL")
    payload = json.loads(tpsl_cmd.payload_json)
    assert payload["leverage"] == 5
    assert payload["position_idx"] == 0


def test_d_multi_tp_generates_partial_commands():
    gate = _make_gate(simple_attached_enabled=False)
    enriched = _make_enriched_signal(tp_count=3, entry_count=1)
    result = gate.process_signal(enriched, [], "NONE")
    cmd_types = [c.command_type for c in result.execution_commands]
    partial_cmds = [c for c in result.execution_commands
                    if c.command_type == "SET_POSITION_TPSL_PARTIAL"]
    assert len(partial_cmds) == 3
    assert "SET_POSITION_TPSL_FULL" not in cmd_types
    for cmd in partial_cmds:
        assert cmd.status == "WAITING_POSITION"


def test_d_multi_tp_partial_tp_size_equals_sl_size():
    gate = _make_gate(simple_attached_enabled=False)
    enriched = _make_enriched_signal(tp_count=2, entry_count=1)
    result = gate.process_signal(enriched, [], "NONE")
    partial_cmds = sorted(
        [c for c in result.execution_commands if c.command_type == "SET_POSITION_TPSL_PARTIAL"],
        key=lambda c: json.loads(c.payload_json)["tp_sequence"],
    )
    for cmd in partial_cmds:
        payload = json.loads(cmd.payload_json)
        assert payload["tp_size"] == payload["sl_size"]


def test_d_multi_entry_forces_d_mode():
    gate = _make_gate(simple_attached_enabled=True)
    enriched = _make_enriched_signal(tp_count=1, entry_count=2)
    result = gate.process_signal(enriched, [], "NONE")
    cmd_types = [c.command_type for c in result.execution_commands]
    assert "PLACE_ENTRY_WITH_ATTACHED_TPSL" not in cmd_types
    assert "PLACE_ENTRY" in cmd_types


# ── Hedge mode tests ───────────────────────────────────────────────────────────

def test_hedge_long_position_idx_1():
    gate = _make_gate(simple_attached_enabled=False)
    enriched = _make_enriched_signal(tp_count=1, entry_count=1, hedge_mode=True)
    result = gate.process_signal(enriched, [], "NONE")
    tpsl_cmd = next(c for c in result.execution_commands
                    if c.command_type == "SET_POSITION_TPSL_FULL")
    payload = json.loads(tpsl_cmd.payload_json)
    assert payload["position_idx"] == 1
```

- [ ] **Step 2: Esegui — devono fallire**

```
pytest tests/runtime_v2/lifecycle/test_entry_gate_cd.py -v
```

Atteso: FAIL (vari errori su parametri e tipi di comandi non supportati)

- [ ] **Step 3: Aggiorna `src/runtime_v2/lifecycle/entry_gate.py`**

Modifica il costruttore di `LifecycleEntryGate`:

```python
class LifecycleEntryGate:
    def __init__(
        self,
        risk_engine: RiskCapacityEngine,
        exchange_port: ExchangeDataPort,
        execution_mode: str = "a_sequential",   # mantenuto per compat
        simple_attached_enabled: bool = True,   # ← nuovo
    ) -> None:
        self._risk = risk_engine
        self._port = exchange_port
        self._execution_mode = execution_mode
        self._simple_attached_enabled = simple_attached_enabled
```

Aggiungi helper statico `resolve_position_idx`:

```python
@staticmethod
def resolve_position_idx(side: str, hedge_mode: bool) -> int:
    if not hedge_mode:
        return 0
    return 1 if side == "LONG" else 2
```

Sostituisci `_build_entry_commands` con la versione C/D:

```python
def _build_entry_commands(
    self,
    enriched: EnrichedCanonicalMessage,
    decision,
) -> list[ExecutionCommand]:
    signal = enriched.enriched_signal
    management_plan = enriched.management_plan or ManagementPlanConfig()
    eid = enriched.enrichment_id

    tp_count = len(signal.take_profits)
    entry_count = len(signal.entries)
    size_usdt = float(decision.size_usdt or 0.0)
    fallback_entry_price = float(decision.risk_snapshot.get("entry_price") or 0.0)
    leverage = int(decision.risk_snapshot.get("leverage") or 1)
    hedge_mode = bool(decision.risk_snapshot.get("hedge_mode", False))
    position_idx = self.resolve_position_idx(signal.side, hedge_mode)
    close_pcts = self._get_close_pcts(management_plan, tp_count)

    sl_price = (
        signal.stop_loss.price.value
        if signal.stop_loss and signal.stop_loss.price else None
    )

    # ── Decision: C vs D ──────────────────────────────────────────────────────
    use_c = (
        self._simple_attached_enabled
        and entry_count == 1
        and tp_count == 1
        and sl_price is not None
    )

    if use_c:
        return self._build_c_commands(
            signal, eid, size_usdt, fallback_entry_price,
            leverage, hedge_mode, position_idx, sl_price,
        )
    return self._build_d_commands(
        signal, eid, size_usdt, fallback_entry_price,
        leverage, hedge_mode, position_idx, sl_price,
        tp_count, close_pcts,
    )

def _build_c_commands(
    self, signal, eid, size_usdt, fallback_entry_price,
    leverage, hedge_mode, position_idx, sl_price,
) -> list[ExecutionCommand]:
    leg = signal.entries[0]
    leg_price = leg.price.value if leg.price else fallback_entry_price
    leg_qty = self._qty_from_notional(size_usdt, leg_price)
    tp = signal.take_profits[0]
    tp_price = tp.price.value if tp.price else None

    payload = {
        "execution_strategy": "C_SIMPLE_ATTACHED",
        "symbol": signal.symbol,
        "side": signal.side,
        "entry_type": leg.entry_type,
        "price": leg_price if leg.entry_type == "LIMIT" else None,
        "qty": leg_qty,
        "leverage": leverage,
        "hedge_mode": hedge_mode,
        "position_idx": position_idx,
        "attached_tpsl": {
            "mode": "FULL",
            "take_profit": tp_price,
            "stop_loss": sl_price,
            "tp_trigger_by": "MarkPrice",
            "sl_trigger_by": "MarkPrice",
        },
    }
    return [ExecutionCommand(
        trade_chain_id=0,
        command_type="PLACE_ENTRY_WITH_ATTACHED_TPSL",
        status="PENDING",
        payload_json=json.dumps(payload),
        idempotency_key=f"place_entry_attached:{eid}",
    )]

def _build_d_commands(
    self, signal, eid, size_usdt, fallback_entry_price,
    leverage, hedge_mode, position_idx, sl_price,
    tp_count, close_pcts,
) -> list[ExecutionCommand]:
    commands: list[ExecutionCommand] = []

    # Entry legs
    for leg in signal.entries:
        leg_price = leg.price.value if leg.price else fallback_entry_price
        leg_notional = size_usdt * float(leg.weight or 0.0)
        leg_qty = self._qty_from_notional(leg_notional, leg_price)
        commands.append(ExecutionCommand(
            trade_chain_id=0,
            command_type="PLACE_ENTRY",
            status="PENDING",
            payload_json=json.dumps({
                "execution_strategy": "D_POSITION_TPSL",
                "symbol": signal.symbol,
                "side": signal.side,
                "entry_type": leg.entry_type,
                "price": leg_price if leg.entry_type == "LIMIT" else None,
                "qty": leg_qty,
                "leverage": leverage,
                "hedge_mode": hedge_mode,
                "position_idx": position_idx,
                "sequence": leg.sequence,
            }),
            idempotency_key=f"place_entry:{eid}:leg{leg.sequence}",
        ))

    if tp_count == 0 or sl_price is None:
        return commands

    total_qty = self._qty_from_notional(size_usdt, fallback_entry_price)

    if tp_count == 1:
        tp = signal.take_profits[0]
        tp_price = tp.price.value if tp.price else None
        commands.append(ExecutionCommand(
            trade_chain_id=0,
            command_type="SET_POSITION_TPSL_FULL",
            status="WAITING_POSITION",
            payload_json=json.dumps({
                "execution_strategy": "D_POSITION_TPSL",
                "symbol": signal.symbol,
                "side": signal.side,
                "leverage": leverage,
                "hedge_mode": hedge_mode,
                "position_idx": position_idx,
                "take_profit": tp_price,
                "stop_loss": sl_price,
                "tp_trigger_by": "MarkPrice",
                "sl_trigger_by": "MarkPrice",
            }),
            idempotency_key=f"set_tpsl_full:{eid}",
        ))
    else:
        # D Partial — one command per TP
        allocated_qty = 0.0
        for i, tp in enumerate(signal.take_profits):
            is_last = (i == len(signal.take_profits) - 1)
            tp_price = tp.price.value if tp.price else None
            close_pct = close_pcts[i] if i < len(close_pcts) else (100.0 / tp_count)
            if is_last:
                tp_qty = max(0.0, total_qty - allocated_qty)
            else:
                tp_qty = round(total_qty * close_pct / 100.0, 8)
                allocated_qty += tp_qty

            commands.append(ExecutionCommand(
                trade_chain_id=0,
                command_type="SET_POSITION_TPSL_PARTIAL",
                status="WAITING_POSITION",
                payload_json=json.dumps({
                    "execution_strategy": "D_POSITION_TPSL",
                    "symbol": signal.symbol,
                    "side": signal.side,
                    "position_idx": position_idx,
                    "tp_sequence": tp.sequence,
                    "take_profit": tp_price,
                    "stop_loss": sl_price,
                    "tp_size": tp_qty,
                    "sl_size": tp_qty,
                    "tp_order_type": "Limit",
                    "tp_limit_price": tp_price,
                    "tp_trigger_by": "MarkPrice",
                    "sl_trigger_by": "MarkPrice",
                }),
                idempotency_key=f"set_tpsl_partial:{eid}:tp{tp.sequence}",
            ))

    return commands
```

Aggiungi anche la logica REVIEW_REQUIRED per UPDATE su catena C con entry pending (in `_apply_action_to_chain`):

```python
def _apply_action_to_chain(self, enriched, chain, action, active_commands):
    # Se la chain è in C mode con entry non ancora fillata → REVIEW_REQUIRED
    chain_exec_mode = getattr(chain, "execution_mode", "")
    if chain_exec_mode == "C_SIMPLE_ATTACHED":
        entry_pending = any(
            c.command_type == "PLACE_ENTRY_WITH_ATTACHED_TPSL"
            and c.status in ("PENDING", "SENT", "ACK")
            for c in active_commands
        )
        if entry_pending:
            return self._review_chain(
                enriched, chain,
                "c_mode_update_blocked:entry_pending_not_filled"
            )
    # ... resto invariato
```

Aggiorna anche `process_signal` per settare `execution_mode` correttamente sulla chain creata:

```python
# Dopo aver costruito `commands`, determina execution_mode
use_c = (
    self._simple_attached_enabled
    and len(signal.entries) == 1
    and len(signal.take_profits) == 1
    and (signal.stop_loss and signal.stop_loss.price) is not None
)
chain.execution_mode = "C_SIMPLE_ATTACHED" if use_c else "D_POSITION_TPSL"
```

- [ ] **Step 4: Esegui i test**

```
pytest tests/runtime_v2/lifecycle/test_entry_gate_cd.py -v
```

Atteso: verde

- [ ] **Step 5: Verifica regressioni entry_gate esistenti**

```
pytest tests/runtime_v2/lifecycle/test_entry_gate.py -v
```

Atteso: verde

- [ ] **Step 6: Commit**

```
git add src/runtime_v2/lifecycle/entry_gate.py \
        tests/runtime_v2/lifecycle/test_entry_gate_cd.py
git commit -m "feat(lifecycle): decision matrix C/D in LifecycleEntryGate, leverage/position_idx nei payload"
```

---

## Task 8: C/D handlers in `BybitOrderBuilder`

**Files:**
- Modify: `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/order_builder.py`
- Create: `tests/runtime_v2/execution_gateway/test_bybit_order_builder_cd.py`

- [ ] **Step 1: Scrivi i test**

Crea `tests/runtime_v2/execution_gateway/test_bybit_order_builder_cd.py`:

```python
from __future__ import annotations

import pytest
from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.order_builder import (
    BybitOrderBuilder, BybitOrderParams,
)


def _b() -> BybitOrderBuilder:
    return BybitOrderBuilder()


# ── PLACE_ENTRY_WITH_ATTACHED_TPSL ────────────────────────────────────────────

def test_place_entry_with_attached_tpsl_limit():
    params = _b().build(
        "PLACE_ENTRY_WITH_ATTACHED_TPSL",
        {
            "symbol": "BTC/USDT:USDT",
            "side": "LONG",
            "entry_type": "LIMIT",
            "price": 65000.0,
            "qty": 0.01,
            "leverage": 5,
            "hedge_mode": False,
            "position_idx": 0,
            "attached_tpsl": {
                "mode": "FULL",
                "take_profit": 70000.0,
                "stop_loss": 63000.0,
                "tp_trigger_by": "MarkPrice",
                "sl_trigger_by": "MarkPrice",
            },
        },
        "tsb:1:1:entry:1",
    )
    assert params.action == "create_order"
    assert params.order_type == "limit"
    assert params.side == "buy"
    assert params.amount == 0.01
    assert params.price == 65000.0
    assert params.extra_params["takeProfit"] == 70000.0
    assert params.extra_params["stopLoss"] == 63000.0
    assert params.extra_params["tpslMode"] == "Full"
    assert params.extra_params["positionIdx"] == 0
    assert params.extra_params["tpTriggerBy"] == "MarkPrice"
    assert params.extra_params["slTriggerBy"] == "MarkPrice"


def test_place_entry_with_attached_tpsl_hedge_long():
    params = _b().build(
        "PLACE_ENTRY_WITH_ATTACHED_TPSL",
        {
            "symbol": "BTC/USDT:USDT",
            "side": "LONG",
            "entry_type": "LIMIT",
            "price": 65000.0,
            "qty": 0.01,
            "leverage": 5,
            "hedge_mode": True,
            "position_idx": 1,
            "attached_tpsl": {
                "mode": "FULL",
                "take_profit": 70000.0,
                "stop_loss": 63000.0,
                "tp_trigger_by": "MarkPrice",
                "sl_trigger_by": "MarkPrice",
            },
        },
        "tsb:1:1:entry:1",
    )
    assert params.extra_params["positionIdx"] == 1


# ── SET_POSITION_TPSL_FULL ────────────────────────────────────────────────────

def test_set_position_tpsl_full():
    params = _b().build(
        "SET_POSITION_TPSL_FULL",
        {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "position_idx": 0,
            "take_profit": 70000.0,
            "stop_loss": 63000.0,
            "tp_trigger_by": "MarkPrice",
            "sl_trigger_by": "MarkPrice",
        },
        "tsb:1:1:tpsl_full:1",
    )
    assert params.action == "trading_stop_full"
    assert params.symbol == "BTCUSDT"
    assert params.extra_params["positionIdx"] == 0
    assert params.extra_params["tpslMode"] == "Full"
    assert params.extra_params["takeProfit"] == "70000.0"
    assert params.extra_params["stopLoss"] == "63000.0"
    assert params.extra_params["tpTriggerBy"] == "MarkPrice"
    assert params.extra_params["tpOrderType"] == "Market"
    assert params.extra_params["slOrderType"] == "Market"


# ── SET_POSITION_TPSL_PARTIAL ─────────────────────────────────────────────────

def test_set_position_tpsl_partial():
    params = _b().build(
        "SET_POSITION_TPSL_PARTIAL",
        {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "position_idx": 0,
            "tp_sequence": 1,
            "take_profit": 67000.0,
            "stop_loss": 63000.0,
            "tp_size": 0.01,
            "sl_size": 0.01,
            "tp_order_type": "Limit",
            "tp_limit_price": 67000.0,
            "tp_trigger_by": "MarkPrice",
            "sl_trigger_by": "MarkPrice",
        },
        "tsb:1:1:tpsl_partial:1",
    )
    assert params.action == "trading_stop_partial"
    assert params.extra_params["tpslMode"] == "Partial"
    assert params.extra_params["tpSize"] == "0.01"
    assert params.extra_params["slSize"] == "0.01"
    assert params.extra_params["tpOrderType"] == "Limit"
    assert params.extra_params["tpLimitPrice"] == "67000.0"


# ── MOVE_POSITION_STOP ────────────────────────────────────────────────────────

def test_move_position_stop():
    params = _b().build(
        "MOVE_POSITION_STOP",
        {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "position_idx": 0,
            "new_stop_loss": 65000.0,
        },
        "tsb:1:1:move_stop:1",
    )
    assert params.action == "trading_stop_move_sl"
    assert params.symbol == "BTCUSDT"
    assert params.extra_params["stopLoss"] == "65000.0"
    assert params.extra_params["positionIdx"] == 0
    # takeProfit non deve essere presente (non vogliamo modificare i TP)
    assert "takeProfit" not in params.extra_params
```

- [ ] **Step 2: Esegui — devono fallire**

```
pytest tests/runtime_v2/execution_gateway/test_bybit_order_builder_cd.py -v
```

Atteso: FAIL

- [ ] **Step 3: Aggiorna `BybitOrderParams` e `BybitOrderBuilder`**

In `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/order_builder.py`, aggiungi i nuovi handler nel `_dispatch`:

```python
def _dispatch(self, command_type, payload, client_order_id):
    # ... handlers esistenti invariati ...
    if command_type == "PLACE_ENTRY_WITH_ATTACHED_TPSL":
        return self._place_entry_with_attached_tpsl(payload, client_order_id)
    if command_type == "SET_POSITION_TPSL_FULL":
        return self._set_position_tpsl_full(payload)
    if command_type == "SET_POSITION_TPSL_PARTIAL":
        return self._set_position_tpsl_partial(payload)
    if command_type == "MOVE_POSITION_STOP":
        return self._move_position_stop(payload)
    raise ValueError(f"Unknown command_type: {command_type!r}")
```

Aggiungi i metodi:

```python
def _place_entry_with_attached_tpsl(self, payload: dict, client_order_id: str) -> BybitOrderParams:
    entry_type = payload["entry_type"]
    price = float(payload["price"]) if entry_type == "LIMIT" and payload.get("price") else None
    tpsl = payload["attached_tpsl"]
    position_idx = int(payload.get("position_idx", 0))
    extra = {
        "positionIdx": position_idx,
        "takeProfit": float(tpsl["take_profit"]),
        "stopLoss": float(tpsl["stop_loss"]),
        "tpslMode": "Full",
        "tpOrderType": "Market",
        "slOrderType": "Market",
        "tpTriggerBy": tpsl.get("tp_trigger_by", "MarkPrice"),
        "slTriggerBy": tpsl.get("sl_trigger_by", "MarkPrice"),
    }
    return BybitOrderParams(
        action="create_order",
        symbol=payload["symbol"],
        order_type=entry_type.lower(),
        side=_ENTRY_SIDE[payload["side"]],
        amount=float(payload["qty"]),
        price=price,
        order_link_id=client_order_id,
        extra_params=extra,
    )

def _set_position_tpsl_full(self, payload: dict) -> BybitOrderParams:
    return BybitOrderParams(
        action="trading_stop_full",
        symbol=payload["symbol"],
        position_side=payload["side"],
        extra_params={
            "positionIdx": int(payload.get("position_idx", 0)),
            "tpslMode": "Full",
            "takeProfit": str(float(payload["take_profit"])),
            "stopLoss": str(float(payload["stop_loss"])),
            "tpTriggerBy": payload.get("tp_trigger_by", "MarkPrice"),
            "slTriggerBy": payload.get("sl_trigger_by", "MarkPrice"),
            "tpOrderType": "Market",
            "slOrderType": "Market",
        },
    )

def _set_position_tpsl_partial(self, payload: dict) -> BybitOrderParams:
    tp_order_type = payload.get("tp_order_type", "Limit")
    extra: dict = {
        "positionIdx": int(payload.get("position_idx", 0)),
        "tpslMode": "Partial",
        "takeProfit": str(float(payload["take_profit"])),
        "stopLoss": str(float(payload["stop_loss"])),
        "tpSize": str(float(payload["tp_size"])),
        "slSize": str(float(payload["sl_size"])),
        "tpOrderType": tp_order_type,
        "slOrderType": payload.get("sl_order_type", "Market"),
        "tpTriggerBy": payload.get("tp_trigger_by", "MarkPrice"),
        "slTriggerBy": payload.get("sl_trigger_by", "MarkPrice"),
    }
    if tp_order_type == "Limit" and payload.get("tp_limit_price"):
        extra["tpLimitPrice"] = str(float(payload["tp_limit_price"]))
    return BybitOrderParams(
        action="trading_stop_partial",
        symbol=payload["symbol"],
        position_side=payload["side"],
        extra_params=extra,
    )

def _move_position_stop(self, payload: dict) -> BybitOrderParams:
    return BybitOrderParams(
        action="trading_stop_move_sl",
        symbol=payload["symbol"],
        position_side=payload["side"],
        extra_params={
            "positionIdx": int(payload.get("position_idx", 0)),
            "stopLoss": str(float(payload["new_stop_loss"])),
        },
    )
```

- [ ] **Step 4: Esegui i test**

```
pytest tests/runtime_v2/execution_gateway/test_bybit_order_builder_cd.py \
       tests/runtime_v2/execution_gateway/test_bybit_order_builder.py -v
```

Atteso: verde

- [ ] **Step 5: Commit**

```
git add src/runtime_v2/execution_gateway/adapters/ccxt_bybit/order_builder.py \
        tests/runtime_v2/execution_gateway/test_bybit_order_builder_cd.py
git commit -m "feat(adapter): aggiungi C/D command handlers a BybitOrderBuilder"
```

---

## Task 9: Handlers C/D in `CcxtBybitAdapter`

**Files:**
- Modify: `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/adapter.py`

- [ ] **Step 1: Aggiungi test per i nuovi action handler**

In `tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py` aggiungi:

```python
def test_place_entry_with_attached_tpsl_calls_create_order():
    exchange = MagicMock()
    exchange.create_order.return_value = {"id": "ord123"}
    adapter = CcxtBybitAdapter(api_key="", api_secret="", connector="bybit", _exchange=exchange)
    result = adapter.place_order(
        command_type="PLACE_ENTRY_WITH_ATTACHED_TPSL",
        payload={
            "symbol": "BTC/USDT:USDT", "side": "LONG", "entry_type": "LIMIT",
            "price": 65000.0, "qty": 0.01, "leverage": 5,
            "hedge_mode": False, "position_idx": 0,
            "attached_tpsl": {
                "mode": "FULL", "take_profit": 70000.0, "stop_loss": 63000.0,
                "tp_trigger_by": "MarkPrice", "sl_trigger_by": "MarkPrice",
            },
        },
        client_order_id="tsb:1:1:entry:1",
        execution_account_id="main",
        connector="bybit",
    )
    assert result.success is True
    exchange.create_order.assert_called_once()


def test_set_position_tpsl_full_calls_trading_stop():
    exchange = MagicMock()
    exchange.private_post_v5_position_trading_stop.return_value = {"retCode": 0}
    adapter = CcxtBybitAdapter(api_key="", api_secret="", connector="bybit", _exchange=exchange)
    result = adapter.place_order(
        command_type="SET_POSITION_TPSL_FULL",
        payload={
            "symbol": "BTCUSDT", "side": "LONG", "position_idx": 0,
            "take_profit": 70000.0, "stop_loss": 63000.0,
            "tp_trigger_by": "MarkPrice", "sl_trigger_by": "MarkPrice",
        },
        client_order_id="tsb:1:1:tpsl_full:1",
        execution_account_id="main",
        connector="bybit",
    )
    assert result.success is True
    exchange.private_post_v5_position_trading_stop.assert_called_once()
    call_args = exchange.private_post_v5_position_trading_stop.call_args[0][0]
    assert call_args["tpslMode"] == "Full"
    assert call_args["positionIdx"] == 0


def test_move_position_stop_calls_trading_stop_only_sl():
    exchange = MagicMock()
    exchange.private_post_v5_position_trading_stop.return_value = {"retCode": 0}
    adapter = CcxtBybitAdapter(api_key="", api_secret="", connector="bybit", _exchange=exchange)
    adapter.place_order(
        command_type="MOVE_POSITION_STOP",
        payload={
            "symbol": "BTCUSDT", "side": "LONG", "position_idx": 0,
            "new_stop_loss": 65000.0,
        },
        client_order_id="tsb:1:1:move_stop:1",
        execution_account_id="main",
        connector="bybit",
    )
    call_args = exchange.private_post_v5_position_trading_stop.call_args[0][0]
    assert "stopLoss" in call_args
    assert "takeProfit" not in call_args
```

- [ ] **Step 2: Esegui — devono fallire**

```
pytest tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py::test_set_position_tpsl_full_calls_trading_stop -v
```

Atteso: FAIL

- [ ] **Step 3: Aggiorna il metodo `place_order` in `adapter.py`**

Aggiungi nel blocco `try` di `place_order` i nuovi action handler, dopo il controllo `params.action`:

```python
if params.action == "trading_stop_full":
    bybit_symbol = payload.get("symbol", params.symbol)
    resp = self._exchange.private_post_v5_position_trading_stop({
        "category": "linear",
        "symbol": bybit_symbol,
        **params.extra_params,
    })
    return AdapterResult(success=True)

if params.action == "trading_stop_partial":
    bybit_symbol = payload.get("symbol", params.symbol)
    resp = self._exchange.private_post_v5_position_trading_stop({
        "category": "linear",
        "symbol": bybit_symbol,
        **params.extra_params,
    })
    return AdapterResult(success=True)

if params.action == "trading_stop_move_sl":
    bybit_symbol = payload.get("symbol", params.symbol)
    self._exchange.private_post_v5_position_trading_stop({
        "category": "linear",
        "symbol": bybit_symbol,
        **params.extra_params,
    })
    return AdapterResult(success=True)
```

Nota: il `set_leverage` ora usa `position_idx` passato come parametro keyword:

```python
# In place_order, prima di ogni create_order o trading_stop,
# se il payload ha leverage e symbol:
# (opzionale in questa fase — set_leverage è già chiamato dal command worker)
```

- [ ] **Step 4: Esegui la suite completa**

```
pytest tests/runtime_v2/execution_gateway/ tests/runtime_v2/lifecycle/ -v --tb=short
```

Atteso: verde con al più gli skip `bybit_testnet`

- [ ] **Step 5: Commit**

```
git add src/runtime_v2/execution_gateway/adapters/ccxt_bybit/adapter.py \
        tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py
git commit -m "feat(adapter): aggiungi handler trading_stop_full/partial/move_sl in CcxtBybitAdapter"
```

---

## Task 10: Verifica finale e suite completa

- [ ] **Step 1: Esegui la suite completa runtime_v2**

```
pytest tests/runtime_v2/ -v --tb=short -q
```

Atteso: verde (273+ passed, skip solo per `bybit_testnet`)

- [ ] **Step 2: Verifica che `config/execution.yaml` si carichi senza errori**

```python
# in un terminale Python o test ad-hoc
from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
c = ExecutionConfigLoader("config/execution.yaml").load()
print(c.adapters["bybit_demo"].strategy.default_mode)  # D_POSITION_TPSL
```

- [ ] **Step 3: Commit finale**

```
git add -u
git commit -m "feat(execution): C/D migration complete — execution.yaml minimal, C_SIMPLE_ATTACHED + D_POSITION_TPSL live"
```

---

## Copertura spec → task

| Requisito PRD | Task |
|---|---|
| `execution.yaml` senza `leverage`/`hedge_mode` | Task 1, 3 |
| `operation_config.yaml` fonte unica di leverage | Task 4, 5 |
| `risk.leverage <= account.max_leverage` → BLOCK | Task 5 |
| `ExecutionStrategyConfig` in `AdapterConfig` | Task 1 |
| `api_key_env`/`api_secret_env` | Task 1, 2 |
| Factory legge credenziali da env | Task 2 |
| `position_idx` calcolato da `side + hedge_mode` | Task 7 |
| `leverage` e `hedge_mode` nei payload comandi | Task 7 |
| C mode: 1 entry + 1 TP + 1 SL → `PLACE_ENTRY_WITH_ATTACHED_TPSL` | Task 7, 8, 9 |
| D Full: `SET_POSITION_TPSL_FULL` in `WAITING_POSITION` | Task 6, 7, 8, 9 |
| D Partial: `SET_POSITION_TPSL_PARTIAL` per ogni TP | Task 7, 8, 9 |
| `tp_size == sl_size` in Partial | Task 7, 8 |
| `MOVE_POSITION_STOP` sovrascrive solo `stopLoss` | Task 8, 9 |
| UPDATE C pre-fill → `REVIEW_REQUIRED` | Task 7 |
| `execution_mode` nel `TradeChain` aggiornato | Task 7 |
| LONG hedge → `positionIdx=1`, SHORT → 2 | Task 7, 8 |
