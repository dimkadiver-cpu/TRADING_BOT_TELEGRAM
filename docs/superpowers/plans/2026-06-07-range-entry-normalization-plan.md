# Range Entry Normalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Normalize parser-level `RANGE` entries into runtime-supported `ONE_SHOT` or `TWO_STEP`, persist range-derivation metadata into `plan_state_json`, and expose that provenance only in signal-phase clean-log notifications.

**Architecture:** The change starts in signal enrichment, where `RANGE` is converted into explicit runtime semantics and annotated with provenance metadata. Lifecycle chain creation copies the normalized `entry_structure` plus the provenance metadata into `ops_trade_chains.plan_state_json`, and clean-log projection/formatting reads only from `ops` to render a derivation label for `SIGNAL_ACCEPTED`, `SIGNAL_REJECTED`, and `REVIEW_REQUIRED`.

**Tech Stack:** Python 3.12, Pydantic v2 models, SQLite-backed `ops`/parser DBs, pytest

---

## File Map

- Modify: `src/runtime_v2/signal_enrichment/models.py`
  - Add explicit models/fields for normalized range provenance.
- Modify: `src/runtime_v2/signal_enrichment/processor.py`
  - Normalize `RANGE` to `ONE_SHOT` or `TWO_STEP`, emit provenance metadata, and write enrichment log entries.
- Modify: `src/runtime_v2/lifecycle/entry_gate.py`
  - Copy normalized `entry_structure` into `entry_mode` and inject provenance metadata into `plan_state_json` when the chain is created.
- Modify: `src/runtime_v2/control_plane/outbox_writer.py`
  - Read range provenance from `plan_state_json` and add a display-ready field only for signal-phase payloads.
- Modify: `src/runtime_v2/control_plane/formatters/clean_log.py`
  - Render the derivation label only for `SIGNAL_ACCEPTED`, `SIGNAL_REJECTED`, and `REVIEW_REQUIRED`.
- Modify: `tests/runtime_v2/signal_enrichment/test_range_split_mode.py`
  - Replace outdated expectations that collapsed range modes keep two legs.
- Modify: `tests/runtime_v2/lifecycle/test_entry_gate.py`
  - Assert `entry_mode` normalization and metadata propagation into `plan_state_json`.
- Modify: `tests/runtime_v2/control_plane/test_outbox_writer.py`
  - Assert signal payload projection includes derivation label fields from `plan_state_json`.
- Modify: `tests/runtime_v2/control_plane/test_clean_log_formatter.py`
  - Assert signal-phase formatting shows derivation label and later notifications do not.

### Task 1: Normalize `RANGE` in Signal Enrichment

**Files:**
- Modify: `src/runtime_v2/signal_enrichment/models.py`
- Modify: `src/runtime_v2/signal_enrichment/processor.py`
- Test: `tests/runtime_v2/signal_enrichment/test_range_split_mode.py`

- [ ] **Step 1: Write the failing tests for normalized runtime semantics**

Add these tests to `tests/runtime_v2/signal_enrichment/test_range_split_mode.py`:

```python
def test_midpoint_collapses_to_single_leg():
    legs = _make_range_legs(64_000.0, 65_000.0)
    result = SignalEnrichmentProcessor._apply_range_split(legs, "midpoint")
    assert len(result) == 1
    assert result[0].sequence == 1
    assert result[0].price.value == 64_500.0
    assert result[0].weight == 1.0


def test_firstpoint_collapses_to_single_leg():
    legs = _make_range_legs(64_000.0, 65_000.0)
    result = SignalEnrichmentProcessor._apply_range_split(legs, "firstpoint")
    assert len(result) == 1
    assert result[0].price.value == 64_000.0


def test_lastpoint_collapses_to_single_leg():
    legs = _make_range_legs(64_000.0, 65_000.0)
    result = SignalEnrichmentProcessor._apply_range_split(legs, "lastpoint")
    assert len(result) == 1
    assert result[0].price.value == 65_000.0
```

- [ ] **Step 2: Write the failing tests for normalized `entry_structure` and provenance**

Append a focused processor-level test:

```python
def test_process_signal_normalizes_range_midpoint_to_one_shot():
    from unittest.mock import MagicMock
    from types import SimpleNamespace
    from src.parser_v2.contracts.entities import EntryLeg, Price, StopLoss, TakeProfit
    from src.runtime_v2.parser_pipeline.models import CanonicalParseResult

    processor = SignalEnrichmentProcessor(config_loader=MagicMock(), repository=MagicMock())
    config = TestRangeSplitIntegration()._make_config("midpoint")
    processor._config.get_effective_config.return_value = config
    processor._config.get_symbol_blacklist_global.return_value = set()
    processor._config.get_symbol_blacklist_for_trader.return_value = set()
    processor._config.get_policy_version.return_value = "test"
    processor._repo.get_by_canonical_message_id.return_value = None
    processor._repo.save.side_effect = lambda enriched: enriched.model_copy(update={"enrichment_id": 1})

    signal = SimpleNamespace(
        symbol="BTCUSDT",
        side="LONG",
        entry_structure="RANGE",
        entries=[
            EntryLeg(sequence=1, entry_type="LIMIT", price=Price(raw="64000", value=64000.0)),
            EntryLeg(sequence=2, entry_type="LIMIT", price=Price(raw="65000", value=65000.0)),
        ],
        stop_loss=StopLoss(price=Price(raw="62000", value=62000.0)),
        take_profits=[TakeProfit(sequence=1, price=Price(raw="68000", value=68000.0))],
    )
    canonical_message = SimpleNamespace(signal=signal, target_action_groups=[])
    parse_result = CanonicalParseResult(
        canonical_message_id=1,
        raw_message_id=1,
        parser_profile="trader_a",
        primary_class="SIGNAL",
        canonical_message=canonical_message,
    )

    enriched = processor.process(parse_result)

    assert enriched.enriched_signal.entry_structure == "ONE_SHOT"
    assert len(enriched.enriched_signal.entries) == 1
    assert any(e.check == "range_price_derived" and e.detail == "midpoint" for e in enriched.enrichment_log)
```

- [ ] **Step 3: Run the failing tests**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/signal_enrichment/test_range_split_mode.py -q
```

Expected:

- failures in the old `_apply_range_split` expectations because collapsed modes still return two legs;
- failure because `entry_structure` still remains `RANGE`.

- [ ] **Step 4: Add provenance models to the enrichment layer**

Update `src/runtime_v2/signal_enrichment/models.py` with:

```python
class RangeDerivation(BaseModel):
    derived_from_range: bool = False
    split_mode: Literal["endpoints", "firstpoint", "lastpoint", "midpoint"]
    original_min_price: float
    original_max_price: float


class EnrichedSignalPayload(BaseModel):
    symbol: str | None
    side: Side | None
    entry_structure: EntryStructure | None
    entries: list[EnrichedEntryLeg]
    take_profits: list[TakeProfit]
    stop_loss: StopLoss | None
    range_derivation: RangeDerivation | None = None
```

- [ ] **Step 5: Implement normalization and enrichment logging**

Refactor `src/runtime_v2/signal_enrichment/processor.py` so `_apply_entry_weights` returns normalized structure plus provenance:

```python
def _apply_entry_weights(self, signal, config: EffectiveEnrichmentConfig) -> tuple[list[EnrichedEntryLeg], str | None, RangeDerivation | None, list[EnrichmentLogEntry]]:
    ...
    if structure == "RANGE" and entry_type_key == "LIMIT":
        result, normalized_structure, range_derivation, range_logs = self._apply_range_split(
            result,
            split.LIMIT.range.split_mode,
        )
        return result, normalized_structure, range_derivation, range_logs
    return result, structure, None, []
