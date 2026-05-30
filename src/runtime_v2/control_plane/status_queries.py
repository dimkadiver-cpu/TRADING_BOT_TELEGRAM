# src/runtime_v2/control_plane/status_queries.py
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone

_ACTIVE_STATES = ("OPEN", "PARTIALLY_CLOSED", "WAITING_ENTRY", "REVIEW_REQUIRED",
                  "BE_MOVE_PENDING", "PROTECTED_BE")


@dataclass
class StatusView:
    updated_at: str
    control_mode: str
    new_entries_enabled: bool
    sync_age_seconds: float | None
    open_count: int
    partial_count: int
    waiting_entry_count: int
    review_count: int
    pending_commands: int
    failed_commands: int
    no_sl_count: int


@dataclass
class TradeRow:
    chain_id: int
    symbol: str
    side: str
    state: str
    has_sl: bool


@dataclass
class TradesView:
    updated_at: str
    total: int
    rows: list[TradeRow] = field(default_factory=list)


@dataclass
class TradeDetail:
    chain_id: int
    symbol: str
    side: str
    trader_id: str
    account_id: str
    state: str
    entry_avg_price: float | None
    current_stop_price: float | None
    last_events: list[str] = field(default_factory=list)


@dataclass
class HealthView:
    updated_at: str
    workers: list[tuple[str, str, str]]
    db_ok: bool
    exchange_connected: bool
    last_event_age_seconds: float | None


@dataclass
class BlockInfo:
    scope_type: str
    scope_value: str | None
    mode: str
    created_at: str | None


@dataclass
class ControlView:
    new_entries_enabled: bool
    active_blocks: list[BlockInfo] = field(default_factory=list)
    blacklist_global: list[str] = field(default_factory=list)
    blacklist_per_trader: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class ReviewItem:
    chain_id: int | None
    symbol: str | None
    reason: str


