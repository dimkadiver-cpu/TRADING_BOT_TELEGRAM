from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest

from src.core.migrations import apply_migrations
from src.core.timeutils import utc_now_iso
from src.execution.update_applier import apply_update_plan
from src.execution.update_planner import build_update_plan
from src.parser.pipeline import MinimalParserPipeline, ParserInput


class TraderAReplayDbTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, path = tempfile.mkstemp(prefix="tsb_parser_test_a_", suffix=".sqlite3")
        os.close(fd)
        self.db_path = path
        apply_migrations(self.db_path, "db/migrations")
        self.pipeline = MinimalParserPipeline(trader_aliases={"A": "A", "trader_a": "trader_a", "TA": "TA", "TB": "TB"})
        self._seed_target_attempt()
        self._raw_message_id = 12000

    def tearDown(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(self.db_path + suffix)
            except (FileNotFoundError, PermissionError):
                pass

    def _seed_target_attempt(self) -> None:
        now = utc_now_iso()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO signals(
                  attempt_key, env, channel_id, root_telegram_id, trader_id, trader_prefix,
                  trader_signal_id, symbol, side, entry_json, sl, tp_json, status, confidence, raw_text, created_at, updated_at
                ) VALUES ('atkA500', 'T', '-100123', '500', 'A', 'A', 500, 'BTCUSDT', 'BUY', ?, 95000.0, ?, 'ACTIVE', 0.9, 'seed', ?, ?)
                """,
                (json.dumps([100000.0]), json.dumps([102000.0]), now, now),
            )
            conn.execute(
                """
                INSERT INTO trades(
                  env, attempt_key, trader_id, symbol, side, execution_mode, state,
                  entry_zone_policy, non_chase_policy, opened_at, meta_json, created_at, updated_at
                ) VALUES ('T', 'atkA500', 'A', 'BTCUSDT', 'BUY', 'PAPER', 'OPEN', 'Z1', 'NI3', ?, '{}', ?, ?)
                """,
                (now, now, now),
            )
            conn.execute(
                """
                INSERT INTO orders(
                  env, attempt_key, symbol, side, order_type, purpose, idx, qty, price, trigger_price,
                  reduce_only, client_order_id, exchange_order_id, status, created_at, updated_at
                ) VALUES
                ('T', 'atkA500', 'BTCUSDT', 'BUY', 'LIMIT', 'ENTRY', 0, 1.0, 100000.0, NULL, 0, 'coid-a-entry', NULL, 'NEW', ?, ?),
                ('T', 'atkA500', 'BTCUSDT', 'SELL', 'STOP', 'SL', 0, 1.0, NULL, 95000.0, 1, 'coid-a-sl', NULL, 'NEW', ?, ?),
                ('T', 'atkA500', 'BTCUSDT', 'SELL', 'LIMIT', 'TP', 0, 1.0, 102000.0, NULL, 1, 'coid-a-tp', NULL, 'NEW', ?, ?)
                """,
                (now, now, now, now, now, now),
            )
            conn.execute(
                """
                INSERT INTO positions(
                  env, symbol, side, size, entry_price, mark_price, unrealized_pnl, realized_pnl, leverage, margin_mode, updated_at
                ) VALUES ('T', 'BTCUSDT', 'BUY', 1.0, 100000.0, 100000.0, 0.0, 0.0, 10.0, 'cross', ?)
                """,
                (now,),
            )
            conn.commit()

    def _run_e2e(
        self,
        *,
        text: str,
        resolved_trader_id: str = "A",
        linkage_method: str | None = None,
        linkage_reference_id: int | None = None,
    ) -> tuple[dict[str, object], object, object]:
        self._raw_message_id += 1
        parse_record = self.pipeline.parse(
            ParserInput(
                raw_message_id=self._raw_message_id,
                raw_text=text,
                eligibility_status="ACQUIRED_ELIGIBLE",
                eligibility_reason="eligible",
                resolved_trader_id=resolved_trader_id,
                trader_resolution_method="tag",
                linkage_method=linkage_method,
                source_chat_id="-100123",
                source_message_id=self._raw_message_id,
                linkage_reference_id=linkage_reference_id,
            )
        )
        normalized = json.loads(parse_record.parse_result_normalized_json or "{}")
        plan = build_update_plan(normalized)
        apply_result = apply_update_plan(
            plan,
            self.db_path,
            env="T",
            channel_id="-100123",
            telegram_msg_id=str(self._raw_message_id),
            trader_id="A",
            trader_prefix="A",
        )
        return normalized, plan, apply_result

    def test_replay_parser_to_db_for_key_trader_a_cases(self) -> None:
        new_signal, plan_new, _ = self._run_e2e(text="BTCUSDT long entry 100000 sl 99000 tp1 101000", resolved_trader_id="A")
        self.assertEqual(new_signal.get("message_type"), "NEW_SIGNAL")
        self.assertEqual(plan_new.actions, [])

        setup_incomplete, plan_inc, _ = self._run_e2e(text="BTCUSDT long entry only, sl later", resolved_trader_id="A")
        self.assertEqual(setup_incomplete.get("message_type"), "SETUP_INCOMPLETE")
        self.assertEqual(plan_inc.actions, [])

        update_reply, _, _ = self._run_e2e(
            text="move stop to be and cancel pending orders",
            resolved_trader_id="A",
            linkage_method="direct_reply",
            linkage_reference_id=500,
        )
        self.assertEqual(update_reply.get("message_type"), "UPDATE")
        self.assertIn("U_MOVE_STOP_TO_BE", update_reply.get("intents", []))
        self.assertIn("U_CANCEL_PENDING_ORDERS", update_reply.get("intents", []))
        self.assertIn("ACT_MOVE_STOP_LOSS", update_reply.get("actions", []))
        self.assertIn("ACT_CANCEL_ALL_PENDING_ENTRIES", update_reply.get("actions", []))

        update_link, _, _ = self._run_e2e(
            text="close all positions https://t.me/c/123/500",
            resolved_trader_id="trader_a",
            linkage_method="explicit_link",
            linkage_reference_id=None,
        )
        self.assertIn(500, update_link.get("target_refs", []))
        self.assertIn("U_CLOSE_FULL", update_link.get("intents", []))

        tp_hit, _, _ = self._run_e2e(
            text="tp1 hit",
            resolved_trader_id="A",
            linkage_method="direct_reply",
            linkage_reference_id=500,
        )
        self.assertIn("U_TP_HIT", tp_hit.get("intents", []))

        stop_hit, _, _ = self._run_e2e(
            text="stopped out",
            resolved_trader_id="A",
            linkage_method="direct_reply",
            linkage_reference_id=500,
        )
        self.assertIn("U_STOP_HIT", stop_hit.get("intents", []))

        mark_filled, _, _ = self._run_e2e(
            text="entry filled",
            resolved_trader_id="A",
            linkage_method="direct_reply",
            linkage_reference_id=500,
        )
        self.assertIn("U_MARK_FILLED", mark_filled.get("intents", []))

        final_result, _, _ = self._run_e2e(
            text="Final result BTCUSDT - 1.2R",
            resolved_trader_id="A",
            linkage_method="direct_reply",
            linkage_reference_id=500,
        )
        self.assertIn("U_REPORT_FINAL_RESULT", final_result.get("intents", []))
        self.assertEqual(final_result.get("reported_results"), [{"symbol": "BTCUSDT", "r_multiple": 1.2}])

        with sqlite3.connect(self.db_path) as conn:
            signal_sl = conn.execute("SELECT sl FROM signals WHERE attempt_key='atkA500'").fetchone()[0]
            orders = dict(conn.execute("SELECT purpose, status FROM orders WHERE attempt_key='atkA500'").fetchall())
            trade_state, close_reason, meta_raw = conn.execute(
                "SELECT state, close_reason, meta_json FROM trades WHERE attempt_key='atkA500'"
            ).fetchone()
            position_size = conn.execute("SELECT size FROM positions WHERE env='T' AND symbol='BTCUSDT'").fetchone()[0]
        self.assertEqual(signal_sl, 100000.0)
        self.assertEqual(orders.get("ENTRY"), "FILLED")
        self.assertEqual(orders.get("TP"), "FILLED")
        self.assertEqual(orders.get("SL"), "FILLED")
        self.assertEqual(trade_state, "CLOSED")
        self.assertIn(close_reason, ("FULL_CLOSE_REQUESTED", "STOP_HIT", "POSITION_CLOSED"))
        self.assertEqual(position_size, 0.0)
        self.assertEqual(json.loads(meta_raw).get("reported_results"), [{"symbol": "BTCUSDT", "r_multiple": 1.2}])

    def test_ambiguous_case_does_not_apply_destructive_updates(self) -> None:
        before = self._snapshot_state()
        normalized, plan, apply_result = self._run_e2e(
            text="maybe close maybe move later",
            resolved_trader_id="A",
            linkage_method=None,
            linkage_reference_id=None,
        )
        self.assertEqual(normalized.get("message_type"), "UNCLASSIFIED")
        self.assertEqual(normalized.get("intents"), [])
        self.assertEqual(plan.actions, [])
        self.assertFalse(apply_result.errors)
        self.assertEqual(before, self._snapshot_state())

    def _snapshot_state(self) -> dict[str, object]:
        with sqlite3.connect(self.db_path) as conn:
            signal = conn.execute("SELECT status, sl FROM signals WHERE attempt_key='atkA500'").fetchone()
            orders = conn.execute(
                "SELECT purpose, status FROM orders WHERE attempt_key='atkA500' ORDER BY purpose"
            ).fetchall()
            trade = conn.execute("SELECT state, close_reason, meta_json FROM trades WHERE attempt_key='atkA500'").fetchone()
            position = conn.execute("SELECT size FROM positions WHERE env='T' AND symbol='BTCUSDT'").fetchone()
        return {"signal": signal, "orders": orders, "trade": trade, "position": position}


if __name__ == "__main__":
    unittest.main()
