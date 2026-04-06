from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.core.migrations import apply_migrations
from src.execution.freqtrade_normalizer import (
    MACHINE_EVENT_RULES_NOT_SUPPORTED,
    PRICE_CORRECTIONS_NOT_SUPPORTED,
    EntryPricePolicy,
    FreqtradeEntryLeg,
    canonical_side_to_freqtrade_side,
    canonical_symbol_to_freqtrade_pair,
    check_entry_rate,
    is_machine_event_mode,
    load_context_by_attempt_key,
    load_pending_contexts_for_pair,
    persist_entry_price_rejected_event,
    persist_entry_rejected_event,
    resolve_allowed_update_intents,
    resolve_entry_price_policy,
)
from src.execution.freqtrade_callback import entry_order_open_callback, order_filled_callback
from src.execution.update_applier import apply_update_plan
from src.execution.update_planner import build_update_plan


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
    spec = importlib.util.spec_from_file_location("signal_bridge_strategy", strategy_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.SignalBridgeStrategy


SignalBridgeStrategy = _load_strategy_class()


def _make_db(tmp_path: Path) -> str:
    db_path = str(tmp_path / "freqtrade_bridge.sqlite3")
    apply_migrations(db_path=db_path, migrations_dir=str(Path("db/migrations").resolve()))
    return db_path


def _insert_parse_result(db_path: str, *, parse_result_id: int = 1, trader_id: str = "trader_3") -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO parse_results
               (parse_result_id, raw_message_id, eligibility_status, eligibility_reason,
                resolved_trader_id, trader_resolution_method, message_type, parse_status,
                completeness, is_executable, risky_flag, created_at, updated_at)
               VALUES (?, ?, 'OK', 'ok', ?, 'direct', 'NEW_SIGNAL', 'PARSED',
                       'COMPLETE', 1, 0, '2026-01-01', '2026-01-01')""",
            (parse_result_id, parse_result_id, trader_id),
        )
        conn.commit()


def _insert_signal(
    db_path: str,
    *,
    attempt_key: str,
    symbol: str = "BTCUSDT",
    side: str = "BUY",
    status: str = "PENDING",
    sl: float = 57000.0,
    tp_json: str = "[]",
    entry_json: str | None = None,
) -> None:
    if entry_json is None:
        entry_json = json.dumps([{"price": 60000.0}])
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO signals
               (attempt_key, env, channel_id, root_telegram_id, trader_id, trader_prefix,
                symbol, side, entry_json, sl, tp_json, status, confidence, raw_text,
                created_at, updated_at)
               VALUES (?, 'T', '-100999', '1', 'trader_3', 'TRAD',
                       ?, ?, ?, ?, ?, ?, 0.9, 'fixture',
                       '2026-01-01', '2026-01-01')""",
            (attempt_key, symbol, side, entry_json, sl, tp_json, status),
        )
        conn.commit()


def _insert_operational_signal(
    db_path: str,
    *,
    parse_result_id: int,
    attempt_key: str,
    position_size_usdt: float = 250.0,
    leverage: int = 3,
    is_blocked: int = 0,
    management_rules_json: str | None = None,
    entry_split_json: str | None = None,
) -> int:
    """Insert an operational_signal row and return the inserted op_signal_id."""
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO operational_signals
               (parse_result_id, attempt_key, trader_id, message_type, is_blocked,
                block_reason, position_size_usdt, leverage, management_rules_json,
                entry_split_json, created_at)
               VALUES (?, ?, 'trader_3', 'NEW_SIGNAL', ?, ?, ?, ?, ?, ?, '2026-01-01')""",
            (
                parse_result_id,
                attempt_key,
                is_blocked,
                "blocked_by_rule" if is_blocked else None,
                position_size_usdt,
                leverage,
                management_rules_json or json.dumps({"tp_handling": "ladder"}),
                entry_split_json,
            ),
        )
        conn.commit()
        return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


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


def test_canonical_symbol_to_freqtrade_pair_maps_usdt_futures() -> None:
    assert canonical_symbol_to_freqtrade_pair("BTCUSDT") == "BTC/USDT:USDT"


def test_canonical_side_to_freqtrade_side_maps_buy_sell() -> None:
    assert canonical_side_to_freqtrade_side("BUY") == "long"
    assert canonical_side_to_freqtrade_side("SELL") == "short"


def test_canonical_side_to_freqtrade_side_maps_long_short() -> None:
    assert canonical_side_to_freqtrade_side("LONG") == "long"
    assert canonical_side_to_freqtrade_side("SHORT") == "short"


def test_load_context_by_attempt_key_reads_signals_and_operational_signals(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path)
    _insert_signal(db_path, attempt_key="atk_btc")
    _insert_operational_signal(db_path, parse_result_id=1, attempt_key="atk_btc")

    context = load_context_by_attempt_key("atk_btc", db_path)

    assert context is not None
    assert context.attempt_key == "atk_btc"
    assert context.pair == "BTC/USDT:USDT"
    assert context.side == "long"
    assert context.entry_tag == "atk_btc"
    assert context.stake_amount == 250.0
    assert context.leverage == 3
    assert context.management_rules == {"tp_handling": "ladder"}

    pending = load_pending_contexts_for_pair("BTC/USDT:USDT", db_path)
    assert [item.attempt_key for item in pending] == ["atk_btc"]


def test_pair_not_mappable_marks_context_not_executable(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path)
    _insert_signal(db_path, attempt_key="atk_bad_pair", symbol="BTCUSD")
    _insert_operational_signal(db_path, parse_result_id=1, attempt_key="atk_bad_pair")

    context = load_context_by_attempt_key("atk_bad_pair", db_path)

    assert context is not None
    assert context.pair is None
    assert context.is_pair_mappable is False
    assert context.is_executable is False


def test_populate_entry_trend_sets_entry_columns_from_normalizer(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path)
    _insert_signal(db_path, attempt_key="atk_entry")
    _insert_operational_signal(db_path, parse_result_id=1, attempt_key="atk_entry")

    strategy = SignalBridgeStrategy(config={})
    strategy.bot_db_path = db_path
    dataframe = _MiniDataFrame(rows=2)

    updated = strategy.populate_entry_trend(dataframe, {"pair": "BTC/USDT:USDT"})

    assert updated["enter_long"] == [0, 1]
    assert updated["enter_short"] == [0, 0]
    assert updated["enter_tag"] == [None, "atk_entry:ENTRY:0"]


def test_populate_entry_trend_skips_when_entry_order_already_open(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path)
    _insert_signal(db_path, attempt_key="atk_entry_open")
    _insert_operational_signal(db_path, parse_result_id=1, attempt_key="atk_entry_open")
    entry_order_open_callback(
        db_path=db_path,
        attempt_key="atk_entry_open",
        qty=0.004,
        price=60000.0,
        order_type="LIMIT",
    )

    strategy = SignalBridgeStrategy(config={})
    strategy.bot_db_path = db_path
    dataframe = _MiniDataFrame(rows=2)

    updated = strategy.populate_entry_trend(dataframe, {"pair": "BTC/USDT:USDT"})

    assert updated["enter_long"] == [0, 0]
    assert updated["enter_short"] == [0, 0]
    assert updated["enter_tag"] == [None, None]


def test_confirm_trade_entry_persists_open_limit_entry_runtime(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path)
    _insert_signal(db_path, attempt_key="atk_entry_runtime")
    _insert_operational_signal(db_path, parse_result_id=1, attempt_key="atk_entry_runtime")

    strategy = SignalBridgeStrategy(config={})
    strategy.bot_db_path = db_path

    accepted = strategy.confirm_trade_entry(
        pair="BTC/USDT:USDT",
        order_type="limit",
        amount=0.004,
        rate=60000.0,
        time_in_force="GTC",
        current_time=None,
        entry_tag="atk_entry_runtime",
        side="long",
    )

    assert accepted is True
    with sqlite3.connect(db_path) as conn:
        trade_row = conn.execute(
            "SELECT state, opened_at, meta_json FROM trades WHERE attempt_key = ?",
            ("atk_entry_runtime",),
        ).fetchone()
        order_row = conn.execute(
            "SELECT purpose, status, price, qty, client_order_id FROM orders WHERE attempt_key = ?",
            ("atk_entry_runtime",),
        ).fetchone()
        event_row = conn.execute(
            "SELECT event_type FROM events WHERE attempt_key = ? ORDER BY event_id DESC LIMIT 1",
            ("atk_entry_runtime",),
        ).fetchone()

    assert trade_row is not None
    assert trade_row[0] == "ENTRY_PENDING"
    assert trade_row[1] is None
    assert order_row == ("ENTRY", "OPEN", 60000.0, 0.004, "atk_entry_runtime:ENTRY:0")
    assert event_row == ("ENTRY_ORDER_OPENED",)


def test_custom_stake_amount_uses_operational_position_size_usdt(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path)
    _insert_signal(db_path, attempt_key="atk_stake")
    _insert_operational_signal(db_path, parse_result_id=1, attempt_key="atk_stake", position_size_usdt=333.0)

    strategy = SignalBridgeStrategy(config={})
    strategy.bot_db_path = db_path

    stake = strategy.custom_stake_amount("BTC/USDT:USDT", None, 60000.0, 10.0)

    assert stake == 333.0


def test_leverage_clamps_and_falls_back_to_one(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path)
    _insert_signal(db_path, attempt_key="atk_lev")
    _insert_operational_signal(db_path, parse_result_id=1, attempt_key="atk_lev", leverage=7)

    strategy = SignalBridgeStrategy(config={})
    strategy.bot_db_path = db_path

    assert strategy.leverage("BTC/USDT:USDT", None, 60000.0, 3.0, 5.0) == 5.0
    assert strategy.leverage("ETH/USDT:USDT", None, 3500.0, 3.0, 5.0) == 1.0


def test_custom_stoploss_uses_signal_initial_stop(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path)
    _insert_signal(db_path, attempt_key="atk_stop", status="ACTIVE", sl=57000.0)
    _insert_operational_signal(db_path, parse_result_id=1, attempt_key="atk_stop")

    strategy = SignalBridgeStrategy(config={})
    strategy.bot_db_path = db_path
    trade = SimpleNamespace(enter_tag="atk_stop")

    stoploss = strategy.custom_stoploss("BTC/USDT:USDT", trade, None, 60000.0, 0.0)

    assert stoploss == pytest.approx(-0.05)


def test_custom_stoploss_reflects_move_stop_update(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path)
    _insert_signal(db_path, attempt_key="atk_move_stop", status="ACTIVE", sl=57000.0)
    _insert_operational_signal(db_path, parse_result_id=1, attempt_key="atk_move_stop")

    plan = build_update_plan(
        {
            "message_type": "UPDATE",
            "actions": ["ACT_MOVE_STOP_LOSS"],
            "entities": {"new_stop_level": "ENTRY"},
            "target_refs": [1],
        }
    )
    result = apply_update_plan(plan, db_path, target_attempt_keys=["atk_move_stop"])
    assert result.errors == []

    strategy = SignalBridgeStrategy(config={})
    strategy.bot_db_path = db_path
    trade = SimpleNamespace(enter_tag="atk_move_stop")

    stoploss = strategy.custom_stoploss("BTC/USDT:USDT", trade, None, 63000.0, 0.0)

    assert stoploss == (60000.0 / 63000.0) - 1.0


def test_custom_stoploss_falls_back_when_exchange_manager_owns_protectives(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path)
    _insert_signal(db_path, attempt_key="atk_stop_manager", status="ACTIVE", sl=57000.0)
    _insert_operational_signal(db_path, parse_result_id=1, attempt_key="atk_stop_manager")

    strategy = SignalBridgeStrategy(config={"execution": {"protective_orders_mode": "exchange_manager"}})
    strategy.bot_db_path = db_path
    trade = SimpleNamespace(enter_tag="atk_stop_manager")

    stoploss = strategy.custom_stoploss("BTC/USDT:USDT", trade, None, 60000.0, 0.0)

    assert stoploss == strategy.stoploss


def test_populate_exit_trend_emits_close_full_only_for_eligible_updates(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path, parse_result_id=1)
    _insert_signal(db_path, attempt_key="atk_exit", status="ACTIVE")
    _insert_operational_signal(db_path, parse_result_id=1, attempt_key="atk_exit")
    _insert_update_parse_result(db_path, parse_result_id=2, intents=["U_CLOSE_FULL"])
    _insert_targeted_update(db_path, parse_result_id=2, target_op_signal_id=1, target_eligibility="ELIGIBLE")

    strategy = SignalBridgeStrategy(config={})
    strategy.bot_db_path = db_path
    dataframe = _MiniDataFrame(rows=2)

    updated = strategy.populate_exit_trend(dataframe, {"pair": "BTC/USDT:USDT"})

    assert updated["exit_long"] == [0, 1]
    assert updated["exit_short"] == [0, 0]
    assert updated["exit_tag"] == [None, "atk_exit"]


def test_adjust_trade_position_uses_partial_close_fraction_from_normalizer(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path, parse_result_id=1)
    _insert_signal(db_path, attempt_key="atk_partial", status="ACTIVE")
    _insert_operational_signal(db_path, parse_result_id=1, attempt_key="atk_partial")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO trades
               (env, attempt_key, trader_id, symbol, side, execution_mode, state,
                entry_zone_policy, non_chase_policy, opened_at, meta_json, created_at, updated_at)
               VALUES ('T', 'atk_partial', 'trader_3', 'BTCUSDT', 'BUY', 'FREQTRADE', 'OPEN',
                       'Z1', 'NI3', '2026-01-01', '{}', '2026-01-01', '2026-01-01')"""
        )
        conn.commit()
    _insert_update_parse_result(
        db_path,
        parse_result_id=2,
        intents=["U_CLOSE_PARTIAL"],
        entities={"close_fraction": 0.5},
    )
    _insert_targeted_update(db_path, parse_result_id=2, target_op_signal_id=1, target_eligibility="ELIGIBLE")

    strategy = SignalBridgeStrategy(config={})
    strategy.bot_db_path = db_path
    trade = SimpleNamespace(enter_tag="atk_partial", pair="BTC/USDT:USDT", stake_amount=250.0)

    reduction = strategy.adjust_trade_position(
        trade=trade,
        current_time=None,
        current_rate=61000.0,
        current_profit=0.05,
        min_stake=None,
        max_stake=1000.0,
    )

    assert reduction == (-125.0, "signal_close_partial:atk_partial")


