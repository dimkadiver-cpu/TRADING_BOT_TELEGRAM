# Analisi: TWO_STEP (MARKET + LIMIT) con N > 1 TP

**Data:** 2026-05-24  
**Contesto:** Verifica comportamento lifecycle per entry a due leg con più di un take profit.

---

## Setup di riferimento

- Entry mode: TWO_STEP — leg1 MARKET (weight=0.5), leg2 LIMIT (weight=0.5)
- `total_qty = size_usdt / fallback_entry_price` (es. 0.1)
- `L1 = L2 = 0.05`
- 2 TP: TP1 intermedio, TP2 finale (50/50)
- Execution mode: `D_POSITION_TPSL` (o `UNIFIED_PLAN` — stesso path per i TP)

---

## Comandi creati dall'entry gate

| Comando | qty / tp_size | Status iniziale |
|---|---|---|
| `PLACE_ENTRY leg1` (MARKET) | 0.05 | PENDING |
| `PLACE_ENTRY leg2` (LIMIT) | 0.05 | PENDING |
| `SET_POSITION_TPSL_PARTIAL tp1` | 0.05 (total × 50%) | WAITING_POSITION |
| `SET_POSITION_TPSL_PARTIAL tp2` | 0.05 (total − allocato) | WAITING_POSITION |

`plan_state_json` generato da `ExecutionPlanBuilder`:
```json
{
  "rebuild_policy": "ON_EACH_ENTRY_FILL",
  "intermediate_tps": [TP1_price],
  "final_tp": TP2_price
}
```

> `rebuild_policy = "ON_EACH_ENTRY_FILL"` viene settato automaticamente ogni volta che `tp_count > 1`.  
> `intermediate_tps` contiene tutti i TP tranne l'ultimo. Il finale va in `final_tp`.

---

## Happy path: leg1 filla → leg2 filla → TP1 → TP2

### Step 1 — Leg1 MARKET filla (fill_qty=0.05)

- `new_filled = 0 + 0.05 = 0.05` (cumulativo)
- `is_first_fill = True` → WAITING_POSITION liberato → TP1_orig e TP2_orig → PENDING
- `PostFillProtectionRebuilder(new_filled=0.05)`:
  - genera **TP1_rebuild**: `tp_size = 0.05 × 50% = 0.025`, `supersedes_previous=True`

Execution worker (FIFO):
1. **TP1_orig** → SENT, `tp_size=0.05` *(oversized: posizione attuale 0.05 ma TP1 copre tutto)*
2. **TP2_orig** → SENT, `tp_size=0.05`
3. **TP1_rebuild** → pre-send: TP1_orig/TP2_orig già SENT, niente da supersedere in PENDING  
   → SENT, `tp_size=0.025` *(corretto per posizione 0.05)*  
   → post-send: **TP1_orig SUPERSEDED, TP2_orig SUPERSEDED** nel DB

