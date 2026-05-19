# Runtime V2 — Fase 1: CcxtBybitAdapter Design

**Status:** approvata — pronta per implementation plan
**Data:** 2026-05-18
**Dipende da:** Fase 0 lifecycle contract (completata, commit 3d8db75)

---

## §1 — Obiettivo

Introdurre `CcxtBybitAdapter` come primo adapter reale nel sistema runtime_v2. Fase 0 ha consolidato il contratto lifecycle interno usando FakeAdapter. Fase 1 collega il sistema a Bybit via CCXT su testnet, abilitando:

- Piazzamento ordini reali (entry, SL, TP, close)
- Polling fill via `ExchangeEventSyncWorker` già esistente (nessun cambio)
- Mode C completo con attached TP/SL (`tpslMode: Partial`) — risolve OD-C1
- Routing multi-account: uno o più subaccount Bybit per trader/gruppo

---

## §2 — Architettura

### Approccio scelto: Adapter + BybitOrderBuilder

`CcxtBybitAdapter` gestisce chiamate CCXT e error handling. `BybitOrderBuilder` traduce `command_type + payload → parametri CCXT`. Il builder è testabile con puri unit test; l'adapter è testato con gated integration test su Bybit testnet.

### File map

```
src/runtime_v2/execution_gateway/
├── adapters/
│   ├── base.py                        (invariato)
│   ├── fake.py                        (invariato)
│   ├── factory.py                     (+ ccxt_bybit branch)
│   ├── ccxt_bybit/
│   │   ├── __init__.py
│   │   ├── adapter.py                 (CcxtBybitAdapter)
│   │   ├── order_builder.py           (BybitOrderBuilder)
│   │   └── status_mapper.py           (CCXT status → RawAdapterOrder)
│   └── hummingbot_api.py              (invariato)
├── models.py                          (AdapterConfig + api_key, testnet)

config/
└── execution.yaml                     (esempio adapter ccxt_bybit)

requirements.txt                       (+ ccxt>=4.0)

tests/runtime_v2/execution_gateway/
├── test_bybit_order_builder.py        (unit, nessuna chiamata di rete)
└── test_ccxt_bybit_adapter.py         (gated: @pytest.mark.bybit_testnet)
```

### Flusso order placement

```
ExecutionGateway.process(cmd)
      ↓
CcxtBybitAdapter.place_order(command_type, payload, client_order_id, ...)
      ↓
BybitOrderBuilder.build(command_type, payload, client_order_id)
→ dict con params CCXT pronti
      ↓
exchange.create_order(**params)   ← CCXT sync
      ↓
AdapterResult(success, exchange_order_id, ...)
```

### Flusso polling fill (ExchangeEventSyncWorker — invariato)

```
ExchangeEventSyncWorker.run_once()
      ↓
CcxtBybitAdapter.get_order_status(client_order_id)
→ exchange.fetch_orders(params={orderLinkId: client_order_id})
→ StatusMapper.map(ccxt_order) → RawAdapterOrder
      ↓
_normalize_and_save() → INSERT ops_exchange_events
```

`ExchangeEventSyncWorker` non richiede modifiche — è già adapter-agnostico.

---

## §3 — BybitOrderBuilder: mapping command → CCXT

### Tabella comandi

| command_type | CCXT call | Parametri chiave |
|---|---|---|
| `PLACE_ENTRY` | `create_order` | type=limit/market, side, amount, price, `orderLinkId` |
| `PLACE_PROTECTIVE_STOP` | `create_order` | type=stop, reduceOnly=True, triggerPrice=stop_price |
| `PLACE_TAKE_PROFIT` | `create_order` | type=limit, reduceOnly=True |
| `CANCEL_PENDING_ENTRY` | `cancel_order` | cerca orderId via orderLinkId → cancella |
| `CLOSE_PARTIAL` | `create_order` | type=market, reduceOnly=True, qty esplicita |
| `CLOSE_FULL` | `create_order` | type=market, reduceOnly=True |
| `MOVE_STOP_TO_BREAKEVEN` | `edit_order` | amend triggerPrice SL esistente |
| `MOVE_STOP` | `edit_order` | amend triggerPrice SL esistente |
| `SYNC_PROTECTIVE_ORDERS` | no-op | restituisce sentinel, adapter ritorna `AdapterResult(success=True)` |

