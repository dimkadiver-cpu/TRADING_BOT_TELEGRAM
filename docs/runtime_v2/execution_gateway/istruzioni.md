# execution_gateway — Istruzioni d'uso

## Configurazione

Il gateway legge `config/execution.yaml`.

### Stack demo (default corrente)

```yaml
execution:
  default_adapter: hummingbot_api_demo

  account_routing:
    default:
      adapter: hummingbot_api_demo
      execution_account_id: master_account

  adapters:
    hummingbot_api_demo:
      type: hummingbot_api
      mode: demo
      base_url: http://localhost:8001
      connector: bybit_perpetual_demo
      leverage: 1
      capabilities:
        place_entry: true
        protective_stop_native: false
        take_profit_native: false
        move_stop: false
        close_partial: true
        close_full: true
      live_safety:
        allow_live_trading: false
```

### Stack paper/testnet (fallback)

```yaml
execution:
  default_adapter: hummingbot_api_paper

  account_routing:
    default:
      adapter: hummingbot_api_paper
      execution_account_id: master_account

  adapters:
    hummingbot_api_paper:
      type: hummingbot_api
      mode: paper
      base_url: http://localhost:8000
      connector: bybit_perpetual_testnet
      leverage: 1
      live_safety:
        allow_live_trading: false
```

Per tornare a paper/testnet: cambia `default_adapter` e `account_routing.default.adapter` a `hummingbot_api_paper`.

### Stack ccxt_bybit / Bybit testnet

