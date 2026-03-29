"""Show pending take-profit levels for all open trades.

Usage:
    python scripts/tp_status.py                  # print to console
    python scripts/tp_status.py --telegram       # send to Telegram bot
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DB_PATH = os.getenv(
    "TELESIGNALBOT_DB_PATH",
    str(Path(__file__).resolve().parent.parent / "db" / "tele_signal_bot.sqlite3"),
)


def get_tp_status(db_path: str) -> str:
    """Query open trades and their TP levels, return formatted text."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    trades = conn.execute(
        """
        SELECT t.attempt_key, t.symbol, t.side, s.tp_json, s.sl, s.entry_json
        FROM trades t
        JOIN signals s ON s.attempt_key = t.attempt_key
        WHERE t.state = 'OPEN'
        ORDER BY t.symbol
        """
    ).fetchall()

    if not trades:
        return "No open trades."

    lines: list[str] = ["*TP Status -- Open Trades*\n"]

    for trade in trades:
        symbol = trade["symbol"]
        side = trade["side"]
        tp_list = json.loads(trade["tp_json"]) if trade["tp_json"] else []
        entry_list = json.loads(trade["entry_json"]) if trade["entry_json"] else []
        sl = trade["sl"]
        attempt_key = trade["attempt_key"]

        # Get entry fill price
        entry_fill = conn.execute(
            """
            SELECT price FROM orders
            WHERE attempt_key = ? AND purpose = 'ENTRY' AND status = 'FILLED'
            LIMIT 1
            """,
            (attempt_key,),
        ).fetchone()
        fill_price = entry_fill["price"] if entry_fill else None

        # Get TP order statuses
        tp_orders = conn.execute(
            """
            SELECT idx, status FROM orders
            WHERE attempt_key = ? AND purpose = 'TP'
            ORDER BY idx
            """,
            (attempt_key,),
        ).fetchall()
        filled_indices = {row["idx"] for row in tp_orders if row["status"] == "FILLED"}

        lines.append(f"*{symbol}* ({side})")
        if fill_price:
            lines.append(f"  Entry fill: `{fill_price}`")
        if sl:
            lines.append(f"  SL: `{sl}`")

        if not tp_list:
            lines.append("  No TP levels defined")
        else:
            for idx, tp in enumerate(tp_list):
                price = tp["price"] if isinstance(tp, dict) else tp
                status = "[FILLED]" if idx in filled_indices else "[PENDING]"
                lines.append(f"  {status} TP{idx}: `{price}`")

        lines.append("")

    conn.close()
    return "\n".join(lines)


def send_telegram(text: str) -> None:
    """Send message via freqtrade's Telegram bot config."""
    config_path = Path(__file__).resolve().parent.parent / "freqtrade" / "user_data" / "config.json"
    if not config_path.exists():
        print("ERROR: config.json not found")
        return

    config = json.loads(config_path.read_text(encoding="utf-8"))
    tg = config.get("telegram", {})
    token = tg.get("token", "")
    chat_id = tg.get("chat_id", "")

    if not token or not chat_id or token.startswith("__"):
        print("ERROR: Telegram token/chat_id not configured in config.json")
        return

    import urllib.request
    import urllib.parse

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
    }).encode()

    try:
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            if result.get("ok"):
                print("Message sent to Telegram.")
            else:
                print(f"Telegram API error: {result}")
    except Exception as exc:
        print(f"Failed to send: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Show TP status for open trades")
    parser.add_argument("--telegram", action="store_true", help="Send to Telegram")
    parser.add_argument("--db", default=DB_PATH, help="DB path")
    args = parser.parse_args()

    text = get_tp_status(args.db)
    print(text)

    if args.telegram:
        send_telegram(text)


if __name__ == "__main__":
    main()
