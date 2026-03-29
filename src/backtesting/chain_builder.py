"""SignalChainBuilder: reconstructs SignalChain objects from the backtest DB.

Reads operational_signals + parse_results + raw_messages and assembles a list
of SignalChain instances, each containing a NEW_SIGNAL and all its linked UPDATEs.

Usage:
    chains = SignalChainBuilder.build_all(
        db_path="db/backtest.sqlite3",
        trader_id="trader_3",
        date_from="2025-01-01",
        date_to="2025-12-31",
    )
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

import aiosqlite

from src.backtesting.models import ChainedMessage, SignalChain
from src.parser.models.new_signal import NewSignalEntities
from src.parser.models.update import UpdateEntities

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

_NEW_SIGNAL_QUERY = """
    SELECT
        rm.raw_message_id,
        pr.parse_result_id,
        rm.telegram_message_id,
        rm.message_ts,
        os.message_type,
        os.op_signal_id,
        os.attempt_key,
        os.is_blocked,
        os.block_reason,
        os.risk_budget_usdt,
        os.position_size_usdt,
        os.entry_split_json,
        os.management_rules_json,
        pr.parse_result_normalized_json,
        rm.source_chat_id,
        rm.source_trader_id AS trader_id_raw,
        os.trader_id,
        s.symbol,
        s.side
    FROM operational_signals os
    JOIN parse_results pr ON pr.parse_result_id = os.parse_result_id
    JOIN raw_messages rm ON rm.raw_message_id = pr.raw_message_id
    LEFT JOIN signals s ON s.attempt_key = os.attempt_key
    WHERE os.message_type = 'NEW_SIGNAL'
"""

_UPDATE_QUERY = """
    SELECT
        rm.raw_message_id,
        pr.parse_result_id,
        rm.telegram_message_id,
        rm.message_ts,
        os.message_type,
        os.op_signal_id,
        os.attempt_key,
        os.is_blocked,
        os.block_reason,
        os.risk_budget_usdt,
        os.position_size_usdt,
        os.entry_split_json,
        os.management_rules_json,
        pr.parse_result_normalized_json,
        rm.source_chat_id,
        os.trader_id,
        os.resolved_target_ids,
        rm.reply_to_message_id
    FROM operational_signals os
    JOIN parse_results pr ON pr.parse_result_id = os.parse_result_id
    JOIN raw_messages rm ON rm.raw_message_id = pr.raw_message_id
    WHERE os.message_type = 'UPDATE'
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_ts(raw: str) -> datetime:
    """Parse an ISO-8601 timestamp string to a timezone-aware UTC datetime."""
    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _parse_intents(normalized_json: str | None) -> list[str]:
    """Extract intent names from parse_result_normalized_json."""
    if not normalized_json:
        return []
    try:
        data = json.loads(normalized_json)
        intents_raw = data.get("intents", [])
        result: list[str] = []
        for item in intents_raw:
            if isinstance(item, dict):
                name = item.get("name")
                if name:
                    result.append(str(name))
            elif isinstance(item, str):
                result.append(item)
        return result
    except (json.JSONDecodeError, AttributeError):
        return []


def _price_obj(v: float) -> dict:
    """Build a Price-compatible dict from a raw float."""
    return {"raw": str(v), "value": v}


