from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from src.core.migrations import apply_migrations
from src.execution.exchange_gateway import ExchangeGateway
from src.execution.market_entry_dispatcher import MarketEntryDispatcher


# ---------------------------------------------------------------------------
# Fake exchange backend
# ---------------------------------------------------------------------------


class _FakeBackend:
    """Minimal exchange backend that fills MARKET orders immediately."""

    def __init__(self, *, market_fill_price: float = 60000.0, ticker_price: float | None = None) -> None:
        self.market_fill_price = market_fill_price
        self.ticker_price = ticker_price if ticker_price is not None else market_fill_price
        self.created_orders: list[dict[str, Any]] = []

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
    ) -> dict[str, Any]:
        fill_price = self.market_fill_price if order_type == "MARKET" else price
        payload = {
            "id": f"ex-{client_order_id}",
            "client_order_id": client_order_id,
            "symbol": symbol,
            "side": side,
            "order_type": order_type,
            "qty": qty,
            "price": fill_price,
            "trigger_price": trigger_price,
            "reduce_only": reduce_only,
            "status": "FILLED" if order_type == "MARKET" else "OPEN",
        }
        self.created_orders.append(payload)
        return payload

    def cancel_order(self, *, exchange_order_id: str, symbol: str | None = None) -> bool:
        return True

    def fetch_open_orders(self, *, symbol: str) -> list[dict[str, Any]]:
        return []

    def fetch_position(self, *, symbol: str) -> None:
        return None

    def fetch_ticker(self, *, symbol: str) -> dict[str, Any]:
        return {"last": self.ticker_price, "symbol": symbol}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_db(tmp_path: Path) -> str:
    db_path = str(tmp_path / "dispatcher_test.sqlite3")
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
    entry_json: list[dict[str, Any]] | None = None,
    status: str = "PENDING",
) -> None:
    if entry_json is None:
        entry_json = [{"type": "MARKET", "price": None}]
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
                json.dumps(entry_json),
                json.dumps([{"price": 65000.0}]),
                status,
            ),
        )
        conn.commit()


