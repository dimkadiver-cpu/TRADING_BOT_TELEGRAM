# Comandi principali - TeleSignalBot Runtime V2

Questa guida descrive il flusso runtime attuale. Per approfondimenti tecnici
fare riferimento a `docs/runtime_v2/`.

## Flusso Runtime V2

```text
Telegram -> main.py
  -> raw_messages su db/parser.sqlite3
  -> parser_v2
  -> canonical_messages
  -> enriched_canonical_messages
  -> LifecycleGateWorker
  -> ops_trade_chains / ops_execution_commands su db/ops.sqlite3
  -> ExecutionGateway, solo se HUMMINGBOT_BASE_URL e' configurato
  -> Hummingbot API paper/testnet
  -> ops_exchange_events
  -> LifecycleEventWorker
```

Senza Hummingbot attivo il bot puo' comunque acquisire, parsare, arricchire e
generare comandi operativi. I comandi restano in `ops_execution_commands` finche'
il gateway non riesce a inviarli.

## Prerequisiti

Da `C:\TeleSignalBot`:

```powershell
# Verifica Python
python --version

# Verifica Docker, necessario solo per Hummingbot API
docker --version

# Verifica che la venv esista
Test-Path .\.venv\Scripts\python.exe
```

Se usi la venv esplicitamente:

```powershell
.\.venv\Scripts\python.exe -m pytest --version
```

## File da configurare

| File | Obbligatorio | Scopo |
|---|---:|---|
| `.env` | Si | Credenziali Telegram, path DB, opzioni gateway |
| `config/channels.yaml` | Si | Canali/topic Telegram attivi, trader, profilo parser, blacklist |
| `config/operation_config.yaml` | Si | Policy globali di enrichment, risk, account, trader abilitati |
| `config/traders/<trader_id>.yaml` | Si | Override per trader: risk, management, admission update |
| `config/execution.yaml` | Solo gateway | Adapter Hummingbot, routing account, connector, retry, safety |
| `docker-compose.yml` | Solo gateway | Hummingbot API, broker EMQX, Postgres, container Hummingbot |

### `.env`

Minimo per avviare il listener Telegram:

```text
TELEGRAM_API_ID=<id>
TELEGRAM_API_HASH=<hash>
TELEGRAM_SESSION=tele_signal_bot
PARSER_DB_PATH=C:\TeleSignalBot\db\parser.sqlite3
OPS_DB_PATH=C:\TeleSignalBot\db\ops.sqlite3
LOG_PATH=C:\TeleSignalBot\logs\bot.log
LOG_LEVEL=INFO
```

Per abilitare anche l'Execution Gateway:

```text
HUMMINGBOT_BASE_URL=http://localhost:8000
HUMMINGBOT_SECRET=admin:admin
```

Non stampare mai i valori reali delle chiavi Telegram, exchange o API.

### `config/channels.yaml`

Controlli principali:

- `recovery.max_hours`: finestra di recupero messaggi al restart.
- `blacklist_global`: frasi/tag da scartare.
- `channels[].active`: solo `true` viene processato.
- `channels[].chat_id` e `topic_id`: sorgente Telegram.
- `channels[].trader_id`: trader risolto.
- `channels[].parser_profile`: profilo parser_v2 da usare.

Il file viene riletto automaticamente da `main.py` senza restart.

### `config/execution.yaml`

Default locale previsto per paper/testnet:

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

Non impostare `mode: live` per i primi test. Il codice blocca comunque il live
trading MVP con `REVIEW_REQUIRED`.

## Primo avvio solo Runtime V2, senza Hummingbot

Usa questo quando vuoi testare intake, parser, enrichment e lifecycle senza
mandare ordini a Hummingbot.

```powershell
cd C:\TeleSignalBot
python main.py --migrate
python main.py
```

Log atteso:

```text
telegram listener started | parser_db=... | ops_db=...
HUMMINGBOT_BASE_URL not set - execution gateway disabled
listener worker started
```

Se `HUMMINGBOT_BASE_URL` e' presente in `.env`, il gateway prova ad avviarsi.

## Primo avvio completo con Hummingbot API

