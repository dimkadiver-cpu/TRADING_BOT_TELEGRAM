# Runtime V2 — Fase 2: CcxtBybitAdapter Production-Ready

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rendere `CcxtBybitAdapter` production-ready con WebSocket fill real-time, SYNC_PROTECTIVE_ORDERS reale, fallback OD-F1-2 per Mode C, e supporto hedge mode Bybit.

**Architecture:** Un thread daemon con asyncio loop esegue `ccxt.pro watch_orders()` come path primario per i fill; `ExchangeEventSyncWorker` diventa fallback configurabile + startup reconciliation. `BybitOrderBuilder` acquisisce `hedge_mode` per aggiungere `positionIdx`. `SYNC_PROTECTIVE_ORDERS` interroga `fetch_positions` per amendare la qty dello SL al residuo reale.

**Tech Stack:** Python 3.12, ccxt>=4.4.0 (include ccxt.pro), Pydantic v2, pytest, sqlite3.

---

## File map

| File | Operazione |
|---|---|
| `src/runtime_v2/execution_gateway/models.py` | +`WebsocketConfig`, +`hedge_mode`, +`websocket` in `AdapterConfig` |
| `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/order_builder.py` | +`hedge_mode` param in `build()`, +`amend_sl_qty` action |
| `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/adapter.py` | +hedge_mode, +`_handle_amend_sl_qty`, +OD-F1-2 fallback in `get_order_status` |
| `src/runtime_v2/execution_gateway/repositories.py` | +`get_payload_by_client_order_id`, +`get_active_client_order_ids` |
| `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/ws_fill_watcher.py` | NUOVO: `BybitWsFillWatcher` |
| `src/runtime_v2/execution_gateway/event_sync.py` | +`run_reconciliation()`, fallback poll configurabile |
| `src/runtime_v2/execution_gateway/adapters/factory.py` | passa `hedge_mode` e `repo` a `CcxtBybitAdapter` |
| `requirements.txt` | `ccxt>=4.0` → `ccxt>=4.4.0` |
| `tests/runtime_v2/execution_gateway/test_adapter_config_ccxt.py` | +hedge_mode, +websocket config tests |
| `tests/runtime_v2/execution_gateway/test_bybit_order_builder.py` | +hedge_mode tests per tutti i comandi |
| `tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py` | +SYNC reale, +OD-F1-2, +hedge_mode set_leverage |
| `tests/runtime_v2/execution_gateway/test_bybit_ws_fill_watcher.py` | NUOVO |
| `tests/runtime_v2/execution_gateway/test_event_sync.py` | +reconciliation tests |

---

## Task 1: Estendi AdapterConfig — hedge_mode + WebsocketConfig

**Files:**
- Modify: `src/runtime_v2/execution_gateway/models.py`
- Modify: `tests/runtime_v2/execution_gateway/test_adapter_config_ccxt.py`

- [ ] **Step 1: Scrivi i test che falliranno**

Aggiungi in fondo a `tests/runtime_v2/execution_gateway/test_adapter_config_ccxt.py`:

```python
def test_adapter_config_hedge_mode_defaults_false():
    cfg = AdapterConfig.model_validate({
        "type": "ccxt_bybit", "mode": "paper", "connector": "bybit",
    })
    assert cfg.hedge_mode is False


def test_adapter_config_hedge_mode_true():
    cfg = AdapterConfig.model_validate({
        "type": "ccxt_bybit", "mode": "paper", "connector": "bybit",
        "hedge_mode": True,
    })
    assert cfg.hedge_mode is True


def test_adapter_config_websocket_defaults():
    cfg = AdapterConfig.model_validate({
        "type": "ccxt_bybit", "mode": "paper", "connector": "bybit",
    })
    assert cfg.websocket.enabled is False
    assert cfg.websocket.poll_fallback_enabled is True
    assert cfg.websocket.poll_fallback_period_seconds == 60


def test_adapter_config_websocket_custom():
    cfg = AdapterConfig.model_validate({
        "type": "ccxt_bybit", "mode": "paper", "connector": "bybit",
        "websocket": {"enabled": True, "poll_fallback_period_seconds": 30},
    })
    assert cfg.websocket.enabled is True
    assert cfg.websocket.poll_fallback_period_seconds == 30
```

- [ ] **Step 2: Verifica che falliscano**

```powershell
pytest tests\runtime_v2\execution_gateway\test_adapter_config_ccxt.py -v -k "hedge_mode or websocket"
```

Atteso: `AttributeError: 'AdapterConfig' object has no attribute 'hedge_mode'`

- [ ] **Step 3: Implementa le modifiche a models.py**

Aggiungi `WebsocketConfig` dopo `LiveSafetyConfig`, prima di `EntryExecutionConfig`:

```python
class WebsocketConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    poll_fallback_enabled: bool = True
    poll_fallback_period_seconds: int = 60
```

Aggiungi in `AdapterConfig` dopo `testnet: bool = False`:

```python
    hedge_mode: bool = False
    websocket: WebsocketConfig = WebsocketConfig()
```

Aggiungi `"WebsocketConfig"` alla lista `__all__`.

- [ ] **Step 4: Verifica che i test passino**

```powershell
pytest tests\runtime_v2\execution_gateway\test_adapter_config_ccxt.py -v
```

Atteso: tutti PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/runtime_v2/execution_gateway/models.py `
        tests/runtime_v2/execution_gateway/test_adapter_config_ccxt.py
git commit -m "feat(execution): add hedge_mode and WebsocketConfig to AdapterConfig"
```

---

## Task 2: Hedge mode — BybitOrderBuilder positionIdx

**Files:**
- Modify: `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/order_builder.py`
- Modify: `tests/runtime_v2/execution_gateway/test_bybit_order_builder.py`

- [ ] **Step 1: Scrivi i test che falliranno**

Aggiungi in fondo a `tests/runtime_v2/execution_gateway/test_bybit_order_builder.py`:

