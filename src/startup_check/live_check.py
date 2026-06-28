"""Verifica di connettività Telegram per --check-live.

Per ogni destinazione (chat_id, thread_id) configurata in telegram_control.yaml:
  send messaggio di test → verifica message_id → delete.
Produce un ValidationReport senza modificare stato persistente.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from src.startup_check.validator import ValidationReport


_SECTION = "Connettività Telegram"
_PROBE_TEXT = "⚙️ TeleSignalBot connectivity check (auto-cancellato)"
_TIMEOUT = 12.0


@dataclass(frozen=True)
class _Destination:
    chat_id: int
    thread_id: int | None
    label: str


def _collect_destinations(config) -> list[_Destination]:
    """Estrae tutte le destinazioni uniche (chat_id, thread_id) dalla config."""
    seen: set[tuple[int, int | None]] = set()
    result: list[_Destination] = []

    def _add(chat_id: int, thread_id: int | None, label: str) -> None:
        key = (chat_id, thread_id)
        if key in seen:
            return
        seen.add(key)
        result.append(_Destination(chat_id=chat_id, thread_id=thread_id, label=label))

    for account_id, account in config.per_account.items():
        cid = account.chat_id
        topics = account.topics
        _add(cid, topics.tech_log.thread_id, f"{account_id}/tech_log")
        _add(cid, topics.clean_log.thread_id, f"{account_id}/clean_log")
        _add(cid, topics.commands.thread_id, f"{account_id}/commands")
        for trader_id, tid in (topics.clean_log.per_trader or {}).items():
            _add(cid, tid, f"{account_id}/clean_log/{trader_id}")

    return result


async def _probe(bot, dest: _Destination, report: ValidationReport) -> None:
    label = f"({dest.chat_id}, thread={dest.thread_id}) [{dest.label}]"
    try:
        msg = await asyncio.wait_for(
            bot.send_message(
                chat_id=dest.chat_id,
                message_thread_id=dest.thread_id,
                text=_PROBE_TEXT,
            ),
            timeout=_TIMEOUT,
        )
    except asyncio.TimeoutError:
        report.warn(_SECTION, f"{label}: timeout send ({_TIMEOUT}s) — rete lenta o bot bloccato")
        return
    except Exception as exc:
        report.error(_SECTION, f"{label}: send fallita — {exc}")
        return

    try:
        await asyncio.wait_for(
            bot.delete_message(chat_id=dest.chat_id, message_id=msg.message_id),
            timeout=_TIMEOUT,
        )
        report.ok(_SECTION, f"{label}: send+delete OK")
    except asyncio.TimeoutError:
        report.warn(_SECTION, f"{label}: send OK, delete timeout — messaggio rimasto nel canale")
    except Exception as exc:
        report.warn(_SECTION, f"{label}: send OK, delete fallita — {exc} (messaggio rimasto nel canale)")


async def run_live_checks(root_dir: Path) -> ValidationReport:
    """Esegue il live check di connettività e ritorna il report."""
    report = ValidationReport()

    config_path = str(root_dir / "config" / "telegram_control.yaml")
    try:
        from src.runtime_v2.control_plane.config import load_control_plane_config
        from src.runtime_v2.control_plane.notification_dispatcher import build_telegram_request
        config = load_control_plane_config(config_path)
    except Exception as exc:
        report.error(_SECTION, f"caricamento telegram_control.yaml fallito — {exc}")
        return report

    if not config.enabled:
        report.ok(_SECTION, "control plane disabilitato — live check saltato")
        return report

    try:
        from telegram import Bot
        bot = Bot(token=config.token, request=build_telegram_request())
    except Exception as exc:
        report.error(_SECTION, f"creazione Bot fallita — {exc}")
        return report

    destinations = _collect_destinations(config)
    if not destinations:
        report.warn(_SECTION, "nessuna destinazione configurata in telegram_control.yaml")
        return report

    report.ok(_SECTION, f"{len(destinations)} destinazioni da verificare")

    tasks = [_probe(bot, dest, report) for dest in destinations]
    await asyncio.gather(*tasks)

    return report


__all__ = ["run_live_checks"]
