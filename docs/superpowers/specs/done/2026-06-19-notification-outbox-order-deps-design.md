# Design: Notification Outbox — Ordine per-chain + Dipendenze con deadline + Robustezza

**Data:** 2026-06-19
**Stato:** in design — Opzione A approvata
**Topic:** sistema universale per ordine ed eventi dipendenti nel control-plane notification dispatcher

---

## 0. Contesto e problema

Il control plane invia le notifiche Telegram (CLEAN_LOG, TECH_LOG, COMMANDS) tramite un
outbox in `ops.sqlite3` drenato ogni ~2s da `TelegramNotificationDispatcher.drain_once`.

Diagnosi della lentezza osservata (notifiche e risposte ai comandi):

1. **Trigger** — commit `7ce699f` ("open prima del signal / chain 32") ha aggiunto in
   `drain_once`:
   ```python
   if root_msg_id is None:
       self._requeue_pending(notification_id)   # rimette a PENDING
       continue
   ```
   Un evento clean-log figlio (ENTRY_OPENED, TP, SL, CLOSE) il cui `SIGNAL_ACCEPTED` root non
   ha ancora un `message_id` viene **rimesso a PENDING e riprovato ogni 2s, senza limite e
   senza via d'uscita**. Se il root non si risolve mai (timeout/rifiuto), gli eventi
   dipendenti girano all'infinito. Ogni giro = `sqlite3.connect` + `UPDATE` + `commit` =
   lock di scrittura sull'intero DB, ogni 2s.

2. **Amplificatore** — tutto gira su un **singolo event loop** (`main_linux_server.py`:
   client Telethon, worker lifecycle, dispatcher, bot) e ogni I/O SQLite è **sincrono e
   bloccante** sul loop. Inoltre `ops.sqlite3` è in journal mode **`delete`, NON WAL**
   (verificato: `PRAGMA journal_mode = delete`), quindi ogni scrittura prende un lock
   sull'intero DB e i contendenti bloccano fino a `busy_timeout = 5s`. Un loop congelato
   fa scadere i timeout HTTP wall-clock di HTTPX → `ConnectTimeout` / `Timed out` (22
   notifiche FAILED nel DB del server, tutte "Timed out").

3. **Ordine fragile** — l'ordine per-chain è già un requisito di fatto, ma ottenuto con
   tre meccanismi sovrapposti: `created_at` wall-clock, "promozione-priorità"
   (`outbox_writer.py:190`), e i due sistemi di dipendenza ad-hoc (root-link requeue +
   pending multi-chain summary).

### Scoperta chiave (riduce il costo del fix)

Una **sequenza monotona per-chain esiste già**, non va creata:

- `ops_lifecycle_events.event_id` è `INTEGER PRIMARY KEY AUTOINCREMENT` (cursore di
  proiezione, vedi `ops_trade_chains.last_projected_event_id`).
- Le righe clean-log vengono scritte **durante la proiezione lifecycle** (in ordine di
  `event_id`), quindi il loro `ops_notification_outbox.notification_id` (PK, monotono in
  ordine di insert) è **già nell'ordine corretto** dentro ogni chain.

Quindi l'ordine corretto si ottiene ordinando il claim per `notification_id` invece che
per `created_at`. **Nessun `sequence_no` nuovo, nessuna migration per l'ordine.**

---

## 1. Obiettivi / Non-obiettivi

**Obiettivi**
- Eliminare lo spin infinito del root-link: un evento dipendente o parte appena il root è
  pronto, o parte best-effort allo scadere di un deadline. Mai loop senza fine.
- Ordine per-chain deterministico via `notification_id`, eliminando la promozione-priorità.
- Una primitiva di dipendenza esplicita e data-driven (`depends_on` + `wait_deadline`),
  single-target (Opzione A).
- Rimuovere l'amplificatore: WAL su `ops.sqlite3`, I/O SQLite fuori dall'event loop,
  retry/backoff coerenti.
- Restare compatibili con lo spec dashboard/stats (`2026-06-19-commands-stats-dashboard-design.md`).

**Non-obiettivi (Opzione A)**
- NON unificare ora la dipendenza multi-target del `MULTI_CHAIN_SUMMARY`: resta com'è
  (`ops_pending_multi_chain_summaries` + `try_release...` + `send_after = now+3s`).
- NON toccare l'aggregazione "update con più azioni" (`write_engine_rule_update_clean_log`):
  è write-side e ortogonale.
- NON cambiare il contratto esterno dell'outbox (notification_type / destination / payload).

---

## 2. Architettura — due fasi indipendenti

- **Fase 1 — Ordine + dipendenze generiche** (sostituisce e generalizza il fix mirato).
- **Fase 2 — Esecuzione robusta** (WAL + non-blocking I/O + retry coerenti).

