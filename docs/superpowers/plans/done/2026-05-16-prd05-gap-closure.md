# PRD-05 Gap Closure — Hummingbot Integration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Chiudere le 4 lacune che impediscono il test end-to-end dell'Execution Gateway contro Hummingbot reale: wiring in main.py, autenticazione, setup Docker Hummingbot, verifica endpoint.

**Architecture:** Tasks 1-2 sono pure modifiche di codice (nessuna dipendenza esterna). Tasks 3-4 richiedono Docker e un'istanza Hummingbot in esecuzione. L'adapter viene istanziato in main.py solo se `HUMMINGBOT_BASE_URL` è presente nell'environment, così il bot funziona anche senza Hummingbot configurato.

**Tech Stack:** Python 3.12+, httpx, Pydantic v2, Docker, Hummingbot (paper trading), pytest

**Spec di riferimento:** `docs/superpowers/specs/2026-05-16-prd05-definitive-design.md`

---

## File map

```
MODIFICATI:
main.py                                                    ← wire ExecutionCommandWorker + ExchangeEventSyncWorker
src/runtime_v2/execution_gateway/models.py                 ← AdapterConfig.secret campo opzionale
src/runtime_v2/execution_gateway/adapters/hummingbot_api_paper.py  ← Bearer auth header
config/execution.yaml                                      ← secret field (opzionale)

NUOVI:
tests/runtime_v2/execution_gateway/test_auth.py            ← test che l'header viene inviato
hummingbot_conf/                                           ← directory config Hummingbot (gitignored)
```

---

## Task 1: Wire ExecutionCommandWorker + ExchangeEventSyncWorker in main.py

**Files:**
- Modify: `main.py:17-44` (import block) e `main.py:134-174` (lifecycle section)

- [ ] **Step 1: Aggiungi gli import**

In `main.py`, dopo la riga `from src.runtime_v2.lifecycle.workers import LifecycleEventWorker, TimeoutWorker`, aggiungi:

```python
from src.runtime_v2.execution_gateway.adapters.hummingbot_api_paper import HummingbotApiPaperAdapter
from src.runtime_v2.execution_gateway.command_worker import ExecutionCommandWorker
from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository
```

- [ ] **Step 2: Istanzia i worker nel body di `_async_main`**

In `main.py`, dopo il blocco `# PRD-04 lifecycle layer` (riga ~134), aggiungi questo blocco prima di `async def _run_lifecycle_workers()`:

```python
    # PRD-05 execution gateway layer (abilitato solo se HUMMINGBOT_BASE_URL è configurato)
    hummingbot_url = os.getenv("HUMMINGBOT_BASE_URL", "")
    execution_worker: ExecutionCommandWorker | None = None
    sync_worker: ExchangeEventSyncWorker | None = None

    if hummingbot_url:
        execution_config_path = str(root_dir / "config" / "execution.yaml")
        exec_config = ExecutionConfigLoader(execution_config_path).load()
        hb_secret = os.getenv("HUMMINGBOT_SECRET", "")
        adapter_name = exec_config.default_adapter
        adapter_cfg = exec_config.adapters[adapter_name]
        hb_adapter = HummingbotApiPaperAdapter(
            base_url=hummingbot_url,
            connector=adapter_cfg.connector,
            secret=hb_secret or None,
        )
        gateway_repo = GatewayCommandRepository(ops_db_path)
        gateway = ExecutionGateway(
            config=exec_config,
            adapter_registry={adapter_name: hb_adapter},
            repo=gateway_repo,
        )
        routing, _ = exec_config.resolve_routing("default")
        execution_worker = ExecutionCommandWorker(
            ops_db_path=ops_db_path,
            gateway=gateway,
            repo=gateway_repo,
        )
        sync_worker = ExchangeEventSyncWorker(
            ops_db_path=ops_db_path,
            adapter=hb_adapter,
            repo=gateway_repo,
            execution_account_id=routing.execution_account_id,
        )
        logger.info(
            "execution gateway started | adapter=%s | url=%s | account=%s",
            adapter_name, hummingbot_url, routing.execution_account_id,
        )
    else:
        logger.warning("HUMMINGBOT_BASE_URL not set — execution gateway disabled (paper commands will queue but not be sent)")
```

- [ ] **Step 3: Aggiungi i worker alla coroutine `_run_lifecycle_workers`**

Sostituisci `async def _run_lifecycle_workers() -> None:` con:

```python
    async def _run_lifecycle_workers() -> None:
        while True:
            try:
                gate_worker.run_once()
                timeout_worker.run_once()
                lifecycle_event_worker.run_once()
                if execution_worker is not None:
                    execution_worker.run_once()
                if sync_worker is not None:
                    sync_worker.run_once()
            except Exception:
                logger.exception("lifecycle worker error")
            await asyncio.sleep(10)
```

- [ ] **Step 4: Smoke test — migrate e avvio senza Hummingbot**

```bash
python main.py --migrate
```

Expected: stampa `Parser migrations applied: 0 | Ops migrations applied: 0` (o simile), nessun errore.

```bash
# Verifica che il bot si avvii e loggi il warning corretto (poi Ctrl+C)
TELEGRAM_API_ID=12345 TELEGRAM_API_HASH=fake python main.py 2>&1 | head -20
```

Expected: vedi nel log `execution gateway disabled` — nessun crash sull'import.

- [ ] **Step 5: Commit**

```bash
git add main.py
git commit -m "feat(prd05): wire ExecutionCommandWorker + ExchangeEventSyncWorker in main.py"
```

---

## Task 2: Aggiungi Bearer auth (secret) all'adapter

**Files:**
- Modify: `src/runtime_v2/execution_gateway/models.py:48-60` (AdapterConfig)
- Modify: `src/runtime_v2/execution_gateway/adapters/hummingbot_api_paper.py:17-21` (__init__)
- Modify: `config/execution.yaml`
- Create: `tests/runtime_v2/execution_gateway/test_auth.py`

- [ ] **Step 1: Scrivi il test**

Crea `tests/runtime_v2/execution_gateway/test_auth.py`:

```python
# tests/runtime_v2/execution_gateway/test_auth.py
from __future__ import annotations

import httpx
import pytest


def test_adapter_sends_bearer_header(respx_mock):
    """Con secret configurato, ogni richiesta porta Authorization: Bearer <secret>."""
    from src.runtime_v2.execution_gateway.adapters.hummingbot_api_paper import HummingbotApiPaperAdapter

    respx_mock.post("http://localhost:8000/trading/orders").mock(
        return_value=httpx.Response(200, json={"id": "abc", "exchange_order_id": "exch_1"})
    )

    adapter = HummingbotApiPaperAdapter(
        base_url="http://localhost:8000",
        connector="bybit_perpetual_paper_trade",
        secret="my_secret_123",
    )
    result = adapter.place_order(
        command_type="PLACE_ENTRY",
        payload={"symbol": "BTC/USDT", "side": "LONG",
                 "entry_type": "LIMIT", "price": 50000.0, "qty": 0.02, "sequence": 1},
        client_order_id="tsb:1:1:entry:1",
        execution_account_id="acc_main",
        connector="bybit_perpetual_paper_trade",
    )
    assert result.success
    sent_request = respx_mock.calls[0].request
    assert sent_request.headers.get("authorization") == "Bearer my_secret_123"


def test_adapter_no_auth_header_when_no_secret(respx_mock):
    """Senza secret, nessun header Authorization."""
    from src.runtime_v2.execution_gateway.adapters.hummingbot_api_paper import HummingbotApiPaperAdapter

    respx_mock.post("http://localhost:8000/trading/orders").mock(
        return_value=httpx.Response(200, json={"id": "abc", "exchange_order_id": "exch_1"})
    )

    adapter = HummingbotApiPaperAdapter(
        base_url="http://localhost:8000",
        connector="bybit_perpetual_paper_trade",
        secret=None,
    )
    adapter.place_order(
        command_type="PLACE_ENTRY",
        payload={"symbol": "BTC/USDT", "side": "LONG",
                 "entry_type": "LIMIT", "price": 50000.0, "qty": 0.02, "sequence": 1},
        client_order_id="tsb:1:2:entry:1",
        execution_account_id="acc_main",
        connector="bybit_perpetual_paper_trade",
    )
    sent_request = respx_mock.calls[0].request
    assert "authorization" not in sent_request.headers
```

- [ ] **Step 2: Installa respx (mock per httpx) e verifica che il test fallisce**

```bash
pip install respx -q
pytest tests/runtime_v2/execution_gateway/test_auth.py -v
```

Expected: FAILED — `TypeError: __init__() got an unexpected keyword argument 'secret'`

- [ ] **Step 3: Aggiungi `secret` ad `AdapterConfig` in models.py**

In `src/runtime_v2/execution_gateway/models.py`, nella classe `AdapterConfig` cambia `model_config = ConfigDict(extra="forbid")` in `model_config = ConfigDict(extra="ignore")` oppure aggiungi il campo (preferibile):

