# src/runtime_v2/execution_gateway/gateway.py
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone

from src.runtime_v2.execution_gateway import client_order_id as coid_mod
from src.runtime_v2.execution_gateway.adapters.base import ExecutionAdapter
from src.runtime_v2.execution_gateway.models import AdapterConfig, ExecutionConfig
from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository
from src.runtime_v2.lifecycle.models import ExecutionCommand

logger = logging.getLogger(__name__)

_CAPABILITY_MAP: dict[str, str] = {
    "PLACE_ENTRY": "place_entry",
    "MOVE_STOP_TO_BREAKEVEN": "move_stop",
    "MOVE_STOP": "move_stop",
    "MOVE_POSITION_STOP": "move_stop",
    "CANCEL_PENDING_ENTRY": "place_entry",
    "CLOSE_PARTIAL": "close_partial",
    "CLOSE_FULL": "close_full",
}

_ROLE_MAP: dict[str, str] = {
    "PLACE_ENTRY": "entry",
    "MOVE_STOP_TO_BREAKEVEN": "sl",
    "MOVE_STOP": "sl",
    "MOVE_POSITION_STOP": "sl",
    "CANCEL_PENDING_ENTRY": "entry",
    "CLOSE_PARTIAL": "exit_partial",
    "CLOSE_FULL": "exit_full",
    "REBUILD_PARTIAL_TPS": "tp",
    "SET_POSITION_TPSL_PARTIAL": "tp",
    "SET_POSITION_TPSL_FULL": "tp",
}

_ENTRY_TYPES: frozenset[str] = frozenset({
    "PLACE_ENTRY",
    "PLACE_ENTRY_WITH_ATTACHED_TPSL",
})

# Commands that execute synchronously and create no pollable exchange order.
# Marked DONE immediately after mark_sent — the sync worker has nothing to poll.
#
# SET_POSITION_TPSL_PARTIAL / SET_POSITION_TPSL_FULL: use Bybit trading_stop API which
# sets a position-level TP — not a standalone order with a queryable orderLinkId.
# The command's job is to SET the TP (done); detecting when it FIRES is handled
# separately by ExchangeEventSyncWorker.run_trade_based_reconciliation().
_FIRE_AND_FORGET: frozenset[str] = frozenset({
    "CANCEL_PENDING_ENTRY",
    "MOVE_STOP_TO_BREAKEVEN",
    "MOVE_STOP",
    "MOVE_POSITION_STOP",
    "REBUILD_PARTIAL_TPS",
    "SET_POSITION_TPSL_PARTIAL",
    "SET_POSITION_TPSL_FULL",
})

# Mappa comando fire-and-forget → evento lifecycle da emettere dopo retCode=0.
# SET_POSITION_TPSL_* esclusi: il loro hit viene rilevato da watchMyTrades/polling.
# CANCEL_PENDING_ENTRY escluso: conferma arriva via PENDING_ENTRY_CANCELLED_CONFIRMED.
_FIRE_AND_FORGET_EVENTS: dict[str, str] = {
    "MOVE_STOP_TO_BREAKEVEN": "STOP_MOVED_CONFIRMED",
    "MOVE_STOP":               "STOP_MOVED_CONFIRMED",
    "MOVE_POSITION_STOP":      "STOP_MOVED_CONFIRMED",
}


def _base36(value: int) -> str:
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    if value == 0:
        return "0"
    digits = []
    while value:
        value, rem = divmod(value, 36)
        digits.append(alphabet[rem])
    return "".join(reversed(digits))


def _command_nonce(cmd: ExecutionCommand) -> str | None:
    if cmd.created_at is None:
        return None
    # Keep orderLinkId compact while making ids unique across local DB resets.
    timestamp_ms = int(cmd.created_at.timestamp() * 1000)
    return _base36(timestamp_ms)