def _normalize_new_signal_entities(raw: dict) -> dict:
    """Normalize legacy flat-float entity format to the nested Pydantic structure.

    Older parse_result_normalized_json records store prices as plain floats:
        stop_loss: 104103.0
        take_profits: [109600.0, ...]
        entry: [105200.0, ...]   (field name may vary)

    NewSignalEntities expects:
        stop_loss: {"price": {"raw": "104103.0", "value": 104103.0}}
        take_profits: [{"price": {...}}]
        entries: [{"price": {...}, "order_type": "LIMIT"}]
    """
    out = dict(raw)

    # stop_loss: float → StopLoss dict
    sl = out.get("stop_loss")
    if isinstance(sl, (int, float)):
        out["stop_loss"] = {"price": _price_obj(float(sl))}

    # take_profits: list[float] → list[TakeProfit dict]
    tps = out.get("take_profits")
    if isinstance(tps, list) and tps and isinstance(tps[0], (int, float)):
        out["take_profits"] = [{"price": _price_obj(float(tp))} for tp in tps]

    # entries: normalize any legacy format to [{price: Price, order_type: str}]
    entries = out.get("entries")
    if not entries:
        # legacy "entry" field: float or list of floats
        entry_raw = out.get("entry")
        if isinstance(entry_raw, (int, float)):
            out["entries"] = [{"price": _price_obj(float(entry_raw)), "order_type": "LIMIT"}]
        elif isinstance(entry_raw, list) and entry_raw:
            out["entries"] = [
                {"price": _price_obj(float(e)), "order_type": "LIMIT"}
                for e in entry_raw
                if isinstance(e, (int, float))
            ]
    elif isinstance(entries, list) and entries:
        normalized_entries = []
        for e in entries:
            if isinstance(e, (int, float)):
                # bare float
                normalized_entries.append({"price": _price_obj(float(e)), "order_type": "LIMIT"})
            elif isinstance(e, dict):
                entry_copy = dict(e)
                # price: float → Price dict
                p = entry_copy.get("price")
                if isinstance(p, (int, float)):
                    entry_copy["price"] = _price_obj(float(p))
                # ensure order_type present
                if "order_type" not in entry_copy:
                    entry_copy["order_type"] = "LIMIT"
                normalized_entries.append(entry_copy)
            else:
                normalized_entries.append(e)
        out["entries"] = normalized_entries

    return out


def _normalize_update_entities(raw: dict) -> dict:
    """Normalize legacy flat-float Price fields in UPDATE entities."""
    out = dict(raw)
    for field in ("new_sl_level", "close_price"):
        v = out.get(field)
        if isinstance(v, (int, float)):
            out[field] = _price_obj(float(v))
    return out


def _deserialize_entities(
    normalized_json: str | None,
    message_type: str,
) -> NewSignalEntities | UpdateEntities | None:
    """Deserialize parse_result_normalized_json into the appropriate Pydantic model."""
    if not normalized_json:
        return None
    try:
        data = json.loads(normalized_json)
        entities_raw = data.get("entities")
        if entities_raw is None:
            return None
        if message_type == "NEW_SIGNAL":
            return NewSignalEntities.model_validate(
                _normalize_new_signal_entities(entities_raw)
            )
        if message_type == "UPDATE":
            return UpdateEntities.model_validate(
                _normalize_update_entities(entities_raw)
            )
        return None
    except (json.JSONDecodeError, Exception) as exc:
        logger.warning("Failed to deserialize entities for %s: %s", message_type, exc)
        return None


def _row_to_chained_message(row: aiosqlite.Row, message_type: str) -> ChainedMessage:
    """Convert a DB row (new signal or update query) to a ChainedMessage."""
    normalized_json: str | None = row["parse_result_normalized_json"]

    entry_split: dict[str, float] | None = None
    if row["entry_split_json"]:
        try:
            entry_split = json.loads(row["entry_split_json"])
        except json.JSONDecodeError:
            pass

    management_rules: dict | None = None
    if row["management_rules_json"]:
        try:
            management_rules = json.loads(row["management_rules_json"])
        except json.JSONDecodeError:
            pass

    return ChainedMessage(
        raw_message_id=row["raw_message_id"],
        parse_result_id=row["parse_result_id"],
        telegram_message_id=row["telegram_message_id"],
        message_ts=_parse_ts(row["message_ts"]),
        message_type=message_type,  # type: ignore[arg-type]
        intents=_parse_intents(normalized_json),
        entities=_deserialize_entities(normalized_json, message_type),
        op_signal_id=row["op_signal_id"],
        attempt_key=row["attempt_key"],
        is_blocked=bool(row["is_blocked"]),
        block_reason=row["block_reason"],
        risk_budget_usdt=row["risk_budget_usdt"],
        position_size_usdt=row["position_size_usdt"],
        entry_split=entry_split,
        management_rules=management_rules,
    )


