from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from src.core.migrations import apply_migrations
from src.execution.exchange_gateway import ExchangeGateway
from src.execution.exchange_order_manager import ExchangeOrderManager
from src.execution.freqtrade_callback import order_filled_callback
from src.execution.order_reconciliation import bootstrap_sync_open_trades


class _FakeReconciliationBackend:
    def __init__(self) -> None:
        self.open_orders_by_symbol: dict[str, list[dict[str, object]]] = {}
        self.positions_by_symbol: dict[str, dict[str, object]] = {}
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
            "status": "OPEN",
        }
        self.open_orders_by_symbol.setdefault(symbol, []).append(payload)
        return payload

    def cancel_order(self, *, exchange_order_id: str, symbol: str | None = None) -> bool:
        self.cancelled_order_ids.append(exchange_order_id)
        for item_symbol, rows in self.open_orders_by_symbol.items():
            if symbol is not None and item_symbol != symbol:
                continue
            self.open_orders_by_symbol[item_symbol] = [
                row for row in rows if str(row.get("id")) != exchange_order_id
            ]
        return True

    def fetch_open_orders(self, *, symbol: str) -> list[dict[str, object]]:
        return [dict(item) for item in self.open_orders_by_symbol.get(symbol, [])]

    def fetch_position(self, *, symbol: str) -> dict[str, object] | None:
        payload = self.positions_by_symbol.get(symbol)
        return dict(payload) if payload is not None else None


