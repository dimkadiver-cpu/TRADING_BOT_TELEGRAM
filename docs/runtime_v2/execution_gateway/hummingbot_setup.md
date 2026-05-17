# Execution Gateway — Setup Hummingbot (paper trading)

Guida passo passo per avviare Hummingbot in locale via Docker e collegarlo all'Execution Gateway del bot.

Segui i passi nell'ordine. Ogni sezione termina con una verifica — non andare avanti finché la verifica non passa.

---

## Prerequisiti

| Cosa | Versione minima | Verifica |
|------|-----------------|---------|
| Docker Desktop | 24.x | `docker --version` |
| Python | 3.12 | `python --version` |
| Account Bybit testnet | — | https://testnet.bybit.com |
| API keys Bybit testnet | — | sezione "API Management" su testnet.bybit.com |

Se Docker non è installato: https://docs.docker.com/get-docker/ (richiede riavvio dopo l'installazione su Windows).

---

## Passo 1 — Aggiorna .gitignore

Le directory di configurazione e log di Hummingbot non devono entrare nel repo.

```bash
echo "hummingbot_conf/" >> .gitignore
echo "hummingbot_logs/" >> .gitignore
git add .gitignore
git commit -m "chore: gitignore hummingbot_conf e hummingbot_logs"
```

**Verifica:**
```bash
git check-ignore -v hummingbot_conf/
# atteso: .gitignore:N:hummingbot_conf/  hummingbot_conf/
```

---

## Passo 2 — Crea le directory locali

```bash
mkdir -p hummingbot_conf hummingbot_logs
```

Queste directory vengono montate come volumi nel container Docker. Hummingbot vi salverà la configurazione e i log — persistono tra i riavvii del container.

---

## Passo 3 — Pull dell'immagine Docker

```bash
docker pull hummingbot/hummingbot:latest
```

L'immagine pesa ~2 GB. Se la connessione è lenta, avvia il pull e lascialo completare in background.

**Verifica:**
```bash
docker images | grep hummingbot
# atteso: hummingbot/hummingbot   latest   <id>   ...
```

---

## Passo 4 — Avvia il container

Opzione consigliata per questo repository:

```bash
docker compose up
```

Il compose avvia anche `hummingbot-backend-api` sulla porta `8000`, PostgreSQL ed EMQX. Il mount di `/var/run/docker.sock` è necessario al backend API per orchestrare i bot via Docker; EMQX è il broker MQTT usato per comunicare con le istanze Hummingbot.

In alternativa, per la sola console interattiva Hummingbot:

```bash
docker run -it \
  --name hummingbot \
  -p 8000:8000 \
  -v "${PWD}/hummingbot_conf:/home/hummingbot/conf" \
  -v "${PWD}/hummingbot_logs:/home/hummingbot/logs" \
  hummingbot/hummingbot:latest
```

> **Windows PowerShell:** sostituisci `${PWD}` con `$(Get-Location)` oppure usa il path assoluto.
>
> ```powershell
> docker run -it --name hummingbot -p 8000:8000 `
>   -v "$(Get-Location)\hummingbot_conf:/home/hummingbot/conf" `
>   -v "$(Get-Location)\hummingbot_logs:/home/hummingbot/logs" `
>   hummingbot/hummingbot:latest
> ```

Al primo avvio Hummingbot mostra un prompt per creare una password. Per sviluppo locale usa `test1234`. **Non usare questa password in produzione.**

Dopo l'inserimento della password compare la console interattiva di Hummingbot (`>>>` oppure una UI testuale).

---

## Passo 5 — Abilita il REST API server

Dentro la console Hummingbot digita:

```
start --script rest_api_server
```

Se la versione è ≥ 2.x (Hummingbot v2 / Gateway):

```
gateway generate-certs
gateway start
```

Lascia il container in esecuzione. Apri un **secondo terminale** (fuori dal container) e verifica:

```bash
curl -I http://localhost:8000/docs
```

**Verifica attesa:** risposta HTTP 200. L'API corrente espone Swagger/OpenAPI su `/docs`; non tutte le versioni espongono `/health`.

Se ottieni `Connection refused`, il server non è ancora attivo — attendi qualche secondo e riprova. Se il problema persiste, controlla i log nel primo terminale.

---

## Passo 6 — Connetti il connector Bybit paper trading

Dentro la console Hummingbot:

```
connect bybit_perpetual_testnet
```

Hummingbot chiede l'API key e il secret della tua chiave Bybit **testnet** (non mainnet). Le trovi su https://testnet.bybit.com → profilo → "API Management" → crea una chiave con permessi di trading.

Al termine:

```
status
```

**Verifica:** la riga relativa a `bybit_perpetual_testnet` mostra `CONNECTED`.

---

## Passo 7 — Crea il file `.env`

Nella root del progetto crea il file `.env` (se non esiste):

```
HUMMINGBOT_BASE_URL=http://localhost:8000
HUMMINGBOT_SECRET=admin:admin
```

`HUMMINGBOT_SECRET` accetta token Bearer semplici oppure credenziali Basic nel formato `username:password`. L'immagine corrente `hummingbot/hummingbot-api` usa HTTP Basic e il default locale è `admin:admin`.

> `.env` è già gitignored. Verifica con:
> ```bash
> git check-ignore -v .env
> ```

---

## Passo 8 — Verifica end-to-end con il bot

In un terminale separato, con il container Hummingbot attivo:

```bash
python main.py --migrate
```

Atteso: migrazione applicata (0 modifiche se già eseguita), nessun errore.

Poi avvia il bot vero e proprio e controlla i log:

```bash
# Linux/Mac
TELEGRAM_API_ID=<id> TELEGRAM_API_HASH=<hash> python main.py 2>&1 | grep -E "execution gateway|HUMMINGBOT"

