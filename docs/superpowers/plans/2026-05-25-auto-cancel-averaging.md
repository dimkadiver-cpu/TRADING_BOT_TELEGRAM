# Auto-Cancel Averaging + Race Hardening + Deferred BE Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implementare `cancel_averaging_pending_after`, `cancel_pending_by_engine`, il BE deferred cancel-first, e il race guard fill/cancel nel lifecycle del runtime_v2.

**Architecture:** Tutta la logica vive in `event_processor.py` (puro, no DB). Il flag `_be_deferred_by_auto_cancel` è scritto in `plan_state_json`. I cancel per averaging leg sono emessi come `ExecutionCommand` con `entry_client_order_id` già incluso nel payload (no expand step). Il race guard usa `active_commands` già disponibile nel dispatcher.

**Tech Stack:** Python 3.12+, Pydantic v2, SQLite (solo via `entry_gate.py`), pytest

---

## Mappa file

| File | Operazione | Responsabilità aggiunta |
|---|---|---|
| `src/runtime_v2/lifecycle/models.py` | Modifica | Aggiunge `AUTO_CANCEL_AVERAGING_REQUESTED`, `NOOP_CANCEL_CONFIRMED_POSITION_UNRESOLVED` a `LifecycleEventType` |
| `src/runtime_v2/lifecycle/execution_plan.py` | Modifica | Aggiunge `get_pending_averaging_legs(plan_state_json)` static helper |
| `src/runtime_v2/lifecycle/event_processor.py` | Modifica | Logica principale: cancel averaging in `_process_tp_filled`, deferred BE in `_process_pending_entry_cancelled_confirmed` e `_process_entry_filled`, race guard |
| `tests/runtime_v2/lifecycle/test_event_processor.py` | Modifica | 9 nuovi test case |
| `docs/debugging/stato_runtime_v2.md` | Modifica | Aggiorna tabella implementati/non-implementati |

---

## Task 1: Aggiungere i nuovi LifecycleEventType in `models.py`

**Files:**
- Modify: `src/runtime_v2/lifecycle/models.py:35-45`

- [ ] **Step 1: Aprire `models.py` e aggiungere i due nuovi tipi all'unione `LifecycleEventType`**

```python
LifecycleEventType = Literal[
    "SIGNAL_ACCEPTED", "TRADE_CHAIN_CREATED", "ENTRY_COMMAND_CREATED",
    "ENTRY_FILLED", "TP_FILLED", "SL_FILLED", "TIMEOUT_REACHED",
    "TELEGRAM_UPDATE_ACCEPTED", "BE_MOVE_REQUESTED",
    "NOOP_ALREADY_PROTECTED_BE", "NOOP_DUPLICATE_COMMAND",
    "NOOP_ALREADY_CLOSED", "NOOP_NOT_PENDING", "NOOP_NO_APPLICABLE_TARGET",
    "NOOP_CANCEL_CONFIRMED_POSITION_UNRESOLVED",
    "REVIEW_REQUIRED",
    "POSITION_SIZE_UPDATED", "ENTRY_AVG_PRICE_UPDATED",
    "PROTECTIVE_SYNC_REQUESTED", "STOP_MOVE_CONFIRMED", "PENDING_ENTRY_CANCELLED",
    "CLOSE_FULL_FILLED", "CLOSE_PARTIAL_FILLED",
    "AUTO_CANCEL_AVERAGING_REQUESTED",
]
```

- [ ] **Step 2: Scrivere un test minimo per verificare che i nuovi tipi siano accettati da Pydantic**

Aggiungere in `tests/runtime_v2/lifecycle/test_models.py`:

```python
def test_new_lifecycle_event_types_are_valid():
    from src.runtime_v2.lifecycle.models import LifecycleEvent
    import json

    e1 = LifecycleEvent(
        trade_chain_id=1,
        event_type="AUTO_CANCEL_AVERAGING_REQUESTED",
        source_type="engine",
        idempotency_key="test:1",
        payload_json=json.dumps({"tp_level": 1, "legs_cancelled": 2, "deferred_be": True}),
    )
    assert e1.event_type == "AUTO_CANCEL_AVERAGING_REQUESTED"

    e2 = LifecycleEvent(
        trade_chain_id=1,
        event_type="NOOP_CANCEL_CONFIRMED_POSITION_UNRESOLVED",
        source_type="engine",
        idempotency_key="test:2",
    )
    assert e2.event_type == "NOOP_CANCEL_CONFIRMED_POSITION_UNRESOLVED"
```

- [ ] **Step 3: Eseguire il test**

```
pytest tests/runtime_v2/lifecycle/test_models.py::test_new_lifecycle_event_types_are_valid -v
```

Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/runtime_v2/lifecycle/models.py tests/runtime_v2/lifecycle/test_models.py
git commit -m "feat(lifecycle): add AUTO_CANCEL_AVERAGING_REQUESTED and NOOP_CANCEL_CONFIRMED_POSITION_UNRESOLVED event types"
```

---

## Task 2: Helper `get_pending_averaging_legs` in `execution_plan.py`

**Files:**
- Modify: `src/runtime_v2/lifecycle/execution_plan.py:115-125`

- [ ] **Step 1: Scrivere il test fallente**

Aggiungere in `tests/runtime_v2/lifecycle/test_execution_plan.py`:

```python
def test_get_pending_averaging_legs_returns_sequence_gt_1():
    import json
    from src.runtime_v2.lifecycle.execution_plan import ExecutionPlanBuilder

    plan = {
        "legs": [
            {"leg_id": "leg_1", "sequence": 1, "status": "FILLED", "client_order_id": "cid_1"},
            {"leg_id": "leg_2", "sequence": 2, "status": "PENDING", "client_order_id": "cid_2"},
            {"leg_id": "leg_3", "sequence": 3, "status": "CANCELLED", "client_order_id": "cid_3"},
        ]
    }
    result = ExecutionPlanBuilder.get_pending_averaging_legs(json.dumps(plan))
    assert len(result) == 1
    assert result[0]["leg_id"] == "leg_2"
    assert result[0]["client_order_id"] == "cid_2"


def test_get_pending_averaging_legs_empty_when_all_filled():
    import json
    from src.runtime_v2.lifecycle.execution_plan import ExecutionPlanBuilder

    plan = {
        "legs": [
            {"leg_id": "leg_1", "sequence": 1, "status": "FILLED"},
            {"leg_id": "leg_2", "sequence": 2, "status": "FILLED"},
        ]
    }
    result = ExecutionPlanBuilder.get_pending_averaging_legs(json.dumps(plan))
    assert result == []


