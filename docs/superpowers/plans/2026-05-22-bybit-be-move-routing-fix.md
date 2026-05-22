# Bybit BE Move Routing Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix `MOVE_STOP_TO_BREAKEVEN` on Bybit so that chains opened with attached/full TPSL use `trading_stop` (position-level) instead of `edit_sl` (standalone order), eliminating the `retCode=10001` failures seen in production demo.

**Architecture:** Add a `protection_style` field to the `MOVE_STOP_TO_BREAKEVEN` payload at emission time (derived from `chain.execution_mode`). The order builder reads this field to route either to `trading_stop_move_sl` (for attached/full flows: all C/D modes) or to `edit_sl` (for legacy flows: `a_sequential`, `b_entry_stop_then_tp`, `c_native_attached_tpsl`). No changes to price calculation, BE triggers, or other command types.

**Tech Stack:** Python 3.12, Pydantic v2, pytest, ccxt/Bybit API

---

## File Map

| File | Change |
|------|--------|
| `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/order_builder.py` | `_move_stop`: route to `trading_stop_move_sl` when `protection_style == "attached_full"` |
| `src/runtime_v2/lifecycle/entry_gate.py` | Two sites that emit `MOVE_STOP_TO_BREAKEVEN` payload: add `protection_style` + `position_idx` |
| `src/runtime_v2/lifecycle/event_processor.py` | One site that emits `MOVE_STOP_TO_BREAKEVEN` payload: add `protection_style` + `position_idx` |
| `tests/runtime_v2/execution_gateway/test_bybit_order_builder.py` | Add 2 new routing tests; existing test updated to reflect standalone path |
| `tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py` | Add 2 new adapter routing tests |

---

## Background Knowledge

### execution_mode values and their protection style

```
ATTACHED/FULL (needs trading_stop_move_sl):
  C_SIMPLE_ATTACHED    → PLACE_ENTRY_WITH_ATTACHED_TPSL
  C_MULTI_TP           → PLACE_ENTRY_WITH_ATTACHED_TPSL  ← confirmed failing in prod
  D_MULTI_ENTRY_1TP    → PLACE_ENTRY_WITH_ATTACHED_TPSL per leg
  D_MULTI_ENTRY_MULTI_TP → PLACE_ENTRY_WITH_ATTACHED_TPSL per leg
  D_POSITION_TPSL      → SET_POSITION_TPSL_FULL (position-level)

STANDALONE ORDER (edit_sl still correct):
  a_sequential
  b_entry_stop_then_tp
  c_native_attached_tpsl
```

### Current MOVE_STOP_TO_BREAKEVEN payload (both emission sites)

```python
{
    "symbol": chain.symbol,
    "side": chain.side,
    "target_price": chain.entry_avg_price,
    "be_buffer_pct": mp.be_buffer_pct,
}
```

### After fix, payload will also include

```python
{
    "protection_style": "attached_full",   # or "standalone_order"
    "position_idx": 0,                      # derived from hedge_mode in risk_snapshot
}
```

### Deriving position_idx from chain

```python
import json as _json

def _position_idx_from_chain(chain) -> int:
    try:
        rs = _json.loads(chain.risk_snapshot_json or "{}")
        hedge_mode = bool(rs.get("hedge_mode", False))
    except Exception:
        hedge_mode = False
    if not hedge_mode:
        return 0
    return 1 if chain.side == "LONG" else 2
```

### Builder routing rule

```python
_ATTACHED_PROTECTION_MODES = frozenset({
    "C_SIMPLE_ATTACHED", "C_MULTI_TP",
    "D_MULTI_ENTRY_1TP", "D_MULTI_ENTRY_MULTI_TP", "D_POSITION_TPSL",
})

def _protection_style_for_mode(execution_mode: str) -> str:
    return "attached_full" if execution_mode in _ATTACHED_PROTECTION_MODES else "standalone_order"
```

In the builder, when `protection_style` is absent → default to `"standalone_order"` (backward-compatible).

---

## Task 1: Write failing builder routing tests

**Files:**
- Modify: `tests/runtime_v2/execution_gateway/test_bybit_order_builder.py`

- [ ] **Step 1: Open the file and locate the move_stop section**

  File: `tests/runtime_v2/execution_gateway/test_bybit_order_builder.py`

  Look for the existing test `test_move_stop_to_breakeven_uses_target_price_and_buffer` (around line 339).