### 1. Avvia Docker Desktop

Verifica:

```powershell
docker ps
```

Se fallisce, Docker Desktop o il Linux engine non sono disponibili.

### 2. Avvia lo stack Hummingbot

```powershell
cd C:\TeleSignalBot
docker compose up -d
```

Servizi attesi:

```powershell
docker ps
```

Dovresti vedere almeno:

- `hummingbot`
- `hummingbot-backend-api`
- `hummingbot-broker`
- `hummingbot-postgres`

Verifica API:

```powershell
Invoke-WebRequest -Uri http://localhost:8000/docs -UseBasicParsing -TimeoutSec 10
```

`/health` puo' non esistere: usare `/docs` o `/openapi.json`.

### 3. Configura Hummingbot/connector

Il connector previsto e':

```text
bybit_perpetual_testnet
```

Apri la console gia' avviata nel container:

```powershell
docker attach hummingbot
```

Se la schermata resta vuota, premi `Enter`.

Per staccarti dalla console senza fermare il container:

```text
Ctrl+P poi Ctrl+Q
```

Dentro la console Hummingbot configura il connector:

```text
connect bybit_perpetual_testnet
```

Inserisci API key e secret Bybit testnet quando richiesti. Le chiavi testnet
non vanno scritte nei documenti o nei log.

Non avviare manualmente `bin/hummingbot.py` da una shell `docker exec`: il
container ha gia' il processo console come entrypoint.

### 4. Abilita gateway in `.env`

```text
HUMMINGBOT_BASE_URL=http://localhost:8000
HUMMINGBOT_SECRET=admin:admin
```

### 5. Applica migrazioni e avvia il bot

```powershell
cd C:\TeleSignalBot
python main.py --migrate
python main.py
```

Log atteso:

```text
execution gateway started | adapter=hummingbot_api_paper | url=http://localhost:8000 | account=master_account
telegram listener started | parser_db=... | ops_db=...
listener worker started
```

## Monitoraggio live

Log del bot:

```powershell
Get-Content logs\bot.log -Wait -Tail 80
```

Log Hummingbot API:

```powershell
docker logs hummingbot-backend-api --tail 100
```

Log broker:

```powershell
docker logs hummingbot-broker --tail 100
```

Stato container:

```powershell
docker ps
```

## Controllo DB Runtime V2

Usa questi comandi per capire dove si e' fermato il flusso.

Conteggi parser DB:

```powershell
python -c "import sqlite3; c=sqlite3.connect('db/parser.sqlite3'); [print(t, c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]) for t in ['raw_messages','canonical_messages','enriched_canonical_messages']]; c.close()"
```

Ultimi enrichment:

```powershell
python -c "import sqlite3; c=sqlite3.connect('db/parser.sqlite3'); [print(r) for r in c.execute('SELECT enrichment_id, canonical_message_id, raw_message_id, trader_id, primary_class, enrichment_decision, lifecycle_processed, reason_code FROM enriched_canonical_messages ORDER BY enrichment_id DESC LIMIT 10')]; c.close()"
```

Conteggi ops DB:

```powershell
python -c "import sqlite3; c=sqlite3.connect('db/ops.sqlite3'); [print(t, c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]) for t in ['ops_trade_chains','ops_lifecycle_events','ops_execution_commands','ops_exchange_events']]; c.close()"
```

Ultimi comandi execution:

```powershell
python -c "import sqlite3; c=sqlite3.connect('db/ops.sqlite3'); [print(r) for r in c.execute('SELECT command_id, trade_chain_id, command_type, status, retry_count, client_order_id, next_retry_at FROM ops_execution_commands ORDER BY command_id DESC LIMIT 20')]; c.close()"
```

Lettura rapida degli stati:

| Stato | Significato |
|---|---|
| `enriched_canonical_messages.lifecycle_processed=0` | Pronto per lifecycle worker |
| `ops_execution_commands.status=PENDING` | Comando pronto per gateway |
| `SENT` o `ACK` | Comando inviato/riconosciuto |
| `WAITING_POSITION` | Comando in attesa che la chain diventi `OPEN` |
| `RETRY` / `next_retry_at` valorizzato | Invio fallito, ritentera' |
| `FAILED` | Errore terminale |
| `REVIEW_REQUIRED` | Serve intervento manuale |

