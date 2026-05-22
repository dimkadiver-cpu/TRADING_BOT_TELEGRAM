# D_MULTI_ENTRY_MULTI_TP — Attached Final TP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Change `D_MULTI_ENTRY_MULTI_TP` so every entry leg is placed with `PARTIAL_TP` (SL + final TP attached), and the post-fill rebuild emits only intermediate TPs, not the final TP.

**Architecture:** Two-point change. `entry_gate._build_d_multi_entry_multi_tp_commands` switches from `SL_ONLY` to `PARTIAL_TP` with the last TP price and leg qty attached. `event_processor._build_tp_partial_commands_after_fill` skips the last `tp_rebuild` level since it is now covered by the exchange-native attached TP. No schema changes, no new dependencies.

**Tech Stack:** Python 3.12, Pydantic v2, pytest — all in-repo; no new packages.

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/runtime_v2/lifecycle/entry_gate.py:701-730` | Modify | Switch `SL_ONLY` → `PARTIAL_TP`, attach last TP + leg qty per entry leg |
| `src/runtime_v2/lifecycle/event_processor.py:129-178` | Modify | Skip last TP level in post-fill rebuild |
| `tests/runtime_v2/lifecycle/test_entry_gate_cd.py:673-694` | Modify + Add | Update 2 existing tests, add 1 new contract test |
| `tests/runtime_v2/lifecycle/test_event_processor.py:574-626` | Modify | Update 3 existing tests to match new expected counts and sizes |

**Not changing:**
- `order_builder.py` — the `PARTIAL_TP` branch already produces the correct Bybit payload (`tpslMode=Partial`, `tpSize`, `tpOrderType`, etc.)
- `test_bybit_order_builder_cd.py:183` — `test_place_entry_attached_partial_tp` already covers `PARTIAL_TP`; no changes needed
- `tp_rebuild` levels in `risk_snapshot_json` — still inject all levels; the skip logic lives in `event_processor`

---

## Task 1 — Failing tests: update entry gate assertions

**Files:**
- Modify: `tests/runtime_v2/lifecycle/test_entry_gate_cd.py:673-694`

These tests currently assert `SL_ONLY`. After this task they will fail until Task 2 implements the production change.

- [ ] **Step 1.1: Replace the `SL_ONLY` assertion test**

In `test_entry_gate_cd.py`, replace lines 673-685 (the function `test_d_multi_entry_multi_tp_each_leg_has_sl_only_attached`) with:

```python
def test_d_multi_entry_multi_tp_each_leg_has_partial_tp_attached():
    """D_MULTI_ENTRY_MULTI_TP: ogni leg ha PLACE_ENTRY_WITH_ATTACHED_TPSL mode PARTIAL_TP con SL + ultimo TP."""
    gate = _make_gate(simple_attached_enabled=True)
    result = gate.process_signal(_make_enriched_signal(entry_count=2, tp_count=2), [], "NONE")
    cmds = result.execution_commands
    entry_cmds = [c for c in cmds if c.command_type == "PLACE_ENTRY_WITH_ATTACHED_TPSL"]
    assert len(entry_cmds) == 2
    for c in entry_cmds:
        p = json.loads(c.payload_json)
        tpsl = p["attached_tpsl"]
        assert tpsl["mode"] == "PARTIAL_TP"
        assert tpsl["stop_loss"] == 63000.0
        assert tpsl["take_profit"] == 70500.0    # TP2 price: 70000 + 1*500
        assert tpsl["tp_qty"] > 0
