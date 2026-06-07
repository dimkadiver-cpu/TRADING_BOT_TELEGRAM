# Trader Risk Hint Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the parser-extracted `risk_hint` into runtime risk sizing, apply it with reduce-only semantics, and persist the applied-hint metadata in `plan_state_json` for downstream use.

**Architecture:** `RiskHint` flows from `canonical_message.signal.risk_hint` → `EnrichedSignalPayload.risk_hint` → `RiskCapacityEngine` (reduce-only) → `RiskDecision.hint_applied` → `ExecutionPlanBuilder.build(extra_plan_metadata)` → `plan_state_json["risk_hint_applied"]`. A new config field `risk_hint_range_mode` controls how range hints (`min_value`/`max_value`) are resolved. Clean-log display is explicitly out of scope.

**Tech Stack:** Python 3.12, Pydantic v2, pytest. No new dependencies.

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/runtime_v2/signal_enrichment/models.py` | Modify | Add `risk_hint_range_mode` to `RiskConfig`; add `risk_hint` to `EnrichedSignalPayload` |
| `src/runtime_v2/signal_enrichment/processor.py` | Modify | Propagate `signal.risk_hint` into `EnrichedSignalPayload` |
| `src/runtime_v2/lifecycle/risk_capacity.py` | Modify | Implement reduce-only hint logic; add `hint_applied` to `RiskDecision` |
| `src/runtime_v2/lifecycle/execution_plan.py` | Modify | Add `extra_plan_metadata: dict \| None = None` to `build()` |
| `src/runtime_v2/lifecycle/entry_gate.py` | Modify | Assemble `extra_plan_metadata` from hint + range; pass to builder; remove inline range_derivation merge |
| `config/operation_config.yaml` | Modify | Add `risk_hint_range_mode: min_value` to `defaults.risk` block |
| `config/traders/trader_3.yaml` | Modify | Add `risk_hint_range_mode: min_value` to `risk` override block |
| `tests/runtime_v2/signal_enrichment/test_models.py` | Modify | Tests for `RiskConfig.risk_hint_range_mode` |
| `tests/runtime_v2/signal_enrichment/test_processor_signal.py` | Modify | Test that processor propagates `risk_hint` |
| `tests/runtime_v2/lifecycle/test_risk_capacity.py` | Modify | Tests for reduce-only logic, range mode resolution, flag-off bypass |
| `tests/runtime_v2/lifecycle/test_execution_plan.py` | Modify | Tests for `extra_plan_metadata` in builder |
| `tests/runtime_v2/lifecycle/test_entry_gate.py` | Modify | Test `plan_state_json` contains `risk_hint_applied` when hint applied |

---

## Task 1: Add `risk_hint_range_mode` to `RiskConfig` and YAML

**Files:**
- Modify: `src/runtime_v2/signal_enrichment/models.py:97-107`
- Modify: `config/operation_config.yaml:136-146`
- Modify: `config/traders/trader_3.yaml` (risk block)
- Test: `tests/runtime_v2/signal_enrichment/test_models.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/runtime_v2/signal_enrichment/test_models.py`:

```python
def test_risk_config_risk_hint_range_mode_defaults_to_min_value():
    from src.runtime_v2.signal_enrichment.models import RiskConfig
    config = RiskConfig()
    assert config.risk_hint_range_mode == "min_value"


def test_risk_config_risk_hint_range_mode_accepts_all_valid_values():
    from src.runtime_v2.signal_enrichment.models import RiskConfig
    for mode in ("min_value", "max_value", "midpoint"):
        config = RiskConfig(risk_hint_range_mode=mode)
        assert config.risk_hint_range_mode == mode


def test_risk_config_risk_hint_range_mode_rejects_invalid():
    from pydantic import ValidationError
    from src.runtime_v2.signal_enrichment.models import RiskConfig
    with pytest.raises(ValidationError):
        RiskConfig(risk_hint_range_mode="invalid_mode")
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/runtime_v2/signal_enrichment/test_models.py::test_risk_config_risk_hint_range_mode_defaults_to_min_value tests/runtime_v2/signal_enrichment/test_models.py::test_risk_config_risk_hint_range_mode_accepts_all_valid_values tests/runtime_v2/signal_enrichment/test_models.py::test_risk_config_risk_hint_range_mode_rejects_invalid -v
```

Expected: FAIL — `RiskConfig` has no `risk_hint_range_mode` field.

- [ ] **Step 3: Add `risk_hint_range_mode` to `RiskConfig`**

In `src/runtime_v2/signal_enrichment/models.py`, change the `RiskConfig` class (currently lines 97-107):

```python
class RiskConfig(BaseModel):
    mode: Literal["risk_pct_of_capital", "risk_usdt_fixed"] = "risk_pct_of_capital"
    risk_pct_of_capital: float = 1.0
    risk_usdt_fixed: float = 10.0
    capital_base_mode: Literal["static_config", "live_equity"] = "static_config"
    capital_base_usdt: float = 1000.0
    leverage: int = 1
    use_trader_risk_hint: bool = False
    risk_hint_range_mode: Literal["min_value", "max_value", "midpoint"] = "min_value"
    max_capital_at_risk_per_trader_pct: float = 5.0
    max_concurrent_trades: int = 5
    max_concurrent_same_symbol: int = 1
```

- [ ] **Step 4: Update `config/operation_config.yaml`**

In `config/operation_config.yaml`, after line `use_trader_risk_hint: true  # ...` (line 143), add:

```yaml
    use_trader_risk_hint: true  # true = usa eventuale hint rischio dal segnale/trader.
    risk_hint_range_mode: min_value  # Valori: min_value | max_value | midpoint. Usato quando risk_hint e' un range.
```

