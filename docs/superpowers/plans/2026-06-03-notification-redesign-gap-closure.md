# Notification Redesign Gap Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Chiudere i gap residui del redesign notifiche completando `MODIFY_ENTRIES`, arricchendo `UPDATE_PARTIAL` e rendendo utile `UPDATE_REJECTED` senza toccare dispatcher, delay o multi-chain summary.

**Architecture:** Il lavoro resta confinato a due punti: generazione del payload update in `src/runtime_v2/lifecycle/entry_gate.py` e rendering testuale in `src/runtime_v2/control_plane/formatters/clean_log.py`. L'approccio è TDD-first: prima si scrivono i test mancanti sui gap reali, poi si implementa il minimo codice necessario per farli passare, infine si esegue la suite mirata del redesign notifiche.

**Tech Stack:** Python 3.12, pytest, SQLite di test, runtime_v2 lifecycle/control_plane

---

## File Structure

- `src/runtime_v2/lifecycle/entry_gate.py`
  - Responsabilità: generazione dei `LifecycleEvent` per gli update trader e sintesi dei payload `UPDATE_DONE|PARTIAL|REJECTED`.
  - Cambi previsti: aggiungere `changed_entries` a `_apply_modify_entries()`; estendere `_write_update_clean_log()` per trasportare `changed`, `rejected_actions` e `reason`.

- `src/runtime_v2/control_plane/formatters/clean_log.py`
  - Responsabilità: rendering Telegram dei payload CLEAN_LOG.
  - Cambi previsti: fare in modo che `_update_partial()` mostri sia i diff applicati sia i rifiuti; estendere `_update_rejected()` con `Rejected:`.

- `tests/runtime_v2/lifecycle/test_entry_gate.py`
  - Responsabilità: copertura TDD del comportamento update lifecycle.
  - Cambi previsti: aggiungere test su `changed_entries`, `UPDATE_PARTIAL`, `UPDATE_REJECTED`.

- `tests/runtime_v2/control_plane/test_clean_log_formatter.py`
  - Responsabilità: copertura formatter CLEAN_LOG.
  - Cambi previsti: aggiungere test per il rendering di `UPDATE_PARTIAL` e `UPDATE_REJECTED`.

---

### Task 1: Add Failing Tests For Missing Update Payload Data

**Files:**
- Modify: `tests/runtime_v2/lifecycle/test_entry_gate.py`

- [ ] **Step 1: Add a failing test for `MODIFY_ENTRIES` changed_entries**

Add a new test near the existing `MODIFY_ENTRIES` coverage that exercises `_apply_modify_entries()` and asserts the emitted `TELEGRAM_UPDATE_ACCEPTED` payload contains concrete `changed_entries`.

```python
def test_apply_modify_entries_emits_changed_entries_for_price_updates():
    import json

    gate = _make_gate_attached()
    chain = TradeChain(
        trade_chain_id=145,
        source_enrichment_id=1,
        canonical_message_id=10,
        raw_message_id=10,
        trader_id="t1",
        account_id="acc_1",
        symbol="BTC/USDT",
        side="LONG",
        lifecycle_state="WAITING_ENTRY",
        entry_mode="LADDER",
        management_plan_json="{}",
        risk_snapshot_json=json.dumps({
            "sl_price": 90000.0,
            "legs": [
                {"sequence": 1, "qty": 0.01},
                {"sequence": 2, "qty": 0.01},
            ],
        }),
        plan_state_json=json.dumps({
            "legs": [
                {"sequence": 1, "entry_type": "LIMIT", "price": 91000.0, "status": "PENDING"},
                {"sequence": 2, "entry_type": "LIMIT", "price": 92000.0, "status": "PENDING"},
            ]
        }),
    )
    enriched = _make_update_enriched(canonical_message_id=10)
    action = _make_modify_entries_action_update_price(sequence=2, new_price=93100.0)

    result = gate._apply_modify_entries(enriched, chain, action, active_commands=[])

    accepted = [e for e in result.lifecycle_events if e.event_type == "TELEGRAM_UPDATE_ACCEPTED"]
    assert len(accepted) == 1
    payload = json.loads(accepted[0].payload_json)
    assert payload["action"] == "MODIFY_ENTRIES"
    assert payload["changed_entries"] == [
        {"sequence": 2, "old_price": 92000.0, "new_price": 93100.0}
    ]
```