def test_get_pending_averaging_legs_returns_empty_on_bad_json():
    from src.runtime_v2.lifecycle.execution_plan import ExecutionPlanBuilder
    result = ExecutionPlanBuilder.get_pending_averaging_legs("not-json")
    assert result == []
```

- [ ] **Step 2: Eseguire per verificare fallimento**

```
pytest tests/runtime_v2/lifecycle/test_execution_plan.py::test_get_pending_averaging_legs_returns_sequence_gt_1 -v
```

Expected: FAIL con `AttributeError: type object 'ExecutionPlanBuilder' has no attribute 'get_pending_averaging_legs'`

- [ ] **Step 3: Aggiungere il metodo in `execution_plan.py` dopo `get_pending_legs`**

```python
@staticmethod
def get_pending_averaging_legs(plan_state_json: str) -> list[dict]:
    """Return legs with sequence > 1 whose status is PENDING (averaging legs not yet filled)."""
    try:
        plan = json.loads(plan_state_json or "{}")
        return [
            leg for leg in plan.get("legs", [])
            if leg.get("sequence", 1) > 1 and leg.get("status") == "PENDING"
        ]
    except Exception:
        return []
```

Aggiornare anche `__all__` se presente (non lo è, ma aggiornare il docstring del modulo se c'è).

- [ ] **Step 4: Eseguire tutti i test del helper**

```
pytest tests/runtime_v2/lifecycle/test_execution_plan.py -v -k "averaging"
```

Expected: tutti e 3 PASS

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/lifecycle/execution_plan.py tests/runtime_v2/lifecycle/test_execution_plan.py
git commit -m "feat(lifecycle): add get_pending_averaging_legs helper to ExecutionPlanBuilder"
```

---

## Task 3: Helper privati in `event_processor.py` per BE e flag deferred

**Files:**
- Modify: `src/runtime_v2/lifecycle/event_processor.py`

Questi helper vengono usati in Task 4, 5, 6. Non richiedono test diretti — sono coperti dai test di integrazione dei task successivi.

- [ ] **Step 1: Aggiungere `_build_be_move_command` come metodo privato di `LifecycleEventProcessor`**

Inserire dopo `_match_pending_legs_by_command_payload` (circa riga 265):

```python
def _build_be_move_command_and_event(
    self,
    chain: "TradeChain",
    eid: int,
    management_plan: "ManagementPlanConfig",
) -> "tuple[ExecutionCommand, LifecycleEvent] | None":
    """
    Calcola il prezzo BE e ritorna (command, event) se possibile.
    Ritorna None se BE non può essere calcolato (entry_avg_price assente)
    o se è già protetto.
    """
    if chain.be_protection_status in ("PROTECTED", "BE_MOVE_PENDING"):
        return None
    chain_id = chain.trade_chain_id
    extra = _be_move_extra(chain)
    new_stop_price = resolve_be_stop_price(chain, management_plan, protection_style=extra["protection_style"])
    if new_stop_price is None:
        logger.warning(
            "skipping deferred be move without entry_avg_price: chain_id=%s event_id=%s",
            chain_id, eid,
        )
        return None
    cmd_payload = {
        "symbol": chain.symbol, "side": chain.side,
        "new_stop_price": new_stop_price,
        "is_breakeven": True,
        **extra,
    }
    command = ExecutionCommand(
        trade_chain_id=chain_id,
        command_type="MOVE_STOP_TO_BREAKEVEN",
        payload_json=json.dumps(cmd_payload),
        idempotency_key=f"deferred_be:{chain_id}:{eid}",
    )
    event = LifecycleEvent(
        trade_chain_id=chain_id,
        event_type="BE_MOVE_REQUESTED",
        source_type="exchange_event",
        source_id=str(eid),
        idempotency_key=f"deferred_be_req:{chain_id}:{eid}",
    )
    return command, event
```

- [ ] **Step 2: Aggiungere `_set_be_deferred_flag` e `_clear_be_deferred_flag` come funzioni modulo-level**

Inserire dopo gli import, prima di `class LifecycleEventProcessor`:

```python
def _set_be_deferred_flag(plan_state_json: str, *, tp_level: int, averaging_legs_pending: int) -> str:
    """Aggiunge il flag _be_deferred_by_auto_cancel al plan_state_json."""
    try:
        plan = json.loads(plan_state_json or "{}")
    except Exception:
        plan = {}
    plan["_be_deferred_by_auto_cancel"] = {
        "tp_level": tp_level,
        "averaging_legs_pending": averaging_legs_pending,
    }
    return json.dumps(plan)


def _clear_be_deferred_flag(plan_state_json: str) -> str:
    """Rimuove il flag _be_deferred_by_auto_cancel dal plan_state_json."""
    try:
        plan = json.loads(plan_state_json or "{}")
    except Exception:
        return plan_state_json or "{}"
    plan.pop("_be_deferred_by_auto_cancel", None)
    return json.dumps(plan)


def _get_be_deferred_flag(plan_state_json: str) -> dict | None:
    """Ritorna il flag _be_deferred_by_auto_cancel se presente, altrimenti None."""
    try:
        plan = json.loads(plan_state_json or "{}")
        return plan.get("_be_deferred_by_auto_cancel") or None
    except Exception:
        return None
```

- [ ] **Step 3: Verificare che il file compila correttamente**

```
python -c "from src.runtime_v2.lifecycle.event_processor import LifecycleEventProcessor; print('ok')"
```

Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add src/runtime_v2/lifecycle/event_processor.py
git commit -m "feat(lifecycle): add _build_be_move_command_and_event helper and deferred BE flag utilities"
```

---

## Task 4: Implementare `cancel_averaging_pending_after` in `_process_tp_filled`

**Files:**
- Modify: `src/runtime_v2/lifecycle/event_processor.py:265-379`

- [ ] **Step 1: Scrivere i test fallenti**

Aggiungere in `tests/runtime_v2/lifecycle/test_event_processor.py`:

```python
# ──────────────────────────────────────────────────────────────────────────────
# Helpers estesi per cancel averaging
# ──────────────────────────────────────────────────────────────────────────────

