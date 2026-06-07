from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest


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
    entry_structure: str = "ONE_SHOT",
    entry_type: str = "LIMIT",
    entry_price: float = 50000.0,
    sl_price: float = 49000.0,
    tp_prices: list[float] | None = None,
    entries: list[dict] | None = None,
    range_derivation: dict | None = None,
    capital_base_usdt: float = 1000.0,
    risk_pct: float = 1.0,
    max_concurrent_trades: int = 5,
    max_concurrent_same_symbol: int = 1,
    be_trigger: str | None = None,
    risk_hint=None,
    use_trader_risk_hint: bool = False,
):
    from src.parser_v2.contracts.entities import Price, StopLoss, TakeProfit
    from src.runtime_v2.signal_enrichment.models import (
        CloseDistributionConfig, EffectiveEnrichmentConfig, EnrichedCanonicalMessage,
        EnrichedEntryLeg, EnrichedSignalPayload, EntrySplitConfig,
        EntryWeightsConfig, EntryRangeConfig,
        LimitEntrySplitConfig, ManagementPlanConfig, MarketEntrySplitConfig,
        MarketExecutionConfig, PriceCorrectionsConfig, PriceSanityConfig,
        RangeDerivation,
        RiskConfig, SignalPolicyConfig, SlConfig, TpConfig,
    )

    entry_legs = [
        EnrichedEntryLeg(
            sequence=leg["sequence"],
            entry_type=leg["entry_type"],
            price=(
                Price(raw=str(leg["price"]), value=leg["price"])
                if leg.get("entry_type") == "LIMIT" and leg.get("price") is not None
                else None
            ),
            weight=leg.get("weight", 1.0),
        )
        for leg in (
            entries
            or [{
                "sequence": 1,
                "entry_type": entry_type,
                "price": entry_price,
                "weight": 1.0,
            }]
        )
    ]
    tps = [
        TakeProfit(sequence=i + 1, price=Price(raw=str(p), value=p))
        for i, p in enumerate(tp_prices or [51000.0])
    ]
    sl = StopLoss(price=Price(raw=str(sl_price), value=sl_price))
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
            use_trader_risk_hint=use_trader_risk_hint,
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


