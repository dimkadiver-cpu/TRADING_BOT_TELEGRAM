# Partial TP Rebuild Fix — `REBUILD_PARTIAL_TPS` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sostituire i multipli `SET_POSITION_TPSL_PARTIAL` emessi dopo ogni fill con un singolo comando atomico `REBUILD_PARTIAL_TPS` che cancella gli ordini TP parziali esistenti sull'exchange e li rimette tutti in un'unica operazione, eliminando 3 bug concatenati.

**Architecture:** `PostFillProtectionRebuilder` emette 1 `REBUILD_PARTIAL_TPS` con tutti i livelli TP in payload. Il gateway supersede REBUILD precedenti per la stessa chain, poi l'adapter Bybit cancella gli ordini condizionali reduce-only esistenti e piazza ogni livello via `trading_stop Partial`. `event_sync` espande i REBUILD per la riconciliazione dei fill.

**Tech Stack:** Python 3.12, Pydantic v2, SQLite, ccxt (Bybit V5), pytest

**Spec:** `docs/superpowers/specs/2026-05-26-partial-tp-rebuild-fix-design.md`

---

## File map

| File | Azione |
|---|---|
| `src/runtime_v2/lifecycle/post_fill_rebuilder.py` | Modify — emette `REBUILD_PARTIAL_TPS` |
| `src/runtime_v2/execution_gateway/repositories.py` | Modify — aggiunge `supersede_rebuild_commands` |
| `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/order_builder.py` | Modify — aggiunge `_rebuild_partial_tps` |
| `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/adapter.py` | Modify — aggiunge `_handle_rebuild_partial_tps` |
| `src/runtime_v2/execution_gateway/gateway.py` | Modify — handler `REBUILD_PARTIAL_TPS`, rimuove vecchia logica `supersedes_previous` |
| `src/runtime_v2/execution_gateway/event_sync.py` | Modify — `_get_tp_reconciliation_entries` include `REBUILD_PARTIAL_TPS` |
| `tests/runtime_v2/lifecycle/test_post_fill_rebuilder.py` | Modify — aggiorna test esistenti, aggiunge nuovi |
| `tests/runtime_v2/execution_gateway/test_bybit_order_builder.py` | Modify — aggiunge test per `rebuild_partial_tps` |
| `tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py` | Modify — aggiunge test per `_handle_rebuild_partial_tps` |
| `tests/runtime_v2/execution_gateway/test_gateway.py` | Modify — aggiunge test supersede REBUILD |
| `tests/runtime_v2/execution_gateway/test_event_sync.py` | Modify — aggiunge test riconciliazione REBUILD |

---

## Task 1: `PostFillProtectionRebuilder` — emette `REBUILD_PARTIAL_TPS`

**Files:**
- Modify: `src/runtime_v2/lifecycle/post_fill_rebuilder.py`
- Modify: `tests/runtime_v2/lifecycle/test_post_fill_rebuilder.py`

- [ ] **Step 1: Aggiorna i test esistenti che si aspettano `SET_POSITION_TPSL_PARTIAL`**

Nel file `tests/runtime_v2/lifecycle/test_post_fill_rebuilder.py`, sostituisci i test `test_multi_tp_emits_intermediate_tp_commands` e `test_multi_tp_tp_size_based_on_filled_qty` con le nuove aspettative:

```python
def test_multi_tp_emits_single_rebuild_command():
    """Dopo un fill con 2 TP intermedi, deve emettere 1 solo REBUILD_PARTIAL_TPS."""
    chain = _make_chain(plan_state_json=_plan_multi_tp([51000.0, 52000.0]))
    cmds = _rebuilder().build_after_fill(chain, filled_entry_qty=0.02, exchange_event_id=7)
    assert len(cmds) == 1
    assert cmds[0].command_type == "REBUILD_PARTIAL_TPS"
    payload = json.loads(cmds[0].payload_json)
    assert len(payload["tps"]) == 2
    assert payload["tps"][0]["sequence"] == 1
    assert payload["tps"][0]["price"] == 51000.0
    assert payload["tps"][1]["sequence"] == 2
    assert payload["tps"][1]["price"] == 52000.0


def test_multi_tp_rebuild_qty_based_on_filled_qty():
    """qty per livello = filled_entry_qty / n_total_tps (arrotondata a 8 decimali)."""
    chain = _make_chain(plan_state_json=_plan_multi_tp([51000.0]))
    cmds = _rebuilder().build_after_fill(chain, filled_entry_qty=0.10, exchange_event_id=9)
    assert len(cmds) == 1
    payload = json.loads(cmds[0].payload_json)
    assert payload["tps"][0]["qty"] == pytest.approx(0.05)


def test_multi_tp_rebuild_two_levels_equal_qty():
    """Con 2 intermedi + 1 finale = 3 totali → ogni livello = filled/3."""
    chain = _make_chain(plan_state_json=_plan_multi_tp([51000.0, 52000.0]))
    cmds = _rebuilder().build_after_fill(chain, filled_entry_qty=0.30, exchange_event_id=11)
    payload = json.loads(cmds[0].payload_json)
    assert payload["tps"][0]["qty"] == pytest.approx(0.10)
    assert payload["tps"][1]["qty"] == pytest.approx(0.10)


def test_multi_tp_rebuild_carries_hedge_mode_and_position_idx():
    chain = _make_chain(
        side="SHORT",
        plan_state_json=_plan_multi_tp([51000.0]),
        risk_snap={"hedge_mode": True},
    )
    cmds = _rebuilder().build_after_fill(chain, filled_entry_qty=0.10, exchange_event_id=13)
    payload = json.loads(cmds[0].payload_json)
    assert payload["hedge_mode"] is True
    assert payload["position_idx"] == 2


def test_multi_tp_rebuild_idempotency_key_uses_event_id():
    chain = _make_chain(plan_state_json=_plan_multi_tp([51000.0]))
    cmds = _rebuilder().build_after_fill(chain, filled_entry_qty=0.01, exchange_event_id=99)
    assert cmds[0].idempotency_key == "rebuild_partial_tps:1:99"


def test_multi_tp_rebuild_preserve_flags_set():
    chain = _make_chain(plan_state_json=_plan_multi_tp([51000.0]))
    cmds = _rebuilder().build_after_fill(chain, filled_entry_qty=0.01, exchange_event_id=5)
    payload = json.loads(cmds[0].payload_json)
    assert payload["preserve_sl"] is True
    assert payload["preserve_full_tp"] is True


def test_empty_tps_emits_no_command():
    """Se non ci sono intermediate_tps non deve emettere nulla."""
    chain = _make_chain(plan_state_json=_plan_single_tp())
    cmds = _rebuilder().build_after_fill(chain, filled_entry_qty=0.01, exchange_event_id=5)
    assert cmds == []
```

