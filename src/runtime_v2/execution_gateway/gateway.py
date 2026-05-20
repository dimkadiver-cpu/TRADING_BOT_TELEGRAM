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
    "PLACE_PROTECTIVE_STOP": "protective_stop_native",
    "PLACE_TAKE_PROFIT": "take_profit_native",
    "MOVE_STOP_TO_BREAKEVEN": "move_stop",
    "MOVE_STOP": "move_stop",
    "CANCEL_PENDING_ENTRY": "place_entry",
    "CLOSE_PARTIAL": "close_partial",
    "CLOSE_FULL": "close_full",
    "SYNC_PROTECTIVE_ORDERS": "sync_protective_orders",
}

_ROLE_MAP: dict[str, str] = {
    "PLACE_ENTRY": "entry",
    "PLACE_PROTECTIVE_STOP": "sl",
    "PLACE_TAKE_PROFIT": "tp",
    "MOVE_STOP_TO_BREAKEVEN": "sl",
    "MOVE_STOP": "sl",
    "CANCEL_PENDING_ENTRY": "entry",
    "CLOSE_PARTIAL": "exit_partial",
    "CLOSE_FULL": "exit_full",
    "SYNC_PROTECTIVE_ORDERS": "sync",
}


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

    def process(self, cmd: ExecutionCommand, *, account_id: str) -> None:
        routing, adapter_cfg = self._config.resolve_routing(account_id)
        adapter = self._adapters.get(routing.adapter)
        if adapter is None:
            self._repo.mark_review_required(
                cmd.command_id, reason=f"adapter_not_found:{routing.adapter}"
            )
            return

        if adapter_cfg.mode == "live":
            if not adapter_cfg.live_safety.allow_live_trading:
                self._repo.mark_review_required(
                    cmd.command_id, reason="live_trading_not_allowed_in_config"
                )
                return
            if os.environ.get("TSB_ALLOW_LIVE_TRADING") != "YES_I_UNDERSTAND":
                self._repo.mark_review_required(
                    cmd.command_id, reason="live_trading_env_gate_not_set"
                )
                return

        # Capability check
        cap_field = _CAPABILITY_MAP.get(cmd.command_type)
        if cap_field and not getattr(adapter.get_capabilities(), cap_field, False):
            self._repo.mark_review_required(
                cmd.command_id,
                reason=f"capability_missing:{cap_field}",
            )
            return

        payload = json.loads(cmd.payload_json)
        symbol = payload.get("symbol", "")

        # Set leverage once per account+symbol
        leverage_key = f"{routing.execution_account_id}:{symbol}"
        if leverage_key not in self._leverage_set and adapter_cfg.leverage > 1:
            try:
                adapter.set_leverage(symbol, adapter_cfg.leverage, routing.execution_account_id)
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
        )

        # Idempotency check
        existing = adapter.get_order_status(
            client_order_id=client_order_id,
            execution_account_id=routing.execution_account_id,
        )
        if existing is not None:
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
            self._handle_error(cmd, adapter_cfg, str(e))
            return

        if not result.success:
            self._repo.mark_failed(
                cmd.command_id, reason=result.reason or result.error or "unknown"
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

    def _handle_error(self, cmd: ExecutionCommand, adapter_cfg: AdapterConfig, error_str: str) -> None:
        retry_cfg = adapter_cfg.retry
        current_retry = self._repo.get_retry_count(cmd.command_id)

        if current_retry >= retry_cfg.max_attempts:
            self._repo.mark_failed(cmd.command_id, reason=error_str)
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
