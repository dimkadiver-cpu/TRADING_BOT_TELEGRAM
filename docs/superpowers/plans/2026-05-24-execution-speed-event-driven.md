# Event-Driven Lifecycle Wake-Up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminare il polling cieco da 10s dal loop lifecycle, sostituendolo con wake-up event-driven che riduce la latenza signal→ordine e fill→risposta da ~5–10s a <200ms.

**Architecture:** Due `asyncio.Event` vengono settati rispettivamente da `SignalEnrichmentProcessor` (nuovo PASS) e da `BybitWsFillWatcher` (nuovo fill in DB). Il loop lifecycle si sveglia su questi eventi invece di dormire 10s fissi; `ExchangeEventSyncWorker` (REST) viene separato in un task indipendente a 8s per evitare rate limiting.

**Tech Stack:** Python 3.12+, asyncio, sqlite3, ccxt.pro

---

## File map

| File | Azione | Responsabilità |
|---|---|---|
| `src/runtime_v2/signal_enrichment/processor.py` | Modifica | Aggiunge `on_pass: Callable[[], None] \| None` |
| `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/ws_fill_watcher.py` | Modifica | Aggiunge `wake_callback: Callable[[], None] \| None` |
| `main.py` | Modifica | Crea eventi, `_wait_any`, riscrive loop, aggiunge `_run_sync_worker` |
| `tests/runtime_v2/signal_enrichment/test_processor_signal.py` | Modifica | Aggiunge test `on_pass` |
| `tests/runtime_v2/execution_gateway/test_bybit_ws_fill_watcher.py` | Modifica | Aggiunge test `wake_callback` |

---

## Task 1: `SignalEnrichmentProcessor` — callback `on_pass`

**Files:**
- Modify: `src/runtime_v2/signal_enrichment/processor.py`
- Test: `tests/runtime_v2/signal_enrichment/test_processor_signal.py`

### Contesto

`SignalEnrichmentProcessor.process()` produce un `EnrichedCanonicalMessage`. Solo i messaggi con `lifecycle_processed=False` sono eleggibili per il lifecycle gate worker. Questi sono esattamente i SIGNAL PASS e gli UPDATE PASS. Il callback `on_pass` va chiamato solo per loro, non per REPORT/INFO PASS (che hanno `lifecycle_processed=True`) né per BLOCK/REVIEW.

Condizione esatta per chiamare il callback: `not saved.lifecycle_processed`.

Il callback viene invocato dal thread del listener (main asyncio loop) — thread-safe per design.

---

- [ ] **Step 1: Scrivi i test failing**

Aggiungi in coda al file `tests/runtime_v2/signal_enrichment/test_processor_signal.py`:

```python
# ── on_pass callback tests ────────────────────────────────────────────────────
# Questi test usano mock puri per isolare la logica del callback dal pipeline
# completo di enrichment. Il repository viene mockato per controllare il valore
# di lifecycle_processed restituito da save().

def _make_processor_mock(on_pass=None):
    """Costruisce SignalEnrichmentProcessor con repo e config interamente mockati."""
    from unittest.mock import MagicMock
    from src.runtime_v2.signal_enrichment.processor import SignalEnrichmentProcessor
    mock_config = MagicMock()
    mock_config.reload_if_changed.return_value = None
    mock_config.get_effective_config.return_value = None  # BLOCK path: config=None
    mock_repo = MagicMock()
    mock_repo.get_by_canonical_message_id.return_value = None
    return SignalEnrichmentProcessor(
        config_loader=mock_config,
        repository=mock_repo,
        on_pass=on_pass,
    ), mock_repo


def _fake_result(canonical_message_id: int = 1) -> "MagicMock":
    from unittest.mock import MagicMock
    r = MagicMock()
    r.canonical_message_id = canonical_message_id
    r.parser_profile = "trader_a"
    return r


def test_on_pass_called_when_saved_lifecycle_processed_false():
    """on_pass viene chiamato quando repo.save() restituisce lifecycle_processed=False."""
    from unittest.mock import MagicMock
    called = []
    processor, mock_repo = _make_processor_mock(on_pass=lambda: called.append(1))

    saved = MagicMock()
    saved.lifecycle_processed = False  # simula SIGNAL PASS / UPDATE PASS
    mock_repo.save.return_value = saved

    processor.process(_fake_result(1))
    assert called == [1]


def test_on_pass_not_called_when_saved_lifecycle_processed_true():
    """on_pass NON viene chiamato quando repo.save() restituisce lifecycle_processed=True."""
    from unittest.mock import MagicMock
    called = []
    processor, mock_repo = _make_processor_mock(on_pass=lambda: called.append(1))

    saved = MagicMock()
    saved.lifecycle_processed = True  # simula BLOCK / REVIEW / REPORT
    mock_repo.save.return_value = saved

    processor.process(_fake_result(2))
    assert called == []


def test_on_pass_called_multiple_times_for_multiple_passes():
    """on_pass viene chiamato una volta per ogni PASS distinto."""
    from unittest.mock import MagicMock
    called = []
    processor, mock_repo = _make_processor_mock(on_pass=lambda: called.append(1))

    saved = MagicMock()
    saved.lifecycle_processed = False
    mock_repo.save.return_value = saved
    mock_repo.get_by_canonical_message_id.return_value = None  # sempre fresh

    processor.process(_fake_result(10))
    processor.process(_fake_result(11))
    assert called == [1, 1]


def test_on_pass_none_default_does_not_raise():
    """on_pass=None (default) non causa errori anche se lifecycle_processed=False."""
    from unittest.mock import MagicMock
    processor, mock_repo = _make_processor_mock(on_pass=None)

    saved = MagicMock()
    saved.lifecycle_processed = False
    mock_repo.save.return_value = saved

    processor.process(_fake_result(99))  # deve completare senza eccezioni
```

- [ ] **Step 2: Verifica che i test falliscano**

```
pytest tests/runtime_v2/signal_enrichment/test_processor_signal.py::test_on_pass_called_when_saved_lifecycle_processed_false tests/runtime_v2/signal_enrichment/test_processor_signal.py::test_on_pass_not_called_when_saved_lifecycle_processed_true tests/runtime_v2/signal_enrichment/test_processor_signal.py::test_on_pass_none_default_does_not_raise -v
```

Atteso: `TypeError: __init__() got an unexpected keyword argument 'on_pass'` (o simile)

- [ ] **Step 3: Implementa `on_pass` in `processor.py`**

Aggiungi l'import `Callable` e modifica `__init__` e `process`:

```python
# src/runtime_v2/signal_enrichment/processor.py
from __future__ import annotations

import logging
from collections.abc import Callable

from src.runtime_v2.parser_pipeline.models import CanonicalParseResult
from src.runtime_v2.signal_enrichment.config_loader import OperationConfigLoader
from src.runtime_v2.signal_enrichment.models import (
    EffectiveEnrichmentConfig,
    EnrichedCanonicalMessage,
    EnrichedEntryLeg,
    EnrichedSignalPayload,
    EnrichmentLogEntry,
)
from src.runtime_v2.signal_enrichment.repository import EnrichedCanonicalMessageRepository

logger = logging.getLogger(__name__)


class SignalEnrichmentProcessor:
    def __init__(
        self,
        config_loader: OperationConfigLoader,
        repository: EnrichedCanonicalMessageRepository,
        on_pass: Callable[[], None] | None = None,
    ) -> None:
        self._config = config_loader
        self._repo = repository
        self._on_pass = on_pass

    def process(self, result: CanonicalParseResult) -> EnrichedCanonicalMessage:
        existing = self._repo.get_by_canonical_message_id(result.canonical_message_id)
        if existing is not None:
            return existing

        self._config.reload_if_changed()
        trader_id = result.parser_profile
        config = self._config.get_effective_config(trader_id)

        if config is None:
            enriched = self._make_outcome(result, "BLOCK", "trader_not_registered",
                                          lifecycle_processed=True)
        elif not config.enabled:
            enriched = self._make_outcome(result, "BLOCK", "trader_disabled",
                                          lifecycle_processed=True)
        else:
            policy_snapshot = config.model_dump()
            policy_version = self._config.get_policy_version()
            enriched = self._route(result, config, policy_snapshot, policy_version)

        saved = self._repo.save(enriched)
        if not saved.lifecycle_processed and self._on_pass:
            self._on_pass()
        return saved
```