def test_adjust_trade_position_requests_next_limit_entry_leg(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path, parse_result_id=1)
    _insert_signal(
        db_path,
        attempt_key="atk_next_limit",
        status="PENDING",
        entry_json=json.dumps([
            {"type": "MARKET", "price": None},
            {"type": "LIMIT", "price": 59500.0},
            {"type": "LIMIT", "price": 59000.0},
        ]),
    )
    _insert_operational_signal(
        db_path,
        parse_result_id=1,
        attempt_key="atk_next_limit",
        position_size_usdt=250.0,
        entry_split_json=json.dumps({"E1": 0.5, "E2": 0.3, "E3": 0.2}),
    )

    order_filled_callback(
        db_path=db_path,
        attempt_key="atk_next_limit",
        qty=1.0,
        fill_price=60000.0,
        client_order_id="entry-next-0",
        exchange_order_id="ex-entry-next-0",
        order_type="MARKET",
        entry_idx=0,
    )

    strategy = SignalBridgeStrategy(config={})
    strategy.bot_db_path = db_path
    trade = SimpleNamespace(enter_tag="atk_next_limit", pair="BTC/USDT:USDT", stake_amount=125.0, has_open_orders=False)

    adjustment = strategy.adjust_trade_position(
        trade=trade,
        current_time=None,
        current_rate=60000.0,
        current_profit=0.0,
        min_stake=None,
        max_stake=1000.0,
    )

    assert adjustment == (75.0, "atk_next_limit:ENTRY:1")


def test_adjust_trade_position_uses_take_profit_distribution_from_management_rules(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path, parse_result_id=1)
    _insert_signal(
        db_path,
        attempt_key="atk_tp_partial",
        status="PENDING",
        tp_json=json.dumps([{"price": 65000.0}, {"price": 66000.0}, {"price": 67000.0}]),
    )
    _insert_operational_signal(
        db_path,
        parse_result_id=1,
        attempt_key="atk_tp_partial",
        management_rules_json=json.dumps(
            {
                "tp_handling": {
                    "tp_handling_mode": "follow_all_signal_tps",
                    "tp_close_distribution": {"3": [30, 30, 40]},
                }
            }
        ),
    )

    strategy = SignalBridgeStrategy(config={"margin_mode": "isolated"})
    strategy.bot_db_path = db_path
    strategy.order_filled(
        "BTC/USDT:USDT",
        SimpleNamespace(enter_tag="atk_tp_partial", entry_side="buy"),
        SimpleNamespace(
            ft_order_side="buy",
            safe_filled=2.0,
            safe_price=60000.0,
            order_id="dry_run_buy_tp_partial",
            order_type="limit",
        ),
        None,
    )

    reduction = strategy.adjust_trade_position(
        trade=SimpleNamespace(
            enter_tag="atk_tp_partial",
            pair="BTC/USDT:USDT",
            stake_amount=250.0,
            has_open_orders=False,
        ),
        current_time=None,
        current_rate=65010.0,
        current_profit=0.08,
        min_stake=None,
        max_stake=1000.0,
    )

    assert reduction == (-75.0, "atk_tp_partial:TP:0")


def test_exchange_manager_disables_strategy_take_profit_emulation(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path, parse_result_id=1)
    _insert_signal(
        db_path,
        attempt_key="atk_tp_exchange_manager",
        status="PENDING",
        tp_json=json.dumps([{"price": 65000.0}, {"price": 66000.0}, {"price": 67000.0}]),
    )
    _insert_operational_signal(
        db_path,
        parse_result_id=1,
        attempt_key="atk_tp_exchange_manager",
        management_rules_json=json.dumps(
            {
                "tp_handling": {
                    "tp_handling_mode": "follow_all_signal_tps",
                    "tp_close_distribution": {"3": [30, 30, 40]},
                }
            }
        ),
    )

    strategy = SignalBridgeStrategy(
        config={
            "margin_mode": "isolated",
            "execution": {"protective_orders_mode": "exchange_manager"},
        }
    )
    strategy.bot_db_path = db_path
    strategy.order_filled(
        "BTC/USDT:USDT",
        SimpleNamespace(enter_tag="atk_tp_exchange_manager", entry_side="buy"),
        SimpleNamespace(
            ft_order_side="buy",
            safe_filled=2.0,
            safe_price=60000.0,
            order_id="dry_run_buy_tp_exchange_manager",
            order_type="limit",
        ),
        None,
    )

    reduction = strategy.adjust_trade_position(
        trade=SimpleNamespace(
            enter_tag="atk_tp_exchange_manager",
            pair="BTC/USDT:USDT",
            stake_amount=250.0,
            has_open_orders=False,
        ),
        current_time=None,
        current_rate=65010.0,
        current_profit=0.08,
        min_stake=None,
        max_stake=1000.0,
    )
    exit_tag = strategy.custom_exit(
        pair="BTC/USDT:USDT",
        trade=SimpleNamespace(
            enter_tag="atk_tp_exchange_manager",
            pair="BTC/USDT:USDT",
            has_open_orders=False,
        ),
        current_time=None,
        current_rate=67010.0,
        current_profit=0.11,
    )

    with sqlite3.connect(db_path) as conn:
        trade_mode = conn.execute(
            "SELECT protective_orders_mode FROM trades WHERE attempt_key = 'atk_tp_exchange_manager'"
        ).fetchone()[0]
        order_rows = conn.execute(
            "SELECT purpose FROM orders WHERE attempt_key = 'atk_tp_exchange_manager' ORDER BY order_pk"
        ).fetchall()

    assert reduction is None
    assert exit_tag is None
    assert trade_mode == "exchange_manager"
    assert order_rows == [("ENTRY",)]


def test_custom_exit_uses_last_take_profit_as_full_close(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path, parse_result_id=1)
    _insert_signal(
        db_path,
        attempt_key="atk_tp_full",
        status="PENDING",
        tp_json=json.dumps([{"price": 65000.0}, {"price": 66000.0}, {"price": 67000.0}]),
    )
    _insert_operational_signal(
        db_path,
        parse_result_id=1,
        attempt_key="atk_tp_full",
        management_rules_json=json.dumps(
            {
                "tp_handling": {
                    "tp_handling_mode": "follow_all_signal_tps",
                    "tp_close_distribution": {"3": [30, 30, 40]},
                }
            }
        ),
    )

    strategy = SignalBridgeStrategy(config={"margin_mode": "isolated"})
    strategy.bot_db_path = db_path
    strategy.order_filled(
        "BTC/USDT:USDT",
        SimpleNamespace(enter_tag="atk_tp_full", entry_side="buy"),
        SimpleNamespace(
            ft_order_side="buy",
            safe_filled=2.0,
            safe_price=60000.0,
            order_id="dry_run_buy_tp_full",
            order_type="limit",
        ),
        None,
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE orders SET status = 'FILLED' WHERE attempt_key = 'atk_tp_full' AND purpose = 'TP' AND idx IN (0, 1)"
        )
        conn.commit()

    exit_tag = strategy.custom_exit(
        pair="BTC/USDT:USDT",
        trade=SimpleNamespace(
            enter_tag="atk_tp_full",
            pair="BTC/USDT:USDT",
            has_open_orders=False,
        ),
        current_time=None,
        current_rate=67010.0,
        current_profit=0.11,
    )

    assert exit_tag == "atk_tp_full:TP:2"


def test_cancel_pending_blocks_pre_entry_and_pending_order(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path, parse_result_id=1)
    _insert_signal(db_path, attempt_key="atk_cancel_pending")
    _insert_operational_signal(db_path, parse_result_id=1, attempt_key="atk_cancel_pending")
    _insert_update_parse_result(db_path, parse_result_id=2, intents=["U_CANCEL_PENDING"])
    _insert_targeted_update(db_path, parse_result_id=2, target_op_signal_id=1, target_eligibility="ELIGIBLE")

    strategy = SignalBridgeStrategy(config={})
    strategy.bot_db_path = db_path

    accepted = strategy.confirm_trade_entry(
        pair="BTC/USDT:USDT",
        order_type="limit",
        amount=1.0,
        rate=60000.0,
        time_in_force="GTC",
        current_time=None,
        entry_tag="atk_cancel_pending",
        side="long",
    )
    timed_out = strategy.check_entry_timeout(
        pair="BTC/USDT:USDT",
        trade=SimpleNamespace(enter_tag="atk_cancel_pending"),
        order=SimpleNamespace(ft_order_tag="atk_cancel_pending"),
        current_time=None,
    )

    assert accepted is False
    assert timed_out is True


def test_confirm_trade_entry_rejects_signal_not_pending(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path)
    _insert_signal(db_path, attempt_key="atk_closed", status="ACTIVE")
    _insert_operational_signal(db_path, parse_result_id=1, attempt_key="atk_closed")

    strategy = SignalBridgeStrategy(config={})
    strategy.bot_db_path = db_path

    accepted = strategy.confirm_trade_entry(
        pair="BTC/USDT:USDT",
        order_type="limit",
        amount=1.0,
        rate=60000.0,
        time_in_force="GTC",
        current_time=None,
        entry_tag="atk_closed",
        side="long",
    )

    assert accepted is False