**Exchange dopo step 1:**
- TP1 @ TP1_price, size=0.025 ✓
- TP2 @ TP2_price, size=0.05 *(attivo sull'exchange, SUPERSEDED solo nel DB)*

### Step 2 — Leg2 LIMIT filla (fill_qty=0.05)

- `new_filled = 0.05 + 0.05 = 0.10` (cumulativo)
- `is_first_fill = False` → nessun WAITING_POSITION da liberare
- `PostFillProtectionRebuilder(new_filled=0.10)`:
  - genera **TP1_rebuild_2**: `tp_size = 0.10 × 50% = 0.05`, `supersedes_previous=True`

Execution worker:
4. **TP1_rebuild_2** → SENT, `tp_size=0.05` *(corretto per posizione piena 0.10)*  
   → post-send: TP1_rebuild SUPERSEDED

**Exchange dopo step 2:**
- TP1 @ TP1_price, size=0.05 ✓
- TP2 @ TP2_price, size=0.05 ✓
- Posizione = 0.10

### Step 3 — TP1 filla

- Chiude 0.05 → posizione = 0.05
- `SYNC_PROTECTIVE_ORDERS` emesso → aggiusta SL qty a 0.05

### Step 4 — TP2 filla

- Chiude 0.05 → posizione = 0 → **CLOSED** ✓

---

## Edge case: Leg2 filla DOPO che TP1 ha già scattato

```
Leg1 filla (0.05)
  → TP1_rebuild inviato: tp_size=0.025
  
TP1 scatta (prima di leg2)
  → chiude 0.025 → posizione=0.025
  → SYNC_PROTECTIVE_ORDERS → SL aggiustato
  
Leg2 LIMIT filla (0.05)
  → new_filled = 0.05 + 0.05 = 0.10
  → PostFillProtectionRebuilder → TP1_rebuild_2: tp_size=0.05
  → TP1 ri-settato sull'exchange con tp_size=0.05
  
Posizione dopo leg2 fill = 0.025 + 0.05 = 0.075
TP1 scatta di nuovo → chiude 0.05 → posizione=0.025
TP2 scatta → chiude max(0.025 disponibili) → CLOSED ✓
```

**Comportamento:** corretto nel contesto averaging. Leg2 ha aumentato la posizione,
quindi TP1 viene ri-applicato per coprire il 50% della posizione aggiornata.
TP1 può scattare due volte ma è atteso: la seconda volta copre l'averaging leg.

---

## Proprietà chiave del design

| Proprietà | Dettaglio |
|---|---|
| **Rebuilder usa qty cumulativa** | `new_filled = chain.filled_entry_qty + fill_qty` — non solo il fill incrementale |
| **Solo intermediate_tps vengono ricostruiti** | Il final TP (TP2_orig) non viene mai toccato dal rebuilder |
| **TP2_orig rimane attivo sull'exchange** | Anche se SUPERSEDED nel DB, l'ordine exchange è vivo |
| **supersedes_previous=True** | Pre-send: cancella PENDING stale. Post-send: marca SUPERSEDED i SENT/DONE |

---

## Problemi aperti / rischi

### ⚠️ Race window TP1_orig → TP1_rebuild

**Quando:** Tra il momento in cui TP1_orig è SENT (tp_size=total=0.05, oversized)
e il momento in cui TP1_rebuild sovrascrive TP1 sull'exchange (tp_size=0.025).

**Rischio:** Se TP1 scatta in questa finestra, chiude il 100% della posizione
(0.05 su 0.05) invece del 50%.

**Mitigazione attuale:** Con il loop event-driven la finestra è < 200ms.
In pratica quasi impossibile, ma non zero.

**Fix possibile:** Inviare TP1_orig e TP2_orig solo DOPO che il rebuild ha
già superseduto i comandi pre-generati — ma richiederebbe un cambio architetturale
nel flusso gate → WAITING_POSITION → event.

### ⚠️ TP2 SUPERSEDED in DB ma attivo sull'exchange

**Situazione:** TP2_orig viene marcato SUPERSEDED dal post-send di TP1_rebuild,
ma non viene mai ri-generato (il rebuilder gestisce solo `intermediate_tps`).

**Impatto:** Il WS fill watcher (`watchMyTrades`) rileva comunque il fill di TP2
perché opera sull'exchange event stream, indipendente dallo stato dei comandi in DB.

**Da verificare:** `ExchangeEventSyncWorker.run_tp_reconciliation()` — controlla
i fill per comandi SUPERSEDED o solo SENT/ACK/DONE? Se ignora SUPERSEDED,
TP2 non verrebbe rilevato via REST sync (solo via WS).

### ⚠️ Coverage test mancante

Non esiste un test integration/acceptance che copra:
- TWO_STEP + 2 TP, happy path (entrambe le leg fillano prima dei TP)
- TWO_STEP + 2 TP, edge case (leg2 filla dopo TP1)

Segnalato anche nel spec `lifecycle-verification-gaps-tp-sync-design.md`.

---

## File rilevanti

| File | Ruolo |
|---|---|
| `src/runtime_v2/lifecycle/entry_gate.py` | `_build_d_commands()` — crea i comandi iniziali |
| `src/runtime_v2/lifecycle/execution_plan.py` | `ExecutionPlanBuilder.build()` — setta `rebuild_policy` |
| `src/runtime_v2/lifecycle/event_processor.py` | `_process_entry_filled()` — invoca il rebuilder |
| `src/runtime_v2/lifecycle/post_fill_rebuilder.py` | `PostFillProtectionRebuilder` — genera TP corretti |
| `src/runtime_v2/execution_gateway/gateway.py` | `supersede_tp_partial_commands()` — gestisce supersede |
