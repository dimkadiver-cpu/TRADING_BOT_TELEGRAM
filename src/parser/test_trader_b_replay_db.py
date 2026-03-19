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


class TraderBReplayDbTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, path = tempfile.mkstemp(prefix="tsb_parser_test_b_", suffix=".sqlite3")
        os.close(fd)
        self.db_path = path
        apply_migrations(self.db_path, "db/migrations")
        self.pipeline = MinimalParserPipeline(trader_aliases={"TB": "TB", "trader_b": "trader_b", "B": "B", "TA": "TA"})
        self._seed_target_attempts()
        self._raw_message_id = 22000

    def tearDown(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(self.db_path + suffix)
            except (FileNotFoundError, PermissionError):
                pass

    def _seed_target_attempts(self) -> None:
        now = utc_now_iso()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO signals(
                  attempt_key, env, channel_id, root_telegram_id, trader_id, trader_prefix,
                  trader_signal_id, symbol, side, entry_json, sl, tp_json, status, confidence, raw_text, created_at, updated_at
                ) VALUES ('atkB501', 'T', '-100777', '501', 'TB', 'TB', 501, 'BTCUSDT', 'BUY', ?, 95000.0, ?, 'ACTIVE', 0.9, 'seed', ?, ?)
                """,
                (json.dumps([100000.0]), json.dumps([102000.0]), now, now),
            )
            conn.execute(
                """
                INSERT INTO signals(
                  attempt_key, env, channel_id, root_telegram_id, trader_id, trader_prefix,
                  trader_signal_id, symbol, side, entry_json, sl, tp_json, status, confidence, raw_text, created_at, updated_at
                ) VALUES ('atkB502', 'T', '-100777', '502', 'TB', 'TB', 502, 'ETHUSDT', 'SELL', ?, 2200.0, ?, 'ACTIVE', 0.9, 'seed', ?, ?)
                """,
                (json.dumps([2100.0]), json.dumps([2000.0]), now, now),
            )
            conn.execute(
                """
                INSERT INTO trades(
                  env, attempt_key, trader_id, symbol, side, execution_mode, state,
                  entry_zone_policy, non_chase_policy, opened_at, meta_json, created_at, updated_at
                ) VALUES
                ('T', 'atkB501', 'TB', 'BTCUSDT', 'BUY', 'PAPER', 'OPEN', 'Z1', 'NI3', ?, '{}', ?, ?),
                ('T', 'atkB502', 'TB', 'ETHUSDT', 'SELL', 'PAPER', 'OPEN', 'Z1', 'NI3', ?, '{}', ?, ?)
                """,
                (now, now, now, now, now, now),
            )
            conn.execute(
                """
                INSERT INTO orders(
                  env, attempt_key, symbol, side, order_type, purpose, idx, qty, price, trigger_price,
                  reduce_only, client_order_id, exchange_order_id, status, created_at, updated_at
                ) VALUES
                ('T', 'atkB501', 'BTCUSDT', 'BUY', 'LIMIT', 'ENTRY', 0, 1.0, 100000.0, NULL, 0, 'coid-b-entry-1', NULL, 'NEW', ?, ?),
                ('T', 'atkB502', 'ETHUSDT', 'SELL', 'LIMIT', 'ENTRY', 0, 1.0, 2100.0, NULL, 0, 'coid-b-entry-2', NULL, 'NEW', ?, ?)
                """,
                (now, now, now, now),
            )
            conn.commit()

    def _run_e2e(self, *, text: str) -> tuple[dict[str, object], object, object]:
        self._raw_message_id += 1
        parse_record = self.pipeline.parse(
            ParserInput(
                raw_message_id=self._raw_message_id,
                raw_text=text,
                eligibility_status="ACQUIRED_ELIGIBLE",
                eligibility_reason="eligible",
                resolved_trader_id="trader_b",
                trader_resolution_method="tag",
                linkage_method=None,
                source_chat_id="-100777",
                source_message_id=self._raw_message_id,
                linkage_reference_id=None,
            )
        )
        normalized = json.loads(parse_record.parse_result_normalized_json or "{}")
        plan = build_update_plan(normalized)
        apply_result = apply_update_plan(
            plan,
            self.db_path,
            env="T",
            channel_id="-100777",
            telegram_msg_id=str(self._raw_message_id),
            trader_id="TB",
            trader_prefix="TB",
        )
        return normalized, plan, apply_result

    def _reset_entry_statuses(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE orders SET status = 'NEW' WHERE purpose = 'ENTRY'")
            conn.commit()

    def _entry_status(self, attempt_key: str) -> str:
        with sqlite3.connect(self.db_path) as conn:
            return str(
                conn.execute(
                    "SELECT status FROM orders WHERE attempt_key = ? AND purpose = 'ENTRY'",
                    (attempt_key,),
                ).fetchone()[0]
            )

    def test_cancel_pending_scopes_replay(self) -> None:
        normalized, _, _ = self._run_e2e(text="cancel pending orders https://t.me/c/777/501")
        self.assertEqual(normalized.get("message_type"), "UPDATE")
        self.assertEqual((normalized.get("entities") or {}).get("cancel_scope"), "TARGETED")
        self.assertEqual(self._entry_status("atkB501"), "CANCELLED")
        self.assertEqual(self._entry_status("atkB502"), "NEW")

        self._reset_entry_statuses()
        normalized, _, _ = self._run_e2e(text="cancel pending all")
        self.assertEqual((normalized.get("entities") or {}).get("cancel_scope"), "ALL_PENDING_ENTRIES")
        self.assertEqual(self._entry_status("atkB501"), "CANCELLED")
        self.assertEqual(self._entry_status("atkB502"), "CANCELLED")

        self._reset_entry_statuses()
        normalized, _, _ = self._run_e2e(text="cancel pending all longs")
        self.assertEqual((normalized.get("entities") or {}).get("cancel_scope"), "ALL_LONG")
        self.assertEqual(self._entry_status("atkB501"), "CANCELLED")
        self.assertEqual(self._entry_status("atkB502"), "NEW")

        self._reset_entry_statuses()
        normalized, _, _ = self._run_e2e(text="cancel pending all shorts")
        self.assertEqual((normalized.get("entities") or {}).get("cancel_scope"), "ALL_SHORT")
        self.assertEqual(self._entry_status("atkB501"), "NEW")
        self.assertEqual(self._entry_status("atkB502"), "CANCELLED")


if __name__ == "__main__":
    unittest.main()