### client_order_id come orderLinkId

Il nostro `client_order_id` (formato `tsb:10:5:entry:1`, max ~20 char) viene passato come `orderLinkId` di Bybit (max 36 char). Ogni chiamata include `orderLinkId` nei params CCXT.

### Mode C — entry con attached TP/SL (risoluzione OD-C1)

Quando `payload["native_attached_tpsl"] is True`:

```python
params["takeProfit"] = payload["attached_take_profit"]
params["stopLoss"] = payload["attached_stop_loss"]
params["tpslMode"] = "Partial"          # non copre 100% posizione
params["tpOrderType"] = "Limit"
params["tpLimitPrice"] = payload["attached_take_profit"]
params["tpSize"] = payload["attached_take_profit_qty"]  # qty esplicita
```

`tpSize` esplicito previene il conflitto con TP intermedi `reduce_only`: Bybit applica l'attached TP solo sulla qty specificata, lasciando spazio ai TP separati.

### StatusMapper — CCXT status → RawAdapterOrder.status

| CCXT status | RawAdapterOrder.status |
|---|---|
| `open`, `partially_filled` | `OPEN` |
| `closed` | `FILLED` |
| `canceled`, `cancelled`, `expired` | `CANCELLED` |
| `rejected` | `FAILED` |

---

## §4 — CcxtBybitAdapter

### Inizializzazione

```python
class CcxtBybitAdapter(ExecutionAdapter):
    def __init__(self, api_key: str, api_secret: str, testnet: bool, connector: str):
        self._exchange = ccxt.bybit({
            "apiKey": api_key,
            "secret": api_secret,
            "options": {"defaultType": "linear"},  # USDT perpetual futures
        })
        if testnet:
            self._exchange.set_sandbox_mode(True)
        self._connector = connector
        self._builder = BybitOrderBuilder()
```

### Metodi

**`place_order`:**
1. `BybitOrderBuilder.build(command_type, payload, client_order_id)` → params
2. Se `command_type == "SYNC_PROTECTIVE_ORDERS"` → `AdapterResult(success=True)` diretto
3. Se action=cancel → `exchange.cancel_order(orderId, symbol)`
4. Altrimenti → `exchange.create_order(**params)`
5. Success → `AdapterResult(success=True, exchange_order_id=resp["id"])`
6. Exception → `AdapterResult(success=False, error=str(e))` → gateway applica retry

**`get_order_status`:**
```python
orders = exchange.fetch_orders(symbol, params={"orderLinkId": client_order_id})
if not orders:
    return None
return StatusMapper.map(orders[-1])  # più recente
```

**`cancel_order`:**
```python
orders = exchange.fetch_orders(symbol, params={"orderLinkId": client_order_id})
if orders:
    exchange.cancel_order(orders[-1]["id"], symbol)
```

**`set_leverage`:**
```python
exchange.set_leverage(leverage, symbol, params={
    "buyLeverage": str(leverage),
    "sellLeverage": str(leverage),
})
```

**`get_position_qty`:**
```python
positions = exchange.fetch_positions([symbol])
for pos in positions:
    if pos["side"].lower() == side.lower():
        return float(pos["contracts"] or 0.0)
return 0.0
```

### Capabilities

```python
AdapterCapabilities(
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
```

### Error handling

| Eccezione CCXT | Comportamento |
|---|---|
| `ccxt.NetworkError` | exception propagata → gateway retry con backoff |
| `ccxt.InvalidOrder` | `AdapterResult(success=False, reason="invalid_order")` → mark_failed |
| `ccxt.InsufficientFunds` | `AdapterResult(success=False, reason="insufficient_funds")` → mark_review_required |
| `ccxt.RateLimitExceeded` | exception propagata → gateway retry |
| altri `ccxt.BaseError` | `AdapterResult(success=False, error=str(e))` → retry |

---

## §5 — Config e credenziali

### AdapterConfig esteso (`models.py`)

```python
class AdapterConfig(BaseModel):
    type: str
    mode: str
    base_url: str = ""          # opzionale per CCXT (non usa HTTP diretto)
    connector: str
    leverage: int = 1
    secret: str | None = None   # Hummingbot Bearer token
    api_key: str | None = None  # CCXT: chiave pubblica, in YAML
    testnet: bool = False       # CCXT: usa sandbox Bybit
    # ... campi esistenti invariati
```

