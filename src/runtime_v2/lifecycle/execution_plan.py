from __future__ import annotations

import json
from typing import Literal

from src.parser_v2.contracts.entities import TakeProfit
from src.runtime_v2.signal_enrichment.models import EnrichedEntryLeg

RebuildPolicy = Literal["NONE", "ON_EACH_ENTRY_FILL"]
ProtectionPolicy = Literal["TPSL_ATTACHED_FIRST_LEG"]
RiskPolicy = Literal["REBALANCE_REMAINING_RISK_ON_REPLAN"]


class ExecutionPlanBuilder:
    """Pure-logic builder that serialises a full execution plan to JSON."""

    @staticmethod
    def build(
        enrichment_id: int,
        entries: list[EnrichedEntryLeg],
        take_profits: list[TakeProfit],
        risk_snapshot: dict,
    ) -> str:
        """Return plan_state_json string."""
        tp_count = len(take_profits)

        # ── rebuild / TP policy ───────────────────────────────────────────────
        if tp_count == 1:
            rebuild_policy: RebuildPolicy = "NONE"
            final_tp = take_profits[0].price.value
            intermediate_tps: list[float] = []
        else:
            rebuild_policy = "ON_EACH_ENTRY_FILL"
            final_tp = take_profits[-1].price.value
            intermediate_tps = [tp.price.value for tp in take_profits[:-1]]

        # ── legs ──────────────────────────────────────────────────────────────
        legs_snap: list[dict] = risk_snapshot.get("legs", [])
        # build a lookup by sequence for the risk snapshot
        snap_by_seq: dict[int, dict] = {s["sequence"]: s for s in legs_snap}

        legs_out: list[dict] = []
        for leg in entries:
            snap = snap_by_seq.get(leg.sequence, {})
            if leg.sequence == 1:
                client_order_id = f"place_entry_attached:{enrichment_id}:leg{leg.sequence}"
            else:
                client_order_id = f"place_entry:{enrichment_id}:leg{leg.sequence}"

            legs_out.append({
                "leg_id": f"leg_{leg.sequence}",
                "sequence": leg.sequence,
                "entry_type": leg.entry_type if isinstance(leg.entry_type, str) else leg.entry_type.value,
                "price": leg.price.value if leg.price is not None else None,
                "risk_budget": snap.get("risk_amount"),
                "qty": snap.get("qty"),
                "qty_mode": snap.get("qty_mode", "fixed"),
                "weight": snap.get("weight", leg.weight),
                "status": "PENDING",
                "client_order_id": client_order_id,
            })

        plan = {
            "plan_version": 1,
            "protection_policy": "TPSL_ATTACHED_FIRST_LEG",
            "rebuild_policy": rebuild_policy,
            "risk_policy": "REBALANCE_REMAINING_RISK_ON_REPLAN",
            "stop_loss": risk_snapshot.get("sl_price"),
            "final_tp": final_tp,
            "intermediate_tps": intermediate_tps,
            "legs": legs_out,
        }

        return json.dumps(plan)

    # ── helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def update_leg_status(
        plan_state_json: str,
        leg_id: str,
        new_status: str,
        *,
        client_order_id: str | None = None,
    ) -> str:
        """Return updated plan_state_json with the given leg's status changed."""
        plan = json.loads(plan_state_json)
        for leg in plan.get("legs", []):
            if leg.get("leg_id") == leg_id:
                leg["status"] = new_status
                if client_order_id is not None:
                    leg["client_order_id"] = client_order_id
                break
        return json.dumps(plan)

    @staticmethod
    def get_rebuild_policy(plan_state_json: str) -> RebuildPolicy:
        """Return the rebuild_policy from the plan."""
        plan = json.loads(plan_state_json)
        return plan["rebuild_policy"]

    @staticmethod
    def get_pending_legs(plan_state_json: str) -> list[dict]:
        """Return all legs whose status is PENDING."""
        plan = json.loads(plan_state_json)
        return [leg for leg in plan.get("legs", []) if leg.get("status") == "PENDING"]


__all__ = ["ExecutionPlanBuilder", "RebuildPolicy"]