```python
class AdapterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str
    mode: str
    base_url: str
    connector: str
    leverage: int = 1
    secret: str | None = None          # ← aggiunto
    entry_execution: EntryExecutionConfig = EntryExecutionConfig()
    retry: RetryConfig = RetryConfig()
    capabilities: AdapterCapabilities = AdapterCapabilities()
    take_profit: TakeProfitConfig = TakeProfitConfig()
    position_management: PositionManagementConfig = PositionManagementConfig()
    live_safety: LiveSafetyConfig = LiveSafetyConfig()
```

- [ ] **Step 4: Aggiungi `secret` a `HummingbotApiPaperAdapter.__init__`**

Sostituisci il `__init__`:

```python
    def __init__(
        self,
        base_url: str,
        connector: str,
        timeout: float = 10.0,
        secret: str | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._connector = connector
        headers = {"Authorization": f"Bearer {secret}"} if secret else {}
        self._client = httpx.Client(
            base_url=self._base_url,
            timeout=timeout,
            headers=headers,
        )
```

- [ ] **Step 5: Aggiorna `config/execution.yaml` con il campo opzionale**

Aggiungi `secret:` (vuoto = nessuna auth, sovrascrivibile da env var `HUMMINGBOT_SECRET` in main.py):

```yaml
    hummingbot_api_paper:
      type: hummingbot_api
      mode: paper
      base_url: http://localhost:8000
      connector: bybit_perpetual_paper_trade
      leverage: 1
      # secret: ""   # lascia vuoto — viene da env var HUMMINGBOT_SECRET in main.py
```

Il campo `secret` rimane assente o commentato in yaml — viene passato esplicitamente da main.py via `os.getenv("HUMMINGBOT_SECRET")`.

- [ ] **Step 6: Esegui i test**

```bash
pytest tests/runtime_v2/execution_gateway/test_auth.py -v
```

Expected: PASS 2/2.

```bash
pytest tests/runtime_v2/execution_gateway/ -v --tb=short 2>&1 | tail -15
```

Expected: tutti PASS, 3 skipped (gated).

- [ ] **Step 7: Commit**

```bash
git add src/runtime_v2/execution_gateway/models.py \
        src/runtime_v2/execution_gateway/adapters/hummingbot_api_paper.py \
        tests/runtime_v2/execution_gateway/test_auth.py
git commit -m "feat(prd05): Bearer auth su HummingbotApiPaperAdapter — secret da HUMMINGBOT_SECRET env var"
```

---

## Task 3: Hummingbot Docker setup + REST server

> **Prerequisito:** Docker installato e in esecuzione. Questo task è infrastruttura, non codice Python.

**Files:**
- Create: `hummingbot_conf/` (directory, gitignored)
- Modify: `.gitignore`

- [ ] **Step 1: Aggiungi `hummingbot_conf/` al .gitignore**

```bash
echo "hummingbot_conf/" >> .gitignore
echo "hummingbot_logs/" >> .gitignore
git add .gitignore
git commit -m "chore: gitignore hummingbot_conf e hummingbot_logs"
```

- [ ] **Step 2: Crea le directory di configurazione**

```bash
mkdir -p hummingbot_conf/connectors
mkdir -p hummingbot_logs
```

- [ ] **Step 3: Pull e avvia Hummingbot in Docker**

```bash
docker pull hummingbot/hummingbot:latest
docker run -it \
  --name hummingbot \
  -p 8000:8000 \
  -v "${PWD}/hummingbot_conf:/home/hummingbot/conf" \
  -v "${PWD}/hummingbot_logs:/home/hummingbot/logs" \
  hummingbot/hummingbot:latest
```

Al primo avvio Hummingbot chiede di creare una password. Usa `test1234` per lo sviluppo locale. **Non usare questa password in produzione.**

- [ ] **Step 4: Abilita il REST server dentro Hummingbot**

Dentro il terminale Hummingbot interattivo:

```
>>> config gateway_api_port 8000
>>> config gateway_api_secret test1234
>>> start --script rest_api_server
```

Oppure (dipende dalla versione, ≥ 2.0):

```
>>> gateway generate-certs
>>> start
```

Verifica che il server sia attivo:

```bash
curl http://localhost:8000/health
```

Expected: `{"status": "ok"}` o simile.

- [ ] **Step 5: Configura il connector Bybit paper trading**

Dentro Hummingbot:

```
>>> connect bybit_perpetual_paper_trade
```

