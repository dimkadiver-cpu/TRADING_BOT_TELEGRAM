# tests/runtime_v2/execution_gateway/test_event_ingest_classifier.py
from __future__ import annotations

import pytest

from src.runtime_v2.execution_gateway.event_ingest.models import ExchangeRawEvent
from src.runtime_v2.execution_gateway.event_ingest.classifier import EventClassifier


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _raw(source_stream="watch_my_trades", exchange_event_id="evt-1", **kwargs) -> ExchangeRawEvent:
    defaults = dict(
        source_stream=source_stream,
        exchange_event_id=exchange_event_id,
        idempotency_key=f"exec:{exchange_event_id}",
        symbol="BTCUSDT",
        side="Sell",
        create_type=None,
        stop_order_type=None,
        exec_type="Trade",
        order_status=None,
        order_link_id=None,
        order_id="ord-1",
        seq=None,
        exec_price=45000.0,
        exec_qty=0.01,
        closed_size=None,
        leaves_qty=None,
        pos_qty=None,
        exec_value=450.0,
        exec_fee=0.18,
        fee_rate=0.0004,
        cum_exec_qty=0.01,
    )
    defaults.update(kwargs)
    return ExchangeRawEvent(**defaults)


# ---------------------------------------------------------------------------
# Shared known_order_link_ids fixture
# ---------------------------------------------------------------------------

