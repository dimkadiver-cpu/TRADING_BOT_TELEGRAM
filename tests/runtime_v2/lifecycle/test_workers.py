from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest


def _apply_migrations(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    for f in sorted(Path("db/migrations").glob("*.sql")):
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
    for db in (parser_db, ops_db):
        _apply_migrations(db)
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
    assert any(c[0] == "PLACE_ENTRY" for c in commands)


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


def test_worker_block_new_entries_produces_review(dbs):
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
    review_events = conn.execute(
        "SELECT COUNT(*) FROM ops_lifecycle_events WHERE event_type='REVIEW_REQUIRED'"
    ).fetchone()
    conn.close()
    assert chains[0] == 0
    assert review_events[0] == 1
