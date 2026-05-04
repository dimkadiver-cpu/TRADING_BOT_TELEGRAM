from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class SignalLifecycle:
    ref_message_id: int
    new_signal_message_id: int | None
    ordered_history: list[str]
    is_terminal: bool


class HistoryProvider(Protocol):
    def get_signal_lifecycle(
        self,
        *,
        ref_message_id: int,
        source_chat_id: str | None = None,
    ) -> SignalLifecycle: ...


class SQLiteHistoryProvider:
    _TERMINAL_EVENTS = {"CLOSE_FULL", "EXIT_BE", "INVALIDATE_SETUP", "SL_HIT"}

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    def get_signal_lifecycle(
        self,
        *,
        ref_message_id: int,
        source_chat_id: str | None = None,
    ) -> SignalLifecycle:
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                WITH RECURSIVE
                seed AS (
                  SELECT
                    raw_message_id,
                    source_chat_id,
                    telegram_message_id,
                    reply_to_message_id,
                    message_ts
                  FROM raw_messages
                  WHERE telegram_message_id = ?
                    AND (? IS NULL OR source_chat_id = ?)
                  ORDER BY message_ts DESC, raw_message_id DESC
                  LIMIT 1
                ),
                chain AS (
                  SELECT * FROM seed
                  UNION ALL
                  SELECT
                    parent.raw_message_id,
                    parent.source_chat_id,
                    parent.telegram_message_id,
                    parent.reply_to_message_id,
                    parent.message_ts
                  FROM raw_messages parent
                  JOIN chain child
                    ON parent.source_chat_id = child.source_chat_id
                   AND parent.telegram_message_id = child.reply_to_message_id
                )
                SELECT
                  chain.raw_message_id,
                  chain.telegram_message_id,
                  chain.message_ts,
                  pm.primary_class,
                  pm.parsed_json,
                  pm.intents_confirmed_json
                FROM chain
                LEFT JOIN parsed_messages pm
                  ON pm.raw_message_id = chain.raw_message_id
                ORDER BY chain.message_ts ASC, chain.raw_message_id ASC
                """,
                (ref_message_id, source_chat_id, source_chat_id),
            ).fetchall()

        if not rows:
            return SignalLifecycle(
                ref_message_id=ref_message_id,
                new_signal_message_id=None,
                ordered_history=[],
                is_terminal=False,
            )

        new_signal_message_id: int | None = None
        ordered_history: list[str] = []

        for row in rows:
            parsed_json = _loads_json_object(row["parsed_json"])
            parse_status = parsed_json.get("parse_status")
            if (
                row["primary_class"] == "SIGNAL"
                and parse_status in {"PARSED", "PARTIAL"}
                and new_signal_message_id is None
            ):
                new_signal_message_id = int(row["telegram_message_id"])
                ordered_history.append("NEW_SIGNAL")

            ordered_history.extend(_loads_json_list(row["intents_confirmed_json"]))

        return SignalLifecycle(
            ref_message_id=ref_message_id,
            new_signal_message_id=new_signal_message_id,
            ordered_history=ordered_history,
            is_terminal=any(event in self._TERMINAL_EVENTS for event in ordered_history),
        )


def _loads_json_object(raw: str | None) -> dict[str, object]:
    if not raw:
        return {}
    data = json.loads(raw)
    return data if isinstance(data, dict) else {}


def _loads_json_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    data = json.loads(raw)
    if not isinstance(data, list):
        return []
    return [str(item) for item in data]
