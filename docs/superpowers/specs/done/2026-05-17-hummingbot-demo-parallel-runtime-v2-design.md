# Spec 2 — Hummingbot Parallelo Demo (bybit_perpetual_demo)

Data: 2026-05-17  
Stato: approvata per pianificazione  
Ambito: stack Hummingbot demo separato collegato a Runtime V2, Bybit Main Demo  

---

## 1. Contesto

Runtime V2 usa Execution Gateway → HummingbotApiAdapter → Hummingbot API su `localhost:8000`.
Il connector attivo è `bybit_perpetual_testnet`, inaccessibile per limiti regionali.

Bybit Main Demo (`https://api-demo.bybit.com`) è disponibile e piazza ordini reali sull'exchange
demo, con fill immediato e semantica perpetual identica al live. Il paper trade Hummingbot simula
ordini localmente e ha semantica diversa — non è un sostituto valido per verificare il lifecycle
Runtime V2.

L'immagine Hummingbot stock conosce `bybit_perpetual_main` e `bybit_perpetual_testnet`.
Non espone un connector dedicato `bybit_perpetual_demo`. Serve una patch leggera.

Questa spec copre **solo il Binario B**: stack Hummingbot separato con connector custom verso
Bybit Main Demo. L'adapter diretto Bybit V5 (Binario A) è fuori scope e oggetto di una spec
separata.

---

## 2. Obiettivo

Creare uno stack Hummingbot demo parallelo, isolato dallo stack esistente, che permetta a
Runtime V2 di eseguire comandi reali su Bybit Main Demo senza modificare il lifecycle o il
codice del gateway.

```text
Telegram signal
  → Parser / Enrichment / Lifecycle Runtime V2
  → ops_execution_commands
  → ExecutionCommandWorker
  → ExecutionGateway
  → HummingbotApiAdapter
  → Hummingbot API demo  :8001
  → Hummingbot demo container  (connector: bybit_perpetual_demo)
  → https://api-demo.bybit.com
  → [risposta exchange]
  → ops_exchange_events  ← normalizzazione fill
  → Runtime V2 lifecycle update
```

Il cambio da demo a qualsiasi altro ambiente (testnet, live) è una scelta di config, non una
modifica al codice Runtime V2.

---

## 3. Non Obiettivi

- Non implementare adapter diretto Runtime V2 → Bybit V5 (Spec 1 separata).
- Non abilitare live trading.
- Non rimuovere o modificare lo stack Hummingbot esistente su `localhost:8000`.
- Non creare una UI TeleSignalBot per lifecycle e DB operativo.
- Non usare paper trade come segnale di correttezza exchange.
- Non salvare API key o secret nel repository.
- Non costruire un fork Hummingbot (vedi Sezione 5 per la decisione).

---

## 4. Decisioni

### 4.1 Patch connector: volume mount con immagine pinned

**Decisione:** volume mount dei file Python del connector custom + immagine Hummingbot stock
pinned a un tag specifico (non `latest`).

**Perché non fork:** per uno stack demo/testing il fork introduce overhead di manutenzione
sproporzionato (sync con upstream, build pipeline, gestione tag). Il volume mount permette
iterazione rapida sui file connector senza rebuild.

**Rischio principale neutralizzato:** pin dell'immagine a un tag specifico impedisce che
Hummingbot aggiorni la struttura interna dei moduli sotto i piedi. Upgrade esplicito e
verificato.

**Quando si passa al fork:** se il connector diventa parte dell'architettura a lungo termine
o viene richiesta auditabilità di produzione, i file del mount diventano il codice nel fork.
È una promozione, non una riscrittura.

### 4.2 Migrazione demo → live

Il codice Runtime V2 è identico per demo e live. Cambia solo la configurazione.

Per il live **non serve il connector custom**: `bybit_perpetual_main` esiste già nell'immagine
stock. Il volume mount demo non è necessario in produzione.

| Cosa | Demo (questa spec) | Live (futuro) |
|---|---|---|
| Immagine Docker | stock + volume mount | stock, nessun mount |
| Connector | `bybit_perpetual_demo` (custom) | `bybit_perpetual_main` (nativo) |
| URL exchange | `api-demo.bybit.com` | `api.bybit.com` |
| API keys | demo keys in `.env` | live keys in `.env` |
| Config `mode` | `demo` | `live` |
| Safety gate env | assente | `TSB_ALLOW_LIVE_TRADING=YES_I_UNDERSTAND` |
| Size cap / kill switch | non richiesti | richiesti prima di abilitare |

---

## 5. Architettura Stack Demo