- [ ] **Step 5: Update `config/traders/trader_3.yaml`**

In `config/traders/trader_3.yaml`, in the `risk:` block, after `use_trader_risk_hint: true`, add:

```yaml
    use_trader_risk_hint: true  # true = usa eventuale hint rischio dal segnale/trader.
    risk_hint_range_mode: min_value  # Valori: min_value | max_value | midpoint.
```

- [ ] **Step 6: Run tests to verify they pass**

```
pytest tests/runtime_v2/signal_enrichment/test_models.py -v
```

Expected: all pass, including pre-existing tests.

- [ ] **Step 7: Commit**

```bash
git add src/runtime_v2/signal_enrichment/models.py config/operation_config.yaml config/traders/trader_3.yaml tests/runtime_v2/signal_enrichment/test_models.py
git commit -m "feat: add risk_hint_range_mode to RiskConfig"
```

---

## Task 2: Add `risk_hint` to `EnrichedSignalPayload` and propagate it in the processor

**Files:**
- Modify: `src/runtime_v2/signal_enrichment/models.py:148-155`
- Modify: `src/runtime_v2/signal_enrichment/processor.py:1-20` (imports) and `136-144` (EnrichedSignalPayload construction)
- Test: `tests/runtime_v2/signal_enrichment/test_processor_signal.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/runtime_v2/signal_enrichment/test_processor_signal.py`:

```python
def _make_parse_result_with_risk_hint(
    risk_hint_value: float,
    *,
    trader_id: str = "trader_a",
    canonical_message_id: int = 42,
    raw_message_id: int = 420,
):
    from src.parser_v2.contracts.canonical_message import CanonicalMessage, SignalPayload
    from src.parser_v2.contracts.entities import EntryLeg, Price, TakeProfit, StopLoss, RiskHint
    from src.parser_v2.contracts.context import RawContext
    from src.runtime_v2.parser_pipeline.models import CanonicalParseResult
    import datetime

    entries = [EntryLeg(sequence=1, entry_type="LIMIT", price=Price(raw="50000", value=50000.0))]
    take_profits = [TakeProfit(sequence=1, price=Price(raw="51000", value=51000.0))]
    stop_loss = StopLoss(price=Price(raw="49000", value=49000.0))
    risk_hint = RiskHint(raw=f"{risk_hint_value}%", value=risk_hint_value)

    try:
        signal = SignalPayload(
            completeness="COMPLETE",
            symbol="BTC/USDT", side="LONG", entry_structure="ONE_SHOT",
            entries=entries, take_profits=take_profits, stop_loss=stop_loss,
            risk_hint=risk_hint,
        )
    except Exception:
        signal = SignalPayload(
            symbol="BTC/USDT", side="LONG", entry_structure="ONE_SHOT",
            entries=entries, take_profits=take_profits, stop_loss=stop_loss,
            risk_hint=risk_hint,
        )

    canonical = CanonicalMessage(
        parser_profile=trader_id, primary_class="SIGNAL",
        parse_status="PARSED", confidence=1.0,
        signal=signal, raw_context=RawContext(raw_text="test"),
    )
    return CanonicalParseResult(
        raw_message_id=raw_message_id,
        canonical_message_id=canonical_message_id,
        parser_profile=trader_id,
        primary_class="SIGNAL",
        parse_status="PARSED",
        canonical_message=canonical,
        warnings=[],
        parsed_at=datetime.datetime.now(datetime.timezone.utc),
    )


def test_processor_propagates_risk_hint_to_enriched_signal(processor):
    result = _make_parse_result_with_risk_hint(risk_hint_value=1.5)
    enriched = processor.process(result)
    assert enriched.enriched_signal is not None
    assert enriched.enriched_signal.risk_hint is not None
    assert enriched.enriched_signal.risk_hint.value == 1.5
    assert enriched.enriched_signal.risk_hint.raw == "1.5%"


def test_processor_risk_hint_is_none_when_absent(processor):
    result = _make_parse_result()
    enriched = processor.process(result)
    assert enriched.enriched_signal is not None
    assert enriched.enriched_signal.risk_hint is None
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/runtime_v2/signal_enrichment/test_processor_signal.py::test_processor_propagates_risk_hint_to_enriched_signal tests/runtime_v2/signal_enrichment/test_processor_signal.py::test_processor_risk_hint_is_none_when_absent -v
```

Expected: FAIL — `EnrichedSignalPayload` has no `risk_hint` field.

- [ ] **Step 3: Add `risk_hint` field to `EnrichedSignalPayload`**

In `src/runtime_v2/signal_enrichment/models.py`, first add `RiskHint` to the import from `src.parser_v2.contracts.entities` (currently line 9):

```python
from src.parser_v2.contracts.entities import Price, RiskHint, StopLoss, TakeProfit
```

Then update `EnrichedSignalPayload` (currently lines 148-155):

```python
class EnrichedSignalPayload(BaseModel):
    symbol: str | None
    side: Side | None
    entry_structure: EntryStructure | None
    entries: list[EnrichedEntryLeg]
    take_profits: list[TakeProfit]
    stop_loss: StopLoss | None
    range_derivation: RangeDerivation | None = None
    risk_hint: RiskHint | None = None
```

- [ ] **Step 4: Propagate `risk_hint` in the processor**

In `src/runtime_v2/signal_enrichment/processor.py`, update the `EnrichedSignalPayload` construction in `_process_signal()` (currently lines 136-143):

```python
        enriched_signal = EnrichedSignalPayload(
            symbol=symbol or None,
            side=signal.side,
            entry_structure=normalized_structure,
            entries=entries,
            take_profits=take_profits,
            stop_loss=signal.stop_loss,
            range_derivation=range_derivation,
            risk_hint=signal.risk_hint,
        )
```