def test_order_filled_hook_persists_entry_fill_to_bot_db(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path)
    _insert_signal(db_path, attempt_key="atk_hook_fill")
    _insert_operational_signal(db_path, parse_result_id=1, attempt_key="atk_hook_fill")

    strategy = SignalBridgeStrategy(config={"margin_mode": "isolated"})
    strategy.bot_db_path = db_path
    trade = SimpleNamespace(enter_tag="atk_hook_fill", entry_side="buy")
    order = SimpleNamespace(
        ft_order_side="buy",
        safe_filled=1.5,
        safe_price=60000.0,
        order_id="dry_run_buy_1",
        order_type="limit",
    )

    strategy.order_filled("BTC/USDT:USDT", trade, order, None)

    with sqlite3.connect(db_path) as conn:
        signal_status = conn.execute(
            "SELECT status FROM signals WHERE attempt_key = 'atk_hook_fill'"
        ).fetchone()[0]
        trade_row = conn.execute(
            "SELECT state, execution_mode FROM trades WHERE attempt_key = 'atk_hook_fill'"
        ).fetchone()
        position_row = conn.execute(
            "SELECT size, leverage, margin_mode FROM positions WHERE symbol = 'BTCUSDT'"
        ).fetchone()
        event_types = [
            row[0]
            for row in conn.execute(
                "SELECT event_type FROM events WHERE attempt_key = 'atk_hook_fill' ORDER BY event_id"
            ).fetchall()
        ]

    assert signal_status == "ACTIVE"
    assert trade_row == ("OPEN", "FREQTRADE")
    assert position_row == (1.5, 3.0, "isolated")
    assert "ENTRY_FILLED" in event_types

def test_order_filled_hook_persists_full_exit_to_bot_db(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path)
    _insert_signal(db_path, attempt_key="atk_hook_exit")
    _insert_operational_signal(db_path, parse_result_id=1, attempt_key="atk_hook_exit")

    strategy = SignalBridgeStrategy(config={"margin_mode": "isolated"})
    strategy.bot_db_path = db_path
    entry_trade = SimpleNamespace(enter_tag="atk_hook_exit", entry_side="buy")
    entry_order = SimpleNamespace(
        ft_order_side="buy",
        safe_filled=1.5,
        safe_price=60000.0,
        order_id="dry_run_buy_exit_1",
        order_type="limit",
    )
    strategy.order_filled("BTC/USDT:USDT", entry_trade, entry_order, None)

    exit_trade = SimpleNamespace(
        enter_tag="atk_hook_exit",
        entry_side="buy",
        exit_side="sell",
        is_open=False,
        amount=0.0,
        exit_reason="FULL_CLOSE_REQUESTED",
    )
    exit_order = SimpleNamespace(
        ft_order_side="sell",
        safe_filled=1.5,
        safe_amount_after_fee=1.5,
        safe_price=61000.0,
        order_id="dry_run_sell_exit_1",
        order_type="limit",
    )

    strategy.order_filled("BTC/USDT:USDT", exit_trade, exit_order, None)

    with sqlite3.connect(db_path) as conn:
        trade_row = conn.execute(
            "SELECT state, close_reason FROM trades WHERE attempt_key = 'atk_hook_exit'"
        ).fetchone()
        position_row = conn.execute(
            "SELECT size, mark_price FROM positions WHERE symbol = 'BTCUSDT'"
        ).fetchone()
        event_types = [
            row[0]
            for row in conn.execute(
                "SELECT event_type FROM events WHERE attempt_key = 'atk_hook_exit' ORDER BY event_id"
            ).fetchall()
        ]

    assert trade_row == ("CLOSED", "FULL_CLOSE_REQUESTED")
    assert position_row == (0.0, 61000.0)
    assert "POSITION_CLOSED" in event_types


def test_order_filled_hook_persists_partial_exit_to_bot_db(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path)
    _insert_signal(db_path, attempt_key="atk_hook_partial")
    _insert_operational_signal(db_path, parse_result_id=1, attempt_key="atk_hook_partial")

    strategy = SignalBridgeStrategy(config={"margin_mode": "isolated"})
    strategy.bot_db_path = db_path
    entry_trade = SimpleNamespace(enter_tag="atk_hook_partial", entry_side="buy")
    entry_order = SimpleNamespace(
        ft_order_side="buy",
        safe_filled=2.0,
        safe_price=60000.0,
        order_id="dry_run_buy_partial_1",
        order_type="limit",
    )
    strategy.order_filled("BTC/USDT:USDT", entry_trade, entry_order, None)

    partial_trade = SimpleNamespace(
        enter_tag="atk_hook_partial",
        entry_side="buy",
        exit_side="sell",
        is_open=True,
        amount=1.0,
    )
    partial_order = SimpleNamespace(
        ft_order_side="sell",
        safe_filled=1.0,
        safe_amount_after_fee=1.0,
        safe_price=61000.0,
        order_id="dry_run_sell_partial_1",
        order_type="limit",
    )

    strategy.order_filled("BTC/USDT:USDT", partial_trade, partial_order, None)

    with sqlite3.connect(db_path) as conn:
        trade_row = conn.execute(
            "SELECT state, meta_json FROM trades WHERE attempt_key = 'atk_hook_partial'"
        ).fetchone()
        position_row = conn.execute(
            "SELECT size, mark_price FROM positions WHERE symbol = 'BTCUSDT'"
        ).fetchone()
        exit_order_row = conn.execute(
            "SELECT purpose, status, qty FROM orders WHERE attempt_key = 'atk_hook_partial' AND purpose = 'EXIT'"
        ).fetchone()
        event_types = [
            row[0]
            for row in conn.execute(
                "SELECT event_type FROM events WHERE attempt_key = 'atk_hook_partial' ORDER BY event_id"
            ).fetchall()
        ]

    trade_meta = json.loads(trade_row[1])
    assert trade_row[0] == "OPEN"
    assert trade_meta["close_fraction"] == 0.5
    assert position_row == (1.0, 61000.0)
    assert exit_order_row == ("EXIT", "FILLED", 1.0)
    assert "PARTIAL_CLOSE_FILLED" in event_types


def test_order_filled_hook_marks_take_profit_row_and_reason(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path)
    _insert_signal(
        db_path,
        attempt_key="atk_hook_tp",
        tp_json=json.dumps([{"price": 61000.0}]),
    )
    _insert_operational_signal(db_path, parse_result_id=1, attempt_key="atk_hook_tp")

    strategy = SignalBridgeStrategy(config={"margin_mode": "isolated"})
    strategy.bot_db_path = db_path
    strategy.order_filled(
        "BTC/USDT:USDT",
        SimpleNamespace(enter_tag="atk_hook_tp", entry_side="buy"),
        SimpleNamespace(
            ft_order_side="buy",
            safe_filled=1.0,
            safe_price=60000.0,
            order_id="dry_run_buy_tp_1",
            order_type="limit",
        ),
        None,
    )

    strategy.order_filled(
        "BTC/USDT:USDT",
        SimpleNamespace(
            enter_tag="atk_hook_tp",
            entry_side="buy",
            exit_side="sell",
            is_open=False,
            amount=0.0,
            exit_reason="atk_hook_tp:TP:0",
        ),
        SimpleNamespace(
            ft_order_side="sell",
            ft_order_tag="atk_hook_tp:TP:0",
            safe_filled=1.0,
            safe_amount_after_fee=1.0,
            safe_price=61000.0,
            order_id="dry_run_sell_tp_1",
            order_type="limit",
        ),
        None,
    )

    with sqlite3.connect(db_path) as conn:
        tp_row = conn.execute(
            "SELECT status, price FROM orders WHERE attempt_key = 'atk_hook_tp' AND purpose = 'TP' AND idx = 0"
        ).fetchone()
        trade_row = conn.execute(
            "SELECT state, close_reason, meta_json FROM trades WHERE attempt_key = 'atk_hook_tp'"
        ).fetchone()

    assert tp_row == ("FILLED", 61000.0)
    assert trade_row[0] == "CLOSED"
    assert trade_row[1] == "TP1_HIT"
    assert json.loads(trade_row[2])["tp_filled_indices"] == [0]


# ---------------------------------------------------------------------------
# Entry contract: entry_prices and entry_split are preserved end-to-end
# ---------------------------------------------------------------------------


def _insert_signal_with_entry(
    db_path: str,
    *,
    attempt_key: str,
    entry_json: str,
    symbol: str = "BTCUSDT",
    side: str = "BUY",
    status: str = "PENDING",
    sl: float = 57000.0,
) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO signals
               (attempt_key, env, channel_id, root_telegram_id, trader_id, trader_prefix,
                symbol, side, entry_json, sl, tp_json, status, confidence, raw_text,
                created_at, updated_at)
               VALUES (?, 'T', '-100999', '1', 'trader_3', 'TRAD',
                       ?, ?, ?, ?, '[]', ?, 0.9, 'fixture',
                       '2026-01-01', '2026-01-01')""",
            (attempt_key, symbol, side, entry_json, sl, status),
        )
        conn.commit()


def _insert_op_signal_with_split(
    db_path: str,
    *,
    parse_result_id: int,
    attempt_key: str,
    entry_split_json: str | None = None,
    position_size_usdt: float = 250.0,
    leverage: int = 3,
    management_rules_json: str | None = None,
) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO operational_signals
               (parse_result_id, attempt_key, trader_id, message_type, is_blocked,
                position_size_usdt, leverage, entry_split_json, management_rules_json, created_at)
               VALUES (?, ?, 'trader_3', 'NEW_SIGNAL', 0, ?, ?, ?, ?, '2026-01-01')""",
            (parse_result_id, attempt_key, position_size_usdt, leverage, entry_split_json,
             management_rules_json or "{}"),
        )
        conn.commit()


def test_context_exposes_entry_prices_from_signals_table(tmp_path: Path) -> None:
    """entry_json from signals is surfaced in FreqtradeSignalContext.entry_prices."""
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path)
    entry_data = [{"price": 66100.0, "type": "LIMIT"}, {"price": 66200.0, "type": "LIMIT"}]
    _insert_signal_with_entry(db_path, attempt_key="atk_ep", entry_json=json.dumps(entry_data))
    _insert_op_signal_with_split(db_path, parse_result_id=1, attempt_key="atk_ep")

    context = load_context_by_attempt_key("atk_ep", db_path)

    assert context is not None
    assert len(context.entry_prices) == 2
    assert context.entry_prices[0]["price"] == 66100.0
    assert context.entry_prices[0]["type"] == "LIMIT"
    assert context.entry_prices[1]["price"] == 66200.0


def test_context_exposes_entry_split_from_operational_signals(tmp_path: Path) -> None:
    """entry_split_json from operational_signals is surfaced in FreqtradeSignalContext.entry_split."""
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path)
    entry_data = [{"price": 66100.0, "type": "LIMIT"}, {"price": 66200.0, "type": "LIMIT"}]
    split_data = {"E1": 0.5, "E2": 0.5}
    _insert_signal_with_entry(db_path, attempt_key="atk_split", entry_json=json.dumps(entry_data))
    _insert_op_signal_with_split(
        db_path,
        parse_result_id=1,
        attempt_key="atk_split",
        entry_split_json=json.dumps(split_data),
    )

    context = load_context_by_attempt_key("atk_split", db_path)

    assert context is not None
    assert context.entry_split == {"E1": 0.5, "E2": 0.5}


def test_context_entry_split_is_none_when_not_persisted(tmp_path: Path) -> None:
    """entry_split is None when operational_signals.entry_split_json is NULL."""
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path)
    _insert_signal_with_entry(
        db_path,
        attempt_key="atk_no_split",
        entry_json=json.dumps([{"price": 60000.0, "type": "LIMIT"}]),
    )
    _insert_op_signal_with_split(db_path, parse_result_id=1, attempt_key="atk_no_split", entry_split_json=None)

    context = load_context_by_attempt_key("atk_no_split", db_path)

    assert context is not None
    assert context.entry_split is None


