"""Batch script: apply operation rules to parse_results → operational_signals + signals.

Reads parse_results (NEW_SIGNAL + UPDATE) from the target DB in chronological
order, runs them through OperationRulesEngine and TargetResolver, and writes
the output to operational_signals and signals.  Designed for use with the
backtesting DB (db/backtest.sqlite3), never against the live DB.

Usage:
    python parser_test/scripts/replay_operation_rules.py \\
        --db-path db/backtest.sqlite3 \\
        --rules-dir config \\
        --trader trader_3 \\
        --from-date 2025-01-01 \\
        --to-date   2025-12-31

    # Preview without writing:
    python parser_test/scripts/replay_operation_rules.py \\
        --db-path db/backtest.sqlite3 --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.migrations import apply_migrations
from src.operation_rules.engine import OperationRulesEngine
from src.parser.trader_profiles.base import TraderParseResult
from src.storage.operational_signals_store import (
    OperationalSignalRecord,
    OperationalSignalsStore,
)
from src.storage.signals_store import SignalRecord, SignalsStore
from src.target_resolver.resolver import TargetResolver

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay operation rules on a backtest DB.",
    )
    parser.add_argument(
        "--db-path",
        required=True,
        help="Path to the backtest SQLite DB (e.g. db/backtest.sqlite3).",
    )
    parser.add_argument(
        "--rules-dir",
        default="config",
        help="Directory containing operation_rules.yaml and trader_rules/ (default: config).",
    )
    parser.add_argument(
        "--trader",
        default=None,
        help="Filter by resolved_trader_id (e.g. trader_3).",
    )
    parser.add_argument(
        "--from-date",
        default=None,
        help="Process only messages with message_ts >= YYYY-MM-DD.",
    )
    parser.add_argument(
        "--to-date",
        default=None,
        help="Process only messages with message_ts <= YYYY-MM-DD.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Process signals without writing to DB.",
    )
    parser.add_argument(
        "--no-skip-capital-gates",
        action="store_true",
        help="Re-enable capital management gates (max_concurrent_same_symbol, "
             "trader_capital_at_risk, global_capital_at_risk). "
             "By default these are skipped in replay mode because positions "
             "never close in the DB.",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Safety guard
# ---------------------------------------------------------------------------


def _ensure_not_live_db(db_path: str) -> None:
    candidate = Path(db_path).resolve()
    live = (PROJECT_ROOT / "db" / "tele_signal_bot.sqlite3").resolve()
    if candidate == live:
        raise RuntimeError(
            f"Refusing to run on live DB path: {db_path}\n"
            "Pass --db-path with a backtest DB path (e.g. db/backtest.sqlite3)."
        )


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ParseResultRow:
    parse_result_id: int
    raw_message_id: int
    resolved_trader_id: str | None
    message_type: str
    parse_result_normalized_json: str | None
    message_ts: str
    source_chat_id: str
    telegram_message_id: int
    reply_to_message_id: int | None


def _fetch_parse_results(
    db_path: str,
    *,
    trader: str | None,
    from_date: str | None,
    to_date: str | None,
) -> list[ParseResultRow]:
    """Fetch parse_results (NEW_SIGNAL executable + all UPDATE) in chronological order."""
    query_parts = [
        """
        SELECT
            pr.parse_result_id,
            pr.raw_message_id,
            pr.resolved_trader_id,
            pr.message_type,
            pr.parse_result_normalized_json,
            rm.message_ts,
            rm.source_chat_id,
            rm.telegram_message_id,
            rm.reply_to_message_id
        FROM parse_results pr
        JOIN raw_messages rm ON pr.raw_message_id = rm.raw_message_id
        WHERE (pr.is_executable = 1 OR pr.message_type = 'UPDATE')
        """.strip()
    ]
    params: list[object] = []

    if trader:
        query_parts.append("AND pr.resolved_trader_id = ?")
        params.append(trader)
    if from_date:
        query_parts.append("AND rm.message_ts >= ?")
        params.append(_normalize_date(from_date, end_of_day=False))
    if to_date:
        query_parts.append("AND rm.message_ts <= ?")
        params.append(_normalize_date(to_date, end_of_day=True))

    query_parts.append("ORDER BY rm.message_ts ASC, rm.raw_message_id ASC")

    sql = "\n".join(query_parts)
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()

    return [
        ParseResultRow(
            parse_result_id=int(row[0]),
            raw_message_id=int(row[1]),
            resolved_trader_id=row[2],
            message_type=str(row[3]),
            parse_result_normalized_json=row[4],
            message_ts=str(row[5]),
            source_chat_id=str(row[6]),
            telegram_message_id=int(row[7]),
            reply_to_message_id=int(row[8]) if row[8] is not None else None,
        )
        for row in rows
    ]


def _normalize_date(value: str, end_of_day: bool) -> str:
    text = value.strip()
    if "T" not in text:
        day = datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if end_of_day:
            day = day.replace(hour=23, minute=59, second=59, microsecond=999999)
        return day.isoformat()
    normalized = text.replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat()


# ---------------------------------------------------------------------------
# TraderParseResult reconstruction
# ---------------------------------------------------------------------------


def _reconstruct_parse_result(
    normalized_json: str,
    *,
    row: ParseResultRow,
) -> TraderParseResult | None:
    """Deserialize a stored parse_result_normalized_json into TraderParseResult.

    Returns None on parse error.
    """
    try:
        data: dict = json.loads(normalized_json)
    except (json.JSONDecodeError, TypeError):
        return None

    return TraderParseResult(
        message_type=data.get("message_type", row.message_type),
        intents=data.get("intents") or [],
        entities=data.get("entities") or {},
        target_refs=data.get("target_refs") or [],
        actions_structured=data.get("actions_structured") or [],
        warnings=data.get("warnings") or [],
        confidence=float(data.get("confidence") or 0.0),
        linking=data.get("linking") or {},
    )


# ---------------------------------------------------------------------------
# Signal insertion helpers
# ---------------------------------------------------------------------------


def _extract_symbol_side(entities: object) -> tuple[str | None, str | None]:
    """Extract symbol and side (BUY/SELL) from entities dict."""
    if not isinstance(entities, dict):
        return None, None
    symbol: str | None = entities.get("symbol") or None
    direction: str = str(entities.get("direction") or entities.get("side") or "").upper()
    if direction in ("LONG", "BUY"):
        side = "BUY"
    elif direction in ("SHORT", "SELL"):
        side = "SELL"
    else:
        side = None
    return symbol, side


def _extract_sl(entities: object) -> float | None:
    """Extract stop-loss price from entities dict."""
    if not isinstance(entities, dict):
        return None
    sl_obj = entities.get("stop_loss") or entities.get("sl")
    if isinstance(sl_obj, (int, float)):
        return float(sl_obj)
    if isinstance(sl_obj, dict):
        price = sl_obj.get("price") or sl_obj.get("value")
        if isinstance(price, dict):
            price = price.get("value")
        if price is not None:
            try:
                return float(price)
            except (TypeError, ValueError):
                pass
    return None


def _build_signal_record(
    *,
    attempt_key: str,
    row: ParseResultRow,
    parse_result: TraderParseResult,
    trader_id: str,
) -> SignalRecord:
    entities = parse_result.entities if isinstance(parse_result.entities, dict) else {}
    symbol, side = _extract_symbol_side(entities)
    sl = _extract_sl(entities)
    now = datetime.now(timezone.utc).isoformat()
    return SignalRecord(
        attempt_key=attempt_key,
        env="BT",
        channel_id=row.source_chat_id,
        root_telegram_id=str(row.telegram_message_id),
        trader_id=trader_id,
        trader_prefix=trader_id[:3] if trader_id else "",
        symbol=symbol,
        side=side,
        entry_json=None,
        sl=sl,
        tp_json=None,
        status="PENDING",
        confidence=parse_result.confidence,
        raw_text="",
        created_at=now,
        updated_at=now,
    )


def _build_op_record(
    *,
    parse_result_id: int,
    attempt_key: str | None,
    trader_id: str,
    op_signal,
    resolved_target,
    now: str,
) -> OperationalSignalRecord:
    resolved_ids: str | None = None
    target_eligibility: str | None = None
    target_reason: str | None = None

    if resolved_target is not None:
        resolved_ids = json.dumps(resolved_target.position_ids)
        target_eligibility = resolved_target.eligibility
        target_reason = resolved_target.reason

    return OperationalSignalRecord(
        parse_result_id=parse_result_id,
        attempt_key=attempt_key,
        trader_id=trader_id,
        message_type=op_signal.parse_result.message_type,
        is_blocked=op_signal.is_blocked,
        block_reason=op_signal.block_reason,
        risk_mode=op_signal.risk_mode,
        risk_pct_of_capital=op_signal.risk_pct_of_capital,
        risk_usdt_fixed=op_signal.risk_usdt_fixed,
        capital_base_usdt=op_signal.capital_base_usdt,
        risk_budget_usdt=op_signal.risk_budget_usdt,
        sl_distance_pct=op_signal.sl_distance_pct,
        position_size_usdt=op_signal.position_size_usdt,
        position_size_pct=op_signal.position_size_pct,
        entry_split_json=(
            json.dumps(op_signal.entry_split) if op_signal.entry_split else None
        ),
        leverage=op_signal.leverage,
        risk_hint_used=op_signal.risk_hint_used,
        management_rules_json=(
            json.dumps(op_signal.management_rules) if op_signal.management_rules else None
        ),
        price_corrections_json=None,
        applied_rules_json=json.dumps(op_signal.applied_rules),
        warnings_json=json.dumps(op_signal.warnings),
        resolved_target_ids=resolved_ids,
        target_eligibility=target_eligibility,
        target_reason=target_reason,
        created_at=now,
    )


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


@dataclass
class ReplayStats:
    total: int = 0
    new_signal_inserted: int = 0
    new_signal_blocked: int = 0
    update_linked: int = 0
    update_orphan: int = 0
    errors: int = 0


# ---------------------------------------------------------------------------
# Main replay logic
# ---------------------------------------------------------------------------


def run_replay(
    *,
    db_path: str,
    rules_dir: str = "config",
    trader: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    dry_run: bool = False,
    skip_capital_gates: bool = True,
) -> ReplayStats:
    """Process parse_results and write operational_signals + signals.

    Args:
        db_path:             Path to the backtest SQLite DB.
        rules_dir:           Directory with operation_rules.yaml and trader_rules/.
        trader:              Optional trader_id filter.
        from_date:           Optional lower-bound date filter (YYYY-MM-DD).
        to_date:             Optional upper-bound date filter (YYYY-MM-DD).
        dry_run:             When True, process but do not write to DB.
        skip_capital_gates:  When True (default), bypass gates 5/7/8
                             (max_concurrent_same_symbol, trader_capital_at_risk,
                             global_capital_at_risk).  Set to False only when
                             simulating live behaviour.

    Returns:
        ReplayStats with processing counters.

    Raises:
        RuntimeError: if db_path resolves to the live DB.
    """
    _ensure_not_live_db(db_path)

    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    apply_migrations(
        db_path=db_path,
        migrations_dir=str(PROJECT_ROOT / "db" / "migrations"),
    )

    rows = _fetch_parse_results(
        db_path,
        trader=trader,
        from_date=from_date,
        to_date=to_date,
    )
    logger.info("Fetched %d parse_results to process", len(rows))

    engine = OperationRulesEngine(rules_dir=rules_dir)
    resolver = TargetResolver()
    op_store = OperationalSignalsStore(db_path)
    sig_store = SignalsStore(db_path)

    stats = ReplayStats()

    for row in rows:
        stats.total += 1
        now = datetime.now(timezone.utc).isoformat()
        trader_id = row.resolved_trader_id or ""

        try:
            if not row.parse_result_normalized_json:
                logger.warning(
                    "parse_result_id=%d has no normalized_json — skipping",
                    row.parse_result_id,
                )
                continue

            parse_result = _reconstruct_parse_result(
                row.parse_result_normalized_json,
                row=row,
            )
            if parse_result is None:
                logger.warning(
                    "parse_result_id=%d: failed to parse normalized_json — skipping",
                    row.parse_result_id,
                )
                continue

            # Apply operation rules gate
            op_signal = engine.apply(
                parse_result, trader_id, db_path=db_path,
                skip_capital_gates=skip_capital_gates,
            )

            attempt_key: str | None = None
            resolved_target = None

            if parse_result.message_type == "NEW_SIGNAL":
                if not op_signal.is_blocked:
                    attempt_key = f"{trader_id}:{row.telegram_message_id}"
                    if not dry_run:
                        sig_record = _build_signal_record(
                            attempt_key=attempt_key,
                            row=row,
                            parse_result=parse_result,
                            trader_id=trader_id,
                        )
                        sig_store.insert(sig_record)
                    stats.new_signal_inserted += 1
                    logger.info(
                        "parse_result_id=%d NEW_SIGNAL inserted: attempt_key=%s",
                        row.parse_result_id,
                        attempt_key,
                    )
                else:
                    stats.new_signal_blocked += 1
                    logger.info(
                        "parse_result_id=%d NEW_SIGNAL BLOCKED: %s",
                        row.parse_result_id,
                        op_signal.block_reason,
                    )

            elif parse_result.message_type == "UPDATE":
                resolved_target = resolver.resolve(op_signal, db_path=db_path)
                if resolved_target is not None and resolved_target.position_ids:
                    stats.update_linked += 1
                    logger.info(
                        "parse_result_id=%d UPDATE linked → %s",
                        row.parse_result_id,
                        resolved_target.position_ids,
                    )
                else:
                    stats.update_orphan += 1
                    logger.warning(
                        "parse_result_id=%d UPDATE orphan (no target found)",
                        row.parse_result_id,
                    )

            if not dry_run:
                op_record = _build_op_record(
                    parse_result_id=row.parse_result_id,
                    attempt_key=attempt_key,
                    trader_id=trader_id,
                    op_signal=op_signal,
                    resolved_target=resolved_target,
                    now=now,
                )
                op_store.insert(op_record)

        except Exception as exc:
            stats.errors += 1
            logger.error(
                "parse_result_id=%d error=%s\n%s",
                row.parse_result_id,
                exc,
                traceback.format_exc(),
            )

    return stats


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    args = parse_args()

    db_path = args.db_path
    if not Path(db_path).is_absolute():
        db_path = str((PROJECT_ROOT / db_path).resolve())

    stats = run_replay(
        db_path=db_path,
        rules_dir=args.rules_dir,
        trader=args.trader,
        from_date=args.from_date,
        to_date=args.to_date,
        dry_run=args.dry_run,
        skip_capital_gates=not args.no_skip_capital_gates,
    )

    print(f"db_path: {db_path}")
    print(f"dry_run: {args.dry_run}")
    print(f"skip_capital_gates: {not args.no_skip_capital_gates}")
    print(f"total processed:       {stats.total}")
    print(f"NEW_SIGNAL inserted:   {stats.new_signal_inserted}")
    print(f"NEW_SIGNAL blocked:    {stats.new_signal_blocked}")
    print(f"UPDATE linked:         {stats.update_linked}")
    print(f"UPDATE orphan:         {stats.update_orphan}")
    print(f"errors:                {stats.errors}")


if __name__ == "__main__":
    main()
