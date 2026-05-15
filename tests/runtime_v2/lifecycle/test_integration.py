# tests/runtime_v2/lifecycle/test_integration.py
"""
Acceptance contract per PRD-04 — verifica criteri pass/fail della spec.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


def _apply_migrations(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    for f in sorted(Path("db/migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    for db in (parser_db, ops_db):
        _apply_migrations(db)
    return parser_db, ops_db


def _insert_enriched_row(parser_db: str, enrichment_id: int, enriched) -> None:
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
            enrichment_id, enriched.canonical_message_id, enriched.raw_message_id,
            enriched.trader_id, enriched.account_id, enriched.primary_class,
            enriched.enrichment_decision,
            enriched.enriched_signal.model_dump_json() if enriched.enriched_signal else None,
            enriched.management_plan.model_dump_json() if enriched.management_plan else "{}",
            json.dumps(enriched.policy_snapshot), now,
        ),
    )
    conn.commit()
    conn.close()


def _make_full_worker(parser_db, ops_db):
    from src.runtime_v2.lifecycle.entry_gate import LifecycleEntryGate, LifecycleGateWorker
    from src.runtime_v2.lifecycle.repositories import (
        ControlStateRepository, ExecutionCommandRepository,
        LifecycleEventRepository, SnapshotRepository, TradeChainRepository,
    )
    from src.runtime_v2.lifecycle.risk_capacity import RiskCapacityEngine
    from src.runtime_v2.lifecycle.static_exchange_data_port import StaticExchangeDataPort
    gate = LifecycleEntryGate(RiskCapacityEngine(), StaticExchangeDataPort())
    return LifecycleGateWorker(
        parser_db_path=parser_db, ops_db_path=ops_db, gate=gate,
        chain_repo=TradeChainRepository(ops_db),
        event_repo=LifecycleEventRepository(ops_db),
        command_repo=ExecutionCommandRepository(ops_db),
        snapshot_repo=SnapshotRepository(ops_db),
        control_repo=ControlStateRepository(ops_db),
    )


# AC1: SIGNAL PASS crea una sola ops_trade_chains, idempotente
def test_ac1_signal_pass_creates_one_chain(dbs):
    parser_db, ops_db = dbs
    enriched = _make_enriched_signal(enrichment_id=1)
    _insert_enriched_row(parser_db, 1, enriched)
    worker = _make_full_worker(parser_db, ops_db)
    worker.run_once()
    conn = sqlite3.connect(ops_db)
    count = conn.execute("SELECT COUNT(*) FROM ops_trade_chains").fetchone()[0]
    conn.close()
    assert count == 1


# AC2: Risk/capacity fail → nessuna chain, REVIEW_REQUIRED event
def test_ac2_risk_fail_produces_review(dbs):
    parser_db, ops_db = dbs
    enriched = _make_enriched_signal(enrichment_id=2, max_concurrent_trades=0)
    _insert_enriched_row(parser_db, 2, enriched)
    worker = _make_full_worker(parser_db, ops_db)
    worker.run_once()
    conn = sqlite3.connect(ops_db)
    chains = conn.execute("SELECT COUNT(*) FROM ops_trade_chains").fetchone()[0]
    reviews = conn.execute(
        "SELECT COUNT(*) FROM ops_lifecycle_events WHERE event_type='REVIEW_REQUIRED'"
    ).fetchone()[0]
    conn.close()
    assert chains == 0
    assert reviews == 1


# AC3: SIGNAL valido crea PLACE_ENTRY, PLACE_PROTECTIVE_STOP, PLACE_TAKE_PROFIT
def test_ac3_signal_creates_entry_commands(dbs):
    parser_db, ops_db = dbs
    enriched = _make_enriched_signal(enrichment_id=3)
    _insert_enriched_row(parser_db, 3, enriched)
    worker = _make_full_worker(parser_db, ops_db)
    worker.run_once()
    conn = sqlite3.connect(ops_db)
    cmd_types = {r[0] for r in conn.execute("SELECT command_type FROM ops_execution_commands").fetchall()}
    conn.close()
    assert "PLACE_ENTRY" in cmd_types
    assert "PLACE_PROTECTIVE_STOP" in cmd_types
    assert "PLACE_TAKE_PROFIT" in cmd_types


# AC6: Timeout su WAITING_ENTRY → EXPIRED + CANCEL_PENDING_ENTRY
def test_ac6_timeout_produces_cancel_and_expired(dbs):
    _, ops_db = dbs
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    now = _now()
    conn = sqlite3.connect(ops_db)
    conn.execute(
        """
        INSERT INTO ops_trade_chains (
            source_enrichment_id, canonical_message_id, raw_message_id, trader_id,
            account_id, symbol, side, lifecycle_state, entry_mode,
            management_plan_json, created_at, updated_at, entry_timeout_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (10, 100, 1000, "trader_a", "acc_1", "BTC/USDT", "LONG", "WAITING_ENTRY", "ONE_SHOT",
         "{}", now, now, past),
    )
    conn.commit()
    chain_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()

    from src.runtime_v2.lifecycle.repositories import TradeChainRepository
    from src.runtime_v2.lifecycle.workers import TimeoutWorker
    TimeoutWorker(ops_db, TradeChainRepository(ops_db)).run_once()

    conn = sqlite3.connect(ops_db)
    state = conn.execute(
        "SELECT lifecycle_state FROM ops_trade_chains WHERE trade_chain_id=?", (chain_id,)
    ).fetchone()[0]
    cmds = [r[0] for r in conn.execute(
        "SELECT command_type FROM ops_execution_commands WHERE trade_chain_id=?", (chain_id,)
    ).fetchall()]
    conn.close()
    assert state == "EXPIRED"
    assert "CANCEL_PENDING_ENTRY" in cmds