# ---------------------------------------------------------------------------
# Single-entry policy: first_in_plan
# ---------------------------------------------------------------------------


def test_first_entry_price_returns_first_limit_price(tmp_path: Path) -> None:
    """first_entry_price == entry_prices[0]["price"] for a LIMIT plan."""
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path)
    entry_data = [{"price": 66100.0, "type": "LIMIT"}, {"price": 66200.0, "type": "LIMIT"}]
    _insert_signal_with_entry(db_path, attempt_key="atk_fep", entry_json=json.dumps(entry_data))
    _insert_op_signal_with_split(db_path, parse_result_id=1, attempt_key="atk_fep")

    context = load_context_by_attempt_key("atk_fep", db_path)

    assert context is not None
    assert context.first_entry_price == 66100.0
    assert context.first_entry_order_type == "LIMIT"


def test_first_entry_price_returns_none_for_market_entry(tmp_path: Path) -> None:
    """first_entry_price is None when entry is MARKET (no price to enforce)."""
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path)
    entry_data = [{"price": None, "type": "MARKET"}]
    _insert_signal_with_entry(db_path, attempt_key="atk_mkt", entry_json=json.dumps(entry_data))
    _insert_op_signal_with_split(db_path, parse_result_id=1, attempt_key="atk_mkt")

    context = load_context_by_attempt_key("atk_mkt", db_path)

    assert context is not None
    assert context.first_entry_price is None
    assert context.first_entry_order_type == "MARKET"


def test_first_entry_price_returns_none_when_no_entries(tmp_path: Path) -> None:
    """first_entry_price is None when entry_prices is empty."""
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path)
    _insert_signal_with_entry(db_path, attempt_key="atk_empty", entry_json="[]")
    _insert_op_signal_with_split(db_path, parse_result_id=1, attempt_key="atk_empty")

    context = load_context_by_attempt_key("atk_empty", db_path)

    assert context is not None
    assert context.first_entry_price is None
    assert context.first_entry_order_type == "MARKET"


def test_custom_entry_price_returns_first_limit_price(tmp_path: Path) -> None:
    """custom_entry_price() returns E1 price for a LIMIT signal."""
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path)
    entry_data = [{"price": 66100.0, "type": "LIMIT"}, {"price": 66200.0, "type": "LIMIT"}]
    _insert_signal_with_entry(db_path, attempt_key="atk_cep_limit", entry_json=json.dumps(entry_data))
    _insert_op_signal_with_split(db_path, parse_result_id=1, attempt_key="atk_cep_limit")

    strategy = SignalBridgeStrategy(config={"bot_db_path": db_path})
    result = strategy.custom_entry_price(
        pair="BTC/USDT:USDT",
        trade=None,
        current_time=None,
        proposed_rate=66500.0,
        entry_tag="atk_cep_limit",
        side="long",
    )

    assert result == 66100.0


def test_custom_entry_price_falls_back_for_market_entry(tmp_path: Path) -> None:
    """custom_entry_price() falls back to proposed_rate for MARKET entries."""
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path)
    entry_data = [{"price": None, "type": "MARKET"}]
    _insert_signal_with_entry(db_path, attempt_key="atk_cep_mkt", entry_json=json.dumps(entry_data))
    _insert_op_signal_with_split(db_path, parse_result_id=1, attempt_key="atk_cep_mkt")

    strategy = SignalBridgeStrategy(config={"bot_db_path": db_path})
    result = strategy.custom_entry_price(
        pair="BTC/USDT:USDT",
        trade=None,
        current_time=None,
        proposed_rate=66500.0,
        entry_tag="atk_cep_mkt",
        side="long",
    )

    assert result == 66500.0


def test_custom_entry_price_falls_back_when_no_context(tmp_path: Path) -> None:
    """custom_entry_price() returns proposed_rate when no pending context exists."""
    db_path = _make_db(tmp_path)
    strategy = SignalBridgeStrategy(config={"bot_db_path": db_path})
    result = strategy.custom_entry_price(
        pair="ETH/USDT:USDT",
        trade=None,
        current_time=None,
        proposed_rate=3200.0,
        entry_tag=None,
        side="long",
    )

    assert result == 3200.0