def _make_gate():
    from src.runtime_v2.lifecycle.entry_gate import LifecycleEntryGate
    from src.runtime_v2.lifecycle.risk_capacity import RiskCapacityEngine
    from src.runtime_v2.lifecycle.static_exchange_data_port import StaticExchangeDataPort
    return LifecycleEntryGate(
        risk_engine=RiskCapacityEngine(),
        exchange_port=StaticExchangeDataPort(),
        simple_attached_enabled=False,
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
    # D mode (simple_attached_enabled=False) generates SET_POSITION_TPSL_FULL instead of
    # PLACE_PROTECTIVE_STOP + PLACE_TAKE_PROFIT (legacy a_sequential behavior)
    assert any(c.command_type == "SET_POSITION_TPSL_FULL" for c in result.execution_commands)


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


def test_gate_signal_range_endpoints_persists_two_step_entry_mode():
    gate = _make_gate()
    enriched = _make_enriched_signal(
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
    result = gate.process_signal(enriched, [], "NONE")
    assert result.trade_chain.entry_mode == "TWO_STEP"


def test_gate_signal_copies_range_derivation_into_plan_state_json():
    gate = _make_gate()
    enriched = _make_enriched_signal(
        entry_structure="ONE_SHOT",
        entries=[
            {"sequence": 1, "entry_type": "LIMIT", "price": 64500.0, "weight": 1.0},
        ],
        range_derivation={
            "derived_from_range": True,
            "split_mode": "midpoint",
            "original_min_price": 64000.0,
            "original_max_price": 65000.0,
        },
    )
    result = gate.process_signal(enriched, [], "NONE")
    plan = json.loads(result.trade_chain.plan_state_json)
    assert plan["range_derivation"]["derived_from_range"] is True
    assert plan["range_derivation"]["split_mode"] == "midpoint"
    assert plan["range_derivation"]["original_min_price"] == 64000.0
    assert plan["range_derivation"]["original_max_price"] == 65000.0


def test_gate_signal_review_event_has_no_chain():
    gate = _make_gate()
    enriched = _make_enriched_signal()
    result = gate.process_signal(enriched, [], "BLOCK_NEW_ENTRIES")
    assert result.trade_chain is None
    review_events = [e for e in result.lifecycle_events if e.event_type == "REVIEW_REQUIRED"]
    assert len(review_events) == 1


# ── UPDATE path tests ──────────────────────────────────────────────────────────

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
        ActionItem, CancelPendingOperation, CloseOperation, SetStopOperation, TargetActionGroup,
    )
    from src.parser_v2.contracts.context import TargetHints
    from src.runtime_v2.signal_enrichment.models import EnrichedCanonicalMessage

    if action_type == "SET_STOP":
        action = ActionItem(
            action_type="SET_STOP",
            set_stop=SetStopOperation(target_type=set_stop_target),
            source_intent="MOVE_STOP_TO_BE",
        )
    elif action_type == "CANCEL_PENDING":
        action = ActionItem(
            action_type="CANCEL_PENDING",
            cancel_pending=CancelPendingOperation(),
            source_intent="CANCEL_PENDING",
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
    # lifecycle_state must NOT change — only be_protection_status should reflect BE pending
    assert cr.new_lifecycle_state is None
    assert cr.new_be_protection_status == "BE_MOVE_PENDING"


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


def test_update_move_to_be_without_entry_avg_requires_review():
    gate = _make_gate()
    enriched = _make_update_enriched(scope_hint="SINGLE_SIGNAL", symbols=["BTC/USDT"])
    chain = _make_open_chain(
        state="WAITING_ENTRY",
        entry_avg_price=None,
        current_stop_price=None,
    )
    result = gate.process_update(enriched, [chain], {})
    cr = result.chain_results[0]
    assert cr.execution_commands == []
    assert cr.new_lifecycle_state is None
    assert cr.new_be_protection_status is None
    assert cr.lifecycle_events[0].event_type == "REVIEW_REQUIRED"
    assert json.loads(cr.lifecycle_events[0].payload_json)["reason"] == "missing_entry_avg_price_for_be"


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


def test_update_close_full_also_cancels_pending_entries():
    """CLOSE_FULL deve emettere CLOSE_FULL + CANCEL_PENDING_ENTRY per cancellare entry pendenti."""
    gate = _make_gate()
    enriched = _make_update_enriched(
        scope_hint="SINGLE_SIGNAL", symbols=["BTC/USDT"],
        action_type="CLOSE", close_scope="FULL",
    )
    chain = _make_open_chain()
    result = gate.process_update(enriched, [chain], {})
    cr = result.chain_results[0]
    cmd_types = [c.command_type for c in cr.execution_commands]
    assert "CLOSE_FULL" in cmd_types, "deve esserci CLOSE_FULL"
    assert "CANCEL_PENDING_ENTRY" in cmd_types, "deve cancellare gli ordini entry pendenti"


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


def test_update_close_full_payload_contains_hedge_context():
    gate = _make_gate()
    enriched = _make_update_enriched(
        scope_hint="SINGLE_SIGNAL", symbols=["BTC/USDT"],
        action_type="CLOSE", close_scope="FULL",
    )
    chain = _make_open_chain(side="SHORT")
    chain = chain.model_copy(update={
        "risk_snapshot_json": '{"hedge_mode": true}',
        "execution_mode": "UNIFIED_PLAN",
    })

    result = gate.process_update(enriched, [chain], {})
    cr = result.chain_results[0]
    command = next(c for c in cr.execution_commands if c.command_type == "CLOSE_FULL")
    payload = json.loads(command.payload_json)
    assert payload["hedge_mode"] is True
    assert payload["position_idx"] == 2


def test_update_close_partial_payload_contains_hedge_context():
    gate = _make_gate()
    enriched = _make_update_enriched(
        scope_hint="SINGLE_SIGNAL", symbols=["BTC/USDT"],
        action_type="CLOSE", close_scope="PARTIAL", fraction=0.25,
    )
    chain = _make_open_chain(side="SHORT")
    chain = chain.model_copy(update={
        "risk_snapshot_json": '{"hedge_mode": true}',
        "execution_mode": "UNIFIED_PLAN",
    })

    result = gate.process_update(enriched, [chain], {})
    cr = result.chain_results[0]
    command = next(c for c in cr.execution_commands if c.command_type == "CLOSE_PARTIAL")
    payload = json.loads(command.payload_json)
    assert payload["hedge_mode"] is True
    assert payload["position_idx"] == 2


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
        scope_hint="SINGLE_SIGNAL",
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


def test_apply_move_to_be_emits_telegram_update_accepted_with_prices():
    """_apply_move_to_be deve emettere TELEGRAM_UPDATE_ACCEPTED con old_sl_price,
    new_sl_price e is_breakeven=True invece di BE_MOVE_REQUESTED."""
    import json
    from src.runtime_v2.lifecycle.models import TradeChain
    from src.runtime_v2.lifecycle.entry_gate import LifecycleEntryGate
    from src.runtime_v2.lifecycle.ports import ExchangeDataPort
    from src.runtime_v2.lifecycle.risk_capacity import RiskCapacityEngine
    from unittest.mock import MagicMock

    # Build a minimal TradeChain in OPEN state with entry_avg_price and current_stop_price
    risk_json = json.dumps({"sl_price": 49000.0, "fee_profile": None})
    chain = TradeChain(
        trade_chain_id=99,
        source_enrichment_id=99,
        canonical_message_id=99,
        raw_message_id=99,
        trader_id="trader_a",
        account_id="acc_1",
        symbol="BTC/USDT",
        side="LONG",
        lifecycle_state="OPEN",
        entry_mode="ONE_SHOT",
        management_plan_json='{"be_fee_correction_enabled": false}',
        risk_snapshot_json=risk_json,
        plan_state_json="{}",
        entry_avg_price=50000.0,
        current_stop_price=49000.0,
        open_position_qty=0.01,
        execution_mode="UNIFIED_PLAN",
    )

    # Build a minimal enriched message (we only need canonical_message_id)
    from src.runtime_v2.signal_enrichment.models import EnrichedCanonicalMessage
    enriched = EnrichedCanonicalMessage(
        enrichment_id=1,
        canonical_message_id=1,
        raw_message_id=99,
        trader_id="trader_a",
        account_id="acc_1",
        primary_class="UPDATE",
        enrichment_decision="PASS",
        policy_snapshot={},
        policy_version="",
    )

    gate = LifecycleEntryGate(
        risk_engine=MagicMock(spec=RiskCapacityEngine),
        exchange_port=MagicMock(spec=ExchangeDataPort),
    )

    result = gate._apply_move_to_be(enriched, chain, active_commands=[])

    accepted = [e for e in result.lifecycle_events if e.event_type == "TELEGRAM_UPDATE_ACCEPTED"]
    be_requested = [e for e in result.lifecycle_events if e.event_type == "BE_MOVE_REQUESTED"]

    assert len(accepted) == 1, f"expected 1 TELEGRAM_UPDATE_ACCEPTED, got {len(accepted)}; events: {[e.event_type for e in result.lifecycle_events]}"
    assert len(be_requested) == 0, "BE_MOVE_REQUESTED should no longer be emitted by _apply_move_to_be"

    p = json.loads(accepted[0].payload_json)
    assert p.get("action") == "MOVE_SL_TO_BE"
    assert p.get("is_breakeven") is True
    assert p.get("old_sl_price") is not None, "old_sl_price must be present"
    assert p.get("new_sl_price") is not None, "new_sl_price must be present"
    # new_sl_price should be at or near entry_avg_price (50000) — within fee adjustment tolerance
    assert abs(float(p["new_sl_price"]) - 50000.0) < 500, (
        f"new_sl_price {p['new_sl_price']} should be near entry_avg_price 50000"
    )


def _make_chain_simple(state="OPEN"):
    from src.runtime_v2.lifecycle.models import TradeChain
    return TradeChain(
        trade_chain_id=10, source_enrichment_id=1, canonical_message_id=2,
        raw_message_id=3, trader_id="t1", account_id="acc1",
        symbol="BTC/USDT", side="LONG", lifecycle_state=state,
        entry_mode="ONE_SHOT",
        management_plan_json='{"be_trigger": null}',
        entry_avg_price=50000.0,
    )


def _make_enriched_update_simple(action_type="SET_STOP"):
    from unittest.mock import MagicMock
    enriched = MagicMock()
    enriched.enrichment_id = 99
    enriched.canonical_message_id = 55
    enriched.trader_id = "t1"
    action = MagicMock()
    action.action_type = action_type
    action.set_stop = MagicMock()
    action.set_stop.target_type = "ENTRY"
    tag = MagicMock()
    tag.actions = [action]
    tag.targeting.scope_hint = "SYMBOL"
    tag.targeting.symbols = {"BTC/USDT"}
    tag.targeting.explicit_ids = None
    enriched.enriched_actions = [tag]
    return enriched


def test_move_to_be_does_not_set_lifecycle_state():
    gate = _make_gate()
    chain = _make_chain_simple("OPEN")
    enriched = _make_enriched_update_simple("SET_STOP")
    result = gate.process_update(enriched, [chain], {10: []})
    assert len(result.chain_results) == 1
    cr = result.chain_results[0]
    # Must NOT set lifecycle_state to a BE state
    assert cr.new_lifecycle_state is None
    # Must set be_protection_status
    assert cr.new_be_protection_status == "BE_MOVE_PENDING"


# ── execution_mode tests (Mode A / B / C) ─────────────────────────────────────

def _make_risk_decision(size_usdt=500.0, entry_price=50000.0):
    from src.runtime_v2.lifecycle.risk_capacity import RiskDecision
    return RiskDecision(
        passed=True,
        reason=None,
        size_usdt=size_usdt,
        leverage=10,
        risk_snapshot={
            "entry_price": entry_price,
            "size_usdt": size_usdt,
            "leverage": 10,
            "legs": [{
                "sequence": 1,
                "qty": size_usdt / entry_price,
                "qty_mode": "fixed",
                "risk_amount": 10.0,
                "weight": 1.0,
            }],
        },
    )


def _make_enriched_signal_for_mode(
    tp_count: int = 2,
    *,
    side: str = "LONG",
    close_distribution_table: dict[int, list[int]] | None = None,
):
    from src.parser_v2.contracts.entities import Price, StopLoss, TakeProfit
    from src.runtime_v2.signal_enrichment.models import (
        CloseDistributionConfig, EnrichedCanonicalMessage, EnrichedEntryLeg,
        EnrichedSignalPayload, ManagementPlanConfig,
    )

    entries = [EnrichedEntryLeg(
        sequence=1, entry_type="LIMIT",
        price=Price(raw="50000", value=50000.0),
        weight=1.0,
    )]
    tps = [
        TakeProfit(sequence=i + 1, price=Price(raw=str(51000 + i * 1000), value=51000.0 + i * 1000))
        for i in range(tp_count)
    ]
    sl = StopLoss(price=Price(raw="49000", value=49000.0))
    signal = EnrichedSignalPayload(
        symbol="BTC/USDT", side=side, entry_structure="ONE_SHOT",
        entries=entries, take_profits=tps, stop_loss=sl,
    )
    mp = ManagementPlanConfig(
        close_distribution=CloseDistributionConfig(
            table=close_distribution_table or {},
        )
    )
    return EnrichedCanonicalMessage(
        enrichment_id=1, canonical_message_id=2, raw_message_id=3,
        trader_id="t1", account_id="acc1",
        primary_class="SIGNAL", enrichment_decision="PASS",
        enriched_signal=signal, management_plan=mp,
        policy_snapshot={},
    )


def _make_gate_with_mode(execution_mode: str | None = None):
    from src.runtime_v2.lifecycle.entry_gate import LifecycleEntryGate
    from src.runtime_v2.lifecycle.risk_capacity import RiskCapacityEngine
    from src.runtime_v2.lifecycle.static_exchange_data_port import StaticExchangeDataPort

    gate = LifecycleEntryGate(
        risk_engine=RiskCapacityEngine(),
        exchange_port=StaticExchangeDataPort(),
        simple_attached_enabled=True,
    )
    gate._risk.validate = lambda *a, **kw: _make_risk_decision()
    return gate


def test_update_move_to_be_payload_uses_new_stop_price_without_legacy_fields():
    gate = _make_gate()
    enriched = _make_update_enriched(scope_hint="SINGLE_SIGNAL", symbols=["BTC/USDT"])
    chain = _make_open_chain(entry_avg_price=50000.0, current_stop_price=49000.0)

    result = gate.process_update(enriched, [chain], {})

    cr = result.chain_results[0]
    command = next(c for c in cr.execution_commands if c.command_type == "MOVE_STOP_TO_BREAKEVEN")
    payload = json.loads(command.payload_json)
    assert payload["new_stop_price"] == 50000.0
    assert payload["is_breakeven"] is True
    assert "target_price" not in payload
    assert "be_buffer_pct" not in payload


def test_update_move_to_be_payload_contains_protection_style_standalone_for_sequential():
    """Manual BE move on a_sequential chain → protection_style='standalone_order'."""
    gate = _make_gate()
    enriched = _make_update_enriched(scope_hint="SINGLE_SIGNAL", symbols=["BTC/USDT"])
    chain = _make_open_chain(entry_avg_price=50000.0)
    chain = chain.model_copy(update={
        "execution_mode": "a_sequential",
        "risk_snapshot_json": '{"hedge_mode": false}',
    })

    result = gate.process_update(enriched, [chain], {})

    cr = result.chain_results[0]
    command = next(c for c in cr.execution_commands if c.command_type == "MOVE_STOP_TO_BREAKEVEN")
    payload = json.loads(command.payload_json)
    assert payload["protection_style"] == "standalone_order"
    assert "position_idx" in payload


def test_update_move_to_be_payload_contains_protection_style_attached_for_unified_plan():
    """Manual BE move on UNIFIED_PLAN chain → protection_style='attached_full'."""
    gate = _make_gate()
    enriched = _make_update_enriched(scope_hint="SINGLE_SIGNAL", symbols=["BTC/USDT"])
    chain = _make_open_chain(entry_avg_price=50000.0)
    chain = chain.model_copy(update={
        "execution_mode": "UNIFIED_PLAN",
        "risk_snapshot_json": '{"hedge_mode": false}',
    })

    result = gate.process_update(enriched, [chain], {})

    cr = result.chain_results[0]
    command = next(c for c in cr.execution_commands if c.command_type == "MOVE_STOP_TO_BREAKEVEN")
    payload = json.loads(command.payload_json)
    assert payload["protection_style"] == "attached_full"
    assert "position_idx" in payload


def test_update_move_to_be_payload_position_idx_zero_for_one_way_mode():
    """Manual BE move with hedge_mode=false → position_idx=0 regardless of execution_mode."""
    gate = _make_gate()
    enriched = _make_update_enriched(scope_hint="SINGLE_SIGNAL", symbols=["BTC/USDT"])
    chain = _make_open_chain(entry_avg_price=50000.0, side="LONG")
    chain = chain.model_copy(update={
        "execution_mode": "UNIFIED_PLAN",
        "risk_snapshot_json": '{"hedge_mode": false}',
    })

    result = gate.process_update(enriched, [chain], {})

    cr = result.chain_results[0]
    command = next(c for c in cr.execution_commands if c.command_type == "MOVE_STOP_TO_BREAKEVEN")
    payload = json.loads(command.payload_json)
    assert payload["position_idx"] == 0


def test_is_already_be_uses_fee_aware_target_when_correction_enabled():
    from src.runtime_v2.lifecycle.entry_gate import LifecycleEntryGate

    chain = _make_open_chain(
        entry_avg_price=100.0,
        current_stop_price=100.05,
    )
    chain = chain.model_copy(update={
        "management_plan_json": json.dumps({
            "be_trigger": None,
            "be_fee_correction_enabled": True,
            "be_fee_fallback_profile": "bybit_linear",
        }),
        "open_position_qty": 2.0,
        "risk_snapshot_json": json.dumps({
            "open_fee_residual": 0.2,
            "fee_profile": {"standalone_order": 0.0004},
        }),
    })

    assert LifecycleEntryGate._is_already_be(chain) is False


def test_process_signal_writes_execution_mode_to_chain():
    gate = _make_gate_with_mode()
    enriched = _make_enriched_signal_for_mode()
    result = gate.process_signal(enriched, [], "NONE")
    assert result.trade_chain is not None
    assert result.trade_chain.execution_mode == "UNIFIED_PLAN"


# ── CANCEL_PENDING on OPEN/PARTIALLY_CLOSED chains ───────────────────────────

def _make_gate_default():
    from src.runtime_v2.lifecycle.entry_gate import LifecycleEntryGate
    from src.runtime_v2.lifecycle.risk_capacity import RiskCapacityEngine
    from src.runtime_v2.lifecycle.static_exchange_data_port import StaticExchangeDataPort
    return LifecycleEntryGate(
        risk_engine=RiskCapacityEngine(),
        exchange_port=StaticExchangeDataPort(),
    )


def _make_chain(state="OPEN"):
    from src.runtime_v2.lifecycle.models import TradeChain
    return TradeChain(
        trade_chain_id=10, source_enrichment_id=1, canonical_message_id=2,
        raw_message_id=3, trader_id="t1", account_id="acc1",
        symbol="BTC/USDT", side="LONG", lifecycle_state=state,
        entry_mode="ONE_SHOT",
        management_plan_json='{"be_trigger": null}',
        entry_avg_price=50000.0,
    )


def _make_enriched_cancel():
    from unittest.mock import MagicMock
    enriched = MagicMock()
    enriched.enrichment_id = 99
    enriched.canonical_message_id = 55
    enriched.trader_id = "t1"
    action = MagicMock()
    action.action_type = "CANCEL_PENDING"
    tag = MagicMock()
    tag.actions = [action]
    tag.targeting.scope_hint = "SYMBOL"
    tag.targeting.symbols = {"BTC/USDT"}
    tag.targeting.explicit_ids = None
    enriched.enriched_actions = [tag]
    return enriched


def test_cancel_pending_on_waiting_entry_does_not_immediately_cancel():
    gate = _make_gate_default()
    chain = _make_chain("WAITING_ENTRY")
    enriched = _make_enriched_cancel()
    result = gate.process_update(enriched, [chain], {10: []})
    assert len(result.chain_results) == 1
    cr = result.chain_results[0]
    assert cr.new_lifecycle_state is None
    cmd_types = [c.command_type for c in cr.execution_commands]
    assert "CANCEL_PENDING_ENTRY" in cmd_types


def test_cancel_pending_on_open_emits_cancel_not_cancelled():
    gate = _make_gate_default()
    chain = _make_chain("OPEN")
    chain = chain.model_copy(update={"open_position_qty": 0.005})
    enriched = _make_enriched_cancel()
    result = gate.process_update(enriched, [chain], {10: []})
    assert len(result.chain_results) == 1
    cr = result.chain_results[0]
    assert cr.new_lifecycle_state is None
    cmd_types = [c.command_type for c in cr.execution_commands]
    assert "CANCEL_PENDING_ENTRY" in cmd_types


def test_cancel_pending_on_partially_closed_emits_cancel_entry():
    gate = _make_gate_default()
    chain = _make_chain("PARTIALLY_CLOSED")
    chain = chain.model_copy(update={"open_position_qty": 0.005})
    enriched = _make_enriched_cancel()
    result = gate.process_update(enriched, [chain], {10: []})
    cr = result.chain_results[0]
    assert cr.new_lifecycle_state is None
    cmd_types = [c.command_type for c in cr.execution_commands]
    assert "CANCEL_PENDING_ENTRY" in cmd_types


# ── Telegram message ID resolution tests ──────────────────────────────────────

def _make_chain_with_raw_id(
    trade_chain_id: int,
    trader_id: str,
    symbol: str,
    side: str,
    raw_message_id: int,
) -> "TradeChain":
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


def _make_enriched_update_reply(
    trader_id: str,
    reply_to_message_id: int,
) -> "EnrichedCanonicalMessage":
    """Helper: UPDATE with reply_to_message_id only (no explicit telegram_message_ids)."""
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
            target_source="REPLY",
            reply_to_message_id=reply_to_message_id,
            telegram_message_ids=[],
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


def test_resolve_targets_matches_via_reply_to_message_id():
    """UPDATE as a simple reply (no explicit links) resolves chain via reply_to_message_id."""
    chain_xrp = _make_chain_with_raw_id(1, "trader_a", "XRPUSDT", "SHORT", raw_message_id=10)
    chain_ada = _make_chain_with_raw_id(2, "trader_a", "ADAUSDT", "SHORT", raw_message_id=20)

    enriched = _make_enriched_update_reply("trader_a", reply_to_message_id=50)
    tg_id_to_raw_id = {50: 10, 51: 20}  # telegram IDs 50,51 map to raw_message_ids 10,20

    gate = _make_gate_with_mode("b_entry_stop_then_tp")
    tag = enriched.enriched_actions[0]
    result = gate._resolve_targets(
        enriched, [chain_xrp, chain_ada], tag,
        tg_id_to_raw_id=tg_id_to_raw_id,
    )

    assert result == [chain_xrp]


def test_resolve_targets_reply_to_absent_chain_returns_empty():
    """UPDATE via reply to a chain that is no longer active returns [] (not ambiguous)."""
    chain_other = _make_chain_with_raw_id(2, "trader_a", "ADAUSDT", "SHORT", raw_message_id=20)

    enriched = _make_enriched_update_reply("trader_a", reply_to_message_id=50)
    tg_id_to_raw_id = {50: 10}  # tg_id 50 → raw_id 10, but no active chain has raw_id 10

    gate = _make_gate_with_mode("b_entry_stop_then_tp")
    tag = enriched.enriched_actions[0]
    result = gate._resolve_targets(
        enriched, [chain_other], tag,
        tg_id_to_raw_id=tg_id_to_raw_id,
    )

    assert result == []  # specific miss: replied-to chain is gone


def test_build_tg_id_to_raw_id_includes_reply_to():
    """_build_tg_id_to_raw_id collects reply_to_message_id alongside telegram_message_ids."""
    from src.parser_v2.contracts.canonical_message import (
        ActionItem, CloseOperation, TargetActionGroup,
    )
    from src.parser_v2.contracts.context import TargetHints

    tag = TargetActionGroup(
        targeting=TargetHints(
            target_source="REPLY",
            reply_to_message_id=58,
            telegram_message_ids=[],
            scope_hint="SINGLE_SIGNAL",
        ),
        actions=[ActionItem(
            action_type="CLOSE",
            close=CloseOperation(close_scope="FULL"),
            source_intent="CLOSE_FULL",
        )],
    )

    # We can't easily call _build_tg_id_to_raw_id without the worker, but we can
    # verify the logic by checking the tg_ids collected from the enriched actions.
    all_tg_ids: set[int] = set()
    for t in [tag]:
        all_tg_ids.update(t.targeting.telegram_message_ids)
        if t.targeting.reply_to_message_id is not None:
            all_tg_ids.add(t.targeting.reply_to_message_id)

    assert 58 in all_tg_ids


def test_resolve_targets_matches_via_telegram_message_id():
    """When two chains are open, Telegram ID resolves to the correct one."""
    chain_xrp = _make_chain_with_raw_id(1, "trader_a", "XRPUSDT", "SHORT", raw_message_id=10)
    chain_ada = _make_chain_with_raw_id(2, "trader_a", "ADAUSDT", "SHORT", raw_message_id=20)

    enriched = _make_enriched_update_tg("trader_a", telegram_message_ids=[50])
    tg_id_to_raw_id = {50: 10, 51: 20}  # telegram IDs 50,51 map to raw_message_ids 10,20

    gate = _make_gate_with_mode("b_entry_stop_then_tp")
    tag = enriched.enriched_actions[0]
    result = gate._resolve_targets(
        enriched, [chain_xrp, chain_ada], tag,
        tg_id_to_raw_id=tg_id_to_raw_id,
    )

    assert result == [chain_xrp]


def test_resolve_targets_telegram_id_no_match_falls_through_to_ambiguous():
    """If Telegram IDs resolve to an absent chain, return [] (specific miss — not ambiguous)."""
    chain_a = _make_chain_with_raw_id(1, "trader_a", "XRPUSDT", "SHORT", raw_message_id=10)
    chain_b = _make_chain_with_raw_id(2, "trader_a", "ADAUSDT", "SHORT", raw_message_id=20)

    enriched = _make_enriched_update_tg("trader_a", telegram_message_ids=[99])
    tg_id_to_raw_id = {99: 999}  # maps to raw_id=999 which no chain has

    gate = _make_gate_with_mode("b_entry_stop_then_tp")
    tag = enriched.enriched_actions[0]
    result = gate._resolve_targets(
        enriched, [chain_a, chain_b], tag,
        tg_id_to_raw_id=tg_id_to_raw_id,
    )

    assert result == []  # Telegram evidence pointed to absent chain → specific miss, not ambiguous


def test_resolve_targets_telegram_id_empty_mapping_falls_through():
    """Empty tg_id_to_raw_id → no Telegram resolution, falls to ambiguous."""
    chain_a = _make_chain_with_raw_id(1, "trader_a", "XRPUSDT", "SHORT", raw_message_id=10)
    chain_b = _make_chain_with_raw_id(2, "trader_a", "ADAUSDT", "SHORT", raw_message_id=20)

    enriched = _make_enriched_update_tg("trader_a", telegram_message_ids=[10])

    gate = _make_gate_with_mode("b_entry_stop_then_tp")
    tag = enriched.enriched_actions[0]
    result = gate._resolve_targets(
        enriched, [chain_a, chain_b], tag,
        tg_id_to_raw_id={},
    )

    assert result is None  # ambiguous


def test_resolve_targets_single_chain_telegram_id_no_match_returns_chain():
    """Single open chain + telegram ID resolves to absent chain → returns [] (specific miss)."""
    chain_a = _make_chain_with_raw_id(1, "trader_a", "XRPUSDT", "SHORT", raw_message_id=10)

    enriched = _make_enriched_update_tg("trader_a", telegram_message_ids=[99])
    tg_id_to_raw_id = {99: 999}  # maps to raw_id 999 — no chain has this

    gate = _make_gate_with_mode("b_entry_stop_then_tp")
    tag = enriched.enriched_actions[0]
    result = gate._resolve_targets(
        enriched, [chain_a], tag,
        tg_id_to_raw_id=tg_id_to_raw_id,
    )

    assert result == []  # Telegram evidence pointed elsewhere → do not apply to wrong chain


def test_gate_signal_empty_entries_produces_review():
    """BUG 3: signal with no entry legs must be rejected, not create a stuck WAITING_ENTRY chain."""
    from src.parser_v2.contracts.entities import Price, StopLoss, TakeProfit
    from src.runtime_v2.signal_enrichment.models import (
        EnrichedCanonicalMessage, EnrichedSignalPayload, ManagementPlanConfig,
    )

    signal = EnrichedSignalPayload(
        symbol="BTC/USDT", side="LONG", entry_structure="ONE_SHOT",
        entries=[],  # ← empty — no legs resolved by enrichment
        take_profits=[TakeProfit(sequence=1, price=Price(raw="51000", value=51000.0))],
        stop_loss=StopLoss(price=Price(raw="49000", value=49000.0)),
    )
    enriched = EnrichedCanonicalMessage(
        enrichment_id=99, canonical_message_id=990, raw_message_id=9900,
        trader_id="trader_a", account_id="acc_1",
        primary_class="SIGNAL", enrichment_decision="PASS",
        enriched_signal=signal, management_plan=ManagementPlanConfig(),
        policy_snapshot={},
    )
    gate = _make_gate()
    result = gate.process_signal(enriched, [], "NONE")
    assert result.trade_chain is None
    assert result.review_reason == "no_entry_legs"
    assert any(e.event_type == "REVIEW_REQUIRED" for e in result.lifecycle_events)


def test_process_update_uses_tg_id_to_raw_id():
    """process_update routes CLOSE_FULL to the correct chain via Telegram ID."""
    chain_xrp = _make_chain_with_raw_id(1, "trader_a", "XRPUSDT", "SHORT", raw_message_id=2)
    chain_bad = _make_chain_with_raw_id(2, "trader_a", "XRPSDTUSDT", "SHORT", raw_message_id=1)

    enriched = _make_enriched_update_tg("trader_a", telegram_message_ids=[50])
    tg_id_to_raw_id = {50: 2}  # Telegram msg 50 → raw_message_id 2 → chain_xrp

    gate = _make_gate_with_mode("b_entry_stop_then_tp")
    result = gate.process_update(
        enriched,
        [chain_xrp, chain_bad],
        active_commands_by_chain={},
        tg_id_to_raw_id=tg_id_to_raw_id,
    )

    assert len(result.chain_results) == 1
    assert result.chain_results[0].trade_chain_id == 1
    cmds = result.chain_results[0].execution_commands
    assert any(c.command_type == "CLOSE_FULL" for c in cmds)
    assert result.review_events == []


def test_lifecycle_gate_worker_builds_tg_mapping_and_resolves_chain(tmp_path):
    """Worker queries parser DB and passes tg_id_to_raw_id to gate, resolving ambiguous update."""
    import json as _json
    from src.runtime_v2.lifecycle.entry_gate import LifecycleGateWorker

    # ── parser DB with raw_messages ──────────────────────────────────────────
    from src.core.migrations import apply_migrations as _core_apply
    parser_db = str(tmp_path / "parser.sqlite3")
    _core_apply(parser_db, "db/migrations")
    pconn = sqlite3.connect(parser_db)
    # Signal raw message: Telegram ID 50 → raw_message_id 1
    pconn.execute(
        "INSERT INTO raw_messages"
        " (raw_message_id, source_chat_id, telegram_message_id, reply_to_message_id,"
        "  message_ts, acquired_at)"
        " VALUES (1, 'chat1', 50, NULL, '2026-01-01', '2026-01-01')"
    )
    # Update raw message: Telegram ID 51 → raw_message_id 2
    pconn.execute(
        "INSERT INTO raw_messages"
        " (raw_message_id, source_chat_id, telegram_message_id, reply_to_message_id,"
        "  message_ts, acquired_at)"
        " VALUES (2, 'chat1', 51, 50, '2026-01-01', '2026-01-01')"
    )

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
    actions_json = _json.dumps([tag.model_dump()])
    pconn.execute(
        "INSERT INTO enriched_canonical_messages "
        "(enrichment_id, canonical_message_id, raw_message_id, trader_id, account_id,"
        " primary_class, enrichment_decision, enriched_actions_json, lifecycle_processed, created_at)"
        " VALUES (3, 3, 2, 'trader_a', 'acc', 'UPDATE', 'PASS', ?, 0, '2026-01-01')",
        (actions_json,),
    )
    pconn.commit()
    pconn.close()

    # ── ops DB ───────────────────────────────────────────────────────────────
    ops_db = str(tmp_path / "ops.sqlite3")
    _core_apply(ops_db, "db/ops_migrations")
    oconn = sqlite3.connect(ops_db)
    now = "2026-01-01T00:00:00+00:00"
    # Two chains: XRPUSDT (raw_message_id=1) and XRPSDTUSDT (raw_message_id=99)
    oconn.execute(
        "INSERT INTO ops_trade_chains (trade_chain_id, source_enrichment_id, canonical_message_id,"
        " raw_message_id, trader_id, account_id, symbol, side, lifecycle_state, entry_mode,"
        " management_plan_json, created_at, updated_at)"
        " VALUES (1, 1, 1, 1, 'trader_a', 'acc', 'XRPUSDT', 'SHORT', 'OPEN',"
        " 'b_entry_stop_then_tp', '{}', ?, ?)",
        (now, now),
    )
    oconn.execute(
        "INSERT INTO ops_trade_chains (trade_chain_id, source_enrichment_id, canonical_message_id,"
        " raw_message_id, trader_id, account_id, symbol, side, lifecycle_state, entry_mode,"
        " management_plan_json, created_at, updated_at)"
        " VALUES (2, 2, 2, 99, 'trader_a', 'acc', 'XRPSDTUSDT', 'SHORT', 'WAITING_ENTRY',"
        " 'b_entry_stop_then_tp', '{}', ?, ?)",
        (now, now),
    )
    oconn.commit()
    oconn.close()

    # ── repos & worker ────────────────────────────────────────────────────────
    from src.runtime_v2.lifecycle.repositories import (
        ControlStateRepository, ExecutionCommandRepository,
        LifecycleEventRepository, SnapshotRepository, TradeChainRepository,
    )
    gate = _make_gate_with_mode("b_entry_stop_then_tp")
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
    oconn2 = sqlite3.connect(ops_db)
    events = oconn2.execute(
        "SELECT event_type FROM ops_lifecycle_events ORDER BY event_id"
    ).fetchall()
    oconn2.close()
    event_types = [e[0] for e in events]
    assert "REVIEW_REQUIRED" not in event_types
    cmds = sqlite3.connect(ops_db).execute(
        "SELECT command_type, trade_chain_id FROM ops_execution_commands"
    ).fetchall()
    assert any(c[0] == "CLOSE_FULL" and c[1] == 1 for c in cmds)
    assert not any(c[1] == 2 for c in cmds)  # chain 2 must not be touched


def test_lifecycle_gate_worker_expands_cancel_pending_for_each_active_entry_leg(tmp_path):
    """UPDATE cancel on a multi-leg waiting chain must emit one cancel command per active entry."""
    import json as _json
    from src.core.migrations import apply_migrations as _core_apply
    from src.runtime_v2.lifecycle.entry_gate import LifecycleGateWorker
    from src.runtime_v2.lifecycle.repositories import (
        ControlStateRepository, ExecutionCommandRepository,
        LifecycleEventRepository, SnapshotRepository, TradeChainRepository,
    )
    from src.parser_v2.contracts.canonical_message import (
        ActionItem, CancelPendingOperation, TargetActionGroup,
    )
    from src.parser_v2.contracts.context import TargetHints

    parser_db = str(tmp_path / "parser.sqlite3")
    ops_db = str(tmp_path / "ops.sqlite3")
    _core_apply(parser_db, "db/migrations")
    _core_apply(ops_db, "db/ops_migrations")

    pconn = sqlite3.connect(parser_db)
    pconn.execute(
        "INSERT INTO raw_messages"
        " (raw_message_id, source_chat_id, telegram_message_id, reply_to_message_id,"
        "  message_ts, acquired_at)"
        " VALUES (1, 'chat1', 50, NULL, '2026-01-01', '2026-01-01')"
    )
    action = ActionItem(
        action_type="CANCEL_PENDING",
        cancel_pending=CancelPendingOperation(),
        source_intent="CANCEL_PENDING",
    )
    tag = TargetActionGroup(
        targeting=TargetHints(
            telegram_message_ids=[50],
            scope_hint="SINGLE_SIGNAL",
        ),
        actions=[action],
    )
    pconn.execute(
        "INSERT INTO enriched_canonical_messages "
        "(enrichment_id, canonical_message_id, raw_message_id, trader_id, account_id,"
        " primary_class, enrichment_decision, enriched_actions_json, lifecycle_processed, created_at)"
        " VALUES (3, 3, 2, 'trader_a', 'acc', 'UPDATE', 'PASS', ?, 0, '2026-01-01')",
        (_json.dumps([tag.model_dump()]),),
    )
    pconn.commit()
    pconn.close()

    oconn = sqlite3.connect(ops_db)
    now = "2026-01-01T00:00:00+00:00"
    oconn.execute(
        "INSERT INTO ops_trade_chains (trade_chain_id, source_enrichment_id, canonical_message_id,"
        " raw_message_id, trader_id, account_id, symbol, side, lifecycle_state, entry_mode,"
        " management_plan_json, created_at, updated_at)"
        " VALUES (1, 1, 1, 1, 'trader_a', 'acc', 'TONUSDT', 'LONG', 'WAITING_ENTRY',"
        " 'b_entry_stop_then_tp', '{}', ?, ?)",
        (now, now),
    )
    oconn.executemany(
        """
        INSERT INTO ops_execution_commands (
            command_id, trade_chain_id, command_type, status, payload_json,
            idempotency_key, created_at, updated_at, client_order_id
        ) VALUES (?,?,?,?,?,?,?,?,?)
        """,
        [
            (
                1, 1, "PLACE_ENTRY", "SENT",
                '{"symbol":"TONUSDT","side":"LONG","sequence":1}',
                "place_entry:1:leg1", now, now, "tsb:1:1:entry:1:aaa",
            ),
            (
                2, 1, "PLACE_ENTRY", "ACK",
                '{"symbol":"TONUSDT","side":"LONG","sequence":2}',
                "place_entry:1:leg2", now, now, "tsb:1:2:entry:2:bbb",
            ),
        ],
    )
    oconn.commit()
    oconn.close()

    worker = LifecycleGateWorker(
        parser_db_path=parser_db,
        ops_db_path=ops_db,
        gate=_make_gate_with_mode("b_entry_stop_then_tp"),
        chain_repo=TradeChainRepository(ops_db),
        event_repo=LifecycleEventRepository(ops_db),
        command_repo=ExecutionCommandRepository(ops_db),
        snapshot_repo=SnapshotRepository(ops_db),
        control_repo=ControlStateRepository(ops_db),
    )

    processed = worker.run_once()

    assert processed == 1
    conn = sqlite3.connect(ops_db)
    cancel_rows = conn.execute(
        """
        SELECT payload_json, idempotency_key
        FROM ops_execution_commands
        WHERE command_type='CANCEL_PENDING_ENTRY'
        ORDER BY command_id
        """
    ).fetchall()
    state = conn.execute(
        "SELECT lifecycle_state FROM ops_trade_chains WHERE trade_chain_id=1"
    ).fetchone()[0]
    conn.close()

    assert state == "WAITING_ENTRY"
    assert len(cancel_rows) == 2
    payloads = [_json.loads(row[0]) for row in cancel_rows]
    assert {payload["entry_client_order_id"] for payload in payloads} == {
        "tsb:1:1:entry:1:aaa",
        "tsb:1:2:entry:2:bbb",
    }
    assert len({row[1] for row in cancel_rows}) == 2


# ── UNIFIED_PLAN path tests ────────────────────────────────────────────────────

def test_lifecycle_gate_worker_rehydrates_entry_avg_price_from_exchange_history(tmp_path):
    """Processed exchange fills must backfill active chains before BE updates are built."""
    import json as _json
    from src.core.migrations import apply_migrations as _core_apply
    from src.runtime_v2.lifecycle.entry_gate import LifecycleGateWorker
    from src.runtime_v2.lifecycle.repositories import (
        ControlStateRepository, ExecutionCommandRepository,
        LifecycleEventRepository, SnapshotRepository, TradeChainRepository,
    )
    from src.parser_v2.contracts.canonical_message import (
        ActionItem, SetStopOperation, TargetActionGroup,
    )
    from src.parser_v2.contracts.context import TargetHints

    parser_db = str(tmp_path / "parser.sqlite3")
    ops_db = str(tmp_path / "ops.sqlite3")
    _core_apply(parser_db, "db/migrations")
    _core_apply(ops_db, "db/ops_migrations")

    pconn = sqlite3.connect(parser_db)
    pconn.execute(
        "INSERT INTO raw_messages"
        " (raw_message_id, source_chat_id, telegram_message_id, reply_to_message_id,"
        "  message_ts, acquired_at)"
        " VALUES (1, 'chat1', 50, NULL, '2026-01-01', '2026-01-01')"
    )
    pconn.execute(
        "INSERT INTO raw_messages"
        " (raw_message_id, source_chat_id, telegram_message_id, reply_to_message_id,"
        "  message_ts, acquired_at)"
        " VALUES (2, 'chat1', 51, 50, '2026-01-01', '2026-01-01')"
    )
    action = ActionItem(
        action_type="SET_STOP",
        set_stop=SetStopOperation(target_type="ENTRY"),
        source_intent="MOVE_STOP_TO_BE",
    )
    tag = TargetActionGroup(
        targeting=TargetHints(
            telegram_message_ids=[50],
            scope_hint="SINGLE_SIGNAL",
        ),
        actions=[action],
    )
    pconn.execute(
        "INSERT INTO enriched_canonical_messages "
        "(enrichment_id, canonical_message_id, raw_message_id, trader_id, account_id,"
        " primary_class, enrichment_decision, enriched_actions_json, lifecycle_processed, created_at)"
        " VALUES (3, 3, 2, 'trader_a', 'acc', 'UPDATE', 'PASS', ?, 0, '2026-01-01')",
        (_json.dumps([tag.model_dump()]),),
    )
    pconn.commit()
    pconn.close()

    oconn = sqlite3.connect(ops_db)
    now = "2026-01-01T00:00:00+00:00"
    oconn.execute(
        """
        INSERT INTO ops_trade_chains (
            trade_chain_id, source_enrichment_id, canonical_message_id, raw_message_id,
            trader_id, account_id, symbol, side, lifecycle_state, entry_mode,
            management_plan_json, risk_snapshot_json, be_protection_status,
            execution_mode, created_at, updated_at
        ) VALUES (1, 1, 1, 1, 'trader_a', 'acc', 'BTC/USDT', 'LONG', 'OPEN',
                  'ONE_SHOT', '{}', '{"hedge_mode":false}',
                  'NOT_PROTECTED', 'UNIFIED_PLAN', ?, ?)
        """,
        (now, now),
    )
    oconn.execute(
        """
        INSERT INTO ops_exchange_events (
            exchange_event_id, trade_chain_id, event_type, payload_json,
            processing_status, idempotency_key, received_at, processed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            1,
            1,
            "ENTRY_FILLED",
            '{"fill_price":50000.0,"filled_qty":0.01,"command_id":10}',
            "DONE",
            "evt:entry-filled:1",
            now,
            now,
        ),
    )
    oconn.commit()
    oconn.close()

    worker = LifecycleGateWorker(
        parser_db_path=parser_db,
        ops_db_path=ops_db,
        gate=_make_gate_with_mode("b_entry_stop_then_tp"),
        chain_repo=TradeChainRepository(ops_db),
        event_repo=LifecycleEventRepository(ops_db),
        command_repo=ExecutionCommandRepository(ops_db),
        snapshot_repo=SnapshotRepository(ops_db),
        control_repo=ControlStateRepository(ops_db),
    )

    processed = worker.run_once()

    assert processed == 1
    conn = sqlite3.connect(ops_db)
    payload_json = conn.execute(
        """
        SELECT payload_json
        FROM ops_execution_commands
        WHERE command_type='MOVE_STOP_TO_BREAKEVEN'
        ORDER BY command_id DESC
        LIMIT 1
        """
    ).fetchone()[0]
    chain_row = conn.execute(
        """
        SELECT entry_avg_price, filled_entry_qty, open_position_qty
        FROM ops_trade_chains
        WHERE trade_chain_id=1
        """
    ).fetchone()
    conn.close()

    payload = _json.loads(payload_json)
    assert payload["new_stop_price"] == 50000.0
    assert payload["is_breakeven"] is True
    assert "target_price" not in payload
    assert chain_row == (50000.0, 0.01, 0.01)


def test_lifecycle_gate_worker_rehydrates_weighted_entry_avg_from_multiple_fills(tmp_path):
    """Backfill from history must use weighted average across multiple entry fills."""
    import json as _json
    from src.core.migrations import apply_migrations as _core_apply
    from src.runtime_v2.lifecycle.entry_gate import LifecycleGateWorker
    from src.runtime_v2.lifecycle.repositories import (
        ControlStateRepository, ExecutionCommandRepository,
        LifecycleEventRepository, SnapshotRepository, TradeChainRepository,
    )
    from src.parser_v2.contracts.canonical_message import (
        ActionItem, SetStopOperation, TargetActionGroup,
    )
    from src.parser_v2.contracts.context import TargetHints

    parser_db = str(tmp_path / "parser.sqlite3")
    ops_db = str(tmp_path / "ops.sqlite3")
    _core_apply(parser_db, "db/migrations")
    _core_apply(ops_db, "db/ops_migrations")

    pconn = sqlite3.connect(parser_db)
    pconn.execute(
        "INSERT INTO raw_messages"
        " (raw_message_id, source_chat_id, telegram_message_id, reply_to_message_id,"
        "  message_ts, acquired_at)"
        " VALUES (1, 'chat1', 50, NULL, '2026-01-01', '2026-01-01')"
    )
    pconn.execute(
        "INSERT INTO raw_messages"
        " (raw_message_id, source_chat_id, telegram_message_id, reply_to_message_id,"
        "  message_ts, acquired_at)"
        " VALUES (2, 'chat1', 51, 50, '2026-01-01', '2026-01-01')"
    )
    action = ActionItem(
        action_type="SET_STOP",
        set_stop=SetStopOperation(target_type="ENTRY"),
        source_intent="MOVE_STOP_TO_BE",
    )
    tag = TargetActionGroup(
        targeting=TargetHints(
            telegram_message_ids=[50],
            scope_hint="SINGLE_SIGNAL",
        ),
        actions=[action],
    )
    pconn.execute(
        "INSERT INTO enriched_canonical_messages "
        "(enrichment_id, canonical_message_id, raw_message_id, trader_id, account_id,"
        " primary_class, enrichment_decision, enriched_actions_json, lifecycle_processed, created_at)"
        " VALUES (3, 3, 2, 'trader_a', 'acc', 'UPDATE', 'PASS', ?, 0, '2026-01-01')",
        (_json.dumps([tag.model_dump()]),),
    )
    pconn.commit()
    pconn.close()

    oconn = sqlite3.connect(ops_db)
    now = "2026-01-01T00:00:00+00:00"
    oconn.execute(
        """
        INSERT INTO ops_trade_chains (
            trade_chain_id, source_enrichment_id, canonical_message_id, raw_message_id,
            trader_id, account_id, symbol, side, lifecycle_state, entry_mode,
            management_plan_json, risk_snapshot_json, be_protection_status,
            execution_mode, created_at, updated_at
        ) VALUES (1, 1, 1, 1, 'trader_a', 'acc', 'BTC/USDT', 'LONG', 'OPEN',
                  'ONE_SHOT', '{}', '{"hedge_mode":false}',
                  'NOT_PROTECTED', 'UNIFIED_PLAN', ?, ?)
        """,
        (now, now),
    )
    oconn.executemany(
        """
        INSERT INTO ops_exchange_events (
            exchange_event_id, trade_chain_id, event_type, payload_json,
            processing_status, idempotency_key, received_at, processed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                1,
                1,
                "ENTRY_FILLED",
                '{"fill_price":50000.0,"filled_qty":0.01,"command_id":10}',
                "DONE",
                "evt:entry-filled:1",
                now,
                now,
            ),
            (
                2,
                1,
                "ENTRY_FILLED",
                '{"fill_price":49000.0,"filled_qty":0.02,"command_id":11}',
                "DONE",
                "evt:entry-filled:2",
                "2026-01-01T00:01:00+00:00",
                "2026-01-01T00:01:00+00:00",
            ),
        ],
    )
    oconn.commit()
    oconn.close()

    worker = LifecycleGateWorker(
        parser_db_path=parser_db,
        ops_db_path=ops_db,
        gate=_make_gate_with_mode("b_entry_stop_then_tp"),
        chain_repo=TradeChainRepository(ops_db),
        event_repo=LifecycleEventRepository(ops_db),
        command_repo=ExecutionCommandRepository(ops_db),
        snapshot_repo=SnapshotRepository(ops_db),
        control_repo=ControlStateRepository(ops_db),
    )

    processed = worker.run_once()

    assert processed == 1
    conn = sqlite3.connect(ops_db)
    payload_json = conn.execute(
        """
        SELECT payload_json
        FROM ops_execution_commands
        WHERE command_type='MOVE_STOP_TO_BREAKEVEN'
        ORDER BY command_id DESC
        LIMIT 1
        """
    ).fetchone()[0]
    chain_row = conn.execute(
        """
        SELECT entry_avg_price, filled_entry_qty, open_position_qty
        FROM ops_trade_chains
        WHERE trade_chain_id=1
        """
    ).fetchone()
    conn.close()

    payload = _json.loads(payload_json)
    assert payload["new_stop_price"] == pytest.approx((50000.0 * 0.01 + 49000.0 * 0.02) / 0.03)
    assert payload["is_breakeven"] is True
    assert "target_price" not in payload
    assert chain_row[0] == pytest.approx((50000.0 * 0.01 + 49000.0 * 0.02) / 0.03)
    assert chain_row[1:] == (0.03, 0.03)


def _make_risk_decision_with_legs(
    size_usdt: float = 500.0,
    entry_price: float = 50000.0,
    legs: list[dict] | None = None,
):
    """Risk decision that includes per-leg qty snapshots required by EntryCommandFactory."""
    from src.runtime_v2.lifecycle.risk_capacity import RiskDecision
    if legs is None:
        legs = [{"sequence": 1, "qty": size_usdt / entry_price}]
    return RiskDecision(
        passed=True,
        reason=None,
        size_usdt=size_usdt,
        leverage=10,
        risk_snapshot={
            "entry_price": entry_price,
            "size_usdt": size_usdt,
            "leverage": 10,
            "legs": legs,
        },
    )


def _make_gate_unified(simple_attached_enabled: bool = True):
    from src.runtime_v2.lifecycle.entry_gate import LifecycleEntryGate
    from src.runtime_v2.lifecycle.risk_capacity import RiskCapacityEngine
    from src.runtime_v2.lifecycle.static_exchange_data_port import StaticExchangeDataPort
    gate = LifecycleEntryGate(
        risk_engine=RiskCapacityEngine(),
        exchange_port=StaticExchangeDataPort(),
        simple_attached_enabled=simple_attached_enabled,
    )
    gate._risk.validate = lambda *a, **kw: _make_risk_decision_with_legs()
    return gate


def _make_enriched_signal_unified(
    tp_count: int = 1,
    entry_count: int = 1,
    *,
    sl_price: float = 49000.0,
):
    """Enriched signal for UNIFIED_PLAN path tests."""
    from src.parser_v2.contracts.entities import Price, StopLoss, TakeProfit
    from src.runtime_v2.signal_enrichment.models import (
        EnrichedCanonicalMessage, EnrichedEntryLeg,
        EnrichedSignalPayload, ManagementPlanConfig,
    )

    entries = [
        EnrichedEntryLeg(
            sequence=i + 1, entry_type="LIMIT",
            price=Price(raw=str(50000 - i * 500), value=50000.0 - i * 500),
            weight=round(1.0 / entry_count, 6),
        )
        for i in range(entry_count)
    ]
    tps = [
        TakeProfit(sequence=i + 1, price=Price(raw=str(51000 + i * 1000), value=51000.0 + i * 1000))
        for i in range(tp_count)
    ]
    sl = StopLoss(price=Price(raw=str(sl_price), value=sl_price))
    signal = EnrichedSignalPayload(
        symbol="BTC/USDT", side="LONG", entry_structure="ONE_SHOT" if entry_count == 1 else "TWO_STEP",
        entries=entries, take_profits=tps, stop_loss=sl,
    )
    return EnrichedCanonicalMessage(
        enrichment_id=1, canonical_message_id=2, raw_message_id=3,
        trader_id="t1", account_id="acc1",
        primary_class="SIGNAL", enrichment_decision="PASS",
        enriched_signal=signal, management_plan=ManagementPlanConfig(),
        policy_snapshot={},
    )


def test_unified_plan_single_entry_single_tp_emits_place_entry_with_attached_tpsl():
    """1 entry, 1 TP → single PLACE_ENTRY_WITH_ATTACHED_TPSL command, no extra TP commands."""
    gate = _make_gate_unified()
    enriched = _make_enriched_signal_unified(tp_count=1, entry_count=1)
    result = gate.process_signal(enriched, [], "NONE")
    assert result.review_reason is None
    assert result.trade_chain is not None
    assert result.trade_chain.execution_mode == "UNIFIED_PLAN"
    cmd_types = [c.command_type for c in result.execution_commands]
    assert cmd_types == ["PLACE_ENTRY_WITH_ATTACHED_TPSL"]
    payload = json.loads(result.execution_commands[0].payload_json)
    assert payload["attached_tpsl"]["stop_loss"] == 49000.0
    assert payload["attached_tpsl"]["take_profit"] == 51000.0
    assert payload["attached_tpsl"]["mode"] == "FULL"


def test_unified_plan_single_entry_multi_tp_no_intermediate_tp_commands():
    """1 entry, 2 TPs → only 1 PLACE_ENTRY_WITH_ATTACHED_TPSL, final TP attached, no intermediate commands."""
    gate = _make_gate_unified()
    gate._risk.validate = lambda *a, **kw: _make_risk_decision_with_legs(
        legs=[{"sequence": 1, "qty": 0.01}],
    )
    enriched = _make_enriched_signal_unified(tp_count=2, entry_count=1)
    result = gate.process_signal(enriched, [], "NONE")
    assert result.review_reason is None
    assert result.trade_chain.execution_mode == "UNIFIED_PLAN"
    cmd_types = [c.command_type for c in result.execution_commands]
    # Only one command — no intermediate TP placement at signal time
    assert cmd_types == ["PLACE_ENTRY_WITH_ATTACHED_TPSL"]
    payload = json.loads(result.execution_commands[0].payload_json)
    # Final TP (highest sequence) is attached
    assert payload["attached_tpsl"]["take_profit"] == 52000.0
    assert payload["attached_tpsl"]["stop_loss"] == 49000.0


def test_unified_plan_multi_entry_single_tp_leg1_attached_legs2plus_plain():
    """2 entries, 1 TP → leg 1 = PLACE_ENTRY_WITH_ATTACHED_TPSL, leg 2 = PLACE_ENTRY (no TPSL)."""
    gate = _make_gate_unified()
    gate._risk.validate = lambda *a, **kw: _make_risk_decision_with_legs(
        legs=[{"sequence": 1, "qty": 0.005}, {"sequence": 2, "qty": 0.005}],
    )
    enriched = _make_enriched_signal_unified(tp_count=1, entry_count=2)
    result = gate.process_signal(enriched, [], "NONE")
    assert result.review_reason is None
    assert result.trade_chain.execution_mode == "UNIFIED_PLAN"
    cmd_types = [c.command_type for c in result.execution_commands]
    assert cmd_types == ["PLACE_ENTRY_WITH_ATTACHED_TPSL", "PLACE_ENTRY"]
    # Leg 1 has attached TPSL
    leg1_payload = json.loads(result.execution_commands[0].payload_json)
    assert "attached_tpsl" in leg1_payload
    assert leg1_payload["attached_tpsl"]["stop_loss"] == 49000.0
    # Leg 2 has no attached TPSL
    leg2_payload = json.loads(result.execution_commands[1].payload_json)
    assert "attached_tpsl" not in leg2_payload


def test_unified_plan_multi_entry_multi_tp_leg1_attached_final_tp_legs2plus_plain():
    """2 entries, 2 TPs → leg 1 = PLACE_ENTRY_WITH_ATTACHED_TPSL with final TP, leg 2 = PLACE_ENTRY."""
    gate = _make_gate_unified()
    gate._risk.validate = lambda *a, **kw: _make_risk_decision_with_legs(
        legs=[{"sequence": 1, "qty": 0.005}, {"sequence": 2, "qty": 0.005}],
    )
    enriched = _make_enriched_signal_unified(tp_count=2, entry_count=2)
    result = gate.process_signal(enriched, [], "NONE")
    assert result.review_reason is None
    assert result.trade_chain.execution_mode == "UNIFIED_PLAN"
    cmd_types = [c.command_type for c in result.execution_commands]
    assert cmd_types == ["PLACE_ENTRY_WITH_ATTACHED_TPSL", "PLACE_ENTRY"]
    leg1_payload = json.loads(result.execution_commands[0].payload_json)
    # Final TP (sequence 2 = 52000.0) attached to leg 1
    assert leg1_payload["attached_tpsl"]["take_profit"] == 52000.0
    assert leg1_payload["attached_tpsl"]["stop_loss"] == 49000.0


def test_unified_plan_no_sl_falls_through_to_d_position_tpsl():
    """When sl_price is None, UNIFIED_PLAN is not selected — chain gets D_POSITION_TPSL."""
    from src.parser_v2.contracts.entities import Price, TakeProfit
    from src.runtime_v2.signal_enrichment.models import (
        EnrichedCanonicalMessage, EnrichedEntryLeg,
        EnrichedSignalPayload, ManagementPlanConfig,
    )
    from src.runtime_v2.lifecycle.risk_capacity import RiskDecision

    gate = _make_gate_unified()
    entries = [EnrichedEntryLeg(
        sequence=1, entry_type="LIMIT",
        price=Price(raw="50000", value=50000.0), weight=1.0,
    )]
    tps = [TakeProfit(sequence=1, price=Price(raw="51000", value=51000.0))]
    signal = EnrichedSignalPayload(
        symbol="BTC/USDT", side="LONG", entry_structure="ONE_SHOT",
        entries=entries, take_profits=tps, stop_loss=None,  # no SL
    )
    enriched = EnrichedCanonicalMessage(
        enrichment_id=5, canonical_message_id=50, raw_message_id=500,
        trader_id="t1", account_id="acc1",
        primary_class="SIGNAL", enrichment_decision="PASS",
        enriched_signal=signal, management_plan=ManagementPlanConfig(),
        policy_snapshot={},
    )
    gate._risk.validate = lambda *a, **kw: RiskDecision(
        passed=True, reason=None, size_usdt=500.0, leverage=10,
        risk_snapshot={"entry_price": 50000.0, "size_usdt": 500.0, "leverage": 10},
    )
    result = gate.process_signal(enriched, [], "NONE")
    assert result.review_reason is None
    assert result.trade_chain.execution_mode == "D_POSITION_TPSL"


def test_unified_plan_execution_mode_written_to_chain():
    """Chain record carries UNIFIED_PLAN execution_mode when path is active."""
    gate = _make_gate_unified()
    enriched = _make_enriched_signal_unified(tp_count=1, entry_count=1)
    result = gate.process_signal(enriched, [], "NONE")
    assert result.trade_chain is not None
    assert result.trade_chain.execution_mode == "UNIFIED_PLAN"


def test_unified_plan_protection_style_attached_full_for_be_move():
    """BE move on UNIFIED_PLAN chain reports protection_style='attached_full'."""
    gate = _make_gate()
    enriched = _make_update_enriched(scope_hint="SINGLE_SIGNAL", symbols=["BTC/USDT"])
    chain = _make_open_chain(entry_avg_price=50000.0)
    chain = chain.model_copy(update={
        "execution_mode": "UNIFIED_PLAN",
        "risk_snapshot_json": '{"hedge_mode": false}',
    })
    result = gate.process_update(enriched, [chain], {})
    cr = result.chain_results[0]
    command = next(c for c in cr.execution_commands if c.command_type == "MOVE_STOP_TO_BREAKEVEN")
    payload = json.loads(command.payload_json)
    assert payload["protection_style"] == "attached_full"


def test_entry_changing_update_limit_to_market_emits_cancel_and_new_entry():
    from src.parser_v2.contracts.canonical_message import (
        ActionItem, ModifyEntriesOperation, TargetActionGroup,
    )
    from src.parser_v2.contracts.context import TargetHints
    from src.parser_v2.contracts.entities import EntryLeg
    from src.runtime_v2.lifecycle.models import ExecutionCommand

    plan_state = json.dumps({
        "plan_version": 1,
        "rebuild_policy": "NONE",
        "protection_policy": "TPSL_ATTACHED_FIRST_LEG",
        "stop_loss": 49000.0,
        "final_tp": 51000.0,
        "intermediate_tps": [],
        "legs": [{
            "leg_id": "leg_1",
            "sequence": 1,
            "entry_type": "LIMIT",
            "price": 50000.0,
            "risk_budget": 100.0,
            "qty": 0.01,
            "qty_mode": "fixed",
            "status": "PENDING",
            "client_order_id": "place_entry_attached:1:leg1",
        }],
    })
    chain = _make_open_chain(state="WAITING_ENTRY")
    chain = chain.model_copy(update={
        "execution_mode": "UNIFIED_PLAN",
        "expected_stop_price": 49000.0,
        "risk_snapshot_json": json.dumps({
            "sl_price": 49000.0,
            "risk_amount": 100.0,
            "entry_price": 50000.0,
            "leverage": 1,
            "hedge_mode": False,
            "legs": [{
                "sequence": 1,
                "entry_type": "LIMIT",
                "price": 50000.0,
                "risk_amount": 100.0,
                "qty": 0.01,
                "qty_mode": "fixed",
                "weight": 1.0,
            }],
        }),
        "plan_state_json": plan_state,
        "risk_remaining": 100.0,
    })
    action = ActionItem(
        action_type="MODIFY_ENTRIES",
        modify_entries=ModifyEntriesOperation(
            kind="MARKET_NOW",
            entries=[EntryLeg(sequence=1, entry_type="MARKET")],
        ),
        source_intent="MODIFY_ENTRY",
    )
    tag = TargetActionGroup(
        targeting=TargetHints(scope_hint="SINGLE_SIGNAL", symbols=["BTC/USDT"]),
        actions=[action],
    )
    enriched = _make_update_enriched(scope_hint="SINGLE_SIGNAL", symbols=["BTC/USDT"])
    enriched.enriched_actions = [tag]
    active_cmds = [
        ExecutionCommand(
            trade_chain_id=chain.trade_chain_id,
            command_type="PLACE_ENTRY_WITH_ATTACHED_TPSL",
            payload_json="{}",
            idempotency_key="place_entry_attached:1:leg1",
            status="PENDING",
        )
    ]

    result = _make_gate().process_update(enriched, [chain], {chain.trade_chain_id: active_cmds})
    all_cmds = [c for cr in result.chain_results for c in cr.execution_commands]
    cmd_types = {c.command_type for c in all_cmds}
    assert "CANCEL_PENDING_ENTRY" in cmd_types
    assert "PLACE_ENTRY_WITH_ATTACHED_TPSL" in cmd_types
    new_entry = next(c for c in all_cmds if c.command_type == "PLACE_ENTRY_WITH_ATTACHED_TPSL")
    payload = json.loads(new_entry.payload_json)
    assert payload["entry_type"] == "MARKET"


def test_entry_changing_update_leg2_replacement_stays_plain_place_entry():
    from src.parser_v2.contracts.canonical_message import (
        ActionItem, ModifyEntriesOperation, TargetActionGroup,
    )
    from src.parser_v2.contracts.context import TargetHints
    from src.parser_v2.contracts.entities import EntryLeg, Price
    from src.runtime_v2.lifecycle.models import ExecutionCommand

    plan_state = json.dumps({
        "plan_version": 1,
        "rebuild_policy": "NONE",
        "protection_policy": "TPSL_ATTACHED_FIRST_LEG",
        "stop_loss": 49000.0,
        "final_tp": 51000.0,
        "intermediate_tps": [],
        "legs": [
            {
                "leg_id": "leg_1",
                "sequence": 1,
                "entry_type": "LIMIT",
                "price": 50000.0,
                "risk_budget": 50.0,
                "qty": 0.005,
                "qty_mode": "fixed",
                "status": "FILLED",
                "client_order_id": "place_entry_attached:1:leg1",
            },
            {
                "leg_id": "leg_2",
                "sequence": 2,
                "entry_type": "LIMIT",
                "price": 48000.0,
                "risk_budget": 50.0,
                "qty": 0.0167,
                "qty_mode": "fixed",
                "status": "PENDING",
                "client_order_id": "place_entry:1:leg2",
            },
        ],
    })
    chain = _make_open_chain(state="OPEN")
    chain = chain.model_copy(update={
        "execution_mode": "UNIFIED_PLAN",
        "expected_stop_price": 49000.0,
        "risk_snapshot_json": json.dumps({
            "sl_price": 49000.0,
            "risk_amount": 100.0,
            "entry_price": 50000.0,
            "leverage": 1,
            "hedge_mode": False,
            "legs": [
                {
                    "sequence": 1,
                    "entry_type": "LIMIT",
                    "price": 50000.0,
                    "risk_amount": 50.0,
                    "qty": 0.005,
                    "qty_mode": "fixed",
                    "weight": 0.5,
                },
                {
                    "sequence": 2,
                    "entry_type": "LIMIT",
                    "price": 48000.0,
                    "risk_amount": 50.0,
                    "qty": 0.0167,
                    "qty_mode": "fixed",
                    "weight": 0.5,
                },
            ],
        }),
        "plan_state_json": plan_state,
        "risk_remaining": 50.0,
    })
    action = ActionItem(
        action_type="MODIFY_ENTRIES",
        modify_entries=ModifyEntriesOperation(
            kind="UPDATE_PRICE",
            entries=[
                EntryLeg(
                    sequence=2,
                    entry_type="LIMIT",
                    price=Price(raw="47000", value=47000.0),
                )
            ],
        ),
        source_intent="MODIFY_ENTRY",
    )
    tag = TargetActionGroup(
        targeting=TargetHints(scope_hint="SINGLE_SIGNAL", symbols=["BTC/USDT"]),
        actions=[action],
    )
    enriched = _make_update_enriched(scope_hint="SINGLE_SIGNAL", symbols=["BTC/USDT"])
    enriched.enriched_actions = [tag]
    active_cmds = [
        ExecutionCommand(
            trade_chain_id=chain.trade_chain_id,
            command_type="PLACE_ENTRY",
            payload_json="{}",
            idempotency_key="place_entry:1:leg2",
            status="PENDING",
        )
    ]

    result = _make_gate().process_update(enriched, [chain], {chain.trade_chain_id: active_cmds})
    all_cmds = [c for cr in result.chain_results for c in cr.execution_commands]
    assert [c.command_type for c in all_cmds] == ["CANCEL_PENDING_ENTRY", "PLACE_ENTRY"]
    new_entry = all_cmds[1]
    payload = json.loads(new_entry.payload_json)
    assert payload["entry_type"] == "LIMIT"
    assert payload["price"] == 47000.0
    assert "attached_tpsl" not in payload


def test_apply_modify_entries_emits_changed_entries_for_price_updates():
    from src.parser_v2.contracts.canonical_message import (
        ActionItem, ModifyEntriesOperation, TargetActionGroup,
    )
    from src.parser_v2.contracts.context import TargetHints
    from src.parser_v2.contracts.entities import EntryLeg, Price
    from src.runtime_v2.lifecycle.models import ExecutionCommand

    plan_legs = [
        {
            "leg_id": "leg_1",
            "sequence": 1,
            "entry_type": "LIMIT",
            "price": 50000.0,
            "risk_budget": 50.0,
            "qty": 0.005,
            "qty_mode": "fixed",
            "status": "FILLED",
            "client_order_id": "place_entry_attached:1:leg1",
        },
        {
            "leg_id": "leg_2",
            "sequence": 2,
            "entry_type": "LIMIT",
            "price": 48000.0,
            "risk_budget": 50.0,
            "qty": 0.0167,
            "qty_mode": "fixed",
            "status": "PENDING",
            "client_order_id": "place_entry:1:leg2",
        },
    ]
    risk_legs = [
        {
            "sequence": 1,
            "entry_type": "LIMIT",
            "price": 50000.0,
            "risk_amount": 50.0,
            "qty": 0.005,
            "qty_mode": "fixed",
            "weight": 0.5,
        },
        {
            "sequence": 2,
            "entry_type": "LIMIT",
            "price": 48000.0,
            "risk_amount": 50.0,
            "qty": 0.0167,
            "qty_mode": "fixed",
            "weight": 0.5,
        },
    ]
    plan_state = json.dumps({
        "plan_version": 1,
        "rebuild_policy": "NONE",
        "protection_policy": "TPSL_ATTACHED_FIRST_LEG",
        "stop_loss": 49000.0,
        "final_tp": 51000.0,
        "intermediate_tps": [],
        "legs": plan_legs,
    })
    chain = _make_open_chain(state="OPEN")
    chain = chain.model_copy(update={
        "execution_mode": "UNIFIED_PLAN",
        "expected_stop_price": 49000.0,
        "risk_snapshot_json": json.dumps({
            "sl_price": 49000.0,
            "risk_amount": 100.0,
            "entry_price": 50000.0,
            "leverage": 1,
            "hedge_mode": False,
            "legs": risk_legs,
        }),
        "plan_state_json": plan_state,
        "risk_remaining": 50.0,
    })
    action = ActionItem(
        action_type="MODIFY_ENTRIES",
        modify_entries=ModifyEntriesOperation(
            kind="UPDATE_PRICE",
            entries=[
                EntryLeg(
                    sequence=2,
                    entry_type="LIMIT",
                    price=Price(raw="47000", value=47000.0),
                )
            ],
        ),
        source_intent="MODIFY_ENTRY",
    )
    tag = TargetActionGroup(
        targeting=TargetHints(scope_hint="SINGLE_SIGNAL", symbols=["BTC/USDT"]),
        actions=[action],
    )
    enriched = _make_update_enriched(scope_hint="SINGLE_SIGNAL", symbols=["BTC/USDT"])
    enriched.enriched_actions = [tag]
    active_cmds = [
        ExecutionCommand(
            trade_chain_id=chain.trade_chain_id,
            command_type="PLACE_ENTRY",
            payload_json="{}",
            idempotency_key="place_entry:1:leg2",
            status="PENDING",
        )
    ]

    result = _make_gate()._apply_modify_entries(
        enriched,
        chain,
        action,
        active_cmds,
    )
    accepted = [
        e for e in result.lifecycle_events
        if e.event_type == "TELEGRAM_UPDATE_ACCEPTED"
    ]
    assert len(accepted) == 1
    payload = json.loads(accepted[0].payload_json)
    assert payload["action"] == "MODIFY_ENTRIES"
    assert payload["changed_entries"] == [
        {"sequence": 2, "old_price": 48000.0, "new_price": 47000.0}
    ]


def test_update_chain_result_has_new_plan_state_json_field():
    from src.runtime_v2.lifecycle.entry_gate import UpdateChainResult
    cr = UpdateChainResult(
        trade_chain_id=1,
        new_lifecycle_state=None,
        new_be_protection_status=None,
        lifecycle_events=[],
        execution_commands=[],
        new_plan_state_json='{"legs": []}',
    )
    assert cr.new_plan_state_json == '{"legs": []}'


def test_update_chain_result_new_plan_state_json_defaults_to_none():
    from src.runtime_v2.lifecycle.entry_gate import UpdateChainResult
    cr = UpdateChainResult(
        trade_chain_id=1,
        new_lifecycle_state=None,
        new_be_protection_status=None,
        lifecycle_events=[],
        execution_commands=[],
    )
    assert cr.new_plan_state_json is None

def test_write_update_clean_log_rejected_includes_reason_and_rejected_actions(tmp_path):
    import json
    import sqlite3
    from src.core.migrations import apply_migrations as _core_apply
    from src.runtime_v2.lifecycle.entry_gate import UpdateChainResult, _write_update_clean_log
    from src.runtime_v2.lifecycle.models import LifecycleEvent

    ops_db = str(tmp_path / "ops.sqlite3")
    _core_apply(ops_db, "db/ops_migrations")
    conn = sqlite3.connect(ops_db)
    conn.execute(
        """INSERT INTO ops_trade_chains (
            source_enrichment_id, canonical_message_id, raw_message_id,
            trader_id, account_id, symbol, side, lifecycle_state, entry_mode,
            management_plan_json, plan_state_json, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))""",
        (1, 1, 1, "trader_a", "acc_1", "BTC/USDT", "LONG", "OPEN", "ONE_SHOT", "{}", "{}"),
    )
    chain_id = conn.execute(
        "SELECT trade_chain_id FROM ops_trade_chains WHERE source_enrichment_id=?",
        (1,),
    ).fetchone()[0]

    cr = UpdateChainResult(
        trade_chain_id=chain_id,
        new_lifecycle_state=None,
        new_be_protection_status=None,
        lifecycle_events=[LifecycleEvent(
            trade_chain_id=chain_id,
            event_type="NOOP_ALREADY_PROTECTED_BE",
            source_type="telegram_update",
            source_id="1",
            payload_json=json.dumps({"reason": "already_protected"}),
            idempotency_key="noop:1",
        )],
        execution_commands=[],
    )

    with conn:
        _write_update_clean_log(conn, cr, canonical_message_id=1, link=None)

    row_count = conn.execute(
        "SELECT COUNT(*) FROM ops_notification_outbox WHERE notification_type='UPDATE_REJECTED'"
    ).fetchone()[0]
    row = conn.execute(
        "SELECT notification_type, payload_json FROM ops_notification_outbox WHERE notification_type='UPDATE_REJECTED'"
    ).fetchall()
    conn.close()

    assert row_count == 1
    assert len(row) == 1
    payload = json.loads(row[0][1])
    assert payload["reason"] == "already_protected"
    assert payload["rejected_actions"] == ["NOOP_ALREADY_PROTECTED_BE"]


def test_write_update_clean_log_partial_keeps_changed_and_rejected_actions(tmp_path):
    import json
    import sqlite3
    from src.core.migrations import apply_migrations as _core_apply
    from src.runtime_v2.lifecycle.entry_gate import UpdateChainResult, _write_update_clean_log
    from src.runtime_v2.lifecycle.models import LifecycleEvent

    ops_db = str(tmp_path / "ops.sqlite3")
    _core_apply(ops_db, "db/ops_migrations")
    conn = sqlite3.connect(ops_db)
    conn.execute(
        """INSERT INTO ops_trade_chains (
            source_enrichment_id, canonical_message_id, raw_message_id,
            trader_id, account_id, symbol, side, lifecycle_state, entry_mode,
            management_plan_json, plan_state_json, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))""",
        (1, 1, 1, "trader_a", "acc_1", "BTC/USDT", "LONG", "OPEN", "ONE_SHOT", "{}", "{}"),
    )
    chain_id = conn.execute(
        "SELECT trade_chain_id FROM ops_trade_chains WHERE source_enrichment_id=?",
        (1,),
    ).fetchone()[0]

    cr = UpdateChainResult(
        trade_chain_id=chain_id,
        new_lifecycle_state=None,
        new_be_protection_status=None,
        lifecycle_events=[
            LifecycleEvent(
                trade_chain_id=chain_id,
                event_type="TELEGRAM_UPDATE_ACCEPTED",
                source_type="telegram_update",
                source_id="1",
                payload_json=json.dumps({
                    "action": "MODIFY_ENTRIES",
                    "changed_entries": [
                        {"sequence": 2, "old_price": 48000.0, "new_price": 47000.0},
                    ],
                }),
                idempotency_key="accepted:1",
            ),
            LifecycleEvent(
                trade_chain_id=chain_id,
                event_type="NOOP_ALREADY_PROTECTED_BE",
                source_type="telegram_update",
                source_id="1",
                payload_json=json.dumps({"reason": "already_protected"}),
                idempotency_key="noop:1",
            ),
        ],
        execution_commands=[],
    )

    with conn:
        _write_update_clean_log(conn, cr, canonical_message_id=1, link=None)

    row = conn.execute(
        "SELECT notification_type, payload_json FROM ops_notification_outbox WHERE notification_type='UPDATE_PARTIAL'"
    ).fetchone()
    conn.close()

    assert row is not None
    assert row[0] == "UPDATE_PARTIAL"
    payload = json.loads(row[1])
    assert payload["reason"] == "already_protected"
    assert payload["rejected_actions"] == ["NOOP_ALREADY_PROTECTED_BE"]
    assert payload["changed"] == [
        {"field": "Entry_2", "old": 48000.0, "new": 47000.0},
    ]


def test_write_update_clean_log_omits_reason_when_no_noop_payload_provides_it(tmp_path):
    import json
    import sqlite3
    from src.core.migrations import apply_migrations as _core_apply
    from src.runtime_v2.lifecycle.entry_gate import UpdateChainResult, _write_update_clean_log
    from src.runtime_v2.lifecycle.models import LifecycleEvent

    ops_db = str(tmp_path / "ops.sqlite3")
    _core_apply(ops_db, "db/ops_migrations")
    conn = sqlite3.connect(ops_db)
    conn.execute(
        """INSERT INTO ops_trade_chains (
            source_enrichment_id, canonical_message_id, raw_message_id,
            trader_id, account_id, symbol, side, lifecycle_state, entry_mode,
            management_plan_json, plan_state_json, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))""",
        (1, 1, 1, "trader_a", "acc_1", "BTC/USDT", "LONG", "OPEN", "ONE_SHOT", "{}", "{}"),
    )
    chain_id = conn.execute(
        "SELECT trade_chain_id FROM ops_trade_chains WHERE source_enrichment_id=?",
        (1,),
    ).fetchone()[0]

    cr = UpdateChainResult(
        trade_chain_id=chain_id,
        new_lifecycle_state=None,
        new_be_protection_status=None,
        lifecycle_events=[LifecycleEvent(
            trade_chain_id=chain_id,
            event_type="NOOP_ALREADY_PROTECTED_BE",
            source_type="telegram_update",
            source_id="1",
            payload_json=json.dumps({}),
            idempotency_key="noop:1",
        )],
        execution_commands=[],
    )

    with conn:
        _write_update_clean_log(conn, cr, canonical_message_id=1, link=None)

    row = conn.execute(
        "SELECT payload_json FROM ops_notification_outbox WHERE notification_type='UPDATE_REJECTED'"
    ).fetchone()
    conn.close()

    assert row is not None
    payload = json.loads(row[0])
    assert "reason" not in payload


# ── _apply_market_entry_now helpers ──────────────────────────────────────────


def _make_gate_attached():
    from src.runtime_v2.lifecycle.entry_gate import LifecycleEntryGate
    from src.runtime_v2.lifecycle.risk_capacity import RiskCapacityEngine
    from src.runtime_v2.lifecycle.static_exchange_data_port import StaticExchangeDataPort
    return LifecycleEntryGate(
        risk_engine=RiskCapacityEngine(),
        exchange_port=StaticExchangeDataPort(),
        simple_attached_enabled=True,
    )


def _make_two_step_chain_for_market(
    market_convert_mode: str = "cancel_subsequent",
    risk_remaining: float = 0.0,
):
    import json
    from src.runtime_v2.lifecycle.models import TradeChain
    from src.runtime_v2.signal_enrichment.models import ManagementPlanConfig
    plan = json.dumps({
        "plan_version": 1,
        "protection_policy": "TPSL_ATTACHED_FIRST_LEG",
        "rebuild_policy": "NONE",
        "risk_policy": "REBALANCE_REMAINING_RISK_ON_REPLAN",
        "stop_loss": 800.0,
        "final_tp": 1200.0,
        "intermediate_tps": [],
        "legs": [
            {
                "leg_id": "leg_1", "sequence": 1, "entry_type": "LIMIT", "price": 1000.0,
                "risk_budget": 70.0, "qty": 0.35, "qty_mode": "fixed", "weight": 0.7,
                "status": "PENDING", "client_order_id": "place_entry_attached:5:leg1",
            },
            {
                "leg_id": "leg_2", "sequence": 2, "entry_type": "LIMIT", "price": 900.0,
                "risk_budget": 30.0, "qty": 0.15, "qty_mode": "fixed", "weight": 0.3,
                "status": "PENDING", "client_order_id": "place_entry:5:leg2",
            },
        ],
    })
    risk_snap = json.dumps({
        "risk_amount": 100.0,
        "sl_price": 800.0,
        "entry_price": 1000.0,
        "leverage": 1,
        "hedge_mode": False,
        "legs": [
            {"sequence": 1, "risk_amount": 70.0, "qty": 0.35, "qty_mode": "fixed", "weight": 0.7},
            {"sequence": 2, "risk_amount": 30.0, "qty": 0.15, "qty_mode": "fixed", "weight": 0.3},
        ],
    })
    mp = ManagementPlanConfig(market_convert_mode=market_convert_mode)
    return TradeChain(
        trade_chain_id=1,
        source_enrichment_id=5, canonical_message_id=50, raw_message_id=500,
        trader_id="t1", account_id="acc_1",
        symbol="TOKEN/USDT:USDT", side="LONG",
        lifecycle_state="WAITING_ENTRY", entry_mode="TWO_STEP",
        expected_stop_price=800.0,
        management_plan_json=mp.model_dump_json(),
        risk_snapshot_json=risk_snap,
        plan_state_json=plan,
        risk_remaining=risk_remaining,
    )


def _make_market_now_update_enriched(
    canonical_message_id: int = 200,
    *,
    entries=None,
):
    from src.parser_v2.contracts.canonical_message import (
        ActionItem, ModifyEntriesOperation, TargetActionGroup,
    )
    from src.parser_v2.contracts.context import TargetHints
    from src.parser_v2.contracts.entities import EntryLeg
    from src.runtime_v2.signal_enrichment.models import (
        ManagementPlanConfig, EnrichedCanonicalMessage,
    )
    if entries is None:
        entries = []
    typed_entries = [
        item if isinstance(item, EntryLeg) else EntryLeg(**item)
        for item in entries
    ]
    action = ActionItem(
        action_type="MODIFY_ENTRIES",
        modify_entries=ModifyEntriesOperation(kind="MARKET_NOW", entries=typed_entries),
        source_intent="MODIFY_ENTRY",
    )
    tag = TargetActionGroup(
        targeting=TargetHints(scope_hint="SINGLE_SIGNAL"),
        actions=[action],
    )
    return EnrichedCanonicalMessage(
        enrichment_id=20, canonical_message_id=canonical_message_id,
        raw_message_id=200, trader_id="t1", account_id="acc_1",
        primary_class="UPDATE", enrichment_decision="PASS",
        enriched_signal=None, enriched_actions=[tag],
        management_plan=ManagementPlanConfig(), policy_snapshot={},
    )


# ── cancel-mode tests ─────────────────────────────────────────────────────────

def test_market_entry_now_cancel_mode_produces_two_cancels_and_one_market_entry():
    import json
    gate = _make_gate_attached()
    chain = _make_two_step_chain_for_market("cancel_subsequent")
    enriched = _make_market_now_update_enriched()
    result = gate.process_update(enriched, [chain], {1: []})

    assert len(result.chain_results) == 1
    cr = result.chain_results[0]
    cmd_types = [c.command_type for c in cr.execution_commands]
    assert cmd_types.count("CANCEL_PENDING_ENTRY") == 2
    assert cmd_types.count("PLACE_ENTRY_WITH_ATTACHED_TPSL") == 1


def test_market_entry_now_cancel_mode_market_command_uses_full_risk_deferred():
    import json
    gate = _make_gate_attached()
    chain = _make_two_step_chain_for_market("cancel_subsequent")
    enriched = _make_market_now_update_enriched()
    result = gate.process_update(enriched, [chain], {1: []})

    cr = result.chain_results[0]
    market_cmd = next(
        c for c in cr.execution_commands
        if c.command_type == "PLACE_ENTRY_WITH_ATTACHED_TPSL"
    )
    p = json.loads(market_cmd.payload_json)
    assert p["entry_type"] == "MARKET"
    assert p.get("qty_mode") == "deferred_market"
    assert p.get("risk_amount") == pytest.approx(100.0)


def test_market_entry_now_cancel_mode_plan_marks_leg1_market_leg2_cancelled():
    import json
    gate = _make_gate_attached()
    chain = _make_two_step_chain_for_market("cancel_subsequent")
    enriched = _make_market_now_update_enriched()
    result = gate.process_update(enriched, [chain], {1: []})

    cr = result.chain_results[0]
    assert cr.new_plan_state_json is not None
    plan = json.loads(cr.new_plan_state_json)
    by_seq = {l["sequence"]: l for l in plan["legs"]}
    assert by_seq[1]["entry_type"] == "MARKET"
    assert by_seq[1]["status"] == "PENDING"
    assert by_seq[1]["qty_mode"] == "deferred_market"
    assert by_seq[2]["status"] == "CANCELLED"


def test_market_entry_now_with_primary_market_entry_payload_still_cancels_subsequent_legs():
    gate = _make_gate_attached()
    chain = _make_two_step_chain_for_market("cancel_subsequent")
    enriched = _make_market_now_update_enriched(
        entries=[{"sequence": 1, "entry_type": "MARKET"}],
    )

    result = gate.process_update(enriched, [chain], {1: []})

    cr = result.chain_results[0]
    cmd_types = [c.command_type for c in cr.execution_commands]
    assert cmd_types.count("CANCEL_PENDING_ENTRY") == 2
    assert cmd_types.count("PLACE_ENTRY_WITH_ATTACHED_TPSL") == 1


def test_market_entry_now_cancel_mode_emits_telegram_update_accepted_event():
    gate = _make_gate_attached()
    chain = _make_two_step_chain_for_market("cancel_subsequent")
    enriched = _make_market_now_update_enriched()
    result = gate.process_update(enriched, [chain], {1: []})

    cr = result.chain_results[0]
    event_types = [e.event_type for e in cr.lifecycle_events]
    assert "TELEGRAM_UPDATE_ACCEPTED" in event_types


# ── keep-mode tests ──────────────────────────────────────────────────────────

def test_market_entry_now_keep_mode_produces_one_cancel_and_one_market_entry():
    import json
    gate = _make_gate_attached()
    chain = _make_two_step_chain_for_market("keep_subsequent")
    enriched = _make_market_now_update_enriched()
    result = gate.process_update(enriched, [chain], {1: []})

    assert len(result.chain_results) == 1
    cr = result.chain_results[0]
    cmd_types = [c.command_type for c in cr.execution_commands]
    assert cmd_types.count("CANCEL_PENDING_ENTRY") == 1  # leg1 only
    assert cmd_types.count("PLACE_ENTRY_WITH_ATTACHED_TPSL") == 1


def test_market_entry_now_keep_mode_uses_leg1_risk_only():
    import json
    gate = _make_gate_attached()
    chain = _make_two_step_chain_for_market("keep_subsequent")
    enriched = _make_market_now_update_enriched()
    result = gate.process_update(enriched, [chain], {1: []})

    cr = result.chain_results[0]
    market_cmd = next(
        c for c in cr.execution_commands
        if c.command_type == "PLACE_ENTRY_WITH_ATTACHED_TPSL"
    )
    p = json.loads(market_cmd.payload_json)
    assert p["entry_type"] == "MARKET"
    assert p.get("qty_mode") == "deferred_market"
    assert p.get("risk_amount") == pytest.approx(70.0)  # leg1 risk_amount only


def test_market_entry_now_keep_mode_plan_leg1_market_leg2_pending_unchanged():
    import json
    gate = _make_gate_attached()
    chain = _make_two_step_chain_for_market("keep_subsequent")
    enriched = _make_market_now_update_enriched()
    result = gate.process_update(enriched, [chain], {1: []})

    cr = result.chain_results[0]
    assert cr.new_plan_state_json is not None
    plan = json.loads(cr.new_plan_state_json)
    by_seq = {l["sequence"]: l for l in plan["legs"]}
    assert by_seq[1]["entry_type"] == "MARKET"
    assert by_seq[1]["status"] == "PENDING"
    assert by_seq[1]["qty_mode"] == "deferred_market"
    # leg2 completely untouched
    assert by_seq[2]["status"] == "PENDING"
    assert by_seq[2]["entry_type"] == "LIMIT"
    assert by_seq[2]["price"] == pytest.approx(900.0)


def test_market_entry_now_no_pending_legs_returns_review():
    import json
    from src.runtime_v2.lifecycle.models import TradeChain
    from src.runtime_v2.signal_enrichment.models import ManagementPlanConfig
    # All legs already FILLED
    plan = json.dumps({"legs": [
        {"leg_id": "leg_1", "sequence": 1, "entry_type": "LIMIT", "price": 1000.0,
         "risk_budget": 100.0, "qty": 0.5, "qty_mode": "fixed", "weight": 1.0,
         "status": "FILLED", "client_order_id": "place_entry_attached:5:leg1"},
    ]})
    risk_snap = json.dumps({"risk_amount": 100.0, "sl_price": 800.0, "entry_price": 1000.0,
                             "leverage": 1, "hedge_mode": False, "legs": []})
    chain = TradeChain(
        trade_chain_id=1,
        source_enrichment_id=5, canonical_message_id=50, raw_message_id=500,
        trader_id="t1", account_id="acc_1", symbol="TOKEN/USDT:USDT", side="LONG",
        lifecycle_state="OPEN", entry_mode="ONE_SHOT",
        management_plan_json=ManagementPlanConfig().model_dump_json(),
        risk_snapshot_json=risk_snap, plan_state_json=plan,
    )
    gate = _make_gate_attached()
    enriched = _make_market_now_update_enriched()
    result = gate.process_update(enriched, [chain], {1: []})
    cr = result.chain_results[0]
    assert any(e.event_type == "REVIEW_REQUIRED" for e in cr.lifecycle_events)
    assert cr.new_plan_state_json is None
    assert cr.execution_commands == []


def test_market_entry_now_single_pending_leg_produces_one_cancel_one_market():
    import json
    from src.runtime_v2.lifecycle.models import TradeChain
    from src.runtime_v2.signal_enrichment.models import ManagementPlanConfig
    plan = json.dumps({
        "plan_version": 1, "protection_policy": "TPSL_ATTACHED_FIRST_LEG",
        "rebuild_policy": "NONE", "risk_policy": "REBALANCE_REMAINING_RISK_ON_REPLAN",
        "stop_loss": 800.0, "final_tp": 1200.0, "intermediate_tps": [],
        "legs": [
            {"leg_id": "leg_1", "sequence": 1, "entry_type": "LIMIT", "price": 1000.0,
             "risk_budget": 100.0, "qty": 0.5, "qty_mode": "fixed", "weight": 1.0,
             "status": "PENDING", "client_order_id": "place_entry_attached:5:leg1"},
        ],
    })
    risk_snap = json.dumps({
        "risk_amount": 100.0, "sl_price": 800.0, "entry_price": 1000.0,
        "leverage": 1, "hedge_mode": False,
        "legs": [{"sequence": 1, "risk_amount": 100.0, "qty": 0.5, "qty_mode": "fixed", "weight": 1.0}],
    })
    # Test with cancel mode
    chain_cancel = TradeChain(
        trade_chain_id=1,
        source_enrichment_id=5, canonical_message_id=50, raw_message_id=500,
        trader_id="t1", account_id="acc_1", symbol="TOKEN/USDT:USDT", side="LONG",
        lifecycle_state="WAITING_ENTRY", entry_mode="ONE_SHOT",
        management_plan_json=ManagementPlanConfig(market_convert_mode="cancel_subsequent").model_dump_json(),
        risk_snapshot_json=risk_snap, plan_state_json=plan,
    )
    gate = _make_gate_attached()
    enriched = _make_market_now_update_enriched()
    result_cancel = gate.process_update(enriched, [chain_cancel], {1: []})
    cr_cancel = result_cancel.chain_results[0]
    cmd_types = [c.command_type for c in cr_cancel.execution_commands]
    assert cmd_types.count("CANCEL_PENDING_ENTRY") == 1
    assert cmd_types.count("PLACE_ENTRY_WITH_ATTACHED_TPSL") == 1

    # Test with keep mode — same result (no "others" to keep)
    chain_keep = chain_cancel.model_copy(update={
        "management_plan_json": ManagementPlanConfig(market_convert_mode="keep_subsequent").model_dump_json()
    })
    result_keep = gate.process_update(enriched, [chain_keep], {1: []})
    cr_keep = result_keep.chain_results[0]
    cmd_types_keep = [c.command_type for c in cr_keep.execution_commands]
    assert cmd_types_keep.count("CANCEL_PENDING_ENTRY") == 1
    assert cmd_types_keep.count("PLACE_ENTRY_WITH_ATTACHED_TPSL") == 1


def test_persist_update_saves_new_plan_state_json_to_db(tmp_path):
    import json
    import sqlite3
    from src.core.migrations import apply_migrations as _core_apply
    from src.runtime_v2.lifecycle.entry_gate import LifecycleEntryGate, UpdateGateResult, UpdateChainResult
    from src.runtime_v2.lifecycle.risk_capacity import RiskCapacityEngine
    from src.runtime_v2.lifecycle.models import LifecycleEvent
    from src.runtime_v2.signal_enrichment.models import ManagementPlanConfig, EnrichedCanonicalMessage
    from src.runtime_v2.lifecycle.static_exchange_data_port import StaticExchangeDataPort

    # Bootstrap parser DB (needs enriched_canonical_messages)
    parser_db = str(tmp_path / "parser.sqlite3")
    _core_apply(parser_db, "db/migrations")

    # Bootstrap ops DB
    ops_db = str(tmp_path / "ops.sqlite3")
    _core_apply(ops_db, "db/ops_migrations")
    conn = sqlite3.connect(ops_db)
    # Insert a minimal chain
    original_plan = json.dumps({"legs": [{"sequence": 1, "status": "PENDING"}]})
    conn.execute(
        """INSERT INTO ops_trade_chains (
            source_enrichment_id, canonical_message_id, raw_message_id,
            trader_id, account_id, symbol, side, lifecycle_state, entry_mode,
            management_plan_json, plan_state_json, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))""",
        (1, 1, 1, "t1", "acc_1", "SYM", "LONG", "WAITING_ENTRY", "ONE_SHOT", "{}", original_plan),
    )
    conn.commit()
    chain_id = conn.execute("SELECT trade_chain_id FROM ops_trade_chains LIMIT 1").fetchone()[0]
    conn.close()

    new_plan = json.dumps({"legs": [{"sequence": 1, "status": "FILLED"}]})
    cr = UpdateChainResult(
        trade_chain_id=chain_id,
        new_lifecycle_state=None,
        new_be_protection_status=None,
        lifecycle_events=[LifecycleEvent(
            trade_chain_id=chain_id, event_type="TELEGRAM_UPDATE_ACCEPTED",
            source_type="test", idempotency_key="test:persist:1",
        )],
        execution_commands=[],
        new_plan_state_json=new_plan,
    )

    enriched = EnrichedCanonicalMessage(
        enrichment_id=99, canonical_message_id=99, raw_message_id=99,
        trader_id="t1", account_id="acc_1", primary_class="UPDATE",
        enrichment_decision="PASS", policy_snapshot={},
    )

    gate = _make_gate_attached()
    from src.runtime_v2.lifecycle.entry_gate import LifecycleGateWorker
    worker = LifecycleGateWorker(
        parser_db_path=parser_db, ops_db_path=ops_db,
        gate=gate, chain_repo=None, event_repo=None,
        command_repo=None, snapshot_repo=None, control_repo=None,
    )
    worker._persist_update(enriched, UpdateGateResult(chain_results=[cr], review_events=[]))

    # Verify plan was saved
    conn2 = sqlite3.connect(ops_db)
    row = conn2.execute(
        "SELECT plan_state_json FROM ops_trade_chains WHERE trade_chain_id=?", (chain_id,)
    ).fetchone()
    conn2.close()
    saved_plan = json.loads(row[0])
    assert saved_plan["legs"][0]["status"] == "FILLED"


def test_persist_update_writes_multi_chain_summary_for_two_chains(tmp_path):
    import json
    import sqlite3
    from src.core.migrations import apply_migrations as _core_apply
    from src.runtime_v2.lifecycle.entry_gate import LifecycleGateWorker, UpdateChainResult, UpdateGateResult
    from src.runtime_v2.lifecycle.models import LifecycleEvent

    parser_db = str(tmp_path / "parser.sqlite3")
    ops_db = str(tmp_path / "ops.sqlite3")
    _core_apply(parser_db, "db/migrations")
    _core_apply(ops_db, "db/ops_migrations")

    pconn = sqlite3.connect(parser_db)
    pconn.execute(
        "INSERT INTO enriched_canonical_messages "
        "(enrichment_id, canonical_message_id, raw_message_id, trader_id, account_id, "
        " primary_class, enrichment_decision, policy_snapshot_json, lifecycle_processed, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,0,datetime('now'))",
        (5, 5, 5, "t1", "acc_1", "UPDATE", "PASS", "{}"),
    )
    pconn.commit()
    pconn.close()

    oconn = sqlite3.connect(ops_db)
    now = datetime.now(timezone.utc).isoformat()
    for chain_id, symbol in ((10, "BTC/USDT"), (11, "ETH/USDT")):
        oconn.execute(
            "INSERT INTO ops_trade_chains "
            "(trade_chain_id, source_enrichment_id, canonical_message_id, raw_message_id, "
            " trader_id, account_id, symbol, side, lifecycle_state, entry_mode, "
            " management_plan_json, risk_snapshot_json, plan_state_json, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (chain_id, chain_id, chain_id, chain_id, "t1", "acc_1", symbol, "LONG",
             "OPEN", "ONE_SHOT", "{}", "{}", "{}", now, now),
        )
    oconn.commit()
    oconn.close()

    def _cr(chain_id: int) -> UpdateChainResult:
        return UpdateChainResult(
            trade_chain_id=chain_id,
            new_lifecycle_state=None,
            new_be_protection_status=None,
            lifecycle_events=[LifecycleEvent(
                trade_chain_id=chain_id,
                event_type="TELEGRAM_UPDATE_ACCEPTED",
                source_type="telegram_update",
                source_id="5",
                payload_json=json.dumps({
                    "action": "MOVE_SL_TO_BE",
                    "is_breakeven": True,
                    "old_sl_price": 49000.0,
                    "new_sl_price": 50100.0,
                }),
                idempotency_key=f"be:{chain_id}:5",
            )],
            execution_commands=[],
        )

    worker = LifecycleGateWorker(
        parser_db_path=parser_db,
        ops_db_path=ops_db,
        gate=_make_gate_attached(),
        chain_repo=None,
        event_repo=None,
        command_repo=None,
        snapshot_repo=None,
        control_repo=None,
    )
    worker._persist_update(
        _make_update_enriched(canonical_message_id=5),
        UpdateGateResult(chain_results=[_cr(10), _cr(11)], review_events=[]),
    )

    conn = sqlite3.connect(ops_db)
    row = conn.execute(
        "SELECT payload_json FROM ops_notification_outbox WHERE notification_type='MULTI_CHAIN_SUMMARY'"
    ).fetchone()
    conn.close()

    assert row is not None
    payload = json.loads(row[0])
    assert {chain["chain_id"] for chain in payload["chains"]} == {10, 11}
    assert all(chain["status"] == "DONE" for chain in payload["chains"])