Rimuovi anche `test_multi_tp_carries_hedge_mode_and_position_idx_from_chain` (rimpiazzato da `test_multi_tp_rebuild_carries_hedge_mode_and_position_idx`).

- [ ] **Step 2: Esegui i test — verifica che falliscano**

```
pytest tests/runtime_v2/lifecycle/test_post_fill_rebuilder.py -v
```

Expected: i nuovi test falliscono con `AssertionError` (command_type ancora `SET_POSITION_TPSL_PARTIAL`).

- [ ] **Step 3: Implementa la nuova logica in `post_fill_rebuilder.py`**

Sostituisci il metodo `build_after_fill` con:

```python
def build_after_fill(
    self,
    chain: TradeChain,
    filled_entry_qty: float,
    exchange_event_id: int,
) -> list[ExecutionCommand]:
    try:
        plan = json.loads(chain.plan_state_json or "{}")
    except Exception:
        return []

    if plan.get("rebuild_policy", "NONE") != "ON_EACH_ENTRY_FILL":
        return []

    intermediate_tps: list[float] = plan.get("intermediate_tps", [])
    if not intermediate_tps:
        return []

    n_total_tps = len(intermediate_tps) + 1
    chain_id = chain.trade_chain_id
    hedge_mode, position_idx = self._resolve_position_context(chain)

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

    return [ExecutionCommand(
        trade_chain_id=chain_id,
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
        idempotency_key=f"rebuild_partial_tps:{chain_id}:{exchange_event_id}",
    )]
```

- [ ] **Step 4: Esegui tutti i test del rebuilder**

```
pytest tests/runtime_v2/lifecycle/test_post_fill_rebuilder.py -v
```

Expected: tutti PASS.

- [ ] **Step 5: Commit**

```
git add src/runtime_v2/lifecycle/post_fill_rebuilder.py tests/runtime_v2/lifecycle/test_post_fill_rebuilder.py
git commit -m "feat(lifecycle): PostFillProtectionRebuilder emits REBUILD_PARTIAL_TPS instead of N SET_POSITION_TPSL_PARTIAL"
```

---

## Task 2: `repositories.py` — `supersede_rebuild_commands`

**Files:**
- Modify: `src/runtime_v2/execution_gateway/repositories.py`
- Modify: `tests/runtime_v2/execution_gateway/test_gateway.py`

- [ ] **Step 1: Scrivi il test**

Aggiungi alla fine di `tests/runtime_v2/execution_gateway/test_gateway.py`:

```python
def _insert_cmd_with_status(db_path, cmd_id, chain_id, cmd_type, status, payload=None):
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_execution_commands "
        "(command_id, trade_chain_id, command_type, status, payload_json, "
        "idempotency_key, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (cmd_id, chain_id, cmd_type, status,
         json.dumps(payload or {}), f"idem:{cmd_id}", now, now),
    )
    conn.commit()
    conn.close()


def test_supersede_rebuild_commands_marks_pending_as_superseded(ops_db):
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_cmd_with_status(ops_db, 201, 1, "REBUILD_PARTIAL_TPS", "PENDING")
    _insert_cmd_with_status(ops_db, 202, 1, "REBUILD_PARTIAL_TPS", "PENDING")
    _insert_cmd_with_status(ops_db, 203, 1, "REBUILD_PARTIAL_TPS", "DONE")

    repo = GatewayCommandRepository(ops_db)
    repo.supersede_rebuild_commands(1, exclude_command_id=202, statuses=("PENDING",))

    conn = sqlite3.connect(ops_db)
    rows = {r[0]: r[1] for r in conn.execute(
        "SELECT command_id, status FROM ops_execution_commands WHERE command_id IN (201,202,203)"
    ).fetchall()}
    conn.close()

    assert rows[201] == "SUPERSEDED"   # PENDING escluso → superseduto
    assert rows[202] == "PENDING"       # è il comando corrente → intoccato
    assert rows[203] == "DONE"          # status non in filtro → intoccato


def test_supersede_rebuild_commands_does_not_touch_other_command_types(ops_db):
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_cmd_with_status(ops_db, 301, 1, "SET_POSITION_TPSL_PARTIAL", "PENDING")
    _insert_cmd_with_status(ops_db, 302, 1, "REBUILD_PARTIAL_TPS", "PENDING")

    repo = GatewayCommandRepository(ops_db)
    repo.supersede_rebuild_commands(1, exclude_command_id=302, statuses=("PENDING",))

    conn = sqlite3.connect(ops_db)
    rows = {r[0]: r[1] for r in conn.execute(
        "SELECT command_id, status FROM ops_execution_commands WHERE command_id IN (301,302)"
    ).fetchall()}
    conn.close()

    assert rows[301] == "PENDING"    # altro tipo → intoccato
    assert rows[302] == "PENDING"    # escluso → intoccato
```

