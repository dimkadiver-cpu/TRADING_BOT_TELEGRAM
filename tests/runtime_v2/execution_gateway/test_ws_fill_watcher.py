# tests/runtime_v2/execution_gateway/test_ws_fill_watcher.py
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.runtime_v2.execution_gateway.adapters.ccxt_bybit.ws_fill_watcher import BybitWsFillWatcher
from src.runtime_v2.execution_gateway.event_ingest.models import ClassifiedEvent, ExchangeRawEvent


def _make_watcher(mock_repo=None):
    if mock_repo is None:
        mock_repo = MagicMock()
        mock_repo.get_known_order_link_ids.return_value = {}
    return BybitWsFillWatcher(
        api_key="k",
        api_secret="s",
        testnet=False,
        ops_db_path=":memory:",
        repo=mock_repo,
        normalizer=MagicMock(),
        classifier=MagicMock(),
    )


# ── _process_batch ────────────────────────────────────────────────────────────

def test_process_batch_normalizes_and_classifies_and_inserts():
    """_process_batch calls normalize_fn, classifies, and calls insert_raw_and_classified."""
    mock_repo = MagicMock()
    mock_normalizer = MagicMock()
    mock_classifier = MagicMock()

    watcher = BybitWsFillWatcher(
        api_key="k", api_secret="s", testnet=False,
        ops_db_path=":memory:",
        repo=mock_repo,
        normalizer=mock_normalizer,
        classifier=mock_classifier,
    )

    # Setup: normalize_fn returns a raw event; classify returns a classified event with chain
    mock_raw = MagicMock(spec=ExchangeRawEvent)
    mock_classified = MagicMock(spec=ClassifiedEvent)
    mock_classified.event_type = "ENTRY_FILLED"
    mock_classified.trade_chain_id = 1
    mock_classified.should_forward_to_lifecycle = True
    mock_repo.get_known_order_link_ids.return_value = {}
    mock_repo.insert_raw_and_classified.return_value = True

    normalize_fn = MagicMock(return_value=mock_raw)

    # Patch EventClassifier to return mock_classified
    with patch(
        "src.runtime_v2.execution_gateway.adapters.ccxt_bybit.ws_fill_watcher.EventClassifier"
    ) as MockClassifier:
        MockClassifier.return_value.classify.return_value = mock_classified
        watcher._process_batch([{"id": "trade-1"}], normalize_fn)

    normalize_fn.assert_called_once_with({"id": "trade-1"})
    mock_repo.insert_raw_and_classified.assert_called_once_with(mock_classified)


def test_process_batch_skips_none_from_normalizer():
    """_process_batch skips items where normalize_fn returns None."""
    mock_repo = MagicMock()
    mock_repo.get_known_order_link_ids.return_value = {}

    watcher = BybitWsFillWatcher(
        api_key="k", api_secret="s", testnet=False,
        ops_db_path=":memory:", repo=mock_repo,
        normalizer=MagicMock(), classifier=MagicMock(),
    )
    normalize_fn = MagicMock(return_value=None)

    with patch("src.runtime_v2.execution_gateway.adapters.ccxt_bybit.ws_fill_watcher.EventClassifier"):
        watcher._process_batch([{"id": "x"}], normalize_fn)

    mock_repo.insert_raw_and_classified.assert_not_called()


def test_process_batch_empty_input():
    """_process_batch with None or empty list does nothing."""
    mock_repo = MagicMock()
    watcher = BybitWsFillWatcher(
        api_key="k", api_secret="s", testnet=False,
        ops_db_path=":memory:", repo=mock_repo,
        normalizer=MagicMock(), classifier=MagicMock(),
    )
    watcher._process_batch(None, MagicMock())
    watcher._process_batch([], MagicMock())
    mock_repo.get_known_order_link_ids.assert_not_called()


