# Guida demo stack — Bybit Main Demo + Hummingbot parallelo

Stack separato dallo stack principale (porta 8000 / container `hummingbot`).
Usa porta **8001**, rete `hummingbot-demo-net`, volumi isolati.

---

## File coinvolti

| File | Ruolo |
|---|---|
| `docker-compose.demo.yml` | Definizione stack Docker demo |
| `.env.demo` | Variabili d'ambiente per lo stack demo (non versionato) |
| `hummingbot_demo_patch/bybit_perpetual_constants.py` | Patch connector: aggiunge `bybit_perpetual_demo -> https://api-demo.bybit.com/` |
| `config/execution.yaml` | Routing adapter Runtime V2, usa `hummingbot_api_demo` come default |

---

## Prerequisiti

```powershell
# Docker attivo
docker ps

# Python funzionante
python --version
```

---

## Fase 1 — Configurare `.env.demo`

Il file `.env.demo` non e' versionato. Aprilo e inserisci i valori reali:

```powershell
notepad C:\TeleSignalBot\.env.demo
```

Contenuto da compilare:

```env
# Bybit Demo API keys
# Ottienile su: https://www.bybit.com/app/user/api-management (tab "Demo Trading")
BYBIT_DEMO_API_KEY=<la_tua_chiave_api>
BYBIT_DEMO_API_SECRET=<il_tuo_secret>

# Password di crittografia per i file di configurazione Hummingbot
HUMMINGBOT_DEMO_CONFIG_PASSWORD=<scegli_una_password>

# Password database Postgres (solo uso interno Docker)
HUMMINGBOT_DEMO_DB_PASSWORD=<scegli_una_password_db>

# Non usato per ora dall'adapter Runtime V2 (auth default: admin:admin)
HUMMINGBOT_SECRET=changeme_hbot_secret
```

> **Nota:** Le API key Bybit Demo sono diverse da quelle mainnet e testnet.
> Non servono soldi reali. Le chiavi non vanno mai salvate in file versionati.

---

## Fase 2 — Avviare lo stack Docker

```powershell
cd C:\TeleSignalBot
docker compose -f docker-compose.demo.yml --env-file .env.demo up -d
```

Verifica che i container siano attivi:

```powershell
docker compose -f docker-compose.demo.yml ps
```

Output atteso:

```
NAME                          STATUS
hummingbot-demo               Up (o Restarting — vedi nota sotto)
hummingbot-demo-backend-api   Up
hummingbot-demo-broker        Up
hummingbot-demo-postgres      Up
```

> **Nota — hummingbot-demo in Restarting:** Il container Hummingbot stesso (la console)
> riavvia finche' non viene configurato con le chiavi. E' normale e non blocca il
> backend-api. Per il primo test infrastructure-only il Restarting non e' un problema.
> Serve intervento manuale solo prima di usare il connector (Fase 4).

Verifica API backend raggiungibile:

```powershell
Invoke-WebRequest -Uri http://localhost:8001/docs -UseBasicParsing -TimeoutSec 5
```

Risposta attesa: `StatusCode: 200`

---

## Fase 3 — Verificare che il patch connector sia caricato

Il file `hummingbot_demo_patch/bybit_perpetual_constants.py` viene montato read-only
nel container Hummingbot. Contiene l'entry demo:

```python
REST_URLS = {
    "bybit_perpetual_main": "https://api.bybit.com/",
    "bybit_perpetual_testnet": "https://api-testnet.bybit.com/",
    "bybit_perpetual_demo": "https://api-demo.bybit.com/",   # <-- aggiunto
}
```

Verifica rapida che il file montato contenga la riga demo:

```powershell
docker exec hummingbot-demo grep bybit_perpetual_demo `
  /home/hummingbot/hummingbot/connector/derivative/bybit_perpetual/bybit_perpetual_constants.py
```

Output atteso (4 righe, una per ogni dict URL):

```
    "bybit_perpetual_demo": "https://api-demo.bybit.com/",
    "bybit_perpetual_demo": "wss://stream-demo.bybit.com/v5/public/inverse",
    "bybit_perpetual_demo": "wss://stream-demo.bybit.com/v5/public/linear",
    "bybit_perpetual_demo": "wss://stream-demo.bybit.com/v5/private",
