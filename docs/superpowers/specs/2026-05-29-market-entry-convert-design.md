# Market Entry Convert — Design Spec
**Date:** 2026-05-29  
**Status:** Draft — pending user review

---

## Problema

Quando un UPDATE Telegram indica "entra a mercato ora" su un piano con 2+ leg LIMIT pendenti, il sistema deve:

1. Non richiedere riferimento esplicito alla leg da convertire
2. Comportarsi in modo config-driven per le leg successive

Bug attuale dimostrato dal caso CLO (chain=3): `UpdateChainResult` non porta `new_plan_state_json` → il piano in DB non viene mai aggiornato al momento dell'UPDATE → le leg rimanenti rimangono aperte su exchange senza controllo del sistema.

---

## Decisioni di design

### Trigger
Il parser emette `MODIFY_ENTRIES` con `kind="MARKET_NOW"` e `op.entries = []` (nessuna leg specifica). Nessuna modifica al contratto parser.

### Leg target
Sempre la prima leg PENDING (sequence minore). Non serve specificarla nel messaggio Telegram.

### Config
Campo `market_convert_mode` in `ManagementPlanConfig`. Default `"cancel_subsequent"`.

### Risk / Qty
Entrambi i modi riutilizzano il meccanismo `deferred_market` già esistente in `gateway.py:156`. Nessuna nuova logica di calcolo.

---

## Matematica del rischio

Con `deferred_market`, il rischio consumato da leg1 è sempre esattamente `risk_amount_leg1`, indipendentemente dal fill price:

```
qty_market = risk_amount / |mark_price - sl_price|
risk_realized = qty_market × |fill_price - sl_price| ≈ risk_amount  (esatto se fill ≈ mark)
```

**Keep mode**: le leg LIMIT rimanenti hanno già i loro `risk_amount` isolati. Non serve modificarne le qty su exchange.

**Cancel mode**: leg1 riceve `risk_amount = risk_remaining` (tutto il rischio residuo della posizione).

---

## Modifiche

### 1. `src/runtime_v2/signal_enrichment/models.py`

```python
class ManagementPlanConfig(BaseModel):
    # ... campi esistenti ...
    market_convert_mode: Literal["cancel_subsequent", "keep_subsequent"] = "cancel_subsequent"
```

### 2. `src/runtime_v2/lifecycle/models.py`

```python
@dataclass
class UpdateChainResult:
    trade_chain_id: int
    new_lifecycle_state: LifecycleState | None
    new_be_protection_status: BeProtectionStatus | None
    lifecycle_events: list[LifecycleEvent]
    execution_commands: list[ExecutionCommand]
    new_plan_state_json: str | None = None   # fix bug CLO
```

### 3. `src/runtime_v2/lifecycle/entry_gate.py` — routing

In `_apply_action_to_chain`:

```python
if action_type == "MODIFY_ENTRIES":
    op = action.modify_entries
    if op and op.kind == "MARKET_NOW" and not op.entries:
        return self._apply_market_entry_now(enriched, chain, action, active_commands)
    if op and op.kind in {"UPDATE_PRICE", "REPLACE_ENTRY", "MARKET_NOW"}:
        return self._apply_modify_entries(enriched, chain, action, active_commands)
    return self._review_chain(enriched, chain, "unsupported_modify_entries_kind")
```

`MARKET_NOW` con entries esplicite rimane nel path `_apply_modify_entries` per compatibilità.

### 4. `src/runtime_v2/lifecycle/entry_gate.py` — `_apply_market_entry_now`

