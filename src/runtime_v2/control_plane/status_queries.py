# src/runtime_v2/control_plane/status_queries.py
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone

from src.runtime_v2.control_plane.scope_resolver import QueryScope

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
    has_be: bool = False
    entry_avg_price: float | None = None
    open_position_qty: float | None = None
    unrealized_pnl: float | None = None
    mark_price: float | None = None
    mark_captured_at: str | None = None
    cum_realized_pnl: float | None = None


@dataclass
class CloseCandidate:
    chain_id: int
    symbol: str
    side: str
    state: str
    trader_id: str
    account_id: str


@dataclass
class TradesView:
    updated_at: str
    total: int
    rows: list[TradeRow] = field(default_factory=list)
    mark_snapshot_max_age_seconds: float | None = None


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
    original_message_link: str | None = None
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


@dataclass
class PnlView:
    updated_at: str
    account_id: str | None
    captured_at: str | None
    source: str | None
    equity_usdt: float | None
    available_balance_usdt: float | None
    total_open_risk_usdt: float | None
    total_margin_used_usdt: float | None
    open_count: int
    partial_count: int
    waiting_entry_count: int
    gross_pnl: float | None = None
    total_fees: float | None = None    # fees + funding (backward compat)
    fees_usdt: float | None = None     # solo cumulative_fees
    funding_usdt: float | None = None  # solo cumulative_funding
    pnl_net: float | None = None


@dataclass
class StatsRow:
    label: str           # "Oggi", "7 giorni", "30 giorni", "Totale"
    trade_count: int
    win_pct: float | None
    pnl_net: float
    fees: float


@dataclass
class StatsView:
    updated_at: str
    rows: list[StatsRow]
    best_chain_id: int | None = None
    best_pnl: float | None = None
    best_symbol: str | None = None
    worst_chain_id: int | None = None
    worst_pnl: float | None = None
    worst_symbol: str | None = None


@dataclass
class ClosedTradeRow:
    chain_id: int
    symbol: str
    side: str
    closed_at: str | None
    gross_pnl: float | None


@dataclass
class ClosedTradesView:
    updated_at: str
    rows: list[ClosedTradeRow]
    total_count: int
    page: int
    page_size: int


@dataclass
class BlockedTradeRow:
    chain_id: int
    symbol: str
    state: str          # "REVIEW_REQUIRED" or "EXEC_FAILED"
    reason: str | None


@dataclass
class BlockedTradesView:
    updated_at: str
    rows: list[BlockedTradeRow]


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


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def _build_telegram_message_link(
    source_chat_id: str | None,
    telegram_message_id: int | None,
) -> str | None:
    if not source_chat_id or telegram_message_id is None:
        return None
    if source_chat_id.startswith("-100"):
        return f"https://t.me/c/{source_chat_id[4:]}/{telegram_message_id}"
    return None


def _extract_stop_price(*json_blobs: str | None) -> float | None:
    for blob in json_blobs:
        if not blob:
            continue
        try:
            data = json.loads(blob)
        except Exception:
            continue
        for key in ("current_stop_price", "sl_price", "stop_loss"):
            value = data.get(key)
            if value is None:
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return None


def _scope_where(scope: QueryScope, table_alias: str = "") -> tuple[str, list]:
    """Return (WHERE-fragment, params) for the given scope.

    The fragment does NOT include the leading WHERE keyword.
    account_id=None means global scope — no filter applied.
    """
    prefix = f"{table_alias}." if table_alias else ""

    # Scope globale — nessun filtro account né trader
    if scope.account_id is None and scope.trader_ids is None:
        return "1=1", []

    # Account singolo, tutti i trader
    if scope.trader_ids is None:
        return f"{prefix}account_id = ?", [scope.account_id]

    # Account singolo + trader specifici
    placeholders = ",".join("?" * len(scope.trader_ids))
    return (
        f"{prefix}account_id = ? AND {prefix}trader_id IN ({placeholders})",
        [scope.account_id, *scope.trader_ids],
    )