def test_write_multi_chain_summary_builds_autosufficient_chain_payload(tmp_path):
    import json
    import sqlite3
    from src.runtime_v2.lifecycle.entry_gate import _write_multi_chain_summary, UpdateChainResult
    from src.runtime_v2.lifecycle.models import LifecycleEvent

    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE ops_trade_chains (
            trade_chain_id INTEGER PRIMARY KEY,
            symbol TEXT,
            side TEXT
        );
        CREATE TABLE ops_clean_log_tracking (
            trade_chain_id INTEGER PRIMARY KEY,
            clean_log_root_message_id TEXT,
            clean_log_last_message_id TEXT,
            telegram_chat_id TEXT,
            telegram_thread_id TEXT,
            last_clean_log_event_type TEXT,
            last_clean_log_sent_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE ops_notification_outbox (
            notification_id INTEGER PRIMARY KEY,
            notification_type TEXT,
            destination TEXT,
            payload_json TEXT,
            priority TEXT,
            status TEXT,
            dedupe_key TEXT UNIQUE,
            attempts INTEGER,
            created_at TEXT,
            send_after TEXT,
            aggregation_group TEXT,
            source_message_id TEXT
        );
        """
    )
    conn.execute("INSERT INTO ops_trade_chains VALUES (6, 'WLD', 'LONG')")
    conn.execute("INSERT INTO ops_trade_chains VALUES (7, 'ICNT', 'LONG')")
    conn.execute(
        "INSERT INTO ops_clean_log_tracking VALUES (6, '468', '468', '-1003897279123', NULL, NULL, NULL, NULL)"
    )
    conn.execute(
        "INSERT INTO ops_clean_log_tracking VALUES (7, '469', '469', '-1003897279123', NULL, NULL, NULL, NULL)"
    )

    accepted_done = LifecycleEvent(
        event_type="TELEGRAM_UPDATE_ACCEPTED",
        source_type="telegram_update",
        source_id="365",
        payload_json=json.dumps({
            "action": "CANCEL_PENDING",
            "cancelled_entries": [{"sequence": 2, "price": "61,192.03"}],
        }),
        idempotency_key="u:6:1",
    )
    accepted_partial = LifecycleEvent(
        event_type="TELEGRAM_UPDATE_ACCEPTED",
        source_type="telegram_update",
        source_id="365",
        payload_json=json.dumps({
            "action": "MOVE_STOP",
            "old_sl_price": "66,400",
            "new_sl_price": "68,500",
            "reference": "TP_1",
        }),
        idempotency_key="u:7:1",
    )
    noop_partial = LifecycleEvent(
        event_type="NOOP_NOT_PENDING",
        source_type="telegram_update",
        source_id="365",
        payload_json=json.dumps({"reason": "no pending averaging order"}),
        idempotency_key="u:7:2",
    )

    _write_multi_chain_summary(
        conn,
        [
            UpdateChainResult(6, None, None, [accepted_done], []),
            UpdateChainResult(7, None, None, [accepted_partial, noop_partial], []),
        ],
        canonical_message_id=365,
        update_source_link="https://t.me/c/3927267771/365",
    )

    row = conn.execute(
        "SELECT payload_json FROM ops_notification_outbox WHERE notification_type='MULTI_CHAIN_SUMMARY'"
    ).fetchone()
    payload = json.loads(row[0])
    assert payload["summary_kind"] == "immediate"
    assert payload["requested_operations"] == ["Cancel pending", "Move stop"]
    assert "Entry_2: 61,192.03 -> cancelled" in payload["chains"][0]["display_lines"]
    assert payload["chains"][1]["display_lines"][0] == "Entry_2: SKIPPED - no pending averaging order"
    assert payload["link"] == "https://t.me/c/3927267771/365"


def test_write_multi_chain_summary_skips_immediate_emit_for_close_full(tmp_path):
    import json
    import sqlite3
    from src.runtime_v2.lifecycle.entry_gate import _write_multi_chain_summary, UpdateChainResult
    from src.runtime_v2.lifecycle.models import LifecycleEvent

    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE ops_trade_chains (trade_chain_id INTEGER PRIMARY KEY, symbol TEXT, side TEXT);
        CREATE TABLE ops_clean_log_tracking (
            trade_chain_id INTEGER PRIMARY KEY,
            clean_log_root_message_id TEXT,
            clean_log_last_message_id TEXT,
            telegram_chat_id TEXT,
            telegram_thread_id TEXT,
            last_clean_log_event_type TEXT,
            last_clean_log_sent_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE ops_notification_outbox (
            notification_id INTEGER PRIMARY KEY,
            notification_type TEXT,
            destination TEXT,
            payload_json TEXT,
            priority TEXT,
            status TEXT,
            dedupe_key TEXT UNIQUE,
            attempts INTEGER,
            created_at TEXT,
            send_after TEXT,
            aggregation_group TEXT,
            source_message_id TEXT
        );
        """
    )
    conn.execute("INSERT INTO ops_trade_chains VALUES (6, 'WLD', 'LONG')")
    conn.execute("INSERT INTO ops_trade_chains VALUES (7, 'ICNT', 'LONG')")
    event_6 = LifecycleEvent(
        event_type='TELEGRAM_UPDATE_ACCEPTED',
        source_type='telegram_update',
        source_id='365',
        payload_json=json.dumps({'action': 'CLOSE_FULL'}),
        idempotency_key='close:6:365',
    )
    event_7 = LifecycleEvent(
        event_type='TELEGRAM_UPDATE_ACCEPTED',
        source_type='telegram_update',
        source_id='365',
        payload_json=json.dumps({'action': 'CLOSE_FULL'}),
        idempotency_key='close:7:365',
    )

    _write_multi_chain_summary(
        conn,
        [
            UpdateChainResult(6, None, None, [event_6], []),
            UpdateChainResult(7, None, None, [event_7], []),
        ],
        canonical_message_id=365,
        update_source_link='https://t.me/c/3927267771/365',
    )

    row = conn.execute(
        "SELECT COUNT(*) FROM ops_notification_outbox WHERE notification_type='MULTI_CHAIN_SUMMARY'"
    ).fetchone()[0]
    assert row == 0


