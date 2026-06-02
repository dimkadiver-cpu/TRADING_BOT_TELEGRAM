# Notification Redesign — TP Immediati + UPDATE Arricchiti

**Data**: 2026-06-02  
**Stato**: Approvato

---

## Obiettivo

Eliminare i delay artificiali dalle notifiche di trading e arricchire i messaggi UPDATE con dati concreti (prezzi old/new, entry cancellate). Sostituire l'aggregazione batch con un modello immediato per chain + riassuntivo multi-chain con link.

---

## Contesto attuale

### Problemi

1. **TP partial fills** hanno un delay di 30s per permettere l'aggregazione in `TP_BATCH_FILLED`. Per un singolo TP questo è latenza pura.
2. **UPDATE notifications** hanno un delay di 20s e mostrano solo nomi di azione (`move_sl`, `cancel_pending`) senza valori concreti.
3. `BE_MOVE_REQUESTED` (move SL a BE da comando esplicito) usa un event type diverso da `TELEGRAM_UPDATE_ACCEPTED` e non appare affatto nell'UPDATE_DONE.
4. `MULTI_CHAIN_UPDATE` è generico, senza link ai messaggi individuali e senza distinzione di esito per chain.

### Pipeline attuale

```
ExchangeEvent → EventProcessor → LifecycleEvent → outbox_writer
  → ops_notification_outbox (con send_after delay)
  → AggregationWorker (ogni N secondi) → batch/merge rows
  → TelegramNotificationDispatcher → Telegram
```

---

## Design

### Approccio scelto: Entry-gate summary + dispatcher link enrichment

- Messaggi individuali scritti immediatamente (nessun delay).
- `MULTI_CHAIN_SUMMARY` scritto con `send_after=now+3s` da `entry_gate`.
- Il dispatcher arricchisce i link al momento dell'invio, leggendo `ops_clean_log_tracking`.
- `AggregationWorker` rimosso.

---

## Sezione 1 — TP Partial Fills: immediati, nessuna aggregazione

### Comportamento

Ogni `TP_FILLED` e `TP_FILLED_FINAL` viene inviato immediatamente come messaggio separato. Se TP1 e TP2 si triggano nello stesso frame WebSocket, arrivano come 2 messaggi in ~1s.

### Modifiche

**`control_plane/outbox_writer.py`**
- Rimuovere il caso `TP_FILLED`/`TP_FILLED_FINAL` da `_send_after_for()` → `send_after = _now()` per default.
- Rimuovere `TP_FILLED`/`TP_FILLED_FINAL` da `_agg_group()`.

**`control_plane/aggregation_worker.py`**
- Rimuovere `_aggregate_tp_batches()` e relativa chiamata in `run_once()`.

**`control_plane/clean_log.py`**
- Rimuovere `_tp_batch_filled()`.
- Rimuovere il caso `TP_BATCH_FILLED` in `format_clean_log()`.

### Comportamento prima/dopo

| Scenario | Prima | Dopo |
|---|---|---|
| 1 TP parziale | messaggio dopo 30s | messaggio immediato |
| TP1 + TP2 simultanei | 1 batch dopo 30s | 2 messaggi in ~1s |

---

## Sezione 2 — UPDATE Arricchiti: dati old/new per ogni azione

### Comportamento

Ogni messaggio UPDATE_DONE/PARTIAL/REJECTED mostra i valori concreti delle azioni applicate: prezzo SL vecchio e nuovo, entry cancellate con sequenza e prezzo, percentuale di chiusura.

### Modifiche payload TELEGRAM_UPDATE_ACCEPTED

**`lifecycle/entry_gate.py`** — ogni `_apply_*` arricchisce il payload:

| Metodo | Payload aggiunto |
|---|---|
| `_apply_move_to_be` | `old_sl_price`, `new_sl_price`, `is_breakeven: true` — **e cambia event_type da `BE_MOVE_REQUESTED` a `TELEGRAM_UPDATE_ACCEPTED`** |
| `_apply_cancel_pending` | `cancelled_entries: [{sequence, price, entry_type}]` (letti da `plan_state_json`) |
| `_apply_close_full` | nessun dato aggiuntivo |
| `_apply_close_partial` | `close_pct: float` (fraction × 100) |
| `_apply_modify_entries` | `changed_entries: [{sequence, old_price, new_price}]` |

### Modifiche a `_write_update_clean_log`

Costruisce la lista `changed` dagli eventi `TELEGRAM_UPDATE_ACCEPTED`:

```python
changed = []
for e in accepted:
    p = json.loads(e.payload_json or "{}")
    action = p.get("action")

    if p.get("is_breakeven"):
        changed.append({
            "field": "SL",
            "old": p.get("old_sl_price"),
            "new": p.get("new_sl_price"),
            "note": "BE",
        })
    elif action == "CANCEL_PENDING":
        for entry in p.get("cancelled_entries", []):
            changed.append({
                "field": f"Entry_{entry['sequence']}",
                "old": entry.get("price"),
                "new": "cancelled",
            })
    elif action == "CLOSE_PARTIAL":
        changed.append({
            "field": "Position",
            "old": "open",
            "new": f"closed {p.get('close_pct', '?')}%",
        })
    elif action == "MODIFY_ENTRIES":
        for ce in p.get("changed_entries", []):
            changed.append({
                "field": f"Entry_{ce['sequence']}",
                "old": ce.get("old_price"),
                "new": ce.get("new_price"),
            })
```

Il campo `changed` viene aggiunto al payload dell'UPDATE_DONE. Il formatter `_update_done` in `clean_log.py` lo usa già (`clean_log.py:289`) — nessuna modifica al formatter.

### Risultato visivo

```
✅ #42 — UPDATE DONE
─ ─ ─ ─ ─ ─ ─ ─
BTC/USDT — 📈 LONG
─ ─ ─ ─ ─ ─ ─ ─
Operation:
▪️ Move SL to BE
▪️ Cancel pending
Changed:
SL: 91,000 → 94,200 *
* Breakeven
Entry_2: 92,500 → cancelled
─ ─ ─ ─ ─ ─ ─ ─
Source: trader_update
```

### Delay

Rimuovere il caso `UPDATE_DONE`/`UPDATE_PARTIAL`/`UPDATE_REJECTED` da `_send_after_for()` in `outbox_writer.py` → invio immediato.

---

## Sezione 3 — MULTI_CHAIN_SUMMARY: riassuntivo con link

### Comportamento

Quando un messaggio del trader colpisce **2 o più chain**, dopo i messaggi individuali (immediati) arriva un riassuntivo con stato per chain e link diretto all'UPDATE_DONE di ciascuna.

L'emoji header è `✅` se tutte le chain sono DONE, `⚠️` se almeno una è PARTIAL o SKIPPED.

### Scrittura del summary (entry_gate)

**`lifecycle/entry_gate.py`** — in `_persist_update()`, dopo aver scritto tutti gli UPDATE per chain, se `len(chain_results) >= 2`:

```python
summary_payload = {
    "source_message_id": canonical_message_id,
    "operations": [op for op in collected_operations],  # deduplicated
    "chains": [
        {
            "chain_id": cr.trade_chain_id,
            "symbol": symbol,
            "side": side,
            "status": "DONE" | "PARTIAL" | "SKIPPED",
        }
        for cr in chain_results
    ],
}
write_clean_log_event(
    conn,
    notification_type="MULTI_CHAIN_SUMMARY",
    chain_id=None,
    payload=summary_payload,
    dedupe_key=f"clean:multi_summary:{canonical_message_id}",
)
```

**`control_plane/outbox_writer.py`** — aggiungere `MULTI_CHAIN_SUMMARY` in `_send_after_for()`:

```python
if notification_type == "MULTI_CHAIN_SUMMARY":
    return _iso_after(3)
```

Questo è il punto canonico dove si gestiscono i delay — nessuna modifica alla firma di `write_clean_log_event`.

### Arricchimento link nel dispatcher

**`control_plane/notification_dispatcher.py`** — in `drain_once()`, prima del render:

```python
if notification_type == "MULTI_CHAIN_SUMMARY":
    for chain in payload.get("chains", []):
        root_msg_id, tracking_chat_id = self._get_clean_log_root(chain["chain_id"])
        chain["link"] = self._build_signal_link(root_msg_id, tracking_chat_id)
```

Il link usa `clean_log_last_message_id` da `ops_clean_log_tracking` — punta all'UPDATE_DONE appena inviato, non al SIGNAL_ACCEPTED originale.

### Formatter `_multi_chain_summary` (nuovo in `clean_log.py`)

