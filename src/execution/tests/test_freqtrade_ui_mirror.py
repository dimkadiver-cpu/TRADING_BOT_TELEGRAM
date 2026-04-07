from __future__ import annotations

import sqlite3
from pathlib import Path

from src.execution.freqtrade_ui_mirror import mirror_entry_fill, mirror_position_update, mirror_trade_stoploss


def _prepare_freqtrade_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE trades (
                id INTEGER NOT NULL PRIMARY KEY,
                exchange VARCHAR(25) NOT NULL,
                pair VARCHAR(25) NOT NULL,
                base_currency VARCHAR(25),
                stake_currency VARCHAR(25),
                is_open BOOLEAN NOT NULL,
                fee_open FLOAT NOT NULL,
                fee_open_cost FLOAT,
                fee_open_currency VARCHAR(25),
                fee_close FLOAT NOT NULL,
                fee_close_cost FLOAT,
                fee_close_currency VARCHAR(25),
                open_rate FLOAT NOT NULL,
                open_rate_requested FLOAT,
                open_trade_value FLOAT,
                close_rate FLOAT,
                close_rate_requested FLOAT,
                realized_profit FLOAT,
                close_profit FLOAT,
                close_profit_abs FLOAT,
                stake_amount FLOAT NOT NULL,
                max_stake_amount FLOAT,
                amount FLOAT NOT NULL,
                amount_requested FLOAT,
                open_date DATETIME NOT NULL,
                close_date DATETIME,
                stop_loss FLOAT,
                stop_loss_pct FLOAT,
                initial_stop_loss FLOAT,
                initial_stop_loss_pct FLOAT,
                is_stop_loss_trailing BOOLEAN NOT NULL,
                max_rate FLOAT,
                min_rate FLOAT,
                exit_reason VARCHAR(255),
                exit_order_status VARCHAR(100),
                strategy VARCHAR(100),
                enter_tag VARCHAR(255),
                timeframe INTEGER,
                trading_mode VARCHAR(7),
                amount_precision FLOAT,
                price_precision FLOAT,
                precision_mode INTEGER,
                precision_mode_price INTEGER,
                contract_size FLOAT,
                leverage FLOAT,
                is_short BOOLEAN NOT NULL,
                liquidation_price FLOAT,
                interest_rate FLOAT NOT NULL,
                funding_fees FLOAT,
                funding_fee_running FLOAT,
                record_version INTEGER NOT NULL
            );

            CREATE TABLE orders (
                id INTEGER NOT NULL PRIMARY KEY,
                ft_trade_id INTEGER NOT NULL,
                ft_order_side VARCHAR(25) NOT NULL,
                ft_pair VARCHAR(25) NOT NULL,
                ft_is_open BOOLEAN NOT NULL,
                ft_amount FLOAT NOT NULL,
                ft_price FLOAT NOT NULL,
                ft_cancel_reason VARCHAR(255),
                order_id VARCHAR(255) NOT NULL,
                status VARCHAR(255),
                symbol VARCHAR(25),
                order_type VARCHAR(50),
                side VARCHAR(25),
                price FLOAT,
                average FLOAT,
                amount FLOAT,
                filled FLOAT,
                remaining FLOAT,
                cost FLOAT,
                stop_price FLOAT,
                order_date DATETIME,
                order_filled_date DATETIME,
                order_update_date DATETIME,
                funding_fee FLOAT,
                ft_fee_base FLOAT,
                ft_order_tag VARCHAR(255),
                CONSTRAINT _order_pair_order_id UNIQUE (ft_pair, order_id),
                FOREIGN KEY(ft_trade_id) REFERENCES trades (id)
            );
            """
        )
        conn.commit()


def _prepare_bot_db_with_signal(path: Path, *, attempt_key: str, sl: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS signals (
                attempt_key TEXT PRIMARY KEY,
                sl REAL
            );
            """
        )
        conn.execute(
            "INSERT INTO signals(attempt_key, sl) VALUES (?, ?)",
            (attempt_key, float(sl)),
        )
        conn.commit()