- [ ] **Step 2: Add two new tests after the existing move_stop tests**

  Add at the end of the file (after `test_move_stop_uses_new_stop_price`):

  ```python
  def test_move_stop_be_attached_flow_routes_to_trading_stop_move_sl() -> None:
      """C/D flows with attached TPSL must use trading_stop, not edit_order."""
      params = _builder().build(
          "MOVE_STOP_TO_BREAKEVEN",
          {
              "symbol": "BTC/USDT:USDT",
              "side": "LONG",
              "target_price": 50000.0,
              "be_buffer_pct": 0.0,
              "protection_style": "attached_full",
              "position_idx": 0,
          },
          "tsb:10:5:sl:1",
      )

      assert params.action == "trading_stop_move_sl"
      assert params.symbol == "BTC/USDT:USDT"
      assert params.position_side == "LONG"
      assert params.extra_params["stopLoss"] == "50000.0"
      assert params.extra_params["positionIdx"] == 0


  def test_move_stop_be_attached_flow_long_applies_buffer() -> None:
      """Buffer is still applied before routing to trading_stop_move_sl."""
      params = _builder().build(
          "MOVE_STOP_TO_BREAKEVEN",
          {
              "symbol": "ETH/USDT:USDT",
              "side": "LONG",
              "target_price": 3000.0,
              "be_buffer_pct": 0.002,
              "protection_style": "attached_full",
              "position_idx": 1,
          },
          "tsb:10:5:sl:1",
      )

      assert params.action == "trading_stop_move_sl"
      assert params.extra_params["stopLoss"] == "3006.0"
      assert params.extra_params["positionIdx"] == 1


  def test_move_stop_be_standalone_flow_still_uses_edit_sl() -> None:
      """Legacy flows without protection_style field keep edit_sl path."""
      params = _builder().build(
          "MOVE_STOP_TO_BREAKEVEN",
          {
              "symbol": "BTC/USDT:USDT",
              "side": "LONG",
              "target_price": 50000.0,
              "be_buffer_pct": 0.0,
              "protection_style": "standalone_order",
          },
          "tsb:10:5:sl:1",
      )

      assert params.action == "edit_sl"
      assert params.new_trigger_price == 50000.0


  def test_move_stop_be_no_protection_style_defaults_to_edit_sl() -> None:
      """Payload without protection_style defaults to standalone (backward-compatible)."""
      params = _builder().build(
          "MOVE_STOP_TO_BREAKEVEN",
          {
              "symbol": "BTC/USDT:USDT",
              "side": "LONG",
              "target_price": 50000.0,
          },
          "tsb:10:5:sl:1",
      )

      assert params.action == "edit_sl"
      assert params.new_trigger_price == 50000.0
  ```

- [ ] **Step 3: Run the new tests to verify they fail**

  ```
  pytest tests/runtime_v2/execution_gateway/test_bybit_order_builder.py -k "be_attached or be_standalone or no_protection_style" -v
  ```

  Expected: FAIL — `test_move_stop_be_attached_flow_routes_to_trading_stop_move_sl` and others fail because `action == "edit_sl"` but we expect `"trading_stop_move_sl"`.

---

## Task 2: Fix order_builder.py routing

**Files:**
- Modify: `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/order_builder.py`

- [ ] **Step 1: Add the `_ATTACHED_PROTECTION_MODES` constant after the existing constants at top of file**

  Add after the `_CLOSE_SIDE` line:

  ```python
  _ATTACHED_PROTECTION_MODES = frozenset({
      "C_SIMPLE_ATTACHED", "C_MULTI_TP",
      "D_MULTI_ENTRY_1TP", "D_MULTI_ENTRY_MULTI_TP", "D_POSITION_TPSL",
  })
  ```

