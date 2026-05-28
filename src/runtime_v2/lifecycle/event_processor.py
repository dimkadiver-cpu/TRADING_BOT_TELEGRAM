# src/runtime_v2/lifecycle/event_processor.py
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from src.runtime_v2.lifecycle.be_move_resolver import resolve_be_stop_price
from src.runtime_v2.lifecycle.models import (
    BeProtectionStatus, ExecutionCommand, ExchangeEvent,
    LifecycleEvent, LifecycleState, TradeChain,
)
from src.runtime_v2.lifecycle.execution_plan import ExecutionPlanBuilder
from src.runtime_v2.lifecycle.post_fill_rebuilder import PostFillProtectionRebuilder
from src.runtime_v2.signal_enrichment.models import ManagementPlanConfig

logger = logging.getLogger(__name__)

_ATTACHED_PROTECTION_MODES = frozenset({"UNIFIED_PLAN", "D_POSITION_TPSL"})


def _be_move_extra(chain: TradeChain) -> dict:
    try:
        rs = json.loads(chain.risk_snapshot_json or "{}")
        hedge_mode = bool(rs.get("hedge_mode", False))
    except Exception:
        hedge_mode = False
    if not hedge_mode:
        position_idx = 0
    else:
        position_idx = 1 if chain.side == "LONG" else 2
    protection_style = (
        "attached_full"
        if chain.execution_mode in _ATTACHED_PROTECTION_MODES
        else "standalone_order"
    )
    return {"protection_style": protection_style, "position_idx": position_idx}


def _set_be_deferred_flag(plan_state_json: str, *, tp_level: int, averaging_legs_pending: int) -> str:
    """Aggiunge il flag _be_deferred_by_auto_cancel al plan_state_json."""
    try:
        plan = json.loads(plan_state_json or "{}")
    except Exception:
        plan = {}
    plan["_be_deferred_by_auto_cancel"] = {
        "tp_level": tp_level,
        "averaging_legs_pending": averaging_legs_pending,
    }
    return json.dumps(plan)


def _clear_be_deferred_flag(plan_state_json: str) -> str:
    """Rimuove il flag _be_deferred_by_auto_cancel dal plan_state_json."""
    try:
        plan = json.loads(plan_state_json or "{}")
    except Exception:
        return plan_state_json or "{}"
    plan.pop("_be_deferred_by_auto_cancel", None)
    return json.dumps(plan)


def _get_be_deferred_flag(plan_state_json: str) -> dict | None:
    """Ritorna il flag _be_deferred_by_auto_cancel se presente, altrimenti None."""
    try:
        plan = json.loads(plan_state_json or "{}")
        return plan.get("_be_deferred_by_auto_cancel") or None
    except Exception:
        return None


