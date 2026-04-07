"""Audit open bridge trades across bot DB and freqtrade DB.

Usage:
    python scripts/audit_live_sync.py
    python scripts/audit_live_sync.py --symbol BTCUSDT
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_BOT_DB = Path(
    os.getenv("TELESIGNALBOT_DB_PATH", str(PROJECT_ROOT / "db" / "tele_signal_bot.sqlite3"))
)
DEFAULT_FREQTRADE_DB = Path(
    os.getenv(
        "TELESIGNALBOT_FREQTRADE_TRADES_DB_PATH",
        str(PROJECT_ROOT / "freqtrade" / "tradesv3.dryrun.sqlite"),
    )
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit sync between bot DB and freqtrade DB.")
    parser.add_argument("--db-path", default=str(DEFAULT_BOT_DB), help="Path to TeleSignalBot DB.")
    parser.add_argument(
        "--freqtrade-db-path",
        default=str(DEFAULT_FREQTRADE_DB),
        help="Path to freqtrade trades DB.",
    )
    parser.add_argument("--symbol", default=None, help="Optional symbol filter like BTCUSDT.")
    return parser.parse_args()


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _canonical_pair(symbol: str | None) -> str | None:
    if not symbol:
        return None
    normalized = str(symbol).strip().upper()
    if not normalized.endswith("USDT"):
        return None
    base = normalized[:-4]
    return f"{base}/USDT:USDT"


def _attempt_key_from_tag(tag: str | None) -> str | None:
    if not isinstance(tag, str) or not tag.strip():
        return None
    normalized = tag.strip()
    if ":ENTRY:" in normalized:
        return normalized.split(":ENTRY:", 1)[0]
    return normalized


def _fetch_open_bot_trades(conn: sqlite3.Connection, *, symbol: str | None) -> list[sqlite3.Row]:
    params: list[Any] = []
    sql = """
        SELECT t.trade_id, t.attempt_key, t.symbol, t.side, t.state, t.close_reason,
               t.opened_at, t.updated_at, t.meta_json, s.status AS signal_status,
               s.trader_id, s.tp_json, s.sl
        FROM trades t
        LEFT JOIN signals s ON s.attempt_key = t.attempt_key
        WHERE t.state IN ('ENTRY_PENDING', 'OPEN', 'PARTIAL_CLOSE_REQUESTED', 'CLOSE_REQUESTED')
    """
    if symbol:
        sql += " AND UPPER(t.symbol) = ?"
        params.append(symbol.upper())
    sql += " ORDER BY t.trade_id DESC"
    return conn.execute(sql, params).fetchall()


def _fetch_bot_orders(conn: sqlite3.Connection, attempt_key: str) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT purpose, idx, side, order_type, qty, price, trigger_price, status,
               client_order_id, exchange_order_id, updated_at
        FROM orders
        WHERE attempt_key = ?
        ORDER BY purpose, idx, order_pk
        """,
        (attempt_key,),
    ).fetchall()


def _fetch_event_types(conn: sqlite3.Connection, attempt_key: str) -> list[str]:
    rows = conn.execute(
        "SELECT event_type FROM events WHERE attempt_key = ? ORDER BY created_at",
        (attempt_key,),
    ).fetchall()
    return [str(row[0]) for row in rows]


def _fetch_warning_codes(conn: sqlite3.Connection, attempt_key: str) -> list[str]:
    rows = conn.execute(
        "SELECT code FROM warnings WHERE attempt_key = ? ORDER BY created_at",
        (attempt_key,),
    ).fetchall()
    return [str(row[0]) for row in rows]


def _fetch_freqtrade_rows(
    conn: sqlite3.Connection, *, attempt_key: str, pair: str | None
) -> tuple[list[sqlite3.Row], list[sqlite3.Row]]:
    trade_rows = conn.execute(
        """
        SELECT id, pair, is_open, open_rate, close_rate, amount, stake_amount,
               open_date, close_date, enter_tag, exit_reason
        FROM trades
        WHERE enter_tag = ?
           OR enter_tag LIKE ?
           OR (? IS NOT NULL AND pair = ?)
        ORDER BY id DESC
        """,
        (attempt_key, f"{attempt_key}:ENTRY:%", pair, pair),
    ).fetchall()
    trade_ids = [int(row["id"]) for row in trade_rows]
    if not trade_ids:
        return trade_rows, []
    placeholders = ",".join("?" for _ in trade_ids)
    order_rows = conn.execute(
        f"""
        SELECT id, ft_trade_id, ft_order_side, order_type, status, price, average, amount,
               filled, remaining, order_id, ft_order_tag, order_update_date
        FROM orders
        WHERE ft_trade_id IN ({placeholders})
        ORDER BY id DESC
        """,
        trade_ids,
    ).fetchall()
    return trade_rows, order_rows


