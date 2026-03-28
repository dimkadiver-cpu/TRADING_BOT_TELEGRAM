from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from src.core.migrations import apply_migrations
from src.execution.freqtrade_callback import order_filled_callback, partial_exit_callback, trade_exit_callback


def _make_db(tmp_path: Path) -> str:
    db_path = str(tmp_path / "freqtrade_callback.sqlite3")
    apply_migrations(db_path=db_path, migrations_dir=str(Path("db/migrations").resolve()))
    return db_path


def _insert_parse_result(db_path: str, *, parse_result_id: int = 1) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO parse_results
               (parse_result_id, raw_message_id, eligibility_status, eligibility_reason,
                resolved_trader_id, trader_resolution_method, message_type, parse_status,
                completeness, is_executable, risky_flag, created_at, updated_at)
               VALUES (?, ?, 'OK', 'ok', 'trader_3', 'direct', 'NEW_SIGNAL', 'PARSED',
                       'COMPLETE', 1, 0, '2026-01-01', '2026-01-01')""",
            (parse_result_id, parse_result_id),
        )
        conn.commit()


def _insert_signal(
    db_path: str,
    *,
    attempt_key: str,
    status: str = "PENDING",
    tp_prices: list[float] | None = None,
) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO signals
               (attempt_key, env, channel_id, root_telegram_id, trader_id, trader_prefix,
                symbol, side, entry_json, sl, tp_json, status, confidence, raw_text,
                created_at, updated_at)
               VALUES (?, 'T', '-100999', '1', 'trader_3', 'TRAD',
                       'BTCUSDT', 'BUY', ?, 57000.0, ?, ?, 0.9, 'fixture',
                       '2026-01-01', '2026-01-01')""",
            (
                attempt_key,
                json.dumps([{"price": 60000.0}]),
                json.dumps(tp_prices or [65000.0, 70000.0]),
                status,
            ),
        )
        conn.commit()


def _insert_operational_signal(db_path: str, *, parse_result_id: int, attempt_key: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO operational_signals
               (parse_result_id, attempt_key, trader_id, message_type, is_blocked,
                position_size_usdt, leverage, management_rules_json, created_at)
               VALUES (?, ?, 'trader_3', 'NEW_SIGNAL', 0, 250.0, 3, ?, '2026-01-01')""",
            (parse_result_id, attempt_key, json.dumps({"tp_handling": "ladder"})),
        )
        conn.commit()


def _insert_update_parse_result(
    db_path: str,
    *,
    parse_result_id: int,
    intents: list[str],
    entities: dict[str, object] | None = None,
) -> None:
    payload = json.dumps({"message_type": "UPDATE", "intents": intents, "entities": entities or {}, "target_refs": []})
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO parse_results
               (parse_result_id, raw_message_id, eligibility_status, eligibility_reason,
                resolved_trader_id, trader_resolution_method, message_type, parse_status,
                completeness, is_executable, risky_flag, parse_result_normalized_json,
                created_at, updated_at)
               VALUES (?, ?, 'OK', 'ok', 'trader_3', 'direct', 'UPDATE', 'PARSED',
                       'COMPLETE', 0, 0, ?, '2026-01-02', '2026-01-02')""",
            (parse_result_id, parse_result_id, payload),
        )
        conn.commit()