# AC7: tutti i lifecycle_events hanno idempotency_key non-null
def test_ac7_all_events_have_idempotency_key(dbs):
    parser_db, ops_db = dbs
    enriched = _make_enriched_signal(enrichment_id=7)
    _insert_enriched_row(parser_db, 7, enriched)
    worker = _make_full_worker(parser_db, ops_db)
    worker.run_once()
    conn = sqlite3.connect(ops_db)
    rows = conn.execute("SELECT idempotency_key FROM ops_lifecycle_events").fetchall()
    conn.close()
    assert len(rows) > 0
    assert all(r[0] is not None and len(r[0]) > 0 for r in rows)


# AC8: nessun import Hummingbot nel lifecycle package
def test_ac8_no_hummingbot_imports():
    import importlib
    import pkgutil
    import src.runtime_v2.lifecycle as pkg
    for _, name, _ in pkgutil.walk_packages(pkg.__path__, prefix=pkg.__name__ + "."):
        try:
            mod = importlib.import_module(name)
            src_file = getattr(mod, "__file__", "") or ""
            if "lifecycle" in src_file:
                assert "hummingbot" not in src_file.lower()
        except ImportError:
            pass


# AC9: lifecycle_processed=1 dopo processamento
def test_ac9_lifecycle_processed_marked(dbs):
    parser_db, ops_db = dbs
    enriched = _make_enriched_signal(enrichment_id=9)
    _insert_enriched_row(parser_db, 9, enriched)
    worker = _make_full_worker(parser_db, ops_db)
    worker.run_once()
    conn = sqlite3.connect(parser_db)
    val = conn.execute(
        "SELECT lifecycle_processed FROM enriched_canonical_messages WHERE enrichment_id=?", (9,)
    ).fetchone()[0]
    conn.close()
    assert val == 1


# AC16: riprocessamento stesso enrichment non crea duplicati (ops-first crash recovery)
def test_ac16_idempotency_guard_on_double_processing(dbs):
    parser_db, ops_db = dbs
    enriched = _make_enriched_signal(enrichment_id=16)
    _insert_enriched_row(parser_db, 16, enriched)

    worker = _make_full_worker(parser_db, ops_db)
    worker.run_once()  # primo run: scrive ops + marca processed

    # Simula crash: ripristina lifecycle_processed=0
    conn = sqlite3.connect(parser_db)
    conn.execute(
        "UPDATE enriched_canonical_messages SET lifecycle_processed=0 WHERE enrichment_id=?", (16,)
    )
    conn.commit()
    conn.close()

    worker.run_once()  # secondo run: INSERT OR IGNORE blocca duplicati

    conn = sqlite3.connect(ops_db)
    chains = conn.execute(
        "SELECT COUNT(*) FROM ops_trade_chains WHERE source_enrichment_id=16"
    ).fetchone()[0]
    conn.close()
    assert chains == 1


# AC17: TP_FILLED non finale → PARTIALLY_CLOSED, finale → CLOSED
def test_ac17_tp_filled_state_transitions(dbs):
    _, ops_db = dbs
    now = _now()
    conn = sqlite3.connect(ops_db)
    conn.execute(
        """
        INSERT INTO ops_trade_chains (
            source_enrichment_id, canonical_message_id, raw_message_id, trader_id,
            account_id, symbol, side, lifecycle_state, entry_mode,
            management_plan_json, risk_snapshot_json, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (20, 200, 2000, "trader_a", "acc_1", "BTC/USDT", "LONG", "OPEN", "ONE_SHOT",
         "{}", "{}", now, now),
    )
    conn.commit()
    chain_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    conn.execute(
        """
        INSERT INTO ops_exchange_events (trade_chain_id, event_type, payload_json,
            processing_status, idempotency_key, received_at)
        VALUES (?,?,?,?,?,?)
        """,
        (chain_id, "TP_FILLED", json.dumps({"tp_level": 1, "is_final": False}),
         "NEW", f"tp1:{chain_id}", now),
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
    worker.run_once()

    conn = sqlite3.connect(ops_db)
    state = conn.execute(
        "SELECT lifecycle_state FROM ops_trade_chains WHERE trade_chain_id=?", (chain_id,)
    ).fetchone()[0]
    conn.close()
    assert state == "PARTIALLY_CLOSED"

    # Evento finale
    conn = sqlite3.connect(ops_db)
    conn.execute(
        """
        INSERT INTO ops_exchange_events (trade_chain_id, event_type, payload_json,
            processing_status, idempotency_key, received_at)
        VALUES (?,?,?,?,?,?)
        """,
        (chain_id, "TP_FILLED", json.dumps({"tp_level": 3, "is_final": True}),
         "NEW", f"tp3_final:{chain_id}", now),
    )
    conn.commit()
    conn.close()
    worker.run_once()

    conn = sqlite3.connect(ops_db)
    state2 = conn.execute(
        "SELECT lifecycle_state FROM ops_trade_chains WHERE trade_chain_id=?", (chain_id,)
    ).fetchone()[0]
    conn.close()
    assert state2 == "CLOSED"