- [ ] **Step 2: Replace the `_move_stop` method body**

  Current body (lines 157–173):

  ```python
  def _move_stop(self, command_type: str, payload: dict) -> BybitOrderParams:
      if command_type == "MOVE_STOP_TO_BREAKEVEN":
          target_price = float(payload["target_price"])
          buffer_pct = float(payload.get("be_buffer_pct") or 0.0)
          if payload["side"] == "LONG":
              new_trigger_price = target_price * (1 + buffer_pct)
          else:
              new_trigger_price = target_price * (1 - buffer_pct)
      else:
          new_trigger_price = float(payload["new_stop_price"])

      return BybitOrderParams(
          action="edit_sl",
          symbol=payload["symbol"],
          new_trigger_price=new_trigger_price,
          position_side=payload["side"],
      )
  ```

  Replace with:

  ```python
  def _move_stop(self, command_type: str, payload: dict) -> BybitOrderParams:
      if command_type == "MOVE_STOP_TO_BREAKEVEN":
          target_price = float(payload["target_price"])
          buffer_pct = float(payload.get("be_buffer_pct") or 0.0)
          if payload["side"] == "LONG":
              new_trigger_price = target_price * (1 + buffer_pct)
          else:
              new_trigger_price = target_price * (1 - buffer_pct)
      else:
          new_trigger_price = float(payload["new_stop_price"])

      protection_style = payload.get("protection_style", "standalone_order")
      if protection_style == "attached_full":
          return BybitOrderParams(
              action="trading_stop_move_sl",
              symbol=payload["symbol"],
              position_side=payload["side"],
              extra_params={
                  "positionIdx": int(payload.get("position_idx", 0)),
                  "stopLoss": str(new_trigger_price),
              },
          )

      return BybitOrderParams(
          action="edit_sl",
          symbol=payload["symbol"],
          new_trigger_price=new_trigger_price,
          position_side=payload["side"],
      )
  ```

- [ ] **Step 3: Run all builder tests to verify**

  ```
  pytest tests/runtime_v2/execution_gateway/test_bybit_order_builder.py tests/runtime_v2/execution_gateway/test_bybit_order_builder_cd.py -v
  ```

  Expected: ALL PASS (including the 4 new tests and all pre-existing tests).

- [ ] **Step 4: Commit**

  ```bash
  git add src/runtime_v2/execution_gateway/adapters/ccxt_bybit/order_builder.py tests/runtime_v2/execution_gateway/test_bybit_order_builder.py
  git commit -m "fix(bybit): route MOVE_STOP_TO_BREAKEVEN to trading_stop when protection_style=attached_full"
  ```

---

## Task 3: Write failing adapter tests

**Files:**
- Modify: `tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py`

- [ ] **Step 1: Add new adapter tests after the existing move_stop section (after line ~335)**

  Add after the `test_move_stop_sl_not_found_returns_failed` test:

  ```python
  def test_move_stop_be_attached_calls_trading_stop_api():
      """Attached/full BE move must call private_post_v5_position_trading_stop, not edit_order."""
      exchange = MagicMock()
      exchange.private_post_v5_position_trading_stop = MagicMock(
          return_value={"retCode": 0}
      )
      adapter = _make_adapter(exchange)

      result = adapter.place_order(
          command_type="MOVE_STOP_TO_BREAKEVEN",
          payload={
              "symbol": "BTC/USDT:USDT",
              "side": "LONG",
              "target_price": 50000.0,
              "be_buffer_pct": 0.0,
              "protection_style": "attached_full",
              "position_idx": 0,
          },
          client_order_id="tsb:10:5:sl:1",
          execution_account_id="bybit_main",
          connector="bybit",
      )

      assert result.success is True
      exchange.edit_order.assert_not_called()
      exchange.fetch_open_orders.assert_not_called()
      exchange.private_post_v5_position_trading_stop.assert_called_once()
      call_body = exchange.private_post_v5_position_trading_stop.call_args[0][0]
      assert call_body["category"] == "linear"
      assert call_body["symbol"] == "BTC/USDT:USDT"
      assert call_body["stopLoss"] == "50000.0"
      assert call_body["positionIdx"] == 0


  def test_move_stop_be_attached_hedge_mode_long_position_idx_1():
      """Hedge mode LONG should use positionIdx=1 in trading_stop payload."""
      exchange = MagicMock()
      exchange.private_post_v5_position_trading_stop = MagicMock(
          return_value={"retCode": 0}
      )
      adapter = _make_adapter(exchange)

      result = adapter.place_order(
          command_type="MOVE_STOP_TO_BREAKEVEN",
          payload={
              "symbol": "XRP/USDT:USDT",
              "side": "LONG",
              "target_price": 0.5,
              "be_buffer_pct": 0.0,
              "protection_style": "attached_full",
              "position_idx": 1,
          },
          client_order_id="tsb:10:5:sl:1",
          execution_account_id="bybit_main",
          connector="bybit",
      )

      assert result.success is True
      call_body = exchange.private_post_v5_position_trading_stop.call_args[0][0]
      assert call_body["positionIdx"] == 1


  def test_move_stop_be_standalone_still_calls_edit_order():
      """Legacy standalone flow must still use edit_order path."""
      exchange = MagicMock()
      sl_order = {
          "id": "sl_ord_1", "side": "sell",
          "type": "stop", "amount": 0.01, "reduceOnly": True, "stopPrice": "49000",
      }
      exchange.fetch_open_orders.return_value = [sl_order]
      adapter = _make_adapter(exchange)

      result = adapter.place_order(
          command_type="MOVE_STOP_TO_BREAKEVEN",
          payload={
              "symbol": "BTC/USDT:USDT",
              "side": "LONG",
              "target_price": 50000.0,
              "protection_style": "standalone_order",
          },
          client_order_id="tsb:10:5:sl:1",
          execution_account_id="bybit_main",
          connector="bybit",
      )

      assert result.success is True
      exchange.edit_order.assert_called_once()
      exchange.private_post_v5_position_trading_stop.assert_not_called()
  ```

