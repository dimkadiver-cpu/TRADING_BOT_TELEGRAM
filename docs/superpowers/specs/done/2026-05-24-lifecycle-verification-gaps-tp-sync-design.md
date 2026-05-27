# Design — Lifecycle Verification Gaps e TP Sync

**Data:** 2026-05-24  
**Branch:** `feat/unified-execution-plan`  
**Basato su:** `docs/Raggionamento/SPEC_lifecycle_verification_gaps_and_tp_sync.md`  
**Revisione:** Claude Code brainstorming session

---

## Scope

3 fix distinti che risolvono gap emersi in live demo Bybit:

| Fix | Problema | Soluzione |
|-----|----------|-----------|
| Fix 1 | `MOVE_STOP_*` e `SYNC_PROTECTIVE_ORDERS` non emettono eventi lifecycle | `insert_exchange_event()` nel gateway dopo retCode=0 |
| Fix 2 | `TP_FILLED` ha 2 idempotency key diverse → doppio processing | Unified key `TP_FILLED:{chain_id}:level:{tp_level}` |
| Fix 3 | TP Mode C rilevato con heuristica polling ~60s | `watchMyTrades` WS → fill reale entro ~1s |

---

## Principio architetturale consolidato

**Categoria B — operazioni posizione-level (sincrone):**  
`trading_stop` con `retCode=0` significa che Bybit ha modificato la posizione *prima* di rispondere.  
→ `retCode=0 + nessuna eccezione CCXT = operazione confermata = emetti evento lifecycle`  
→ Nessuna chiamata API aggiuntiva di verifica (over-engineering + race condition).

---

## Fix 1 — STOP_MOVED_CONFIRMED e PROTECTIVE_ORDERS_SYNCED

### Stato attuale

`gateway.py:253-254`:
```python
if cmd.command_type in _FIRE_AND_FORGET:
    self._repo.mark_done(cmd.command_id)
```
Nessun evento in `ops_exchange_events`. Il lifecycle rimane bloccato su `BE_MOVE_PENDING`.

**Dead code identificato:** `event_sync._normalize_and_save()` ha un branch `coid.role == "sync"` che emetterebbe `PROTECTIVE_ORDERS_SYNCED` — ma `SYNC_PROTECTIVE_ORDERS` è in `_FIRE_AND_FORGET` quindi non finisce mai in `get_sent_or_ack()`. **Va rimosso.**

### Soluzione

#### 1a — Nuovo metodo `GatewayCommandRepository.insert_exchange_event()`

```python
def insert_exchange_event(
    self,
    trade_chain_id: int,
    event_type: str,
    payload_json: str,
    idempotency_key: str,
) -> None:
    now = _now()
    conn = sqlite3.connect(self._db)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO ops_exchange_events "
            "(trade_chain_id, event_type, payload_json, processing_status, "
            "idempotency_key, received_at) VALUES (?,?,?,?,?,?)",
            (trade_chain_id, event_type, payload_json, "NEW", idempotency_key, now),
        )
        conn.commit()
    finally:
        conn.close()
```

**Nota:** `GatewayCommandRepository` usa `self._db` che punta a `ops.sqlite3` — stesso DB dove risiede `ops_exchange_events`. Nessuna dipendenza aggiuntiva da iniettare.

#### 1b — Mappa comando → evento in `gateway.py`

```python
_FIRE_AND_FORGET_EVENTS: dict[str, tuple[str, ...]] = {
    "MOVE_STOP_TO_BREAKEVEN": ("STOP_MOVED_CONFIRMED",),
    "MOVE_STOP":               ("STOP_MOVED_CONFIRMED",),
    "SYNC_PROTECTIVE_ORDERS":  ("PROTECTIVE_ORDERS_SYNCED",),
    # SET_POSITION_TPSL_*: il loro hit è rilevato da watchMyTrades/polling
    # CANCEL_PENDING_ENTRY: conferma via PENDING_ENTRY_CANCELLED_CONFIRMED
}
```

#### 1c — `_emit_confirmed_event()` in `gateway.py`

```python
def _emit_confirmed_event(
    self,
    cmd: ExecutionCommand,
    event_type: str,
    payload: dict,
) -> None:
    idempotency_key = f"{event_type}:{cmd.trade_chain_id}:{cmd.command_id}"
    self._repo.insert_exchange_event(
        trade_chain_id=cmd.trade_chain_id,
        event_type=event_type,
        payload_json=json.dumps(payload),
        idempotency_key=idempotency_key,
    )
```