- [ ] **Step 2: Esegui il test — verifica che fallisca**

```
pytest tests/runtime_v2/execution_gateway/test_gateway.py::test_supersede_rebuild_commands_marks_pending_as_superseded -v
```

Expected: `AttributeError: 'GatewayCommandRepository' object has no attribute 'supersede_rebuild_commands'`

- [ ] **Step 3: Implementa `supersede_rebuild_commands` in `repositories.py`**

Aggiungi il metodo nella classe `GatewayCommandRepository` dopo `supersede_tp_partial_commands`:

```python
def supersede_rebuild_commands(
    self,
    trade_chain_id: int,
    exclude_command_id: int,
    *,
    statuses: tuple[str, ...],
) -> None:
    """Marks SUPERSEDED all REBUILD_PARTIAL_TPS commands for the chain except the current one."""
    now = _now()
    placeholders = ",".join("?" for _ in statuses)
    conn = sqlite3.connect(self._db)
    try:
        conn.execute(
            "UPDATE ops_execution_commands SET status='SUPERSEDED', updated_at=? "
            "WHERE trade_chain_id=? AND command_type='REBUILD_PARTIAL_TPS' "
            f"AND status IN ({placeholders}) AND command_id != ?",
            (now, trade_chain_id, *statuses, exclude_command_id),
        )
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 4: Esegui i nuovi test**

```
pytest tests/runtime_v2/execution_gateway/test_gateway.py::test_supersede_rebuild_commands_marks_pending_as_superseded tests/runtime_v2/execution_gateway/test_gateway.py::test_supersede_rebuild_commands_does_not_touch_other_command_types -v
```

Expected: entrambi PASS.

- [ ] **Step 5: Commit**

```
git add src/runtime_v2/execution_gateway/repositories.py tests/runtime_v2/execution_gateway/test_gateway.py
git commit -m "feat(gateway): add supersede_rebuild_commands to GatewayCommandRepository"
```

---

## Task 3: `order_builder.py` — mapping `REBUILD_PARTIAL_TPS`

**Files:**
- Modify: `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/order_builder.py`
- Modify: `tests/runtime_v2/execution_gateway/test_bybit_order_builder.py`

- [ ] **Step 1: Scrivi il test**

Aggiungi alla fine di `tests/runtime_v2/execution_gateway/test_bybit_order_builder.py`:

```python
def test_rebuild_partial_tps_builds_rebuild_params():
    """REBUILD_PARTIAL_TPS deve mappare ad action='rebuild_partial_tps' con tps nel payload."""
    builder = BybitOrderBuilder()
    params = builder.build(
        "REBUILD_PARTIAL_TPS",
        {
            "symbol": "REQUSDT",
            "side": "SHORT",
            "hedge_mode": True,
            "position_idx": 2,
            "preserve_sl": True,
            "preserve_full_tp": True,
            "tps": [
                {"sequence": 1, "price": 0.090, "qty": 3333.0,
                 "order_type": "Limit", "limit_price": 0.090, "trigger_by": "MarkPrice"},
                {"sequence": 2, "price": 0.085, "qty": 3333.0,
                 "order_type": "Limit", "limit_price": 0.085, "trigger_by": "MarkPrice"},
            ],
        },
        "tsb:10:99:rebuild:1",
    )
    assert params.action == "rebuild_partial_tps"
    assert params.symbol == "REQUSDT"
    assert params.position_side == "SHORT"
    assert len(params.extra_params["tps"]) == 2
    assert params.extra_params["position_idx"] == 2
    assert params.extra_params["preserve_sl"] is True
    assert params.extra_params["preserve_full_tp"] is True


def test_rebuild_partial_tps_unknown_command_raises():
    """Command type sconosciuto deve sollevare ValueError."""
    builder = BybitOrderBuilder()
    with pytest.raises(ValueError, match="Unknown command_type"):
        builder.build("UNKNOWN_CMD", {}, "coid")
```

- [ ] **Step 2: Esegui il test — verifica che fallisca**

```
pytest tests/runtime_v2/execution_gateway/test_bybit_order_builder.py::test_rebuild_partial_tps_builds_rebuild_params -v
```

Expected: `ValueError: Unknown command_type: 'REBUILD_PARTIAL_TPS'`

- [ ] **Step 3: Implementa `_rebuild_partial_tps` in `order_builder.py`**

Aggiungi nella classe `BybitOrderBuilder`, nel metodo `build`, la riga di dispatch prima di `raise ValueError`:

```python
if command_type == "REBUILD_PARTIAL_TPS":
    return self._rebuild_partial_tps(payload)
```

Aggiungi il metodo privato:

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

- [ ] **Step 4: Esegui tutti i test dell'order builder**

```
pytest tests/runtime_v2/execution_gateway/test_bybit_order_builder.py -v
```

Expected: tutti PASS.

- [ ] **Step 5: Commit**

```
git add src/runtime_v2/execution_gateway/adapters/ccxt_bybit/order_builder.py tests/runtime_v2/execution_gateway/test_bybit_order_builder.py
git commit -m "feat(adapter): add REBUILD_PARTIAL_TPS mapping in BybitOrderBuilder"
```

---

## Task 4: `adapter.py` — `_handle_rebuild_partial_tps`

**Files:**
- Modify: `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/adapter.py`
- Modify: `tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py`

- [ ] **Step 1: Scrivi i test**

Aggiungi alla fine di `tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py`:

```python
def _make_open_order(order_id, side, amount, stop_price, reduce_only=True):
    return {
        "id": order_id,
        "side": side,
        "amount": amount,
        "stopPrice": stop_price,
        "reduceOnly": reduce_only,
    }