- [ ] **Step 2: Run the new adapter tests**

  ```
  pytest tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py -k "be_attached or be_standalone" -v
  ```

  Expected: All 3 PASS (the builder fix from Task 2 already makes these work).

- [ ] **Step 3: Run full adapter unit test suite to check for regressions**

  ```
  pytest tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py -v
  ```

  Expected: ALL PASS.

- [ ] **Step 4: Commit**

  ```bash
  git add tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py
  git commit -m "test(bybit): add adapter routing tests for attached vs standalone BE move"
  ```

---

## Task 4: Write failing lifecycle payload tests

**Files:**
- Modify: `tests/runtime_v2/execution_gateway/test_command_worker.py` OR a new file

  Check if there are tests for `entry_gate` BE payload building in:
  - `tests/runtime_v2/lifecycle/` (look for tests of `_apply_manual_be_trigger`)

  Then add new tests verifying the emitted payload contains `protection_style`.

- [ ] **Step 1: Locate the entry_gate BE trigger test file**

  ```
  grep -r "apply_manual_be\|MOVE_STOP_TO_BREAKEVEN\|move_be" tests/runtime_v2/lifecycle/ --include="*.py" -l
  ```

  Note which file covers `_apply_manual_be_trigger`. Most likely `tests/runtime_v2/lifecycle/test_entry_gate.py`.

- [ ] **Step 2: Add tests for `protection_style` in emitted payload**

  Find the section covering `_apply_manual_be_trigger` (or `update_chain` for BE move) and add:

  ```python
  def test_manual_be_trigger_attached_flow_includes_protection_style(
      entry_gate_factory,   # or however the fixture is named
  ):
      """entry_gate emits protection_style=attached_full for C_MULTI_TP chains."""
      # Build a minimal TradeChain with execution_mode="C_MULTI_TP"
      chain = TradeChain(
          trade_chain_id=1,
          source_enrichment_id=1,
          canonical_message_id=1,
          raw_message_id=1,
          trader_id="trader_a",
          account_id="main",
          symbol="BTCUSDT",
          side="LONG",
          lifecycle_state="BE_MOVE_PENDING",
          entry_mode="limit",
          entry_avg_price=50000.0,
          execution_mode="C_MULTI_TP",
          management_plan_json='{"be_buffer_pct": 0.0}',
          risk_snapshot_json='{"hedge_mode": false}',
      )

      # Invoke the BE trigger (see how the existing test calls it)
      # The result should contain a MOVE_STOP_TO_BREAKEVEN command
      # with protection_style="attached_full" in its payload
      import json
      cmd = result.execution_commands[0]
      assert cmd.command_type == "MOVE_STOP_TO_BREAKEVEN"
      payload = json.loads(cmd.payload_json)
      assert payload["protection_style"] == "attached_full"
      assert "position_idx" in payload


  def test_manual_be_trigger_legacy_flow_includes_standalone_style(
      entry_gate_factory,
  ):
      """entry_gate emits protection_style=standalone_order for a_sequential chains."""
      chain = TradeChain(
          ...
          execution_mode="a_sequential",
          ...
      )
      # same pattern
      payload = json.loads(cmd.payload_json)
      assert payload["protection_style"] == "standalone_order"
  ```

  > **Note:** Look at the existing entry_gate test fixtures to understand how to construct the TradeChain and call the BE trigger method. Adapt the test to match the existing fixture/factory pattern exactly.