@dataclass
class EventProcessorResult:
    new_lifecycle_state: LifecycleState | None
    new_be_protection_status: BeProtectionStatus | None
    entry_avg_price: float | None
    current_stop_price: float | None
    lifecycle_events: list[LifecycleEvent]
    execution_commands: list[ExecutionCommand]
    new_filled_entry_qty: float | None = None
    new_open_position_qty: float | None = None
    new_closed_position_qty: float | None = None
    new_risk_already_realized: float | None = None
    new_risk_remaining: float | None = None
    new_plan_state_json: str | None = None
    release_waiting_position: bool = False


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
        if etype == "CLOSE_FULL_FILLED":
            return self._process_close_full_filled(exchange_event, chain)
        if etype == "CLOSE_PARTIAL_FILLED":
            return self._process_close_partial_filled(exchange_event, chain)
        if etype == "STOP_MOVED_CONFIRMED":
            return self._process_stop_moved_confirmed(exchange_event, chain)
        if etype == "PENDING_ENTRY_CANCELLED_CONFIRMED":
            return self._process_pending_entry_cancelled_confirmed(exchange_event, chain, active_commands)
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
        fill_price = float(payload.get("fill_price") or 0.0)
        fill_qty = float(payload.get("filled_qty") or 0.0)
        filled_client_order_id = payload.get("entry_client_order_id")
        filled_command_payload = payload.get("entry_command_payload")
        if not isinstance(filled_command_payload, dict):
            filled_command_payload = None
        eid = exchange_event.exchange_event_id
        chain_id = chain.trade_chain_id

        old_filled = chain.filled_entry_qty
        old_avg = chain.entry_avg_price or 0.0
        new_filled = old_filled + fill_qty
        if new_filled > 0:
            new_avg = ((old_avg * old_filled) + (fill_price * fill_qty)) / new_filled
        else:
            new_avg = fill_price
        new_open = chain.open_position_qty + fill_qty
        risk_snapshot = {}
        sl_price: float | None = None
        try:
            risk_snapshot = json.loads(chain.risk_snapshot_json or "{}")
            sl_raw = risk_snapshot.get("sl_price")
            sl_price = float(sl_raw) if sl_raw is not None else None
        except Exception:
            sl_price = None

        new_risk_already_realized: float | None = None
        new_risk_remaining: float | None = None
        if sl_price is not None:
            fill_risk = fill_qty * abs(fill_price - sl_price)
            new_risk_already_realized = chain.risk_already_realized + fill_risk
            risk_total = float(risk_snapshot.get("risk_amount", 0.0) or 0.0)
            if risk_total > 0:
                new_risk_remaining = max(0.0, risk_total - new_risk_already_realized)

        is_first_fill = chain.lifecycle_state == "WAITING_ENTRY"
        new_state: LifecycleState | None = "OPEN" if is_first_fill else None

        events: list[LifecycleEvent] = [
            LifecycleEvent(
                trade_chain_id=chain_id,
                event_type="ENTRY_FILLED",
                source_type="exchange_event",
                source_id=str(eid),
                previous_state=chain.lifecycle_state,
                next_state=new_state or chain.lifecycle_state,
                payload_json=json.dumps({"fill_price": fill_price, "filled_qty": fill_qty}),
                idempotency_key=f"entry_filled:{chain_id}:{eid}",
            ),
            LifecycleEvent(
                trade_chain_id=chain_id,
                event_type="POSITION_SIZE_UPDATED",
                source_type="exchange_event",
                source_id=str(eid),
                payload_json=json.dumps({"filled_entry_qty": new_filled, "open_position_qty": new_open}),
                idempotency_key=f"pos_size_updated:{chain_id}:{eid}",
            ),
            LifecycleEvent(
                trade_chain_id=chain_id,
                event_type="ENTRY_AVG_PRICE_UPDATED",
                source_type="exchange_event",
                source_id=str(eid),
                payload_json=json.dumps({"entry_avg_price": new_avg}),
                idempotency_key=f"avg_price_updated:{chain_id}:{eid}",
            ),
        ]

        commands = PostFillProtectionRebuilder().build_after_fill(chain, new_filled, eid or 0)
        new_plan_state_json = self._mark_entry_leg_status(
            chain.plan_state_json,
            client_order_ids=[filled_client_order_id] if filled_client_order_id else [],
            command_payload=filled_command_payload,
            new_status="FILLED",
            fallback_first_pending=True,
        )

        # ── Deferred BE: controlla se questa fill completa le averaging leg ───
        effective_plan = new_plan_state_json or chain.plan_state_json or "{}"
        deferred = _get_be_deferred_flag(effective_plan)
        if deferred:
            try:
                mp_fill = ManagementPlanConfig.model_validate_json(chain.management_plan_json)
            except Exception:
                mp_fill = ManagementPlanConfig()
            remaining_averaging = ExecutionPlanBuilder.get_pending_averaging_legs(effective_plan)
            if not remaining_averaging:
                # Crea una chain temporanea con entry_avg_price aggiornato per calcolare BE corretto
                chain_for_be = chain.model_copy(update={"entry_avg_price": new_avg})
                be_result = self._build_be_move_command_and_event(chain_for_be, eid or 0, mp_fill)
                if be_result is not None:
                    be_cmd, be_event = be_result
                    commands.append(be_cmd)
                    events.append(be_event)
                effective_plan = _clear_be_deferred_flag(effective_plan)
                new_plan_state_json = effective_plan

        return EventProcessorResult(
            new_lifecycle_state=new_state,
            new_be_protection_status=None,
            entry_avg_price=new_avg,
            current_stop_price=None,
            lifecycle_events=events,
            execution_commands=commands,
            new_filled_entry_qty=new_filled,
            new_open_position_qty=new_open,
            new_risk_already_realized=new_risk_already_realized,
            new_risk_remaining=new_risk_remaining,
            new_plan_state_json=new_plan_state_json,
            release_waiting_position=is_first_fill,
        )

    def _mark_entry_leg_status(
        self,
        plan_state_json: str,
        *,
        client_order_ids: list[str],
        command_payload: dict | None = None,
        new_status: str,
        fallback_first_pending: bool = False,
    ) -> str | None:
        try:
            current_plan_json = plan_state_json or "{}"
            plan = json.loads(current_plan_json)
        except Exception:
            return None

        legs = plan.get("legs", [])
        if not legs:
            return None

        target_legs = [
            leg for leg in legs
            if leg.get("client_order_id") in client_order_ids
            and leg.get("status") == "PENDING"
        ]
        if not target_legs and command_payload:
            target_legs = self._match_pending_legs_by_command_payload(
                legs,
                command_payload,
            )
        if not target_legs and fallback_first_pending:
            pending_legs = [leg for leg in legs if leg.get("status") == "PENDING"]
            target_legs = pending_legs if len(pending_legs) == 1 else []
        if not target_legs:
            return None

        updated = current_plan_json
        for leg in target_legs:
            updated = ExecutionPlanBuilder.update_leg_status(
                updated,
                str(leg.get("leg_id")),
                new_status,
            )
        return updated

    def _mark_entry_leg_status_by_sequence(
        self,
        plan_state_json: str,
        *,
        sequence: int,
        new_status: str,
    ) -> str | None:
        """Fallback: cerca le leg con leg["sequence"] == sequence e status PENDING.
        Usato quando il match per client_order_id fallisce (piano ha placeholder ID)."""
        try:
            plan = json.loads(plan_state_json or "{}")
        except Exception:
            return None
        legs = plan.get("legs", [])
        target_legs = [
            leg for leg in legs
            if leg.get("sequence") == sequence and leg.get("status") == "PENDING"
        ]
        if len(target_legs) != 1:
            # 0 = not found; >1 = ambiguous (should not happen in well-formed plan)
            return None
        updated = plan_state_json
        for leg in target_legs:
            updated = ExecutionPlanBuilder.update_leg_status(
                updated,
                str(leg.get("leg_id")),
                new_status,
            )
        return updated

    def _match_pending_legs_by_command_payload(
        self,
        legs: list[dict],
        command_payload: dict,
    ) -> list[dict]:
        pending_legs = [leg for leg in legs if leg.get("status") == "PENDING"]
        sequence = command_payload.get("sequence")
        if sequence is not None:
            try:
                seq_int = int(sequence)
            except (TypeError, ValueError):
                seq_int = None
            if seq_int is not None:
                matches = [leg for leg in pending_legs if leg.get("sequence") == seq_int]
                if len(matches) == 1:
                    return matches

        entry_type = command_payload.get("entry_type")
        price = command_payload.get("price")
        qty = command_payload.get("qty")

        def _same_number(left, right) -> bool:
            if left is None or right is None:
                return left is right
            try:
                return abs(float(left) - float(right)) <= 1e-9
            except (TypeError, ValueError):
                return left == right

        matches = [
            leg for leg in pending_legs
            if (entry_type is None or leg.get("entry_type") == entry_type)
            and _same_number(leg.get("price"), price)
            and _same_number(leg.get("qty"), qty)
        ]
        return matches if len(matches) == 1 else []

    def _build_be_move_command_and_event(
        self,
        chain: TradeChain,
        eid: int,
        management_plan: ManagementPlanConfig,
    ) -> tuple[ExecutionCommand, LifecycleEvent] | None:
        """
        Calcola il prezzo BE e ritorna (command, event) se possibile.
        Ritorna None se BE non può essere calcolato (entry_avg_price assente)
        o se è già protetto.
        """
        if chain.be_protection_status in ("PROTECTED", "BE_MOVE_PENDING"):
            return None
        chain_id = chain.trade_chain_id
        extra = _be_move_extra(chain)
        new_stop_price = resolve_be_stop_price(chain, management_plan, protection_style=extra["protection_style"])
        if new_stop_price is None:
            logger.warning(
                "skipping deferred be move without entry_avg_price: chain_id=%s event_id=%s",
                chain_id, eid,
            )
            return None
        cmd_payload = {
            "symbol": chain.symbol, "side": chain.side,
            "new_stop_price": new_stop_price,
            "is_breakeven": True,
            **extra,
        }
        command = ExecutionCommand(
            trade_chain_id=chain_id,
            command_type="MOVE_STOP_TO_BREAKEVEN",
            payload_json=json.dumps(cmd_payload),
            idempotency_key=f"deferred_be:{chain_id}:{eid}",
        )
        event = LifecycleEvent(
            trade_chain_id=chain_id,
            event_type="BE_MOVE_REQUESTED",
            source_type="exchange_event",
            source_id=str(eid),
            idempotency_key=f"deferred_be_req:{chain_id}:{eid}",
        )
        return command, event

    def _process_tp_filled(
        self,
        exchange_event: ExchangeEvent,
        chain: TradeChain,
        active_commands: list[ExecutionCommand],
    ) -> EventProcessorResult:
        payload = json.loads(exchange_event.payload_json)
        tp_level = int(payload.get("tp_level", 1))
        is_final = bool(payload.get("is_final", False))
        fill_qty = float(payload.get("filled_qty") or 0.0)
        eid = exchange_event.exchange_event_id
        chain_id = chain.trade_chain_id

        new_state: LifecycleState = "CLOSED" if is_final else "PARTIALLY_CLOSED"
        new_open = 0.0 if is_final else max(chain.open_position_qty - fill_qty, 0.0)
        new_closed = chain.closed_position_qty + fill_qty
        events: list[LifecycleEvent] = []
        commands: list[ExecutionCommand] = []
        new_be: BeProtectionStatus | None = None
        new_plan_state_json: str | None = None

        if not is_final:
            try:
                mp = ManagementPlanConfig.model_validate_json(chain.management_plan_json)
            except Exception:
                mp = ManagementPlanConfig()

            # ── Auto-cancel averaging legs ─────────────────────────────────────
            be_trigger = mp.be_trigger
            be_would_fire_now = be_trigger == f"tp{tp_level}"
            auto_cancel_active = False

            if mp.cancel_pending_by_engine and mp.cancel_averaging_pending_after == f"tp{tp_level}":
                averaging_legs = ExecutionPlanBuilder.get_pending_averaging_legs(chain.plan_state_json)
                if averaging_legs:
                    auto_cancel_active = True
                    # Pre-calcola se il BE sarà differito (evita mutazione lista eventi)
                    deferred_be = be_would_fire_now and chain.be_protection_status not in ("PROTECTED", "BE_MOVE_PENDING")
                    commands.append(ExecutionCommand(
                        trade_chain_id=chain_id,
                        command_type="CANCEL_PENDING_ENTRY",
                        payload_json=json.dumps({
                            "symbol": chain.symbol,
                            "side": chain.side,
                            "cancel_reason": "auto_cancel_averaging",
                        }),
                        idempotency_key=f"auto_cancel_avg:{chain_id}:{eid}",
                    ))
                    events.append(LifecycleEvent(
                        trade_chain_id=chain_id,
                        event_type="AUTO_CANCEL_AVERAGING_REQUESTED",
                        source_type="engine",
                        source_id=str(eid),
                        payload_json=json.dumps({
                            "tp_level": tp_level,
                            "legs_cancelled": len(averaging_legs),
                            "deferred_be": deferred_be,
                        }),
                        idempotency_key=f"auto_cancel_avg_req:{chain_id}:{eid}",
                    ))
                    if deferred_be:
                        new_plan_state_json = _set_be_deferred_flag(
                            chain.plan_state_json,
                            tp_level=tp_level,
                            averaging_legs_pending=len(averaging_legs),
                        )

            # ── Breakeven trigger ──────────────────────────────────────────────
            if be_would_fire_now:
                if auto_cancel_active:
                    pass  # BE differito — verrà emesso da _process_pending_entry_cancelled_confirmed
                elif chain.be_protection_status == "PROTECTED":
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
                        extra = _be_move_extra(chain)
                        new_stop_price = resolve_be_stop_price(chain, mp, protection_style=extra["protection_style"])
                        if new_stop_price is None:
                            logger.warning(
                                "skipping automatic be move without entry_avg_price: chain_id=%s event_id=%s",
                                chain_id, eid,
                            )
                        else:
                            cmd_payload = {
                                "symbol": chain.symbol, "side": chain.side,
                                "new_stop_price": new_stop_price,
                                "is_breakeven": True,
                                **extra,
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
                            new_be = "BE_MOVE_PENDING"

            # Non-final TP: emit SYNC_PROTECTIVE_ORDERS so exchange orders reflect new qty
            commands.append(ExecutionCommand(
                trade_chain_id=chain_id,
                command_type="SYNC_PROTECTIVE_ORDERS",
                payload_json=json.dumps({"symbol": chain.symbol, "side": chain.side}),
                idempotency_key=f"sync_after_tp:{chain_id}:{eid}",
            ))

        tp_event = LifecycleEvent(
            trade_chain_id=chain_id,
            event_type="TP_FILLED",
            source_type="exchange_event",
            source_id=str(eid),
            previous_state=chain.lifecycle_state,
            next_state=new_state,
            payload_json=json.dumps({"tp_level": tp_level, "is_final": is_final}),
            idempotency_key=f"tp_filled:{chain_id}:{eid}",
        )
        events.insert(0, tp_event)

        return EventProcessorResult(
            new_lifecycle_state=new_state,
            new_be_protection_status=new_be,
            entry_avg_price=None,
            current_stop_price=None,
            lifecycle_events=events,
            execution_commands=commands,
            new_open_position_qty=new_open,
            new_closed_position_qty=new_closed,
            new_plan_state_json=new_plan_state_json,
        )

    def _process_sl_filled(
        self, exchange_event: ExchangeEvent, chain: TradeChain
    ) -> EventProcessorResult:
        payload = json.loads(exchange_event.payload_json)
        fill_qty = float(payload.get("filled_qty") or chain.open_position_qty)
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
            new_open_position_qty=0.0,
            new_closed_position_qty=chain.closed_position_qty + fill_qty,
        )

    def _process_close_full_filled(
        self, exchange_event: ExchangeEvent, chain: TradeChain
    ) -> EventProcessorResult:
        payload = json.loads(exchange_event.payload_json)
        fill_qty = float(payload.get("filled_qty") or chain.open_position_qty)
        eid = exchange_event.exchange_event_id
        chain_id = chain.trade_chain_id
        return EventProcessorResult(
            new_lifecycle_state="CLOSED",
            new_be_protection_status=None,
            entry_avg_price=None,
            current_stop_price=None,
            lifecycle_events=[LifecycleEvent(
                trade_chain_id=chain_id,
                event_type="CLOSE_FULL_FILLED",
                source_type="exchange_event",
                source_id=str(eid),
                previous_state=chain.lifecycle_state,
                next_state="CLOSED",
                payload_json=exchange_event.payload_json,
                idempotency_key=f"close_full_filled:{chain_id}:{eid}",
            )],
            execution_commands=[ExecutionCommand(
                trade_chain_id=chain_id,
                command_type="CANCEL_PENDING_ENTRY",
                payload_json=json.dumps({
                    "symbol": chain.symbol,
                    "side": chain.side,
                    "cancel_reason": "position_closed",
                }),
                idempotency_key=f"cancel_on_close:{chain_id}",
            )],
            new_open_position_qty=0.0,
            new_closed_position_qty=chain.closed_position_qty + fill_qty,
        )

    def _process_close_partial_filled(
        self, exchange_event: ExchangeEvent, chain: TradeChain
    ) -> EventProcessorResult:
        payload = json.loads(exchange_event.payload_json)
        fill_qty = float(payload.get("filled_qty") or 0.0)
        eid = exchange_event.exchange_event_id
        chain_id = chain.trade_chain_id
        new_open = max(chain.open_position_qty - fill_qty, 0.0)
        new_closed = chain.closed_position_qty + fill_qty
        new_state: LifecycleState = "CLOSED" if new_open <= 0 else "PARTIALLY_CLOSED"
        commands: list[ExecutionCommand] = []
        if new_state == "PARTIALLY_CLOSED":
            commands.append(ExecutionCommand(
                trade_chain_id=chain_id,
                command_type="SYNC_PROTECTIVE_ORDERS",
                payload_json=json.dumps({"symbol": chain.symbol, "side": chain.side}),
                idempotency_key=f"sync_after_close_partial:{chain_id}:{eid}",
            ))
        return EventProcessorResult(
            new_lifecycle_state=new_state,
            new_be_protection_status=None,
            entry_avg_price=None,
            current_stop_price=None,
            lifecycle_events=[LifecycleEvent(
                trade_chain_id=chain_id,
                event_type="CLOSE_PARTIAL_FILLED",
                source_type="exchange_event",
                source_id=str(eid),
                previous_state=chain.lifecycle_state,
                next_state=new_state,
                payload_json=exchange_event.payload_json,
                idempotency_key=f"close_partial_filled:{chain_id}:{eid}",
            )],
            execution_commands=commands,
            new_open_position_qty=new_open,
            new_closed_position_qty=new_closed,
        )


    def _process_stop_moved_confirmed(
        self, exchange_event: ExchangeEvent, chain: TradeChain
    ) -> EventProcessorResult:
        payload = json.loads(exchange_event.payload_json)
        new_stop_price = float(payload.get("new_stop_price") or 0.0)
        is_breakeven = bool(payload.get("is_breakeven", False))
        eid = exchange_event.exchange_event_id
        chain_id = chain.trade_chain_id
        new_be: BeProtectionStatus | None = "PROTECTED" if is_breakeven else None
        return EventProcessorResult(
            new_lifecycle_state=None,
            new_be_protection_status=new_be,
            entry_avg_price=None,
            current_stop_price=new_stop_price if new_stop_price > 0 else None,
            lifecycle_events=[LifecycleEvent(
                trade_chain_id=chain_id,
                event_type="STOP_MOVE_CONFIRMED",
                source_type="exchange_event",
                source_id=str(eid),
                payload_json=json.dumps({"new_stop_price": new_stop_price, "is_breakeven": is_breakeven}),
                idempotency_key=f"stop_moved:{chain_id}:{eid}",
            )],
            execution_commands=[],
        )

    def _process_pending_entry_cancelled_confirmed(
        self,
        exchange_event: ExchangeEvent,
        chain: TradeChain,
        active_commands: list[ExecutionCommand],
    ) -> EventProcessorResult:
        payload = json.loads(exchange_event.payload_json)
        position_already_open = (chain.open_position_qty or 0.0) > 0.0
        cancelled_order_ids = [str(v) for v in payload.get("cancelled_order_ids", []) if v]
        eid = exchange_event.exchange_event_id
        chain_id = chain.trade_chain_id
        commands: list[ExecutionCommand] = []
        events: list[LifecycleEvent] = []
        new_state: str | None = None

        # ── Marca leg come CANCELLED nel piano ────────────────────────────────
        new_plan_state_json = self._mark_entry_leg_status(
            chain.plan_state_json,
            client_order_ids=cancelled_order_ids,
            command_payload=None,
            new_status="CANCELLED",
        )

        # Fallback: match per sequence (piano ha placeholder ID, ID exchange non corrisponde)
        if new_plan_state_json is None:
            sequence = payload.get("sequence")
            if sequence is not None:
                try:
                    seq_int = int(sequence)
                except (TypeError, ValueError):
                    logger.warning(
                        "PENDING_ENTRY_CANCELLED_CONFIRMED: invalid sequence value %r chain_id=%s",
                        sequence, chain_id,
                    )
                    seq_int = None
                if seq_int is not None:
                    new_plan_state_json = self._mark_entry_leg_status_by_sequence(
                        chain.plan_state_json,
                        sequence=seq_int,
                        new_status="CANCELLED",
                    )

        effective_plan_json = new_plan_state_json or chain.plan_state_json or "{}"

        # ── Deferred BE: emetti BE se tutte le averaging leg sono confermate ──
        # Solo se abbiamo effettivamente aggiornato il piano (evita flag stale su mismatch)
        deferred = _get_be_deferred_flag(effective_plan_json) if new_plan_state_json is not None else False
        if deferred:
            try:
                mp = ManagementPlanConfig.model_validate_json(chain.management_plan_json)
            except Exception:
                mp = ManagementPlanConfig()

            remaining_averaging = ExecutionPlanBuilder.get_pending_averaging_legs(effective_plan_json)
            if not remaining_averaging:
                # Ultima leg confermata: emetti BE con avg price corrente
                be_result = self._build_be_move_command_and_event(chain, eid or 0, mp)
                if be_result is not None:
                    be_cmd, be_event = be_result
                    commands.append(be_cmd)
                    events.append(be_event)
                # Rimuovi il flag dal piano
                effective_plan_json = _clear_be_deferred_flag(effective_plan_json)
                new_plan_state_json = effective_plan_json

        # ── Stato finale chain ─────────────────────────────────────────────────
        if position_already_open:
            if chain.execution_mode not in _ATTACHED_PROTECTION_MODES:
                commands.append(ExecutionCommand(
                    trade_chain_id=chain_id,
                    command_type="SYNC_PROTECTIVE_ORDERS",
                    payload_json=json.dumps({"symbol": chain.symbol, "side": chain.side}),
                    idempotency_key=f"sync_after_cancel:{chain_id}:{eid}",
                ))
        else:
            # Race guard: non finalizzare se ci sono entry commands ancora in volo
            entry_in_flight = [
                c for c in active_commands
                if c.command_type in ("PLACE_ENTRY", "PLACE_ENTRY_WITH_ATTACHED_TPSL")
                and c.status in ("SENT", "ACK")
            ]
            if len(entry_in_flight) > 0:
                # Altre entry ancora in attesa di fill o conferma — non finalizzare
                events.append(LifecycleEvent(
                    trade_chain_id=chain_id,
                    event_type="NOOP_CANCEL_CONFIRMED_POSITION_UNRESOLVED",
                    source_type="exchange_event",
                    source_id=str(eid),
                    idempotency_key=f"noop_cancel_unresolved:{chain_id}:{eid}",
                ))
            else:
                new_state = "CANCELLED"

        events.insert(0, LifecycleEvent(
            trade_chain_id=chain_id,
            event_type="PENDING_ENTRY_CANCELLED",
            source_type="exchange_event",
            source_id=str(eid),
            previous_state=chain.lifecycle_state,
            next_state=new_state,
            payload_json=exchange_event.payload_json,
            idempotency_key=f"pending_cancelled:{chain_id}:{eid}",
        ))

        return EventProcessorResult(
            new_lifecycle_state=new_state,
            new_be_protection_status=None,
            entry_avg_price=None,
            current_stop_price=None,
            lifecycle_events=events,
            execution_commands=commands,
            new_plan_state_json=new_plan_state_json,
        )


__all__ = ["LifecycleEventProcessor", "EventProcessorResult"]
