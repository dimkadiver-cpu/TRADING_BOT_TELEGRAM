# src/runtime_v2/control_plane/status_queries.py
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone

from src.runtime_v2.control_plane.scope_resolver import QueryScope

_ACTIVE_STATES = ("OPEN", "PARTIALLY_CLOSED", "WAITING_ENTRY", "REVIEW_REQUIRED",
                  "BE_MOVE_PENDING", "PROTECTED_BE", "PARTIALLY_FILLED", "CLOSE_PENDING")

_EVENT_LABEL_MAP = {
    "SIGNAL_ACCEPTED": "SIGNAL ACCEPTED",
    "ENTRY_OPENED": "ENTRY OPENED",
    "ENTRY_FILLED": "ENTRY OPENED",
    "ENTRY_PARTIALLY_FILLED": "ENTRY PARTIALLY FILLED",
    "TP_FILLED": "TP FILLED",          # overridden per-event using tp_level from payload
    "STOP_MOVE_CONFIRMED": "SL UPDATED",  # overridden to "UPDATE DONE" when is_breakeven
    "UPDATE_DONE": "UPDATE DONE",
    "REVIEW_REQUIRED": "REVIEW REQUIRED",
    "SL_FILLED": "SL HIT",
    "CLOSE_PARTIAL_FILLED": "PARTIAL CLOSE",
    "CLOSE_FULL_FILLED": "POSITION CLOSED",
    "LIQUIDATION_FILLED": "POSITION CLOSED",
    "PENDING_ENTRY_CANCELLED": "ENTRY CANCELLED",
}

_EVENT_SOURCE_MAP: dict[str, str] = {
    "SIGNAL_ACCEPTED": "Signal",
    "ENTRY_OPENED": "exchange",
    "ENTRY_FILLED": "exchange",
    "ENTRY_PARTIALLY_FILLED": "exchange",
    "TP_FILLED": "exchange",
    "SL_FILLED": "exchange",
    "CLOSE_PARTIAL_FILLED": "exchange",
    "CLOSE_FULL_FILLED": "exchange",
    "LIQUIDATION_FILLED": "exchange",
    "PENDING_ENTRY_CANCELLED": "exchange",
    "STOP_MOVE_CONFIRMED": "operation_rules",
    "UPDATE_DONE": "operation_rules",
    "REVIEW_REQUIRED": "system",
}
_TERMINAL_STATES = {"CLOSED", "CANCELLED_UNFILLED", "POSITION_CLOSED"}
_ACTIONABLE_STATES = {"OPEN", "PARTIALLY_CLOSED", "WAITING_ENTRY",
                      "REVIEW_REQUIRED", "PARTIALLY_FILLED", "CLOSE_PENDING"}

# Maps lifecycle event_type → outbox notification_type for CLEAN_LOG link lookup
_LIFECYCLE_TO_CLEAN_LOG_NOTIF: dict[str, str] = {
    "SIGNAL_ACCEPTED": "SIGNAL_ACCEPTED",
    "ENTRY_FILLED": "ENTRY_OPENED",
    "ENTRY_OPENED": "ENTRY_OPENED",
    "TP_FILLED": "TP_FILLED",
    "SL_FILLED": "SL_FILLED",
    "CLOSE_FULL_FILLED": "POSITION_CLOSED",
    "ENTRY_UPDATED": "ENTRY_UPDATED",
    "CLOSE_PARTIAL_FILLED": "PARTIAL_CLOSE_EXECUTED",
    "PENDING_ENTRY_CANCELLED": "ENTRY_CANCELLED",
    "UPDATE_DONE": "UPDATE_DONE",
    "LIQUIDATION_FILLED": "LIQUIDATION_CLOSED",
    "STOP_MOVE_CONFIRMED": "STOP_MOVED",
    "REVIEW_REQUIRED": "REVIEW_REQUIRED",
}


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
    by_account: list[dict] | None = None


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
    trader_id: str | None = None
    account_id: str | None = None


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
class TradeEvent:
    label: str
    timestamp: str
    source: str | None = None
    event_type: str | None = None
    reason: str | None = None
    note: str | None = None
    clean_log_link: str | None = None


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
    # Legacy — kept for backward compatibility with code not yet migrated
    last_events: list[str] = field(default_factory=list)
    # New spec fields
    events: list[TradeEvent] = field(default_factory=list)
    entry_legs: list[dict] = field(default_factory=list)   # [{"price": str, "status": str}]
    tp_legs: list[dict] = field(default_factory=list)
    sl_price: str | None = None
    has_be: bool = False
    unrealized_pnl: float | None = None
    cum_realized_pnl: float | None = None
    final_result: dict | None = None  # {roi_net, ror, r_mult, pnl_net, pnl_gross, fees, funding}
    is_actionable: bool = False
    is_terminal: bool = False


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
    trader_id: str | None = None
    account_id: str | None = None


@dataclass
class ReviewsView:
    updated_at: str
    items: list[ReviewItem] = field(default_factory=list)


SNAPSHOT_STALE_SECONDS = 180


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
    partial_pnl: float | None = None
    partial_fees: float | None = None
    partial_pnl_net: float | None = None
    by_account: list[dict] | None = None
    accounts_in_scope: int | None = None
    account_unrealized_pnl_usdt: float | None = None
    snapshot_age_seconds: float | None = None
    snapshot_stale: bool = False
    accounts_fresh: int | None = None
    accounts_stale: int | None = None
    by_trader: list[dict] | None = None


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
    by_account: list[dict] | None = None


@dataclass
class ClosedTradeRow:
    chain_id: int
    symbol: str
    side: str
    closed_at: str | None
    gross_pnl: float | None
    trader_id: str | None = None
    account_id: str | None = None
    created_at: str | None = None
    closed_reason: str | None = None
    lifecycle_state: str | None = None


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
    trader_id: str | None = None
    account_id: str | None = None
    side: str | None = None
    blocked_at: str | None = None


@dataclass
class BlockedTradesView:
    updated_at: str
    rows: list[BlockedTradeRow]


@dataclass
class NotExecutedRow:
    reference: str
    trade_chain_id: int | None
    signal_reference: int | None
    account_id: str | None
    trader_id: str | None
    symbol: str | None
    side: str | None
    outcome: str
    phase: str
    reason: str | None
    command_type: str | None
    occurred_at: str
    details_command: str
    clean_log_link: str | None = None


@dataclass
class NotExecutedView:
    updated_at: str
    rows: list[NotExecutedRow]


@dataclass
class OperationalIssueRow:
    trade_chain_id: int
    account_id: str | None
    trader_id: str | None
    symbol: str | None
    side: str | None
    issue_type: str
    phase: str
    reason: str | None
    command_type: str | None
    occurred_at: str
    details_command: str


@dataclass
class OperationalIssuesView:
    updated_at: str
    rows: list[OperationalIssueRow]


@dataclass
class _IssueCandidate:
    kind: str
    command_type: str | None
    reason: str | None
    occurred_at: str
    phase: str
    details_command: str


@dataclass
class _ChainIssueEvidence:
    trade_chain_id: int
    account_id: str | None
    trader_id: str | None
    symbol: str | None
    side: str | None
    lifecycle_state: str
    filled_entry_qty: float
    open_position_qty: float
    entry_acknowledged: bool = False
    first_entry_evidence_at: str | None = None
    latest_pre_entry_review: _IssueCandidate | None = None
    latest_post_entry_review: _IssueCandidate | None = None
    latest_entry_failure: _IssueCandidate | None = None
    latest_operational_failure: _IssueCandidate | None = None


_ENTRY_COMMAND_TYPES = {"PLACE_ENTRY", "PLACE_ENTRY_WITH_ATTACHED_TPSL"}
_ENTRY_ACK_STATUSES = {"ACK", "WAITING_POSITION", "DONE"}


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