`signal` here is `result.canonical_message.signal` (a `SignalPayload`, which inherits `SignalFields.risk_hint`).

- [ ] **Step 5: Run tests to verify they pass**

```
pytest tests/runtime_v2/signal_enrichment/test_processor_signal.py -v
```

Expected: all pass, including pre-existing tests.

- [ ] **Step 6: Commit**

```bash
git add src/runtime_v2/signal_enrichment/models.py src/runtime_v2/signal_enrichment/processor.py tests/runtime_v2/signal_enrichment/test_processor_signal.py
git commit -m "feat: propagate risk_hint through EnrichedSignalPayload"
```

---

## Task 3: Implement reduce-only risk hint logic in `RiskCapacityEngine`

**Files:**
- Modify: `src/runtime_v2/lifecycle/risk_capacity.py`
- Test: `tests/runtime_v2/lifecycle/test_risk_capacity.py`

- [ ] **Step 1: Write failing tests**

First, extend the existing `_make_policy_snapshot` helper in `tests/runtime_v2/lifecycle/test_risk_capacity.py` to accept new params. Find `_make_policy_snapshot` (currently starts around line 41) and **replace** it with:

```python
def _make_policy_snapshot(
    capital_base_usdt: float = 1000.0,
    risk_pct: float = 1.0,
    capital_base_mode: str = "static_config",
    max_concurrent_trades: int = 5,
    max_concurrent_same_symbol: int = 1,
    use_trader_risk_hint: bool = False,
    risk_hint_range_mode: str = "min_value",
) -> dict:
    entry_weights = EntryWeightsConfig(weights={"1": 1.0})
    entry_range = EntryRangeConfig(weights={"1": 0.5, "2": 0.5})
    config = EffectiveEnrichmentConfig(
        trader_id="trader_a",
        enabled=True,
        gate_mode="block",
        hedge_mode=False,
        account_id="acc1",
        signal_policy=SignalPolicyConfig(
            accepted_entry_structures=["ONE_SHOT", "TWO_STEP", "RANGE", "LADDER"],
            market_execution=MarketExecutionConfig(),
            entry_split=EntrySplitConfig(
                LIMIT=LimitEntrySplitConfig(
                    single=entry_weights,
                    range=entry_range,
                    averaging=entry_weights,
                    ladder=entry_weights,
                ),
                MARKET=MarketEntrySplitConfig(
                    single=entry_weights,
                    averaging=entry_weights,
                ),
            ),
            tp=TpConfig(),
            sl=SlConfig(),
            price_corrections=PriceCorrectionsConfig(),
            price_sanity=PriceSanityConfig(),
        ),
        update_admission={},
        management_plan=ManagementPlanConfig(),
        risk=RiskConfig(
            mode="risk_pct_of_capital",
            risk_pct_of_capital=risk_pct,
            capital_base_mode=capital_base_mode,
            capital_base_usdt=capital_base_usdt,
            leverage=1,
            max_concurrent_trades=max_concurrent_trades,
            max_concurrent_same_symbol=max_concurrent_same_symbol,
            use_trader_risk_hint=use_trader_risk_hint,
            risk_hint_range_mode=risk_hint_range_mode,
        ),
    )
    return config.model_dump()
```

Then extend `_make_enriched` helper (currently around line 91) to accept a `risk_hint` param. Add these params to its signature and body. Find the `_make_enriched` function and **replace** it:

```python
def _make_enriched(
    *,
    trader_id: str = "trader_a",
    enrichment_id: int = 1,
    symbol: str = "BTC/USDT",
    side: str = "LONG",
    entry_type: str = "LIMIT",
    entry_price: float = 50000.0,
    sl_price: float = 49000.0,
    tp_prices: list[float] | None = None,
    capital_base_usdt: float = 1000.0,
    risk_pct: float = 1.0,
    capital_base_mode: str = "static_config",
    max_concurrent_trades: int = 5,
    max_concurrent_same_symbol: int = 1,
    use_trader_risk_hint: bool = False,
    risk_hint_range_mode: str = "min_value",
    risk_hint=None,
) -> EnrichedCanonicalMessage:
    if tp_prices is None:
        tp_prices = [51000.0]

    entry_leg = EnrichedEntryLeg(
        sequence=1,
        entry_type=entry_type,
        price=_make_price(entry_price) if entry_type == "LIMIT" else None,
        role="PRIMARY",
        weight=1.0,
    )
    take_profits = [
        TakeProfit(sequence=i + 1, price=_make_price(p))
        for i, p in enumerate(tp_prices)
    ]
    stop_loss = StopLoss(price=_make_price(sl_price)) if sl_price is not None else None

    enriched_signal = EnrichedSignalPayload(
        symbol=symbol,
        side=side,
        entry_structure="ONE_SHOT",
        entries=[entry_leg],
        take_profits=take_profits,
        stop_loss=stop_loss,
        risk_hint=risk_hint,
    )

    policy_snapshot = _make_policy_snapshot(
        capital_base_usdt=capital_base_usdt,
        risk_pct=risk_pct,
        capital_base_mode=capital_base_mode,
        max_concurrent_trades=max_concurrent_trades,
        max_concurrent_same_symbol=max_concurrent_same_symbol,
        use_trader_risk_hint=use_trader_risk_hint,
        risk_hint_range_mode=risk_hint_range_mode,
    )

    return EnrichedCanonicalMessage(
        enrichment_id=enrichment_id,
        canonical_message_id=100,
        raw_message_id=200,
        trader_id=trader_id,
        account_id="acc1",
        primary_class="SIGNAL",
        enrichment_decision="PASS",
        enriched_signal=enriched_signal,
        policy_snapshot=policy_snapshot,
        management_plan=ManagementPlanConfig(),
    )
```

