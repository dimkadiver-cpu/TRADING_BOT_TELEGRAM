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


def _make_gate(simple_attached_enabled: bool = True) -> LifecycleEntryGate:
    return LifecycleEntryGate(
        risk_engine=RiskCapacityEngine(),
        exchange_port=_make_port(),
        simple_attached_enabled=simple_attached_enabled,
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


def test_d_multi_entry_forces_d_mode():
    gate = _make_gate(simple_attached_enabled=True)
    enriched = _make_enriched_signal(tp_count=1, entry_count=2)
    result = gate.process_signal(enriched, [], "NONE")
    cmd_types = [c.command_type for c in result.execution_commands]
    assert "PLACE_ENTRY_WITH_ATTACHED_TPSL" not in cmd_types
    assert "PLACE_ENTRY" in cmd_types


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
