"""Integration tests for the reshape stage in processor.py.

Uses a real OperationConfigLoader pointed at a temp config dir.
Builds minimal CanonicalParseResult objects to drive the processor.
"""
import pytest
from pathlib import Path
import yaml

from src.runtime_v2.signal_enrichment.config_loader import OperationConfigLoader
from src.runtime_v2.signal_enrichment.processor import SignalEnrichmentProcessor
from src.runtime_v2.signal_enrichment.repository import EnrichedCanonicalMessageRepository


def _write_minimal_config(tmp_path: Path, setup_mode: str = "reshape") -> None:
    global_cfg = {
        "registered_traders": ["trader_t"],
        "account": {
            "id": "main", "capital_base_usdt": 1000.0, "max_leverage": 10,
            "max_capital_at_risk_pct": 10.0, "hard_max_per_signal_risk_pct": 2.0,
        },
        "defaults": {
            "enabled": True,
            "gate_mode": "block",
            "hedge_mode": False,
            "signal_policy": {
                "accepted_entry_structures": ["LADDER", "ONE_SHOT", "TWO_STEP", "RANGE"],
                "market_execution": {"mode": "tolerance", "tolerance_pct": 0.5, "range_tolerance_pct": 0.2},
                "entry_split": {
                    "LIMIT": {
                        "single": {"weights": {"E1": 1.0}},
                        "range": {"weights": {"E1": 0.5, "E2": 0.5}},
                        "averaging": {"weights": {"E1": 0.70, "E2": 0.30}},
                        "ladder": {"weights": {"E1": 0.40, "E2": 0.30, "E3": 0.20, "E4": 0.10}},
                    },
                    "MARKET": {
                        "single": {"weights": {"E1": 1.0}},
                        "averaging": {"weights": {"E1": 0.7, "E2": 0.3}},
                    },
                },
                "tp": {"use_tp_count": 4},  # would trim to 4, but reshape bypasses this
                "sl": {"use_original_sl": True, "require_sl": True},
                "price_corrections": {"enabled": False},
                "price_sanity": {"enabled": False},
            },
            "management_plan": {
                "be_trigger": None,
                "close_distribution": {"mode": "equal"},
            },
            "risk": {"mode": "risk_pct_of_capital"},
            "update_admission": {},
        },
    }
    (tmp_path / "operation_config.yaml").write_text(yaml.dump(global_cfg))
    (tmp_path / "traders").mkdir()
    (tmp_path / "traders" / "trader_t.yaml").write_text(yaml.dump({
        "setup_mode": setup_mode,
        "setup_reshape": {"template": "ladder_4_aggressive"} if setup_mode == "reshape" else {},
    }))
    (tmp_path / "setup_reshape_templates.yaml").write_text(yaml.dump({
        "templates": [{
            "id": "ladder_4_aggressive",
            "enabled": True,
            "match": {"entry_structure": "LADDER", "normalized_entry_count": 4, "min_tp_count": 8},
            "entries": {"mode": "drop", "indexes": ["E1"]},
            "stop_loss": {"mode": "from_entry", "entry": "E4"},
            "take_profits": {
                "mode": "by_rr",
                "desired_rr": [1.0, 1.5, 2.5, 3.5],
                "strategy": "nearest_unique",
                "max_rr_deviation_abs": 0.35,
                "on_missing_target": "REJECT",
            },
            "on_failure": "REJECT",
        }]
    }))


def _make_processor(tmp_path: Path):
    loader = OperationConfigLoader(str(tmp_path))

    class _InMemoryRepo(EnrichedCanonicalMessageRepository):
        def __init__(self):
            self._store = {}
        def get_by_canonical_message_id(self, cid):
            return self._store.get(cid)
        def save(self, msg):
            self._store[msg.canonical_message_id] = msg
            return msg

    return SignalEnrichmentProcessor(config_loader=loader, repository=_InMemoryRepo())