```python
@pytest.mark.parametrize("command_type,payload,expected_position_idx", [
    (
        "PLACE_ENTRY",
        {"symbol": "BTC/USDT:USDT", "side": "LONG", "entry_type": "LIMIT",
         "qty": 0.01, "price": 50000.0},
        1,
    ),
    (
        "PLACE_ENTRY",
        {"symbol": "BTC/USDT:USDT", "side": "SHORT", "entry_type": "LIMIT",
         "qty": 0.01, "price": 50000.0},
        2,
    ),
    (
        "PLACE_PROTECTIVE_STOP",
        {"symbol": "BTC/USDT:USDT", "side": "LONG", "qty": 0.01, "stop_price": 45000.0},
        1,
    ),
    (
        "PLACE_TAKE_PROFIT",
        {"symbol": "BTC/USDT:USDT", "side": "LONG", "qty": 0.01, "price": 55000.0},
        1,
    ),
    (
        "CLOSE_FULL",
        {"symbol": "BTC/USDT:USDT", "side": "SHORT", "qty": 0.01},
        2,
    ),
])
def test_hedge_mode_adds_position_idx(command_type, payload, expected_position_idx):
    params = BybitOrderBuilder().build(
        command_type, payload, "tsb:1:1:entry:1", hedge_mode=True
    )
    assert params.extra_params.get("positionIdx") == expected_position_idx
    assert "reduceOnly" not in params.extra_params


def test_hedge_mode_false_no_position_idx():
    params = BybitOrderBuilder().build(
        "PLACE_ENTRY",
        {"symbol": "BTC/USDT:USDT", "side": "LONG", "entry_type": "LIMIT",
         "qty": 0.01, "price": 50000.0},
        "tsb:1:1:entry:1",
        hedge_mode=False,
    )
    assert "positionIdx" not in params.extra_params


def test_hedge_mode_noop_sync_protective_orders():
    params = BybitOrderBuilder().build(
        "SYNC_PROTECTIVE_ORDERS",
        {"symbol": "BTC/USDT:USDT", "side": "LONG"},
        "tsb:1:1:sync:1",
        hedge_mode=True,
    )
    assert params.action == "amend_sl_qty"
```

- [ ] **Step 2: Verifica che falliscano**

```powershell
pytest tests\runtime_v2\execution_gateway\test_bybit_order_builder.py -v -k "hedge_mode"
```

Atteso: `TypeError: build() got an unexpected keyword argument 'hedge_mode'`

- [ ] **Step 3: Implementa le modifiche a order_builder.py**

Modifica la firma di `build()`:

```python
def build(
    self,
    command_type: str,
    payload: dict,
    client_order_id: str,
    *,
    hedge_mode: bool = False,
) -> BybitOrderParams:
```

Cambia il dispatch di `SYNC_PROTECTIVE_ORDERS` (rimuovi il return diretto del noop — ora restituisce `amend_sl_qty`):

```python
        if command_type == "SYNC_PROTECTIVE_ORDERS":
            return BybitOrderParams(
                action="amend_sl_qty",
                symbol=payload["symbol"],
                position_side=payload["side"],
            )
```

Aggiungi il post-processing hedge_mode in fondo al metodo `build()`, prima del `raise ValueError`:

```python
        params = self._dispatch(command_type, payload, client_order_id)
        if hedge_mode and params.action == "create_order":
            params.extra_params["positionIdx"] = (
                1 if payload.get("side") == "LONG" else 2
            )
            params.extra_params.pop("reduceOnly", None)
        return params
```

Refactora `build()` per usare `_dispatch()`:

```python
    def build(
        self,
        command_type: str,
        payload: dict,
        client_order_id: str,
        *,
        hedge_mode: bool = False,
    ) -> BybitOrderParams:
        params = self._dispatch(command_type, payload, client_order_id)
        if hedge_mode and params.action == "create_order":
            params.extra_params["positionIdx"] = (
                1 if payload.get("side") == "LONG" else 2
            )
            params.extra_params.pop("reduceOnly", None)
        return params

    def _dispatch(
        self, command_type: str, payload: dict, client_order_id: str
    ) -> BybitOrderParams:
        if command_type == "PLACE_ENTRY":
            return self._place_entry(payload, client_order_id)
        if command_type == "PLACE_PROTECTIVE_STOP":
            return self._place_protective_stop(payload, client_order_id)
        if command_type == "PLACE_TAKE_PROFIT":
            return self._place_take_profit(payload, client_order_id)
        if command_type in {"CLOSE_PARTIAL", "CLOSE_FULL"}:
            return self._close_market(payload, client_order_id)
        if command_type == "CANCEL_PENDING_ENTRY":
            return self._cancel_pending_entry(payload, client_order_id)
        if command_type in {"MOVE_STOP_TO_BREAKEVEN", "MOVE_STOP"}:
            return self._move_stop(command_type, payload)
        if command_type == "SYNC_PROTECTIVE_ORDERS":
            return BybitOrderParams(
                action="amend_sl_qty",
                symbol=payload["symbol"],
                position_side=payload["side"],
            )
        raise ValueError(f"Unknown command_type: {command_type!r}")
```

- [ ] **Step 4: Verifica che i test passino**

```powershell
pytest tests\runtime_v2\execution_gateway\test_bybit_order_builder.py -v
```

Atteso: tutti PASS (inclusi i vecchi test — `SYNC_PROTECTIVE_ORDERS` ora ritorna `amend_sl_qty` invece di `noop`, aggiorna il vecchio test se esistente).

> **Nota:** Se c'è un test esistente che verifica `action == "noop"` per `SYNC_PROTECTIVE_ORDERS`, aggiornalo a `action == "amend_sl_qty"`.

- [ ] **Step 5: Commit**

```powershell
git add src/runtime_v2/execution_gateway/adapters/ccxt_bybit/order_builder.py `
        tests/runtime_v2/execution_gateway/test_bybit_order_builder.py
git commit -m "feat(execution): hedge_mode positionIdx in BybitOrderBuilder; SYNC→amend_sl_qty"
```

---

## Task 3: Hedge mode — CcxtBybitAdapter

**Files:**
- Modify: `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/adapter.py`
- Modify: `tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py`

- [ ] **Step 1: Scrivi i test che falliranno**

Aggiungi in `tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py`:

```python
def _make_adapter(exchange_mock, hedge_mode=False):
    from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.adapter import CcxtBybitAdapter
    return CcxtBybitAdapter(
        api_key="k", api_secret="s", testnet=True,
        connector="bybit", hedge_mode=hedge_mode, _exchange=exchange_mock,
    )


