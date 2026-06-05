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
        def symbol_exists(self, account_id, symbol):
            return True
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
        def symbol_exists(self, account_id, symbol):
            return True
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
    # All simple_attached_enabled + SL paths now use UNIFIED_PLAN
    assert result.trade_chain.execution_mode == "UNIFIED_PLAN"


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
    """Multi-entry + 1 TP: leg 1 = PLACE_ENTRY_WITH_ATTACHED_TPSL, leg 2+ = PLACE_ENTRY (unified rule)."""
    gate = _make_gate(simple_attached_enabled=True)
    enriched = _make_enriched_signal(tp_count=1, entry_count=2)
    result = gate.process_signal(enriched, [], "NONE")
    assert result.trade_chain.execution_mode == "UNIFIED_PLAN"
    attached_cmds = [c for c in result.execution_commands if c.command_type == "PLACE_ENTRY_WITH_ATTACHED_TPSL"]
    plain_cmds = [c for c in result.execution_commands if c.command_type == "PLACE_ENTRY"]
    assert len(attached_cmds) == 1   # only leg 1 gets attached TPSL
    assert len(plain_cmds) == 1      # leg 2 is plain


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
    """1 entry MARKET no mark_price + 2 TP: UNIFIED_PLAN, 1 deferred PLACE_ENTRY_WITH_ATTACHED_TPSL.

    Intermediate TPs are NOT emitted at signal time under the unified rule.
    Only the final (highest-sequence) TP is attached.
    """
    gate = _make_gate_no_mark(simple_attached_enabled=True)
    enriched = _make_enriched_market_d_multi_tp(sl_price=0.45)
    result = gate.process_signal(enriched, [], "NONE")

    assert result.review_reason is None, result.review_reason
    assert result.trade_chain.execution_mode == "UNIFIED_PLAN"
    entry_cmds = [c for c in result.execution_commands if c.command_type == "PLACE_ENTRY_WITH_ATTACHED_TPSL"]
    assert len(entry_cmds) == 1
    payload = json.loads(entry_cmds[0].payload_json)
    assert payload["qty_mode"] == "deferred_market"
    assert payload["risk_amount"] > 0
    assert "qty" not in payload
    assert payload["attached_tpsl"]["mode"] == "FULL"
    # Final TP (sequence 2, price=0.65) is attached; no intermediate TP commands
    assert payload["attached_tpsl"]["take_profit"] == 0.65

    # No intermediate TP commands — all deferred to post-fill
    tp_cmds = [c for c in result.execution_commands if c.command_type == "SET_POSITION_TPSL_PARTIAL"]
    assert len(tp_cmds) == 0


def test_d_multi_entry_1tp_mixed_market_limit_legs():
    """2 entries (MARKET seq=1 deferred + LIMIT seq=2), 1 TP — unified rule applies.

    leg 1 (MARKET, deferred) → PLACE_ENTRY_WITH_ATTACHED_TPSL with qty_mode=deferred_market
    leg 2 (LIMIT, fixed qty) → PLACE_ENTRY (no attached TPSL)
    """
    gate = _make_gate_no_mark(simple_attached_enabled=True)
    enriched = _make_enriched_mixed_legs(sl_price=0.45, limit_price=0.48)
    result = gate.process_signal(enriched, [], "NONE")

    assert result.review_reason is None, result.review_reason
    assert result.trade_chain.execution_mode == "UNIFIED_PLAN"

    attached_cmds = [c for c in result.execution_commands if c.command_type == "PLACE_ENTRY_WITH_ATTACHED_TPSL"]
    plain_cmds = [c for c in result.execution_commands if c.command_type == "PLACE_ENTRY"]
    assert len(attached_cmds) == 1  # leg 1 only
    assert len(plain_cmds) == 1     # leg 2 only

    # leg 1 = MARKET, deferred
    p_leg1 = json.loads(attached_cmds[0].payload_json)
    assert p_leg1["entry_type"] == "MARKET"
    assert p_leg1["qty_mode"] == "deferred_market"
    assert "qty" not in p_leg1
    assert "attached_tpsl" in p_leg1

    # leg 2 = LIMIT, fixed qty, no attached TPSL
    p_leg2 = json.loads(plain_cmds[0].payload_json)
    assert p_leg2["entry_type"] == "LIMIT"
    assert "qty" in p_leg2
    assert p_leg2["qty"] > 0
    assert "attached_tpsl" not in p_leg2