Il resto del file (metodi `_route`, `_process_signal`, ecc.) rimane invariato. Sostituisci solo `__init__` e `process`.

- [ ] **Step 4: Esegui i test**

```
pytest tests/runtime_v2/signal_enrichment/test_processor_signal.py::test_on_pass_called_when_saved_lifecycle_processed_false tests/runtime_v2/signal_enrichment/test_processor_signal.py::test_on_pass_not_called_when_saved_lifecycle_processed_true tests/runtime_v2/signal_enrichment/test_processor_signal.py::test_on_pass_called_multiple_times_for_multiple_passes tests/runtime_v2/signal_enrichment/test_processor_signal.py::test_on_pass_none_default_does_not_raise -v
```

Atteso: 4 PASSED

- [ ] **Step 5: Verifica la suite signal enrichment completa**

```
pytest tests/runtime_v2/signal_enrichment/ -q --tb=short
```

Atteso: tutti i test preesistenti PASSED (il parametro `on_pass` è opzionale e non rompe nulla).

- [ ] **Step 6: Commit**

```
git add src/runtime_v2/signal_enrichment/processor.py tests/runtime_v2/signal_enrichment/test_processor_signal.py
git commit -m "feat(signal_enrichment): add on_pass callback to SignalEnrichmentProcessor"
```

---

## Task 2: `BybitWsFillWatcher` — callback `wake_callback`

**Files:**
- Modify: `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/ws_fill_watcher.py`
- Test: `tests/runtime_v2/execution_gateway/test_bybit_ws_fill_watcher.py`

### Contesto

Il watcher gira in un **thread separato** con il proprio asyncio loop (`_run_in_thread`). Il callback `wake_callback` viene chiamato da quel thread. In `main.py` il chiamante è responsabile di usare `call_soon_threadsafe` per segnalare il loop principale; il watcher non ne sa nulla.

Ci sono **due punti di insert** in `ops_exchange_events`:
1. `_save_fill()` — inserisce `ENTRY_FILLED`, `TP_FILLED`, `SL_FILLED`, `CLOSE_*_FILLED` via sqlite3 diretto
2. `_save_tp_fill_from_trade()` — inserisce `TP_FILLED` da `watchMyTrades` via `self._repo.insert_exchange_event()`

Il callback va chiamato dopo entrambi.

---

- [ ] **Step 1: Scrivi i test failing**

Aggiungi in coda al file `tests/runtime_v2/execution_gateway/test_bybit_ws_fill_watcher.py`:

```python
# ── wake_callback tests ───────────────────────────────────────────────────────

def _make_watcher_with_callback(ops_db, wake_callback=None):
    """Costruisce BybitWsFillWatcher con callback iniettato e repo reale."""
    from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.ws_fill_watcher import BybitWsFillWatcher
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository
    repo = GatewayCommandRepository(ops_db)
    return BybitWsFillWatcher(
        api_key="key",
        api_secret="secret",
        testnet=True,
        ops_db_path=ops_db,
        repo=repo,
        wake_callback=wake_callback,
    )


def _insert_chain_open(db_path: str, chain_id: int = 1) -> None:
    import sqlite3
    now = "2026-01-01T00:00:00+00:00"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_trade_chains "
        "(trade_chain_id, source_enrichment_id, trader_id, account_id, symbol, side, "
        "entry_structure, lifecycle_state, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (chain_id, chain_id, "trader_a", "main", "BTCUSDT", "LONG",
         "ONE_SHOT", "OPEN", now, now),
    )
    conn.commit()
    conn.close()


def test_wake_callback_called_on_save_fill(ops_db):
    """wake_callback viene chiamato dopo _save_fill (ENTRY_FILLED)."""
    from unittest.mock import MagicMock
    from src.runtime_v2.execution_gateway.models import RawAdapterOrder
    from src.runtime_v2.execution_gateway import client_order_id as coid_mod

    called = []
    watcher = _make_watcher_with_callback(ops_db, wake_callback=lambda: called.append(1))

    # Inserisci un comando entry nella catena 1
    _insert_chain_open(ops_db, chain_id=1)
    _insert_command(ops_db, command_id=1, trade_chain_id=1,
                    command_type="PLACE_ENTRY", status="SENT",
                    client_order_id="tsb:1:1:entry:1")

    raw = RawAdapterOrder(
        exchange_order_id="ex-001",
        client_order_id="tsb:1:1:entry:1",
        status="closed",
        is_filled=True,
        average_price=60000.0,
        filled_qty=0.001,
    )
    watcher._save_fill("tsb:1:1:entry:1", raw)
    assert called == [1]


def test_wake_callback_called_on_save_tp_fill_from_trade(ops_db):
    """wake_callback viene chiamato dopo _save_tp_fill_from_trade."""
    called = []
    watcher = _make_watcher_with_callback(ops_db, wake_callback=lambda: called.append(1))

    _insert_chain_open(ops_db, chain_id=2)

    watcher._save_tp_fill_from_trade(
        chain_id=2,
        tp_level=1,
        fill_price=65000.0,
        filled_qty=0.001,
        is_final=False,
        exchange_trade_id="trade-001",
    )
    assert called == [1]


def test_wake_callback_none_does_not_raise(ops_db):
    """wake_callback=None (default) non causa errori."""
    from unittest.mock import MagicMock
    from src.runtime_v2.execution_gateway.models import RawAdapterOrder

    watcher = _make_watcher_with_callback(ops_db, wake_callback=None)
    _insert_chain_open(ops_db, chain_id=3)
    _insert_command(ops_db, command_id=3, trade_chain_id=3,
                    command_type="PLACE_ENTRY", status="SENT",
                    client_order_id="tsb:3:3:entry:1")

    raw = RawAdapterOrder(
        exchange_order_id="ex-003",
        client_order_id="tsb:3:3:entry:1",
        status="closed",
        is_filled=True,
        average_price=60000.0,
        filled_qty=0.001,
    )
    watcher._save_fill("tsb:3:3:entry:1", raw)  # non deve sollevare
```

- [ ] **Step 2: Verifica che i test falliscano**

```
pytest tests/runtime_v2/execution_gateway/test_bybit_ws_fill_watcher.py::test_wake_callback_called_on_save_fill tests/runtime_v2/execution_gateway/test_bybit_ws_fill_watcher.py::test_wake_callback_called_on_save_tp_fill_from_trade tests/runtime_v2/execution_gateway/test_bybit_ws_fill_watcher.py::test_wake_callback_none_does_not_raise -v
```

Atteso: `TypeError: __init__() got an unexpected keyword argument 'wake_callback'`

- [ ] **Step 3: Implementa `wake_callback` in `ws_fill_watcher.py`**

**Nel `__init__`**, aggiungi `wake_callback` come ultimo parametro:

```python
def __init__(
    self,
    api_key: str,
    api_secret: str,
    testnet: bool,
    ops_db_path: str,
    repo: GatewayCommandRepository,
    reconciliation_callback=None,
    mode: str = "live",
    wake_callback: "Callable[[], None] | None" = None,
) -> None:
    # ... tutti gli attributi esistenti ...
    self._wake_callback = wake_callback
```

Aggiungi anche `from collections.abc import Callable` in testa al file (dopo gli import standard).

**In `_save_fill`**, aggiungi la chiamata al callback dopo il `finally`:

```python
def _save_fill(self, client_order_id: str, raw: RawAdapterOrder) -> None:
    try:
        coid = coid_mod.parse(client_order_id)
    except ValueError:
        logger.warning("cannot parse client_order_id from websocket fill: %s", client_order_id)
        return

    exchange_order_id = raw.exchange_order_id or client_order_id
    payload: dict[str, object]

    if coid.role == "entry":
        event_type = "ENTRY_FILLED"
        payload = self._base_fill_payload(raw, coid.command_id)
    elif coid.role == "sl":
        event_type = "SL_FILLED"
        payload = self._base_fill_payload(raw, coid.command_id)
    elif coid.role == "tp":
        event_type = "TP_FILLED"
        payload = {
            "tp_level": coid.sequence,
            "is_final": self._repo.count_active_tps(coid.trade_chain_id) <= 1,
            **self._base_fill_payload(raw, coid.command_id),
        }
    elif coid.role == "exit_partial":
        event_type = "CLOSE_PARTIAL_FILLED"
        payload = self._base_fill_payload(raw, coid.command_id)
    elif coid.role == "exit_full":
        event_type = "CLOSE_FULL_FILLED"
        payload = self._base_fill_payload(raw, coid.command_id)
    else:
        logger.debug("ignoring websocket fill for unsupported role '%s'", coid.role)
        return

    if event_type == "TP_FILLED":
        idempotency_key = f"TP_FILLED:{coid.trade_chain_id}:level:{coid.sequence}"
    else:
        idempotency_key = f"{event_type}:{coid.trade_chain_id}:{exchange_order_id}"

    conn = sqlite3.connect(self._ops_db_path)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO ops_exchange_events "
            "(trade_chain_id, event_type, payload_json, processing_status, "
            "idempotency_key, received_at) VALUES (?,?,?,?,?,?)",
            (
                coid.trade_chain_id,
                event_type,
                json.dumps(payload),
                "NEW",
                idempotency_key,
                _now(),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    if self._wake_callback:
        self._wake_callback()
```

**In `_save_tp_fill_from_trade`**, aggiungi la chiamata dopo `logger.debug`:

```python
def _save_tp_fill_from_trade(
    self,
    chain_id: int,
    tp_level: int,
    fill_price: float,
    filled_qty: float,
    is_final: bool,
    exchange_trade_id: str,
) -> None:
    """INSERT OR IGNORE di TP_FILLED con dati reali da watchMyTrades."""
    idempotency_key = f"TP_FILLED:{chain_id}:level:{tp_level}"
    payload = json.dumps({
        "tp_level": tp_level,
        "is_final": is_final,
        "fill_price": fill_price,
        "filled_qty": filled_qty,
        "source": "watch_my_trades",
        "exchange_trade_id": exchange_trade_id,
    })
    self._repo.insert_exchange_event(chain_id, "TP_FILLED", payload, idempotency_key)
    logger.debug(
        "TP_FILLED inserted from watch_my_trades: chain=%d level=%d price=%.4f trade_id=%s",
        chain_id, tp_level, fill_price, exchange_trade_id,
    )
    if self._wake_callback:
        self._wake_callback()
```

- [ ] **Step 4: Esegui i test**

```
pytest tests/runtime_v2/execution_gateway/test_bybit_ws_fill_watcher.py::test_wake_callback_called_on_save_fill tests/runtime_v2/execution_gateway/test_bybit_ws_fill_watcher.py::test_wake_callback_called_on_save_tp_fill_from_trade tests/runtime_v2/execution_gateway/test_bybit_ws_fill_watcher.py::test_wake_callback_none_does_not_raise -v
```

Atteso: 3 PASSED

- [ ] **Step 5: Verifica la suite ws fill watcher completa**

```
pytest tests/runtime_v2/execution_gateway/test_bybit_ws_fill_watcher.py -q --tb=short
```

Atteso: tutti i test preesistenti PASSED.

- [ ] **Step 6: Commit**

```
git add src/runtime_v2/execution_gateway/adapters/ccxt_bybit/ws_fill_watcher.py tests/runtime_v2/execution_gateway/test_bybit_ws_fill_watcher.py
git commit -m "feat(ws_fill_watcher): add wake_callback to signal lifecycle loop on fill"
```

---

## Task 3: `main.py` — loop event-driven + wiring

**Files:**
- Modify: `main.py`

### Contesto

`_build_execution_runtime` costruisce `BybitWsFillWatcher` internamente. Va modificato per ricevere `wake_callback` e passarlo al watcher. Il callback deve usare `call_soon_threadsafe` perché il watcher gira in un thread separato.

Il nuovo `_run_lifecycle_workers` attende `new_enriched_event` o `new_fill_event` (whichever comes first) con un timeout di fallback a 10s, poi esegue tutti i worker tranne `sync_worker`.

`_run_sync_worker` è un nuovo task asyncio separato che chiama `sync_worker.run_once()` ogni 8s.

---

- [ ] **Step 1: Aggiungi `_wait_any` in `main.py`**

Inserisci questa funzione subito dopo le import, prima di `_required_env`:

```python
async def _wait_any(*events: asyncio.Event) -> None:
    """Ritorna appena uno qualsiasi degli eventi viene settato."""
    tasks = [asyncio.ensure_future(e.wait()) for e in events]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
```

- [ ] **Step 2: Aggiorna `_build_execution_runtime` per accettare `wake_callback`**

Modifica la firma e il corpo di `_build_execution_runtime`:

```python
def _build_execution_runtime(
    *,
    root_dir: Path,
    ops_db_path: str,
    logger,
    wake_callback: "Callable[[], None] | None" = None,
) -> ExecutionRuntime | None:
    execution_config_path = str(root_dir / "config" / "execution.yaml")
    exec_config = ExecutionConfigLoader(execution_config_path).load()
    adapter_name = exec_config.default_adapter
    routing, adapter_cfg = exec_config.resolve_routing("default")
    adapter = build_adapter(adapter_name, adapter_cfg)
    gateway_repo = GatewayCommandRepository(ops_db_path)
    gateway = ExecutionGateway(
        config=exec_config,
        adapter_registry={adapter_name: adapter},
        repo=gateway_repo,
    )
    execution_worker = ExecutionCommandWorker(
        ops_db_path=ops_db_path,
        gateway=gateway,
        repo=gateway_repo,
    )
    sync_worker = ExchangeEventSyncWorker(
        ops_db_path=ops_db_path,
        adapter=adapter,
        repo=gateway_repo,
        execution_account_id=routing.execution_account_id,
    )

    ws_watcher = None
    reconciliation_interval_seconds = None
    if adapter_cfg.type == "ccxt_bybit" and adapter_cfg.websocket.enabled:
        api_key = os.environ.get(adapter_cfg.api_key_env or "") if adapter_cfg.api_key_env else ""
        api_secret = os.environ.get(adapter_cfg.api_secret_env or "") if adapter_cfg.api_secret_env else ""
        testnet = bool(getattr(adapter_cfg, "testnet", False) or adapter_cfg.mode == "testnet")
        ws_watcher = BybitWsFillWatcher(
            api_key=api_key,
            api_secret=api_secret,
            testnet=testnet,
            ops_db_path=ops_db_path,
            repo=gateway_repo,
            reconciliation_callback=sync_worker.run_reconciliation,
            mode=adapter_cfg.mode,
            wake_callback=wake_callback,
        )
        ws_watcher.start()
        if adapter_cfg.websocket.poll_fallback_enabled:
            reconciliation_interval_seconds = adapter_cfg.websocket.poll_fallback_period_seconds

    logger.info(
        "execution gateway started | adapter=%s | account=%s",
        adapter_name, routing.execution_account_id,
    )
    return ExecutionRuntime(
        adapter=adapter,
        execution_worker=execution_worker,
        sync_worker=sync_worker,
        ws_watcher=ws_watcher,
        reconciliation_interval_seconds=reconciliation_interval_seconds,
    )
```

Aggiungi anche `from collections.abc import Callable` tra gli import standard di `main.py`.

- [ ] **Step 3: Riscrivi `_run_lifecycle_workers`**

Sostituisci completamente la funzione esistente:

```python
async def _run_lifecycle_workers(
    *,
    new_enriched_event: asyncio.Event,
    new_fill_event: asyncio.Event,
    gate_worker: LifecycleGateWorker,
    timeout_worker: TimeoutWorker,
    lifecycle_event_worker: LifecycleEventWorker,
    execution_runtime: ExecutionRuntime | None,
    logger,
) -> None:
    while True:
        try:
            await asyncio.wait_for(
                _wait_any(new_enriched_event, new_fill_event),
                timeout=10.0,
            )
        except asyncio.TimeoutError:
            pass  # fallback: gestisce timeout entry scaduti e retry

        new_enriched_event.clear()
        new_fill_event.clear()

        try:
            gate_worker.run_once()
            timeout_worker.run_once()
            lifecycle_event_worker.run_once()
            if execution_runtime is not None:
                execution_runtime.execution_worker.run_once()
        except Exception:
            logger.exception("lifecycle worker error")
```

- [ ] **Step 4: Aggiungi `_run_sync_worker`**

Aggiungi questa funzione subito dopo `_run_lifecycle_workers`:

```python
async def _run_sync_worker(
    *,
    sync_worker: ExchangeEventSyncWorker,
    interval_seconds: int = 8,
    logger,
) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            sync_worker.run_once()
        except Exception:
            logger.exception("sync worker error")
```

- [ ] **Step 5: Aggiorna il wiring in `_async_main`**

Sostituisci il blocco che crea `enrichment_processor`, chiama `_build_execution_runtime`, crea i task e il blocco `finally` con il codice seguente.

**Crea gli eventi e il callback fill** (inserisci subito prima di `enrichment_processor = ...`):

```python
new_enriched_event = asyncio.Event()
new_fill_event = asyncio.Event()
_main_loop = asyncio.get_running_loop()

def _fill_wake_callback() -> None:
    _main_loop.call_soon_threadsafe(new_fill_event.set)
```

**Aggiorna la costruzione di `enrichment_processor`**:

```python
enrichment_processor = SignalEnrichmentProcessor(
    config_loader=OperationConfigLoader(config_dir),
    repository=EnrichedCanonicalMessageRepository(parser_db_path),
    on_pass=new_enriched_event.set,
)
```

**Aggiorna la chiamata a `_build_execution_runtime`** (aggiungi `wake_callback`):

```python
try:
    execution_runtime = _build_execution_runtime(
        root_dir=root_dir,
        ops_db_path=ops_db_path,
        logger=logger,
        wake_callback=_fill_wake_callback,
    )
except Exception:
    logger.exception("execution gateway init failed — gateway disabled")
```

**Sostituisci la creazione dei task** (il blocco `worker_task = ...` fino alla fine del `try` prima del `finally`):

```python
worker_task = asyncio.create_task(listener.run_worker())

lifecycle_task = asyncio.create_task(
    _run_lifecycle_workers(
        new_enriched_event=new_enriched_event,
        new_fill_event=new_fill_event,
        gate_worker=gate_worker,
        timeout_worker=timeout_worker,
        lifecycle_event_worker=lifecycle_event_worker,
        execution_runtime=execution_runtime,
        logger=logger,
    )
)

sync_task = None
if execution_runtime is not None:
    sync_task = asyncio.create_task(
        _run_sync_worker(
            sync_worker=execution_runtime.sync_worker,
            logger=logger,
        )
    )

reconciliation_task = None
position_reconciliation_task = None
if (
    execution_runtime is not None
    and execution_runtime.reconciliation_interval_seconds is not None
):
    reconciliation_task = asyncio.create_task(
        _run_reconciliation_periodically(
            sync_worker=execution_runtime.sync_worker,
            interval_seconds=execution_runtime.reconciliation_interval_seconds,
            logger=logger,
        )
    )
if execution_runtime is not None:
    position_reconciliation_task = asyncio.create_task(
        _run_position_reconciliation_periodically(
            sync_worker=execution_runtime.sync_worker,
            interval_seconds=60,
            logger=logger,
        )
    )
try:
    await client.run_until_disconnected()
finally:
    worker_task.cancel()
    lifecycle_task.cancel()
    if sync_task is not None:
        sync_task.cancel()
    if reconciliation_task is not None:
        reconciliation_task.cancel()
    if position_reconciliation_task is not None:
        position_reconciliation_task.cancel()
```

- [ ] **Step 6: Esegui la suite runtime_v2 completa**

```
pytest tests/runtime_v2/ -q --tb=short
```

Atteso: 400+ PASSED, ≤16 skipped (invariato rispetto al baseline).

- [ ] **Step 7: Commit**

```
git add main.py
git commit -m "feat(main): event-driven lifecycle wake-up, split sync_worker to separate task"
```

---

## Task 4: Verifica finale

- [ ] **Step 1: Esegui l'intera suite**

```
pytest tests/ -q --tb=short
```

Atteso: tutti i test PASSED (nessuna regressione).

- [ ] **Step 2: Verifica import puliti**

```
python -c "import main; print('OK')"
```

Atteso: `OK` senza errori di import.

- [ ] **Step 3: Commit finale**

```
git add -A
git commit -m "chore: final cleanup after event-driven lifecycle wake-up"
```

Se non ci sono file non committati, salta questo step.
