# execution_gateway — Istruzioni d'uso

## Configurazione

Il gateway legge `config/execution.yaml`.

Default locale verificato:

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
```

## Variabili ambiente

```bash
HUMMINGBOT_BASE_URL=http://localhost:8000
HUMMINGBOT_SECRET=admin:admin
```

- `HUMMINGBOT_BASE_URL` abilita il wiring PRD 05 in `main.py`.
- `HUMMINGBOT_SECRET` può essere un token Bearer o una coppia Basic `username:password`.
- Per l'immagine corrente `hummingbot/hummingbot-api`, il default locale verificato è `admin:admin`.

Senza `HUMMINGBOT_BASE_URL`, `main.py` avvia il runtime e i worker lifecycle ma lascia disabilitato l'Execution Gateway.

## Avvio Hummingbot API

Usa il compose del repository:

```bash
docker compose up -d
```

Servizi attesi:

- `hummingbot`
- `hummingbot-backend-api`
- `hummingbot-broker` (EMQX)
- `hummingbot-postgres`

Verifica reachability:

```bash
curl -I http://localhost:8000/docs
```

L'API corrente non espone sempre `/health`; usare `/docs` o `/openapi.json` come smoke check.

Per la guida completa: `docs/runtime_v2/execution_gateway/hummingbot_setup.md`.

## Avvio bot

```bash
python main.py --migrate
python main.py
```

Log atteso quando il gateway è abilitato:

```text
execution gateway started | adapter=hummingbot_api_paper | url=http://localhost:8000 | account=master_account
```

Log atteso quando manca `HUMMINGBOT_BASE_URL`:

```text
execution gateway disabled
```

## Test locali

Suite del package:

```bash
pytest tests/runtime_v2/execution_gateway -v --tb=short
```

Solo auth:

```bash
pytest tests/runtime_v2/execution_gateway/test_auth.py -v
```

Suite runtime:

```bash
pytest tests/runtime_v2 -v --tb=short
```

## Test gated contro Hummingbot API

Con API attiva:

```bash
RUN_HUMMINGBOT_API_TESTS=1 \
HUMMINGBOT_API_URL=http://localhost:8000 \
HUMMINGBOT_SECRET=admin:admin \
pytest tests/runtime_v2/execution_gateway/test_hummingbot_adapter.py -v
```

Su Windows PowerShell:

```powershell
$env:RUN_HUMMINGBOT_API_TESTS="1"
$env:HUMMINGBOT_API_URL="http://localhost:8000"
$env:HUMMINGBOT_SECRET="admin:admin"
pytest tests\runtime_v2\execution_gateway\test_hummingbot_adapter.py -v
```

Nota operativa: `test_api_reachable` e `test_capabilities_declared` validano l'integrazione API senza piazzare ordini. `test_place_and_query_order` richiede account/connector exchange funzionanti e può fallire per problemi interni Hummingbot/API o certificati verso provider esterni.

## Troubleshooting rapido

| Sintomo | Causa probabile | Azione |
|---|---|---|
| `/health` ritorna 404 | Versione API corrente senza health endpoint | Usa `/docs` o `/openapi.json` |
| `401 Unauthorized` | Secret errato | Usa `HUMMINGBOT_SECRET=admin:admin` o credenziali configurate |
| Backend API non parte con `docker.from_env()` | Docker socket non montato | Usa `docker compose up -d` con il compose del repo |
| MQTT connection refused | EMQX assente/non pronto | Verifica container `hummingbot-broker` |
| `bybit_perpetual_paper_trade` non trovato | Nome connector vecchio | Usa `bybit_perpetual_testnet` |
| `place_order` 500 con SSL/certificati | Errore interno Hummingbot API/rate oracle/provider esterno | Verificare log `docker logs hummingbot-backend-api` e rete/certificati container |