def test_rebuild_partial_tps_cancels_existing_partial_tp_orders():
    """Deve cancellare gli ordini reduce-only condizionali con qty != full_qty."""
    exchange = MagicMock()
    exchange.fetch_positions.return_value = [{
        "side": "SHORT",
        "contracts": 10000.0,
        "info": {"positionIdx": 2},
    }]
    exchange.fetch_open_orders.return_value = [
        _make_open_order("ord_old1", "Buy", 3475.5, "0.090"),   # partial TP da cancellare
        _make_open_order("ord_old2", "Buy", 5441.0, "0.090"),   # partial TP da cancellare
        _make_open_order("ord_full", "Buy", 10000.0, "0.079"),  # full TP → NON cancellare
    ]
    exchange.private_post_v5_position_trading_stop.return_value = {
        "retCode": 0, "retMsg": "OK"
    }

    adapter = _make_adapter(exchange)
    result = adapter.place_order(
        command_type="REBUILD_PARTIAL_TPS",
        payload={
            "symbol": "REQUSDT",
            "side": "SHORT",
            "hedge_mode": True,
            "position_idx": 2,
            "preserve_sl": True,
            "preserve_full_tp": True,
            "tps": [
                {"sequence": 1, "price": 0.090, "qty": 5441.0,
                 "order_type": "Limit", "limit_price": 0.090, "trigger_by": "MarkPrice"},
            ],
        },
        client_order_id="tsb:10:99:rebuild:1",
        execution_account_id="bybit_demo",
        connector="bybit",
    )

    assert result.success is True
    # cancel chiamato per i 2 partial TP, NON per il full TP
    assert exchange.cancel_order.call_count == 2
    cancelled_ids = {c.args[0] for c in exchange.cancel_order.call_args_list}
    assert "ord_old1" in cancelled_ids
    assert "ord_old2" in cancelled_ids
    assert "ord_full" not in cancelled_ids


def test_rebuild_partial_tps_places_each_level_via_trading_stop():
    """Deve chiamare trading_stop una volta per ogni livello TP."""
    exchange = MagicMock()
    exchange.fetch_positions.return_value = [{
        "side": "SHORT", "contracts": 10000.0, "info": {"positionIdx": 2},
    }]
    exchange.fetch_open_orders.return_value = []
    exchange.private_post_v5_position_trading_stop.return_value = {
        "retCode": 0, "retMsg": "OK"
    }

    adapter = _make_adapter(exchange)
    result = adapter.place_order(
        command_type="REBUILD_PARTIAL_TPS",
        payload={
            "symbol": "REQUSDT",
            "side": "SHORT",
            "hedge_mode": True,
            "position_idx": 2,
            "preserve_sl": True,
            "preserve_full_tp": True,
            "tps": [
                {"sequence": 1, "price": 0.090, "qty": 3333.0,
                 "order_type": "Limit", "limit_price": 0.090, "trigger_by": "MarkPrice"},
                {"sequence": 2, "price": 0.085, "qty": 3333.0,
                 "order_type": "Limit", "limit_price": 0.085, "trigger_by": "MarkPrice"},
            ],
        },
        client_order_id="tsb:10:99:rebuild:1",
        execution_account_id="bybit_demo",
        connector="bybit",
    )

    assert result.success is True
    assert exchange.private_post_v5_position_trading_stop.call_count == 2
    calls = exchange.private_post_v5_position_trading_stop.call_args_list
    assert calls[0].args[0]["takeProfit"] == "0.09"
    assert calls[0].args[0]["tpSize"] == "3333.0"
    assert calls[1].args[0]["takeProfit"] == "0.085"
    assert calls[1].args[0]["tpSize"] == "3333.0"


def test_rebuild_partial_tps_cancel_error_does_not_block_place():
    """Errore nel cancel di un ordine non deve bloccare il place dei nuovi TP."""
    exchange = MagicMock()
    exchange.fetch_positions.return_value = [{
        "side": "SHORT", "contracts": 5000.0, "info": {"positionIdx": 2},
    }]
    exchange.fetch_open_orders.return_value = [
        _make_open_order("ord_partial", "Buy", 2500.0, "0.090"),
    ]
    exchange.cancel_order.side_effect = Exception("already filled")
    exchange.private_post_v5_position_trading_stop.return_value = {
        "retCode": 0, "retMsg": "OK"
    }

    adapter = _make_adapter(exchange)
    result = adapter.place_order(
        command_type="REBUILD_PARTIAL_TPS",
        payload={
            "symbol": "REQUSDT",
            "side": "SHORT",
            "hedge_mode": True,
            "position_idx": 2,
            "preserve_sl": True,
            "preserve_full_tp": True,
            "tps": [
                {"sequence": 1, "price": 0.090, "qty": 2500.0,
                 "order_type": "Limit", "limit_price": 0.090, "trigger_by": "MarkPrice"},
            ],
        },
        client_order_id="tsb:10:99:rebuild:1",
        execution_account_id="bybit_demo",
        connector="bybit",
    )

    assert result.success is True
    assert exchange.private_post_v5_position_trading_stop.call_count == 1


def test_rebuild_partial_tps_trading_stop_failure_returns_failure():
    """Se trading_stop ritorna retCode != 0, AdapterResult.success deve essere False."""
    exchange = MagicMock()
    exchange.fetch_positions.return_value = [{
        "side": "SHORT", "contracts": 5000.0, "info": {"positionIdx": 2},
    }]
    exchange.fetch_open_orders.return_value = []
    exchange.private_post_v5_position_trading_stop.return_value = {
        "retCode": 110001, "retMsg": "params error"
    }

    adapter = _make_adapter(exchange)
    result = adapter.place_order(
        command_type="REBUILD_PARTIAL_TPS",
        payload={
            "symbol": "REQUSDT",
            "side": "SHORT",
            "hedge_mode": True,
            "position_idx": 2,
            "preserve_sl": True,
            "preserve_full_tp": True,
            "tps": [
                {"sequence": 1, "price": 0.090, "qty": 5000.0,
                 "order_type": "Limit", "limit_price": 0.090, "trigger_by": "MarkPrice"},
            ],
        },
        client_order_id="tsb:10:99:rebuild:1",
        execution_account_id="bybit_demo",
        connector="bybit",
    )

    assert result.success is False
    assert "110001" in result.error
