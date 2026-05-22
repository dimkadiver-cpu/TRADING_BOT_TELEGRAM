# Design: Execution Strategy — Casi 1_1, 2, 2_2

**Data:** 2026-05-22  
**Riferimento:** `docs/debugging/Market/execution_strategy_design.md`

---

## Obiettivo

Implementare i tre casi di esecuzione ancora mancanti nella matrice di routing:

| Caso | entry_count | tp_count | execution_mode | Stato |
|------|-------------|----------|----------------|-------|
| Caso_1 | 1 | 1 | `C_SIMPLE_ATTACHED` | ✅ già implementato |
| Caso_1_1 | 1 | >1 | `C_MULTI_TP` | ❌ da implementare |
| Caso_2 | >1 | 1 | `D_MULTI_ENTRY_1TP` | ❌ da implementare |
| Caso_2_2 | >1 | >1 | `D_MULTI_ENTRY_MULTI_TP` | ❌ da implementare |

Il principio guida è: **il SL deve essere attached all'ordine entry al momento del placement**. Mai creato solo dopo il fill.

---

## Architettura: Approccio C — 4 execution_mode con helper condivisi

### Routing matrix in `entry_gate._build_entry_commands`

```python
# Condizioni (sl_price presente in tutti i casi non-legacy)
entry_count == 1 AND tp_count == 1  → C_SIMPLE_ATTACHED   (esistente)
entry_count == 1 AND tp_count > 1   → C_MULTI_TP          (nuovo)
entry_count > 1  AND tp_count == 1  → D_MULTI_ENTRY_1TP   (nuovo)
entry_count > 1  AND tp_count > 1   → D_MULTI_ENTRY_MULTI_TP (nuovo)
sl assente o legacy routing         → D_POSITION_TPSL     (fallback, backward compat)
```

`chain.execution_mode` viene impostato al momento della creazione e persiste nel DB. Il `event_processor` lo legge per decidere la logica post-fill.

---

## Sezione 1: entry_gate.py

### 1.1 Routing — `_build_entry_commands`

Il blocco decisionale corrente:
```python
use_c = (simple_attached_enabled and entry_count == 1 and tp_count == 1 and sl_price)
if use_c: return _build_c_commands(...)
return _build_d_commands(...)
```

Diventa:
```python
if not simple_attached_enabled or sl_price is None:
    return _build_d_commands(...)   # legacy fallback

if entry_count == 1 and tp_count == 1:
    return _build_c_commands(...)
if entry_count == 1 and tp_count > 1:
    return _build_c_multi_tp_commands(...)
if entry_count > 1 and tp_count == 1:
    return _build_d_multi_entry_1tp_commands(...)
return _build_d_multi_entry_multi_tp_commands(...)
```

Lo stesso schema condizionale determina `chain_execution_mode` nel blocco che costruisce la `TradeChain`.

### 1.2 Helper condiviso `_build_per_leg_attached_entry`

Produce il payload `PLACE_ENTRY_WITH_ATTACHED_TPSL` per una singola leg con SL attached.
Parametri: `leg`, `sl_price`, `leg_qty` (o `deferred_market` params), `tp_price=None`, `tp_qty=None`, `leverage`, `hedge_mode`, `position_idx`, `eid`, `execution_strategy_label`.

- Se `tp_price` è `None`: emette solo SL nell'`attached_tpsl` (mode `"FULL"` con solo `stop_loss`)
- Se `tp_price` è fornito: emette SL + TP nell'`attached_tpsl` (mode `"FULL"` con `stop_loss` + `take_profit` + `tp_qty`)
- Supporta `deferred_market`: se il leg_snap ha `qty_mode == "deferred_market"`, emette il payload con `qty_mode`, `risk_amount`, `sl_price` invece di `qty`

### 1.3 `_build_c_multi_tp_commands` (Caso_1_1)

Per 1 entry + N TP:
1. Usa `_build_per_leg_attached_entry` per la singola entry con SL + ultimo TP attached
   - `tp_price` = prezzo dell'ultimo TP
   - `tp_qty` = `leg_qty × close_pct_last / 100`
2. Per i TP intermedi (sequenze 1..N-1): emette `SET_POSITION_TPSL_PARTIAL` con `status="WAITING_POSITION"` e `preserve_sl=True`
   - `tp_qty` = `leg_qty × close_pct_i / 100`
   - L'ultimo TP intermedio prende il residuo per evitare drift
3. Supporta `deferred_market`: in tal caso `leg_qty` è sconosciuta al gate; i TP intermedi includono `qty_mode="deferred_market"` + `risk_amount` + `close_pct`. Il gateway, al momento del rilascio dei comandi WAITING_POSITION, usa `filled_entry_qty` reale della chain (non mark_price live) per calcolare `tp_qty = filled_entry_qty × close_pct / 100`

### 1.4 `_build_d_multi_entry_1tp_commands` (Caso_2)

Per N entry LIMIT + 1 TP:
1. Per ogni leg: usa `_build_per_leg_attached_entry` con SL + TP attached
   - `tp_price` = prezzo del singolo TP
   - `tp_qty` = qty della leg (full, perché 1 TP chiude il residuo)
2. Nessun `SET_POSITION_TPSL_FULL` WAITING_POSITION — la protezione è già exchange-native per ogni leg

