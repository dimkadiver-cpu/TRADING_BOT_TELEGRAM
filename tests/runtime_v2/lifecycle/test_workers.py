from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest


def _apply_migrations(db_path: str, migrations_dir: Path) -> None:
    conn = sqlite3.connect(db_path)
    for f in sorted(migrations_dir.glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_enriched_signal(
    *,
    enrichment_id: int = 1,
    trader_id: str = "trader_a",
    symbol: str = "BTC/USDT",
    side: str = "LONG",
    entry_type: str = "LIMIT",
    entry_price: float = 50000.0,
    sl_price: float = 49000.0,
    tp_prices: list[float] | None = None,
    capital_base_usdt: float = 1000.0,
    risk_pct: float = 1.0,
    max_concurrent_trades: int = 5,
    max_concurrent_same_symbol: int = 1,
    be_trigger: str | None = None,
):
    from src.parser_v2.contracts.entities import Price, StopLoss, TakeProfit
    from src.runtime_v2.signal_enrichment.models import (
        EffectiveEnrichmentConfig, EnrichedCanonicalMessage,
        EnrichedEntryLeg, EnrichedSignalPayload, ManagementPlanConfig,
        MarketExecutionConfig, EntrySplitConfig, EntryWeightsConfig,
        LimitEntrySplitConfig, MarketEntrySplitConfig, EntryRangeConfig,
        RiskConfig, SignalPolicyConfig, TpConfig, SlConfig,
        PriceCorrectionsConfig, PriceSanityConfig,
    )
    entries = [EnrichedEntryLeg(
        sequence=1, entry_type=entry_type,
        price=Price(raw=str(entry_price), value=entry_price) if entry_type == "LIMIT" else None,
        weight=1.0,
    )]
    tps = [
        TakeProfit(sequence=i + 1, price=Price(raw=str(p), value=p))
        for i, p in enumerate(tp_prices or [51000.0])
    ]
    sl = StopLoss(price=Price(raw=str(sl_price), value=sl_price))
    signal = EnrichedSignalPayload(
        symbol=symbol, side=side, entry_structure="ONE_SHOT",
        entries=entries, take_profits=tps, stop_loss=sl,
    )
    weights = EntryWeightsConfig(weights={"E1": 1.0})
    policy = EffectiveEnrichmentConfig(
        trader_id=trader_id, enabled=True, gate_mode="block",
        hedge_mode=False, account_id="acc_1",
        signal_policy=SignalPolicyConfig(
            accepted_entry_structures=["ONE_SHOT", "TWO_STEP", "RANGE", "LADDER"],
            market_execution=MarketExecutionConfig(),
            entry_split=EntrySplitConfig(
                LIMIT=LimitEntrySplitConfig(
                    single=weights,
                    range=EntryRangeConfig(weights={"E1": 0.5, "E2": 0.5}),
                    averaging=weights, ladder=weights,
                ),
                MARKET=MarketEntrySplitConfig(single=weights, averaging=weights),
            ),
            tp=TpConfig(), sl=SlConfig(),
            price_corrections=PriceCorrectionsConfig(), price_sanity=PriceSanityConfig(),
        ),
        update_admission={},
        management_plan=ManagementPlanConfig(be_trigger=be_trigger),
        risk=RiskConfig(
            mode="risk_pct_of_capital", risk_pct_of_capital=risk_pct,
            capital_base_mode="static_config", capital_base_usdt=capital_base_usdt,
            leverage=1, max_capital_at_risk_per_trader_pct=50.0,
            max_concurrent_trades=max_concurrent_trades,
            max_concurrent_same_symbol=max_concurrent_same_symbol,
        ),
    )
    return EnrichedCanonicalMessage(
        enrichment_id=enrichment_id,
        canonical_message_id=enrichment_id * 10,
        raw_message_id=enrichment_id * 100,
        trader_id=trader_id, account_id="acc_1",
        primary_class="SIGNAL", enrichment_decision="PASS",
        enriched_signal=signal,
        management_plan=ManagementPlanConfig(be_trigger=be_trigger),
        policy_snapshot=policy.model_dump(),
    )


@pytest.fixture
def dbs(tmp_path):
    parser_db = str(tmp_path / "parser.sqlite3")
    ops_db = str(tmp_path / "ops.sqlite3")
    _apply_migrations(parser_db, Path("db/migrations"))
    _apply_migrations(ops_db, Path("db/ops_migrations"))
    return parser_db, ops_db


def _insert_enriched(parser_db: str, enrichment_id: int, enriched) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(parser_db)
    conn.execute(
        """
        INSERT INTO enriched_canonical_messages (
            enrichment_id, canonical_message_id, raw_message_id, trader_id, account_id,
            primary_class, enrichment_decision, enriched_signal_json,
            management_plan_json, policy_snapshot_json, lifecycle_processed, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,0,?)
        """,
        (
            enrichment_id,
            enriched.canonical_message_id,
            enriched.raw_message_id,
            enriched.trader_id,
            enriched.account_id,
            enriched.primary_class,
            enriched.enrichment_decision,
            enriched.enriched_signal.model_dump_json() if enriched.enriched_signal else None,
            enriched.management_plan.model_dump_json() if enriched.management_plan else "{}",
            json.dumps(enriched.policy_snapshot),
            now,
        ),
    )
    conn.commit()
    conn.close()


def _make_worker(parser_db, ops_db):
    from src.runtime_v2.lifecycle.entry_gate import LifecycleEntryGate, LifecycleGateWorker
    from src.runtime_v2.lifecycle.repositories import (
        ControlStateRepository, ExecutionCommandRepository,
        LifecycleEventRepository, SnapshotRepository, TradeChainRepository,
    )
    from src.runtime_v2.lifecycle.risk_capacity import RiskCapacityEngine
    from src.runtime_v2.lifecycle.static_exchange_data_port import StaticExchangeDataPort

    gate = LifecycleEntryGate(
        risk_engine=RiskCapacityEngine(),
        exchange_port=StaticExchangeDataPort(),
    )
    return LifecycleGateWorker(
        parser_db_path=parser_db,
        ops_db_path=ops_db,
        gate=gate,
        chain_repo=TradeChainRepository(ops_db),
        event_repo=LifecycleEventRepository(ops_db),
        command_repo=ExecutionCommandRepository(ops_db),
        snapshot_repo=SnapshotRepository(ops_db),
        control_repo=ControlStateRepository(ops_db),
    )


def test_worker_processes_signal_creates_chain(dbs):
    parser_db, ops_db = dbs
    enriched = _make_enriched_signal(enrichment_id=1)
    _insert_enriched(parser_db, 1, enriched)

    worker = _make_worker(parser_db, ops_db)
    count = worker.run_once()
    assert count == 1

    conn = sqlite3.connect(ops_db)
    chains = conn.execute("SELECT * FROM ops_trade_chains").fetchall()
    commands = conn.execute("SELECT command_type FROM ops_execution_commands").fetchall()
    conn.close()
    assert len(chains) == 1
    assert any(c[0] == "PLACE_ENTRY_WITH_ATTACHED_TPSL" for c in commands)


def test_worker_processes_signal_persists_initial_risk_amount(dbs):
    parser_db, ops_db = dbs
    enriched = _make_enriched_signal(
        enrichment_id=11,
        risk_pct=1.0,
        capital_base_usdt=10000.0,
    )
    _insert_enriched(parser_db, 11, enriched)

    worker = _make_worker(parser_db, ops_db)
    count = worker.run_once()
    assert count == 1

    conn = sqlite3.connect(ops_db)
    row = conn.execute(
        "SELECT initial_risk_amount, peak_margin_used FROM ops_trade_chains"
    ).fetchone()
    conn.close()
    assert row[0] == pytest.approx(100.0)
    assert row[1] is None


def test_worker_marks_lifecycle_processed(dbs):
    parser_db, ops_db = dbs
    enriched = _make_enriched_signal(enrichment_id=2)
    _insert_enriched(parser_db, 2, enriched)

    worker = _make_worker(parser_db, ops_db)
    worker.run_once()

    conn = sqlite3.connect(parser_db)
    row = conn.execute(
        "SELECT lifecycle_processed FROM enriched_canonical_messages WHERE enrichment_id=?", (2,)
    ).fetchone()
    conn.close()
    assert row[0] == 1


def test_worker_idempotent_on_double_run(dbs):
    parser_db, ops_db = dbs
    enriched = _make_enriched_signal(enrichment_id=3)
    _insert_enriched(parser_db, 3, enriched)

    worker = _make_worker(parser_db, ops_db)
    worker.run_once()
    worker.run_once()  # second run: nothing to do

    conn = sqlite3.connect(ops_db)
    chains = conn.execute("SELECT COUNT(*) FROM ops_trade_chains").fetchone()
    conn.close()
    assert chains[0] == 1


def test_worker_block_new_entries_produces_reject(dbs):
    parser_db, ops_db = dbs
    enriched = _make_enriched_signal(enrichment_id=4)
    _insert_enriched(parser_db, 4, enriched)

    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(ops_db)
    conn.execute(
        "INSERT INTO ops_control_state (scope_type, execution_pause_mode, active, created_at, updated_at) VALUES (?,?,1,?,?)",
        ("GLOBAL", "BLOCK_NEW_ENTRIES", now, now),
    )
    conn.commit()
    conn.close()

    worker = _make_worker(parser_db, ops_db)
    worker.run_once()

    conn = sqlite3.connect(ops_db)
    chains = conn.execute("SELECT COUNT(*) FROM ops_trade_chains").fetchone()
    reject_events = conn.execute(
        "SELECT COUNT(*) FROM ops_lifecycle_events WHERE event_type='SIGNAL_REJECTED'"
    ).fetchone()
    conn.close()
    assert chains[0] == 0
    assert reject_events[0] == 1


def _make_chain_in_db(ops_db: str, *, trade_chain_id_hint: int, state: str,
                      timeout_at_isoformat: str | None = None, symbol: str = "BTC/USDT") -> int:
    import sqlite3
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(ops_db)
    cursor = conn.execute(
        """
        INSERT INTO ops_trade_chains (
            source_enrichment_id, canonical_message_id, raw_message_id,
            trader_id, account_id, symbol, side, lifecycle_state, entry_mode,
            management_plan_json, created_at, updated_at, entry_timeout_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            trade_chain_id_hint, trade_chain_id_hint * 10, trade_chain_id_hint * 100,
            "trader_a", "acc_1", symbol, "LONG", state, "ONE_SHOT",
            "{}", now, now, timeout_at_isoformat,
        ),
    )
    conn.commit()
    inserted_id = cursor.lastrowid
    conn.close()
    return inserted_id


def test_timeout_worker_expires_waiting_entry(dbs):
    import sqlite3
    from datetime import datetime, timedelta, timezone
    _, ops_db = dbs
    past_timeout = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    chain_id = _make_chain_in_db(ops_db, trade_chain_id_hint=50, state="WAITING_ENTRY",
                                  timeout_at_isoformat=past_timeout)

    from src.runtime_v2.lifecycle.repositories import TradeChainRepository
    from src.runtime_v2.lifecycle.workers import TimeoutWorker
    worker = TimeoutWorker(ops_db_path=ops_db, chain_repo=TradeChainRepository(ops_db))
    count = worker.run_once()
    assert count == 1

    conn = sqlite3.connect(ops_db)
    state = conn.execute(
        "SELECT lifecycle_state FROM ops_trade_chains WHERE trade_chain_id=?", (chain_id,)
    ).fetchone()[0]
    cmds = conn.execute(
        "SELECT command_type FROM ops_execution_commands WHERE trade_chain_id=?", (chain_id,)
    ).fetchall()
    events = conn.execute(
        "SELECT event_type FROM ops_lifecycle_events WHERE trade_chain_id=?", (chain_id,)
    ).fetchall()
    conn.close()

    assert state == "EXPIRED"
    assert any(c[0] == "CANCEL_PENDING_ENTRY" for c in cmds)
    assert any(e[0] == "PENDING_TIMEOUT" for e in events)


def test_persist_result_expands_cancel_pending_entry_to_per_order_commands(tmp_path):
    """_persist_result deve espandere CANCEL_PENDING_ENTRY con ID exchange reali."""
    import json as _json
    import sqlite3 as _sqlite3
    from datetime import datetime, timezone
    from pathlib import Path
    from unittest.mock import MagicMock

    # Setup DB
    db = str(tmp_path / "ops.sqlite3")
    conn = _sqlite3.connect(db)
    for f in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit()

    now_str = datetime.now(timezone.utc).isoformat()
    chain_id = 42

    # Insert una trade chain
    conn.execute(
        "INSERT INTO ops_trade_chains "
        "(trade_chain_id, source_enrichment_id, canonical_message_id, raw_message_id, "
        "trader_id, account_id, symbol, side, lifecycle_state, entry_mode, "
        "be_protection_status, management_plan_json, plan_state_json, "
        "created_at, updated_at) "
        "VALUES (?,1,1,1,'t1','acc1','BTC/USDT','LONG','OPEN','ONE_SHOT','NOT_PROTECTED','{}','{}',?,?)",
        (chain_id, now_str, now_str),
    )

    # Inserire 2 PLACE_ENTRY commands attivi con client_order_id reali
    for cmd_id, seq in [(100, 2), (101, 3)]:
        coid = f"tsb:{chain_id}:{cmd_id}:entry:{seq}"
        conn.execute(
            "INSERT INTO ops_execution_commands "
            "(command_id, trade_chain_id, command_type, status, payload_json, "
            "idempotency_key, client_order_id, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (cmd_id, chain_id, "PLACE_ENTRY", "SENT", "{}",
             f"place_entry:{chain_id}:leg{seq}", coid, now_str, now_str),
        )
    conn.commit()
    conn.close()

    # Costruire un result con un CANCEL_PENDING_ENTRY (come emesso da auto-cancel averaging)
    from src.runtime_v2.lifecycle.event_processor import EventProcessorResult
    from src.runtime_v2.lifecycle.models import ExecutionCommand
    from src.runtime_v2.lifecycle.workers import LifecycleEventWorker
    from src.runtime_v2.lifecycle.repositories import (
        ExecutionCommandRepository, ExchangeEventRepository,
        LifecycleEventRepository, TradeChainRepository,
    )

    cancel_cmd = ExecutionCommand(
        trade_chain_id=chain_id,
        command_type="CANCEL_PENDING_ENTRY",
        status="PENDING",
        payload_json=_json.dumps({"symbol": "BTC/USDT", "side": "LONG"}),
        idempotency_key=f"auto_cancel:{chain_id}:5:legX",
    )
    result = EventProcessorResult(
        new_lifecycle_state=None,
        new_be_protection_status=None,
        entry_avg_price=None,
        current_stop_price=None,
        lifecycle_events=[],
        execution_commands=[cancel_cmd],
    )

    worker = LifecycleEventWorker(
        ops_db_path=db,
        processor=MagicMock(),
        chain_repo=TradeChainRepository(db),
        event_repo=LifecycleEventRepository(db),
        command_repo=ExecutionCommandRepository(db),
        exchange_event_repo=MagicMock(),
    )
    worker._persist_result(chain_id, result)

    # Verificare che siano stati inseriti 2 comandi espansi
    conn2 = _sqlite3.connect(db)
    rows = conn2.execute(
        "SELECT payload_json, idempotency_key FROM ops_execution_commands "
        "WHERE command_type='CANCEL_PENDING_ENTRY' ORDER BY command_id"
    ).fetchall()
    conn2.close()

    assert len(rows) == 2, f"Attesi 2 comandi espansi, trovati {len(rows)}"
    coids_in_payload = [_json.loads(r[0]).get("entry_client_order_id") for r in rows]
    assert f"tsb:{chain_id}:100:entry:2" in coids_in_payload
    assert f"tsb:{chain_id}:101:entry:3" in coids_in_payload


def test_timeout_worker_ignores_future_timeout(dbs):
    from datetime import datetime, timedelta, timezone
    _, ops_db = dbs
    future_timeout = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
    _make_chain_in_db(ops_db, trade_chain_id_hint=51, state="WAITING_ENTRY",
                       timeout_at_isoformat=future_timeout)

    from src.runtime_v2.lifecycle.repositories import TradeChainRepository
    from src.runtime_v2.lifecycle.workers import TimeoutWorker
    worker = TimeoutWorker(ops_db_path=ops_db, chain_repo=TradeChainRepository(ops_db))
    count = worker.run_once()
    assert count == 0


def test_timeout_worker_idempotent(dbs):
    import sqlite3
    from datetime import datetime, timedelta, timezone
    _, ops_db = dbs
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    chain_id = _make_chain_in_db(ops_db, trade_chain_id_hint=52, state="WAITING_ENTRY",
                                  timeout_at_isoformat=past)

    from src.runtime_v2.lifecycle.repositories import TradeChainRepository
    from src.runtime_v2.lifecycle.workers import TimeoutWorker
    worker = TimeoutWorker(ops_db_path=ops_db, chain_repo=TradeChainRepository(ops_db))
    worker.run_once()
    worker.run_once()  # segundo run non trova WAITING_ENTRY scaduto

    conn = sqlite3.connect(ops_db)
    cmd_count = conn.execute(
        "SELECT COUNT(*) FROM ops_execution_commands WHERE trade_chain_id=?", (chain_id,)
    ).fetchone()[0]
    conn.close()
    assert cmd_count == 1  # no duplicati


def test_lifecycle_event_worker_processes_tp_filled(dbs):
    import sqlite3
    import json
    from datetime import datetime, timezone
    _, ops_db = dbs
    chain_id = _make_chain_in_db(ops_db, trade_chain_id_hint=60, state="OPEN")

    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(ops_db)
    conn.execute(
        """
        INSERT INTO ops_exchange_events (trade_chain_id, event_type, payload_json,
            processing_status, idempotency_key, received_at)
        VALUES (?,?,?,?,?,?)
        """,
        (chain_id, "TP_FILLED", json.dumps({"tp_level": 1, "is_final": False}),
         "NEW", f"tp_filled:{chain_id}:1", now),
    )
    conn.commit()
    conn.close()

    from src.runtime_v2.lifecycle.event_processor import LifecycleEventProcessor
    from src.runtime_v2.lifecycle.repositories import (
        ExecutionCommandRepository, ExchangeEventRepository,
        LifecycleEventRepository, TradeChainRepository,
    )
    from src.runtime_v2.lifecycle.workers import LifecycleEventWorker

    worker = LifecycleEventWorker(
        ops_db_path=ops_db,
        processor=LifecycleEventProcessor(),
        chain_repo=TradeChainRepository(ops_db),
        event_repo=LifecycleEventRepository(ops_db),
        command_repo=ExecutionCommandRepository(ops_db),
        exchange_event_repo=ExchangeEventRepository(ops_db),
    )
    count = worker.run_once()
    assert count == 1

    conn = sqlite3.connect(ops_db)
    state = conn.execute(
        "SELECT lifecycle_state FROM ops_trade_chains WHERE trade_chain_id=?", (chain_id,)
    ).fetchone()[0]
    status = conn.execute(
        "SELECT processing_status FROM ops_exchange_events WHERE trade_chain_id=?", (chain_id,)
    ).fetchone()[0]
    conn.close()
    assert state == "PARTIALLY_CLOSED"
    assert status == "DONE"


def test_lifecycle_event_worker_marks_filled_leg_in_plan_state(dbs):
    import sqlite3
    import json
    from datetime import datetime, timezone
    _, ops_db = dbs
    plan_state = json.dumps({
        "plan_version": 1,
        "rebuild_policy": "NONE",
        "protection_policy": "TPSL_ATTACHED_FIRST_LEG",
        "stop_loss": 49000.0,
        "final_tp": 51000.0,
        "intermediate_tps": [],
        "legs": [{
            "leg_id": "leg_1",
            "sequence": 1,
            "entry_type": "LIMIT",
            "price": 50000.0,
            "risk_budget": 100.0,
            "qty": 0.01,
            "qty_mode": "fixed",
            "status": "PENDING",
            "client_order_id": "place_entry_attached:60:leg1",
        }],
    })
    chain_id = _make_chain_in_db(ops_db, trade_chain_id_hint=61, state="WAITING_ENTRY")

    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(ops_db)
    conn.execute(
        """
        UPDATE ops_trade_chains
        SET risk_snapshot_json=?, plan_state_json=?
        WHERE trade_chain_id=?
        """,
        (json.dumps({"sl_price": 49000.0, "risk_amount": 100.0}), plan_state, chain_id),
    )
    conn.execute(
        """
        INSERT INTO ops_exchange_events (trade_chain_id, event_type, payload_json,
            processing_status, idempotency_key, received_at)
        VALUES (?,?,?,?,?,?)
        """,
        (
            chain_id,
            "ENTRY_FILLED",
            json.dumps({
                "fill_price": 50000.0,
                "filled_qty": 0.01,
                "entry_client_order_id": "place_entry_attached:60:leg1",
            }),
            "NEW",
            f"entry_filled:{chain_id}:1",
            now,
        ),
    )
    conn.commit()
    conn.close()

    from src.runtime_v2.lifecycle.event_processor import LifecycleEventProcessor
    from src.runtime_v2.lifecycle.repositories import (
        ExecutionCommandRepository, ExchangeEventRepository,
        LifecycleEventRepository, TradeChainRepository,
    )
    from src.runtime_v2.lifecycle.workers import LifecycleEventWorker

    worker = LifecycleEventWorker(
        ops_db_path=ops_db,
        processor=LifecycleEventProcessor(),
        chain_repo=TradeChainRepository(ops_db),
        event_repo=LifecycleEventRepository(ops_db),
        command_repo=ExecutionCommandRepository(ops_db),
        exchange_event_repo=ExchangeEventRepository(ops_db),
    )
    count = worker.run_once()
    assert count == 1

    conn = sqlite3.connect(ops_db)
    persisted = conn.execute(
        "SELECT plan_state_json FROM ops_trade_chains WHERE trade_chain_id=?", (chain_id,)
    ).fetchone()[0]
    conn.close()
    persisted_plan = json.loads(persisted)
    assert persisted_plan["legs"][0]["status"] == "FILLED"


def test_lifecycle_event_worker_uses_command_id_to_mark_filled_leg(dbs):
    import sqlite3
    import json
    from datetime import datetime, timezone
    _, ops_db = dbs
    leg2_client_order_id = "tsb:63:2:entry:1:test"
    plan_state = json.dumps({
        "plan_version": 1,
        "rebuild_policy": "NONE",
        "protection_policy": "TPSL_ATTACHED_FIRST_LEG",
        "stop_loss": 49000.0,
        "final_tp": 51000.0,
        "intermediate_tps": [],
        "legs": [
            {
                "leg_id": "leg_1",
                "sequence": 1,
                "entry_type": "LIMIT",
                "price": 50000.0,
                "risk_budget": 50.0,
                "qty": 0.005,
                "qty_mode": "fixed",
                "status": "PENDING",
                "client_order_id": "place_entry_attached:63:leg1",
            },
            {
                "leg_id": "leg_2",
                "sequence": 2,
                "entry_type": "LIMIT",
                "price": 48000.0,
                "risk_budget": 50.0,
                "qty": 0.0167,
                "qty_mode": "fixed",
                "status": "PENDING",
                "client_order_id": leg2_client_order_id,
            },
        ],
    })
    chain_id = _make_chain_in_db(ops_db, trade_chain_id_hint=63, state="WAITING_ENTRY")

    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(ops_db)
    conn.execute(
        """
        UPDATE ops_trade_chains
        SET risk_snapshot_json=?, plan_state_json=?
        WHERE trade_chain_id=?
        """,
        (json.dumps({"sl_price": 49000.0, "risk_amount": 100.0}), plan_state, chain_id),
    )
    command_id = conn.execute(
        """
        INSERT INTO ops_execution_commands (
            trade_chain_id, command_type, status, payload_json,
            idempotency_key, client_order_id, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?)
        """,
        (
            chain_id,
            "PLACE_ENTRY",
            "DONE",
            json.dumps({"symbol": "BTC/USDT", "side": "LONG"}),
            f"place_entry:{chain_id}:2",
            leg2_client_order_id,
            now,
            now,
        ),
    ).lastrowid
    conn.execute(
        """
        INSERT INTO ops_exchange_events (trade_chain_id, event_type, payload_json,
            processing_status, idempotency_key, received_at)
        VALUES (?,?,?,?,?,?)
        """,
        (
            chain_id,
            "ENTRY_FILLED",
            json.dumps({
                "fill_price": 48000.0,
                "filled_qty": 0.0167,
                "command_id": command_id,
            }),
            "NEW",
            f"entry_filled:{chain_id}:command:{command_id}",
            now,
        ),
    )
    conn.commit()
    conn.close()

    from src.runtime_v2.lifecycle.event_processor import LifecycleEventProcessor
    from src.runtime_v2.lifecycle.repositories import (
        ExecutionCommandRepository, ExchangeEventRepository,
        LifecycleEventRepository, TradeChainRepository,
    )
    from src.runtime_v2.lifecycle.workers import LifecycleEventWorker

    worker = LifecycleEventWorker(
        ops_db_path=ops_db,
        processor=LifecycleEventProcessor(),
        chain_repo=TradeChainRepository(ops_db),
        event_repo=LifecycleEventRepository(ops_db),
        command_repo=ExecutionCommandRepository(ops_db),
        exchange_event_repo=ExchangeEventRepository(ops_db),
    )
    count = worker.run_once()
    assert count == 1

    conn = sqlite3.connect(ops_db)
    persisted = conn.execute(
        "SELECT plan_state_json FROM ops_trade_chains WHERE trade_chain_id=?", (chain_id,)
    ).fetchone()[0]
    conn.close()
    persisted_plan = json.loads(persisted)
    leg1 = next(leg for leg in persisted_plan["legs"] if leg["leg_id"] == "leg_1")
    leg2 = next(leg for leg in persisted_plan["legs"] if leg["leg_id"] == "leg_2")
    assert leg1["status"] == "PENDING"
    assert leg2["status"] == "FILLED"


def test_lifecycle_event_worker_uses_command_payload_when_plan_has_logical_leg_ids(dbs):
    import sqlite3
    import json
    from datetime import datetime, timezone
    _, ops_db = dbs
    actual_client_order_id = "tsb:65:2:entry:1:test"
    plan_state = json.dumps({
        "plan_version": 1,
        "rebuild_policy": "NONE",
        "protection_policy": "TPSL_ATTACHED_FIRST_LEG",
        "stop_loss": 49000.0,
        "final_tp": 51000.0,
        "intermediate_tps": [],
        "legs": [
            {
                "leg_id": "leg_1",
                "sequence": 1,
                "entry_type": "LIMIT",
                "price": 50000.0,
                "risk_budget": 50.0,
                "qty": 0.005,
                "qty_mode": "fixed",
                "status": "PENDING",
                "client_order_id": "place_entry_attached:65:leg1",
            },
            {
                "leg_id": "leg_2",
                "sequence": 2,
                "entry_type": "LIMIT",
                "price": 48000.0,
                "risk_budget": 50.0,
                "qty": 0.0167,
                "qty_mode": "fixed",
                "status": "PENDING",
                "client_order_id": "place_entry:65:leg2",
            },
        ],
    })
    chain_id = _make_chain_in_db(ops_db, trade_chain_id_hint=65, state="WAITING_ENTRY")

    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(ops_db)
    conn.execute(
        """
        UPDATE ops_trade_chains
        SET risk_snapshot_json=?, plan_state_json=?
        WHERE trade_chain_id=?
        """,
        (json.dumps({"sl_price": 49000.0, "risk_amount": 100.0}), plan_state, chain_id),
    )
    command_id = conn.execute(
        """
        INSERT INTO ops_execution_commands (
            trade_chain_id, command_type, status, payload_json,
            idempotency_key, client_order_id, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?)
        """,
        (
            chain_id,
            "PLACE_ENTRY",
            "DONE",
            json.dumps({
                "symbol": "BTC/USDT",
                "side": "LONG",
                "entry_type": "LIMIT",
                "price": 48000.0,
                "qty": 0.0167,
            }),
            f"place_entry:{chain_id}:2",
            actual_client_order_id,
            now,
            now,
        ),
    ).lastrowid
    conn.execute(
        """
        INSERT INTO ops_exchange_events (trade_chain_id, event_type, payload_json,
            processing_status, idempotency_key, received_at)
        VALUES (?,?,?,?,?,?)
        """,
        (
            chain_id,
            "ENTRY_FILLED",
            json.dumps({
                "fill_price": 48000.0,
                "filled_qty": 0.0167,
                "command_id": command_id,
            }),
            "NEW",
            f"entry_filled:{chain_id}:command:{command_id}",
            now,
        ),
    )
    conn.commit()
    conn.close()

    from src.runtime_v2.lifecycle.event_processor import LifecycleEventProcessor
    from src.runtime_v2.lifecycle.repositories import (
        ExecutionCommandRepository, ExchangeEventRepository,
        LifecycleEventRepository, TradeChainRepository,
    )
    from src.runtime_v2.lifecycle.workers import LifecycleEventWorker

    worker = LifecycleEventWorker(
        ops_db_path=ops_db,
        processor=LifecycleEventProcessor(),
        chain_repo=TradeChainRepository(ops_db),
        event_repo=LifecycleEventRepository(ops_db),
        command_repo=ExecutionCommandRepository(ops_db),
        exchange_event_repo=ExchangeEventRepository(ops_db),
    )
    count = worker.run_once()
    assert count == 1

    conn = sqlite3.connect(ops_db)
    persisted = conn.execute(
        "SELECT plan_state_json FROM ops_trade_chains WHERE trade_chain_id=?", (chain_id,)
    ).fetchone()[0]
    conn.close()
    persisted_plan = json.loads(persisted)
    leg1 = next(leg for leg in persisted_plan["legs"] if leg["leg_id"] == "leg_1")
    leg2 = next(leg for leg in persisted_plan["legs"] if leg["leg_id"] == "leg_2")
    assert leg1["status"] == "PENDING"
    assert leg2["status"] == "FILLED"


def test_lifecycle_event_worker_does_not_guess_filled_leg_when_multiple_pending(dbs):
    import sqlite3
    import json
    from datetime import datetime, timezone
    _, ops_db = dbs
    plan_state = json.dumps({
        "plan_version": 1,
        "rebuild_policy": "NONE",
        "protection_policy": "TPSL_ATTACHED_FIRST_LEG",
        "stop_loss": 49000.0,
        "final_tp": 51000.0,
        "intermediate_tps": [],
        "legs": [
            {"leg_id": "leg_1", "sequence": 1, "entry_type": "LIMIT", "price": 50000.0,
             "risk_budget": 50.0, "qty": 0.005, "qty_mode": "fixed", "status": "PENDING",
             "client_order_id": "place_entry_attached:64:leg1"},
            {"leg_id": "leg_2", "sequence": 2, "entry_type": "LIMIT", "price": 48000.0,
             "risk_budget": 50.0, "qty": 0.0167, "qty_mode": "fixed", "status": "PENDING",
             "client_order_id": "place_entry:64:leg2"},
        ],
    })
    chain_id = _make_chain_in_db(ops_db, trade_chain_id_hint=64, state="WAITING_ENTRY")

    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(ops_db)
    conn.execute(
        "UPDATE ops_trade_chains SET risk_snapshot_json=?, plan_state_json=? WHERE trade_chain_id=?",
        (json.dumps({"sl_price": 49000.0, "risk_amount": 100.0}), plan_state, chain_id),
    )
    conn.execute(
        """
        INSERT INTO ops_exchange_events (trade_chain_id, event_type, payload_json,
            processing_status, idempotency_key, received_at)
        VALUES (?,?,?,?,?,?)
        """,
        (
            chain_id,
            "ENTRY_FILLED",
            json.dumps({"fill_price": 48000.0, "filled_qty": 0.0167}),
            "NEW",
            f"entry_filled:{chain_id}:unknown",
            now,
        ),
    )
    conn.commit()
    conn.close()

    from src.runtime_v2.lifecycle.event_processor import LifecycleEventProcessor
    from src.runtime_v2.lifecycle.repositories import (
        ExecutionCommandRepository, ExchangeEventRepository,
        LifecycleEventRepository, TradeChainRepository,
    )
    from src.runtime_v2.lifecycle.workers import LifecycleEventWorker

    worker = LifecycleEventWorker(
        ops_db_path=ops_db,
        processor=LifecycleEventProcessor(),
        chain_repo=TradeChainRepository(ops_db),
        event_repo=LifecycleEventRepository(ops_db),
        command_repo=ExecutionCommandRepository(ops_db),
        exchange_event_repo=ExchangeEventRepository(ops_db),
    )
    count = worker.run_once()
    assert count == 1

    conn = sqlite3.connect(ops_db)
    persisted = conn.execute(
        "SELECT plan_state_json FROM ops_trade_chains WHERE trade_chain_id=?", (chain_id,)
    ).fetchone()[0]
    conn.close()
    persisted_plan = json.loads(persisted)
    assert [leg["status"] for leg in persisted_plan["legs"]] == ["PENDING", "PENDING"]


def test_lifecycle_event_worker_marks_cancelled_leg_in_plan_state(dbs):
    import sqlite3
    import json
    from datetime import datetime, timezone
    _, ops_db = dbs
    client_order_id = "place_entry:62:leg2"
    plan_state = json.dumps({
        "plan_version": 1,
        "rebuild_policy": "NONE",
        "protection_policy": "TPSL_ATTACHED_FIRST_LEG",
        "stop_loss": 49000.0,
        "final_tp": 51000.0,
        "intermediate_tps": [],
        "legs": [
            {
                "leg_id": "leg_1",
                "sequence": 1,
                "entry_type": "LIMIT",
                "price": 50000.0,
                "risk_budget": 50.0,
                "qty": 0.005,
                "qty_mode": "fixed",
                "status": "FILLED",
                "client_order_id": "place_entry_attached:62:leg1",
            },
            {
                "leg_id": "leg_2",
                "sequence": 2,
                "entry_type": "LIMIT",
                "price": 48000.0,
                "risk_budget": 50.0,
                "qty": 0.0167,
                "qty_mode": "fixed",
                "status": "PENDING",
                "client_order_id": client_order_id,
            },
        ],
    })
    chain_id = _make_chain_in_db(ops_db, trade_chain_id_hint=62, state="OPEN")

    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(ops_db)
    conn.execute(
        "UPDATE ops_trade_chains SET plan_state_json=? WHERE trade_chain_id=?",
        (plan_state, chain_id),
    )
    conn.execute(
        """
        INSERT INTO ops_exchange_events (trade_chain_id, event_type, payload_json,
            processing_status, idempotency_key, received_at)
        VALUES (?,?,?,?,?,?)
        """,
        (
            chain_id,
            "PENDING_ENTRY_CANCELLED_CONFIRMED",
            json.dumps({
                "cancelled_order_ids": [client_order_id],
                "cancelled_pending_qty": 0.0167,
                "position_already_open": True,
            }),
            "NEW",
            f"cancel_confirmed:{chain_id}:1",
            now,
        ),
    )
    conn.commit()
    conn.close()

    from src.runtime_v2.lifecycle.event_processor import LifecycleEventProcessor
    from src.runtime_v2.lifecycle.repositories import (
        ExecutionCommandRepository, ExchangeEventRepository,
        LifecycleEventRepository, TradeChainRepository,
    )
    from src.runtime_v2.lifecycle.workers import LifecycleEventWorker

    worker = LifecycleEventWorker(
        ops_db_path=ops_db,
        processor=LifecycleEventProcessor(),
        chain_repo=TradeChainRepository(ops_db),
        event_repo=LifecycleEventRepository(ops_db),
        command_repo=ExecutionCommandRepository(ops_db),
        exchange_event_repo=ExchangeEventRepository(ops_db),
    )
    count = worker.run_once()
    assert count == 1

    conn = sqlite3.connect(ops_db)
    persisted = conn.execute(
        "SELECT plan_state_json FROM ops_trade_chains WHERE trade_chain_id=?", (chain_id,)
    ).fetchone()[0]
    conn.close()
    persisted_plan = json.loads(persisted)
    leg2 = next(leg for leg in persisted_plan["legs"] if leg["leg_id"] == "leg_2")
    assert leg2["status"] == "CANCELLED"


def test_lifecycle_event_worker_market_convert_cancel_mode_old_cancel_then_fill_keeps_chain_open(dbs):
    _, ops_db = dbs
    chain_id = _make_chain_in_db(ops_db, trade_chain_id_hint=70, state="WAITING_ENTRY", symbol="BASEDUSDT")
    plan_state = json.dumps({
        "plan_version": 1,
        "rebuild_policy": "ON_EACH_ENTRY_FILL",
        "protection_policy": "TPSL_ATTACHED_FIRST_LEG",
        "stop_loss": 0.0615,
        "final_tp": 0.07993,
        "intermediate_tps": [0.07402, 0.07575],
        "legs": [
            {
                "leg_id": "leg_1",
                "sequence": 1,
                "entry_type": "MARKET",
                "price": None,
                "risk_budget": 70.0,
                "qty": None,
                "qty_mode": "deferred_market",
                "weight": 0.7,
                "status": "PENDING",
                "client_order_id": "cid_replacement_leg1",
            },
            {
                "leg_id": "leg_2",
                "sequence": 2,
                "entry_type": "LIMIT",
                "price": 0.0636,
                "risk_budget": 30.0,
                "qty": 14285.714285714253,
                "qty_mode": "fixed",
                "weight": 0.3,
                "status": "CANCELLED",
                "client_order_id": "cid_leg2",
            },
        ],
    })
    now_1 = datetime.now(timezone.utc).isoformat()
    now_2 = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(ops_db)
    conn.execute(
        """
        UPDATE ops_trade_chains
        SET risk_snapshot_json=?, plan_state_json=?
        WHERE trade_chain_id=?
        """,
        (json.dumps({"sl_price": 0.0615, "risk_amount": 100.0}), plan_state, chain_id),
    )
    conn.execute(
        """
        INSERT INTO ops_exchange_events (trade_chain_id, event_type, payload_json,
            processing_status, idempotency_key, received_at)
        VALUES (?,?,?,?,?,?)
        """,
        (
            chain_id,
            "PENDING_ENTRY_CANCELLED_CONFIRMED",
            json.dumps({
                "cancelled_order_ids": ["cid_old_leg1"],
                "sequence": 1,
                "position_already_open": False,
            }),
            "NEW",
            f"cancel_confirmed:{chain_id}:convert",
            now_1,
        ),
    )
    conn.execute(
        """
        INSERT INTO ops_exchange_events (trade_chain_id, event_type, payload_json,
            processing_status, idempotency_key, received_at)
        VALUES (?,?,?,?,?,?)
        """,
        (
            chain_id,
            "ENTRY_FILLED",
            json.dumps({
                "fill_price": 0.07014,
                "filled_qty": 11600.0,
                "entry_client_order_id": "cid_replacement_leg1",
            }),
            "NEW",
            f"entry_filled:{chain_id}:convert",
            now_2,
        ),
    )
    conn.commit()
    conn.close()

    from src.runtime_v2.lifecycle.event_processor import LifecycleEventProcessor
    from src.runtime_v2.lifecycle.repositories import (
        ExecutionCommandRepository, ExchangeEventRepository,
        LifecycleEventRepository, TradeChainRepository,
    )
    from src.runtime_v2.lifecycle.workers import LifecycleEventWorker

    worker = LifecycleEventWorker(
        ops_db_path=ops_db,
        processor=LifecycleEventProcessor(),
        chain_repo=TradeChainRepository(ops_db),
        event_repo=LifecycleEventRepository(ops_db),
        command_repo=ExecutionCommandRepository(ops_db),
        exchange_event_repo=ExchangeEventRepository(ops_db),
    )
    count = worker.run_once()
    assert count == 2

    conn = sqlite3.connect(ops_db)
    state, filled_qty, open_qty, persisted = conn.execute(
        "SELECT lifecycle_state, filled_entry_qty, open_position_qty, plan_state_json "
        "FROM ops_trade_chains WHERE trade_chain_id=?",
        (chain_id,),
    ).fetchone()
    rebuild_count = conn.execute(
        "SELECT COUNT(*) FROM ops_execution_commands WHERE trade_chain_id=? AND command_type='REBUILD_PARTIAL_TPS'",
        (chain_id,),
    ).fetchone()[0]
    conn.close()

    persisted_plan = json.loads(persisted)
    leg1 = next(leg for leg in persisted_plan["legs"] if leg["leg_id"] == "leg_1")
    leg2 = next(leg for leg in persisted_plan["legs"] if leg["leg_id"] == "leg_2")
    assert state == "OPEN"
    assert filled_qty == pytest.approx(11600.0)
    assert open_qty == pytest.approx(11600.0)
    assert leg1["status"] == "FILLED"
    assert leg2["status"] == "CANCELLED"
    assert rebuild_count == 1


def test_lifecycle_event_worker_market_convert_keep_mode_old_cancel_then_fill_preserves_other_pending_leg(dbs):
    _, ops_db = dbs
    chain_id = _make_chain_in_db(ops_db, trade_chain_id_hint=71, state="WAITING_ENTRY", symbol="BASEDUSDT")
    plan_state = json.dumps({
        "plan_version": 1,
        "rebuild_policy": "ON_EACH_ENTRY_FILL",
        "protection_policy": "TPSL_ATTACHED_FIRST_LEG",
        "stop_loss": 0.0615,
        "final_tp": 0.07993,
        "intermediate_tps": [0.07402, 0.07575],
        "legs": [
            {
                "leg_id": "leg_1",
                "sequence": 1,
                "entry_type": "MARKET",
                "price": None,
                "risk_budget": 70.0,
                "qty": None,
                "qty_mode": "deferred_market",
                "weight": 0.7,
                "status": "PENDING",
                "client_order_id": "cid_replacement_leg1",
            },
            {
                "leg_id": "leg_2",
                "sequence": 2,
                "entry_type": "LIMIT",
                "price": 0.0636,
                "risk_budget": 30.0,
                "qty": 14285.714285714253,
                "qty_mode": "fixed",
                "weight": 0.3,
                "status": "PENDING",
                "client_order_id": "cid_leg2",
            },
        ],
    })
    now_1 = datetime.now(timezone.utc).isoformat()
    now_2 = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(ops_db)
    conn.execute(
        "UPDATE ops_trade_chains SET risk_snapshot_json=?, plan_state_json=? WHERE trade_chain_id=?",
        (json.dumps({"sl_price": 0.0615, "risk_amount": 100.0}), plan_state, chain_id),
    )
    conn.execute(
        """
        INSERT INTO ops_exchange_events (trade_chain_id, event_type, payload_json,
            processing_status, idempotency_key, received_at)
        VALUES (?,?,?,?,?,?)
        """,
        (
            chain_id,
            "PENDING_ENTRY_CANCELLED_CONFIRMED",
            json.dumps({
                "cancelled_order_ids": ["cid_old_leg1"],
                "sequence": 1,
                "position_already_open": False,
            }),
            "NEW",
            f"cancel_confirmed:{chain_id}:keep",
            now_1,
        ),
    )
    conn.execute(
        """
        INSERT INTO ops_exchange_events (trade_chain_id, event_type, payload_json,
            processing_status, idempotency_key, received_at)
        VALUES (?,?,?,?,?,?)
        """,
        (
            chain_id,
            "ENTRY_FILLED",
            json.dumps({
                "fill_price": 0.07014,
                "filled_qty": 11600.0,
                "entry_client_order_id": "cid_replacement_leg1",
            }),
            "NEW",
            f"entry_filled:{chain_id}:keep",
            now_2,
        ),
    )
    conn.commit()
    conn.close()

    from src.runtime_v2.lifecycle.event_processor import LifecycleEventProcessor
    from src.runtime_v2.lifecycle.repositories import (
        ExecutionCommandRepository, ExchangeEventRepository,
        LifecycleEventRepository, TradeChainRepository,
    )
    from src.runtime_v2.lifecycle.workers import LifecycleEventWorker

    worker = LifecycleEventWorker(
        ops_db_path=ops_db,
        processor=LifecycleEventProcessor(),
        chain_repo=TradeChainRepository(ops_db),
        event_repo=LifecycleEventRepository(ops_db),
        command_repo=ExecutionCommandRepository(ops_db),
        exchange_event_repo=ExchangeEventRepository(ops_db),
    )
    count = worker.run_once()
    assert count == 2

    conn = sqlite3.connect(ops_db)
    state, persisted = conn.execute(
        "SELECT lifecycle_state, plan_state_json FROM ops_trade_chains WHERE trade_chain_id=?",
        (chain_id,),
    ).fetchone()
    rebuild_count = conn.execute(
        "SELECT COUNT(*) FROM ops_execution_commands WHERE trade_chain_id=? AND command_type='REBUILD_PARTIAL_TPS'",
        (chain_id,),
    ).fetchone()[0]
    conn.close()

    persisted_plan = json.loads(persisted)
    leg1 = next(leg for leg in persisted_plan["legs"] if leg["leg_id"] == "leg_1")
    leg2 = next(leg for leg in persisted_plan["legs"] if leg["leg_id"] == "leg_2")
    assert state == "OPEN"
    assert leg1["status"] == "FILLED"
    assert leg2["status"] == "PENDING"
    assert rebuild_count == 1


def test_lifecycle_event_worker_market_convert_single_limit_old_cancel_then_fill_keeps_chain_open(dbs):
    _, ops_db = dbs
    chain_id = _make_chain_in_db(ops_db, trade_chain_id_hint=72, state="WAITING_ENTRY", symbol="BASEDUSDT")
    plan_state = json.dumps({
        "plan_version": 1,
        "rebuild_policy": "NONE",
        "protection_policy": "TPSL_ATTACHED_FIRST_LEG",
        "stop_loss": 0.0615,
        "final_tp": 0.07993,
        "intermediate_tps": [],
        "legs": [
            {
                "leg_id": "leg_1",
                "sequence": 1,
                "entry_type": "MARKET",
                "price": None,
                "risk_budget": 100.0,
                "qty": None,
                "qty_mode": "deferred_market",
                "weight": 1.0,
                "status": "PENDING",
                "client_order_id": "cid_replacement_leg1",
            },
        ],
    })
    now_1 = datetime.now(timezone.utc).isoformat()
    now_2 = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(ops_db)
    conn.execute(
        "UPDATE ops_trade_chains SET risk_snapshot_json=?, plan_state_json=? WHERE trade_chain_id=?",
        (json.dumps({"sl_price": 0.0615, "risk_amount": 100.0}), plan_state, chain_id),
    )
    conn.execute(
        """
        INSERT INTO ops_exchange_events (trade_chain_id, event_type, payload_json,
            processing_status, idempotency_key, received_at)
        VALUES (?,?,?,?,?,?)
        """,
        (
            chain_id,
            "PENDING_ENTRY_CANCELLED_CONFIRMED",
            json.dumps({
                "cancelled_order_ids": ["cid_old_leg1"],
                "sequence": 1,
                "position_already_open": False,
            }),
            "NEW",
            f"cancel_confirmed:{chain_id}:single",
            now_1,
        ),
    )
    conn.execute(
        """
        INSERT INTO ops_exchange_events (trade_chain_id, event_type, payload_json,
            processing_status, idempotency_key, received_at)
        VALUES (?,?,?,?,?,?)
        """,
        (
            chain_id,
            "ENTRY_FILLED",
            json.dumps({
                "fill_price": 0.07014,
                "filled_qty": 11600.0,
                "entry_client_order_id": "cid_replacement_leg1",
            }),
            "NEW",
            f"entry_filled:{chain_id}:single",
            now_2,
        ),
    )
    conn.commit()
    conn.close()

    from src.runtime_v2.lifecycle.event_processor import LifecycleEventProcessor
    from src.runtime_v2.lifecycle.repositories import (
        ExecutionCommandRepository, ExchangeEventRepository,
        LifecycleEventRepository, TradeChainRepository,
    )
    from src.runtime_v2.lifecycle.workers import LifecycleEventWorker

    worker = LifecycleEventWorker(
        ops_db_path=ops_db,
        processor=LifecycleEventProcessor(),
        chain_repo=TradeChainRepository(ops_db),
        event_repo=LifecycleEventRepository(ops_db),
        command_repo=ExecutionCommandRepository(ops_db),
        exchange_event_repo=ExchangeEventRepository(ops_db),
    )
    count = worker.run_once()
    assert count == 2

    conn = sqlite3.connect(ops_db)
    state, persisted = conn.execute(
        "SELECT lifecycle_state, plan_state_json FROM ops_trade_chains WHERE trade_chain_id=?",
        (chain_id,),
    ).fetchone()
    rebuild_count = conn.execute(
        "SELECT COUNT(*) FROM ops_execution_commands WHERE trade_chain_id=? AND command_type='REBUILD_PARTIAL_TPS'",
        (chain_id,),
    ).fetchone()[0]
    conn.close()

    persisted_plan = json.loads(persisted)
    leg1 = persisted_plan["legs"][0]
    assert state == "OPEN"
    assert leg1["status"] == "FILLED"
    assert rebuild_count == 0


def test_worker_accumulates_long_tp_pnl_and_fee(tmp_path):
    import json as _json
    import sqlite3 as _sqlite3
    from pathlib import Path
    from unittest.mock import MagicMock
    from src.runtime_v2.lifecycle.event_processor import EventProcessorResult
    from src.runtime_v2.lifecycle.models import LifecycleEvent
    from src.runtime_v2.lifecycle.workers import LifecycleEventWorker
    from src.runtime_v2.lifecycle.repositories import (
        ExecutionCommandRepository, ExchangeEventRepository,
        LifecycleEventRepository, TradeChainRepository,
    )

    db = str(tmp_path / "ops.sqlite3")
    conn = _sqlite3.connect(db)
    for f in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit()

    now_str = "2026-05-31T00:00:00+00:00"
    chain_id = 145
    conn.execute(
        "INSERT INTO ops_trade_chains "
        "(trade_chain_id, source_enrichment_id, canonical_message_id, raw_message_id, "
        "trader_id, account_id, symbol, side, lifecycle_state, entry_mode, "
        "entry_avg_price, open_position_qty, filled_entry_qty, "
        "be_protection_status, management_plan_json, plan_state_json, "
        "created_at, updated_at) "
        "VALUES (?,1,1,1,'t','main','BTC/USDT','LONG','OPEN','ONE_SHOT',"
        "65000.0,0.01,0.01,'NOT_PROTECTED','{}','{}',?,?)",
        (chain_id, now_str, now_str),
    )
    conn.commit()
    conn.close()

    result = EventProcessorResult(
        new_lifecycle_state="PARTIALLY_CLOSED",
        new_be_protection_status=None,
        entry_avg_price=None,
        current_stop_price=None,
        lifecycle_events=[
            LifecycleEvent(
                trade_chain_id=chain_id,
                event_type="TP_FILLED",
                source_type="exchange_event",
                payload_json=_json.dumps({
                    "tp_level": 1,
                    "is_final": False,
                    "fill_price": 68000.0,
                    "filled_qty": 0.002,
                    "exec_fee": 1.10,
                    "closed_size": 0.002,
                }),
                idempotency_key=f"tp:{chain_id}:1",
            )
        ],
        execution_commands=[],
        new_open_position_qty=0.008,
        new_closed_position_qty=0.002,
    )

    worker = LifecycleEventWorker(
        ops_db_path=db,
        processor=MagicMock(),
        chain_repo=TradeChainRepository(db),
        event_repo=LifecycleEventRepository(db),
        command_repo=ExecutionCommandRepository(db),
        exchange_event_repo=MagicMock(),
    )
    worker._persist_result(chain_id, result)

    conn2 = _sqlite3.connect(db)
    row = conn2.execute(
        "SELECT cumulative_gross_pnl, cumulative_fees FROM ops_trade_chains WHERE trade_chain_id=?",
        (chain_id,),
    ).fetchone()
    conn2.close()
    # LONG: (68000 - 65000) * 0.002 = 6.0
    assert row[0] == pytest.approx(6.0)
    assert row[1] == pytest.approx(1.10)


def test_worker_close_full_reconciliation_updates_cumulative_pnl_and_fees(tmp_path):
    import json as _json
    import sqlite3 as _sqlite3
    from pathlib import Path
    from unittest.mock import MagicMock

    from src.runtime_v2.lifecycle.event_processor import EventProcessorResult
    from src.runtime_v2.lifecycle.models import LifecycleEvent
    from src.runtime_v2.lifecycle.repositories import (
        ExecutionCommandRepository, LifecycleEventRepository, TradeChainRepository,
    )
    from src.runtime_v2.lifecycle.workers import LifecycleEventWorker

    db = str(tmp_path / "ops.sqlite3")
    conn = _sqlite3.connect(db)
    for f in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    now_str = "2026-06-06T00:00:00+00:00"
    chain_id = 302
    conn.execute(
        "INSERT INTO ops_trade_chains "
        "(trade_chain_id, source_enrichment_id, canonical_message_id, raw_message_id, "
        "trader_id, account_id, symbol, side, lifecycle_state, entry_mode, "
        "entry_avg_price, open_position_qty, filled_entry_qty, "
        "management_plan_json, plan_state_json, created_at, updated_at) "
        "VALUES (?,1,1,1,'t','main','BTC/USDT','LONG','OPEN','ONE_SHOT',"
        "65000.0,0.01,0.01,'{}','{}',?,?)",
        (chain_id, now_str, now_str),
    )
    conn.commit()
    conn.close()

    result = EventProcessorResult(
        new_lifecycle_state="CLOSED",
        new_be_protection_status=None,
        entry_avg_price=None,
        current_stop_price=None,
        lifecycle_events=[
            LifecycleEvent(
                trade_chain_id=chain_id,
                event_type="CLOSE_FULL_FILLED",
                source_type="exchange_event",
                payload_json=_json.dumps({
                    "fill_price": 64000.0,
                    "filled_qty": 0.01,
                    "exec_fee": 1.70,
                    "closed_size": 0.01,
                    "source": "position_reconciliation",
                }),
                idempotency_key=f"close:{chain_id}:1",
            )
        ],
        execution_commands=[],
        new_open_position_qty=0.0,
        new_closed_position_qty=0.01,
    )

    worker = LifecycleEventWorker(
        ops_db_path=db,
        processor=MagicMock(),
        chain_repo=TradeChainRepository(db),
        event_repo=LifecycleEventRepository(db),
        command_repo=ExecutionCommandRepository(db),
        exchange_event_repo=MagicMock(),
    )
    worker._persist_result(chain_id, result)

    conn2 = _sqlite3.connect(db)
    row = conn2.execute(
        "SELECT cumulative_gross_pnl, cumulative_fees FROM ops_trade_chains WHERE trade_chain_id=?",
        (chain_id,),
    ).fetchone()
    conn2.close()
    assert row[0] == pytest.approx(-10.0)
    assert row[1] == pytest.approx(1.70)


def test_worker_entry_fill_sets_peak_margin_used(tmp_path):
    import json as _json
    import sqlite3 as _sqlite3
    from pathlib import Path
    from unittest.mock import MagicMock

    from src.runtime_v2.lifecycle.event_processor import EventProcessorResult
    from src.runtime_v2.lifecycle.models import LifecycleEvent
    from src.runtime_v2.lifecycle.repositories import (
        ExecutionCommandRepository, LifecycleEventRepository, TradeChainRepository,
    )
    from src.runtime_v2.lifecycle.workers import LifecycleEventWorker

    db = str(tmp_path / "ops.sqlite3")
    conn = _sqlite3.connect(db)
    for f in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    now_str = "2026-06-06T00:00:00+00:00"
    chain_id = 301
    conn.execute(
        "INSERT INTO ops_trade_chains "
        "(trade_chain_id, source_enrichment_id, canonical_message_id, raw_message_id, "
        "trader_id, account_id, symbol, side, lifecycle_state, entry_mode, "
        "risk_snapshot_json, created_at, updated_at) "
        "VALUES (?,1,1,1,'t','main','BTC/USDT','LONG','WAITING_ENTRY','ONE_SHOT',?,?,?)",
        (chain_id, _json.dumps({"leverage": 5, "risk_amount": 100.0}), now_str, now_str),
    )
    conn.commit()
    conn.close()

    result = EventProcessorResult(
        new_lifecycle_state="OPEN",
        new_be_protection_status=None,
        entry_avg_price=65000.0,
        current_stop_price=None,
        lifecycle_events=[LifecycleEvent(
            trade_chain_id=chain_id,
            event_type="ENTRY_FILLED",
            source_type="exchange_event",
            payload_json=_json.dumps({"fill_price": 65000.0, "filled_qty": 0.01}),
            idempotency_key=f"entry:{chain_id}:1",
        )],
        execution_commands=[],
        new_filled_entry_qty=0.01,
        new_open_position_qty=0.01,
    )

    worker = LifecycleEventWorker(
        ops_db_path=db,
        processor=MagicMock(),
        chain_repo=TradeChainRepository(db),
        event_repo=LifecycleEventRepository(db),
        command_repo=ExecutionCommandRepository(db),
        exchange_event_repo=MagicMock(),
    )
    worker._persist_result(chain_id, result)

    conn2 = _sqlite3.connect(db)
    peak = conn2.execute(
        "SELECT peak_margin_used FROM ops_trade_chains WHERE trade_chain_id=?",
        (chain_id,),
    ).fetchone()[0]
    conn2.close()
    assert peak == pytest.approx(130.0)


def test_worker_partial_close_does_not_reduce_peak_margin_used(tmp_path):
    import json as _json
    import sqlite3 as _sqlite3
    from pathlib import Path
    from unittest.mock import MagicMock

    from src.runtime_v2.lifecycle.event_processor import EventProcessorResult
    from src.runtime_v2.lifecycle.models import LifecycleEvent
    from src.runtime_v2.lifecycle.repositories import (
        ExecutionCommandRepository, LifecycleEventRepository, TradeChainRepository,
    )
    from src.runtime_v2.lifecycle.workers import LifecycleEventWorker

    db = str(tmp_path / "ops.sqlite3")
    conn = _sqlite3.connect(db)
    for f in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    now_str = "2026-06-06T00:00:00+00:00"
    chain_id = 302
    conn.execute(
        "INSERT INTO ops_trade_chains "
        "(trade_chain_id, source_enrichment_id, canonical_message_id, raw_message_id, "
        "trader_id, account_id, symbol, side, lifecycle_state, entry_mode, "
        "entry_avg_price, open_position_qty, peak_margin_used, risk_snapshot_json, created_at, updated_at) "
        "VALUES (?,1,1,1,'t','main','BTC/USDT','LONG','OPEN','ONE_SHOT',65000.0,0.02,260.0,?,?,?)",
        (chain_id, _json.dumps({"leverage": 5, "risk_amount": 100.0}), now_str, now_str),
    )
    conn.commit()
    conn.close()

    result = EventProcessorResult(
        new_lifecycle_state="PARTIALLY_CLOSED",
        new_be_protection_status=None,
        entry_avg_price=None,
        current_stop_price=None,
        lifecycle_events=[LifecycleEvent(
            trade_chain_id=chain_id,
            event_type="CLOSE_PARTIAL_FILLED",
            source_type="exchange_event",
            payload_json=_json.dumps({"fill_price": 65000.0, "filled_qty": 0.01, "closed_size": 0.01}),
            idempotency_key=f"partial_close:{chain_id}:1",
        )],
        execution_commands=[],
        new_open_position_qty=0.01,
        new_closed_position_qty=0.01,
    )

    worker = LifecycleEventWorker(
        ops_db_path=db,
        processor=MagicMock(),
        chain_repo=TradeChainRepository(db),
        event_repo=LifecycleEventRepository(db),
        command_repo=ExecutionCommandRepository(db),
        exchange_event_repo=MagicMock(),
    )
    worker._persist_result(chain_id, result)

    conn2 = _sqlite3.connect(db)
    peak = conn2.execute(
        "SELECT peak_margin_used FROM ops_trade_chains WHERE trade_chain_id=?",
        (chain_id,),
    ).fetchone()[0]
    conn2.close()
    assert peak == pytest.approx(260.0)


def test_worker_scale_in_raises_peak_margin_used(tmp_path):
    import json as _json
    import sqlite3 as _sqlite3
    from pathlib import Path
    from unittest.mock import MagicMock

    from src.runtime_v2.lifecycle.event_processor import EventProcessorResult
    from src.runtime_v2.lifecycle.models import LifecycleEvent
    from src.runtime_v2.lifecycle.repositories import (
        ExecutionCommandRepository, LifecycleEventRepository, TradeChainRepository,
    )
    from src.runtime_v2.lifecycle.workers import LifecycleEventWorker

    db = str(tmp_path / "ops.sqlite3")
    conn = _sqlite3.connect(db)
    for f in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    now_str = "2026-06-06T00:00:00+00:00"
    chain_id = 303
    conn.execute(
        "INSERT INTO ops_trade_chains "
        "(trade_chain_id, source_enrichment_id, canonical_message_id, raw_message_id, "
        "trader_id, account_id, symbol, side, lifecycle_state, entry_mode, "
        "entry_avg_price, open_position_qty, peak_margin_used, risk_snapshot_json, created_at, updated_at) "
        "VALUES (?,1,1,1,'t','main','BTC/USDT','LONG','OPEN','ONE_SHOT',65000.0,0.01,130.0,?,?,?)",
        (chain_id, _json.dumps({"leverage": 5, "risk_amount": 100.0}), now_str, now_str),
    )
    conn.commit()
    conn.close()

    result = EventProcessorResult(
        new_lifecycle_state="OPEN",
        new_be_protection_status=None,
        entry_avg_price=65500.0,
        current_stop_price=None,
        lifecycle_events=[LifecycleEvent(
            trade_chain_id=chain_id,
            event_type="ENTRY_UPDATED",
            source_type="exchange_event",
            payload_json=_json.dumps({"fill_price": 65500.0, "filled_qty": 0.02}),
            idempotency_key=f"scale_in:{chain_id}:1",
        )],
        execution_commands=[],
        new_filled_entry_qty=0.03,
        new_open_position_qty=0.03,
    )

    worker = LifecycleEventWorker(
        ops_db_path=db,
        processor=MagicMock(),
        chain_repo=TradeChainRepository(db),
        event_repo=LifecycleEventRepository(db),
        command_repo=ExecutionCommandRepository(db),
        exchange_event_repo=MagicMock(),
    )
    worker._persist_result(chain_id, result)

    conn2 = _sqlite3.connect(db)
    peak = conn2.execute(
        "SELECT peak_margin_used FROM ops_trade_chains WHERE trade_chain_id=?",
        (chain_id,),
    ).fetchone()[0]
    conn2.close()
    assert peak == pytest.approx(393.0)


def test_worker_close_full_keeps_historical_peak_margin_used(tmp_path):
    import json as _json
    import sqlite3 as _sqlite3
    from pathlib import Path
    from unittest.mock import MagicMock

    from src.runtime_v2.lifecycle.event_processor import EventProcessorResult
    from src.runtime_v2.lifecycle.models import ExecutionCommand, LifecycleEvent
    from src.runtime_v2.lifecycle.repositories import (
        ExecutionCommandRepository, LifecycleEventRepository, TradeChainRepository,
    )
    from src.runtime_v2.lifecycle.workers import LifecycleEventWorker

    db = str(tmp_path / "ops.sqlite3")
    conn = _sqlite3.connect(db)
    for f in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    now_str = "2026-06-06T00:00:00+00:00"
    chain_id = 304
    conn.execute(
        "INSERT INTO ops_trade_chains "
        "(trade_chain_id, source_enrichment_id, canonical_message_id, raw_message_id, "
        "trader_id, account_id, symbol, side, lifecycle_state, entry_mode, "
        "entry_avg_price, open_position_qty, peak_margin_used, risk_snapshot_json, created_at, updated_at) "
        "VALUES (?,1,1,1,'t','main','BTC/USDT','LONG','OPEN','ONE_SHOT',65000.0,0.02,260.0,?,?,?)",
        (chain_id, _json.dumps({"leverage": 5, "risk_amount": 100.0}), now_str, now_str),
    )
    conn.commit()
    conn.close()

    result = EventProcessorResult(
        new_lifecycle_state="CLOSED",
        new_be_protection_status=None,
        entry_avg_price=None,
        current_stop_price=None,
        lifecycle_events=[LifecycleEvent(
            trade_chain_id=chain_id,
            event_type="CLOSE_FULL_FILLED",
            source_type="exchange_event",
            payload_json=_json.dumps({"fill_price": 65000.0, "filled_qty": 0.02, "closed_size": 0.02}),
            idempotency_key=f"close_full:{chain_id}:1",
        )],
        execution_commands=[ExecutionCommand(
            trade_chain_id=chain_id,
            command_type="CANCEL_PENDING_ENTRY",
            payload_json=_json.dumps({"symbol": "BTC/USDT", "side": "LONG"}),
            idempotency_key=f"cancel_on_close:{chain_id}",
        )],
        new_open_position_qty=0.0,
        new_closed_position_qty=0.02,
    )

    worker = LifecycleEventWorker(
        ops_db_path=db,
        processor=MagicMock(),
        chain_repo=TradeChainRepository(db),
        event_repo=LifecycleEventRepository(db),
        command_repo=ExecutionCommandRepository(db),
        exchange_event_repo=MagicMock(),
    )
    worker._persist_result(chain_id, result)

    conn2 = _sqlite3.connect(db)
    peak = conn2.execute(
        "SELECT peak_margin_used FROM ops_trade_chains WHERE trade_chain_id=?",
        (chain_id,),
    ).fetchone()[0]
    conn2.close()
    assert peak == pytest.approx(260.0)


def test_worker_peak_margin_uses_post_event_leverage_from_result(tmp_path):
    import json as _json
    import sqlite3 as _sqlite3
    from pathlib import Path
    from unittest.mock import MagicMock

    from src.runtime_v2.lifecycle.event_processor import EventProcessorResult
    from src.runtime_v2.lifecycle.models import LifecycleEvent
    from src.runtime_v2.lifecycle.repositories import (
        ExecutionCommandRepository, LifecycleEventRepository, TradeChainRepository,
    )
    from src.runtime_v2.lifecycle.workers import LifecycleEventWorker

    db = str(tmp_path / "ops.sqlite3")
    conn = _sqlite3.connect(db)
    for f in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    now_str = "2026-06-06T00:00:00+00:00"
    chain_id = 305
    conn.execute(
        "INSERT INTO ops_trade_chains "
        "(trade_chain_id, source_enrichment_id, canonical_message_id, raw_message_id, "
        "trader_id, account_id, symbol, side, lifecycle_state, entry_mode, "
        "risk_snapshot_json, created_at, updated_at) "
        "VALUES (?,1,1,1,'t','main','BTC/USDT','LONG','WAITING_ENTRY','ONE_SHOT',?,?,?)",
        (chain_id, _json.dumps({"leverage": 10, "risk_amount": 100.0}), now_str, now_str),
    )
    conn.commit()
    conn.close()

    result = EventProcessorResult(
        new_lifecycle_state="OPEN",
        new_be_protection_status=None,
        entry_avg_price=65000.0,
        current_stop_price=None,
        lifecycle_events=[LifecycleEvent(
            trade_chain_id=chain_id,
            event_type="ENTRY_FILLED",
            source_type="exchange_event",
            payload_json=_json.dumps({"fill_price": 65000.0, "filled_qty": 0.01}),
            idempotency_key=f"entry:{chain_id}:1",
        )],
        execution_commands=[],
        new_filled_entry_qty=0.01,
        new_open_position_qty=0.01,
        new_risk_snapshot_json=_json.dumps({"leverage": 5, "risk_amount": 100.0}),
    )

    worker = LifecycleEventWorker(
        ops_db_path=db,
        processor=MagicMock(),
        chain_repo=TradeChainRepository(db),
        event_repo=LifecycleEventRepository(db),
        command_repo=ExecutionCommandRepository(db),
        exchange_event_repo=MagicMock(),
    )
    worker._persist_result(chain_id, result)

    conn2 = _sqlite3.connect(db)
    peak = conn2.execute(
        "SELECT peak_margin_used FROM ops_trade_chains WHERE trade_chain_id=?",
        (chain_id,),
    ).fetchone()[0]
    conn2.close()
    assert peak == pytest.approx(130.0)


def test_worker_accumulates_short_sl_pnl_negative(tmp_path):
    import json as _json
    import sqlite3 as _sqlite3
    from pathlib import Path
    from unittest.mock import MagicMock
    from src.runtime_v2.lifecycle.event_processor import EventProcessorResult
    from src.runtime_v2.lifecycle.models import LifecycleEvent
    from src.runtime_v2.lifecycle.workers import LifecycleEventWorker
    from src.runtime_v2.lifecycle.repositories import (
        ExecutionCommandRepository, ExchangeEventRepository,
        LifecycleEventRepository, TradeChainRepository,
    )

    db = str(tmp_path / "ops.sqlite3")
    conn = _sqlite3.connect(db)
    for f in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit()

    now_str = "2026-05-31T00:00:00+00:00"
    chain_id = 146
    conn.execute(
        "INSERT INTO ops_trade_chains "
        "(trade_chain_id, source_enrichment_id, canonical_message_id, raw_message_id, "
        "trader_id, account_id, symbol, side, lifecycle_state, entry_mode, "
        "entry_avg_price, open_position_qty, filled_entry_qty, "
        "be_protection_status, management_plan_json, plan_state_json, "
        "created_at, updated_at) "
        "VALUES (?,1,1,1,'t','main','ETH/USDT','SHORT','OPEN','ONE_SHOT',"
        "3000.0,0.5,0.5,'NOT_PROTECTED','{}','{}',?,?)",
        (chain_id, now_str, now_str),
    )
    conn.commit()
    conn.close()

    result = EventProcessorResult(
        new_lifecycle_state="CLOSED",
        new_be_protection_status=None,
        entry_avg_price=None,
        current_stop_price=None,
        lifecycle_events=[
            LifecycleEvent(
                trade_chain_id=chain_id,
                event_type="SL_FILLED",
                source_type="exchange_event",
                payload_json=_json.dumps({
                    "fill_price": 3050.0,
                    "filled_qty": 0.5,
                    "exec_fee": 2.0,
                    "closed_size": 0.5,
                }),
                idempotency_key=f"sl:{chain_id}:1",
            )
        ],
        execution_commands=[],
        new_open_position_qty=0.0,
        new_closed_position_qty=0.5,
    )

    worker = LifecycleEventWorker(
        ops_db_path=db,
        processor=MagicMock(),
        chain_repo=TradeChainRepository(db),
        event_repo=LifecycleEventRepository(db),
        command_repo=ExecutionCommandRepository(db),
        exchange_event_repo=MagicMock(),
    )
    worker._persist_result(chain_id, result)

    conn2 = _sqlite3.connect(db)
    row = conn2.execute(
        "SELECT cumulative_gross_pnl, cumulative_fees FROM ops_trade_chains WHERE trade_chain_id=?",
        (chain_id,),
    ).fetchone()
    conn2.close()
    # SHORT: (3000 - 3050) * 0.5 = -25.0
    assert row[0] == pytest.approx(-25.0)
    assert row[1] == pytest.approx(2.0)


def test_lifecycle_worker_funding_settled_stores_positive_exchange_fee_as_positive_cost(tmp_path):
    import sqlite3 as _sqlite3
    from pathlib import Path
    from unittest.mock import MagicMock

    from src.runtime_v2.lifecycle.event_processor import LifecycleEventProcessor
    from src.runtime_v2.lifecycle.repositories import (
        ExecutionCommandRepository, ExchangeEventRepository,
        LifecycleEventRepository, TradeChainRepository,
    )
    from src.runtime_v2.lifecycle.workers import LifecycleEventWorker

    db = str(tmp_path / "ops.sqlite3")
    conn = _sqlite3.connect(db)
    for f in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit()

    now_str = "2026-06-05T12:00:00+00:00"
    chain_id = 147
    conn.execute(
        "INSERT INTO ops_trade_chains "
        "(trade_chain_id, source_enrichment_id, canonical_message_id, raw_message_id, "
        "trader_id, account_id, symbol, side, lifecycle_state, entry_mode, "
        "entry_avg_price, open_position_qty, filled_entry_qty, cumulative_funding, "
        "be_protection_status, management_plan_json, plan_state_json, "
        "created_at, updated_at) "
        "VALUES (?,1,1,1,'t','main','ICNTUSDT','LONG','OPEN','ONE_SHOT',"
        "0.2532,6042.0,6042.0,0.0,'NOT_PROTECTED','{}','{}',?,?)",
        (chain_id, now_str, now_str),
    )
    conn.execute(
        """
        INSERT INTO ops_exchange_events (
            trade_chain_id, event_type, payload_json,
            processing_status, idempotency_key, received_at
        ) VALUES (?,?,?,?,?,?)
        """,
        (
            chain_id,
            "FUNDING_SETTLED",
            json.dumps({"exec_fee": 0.07628025, "source": "exchange_auto"}),
            "NEW",
            f"funding:{chain_id}:1",
            now_str,
        ),
    )
    conn.commit()
    conn.close()

    worker = LifecycleEventWorker(
        ops_db_path=db,
        processor=LifecycleEventProcessor(),
        chain_repo=TradeChainRepository(db),
        event_repo=LifecycleEventRepository(db),
        command_repo=ExecutionCommandRepository(db),
        exchange_event_repo=ExchangeEventRepository(db),
    )
    count = worker.run_once()
    assert count == 1

    conn2 = _sqlite3.connect(db)
    funding_row = conn2.execute(
        "SELECT cumulative_funding FROM ops_trade_chains WHERE trade_chain_id=?",
        (chain_id,),
    ).fetchone()
    status_row = conn2.execute(
        "SELECT processing_status FROM ops_exchange_events WHERE trade_chain_id=?",
        (chain_id,),
    ).fetchone()
    conn2.close()
    assert funding_row[0] == pytest.approx(0.07628025)
    assert status_row[0] == "DONE"