def _make_chain_with_plan(
    *,
    trade_chain_id: int = 1,
    state: str = "OPEN",
    side: str = "LONG",
    entry_avg_price: float = 50000.0,
    be_trigger: str | None = None,
    cancel_averaging_pending_after: str | None = None,
    cancel_pending_by_engine: bool = True,
    be_status: str = "NOT_PROTECTED",
    plan_legs: list[dict] | None = None,
    open_position_qty: float = 1.0,
):
    from src.runtime_v2.lifecycle.models import TradeChain
    from src.runtime_v2.signal_enrichment.models import ManagementPlanConfig
    import json
    mp = ManagementPlanConfig(
        be_trigger=be_trigger,
        cancel_averaging_pending_after=cancel_averaging_pending_after,
        cancel_pending_by_engine=cancel_pending_by_engine,
    )
    legs = plan_legs or []
    plan_state = json.dumps({"plan_version": 1, "legs": legs})
    return TradeChain(
        trade_chain_id=trade_chain_id,
        source_enrichment_id=trade_chain_id,
        canonical_message_id=trade_chain_id * 10,
        raw_message_id=trade_chain_id * 100,
        trader_id="trader_a", account_id="acc_1",
        symbol="BTCUSDT", side=side,
        lifecycle_state=state,
        entry_mode="LADDER",
        management_plan_json=mp.model_dump_json(),
        entry_avg_price=entry_avg_price,
        open_position_qty=open_position_qty,
        be_protection_status=be_status,
        plan_state_json=plan_state,
    )


def _averaging_legs_fixture():
    """Ritorna una lista di leg con leg 1 FILLED e leg 2/3 PENDING."""
    return [
        {"leg_id": "leg_1", "sequence": 1, "status": "FILLED", "client_order_id": "cid_leg1"},
        {"leg_id": "leg_2", "sequence": 2, "status": "PENDING", "client_order_id": "cid_leg2"},
        {"leg_id": "leg_3", "sequence": 3, "status": "PENDING", "client_order_id": "cid_leg3"},
    ]


def test_cancel_averaging_after_tp1_emits_cancel_commands():
    """Quando TP1 scatta e cancel_averaging_pending_after=tp1, emette CANCEL_PENDING_ENTRY per leg 2 e 3."""
    proc = _make_processor()
    chain = _make_chain_with_plan(
        cancel_averaging_pending_after="tp1",
        plan_legs=_averaging_legs_fixture(),
    )
    event = _make_exchange_event(event_type="TP_FILLED", payload={"tp_level": 1, "is_final": False, "filled_qty": 0.5})

    result = proc.process(event, chain, [])

    cancel_cmds = [c for c in result.execution_commands if c.command_type == "CANCEL_PENDING_ENTRY"]
    assert len(cancel_cmds) == 2
    payloads = [json.loads(c.payload_json) for c in cancel_cmds]
    cids = {p["entry_client_order_id"] for p in payloads}
    assert cids == {"cid_leg2", "cid_leg3"}

    assert any(e.event_type == "AUTO_CANCEL_AVERAGING_REQUESTED" for e in result.lifecycle_events)


def test_cancel_averaging_by_engine_false_skips_auto_cancel():
    """Quando cancel_pending_by_engine=False, nessun cancel automatico viene emesso."""
    proc = _make_processor()
    chain = _make_chain_with_plan(
        cancel_averaging_pending_after="tp1",
        cancel_pending_by_engine=False,
        plan_legs=_averaging_legs_fixture(),
    )
    event = _make_exchange_event(event_type="TP_FILLED", payload={"tp_level": 1, "is_final": False, "filled_qty": 0.5})

    result = proc.process(event, chain, [])

    cancel_cmds = [c for c in result.execution_commands if c.command_type == "CANCEL_PENDING_ENTRY"]
    assert len(cancel_cmds) == 0
    assert not any(e.event_type == "AUTO_CANCEL_AVERAGING_REQUESTED" for e in result.lifecycle_events)


def test_cancel_averaging_no_pending_legs_be_emitted_immediately():
    """Quando non ci sono averaging leg pendenti, il BE viene emesso subito."""
    proc = _make_processor()
    all_filled_legs = [
        {"leg_id": "leg_1", "sequence": 1, "status": "FILLED", "client_order_id": "cid_leg1"},
        {"leg_id": "leg_2", "sequence": 2, "status": "FILLED", "client_order_id": "cid_leg2"},
    ]
    chain = _make_chain_with_plan(
        be_trigger="tp1",
        cancel_averaging_pending_after="tp1",
        plan_legs=all_filled_legs,
    )
    event = _make_exchange_event(event_type="TP_FILLED", payload={"tp_level": 1, "is_final": False, "filled_qty": 0.5})

    result = proc.process(event, chain, [])

    be_cmds = [c for c in result.execution_commands if c.command_type == "MOVE_STOP_TO_BREAKEVEN"]
    assert len(be_cmds) == 1  # BE emesso subito
    cancel_cmds = [c for c in result.execution_commands if c.command_type == "CANCEL_PENDING_ENTRY"]
    assert len(cancel_cmds) == 0
    assert not any(e.event_type == "AUTO_CANCEL_AVERAGING_REQUESTED" for e in result.lifecycle_events)


def test_cancel_averaging_with_be_trigger_defers_be():
    """Quando be_trigger e cancel_averaging coincidono su tp1, il BE viene differito (no MOVE_STOP_TO_BREAKEVEN)."""
    proc = _make_processor()
    chain = _make_chain_with_plan(
        be_trigger="tp1",
        cancel_averaging_pending_after="tp1",
        plan_legs=_averaging_legs_fixture(),
    )
    event = _make_exchange_event(event_type="TP_FILLED", payload={"tp_level": 1, "is_final": False, "filled_qty": 0.5})

    result = proc.process(event, chain, [])

    be_cmds = [c for c in result.execution_commands if c.command_type == "MOVE_STOP_TO_BREAKEVEN"]
    assert len(be_cmds) == 0  # BE non emesso ora

    cancel_cmds = [c for c in result.execution_commands if c.command_type == "CANCEL_PENDING_ENTRY"]
    assert len(cancel_cmds) == 2  # cancel emessi

    assert result.new_plan_state_json is not None
    plan = json.loads(result.new_plan_state_json)
    assert "_be_deferred_by_auto_cancel" in plan
    assert plan["_be_deferred_by_auto_cancel"]["tp_level"] == 1
    assert plan["_be_deferred_by_auto_cancel"]["averaging_legs_pending"] == 2