def test_market_entry_now_cancel_mode_full_roundtrip(tmp_path):
    """cancel mode: market order placed + leg2 cancelled + plan updated in result."""
    import json
    import sqlite3
    from pathlib import Path

    chain = _make_two_step_chain_for_market("cancel_subsequent")
    gate = _make_gate_attached()
    enriched = _make_market_now_update_enriched(canonical_message_id=300)
    result = gate.process_update(enriched, [chain], {1: []})

    cr = result.chain_results[0]
    # Commands: 2 cancels + 1 MARKET entry
    cmd_types = [c.command_type for c in cr.execution_commands]
    assert cmd_types.count("CANCEL_PENDING_ENTRY") == 2
    assert cmd_types.count("PLACE_ENTRY_WITH_ATTACHED_TPSL") == 1
    # Plan in result
    plan = json.loads(cr.new_plan_state_json)
    by_seq = {l["sequence"]: l for l in plan["legs"]}
    assert by_seq[1]["entry_type"] == "MARKET"
    assert by_seq[2]["status"] == "CANCELLED"
    # Event
    assert any(e.event_type == "TELEGRAM_UPDATE_ACCEPTED" for e in cr.lifecycle_events)


def test_apply_cancel_pending_includes_cancelled_entries():
    """_apply_cancel_pending deve includere cancelled_entries nel payload."""
    import json
    from src.runtime_v2.lifecycle.models import TradeChain
    from src.runtime_v2.lifecycle.entry_gate import LifecycleEntryGate
    from src.runtime_v2.lifecycle.ports import ExchangeDataPort
    from src.runtime_v2.lifecycle.risk_capacity import RiskCapacityEngine
    from src.runtime_v2.signal_enrichment.models import EnrichedCanonicalMessage
    from unittest.mock import MagicMock

    plan = json.dumps({"legs": [
        {"leg_id": "1", "sequence": 2, "status": "PENDING", "entry_type": "LIMIT", "price": 92500.0},
        {"leg_id": "2", "sequence": 3, "status": "PENDING", "entry_type": "LIMIT", "price": 91000.0},
        {"leg_id": "3", "sequence": 1, "status": "FILLED",  "entry_type": "LIMIT", "price": 93000.0},
    ]})
    chain = TradeChain(
        trade_chain_id=88, source_enrichment_id=88, canonical_message_id=88,
        raw_message_id=88, trader_id="t", account_id="a",
        symbol="BTC/USDT", side="LONG", lifecycle_state="OPEN",
        entry_mode="TWO_STEP",
        management_plan_json="{}", risk_snapshot_json="{}",
        plan_state_json=plan,
        entry_avg_price=93000.0, open_position_qty=0.01,
    )
    enriched = EnrichedCanonicalMessage(
        enrichment_id=2, canonical_message_id=2, raw_message_id=88,
        trader_id="t", account_id="a", primary_class="UPDATE",
        enrichment_decision="PASS", policy_snapshot={}, policy_version="",
    )
    gate = LifecycleEntryGate(
        risk_engine=MagicMock(spec=RiskCapacityEngine),
        exchange_port=MagicMock(spec=ExchangeDataPort),
    )

    result = gate._apply_cancel_pending(enriched, chain)

    accepted = [e for e in result.lifecycle_events if e.event_type == "TELEGRAM_UPDATE_ACCEPTED"]
    assert len(accepted) == 1
    p = json.loads(accepted[0].payload_json)
    assert p["action"] == "CANCEL_PENDING"
    entries = p.get("cancelled_entries", [])
    assert len(entries) == 2, f"Expected 2 pending entries, got {len(entries)}: {entries}"
    sequences = {e["sequence"] for e in entries}
    assert sequences == {2, 3}


