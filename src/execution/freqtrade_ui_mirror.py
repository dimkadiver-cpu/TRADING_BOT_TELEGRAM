"""Best-effort mirror from bridge runtime state to Freqtrade trades DB.

This module is intentionally defensive: mirror failures must never break
execution callbacks.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path

from src.execution.freqtrade_normalizer import canonical_symbol_to_freqtrade_pair

_log = logging.getLogger(__name__)


def mirror_entry_fill(
    *,
    attempt_key: str,
    symbol: str,
    side: str,
    qty: float,
    fill_price: float,
    opened_at: str,
    exchange_order_id: str | None = None,
    client_order_id: str | None = None,
    order_type: str = "market",
    strategy_name: str = "SignalBridgeStrategy",
) -> None:
    if not _should_mirror():
        return
    db_path = _resolve_freqtrade_trades_db_path()
    if not db_path:
        return
    bot_db_path = _resolve_bot_db_path()
    stoploss_ref = _load_signal_stoploss(attempt_key=attempt_key, bot_db_path=bot_db_path)
    try:
        with sqlite3.connect(db_path) as conn:
            if not _has_required_tables(conn):
                return
            trade_id = _upsert_open_trade(
                conn=conn,
                attempt_key=attempt_key,
                symbol=symbol,
                side=side,
                qty=qty,
                fill_price=fill_price,
                opened_at=opened_at,
                strategy_name=strategy_name,
                stoploss_ref=stoploss_ref,
            )
            _upsert_entry_order(
                conn=conn,
                trade_id=trade_id,
                attempt_key=attempt_key,
                symbol=symbol,
                side=side,
                qty=qty,
                fill_price=fill_price,
                opened_at=opened_at,
                exchange_order_id=exchange_order_id,
                client_order_id=client_order_id,
                order_type=order_type,
            )
            conn.commit()
    except Exception:
        _log.debug("freqtrade UI mirror entry fill failed", exc_info=True)


def mirror_position_update(
    *,
    attempt_key: str,
    remaining_qty: float,
    exit_price: float | None,
    updated_at: str,
    close_reason: str | None = None,
) -> None:
    if not _should_mirror():
        return
    db_path = _resolve_freqtrade_trades_db_path()
    if not db_path:
        return
    try:
        with sqlite3.connect(db_path) as conn:
            if not _has_required_tables(conn):
                return
            row = _find_trade_row_by_attempt_key(conn=conn, attempt_key=attempt_key)
            if not row:
                return
            trade_id = int(row[0])
            if remaining_qty <= 0:
                conn.execute(
                    """
                    UPDATE trades
                    SET is_open = 0,
                        amount = 0.0,
                        close_date = ?,
                        close_rate = COALESCE(?, close_rate),
                        exit_reason = COALESCE(?, exit_reason),
                        exit_order_status = 'closed',
                        max_rate = COALESCE(max_rate, open_rate),
                        min_rate = COALESCE(min_rate, open_rate)
                    WHERE id = ?
                    """,
                    (updated_at, exit_price, close_reason, trade_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE trades
                    SET is_open = 1,
                        amount = ?,
                        max_rate = CASE
                            WHEN ? IS NULL THEN max_rate
                            WHEN max_rate IS NULL THEN ?
                            ELSE MAX(max_rate, ?)
                        END,
                        min_rate = CASE
                            WHEN ? IS NULL THEN min_rate
                            WHEN min_rate IS NULL THEN ?
                            ELSE MIN(min_rate, ?)
                        END
                    WHERE id = ?
                    """,
                    (
                        float(remaining_qty),
                        exit_price,
                        exit_price,
                        exit_price,
                        exit_price,
                        exit_price,
                        exit_price,
                        trade_id,
                    ),
                )
            conn.commit()
    except Exception:
        _log.debug("freqtrade UI mirror position update failed", exc_info=True)


def mirror_trade_stoploss(
    *,
    attempt_key: str,
    stoploss_ref: float | None = None,
    bot_db_path: str | None = None,
    freqtrade_db_path: str | None = None,
) -> None:
    """Best-effort sync of trade-level stoploss fields for FreqUI."""
    if not _should_mirror():
        return
    db_path = freqtrade_db_path or _resolve_freqtrade_trades_db_path()
    if not db_path:
        return
    resolved_bot_db_path = bot_db_path or _resolve_bot_db_path()
    resolved_stoploss = (
        float(stoploss_ref)
        if isinstance(stoploss_ref, (int, float)) and float(stoploss_ref) > 0
        else _load_signal_stoploss(attempt_key=attempt_key, bot_db_path=resolved_bot_db_path)
    )
    if resolved_stoploss is None or resolved_stoploss <= 0:
        return
    try:
        with sqlite3.connect(db_path) as conn:
            row = _find_trade_row_by_attempt_key(
                conn=conn,
                attempt_key=attempt_key,
                columns="id, open_rate, is_short",
            )
            if not row:
                return
            reference_price = float(row[1]) if isinstance(row[1], (int, float)) and float(row[1]) > 0 else None
            if reference_price is None:
                return
            side = "SHORT" if bool(row[2]) else "LONG"
            stoploss_pct = _absolute_stop_to_relative(
                side=side,
                stop_price=float(resolved_stoploss),
                reference_price=reference_price,
            )
            conn.execute(
                """
                UPDATE trades
                SET stop_loss = ?,
                    initial_stop_loss = COALESCE(initial_stop_loss, ?),
                    stop_loss_pct = ?,
                    initial_stop_loss_pct = COALESCE(initial_stop_loss_pct, ?)
                WHERE id = ?
                """,
                (
                    float(resolved_stoploss),
                    float(resolved_stoploss),
                    stoploss_pct,
                    stoploss_pct,
                    int(row[0]),
                ),
            )
            conn.commit()
    except Exception:
        _log.debug("freqtrade UI mirror stoploss sync failed", exc_info=True)