```

- [ ] **Step 2: Esegui i test — verifica che falliscano**

```
pytest tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py::test_rebuild_partial_tps_cancels_existing_partial_tp_orders tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py::test_rebuild_partial_tps_places_each_level_via_trading_stop -v
```

Expected: FAIL con `ValueError: Unknown command_type: 'REBUILD_PARTIAL_TPS'` o `AttributeError`.

- [ ] **Step 3: Implementa `_handle_rebuild_partial_tps` in `adapter.py`**

Nel metodo `place_order` di `CcxtBybitAdapter`, aggiungi il dispatch nel blocco delle action dopo `trading_stop_partial`:

```python
if params.action == "rebuild_partial_tps":
    return self._handle_rebuild_partial_tps(
        params.symbol, params.position_side, params.extra_params
    )
```

Aggiungi il metodo privato nella classe `CcxtBybitAdapter`:

```python
def _handle_rebuild_partial_tps(
    self,
    symbol: str,
    position_side: str,
    extra: dict,
) -> "AdapterResult":
    """Cancel existing partial TP orders then place each TP level atomically."""
    position_idx = extra["position_idx"]
    tps = extra["tps"]
    close_side = "Buy" if position_side == "SHORT" else "Sell"

    # 1. Quota posizione aperta per distinguere partial da full TP
    try:
        positions = self._exchange.fetch_positions([symbol])
    except Exception as exc:
        logger.warning("rebuild_partial_tps: fetch_positions failed: %s", exc)
        positions = []
    pos = next(
        (p for p in positions
         if p.get("side", "").upper() == position_side.upper()
         and int(p.get("info", {}).get("positionIdx", 0)) == position_idx),
        None,
    )
    full_qty = float(pos["contracts"]) if pos else 0.0

    # 2. Trova ordini TP parziali aperti (reduce-only condizionali, qty != full_qty)
    try:
        open_orders = self._exchange.fetch_open_orders(symbol)
    except Exception as exc:
        logger.warning("rebuild_partial_tps: fetch_open_orders failed: %s", exc)
        open_orders = []
    partial_tp_orders = [
        o for o in open_orders
        if o.get("reduceOnly")
        and o.get("stopPrice")
        and o.get("side", "").capitalize() == close_side
        and abs(float(o.get("amount", 0)) - full_qty) > 0.01
    ]

    # 3. Cancella ciascun ordine parziale (errori non bloccanti)
    for o in partial_tp_orders:
        try:
            self._exchange.cancel_order(o["id"], symbol)
        except Exception as exc:
            logger.warning(
                "rebuild_partial_tps: cancel order %s failed (may have already filled): %s",
                o["id"], exc,
            )

    # 4. Place ogni livello TP in sequenza crescente
    bybit_symbol = self._normalize_bybit_symbol(symbol)
    for tp in sorted(tps, key=lambda t: t["sequence"]):
        extra_params: dict = {
            "positionIdx": position_idx,
            "tpslMode":    "Partial",
            "takeProfit":  str(float(tp["price"])),
            "tpSize":      str(float(tp["qty"])),
            "tpOrderType": tp.get("order_type", "Limit"),
            "tpTriggerBy": tp.get("trigger_by", "MarkPrice"),
        }
        if tp.get("order_type") == "Limit" and tp.get("limit_price"):
            extra_params["tpLimitPrice"] = str(float(tp["limit_price"]))

        try:
            resp = self._exchange.private_post_v5_position_trading_stop({
                "category": "linear",
                "symbol":   bybit_symbol,
                **extra_params,
            })
        except Exception as exc:
            return AdapterResult(success=False, error=f"tp{tp['sequence']}: {exc}")

        ret_code, ret_msg = self._parse_trading_stop_retcode(resp)
        if ret_code != 0:
            logger.warning(
                "rebuild_partial_tps tp%s retCode=%s msg=%s",
                tp["sequence"], ret_code, ret_msg,
            )
            return AdapterResult(
                success=False,
                error=f"tp{tp['sequence']}: retCode={ret_code}: {ret_msg}",
            )

    return AdapterResult(success=True)
```

- [ ] **Step 4: Esegui tutti i nuovi test dell'adapter**

```
pytest tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py -k "rebuild" -v
```

Expected: tutti e 4 PASS.

- [ ] **Step 5: Esegui la suite completa dell'adapter per regressioni**

```
pytest tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py -v
```

Expected: tutti PASS.

- [ ] **Step 6: Commit**

```
git add src/runtime_v2/execution_gateway/adapters/ccxt_bybit/adapter.py tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py
git commit -m "feat(adapter): add _handle_rebuild_partial_tps — cancel-all + replace partial TPs on Bybit"
```

---

## Task 5: `gateway.py` — handler `REBUILD_PARTIAL_TPS` + rimozione vecchia logica

**Files:**
- Modify: `src/runtime_v2/execution_gateway/gateway.py`
- Modify: `tests/runtime_v2/execution_gateway/test_gateway.py`

- [ ] **Step 1: Scrivi i test**

Aggiungi alla fine di `tests/runtime_v2/execution_gateway/test_gateway.py`:

```python
def _rebuild_payload(tps=None):
    return {
        "symbol": "BTC/USDT",
        "side": "SHORT",
        "hedge_mode": False,
        "position_idx": 0,
        "preserve_sl": True,
        "preserve_full_tp": True,
        "tps": tps or [
            {"sequence": 1, "price": 48000.0, "qty": 0.01,
             "order_type": "Limit", "limit_price": 48000.0, "trigger_by": "MarkPrice"},
        ],
    }