def _parse_json_blob(blob: str | None) -> dict:
    if not blob:
        return {}
    try:
        data = json.loads(blob)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _extract_reason(blob: str | None) -> str | None:
    data = _parse_json_blob(blob)
    value = data.get("reason") or data.get("error")
    if value is None:
        return None
    return str(value)


def _extract_command_reason(
    result_payload_json: str | None,
    payload_json: str | None = None,
) -> str | None:
    return _extract_reason(result_payload_json) or _extract_reason(payload_json)


def _command_effective_timestamp(
    *,
    status: str,
    created_at: str | None,
    updated_at: str | None,
    acknowledged_at: str | None,
    completed_at: str | None,
) -> str:
    if status in _ENTRY_ACK_STATUSES:
        return acknowledged_at or completed_at or updated_at or created_at or ""
    if status == "FAILED":
        return completed_at or updated_at or created_at or ""
    return updated_at or created_at or ""


def _entry_evidence_present(evidence: _ChainIssueEvidence) -> bool:
    return (
        evidence.entry_acknowledged
        or evidence.filled_entry_qty > 0
        or evidence.open_position_qty > 0
    )


def _matches_scope_values(
    scope: QueryScope,
    *,
    account_id: str | None,
    trader_id: str | None,
) -> bool:
    if scope.account_id is None and scope.trader_ids is None:
        return True
    if scope.account_id != account_id:
        return False
    if scope.trader_ids is None:
        return True
    return trader_id in scope.trader_ids


def _phase_for_signal_event(event_type: str) -> str:
    return {
        "SIGNAL_REJECTED": "Risk",
        "REVIEW_REQUIRED": "Manual review",
    }.get(event_type, "Validation")


def _phase_for_command(command_type: str | None) -> str:
    return {
        "PLACE_ENTRY": "Entry submission",
        "PLACE_ENTRY_WITH_ATTACHED_TPSL": "Entry submission",
        "MOVE_STOP": "Protection",
        "MOVE_STOP_TO_BREAKEVEN": "Breakeven",
        "PLACE_TP": "Take profit",
        "CLOSE_FULL": "Close",
        "CLOSE_PARTIAL": "Close",
        "CANCEL_ENTRY": "Entry cancel",
    }.get(command_type or "", "Operations")


def _latest_candidate(*candidates: _IssueCandidate | None) -> _IssueCandidate | None:
    present = [candidate for candidate in candidates if candidate is not None]
    if not present:
        return None
    return max(present, key=lambda candidate: candidate.occurred_at)


def _earliest_timestamp(*timestamps: str | None) -> str | None:
    present = [timestamp for timestamp in timestamps if timestamp]
    if not present:
        return None
    return min(present)