### 1.5 `_build_d_multi_entry_multi_tp_commands` (Caso_2_2)

Per N entry + N TP:
1. Per ogni leg: usa `_build_per_leg_attached_entry` con solo SL attached (`tp_price=None`)
2. Nessun TP pre-emesso come WAITING_POSITION
3. Aggiunge al `risk_snapshot` della chain il campo `tp_rebuild`:
   ```json
   {
     "tp_rebuild": {
       "levels": [
         {"sequence": 1, "price": 0.52, "close_pct": 50.0},
         {"sequence": 2, "price": 0.55, "close_pct": 30.0},
         {"sequence": 3, "price": 0.60, "close_pct": 20.0}
       ]
     }
   }
   ```
   Questo dato è necessario a `event_processor` per ricalcolare le qty post-fill.

---

## Sezione 2: event_processor.py — logica post-fill

`_process_entry_filled` aggiunge dispatch su `chain.execution_mode`. Nessun nuovo parametro — la logica di supersessione avviene nel gateway, non qui.

### C_SIMPLE_ATTACHED
Nessuna azione post-fill aggiuntiva (invariato).

### C_MULTI_TP
Nessuna azione post-fill aggiuntiva. I `SET_POSITION_TPSL_PARTIAL` intermedi sono già stati creati come `WAITING_POSITION` da `entry_gate`. Il meccanismo `release_waiting_position=True` li rilascia automaticamente al primo fill (già funzionante nel worker).

### D_MULTI_ENTRY_1TP
Nessuna azione post-fill aggiuntiva. Ogni leg ha già SL + TP attached order-level.

### D_MULTI_ENTRY_MULTI_TP
Logica incrementale dopo ogni fill:

1. Legge `risk_snapshot_json["tp_rebuild"]["levels"]`
2. Calcola `new_filled` (già disponibile nel risultato corrente)
3. Costruisce nuovi `SET_POSITION_TPSL_PARTIAL` con `tp_qty = new_filled × close_pct / 100`; l'ultimo livello prende il residuo
4. Ogni nuovo comando ha nel payload `supersedes_previous=True`
5. Restituisce i nuovi comandi in `execution_commands`

### Sostituzione implicita (flag `supersedes_previous`)

Quando il gateway processa un `SET_POSITION_TPSL_PARTIAL` con `supersedes_previous=True`:
1. Cerca nel DB i `SET_POSITION_TPSL_PARTIAL` precedenti per lo stesso `trade_chain_id`
2. Se PENDING: li marca SUPERSEDED nel DB (non verranno inviati a exchange)
3. Se SENT/ACK: la nuova chiamata `Set Trading Stop` a Bybit sovrascrive automaticamente i valori precedenti (comportamento nativo Bybit — `Set Trading Stop` è un'operazione di sostituzione)
4. Marca i vecchi record come SUPERSEDED nel DB dopo l'invio

---

## Sezione 3: order_builder.py — `preserve_sl` flag

Per `C_MULTI_TP`, i TP intermedi vengono inviati via `SET_POSITION_TPSL_PARTIAL`. Il builder corrente include `stopLoss`/`slSize` nel payload Bybit, che rischierebbe di sovrascrivere lo SL già attached order-level.

Fix: se il payload del comando contiene `preserve_sl=True`, il builder **omette** `stopLoss` e `slSize` dalla chiamata API `trading_stop`. Solo i campi TP vengono inviati.

---

## Sezione 4: file coinvolti

| File | Modifica |
|------|----------|
| `lifecycle/entry_gate.py` | Nuova routing matrix; `_build_c_multi_tp_commands`; `_build_d_multi_entry_1tp_commands`; `_build_d_multi_entry_multi_tp_commands`; helper `_build_per_leg_attached_entry` |
| `lifecycle/event_processor.py` | `_process_entry_filled`: aggiunge `active_commands` param + dispatch su `execution_mode` per `D_MULTI_ENTRY_MULTI_TP` |
| `execution_gateway/adapters/ccxt_bybit/order_builder.py` | Rispetta `preserve_sl=True` in `SET_POSITION_TPSL_PARTIAL` |
| `execution_gateway/gateway.py` | Gestione `supersedes_previous=True` in `SET_POSITION_TPSL_PARTIAL` |

---

## Sezione 5: test

| File test | Cosa copre |
|-----------|------------|
| `tests/runtime_v2/lifecycle/test_entry_gate_cd.py` | Routing matrix (4 casi); builder output per ogni caso; deferred_market nei nuovi path |
| `tests/runtime_v2/lifecycle/test_event_processor.py` | `D_MULTI_ENTRY_MULTI_TP` post-fill: primo fill emette TP commands; secondo fill emette TP con `supersedes_previous=True` |
| `tests/runtime_v2/execution_gateway/test_bybit_order_builder.py` | `preserve_sl=True` omette stopLoss dal payload Bybit |
| `tests/runtime_v2/execution_gateway/test_gateway.py` | `supersedes_previous=True`: vecchi comandi marcati SUPERSEDED prima dell'invio |

---

## Fuori scope

- Update `LIMIT -> MARKET` (menzionato nel doc come requisito futuro — non implementato in questa fase)
- Watchdog di protezione (hardening operativo — fase separata)
- Reconciliation conservativa (fase separata)