def test_custom_entry_price_returns_limit_price_for_second_entry_tag(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path)
    _insert_signal(
        db_path,
        attempt_key="atk_second_leg_price",
        entry_json=json.dumps([
            {"type": "MARKET", "price": None},
            {"type": "LIMIT", "price": 59500.0},
            {"type": "LIMIT", "price": 59000.0},
        ]),
        status="ACTIVE",
    )
    _insert_operational_signal(
        db_path,
        parse_result_id=1,
        attempt_key="atk_second_leg_price",
        entry_split_json=json.dumps({"E1": 0.5, "E2": 0.3, "E3": 0.2}),
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO trades
               (env, attempt_key, trader_id, symbol, side, execution_mode, state, meta_json, created_at, updated_at)
               VALUES ('T', 'atk_second_leg_price', 'trader_3', 'BTCUSDT', 'BUY', 'FREQTRADE', 'OPEN', ?, '2026-01-01', '2026-01-01')""",
            (json.dumps({"entry_legs": [
                {"entry_id": "E1", "sequence": 1, "order_type": "MARKET", "price": None, "split": 0.5, "status": "FILLED", "filled_at": "2026-01-01T00:00:00+00:00"},
                {"entry_id": "E2", "sequence": 2, "order_type": "LIMIT", "price": 59500.0, "split": 0.3, "status": "PENDING"},
                {"entry_id": "E3", "sequence": 3, "order_type": "LIMIT", "price": 59000.0, "split": 0.2, "status": "PENDING"},
            ]}),),
        )
        conn.commit()

    strategy = SignalBridgeStrategy(config={})
    strategy.bot_db_path = db_path

    price = strategy.custom_entry_price(
        pair="BTC/USDT:USDT",
        trade=None,
        current_time=None,
        proposed_rate=61000.0,
        entry_tag="atk_second_leg_price:ENTRY:1",
        side="long",
    )

    assert price == 59500.0


def test_custom_entry_price_zone_uses_first_endpoint(tmp_path: Path) -> None:
    """For ZONE plans, E1 (first/lower endpoint) is used as the single entry price."""
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path)
    # ZONE plan: E1=lower endpoint, E2=upper endpoint
    entry_data = [{"price": 66100.0, "type": "LIMIT"}, {"price": 66200.0, "type": "LIMIT"}]
    split_data = {"E1": 0.5, "E2": 0.5}
    _insert_signal_with_entry(db_path, attempt_key="atk_zone", entry_json=json.dumps(entry_data))
    _insert_op_signal_with_split(
        db_path,
        parse_result_id=1,
        attempt_key="atk_zone",
        entry_split_json=json.dumps(split_data),
    )

    strategy = SignalBridgeStrategy(config={"bot_db_path": db_path})
    result = strategy.custom_entry_price(
        pair="BTC/USDT:USDT",
        trade=None,
        current_time=None,
        proposed_rate=66500.0,
        entry_tag="atk_zone",
        side="long",
    )

    # E1 = 66100.0 (conservative lower bound for long)
    assert result == 66100.0


def test_adjust_entry_price_keeps_first_limit_price_on_reprice(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path)
    entry_data = [{"price": 66100.0, "type": "LIMIT"}, {"price": 66200.0, "type": "LIMIT"}]
    _insert_signal_with_entry(db_path, attempt_key="atk_adjust_limit", entry_json=json.dumps(entry_data))
    _insert_op_signal_with_split(db_path, parse_result_id=1, attempt_key="atk_adjust_limit")

    strategy = SignalBridgeStrategy(config={"bot_db_path": db_path})
    result = strategy.adjust_entry_price(
        trade=None,
        order=None,
        pair="BTC/USDT:USDT",
        current_time=None,
        proposed_rate=66500.0,
        current_order_rate=66400.0,
        entry_tag="atk_adjust_limit",
        side="long",
    )

    assert result == 66100.0


def test_adjust_entry_price_preserves_market_behavior(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path)
    entry_data = [{"price": None, "type": "MARKET"}]
    _insert_signal_with_entry(db_path, attempt_key="atk_adjust_market", entry_json=json.dumps(entry_data))
    _insert_op_signal_with_split(db_path, parse_result_id=1, attempt_key="atk_adjust_market")

    strategy = SignalBridgeStrategy(config={"bot_db_path": db_path})
    result = strategy.adjust_entry_price(
        trade=None,
        order=None,
        pair="BTC/USDT:USDT",
        current_time=None,
        proposed_rate=66500.0,
        current_order_rate=66400.0,
        entry_tag="atk_adjust_market",
        side="long",
    )

    assert result == 66400.0


# ---------------------------------------------------------------------------
# Entry price policy: unit tests for check_entry_rate and resolve_entry_price_policy
# ---------------------------------------------------------------------------


_STRICT_POLICY = EntryPricePolicy(enabled=True, max_slippage_pct=0.005, zone_tolerance_pct=0.002)
_OFF_POLICY = EntryPricePolicy(enabled=False)


def _limit_entry(price: float) -> dict:
    return {"price": price, "type": "LIMIT"}


def _market_entry() -> dict:
    return {"price": None, "type": "MARKET"}


# -- check_entry_rate: policy disabled -----------------------------------------

def test_check_entry_rate_disabled_policy_always_allows() -> None:
    entries = (_limit_entry(66100.0),)
    result = check_entry_rate(entries, rate=70000.0, order_type="limit", policy=_OFF_POLICY)
    assert result is None


# -- check_entry_rate: MARKET order type -----------------------------------------

def test_check_entry_rate_market_order_type_always_allows() -> None:
    entries = (_limit_entry(66100.0),)
    result = check_entry_rate(entries, rate=70000.0, order_type="market", policy=_STRICT_POLICY)
    assert result is None


# -- check_entry_rate: first entry is MARKET type --------------------------------

def test_check_entry_rate_first_entry_market_type_always_allows() -> None:
    entries = (_market_entry(),)
    result = check_entry_rate(entries, rate=70000.0, order_type="limit", policy=_STRICT_POLICY)
    assert result is None


# -- check_entry_rate: no entries -----------------------------------------------

def test_check_entry_rate_no_entries_always_allows() -> None:
    result = check_entry_rate((), rate=66500.0, order_type="limit", policy=_STRICT_POLICY)
    assert result is None


# -- check_entry_rate: single LIMIT price ---------------------------------------

def test_check_entry_rate_single_limit_within_tolerance() -> None:
    entries = (_limit_entry(66100.0),)
    # 66100 * 1.004 = 66364.4 → within 0.5%
    result = check_entry_rate(entries, rate=66364.0, order_type="limit", policy=_STRICT_POLICY)
    assert result is None


def test_check_entry_rate_single_limit_outside_tolerance_high() -> None:
    entries = (_limit_entry(66100.0),)
    # 66100 * 1.006 = 66496.6 → 0.6% > 0.5% tolerance → rejected
    result = check_entry_rate(entries, rate=66496.6, order_type="limit", policy=_STRICT_POLICY)
    assert result is not None
    assert result["reason"] == "rate_outside_limit_tolerance"
    assert result["e1"] == 66100.0
    assert result["e2"] is None
    assert result["deviation_pct"] > _STRICT_POLICY.max_slippage_pct


def test_check_entry_rate_single_limit_outside_tolerance_low() -> None:
    entries = (_limit_entry(66100.0),)
    # 66100 * 0.994 = 65723.4 → 0.6% below → rejected
    result = check_entry_rate(entries, rate=65723.4, order_type="limit", policy=_STRICT_POLICY)
    assert result is not None
    assert result["reason"] == "rate_outside_limit_tolerance"


# -- check_entry_rate: ZONE (multi-price) ---------------------------------------

def test_check_entry_rate_zone_inside_bounds() -> None:
    # Zone [66100, 66200], rate=66150 → inside → no rejection
    entries = (_limit_entry(66100.0), _limit_entry(66200.0))
    result = check_entry_rate(entries, rate=66150.0, order_type="limit", policy=_STRICT_POLICY)
    assert result is None


def test_check_entry_rate_zone_at_upper_edge_with_tolerance() -> None:
    # Zone [66100, 66200], tol=0.2%, hi_bound=66200*1.002=66332.4, rate=66222 < 66332 → OK
    entries = (_limit_entry(66100.0), _limit_entry(66200.0))
    result = check_entry_rate(entries, rate=66222.0, order_type="limit", policy=_STRICT_POLICY)
    assert result is None


def test_check_entry_rate_zone_above_upper_bound() -> None:
    # Zone [66100, 66200], tol=0.2%, hi_bound=66332, rate=66400 → rejected
    entries = (_limit_entry(66100.0), _limit_entry(66200.0))
    result = check_entry_rate(entries, rate=66400.0, order_type="limit", policy=_STRICT_POLICY)
    assert result is not None
    assert result["reason"] == "rate_above_zone"
    assert result["e1"] == 66100.0
    assert result["e2"] == 66200.0


def test_check_entry_rate_zone_below_lower_bound() -> None:
    # Zone [66100, 66200], tol=0.2%, lo_bound=66100*0.998=65967.8, rate=65900 → rejected
    entries = (_limit_entry(66100.0), _limit_entry(66200.0))
    result = check_entry_rate(entries, rate=65900.0, order_type="limit", policy=_STRICT_POLICY)
    assert result is not None
    assert result["reason"] == "rate_below_zone"


# -- resolve_entry_price_policy -------------------------------------------------

def test_resolve_entry_price_policy_from_management_rules() -> None:
    mgmt = {"entry_policy": {"enabled": True, "max_slippage_pct": 0.01, "zone_tolerance_pct": 0.003}}
    policy = resolve_entry_price_policy(mgmt, None)
    assert policy.enabled is True
    assert policy.max_slippage_pct == 0.01
    assert policy.zone_tolerance_pct == 0.003


def test_resolve_entry_price_policy_from_runtime_config() -> None:
    config = {"execution": {"entry_price_policy": {"enabled": False}}}
    policy = resolve_entry_price_policy(None, config)
    assert policy.enabled is False


def test_resolve_entry_price_policy_management_rules_takes_precedence() -> None:
    mgmt = {"entry_policy": {"max_slippage_pct": 0.02}}
    config = {"execution": {"entry_price_policy": {"max_slippage_pct": 0.10}}}
    policy = resolve_entry_price_policy(mgmt, config)
    assert policy.max_slippage_pct == 0.02  # management_rules wins


def test_resolve_entry_price_policy_defaults_when_absent() -> None:
    policy = resolve_entry_price_policy(None, None)
    assert policy.enabled is True
    assert policy.max_slippage_pct == 0.005
    assert policy.zone_tolerance_pct == 0.002


def test_resolve_entry_price_policy_disabled_in_management_rules() -> None:
    mgmt = {"entry_policy": {"enabled": False}}
    policy = resolve_entry_price_policy(mgmt, None)
    assert policy.enabled is False


# -- confirm_trade_entry integration with price policy --------------------------

def test_confirm_trade_entry_accepts_rate_within_tolerance(tmp_path: Path) -> None:
    """confirm_trade_entry allows rate within policy tolerance for LIMIT signal."""
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path)
    # E1=66100, max_slippage=0.5%: rate 66200 is +0.15% → accepted
    entry_data = [{"price": 66100.0, "type": "LIMIT"}]
    mgmt = {"entry_policy": {"enabled": True, "max_slippage_pct": 0.005}}
    _insert_signal_with_entry(db_path, attempt_key="atk_conf_ok", entry_json=json.dumps(entry_data))
    _insert_op_signal_with_split(
        db_path, parse_result_id=1, attempt_key="atk_conf_ok",
        management_rules_json=json.dumps(mgmt),
    )

    strategy = SignalBridgeStrategy(config={"bot_db_path": db_path})
    result = strategy.confirm_trade_entry(
        pair="BTC/USDT:USDT",
        order_type="limit",
        amount=0.01,
        rate=66200.0,
        time_in_force="gtc",
        current_time=None,
        entry_tag="atk_conf_ok",
        side="long",
    )

    assert result is True


def test_confirm_trade_entry_limit_uses_planned_entry_price(tmp_path: Path) -> None:
    """For LIMIT entries, confirm_trade_entry validates against the planned limit price, not proposed_rate."""
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path)
    # Even if proposed rate is far away, LIMIT entries use E1 as effective order price.
    entry_data = [{"price": 66100.0, "type": "LIMIT"}]
    mgmt = {"entry_policy": {"enabled": True, "max_slippage_pct": 0.005}}
    _insert_signal_with_entry(db_path, attempt_key="atk_conf_rej", entry_json=json.dumps(entry_data))
    _insert_op_signal_with_split(
        db_path, parse_result_id=1, attempt_key="atk_conf_rej",
        management_rules_json=json.dumps(mgmt),
    )

    strategy = SignalBridgeStrategy(config={"bot_db_path": db_path})
    result = strategy.confirm_trade_entry(
        pair="BTC/USDT:USDT",
        order_type="limit",
        amount=0.01,
        rate=66800.0,
        time_in_force="gtc",
        current_time=None,
        entry_tag="atk_conf_rej",
        side="long",
    )

    assert result is True

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM events WHERE attempt_key = ? AND event_type = 'ENTRY_PRICE_REJECTED'",
            ("atk_conf_rej",),
        ).fetchone()
    assert row is not None
    assert row[0] == 0


def test_confirm_trade_entry_policy_disabled_allows_any_rate(tmp_path: Path) -> None:
    """When entry_policy.enabled=False, any rate is accepted (backward compat)."""
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path)
    entry_data = [{"price": 66100.0, "type": "LIMIT"}]
    mgmt = {"entry_policy": {"enabled": False}}
    _insert_signal_with_entry(db_path, attempt_key="atk_conf_off", entry_json=json.dumps(entry_data))
    _insert_op_signal_with_split(
        db_path, parse_result_id=1, attempt_key="atk_conf_off",
        management_rules_json=json.dumps(mgmt),
    )

    strategy = SignalBridgeStrategy(config={"bot_db_path": db_path})
    result = strategy.confirm_trade_entry(
        pair="BTC/USDT:USDT",
        order_type="limit",
        amount=0.01,
        rate=99999.0,  # wildly off-range
        time_in_force="gtc",
        current_time=None,
        entry_tag="atk_conf_off",
        side="long",
    )

    assert result is True


def test_confirm_trade_entry_market_order_skips_price_check(tmp_path: Path) -> None:
    """Market orders bypass the price policy check."""
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path)
    entry_data = [{"price": None, "type": "MARKET"}]
    mgmt = {"entry_policy": {"enabled": True, "max_slippage_pct": 0.001}}  # very strict
    _insert_signal_with_entry(db_path, attempt_key="atk_conf_mkt", entry_json=json.dumps(entry_data))
    _insert_op_signal_with_split(
        db_path, parse_result_id=1, attempt_key="atk_conf_mkt",
        management_rules_json=json.dumps(mgmt),
    )

    strategy = SignalBridgeStrategy(config={"bot_db_path": db_path})
    result = strategy.confirm_trade_entry(
        pair="BTC/USDT:USDT",
        order_type="market",
        amount=0.01,
        rate=99999.0,
        time_in_force="gtc",
        current_time=None,
        entry_tag="atk_conf_mkt",
        side="long",
    )

    assert result is True


def test_confirm_trade_entry_rejects_market_signal_with_limit_runtime(tmp_path: Path) -> None:
    """MARKET first legs are reserved for the dispatcher, not the strategy LIMIT path."""
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path)
    entry_data = [{"price": 60000.0, "type": "MARKET"}]
    _insert_signal_with_entry(db_path, attempt_key="atk_conf_mkt_limit", entry_json=json.dumps(entry_data))
    _insert_op_signal_with_split(db_path, parse_result_id=1, attempt_key="atk_conf_mkt_limit")

    strategy = SignalBridgeStrategy(config={"bot_db_path": db_path})
    result = strategy.confirm_trade_entry(
        pair="BTC/USDT:USDT",
        order_type="limit",
        amount=0.01,
        rate=60000.0,
        time_in_force="gtc",
        current_time=None,
        entry_tag="atk_conf_mkt_limit",
        side="long",
    )

    assert result is False


def test_persist_entry_price_rejected_event_writes_to_db(tmp_path: Path) -> None:
    """persist_entry_price_rejected_event inserts an ENTRY_PRICE_REJECTED row."""
    db_path = _make_db(tmp_path)
    info = {"reason": "rate_outside_limit_tolerance", "rate": 67000.0, "e1": 66100.0}

    persist_entry_price_rejected_event(db_path, "T_-100999_12345_trader_3", info)

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT env, channel_id, telegram_msg_id, trader_id, event_type, payload_json"
            " FROM events WHERE attempt_key = 'T_-100999_12345_trader_3'"
        ).fetchone()
    assert row is not None
    assert row[0] == "T"
    assert row[1] == "-100999"
    assert row[2] == "12345"
    assert row[3] == "trader_3"
    assert row[4] == "ENTRY_PRICE_REJECTED"
    payload = json.loads(row[5])
    assert payload["reason"] == "rate_outside_limit_tolerance"


# ---------------------------------------------------------------------------
# position_management: auto_apply_intents filter (Step D)
# ---------------------------------------------------------------------------


def _mgmt_trader_hint(auto_apply: list[str], log_only: list[str] | None = None) -> dict:
    return {
        "mode": "trader_hint",
        "trader_hint": {
            "auto_apply_intents": auto_apply,
            "log_only_intents": log_only or [],
        },
        "machine_event": {"rules": []},
    }


def _mgmt_machine_event() -> dict:
    return {
        "mode": "machine_event",
        "trader_hint": {"auto_apply_intents": [], "log_only_intents": []},
        "machine_event": {"rules": [{"event_type": "TP_EXECUTED", "actions": ["MOVE_STOP_TO_BE"]}]},
    }


# -- resolve_allowed_update_intents unit tests ----------------------------------

def test_resolve_allowed_intents_returns_none_when_no_management_rules() -> None:
    assert resolve_allowed_update_intents(None) is None


def test_resolve_allowed_intents_returns_none_when_empty_auto_apply() -> None:
    mgmt = _mgmt_trader_hint(auto_apply=[], log_only=["U_TP_HIT"])
    assert resolve_allowed_update_intents(mgmt) is None  # allow all (backward compat)


def test_resolve_allowed_intents_returns_frozenset_when_non_empty() -> None:
    mgmt = _mgmt_trader_hint(auto_apply=["U_MOVE_STOP", "U_CLOSE_FULL"])
    allowed = resolve_allowed_update_intents(mgmt)
    assert allowed == frozenset({"U_MOVE_STOP", "U_CLOSE_FULL"})


def test_resolve_allowed_intents_machine_event_returns_empty_set() -> None:
    """machine_event mode blocks Telegram UPDATE auto-apply."""
    mgmt = _mgmt_machine_event()
    assert resolve_allowed_update_intents(mgmt) == frozenset()


def test_resolve_allowed_intents_hybrid_mode_uses_auto_apply() -> None:
    mgmt = {
        "mode": "hybrid",
        "trader_hint": {"auto_apply_intents": ["U_MOVE_STOP"], "log_only_intents": []},
        "machine_event": {"rules": []},
    }
    allowed = resolve_allowed_update_intents(mgmt)
    assert allowed == frozenset({"U_MOVE_STOP"})


def test_resolve_allowed_intents_absent_mode_defaults_to_hybrid() -> None:
    mgmt = {"trader_hint": {"auto_apply_intents": ["U_CANCEL_PENDING"], "log_only_intents": []}}
    allowed = resolve_allowed_update_intents(mgmt)
    assert allowed == frozenset({"U_CANCEL_PENDING"})


# -- is_machine_event_mode unit tests ------------------------------------------

def test_is_machine_event_mode_true() -> None:
    assert is_machine_event_mode(_mgmt_machine_event()) is True


def test_is_machine_event_mode_false_for_trader_hint() -> None:
    assert is_machine_event_mode(_mgmt_trader_hint(["U_MOVE_STOP"])) is False


def test_is_machine_event_mode_false_when_none() -> None:
    assert is_machine_event_mode(None) is False


# -- machine_event sentinel is explicit ---------------------------------------

def test_machine_event_rules_not_supported_sentinel_is_false() -> None:
    """Verify machine_event is not marked as unsupported anymore."""
    assert MACHINE_EVENT_RULES_NOT_SUPPORTED is False


# -- allowed_update_directives filters correctly --------------------------------

def test_allowed_directives_all_pass_when_auto_apply_empty(tmp_path: Path) -> None:
    """When auto_apply_intents is empty, all eligible directives pass through."""
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path, parse_result_id=1)
    _insert_signal(db_path, attempt_key="atk_filter_off", status="ACTIVE")
    mgmt = _mgmt_trader_hint(auto_apply=[], log_only=["U_TP_HIT"])
    op_id = _insert_operational_signal(db_path, parse_result_id=1, attempt_key="atk_filter_off",
                                       management_rules_json=json.dumps(mgmt))
    _insert_update_parse_result(db_path, parse_result_id=2, intents=["U_CLOSE_FULL"])
    _insert_targeted_update(db_path, parse_result_id=2, target_op_signal_id=op_id)

    context = load_context_by_attempt_key("atk_filter_off", db_path)
    assert context is not None
    assert len(context.update_directives) == 1
    assert len(context.allowed_update_directives) == 1  # passes (no filter)
    assert context.close_full_requested is True


def test_allowed_directives_filters_out_not_in_auto_apply(tmp_path: Path) -> None:
    """U_CLOSE_FULL is blocked when not in auto_apply_intents."""
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path, parse_result_id=1)
    _insert_signal(db_path, attempt_key="atk_filter_strict", status="ACTIVE")
    # Only U_MOVE_STOP is auto-applied; U_CLOSE_FULL is NOT
    mgmt = _mgmt_trader_hint(auto_apply=["U_MOVE_STOP"], log_only=["U_TP_HIT"])
    op_id = _insert_operational_signal(db_path, parse_result_id=1, attempt_key="atk_filter_strict",
                                       management_rules_json=json.dumps(mgmt))
    _insert_update_parse_result(db_path, parse_result_id=2, intents=["U_CLOSE_FULL"])
    _insert_targeted_update(db_path, parse_result_id=2, target_op_signal_id=op_id)

    context = load_context_by_attempt_key("atk_filter_strict", db_path)
    assert context is not None
    assert len(context.update_directives) == 1       # raw: U_CLOSE_FULL present
    assert len(context.allowed_update_directives) == 0  # filtered: not in auto_apply
    assert context.close_full_requested is False     # strategy does NOT exit


def test_allowed_directives_only_listed_intents_pass(tmp_path: Path) -> None:
    """Only intents listed in auto_apply_intents are kept."""
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path, parse_result_id=1)
    _insert_signal(db_path, attempt_key="atk_multi_intent", status="ACTIVE")
    mgmt = _mgmt_trader_hint(auto_apply=["U_MOVE_STOP", "U_CANCEL_PENDING"], log_only=[])
    op_id = _insert_operational_signal(db_path, parse_result_id=1, attempt_key="atk_multi_intent",
                                       management_rules_json=json.dumps(mgmt))

    # U_CLOSE_FULL → should be filtered
    _insert_update_parse_result(db_path, parse_result_id=2, intents=["U_CLOSE_FULL"])
    _insert_targeted_update(db_path, parse_result_id=2, target_op_signal_id=op_id)
    # U_CANCEL_PENDING → should pass
    _insert_update_parse_result(db_path, parse_result_id=3, intents=["U_CANCEL_PENDING"])
    _insert_targeted_update(db_path, parse_result_id=3, target_op_signal_id=op_id)

    context = load_context_by_attempt_key("atk_multi_intent", db_path)
    assert context is not None
    assert len(context.update_directives) == 2                   # raw: both present
    assert len(context.allowed_update_directives) == 1           # only U_CANCEL_PENDING
    assert context.allowed_update_directives[0].intent == "U_CANCEL_PENDING"
    assert context.cancel_pending_requested is True
    assert context.close_full_requested is False


def test_allowed_directives_machine_event_mode_blocks_telegram_updates(tmp_path: Path) -> None:
    """machine_event mode delegates management to callbacks, so Telegram UPDATEs are blocked."""
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path, parse_result_id=1)
    _insert_signal(db_path, attempt_key="atk_machine_evt", status="ACTIVE")
    mgmt = _mgmt_machine_event()
    op_id = _insert_operational_signal(db_path, parse_result_id=1, attempt_key="atk_machine_evt",
                                       management_rules_json=json.dumps(mgmt))
    _insert_update_parse_result(db_path, parse_result_id=2, intents=["U_CLOSE_FULL"])
    _insert_targeted_update(db_path, parse_result_id=2, target_op_signal_id=op_id)

    context = load_context_by_attempt_key("atk_machine_evt", db_path)
    assert context is not None
    assert len(context.update_directives) == 1
    assert len(context.allowed_update_directives) == 0
    assert context.close_full_requested is False


# -- strategy uses filtered directives in populate_exit_trend -------------------

def test_populate_exit_trend_blocked_by_auto_apply_filter(tmp_path: Path) -> None:
    """Strategy does NOT exit when U_CLOSE_FULL is not in auto_apply_intents."""
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path, parse_result_id=1)
    _insert_signal(db_path, attempt_key="atk_exit_blocked", status="ACTIVE")
    mgmt = _mgmt_trader_hint(auto_apply=["U_MOVE_STOP"], log_only=["U_TP_HIT"])
    op_id = _insert_operational_signal(db_path, parse_result_id=1, attempt_key="atk_exit_blocked",
                                       management_rules_json=json.dumps(mgmt))
    _insert_update_parse_result(db_path, parse_result_id=2, intents=["U_CLOSE_FULL"])
    _insert_targeted_update(db_path, parse_result_id=2, target_op_signal_id=op_id)

    strategy = SignalBridgeStrategy(config={})
    strategy.bot_db_path = db_path
    dataframe = _MiniDataFrame(rows=2)

    updated = strategy.populate_exit_trend(dataframe, {"pair": "BTC/USDT:USDT"})

    # Exit should NOT be triggered because U_CLOSE_FULL is not in auto_apply_intents
    assert updated["exit_long"] == [0, 0]


def test_populate_exit_trend_allowed_when_in_auto_apply(tmp_path: Path) -> None:
    """Strategy exits when U_CLOSE_FULL is explicitly in auto_apply_intents."""
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path, parse_result_id=1)
    _insert_signal(db_path, attempt_key="atk_exit_allowed", status="ACTIVE")
    mgmt = _mgmt_trader_hint(auto_apply=["U_CLOSE_FULL", "U_MOVE_STOP"], log_only=[])
    op_id = _insert_operational_signal(db_path, parse_result_id=1, attempt_key="atk_exit_allowed",
                                       management_rules_json=json.dumps(mgmt))
    _insert_update_parse_result(db_path, parse_result_id=2, intents=["U_CLOSE_FULL"])
    _insert_targeted_update(db_path, parse_result_id=2, target_op_signal_id=op_id)

    strategy = SignalBridgeStrategy(config={})
    strategy.bot_db_path = db_path
    dataframe = _MiniDataFrame(rows=2)

    updated = strategy.populate_exit_trend(dataframe, {"pair": "BTC/USDT:USDT"})

    assert updated["exit_long"] == [0, 1]
    assert updated["exit_tag"] == [None, "atk_exit_allowed"]


# ─── Alignment contract — all four pillars in one place ────────────────────
#
# These tests prove the final alignment contract between operation_rules and
# the freqtrade runtime. Each test is a single-concern proof; together they
# document what is supported, what is not, and which guarantees hold at runtime.
#
# Pillar 1 (Step A+B): entry price comes from signal E1 (first_in_plan).
# Pillar 2 (Step C):   fill outside tolerance is hard-rejected.
# Pillar 3 (Step D):   auto_apply_intents filter is the runtime source-of-truth.
# Pillar 4 (Step E):   price_corrections is NOT active at runtime.
# Note    (Step E):    price_sanity is a parse-time gate, not a runtime gate.
# ---------------------------------------------------------------------------


def test_alignment_pillar1_entry_price_sourced_from_e1(tmp_path: Path) -> None:
    """Entry price in custom_entry_price is E1 from signal, not proposed_rate."""
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path, parse_result_id=1)
    _insert_signal_with_entry(
        db_path,
        attempt_key="atk_align_p1",
        entry_json=json.dumps([{"price": 66100.0, "type": "LIMIT"}]),
        status="PENDING",
    )
    _insert_op_signal_with_split(db_path, parse_result_id=1, attempt_key="atk_align_p1")

    strategy = SignalBridgeStrategy(config={})
    strategy.bot_db_path = db_path

    price = strategy.custom_entry_price(
        pair="BTC/USDT:USDT",
        trade=None,
        current_time=None,
        proposed_rate=66500.0,  # freqtrade proposal: different from E1
        entry_tag="atk_align_p1",
        side="long",
    )
    # Must use E1 (66100), NOT the proposed_rate (66500)
    assert price == 66100.0


def test_alignment_pillar2_limit_entry_uses_e1_not_runtime_rate(tmp_path: Path) -> None:
    """For LIMIT entries, confirm_trade_entry uses E1 as the effective order price."""
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path, parse_result_id=1)
    _insert_signal_with_entry(
        db_path,
        attempt_key="atk_align_p2",
        entry_json=json.dumps([{"price": 66100.0, "type": "LIMIT"}]),
        status="PENDING",
    )
    # management_rules with entry_policy enabled, tight tolerance (0.1%)
    mgmt = {"entry_policy": {"enabled": True, "max_slippage_pct": 0.001, "zone_tolerance_pct": 0.001}}
    _insert_op_signal_with_split(
        db_path,
        parse_result_id=1,
        attempt_key="atk_align_p2",
        management_rules_json=json.dumps(mgmt),
    )

    strategy = SignalBridgeStrategy(config={})
    strategy.bot_db_path = db_path

    # Proposed runtime rate is far from E1, but the pending LIMIT order is still anchored to E1.
    accepted = strategy.confirm_trade_entry(
        pair="BTC/USDT:USDT",
        order_type="limit",
        amount=1.0,
        rate=66500.0,
        time_in_force="GTC",
        current_time=None,
        entry_tag="atk_align_p2",
        side="long",
    )
    assert accepted is True


def test_alignment_pillar2_fill_within_tolerance_accepted(tmp_path: Path) -> None:
    """confirm_trade_entry returns True when fill rate is within tolerance."""
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path, parse_result_id=1)
    _insert_signal_with_entry(
        db_path,
        attempt_key="atk_align_p2b",
        entry_json=json.dumps([{"price": 66100.0, "type": "LIMIT"}]),
        status="PENDING",
    )
    mgmt = {"entry_policy": {"enabled": True, "max_slippage_pct": 0.005, "zone_tolerance_pct": 0.002}}
    _insert_op_signal_with_split(
        db_path,
        parse_result_id=1,
        attempt_key="atk_align_p2b",
        management_rules_json=json.dumps(mgmt),
    )

    strategy = SignalBridgeStrategy(config={})
    strategy.bot_db_path = db_path

    # Fill at 66120 — 0.03% deviation, within 0.5% tolerance → accept
    accepted = strategy.confirm_trade_entry(
        pair="BTC/USDT:USDT",
        order_type="limit",
        amount=1.0,
        rate=66120.0,
        time_in_force="GTC",
        current_time=None,
        entry_tag="atk_align_p2b",
        side="long",
    )
    assert accepted is True


def test_alignment_pillar3_update_blocked_outside_auto_apply(tmp_path: Path) -> None:
    """populate_exit_trend does NOT exit when intent is not in auto_apply_intents."""
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path, parse_result_id=1)
    _insert_signal(db_path, attempt_key="atk_align_p3", status="ACTIVE")
    mgmt = _mgmt_trader_hint(auto_apply=["U_MOVE_STOP"], log_only=[])  # U_CLOSE_FULL NOT listed
    op_id = _insert_operational_signal(
        db_path, parse_result_id=1, attempt_key="atk_align_p3",
        management_rules_json=json.dumps(mgmt),
    )
    _insert_update_parse_result(db_path, parse_result_id=2, intents=["U_CLOSE_FULL"])
    _insert_targeted_update(db_path, parse_result_id=2, target_op_signal_id=op_id)

    strategy = SignalBridgeStrategy(config={})
    strategy.bot_db_path = db_path
    updated = strategy.populate_exit_trend(_MiniDataFrame(rows=2), {"pair": "BTC/USDT:USDT"})
    assert updated["exit_long"] == [0, 0]  # blocked by filter


def test_alignment_pillar4_price_corrections_not_supported_sentinel() -> None:
    """PRICE_CORRECTIONS_NOT_SUPPORTED sentinel is True: feature is declared but not active."""
    assert PRICE_CORRECTIONS_NOT_SUPPORTED is True


def test_alignment_pillar4_price_corrections_json_is_null_in_router(tmp_path: Path) -> None:
    """Router always writes price_corrections_json=None (feature not implemented)."""
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path, parse_result_id=1)
    _insert_signal(db_path, attempt_key="atk_align_p4", status="PENDING")
    _insert_operational_signal(db_path, parse_result_id=1, attempt_key="atk_align_p4")

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT price_corrections_json FROM operational_signals WHERE attempt_key = ?",
            ("atk_align_p4",),
        ).fetchone()

    # Column exists and is NULL (not a non-null value)
    assert row is not None
    assert row[0] is None


def test_alignment_note_price_sanity_is_not_in_normalizer() -> None:
    """price_sanity has no entry point in the normalizer (it is parse-time only).

    This test documents the architectural boundary: price_sanity runs in
    src/operation_rules/engine.py Gate 9 before signal creation. The runtime
    fill gate is EntryPricePolicy (this module). They are independent.
    """
    import src.execution.freqtrade_normalizer as norm_module

    assert not hasattr(norm_module, "check_price_sanity")
    assert not hasattr(norm_module, "apply_price_sanity")
    assert hasattr(norm_module, "check_entry_rate")        # runtime gate: present
    assert hasattr(norm_module, "EntryPricePolicy")        # runtime gate: present
    assert hasattr(norm_module, "PRICE_CORRECTIONS_NOT_SUPPORTED")   # explicit non-support
    assert hasattr(norm_module, "MACHINE_EVENT_RULES_NOT_SUPPORTED")  # explicit non-support


# -----------------------------------------------------------------------
# Bridge plotting tests
# -----------------------------------------------------------------------


def test_plot_config_declares_bridge_columns() -> None:
    """plot_config exposes bridge context and event series for FreqUI."""
    config = SignalBridgeStrategy.plot_config
    assert "main_plot" in config
    assert "bridge_sl" in config["main_plot"]
    assert "bridge_tp1" in config["main_plot"]
    assert "bridge_entry_price" in config["main_plot"]
    assert "Bridge Events" in config["subplots"]
    events_subplot = config["subplots"]["Bridge Events"]
    assert "bridge_event_entry" in events_subplot
    assert "bridge_event_sl_hit" in events_subplot
    assert "bridge_event_tp_hit" in events_subplot


def test_populate_indicators_injects_bridge_columns_with_no_active_trade(tmp_path: Path) -> None:
    """When no active trade exists, bridge columns are present but empty (NaN/0)."""
    import math

    db_path = _make_db(tmp_path)
    strategy = SignalBridgeStrategy(config={})
    strategy.bot_db_path = db_path
    dataframe = _MiniDataFrame(rows=3)

    updated = strategy.populate_indicators(dataframe, {"pair": "BTC/USDT:USDT"})

    # Context columns should exist as NaN
    assert "bridge_sl" in updated._data
    for val in updated["bridge_sl"]:
        assert val is None or (isinstance(val, float) and math.isnan(val))

    # Event columns should be 0
    assert updated["bridge_event_entry"] == [0, 0, 0]


def test_populate_indicators_fills_sl_tp_from_active_context(tmp_path: Path) -> None:
    """Active trade context injects SL and TP levels into the dataframe."""
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path)
    _insert_signal(
        db_path,
        attempt_key="atk_plot",
        status="ACTIVE",
        sl=57000.0,
        tp_json=json.dumps([62000.0, 64000.0, 66000.0]),
    )
    _insert_operational_signal(db_path, parse_result_id=1, attempt_key="atk_plot")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO trades
               (env, attempt_key, trader_id, symbol, side, execution_mode, state, created_at, updated_at)
               VALUES ('T', 'atk_plot', 'trader_3', 'BTCUSDT', 'BUY', 'FREQTRADE', 'OPEN', '2026-01-01', '2026-01-01')"""
        )
        conn.commit()

    strategy = SignalBridgeStrategy(config={})
    strategy.bot_db_path = db_path
    dataframe = _MiniDataFrame(rows=2)

    updated = strategy.populate_indicators(dataframe, {"pair": "BTC/USDT:USDT"})

    assert updated["bridge_sl"] == [57000.0, 57000.0]
    assert updated["bridge_tp1"] == [62000.0, 62000.0]
    assert updated["bridge_tp2"] == [64000.0, 64000.0]
    assert updated["bridge_tp3"] == [66000.0, 66000.0]


