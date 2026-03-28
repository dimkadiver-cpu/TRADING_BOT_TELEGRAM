from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from src.core.migrations import apply_migrations
from src.execution.exchange_gateway import ExchangeGateway
from src.execution.exchange_order_manager import (
    ExchangeOrderManager,
    build_entry_protective_order_plan,
)
from src.execution.freqtrade_normalizer import load_context_by_attempt_key
from src.execution.freqtrade_callback import order_filled_callback


class _FakeExchangeBackend:
    def __init__(self, *, fail_client_order_ids: set[str] | None = None) -> None:
        self.fail_client_order_ids = fail_client_order_ids or set()
        self.created_orders: list[dict[str, object]] = []
        self.cancelled_order_ids: list[str] = []

    def create_order(
        self,
        *,
        symbol: str,
        side: str,
        order_type: str,
        qty: float,
        price: float | None = None,
        trigger_price: float | None = None,
        reduce_only: bool = False,
        client_order_id: str | None = None,
    ) -> dict[str, object]:
        if client_order_id in self.fail_client_order_ids:
            raise RuntimeError(f"backend rejected {client_order_id}")
        payload = {
            "id": f"ex-{client_order_id}",
            "client_order_id": client_order_id,
            "symbol": symbol,
            "side": side,
            "order_type": order_type,
            "qty": qty,
            "price": price,
            "trigger_price": trigger_price,
            "reduce_only": reduce_only,
            "status": "FILLED" if order_type == "MARKET" else "OPEN",
        }
        self.created_orders.append(payload)
        return payload

    def cancel_order(self, *, exchange_order_id: str, symbol: str | None = None) -> bool:
        self.cancelled_order_ids.append(exchange_order_id)
        return True

    def fetch_open_orders(self, *, symbol: str) -> list[dict[str, object]]:
        return [order for order in self.created_orders if order["symbol"] == symbol]

    def fetch_position(self, *, symbol: str) -> dict[str, object] | None:
        return None


def _make_db(tmp_path: Path) -> str:
    db_path = str(tmp_path / "exchange_order_manager.sqlite3")
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
    tp_json: str | None = None,
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
                tp_json or json.dumps([{"price": 65000.0}, {"price": 66000.0}, {"price": 67000.0}]),
                status,
            ),
        )
        conn.commit()