- [ ] **Step 3: Run lifecycle tests to verify they fail**

  ```
  pytest tests/runtime_v2/lifecycle/test_entry_gate.py -k "be_trigger or manual_be" -v
  ```

  Expected: FAIL — `protection_style` not in payload yet.

---

## Task 5: Fix entry_gate.py payload emission

**Files:**
- Modify: `src/runtime_v2/lifecycle/entry_gate.py`

There are **two** `MOVE_STOP_TO_BREAKEVEN` payload sites in `entry_gate.py`:

### Site A — Manual trigger (`_apply_manual_be_trigger`, ~line 942)

- [ ] **Step 1: Add helper at module level (near the top of file, after imports)**

  Add after the existing module-level constants:

  ```python
  _ATTACHED_PROTECTION_MODES = frozenset({
      "C_SIMPLE_ATTACHED", "C_MULTI_TP",
      "D_MULTI_ENTRY_1TP", "D_MULTI_ENTRY_MULTI_TP", "D_POSITION_TPSL",
  })


  def _be_move_extra(chain: "TradeChain") -> dict:
      """Return protection_style and position_idx for a MOVE_STOP_TO_BREAKEVEN payload."""
      import json as _json
      try:
          rs = _json.loads(chain.risk_snapshot_json or "{}")
          hedge_mode = bool(rs.get("hedge_mode", False))
      except Exception:
          hedge_mode = False
      position_idx = LifecycleEntryGate.resolve_position_idx(chain.side, hedge_mode)
      protection_style = (
          "attached_full"
          if chain.execution_mode in _ATTACHED_PROTECTION_MODES
          else "standalone_order"
      )
      return {"protection_style": protection_style, "position_idx": position_idx}
  ```

  > Note: The `import json as _json` inside the function avoids a circular import risk. If `json` is already imported at the top of the module (it is), you can just use `json` directly and skip the local import.

- [ ] **Step 2: Update Site A — `_apply_manual_be_trigger` (~line 942)**

  Current code:

  ```python
  cmd = ExecutionCommand(
      trade_chain_id=chain_id,
      command_type="MOVE_STOP_TO_BREAKEVEN",
      payload_json=json.dumps({
          "symbol": chain.symbol, "side": chain.side,
          "target_price": chain.entry_avg_price,
          "be_buffer_pct": mp.be_buffer_pct,
      }),
      idempotency_key=f"move_be:{chain_id}:{cmid}",
  )
  ```

  Replace with:

  ```python
  cmd = ExecutionCommand(
      trade_chain_id=chain_id,
      command_type="MOVE_STOP_TO_BREAKEVEN",
      payload_json=json.dumps({
          "symbol": chain.symbol, "side": chain.side,
          "target_price": chain.entry_avg_price,
          "be_buffer_pct": mp.be_buffer_pct,
          **_be_move_extra(chain),
      }),
      idempotency_key=f"move_be:{chain_id}:{cmid}",
  )
  ```

### Site B — Automatic TP-trigger in event_processor-style entry_gate path (~line 231)

  Search `entry_gate.py` for the second `MOVE_STOP_TO_BREAKEVEN` emission. It's the one with `idempotency_key=f"move_be_tp:{chain_id}:{eid}"` or similar. Apply the same `**_be_move_extra(chain)` pattern.

- [ ] **Step 3: Run lifecycle tests**

  ```
  pytest tests/runtime_v2/lifecycle/test_entry_gate.py -v
  ```

  Expected: all tests PASS, including the new ones from Task 4.

- [ ] **Step 4: Commit**

  ```bash
  git add src/runtime_v2/lifecycle/entry_gate.py
  git commit -m "fix(entry_gate): include protection_style and position_idx in MOVE_STOP_TO_BREAKEVEN payload"
  ```

---

## Task 6: Fix event_processor.py payload emission

**Files:**
- Modify: `src/runtime_v2/lifecycle/event_processor.py`

The single BE move emission in `event_processor.py` is at ~line 228–238:

```python
cmd_payload = {
    "symbol": chain.symbol, "side": chain.side,
    "target_price": chain.entry_avg_price,
    "be_buffer_pct": mp.be_buffer_pct,
}
commands.append(ExecutionCommand(
    trade_chain_id=chain_id,
    command_type="MOVE_STOP_TO_BREAKEVEN",
    payload_json=json.dumps(cmd_payload),
    idempotency_key=f"move_be_tp:{chain_id}:{eid}",
))
```

