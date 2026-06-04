# Audit Notifiche Telegram — Gap Analysis

Data: 2026-06-04  
Scope: `src/runtime_v2/` — tutti i percorsi che producono o NON producono notifiche Telegram.  
Riferimento spec: `runtime_v2_control_plane_messaggi.md`

---

## Metodo

Per ogni evento lifecycle generato dal runtime è stato verificato che:
1. l'evento arrivi a `project_clean_log_for_chain` o a una funzione di sintesi equivalente
2. sia presente in `_CLEAN_LOG_EVENT_MAP` (o scritto direttamente come sintetizzato)
3. il payload builder in `_build_payload` copra il tipo
4. il formatter in `clean_log.py` produca output leggibile

---

## Risultati

### GAP-1 — CRITICO: Timeout entry mai notificato (nome evento sbagliato)

**File**: `src/runtime_v2/lifecycle/workers.py:204`  
**Comportamento attuale**: `TimeoutWorker._process_timeout()` scrive l'evento lifecycle con `event_type="TIMEOUT_REACHED"`.  
**Spec (§2a)**: la mappa `_CLEAN_LOG_EVENT_MAP` in `outbox_writer.py:19` mappa `"PENDING_TIMEOUT"` → `"PENDING_ENTRY_EXPIRED"`.

`"TIMEOUT_REACHED"` non è presente nella mappa → l'evento viene scartato silenziosamente.  
`"PENDING_TIMEOUT"` non viene mai emesso da nessun file nel codebase.

**Effetto**: l'utente non riceve mai il messaggio `PENDING_ENTRY_EXPIRED` (§3.17) quando un ordine pending scade senza essere eseguito. Il formatter, il payload builder e la notifica esistono ma sono irraggiungibili.

**Fix**: in `workers.py:204` cambiare `"TIMEOUT_REACHED"` → `"PENDING_TIMEOUT"`.

```python
# PRIMA (workers.py:204)
(chain_id, "TIMEOUT_REACHED", "timeout_worker", ...)

# DOPO
(chain_id, "PENDING_TIMEOUT", "timeout_worker", ...)
```

Nessuna altra modifica necessaria: la mappa, il payload builder e il formatter sono già corretti.

---

### GAP-2 — CRITICO: Fallimenti esecuzione gateway mai notificati

**File**: `src/runtime_v2/execution_gateway/gateway.py` + `repositories.py:236`

`ExecutionGateway.process()` chiama `self._repo.mark_review_required()` in 7 casi distinti:

| Linea | Motivo |
|---|---|
| 126 | `adapter_not_found` |
| 133 | `live_trading_not_allowed_in_config` |
| 138 | `live_trading_env_gate_not_set` |
| 146 | `capability_missing:<cap>` |
| 159 | `deferred_market_no_mark_price` |
| 167 | `deferred_market_zero_risk_distance` |
| 195 | `open_position_qty_unavailable_for_close` |

`GatewayCommandRepository.mark_review_required()` (`repositories.py:236`) aggiorna solo il DB (`status='REVIEW_REQUIRED'`) **senza scrivere nessuna riga in `ops_notification_outbox`**.

Ugualmente `mark_failed()` + `cancel_chain_if_all_entries_failed()` in caso di adapter failure non emettono notifiche.

**Effetto**: ordini bloccati, esecuzione ferma, utente ignaro. Visibile solo via `/status` (contatore `failed_commands`) o `/reviews`, non proattivamente.

**Fix**: `GatewayCommandRepository.mark_review_required()` deve scrivere un evento TECH_LOG nel outbox tramite `write_tech_log_event`.  
Aggiungere connessione a `ops_db` e chiamata `write_tech_log_event` per i casi `adapter_not_found`, `capability_missing` e per tutti i casi che non sono transitori.

