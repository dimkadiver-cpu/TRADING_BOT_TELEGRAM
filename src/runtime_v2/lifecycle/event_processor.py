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
