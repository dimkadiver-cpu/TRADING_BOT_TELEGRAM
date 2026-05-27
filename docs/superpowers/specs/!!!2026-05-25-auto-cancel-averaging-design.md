# Design: Auto-Cancel Averaging Pending + Race Condition Hardening

**Data:** 2026-05-25  
**Scope:** `src/runtime_v2/lifecycle/`  
**Stato:** Approvato — pronto per implementazione

---

## Contesto

Il sistema gestisce già il cancel manuale degli ordini pendenti (da update Telegram) e il cancel per timeout (`TimeoutWorker`). Tre campi di configurazione esistono nel modello (`cancel_averaging_pending_after`, `cancel_unfilled_pending_after`, `cancel_pending_by_engine`) ma non sono implementati nel lifecycle.

Questo design implementa:
1. **`cancel_averaging_pending_after`** — cancel automatico delle averaging leg dopo un TP configurato
2. **`cancel_pending_by_engine`** — gate globale on/off sul cancel automatico da engine
3. **BE deferred** — quando TP1 triggera sia cancel averaging che breakeven, il BE viene emesso solo dopo la conferma dei cancel (per evitare un prezzo BE calcolato su una posizione non ancora definitiva)
4. **Race condition hardening** — robustezza sulla state machine per eventi exchange fuori ordine

**Escluso dallo scope:** `cancel_unfilled_pending_after` — richiede un price-watcher non presente nell'architettura attuale. Documentato in `stato_runtime_v2.md` come bloccato da feature mancante.

---

## Semantica dei campi config

### `cancel_averaging_pending_after: "tp1" | "tp2" | null`

Quando il TP configurato si filla, cancella le leg con `sequence > 1` (averaging legs) che sono ancora in stato `PENDING/SENT/ACK`. Presuppone che leg 1 sia già fillata (chain in stato `OPEN` o `PARTIALLY_CLOSED`).

### `cancel_pending_by_engine: bool` (default: `true`)

Gate globale. Se `false`, nessun cancel automatico da engine parte — né `cancel_averaging_pending_after` né eventuali future automazioni. Il cancel manuale da Telegram resta sempre attivo indipendentemente da questo flag.

### `cancel_unfilled_pending_after` — ESCLUSO

Richiederebbe il controllo che il prezzo di mercato abbia raggiunto un livello TP mentre l'entry non era ancora fillata. Architettura price-watcher non presente. Lasciato nel modello come placeholder.

---

## Architettura

```
TP_FILLED
    │
    ▼
_process_tp_filled (event_processor.py)
    ├─ [cancel_pending_by_engine == false] → skip tutto il blocco auto-cancel
    ├─ [cancel_averaging_pending_after == "tpN"]
    │       ├─ Legge averaging legs pendenti da plan_state_json (sequence > 1, PENDING/SENT/ACK)
    │       ├─ Emette CANCEL_PENDING_ENTRY per ciascuna leg
    │       └─ Emette AUTO_CANCEL_AVERAGING_REQUESTED {tp_level, deferred_be: bool}
    └─ [be_trigger == "tpN" AND averaging legs pendenti > 0]
            ├─ NON emette MOVE_STOP_TO_BREAKEVEN ora
            └─ Scrive _be_deferred_by_auto_cancel in plan_state_json

    [be_trigger == "tpN" AND averaging legs pendenti == 0]
            └─ Emette MOVE_STOP_TO_BREAKEVEN immediatamente (nessuna leg da attendere)

PENDING_ENTRY_CANCELLED_CONFIRMED
    │
    ▼
_process_pending_entry_cancelled_confirmed (event_processor.py)
    ├─ Marca leg come CANCELLED in plan_state_json
    ├─ [_be_deferred_by_auto_cancel presente in plan_state_json]
    │       ├─ Conta averaging legs ancora PENDING/SENT/ACK nel plan_state_json
    │       ├─ Se > 0 rimaste → noop, aspetta prossima conferma
    │       └─ Se 0 rimaste → emette MOVE_STOP_TO_BREAKEVEN con entry_avg_price corrente
    │                         → rimuove _be_deferred_by_auto_cancel da plan_state_json
    └─ [Race guard prima di → CANCELLED]
            ├─ Se open_position_qty == 0:
            │       ├─ Conta PLACE_ENTRY* comandi in stato SENT/ACK
            │       ├─ Se > 0 → new_state = None (chain resta corrente)
            │       │           emette NOOP_CANCEL_CONFIRMED_POSITION_UNRESOLVED
            │       └─ Se 0  → new_state = "CANCELLED" (comportamento attuale)
            └─ Se open_position_qty > 0 → chain resta viva (comportamento attuale)

ENTRY_FILLED (averaging leg, race fill prima del cancel)
    │
    ▼
_process_entry_filled (event_processor.py) — estensione
    └─ [_be_deferred_by_auto_cancel presente in plan_state_json]
            ├─ Decrementa averaging_legs_pending nel flag
            └─ Se averaging_legs_pending == 0 → emette MOVE_STOP_TO_BREAKEVEN
                                                 → rimuove flag
```