KNOWN_IDS: dict[str, tuple[int, str, int]] = {
    "bot-entry-1":  (10, "entry", 1),
    "bot-tp-1":     (10, "tp_1",  2),
    "bot-tp-2":     (10, "tp_2",  3),
    "bot-sl-1":     (10, "sl",    4),
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPriority1DeterministicFields:

    def test_classify_tp_position_level_no_link_id(self):
        """createType=CreateByTakeProfit, no orderLinkId → TP_FILLED, exchange_auto, no chain."""
        clf = EventClassifier(known_order_link_ids={})
        raw = _raw(create_type="CreateByTakeProfit", order_link_id=None)
        result = clf.classify(raw)
        assert result.event_type == "TP_FILLED"
        assert result.source == "exchange_auto"
        assert result.trade_chain_id is None

    def test_classify_tp_position_level_with_chain_correlation(self):
        """createType=CreateByTakeProfit, no orderLinkId — even with known ids, no correlation possible."""
        clf = EventClassifier(known_order_link_ids=KNOWN_IDS)
        raw = _raw(create_type="CreateByTakeProfit", order_link_id=None)
        result = clf.classify(raw)
        assert result.event_type == "TP_FILLED"
        assert result.source == "exchange_auto"
        assert result.trade_chain_id is None

    def test_classify_sl_by_create_type(self):
        """createType=CreateByStopLoss → SL_FILLED, exchange_auto."""
        clf = EventClassifier(known_order_link_ids={})
        raw = _raw(create_type="CreateByStopLoss", order_link_id=None)
        result = clf.classify(raw)
        assert result.event_type == "SL_FILLED"
        assert result.source == "exchange_auto"

    def test_classify_sl_by_stop_order_type(self):
        """stopOrderType=StopLoss (no createType) → SL_FILLED, exchange_auto."""
        clf = EventClassifier(known_order_link_ids={})
        raw = _raw(create_type=None, stop_order_type="StopLoss", order_link_id=None)
        result = clf.classify(raw)
        assert result.event_type == "SL_FILLED"
        assert result.source == "exchange_auto"

    def test_classify_liquidation(self):
        """createType=CreateByLiq → LIQUIDATION_FILLED, exchange_auto."""
        clf = EventClassifier(known_order_link_ids={})
        raw = _raw(create_type="CreateByLiq", order_link_id=None)
        result = clf.classify(raw)
        assert result.event_type == "LIQUIDATION_FILLED"
        assert result.source == "exchange_auto"


class TestPriority2OrderLinkIdCorrelation:

    def test_classify_entry_by_order_link_id(self):
        """createType=CreateByUser, orderLinkId=known_entry, closedSize=0 → ENTRY_FILLED, bot_command, chain 10."""
        clf = EventClassifier(known_order_link_ids=KNOWN_IDS)
        raw = _raw(
            create_type="CreateByUser",
            order_link_id="bot-entry-1",
            closed_size=0.0,
            pos_qty=0.1,
        )
        result = clf.classify(raw)
        assert result.event_type == "ENTRY_FILLED"
        assert result.source == "bot_command"
        assert result.trade_chain_id == 10

    def test_classify_tp_by_order_link_id(self):
        """orderLinkId=known_tp_2 → TP_FILLED, bot_command, tp_level=2, chain 10."""
        clf = EventClassifier(known_order_link_ids=KNOWN_IDS)
        raw = _raw(
            create_type="CreateByUser",
            order_link_id="bot-tp-2",
            closed_size=0.05,
            pos_qty=0.05,
        )
        result = clf.classify(raw)
        assert result.event_type == "TP_FILLED"
        assert result.source == "bot_command"
        assert result.tp_level == 2
        assert result.trade_chain_id == 10

    def test_classify_close_full_by_order_link_id(self):
        """orderLinkId=known_entry, closedSize=0.1, posQty=0 → CLOSE_FULL_FILLED, bot_command."""
        clf = EventClassifier(known_order_link_ids=KNOWN_IDS)
        raw = _raw(
            create_type="CreateByUser",
            order_link_id="bot-entry-1",
            closed_size=0.1,
            pos_qty=0.0,
        )
        result = clf.classify(raw)
        assert result.event_type == "CLOSE_FULL_FILLED"
        assert result.source == "bot_command"
        assert result.trade_chain_id == 10

    def test_classify_close_partial_by_order_link_id(self):
        """orderLinkId=known_entry, closedSize=0.05, posQty=0.05 → CLOSE_PARTIAL_FILLED, bot_command."""
        clf = EventClassifier(known_order_link_ids=KNOWN_IDS)
        raw = _raw(
            create_type="CreateByUser",
            order_link_id="bot-entry-1",
            closed_size=0.05,
            pos_qty=0.05,
        )
        result = clf.classify(raw)
        assert result.event_type == "CLOSE_PARTIAL_FILLED"
        assert result.source == "bot_command"
        assert result.trade_chain_id == 10


class TestPriority3StructuralInference:

    def test_classify_manual_close_full(self):
        """createType=CreateByUser, no orderLinkId, closedSize=0.1, posQty=0 → MANUAL_CLOSE_FULL."""
        clf = EventClassifier(known_order_link_ids={})
        raw = _raw(
            create_type="CreateByUser",
            order_link_id=None,
            closed_size=0.1,
            pos_qty=0.0,
        )
        result = clf.classify(raw)
        assert result.event_type == "MANUAL_CLOSE_FULL"
        assert result.source == "exchange_manual"

    def test_classify_manual_close_partial(self):
        """createType=CreateByUser, no orderLinkId, closedSize=0.05, posQty=0.05 → MANUAL_CLOSE_PARTIAL."""
        clf = EventClassifier(known_order_link_ids={})
        raw = _raw(
            create_type="CreateByUser",
            order_link_id=None,
            closed_size=0.05,
            pos_qty=0.05,
        )
        result = clf.classify(raw)
        assert result.event_type == "MANUAL_CLOSE_PARTIAL"
        assert result.source == "exchange_manual"


class TestWatchOrdersStream:

    def test_classify_pending_entry_cancelled(self):
        """watch_orders, Cancelled, orderLinkId=known_entry → PENDING_ENTRY_CANCELLED, bot_command."""
        clf = EventClassifier(known_order_link_ids=KNOWN_IDS)
        raw = _raw(
            source_stream="watch_orders",
            order_status="Cancelled",
            order_link_id="bot-entry-1",
        )
        result = clf.classify(raw)
        assert result.event_type == "PENDING_ENTRY_CANCELLED"
        assert result.source == "bot_command"
        assert result.trade_chain_id == 10

    def test_classify_standalone_protective_cancelled(self):
        """watch_orders, Cancelled, orderLinkId=known_tp_1 → STANDALONE_PROTECTIVE_CANCELLED, bot_command."""
        clf = EventClassifier(known_order_link_ids=KNOWN_IDS)
        raw = _raw(
            source_stream="watch_orders",
            order_status="Cancelled",
            order_link_id="bot-tp-1",
        )
        result = clf.classify(raw)
        assert result.event_type == "STANDALONE_PROTECTIVE_CANCELLED"
        assert result.source == "bot_command"
        assert result.trade_chain_id == 10


class TestWatchPositionsStream:

    def test_classify_protective_order_cancelled_via_position(self):
        """watch_positions, position_take_profit=0.0 → PROTECTIVE_ORDER_CANCELLED, exchange_auto, is_actionable=True."""
        clf = EventClassifier(known_order_link_ids={})
        raw = _raw(
            source_stream="watch_positions",
            position_take_profit=0.0,
            position_stop_loss=10000.0,
        )
        result = clf.classify(raw)
        assert result.event_type == "PROTECTIVE_ORDER_CANCELLED"
        assert result.source == "exchange_auto"
        assert result.is_actionable is True


class TestFallback:

    def test_classify_unknown_fallback(self):
        """createType=CreateByUser, no orderLinkId, closedSize=0, posQty=50 → UNKNOWN, is_actionable=False."""
        clf = EventClassifier(known_order_link_ids={})
        raw = _raw(
            create_type="CreateByUser",
            order_link_id=None,
            closed_size=0.0,
            pos_qty=50.0,
        )
        result = clf.classify(raw)
        assert result.event_type == "UNKNOWN"
        assert result.is_actionable is False