Now add the `RiskHint` import at the top of the test file (after existing imports):

```python
from src.parser_v2.contracts.entities import Price, StopLoss, TakeProfit, RiskHint
```

Now append the new tests at the bottom of `tests/runtime_v2/lifecycle/test_risk_capacity.py`:

```python
class TestRiskHintReduceOnly:
    def setup_method(self) -> None:
        self.engine = RiskCapacityEngine()

    def test_hint_smaller_than_config_reduces_risk_amount(self) -> None:
        # config: risk_pct=2%, capital=1000 → base risk_amount=20
        # hint: 1% → hint_risk_amount=10 < 20 → applies
        hint = RiskHint(raw="1%", value=1.0)
        enriched = _make_enriched(
            risk_pct=2.0,
            capital_base_usdt=1000.0,
            use_trader_risk_hint=True,
            risk_hint=hint,
        )
        decision = self.engine.validate(enriched, [], None, None)
        assert decision.passed is True
        assert decision.risk_snapshot["risk_amount"] == pytest.approx(10.0)
        assert decision.hint_applied is not None
        assert decision.hint_applied["hint_effective_pct"] == pytest.approx(1.0)
        assert decision.hint_applied["configured_risk_pct"] == pytest.approx(2.0)
        assert decision.hint_applied["effective_risk_pct"] == pytest.approx(1.0)
        assert decision.hint_applied["hint_raw"] == "1%"
        assert decision.hint_applied["hint_used"] is True

    def test_hint_larger_than_config_does_not_increase_risk(self) -> None:
        # hint=3% > config=2% → config value is kept, hint_applied is None
        hint = RiskHint(raw="3%", value=3.0)
        enriched = _make_enriched(
            risk_pct=2.0,
            capital_base_usdt=1000.0,
            use_trader_risk_hint=True,
            risk_hint=hint,
        )
        decision = self.engine.validate(enriched, [], None, None)
        assert decision.passed is True
        assert decision.risk_snapshot["risk_amount"] == pytest.approx(20.0)
        assert decision.hint_applied is None

    def test_hint_ignored_when_flag_false(self) -> None:
        # use_trader_risk_hint=False → hint is never read
        hint = RiskHint(raw="0.1%", value=0.1)
        enriched = _make_enriched(
            risk_pct=2.0,
            capital_base_usdt=1000.0,
            use_trader_risk_hint=False,
            risk_hint=hint,
        )
        decision = self.engine.validate(enriched, [], None, None)
        assert decision.risk_snapshot["risk_amount"] == pytest.approx(20.0)
        assert decision.hint_applied is None

    def test_hint_none_when_signal_has_no_risk_hint(self) -> None:
        enriched = _make_enriched(
            risk_pct=2.0,
            use_trader_risk_hint=True,
            risk_hint=None,
        )
        decision = self.engine.validate(enriched, [], None, None)
        assert decision.hint_applied is None

    def test_range_hint_uses_min_value_in_min_value_mode(self) -> None:
        # hint range: min=0.5%, max=2.0%, mode=min_value → use 0.5%
        # config=2%, capital=1000 → hint_risk=5 < 20 → applies
        hint = RiskHint(raw="0.5-2%", min_value=0.5, max_value=2.0)
        enriched = _make_enriched(
            risk_pct=2.0,
            capital_base_usdt=1000.0,
            use_trader_risk_hint=True,
            risk_hint_range_mode="min_value",
            risk_hint=hint,
        )
        decision = self.engine.validate(enriched, [], None, None)
        assert decision.risk_snapshot["risk_amount"] == pytest.approx(5.0)

    def test_range_hint_uses_max_value_in_max_value_mode(self) -> None:
        # hint range: min=0.5%, max=1.5%, mode=max_value → use 1.5%
        # config=2%, capital=1000 → hint_risk=15 < 20 → applies
        hint = RiskHint(raw="0.5-1.5%", min_value=0.5, max_value=1.5)
        enriched = _make_enriched(
            risk_pct=2.0,
            capital_base_usdt=1000.0,
            use_trader_risk_hint=True,
            risk_hint_range_mode="max_value",
            risk_hint=hint,
        )
        decision = self.engine.validate(enriched, [], None, None)
        assert decision.risk_snapshot["risk_amount"] == pytest.approx(15.0)

    def test_range_hint_uses_midpoint_in_midpoint_mode(self) -> None:
        # hint range: min=0.5%, max=1.5%, midpoint=1.0%
        # config=2%, capital=1000 → hint_risk=10 < 20 → applies
        hint = RiskHint(raw="0.5-1.5%", min_value=0.5, max_value=1.5)
        enriched = _make_enriched(
            risk_pct=2.0,
            capital_base_usdt=1000.0,
            use_trader_risk_hint=True,
            risk_hint_range_mode="midpoint",
            risk_hint=hint,
        )
        decision = self.engine.validate(enriched, [], None, None)
        assert decision.risk_snapshot["risk_amount"] == pytest.approx(10.0)
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/runtime_v2/lifecycle/test_risk_capacity.py::TestRiskHintReduceOnly -v
```

Expected: FAIL — `RiskDecision` has no `hint_applied`, `RiskCapacityEngine` has no hint logic.

- [ ] **Step 3: Implement in `risk_capacity.py`**

Replace the entire content of `src/runtime_v2/lifecycle/risk_capacity.py` with:

```python
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from src.runtime_v2.lifecycle.models import TradeChain
from src.runtime_v2.lifecycle.ports import AccountStateSnapshot, SymbolMarketSnapshot
from src.runtime_v2.signal_enrichment.models import (
    EffectiveEnrichmentConfig,
    EnrichedCanonicalMessage,
)

logger = logging.getLogger(__name__)


@dataclass
class RiskDecision:
    passed: bool
    reason: str | None
    size_usdt: float | None = None
    leverage: int | None = None
    risk_snapshot: dict = field(default_factory=dict)
    hint_applied: dict | None = None


def _resolve_risk_hint(hint, mode: str) -> float | None:
    """Return resolved percent value from a RiskHint, or None if unresolvable."""
    if hint.value is not None:
        return hint.value
    if hint.min_value is not None and hint.max_value is not None:
        if mode == "min_value":
            return hint.min_value
        if mode == "max_value":
            return hint.max_value
        if mode == "midpoint":
            return (hint.min_value + hint.max_value) / 2.0
    return None


class RiskCapacityEngine:
    def validate(
        self,
        enriched: EnrichedCanonicalMessage,
        open_chains: list[TradeChain],
        account_snapshot: AccountStateSnapshot | None,
        market_snapshot: SymbolMarketSnapshot | None,
    ) -> RiskDecision:
        signal = enriched.enriched_signal
        if signal is None:
            return RiskDecision(passed=False, reason="no_signal_payload")

        try:
            config = EffectiveEnrichmentConfig.model_validate(enriched.policy_snapshot)
        except Exception as exc:
            logger.warning("invalid policy_snapshot: %s", exc)
            return RiskDecision(passed=False, reason="invalid_policy_snapshot")

        risk = config.risk
        symbol = signal.symbol or ""
        side = signal.side or ""

        trader_chains = [c for c in open_chains if c.trader_id == enriched.trader_id]

        # ── concurrency guards ────────────────────────────────────────────────
        if len(trader_chains) >= risk.max_concurrent_trades:
            return RiskDecision(passed=False, reason="max_concurrent_trades_reached")

        same_symbol = [c for c in trader_chains if c.symbol == symbol]
        if len(same_symbol) >= risk.max_concurrent_same_symbol:
            return RiskDecision(passed=False, reason="max_concurrent_same_symbol_reached")

        if any(c.symbol == symbol and c.side == side for c in trader_chains):
            return RiskDecision(passed=False, reason="duplicate_position")

        # ── stop-loss required ────────────────────────────────────────────────
        if signal.stop_loss is None or signal.stop_loss.price is None:
            return RiskDecision(passed=False, reason="missing_stop_loss_for_risk_calc")
        sl_price = signal.stop_loss.price.value

        # ── entry price resolution ────────────────────────────────────────────
        if not signal.entries:
            return RiskDecision(passed=False, reason="no_entry_legs")

        first_leg = signal.entries[0]
        entry_price_deferred = False

        if first_leg.entry_type == "MARKET":
            if market_snapshot is not None and market_snapshot.mark_price is not None:
                entry_price: float | None = market_snapshot.mark_price
            else:
                entry_price = None
                entry_price_deferred = True
        else:
            if first_leg.price is None:
                return RiskDecision(passed=False, reason="missing_limit_price")
            entry_price = first_leg.price.value

        if not entry_price_deferred:
            risk_distance: float | None = abs(entry_price - sl_price)  # type: ignore[arg-type]
            if risk_distance == 0:
                return RiskDecision(passed=False, reason="zero_risk_distance")
        else:
            risk_distance = None

        # ── capital base ──────────────────────────────────────────────────────
        if risk.capital_base_mode == "live_equity":
            if account_snapshot is None or account_snapshot.equity_usdt is None:
                return RiskDecision(passed=False, reason="missing_account_snapshot_for_live_equity")
            capital = account_snapshot.equity_usdt
        else:
            capital = risk.capital_base_usdt

        # ── base risk amount ──────────────────────────────────────────────────
        if risk.mode == "risk_usdt_fixed":
            risk_amount = risk.risk_usdt_fixed
        else:
            risk_amount = capital * risk.risk_pct_of_capital / 100.0

        # ── trader risk hint (reduce-only, pct-based mode only) ───────────────
        hint_applied: dict | None = None
        if risk.mode == "risk_pct_of_capital" and risk.use_trader_risk_hint and signal.risk_hint is not None:
            hint_value = _resolve_risk_hint(signal.risk_hint, risk.risk_hint_range_mode)
            if hint_value is not None:
                hint_risk_amount = capital * hint_value / 100.0
                if hint_risk_amount < risk_amount:
                    hint_applied = {
                        "hint_used": True,
                        "hint_raw": signal.risk_hint.raw,
                        "hint_effective_pct": hint_value,
                        "configured_risk_pct": risk.risk_pct_of_capital,
                        "effective_risk_pct": hint_value,
                    }
                    risk_amount = hint_risk_amount

        # ── max capital-at-risk guard ─────────────────────────────────────────
        max_risk = capital * risk.max_capital_at_risk_per_trader_pct / 100.0
        current_open_risk = 0.0
        for c in trader_chains:
            if c.be_protection_status == "PROTECTED":
                continue
            try:
                snap = json.loads(c.risk_snapshot_json)
                current_open_risk += float(snap.get("risk_amount", 0.0))
            except (json.JSONDecodeError, ValueError, TypeError):
                pass
        if current_open_risk + risk_amount > max_risk:
            return RiskDecision(
                passed=False,
                reason="max_capital_at_risk_exceeded",
                risk_snapshot={"capital": capital, "risk_amount": risk_amount},
            )

        # ── max_leverage guard ────────────────────────────────────────────────
        if config.account is not None:
            if risk.leverage > config.account.max_leverage:
                return RiskDecision(
                    passed=False,
                    reason="risk_leverage_exceeds_account_max_leverage",
                )

        # ── per-leg risk allocation ───────────────────────────────────────────
        n_legs = len(signal.entries)
        legs_snapshot: list[dict] = []
        for leg in signal.entries:
            w = float(leg.weight) if leg.weight is not None else 1.0 / n_legs
            leg_risk = risk_amount * w
            leg_price_val: float | None = leg.price.value if leg.price else (
                entry_price if not entry_price_deferred else None
            )
            is_leg_deferred = leg.entry_type == "MARKET" and entry_price_deferred
            if not is_leg_deferred and leg_price_val is not None:
                leg_rd = abs(leg_price_val - sl_price)
                leg_qty_val: float | None = leg_risk / leg_rd if leg_rd > 0 else 0.0
                qty_mode = "fixed"
            else:
                leg_qty_val = None
                qty_mode = "deferred_market"
            legs_snapshot.append({
                "sequence": leg.sequence,
                "entry_type": leg.entry_type,
                "weight": w,
                "price": leg_price_val,
                "risk_amount": leg_risk,
                "qty": leg_qty_val,
                "qty_mode": qty_mode,
            })

        # ── size calculation ──────────────────────────────────────────────────
        if not entry_price_deferred:
            size_usdt: float | None = risk_amount / risk_distance * entry_price  # type: ignore[operator]
        else:
            size_usdt = None
        leverage = risk.leverage

        risk_snapshot = {
            "capital": capital,
            "risk_amount": risk_amount,
            "entry_price": entry_price,
            "entry_price_deferred": entry_price_deferred,
            "sl_price": sl_price,
            "risk_distance": risk_distance,
            "size_usdt": size_usdt,
            "leverage": leverage,
            "hedge_mode": config.hedge_mode,
            "capital_base_mode": risk.capital_base_mode,
            "legs": legs_snapshot,
        }

        return RiskDecision(
            passed=True,
            reason=None,
            size_usdt=size_usdt,
            leverage=leverage,
            risk_snapshot=risk_snapshot,
            hint_applied=hint_applied,
        )


__all__ = ["RiskCapacityEngine", "RiskDecision"]
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/runtime_v2/lifecycle/test_risk_capacity.py -v
```