```

Se il container e' in Restarting, aspetta che torni up o usa:

```powershell
docker run --rm `
  -v "C:\TeleSignalBot\hummingbot_demo_patch\bybit_perpetual_constants.py:/home/hummingbot/hummingbot/connector/derivative/bybit_perpetual/bybit_perpetual_constants.py:ro" `
  hummingbot/hummingbot:latest `
  conda run -n hummingbot bash -c "grep bybit_perpetual_demo /home/hummingbot/hummingbot/connector/derivative/bybit_perpetual/bybit_perpetual_constants.py"
```

---

## Fase 4 — Configurare le API key nel backend-api

Questa fase richiede le API key Bybit Demo reali (Fase 1 completata).

Le credenziali vengono salvate cifrate nel volume del backend-api tramite API REST.
La password di cifratura e' `HUMMINGBOT_DEMO_CONFIG_PASSWORD` da `.env.demo`.

**Credenziali HTTP Basic per le chiamate API al backend-api:**
- username: `admin`
- password: `admin`

(default built-in dell'immagine `hummingbot/hummingbot-api`)

### 4a. Verifica che l'account `master_account` esista

```powershell
curl -s -u "admin:admin" http://localhost:8001/accounts/
```

Output atteso: `["master_account"]`

### 4b. Aggiungi le credenziali Bybit Demo

```powershell
$apiKey = "<la_tua_api_key_demo>"
$apiSecret = "<il_tuo_api_secret_demo>"

$body = @{
    bybit_perpetual_api_key    = $apiKey
    bybit_perpetual_secret_key = $apiSecret
} | ConvertTo-Json

Invoke-WebRequest `
  -Uri "http://localhost:8001/accounts/add-credential/master_account/bybit_perpetual_demo" `
  -Method POST `
  -Headers @{ Authorization = "Basic " + [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("admin:admin")) } `
  -ContentType "application/json" `
  -Body $body `
  -UseBasicParsing
```

Output atteso: HTTP 201

### 4c. Verifica che le credenziali siano salvate

```powershell
Invoke-WebRequest `
  -Uri "http://localhost:8001/accounts/master_account/credentials" `
  -Headers @{ Authorization = "Basic " + [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("admin:admin")) } `
  -UseBasicParsing
```

Output atteso: JSON con `bybit_perpetual_demo` nella lista.

---

## Fase 5 — Configurare il connector Hummingbot (console)

Il container `hummingbot-demo` ha una console interattiva. Va configurato
separatamente dal backend-api.

Stabilisci una connessione alla console:

```powershell
docker attach hummingbot-demo
```

Se lo schermo resta vuoto, premi `Enter`.

Dentro la console:

```text
# Inserisci la password (quella in HUMMINGBOT_DEMO_CONFIG_PASSWORD)
> <password>

# Configura il connector demo
connect bybit_perpetual_demo
```

Inserisci API key e secret quando richiesti. La console li cripta automaticamente.

Per uscire senza fermare il container:

```text
Ctrl+P poi Ctrl+Q
```

> **Differenza backend-api vs console:**
> - Il backend-api (Fase 4) gestisce le credenziali per le chiamate REST dell'API
>   (es. piazzare ordini da Runtime V2).
> - La console Hummingbot (Fase 5) e' l'interfaccia nativa per configurare strategie.
> - Per il flusso Runtime V2 e' sufficiente Fase 4. Fase 5 serve se si vuole anche
>   usare la console Hummingbot direttamente.

---

## Fase 6 — Test infrastructure-only (senza API key reali)

Questi test non piazzano ordini e non richiedono API key valide.

```powershell
cd C:\TeleSignalBot

$env:RUN_HUMMINGBOT_DEMO_TESTS = "1"
$env:HUMMINGBOT_DEMO_API_URL   = "http://localhost:8001"
$env:HUMMINGBOT_SECRET         = "changeme_hbot_secret"

pytest tests\runtime_v2\execution_gateway\test_hummingbot_demo_gated.py::test_01_api_reachable `
       tests\runtime_v2\execution_gateway\test_hummingbot_demo_gated.py::test_02_connector_available `
       -v
```

Output atteso:

```
test_01_api_reachable PASSED
test_02_connector_available PASSED
```

---

## Fase 7 — Test completi con API key reali

Richiede Fase 1 + Fase 4 completate con chiavi reali.
I test piazzano un ordine limit a prezzo 1.0 (non eseguibile), lo interrogano e lo cancellano.

```powershell
cd C:\TeleSignalBot

$env:RUN_HUMMINGBOT_DEMO_TESTS     = "1"
$env:HUMMINGBOT_DEMO_API_URL       = "http://localhost:8001"
$env:HUMMINGBOT_DEMO_CONNECTOR     = "bybit_perpetual_demo"
$env:HUMMINGBOT_DEMO_ACCOUNT       = "master_account"
$env:HUMMINGBOT_SECRET             = "changeme_hbot_secret"

pytest tests\runtime_v2\execution_gateway\test_hummingbot_demo_gated.py -v -s
```

Test inclusi:

| Test | Cosa fa | Richiede chiavi |
|---|---|---|
| `test_01_api_reachable` | GET /docs | No |
| `test_02_connector_available` | Verifica capabilities adapter | No |
| `test_03_set_leverage` | POST leverage=1 su BTC/USDT | Si |
| `test_04_place_entry_limit` | Piazza ordine LIMIT a 1.0 | Si |
| `test_05_get_order_status` | Legge stato ordine | Si |
| `test_06_cancel_order` | Cancella ordine | Si |
| `test_07_get_position` | Legge posizione aperta | Si |

---

## Monitoraggio

Log backend-api demo:

```powershell
docker logs hummingbot-demo-backend-api --tail 50 -f
```

Log container Hummingbot demo:

```powershell
docker logs hummingbot-demo --tail 50 -f
```

Stato container:

```powershell
docker compose -f docker-compose.demo.yml ps
```

---

## Fermare lo stack

```powershell
cd C:\TeleSignalBot
docker compose -f docker-compose.demo.yml --env-file .env.demo down
```

Per rimuovere anche i volumi (cancella DB e credenziali cifrate):

```powershell
docker compose -f docker-compose.demo.yml --env-file .env.demo down -v
```

> **Attenzione:** `-v` cancella anche le credenziali salvate nel volume Postgres.
> Dopo `down -v` serve ripetere Fase 4 e Fase 5.

---

## Troubleshooting

| Sintomo | Causa probabile | Azione |
|---|---|---|
| `hummingbot-demo` in Restarting | Nessuna config/password nel volume | Normale finche' non si fa Fase 5. Non blocca il backend-api |
| `http://localhost:8001/docs` non risponde | backend-api non partito | `docker logs hummingbot-demo-backend-api --tail 30` |
| `detail: Not authenticated` | Credenziali HTTP Basic assenti | Aggiungere `-u "admin:admin"` alla chiamata curl o header Authorization |
| `detail: Incorrect username or password` | Username/password sbagliati | Default: `admin` / `admin` |
| Patch non caricato in container | Volume mount fallito | Controlla che `hummingbot_demo_patch/bybit_perpetual_constants.py` esista e che il path in `docker-compose.demo.yml` sia corretto |
| `test_03_set_leverage` fallisce | Chiavi non configurate in backend-api | Ripetere Fase 4 |
| `Connection refused localhost:8001` | Stack non avviato | `docker compose -f docker-compose.demo.yml --env-file .env.demo up -d` |
| Porta 8001 gia' occupata | Altro processo | `netstat -ano | findstr :8001` per trovare chi la usa |

---

## Configurazione Runtime V2

`config/execution.yaml` e' gia' configurato per usare il demo stack:

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
      live_safety:
        allow_live_trading: false
```

Per tornare allo stack paper/testnet cambia `default_adapter` in `hummingbot_api_paper`
e aggiorna `account_routing.default.adapter`. Non serve modificare altro.

---

## Sequenza rapida primo avvio

1. Inserisci API key demo in `.env.demo`
2. `docker compose -f docker-compose.demo.yml --env-file .env.demo up -d`
3. Controlla che `http://localhost:8001/docs` risponda 200
4. Aggiungi credenziali via API (Fase 4b)
5. Avvia `python main.py` con `HUMMINGBOT_BASE_URL=http://localhost:8001` in `.env`
6. Esegui test gated (Fase 7)
