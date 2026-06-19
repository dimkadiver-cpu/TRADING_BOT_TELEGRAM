# Design: Contesto di esecuzione per-adapter (Opzione A)

**Data:** 2026-06-19
**Stato:** design — in attesa di approvazione
**Supersede:** `2026-06-19-event-loop-offload-blocking-workers-design.md` (bozza tattica di offload)
**Topic:** isolare tutto il lavoro REST bloccante (ccxt) in un contesto per-adapter, tenendo
l'event loop libero; modello che assorbe i worker attuali e quelli futuri (price-follow).

---

## 1. Problema e causa radice (verificata)

Tutto il runtime gira su **un singolo event loop**. I worker di esecuzione invocano
chiamate **ccxt REST sincrone** (`get_order_status`, `get_position_qty_with_details`,
`fetch_recent_reduce_trades`, `fetch_position_details`, piazzamento ordini) **direttamente
sul loop** → il loop si congela per `N_posizioni × round-trip Bybit`.

**Evidenza (log server 2026-06-19):** battito `getUpdates` con gap crescenti 16s→139s; test
di rete `connect+TLS` a Telegram in ~50ms stabile (rete sana). Quindi i timeout notifiche e
l'esecuzione lenta sono **starvation del loop**, e crescono col **numero di posizioni aperte**
(non col carico di messaggi). I fix già fatti (deadline anti-spin, WAL, keep-alive) sono
corretti ma **secondari**: questa è la causa dominante.

---

## 2. Principio architetturale

