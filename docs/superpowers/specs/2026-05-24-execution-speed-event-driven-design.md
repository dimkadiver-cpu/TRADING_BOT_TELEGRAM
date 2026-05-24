# Design: Event-Driven Lifecycle Wake-Up

**Data:** 2026-05-24  
**Obiettivo:** Ridurre la latenza end-to-end dal segnale Telegram all'ordine exchange, e dal fill WebSocket alla risposta lifecycle (BE, close, TP successivi), passando da polling cieco a 10s a wake-up event-driven.

---

## Problema

Il loop `_run_lifecycle_workers()` in `main.py` esegue tutti i worker lifecycle e poi dorme 10 secondi fissi:

```python
while True:
    gate_worker.run_once()
    lifecycle_event_worker.run_once()
    execution_worker.run_once()
    sync_worker.run_once()
    await asyncio.sleep(10)   # ← collo di bottiglia
```

**Latenze risultanti (worst case / media):**

| Percorso | Prima |
|---|---|
| SIGNAL/UPDATE PASS → ordine inviato | 10s / ~5s |
| WS fill → BE/close cmd → exchange | 10–20s / ~10s |

Il `BybitWsFillWatcher` riceve i fill in real-time via WebSocket, ma il `LifecycleEventWorker` li elabora solo al prossimo tick del loop. Stesso problema per segnali nuovi.

---

## Soluzione: Event-Driven Wake-Up

### Principio

Due `asyncio.Event` segnalano al loop lifecycle che c'è lavoro da fare. Il loop aspetta uno dei due eventi (oppure un timeout di fallback a 10s), poi esegue i worker immediatamente.

```
new_enriched_event  ← settato da SignalEnrichmentProcessor dopo ogni PASS
new_fill_event      ← settato da BybitWsFillWatcher dopo ogni fill scritto in DB
```

Il `ExchangeEventSyncWorker` (chiamate REST Bybit) viene separato in un task indipendente a ~8s per evitare rate limiting.

---

## Architettura

### Flusso hot path

```
Telegram msg
  ↓ (listener, event-driven, ms)
parse + enrich → DB
  ↓ new_enriched_event.set()
_run_lifecycle_workers si sveglia
  ↓ gate_worker.run_once()
TradeChain + ExecutionCommands scritti in DB
  ↓ execution_worker.run_once()
Ordine inviato a Bybit (< 200ms totali)

WS fill ricevuto
  ↓ BybitWsFillWatcher.insert_exchange_event()
  ↓ new_fill_event.set()
_run_lifecycle_workers si sveglia
  ↓ lifecycle_event_worker.run_once()
ENTRY_FILLED/TP_FILLED/SL_FILLED processato → BE/close cmd
  ↓ execution_worker.run_once()
Comando inviato a Bybit (< 200ms totali)
```

---

## Componenti modificati

### 1. `SignalEnrichmentProcessor`

**File:** `src/runtime_v2/signal_enrichment/processor.py`

Aggiunta parametro opzionale `on_pass: Callable[[], None] | None = None`.  
Chiamato dopo ogni salvataggio con `enrichment_decision == "PASS"` (sia SIGNAL che UPDATE).

```python
class SignalEnrichmentProcessor:
    def __init__(
        self,
        config_loader: OperationConfigLoader,
        repository: EnrichedCanonicalMessageRepository,
        on_pass: Callable[[], None] | None = None,
    ) -> None:
        self._on_pass = on_pass

    def process(self, parse_result: CanonicalParseResult) -> EnrichedCanonicalMessage:
        ...
        # dopo repository.save(enriched):
        if enriched.enrichment_decision == "PASS" and self._on_pass:
            self._on_pass()
        return result
```

`asyncio.Event.set()` è thread-safe: può essere chiamato dal thread del listener senza lock aggiuntivi.

**Non notificato:** `BLOCK`, `REVIEW` — quei messaggi hanno già `lifecycle_processed=1`, il worker non deve elaborarli.

---

### 2. `BybitWsFillWatcher`

**File:** `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/ws_fill_watcher.py`

Aggiunta parametro opzionale `wake_event: asyncio.Event | None = None`.  
Settato dopo ogni `INSERT` riuscito in `ops_exchange_events`.

```python
class BybitWsFillWatcher:
    def __init__(
        self,
        ...,
        wake_event: asyncio.Event | None = None,
    ) -> None:
        self._wake_event = wake_event

    def _on_fill(self, fill) -> None:
        self._repo.insert_exchange_event(...)
        if self._wake_event:
            self._wake_event.set()
```

Il watcher gira già in un task asyncio separato — `set()` è sicuro.

---

### 3. `main.py`

#### Helper `_wait_any`

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

#### Loop lifecycle hot (sostituisce `_run_lifecycle_workers`)

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

`timeout_worker` rimane nel loop hot perché è solo SQLite (nessuna rete) e deve girare anche sul fallback tick per non perdere scadenze `WAITING_ENTRY`.

#### Loop sync REST separato

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

#### Wiring in `_async_main`

```python
new_enriched_event = asyncio.Event()
new_fill_event     = asyncio.Event()

enrichment_processor = SignalEnrichmentProcessor(
    config_loader=OperationConfigLoader(config_dir),
    repository=EnrichedCanonicalMessageRepository(parser_db_path),
    on_pass=new_enriched_event.set,
)

# In _build_execution_runtime: passa wake_event=new_fill_event a BybitWsFillWatcher

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
```

`sync_task` va cancellato nel blocco `finally` insieme agli altri task.

---

## Latenza attesa dopo la modifica

| Evento | Prima | Dopo |
|---|---|---|
| SIGNAL PASS → ordine inviato | ~5s media, 10s worst | < 200ms |
| UPDATE PASS → comando exchange | ~5s media, 10s worst | < 200ms |
| WS fill → BE/close cmd → exchange | ~5–15s | < 200ms |
| Timeout WAITING_ENTRY scaduto | fino a 10s | fino a 10s (invariato, ok) |
| REST sync reconciliation | ogni 10s | ogni 8s |

---

## File toccati

| File | Tipo modifica |
|---|---|
| `src/runtime_v2/signal_enrichment/processor.py` | +`on_pass` callback opzionale |
| `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/ws_fill_watcher.py` | +`wake_event` opzionale |
| `main.py` | crea eventi, riscrive loop lifecycle, aggiunge loop sync separato |

---

## Test

- `SignalEnrichmentProcessor`: test esistenti invariati (parametro opzionale, default `None`). Aggiungere test che verifica che `on_pass` venga chiamato su PASS e non su BLOCK/REVIEW.
- `BybitWsFillWatcher`: aggiungere test che verifica `wake_event.set()` chiamato dopo fill insert.
- `main.py`: integration test esistenti (`test_acceptance.py`, `test_integration.py`) invariati — nessuna modifica ai contratti.

---

## Vincoli e rischi

| Rischio | Mitigazione |
|---|---|
| `asyncio.Event.set()` da thread listener | Thread-safe per design di asyncio |
| Loop che gira troppo spesso (burst di segnali) | I worker sono idempotenti e veloci su batch vuoti; nessun danno |
| `sync_worker` separato non cancellato al shutdown | Aggiungere `sync_task.cancel()` nel blocco `finally` di `_async_main` |
| `new_fill_event` non disponibile se WS non abilitato | `wake_event=None` di default — il loop usa solo `new_enriched_event` + fallback timeout |