def _make_db(tmp_path: Path) -> str:
    db_path = str(tmp_path / "order_reconciliation.sqlite3")
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
    tp_json: str | None = None,
) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO signals
               (attempt_key, env, channel_id, root_telegram_id, trader_id, trader_prefix,
                symbol, side, entry_json, sl, tp_json, status, confidence, raw_text,
                created_at, updated_at)
               VALUES (?, 'T', '-100999', '1', 'trader_3', 'TRAD',
                       'BTCUSDT', 'BUY', ?, 57000.0, ?, 'PENDING', 0.9, 'fixture',
                       '2026-01-01', '2026-01-01')""",
            (
                attempt_key,
                json.dumps([{"price": 60000.0}]),
                tp_json or json.dumps([{"price": 65000.0}, {"price": 66000.0}, {"price": 67000.0}]),
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
            (
                parse_result_id,
                attempt_key,
                json.dumps(
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


def _exchange_open_orders(attempt_key: str, *, qtys: tuple[float, float, float, float] = (2.0, 0.6, 0.6, 0.8)) -> list[dict[str, object]]:
    sl_qty, tp1_qty, tp2_qty, tp3_qty = qtys
    return [
        {
            "id": f"ex-{attempt_key}:SL:0",
            "client_order_id": f"{attempt_key}:SL:0",
            "symbol": "BTCUSDT",
            "side": "SELL",
            "order_type": "STOP",
            "qty": sl_qty,
            "trigger_price": 57000.0,
            "reduce_only": True,
            "status": "OPEN",
        },
        {
            "id": f"ex-{attempt_key}:TP:0",
            "client_order_id": f"{attempt_key}:TP:0",
            "symbol": "BTCUSDT",
            "side": "SELL",
            "order_type": "LIMIT",
            "qty": tp1_qty,
            "price": 65000.0,
            "reduce_only": True,
            "status": "OPEN",
        },
        {
            "id": f"ex-{attempt_key}:TP:1",
            "client_order_id": f"{attempt_key}:TP:1",
            "symbol": "BTCUSDT",
            "side": "SELL",
            "order_type": "LIMIT",
            "qty": tp2_qty,
            "price": 66000.0,
            "reduce_only": True,
            "status": "OPEN",
        },
        {
            "id": f"ex-{attempt_key}:TP:2",
            "client_order_id": f"{attempt_key}:TP:2",
            "symbol": "BTCUSDT",
            "side": "SELL",
            "order_type": "LIMIT",
            "qty": tp3_qty,
            "price": 67000.0,
            "reduce_only": True,
            "status": "OPEN",
        },
    ]


def _make_manager(db_path: str, backend: _FakeReconciliationBackend) -> ExchangeOrderManager:
    return ExchangeOrderManager(db_path=db_path, gateway=ExchangeGateway(backend))


def _seed_open_trade(db_path: str, *, attempt_key: str, manager: ExchangeOrderManager | None = None) -> None:
    _insert_parse_result(db_path)
    _insert_signal(db_path, attempt_key=attempt_key)
    _insert_operational_signal(db_path, parse_result_id=1, attempt_key=attempt_key)
    order_filled_callback(
        db_path=db_path,
        attempt_key=attempt_key,
        qty=2.0,
        fill_price=60000.0,
        client_order_id=f"entry-{attempt_key}",
        exchange_order_id=f"ex-entry-{attempt_key}",
        protective_orders_mode="exchange_manager",
        order_manager=manager,
    )


def test_bootstrap_restart_updates_existing_and_imports_missing_orders(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    backend = _FakeReconciliationBackend()
    gateway = ExchangeGateway(backend)
    _seed_open_trade(db_path, attempt_key="atk_restart")
    with sqlite3.connect(db_path) as conn:
        now = "2026-01-01"
        conn.execute(
            """
            INSERT INTO orders(
              env, attempt_key, symbol, side, order_type, purpose, idx, qty, price, trigger_price,
              reduce_only, client_order_id, exchange_order_id, status, created_at, updated_at
            ) VALUES ('T', 'atk_restart', 'BTCUSDT', 'SELL', 'STOP', 'SL', 0, 2.0, NULL, 57000.0,
                      1, 'atk_restart:SL:0', NULL, 'NEW', ?, ?)
            """,
            (now, now),
        )
        conn.commit()
    backend.open_orders_by_symbol["BTCUSDT"] = _exchange_open_orders("atk_restart")
    backend.positions_by_symbol["BTCUSDT"] = {"symbol": "BTCUSDT", "side": "BUY", "size": 2.0, "entry_price": 60000.0}

    result = bootstrap_sync_open_trades(db_path=db_path, gateway=gateway)

    assert result.processed_attempt_keys == ["atk_restart"]
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT purpose, idx, exchange_order_id, status
            FROM orders
            WHERE attempt_key = 'atk_restart'
              AND purpose IN ('SL', 'TP')
            ORDER BY purpose, idx
            """
        ).fetchall()

    assert rows == [
        ("SL", 0, "ex-atk_restart:SL:0", "OPEN"),
        ("TP", 0, "ex-atk_restart:TP:0", "OPEN"),
        ("TP", 1, "ex-atk_restart:TP:1", "OPEN"),
        ("TP", 2, "ex-atk_restart:TP:2", "OPEN"),
    ]