def _insert_freqtrade_trade(path: Path, *, enter_tag: str, pair: str, open_rate: float, amount: float, is_short: bool) -> int:
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            INSERT INTO trades(
                exchange, pair, base_currency, stake_currency, is_open,
                fee_open, fee_close, open_rate, open_rate_requested, open_trade_value,
                stake_amount, max_stake_amount, amount, amount_requested, open_date,
                is_stop_loss_trailing, strategy, enter_tag, timeframe, trading_mode,
                leverage, is_short, interest_rate, record_version, max_rate, min_rate
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "bybit",
                pair,
                "BTC",
                "USDT",
                1,
                0.0,
                0.0,
                float(open_rate),
                float(open_rate),
                float(open_rate) * float(amount),
                float(open_rate) * float(amount),
                float(open_rate) * float(amount),
                float(amount),
                float(amount),
                "2026-04-05T21:00:00+00:00",
                0,
                "SignalBridgeStrategy",
                enter_tag,
                1,
                "FUTURES",
                1.0,
                1 if is_short else 0,
                0.0,
                2,
                float(open_rate),
                float(open_rate),
            ),
        )
        row = conn.execute("SELECT last_insert_rowid()").fetchone()
        conn.commit()
    return int(row[0]) if row else 0


def test_mirror_entry_fill_inserts_trade_and_order(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "tradesv3.dryrun.sqlite"
    _prepare_freqtrade_db(db_path)
    monkeypatch.setenv("TELESIGNALBOT_FREQTRADE_TRADES_DB_PATH", str(db_path))

    mirror_entry_fill(
        attempt_key="atk_market_1",
        symbol="BTCUSDT",
        side="BUY",
        qty=0.2,
        fill_price=65000.0,
        opened_at="2026-04-05T21:00:00+00:00",
        exchange_order_id="ex-1",
        client_order_id="cid-1",
    )

    with sqlite3.connect(db_path) as conn:
        trade = conn.execute(
            "SELECT pair, is_open, amount, enter_tag, strategy, trading_mode FROM trades WHERE enter_tag = ?",
            ("atk_market_1",),
        ).fetchone()
        assert trade is not None
        assert trade[0] == "BTC/USDT:USDT"
        assert int(trade[1]) == 1
        assert float(trade[2]) == 0.2
        assert trade[3] == "atk_market_1"
        assert trade[4] == "SignalBridgeStrategy"
        assert trade[5] == "FUTURES"

        order = conn.execute(
            "SELECT ft_order_side, ft_pair, ft_is_open, order_id, status, ft_order_tag, order_type FROM orders"
        ).fetchone()
        assert order is not None
        assert order[0] == "buy"
        assert order[1] == "BTC/USDT:USDT"
        assert int(order[2]) == 0
        assert order[3] == "ex-1"
        assert order[4] == "closed"
        assert order[5] == "atk_market_1"
        assert order[6] == "market"


def test_mirror_entry_fill_limit_order_type_preserved(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "tradesv3.dryrun.sqlite"
    _prepare_freqtrade_db(db_path)
    monkeypatch.setenv("TELESIGNALBOT_FREQTRADE_TRADES_DB_PATH", str(db_path))

    mirror_entry_fill(
        attempt_key="atk_limit_1",
        symbol="ETHUSDT",
        side="BUY",
        qty=1.0,
        fill_price=3000.0,
        opened_at="2026-04-06T10:00:00+00:00",
        exchange_order_id="ex-limit-1",
        order_type="LIMIT",
    )

    with sqlite3.connect(db_path) as conn:
        order = conn.execute(
            "SELECT order_type FROM orders WHERE ft_order_tag = ?",
            ("atk_limit_1",),
        ).fetchone()
    assert order is not None
    assert order[0] == "limit"


def test_mirror_entry_fill_uses_bot_signal_stoploss_for_ui_trade(tmp_path: Path, monkeypatch) -> None:
    ft_db_path = tmp_path / "tradesv3.dryrun.sqlite"
    bot_db_path = tmp_path / "tele_signal_bot.sqlite3"
    _prepare_freqtrade_db(ft_db_path)
    _prepare_bot_db_with_signal(bot_db_path, attempt_key="atk_sl_sync", sl=66150.0)
    monkeypatch.setenv("TELESIGNALBOT_FREQTRADE_TRADES_DB_PATH", str(ft_db_path))
    monkeypatch.setenv("TELESIGNALBOT_DB_PATH", str(bot_db_path))

    mirror_entry_fill(
        attempt_key="atk_sl_sync",
        symbol="BTCUSDT",
        side="BUY",
        qty=0.003,
        fill_price=68075.0,
        opened_at="2026-04-06T10:02:31+00:00",
    )

    with sqlite3.connect(ft_db_path) as conn:
        trade = conn.execute(
            "SELECT stop_loss, initial_stop_loss, stop_loss_pct, initial_stop_loss_pct FROM trades WHERE enter_tag = ?",
            ("atk_sl_sync",),
        ).fetchone()
    assert trade is not None
    assert float(trade[0]) == 66150.0
    assert float(trade[1]) == 66150.0
    assert float(trade[2]) < 0.0
    assert float(trade[3]) < 0.0


def test_mirror_position_update_closes_trade(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "tradesv3.dryrun.sqlite"
    _prepare_freqtrade_db(db_path)
    monkeypatch.setenv("TELESIGNALBOT_FREQTRADE_TRADES_DB_PATH", str(db_path))

    mirror_entry_fill(
        attempt_key="atk_close_1",
        symbol="SOLUSDT",
        side="BUY",
        qty=1.0,
        fill_price=100.0,
        opened_at="2026-04-05T21:10:00+00:00",
    )
    mirror_position_update(
        attempt_key="atk_close_1",
        remaining_qty=0.0,
        exit_price=105.0,
        updated_at="2026-04-05T21:20:00+00:00",
        close_reason="POSITION_CLOSED",
    )

    with sqlite3.connect(db_path) as conn:
        trade = conn.execute(
            "SELECT is_open, amount, close_rate, close_date, exit_reason FROM trades WHERE enter_tag = ?",
            ("atk_close_1",),
        ).fetchone()
        assert trade is not None
        assert int(trade[0]) == 0
        assert float(trade[1]) == 0.0
        assert float(trade[2]) == 105.0
        assert trade[3] == "2026-04-05T21:20:00+00:00"
        assert trade[4] == "POSITION_CLOSED"


def test_mirror_trade_stoploss_updates_existing_short_trade(tmp_path: Path, monkeypatch) -> None:
    ft_db_path = tmp_path / "tradesv3.dryrun.sqlite"
    bot_db_path = tmp_path / "tele_signal_bot.sqlite3"
    _prepare_freqtrade_db(ft_db_path)
    _prepare_bot_db_with_signal(bot_db_path, attempt_key="atk_short_sl_sync", sl=70200.0)
    monkeypatch.setenv("TELESIGNALBOT_FREQTRADE_TRADES_DB_PATH", str(ft_db_path))
    monkeypatch.setenv("TELESIGNALBOT_DB_PATH", str(bot_db_path))

    mirror_entry_fill(
        attempt_key="atk_short_sl_sync",
        symbol="BTCUSDT",
        side="SHORT",
        qty=0.025,
        fill_price=69845.7,
        opened_at="2026-04-06T18:30:31+00:00",
    )

    with sqlite3.connect(ft_db_path) as conn:
        conn.execute(
            """
            UPDATE trades
            SET stop_loss = 138373.4,
                initial_stop_loss = 138992.9,
                stop_loss_pct = -0.99,
                initial_stop_loss_pct = -0.99
            WHERE enter_tag = 'atk_short_sl_sync'
            """
        )
        conn.commit()

    mirror_trade_stoploss(attempt_key="atk_short_sl_sync")

    with sqlite3.connect(ft_db_path) as conn:
        trade = conn.execute(
            "SELECT stop_loss, initial_stop_loss, stop_loss_pct, initial_stop_loss_pct FROM trades WHERE enter_tag = ?",
            ("atk_short_sl_sync",),
        ).fetchone()
    assert trade is not None
    assert float(trade[0]) == 70200.0
    assert float(trade[1]) == 138992.9
    assert float(trade[2]) < 0.0
    assert float(trade[2]) > -0.99
    assert float(trade[3]) == -0.99


def test_mirror_entry_fill_reuses_entry_alias_trade_tag(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "tradesv3.dryrun.sqlite"
    _prepare_freqtrade_db(db_path)
    monkeypatch.setenv("TELESIGNALBOT_FREQTRADE_TRADES_DB_PATH", str(db_path))
    original_trade_id = _insert_freqtrade_trade(
        db_path,
        enter_tag="atk_alias:ENTRY:0",
        pair="BTC/USDT:USDT",
        open_rate=65000.0,
        amount=0.1,
        is_short=False,
    )

    mirror_entry_fill(
        attempt_key="atk_alias",
        symbol="BTCUSDT",
        side="BUY",
        qty=0.2,
        fill_price=65100.0,
        opened_at="2026-04-05T21:00:00+00:00",
    )

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT id, enter_tag, amount FROM trades ORDER BY id").fetchall()

    assert rows == [(original_trade_id, "atk_alias", 0.2)]


def test_mirror_position_update_matches_entry_alias_trade_tag(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "tradesv3.dryrun.sqlite"
    _prepare_freqtrade_db(db_path)
    monkeypatch.setenv("TELESIGNALBOT_FREQTRADE_TRADES_DB_PATH", str(db_path))
    _insert_freqtrade_trade(
        db_path,
        enter_tag="atk_alias_close:ENTRY:0",
        pair="SOL/USDT:USDT",
        open_rate=100.0,
        amount=1.0,
        is_short=False,
    )

    mirror_position_update(
        attempt_key="atk_alias_close",
        remaining_qty=0.0,
        exit_price=105.0,
        updated_at="2026-04-05T21:20:00+00:00",
        close_reason="POSITION_CLOSED",
    )

    with sqlite3.connect(db_path) as conn:
        trade = conn.execute(
            "SELECT is_open, amount, close_rate, close_date, exit_reason FROM trades WHERE enter_tag = ?",
            ("atk_alias_close:ENTRY:0",),
        ).fetchone()
    assert trade is not None
    assert int(trade[0]) == 0
    assert float(trade[1]) == 0.0
    assert float(trade[2]) == 105.0
    assert trade[3] == "2026-04-05T21:20:00+00:00"
    assert trade[4] == "POSITION_CLOSED"


def test_mirror_trade_stoploss_matches_entry_alias_trade_tag(tmp_path: Path, monkeypatch) -> None:
    ft_db_path = tmp_path / "tradesv3.dryrun.sqlite"
    bot_db_path = tmp_path / "tele_signal_bot.sqlite3"
    _prepare_freqtrade_db(ft_db_path)
    _prepare_bot_db_with_signal(bot_db_path, attempt_key="atk_alias_sl", sl=70200.0)
    monkeypatch.setenv("TELESIGNALBOT_FREQTRADE_TRADES_DB_PATH", str(ft_db_path))
    monkeypatch.setenv("TELESIGNALBOT_DB_PATH", str(bot_db_path))
    _insert_freqtrade_trade(
        ft_db_path,
        enter_tag="atk_alias_sl:ENTRY:0",
        pair="BTC/USDT:USDT",
        open_rate=69845.7,
        amount=0.025,
        is_short=True,
    )

    mirror_trade_stoploss(attempt_key="atk_alias_sl")

    with sqlite3.connect(ft_db_path) as conn:
        trade = conn.execute(
            "SELECT stop_loss, initial_stop_loss, stop_loss_pct, initial_stop_loss_pct FROM trades WHERE enter_tag = ?",
            ("atk_alias_sl:ENTRY:0",),
        ).fetchone()
    assert trade is not None
    assert float(trade[0]) == 70200.0
    assert float(trade[1]) == 70200.0
    assert float(trade[2]) < 0.0
    assert float(trade[3]) < 0.0
