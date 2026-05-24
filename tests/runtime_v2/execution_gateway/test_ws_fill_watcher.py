# tests/runtime_v2/execution_gateway/test_ws_fill_watcher.py
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest


def _apply_migrations(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    for f in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


def _insert_open_chain(
    db_path: str,
    chain_id: int,
    symbol: str = "BTC/USDT:USDT",
    side: str = "LONG",
    open_qty: float = 0.01,
) -> None:
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_trade_chains "
        "(trade_chain_id, source_enrichment_id, canonical_message_id, raw_message_id, "
        "trader_id, account_id, symbol, side, lifecycle_state, entry_mode, "
        "management_plan_json, open_position_qty, filled_entry_qty, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (chain_id, chain_id, chain_id, chain_id, "t1", "acc",
         symbol, side, "OPEN", "ONE_SHOT", "{}", open_qty, open_qty, now, now),
    )
    conn.commit()
    conn.close()


def _insert_tp_command(
    db_path: str,
    chain_id: int,
    cmd_id: int,
    tp_price: float,
    tp_level: int = 1,
    tp_size: float = 0.005,
) -> None:
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    payload = json.dumps({
        "symbol": "BTC/USDT:USDT",
        "side": "LONG",
        "take_profit": tp_price,
        "tp_size": tp_size,
        "tp_sequence": tp_level,
        "tp_order_type": "Limit",
        "tp_limit_price": tp_price,
        "tp_trigger_by": "MarkPrice",
        "preserve_sl": True,
    })
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_execution_commands "
        "(command_id, trade_chain_id, command_type, status, payload_json, "
        "idempotency_key, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (cmd_id, chain_id, "SET_POSITION_TPSL_PARTIAL", "DONE",
         payload, f"idem_tp:{cmd_id}", now, now),
    )
    conn.commit()
    conn.close()


@pytest.fixture
def ops_db(tmp_path):
    db = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db)
    return db


def _make_watcher(ops_db: str):
    from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.ws_fill_watcher import BybitWsFillWatcher
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository
    repo = GatewayCommandRepository(ops_db)
    return BybitWsFillWatcher(
        api_key="k", api_secret="s", testnet=False,
        ops_db_path=ops_db, repo=repo,
    )


# ── _save_tp_fill_from_trade ──────────────────────────────────────────────────

