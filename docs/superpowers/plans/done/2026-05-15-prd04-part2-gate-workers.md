# PRD-04 Part 2 — Gate Logic + Workers

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implementare la logica operativa di PRD-04: Lifecycle Entry Gate (SIGNAL e UPDATE), LifecycleGateWorker, LifecycleEventProcessor, LifecycleEventWorker, TimeoutWorker, e i test di acceptance contract.

**Prerequisito:** Piano 1 completato — `src/runtime_v2/lifecycle/models.py`, `ports.py`, `static_exchange_data_port.py`, `repositories.py`, `risk_capacity.py` devono esistere.

**Architecture:** Il gate produce result dataclass con chain/eventi/comandi (logica pura, testabile senza DB). Il worker orchestra: legge da parser.sqlite3, chiama il gate, scrive su ops.sqlite3 in transazione atomica, marca `lifecycle_processed=1` come ultimo step.

**Tech Stack:** Python 3.12+, Pydantic v2, sqlite3, pytest

---

## File prodotti da questo piano

| File | Responsabilità |
|------|----------------|
| `src/runtime_v2/lifecycle/entry_gate.py` | LifecycleEntryGate (logica pura SIGNAL+UPDATE), LifecycleGateWorker (orchestrazione) |
| `src/runtime_v2/lifecycle/event_processor.py` | LifecycleEventProcessor (TP_FILLED, SL_FILLED, ENTRY_FILLED) |
| `src/runtime_v2/lifecycle/workers.py` | LifecycleEventWorker, TimeoutWorker |
| `tests/runtime_v2/lifecycle/test_entry_gate.py` | Test gate SIGNAL + UPDATE + control state |
| `tests/runtime_v2/lifecycle/test_event_processor.py` | Test event processor |
| `tests/runtime_v2/lifecycle/test_workers.py` | Test TimeoutWorker + LifecycleEventWorker |
| `tests/runtime_v2/lifecycle/test_integration.py` | Test acceptance contract end-to-end |

---

## Helpers condivisi (definisci in ogni test file che ne ha bisogno)

```python
# Copia questo blocco nei test file che lo richiedono

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def _apply_migrations(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    for f in sorted(Path("db/migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_enriched_signal(
    *,
    enrichment_id: int = 1,
    trader_id: str = "trader_a",
    symbol: str = "BTC/USDT",
    side: str = "LONG",
    entry_type: str = "LIMIT",
    entry_price: float = 50000.0,
    sl_price: float = 49000.0,
    tp_prices: list[float] | None = None,
    capital_base_usdt: float = 1000.0,
    risk_pct: float = 1.0,
    max_concurrent_trades: int = 5,
    max_concurrent_same_symbol: int = 1,
    be_trigger: str | None = None,
):
    from src.parser_v2.contracts.entities import Price, StopLoss, TakeProfit
    from src.runtime_v2.signal_enrichment.models import (
        CloseDistributionConfig, EffectiveEnrichmentConfig, EnrichedCanonicalMessage,
        EnrichedEntryLeg, EnrichedSignalPayload, ManagementPlanConfig,
        MarketExecutionConfig, EntrySplitConfig, EntryWeightsConfig,
        LimitEntrySplitConfig, MarketEntrySplitConfig, EntryRangeConfig,
        RiskConfig, SignalPolicyConfig, TpConfig, SlConfig,
        PriceCorrectionsConfig, PriceSanityConfig,
    )
    entries = [EnrichedEntryLeg(
        sequence=1, entry_type=entry_type,
        price=Price(raw=str(entry_price), value=entry_price) if entry_type == "LIMIT" else None,
        weight=1.0,
    )]
    tps = [
        TakeProfit(sequence=i + 1, price=Price(raw=str(p), value=p))
        for i, p in enumerate(tp_prices or [51000.0])
    ]
    sl = StopLoss(price=Price(raw=str(sl_price), value=sl_price))
    signal = EnrichedSignalPayload(
        symbol=symbol, side=side, entry_structure="ONE_SHOT",
        entries=entries, take_profits=tps, stop_loss=sl,
    )
    weights = EntryWeightsConfig(weights={"E1": 1.0})
    policy = EffectiveEnrichmentConfig(
        trader_id=trader_id, enabled=True, gate_mode="block",
        hedge_mode=False, account_id="acc_1",
        signal_policy=SignalPolicyConfig(
            accepted_entry_structures=["ONE_SHOT", "TWO_STEP", "RANGE", "LADDER"],
            market_execution=MarketExecutionConfig(),
            entry_split=EntrySplitConfig(
                LIMIT=LimitEntrySplitConfig(
                    single=weights,
                    range=EntryRangeConfig(weights={"E1": 0.5, "E2": 0.5}),
                    averaging=weights, ladder=weights,
                ),
                MARKET=MarketEntrySplitConfig(single=weights, averaging=weights),
            ),
            tp=TpConfig(), sl=SlConfig(),
            price_corrections=PriceCorrectionsConfig(), price_sanity=PriceSanityConfig(),
        ),
        update_admission={},
        management_plan=ManagementPlanConfig(be_trigger=be_trigger),
        risk=RiskConfig(
            mode="risk_pct_of_capital", risk_pct_of_capital=risk_pct,
            capital_base_mode="static_config", capital_base_usdt=capital_base_usdt,
            leverage=1, max_capital_at_risk_per_trader_pct=50.0,
            max_concurrent_trades=max_concurrent_trades,
            max_concurrent_same_symbol=max_concurrent_same_symbol,
        ),
    )
    return EnrichedCanonicalMessage(
        enrichment_id=enrichment_id,
        canonical_message_id=enrichment_id * 10,
        raw_message_id=enrichment_id * 100,
        trader_id=trader_id, account_id="acc_1",
        primary_class="SIGNAL", enrichment_decision="PASS",
        enriched_signal=signal,
        management_plan=ManagementPlanConfig(be_trigger=be_trigger),
        policy_snapshot=policy.model_dump(),
    )
```

---

## Task 1: Lifecycle Entry Gate — SIGNAL path

**Files:**
- Create: `src/runtime_v2/lifecycle/entry_gate.py`
- Create: `tests/runtime_v2/lifecycle/test_entry_gate.py`

- [ ] **Step 1: Scrivi i test per il SIGNAL path**

```python
# tests/runtime_v2/lifecycle/test_entry_gate.py
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

# [Incolla qui il blocco helper _apply_migrations, _now, _make_enriched_signal dal blocco condiviso sopra]


def _make_gate():
    from src.runtime_v2.lifecycle.entry_gate import LifecycleEntryGate
    from src.runtime_v2.lifecycle.risk_capacity import RiskCapacityEngine
    from src.runtime_v2.lifecycle.static_exchange_data_port import StaticExchangeDataPort
    return LifecycleEntryGate(
        risk_engine=RiskCapacityEngine(),
        exchange_port=StaticExchangeDataPort(),
    )


def test_gate_signal_pass_creates_chain_and_commands():
    gate = _make_gate()
    enriched = _make_enriched_signal()
    result = gate.process_signal(enriched, [], "NONE")
    assert result.review_reason is None
    assert result.trade_chain is not None
    assert result.trade_chain.lifecycle_state == "WAITING_ENTRY"
    assert result.trade_chain.symbol == "BTC/USDT"
    assert result.trade_chain.side == "LONG"
    assert any(c.command_type == "PLACE_ENTRY" for c in result.execution_commands)
    assert any(c.command_type == "PLACE_PROTECTIVE_STOP" for c in result.execution_commands)
    assert any(c.command_type == "PLACE_TAKE_PROFIT" for c in result.execution_commands)


def test_gate_signal_events_include_signal_accepted_and_chain_created():
    gate = _make_gate()
    enriched = _make_enriched_signal()
    result = gate.process_signal(enriched, [], "NONE")
    event_types = [e.event_type for e in result.lifecycle_events]
    assert "SIGNAL_ACCEPTED" in event_types
    assert "TRADE_CHAIN_CREATED" in event_types


def test_gate_signal_block_new_entries_produces_review():
    gate = _make_gate()
    enriched = _make_enriched_signal()
    result = gate.process_signal(enriched, [], "BLOCK_NEW_ENTRIES")
    assert result.review_reason is not None
    assert "new_entries_paused" in result.review_reason
    assert result.trade_chain is None
    assert any(e.event_type == "REVIEW_REQUIRED" for e in result.lifecycle_events)


def test_gate_signal_full_stop_produces_review():
    gate = _make_gate()
    enriched = _make_enriched_signal()
    result = gate.process_signal(enriched, [], "FULL_STOP")
    assert result.review_reason is not None
    assert result.trade_chain is None


def test_gate_signal_risk_fail_produces_review():
    from src.runtime_v2.lifecycle.models import TradeChain
    gate = _make_gate()
    enriched = _make_enriched_signal(max_concurrent_trades=1)
    open_chains = [
        TradeChain(
            source_enrichment_id=99, canonical_message_id=99, raw_message_id=999,
            trader_id="trader_a", account_id="acc_1", symbol="ETH/USDT", side="LONG",
            lifecycle_state="OPEN", entry_mode="ONE_SHOT", management_plan_json="{}",
            trade_chain_id=99,
        )
    ]
    result = gate.process_signal(enriched, open_chains, "NONE")
    assert result.review_reason == "max_concurrent_trades_reached"
    assert result.trade_chain is None


def test_gate_signal_commands_have_unique_idempotency_keys():
    gate = _make_gate()
    enriched = _make_enriched_signal()
    result = gate.process_signal(enriched, [], "NONE")
    keys = [c.idempotency_key for c in result.execution_commands]
    assert len(keys) == len(set(keys))


def test_gate_signal_events_have_unique_idempotency_keys():
    gate = _make_gate()
    enriched = _make_enriched_signal()
    result = gate.process_signal(enriched, [], "NONE")
    keys = [e.idempotency_key for e in result.lifecycle_events]
    assert len(keys) == len(set(keys))


def test_gate_signal_entry_mode_matches_entry_structure():
    gate = _make_gate()
    enriched = _make_enriched_signal()
    result = gate.process_signal(enriched, [], "NONE")
    assert result.trade_chain.entry_mode == "ONE_SHOT"


def test_gate_signal_review_event_has_no_chain():
    gate = _make_gate()
    enriched = _make_enriched_signal()
    result = gate.process_signal(enriched, [], "BLOCK_NEW_ENTRIES")
    assert result.trade_chain is None
    review_events = [e for e in result.lifecycle_events if e.event_type == "REVIEW_REQUIRED"]
    assert len(review_events) == 1
```

- [ ] **Step 2: Esegui — devono fallire**

```
pytest tests/runtime_v2/lifecycle/test_entry_gate.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implementa LifecycleEntryGate (SIGNAL path) in entry_gate.py**

```python
# src/runtime_v2/lifecycle/entry_gate.py
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from src.runtime_v2.lifecycle.models import (
    BeProtectionStatus, ControlMode, ExecutionCommand,
    LifecycleEvent, LifecycleState, TradeChain,
)
from src.runtime_v2.lifecycle.ports import (
    AccountStateSnapshot, ExchangeDataPort, SymbolMarketSnapshot,
)
from src.runtime_v2.lifecycle.risk_capacity import RiskCapacityEngine
from src.runtime_v2.signal_enrichment.models import (
    EnrichedCanonicalMessage, ManagementPlanConfig,
)

logger = logging.getLogger(__name__)

GLOBAL_SCOPES = frozenset({"ALL_POSITIONS", "ALL_OPEN", "ALL_REMAINING"})


@dataclass
class SignalGateResult:
    trade_chain: TradeChain | None
    lifecycle_events: list[LifecycleEvent]
    execution_commands: list[ExecutionCommand]
    account_snapshot: AccountStateSnapshot | None
    market_snapshot: SymbolMarketSnapshot | None
    review_reason: str | None