```

- [ ] **Step 2: Eseguire per verificare fallimento**

```
pytest tests/runtime_v2/lifecycle/test_event_processor.py::test_cancel_averaging_after_tp1_emits_cancel_commands -v
```

Expected: FAIL con `AssertionError` (0 cancel commands)

- [ ] **Step 3: Implementare la logica in `_process_tp_filled`**

Trovare il blocco `if not is_final:` in `_process_tp_filled` (circa riga 285). Aggiungere il blocco auto-cancel **prima** della logica BE esistente, e modificare la logica BE per saltarla se il cancel è in corso:

```python
    def _process_tp_filled(
        self,
        exchange_event: ExchangeEvent,
        chain: TradeChain,
        active_commands: list[ExecutionCommand],
    ) -> EventProcessorResult:
        payload = json.loads(exchange_event.payload_json)
        tp_level = int(payload.get("tp_level", 1))
        is_final = bool(payload.get("is_final", False))
        fill_qty = float(payload.get("filled_qty") or 0.0)
        eid = exchange_event.exchange_event_id
        chain_id = chain.trade_chain_id

        new_state: LifecycleState = "CLOSED" if is_final else "PARTIALLY_CLOSED"
        new_open = 0.0 if is_final else max(chain.open_position_qty - fill_qty, 0.0)
        new_closed = chain.closed_position_qty + fill_qty
        events: list[LifecycleEvent] = []
        commands: list[ExecutionCommand] = []
        new_be: BeProtectionStatus | None = None
        new_plan_state_json: str | None = None

        if not is_final:
            try:
                mp = ManagementPlanConfig.model_validate_json(chain.management_plan_json)
            except Exception:
                mp = ManagementPlanConfig()

            # ── Auto-cancel averaging legs ─────────────────────────────────────
            from src.runtime_v2.lifecycle.execution_plan import ExecutionPlanBuilder
            be_trigger = mp.be_trigger
            be_would_fire_now = be_trigger == f"tp{tp_level}"
            auto_cancel_active = False

            if mp.cancel_pending_by_engine and mp.cancel_averaging_pending_after == f"tp{tp_level}":
                averaging_legs = ExecutionPlanBuilder.get_pending_averaging_legs(chain.plan_state_json)
                if averaging_legs:
                    auto_cancel_active = True
                    # Pre-calcola se il BE sarà differito (evita mutazione lista eventi)
                    deferred_be = be_would_fire_now and chain.be_protection_status not in ("PROTECTED", "BE_MOVE_PENDING")
                    for leg in averaging_legs:
                        commands.append(ExecutionCommand(
                            trade_chain_id=chain_id,
                            command_type="CANCEL_PENDING_ENTRY",
                            payload_json=json.dumps({
                                "symbol": chain.symbol,
                                "side": chain.side,
                                "entry_client_order_id": leg["client_order_id"],
                            }),
                            idempotency_key=f"auto_cancel_avg:{chain_id}:{eid}:{leg['leg_id']}",
                        ))
                    events.append(LifecycleEvent(
                        trade_chain_id=chain_id,
                        event_type="AUTO_CANCEL_AVERAGING_REQUESTED",
                        source_type="engine",
                        source_id=str(eid),
                        payload_json=json.dumps({
                            "tp_level": tp_level,
                            "legs_cancelled": len(averaging_legs),
                            "deferred_be": deferred_be,
                        }),
                        idempotency_key=f"auto_cancel_avg_req:{chain_id}:{eid}",
                    ))
                    if deferred_be:
                        new_plan_state_json = _set_be_deferred_flag(
                            chain.plan_state_json,
                            tp_level=tp_level,
                            averaging_legs_pending=len(averaging_legs),
                        )

            # ── Breakeven trigger ──────────────────────────────────────────────
            if be_would_fire_now:
                if auto_cancel_active:
                    pass  # BE differito — verrà emesso da _process_pending_entry_cancelled_confirmed
                elif chain.be_protection_status == "PROTECTED":
                    events.append(LifecycleEvent(
                        trade_chain_id=chain_id,
                        event_type="NOOP_ALREADY_PROTECTED_BE",
                        source_type="exchange_event",
                        source_id=str(eid),
                        idempotency_key=f"noop_already_be_tp:{chain_id}:{eid}",
                    ))
                else:
                    active_be = [
                        c for c in active_commands
                        if c.command_type == "MOVE_STOP_TO_BREAKEVEN"
                        and c.status in ("PENDING", "SENT", "ACK")
                    ]
                    if active_be:
                        events.append(LifecycleEvent(
                            trade_chain_id=chain_id,
                            event_type="NOOP_DUPLICATE_COMMAND",
                            source_type="exchange_event",
                            source_id=str(eid),
                            idempotency_key=f"noop_dup_be_tp:{chain_id}:{eid}",
                        ))
                    else:
                        extra = _be_move_extra(chain)
                        new_stop_price = resolve_be_stop_price(chain, mp, protection_style=extra["protection_style"])
                        if new_stop_price is None:
                            logger.warning(
                                "skipping automatic be move without entry_avg_price: chain_id=%s event_id=%s",
                                chain_id, eid,
                            )
                        else:
                            cmd_payload = {
                                "symbol": chain.symbol, "side": chain.side,
                                "new_stop_price": new_stop_price,
                                "is_breakeven": True,
                                **extra,
                            }
                            commands.append(ExecutionCommand(
                                trade_chain_id=chain_id,
                                command_type="MOVE_STOP_TO_BREAKEVEN",
                                payload_json=json.dumps(cmd_payload),
                                idempotency_key=f"move_be_tp:{chain_id}:{eid}",
                            ))
                            events.append(LifecycleEvent(
                                trade_chain_id=chain_id,
                                event_type="BE_MOVE_REQUESTED",
                                source_type="exchange_event",
                                source_id=str(eid),
                                idempotency_key=f"be_req_tp:{chain_id}:{eid}",
                            ))
                            new_be = "BE_MOVE_PENDING"

            # Non-final TP: emit SYNC_PROTECTIVE_ORDERS
            commands.append(ExecutionCommand(
                trade_chain_id=chain_id,
                command_type="SYNC_PROTECTIVE_ORDERS",
                payload_json=json.dumps({"symbol": chain.symbol, "side": chain.side}),
                idempotency_key=f"sync_after_tp:{chain_id}:{eid}",
            ))

        tp_event = LifecycleEvent(
            trade_chain_id=chain_id,
            event_type="TP_FILLED",
            source_type="exchange_event",
            source_id=str(eid),
            previous_state=chain.lifecycle_state,
            next_state=new_state,
            payload_json=json.dumps({"tp_level": tp_level, "is_final": is_final}),
            idempotency_key=f"tp_filled:{chain_id}:{eid}",
        )
        events.insert(0, tp_event)

        return EventProcessorResult(
            new_lifecycle_state=new_state,
            new_be_protection_status=new_be,
            entry_avg_price=None,
            current_stop_price=None,
            lifecycle_events=events,
            execution_commands=commands,
            new_open_position_qty=new_open,
            new_closed_position_qty=new_closed,
            new_plan_state_json=new_plan_state_json,
        )