### Stack esistente (invariato)

```text
Runtime V2
  → Execution Gateway
  → Hummingbot API  :8000
  → hummingbot (container)
  → bybit_perpetual_testnet
```

### Stack demo parallelo (questa spec)

```text
Runtime V2
  → Execution Gateway
  → Hummingbot API demo  :8001
  → hummingbot-demo (container)  + volume mount connector
  → bybit_perpetual_demo (custom)
  → https://api-demo.bybit.com
```

Ogni componente demo usa risorse completamente separate: container, porte, volumi, database,
broker. Nessuna risorsa è condivisa con lo stack esistente.

---

## 6. Struttura Docker

### File

```text
docker-compose.demo.yml          ← compose separato per lo stack demo
hummingbot_demo_patch/           ← file Python del connector custom (montati nel container)
  bybit_perpetual_demo/
    __init__.py
    bybit_perpetual_demo_constants.py   ← URL demo, WebSocket, parametri
    bybit_perpetual_demo_utils.py       ← helper specifici se necessari
    ... (altri file connector da verificare in implementazione)
hummingbot_demo_conf/            ← config Hummingbot demo (gitignored)
hummingbot_demo_logs/            ← log Hummingbot demo (gitignored)
hummingbot_demo_data/            ← dati runtime demo (gitignored)
```

`hummingbot_demo_patch/` è versionato (contiene solo codice Python, nessun secret).
Le directory `hummingbot_demo_conf/`, `hummingbot_demo_logs/`, `hummingbot_demo_data/` sono
in `.gitignore`.

### docker-compose.demo.yml

```yaml
services:
  hummingbot-demo:
    image: hummingbot/hummingbot:2.3.0        # pinned — non usare latest
    container_name: hummingbot-demo
    volumes:
      - ./hummingbot_demo_patch/bybit_perpetual_demo:/hummingbot_src/hummingbot/connector/derivative/bybit_perpetual_demo
      - ./hummingbot_demo_conf:/conf
      - ./hummingbot_demo_logs:/logs
      - ./hummingbot_demo_data:/data
    environment:
      - CONFIG_PASSWORD=${HUMMINGBOT_DEMO_CONFIG_PASSWORD}
    networks:
      - hummingbot-demo-net
    depends_on:
      - hummingbot-demo-broker
      - hummingbot-demo-postgres

  hummingbot-demo-backend-api:
    image: hummingbot/backend-api:latest      # pinned in implementazione
    container_name: hummingbot-demo-backend-api
    ports:
      - "8001:8000"
    environment:
      - BROKER_HOST=hummingbot-demo-broker
      - BROKER_PORT=1883
      - DATABASE_URL=postgresql://hummingbot:${HUMMINGBOT_DEMO_DB_PASSWORD}@hummingbot-demo-postgres/hummingbot_demo
    networks:
      - hummingbot-demo-net
    depends_on:
      - hummingbot-demo-broker
      - hummingbot-demo-postgres

  hummingbot-demo-broker:
    image: eclipse-mosquitto:2.0
    container_name: hummingbot-demo-broker
    networks:
      - hummingbot-demo-net

  hummingbot-demo-postgres:
    image: postgres:15
    container_name: hummingbot-demo-postgres
    volumes:
      - hummingbot_demo_postgres:/var/lib/postgresql/data
    environment:
      - POSTGRES_DB=hummingbot_demo
      - POSTGRES_USER=hummingbot
      - POSTGRES_PASSWORD=${HUMMINGBOT_DEMO_DB_PASSWORD}
    networks:
      - hummingbot-demo-net

networks:
  hummingbot-demo-net:
    driver: bridge

volumes:
  hummingbot_demo_postgres:
```

**Nota:** il path di mount del connector (`/hummingbot_src/hummingbot/connector/derivative/`)
e il meccanismo di registrazione del connector nel registry Hummingbot devono essere verificati
sulla versione pinned durante l'implementazione. Il connector potrebbe richiedere anche una
entry nel file di registrazione interno di Hummingbot (da montare o patchare con un secondo
volume).

---

## 7. Connector bybit_perpetual_demo

### Approccio

Il connector è una copia minima di `bybit_perpetual` (o `bybit_perpetual_main`) con URL
sostituite:

| Parametro | bybit_perpetual_main | bybit_perpetual_demo |
|---|---|---|
| REST base | `https://api.bybit.com` | `https://api-demo.bybit.com` |
| WebSocket pubblico | `wss://stream.bybit.com` | da verificare |
| WebSocket privato | `wss://stream.bybit.com` | da verificare |

