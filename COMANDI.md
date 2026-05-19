# Comandi principali - TeleSignalBot Runtime V2

Questa guida descrive il runtime reale attuale del progetto.
Per approfondimenti tecnici vedere `docs/runtime_v2/` e
`docs/Raggionamento/CCXT/AUDIT_stato_migrazione_ccxt.md`.

## Flusso Runtime V2

```text
Telegram -> main.py
  -> raw_messages su db/parser.sqlite3
  -> parser_v2
  -> canonical_messages
  -> enriched_canonical_messages
  -> LifecycleGateWorker
  -> ops_trade_chains / ops_execution_commands su db/ops.sqlite3
  -> ExecutionGateway (CCXT Bybit, se bootstrap gateway valido)
  -> ops_exchange_events
  -> LifecycleEventWorker

Opzionale:
  -> BybitWsFillWatcher, se execution.yaml ha websocket.enabled: true
  -> reconciliation periodica, se poll_fallback_enabled: true
```

Il bot puo' acquisire, parsare, arricchire e generare comandi operativi anche
senza credenziali exchange valide. In quel caso i comandi restano in
`ops_execution_commands` oppure vanno in stati diagnostici del gateway.

## Prerequisiti

Da `C:\TeleSignalBot`:

```powershell
python --version
Test-Path .\.venv\Scripts\python.exe
.\.venv\Scripts\python.exe -m pytest --version
```

Docker non e' richiesto per il runtime CCXT corrente.

## File da configurare

| File | Obbligatorio | Scopo |
|---|---:|---|
| `.env` | Si | Credenziali Telegram, path DB, secret gateway |
| `config/channels.yaml` | Si | Canali/topic Telegram attivi, trader, parser profile |
| `config/operation_config.yaml` | Si | Policy enrichment, risk, account, trader |
| `config/traders/<trader_id>.yaml` | Si | Override trader-specific |
| `config/execution.yaml` | Solo gateway | Adapter CCXT, routing account, retry, websocket, safety |

## `.env`

Minimo per avviare listener + runtime:

```text
TELEGRAM_API_ID=<id>
TELEGRAM_API_HASH=<hash>
TELEGRAM_SESSION=tele_signal_bot
PARSER_DB_PATH=C:\TeleSignalBot\db\parser.sqlite3
OPS_DB_PATH=C:\TeleSignalBot\db\ops.sqlite3
LOG_PATH=C:\TeleSignalBot\logs\bot.log
LOG_LEVEL=INFO
```

Per l'adapter CCXT Bybit demo configurato di default:

```text
BYBIT_API_SECRET_BYBIT_DEMO=<secret>
```

Note:

- la `api_key` oggi viene letta da `config/execution.yaml`
- il `secret` viene letto da env come `BYBIT_API_SECRET_<ADAPTER_NAME_UPPER>`
- per `mode: live` serve anche:

```text
TSB_ALLOW_LIVE_TRADING=YES_I_UNDERSTAND
```

Non stampare mai chiavi Telegram o exchange nei log o nei report.

## `config/channels.yaml`

Controlli principali:

- `recovery.max_hours`: finestra di recupero messaggi al restart
- `blacklist_global`: frasi/tag da scartare
- `channels[].active`: solo `true` viene processato
- `channels[].chat_id` e `topic_id`: sorgente Telegram
- `channels[].trader_id`: trader risolto
- `channels[].parser_profile`: profilo parser_v2 da usare

Il file viene riletto automaticamente da `main.py` senza restart.

## `config/execution.yaml`

Default locale attuale:

```yaml
execution:
  default_adapter: bybit_demo
  account_routing:
    default:
      adapter: bybit_demo
      execution_account_id: master_account
  adapters:
    bybit_demo:
      type: ccxt_bybit
      mode: demo
      connector: bybit
      testnet: true
      api_key: ""
      leverage: 10
      hedge_mode: false
      websocket:
        enabled: false
        poll_fallback_enabled: true
        poll_fallback_period_seconds: 60
      live_safety:
        allow_live_trading: false
```

Lettura pratica:

- `default_adapter: bybit_demo`: bootstrap gateway predefinito
- `websocket.enabled: true`: avvia `BybitWsFillWatcher`
- `poll_fallback_enabled: true`: schedula reconciliation periodica
- `mode: live` senza gate esplicito resta bloccato

## Avvio runtime locale

```powershell
cd C:\TeleSignalBot
python main.py --migrate
python main.py
```

Log attesi, se il bootstrap gateway CCXT riesce:

```text
execution gateway started | adapter=bybit_demo | account=master_account
telegram listener started | parser_db=... | ops_db=...
```

Se il bootstrap gateway fallisce, il listener parte comunque e nel log compare:

```text
execution gateway init failed - gateway disabled
```

In quel caso intake/parser/lifecycle continuano, ma il gateway exchange resta spento.

## Abilitare watcher WS e reconciliation periodica

In `config/execution.yaml`:

```yaml
websocket:
  enabled: true
  poll_fallback_enabled: true
  poll_fallback_period_seconds: 60
```

Comportamento:

- `enabled: true` avvia `BybitWsFillWatcher`
- se il watcher incontra errori, invoca la callback di reconciliation
- `poll_fallback_enabled: true` avvia anche un ciclo periodico di `run_reconciliation()`

Se `enabled: false`, il runtime usa solo il path polling/reconciliation dei worker.

## Monitoraggio live

Log del bot:

```powershell
Get-Content logs\bot.log -Wait -Tail 80
```

Se vuoi verificare che il processo stia girando:

```powershell
Get-Process python | Where-Object { $_.Path -like '*TeleSignalBot*' }
```

## Controllo DB Runtime V2

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

Ultimi exchange events:

```powershell
python -c "import sqlite3; c=sqlite3.connect('db/ops.sqlite3'); [print(r) for r in c.execute('SELECT exchange_event_id, trade_chain_id, event_type, processing_status, received_at FROM ops_exchange_events ORDER BY exchange_event_id DESC LIMIT 20')]; c.close()"
```

Lettura rapida degli stati:

| Stato | Significato |
|---|---|
| `enriched_canonical_messages.lifecycle_processed=0` | pronto per lifecycle worker |
| `ops_execution_commands.status=PENDING` | comando pronto per gateway |
| `SENT` o `ACK` | comando inviato/riconosciuto |
| `WAITING_POSITION` | comando in attesa che la chain diventi `OPEN` |
| `RETRY` / `next_retry_at` valorizzato | invio fallito, ritentera' |
| `FAILED` | errore terminale |
| `REVIEW_REQUIRED` | serve intervento manuale |

## Test

Suite runtime_v2 completa:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\runtime_v2 -q --tb=short
```

Gateway + lifecycle:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\runtime_v2\execution_gateway tests\runtime_v2\lifecycle -q --tb=short
```

Test bootstrap runtime:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\runtime_v2\test_main_runtime_bootstrap.py -q --tb=short
```

Test gated Bybit testnet:

```powershell
$env:BYBIT_TESTNET_API_KEY="<key>"
$env:BYBIT_API_SECRET_BYBIT_TESTNET="<secret>"
.\.venv\Scripts\python.exe -m pytest tests\runtime_v2\execution_gateway\test_ccxt_bybit_gated.py -v -s -m bybit_testnet
```

Nota:

- i test gated richiedono credenziali reali Bybit testnet
- non sono una prova end-to-end completa dell'intera catena Telegram -> lifecycle -> exchange

## Reset DB per test pulito

Operazione distruttiva. Fare backup prima.

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
| Nessun messaggio entra | `config/channels.yaml`, `active: true`, `chat_id`, `topic_id` | attiva il canale corretto e guarda `logs/bot.log` |
| Messaggi in `raw_messages` ma non in `canonical_messages` | log `parse failed` | controlla `parser_profile` e il profilo in `src/parser_v2/profiles/` |
| Enrichment `BLOCK` o `REVIEW` | `reason_code` in `enriched_canonical_messages` | controlla `operation_config.yaml` e `config/traders/*.yaml` |
| `ops_execution_commands` resta `PENDING` | log gateway e `config/execution.yaml` | verifica bootstrap adapter, credenziali e routing |
| `execution gateway init failed - gateway disabled` | log bootstrap | correggi `config/execution.yaml`, `api_key` e env `BYBIT_API_SECRET_*` |
| Nessun `ops_exchange_events` nonostante comandi `SENT` | watcher/reconciliation | abilita `websocket.enabled` oppure controlla polling/reconciliation |
| Ordine va in `REVIEW_REQUIRED` | `result_payload_json` sul comando | controlla capability, safety gate, payload, connector |
| Test gated saltati | env mancanti | imposta `BYBIT_TESTNET_API_KEY` e `BYBIT_API_SECRET_BYBIT_TESTNET` |

## Sequenza consigliata per il primo test end-to-end demo

1. Controlla `.env`, `channels.yaml`, `operation_config.yaml`, `config/traders/*.yaml`, `execution.yaml`.
2. Imposta la `api_key` dell'adapter demo in `config/execution.yaml`.
3. Imposta `BYBIT_API_SECRET_BYBIT_DEMO` in `.env`.
4. Decidi se attivare `websocket.enabled`.
5. Esegui `python main.py --migrate`.
6. Esegui `python main.py`.
7. Verifica nel log `execution gateway started | adapter=bybit_demo | account=master_account`.
8. Invia o attendi un messaggio Telegram su un canale `active: true`.
9. Guarda `logs\bot.log`.
10. Controlla `raw_messages`, `canonical_messages`, `enriched_canonical_messages`.
11. Controlla `ops_trade_chains`, `ops_execution_commands`, `ops_exchange_events`.
12. Verifica che i comandi transitino da `PENDING` a `SENT`/`ACK`/`DONE` oppure a uno stato diagnostico comprensibile.

## Limite attuale

Anche con il wiring runtime sistemato, la migrazione non va considerata chiusa
senza una prova reale Bybit Demo completa del lifecycle:

```text
entry -> fill -> TP/SL/close -> exchange events -> lifecycle update
```

Per lo stato piu' aggiornato del lavoro guarda:

- `docs/Raggionamento/CCXT/AUDIT_stato_migrazione_ccxt.md`
