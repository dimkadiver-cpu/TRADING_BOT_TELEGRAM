# tests/runtime_v2/lifecycle/test_cancel_expander.py
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def _apply_migrations(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    for f in sorted((_REPO_ROOT / "db" / "ops_migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


def _insert_place_entry_cmd(conn, cmd_id, chain_id, client_order_id, status="SENT"):
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO ops_execution_commands "
        "(command_id, trade_chain_id, command_type, status, payload_json, "
        "idempotency_key, client_order_id, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (cmd_id, chain_id, "PLACE_ENTRY", status, "{}",
         f"place_entry:{chain_id}:leg{cmd_id}", client_order_id, now, now),
    )


def test_expand_non_cancel_command_returns_original(tmp_path):
    """Comandi non CANCEL_PENDING_ENTRY vengono restituiti invariati."""
    from src.runtime_v2.lifecycle.cancel_expander import expand_cancel_pending_commands
    db = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db)
    conn = sqlite3.connect(db)
    results = expand_cancel_pending_commands(
        conn,
        trade_chain_id=1,
        command_type="REBUILD_PARTIAL_TPS",
        payload_json='{"symbol": "BTC/USDT"}',
        idempotency_key="rebuild:1:42",
    )
    conn.close()
    assert results == [('{"symbol": "BTC/USDT"}', "rebuild:1:42")]


def test_expand_cancel_with_no_pending_entry_commands_returns_original(tmp_path):
    """Se non ci sono PLACE_ENTRY attivi, ritorna il comando originale invariato."""
    from src.runtime_v2.lifecycle.cancel_expander import expand_cancel_pending_commands
    db = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db)
    conn = sqlite3.connect(db)
    results = expand_cancel_pending_commands(
        conn,
        trade_chain_id=99,
        command_type="CANCEL_PENDING_ENTRY",
        payload_json='{"symbol": "BTC/USDT", "side": "LONG"}',
        idempotency_key="auto_cancel:99:1:legX",
    )
    conn.close()
    assert len(results) == 1
    assert results[0][1] == "auto_cancel:99:1:legX"


def test_expand_cancel_with_two_pending_entries_expands_to_two(tmp_path):
    """Con 2 PLACE_ENTRY attivi, il comando CANCEL viene espanso in 2 comandi."""
    from src.runtime_v2.lifecycle.cancel_expander import expand_cancel_pending_commands
    db = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db)
    conn = sqlite3.connect(db)
    _insert_place_entry_cmd(conn, 1, 10, "tsb:10:1:entry:1", status="SENT")
    _insert_place_entry_cmd(conn, 2, 10, "tsb:10:2:entry:2", status="ACK")
    conn.commit()

    results = expand_cancel_pending_commands(
        conn,
        trade_chain_id=10,
        command_type="CANCEL_PENDING_ENTRY",
        payload_json='{"symbol": "BTC/USDT", "side": "LONG"}',
        idempotency_key="auto_cancel:10:5:legX",
    )
    conn.close()

    assert len(results) == 2
    payloads = [json.loads(p) for p, _ in results]
    keys = [k for _, k in results]
    assert payloads[0]["entry_client_order_id"] == "tsb:10:1:entry:1"
    assert payloads[1]["entry_client_order_id"] == "tsb:10:2:entry:2"
    assert "tsb:10:1:entry:1" in keys[0]
    assert "tsb:10:2:entry:2" in keys[1]


def test_expand_cancel_does_not_include_done_commands(tmp_path):
    """Comandi PLACE_ENTRY con status DONE non vengono inclusi nell'espansione."""
    from src.runtime_v2.lifecycle.cancel_expander import expand_cancel_pending_commands
    db = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db)
    conn = sqlite3.connect(db)
    _insert_place_entry_cmd(conn, 1, 10, "tsb:10:1:entry:1", status="DONE")  # escluso
    _insert_place_entry_cmd(conn, 2, 10, "tsb:10:2:entry:2", status="SENT")  # incluso
    conn.commit()

    results = expand_cancel_pending_commands(
        conn,
        trade_chain_id=10,
        command_type="CANCEL_PENDING_ENTRY",
        payload_json='{"symbol": "BTC/USDT"}',
        idempotency_key="idem:10",
    )
    conn.close()

    assert len(results) == 1
    assert json.loads(results[0][0])["entry_client_order_id"] == "tsb:10:2:entry:2"


def test_load_pending_entry_client_order_ids_returns_tsb_ids(tmp_path):
    """load_pending_entry_client_order_ids ritorna solo i client_order_id dei comandi attivi."""
    from src.runtime_v2.lifecycle.cancel_expander import load_pending_entry_client_order_ids
    db = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db)
    conn = sqlite3.connect(db)
    _insert_place_entry_cmd(conn, 10, 5, "tsb:5:10:entry:1", status="PENDING")
    _insert_place_entry_cmd(conn, 11, 5, "tsb:5:11:entry:2", status="SENT")
    _insert_place_entry_cmd(conn, 12, 5, "tsb:5:12:entry:3", status="DONE")  # escluso
    conn.commit()

    ids = load_pending_entry_client_order_ids(conn, 5)
    conn.close()

    assert ids == ["tsb:5:10:entry:1", "tsb:5:11:entry:2"]


def test_expand_cancel_with_existing_entry_client_order_id_returns_original(tmp_path):
    """Se il payload ha già entry_client_order_id, non deve essere ri-espanso."""
    from src.runtime_v2.lifecycle.cancel_expander import expand_cancel_pending_commands
    db = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db)
    conn = sqlite3.connect(db)
    # Inserire PLACE_ENTRY attivi — non devono influenzare il risultato
    _insert_place_entry_cmd(conn, 1, 10, "tsb:10:1:entry:1", status="SENT")
    _insert_place_entry_cmd(conn, 2, 10, "tsb:10:2:entry:2", status="SENT")
    conn.commit()

    # Comando già concreto con entry_client_order_id nel payload
    concrete_payload = json.dumps({
        "symbol": "BTC/USDT",
        "side": "LONG",
        "entry_client_order_id": "tsb:10:1:entry:1",
    })
    results = expand_cancel_pending_commands(
        conn,
        trade_chain_id=10,
        command_type="CANCEL_PENDING_ENTRY",
        payload_json=concrete_payload,
        idempotency_key="auto_cancel:10:5:leg_1",
    )
    conn.close()

    # Deve tornare il comando originale invariato, NON espanderlo in 2
    assert len(results) == 1
    assert results[0][0] == concrete_payload
    assert results[0][1] == "auto_cancel:10:5:leg_1"
