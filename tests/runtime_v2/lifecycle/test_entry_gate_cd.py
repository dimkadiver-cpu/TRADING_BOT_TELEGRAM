from __future__ import annotations

import json
import pytest

from datetime import datetime, timezone

from src.runtime_v2.lifecycle.entry_gate import LifecycleEntryGate
from src.runtime_v2.lifecycle.risk_capacity import RiskCapacityEngine
from src.runtime_v2.lifecycle.ports import AccountStateSnapshot, SymbolMarketSnapshot
from src.runtime_v2.signal_enrichment.models import (
    AccountConfig, CloseDistributionConfig, EffectiveEnrichmentConfig,
    EnrichedCanonicalMessage, EnrichedEntryLeg, EnrichedSignalPayload,
    EntryRangeConfig, EntrySplitConfig, EntryWeightsConfig, LimitEntrySplitConfig,
    ManagementPlanConfig, MarketEntrySplitConfig, MarketExecutionConfig,
    PriceCorrectionsConfig, PriceSanityConfig, RiskConfig, SignalPolicyConfig,
    SlConfig, TpConfig,
)
from src.parser_v2.contracts.entities import Price, StopLoss, TakeProfit


def _make_port():
    from src.runtime_v2.lifecycle.ports import ExchangeDataPort
    class FakePort(ExchangeDataPort):
        def get_account_state(self, account_id):
            return AccountStateSnapshot(
                account_id=account_id, equity_usdt=1000.0, available_balance_usdt=900.0,
                total_open_risk_usdt=0.0, total_margin_used_usdt=0.0,
                source="test", captured_at=datetime.now(timezone.utc),
            )
        def get_symbol_market_state(self, account_id, symbol):
            return SymbolMarketSnapshot(
                symbol=symbol, mark_price=65000.0, bid=64990.0, ask=65010.0,
                min_order_size=0.001, price_precision=None, qty_precision=None,
                source="test", captured_at=datetime.now(timezone.utc),
            )
        def get_open_orders(self, account_id, symbol=None):
            return []
        def get_open_position(self, account_id, symbol, side):
            return None
    return FakePort()


def _make_enriched_signal(tp_count: int = 1, entry_count: int = 1,
                           leverage: int = 5, hedge_mode: bool = False):
    entries = [
        EnrichedEntryLeg(
            sequence=i + 1, entry_type="LIMIT",
            price=Price(raw=str(65000 - i * 100), value=65000.0 - i * 100),
            weight=1.0 / entry_count,
        )
        for i in range(entry_count)
    ]
    take_profits = [
        TakeProfit(price=Price(raw=str(70000 + i * 500), value=70000.0 + i * 500), sequence=i + 1)
        for i in range(tp_count)
    ]
    signal = EnrichedSignalPayload(
        symbol="BTC/USDT:USDT", side="LONG", entry_structure="ONE_SHOT",
        entries=entries, take_profits=take_profits,
        stop_loss=StopLoss(price=Price(raw="63000", value=63000.0)),
    )
    w = EntryWeightsConfig(weights={"E1": 1.0})
    r = EntryRangeConfig(weights={"E1": 0.5, "E2": 0.5})
    risk = RiskConfig(leverage=leverage, capital_base_usdt=1000.0, risk_pct_of_capital=1.0)
    account = AccountConfig(id="main", capital_base_usdt=1000.0, max_leverage=10,
                            max_capital_at_risk_pct=10.0, hard_max_per_signal_risk_pct=2.0)
    signal_policy = SignalPolicyConfig(
        accepted_entry_structures=["ONE_SHOT", "TWO_STEP", "RANGE", "LADDER"],
        market_execution=MarketExecutionConfig(),
        entry_split=EntrySplitConfig(
            LIMIT=LimitEntrySplitConfig(single=w, range=r, averaging=w, ladder=w),
            MARKET=MarketEntrySplitConfig(single=w, averaging=w),
        ),
        tp=TpConfig(), sl=SlConfig(),
        price_corrections=PriceCorrectionsConfig(),
        price_sanity=PriceSanityConfig(),
    )
    cfg = EffectiveEnrichmentConfig(
        trader_id="t1", enabled=True, gate_mode="block", hedge_mode=hedge_mode,
        account_id="main", signal_policy=signal_policy, update_admission={},
        management_plan=ManagementPlanConfig(), risk=risk, account=account,
    )
    return EnrichedCanonicalMessage(
        enrichment_id=1, canonical_message_id=10, raw_message_id=5,
        trader_id="t1", account_id="main", primary_class="SIGNAL",
        enrichment_decision="PASS", enriched_signal=signal, enriched_actions=None,
        management_plan=ManagementPlanConfig(), policy_snapshot=cfg.model_dump(),
    )


