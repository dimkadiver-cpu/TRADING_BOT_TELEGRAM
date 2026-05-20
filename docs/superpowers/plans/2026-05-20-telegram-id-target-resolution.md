# Telegram Message ID Target Resolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When an UPDATE message targets a signal via Telegram reply/link IDs, resolve those IDs to the correct trade chain using the `raw_message_id` bridge.

**Architecture:** `_resolve_targets` in `LifecycleEntryGate` receives a pre-built mapping `{telegram_message_id → raw_message_id}` from `LifecycleGateWorker`. The worker queries `raw_messages` in the parser DB before calling `process_update`. Resolution adds one new step: after `explicit_ids` fails, try matching `tag.targeting.telegram_message_ids` → `raw_message_id` → `chain.raw_message_id`.

**Tech Stack:** Python 3.12, SQLite (`sqlite3`), Pydantic v2, pytest

---

## File Map

| File | Change |
|---|---|
| `src/runtime_v2/lifecycle/entry_gate.py` | Add `tg_id_to_raw_id` param to `process_update` and `_resolve_targets`; add Telegram ID matching step |
| `src/runtime_v2/lifecycle/entry_gate.py` | `LifecycleGateWorker._process_row`: query parser DB for Telegram→raw_message_id mapping |
| `tests/runtime_v2/lifecycle/test_entry_gate.py` | New tests for Telegram ID resolution |

---

## Task 1: Add Telegram ID matching to `_resolve_targets`

**Files:**
- Modify: `src/runtime_v2/lifecycle/entry_gate.py:316-354`
- Test: `tests/runtime_v2/lifecycle/test_entry_gate.py`

### Context

`_resolve_targets` currently tries (in order):
1. Global scope (ALL_SHORT, ALL_LONG, GLOBAL_SCOPES, SYMBOL)
2. Symbol matching
3. `explicit_ids` vs `canonical_message_id`
4. Fallback (single chain or ambiguous)

We add step 3b between explicit_ids and fallback: match `telegram_message_ids` via `tg_id_to_raw_id` dict → `chain.raw_message_id`.

`TradeChain.raw_message_id: int` already exists (confirmed in models.py:62).

- [ ] **Step 1: Write the failing tests**

Add to `tests/runtime_v2/lifecycle/test_entry_gate.py`:

```python
# ── helpers ──────────────────────────────────────────────────────────────────

def _make_chain_with_raw_id(
    trade_chain_id: int,
    trader_id: str,
    symbol: str,
    side: str,
    raw_message_id: int,
) -> TradeChain:
    from src.runtime_v2.lifecycle.models import TradeChain
    return TradeChain(
        trade_chain_id=trade_chain_id,
        source_enrichment_id=trade_chain_id,
        canonical_message_id=trade_chain_id,
        raw_message_id=raw_message_id,
        trader_id=trader_id,
        account_id="acc",
        symbol=symbol,
        side=side,
        lifecycle_state="OPEN",
        entry_mode="b_entry_stop_then_tp",
        management_plan_json="{}",
    )


def _make_enriched_update_tg(
    trader_id: str,
    telegram_message_ids: list[int],
) -> "EnrichedCanonicalMessage":
    """UPDATE enriched message with telegram_message_ids targeting."""
    from src.parser_v2.contracts.canonical_message import (
        ActionItem, CloseOperation, TargetActionGroup,
    )
    from src.parser_v2.contracts.context import TargetHints
    from src.runtime_v2.signal_enrichment.models import EnrichedCanonicalMessage

    action = ActionItem(
        action_type="CLOSE",
        close=CloseOperation(close_scope="FULL"),
        source_intent="CLOSE_FULL",
    )
    tag = TargetActionGroup(
        targeting=TargetHints(
            telegram_message_ids=telegram_message_ids,
            scope_hint="SINGLE_SIGNAL",
        ),
        actions=[action],
    )
    return EnrichedCanonicalMessage(
        enrichment_id=99,
        canonical_message_id=99,
        raw_message_id=99,
        trader_id=trader_id,
        account_id="acc",
        primary_class="UPDATE",
        enrichment_decision="PASS",
        enriched_actions=[tag],
    )


# ── tests ─────────────────────────────────────────────────────────────────────

def test_resolve_targets_matches_via_telegram_message_id():
    """When two chains are open, Telegram ID resolves to the correct one."""
    from src.runtime_v2.lifecycle.entry_gate import LifecycleEntryGate

    chain_xrp = _make_chain_with_raw_id(1, "trader_a", "XRPUSDT", "SHORT", raw_message_id=10)
    chain_ada = _make_chain_with_raw_id(2, "trader_a", "ADAUSDT", "SHORT", raw_message_id=20)

    enriched = _make_enriched_update_tg("trader_a", telegram_message_ids=[10])
    tg_id_to_raw_id = {10: 10, 20: 20}

    gate = LifecycleEntryGate(execution_mode="b_entry_stop_then_tp")
    tag = enriched.enriched_actions[0]
    result = gate._resolve_targets(
        enriched, [chain_xrp, chain_ada], tag,
        tg_id_to_raw_id=tg_id_to_raw_id,
    )

    assert result == [chain_xrp]


def test_resolve_targets_telegram_id_no_match_falls_through_to_ambiguous():
    """If Telegram IDs don't match any chain, falls back to ambiguous."""
    from src.runtime_v2.lifecycle.entry_gate import LifecycleEntryGate

    chain_a = _make_chain_with_raw_id(1, "trader_a", "XRPUSDT", "SHORT", raw_message_id=10)
    chain_b = _make_chain_with_raw_id(2, "trader_a", "ADAUSDT", "SHORT", raw_message_id=20)

    enriched = _make_enriched_update_tg("trader_a", telegram_message_ids=[99])
    tg_id_to_raw_id = {99: 999}  # maps to raw_id=999 which no chain has

    gate = LifecycleEntryGate(execution_mode="b_entry_stop_then_tp")
    tag = enriched.enriched_actions[0]
    result = gate._resolve_targets(
        enriched, [chain_a, chain_b], tag,
        tg_id_to_raw_id=tg_id_to_raw_id,
    )

    assert result is None  # ambiguous — two chains, no Telegram match


def test_resolve_targets_telegram_id_empty_mapping_falls_through():
    """Empty tg_id_to_raw_id → no Telegram resolution, falls to ambiguous."""
    from src.runtime_v2.lifecycle.entry_gate import LifecycleEntryGate

    chain_a = _make_chain_with_raw_id(1, "trader_a", "XRPUSDT", "SHORT", raw_message_id=10)
    chain_b = _make_chain_with_raw_id(2, "trader_a", "ADAUSDT", "SHORT", raw_message_id=20)

    enriched = _make_enriched_update_tg("trader_a", telegram_message_ids=[10])

    gate = LifecycleEntryGate(execution_mode="b_entry_stop_then_tp")
    tag = enriched.enriched_actions[0]
    result = gate._resolve_targets(
        enriched, [chain_a, chain_b], tag,
        tg_id_to_raw_id={},  # empty — no lookup available
    )

    assert result is None  # ambiguous


def test_process_update_uses_tg_id_to_raw_id():
    """process_update routes CLOSE_FULL to the correct chain via Telegram ID."""
    from src.runtime_v2.lifecycle.entry_gate import LifecycleEntryGate

    chain_xrp = _make_chain_with_raw_id(1, "trader_a", "XRPUSDT", "SHORT", raw_message_id=2)
    chain_bad = _make_chain_with_raw_id(2, "trader_a", "XRPSDTUSDT", "SHORT", raw_message_id=1)

    enriched = _make_enriched_update_tg("trader_a", telegram_message_ids=[50])
    tg_id_to_raw_id = {50: 2}  # Telegram msg 50 → raw_message_id 2 → chain_xrp

    gate = LifecycleEntryGate(execution_mode="b_entry_stop_then_tp")
    result = gate.process_update(
        enriched,
        [chain_xrp, chain_bad],
        active_commands_by_chain={},
        tg_id_to_raw_id=tg_id_to_raw_id,
    )

    assert len(result.chain_results) == 1
    assert result.chain_results[0].trade_chain_id == 1  # XRPUSDT
    cmds = result.chain_results[0].execution_commands
    assert any(c.command_type == "CLOSE_FULL" for c in cmds)
    assert result.review_events == []
```