def test_process_batch_triggers_wake_callback_on_insert():
    """_process_batch calls wake_callback when insert_raw_and_classified returns True and should_forward."""
    mock_repo = MagicMock()
    mock_repo.get_known_order_link_ids.return_value = {}
    mock_repo.insert_raw_and_classified.return_value = True

    wake = MagicMock()

    watcher = BybitWsFillWatcher(
        api_key="k", api_secret="s", testnet=False,
        ops_db_path=":memory:", repo=mock_repo,
        normalizer=MagicMock(), classifier=MagicMock(),
        wake_callback=wake,
    )

    mock_classified = MagicMock(spec=ClassifiedEvent)
    mock_classified.event_type = "ENTRY_FILLED"
    mock_classified.trade_chain_id = 1
    mock_classified.should_forward_to_lifecycle = True

    normalize_fn = MagicMock(return_value=MagicMock(spec=ExchangeRawEvent))

    with patch(
        "src.runtime_v2.execution_gateway.adapters.ccxt_bybit.ws_fill_watcher.EventClassifier"
    ) as MockClassifier:
        MockClassifier.return_value.classify.return_value = mock_classified
        watcher._process_batch([{"id": "t1"}], normalize_fn)

    wake.assert_called_once()


def test_process_batch_no_wake_when_not_forwarded():
    """_process_batch does NOT call wake_callback when should_forward_to_lifecycle is False."""
    mock_repo = MagicMock()
    mock_repo.get_known_order_link_ids.return_value = {}
    mock_repo.insert_raw_and_classified.return_value = True

    wake = MagicMock()

    watcher = BybitWsFillWatcher(
        api_key="k", api_secret="s", testnet=False,
        ops_db_path=":memory:", repo=mock_repo,
        normalizer=MagicMock(), classifier=MagicMock(),
        wake_callback=wake,
    )

    mock_classified = MagicMock(spec=ClassifiedEvent)
    mock_classified.should_forward_to_lifecycle = False

    normalize_fn = MagicMock(return_value=MagicMock(spec=ExchangeRawEvent))

    with patch(
        "src.runtime_v2.execution_gateway.adapters.ccxt_bybit.ws_fill_watcher.EventClassifier"
    ) as MockClassifier:
        MockClassifier.return_value.classify.return_value = mock_classified
        watcher._process_batch([{"id": "t1"}], normalize_fn)

    wake.assert_not_called()


def test_process_batch_exception_in_item_is_swallowed():
    """_process_batch logs but does not raise when normalize_fn throws."""
    mock_repo = MagicMock()
    mock_repo.get_known_order_link_ids.return_value = {}

    watcher = BybitWsFillWatcher(
        api_key="k", api_secret="s", testnet=False,
        ops_db_path=":memory:", repo=mock_repo,
        normalizer=MagicMock(), classifier=MagicMock(),
    )

    normalize_fn = MagicMock(side_effect=RuntimeError("boom"))

    with patch("src.runtime_v2.execution_gateway.adapters.ccxt_bybit.ws_fill_watcher.EventClassifier"):
        # Must not raise
        watcher._process_batch([{"id": "bad"}], normalize_fn)

    mock_repo.insert_raw_and_classified.assert_not_called()


# ── Constructor / attribute checks ───────────────────────────────────────────

def test_constructor_stores_normalizer_and_classifier():
    """Constructor stores normalizer and classifier as instance attributes."""
    mock_normalizer = MagicMock()
    mock_classifier = MagicMock()
    mock_repo = MagicMock()

    watcher = BybitWsFillWatcher(
        api_key="k", api_secret="s", testnet=False,
        ops_db_path=":memory:", repo=mock_repo,
        normalizer=mock_normalizer, classifier=mock_classifier,
    )

    assert watcher._normalizer is mock_normalizer
    assert watcher._classifier is mock_classifier
    assert watcher._watch_positions_task is None