def test_rebuild_partial_tps_supersedes_pending_before_send(ops_db):
    """Quando gateway processa REBUILD, supersede i REBUILD PENDING precedenti."""
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    # cmd 401 è PENDING (vecchio rebuild), cmd 402 è quello corrente
    _insert_cmd_with_status(ops_db, 401, 1, "REBUILD_PARTIAL_TPS", "PENDING",
                             payload=_rebuild_payload())
    _insert_cmd_with_status(ops_db, 402, 1, "REBUILD_PARTIAL_TPS", "PENDING",
                             payload=_rebuild_payload())

    repo = GatewayCommandRepository(ops_db)
    gw = ExecutionGateway(
        config=ExecutionConfigLoader("config/execution.yaml").load(),
        adapter_registry={"bybit_demo": FakeAdapter()},
        repo=repo,
    )

    # Processa cmd 402 (il più recente)
    conn_r = sqlite3.connect(ops_db)
    cmd_row = conn_r.execute(
        "SELECT command_id, trade_chain_id, command_type, status, payload_json, "
        "idempotency_key, adapter, execution_account_id, client_order_id, retry_count "
        "FROM ops_execution_commands WHERE command_id=402"
    ).fetchone()
    conn_r.close()

    from src.runtime_v2.execution_gateway.models import ExecutionCommand
    cmd = ExecutionCommand(
        command_id=cmd_row[0], trade_chain_id=cmd_row[1], command_type=cmd_row[2],
        status=cmd_row[3], payload_json=cmd_row[4], idempotency_key=cmd_row[5],
        adapter=cmd_row[6], execution_account_id=cmd_row[7],
        client_order_id=cmd_row[8], retry_count=cmd_row[9] or 0,
    )
    gw.process(cmd, account_id="acc_1")

    conn = sqlite3.connect(ops_db)
    rows = {r[0]: r[1] for r in conn.execute(
        "SELECT command_id, status FROM ops_execution_commands WHERE command_id IN (401, 402)"
    ).fetchall()}
    conn.close()

    assert rows[401] == "SUPERSEDED"
    assert rows[402] == "SENT"


def test_rebuild_partial_tps_does_not_supersede_set_position_tpsl_partial(ops_db):
    """supersede_rebuild_commands non deve toccare SET_POSITION_TPSL_PARTIAL."""
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_cmd_with_status(ops_db, 501, 1, "SET_POSITION_TPSL_PARTIAL", "PENDING",
                             payload={"symbol": "BTC/USDT", "side": "SHORT",
                                      "hedge_mode": False, "position_idx": 0,
                                      "tp_sequence": 1, "take_profit": 48000.0,
                                      "tp_size": 0.01, "tp_order_type": "Limit",
                                      "tp_limit_price": 48000.0,
                                      "tp_trigger_by": "MarkPrice", "preserve_sl": True})
    _insert_cmd_with_status(ops_db, 502, 1, "REBUILD_PARTIAL_TPS", "PENDING",
                             payload=_rebuild_payload())

    repo = GatewayCommandRepository(ops_db)
    gw = ExecutionGateway(
        config=ExecutionConfigLoader("config/execution.yaml").load(),
        adapter_registry={"bybit_demo": FakeAdapter()},
        repo=repo,
    )

    from src.runtime_v2.execution_gateway.models import ExecutionCommand
    conn_r = sqlite3.connect(ops_db)
    cmd_row = conn_r.execute(
        "SELECT command_id, trade_chain_id, command_type, status, payload_json, "
        "idempotency_key, adapter, execution_account_id, client_order_id, retry_count "
        "FROM ops_execution_commands WHERE command_id=502"
    ).fetchone()
    conn_r.close()
    cmd = ExecutionCommand(
        command_id=cmd_row[0], trade_chain_id=cmd_row[1], command_type=cmd_row[2],
        status=cmd_row[3], payload_json=cmd_row[4], idempotency_key=cmd_row[5],
        adapter=cmd_row[6], execution_account_id=cmd_row[7],
        client_order_id=cmd_row[8], retry_count=cmd_row[9] or 0,
    )
    gw.process(cmd, account_id="acc_1")

    conn = sqlite3.connect(ops_db)
    rows = {r[0]: r[1] for r in conn.execute(
        "SELECT command_id, status FROM ops_execution_commands WHERE command_id IN (501, 502)"
    ).fetchall()}
    conn.close()

    assert rows[501] == "PENDING"   # SET_POSITION_TPSL_PARTIAL → intoccato
    assert rows[502] == "SENT"
```

- [ ] **Step 2: Esegui i test — verifica che falliscano**

```
pytest tests/runtime_v2/execution_gateway/test_gateway.py::test_rebuild_partial_tps_supersedes_pending_before_send -v
```

Expected: FAIL — FakeAdapter non conosce `REBUILD_PARTIAL_TPS` oppure il supersede non avviene.

- [ ] **Step 3: Aggiorna `FakeAdapter` per supportare `REBUILD_PARTIAL_TPS`**

In `src/runtime_v2/execution_gateway/adapters/fake.py`, aggiungi `REBUILD_PARTIAL_TPS` alla lista dei command type supportati (cerca dove vengono elencati `SET_POSITION_TPSL_PARTIAL` o `PLACE_ENTRY`):

```python
# Cerca il metodo place_order o _SUPPORTED_TYPES e aggiungi:
"REBUILD_PARTIAL_TPS",
```

Se FakeAdapter ha un handler generico fire-and-forget, `REBUILD_PARTIAL_TPS` dovrebbe già funzionare. Verifica eseguendo il test prima di modificare.

- [ ] **Step 4: Aggiungi handler `REBUILD_PARTIAL_TPS` in `gateway.py`**

Nella funzione `process` di `ExecutionGateway`, aggiungi il blocco supersede **prima** del dispatch all'adapter (cerca il blocco `supersedes_previous` esistente):

```python
# Nuovo blocco REBUILD_PARTIAL_TPS supersede
is_rebuild = cmd.command_type == "REBUILD_PARTIAL_TPS" and cmd.command_id is not None
if is_rebuild:
    self._repo.supersede_rebuild_commands(
        cmd.trade_chain_id,
        exclude_command_id=cmd.command_id,
        statuses=("PENDING",),
    )
