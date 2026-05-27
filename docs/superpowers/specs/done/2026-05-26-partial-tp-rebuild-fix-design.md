# Design: Partial TP Rebuild Fix — `REBUILD_PARTIAL_TPS`

**Data:** 2026-05-26  
**Stato:** Approvato — pronto per pianificazione  
**Scope:** `runtime_v2/lifecycle` + `runtime_v2/execution_gateway`

---

## Problema

Tre bug concatenati nel path post-fill dei TP parziali.

### Bug 1 — `supersede_tp_partial_commands` uccide sequence sbagliate

`PostFillProtectionRebuilder` emette N comandi `SET_POSITION_TPSL_PARTIAL` (uno per livello TP intermedio) dopo ogni fill. Il gateway, processando il primo, chiama `supersede_tp_partial_commands` che supersede **tutti** i `SET_POSITION_TPSL_PARTIAL` PENDING della chain, indipendentemente dal `tp_sequence`. Il secondo livello viene ucciso prima di essere inviato.

### Bug 2 — Bybit `trading_stop` Partial aggiunge invece di sostituire

L'API Bybit V5 `v5/position/trading-stop` con `tpslMode=Partial` **aggiunge** un nuovo ordine TP condizionale alla posizione invece di sostituire quello esistente. Ogni fill che genera un rebuild crea un ulteriore ordine TP allo stesso prezzo ma con qty diversa. Con 2 leg fillate, sull'exchange si trovano 2 ordini TP1 a prezzi uguali e qty diverse.

### Bug 3 — Idempotency collision sui fill

La chiave idempotenza dei fill è `TP_FILLED:{chain_id}:level:{tp_level}`. Quando i 2 ordini TP1 duplicati scattano in sequenza, il primo fill viene registrato e il secondo viene silenziosamente ignorato (`INSERT OR IGNORE`). La `open_position_qty` del sistema risulta sbagliata.

---

## Approccio scelto: Cancel-all + Replace atomico

Collassare "cancella tutti i partial TP esistenti + rimetti tutti i livelli" in **un singolo comando atomico** `REBUILD_PARTIAL_TPS`. Elimina la dipendenza di ordinamento tra comandi separati e risolve il problema alla radice.

---

## Design

### Nuovo command type: `REBUILD_PARTIAL_TPS`

Sostituisce i multipli `SET_POSITION_TPSL_PARTIAL` nel path post-fill.

**Payload:**
```json
{
  "symbol": "REQUSDT",
  "side": "SHORT",
  "hedge_mode": true,
  "position_idx": 2,
  "preserve_sl": true,
  "preserve_full_tp": true,
  "tps": [
    {
      "sequence": 1,
      "price": 0.090,
      "qty": 3333,
      "order_type": "Limit",
      "limit_price": 0.090,
      "trigger_by": "MarkPrice"
    },
    {
      "sequence": 2,
      "price": 0.085,
      "qty": 3333,
      "order_type": "Limit",
      "limit_price": 0.085,
      "trigger_by": "MarkPrice"
    }
  ]
}
```

- `tps` ordinato per `sequence` crescente
- `preserve_sl: true` — il cancel non tocca lo SL della posizione
- `preserve_full_tp: true` — il cancel non tocca il TP finale position-level (Full mode)
- Se `tps` è lista vuota, `PostFillProtectionRebuilder` non emette il comando

**Idempotency key:**
```
rebuild_partial_tps:{chain_id}:{exchange_event_id}
```

**Ciclo di vita:** `PENDING → SENT → DONE | FAILED`  
Fire-and-forget, stesso pattern degli altri comandi exchange.

---

### Distinzione TP parziali vs TP finale (Full)

Su Bybit V5 esistono due tipi di TP:

| | TP finale (Full) | TP parziali (Partial) |
|---|---|---|
| Origine | `PLACE_ENTRY_WITH_ATTACHED_TPSL` → parametro `takeProfit` sull'ordine entry | `trading_stop` con `tpslMode=Partial, tpSize=X` |
| Dove vive | Campo `takeProfit` della posizione (position-level) | Ordini condizionali separati con qty specifica |
| Cancelabile via `cancel_order` | No | Sì |