def _make_port_no_mark_price():
    from src.runtime_v2.lifecycle.ports import ExchangeDataPort
    class FakePortNoMark(ExchangeDataPort):
        def get_account_state(self, account_id):
            return AccountStateSnapshot(
                account_id=account_id, equity_usdt=1000.0, available_balance_usdt=900.0,
                total_open_risk_usdt=0.0, total_margin_used_usdt=0.0,
                source="test", captured_at=datetime.now(timezone.utc),
            )
        def get_symbol_market_state(self, account_id, symbol):
            return SymbolMarketSnapshot(
                symbol=symbol, mark_price=None,
                source="test", captured_at=datetime.now(timezone.utc),
            )
        def get_open_orders(self, account_id, symbol=None):
            return []
        def get_open_position(self, account_id, symbol, side):
            return None
    return FakePortNoMark()


def _make_gate(simple_attached_enabled: bool = True) -> LifecycleEntryGate:
    return LifecycleEntryGate(
        risk_engine=RiskCapacityEngine(),
        exchange_port=_make_port(),
        simple_attached_enabled=simple_attached_enabled,
    )


def _make_gate_no_mark(simple_attached_enabled: bool = True) -> LifecycleEntryGate:
    return LifecycleEntryGate(
        risk_engine=RiskCapacityEngine(),
        exchange_port=_make_port_no_mark_price(),
        simple_attached_enabled=simple_attached_enabled,
    )


def _make_enriched_market_c(sl_price: float = 0.45, tp_price: float = 0.60):
    """1 entry MARKET, 1 TP — routes to C mode when simple_attached_enabled."""
    entries = [EnrichedEntryLeg(sequence=1, entry_type="MARKET", price=None, weight=1.0)]
    take_profits = [TakeProfit(price=Price(raw=str(tp_price), value=tp_price), sequence=1)]
    signal = EnrichedSignalPayload(
        symbol="TOKEN/USDT:USDT", side="LONG", entry_structure="ONE_SHOT",
        entries=entries, take_profits=take_profits,
        stop_loss=StopLoss(price=Price(raw=str(sl_price), value=sl_price)),
    )
    w = EntryWeightsConfig(weights={"E1": 1.0})
    r = EntryRangeConfig(weights={"E1": 0.5, "E2": 0.5})
    risk = RiskConfig(leverage=1, capital_base_usdt=1000.0, risk_pct_of_capital=1.0)
    account = AccountConfig(id="main", capital_base_usdt=1000.0, max_leverage=10,
                            max_capital_at_risk_pct=10.0, hard_max_per_signal_risk_pct=2.0)
    signal_policy = SignalPolicyConfig(
        accepted_entry_structures=["ONE_SHOT", "TWO_STEP", "RANGE", "LADDER"],
        market_execution=MarketExecutionConfig(),
        entry_split=EntrySplitConfig(
            LIMIT=LimitEntrySplitConfig(single=w, range=r, averaging=w, ladder=w),
            MARKET=MarketEntrySplitConfig(single=w, averaging=w),
        ),
        tp=TpConfig(), sl=SlConfig(),
        price_corrections=PriceCorrectionsConfig(),
        price_sanity=PriceSanityConfig(),
    )
    cfg = EffectiveEnrichmentConfig(
        trader_id="t1", enabled=True, gate_mode="block", hedge_mode=False,
        account_id="main", signal_policy=signal_policy, update_admission={},
        management_plan=ManagementPlanConfig(), risk=risk, account=account,
    )
    return EnrichedCanonicalMessage(
        enrichment_id=1, canonical_message_id=10, raw_message_id=5,
        trader_id="t1", account_id="main", primary_class="SIGNAL",
        enrichment_decision="PASS", enriched_signal=signal, enriched_actions=None,
        management_plan=ManagementPlanConfig(), policy_snapshot=cfg.model_dump(),
    )