- [ ] **Step 2: Run the single test to verify it fails**

Run:

```bash
C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest tests\runtime_v2\lifecycle\test_entry_gate.py::test_apply_modify_entries_emits_changed_entries_for_price_updates -v
```

Expected: FAIL because the current payload only contains `{"action": "MODIFY_ENTRIES"}`.

- [ ] **Step 3: Add a failing test for `UPDATE_REJECTED` synthesis**

Add a direct `_write_update_clean_log()` test asserting that a fully rejected update carries both `reason` and `rejected_actions`.

```python
def test_write_update_clean_log_rejected_includes_reason_and_rejected_actions(ops_db):
    import json
    import sqlite3
    from src.runtime_v2.lifecycle.entry_gate import UpdateChainResult, _write_update_clean_log
    from src.runtime_v2.lifecycle.models import LifecycleEvent

    conn = sqlite3.connect(ops_db)
    _seed_chain(conn, chain_id=88, symbol="ETH/USDT", side="LONG")
    conn.commit()

    cr = UpdateChainResult(
        trade_chain_id=88,
        new_lifecycle_state=None,
        new_be_protection_status=None,
        lifecycle_events=[
            LifecycleEvent(
                trade_chain_id=88,
                event_type="NOOP_NOT_PENDING",
                source_type="telegram_update",
                source_id="9",
                payload_json=json.dumps({"reason": "not_pending"}),
                idempotency_key="noop:88:9",
            )
        ],
        execution_commands=[],
    )

    with conn:
        _write_update_clean_log(conn, cr, canonical_message_id=9, link=None)

    row = conn.execute(
        "SELECT payload_json FROM ops_notification_outbox WHERE notification_type='UPDATE_REJECTED'"
    ).fetchone()
    payload = json.loads(row[0])
    assert payload["rejected_actions"] == ["NOOP_NOT_PENDING"]
    assert payload["reason"] == "not_pending"
```

- [ ] **Step 4: Run the rejected synthesis test to verify it fails**

Run:

```bash
C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest tests\runtime_v2\lifecycle\test_entry_gate.py::test_write_update_clean_log_rejected_includes_reason_and_rejected_actions -v
```

Expected: FAIL because current synthesis does not populate `reason` for `UPDATE_REJECTED`.

- [ ] **Step 5: Commit the failing tests**

```bash
git add tests/runtime_v2/lifecycle/test_entry_gate.py
git commit -m "test: cover notification redesign payload gaps"
```

---

### Task 2: Implement Lifecycle Payload Enrichment And Synthesis

**Files:**
- Modify: `src/runtime_v2/lifecycle/entry_gate.py`
- Test: `tests/runtime_v2/lifecycle/test_entry_gate.py`

- [ ] **Step 1: Implement `changed_entries` in `_apply_modify_entries()`**

Update the event payload generation so it records concrete price changes while iterating diff actions.

```python
changed_entries: list[dict] = []

for diff_action in diff_actions:
    if diff_action["action"] == "update_entry_price":
        sequence = diff_action["sequence"]
        old_leg = next(
            (leg for leg in plan.get("legs", []) if leg.get("sequence") == sequence),
            None,
        )
        old_price = old_leg.get("price") if old_leg else None
        new_price = diff_action.get("new_price")
        if old_price is not None and new_price is not None:
            changed_entries.append({
                "sequence": sequence,
                "old_price": old_price,
                "new_price": new_price,
            })

event = LifecycleEvent(
    trade_chain_id=chain_id,
    event_type="TELEGRAM_UPDATE_ACCEPTED",
    source_type="telegram_update",
    source_id=str(cmid),
    payload_json=json.dumps({
        "action": "MODIFY_ENTRIES",
        "changed_entries": changed_entries,
    }),
    idempotency_key=f"update_modify_entries:{chain_id}:{cmid}",
)
```