#### 1d — Modifica nel loop principale di `gateway.process()`

```python
# Dopo mark_sent(), sostituisce il blocco fire-and-forget attuale:
if cmd.command_type in _FIRE_AND_FORGET:
    event_type_tuple = _FIRE_AND_FORGET_EVENTS.get(cmd.command_type)
    if event_type_tuple:
        event_type = event_type_tuple[0]
        event_payload = self._build_event_payload(cmd, event_type, payload)
        self._emit_confirmed_event(cmd, event_type, event_payload)
    self._repo.mark_done(cmd.command_id)
```

#### Payload eventi

**`STOP_MOVED_CONFIRMED`:**
```json
{
  "new_stop_price": "<payload.new_stop_price>",
  "is_breakeven": "<payload.is_breakeven | cmd.command_type == MOVE_STOP_TO_BREAKEVEN>",
  "command_id": "<cmd.command_id>"
}
```

**`PROTECTIVE_ORDERS_SYNCED`:**
```json
{
  "command_id": "<cmd.command_id>"
}
```

#### Idempotency key

```
STOP_MOVED_CONFIRMED:{trade_chain_id}:{command_id}
PROTECTIVE_ORDERS_SYNCED:{trade_chain_id}:{command_id}
```

Usa `command_id` (non `exchange_order_id` che non esiste per `trading_stop`).

#### Test richiesti

| Test | Verifica |
|------|----------|
| `test_move_stop_to_be_emits_stop_moved_confirmed` | `ops_exchange_events` ha `STOP_MOVED_CONFIRMED` con `is_breakeven=True` |
| `test_move_stop_emits_stop_moved_confirmed` | stesso con `is_breakeven=False` |
| `test_sync_protective_orders_emits_protective_orders_synced` | `ops_exchange_events` ha `PROTECTIVE_ORDERS_SYNCED` |
| `test_fire_and_forget_failed_does_not_emit_event` | se `place_order()` fallisce → nessun evento inserito |
| `test_set_tpsl_does_not_emit_direct_event` | `SET_POSITION_TPSL_*` non emettono eventi lifecycle diretti |

---

## Fix 2 — Unified idempotency key per `TP_FILLED`

### Problema

| Source | Chiave attuale |
|--------|----------------|
| `event_sync._normalize_and_save()` role="tp" | `TP_FILLED:{chain_id}:{exchange_order_id}` |
| `ws_fill_watcher._save_fill()` role="tp" | `TP_FILLED:{chain_id}:{exchange_order_id}` |
| `event_sync._save_tp_fill()` (polling) | `TP_FILLED:reconciliation:{chain_id}:{tp_level}` |
| `ws_fill_watcher._save_tp_fill_from_trade()` (nuovo) | da definire |

WS e polling usano chiavi diverse → `TP_FILLED` processato due volte → doppia riduzione `open_position_qty`.

### Soluzione — chiave unificata

```
TP_FILLED:{chain_id}:level:{tp_level}
```

**Rationale:** `tp_level` identifica univocamente un livello TP per chain. Una volta che un livello scatta, qualsiasi source successiva trova la riga già esistente e `INSERT OR IGNORE` non inserisce duplicati.

### File da aggiornare

**`event_sync._save_tp_fill()`** (polling):
```python
# Prima:
idempotency_key = f"TP_FILLED:reconciliation:{trade_chain_id}:{tp_level}"
# Dopo:
idempotency_key = f"TP_FILLED:{trade_chain_id}:level:{tp_level}"
```

**`event_sync._normalize_and_save()`** role="tp":
```python
# Prima (line 348):
idempotency_key = f"{event_type}:{coid.trade_chain_id}:{exchange_order_id}"
# Dopo (solo per tp, gli altri restano uguali):
idempotency_key = f"TP_FILLED:{coid.trade_chain_id}:level:{coid.sequence}"
```

**`ws_fill_watcher._save_fill()`** role="tp":
```python
# Prima:
f"{event_type}:{coid.trade_chain_id}:{exchange_order_id}"
# Dopo:
f"TP_FILLED:{coid.trade_chain_id}:level:{coid.sequence}"
```

**`event_sync._normalize_and_save()`** role="sync" → **rimosso** (dead code).

