# src/runtime_v2/lifecycle/models.py
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

LifecycleState = Literal[
    "CREATED", "WAITING_ENTRY", "OPEN", "PARTIALLY_CLOSED",
    "BE_MOVE_PENDING", "PROTECTED_BE", "CLOSED", "CANCELLED",
    "EXPIRED", "REVIEW_REQUIRED", "ERROR",
]
TERMINAL_STATES: frozenset[str] = frozenset({"CLOSED", "CANCELLED", "EXPIRED"})

CommandType = Literal[
    "PLACE_ENTRY", "PLACE_ENTRY_WITH_ATTACHED_TPSL",
    "SET_POSITION_TPSL_FULL", "SET_POSITION_TPSL_PARTIAL",
    "MOVE_STOP_TO_BREAKEVEN", "MOVE_STOP", "MOVE_POSITION_STOP",
    "CANCEL_PENDING_ENTRY", "CANCEL_POSITION_TPSL",
    "CLOSE_PARTIAL", "CLOSE_FULL",
    "REBUILD_PARTIAL_TPS",
]
CommandStatus = Literal[
    "PENDING",           # creato da PRD-04, non ancora inviato
    "SENT",              # richiesta inviata all'adapter
    "ACK",               # exchange ha accettato l'ordine
    "WAITING_POSITION",  # attende fill reale (TP prima di entry fill)
    "DONE",              # ordine completato
    "FAILED",            # errore terminale
    "REVIEW_REQUIRED",   # richiede intervento manuale
    "CANCELLED",         # annullato da lifecycle o sostituito
    "SUPERSEDED",        # sostituito da un comando successivo equivalente
]

LifecycleEventType = Literal[
    "SIGNAL_ACCEPTED", "TRADE_CHAIN_CREATED", "ENTRY_COMMAND_CREATED",
    "ENTRY_FILLED", "TP_FILLED", "SL_FILLED", "TIMEOUT_REACHED",
    "TELEGRAM_UPDATE_ACCEPTED", "BE_MOVE_REQUESTED",
    "NOOP_ALREADY_PROTECTED_BE", "NOOP_DUPLICATE_COMMAND",
    "NOOP_ALREADY_CLOSED", "NOOP_NOT_PENDING", "NOOP_NO_APPLICABLE_TARGET",
    "NOOP_CANCEL_CONFIRMED_POSITION_UNRESOLVED",
    "REVIEW_REQUIRED",
    "POSITION_SIZE_UPDATED", "ENTRY_AVG_PRICE_UPDATED",
    "PROTECTIVE_SYNC_REQUESTED", "STOP_MOVE_CONFIRMED", "PENDING_ENTRY_CANCELLED",
    "CLOSE_FULL_FILLED", "CLOSE_PARTIAL_FILLED",
    "AUTO_CANCEL_AVERAGING_REQUESTED",
]

ExchangeEventType = Literal[
    "ENTRY_FILLED", "TP_FILLED", "SL_FILLED",
    "CLOSE_PARTIAL_FILLED", "CLOSE_FULL_FILLED",
    "STOP_MOVED_CONFIRMED", "PENDING_ENTRY_CANCELLED_CONFIRMED",
    "ORDER_REJECTED", "ORDER_CANCELLED",
]

LEGACY_BE_STATES: frozenset[str] = frozenset({"BE_MOVE_PENDING", "PROTECTED_BE"})

ControlMode = Literal["NONE", "BLOCK_NEW_ENTRIES", "FULL_STOP"]
BeProtectionStatus = Literal["NOT_PROTECTED", "BE_MOVE_PENDING", "PROTECTED"]


class TradeChain(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trade_chain_id: int | None = None
    source_enrichment_id: int
    canonical_message_id: int
    raw_message_id: int
    trader_id: str
    account_id: str
    symbol: str
    side: str
    lifecycle_state: LifecycleState
    entry_mode: str
    entry_avg_price: float | None = None
    current_stop_price: float | None = None
    expected_stop_price: float | None = None
    be_protection_status: BeProtectionStatus = "NOT_PROTECTED"
    entry_timeout_at: datetime | None = None
    management_plan_json: str
    risk_snapshot_json: str = "{}"
    planned_entry_qty: float = 0.0
    filled_entry_qty: float = 0.0
    open_position_qty: float = 0.0
    closed_position_qty: float = 0.0
    last_position_sync_at: datetime | None = None
    execution_mode: str = "D_POSITION_TPSL"
    risk_already_realized: float = 0.0
    risk_remaining: float = 0.0
    plan_state_json: str = "{}"
    source_chat_id: str | None = None
    telegram_message_id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class LifecycleEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: int | None = None
    trade_chain_id: int | None = None
    event_type: LifecycleEventType
    source_type: str
    source_id: str | None = None
    previous_state: str | None = None
    next_state: str | None = None
    payload_json: str = "{}"
    idempotency_key: str
    created_at: datetime | None = None


class ExecutionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command_id: int | None = None
    trade_chain_id: int
    command_type: CommandType
    status: CommandStatus = "PENDING"
    payload_json: str = "{}"
    idempotency_key: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ControlState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    control_id: int | None = None
    scope_type: str
    scope_value: str | None = None
    execution_pause_mode: ControlMode = "NONE"
    emergency_action: str | None = None
    reason: str | None = None
    created_by: str | None = None
    active: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ExchangeEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    exchange_event_id: int | None = None
    trade_chain_id: int | None = None
    event_type: str
    payload_json: str = "{}"
    processing_status: str = "NEW"
    idempotency_key: str
    received_at: datetime | None = None
    processed_at: datetime | None = None


__all__ = [
    "LifecycleState", "TERMINAL_STATES", "CommandType", "CommandStatus",
    "LifecycleEventType", "ExchangeEventType", "LEGACY_BE_STATES",
    "ControlMode", "BeProtectionStatus",
    "TradeChain", "LifecycleEvent", "ExecutionCommand",
    "ControlState", "ExchangeEvent",
]