Sostituisce il blocco `execution:` in `execution.yaml` (l'esempio commentato è già nel file):

```yaml
execution:
  default_adapter: bybit_main

  account_routing:
    default:
      adapter: bybit_main
      execution_account_id: bybit_main

  adapters:
    bybit_main:
      type: ccxt_bybit
      mode: paper
      connector: bybit
      testnet: true
      api_key: "abc123"
      leverage: 10
      hedge_mode: false
      websocket:
        enabled: true
        reconnect_backoff_sec: 5
```

Credenziali: il secret **non va in YAML** — si passa via env:

```bash
BYBIT_API_SECRET_BYBIT_MAIN=<tua_api_secret>
```

Pattern env: `BYBIT_API_SECRET_<ADAPTER_NAME_UPPERCASE>`.
Multi-account: aggiungere un secondo adapter e definire `BYBIT_API_SECRET_BYBIT_TRADER_B`, ecc.

Note operative:
- `hedge_mode=true` va usato solo se l'account Bybit è realmente in hedge mode.
- `websocket.enabled=true` richiede `ccxt.pro` disponibile nell'ambiente.

## Variabili ambiente

Per lo stack demo:

```bash
HUMMINGBOT_BASE_URL=http://localhost:8001
HUMMINGBOT_SECRET=admin:admin
```

Per lo stack paper/testnet:

```bash
HUMMINGBOT_BASE_URL=http://localhost:8000
HUMMINGBOT_SECRET=admin:admin
```

- `HUMMINGBOT_BASE_URL` abilita il wiring PRD 05 in `main.py`.
- `HUMMINGBOT_SECRET` può essere un token Bearer o una coppia Basic `username:password`.
- Per entrambe le immagini `hummingbot/hummingbot-api`, il default HTTP Basic è `admin:admin`.
- `TSB_ALLOW_LIVE_TRADING=YES_I_UNDERSTAND` è il secondo gate richiesto se `mode=live`.

Senza `HUMMINGBOT_BASE_URL`, `main.py` avvia il runtime e i worker lifecycle ma lascia disabilitato l'Execution Gateway.

## Avvio stack demo (porta 8001)

```powershell
cd C:\TeleSignalBot
docker compose -f docker-compose.demo.yml --env-file .env.demo up -d
```

Verifica reachability:

```powershell
Invoke-WebRequest -Uri http://localhost:8001/docs -UseBasicParsing -TimeoutSec 5
```

Per la guida completa (configurazione API key, connector, test): `COMANDI_DEMO.md`.

## Avvio stack paper/testnet (porta 8000)

```powershell
docker compose up -d
```

Servizi attesi: `hummingbot`, `hummingbot-backend-api`, `hummingbot-broker` (EMQX), `hummingbot-postgres`.

```powershell
Invoke-WebRequest -Uri http://localhost:8000/docs -UseBasicParsing -TimeoutSec 5
```

Per la guida completa: `docs/runtime_v2/execution_gateway/hummingbot_setup.md`.

## Avvio bot

```powershell
python main.py --migrate
python main.py
```

Log atteso quando il gateway è abilitato:

```text
execution gateway started | adapter=hummingbot_api_demo | url=http://localhost:8001 | account=master_account
```

Log atteso quando manca `HUMMINGBOT_BASE_URL`:

```text
execution gateway disabled
```

## Test locali (senza stack attivo)

Suite del package:

```powershell
.venv\Scripts\python.exe -m pytest tests\runtime_v2\execution_gateway -q --tb=short
```

Suite runtime completa:

```powershell
.venv\Scripts\python.exe -m pytest tests\runtime_v2 -q --tb=short
```

Suite mirate utili per `ccxt_bybit`:

```powershell
.venv\Scripts\python.exe -m pytest tests\runtime_v2\execution_gateway\test_adapter_config_ccxt.py -q
.venv\Scripts\python.exe -m pytest tests\runtime_v2\execution_gateway\test_bybit_order_builder.py -q
.venv\Scripts\python.exe -m pytest tests\runtime_v2\execution_gateway\test_ccxt_bybit_adapter_unit.py -q
.venv\Scripts\python.exe -m pytest tests\runtime_v2\execution_gateway\test_bybit_ws_fill_watcher.py -q
.venv\Scripts\python.exe -m pytest tests\runtime_v2\execution_gateway\test_event_sync.py -q
.venv\Scripts\python.exe -m pytest tests\runtime_v2\execution_gateway\test_adapter_factory.py -q
```

## Test gated stack paper/testnet (porta 8000)

```powershell
$env:RUN_HUMMINGBOT_API_TESTS="1"
$env:HUMMINGBOT_API_URL="http://localhost:8000"
$env:HUMMINGBOT_CONNECTOR="bybit_perpetual_testnet"
$env:HUMMINGBOT_ACCOUNT="master_account"
$env:HUMMINGBOT_SECRET="admin:admin"
pytest tests\runtime_v2\execution_gateway\test_hummingbot_adapter.py -v
```

`test_api_reachable` e `test_capabilities_declared` non piazzano ordini.
`test_place_and_query_order` richiede account/connector funzionanti.

## Test gated stack demo (porta 8001)

Infrastructure-only (senza API key Bybit):

```powershell
$env:RUN_HUMMINGBOT_DEMO_TESTS="1"
$env:HUMMINGBOT_DEMO_API_URL="http://localhost:8001"
pytest tests\runtime_v2\execution_gateway\test_hummingbot_demo_gated.py::test_01_api_reachable `
       tests\runtime_v2\execution_gateway\test_hummingbot_demo_gated.py::test_02_connector_available -v
```

Suite completa (richiede API key Bybit Demo configurate in Hummingbot):

```powershell
$env:RUN_HUMMINGBOT_DEMO_TESTS="1"
$env:HUMMINGBOT_DEMO_API_URL="http://localhost:8001"
$env:HUMMINGBOT_DEMO_CONNECTOR="bybit_perpetual_demo"
$env:HUMMINGBOT_DEMO_ACCOUNT="master_account"
pytest tests\runtime_v2\execution_gateway\test_hummingbot_demo_gated.py -v -s
```

## Test gated Bybit testnet (ccxt_bybit)

Unit test (nessuna chiamata di rete):

```powershell
.venv\Scripts\python.exe -m pytest tests\runtime_v2\execution_gateway\test_bybit_order_builder.py `
       tests\runtime_v2\execution_gateway\test_bybit_status_mapper.py `
       tests\runtime_v2\execution_gateway\test_ccxt_bybit_adapter_unit.py `
       tests\runtime_v2\execution_gateway\test_bybit_ws_fill_watcher.py `
       tests\runtime_v2\execution_gateway\test_event_sync.py `
       tests\runtime_v2\execution_gateway\test_adapter_factory.py -v
```

Integration test gated (richiede account Bybit testnet reale):

```powershell
$env:BYBIT_TESTNET_API_KEY="<tua_api_key>"
$env:BYBIT_API_SECRET_BYBIT_TESTNET="<tua_api_secret>"
pytest tests\runtime_v2\execution_gateway\test_ccxt_bybit_gated.py -v -s -m bybit_testnet
```

Prerequisiti account testnet: Unified Trading Account, USDT perpetual abilitato, almeno 10 USDT testnet.

## Troubleshooting rapido

| Sintomo | Causa probabile | Azione |
|---|---|---|
| `/health` ritorna 404 | Endpoint assente nell'immagine corrente | Usa `/docs` o `/openapi.json` |
| `401 Unauthorized` | Secret errato | Usa `HUMMINGBOT_SECRET=admin:admin` o credenziali configurate |
| `detail: Incorrect username or password` | Username/password HTTP Basic sbagliati | Default integrato: `admin` / `admin` |
| Backend API non parte (`docker.from_env()`) | Docker socket non montato | Usa `docker compose` che monta `/var/run/docker.sock` |
| `hummingbot-demo` in Restarting | Container console non configurato | Normale finché non si esegue `COMANDI_DEMO.md` Fase 5 — non blocca il backend-api |
| Porta 8001 non risponde | Stack demo non avviato | `docker compose -f docker-compose.demo.yml --env-file .env.demo up -d` |
| `bybit_perpetual_demo` non trovato dall'adapter | Patch connector non caricata | Verifica volume mount con `docker exec hummingbot-demo grep bybit_perpetual_demo /home/hummingbot/...` |
| Patch connector assente in `hummingbot_demo_patch/` | File non estratto | Seguire Passo 3 di `COMANDI_DEMO.md` (estrazione da container) |
| `bybit_perpetual_paper_trade` non trovato | Nome connector vecchio | Usa `bybit_perpetual_testnet` per lo stack paper |
| `place_order` 500 con SSL/certificati | Errore interno API/rate oracle | Verificare `docker logs hummingbot-demo-backend-api` |
| `ccxt_bybit`: `AuthenticationError` | API key/secret errati o permessi mancanti | Verifica env vars `BYBIT_API_SECRET_*` e permessi account Bybit |
| `ccxt_bybit`: `sl_order_not_found` su MOVE_STOP | Nessun ordine SL aperto trovato per il symbol | Verificare che l'ordine SL sia ancora aperto su Bybit testnet |
| `ccxt_bybit`: watcher WS non parte | `ccxt.pro` non installato o `websocket.enabled=false` | Verifica `requirements.txt`, `python -c "import ccxt.pro"` e config adapter |
| `ccxt_bybit`: stato `sl/tp` attached non visibile via `orderLinkId` | Limite Bybit/CCXT sugli ordini attached Mode C | Il fallback repository + `fetch_positions` copre il caso quando il repo è disponibile |
