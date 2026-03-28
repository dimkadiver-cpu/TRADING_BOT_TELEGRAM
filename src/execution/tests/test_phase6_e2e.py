from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from src.core.migrations import apply_migrations
from src.execution.exchange_gateway import ExchangeGateway
from src.execution.exchange_order_manager import ExchangeOrderManager
from src.execution.freqtrade_callback import order_filled_callback


class _AtAccessor:
    def __init__(self, frame: "_MiniDataFrame") -> None:
        self._frame = frame

    def __setitem__(self, key: tuple[int, str], value: object) -> None:
        row_index, column = key
        self._frame._data[column][row_index] = value


class _MiniDataFrame:
    def __init__(self, rows: int = 1) -> None:
        self.index = list(range(rows))
        self._data: dict[str, list[object]] = {"close": [0.0 for _ in self.index]}
        self.at = _AtAccessor(self)

    def __setitem__(self, key: str, value: object) -> None:
        if isinstance(value, list):
            self._data[key] = list(value)
            return
        self._data[key] = [value for _ in self.index]

    def __getitem__(self, key: str) -> list[object]:
        return self._data[key]


def _load_strategy_class():
    strategy_path = Path("freqtrade/user_data/strategies/SignalBridgeStrategy.py").resolve()
    spec = importlib.util.spec_from_file_location("signal_bridge_strategy_phase6_e2e", strategy_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.SignalBridgeStrategy


SignalBridgeStrategy = _load_strategy_class()


class _FakeExchangeBackend:
    def __init__(self) -> None:
        self.open_orders_by_symbol: dict[str, list[dict[str, object]]] = {}
        self.positions_by_symbol: dict[str, dict[str, object]] = {}

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
            "status": "FILLED" if order_type == "MARKET" else "OPEN",
        }
        if order_type != "MARKET":
            self.open_orders_by_symbol.setdefault(symbol, []).append(payload)
        return payload

    def cancel_order(self, *, exchange_order_id: str, symbol: str | None = None) -> bool:
        for item_symbol, rows in list(self.open_orders_by_symbol.items()):
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
    db_path = str(tmp_path / "phase6_e2e.sqlite3")
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


def _insert_signal(db_path: str, *, attempt_key: str) -> None:
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
                json.dumps([{"price": 65000.0}, {"price": 66000.0}, {"price": 67000.0}]),
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