### execution.yaml — esempio

```yaml
adapters:
  bybit_main:
    type: ccxt_bybit
    mode: paper
    connector: bybit
    testnet: true
    api_key: "abc123"
    leverage: 10

  bybit_trader_b:
    type: ccxt_bybit
    mode: paper
    connector: bybit
    testnet: true
    api_key: "xyz789"
    leverage: 5

account_routing:
  default:
    adapter: bybit_main
    execution_account_id: bybit_main
  trader_b:
    adapter: bybit_trader_b
    execution_account_id: bybit_trader_b
```

### Credenziali — env vars

Pattern: `BYBIT_API_SECRET_<ADAPTER_NAME_UPPERCASE>`

```bash
BYBIT_API_SECRET_BYBIT_MAIN=...
BYBIT_API_SECRET_BYBIT_TRADER_B=...
```

### factory.py aggiornato

```python
elif cfg.type == "ccxt_bybit":
    api_secret = os.environ.get(f"BYBIT_API_SECRET_{adapter_name.upper()}")
    return CcxtBybitAdapter(
        api_key=cfg.api_key or "",
        api_secret=api_secret or "",
        testnet=cfg.testnet,
        connector=cfg.connector,
        capabilities=cfg.capabilities,
    )
```

---

## §6 — Account routing e multi-account

Il sistema già supporta (da Fase 0):
- **Single account**: tutti i trader → `account_id = "main"` → un adapter Bybit
- **Per-trader subaccount**: ogni trader ha proprio `account_id` in `config/traders/<trader_id>.yaml` → adapter Bybit separato con API key propria

Nessuna modifica al routing engine — `ExecutionConfig.resolve_routing(account_id)` funziona già.

---

## §7 — Testing strategy

### Unit test — `test_bybit_order_builder.py`

Nessun mock di rete. Testa solo `BybitOrderBuilder.build()` e `StatusMapper.map()`:

- `PLACE_ENTRY` limit → params: symbol, side, amount, price, orderLinkId, type=limit
- `PLACE_ENTRY` market → type=market, price=None
- `PLACE_PROTECTIVE_STOP` → reduceOnly=True, triggerPrice=stop_price
- `PLACE_TAKE_PROFIT` → reduceOnly=True, type=limit
- Mode C entry, tp_count=2 → tpslMode=Partial, tpSize=qty esplicita (OD-C1)
- Mode C entry, tp_count=1 → tpSize=total_qty
- `CANCEL_PENDING_ENTRY` → action=cancel, orderLinkId
- `SYNC_PROTECTIVE_ORDERS` → sentinel no-op
- StatusMapper: ogni status CCXT → status RawAdapterOrder corretto

### Gated integration test — `test_ccxt_bybit_adapter.py`

```python
@pytest.mark.bybit_testnet
# skippato automaticamente se BYBIT_TESTNET_API_KEY non in env
```

Scenari:
- `set_leverage` su BTC/USDT:USDT → non solleva
- `place_order(PLACE_ENTRY limit)` → ordine creato, `get_order_status` → OPEN
- `cancel_order` → status → CANCELLED
- `place_order(PLACE_PROTECTIVE_STOP)` → stop piazzato
- `get_position_qty` → float

### requirements.txt

```
ccxt>=4.0
```

---

## §8 — Open decisions

| ID | Decisione | Stato |
|---|---|---|
| OD-C1 | Mode C: `tpSize` esplicito via `tpslMode: Partial` | **Chiusa** — implementata in Fase 1 |
| OD-F1-1 | SYNC_PROTECTIVE_ORDERS: verifica passiva (no amend) | **Chiusa** — no-op per Fase 1, amend in Fase 2 |
| OD-F1-2 | `get_order_status` per ordini SL/TP attached: Bybit potrebbe non restituirli via `orderLinkId` standard | **Aperta** — verificare su testnet, eventuale fallback via `fetch_positions` |

---

## §9 — Fuori scope (Fase 2)

- Amend quantità SL dopo fill parziale (implementazione reale di SYNC_PROTECTIVE_ORDERS)
- WebSocket per fill real-time (oggi: REST polling)
- Altri exchange CCXT (OKX, Binance)
- Gestione hedge mode Bybit