def _resolve_freqtrade_trades_db_path() -> str | None:
    explicit = os.getenv("TELESIGNALBOT_FREQTRADE_TRADES_DB_PATH")
    if explicit and Path(explicit).exists():
        return str(Path(explicit))

    repo_root = Path(__file__).resolve().parents[2]
    candidates = (
        repo_root / "freqtrade" / "tradesv3.dryrun.sqlite",
        repo_root / "freqtrade" / "user_data" / "tradesv3.dryrun.sqlite",
    )
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def _is_pytest_context() -> bool:
    marker = os.getenv("PYTEST_CURRENT_TEST")
    return isinstance(marker, str) and bool(marker.strip())


def _should_mirror() -> bool:
    # During pytest runs we only mirror if an explicit freqtrade DB path was set
    # for the test. This prevents accidental writes to the real runtime DB.
    if not _is_pytest_context():
        return True
    explicit = os.getenv("TELESIGNALBOT_FREQTRADE_TRADES_DB_PATH")
    return isinstance(explicit, str) and bool(explicit.strip())


def _resolve_bot_db_path() -> str | None:
    explicit = os.getenv("TELESIGNALBOT_DB_PATH") or os.getenv("DB_PATH")
    if explicit:
        candidate = Path(explicit)
        if candidate.exists():
            return str(candidate)
    repo_root = Path(__file__).resolve().parents[2]
    fallback = repo_root / "db" / "tele_signal_bot.sqlite3"
    if fallback.exists():
        return str(fallback)
    return None


def _load_signal_stoploss(*, attempt_key: str, bot_db_path: str | None) -> float | None:
    if not bot_db_path:
        return None
    try:
        with sqlite3.connect(bot_db_path) as conn:
            row = conn.execute(
                "SELECT sl FROM signals WHERE attempt_key = ? ORDER BY rowid DESC LIMIT 1",
                (attempt_key,),
            ).fetchone()
    except Exception:
        return None
    if not row:
        return None
    value = row[0]
    return float(value) if isinstance(value, (int, float)) and float(value) > 0 else None


def _has_required_tables(conn: sqlite3.Connection) -> bool:
    trades_ok = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='trades' LIMIT 1"
    ).fetchone()
    orders_ok = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='orders' LIMIT 1"
    ).fetchone()
    return bool(trades_ok and orders_ok)


