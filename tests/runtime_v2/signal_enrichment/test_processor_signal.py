# tests/runtime_v2/signal_enrichment/test_processor_signal.py
from __future__ import annotations

import sqlite3
import pytest
import yaml
from pathlib import Path
from unittest.mock import MagicMock


def _apply_migrations(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    for f in sorted(Path("db/migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


def _minimal_global_config() -> dict:
    return {
        "account_mode": "single",
        "account": {"id": "main", "capital_base_usdt": 1000.0, "max_leverage": 5,
                     "max_capital_at_risk_pct": 10.0, "hard_max_per_signal_risk_pct": 2.0},
        "registered_traders": ["trader_a"],
        "symbol_blacklist": {"global": [], "per_trader": {}},
        "defaults": {
            "enabled": True, "gate_mode": "block", "hedge_mode": False,
            "signal_policy": {
                "accepted_entry_structures": ["ONE_SHOT", "TWO_STEP", "RANGE", "LADDER"],
                "market_execution": {"mode": "tolerance", "tolerance_pct": 0.5, "range_tolerance_pct": 0.2},
                "entry_split": {
                    "LIMIT": {
                        "single": {"weights": {"E1": 1.0}},
                        "range": {"split_mode": "endpoints", "weights": {"E1": 0.5, "E2": 0.5}},
                        "averaging": {"weights": {"E1": 0.7, "E2": 0.3}},
                        "ladder": {"weights": {"E1": 0.5, "E2": 0.3, "E3": 0.2}},
                    },
                    "MARKET": {
                        "single": {"weights": {"E1": 1.0}},
                        "averaging": {"weights": {"E1": 0.7, "E2": 0.3}},
                    },
                },
                "tp": {"use_tp_count": None},
                "sl": {"use_original_sl": True, "require_sl": True},
                "price_corrections": {"enabled": False, "round_to_tick": False, "clamp_to_exchange_precision": False},
                "price_sanity": {"enabled": False, "symbol_ranges": {}},
            },
            "update_admission": {"MOVE_STOP": True, "MOVE_STOP_TO_BE": False, "CLOSE_FULL": True,
                                  "CLOSE_PARTIAL": True, "CANCEL_PENDING": True, "ADD_ENTRY": False,
                                  "REENTER": False, "MODIFY_ENTRY": False, "MODIFY_TARGETS": False,
                                  "INVALIDATE_SETUP": False},
            "management_plan": {
                "be_trigger": None, "be_buffer_pct": 0.0,
                "close_distribution": {"mode": "table", "table": {1: [100], 2: [50, 50]}},
                "cancel_pending_by_engine": True, "cancel_pending_on_timeout": True,
                "pending_timeout_hours": 24, "cancel_averaging_pending_after": None,
                "cancel_unfilled_pending_after": None, "risk_freed_by_be": True,
                "protective_sl_mode": "exchange_native_first",
            },
            "risk": {"mode": "risk_pct_of_capital", "risk_pct_of_capital": 1.0,
                     "risk_usdt_fixed": 10.0, "capital_base_mode": "static_config",
                     "capital_base_usdt": 1000.0, "leverage": 1, "use_trader_risk_hint": False,
                     "max_capital_at_risk_per_trader_pct": 5.0, "max_concurrent_trades": 5,
                     "max_concurrent_same_symbol": 1},
        },
    }


def _make_parse_result(
    *,
    trader_id: str = "trader_a",
    canonical_message_id: int = 1,
    raw_message_id: int = 10,
    symbol: str = "BTC/USDT",
    side: str = "LONG",
    entry_structure: str = "ONE_SHOT",
    has_sl: bool = True,
    tp_count: int = 3,
    primary_class: str = "SIGNAL",
):
    from src.parser_v2.contracts.canonical_message import (
        CanonicalMessage, SignalPayload,
    )
    from src.parser_v2.contracts.entities import EntryLeg, Price, TakeProfit, StopLoss
    from src.parser_v2.contracts.context import RawContext
    from src.runtime_v2.parser_pipeline.models import CanonicalParseResult
    import datetime

    entries = [EntryLeg(sequence=1, entry_type="LIMIT", price=Price(raw="50000", value=50000.0))]
    take_profits = [
        TakeProfit(sequence=i + 1, price=Price(raw=str(51000 + i * 500), value=51000.0 + i * 500))
        for i in range(tp_count)
    ]
    stop_loss = StopLoss(price=Price(raw="49000", value=49000.0)) if has_sl else None

    # Build SignalPayload — check what fields it accepts
    signal_kwargs = dict(
        symbol=symbol, side=side, entry_structure=entry_structure,
        entries=entries, take_profits=take_profits, stop_loss=stop_loss,
    )
    # Try with completeness field; if SignalPayload doesn't have it, omit it
    try:
        signal = SignalPayload(completeness="COMPLETE", **signal_kwargs)
    except Exception:
        signal = SignalPayload(**signal_kwargs)

    canonical = CanonicalMessage(
        parser_profile=trader_id, primary_class=primary_class,
        parse_status="PARSED", confidence=1.0,
        signal=signal, raw_context=RawContext(raw_text="test"),
    )
    return CanonicalParseResult(
        raw_message_id=raw_message_id,
        canonical_message_id=canonical_message_id,
        parser_profile=trader_id,
        primary_class=primary_class,
        parse_status="PARSED",
        canonical_message=canonical,
        warnings=[],
        parsed_at=datetime.datetime.now(datetime.timezone.utc),
    )


@pytest.fixture
def processor(tmp_path):
    from src.runtime_v2.signal_enrichment.config_loader import OperationConfigLoader
    from src.runtime_v2.signal_enrichment.repository import EnrichedCanonicalMessageRepository
    from src.runtime_v2.signal_enrichment.processor import SignalEnrichmentProcessor

    config_file = tmp_path / "operation_config.yaml"
    with config_file.open("w") as f:
        yaml.dump(_minimal_global_config(), f)
    (tmp_path / "traders").mkdir()

    db_path = str(tmp_path / "test.db")
    _apply_migrations(db_path)

    loader = OperationConfigLoader(str(tmp_path))
    repo = EnrichedCanonicalMessageRepository(db_path)
    return SignalEnrichmentProcessor(config_loader=loader, repository=repo)


def test_unregistered_trader_is_blocked(processor):
    result = _make_parse_result(trader_id="unknown_trader")
    enriched = processor.process(result)
    assert enriched.enrichment_decision == "BLOCK"
    assert enriched.reason_code == "trader_not_registered"
    assert enriched.lifecycle_processed is True


def test_global_blacklisted_symbol_is_blocked(tmp_path):
    from src.runtime_v2.signal_enrichment.config_loader import OperationConfigLoader
    from src.runtime_v2.signal_enrichment.repository import EnrichedCanonicalMessageRepository
    from src.runtime_v2.signal_enrichment.processor import SignalEnrichmentProcessor

    cfg = _minimal_global_config()
    cfg["symbol_blacklist"]["global"] = ["SCAM/USDT"]
    config_file = tmp_path / "operation_config.yaml"
    with config_file.open("w") as f:
        yaml.dump(cfg, f)
    (tmp_path / "traders").mkdir()
    db_path = str(tmp_path / "test.db")
    _apply_migrations(db_path)
    proc = SignalEnrichmentProcessor(
        config_loader=OperationConfigLoader(str(tmp_path)),
        repository=EnrichedCanonicalMessageRepository(db_path),
    )
    result = _make_parse_result(symbol="SCAM/USDT")
    enriched = proc.process(result)
    assert enriched.enrichment_decision == "BLOCK"
    assert enriched.reason_code == "symbol_blacklisted_global"
    assert enriched.lifecycle_processed is True


def test_per_trader_blacklisted_symbol_is_blocked(tmp_path):
    from src.runtime_v2.signal_enrichment.config_loader import OperationConfigLoader
    from src.runtime_v2.signal_enrichment.repository import EnrichedCanonicalMessageRepository
    from src.runtime_v2.signal_enrichment.processor import SignalEnrichmentProcessor

    cfg = _minimal_global_config()
    cfg["symbol_blacklist"]["per_trader"] = {"trader_a": ["RUG/USDT"]}
    config_file = tmp_path / "operation_config.yaml"
    with config_file.open("w") as f:
        yaml.dump(cfg, f)
    (tmp_path / "traders").mkdir()
    db_path = str(tmp_path / "test.db")
    _apply_migrations(db_path)
    proc = SignalEnrichmentProcessor(
        config_loader=OperationConfigLoader(str(tmp_path)),
        repository=EnrichedCanonicalMessageRepository(db_path),
    )
    result = _make_parse_result(symbol="RUG/USDT")
    enriched = proc.process(result)
    assert enriched.enrichment_decision == "BLOCK"
    assert enriched.reason_code == "symbol_blacklisted_trader"


def test_missing_sl_is_blocked(processor):
    result = _make_parse_result(has_sl=False)
    enriched = processor.process(result)
    assert enriched.enrichment_decision == "BLOCK"
    assert enriched.reason_code == "missing_stop_loss"
    assert enriched.lifecycle_processed is True


def test_unsupported_entry_structure_is_blocked(tmp_path):
    from src.runtime_v2.signal_enrichment.config_loader import OperationConfigLoader
    from src.runtime_v2.signal_enrichment.repository import EnrichedCanonicalMessageRepository
    from src.runtime_v2.signal_enrichment.processor import SignalEnrichmentProcessor

    cfg = _minimal_global_config()
    cfg["defaults"]["signal_policy"]["accepted_entry_structures"] = ["ONE_SHOT"]
    config_file = tmp_path / "operation_config.yaml"
    with config_file.open("w") as f:
        yaml.dump(cfg, f)
    (tmp_path / "traders").mkdir()
    db_path = str(tmp_path / "test.db")
    _apply_migrations(db_path)
    proc = SignalEnrichmentProcessor(
        config_loader=OperationConfigLoader(str(tmp_path)),
        repository=EnrichedCanonicalMessageRepository(db_path),
    )
    result = _make_parse_result(entry_structure="TWO_STEP")
    enriched = proc.process(result)
    assert enriched.enrichment_decision == "BLOCK"
    assert enriched.reason_code == "unsupported_entry_structure"


def test_signal_pass_with_tp_trim(tmp_path):
    from src.runtime_v2.signal_enrichment.config_loader import OperationConfigLoader
    from src.runtime_v2.signal_enrichment.repository import EnrichedCanonicalMessageRepository
    from src.runtime_v2.signal_enrichment.processor import SignalEnrichmentProcessor

    cfg = _minimal_global_config()
    cfg["defaults"]["signal_policy"]["tp"]["use_tp_count"] = 2
    config_file = tmp_path / "operation_config.yaml"
    with config_file.open("w") as f:
        yaml.dump(cfg, f)
    (tmp_path / "traders").mkdir()
    db_path = str(tmp_path / "test.db")
    _apply_migrations(db_path)
    proc = SignalEnrichmentProcessor(
        config_loader=OperationConfigLoader(str(tmp_path)),
        repository=EnrichedCanonicalMessageRepository(db_path),
    )
    result = _make_parse_result(tp_count=5)
    enriched = proc.process(result)
    assert enriched.enrichment_decision == "PASS"
    assert enriched.enriched_signal is not None
    assert len(enriched.enriched_signal.take_profits) == 2
    assert any(e.check == "tp_count_trimmed" for e in enriched.enrichment_log)
    log = next(e for e in enriched.enrichment_log if e.check == "tp_count_trimmed")
    assert log.original == "5"
    assert log.result == "2"
    assert enriched.lifecycle_processed is False


def test_signal_pass_has_management_plan(processor):
    result = _make_parse_result(tp_count=2)
    enriched = processor.process(result)
    assert enriched.enrichment_decision == "PASS"
    assert enriched.management_plan is not None
    assert enriched.management_plan.pending_timeout_hours == 24


def test_signal_pass_entry_weights_applied(processor):
    result = _make_parse_result(tp_count=2)
    enriched = processor.process(result)
    assert enriched.enriched_signal is not None
    assert len(enriched.enriched_signal.entries) == 1
    assert enriched.enriched_signal.entries[0].weight == 1.0


def test_idempotency_same_canonical_message_id(processor):
    result = _make_parse_result(has_sl=False, canonical_message_id=99)
    enriched1 = processor.process(result)
    enriched2 = processor.process(result)
    assert enriched1.enrichment_id == enriched2.enrichment_id


def test_trader_override_tp_count_via_yaml(tmp_path):
    from src.runtime_v2.signal_enrichment.config_loader import OperationConfigLoader
    from src.runtime_v2.signal_enrichment.repository import EnrichedCanonicalMessageRepository
    from src.runtime_v2.signal_enrichment.processor import SignalEnrichmentProcessor

    cfg = _minimal_global_config()
    config_file = tmp_path / "operation_config.yaml"
    with config_file.open("w") as f:
        yaml.dump(cfg, f)
    traders_dir = tmp_path / "traders"
    traders_dir.mkdir()
    with (traders_dir / "trader_a.yaml").open("w") as f:
        yaml.dump({"signal_policy": {"tp": {"use_tp_count": 2}}}, f)

    db_path = str(tmp_path / "test.db")
    _apply_migrations(db_path)
    proc = SignalEnrichmentProcessor(
        config_loader=OperationConfigLoader(str(tmp_path)),
        repository=EnrichedCanonicalMessageRepository(db_path),
    )
    result = _make_parse_result(tp_count=3)
    enriched = proc.process(result)
    assert enriched.enrichment_decision == "PASS"
    assert len(enriched.enriched_signal.take_profits) == 2