- [ ] **Step 2: Run tests to confirm they fail**

```
python -m pytest tests/runtime_v2/lifecycle/test_entry_gate.py -k "telegram" -v
```

Expected: `FAILED` — `_resolve_targets() got unexpected keyword argument 'tg_id_to_raw_id'` and `process_update() got unexpected keyword argument 'tg_id_to_raw_id'`

- [ ] **Step 3: Implement — update `_resolve_targets` signature and add Telegram ID step**

In `src/runtime_v2/lifecycle/entry_gate.py`, change `_resolve_targets`:

```python
def _resolve_targets(
    self,
    enriched: EnrichedCanonicalMessage,
    open_chains: list[TradeChain],
    tag,
    *,
    tg_id_to_raw_id: dict[int, int] | None = None,
) -> list[TradeChain] | None:
    scope = tag.targeting.scope_hint
    trader_chains = [c for c in open_chains if c.trader_id == enriched.trader_id]

    if scope == "ALL_SHORT":
        return [c for c in trader_chains if c.side == "SHORT"]
    if scope == "ALL_LONG":
        return [c for c in trader_chains if c.side == "LONG"]
    if scope in GLOBAL_SCOPES:
        return trader_chains

    if scope == "SYMBOL":
        symbols = tag.targeting.symbols
        return [c for c in trader_chains if c.symbol in symbols] if symbols else []

    # SINGLE_SIGNAL or UNKNOWN — try symbol matching then explicit_ids then telegram IDs
    if tag.targeting.symbols:
        matched = [c for c in trader_chains if c.symbol in tag.targeting.symbols]
        if len(matched) == 1:
            return matched
        if len(matched) > 1:
            return None

    if tag.targeting.explicit_ids:
        matched = [
            c for c in trader_chains
            if str(c.canonical_message_id) in tag.targeting.explicit_ids
        ]
        if matched:
            return matched

    if tag.targeting.telegram_message_ids and tg_id_to_raw_id:
        raw_ids = {
            tg_id_to_raw_id[tid]
            for tid in tag.targeting.telegram_message_ids
            if tid in tg_id_to_raw_id
        }
        if raw_ids:
            matched = [c for c in trader_chains if c.raw_message_id in raw_ids]
            if matched:
                return matched

    if len(trader_chains) > 1:
        return None
    return trader_chains
```

- [ ] **Step 4: Update `process_update` to accept and thread `tg_id_to_raw_id`**

```python
def process_update(
    self,
    enriched: EnrichedCanonicalMessage,
    open_chains: list[TradeChain],
    active_commands_by_chain: dict[int, list[ExecutionCommand]],
    *,
    tg_id_to_raw_id: dict[int, int] | None = None,
) -> UpdateGateResult:
    tags = enriched.enriched_actions or []
    if not tags:
        event = self._make_review_event_no_chain(enriched, "no_actionable_targets")
        return UpdateGateResult(chain_results=[], review_events=[event])

    chain_results: list[UpdateChainResult] = []
    review_events: list[LifecycleEvent] = []

    for tag in tags:
        matched = self._resolve_targets(
            enriched, open_chains, tag,
            tg_id_to_raw_id=tg_id_to_raw_id,
        )

        if matched is None:
            review_events.append(
                self._make_review_event_no_chain(enriched, "ambiguous_update_target")
            )
            continue
        if len(matched) == 0:
            review_events.append(
                self._make_review_event_no_chain(enriched, "no_update_target")
            )
            continue

        for chain in matched:
            chain_cmds = active_commands_by_chain.get(chain.trade_chain_id or 0, [])
            for action in tag.actions:
                chain_results.append(
                    self._apply_action_to_chain(enriched, chain, action, chain_cmds)
                )

    return UpdateGateResult(chain_results=chain_results, review_events=review_events)
```