# ── Routing matrix ────────────────────────────────────────────────────────────

def test_routing_1entry_1tp_uses_unified_plan():
    """All simple_attached_enabled + SL paths now use UNIFIED_PLAN regardless of entry/TP counts."""
    gate = _make_gate(simple_attached_enabled=True)
    result = gate.process_signal(_make_enriched_signal(entry_count=1, tp_count=1), [], "NONE")
    assert result.trade_chain is not None
    assert result.trade_chain.execution_mode == "UNIFIED_PLAN"


def test_routing_1entry_multi_tp_uses_unified_plan():
    gate = _make_gate(simple_attached_enabled=True)
    result = gate.process_signal(_make_enriched_signal(entry_count=1, tp_count=2), [], "NONE")
    assert result.trade_chain is not None
    assert result.trade_chain.execution_mode == "UNIFIED_PLAN"


def test_routing_multi_entry_1tp_uses_unified_plan():
    gate = _make_gate(simple_attached_enabled=True)
    result = gate.process_signal(_make_enriched_signal(entry_count=2, tp_count=1), [], "NONE")
    assert result.trade_chain is not None
    assert result.trade_chain.execution_mode == "UNIFIED_PLAN"


def test_routing_multi_entry_multi_tp_uses_unified_plan():
    gate = _make_gate(simple_attached_enabled=True)
    result = gate.process_signal(_make_enriched_signal(entry_count=2, tp_count=2), [], "NONE")
    assert result.trade_chain is not None
    assert result.trade_chain.execution_mode == "UNIFIED_PLAN"


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


def test_routing_unified_plan_no_tp_rebuild_in_snapshot():
    """UNIFIED_PLAN: tp_rebuild is NOT injected into risk_snapshot (removed with the old routing)."""
    gate = _make_gate(simple_attached_enabled=True)
    result = gate.process_signal(_make_enriched_signal(entry_count=2, tp_count=2), [], "NONE")
    assert result.trade_chain is not None
    assert result.trade_chain.execution_mode == "UNIFIED_PLAN"
    snap = json.loads(result.trade_chain.risk_snapshot_json)
    assert "tp_rebuild" not in snap


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


def test_unified_plan_multi_tp_no_intermediate_commands():
    """UNIFIED_PLAN: NO intermediate TP commands emitted at signal time (all deferred to post-fill).

    Only 1 PLACE_ENTRY_WITH_ATTACHED_TPSL with final TP attached.
    """
    gate = _make_gate(simple_attached_enabled=True)
    result = gate.process_signal(_make_enriched_signal(entry_count=1, tp_count=2), [], "NONE")
    cmds = result.execution_commands
    # No intermediate TP commands
    tp_cmds = [c for c in cmds if c.command_type == "SET_POSITION_TPSL_PARTIAL"]
    assert len(tp_cmds) == 0
    # Only 1 entry command with final TP attached
    entry_cmds = [c for c in cmds if c.command_type == "PLACE_ENTRY_WITH_ATTACHED_TPSL"]
    assert len(entry_cmds) == 1
    p = json.loads(entry_cmds[0].payload_json)
    assert p["attached_tpsl"]["take_profit"] == 70500.0   # seq=2, price=70000+1*500


def test_unified_plan_3tp_no_intermediate_commands():
    """UNIFIED_PLAN con 3 TP: nessun comando intermedio, solo 1 PLACE_ENTRY_WITH_ATTACHED_TPSL con TP finale."""
    gate = _make_gate(simple_attached_enabled=True)
    result = gate.process_signal(_make_enriched_signal(entry_count=1, tp_count=3), [], "NONE")
    cmds = result.execution_commands
    tp_cmds = [c for c in cmds if c.command_type == "SET_POSITION_TPSL_PARTIAL"]
    assert len(tp_cmds) == 0
    entry_cmds = [c for c in cmds if c.command_type == "PLACE_ENTRY_WITH_ATTACHED_TPSL"]
    assert len(entry_cmds) == 1
    p = json.loads(entry_cmds[0].payload_json)
    assert p["attached_tpsl"]["take_profit"] == 71000.0  # seq=3, price=70000+2*500