- [ ] **Step 1: Add a module-level helper in event_processor.py**

  Add near the top of `event_processor.py` (after imports):

  ```python
  _ATTACHED_PROTECTION_MODES = frozenset({
      "C_SIMPLE_ATTACHED", "C_MULTI_TP",
      "D_MULTI_ENTRY_1TP", "D_MULTI_ENTRY_MULTI_TP", "D_POSITION_TPSL",
  })


  def _be_move_extra(chain: "TradeChain") -> dict:
      try:
          rs = json.loads(chain.risk_snapshot_json or "{}")
          hedge_mode = bool(rs.get("hedge_mode", False))
      except Exception:
          hedge_mode = False
      if not hedge_mode:
          position_idx = 0
      else:
          position_idx = 1 if chain.side == "LONG" else 2
      protection_style = (
          "attached_full"
          if chain.execution_mode in _ATTACHED_PROTECTION_MODES
          else "standalone_order"
      )
      return {"protection_style": protection_style, "position_idx": position_idx}
  ```

- [ ] **Step 2: Update the cmd_payload dict**

  ```python
  cmd_payload = {
      "symbol": chain.symbol, "side": chain.side,
      "target_price": chain.entry_avg_price,
      "be_buffer_pct": mp.be_buffer_pct,
      **_be_move_extra(chain),
  }
  ```

- [ ] **Step 3: Run event_processor tests**

  ```
  pytest tests/runtime_v2/lifecycle/ -v
  ```

  Expected: ALL PASS.

- [ ] **Step 4: Commit**

  ```bash
  git add src/runtime_v2/lifecycle/event_processor.py
  git commit -m "fix(event_processor): include protection_style and position_idx in MOVE_STOP_TO_BREAKEVEN payload"
  ```

---

## Task 7: Full regression run

- [ ] **Step 1: Run the complete test suite**

  ```
  pytest tests/runtime_v2/ -v
  ```

  Expected: ALL PASS. Zero regressions.

- [ ] **Step 2: Verify the four acceptance criteria from PRD**

  1. `MOVE_STOP_TO_BREAKEVEN` on `C_MULTI_TP` chains → builder produces `trading_stop_move_sl` (covered by Task 1 & 3 tests)
  2. `MOVE_STOP_TO_BREAKEVEN` on legacy chains → builder produces `edit_sl` (covered by backward-compat tests)
  3. Builder routing test for `protection_style="attached_full"` with buffer LONG → confirmed `stopLoss` value
  4. `position_idx` propagated correctly for both hedge_mode=False (idx=0) and LONG hedge (idx=1)

- [ ] **Step 3: Confirm the exact failing cases from PRD are now handled**

  The PRD reports failures on:
  - `BTCUSDT`, chain 2, `execution_mode=C_MULTI_TP`
  - `XRPUSDT`, chain 5, `execution_mode=C_MULTI_TP`

  Both are `C_MULTI_TP` → `"attached_full"` → builder now routes to `trading_stop_move_sl` → adapter calls `private_post_v5_position_trading_stop` → no more `retCode=10001`.

---

## Self-Review Against PRD

| PRD Requirement | Task | Status |
|---|---|---|
| `MOVE_STOP_TO_BREAKEVEN` on attached/full uses `trading_stop_move_sl` | Task 1+2+3 | ✅ |
| `MOVE_STOP_TO_BREAKEVEN` on standalone SL keeps `edit_sl` | Task 1 backward-compat tests | ✅ |
| No change to BE price calculation | N/A — only routing changed | ✅ |
| Tests distinguish `edit_sl` vs `trading_stop_move_sl` explicitly | Tasks 1, 3, 4 | ✅ |
| Contratto 1: attached flow must NOT use `edit_order` | Task 3 asserts `edit_order.assert_not_called()` | ✅ |
| Contratto 2: attached flow uses `trading_stop_move_sl` | Task 2 + Task 3 | ✅ |
| Contratto 3: legacy flow still uses `edit_sl` | Task 1 + Task 3 backward-compat | ✅ |
| `D_POSITION_TPSL` and `D_MULTI_*` also fixed | Included in `_ATTACHED_PROTECTION_MODES` | ✅ |
| `position_idx` propagated to exchange call | Task 2 builder routes `position_idx` → extra_params | ✅ |
