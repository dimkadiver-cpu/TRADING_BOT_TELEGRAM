"""
Live test — TelegramNotificationDispatcher

Inserisce messaggi di test nell'outbox e li invia a Telegram.
Usa il DB ops.sqlite3 reale e le env var da .env.

Usage:
    python scripts/test_live_dispatcher.py
    python scripts/test_live_dispatcher.py --mode private_bot
"""
from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# Aggiungi root al path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

import ssl
import httpx
from telegram import Bot
from telegram.request import HTTPXRequest

from src.runtime_v2.control_plane.config import load_control_plane_config, ControlPlaneConfigError
from src.runtime_v2.control_plane.notification_dispatcher import (
    TelegramNotificationDispatcher,
    TelegramBotSender,
)
from src.runtime_v2.control_plane.topic_router import TopicRouter

CONFIG_PATH = "config/telegram_control.yaml"
OPS_DB = "db/ops.sqlite3"

MIGRATIONS_DIR = Path("db/ops_migrations")

TEST_EVENTS = [
    {
        "notification_type": "SIGNAL_ACCEPTED",
        "destination": "CLEAN_LOG",
        "payload": {
            "chain_id": 9999,
            "symbol": "BTCUSDT",
            "side": "LONG",
            "entry_mode": "LIMIT",
            "trader_id": "test_live",
        },
        "priority": "MEDIUM",
        "dedupe_key": "live_test:signal_accepted:9999",
    },
    {
        "notification_type": "ENTRY_OPENED",
        "destination": "CLEAN_LOG",
        "payload": {
            "chain_id": 9999,
            "symbol": "BTCUSDT",
            "side": "LONG",
            "fill_price": 65000.0,
            "fill_qty": 0.01,
        },
        "priority": "MEDIUM",
        "dedupe_key": "live_test:entry_opened:9999",
    },
    {
        "notification_type": "TP_FILLED",
        "destination": "CLEAN_LOG",
        "payload": {
            "chain_id": 9999,
            "symbol": "BTCUSDT",
            "side": "LONG",
            "fill_price": 67000.0,
            "fill_qty": 0.01,
            "tp_index": 1,
            "is_final": False,
        },
        "priority": "MEDIUM",
        "dedupe_key": "live_test:tp_filled:9999",
    },
    {
        "notification_type": "SL_FILLED",
        "destination": "CLEAN_LOG",
        "payload": {
            "chain_id": 9999,
            "symbol": "BTCUSDT",
            "side": "LONG",
            "fill_price": 63000.0,
            "fill_qty": 0.01,
        },
        "priority": "HIGH",
        "dedupe_key": "live_test:sl_filled:9999",
    },
]


def _apply_migrations(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
        try:
            conn.executescript(f.read_text(encoding="utf-8"))
            conn.commit()
        except sqlite3.OperationalError:
            conn.rollback()
    conn.close()
    print(f"  Migrations OK ({db_path})")


def _insert_test_events(db_path: str, events: list[dict]) -> int:
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    inserted = 0
    for ev in events:
        import json
        rows = conn.execute(
            "SELECT notification_id FROM ops_notification_outbox WHERE dedupe_key=?",
            (ev["dedupe_key"],),
        ).fetchall()
        if rows:
            # Reset a PENDING per ri-testare
            conn.execute(
                "UPDATE ops_notification_outbox SET status='PENDING', attempts=0, last_error=NULL WHERE dedupe_key=?",
                (ev["dedupe_key"],),
            )
            print(f"  RESET  {ev['notification_type']} ({ev['dedupe_key']})")
        else:
            conn.execute(
                """
                INSERT INTO ops_notification_outbox
                    (notification_type, destination, payload_json, priority, status,
                     dedupe_key, attempts, created_at)
                VALUES (?,?,?,?,'PENDING',?,0,?)
                """,
                (
                    ev["notification_type"],
                    ev["destination"],
                    json.dumps(ev["payload"]),
                    ev["priority"],
                    ev["dedupe_key"],
                    now,
                ),
            )
            inserted += 1
            print(f"  INSERT {ev['notification_type']} ({ev['dedupe_key']})")
    conn.commit()
    conn.close()
    return inserted


async def run(mode: str | None) -> None:
    print("\n=== Control Plane — Live Dispatcher Test ===\n")

    # 1. Config
    try:
        cfg = load_control_plane_config(CONFIG_PATH)
    except ControlPlaneConfigError as e:
        print(f"CONFIG ERROR: {e}")
        print("Controlla le env var: CONTROL_TELEGRAM_BOT_TOKEN, CONTROL_TELEGRAM_CHAT_ID, CONTROL_TELEGRAM_USER_ID")
        sys.exit(1)

    if mode:
        cfg = cfg.model_copy(update={"delivery_mode": mode})
    print(f"  delivery_mode : {cfg.delivery_mode}")
    print(f"  chat_id       : {cfg.chat_id}")
    if cfg.delivery_mode == "supergroup_topics":
        print(f"  clean_log thread_id: {cfg.topics.clean_log.thread_id}")

    # 2. Migrations
    print("\n[1] Applying migrations...")
    _apply_migrations(OPS_DB)

    # 3. Insert test events
    print("\n[2] Inserting test events into outbox...")
    _insert_test_events(OPS_DB, TEST_EVENTS)

    # 4. Build dispatcher
    print("\n[3] Sending via dispatcher...")
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    request = HTTPXRequest(httpx_kwargs={"verify": False})
    bot = Bot(token=cfg.token, request=request)
    sender = TelegramBotSender(bot)
    router = TopicRouter(cfg)
    dispatcher = TelegramNotificationDispatcher(
        config=cfg,
        ops_db_path=OPS_DB,
        topic_router=router,
        sender=sender,
    )

    sent = await dispatcher.drain_once()
    print(f"\n  Inviati: {sent}/{len(TEST_EVENTS)}")

    # 5. Report DB state
    conn = sqlite3.connect(OPS_DB)
    rows = conn.execute(
        "SELECT notification_type, status, attempts, last_error FROM ops_notification_outbox "
        "WHERE dedupe_key LIKE 'live_test:%' ORDER BY notification_id"
    ).fetchall()
    conn.close()

    print("\n[4] Stato finale outbox:")
    print(f"  {'TYPE':<25} {'STATUS':<8} {'ATTEMPTS':<9} ERROR")
    for ntype, status, attempts, err in rows:
        err_str = (err[:60] + "...") if err and len(err) > 60 else (err or "")
        print(f"  {ntype:<25} {status:<8} {attempts:<9} {err_str}")

    print("\n=== Done ===\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["supergroup_topics", "private_bot"], default=None,
                        help="Override delivery_mode from config")
    args = parser.parse_args()
    asyncio.run(run(args.mode))