def test_apply_close_partial_includes_close_pct():
    """_apply_close_partial deve includere close_pct nel payload."""
    import json
    from src.runtime_v2.lifecycle.models import TradeChain
    from src.runtime_v2.lifecycle.entry_gate import LifecycleEntryGate
    from src.runtime_v2.lifecycle.ports import ExchangeDataPort
    from src.runtime_v2.lifecycle.risk_capacity import RiskCapacityEngine
    from src.runtime_v2.signal_enrichment.models import EnrichedCanonicalMessage
    from unittest.mock import MagicMock

    chain = TradeChain(
        trade_chain_id=77, source_enrichment_id=77, canonical_message_id=77,
        raw_message_id=77, trader_id="t", account_id="a",
        symbol="ETH/USDT", side="LONG", lifecycle_state="OPEN",
        entry_mode="ONE_SHOT",
        management_plan_json="{}", risk_snapshot_json="{}",
        plan_state_json="{}",
        entry_avg_price=3000.0, open_position_qty=1.0,
    )
    enriched = EnrichedCanonicalMessage(
        enrichment_id=3, canonical_message_id=3, raw_message_id=77,
        trader_id="t", account_id="a", primary_class="UPDATE",
        enrichment_decision="PASS", policy_snapshot={}, policy_version="",
    )

    # Build a minimal close op
    from dataclasses import dataclass
    @dataclass
    class FakeCloseOp:
        close_scope: str = "PARTIAL"
        fraction: float = 0.5

    gate = LifecycleEntryGate(
        risk_engine=MagicMock(spec=RiskCapacityEngine),
        exchange_port=MagicMock(spec=ExchangeDataPort),
    )

    result = gate._apply_close_partial(enriched, chain, op=FakeCloseOp(fraction=0.5))

    accepted = [e for e in result.lifecycle_events if e.event_type == "TELEGRAM_UPDATE_ACCEPTED"]
    assert len(accepted) == 1
    p = json.loads(accepted[0].payload_json)
    assert p["action"] == "CLOSE_PARTIAL"
    assert p.get("close_pct") == 50.0, f"Expected 50.0, got {p.get('close_pct')}"
    assert p.get("fraction") == 0.5