- [ ] **Step 5: Run tests — verify they pass**

```
python -m pytest tests/runtime_v2/lifecycle/test_entry_gate.py -k "telegram" -v
```

Expected: 4 tests `PASSED`

- [ ] **Step 6: Run full test suite to check no regressions**

```
python -m pytest tests/runtime_v2/lifecycle/test_entry_gate.py -q
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/runtime_v2/lifecycle/entry_gate.py tests/runtime_v2/lifecycle/test_entry_gate.py
git commit -m "feat(lifecycle): resolve UPDATE targets via telegram_message_id→raw_message_id"
```

---

## Task 2: Build Telegram ID mapping in `LifecycleGateWorker`

**Files:**
- Modify: `src/runtime_v2/lifecycle/entry_gate.py:745-751` (`LifecycleGateWorker._process_row`)
- Test: `tests/runtime_v2/lifecycle/test_entry_gate.py`

### Context

`LifecycleGateWorker._process_row` already has access to `self._parser_db` (parser SQLite path). Before calling `process_update`, we query `raw_messages` for all `telegram_message_id` values appearing in the current update's `target_action_groups`.

- [ ] **Step 1: Write the failing integration test**

Add to `tests/runtime_v2/lifecycle/test_entry_gate.py`:

```python
def test_lifecycle_gate_worker_builds_tg_mapping_and_resolves_chain(tmp_path):
    """Worker queries parser DB and passes tg_id_to_raw_id to gate, resolving ambiguous update."""
    import sqlite3 as _sqlite3
    import json
    from unittest.mock import MagicMock
    from src.runtime_v2.lifecycle.entry_gate import LifecycleGateWorker, LifecycleEntryGate
    from src.runtime_v2.lifecycle.models import TradeChain

    # ── parser DB with raw_messages ──────────────────────────────────────────
    parser_db = str(tmp_path / "parser.sqlite3")
    pconn = _sqlite3.connect(parser_db)
    pconn.execute("""CREATE TABLE raw_messages (
        raw_message_id INTEGER PRIMARY KEY,
        telegram_message_id INTEGER,
        reply_to_message_id INTEGER
    )""")
    pconn.execute("""CREATE TABLE canonical_messages (
        canonical_message_id INTEGER PRIMARY KEY,
        raw_message_id INTEGER,
        primary_class TEXT,
        parse_status TEXT,
        run_context TEXT,
        canonical_json TEXT,
        created_at TEXT
    )""")
    pconn.execute("""CREATE TABLE enriched_canonical_messages (
        enrichment_id INTEGER PRIMARY KEY,
        canonical_message_id INTEGER,
        raw_message_id INTEGER,
        trader_id TEXT,
        account_id TEXT,
        primary_class TEXT,
        enrichment_decision TEXT,
        reason_code TEXT,
        enriched_signal_json TEXT,
        enriched_actions_json TEXT,
        management_plan_json TEXT,
        policy_snapshot_json TEXT,
        policy_version TEXT,
        enrichment_log_json TEXT,
        lifecycle_processed INTEGER DEFAULT 0,
        created_at TEXT
    )""")
    # Signal raw message: Telegram ID 50 → raw_message_id 1
    pconn.execute("INSERT INTO raw_messages VALUES (1, 50, NULL)")
    # Update raw message: Telegram ID 51 → raw_message_id 2, reply to 50
    pconn.execute("INSERT INTO raw_messages VALUES (2, 51, 50)")

    from src.parser_v2.contracts.canonical_message import (
        ActionItem, CloseOperation, TargetActionGroup,
    )
    from src.parser_v2.contracts.context import TargetHints

    action = ActionItem(
        action_type="CLOSE",
        close=CloseOperation(close_scope="FULL"),
        source_intent="CLOSE_FULL",
    )
    tag = TargetActionGroup(
        targeting=TargetHints(
            telegram_message_ids=[50],
            scope_hint="SINGLE_SIGNAL",
        ),
        actions=[action],
    )
    from src.runtime_v2.signal_enrichment.models import EnrichedCanonicalMessage
    update_enriched = EnrichedCanonicalMessage(
        enrichment_id=3,
        canonical_message_id=3,
        raw_message_id=2,
        trader_id="trader_a",
        account_id="acc",
        primary_class="UPDATE",
        enrichment_decision="PASS",
        enriched_actions=[tag],
    )
    actions_json = json.dumps([tag.model_dump()])
    pconn.execute(
        "INSERT INTO enriched_canonical_messages "
        "(enrichment_id,canonical_message_id,raw_message_id,trader_id,account_id,"
        "primary_class,enrichment_decision,enriched_actions_json,lifecycle_processed,created_at) "
        "VALUES (3,3,2,'trader_a','acc','UPDATE','PASS',?,0,'2026-01-01')",
        (actions_json,),
    )
    pconn.commit()
    pconn.close()

    # ── ops DB ───────────────────────────────────────────────────────────────
    ops_db = str(tmp_path / "ops.sqlite3")
    from src.core.migrations import apply_migrations
    apply_migrations(ops_db)
    oconn = _sqlite3.connect(ops_db)
    now = "2026-01-01T00:00:00+00:00"
    # Two chains: XRPUSDT (raw_message_id=1) and XRPSDTUSDT (raw_message_id=99)
    oconn.execute(
        "INSERT INTO ops_trade_chains (trade_chain_id,source_enrichment_id,canonical_message_id,"
        "raw_message_id,trader_id,account_id,symbol,side,lifecycle_state,entry_mode,"
        "management_plan_json,created_at,updated_at) VALUES (1,1,1,1,'trader_a','acc',"
        "'XRPUSDT','SHORT','OPEN','b_entry_stop_then_tp','{}',?,?)",
        (now, now),
    )
    oconn.execute(
        "INSERT INTO ops_trade_chains (trade_chain_id,source_enrichment_id,canonical_message_id,"
        "raw_message_id,trader_id,account_id,symbol,side,lifecycle_state,entry_mode,"
        "management_plan_json,created_at,updated_at) VALUES (2,2,2,99,'trader_a','acc',"
        "'XRPSDTUSDT','SHORT','WAITING_ENTRY','b_entry_stop_then_tp','{}',?,?)",
        (now, now),
    )
    oconn.commit()
    oconn.close()

    # ── repos & worker ────────────────────────────────────────────────────────
    from src.runtime_v2.lifecycle.repositories import (
        ControlStateRepository, ExecutionCommandRepository,
        ExchangeEventRepository, LifecycleEventRepository,
        SnapshotRepository, TradeChainRepository,
    )
    gate = LifecycleEntryGate(execution_mode="b_entry_stop_then_tp")
    worker = LifecycleGateWorker(
        parser_db_path=parser_db,
        ops_db_path=ops_db,
        gate=gate,
        chain_repo=TradeChainRepository(ops_db),
        event_repo=LifecycleEventRepository(ops_db),
        command_repo=ExecutionCommandRepository(ops_db),
        snapshot_repo=SnapshotRepository(ops_db),
        control_repo=ControlStateRepository(ops_db),
    )

    processed = worker.run_once()

    assert processed == 1
    oconn2 = _sqlite3.connect(ops_db)
    events = oconn2.execute(
        "SELECT event_type FROM ops_lifecycle_events ORDER BY event_id"
    ).fetchall()
    oconn2.close()
    event_types = [e[0] for e in events]
    assert "TELEGRAM_UPDATE_ACCEPTED" in event_types
    assert "REVIEW_REQUIRED" not in event_types
    cmds = _sqlite3.connect(ops_db).execute(
        "SELECT command_type, trade_chain_id FROM ops_execution_commands"
    ).fetchall()
    assert any(c[0] == "CLOSE_FULL" and c[1] == 1 for c in cmds)
```