def _make_enriched_market_d_multi_tp(sl_price: float = 0.45):
    """1 entry MARKET, 2 TP — routes to D mode (multi-TP)."""
    entries = [EnrichedEntryLeg(sequence=1, entry_type="MARKET", price=None, weight=1.0)]
    take_profits = [
        TakeProfit(price=Price(raw="0.55", value=0.55), sequence=1),
        TakeProfit(price=Price(raw="0.65", value=0.65), sequence=2),
    ]
    signal = EnrichedSignalPayload(
        symbol="TOKEN/USDT:USDT", side="LONG", entry_structure="ONE_SHOT",
        entries=entries, take_profits=take_profits,
        stop_loss=StopLoss(price=Price(raw=str(sl_price), value=sl_price)),
    )
    w = EntryWeightsConfig(weights={"E1": 1.0})
    r = EntryRangeConfig(weights={"E1": 0.5, "E2": 0.5})
    risk = RiskConfig(leverage=1, capital_base_usdt=1000.0, risk_pct_of_capital=1.0)
    account = AccountConfig(id="main", capital_base_usdt=1000.0, max_leverage=10,
                            max_capital_at_risk_pct=10.0, hard_max_per_signal_risk_pct=2.0)
    signal_policy = SignalPolicyConfig(
        accepted_entry_structures=["ONE_SHOT", "TWO_STEP", "RANGE", "LADDER"],
        market_execution=MarketExecutionConfig(),
        entry_split=EntrySplitConfig(
            LIMIT=LimitEntrySplitConfig(single=w, range=r, averaging=w, ladder=w),
            MARKET=MarketEntrySplitConfig(single=w, averaging=w),
        ),
        tp=TpConfig(), sl=SlConfig(),
        price_corrections=PriceCorrectionsConfig(),
        price_sanity=PriceSanityConfig(),
    )
    cfg = EffectiveEnrichmentConfig(
        trader_id="t1", enabled=True, gate_mode="block", hedge_mode=False,
        account_id="main", signal_policy=signal_policy, update_admission={},
        management_plan=ManagementPlanConfig(), risk=risk, account=account,
    )
    return EnrichedCanonicalMessage(
        enrichment_id=2, canonical_message_id=11, raw_message_id=6,
        trader_id="t1", account_id="main", primary_class="SIGNAL",
        enrichment_decision="PASS", enriched_signal=signal, enriched_actions=None,
        management_plan=ManagementPlanConfig(), policy_snapshot=cfg.model_dump(),
    )


def _make_enriched_mixed_legs(sl_price: float = 0.45, limit_price: float = 0.48):
    """2 entries: MARKET seq=1 weight=0.7, LIMIT seq=2 weight=0.3, 1 TP — routes to D mode."""
    entries = [
        EnrichedEntryLeg(sequence=1, entry_type="MARKET", price=None, weight=0.7),
        EnrichedEntryLeg(
            sequence=2, entry_type="LIMIT",
            price=Price(raw=str(limit_price), value=limit_price),
            weight=0.3,
        ),
    ]
    take_profits = [TakeProfit(price=Price(raw="0.60", value=0.60), sequence=1)]
    signal = EnrichedSignalPayload(
        symbol="TOKEN/USDT:USDT", side="LONG", entry_structure="TWO_STEP",
        entries=entries, take_profits=take_profits,
        stop_loss=StopLoss(price=Price(raw=str(sl_price), value=sl_price)),
    )
    w = EntryWeightsConfig(weights={"E1": 1.0})
    r = EntryRangeConfig(weights={"E1": 0.5, "E2": 0.5})
    risk = RiskConfig(leverage=1, capital_base_usdt=1000.0, risk_pct_of_capital=1.0)
    account = AccountConfig(id="main", capital_base_usdt=1000.0, max_leverage=10,
                            max_capital_at_risk_pct=10.0, hard_max_per_signal_risk_pct=2.0)
    signal_policy = SignalPolicyConfig(
        accepted_entry_structures=["ONE_SHOT", "TWO_STEP", "RANGE", "LADDER"],
        market_execution=MarketExecutionConfig(),
        entry_split=EntrySplitConfig(
            LIMIT=LimitEntrySplitConfig(single=w, range=r, averaging=w, ladder=w),
            MARKET=MarketEntrySplitConfig(single=w, averaging=w),
        ),
        tp=TpConfig(), sl=SlConfig(),
        price_corrections=PriceCorrectionsConfig(),
        price_sanity=PriceSanityConfig(),
    )
    cfg = EffectiveEnrichmentConfig(
        trader_id="t1", enabled=True, gate_mode="block", hedge_mode=False,
        account_id="main", signal_policy=signal_policy, update_admission={},
        management_plan=ManagementPlanConfig(), risk=risk, account=account,
    )
    return EnrichedCanonicalMessage(
        enrichment_id=3, canonical_message_id=12, raw_message_id=7,
        trader_id="t1", account_id="main", primary_class="SIGNAL",
        enrichment_decision="PASS", enriched_signal=signal, enriched_actions=None,
        management_plan=ManagementPlanConfig(), policy_snapshot=cfg.model_dump(),
    )


# ── C mode tests ──────────────────────────────────────────────────────────────