Sono indipendenti: la Fase 1 risolve il bug chain-32; la Fase 2 elimina la fragilità che
lo amplifica. Sequenza consigliata: **Fase 2 prima** (sblocca subito la latenza e de-risca
la dashboard), poi **Fase 1**.

---

## 3. Fase 1 — Ordine per-chain + `depends_on` con deadline

### 3.1 Schema — migration `017_ops_outbox_dependencies.sql`

```sql
-- Universal single-target dependency for outbox rows.
ALTER TABLE ops_notification_outbox ADD COLUMN depends_on    TEXT;   -- es. "clean_root:42"; NULL = nessuna
ALTER TABLE ops_notification_outbox ADD COLUMN wait_deadline TEXT;   -- ISO UTC; oltre questo → best-effort

-- Claim ordinato per insert-order (ordine per-chain garantito), filtrato su stato/send_after.
CREATE INDEX IF NOT EXISTS idx_outbox_claim
    ON ops_notification_outbox(status, send_after, notification_id);
```

`depends_on` è una stringa namespaced (`"clean_root:{chain_id}"`) così la primitiva è
estendibile in futuro (Opzione B) senza nuove colonne.

### 3.2 Lato scrittura — `outbox_writer.write_clean_log_event`

Per gli eventi CLEAN_LOG **non-SIGNAL** con `chain_id`:
- `depends_on = f"clean_root:{chain_id}"`
- `wait_deadline = now + ROOT_WAIT_SECONDS` (default **45s**)

Per i tipi SIGNAL (`SIGNAL_ACCEPTED`, `SIGNAL_REJECTED`, `REVIEW_REQUIRED`) e per gli altri
destination: `depends_on = NULL`, `wait_deadline = NULL`.

**Rimuovere** il blocco di promozione-priorità (`outbox_writer.py:187-195`): l'ordine non
dipende più dai bucket di priorità. La colonna `priority` resta solo come segnale di
urgenza opzionale, non come meccanismo d'ordine.

### 3.3 Lato dispatcher — `_claim_pending` e `drain_once`

**Claim** — ordinare per insert-order:
```sql
SELECT notification_id, notification_type, destination, payload_json, attempts,
       account_id, depends_on, wait_deadline
FROM ops_notification_outbox
WHERE status='PENDING'
  AND (send_after IS NULL OR send_after <= :now)
ORDER BY notification_id          -- era: priority, created_at, notification_id
LIMIT :batch
```

**Risoluzione dipendenza** in `drain_once`, al posto del requeue-spin attuale:

```text
se depends_on inizia con "clean_root:" e notification_type non è un SIGNAL_TYPE:
    root_msg_id = _get_clean_log_root(chain_id)
    se root_msg_id is None:
        se now < wait_deadline:
            _defer(notification_id, send_after = now + RETRY_INTERVAL)   # default 3s
            continue
        altrimenti:                       # DEADLINE SCADUTO
            _emit_root_missing_tech_log(chain_id, notification_id)       # WARNING in TECH_LOG
            # prosegui all'invio SENZA signal_link (best-effort)
    altrimenti:
        # root presente → pin chat/thread + aggiungi signal_link come oggi
```

Differenze rispetto a oggi:
- **Deadline**: oltre `wait_deadline` l'evento parte comunque (best-effort, niente link).
  Niente più spin infinito.
- **Meno churn**: il deferral usa `send_after = now + RETRY_INTERVAL` (riprovato tra ~3s),
  non un `UPDATE status='PENDING'` immediato ri-claimato ad ogni drain.
- **Ordine preservato**: una volta inviato il root, i figli deferiti tornano claimabili e
  vengono drenati per `notification_id` crescente → ordine corretto. Anche un
  `POSITION_CLOSED` deferito non può superare il proprio `ENTRY_OPENED`.

**Comportamento al deadline (deciso):** invio best-effort senza link **+ WARNING in
TECH_LOG** (`notification_type` nuovo: `CLEAN_LOG_ROOT_MISSING`, level WARNING, payload con
`chain_id` e `notification_type` originale). Vedi sezione 6.

### 3.4 Costanti

| Costante | Default | Significato |
|---|---|---|
| `ROOT_WAIT_SECONDS` | 45 | finestra d'attesa del root prima del best-effort |
| `RETRY_INTERVAL` | 3 | intervallo di re-check del root durante l'attesa |

---

## 4. Fase 2 — Esecuzione robusta

### 4.1 WAL + pragmas

- Migration `017` (o `018`) include `PRAGMA journal_mode=WAL;` su `ops.sqlite3` (persistente).
- Connection helper centrale `connect_ops(path)` che applica ad ogni connessione:
  `PRAGMA busy_timeout=5000; PRAGMA synchronous=NORMAL;`
  Sostituisce i `sqlite3.connect(self._ops_db)` sparsi nel control plane.