def test_hedge_mode_place_entry_adds_position_idx():
    exchange = MagicMock()
    exchange.create_order.return_value = {"id": "123"}
    adapter = _make_adapter(exchange, hedge_mode=True)
    adapter.place_order(
        command_type="PLACE_ENTRY",
        payload={"symbol": "BTC/USDT:USDT", "side": "LONG",
                 "entry_type": "LIMIT", "qty": 0.01, "price": 50000.0},
        client_order_id="tsb:1:1:entry:1",
        execution_account_id="main",
        connector="bybit",
    )
    call_params = exchange.create_order.call_args[1]["params"]
    assert call_params.get("positionIdx") == 1
    assert "reduceOnly" not in call_params


def test_hedge_mode_set_leverage_passes_position_idx_zero():
    exchange = MagicMock()
    adapter = _make_adapter(exchange, hedge_mode=True)
    adapter.set_leverage("BTC/USDT:USDT", 10, "main")
    call_params = exchange.set_leverage.call_args[1]["params"]
    assert call_params.get("positionIdx") == 0


def test_one_way_mode_set_leverage_no_position_idx():
    exchange = MagicMock()
    adapter = _make_adapter(exchange, hedge_mode=False)
    adapter.set_leverage("BTC/USDT:USDT", 10, "main")
    call_params = exchange.set_leverage.call_args[1]["params"]
    assert "positionIdx" not in call_params
```

- [ ] **Step 2: Verifica che falliscano**

```powershell
pytest tests\runtime_v2\execution_gateway\test_ccxt_bybit_adapter_unit.py -v -k "hedge_mode"
```

Atteso: `TypeError: __init__() got an unexpected keyword argument 'hedge_mode'`

- [ ] **Step 3: Implementa le modifiche a adapter.py**

Aggiungi `hedge_mode: bool = False` al costruttore e salvalo:

```python
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        testnet: bool,
        connector: str,
        capabilities: AdapterCapabilities | None = None,
        hedge_mode: bool = False,
        _exchange=None,
    ) -> None:
        # ... codice esistente invariato ...
        self._hedge_mode = hedge_mode
        self._builder = BybitOrderBuilder()
```

In `place_order`, passa `hedge_mode` al builder:

```python
        params = self._builder.build(
            command_type, payload, client_order_id, hedge_mode=self._hedge_mode
        )
```

Aggiorna `set_leverage`:

```python
    def set_leverage(self, symbol: str, leverage: int, execution_account_id: str) -> None:
        extra: dict = {
            "buyLeverage": str(leverage),
            "sellLeverage": str(leverage),
        }
        if self._hedge_mode:
            extra["positionIdx"] = 0
        self._exchange.set_leverage(leverage, symbol, params=extra)
```

- [ ] **Step 4: Verifica che i test passino**

```powershell
pytest tests\runtime_v2\execution_gateway\test_ccxt_bybit_adapter_unit.py -v
```

Atteso: tutti PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/runtime_v2/execution_gateway/adapters/ccxt_bybit/adapter.py `
        tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py
git commit -m "feat(execution): hedge_mode support in CcxtBybitAdapter"
```

---

## Task 4: SYNC_PROTECTIVE_ORDERS reale — _handle_amend_sl_qty

**Files:**
- Modify: `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/adapter.py`
- Modify: `tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py`

- [ ] **Step 1: Scrivi i test che falliranno**

```python
def test_sync_protective_orders_mode_b_amends_sl_qty():
    """Mode B: SL aperto come ordine separato reduceOnly."""
    exchange = MagicMock()
    # posizione residua: 0.5 BTC LONG
    exchange.fetch_positions.return_value = [
        {"side": "long", "contracts": 0.5,
         "info": {"symbol": "BTCUSDT", "stopLoss": "0"}}
    ]
    # SL aperto reduce-only
    exchange.fetch_open_orders.return_value = [
        {"id": "sl-order-1", "side": "sell", "type": "stop",
         "amount": 1.0, "reduceOnly": True, "stopPrice": "45000.0",
         "info": {}}
    ]
    exchange.edit_order.return_value = {"id": "sl-order-1"}
    adapter = _make_adapter(exchange)

    result = adapter.place_order(
        command_type="SYNC_PROTECTIVE_ORDERS",
        payload={"symbol": "BTC/USDT:USDT", "side": "LONG"},
        client_order_id="tsb:1:1:sync:1",
        execution_account_id="main",
        connector="bybit",
    )

    assert result.success is True
    exchange.edit_order.assert_called_once_with(
        "sl-order-1", "BTC/USDT:USDT", "stop", "sell", 0.5,
        params={"triggerPrice": 45000.0},
    )


def test_sync_protective_orders_mode_c_calls_trading_stop():
    """Mode C: SL attached, visibile nel campo info.stopLoss della posizione."""
    exchange = MagicMock()
    exchange.fetch_positions.return_value = [
        {"side": "long", "contracts": 0.7,
         "info": {"symbol": "BTCUSDT", "stopLoss": "45000.0"}}
    ]
    exchange.fetch_open_orders.return_value = []  # nessun SL separato
    exchange.private_post_v5_position_trading_stop = MagicMock(return_value={})
    adapter = _make_adapter(exchange)

    result = adapter.place_order(
        command_type="SYNC_PROTECTIVE_ORDERS",
        payload={"symbol": "BTC/USDT:USDT", "side": "LONG"},
        client_order_id="tsb:1:1:sync:1",
        execution_account_id="main",
        connector="bybit",
    )

    assert result.success is True
    exchange.private_post_v5_position_trading_stop.assert_called_once_with({
        "category": "linear",
        "symbol": "BTCUSDT",
        "positionIdx": 1,
        "stopLoss": "45000.0",
        "slSize": "0.7",
    })


def test_sync_protective_orders_qty_zero_cancels_reduce_only():
    """Posizione chiusa: cancella tutti gli ordini reduce-only residui."""
    exchange = MagicMock()
    exchange.fetch_positions.return_value = [
        {"side": "long", "contracts": 0.0,
         "info": {"symbol": "BTCUSDT", "stopLoss": "0"}}
    ]
    exchange.fetch_open_orders.return_value = [
        {"id": "sl-1", "side": "sell", "reduceOnly": True, "stopPrice": "45000"},
        {"id": "tp-1", "side": "sell", "reduceOnly": True, "stopPrice": None},
    ]
    adapter = _make_adapter(exchange)

    result = adapter.place_order(
        command_type="SYNC_PROTECTIVE_ORDERS",
        payload={"symbol": "BTC/USDT:USDT", "side": "LONG"},
        client_order_id="tsb:1:1:sync:1",
        execution_account_id="main",
        connector="bybit",
    )

    assert result.success is True
    assert exchange.cancel_order.call_count == 2


