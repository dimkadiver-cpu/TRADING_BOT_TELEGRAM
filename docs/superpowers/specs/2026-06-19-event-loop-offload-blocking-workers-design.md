# Design: Offload dei worker bloccanti fuori dall'event loop

**Data:** 2026-06-19
**Stato:** design — in attesa di approvazione
**Topic:** eliminare la starvation dell'event loop causata da chiamate ccxt sincrone nei worker

---

## 1. Problema e causa radice (verificata)

Tutto il runtime gira su **un singolo event loop** (`main_linux_server.py`: client Telethon,
worker lifecycle, reconciliation, sync, dispatcher notifiche, bot comandi). I worker di
esecuzione vengono invocati **sincroni dentro coroutine**, quindi bloccano il loop:

```python
async def _run_reconciliation_periodically(...):
    while True:
        await asyncio.sleep(interval)
        sync_worker.run_reconciliation()           # SINCRONO sul loop
async def _run_position_reconciliation_periodically(...):
        sync_worker.run_position_reconciliation()  # SINCRONO sul loop
async def _run_sync_worker(...):
        sync_worker.run_once()                     # SINCRONO sul loop
# loop lifecycle:
        gate_worker.run_once(); timeout_worker.run_once()
        lifecycle_event_worker.run_once(); execution_worker.run_once()  # SINCRONI
```

`run_reconciliation` / `run_position_reconciliation` (in `event_sync.py`) **iterano su tutte
le posizioni/chain aperte** e per ciascuna fanno una chiamata **ccxt bloccante** verso Bybit
(`get_order_status`, `get_position_qty_with_details`, `fetch_recent_reduce_trades`,
`fetch_position_details`…). Con N posizioni aperte, ogni ciclo congela il loop per
`N × round-trip Bybit`.

### Evidenza (log server 2026-06-19)
- Battito `getUpdates` (che dovrebbe essere ~10-12s) con **gap crescenti**: 16s (14:00) →
  65s (15:46) → 98s (15:57) → **139s (16:04)**. Il loop è fermo per minuti, sempre di più.
- Test di rete lato server: connect+TLS a `api.telegram.org` in **~50ms, stabilissimo** →
  la rete NON è il problema.
- Quindi i `ConnectTimeout` delle notifiche e l'esecuzione lenta sono **starvation del loop**.
- Cresce nel tempo perché cresce il **numero di posizioni aperte** (non il carico di
  messaggi) → coerente con "persiste a basso carico".

I fix precedenti (deadline anti-spin, WAL, keep-alive/timeout) sono corretti ma **secondari**.
Questa è la causa **dominante**.

---

## 2. Obiettivo / Non-obiettivo

**Obiettivo**
- Tenere l'event loop **sempre libero**: nessuna chiamata ccxt/bloccante eseguita sul loop.
- Spostare le chiamate bloccanti dei worker in **thread**.
- Preservare la **sicurezza per-adapter**: mai due chiamate ccxt concorrenti sullo stesso
  client (ccxt sync non è concurrency-safe).
- Preservare il parallelismo **tra adapter diversi** (la reconciliation di un account non
  deve bloccare gli ordini di un altro).

**Non-obiettivo**
- Non riscrivere gli adapter in async, non introdurre un nuovo gateway, non toccare la
  logica di reconciliation/gateway. L'intervento è di **orchestrazione** (`main_linux_server.py`).
- Non cambiare il comportamento funzionale dei worker.

---

## 3. Vincoli di topologia (verificati)

- `adapter_registry`: adapter per nome (`bybit_demo_1`, `bybit_demo_2`, `bybit_demo_3`).
  **`bybit_demo_1` è condiviso** da più routing.
- `sync_workers[account_id]` → un `ExchangeEventSyncWorker` per account, ognuno con
  `adapter = route_adapter` (istanza condivisa per gli account che puntano allo stesso adapter).
- Un solo `ExecutionCommandWorker` (`execution_worker`) col `gateway`, che instrada per
  account → usa gli **stessi** adapter dei sync worker.
- I worker aprono connessioni SQLite **per-chiamata** → sicure se eseguite in thread
  (nessuna connessione condivisa fra thread).

Conseguenza: la serializzazione deve essere **per-adapter** (chiave = nome adapter), perché
più account possono condividere un adapter e il command worker tocca gli stessi adapter.

---

## 4. Approcci considerati

### Approccio A — Offload in to_thread + lock asyncio per-adapter (RACCOMANDATO)
- Le coroutine periodiche eseguono la chiamata bloccante via `await asyncio.to_thread(...)`.
- Un `dict[str, asyncio.Lock]` per **nome adapter**: prima di ogni `to_thread` si fa
  `async with adapter_lock[name]:`. L'`asyncio.Lock` serializza le coroutine, quindi le
  rispettive `to_thread` non si sovrappongono **sullo stesso adapter**; adapter diversi
  restano paralleli.
- Tutto il locking vive nel **layer di orchestrazione** (`main_linux_server.py`); i worker
  restano invariati.
- **Pro**: minimale, owner-layer, nessuna modifica alla logica execution, parallelismo
  inter-adapter preservato. **Contro**: il command worker tocca più adapter in un
  `run_once` (vedi §5) — va gestito.

