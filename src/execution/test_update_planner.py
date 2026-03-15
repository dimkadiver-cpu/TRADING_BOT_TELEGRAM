from __future__ import annotations

import unittest

from src.execution.update_planner import build_update_plan


class UpdatePlannerTests(unittest.TestCase):
    def test_move_stop_and_cancel_pending(self) -> None:
        plan = build_update_plan(
            {
                "message_type": "UPDATE",
                "actions": ["ACT_MOVE_STOP_LOSS", "ACT_CANCEL_ALL_PENDING_ENTRIES"],
                "entities": {"new_stop_level": "ENTRY", "cancel_scope": "ALL_PENDING_ENTRIES"},
                "target_refs": [],
            }
        )
        self.assertIn({"field": "stop_loss", "op": "SET_FROM_ENTRY", "value": "ENTRY"}, plan.position_updates)
        self.assertIn(
            {"selector": "ALL_PENDING_ENTRIES", "field": "status", "op": "SET", "value": "CANCELLED"},
            plan.order_updates,
        )
        self.assertIn("STOP_MOVED_TO_BE", plan.events)
        self.assertIn("PENDING_ENTRIES_CANCELLED", plan.events)
        self.assertIn("update_plan_missing_target_refs", plan.warnings)

    def test_update_status_and_results(self) -> None:
        plan = build_update_plan(
            {
                "message_type": "UPDATE",
                "actions": [
                    "ACT_CLOSE_PARTIAL",
                    "ACT_CLOSE_FULL",
                    "ACT_MARK_TP_HIT",
                    "ACT_MARK_STOP_HIT",
                    "ACT_MARK_ORDER_FILLED",
                    "ACT_MARK_SIGNAL_INVALID",
                    "ACT_MARK_POSITION_CLOSED",
                    "ACT_ATTACH_RESULT",
                ],
                "entities": {
                    "close_scope": "PARTIAL",
                    "close_fraction": 0.5,
                    "hit_target": "TP1",
                    "fill_state": "FILLED",
                    "result_mode": "R_MULTIPLE",
                },
                "reported_results": [{"symbol": "BTCUSDT", "r_multiple": 1.2}],
                "target_refs": [123],
            }
        )
        self.assertIn({"field": "close_scope", "op": "SET", "value": "PARTIAL", "close_fraction": 0.5}, plan.position_updates)
        self.assertIn({"field": "status", "op": "SET", "value": "CLOSED_CANDIDATE"}, plan.position_updates)
        self.assertIn({"field": "target_hit", "op": "MARK", "value": "TP1"}, plan.result_updates)
        self.assertIn({"field": "stop_hit", "op": "SET", "value": True}, plan.position_updates)
        self.assertIn({"field": "status", "op": "SET", "value": "FILLED"}, plan.order_updates)
        self.assertIn({"field": "status", "op": "SET", "value": "INVALID"}, plan.signal_updates)
        self.assertIn({"field": "status", "op": "SET", "value": "CLOSED"}, plan.position_updates)
        self.assertIn(
            {
                "field": "reported_results",
                "op": "ATTACH",
                "value": [{"symbol": "BTCUSDT", "r_multiple": 1.2}],
                "mode": "R_MULTIPLE",
            },
            plan.result_updates,
        )


if __name__ == "__main__":
    unittest.main()