```

Use this `_apply_range_split` shape:

```python
@staticmethod
def _apply_range_split(
    legs: list[EnrichedEntryLeg],
    split_mode: str,
) -> tuple[list[EnrichedEntryLeg], str, RangeDerivation | None, list[EnrichmentLogEntry]]:
    from src.parser_v2.contracts.entities import Price

    if len(legs) < 2:
        structure = "ONE_SHOT" if len(legs) == 1 else "RANGE"
        return legs, structure, None, []

    valid_prices = [leg.price.value for leg in legs if leg.price is not None]
    if not valid_prices:
        return legs, "RANGE", None, []

    min_price = min(valid_prices)
    max_price = max(valid_prices)
    meta = RangeDerivation(
        derived_from_range=True,
        split_mode=split_mode,
        original_min_price=min_price,
        original_max_price=max_price,
    )

    if split_mode == "endpoints":
        log_entry = EnrichmentLogEntry(
            check="range_endpoints_retained",
            original=f"{min_price}-{max_price}",
            result="two_step",
            detail="endpoints",
        )
        return legs, "TWO_STEP", meta, [log_entry]

    if split_mode == "firstpoint":
        target = min_price
    elif split_mode == "lastpoint":
        target = max_price
    elif split_mode == "midpoint":
        target = round((min_price + max_price) / 2, 8)
    else:
        return legs, "RANGE", None, []

    first_leg = min(legs, key=lambda l: l.sequence)
    collapsed = [first_leg.model_copy(update={
        "price": Price(raw=str(target), value=target),
        "weight": 1.0,
    })]
    log_entry = EnrichmentLogEntry(
        check="range_price_derived",
        original=f"{min_price}-{max_price}",
        result=str(target),
        detail=split_mode,
    )
    return collapsed, "ONE_SHOT", meta, [log_entry]
```

Then consume it in `_process_signal`:

```python
entries, normalized_structure, range_derivation, range_logs = self._apply_entry_weights(signal, config)
log.extend(range_logs)
...
enriched_signal = EnrichedSignalPayload(
    symbol=symbol or None,
    side=signal.side,
    entry_structure=normalized_structure,
    entries=entries,
    take_profits=take_profits,
    stop_loss=signal.stop_loss,
    range_derivation=range_derivation,
)
```

- [ ] **Step 6: Run the focused enrichment tests**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/signal_enrichment/test_range_split_mode.py -q
```

Expected:

- all tests in `test_range_split_mode.py` pass.

- [ ] **Step 7: Commit the enrichment normalization**

```bash
git add src/runtime_v2/signal_enrichment/models.py src/runtime_v2/signal_enrichment/processor.py tests/runtime_v2/signal_enrichment/test_range_split_mode.py
git commit -m "feat: normalize range entries in signal enrichment"
```

### Task 2: Propagate Normalized Semantics into Lifecycle Chain Creation

**Files:**
- Modify: `src/runtime_v2/lifecycle/entry_gate.py`
- Test: `tests/runtime_v2/lifecycle/test_entry_gate.py`

- [ ] **Step 1: Write the failing lifecycle test for `entry_mode` normalization**

Add a test near existing `entry_mode` assertions in `tests/runtime_v2/lifecycle/test_entry_gate.py`:

```python
def test_gate_signal_range_endpoints_persists_two_step_entry_mode():
    result, _ = _build_signal_gate_result(
        entry_structure="TWO_STEP",
        entries=[
            {"sequence": 1, "entry_type": "LIMIT", "price": 64000.0, "weight": 0.5},
            {"sequence": 2, "entry_type": "LIMIT", "price": 65000.0, "weight": 0.5},
        ],
        range_derivation={
            "derived_from_range": True,
            "split_mode": "endpoints",
            "original_min_price": 64000.0,
            "original_max_price": 65000.0,
        },
    )
    assert result.trade_chain.entry_mode == "TWO_STEP"
```

- [ ] **Step 2: Write the failing lifecycle test for `plan_state_json` provenance copy**

Add:

```python
def test_gate_signal_copies_range_derivation_into_plan_state_json():
    result, _ = _build_signal_gate_result(
        entry_structure="ONE_SHOT",
        entries=[{"sequence": 1, "entry_type": "LIMIT", "price": 64500.0, "weight": 1.0}],
        range_derivation={
            "derived_from_range": True,
            "split_mode": "midpoint",
            "original_min_price": 64000.0,
            "original_max_price": 65000.0,
        },
    )
    plan = json.loads(result.trade_chain.plan_state_json)
    assert plan["range_derivation"]["derived_from_range"] is True
    assert plan["range_derivation"]["split_mode"] == "midpoint"
    assert plan["range_derivation"]["original_min_price"] == 64000.0
    assert plan["range_derivation"]["original_max_price"] == 65000.0
```

