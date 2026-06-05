# Audit Notifiche Telegram — Gap Analysis

Data: 2026-06-04 — aggiornato 2026-06-05 — aggiornato 2026-06-05 (sessione 2)  
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

### GAP-2 — ~~CRITICO~~ **CHIUSO**: Fallimenti esecuzione gateway mai notificati

**File**: `src/runtime_v2/execution_gateway/gateway.py` + `repositories.py`

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

**Fix applicato (2026-06-05)**: `GatewayCommandRepository.mark_review_required()` ora:
- usa `with conn:` per transazione atomica
- legge `trade_chain_id` e `command_type` dall'`ops_execution_commands` tramite `command_id`
- scrive TECH_LOG `GATEWAY_REVIEW_REQUIRED` livello WARNING nella stessa transazione

Notifica prodotta:
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

### GAP-4 — ~~MEDIO~~ **CHIUSO**: `cancel_chain_if_all_entries_failed` — fallimenti interni gateway mai notificati

**File**: `src/runtime_v2/execution_gateway/repositories.py`

**Chiarimento semantico emerso dall'analisi**: il GAP-4 non riguarda fallimenti sull'exchange (coperti dal percorso `PENDING_ENTRY_CANCELLED_CONFIRMED` → `event_processor` → `project_clean_log_for_chain` ✅), ma fallimenti **pre-exchange** interni al bot (adapter failure, exception dopo max retry). Notificare questi casi come `ENTRY_CANCELLED` CLEAN_LOG sarebbe semanticamente errato — sono errori operativi, non cancellazioni.

**Fix applicato (2026-06-05)**: `cancel_chain_if_all_entries_failed()` ora:
- espande la query su `ops_trade_chains` per leggere anche `symbol` e `side`
- scrive TECH_LOG `GATEWAY_ENTRY_ALL_FAILED` livello ERROR dentro il `with conn:` esistente, atomico con UPDATE chain + INSERT lifecycle event

Notifica prodotta:
```
[ERROR] Gateway: entry_all_failed
────────────────
Tutti i comandi PLACE_ENTRY falliti. Catena cancellata.

Context:
chain_id: {chain_id}
symbol: {symbol}
side: {side}
reason: {reason}

Action: intervento manuale richiesto
────────────────
Source: execution_gateway
```

---

### GAP-5 — ~~BASSO~~ **CHIUSO**: `ENTRY_CANCEL_FAILED` nella mappa è codice morto

**File**: `src/runtime_v2/control_plane/outbox_writer.py:21`

La mappa contiene `"ENTRY_CANCEL_FAILED": "CANCEL_FAILED"`.  
`"ENTRY_CANCEL_FAILED"` non veniva emesso da nessun file nel codebase.

Il formatter `_cancel_failed()` in `clean_log.py:463` e il payload builder `outbox_writer.py:499` erano irraggiungibili.

**Fix applicato (2026-06-05)**:
- `GatewayCommandRepository.write_cancel_entry_failed_lifecycle()` aggiunto in `repositories.py`: scrive lifecycle event `ENTRY_CANCEL_FAILED` con payload `{entry_ref, attempts, source}` in transazione atomica.
- `ExecutionGateway._handle_error()` in `gateway.py`: dopo `mark_failed()`, se `command_type == "CANCEL_PENDING_ENTRY"` chiama il nuovo metodo.

Notifica prodotta:
```
🚨 CANCEL FAILED
────────────────
Cancellation of <order_ref> failed after N attempts.
Requires manual review required to resolve the position.
────────────────
Source: execution_gateway
```

---

### GAP-6 — APERTO (cambio design, fuori scope audit)

`SignalEnrichmentProcessor` porta `enrichment_decision='BLOCK'` o `'REVIEW'` a `lifecycle_processed=True` senza passare dal lifecycle gate → nessuna notifica generata.

**Decisione (2026-06-05)**: lasciato aperto intenzionalmente. Non è un fix puntuale — richiede un cambio di design (worker separato o scrittura diretta in outbox dal processor senza una trade chain). Da affrontare come task autonomo in futuro.

---

### GAP-7 — MEDIO: Update fallito per `no_update_target` / `ambiguous_update_target` non genera notifica

**Scoperto (2026-06-05)**: investigazione su ops.sqlite3 — enrichment #9 e #10 (tg_msg 340, 341) non eseguiti.

**File**: `src/runtime_v2/lifecycle/entry_gate.py:1997-2009`

**Comportamento attuale**: quando il resolver degli update non trova nessuna chain target (`len(matched) == 0`) oppure trova un match ambiguo (`matched is None`), `_gate_update()` ritorna un `UpdateGateResult` con `review_events` popolato e `chain_results=[]`. Il chiamante persiste correttamente i `review_events` in `ops_lifecycle_events` con `trade_chain_id=NULL`, ma non scrive nulla in `ops_notification_outbox`.

```python
# entry_gate.py — blocco review_events (riga 1997)
for event in result.review_events:
    conn.execute("INSERT OR IGNORE INTO ops_lifecycle_events ...", (None, ...))
# ← nessuna chiamata outbox dopo questo blocco
```

