from __future__ import annotations

import json

from src.runtime_v2.lifecycle.models import ExecutionCommand
from src.runtime_v2.signal_enrichment.models import EnrichedEntryLeg
from src.parser_v2.contracts.entities import TakeProfit


class EntryCommandFactory:
    """Builds ExecutionCommand list from enriched entry legs and risk snapshot.

    Unified rule (all 8 cases):
    - leg sequence == 1 → PLACE_ENTRY_WITH_ATTACHED_TPSL, tpsl_mode=FULL, SL + final TP attached
    - leg sequence >  1 → PLACE_ENTRY (no attached TPSL)
    Intermediate TPs are NOT emitted here (handled after fills).
    """

    def build_entry_commands(
        self,
        *,
        enrichment_id: int,
        symbol: str,
        side: str,
        entries: list[EnrichedEntryLeg],
        take_profits: list[TakeProfit],
        sl_price: float | None,
        leverage: int,
        hedge_mode: bool,
        position_idx: int,
        risk_snapshot: dict,
    ) -> list[ExecutionCommand]:
        # Sort entries by sequence so leg1 is always first regardless of input order
        sorted_entries = sorted(entries, key=lambda leg: leg.sequence)

        # Build snap lookup: sequence → snap dict
        snap_by_seq: dict[int, dict] = {
            s["sequence"]: s for s in risk_snapshot.get("legs", [])
        }

        # Determine final TP price (highest sequence TP)
        final_tp_price: float | None = None
        if take_profits:
            final_tp = max(take_profits, key=lambda tp: tp.sequence)
            final_tp_price = final_tp.price.value if final_tp.price else None

        commands: list[ExecutionCommand] = []

        for leg in sorted_entries:
            snap = snap_by_seq.get(leg.sequence, {})
            is_deferred = snap.get("qty_mode") == "deferred_market"
            is_attached = leg.sequence == 1

            # Common base payload fields
            payload: dict = {
                "symbol": symbol,
                "side": side,
                "entry_type": leg.entry_type,
                "price": leg.price.value if leg.entry_type == "LIMIT" and leg.price else None,
                "leverage": leverage,
                "hedge_mode": hedge_mode,
                "position_idx": position_idx,
            }

            # Quantity fields
            if is_deferred:
                payload["qty_mode"] = "deferred_market"
                payload["risk_amount"] = snap.get("risk_amount")
                if is_attached:
                    payload["sl_price"] = sl_price
            else:
                payload["qty"] = snap.get("qty")

            if is_attached:
                # Build attached_tpsl block
                attached: dict = {
                    "mode": "FULL",
                    "stop_loss": sl_price,
                    "sl_trigger_by": "MarkPrice",
                }
                if final_tp_price is not None:
                    attached["take_profit"] = final_tp_price
                    attached["tp_trigger_by"] = "MarkPrice"
                payload["attached_tpsl"] = attached

                command = ExecutionCommand(
                    trade_chain_id=0,
                    command_type="PLACE_ENTRY_WITH_ATTACHED_TPSL",
                    status="PENDING",
                    payload_json=json.dumps(payload),
                    idempotency_key=f"place_entry_attached:{enrichment_id}:leg{leg.sequence}",
                )
            else:
                payload["sequence"] = leg.sequence

                command = ExecutionCommand(
                    trade_chain_id=0,
                    command_type="PLACE_ENTRY",
                    status="PENDING",
                    payload_json=json.dumps(payload),
                    idempotency_key=f"place_entry:{enrichment_id}:leg{leg.sequence}",
                )

            commands.append(command)

        return commands


__all__ = ["EntryCommandFactory"]