```

- [ ] **Step 4: Eseguire i nuovi test**

```
pytest tests/runtime_v2/lifecycle/test_event_processor.py -v -k "cancel_averaging"
```

Expected: tutti PASS

- [ ] **Step 5: Eseguire la suite completa del processor per regressione**

```
pytest tests/runtime_v2/lifecycle/test_event_processor.py -v
```

Expected: tutti PASS (nessuna regressione sui test esistenti)

- [ ] **Step 6: Commit**

```bash
git add src/runtime_v2/lifecycle/event_processor.py tests/runtime_v2/lifecycle/test_event_processor.py
git commit -m "feat(lifecycle): implement cancel_averaging_pending_after + cancel_pending_by_engine in _process_tp_filled"
```

---

## Task 5: Deferred BE + race guard in `_process_pending_entry_cancelled_confirmed`

**Files:**
- Modify: `src/runtime_v2/lifecycle/event_processor.py:499-546`
- Modify: dispatch in `process()` per passare `active_commands`

- [ ] **Step 1: Scrivere i test fallenti**

Aggiungere in `tests/runtime_v2/lifecycle/test_event_processor.py`:

```python
def test_deferred_be_emitted_after_last_cancel_confirmed():
    """Deferred BE viene emesso quando l'ultima averaging leg viene confermata cancelled."""
    import json
    from src.runtime_v2.lifecycle.models import ExecutionCommand

    proc = _make_processor()

    # Chain con flag deferred attivo, leg 2 ancora pending (leg 3 è l'ultima da confermare)
    plan_with_deferred = {
        "plan_version": 1,
        "legs": [
            {"leg_id": "leg_1", "sequence": 1, "status": "FILLED", "client_order_id": "cid_leg1"},
            {"leg_id": "leg_2", "sequence": 2, "status": "CANCELLED", "client_order_id": "cid_leg2"},
            {"leg_id": "leg_3", "sequence": 3, "status": "PENDING", "client_order_id": "cid_leg3"},
        ],
        "_be_deferred_by_auto_cancel": {"tp_level": 1, "averaging_legs_pending": 1},
    }
    chain = _make_chain_with_plan(
        be_trigger="tp1",
        cancel_averaging_pending_after="tp1",
        plan_legs=[],  # verrà sostituito da plan_state_json direttamente
        entry_avg_price=50000.0,
        open_position_qty=1.0,
    )
    # Override plan_state_json manualmente
    import dataclasses
    chain = chain.model_copy(update={"plan_state_json": json.dumps(plan_with_deferred)})

    event = _make_exchange_event(
        event_type="PENDING_ENTRY_CANCELLED_CONFIRMED",
        payload={"cancelled_order_ids": ["cid_leg3"]},
    )

    result = proc.process(event, chain, [])

    be_cmds = [c for c in result.execution_commands if c.command_type == "MOVE_STOP_TO_BREAKEVEN"]
    assert len(be_cmds) == 1
    be_payload = json.loads(be_cmds[0].payload_json)
    assert be_payload["new_stop_price"] == 50000.0  # entry_avg_price (no fee correction in default config)
    assert be_payload["is_breakeven"] is True

    assert result.new_plan_state_json is not None
    final_plan = json.loads(result.new_plan_state_json)
    assert "_be_deferred_by_auto_cancel" not in final_plan  # flag rimosso


def test_deferred_be_not_emitted_until_all_legs_confirmed():
    """Deferred BE NON viene emesso se ci sono ancora averaging leg pending."""
    import json

    proc = _make_processor()

    plan_with_deferred = {
        "plan_version": 1,
        "legs": [
            {"leg_id": "leg_1", "sequence": 1, "status": "FILLED", "client_order_id": "cid_leg1"},
            {"leg_id": "leg_2", "sequence": 2, "status": "PENDING", "client_order_id": "cid_leg2"},  # ancora pending
            {"leg_id": "leg_3", "sequence": 3, "status": "PENDING", "client_order_id": "cid_leg3"},
        ],
        "_be_deferred_by_auto_cancel": {"tp_level": 1, "averaging_legs_pending": 2},
    }
    chain = _make_chain_with_plan(plan_legs=[], entry_avg_price=50000.0)
    chain = chain.model_copy(update={"plan_state_json": json.dumps(plan_with_deferred)})

    event = _make_exchange_event(
        event_type="PENDING_ENTRY_CANCELLED_CONFIRMED",
        payload={"cancelled_order_ids": ["cid_leg3"]},
    )

    result = proc.process(event, chain, [])

    be_cmds = [c for c in result.execution_commands if c.command_type == "MOVE_STOP_TO_BREAKEVEN"]
    assert len(be_cmds) == 0  # leg 2 ancora pending → no BE