- [ ] **Step 3: Run the failing lifecycle tests**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/lifecycle/test_entry_gate.py -k range_derivation -q
```

Expected:

- failures because `plan_state_json` currently contains no `range_derivation`;
- possible fixture-helper failures because the helper does not yet accept `range_derivation`.

- [ ] **Step 4: Extend lifecycle chain creation to persist provenance**

In `src/runtime_v2/lifecycle/entry_gate.py`, when building the execution plan and trade chain, inject the provenance metadata into the serialized plan:

```python
plan_state = ExecutionPlanBuilder.build(
    enrichment_id=eid,
    entries=signal.entries,
    take_profits=signal.take_profits,
    risk_snapshot=risk_snapshot,
)

plan_data = json.loads(plan_state)
if signal.range_derivation is not None:
    plan_data["range_derivation"] = signal.range_derivation.model_dump()
plan_state = json.dumps(plan_data)

trade_chain = TradeChain(
    source_enrichment_id=eid,
    canonical_message_id=enriched.canonical_message_id,
    raw_message_id=enriched.raw_message_id,
    trader_id=enriched.trader_id,
    account_id=enriched.account_id,
    symbol=signal.symbol or "",
    side=signal.side or "",
    lifecycle_state="WAITING_ENTRY",
    entry_mode=signal.entry_structure or "ONE_SHOT",
    ...
    plan_state_json=plan_state,
)
```

- [ ] **Step 5: Update or add helper fixtures used by the new tests**

Where lifecycle tests build `EnrichedSignalPayload`, use:

```python
range_derivation = (
    RangeDerivation.model_validate(range_derivation_dict)
    if range_derivation_dict is not None
    else None
)

signal = EnrichedSignalPayload(
    symbol="BTC/USDT",
    side="LONG",
    entry_structure=entry_structure,
    entries=entries,
    take_profits=[],
    stop_loss=None,
    range_derivation=range_derivation,
)
```

- [ ] **Step 6: Run the focused lifecycle tests**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/lifecycle/test_entry_gate.py -k "range_derivation or entry_mode_matches_entry_structure" -q
```

Expected:

- targeted lifecycle tests pass;
- no regression in the existing `entry_mode` expectation.

- [ ] **Step 7: Commit the lifecycle propagation**

```bash
git add src/runtime_v2/lifecycle/entry_gate.py tests/runtime_v2/lifecycle/test_entry_gate.py
git commit -m "feat: persist range derivation in lifecycle plan state"
```

### Task 3: Project Range Provenance into Signal-Phase Clean-Log Payloads

**Files:**
- Modify: `src/runtime_v2/control_plane/outbox_writer.py`
- Test: `tests/runtime_v2/control_plane/test_outbox_writer.py`

- [ ] **Step 1: Write the failing outbox projection test**

Add to `tests/runtime_v2/control_plane/test_outbox_writer.py`:

```python
def test_signal_accepted_payload_includes_range_derivation_summary(ops_db):
    conn = sqlite3.connect(ops_db)
    plan_state = json.dumps({
        "stop_loss": 62000.0,
        "final_tp": 71000.0,
        "intermediate_tps": [68000.0],
        "legs": [{"sequence": 1, "entry_type": "LIMIT", "price": 64500.0, "status": "PENDING"}],
        "range_derivation": {
            "derived_from_range": True,
            "split_mode": "midpoint",
            "original_min_price": 64000.0,
            "original_max_price": 65000.0,
        },
    })
    with conn:
        _seed_chain(conn, 145)
        conn.execute(
            "UPDATE ops_trade_chains SET plan_state_json=?, entry_mode='ONE_SHOT' WHERE trade_chain_id=?",
            (plan_state, 145),
        )
        _seed_event(conn, 145, "SIGNAL_ACCEPTED", "sig_accepted:145")
        project_clean_log_for_chain(conn, 145)
    payload = json.loads(conn.execute(
        "SELECT payload_json FROM ops_notification_outbox WHERE notification_type='SIGNAL_ACCEPTED'"
    ).fetchone()[0])
    conn.close()
    assert payload["range_derivation_label"] == "midpoint from range 64000.0-65000.0"
```

- [ ] **Step 2: Write the failing negative test for execution-phase notifications**

Add:

```python
def test_entry_opened_payload_does_not_include_range_derivation_summary(ops_db):
    conn = sqlite3.connect(ops_db)
    plan_state = json.dumps({
        "legs": [
            {"sequence": 1, "entry_type": "LIMIT", "price": 64500.0, "status": "FILLED"},
            {"sequence": 2, "entry_type": "LIMIT", "price": 64000.0, "status": "PENDING"},
        ],
        "range_derivation": {
            "derived_from_range": True,
            "split_mode": "endpoints",
            "original_min_price": 64000.0,
            "original_max_price": 65000.0,
        },
    })
    with conn:
        _seed_chain(conn, 200)
        conn.execute(
            "UPDATE ops_trade_chains SET plan_state_json=?, entry_mode='TWO_STEP' WHERE trade_chain_id=?",
            (plan_state, 200),
        )
        _seed_event(conn, 200, "ENTRY_FILLED", "entry_filled:200:1", {"fill_price": 64500.0, "filled_qty": 0.01})
        project_clean_log_for_chain(conn, 200)
    payload = json.loads(conn.execute(
        "SELECT payload_json FROM ops_notification_outbox WHERE notification_type='ENTRY_OPENED'"
    ).fetchone()[0])
    conn.close()
    assert "range_derivation_label" not in payload
```

- [ ] **Step 3: Run the failing outbox writer tests**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/control_plane/test_outbox_writer.py -k range_derivation -q
```

Expected:

- both tests fail because `_build_payload` does not yet expose `range_derivation_label`.

- [ ] **Step 4: Add a helper to derive signal-phase label from `plan_state_json`**

In `src/runtime_v2/control_plane/outbox_writer.py`, add:

```python
def _range_derivation_label(plan: dict) -> str | None:
    meta = plan.get("range_derivation") or {}
    if not meta or not meta.get("derived_from_range"):
        return None
    split_mode = meta.get("split_mode")
    min_price = meta.get("original_min_price")
    max_price = meta.get("original_max_price")
    if split_mode is None or min_price is None or max_price is None:
        return None
    return f"{split_mode} from range {min_price}-{max_price}"
```

- [ ] **Step 5: Inject the label only for signal-phase payloads**

Update signal branches in `_build_payload`:

```python
range_label = _range_derivation_label(plan)

if notification_type == "SIGNAL_ACCEPTED":
    payload = {
        ...
        "link": ev.get("source_message_link"),
    }
    if range_label is not None:
        payload["range_derivation_label"] = range_label
    ...

if notification_type == "SIGNAL_REJECTED":
    payload = {
        ...
        "link": ev.get("source_message_link"),
    }
    if range_label is not None:
        payload["range_derivation_label"] = range_label
    return payload

if notification_type == "REVIEW_REQUIRED":
    payload = {
        ...
        "link": ev.get("source_message_link"),
    }
    if range_label is not None:
        payload["range_derivation_label"] = range_label
    return payload
```

- [ ] **Step 6: Run the focused outbox writer tests**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/control_plane/test_outbox_writer.py -k "range_derivation or projection_maps_signal_accepted" -q
```

Expected:

- the new signal-phase provenance tests pass;
- existing signal projection tests remain green.

- [ ] **Step 7: Commit the outbox projection change**

```bash
git add src/runtime_v2/control_plane/outbox_writer.py tests/runtime_v2/control_plane/test_outbox_writer.py
git commit -m "feat: project range derivation into signal clean-log payloads"
```

### Task 4: Render the Derivation Label in Signal-Phase Clean-Log Output

**Files:**
- Modify: `src/runtime_v2/control_plane/formatters/clean_log.py`
- Test: `tests/runtime_v2/control_plane/test_clean_log_formatter.py`

- [ ] **Step 1: Write the failing formatter test for `SIGNAL_ACCEPTED`**

Add:

```python
def test_signal_accepted_renders_range_derivation_label():
    text = format_clean_log("SIGNAL_ACCEPTED", {
        "chain_id": 145,
        "symbol": "BTC/USDT",
        "side": "LONG",
        "entries": [{"sequence": 1, "entry_type": "LIMIT", "price": 64500.0}],
        "range_derivation_label": "midpoint from range 64000.0-65000.0",
        "source": "trader_signal",
    })
    assert "Derived entry: midpoint from range 64000.0-65000.0" in text
```

