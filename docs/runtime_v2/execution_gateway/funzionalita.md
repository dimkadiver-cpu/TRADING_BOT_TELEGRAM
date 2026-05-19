# execution_gateway — Funzionalità

## Responsabilità

Il package `execution_gateway` è il layer PRD 05 che consuma i comandi neutrali prodotti dal lifecycle (`ops_execution_commands`) e li invia a un adapter di esecuzione concreto.

Il lifecycle decide cosa va fatto. L'Execution Gateway decide come tradurre il comando verso l'exchange/Hummingbot, applicando safety, capabilities, retry e correlazione idempotente.

## Componenti

### `models.py`

- **`ExecutionConfig`** — configurazione caricata da `config/execution.yaml`: adapter di default, routing account, adapter concreti.
- **`AdapterConfig`** — configurazione adapter: `type`, `mode`, `base_url`, `connector`, `leverage`, `secret`, `entry_execution`, `retry`, `capabilities`, `take_profit`, `position_management`, `live_safety`.
- **`AdapterCapabilities`** — dichiara cosa può fare l'adapter: entry, stop nativo, TP nativo, move stop, close partial/full, executor_position.
- **`AdapterResult`** — risultato normalizzato di un invio ordine/cancel.
- **`RawAdapterOrder`** — stato ordine letto dall'adapter e normalizzato per il sync fill.

### `config_loader.py`

Carica e valida `config/execution.yaml` con Pydantic. Il routing default corrente punta a:

- `execution_account_id: master_account`
- `connector: bybit_perpetual_demo` (stack demo su porta 8001)

Per paper/testnet: `connector: bybit_perpetual_testnet` su porta 8000.

### `gateway.py`

`ExecutionGateway` prende un `ExecutionCommand` e:

1. risolve routing e adapter;
2. applica live safety;
3. verifica le capabilities richieste dal comando;
4. genera `client_order_id` deterministico;
5. chiama l'adapter;
6. marca il comando come `SENT`, `DONE`, `RETRY`, `REVIEW_REQUIRED` o `WAITING_POSITION`.

### `command_worker.py`

`ExecutionCommandWorker.run_once()` processa:

- comandi `PENDING`;
- comandi in retry con `next_retry_at` scaduto;
- comandi `WAITING_POSITION` quando la chain è diventata `OPEN`.

Il worker è istanziato da `main.py` solo quando `HUMMINGBOT_BASE_URL` è configurato.

### `event_sync.py`

`ExchangeEventSyncWorker.run_once()` legge comandi inviati con `client_order_id`, interroga l'adapter e, se l'ordine risulta filled, inserisce un evento normalizzato in `ops_exchange_events`:

- `ENTRY_FILLED`
- `TP_FILLED`
- `SL_FILLED`

Il `LifecycleEventWorker` consuma questi eventi al giro successivo.

### `adapters/hummingbot_api.py`

Adapter HTTP neutro per Hummingbot API. Sostituisce `HummingbotApiPaperAdapter`.

Differenze rispetto al precedente:

- Riceve `capabilities: AdapterCapabilities | None` al costruttore — non hardcoda più le capabilities.
- Se `capabilities=None` usa i default (`protective_stop_native=True`, `take_profit_native=True`, ecc.).
- Supporta Bearer auth (token semplice) e Basic auth (`username:password`).
- API corrente `hummingbot/hummingbot-api`: HTTP Basic default `admin:admin`.

### `adapters/hummingbot_api_paper.py`

Alias retrocompatibile:

```python
from src.runtime_v2.execution_gateway.adapters.hummingbot_api import HummingbotApiAdapter
HummingbotApiPaperAdapter = HummingbotApiAdapter
```

Nessuna logica propria. Mantenuto per compatibilità con codice e test esistenti.

### `adapters/factory.py`

`build_adapter(adapter_name, cfg: AdapterConfig) -> ExecutionAdapter`

Crea l'adapter concreto dal campo `cfg.type`. Supporta `"hummingbot_api"` e `"ccxt_bybit"`.

- Per `hummingbot_api`: inietta `cfg.capabilities` e risolve il secret da `cfg.secret` o `HUMMINGBOT_SECRET`.
- Per `ccxt_bybit`: legge `cfg.api_key` dallo YAML e `api_secret` dall'env `BYBIT_API_SECRET_<ADAPTER_NAME_UPPERCASE>`.

### `adapters/ccxt_bybit/`

Adapter CCXT per Bybit perpetuals USDT (Fase 1). Composto da tre moduli:

**`adapter.py` — `CcxtBybitAdapter`**

Implementa `ExecutionAdapter` via `ccxt.bybit`. Usa `options.defaultType=linear` (USDT perpetual). In testnet: `set_sandbox_mode(True)`. Il campo `_exchange` è iniettabile per unit test.