def test_save_tp_fill_from_trade_inserts_event(ops_db):
    """_save_tp_fill_from_trade() inserisce TP_FILLED con dati reali e chiave level:N."""
    watcher = _make_watcher(ops_db)
    watcher._save_tp_fill_from_trade(
        chain_id=1, tp_level=1,
        fill_price=67350.5, filled_qty=0.01,
        is_final=True, exchange_trade_id="trade-xyz",
    )

    conn = sqlite3.connect(ops_db)
    rows = conn.execute(
        "SELECT event_type, payload_json, idempotency_key FROM ops_exchange_events"
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    event_type, payload_json, ikey = rows[0]
    assert event_type == "TP_FILLED"
    assert ikey == "TP_FILLED:1:level:1"
    p = json.loads(payload_json)
    assert p["fill_price"] == 67350.5
    assert p["filled_qty"] == 0.01
    assert p["is_final"] is True
    assert p["tp_level"] == 1
    assert p["source"] == "watch_my_trades"
    assert p["exchange_trade_id"] == "trade-xyz"


def test_save_tp_fill_from_trade_idempotent(ops_db):
    """Due chiamate con stesso chain+level → esattamente 1 riga (INSERT OR IGNORE)."""
    watcher = _make_watcher(ops_db)
    watcher._save_tp_fill_from_trade(1, 1, 67350.5, 0.01, True, "t1")
    watcher._save_tp_fill_from_trade(1, 1, 67360.0, 0.01, True, "t2")  # stesso livello

    conn = sqlite3.connect(ops_db)
    count = conn.execute("SELECT COUNT(*) FROM ops_exchange_events").fetchone()[0]
    conn.close()
    assert count == 1


# ── _save_fill() idempotency key per TP standalone ───────────────────────────

def test_save_fill_tp_uses_unified_key(ops_db):
    """_save_fill() per role=tp usa 'TP_FILLED:{chain}:level:{seq}' non exchange_order_id."""
    from src.runtime_v2.execution_gateway.models import RawAdapterOrder
    watcher = _make_watcher(ops_db)

    raw = RawAdapterOrder(
        client_order_id="tsb:42:999:tp:2",
        exchange_order_id="exch-order-123",
        status="FILLED",
        filled_qty=0.005,
        average_price=70000.0,
    )
    # Necessario per count_active_tps — aggiunge chain e TP DONE
    _insert_open_chain(ops_db, 42)
    _insert_tp_command(ops_db, 42, 9990, tp_price=70000.0, tp_level=2)

    watcher._save_fill("tsb:42:999:tp:2", raw)

    conn = sqlite3.connect(ops_db)
    rows = conn.execute(
        "SELECT idempotency_key FROM ops_exchange_events WHERE trade_chain_id=42"
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0][0] == "TP_FILLED:42:level:2"


# ── _process_trade_batch matching ────────────────────────────────────────────

def test_process_trade_batch_matched_tp_inserts_event(ops_db):
    """Trade reduceOnly con price che matcha TP attivo → TP_FILLED inserito."""
    _insert_open_chain(ops_db, 10, symbol="BTC/USDT:USDT", side="LONG")
    _insert_tp_command(ops_db, 10, 1001, tp_price=67000.0, tp_level=1)

    watcher = _make_watcher(ops_db)
    # Fill sell (close LONG): price 67005.0 è entro ±1% di 67000.0
    trades = [{
        "symbol": "BTC/USDT:USDT",
        "side": "sell",
        "price": 67005.0,
        "amount": 0.005,
        "reduceOnly": True,
        "id": "trade-001",
        "info": {"posQty": "0.005"},  # posizione residua non zero
    }]
    watcher._process_trade_batch(trades)

    conn = sqlite3.connect(ops_db)
    rows = conn.execute(
        "SELECT event_type, payload_json, idempotency_key FROM ops_exchange_events WHERE trade_chain_id=10"
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0][0] == "TP_FILLED"
    assert rows[0][2] == "TP_FILLED:10:level:1"
    p = json.loads(rows[0][1])
    assert p["fill_price"] == 67005.0
    assert p["filled_qty"] == 0.005
    assert p["is_final"] is False  # posQty=0.005 > 0
    assert p["source"] == "watch_my_trades"


def test_process_trade_batch_is_final_true_when_pos_qty_zero(ops_db):
    """`posQty=0` nel trade → is_final=True."""
    _insert_open_chain(ops_db, 11, symbol="ETH/USDT:USDT", side="LONG")
    _insert_tp_command(ops_db, 11, 1101, tp_price=3200.0, tp_level=1)

    watcher = _make_watcher(ops_db)
    trades = [{
        "symbol": "ETH/USDT:USDT",
        "side": "sell",
        "price": 3201.0,
        "amount": 0.1,
        "reduceOnly": True,
        "id": "t-eth-001",
        "info": {"posQty": "0"},  # posizione azzerata
    }]
    watcher._process_trade_batch(trades)

    conn = sqlite3.connect(ops_db)
    p = json.loads(conn.execute(
        "SELECT payload_json FROM ops_exchange_events WHERE trade_chain_id=11"
    ).fetchone()[0])
    conn.close()
    assert p["is_final"] is True


def test_process_trade_batch_is_final_false_fallback_when_no_pos_qty(ops_db):
    """`posQty` assente → is_final=False (conservativo)."""
    _insert_open_chain(ops_db, 12, symbol="SOL/USDT:USDT", side="LONG")
    _insert_tp_command(ops_db, 12, 1201, tp_price=160.0, tp_level=1)

    watcher = _make_watcher(ops_db)
    trades = [{
        "symbol": "SOL/USDT:USDT",
        "side": "sell",
        "price": 160.5,
        "amount": 5.0,
        "reduceOnly": True,
        "id": "t-sol-001",
        "info": {},  # posQty non presente
    }]
    watcher._process_trade_batch(trades)

    conn = sqlite3.connect(ops_db)
    p = json.loads(conn.execute(
        "SELECT payload_json FROM ops_exchange_events WHERE trade_chain_id=12"
    ).fetchone()[0])
    conn.close()
    assert p["is_final"] is False


def test_process_trade_batch_ignores_non_reduce_only(ops_db):
    """Fill NON reduceOnly (entry, SL) → ignorato, nessun evento inserito."""
    _insert_open_chain(ops_db, 13)
    _insert_tp_command(ops_db, 13, 1301, tp_price=70000.0)

    watcher = _make_watcher(ops_db)
    trades = [{
        "symbol": "BTC/USDT:USDT",
        "side": "buy",
        "price": 70000.0,
        "amount": 0.01,
        "reduceOnly": False,  # ← entry fill, non TP
        "id": "t-entry",
        "info": {},
    }]
    watcher._process_trade_batch(trades)

    conn = sqlite3.connect(ops_db)
    count = conn.execute("SELECT COUNT(*) FROM ops_exchange_events").fetchone()[0]
    conn.close()
    assert count == 0


def test_process_trade_batch_ambiguous_skipped(ops_db):
    """2 chain con TP a prezzi simili (entro ±1%) → skip silenzioso, nessun INSERT."""
    # Chain 20 con TP a 70000
    _insert_open_chain(ops_db, 20, symbol="BTC/USDT:USDT", side="LONG")
    _insert_tp_command(ops_db, 20, 2001, tp_price=70000.0, tp_level=1)
    # Chain 21 con TP a 70050 (entro 1% da 70100)
    _insert_open_chain(ops_db, 21, symbol="BTC/USDT:USDT", side="LONG")
    _insert_tp_command(ops_db, 21, 2101, tp_price=70050.0, tp_level=1)

    watcher = _make_watcher(ops_db)
    # fill a 70100: entro 1% sia da 70000 (0.14%) che da 70050 (0.07%) → ambiguo
    trades = [{
        "symbol": "BTC/USDT:USDT",
        "side": "sell",
        "price": 70100.0,
        "amount": 0.005,
        "reduceOnly": True,
        "id": "t-ambig",
        "info": {"posQty": "0.005"},
    }]
    watcher._process_trade_batch(trades)

    conn = sqlite3.connect(ops_db)
    count = conn.execute("SELECT COUNT(*) FROM ops_exchange_events").fetchone()[0]
    conn.close()
    assert count == 0, "Match ambiguo: nessun evento deve essere inserito"


def test_process_trade_batch_none_is_noop(ops_db):
    """_process_trade_batch(None) silently no-ops — no crash, no events."""
    watcher = _make_watcher(ops_db)
    watcher._process_trade_batch(None)  # must not raise

    conn = sqlite3.connect(ops_db)
    count = conn.execute("SELECT COUNT(*) FROM ops_exchange_events").fetchone()[0]
    conn.close()
    assert count == 0


def test_process_trade_batch_is_final_false_on_unparseable_pos_qty(ops_db):
    """`posQty` with non-numeric value → is_final=False (fallback branch)."""
    _insert_open_chain(ops_db, 14, symbol="BTC/USDT:USDT", side="LONG")
    _insert_tp_command(ops_db, 14, 1401, tp_price=70000.0, tp_level=1)

    watcher = _make_watcher(ops_db)
    trades = [{
        "symbol": "BTC/USDT:USDT",
        "side": "sell",
        "price": 70050.0,
        "amount": 0.005,
        "reduceOnly": True,
        "id": "t-bad-posqty",
        "info": {"posQty": "N/A"},  # unparseable
    }]
    watcher._process_trade_batch(trades)

    conn = sqlite3.connect(ops_db)
    p = json.loads(conn.execute(
        "SELECT payload_json FROM ops_exchange_events WHERE trade_chain_id=14"
    ).fetchone()[0])
    conn.close()
    assert p["is_final"] is False