- [ ] **Step 2: Write the failing formatter tests for `SIGNAL_REJECTED` and `REVIEW_REQUIRED`**

Add:

```python
def test_signal_rejected_renders_range_derivation_label():
    text = format_clean_log("SIGNAL_REJECTED", {
        "chain_id": 146,
        "symbol": "BTC/USDT",
        "side": "LONG",
        "reason": "risk_capacity_exceeded",
        "range_derivation_label": "endpoints from range 64000.0-65000.0",
        "source": "runtime",
    })
    assert "Derived entry: endpoints from range 64000.0-65000.0" in text


def test_entry_opened_does_not_render_range_derivation_label():
    text = format_clean_log("ENTRY_OPENED", {
        "chain_id": 145,
        "symbol": "BTC/USDT",
        "side": "LONG",
        "fill_price": 64500.0,
        "filled_qty": 0.01,
        "range_derivation_label": "midpoint from range 64000.0-65000.0",
        "source": "exchange",
    })
    assert "Derived entry:" not in text
```

- [ ] **Step 3: Run the failing formatter tests**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/control_plane/test_clean_log_formatter.py -k range_derivation -q
```

Expected:

- failures because signal formatter branches ignore `range_derivation_label`.

- [ ] **Step 4: Render the label in the shared signal-body helpers**

Update `src/runtime_v2/control_plane/formatters/clean_log.py`:

```python
def _append_signal_body(lines: list[str], p: dict) -> None:
    for entry in p.get("entries") or []:
        ...
    if p.get("range_derivation_label"):
        lines.append(f"Derived entry: {p['range_derivation_label']}")
    if p.get("sl") is not None:
        lines.append(f"SL: {_num(p['sl'])}")
    ...
```

Update `_review_required` similarly because it currently duplicates the signal body instead of calling `_append_signal_body`:

```python
def _review_required(p: dict) -> str:
    lines = _header("⚠️", p.get("chain_id"), "REVIEW REQUIRED", p.get("symbol"), p.get("side"))
    for entry in p.get("entries") or []:
        ...
    if p.get("range_derivation_label"):
        lines.append(f"Derived entry: {p['range_derivation_label']}")
    ...
```

- [ ] **Step 5: Run the focused formatter tests**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/control_plane/test_clean_log_formatter.py -k "range_derivation or signal_accepted or review_required" -q
```

Expected:

- all new formatter tests pass;
- no execution-phase formatter accidentally renders the label.

- [ ] **Step 6: Run the full targeted regression pack**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/signal_enrichment/test_range_split_mode.py tests/runtime_v2/lifecycle/test_entry_gate.py tests/runtime_v2/control_plane/test_outbox_writer.py tests/runtime_v2/control_plane/test_clean_log_formatter.py -q
```

Expected:

- all selected tests pass;
- no regression around averaging behavior, signal payload projection, or formatter output.

- [ ] **Step 7: Commit the formatter and final verification**

```bash
git add src/runtime_v2/control_plane/formatters/clean_log.py tests/runtime_v2/control_plane/test_clean_log_formatter.py
git add docs/superpowers/specs/2026-06-07-range-entry-normalization-design.md docs/superpowers/plans/2026-06-07-range-entry-normalization-plan.md
git commit -m "feat: show range derivation in signal clean log"
```

## Self-Review

### Spec coverage

- Runtime normalization is covered in Task 1.
- Lifecycle propagation into `plan_state_json` is covered in Task 2.
- Clean-log payload projection from `ops` only is covered in Task 3.
- Visibility only for `SIGNAL_ACCEPTED`, `SIGNAL_REJECTED`, `REVIEW_REQUIRED` is covered in Tasks 3 and 4.
- Preservation of existing averaging behavior is covered by not changing post-fill logic and by regression runs in Task 4 Step 6.

### Placeholder scan

- No `TODO`/`TBD` markers remain.
- Each code-changing step includes concrete code.
- Each validation step includes an exact command and expected outcome.

### Type consistency

- Provenance model name is `RangeDerivation` throughout.
- Payload/display field name is `range_derivation_label` throughout.
- `plan_state_json` metadata key is `range_derivation` throughout.