```

Dopo il successo (nel blocco post-`mark_done` o `_FIRE_AND_FORGET`), aggiungi:

```python
if is_rebuild:
    self._repo.supersede_rebuild_commands(
        cmd.trade_chain_id,
        exclude_command_id=cmd.command_id,
        statuses=("SENT", "ACK", "DONE"),
    )
```

- [ ] **Step 5: Rimuovi la vecchia logica `supersedes_previous` da `gateway.py`**

Cerca e rimuovi il blocco:

```python
# RIMUOVERE — logica supersede rotta per SET_POSITION_TPSL_PARTIAL
supersedes_previous = (
    cmd.command_type == "SET_POSITION_TPSL_PARTIAL"
    and payload.get("supersedes_previous")
    and cmd.command_id is not None
)
if supersedes_previous:
    self._repo.supersede_tp_partial_commands(
        cmd.trade_chain_id,
        exclude_command_id=cmd.command_id,
        statuses=("PENDING",),
    )
payload = {k: v for k, v in payload.items() if k != "supersedes_previous"}
```

E il relativo blocco post-success:

```python
# RIMUOVERE
if supersedes_previous:
    self._repo.supersede_tp_partial_commands(
        cmd.trade_chain_id,
        exclude_command_id=cmd.command_id,
        statuses=("SENT", "ACK", "DONE"),
    )
```

- [ ] **Step 6: Esegui tutti i test del gateway**

```
pytest tests/runtime_v2/execution_gateway/test_gateway.py -v
```

Expected: tutti PASS (inclusi i nuovi).

- [ ] **Step 7: Commit**

```
git add src/runtime_v2/execution_gateway/gateway.py src/runtime_v2/execution_gateway/adapters/fake.py tests/runtime_v2/execution_gateway/test_gateway.py
git commit -m "feat(gateway): handle REBUILD_PARTIAL_TPS with supersede, remove broken supersedes_previous logic"
```

---

## Task 6: `event_sync.py` — `_get_tp_reconciliation_entries` include `REBUILD_PARTIAL_TPS`

**Files:**
- Modify: `src/runtime_v2/execution_gateway/event_sync.py`
- Modify: `tests/runtime_v2/execution_gateway/test_event_sync.py`

- [ ] **Step 1: Scrivi i test**

Aggiungi alla fine di `tests/runtime_v2/execution_gateway/test_event_sync.py`:

```python
def _insert_chain(db_path, chain_id=42, symbol="REQUSDT", side="SHORT",
                  state="OPEN"):
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_trade_chains "
        "(trade_chain_id, source_enrichment_id, canonical_message_id, raw_message_id, "
        "trader_id, account_id, symbol, side, lifecycle_state, entry_mode, "
        "management_plan_json, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (chain_id, 1, 10, 100, "trader_a", "acc", symbol, side,
         state, "TWO_STEP", "{}", now, now),
    )
    conn.commit()
    conn.close()


def _insert_cmd_done(db_path, cmd_id, chain_id, cmd_type, payload):
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_execution_commands "
        "(command_id, trade_chain_id, command_type, status, payload_json, "
        "idempotency_key, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (cmd_id, chain_id, cmd_type, "DONE",
         json.dumps(payload), f"idem:{cmd_id}", now, now),
    )
    conn.commit()
    conn.close()


def test_tp_reconciliation_entries_includes_rebuild_partial_tps(ops_db):
    """_get_tp_reconciliation_entries deve espandere REBUILD_PARTIAL_TPS in entry per livello."""
    from unittest.mock import MagicMock
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_chain(ops_db, chain_id=42, symbol="REQUSDT", side="SHORT", state="OPEN")
    _insert_cmd_done(ops_db, 801, 42, "REBUILD_PARTIAL_TPS", {
        "symbol": "REQUSDT",
        "side": "SHORT",
        "tps": [
            {"sequence": 1, "price": 0.090, "qty": 3333.0,
             "order_type": "Limit", "limit_price": 0.090, "trigger_by": "MarkPrice"},
            {"sequence": 2, "price": 0.085, "qty": 3333.0,
             "order_type": "Limit", "limit_price": 0.085, "trigger_by": "MarkPrice"},
        ],
    })

    adapter = MagicMock()
    adapter.fetch_recent_reduce_trades = MagicMock(return_value=[])
    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(
        ops_db_path=ops_db,
        adapter=adapter,
        repo=repo,
        execution_account_id="acc",
    )
    entries = worker._get_tp_reconciliation_entries()

    assert len(entries) == 2
    levels = {e["tp_level"]: e for e in entries}
    assert levels[1]["tp_price"] == 0.090
    assert levels[1]["tp_size"] == 3333.0
    assert levels[2]["tp_price"] == 0.085
    assert levels[2]["tp_size"] == 3333.0
    assert all(e["chain_id"] == 42 for e in entries)