def _insert_operational_signal(
    db_path: str,
    *,
    parse_result_id: int,
    attempt_key: str,
    management_rules_json: str | None = None,
) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO operational_signals
               (parse_result_id, attempt_key, trader_id, message_type, is_blocked,
                position_size_usdt, leverage, management_rules_json, created_at)
               VALUES (?, ?, 'trader_3', 'NEW_SIGNAL', 0, 250.0, 3, ?, '2026-01-01')""",
            (
                parse_result_id,
                attempt_key,
                management_rules_json
                or json.dumps(
                    {
                        "tp_handling": {
                            "tp_handling_mode": "follow_all_signal_tps",
                            "tp_close_distribution": {"3": [30, 30, 40]},
                        }
                    }
                ),
            ),
        )
        conn.commit()


def _make_manager(
    db_path: str,
    *,
    fail_client_order_ids: set[str] | None = None,
) -> ExchangeOrderManager:
    gateway = ExchangeGateway(_FakeExchangeBackend(fail_client_order_ids=fail_client_order_ids))
    return ExchangeOrderManager(db_path=db_path, gateway=gateway)


def test_build_entry_protective_order_plan_uses_tp_close_distribution(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path)
    _insert_signal(db_path, attempt_key="atk_plan")
    _insert_operational_signal(db_path, parse_result_id=1, attempt_key="atk_plan")
    order_filled_callback(
        db_path=db_path,
        attempt_key="atk_plan",
        qty=2.0,
        fill_price=60000.0,
        client_order_id="entry-plan",
        exchange_order_id="ex-entry-plan",
        protective_orders_mode="exchange_manager",
    )

    context = load_context_by_attempt_key("atk_plan", db_path)
    assert context is not None

    plan = build_entry_protective_order_plan(
        context=context,
        fill_qty=2.0,
        fill_price=60000.0,
    )

    assert plan.stop_order is not None
    assert plan.stop_order.trigger_price == 57000.0
    assert [item.qty for item in plan.take_profit_orders] == [0.6, 0.6, 0.8]
    assert [item.price for item in plan.take_profit_orders] == [65000.0, 66000.0, 67000.0]


def test_sync_after_entry_fill_creates_sl_and_take_profits_with_exchange_order_ids(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path)
    _insert_signal(db_path, attempt_key="atk_sync")
    _insert_operational_signal(db_path, parse_result_id=1, attempt_key="atk_sync")
    manager = _make_manager(db_path)

    callback_result = order_filled_callback(
        db_path=db_path,
        attempt_key="atk_sync",
        qty=2.0,
        fill_price=60000.0,
        client_order_id="entry-sync",
        exchange_order_id="ex-entry-sync",
        protective_orders_mode="exchange_manager",
        order_manager=manager,
    )

    assert callback_result["ok"] is True
    assert callback_result["manager_result"]["ok"] is True

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT purpose, idx, qty, price, trigger_price, exchange_order_id, status
            FROM orders
            WHERE attempt_key = 'atk_sync'
            ORDER BY purpose, idx
            """
        ).fetchall()

    assert rows == [
        ("ENTRY", 0, 2.0, 60000.0, None, "ex-entry-sync", "FILLED"),
        ("SL", 0, 2.0, None, 57000.0, "ex-atk_sync:SL:0", "OPEN"),
        ("TP", 0, 0.6, 65000.0, None, "ex-atk_sync:TP:0", "OPEN"),
        ("TP", 1, 0.6, 66000.0, None, "ex-atk_sync:TP:1", "OPEN"),
        ("TP", 2, 0.8, 67000.0, None, "ex-atk_sync:TP:2", "OPEN"),
    ]