@dataclass
class ReviewsView:
    updated_at: str
    items: list[ReviewItem] = field(default_factory=list)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def _age_seconds(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds()


class StatusQueries:
    def __init__(self, ops_db_path: str) -> None:
        self._db = ops_db_path

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db)

    def get_status(self) -> StatusView:
        conn = self._connect()
        try:
            def _count(state: str) -> int:
                return conn.execute(
                    "SELECT COUNT(*) FROM ops_trade_chains WHERE lifecycle_state=?",
                    (state,),
                ).fetchone()[0]

            open_count = _count("OPEN")
            partial_count = _count("PARTIALLY_CLOSED")
            waiting = _count("WAITING_ENTRY")
            review = _count("REVIEW_REQUIRED")
            pending = conn.execute(
                "SELECT COUNT(*) FROM ops_execution_commands WHERE status='PENDING'"
            ).fetchone()[0]
            failed = conn.execute(
                "SELECT COUNT(*) FROM ops_execution_commands WHERE status='FAILED'"
            ).fetchone()[0]
            no_sl = conn.execute(
                "SELECT COUNT(*) FROM ops_trade_chains "
                "WHERE lifecycle_state IN ('OPEN','PARTIALLY_CLOSED') "
                "AND current_stop_price IS NULL"
            ).fetchone()[0]
            last_event_ts = conn.execute(
                "SELECT MAX(received_at) FROM ops_exchange_events"
            ).fetchone()[0]
        finally:
            conn.close()

        control = self.get_control()
        control_mode = "NONE"
        if control.active_blocks:
            if any(block.mode == "FULL_STOP" for block in control.active_blocks):
                control_mode = "FULL_STOP"
            else:
                control_mode = "BLOCK_NEW_ENTRIES"
        return StatusView(
            updated_at=_now_iso(),
            control_mode=control_mode,
            new_entries_enabled=control.new_entries_enabled,
            sync_age_seconds=_age_seconds(last_event_ts),
            open_count=open_count,
            partial_count=partial_count,
            waiting_entry_count=waiting,
            review_count=review,
            pending_commands=pending,
            failed_commands=failed,
            no_sl_count=no_sl,
        )

    def get_open_trades(self) -> TradesView:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT trade_chain_id, symbol, side, lifecycle_state, current_stop_price "
                "FROM ops_trade_chains "
                "WHERE lifecycle_state IN ({}) "
                "ORDER BY trade_chain_id".format(
                    ",".join("?" * len(_ACTIVE_STATES))
                ),
                _ACTIVE_STATES,
            ).fetchall()
        finally:
            conn.close()
        trade_rows = [
            TradeRow(chain_id=r[0], symbol=r[1], side=r[2], state=r[3], has_sl=r[4] is not None)
            for r in rows
        ]
        return TradesView(updated_at=_now_iso(), total=len(trade_rows), rows=trade_rows)

    def get_trade(self, chain_id: int) -> TradeDetail | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT trade_chain_id, symbol, side, trader_id, account_id, "
                "lifecycle_state, entry_avg_price, current_stop_price "
                "FROM ops_trade_chains WHERE trade_chain_id=?",
                (chain_id,),
            ).fetchone()
            if row is None:
                return None
            events = conn.execute(
                "SELECT created_at, event_type FROM ops_lifecycle_events "
                "WHERE trade_chain_id=? ORDER BY event_id DESC LIMIT 3",
                (chain_id,),
            ).fetchall()
        finally:
            conn.close()
        last_events = []
        for created_at, etype in reversed(events):
            hhmm = created_at[11:16] if created_at and len(created_at) >= 16 else ""
            last_events.append(f"{hhmm} {etype}".strip())
        return TradeDetail(
            chain_id=row[0], symbol=row[1], side=row[2], trader_id=row[3],
            account_id=row[4], state=row[5], entry_avg_price=row[6],
            current_stop_price=row[7], last_events=last_events,
        )

    def get_health(self) -> HealthView:
        conn = self._connect()
        try:
            try:
                conn.execute("SELECT 1 FROM ops_trade_chains LIMIT 1").fetchone()
                db_ok = True
            except sqlite3.Error:
                db_ok = False
            last_event_ts = conn.execute(
                "SELECT MAX(received_at) FROM ops_exchange_events"
            ).fetchone()[0]
        finally:
            conn.close()
        age = _age_seconds(last_event_ts)
        sync_status = "OK" if (age is None or age < 60) else "WARNING"
        workers = [
            ("Parser pipeline", "OK", ""),
            ("Lifecycle gate", "OK", ""),
            ("Execution worker", "OK", ""),
            ("Exchange sync", sync_status, f"last event {int(age)}s ago" if age is not None else "no events"),
            ("Notification disp.", "OK", ""),
        ]
        return HealthView(
            updated_at=_now_iso(),
            workers=workers,
            db_ok=db_ok,
            exchange_connected=(age is not None and age < 120),
            last_event_age_seconds=age,
        )

    def get_control(self) -> ControlView:
        conn = self._connect()
        try:
            block_rows = conn.execute(
                "SELECT scope_type, scope_value, execution_pause_mode, created_at "
                "FROM ops_control_state "
                "WHERE active=1 AND execution_pause_mode IN ('BLOCK_NEW_ENTRIES','FULL_STOP')"
            ).fetchall()
            override_rows = conn.execute(
                "SELECT override_key, scope_type, scope_value, value_json "
                "FROM ops_config_overrides WHERE active=1 AND override_key LIKE 'symbol_blacklist%'"
            ).fetchall()
        finally:
            conn.close()

        blocks = [
            BlockInfo(scope_type=r[0], scope_value=r[1], mode=r[2], created_at=r[3])
            for r in block_rows
        ]
        new_entries_enabled = len(blocks) == 0

        blacklist_global: list[str] = []
        blacklist_per_trader: dict[str, list[str]] = {}
        for _key, scope_type, scope_value, value_json in override_rows:
            try:
                symbols = json.loads(value_json or "[]")
            except Exception:
                symbols = []
            if scope_type == "GLOBAL":
                blacklist_global = list(symbols)
            elif scope_type == "PER_TRADER" and scope_value:
                blacklist_per_trader[scope_value] = list(symbols)

        return ControlView(
            new_entries_enabled=new_entries_enabled,
            active_blocks=blocks,
            blacklist_global=blacklist_global,
            blacklist_per_trader=blacklist_per_trader,
        )

    def get_reviews(self) -> ReviewsView:
        conn = self._connect()
        try:
            chain_rows = conn.execute(
                "SELECT trade_chain_id, symbol FROM ops_trade_chains "
                "WHERE lifecycle_state='REVIEW_REQUIRED' ORDER BY trade_chain_id"
            ).fetchall()
            reasons = dict(conn.execute(
                "SELECT trade_chain_id, payload_json FROM ops_lifecycle_events "
                "WHERE event_type='REVIEW_REQUIRED' AND trade_chain_id IS NOT NULL "
                "ORDER BY event_id"
            ).fetchall())
        finally:
            conn.close()
        items: list[ReviewItem] = []
        for cid, symbol in chain_rows:
            reason = "review_required"
            raw = reasons.get(cid)
            if raw:
                try:
                    reason = json.loads(raw).get("reason", reason)
                except Exception:
                    pass
            items.append(ReviewItem(chain_id=cid, symbol=symbol, reason=reason))
        return ReviewsView(updated_at=_now_iso(), items=items)


__all__ = [
    "StatusQueries", "StatusView", "TradesView", "TradeRow", "TradeDetail",
    "HealthView", "ControlView", "BlockInfo", "ReviewsView", "ReviewItem",
]