Expected: all pass, including all pre-existing tests.

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/lifecycle/risk_capacity.py tests/runtime_v2/lifecycle/test_risk_capacity.py
git commit -m "feat: implement reduce-only risk hint in RiskCapacityEngine"
```

---

## Task 4: Add `extra_plan_metadata` to `ExecutionPlanBuilder.build()`

**Files:**
- Modify: `src/runtime_v2/lifecycle/execution_plan.py:18-81`
- Test: `tests/runtime_v2/lifecycle/test_execution_plan.py`

- [ ] **Step 1: Write failing tests**

First update the `_build` helper in `tests/runtime_v2/lifecycle/test_execution_plan.py` to accept `extra_plan_metadata`. Find the `_build` function (currently around line 38) and **replace** it:

```python
def _build(enrichment_id: int, entries, tps, risk_snap: dict, extra: dict | None = None) -> dict:
    from src.runtime_v2.lifecycle.execution_plan import ExecutionPlanBuilder
    plan_json = ExecutionPlanBuilder.build(enrichment_id, entries, tps, risk_snap, extra)
    return json.loads(plan_json)
```

Now append these tests at the bottom of `tests/runtime_v2/lifecycle/test_execution_plan.py`:

```python
def test_build_includes_extra_plan_metadata_keys():
    entries = _make_entries([(1, "LIMIT", 50000.0, 1.0)])
    tps = [_make_tp(1, 51000.0)]
    risk_snap = _make_risk_snap()
    extra = {
        "risk_hint_applied": {
            "hint_used": True,
            "hint_raw": "1%",
            "hint_effective_pct": 1.0,
            "configured_risk_pct": 2.0,
            "effective_risk_pct": 1.0,
        }
    }
    plan = _build(1, entries, tps, risk_snap, extra)
    assert "risk_hint_applied" in plan
    assert plan["risk_hint_applied"]["hint_raw"] == "1%"
    assert plan["risk_hint_applied"]["hint_effective_pct"] == 1.0


def test_build_without_extra_metadata_has_no_hint_key():
    entries = _make_entries([(1, "LIMIT", 50000.0, 1.0)])
    tps = [_make_tp(1, 51000.0)]
    risk_snap = _make_risk_snap()
    plan = _build(1, entries, tps, risk_snap)
    assert "risk_hint_applied" not in plan


def test_build_with_none_extra_metadata_has_no_hint_key():
    entries = _make_entries([(1, "LIMIT", 50000.0, 1.0)])
    tps = [_make_tp(1, 51000.0)]
    risk_snap = _make_risk_snap()
    plan = _build(1, entries, tps, risk_snap, None)
    assert "risk_hint_applied" not in plan