Alternativa più leggera: in `command_worker.py`, dopo `self._gw.process(cmd, account_id=account_id)`, chiamare `project_clean_log_for_chain(conn, cmd.trade_chain_id)` e aggiungere un evento `RECONCILIATION_WARNING` lifecycle quando il comando transisce in `REVIEW_REQUIRED`.

---

### GAP-3 — MEDIO: `CLOSE_PARTIAL_FILLED` da exchange mai notificato

**File**: `src/runtime_v2/lifecycle/event_processor.py:715-745`  
**Comportamento attuale**: `_process_close_partial_filled()` scrive l'evento lifecycle `"CLOSE_PARTIAL_FILLED"`.  
La mappa `_CLEAN_LOG_EVENT_MAP` in `outbox_writer.py` non contiene questa chiave.

**Effetto**: chiusure parziali generate dall'exchange (non da comando bot) — ad esempio un TP parziale gestito come `CLOSE_PARTIAL_FILLED` dal classificatore, o una chiusura parziale manuale rilevata dalla riconciliazione — sono invisibili su Telegram.

Nota: le chiusure parziali tramite comando bot (`U_CLOSE_PARTIAL`) sono notificate correttamente via `_write_update_clean_log` → `UPDATE_DONE` (percorso §2b). Il gap riguarda solo il percorso exchange→lifecycle.

**Fix**: aggiungere in `_CLEAN_LOG_EVENT_MAP`:
```python
"CLOSE_PARTIAL_FILLED": "POSITION_CLOSED",
```
e in `_build_payload` gestire il caso `POSITION_CLOSED` proveniente da `CLOSE_PARTIAL_FILLED` (aggiungere `close_reason` dal payload evento, es. `"PARTIAL_CLOSE"`).

---

### GAP-4 — MEDIO: `cancel_chain_if_all_entries_failed` scrive lifecycle ma non proietta su CLEAN_LOG

**File**: `src/runtime_v2/execution_gateway/repositories.py:177-234`

Quando tutti i comandi di entry falliscono, `cancel_chain_if_all_entries_failed()` scrive direttamente un evento `"PENDING_ENTRY_CANCELLED"` in `ops_lifecycle_events`.  
La catena viene portata in stato `CANCELLED` (terminale).

**Problema**: `project_clean_log_for_chain` viene chiamato da:
- `LifecycleEventWorker._persist_result()` — ma processa solo exchange events, e una catena `CANCELLED` viene skippata (linea 266)
- `LifecycleGateWorker._persist_signal/update()` — non riprocesserà questa catena

Nessuno dei due percorsi viene eseguito dopo la scrittura di `cancel_chain_if_all_entries_failed`. L'evento lifecycle `"PENDING_ENTRY_CANCELLED"` rimane non proiettato → nessuna notifica `ENTRY_CANCELLED`.

**Fix**: in `command_worker.py`, dopo la chiamata a `self._gw.process(cmd, account_id=account_id)`, verificare se il comando è stato contrassegnato come `FAILED` e chiamare `project_clean_log_for_chain`:

```python
# command_worker.py — dopo gw.process()
# Se la chain è stata cancellata, proietta i nuovi eventi
conn = sqlite3.connect(self._ops_db)
try:
    with conn:
        project_clean_log_for_chain(conn, cmd.trade_chain_id)
finally:
    conn.close()
```

---

### GAP-5 — BASSO: `ENTRY_CANCEL_FAILED` nella mappa è codice morto

**File**: `src/runtime_v2/control_plane/outbox_writer.py:21`

La mappa contiene `"ENTRY_CANCEL_FAILED": "CANCEL_FAILED"`.  
`"ENTRY_CANCEL_FAILED"` non viene emesso da nessun file nel codebase (grep completo confermato).

Il formatter `_cancel_failed()` in `clean_log.py:463` e il payload builder `outbox_writer.py:499` sono irraggiungibili.