Inserisci le API keys Bybit (paper trading keys da https://testnet.bybit.com).

Verifica che il connector sia connesso:

```
>>> status
```

Expected: `bybit_perpetual_paper_trade: CONNECTED`.

- [ ] **Step 6: Configura le variabili d'ambiente nel progetto**

Crea `.env` (se non esiste) con:

```bash
HUMMINGBOT_BASE_URL=http://localhost:8000
HUMMINGBOT_SECRET=test1234
```

**Nota:** `.env` è già gitignored (verificare con `git check-ignore .env`).

---

## Task 4: Verifica endpoint + fix mismatches

> **Prerequisito:** Task 3 completato, Hummingbot in esecuzione su localhost:8000.

**Files:**
- Modify: `src/runtime_v2/execution_gateway/adapters/hummingbot_api_paper.py` (se endpoint differ)
- Modify: `tests/runtime_v2/execution_gateway/test_hummingbot_adapter.py` (aggiorna test se necessario)

- [ ] **Step 1: Esegui il test di health check**

```bash
RUN_HUMMINGBOT_API_TESTS=1 \
HUMMINGBOT_API_URL=http://localhost:8000 \
HUMMINGBOT_CONNECTOR=bybit_perpetual_paper_trade \
HUMMINGBOT_ACCOUNT=bybit_paper_main \
pytest tests/runtime_v2/execution_gateway/test_hummingbot_adapter.py::test_api_reachable -v
```

Expected: PASS. Se fallisce con `ConnectionRefused`, Hummingbot non è in ascolto — torna al Task 3 Step 4.

- [ ] **Step 2: Esegui il test di capabilities**

```bash
RUN_HUMMINGBOT_API_TESTS=1 \
HUMMINGBOT_API_URL=http://localhost:8000 \
pytest tests/runtime_v2/execution_gateway/test_hummingbot_adapter.py::test_capabilities_declared -v
```

Expected: PASS (questo non fa chiamate HTTP, solo verifica la struttura locale).

- [ ] **Step 3: Esegui il test place+query+cancel**

```bash
RUN_HUMMINGBOT_API_TESTS=1 \
HUMMINGBOT_SECRET=test1234 \
HUMMINGBOT_API_URL=http://localhost:8000 \
pytest tests/runtime_v2/execution_gateway/test_hummingbot_adapter.py::test_place_and_query_order -v -s
```

Expected: PASS. Se fallisce con `422 Unprocessable Entity` o `404`, l'endpoint o il body non corrisponde all'API reale. Leggi l'errore (aggiungere `-s` mostra output).

- [ ] **Step 4: Correggi gli endpoint se necessario**

Se `POST /trading/orders` restituisce 404, ispeziona l'API Hummingbot:

```bash
curl -s http://localhost:8000/docs 2>/dev/null | python -m json.tool | head -100
# oppure
curl -s http://localhost:8000/openapi.json 2>/dev/null | python -m json.tool | grep '"path"'
```

Mappa gli endpoint reali ai metodi dell'adapter in `hummingbot_api_paper.py` e aggiorna i path. Esempio di correzioni tipiche:

| Nostro path | Path reale (se diverso) |
|-------------|------------------------|
| `POST /trading/orders` | `POST /v2/orders` |
| `POST /trading/orders/search` | `GET /v2/orders/{client_order_id}` |
| `POST /trading/leverage` | `POST /v2/leverage` |

Dopo ogni correzione, rilanciare il test del Step 3.

- [ ] **Step 5: Test di integrazione E2E manuale**

Con Hummingbot in esecuzione e `.env` configurato:

```bash
python main.py --migrate
```

Poi in un secondo terminale:

```bash
cat logs/bot.log | grep -E "execution gateway|HUMMINGBOT"
```

Expected: `execution gateway started | adapter=hummingbot_api_paper | url=http://localhost:8000 | account=bybit_paper_main`

- [ ] **Step 6: Commit delle correzioni endpoint**

```bash
git add src/runtime_v2/execution_gateway/adapters/hummingbot_api_paper.py
git commit -m "fix(prd05): correggi endpoint API Hummingbot dopo verifica su istanza reale"
```

---

## Checklist finale

```bash
# Test suite completa (nessuna regressione)
pytest tests/runtime_v2/ -v --tb=short 2>&1 | tail -10

# Test auth
pytest tests/runtime_v2/execution_gateway/test_auth.py -v

# Test gated (richiede Hummingbot attivo)
RUN_HUMMINGBOT_API_TESTS=1 HUMMINGBOT_SECRET=test1234 \
HUMMINGBOT_API_URL=http://localhost:8000 \
pytest tests/runtime_v2/execution_gateway/test_hummingbot_adapter.py -v

# Smoke test main.py senza Hummingbot (deve loggare warning, non crashare)
python main.py --migrate
```