def test_populate_indicators_fills_entry_price_from_active_context(tmp_path: Path) -> None:
    """Active trade context injects entry price into the dataframe."""
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path)
    _insert_signal(db_path, attempt_key="atk_eprice", status="ACTIVE", sl=57000.0)
    _insert_operational_signal(db_path, parse_result_id=1, attempt_key="atk_eprice")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO trades
               (env, attempt_key, trader_id, symbol, side, execution_mode, state, created_at, updated_at)
               VALUES ('T', 'atk_eprice', 'trader_3', 'BTCUSDT', 'BUY', 'FREQTRADE', 'OPEN', '2026-01-01', '2026-01-01')"""
        )
        conn.commit()

    strategy = SignalBridgeStrategy(config={})
    strategy.bot_db_path = db_path
    dataframe = _MiniDataFrame(rows=2)

    updated = strategy.populate_indicators(dataframe, {"pair": "BTC/USDT:USDT"})

    # entry_json was [{"price": 60000.0}] from _insert_signal fixture
    assert updated["bridge_entry_price"] == [60000.0, 60000.0]




def test_populate_indicators_ignores_closed_trade_context(tmp_path: Path) -> None:
    """Closed trades must not keep SL/TP lines visible on the pair chart."""
    import math

    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path)
    _insert_signal(
        db_path,
        attempt_key="atk_closed_plot",
        status="ACTIVE",
        sl=57000.0,
        tp_json=json.dumps([62000.0, 64000.0, 66000.0]),
    )
    _insert_operational_signal(db_path, parse_result_id=1, attempt_key="atk_closed_plot")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO trades
               (env, attempt_key, trader_id, symbol, side, execution_mode, state, created_at, updated_at)
               VALUES ('T', 'atk_closed_plot', 'trader_3', 'BTCUSDT', 'BUY', 'FREQTRADE', 'CLOSED', '2026-01-01', '2026-01-01')"""
        )
        conn.commit()

    strategy = SignalBridgeStrategy(config={})
    strategy.bot_db_path = db_path
    dataframe = _MiniDataFrame(rows=2)

    updated = strategy.populate_indicators(dataframe, {"pair": "BTC/USDT:USDT"})

    for val in updated["bridge_sl"]:
        assert val is None or (isinstance(val, float) and math.isnan(val))
    for val in updated["bridge_tp1"]:
        assert val is None or (isinstance(val, float) and math.isnan(val))
    for val in updated["bridge_entry_price"]:
        assert val is None or (isinstance(val, float) and math.isnan(val))