class StatusQueries:
    def __init__(
        self,
        ops_db_path: str,
        position_reconciliation_interval_seconds: float = 60.0,
    ) -> None:
        self._db = ops_db_path
        self._reconciliation_interval = position_reconciliation_interval_seconds

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db)

    def get_status(self, scope: QueryScope | None = None) -> StatusView:
        conn = self._connect()
        try:
            if scope is not None:
                scope_frag, scope_params = _scope_where(scope)

                def _count(state: str) -> int:
                    return conn.execute(
                        f"SELECT COUNT(*) FROM ops_trade_chains "
                        f"WHERE lifecycle_state=? AND {scope_frag}",
                        [state, *scope_params],
                    ).fetchone()[0]

                t_frag, t_params = _scope_where(scope, 't')
                pending = conn.execute(
                    f"SELECT COUNT(*) FROM ops_execution_commands ec "
                    f"JOIN ops_trade_chains t ON t.trade_chain_id = ec.trade_chain_id "
                    f"WHERE ec.status='PENDING' AND {t_frag}",
                    t_params,
                ).fetchone()[0]
                failed = conn.execute(
                    f"SELECT COUNT(*) FROM ops_execution_commands ec "
                    f"JOIN ops_trade_chains t ON t.trade_chain_id = ec.trade_chain_id "
                    f"WHERE ec.status='FAILED' AND {t_frag}",
                    t_params,
                ).fetchone()[0]
                no_sl = conn.execute(
                    f"SELECT COUNT(*) FROM ops_trade_chains "
                    f"WHERE lifecycle_state IN ('OPEN','PARTIALLY_CLOSED') "
                    f"AND current_stop_price IS NULL AND {scope_frag}",
                    scope_params,
                ).fetchone()[0]
            else:
                def _count(state: str) -> int:
                    return conn.execute(
                        "SELECT COUNT(*) FROM ops_trade_chains WHERE lifecycle_state=?",
                        (state,),
                    ).fetchone()[0]

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

            open_count = _count("OPEN")
            partial_count = _count("PARTIALLY_CLOSED")
            waiting = _count("WAITING_ENTRY")
            review = _count("REVIEW_REQUIRED")
            last_event_ts = conn.execute(
                "SELECT MAX(received_at) FROM ops_exchange_events"
            ).fetchone()[0]
        finally:
            conn.close()

        control = self.get_control(scope)
        control_mode = "NONE"
        global_blocks = [
            block for block in control.active_blocks if block.scope_type == "GLOBAL"
        ]
        if global_blocks:
            if any(block.mode == "FULL_STOP" for block in global_blocks):
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

    def get_open_trades(self, scope: QueryScope | None = None) -> TradesView:
        conn = self._connect()
        try:
            if scope is not None:
                scope_frag, scope_params = _scope_where(scope)
                active_placeholders = ",".join("?" * len(_ACTIVE_STATES))
                rows = conn.execute(
                    f"SELECT t.trade_chain_id, t.account_id, t.symbol, t.side, t.lifecycle_state, "
                    f"COALESCE(t.current_stop_price, t.expected_stop_price), "
                    f"t.be_protection_status, t.entry_avg_price, t.open_position_qty "
                    f"FROM ops_trade_chains t "
                    f"WHERE t.lifecycle_state IN ({active_placeholders}) "
                    f"AND {scope_frag} "
                    f"ORDER BY t.trade_chain_id",
                    [*_ACTIVE_STATES, *scope_params],
                ).fetchall()
            else:
                active_placeholders = ",".join("?" * len(_ACTIVE_STATES))
                rows = conn.execute(
                    f"SELECT trade_chain_id, account_id, symbol, side, lifecycle_state, "
                    f"COALESCE(current_stop_price, expected_stop_price), "
                    f"be_protection_status, entry_avg_price, open_position_qty "
                    f"FROM ops_trade_chains "
                    f"WHERE lifecycle_state IN ({active_placeholders}) "
                    f"ORDER BY trade_chain_id",
                    _ACTIVE_STATES,
                ).fetchall()

            pos_snapshots: dict[
                tuple[str, str, str],
                tuple[float | None, float | None, float | None, str],
            ] = {}
            if _table_exists(conn, "ops_position_snapshots"):
                account_id_filter = scope.account_id if scope else None
                if account_id_filter:
                    snap_rows = conn.execute(
                        "SELECT account_id, symbol, side, mark_price, unrealized_pnl, "
                        "cum_realized_pnl, captured_at "
                        "FROM ops_position_snapshots "
                        "WHERE account_id=?",
                        (account_id_filter,),
                    ).fetchall()
                else:
                    snap_rows = conn.execute(
                        "SELECT account_id, symbol, side, mark_price, unrealized_pnl, "
                        "cum_realized_pnl, captured_at "
                        "FROM ops_position_snapshots",
                    ).fetchall()
                for account_id, sym, side_snap, mp, upl, crpnl, cap in snap_rows:
                    pos_snapshots[(account_id, sym, side_snap)] = (
                        float(mp) if mp is not None else None,
                        float(upl) if upl is not None else None,
                        float(crpnl) if crpnl is not None else None,
                        cap,
                    )
        finally:
            conn.close()

        trade_rows = []
        for r in rows:
            chain_id, account_id, symbol, side, state, sl_price, be_status = (
                r[0], r[1], r[2], r[3], r[4], r[5], r[6]
            )
            entry_avg_price: float | None = r[7]
            open_position_qty: float | None = r[8]

            mark_price: float | None = None
            mark_captured_at: str | None = None
            unrealized_pnl: float | None = None
            cum_realized_pnl: float | None = None

            snap = pos_snapshots.get((account_id, symbol, side))
            if snap is not None:
                mark_price, snapshot_upl, cum_realized_pnl, mark_captured_at = snap
                if snapshot_upl is not None:
                    unrealized_pnl = snapshot_upl
                elif (
                    mark_price is not None
                    and entry_avg_price is not None
                    and open_position_qty is not None
                    and open_position_qty != 0
                ):
                    direction = 1.0 if side == "LONG" else -1.0
                    unrealized_pnl = (mark_price - entry_avg_price) * open_position_qty * direction

            trade_rows.append(TradeRow(
                chain_id=chain_id,
                symbol=symbol,
                side=side,
                state=state,
                has_sl=sl_price is not None,
                has_be=be_status == "PROTECTED",
                entry_avg_price=entry_avg_price,
                open_position_qty=open_position_qty,
                unrealized_pnl=unrealized_pnl,
                mark_price=mark_price,
                mark_captured_at=mark_captured_at,
                cum_realized_pnl=cum_realized_pnl,
            ))

        # Compute freshness age of the most-recent snapshot across all displayed trades
        mark_snapshot_max_age_seconds: float | None = None
        freshest_cap: str | None = None
        for tr in trade_rows:
            if tr.mark_captured_at is not None:
                if freshest_cap is None or tr.mark_captured_at > freshest_cap:
                    freshest_cap = tr.mark_captured_at
        if freshest_cap is not None:
            age = _age_seconds(freshest_cap)
            mark_snapshot_max_age_seconds = age

        return TradesView(
            updated_at=_now_iso(),
            total=len(trade_rows),
            rows=trade_rows,
            mark_snapshot_max_age_seconds=mark_snapshot_max_age_seconds,
        )

    def get_trade(self, chain_id: int) -> TradeDetail | None:
        conn = self._connect()
        try:
            if _table_exists(conn, "raw_messages"):
                row = conn.execute(
                    "SELECT t.trade_chain_id, t.symbol, t.side, t.trader_id, t.account_id, "
                    "t.lifecycle_state, t.entry_avg_price, t.current_stop_price, "
                    "t.management_plan_json, t.risk_snapshot_json, t.plan_state_json, "
                    "COALESCE(t.source_chat_id, rm.source_chat_id), "
                    "COALESCE(t.telegram_message_id, rm.telegram_message_id) "
                    "FROM ops_trade_chains t "
                    "LEFT JOIN raw_messages rm ON t.raw_message_id = rm.raw_message_id "
                    "WHERE t.trade_chain_id=?",
                    (chain_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT trade_chain_id, symbol, side, trader_id, account_id, "
                    "lifecycle_state, entry_avg_price, current_stop_price, "
                    "management_plan_json, risk_snapshot_json, plan_state_json, "
                    "source_chat_id, telegram_message_id "
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
            original_message_link = _build_telegram_message_link(row[11], row[12])
            current_stop_price = row[7]
            if current_stop_price is None:
                current_stop_price = _extract_stop_price(row[10], row[9], row[8])
        finally:
            conn.close()
        last_events = []
        for created_at, etype in reversed(events):
            hhmm = created_at[11:16] if created_at and len(created_at) >= 16 else ""
            last_events.append(f"{hhmm} {etype}".strip())
        return TradeDetail(
            chain_id=row[0], symbol=row[1], side=row[2], trader_id=row[3],
            account_id=row[4], state=row[5], entry_avg_price=row[6],
            current_stop_price=current_stop_price, original_message_link=original_message_link,
            last_events=last_events,
        )

    def get_health(self) -> HealthView:
        """Health is intentionally NOT scoped — always global."""
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

    def get_control(self, scope: QueryScope | None = None) -> ControlView:
        conn = self._connect()
        try:
            # Control blocks (active pause modes) are always fetched globally.
            # scope parameter accepted for API uniformity but has no effect on block retrieval.
            # (Scope filtering would apply to trades or accounts, not global control state.)
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
        new_entries_enabled = not any(block.scope_type == "GLOBAL" for block in blocks)

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

    def get_reviews(self, scope: QueryScope | None = None) -> ReviewsView:
        conn = self._connect()
        try:
            if scope is not None:
                scope_frag, scope_params = _scope_where(scope)
                chain_rows = conn.execute(
                    f"SELECT trade_chain_id, symbol FROM ops_trade_chains "
                    f"WHERE lifecycle_state='REVIEW_REQUIRED' AND {scope_frag} "
                    f"ORDER BY trade_chain_id",
                    scope_params,
                ).fetchall()
            else:
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

    def get_pnl(self, scope: QueryScope | None = None) -> PnlView:
        conn = self._connect()
        try:
            if scope is not None:
                if scope.account_id is not None:
                    snapshot = conn.execute(
                        "SELECT account_id, equity_usdt, available_balance_usdt, "
                        "total_open_risk_usdt, total_margin_used_usdt, source, captured_at "
                        "FROM ops_account_snapshots "
                        "WHERE account_id=? "
                        "ORDER BY datetime(captured_at) DESC, snapshot_id DESC "
                        "LIMIT 1",
                        (scope.account_id,),
                    ).fetchone()
                else:
                    # Scope globale: snapshot più recente tra tutti gli account
                    snapshot = conn.execute(
                        "SELECT account_id, equity_usdt, available_balance_usdt, "
                        "total_open_risk_usdt, total_margin_used_usdt, source, captured_at "
                        "FROM ops_account_snapshots "
                        "ORDER BY datetime(captured_at) DESC, snapshot_id DESC "
                        "LIMIT 1"
                    ).fetchone()
            else:
                snapshot = conn.execute(
                    "SELECT account_id, equity_usdt, available_balance_usdt, "
                    "total_open_risk_usdt, total_margin_used_usdt, source, captured_at "
                    "FROM ops_account_snapshots "
                    "ORDER BY datetime(captured_at) DESC, snapshot_id DESC "
                    "LIMIT 1"
                ).fetchone()
            account_id = snapshot[0] if snapshot else None

            if scope is not None:
                scope_frag, scope_params = _scope_where(scope)

                def _count(state: str) -> int:
                    return conn.execute(
                        f"SELECT COUNT(*) FROM ops_trade_chains "
                        f"WHERE lifecycle_state=? AND {scope_frag}",
                        [state, *scope_params],
                    ).fetchone()[0]

                # Realized PnL from closed trades in scope
                closed_row = conn.execute(
                    f"SELECT "
                    f"SUM(cumulative_gross_pnl), "
                    f"SUM(cumulative_fees + cumulative_funding), "
                    f"SUM(cumulative_fees), "
                    f"SUM(cumulative_funding) "
                    f"FROM ops_trade_chains "
                    f"WHERE lifecycle_state='CLOSED' AND {scope_frag}",
                    scope_params,
                ).fetchone()
            else:
                if account_id is not None:
                    def _count(state: str) -> int:
                        return conn.execute(
                            "SELECT COUNT(*) FROM ops_trade_chains "
                            "WHERE lifecycle_state=? AND account_id=?",
                            (state, account_id),
                        ).fetchone()[0]
                else:
                    def _count(state: str) -> int:
                        return conn.execute(
                            "SELECT COUNT(*) FROM ops_trade_chains WHERE lifecycle_state=?",
                            (state,),
                        ).fetchone()[0]

                if account_id is not None:
                    closed_row = conn.execute(
                        "SELECT "
                        "SUM(cumulative_gross_pnl), "
                        "SUM(cumulative_fees + cumulative_funding), "
                        "SUM(cumulative_fees), "
                        "SUM(cumulative_funding) "
                        "FROM ops_trade_chains "
                        "WHERE lifecycle_state='CLOSED' AND account_id=?",
                        (account_id,),
                    ).fetchone()
                else:
                    closed_row = None

            open_count = _count("OPEN")
            partial_count = _count("PARTIALLY_CLOSED")
            waiting_count = _count("WAITING_ENTRY")

            gross_pnl: float | None = None
            total_fees: float | None = None
            fees_usdt: float | None = None
            funding_usdt: float | None = None
            pnl_net: float | None = None
            if closed_row and closed_row[0] is not None:
                gross_pnl = float(closed_row[0])
                total_fees = float(closed_row[1]) if closed_row[1] is not None else 0.0
                fees_usdt = float(closed_row[2]) if closed_row[2] is not None else 0.0
                funding_usdt = float(closed_row[3]) if closed_row[3] is not None else 0.0
                pnl_net = gross_pnl - (total_fees or 0.0)
        finally:
            conn.close()

        return PnlView(
            updated_at=_now_iso(),
            account_id=snapshot[0] if snapshot else None,
            captured_at=snapshot[6] if snapshot else None,
            source=snapshot[5] if snapshot else None,
            equity_usdt=snapshot[1] if snapshot else None,
            available_balance_usdt=snapshot[2] if snapshot else None,
            total_open_risk_usdt=snapshot[3] if snapshot else None,
            total_margin_used_usdt=snapshot[4] if snapshot else None,
            open_count=open_count,
            partial_count=partial_count,
            waiting_entry_count=waiting_count,
            gross_pnl=gross_pnl,
            total_fees=total_fees,
            fees_usdt=fees_usdt,
            funding_usdt=funding_usdt,
            pnl_net=pnl_net,
        )

    def get_stats(self, scope: QueryScope) -> StatsView:
        conn = self._connect()
        try:
            scope_frag, scope_params = _scope_where(scope)

            def _stats_for_window(date_filter_sql: str, date_params: list) -> tuple[int, int, float, float, float | None]:
                row = conn.execute(
                    f"SELECT "
                    f"COUNT(*), "
                    f"SUM(CASE WHEN cumulative_gross_pnl > 0 THEN 1 ELSE 0 END), "
                    f"SUM(cumulative_gross_pnl - cumulative_fees - cumulative_funding), "
                    f"SUM(cumulative_fees + cumulative_funding) "
                    f"FROM ops_trade_chains "
                    f"WHERE lifecycle_state='CLOSED' AND {scope_frag} {date_filter_sql}",
                    [*scope_params, *date_params],
                ).fetchone()
                count = row[0] or 0
                wins = row[1] or 0
                pnl_net = float(row[2]) if row[2] is not None else 0.0
                fees = float(row[3]) if row[3] is not None else 0.0
                win_pct = (wins / count * 100.0) if count > 0 else None
                return count, wins, pnl_net, fees, win_pct

            # Today (UTC date)
            today_count, _, today_pnl, today_fees, today_win = _stats_for_window(
                "AND date(created_at) = date('now')", []
            )
            # 7 days
            d7_count, _, d7_pnl, d7_fees, d7_win = _stats_for_window(
                "AND created_at >= datetime('now', '-7 days')", []
            )
            # 30 days
            d30_count, _, d30_pnl, d30_fees, d30_win = _stats_for_window(
                "AND created_at >= datetime('now', '-30 days')", []
            )
            # Total
            tot_count, _, tot_pnl, tot_fees, tot_win = _stats_for_window("", [])

            # Best / worst chain by cumulative_gross_pnl (all time, in scope)
            best_row = conn.execute(
                f"SELECT trade_chain_id, cumulative_gross_pnl, symbol FROM ops_trade_chains "
                f"WHERE lifecycle_state='CLOSED' AND {scope_frag} "
                f"ORDER BY cumulative_gross_pnl DESC LIMIT 1",
                scope_params,
            ).fetchone()
            worst_row = conn.execute(
                f"SELECT trade_chain_id, cumulative_gross_pnl, symbol FROM ops_trade_chains "
                f"WHERE lifecycle_state='CLOSED' AND {scope_frag} "
                f"ORDER BY cumulative_gross_pnl ASC LIMIT 1",
                scope_params,
            ).fetchone()
        finally:
            conn.close()

        rows = [
            StatsRow(label="Oggi", trade_count=today_count, win_pct=today_win,
                     pnl_net=today_pnl, fees=today_fees),
            StatsRow(label="7 giorni", trade_count=d7_count, win_pct=d7_win,
                     pnl_net=d7_pnl, fees=d7_fees),
            StatsRow(label="30 giorni", trade_count=d30_count, win_pct=d30_win,
                     pnl_net=d30_pnl, fees=d30_fees),
            StatsRow(label="Totale", trade_count=tot_count, win_pct=tot_win,
                     pnl_net=tot_pnl, fees=tot_fees),
        ]
        return StatsView(
            updated_at=_now_iso(),
            rows=rows,
            best_chain_id=best_row[0] if best_row else None,
            best_pnl=float(best_row[1]) if best_row and best_row[1] is not None else None,
            best_symbol=best_row[2] if best_row else None,
            worst_chain_id=worst_row[0] if worst_row else None,
            worst_pnl=float(worst_row[1]) if worst_row and worst_row[1] is not None else None,
            worst_symbol=worst_row[2] if worst_row else None,
        )

    def get_closed_trades(
        self,
        scope: QueryScope,
        page: int = 0,
        page_size: int = 5,
    ) -> ClosedTradesView:
        conn = self._connect()
        try:
            scope_frag, scope_params = _scope_where(scope)
            offset = page * page_size

            # Check if closed_at column exists
            columns = {row[1] for row in conn.execute("PRAGMA table_info(ops_trade_chains)")}
            closed_at_expr = "closed_at" if "closed_at" in columns else "updated_at"

            total_count = conn.execute(
                f"SELECT COUNT(*) FROM ops_trade_chains "
                f"WHERE lifecycle_state='CLOSED' AND {scope_frag}",
                scope_params,
            ).fetchone()[0]

            rows = conn.execute(
                f"SELECT trade_chain_id, symbol, side, "
                f"COALESCE({closed_at_expr}, updated_at) as closed_at, "
                f"cumulative_gross_pnl "
                f"FROM ops_trade_chains "
                f"WHERE lifecycle_state='CLOSED' AND {scope_frag} "
                f"ORDER BY {closed_at_expr} DESC, trade_chain_id DESC "
                f"LIMIT ? OFFSET ?",
                [*scope_params, page_size, offset],
            ).fetchall()
        finally:
            conn.close()

        trade_rows = [
            ClosedTradeRow(
                chain_id=r[0],
                symbol=r[1],
                side=r[2],
                closed_at=r[3],
                gross_pnl=float(r[4]) if r[4] is not None else None,
            )
            for r in rows
        ]
        return ClosedTradesView(
            updated_at=_now_iso(),
            rows=trade_rows,
            total_count=total_count,
            page=page,
            page_size=page_size,
        )

    def get_blocked_trades(self, scope: QueryScope) -> BlockedTradesView:
        conn = self._connect()
        try:
            scope_frag, scope_params = _scope_where(scope)
            t_frag, t_params = _scope_where(scope, 't')

            # REVIEW_REQUIRED chains in scope
            review_rows = conn.execute(
                f"SELECT trade_chain_id, symbol FROM ops_trade_chains "
                f"WHERE lifecycle_state='REVIEW_REQUIRED' AND {scope_frag} "
                f"ORDER BY trade_chain_id",
                scope_params,
            ).fetchall()

            # Reason for REVIEW_REQUIRED from lifecycle events
            reasons = dict(conn.execute(
                "SELECT trade_chain_id, payload_json FROM ops_lifecycle_events "
                "WHERE event_type='REVIEW_REQUIRED' AND trade_chain_id IS NOT NULL "
                "ORDER BY event_id"
            ).fetchall())

            # Chains with EXEC_FAILED commands in scope
            exec_failed_rows = conn.execute(
                f"SELECT DISTINCT t.trade_chain_id, t.symbol, ec.payload_json "
                f"FROM ops_execution_commands ec "
                f"JOIN ops_trade_chains t ON t.trade_chain_id = ec.trade_chain_id "
                f"WHERE ec.status='FAILED' AND {t_frag} "
                f"ORDER BY t.trade_chain_id",
                t_params,
            ).fetchall()
        finally:
            conn.close()

        result_rows: list[BlockedTradeRow] = []

        # Track chain_ids already added
        seen: set[int] = set()

        for cid, symbol in review_rows:
            reason: str | None = None
            raw = reasons.get(cid)
            if raw:
                try:
                    reason = json.loads(raw).get("reason")
                except Exception:
                    pass
            result_rows.append(BlockedTradeRow(
                chain_id=cid,
                symbol=symbol,
                state="REVIEW_REQUIRED",
                reason=reason,
            ))
            seen.add(cid)

        for cid, symbol, payload_json in exec_failed_rows:
            if cid in seen:
                continue
            reason = None
            if payload_json:
                try:
                    reason = json.loads(payload_json).get("reason") or json.loads(payload_json).get("error")
                except Exception:
                    pass
            result_rows.append(BlockedTradeRow(
                chain_id=cid,
                symbol=symbol,
                state="EXEC_FAILED",
                reason=reason,
            ))
            seen.add(cid)

        return BlockedTradesView(updated_at=_now_iso(), rows=result_rows)

    def get_open_for_close(self, scope: QueryScope) -> list[CloseCandidate]:
        """Trade aperti chiudibili via CLOSE_FULL (OPEN + PARTIALLY_CLOSED)."""
        _CLOSEABLE_STATES = ("OPEN", "PARTIALLY_CLOSED")
        where, params = _scope_where(scope)
        conn = self._connect()
        try:
            rows = conn.execute(
                f"SELECT trade_chain_id, symbol, side, lifecycle_state, trader_id, account_id "
                f"FROM ops_trade_chains "
                f"WHERE lifecycle_state IN ({','.join('?' * len(_CLOSEABLE_STATES))}) "
                f"AND {where} ORDER BY trade_chain_id",
                (*_CLOSEABLE_STATES, *params),
            ).fetchall()
        finally:
            conn.close()
        return [CloseCandidate(r[0], r[1], r[2], r[3], r[4] or "", r[5] or "") for r in rows]

    def get_waiting_for_cancel(self, scope: QueryScope) -> list[CloseCandidate]:
        """Ordini WAITING_ENTRY cancellabili via CANCEL_ENTRY."""
        where, params = _scope_where(scope)
        conn = self._connect()
        try:
            rows = conn.execute(
                f"SELECT trade_chain_id, symbol, side, lifecycle_state, trader_id, account_id "
                f"FROM ops_trade_chains "
                f"WHERE lifecycle_state='WAITING_ENTRY' AND {where} "
                f"ORDER BY trade_chain_id",
                params,
            ).fetchall()
        finally:
            conn.close()
        return [CloseCandidate(r[0], r[1], r[2], r[3], r[4] or "", r[5] or "") for r in rows]

    def get_open_count_excluding_waiting(self, scope: QueryScope) -> int:
        """Conta trade OPEN/PARTIALLY_CLOSED per il messaggio '/cancel_all — posizioni aperte non toccate'."""
        where, params = _scope_where(scope)
        conn = self._connect()
        try:
            count = conn.execute(
                f"SELECT COUNT(*) FROM ops_trade_chains "
                f"WHERE lifecycle_state IN ('OPEN','PARTIALLY_CLOSED') AND {where}",
                params,
            ).fetchone()[0]
        finally:
            conn.close()
        return count


__all__ = [
    "StatusQueries", "StatusView", "TradesView", "TradeRow", "CloseCandidate", "TradeDetail",
    "HealthView", "ControlView", "BlockInfo", "ReviewsView", "ReviewItem",
    "PnlView", "StatsView", "StatsRow", "ClosedTradesView", "ClosedTradeRow",
    "BlockedTradesView", "BlockedTradeRow",
]
