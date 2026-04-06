"""Inspect one bridge attempt_key across bot DB and freqtrade dry-run DB.

Usage examples:
    python scripts/inspect_attempt.py --attempt-key T_xxx
    python scripts/inspect_attempt.py --latest-signal
    python scripts/inspect_attempt.py --latest-trade
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_BOT_DB_CANDIDATES = (
    PROJECT_ROOT / "db" / "tele_signal_bot.sqlite3",
    PROJECT_ROOT / ".local" / "tele_signalbot.sqlite3",
)
DEFAULT_FREQTRADE_DB = PROJECT_ROOT / "freqtrade" / "tradesv3.dryrun.sqlite"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspect one attempt_key end-to-end for dry-run bridge verification."
    )
    parser.add_argument("--attempt-key", default=None, help="Exact attempt_key to inspect.")
    parser.add_argument(
        "--latest-signal",
        action="store_true",
        help="Resolve attempt_key from the newest row in signals.",
    )
    parser.add_argument(
        "--latest-trade",
        action="store_true",
        help="Resolve attempt_key from the newest row in trades.",
    )
    parser.add_argument(
        "--symbol",
        default=None,
        help="Resolve the latest attempt_key for a symbol like BTCUSDT.",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Path to the TeleSignalBot SQLite DB. If omitted, auto-detects a matching DB.",
    )
    parser.add_argument(
        "--freqtrade-db-path",
        default=str(DEFAULT_FREQTRADE_DB),
        help="Path to the freqtrade dry-run trades DB.",
    )
    return parser.parse_args()


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(row["name"]) for row in rows}


def _latest_order_expr(columns: set[str], preferred: list[str]) -> str:
    for column in preferred:
        if column in columns:
            return f"{column} DESC"
    return "rowid DESC"


def _candidate_bot_db_paths(explicit_db_path: str | None) -> list[str]:
    if explicit_db_path:
        return [str(Path(explicit_db_path).resolve())]
    return [str(path.resolve()) for path in DEFAULT_BOT_DB_CANDIDATES if path.exists()]


def _coerce_json(value: Any) -> Any:
    if not isinstance(value, str) or not value.strip():
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    out: dict[str, Any] = {}
    for key in row.keys():
        out[key] = _coerce_json(row[key])
    return out


def _rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [_row_to_dict(row) or {} for row in rows]


def resolve_attempt_key(conn: sqlite3.Connection, args: argparse.Namespace) -> str | None:
    selectors = sum(
        bool(value)
        for value in (args.attempt_key, args.latest_signal, args.latest_trade, args.symbol)
    )
    if selectors != 1:
        raise SystemExit(
            "Use exactly one selector: --attempt-key, --latest-signal, --latest-trade, or --symbol."
        )

    if args.attempt_key:
        return str(args.attempt_key)

    if args.latest_signal:
        signal_cols = _table_columns(conn, "signals")
        order_expr = _latest_order_expr(signal_cols, ["created_at", "updated_at", "trader_signal_id"])
        row = conn.execute(
            f"SELECT attempt_key FROM signals ORDER BY {order_expr} LIMIT 1"
        ).fetchone()
        return str(row["attempt_key"]) if row and row["attempt_key"] else None

    if args.latest_trade:
        trade_cols = _table_columns(conn, "trades")
        order_expr = _latest_order_expr(trade_cols, ["trade_id", "created_at", "updated_at"])
        row = conn.execute(
            f"SELECT attempt_key FROM trades ORDER BY {order_expr} LIMIT 1"
        ).fetchone()
        return str(row["attempt_key"]) if row and row["attempt_key"] else None

    symbol = str(args.symbol or "").strip().upper()
    signal_cols = _table_columns(conn, "signals")
    order_expr = _latest_order_expr(signal_cols, ["created_at", "updated_at", "trader_signal_id"])
    row = conn.execute(
        f"""
        SELECT attempt_key
        FROM signals
        WHERE UPPER(symbol) = ?
        ORDER BY {order_expr}
        LIMIT 1
        """,
        (symbol,),
    ).fetchone()
    return str(row["attempt_key"]) if row and row["attempt_key"] else None


def resolve_db_and_attempt_key(args: argparse.Namespace) -> tuple[str, str]:
    last_error: str | None = None
    for db_path in _candidate_bot_db_paths(args.db_path):
        try:
            with _connect(db_path) as conn:
                attempt_key = resolve_attempt_key(conn, args)
                if attempt_key:
                    return db_path, attempt_key
        except sqlite3.Error as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            continue

    if args.db_path:
        raise SystemExit(f"No attempt_key found in requested DB: {args.db_path}")
    candidate_text = ", ".join(str(path) for path in DEFAULT_BOT_DB_CANDIDATES)
    if last_error:
        raise SystemExit(f"No attempt_key found in candidate DBs ({candidate_text}). Last error: {last_error}")
    raise SystemExit(f"No attempt_key found in candidate DBs ({candidate_text}).")


def load_attempt_snapshot(
    *,
    conn: sqlite3.Connection,
    attempt_key: str,
) -> dict[str, Any]:
    signal = conn.execute("SELECT * FROM signals WHERE attempt_key = ? LIMIT 1", (attempt_key,)).fetchone()

    operational = conn.execute(
        """
        SELECT *
        FROM operational_signals
        WHERE attempt_key = ?
        ORDER BY op_signal_id ASC
        """,
        (attempt_key,),
    ).fetchall()

    trade = conn.execute(
        """
        SELECT *
        FROM trades
        WHERE attempt_key = ?
        ORDER BY trade_id DESC
        LIMIT 1
        """,
        (attempt_key,),
    ).fetchone()

    symbol = None
    env = "DRY_RUN"
    if signal is not None:
        symbol = signal["symbol"]
        env = str(signal["env"] or env)
    elif trade is not None:
        symbol = trade["symbol"]

    position = None
    if symbol:
        position = conn.execute(
            "SELECT * FROM positions WHERE env = ? AND symbol = ? LIMIT 1",
            (env, symbol),
        ).fetchone()

    orders = conn.execute(
        """
        SELECT order_pk, attempt_key, symbol, side, order_type, purpose, idx,
               qty, price, trigger_price, reduce_only, client_order_id,
               exchange_order_id, status, last_exchange_sync_at, created_at, updated_at
        FROM orders
        WHERE attempt_key = ?
        ORDER BY order_pk ASC
        """,
        (attempt_key,),
    ).fetchall()

    events = conn.execute(
        """
        SELECT event_id, event_type, payload_json, created_at
        FROM events
        WHERE attempt_key = ?
        ORDER BY event_id ASC
        """,
        (attempt_key,),
    ).fetchall()

    warnings = conn.execute(
        """
        SELECT warning_id, code, severity, detail_json, created_at
        FROM warnings
        WHERE attempt_key = ?
        ORDER BY warning_id ASC
        """,
        (attempt_key,),
    ).fetchall()

    return {
        "signal": _row_to_dict(signal),
        "operational_signals": _rows_to_dicts(operational),
        "trade": _row_to_dict(trade),
        "position": _row_to_dict(position),
        "orders": _rows_to_dicts(orders),
        "events": _rows_to_dicts(events),
        "warnings": _rows_to_dicts(warnings),
    }


def load_freqtrade_snapshot(*, freqtrade_db_path: str, attempt_key: str) -> dict[str, Any]:
    path = Path(freqtrade_db_path)
    if not path.exists():
        return {"db_found": False, "orders": []}

    with _connect(str(path)) as conn:
        tag_like = f"{attempt_key}:%"
        orders = conn.execute(
            """
            SELECT o.id, o.ft_trade_id, o.ft_order_side, o.order_type, o.status,
                   o.price, o.stop_price, o.amount, o.filled, o.ft_order_tag,
                   t.enter_tag, t.is_open, t.pair
            FROM orders o
            LEFT JOIN trades t ON t.id = o.ft_trade_id
            WHERE o.ft_order_tag = ?
               OR o.ft_order_tag LIKE ?
               OR t.enter_tag = ?
            ORDER BY o.id ASC
            """,
            (attempt_key, tag_like, attempt_key),
        ).fetchall()
    return {"db_found": True, "orders": _rows_to_dicts(orders)}


def print_summary(*, attempt_key: str, snapshot: dict[str, Any], freqtrade: dict[str, Any]) -> None:
    print(f"ATTEMPT_KEY: {attempt_key}")
    print("")

    signal = snapshot.get("signal") or {}
    trade = snapshot.get("trade") or {}
    position = snapshot.get("position") or {}

    if signal:
        print("SIGNAL")
        print(
            f"  trader={signal.get('trader_id')} symbol={signal.get('symbol')} side={signal.get('side')} "
            f"status={signal.get('status')} entry_type={signal.get('entry_type')}"
        )
        print(f"  created_at={signal.get('created_at')} updated_at={signal.get('updated_at')}")
        print(f"  sl={signal.get('sl')}")
        print(f"  entry_json={json.dumps(signal.get('entry_json'), ensure_ascii=True)}")
        print(f"  tp_json={json.dumps(signal.get('tp_json'), ensure_ascii=True)}")
        print("")

    if trade:
        print("TRADE")
        print(
            f"  state={trade.get('state')} side={trade.get('side')} symbol={trade.get('symbol')} "
            f"close_reason={trade.get('close_reason')}"
        )
        print(f"  opened_at={trade.get('opened_at')} closed_at={trade.get('closed_at')}")
        print(f"  meta_json={json.dumps(trade.get('meta_json'), ensure_ascii=True)}")
        print("")

    if position:
        print("POSITION")
        print(
            f"  size={position.get('size')} entry_price={position.get('entry_price')} "
            f"mark_price={position.get('mark_price')} leverage={position.get('leverage')}"
        )
        print(
            f"  unrealized_pnl={position.get('unrealized_pnl')} realized_pnl={position.get('realized_pnl')}"
        )
        print("")

    operational = snapshot.get("operational_signals") or []
    if operational:
        print("OPERATIONAL_SIGNALS")
        for row in operational:
            print(
                f"  op_signal_id={row.get('op_signal_id')} type={row.get('message_type')} "
                f"blocked={row.get('is_blocked')} target_eligibility={row.get('target_eligibility')}"
            )
        print("")

    orders = snapshot.get("orders") or []
    entry_orders = [row for row in orders if str(row.get("purpose") or "").upper() == "ENTRY"]
    protective_orders = [row for row in orders if str(row.get("purpose") or "").upper() in {"SL", "TP"}]
    filled_entries = [row for row in entry_orders if str(row.get("status") or "").upper() == "FILLED"]
    pending_entries = [
        row for row in entry_orders
        if str(row.get("status") or "").upper() not in {"FILLED", "CANCELLED", "REJECTED", "EXPIRED"}
    ]
    open_protectives = [
        row for row in protective_orders
        if str(row.get("status") or "").upper() in {"OPEN", "NEW", "PARTIALLY_FILLED"}
    ]

    if filled_entries or pending_entries or open_protectives:
        print("ORDER_OVERVIEW")
        if filled_entries:
            print(f"  entry_filled ({len(filled_entries)})")
            for row in filled_entries:
                print(
                    f"    idx={row.get('idx')} side={row.get('side')} type={row.get('order_type')} "
                    f"qty={row.get('qty')} price={row.get('price')} status={row.get('status')}"
                )
        if pending_entries:
            print(f"  averaging_pending ({len(pending_entries)})")
            for row in pending_entries:
                print(
                    f"    idx={row.get('idx')} side={row.get('side')} type={row.get('order_type')} "
                    f"qty={row.get('qty')} price={row.get('price')} status={row.get('status')}"
                )
        if open_protectives:
            print(f"  protective_open ({len(open_protectives)})")
            for row in open_protectives:
                price = row.get("trigger_price") if row.get("trigger_price") not in (None, 0, 0.0) else row.get("price")
                print(
                    f"    purpose={row.get('purpose')} idx={row.get('idx')} side={row.get('side')} "
                    f"type={row.get('order_type')} qty={row.get('qty')} price={price} status={row.get('status')}"
                )
        print("")

    print(f"BOT_DB_ORDERS ({len(orders)})")
    for row in orders:
        print(
            f"  #{row.get('order_pk')} purpose={row.get('purpose')} idx={row.get('idx')} "
            f"status={row.get('status')} side={row.get('side')} type={row.get('order_type')} "
            f"qty={row.get('qty')} price={row.get('price')} trigger={row.get('trigger_price')} "
            f"client_id={row.get('client_order_id')} exchange_id={row.get('exchange_order_id')}"
        )
    print("")

    events = snapshot.get("events") or []
    print(f"EVENT_TIMELINE ({len(events)})")
    for row in events:
        payload = row.get("payload_json")
        payload_text = json.dumps(payload, ensure_ascii=True) if isinstance(payload, (dict, list)) else str(payload)
        print(
            f"  #{row.get('event_id')} {row.get('created_at')} {row.get('event_type')} payload={payload_text}"
        )
    print("")

    warnings = snapshot.get("warnings") or []
    print(f"WARNINGS ({len(warnings)})")
    for row in warnings:
        detail = row.get("detail_json")
        detail_text = json.dumps(detail, ensure_ascii=True) if isinstance(detail, (dict, list)) else str(detail)
        print(
            f"  #{row.get('warning_id')} {row.get('created_at')} {row.get('severity')} {row.get('code')} detail={detail_text}"
        )
    print("")

    ft_orders = freqtrade.get("orders") or []
    if not freqtrade.get("db_found"):
        print("FREQTRADE_DB")
        print("  db not found")
        return

    print(f"FREQTRADE_DB_ORDERS ({len(ft_orders)})")
    for row in ft_orders:
        print(
            f"  #{row.get('id')} trade_id={row.get('ft_trade_id')} pair={row.get('pair')} "
            f"side={row.get('ft_order_side')} status={row.get('status')} type={row.get('order_type')} "
            f"price={row.get('price')} stop_price={row.get('stop_price')} amount={row.get('amount')} "
            f"filled={row.get('filled')} tag={row.get('ft_order_tag')}"
        )


def main() -> None:
    args = parse_args()
    db_path, attempt_key = resolve_db_and_attempt_key(args)
    with _connect(db_path) as conn:
        snapshot = load_attempt_snapshot(conn=conn, attempt_key=attempt_key)

    freqtrade = load_freqtrade_snapshot(
        freqtrade_db_path=str(Path(args.freqtrade_db_path).resolve()),
        attempt_key=attempt_key,
    )
    print(f"BOT_DB: {db_path}")
    print_summary(attempt_key=attempt_key, snapshot=snapshot, freqtrade=freqtrade)


if __name__ == "__main__":
    main()