- [ ] **Step 2: Extend `_write_update_clean_log()` to preserve rejected details**

Populate `reason` for rejected cases from the first noop/review payload that exposes one, and keep `rejected_actions` for both partial and rejected cases.

```python
rejected_actions: list[str] = [e.event_type for e in noops]
reason = None

for e in noops:
    try:
        noop_payload = json.loads(e.payload_json or "{}")
    except Exception:
        noop_payload = {}
    if reason is None and noop_payload.get("reason"):
        reason = noop_payload["reason"]

payload = {
    "chain_id": cr.trade_chain_id,
    "symbol": symbol,
    "side": side,
    "applied_actions": applied_actions,
    "rejected_actions": rejected_actions,
    "changed": changed,
    "source": source,
    "link": link,
}
if reason is not None:
    payload["reason"] = reason
```

- [ ] **Step 3: Run the two targeted lifecycle tests**

Run:

```bash
C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest ^
  tests\runtime_v2\lifecycle\test_entry_gate.py::test_apply_modify_entries_emits_changed_entries_for_price_updates ^
  tests\runtime_v2\lifecycle\test_entry_gate.py::test_write_update_clean_log_rejected_includes_reason_and_rejected_actions -v
```

Expected: PASS.

- [ ] **Step 4: Add a failing test for `UPDATE_PARTIAL` synthesis carrying changed data**

Add a test that creates one accepted event plus one noop and asserts the synthesized payload has both `changed` and `rejected_actions`.

```python
def test_write_update_clean_log_partial_keeps_changed_and_rejected_actions(ops_db):
    import json
    import sqlite3
    from src.runtime_v2.lifecycle.entry_gate import UpdateChainResult, _write_update_clean_log
    from src.runtime_v2.lifecycle.models import LifecycleEvent

    conn = sqlite3.connect(ops_db)
    _seed_chain(conn, chain_id=89, symbol="BTC/USDT", side="LONG")
    conn.commit()

    accepted = LifecycleEvent(
        trade_chain_id=89,
        event_type="TELEGRAM_UPDATE_ACCEPTED",
        source_type="telegram_update",
        source_id="10",
        payload_json=json.dumps({
            "action": "MOVE_SL_TO_BE",
            "old_sl_price": 91000.0,
            "new_sl_price": 94200.0,
            "is_breakeven": True,
        }),
        idempotency_key="accepted:89:10",
    )
    noop = LifecycleEvent(
        trade_chain_id=89,
        event_type="NOOP_ALREADY_PROTECTED_BE",
        source_type="telegram_update",
        source_id="10",
        payload_json=json.dumps({"reason": "already_protected_be"}),
        idempotency_key="noop:89:10",
    )

    cr = UpdateChainResult(
        trade_chain_id=89,
        new_lifecycle_state=None,
        new_be_protection_status=None,
        lifecycle_events=[accepted, noop],
        execution_commands=[],
    )

    with conn:
        _write_update_clean_log(conn, cr, canonical_message_id=10, link=None)

    row = conn.execute(
        "SELECT payload_json FROM ops_notification_outbox WHERE notification_type='UPDATE_PARTIAL'"
    ).fetchone()
    payload = json.loads(row[0])
    assert payload["rejected_actions"] == ["NOOP_ALREADY_PROTECTED_BE"]
    assert payload["changed"] == [
        {"field": "SL", "old": 91000.0, "new": 94200.0, "note": "BE"}
    ]
```

- [ ] **Step 5: Run the partial synthesis test and commit**

Run:

```bash
C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest tests\runtime_v2\lifecycle\test_entry_gate.py::test_write_update_clean_log_partial_keeps_changed_and_rejected_actions -v
```

Expected: PASS if the synthesis already carries the expected payload; if it fails, fix the minimal logic before committing.

Commit:

```bash
git add src/runtime_v2/lifecycle/entry_gate.py tests/runtime_v2/lifecycle/test_entry_gate.py
git commit -m "feat: enrich update notification lifecycle payloads"
```

---

### Task 3: Add Failing Formatter Tests For Partial And Rejected Updates

