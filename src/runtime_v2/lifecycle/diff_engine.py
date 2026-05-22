from __future__ import annotations

import json


class ExecutionPlanDiffEngine:
    """Compares current and target execution plans without modifying filled legs."""

    def diff(
        self,
        current_plan_json: str,
        target_plan_json: str,
        *,
        risk_remaining: float,
        sl_price: float,
        current_market_price: float | None = None,
        consolidation_policy: str = "keep_remaining",
    ) -> list[dict]:
        current = json.loads(current_plan_json)
        target = json.loads(target_plan_json)

        current_by_seq: dict[int, dict] = {
            leg["sequence"]: leg for leg in current.get("legs", [])
        }
        target_by_seq: dict[int, dict] = {
            leg["sequence"]: leg for leg in target.get("legs", [])
        }

        actions: list[dict] = []

        for seq, target_leg in sorted(target_by_seq.items()):
            current_leg = current_by_seq.get(seq)

            if current_leg and current_leg.get("status") == "FILLED":
                actions.append({
                    "action": "keep_entry_leg",
                    "sequence": seq,
                    "reason": "already_filled",
                })
                continue

            if current_leg is None:
                actions.append({
                    "action": "add_entry_leg",
                    "sequence": seq,
                    "leg": target_leg,
                })
                continue

            legs_differ = (
                current_leg.get("entry_type") != target_leg.get("entry_type")
                or current_leg.get("price") != target_leg.get("price")
            )
            if not legs_differ:
                actions.append({"action": "keep_entry_leg", "sequence": seq})
                continue

            ref_price = target_leg.get("price") or current_market_price
            new_qty: float | None = None
            if ref_price is not None:
                risk_distance = abs(float(ref_price) - float(sl_price))
                if risk_distance == 0:
                    raise ValueError(f"zero_risk_distance for leg sequence={seq}")
                leg_risk = float(target_leg.get("risk_budget", risk_remaining) or 0.0)
                new_qty = leg_risk / risk_distance

            actions.append({
                "action": "cancel_pending_entry",
                "sequence": seq,
                "client_order_id": current_leg.get("client_order_id"),
            })
            actions.append({
                "action": "replace_entry_leg",
                "sequence": seq,
                "old_client_order_id": current_leg.get("client_order_id"),
                "new_entry_type": target_leg["entry_type"],
                "new_price": target_leg.get("price"),
                "new_qty": new_qty,
            })

        for seq, current_leg in sorted(current_by_seq.items()):
            if seq not in target_by_seq and current_leg.get("status") == "PENDING":
                actions.append({
                    "action": "cancel_pending_entry",
                    "sequence": seq,
                    "client_order_id": current_leg.get("client_order_id"),
                })

        return actions


__all__ = ["ExecutionPlanDiffEngine"]