Metodi:
- `place_order` — delega a `BybitOrderBuilder`, poi chiama CCXT (`create_order` / `cancel_order` / `edit_order`). Gestisce `noop` (SYNC_PROTECTIVE_ORDERS), `cancel_by_link`, `edit_sl`.
- `get_order_status` — `fetch_open_orders` poi `fetch_closed_orders` per `orderLinkId`; restituisce `RawAdapterOrder | None`.
- `cancel_order` — no-op (la cancellazione passa via `CANCEL_PENDING_ENTRY` in `place_order`).
- `set_leverage` — chiama `set_leverage` con `buyLeverage`/`sellLeverage` come stringa.
- `get_position_qty` — `fetch_positions` + filtra per `side`; restituisce `float | None`.

Error handling:
| Eccezione | Comportamento |
|---|---|
| `ccxt.NetworkError`, `ccxt.RateLimitExceeded` | propagata → gateway retry |
| `ccxt.InvalidOrder` | `AdapterResult(success=False, reason="invalid_order")` |
| `ccxt.InsufficientFunds` | `AdapterResult(success=False, reason="insufficient_funds")` |
| altri `ccxt.BaseError` | `AdapterResult(success=False, error=...)` → retry |

**`order_builder.py` — `BybitOrderBuilder`**

Traduce `command_type + payload + client_order_id → BybitOrderParams`. Puro, nessuna chiamata di rete.

| command_type | action | Note |
|---|---|---|
| `PLACE_ENTRY` | `create_order` | `orderLinkId=client_order_id`; Mode C se `native_attached_tpsl=True` |
| `PLACE_PROTECTIVE_STOP` | `create_order` | `reduceOnly=True`, `triggerPrice` |
| `PLACE_TAKE_PROFIT` | `create_order` | `reduceOnly=True`, type=limit |
| `CANCEL_PENDING_ENTRY` | `cancel_by_link` | cerca ordine aperto per `orderLinkId` |
| `CLOSE_PARTIAL`, `CLOSE_FULL` | `create_order` | type=market, `reduceOnly=True` |
| `MOVE_STOP_TO_BREAKEVEN`, `MOVE_STOP` | `edit_sl` | amend triggerPrice ordine SL aperto |
| `SYNC_PROTECTIVE_ORDERS` | `noop` | no-op, Fase 2 |

Mode C (`native_attached_tpsl=True`): aggiunge `takeProfit`, `stopLoss`, `tpslMode=Partial`, `tpOrderType=Limit`, `tpLimitPrice`, `tpSize` esplicito (risolve OD-C1).

**`status_mapper.py` — `StatusMapper`**

Mappa `ccxt_order["status"]` → `RawAdapterOrder.status`:

| CCXT | RawAdapterOrder |
|---|---|
| `open`, `partially_filled` | `OPEN` |
| `closed` | `FILLED` |
| `canceled`, `cancelled`, `expired` | `CANCELLED` |
| `rejected` | `FAILED` |

## Adapter configurati in `config/execution.yaml`

| Nome | Tipo | Endpoint | Connector | Capabilities |
|---|---|---|---|---|
| `hummingbot_api_paper` | `hummingbot_api` | porta 8000 | `bybit_perpetual_testnet` | Full (stop nativo, TP nativo, move stop) |
| `hummingbot_api_demo` | `hummingbot_api` | porta 8001 | `bybit_perpetual_demo` | Solo entry + close (stop/TP non nativi) |
| `bybit_main` *(esempio)* | `ccxt_bybit` | CCXT direct | `bybit` | Full (stop nativo, TP nativo, move stop, Mode C) |

`default_adapter` corrente: `hummingbot_api_demo`. L'esempio `ccxt_bybit` è in `execution.yaml` come sezione commentata.

## Stack Docker demo

Il connector `bybit_perpetual_demo` non esiste nell'immagine stock di Hummingbot.
Viene aggiunto tramite volume mount read-only:

```
hummingbot_demo_patch/bybit_perpetual_constants.py
  → /home/hummingbot/hummingbot/connector/derivative/bybit_perpetual/bybit_perpetual_constants.py
```

Il file patch aggiunge `bybit_perpetual_demo → https://api-demo.bybit.com/` a tutti i dict URL.

Lo stack è definito in `docker-compose.demo.yml` con:
- 4 servizi su rete isolata `hummingbot-demo-net`
- Porta `8001` (backend-api demo)
- Volumi separati dallo stack principale

Per avviare: `docker compose -f docker-compose.demo.yml --env-file .env.demo up -d`

## Invarianti

- Il gateway non crea decisioni operative nuove: esegue comandi già prodotti dal lifecycle.
- Il lifecycle non importa Hummingbot né exchange SDK.
- Ogni ordine inviato usa un `client_order_id` deterministico per correlare retry, query e fill.
- `HUMMINGBOT_BASE_URL` è il gate operativo: senza questa variabile, l'execution gateway resta disabilitato.
- `allow_live_trading=false` blocca modalità live anche se l'adapter è configurato.
- La modalità `live` richiede anche `TSB_ALLOW_LIVE_TRADING=YES_I_UNDERSTAND` come secondo gate in env.