```

Note: `take_profits[1].price.value = 70000.0 + 1 * 500 = 70500.0` per `_make_enriched_signal`.

- [ ] **Step 1.2: Add a new contract test — no TP commands on creation (still holds)**

After the test above, add:

```python
def test_d_multi_entry_multi_tp_no_set_position_tpsl_at_creation():
    """D_MULTI_ENTRY_MULTI_TP: nessun SET_POSITION_TPSL_* al momento della creazione (TP e' attached)."""
    gate = _make_gate(simple_attached_enabled=True)
    result = gate.process_signal(_make_enriched_signal(entry_count=2, tp_count=2), [], "NONE")
    cmds = result.execution_commands
    assert not any(c.command_type in {"SET_POSITION_TPSL_FULL", "SET_POSITION_TPSL_PARTIAL"}
                   for c in cmds)
```

Note: `test_d_multi_entry_multi_tp_no_tp_commands_at_creation` (line 688) makes the same assertion — keep it or replace it with this one. If keeping both, rename the old one to avoid confusion. The safest approach: rename the existing test at line 688 to `test_d_multi_entry_multi_tp_no_set_position_tpsl_at_creation` (same assertion, better name).

- [ ] **Step 1.3: Run the updated tests to confirm they fail**

```
pytest tests/runtime_v2/lifecycle/test_entry_gate_cd.py::test_d_multi_entry_multi_tp_each_leg_has_partial_tp_attached -v
```

Expected: `FAILED` — `AssertionError: assert 'SL_ONLY' == 'PARTIAL_TP'`

---

## Task 2 — Implement: update `_build_d_multi_entry_multi_tp_commands`

**Files:**
- Modify: `src/runtime_v2/lifecycle/entry_gate.py:701-730`

- [ ] **Step 2.1: Rewrite `_build_d_multi_entry_multi_tp_commands`**

Replace the current method (lines 701-730) with:

```python
def _build_d_multi_entry_multi_tp_commands(
    self, signal, eid, size_usdt, fallback_entry_price,
    leverage, hedge_mode, position_idx, sl_price,
    tp_count, close_pcts, legs_snap,
) -> list[ExecutionCommand]:
    commands: list[ExecutionCommand] = []
    last_tp = signal.take_profits[-1] if signal.take_profits else None
    last_tp_price = last_tp.price.value if last_tp and last_tp.price else None

    for leg in signal.entries:
        leg_snap = _find_leg_snap(legs_snap, leg.sequence)
        is_deferred = leg_snap is not None and leg_snap.get("qty_mode") == "deferred_market"

        if not is_deferred:
            if leg_snap and leg_snap.get("qty") is not None:
                leg_qty = float(leg_snap["qty"])
            else:
                leg_price = leg.price.value if leg.price else fallback_entry_price
                leg_notional = size_usdt * float(leg.weight or 0.0)
                leg_qty = self._qty_from_notional(leg_notional, leg_price)
        else:
            leg_qty = None

        commands.append(self._place_entry_attached_cmd(
            signal=signal, leg=leg, eid=eid, label="D_MULTI_ENTRY_MULTI_TP",
            leverage=leverage, hedge_mode=hedge_mode, position_idx=position_idx,
            sl_price=sl_price, leg_snap=leg_snap,
            qty=leg_qty,
            tpsl_mode="PARTIAL_TP",
            tp_price=last_tp_price,
            tp_qty=leg_qty,
            tp_qty_ratio=float(leg.weight) if is_deferred and leg.weight else None,
        ))

    return commands