**`_tp_fill_event_exists()`** in `event_sync.py`: rimane invariata — serve per evitare la chiamata costosa a `get_position_qty()` nell'exchange. Con la unified key l'INSERT OR IGNORE gestisce l'idempotency, ma il check pre-emptivo evita la chiamata REST. 

### Test richiesti

| Test | Verifica |
|------|----------|
| `test_idempotency_key_tp_filled_unified` | WS inserisce `TP_FILLED`, poi `run_tp_reconciliation()`: esattamente 1 riga |

---

## Fix 3 — watchMyTrades per TP Mode C in tempo reale

### Problema

`SET_POSITION_TPSL_*` usa `trading_stop` (posizione-level). Non crea un ordine standalone con `clientOrderId`. `watchOrders` filtra per `clientOrderId` → fill TP Mode C ignorati. Rilevamento attuale: polling ~60s, no `fill_price`/`filled_qty` reali.

### Soluzione — `watchMyTrades` (CCXT Pro)

`watchMyTrades` riceve tutti i fill reali inclusi position-level, con `price`, `amount`, `symbol`, `side`. Nessun `clientOrderId`.

### Ristrutturazione task management

Il `_run_in_thread()` attuale esegue un solo task. Servono due task paralleli:

```python
def _run_in_thread(self) -> None:
    loop = asyncio.new_event_loop()
    self._loop = loop
    asyncio.set_event_loop(loop)
    self._loop_ready.set()
    self._watch_orders_task = loop.create_task(self._watch_orders_forever())
    self._watch_trades_task = loop.create_task(self._watch_trades_forever())
    try:
        loop.run_until_complete(
            asyncio.gather(
                self._watch_orders_task,
                self._watch_trades_task,
                return_exceptions=True,
            )
        )
    except asyncio.CancelledError:
        pass
    finally:
        # cleanup invariato
        ...
```

`_cancel_watch_task()` aggiornato per cancellare entrambi i task.

### Step 3a — Repository: `get_active_tp_commands()`

```python
def get_active_tp_commands(self, trade_chain_id: int) -> list[dict]:
    """Payload dei SET_POSITION_TPSL_* DONE per chain aperta (OPEN o PARTIALLY_CLOSED)."""
    conn = sqlite3.connect(self._db)
    try:
        rows = conn.execute(
            "SELECT c.payload_json FROM ops_execution_commands c "
            "JOIN ops_trade_chains t ON c.trade_chain_id = t.trade_chain_id "
            "WHERE c.trade_chain_id = ? "
            "AND c.command_type IN ('SET_POSITION_TPSL_PARTIAL', 'SET_POSITION_TPSL_FULL') "
            "AND c.status IN ('SENT', 'DONE') "
            "AND t.lifecycle_state IN ('OPEN', 'PARTIALLY_CLOSED')",
            (trade_chain_id,),
        ).fetchall()
        result = []
        for (payload_json,) in rows:
            try:
                result.append(json.loads(payload_json))
            except Exception:
                pass
        return result
    finally:
        conn.close()
```

### Step 3b — `_watch_trades_forever()` e `_process_trade_batch()`

```python
async def _watch_trades_forever(self, exchange) -> None:
    try:
        while not self._stop_event.is_set():
            try:
                trades = await exchange.watch_my_trades()
            except asyncio.CancelledError:
                raise
            except Exception:
                if self._stop_event.is_set():
                    break
                logger.exception("bybit watch_my_trades failed")
                await asyncio.sleep(5)
                continue
            await self._process_trade_batch(trades, exchange)
    finally:
        pass  # exchange.close() gestito dal caller

async def _process_trade_batch(self, trades: list[dict], exchange) -> None:
    if not trades:
        return
    for trade in trades:
        # Solo fill reduceOnly (TP chiudono posizione, non entry/SL)
        if not trade.get("reduceOnly", False):
            continue
        symbol = trade.get("symbol", "")
        side = trade.get("side", "")  # "sell" per LONG, "buy" per SHORT
        fill_price = float(trade.get("price") or 0.0)
        filled_qty = float(trade.get("amount") or 0.0)
        if not symbol or not fill_price:
            continue
        await self._match_and_save_tp_fill(trade, symbol, side, fill_price, filled_qty)
```

### Step 3c — Matching fill → chain + tp_level