def test_sync_protective_orders_no_sl_found_returns_success():
    """Nessun SL trovato (già chiuso/cancellato): non è un errore."""
    exchange = MagicMock()
    exchange.fetch_positions.return_value = [
        {"side": "long", "contracts": 0.5,
         "info": {"symbol": "BTCUSDT", "stopLoss": "0"}}
    ]
    exchange.fetch_open_orders.return_value = []
    adapter = _make_adapter(exchange)

    result = adapter.place_order(
        command_type="SYNC_PROTECTIVE_ORDERS",
        payload={"symbol": "BTC/USDT:USDT", "side": "LONG"},
        client_order_id="tsb:1:1:sync:1",
        execution_account_id="main",
        connector="bybit",
    )

    assert result.success is True
    exchange.edit_order.assert_not_called()
```

- [ ] **Step 2: Verifica che falliscano**

```powershell
pytest tests\runtime_v2\execution_gateway\test_ccxt_bybit_adapter_unit.py -v -k "sync_protective"
```

Atteso: `FAIL` — `place_order` riceve `action="amend_sl_qty"` e cade nel `return AdapterResult(success=False, error="unhandled action: 'amend_sl_qty'")`

- [ ] **Step 3: Implementa _handle_amend_sl_qty in adapter.py**

Aggiungi il branch `amend_sl_qty` in `place_order`, dopo il branch `edit_sl`:

```python
            if params.action == "amend_sl_qty":
                return self._handle_amend_sl_qty(params.symbol, params.position_side)
```

Aggiungi il metodo privato in `CcxtBybitAdapter`:

```python
    def _handle_amend_sl_qty(self, symbol: str, side: str) -> AdapterResult:
        close_side = "sell" if side == "LONG" else "buy"
        position_idx = 1 if side == "LONG" else 2

        try:
            positions = self._exchange.fetch_positions([symbol])
        except Exception as e:
            return AdapterResult(success=False, error=f"fetch_positions failed: {e}")

        current_qty = 0.0
        pos_info: dict = {}
        for pos in positions:
            if str(pos.get("side") or "").lower() == side.lower():
                current_qty = float(pos.get("contracts") or 0.0)
                pos_info = pos.get("info") or {}
                break

        if current_qty == 0.0:
            # posizione chiusa: cancella reduce-only residui
            try:
                open_orders = self._exchange.fetch_open_orders(symbol)
                for o in open_orders:
                    if o.get("reduceOnly") and o["side"] == close_side:
                        self._exchange.cancel_order(o["id"], symbol)
            except Exception as e:
                logger.warning("cancel residual orders failed: %s", e)
            return AdapterResult(success=True)

        # tenta Mode B: SL separato reduce-only
        try:
            open_orders = self._exchange.fetch_open_orders(symbol)
        except Exception:
            open_orders = []

        sl_orders = [
            o for o in open_orders
            if o.get("reduceOnly") and o.get("stopPrice") and o["side"] == close_side
        ]
        if sl_orders:
            sl = sl_orders[-1]
            try:
                self._exchange.edit_order(
                    sl["id"], symbol, sl["type"], sl["side"],
                    current_qty,
                    params={"triggerPrice": float(sl["stopPrice"])},
                )
            except Exception as e:
                return AdapterResult(success=False, error=f"edit_order sl failed: {e}")
            return AdapterResult(success=True)

        # tenta Mode C: SL attached nella posizione
        attached_sl = pos_info.get("stopLoss", "0")
        if attached_sl and float(attached_sl) > 0:
            bybit_symbol = pos_info.get("symbol", symbol.replace("/", "").replace(":USDT", ""))
            try:
                self._exchange.private_post_v5_position_trading_stop({
                    "category": "linear",
                    "symbol": bybit_symbol,
                    "positionIdx": position_idx,
                    "stopLoss": str(attached_sl),
                    "slSize": str(current_qty),
                })
            except Exception as e:
                return AdapterResult(success=False, error=f"trading_stop failed: {e}")
            return AdapterResult(success=True)

        # nessun SL trovato: non è un errore
        return AdapterResult(success=True)
```

- [ ] **Step 4: Verifica che i test passino**

```powershell
pytest tests\runtime_v2\execution_gateway\test_ccxt_bybit_adapter_unit.py -v -k "sync_protective"
```

Atteso: tutti e 4 PASS.

- [ ] **Step 5: Esegui la suite completa dell'adapter**

```powershell
pytest tests\runtime_v2\execution_gateway\test_ccxt_bybit_adapter_unit.py -v
```

Atteso: tutti PASS.

- [ ] **Step 6: Commit**

```powershell
git add src/runtime_v2/execution_gateway/adapters/ccxt_bybit/adapter.py `
        tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py
git commit -m "feat(execution): SYNC_PROTECTIVE_ORDERS reale — amend SL qty via fetch_positions"
```

---

## Task 5: OD-F1-2 — fallback get_order_status per Mode C

**Files:**
- Modify: `src/runtime_v2/execution_gateway/repositories.py`
- Modify: `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/adapter.py`
- Modify: `tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py`

- [ ] **Step 1: Aggiungi metodo repo e scrivi i test**

In `repositories.py`, aggiungi dopo `get_entry_client_order_id`:

```python
    def get_payload_by_client_order_id(self, client_order_id: str) -> dict | None:
        conn = sqlite3.connect(self._db)
        try:
            row = conn.execute(
                "SELECT payload_json FROM ops_execution_commands "
                "WHERE client_order_id=? LIMIT 1",
                (client_order_id,),
            ).fetchone()
            return json.loads(row[0]) if row else None
        finally:
            conn.close()
```

Aggiungi in `test_ccxt_bybit_adapter_unit.py`:

```python
def _make_adapter_with_repo(exchange_mock, repo_mock=None):
    from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.adapter import CcxtBybitAdapter
    return CcxtBybitAdapter(
        api_key="k", api_secret="s", testnet=True,
        connector="bybit", _exchange=exchange_mock, repo=repo_mock,
    )


def test_od_f1_2_fallback_returns_filled_when_position_closed():
    """SL attached non trovabile via orderLinkId: fallback via fetch_positions."""
    exchange = MagicMock()
    # fetch_open_orders e fetch_closed_orders non trovano nulla
    exchange.fetch_open_orders.return_value = []
    exchange.fetch_closed_orders.return_value = []
    # la posizione è chiusa
    exchange.fetch_positions.return_value = [
        {"side": "long", "contracts": 0.0, "info": {}}
    ]

    repo = MagicMock()
    repo.get_payload_by_client_order_id.return_value = {
        "symbol": "BTC/USDT:USDT", "side": "LONG",
    }

    adapter = _make_adapter_with_repo(exchange, repo)
    result = adapter.get_order_status(
        client_order_id="tsb:1:1:sl:1",
        execution_account_id="main",
    )

    assert result is not None
    assert result.status == "FILLED"


def test_od_f1_2_fallback_returns_none_when_position_still_open():
    """Posizione ancora aperta: non è detto che il SL attached si sia riempito."""
    exchange = MagicMock()
    exchange.fetch_open_orders.return_value = []
    exchange.fetch_closed_orders.return_value = []
    exchange.fetch_positions.return_value = [
        {"side": "long", "contracts": 0.5, "info": {}}
    ]

    repo = MagicMock()
    repo.get_payload_by_client_order_id.return_value = {
        "symbol": "BTC/USDT:USDT", "side": "LONG",
    }

    adapter = _make_adapter_with_repo(exchange, repo)
    result = adapter.get_order_status(
        client_order_id="tsb:1:1:sl:1",
        execution_account_id="main",
    )

    assert result is None


def test_od_f1_2_fallback_skipped_for_entry_role():
    """Il fallback si applica solo a role=sl e role=tp."""
    exchange = MagicMock()
    exchange.fetch_open_orders.return_value = []
    exchange.fetch_closed_orders.return_value = []

    repo = MagicMock()
    repo.get_payload_by_client_order_id.return_value = {
        "symbol": "BTC/USDT:USDT", "side": "LONG",
    }

    adapter = _make_adapter_with_repo(exchange, repo)
    result = adapter.get_order_status(
        client_order_id="tsb:1:1:entry:1",
        execution_account_id="main",
    )

    assert result is None
    exchange.fetch_positions.assert_not_called()
```

- [ ] **Step 2: Verifica che falliscano**

```powershell
pytest tests\runtime_v2\execution_gateway\test_ccxt_bybit_adapter_unit.py -v -k "od_f1_2"
```

Atteso: `TypeError: __init__() got an unexpected keyword argument 'repo'`

- [ ] **Step 3: Implementa il fallback in adapter.py**

Aggiungi `repo` al costruttore (opzionale):

```python
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        testnet: bool,
        connector: str,
        capabilities: AdapterCapabilities | None = None,
        hedge_mode: bool = False,
        repo=None,          # GatewayCommandRepository | None
        _exchange=None,
    ) -> None:
        # ... invariato ...
        self._repo = repo
```

Sostituisci `get_order_status` con la versione estesa:

```python
    def get_order_status(
        self,
        *,
        client_order_id: str,
        execution_account_id: str,
    ) -> RawAdapterOrder | None:
        # path normale
        try:
            orders = self._exchange.fetch_open_orders(
                None, params={"orderLinkId": client_order_id}
            )
        except Exception as exc:
            logger.debug("fetch_open_orders error: %s", exc)
            orders = []
        if not orders:
            try:
                orders = self._exchange.fetch_closed_orders(
                    None, params={"orderLinkId": client_order_id}
                )
            except Exception as exc:
                logger.debug("fetch_closed_orders error: %s", exc)
                orders = []
        if orders:
            return StatusMapper.map(orders[-1], client_order_id=client_order_id)

        # OD-F1-2: fallback via fetch_positions per SL/TP attached Mode C
        from src.runtime_v2.execution_gateway import client_order_id as coid_mod
        try:
            coid = coid_mod.parse(client_order_id)
        except ValueError:
            return None
        if coid.role not in ("sl", "tp"):
            return None
        if self._repo is None:
            return None

        payload = self._repo.get_payload_by_client_order_id(client_order_id)
        if payload is None:
            return None

        symbol = payload.get("symbol")
        side = payload.get("side")
        if not symbol or not side:
            return None

        try:
            positions = self._exchange.fetch_positions([symbol])
        except Exception:
            return None

        current_qty = 0.0
        for pos in positions:
            if str(pos.get("side") or "").lower() == side.lower():
                current_qty = float(pos.get("contracts") or 0.0)
                break

        if current_qty == 0.0:
            return RawAdapterOrder(
                client_order_id=client_order_id,
                status="FILLED",
                filled_qty=0.0,
                average_price=None,
            )
        return None
```

- [ ] **Step 4: Verifica che i test passino**

```powershell
pytest tests\runtime_v2\execution_gateway\test_ccxt_bybit_adapter_unit.py -v
```

Atteso: tutti PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/runtime_v2/execution_gateway/repositories.py `
        src/runtime_v2/execution_gateway/adapters/ccxt_bybit/adapter.py `
        tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py
git commit -m "feat(execution): OD-F1-2 — get_order_status fallback via fetch_positions for Mode C"
```

---

## Task 6: BybitWsFillWatcher — WebSocket fill real-time

**Files:**
- Create: `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/ws_fill_watcher.py`
- Create: `tests/runtime_v2/execution_gateway/test_bybit_ws_fill_watcher.py`

- [ ] **Step 1: Scrivi i test**

Crea `tests/runtime_v2/execution_gateway/test_bybit_ws_fill_watcher.py`:

```python
from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.ws_fill_watcher import (
    BybitWsFillWatcher,
)
from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository


def _make_db() -> str:
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
    conn = sqlite3.connect(tmp.name)
    conn.executescript("""
        CREATE TABLE ops_execution_commands (
            command_id INTEGER PRIMARY KEY,
            trade_chain_id INTEGER,
            command_type TEXT,
            status TEXT,
            payload_json TEXT,
            idempotency_key TEXT,
            created_at TEXT,
            updated_at TEXT,
            client_order_id TEXT,
            adapter TEXT,
            execution_account_id TEXT,
            result_payload_json TEXT,
            sent_at TEXT,
            acknowledged_at TEXT,
            completed_at TEXT,
            retry_count INTEGER DEFAULT 0,
            next_retry_at TEXT
        );
        CREATE TABLE ops_exchange_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_chain_id INTEGER,
            event_type TEXT,
            payload_json TEXT,
            processing_status TEXT DEFAULT 'NEW',
            idempotency_key TEXT UNIQUE,
            received_at TEXT
        );
    """)
    # Inserisci un comando SENT con client_order_id noto
    conn.execute(
        "INSERT INTO ops_execution_commands "
        "(command_id, trade_chain_id, command_type, status, payload_json, "
        "idempotency_key, client_order_id) "
        "VALUES (1, 10, 'PLACE_ENTRY', 'SENT', '{\"sequence\": 1}', 'ik1', 'tsb:10:1:entry:1')"
    )
    conn.commit()
    conn.close()
    return tmp.name