def _close_ts_from_updates(updates: list[ChainedMessage]) -> datetime | None:
    """Return the timestamp of U_CLOSE_FULL or U_SL_HIT, if present."""
    close_intents = {"U_CLOSE_FULL", "U_SL_HIT"}
    for upd in sorted(updates, key=lambda u: u.message_ts):
        if any(i in close_intents for i in upd.intents):
            return upd.message_ts
    return None


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

class SignalChainBuilder:
    """Reconstructs SignalChain objects from the backtest (or live) database."""

    @classmethod
    async def build_all_async(
        cls,
        db_path: str,
        trader_id: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[SignalChain]:
        """Async implementation — returns list[SignalChain].

        Args:
            db_path:    Path to the SQLite database file.
            trader_id:  Filter by trader, or None for all traders.
            date_from:  ISO date string (inclusive), e.g. "2025-01-01".
            date_to:    ISO date string (inclusive), e.g. "2025-12-31".
        """
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row

            # ------------------------------------------------------------------
            # Pass 1 — load all NEW_SIGNAL rows
            # ------------------------------------------------------------------
            new_signal_query = _NEW_SIGNAL_QUERY
            params: list[str] = []

            conditions: list[str] = []
            if trader_id is not None:
                conditions.append("os.trader_id = ?")
                params.append(trader_id)
            if date_from is not None:
                conditions.append("rm.message_ts >= ?")
                params.append(date_from)
            if date_to is not None:
                conditions.append("rm.message_ts <= ?")
                params.append(date_to + "T23:59:59" if "T" not in date_to else date_to)

            if conditions:
                new_signal_query += " AND " + " AND ".join(conditions)
            new_signal_query += " ORDER BY rm.message_ts ASC"

            async with db.execute(new_signal_query, params) as cursor:
                ns_rows = await cursor.fetchall()

            # op_signal_id → ChainedMessage, for linking UPDATEs
            op_id_to_new_signal: dict[int, ChainedMessage] = {}
            # telegram_message_id → op_signal_id, for fallback via reply_to_message_id
            # keyed by (source_chat_id, telegram_message_id) for correct scoping
            chat_tg_to_op_id: dict[tuple[str, int], int] = {}

            new_signal_by_op_id: dict[int, dict] = {}
            for row in ns_rows:
                cm = _row_to_chained_message(row, "NEW_SIGNAL")
                if cm.op_signal_id is not None:
                    op_id_to_new_signal[cm.op_signal_id] = cm
                    new_signal_by_op_id[cm.op_signal_id] = {
                        "chained": cm,
                        "row": row,
                    }
                    chat_tg_to_op_id[(row["source_chat_id"], row["telegram_message_id"])] = cm.op_signal_id

            # ------------------------------------------------------------------
            # Pass 2 — load all UPDATE rows, link to NEW_SIGNALs
            # ------------------------------------------------------------------
            update_query = _UPDATE_QUERY
            update_params: list[str] = []
            update_conditions: list[str] = []

            if trader_id is not None:
                update_conditions.append("os.trader_id = ?")
                update_params.append(trader_id)
            if date_from is not None:
                update_conditions.append("rm.message_ts >= ?")
                update_params.append(date_from)
            if date_to is not None:
                update_conditions.append("rm.message_ts <= ?")
                update_params.append(date_to + "T23:59:59" if "T" not in date_to else date_to)

            if update_conditions:
                update_query += " AND " + " AND ".join(update_conditions)
            update_query += " ORDER BY rm.message_ts ASC"

            async with db.execute(update_query, update_params) as cursor:
                upd_rows = await cursor.fetchall()

            # op_signal_id → list[ChainedMessage updates]
            updates_by_new_signal: dict[int, list[ChainedMessage]] = {}

            for row in upd_rows:
                cm = _row_to_chained_message(row, "UPDATE")

                linked_op_id: int | None = None

                # Strategy 1: resolved_target_ids (JSON list of op_signal_ids)
                resolved_raw: str | None = row["resolved_target_ids"]
                if resolved_raw:
                    try:
                        resolved_ids: list[int] = json.loads(resolved_raw)
                        for rid in resolved_ids:
                            if rid in op_id_to_new_signal:
                                linked_op_id = rid
                                break
                    except (json.JSONDecodeError, TypeError):
                        pass

                # Strategy 2: fallback via reply_to_message_id
                if linked_op_id is None:
                    reply_id: int | None = row["reply_to_message_id"]
                    if reply_id is not None:
                        source_chat = row["source_chat_id"]
                        candidate = chat_tg_to_op_id.get((source_chat, reply_id))
                        if candidate is not None:
                            linked_op_id = candidate

                if linked_op_id is None:
                    logger.warning(
                        "UPDATE op_signal_id=%s could not be linked to any NEW_SIGNAL "
                        "(tg_msg_id=%s, resolved_target_ids=%r, reply_to=%s) — skipping",
                        cm.op_signal_id,
                        cm.telegram_message_id,
                        resolved_raw,
                        row["reply_to_message_id"],
                    )
                    continue

                updates_by_new_signal.setdefault(linked_op_id, []).append(cm)

        # ------------------------------------------------------------------
        # Pass 3 — assemble SignalChain per attempt_key
        # ------------------------------------------------------------------
        chains: list[SignalChain] = []

        for op_id, info in new_signal_by_op_id.items():
            cm: ChainedMessage = info["chained"]
            row = info["row"]

            symbol: str | None = row["symbol"]
            side: str | None = row["side"]

            if not symbol or not side:
                logger.warning(
                    "NEW_SIGNAL op_signal_id=%s missing symbol or side — skipping chain",
                    op_id,
                )
                continue

            if cm.attempt_key is None:
                logger.warning(
                    "NEW_SIGNAL op_signal_id=%s has no attempt_key — skipping chain",
                    op_id,
                )
                continue

            # Extract prices from NewSignalEntities
            entry_prices: list[float] = []
            sl_price: float = 0.0
            tp_prices: list[float] = []

            if isinstance(cm.entities, NewSignalEntities):
                entities = cm.entities
                entry_prices = [
                    e.price.value
                    for e in entities.entries
                    if e.price is not None
                ]
                if entities.stop_loss is not None:
                    sl_price = entities.stop_loss.price.value
                tp_prices = [tp.price.value for tp in entities.take_profits]

            updates = sorted(
                updates_by_new_signal.get(op_id, []),
                key=lambda u: u.message_ts,
            )

            close_ts = _close_ts_from_updates(updates)

            chain_id = f"{cm.entities.symbol if isinstance(cm.entities, NewSignalEntities) and cm.entities.symbol else symbol}:{cm.attempt_key}"
            chain_id = f"{row['trader_id']}:{cm.attempt_key}"

            chains.append(
                SignalChain(
                    chain_id=chain_id,
                    trader_id=row["trader_id"],
                    symbol=symbol,
                    side=side,
                    new_signal=cm,
                    updates=updates,
                    entry_prices=entry_prices,
                    sl_price=sl_price,
                    tp_prices=tp_prices,
                    open_ts=cm.message_ts,
                    close_ts=close_ts,
                )
            )

        return chains

    @classmethod
    def build_all(
        cls,
        db_path: str,
        trader_id: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[SignalChain]:
        """Synchronous wrapper around build_all_async.

        Runs the async implementation in a new event loop, making it easy to
        call from scripts and test fixtures that don't manage an event loop.
        """
        return asyncio.run(
            cls.build_all_async(
                db_path=db_path,
                trader_id=trader_id,
                date_from=date_from,
                date_to=date_to,
            )
        )