def _make_signal_result(
    canonical_message_id: int,
    entries,
    sl_price: float,
    tp_prices,
    entry_structure: str,
    side: str,
):
    """Build a minimal CanonicalParseResult with a SIGNAL payload."""
    from src.runtime_v2.parser_pipeline.models import CanonicalParseResult
    from src.parser_v2.contracts.canonical_message import CanonicalMessage, SignalPayload
    from src.parser_v2.contracts.context import RawContext
    from src.parser_v2.contracts.entities import EntryLeg, StopLoss, TakeProfit, Price

    entry_legs = [
        EntryLeg(sequence=i + 1, entry_type="LIMIT", price=Price(raw=str(p), value=p))
        for i, p in enumerate(entries)
    ]
    tps = [
        TakeProfit(sequence=i + 1, price=Price(raw=str(p), value=p))
        for i, p in enumerate(tp_prices)
    ]
    signal = SignalPayload(
        entry_structure=entry_structure,
        side=side,
        symbol="BTCUSDT",
        entries=entry_legs,
        stop_loss=StopLoss(price=Price(raw=str(sl_price), value=sl_price)),
        take_profits=tps,
        completeness="COMPLETE",
    )
    msg = CanonicalMessage(
        primary_class="SIGNAL",
        parser_profile="trader_t",
        parse_status="PARSED",
        confidence=1.0,
        signal=signal,
        raw_context=RawContext(raw_text="test"),
    )
    from datetime import datetime, timezone
    return CanonicalParseResult(
        canonical_message_id=canonical_message_id,
        raw_message_id=1,
        canonical_message=msg,
        primary_class="SIGNAL",
        resolved_trader_id="trader_t",
        parser_profile="trader_t",
        parse_status="PARSED",
        warnings=[],
        parsed_at=datetime.now(tz=timezone.utc),
    )


def test_reshape_pass_produces_reshaped_payload(tmp_path):
    _write_minimal_config(tmp_path, setup_mode="reshape")
    proc = _make_processor(tmp_path)
    # Spec §5 example: LONG LADDER, 4 entries, 8 TPs
    result = _make_signal_result(
        canonical_message_id=1,
        entries=[100.0, 98.0, 96.0, 94.0],
        sl_price=92.0,
        tp_prices=[98.0, 100.0, 102.0, 104.0, 106.0, 108.0, 110.0, 112.0],
        entry_structure="LADDER",
        side="LONG",
    )
    enriched = proc.process(result)
    assert enriched.enrichment_decision == "PASS"
    assert enriched.enriched_signal is not None
    # use_tp_count=4 was configured but should be bypassed in reshape mode: 4 TPs from by_rr
    assert len(enriched.enriched_signal.take_profits) == 4
    # Reshape audit present
    assert enriched.enriched_signal.reshaped is not None
    assert enriched.enriched_signal.reshaped.rule_id == "ladder_4_aggressive"
    # Operative entries: E2(98), E3(96) — E1(100) discarded, E4(94)→SL
    operative_prices = [e.price.value for e in enriched.enriched_signal.entries]
    assert operative_prices == [98.0, 96.0]
    # SL is now 94 (E4)
    assert enriched.enriched_signal.stop_loss.price.value == 94.0


def test_reshape_no_match_blocks_signal(tmp_path):
    _write_minimal_config(tmp_path, setup_mode="reshape")
    proc = _make_processor(tmp_path)
    # Only 3 entries — template requires exactly 4
    result = _make_signal_result(
        canonical_message_id=2,
        entries=[100.0, 98.0, 96.0],
        sl_price=92.0,
        tp_prices=[98.0, 100.0, 102.0, 104.0, 106.0, 108.0, 110.0, 112.0],
        entry_structure="LADDER",
        side="LONG",
    )
    enriched = proc.process(result)
    assert enriched.enrichment_decision == "BLOCK"
    assert "reshape" in enriched.reason_code


def test_passthrough_unchanged(tmp_path):
    _write_minimal_config(tmp_path, setup_mode="passthrough")
    proc = _make_processor(tmp_path)
    result = _make_signal_result(
        canonical_message_id=3,
        entries=[100.0, 98.0, 96.0, 94.0],
        sl_price=92.0,
        tp_prices=[98.0, 100.0, 102.0, 104.0, 106.0, 108.0, 110.0, 112.0],
        entry_structure="LADDER",
        side="LONG",
    )
    enriched = proc.process(result)
    assert enriched.enrichment_decision == "PASS"
    # use_tp_count=4 is respected in passthrough
    assert len(enriched.enriched_signal.take_profits) == 4
    # No reshape audit
    assert enriched.enriched_signal.reshaped is None
    # All 4 entries kept
    assert len(enriched.enriched_signal.entries) == 4