```

Key changes vs old code:
- `tpsl_mode="PARTIAL_TP"` replaces `tpsl_mode="SL_ONLY"`
- `tp_price=last_tp_price` — attach the last TP as exchange-native
- `tp_qty=leg_qty` — TP covers exactly this leg's qty
- `tp_qty_ratio=float(leg.weight) if is_deferred` — deferred market ratio for qty resolution at submit

- [ ] **Step 2.2: Run the failing tests from Task 1 to confirm they now pass**

```
pytest tests/runtime_v2/lifecycle/test_entry_gate_cd.py::test_d_multi_entry_multi_tp_each_leg_has_partial_tp_attached -v
```

Expected: `PASSED`

- [ ] **Step 2.3: Run the full entry gate CD test suite to check for regressions**

```
pytest tests/runtime_v2/lifecycle/test_entry_gate_cd.py -v
```

Expected: all tests PASSED (the renamed no-tp-commands test should also pass since we still don't emit `SET_POSITION_TPSL_*` at creation).

- [ ] **Step 2.4: Commit**

```bash
git add src/runtime_v2/lifecycle/entry_gate.py tests/runtime_v2/lifecycle/test_entry_gate_cd.py
git commit -m "feat(entry_gate): D_MULTI_ENTRY_MULTI_TP uses PARTIAL_TP with final TP attached per leg"
```

---

## Task 3 — Failing tests: update event processor assertions

**Files:**
- Modify: `tests/runtime_v2/lifecycle/test_event_processor.py:574-626`

The three existing tests expect 2 `SET_POSITION_TPSL_PARTIAL` commands (all TP levels). After this task they will drive the new contract: only 1 command (intermediate TPs only, last TP excluded).

- [ ] **Step 3.1: Update `test_d_multi_entry_multi_tp_first_fill_emits_tp_partial_commands`**

Replace lines 574-587 with:

```python
def test_d_multi_entry_multi_tp_first_fill_emits_tp_partial_commands():
    """Primo fill: emette SET_POSITION_TPSL_PARTIAL solo per i TP intermedi (non l'ultimo)."""
    proc = _make_processor()
    chain = _make_chain_multi_tp(state="WAITING_ENTRY")
    event = _make_exchange_event(
        event_type="ENTRY_FILLED",
        payload={"fill_price": 0.50, "filled_qty": 0.7},
    )
    result = proc.process(event, chain, [])
    tp_cmds = [c for c in result.execution_commands if c.command_type == "SET_POSITION_TPSL_PARTIAL"]
    # 2 levels in tp_rebuild, last is attached at entry → only 1 intermediate emitted
    assert len(tp_cmds) == 1
    p = _json.loads(tp_cmds[0].payload_json)
    assert abs(float(p["tp_size"]) - 0.35) < 1e-6   # 0.7 * 50%
    assert p["take_profit"] == 0.52                  # TP1 price (intermediate)
```

- [ ] **Step 3.2: Update `test_d_multi_entry_multi_tp_second_fill_emits_supersedes_previous`**

Replace lines 590-610 with:

```python
def test_d_multi_entry_multi_tp_second_fill_emits_supersedes_previous():
    """Secondo fill: il TP intermedio aggiornato ha supersedes_previous=True e qty ricalcolata."""
    proc = _make_processor()
    chain = _make_chain_multi_tp(
        state="OPEN",
        filled_entry_qty=0.7,
        open_position_qty=0.7,
    )
    event = _make_exchange_event(
        event_type="ENTRY_FILLED",
        payload={"fill_price": 0.48, "filled_qty": 0.3},
    )
    result = proc.process(event, chain, [])
    tp_cmds = [c for c in result.execution_commands if c.command_type == "SET_POSITION_TPSL_PARTIAL"]
    # Only 1 intermediate TP, last is attached
    assert len(tp_cmds) == 1
    p = _json.loads(tp_cmds[0].payload_json)
    assert p.get("supersedes_previous") is True
    assert abs(float(p["tp_size"]) - 0.5) < 1e-6   # 1.0 * 50%
```

- [ ] **Step 3.3: Update `test_d_multi_entry_multi_tp_tp_prices_match_tp_rebuild_levels`**

Replace lines 613-625 with:

```python
def test_d_multi_entry_multi_tp_tp_prices_match_tp_rebuild_levels():
    """Solo il prezzo del TP intermedio (TP1=0.52) viene emesso; TP2 (0.55) e' attached e non viene rebuild."""
    proc = _make_processor()
    chain = _make_chain_multi_tp(state="WAITING_ENTRY")
    event = _make_exchange_event(
        event_type="ENTRY_FILLED",
        payload={"fill_price": 0.50, "filled_qty": 1.0},
    )
    result = proc.process(event, chain, [])
    tp_cmds = [c for c in result.execution_commands if c.command_type == "SET_POSITION_TPSL_PARTIAL"]
    prices = {_json.loads(c.payload_json)["take_profit"] for c in tp_cmds}
    assert prices == {0.52}      # TP1 only; TP2 (0.55) stays attached