# ── Symbol existence check ────────────────────────────────────────────────────

def test_unknown_symbol_rejects_signal_before_chain_creation():
    """SIGNAL_REJECTED con reason=unknown_symbol quando il simbolo non è nella whitelist."""
    from src.runtime_v2.lifecycle.entry_gate import LifecycleEntryGate
    from src.runtime_v2.lifecycle.risk_capacity import RiskCapacityEngine
    from src.runtime_v2.lifecycle.static_exchange_data_port import StaticExchangeDataPort

    gate = LifecycleEntryGate(
        risk_engine=RiskCapacityEngine(),
        exchange_port=StaticExchangeDataPort(known_symbols=frozenset({"BTC/USDT", "ETH/USDT"})),
    )
    enriched = _make_enriched_signal(symbol="INCTUSDT")
    result = gate.process_signal(enriched, [], "NONE")

    assert result.trade_chain is None
    assert result.execution_commands == []
    assert result.review_reason == "unknown_symbol"
    event_types = [e.event_type for e in result.lifecycle_events]
    assert "SIGNAL_REJECTED" in event_types
    assert "SIGNAL_ACCEPTED" not in event_types
    assert "TRADE_CHAIN_CREATED" not in event_types


def test_unknown_symbol_check_is_universal_also_for_limit_orders():
    """Il check simbolo si applica sia a MARKET che a LIMIT — universale."""
    from src.runtime_v2.lifecycle.entry_gate import LifecycleEntryGate
    from src.runtime_v2.lifecycle.risk_capacity import RiskCapacityEngine
    from src.runtime_v2.lifecycle.static_exchange_data_port import StaticExchangeDataPort

    gate = LifecycleEntryGate(
        risk_engine=RiskCapacityEngine(),
        exchange_port=StaticExchangeDataPort(known_symbols=frozenset({"BTC/USDT"})),
    )
    enriched = _make_enriched_signal(symbol="FAKEUSDT", entry_type="LIMIT", entry_price=1.0)
    result = gate.process_signal(enriched, [], "NONE")

    assert result.trade_chain is None
    assert result.review_reason == "unknown_symbol"