def test_watcher_inserts_fill_event_for_known_order():
    db_path = _make_db()
    repo = GatewayCommandRepository(db_path)

    filled_order = {
        "clientOrderId": "tsb:10:1:entry:1",
        "id": "exchange-order-123",
        "status": "closed",
        "filled": 0.01,
        "average": 50000.0,
    }

    with patch(
        "src.runtime_v2.execution_gateway.adapters.ccxt_bybit.ws_fill_watcher.ccxtpro"
    ) as mock_ccxtpro:
        mock_exchange = AsyncMock()
        # Prima chiamata ritorna l'ordine filled, poi ferma il loop
        call_count = 0

        async def mock_watch_orders():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [filled_order]
            raise asyncio.CancelledError()

        mock_exchange.watch_orders = mock_watch_orders
        mock_exchange.close = AsyncMock()
        mock_ccxtpro.bybit.return_value = mock_exchange

        watcher = BybitWsFillWatcher(
            api_key="k", api_secret="s", testnet=True,
            ops_db_path=db_path, repo=repo,
        )
        watcher.start()
        time.sleep(0.3)
        watcher.stop()

    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT event_type, trade_chain_id FROM ops_exchange_events").fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0][0] == "ENTRY_FILLED"
    assert rows[0][1] == 10


def test_watcher_discards_unknown_order():
    db_path = _make_db()
    repo = GatewayCommandRepository(db_path)

    unknown_order = {
        "clientOrderId": "unknown-bybit-order",
        "id": "bybit-123",
        "status": "closed",
        "filled": 1.0,
        "average": 100.0,
    }

    with patch(
        "src.runtime_v2.execution_gateway.adapters.ccxt_bybit.ws_fill_watcher.ccxtpro"
    ) as mock_ccxtpro:
        mock_exchange = AsyncMock()
        call_count = 0

        async def mock_watch_orders():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [unknown_order]
            raise asyncio.CancelledError()

        mock_exchange.watch_orders = mock_watch_orders
        mock_exchange.close = AsyncMock()
        mock_ccxtpro.bybit.return_value = mock_exchange

        watcher = BybitWsFillWatcher(
            api_key="k", api_secret="s", testnet=True,
            ops_db_path=db_path, repo=repo,
        )
        watcher.start()
        time.sleep(0.3)
        watcher.stop()

    conn = sqlite3.connect(db_path)
    count = conn.execute("SELECT COUNT(*) FROM ops_exchange_events").fetchone()[0]
    conn.close()
    assert count == 0


def test_watcher_idempotent_on_duplicate_fill():
    """Stesso fill inviato due volte: INSERT OR IGNORE, nessun errore."""
    db_path = _make_db()
    repo = GatewayCommandRepository(db_path)

    filled_order = {
        "clientOrderId": "tsb:10:1:entry:1",
        "id": "exchange-order-123",
        "status": "closed",
        "filled": 0.01,
        "average": 50000.0,
    }

    with patch(
        "src.runtime_v2.execution_gateway.adapters.ccxt_bybit.ws_fill_watcher.ccxtpro"
    ) as mock_ccxtpro:
        mock_exchange = AsyncMock()
        call_count = 0

        async def mock_watch_orders():
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return [filled_order]
            raise asyncio.CancelledError()

        mock_exchange.watch_orders = mock_watch_orders
        mock_exchange.close = AsyncMock()
        mock_ccxtpro.bybit.return_value = mock_exchange

        watcher = BybitWsFillWatcher(
            api_key="k", api_secret="s", testnet=True,
            ops_db_path=db_path, repo=repo,
        )
        watcher.start()
        time.sleep(0.4)
        watcher.stop()

    conn = sqlite3.connect(db_path)
    count = conn.execute("SELECT COUNT(*) FROM ops_exchange_events").fetchone()[0]
    conn.close()
    assert count == 1  # non duplicato
```

- [ ] **Step 2: Verifica che falliscano**

```powershell
pytest tests\runtime_v2\execution_gateway\test_bybit_ws_fill_watcher.py -v
```

Atteso: `ModuleNotFoundError: ws_fill_watcher`

- [ ] **Step 3: Implementa ws_fill_watcher.py**

Crea `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/ws_fill_watcher.py`:

```python
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone

import ccxt.pro as ccxtpro