# ── D_MULTI_ENTRY_1TP (2 entry + 1 TP) ──────────────────────────────────────

def test_d_multi_entry_1tp_each_leg_has_attached_tpsl():
    """UNIFIED_PLAN multi-entry + 1 TP: only leg 1 has PLACE_ENTRY_WITH_ATTACHED_TPSL mode FULL.

    Leg 2+ gets plain PLACE_ENTRY (no attached TPSL) — unified rule.
    """
    gate = _make_gate(simple_attached_enabled=True)
    result = gate.process_signal(_make_enriched_signal(entry_count=2, tp_count=1), [], "NONE")
    cmds = result.execution_commands
    attached_cmds = [c for c in cmds if c.command_type == "PLACE_ENTRY_WITH_ATTACHED_TPSL"]
    plain_cmds = [c for c in cmds if c.command_type == "PLACE_ENTRY"]
    assert len(attached_cmds) == 1  # leg 1 only
    assert len(plain_cmds) == 1     # leg 2
    p = json.loads(attached_cmds[0].payload_json)
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
    """UNIFIED_PLAN multi-entry: all entry commands have distinct idempotency keys."""
    gate = _make_gate(simple_attached_enabled=True)
    result = gate.process_signal(_make_enriched_signal(entry_count=2, tp_count=1), [], "NONE")
    all_entry_cmds = [c for c in result.execution_commands
                      if c.command_type in ("PLACE_ENTRY_WITH_ATTACHED_TPSL", "PLACE_ENTRY")]
    keys = [c.idempotency_key for c in all_entry_cmds]
    assert len(set(keys)) == 2  # 2 distinct keys for 2 legs


# ── D_MULTI_ENTRY_MULTI_TP (2 entry + 2 TP) ──────────────────────────────────

def test_d_multi_entry_multi_tp_each_leg_has_partial_tp_attached():
    """UNIFIED_PLAN multi-entry + multi-TP: leg 1 = PLACE_ENTRY_WITH_ATTACHED_TPSL (FULL mode, final TP).

    Leg 2+ = PLACE_ENTRY (no attached TPSL). No PARTIAL_TP mode under unified rule.
    """
    gate = _make_gate(simple_attached_enabled=True)
    result = gate.process_signal(_make_enriched_signal(entry_count=2, tp_count=2), [], "NONE")
    cmds = result.execution_commands
    attached_cmds = [c for c in cmds if c.command_type == "PLACE_ENTRY_WITH_ATTACHED_TPSL"]
    plain_cmds = [c for c in cmds if c.command_type == "PLACE_ENTRY"]
    assert len(attached_cmds) == 1  # leg 1 only
    assert len(plain_cmds) == 1     # leg 2
    p = json.loads(attached_cmds[0].payload_json)
    tpsl = p["attached_tpsl"]
    assert tpsl["mode"] == "FULL"   # unified factory always uses FULL mode
    assert tpsl["stop_loss"] == 63000.0
    assert tpsl["take_profit"] == 70500.0   # final TP (seq=2, price=70000+1*500)


def test_d_multi_entry_multi_tp_no_set_position_tpsl_at_creation():
    """D_MULTI_ENTRY_MULTI_TP: nessun comando SET_POSITION_TPSL al momento della creazione."""
    gate = _make_gate(simple_attached_enabled=True)
    result = gate.process_signal(_make_enriched_signal(entry_count=2, tp_count=2), [], "NONE")
    cmds = result.execution_commands
    assert not any(c.command_type in {"SET_POSITION_TPSL_FULL", "SET_POSITION_TPSL_PARTIAL"}
                   for c in cmds)
