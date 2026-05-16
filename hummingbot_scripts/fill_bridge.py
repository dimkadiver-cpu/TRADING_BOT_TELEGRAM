# hummingbot_scripts/fill_bridge.py
"""
FillBridge — script opzionale che gira dentro Hummingbot come ScriptStrategyBase.
Scrive fill direttamente in ops_exchange_events via SQLite appena Hummingbot
riceve un fill dall'exchange. Zero latency, nessun polling.

Deploy: copiare in scripts/ di Hummingbot e configurare OPS_DB_PATH.
Upgrade dal polling smart — non richiede modifiche al gateway o al DB.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone

# Import Hummingbot — disponibili solo dentro il processo Hummingbot
try:
    from hummingbot.strategy.script_strategy_base import ScriptStrategyBase
    from hummingbot.core.event.events import OrderFilledEvent
    HUMMINGBOT_AVAILABLE = True
except ImportError:
    HUMMINGBOT_AVAILABLE = False
    class ScriptStrategyBase:  # type: ignore[no-redef]
        pass

OPS_DB_PATH = os.environ.get("OPS_DB_PATH", "ops.sqlite3")
_PREFIX = "tsb"

_ROLE_EVENT_MAP = {
    "entry": "ENTRY_FILLED",
    "sl": "SL_FILLED",
    "tp": "TP_FILLED",
}


def _parse_chain_id(client_order_id: str) -> int | None:
    parts = client_order_id.split(":")
    if len(parts) == 5 and parts[0] == _PREFIX:
        try:
            return int(parts[1])
        except ValueError:
            pass
    return None


def _parse_role(client_order_id: str) -> str | None:
    parts = client_order_id.split(":")
    return parts[3] if len(parts) == 5 else None


def _parse_sequence(client_order_id: str) -> int:
    parts = client_order_id.split(":")
    try:
        return int(parts[4]) if len(parts) == 5 else 1
    except ValueError:
        return 1


class FillBridge(ScriptStrategyBase):
    def on_order_filled(self, event: "OrderFilledEvent") -> None:  # type: ignore[override]
        coid = getattr(event, "client_order_id", None) or ""
        if not coid.startswith(_PREFIX + ":"):
            return

        chain_id = _parse_chain_id(coid)
        role = _parse_role(coid)
        if chain_id is None or role is None:
            return

        event_type = _ROLE_EVENT_MAP.get(role)
        if event_type is None:
            return

        price = float(getattr(event, "price", 0) or 0)
        qty = float(getattr(event, "amount", 0) or 0)
        exchange_order_id = str(getattr(event, "exchange_order_id", coid))
        sequence = _parse_sequence(coid)

        if event_type == "TP_FILLED":
            payload = {
                "tp_level": sequence,
                "is_final": False,
                "fill_price": price,
                "filled_qty": qty,
            }
        else:
            payload = {"fill_price": price, "filled_qty": qty}

        idempotency_key = f"{event_type}:{chain_id}:{exchange_order_id}"
        now = datetime.now(timezone.utc).isoformat()

        try:
            conn = sqlite3.connect(OPS_DB_PATH)
            conn.execute(
                "INSERT OR IGNORE INTO ops_exchange_events "
                "(trade_chain_id, event_type, payload_json, processing_status, "
                "idempotency_key, received_at) VALUES (?,?,?,?,?,?)",
                (chain_id, event_type, json.dumps(payload), "NEW", idempotency_key, now),
            )
            conn.commit()
            conn.close()
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error("FillBridge write error: %s", exc)