def test_build_extra_metadata_does_not_overwrite_plan_version():
    entries = _make_entries([(1, "LIMIT", 50000.0, 1.0)])
    tps = [_make_tp(1, 51000.0)]
    risk_snap = _make_risk_snap()
    extra = {"range_derivation": {"derived_from_range": True, "split_mode": "midpoint",
                                   "original_min_price": 63000.0, "original_max_price": 65000.0}}
    plan = _build(1, entries, tps, risk_snap, extra)
    assert plan["plan_version"] == 1
    assert "range_derivation" in plan
    assert plan["range_derivation"]["split_mode"] == "midpoint"
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/runtime_v2/lifecycle/test_execution_plan.py::test_build_includes_extra_plan_metadata_keys tests/runtime_v2/lifecycle/test_execution_plan.py::test_build_without_extra_metadata_has_no_hint_key -v
```

Expected: FAIL — `ExecutionPlanBuilder.build()` does not accept `extra_plan_metadata`.

- [ ] **Step 3: Update `ExecutionPlanBuilder.build()`**

In `src/runtime_v2/lifecycle/execution_plan.py`, update the `build` static method signature and final plan assembly. Replace the `build` method (lines 18-81):

```python
    @staticmethod
    def build(
        enrichment_id: int,
        entries: list[EnrichedEntryLeg],
        take_profits: list[TakeProfit],
        risk_snapshot: dict,
        extra_plan_metadata: dict | None = None,
    ) -> str:
        """Return plan_state_json string."""
        tp_count = len(take_profits)

        # ── rebuild / TP policy ───────────────────────────────────────────────
        if tp_count == 1:
            rebuild_policy: RebuildPolicy = "NONE"
            final_tp = take_profits[0].price.value if take_profits[0].price else None
            intermediate_tps: list[float] = []
        elif tp_count > 1:
            sorted_tps = sorted(take_profits, key=lambda t: t.sequence)
            rebuild_policy = "ON_EACH_ENTRY_FILL"
            final_tp = sorted_tps[-1].price.value if sorted_tps[-1].price else None
            intermediate_tps = [t.price.value for t in sorted_tps[:-1] if t.price]
        else:
            rebuild_policy = "NONE"
            final_tp = None
            intermediate_tps = []

        # ── legs ──────────────────────────────────────────────────────────────
        legs_snap: list[dict] = risk_snapshot.get("legs", [])
        snap_by_seq: dict[int, dict] = {s["sequence"]: s for s in legs_snap}

        legs_out: list[dict] = []
        for leg in sorted(entries, key=lambda e: e.sequence):
            snap = snap_by_seq.get(leg.sequence, {})
            if leg.sequence == 1:
                client_order_id = f"place_entry_attached:{enrichment_id}:leg{leg.sequence}"
            else:
                client_order_id = f"place_entry:{enrichment_id}:leg{leg.sequence}"

            legs_out.append({
                "leg_id": f"leg_{leg.sequence}",
                "sequence": leg.sequence,
                "entry_type": leg.entry_type if isinstance(leg.entry_type, str) else leg.entry_type.value,
                "price": leg.price.value if leg.price is not None else None,
                "risk_budget": float(snap.get("risk_amount") or 0.0),
                "qty": snap.get("qty"),
                "qty_mode": snap.get("qty_mode", "fixed"),
                "weight": snap.get("weight", leg.weight),
                "status": "PENDING",
                "client_order_id": client_order_id,
            })

        plan = {
            "plan_version": 1,
            "protection_policy": "TPSL_ATTACHED_FIRST_LEG",
            "rebuild_policy": rebuild_policy,
            "risk_policy": "REBALANCE_REMAINING_RISK_ON_REPLAN",
            "stop_loss": risk_snapshot.get("sl_price"),
            "final_tp": final_tp,
            "intermediate_tps": intermediate_tps,
            "legs": legs_out,
        }
        if extra_plan_metadata:
            plan.update(extra_plan_metadata)

        return json.dumps(plan)
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/runtime_v2/lifecycle/test_execution_plan.py -v
```

Expected: all pass, including all pre-existing tests.

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/lifecycle/execution_plan.py tests/runtime_v2/lifecycle/test_execution_plan.py
git commit -m "feat: add extra_plan_metadata to ExecutionPlanBuilder.build()"
```

---

## Task 5: Wire `extra_plan_metadata` in `entry_gate.py`

This task refactors the existing inline `range_derivation` merge (approach C) to use the new builder parameter (approach B), and adds `risk_hint_applied` to the same block.

**Files:**
- Modify: `src/runtime_v2/lifecycle/entry_gate.py:590-599`
- Test: `tests/runtime_v2/lifecycle/test_entry_gate.py`

- [ ] **Step 1: Write failing tests**

First update `_make_enriched_signal()` in `tests/runtime_v2/lifecycle/test_entry_gate.py` to accept `risk_hint` and `use_trader_risk_hint`. Find the function signature (currently around line 23) and add params:

```python
def _make_enriched_signal(
    *,
    enrichment_id: int = 1,
    trader_id: str = "trader_a",
    symbol: str = "BTC/USDT",
    side: str = "LONG",
    entry_structure: str = "ONE_SHOT",
    entry_type: str = "LIMIT",
    entry_price: float = 50000.0,
    sl_price: float = 49000.0,
    tp_prices: list[float] | None = None,
    entries: list[dict] | None = None,
    range_derivation: dict | None = None,
    risk_hint=None,
    use_trader_risk_hint: bool = False,
    capital_base_usdt: float = 1000.0,
    risk_pct: float = 1.0,
    max_concurrent_trades: int = 5,
    max_concurrent_same_symbol: int = 1,
    be_trigger: str | None = None,
):
```

Then inside the function, update the `EnrichedSignalPayload` construction to add `risk_hint=risk_hint`:

```python
    signal = EnrichedSignalPayload(
        symbol=symbol,
        side=side,
        entry_structure=entry_structure,
        entries=entry_legs,
        take_profits=tps,
        stop_loss=sl,
        range_derivation=(
            RangeDerivation.model_validate(range_derivation)
            if range_derivation is not None
            else None
        ),
        risk_hint=risk_hint,
    )
```

And update the `RiskConfig` in the policy to pass `use_trader_risk_hint`:

```python
        risk=RiskConfig(
            mode="risk_pct_of_capital", risk_pct_of_capital=risk_pct,
            capital_base_mode="static_config", capital_base_usdt=capital_base_usdt,
            leverage=1, max_capital_at_risk_per_trader_pct=50.0,
            max_concurrent_trades=max_concurrent_trades,
            max_concurrent_same_symbol=max_concurrent_same_symbol,
            use_trader_risk_hint=use_trader_risk_hint,
        ),
```