def test_tp_reconciliation_entries_backward_compat_set_position_tpsl_partial(ops_db):
    """Le chain precedenti con SET_POSITION_TPSL_PARTIAL devono continuare a funzionare."""
    from unittest.mock import MagicMock
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    _insert_chain(ops_db, chain_id=43, symbol="PHAUSDT", side="SHORT", state="OPEN")
    _insert_cmd_done(ops_db, 901, 43, "SET_POSITION_TPSL_PARTIAL", {
        "symbol": "PHAUSDT",
        "side": "SHORT",
        "tp_sequence": 1,
        "take_profit": 0.080,
        "tp_size": 5000.0,
        "tp_order_type": "Limit",
        "tp_limit_price": 0.080,
        "tp_trigger_by": "MarkPrice",
        "preserve_sl": True,
    })

    adapter = MagicMock()
    adapter.fetch_recent_reduce_trades = MagicMock(return_value=[])
    repo = GatewayCommandRepository(ops_db)
    worker = ExchangeEventSyncWorker(
        ops_db_path=ops_db,
        adapter=adapter,
        repo=repo,
        execution_account_id="acc",
    )
    entries = worker._get_tp_reconciliation_entries()

    assert len(entries) == 1
    assert entries[0]["tp_level"] == 1
    assert entries[0]["tp_price"] == 0.080
    assert entries[0]["tp_size"] == 5000.0
    assert entries[0]["chain_id"] == 43
```

- [ ] **Step 2: Esegui i test — verifica che falliscano**

```
pytest tests/runtime_v2/execution_gateway/test_event_sync.py::test_tp_reconciliation_entries_includes_rebuild_partial_tps -v
```

Expected: FAIL — `_get_tp_reconciliation_entries` non include `REBUILD_PARTIAL_TPS`, entries vuota.

- [ ] **Step 3: Aggiorna `_get_tp_reconciliation_entries` in `event_sync.py`**

Sostituisci il metodo con:

```python
def _get_tp_reconciliation_entries(self) -> list[dict]:
    """Return active TP commands for open chains with price, level, and symbol.

    Supports both REBUILD_PARTIAL_TPS (new) and SET_POSITION_TPSL_PARTIAL (legacy).
    REBUILD_PARTIAL_TPS is expanded: one entry per tp level in the tps list.
    """
    conn = sqlite3.connect(self._ops_db)
    try:
        rows = conn.execute(
            "SELECT c.command_id, c.trade_chain_id, c.command_type, c.payload_json, "
            "t.symbol, t.side "
            "FROM ops_execution_commands c "
            "JOIN ops_trade_chains t ON c.trade_chain_id = t.trade_chain_id "
            "WHERE c.command_type IN ('SET_POSITION_TPSL_PARTIAL','REBUILD_PARTIAL_TPS') "
            "AND c.status IN ('SENT', 'DONE') "
            "AND t.lifecycle_state IN ('OPEN', 'PARTIALLY_CLOSED')"
        ).fetchall()
        result: list[dict] = []
        for cmd_id, chain_id, cmd_type, payload_json, symbol, side in rows:
            try:
                payload = json.loads(payload_json)
            except Exception:
                continue
            if cmd_type == "REBUILD_PARTIAL_TPS":
                for tp in payload.get("tps", []):
                    try:
                        result.append({
                            "cmd_id":   cmd_id,
                            "chain_id": chain_id,
                            "tp_level": int(tp["sequence"]),
                            "tp_price": float(tp["price"]),
                            "tp_size":  float(tp["qty"]),
                            "symbol":   symbol,
                            "side":     side,
                        })
                    except (KeyError, TypeError, ValueError):
                        continue
            else:  # SET_POSITION_TPSL_PARTIAL — backward compat
                try:
                    result.append({
                        "cmd_id":   cmd_id,
                        "chain_id": chain_id,
                        "tp_level": int(payload.get("tp_sequence", 1)),
                        "tp_price": float(payload.get("take_profit", 0)),
                        "tp_size":  float(payload.get("tp_size", 0)),
                        "symbol":   symbol,
                        "side":     side,
                    })
                except (TypeError, ValueError):
                    continue
        return result
    finally:
        conn.close()
```

- [ ] **Step 4: Esegui tutti i test di event_sync**

```
pytest tests/runtime_v2/execution_gateway/test_event_sync.py -v
```

Expected: tutti PASS.

- [ ] **Step 5: Commit**

```
git add src/runtime_v2/execution_gateway/event_sync.py tests/runtime_v2/execution_gateway/test_event_sync.py
git commit -m "feat(event_sync): _get_tp_reconciliation_entries includes REBUILD_PARTIAL_TPS with backward compat"
```

---

## Task 7: Smoke test end-to-end + suite completa

- [ ] **Step 1: Esegui la suite lifecycle completa**

```
pytest tests/runtime_v2/lifecycle/ -v
```

Expected: tutti PASS. Verifica in particolare che `test_event_processor.py` e `test_workers.py` non abbiano rotto nulla.

- [ ] **Step 2: Esegui la suite execution_gateway completa**

```
pytest tests/runtime_v2/execution_gateway/ -v
```

Expected: tutti PASS.

- [ ] **Step 3: Esegui l'intera suite runtime_v2**

```
pytest tests/runtime_v2/ -v
```

Expected: tutti PASS, nessuna regressione.

- [ ] **Step 4: Verifica che il vecchio `supersedes_previous` sia stato rimosso**

```
grep -r "supersedes_previous" src/runtime_v2/execution_gateway/gateway.py
```

Expected: nessun output (la logica è stata rimossa).

- [ ] **Step 5: Commit finale**

```
git add -A
git commit -m "test(runtime_v2): full suite passes after REBUILD_PARTIAL_TPS implementation"
```

---

## Checklist self-review

- [x] **Spec coverage:** tutti e 6 i file modificati hanno task dedicato con test TDD
- [x] **Placeholder scan:** nessun TBD o "implementa poi"
- [x] **Type consistency:** `REBUILD_PARTIAL_TPS` usato uniformemente in tutti i task, `supersede_rebuild_commands` signature coerente tra Task 2 e Task 5
- [x] **Backward compat:** `SET_POSITION_TPSL_PARTIAL` rimane funzionante per chain esistenti (event_sync Task 6 ha test dedicato)
- [x] **FakeAdapter:** Task 5 include step per aggiornare FakeAdapter se necessario