def _insert_operational_signal(
    db_path: str,
    *,
    parse_result_id: int,
    attempt_key: str,
) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO operational_signals
               (parse_result_id, attempt_key, trader_id, message_type, is_blocked,
                position_size_usdt, leverage, management_rules_json, created_at)
               VALUES (?, ?, 'trader_3', 'NEW_SIGNAL', 0, 250.0, 3, ?, '2026-01-01')""",
            (parse_result_id, attempt_key, json.dumps({})),
        )
        conn.commit()


def _make_gateway(backend: _FakeBackend | None = None) -> ExchangeGateway:
    return ExchangeGateway(backend or _FakeBackend())


def _setup(
    tmp_path: Path,
    *,
    attempt_key: str,
    entry_json: list[dict[str, Any]] | None = None,
    parse_result_id: int = 1,
) -> str:
    db_path = _make_db(tmp_path)
    _insert_parse_result(db_path, parse_result_id=parse_result_id)
    _insert_signal(db_path, attempt_key=attempt_key, entry_json=entry_json)
    _insert_operational_signal(db_path, parse_result_id=parse_result_id, attempt_key=attempt_key)
    return db_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class _NoopManager:
    def sync_after_entry_fill(self, **kwargs: Any) -> dict[str, Any]:
        return {"ok": True, "synced": True, "kwargs": kwargs}


def test_dispatch_market_persists_exchange_manager_mode(tmp_path: Path) -> None:
    atk = "T_-100999_9_trader_3"
    db_path = _setup(tmp_path, attempt_key=atk)
    backend = _FakeBackend(market_fill_price=61000.0)
    dispatcher = MarketEntryDispatcher(
        db_path=db_path,
        gateway=_make_gateway(backend),
        protective_orders_mode="exchange_manager",
        order_manager=_NoopManager(),
    )

    results = dispatcher.dispatch_pending_market_entries()

    assert len(results) == 1
    assert results[0]["ok"] is True

    with sqlite3.connect(db_path) as conn:
        trade_mode = conn.execute(
            "SELECT protective_orders_mode FROM trades WHERE attempt_key = ?",
            (atk,),
        ).fetchone()[0]

    assert trade_mode == "exchange_manager"


def test_dispatch_single_market(tmp_path: Path) -> None:
    """A PENDING MARKET signal gets dispatched and the trade is recorded."""
    atk = "T_-100999_1_trader_3"
    db_path = _setup(tmp_path, attempt_key=atk)
    backend = _FakeBackend(market_fill_price=61000.0)
    dispatcher = MarketEntryDispatcher(db_path=db_path, gateway=_make_gateway(backend))

    results = dispatcher.dispatch_pending_market_entries()

    assert len(results) == 1
    r = results[0]
    assert r["attempt_key"] == atk
    assert r["ok"] is True
    assert r["action"] == "ENTRY_FILLED"
    assert r["error"] is None

    with sqlite3.connect(db_path) as conn:
        trade = conn.execute("SELECT state, meta_json FROM trades WHERE attempt_key = ?", (atk,)).fetchone()
        order = conn.execute(
            "SELECT status, order_type FROM orders WHERE attempt_key = ? AND purpose = 'ENTRY'",
            (atk,),
        ).fetchone()
        event_types = [
            row[0]
            for row in conn.execute(
                "SELECT event_type FROM events WHERE attempt_key = ? ORDER BY event_id",
                (atk,),
            ).fetchall()
        ]

    assert trade is not None
    assert trade[0] == "OPEN"
    trade_meta = json.loads(trade[1])
    assert trade_meta["entry_legs"][0]["entry_id"] == "E1"
    assert trade_meta["entry_legs"][0]["order_type"] == "MARKET"
    assert trade_meta["entry_legs"][0]["status"] == "FILLED"

    assert order is not None
    assert order[0] == "FILLED"
    assert order[1] == "MARKET"
    assert "MARKET_ENTRY_DISPATCHED" in event_types
    assert "ENTRY_FILLED" in event_types

    assert len(backend.created_orders) == 1
    assert backend.created_orders[0]["order_type"] == "MARKET"
    assert backend.created_orders[0]["reduce_only"] is False
    assert backend.created_orders[0]["symbol"] == "BTCUSDT"
    assert backend.created_orders[0]["qty"] == pytest.approx(250.0 / 61000.0)


def test_dispatch_idempotent(tmp_path: Path) -> None:
    """A second dispatcher pass becomes a no-op and does not emit duplicate dispatch audit."""
    atk = "T_-100999_2_trader_3"
    db_path = _setup(tmp_path, attempt_key=atk)
    backend = _FakeBackend()
    dispatcher = MarketEntryDispatcher(db_path=db_path, gateway=_make_gateway(backend))

    results1 = dispatcher.dispatch_pending_market_entries()
    assert results1[0]["ok"] is True

    results2 = dispatcher.dispatch_pending_market_entries()
    assert results2 == []
    assert len(backend.created_orders) == 1

    with sqlite3.connect(db_path) as conn:
        dispatched_count = conn.execute(
            "SELECT COUNT(*) FROM events WHERE attempt_key = ? AND event_type = 'MARKET_ENTRY_DISPATCHED'",
            (atk,),
        ).fetchone()[0]
    assert dispatched_count == 1


def test_dispatch_market_uses_exchange_average_fill_price_when_available(tmp_path: Path) -> None:
    """When exchange payload has average fill price, callback must use it (not reference price)."""
    atk = "T_-100999_2b_trader_3"
    db_path = _setup(
        tmp_path,
        attempt_key=atk,
        entry_json=[{"type": "MARKET", "price": 67200.0}],
    )

    class _AverageOnlyBackend(_FakeBackend):
        def create_order(self, **kwargs: Any) -> dict[str, Any]:
            payload = super().create_order(**kwargs)
            payload["price"] = None
            payload["average"] = 68123.5
            return payload

    backend = _AverageOnlyBackend(market_fill_price=61000.0)
    dispatcher = MarketEntryDispatcher(db_path=db_path, gateway=_make_gateway(backend))

    results = dispatcher.dispatch_pending_market_entries()

    assert len(results) == 1
    assert results[0]["ok"] is True

    with sqlite3.connect(db_path) as conn:
        entry_order_price = conn.execute(
            "SELECT price FROM orders WHERE attempt_key = ? AND purpose = 'ENTRY' LIMIT 1",
            (atk,),
        ).fetchone()[0]
        entry_event_payload = conn.execute(
            "SELECT payload_json FROM events WHERE attempt_key = ? AND event_type = 'ENTRY_FILLED' LIMIT 1",
            (atk,),
        ).fetchone()[0]

    assert entry_order_price == pytest.approx(68123.5)
    assert json.loads(entry_event_payload)["fill_price"] == pytest.approx(68123.5)


def test_dispatch_market_ignores_signal_entry_price_for_market_first_leg(tmp_path: Path) -> None:
    """MARKET first leg must not use textual entry price as execution anchor."""
    atk = "T_-100999_2c_trader_3"
    db_path = _setup(
        tmp_path,
        attempt_key=atk,
        entry_json=[{"type": "MARKET", "price": 67200.0}],
    )
    backend = _FakeBackend(market_fill_price=61000.0)
    dispatcher = MarketEntryDispatcher(db_path=db_path, gateway=_make_gateway(backend))

    results = dispatcher.dispatch_pending_market_entries()

    assert len(results) == 1
    assert results[0]["ok"] is True
    assert len(backend.created_orders) == 1
    # Ticker returns 61000 (== market_fill_price default). Signal entry price
    # 67200 must not influence qty — if it did, qty would be 250/67200 instead.
    assert backend.created_orders[0]["qty"] == pytest.approx(250.0 / 61000.0)


def test_skip_if_trade_already_exists(tmp_path: Path) -> None:
    """Dispatcher skips a signal for which a trade row already exists."""
    atk = "T_-100999_3_trader_3"
    db_path = _setup(tmp_path, attempt_key=atk)

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO trades
               (env, attempt_key, trader_id, symbol, side, execution_mode, state, created_at, updated_at)
               VALUES ('T', ?, 'trader_3', 'BTCUSDT', 'BUY', 'PAPER', 'OPEN', '2026-01-01', '2026-01-01')""",
            (atk,),
        )
        conn.commit()

    backend = _FakeBackend()
    dispatcher = MarketEntryDispatcher(db_path=db_path, gateway=_make_gateway(backend))

    results = dispatcher.dispatch_pending_market_entries()

    assert len(results) == 1
    assert results[0]["ok"] is False
    assert results[0]["action"] == "SKIP_TRADE_EXISTS"
    assert len(backend.created_orders) == 0