**Scenario inteso (§3.7)**: cancellazione ordine fallita dopo 3 tentativi → notifica `CANCEL_FAILED`. Questo scenario esiste (cancellazione timeout fallisce su exchange) ma nessun percorso emette l'evento lifecycle.

**Fix** (da fare come task separato): il timeout worker o il gateway dovrebbe emettere `"ENTRY_CANCEL_FAILED"` quando un `CANCEL_PENDING_ENTRY` raggiunge `max_attempts` senza conferma. Al momento questo path non esiste nel codice.

---

### GAP-6 — NOTO (già in Osservazioni.md §2): Enrichment blocks non notificati

Già documentato in `Osservazioni.md` come gap residuo. `SignalEnrichmentProcessor` porta `enrichment_decision='BLOCK'` o `'REVIEW'` a `lifecycle_processed=True` senza passare dal lifecycle gate → nessuna notifica generata.

Soluzione necessaria: worker separato che scansiona `enriched_canonical_messages WHERE enrichment_decision IN ('BLOCK','REVIEW')` e scrive le notifiche appropriate.

**Non incluso nel fix proposto qui** — è un task separato come già indicato.

---

## Riepilogo

| # | Severità | Evento perso | Causa | Fix |
|---|---|---|---|---|
| GAP-1 | ~~CRITICO~~ **CHIUSO** | `PENDING_ENTRY_EXPIRED` (timeout) | Rename `TIMEOUT_REACHED` → `PENDING_TIMEOUT` in `workers.py:204` | ✅ |
| GAP-2 | CRITICO | Nessuna notifica per blocchi esecuzione | `mark_review_required` non scrive outbox | `write_tech_log_event` in repo o command_worker |
| GAP-3 | ~~MEDIO~~ **CHIUSO** | Chiusure parziali da bot | `PARTIAL_CLOSE_EXECUTED` — filtro `source=bot_command`, fill data con PnL | ✅ |
| GAP-4 | MEDIO | `ENTRY_CANCELLED` su all-entries-failed | `project_clean_log_for_chain` mai chiamato | Chiamare proiezione in `command_worker.py` |
| GAP-5 | BASSO | `CANCEL_FAILED` (3.7) mai raggiungibile | `ENTRY_CANCEL_FAILED` mai emesso | Emettere evento nel gateway/timeout worker |
| GAP-6 | NOTO | Enrichment BLOCK/REVIEW | Worker mancante | Task separato |

---

## Copertura corretta (invariata)

I seguenti percorsi sono stati verificati come **correttamente coperti**:

| Evento lifecycle | Notification type | Percorso |
|---|---|---|
| `SIGNAL_ACCEPTED` | `SIGNAL_ACCEPTED` | `project_clean_log_for_chain` + `_write_no_chain_signal_clean_log` |
| `SIGNAL_REJECTED` | `SIGNAL_REJECTED` | `_write_no_chain_signal_clean_log` |
| `REVIEW_REQUIRED` | `REVIEW_REQUIRED` | entrambi i percorsi sopra |
| `ENTRY_FILLED` | `ENTRY_OPENED` | `project_clean_log_for_chain` |
| `ENTRY_UPDATED` | `ENTRY_UPDATED` | `project_clean_log_for_chain` |
| `TP_FILLED` / `TP_FILLED_FINAL` | `TP_FILLED` / `TP_FILLED_FINAL` | promozione `is_final` in `project_clean_log_for_chain` |
| `SL_FILLED` | `SL_FILLED` | `project_clean_log_for_chain` |
| `CLOSE_FULL_FILLED` | `POSITION_CLOSED` / `BE_EXIT` | `project_clean_log_for_chain` (promozione `PROTECTED`) |
| `PENDING_ENTRY_CANCELLED` (normale) | `ENTRY_CANCELLED` | `project_clean_log_for_chain` |
| `TELEGRAM_UPDATE_ACCEPTED` | `UPDATE_DONE/PARTIAL/REJECTED` | `_write_update_clean_log` |
| multi-chain update | `MULTI_CHAIN_SUMMARY` | `_write_multi_chain_summary` |
| `RECONCILIATION_WARNING/FIXED` | idem | `project_clean_log_for_chain` |
| `REENTRY_ACCEPTED` | `REENTRY_ACCEPTED` | `project_clean_log_for_chain` |
| Policy TECH_LOG (gating, rate limit) | — | `notification_dispatcher._should_send_tech_log` |

