# src/runtime_v2/execution_gateway/event_ingest/models.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

SourceStream = Literal[
    "watch_my_trades",
    "watch_orders",
    "watch_positions",
    "fetch_my_trades",
    "fetch_open_orders",
    "fetch_positions",
]

EventSource = Literal[
    "bot_command",
    "exchange_auto",
    "exchange_manual",
    "reconciliation_inferred",
]

ExchangeEventType = Literal[
    "ENTRY_FILLED",
    "TP_FILLED",
    "SL_FILLED",
    "CLOSE_PARTIAL_FILLED",
    "CLOSE_FULL_FILLED",
    "MANUAL_CLOSE_PARTIAL",
    "MANUAL_CLOSE_FULL",
    "LIQUIDATION_FILLED",
    "PENDING_ENTRY_CANCELLED",
    "STANDALONE_PROTECTIVE_CANCELLED",
    "PROTECTIVE_ORDER_CANCELLED",
    "STOP_MOVED_CONFIRMED",
    "UNKNOWN",
]


@dataclass
class ExchangeRawEvent:
    source_stream:        SourceStream
    exchange_event_id:    str
    idempotency_key:      str
    symbol:               str
    side:                 str
    create_type:          str | None
    stop_order_type:      str | None
    exec_type:            str | None
    order_status:         str | None
    order_link_id:        str | None
    order_id:             str | None
    seq:                  int | None
    exec_price:           float | None
    exec_qty:             float | None
    closed_size:          float | None
    leaves_qty:           float | None
    pos_qty:              float | None
    exec_value:           float | None
    exec_fee:             float | None
    fee_rate:             float | None
    cum_exec_qty:         float | None
    position_take_profit: float | None = None
    position_stop_loss:   float | None = None
    exchange_time:        str | None = None
    received_at:          str = ""
    raw_info:             dict = field(default_factory=dict)


@dataclass
class ClassifiedEvent:
    raw:            ExchangeRawEvent
    event_type:     ExchangeEventType
    source:         EventSource
    trade_chain_id: int | None = None
    tp_level:       int | None = None
    is_actionable:  bool = True

    @property
    def should_forward_to_lifecycle(self) -> bool:
        return (
            self.is_actionable
            and self.trade_chain_id is not None
            and self.event_type != "UNKNOWN"
        )