def test_bootstrap_imports_exchange_orders_when_db_is_incomplete(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    backend = _FakeReconciliationBackend()
    gateway = ExchangeGateway(backend)
    _seed_open_trade(db_path, attempt_key="atk_db_incomplete")
    backend.open_orders_by_symbol["BTCUSDT"] = _exchange_open_orders("atk_db_incomplete")
    backend.positions_by_symbol["BTCUSDT"] = {"symbol": "BTCUSDT", "side": "BUY", "size": 2.0, "entry_price": 60000.0}

    bootstrap_sync_open_trades(db_path=db_path, gateway=gateway)

    with sqlite3.connect(db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE attempt_key = 'atk_db_incomplete' AND purpose IN ('SL', 'TP')"
        ).fetchone()[0]

    assert count == 4


def test_bootstrap_recreates_missing_exchange_protectives_from_db_state(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    backend = _FakeReconciliationBackend()
    manager = _make_manager(db_path, backend)
    _seed_open_trade(db_path, attempt_key="atk_exchange_missing", manager=manager)
    backend.positions_by_symbol["BTCUSDT"] = {"symbol": "BTCUSDT", "side": "BUY", "size": 2.0, "entry_price": 60000.0}
    backend.open_orders_by_symbol["BTCUSDT"] = []

    result = bootstrap_sync_open_trades(
        db_path=db_path,
        gateway=manager.gateway,
        order_manager=manager,
    )

    assert result.trade_results[0].recreated_orders == 4
    with sqlite3.connect(db_path) as conn:
        open_rows = conn.execute(
            """
            SELECT purpose, idx, status
            FROM orders
            WHERE attempt_key = 'atk_exchange_missing'
              AND purpose IN ('SL', 'TP')
              AND status = 'OPEN'
            ORDER BY purpose, idx, order_pk
            """
        ).fetchall()
        cancelled_rows = conn.execute(
            """
            SELECT COUNT(*)
            FROM orders
            WHERE attempt_key = 'atk_exchange_missing'
              AND purpose IN ('SL', 'TP')
              AND status = 'CANCELLED'
            """
        ).fetchone()[0]

    assert len(open_rows) == 4
    assert cancelled_rows == 4


def test_bootstrap_rebuilds_when_exchange_quantities_are_incompatible(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    backend = _FakeReconciliationBackend()
    manager = _make_manager(db_path, backend)
    _seed_open_trade(db_path, attempt_key="atk_qty_mismatch", manager=manager)
    backend.positions_by_symbol["BTCUSDT"] = {"symbol": "BTCUSDT", "side": "BUY", "size": 2.0, "entry_price": 60000.0}
    backend.open_orders_by_symbol["BTCUSDT"] = _exchange_open_orders("atk_qty_mismatch", qtys=(5.0, 2.0, 2.0, 2.0))

    result = bootstrap_sync_open_trades(
        db_path=db_path,
        gateway=manager.gateway,
        order_manager=manager,
    )

    assert "recreated_incompatible_protectives" in result.trade_results[0].actions
    with sqlite3.connect(db_path) as conn:
        open_rows = conn.execute(
            """
            SELECT purpose, idx, qty
            FROM orders
            WHERE attempt_key = 'atk_qty_mismatch'
              AND purpose IN ('SL', 'TP')
              AND status = 'OPEN'
            ORDER BY purpose, idx, order_pk
            """
        ).fetchall()

    assert open_rows == [
        ("SL", 0, 2.0),
        ("TP", 0, 0.6),
        ("TP", 1, 0.6),
        ("TP", 2, 0.8),
    ]


def test_bootstrap_leaves_ambiguous_mismatch_as_warning(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    backend = _FakeReconciliationBackend()
    gateway = ExchangeGateway(backend)
    _seed_open_trade(db_path, attempt_key="atk_ambiguous")
    backend.positions_by_symbol["BTCUSDT"] = {"symbol": "BTCUSDT", "side": "BUY", "size": 2.0, "entry_price": 60000.0}
    backend.open_orders_by_symbol["BTCUSDT"] = [
        {
            "id": "ex-foreign-order",
            "client_order_id": "foreign-order",
            "symbol": "BTCUSDT",
            "side": "SELL",
            "order_type": "LIMIT",
            "qty": 1.0,
            "price": 65555.0,
            "reduce_only": True,
            "status": "OPEN",
        }
    ]

    bootstrap_sync_open_trades(db_path=db_path, gateway=gateway)

    with sqlite3.connect(db_path) as conn:
        warning_codes = [
            row[0]
            for row in conn.execute(
                "SELECT code FROM warnings WHERE attempt_key = 'atk_ambiguous' ORDER BY warning_id"
            ).fetchall()
        ]
        protective_count = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE attempt_key = 'atk_ambiguous' AND purpose IN ('SL', 'TP')"
        ).fetchone()[0]

    assert "exchange_reconciliation_ambiguous" in warning_codes
    assert protective_count == 0