def test_pair_to_symbol_reverses_freqtrade_pair() -> None:
    assert SignalBridgeStrategy._pair_to_symbol("BTC/USDT:USDT") == "BTCUSDT"
    assert SignalBridgeStrategy._pair_to_symbol("ETH/USDT:USDT") == "ETHUSDT"
    assert SignalBridgeStrategy._pair_to_symbol("") is None
    assert SignalBridgeStrategy._pair_to_symbol(None) is None


def test_populate_indicators_marks_entry_filled_event(tmp_path: Path) -> None:
    """An ENTRY_FILLED event in the DB sets bridge_event_entry on the matching candle."""
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path)
    _insert_signal(db_path, attempt_key="atk_evt", status="ACTIVE", sl=57000.0)
    _insert_operational_signal(db_path, parse_result_id=1, attempt_key="atk_evt")

    # Insert an ENTRY_FILLED event
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO events
               (env, channel_id, telegram_msg_id, trader_id, trader_prefix,
                attempt_key, event_type, payload_json, confidence, created_at)
               VALUES ('T', '-100999', '1', 'trader_3', 'TRAD',
                       'atk_evt', 'ENTRY_FILLED', '{"fill_price": 60000}', 1.0,
                       '2026-01-01T12:00:00')""",
        )
        conn.commit()

    strategy = SignalBridgeStrategy(config={})
    strategy.bot_db_path = db_path

    # Use a real pandas DataFrame for event timestamp matching
    try:
        import pandas as pd
    except ImportError:
        pytest.skip("pandas not available")

    dates = pd.date_range("2026-01-01 11:59:00", periods=3, freq="1min")
    df = pd.DataFrame({"close": [60000.0] * 3, "date": dates})

    updated = strategy.populate_indicators(df, {"pair": "BTC/USDT:USDT"})

    # The event at 12:00:00 should match the candle at index 1 (12:00)
    assert updated["bridge_event_entry"].iloc[1] == 1
    # Other event columns should remain 0
    assert updated["bridge_event_sl_hit"].sum() == 0


def test_populate_indicators_no_db_path_still_adds_columns() -> None:
    """With no DB path, columns are still added (NaN/0) — no crash."""
    import math

    strategy = SignalBridgeStrategy(config={})
    strategy.bot_db_path = None
    dataframe = _MiniDataFrame(rows=2)

    updated = strategy.populate_indicators(dataframe, {"pair": "BTC/USDT:USDT"})

    assert "bridge_sl" in updated._data
    assert updated["bridge_event_entry"] == [0, 0]


def test_resolve_db_path_falls_back_to_repo_db() -> None:
    strategy = SignalBridgeStrategy(config={})
    strategy.bot_db_path = None

    resolved = strategy._resolve_db_path()

    assert resolved is not None
    assert resolved.replace('\\', '/').endswith('/db/tele_signal_bot.sqlite3')


def test_custom_entry_price_returns_limit_price_for_fourth_entry_tag(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path)
    _insert_signal(
        db_path,
        attempt_key="atk_fourth_leg_price",
        entry_json=json.dumps([
            {"type": "MARKET", "price": None},
            {"type": "LIMIT", "price": 59500.0},
            {"type": "LIMIT", "price": 59000.0},
            {"type": "LIMIT", "price": 58500.0},
        ]),
        status="ACTIVE",
    )
    _insert_operational_signal(
        db_path,
        parse_result_id=1,
        attempt_key="atk_fourth_leg_price",
        entry_split_json=json.dumps({"E1": 0.4, "E2": 0.25, "E3": 0.2, "E4": 0.15}),
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO trades
               (env, attempt_key, trader_id, symbol, side, execution_mode, state, meta_json, created_at, updated_at)
               VALUES ('T', 'atk_fourth_leg_price', 'trader_3', 'BTCUSDT', 'BUY', 'FREQTRADE', 'OPEN', ?, '2026-01-01', '2026-01-01')""",
            (json.dumps({"entry_legs": [
                {"entry_id": "E1", "sequence": 1, "order_type": "MARKET", "price": None, "split": 0.4, "status": "FILLED", "filled_at": "2026-01-01T00:00:00+00:00"},
                {"entry_id": "E2", "sequence": 2, "order_type": "LIMIT", "price": 59500.0, "split": 0.25, "status": "FILLED", "filled_at": "2026-01-01T00:05:00+00:00"},
                {"entry_id": "E3", "sequence": 3, "order_type": "LIMIT", "price": 59000.0, "split": 0.2, "status": "PENDING"},
                {"entry_id": "E4", "sequence": 4, "order_type": "LIMIT", "price": 58500.0, "split": 0.15, "status": "PENDING"},
            ]}),),
        )
        conn.commit()

    strategy = SignalBridgeStrategy(config={})
    strategy.bot_db_path = db_path

    price = strategy.custom_entry_price(
        pair="BTC/USDT:USDT",
        trade=None,
        current_time=None,
        proposed_rate=61000.0,
        entry_tag="atk_fourth_leg_price:ENTRY:3",
        side="long",
    )

    assert price == 58500.0