def test_c_mode_single_entry_single_tp():
    gate = _make_gate(simple_attached_enabled=True)
    enriched = _make_enriched_signal(tp_count=1, entry_count=1)
    result = gate.process_signal(enriched, [], "NONE")
    cmd_types = [c.command_type for c in result.execution_commands]
    assert "PLACE_ENTRY_WITH_ATTACHED_TPSL" in cmd_types
    assert "PLACE_ENTRY" not in cmd_types
    assert "SET_POSITION_TPSL_FULL" not in cmd_types


def test_c_mode_payload_has_leverage_and_position_idx():
    gate = _make_gate(simple_attached_enabled=True)
    enriched = _make_enriched_signal(tp_count=1, entry_count=1, leverage=5, hedge_mode=False)
    result = gate.process_signal(enriched, [], "NONE")
    attached_cmd = next(c for c in result.execution_commands
                        if c.command_type == "PLACE_ENTRY_WITH_ATTACHED_TPSL")
    payload = json.loads(attached_cmd.payload_json)
    assert payload["leverage"] == 5
    assert payload["hedge_mode"] is False
    assert payload["position_idx"] == 0
    assert "attached_tpsl" in payload
    assert payload["attached_tpsl"]["take_profit"] == 70000.0
    assert payload["attached_tpsl"]["stop_loss"] == 63000.0


def test_c_mode_disabled_uses_d():
    gate = _make_gate(simple_attached_enabled=False)
    enriched = _make_enriched_signal(tp_count=1, entry_count=1)
    result = gate.process_signal(enriched, [], "NONE")
    cmd_types = [c.command_type for c in result.execution_commands]
    assert "PLACE_ENTRY" in cmd_types
    assert "SET_POSITION_TPSL_FULL" in cmd_types
    assert "PLACE_ENTRY_WITH_ATTACHED_TPSL" not in cmd_types


def test_c_mode_sets_execution_mode_on_chain():
    gate = _make_gate(simple_attached_enabled=True)
    enriched = _make_enriched_signal(tp_count=1, entry_count=1)
    result = gate.process_signal(enriched, [], "NONE")
    assert result.trade_chain.execution_mode == "C_SIMPLE_ATTACHED"


# ── D Full tests ──────────────────────────────────────────────────────────────

def test_d_full_single_tp_status_waiting_position():
    gate = _make_gate(simple_attached_enabled=False)
    enriched = _make_enriched_signal(tp_count=1, entry_count=1)
    result = gate.process_signal(enriched, [], "NONE")
    tpsl_cmd = next(c for c in result.execution_commands
                    if c.command_type == "SET_POSITION_TPSL_FULL")
    assert tpsl_cmd.status == "WAITING_POSITION"


def test_d_full_payload_has_leverage():
    gate = _make_gate(simple_attached_enabled=False)
    enriched = _make_enriched_signal(tp_count=1, entry_count=1, leverage=5)
    result = gate.process_signal(enriched, [], "NONE")
    tpsl_cmd = next(c for c in result.execution_commands
                    if c.command_type == "SET_POSITION_TPSL_FULL")
    payload = json.loads(tpsl_cmd.payload_json)
    assert payload["leverage"] == 5
    assert payload["position_idx"] == 0


def test_d_multi_tp_generates_partial_commands():
    gate = _make_gate(simple_attached_enabled=False)
    enriched = _make_enriched_signal(tp_count=3, entry_count=1)
    result = gate.process_signal(enriched, [], "NONE")
    cmd_types = [c.command_type for c in result.execution_commands]
    partial_cmds = [c for c in result.execution_commands
                    if c.command_type == "SET_POSITION_TPSL_PARTIAL"]
    assert len(partial_cmds) == 3
    assert "SET_POSITION_TPSL_FULL" not in cmd_types
    for cmd in partial_cmds:
        assert cmd.status == "WAITING_POSITION"


def test_d_multi_tp_partial_tp_size_equals_sl_size():
    gate = _make_gate(simple_attached_enabled=False)
    enriched = _make_enriched_signal(tp_count=2, entry_count=1)
    result = gate.process_signal(enriched, [], "NONE")
    partial_cmds = sorted(
        [c for c in result.execution_commands if c.command_type == "SET_POSITION_TPSL_PARTIAL"],
        key=lambda c: json.loads(c.payload_json)["tp_sequence"],
    )
    for cmd in partial_cmds:
        payload = json.loads(cmd.payload_json)
        assert payload["tp_size"] == payload["sl_size"]