def test_sync_after_entry_fill_persists_exchange_metadata(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path)
    _insert_signal(db_path, attempt_key="atk_metadata")
    _insert_operational_signal(db_path, parse_result_id=1, attempt_key="atk_metadata")
    manager = _make_manager(db_path)

    order_filled_callback(
        db_path=db_path,
        attempt_key="atk_metadata",
        qty=1.0,
        fill_price=60000.0,
        client_order_id="entry-metadata",
        exchange_order_id="ex-entry-metadata",
        protective_orders_mode="exchange_manager",
        order_manager=manager,
    )

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT purpose, venue_status_raw, last_exchange_sync_at
            FROM orders
            WHERE attempt_key = 'atk_metadata'
              AND purpose IN ('SL', 'TP')
            ORDER BY purpose, idx
            """
        ).fetchall()

    assert len(rows) == 4
    assert all(row[1] == "OPEN" for row in rows)
    assert all(isinstance(row[2], str) and row[2] for row in rows)


def test_sync_after_entry_fill_records_partial_failure_warning(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path)
    _insert_signal(db_path, attempt_key="atk_partial_failure")
    _insert_operational_signal(db_path, parse_result_id=1, attempt_key="atk_partial_failure")
    manager = _make_manager(db_path, fail_client_order_ids={"atk_partial_failure:TP:1"})

    callback_result = order_filled_callback(
        db_path=db_path,
        attempt_key="atk_partial_failure",
        qty=2.0,
        fill_price=60000.0,
        client_order_id="entry-partial-failure",
        exchange_order_id="ex-entry-partial-failure",
        protective_orders_mode="exchange_manager",
        order_manager=manager,
    )

    assert callback_result["ok"] is True
    assert callback_result["manager_result"]["ok"] is False
    assert callback_result["manager_result"]["partial_failure"] is True

    with sqlite3.connect(db_path) as conn:
        protective_orders = conn.execute(
            """
            SELECT purpose, idx, exchange_order_id
            FROM orders
            WHERE attempt_key = 'atk_partial_failure'
              AND purpose IN ('SL', 'TP')
            ORDER BY purpose, idx
            """
        ).fetchall()
        warning_codes = [
            row[0]
            for row in conn.execute(
                "SELECT code FROM warnings WHERE attempt_key = 'atk_partial_failure' ORDER BY warning_id"
            ).fetchall()
        ]
        event_types = [
            row[0]
            for row in conn.execute(
                "SELECT event_type FROM events WHERE attempt_key = 'atk_partial_failure' ORDER BY event_id"
            ).fetchall()
        ]

    assert protective_orders == [
        ("SL", 0, "ex-atk_partial_failure:SL:0"),
        ("TP", 0, "ex-atk_partial_failure:TP:0"),
        ("TP", 2, "ex-atk_partial_failure:TP:2"),
    ]
    assert "exchange_manager_order_create_failed" in warning_codes
    assert "exchange_manager_partial_failure" in warning_codes
    assert "PROTECTIVE_ORDER_CREATE_FAILED" in event_types
    assert "PROTECTIVE_ORDERS_SYNCED" in event_types


def test_apply_update_replace_stop_cancels_old_sl_and_creates_new_one(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path)
    _insert_signal(db_path, attempt_key="atk_move_stop")
    _insert_operational_signal(db_path, parse_result_id=1, attempt_key="atk_move_stop")
    manager = _make_manager(db_path)

    order_filled_callback(
        db_path=db_path,
        attempt_key="atk_move_stop",
        qty=2.0,
        fill_price=60000.0,
        client_order_id="entry-move-stop",
        exchange_order_id="ex-entry-move-stop",
        protective_orders_mode="exchange_manager",
        order_manager=manager,
    )

    result = manager.apply_update(
        attempt_key="atk_move_stop",
        update_context={"intent": "U_MOVE_STOP", "new_stop_level": 58500.0},
    )

    assert result.ok is True
    with sqlite3.connect(db_path) as conn:
        sl_rows = conn.execute(
            """
            SELECT client_order_id, exchange_order_id, status, trigger_price
            FROM orders
            WHERE attempt_key = 'atk_move_stop'
              AND purpose = 'SL'
            ORDER BY order_pk
            """
        ).fetchall()

    assert sl_rows == [
        ("atk_move_stop:SL:0", "ex-atk_move_stop:SL:0", "CANCELLED", 57000.0),
        ("atk_move_stop:SL:0:R1", "ex-atk_move_stop:SL:0:R1", "OPEN", 58500.0),
    ]


def test_apply_update_close_full_cancels_protectives_and_closes_trade(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path)
    _insert_signal(db_path, attempt_key="atk_close_full")
    _insert_operational_signal(db_path, parse_result_id=1, attempt_key="atk_close_full")
    manager = _make_manager(db_path)

    order_filled_callback(
        db_path=db_path,
        attempt_key="atk_close_full",
        qty=2.0,
        fill_price=60000.0,
        client_order_id="entry-close-full",
        exchange_order_id="ex-entry-close-full",
        protective_orders_mode="exchange_manager",
        order_manager=manager,
    )

    result = manager.apply_update(
        attempt_key="atk_close_full",
        update_context={"intent": "U_CLOSE_FULL"},
    )

    assert result.ok is True
    with sqlite3.connect(db_path) as conn:
        active_orders = conn.execute(
            """
            SELECT purpose, status
            FROM orders
            WHERE attempt_key = 'atk_close_full'
            ORDER BY order_pk
            """
        ).fetchall()
        trade_row = conn.execute(
            "SELECT state, close_reason FROM trades WHERE attempt_key = 'atk_close_full'"
        ).fetchone()
        position_row = conn.execute(
            "SELECT size FROM positions WHERE symbol = 'BTCUSDT'"
        ).fetchone()

    assert ("EXIT", "FILLED") in active_orders
    assert all(status != "OPEN" for purpose, status in active_orders if purpose in {"SL", "TP"})
    assert trade_row == ("CLOSED", "FULL_CLOSE_REQUESTED")
    assert position_row == (0.0,)


def test_apply_update_partial_close_rebuilds_ladder_and_realigns_stop(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path)
    _insert_signal(db_path, attempt_key="atk_partial_close")
    _insert_operational_signal(db_path, parse_result_id=1, attempt_key="atk_partial_close")
    manager = _make_manager(db_path)

    order_filled_callback(
        db_path=db_path,
        attempt_key="atk_partial_close",
        qty=2.0,
        fill_price=60000.0,
        client_order_id="entry-partial-close",
        exchange_order_id="ex-entry-partial-close",
        protective_orders_mode="exchange_manager",
        order_manager=manager,
    )

    result = manager.apply_update(
        attempt_key="atk_partial_close",
        update_context={"intent": "U_CLOSE_PARTIAL", "close_fraction": 0.5},
    )

    assert result.ok is True
    with sqlite3.connect(db_path) as conn:
        position_row = conn.execute(
            "SELECT size FROM positions WHERE symbol = 'BTCUSDT'"
        ).fetchone()
        open_protectives = conn.execute(
            """
            SELECT purpose, idx, qty, trigger_price, price
            FROM orders
            WHERE attempt_key = 'atk_partial_close'
              AND status = 'OPEN'
              AND purpose IN ('SL', 'TP')
            ORDER BY purpose, idx, order_pk
            """
        ).fetchall()

    assert position_row == (1.0,)
    assert open_protectives == [
        ("SL", 0, 1.0, 57000.0, None),
        ("TP", 0, 0.3, None, 65000.0),
        ("TP", 1, 0.3, None, 66000.0),
        ("TP", 2, 0.4, None, 67000.0),
    ]


def test_sync_after_tp_fill_marks_correct_tp_and_rebuilds_residual_orders(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path)
    _insert_signal(db_path, attempt_key="atk_tp_fill")
    _insert_operational_signal(db_path, parse_result_id=1, attempt_key="atk_tp_fill")
    manager = _make_manager(db_path)

    order_filled_callback(
        db_path=db_path,
        attempt_key="atk_tp_fill",
        qty=2.0,
        fill_price=60000.0,
        client_order_id="entry-tp-fill",
        exchange_order_id="ex-entry-tp-fill",
        protective_orders_mode="exchange_manager",
        order_manager=manager,
    )

    result = manager.sync_after_tp_fill(
        attempt_key="atk_tp_fill",
        tp_idx=0,
        closed_qty=0.6,
        fill_price=65000.0,
        exchange_order_id="ex-atk_tp_fill:TP:0",
    )

    assert result.ok is True
    with sqlite3.connect(db_path) as conn:
        tp_rows = conn.execute(
            """
            SELECT idx, status, price
            FROM orders
            WHERE attempt_key = 'atk_tp_fill'
              AND purpose = 'TP'
            ORDER BY order_pk
            """
        ).fetchall()
        position_row = conn.execute(
            "SELECT size FROM positions WHERE symbol = 'BTCUSDT'"
        ).fetchone()
        meta_json = conn.execute(
            "SELECT meta_json FROM trades WHERE attempt_key = 'atk_tp_fill'"
        ).fetchone()[0]
        open_protectives = conn.execute(
            """
            SELECT purpose, idx, qty, trigger_price, price
            FROM orders
            WHERE attempt_key = 'atk_tp_fill'
              AND status = 'OPEN'
              AND purpose IN ('SL', 'TP')
            ORDER BY purpose, idx, order_pk
            """
        ).fetchall()

    assert tp_rows[0] == (0, "FILLED", 65000.0)
    assert position_row == (1.4,)
    assert json.loads(meta_json)["tp_filled_indices"] == [0]
    assert open_protectives == [
        ("SL", 0, 1.4, 57000.0, None),
        ("TP", 1, 0.6, None, 66000.0),
        ("TP", 2, 0.8, None, 67000.0),
    ]
