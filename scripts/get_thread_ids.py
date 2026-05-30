"""
Legge gli ultimi update del bot e mostra chat_id + message_thread_id.
Scrivi un messaggio in ogni topic del supergruppo, poi esegui questo script.

Usage:
    python scripts/get_thread_ids.py
"""
from __future__ import annotations

import asyncio
import ssl
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from telegram import Bot
from telegram.request import HTTPXRequest


async def main() -> None:
    import os
    token = os.environ.get("CONTROL_TELEGRAM_BOT_TOKEN")
    if not token:
        print("CONTROL_TELEGRAM_BOT_TOKEN non trovato in .env")
        sys.exit(1)

    request = HTTPXRequest(httpx_kwargs={"verify": False})
    bot = Bot(token=token, request=request)

    updates = await bot.get_updates(limit=20, timeout=10)
    if not updates:
        print("Nessun update trovato.")
        print("Scrivi un messaggio in ogni topic del supergruppo, poi riesegui.")
        return

    seen = set()
    print(f"\n{'CHAT_ID':<20} {'THREAD_ID':<12} {'CHAT_TITLE':<30} TESTO")
    print("-" * 80)
    for u in updates:
        msg = u.message or u.channel_post
        if not msg:
            continue
        key = (msg.chat.id, msg.message_thread_id)
        if key in seen:
            continue
        seen.add(key)
        title = msg.chat.title or msg.chat.username or "—"
        text = (msg.text or "")[:40]
        print(f"{msg.chat.id:<20} {str(msg.message_thread_id):<12} {title:<30} {text}")

    await bot.close()


asyncio.run(main())