def test_d_multi_entry_1tp_uses_attached_per_leg():
    """Multi-entry + 1 TP routes to D_MULTI_ENTRY_1TP producing PLACE_ENTRY_WITH_ATTACHED_TPSL per leg."""
    gate = _make_gate(simple_attached_enabled=True)
    enriched = _make_enriched_signal(tp_count=1, entry_count=2)
    result = gate.process_signal(enriched, [], "NONE")
    assert result.trade_chain.execution_mode == "D_MULTI_ENTRY_1TP"
    entry_cmds = [c for c in result.execution_commands if c.command_type == "PLACE_ENTRY_WITH_ATTACHED_TPSL"]
    assert len(entry_cmds) == 2


def test_d_sets_execution_mode_on_chain():
    gate = _make_gate(simple_attached_enabled=False)
    enriched = _make_enriched_signal(tp_count=1, entry_count=1)
    result = gate.process_signal(enriched, [], "NONE")
    assert result.trade_chain.execution_mode == "D_POSITION_TPSL"


# ── Hedge mode tests ──────────────────────────────────────────────────────────

def test_hedge_long_position_idx_1():
    gate = _make_gate(simple_attached_enabled=False)
    enriched = _make_enriched_signal(tp_count=1, entry_count=1, hedge_mode=True)
    result = gate.process_signal(enriched, [], "NONE")
    tpsl_cmd = next(c for c in result.execution_commands
                    if c.command_type == "SET_POSITION_TPSL_FULL")
    payload = json.loads(tpsl_cmd.payload_json)
    assert payload["position_idx"] == 1


# ── C_SIMPLE_ATTACHED update guard ────────────────────────────────────────────

def test_c_mode_market_no_mark_price_produces_deferred_payload():
    """C mode con MARKET senza mark_price: payload deve avere qty_mode=deferred_market."""
    gate = _make_gate_no_mark(simple_attached_enabled=True)
    enriched = _make_enriched_market_c(sl_price=0.45, tp_price=0.60)
    result = gate.process_signal(enriched, [], "NONE")

    assert result.review_reason is None, result.review_reason
    assert len(result.execution_commands) == 1
    cmd = result.execution_commands[0]
    assert cmd.command_type == "PLACE_ENTRY_WITH_ATTACHED_TPSL"
    payload = json.loads(cmd.payload_json)
    assert payload["qty_mode"] == "deferred_market"
    assert "risk_amount" in payload
    assert payload["risk_amount"] > 0
    assert payload["sl_price"] == 0.45
    assert "qty" not in payload


def test_c_multi_tp_market_no_mark_price_produces_deferred_payload():
    """C_MULTI_TP con MARKET senza mark_price: PLACE_ENTRY_WITH_ATTACHED_TPSL ha qty_mode=deferred_market."""
    gate = _make_gate_no_mark(simple_attached_enabled=True)
    enriched = _make_enriched_market_d_multi_tp(sl_price=0.45)
    result = gate.process_signal(enriched, [], "NONE")

    assert result.review_reason is None, result.review_reason
    assert result.trade_chain.execution_mode == "C_MULTI_TP"
    entry_cmds = [c for c in result.execution_commands if c.command_type == "PLACE_ENTRY_WITH_ATTACHED_TPSL"]
    assert len(entry_cmds) == 1
    payload = json.loads(entry_cmds[0].payload_json)
    assert payload["qty_mode"] == "deferred_market"
    assert payload["risk_amount"] > 0
    assert "qty" not in payload
    assert payload["attached_tpsl"]["mode"] == "FULL"

    tp_cmds = [c for c in result.execution_commands if c.command_type == "SET_POSITION_TPSL_PARTIAL"]
    assert len(tp_cmds) == 1
    tp_payload = json.loads(tp_cmds[0].payload_json)
    assert tp_payload["tp_qty_mode"] == "filled_entry_pct"
    assert tp_payload["close_pct"] == 50.0


def test_d_multi_entry_1tp_mixed_market_limit_legs():
    """D_MULTI_ENTRY_1TP mixed: leg1 MARKET deferred + leg2 LIMIT, entrambi con PLACE_ENTRY_WITH_ATTACHED_TPSL."""
    gate = _make_gate_no_mark(simple_attached_enabled=True)
    enriched = _make_enriched_mixed_legs(sl_price=0.45, limit_price=0.48)
    result = gate.process_signal(enriched, [], "NONE")

    assert result.review_reason is None, result.review_reason
    assert result.trade_chain.execution_mode == "D_MULTI_ENTRY_1TP"
    entry_cmds = sorted(
        [c for c in result.execution_commands if c.command_type == "PLACE_ENTRY_WITH_ATTACHED_TPSL"],
        key=lambda c: json.loads(c.payload_json)["entry_type"],
    )
    assert len(entry_cmds) == 2

    # LIMIT leg
    limit_cmd = next(c for c in entry_cmds if json.loads(c.payload_json)["entry_type"] == "LIMIT")
    p_limit = json.loads(limit_cmd.payload_json)
    assert "qty" in p_limit
    assert p_limit["qty"] > 0
    assert "qty_mode" not in p_limit

    # MARKET leg (deferred)
    market_cmd = next(c for c in entry_cmds if json.loads(c.payload_json)["entry_type"] == "MARKET")
    p_market = json.loads(market_cmd.payload_json)
    assert p_market["qty_mode"] == "deferred_market"
    assert "qty" not in p_market