### Approccio B — Executor single-thread per adapter
- Ogni adapter ha un `ThreadPoolExecutor(max_workers=1)`; tutte le sue chiamate passano di lì
  → serializzazione naturale per-adapter, loop libero.
- **Pro**: robusto, serializzazione garantita anche se in futuro qualcuno chiama l'adapter
  da altri punti. **Contro**: più invasivo (l'adapter o i call-site devono usare l'executor),
  più codice.

**Raccomandazione: Approccio A**, con la gestione del command worker descritta sotto.

---

## 5. Design (Approccio A)

### 5.1 Lock per-adapter
In `main_linux_server.py`, al boot:
```python
adapter_locks = {name: asyncio.Lock() for name in adapter_registry}
# mappa account_id -> nome adapter, per risolvere il lock dal worker per-account
account_to_adapter = {account_id: route_adapter_name ...}
```

### 5.2 Worker periodici (reconciliation / position-reconciliation / sync)
```python
async def _run_reconciliation_periodically(sync_worker, interval, lock, logger):
    while True:
        await asyncio.sleep(interval)
        try:
            async with lock:
                await asyncio.to_thread(sync_worker.run_reconciliation)
        except Exception:
            logger.exception(...)
```
Idem per `run_position_reconciliation` e `_run_sync_worker.run_once`. Il `lock` passato è
`adapter_locks[account_to_adapter[account_id]]`.

### 5.3 Command worker (tocca più adapter)
`execution_worker.run_once()` processa un batch e instrada per account → può toccare più
adapter. Due opzioni, in ordine di preferenza:

1. **Granularità per-comando con lock per-adapter** (preferita): spostare il command worker
   in una sua coroutine che processa i comandi **raggruppati per account**, acquisendo
   `adapter_locks[account_to_adapter[account_id]]` attorno al gruppo, ed eseguendo il
   processing del gruppo in `to_thread`. Richiede un piccolo metodo sul worker per processare
   "solo gli account X" o l'iterazione lato orchestrazione.
2. **Fallback minimale**: `await asyncio.to_thread(execution_worker.run_once)` **senza** lock,
   MA serializzato con le reconciliation tramite un lock unico "execution" — semplice, però
   serializza tutti gli adapter (accettabile come step intermedio, da misurare). Da evitare se
   penalizza l'apertura ordini.

Decisione di dettaglio (1 vs 2) da fissare in fase di piano dopo aver letto `gateway.process`
e `get_pending_batch` (se il batch è già naturalmente per-account, la 1 è semplice).

### 5.4 Lifecycle workers (gate / timeout / lifecycle_event)
Sono prevalentemente **DB** (veloci sotto WAL), non ccxt. Restano sul loop in prima battuta;
se la misura post-intervento mostra freeze residui imputabili a loro, si offload anche questi
in `to_thread` (senza lock adapter, perché non toccano l'exchange) come step successivo.

---

## 6. File toccati

| File | Modifica |
|---|---|
| `main_linux_server.py` | `adapter_locks` + `account_to_adapter`; `_run_reconciliation_periodically`, `_run_position_reconciliation_periodically`, `_run_sync_worker` usano `async with lock: await asyncio.to_thread(...)`; command worker offload (opz. 1) |
| (event_sync/command_worker) | **Invariati** se si sceglie la granularità via orchestrazione; piccolo helper sul command worker solo se serve l'opzione 1 |

Nessuna migration, nessuno schema, nessun cambiamento al control plane.

---

## 7. Testing

- **Serializzazione per-adapter**: test che, dato un adapter finto la cui chiamata dorme e
  registra inizio/fine, due worker sullo stesso adapter **non si sovrappongono**, mentre su
  adapter diversi **sì** (parallelismo).
- **Loop libero**: test che durante una chiamata worker lenta (sleep in thread) una coroutine
  concorrente (es. un tick) continua a girare (non starved).
- **Equivalenza funzionale**: i worker producono lo stesso risultato chiamati via `to_thread`
  (smoke su reconciliation con adapter finto).
- Suite esistenti di `execution_gateway` verdi.

---

## 8. Rischi / rollout

- **Thread-safety SQLite**: i worker aprono connessioni per-chiamata → ok in thread. Verificare
  che nessun oggetto SQLite venga condiviso tra loop e thread.
- **Condivisione adapter**: il lock è per **nome adapter**, non per account → copre il caso
  `bybit_demo_1` condiviso.
- **Ordine/timing**: l'offload non cambia gli intervalli; cambia solo *dove* gira la chiamata.
- **WS fill watcher** (`BybitWsFillWatcher`): verificare se tocca lo stesso adapter; se sì,
  includerlo nello schema di lock.
- Rollback: ripristinare le chiamate sincrone dirette.
- **Validazione primaria post-deploy**: i gap di `getUpdates` tornano ~10-12s costanti anche
  con molte posizioni aperte; spariscono i ConnectTimeout; esecuzione scattante.

---

## 9. Decisione aperta per il piano
- Command worker: opzione 1 (per-account) vs 2 (lock unico execution) — da fissare dopo lettura
  di `gateway.process` / `get_pending_batch`.
- Lifecycle workers: offload ora o solo se la misura post-intervento lo richiede (preferito:
  dopo misura).