def test_known_symbol_passes_check():
    """Simbolo presente nella whitelist: segnale accettato normalmente."""
    from src.runtime_v2.lifecycle.entry_gate import LifecycleEntryGate
    from src.runtime_v2.lifecycle.risk_capacity import RiskCapacityEngine
    from src.runtime_v2.lifecycle.static_exchange_data_port import StaticExchangeDataPort

    gate = LifecycleEntryGate(
        risk_engine=RiskCapacityEngine(),
        exchange_port=StaticExchangeDataPort(known_symbols=frozenset({"BTC/USDT"})),
    )
    enriched = _make_enriched_signal(symbol="BTC/USDT")
    result = gate.process_signal(enriched, [], "NONE")

    assert result.trade_chain is not None
    assert result.trade_chain.symbol == "BTC/USDT"


def test_no_known_symbols_list_is_fail_open():
    """known_symbols=None (nessun dato dall'exchange) → non blocca i segnali."""
    from src.runtime_v2.lifecycle.entry_gate import LifecycleEntryGate
    from src.runtime_v2.lifecycle.risk_capacity import RiskCapacityEngine
    from src.runtime_v2.lifecycle.static_exchange_data_port import StaticExchangeDataPort

    gate = LifecycleEntryGate(
        risk_engine=RiskCapacityEngine(),
        exchange_port=StaticExchangeDataPort(known_symbols=None),
    )
    enriched = _make_enriched_signal(symbol="QUALSIASI/USDT")
    result = gate.process_signal(enriched, [], "NONE")

    # Non viene rifiutato per unknown_symbol — potrebbe fallire per altri motivi (SL, ecc.)
    assert result.review_reason != "unknown_symbol"