def _tp_fill_count(order_rows: list[sqlite3.Row]) -> int:
    seen: set[int] = set()
    for row in order_rows:
        tag = str(row["ft_order_tag"] or "")
        if ":TP:" not in tag:
            continue
        if str(row["status"] or "").lower() != "closed":
            continue
        try:
            seen.add(int(tag.rsplit(":", 1)[1]))
        except ValueError:
            continue
    return len(seen)


def _format_json_summary(value: str | None) -> str:
    if not value:
        return "-"
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return value
    return json.dumps(parsed, ensure_ascii=True, sort_keys=True)


def audit_trade(
    *,
    bot_conn: sqlite3.Connection,
    ft_conn: sqlite3.Connection,
    trade_row: sqlite3.Row,
) -> list[str]:
    attempt_key = str(trade_row["attempt_key"])
    symbol = str(trade_row["symbol"] or "")
    pair = _canonical_pair(symbol)
    bot_orders = _fetch_bot_orders(bot_conn, attempt_key)
    event_types = _fetch_event_types(bot_conn, attempt_key)
    warning_codes = _fetch_warning_codes(bot_conn, attempt_key)
    ft_trades, ft_orders = _fetch_freqtrade_rows(ft_conn, attempt_key=attempt_key, pair=pair)

    bot_entry_open = [
        row for row in bot_orders if row["purpose"] == "ENTRY" and str(row["status"]).upper() in {"OPEN", "NEW"}
    ]
    bot_tp_filled = [
        row for row in bot_orders if row["purpose"] == "TP" and str(row["status"]).upper() == "FILLED"
    ]
    ft_open_trade_count = sum(1 for row in ft_trades if int(row["is_open"] or 0) == 1)
    ft_alias_open_count = sum(
        1
        for row in ft_trades
        if int(row["is_open"] or 0) == 1 and _attempt_key_from_tag(row["enter_tag"]) == attempt_key
    )
    ft_tp_fills = _tp_fill_count(ft_orders)

    findings: list[str] = []
    if ft_open_trade_count > 1:
        findings.append(f"freqtrade has {ft_open_trade_count} open trade rows")
    if trade_row["state"] == "ENTRY_PENDING" and not bot_entry_open and ft_open_trade_count > 0:
        findings.append("ENTRY_PENDING in bot but freqtrade already has open trade")
    if trade_row["state"] == "OPEN" and ft_open_trade_count == 0:
        findings.append("bot trade OPEN but freqtrade has no open trade")
    if len(bot_tp_filled) != ft_tp_fills:
        findings.append(f"TP fill mismatch bot={len(bot_tp_filled)} freqtrade={ft_tp_fills}")
    if "RECONCILIATION_WARNING" in event_types or warning_codes:
        findings.append("reconciliation warnings present")
    if "ENTRY_FILLED" not in event_types and trade_row["state"] != "ENTRY_PENDING":
        findings.append("missing ENTRY_FILLED event")

    status_label = "OK" if not findings else "WARN"
    lines = [
        f"[{status_label}] {symbol} attempt={attempt_key}",
        f"  bot: state={trade_row['state']} signal={trade_row['signal_status']} trader={trade_row['trader_id']}",
        f"  bot: tp_filled={len(bot_tp_filled)} entry_open={len(bot_entry_open)} sl={trade_row['sl']}",
        f"  ft : open_trades={ft_open_trade_count} alias_open={ft_alias_open_count} tp_filled={ft_tp_fills}",
    ]
    if ft_trades:
        lines.append(
            "  ft : trade_tags="
            + ", ".join(
                f"{row['enter_tag']}[open={int(row['is_open'] or 0)}]"
                for row in ft_trades[:4]
            )
        )
    else:
        lines.append("  ft : no matching trades")
    if findings:
        lines.extend(f"  ! {item}" for item in findings)
    if warning_codes:
        lines.append("  warnings: " + ", ".join(warning_codes))
    if trade_row["meta_json"]:
        lines.append("  meta: " + _format_json_summary(trade_row["meta_json"]))
    return lines


def main() -> None:
    args = parse_args()
    bot_db_path = str(Path(args.db_path).resolve())
    ft_db_path = str(Path(args.freqtrade_db_path).resolve())

    with _connect(bot_db_path) as bot_conn, _connect(ft_db_path) as ft_conn:
        rows = _fetch_open_bot_trades(bot_conn, symbol=args.symbol)
        if not rows:
            print("No open or pending bot trades found.")
            return
        print(f"Bot DB: {bot_db_path}")
        print(f"Freqtrade DB: {ft_db_path}")
        print("")
        for index, row in enumerate(rows):
            if index:
                print("")
            for line in audit_trade(bot_conn=bot_conn, ft_conn=ft_conn, trade_row=row):
                print(line)


if __name__ == "__main__":
    main()