def test_skip_if_first_leg_not_market(tmp_path: Path) -> None:
    """LIMIT signals are not picked up by the market dispatcher."""
    atk = "T_-100999_4_trader_3"
    db_path = _setup(
        tmp_path,
        attempt_key=atk,
        entry_json=[{"type": "LIMIT", "price": 60000.0}],
    )
    backend = _FakeBackend()
    dispatcher = MarketEntryDispatcher(db_path=db_path, gateway=_make_gateway(backend))

    results = dispatcher.dispatch_pending_market_entries()

    assert results == []
    assert len(backend.created_orders) == 0


def test_skip_if_no_gateway(tmp_path: Path) -> None:
    """Without a gateway the dispatcher returns an explicit error result."""
    atk = "T_-100999_5_trader_3"
    db_path = _setup(tmp_path, attempt_key=atk)
    dispatcher = MarketEntryDispatcher(db_path=db_path, gateway=None)

    results = dispatcher.dispatch_pending_market_entries()

    assert len(results) == 1
    assert results[0]["ok"] is False
    assert results[0]["action"] == "SKIP_NO_GATEWAY"


def test_gateway_failure_recorded_and_retriable(tmp_path: Path) -> None:
    """When the gateway raises, a DISPATCH_FAILED event is written and no DISPATCHED event."""
    atk = "T_-100999_6_trader_3"
    db_path = _setup(tmp_path, attempt_key=atk)

    class _FailBackend(_FakeBackend):
        def create_order(self, **kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("exchange unreachable")

    dispatcher = MarketEntryDispatcher(db_path=db_path, gateway=_make_gateway(_FailBackend()))

    results = dispatcher.dispatch_pending_market_entries()

    assert results[0]["ok"] is False
    assert results[0]["action"] == "DISPATCH_FAILED"

    with sqlite3.connect(db_path) as conn:
        failed = conn.execute(
            "SELECT event_type FROM events WHERE attempt_key = ? AND event_type = 'MARKET_ENTRY_DISPATCH_FAILED'",
            (atk,),
        ).fetchone()
        dispatched = conn.execute(
            "SELECT event_type FROM events WHERE attempt_key = ? AND event_type = 'MARKET_ENTRY_DISPATCHED'",
            (atk,),
        ).fetchone()

    assert failed is not None
    assert dispatched is None


def test_skip_if_dispatch_event_already_exists(tmp_path: Path) -> None:
    """Dispatcher skips a PENDING signal if dispatch was already audited for the same entry."""
    atk = "T_-100999_7_trader_3"
    db_path = _setup(tmp_path, attempt_key=atk)

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO events
               (env, channel_id, telegram_msg_id, trader_id, trader_prefix,
                attempt_key, event_type, payload_json, confidence, created_at)
               VALUES ('T', 'market_dispatcher', '0', 'trader_3', 'TRAD',
                       ?, 'MARKET_ENTRY_DISPATCHED', ?, 1.0, '2026-01-01')""",
            (atk, json.dumps({"entry_id": "E1"})),
        )
        conn.commit()

    backend = _FakeBackend()
    dispatcher = MarketEntryDispatcher(db_path=db_path, gateway=_make_gateway(backend))

    results = dispatcher.dispatch_pending_market_entries()

    assert len(results) == 1
    assert results[0]["ok"] is False
    assert results[0]["action"] == "SKIP_ALREADY_DISPATCHED"
    assert len(backend.created_orders) == 0


def test_skip_if_active_entry_order_exists(tmp_path: Path) -> None:
    """Dispatcher skips a PENDING signal if an active ENTRY order already exists."""
    atk = "T_-100999_8_trader_3"
    db_path = _setup(tmp_path, attempt_key=atk)

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO orders
               (env, attempt_key, symbol, side, order_type, purpose, idx, qty, price, trigger_price,
                reduce_only, client_order_id, exchange_order_id, status, created_at, updated_at)
               VALUES ('T', ?, 'BTCUSDT', 'BUY', 'MARKET', 'ENTRY', 0, 0.01, NULL, NULL,
                       0, ?, 'ex-existing', 'OPEN', '2026-01-01', '2026-01-01')""",
            (atk, f"{atk}:ENTRY:0"),
        )
        conn.commit()

    backend = _FakeBackend()
    dispatcher = MarketEntryDispatcher(db_path=db_path, gateway=_make_gateway(backend))

    results = dispatcher.dispatch_pending_market_entries()

    assert len(results) == 1
    assert results[0]["ok"] is False
    assert results[0]["action"] == "SKIP_ORDER_EXISTS"
    assert len(backend.created_orders) == 0