def test_c_mode_update_blocked_while_entry_pending():
    from src.parser_v2.contracts.canonical_message import (
        ActionItem, CloseOperation, TargetActionGroup,
    )
    from src.parser_v2.contracts.context import TargetHints
    from src.runtime_v2.lifecycle.models import ExecutionCommand, TradeChain

    gate = _make_gate(simple_attached_enabled=True)

    chain = TradeChain(
        trade_chain_id=99,
        source_enrichment_id=1, canonical_message_id=10, raw_message_id=5,
        trader_id="t1", account_id="main", symbol="BTC/USDT:USDT", side="LONG",
        lifecycle_state="WAITING_ENTRY", entry_mode="ONE_SHOT",
        management_plan_json="{}", execution_mode="C_SIMPLE_ATTACHED",
    )
    pending_cmd = ExecutionCommand(
        trade_chain_id=99,
        command_type="PLACE_ENTRY_WITH_ATTACHED_TPSL",
        status="PENDING",
        payload_json="{}",
        idempotency_key="test:1",
    )
    action = ActionItem(
        action_type="CLOSE",
        close=CloseOperation(close_scope="FULL"),
        source_intent="CLOSE_FULL",
    )
    tag = TargetActionGroup(
        targeting=TargetHints(scope_hint="SINGLE_SIGNAL", symbols=[]),
        actions=[action],
    )
    from src.runtime_v2.signal_enrichment.models import EnrichedCanonicalMessage
    enriched_update = EnrichedCanonicalMessage(
        enrichment_id=2, canonical_message_id=11, raw_message_id=50,
        trader_id="t1", account_id="main",
        primary_class="UPDATE", enrichment_decision="PASS",
        enriched_actions=[tag], policy_snapshot={},
    )

    result = gate.process_update(
        enriched_update,
        open_chains=[chain],
        active_commands_by_chain={99: [pending_cmd]},
    )
    chain_result = result.chain_results[0]
    events = chain_result.lifecycle_events
    assert any(e.event_type == "REVIEW_REQUIRED" for e in events)


# ── Routing matrix ────────────────────────────────────────────────────────────

def test_routing_1entry_1tp_uses_c_simple_attached():
    gate = _make_gate(simple_attached_enabled=True)
    result = gate.process_signal(_make_enriched_signal(entry_count=1, tp_count=1), [], "NONE")
    assert result.trade_chain is not None
    assert result.trade_chain.execution_mode == "C_SIMPLE_ATTACHED"


def test_routing_1entry_multi_tp_uses_c_multi_tp():
    gate = _make_gate(simple_attached_enabled=True)
    result = gate.process_signal(_make_enriched_signal(entry_count=1, tp_count=2), [], "NONE")
    assert result.trade_chain is not None
    assert result.trade_chain.execution_mode == "C_MULTI_TP"


def test_routing_multi_entry_1tp_uses_d_multi_entry_1tp():
    gate = _make_gate(simple_attached_enabled=True)
    result = gate.process_signal(_make_enriched_signal(entry_count=2, tp_count=1), [], "NONE")
    assert result.trade_chain is not None
    assert result.trade_chain.execution_mode == "D_MULTI_ENTRY_1TP"


def test_routing_multi_entry_multi_tp_uses_d_multi_entry_multi_tp():
    gate = _make_gate(simple_attached_enabled=True)
    result = gate.process_signal(_make_enriched_signal(entry_count=2, tp_count=2), [], "NONE")
    assert result.trade_chain is not None
    assert result.trade_chain.execution_mode == "D_MULTI_ENTRY_MULTI_TP"