Il cancel step del `REBUILD_PARTIAL_TPS` cancella **solo** gli ordini condizionali reduce-only con qty < full_position_qty. Il TP finale position-level non viene mai toccato.

---

### Modifiche per componente

#### `post_fill_rebuilder.py`

**Prima:**
```python
for i, tp_price in enumerate(intermediate_tps):
    close_pct = 100.0 / n_total_tps
    tp_qty = round(filled_entry_qty * close_pct / 100.0, 8)
    commands.append(ExecutionCommand(
        command_type="SET_POSITION_TPSL_PARTIAL",
        payload_json=json.dumps({..., "tp_sequence": i+1, "supersedes_previous": True}),
        idempotency_key=f"tp_partial_fill:{chain_id}:{event_id}:tp{i+1}",
    ))
```

**Dopo:**
```python
close_pct = 100.0 / n_total_tps
tps = [
    {
        "sequence":    i + 1,
        "price":       tp_price,
        "qty":         round(filled_entry_qty * close_pct / 100.0, 8),
        "order_type":  "Limit",
        "limit_price": tp_price,
        "trigger_by":  "MarkPrice",
    }
    for i, tp_price in enumerate(intermediate_tps)
]
commands.append(ExecutionCommand(
    command_type="REBUILD_PARTIAL_TPS",
    payload_json=json.dumps({
        "symbol":           chain.symbol,
        "side":             chain.side,
        "hedge_mode":       hedge_mode,
        "position_idx":     position_idx,
        "preserve_sl":      True,
        "preserve_full_tp": True,
        "tps":              tps,
    }),
    idempotency_key=f"rebuild_partial_tps:{chain_id}:{event_id}",
))
```

#### `gateway.py`

Rimuovere tutta la logica `supersedes_previous` legata a `SET_POSITION_TPSL_PARTIAL`:
```python
# RIMUOVERE
supersedes_previous = (
    cmd.command_type == "SET_POSITION_TPSL_PARTIAL"
    and payload.get("supersedes_previous")
    ...
)
```

Aggiungere handler per `REBUILD_PARTIAL_TPS`:
```python
if cmd.command_type == "REBUILD_PARTIAL_TPS":
    # Pre-send: supersede REBUILD PENDING della stessa chain
    self._repo.supersede_rebuild_commands(
        cmd.trade_chain_id,
        exclude_command_id=cmd.command_id,
        statuses=("PENDING",),
    )
    # Post-success: storicizza REBUILD precedenti già inviati
    self._repo.supersede_rebuild_commands(
        cmd.trade_chain_id,
        exclude_command_id=cmd.command_id,
        statuses=("SENT", "ACK", "DONE"),
    )
```

#### `repositories.py`

Nuovo metodo (sostituisce `supersede_tp_partial_commands` per il nuovo path):
```python
def supersede_rebuild_commands(
    self,
    trade_chain_id: int,
    exclude_command_id: int,
    *,
    statuses: tuple[str, ...],
) -> None:
    placeholders = ",".join("?" for _ in statuses)
    conn.execute(
        "UPDATE ops_execution_commands SET status='SUPERSEDED', updated_at=? "
        "WHERE trade_chain_id=? AND command_type='REBUILD_PARTIAL_TPS' "
        f"AND status IN ({placeholders}) AND command_id != ?",
        (now, trade_chain_id, *statuses, exclude_command_id),
    )
```

`supersede_tp_partial_commands` rimane per backward compat con `SET_POSITION_TPSL_PARTIAL` (altri usi non post-fill).

#### `order_builder.py`

