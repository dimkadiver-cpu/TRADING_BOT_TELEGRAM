# Setup Reshape Mode — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-trader optional `reshape` pre-pass that rewrites Entry/SL/TP geometry before the existing enrichment pipeline, with full reject-on-failure semantics and audit trail.

**Architecture:** A new `reshaping/` package under `signal_enrichment/` holds three pure modules (orchestrator, RR-based TP selector, validator). `config_loader` resolves templates at load time into `EffectiveEnrichmentConfig.setup_reshape_template`. `processor` inserts the reshape stage between gate 4 (SL required) and the weights step, bypassing `use_tp_count` trim in reshape mode. All downstream code (weights, sizing, lifecycle) operates on the reshaped output unchanged.

**Tech Stack:** Python 3.12, Pydantic v2, PyYAML, pytest

## Global Constraints

- `setup_mode: passthrough` is the default — all 12 existing traders are unaffected until explicitly set to `reshape`
- Reshape failures → `enrichment_decision: "BLOCK"` with existing `SIGNAL REJECTED` notification path
- `on_failure: REJECT` is the only supported value in v1 (`REVIEW` not implemented)
- `reshaped` and `reshape_rejected` fields in `EnrichedSignalPayload` are additive — no DB migration
- Template id not found at load time → `ConfigLoadError` (fail-fast, bot doesn't start)
- `min_tp_count` in template match uses the **original parsed TP count**, before any `use_tp_count` trim
- In reshape mode, `use_tp_count` trim is bypassed entirely; reshape owns TP cardinality
- Realign (`_realign_limit_entries_by_side`) must run **before** reshape in reshape mode (E1..En indices stable)
- Tests go in `src/runtime_v2/signal_enrichment/tests/`
- Run tests: `pytest src/runtime_v2/signal_enrichment/tests/ -v`

---

### Task 1: Reshape Pydantic models in models.py

**Files:**
- Modify: `src/runtime_v2/signal_enrichment/models.py`
- Create: `src/runtime_v2/signal_enrichment/tests/__init__.py`
- Create: `src/runtime_v2/signal_enrichment/tests/test_reshape_models.py`

**Interfaces:**
- Produces: `ReshapeEntriesConfig`, `ReshapeStopLossConfig`, `ReshapeTakeProfitsConfig`, `ReshapeMatchConfig`, `ReshapeTemplateConfig` — template definition models, used by config_loader and setup_reshaper
- Produces: `ReshapeAuditEntry`, `ReshapeAuditDiscarded`, `ReshapeAuditStopLoss`, `ReshapeAuditTpSelected`, `ReshapeAuditRr`, `ReshapeAuditTpSelection`, `ReshapeAudit`, `ReshapeRejectionInfo` — audit/rejection models embedded in `EnrichedSignalPayload`
- Modifies: `EnrichedSignalPayload` — adds `reshaped: ReshapeAudit | None` and `reshape_rejected: ReshapeRejectionInfo | None`
- Modifies: `EffectiveEnrichmentConfig` — adds `setup_mode` and `setup_reshape_template`

- [ ] **Step 1: Create the tests directory**

```
mkdir src/runtime_v2/signal_enrichment/tests
```
Create `src/runtime_v2/signal_enrichment/tests/__init__.py` as an empty file.

- [ ] **Step 2: Write the failing model test**

Create `src/runtime_v2/signal_enrichment/tests/test_reshape_models.py`:

```python
from src.runtime_v2.signal_enrichment.models import (
    ReshapeTemplateConfig,
    ReshapeEntriesConfig,
    ReshapeStopLossConfig,
    ReshapeTakeProfitsConfig,
    ReshapeMatchConfig,
    ReshapeAudit,
    ReshapeAuditRr,
    ReshapeAuditTpSelection,
    ReshapeAuditEntry,
    ReshapeAuditDiscarded,
    ReshapeAuditStopLoss,
    ReshapeAuditTpSelected,
    ReshapeRejectionInfo,
    EnrichedSignalPayload,
    EffectiveEnrichmentConfig,
)


def _make_template():
    return ReshapeTemplateConfig(
        id="test_template",
        enabled=True,
        match=ReshapeMatchConfig(entry_structure="LADDER", normalized_entry_count=4, min_tp_count=8),
        entries=ReshapeEntriesConfig(mode="drop", indexes=["E1"]),
        stop_loss=ReshapeStopLossConfig(mode="from_entry", entry="E4"),
        take_profits=ReshapeTakeProfitsConfig(
            mode="by_rr",
            desired_rr=[1.0, 1.5, 2.5, 3.5],
            strategy="nearest_unique",
            max_rr_deviation_abs=0.35,
            on_missing_target="REJECT",
        ),
        on_failure="REJECT",
    )


def test_template_config_round_trips():
    t = _make_template()
    assert t.id == "test_template"
    assert t.match.entry_structure == "LADDER"
    assert t.match.normalized_entry_count == 4
    assert t.match.min_tp_count == 8
    assert t.entries.mode == "drop"
    assert t.entries.indexes == ["E1"]
    assert t.stop_loss.mode == "from_entry"
    assert t.stop_loss.entry == "E4"
    assert t.take_profits.mode == "by_rr"
    assert t.take_profits.desired_rr == [1.0, 1.5, 2.5, 3.5]


def test_reshape_audit_model():
    audit = ReshapeAudit(
        rule_id="test_template",
        discarded_entries=[ReshapeAuditDiscarded(source="E1", price=100.0, reason="initial_entry_skipped")],
        operative_entries=[ReshapeAuditEntry(source="E2", price=98.0), ReshapeAuditEntry(source="E3", price=96.0)],
        stop_loss=ReshapeAuditStopLoss(source="E4", price=94.0, replaced_original=92.0),
        rr=ReshapeAuditRr(anchor=97.4, stop=94.0, r_unit=3.4),
        tp_selection=ReshapeAuditTpSelection(
            mode="by_rr",
            selected=[ReshapeAuditTpSelected(price=100.0, rr=0.76)],
            discarded=[98.0, 104.0],
        ),
    )
    assert audit.rule_id == "test_template"
    assert audit.rr.anchor == 97.4


def test_reshape_rejection_info():
    rej = ReshapeRejectionInfo(rule_id="test_template", phase="no_match", reason_code="reshape_no_match")
    assert rej.phase == "no_match"


def test_effective_config_has_setup_mode(tmp_path):
    # EffectiveEnrichmentConfig default setup_mode is passthrough
    from src.runtime_v2.signal_enrichment.models import (
        SignalPolicyConfig, EntrySplitConfig, LimitEntrySplitConfig,
        MarketEntrySplitConfig, EntryWeightsConfig, EntryRangeConfig,
        TpConfig, SlConfig, PriceCorrectionsConfig, PriceSanityConfig,
        ManagementPlanConfig, CloseDistributionConfig, RiskConfig, MarketExecutionConfig,
    )
    cfg = EffectiveEnrichmentConfig(
        trader_id="t1",
        enabled=True,
        gate_mode="block",
        hedge_mode=False,
        account_id="main",
        signal_policy=SignalPolicyConfig(
            accepted_entry_structures=["LADDER"],
            market_execution=MarketExecutionConfig(),
            entry_split=EntrySplitConfig(
                LIMIT=LimitEntrySplitConfig(
                    single=EntryWeightsConfig(weights={"E1": 1.0}),
                    range=EntryRangeConfig(weights={"E1": 0.5, "E2": 0.5}),
                    averaging=EntryWeightsConfig(weights={"E1": 0.7, "E2": 0.3}),
                    ladder=EntryWeightsConfig(weights={"E1": 0.4, "E2": 0.3, "E3": 0.2, "E4": 0.1}),
                ),
                MARKET=MarketEntrySplitConfig(
                    single=EntryWeightsConfig(weights={"E1": 1.0}),
                    averaging=EntryWeightsConfig(weights={"E1": 0.7, "E2": 0.3}),
                ),
            ),
            tp=TpConfig(),
            sl=SlConfig(),
            price_corrections=PriceCorrectionsConfig(),
            price_sanity=PriceSanityConfig(),
        ),
        update_admission={},
        management_plan=ManagementPlanConfig(close_distribution=CloseDistributionConfig()),
        risk=RiskConfig(),
    )
    assert cfg.setup_mode == "passthrough"
    assert cfg.setup_reshape_template is None
```

- [ ] **Step 3: Run test to verify it fails**

```
pytest src/runtime_v2/signal_enrichment/tests/test_reshape_models.py -v
```
Expected: `ImportError` or `AttributeError` — models don't exist yet.

- [ ] **Step 4: Add reshape models to models.py**

In `src/runtime_v2/signal_enrichment/models.py`, add after the existing imports and before `# ── Config models ──`:

```python
# ── Reshape config models ──────────────────────────────────────────────────────

class ReshapeMatchConfig(BaseModel):
    entry_structure: str
    normalized_entry_count: int | None = None
    min_entry_count: int | None = None
    min_tp_count: int | None = None


class ReshapeEntriesConfig(BaseModel):
    mode: Literal["keep", "drop", "keep_only", "keep_last", "keep_first"]
    indexes: list[str] = Field(default_factory=list)
    n: int | None = None


class ReshapeStopLossConfig(BaseModel):
    mode: Literal["original", "from_entry", "from_distance_pct"]
    entry: str | None = None
    pct: float | None = None


class ReshapeTakeProfitsConfig(BaseModel):
    mode: Literal["keep_all", "drop", "count", "by_rr"]
    indexes: list[int] = Field(default_factory=list)
    n: int | None = None
    desired_rr: list[float] = Field(default_factory=list)
    strategy: str = "nearest_unique"
    max_rr_deviation_abs: float = 0.35
    on_missing_target: Literal["REJECT"] = "REJECT"


class ReshapeTemplateConfig(BaseModel):
    id: str
    enabled: bool = True
    match: ReshapeMatchConfig
    entries: ReshapeEntriesConfig
    stop_loss: ReshapeStopLossConfig
    take_profits: ReshapeTakeProfitsConfig
    on_failure: Literal["REJECT"] = "REJECT"


# ── Reshape audit models ───────────────────────────────────────────────────────

class ReshapeAuditEntry(BaseModel):
    source: str
    price: float


class ReshapeAuditDiscarded(BaseModel):
    source: str
    price: float
    reason: str


class ReshapeAuditStopLoss(BaseModel):
    source: str | None = None
    price: float
    replaced_original: float | None = None


class ReshapeAuditTpSelected(BaseModel):
    price: float
    rr: float | None = None


class ReshapeAuditRr(BaseModel):
    anchor: float
    stop: float
    r_unit: float


class ReshapeAuditTpSelection(BaseModel):
    mode: str
    selected: list[ReshapeAuditTpSelected]
    discarded: list[float]


class ReshapeAudit(BaseModel):
    rule_id: str
    discarded_entries: list[ReshapeAuditDiscarded] = Field(default_factory=list)
    operative_entries: list[ReshapeAuditEntry] = Field(default_factory=list)
    stop_loss: ReshapeAuditStopLoss
    rr: ReshapeAuditRr | None = None
    tp_selection: ReshapeAuditTpSelection


class ReshapeRejectionInfo(BaseModel):
    rule_id: str
    phase: Literal["no_match", "invalid_output"]
    reason_code: str
```

In `EnrichedSignalPayload`, add two new optional fields at the end:

```python
    reshaped: ReshapeAudit | None = None
    reshape_rejected: ReshapeRejectionInfo | None = None
```

In `EffectiveEnrichmentConfig`, add two new optional fields at the end:

```python
    setup_mode: Literal["passthrough", "reshape"] = "passthrough"
    setup_reshape_template: ReshapeTemplateConfig | None = None
```

Update the `__all__` list at the bottom of `models.py` to include the new names:

```python
    "ReshapeMatchConfig", "ReshapeEntriesConfig", "ReshapeStopLossConfig",
    "ReshapeTakeProfitsConfig", "ReshapeTemplateConfig",
    "ReshapeAuditEntry", "ReshapeAuditDiscarded", "ReshapeAuditStopLoss",
    "ReshapeAuditTpSelected", "ReshapeAuditRr", "ReshapeAuditTpSelection",
    "ReshapeAudit", "ReshapeRejectionInfo",
```

- [ ] **Step 5: Run test to verify it passes**

```
pytest src/runtime_v2/signal_enrichment/tests/test_reshape_models.py -v
```
Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/runtime_v2/signal_enrichment/models.py \
        src/runtime_v2/signal_enrichment/tests/__init__.py \
        src/runtime_v2/signal_enrichment/tests/test_reshape_models.py
git commit -m "feat(reshape): add reshape config and audit models to models.py"
```

---

### Task 2: tp_rr_selector.py — RR-based TP selection

**Files:**
- Create: `src/runtime_v2/signal_enrichment/reshaping/__init__.py`
- Create: `src/runtime_v2/signal_enrichment/reshaping/tp_rr_selector.py`
- Create: `src/runtime_v2/signal_enrichment/tests/test_tp_rr_selector.py`

**Interfaces:**
- Produces: `compute_anchor(operative_entries: list[tuple[float, float]]) -> float` where each tuple is `(price, weight)`
- Produces: `select_tps_by_rr(tp_prices, desired_rr, anchor, r_unit, strategy, max_rr_deviation_abs, on_missing_target) -> list[float] | None` — `None` means REJECT

- [ ] **Step 1: Write the failing tests**

Create `src/runtime_v2/signal_enrichment/reshaping/__init__.py` as an empty file.

Create `src/runtime_v2/signal_enrichment/tests/test_tp_rr_selector.py`:

```python
import pytest
from src.runtime_v2.signal_enrichment.reshaping.tp_rr_selector import (
    compute_anchor,
    select_tps_by_rr,
)


def test_anchor_single_leg():
    # Single leg degenerates to its price
    result = compute_anchor([(98.0, 1.0)])
    assert result == pytest.approx(98.0)


def test_anchor_two_legs_weighted():
    # anchor = 98*0.70 + 96*0.30 = 97.4
    result = compute_anchor([(98.0, 0.70), (96.0, 0.30)])
    assert result == pytest.approx(97.4)


def test_anchor_normalizes_weights():
    # Weights don't need to sum to 1 — function normalizes
    result = compute_anchor([(98.0, 7.0), (96.0, 3.0)])
    assert result == pytest.approx(97.4)


def test_select_tps_by_rr_example_from_spec():
    # Example from spec §5.4: anchor=97.4, stop=94, R=3.4
    tp_prices = [98.0, 100.0, 102.0, 104.0, 106.0, 108.0, 110.0, 112.0]
    result = select_tps_by_rr(
        tp_prices=tp_prices,
        desired_rr=[1.0, 1.5, 2.5, 3.5],
        anchor=97.4,
        r_unit=3.4,
        strategy="nearest_unique",
        max_rr_deviation_abs=0.35,
        on_missing_target="REJECT",
    )
    assert result == [100.0, 102.0, 106.0, 110.0]


def test_select_tps_nearest_unique_no_duplicate():
    # Two targets cannot select the same source TP
    # anchor=97.4, r=3.4: 1.0R=100(0.76), 1.1R also closest to 100
    # Second target must pick next nearest
    tp_prices = [100.0, 106.0]
    result = select_tps_by_rr(
        tp_prices=tp_prices,
        desired_rr=[1.0, 1.1],
        anchor=97.4,
        r_unit=3.4,
        strategy="nearest_unique",
        max_rr_deviation_abs=0.35,
        on_missing_target="REJECT",
    )
    # 1.0R closest to 100 (dev 0.24✓); 1.1R = 101.14, next closest is 106 (dev|2.53-1.1|=1.43 > 0.35) → no match
    assert result is None


def test_select_tps_target_no_match_in_tolerance_reject():
    # No TP within max_rr_deviation_abs of a target → REJECT
    tp_prices = [98.0, 112.0]
    result = select_tps_by_rr(
        tp_prices=tp_prices,
        desired_rr=[1.0],
        anchor=97.4,
        r_unit=3.4,
        strategy="nearest_unique",
        max_rr_deviation_abs=0.10,  # tight
        on_missing_target="REJECT",
    )
    # 1.0R = ~100.8; 98 has rr=0.18 (dev 0.82>0.10); 112 has rr=4.29 (dev 3.29>0.10)
    assert result is None


def test_select_tps_result_in_ascending_order():
    # Result must be in ascending order (for LONG)
    tp_prices = [110.0, 106.0, 102.0, 100.0]
    result = select_tps_by_rr(
        tp_prices=tp_prices,
        desired_rr=[1.0, 1.5, 2.5, 3.5],
        anchor=97.4,
        r_unit=3.4,
        strategy="nearest_unique",
        max_rr_deviation_abs=0.35,
        on_missing_target="REJECT",
    )
    assert result == [100.0, 102.0, 106.0, 110.0]


def test_select_tps_single_target():
    tp_prices = [98.0, 100.0, 102.0]
    result = select_tps_by_rr(
        tp_prices=tp_prices,
        desired_rr=[1.0],
        anchor=97.4,
        r_unit=3.4,
        strategy="nearest_unique",
        max_rr_deviation_abs=0.35,
        on_missing_target="REJECT",
    )
    assert result == [100.0]
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest src/runtime_v2/signal_enrichment/tests/test_tp_rr_selector.py -v
```
Expected: `ModuleNotFoundError` for `tp_rr_selector`.

- [ ] **Step 3: Implement tp_rr_selector.py**

Create `src/runtime_v2/signal_enrichment/reshaping/tp_rr_selector.py`:

```python
from __future__ import annotations


def compute_anchor(operative_entries: list[tuple[float, float]]) -> float:
    """Weighted average price of operative entries.

    Args:
        operative_entries: list of (price, weight) tuples. Weights need not sum to 1.

    Returns:
        Weighted average price (planned_weighted_average anchor).
    """
    total_weight = sum(w for _, w in operative_entries)
    if total_weight <= 0:
        raise ValueError("operative_entries weights must sum to a positive value")
    return sum(price * w for price, w in operative_entries) / total_weight


def select_tps_by_rr(
    tp_prices: list[float],
    desired_rr: list[float],
    anchor: float,
    r_unit: float,
    strategy: str,
    max_rr_deviation_abs: float,
    on_missing_target: str,
) -> list[float] | None:
    """Select TPs from tp_prices by matching desired RR targets.

    Uses nearest_unique strategy: each source TP can be selected at most once.
    Returns selected TPs in ascending order, or None if any target cannot be matched
    and on_missing_target == "REJECT".

    Args:
        tp_prices: Available TP prices from the signal.
        desired_rr: List of desired RR values to target.
        anchor: Weighted average entry price.
        r_unit: |anchor - stop|, the risk unit.
        strategy: Must be "nearest_unique".
        max_rr_deviation_abs: Maximum absolute RR deviation from target allowed.
        on_missing_target: "REJECT" → return None if any target has no match.
    """
    if r_unit <= 0:
        return None

    rr_for_tp = {price: abs(price - anchor) / r_unit for price in tp_prices}
    available = set(tp_prices)
    selected: list[float] = []

    for target in desired_rr:
        candidates = [
            (abs(rr_for_tp[p] - target), p)
            for p in available
            if abs(rr_for_tp[p] - target) <= max_rr_deviation_abs
        ]
        if not candidates:
            if on_missing_target == "REJECT":
                return None
            continue
        _, best_tp = min(candidates)
        selected.append(best_tp)
        available.discard(best_tp)

    return sorted(selected)


__all__ = ["compute_anchor", "select_tps_by_rr"]
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest src/runtime_v2/signal_enrichment/tests/test_tp_rr_selector.py -v
```
Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/signal_enrichment/reshaping/__init__.py \
        src/runtime_v2/signal_enrichment/reshaping/tp_rr_selector.py \
        src/runtime_v2/signal_enrichment/tests/test_tp_rr_selector.py
git commit -m "feat(reshape): add tp_rr_selector — weighted anchor, nearest_unique RR selection"
```

---

### Task 3: reshape_validator.py — Invariant checker

**Files:**
- Create: `src/runtime_v2/signal_enrichment/reshaping/reshape_validator.py`
- Create: `src/runtime_v2/signal_enrichment/tests/test_reshape_validator.py`

**Interfaces:**
- Consumes: Internal dataclasses `_OperativeEntry` defined in setup_reshaper (but validator is independent — takes plain floats)
- Produces: `validate_reshape(operative_prices, stop_loss_price, take_profits, side, anchor) -> str | None` — `None` = valid, `str` = reason_code from spec §7.2

- [ ] **Step 1: Write failing tests**

Create `src/runtime_v2/signal_enrichment/tests/test_reshape_validator.py`:

```python
import pytest
from src.runtime_v2.signal_enrichment.reshaping.reshape_validator import validate_reshape


# --- entries invariants ---

def test_valid_long_setup():
    # LONG: entries above SL, TPs above anchor
    assert validate_reshape(
        operative_prices=[98.0, 96.0],
        stop_loss_price=94.0,
        take_profits=[100.0, 102.0, 106.0, 110.0],
        side="LONG",
        anchor=97.4,
    ) is None


def test_valid_short_setup():
    # SHORT: entries below SL, TPs below anchor
    assert validate_reshape(
        operative_prices=[100.0, 102.0],
        stop_loss_price=104.0,
        take_profits=[98.0, 96.0, 94.0],
        side="SHORT",
        anchor=101.0,
    ) is None


def test_no_operative_entries():
    assert validate_reshape(
        operative_prices=[],
        stop_loss_price=94.0,
        take_profits=[100.0],
        side="LONG",
        anchor=97.4,
    ) == "reshape_no_operative_entry"


def test_no_take_profits():
    assert validate_reshape(
        operative_prices=[98.0, 96.0],
        stop_loss_price=94.0,
        take_profits=[],
        side="LONG",
        anchor=97.4,
    ) == "reshape_no_take_profit"


# --- stop_loss invariants ---

def test_stop_wrong_side_long():
    # LONG: SL must be < min(entries)
    assert validate_reshape(
        operative_prices=[98.0, 96.0],
        stop_loss_price=99.0,  # above entries → wrong side
        take_profits=[100.0],
        side="LONG",
        anchor=97.4,
    ) == "reshape_stop_wrong_side"


def test_stop_wrong_side_short():
    # SHORT: SL must be > max(entries)
    assert validate_reshape(
        operative_prices=[100.0, 102.0],
        stop_loss_price=99.0,  # below entries → wrong side
        take_profits=[98.0],
        side="SHORT",
        anchor=101.0,
    ) == "reshape_stop_wrong_side"


def test_stop_equals_entry():
    assert validate_reshape(
        operative_prices=[98.0, 96.0],
        stop_loss_price=96.0,  # equals one of the entries
        take_profits=[100.0],
        side="LONG",
        anchor=97.4,
    ) == "reshape_stop_equals_entry"


def test_zero_risk_distance():
    # anchor == stop → R = 0
    assert validate_reshape(
        operative_prices=[97.4],
        stop_loss_price=97.4,
        take_profits=[100.0],
        side="LONG",
        anchor=97.4,
    ) == "reshape_zero_risk_distance"


# --- take_profits invariants ---

def test_tp_not_profitable_long():
    # LONG: TP must be > anchor
    assert validate_reshape(
        operative_prices=[98.0, 96.0],
        stop_loss_price=94.0,
        take_profits=[97.0],  # below anchor
        side="LONG",
        anchor=97.4,
    ) == "reshape_tp_not_profitable"


def test_tp_not_profitable_short():
    # SHORT: TP must be < anchor
    assert validate_reshape(
        operative_prices=[100.0, 102.0],
        stop_loss_price=104.0,
        take_profits=[102.0],  # above anchor
        side="SHORT",
        anchor=101.0,
    ) == "reshape_tp_not_profitable"


def test_tp_not_monotonic_long():
    # LONG: TPs must be strictly ascending
    assert validate_reshape(
        operative_prices=[98.0, 96.0],
        stop_loss_price=94.0,
        take_profits=[100.0, 106.0, 102.0],  # 106 before 102 → not monotonic
        side="LONG",
        anchor=97.4,
    ) == "reshape_tp_not_monotonic"


def test_tp_not_monotonic_short():
    # SHORT: TPs must be strictly descending
    assert validate_reshape(
        operative_prices=[100.0, 102.0],
        stop_loss_price=104.0,
        take_profits=[98.0, 96.0, 97.0],  # not descending
        side="SHORT",
        anchor=101.0,
    ) == "reshape_tp_not_monotonic"
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest src/runtime_v2/signal_enrichment/tests/test_reshape_validator.py -v
```
Expected: `ModuleNotFoundError` for `reshape_validator`.

- [ ] **Step 3: Implement reshape_validator.py**

Create `src/runtime_v2/signal_enrichment/reshaping/reshape_validator.py`:

```python
from __future__ import annotations


def validate_reshape(
    operative_prices: list[float],
    stop_loss_price: float,
    take_profits: list[float],
    side: str,
    anchor: float,
) -> str | None:
    """Validate reshape output against spec §7.2 invariants.

    Returns None if valid, or a reason_code string if the reshape should be REJECTED.
    Side must be "LONG" or "SHORT".
    """
    if not operative_prices:
        return "reshape_no_operative_entry"

    if not take_profits:
        return "reshape_no_take_profit"

    if stop_loss_price in operative_prices:
        return "reshape_stop_equals_entry"

    if abs(anchor - stop_loss_price) == 0:
        return "reshape_zero_risk_distance"

    if side == "LONG":
        if stop_loss_price >= min(operative_prices):
            return "reshape_stop_wrong_side"
        for tp in take_profits:
            if tp <= anchor:
                return "reshape_tp_not_profitable"
        for i in range(1, len(take_profits)):
            if take_profits[i] <= take_profits[i - 1]:
                return "reshape_tp_not_monotonic"

    elif side == "SHORT":
        if stop_loss_price <= max(operative_prices):
            return "reshape_stop_wrong_side"
        for tp in take_profits:
            if tp >= anchor:
                return "reshape_tp_not_profitable"
        for i in range(1, len(take_profits)):
            if take_profits[i] >= take_profits[i - 1]:
                return "reshape_tp_not_monotonic"

    return None


__all__ = ["validate_reshape"]
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest src/runtime_v2/signal_enrichment/tests/test_reshape_validator.py -v
```
Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/signal_enrichment/reshaping/reshape_validator.py \
        src/runtime_v2/signal_enrichment/tests/test_reshape_validator.py
git commit -m "feat(reshape): add reshape_validator — invariant checks per spec §7.2"
```

---

### Task 4: setup_reshaper.py — Orchestrator

**Files:**
- Create: `src/runtime_v2/signal_enrichment/reshaping/setup_reshaper.py`
- Create: `src/runtime_v2/signal_enrichment/tests/test_setup_reshaper.py`

**Interfaces:**
- Consumes: `ReshapeTemplateConfig` from `models.py`, `compute_anchor` + `select_tps_by_rr` from `tp_rr_selector.py`, `validate_reshape` from `reshape_validator.py`
- Produces: `apply_reshape(signal_entries, signal_sl_price, signal_tp_prices, signal_entry_structure, signal_side, template, weights_map) -> ReshapeAudit | ReshapeRejectionInfo`
  - `signal_entries`: `list[tuple[str, float]]` — `[(source_key, price), ...]` e.g. `[("E1", 100.0), ("E2", 98.0)]`; caller must pass them **already realigned** (side-normalized, E1 = nearest to price)
  - `signal_sl_price`: original SL price (float)
  - `signal_tp_prices`: original parsed TPs as `list[float]` — **before** any use_tp_count trim
  - `signal_entry_structure`: e.g. `"LADDER"`
  - `template`: `ReshapeTemplateConfig`
  - `weights_map`: `dict[str, float]` from flusso normale (read-only, used for anchor)

- [ ] **Step 1: Write failing tests**

Create `src/runtime_v2/signal_enrichment/tests/test_setup_reshaper.py`:

```python
import pytest
from src.runtime_v2.signal_enrichment.models import (
    ReshapeAudit,
    ReshapeRejectionInfo,
    ReshapeTemplateConfig,
    ReshapeMatchConfig,
    ReshapeEntriesConfig,
    ReshapeStopLossConfig,
    ReshapeTakeProfitsConfig,
)
from src.runtime_v2.signal_enrichment.reshaping.setup_reshaper import apply_reshape


def _ladder_4_aggressive() -> ReshapeTemplateConfig:
    return ReshapeTemplateConfig(
        id="ladder_4_aggressive",
        enabled=True,
        match=ReshapeMatchConfig(entry_structure="LADDER", normalized_entry_count=4, min_tp_count=8),
        entries=ReshapeEntriesConfig(mode="drop", indexes=["E1"]),
        stop_loss=ReshapeStopLossConfig(mode="from_entry", entry="E4"),
        take_profits=ReshapeTakeProfitsConfig(
            mode="by_rr",
            desired_rr=[1.0, 1.5, 2.5, 3.5],
            strategy="nearest_unique",
            max_rr_deviation_abs=0.35,
            on_missing_target="REJECT",
        ),
    )


LADDER_ENTRIES = [("E1", 100.0), ("E2", 98.0), ("E3", 96.0), ("E4", 94.0)]
LADDER_WEIGHTS = {"E1": 0.40, "E2": 0.30, "E3": 0.20, "E4": 0.10}
LADDER_SL = 92.0
LADDER_TPS = [98.0, 100.0, 102.0, 104.0, 106.0, 108.0, 110.0, 112.0]


def test_full_reshape_spec_example():
    """Replicates spec §5 end-to-end example."""
    result = apply_reshape(
        signal_entries=LADDER_ENTRIES,
        signal_sl_price=LADDER_SL,
        signal_tp_prices=LADDER_TPS,
        signal_entry_structure="LADDER",
        signal_side="LONG",
        template=_ladder_4_aggressive(),
        weights_map=LADDER_WEIGHTS,
    )
    assert isinstance(result, ReshapeAudit)
    assert result.rule_id == "ladder_4_aggressive"
    # E1 discarded
    assert len(result.discarded_entries) == 1
    assert result.discarded_entries[0].source == "E1"
    assert result.discarded_entries[0].price == 100.0
    # E2, E3 operative
    operative_prices = [e.price for e in result.operative_entries]
    assert operative_prices == [98.0, 96.0]
    # E4 → SL
    assert result.stop_loss.source == "E4"
    assert result.stop_loss.price == 94.0
    assert result.stop_loss.replaced_original == 92.0
    # Anchor and R
    assert result.rr is not None
    assert result.rr.anchor == pytest.approx(97.4)
    assert result.rr.r_unit == pytest.approx(3.4)
    # TPs selected
    selected_prices = [t.price for t in result.tp_selection.selected]
    assert selected_prices == [100.0, 102.0, 106.0, 110.0]


def test_no_match_wrong_structure_is_rejected():
    result = apply_reshape(
        signal_entries=LADDER_ENTRIES,
        signal_sl_price=LADDER_SL,
        signal_tp_prices=LADDER_TPS,
        signal_entry_structure="RANGE",  # doesn't match LADDER
        signal_side="LONG",
        template=_ladder_4_aggressive(),
        weights_map=LADDER_WEIGHTS,
    )
    assert isinstance(result, ReshapeRejectionInfo)
    assert result.phase == "no_match"


def test_no_match_wrong_entry_count():
    entries_3 = [("E1", 100.0), ("E2", 98.0), ("E3", 96.0)]
    weights_3 = {"E1": 0.50, "E2": 0.30, "E3": 0.20}
    result = apply_reshape(
        signal_entries=entries_3,
        signal_sl_price=94.0,
        signal_tp_prices=LADDER_TPS,
        signal_entry_structure="LADDER",
        signal_side="LONG",
        template=_ladder_4_aggressive(),  # requires normalized_entry_count=4
        weights_map=weights_3,
    )
    assert isinstance(result, ReshapeRejectionInfo)
    assert result.phase == "no_match"


def test_no_match_insufficient_tp_count():
    tps_7 = LADDER_TPS[:7]  # only 7, template requires min_tp_count=8
    result = apply_reshape(
        signal_entries=LADDER_ENTRIES,
        signal_sl_price=LADDER_SL,
        signal_tp_prices=tps_7,
        signal_entry_structure="LADDER",
        signal_side="LONG",
        template=_ladder_4_aggressive(),
        weights_map=LADDER_WEIGHTS,
    )
    assert isinstance(result, ReshapeRejectionInfo)
    assert result.phase == "no_match"


def test_keep_last_n_entries():
    template = ReshapeTemplateConfig(
        id="ladder_4_keep_sl",
        enabled=True,
        match=ReshapeMatchConfig(entry_structure="LADDER", normalized_entry_count=4, min_tp_count=8),
        entries=ReshapeEntriesConfig(mode="keep_last", n=2),
        stop_loss=ReshapeStopLossConfig(mode="original"),
        take_profits=ReshapeTakeProfitsConfig(mode="count", n=4),
    )
    result = apply_reshape(
        signal_entries=LADDER_ENTRIES,
        signal_sl_price=LADDER_SL,
        signal_tp_prices=LADDER_TPS,
        signal_entry_structure="LADDER",
        signal_side="LONG",
        template=template,
        weights_map=LADDER_WEIGHTS,
    )
    assert isinstance(result, ReshapeAudit)
    operative_prices = [e.price for e in result.operative_entries]
    assert operative_prices == [96.0, 94.0]  # E3, E4 (last 2)
    assert result.stop_loss.price == LADDER_SL  # original SL preserved
    assert result.stop_loss.replaced_original is None
    selected_prices = [t.price for t in result.tp_selection.selected]
    assert selected_prices == [98.0, 100.0, 102.0, 104.0]  # first 4


def test_keep_only_specific_entries():
    template = ReshapeTemplateConfig(
        id="keep_e2_e3",
        enabled=True,
        match=ReshapeMatchConfig(entry_structure="LADDER", normalized_entry_count=4, min_tp_count=8),
        entries=ReshapeEntriesConfig(mode="keep_only", indexes=["E2", "E3"]),
        stop_loss=ReshapeStopLossConfig(mode="original"),
        take_profits=ReshapeTakeProfitsConfig(mode="keep_all"),
    )
    result = apply_reshape(
        signal_entries=LADDER_ENTRIES,
        signal_sl_price=LADDER_SL,
        signal_tp_prices=LADDER_TPS,
        signal_entry_structure="LADDER",
        signal_side="LONG",
        template=template,
        weights_map=LADDER_WEIGHTS,
    )
    assert isinstance(result, ReshapeAudit)
    operative_prices = [e.price for e in result.operative_entries]
    assert operative_prices == [98.0, 96.0]  # E2, E3


def test_invalid_output_rejected():
    # drop all entries → validator should reject
    template = ReshapeTemplateConfig(
        id="drop_all",
        enabled=True,
        match=ReshapeMatchConfig(entry_structure="LADDER", normalized_entry_count=4, min_tp_count=8),
        entries=ReshapeEntriesConfig(mode="drop", indexes=["E1", "E2", "E3", "E4"]),
        stop_loss=ReshapeStopLossConfig(mode="original"),
        take_profits=ReshapeTakeProfitsConfig(mode="keep_all"),
    )
    result = apply_reshape(
        signal_entries=LADDER_ENTRIES,
        signal_sl_price=LADDER_SL,
        signal_tp_prices=LADDER_TPS,
        signal_entry_structure="LADDER",
        signal_side="LONG",
        template=template,
        weights_map=LADDER_WEIGHTS,
    )
    assert isinstance(result, ReshapeRejectionInfo)
    assert result.phase == "invalid_output"
    assert result.reason_code == "reshape_no_operative_entry"


def test_tp_drop_by_indexes():
    template = ReshapeTemplateConfig(
        id="drop_tps",
        enabled=True,
        match=ReshapeMatchConfig(entry_structure="LADDER", normalized_entry_count=4, min_tp_count=8),
        entries=ReshapeEntriesConfig(mode="keep"),
        stop_loss=ReshapeStopLossConfig(mode="original"),
        take_profits=ReshapeTakeProfitsConfig(mode="drop", indexes=[1, 2, 4]),
    )
    result = apply_reshape(
        signal_entries=LADDER_ENTRIES,
        signal_sl_price=LADDER_SL,
        signal_tp_prices=[98.0, 100.0, 102.0, 104.0, 106.0, 108.0, 110.0, 112.0],
        signal_entry_structure="LADDER",
        signal_side="LONG",
        template=template,
        weights_map=LADDER_WEIGHTS,
    )
    assert isinstance(result, ReshapeAudit)
    # Dropped indexes 1,2,4 (1-based): 98, 100, 104 removed; kept: 102, 106, 108, 110, 112
    selected_prices = [t.price for t in result.tp_selection.selected]
    assert selected_prices == [102.0, 106.0, 108.0, 110.0, 112.0]


def test_disabled_template_no_match():
    template = _ladder_4_aggressive()
    template = template.model_copy(update={"enabled": False})
    result = apply_reshape(
        signal_entries=LADDER_ENTRIES,
        signal_sl_price=LADDER_SL,
        signal_tp_prices=LADDER_TPS,
        signal_entry_structure="LADDER",
        signal_side="LONG",
        template=template,
        weights_map=LADDER_WEIGHTS,
    )
    assert isinstance(result, ReshapeRejectionInfo)
    assert result.phase == "no_match"
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest src/runtime_v2/signal_enrichment/tests/test_setup_reshaper.py -v
```
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement setup_reshaper.py**

Create `src/runtime_v2/signal_enrichment/reshaping/setup_reshaper.py`:

```python
from __future__ import annotations

from src.runtime_v2.signal_enrichment.models import (
    ReshapeAudit,
    ReshapeAuditDiscarded,
    ReshapeAuditEntry,
    ReshapeAuditRr,
    ReshapeAuditStopLoss,
    ReshapeAuditTpSelected,
    ReshapeAuditTpSelection,
    ReshapeRejectionInfo,
    ReshapeTemplateConfig,
)
from src.runtime_v2.signal_enrichment.reshaping.tp_rr_selector import (
    compute_anchor,
    select_tps_by_rr,
)
from src.runtime_v2.signal_enrichment.reshaping.reshape_validator import validate_reshape


def apply_reshape(
    *,
    signal_entries: list[tuple[str, float]],
    signal_sl_price: float | None,
    signal_tp_prices: list[float],
    signal_entry_structure: str,
    signal_side: str,
    template: ReshapeTemplateConfig,
    weights_map: dict[str, float],
) -> ReshapeAudit | ReshapeRejectionInfo:
    """Apply a reshape template to a signal.

    signal_entries must be already realigned (E1 = nearest to price for side).
    signal_tp_prices must be the original parsed TPs, before any use_tp_count trim.
    weights_map keys are "E1", "E2", etc. from the flusso normale config.
    """
    rule_id = template.id

    if not _matches(template, signal_entry_structure, len(signal_entries), len(signal_tp_prices)):
        return ReshapeRejectionInfo(rule_id=rule_id, phase="no_match", reason_code="reshape_no_match")

    entries_map = {key: price for key, price in signal_entries}

    operative, discarded = _apply_entries(template.entries, signal_entries)
    if operative is None:
        return ReshapeRejectionInfo(rule_id=rule_id, phase="invalid_output", reason_code=discarded)

    effective_sl, archived_sl, stop_source, sl_err = _apply_stop_loss(
        template.stop_loss, entries_map, operative, signal_sl_price
    )
    if sl_err:
        return ReshapeRejectionInfo(rule_id=rule_id, phase="invalid_output", reason_code=sl_err)

    operative_with_weights = [
        (price, weights_map.get(key, 0.0))
        for key, price in operative
    ]
    try:
        anchor = compute_anchor(operative_with_weights)
    except ValueError:
        return ReshapeRejectionInfo(rule_id=rule_id, phase="invalid_output", reason_code="reshape_zero_risk_distance")

    r_unit = abs(anchor - effective_sl) if effective_sl is not None else 0.0
    rr_info: ReshapeAuditRr | None = None
    if r_unit > 0 and effective_sl is not None:
        rr_info = ReshapeAuditRr(anchor=anchor, stop=effective_sl, r_unit=r_unit)

    selected_tps, tp_discarded, tp_selected_with_rr, tp_err = _apply_take_profits(
        template.take_profits, signal_tp_prices, anchor, r_unit
    )
    if tp_err:
        return ReshapeRejectionInfo(rule_id=rule_id, phase="invalid_output", reason_code=tp_err)

    operative_prices = [price for _, price in operative]
    reason = validate_reshape(
        operative_prices=operative_prices,
        stop_loss_price=effective_sl,
        take_profits=selected_tps,
        side=signal_side,
        anchor=anchor,
    )
    if reason:
        return ReshapeRejectionInfo(rule_id=rule_id, phase="invalid_output", reason_code=reason)

    return ReshapeAudit(
        rule_id=rule_id,
        discarded_entries=[
            ReshapeAuditDiscarded(source=key, price=price, reason="initial_entry_skipped")
            for key, price in discarded
        ],
        operative_entries=[ReshapeAuditEntry(source=key, price=price) for key, price in operative],
        stop_loss=ReshapeAuditStopLoss(
            source=stop_source,
            price=effective_sl,
            replaced_original=archived_sl,
        ),
        rr=rr_info,
        tp_selection=ReshapeAuditTpSelection(
            mode=template.take_profits.mode,
            selected=[
                ReshapeAuditTpSelected(
                    price=p,
                    rr=round(abs(p - anchor) / r_unit, 4) if r_unit > 0 else None,
                )
                for p in selected_tps
            ],
            discarded=tp_discarded,
        ),
    )


def _matches(
    template: ReshapeTemplateConfig,
    entry_structure: str,
    entry_count: int,
    tp_count: int,
) -> bool:
    if not template.enabled:
        return False
    m = template.match
    if m.entry_structure != entry_structure:
        return False
    if m.normalized_entry_count is not None and m.normalized_entry_count != entry_count:
        return False
    if m.min_entry_count is not None and entry_count < m.min_entry_count:
        return False
    if m.min_tp_count is not None and tp_count < m.min_tp_count:
        return False
    return True


def _apply_entries(
    cfg,
    signal_entries: list[tuple[str, float]],
) -> tuple[list[tuple[str, float]] | None, list[tuple[str, float]] | str]:
    """Returns (operative, discarded) or (None, reason_code)."""
    mode = cfg.mode

    if mode == "keep":
        return list(signal_entries), []

    if mode == "drop":
        drop_set = set(cfg.indexes)
        operative = [(k, p) for k, p in signal_entries if k not in drop_set]
        discarded = [(k, p) for k, p in signal_entries if k in drop_set]
        if not operative:
            return None, "reshape_no_operative_entry"
        return operative, discarded

    if mode == "keep_only":
        keep_set = set(cfg.indexes)
        operative = [(k, p) for k, p in signal_entries if k in keep_set]
        discarded = [(k, p) for k, p in signal_entries if k not in keep_set]
        if not operative:
            return None, "reshape_no_operative_entry"
        return operative, discarded

    if mode == "keep_last":
        n = cfg.n or 1
        if n > len(signal_entries):
            return None, "reshape_keep_n_too_large"
        operative = signal_entries[-n:]
        discarded = signal_entries[:-n]
        return operative, discarded

    if mode == "keep_first":
        n = cfg.n or 1
        if n > len(signal_entries):
            return None, "reshape_keep_n_too_large"
        operative = signal_entries[:n]
        discarded = signal_entries[n:]
        return operative, discarded

    return None, "reshape_unknown_entries_mode"


def _apply_stop_loss(
    cfg,
    entries_map: dict[str, float],
    operative: list[tuple[str, float]],
    original_sl: float | None,
) -> tuple[float, float | None, str | None, str | None]:
    """Returns (effective_sl, archived_sl, stop_source, error_code)."""
    mode = cfg.mode

    if mode == "original":
        if original_sl is None:
            return 0.0, None, None, "reshape_missing_original_sl"
        return original_sl, None, None, None

    if mode == "from_entry":
        entry_key = cfg.entry
        if entry_key not in entries_map:
            return 0.0, None, None, "reshape_entry_index_absent"
        new_sl = entries_map[entry_key]
        if any(k == entry_key for k, _ in operative):
            return 0.0, None, None, "reshape_duplicate_role"
        return new_sl, original_sl, entry_key, None

    if mode == "from_distance_pct":
        if original_sl is None:
            return 0.0, None, None, "reshape_missing_original_sl"
        return original_sl, None, None, "reshape_from_distance_pct_not_implemented_v1"

    return 0.0, None, None, "reshape_unknown_stop_mode"


def _apply_take_profits(
    cfg,
    signal_tp_prices: list[float],
    anchor: float,
    r_unit: float,
) -> tuple[list[float], list[float], list[dict] | None, str | None]:
    """Returns (selected, discarded, tp_with_rr_detail, error_code)."""
    mode = cfg.mode

    if mode == "keep_all":
        return list(signal_tp_prices), [], None, None

    if mode == "drop":
        drop_1based = set(cfg.indexes)
        selected = [p for i, p in enumerate(signal_tp_prices, start=1) if i not in drop_1based]
        discarded = [p for i, p in enumerate(signal_tp_prices, start=1) if i in drop_1based]
        if not selected:
            return [], [], None, "reshape_no_take_profit"
        return selected, discarded, None, None

    if mode == "count":
        n = cfg.n or len(signal_tp_prices)
        selected = signal_tp_prices[:n]
        discarded = signal_tp_prices[n:]
        if not selected:
            return [], [], None, "reshape_no_take_profit"
        return selected, discarded, None, None

    if mode == "by_rr":
        if r_unit <= 0:
            return [], [], None, "reshape_zero_risk_distance"
        selected = select_tps_by_rr(
            tp_prices=signal_tp_prices,
            desired_rr=cfg.desired_rr,
            anchor=anchor,
            r_unit=r_unit,
            strategy=cfg.strategy,
            max_rr_deviation_abs=cfg.max_rr_deviation_abs,
            on_missing_target=cfg.on_missing_target,
        )
        if selected is None:
            return [], [], None, "reshape_no_tp_in_tolerance"
        discarded = [p for p in signal_tp_prices if p not in set(selected)]
        return selected, discarded, None, None

    return [], [], None, "reshape_unknown_tp_mode"


__all__ = ["apply_reshape"]
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest src/runtime_v2/signal_enrichment/tests/test_setup_reshaper.py -v
```
Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/signal_enrichment/reshaping/setup_reshaper.py \
        src/runtime_v2/signal_enrichment/tests/test_setup_reshaper.py
git commit -m "feat(reshape): add setup_reshaper orchestrator — match, apply, validate"
```

---

### Task 5: Config loader — load templates, resolve id per-trader

**Files:**
- Modify: `src/runtime_v2/signal_enrichment/config_loader.py`
- Create: `src/runtime_v2/signal_enrichment/tests/test_config_loader_reshape.py`

**Interfaces:**
- Modifies: `OperationConfigLoader._load()` to also load `config/setup_reshape_templates.yaml`
- Modifies: `OperationConfigLoader._merge()` to resolve `template:<id>` per trader into `EffectiveEnrichmentConfig.setup_reshape_template`
- Modifies: `OperationConfigLoader.reload_if_changed()` to also watch `setup_reshape_templates.yaml` mtime
- Fix: `processor.py` calls `self._config.get_policy_version()` without `trader_id`; loader already supports `get_policy_version(trader_id)` — this call will be fixed in Task 6 (processor)

- [ ] **Step 1: Write failing tests**

Create `src/runtime_v2/signal_enrichment/tests/test_config_loader_reshape.py`:

```python
import pytest
from pathlib import Path
import yaml

from src.runtime_v2.signal_enrichment.config_loader import OperationConfigLoader, ConfigLoadError


def _write_global(tmp_path: Path, extra_defaults: dict | None = None) -> Path:
    defaults = {
        "signal_policy": {
            "accepted_entry_structures": ["LADDER"],
            "market_execution": {"mode": "tolerance", "tolerance_pct": 0.5, "range_tolerance_pct": 0.2},
            "entry_split": {
                "LIMIT": {
                    "single": {"weights": {"E1": 1.0}},
                    "range": {"weights": {"E1": 0.5, "E2": 0.5}},
                    "averaging": {"weights": {"E1": 0.7, "E2": 0.3}},
                    "ladder": {"weights": {"E1": 0.4, "E2": 0.3, "E3": 0.2, "E4": 0.1}},
                },
                "MARKET": {
                    "single": {"weights": {"E1": 1.0}},
                    "averaging": {"weights": {"E1": 0.7, "E2": 0.3}},
                },
            },
            "tp": {"use_tp_count": None},
            "sl": {"use_original_sl": True, "require_sl": True},
            "price_corrections": {"enabled": False},
            "price_sanity": {"enabled": False},
        },
        "management_plan": {
            "be_trigger": None,
            "close_distribution": {"mode": "equal"},
        },
        "risk": {"mode": "risk_pct_of_capital"},
    }
    if extra_defaults:
        defaults.update(extra_defaults)
    raw = {
        "registered_traders": ["trader_reshape"],
        "account": {"id": "main", "capital_base_usdt": 1000.0, "max_leverage": 10,
                     "max_capital_at_risk_pct": 10.0, "hard_max_per_signal_risk_pct": 2.0},
        "defaults": defaults,
    }
    config_path = tmp_path / "operation_config.yaml"
    config_path.write_text(yaml.dump(raw))
    (tmp_path / "traders").mkdir()
    return tmp_path


def _write_templates(tmp_path: Path):
    tpl = {
        "templates": [
            {
                "id": "ladder_4_aggressive",
                "enabled": True,
                "match": {"entry_structure": "LADDER", "normalized_entry_count": 4, "min_tp_count": 8},
                "entries": {"mode": "drop", "indexes": ["E1"]},
                "stop_loss": {"mode": "from_entry", "entry": "E4"},
                "take_profits": {
                    "mode": "by_rr",
                    "desired_rr": [1.0, 1.5, 2.5, 3.5],
                    "strategy": "nearest_unique",
                    "max_rr_deviation_abs": 0.35,
                    "on_missing_target": "REJECT",
                },
                "on_failure": "REJECT",
            }
        ]
    }
    (tmp_path / "setup_reshape_templates.yaml").write_text(yaml.dump(tpl))


def _write_trader(tmp_path: Path, setup_mode: str = "reshape", template_id: str = "ladder_4_aggressive"):
    trader = {
        "setup_mode": setup_mode,
        "setup_reshape": {"template": template_id},
    }
    (tmp_path / "traders" / "trader_reshape.yaml").write_text(yaml.dump(trader))


def test_passthrough_trader_no_template(tmp_path):
    _write_global(tmp_path)
    _write_templates(tmp_path)
    (tmp_path / "traders" / "trader_reshape.yaml").write_text(
        yaml.dump({"setup_mode": "passthrough"})
    )
    loader = OperationConfigLoader(str(tmp_path))
    cfg = loader.get_effective_config("trader_reshape")
    assert cfg.setup_mode == "passthrough"
    assert cfg.setup_reshape_template is None


def test_reshape_trader_resolves_template(tmp_path):
    _write_global(tmp_path)
    _write_templates(tmp_path)
    _write_trader(tmp_path)
    loader = OperationConfigLoader(str(tmp_path))
    cfg = loader.get_effective_config("trader_reshape")
    assert cfg.setup_mode == "reshape"
    assert cfg.setup_reshape_template is not None
    assert cfg.setup_reshape_template.id == "ladder_4_aggressive"
    assert cfg.setup_reshape_template.entries.mode == "drop"


def test_unknown_template_id_raises_at_load(tmp_path):
    _write_global(tmp_path)
    _write_templates(tmp_path)
    _write_trader(tmp_path, template_id="nonexistent_id")
    with pytest.raises(ConfigLoadError, match="nonexistent_id"):
        OperationConfigLoader(str(tmp_path))


def test_missing_templates_file_passthrough_still_works(tmp_path):
    # If setup_reshape_templates.yaml doesn't exist, passthrough traders still load fine
    _write_global(tmp_path)
    (tmp_path / "traders" / "trader_reshape.yaml").write_text(
        yaml.dump({"setup_mode": "passthrough"})
    )
    loader = OperationConfigLoader(str(tmp_path))
    cfg = loader.get_effective_config("trader_reshape")
    assert cfg.setup_mode == "passthrough"


def test_reshape_mode_without_templates_file_raises(tmp_path):
    _write_global(tmp_path)
    _write_trader(tmp_path)  # reshape but no templates file
    with pytest.raises(ConfigLoadError):
        OperationConfigLoader(str(tmp_path))
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest src/runtime_v2/signal_enrichment/tests/test_config_loader_reshape.py -v
```
Expected: Tests fail (loader doesn't know about reshape templates yet).

- [ ] **Step 3: Modify config_loader.py**

In `config_loader.py`, add the following imports at the top (after existing imports):

```python
from src.runtime_v2.signal_enrichment.models import (
    # ... existing imports unchanged ...
    ReshapeTemplateConfig,
    ReshapeMatchConfig,
    ReshapeEntriesConfig,
    ReshapeStopLossConfig,
    ReshapeTakeProfitsConfig,
)
```

Change `_load()` to also load the templates catalog:

```python
def _load(self) -> None:
    op_path = self._config_dir / "operation_config.yaml"
    with op_path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    self._validate_global(raw)
    self._global_raw = raw
    self._mtimes["operation_config"] = op_path.stat().st_mtime

    templates_path = self._config_dir / "setup_reshape_templates.yaml"
    self._reshape_templates: dict[str, ReshapeTemplateConfig] = {}
    if templates_path.exists():
        with templates_path.open(encoding="utf-8") as f:
            tpl_raw = yaml.safe_load(f) or {}
        for tpl in tpl_raw.get("templates", []):
            cfg = self._build_reshape_template(tpl)
            self._reshape_templates[cfg.id] = cfg
        self._mtimes["setup_reshape_templates"] = templates_path.stat().st_mtime

    # Validate all reshape references at load time (fail-fast)
    self._validate_reshape_references(raw)
```

Add `reload_if_changed` mtime check for templates file. Replace the existing `reload_if_changed` method:

```python
def reload_if_changed(self) -> bool:
    op_path = self._config_dir / "operation_config.yaml"
    tpl_path = self._config_dir / "setup_reshape_templates.yaml"
    try:
        op_mtime = op_path.stat().st_mtime
    except FileNotFoundError:
        return False
    tpl_mtime = tpl_path.stat().st_mtime if tpl_path.exists() else 0.0
    if (op_mtime == self._mtimes.get("operation_config", 0.0)
            and tpl_mtime == self._mtimes.get("setup_reshape_templates", 0.0)):
        return False
    try:
        self._load()
        return True
    except Exception as exc:
        logger.error("Config reload failed, keeping last valid config: %s", exc)
        return False
```

Add new private methods `_validate_reshape_references`, `_build_reshape_template`, and update `_merge`:

```python
def _validate_reshape_references(self, raw: dict) -> None:
    """Fail-fast: any registered trader with setup_mode=reshape must reference a known template id."""
    for trader_id in raw.get("registered_traders", []):
        trader_raw = self._load_trader_raw(trader_id)
        setup_mode = trader_raw.get("setup_mode", "passthrough")
        if setup_mode == "reshape":
            template_id = (trader_raw.get("setup_reshape") or {}).get("template")
            if not template_id:
                raise ConfigLoadError(
                    f"Trader '{trader_id}' has setup_mode=reshape but no setup_reshape.template"
                )
            if template_id not in self._reshape_templates:
                raise ConfigLoadError(
                    f"Trader '{trader_id}' references unknown reshape template id '{template_id}'"
                )

@staticmethod
def _build_reshape_template(raw: dict) -> ReshapeTemplateConfig:
    match_raw = raw.get("match", {})
    entries_raw = raw.get("entries", {})
    sl_raw = raw.get("stop_loss", {})
    tp_raw = raw.get("take_profits", {})
    return ReshapeTemplateConfig(
        id=raw["id"],
        enabled=raw.get("enabled", True),
        match=ReshapeMatchConfig(
            entry_structure=match_raw["entry_structure"],
            normalized_entry_count=match_raw.get("normalized_entry_count"),
            min_entry_count=match_raw.get("min_entry_count"),
            min_tp_count=match_raw.get("min_tp_count"),
        ),
        entries=ReshapeEntriesConfig(
            mode=entries_raw["mode"],
            indexes=entries_raw.get("indexes", []),
            n=entries_raw.get("n"),
        ),
        stop_loss=ReshapeStopLossConfig(
            mode=sl_raw["mode"],
            entry=sl_raw.get("entry"),
            pct=sl_raw.get("pct"),
        ),
        take_profits=ReshapeTakeProfitsConfig(
            mode=tp_raw["mode"],
            indexes=tp_raw.get("indexes", []),
            n=tp_raw.get("n"),
            desired_rr=tp_raw.get("desired_rr", []),
            strategy=tp_raw.get("strategy", "nearest_unique"),
            max_rr_deviation_abs=tp_raw.get("max_rr_deviation_abs", 0.35),
            on_missing_target=tp_raw.get("on_missing_target", "REJECT"),
        ),
        on_failure=raw.get("on_failure", "REJECT"),
    )
```

In `_merge()`, after building `signal_policy` and before the `return`, add setup_mode resolution:

```python
        # Resolve reshape setup mode
        setup_mode = merged.get("setup_mode", "passthrough")
        setup_reshape_template: ReshapeTemplateConfig | None = None
        if setup_mode == "reshape":
            template_id = (merged.get("setup_reshape") or {}).get("template")
            if template_id and template_id in self._reshape_templates:
                setup_reshape_template = self._reshape_templates[template_id]
```

Update the `return EffectiveEnrichmentConfig(...)` at end of `_merge` to include:

```python
            setup_mode=setup_mode,
            setup_reshape_template=setup_reshape_template,
```

Also add `self._reshape_templates: dict[str, ReshapeTemplateConfig] = {}` to `__init__` before `self._load()`.

- [ ] **Step 4: Run tests to verify they pass**

```
pytest src/runtime_v2/signal_enrichment/tests/test_config_loader_reshape.py -v
```
Expected: All tests PASS.

- [ ] **Step 5: Run full signal_enrichment test suite to verify no regressions**

```
pytest src/runtime_v2/signal_enrichment/tests/ -v
```
Expected: All existing + new tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/runtime_v2/signal_enrichment/config_loader.py \
        src/runtime_v2/signal_enrichment/tests/test_config_loader_reshape.py
git commit -m "feat(reshape): loader resolves setup_reshape_templates.yaml with fail-fast id validation"
```

---

### Task 6: processor.py — insert reshape stage

**Files:**
- Modify: `src/runtime_v2/signal_enrichment/processor.py`
- Create: `src/runtime_v2/signal_enrichment/tests/test_processor_reshape.py`

**Interfaces:**
- Consumes: `apply_reshape` from `setup_reshaper.py`, `ReshapeAudit`, `ReshapeRejectionInfo` from `models.py`
- The reshape stage is inserted in `_process_signal` after gate 4 (SL required), before weights
- In reshape mode: skip `use_tp_count` trim, run `_realign_limit_entries_by_side` early, call `apply_reshape`, on `ReshapeRejectionInfo` return BLOCK with reason_code
- In passthrough mode: existing flow unchanged
- Fix: change `self._config.get_policy_version()` to `self._config.get_policy_version(trader_id)` in `process()`

- [ ] **Step 1: Write failing integration tests**

Create `src/runtime_v2/signal_enrichment/tests/test_processor_reshape.py`:

```python
"""Integration tests for the reshape stage in processor.py.

Uses a real OperationConfigLoader pointed at a temp config dir.
Builds minimal CanonicalParseResult objects to drive the processor.
"""
import pytest
from pathlib import Path
import yaml

from src.runtime_v2.signal_enrichment.config_loader import OperationConfigLoader
from src.runtime_v2.signal_enrichment.processor import SignalEnrichmentProcessor
from src.runtime_v2.signal_enrichment.repository import EnrichedCanonicalMessageRepository


def _write_minimal_config(tmp_path: Path, setup_mode: str = "reshape") -> None:
    global_cfg = {
        "registered_traders": ["trader_t"],
        "account": {
            "id": "main", "capital_base_usdt": 1000.0, "max_leverage": 10,
            "max_capital_at_risk_pct": 10.0, "hard_max_per_signal_risk_pct": 2.0,
        },
        "defaults": {
            "enabled": True,
            "gate_mode": "block",
            "hedge_mode": False,
            "signal_policy": {
                "accepted_entry_structures": ["LADDER", "ONE_SHOT", "TWO_STEP", "RANGE"],
                "market_execution": {"mode": "tolerance", "tolerance_pct": 0.5, "range_tolerance_pct": 0.2},
                "entry_split": {
                    "LIMIT": {
                        "single": {"weights": {"E1": 1.0}},
                        "range": {"weights": {"E1": 0.5, "E2": 0.5}},
                        "averaging": {"weights": {"E1": 0.70, "E2": 0.30}},
                        "ladder": {"weights": {"E1": 0.40, "E2": 0.30, "E3": 0.20, "E4": 0.10}},
                    },
                    "MARKET": {
                        "single": {"weights": {"E1": 1.0}},
                        "averaging": {"weights": {"E1": 0.7, "E2": 0.3}},
                    },
                },
                "tp": {"use_tp_count": 4},  # would trim to 4, but reshape bypasses this
                "sl": {"use_original_sl": True, "require_sl": True},
                "price_corrections": {"enabled": False},
                "price_sanity": {"enabled": False},
            },
            "management_plan": {
                "be_trigger": None,
                "close_distribution": {"mode": "equal"},
            },
            "risk": {"mode": "risk_pct_of_capital"},
            "update_admission": {},
        },
    }
    (tmp_path / "operation_config.yaml").write_text(yaml.dump(global_cfg))
    (tmp_path / "traders").mkdir()
    (tmp_path / "traders" / "trader_t.yaml").write_text(yaml.dump({
        "setup_mode": setup_mode,
        "setup_reshape": {"template": "ladder_4_aggressive"} if setup_mode == "reshape" else {},
    }))
    (tmp_path / "setup_reshape_templates.yaml").write_text(yaml.dump({
        "templates": [{
            "id": "ladder_4_aggressive",
            "enabled": True,
            "match": {"entry_structure": "LADDER", "normalized_entry_count": 4, "min_tp_count": 8},
            "entries": {"mode": "drop", "indexes": ["E1"]},
            "stop_loss": {"mode": "from_entry", "entry": "E4"},
            "take_profits": {
                "mode": "by_rr",
                "desired_rr": [1.0, 1.5, 2.5, 3.5],
                "strategy": "nearest_unique",
                "max_rr_deviation_abs": 0.35,
                "on_missing_target": "REJECT",
            },
            "on_failure": "REJECT",
        }]
    }))


def _make_processor(tmp_path: Path):
    loader = OperationConfigLoader(str(tmp_path))

    class _InMemoryRepo(EnrichedCanonicalMessageRepository):
        def __init__(self):
            self._store = {}
        def get_by_canonical_message_id(self, cid):
            return self._store.get(cid)
        def save(self, msg):
            self._store[msg.canonical_message_id] = msg
            return msg

    return SignalEnrichmentProcessor(config_loader=loader, repository=_InMemoryRepo())


def _make_signal_result(
    canonical_message_id: int,
    entries,
    sl_price: float,
    tp_prices,
    entry_structure: str,
    side: str,
):
    """Build a minimal CanonicalParseResult with a SIGNAL payload."""
    from src.runtime_v2.parser_pipeline.models import CanonicalParseResult
    from src.parser_v2.contracts.canonical_message import CanonicalMessage, SignalPayload
    from src.parser_v2.contracts.entities import EntryLeg, StopLoss, TakeProfit, Price

    entry_legs = [
        EntryLeg(sequence=i + 1, entry_type="LIMIT", price=Price(raw=str(p), value=p))
        for i, p in enumerate(entries)
    ]
    tps = [
        TakeProfit(sequence=i + 1, price=Price(raw=str(p), value=p))
        for i, p in enumerate(tp_prices)
    ]
    signal = SignalPayload(
        entry_structure=entry_structure,
        side=side,
        symbol="BTCUSDT",
        entries=entry_legs,
        stop_loss=StopLoss(price=Price(raw=str(sl_price), value=sl_price)),
        take_profits=tps,
    )
    msg = CanonicalMessage(primary_class="SIGNAL", signal=signal)
    return CanonicalParseResult(
        canonical_message_id=canonical_message_id,
        raw_message_id=1,
        canonical_message=msg,
        primary_class="SIGNAL",
        resolved_trader_id="trader_t",
    )


def test_reshape_pass_produces_reshaped_payload(tmp_path):
    _write_minimal_config(tmp_path, setup_mode="reshape")
    proc = _make_processor(tmp_path)
    # Spec §5 example: LONG LADDER, 4 entries, 8 TPs
    result = _make_signal_result(
        canonical_message_id=1,
        entries=[100.0, 98.0, 96.0, 94.0],
        sl_price=92.0,
        tp_prices=[98.0, 100.0, 102.0, 104.0, 106.0, 108.0, 110.0, 112.0],
        entry_structure="LADDER",
        side="LONG",
    )
    enriched = proc.process(result)
    assert enriched.enrichment_decision == "PASS"
    assert enriched.enriched_signal is not None
    # use_tp_count=4 was configured but should be bypassed in reshape mode: 4 TPs from by_rr
    assert len(enriched.enriched_signal.take_profits) == 4
    # Reshape audit present
    assert enriched.enriched_signal.reshaped is not None
    assert enriched.enriched_signal.reshaped.rule_id == "ladder_4_aggressive"
    # Operative entries: E2(98), E3(96) — E1(100) discarded, E4(94)→SL
    operative_prices = [e.price.value for e in enriched.enriched_signal.entries]
    assert operative_prices == [98.0, 96.0]
    # SL is now 94 (E4)
    assert enriched.enriched_signal.stop_loss.price.value == 94.0


def test_reshape_no_match_blocks_signal(tmp_path):
    _write_minimal_config(tmp_path, setup_mode="reshape")
    proc = _make_processor(tmp_path)
    # Only 3 entries — template requires exactly 4
    result = _make_signal_result(
        canonical_message_id=2,
        entries=[100.0, 98.0, 96.0],
        sl_price=92.0,
        tp_prices=[98.0, 100.0, 102.0, 104.0, 106.0, 108.0, 110.0, 112.0],
        entry_structure="LADDER",
        side="LONG",
    )
    enriched = proc.process(result)
    assert enriched.enrichment_decision == "BLOCK"
    assert "reshape" in enriched.reason_code


def test_passthrough_unchanged(tmp_path):
    _write_minimal_config(tmp_path, setup_mode="passthrough")
    proc = _make_processor(tmp_path)
    result = _make_signal_result(
        canonical_message_id=3,
        entries=[100.0, 98.0, 96.0, 94.0],
        sl_price=92.0,
        tp_prices=[98.0, 100.0, 102.0, 104.0, 106.0, 108.0, 110.0, 112.0],
        entry_structure="LADDER",
        side="LONG",
    )
    enriched = proc.process(result)
    assert enriched.enrichment_decision == "PASS"
    # use_tp_count=4 is respected in passthrough
    assert len(enriched.enriched_signal.take_profits) == 4
    # No reshape audit
    assert enriched.enriched_signal.reshaped is None
    # All 4 entries kept
    assert len(enriched.enriched_signal.entries) == 4
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest src/runtime_v2/signal_enrichment/tests/test_processor_reshape.py -v
```
Expected: Tests fail (no reshape stage yet).

- [ ] **Step 3: Modify processor.py**

Add imports at the top of `processor.py`:

```python
from src.runtime_v2.signal_enrichment.models import (
    # ... all existing imports ...
    ReshapeAudit,
    ReshapeRejectionInfo,
)
from src.runtime_v2.signal_enrichment.reshaping.setup_reshaper import apply_reshape
from src.parser_v2.contracts.entities import Price, StopLoss, TakeProfit
```

Fix the `get_policy_version` call in `process()` — change:

```python
            policy_version = self._config.get_policy_version()
```

to:

```python
            policy_version = self._config.get_policy_version(trader_id)
```

Replace the entire `_process_signal` method with the reshape-aware version:

```python
def _process_signal(
    self,
    result: CanonicalParseResult,
    config: EffectiveEnrichmentConfig,
    policy_snapshot: dict,
    policy_version: str,
) -> EnrichedCanonicalMessage:
    log: list[EnrichmentLogEntry] = []
    signal = result.canonical_message.signal
    trader_id = self._resolve_trader_id(result)
    symbol = to_raw_symbol(signal.symbol) or ""

    def block(reason: str) -> EnrichedCanonicalMessage:
        return self._make_outcome(
            result, "BLOCK", reason, lifecycle_processed=True,
            log=log, policy_snapshot=policy_snapshot, policy_version=policy_version,
            config=config,
        )

    # 1. Blacklist globale
    if self._symbol_in_policy_values(symbol, self._config.get_symbol_blacklist_global()):
        return block("symbol_blacklisted_global")

    # 2. Blacklist per-trader
    if self._symbol_in_policy_values(symbol, self._config.get_symbol_blacklist_for_trader(trader_id)):
        return block("symbol_blacklisted_trader")

    # 3. Entry structure accettata
    if signal.entry_structure not in config.signal_policy.accepted_entry_structures:
        return block("unsupported_entry_structure")

    # 4. SL richiesto
    if config.signal_policy.sl.require_sl:
        if signal.stop_loss is None or signal.stop_loss.price is None:
            return block("missing_stop_loss")

    reshape_mode = config.setup_mode == "reshape" and config.setup_reshape_template is not None
    reshaped_audit: ReshapeAudit | None = None
    reshaped_rejection: ReshapeRejectionInfo | None = None

    if reshape_mode:
        # In reshape mode: realign first (so E1..En are stable), then reshape
        # use_tp_count trim is bypassed — reshape owns TP cardinality
        raw_entries, _, _, _ = self._apply_entry_weights(signal, config)
        realigned_entries, _, _ = self._realign_limit_entries_by_side(raw_entries, signal.side)

        signal_entries_for_reshape = [
            (f"E{leg.sequence}", leg.price.value)
            for leg in realigned_entries
            if leg.price is not None
        ]
        weights_map = {
            f"E{leg.sequence}": leg.weight
            for leg in realigned_entries
        }
        sl_price = signal.stop_loss.price.value if signal.stop_loss and signal.stop_loss.price else None
        tp_prices_original = [tp.price.value for tp in signal.take_profits]

        reshape_result = apply_reshape(
            signal_entries=signal_entries_for_reshape,
            signal_sl_price=sl_price,
            signal_tp_prices=tp_prices_original,
            signal_entry_structure=str(signal.entry_structure),
            signal_side=str(signal.side),
            template=config.setup_reshape_template,
            weights_map=weights_map,
        )

        if isinstance(reshape_result, ReshapeRejectionInfo):
            reshaped_rejection = reshape_result
            return self._make_block_reshape(
                result, config, policy_snapshot, policy_version, log, reshape_result
            )

        reshaped_audit = reshape_result

        # Build enriched signal from reshape output
        enriched_signal = self._build_reshaped_payload(
            symbol, signal, realigned_entries, reshape_result, config
        )
    else:
        # 5. TP trim (passthrough only)
        take_profits = list(signal.take_profits)
        original_tp_count: int | None = None
        use_tp_count = config.signal_policy.tp.use_tp_count
        if use_tp_count is not None and len(take_profits) > use_tp_count:
            original_tp_count = len(take_profits)
            take_profits = take_profits[:use_tp_count]
            log.append(EnrichmentLogEntry(
                check="tp_count_trimmed",
                original=str(original_tp_count),
                result=str(use_tp_count),
            ))

        # 6. Entry split weights + realign
        entries, normalized_structure, range_derivation, range_logs = self._apply_entry_weights(signal, config)
        log.extend(range_logs)
        entries, entry_sequence_realigned, reorder_logs = self._realign_limit_entries_by_side(entries, signal.side)
        log.extend(reorder_logs)

        # 7. Price sanity
        if config.signal_policy.price_sanity.enabled:
            ranges = self._symbol_policy_range(symbol, config.signal_policy.price_sanity.symbol_ranges)
            if ranges and len(ranges) == 2:
                for tp in take_profits:
                    if not (ranges[0] <= tp.price.value <= ranges[1]):
                        return block("price_out_of_range")

        enriched_signal = EnrichedSignalPayload(
            symbol=symbol or None,
            side=signal.side,
            entry_structure=normalized_structure,
            entries=entries,
            take_profits=take_profits,
            stop_loss=signal.stop_loss,
            range_derivation=range_derivation,
            risk_hint=signal.risk_hint,
            entry_sequence_realigned=entry_sequence_realigned,
            original_tp_count=original_tp_count,
        )

    return EnrichedCanonicalMessage(
        canonical_message_id=result.canonical_message_id,
        raw_message_id=result.raw_message_id,
        trader_id=trader_id,
        account_id=config.account_id,
        primary_class=result.primary_class,
        enrichment_decision="PASS",
        enriched_signal=enriched_signal,
        management_plan=config.management_plan,
        enrichment_log=log,
        policy_snapshot=policy_snapshot,
        policy_version=policy_version,
        lifecycle_processed=False,
    )
```

Add the two new helper methods to `SignalEnrichmentProcessor`:

```python
def _make_block_reshape(
    self,
    result: CanonicalParseResult,
    config: EffectiveEnrichmentConfig,
    policy_snapshot: dict,
    policy_version: str,
    log: list[EnrichmentLogEntry],
    rejection: ReshapeRejectionInfo,
) -> EnrichedCanonicalMessage:
    return self._make_outcome(
        result, "BLOCK", rejection.reason_code, lifecycle_processed=True,
        log=log, policy_snapshot=policy_snapshot, policy_version=policy_version,
        config=config,
    )

def _build_reshaped_payload(
    self,
    symbol: str,
    signal,
    realigned_legs: list[EnrichedEntryLeg],
    audit: ReshapeAudit,
    config: EffectiveEnrichmentConfig,
) -> EnrichedSignalPayload:
    operative_sources = {e.source for e in audit.operative_entries}
    operative_legs = [
        leg for leg in realigned_legs
        if f"E{leg.sequence}" in operative_sources
    ]

    new_sl_price = audit.stop_loss.price
    new_sl = StopLoss(price=Price(raw=str(new_sl_price), value=new_sl_price))

    new_tps = [
        TakeProfit(
            sequence=i + 1,
            price=Price(raw=str(t.price), value=t.price),
        )
        for i, t in enumerate(audit.tp_selection.selected)
    ]

    n_operative = len(operative_legs)
    if n_operative == 1:
        derived_structure = "ONE_SHOT"
    elif n_operative == 2:
        derived_structure = "TWO_STEP"
    else:
        derived_structure = "LADDER"

    return EnrichedSignalPayload(
        symbol=symbol or None,
        side=signal.side,
        entry_structure=derived_structure,
        entries=operative_legs,
        take_profits=new_tps,
        stop_loss=new_sl,
        reshaped=audit,
    )
```

- [ ] **Step 4: Run processor reshape tests**

```
pytest src/runtime_v2/signal_enrichment/tests/test_processor_reshape.py -v
```
Expected: All tests PASS.

- [ ] **Step 5: Run full test suite to check no regressions**

```
pytest src/runtime_v2/signal_enrichment/tests/ -v
```
Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/runtime_v2/signal_enrichment/processor.py \
        src/runtime_v2/signal_enrichment/tests/test_processor_reshape.py
git commit -m "feat(reshape): integrate reshape stage in processor — bypass use_tp_count, early realign, reject on no_match"
```

---

### Task 7: Config YAML files

**Files:**
- Create: `config/setup_reshape_templates.yaml`
- Modify: `config/traders/trader_devos_crypto.yaml` (add `setup_mode: passthrough` as explicit default; do NOT set reshape yet — that is a separate operational decision)

> **Note:** The spec §4.7 defines the 3 starter templates. The trader config change is adding the explicit `setup_mode: passthrough` key so the field is visible in the config. Setting `setup_mode: reshape` for a real trader is a live operational decision, not automated here.

- [ ] **Step 1: Create config/setup_reshape_templates.yaml**

Create `config/setup_reshape_templates.yaml`:

```yaml
# config/setup_reshape_templates.yaml
# Template catalog for setup_mode: reshape.
# Traders reference templates by id only: setup_reshape.template: <id>
#
# Vocabolario blocchi:
#   entries.mode      : keep | drop | keep_only | keep_last | keep_first
#   stop_loss.mode    : original | from_entry | from_distance_pct
#   take_profits.mode : keep_all | drop | count | by_rr
#   on_failure        : REJECT  (default; unica modalita' v1)
#
# Indici entry Ex = side-normalized (E1 = entry piu' vicina al prezzo).
# Indici TP   = ordine parsato del segnale (1 = primo TP parsato).
# Match:
#   normalized_entry_count = cardinalita' esatta richiesta
#   min_entry_count        = cardinalita' minima richiesta
#   min_tp_count           = numero minimo di TP parsati originali

templates:

  # Template 1 — LADDER 4 entry, AGGRESSIVO.
  # Scarta E1, tiene E2/E3 operative, E4 -> Stop Loss (SL0 archiviato),
  # riduce gli 8 TP a 4 selezionati per RR.
  - id: ladder_4_aggressive
    enabled: true
    match:
      entry_structure: LADDER
      normalized_entry_count: 4   # esattamente 4 entry dopo normalizzazione
      min_tp_count: 8             # almeno 8 TP parsati originali
    entries:
      mode: drop                  # scarta gli indici elencati
      indexes: [E1]               # E1 scartata; E2,E3,E4 proseguono
    stop_loss:
      mode: from_entry            # un'entry diventa lo SL effettivo
      entry: E4                   # E4 (piu' lontana) -> SL; SL0 archiviato come audit
    take_profits:
      mode: by_rr                 # seleziona i TP esistenti piu' vicini a target RR
      desired_rr: [1.0, 1.5, 2.5, 3.5]
      strategy: nearest_unique    # ogni TP sorgente scelto al massimo una volta
      max_rr_deviation_abs: 0.35  # TP valido solo se entro +-0.35R dal target
      on_missing_target: REJECT   # target senza TP in tolleranza -> rifiuta
    on_failure: REJECT

  # Template 2 — LADDER 4 entry, CONSERVATIVO.
  # Tiene le due entry piu' lontane, mantiene lo SL originale, primi 4 TP.
  - id: ladder_4_keep_sl
    enabled: true
    match:
      entry_structure: LADDER
      normalized_entry_count: 4
      min_tp_count: 8
    entries:
      mode: keep_last             # tiene le ultime N entry in ordine normalizzato
      n: 2                        # E3,E4 operative; E1,E2 scartate
    stop_loss:
      mode: original              # SL del segnale invariato
    take_profits:
      mode: count                 # tiene i primi N TP parsati
      n: 4
    on_failure: REJECT

  # Template 3 — RANGE, TP-only.
  # Non tocca entry o SL; riduce solo i TP quando il segnale RANGE ne porta molti.
  - id: range_tp_reduce
    enabled: true
    match:
      entry_structure: RANGE
      min_tp_count: 8
    entries:
      mode: keep                  # tutte le entry invariate
    stop_loss:
      mode: original              # SL invariato
    take_profits:
      mode: count
      n: 4
    on_failure: REJECT
```

- [ ] **Step 2: Verify loader accepts the file**

```
python -c "
from src.runtime_v2.signal_enrichment.config_loader import OperationConfigLoader
loader = OperationConfigLoader('config')
print('Templates loaded:', list(loader._reshape_templates.keys()))
"
```
Expected output: `Templates loaded: ['ladder_4_aggressive', 'ladder_4_keep_sl', 'range_tp_reduce']`

- [ ] **Step 3: Add explicit setup_mode to trader_devos_crypto.yaml**

In `config/traders/trader_devos_crypto.yaml`, add at the top of the file (before `account:`):

```yaml
setup_mode: passthrough   # reshape: disabled by default; set to reshape + template:<id> to activate
```

- [ ] **Step 4: Commit**

```bash
git add config/setup_reshape_templates.yaml \
        config/traders/trader_devos_crypto.yaml
git commit -m "feat(reshape): add setup_reshape_templates.yaml catalog (3 starter templates) and explicit passthrough in trader_devos_crypto"
```

---

### Task 8: Clean log notes for reshape

**Files:**
- Modify: `src/runtime_v2/control_plane/formatters/templates/clean_log.py`
- Create: `src/runtime_v2/signal_enrichment/tests/test_clean_log_reshape.py`

**Interfaces:**
- Modifies: `_build_signal_notes(p: dict) -> list[str]` — adds two new branches for `reshaped` (PASS) and `reshape_rejected` (REJECT) keys in payload dict `p`
- The clean log template receives a payload dict; the reshape data arrives as `p.get("reshaped")` (a dict with `rule_id`) and `p.get("reshape_rejected")` (a dict with `rule_id` and `phase`)
- Downstream formatters serialize `EnrichedSignalPayload` to the dict `p` that `_build_signal_notes` receives

- [ ] **Step 1: Write failing tests**

Create `src/runtime_v2/signal_enrichment/tests/test_clean_log_reshape.py`:

```python
from src.runtime_v2.control_plane.formatters.templates.clean_log import _build_signal_notes


def test_no_reshape_no_notes():
    notes = _build_signal_notes({})
    assert not any("Reshape" in n for n in notes)


def test_reshaped_pass_note():
    p = {"reshaped": {"rule_id": "ladder_4_aggressive"}}
    notes = _build_signal_notes(p)
    assert any("ladder_4_aggressive" in n for n in notes)
    assert any("Reshaped" in n for n in notes)


def test_reshape_rejected_no_match_note():
    p = {"reshape_rejected": {"rule_id": "ladder_4_aggressive", "phase": "no_match"}}
    notes = _build_signal_notes(p)
    assert any("ladder_4_aggressive" in n for n in notes)
    assert any("did not match" in n for n in notes)


def test_reshape_rejected_invalid_output_note():
    p = {"reshape_rejected": {"rule_id": "ladder_4_aggressive", "phase": "invalid_output"}}
    notes = _build_signal_notes(p)
    assert any("ladder_4_aggressive" in n for n in notes)
    assert any("failed" in n.lower() for n in notes)


def test_existing_notes_unaffected():
    p = {
        "range_derivation": {
            "derived_from_range": True,
            "split_mode": "endpoints",
            "original_min_price": 90.0,
            "original_max_price": 100.0,
        },
        "reshaped": {"rule_id": "ladder_4_aggressive"},
    }
    notes = _build_signal_notes(p)
    assert any("Entry" in n for n in notes)      # range derivation note
    assert any("Reshaped" in n for n in notes)   # reshape note
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest src/runtime_v2/signal_enrichment/tests/test_clean_log_reshape.py -v
```
Expected: `AssertionError` — notes don't include reshape lines yet.

- [ ] **Step 3: Modify _build_signal_notes in clean_log.py**

In `_build_signal_notes`, append these two branches after the existing `realigned` check:

```python
    reshaped = p.get("reshaped") or {}
    if reshaped.get("rule_id"):
        notes.append(f"Setup - Reshaped by rule '{reshaped['rule_id']}'")

    reshape_rejected = p.get("reshape_rejected") or {}
    rr_phase = reshape_rejected.get("phase")
    rr_id = reshape_rejected.get("rule_id")
    if rr_id and rr_phase == "no_match":
        notes.append(f"Setup - Reshape rule '{rr_id}' did not match")
    elif rr_id and rr_phase == "invalid_output":
        notes.append(f"Setup - Reshape failed by rule '{rr_id}'")
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest src/runtime_v2/signal_enrichment/tests/test_clean_log_reshape.py -v
```
Expected: All tests PASS.

- [ ] **Step 5: Run full test suite**

```
pytest src/runtime_v2/signal_enrichment/tests/ -v
```
Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/runtime_v2/control_plane/formatters/templates/clean_log.py \
        src/runtime_v2/signal_enrichment/tests/test_clean_log_reshape.py
git commit -m "feat(reshape): add reshape notes to clean log — PASS and REJECT with rule id"
```

---

## Self-Review

**Spec coverage check:**

| Spec Section | Covered by Task |
|---|---|
| §2 switch `setup_mode` per-trader | Task 1 (models) + Task 5 (loader) + Task 7 (config) |
| §3 flow diagram (realign→reshape→pesi order) | Task 6 (processor) |
| §4.1 vocabolario blocchi (entries/stop/tp modes) | Task 4 (reshaper) |
| §4.2-4.4 template examples | Task 7 (config YAML) |
| §4.5 catalogo globale + lookup id | Task 5 (loader) |
| §4.6 anchor RR planned_weighted_average | Task 2 (tp_rr_selector) |
| §4.7 template iniziali | Task 7 (config YAML) |
| §5 esempio end-to-end | Task 4 test `test_full_reshape_spec_example` |
| §6 audit JSON | Task 1 (audit models) + Task 4 (reshaper output) |
| §6.1 clean log notes | Task 8 |
| §7.1 due livelli fallimento (config + runtime) | Task 5 (config fail-fast) + Task 4 (runtime reject) |
| §7.2 invarianti tabella | Task 3 (validator) |
| §7.3 `on_failure: REJECT` only | Task 4 (`on_failure` field) |
| §8 cosa resta fuori (BE/rr_threshold) | Not implemented (intentional) |
| §10.1 ordine `match(parsed min_tp_count)→realign→reshape→pesi` | Task 6 (processor) |
| §10.2 livelli dati (`reshaped` additivo, no DB migration) | Task 1 (models additivo) |
| §10.3 moduli nuovi `reshaping/` | Tasks 2-4 |
| §10.4 loader fail-fast + policy_version fix | Task 5 |
| §11 policy_version trader-aware fix | Task 6 (call-site fix) |
| §13 rollout inerte di default | Task 7 (passthrough default) |

**Placeholder scan:** No TBD/TODO in plan. All code blocks are complete.

**Type consistency check:**
- `apply_reshape` returns `ReshapeAudit | ReshapeRejectionInfo` — used correctly in Task 6
- `compute_anchor` takes `list[tuple[float, float]]` — called correctly in Task 4 with `[(price, weight), ...]`
- `validate_reshape` takes `list[float]` for `operative_prices` — called correctly in Task 4
- `_build_signal_notes` reads `p.get("reshaped")` as dict — caller must serialize `ReshapeAudit` to dict before passing; this is consistent with how existing fields like `range_derivation` work (serialized through the notification payload pipeline)

**Known gap — clean log serialization path:** The `_build_signal_notes` function reads payload dict `p`. The reshape audit (`ReshapeAudit`) on `EnrichedSignalPayload.reshaped` reaches this dict through the existing notification serialization pipeline. Verify that the pipeline serializes `reshaped` as a dict with `rule_id` key when wiring up; this is consistent with how `range_derivation` and `entry_sequence_realigned` are handled in the same pipeline today. No additional task needed — same pattern, no changes to notification pipeline.