**Files:**
- Modify: `tests/runtime_v2/control_plane/test_clean_log_formatter.py`

- [ ] **Step 1: Add a failing formatter test for `UPDATE_PARTIAL`**

```python
def test_update_partial_renders_changed_and_rejected_sections():
    text = format_clean_log("UPDATE_PARTIAL", {
        "chain_id": 42,
        "symbol": "BTC/USDT",
        "side": "LONG",
        "changed": [
            {"field": "SL", "old": 91000.0, "new": 94200.0, "note": "BE"},
            {"field": "Entry_2", "old": 92500.0, "new": "cancelled"},
        ],
        "rejected_actions": ["NOOP_ALREADY_PROTECTED_BE"],
        "source": "trader_update",
    })

    assert "UPDATE PARTIAL" in text
    assert "Changed:" in text
    assert "SL: 91,000 -> 94,200 *" in text
    assert "Entry_2: 92,500 -> cancelled" in text
    assert "Rejected:" in text
    assert "NOOP_ALREADY_PROTECTED_BE" in text
```

- [ ] **Step 2: Run the partial formatter test to verify it fails**

Run:

```bash
C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest tests\runtime_v2\control_plane\test_clean_log_formatter.py::test_update_partial_renders_changed_and_rejected_sections -v
```

Expected: FAIL because `_update_partial()` currently ignores `changed`.

- [ ] **Step 3: Add a failing formatter test for `UPDATE_REJECTED`**

```python
def test_update_rejected_renders_reason_and_rejected_actions():
    text = format_clean_log("UPDATE_REJECTED", {
        "chain_id": 43,
        "symbol": "ETH/USDT",
        "side": "LONG",
        "reason": "not_pending",
        "rejected_actions": ["NOOP_NOT_PENDING"],
        "source": "trader_update",
    })

    assert "UPDATE REJECTED" in text
    assert "Reason: not_pending" in text
    assert "Rejected:" in text
    assert "NOOP_NOT_PENDING" in text
```

- [ ] **Step 4: Run the rejected formatter test to verify it fails**

Run:

```bash
C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest tests\runtime_v2\control_plane\test_clean_log_formatter.py::test_update_rejected_renders_reason_and_rejected_actions -v
```

Expected: FAIL because `_update_rejected()` currently does not render `Rejected:`.

- [ ] **Step 5: Commit the failing formatter tests**

```bash
git add tests/runtime_v2/control_plane/test_clean_log_formatter.py
git commit -m "test: cover partial and rejected update rendering"
```

---

### Task 4: Implement Formatter Changes

**Files:**
- Modify: `src/runtime_v2/control_plane/formatters/clean_log.py`
- Test: `tests/runtime_v2/control_plane/test_clean_log_formatter.py`

- [ ] **Step 1: Update `_update_partial()` to render changed diff lines**

Implement the same `changed` rendering shape already used by `_update_done()`, then keep the `Rejected:` section with technical codes.

```python
def _update_partial(p: dict) -> str:
    lines = _header("⚠️", p.get("chain_id"), "UPDATE PARTIAL", p.get("symbol"), p.get("side"), signal_link=p.get("signal_link"))
    changed = p.get("changed") or []
    if changed:
        lines.append("Changed:")
        for item in changed:
            field = item.get("field", "?")
            value = f"{_num(item.get('old'))} -> {_num(item.get('new'))}"
            note = item.get("note")
            if note:
                lines.append(f"{field}: {value} *")
                lines.append(f"* {note}")
            else:
                lines.append(f"{field}: {value}")
    rejected = p.get("rejected_actions") or []
    if rejected:
        lines.append("Rejected:")
        for action in rejected:
            lines.append(f"  • {action}")
    lines += _footer(p.get("source", "runtime"), p.get("link"))
    return _finalize(lines)
```

- [ ] **Step 2: Update `_update_rejected()` to render technical rejected actions**