```python
async def _match_and_save_tp_fill(self, trade, symbol, side, fill_price, filled_qty) -> None:
    # Cerca chain aperte con stesso symbol+side (lato opposto per close)
    # side del trade è opposto alla chain: sell close = LONG chain
    chain_side = "LONG" if side.lower() == "sell" else "SHORT"
    open_chains = self._repo.get_open_chains_for_symbol(symbol, chain_side)
    
    matches = []
    for chain_id in open_chains:
        tp_cmds = self._repo.get_active_tp_commands(chain_id)
        for cmd_payload in tp_cmds:
            tp_price = float(cmd_payload.get("take_profit") or 0.0)
            tp_level = int(cmd_payload.get("tp_sequence", 1))
            if tp_price == 0.0:
                continue
            # Tolerance ±1% per slippage
            if abs(fill_price - tp_price) / tp_price <= 0.01:
                matches.append((chain_id, tp_level, tp_price))
    
    if len(matches) != 1:
        # Ambiguo o nessun match → skip silenzioso, polling gestisce
        if len(matches) > 1:
            logger.warning(
                "ambiguous TP fill match: symbol=%s price=%s matches=%s",
                symbol, fill_price, matches,
            )
        return
    
    chain_id, tp_level, _ = matches[0]
    
    # is_final da posQty Bybit se disponibile, fallback False
    pos_qty_raw = trade.get("info", {}).get("posQty")
    if pos_qty_raw is not None:
        is_final = float(pos_qty_raw) == 0.0
    else:
        is_final = False  # conservativo, run_position_reconciliation() cattura il full-close
    
    exchange_trade_id = str(trade.get("id") or "")
    self._save_tp_fill_from_trade(
        chain_id=chain_id,
        tp_level=tp_level,
        fill_price=fill_price,
        filled_qty=filled_qty,
        is_final=is_final,
        exchange_trade_id=exchange_trade_id,
    )
```

### Step 3d — `_save_tp_fill_from_trade()`

```python
def _save_tp_fill_from_trade(
    self,
    chain_id: int,
    tp_level: int,
    fill_price: float,
    filled_qty: float,
    is_final: bool,
    exchange_trade_id: str,
) -> None:
    idempotency_key = f"TP_FILLED:{chain_id}:level:{tp_level}"
    payload = json.dumps({
        "tp_level": tp_level,
        "is_final": is_final,
        "fill_price": fill_price,
        "filled_qty": filled_qty,
        "source": "watch_my_trades",
        "exchange_trade_id": exchange_trade_id,
    })
    conn = sqlite3.connect(self._ops_db_path)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO ops_exchange_events "
            "(trade_chain_id, event_type, payload_json, processing_status, "
            "idempotency_key, received_at) VALUES (?,?,?,?,?,?)",
            (chain_id, "TP_FILLED", payload, "NEW", idempotency_key, _now()),
        )
        conn.commit()
    finally:
        conn.close()
```

### Repository aggiuntivo necessario

```python
def get_open_chains_for_symbol(self, symbol: str, side: str) -> list[int]:
    """chain_id di catene aperte (OPEN/PARTIALLY_CLOSED) per symbol+side."""
    conn = sqlite3.connect(self._db)
    try:
        rows = conn.execute(
            "SELECT trade_chain_id FROM ops_trade_chains "
            "WHERE symbol=? AND side=? "
            "AND lifecycle_state IN ('OPEN','PARTIALLY_CLOSED')",
            (symbol, side),
        ).fetchall()
        return [int(r[0]) for r in rows]
    finally:
        conn.close()
```

### `is_final` — decisione finale

| Scenario | `posQty` disponibile | `is_final` |
|----------|---------------------|------------|
| Bybit include `info.posQty=0` | ✓ | `True` |
| Bybit include `info.posQty>0` | ✓ | `False` |
| Campo assente | ✗ | `False` (conservativo) |

`run_position_reconciliation()` cattura i full-close in max 60s se `is_final` è erroneamente `False`.

### Test richiesti

| Test | Verifica |
|------|----------|
| `test_watch_trades_tp_fill_matched` | trade con `price` che matcha TP attivo → INSERT con `fill_price` e `filled_qty` reali |
| `test_watch_trades_tp_fill_ambiguous_skipped` | 2 chain stesso symbol/side, TP a prezzi simili → nessun INSERT |
| `test_watch_trades_tp_fill_is_final_true` | `posQty=0` → `is_final=True` |
| `test_watch_trades_tp_fill_is_final_false_fallback` | `posQty` assente → `is_final=False` |
| `test_watch_trades_ignores_non_reduce_only` | fill non reduceOnly → ignorati |
| `test_idempotency_key_tp_filled_unified` | WS + polling stesso evento → 1 sola riga in DB |