- [ ] **Step 2: Run to confirm it fails**

```
python -m pytest tests/runtime_v2/lifecycle/test_entry_gate.py::test_lifecycle_gate_worker_builds_tg_mapping_and_resolves_chain -v
```

Expected: `FAILED` — `REVIEW_REQUIRED` in events (mapping not built yet)

- [ ] **Step 3: Implement — add `_build_tg_id_to_raw_id` and wire into `_process_row`**

In `src/runtime_v2/lifecycle/entry_gate.py`, inside `LifecycleGateWorker`, add the helper method:

```python
def _build_tg_id_to_raw_id(self, enriched_actions) -> dict[int, int]:
    all_tg_ids: set[int] = set()
    for tag in (enriched_actions or []):
        all_tg_ids.update(tag.targeting.telegram_message_ids)
        if tag.secondary_targeting:
            all_tg_ids.update(tag.secondary_targeting.telegram_message_ids)
    if not all_tg_ids:
        return {}
    placeholders = ",".join("?" for _ in all_tg_ids)
    conn = _sqlite3.connect(self._parser_db)
    try:
        rows = conn.execute(
            f"SELECT telegram_message_id, raw_message_id FROM raw_messages "
            f"WHERE telegram_message_id IN ({placeholders})",
            list(all_tg_ids),
        ).fetchall()
    finally:
        conn.close()
    return {int(r[0]): int(r[1]) for r in rows}
```

