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

Crea l'adapter concreto dal campo `cfg.type`. Attualmente supporta `"hummingbot_api"`.
Inietta `cfg.capabilities` e risolve il secret da `cfg.secret` o dalla variabile d'ambiente `HUMMINGBOT_SECRET`.

Per aggiungere un nuovo tipo adapter: aggiungere un branch `if cfg.type == "nuovo_tipo"` qui.

## Adapter configurati in `config/execution.yaml`

| Nome | Tipo | Porta | Connector | Capabilities |
|---|---|---|---|---|
| `hummingbot_api_paper` | `hummingbot_api` | 8000 | `bybit_perpetual_testnet` | Full (stop nativo, TP nativo, move stop) |
| `hummingbot_api_demo` | `hummingbot_api` | 8001 | `bybit_perpetual_demo` | Solo entry + close (stop/TP non nativi) |

`default_adapter` corrente: `hummingbot_api_demo`.

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