def test_race_guard_cancel_confirmed_before_entry_filled():
    """PENDING_ENTRY_CANCELLED_CONFIRMED arriva prima di ENTRY_FILLED: chain NON va a CANCELLED."""
    import json
    from src.runtime_v2.lifecycle.models import ExecutionCommand

    proc = _make_processor()
    chain = _make_chain_with_plan(
        state="WAITING_ENTRY",
        open_position_qty=0.0,
        plan_legs=[
            {"leg_id": "leg_1", "sequence": 1, "status": "PENDING", "client_order_id": "cid_leg1"},
            {"leg_id": "leg_2", "sequence": 2, "status": "PENDING", "client_order_id": "cid_leg2"},
        ],
    )

    # Leg 1 è stata inviata all'exchange (SENT) ma il fill non è ancora arrivato
    active_cmds = [
        ExecutionCommand(
            trade_chain_id=1,
            command_type="PLACE_ENTRY_WITH_ATTACHED_TPSL",
            status="SENT",
            payload_json="{}",
            idempotency_key="place_entry:1:leg1",
        ),
    ]

    # Leg 2 viene confermata cancelled (ma leg 1 potrebbe ancora fillarsi)
    event = _make_exchange_event(
        event_type="PENDING_ENTRY_CANCELLED_CONFIRMED",
        payload={"cancelled_order_ids": ["cid_leg2"]},
    )

    result = proc.process(event, chain, active_cmds)

    assert result.new_lifecycle_state is None  # NON va a CANCELLED
    assert any(e.event_type == "NOOP_CANCEL_CONFIRMED_POSITION_UNRESOLVED" for e in result.lifecycle_events)


def test_race_guard_allows_cancelled_when_no_entries_in_flight():
    """PENDING_ENTRY_CANCELLED_CONFIRMED con nessun PLACE_ENTRY SENT/ACK → CANCELLED corretto."""
    proc = _make_processor()
    chain = _make_chain_with_plan(
        state="WAITING_ENTRY",
        open_position_qty=0.0,
        plan_legs=[
            {"leg_id": "leg_1", "sequence": 1, "status": "PENDING", "client_order_id": "cid_leg1"},
        ],
    )

    # Nessun comando PLACE_ENTRY in SENT/ACK
    event = _make_exchange_event(
        event_type="PENDING_ENTRY_CANCELLED_CONFIRMED",
        payload={"cancelled_order_ids": ["cid_leg1"]},
    )

    result = proc.process(event, chain, [])

    assert result.new_lifecycle_state == "CANCELLED"
```

- [ ] **Step 2: Eseguire per verificare fallimento**

```
pytest tests/runtime_v2/lifecycle/test_event_processor.py::test_deferred_be_emitted_after_last_cancel_confirmed -v
```

Expected: FAIL

- [ ] **Step 3: Aggiornare il dispatcher `process()` per passare `active_commands` a `_process_pending_entry_cancelled_confirmed`**

Nel metodo `process()` (circa riga 77):

```python
        if etype == "PENDING_ENTRY_CANCELLED_CONFIRMED":
            return self._process_pending_entry_cancelled_confirmed(exchange_event, chain, active_commands)
```

- [ ] **Step 4: Riscrivere `_process_pending_entry_cancelled_confirmed`**

```python
    def _process_pending_entry_cancelled_confirmed(
        self,
        exchange_event: ExchangeEvent,
        chain: TradeChain,
        active_commands: list[ExecutionCommand],
    ) -> EventProcessorResult:
        payload = json.loads(exchange_event.payload_json)
        position_already_open = (chain.open_position_qty or 0.0) > 0.0
        cancelled_order_ids = [str(v) for v in payload.get("cancelled_order_ids", []) if v]
        eid = exchange_event.exchange_event_id
        chain_id = chain.trade_chain_id
        commands: list[ExecutionCommand] = []
        events: list[LifecycleEvent] = []
        new_state: str | None = None

        # ── Marca leg come CANCELLED nel piano ────────────────────────────────
        new_plan_state_json = self._mark_entry_leg_status(
            chain.plan_state_json,
            client_order_ids=cancelled_order_ids,
            command_payload=None,
            new_status="CANCELLED",
        )
        effective_plan_json = new_plan_state_json or chain.plan_state_json or "{}"

        # ── Deferred BE: emetti BE se tutte le averaging leg sono confermate ──
        deferred = _get_be_deferred_flag(effective_plan_json)
        if deferred:
            try:
                mp = ManagementPlanConfig.model_validate_json(chain.management_plan_json)
            except Exception:
                mp = ManagementPlanConfig()

            from src.runtime_v2.lifecycle.execution_plan import ExecutionPlanBuilder
            remaining_averaging = ExecutionPlanBuilder.get_pending_averaging_legs(effective_plan_json)
            if not remaining_averaging:
                # Ultima leg confermata: emetti BE con avg price corrente
                be_result = self._build_be_move_command_and_event(chain, eid or 0, mp)
                if be_result is not None:
                    be_cmd, be_event = be_result
                    commands.append(be_cmd)
                    events.append(be_event)
                # Rimuovi il flag dal piano
                effective_plan_json = _clear_be_deferred_flag(effective_plan_json)
                new_plan_state_json = effective_plan_json

        # ── Stato finale chain ─────────────────────────────────────────────────
        if position_already_open:
            if chain.execution_mode not in _ATTACHED_PROTECTION_MODES:
                commands.append(ExecutionCommand(
                    trade_chain_id=chain_id,
                    command_type="SYNC_PROTECTIVE_ORDERS",
                    payload_json=json.dumps({"symbol": chain.symbol, "side": chain.side}),
                    idempotency_key=f"sync_after_cancel:{chain_id}:{eid}",
                ))
        else:
            # Race guard: non finalizzare se ci sono entry commands ancora in volo
            entry_in_flight = [
                c for c in active_commands
                if c.command_type in ("PLACE_ENTRY", "PLACE_ENTRY_WITH_ATTACHED_TPSL")
                and c.status in ("SENT", "ACK")
            ]
            if len(entry_in_flight) > len(cancelled_order_ids):
                # Altre entry ancora in attesa di fill o conferma — non finalizzare
                events.append(LifecycleEvent(
                    trade_chain_id=chain_id,
                    event_type="NOOP_CANCEL_CONFIRMED_POSITION_UNRESOLVED",
                    source_type="exchange_event",
                    source_id=str(eid),
                    idempotency_key=f"noop_cancel_unresolved:{chain_id}:{eid}",
                ))
            else:
                new_state = "CANCELLED"

        events.insert(0, LifecycleEvent(
            trade_chain_id=chain_id,
            event_type="PENDING_ENTRY_CANCELLED",
            source_type="exchange_event",
            source_id=str(eid),
            previous_state=chain.lifecycle_state,
            next_state=new_state,
            payload_json=exchange_event.payload_json,
            idempotency_key=f"pending_cancelled:{chain_id}:{eid}",
        ))

        return EventProcessorResult(
            new_lifecycle_state=new_state,
            new_be_protection_status=None,
            entry_avg_price=None,
            current_stop_price=None,
            lifecycle_events=events,
            execution_commands=commands,
            new_plan_state_json=new_plan_state_json,
        )