def test_watcher_has_three_task_attributes():
    """Watcher exposes _watch_orders_task, _watch_trades_task, _watch_positions_task."""
    watcher = _make_watcher()
    assert hasattr(watcher, "_watch_orders_task")
    assert hasattr(watcher, "_watch_trades_task")
    assert hasattr(watcher, "_watch_positions_task")


import dataclasses


def test_process_batch_enriches_tp_fill_with_chain_id_when_no_link_id():
    """TP_FILLED with trade_chain_id=None gets enriched via resolve_chain_for_fill."""
    from src.runtime_v2.execution_gateway.event_ingest.models import (
        ClassifiedEvent, ExchangeRawEvent,
    )

    mock_repo = MagicMock()
    mock_repo.get_known_order_link_ids.return_value = {}
    mock_repo.insert_raw_and_classified.return_value = True
    mock_repo.resolve_chain_for_fill.return_value = 42  # one open chain found

    watcher = BybitWsFillWatcher(
        api_key="k", api_secret="s", testnet=False,
        ops_db_path=":memory:",
        repo=mock_repo,
        normalizer=MagicMock(),
        classifier=MagicMock(),
    )

    mock_raw = MagicMock(spec=ExchangeRawEvent)
    mock_raw.side = "Sell"
    mock_raw.symbol = "BTCUSDT"
    mock_raw.order_link_id = ""

    unlinked = ClassifiedEvent(
        raw=mock_raw,
        event_type="TP_FILLED",
        source="exchange_auto",
        trade_chain_id=None,
        tp_level=None,
        is_actionable=True,
    )

    normalize_fn = MagicMock(return_value=mock_raw)

    with patch(
        "src.runtime_v2.execution_gateway.adapters.ccxt_bybit.ws_fill_watcher.EventClassifier"
    ) as MockClassifier:
        MockClassifier.return_value.classify.return_value = unlinked
        watcher._process_batch([{"id": "tp-trade-1"}], normalize_fn)

    mock_repo.resolve_chain_for_fill.assert_called_once_with("BTCUSDT", "LONG")
    inserted_event = mock_repo.insert_raw_and_classified.call_args[0][0]
    assert inserted_event.trade_chain_id == 42
    assert inserted_event.event_type == "TP_FILLED"


def test_process_batch_does_not_enrich_tp_fill_when_multiple_chains():
    """TP_FILLED stays unlinked when resolve_chain_for_fill returns None (ambiguous)."""
    from src.runtime_v2.execution_gateway.event_ingest.models import (
        ClassifiedEvent, ExchangeRawEvent,
    )

    mock_repo = MagicMock()
    mock_repo.get_known_order_link_ids.return_value = {}
    mock_repo.insert_raw_and_classified.return_value = True
    mock_repo.resolve_chain_for_fill.return_value = None  # ambiguous

    watcher = BybitWsFillWatcher(
        api_key="k", api_secret="s", testnet=False,
        ops_db_path=":memory:",
        repo=mock_repo,
        normalizer=MagicMock(),
        classifier=MagicMock(),
    )

    mock_raw = MagicMock(spec=ExchangeRawEvent)
    mock_raw.side = "Sell"
    mock_raw.symbol = "BTCUSDT"
    mock_raw.order_link_id = ""

    unlinked = ClassifiedEvent(
        raw=mock_raw,
        event_type="TP_FILLED",
        source="exchange_auto",
        trade_chain_id=None,
        tp_level=None,
        is_actionable=True,
    )

    normalize_fn = MagicMock(return_value=mock_raw)

    with patch(
        "src.runtime_v2.execution_gateway.adapters.ccxt_bybit.ws_fill_watcher.EventClassifier"
    ) as MockClassifier:
        MockClassifier.return_value.classify.return_value = unlinked
        watcher._process_batch([{"id": "tp-trade-2"}], normalize_fn)

    inserted_event = mock_repo.insert_raw_and_classified.call_args[0][0]
    assert inserted_event.trade_chain_id is None
