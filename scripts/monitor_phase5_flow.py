from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path


TABLES = (
    "raw_messages",
    "parse_results",
    "signals",
    "operational_signals",
    "trades",
    "orders",
    "events",
)


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (name,),
    ).fetchone()
    return bool(row)


def _counts(db_path: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    with sqlite3.connect(str(db_path), timeout=5) as conn:
        for table in TABLES:
            if _table_exists(conn, table):
                counts[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            else:
                counts[table] = -1
    return counts


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row[1])
        for row in conn.execute(f"PRAGMA table_info('{table}')").fetchall()
        if len(row) > 1
    }


def _latest_query(conn: sqlite3.Connection, table: str, candidates: list[str], fallback_order: str) -> sqlite3.Row | tuple | None:
    available = _columns(conn, table)
    selected = [column for column in candidates if column in available]
    if not selected:
        return None
    order_by = fallback_order if fallback_order in available else selected[0]
    query = f"SELECT {', '.join(selected)} FROM {table} ORDER BY {order_by} DESC LIMIT 1"
    return conn.execute(query).fetchone()


def _latest_rows(db_path: Path) -> dict[str, object]:
    rows: dict[str, object] = {}
    with sqlite3.connect(str(db_path), timeout=5) as conn:
        if _table_exists(conn, "raw_messages"):
            rows["raw_messages"] = _latest_query(
                conn,
                "raw_messages",
                ["raw_message_id", "source_chat_id", "telegram_message_id", "processing_status"],
                "raw_message_id",
            )
        if _table_exists(conn, "parse_results"):
            rows["parse_results"] = _latest_query(
                conn,
                "parse_results",
                ["parse_result_id", "resolved_trader_id", "message_type", "parse_status"],
                "parse_result_id",
            )
        if _table_exists(conn, "signals"):
            rows["signals"] = _latest_query(
                conn,
                "signals",
                ["attempt_key", "trader_id", "symbol", "side", "status"],
                "created_at",
            )
        if _table_exists(conn, "operational_signals"):
            rows["operational_signals"] = _latest_query(
                conn,
                "operational_signals",
                ["op_signal_id", "attempt_key", "trader_id", "message_type", "is_blocked", "target_eligibility"],
                "op_signal_id",
            )
        if _table_exists(conn, "trades"):
            rows["trades"] = _latest_query(
                conn,
                "trades",
                ["trade_id", "attempt_key", "symbol", "state", "close_reason"],
                "trade_id",
            )
        if _table_exists(conn, "orders"):
            rows["orders"] = _latest_query(
                conn,
                "orders",
                ["order_pk", "attempt_key", "purpose", "side", "order_type", "status"],
                "order_pk",
            )
        if _table_exists(conn, "events"):
            rows["events"] = _latest_query(
                conn,
                "events",
                ["event_id", "attempt_key", "event_type"],
                "event_id",
            )
    return rows


def _dynamic_pairs(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    pairs = payload.get("pairs", [])
    return [str(pair) for pair in pairs if isinstance(pair, str)]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", required=True)
    parser.add_argument("--dynamic-pairlist-path", required=True)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--poll-seconds", type=int, default=3)
    args = parser.parse_args()

    db_path = Path(args.db_path)
    pairlist_path = Path(args.dynamic_pairlist_path)

    print(f"[monitor] DB path: {db_path}")
    print(f"[monitor] Dynamic pairlist path: {pairlist_path}")

    if not db_path.exists():
        print("[monitor] DB file does not exist yet. Waiting for creation...")
        started = time.time()
        while time.time() - started < args.timeout:
            if db_path.exists():
                break
            time.sleep(1)
        if not db_path.exists():
            print("[monitor] Timeout: DB file was not created.")
            return 4

    baseline_counts = _counts(db_path)
    baseline_pairs = _dynamic_pairs(pairlist_path)
    print(f"[monitor] Baseline counts: {baseline_counts}")
    print(f"[monitor] Baseline dynamic pairs: {baseline_pairs}")

    started = time.time()
    partial = False

    while time.time() - started < args.timeout:
        time.sleep(max(1, args.poll_seconds))
        current_counts = _counts(db_path)
        current_pairs = _dynamic_pairs(pairlist_path)

        delta = {
            key: current_counts[key] - baseline_counts.get(key, 0)
            for key in current_counts
            if current_counts[key] >= 0 and baseline_counts.get(key, -1) >= 0
            and current_counts[key] != baseline_counts.get(key, 0)
        }
        pair_delta = sorted(set(current_pairs) - set(baseline_pairs))

        if not delta and not pair_delta:
            continue

        print(f"[monitor] Change detected. Count delta: {delta}")
        if pair_delta:
            print(f"[monitor] New dynamic pairs: {pair_delta}")

        latest = _latest_rows(db_path)
        for key, value in latest.items():
            print(f"[monitor] Latest {key}: {value}")

        if any(key in delta for key in ("signals", "operational_signals", "trades", "orders", "events")) or pair_delta:
            print("[monitor] PASS: data reached the bridge/freqtrade side.")
            return 0

        if any(key in delta for key in ("raw_messages", "parse_results")):
            partial = True

    if partial:
        print("[monitor] PARTIAL: message entered listener/parser, but did not reach signals/trades during the timeout.")
        return 3

    print("[monitor] READY: processes look healthy, but no new Telegram message was observed during the timeout.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
