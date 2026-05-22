from __future__ import annotations

import json

from src.runtime_v2.lifecycle.models import ExecutionCommand, TradeChain


class PostFillProtectionRebuilder:
    """Generates intermediate TP commands after an entry fill for multi-TP plans."""

    def build_after_fill(
        self,
        chain: TradeChain,
        filled_entry_qty: float,
        exchange_event_id: int,
    ) -> list[ExecutionCommand]:
        try:
            plan = json.loads(chain.plan_state_json or "{}")
        except Exception:
            return []

        if plan.get("rebuild_policy", "NONE") != "ON_EACH_ENTRY_FILL":
            return []

        intermediate_tps: list[float] = plan.get("intermediate_tps", [])
        if not intermediate_tps:
            return []

        n_total_tps = len(intermediate_tps) + 1
        chain_id = chain.trade_chain_id
        commands: list[ExecutionCommand] = []

        for i, tp_price in enumerate(intermediate_tps):
            close_pct = 100.0 / n_total_tps
            tp_qty = round(filled_entry_qty * close_pct / 100.0, 8)
            payload = {
                "symbol": chain.symbol,
                "side": chain.side,
                "tp_sequence": i + 1,
                "take_profit": tp_price,
                "tp_size": tp_qty,
                "tp_order_type": "Limit",
                "tp_limit_price": tp_price,
                "tp_trigger_by": "MarkPrice",
                "preserve_sl": True,
                "supersedes_previous": True,
            }
            commands.append(ExecutionCommand(
                trade_chain_id=chain_id,
                command_type="SET_POSITION_TPSL_PARTIAL",
                payload_json=json.dumps(payload),
                idempotency_key=f"tp_partial_fill:{chain_id}:{exchange_event_id}:tp{i + 1}",
            ))

        return commands


__all__ = ["PostFillProtectionRebuilder"]