```

- [ ] **Step 5: Eseguire i nuovi test**

```
pytest tests/runtime_v2/lifecycle/test_event_processor.py -v -k "deferred_be or race_guard"
```

Expected: tutti PASS

- [ ] **Step 6: Suite completa**

```
pytest tests/runtime_v2/lifecycle/test_event_processor.py -v
```

Expected: tutti PASS

- [ ] **Step 7: Commit**

```bash
git add src/runtime_v2/lifecycle/event_processor.py tests/runtime_v2/lifecycle/test_event_processor.py
git commit -m "feat(lifecycle): deferred BE after cancel confirmed + race guard CANCELLED finalization"
```

---

## Task 6: Gestire deferred BE in `_process_entry_filled` (race fill)

**Files:**
- Modify: `src/runtime_v2/lifecycle/event_processor.py:89-182`

Questo task gestisce il caso in cui una averaging leg si **filla** prima che il cancel arrivi all'exchange (race). In questo caso l'evento `ENTRY_FILLED` deve decrementare il counter e, se è l'ultimo, emettere il BE.

- [ ] **Step 1: Scrivere il test fallente**

```python
def test_deferred_be_emitted_on_race_entry_fill():
    """Se una averaging leg si filla prima del cancel (race), il BE viene emesso dall'ENTRY_FILLED handler."""
    import json

    proc = _make_processor()

    plan_with_deferred = {
        "plan_version": 1,
        "legs": [
            {"leg_id": "leg_1", "sequence": 1, "status": "FILLED", "client_order_id": "cid_leg1"},
            {"leg_id": "leg_2", "sequence": 2, "status": "PENDING", "client_order_id": "cid_leg2"},  # si fillerà per race
        ],
        "_be_deferred_by_auto_cancel": {"tp_level": 1, "averaging_legs_pending": 1},
    }
    chain = _make_chain_with_plan(
        state="OPEN",
        plan_legs=[],
        entry_avg_price=50000.0,
        open_position_qty=1.0,
        be_trigger="tp1",
        cancel_averaging_pending_after="tp1",
    )
    chain = chain.model_copy(update={"plan_state_json": json.dumps(plan_with_deferred)})

    # Leg 2 si filla invece di cancellarsi (race)
    event = _make_exchange_event(
        event_type="ENTRY_FILLED",
        payload={
            "fill_price": 49500.0,
            "filled_qty": 0.5,
            "entry_client_order_id": "cid_leg2",
        },
    )

    result = proc.process(event, chain, [])

    be_cmds = [c for c in result.execution_commands if c.command_type == "MOVE_STOP_TO_BREAKEVEN"]
    assert len(be_cmds) == 1
    assert result.new_plan_state_json is not None
    final_plan = json.loads(result.new_plan_state_json)
    assert "_be_deferred_by_auto_cancel" not in final_plan
```

- [ ] **Step 2: Eseguire per verificare fallimento**

```
pytest tests/runtime_v2/lifecycle/test_event_processor.py::test_deferred_be_emitted_on_race_entry_fill -v
```

Expected: FAIL (0 be_cmds)

- [ ] **Step 3: Aggiungere la logica deferred BE in `_process_entry_filled`**

Trovare la fine di `_process_entry_filled` (circa riga 160-182, prima del `return`). Inserire il blocco dopo il calcolo di `new_plan_state_json`:

```python
        # ── Deferred BE: controlla se questa fill completa le averaging leg ───
        effective_plan = new_plan_state_json or chain.plan_state_json or "{}"
        deferred = _get_be_deferred_flag(effective_plan)
        if deferred:
            try:
                mp_fill = ManagementPlanConfig.model_validate_json(chain.management_plan_json)
            except Exception:
                mp_fill = ManagementPlanConfig()
            from src.runtime_v2.lifecycle.execution_plan import ExecutionPlanBuilder
            remaining_averaging = ExecutionPlanBuilder.get_pending_averaging_legs(effective_plan)
            if not remaining_averaging:
                # Crea una chain temporanea con entry_avg_price aggiornato per calcolare BE corretto
                chain_for_be = chain.model_copy(update={"entry_avg_price": new_avg})
                be_result = self._build_be_move_command_and_event(chain_for_be, eid or 0, mp_fill)
                if be_result is not None:
                    be_cmd, be_event = be_result
                    commands.append(be_cmd)
                    events.append(be_event)
                effective_plan = _clear_be_deferred_flag(effective_plan)
                new_plan_state_json = effective_plan
```

Assicurarsi che il `return` finale usi `new_plan_state_json` aggiornato (già lo fa).

- [ ] **Step 4: Eseguire il test**

```
pytest tests/runtime_v2/lifecycle/test_event_processor.py::test_deferred_be_emitted_on_race_entry_fill -v
```

Expected: PASS

- [ ] **Step 5: Suite completa**

```
pytest tests/runtime_v2/lifecycle/test_event_processor.py -v
```

Expected: tutti PASS

- [ ] **Step 6: Commit**

```bash
git add src/runtime_v2/lifecycle/event_processor.py tests/runtime_v2/lifecycle/test_event_processor.py
git commit -m "feat(lifecycle): handle deferred BE in _process_entry_filled for race fill scenario"
```

---

## Task 7: Test casi limite (edge cases)

**Files:**
- Modify: `tests/runtime_v2/lifecycle/test_event_processor.py`

- [ ] **Step 1: Aggiungere i test per i casi limite**

```python
def test_deferred_be_skipped_if_already_protected():
    """Deferred BE non viene emesso se be_protection_status è già PROTECTED."""
    import json

    proc = _make_processor()
    plan_with_deferred = {
        "plan_version": 1,
        "legs": [
            {"leg_id": "leg_1", "sequence": 1, "status": "FILLED", "client_order_id": "cid_leg1"},
            {"leg_id": "leg_2", "sequence": 2, "status": "PENDING", "client_order_id": "cid_leg2"},
        ],
        "_be_deferred_by_auto_cancel": {"tp_level": 1, "averaging_legs_pending": 1},
    }
    chain = _make_chain_with_plan(
        plan_legs=[],
        open_position_qty=1.0,
        be_status="PROTECTED",  # già protetto manualmente
    )
    chain = chain.model_copy(update={"plan_state_json": json.dumps(plan_with_deferred)})

    event = _make_exchange_event(
        event_type="PENDING_ENTRY_CANCELLED_CONFIRMED",
        payload={"cancelled_order_ids": ["cid_leg2"]},
    )

    result = proc.process(event, chain, [])

    be_cmds = [c for c in result.execution_commands if c.command_type == "MOVE_STOP_TO_BREAKEVEN"]
    assert len(be_cmds) == 0  # già protetto, no BE