```python
def _update_rejected(p: dict) -> str:
    lines = _header("❌", p.get("chain_id"), "UPDATE REJECTED", p.get("symbol"), p.get("side"), signal_link=p.get("signal_link"))
    if p.get("reason") is not None:
        lines.append(f"Reason: {p['reason']}")
    rejected = p.get("rejected_actions") or []
    if rejected:
        lines.append("Rejected:")
        for action in rejected:
            lines.append(f"  • {action}")
    lines += _footer(p.get("source", "runtime"), p.get("link"))
    return _finalize(lines)
```

- [ ] **Step 3: Run the two targeted formatter tests**

Run:

```bash
C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest ^
  tests\runtime_v2\control_plane\test_clean_log_formatter.py::test_update_partial_renders_changed_and_rejected_sections ^
  tests\runtime_v2\control_plane\test_clean_log_formatter.py::test_update_rejected_renders_reason_and_rejected_actions -v
```

Expected: PASS.

- [ ] **Step 4: Run the full formatter file**

Run:

```bash
C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest tests\runtime_v2\control_plane\test_clean_log_formatter.py -q
```

Expected: PASS with no regressions in existing CLEAN_LOG renderers.

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/control_plane/formatters/clean_log.py tests/runtime_v2/control_plane/test_clean_log_formatter.py
git commit -m "feat: render richer partial and rejected update notifications"
```

---

### Task 5: Final Verification

**Files:**
- Verify: `src/runtime_v2/lifecycle/entry_gate.py`
- Verify: `src/runtime_v2/control_plane/formatters/clean_log.py`
- Verify: `tests/runtime_v2/lifecycle/test_entry_gate.py`
- Verify: `tests/runtime_v2/control_plane/test_clean_log_formatter.py`
- Verify: `tests/runtime_v2/control_plane/test_dispatcher.py`
- Verify: `tests/runtime_v2/control_plane/test_outbox_writer.py`

- [ ] **Step 1: Run the notification redesign target suite**

```bash
C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest \
  tests\runtime_v2\control_plane\test_outbox_writer.py \
  tests\runtime_v2\control_plane\test_clean_log_formatter.py \
  tests\runtime_v2\control_plane\test_dispatcher.py \
  tests\runtime_v2\lifecycle\test_entry_gate.py -q
```

Expected: PASS for the full targeted suite.

- [ ] **Step 2: Inspect the diff stays within scope**

```bash
git diff -- src/runtime_v2/lifecycle/entry_gate.py src/runtime_v2/control_plane/formatters/clean_log.py tests/runtime_v2/lifecycle/test_entry_gate.py tests/runtime_v2/control_plane/test_clean_log_formatter.py
```

Expected: diff limited to lifecycle payload enrichment, clean log formatter rendering, and their tests.

- [ ] **Step 3: Manual acceptance checklist**

Verify against the approved spec:

```text
[ ] MODIFY_ENTRIES exposes changed_entries with old/new prices
[ ] UPDATE_PARTIAL renders applied changes and technical rejected actions
[ ] UPDATE_REJECTED renders reason and technical rejected actions
[ ] MULTI_CHAIN_SUMMARY unchanged
[ ] send_after / delay behavior unchanged
```

- [ ] **Step 4: Final commit**

```bash
git add src/runtime_v2/lifecycle/entry_gate.py src/runtime_v2/control_plane/formatters/clean_log.py tests/runtime_v2/lifecycle/test_entry_gate.py tests/runtime_v2/control_plane/test_clean_log_formatter.py
git commit -m "feat: close remaining notification redesign gaps"
```

- [ ] **Step 5: Prepare handoff summary**

```text
Report:
- root cause closed
- files changed
- tests run
- primary signal met/not met
- residual risks
```

---

## Self-Review

- Spec coverage checked:
  - `MODIFY_ENTRIES` diff concreti: coperto da Task 1-2
  - `UPDATE_PARTIAL` con modifiche + rifiuti: coperto da Task 2-4
  - `UPDATE_REJECTED` minimale con reason + rejected: coperto da Task 1-4
  - no change a dispatcher/delay/multi-chain: verificato in Task 5

- Placeholder scan:
  - nessun `TODO`, `TBD` o “implement later”
  - ogni step con codice o comando esplicito

- Type consistency:
  - `changed_entries`, `changed`, `rejected_actions`, `reason` usati con naming coerente in tutti i task