Se i WebSocket demo non sono disponibili o documentati, il primo MVP usa polling REST per
stato ordini e posizioni. Il connector deve essere dichiaratamente degradato in quel caso
(`polling_mode: true` o equivalente), non silenziosamente broken.

### Funzioni da verificare su demo reale

- REST base URL demo risponde
- auth e firma HMAC accettate
- `set leverage`
- `place order` entry market e limit
- `cancel order`
- `get order status`
- `get position`
- `close full` (reduce-only)
- `close partial` (reduce-only con size)
- stop loss e take profit nativi (abilitati nel connector solo dopo verifica)

### Nome canonico

`bybit_perpetual_demo` è il nome stabile da usare in `config/execution.yaml`, nei test gated
e nelle istruzioni operative. Eventuali alias interni Hummingbot non devono comparire nella
configurazione Runtime V2.

---

## 8. Configurazione Runtime V2

`config/execution.yaml` supporta più adapter. Il cambio ambiente è solo `default_adapter`.

```yaml
execution:
  default_adapter: hummingbot_api_demo

  account_routing:
    default:
      adapter: hummingbot_api_demo
      execution_account_id: master_account

  adapters:
    hummingbot_api_testnet:
      type: hummingbot_api
      mode: testnet
      base_url: http://localhost:8000
      connector: bybit_perpetual_testnet
      leverage: 1
      live_safety:
        allow_live_trading: false

    hummingbot_api_demo:
      type: hummingbot_api
      mode: demo
      base_url: http://localhost:8001
      connector: bybit_perpetual_demo
      leverage: 1
      live_safety:
        allow_live_trading: false
```

Riferimento futuro per live (fuori scope di questa spec):

```yaml
    hummingbot_api_live:
      type: hummingbot_api
      mode: live
      base_url: http://localhost:8002
      connector: bybit_perpetual_main        # nativo, nessun volume mount
      live_safety:
        allow_live_trading: true             # richiede anche TSB_ALLOW_LIVE_TRADING env
```

---

## 9. Flusso Operativo

Il gateway gestisce gli stessi command type definiti da PRD-05:

- `PLACE_ENTRY`
- `PLACE_PROTECTIVE_STOP`
- `PLACE_TAKE_PROFIT`
- `MOVE_STOP_TO_BREAKEVEN`
- `MOVE_STOP`
- `CANCEL_PENDING_ENTRY`
- `CLOSE_PARTIAL`
- `CLOSE_FULL`

Il gateway mantiene:

- risoluzione account tramite `account_routing`
- capability check prima di ogni comando
- `client_order_id` deterministico `tsb:<chain>:<command>:<role>:<seq>`
- set leverage prima del primo ordine
- aggiornamento stati in `ops_execution_commands`
- normalizzazione fill in `ops_exchange_events`
- retry policy per errori tecnici

Lo stack demo non introduce un lifecycle diverso. Se il connector demo non supporta una
funzione, il comando diventa `REVIEW_REQUIRED` con motivo esplicito — non fallisce
silenziosamente.

---

## 10. Compatibilità Console, Dashboard, Condor

Lo stack demo è compatibile con i client Hummingbot standard:

- console Hummingbot via `docker attach hummingbot-demo`
- Swagger UI su `http://localhost:8001/docs`
- Hummingbot Dashboard configurata con API demo `localhost:8001`
- Condor configurato con API server demo

Questi client controllano lo stack Hummingbot demo. Non sono UI per il lifecycle interno
TeleSignalBot, che resta tracciato in DB e log Runtime V2.

---

## 11. Safety

- `mode: demo` non abilita mai live trading.
- `mode: testnet` non abilita live trading.
- `mode: live` richiede `allow_live_trading: true` **e** `TSB_ALLOW_LIVE_TRADING=YES_I_UNDERSTAND` in env.
- Ogni stack usa porte, volumi, database e container separati.
- Nessun secret viene scritto in log, test, report o documentazione.
- Le API key demo stanno solo in `.env` (gitignored).
- Le API key live future non devono essere riusate in stack demo.

---

## 12. Error Handling

| Situazione | Stato Runtime V2 |
|---|---|
| Capability mancante | `REVIEW_REQUIRED` |
| Stop/TP non supportati dal connector demo | `REVIEW_REQUIRED` |
| Account routing assente | `REVIEW_REQUIRED` |
| Bybit 4xx terminale (400, 401, 403) | `FAILED` |
| Bybit 429 rate limit | retry con backoff, poi `FAILED` |
| Timeout, connection reset, 5xx | retry secondo policy gateway |
| Retry esauriti | `FAILED` |
| Secret mancante | errore startup configurazione |
| Live mode senza gate ambiente | `REVIEW_REQUIRED` |