---

## Sequenza di implementazione

```
Step 1 — Fix idempotency key TP_FILLED              (~30 min)
  1.1  event_sync._save_tp_fill() → nuova chiave
  1.2  event_sync._normalize_and_save() role="tp" → nuova chiave
  1.3  event_sync._normalize_and_save() role="sync" → RIMOSSO (dead code)
  1.4  ws_fill_watcher._save_fill() role="tp" → nuova chiave
  ✓ test: idempotency WS + polling (1 sola riga)

Step 2 — Fix STOP_MOVED_CONFIRMED e PROTECTIVE_ORDERS_SYNCED   (~2h)
  2.1  repositories.insert_exchange_event()
  2.2  gateway._FIRE_AND_FORGET_EVENTS mappa
  2.3  gateway._emit_confirmed_event()
  2.4  gateway._build_event_payload()
  2.5  gateway.process() → chiama emit + mark_done
  ✓ test: 5 casi gateway

Step 3 — watchMyTrades                              (~1 giorno)
  3a  Ristrutturazione task management (2 task paralleli, cancel entrambi)
  3b  repositories.get_active_tp_commands()
      repositories.get_open_chains_for_symbol()
  3c  ws_fill_watcher._watch_trades_forever()
      ws_fill_watcher._process_trade_batch()
      ws_fill_watcher._match_and_save_tp_fill()
  3d  ws_fill_watcher._save_tp_fill_from_trade()
  ✓ test: 6 casi WS trade matching

Step 4 — Verifica integrazione                      (~2h)
  4.1  pytest tests/runtime_v2/ — nessuna regressione
  4.2  Verifica su Bybit Demo: entry → TP hit → be_protection_status=PROTECTED
```

---

## Acceptance criteria

| # | Criterio | Verifica |
|---|----------|----------|
| AC1 | Dopo `MOVE_STOP_TO_BREAKEVEN` retCode=0, `ops_exchange_events` ha `STOP_MOVED_CONFIRMED` senza chiamate aggiuntive | test unitario |
| AC2 | Dopo `STOP_MOVED_CONFIRMED` processato, `be_protection_status=PROTECTED` e `current_stop_price` aggiornato | test integrazione lifecycle |
| AC3 | Dopo `SYNC_PROTECTIVE_ORDERS` retCode=0, `ops_exchange_events` ha `PROTECTIVE_ORDERS_SYNCED` | test unitario |
| AC4 | `TP_FILLED` da WS e polling → stessa idempotency key → 1 sola riga | test idempotency |
| AC5 | `watchMyTrades` inserisce `TP_FILLED` con `fill_price` e `filled_qty` reali entro ~1s | verifica live demo |
| AC6 | Match non univoco → skip silenzioso, polling fallback gestisce | test chain ambigue |
| AC7 | Nessuna regressione | `pytest tests/runtime_v2/` |

---

## File toccati

```
src/runtime_v2/execution_gateway/gateway.py                    # Fix 1
src/runtime_v2/execution_gateway/repositories.py               # Fix 1 + Fix 3
src/runtime_v2/execution_gateway/event_sync.py                 # Fix 2 (rimozione dead code + nuova chiave)
src/runtime_v2/execution_gateway/adapters/ccxt_bybit/ws_fill_watcher.py  # Fix 2 + Fix 3
tests/runtime_v2/execution_gateway/test_gateway.py             # Fix 1 tests
tests/runtime_v2/execution_gateway/test_event_sync.py          # Fix 2 tests
tests/runtime_v2/execution_gateway/test_ws_fill_watcher.py     # Fix 2 + Fix 3 tests (nuovo o esteso)
```

---

## Rischi

| Rischio | Probabilità | Mitigazione |
|---------|-------------|-------------|
| `trade["info"]["posQty"]` assente su Bybit Demo | Media | `is_final=False` conservativo, `run_position_reconciliation()` cattura |
| Match ambiguo su multi-chain stesso symbol/side | Bassa in demo | Skip + polling fallback + log per analisi |
| `watchMyTrades` riceve fill entry/SL già gestiti da `watchOrders` | Certa | Filtrare `reduceOnly=True` prima del matching |
| Task management asincrono: eccezione in un task non cancella l'altro | Bassa | `asyncio.gather(return_exceptions=True)` assorbe senza propagare |