---

## Tracking del flag deferred BE

### Storage: `plan_state_json`

Nessuna colonna aggiuntiva al DB. Il flag è scritto nel blob `plan_state_json` già gestito dal processor, con prefisso `_` per indicare stato interno transitorio.

```json
{
  "legs": [...],
  "_be_deferred_by_auto_cancel": {
    "tp_level": 1,
    "averaging_legs_pending": 2
  }
}
```

### Ciclo di vita del flag

| Evento | Azione sul flag |
|---|---|
| `TP_FILLED` → averaging legs trovate + be_trigger coincide con tp_level | Scritto con `tp_level` e count delle leg trovate |
| `TP_FILLED` → nessuna averaging leg pendente | Non scritto; BE emesso subito |
| `PENDING_ENTRY_CANCELLED_CONFIRMED` → altre leg ancora pendenti | Flag invariato |
| `PENDING_ENTRY_CANCELLED_CONFIRMED` → ultima leg confermata | Flag rimosso; BE emesso |
| `ENTRY_FILLED` su averaging leg (race fill prima del cancel) | `averaging_legs_pending` decrementato; se 0 → BE emesso, flag rimosso |
| `SL_FILLED` / chain terminale con flag in flight | Flag ignorato (chain è terminale); nessun BE emesso |

### Guard BE già emesso manualmente

Prima di emettere `MOVE_STOP_TO_BREAKEVEN` nel path deferred, verificare:
```
if chain.be_protection_status in ("PROTECTED", "BE_MOVE_PENDING"):
    skip — BE già gestito manualmente
```

---

## Nuovo lifecycle event type

### `AUTO_CANCEL_AVERAGING_REQUESTED`

```python
payload: {
    "tp_level": int,          # TP che ha triggerato il cancel
    "legs_cancelled": int,    # numero di CANCEL_PENDING_ENTRY emessi
    "deferred_be": bool,      # True se BE è stato differito
}
source_type: "engine"
```

Aggiunto all'enum dei tipi evento in `models.py`.

---

## Race guard in `_process_pending_entry_cancelled_confirmed`

**Problema:** `PENDING_ENTRY_CANCELLED_CONFIRMED` per leg 2 arriva prima di `ENTRY_FILLED` per leg 1. `open_position_qty == 0` → chain erroneamente va a `CANCELLED`.

**Fix:**
```python
if open_position_qty == 0:
    remaining_sent_ack = conn.execute(
        """
        SELECT COUNT(*) FROM ops_execution_commands
        WHERE trade_chain_id = ?
          AND command_type IN ('PLACE_ENTRY', 'PLACE_ENTRY_WITH_ATTACHED_TPSL')
          AND status IN ('SENT', 'ACK')
        """,
        (chain_id,)
    ).fetchone()[0]

    if remaining_sent_ack > 0:
        new_state = None  # non finalizzare
        events.append(LifecycleEvent(..., event_type="NOOP_CANCEL_CONFIRMED_POSITION_UNRESOLVED"))
    else:
        new_state = "CANCELLED"
```

La query usa la stessa connessione della transazione corrente — nessun lock aggiuntivo.

---

## Guard anti-rumore in `_expand_cancel_pending_commands`

`_load_pending_entry_client_order_ids` filtra già per `status IN ('PENDING','SENT','ACK')` — questo esclude già i comandi `DONE`. La funzione rimane invariata per il cancel manuale.

Per il cancel averaging, viene introdotta `_load_pending_averaging_entry_client_order_ids` — variante che legge `sequence` dalla `plan_state_json` per filtrare solo le leg con `sequence > 1`:

```python
def _load_pending_averaging_entry_client_order_ids(
    conn, trade_chain_id: int, plan_state_json: str
) -> list[str]:
    # Carica tutti i pending client_order_id
    # Incrocia con plan_state_json per filtrare sequence > 1
    ...
```

---

## File toccati

| File | Tipo modifica |
|---|---|
| `src/runtime_v2/lifecycle/event_processor.py` | Principale — nuova logica in `_process_tp_filled`, `_process_pending_entry_cancelled_confirmed`, `_process_entry_filled` |
| `src/runtime_v2/lifecycle/entry_gate.py` | Nuova funzione `_load_pending_averaging_entry_client_order_ids` |
| `src/runtime_v2/lifecycle/models.py` | Nuovo event type `AUTO_CANCEL_AVERAGING_REQUESTED`, `NOOP_CANCEL_CONFIRMED_POSITION_UNRESOLVED` |
| `docs/debugging/stato_runtime_v2.md` | Aggiorna tabella "Implementati" e sposta/documenta `cancel_unfilled_pending_after` come bloccato |

---

## Test da aggiungere

| Test | Scenario | Assert principale |
|---|---|---|
| `test_cancel_averaging_after_tp1_no_be_conflict` | TP1 + 2 averaging leg pendenti + be_trigger tp1 | BE emesso solo dopo 2° `PENDING_ENTRY_CANCELLED_CONFIRMED`, con avg_price finale |
| `test_cancel_averaging_no_pending_legs` | TP1 + 0 averaging leg pendenti | BE emesso immediatamente in `_process_tp_filled` |
| `test_cancel_averaging_race_fill_before_cancel` | TP1 → leg 2 `ENTRY_FILLED` arriva prima di leg 3 `CANCELLED_CONFIRMED` | `averaging_legs_pending` decrementato da ENTRY_FILLED; BE emesso dopo last confirmed |
| `test_race_cancel_confirmed_before_entry_filled` | Leg 2 confirmed prima di leg 1 filled | Chain NON va a CANCELLED; successivo ENTRY_FILLED apre la chain |
| `test_race_entry_filled_before_cancel_confirmed` | Leg 1 filled prima di leg 2 confirmed | Chain va a OPEN; confirmed → SYNC_PROTECTIVE_ORDERS, no CANCELLED |
| `test_cancel_pending_by_engine_false` | `cancel_pending_by_engine=False`, TP1 con cancel_averaging configurato | Nessun CANCEL_PENDING_ENTRY emesso; nessun AUTO_CANCEL_AVERAGING_REQUESTED |
| `test_be_deferred_cleared_on_sl_hit` | TP1 → deferred BE in flight → SL_FILLED | Chain CLOSED; nessun MOVE_STOP_TO_BREAKEVEN |
| `test_be_already_manual_before_confirmed` | Trader invia U_MOVE_STOP manualmente tra TP1 e cancel confirmed | Deferred BE skippato (`be_protection_status == PROTECTED`) |
| `test_expand_cancel_skips_done_commands` | Leg già fillata (DONE) tra le averaging | Nessun cancel emesso per quella leg |

---

## Casi limite documentati (comportamento atteso, no codice)

**`cancel_pending_by_engine=false` ha precedenza assoluta:** se `false`, nessun cancel automatico parte, indipendentemente da `cancel_averaging_pending_after`. Il cancel manuale Telegram resta sempre attivo.

**Multi-TP con trigger su livelli diversi:** `cancel_averaging_pending_after: "tp2"` e `be_trigger: "tp1"` sono indipendenti. TP1 → BE emesso subito (nessun cancel). TP2 → cancel averaging + deferred BE (se be_trigger: "tp2") o solo cancel (se be_trigger: "tp1" già emesso).

**Timeout con deferred BE in flight:** `TimeoutWorker` porta la chain a `EXPIRED`. Il flag `_be_deferred_by_auto_cancel` resta in `plan_state_json` ma la chain è terminale — nessun BE emesso. Il flag è garbage con la chain.

**`cancel_unfilled_pending_after` — bloccato:** richiede price-watcher non presente. Lasciato nel modello, documentato in `stato_runtime_v2.md` come non implementabile senza architettura di monitoraggio prezzi.