# Windows PowerShell
$env:TELEGRAM_API_ID="<id>"; $env:TELEGRAM_API_HASH="<hash>"; python main.py 2>&1 | Select-String "execution gateway|HUMMINGBOT"
```

**Verifica attesa nel log:**
```
execution gateway started | adapter=hummingbot_api_paper | url=http://localhost:8000 | account=master_account
```

Se vedi invece `execution gateway disabled`, il file `.env` non viene caricato — assicurati che `python-dotenv` sia installato e che `load_dotenv()` sia chiamato in `main.py` prima di `os.getenv`.

---

## Riavvio del container (sessioni successive)

Il container esiste già dopo il primo avvio. Per riavviarlo:

```bash
docker start -ai hummingbot
```

All'interno riesegui `start --script rest_api_server` (o `gateway start`) se il server non riparte automaticamente.

Per fermarlo pulitamente:

```bash
docker stop hummingbot
```

---

## Troubleshooting

| Sintomo | Causa probabile | Soluzione |
|---------|----------------|-----------|
| `Connection refused` su porta 8000 | REST server non avviato | Dentro Hummingbot: `start --script rest_api_server` |
| `401 Unauthorized` nelle chiamate API | Secret errato o assente | Con `hummingbot/hummingbot-api`, usa `HUMMINGBOT_SECRET=admin:admin` oppure le credenziali impostate nell'API |
| `422 Unprocessable Entity` | Body della richiesta non valido | Vedi Task 4 del piano di gap closure — endpoint da verificare |
| `Container name already in use` | Container precedente non rimosso | `docker rm hummingbot` poi ripeti il `docker run` |
| `docker.errors.DockerException` nel container `hummingbot-backend-api` | Docker socket non montato | Usa `docker compose up`; il compose monta `/var/run/docker.sock` |
| `ConnectionRefusedError` verso MQTT nel backend API | Broker EMQX assente o non pronto | Usa il compose aggiornato e verifica `docker ps` per `hummingbot-broker` |
| `execution gateway disabled` nel log | `HUMMINGBOT_BASE_URL` non caricato | Verifica che `.env` esista e che `load_dotenv()` sia chiamato |
| `bybit_perpetual_testnet: DISCONNECTED` | API keys scadute o errate | Rigenera le API keys su testnet.bybit.com |

---

## Passo successivo — paper/testnet

Con Hummingbot attivo e `curl -I http://localhost:8000/docs` che risponde OK, esegui i test gated:

```bash
RUN_HUMMINGBOT_API_TESTS=1 \
HUMMINGBOT_API_URL=http://localhost:8000 \
HUMMINGBOT_CONNECTOR=bybit_perpetual_testnet \
HUMMINGBOT_ACCOUNT=master_account \
HUMMINGBOT_SECRET=admin:admin \
pytest tests/runtime_v2/execution_gateway/test_hummingbot_adapter.py -v
```

---

## Stack demo (Bybit Main Demo)

Per usare Bybit Main Demo invece di testnet, lo stack principale **non viene modificato**.
Esiste uno stack parallelo separato su porta 8001 con connector `bybit_perpetual_demo`.

Guida completa: `COMANDI_DEMO.md`.

Differenze rispetto allo stack paper/testnet:

| Aspetto | Paper/Testnet | Demo |
|---|---|---|
| Compose | `docker-compose.yml` | `docker-compose.demo.yml` |
| Porta backend-api | 8000 | 8001 |
| Connector | `bybit_perpetual_testnet` | `bybit_perpetual_demo` |
| URL exchange | `api-testnet.bybit.com` | `api-demo.bybit.com` |
| Patch necessaria | No | Sì (volume mount `bybit_perpetual_constants.py`) |
| Broker | EMQX 5 | Mosquitto 2.0 |
| Network Docker | `hbot-network` | `hummingbot-demo-net` |
| Env file | `.env` | `.env.demo` |