@dataclass
class UpdateChainResult:
    trade_chain_id: int
    new_lifecycle_state: LifecycleState | None
    new_be_protection_status: BeProtectionStatus | None
    lifecycle_events: list[LifecycleEvent]
    execution_commands: list[ExecutionCommand]


@dataclass
class UpdateGateResult:
    chain_results: list[UpdateChainResult]
    review_events: list[LifecycleEvent]


class LifecycleEntryGate:
    def __init__(self, risk_engine: RiskCapacityEngine, exchange_port: ExchangeDataPort) -> None:
        self._risk = risk_engine
        self._port = exchange_port

    # ── SIGNAL ────────────────────────────────────────────────────────────────

    def process_signal(
        self,
        enriched: EnrichedCanonicalMessage,
        open_chains: list[TradeChain],
        control_mode: ControlMode,
    ) -> SignalGateResult:
        eid = enriched.enrichment_id

        if control_mode in ("BLOCK_NEW_ENTRIES", "FULL_STOP"):
            return self._review_signal(eid, f"control_mode:new_entries_paused")

        signal = enriched.enriched_signal
        if signal is None or not signal.symbol or not signal.side:
            return self._review_signal(eid, "missing_symbol_or_side")

        account_snapshot = self._port.get_account_state(enriched.account_id)
        market_snapshot = self._port.get_symbol_market_state(enriched.account_id, signal.symbol)

        decision = self._risk.validate(enriched, open_chains, account_snapshot, market_snapshot)
        if not decision.passed:
            return self._review_signal(eid, decision.reason or "risk_check_failed")

        management_plan = enriched.management_plan or ManagementPlanConfig()
        timeout_at = None
        if management_plan.cancel_pending_on_timeout:
            timeout_at = datetime.now(timezone.utc) + timedelta(
                hours=management_plan.pending_timeout_hours
            )

        chain = TradeChain(
            source_enrichment_id=eid,
            canonical_message_id=enriched.canonical_message_id,
            raw_message_id=enriched.raw_message_id,
            trader_id=enriched.trader_id,
            account_id=enriched.account_id,
            symbol=signal.symbol,
            side=signal.side,
            lifecycle_state="WAITING_ENTRY",
            entry_mode=signal.entry_structure or "ONE_SHOT",
            expected_stop_price=(
                signal.stop_loss.price.value
                if signal.stop_loss and signal.stop_loss.price else None
            ),
            be_protection_status="NOT_PROTECTED",
            entry_timeout_at=timeout_at,
            management_plan_json=management_plan.model_dump_json(),
            risk_snapshot_json=json.dumps(decision.risk_snapshot),
        )

        events = [
            LifecycleEvent(
                event_type="SIGNAL_ACCEPTED",
                source_type="enrichment",
                source_id=str(eid),
                next_state="WAITING_ENTRY",
                idempotency_key=f"sig_accepted:{eid}",
            ),
            LifecycleEvent(
                event_type="TRADE_CHAIN_CREATED",
                source_type="enrichment",
                source_id=str(eid),
                idempotency_key=f"chain_created:{eid}",
            ),
        ]

        commands = self._build_entry_commands(enriched)

        return SignalGateResult(
            trade_chain=chain,
            lifecycle_events=events,
            execution_commands=commands,
            account_snapshot=account_snapshot,
            market_snapshot=market_snapshot,
            review_reason=None,
        )

    def _review_signal(self, eid: int, reason: str) -> SignalGateResult:
        event = LifecycleEvent(
            event_type="REVIEW_REQUIRED",
            source_type="enrichment",
            source_id=str(eid),
            payload_json=json.dumps({"reason": reason}),
            idempotency_key=f"review_signal:{eid}",
        )
        return SignalGateResult(
            trade_chain=None,
            lifecycle_events=[event],
            execution_commands=[],
            account_snapshot=None,
            market_snapshot=None,
            review_reason=reason,
        )

    def _build_entry_commands(self, enriched: EnrichedCanonicalMessage) -> list[ExecutionCommand]:
        signal = enriched.enriched_signal
        management_plan = enriched.management_plan or ManagementPlanConfig()
        eid = enriched.enrichment_id
        risk_snap = json.loads(enriched.policy_snapshot.get("risk", "{}") or "{}")
        size_usdt = 0.0

        commands: list[ExecutionCommand] = []

        tp_count = len(signal.take_profits)
        close_pcts = self._get_close_pcts(management_plan, tp_count)

        for leg in signal.entries:
            payload = {
                "symbol": signal.symbol,
                "side": signal.side,
                "entry_type": leg.entry_type,
                "price": leg.price.value if leg.price else None,
                "weight": leg.weight,
                "sequence": leg.sequence,
            }
            commands.append(ExecutionCommand(
                trade_chain_id=0,
                command_type="PLACE_ENTRY",
                payload_json=json.dumps(payload),
                idempotency_key=f"place_entry:{eid}:leg{leg.sequence}",
            ))

        if signal.stop_loss and signal.stop_loss.price:
            payload = {
                "symbol": signal.symbol,
                "side": signal.side,
                "stop_price": signal.stop_loss.price.value,
            }
            commands.append(ExecutionCommand(
                trade_chain_id=0,
                command_type="PLACE_PROTECTIVE_STOP",
                payload_json=json.dumps(payload),
                idempotency_key=f"place_stop:{eid}",
            ))

        for i, tp in enumerate(signal.take_profits):
            close_pct = close_pcts[i] if i < len(close_pcts) else (100.0 / tp_count)
            payload = {
                "symbol": signal.symbol,
                "side": signal.side,
                "tp_price": tp.price.value if tp.price else None,
                "sequence": tp.sequence,
                "close_pct": close_pct,
            }
            commands.append(ExecutionCommand(
                trade_chain_id=0,
                command_type="PLACE_TAKE_PROFIT",
                payload_json=json.dumps(payload),
                idempotency_key=f"place_tp:{eid}:tp{tp.sequence}",
            ))

        return commands

    @staticmethod
    def _get_close_pcts(management_plan: ManagementPlanConfig, tp_count: int) -> list[float]:
        if tp_count == 0:
            return []
        dist = management_plan.close_distribution
        if dist.mode == "table" and tp_count in dist.table:
            return [float(p) for p in dist.table[tp_count]]
        pct = 100.0 / tp_count
        return [pct] * tp_count

    # ── UPDATE ────────────────────────────────────────────────────────────────

    def process_update(
        self,
        enriched: EnrichedCanonicalMessage,
        open_chains: list[TradeChain],
        active_commands_by_chain: dict[int, list[ExecutionCommand]],
    ) -> UpdateGateResult:
        tags = enriched.enriched_actions or []
        if not tags:
            event = self._make_review_event_no_chain(enriched, "no_actionable_targets")
            return UpdateGateResult(chain_results=[], review_events=[event])

        chain_results: list[UpdateChainResult] = []
        review_events: list[LifecycleEvent] = []

        for tag in tags:
            matched = self._resolve_targets(enriched, open_chains, tag)

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

    def _resolve_targets(
        self,
        enriched: EnrichedCanonicalMessage,
        open_chains: list[TradeChain],
        tag,
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

        if len(trader_chains) > 1:
            return None
        return trader_chains

    def _apply_action_to_chain(
        self,
        enriched: EnrichedCanonicalMessage,
        chain: TradeChain,
        action,
        active_commands: list[ExecutionCommand],
    ) -> UpdateChainResult:
        action_type = action.action_type
        if action_type == "SET_STOP":
            op = action.set_stop
            if op and op.target_type == "ENTRY":
                return self._apply_move_to_be(enriched, chain, active_commands)
            return self._review_chain(enriched, chain, "unsupported_set_stop_target_type")

        if action_type == "CLOSE":
            op = action.close
            if op and op.close_scope == "FULL":
                return self._apply_close_full(enriched, chain)
            if op and op.close_scope == "PARTIAL":
                return self._apply_close_partial(enriched, chain, op)
            return self._review_chain(enriched, chain, "unknown_close_scope")

        if action_type == "CANCEL_PENDING":
            return self._apply_cancel_pending(enriched, chain)

        return self._review_chain(enriched, chain, f"unsupported_action_type:{action_type}")

    def _apply_move_to_be(
        self,
        enriched: EnrichedCanonicalMessage,
        chain: TradeChain,
        active_commands: list[ExecutionCommand],
    ) -> UpdateChainResult:
        chain_id = chain.trade_chain_id
        cmid = enriched.canonical_message_id

        if self._is_already_be(chain):
            return UpdateChainResult(
                trade_chain_id=chain_id,
                new_lifecycle_state=None,
                new_be_protection_status=None,
                lifecycle_events=[LifecycleEvent(
                    trade_chain_id=chain_id,
                    event_type="NOOP_ALREADY_PROTECTED_BE",
                    source_type="telegram_update",
                    source_id=str(cmid),
                    idempotency_key=f"noop_be:{chain_id}:{cmid}",
                )],
                execution_commands=[],
            )

        active_be = [
            c for c in active_commands
            if c.command_type == "MOVE_STOP_TO_BREAKEVEN" and c.status in ("PENDING", "SENT", "ACK")
        ]
        if active_be:
            return UpdateChainResult(
                trade_chain_id=chain_id,
                new_lifecycle_state=None,
                new_be_protection_status=None,
                lifecycle_events=[LifecycleEvent(
                    trade_chain_id=chain_id,
                    event_type="NOOP_DUPLICATE_COMMAND",
                    source_type="telegram_update",
                    source_id=str(cmid),
                    idempotency_key=f"noop_dup_be:{chain_id}:{cmid}",
                )],
                execution_commands=[],
            )

        try:
            mp = ManagementPlanConfig.model_validate_json(chain.management_plan_json)
        except Exception:
            mp = ManagementPlanConfig()

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
        event = LifecycleEvent(
            trade_chain_id=chain_id,
            event_type="BE_MOVE_REQUESTED",
            source_type="telegram_update",
            source_id=str(cmid),
            previous_state=chain.lifecycle_state,
            next_state="BE_MOVE_PENDING",
            idempotency_key=f"be_requested:{chain_id}:{cmid}",
        )
        return UpdateChainResult(
            trade_chain_id=chain_id,
            new_lifecycle_state="BE_MOVE_PENDING",
            new_be_protection_status="BE_MOVE_PENDING",
            lifecycle_events=[event],
            execution_commands=[cmd],
        )

    def _apply_close_full(
        self, enriched: EnrichedCanonicalMessage, chain: TradeChain
    ) -> UpdateChainResult:
        chain_id = chain.trade_chain_id
        cmid = enriched.canonical_message_id
        state = chain.lifecycle_state

        if state in ("CLOSED", "CANCELLED", "EXPIRED"):
            return UpdateChainResult(
                trade_chain_id=chain_id,
                new_lifecycle_state=None,
                new_be_protection_status=None,
                lifecycle_events=[LifecycleEvent(
                    trade_chain_id=chain_id,
                    event_type="NOOP_ALREADY_CLOSED",
                    source_type="telegram_update",
                    source_id=str(cmid),
                    idempotency_key=f"noop_closed:{chain_id}:{cmid}",
                )],
                execution_commands=[],
            )

        cmd = ExecutionCommand(
            trade_chain_id=chain_id,
            command_type="CLOSE_FULL",
            payload_json=json.dumps({"symbol": chain.symbol, "side": chain.side}),
            idempotency_key=f"close_full:{chain_id}:{cmid}",
        )
        event = LifecycleEvent(
            trade_chain_id=chain_id,
            event_type="TELEGRAM_UPDATE_ACCEPTED",
            source_type="telegram_update",
            source_id=str(cmid),
            payload_json=json.dumps({"action": "CLOSE_FULL"}),
            idempotency_key=f"update_close_full:{chain_id}:{cmid}",
        )
        return UpdateChainResult(
            trade_chain_id=chain_id,
            new_lifecycle_state=None,
            new_be_protection_status=None,
            lifecycle_events=[event],
            execution_commands=[cmd],
        )

    def _apply_close_partial(
        self, enriched: EnrichedCanonicalMessage, chain: TradeChain, op
    ) -> UpdateChainResult:
        chain_id = chain.trade_chain_id
        cmid = enriched.canonical_message_id
        fraction = op.fraction or 0.5
        cmd = ExecutionCommand(
            trade_chain_id=chain_id,
            command_type="CLOSE_PARTIAL",
            payload_json=json.dumps({"symbol": chain.symbol, "side": chain.side, "fraction": fraction}),
            idempotency_key=f"close_partial:{chain_id}:{cmid}",
        )
        event = LifecycleEvent(
            trade_chain_id=chain_id,
            event_type="TELEGRAM_UPDATE_ACCEPTED",
            source_type="telegram_update",
            source_id=str(cmid),
            payload_json=json.dumps({"action": "CLOSE_PARTIAL", "fraction": fraction}),
            idempotency_key=f"update_close_partial:{chain_id}:{cmid}",
        )
        return UpdateChainResult(
            trade_chain_id=chain_id,
            new_lifecycle_state=None,
            new_be_protection_status=None,
            lifecycle_events=[event],
            execution_commands=[cmd],
        )

    def _apply_cancel_pending(
        self, enriched: EnrichedCanonicalMessage, chain: TradeChain
    ) -> UpdateChainResult:
        chain_id = chain.trade_chain_id
        cmid = enriched.canonical_message_id

        if chain.lifecycle_state != "WAITING_ENTRY":
            return UpdateChainResult(
                trade_chain_id=chain_id,
                new_lifecycle_state=None,
                new_be_protection_status=None,
                lifecycle_events=[LifecycleEvent(
                    trade_chain_id=chain_id,
                    event_type="NOOP_NOT_PENDING",
                    source_type="telegram_update",
                    source_id=str(cmid),
                    idempotency_key=f"noop_not_pending:{chain_id}:{cmid}",
                )],
                execution_commands=[],
            )

        cmd = ExecutionCommand(
            trade_chain_id=chain_id,
            command_type="CANCEL_PENDING_ENTRY",
            payload_json=json.dumps({"symbol": chain.symbol, "side": chain.side}),
            idempotency_key=f"cancel_pending:{chain_id}:{cmid}",
        )
        event = LifecycleEvent(
            trade_chain_id=chain_id,
            event_type="TELEGRAM_UPDATE_ACCEPTED",
            source_type="telegram_update",
            source_id=str(cmid),
            payload_json=json.dumps({"action": "CANCEL_PENDING"}),
            idempotency_key=f"update_cancel:{chain_id}:{cmid}",
        )
        return UpdateChainResult(
            trade_chain_id=chain_id,
            new_lifecycle_state="CANCELLED",
            new_be_protection_status=None,
            lifecycle_events=[event],
            execution_commands=[cmd],
        )

    @staticmethod
    def _is_already_be(chain: TradeChain) -> bool:
        if chain.be_protection_status == "PROTECTED":
            return True
        if chain.entry_avg_price is None or chain.current_stop_price is None:
            return False
        try:
            mp = ManagementPlanConfig.model_validate_json(chain.management_plan_json)
            buffer = mp.be_buffer_pct
        except Exception:
            buffer = 0.0
        if chain.side == "LONG":
            return chain.current_stop_price >= chain.entry_avg_price * (1 + buffer)
        return chain.current_stop_price <= chain.entry_avg_price * (1 - buffer)

    def _review_chain(
        self, enriched: EnrichedCanonicalMessage, chain: TradeChain, reason: str
    ) -> UpdateChainResult:
        chain_id = chain.trade_chain_id
        cmid = enriched.canonical_message_id
        return UpdateChainResult(
            trade_chain_id=chain_id,
            new_lifecycle_state=None,
            new_be_protection_status=None,
            lifecycle_events=[LifecycleEvent(
                trade_chain_id=chain_id,
                event_type="REVIEW_REQUIRED",
                source_type="telegram_update",
                source_id=str(cmid),
                payload_json=json.dumps({"reason": reason}),
                idempotency_key=f"review_chain:{chain_id}:{cmid}:{reason}",
            )],
            execution_commands=[],
        )

    def _make_review_event_no_chain(
        self, enriched: EnrichedCanonicalMessage, reason: str
    ) -> LifecycleEvent:
        cmid = enriched.canonical_message_id
        return LifecycleEvent(
            event_type="REVIEW_REQUIRED",
            source_type="telegram_update",
            source_id=str(cmid),
            payload_json=json.dumps({"reason": reason}),
            idempotency_key=f"review_update:{cmid}:{reason}",
        )


__all__ = [
    "LifecycleEntryGate", "SignalGateResult",
    "UpdateGateResult", "UpdateChainResult",
]
```

- [ ] **Step 4: Esegui i test — devono passare**

```
pytest tests/runtime_v2/lifecycle/test_entry_gate.py -v
```

Expected: PASS (9 test)

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/lifecycle/entry_gate.py tests/runtime_v2/lifecycle/test_entry_gate.py
git commit -m "feat(prd04): add LifecycleEntryGate — SIGNAL + UPDATE pure logic"
```

---

## Task 2: UPDATE path — test aggiuntivi

**Files:**
- Modify: `tests/runtime_v2/lifecycle/test_entry_gate.py`

- [ ] **Step 1: Aggiungi i test UPDATE in fondo al file**

```python
# Aggiungi in fondo a tests/runtime_v2/lifecycle/test_entry_gate.py

def _make_update_enriched(
    *,
    canonical_message_id: int = 200,
    trader_id: str = "trader_a",
    scope_hint: str = "SINGLE_SIGNAL",
    action_type: str = "SET_STOP",
    set_stop_target: str = "ENTRY",
    symbols: list[str] | None = None,
    close_scope: str = "FULL",
    fraction: float | None = None,
):
    from src.parser_v2.contracts.canonical_message import (
        ActionItem, CloseOperation, SetStopOperation, TargetActionGroup,
    )
    from src.parser_v2.contracts.context import TargetHints
    from src.runtime_v2.signal_enrichment.models import EnrichedCanonicalMessage

    if action_type == "SET_STOP":
        action = ActionItem(
            action_type="SET_STOP",
            set_stop=SetStopOperation(target_type=set_stop_target),
            source_intent="MOVE_STOP_TO_BE",
        )
    else:
        action = ActionItem(
            action_type="CLOSE",
            close=CloseOperation(close_scope=close_scope, fraction=fraction),
            source_intent="CLOSE_FULL" if close_scope == "FULL" else "CLOSE_PARTIAL",
        )

    tag = TargetActionGroup(
        targeting=TargetHints(scope_hint=scope_hint, symbols=symbols or []),
        actions=[action],
    )
    return EnrichedCanonicalMessage(
        enrichment_id=canonical_message_id,
        canonical_message_id=canonical_message_id,
        raw_message_id=canonical_message_id * 10,
        trader_id=trader_id, account_id="acc_1",
        primary_class="UPDATE", enrichment_decision="PASS",
        enriched_actions=[tag],
        policy_snapshot={},
    )


def _make_open_chain(
    *,
    trade_chain_id: int = 1,
    trader_id: str = "trader_a",
    symbol: str = "BTC/USDT",
    side: str = "LONG",
    state: str = "OPEN",
    entry_avg_price: float | None = None,
    current_stop_price: float | None = None,
    be_status: str = "NOT_PROTECTED",
):
    from src.runtime_v2.lifecycle.models import TradeChain
    return TradeChain(
        trade_chain_id=trade_chain_id,
        source_enrichment_id=trade_chain_id,
        canonical_message_id=trade_chain_id * 10,
        raw_message_id=trade_chain_id * 100,
        trader_id=trader_id, account_id="acc_1",
        symbol=symbol, side=side, lifecycle_state=state,
        entry_mode="ONE_SHOT", management_plan_json="{}",
        entry_avg_price=entry_avg_price,
        current_stop_price=current_stop_price,
        be_protection_status=be_status,
    )


def test_update_move_to_be_creates_command():
    gate = _make_gate()
    enriched = _make_update_enriched(scope_hint="SINGLE_SIGNAL", symbols=["BTC/USDT"])
    chain = _make_open_chain(entry_avg_price=50000.0, current_stop_price=49000.0)
    result = gate.process_update(enriched, [chain], {})
    assert len(result.chain_results) == 1
    cr = result.chain_results[0]
    assert any(c.command_type == "MOVE_STOP_TO_BREAKEVEN" for c in cr.execution_commands)
    assert cr.new_lifecycle_state == "BE_MOVE_PENDING"


def test_update_move_to_be_already_protected_noop():
    gate = _make_gate()
    enriched = _make_update_enriched(scope_hint="SINGLE_SIGNAL", symbols=["BTC/USDT"])
    chain = _make_open_chain(be_status="PROTECTED")
    result = gate.process_update(enriched, [chain], {})
    cr = result.chain_results[0]
    assert len(cr.execution_commands) == 0
    assert cr.lifecycle_events[0].event_type == "NOOP_ALREADY_PROTECTED_BE"


def test_update_move_to_be_duplicate_command_noop():
    from src.runtime_v2.lifecycle.models import ExecutionCommand
    gate = _make_gate()
    enriched = _make_update_enriched(scope_hint="SINGLE_SIGNAL", symbols=["BTC/USDT"])
    chain = _make_open_chain(trade_chain_id=1, entry_avg_price=50000.0)
    existing_be_cmd = ExecutionCommand(
        trade_chain_id=1, command_type="MOVE_STOP_TO_BREAKEVEN",
        payload_json="{}", idempotency_key="move_be:1:999", status="PENDING",
    )
    result = gate.process_update(enriched, [chain], {1: [existing_be_cmd]})
    cr = result.chain_results[0]
    assert cr.lifecycle_events[0].event_type == "NOOP_DUPLICATE_COMMAND"


def test_update_close_full_active_chain():
    gate = _make_gate()
    enriched = _make_update_enriched(
        scope_hint="SINGLE_SIGNAL", symbols=["BTC/USDT"],
        action_type="CLOSE", close_scope="FULL",
    )
    chain = _make_open_chain()
    result = gate.process_update(enriched, [chain], {})
    cr = result.chain_results[0]
    assert any(c.command_type == "CLOSE_FULL" for c in cr.execution_commands)


def test_update_close_full_already_closed_noop():
    gate = _make_gate()
    enriched = _make_update_enriched(
        scope_hint="SINGLE_SIGNAL", symbols=["BTC/USDT"],
        action_type="CLOSE", close_scope="FULL",
    )
    chain = _make_open_chain(state="CLOSED")
    result = gate.process_update(enriched, [chain], {})
    cr = result.chain_results[0]
    assert cr.lifecycle_events[0].event_type == "NOOP_ALREADY_CLOSED"


def test_update_all_short_targets_only_short_chains_of_trader():
    gate = _make_gate()
    enriched = _make_update_enriched(
        trader_id="trader_a", scope_hint="ALL_SHORT",
        action_type="CLOSE", close_scope="FULL",
    )
    chains = [
        _make_open_chain(trade_chain_id=1, trader_id="trader_a", symbol="BTC/USDT", side="SHORT"),
        _make_open_chain(trade_chain_id=2, trader_id="trader_a", symbol="ETH/USDT", side="LONG"),
        _make_open_chain(trade_chain_id=3, trader_id="trader_b", symbol="BTC/USDT", side="SHORT"),
    ]
    result = gate.process_update(enriched, chains, {})
    assert len(result.chain_results) == 1
    assert result.chain_results[0].trade_chain_id == 1


def test_update_all_positions_targets_all_trader_chains():
    gate = _make_gate()
    enriched = _make_update_enriched(
        trader_id="trader_a", scope_hint="ALL_POSITIONS",
        action_type="CLOSE", close_scope="FULL",
    )
    chains = [
        _make_open_chain(trade_chain_id=1, trader_id="trader_a", symbol="BTC/USDT", side="LONG"),
        _make_open_chain(trade_chain_id=2, trader_id="trader_a", symbol="ETH/USDT", side="SHORT"),
        _make_open_chain(trade_chain_id=3, trader_id="trader_b", symbol="BTC/USDT", side="LONG"),
    ]
    result = gate.process_update(enriched, chains, {})
    chain_ids = {cr.trade_chain_id for cr in result.chain_results}
    assert chain_ids == {1, 2}


def test_update_all_remaining_same_as_all_positions():
    gate = _make_gate()
    enriched_rem = _make_update_enriched(
        trader_id="trader_a", scope_hint="ALL_REMAINING",
        action_type="CLOSE", close_scope="FULL",
    )
    enriched_pos = _make_update_enriched(
        trader_id="trader_a", scope_hint="ALL_POSITIONS",
        action_type="CLOSE", close_scope="FULL",
    )
    chains = [
        _make_open_chain(trade_chain_id=1, trader_id="trader_a"),
        _make_open_chain(trade_chain_id=2, trader_id="trader_a", symbol="ETH/USDT"),
    ]
    result_rem = gate.process_update(enriched_rem, chains, {})
    result_pos = gate.process_update(enriched_pos, chains, {})
    assert len(result_rem.chain_results) == len(result_pos.chain_results)


def test_update_ambiguous_target_produces_review():
    gate = _make_gate()
    enriched = _make_update_enriched(
        scope_hint="SINGLE_SIGNAL",  # non global + multiple chains = ambiguous
        action_type="CLOSE", close_scope="FULL",
    )
    chains = [
        _make_open_chain(trade_chain_id=1, symbol="BTC/USDT"),
        _make_open_chain(trade_chain_id=2, symbol="ETH/USDT"),
    ]
    result = gate.process_update(enriched, chains, {})
    assert len(result.review_events) == 1
    assert result.review_events[0].event_type == "REVIEW_REQUIRED"


def test_update_no_match_produces_review():
    gate = _make_gate()
    enriched = _make_update_enriched(
        scope_hint="SYMBOL", symbols=["XRP/USDT"],
        action_type="CLOSE", close_scope="FULL",
    )
    chains = [_make_open_chain(symbol="BTC/USDT")]
    result = gate.process_update(enriched, chains, {})
    assert len(result.review_events) == 1


def test_update_batch_idempotency_keys_per_chain():
    gate = _make_gate()
    enriched = _make_update_enriched(
        trader_id="trader_a", scope_hint="ALL_SHORT",
        action_type="CLOSE", close_scope="FULL",
        canonical_message_id=300,
    )
    chains = [
        _make_open_chain(trade_chain_id=10, side="SHORT"),
        _make_open_chain(trade_chain_id=11, side="SHORT", symbol="ETH/USDT"),
    ]
    result = gate.process_update(enriched, chains, {})
    all_keys = [c.idempotency_key for cr in result.chain_results for c in cr.execution_commands]
    assert len(all_keys) == len(set(all_keys))
```

- [ ] **Step 2: Esegui — devono passare**

```
pytest tests/runtime_v2/lifecycle/test_entry_gate.py -v
```

Expected: PASS (tutti i test)

- [ ] **Step 3: Commit**

```bash
git add tests/runtime_v2/lifecycle/test_entry_gate.py
git commit -m "test(prd04): add UPDATE path tests for LifecycleEntryGate"
```

---

## Task 3: LifecycleGateWorker

**Files:**
- Modify: `src/runtime_v2/lifecycle/entry_gate.py` (aggiungi LifecycleGateWorker)
- Create: `tests/runtime_v2/lifecycle/test_workers.py` (worker tests)

- [ ] **Step 1: Scrivi il test del worker**

```python
# tests/runtime_v2/lifecycle/test_workers.py
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

# [Incolla qui _apply_migrations, _now, _make_enriched_signal dal blocco helper]


@pytest.fixture
def dbs(tmp_path):
    parser_db = str(tmp_path / "parser.sqlite3")
    ops_db = str(tmp_path / "ops.sqlite3")
    for db in (parser_db, ops_db):
        conn = sqlite3.connect(db)
        for f in sorted(Path("db/migrations").glob("*.sql")):
            conn.executescript(f.read_text(encoding="utf-8"))
        conn.commit()
        conn.close()
    return parser_db, ops_db


def _insert_enriched(parser_db: str, enrichment_id: int, enriched) -> None:
    import json
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(parser_db)
    conn.execute(
        """
        INSERT INTO enriched_canonical_messages (
            enrichment_id, canonical_message_id, raw_message_id, trader_id, account_id,
            primary_class, enrichment_decision, enriched_signal_json,
            management_plan_json, policy_snapshot_json, lifecycle_processed, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,0,?)
        """,
        (
            enrichment_id,
            enriched.canonical_message_id,
            enriched.raw_message_id,
            enriched.trader_id,
            enriched.account_id,
            enriched.primary_class,
            enriched.enrichment_decision,
            enriched.enriched_signal.model_dump_json() if enriched.enriched_signal else None,
            enriched.management_plan.model_dump_json() if enriched.management_plan else "{}",
            json.dumps(enriched.policy_snapshot),
            now,
        ),
    )
    conn.commit()
    conn.close()


def _make_worker(parser_db, ops_db):
    from src.runtime_v2.lifecycle.entry_gate import LifecycleEntryGate, LifecycleGateWorker
    from src.runtime_v2.lifecycle.repositories import (
        ControlStateRepository, ExecutionCommandRepository,
        LifecycleEventRepository, SnapshotRepository, TradeChainRepository,
    )
    from src.runtime_v2.lifecycle.risk_capacity import RiskCapacityEngine
    from src.runtime_v2.lifecycle.static_exchange_data_port import StaticExchangeDataPort

    gate = LifecycleEntryGate(
        risk_engine=RiskCapacityEngine(),
        exchange_port=StaticExchangeDataPort(),
    )
    return LifecycleGateWorker(
        parser_db_path=parser_db,
        ops_db_path=ops_db,
        gate=gate,
        chain_repo=TradeChainRepository(ops_db),
        event_repo=LifecycleEventRepository(ops_db),
        command_repo=ExecutionCommandRepository(ops_db),
        snapshot_repo=SnapshotRepository(ops_db),
        control_repo=ControlStateRepository(ops_db),
    )


def test_worker_processes_signal_creates_chain(dbs):
    parser_db, ops_db = dbs
    enriched = _make_enriched_signal(enrichment_id=1)
    _insert_enriched(parser_db, 1, enriched)

    worker = _make_worker(parser_db, ops_db)
    count = worker.run_once()
    assert count == 1

    conn = sqlite3.connect(ops_db)
    chains = conn.execute("SELECT * FROM ops_trade_chains").fetchall()
    commands = conn.execute("SELECT command_type FROM ops_execution_commands").fetchall()
    conn.close()
    assert len(chains) == 1
    assert any(c[0] == "PLACE_ENTRY" for c in commands)


def test_worker_marks_lifecycle_processed(dbs):
    parser_db, ops_db = dbs
    enriched = _make_enriched_signal(enrichment_id=2)
    _insert_enriched(parser_db, 2, enriched)

    worker = _make_worker(parser_db, ops_db)
    worker.run_once()

    conn = sqlite3.connect(parser_db)
    row = conn.execute(
        "SELECT lifecycle_processed FROM enriched_canonical_messages WHERE enrichment_id=?", (2,)
    ).fetchone()
    conn.close()
    assert row[0] == 1


def test_worker_idempotent_on_double_run(dbs):
    parser_db, ops_db = dbs
    enriched = _make_enriched_signal(enrichment_id=3)
    _insert_enriched(parser_db, 3, enriched)

    worker = _make_worker(parser_db, ops_db)
    worker.run_once()
    worker.run_once()  # secondo run: niente da fare

    conn = sqlite3.connect(ops_db)
    chains = conn.execute("SELECT COUNT(*) FROM ops_trade_chains").fetchone()
    conn.close()
    assert chains[0] == 1


def test_worker_block_new_entries_produces_review(dbs):
    import sqlite3
    parser_db, ops_db = dbs
    enriched = _make_enriched_signal(enrichment_id=4)
    _insert_enriched(parser_db, 4, enriched)

    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(ops_db)
    conn.execute(
        "INSERT INTO ops_control_state (scope_type, execution_pause_mode, active, created_at, updated_at) VALUES (?,?,1,?,?)",
        ("GLOBAL", "BLOCK_NEW_ENTRIES", now, now),
    )
    conn.commit()
    conn.close()

    worker = _make_worker(parser_db, ops_db)
    worker.run_once()

    conn = sqlite3.connect(ops_db)
    chains = conn.execute("SELECT COUNT(*) FROM ops_trade_chains").fetchone()
    review_events = conn.execute(
        "SELECT COUNT(*) FROM ops_lifecycle_events WHERE event_type='REVIEW_REQUIRED'"
    ).fetchone()
    conn.close()
    assert chains[0] == 0
    assert review_events[0] == 1
```

- [ ] **Step 2: Esegui — devono fallire**

```
pytest tests/runtime_v2/lifecycle/test_workers.py::test_worker_processes_signal_creates_chain -v
```

Expected: FAIL — `LifecycleGateWorker` non esiste

- [ ] **Step 3: Aggiungi LifecycleGateWorker a entry_gate.py**

Aggiungi in fondo a `src/runtime_v2/lifecycle/entry_gate.py`:

```python
# Aggiungi dopo la class LifecycleEntryGate e prima di __all__

import sqlite3 as _sqlite3


class LifecycleGateWorker:
    def __init__(
        self,
        parser_db_path: str,
        ops_db_path: str,
        gate: LifecycleEntryGate,
        chain_repo,
        event_repo,
        command_repo,
        snapshot_repo,
        control_repo,
    ) -> None:
        self._parser_db = parser_db_path
        self._ops_db = ops_db_path
        self._gate = gate
        self._chain_repo = chain_repo
        self._event_repo = event_repo
        self._command_repo = command_repo
        self._snapshot_repo = snapshot_repo
        self._control_repo = control_repo

    def run_once(self, batch_size: int = 50) -> int:
        rows = self._fetch_pending(batch_size)
        processed = 0
        for row in rows:
            try:
                self._process_row(row)
                processed += 1
            except Exception:
                logger.exception("error processing enrichment_id=%s", row[0])
        return processed

    def _fetch_pending(self, limit: int) -> list[tuple]:
        conn = _sqlite3.connect(self._parser_db)
        try:
            return conn.execute(
                """
                SELECT enrichment_id, canonical_message_id, raw_message_id, trader_id, account_id,
                       primary_class, enrichment_decision, enriched_signal_json,
                       enriched_actions_json, management_plan_json, policy_snapshot_json
                FROM enriched_canonical_messages
                WHERE lifecycle_processed=0
                  AND enrichment_decision='PASS'
                  AND primary_class IN ('SIGNAL','UPDATE')
                ORDER BY created_at
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        finally:
            conn.close()

    def _process_row(self, row: tuple) -> None:
        import json as _json
        from src.runtime_v2.signal_enrichment.models import (
            EnrichedCanonicalMessage, EnrichedSignalPayload, ManagementPlanConfig,
        )
        from src.parser_v2.contracts.canonical_message import TargetActionGroup

        (
            enrichment_id, canonical_message_id, raw_message_id, trader_id, account_id,
            primary_class, enrichment_decision, enriched_signal_json,
            enriched_actions_json, management_plan_json, policy_snapshot_json,
        ) = row

        enriched_signal = (
            EnrichedSignalPayload.model_validate_json(enriched_signal_json)
            if enriched_signal_json else None
        )
        enriched_actions = None
        if enriched_actions_json:
            enriched_actions = [
                TargetActionGroup.model_validate(a)
                for a in _json.loads(enriched_actions_json)
            ]
        management_plan = (
            ManagementPlanConfig.model_validate_json(management_plan_json)
            if management_plan_json else ManagementPlanConfig()
        )

        enriched = EnrichedCanonicalMessage(
            enrichment_id=enrichment_id,
            canonical_message_id=canonical_message_id,
            raw_message_id=raw_message_id,
            trader_id=trader_id,
            account_id=account_id,
            primary_class=primary_class,
            enrichment_decision=enrichment_decision,
            enriched_signal=enriched_signal,
            enriched_actions=enriched_actions,
            management_plan=management_plan,
            policy_snapshot=_json.loads(policy_snapshot_json or "{}"),
        )

        open_chains = self._chain_repo.get_active_by_trader(trader_id)
        symbol = enriched_signal.symbol or "" if enriched_signal else ""
        side = enriched_signal.side or "" if enriched_signal else ""
        control_mode = self._control_repo.get_effective_mode(account_id, trader_id, symbol, side)

        if primary_class == "SIGNAL":
            result = self._gate.process_signal(enriched, open_chains, control_mode)
            self._persist_signal(enriched, result)
        else:
            active_cmds = {
                c.trade_chain_id: self._command_repo.get_active_for_chain(c.trade_chain_id)
                for c in open_chains
            }
            result = self._gate.process_update(enriched, open_chains, active_cmds)
            self._persist_update(enriched, result)

    def _persist_signal(self, enriched: EnrichedCanonicalMessage, result: SignalGateResult) -> None:
        now = datetime.now(timezone.utc).isoformat()
        conn = _sqlite3.connect(self._ops_db)
        try:
            with conn:
                chain_id = None
                if result.trade_chain is not None:
                    c = result.trade_chain
                    cursor = conn.execute(
                        """
                        INSERT OR IGNORE INTO ops_trade_chains (
                            source_enrichment_id, canonical_message_id, raw_message_id,
                            trader_id, account_id, symbol, side, lifecycle_state, entry_mode,
                            entry_avg_price, current_stop_price, expected_stop_price,
                            be_protection_status, entry_timeout_at, management_plan_json,
                            risk_snapshot_json, created_at, updated_at
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            c.source_enrichment_id, c.canonical_message_id, c.raw_message_id,
                            c.trader_id, c.account_id, c.symbol, c.side,
                            c.lifecycle_state, c.entry_mode,
                            c.entry_avg_price, c.current_stop_price, c.expected_stop_price,
                            c.be_protection_status,
                            c.entry_timeout_at.isoformat() if c.entry_timeout_at else None,
                            c.management_plan_json, c.risk_snapshot_json, now, now,
                        ),
                    )
                    if cursor.lastrowid and cursor.rowcount > 0:
                        chain_id = cursor.lastrowid
                    else:
                        row = conn.execute(
                            "SELECT trade_chain_id FROM ops_trade_chains WHERE source_enrichment_id=?",
                            (c.source_enrichment_id,),
                        ).fetchone()
                        chain_id = row[0] if row else None

                for event in result.lifecycle_events:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO ops_lifecycle_events (
                            trade_chain_id, event_type, source_type, source_id,
                            previous_state, next_state, payload_json, idempotency_key, created_at
                        ) VALUES (?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            chain_id, event.event_type, event.source_type, event.source_id,
                            event.previous_state, event.next_state, event.payload_json,
                            event.idempotency_key, now,
                        ),
                    )

                for cmd in result.execution_commands:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO ops_execution_commands (
                            trade_chain_id, command_type, status, payload_json,
                            idempotency_key, created_at, updated_at
                        ) VALUES (?,?,?,?,?,?,?)
                        """,
                        (
                            chain_id, cmd.command_type, cmd.status, cmd.payload_json,
                            cmd.idempotency_key, now, now,
                        ),
                    )

                if result.account_snapshot:
                    s = result.account_snapshot
                    conn.execute(
                        """
                        INSERT INTO ops_account_snapshots (
                            account_id, equity_usdt, available_balance_usdt,
                            total_open_risk_usdt, total_margin_used_usdt,
                            source, captured_at, payload_json
                        ) VALUES (?,?,?,?,?,?,?,?)
                        """,
                        (
                            enriched.account_id, s.equity_usdt, s.available_balance_usdt,
                            s.total_open_risk_usdt, s.total_margin_used_usdt,
                            s.source, s.captured_at.isoformat(), "{}",
                        ),
                    )

                if result.market_snapshot:
                    s = result.market_snapshot
                    conn.execute(
                        """
                        INSERT INTO ops_market_snapshots (
                            account_id, symbol, mark_price, bid, ask, min_order_size,
                            price_precision, qty_precision, source, captured_at, payload_json
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            enriched.account_id, s.symbol, s.mark_price, s.bid, s.ask,
                            s.min_order_size, s.price_precision, s.qty_precision,
                            s.source, s.captured_at.isoformat(), "{}",
                        ),
                    )
        finally:
            conn.close()

        self._mark_processed(enriched.enrichment_id)

    def _persist_update(self, enriched: EnrichedCanonicalMessage, result: UpdateGateResult) -> None:
        now = datetime.now(timezone.utc).isoformat()
        conn = _sqlite3.connect(self._ops_db)
        try:
            with conn:
                for cr in result.chain_results:
                    if cr.new_lifecycle_state or cr.new_be_protection_status:
                        fields = ["updated_at=?"]
                        vals: list = [now]
                        if cr.new_lifecycle_state:
                            fields.append("lifecycle_state=?")
                            vals.append(cr.new_lifecycle_state)
                        if cr.new_be_protection_status:
                            fields.append("be_protection_status=?")
                            vals.append(cr.new_be_protection_status)
                        vals.append(cr.trade_chain_id)
                        conn.execute(
                            f"UPDATE ops_trade_chains SET {', '.join(fields)} WHERE trade_chain_id=?",
                            vals,
                        )
                    for event in cr.lifecycle_events:
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO ops_lifecycle_events (
                                trade_chain_id, event_type, source_type, source_id,
                                previous_state, next_state, payload_json, idempotency_key, created_at
                            ) VALUES (?,?,?,?,?,?,?,?,?)
                            """,
                            (
                                cr.trade_chain_id, event.event_type, event.source_type, event.source_id,
                                event.previous_state, event.next_state, event.payload_json,
                                event.idempotency_key, now,
                            ),
                        )
                    for cmd in cr.execution_commands:
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO ops_execution_commands (
                                trade_chain_id, command_type, status, payload_json,
                                idempotency_key, created_at, updated_at
                            ) VALUES (?,?,?,?,?,?,?)
                            """,
                            (cr.trade_chain_id, cmd.command_type, cmd.status, cmd.payload_json,
                             cmd.idempotency_key, now, now),
                        )
                for event in result.review_events:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO ops_lifecycle_events (
                            trade_chain_id, event_type, source_type, source_id,
                            payload_json, idempotency_key, created_at
                        ) VALUES (?,?,?,?,?,?,?)
                        """,
                        (
                            None, event.event_type, event.source_type, event.source_id,
                            event.payload_json, event.idempotency_key, now,
                        ),
                    )
        finally:
            conn.close()

        self._mark_processed(enriched.enrichment_id)

    def _mark_processed(self, enrichment_id: int) -> None:
        conn = _sqlite3.connect(self._parser_db)
        try:
            conn.execute(
                "UPDATE enriched_canonical_messages SET lifecycle_processed=1 WHERE enrichment_id=?",
                (enrichment_id,),
            )
            conn.commit()
        finally:
            conn.close()
```

Aggiorna anche `__all__` alla fine del file:

```python
__all__ = [
    "LifecycleEntryGate", "LifecycleGateWorker",
    "SignalGateResult", "UpdateGateResult", "UpdateChainResult",
]
```

- [ ] **Step 4: Esegui i test del worker**

```
pytest tests/runtime_v2/lifecycle/test_workers.py -v
```

Expected: PASS (4 test)

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/lifecycle/entry_gate.py tests/runtime_v2/lifecycle/test_workers.py
git commit -m "feat(prd04): add LifecycleGateWorker with ops-first atomicity"
```

---

## Task 4: Lifecycle Event Processor

**Files:**
- Create: `src/runtime_v2/lifecycle/event_processor.py`
- Create: `tests/runtime_v2/lifecycle/test_event_processor.py`

- [ ] **Step 1: Scrivi i test**

```python
# tests/runtime_v2/lifecycle/test_event_processor.py
from __future__ import annotations

import json
from datetime import datetime, timezone


def _now():
    return datetime.now(timezone.utc)


def _make_exchange_event(
    *,
    event_id: int = 1,
    trade_chain_id: int = 1,
    event_type: str = "TP_FILLED",
    payload: dict | None = None,
):
    from src.runtime_v2.lifecycle.models import ExchangeEvent
    return ExchangeEvent(
        exchange_event_id=event_id,
        trade_chain_id=trade_chain_id,
        event_type=event_type,
        payload_json=json.dumps(payload or {}),
        idempotency_key=f"{event_type}:{trade_chain_id}:{event_id}",
        received_at=_now(),
    )


def _make_chain(
    *,
    trade_chain_id: int = 1,
    state: str = "OPEN",
    side: str = "LONG",
    entry_avg_price: float = 50000.0,
    current_stop_price: float = 49000.0,
    be_status: str = "NOT_PROTECTED",
    be_trigger: str | None = None,
):
    from src.runtime_v2.lifecycle.models import TradeChain
    from src.runtime_v2.signal_enrichment.models import ManagementPlanConfig
    mp = ManagementPlanConfig(be_trigger=be_trigger)
    return TradeChain(
        trade_chain_id=trade_chain_id,
        source_enrichment_id=trade_chain_id,
        canonical_message_id=trade_chain_id * 10,
        raw_message_id=trade_chain_id * 100,
        trader_id="trader_a", account_id="acc_1",
        symbol="BTC/USDT", side=side, lifecycle_state=state,
        entry_mode="ONE_SHOT", management_plan_json=mp.model_dump_json(),
        entry_avg_price=entry_avg_price,
        current_stop_price=current_stop_price,
        be_protection_status=be_status,
    )


def _make_processor():
    from src.runtime_v2.lifecycle.event_processor import LifecycleEventProcessor
    return LifecycleEventProcessor()


def test_entry_filled_transitions_to_open():
    proc = _make_processor()
    event = _make_exchange_event(event_type="ENTRY_FILLED",
                                  payload={"fill_price": 50100.0})
    chain = _make_chain(state="WAITING_ENTRY")
    result = proc.process(event, chain, [])
    assert result.new_lifecycle_state == "OPEN"
    assert result.entry_avg_price == 50100.0
    assert any(e.event_type == "ENTRY_FILLED" for e in result.lifecycle_events)


def test_tp_filled_not_final_transitions_to_partially_closed():
    proc = _make_processor()
    event = _make_exchange_event(event_type="TP_FILLED",
                                  payload={"tp_level": 1, "is_final": False})
    chain = _make_chain(state="OPEN")
    result = proc.process(event, chain, [])
    assert result.new_lifecycle_state == "PARTIALLY_CLOSED"
    assert any(e.event_type == "TP_FILLED" for e in result.lifecycle_events)


def test_tp_filled_final_transitions_to_closed():
    proc = _make_processor()
    event = _make_exchange_event(event_type="TP_FILLED",
                                  payload={"tp_level": 3, "is_final": True})
    chain = _make_chain(state="PARTIALLY_CLOSED")
    result = proc.process(event, chain, [])
    assert result.new_lifecycle_state == "CLOSED"


def test_tp_filled_be_trigger_creates_be_command():
    proc = _make_processor()
    event = _make_exchange_event(event_type="TP_FILLED",
                                  payload={"tp_level": 1, "is_final": False})
    chain = _make_chain(state="OPEN", be_trigger="tp1")
    result = proc.process(event, chain, [])
    assert any(c.command_type == "MOVE_STOP_TO_BREAKEVEN" for c in result.execution_commands)
    assert result.new_be_protection_status == "BE_MOVE_PENDING"


def test_tp_filled_be_trigger_already_protected_noop():
    proc = _make_processor()
    event = _make_exchange_event(event_type="TP_FILLED",
                                  payload={"tp_level": 1, "is_final": False})
    chain = _make_chain(state="OPEN", be_trigger="tp1", be_status="PROTECTED")
    result = proc.process(event, chain, [])
    assert not any(c.command_type == "MOVE_STOP_TO_BREAKEVEN" for c in result.execution_commands)
    assert any(e.event_type == "NOOP_ALREADY_PROTECTED_BE" for e in result.lifecycle_events)


def test_tp_filled_be_trigger_duplicate_command_noop():
    from src.runtime_v2.lifecycle.models import ExecutionCommand
    proc = _make_processor()
    event = _make_exchange_event(event_type="TP_FILLED",
                                  payload={"tp_level": 1, "is_final": False})
    chain = _make_chain(state="OPEN", be_trigger="tp1")
    existing = ExecutionCommand(
        trade_chain_id=1, command_type="MOVE_STOP_TO_BREAKEVEN",
        payload_json="{}", idempotency_key="move_be:1:old", status="PENDING",
    )
    result = proc.process(event, chain, [existing])
    assert not any(c.command_type == "MOVE_STOP_TO_BREAKEVEN" for c in result.execution_commands)
    assert any(e.event_type == "NOOP_DUPLICATE_COMMAND" for e in result.lifecycle_events)


def test_double_tp_filled_same_event_idempotency():
    proc = _make_processor()
    event = _make_exchange_event(event_id=5, event_type="TP_FILLED",
                                  payload={"tp_level": 1, "is_final": False})
    chain = _make_chain(state="OPEN", be_trigger="tp1")
    result1 = proc.process(event, chain, [])
    result2 = proc.process(event, chain, [])
    keys1 = {e.idempotency_key for e in result1.lifecycle_events}
    keys2 = {e.idempotency_key for e in result2.lifecycle_events}
    assert keys1 == keys2


def test_sl_filled_transitions_to_closed():
    proc = _make_processor()
    event = _make_exchange_event(event_type="SL_FILLED", payload={"fill_price": 48900.0})
    chain = _make_chain(state="OPEN")
    result = proc.process(event, chain, [])
    assert result.new_lifecycle_state == "CLOSED"
    assert any(e.event_type == "SL_FILLED" for e in result.lifecycle_events)
```

- [ ] **Step 2: Esegui — devono fallire**

```
pytest tests/runtime_v2/lifecycle/test_event_processor.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implementa event_processor.py**

```python
# src/runtime_v2/lifecycle/event_processor.py
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from src.runtime_v2.lifecycle.models import (
    BeProtectionStatus, ExecutionCommand, ExchangeEvent,
    LifecycleEvent, LifecycleState, TradeChain,
)
from src.runtime_v2.signal_enrichment.models import ManagementPlanConfig

logger = logging.getLogger(__name__)


@dataclass
class EventProcessorResult:
    new_lifecycle_state: LifecycleState | None
    new_be_protection_status: BeProtectionStatus | None
    entry_avg_price: float | None
    current_stop_price: float | None
    lifecycle_events: list[LifecycleEvent]
    execution_commands: list[ExecutionCommand]


class LifecycleEventProcessor:
    def process(
        self,
        exchange_event: ExchangeEvent,
        chain: TradeChain,
        active_commands: list[ExecutionCommand],
    ) -> EventProcessorResult:
        etype = exchange_event.event_type
        if etype == "ENTRY_FILLED":
            return self._process_entry_filled(exchange_event, chain)
        if etype == "TP_FILLED":
            return self._process_tp_filled(exchange_event, chain, active_commands)
        if etype == "SL_FILLED":
            return self._process_sl_filled(exchange_event, chain)
        logger.warning("unhandled exchange event type: %s", etype)
        return EventProcessorResult(
            new_lifecycle_state=None,
            new_be_protection_status=None,
            entry_avg_price=None,
            current_stop_price=None,
            lifecycle_events=[],
            execution_commands=[],
        )

    def _process_entry_filled(
        self, exchange_event: ExchangeEvent, chain: TradeChain
    ) -> EventProcessorResult:
        payload = json.loads(exchange_event.payload_json)
        fill_price = payload.get("fill_price")
        eid = exchange_event.exchange_event_id
        chain_id = chain.trade_chain_id
        return EventProcessorResult(
            new_lifecycle_state="OPEN",
            new_be_protection_status=None,
            entry_avg_price=fill_price,
            current_stop_price=None,
            lifecycle_events=[LifecycleEvent(
                trade_chain_id=chain_id,
                event_type="ENTRY_FILLED",
                source_type="exchange_event",
                source_id=str(eid),
                previous_state=chain.lifecycle_state,
                next_state="OPEN",
                payload_json=json.dumps({"fill_price": fill_price}),
                idempotency_key=f"entry_filled:{chain_id}:{eid}",
            )],
            execution_commands=[],
        )

    def _process_tp_filled(
        self,
        exchange_event: ExchangeEvent,
        chain: TradeChain,
        active_commands: list[ExecutionCommand],
    ) -> EventProcessorResult:
        payload = json.loads(exchange_event.payload_json)
        tp_level = payload.get("tp_level", 1)
        is_final = bool(payload.get("is_final", False))
        eid = exchange_event.exchange_event_id
        chain_id = chain.trade_chain_id

        new_state: LifecycleState = "CLOSED" if is_final else "PARTIALLY_CLOSED"
        events: list[LifecycleEvent] = [LifecycleEvent(
            trade_chain_id=chain_id,
            event_type="TP_FILLED",
            source_type="exchange_event",
            source_id=str(eid),
            previous_state=chain.lifecycle_state,
            next_state=new_state,
            payload_json=json.dumps({"tp_level": tp_level, "is_final": is_final}),
            idempotency_key=f"tp_filled:{chain_id}:{eid}",
        )]
        commands: list[ExecutionCommand] = []
        new_be: BeProtectionStatus | None = None

        if not is_final:
            try:
                mp = ManagementPlanConfig.model_validate_json(chain.management_plan_json)
            except Exception:
                mp = ManagementPlanConfig()
            be_trigger = mp.be_trigger
            if be_trigger and be_trigger == f"tp{tp_level}":
                if chain.be_protection_status == "PROTECTED":
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
                        events.append(LifecycleEvent(
                            trade_chain_id=chain_id,
                            event_type="BE_MOVE_REQUESTED",
                            source_type="exchange_event",
                            source_id=str(eid),
                            idempotency_key=f"be_req_tp:{chain_id}:{eid}",
                        ))
                        new_state = "BE_MOVE_PENDING"
                        new_be = "BE_MOVE_PENDING"

        return EventProcessorResult(
            new_lifecycle_state=new_state,
            new_be_protection_status=new_be,
            entry_avg_price=None,
            current_stop_price=None,
            lifecycle_events=events,
            execution_commands=commands,
        )

    def _process_sl_filled(
        self, exchange_event: ExchangeEvent, chain: TradeChain
    ) -> EventProcessorResult:
        eid = exchange_event.exchange_event_id
        chain_id = chain.trade_chain_id
        return EventProcessorResult(
            new_lifecycle_state="CLOSED",
            new_be_protection_status=None,
            entry_avg_price=None,
            current_stop_price=None,
            lifecycle_events=[LifecycleEvent(
                trade_chain_id=chain_id,
                event_type="SL_FILLED",
                source_type="exchange_event",
                source_id=str(eid),
                previous_state=chain.lifecycle_state,
                next_state="CLOSED",
                payload_json=exchange_event.payload_json,
                idempotency_key=f"sl_filled:{chain_id}:{eid}",
            )],
            execution_commands=[],
        )


__all__ = ["LifecycleEventProcessor", "EventProcessorResult"]
```

- [ ] **Step 4: Esegui i test**

```
pytest tests/runtime_v2/lifecycle/test_event_processor.py -v
```

Expected: PASS (8 test)

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/lifecycle/event_processor.py tests/runtime_v2/lifecycle/test_event_processor.py
git commit -m "feat(prd04): add LifecycleEventProcessor"
```

---

## Task 5: LifecycleEventWorker + TimeoutWorker

**Files:**
- Create: `src/runtime_v2/lifecycle/workers.py`
- Modify: `tests/runtime_v2/lifecycle/test_workers.py` (aggiungi test)

- [ ] **Step 1: Aggiungi i test TimeoutWorker e LifecycleEventWorker**

Aggiungi in fondo a `tests/runtime_v2/lifecycle/test_workers.py`:

```python
# Aggiungi dopo i test esistenti in test_workers.py

def _make_chain_in_db(ops_db: str, *, trade_chain_id_hint: int, state: str,
                      timeout_at_isoformat: str | None = None, symbol: str = "BTC/USDT") -> int:
    import sqlite3
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(ops_db)
    cursor = conn.execute(
        """
        INSERT INTO ops_trade_chains (
            source_enrichment_id, canonical_message_id, raw_message_id,
            trader_id, account_id, symbol, side, lifecycle_state, entry_mode,
            management_plan_json, created_at, updated_at, entry_timeout_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            trade_chain_id_hint, trade_chain_id_hint * 10, trade_chain_id_hint * 100,
            "trader_a", "acc_1", symbol, "LONG", state, "ONE_SHOT",
            "{}", now, now, timeout_at_isoformat,
        ),
    )
    conn.commit()
    inserted_id = cursor.lastrowid
    conn.close()
    return inserted_id


def test_timeout_worker_expires_waiting_entry(dbs):
    import sqlite3
    from datetime import datetime, timedelta, timezone
    _, ops_db = dbs
    past_timeout = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    chain_id = _make_chain_in_db(ops_db, trade_chain_id_hint=50, state="WAITING_ENTRY",
                                  timeout_at_isoformat=past_timeout)

    from src.runtime_v2.lifecycle.repositories import TradeChainRepository
    from src.runtime_v2.lifecycle.workers import TimeoutWorker
    worker = TimeoutWorker(ops_db_path=ops_db, chain_repo=TradeChainRepository(ops_db))
    count = worker.run_once()
    assert count == 1

    conn = sqlite3.connect(ops_db)
    state = conn.execute(
        "SELECT lifecycle_state FROM ops_trade_chains WHERE trade_chain_id=?", (chain_id,)
    ).fetchone()[0]
    cmds = conn.execute(
        "SELECT command_type FROM ops_execution_commands WHERE trade_chain_id=?", (chain_id,)
    ).fetchall()
    events = conn.execute(
        "SELECT event_type FROM ops_lifecycle_events WHERE trade_chain_id=?", (chain_id,)
    ).fetchall()
    conn.close()

    assert state == "EXPIRED"
    assert any(c[0] == "CANCEL_PENDING_ENTRY" for c in cmds)
    assert any(e[0] == "TIMEOUT_REACHED" for e in events)


def test_timeout_worker_ignores_future_timeout(dbs):
    from datetime import datetime, timedelta, timezone
    _, ops_db = dbs
    future_timeout = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
    _make_chain_in_db(ops_db, trade_chain_id_hint=51, state="WAITING_ENTRY",
                       timeout_at_isoformat=future_timeout)

    from src.runtime_v2.lifecycle.repositories import TradeChainRepository
    from src.runtime_v2.lifecycle.workers import TimeoutWorker
    worker = TimeoutWorker(ops_db_path=ops_db, chain_repo=TradeChainRepository(ops_db))
    count = worker.run_once()
    assert count == 0


def test_timeout_worker_idempotent(dbs):
    import sqlite3
    from datetime import datetime, timedelta, timezone
    _, ops_db = dbs
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    chain_id = _make_chain_in_db(ops_db, trade_chain_id_hint=52, state="WAITING_ENTRY",
                                  timeout_at_isoformat=past)

    from src.runtime_v2.lifecycle.repositories import TradeChainRepository
    from src.runtime_v2.lifecycle.workers import TimeoutWorker
    worker = TimeoutWorker(ops_db_path=ops_db, chain_repo=TradeChainRepository(ops_db))
    worker.run_once()
    worker.run_once()  # secondo run non trova WAITING_ENTRY scaduto

    conn = sqlite3.connect(ops_db)
    cmd_count = conn.execute(
        "SELECT COUNT(*) FROM ops_execution_commands WHERE trade_chain_id=?", (chain_id,)
    ).fetchone()[0]
    conn.close()
    assert cmd_count == 1  # no duplicati


def test_lifecycle_event_worker_processes_tp_filled(dbs):
    import sqlite3
    import json
    from datetime import datetime, timezone
    _, ops_db = dbs
    chain_id = _make_chain_in_db(ops_db, trade_chain_id_hint=60, state="OPEN")

    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(ops_db)
    conn.execute(
        """
        INSERT INTO ops_exchange_events (trade_chain_id, event_type, payload_json,
            processing_status, idempotency_key, received_at)
        VALUES (?,?,?,?,?,?)
        """,
        (chain_id, "TP_FILLED", json.dumps({"tp_level": 1, "is_final": False}),
         "NEW", f"tp_filled:{chain_id}:1", now),
    )
    conn.commit()
    conn.close()

    from src.runtime_v2.lifecycle.event_processor import LifecycleEventProcessor
    from src.runtime_v2.lifecycle.repositories import (
        ExecutionCommandRepository, ExchangeEventRepository,
        LifecycleEventRepository, TradeChainRepository,
    )
    from src.runtime_v2.lifecycle.workers import LifecycleEventWorker

    worker = LifecycleEventWorker(
        ops_db_path=ops_db,
        processor=LifecycleEventProcessor(),
        chain_repo=TradeChainRepository(ops_db),
        event_repo=LifecycleEventRepository(ops_db),
        command_repo=ExecutionCommandRepository(ops_db),
        exchange_event_repo=ExchangeEventRepository(ops_db),
    )
    count = worker.run_once()
    assert count == 1

    conn = sqlite3.connect(ops_db)
    state = conn.execute(
        "SELECT lifecycle_state FROM ops_trade_chains WHERE trade_chain_id=?", (chain_id,)
    ).fetchone()[0]
    status = conn.execute(
        "SELECT processing_status FROM ops_exchange_events WHERE trade_chain_id=?", (chain_id,)
    ).fetchone()[0]
    conn.close()
    assert state == "PARTIALLY_CLOSED"
    assert status == "DONE"
```

- [ ] **Step 2: Esegui — devono fallire**

```
pytest tests/runtime_v2/lifecycle/test_workers.py -k "timeout or lifecycle_event" -v
```

Expected: FAIL — `workers.py` non esiste

- [ ] **Step 3: Implementa workers.py**

```python
# src/runtime_v2/lifecycle/workers.py
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone

from src.runtime_v2.lifecycle.event_processor import EventProcessorResult, LifecycleEventProcessor
from src.runtime_v2.lifecycle.models import TERMINAL_STATES, ExecutionCommand, LifecycleEvent
from src.runtime_v2.lifecycle.repositories import (
    ExecutionCommandRepository, ExchangeEventRepository,
    LifecycleEventRepository, TradeChainRepository,
)

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TimeoutWorker:
    def __init__(self, ops_db_path: str, chain_repo: TradeChainRepository) -> None:
        self._ops_db = ops_db_path
        self._chain_repo = chain_repo

    def run_once(self, batch_size: int = 100) -> int:
        chains = self._chain_repo.get_timed_out_waiting_entry(batch_size)
        processed = 0
        for chain in chains:
            try:
                self._process_timeout(chain)
                processed += 1
            except Exception:
                logger.exception("timeout error for chain %s", chain.trade_chain_id)
        return processed

    def _process_timeout(self, chain) -> None:
        chain_id = chain.trade_chain_id
        now = _now()
        conn = sqlite3.connect(self._ops_db)
        try:
            with conn:
                conn.execute(
                    "UPDATE ops_trade_chains SET lifecycle_state='EXPIRED', updated_at=? WHERE trade_chain_id=?",
                    (now, chain_id),
                )
                conn.execute(
                    """
                    INSERT OR IGNORE INTO ops_lifecycle_events (
                        trade_chain_id, event_type, source_type,
                        previous_state, next_state, payload_json, idempotency_key, created_at
                    ) VALUES (?,?,?,?,?,?,?,?)
                    """,
                    (chain_id, "TIMEOUT_REACHED", "timeout_worker",
                     "WAITING_ENTRY", "EXPIRED", "{}", f"timeout:{chain_id}", now),
                )
                conn.execute(
                    """
                    INSERT OR IGNORE INTO ops_execution_commands (
                        trade_chain_id, command_type, status, payload_json,
                        idempotency_key, created_at, updated_at
                    ) VALUES (?,?,?,?,?,?,?)
                    """,
                    (chain_id, "CANCEL_PENDING_ENTRY", "PENDING",
                     f'{{"symbol": "{chain.symbol}", "side": "{chain.side}"}}',
                     f"cancel_timeout:{chain_id}", now, now),
                )
        finally:
            conn.close()


class LifecycleEventWorker:
    def __init__(
        self,
        ops_db_path: str,
        processor: LifecycleEventProcessor,
        chain_repo: TradeChainRepository,
        event_repo: LifecycleEventRepository,
        command_repo: ExecutionCommandRepository,
        exchange_event_repo: ExchangeEventRepository,
    ) -> None:
        self._ops_db = ops_db_path
        self._processor = processor
        self._chain_repo = chain_repo
        self._event_repo = event_repo
        self._command_repo = command_repo
        self._exchange_event_repo = exchange_event_repo

    def run_once(self, batch_size: int = 100) -> int:
        events = self._exchange_event_repo.get_new_events(batch_size)
        processed = 0
        for exchange_event in events:
            try:
                if exchange_event.trade_chain_id is None:
                    self._exchange_event_repo.mark_processed(exchange_event.exchange_event_id)
                    processed += 1
                    continue

                chain = self._chain_repo.get_by_id(exchange_event.trade_chain_id)
                if chain is None or chain.lifecycle_state in TERMINAL_STATES:
                    self._exchange_event_repo.mark_processed(exchange_event.exchange_event_id)
                    processed += 1
                    continue

                active_commands = self._command_repo.get_active_for_chain(chain.trade_chain_id)
                result = self._processor.process(exchange_event, chain, active_commands)
                self._persist_result(chain.trade_chain_id, result)
                self._exchange_event_repo.mark_processed(exchange_event.exchange_event_id)
                processed += 1
            except Exception:
                logger.exception("error processing exchange_event %s", exchange_event.exchange_event_id)
        return processed

    def _persist_result(self, chain_id: int, result: EventProcessorResult) -> None:
        now = _now()
        conn = sqlite3.connect(self._ops_db)
        try:
            with conn:
                if result.new_lifecycle_state or result.new_be_protection_status or \
                   result.entry_avg_price is not None or result.current_stop_price is not None:
                    fields = ["updated_at=?"]
                    vals: list = [now]
                    if result.new_lifecycle_state:
                        fields.append("lifecycle_state=?")
                        vals.append(result.new_lifecycle_state)
                    if result.new_be_protection_status:
                        fields.append("be_protection_status=?")
                        vals.append(result.new_be_protection_status)
                    if result.entry_avg_price is not None:
                        fields.append("entry_avg_price=?")
                        vals.append(result.entry_avg_price)
                    if result.current_stop_price is not None:
                        fields.append("current_stop_price=?")
                        vals.append(result.current_stop_price)
                    vals.append(chain_id)
                    conn.execute(
                        f"UPDATE ops_trade_chains SET {', '.join(fields)} WHERE trade_chain_id=?",
                        vals,
                    )

                for event in result.lifecycle_events:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO ops_lifecycle_events (
                            trade_chain_id, event_type, source_type, source_id,
                            previous_state, next_state, payload_json, idempotency_key, created_at
                        ) VALUES (?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            chain_id, event.event_type, event.source_type, event.source_id,
                            event.previous_state, event.next_state, event.payload_json,
                            event.idempotency_key, now,
                        ),
                    )

                for cmd in result.execution_commands:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO ops_execution_commands (
                            trade_chain_id, command_type, status, payload_json,
                            idempotency_key, created_at, updated_at
                        ) VALUES (?,?,?,?,?,?,?)
                        """,
                        (chain_id, cmd.command_type, cmd.status, cmd.payload_json,
                         cmd.idempotency_key, now, now),
                    )
        finally:
            conn.close()


__all__ = ["TimeoutWorker", "LifecycleEventWorker"]
```

- [ ] **Step 4: Esegui tutti i test workers**

```
pytest tests/runtime_v2/lifecycle/test_workers.py -v
```

Expected: PASS (tutti)

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/lifecycle/workers.py tests/runtime_v2/lifecycle/test_workers.py
git commit -m "feat(prd04): add TimeoutWorker + LifecycleEventWorker"
```

---

## Task 6: Integration Tests — Acceptance Contract

**Files:**
- Create: `tests/runtime_v2/lifecycle/test_integration.py`

- [ ] **Step 1: Scrivi i test di acceptance**

```python
# tests/runtime_v2/lifecycle/test_integration.py
"""
Acceptance contract per PRD-04 — verifica criteri pass/fail §14 della spec.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# [Incolla _apply_migrations, _now, _make_enriched_signal dal blocco helper]


@pytest.fixture
def dbs(tmp_path):
    parser_db = str(tmp_path / "parser.sqlite3")
    ops_db = str(tmp_path / "ops.sqlite3")
    for db in (parser_db, ops_db):
        conn = sqlite3.connect(db)
        for f in sorted(Path("db/migrations").glob("*.sql")):
            conn.executescript(f.read_text(encoding="utf-8"))
        conn.commit()
        conn.close()
    return parser_db, ops_db


def _insert_enriched_row(parser_db: str, enrichment_id: int, enriched) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(parser_db)
    conn.execute(
        """
        INSERT INTO enriched_canonical_messages (
            enrichment_id, canonical_message_id, raw_message_id, trader_id, account_id,
            primary_class, enrichment_decision, enriched_signal_json,
            management_plan_json, policy_snapshot_json, lifecycle_processed, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,0,?)
        """,
        (
            enrichment_id, enriched.canonical_message_id, enriched.raw_message_id,
            enriched.trader_id, enriched.account_id, enriched.primary_class,
            enriched.enrichment_decision,
            enriched.enriched_signal.model_dump_json() if enriched.enriched_signal else None,
            enriched.management_plan.model_dump_json() if enriched.management_plan else "{}",
            json.dumps(enriched.policy_snapshot), now,
        ),
    )
    conn.commit()
    conn.close()


def _make_full_worker(parser_db, ops_db):
    from src.runtime_v2.lifecycle.entry_gate import LifecycleEntryGate, LifecycleGateWorker
    from src.runtime_v2.lifecycle.repositories import (
        ControlStateRepository, ExecutionCommandRepository,
        LifecycleEventRepository, SnapshotRepository, TradeChainRepository,
    )
    from src.runtime_v2.lifecycle.risk_capacity import RiskCapacityEngine
    from src.runtime_v2.lifecycle.static_exchange_data_port import StaticExchangeDataPort
    gate = LifecycleEntryGate(RiskCapacityEngine(), StaticExchangeDataPort())
    return LifecycleGateWorker(
        parser_db_path=parser_db, ops_db_path=ops_db, gate=gate,
        chain_repo=TradeChainRepository(ops_db),
        event_repo=LifecycleEventRepository(ops_db),
        command_repo=ExecutionCommandRepository(ops_db),
        snapshot_repo=SnapshotRepository(ops_db),
        control_repo=ControlStateRepository(ops_db),
    )


# Criterio 1: SIGNAL PASS crea una sola ops_trade_chains idempotente
def test_ac1_signal_pass_creates_one_chain(dbs):
    parser_db, ops_db = dbs
    enriched = _make_enriched_signal(enrichment_id=1)
    _insert_enriched_row(parser_db, 1, enriched)
    worker = _make_full_worker(parser_db, ops_db)
    worker.run_once()
    conn = sqlite3.connect(ops_db)
    count = conn.execute("SELECT COUNT(*) FROM ops_trade_chains").fetchone()[0]
    conn.close()
    assert count == 1


# Criterio 2: Risk/capacity → REVIEW_REQUIRED
def test_ac2_risk_fail_produces_review(dbs):
    parser_db, ops_db = dbs
    enriched = _make_enriched_signal(enrichment_id=2, max_concurrent_trades=0)
    _insert_enriched_row(parser_db, 2, enriched)
    worker = _make_full_worker(parser_db, ops_db)
    worker.run_once()
    conn = sqlite3.connect(ops_db)
    chains = conn.execute("SELECT COUNT(*) FROM ops_trade_chains").fetchone()[0]
    reviews = conn.execute(
        "SELECT COUNT(*) FROM ops_lifecycle_events WHERE event_type='REVIEW_REQUIRED'"
    ).fetchone()[0]
    conn.close()
    assert chains == 0
    assert reviews == 1


# Criterio 3: SIGNAL valido crea PLACE_ENTRY, PLACE_PROTECTIVE_STOP, PLACE_TAKE_PROFIT
def test_ac3_signal_creates_neutral_commands(dbs):
    parser_db, ops_db = dbs
    enriched = _make_enriched_signal(enrichment_id=3)
    _insert_enriched_row(parser_db, 3, enriched)
    worker = _make_full_worker(parser_db, ops_db)
    worker.run_once()
    conn = sqlite3.connect(ops_db)
    cmd_types = {r[0] for r in conn.execute("SELECT command_type FROM ops_execution_commands").fetchall()}
    conn.close()
    assert "PLACE_ENTRY" in cmd_types
    assert "PLACE_PROTECTIVE_STOP" in cmd_types
    assert "PLACE_TAKE_PROFIT" in cmd_types


# Criterio 6: Timeout su WAITING_ENTRY → CANCEL_PENDING_ENTRY + EXPIRED
def test_ac6_timeout_produces_cancel_and_expired(dbs):
    _, ops_db = dbs
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    conn = sqlite3.connect(ops_db)
    conn.execute(
        """
        INSERT INTO ops_trade_chains (
            source_enrichment_id, canonical_message_id, raw_message_id, trader_id,
            account_id, symbol, side, lifecycle_state, entry_mode,
            management_plan_json, created_at, updated_at, entry_timeout_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (10, 100, 1000, "trader_a", "acc_1", "BTC/USDT", "LONG", "WAITING_ENTRY", "ONE_SHOT",
         "{}", past, past, past),
    )
    conn.commit()
    chain_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()

    from src.runtime_v2.lifecycle.repositories import TradeChainRepository
    from src.runtime_v2.lifecycle.workers import TimeoutWorker
    TimeoutWorker(ops_db, TradeChainRepository(ops_db)).run_once()

    conn = sqlite3.connect(ops_db)
    state = conn.execute(
        "SELECT lifecycle_state FROM ops_trade_chains WHERE trade_chain_id=?", (chain_id,)
    ).fetchone()[0]
    cmds = [r[0] for r in conn.execute(
        "SELECT command_type FROM ops_execution_commands WHERE trade_chain_id=?", (chain_id,)
    ).fetchall()]
    conn.close()
    assert state == "EXPIRED"
    assert "CANCEL_PENDING_ENTRY" in cmds


# Criterio 7: idempotency_key su tutti i lifecycle_events
def test_ac7_all_events_have_idempotency_key(dbs):
    parser_db, ops_db = dbs
    enriched = _make_enriched_signal(enrichment_id=7)
    _insert_enriched_row(parser_db, 7, enriched)
    worker = _make_full_worker(parser_db, ops_db)
    worker.run_once()
    conn = sqlite3.connect(ops_db)
    rows = conn.execute("SELECT idempotency_key FROM ops_lifecycle_events").fetchall()
    conn.close()
    assert len(rows) > 0
    assert all(r[0] is not None and len(r[0]) > 0 for r in rows)


# Criterio 8: nessun import Hummingbot
def test_ac8_no_hummingbot_imports():
    import importlib
    import pkgutil
    import src.runtime_v2.lifecycle as pkg
    for _, name, _ in pkgutil.walk_packages(pkg.__path__, prefix=pkg.__name__ + "."):
        try:
            mod = importlib.import_module(name)
            src_file = getattr(mod, "__file__", "") or ""
            if "lifecycle" in src_file:
                assert "hummingbot" not in src_file.lower()
        except ImportError:
            pass


# Criterio 9: lifecycle_processed=1 dopo processamento
def test_ac9_lifecycle_processed_marked(dbs):
    parser_db, ops_db = dbs
    enriched = _make_enriched_signal(enrichment_id=9)
    _insert_enriched_row(parser_db, 9, enriched)
    worker = _make_full_worker(parser_db, ops_db)
    worker.run_once()
    conn = sqlite3.connect(parser_db)
    val = conn.execute(
        "SELECT lifecycle_processed FROM enriched_canonical_messages WHERE enrichment_id=?", (9,)
    ).fetchone()[0]
    conn.close()
    assert val == 1


# Criterio 16: riprocessamento stesso enrichment non crea duplicati
def test_ac16_idempotency_guard_on_double_processing(dbs):
    parser_db, ops_db = dbs
    enriched = _make_enriched_signal(enrichment_id=16)
    _insert_enriched_row(parser_db, 16, enriched)

    # Simula crash dopo ops ma prima del mark: lifecycle_processed resta 0
    conn = sqlite3.connect(parser_db)
    conn.execute(
        "UPDATE enriched_canonical_messages SET lifecycle_processed=0 WHERE enrichment_id=?", (16,)
    )
    conn.commit()
    conn.close()

    worker = _make_full_worker(parser_db, ops_db)
    worker.run_once()  # primo run: scrive ops
    # Ripristina lifecycle_processed=0 per simulare secondo tentativo
    conn = sqlite3.connect(parser_db)
    conn.execute(
        "UPDATE enriched_canonical_messages SET lifecycle_processed=0 WHERE enrichment_id=?", (16,)
    )
    conn.commit()
    conn.close()
    worker.run_once()  # secondo run: INSERT OR IGNORE blocca duplicati

    conn = sqlite3.connect(ops_db)
    chains = conn.execute("SELECT COUNT(*) FROM ops_trade_chains WHERE source_enrichment_id=16").fetchone()[0]
    conn.close()
    assert chains == 1


# Criterio 17: TP_FILLED non finale → PARTIALLY_CLOSED, finale → CLOSED
def test_ac17_tp_filled_state_transitions(dbs):
    _, ops_db = dbs
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(ops_db)
    conn.execute(
        """
        INSERT INTO ops_trade_chains (
            source_enrichment_id, canonical_message_id, raw_message_id, trader_id,
            account_id, symbol, side, lifecycle_state, entry_mode,
            management_plan_json, risk_snapshot_json, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (20, 200, 2000, "trader_a", "acc_1", "BTC/USDT", "LONG", "OPEN", "ONE_SHOT",
         "{}", "{}", now, now),
    )
    conn.commit()
    chain_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    conn.execute(
        """
        INSERT INTO ops_exchange_events (trade_chain_id, event_type, payload_json,
            processing_status, idempotency_key, received_at)
        VALUES (?,?,?,?,?,?)
        """,
        (chain_id, "TP_FILLED", json.dumps({"tp_level": 1, "is_final": False}),
         "NEW", f"tp1:{chain_id}", now),
    )
    conn.commit()
    conn.close()

    from src.runtime_v2.lifecycle.event_processor import LifecycleEventProcessor
    from src.runtime_v2.lifecycle.repositories import (
        ExecutionCommandRepository, ExchangeEventRepository,
        LifecycleEventRepository, TradeChainRepository,
    )
    from src.runtime_v2.lifecycle.workers import LifecycleEventWorker
    worker = LifecycleEventWorker(
        ops_db_path=ops_db,
        processor=LifecycleEventProcessor(),
        chain_repo=TradeChainRepository(ops_db),
        event_repo=LifecycleEventRepository(ops_db),
        command_repo=ExecutionCommandRepository(ops_db),
        exchange_event_repo=ExchangeEventRepository(ops_db),
    )
    worker.run_once()

    conn = sqlite3.connect(ops_db)
    state = conn.execute(
        "SELECT lifecycle_state FROM ops_trade_chains WHERE trade_chain_id=?", (chain_id,)
    ).fetchone()[0]
    conn.close()
    assert state == "PARTIALLY_CLOSED"

    # Ora evento finale
    conn = sqlite3.connect(ops_db)
    conn.execute(
        """
        INSERT INTO ops_exchange_events (trade_chain_id, event_type, payload_json,
            processing_status, idempotency_key, received_at)
        VALUES (?,?,?,?,?,?)
        """,
        (chain_id, "TP_FILLED", json.dumps({"tp_level": 3, "is_final": True}),
         "NEW", f"tp3_final:{chain_id}", now),
    )
    conn.commit()
    conn.close()
    worker.run_once()

    conn = sqlite3.connect(ops_db)
    state2 = conn.execute(
        "SELECT lifecycle_state FROM ops_trade_chains WHERE trade_chain_id=?", (chain_id,)
    ).fetchone()[0]
    conn.close()
    assert state2 == "CLOSED"
```

- [ ] **Step 2: Esegui i test di acceptance**

```
pytest tests/runtime_v2/lifecycle/test_integration.py -v
```

Expected: PASS (tutti)

- [ ] **Step 3: Esegui l'intera suite lifecycle**

```
pytest tests/runtime_v2/lifecycle/ -v --tb=short
```

Expected: PASS (tutti i test dei due piani)

- [ ] **Step 4: Commit finale**

```bash
git add tests/runtime_v2/lifecycle/test_integration.py
git commit -m "test(prd04): add acceptance contract integration tests"
```

---

## Verifica finale Piano 2

```
pytest tests/runtime_v2/lifecycle/ -v --tb=short
```

Expected: tutti i test passano.

```python
# Verifica import isolation
import src.runtime_v2.lifecycle.entry_gate
import src.runtime_v2.lifecycle.event_processor
import src.runtime_v2.lifecycle.workers
# nessuna ImportError relativa a hummingbot/exchange SDK
```

```bash
git log --oneline -6
```

Deve mostrare i 6 commit di questo piano.