def _upsert_open_trade(
    *,
    conn: sqlite3.Connection,
    attempt_key: str,
    symbol: str,
    side: str,
    qty: float,
    fill_price: float,
    opened_at: str,
    strategy_name: str,
    stoploss_ref: float | None,
) -> int:
    existing = _find_trade_row_by_attempt_key(conn=conn, attempt_key=attempt_key)
    pair = canonical_symbol_to_freqtrade_pair(symbol) or symbol
    base_currency, stake_currency = _split_pair(pair)
    is_short = 1 if side.strip().upper() in {"SELL", "SHORT"} else 0
    stake_amount = float(qty) * float(fill_price)
    resolved_stoploss = float(stoploss_ref) if isinstance(stoploss_ref, (int, float)) and float(stoploss_ref) > 0 else float(fill_price)
    stoploss_pct = _absolute_stop_to_relative(
        side=side,
        stop_price=resolved_stoploss,
        reference_price=float(fill_price),
    )

    if existing:
        trade_id = int(existing[0])
        conn.execute(
            """
            UPDATE trades
            SET is_open = 1,
                amount = ?,
                stake_amount = ?,
                open_rate = COALESCE(open_rate, ?),
                open_trade_value = COALESCE(open_trade_value, ?),
                open_date = COALESCE(open_date, ?),
                timeframe = COALESCE(timeframe, 1),
                stop_loss = COALESCE(?, stop_loss),
                initial_stop_loss = COALESCE(?, initial_stop_loss),
                stop_loss_pct = COALESCE(?, stop_loss_pct),
                initial_stop_loss_pct = COALESCE(?, initial_stop_loss_pct),
                realized_profit = COALESCE(realized_profit, 0.0),
                close_profit = COALESCE(close_profit, 0.0),
                close_profit_abs = COALESCE(close_profit_abs, 0.0),
                enter_tag = ?
            WHERE id = ?
            """,
            (
                float(qty),
                stake_amount,
                float(fill_price),
                stake_amount,
                opened_at,
                resolved_stoploss,
                resolved_stoploss,
                stoploss_pct,
                stoploss_pct,
                attempt_key,
                trade_id,
            ),
        )
        return trade_id

    conn.execute(
        """
        INSERT INTO trades(
          exchange, pair, base_currency, stake_currency,
          is_open, fee_open, fee_close, open_rate, open_rate_requested, open_trade_value,
          stake_amount, max_stake_amount, amount, amount_requested, open_date,
          is_stop_loss_trailing, strategy, enter_tag, timeframe, trading_mode, leverage, is_short,
          interest_rate, record_version, max_rate, min_rate
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "bybit",
            pair,
            base_currency,
            stake_currency,
            1,
            0.0,
            0.0,
            float(fill_price),
            float(fill_price),
            stake_amount,
            stake_amount,
            stake_amount,
            float(qty),
            float(qty),
            opened_at,
            0,
            strategy_name,
            attempt_key,
            1,
            "FUTURES",
            1.0,
            is_short,
            0.0,
            2,
            float(fill_price),
            float(fill_price),
        ),
    )
    conn.execute(
        """
        UPDATE trades
        SET stop_loss = ?,
            initial_stop_loss = ?,
            stop_loss_pct = ?,
            initial_stop_loss_pct = ?,
            realized_profit = COALESCE(realized_profit, 0.0),
            close_profit = COALESCE(close_profit, 0.0),
            close_profit_abs = COALESCE(close_profit_abs, 0.0)
        WHERE id = last_insert_rowid()
        """,
        (resolved_stoploss, resolved_stoploss, stoploss_pct, stoploss_pct),
    )
    row = conn.execute("SELECT last_insert_rowid()").fetchone()
    return int(row[0]) if row else 0


def _upsert_entry_order(
    *,
    conn: sqlite3.Connection,
    trade_id: int,
    attempt_key: str,
    symbol: str,
    side: str,
    qty: float,
    fill_price: float,
    opened_at: str,
    exchange_order_id: str | None,
    client_order_id: str | None,
    order_type: str = "market",
) -> None:
    pair = canonical_symbol_to_freqtrade_pair(symbol) or symbol
    order_id = exchange_order_id or client_order_id or f"{attempt_key}:ENTRY:0"
    order_side = "buy" if side.strip().upper() in {"BUY", "LONG"} else "sell"
    conn.execute(
        """
        INSERT INTO orders(
          ft_trade_id, ft_order_side, ft_pair, ft_is_open, ft_amount, ft_price,
          order_id, status, symbol, order_type, side, price, average, amount, filled,
          remaining, cost, order_date, order_filled_date, order_update_date, ft_order_tag
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(ft_pair, order_id) DO UPDATE SET
          ft_trade_id=excluded.ft_trade_id,
          ft_is_open=excluded.ft_is_open,
          status=excluded.status,
          price=excluded.price,
          average=excluded.average,
          amount=excluded.amount,
          filled=excluded.filled,
          remaining=excluded.remaining,
          cost=excluded.cost,
          order_update_date=excluded.order_update_date
        """,
        (
            trade_id,
            order_side,
            pair,
            0,
            float(qty),
            float(fill_price),
            order_id,
            "closed",
            pair,
            order_type.lower(),
            order_side,
            float(fill_price),
            float(fill_price),
            float(qty),
            float(qty),
            0.0,
            float(qty) * float(fill_price),
            opened_at,
            opened_at,
            opened_at,
            attempt_key,
        ),
    )


def _split_pair(pair: str) -> tuple[str | None, str | None]:
    if not pair:
        return None, None
    normalized = pair
    if ":" in normalized:
        normalized = normalized.split(":", 1)[0]
    if "/" not in normalized:
        return None, None
    base, quote = normalized.split("/", 1)
    return base or None, quote or None


def _find_trade_row_by_attempt_key(
    *,
    conn: sqlite3.Connection,
    attempt_key: str,
    columns: str = "id",
) -> sqlite3.Row | tuple | None:
    entry_like = f"{attempt_key}:ENTRY:%"
    return conn.execute(
        f"""
        SELECT {columns}
        FROM trades
        WHERE enter_tag = ?
           OR enter_tag LIKE ?
        ORDER BY
          CASE
            WHEN enter_tag = ? THEN 0
            WHEN enter_tag LIKE ? THEN 1
            ELSE 2
          END,
          COALESCE(is_open, 0) DESC,
          id DESC
        LIMIT 1
        """,
        (attempt_key, entry_like, attempt_key, entry_like),
    ).fetchone()


def _absolute_stop_to_relative(*, side: str, stop_price: float, reference_price: float) -> float:
    if reference_price <= 0:
        return -0.99
    normalized = side.strip().upper()
    if normalized in {"BUY", "LONG"}:
        return min(0.0, (float(stop_price) / float(reference_price)) - 1.0)
    if normalized in {"SELL", "SHORT"}:
        return min(0.0, 1.0 - (float(stop_price) / float(reference_price)))
    return -0.99