### 4.2 I/O fuori dall'event loop

- `TelegramControlBot._on_command`: eseguire `self._router.route(...)` (sincrono, fa query
  e audit DB) via `await asyncio.to_thread(...)` invece che inline sul loop.
- Dispatcher: incapsulare le sequenze DB di `drain_once` (claim/mark/tracking) in
  `asyncio.to_thread`, oppure mantenerle sincrone ma brevi sotto WAL (decisione in fase di
  piano, misurando). WAL da solo rimuove la maggior parte dei freeze.

### 4.3 Retry/backoff coerenti

- Mantenere `_MAX_ATTEMPTS`, `_FAILURE_BACKOFF_SECONDS` esistenti.
- Trattare `Timed out`/`ConnectTimeout` come errore transitorio (retry), già il caso oggi.

---

## 5. Cosa resta INVARIATO

- **Update con più azioni** → `write_engine_rule_update_clean_log` collassa N
  `ENGINE_RULE_UPDATE_ACCEPTED` in **un solo** `UPDATE_DONE` prima dell'outbox. Ortogonale,
  non toccato.
- **MULTI_CHAIN_SUMMARY** → `ops_pending_multi_chain_summaries` + `send_after = now+3s` +
  `try_release_pending_close_full_summaries()`. Resta com'è (Opzione A).
- **Contratto outbox** (`notification_type`, `destination`, `payload`) → invariato, quindi
  la dashboard (`on_trade_event` agganciato ai lifecycle event) non è impattata.

---

## 6. File toccati

### Nuovi
| File | Contenuto |
|---|---|
| `db/ops_migrations/017_ops_outbox_dependencies.sql` | colonne `depends_on`, `wait_deadline`, indice claim, WAL |
| `src/runtime_v2/control_plane/db.py` *(opz.)* | `connect_ops(path)` con pragmas |

### Modificati
| File | Modifica |
|---|---|
| `control_plane/outbox_writer.py` | set `depends_on`/`wait_deadline` per clean-log non-SIGNAL; **rimuovere** promozione-priorità |
| `control_plane/notification_dispatcher.py` | `_claim_pending` ORDER BY `notification_id` + select nuove colonne; sostituire requeue-spin con risoluzione dipendenza + deadline; `_defer()`; `_emit_root_missing_tech_log()`; rimuovere `_requeue_pending` |
| `control_plane/telegram_bot.py` | `route()` via `asyncio.to_thread` (Fase 2) |
| `control_plane/*` (vari) | usare `connect_ops()` + busy_timeout/synchronous (Fase 2) |

---

## 7. Testing (TDD)

Fase 1 — `tests/runtime_v2/control_plane/test_dispatcher.py`:
1. **Root assente, dentro deadline** → evento NON inviato, `send_after` posticipato, niente spin.
2. **Root arriva** → figli inviati in ordine di `notification_id`, con `signal_link`.
3. **Deadline scaduto, root mai arrivato** → evento inviato **senza link** + riga TECH_LOG
   `CLEAN_LOG_ROOT_MISSING` scritta.
4. **Ordine** → entry/update/tp/close della stessa chain inviati nell'ordine di insert anche
   se `created_at` collide o priorità differiscono; `POSITION_CLOSED` non supera `ENTRY_OPENED`.
5. **Regressione chain-32**: ENTRY_OPENED scritto prima che SIGNAL_ACCEPTED abbia message_id
   → non spinna, rispetta deadline.

Fase 2:
6. `connect_ops` apre in WAL con busy_timeout atteso.
7. (se to_thread) il comando risponde mentre il dispatcher è occupato (no starvation).

---

## 8. Migrazione / rollout

- Migration `017` idempotente e auto-applicata al boot (`apply_migrations`).
- `ALTER TABLE ADD COLUMN` su SQLite è non-distruttivo; righe esistenti hanno
  `depends_on = NULL` → trattate come "nessuna dipendenza" (invariate).
- WAL persistente una volta impostato; nessun downtime.
- Rollback: le colonne nuove sono ignorabili; ripristinare il vecchio ORDER BY e la
  promozione-priorità se necessario.

---

## 9. Compatibilità con lo spec dashboard/stats

- Schema: la dashboard aggiunge `ops_dashboard_messages` (tabella diversa) → nessun conflitto.
- La dashboard si aggancia ai **lifecycle event**, non all'outbox, e assume "dispatcher
  invariato": il contratto outbox non cambia → assunzione preservata.
- La Fase 2 (WAL + non-blocking) è **prerequisito consigliato** della dashboard, che
  aggiunge `edit_message_text` ad ogni evento sullo stesso loop.
- Sinergia futura (Opzione B): il throttle/refresh della dashboard e il deferral
  dell'outbox potrebbero condividere un unico scheduler. Fuori scope ora.
```
