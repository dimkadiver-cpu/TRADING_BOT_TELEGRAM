# tests/runtime_v2/signal_enrichment/test_processor_routing.py
from __future__ import annotations

import sqlite3
import pytest
import yaml
from pathlib import Path
from datetime import datetime, timezone


def _apply_migrations(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    for f in sorted(Path("db/migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


def _make_result(primary_class: str, trader_id: str = "trader_a", canonical_message_id: int = 1):
    from src.parser_v2.contracts.canonical_message import (
        CanonicalMessage, ReportPayload, InfoPayload, ReportEvent,
    )
    from src.parser_v2.contracts.context import RawContext
    from src.runtime_v2.parser_pipeline.models import CanonicalParseResult

    if primary_class == "REPORT":
        payload_kwargs = {"report": ReportPayload(events=[ReportEvent(event_type="TP_HIT", source_intent="TP_HIT")])}
    else:
        payload_kwargs = {"info": InfoPayload(raw_fragment="test")}

    canonical = CanonicalMessage(
        parser_profile=trader_id, primary_class=primary_class,
        parse_status="PARSED", confidence=1.0,
        raw_context=RawContext(raw_text="test"),
        **payload_kwargs,
    )
    return CanonicalParseResult(
        raw_message_id=10, canonical_message_id=canonical_message_id,
        parser_profile=trader_id, primary_class=primary_class,
        parse_status="PARSED", canonical_message=canonical,
        warnings=[], parsed_at=datetime.now(timezone.utc),
    )


def _make_processor(tmp_path):
    from src.runtime_v2.signal_enrichment.config_loader import OperationConfigLoader
    from src.runtime_v2.signal_enrichment.repository import EnrichedCanonicalMessageRepository
    from src.runtime_v2.signal_enrichment.processor import SignalEnrichmentProcessor

    cfg = {
        "account_mode": "single",
        "account": {"id": "main", "capital_base_usdt": 1000.0, "max_leverage": 5,
                     "max_capital_at_risk_pct": 10.0, "hard_max_per_signal_risk_pct": 2.0},
        "registered_traders": ["trader_a"],
        "symbol_blacklist": {"global": [], "per_trader": {}},
        "defaults": {
            "enabled": True, "gate_mode": "block", "hedge_mode": False,
            "signal_policy": {
                "accepted_entry_structures": ["ONE_SHOT"],
                "market_execution": {"mode": "tolerance", "tolerance_pct": 0.5, "range_tolerance_pct": 0.2},
                "entry_split": {
                    "LIMIT": {"single": {"weights": {"E1": 1.0}}, "range": {"split_mode": "endpoints", "weights": {"E1": 0.5, "E2": 0.5}}, "averaging": {"weights": {"E1": 0.7, "E2": 0.3}}, "ladder": {"weights": {"E1": 0.5, "E2": 0.3, "E3": 0.2}}},
                    "MARKET": {"single": {"weights": {"E1": 1.0}}, "averaging": {"weights": {"E1": 0.7, "E2": 0.3}}},
                },
                "tp": {"use_tp_count": None}, "sl": {"use_original_sl": True, "require_sl": True},
                "price_corrections": {"enabled": False, "round_to_tick": False, "clamp_to_exchange_precision": False},
                "price_sanity": {"enabled": False, "symbol_ranges": {}},
            },
            "update_admission": {"MOVE_STOP": True, "MOVE_STOP_TO_BE": False, "CLOSE_FULL": True,
                                  "CLOSE_PARTIAL": True, "CANCEL_PENDING": True, "ADD_ENTRY": False,
                                  "REENTER": False, "MODIFY_ENTRY": False, "MODIFY_TARGETS": False,
                                  "INVALIDATE_SETUP": False},
            "management_plan": {"be_trigger": None, "be_buffer_pct": 0.0,
                "close_distribution": {"mode": "table", "table": {1: [100]}},
                "cancel_pending_by_engine": True, "cancel_pending_on_timeout": True,
                "pending_timeout_hours": 24, "cancel_averaging_pending_after": None,
                "cancel_unfilled_pending_after": None, "risk_freed_by_be": True,
                "protective_sl_mode": "exchange_native_first"},
            "risk": {"mode": "risk_pct_of_capital", "risk_pct_of_capital": 1.0,
                     "risk_usdt_fixed": 10.0, "capital_base_mode": "static_config",
                     "capital_base_usdt": 1000.0, "leverage": 1, "use_trader_risk_hint": False,
                     "max_capital_at_risk_per_trader_pct": 5.0, "max_concurrent_trades": 5,
                     "max_concurrent_same_symbol": 1},
        },
    }
    config_file = tmp_path / "operation_config.yaml"
    with config_file.open("w") as f:
        yaml.dump(cfg, f)
    (tmp_path / "traders").mkdir(exist_ok=True)
    db_path = str(tmp_path / "test.db")
    _apply_migrations(db_path)
    return SignalEnrichmentProcessor(
        config_loader=OperationConfigLoader(str(tmp_path)),
        repository=EnrichedCanonicalMessageRepository(db_path),
    )


def test_report_passes_with_lifecycle_processed_true(tmp_path):
    proc = _make_processor(tmp_path)
    result = _make_result("REPORT", canonical_message_id=1)
    enriched = proc.process(result)
    assert enriched.enrichment_decision == "PASS"
    assert enriched.enriched_signal is None
    assert enriched.enriched_actions is None
    assert enriched.management_plan is None
    assert enriched.lifecycle_processed is True


def test_info_passes_with_lifecycle_processed_true(tmp_path):
    proc = _make_processor(tmp_path)
    result = _make_result("INFO", canonical_message_id=2)
    enriched = proc.process(result)
    assert enriched.enrichment_decision == "PASS"
    assert enriched.lifecycle_processed is True