def test_dispatch_market_uses_live_ticker_price_for_qty(tmp_path: Path) -> None:
    """Dispatcher must use live ticker price, not reference_price, for qty sizing."""
    atk = "T_-100999_9_trader_3"
    db_path = _setup(tmp_path, attempt_key=atk)
    # SL=57000, TP1=65000 → reference_price midpoint = 61000
    # Live ticker returns 75000 — significantly different from midpoint
    backend = _FakeBackend(market_fill_price=75000.0, ticker_price=75000.0)
    dispatcher = MarketEntryDispatcher(db_path=db_path, gateway=_make_gateway(backend))

    results = dispatcher.dispatch_pending_market_entries()

    assert len(results) == 1
    assert results[0]["ok"] is True
    # qty must be based on live price 75000, not midpoint 61000
    assert backend.created_orders[0]["qty"] == pytest.approx(250.0 / 75000.0)

    with sqlite3.connect(db_path) as conn:
        event = conn.execute(
            "SELECT payload_json FROM events WHERE attempt_key = ? AND event_type = 'MARKET_ENTRY_DISPATCHED'",
            (atk,),
        ).fetchone()
    payload = json.loads(event[0])
    assert payload["qty_price"] == pytest.approx(75000.0)
    assert payload["qty_price_source"] == "live"


