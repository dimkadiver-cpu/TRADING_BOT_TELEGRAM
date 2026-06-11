# tests/runtime_v2/execution_gateway/test_command_source_attribution.py
from __future__ import annotations

import json
import sqlite3

from src.runtime_v2.execution_gateway.event_ingest.models import ClassifiedEvent, ExchangeRawEvent
from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

from tests.runtime_v2.execution_gateway.test_fill_identity_dedupe import _make_db


def _insert_command(db_path: str, *, command_id: int, chain_id: int, payload: dict) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_execution_commands "
        "(command_id, trade_chain_id, command_type, status, payload_json, idempotency_key, created_at, updated_at) "
        "VALUES (?, ?, 'CLOSE_FULL', 'SENT', ?, ?, '2026-06-10T00:00:00Z', '2026-06-10T00:00:00Z')",
        (command_id, chain_id, json.dumps(payload), f"idem:{command_id}"),
    )
    conn.commit()
    conn.close()


def _make_sl_fill(exec_id: str, *, order_link_id: str | None, order_id: str | None) -> ClassifiedEvent:
    raw = ExchangeRawEvent(
        source_stream="watch_my_trades",
        exchange_event_id=exec_id,
        idempotency_key=f"exec:{exec_id}",
        symbol="BTCUSDT",
        side="Sell",
        create_type=None,
        stop_order_type=None,
        exec_type="Trade",
        order_status=None,
        order_link_id=order_link_id,
        order_id=order_id,
        seq=1000,
        exec_price=59000.0,
        exec_qty=0.1,
        closed_size=0.1,
        leaves_qty=0.0,
        pos_qty=0.0,
        exec_value=5900.0,
        exec_fee=0.002,
        fee_rate=0.00055,
        cum_exec_qty=None,
        position_take_profit=None,
        position_stop_loss=None,
        exchange_time="2026-06-10T10:00:00Z",
        received_at="2026-06-10T10:00:01Z",
        raw_info={},
    )
    return ClassifiedEvent(
        raw=raw,
        event_type="SL_FILLED",
        source="manual_command",
        trade_chain_id=1,
        tp_level=None,
        is_actionable=True,
    )


def _read_payload(db_path: str) -> dict:
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT payload_json FROM ops_exchange_events WHERE event_type='SL_FILLED'"
    ).fetchone()
    conn.close()
    assert row is not None
    return json.loads(row[0])


def test_ws_fill_with_command_coid_inherits_command_source(tmp_path):
    """Fill WS di un ordine piazzato da un comando trader_update: il payload deve
    portare il source del comando, come già fa il path REST."""
    db_path = _make_db(tmp_path)
    _insert_command(db_path, command_id=7, chain_id=1, payload={"command_source": "trader_update"})
    repo = GatewayCommandRepository(db_path)

    ev = _make_sl_fill("exec-ws-1", order_link_id="tsb:1:7:sl:1", order_id="ord-1")
    assert repo.insert_raw_and_classified(ev) is True

    payload = _read_payload(db_path)
    assert payload["source"] == "trader_update"
    assert payload["command_id"] == 7


def test_ws_fill_with_command_without_source_keeps_classifier_source(tmp_path):
    """Comando senza command_source nel payload: resta il verdetto del classifier."""
    db_path = _make_db(tmp_path)
    _insert_command(db_path, command_id=7, chain_id=1, payload={})
    repo = GatewayCommandRepository(db_path)

    ev = _make_sl_fill("exec-ws-2", order_link_id="tsb:1:7:sl:1", order_id="ord-1")
    assert repo.insert_raw_and_classified(ev) is True

    payload = _read_payload(db_path)
    assert payload["source"] == "manual_command"
    assert payload["command_id"] == 7


def test_ws_fill_without_coid_keeps_classifier_source(tmp_path):
    """SL position-level (nessun orderLinkId): nessuna attribuzione comando."""
    db_path = _make_db(tmp_path)
    repo = GatewayCommandRepository(db_path)

    ev = _make_sl_fill("exec-ws-3", order_link_id=None, order_id=None)
    assert repo.insert_raw_and_classified(ev) is True

    payload = _read_payload(db_path)
    assert payload["source"] == "manual_command"
    assert payload["command_id"] is None


def test_has_exchange_event_for_order(tmp_path):
    db_path = _make_db(tmp_path)
    repo = GatewayCommandRepository(db_path)

    ev = _make_sl_fill("exec-ws-4", order_link_id="tsb:1:7:sl:1", order_id="ord-9")
    repo.insert_raw_and_classified(ev)

    assert repo.has_exchange_event_for_order(1, "SL_FILLED", "ord-9") is True
    assert repo.has_exchange_event_for_order(1, "SL_FILLED", "ord-altro") is False
    assert repo.has_exchange_event_for_order(2, "SL_FILLED", "ord-9") is False
    assert repo.has_exchange_event_for_order(1, "TP_FILLED", "ord-9") is False
    assert repo.has_exchange_event_for_order(1, "SL_FILLED", None) is False


class _FakeOrderRaw:
    def __init__(self, exchange_order_id: str, client_order_id: str):
        self.exchange_order_id = exchange_order_id
        self.client_order_id = client_order_id
        self.average_price = 59000.0
        self.filled_qty = 0.1
        self.exec_fee = 0.002
        self.exec_value = 5900.0
        self.exchange_time = "2026-06-10T10:00:00Z"
        self.leaves_qty = 0.0
        self.cum_exec_qty = 0.1


def test_rest_save_fill_skips_when_ws_already_recorded_same_order(tmp_path):
    """Il fill WS (chiave fill:{execId}) e quello REST (chiave semantica) per lo
    stesso ordine non devono coesistere: il REST deve saltare l'inserimento."""
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker

    db_path = _make_db(tmp_path)
    _insert_command(db_path, command_id=7, chain_id=1, payload={"command_source": "trader_update"})
    repo = GatewayCommandRepository(db_path)

    # il WS ha già registrato il fill
    ws_ev = _make_sl_fill("exec-ws-5", order_link_id="tsb:1:7:sl:1", order_id="ord-5")
    assert repo.insert_raw_and_classified(ws_ev) is True

    worker = ExchangeEventSyncWorker(
        ops_db_path=db_path,
        adapter=None,
        repo=repo,
        execution_account_id="test_account",
    )
    result = worker._save_fill_event("tsb:1:7:sl:1", _FakeOrderRaw("ord-5", "tsb:1:7:sl:1"))

    assert result is True  # trattato come già registrato: il comando può andare in DONE

    conn = sqlite3.connect(db_path)
    cnt = conn.execute(
        "SELECT COUNT(*) FROM ops_exchange_events WHERE event_type='SL_FILLED'"
    ).fetchone()[0]
    conn.close()
    assert cnt == 1


def test_rest_save_fill_inserts_when_no_ws_event(tmp_path):
    """Senza evento WS preesistente il path REST inserisce normalmente, con il
    source del comando."""
    from src.runtime_v2.execution_gateway.event_sync import ExchangeEventSyncWorker

    db_path = _make_db(tmp_path)
    _insert_command(db_path, command_id=7, chain_id=1, payload={"command_source": "trader_update"})
    repo = GatewayCommandRepository(db_path)

    worker = ExchangeEventSyncWorker(
        ops_db_path=db_path,
        adapter=None,
        repo=repo,
        execution_account_id="test_account",
    )
    result = worker._save_fill_event("tsb:1:7:sl:1", _FakeOrderRaw("ord-6", "tsb:1:7:sl:1"))

    assert result is True
    payload = _read_payload(db_path)
    assert payload["source"] == "trader_update"