def _review_is_post_entry(
    evidence: _ChainIssueEvidence,
    candidate: _IssueCandidate,
) -> bool:
    if evidence.first_entry_evidence_at:
        return evidence.first_entry_evidence_at <= candidate.occurred_at
    return False


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

    def get_open_trades(
        self,
        scope: QueryScope | None = None,
        side: str | None = None,
        status: str | None = None,
    ) -> TradesView:
        conn = self._connect()
        try:
            active_states = (status,) if status else _ACTIVE_STATES
            state_ph = ",".join("?" * len(active_states))
            side_sql_t = "AND t.side=?" if side else ""
            side_sql = "AND side=?" if side else ""
            side_params = [side] if side else []

            if scope is not None:
                scope_frag, scope_params = _scope_where(scope)
                rows = conn.execute(
                    f"SELECT t.trade_chain_id, t.account_id, t.trader_id, t.symbol, t.side, t.lifecycle_state, "
                    f"COALESCE(t.current_stop_price, t.expected_stop_price), "
                    f"t.be_protection_status, t.entry_avg_price, t.open_position_qty, "
                    f"t.cumulative_gross_pnl, t.cumulative_fees, t.cumulative_funding "
                    f"FROM ops_trade_chains t "
                    f"WHERE t.lifecycle_state IN ({state_ph}) "
                    f"AND {scope_frag} {side_sql_t} "
                    f"ORDER BY t.trade_chain_id DESC",
                    [*active_states, *scope_params, *side_params],
                ).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT trade_chain_id, account_id, trader_id, symbol, side, lifecycle_state, "
                    f"COALESCE(current_stop_price, expected_stop_price), "
                    f"be_protection_status, entry_avg_price, open_position_qty, "
                    f"cumulative_gross_pnl, cumulative_fees, cumulative_funding "
                    f"FROM ops_trade_chains "
                    f"WHERE lifecycle_state IN ({state_ph}) {side_sql} "
                    f"ORDER BY trade_chain_id DESC",
                    [*active_states, *side_params],
                ).fetchall()

            pos_snapshots: dict[
                tuple[str, str, str],
                tuple[float | None, float | None, str],
            ] = {}
            if _table_exists(conn, "ops_position_snapshots"):
                account_id_filter = scope.account_id if scope else None
                if account_id_filter:
                    snap_rows = conn.execute(
                        "SELECT account_id, symbol, side, mark_price, unrealized_pnl, "
                        "captured_at "
                        "FROM ops_position_snapshots "
                        "WHERE account_id=?",
                        (account_id_filter,),
                    ).fetchall()
                else:
                    snap_rows = conn.execute(
                        "SELECT account_id, symbol, side, mark_price, unrealized_pnl, "
                        "captured_at "
                        "FROM ops_position_snapshots",
                    ).fetchall()
                for account_id, sym, side_snap, mp, upl, cap in snap_rows:
                    pos_snapshots[(account_id, sym, side_snap)] = (
                        float(mp) if mp is not None else None,
                        float(upl) if upl is not None else None,
                        cap,
                    )
        finally:
            conn.close()

        trade_rows = []
        for r in rows:
            chain_id, account_id, trader_id, symbol, side, state, sl_price, be_status = (
                r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7]
            )
            entry_avg_price: float | None = r[8]
            open_position_qty: float | None = r[9]
            chain_gross: float | None = float(r[10]) if r[10] is not None else None
            chain_fees: float | None = float(r[11]) if r[11] is not None else None
            chain_funding: float | None = float(r[12]) if r[12] is not None else None

            mark_price: float | None = None
            mark_captured_at: str | None = None
            unrealized_pnl: float | None = None
            cum_realized_pnl: float | None = None

            if chain_gross is not None:
                cum_realized_pnl = chain_gross - (chain_fees or 0.0) - (chain_funding or 0.0)

            snap = pos_snapshots.get((account_id, symbol, side))
            if snap is not None:
                mark_price, snapshot_upl, mark_captured_at = snap
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
                trader_id=trader_id,
                account_id=account_id,
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
                    "COALESCE(t.telegram_message_id, rm.telegram_message_id), "
                    "t.be_protection_status, "
                    "t.cumulative_gross_pnl, t.cumulative_fees, t.cumulative_funding, "
                    "t.peak_margin_used "
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
                    "source_chat_id, telegram_message_id, "
                    "be_protection_status, "
                    "cumulative_gross_pnl, cumulative_fees, cumulative_funding, "
                    "peak_margin_used "
                    "FROM ops_trade_chains WHERE trade_chain_id=?",
                    (chain_id,),
                ).fetchone()
            if row is None:
                return None
            events_rows = conn.execute(
                "SELECT created_at, event_type, payload_json FROM ops_lifecycle_events "
                "WHERE trade_chain_id=? ORDER BY event_id ASC",
                (chain_id,),
            ).fetchall()
            original_message_link = _build_telegram_message_link(row[11], row[12])
            current_stop_price = row[7]
            if current_stop_price is None:
                current_stop_price = _extract_stop_price(row[10], row[9], row[8])

            # Live PnL from position snapshot (non-terminal trades only)
            _state_inner = row[5]
            _is_terminal_inner = _state_inner in _TERMINAL_STATES
            _unrealized_pnl: float | None = None
            _cum_realized_pnl: float | None = None
            if not _is_terminal_inner and _table_exists(conn, "ops_position_snapshots"):
                _snap = conn.execute(
                    "SELECT unrealized_pnl FROM ops_position_snapshots "
                    "WHERE account_id=? AND symbol=? AND side=? LIMIT 1",
                    (row[4], row[1], row[2]),
                ).fetchone()
                if _snap:
                    _unrealized_pnl = float(_snap[0]) if _snap[0] is not None else None

            # Cumulative PnL columns for final_result (terminal trades)
            _cum_gross_pnl_raw = row[14]
            _cum_fees_raw = row[15]
            _cum_funding_raw = row[16]

            if not _is_terminal_inner and _cum_gross_pnl_raw is not None:
                _cum_realized_pnl = (
                    float(_cum_gross_pnl_raw)
                    - (float(_cum_fees_raw) if _cum_fees_raw else 0.0)
                    - (float(_cum_funding_raw) if _cum_funding_raw else 0.0)
                )
            _peak_margin_used_raw = row[17] if len(row) > 17 else None

            # Clean-log links: query outbox for sent_message_id per notification_type
            _clean_log_links: dict = {}
            _tracking_chat_id: str | None = None
            if _table_exists(conn, "ops_clean_log_tracking"):
                _tr = conn.execute(
                    "SELECT telegram_chat_id FROM ops_clean_log_tracking WHERE trade_chain_id=?",
                    (chain_id,),
                ).fetchone()
                if _tr and _tr[0]:
                    _tracking_chat_id = str(_tr[0]).removeprefix("-100")
            if _tracking_chat_id:
                _outbox_cols = {r[1] for r in conn.execute("PRAGMA table_info(ops_notification_outbox)")}
                if "sent_message_id" in _outbox_cols:
                    try:
                        _orows = conn.execute(
                            "SELECT notification_type, sent_message_id "
                            "FROM ops_notification_outbox "
                            "WHERE destination='CLEAN_LOG' AND chain_id=? "
                            "  AND sent_message_id IS NOT NULL "
                            "ORDER BY notification_id ASC",
                            (chain_id,),
                        ).fetchall()
                        from collections import deque as _deque
                        for _ntype, _mid in _orows:
                            if _ntype not in _clean_log_links:
                                _clean_log_links[_ntype] = _deque()
                            _clean_log_links[_ntype].append(
                                f"https://t.me/c/{_tracking_chat_id}/{_mid}"
                            )
                    except Exception:
                        pass
        finally:
            conn.close()

        structured_events: list[TradeEvent] = []
        for created_at, etype, payload_json in events_rows:
            # Only show user-facing events — skip internal lifecycle noise
            if etype not in _EVENT_LABEL_MAP:
                continue
            label = _EVENT_LABEL_MAP[etype]
            ts = ""
            if created_at and len(created_at) >= 16:
                try:
                    dt = datetime.fromisoformat(created_at.rstrip("Z"))
                    ts = dt.strftime("%-d %b %H:%M:%S")
                except Exception:
                    try:
                        dt = datetime.fromisoformat(created_at[:19])
                        ts = dt.strftime("%d %b %H:%M:%S").lstrip("0")
                    except Exception:
                        ts = created_at[11:19] if len(created_at) >= 19 else created_at
            source_val = None
            event_type_val = None
            reason_val = None
            note_val = None
            pdata: dict = {}
            if payload_json:
                try:
                    pdata = json.loads(payload_json)
                    source_val = pdata.get("source")
                    event_type_val = pdata.get("update_type") or pdata.get("type")
                    reason_val = (
                        pdata.get("reason")
                        or pdata.get("close_reason")
                        or pdata.get("cancel_reason")
                        or pdata.get("error")
                    )
                    note_val = pdata.get("note")
                except Exception:
                    pass
            if not source_val:
                source_val = _EVENT_SOURCE_MAP.get(etype)
            # Dynamic label overrides
            if etype == "TP_FILLED":
                level = pdata.get("tp_level")
                label = f"TP{level} FILLED" if level else "TP FILLED"
            elif etype == "STOP_MOVE_CONFIRMED" and pdata.get("is_breakeven"):
                label = "UPDATE DONE"
                event_type_val = "BE_MOVE"
            elif etype == "SL_FILLED" and pdata.get("close_reason") == "BREAKEVEN_AFTER_TP":
                label = "POSITION CLOSED"
            # Assign clean_log link from outbox (pop from deque to handle repeated types)
            _notif_type = _LIFECYCLE_TO_CLEAN_LOG_NOTIF.get(etype)
            _ev_link: str | None = None
            if _notif_type and _notif_type in _clean_log_links and _clean_log_links[_notif_type]:
                _ev_link = _clean_log_links[_notif_type].popleft()
            structured_events.append(TradeEvent(
                label=label,
                timestamp=ts,
                source=source_val,
                event_type=event_type_val,
                reason=reason_val,
                note=note_val,
                clean_log_link=_ev_link,
            ))

        # Fallback: attach original signal link to SIGNAL ACCEPTED if outbox didn't cover it
        if original_message_link:
            for ev in structured_events:
                if ev.label == "SIGNAL ACCEPTED" and ev.clean_log_link is None:
                    ev.clean_log_link = original_message_link
                    break

        # Legacy last_events (backward compat) — last 3, oldest first
        last_events_legacy = [
            f"{ev.timestamp} {ev.label}".strip() for ev in structured_events[-3:]
        ] if structured_events else []

        # Determine trade state flags
        state_val = row[5]
        is_terminal = state_val in _TERMINAL_STATES
        is_actionable = state_val in _ACTIONABLE_STATES

        # Build final_result for terminal trades
        final_result: dict | None = None
        if is_terminal and _cum_gross_pnl_raw is not None:
            pnl_gross_f = float(_cum_gross_pnl_raw)
            fees_f = float(_cum_fees_raw) if _cum_fees_raw is not None else 0.0
            funding_f = float(_cum_funding_raw) if _cum_funding_raw is not None else 0.0
            pnl_net_f = pnl_gross_f - fees_f - funding_f
            try:
                _risk_snap = json.loads(row[9] or "{}")
                _initial_risk = float(_risk_snap["risk_amount"]) if _risk_snap.get("risk_amount") else None
            except Exception:
                _initial_risk = None
            _peak_margin = float(_peak_margin_used_raw) if _peak_margin_used_raw is not None else None
            roi_net = round(pnl_net_f / _peak_margin * 100.0, 4) if _peak_margin and _peak_margin > 0 else None
            ror = round(pnl_net_f / _initial_risk * 100.0, 4) if _initial_risk and _initial_risk > 0 else None
            r_mult = round(pnl_net_f / _initial_risk, 2) if _initial_risk and _initial_risk > 0 else None
            final_result = {
                "roi_net": roi_net,
                "ror": ror,
                "r_mult": r_mult,
                "pnl_net": pnl_net_f,
                "pnl_gross": pnl_gross_f,
                "fees": -fees_f,
                "funding": funding_f,
            }

        # Build entry_legs and tp_legs from plan_state_json
        entry_legs: list[dict] = []
        tp_legs: list[dict] = []
        sl_price_str: str | None = None
        has_be = (row[13] == "PROTECTED") if row[13] is not None else False

        try:
            plan_state = json.loads(row[10] or "{}")
            for leg in plan_state.get("legs") or []:
                raw_price = leg.get("price")
                if raw_price is None:
                    continue
                status_raw = (leg.get("status") or "pending").upper()
                leg_status = (
                    "filled" if status_raw == "FILLED"
                    else "cancelled" if status_raw == "CANCELLED"
                    else "pending"
                )
                entry_legs.append({"price": f"{float(raw_price):.8g}", "status": leg_status})
            all_tp_prices = list(plan_state.get("intermediate_tps") or [])
            final_tp = plan_state.get("final_tp")
            if final_tp is not None:
                all_tp_prices.append(final_tp)
            filled_count = int(plan_state.get("_filled_tp_count") or 0)
            for i, tp_price in enumerate(all_tp_prices):
                if i < filled_count:
                    tp_status = "filled"
                elif is_terminal:
                    tp_status = "cancelled"
                else:
                    tp_status = "pending"
                tp_legs.append({"price": f"{float(tp_price):.8g}", "status": tp_status})
        except Exception:
            pass

        if current_stop_price is not None:
            sl_price_str = f"{current_stop_price:.8g}"

        return TradeDetail(
            chain_id=row[0], symbol=row[1], side=row[2], trader_id=row[3],
            account_id=row[4], state=state_val, entry_avg_price=row[6],
            current_stop_price=current_stop_price,
            original_message_link=original_message_link,
            last_events=last_events_legacy,
            events=structured_events,
            entry_legs=entry_legs,
            tp_legs=tp_legs,
            sl_price=sl_price_str,
            has_be=has_be,
            unrealized_pnl=_unrealized_pnl,
            cum_realized_pnl=_cum_realized_pnl,
            final_result=final_result,
            is_actionable=is_actionable,
            is_terminal=is_terminal,
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
            lifecycle_ts = conn.execute(
                "SELECT MAX(created_at) FROM ops_lifecycle_events"
            ).fetchone()[0]
            exec_ts = conn.execute(
                "SELECT MAX(updated_at) FROM ops_execution_commands"
            ).fetchone()[0]
        finally:
            conn.close()

        age = _age_seconds(last_event_ts)
        sync_status = "OK" if (age is None or age < 60) else "WARNING"

        _STALE_THRESHOLD = 300  # 5 minutes — worker is considered idle/stale

        def _probe(ts: str | None, label: str) -> tuple[str, str, str]:
            a = _age_seconds(ts)
            if a is None:
                return (label, "OK", "")
            if a > _STALE_THRESHOLD:
                return (label, "WARNING", f"last event {int(a)}s ago")
            return (label, "OK", "")

        workers = [
            _probe(None, "Parser pipeline"),  # no dedicated table yet — treat as OK
            _probe(lifecycle_ts, "Lifecycle gate"),
            _probe(exec_ts, "Execution worker"),
            ("Exchange sync", sync_status, f"last event {int(age)}s ago" if age is not None else "no events"),
            _probe(None, "Notification disp."),  # no timestamp col in outbox — treat as OK
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
                    f"SELECT trade_chain_id, symbol, trader_id, account_id FROM ops_trade_chains "
                    f"WHERE lifecycle_state='REVIEW_REQUIRED' AND {scope_frag} "
                    f"ORDER BY trade_chain_id",
                    scope_params,
                ).fetchall()
            else:
                chain_rows = conn.execute(
                    "SELECT trade_chain_id, symbol, trader_id, account_id FROM ops_trade_chains "
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
        for row in chain_rows:
            cid, symbol, trader_id, account_id = row[0], row[1], row[2], row[3]
            reason = "review_required"
            raw = reasons.get(cid)
            if raw:
                try:
                    reason = json.loads(raw).get("reason", reason)
                except Exception:
                    pass
            items.append(ReviewItem(
                chain_id=cid, symbol=symbol, reason=reason,
                trader_id=trader_id, account_id=account_id,
            ))
        return ReviewsView(updated_at=_now_iso(), items=items)

    def get_pnl(self, scope: QueryScope | None = None) -> PnlView:
        # Normalise: legacy scope=None is treated identically to explicit global scope.
        # This eliminates the hybrid path that ran the CTE for freshness counts but
        # used a staleness-blind LIMIT 1 SELECT for equity.
        is_global_scope = (scope is None) or (scope.account_id is None)
        conn = self._connect()
        try:
            if scope is not None and scope.account_id is not None:
                # Single-account scope: fetch the latest OK snapshot for this account
                # FALLBACK/FAILED records are diagnostic-only; STALE is signalled by captured_at age
                snapshot = conn.execute(
                    "SELECT account_id, equity_usdt, available_balance_usdt, "
                    "total_open_risk_usdt, total_margin_used_usdt, source, captured_at, "
                    "account_unrealized_pnl_usdt "
                    "FROM ops_account_snapshots "
                    "WHERE account_id=? AND snapshot_status='OK' "
                    "ORDER BY datetime(captured_at) DESC, snapshot_id DESC "
                    "LIMIT 1",
                    (scope.account_id,),
                ).fetchone()
            else:
                # Global scope (scope is None OR scope.account_id is None): CTE handles equity
                snapshot = None

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
                partial_row = conn.execute(
                    f"SELECT "
                    f"SUM(cumulative_gross_pnl), "
                    f"SUM(cumulative_fees + cumulative_funding) "
                    f"FROM ops_trade_chains "
                    f"WHERE lifecycle_state='PARTIALLY_CLOSED' AND {scope_frag}",
                    scope_params,
                ).fetchone()
                # by_trader: per-trader breakdown, only for single-account non-global scope
                _by_trader: list[dict] | None = None
                if scope.account_id is not None:
                    distinct_traders = conn.execute(
                        f"SELECT DISTINCT trader_id FROM ops_trade_chains WHERE {scope_frag}",
                        scope_params,
                    ).fetchall()
                    trader_ids_in_scope = [r[0] for r in distinct_traders if r[0] is not None]
                    if len(trader_ids_in_scope) >= 2:
                        by_trader_list: list[dict] = []
                        for tid in trader_ids_in_scope:
                            oc = conn.execute(
                                "SELECT COUNT(*) FROM ops_trade_chains "
                                "WHERE lifecycle_state IN ('OPEN','PARTIALLY_CLOSED') "
                                "AND account_id=? AND trader_id=?",
                                (scope.account_id, tid),
                            ).fetchone()[0]
                            cp = conn.execute(
                                "SELECT SUM(cumulative_gross_pnl - cumulative_fees - cumulative_funding) "
                                "FROM ops_trade_chains "
                                "WHERE lifecycle_state='CLOSED' AND account_id=? AND trader_id=?",
                                (scope.account_id, tid),
                            ).fetchone()
                            pp = conn.execute(
                                "SELECT SUM(cumulative_gross_pnl - cumulative_fees - cumulative_funding) "
                                "FROM ops_trade_chains "
                                "WHERE lifecycle_state='PARTIALLY_CLOSED' AND account_id=? AND trader_id=?",
                                (scope.account_id, tid),
                            ).fetchone()
                            risk_rows = conn.execute(
                                "SELECT risk_snapshot_json FROM ops_trade_chains "
                                "WHERE lifecycle_state IN ('OPEN','PARTIALLY_CLOSED','WAITING_ENTRY') "
                                "AND account_id=? AND trader_id=? AND risk_snapshot_json IS NOT NULL",
                                (scope.account_id, tid),
                            ).fetchall()
                            risk_total: float | None = None
                            for rrow in risk_rows:
                                try:
                                    v = json.loads(rrow[0]).get("risk_amount")
                                    if v is not None:
                                        risk_total = (risk_total or 0.0) + float(v)
                                except Exception:
                                    pass
                            by_trader_list.append({
                                "trader_id": tid,
                                "open_count": oc,
                                "risk_usdt": risk_total,
                                "closed_pnl": float(cp[0]) if cp and cp[0] is not None else 0.0,
                                "partial_pnl": float(pp[0]) if pp and pp[0] is not None else 0.0,
                            })
                        _by_trader = by_trader_list
            else:
                # Legacy scope=None: global counts and PnL (all accounts)
                def _count(state: str) -> int:
                    return conn.execute(
                        "SELECT COUNT(*) FROM ops_trade_chains WHERE lifecycle_state=?",
                        (state,),
                    ).fetchone()[0]

                closed_row = conn.execute(
                    "SELECT "
                    "SUM(cumulative_gross_pnl), "
                    "SUM(cumulative_fees + cumulative_funding), "
                    "SUM(cumulative_fees), "
                    "SUM(cumulative_funding) "
                    "FROM ops_trade_chains "
                    "WHERE lifecycle_state='CLOSED'"
                ).fetchone()
                partial_row = conn.execute(
                    "SELECT "
                    "SUM(cumulative_gross_pnl), "
                    "SUM(cumulative_fees + cumulative_funding) "
                    "FROM ops_trade_chains "
                    "WHERE lifecycle_state='PARTIALLY_CLOSED'"
                ).fetchone()
                _by_trader = None

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

            partial_pnl: float | None = None
            partial_fees: float | None = None
            partial_pnl_net: float | None = None
            if partial_row and partial_row[0] is not None:
                partial_pnl = float(partial_row[0])
                partial_fees = float(partial_row[1]) if partial_row[1] is not None else 0.0
                partial_pnl_net = partial_pnl - (partial_fees or 0.0)

            # Global scope: CTE latest-per-account + freshness aggregation
            by_account: list[dict] | None = None
            accounts_in_scope: int | None = None
            accounts_fresh: int | None = None
            accounts_stale: int | None = None
            # For global scope, equity_usdt/available_balance_usdt/total_margin_used_usdt/
            # account_unrealized_pnl_usdt are aggregated from fresh snapshots only.
            global_equity_usdt: float | None = None
            global_available_balance_usdt: float | None = None
            global_total_margin_used_usdt: float | None = None
            global_account_unrealized_pnl_usdt: float | None = None

            if is_global_scope:
                snap_rows = conn.execute(
                    """
                    WITH ranked AS (
                        -- FALLBACK/FAILED records are diagnostic-only; STALE is signalled by captured_at age
                        SELECT account_id, equity_usdt, available_balance_usdt,
                               total_open_risk_usdt, total_margin_used_usdt,
                               account_unrealized_pnl_usdt, source, captured_at,
                               ROW_NUMBER() OVER (
                                   PARTITION BY account_id
                                   ORDER BY datetime(captured_at) DESC, snapshot_id DESC
                               ) AS rn
                        FROM ops_account_snapshots
                        WHERE snapshot_status = 'OK'
                    )
                    SELECT account_id, equity_usdt, available_balance_usdt,
                           total_open_risk_usdt, total_margin_used_usdt,
                           account_unrealized_pnl_usdt, source, captured_at
                    FROM ranked WHERE rn = 1
                    """
                ).fetchall()

                fresh_count = 0
                stale_count = 0
                eq_sum = av_sum = mg_sum = upl_sum = 0.0
                has_any_fresh = False

                for r in snap_rows:
                    age = _age_seconds(r[7])
                    is_stale = age is None or age > SNAPSHOT_STALE_SECONDS
                    if is_stale:
                        stale_count += 1
                    else:
                        fresh_count += 1
                        if r[1] is not None:
                            eq_sum += r[1]
                        if r[2] is not None:
                            av_sum += r[2]
                        if r[4] is not None:
                            mg_sum += r[4]
                        if r[5] is not None:
                            upl_sum += r[5]
                        has_any_fresh = True

                accounts_fresh = fresh_count
                accounts_stale = stale_count

                if has_any_fresh:
                    global_equity_usdt = eq_sum
                    global_available_balance_usdt = av_sum
                    global_total_margin_used_usdt = mg_sum
                    global_account_unrealized_pnl_usdt = upl_sum

                # Build per-account snapshot index for by_account
                snap_index = {r[0]: r for r in snap_rows}

                # Collect account_ids from trade chains (for trade counts)
                acc_rows = conn.execute(
                    "SELECT DISTINCT account_id FROM ops_trade_chains WHERE account_id IS NOT NULL"
                ).fetchall()
                # Merge: include accounts from snapshots too
                all_account_ids = list({r[0] for r in acc_rows} | set(snap_index.keys()))
                accounts_in_scope = len(all_account_ids)
                by_account = []
                for acc_id in all_account_ids:
                    net_row = conn.execute(
                        "SELECT SUM(cumulative_gross_pnl - cumulative_fees - cumulative_funding) "
                        "FROM ops_trade_chains WHERE lifecycle_state='CLOSED' AND account_id=?",
                        (acc_id,)
                    ).fetchone()
                    net_pnl_acc = float(net_row[0]) if net_row and net_row[0] is not None else 0.0
                    open_c = conn.execute(
                        "SELECT COUNT(*) FROM ops_trade_chains "
                        "WHERE lifecycle_state IN ('OPEN','PARTIALLY_CLOSED') AND account_id=?",
                        (acc_id,)
                    ).fetchone()[0]
                    snap_r = snap_index.get(acc_id)
                    age = _age_seconds(snap_r[7]) if snap_r else None
                    is_stale_acc = age is None or age > SNAPSHOT_STALE_SECONDS
                    by_account.append({
                        "account_id": acc_id,
                        "net_pnl": net_pnl_acc,
                        "open_count": open_c,
                        "available_usdt": snap_r[2] if snap_r else None,
                        "margin_usdt": snap_r[4] if snap_r else None,
                        "age_seconds": age,
                        "stale": is_stale_acc,
                    })
                by_account.sort(key=lambda x: x["net_pnl"], reverse=True)
        finally:
            conn.close()

        # Single account scope: compute age/stale from snapshot
        snap_age: float | None = None
        snap_stale = False
        snap_unrealized_pnl: float | None = None
        if not is_global_scope:
            snap_age = _age_seconds(snapshot[6]) if snapshot else None
            snap_stale = snap_age is not None and snap_age > SNAPSHOT_STALE_SECONDS
            snap_unrealized_pnl = snapshot[7] if snapshot else None

        # Resolve equity fields:
        # - global scope (scope is None OR scope.account_id is None): equity from CTE fresh-only aggregate
        # - single account scope: equity from single account snapshot
        if is_global_scope:
            _equity_usdt = global_equity_usdt
            _available_balance_usdt = global_available_balance_usdt
            _total_margin_used_usdt = global_total_margin_used_usdt
            _account_unrealized_pnl_usdt = global_account_unrealized_pnl_usdt
            _account_id = None
            _captured_at = None
            _source = None
            _total_open_risk_usdt = None
        else:
            _equity_usdt = snapshot[1] if snapshot else None
            _available_balance_usdt = snapshot[2] if snapshot else None
            _total_open_risk_usdt = snapshot[3] if snapshot else None
            _total_margin_used_usdt = snapshot[4] if snapshot else None
            _account_id = snapshot[0] if snapshot else None
            _captured_at = snapshot[6] if snapshot else None
            _source = snapshot[5] if snapshot else None
            _account_unrealized_pnl_usdt = snap_unrealized_pnl

        return PnlView(
            updated_at=_now_iso(),
            account_id=_account_id,
            captured_at=_captured_at,
            source=_source,
            equity_usdt=_equity_usdt,
            available_balance_usdt=_available_balance_usdt,
            total_open_risk_usdt=_total_open_risk_usdt,
            total_margin_used_usdt=_total_margin_used_usdt,
            open_count=open_count,
            partial_count=partial_count,
            waiting_entry_count=waiting_count,
            gross_pnl=gross_pnl,
            total_fees=total_fees,
            fees_usdt=fees_usdt,
            funding_usdt=funding_usdt,
            pnl_net=pnl_net,
            partial_pnl=partial_pnl,
            partial_fees=partial_fees,
            partial_pnl_net=partial_pnl_net,
            by_account=by_account,
            accounts_in_scope=accounts_in_scope,
            account_unrealized_pnl_usdt=_account_unrealized_pnl_usdt,
            snapshot_age_seconds=snap_age,
            snapshot_stale=snap_stale,
            accounts_fresh=accounts_fresh,
            accounts_stale=accounts_stale,
            by_trader=_by_trader,
        )

    def get_stats(self, scope: QueryScope, side: str | None = None) -> StatsView:
        conn = self._connect()
        try:
            scope_frag, scope_params = _scope_where(scope)

            side_sql = "AND side=?" if side else ""
            side_params = [side] if side else []

            def _stats_for_window(date_filter_sql: str, date_params: list) -> tuple[int, int, float, float, float | None]:
                row = conn.execute(
                    f"SELECT "
                    f"COUNT(*), "
                    f"SUM(CASE WHEN cumulative_gross_pnl > 0 THEN 1 ELSE 0 END), "
                    f"SUM(cumulative_gross_pnl - cumulative_fees - cumulative_funding), "
                    f"SUM(cumulative_fees + cumulative_funding) "
                    f"FROM ops_trade_chains "
                    f"WHERE lifecycle_state='CLOSED' AND {scope_frag} {date_filter_sql} {side_sql}",
                    [*scope_params, *date_params, *side_params],
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
                f"WHERE lifecycle_state='CLOSED' AND {scope_frag} {side_sql} "
                f"ORDER BY cumulative_gross_pnl DESC LIMIT 1",
                [*scope_params, *side_params],
            ).fetchone()
            worst_row = conn.execute(
                f"SELECT trade_chain_id, cumulative_gross_pnl, symbol FROM ops_trade_chains "
                f"WHERE lifecycle_state='CLOSED' AND {scope_frag} {side_sql} "
                f"ORDER BY cumulative_gross_pnl ASC LIMIT 1",
                [*scope_params, *side_params],
            ).fetchone()

            # Global scope: per-account stats breakdown
            by_account_stats: list[dict] | None = None
            if scope.account_id is None:
                acc_rows = conn.execute(
                    "SELECT DISTINCT account_id FROM ops_trade_chains WHERE account_id IS NOT NULL"
                ).fetchall()
                account_ids = [r[0] for r in acc_rows]
                by_account_stats = []
                for acc_id in account_ids:
                    acc_row = conn.execute(
                        f"SELECT COUNT(*), "
                        f"SUM(CASE WHEN cumulative_gross_pnl > 0 THEN 1 ELSE 0 END), "
                        f"SUM(cumulative_gross_pnl - cumulative_fees - cumulative_funding) "
                        f"FROM ops_trade_chains WHERE lifecycle_state='CLOSED' AND account_id=? {side_sql}",
                        [acc_id, *side_params],
                    ).fetchone()
                    cnt = acc_row[0] or 0
                    wins = acc_row[1] or 0
                    net_pnl_acc = float(acc_row[2]) if acc_row[2] is not None else 0.0
                    win_pct_acc = (wins / cnt * 100.0) if cnt > 0 else None
                    by_account_stats.append({
                        "account_id": acc_id,
                        "trade_count": cnt,
                        "win_pct": win_pct_acc,
                        "net_pnl": net_pnl_acc,
                    })
                by_account_stats.sort(key=lambda x: x["net_pnl"], reverse=True)
        finally:
            conn.close()

        rows = [
            StatsRow(label="Today", trade_count=today_count, win_pct=today_win,
                     pnl_net=today_pnl, fees=today_fees),
            StatsRow(label="Last 7d", trade_count=d7_count, win_pct=d7_win,
                     pnl_net=d7_pnl, fees=d7_fees),
            StatsRow(label="Last 30d", trade_count=d30_count, win_pct=d30_win,
                     pnl_net=d30_pnl, fees=d30_fees),
            StatsRow(label="All time", trade_count=tot_count, win_pct=tot_win,
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
            by_account=by_account_stats,
        )

    def get_closed_trades(
        self,
        scope: QueryScope,
        page: int = 0,
        page_size: int = 5,
        side: str | None = None,
        period: str | None = None,
    ) -> ClosedTradesView:
        _CLOSED_STATES = ("CLOSED", "CANCELLED_UNFILLED")
        closed_placeholders = ",".join("?" * len(_CLOSED_STATES))
        conn = self._connect()
        try:
            scope_frag, scope_params = _scope_where(scope)
            offset = page * page_size

            # Check if closed_at column exists
            columns = {row[1] for row in conn.execute("PRAGMA table_info(ops_trade_chains)")}
            closed_at_expr = "closed_at" if "closed_at" in columns else "updated_at"

            side_sql = "AND t.side=?" if side else ""
            side_params = [side] if side else []
            _period_map = {
                "today": f"AND date(COALESCE(t.{closed_at_expr}, t.updated_at)) = date('now')",
                "week": f"AND COALESCE(t.{closed_at_expr}, t.updated_at) >= datetime('now', '-7 days')",
                "month": f"AND COALESCE(t.{closed_at_expr}, t.updated_at) >= datetime('now', '-30 days')",
            }
            period_sql = _period_map.get(period, "") if period else ""

            total_count = conn.execute(
                f"SELECT COUNT(*) FROM ops_trade_chains t "
                f"WHERE t.lifecycle_state IN ({closed_placeholders}) AND {scope_frag} {side_sql} {period_sql}",
                [*_CLOSED_STATES, *scope_params, *side_params],
            ).fetchone()[0]

            rows = conn.execute(
                f"SELECT t.trade_chain_id, t.symbol, t.side, t.trader_id, t.account_id, t.created_at, "
                f"COALESCE(t.{closed_at_expr}, t.updated_at) as closed_at, "
                f"t.cumulative_gross_pnl, t.lifecycle_state, "
                f"(SELECT json_extract(le.payload_json, '$.reason') "
                f" FROM ops_lifecycle_events le "
                f" WHERE le.trade_chain_id = t.trade_chain_id "
                f" AND le.event_type IN ('POSITION_CLOSED','POSITION_CANCELLED','SL_HIT','TP_HIT') "
                f" ORDER BY le.event_id DESC LIMIT 1) as close_reason "
                f"FROM ops_trade_chains t "
                f"WHERE t.lifecycle_state IN ({closed_placeholders}) AND {scope_frag} {side_sql} {period_sql} "
                f"ORDER BY t.{closed_at_expr} DESC, t.trade_chain_id DESC "
                f"LIMIT ? OFFSET ?",
                [*_CLOSED_STATES, *scope_params, *side_params, page_size, offset],
            ).fetchall()
        finally:
            conn.close()

        trade_rows = [
            ClosedTradeRow(
                chain_id=r[0],
                symbol=r[1],
                side=r[2],
                closed_at=r[6],
                gross_pnl=float(r[7]) if r[7] is not None else None,
                lifecycle_state=r[8],
                closed_reason=r[9],
                trader_id=r[3],
                account_id=r[4],
                created_at=r[5],
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

    def _load_chain_issue_evidence(
        self,
        conn: sqlite3.Connection,
        scope: QueryScope,
        *,
        side: str | None = None,
    ) -> dict[int, _ChainIssueEvidence]:
        scope_frag, scope_params = _scope_where(scope)
        side_sql = "AND side=?" if side else ""
        side_params = [side] if side else []

        chain_rows = conn.execute(
            "SELECT trade_chain_id, account_id, trader_id, symbol, side, lifecycle_state, "
            "COALESCE(filled_entry_qty, 0), COALESCE(open_position_qty, 0) "
            "FROM ops_trade_chains "
            f"WHERE {scope_frag} {side_sql}",
            [*scope_params, *side_params],
        ).fetchall()
        evidence_by_chain = {
            row[0]: _ChainIssueEvidence(
                trade_chain_id=row[0],
                account_id=row[1],
                trader_id=row[2],
                symbol=row[3],
                side=row[4],
                lifecycle_state=row[5],
                filled_entry_qty=float(row[6] or 0.0),
                open_position_qty=float(row[7] or 0.0),
            )
            for row in chain_rows
        }
        if not evidence_by_chain:
            return evidence_by_chain

        chain_ids = list(evidence_by_chain)
        placeholders = ",".join("?" * len(chain_ids))

        for row in conn.execute(
            "SELECT trade_chain_id, command_type, status, payload_json, result_payload_json, "
            "created_at, updated_at, acknowledged_at, completed_at "
            "FROM ops_execution_commands "
            f"WHERE trade_chain_id IN ({placeholders}) "
            "ORDER BY created_at, command_id",
            chain_ids,
        ).fetchall():
            (
                chain_id,
                command_type,
                status,
                payload_json,
                result_payload_json,
                created_at,
                updated_at,
                acknowledged_at,
                completed_at,
            ) = row
            evidence = evidence_by_chain.get(chain_id)
            if evidence is None:
                continue
            effective_at = _command_effective_timestamp(
                status=status,
                created_at=created_at,
                updated_at=updated_at,
                acknowledged_at=acknowledged_at,
                completed_at=completed_at,
            )
            if (
                command_type in _ENTRY_COMMAND_TYPES
                and status in _ENTRY_ACK_STATUSES
            ):
                evidence.entry_acknowledged = True
                evidence.first_entry_evidence_at = _earliest_timestamp(
                    evidence.first_entry_evidence_at,
                    effective_at,
                )
            if status != "FAILED":
                continue
            candidate = _IssueCandidate(
                kind="COMMAND_FAILED",
                command_type=command_type,
                reason=_extract_command_reason(result_payload_json, payload_json),
                occurred_at=effective_at,
                phase=_phase_for_command(command_type),
                details_command=command_type or "UNKNOWN_COMMAND",
            )
            if command_type in _ENTRY_COMMAND_TYPES:
                evidence.latest_entry_failure = _latest_candidate(
                    evidence.latest_entry_failure,
                    candidate,
                )
            else:
                evidence.latest_operational_failure = _latest_candidate(
                    evidence.latest_operational_failure,
                    candidate,
                )

        for row in conn.execute(
            "SELECT trade_chain_id, event_type, created_at "
            "FROM ops_lifecycle_events "
            f"WHERE trade_chain_id IN ({placeholders}) "
            "AND event_type IN ('ENTRY_OPENED', 'ENTRY_FILLED', 'ENTRY_PARTIALLY_FILLED') "
            "ORDER BY created_at, event_id",
            chain_ids,
        ).fetchall():
            chain_id, _event_type, created_at = row
            evidence = evidence_by_chain.get(chain_id)
            if evidence is None:
                continue
            evidence.first_entry_evidence_at = _earliest_timestamp(
                evidence.first_entry_evidence_at,
                created_at,
            )

        for row in conn.execute(
            "SELECT trade_chain_id, event_type, payload_json, created_at "
            "FROM ops_lifecycle_events "
            f"WHERE trade_chain_id IN ({placeholders}) "
            "AND event_type='REVIEW_REQUIRED' "
            "ORDER BY created_at, event_id",
            chain_ids,
        ).fetchall():
            chain_id, event_type, payload_json, created_at = row
            evidence = evidence_by_chain.get(chain_id)
            if evidence is None:
                continue
            candidate = _IssueCandidate(
                kind="REVIEW_REQUIRED",
                command_type=None,
                reason=_extract_reason(payload_json),
                occurred_at=created_at,
                phase=_phase_for_signal_event(event_type),
                details_command="REVIEW_REQUIRED",
            )
            if _review_is_post_entry(evidence, candidate):
                evidence.latest_post_entry_review = _latest_candidate(
                    evidence.latest_post_entry_review,
                    candidate,
                )
            else:
                evidence.latest_pre_entry_review = _latest_candidate(
                    evidence.latest_pre_entry_review,
                    candidate,
                )

        return evidence_by_chain

    def get_not_executed_trades(
        self,
        scope: QueryScope,
        *,
        side: str | None = None,
        outcome: str | None = None,
        phase: str | None = None,
    ) -> NotExecutedView:
        conn = self._connect()
        try:
            rows: list[NotExecutedRow] = []
            seen_signals: set[str] = set()
            seen_chains: set[int] = set()

            rejected_event_rows = conn.execute(
                "SELECT source_id, payload_json, created_at "
                "FROM ops_lifecycle_events "
                "WHERE trade_chain_id IS NULL AND event_type='SIGNAL_REJECTED' "
                "ORDER BY created_at DESC, event_id DESC",
            ).fetchall()

            # Build source_id → clean_log_link map in a single query
            _rej_source_ids = [
                r[0] for r in rejected_event_rows if r[0] is not None
            ]
            _clean_log_link_by_source: dict[str, str] = {}
            if _rej_source_ids:
                _placeholders = ",".join("?" * len(_rej_source_ids))
                _keys = [f"clean:signal_rejected:{sid}" for sid in _rej_source_ids]
                _key_placeholders = ",".join("?" * len(_keys))
                for _dk, _msg_id, _chat_id in conn.execute(
                    f"SELECT dedupe_key, sent_message_id, sent_chat_id "
                    f"FROM ops_notification_outbox "
                    f"WHERE dedupe_key IN ({_key_placeholders}) AND status='SENT'",
                    _keys,
                ).fetchall():
                    if _msg_id and _chat_id:
                        _normalized = str(_chat_id).removeprefix("-100")
                        _sid = _dk.removeprefix("clean:signal_rejected:")
                        _clean_log_link_by_source[_sid] = f"https://t.me/c/{_normalized}/{_msg_id}"

            for row in rejected_event_rows:
                source_id, payload_json, created_at = row
                payload = _parse_json_blob(payload_json)
                account_id = payload.get("account_id")
                trader_id = payload.get("trader_id")
                symbol = payload.get("symbol")
                signal_side = payload.get("side")
                if not _matches_scope_values(
                    scope,
                    account_id=account_id,
                    trader_id=trader_id,
                ):
                    continue
                if side and signal_side != side:
                    continue
                signal_reference = None
                if source_id is not None:
                    try:
                        signal_reference = int(source_id)
                    except (TypeError, ValueError):
                        signal_reference = None
                reference = f"#S-{source_id}" if source_id is not None else "#S-?"
                if reference in seen_signals:
                    continue
                row_outcome = "REJECTED"
                row_phase = _phase_for_signal_event("SIGNAL_REJECTED")
                if outcome and row_outcome != outcome:
                    continue
                if phase and row_phase != phase:
                    continue
                rows.append(
                    NotExecutedRow(
                        reference=reference,
                        trade_chain_id=None,
                        signal_reference=signal_reference,
                        account_id=account_id,
                        trader_id=trader_id,
                        symbol=symbol,
                        side=signal_side,
                        outcome=row_outcome,
                        phase=row_phase,
                        reason=_extract_reason(payload_json),
                        command_type=None,
                        occurred_at=created_at,
                        details_command="SIGNAL_REJECTED",
                        clean_log_link=_clean_log_link_by_source.get(str(source_id)) if source_id else None,
                    )
                )
                seen_signals.add(reference)

            evidence_by_chain = self._load_chain_issue_evidence(conn, scope, side=side)
            for chain_id, evidence in evidence_by_chain.items():
                if chain_id in seen_chains:
                    continue
                if _entry_evidence_present(evidence):
                    continue

                candidate = _latest_candidate(
                    evidence.latest_pre_entry_review,
                    evidence.latest_entry_failure,
                )
                if candidate is None:
                    continue

                row_outcome = (
                    "REVIEW_REQUIRED"
                    if candidate.kind == "REVIEW_REQUIRED"
                    else "NOT_EXECUTED"
                )
                if outcome and row_outcome != outcome:
                    continue
                if phase and candidate.phase != phase:
                    continue
                rows.append(
                    NotExecutedRow(
                        reference=f"#{chain_id}",
                        trade_chain_id=chain_id,
                        signal_reference=None,
                        account_id=evidence.account_id,
                        trader_id=evidence.trader_id,
                        symbol=evidence.symbol,
                        side=evidence.side,
                        outcome=row_outcome,
                        phase=candidate.phase,
                        reason=candidate.reason,
                        command_type=candidate.command_type,
                        occurred_at=candidate.occurred_at,
                        details_command=candidate.details_command,
                    )
                )
                seen_chains.add(chain_id)
        finally:
            conn.close()

        rows.sort(key=lambda item: item.occurred_at, reverse=True)
        return NotExecutedView(updated_at=_now_iso(), rows=rows)

    def get_operational_issues(
        self,
        scope: QueryScope,
        *,
        side: str | None = None,
        issue_type: str | None = None,
        phase: str | None = None,
    ) -> OperationalIssuesView:
        conn = self._connect()
        try:
            evidence_by_chain = self._load_chain_issue_evidence(conn, scope, side=side)
        finally:
            conn.close()

        rows: list[OperationalIssueRow] = []
        for chain_id, evidence in evidence_by_chain.items():
            if not _entry_evidence_present(evidence):
                continue

            candidate = _latest_candidate(
                evidence.latest_post_entry_review,
                evidence.latest_operational_failure,
                evidence.latest_entry_failure,
            )
            if candidate is None:
                continue

            row_issue_type = candidate.kind
            if issue_type and row_issue_type != issue_type:
                continue
            if phase and candidate.phase != phase:
                continue

            rows.append(
                OperationalIssueRow(
                    trade_chain_id=chain_id,
                    account_id=evidence.account_id,
                    trader_id=evidence.trader_id,
                    symbol=evidence.symbol,
                    side=evidence.side,
                    issue_type=row_issue_type,
                    phase=candidate.phase,
                    reason=candidate.reason,
                    command_type=candidate.command_type,
                    occurred_at=candidate.occurred_at,
                    details_command=candidate.details_command,
                )
            )

        rows.sort(key=lambda item: item.occurred_at, reverse=True)
        return OperationalIssuesView(updated_at=_now_iso(), rows=rows)

    def get_blocked_trades(self, scope: QueryScope, side: str | None = None) -> BlockedTradesView:
        conn = self._connect()
        try:
            scope_frag, scope_params = _scope_where(scope)
            t_frag, t_params = _scope_where(scope, 't')

            side_sql = "AND side=?" if side else ""
            side_sql_t = "AND t.side=?" if side else ""
            side_params = [side] if side else []

            review_rows = conn.execute(
                f"SELECT trade_chain_id, symbol, trader_id, account_id, side FROM ops_trade_chains "
                f"WHERE lifecycle_state='REVIEW_REQUIRED' AND {scope_frag} {side_sql} "
                f"ORDER BY trade_chain_id",
                [*scope_params, *side_params],
            ).fetchall()

            reason_data: dict[int, tuple[str | None, str | None]] = {}
            for row in conn.execute(
                "SELECT trade_chain_id, payload_json, created_at FROM ops_lifecycle_events "
                "WHERE trade_chain_id IS NOT NULL "
                "ORDER BY event_id"
            ).fetchall():
                cid_r, pjson, cat = row[0], row[1], row[2]
                if pjson:
                    try:
                        reason_candidate = json.loads(pjson).get("reason")
                    except Exception:
                        reason_candidate = None
                    if reason_candidate:
                        reason_data[cid_r] = (pjson, cat)

            exec_failed_rows = conn.execute(
                f"SELECT DISTINCT t.trade_chain_id, t.symbol, t.trader_id, t.account_id, t.side, "
                f"ec.payload_json, ec.result_payload_json, ec.created_at, ec.updated_at, ec.completed_at "
                f"FROM ops_execution_commands ec "
                f"JOIN ops_trade_chains t ON t.trade_chain_id = ec.trade_chain_id "
                f"WHERE ec.status='FAILED' AND {t_frag} {side_sql_t} "
                f"ORDER BY t.trade_chain_id",
                [*t_params, *side_params],
            ).fetchall()
        finally:
            conn.close()

        result_rows: list[BlockedTradeRow] = []
        seen: set[int] = set()

        for cid, symbol, trader_id, account_id, side in review_rows:
            reason: str | None = None
            blocked_at: str | None = None
            raw_payload, raw_at = reason_data.get(cid, (None, None))
            if raw_payload:
                try:
                    reason = json.loads(raw_payload).get("reason")
                except Exception:
                    pass
            if raw_at and len(raw_at) >= 16:
                try:
                    from datetime import datetime as _dt
                    dt = _dt.fromisoformat(raw_at.rstrip("Z"))
                    blocked_at = dt.strftime("%-d %b %H:%M")
                except Exception:
                    blocked_at = raw_at[:16]
            result_rows.append(BlockedTradeRow(
                chain_id=cid,
                symbol=symbol,
                state="REVIEW_REQUIRED",
                reason=reason,
                trader_id=trader_id,
                account_id=account_id,
                side=side,
                blocked_at=blocked_at,
            ))
            seen.add(cid)

        for (
            cid,
            symbol,
            trader_id,
            account_id,
            side,
            payload_json,
            result_payload_json,
            created_at,
            updated_at,
            completed_at,
        ) in exec_failed_rows:
            if cid in seen:
                continue
            reason = _extract_command_reason(result_payload_json, payload_json)
            blocked_at = None
            effective_at = completed_at or updated_at or created_at
            raw_blocked_at = effective_at or created_at
            if raw_blocked_at and len(raw_blocked_at) >= 16:
                try:
                    from datetime import datetime as _dt
                    dt = _dt.fromisoformat(raw_blocked_at.rstrip("Z"))
                    blocked_at = dt.strftime("%-d %b %H:%M")
                except Exception:
                    blocked_at = raw_blocked_at[:16]
            result_rows.append(BlockedTradeRow(
                chain_id=cid,
                symbol=symbol,
                state="EXEC_FAILED",
                reason=reason,
                trader_id=trader_id,
                account_id=account_id,
                side=side,
                blocked_at=blocked_at,
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

    def get_status_by_account(self, accounts: list[str]) -> list[dict]:
        """Per ogni account, ritorna conteggi open/waiting/failed per il breakdown global scope."""
        conn = self._connect()
        try:
            result = []
            for acc in accounts:
                open_c = conn.execute(
                    "SELECT COUNT(*) FROM ops_trade_chains "
                    "WHERE lifecycle_state='OPEN' AND account_id=?", (acc,)
                ).fetchone()[0]
                waiting_c = conn.execute(
                    "SELECT COUNT(*) FROM ops_trade_chains "
                    "WHERE lifecycle_state='WAITING_ENTRY' AND account_id=?", (acc,)
                ).fetchone()[0]
                failed_c = conn.execute(
                    "SELECT COUNT(*) FROM ops_execution_commands ec "
                    "JOIN ops_trade_chains t ON t.trade_chain_id = ec.trade_chain_id "
                    "WHERE ec.status='FAILED' AND t.account_id=?", (acc,)
                ).fetchone()[0]
                result.append({
                    "account_id": acc,
                    "open_count": open_c,
                    "waiting_count": waiting_c,
                    "failed_commands": failed_c,
                })
        finally:
            conn.close()
        return result


__all__ = [
    "StatusQueries", "StatusView", "TradesView", "TradeRow", "CloseCandidate",
    "TradeEvent", "TradeDetail",
    "HealthView", "ControlView", "BlockInfo", "ReviewsView", "ReviewItem",
    "PnlView", "StatsView", "StatsRow", "ClosedTradesView", "ClosedTradeRow",
    "BlockedTradesView", "BlockedTradeRow",
    "NotExecutedView", "NotExecutedRow",
    "OperationalIssuesView", "OperationalIssueRow",
]