def test_dispatch_market_uses_live_price_when_exchange_fill_price_missing(tmp_path: Path) -> None:
    atk = "T_-100999_10b_trader_3"
    db_path = _setup(
        tmp_path,
        attempt_key=atk,
        parse_result_id=11,
        entry_json=[{"type": "MARKET", "price": None}, {"type": "LIMIT", "price": 70000.0}],
    )

    class _NoPriceBackend(_FakeBackend):
        def create_order(self, **kwargs: Any) -> dict[str, Any]:
            payload = super().create_order(**kwargs)
            payload["price"] = None
            return payload

    backend = _NoPriceBackend(market_fill_price=68075.0, ticker_price=68075.0)
    dispatcher = MarketEntryDispatcher(db_path=db_path, gateway=_make_gateway(backend))

    results = dispatcher.dispatch_pending_market_entries()

    assert len(results) == 1
    assert results[0]["ok"] is True

    with sqlite3.connect(db_path) as conn:
        entry_order_price = conn.execute(
            "SELECT price FROM orders WHERE attempt_key = ? AND purpose = 'ENTRY' LIMIT 1",
            (atk,),
        ).fetchone()[0]
        entry_event_payload = conn.execute(
            "SELECT payload_json FROM events WHERE attempt_key = ? AND event_type = 'ENTRY_FILLED' LIMIT 1",
            (atk,),
        ).fetchone()[0]

    assert entry_order_price == pytest.approx(68075.0)
    assert json.loads(entry_event_payload)["fill_price"] == pytest.approx(68075.0)


def test_dispatch_market_falls_back_to_reference_price_when_ticker_fails(tmp_path: Path) -> None:
    """When fetch_ticker raises, dispatcher must fall back to reference_price for qty."""
    atk = "T_-100999_10_trader_3"
    db_path = _setup(tmp_path, attempt_key=atk, parse_result_id=10)

    class _NoTickerBackend(_FakeBackend):
        def fetch_ticker(self, *, symbol: str) -> None:  # type: ignore[override]
            raise RuntimeError("ticker unavailable")

    backend = _NoTickerBackend(market_fill_price=61000.0)
    dispatcher = MarketEntryDispatcher(db_path=db_path, gateway=_make_gateway(backend))

    results = dispatcher.dispatch_pending_market_entries()

    assert len(results) == 1
    assert results[0]["ok"] is True
    # Falls back to midpoint(57000, 65000) = 61000
    assert backend.created_orders[0]["qty"] == pytest.approx(250.0 / 61000.0)

    with sqlite3.connect(db_path) as conn:
        event = conn.execute(
            "SELECT payload_json FROM events WHERE attempt_key = ? AND event_type = 'MARKET_ENTRY_DISPATCHED'",
            (atk,),
        ).fetchone()
    payload = json.loads(event[0])
    assert payload["qty_price"] == pytest.approx(61000.0)
    assert payload["qty_price_source"] == "fallback"
