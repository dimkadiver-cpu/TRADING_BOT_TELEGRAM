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


class UpdateApplierTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, path = tempfile.mkstemp(prefix="tsb_update_applier_", suffix=".sqlite3")
        os.close(fd)
        self.db_path = path
        apply_migrations(self.db_path, "db/migrations")
        self._seed()

    def tearDown(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(self.db_path + suffix)
            except (FileNotFoundError, PermissionError):
                pass

    def _seed(self) -> None:
        now = utc_now_iso()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO signals(
                  attempt_key, env, channel_id, root_telegram_id, trader_id, trader_prefix,
                  trader_signal_id, symbol, side, entry_json, sl, tp_json, status, confidence, raw_text, created_at, updated_at
                ) VALUES ('atk1', 'T', '-1001', '101', 'TA', 'TA', 101, 'BTCUSDT', 'BUY', ?, 90.0, ?, 'ACTIVE', 0.9, 'seed', ?, ?)
                """,
                (json.dumps([100.0]), json.dumps([110.0]), now, now),
            )
            conn.execute(
                """
                INSERT INTO signals(
                  attempt_key, env, channel_id, root_telegram_id, trader_id, trader_prefix,
                  trader_signal_id, symbol, side, entry_json, sl, tp_json, status, confidence, raw_text, created_at, updated_at
                ) VALUES ('atk2', 'T', '-1001', '102', 'TA', 'TA', 102, 'ETHUSDT', 'SELL', ?, 2300.0, ?, 'ACTIVE', 0.9, 'seed', ?, ?)
                """,
                (json.dumps([2200.0]), json.dumps([2100.0]), now, now),
            )
            conn.execute(
                """
                INSERT INTO trades(
                  env, attempt_key, trader_id, symbol, side, execution_mode, state,
                  entry_zone_policy, non_chase_policy, opened_at, meta_json, created_at, updated_at
                ) VALUES ('T', 'atk1', 'TA', 'BTCUSDT', 'BUY', 'PAPER', 'OPEN', 'Z1', 'NI3', ?, '{}', ?, ?)
                """,
                (now, now, now),
            )
            conn.execute(
                """
                INSERT INTO trades(
                  env, attempt_key, trader_id, symbol, side, execution_mode, state,
                  entry_zone_policy, non_chase_policy, opened_at, meta_json, created_at, updated_at
                ) VALUES ('T', 'atk2', 'TA', 'ETHUSDT', 'SELL', 'PAPER', 'OPEN', 'Z1', 'NI3', ?, '{}', ?, ?)
                """,
                (now, now, now),
            )
            conn.execute(
                """
                INSERT INTO orders(
                  env, attempt_key, symbol, side, order_type, purpose, idx, qty, price, trigger_price,
                  reduce_only, client_order_id, exchange_order_id, status, created_at, updated_at
                ) VALUES
                ('T', 'atk1', 'BTCUSDT', 'BUY', 'LIMIT', 'ENTRY', 0, 1.0, 100.0, NULL, 0, 'coid-entry-1', NULL, 'NEW', ?, ?),
                ('T', 'atk1', 'BTCUSDT', 'SELL', 'STOP', 'SL', 0, 1.0, NULL, 90.0, 1, 'coid-sl-1', NULL, 'NEW', ?, ?),
                ('T', 'atk1', 'BTCUSDT', 'SELL', 'LIMIT', 'TP', 0, 1.0, 110.0, NULL, 1, 'coid-tp-1', NULL, 'NEW', ?, ?),
                ('T', 'atk2', 'ETHUSDT', 'SELL', 'LIMIT', 'ENTRY', 0, 1.0, 2200.0, NULL, 0, 'coid-entry-2', NULL, 'NEW', ?, ?),
                ('T', 'atk2', 'ETHUSDT', 'BUY', 'STOP', 'SL', 0, 1.0, NULL, 2300.0, 1, 'coid-sl-2', NULL, 'NEW', ?, ?),
                ('T', 'atk2', 'ETHUSDT', 'BUY', 'LIMIT', 'TP', 0, 1.0, 2100.0, NULL, 1, 'coid-tp-2', NULL, 'NEW', ?, ?)
                """,
                (now, now, now, now, now, now, now, now, now, now, now, now),
            )
            conn.execute(
                """
                INSERT INTO positions(
                  env, symbol, side, size, entry_price, mark_price, unrealized_pnl, realized_pnl, leverage, margin_mode, updated_at
                ) VALUES ('T', 'BTCUSDT', 'BUY', 1.0, 100.0, 100.0, 0.0, 0.0, 10.0, 'cross', ?)
                """,
                (now,),
            )
            conn.commit()

    def test_move_stop_to_be_applies(self) -> None:
        plan = build_update_plan(
            {
                "message_type": "UPDATE",
                "actions": ["ACT_MOVE_STOP_LOSS"],
                "entities": {"new_stop_level": "ENTRY"},
                "target_refs": [101],
            }
        )
        result = apply_update_plan(plan, self.db_path, channel_id="-1001", telegram_msg_id="201")
        self.assertFalse(result.errors)
        self.assertTrue(result.applied_position_updates)

        with sqlite3.connect(self.db_path) as conn:
            sl = conn.execute("SELECT sl FROM signals WHERE attempt_key='atk1'").fetchone()[0]
            sl_trigger = conn.execute(
                "SELECT trigger_price FROM orders WHERE attempt_key='atk1' AND purpose='SL'"
            ).fetchone()[0]
        self.assertEqual(sl, 100.0)
        self.assertEqual(sl_trigger, 100.0)

    def test_cancel_pending_entries(self) -> None:
        plan = build_update_plan(
            {
                "message_type": "UPDATE",
                "actions": ["ACT_CANCEL_ALL_PENDING_ENTRIES"],
                "entities": {"cancel_scope": "ALL_ALL"},
                "target_refs": [101],
            }
        )
        result = apply_update_plan(plan, self.db_path)
        self.assertFalse(result.errors)
        with sqlite3.connect(self.db_path) as conn:
            status = conn.execute(
                "SELECT status FROM orders WHERE attempt_key='atk1' AND purpose='ENTRY'"
            ).fetchone()[0]
        self.assertEqual(status, "CANCELLED")

    def test_cancel_pending_all_short_entries_by_trader_scope(self) -> None:
        plan = build_update_plan(
            {
                "message_type": "UPDATE",
                "actions": ["ACT_CANCEL_ALL_PENDING_ENTRIES"],
                "entities": {"cancel_scope": "ALL_SHORT"},
                "target_refs": [],
            }
        )
        result = apply_update_plan(plan, self.db_path, trader_id="TA")
        self.assertFalse(result.errors)
        with sqlite3.connect(self.db_path) as conn:
            long_entry = conn.execute(
                "SELECT status FROM orders WHERE attempt_key='atk1' AND purpose='ENTRY'"
            ).fetchone()[0]
            short_entry = conn.execute(
                "SELECT status FROM orders WHERE attempt_key='atk2' AND purpose='ENTRY'"
            ).fetchone()[0]
        self.assertEqual(long_entry, "NEW")
        self.assertEqual(short_entry, "CANCELLED")

    def test_close_full_updates_trade(self) -> None:
        plan = build_update_plan(
            {
                "message_type": "UPDATE",
                "actions": ["ACT_CLOSE_FULL"],
                "entities": {"close_scope": "FULL"},
                "target_refs": [101],
            }
        )
        result = apply_update_plan(plan, self.db_path)
        self.assertFalse(result.errors)
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT state, close_reason FROM trades WHERE attempt_key='atk1'").fetchone()
        self.assertEqual(row[0], "CLOSED")
        self.assertEqual(row[1], "FULL_CLOSE_REQUESTED")

    def test_mark_order_filled(self) -> None:
        plan = build_update_plan(
            {
                "message_type": "UPDATE",
                "actions": ["ACT_MARK_ORDER_FILLED"],
                "entities": {"fill_state": "FILLED"},
                "target_refs": [101],
            }
        )
        result = apply_update_plan(plan, self.db_path)
        self.assertFalse(result.errors)
        with sqlite3.connect(self.db_path) as conn:
            status = conn.execute(
                "SELECT status FROM orders WHERE attempt_key='atk1' AND purpose='ENTRY'"
            ).fetchone()[0]
        self.assertEqual(status, "FILLED")

    def test_attach_result(self) -> None:
        plan = build_update_plan(
            {
                "message_type": "UPDATE",
                "actions": ["ACT_ATTACH_RESULT"],
                "entities": {"result_mode": "R_MULTIPLE"},
                "reported_results": [{"symbol": "BTCUSDT", "r_multiple": 1.1}],
                "target_refs": [101],
            }
        )
        result = apply_update_plan(plan, self.db_path)
        self.assertFalse(result.errors)
        with sqlite3.connect(self.db_path) as conn:
            meta_raw = conn.execute("SELECT meta_json FROM trades WHERE attempt_key='atk1'").fetchone()[0]
        meta = json.loads(meta_raw)
        self.assertEqual(meta.get("result_mode"), "R_MULTIPLE")
        self.assertEqual(meta.get("reported_results"), [{"symbol": "BTCUSDT", "r_multiple": 1.1}])

    def test_missing_target_refs_only_warns(self) -> None:
        plan = build_update_plan(
            {
                "message_type": "UPDATE",
                "actions": ["ACT_CLOSE_FULL"],
                "entities": {"close_scope": "FULL"},
                "target_refs": [],
            }
        )
        result = apply_update_plan(plan, self.db_path)
        self.assertIn("apply_missing_target_attempt_keys", result.warnings)
        with sqlite3.connect(self.db_path) as conn:
            state = conn.execute("SELECT state FROM trades WHERE attempt_key='atk1'").fetchone()[0]
        self.assertEqual(state, "OPEN")

    def test_incomplete_attach_result_warns(self) -> None:
        plan = build_update_plan(
            {
                "message_type": "UPDATE",
                "actions": ["ACT_ATTACH_RESULT"],
                "entities": {"result_mode": "R_MULTIPLE"},
                "target_refs": [101],
            }
        )
        result = apply_update_plan(plan, self.db_path)
        self.assertIn("apply_attach_result_missing_payload", result.warnings)


if __name__ == "__main__":
    unittest.main()