## Test

Suite Runtime V2 completa:

```powershell
pytest tests\runtime_v2 -q --tb=short
```

Lifecycle + execution gateway:

```powershell
pytest tests\runtime_v2\lifecycle tests\runtime_v2\execution_gateway -q --tb=short
```

Solo gateway:

```powershell
pytest tests\runtime_v2\execution_gateway -q --tb=short
```

Test gated contro Hummingbot API reale:

```powershell
$env:RUN_HUMMINGBOT_API_TESTS="1"
$env:HUMMINGBOT_API_URL="http://localhost:8000"
$env:HUMMINGBOT_CONNECTOR="bybit_perpetual_testnet"
$env:HUMMINGBOT_ACCOUNT="master_account"
$env:HUMMINGBOT_SECRET="admin:admin"
pytest tests\runtime_v2\execution_gateway\test_hummingbot_adapter.py -v
```

Nota: `test_api_reachable` e `test_capabilities_declared` non piazzano ordini.
`test_place_and_query_order` richiede account/connector realmente funzionanti.

## Reset DB per test pulito

Operazione distruttiva. Fare sempre backup prima.

```powershell
Copy-Item db\parser.sqlite3 db\parser.backup.sqlite3
Copy-Item db\ops.sqlite3 db\ops.backup.sqlite3
```

Poi, solo se vuoi davvero ripartire da DB vuoti:

```powershell
Remove-Item db\parser.sqlite3
Remove-Item db\ops.sqlite3
python main.py --migrate
```

## Troubleshooting rapido

| Sintomo | Verifica | Azione |
|---|---|---|
| Nessun messaggio entra | `config/channels.yaml`, `active: true`, `chat_id`, `topic_id` | Attiva il canale corretto e guarda `logs/bot.log` |
| Messaggi in `raw_messages` ma non in `canonical_messages` | Log `parse failed` | Controlla `parser_profile` e profilo in `src/parser_v2/profiles/` |
| Enrichment `BLOCK` o `REVIEW` | `reason_code` in `enriched_canonical_messages` | Controlla `operation_config.yaml` e `config/traders/*.yaml` |
| `ops_execution_commands` resta `PENDING` | Log gateway e `HUMMINGBOT_BASE_URL` | Avvia Hummingbot API o configura `.env` |
| `execution gateway disabled` | `.env` | Aggiungi `HUMMINGBOT_BASE_URL` e riavvia `main.py` |
| Docker non risponde | `docker ps` | Avvia Docker Desktop/Linux engine |
| API Hummingbot 401 | `HUMMINGBOT_SECRET` | Verifica credenziali, senza stamparle nei log |
| API Hummingbot 404 su `/health` | Endpoint assente | Usa `/docs` o `/openapi.json` |
| Ordine va in `REVIEW_REQUIRED` | `result_payload_json` su comando | Controlla capability, live safety, connector, account |

## Sequenza consigliata per il primo test end-to-end

1. Controlla `.env`, `channels.yaml`, `operation_config.yaml`, `config/traders/trader_a.yaml`, `execution.yaml`.
2. Avvia Docker Desktop.
3. Esegui `docker compose up -d`.
4. Esegui `docker attach hummingbot`.
5. Configura `connect bybit_perpetual_testnet`.
6. Staccati con `Ctrl+P` poi `Ctrl+Q`.
7. Verifica `http://localhost:8000/docs`.
8. Esegui `python main.py --migrate`.
9. Esegui `python main.py`.
10. Invia o attendi un messaggio Telegram su un canale `active: true`.
11. Guarda `logs\bot.log`.
12. Controlla `raw_messages`, `canonical_messages`, `enriched_canonical_messages`.
13. Controlla `ops_trade_chains`, `ops_execution_commands`, `ops_exchange_events`.
14. Se Hummingbot e' configurato, verifica che i comandi passino da `PENDING` a `SENT`/`ACK`/`DONE` oppure a uno stato diagnostico.