def test_routing_no_sl_falls_back_to_review():
    """Senza SL il risk engine blocca il segnale."""
    from src.runtime_v2.signal_enrichment.models import (
        AccountConfig, EffectiveEnrichmentConfig, EnrichedCanonicalMessage,
        EnrichedEntryLeg, EnrichedSignalPayload, EntryRangeConfig,
        EntrySplitConfig, LimitEntrySplitConfig, ManagementPlanConfig,
        MarketEntrySplitConfig, MarketExecutionConfig, PriceCorrectionsConfig,
        PriceSanityConfig, RiskConfig, SignalPolicyConfig, SlConfig,
        TpConfig, EntryWeightsConfig,
    )
    from src.parser_v2.contracts.entities import Price, TakeProfit

    entries = [EnrichedEntryLeg(sequence=1, entry_type="LIMIT",
                                price=Price(raw="65000", value=65000.0), weight=1.0)]
    tps = [TakeProfit(price=Price(raw="70000", value=70000.0), sequence=1)]
    signal = EnrichedSignalPayload(
        symbol="BTC/USDT:USDT", side="LONG", entry_structure="ONE_SHOT",
        entries=entries, take_profits=tps, stop_loss=None,
    )
    w = EntryWeightsConfig(weights={"E1": 1.0})
    r = EntryRangeConfig(weights={"E1": 0.5, "E2": 0.5})
    risk = RiskConfig(leverage=5, capital_base_usdt=1000.0, risk_pct_of_capital=1.0)
    account = AccountConfig(id="main", capital_base_usdt=1000.0, max_leverage=10,
                            max_capital_at_risk_pct=10.0, hard_max_per_signal_risk_pct=2.0)
    sp = SignalPolicyConfig(
        accepted_entry_structures=["ONE_SHOT"],
        market_execution=MarketExecutionConfig(),
        entry_split=EntrySplitConfig(
            LIMIT=LimitEntrySplitConfig(single=w, range=r, averaging=w, ladder=w),
            MARKET=MarketEntrySplitConfig(single=w, averaging=w),
        ),
        tp=TpConfig(), sl=SlConfig(),
        price_corrections=PriceCorrectionsConfig(),
        price_sanity=PriceSanityConfig(),
    )
    cfg = EffectiveEnrichmentConfig(
        trader_id="t1", enabled=True, gate_mode="block", hedge_mode=False,
        account_id="main", signal_policy=sp, update_admission={},
        management_plan=ManagementPlanConfig(),
        risk=risk, account=account,
    )
    enriched = EnrichedCanonicalMessage(
        enrichment_id=99, canonical_message_id=990, raw_message_id=9900,
        trader_id="t1", account_id="main", primary_class="SIGNAL",
        enrichment_decision="PASS", enriched_signal=signal, enriched_actions=None,
        management_plan=ManagementPlanConfig(), policy_snapshot=cfg.model_dump(),
    )
    gate = _make_gate(simple_attached_enabled=True)
    result = gate.process_signal(enriched, [], "NONE")
    assert result.review_reason == "missing_stop_loss_for_risk_calc"


def test_routing_d_multi_entry_multi_tp_injects_tp_rebuild_in_snapshot():
    """D_MULTI_ENTRY_MULTI_TP: risk_snapshot_json contiene tp_rebuild con 2 livelli."""
    gate = _make_gate(simple_attached_enabled=True)
    result = gate.process_signal(_make_enriched_signal(entry_count=2, tp_count=2), [], "NONE")
    assert result.trade_chain is not None
    snap = json.loads(result.trade_chain.risk_snapshot_json)
    assert "tp_rebuild" in snap
    levels = snap["tp_rebuild"]["levels"]
    assert len(levels) == 2
    assert levels[0]["sequence"] == 1
    assert levels[1]["sequence"] == 2
    assert all("price" in lv and "close_pct" in lv for lv in levels)


# ── C_MULTI_TP (1 entry + 2 TP) ──────────────────────────────────────────────

def test_c_multi_tp_entry_has_sl_and_last_tp_attached():
    """C_MULTI_TP: entry ha PLACE_ENTRY_WITH_ATTACHED_TPSL con PARTIAL_TP (SL + ultimo TP)."""
    gate = _make_gate(simple_attached_enabled=True)
    result = gate.process_signal(_make_enriched_signal(entry_count=1, tp_count=2), [], "NONE")
    cmds = result.execution_commands
    entry_cmds = [c for c in cmds if c.command_type == "PLACE_ENTRY_WITH_ATTACHED_TPSL"]
    assert len(entry_cmds) == 1
    p = json.loads(entry_cmds[0].payload_json)
    tpsl = p["attached_tpsl"]
    assert tpsl["mode"] == "FULL"
    assert tpsl["stop_loss"] == 63000.0
    assert tpsl["take_profit"] == 70500.0   # sequence=2, price=70000+1*500
    assert "tp_qty" not in tpsl


