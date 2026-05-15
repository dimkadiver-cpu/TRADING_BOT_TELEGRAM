# tests/runtime_v2/lifecycle/test_models.py
from __future__ import annotations

import pytest
from pydantic import ValidationError


def test_trade_chain_requires_mandatory_fields():
    from src.runtime_v2.lifecycle.models import TradeChain
    with pytest.raises(ValidationError):
        TradeChain()  # mancano campi obbligatori


def test_trade_chain_valid():
    from src.runtime_v2.lifecycle.models import TradeChain
    chain = TradeChain(
        source_enrichment_id=1,
        canonical_message_id=10,
        raw_message_id=100,
        trader_id="trader_a",
        account_id="acc_1",
        symbol="BTC/USDT",
        side="LONG",
        lifecycle_state="WAITING_ENTRY",
        entry_mode="ONE_SHOT",
        management_plan_json="{}",
    )
    assert chain.be_protection_status == "NOT_PROTECTED"
    assert chain.trade_chain_id is None


def test_lifecycle_event_valid():
    from src.runtime_v2.lifecycle.models import LifecycleEvent
    event = LifecycleEvent(
        event_type="SIGNAL_ACCEPTED",
        source_type="enrichment",
        idempotency_key="sig_accepted:1",
    )
    assert event.trade_chain_id is None
    assert event.payload_json == "{}"


def test_execution_command_valid():
    from src.runtime_v2.lifecycle.models import ExecutionCommand
    cmd = ExecutionCommand(
        trade_chain_id=1,
        command_type="PLACE_ENTRY",
        payload_json='{"symbol": "BTC/USDT"}',
        idempotency_key="place_entry:1:1",
    )
    assert cmd.status == "PENDING"


def test_terminal_states():
    from src.runtime_v2.lifecycle.models import TERMINAL_STATES
    assert "CLOSED" in TERMINAL_STATES
    assert "CANCELLED" in TERMINAL_STATES
    assert "EXPIRED" in TERMINAL_STATES
    assert "OPEN" not in TERMINAL_STATES