Also add `RiskHint` to the imports in `_make_enriched_signal`:

```python
    from src.parser_v2.contracts.entities import Price, StopLoss, TakeProfit, RiskHint
```

Now append the new test at the bottom of `tests/runtime_v2/lifecycle/test_entry_gate.py`:

```python
def test_gate_signal_copies_risk_hint_applied_into_plan_state_json():
    from src.parser_v2.contracts.entities import RiskHint
    hint = RiskHint(raw="1%", value=1.0)
    gate = _make_gate()
    enriched = _make_enriched_signal(
        risk_pct=2.0,          # config risk 2%
        use_trader_risk_hint=True,
        risk_hint=hint,        # hint 1% < 2% → should apply
    )
    result = gate.process_signal(enriched, [], "NONE")
    plan = json.loads(result.trade_chain.plan_state_json)
    assert "risk_hint_applied" in plan
    assert plan["risk_hint_applied"]["hint_raw"] == "1%"
    assert plan["risk_hint_applied"]["hint_effective_pct"] == pytest.approx(1.0)
    assert plan["risk_hint_applied"]["configured_risk_pct"] == pytest.approx(2.0)


def test_gate_signal_no_risk_hint_applied_key_when_flag_false():
    from src.parser_v2.contracts.entities import RiskHint
    hint = RiskHint(raw="1%", value=1.0)
    gate = _make_gate()
    enriched = _make_enriched_signal(
        risk_pct=2.0,
        use_trader_risk_hint=False,  # flag off
        risk_hint=hint,
    )
    result = gate.process_signal(enriched, [], "NONE")
    plan = json.loads(result.trade_chain.plan_state_json)
    assert "risk_hint_applied" not in plan


def test_gate_signal_no_risk_hint_applied_key_when_hint_absent():
    gate = _make_gate()
    enriched = _make_enriched_signal(
        risk_pct=2.0,
        use_trader_risk_hint=True,
        risk_hint=None,
    )
    result = gate.process_signal(enriched, [], "NONE")
    plan = json.loads(result.trade_chain.plan_state_json)
    assert "risk_hint_applied" not in plan
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/runtime_v2/lifecycle/test_entry_gate.py::test_gate_signal_copies_risk_hint_applied_into_plan_state_json tests/runtime_v2/lifecycle/test_entry_gate.py::test_gate_signal_no_risk_hint_applied_key_when_flag_false tests/runtime_v2/lifecycle/test_entry_gate.py::test_gate_signal_no_risk_hint_applied_key_when_hint_absent -v
```

Expected: FAIL — entry_gate does not yet pass `hint_applied` to the builder.

- [ ] **Step 3: Update `entry_gate.py` chain-creation callsite**

In `src/runtime_v2/lifecycle/entry_gate.py`, find the block at lines 590-599:

```python
        plan_state = ExecutionPlanBuilder.build(
            eid,
            signal.entries,
            signal.take_profits,
            decision.risk_snapshot,
        )
        if signal.range_derivation is not None:
            plan_data = json.loads(plan_state)
            plan_data["range_derivation"] = signal.range_derivation.model_dump()
            plan_state = json.dumps(plan_data)
```

Replace it with:

```python
        extra_plan: dict = {}
        if signal.range_derivation is not None:
            extra_plan["range_derivation"] = signal.range_derivation.model_dump()
        if decision.hint_applied is not None:
            extra_plan["risk_hint_applied"] = decision.hint_applied

        plan_state = ExecutionPlanBuilder.build(
            eid,
            signal.entries,
            signal.take_profits,
            decision.risk_snapshot,
            extra_plan_metadata=extra_plan or None,
        )
```

- [ ] **Step 4: Run all tests to verify everything passes**

```
pytest tests/runtime_v2/lifecycle/test_entry_gate.py -v
```

Expected: all pass, including the pre-existing `test_gate_signal_copies_range_derivation_into_plan_state_json`.

- [ ] **Step 5: Run full suite to check for regressions**

```
pytest tests/runtime_v2/ -v --tb=short
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/runtime_v2/lifecycle/entry_gate.py tests/runtime_v2/lifecycle/test_entry_gate.py
git commit -m "feat: wire risk_hint_applied and range_derivation into plan_state_json via extra_plan_metadata"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task |
|---|---|
| `use_trader_risk_hint` implemented in `RiskCapacityEngine` (reduce-only) | Task 3 |
| New `risk_hint_range_mode` config field | Task 1 |
| `EnrichedSignalPayload.risk_hint` propagated from canonical message | Task 2 |
| `RiskDecision.hint_applied` carries metadata forward | Task 3 |
| `ExecutionPlanBuilder.build()` accepts `extra_plan_metadata` | Task 4 |
| `entry_gate.py` assembles and passes `extra_plan_metadata` | Task 5 |
| Existing `range_derivation` merge refactored to use builder param (approach B) | Task 5 |
| YAML configs updated | Task 1 |
| `risk_usdt_fixed` mode skips hint (pct-based only) | Task 3 (guard in engine) |
| Clean-log display: out of scope | — |

**Type consistency:**
- `RiskHint` imported from `src.parser_v2.contracts.entities` in all tasks — consistent.
- `hint_applied: dict | None` on `RiskDecision` — used as `dict | None` in all callsites — consistent.
- `extra_plan_metadata: dict | None` in builder — matches `extra_plan or None` in entry_gate — consistent.
- `_resolve_risk_hint(hint, mode)` takes a `RiskHint` instance and a `str` — matches usage in engine — consistent.

**Placeholder scan:** No TBD, no "implement later", all test code is complete.
