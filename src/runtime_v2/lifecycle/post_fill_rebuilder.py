from __future__ import annotations

import json

from src.runtime_v2.lifecycle.models import ExecutionCommand, TradeChain


class PostFillProtectionRebuilder:
    """Generates intermediate TP commands after an entry fill for multi-TP plans."""

    @staticmethod
    def _resolve_position_context(chain: TradeChain) -> tuple[bool, int]:
        try:
            risk_snapshot = json.loads(chain.risk_snapshot_json or "{}")
            hedge_mode = bool(risk_snapshot.get("hedge_mode", False))
        except Exception:
            hedge_mode = False
        position_idx = 0 if not hedge_mode else (1 if chain.side == "LONG" else 2)
        return hedge_mode, position_idx

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
        hedge_mode, position_idx = self._resolve_position_context(chain)
        tp_qty = round(filled_entry_qty * (100.0 / n_total_tps) / 100.0, 8)
        payload = {
            "symbol": chain.symbol,
            "side": chain.side,
            "hedge_mode": hedge_mode,
            "position_idx": position_idx,
            "preserve_sl": True,
            "preserve_full_tp": True,
            "tps": [
                {
                    "sequence": i + 1,
                    "price": tp_price,
                    "qty": tp_qty,
                    "order_type": "Limit",
                    "limit_price": tp_price,
                    "trigger_by": "MarkPrice",
                }
                for i, tp_price in enumerate(intermediate_tps)
            ],
        }
        return [ExecutionCommand(
            trade_chain_id=chain_id,
            command_type="REBUILD_PARTIAL_TPS",
            payload_json=json.dumps(payload),
            idempotency_key=f"rebuild_partial_tps:{chain_id}:{exchange_event_id}",
        )]


__all__ = ["PostFillProtectionRebuilder"]