---

## Proposta chiusura gap — priorità e ordine

### Fix immediati (1 sessione)

**F1 — GAP-1** (1 linea):
```python
# src/runtime_v2/lifecycle/workers.py:204
# Prima:
(chain_id, "TIMEOUT_REACHED", "timeout_worker", "WAITING_ENTRY", "EXPIRED", ...)
# Dopo:
(chain_id, "PENDING_TIMEOUT", "timeout_worker", "WAITING_ENTRY", "EXPIRED", ...)
```
Zero rischi, zero side effects. La specifica (§2a, §3.17) è già completa.

**F2 — GAP-3** (3 file, ~15 righe):

`outbox_writer.py` — aggiungere alla mappa:
```python
"CLOSE_PARTIAL_FILLED": "POSITION_CLOSED",
```

`outbox_writer.py — _build_payload()` — gestire il caso aggiuntivo:  
Il payload di `CLOSE_PARTIAL_FILLED` contiene `fill_price`, `filled_qty`, `closed_size`.  
Riutilizzare il builder `POSITION_CLOSED` aggiungendo `close_reason="PARTIAL_CLOSE"` come valore di fallback quando non presente nel payload evento.

`clean_log.py` — nessuna modifica: `_position_closed()` usa già `p.get('close_reason', 'MANUAL_CLOSE')`, che mostrerà `PARTIAL_CLOSE` correttamente.

**F3 — GAP-4** (aggiunta in `command_worker.py`, ~8 righe):

```python
# src/runtime_v2/execution_gateway/command_worker.py
# Dopo ogni self._gw.process(cmd, account_id=account_id)
# aggiungere proiezione clean log

from src.runtime_v2.control_plane.outbox_writer import project_clean_log_for_chain
import sqlite3

# Nel blocco try dopo gw.process():
try:
    _conn = sqlite3.connect(self._ops_db)
    try:
        with _conn:
            project_clean_log_for_chain(_conn, cmd.trade_chain_id)
    finally:
        _conn.close()
except Exception:
    logger.exception("clean_log projection failed after gateway for chain %s", cmd.trade_chain_id)
```

Alternativa più pulita: estrarre `_project_chain(chain_id)` come metodo privato in `ExecutionCommandWorker` e chiamarlo nei 3 punti di `run_once()`.

### Fix nella prossima sessione

**F4 — GAP-2**: aggiungere notifica TECH_LOG per `mark_review_required`.

Approccio raccomandato: in `GatewayCommandRepository.mark_review_required()` accettare un parametro opzionale `ops_db_path` e scrivere un `TECH_LOG` outbox entry con `level=WARNING` se `ops_db_path` è fornito. Il command_worker passa `self._ops_db`.

Il payload TECH_LOG segue il formato spec §4:
```
[WARNING] Gateway: command_blocked
────────────────
Comando bloccato in REVIEW_REQUIRED.

Context:
command_id: {id}
command_type: {type}
chain_id: {chain_id}
reason: {reason}

Action: intervento manuale richiesto
────────────────
Source: execution_gateway
```

### Task separato (fuori scope immediato)

**F5 — GAP-5**: emettere `ENTRY_CANCEL_FAILED` quando un `CANCEL_PENDING_ENTRY` esaurisce i tentativi.  
Richiede un meccanismo di tracking tentativi cancel nel gateway + scrittura lifecycle event.

**F6 — GAP-6**: worker enrichment blocks (già documentato in `Osservazioni.md`).