def test_release_close_full_summary_uses_position_closed_links(tmp_path):
    import json
    import sqlite3
    from src.runtime_v2.control_plane.outbox_writer import try_release_pending_close_full_summaries

    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE ops_pending_multi_chain_summaries (
            pending_id INTEGER PRIMARY KEY,
            canonical_message_id INTEGER,
            payload_json TEXT
        );
        CREATE TABLE ops_clean_log_tracking (
            trade_chain_id INTEGER PRIMARY KEY,
            clean_log_root_message_id TEXT,
            clean_log_last_message_id TEXT,
            telegram_chat_id TEXT,
            telegram_thread_id TEXT,
            last_clean_log_event_type TEXT,
            last_clean_log_sent_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE ops_notification_outbox (
            notification_id INTEGER PRIMARY KEY,
            notification_type TEXT,
            destination TEXT,
            payload_json TEXT,
            priority TEXT,
            status TEXT,
            dedupe_key TEXT UNIQUE,
            attempts INTEGER,
            created_at TEXT,
            send_after TEXT,
            aggregation_group TEXT,
            source_message_id TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO ops_pending_multi_chain_summaries (canonical_message_id, payload_json) VALUES (?, ?)",
        (
            365,
            json.dumps({
                "summary_kind": "pending_final_close_links",
                "requested_operations": ["Close full"],
                "chains": [
                    {"chain_id": 6, "symbol": "WLD", "side": "LONG", "status": "DONE", "link_mode": "final_close", "link": None, "display_lines": []},
                    {"chain_id": 7, "symbol": "ICNT", "side": "LONG", "status": "DONE", "link_mode": "final_close", "link": None, "display_lines": []},
                ],
                "counts": {"done": 2, "partial": 0, "skipped": 0, "error": 0},
                "source": "trader_update",
                "link": "https://t.me/c/3927267771/365",
            }),
        ),
    )
    conn.execute("INSERT INTO ops_clean_log_tracking VALUES (6, '453', '468', '-1003897279123', NULL, 'POSITION_CLOSED', NULL, NULL)")
    conn.execute("INSERT INTO ops_clean_log_tracking VALUES (7, '454', '469', '-1003897279123', NULL, 'POSITION_CLOSED', NULL, NULL)")

    try_release_pending_close_full_summaries(conn)

    row = conn.execute(
        "SELECT payload_json FROM ops_notification_outbox WHERE notification_type='MULTI_CHAIN_SUMMARY'"
    ).fetchone()
    payload = json.loads(row[0])
    assert payload["summary_kind"] == "final_close"
    assert payload["chains"][0]["link"] == "https://t.me/c/3897279123/468"
    assert payload["chains"][1]["link"] == "https://t.me/c/3897279123/469"


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
