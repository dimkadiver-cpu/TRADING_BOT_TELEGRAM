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
        EnrichedEntryLeg, EnrichedSignalPayload, EntrySplitConfig,
        EntryWeightsConfig, EntryRangeConfig,
        LimitEntrySplitConfig, ManagementPlanConfig, MarketEntrySplitConfig,
        MarketExecutionConfig, PriceCorrectionsConfig, PriceSanityConfig,
        RiskConfig, SignalPolicyConfig, SlConfig, TpConfig,
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


def _make_chain_simple(state="OPEN"):
    from src.runtime_v2.lifecycle.models import TradeChain
    return TradeChain(
        trade_chain_id=10, source_enrichment_id=1, canonical_message_id=2,
        raw_message_id=3, trader_id="t1", account_id="acc1",
        symbol="BTC/USDT", side="LONG", lifecycle_state=state,
        entry_mode="ONE_SHOT",
        management_plan_json='{"be_trigger": null, "be_buffer_pct": 0.0}',
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
        risk_snapshot={"entry_price": entry_price, "size_usdt": size_usdt},
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


def _make_gate_with_mode(execution_mode: str):
    from src.runtime_v2.lifecycle.entry_gate import LifecycleEntryGate
    from src.runtime_v2.lifecycle.risk_capacity import RiskCapacityEngine
    from src.runtime_v2.lifecycle.static_exchange_data_port import StaticExchangeDataPort

    gate = LifecycleEntryGate(
        risk_engine=RiskCapacityEngine(),
        exchange_port=StaticExchangeDataPort(),
        execution_mode=execution_mode,
    )
    gate._risk.validate = lambda *a, **kw: _make_risk_decision()
    return gate


def test_mode_a_sl_and_tp_are_waiting_position():
    gate = _make_gate_with_mode("a_sequential")
    enriched = _make_enriched_signal_for_mode(tp_count=2)
    result = gate.process_signal(enriched, [], "NONE")
    assert result.review_reason is None
    entry_cmd = next(c for c in result.execution_commands if c.command_type == "PLACE_ENTRY")
    sl_cmd = next(c for c in result.execution_commands if c.command_type == "PLACE_PROTECTIVE_STOP")
    tp_cmds = [c for c in result.execution_commands if c.command_type == "PLACE_TAKE_PROFIT"]
    assert entry_cmd.status == "PENDING"
    assert sl_cmd.status == "WAITING_POSITION"
    assert all(c.status == "WAITING_POSITION" for c in tp_cmds)


def test_mode_b_sl_pending_tp_waiting_position():
    gate = _make_gate_with_mode("b_entry_stop_then_tp")
    enriched = _make_enriched_signal_for_mode(tp_count=2)
    result = gate.process_signal(enriched, [], "NONE")
    assert result.review_reason is None
    entry_cmd = next(c for c in result.execution_commands if c.command_type == "PLACE_ENTRY")
    sl_cmd = next(c for c in result.execution_commands if c.command_type == "PLACE_PROTECTIVE_STOP")
    tp_cmds = [c for c in result.execution_commands if c.command_type == "PLACE_TAKE_PROFIT"]
    assert entry_cmd.status == "PENDING"
    assert sl_cmd.status == "PENDING"
    assert all(c.status == "WAITING_POSITION" for c in tp_cmds)


def test_mode_c_entry_has_native_tpsl_no_sl_command():
    gate = _make_gate_with_mode("c_native_attached_tpsl")
    enriched = _make_enriched_signal_for_mode(tp_count=2)
    result = gate.process_signal(enriched, [], "NONE")
    assert result.review_reason is None
    cmd_types = [c.command_type for c in result.execution_commands]
    assert "PLACE_PROTECTIVE_STOP" not in cmd_types
    entry_cmd = next(c for c in result.execution_commands if c.command_type == "PLACE_ENTRY")
    entry_payload = json.loads(entry_cmd.payload_json)
    assert entry_payload["native_attached_tpsl"] is True
    assert "attached_stop_loss" in entry_payload
    assert "attached_take_profit" in entry_payload
    # With 2 TPs: last is attached, first is WAITING_POSITION
    tp_cmds = [c for c in result.execution_commands if c.command_type == "PLACE_TAKE_PROFIT"]
    assert len(tp_cmds) == 1
    assert tp_cmds[0].status == "WAITING_POSITION"


def test_mode_c_single_tp_no_intermediate_commands():
    gate = _make_gate_with_mode("c_native_attached_tpsl")
    enriched = _make_enriched_signal_for_mode(tp_count=1)
    result = gate.process_signal(enriched, [], "NONE")
    assert result.review_reason is None
    cmd_types = [c.command_type for c in result.execution_commands]
    assert "PLACE_TAKE_PROFIT" not in cmd_types
    assert "PLACE_PROTECTIVE_STOP" not in cmd_types


def test_mode_c_multi_tp_entry_payload_includes_builder_fields_and_final_tp_slice_qty():
    gate = _make_gate_with_mode("c_native_attached_tpsl")
    enriched = _make_enriched_signal_for_mode(
        tp_count=2,
        close_distribution_table={2: [60, 40]},
    )
    result = gate.process_signal(enriched, [], "NONE")

    entry_cmd = next(c for c in result.execution_commands if c.command_type == "PLACE_ENTRY")
    payload = json.loads(entry_cmd.payload_json)

    assert payload["attached_take_profit"] == 52000.0
    assert payload["attached_stop_loss"] == 49000.0
    assert payload["tp_count"] == 2
    assert payload["qty"] == pytest.approx(0.01)
    assert payload["attached_take_profit_qty"] == pytest.approx(0.004)


def test_mode_c_single_tp_entry_payload_uses_full_leg_qty_for_attached_tp():
    gate = _make_gate_with_mode("c_native_attached_tpsl")
    enriched = _make_enriched_signal_for_mode(tp_count=1)
    result = gate.process_signal(enriched, [], "NONE")

    entry_cmd = next(c for c in result.execution_commands if c.command_type == "PLACE_ENTRY")
    payload = json.loads(entry_cmd.payload_json)

    assert payload["tp_count"] == 1
    assert payload["qty"] == pytest.approx(0.01)
    assert payload["attached_take_profit_qty"] == pytest.approx(0.01)


def test_update_move_to_be_payload_uses_target_price_and_buffer_pct():
    gate = _make_gate()
    enriched = _make_update_enriched(scope_hint="SINGLE_SIGNAL", symbols=["BTC/USDT"])
    chain = _make_open_chain(entry_avg_price=50000.0, current_stop_price=49000.0)
    chain = chain.model_copy(update={"management_plan_json": '{"be_trigger": null, "be_buffer_pct": 0.01}'})

    result = gate.process_update(enriched, [chain], {})

    cr = result.chain_results[0]
    command = next(c for c in cr.execution_commands if c.command_type == "MOVE_STOP_TO_BREAKEVEN")
    payload = json.loads(command.payload_json)
    assert payload["target_price"] == 50000.0
    assert payload["be_buffer_pct"] == 0.01


def test_process_signal_writes_execution_mode_to_chain():
    gate = _make_gate_with_mode("b_entry_stop_then_tp")
    enriched = _make_enriched_signal_for_mode()
    result = gate.process_signal(enriched, [], "NONE")
    assert result.trade_chain is not None
    assert result.trade_chain.execution_mode == "b_entry_stop_then_tp"


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
        management_plan_json='{"be_trigger": null, "be_buffer_pct": 0.0}',
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


def test_cancel_pending_on_waiting_entry_becomes_cancelled():
    gate = _make_gate_default()
    chain = _make_chain("WAITING_ENTRY")
    enriched = _make_enriched_cancel()
    result = gate.process_update(enriched, [chain], {10: []})
    assert len(result.chain_results) == 1
    cr = result.chain_results[0]
    assert cr.new_lifecycle_state == "CANCELLED"
    cmd_types = [c.command_type for c in cr.execution_commands]
    assert "CANCEL_PENDING_ENTRY" in cmd_types
    assert "SYNC_PROTECTIVE_ORDERS" not in cmd_types


def test_cancel_pending_on_open_emits_sync_not_cancelled():
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
    assert "SYNC_PROTECTIVE_ORDERS" in cmd_types


def test_cancel_pending_on_partially_closed_emits_sync():
    gate = _make_gate_default()
    chain = _make_chain("PARTIALLY_CLOSED")
    chain = chain.model_copy(update={"open_position_qty": 0.005})
    enriched = _make_enriched_cancel()
    result = gate.process_update(enriched, [chain], {10: []})
    cr = result.chain_results[0]
    assert cr.new_lifecycle_state is None
    cmd_types = [c.command_type for c in cr.execution_commands]
    assert "SYNC_PROTECTIVE_ORDERS" in cmd_types


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


def test_resolve_targets_matches_via_telegram_message_id():
    """When two chains are open, Telegram ID resolves to the correct one."""
    chain_xrp = _make_chain_with_raw_id(1, "trader_a", "XRPUSDT", "SHORT", raw_message_id=10)
    chain_ada = _make_chain_with_raw_id(2, "trader_a", "ADAUSDT", "SHORT", raw_message_id=20)

    enriched = _make_enriched_update_tg("trader_a", telegram_message_ids=[10])
    tg_id_to_raw_id = {10: 10, 20: 20}

    gate = _make_gate_with_mode("b_entry_stop_then_tp")
    tag = enriched.enriched_actions[0]
    result = gate._resolve_targets(
        enriched, [chain_xrp, chain_ada], tag,
        tg_id_to_raw_id=tg_id_to_raw_id,
    )

    assert result == [chain_xrp]


def test_resolve_targets_telegram_id_no_match_falls_through_to_ambiguous():
    """If Telegram IDs don't match any chain, falls back to ambiguous."""
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

    assert result is None  # ambiguous — two chains, no Telegram match


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