```
INPUT:  chain, enriched, active_commands
OUTPUT: UpdateChainResult con execution_commands + new_plan_state_json

Algoritmo:
1. mp = ManagementPlanConfig dal chain
2. mode = mp.market_convert_mode
3. risk_snap = json.loads(chain.risk_snapshot_json)
4. sl_price = risk_snap["sl_price"] o chain.expected_stop_price
5. pending_legs = [leg for leg in plan["legs"] if leg["status"] == "PENDING"]
6. Se pending_legs vuoto → _review_chain("no_pending_legs_for_market_convert")
7. leg1 = min(pending_legs, key=sequence)
8. others = [l for l in pending_legs if l["sequence"] != leg1["sequence"]]

── CANCEL MODE ──────────────────────────────────────────────────────────
9a. risk_amount = chain.risk_remaining or risk_snap["risk_amount"]
9b. commands += CANCEL_PENDING_ENTRY(leg1.client_order_id)
9c. commands += PLACE_ENTRY[_WITH_ATTACHED_TPSL](MARKET, deferred_market, risk_amount)
9d. commands += CANCEL_PENDING_ENTRY(leg.client_order_id) for leg in others
9e. new_plan: leg1 → MARKET/PENDING/new_coid, others → CANCELLED

── KEEP MODE ────────────────────────────────────────────────────────────
9a. leg1_snap = risk_snap["legs"][leg1.sequence]
9b. risk_amount = leg1_snap["risk_amount"]
9c. commands += CANCEL_PENDING_ENTRY(leg1.client_order_id)
9d. commands += PLACE_ENTRY[_WITH_ATTACHED_TPSL](MARKET, deferred_market, risk_amount)
    [nessuna azione sulle altre leg]
9e. new_plan: leg1 → MARKET/PENDING/new_coid, others invariate

── COMUNE ───────────────────────────────────────────────────────────────
10. event = TELEGRAM_UPDATE_ACCEPTED(action="MARKET_ENTRY_NOW", mode=mode)
11. return UpdateChainResult(commands, [event], new_plan_state_json)
```

**Scelta PLACE_ENTRY vs PLACE_ENTRY_WITH_ATTACHED_TPSL**: se `leg1.sequence == 1` → `PLACE_ENTRY_WITH_ATTACHED_TPSL` (stessa regola dell'`EntryCommandFactory`). Altrimenti `PLACE_ENTRY`.

**`new_plan_state_json` — `client_order_id` di leg1**: aggiornato all'idempotency key del nuovo comando MARKET (`place_entry_attached:{cmid}:leg1` o `place_entry:{cmid}:leg1`), così il fill successivo può matchare via `command_payload.sequence`.

### 5. `src/runtime_v2/lifecycle/entry_gate.py` — `_persist_update`

```python
for cr in result.chain_results:
    fields = ["updated_at=?"]
    vals = [now]
    if cr.new_lifecycle_state:
        fields.append("lifecycle_state=?"); vals.append(cr.new_lifecycle_state)
    if cr.new_be_protection_status:
        fields.append("be_protection_status=?"); vals.append(cr.new_be_protection_status)
    if cr.new_plan_state_json is not None:
        fields.append("plan_state_json=?"); vals.append(cr.new_plan_state_json)
    if len(fields) > 1:
        vals.append(cr.trade_chain_id)
        conn.execute(f"UPDATE ops_trade_chains SET {', '.join(fields)} ...", vals)
```

---

## File NON modificati

| File | Motivo |
|---|---|
| `diff_engine.py` | Non coinvolto nel nuovo path |
| `gateway.py` | `deferred_market` già funziona |
| `event_processor.py` | Fill processing già corretto |
| Parser contract | Nessuna modifica necessaria |

---

## Edge cases

| Caso | Comportamento |
|---|---|
| 0 leg PENDING | `REVIEW_REQUIRED("no_pending_legs_for_market_convert")` |
| 1 sola leg PENDING | Cancel e keep producono lo stesso risultato |
| Leg1 già parzialmente fillata | Il cancel arriva dopo un fill parziale → `position_already_open=True` nel `PENDING_ENTRY_CANCELLED_CONFIRMED` → chain rimane OPEN, il MARKET entry è un averaging |
| `risk_remaining == 0` (mai aggiornato) | Fallback a `risk_snap["risk_amount"]` |

---

## Config per trader

```yaml
# config/enrichment/<trader_id>.yaml
management_plan:
  market_convert_mode: "cancel_subsequent"  # o "keep_subsequent"
```

Default globale: `"cancel_subsequent"`.

---

## Test da scrivere

1. **Cancel mode, 2 leg**: UPDATE market-now → 1 CANCEL leg1 + 1 PLACE_ENTRY MARKET (risk=total) + 1 CANCEL leg2. Piano: leg1 MARKET/PENDING, leg2 CANCELLED.
2. **Keep mode, 2 leg**: UPDATE market-now → 1 CANCEL leg1 + 1 PLACE_ENTRY MARKET (risk=leg1_budget). Piano: leg1 MARKET/PENDING, leg2 LIMIT/PENDING invariata.
3. **1 sola leg PENDING**: cancel e keep → stesso output.
4. **0 leg PENDING**: REVIEW_REQUIRED.
5. **`_persist_update` persiste `new_plan_state_json`**: verifica che dopo l'UPDATE il piano in DB sia aggiornato.