Then in `_process_row`, replace the `else` branch (lines 745-751):

```python
        else:
            active_cmds = {
                c.trade_chain_id: self._command_repo.get_active_for_chain(c.trade_chain_id)
                for c in open_chains
            }
            tg_id_to_raw_id = self._build_tg_id_to_raw_id(enriched.enriched_actions)
            result = self._gate.process_update(
                enriched, open_chains, active_cmds,
                tg_id_to_raw_id=tg_id_to_raw_id,
            )
            self._persist_update(enriched, result)
```

Also add the import at the top of the `LifecycleGateWorker` section (it already imports `_sqlite3` at line 640).

- [ ] **Step 4: Run the integration test**

```
python -m pytest tests/runtime_v2/lifecycle/test_entry_gate.py::test_lifecycle_gate_worker_builds_tg_mapping_and_resolves_chain -v
```

Expected: `PASSED`

- [ ] **Step 5: Run full lifecycle test suite**

```
python -m pytest tests/runtime_v2/lifecycle/ -q
```

Expected: all pass.

- [ ] **Step 6: Run full test suite**

```
python -m pytest tests/ -q --ignore=tests/parser_shared/test_rules_schema.py
```

Expected: all pass (the excluded test is a pre-existing failure unrelated to this fix).

- [ ] **Step 7: Commit**

```bash
git add src/runtime_v2/lifecycle/entry_gate.py tests/runtime_v2/lifecycle/test_entry_gate.py
git commit -m "feat(lifecycle): build telegram_message_id→raw_message_id mapping in worker"
```

---

## Self-Review

**Spec coverage:**
- ✅ `_resolve_targets` checks `telegram_message_ids` via `tg_id_to_raw_id`
- ✅ `process_update` threads `tg_id_to_raw_id` down
- ✅ `LifecycleGateWorker` queries parser DB and builds mapping before `process_update`
- ✅ Empty mapping → graceful fallback (existing behavior unchanged)
- ✅ No match found via Telegram IDs → still falls through to ambiguous/single-chain fallback

**Placeholder scan:** None found.

**Type consistency:**
- `tg_id_to_raw_id: dict[int, int]` — consistent across `_resolve_targets`, `process_update`, `_build_tg_id_to_raw_id`
- `tag.targeting.telegram_message_ids: list[int]` — confirmed in `src/parser_v2/contracts/context.py:28`
- `chain.raw_message_id: int` — confirmed in `src/runtime_v2/lifecycle/models.py:62`