# ---------------------------------------------------------------------------
# Step 28 — entry legs runtime model
# ---------------------------------------------------------------------------


def test_entry_legs_single_market(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path)
    _insert_signal(
        db_path,
        attempt_key="atk_leg_market",
        entry_json=json.dumps([{"type": "MARKET", "price": None}]),
    )
    _insert_operational_signal(db_path, parse_result_id=1, attempt_key="atk_leg_market")

    ctx = load_context_by_attempt_key("atk_leg_market", db_path)

    assert ctx is not None
    assert len(ctx.entry_legs) == 1
    leg = ctx.entry_legs[0]
    assert leg.entry_id == "E1"
    assert leg.sequence == 1
    assert leg.order_type == "MARKET"
    assert leg.price is None
    assert leg.split == pytest.approx(1.0)
    assert ctx.first_entry_leg == leg
    assert ctx.market_entry_required is True
    assert ctx.limit_entry_required is False
    assert ctx.first_entry_order_type == "MARKET"


def test_entry_legs_single_limit(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path)
    _insert_signal(
        db_path,
        attempt_key="atk_leg_limit",
        entry_json=json.dumps([{"type": "LIMIT", "price": 60000.0}]),
    )
    _insert_operational_signal(db_path, parse_result_id=1, attempt_key="atk_leg_limit")

    ctx = load_context_by_attempt_key("atk_leg_limit", db_path)

    assert ctx is not None
    assert len(ctx.entry_legs) == 1
    leg = ctx.entry_legs[0]
    assert leg.entry_id == "E1"
    assert leg.order_type == "LIMIT"
    assert leg.price == pytest.approx(60000.0)
    assert leg.split == pytest.approx(1.0)
    assert ctx.market_entry_required is False
    assert ctx.limit_entry_required is True
    assert ctx.first_entry_order_type == "LIMIT"


def test_entry_legs_mixed_market_limit(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path)
    _insert_signal(
        db_path,
        attempt_key="atk_leg_mixed",
        entry_json=json.dumps([
            {"type": "MARKET", "price": None},
            {"type": "LIMIT", "price": 59500.0},
        ]),
    )
    _insert_operational_signal(db_path, parse_result_id=1, attempt_key="atk_leg_mixed")

    ctx = load_context_by_attempt_key("atk_leg_mixed", db_path)

    assert ctx is not None
    assert len(ctx.entry_legs) == 2
    e1, e2 = ctx.entry_legs
    assert e1.entry_id == "E1"
    assert e1.order_type == "MARKET"
    assert e1.price is None
    assert e1.split == pytest.approx(0.5)
    assert e2.entry_id == "E2"
    assert e2.order_type == "LIMIT"
    assert e2.price == pytest.approx(59500.0)
    assert e2.split == pytest.approx(0.5)
    assert ctx.market_entry_required is True
    assert ctx.first_entry_order_type == "MARKET"


def test_entry_legs_split_json_applied(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path)
    _insert_signal(
        db_path,
        attempt_key="atk_leg_split",
        entry_json=json.dumps([
            {"type": "MARKET", "price": None},
            {"type": "LIMIT", "price": 610.0},
            {"type": "LIMIT", "price": 605.0},
        ]),
    )
    _insert_operational_signal(
        db_path,
        parse_result_id=1,
        attempt_key="atk_leg_split",
        entry_split_json=json.dumps({"E1": 0.5, "E2": 0.3, "E3": 0.2}),
    )

    ctx = load_context_by_attempt_key("atk_leg_split", db_path)

    assert ctx is not None
    assert len(ctx.entry_legs) == 3
    e1, e2, e3 = ctx.entry_legs
    assert e1.split == pytest.approx(0.5)
    assert e2.split == pytest.approx(0.3)
    assert e3.split == pytest.approx(0.2)
    assert e1.order_type == "MARKET"
    assert e2.order_type == "LIMIT"
    assert e3.order_type == "LIMIT"


def test_runtime_entry_leg_helpers_read_serialized_mixed_plan(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path)
    _insert_signal(
        db_path,
        attempt_key="atk_leg_runtime",
        status="ACTIVE",
        entry_json=json.dumps([
            {"type": "MARKET", "price": None},
            {"type": "LIMIT", "price": 59500.0},
            {"type": "LIMIT", "price": 59000.0},
        ]),
    )
    _insert_operational_signal(
        db_path,
        parse_result_id=1,
        attempt_key="atk_leg_runtime",
        entry_split_json=json.dumps({"E1": 0.5, "E2": 0.3, "E3": 0.2}),
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO trades
               (env, attempt_key, trader_id, symbol, side, execution_mode, state,
                entry_zone_policy, non_chase_policy, opened_at, meta_json, created_at, updated_at)
               VALUES ('T', 'atk_leg_runtime', 'trader_3', 'BTCUSDT', 'BUY', 'FREQTRADE', 'OPEN',
                       'Z1', 'NI3', '2026-01-01', ?, '2026-01-01', '2026-01-01')""",
            (
                json.dumps(
                    {
                        "entry_legs": [
                            {
                                "entry_id": "E1",
                                "sequence": 1,
                                "order_type": "MARKET",
                                "price": None,
                                "split": 0.5,
                                "status": "FILLED",
                                "filled_at": "2026-01-01T00:00:00+00:00",
                            },
                            {
                                "entry_id": "E2",
                                "sequence": 2,
                                "order_type": "LIMIT",
                                "price": 59500.0,
                                "split": 0.3,
                                "status": "PENDING",
                            },
                            {
                                "entry_id": "E3",
                                "sequence": 3,
                                "order_type": "LIMIT",
                                "price": 59000.0,
                                "split": 0.2,
                                "status": "PENDING",
                            },
                        ]
                    }
                ),
            ),
        )
        conn.commit()

    ctx = load_context_by_attempt_key("atk_leg_runtime", db_path)

    assert ctx is not None
    assert [leg.status for leg in ctx.runtime_entry_legs] == ["FILLED", "PENDING", "PENDING"]
    assert ctx.next_pending_entry_leg is not None
    assert ctx.next_pending_entry_leg.entry_id == "E2"
    assert ctx.next_pending_entry_leg.order_type == "LIMIT"
    assert [leg.entry_id for leg in ctx.pending_limit_entry_legs] == ["E2", "E3"]


def test_runtime_entry_leg_helpers_fallback_for_single_limit_plan(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path)
    _insert_signal(
        db_path,
        attempt_key="atk_leg_limit_runtime",
        entry_json=json.dumps([{"type": "LIMIT", "price": 60000.0}]),
    )
    _insert_operational_signal(db_path, parse_result_id=1, attempt_key="atk_leg_limit_runtime")

    ctx = load_context_by_attempt_key("atk_leg_limit_runtime", db_path)

    assert ctx is not None
    assert len(ctx.runtime_entry_legs) == 1
    assert ctx.runtime_entry_legs[0].entry_id == "E1"
    assert ctx.runtime_entry_legs[0].status == "PENDING"
    assert ctx.next_pending_entry_leg is not None
    assert ctx.next_pending_entry_leg.entry_id == "E1"
    assert [leg.entry_id for leg in ctx.pending_limit_entry_legs] == ["E1"]


def test_runtime_entry_leg_helpers_fallback_for_single_market_plan(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path)
    _insert_signal(
        db_path,
        attempt_key="atk_leg_market_runtime",
        entry_json=json.dumps([{"type": "MARKET", "price": None}]),
    )
    _insert_operational_signal(db_path, parse_result_id=1, attempt_key="atk_leg_market_runtime")

    ctx = load_context_by_attempt_key("atk_leg_market_runtime", db_path)

    assert ctx is not None
    assert len(ctx.runtime_entry_legs) == 1
    assert ctx.runtime_entry_legs[0].entry_id == "E1"
    assert ctx.runtime_entry_legs[0].status == "PENDING"
    assert ctx.next_pending_entry_leg is not None
    assert ctx.next_pending_entry_leg.entry_id == "E1"
    assert ctx.pending_limit_entry_legs == ()


# ---------------------------------------------------------------------------
# Step 29 — populate_entry_trend: LIMIT-only strategy path
# ---------------------------------------------------------------------------


def test_populate_entry_trend_emits_entry_for_limit_signal(tmp_path: Path) -> None:
    """populate_entry_trend emits enter_long when first entry leg is LIMIT."""
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path)
    _insert_signal_with_entry(
        db_path,
        attempt_key="atk_pet_limit",
        entry_json=json.dumps([{"price": 60000.0, "type": "LIMIT"}]),
    )
    _insert_op_signal_with_split(db_path, parse_result_id=1, attempt_key="atk_pet_limit")

    strategy = SignalBridgeStrategy(config={})
    strategy.bot_db_path = db_path
    dataframe = _MiniDataFrame(rows=2)

    updated = strategy.populate_entry_trend(dataframe, {"pair": "BTC/USDT:USDT"})

    assert updated["enter_long"] == [0, 1]
    assert updated["enter_short"] == [0, 0]
    assert updated["enter_tag"] == [None, "atk_pet_limit:ENTRY:0"]


def test_populate_entry_trend_skips_market_signal(tmp_path: Path) -> None:
    """populate_entry_trend skips MARKET-first signals because the dispatcher owns E1."""
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path)
    _insert_signal_with_entry(
        db_path,
        attempt_key="atk_pet_market",
        entry_json=json.dumps([{"price": None, "type": "MARKET"}]),
    )
    _insert_op_signal_with_split(db_path, parse_result_id=1, attempt_key="atk_pet_market")

    strategy = SignalBridgeStrategy(config={})
    strategy.bot_db_path = db_path
    dataframe = _MiniDataFrame(rows=2)

    updated = strategy.populate_entry_trend(dataframe, {"pair": "BTC/USDT:USDT"})

    assert updated["enter_long"] == [0, 0]
    assert updated["enter_short"] == [0, 0]
    assert updated["enter_tag"] == [None, None]