from src.runtime_v2.execution_gateway import client_order_id as coid_mod
from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.status_mapper import StatusMapper
from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class BybitWsFillWatcher:
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        testnet: bool,
        ops_db_path: str,
        repo: GatewayCommandRepository,
        reconciliation_callback=None,  # Callable[[], None] | None
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._testnet = testnet
        self._ops_db = ops_db_path
        self._repo = repo
        self._reconciliation_callback = reconciliation_callback
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="bybit-ws-watcher")
        self._thread.start()
        logger.info("BybitWsFillWatcher started")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None
        logger.info("BybitWsFillWatcher stopped")

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self._watch_loop())
        finally:
            loop.close()

    async def _watch_loop(self) -> None:
        exchange = ccxtpro.bybit({
            "apiKey": self._api_key,
            "secret": self._api_secret,
            "options": {"defaultType": "linear"},
        })
        if self._testnet:
            exchange.set_sandbox_mode(True)
        try:
            while not self._stop_event.is_set():
                try:
                    orders = await exchange.watch_orders()
                    active = self._repo.get_active_client_order_ids()
                    for order in orders:
                        client_order_id = order.get("clientOrderId") or ""
                        if client_order_id and client_order_id in active:
                            raw = StatusMapper.map(order, client_order_id=client_order_id)
                            if raw.is_filled:
                                self._save_fill(client_order_id, raw)
                except asyncio.CancelledError:
                    break
                except Exception:
                    logger.exception("watch_orders error — retrying in 5s")
                    await asyncio.sleep(5)
                    # riconciliazione dopo riconnessione: copre il gap
                    if self._reconciliation_callback:
                        try:
                            self._reconciliation_callback()
                        except Exception:
                            logger.exception("reconciliation_callback failed")
        finally:
            try:
                await exchange.close()
            except Exception:
                pass

    def _save_fill(self, client_order_id: str, raw) -> None:
        try:
            coid = coid_mod.parse(client_order_id)
        except ValueError:
            logger.warning("ws: cannot parse client_order_id: %s", client_order_id)
            return

        exchange_order_id = raw.exchange_order_id or client_order_id

        if coid.role == "entry":
            event_type = "ENTRY_FILLED"
            payload = {"fill_price": raw.average_price, "filled_qty": raw.filled_qty,
                       "command_id": coid.command_id}
        elif coid.role == "sl":
            event_type = "SL_FILLED"
            payload = {"fill_price": raw.average_price, "filled_qty": raw.filled_qty,
                       "command_id": coid.command_id}
        elif coid.role == "tp":
            remaining = self._repo.count_active_tps(coid.trade_chain_id)
            event_type = "TP_FILLED"
            payload = {"tp_level": coid.sequence, "is_final": remaining <= 1,
                       "fill_price": raw.average_price, "filled_qty": raw.filled_qty,
                       "command_id": coid.command_id}
        elif coid.role == "exit_partial":
            event_type = "CLOSE_PARTIAL_FILLED"
            payload = {"fill_price": raw.average_price, "filled_qty": raw.filled_qty,
                       "command_id": coid.command_id}
        elif coid.role == "exit_full":
            event_type = "CLOSE_FULL_FILLED"
            payload = {"fill_price": raw.average_price, "filled_qty": raw.filled_qty,
                       "command_id": coid.command_id}
        else:
            logger.warning("ws: unknown role '%s' in %s", coid.role, client_order_id)
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
        except Exception:
            logger.exception("ws: save_fill failed for %s", client_order_id)
        finally:
            conn.close()


__all__ = ["BybitWsFillWatcher"]
```

- [ ] **Step 4: Aggiungi get_active_client_order_ids a repositories.py**

```python
    def get_active_client_order_ids(self) -> set[str]:
        conn = sqlite3.connect(self._db)
        try:
            rows = conn.execute(
                "SELECT client_order_id FROM ops_execution_commands "
                "WHERE status IN ('SENT','ACK') AND client_order_id IS NOT NULL"
            ).fetchall()
            return {r[0] for r in rows}
        finally:
            conn.close()
```

- [ ] **Step 5: Verifica che i test passino**

```powershell
pytest tests\runtime_v2\execution_gateway\test_bybit_ws_fill_watcher.py -v
```

Atteso: tutti PASS.

- [ ] **Step 6: Verifica requirements.txt**

Controlla che `requirements.txt` abbia `ccxt>=4.4.0` (ccxt.pro è incluso dal 4.x, nessun pacchetto separato):

```powershell
python -c "import ccxt.pro as p; print('ccxt.pro ok:', p.__version__ if hasattr(p, '__version__') else 'loaded')"
```

Se la versione installata è < 4.4.0, aggiorna `requirements.txt`:

```
ccxt>=4.4.0
```

- [ ] **Step 7: Commit**

```powershell
git add src/runtime_v2/execution_gateway/adapters/ccxt_bybit/ws_fill_watcher.py `
        src/runtime_v2/execution_gateway/repositories.py `
        tests/runtime_v2/execution_gateway/test_bybit_ws_fill_watcher.py `
        requirements.txt
git commit -m "feat(execution): BybitWsFillWatcher — ccxt.pro WebSocket fill real-time"
```

---

## Task 7: ExchangeEventSyncWorker — reconciliation + fallback configurabile

**Files:**
- Modify: `src/runtime_v2/execution_gateway/event_sync.py`
- Modify: `tests/runtime_v2/execution_gateway/test_event_sync.py`

- [ ] **Step 1: Scrivi i test che falliranno**

Aggiungi in `tests/runtime_v2/execution_gateway/test_event_sync.py`:

```python
def test_run_reconciliation_processes_sent_commands(tmp_path):
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository
    from src.runtime_v2.execution_gateway.models import RawAdapterOrder
    from unittest.mock import MagicMock
    import sqlite3

    db_path = str(tmp_path / "ops.sqlite3")
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE ops_execution_commands (
            command_id INTEGER PRIMARY KEY,
            trade_chain_id INTEGER,
            command_type TEXT,
            status TEXT DEFAULT 'SENT',
            payload_json TEXT DEFAULT '{"sequence":1}',
            idempotency_key TEXT,
            created_at TEXT,
            updated_at TEXT,
            client_order_id TEXT,
            adapter TEXT,
            execution_account_id TEXT,
            result_payload_json TEXT,
            sent_at TEXT,
            acknowledged_at TEXT,
            completed_at TEXT,
            retry_count INTEGER DEFAULT 0,
            next_retry_at TEXT
        );
        CREATE TABLE ops_exchange_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_chain_id INTEGER,
            event_type TEXT,
            payload_json TEXT,
            processing_status TEXT DEFAULT 'NEW',
            idempotency_key TEXT UNIQUE,
            received_at TEXT
        );
        INSERT INTO ops_execution_commands
            (command_id, trade_chain_id, command_type, status, idempotency_key, client_order_id)
        VALUES (1, 5, 'PLACE_ENTRY', 'SENT', 'ik1', 'tsb:5:1:entry:1');
    """)
    conn.commit()
    conn.close()

    adapter = MagicMock()
    adapter.get_order_status.return_value = RawAdapterOrder(
        client_order_id="tsb:5:1:entry:1",
        exchange_order_id="ex-1",
        status="FILLED",
        filled_qty=0.01,
        average_price=50000.0,
    )

    repo = GatewayCommandRepository(db_path)
    worker = ExchangeEventSyncWorker(
        ops_db_path=db_path, adapter=adapter, repo=repo,
        execution_account_id="main",
    )
    count = worker.run_reconciliation()

    assert count == 1
    conn2 = sqlite3.connect(db_path)
    rows = conn2.execute("SELECT event_type FROM ops_exchange_events").fetchall()
    conn2.close()
    assert rows[0][0] == "ENTRY_FILLED"
```