Il pattern del sistema è sano e si conserva:
- **WS async (`ccxt.pro`, client dedicato)** = via veloce dei fill → **resta sul loop**, non blocca.
- **REST sincrono (`ccxt`, l'adapter)** = rete di sicurezza + piazzamento ordini → **bloccante**.

Il difetto è solo che il REST bloccante gira sul loop. La cura: **ogni adapter (account
exchange) ha un proprio contesto di esecuzione isolato** (un thread dedicato) che possiede
**tutto** il lavoro REST bloccante verso quell'exchange. L'event loop non fa mai chiamate REST.

### Unità di isolamento = adapter (account), non trader
Routing attuale (conservato): `trader → account (account_routing) → adapter (client ccxt)`.
Più trader possono condividere un adapter (es. `bybit_demo_1`). Quindi **il numero di contesti
= numero di adapter**, non di trader. Scala: decine di adapter per processo → decine di thread
(ok). Crescita oltre = nuovi **processi/bot** (vedi §9, evoluzione verso isolamento di processo).

### ccxt sincrono, niente migrazione async
A queste scale (decine) si resta su **ccxt sincrono** isolato nel contesto per-adapter. Niente
migrazione a `ccxt.async_support` (cantiere sproporzionato, rischioso su soldi veri).

---

## 3. Il contesto per-adapter (modello ad attore)

### 3.1 `AdapterExecutionContext` (nuovo)
Un oggetto per adapter, con:
- **un thread dedicato** che esegue un **loop interno**;
- una **coda di job** (callable) thread-safe (`queue.Queue`);
- **timer periodici interni** per i tick (reconciliation, position-recon, price-follow);
- esecuzione **strettamente seriale** nel suo thread → mai due chiamate ccxt concorrenti sullo
  stesso client (ccxt-safe per costruzione, senza lock sparsi).

Responsabilità:
- esegue i **job event-driven** accodati dal loop (es. nuovo comando da piazzare, reconciliation
  innescata dal WS);
- esegue i **tick periodici** sulle proprie cadenze;
- al termine di un job che cambia stato, **sveglia** il loop asyncio (lifecycle/dispatcher) in
  modo thread-safe.

### 3.2 Confine thread ↔ loop (regole)
- **Loop → contesto**: `context.submit(job)` mette il job in coda (non bloccante). Nessuna
  chiamata ccxt sul loop.
- **Contesto → loop**: per svegliare un `asyncio.Event` (es. `new_fill_event`) dal thread si usa
  **`loop.call_soon_threadsafe(event.set)`**. Mai toccare oggetti asyncio direttamente dal thread.
- **SQLite**: i worker aprono connessioni **per-chiamata** → ogni connessione vive nel thread del
  contesto. Nessun oggetto SQLite condiviso fra loop e thread. (WAL già attivo: scritture rapide.)
- **WS watcher**: client `ccxt.pro` **separato** (api_key proprie) → nessun conflitto col client
  REST del contesto. Il `reconciliation_callback` del WS **accoda** un job al contesto
  (`context.submit(...)`), non esegue REST sul loop.

### 3.3 Cadenze e consolidamento worker
I worker attuali diventano **tick/job dentro il contesto**, eliminando le ridondanze:

| Oggi | Domani (nel contesto per-adapter) |
|---|---|
| `_run_sync_worker.run_once` (8s) **+** `_run_reconciliation_periodically` (60s), entrambi = `run_reconciliation` | **un solo** tick `reconciliation` a cadenza unica (default 8s) — elimina il doppione |
| `_run_position_reconciliation_periodically` → `run_position_reconciliation` + `run_trade_based_reconciliation` + `run_protective_orders_reconciliation` (≈600s) | tick `position_reconciliation` (≈600s) che esegue i tre in sequenza, nel thread |
| `execution_worker.run_once` (command, event-driven, nel lifecycle loop) | job `process_commands` accodato quando arrivano comandi (o tick breve) |
| WS `reconciliation_callback` | job accodato al contesto |
| **futuro** price-follow / cancel-stale | nuovo tick `price_follow` (~1–2s) nel contesto — legge prezzo via REST e valuta trailing / chiusura ordini pendenti non filati |

Cadenze configurabili per adapter (riuso dei valori già in `execution.yaml`:
`poll_fallback_period_seconds`, `position_reconciliation_interval_seconds`).

---

## 4. Cosa resta sul loop (invariato)
- Telethon listener, parsing/enrichment, lifecycle DB-only (gate/timeout/lifecycle_event),
  dispatcher notifiche, control bot, **WS fill watcher** (async).
- Nota: i lifecycle worker gate/timeout/lifecycle_event sono **solo DB** (veloci sotto WAL) →
  restano sul loop; offload solo se una misura futura mostrasse freeze residui.

---

## 5. Orchestrazione (`main_linux_server.py`)
- Al boot, per ogni adapter in `adapter_registry`: creare un `AdapterExecutionContext` e
  **avviare il suo thread**.
- Sostituire le coroutine periodiche bloccanti (`_run_reconciliation_periodically`,
  `_run_position_reconciliation_periodically`, `_run_sync_worker`) con i tick **interni** ai
  contesti. Le coroutine asyncio residue per execution si limitano a **accodare** job
  event-driven (es. su `new_fill_event` / nuovi comandi).
- Il command worker: passa da chiamata sincrona nel lifecycle loop ad **enqueue** verso il
  contesto dell'adapter corretto (risolto da `account_to_adapter`).
- Shutdown ordinato: segnalare stop ai contesti e `join()` dei thread.

Mapping necessari: `adapter_name → AdapterExecutionContext` e `account_id → adapter_name`.

---

## 6. File toccati

| File | Modifica |
|---|---|
| `src/runtime_v2/execution_gateway/adapter_context.py` *(nuovo)* | `AdapterExecutionContext`: thread, coda job, tick periodici, submit, shutdown, wakeup thread-safe |
| `main_linux_server.py` | costruzione contesti per adapter; rimozione coroutine bloccanti; enqueue command/WS-recon; shutdown |
| `event_sync.py` / `command_worker.py` | **invariati nella logica**; vengono solo *invocati* dal contesto invece che dal loop (eventuale piccolo adattamento di firma se serve raggruppare i comandi per adapter) |

Nessuna migration, nessuno schema, nessun cambiamento al control plane o al WS watcher.

---

## 7. Testing (TDD)

- **Serializzazione per-adapter**: con un adapter finto la cui chiamata dorme e registra
  start/end, due job sullo stesso contesto **non si sovrappongono**; contesti di adapter diversi
  **sì** (parallelismo).
- **Loop non bloccato**: mentre un job del contesto è "lento" (sleep nel thread), una coroutine
  asyncio concorrente continua a girare (tick di prova non starved).
- **Wakeup thread-safe**: un job che chiama il wakeup fa `set()` dell'`asyncio.Event` via
  `call_soon_threadsafe` e la coroutine in attesa si risveglia.
- **Equivalenza funzionale**: reconciliation/position-recon/command danno lo stesso risultato
  eseguiti nel contesto (smoke con adapter finto + DB temporaneo).
- **Cadenza unica reconciliation**: verificare che non esista più il doppio invio 8s/60s.
- Suite esistenti `execution_gateway` verdi.

---

## 8. Rischi / rollout

- **Cross-thread asyncio**: unico punto critico → centralizzato nella regola
  `call_soon_threadsafe`. Vietato toccare `asyncio.Event`/loop dal thread direttamente.
- **Stato per-worker non thread-safe** (`_position_zero_count` in `event_sync`): sicuro perché un
  adapter è servito da **un solo thread** (serializzazione naturale).
- **Adapter condiviso** (`bybit_demo_1`): un contesto per **adapter** copre tutti gli account che
  lo condividono.
- **Ordine di shutdown**: drenare/joinare i thread prima di chiudere il loop.
- **WS↔REST**: client separati → nessun lock necessario tra loro; la callback WS accoda soltanto.
- Rollback: riattivare le coroutine sincrone dirette (i worker restano invariati).
- **Validazione primaria post-deploy**: gap `getUpdates` ~10–12s costanti anche con molte
  posizioni; spariscono i ConnectTimeout; esecuzione scattante; reconciliation invariata nei
  risultati.

---

## 9. Evoluzione (non in scope ora, ma il modello la prepara)
- **Più bot/processi** (tuo piano di scala): gruppi di adapter su processi separati. Il command
  outbox sul DB (`ops_execution_commands`) **già disaccoppia** produzione ed esecuzione → il
  passaggio da "contesto-thread per adapter" a "processo per gruppo di adapter" non richiede di
  riscrivere la logica. Opzione A ora **non chiude la porta** all'isolamento di processo (Opzione C).
- **price-follow reattivo**: se in futuro servisse sub-secondo, il tick REST ~1–2s può essere
  affiancato da un feed prezzo WS (`ccxt.pro`), sempre dentro il contesto per-adapter.

---

## 10. Decisioni aperte per il piano
1. **Scheduler dei tick**: timer interni al thread del contesto (preferito) vs coroutine asyncio
   che accodano i tick. Preferenza: timer interni → zero lavoro periodico sul loop.
2. **Command worker**: enqueue per-account con raggruppamento dei comandi per adapter — verificare
   `get_pending_batch`/`gateway.process` per la firma esatta.
3. **Cadenza unica reconciliation**: valore di default (8s) e se renderlo per-adapter da config.