def test_phase6_end_to_end_entry_update_tp_restart_reconciliation(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    attempt_key = "atk_phase6_e2e"
    _insert_parse_result(db_path)
    _insert_signal(db_path, attempt_key=attempt_key)
    _insert_operational_signal(db_path, parse_result_id=1, attempt_key=attempt_key)

    backend = _FakeExchangeBackend()
    manager = ExchangeOrderManager(db_path=db_path, gateway=ExchangeGateway(backend))
    strategy = SignalBridgeStrategy(config={"execution": {"protective_orders_mode": "exchange_manager"}})
    strategy.bot_db_path = db_path
    strategy.exchange_order_manager = manager

    callback_result = order_filled_callback(
        db_path=db_path,
        attempt_key=attempt_key,
        qty=2.0,
        fill_price=60000.0,
        client_order_id="entry-phase6-e2e",
        exchange_order_id="ex-entry-phase6-e2e",
        protective_orders_mode="exchange_manager",
        order_manager=manager,
    )
    assert callback_result["ok"] is True
    backend.positions_by_symbol["BTCUSDT"] = {
        "symbol": "BTCUSDT",
        "side": "BUY",
        "size": 2.0,
        "entry_price": 60000.0,
    }

    move_stop_result = manager.apply_update(
        attempt_key=attempt_key,
        update_context={"intent": "U_MOVE_STOP", "new_stop_level": 58500.0},
    )
    assert move_stop_result.ok is True

    backend.positions_by_symbol["BTCUSDT"]["size"] = 1.4
    tp_fill_result = manager.sync_after_tp_fill(
        attempt_key=attempt_key,
        tp_idx=0,
        closed_qty=0.6,
        fill_price=65000.0,
        exchange_order_id="ex-atk_phase6_e2e:TP:0",
    )
    assert tp_fill_result.ok is True

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE orders
            SET exchange_order_id = NULL,
                status = 'NEW',
                updated_at = '2026-01-02'
            WHERE attempt_key = ?
              AND purpose = 'SL'
              AND status = 'OPEN'
            """,
            (attempt_key,),
        )
        conn.commit()

    restarted_manager = ExchangeOrderManager(db_path=db_path, gateway=ExchangeGateway(backend))
    restarted_strategy = SignalBridgeStrategy(config={"execution": {"protective_orders_mode": "exchange_manager"}})
    restarted_strategy.bot_db_path = db_path
    restarted_strategy.exchange_order_manager = restarted_manager
    restarted_strategy.populate_indicators(_MiniDataFrame(rows=1), {"pair": "BTC/USDT:USDT"})

    open_orders = backend.fetch_open_orders(symbol="BTCUSDT")
    open_client_order_ids = [str(item["client_order_id"]) for item in open_orders]

    with sqlite3.connect(db_path) as conn:
        protective_rows = conn.execute(
            """
            SELECT purpose, idx, exchange_order_id, status, qty, price, trigger_price
            FROM orders
            WHERE attempt_key = ?
              AND purpose IN ('SL', 'TP')
            ORDER BY order_pk
            """,
            (attempt_key,),
        ).fetchall()
        event_types = [
            row[0]
            for row in conn.execute(
                "SELECT event_type FROM events WHERE attempt_key = ? ORDER BY event_id",
                (attempt_key,),
            ).fetchall()
        ]
        warning_codes = [
            row[0]
            for row in conn.execute(
                "SELECT code FROM warnings WHERE attempt_key = ? ORDER BY warning_id",
                (attempt_key,),
            ).fetchall()
        ]

    assert all(row[2] for row in protective_rows)
    assert protective_rows == [
        ("SL", 0, "ex-atk_phase6_e2e:SL:0", "CANCELLED", 2.0, None, 57000.0),
        ("TP", 0, "ex-atk_phase6_e2e:TP:0", "CANCELLED", 0.6, 65000.0, None),
        ("TP", 1, "ex-atk_phase6_e2e:TP:1", "CANCELLED", 0.6, 66000.0, None),
        ("TP", 2, "ex-atk_phase6_e2e:TP:2", "CANCELLED", 0.8, 67000.0, None),
        ("SL", 0, "ex-atk_phase6_e2e:SL:0:R1", "CANCELLED", 2.0, None, 58500.0),
        ("SL", 0, "ex-atk_phase6_e2e:SL:0:R2", "CANCELLED", 1.4, None, 58500.0),
        ("TP", 1, "ex-atk_phase6_e2e:TP:1:R1", "CANCELLED", 0.6, 66000.0, None),
        ("TP", 2, "ex-atk_phase6_e2e:TP:2:R1", "CANCELLED", 0.8, 67000.0, None),
        ("SL", 0, "ex-atk_phase6_e2e:SL:0:R3", "OPEN", 1.4, None, 58500.0),
        ("TP", 1, "ex-atk_phase6_e2e:TP:1:R2", "OPEN", 0.6, 66000.0, None),
        ("TP", 2, "ex-atk_phase6_e2e:TP:2:R2", "OPEN", 0.8, 67000.0, None),
    ]
    assert "ENTRY_FILLED" in event_types
    assert "STOP_REPLACED" in event_types
    assert "TP_FILL_SYNCED" in event_types
    assert "RECONCILIATION_COMPLETED" in event_types
    assert warning_codes == []
    assert len(open_client_order_ids) == len(set(open_client_order_ids)) == 3
    assert set(open_client_order_ids) == {
        "atk_phase6_e2e:SL:0:R3",
        "atk_phase6_e2e:TP:1:R2",
        "atk_phase6_e2e:TP:2:R2",
    }

    stoploss_value = restarted_strategy.custom_stoploss(
        "BTC/USDT:USDT",
        SimpleNamespace(enter_tag=attempt_key),
        None,
        65000.0,
        0.0,
    )
    reduction = restarted_strategy.adjust_trade_position(
        trade=SimpleNamespace(
            enter_tag=attempt_key,
            pair="BTC/USDT:USDT",
            stake_amount=250.0,
            has_open_orders=False,
        ),
        current_time=None,
        current_rate=70000.0,
        current_profit=0.15,
        min_stake=None,
        max_stake=1000.0,
    )
    exit_tag = restarted_strategy.custom_exit(
        pair="BTC/USDT:USDT",
        trade=SimpleNamespace(
            enter_tag=attempt_key,
            pair="BTC/USDT:USDT",
            has_open_orders=False,
        ),
        current_time=None,
        current_rate=70000.0,
        current_profit=0.15,
    )

    assert stoploss_value == restarted_strategy.stoploss
    assert reduction is None
    assert exit_tag is None