- [ ] **Step 2: Verifica che fallisca**

```powershell
pytest tests\runtime_v2\execution_gateway\test_event_sync.py -v -k "reconciliation"
```

Atteso: `AttributeError: 'ExchangeEventSyncWorker' object has no attribute 'run_reconciliation'`

- [ ] **Step 3: Implementa run_reconciliation in event_sync.py**

Aggiungi il metodo a `ExchangeEventSyncWorker`:

```python
    def run_reconciliation(self) -> int:
        """Passaggio REST una-tantum: controlla tutti i comandi SENT e salva i fill.
        Chiamato a startup e dopo ogni riconnessione WS."""
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
                    saved = self._normalize_and_save(client_order_id, raw)
                    if saved:
                        self._repo.mark_done(cmd.command_id)
                        processed += 1
            except Exception:
                logger.exception("reconciliation error for %s", client_order_id)
        return processed
```

- [ ] **Step 4: Verifica che i test passino**

```powershell
pytest tests\runtime_v2\execution_gateway\test_event_sync.py -v
```

Atteso: tutti PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/runtime_v2/execution_gateway/event_sync.py `
        tests/runtime_v2/execution_gateway/test_event_sync.py
git commit -m "feat(execution): ExchangeEventSyncWorker.run_reconciliation() — startup REST check"
```

---

## Task 8: Wiring — factory, aggiornamento execution.yaml

**Files:**
- Modify: `src/runtime_v2/execution_gateway/adapters/factory.py`
- Modify: `config/execution.yaml`
- Modify: `tests/runtime_v2/execution_gateway/test_adapter_factory.py`

- [ ] **Step 1: Scrivi i test che falliranno**

Aggiungi in `tests/runtime_v2/execution_gateway/test_adapter_factory.py`:

```python
def test_factory_ccxt_bybit_passes_hedge_mode(monkeypatch):
    from src.runtime_v2.execution_gateway.adapters.factory import build_adapter
    from src.runtime_v2.execution_gateway.models import AdapterConfig

    monkeypatch.setenv("BYBIT_API_SECRET_HEDGE_MAIN", "secret123")
    cfg = AdapterConfig.model_validate({
        "type": "ccxt_bybit", "mode": "paper",
        "connector": "bybit", "testnet": True,
        "api_key": "key123", "hedge_mode": True,
    })
    adapter = build_adapter("hedge_main", cfg)
    assert adapter._hedge_mode is True


def test_factory_ccxt_bybit_hedge_mode_false_by_default(monkeypatch):
    from src.runtime_v2.execution_gateway.adapters.factory import build_adapter
    from src.runtime_v2.execution_gateway.models import AdapterConfig

    monkeypatch.setenv("BYBIT_API_SECRET_BYBIT_MAIN", "secret123")
    cfg = AdapterConfig.model_validate({
        "type": "ccxt_bybit", "mode": "paper",
        "connector": "bybit", "testnet": True,
    })
    adapter = build_adapter("bybit_main", cfg)
    assert adapter._hedge_mode is False
```

- [ ] **Step 2: Verifica che falliscano**

```powershell
pytest tests\runtime_v2\execution_gateway\test_adapter_factory.py -v -k "hedge_mode"
```

Atteso: `AssertionError` — factory non passa ancora `hedge_mode`

- [ ] **Step 3: Aggiorna factory.py**

Sostituisci il branch `ccxt_bybit` in `build_adapter`:

```python
    if cfg.type == "ccxt_bybit":
        from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.adapter import CcxtBybitAdapter
        api_secret = os.environ.get(f"BYBIT_API_SECRET_{adapter_name.upper()}")
        return CcxtBybitAdapter(
            api_key=cfg.api_key or "",
            api_secret=api_secret or "",
            testnet=cfg.testnet,
            connector=cfg.connector,
            capabilities=cfg.capabilities,
            hedge_mode=cfg.hedge_mode,
        )
```

> **Nota:** `repo` non viene passato dalla factory — viene iniettato dall'`ExecutionCommandWorker` se disponibile. Per ora il fallback OD-F1-2 è opzionale (repo=None significa che il fallback è disabilitato finché il repo non è iniettato). Il wiring completo del repo è fuori scope di questa Fase (richiede refactor del worker).

- [ ] **Step 4: Aggiorna il commento in execution.yaml**

Nel blocco commentato `ccxt_bybit`, aggiungi `hedge_mode`:

```yaml
#     bybit_main:
#       type: ccxt_bybit
#       mode: paper
#       connector: bybit
#       testnet: true
#       api_key: "abc123"
#       leverage: 10
#       hedge_mode: false        # true se account Bybit in hedge mode
#       websocket:
#         enabled: true
#         poll_fallback_enabled: true
#         poll_fallback_period_seconds: 60
```

- [ ] **Step 5: Verifica che i test passino**

```powershell
pytest tests\runtime_v2\execution_gateway\test_adapter_factory.py -v
```

Atteso: tutti PASS.

- [ ] **Step 6: Esegui la suite completa**

```powershell
pytest tests\runtime_v2\execution_gateway -v --tb=short
```

Atteso: tutti PASS, nessuna regressione.

- [ ] **Step 7: Commit**

```powershell
git add src/runtime_v2/execution_gateway/adapters/factory.py `
        config/execution.yaml `
        tests/runtime_v2/execution_gateway/test_adapter_factory.py
git commit -m "feat(execution): factory passes hedge_mode; execution.yaml example updated"
```

---

## Task 9: Suite finale e regressione

**Files:**
- Run: suite completa runtime_v2

- [ ] **Step 1: Esegui la suite completa**

```powershell
pytest tests\runtime_v2 -v --tb=short
```

Atteso: tutti PASS. Nota: i test `@pytest.mark.bybit_testnet` sono skippati automaticamente senza env vars.

- [ ] **Step 2: Verifica import ccxt.pro**

```powershell
python -c "import ccxt.pro; print('ok')"
```

Atteso: `ok`. Se fallisce: `pip install "ccxt>=4.4.0"` e aggiorna `requirements.txt`.

- [ ] **Step 3: Commit finale se necessario**

Se requirements.txt è stato aggiornato:

```powershell
git add requirements.txt
git commit -m "chore: bump ccxt>=4.4.0 for ccxt.pro WebSocket support"
```