```

- [ ] **Step 3.4: Run updated tests to confirm they fail**

```
pytest tests/runtime_v2/lifecycle/test_event_processor.py::test_d_multi_entry_multi_tp_first_fill_emits_tp_partial_commands tests/runtime_v2/lifecycle/test_event_processor.py::test_d_multi_entry_multi_tp_second_fill_emits_supersedes_previous tests/runtime_v2/lifecycle/test_event_processor.py::test_d_multi_entry_multi_tp_tp_prices_match_tp_rebuild_levels -v
```

Expected: all `FAILED` — `AssertionError: assert 2 == 1`

---

## Task 4 — Implement: update `_build_tp_partial_commands_after_fill`

**Files:**
- Modify: `src/runtime_v2/lifecycle/event_processor.py:129-178`

- [ ] **Step 4.1: Rewrite `_build_tp_partial_commands_after_fill`**

Replace the current method (lines 129-178) with:

```python
def _build_tp_partial_commands_after_fill(
    self, chain: TradeChain, new_filled: float, exchange_event_id: int
) -> list[ExecutionCommand]:
    try:
        risk_snap = json.loads(chain.risk_snapshot_json or "{}")
        levels = risk_snap.get("tp_rebuild", {}).get("levels", [])
    except Exception:
        return []
    if not levels:
        return []

    # Last TP is attached at entry order level for D_MULTI_ENTRY_MULTI_TP.
    # Only emit intermediate levels (all except last) as position-level partial TPs.
    intermediate_levels = levels[:-1]
    if not intermediate_levels:
        return []

    chain_id = chain.trade_chain_id
    commands: list[ExecutionCommand] = []

    for level in intermediate_levels:
        tp_price = level.get("price")
        close_pct = float(level.get("close_pct", 100.0 / len(levels)))
        sequence = int(level.get("sequence", 1))
        tp_qty = round(new_filled * close_pct / 100.0, 8)

        payload: dict = {
            "symbol": chain.symbol,
            "side": chain.side,
            "tp_sequence": sequence,
            "take_profit": tp_price,
            "tp_size": tp_qty,
            "tp_order_type": "Limit",
            "tp_limit_price": tp_price,
            "tp_trigger_by": "MarkPrice",
            "preserve_sl": True,
            "supersedes_previous": True,
        }
        commands.append(ExecutionCommand(
            trade_chain_id=chain_id,
            command_type="SET_POSITION_TPSL_PARTIAL",
            payload_json=json.dumps(payload),
            idempotency_key=(
                f"tp_partial_fill:{chain_id}:{exchange_event_id}:tp{sequence}"
            ),
        ))

    return commands
