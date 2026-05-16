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
- `connector: bybit_perpetual_testnet`

Questi nomi riflettono la Hummingbot API locale verificata con `hummingbot/hummingbot-api`.

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

### `adapters/hummingbot_api_paper.py`

Adapter HTTP per Hummingbot API.

Supporta:

- Bearer auth quando `secret` è un token semplice;
- Basic auth quando `secret` è nel formato `username:password`;
- API corrente `hummingbot/hummingbot-api` con `/docs` e HTTP Basic default `admin:admin`.

## Invarianti

- Il gateway non crea decisioni operative nuove: esegue comandi già prodotti dal lifecycle.
- Il lifecycle non importa Hummingbot né exchange SDK.
- Ogni ordine inviato usa un `client_order_id` deterministico per correlare retry, query e fill.
- `HUMMINGBOT_BASE_URL` è il gate operativo: senza questa variabile, l'execution gateway resta disabilitato.
- `allow_live_trading=false` blocca modalità live anche se l'adapter è configurato.

