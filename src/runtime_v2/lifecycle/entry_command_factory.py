from __future__ import annotations

import json

from src.runtime_v2.lifecycle.models import ExecutionCommand
from src.runtime_v2.signal_enrichment.models import EnrichedEntryLeg, ManagementPlanConfig
from src.parser_v2.contracts.entities import TakeProfit


class EntryCommandFactory:
    """Builds ExecutionCommand list from enriched entry legs and risk snapshot.

    Unified rule (all 8 cases):
    - first leg (lowest sequence) → PLACE_ENTRY_WITH_ATTACHED_TPSL, tpsl_mode=FULL, SL + final TP attached
    - subsequent legs → PLACE_ENTRY (no attached TPSL)
    Intermediate TPs are NOT emitted here (handled after fills).

    NOTE: "first leg" is determined by sorted sequence order, NOT by sequence == 1.
    TWO_STEP signals may have entries starting at sequence 2, 3, etc.

    NOTE: Payloads from this factory do not include 'execution_strategy'.
    The adapter must route solely on command_type (PLACE_ENTRY_WITH_ATTACHED_TPSL
    vs PLACE_ENTRY), not on payload fields. Verify adapter routing before wiring
    this factory in place of the legacy builders (Task 5).
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
        management_plan: ManagementPlanConfig | None = None,
    ) -> list[ExecutionCommand]:
        # Sort entries by sequence so leg1 is always first regardless of input order
        sorted_entries = sorted(entries, key=lambda leg: leg.sequence)
        management_plan = management_plan or ManagementPlanConfig()

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

        for i, leg in enumerate(sorted_entries):
            snap = snap_by_seq.get(leg.sequence, {})
            is_deferred = snap.get("qty_mode") == "deferred_market"
            is_attached = (i == 0)

            # Common base payload fields
            payload: dict = {
                "symbol": symbol,
                "side": side,
                "sequence": leg.sequence,
                "entry_type": leg.entry_type,
                "price": leg.price.value if leg.entry_type == "LIMIT" and leg.price else None,
                "leverage": leverage,
                "hedge_mode": hedge_mode,
                "position_idx": position_idx,
            }

            # Guard: attached TPSL requires sl_price regardless of qty mode
            if is_attached and sl_price is None:
                raise ValueError("sl_price required for attached TPSL")

            # Quantity fields
            if is_deferred:
                payload["qty_mode"] = "deferred_market"
                risk_raw = snap.get("risk_amount")
                if risk_raw is None:
                    raise ValueError(
                        f"risk_amount missing in risk snapshot for deferred leg sequence={leg.sequence}"
                    )
                payload["risk_amount"] = float(risk_raw)
                if sl_price is None:
                    raise ValueError("sl_price required for deferred legs")
                payload["sl_price"] = sl_price  # always needed for qty at fill time
            else:
                qty_raw = snap.get("qty")
                if qty_raw is None:
                    raise ValueError(
                        f"qty missing in risk snapshot for fixed leg sequence={leg.sequence}"
                    )
                payload["qty"] = float(qty_raw)

            if is_attached:
                # Build attached_tpsl block (sl_price already validated above)
                attached: dict = {
                    "mode": "FULL" if final_tp_price is not None else "SL_ONLY",
                    "stop_loss": sl_price,
                    "sl_trigger_by": management_plan.sl_trigger_by,
                }
                if final_tp_price is not None:
                    attached["take_profit"] = final_tp_price
                    attached["tp_trigger_by"] = management_plan.tp_trigger_by
                payload["attached_tpsl"] = attached

                command = ExecutionCommand(
                    trade_chain_id=0,
                    command_type="PLACE_ENTRY_WITH_ATTACHED_TPSL",
                    status="PENDING",
                    payload_json=json.dumps(payload),
                    idempotency_key=f"place_entry_attached:{enrichment_id}:leg{leg.sequence}",
                )
            else:
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