def test_cancel_averaging_different_tp_levels_independent():
    """cancel_averaging_pending_after=tp2 e be_trigger=tp1 sono indipendenti: TP1 emette solo BE."""
    proc = _make_processor()
    chain = _make_chain_with_plan(
        be_trigger="tp1",
        cancel_averaging_pending_after="tp2",  # cancel solo su TP2
        plan_legs=_averaging_legs_fixture(),
    )
    event = _make_exchange_event(
        event_type="TP_FILLED",
        payload={"tp_level": 1, "is_final": False, "filled_qty": 0.5},
    )

    result = proc.process(event, chain, [])

    cancel_cmds = [c for c in result.execution_commands if c.command_type == "CANCEL_PENDING_ENTRY"]
    assert len(cancel_cmds) == 0  # TP1 non triggera cancel

    be_cmds = [c for c in result.execution_commands if c.command_type == "MOVE_STOP_TO_BREAKEVEN"]
    assert len(be_cmds) == 1  # BE emesso normalmente


def test_expand_cancel_does_not_include_done_commands_via_plan_state():
    """get_pending_averaging_legs non include leg CANCELLED o FILLED dal plan_state_json."""
    import json
    from src.runtime_v2.lifecycle.execution_plan import ExecutionPlanBuilder

    plan = json.dumps({
        "legs": [
            {"leg_id": "leg_1", "sequence": 1, "status": "FILLED"},
            {"leg_id": "leg_2", "sequence": 2, "status": "FILLED"},   # già fillata
            {"leg_id": "leg_3", "sequence": 3, "status": "CANCELLED"}, # già cancellata
        ]
    })
    result = ExecutionPlanBuilder.get_pending_averaging_legs(plan)
    assert result == []  # nessuna leg averaging pending
```

- [ ] **Step 2: Eseguire i nuovi test**

```
pytest tests/runtime_v2/lifecycle/test_event_processor.py -v -k "skipped_if_already or different_tp or expand_cancel"
```

Expected: tutti PASS

- [ ] **Step 3: Eseguire la suite lifecycle completa per regressione finale**

```
pytest tests/runtime_v2/lifecycle/ -v
```

Expected: tutti PASS

- [ ] **Step 4: Commit**

```bash
git add tests/runtime_v2/lifecycle/test_event_processor.py tests/runtime_v2/lifecycle/test_execution_plan.py
git commit -m "test(lifecycle): edge cases auto-cancel averaging, deferred BE, race guard"
```

---

## Task 8: Aggiornare `stato_runtime_v2.md`

**Files:**
- Modify: `docs/debugging/stato_runtime_v2.md:75-103`

- [ ] **Step 1: Aggiornare la tabella "Implementati"**

Sostituire la sezione `### Implementati` con:

```markdown
### Implementati

| Campo | Trigger | Effetto |
|---|---|---|
| `be_trigger` (tp1/tp2/tp3) | TP N colpito | Emette `MOVE_STOP_TO_BREAKEVEN` automatico |
| `be_buffer_pct` | Con `be_trigger` | Aggiunge buffer % al prezzo BE |
| `close_distribution` | Ogni TP | Calcola % di chiusura per TP successivo |
| `cancel_pending_on_timeout` + `pending_timeout_hours` | Worker periodico | Cancella entry pending scaduta → chain `EXPIRED` |
| `cancel_averaging_pending_after` (tp1/tp2) | TP N colpito | Cancella leg averaging (sequence > 1) ancora PENDING → BE deferred se be_trigger coincide |
| `cancel_pending_by_engine` | Gate globale | Se `false`, disabilita tutti i cancel automatici da engine (cancel manuale Telegram sempre attivo) |
```

- [ ] **Step 2: Aggiornare la tabella "Definiti ma NON ancora implementati"**

```markdown
### Definiti ma NON ancora implementati

| Campo | Semantica | Note |
|---|---|---|
| `cancel_unfilled_pending_after` | Cancella entry non fillata se prezzo ha raggiunto livello TP | **Bloccato**: richiede price-watcher non presente nell'architettura. Lasciato nel modello come placeholder. |
| `risk_freed_by_be` | Libera rischio allocato quando BE scatta | Solo nel modello |
| `protective_sl_mode` | `exchange_native_first` vs `bot_managed` | Solo nel modello |
```

- [ ] **Step 3: Aggiornare la checklist "Da fare / verificare"**

Rimuovere i checkbox già completati:
```markdown
## Da fare / verificare

### Funzionalità mancanti nel lifecycle

- [x] `cancel_averaging_pending_after` — implementato in `event_processor._process_tp_filled`
- [x] `cancel_pending_by_engine` — gate implementato in `_process_tp_filled`
- [ ] `cancel_unfilled_pending_after` — BLOCCATO: richiede price-watcher
- [ ] `risk_freed_by_be` — aggiornare `risk_remaining` della chain quando BE scatta
- [ ] `SET_STOP` su prezzo esplicito (non solo ENTRY) — ora va in REVIEW
```

- [ ] **Step 4: Commit**

```bash
git add docs/debugging/stato_runtime_v2.md
git commit -m "docs(lifecycle): aggiorna stato implementazione cancel_averaging e cancel_pending_by_engine"
```

---

## Checklist finale

- [ ] `pytest tests/runtime_v2/lifecycle/ -v` → tutti PASS
- [ ] `pytest tests/runtime_v2/ -v` → tutti PASS (no regressioni)
- [ ] `python -c "from src.runtime_v2.lifecycle.event_processor import LifecycleEventProcessor; print('ok')"` → ok
- [ ] Nessun `_be_deferred_by_auto_cancel` che sopravvive a chain terminale (verificato dai test `test_be_deferred_cleared_on_sl_hit` — da aggiungere come estensione se si vuole copertura aggiuntiva)