def _insert_targeted_update(
    db_path: str,
    *,
    parse_result_id: int,
    target_op_signal_id: int,
    target_eligibility: str = "ELIGIBLE",
) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO operational_signals
               (parse_result_id, attempt_key, trader_id, message_type, is_blocked,
                resolved_target_ids, target_eligibility, created_at)
               VALUES (?, NULL, 'trader_3', 'UPDATE', 0, ?, ?, '2026-01-02')""",
            (parse_result_id, json.dumps([target_op_signal_id]), target_eligibility),
        )
        conn.commit()


def test_entry_fill_sets_signal_active_and_creates_trade(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path)
    _insert_signal(db_path, attempt_key="atk_fill")
    _insert_operational_signal(db_path, parse_result_id=1, attempt_key="atk_fill")

    result = order_filled_callback(
        db_path=db_path,
        attempt_key="atk_fill",
        qty=1.5,
        fill_price=60000.0,
        client_order_id="entry-1",
        exchange_order_id="ex-entry-1",
    )

    assert result["ok"] is True

    with sqlite3.connect(db_path) as conn:
        signal_status = conn.execute(
            "SELECT status FROM signals WHERE attempt_key = 'atk_fill'"
        ).fetchone()[0]
        trade = conn.execute(
            "SELECT state, execution_mode FROM trades WHERE attempt_key = 'atk_fill'"
        ).fetchone()
        order_rows = conn.execute(
            "SELECT purpose, status FROM orders WHERE attempt_key = 'atk_fill' ORDER BY purpose, idx"
        ).fetchall()
        position = conn.execute(
            "SELECT size, entry_price, leverage FROM positions WHERE symbol = 'BTCUSDT'"
        ).fetchone()
        event_types = [row[0] for row in conn.execute(
            "SELECT event_type FROM events WHERE attempt_key = 'atk_fill' ORDER BY event_id"
        ).fetchall()]

    assert signal_status == "ACTIVE"
    assert trade == ("OPEN", "FREQTRADE")
    assert ("ENTRY", "FILLED") in order_rows
    assert ("SL", "NEW") in order_rows
    assert order_rows.count(("TP", "NEW")) == 2
    assert position == (1.5, 60000.0, 3.0)
    assert "ENTRY_FILLED" in event_types


def test_entry_fill_exchange_manager_persists_mode_and_defers_protective_rows(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path)
    _insert_signal(db_path, attempt_key="atk_exchange_manager")
    _insert_operational_signal(db_path, parse_result_id=1, attempt_key="atk_exchange_manager")

    result = order_filled_callback(
        db_path=db_path,
        attempt_key="atk_exchange_manager",
        qty=1.5,
        fill_price=60000.0,
        client_order_id="entry-exchange-manager",
        exchange_order_id="ex-entry-exchange-manager",
        protective_orders_mode="exchange_manager",
    )

    assert result["ok"] is True

    with sqlite3.connect(db_path) as conn:
        trade_mode = conn.execute(
            "SELECT protective_orders_mode FROM trades WHERE attempt_key = 'atk_exchange_manager'"
        ).fetchone()[0]
        order_rows = conn.execute(
            "SELECT purpose, status FROM orders WHERE attempt_key = 'atk_exchange_manager' ORDER BY purpose, idx"
        ).fetchall()

    assert trade_mode == "exchange_manager"
    assert order_rows == [("ENTRY", "FILLED")]


def test_close_full_sets_trade_closed_and_position_zero(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path)
    _insert_signal(db_path, attempt_key="atk_close")
    _insert_operational_signal(db_path, parse_result_id=1, attempt_key="atk_close")
    order_filled_callback(
        db_path=db_path,
        attempt_key="atk_close",
        qty=1.0,
        fill_price=60000.0,
        client_order_id="entry-2",
        exchange_order_id="ex-entry-2",
    )

    result = trade_exit_callback(
        db_path=db_path,
        attempt_key="atk_close",
        close_reason="FULL_CLOSE_REQUESTED",
        exit_price=61000.0,
    )

    assert result["ok"] is True

    with sqlite3.connect(db_path) as conn:
        trade = conn.execute(
            "SELECT state, close_reason FROM trades WHERE attempt_key = 'atk_close'"
        ).fetchone()
        position = conn.execute(
            "SELECT size, mark_price FROM positions WHERE symbol = 'BTCUSDT'"
        ).fetchone()
        event_types = [row[0] for row in conn.execute(
            "SELECT event_type FROM events WHERE attempt_key = 'atk_close' ORDER BY event_id"
        ).fetchall()]

    assert trade == ("CLOSED", "FULL_CLOSE_REQUESTED")
    assert position == (0.0, 61000.0)
    assert "POSITION_CLOSED" in event_types


def test_partial_close_keeps_trade_open_and_persists_fraction(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path, parse_result_id=1)
    _insert_signal(db_path, attempt_key="atk_partial")
    _insert_operational_signal(db_path, parse_result_id=1, attempt_key="atk_partial")
    order_filled_callback(
        db_path=db_path,
        attempt_key="atk_partial",
        qty=1.0,
        fill_price=60000.0,
        client_order_id="entry-3",
        exchange_order_id="ex-entry-3",
    )
    _insert_update_parse_result(
        db_path,
        parse_result_id=2,
        intents=["U_CLOSE_PARTIAL"],
        entities={"close_fraction": 0.5},
    )
    _insert_targeted_update(db_path, parse_result_id=2, target_op_signal_id=1)

    result = partial_exit_callback(
        db_path=db_path,
        attempt_key="atk_partial",
        close_fraction=0.5,
        remaining_qty=0.5,
        closed_qty=0.5,
        exit_price=61000.0,
        realized_pnl=125.0,
        client_order_id="exit-1",
        exchange_order_id="ex-exit-1",
    )

    assert result["ok"] is True

    with sqlite3.connect(db_path) as conn:
        trade = conn.execute(
            "SELECT state, meta_json FROM trades WHERE attempt_key = 'atk_partial'"
        ).fetchone()
        position = conn.execute(
            "SELECT size, mark_price, realized_pnl FROM positions WHERE symbol = 'BTCUSDT'"
        ).fetchone()
        exit_order = conn.execute(
            "SELECT purpose, status, qty FROM orders WHERE attempt_key = 'atk_partial' AND purpose = 'EXIT'"
        ).fetchone()
        events = [row[0] for row in conn.execute(
            "SELECT event_type FROM events WHERE attempt_key = 'atk_partial' ORDER BY event_id"
        ).fetchall()]

    trade_meta = json.loads(trade[1])
    assert trade[0] == "OPEN"
    assert trade_meta["close_fraction"] == 0.5
    assert trade_meta["last_partial_exit_update_id"] == 2
    assert position == (0.5, 61000.0, 125.0)
    assert exit_order == ("EXIT", "FILLED", 0.5)
    assert "PARTIAL_CLOSE_FILLED" in events


def test_race_condition_cancel_update_before_fill_rejects_entry_fill(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path, parse_result_id=1)
    _insert_signal(db_path, attempt_key="atk_race")
    _insert_operational_signal(db_path, parse_result_id=1, attempt_key="atk_race")
    _insert_update_parse_result(db_path, parse_result_id=2, intents=["U_CANCEL_PENDING"])
    _insert_targeted_update(db_path, parse_result_id=2, target_op_signal_id=1)

    result = order_filled_callback(
        db_path=db_path,
        attempt_key="atk_race",
        qty=1.0,
        fill_price=60000.0,
        client_order_id="entry-race",
        exchange_order_id="ex-entry-race",
    )

    assert result == {"ok": False, "error": "signal_cancelled_before_fill"}

    with sqlite3.connect(db_path) as conn:
        signal_status = conn.execute(
            "SELECT status FROM signals WHERE attempt_key = 'atk_race'"
        ).fetchone()[0]
        trade_count = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE attempt_key = 'atk_race'"
        ).fetchone()[0]

    assert signal_status == "PENDING"
    assert trade_count == 0
