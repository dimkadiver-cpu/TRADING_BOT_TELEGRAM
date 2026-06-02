from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AggregationWorker:
    def __init__(
        self,
        ops_db_path: str,
        *,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._ops_db = ops_db_path
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))

    def run_once(self) -> int:
        with sqlite3.connect(self._ops_db) as conn:
            created = 0
            created += self._aggregate_update_batches(conn)
            created += self._aggregate_multi_chain_updates(conn)
            return created

    def _mature_now(self) -> str:
        return self._now_fn().isoformat()

    def _aggregate_update_batches(self, conn: sqlite3.Connection) -> int:
        """Group mature UPDATE_* rows by aggregation_group (same chain/source).

        If group has >1 rows: insert merged UPDATE_DONE, suppress originals.
        """
        rows = conn.execute(
            """
            SELECT notification_id, notification_type, payload_json, aggregation_group, created_at
            FROM ops_notification_outbox
            WHERE status='PENDING'
              AND notification_type IN ('UPDATE_DONE', 'UPDATE_PARTIAL', 'UPDATE_REJECTED')
              AND aggregation_group IS NOT NULL
              AND (send_after IS NULL OR send_after <= ?)
            ORDER BY aggregation_group, notification_id
            """,
            (self._mature_now(),),
        ).fetchall()

        if not rows:
            return 0

        groups: dict[str, list[tuple]] = {}
        for row in rows:
            groups.setdefault(row[3], []).append(row)

        created = 0
        for group, group_rows in groups.items():
            if len(group_rows) < 2:
                continue

            dedupe_key = f"clean:aggregate:update_batch:{group}"
            if conn.execute(
                "SELECT 1 FROM ops_notification_outbox WHERE dedupe_key=?", (dedupe_key,)
            ).fetchone():
                continue

            has_rejected = any(r[1] == "UPDATE_REJECTED" for r in group_rows)
            merged_type = "UPDATE_PARTIAL" if has_rejected else "UPDATE_DONE"
            chain_id = None
            symbol = None
            side = None
            all_ops: list = []
            # Use earliest created_at so the merged row sorts before POSITION_CLOSED
            earliest_created_at = min(r[4] for r in group_rows if r[4])

            for nid, ntype, payload_json, agroup, created_at in group_rows:
                try:
                    p = json.loads(payload_json or "{}")
                except Exception:
                    p = {}
                if chain_id is None:
                    chain_id = p.get("chain_id")
                    symbol = p.get("symbol")
                    side = p.get("side")
                all_ops.extend(p.get("operations") or p.get("applied_actions") or [])

            batch_payload = {
                "chain_id": chain_id,
                "symbol": symbol,
                "side": side,
                "operations": all_ops,
            }

            ids = [r[0] for r in group_rows]
            conn.execute(
                f"UPDATE ops_notification_outbox SET status='SUPPRESSED' "
                f"WHERE notification_id IN ({','.join('?' * len(ids))})",
                ids,
            )
            conn.execute(
                f"""
                INSERT OR IGNORE INTO ops_notification_outbox
                    (notification_type, destination, payload_json, priority, status,
                     dedupe_key, attempts, created_at)
                VALUES (?, 'CLEAN_LOG', ?, 'MEDIUM', 'PENDING', ?, 0, ?)
                """,
                (merged_type, json.dumps(batch_payload), dedupe_key, earliest_created_at),
            )
            created += 1

        return created

    def _aggregate_multi_chain_updates(self, conn: sqlite3.Connection) -> int:
        """Group UPDATE_DONE rows by source_message_id across chains.

        If distinct chains >= threshold (3): insert MULTI_CHAIN_UPDATE, suppress DONE rows.
        """
        rows = conn.execute(
            """
            SELECT notification_id, payload_json, source_message_id
            FROM ops_notification_outbox
            WHERE status='PENDING'
              AND notification_type='UPDATE_DONE'
              AND source_message_id IS NOT NULL
              AND (send_after IS NULL OR send_after <= ?)
            ORDER BY source_message_id, notification_id
            """,
            (self._mature_now(),),
        ).fetchall()

        if not rows:
            return 0

        groups: dict[str, list[tuple]] = {}
        for row in rows:
            groups.setdefault(row[2], []).append(row)

        created = 0
        for source_msg_id, group_rows in groups.items():
            chain_ids = set()
            for nid, payload_json, _ in group_rows:
                try:
                    p = json.loads(payload_json or "{}")
                    cid = p.get("chain_id")
                    if cid is not None:
                        chain_ids.add(cid)
                except Exception:
                    pass

            if len(chain_ids) < 3:
                continue

            dedupe_key = f"clean:aggregate:multi_chain_update:{source_msg_id}"
            if conn.execute(
                "SELECT 1 FROM ops_notification_outbox WHERE dedupe_key=?", (dedupe_key,)
            ).fetchone():
                continue

            chains = []
            operations: list = []
            for nid, payload_json, _ in group_rows:
                try:
                    p = json.loads(payload_json or "{}")
                except Exception:
                    p = {}
                chains.append({
                    "chain_id": p.get("chain_id"),
                    "symbol": p.get("symbol"),
                    "side": p.get("side"),
                    "status": "DONE",
                })
                operations.extend(p.get("operations") or p.get("applied_actions") or [])

            batch_payload = {
                "operations": operations,
                "chains": chains,
                "summary": {"done": len(chain_ids), "rejected": 0},
            }

            ids = [r[0] for r in group_rows]
            conn.execute(
                f"UPDATE ops_notification_outbox SET status='SUPPRESSED' "
                f"WHERE notification_id IN ({','.join('?' * len(ids))})",
                ids,
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO ops_notification_outbox
                    (notification_type, destination, payload_json, priority, status,
                     dedupe_key, attempts, created_at)
                VALUES ('MULTI_CHAIN_UPDATE', 'CLEAN_LOG', ?, 'MEDIUM', 'PENDING', ?, 0, ?)
                """,
                (json.dumps(batch_payload), dedupe_key, _now()),
            )
            created += 1

        return created


__all__ = ["AggregationWorker"]