```python
def _multi_chain_summary(p: dict) -> str:
    chains = p.get("chains") or []
    statuses = {c.get("status") for c in chains}
    has_issues = bool(statuses & {"PARTIAL", "SKIPPED"})
    emoji = "⚠️" if has_issues else "✅"
    n = len(chains)
    lines = _header(emoji, None, f"UPDATE APPLICATO — {n} chain", None, None)
    operations = p.get("operations") or []
    if operations:
        lines.append("Operation:")
        for op in operations:
            lines.append(f"{_BULLET} {op}")
    lines.append(_SEP)
    for chain in chains:
        status = chain.get("status", "DONE")
        link = chain.get("link")
        side_e = _side_emoji(chain.get("side"))
        label = f"#{chain.get('chain_id')} {chain.get('symbol')} {side_e}  {status}"
        if link:
            label += f"  → {link}"
        lines.append(label)
    lines.append(_SEP)
    done = sum(1 for c in chains if c.get("status") == "DONE")
    partial = sum(1 for c in chains if c.get("status") == "PARTIAL")
    skipped = sum(1 for c in chains if c.get("status") == "SKIPPED")
    summary_parts = [f"Done: {done}"]
    if partial:
        summary_parts.append(f"Partial: {partial}")
    if skipped:
        summary_parts.append(f"Skipped: {skipped}")
    lines.append("   ".join(summary_parts))
    lines += _footer(p.get("source", "trader_update"))
    return _finalize(lines)
```

### Risultato visivo

```
✅ UPDATE APPLICATO — 3 chain
─ ─ ─ ─ ─ ─ ─ ─
Operation:
▪️ Move SL to BE
▪️ Cancel pending
─ ─ ─ ─ ─ ─ ─ ─
#42 BTC/USDT 📈  DONE    → t.me/c/xxx/101
#43 ETH/USDT 📈  PARTIAL → t.me/c/xxx/102
#44 SOL/USDT 📊  SKIPPED → t.me/c/xxx/103
─ ─ ─ ─ ─ ─ ─ ─
Done: 1   Partial: 1   Skipped: 1
Source: trader_update
```

---

## Sezione 4 — Rimozione AggregationWorker

### Modifiche

**`control_plane/aggregation_worker.py`** — file rimosso.

**`control_plane/bootstrap.py` / `startup.py`** — rimuovere avvio e import di `AggregationWorker`.

**`control_plane/aggregation_worker.py`** non ha dipendenti oltre bootstrap — rimozione sicura.

**`control_plane/outbox_writer.py`**
- Rimuovere `_agg_group()` e il parametro `aggregation_group` da `_record()` e `write_clean_log_event()`.
- Rimuovere `_aggregate_update_batches` reference da `_TERMINAL_NOTIFICATION_TYPES` se presente.
- Il tipo `MULTI_CHAIN_UPDATE` (vecchio) viene sostituito da `MULTI_CHAIN_SUMMARY`. Rimuovere dalla mappa `_CLEAN_LOG_EVENT_MAP` se presente.

---

## Riepilogo file coinvolti

| File | Modifica |
|---|---|
| `lifecycle/entry_gate.py` | Arricchisci payload `TELEGRAM_UPDATE_ACCEPTED`; `_apply_move_to_be` usa `TELEGRAM_UPDATE_ACCEPTED`; `_write_update_clean_log` popola `changed`; `_persist_update` scrive `MULTI_CHAIN_SUMMARY` se chain ≥ 2 |
| `control_plane/outbox_writer.py` | Rimuovi delay TP e UPDATE; rimuovi `_agg_group`; aggiungi `send_after=3s` per `MULTI_CHAIN_SUMMARY` |
| `control_plane/notification_dispatcher.py` | Arricchisci `MULTI_CHAIN_SUMMARY` con link da `ops_clean_log_tracking` |
| `control_plane/aggregation_worker.py` | **Rimosso** |
| `control_plane/clean_log.py` | Rimuovi `_tp_batch_filled`; aggiungi `_multi_chain_summary`; aggiorna `format_clean_log` |
| `control_plane/bootstrap.py` / `startup.py` | Rimuovi avvio `AggregationWorker` |

### File non modificati

- `lifecycle/event_processor.py`
- `lifecycle/workers.py`
- `lifecycle/be_move_resolver.py`
- Schema DB (`ops_notification_outbox`, `ops_clean_log_tracking`)

---

## Comportamento finale

| Evento | Prima | Dopo |
|---|---|---|
| TP parziale singolo | +30s delay | immediato |
| TP1 + TP2 simultanei | 1 batch dopo 30s | 2 messaggi in ~1s |
| UPDATE 1 chain | +20s delay, dati poveri | immediato, dati completi |
| UPDATE N chain | +20s delay, MULTI_CHAIN_UPDATE generico | immediato per chain + summary dopo 3s con link |
| BE move da comando esplicito | non visibile in UPDATE_DONE | visibile con SL old→new |