Nuovo metodo `_rebuild_partial_tps`:
```python
def _rebuild_partial_tps(self, payload: dict) -> BybitOrderParams:
    return BybitOrderParams(
        action="rebuild_partial_tps",
        symbol=payload["symbol"],
        position_side=payload["side"],
        extra_params={
            "position_idx":     int(payload.get("position_idx", 0)),
            "preserve_sl":      bool(payload.get("preserve_sl", True)),
            "preserve_full_tp": bool(payload.get("preserve_full_tp", True)),
            "tps":              payload["tps"],
        },
    )
```

#### `adapter.py` — `_handle_rebuild_partial_tps`

```python
def _handle_rebuild_partial_tps(self, symbol, position_side, extra) -> AdapterResult:
    position_idx  = extra["position_idx"]
    tps           = extra["tps"]
    close_side    = "Buy" if position_side == "SHORT" else "Sell"

    # 1. Quota posizione aperta (per distinguere partial da full TP)
    positions = self._exchange.fetch_positions([symbol])
    pos = next(
        (p for p in positions
         if p["side"].upper() == position_side.upper()
         and int(p["info"].get("positionIdx", 0)) == position_idx),
        None,
    )
    full_qty = float(pos["contracts"]) if pos else 0.0

    # 2. Trova ordini TP parziali (reduce-only condizionali, qty < full)
    open_orders = self._exchange.fetch_open_orders(symbol)
    partial_tp_orders = [
        o for o in open_orders
        if o.get("reduceOnly")
        and o.get("stopPrice")
        and o["side"].capitalize() == close_side
        and abs(float(o.get("amount", 0)) - full_qty) > 0.01
    ]

    # 3. Cancella ciascuno (errori non bloccanti — ordine potrebbe essere già scattato)
    for o in partial_tp_orders:
        try:
            self._exchange.cancel_order(o["id"], symbol)
        except Exception as exc:
            logger.warning("cancel partial TP order %s failed: %s", o["id"], exc)

    # 4. Place ogni livello TP in sequenza
    for tp in sorted(tps, key=lambda t: t["sequence"]):
        extra_params = {
            "positionIdx": position_idx,
            "tpslMode":    "Partial",
            "takeProfit":  str(float(tp["price"])),
            "tpSize":      str(float(tp["qty"])),
            "tpOrderType": tp.get("order_type", "Limit"),
            "tpTriggerBy": tp.get("trigger_by", "MarkPrice"),
        }
        if tp.get("order_type") == "Limit" and tp.get("limit_price"):
            extra_params["tpLimitPrice"] = str(float(tp["limit_price"]))

        resp = self._exchange.private_post_v5_position_trading_stop({
            "category": "linear",
            "symbol":   self._normalize_bybit_symbol(symbol),
            **extra_params,
        })
        ret_code, ret_msg = self._parse_trading_stop_retcode(resp)
        if ret_code != 0:
            logger.warning("trading_stop tp%s retCode=%s msg=%s",
                           tp["sequence"], ret_code, ret_msg)
            return AdapterResult(
                success=False,
                error=f"tp{tp['sequence']}: retCode={ret_code}: {ret_msg}",
            )

    return AdapterResult(success=True)
```

**Gestione errori e retry:**

| Scenario | Comportamento |
|---|---|
| Cancel fallisce (ordine già scattato) | log warning + continua — non blocca il place |
| `trading_stop` fallisce su un livello | `AdapterResult(success=False)` → gateway fa retry dell'intero REBUILD |
| Retry dopo cancel parziale | cancel step è idempotente — trova e cancella quello che c'è |
| Posizione non trovata (full_qty=0) | procede con `full_qty=0` → nessun ordine escluso dal cancel |

#### `event_sync.py` — `_get_tp_reconciliation_entries`

Aggiornata per includere `REBUILD_PARTIAL_TPS` con backward compat per `SET_POSITION_TPSL_PARTIAL`:

```python
def _get_tp_reconciliation_entries(self) -> list[dict]:
    conn = sqlite3.connect(self._ops_db)
    try:
        rows = conn.execute(
            "SELECT c.command_id, c.trade_chain_id, c.command_type, c.payload_json, "
            "t.symbol, t.side "
            "FROM ops_execution_commands c "
            "JOIN ops_trade_chains t ON c.trade_chain_id = t.trade_chain_id "
            "WHERE c.command_type IN ('SET_POSITION_TPSL_PARTIAL','REBUILD_PARTIAL_TPS') "
            "AND c.status IN ('SENT','DONE') "
            "AND t.lifecycle_state IN ('OPEN','PARTIALLY_CLOSED')"
        ).fetchall()
        result = []
        for cmd_id, chain_id, cmd_type, payload_json, symbol, side in rows:
            payload = json.loads(payload_json)
            if cmd_type == "REBUILD_PARTIAL_TPS":
                for tp in payload.get("tps", []):
                    result.append({
                        "cmd_id":   cmd_id,
                        "chain_id": chain_id,
                        "tp_level": int(tp["sequence"]),
                        "tp_price": float(tp["price"]),
                        "tp_size":  float(tp["qty"]),
                        "symbol":   symbol,
                        "side":     side,
                    })
            else:  # SET_POSITION_TPSL_PARTIAL — backward compat
                result.append({
                    "cmd_id":   cmd_id,
                    "chain_id": chain_id,
                    "tp_level": int(payload.get("tp_sequence", 1)),
                    "tp_price": float(payload.get("take_profit", 0)),
                    "tp_size":  float(payload.get("tp_size", 0)),
                    "symbol":   symbol,
                    "side":     side,
                })
        return result
    finally:
        conn.close()
```

---

## Flusso corretto post-fix (1 MARKET + 1 LIMIT + 2 TP intermedi)

```
Leg 1 fill (qty=6000):
  PostFillRebuilder → REBUILD cmd A {tps: [tp1@2000, tp2@2000]}
  Gateway: supersede REBUILD PENDING → niente da supersedere
           → adapter: cancel partial TPs (nessuno) → place tp1 → place tp2
  Exchange: 1 ordine tp1 @2000, 1 ordine tp2 @2000 ✅

Leg 2 fill (qty=4000, totale=10000):
  PostFillRebuilder → REBUILD cmd B {tps: [tp1@3333, tp2@3333]}
  Gateway: supersede cmd A DONE → marcato SUPERSEDED
           → adapter: cancel tp1@2000 + tp2@2000 → place tp1@3333 → place tp2@3333
  Exchange: 1 ordine tp1 @3333, 1 ordine tp2 @3333 ✅

TP1 scatta:
  TP_FILLED:chain:level:1 → 1 solo fill registrato ✅

TP2 scatta:
  TP_FILLED:chain:level:2 → 1 solo fill registrato ✅
```

---

## File modificati (riepilogo)

| File | Tipo modifica |
|---|---|
| `src/runtime_v2/lifecycle/post_fill_rebuilder.py` | Emette `REBUILD_PARTIAL_TPS` invece di N `SET_POSITION_TPSL_PARTIAL` |
| `src/runtime_v2/execution_gateway/gateway.py` | Rimuove logica `supersedes_previous`, aggiunge handler `REBUILD_PARTIAL_TPS` |
| `src/runtime_v2/execution_gateway/repositories.py` | Aggiunge `supersede_rebuild_commands` |
| `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/order_builder.py` | Aggiunge `_rebuild_partial_tps` |
| `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/adapter.py` | Aggiunge `_handle_rebuild_partial_tps` |
| `src/runtime_v2/execution_gateway/event_sync.py` | Aggiorna `_get_tp_reconciliation_entries` per `REBUILD_PARTIAL_TPS` |

## Non modificati

- `SET_POSITION_TPSL_PARTIAL` — rimane per usi non post-fill (entry_gate D-mode, comandi manuali)
- `supersede_tp_partial_commands` — rimane per backward compat
- Schema DB — nessuna migrazione necessaria
- `event_sync.py` idempotency key — `TP_FILLED:chain:level:N` torna corretta con 1 ordine per livello