def test_c_multi_tp_intermediate_tps_are_waiting_position():
    """C_MULTI_TP: TP intermedi (non ultimo) sono SET_POSITION_TPSL_PARTIAL WAITING_POSITION con preserve_sl=True."""
    gate = _make_gate(simple_attached_enabled=True)
    result = gate.process_signal(_make_enriched_signal(entry_count=1, tp_count=2), [], "NONE")
    cmds = result.execution_commands
    tp_cmds = [c for c in cmds if c.command_type == "SET_POSITION_TPSL_PARTIAL"]
    assert len(tp_cmds) == 1    # 1 intermedio (TP seq=1), l'ultimo è attached
    assert tp_cmds[0].status == "WAITING_POSITION"
    p = json.loads(tp_cmds[0].payload_json)
    assert p["preserve_sl"] is True
    assert p["take_profit"] == 70000.0   # TP sequence=1


def test_c_multi_tp_3tp_has_2_intermediate_commands():
    """C_MULTI_TP con 3 TP: 2 comandi WAITING_POSITION (seq 1,2), 1 TP attached (seq 3)."""
    gate = _make_gate(simple_attached_enabled=True)
    result = gate.process_signal(_make_enriched_signal(entry_count=1, tp_count=3), [], "NONE")
    cmds = result.execution_commands
    tp_cmds = [c for c in cmds if c.command_type == "SET_POSITION_TPSL_PARTIAL"]
    assert len(tp_cmds) == 2
    seqs = sorted(json.loads(c.payload_json)["tp_sequence"] for c in tp_cmds)
    assert seqs == [1, 2]
    entry_cmds = [c for c in cmds if c.command_type == "PLACE_ENTRY_WITH_ATTACHED_TPSL"]
    p = json.loads(entry_cmds[0].payload_json)
    assert p["attached_tpsl"]["take_profit"] == 71000.0  # seq=3, price=70000+2*500


# ── D_MULTI_ENTRY_1TP (2 entry + 1 TP) ──────────────────────────────────────

def test_d_multi_entry_1tp_each_leg_has_attached_tpsl():
    """D_MULTI_ENTRY_1TP: ogni leg produce PLACE_ENTRY_WITH_ATTACHED_TPSL mode FULL con SL+TP."""
    gate = _make_gate(simple_attached_enabled=True)
    result = gate.process_signal(_make_enriched_signal(entry_count=2, tp_count=1), [], "NONE")
    cmds = result.execution_commands
    entry_cmds = [c for c in cmds if c.command_type == "PLACE_ENTRY_WITH_ATTACHED_TPSL"]
    assert len(entry_cmds) == 2
    for c in entry_cmds:
        p = json.loads(c.payload_json)
        assert p["attached_tpsl"]["mode"] == "FULL"
        assert p["attached_tpsl"]["take_profit"] == 70000.0
        assert p["attached_tpsl"]["stop_loss"] == 63000.0


def test_d_multi_entry_1tp_no_waiting_position_commands():
    """D_MULTI_ENTRY_1TP: nessun comando SET_POSITION_TPSL_FULL o SET_POSITION_TPSL_PARTIAL."""
    gate = _make_gate(simple_attached_enabled=True)
    result = gate.process_signal(_make_enriched_signal(entry_count=2, tp_count=1), [], "NONE")
    cmds = result.execution_commands
    assert not any(c.command_type in {"SET_POSITION_TPSL_FULL", "SET_POSITION_TPSL_PARTIAL"}
                   for c in cmds)
    assert not any(c.status == "WAITING_POSITION" for c in cmds)


def test_d_multi_entry_1tp_idempotency_keys_are_distinct():
    """D_MULTI_ENTRY_1TP: ogni leg ha una idempotency_key diversa."""
    gate = _make_gate(simple_attached_enabled=True)
    result = gate.process_signal(_make_enriched_signal(entry_count=2, tp_count=1), [], "NONE")
    entry_cmds = [c for c in result.execution_commands
                  if c.command_type == "PLACE_ENTRY_WITH_ATTACHED_TPSL"]
    keys = [c.idempotency_key for c in entry_cmds]
    assert len(set(keys)) == 2


# ── D_MULTI_ENTRY_MULTI_TP (2 entry + 2 TP) ──────────────────────────────────

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


def test_d_multi_entry_multi_tp_no_set_position_tpsl_at_creation():
    """D_MULTI_ENTRY_MULTI_TP: nessun comando SET_POSITION_TPSL al momento della creazione."""
    gate = _make_gate(simple_attached_enabled=True)
    result = gate.process_signal(_make_enriched_signal(entry_count=2, tp_count=2), [], "NONE")
    cmds = result.execution_commands
    assert not any(c.command_type in {"SET_POSITION_TPSL_FULL", "SET_POSITION_TPSL_PARTIAL"}
                   for c in cmds)