class ExecutionGateway:
    def __init__(
        self,
        config: ExecutionConfig,
        adapter_registry: dict[str, ExecutionAdapter],
        repo: GatewayCommandRepository,
    ) -> None:
        self._config = config
        self._adapters = adapter_registry
        self._repo = repo
        self._leverage_set: set[str] = set()

    def _build_confirmed_event_payload(
        self, cmd: ExecutionCommand, event_type: str, payload: dict
    ) -> dict:
        """Costruisce il payload dell'evento lifecycle per operazioni fire-and-forget sincrone."""
        if event_type == "STOP_MOVED_CONFIRMED":
            return {
                "new_stop_price": payload.get("new_stop_price"),
                "is_breakeven": cmd.command_type == "MOVE_STOP_TO_BREAKEVEN",
                "command_id": cmd.command_id,
            }
        return {"command_id": cmd.command_id}

    def _emit_confirmed_event(
        self, cmd: ExecutionCommand, event_type: str, payload: dict
    ) -> None:
        """INSERT OR IGNORE in ops_exchange_events. Chiave: {event_type}:{chain_id}:{command_id}."""
        idempotency_key = f"{event_type}:{cmd.trade_chain_id}:{cmd.command_id}"
        self._repo.insert_exchange_event(
            trade_chain_id=cmd.trade_chain_id,
            event_type=event_type,
            payload_json=json.dumps(payload),
            idempotency_key=idempotency_key,
        )

    def _review_and_cancel_chain(
        self,
        cmd: ExecutionCommand,
        *,
        reason: str,
        execution_account_id: str | None = None,
    ) -> None:
        """Mark command REVIEW_REQUIRED then cancel chain if no entry command remains active.

        Used for permanent technical failures where retrying identically would produce
        the same result (unknown symbol, missing adapter, capability gap). Distinct from
        intentional safety gates (live_trading_*) where REVIEW_REQUIRED is a hold state
        while the operator fixes config — those must NOT cancel the chain.
        """
        self._repo.mark_review_required(
            cmd.command_id,
            reason=reason,
            execution_account_id=execution_account_id,
        )
        self._repo.cancel_chain_if_all_entries_failed(
            cmd.trade_chain_id, cmd.command_type, reason=reason
        )

    @staticmethod
    def _is_entry_signal_rejection(reason: str, *, command_type: str) -> bool:
        if command_type not in _ENTRY_TYPES:
            return False
        normalized = reason.lower()
        return "30228" in normalized and "delisting" in normalized

    def process(self, cmd: ExecutionCommand, *, account_id: str) -> None:
        routing, adapter_cfg = self._config.resolve_routing(account_id)
        adapter = self._adapters.get(routing.adapter)
        if adapter is None:
            self._review_and_cancel_chain(
                cmd,
                reason=f"adapter_not_found:{routing.adapter}",
                execution_account_id=routing.execution_account_id,
            )
            return

        if adapter_cfg.mode == "live":
            if not adapter_cfg.live_safety.allow_live_trading:
                self._repo.mark_review_required(
                    cmd.command_id,
                    reason="live_trading_not_allowed_in_config",
                    execution_account_id=routing.execution_account_id,
                )
                return
            if os.environ.get("TSB_ALLOW_LIVE_TRADING") != "YES_I_UNDERSTAND":
                self._repo.mark_review_required(
                    cmd.command_id,
                    reason="live_trading_env_gate_not_set",
                    execution_account_id=routing.execution_account_id,
                )
                return

        # Capability check
        cap_field = _CAPABILITY_MAP.get(cmd.command_type)
        if cap_field and not getattr(adapter.get_capabilities(), cap_field, False):
            self._review_and_cancel_chain(
                cmd,
                reason=f"capability_missing:{cap_field}",
                execution_account_id=routing.execution_account_id,
            )
            return

        payload = json.loads(cmd.payload_json)
        symbol = payload.get("symbol", "")

        # ── Resolve deferred MARKET qty ───────────────────────────────────────
        if payload.get("qty_mode") == "deferred_market":
            mark_price = adapter.fetch_mark_price(symbol, routing.execution_account_id)
            if mark_price is None:
                self._review_and_cancel_chain(
                    cmd,
                    reason="deferred_market_no_mark_price",
                    execution_account_id=routing.execution_account_id,
                )
                return
            risk_amount_leg = float(payload["risk_amount"])
            sl_price_val = float(payload["sl_price"])
            risk_dist = abs(mark_price - sl_price_val)
            if risk_dist == 0.0:
                self._review_and_cancel_chain(
                    cmd,
                    reason="deferred_market_zero_risk_distance",
                    execution_account_id=routing.execution_account_id,
                )
                return
            computed_qty = risk_amount_leg / risk_dist
            max_order_qty = adapter.fetch_max_order_qty(symbol, routing.execution_account_id)
            if max_order_qty is not None and computed_qty > max_order_qty:
                self._repo.reject_entry_as_signal(
                    cmd.command_id,
                    reason="computed_qty_exceeds_exchange_max",
                    result_payload={
                        "computed_qty": computed_qty,
                        "max_qty": max_order_qty,
                        "mark_price": mark_price,
                        "sl_price": sl_price_val,
                    },
                )
                return
            payload = {
                k: v for k, v in payload.items()
                if k not in ("qty_mode", "risk_amount", "sl_price")
            }
            payload["qty"] = computed_qty
        if payload.get("tp_qty_mode") == "filled_entry_pct":
            filled_entry_qty = self._repo.get_chain_filled_entry_qty(cmd.trade_chain_id)
            if filled_entry_qty is None or filled_entry_qty <= 0.0:
                self._repo.mark_review_required(
                    cmd.command_id,
                    reason="filled_entry_qty_unavailable_for_partial_tp",
                    execution_account_id=routing.execution_account_id,
                )
                return
            close_pct = float(payload["close_pct"])
            payload = {
                k: v for k, v in payload.items()
                if k not in ("tp_qty_mode", "close_pct")
            }
            payload["tp_size"] = round(filled_entry_qty * close_pct / 100.0, 8)

        # ── Resolve qty for CLOSE_FULL / CLOSE_PARTIAL ───────────────────────
        if cmd.command_type in {"CLOSE_FULL", "CLOSE_PARTIAL"} and "qty" not in payload:
            open_qty = self._repo.get_chain_open_position_qty(cmd.trade_chain_id)
            if cmd.command_type == "CLOSE_PARTIAL":
                if open_qty is None or open_qty <= 0.0:
                    self._repo.mark_review_required(
                        cmd.command_id,
                        reason="open_position_qty_unavailable_for_close",
                        execution_account_id=routing.execution_account_id,
                    )
                    return
                fraction = float(payload.get("fraction", 0.5))
                payload = {k: v for k, v in payload.items() if k != "fraction"}
                payload["qty"] = round(open_qty * fraction, 8)
            else:  # CLOSE_FULL
                live_qty = adapter.get_position_qty(
                    symbol=symbol,
                    side=payload.get("side", ""),
                    execution_account_id=routing.execution_account_id,
                )
                resolved_qty = live_qty if live_qty is not None and live_qty > 0.0 else open_qty
                if resolved_qty is None or resolved_qty <= 0.0:
                    self._repo.mark_review_required(
                        cmd.command_id,
                        reason="open_position_qty_unavailable_for_close",
                        execution_account_id=routing.execution_account_id,
                    )
                    return
                payload["qty"] = resolved_qty

        if (
            cmd.command_type == "REBUILD_PARTIAL_TPS"
            and cmd.command_id is not None
        ):
            self._repo.supersede_rebuild_commands(
                cmd.trade_chain_id,
                exclude_command_id=cmd.command_id,
                statuses=("PENDING",),
            )

        # Set leverage once per account+symbol — leverage comes from payload (set by LifecycleEntryGate)
        leverage = int(payload.get("leverage", 1))
        position_idx = int(payload.get("position_idx", 0))
        leverage_key = f"{routing.execution_account_id}:{symbol}"
        if leverage_key not in self._leverage_set and leverage > 1:
            try:
                adapter.set_leverage(symbol, leverage, routing.execution_account_id,
                                     position_idx=position_idx)
                self._leverage_set.add(leverage_key)
            except Exception as e:
                logger.warning("set_leverage failed for %s: %s", leverage_key, e)

        # Generate client_order_id
        role = _ROLE_MAP.get(cmd.command_type, "entry")
        sequence = payload.get("sequence", payload.get("tp_sequence", 1))
        client_order_id = coid_mod.build(
            trade_chain_id=cmd.trade_chain_id,
            command_id=cmd.command_id,
            role=role,
            sequence=sequence,
            nonce=_command_nonce(cmd),
        )

        # Idempotency check
        existing = adapter.get_order_status(
            client_order_id=client_order_id,
            execution_account_id=routing.execution_account_id,
        )
        if existing is not None and existing.status not in ("CANCELLED", "FAILED"):
            logger.info("command %s already sent, recovering state", cmd.command_id)
            # Must store client_order_id so ExchangeEventSyncWorker can reconcile the fill
            # (it filters WHERE client_order_id IS NOT NULL)
            self._repo.mark_sent(
                cmd.command_id,
                client_order_id=client_order_id,
                adapter=routing.adapter,
                execution_account_id=routing.execution_account_id,
                adapter_order_id=existing.adapter_order_id,
                exchange_order_id=existing.exchange_order_id,
            )
            if cmd.command_type in _FIRE_AND_FORGET:
                event_type = _FIRE_AND_FORGET_EVENTS.get(cmd.command_type)
                if event_type:
                    event_payload = self._build_confirmed_event_payload(cmd, event_type, payload)
                    self._emit_confirmed_event(cmd, event_type, event_payload)
                self._repo.mark_done(cmd.command_id)
                if (
                    cmd.command_type == "REBUILD_PARTIAL_TPS"
                    and cmd.command_id is not None
                ):
                    self._repo.supersede_rebuild_commands(
                        cmd.trade_chain_id,
                        exclude_command_id=cmd.command_id,
                        statuses=("SENT", "ACK", "DONE"),
                    )
            else:
                self._repo.mark_ack(cmd.command_id)
            return

        # Send to adapter
        try:
            result = adapter.place_order(
                command_type=cmd.command_type,
                payload=payload,
                client_order_id=client_order_id,
                execution_account_id=routing.execution_account_id,
                connector=adapter_cfg.connector,
            )
        except Exception as e:
            self._handle_error(
                cmd,
                adapter_cfg,
                e,
                execution_account_id=routing.execution_account_id,
            )
            return

        if not result.success:
            reason = result.reason or result.error or "unknown"
            if self._is_entry_signal_rejection(reason, command_type=cmd.command_type):
                self._repo.reject_entry_as_signal(cmd.command_id, reason=reason)
                return
            self._repo.mark_failed(cmd.command_id, reason=reason)
            self._repo.write_command_failed_tech_log(
                cmd.command_id,
                cmd.trade_chain_id,
                cmd.command_type,
                reason=reason,
                execution_account_id=routing.execution_account_id,
            )
            self._repo.cancel_chain_if_all_entries_failed(
                cmd.trade_chain_id, cmd.command_type, reason=reason
            )
            return

        self._repo.mark_sent(
            cmd.command_id,
            client_order_id=client_order_id,
            adapter=routing.adapter,
            execution_account_id=routing.execution_account_id,
            adapter_order_id=result.adapter_order_id,
            exchange_order_id=result.exchange_order_id,
        )
        if cmd.command_type in _FIRE_AND_FORGET:
            event_type = _FIRE_AND_FORGET_EVENTS.get(cmd.command_type)
            if event_type:
                event_payload = self._build_confirmed_event_payload(cmd, event_type, payload)
                self._emit_confirmed_event(cmd, event_type, event_payload)
            self._repo.mark_done(cmd.command_id)
            if (
                cmd.command_type == "REBUILD_PARTIAL_TPS"
                and cmd.command_id is not None
            ):
                self._repo.supersede_rebuild_commands(
                    cmd.trade_chain_id,
                    exclude_command_id=cmd.command_id,
                    statuses=("SENT", "ACK", "DONE"),
                )

    # Eccezioni che indicano un bug nel codice locale (accesso a chiave/attributo assente,
    # tipo sbagliato, indice fuori range) — mai transitori, retry identico produce lo stesso
    # crash. ValueError escluso: può provenire da parsing di risposte exchange malformate
    # (float("nan"), campo vuoto) che sono errori transitori e devono essere ritentati.
    _PERMANENT_EXC = (KeyError, TypeError, AttributeError, IndexError)

    def _handle_error(
        self,
        cmd: ExecutionCommand,
        adapter_cfg: AdapterConfig,
        exc: Exception,
        *,
        execution_account_id: str | None = None,
    ) -> None:
        error_str = str(exc)
        retry_cfg = adapter_cfg.retry
        current_retry = self._repo.get_retry_count(cmd.command_id)

        is_permanent = isinstance(exc, self._PERMANENT_EXC)
        if is_permanent or current_retry >= retry_cfg.max_attempts:
            if is_permanent:
                logger.error(
                    "permanent error for command %s (no retry): %s: %s",
                    cmd.command_id, type(exc).__name__, error_str,
                )
            self._repo.mark_failed(cmd.command_id, reason=error_str)
            self._repo.write_command_failed_tech_log(
                cmd.command_id,
                cmd.trade_chain_id,
                cmd.command_type,
                reason=error_str,
                execution_account_id=execution_account_id or cmd.execution_account_id,
            )
            cancelled = self._repo.cancel_chain_if_all_entries_failed(
                cmd.trade_chain_id, cmd.command_type, reason=error_str
            )
            if cmd.command_type == "CANCEL_PENDING_ENTRY":
                self._repo.write_cancel_entry_failed_lifecycle(
                    cmd.command_id, cmd.trade_chain_id, attempts=current_retry + 1
                )
            return

        backoff = retry_cfg.backoff_seconds[
            min(current_retry, len(retry_cfg.backoff_seconds) - 1)
        ]
        next_retry = (
            datetime.now(timezone.utc) + timedelta(seconds=backoff)
        ).isoformat()
        self._repo.mark_retry(
            cmd.command_id,
            retry_count=current_retry + 1,
            next_retry_at=next_retry,
        )


__all__ = ["ExecutionGateway"]