Gli errori vengono salvati in `result_payload_json` in forma leggibile e senza secret.
Secret presenti in messaggi di errore Bybit o Hummingbot devono essere mascherati prima
della persistenza.

---

## 13. Testing

### Test automatici (nessun container richiesto)

- config loader con adapter multipli `hummingbot_api_testnet` e `hummingbot_api_demo`
- factory adapter risolve tipo `hummingbot_api` correttamente
- gateway con adapter fake: `mode=demo` non altera lifecycle rispetto a `mode=testnet`
- safety gate: `mode=live` senza env var → `REVIEW_REQUIRED`
- masking secret in `result_payload_json`

### Test gated contro stack demo reale

Attivati con variabili d'ambiente:

```env
RUN_HUMMINGBOT_DEMO_TESTS=1
HUMMINGBOT_DEMO_API_URL=http://localhost:8001
HUMMINGBOT_DEMO_CONNECTOR=bybit_perpetual_demo
HUMMINGBOT_DEMO_ACCOUNT=master_account
```

Sequenza verifiche:

1. API demo risponde (`GET /`)
2. Connector `bybit_perpetual_demo` disponibile
3. `set leverage` accettato
4. `place entry` con size minima — ordine accettato da Bybit Demo
5. `get order status` — stato coerente
6. `cancel order` su ordine aperto
7. `get position` — posizione visibile
8. `close full` su posizione aperta

Stop loss, take profit e close partial dichiarati supportati solo dopo verifica reale.

---

## 14. Acceptance Criteria

1. Lo stack esistente su `localhost:8000` continua a funzionare senza modifiche obbligatorie.
2. `docker-compose.demo.yml` avvia i quattro container demo su risorse separate.
3. Hummingbot demo riconosce `bybit_perpetual_demo` come connector valido.
4. `config/execution.yaml` con `default_adapter: hummingbot_api_demo` è accettato da Runtime V2.
5. Runtime V2 genera gli stessi `ops_execution_commands` di oggi.
6. Execution Gateway invia i comandi alla Hummingbot API demo quando `default_adapter=hummingbot_api_demo`.
7. Un segnale controllato produce almeno un `PLACE_ENTRY` accettato da Bybit Main Demo.
8. `client_order_id` resta deterministico e tracciabile in `ops_execution_commands`.
9. `cancel`, `get position` e `close_full` sono verificati su demo o esplicitamente marcati
   come non supportati in capability config.
10. Stop loss e take profit sono abilitati nel connector solo dopo verifica reale su Bybit Demo.
11. Swagger UI, console Hummingbot e client esterni (Condor/Dashboard) puntano allo stack demo.
12. Nessun secret viene scritto in repository, log, test o output.
13. I test automatici passano senza stack demo attivo.

---

## 15. Rischi

| Rischio | Impatto | Mitigazione |
|---|---|---|
| Path mount connector errato sulla versione pinned | connector non caricato | verificare path nella versione esatta prima di scrivere il connector |
| Registrazione connector richiede file aggiuntivi | build più lunga | esplorare registry Hummingbot nella versione pinned prima dell'implementazione |
| WebSocket demo non documentati o non funzionanti | tracking ordini degradato | MVP con polling REST, documentato esplicitamente |
| Bybit Demo ha endpoint V5 parzialmente diversi da mainnet | ordini o posizioni falliscono | test gated con size minima prima di integrare nel lifecycle |
| TP/SL demo non compatibili con mapping attuale | lifecycle incompleto | dichiarare capability false finché non verificato |

---

## 16. Migrazione a Live (riferimento futuro)

Nessun codice Runtime V2 da modificare. Passi operativi:

1. Aggiungere adapter `hummingbot_api_live` in `config/execution.yaml` con `connector: bybit_perpetual_main`.
2. Creare `docker-compose.live.yml` con immagine stock (nessun volume mount — connector nativo).
3. Configurare chiavi live in `.env` separato.
4. Impostare `TSB_ALLOW_LIVE_TRADING=YES_I_UNDERSTAND` in env live.
5. Aggiungere size cap e kill switch (prerequisiti da definire in PRD live).
6. Cambiare `default_adapter: hummingbot_api_live`.

I file Python in `hummingbot_demo_patch/` non vengono usati in produzione live.

---

## 17. Suggested Commit Message

```text
docs(runtime-v2): spec2 hummingbot parallel demo stack with bybit_perpetual_demo
```