```

Key changes vs old code:
- `intermediate_levels = levels[:-1]` — skip the last TP (attached at entry)
- Return early if no intermediate levels
- No `allocated_qty` residual logic — each intermediate level uses `new_filled * close_pct / 100.0` directly

- [ ] **Step 4.2: Run failing tests from Task 3 to confirm they now pass**

```
pytest tests/runtime_v2/lifecycle/test_event_processor.py::test_d_multi_entry_multi_tp_first_fill_emits_tp_partial_commands tests/runtime_v2/lifecycle/test_event_processor.py::test_d_multi_entry_multi_tp_second_fill_emits_supersedes_previous tests/runtime_v2/lifecycle/test_event_processor.py::test_d_multi_entry_multi_tp_tp_prices_match_tp_rebuild_levels -v
```

Expected: all `PASSED`

- [ ] **Step 4.3: Run full event processor test suite to check for regressions**

```
pytest tests/runtime_v2/lifecycle/test_event_processor.py -v
```

Expected: all tests PASSED. Specifically verify:
- `test_non_multi_entry_multi_tp_entry_fill_emits_no_tp_commands` still passes (unchanged behavior for other modes)

- [ ] **Step 4.4: Commit**

```bash
git add src/runtime_v2/lifecycle/event_processor.py tests/runtime_v2/lifecycle/test_event_processor.py
git commit -m "feat(event_processor): D_MULTI_ENTRY_MULTI_TP rebuild skips last TP (now attached at entry)"
```

---

## Task 5 — Verify: confirm no regressions across the runtime_v2 test suite

**Files:** none — test run only

- [ ] **Step 5.1: Run the complete lifecycle test suite**

```
pytest tests/runtime_v2/lifecycle/ -v
```

Expected: all tests PASSED.

- [ ] **Step 5.2: Run the full execution gateway test suite**

```
pytest tests/runtime_v2/execution_gateway/ -v
```

Expected: all tests PASSED. Specifically:
- `test_place_entry_attached_partial_tp` in `test_bybit_order_builder_cd.py` already covers the PARTIAL_TP Bybit payload — no changes needed, should still pass.

- [ ] **Step 5.3: Run the full test suite**

```
pytest tests/runtime_v2/ -v
```

Expected: all tests PASSED, no regressions.

- [ ] **Step 5.4: Final commit (if any fixes were needed during step 5)**

If any fixes were applied, commit them. Otherwise skip.

---

## Self-Review Checklist

### Spec coverage

| PRD requirement | Covered by |
|-----------------|-----------|
| Every leg placed with `mode = PARTIAL_TP` | Task 2 — `entry_gate._build_d_multi_entry_multi_tp_commands` |
| `take_profit = TP_last`, `tp_qty = qty_leg` attached | Task 2 — `_place_entry_attached_cmd` with `tp_price`, `tp_qty` |
| Deferred market leg: `tp_qty_ratio` passed | Task 2 — `tp_qty_ratio=float(leg.weight)` for deferred |
| Post-fill rebuild emits only intermediate TPs | Task 4 — `levels[:-1]` in `_build_tp_partial_commands_after_fill` |
| Last TP NOT recreated as position-level partial | Task 4 — early return if `not intermediate_levels` |
| `supersedes_previous=True` on second fill | Not changed — already present in old code, preserved in new code |
| Bybit payload has `tpslMode=Partial`, `tpSize`, `tpOrderType` | Unchanged — `order_builder.py` PARTIAL_TP branch already correct |
| `C_SIMPLE_ATTACHED`, `C_MULTI_TP`, `D_MULTI_ENTRY_1TP` unaffected | Confirmed — none of those builders are touched |

### Edge cases
- **1 TP total in signal**: signal would route to `D_MULTI_ENTRY_1TP` (not `D_MULTI_ENTRY_MULTI_TP`), so this method is never called with `tp_count=1`. Safe.
- **`tp_rebuild` has 1 level**: `intermediate_levels = levels[:-1]` = empty list → returns `[]`. Defensive correct behavior even if data is unexpected.
- **Deferred market leg with `weight=None`**: `tp_qty_ratio=None` is passed to `_place_entry_attached_cmd`. The `attached_tpsl` dict for deferred will have `tp_qty_ratio=None` if weight is missing — acceptable graceful degradation.

### Type consistency
- `_place_entry_attached_cmd` parameter `tp_qty: float | None` — we pass `leg_qty` which is `float | None`. ✓
- `_place_entry_attached_cmd` parameter `tp_qty_ratio: float | None` — we pass `float(leg.weight)` or `None`. ✓
- `intermediate_levels = levels[:-1]` — same type as `levels` (list). ✓
