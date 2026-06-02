from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ExchangeEventPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    # Fill core (guaranteed by both WS and REST paths)
    fill_price: float | None = None
    filled_qty: float | None = None
    exec_fee: float | None = None
    exec_value: float | None = None
    exchange_time: str | None = None
    leaves_qty: float | None = None
    cum_exec_qty: float | None = None

    # WS-only — None on REST path (Bybit API limitation, not ours)
    closed_size: float | None = None
    fee_rate: float | None = None
    pos_qty: float | None = None

    # Identifiers
    exchange_event_id: str | None = None
    order_id: str | None = None
    order_link_id: str | None = None

    # Routing / classification
    tp_level: int | None = None
    command_id: int | None = None
    source: str | None = None


__all__ = ["ExchangeEventPayload"]