**Perché `project_clean_log_for_chain` non intercetta il caso**: quella funzione interroga `ops_lifecycle_events WHERE trade_chain_id=?` — gli eventi con `trade_chain_id=NULL` non vengono mai letti.

**Causa root**: il path di notifica per gli update (`_write_update_clean_log`, `_write_multi_chain_summary`) opera su `chain_results` non su `review_events`. Non esiste un writer equivalente per i casi di fallimento targeting.

**Caso concreto osservato**: il trader ha risposto al messaggio 337 (ultimo duplicato rigettato con `duplicate_position`) invece che al messaggio 333 (il segnale che aveva creato la chain). La chain #2 (BTCUSDT OPEN) non ha `telegram_message_id=337` → `no_update_target`. L'update `CANCEL_PENDING + MOVE_STOP_TO_BE` è rimasto silenziosamente nel DB senza che l'utente fosse avvisato. Nota: un terzo tentativo precedente (enrichment #8, tg_msg 339) è stato bloccato già al layer di enrichment con `action_type_disabled:MOVE_STOP_TO_BE` — questo ricade nel GAP-6.

**Effetto operativo**: l'utente non riceve alcun feedback quando un update non viene applicato per mancanza di target. Non può distinguere tra "update applicato" e "update ignorato".

**Fix**: nel blocco `for event in result.review_events:` (entry_gate.py:1997), dopo l'INSERT in `ops_lifecycle_events`, aggiungere una scrittura in `ops_notification_outbox` di tipo `TECH_LOG` (livello WARNING) con payload minimo: `reason`, `source_link`, `enrichment_id`, `raw_message_id`. In alternativa, un `CLEAN_LOG` `UPDATE_REVIEW_REQUIRED` se si vuole che arrivi nel clean log utente.

```python
# Proposta
for event in result.review_events:
    conn.execute("INSERT OR IGNORE INTO ops_lifecycle_events ...", (None, ...))
    _write_update_review_notification(conn, enriched, event)  # ← da aggiungere
```

---

## Riepilogo

| # | Severità | Evento perso | Causa | Fix |
|---|---|---|---|---|
| GAP-1 | ~~CRITICO~~ **CHIUSO** | `PENDING_ENTRY_EXPIRED` (timeout) | Rename `TIMEOUT_REACHED` → `PENDING_TIMEOUT` in `workers.py:204` | ✅ |
| GAP-2 | ~~CRITICO~~ **CHIUSO** | Nessuna notifica per blocchi esecuzione | `mark_review_required` non scriveva outbox | TECH_LOG `GATEWAY_REVIEW_REQUIRED` in `repositories.py` | ✅ |
| GAP-3 | ~~MEDIO~~ **CHIUSO** | Chiusure parziali da bot | `PARTIAL_CLOSE_EXECUTED` — filtro `source=bot_command`, fill data con PnL | ✅ |
| GAP-4 | ~~MEDIO~~ **CHIUSO** | Fallimenti interni gateway mai notificati | `cancel_chain_if_all_entries_failed` non scriveva outbox | TECH_LOG `GATEWAY_ENTRY_ALL_FAILED` in `repositories.py` | ✅ |
| GAP-5 | ~~BASSO~~ **CHIUSO** | `CANCEL_FAILED` (3.7) mai raggiungibile | `ENTRY_CANCEL_FAILED` mai emesso | `write_cancel_entry_failed_lifecycle` in `repositories.py` + call in `gateway._handle_error` | ✅ |
| GAP-6 | APERTO | Enrichment BLOCK/REVIEW | Cambio design (worker separato) | Fuori scope — task autonomo |
| GAP-7 | APERTO | Update `no_update_target` / `ambiguous_update_target` silenzioso | `review_events` in `entry_gate.py` scritti con `trade_chain_id=NULL`, nessun path outbox | `_write_update_review_notification` in `entry_gate.py:1997` |

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

### Fix applicati (2026-06-05) ✅

- **GAP-1**: rinomina `TIMEOUT_REACHED` → `PENDING_TIMEOUT` in `workers.py` — chiuso in sessione precedente
- **GAP-2**: TECH_LOG `GATEWAY_REVIEW_REQUIRED` in `repositories.mark_review_required()` — chiuso in questa sessione
- **GAP-3**: mappatura `CLOSE_PARTIAL_FILLED` → `PARTIAL_CLOSE_EXECUTED` — chiuso in sessione precedente
- **GAP-4**: TECH_LOG `GATEWAY_ENTRY_ALL_FAILED` in `repositories.cancel_chain_if_all_entries_failed()` — chiuso in questa sessione

### Fix applicati (2026-06-05) — secondo round ✅

- **GAP-5**: `write_cancel_entry_failed_lifecycle()` in `repositories.py` + call in `gateway._handle_error()` — chiuso in questa sessione

### Task separato (fuori scope immediato)

**F6 — GAP-6**: enrichment BLOCK/REVIEW — cambio design, lasciato aperto intenzionalmente.

**F7 — GAP-7**: `_write_update_review_notification` in `entry_gate.py` — fix puntuale, basso rischio. Da pianificare come task autonomo.
