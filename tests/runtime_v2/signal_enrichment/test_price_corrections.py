from __future__ import annotations

from datetime import datetime, timezone

from src.parser_v2.contracts.entities import EntryLeg, Price, StopLoss, TakeProfit
from src.runtime_v2.lifecycle.ports import SymbolMarketSnapshot
from src.runtime_v2.signal_enrichment.models import PriceCorrectionsConfig


def _make_signal(
    *,
    symbol: str = "1000PEPEUSDT",
    side: str = "SHORT",
    entry_price: float = 0.00000226,
    stop_price: float = 0.00000263,
    tp_prices: list[float] | None = None,
):
    from src.parser_v2.contracts.canonical_message import SignalPayload

    if tp_prices is None:
        tp_prices = [0.00000192, 0.00000158, 0.00000085]

    return SignalPayload(
        symbol=symbol,
        side=side,
        entry_structure="ONE_SHOT",
        entries=[
            EntryLeg(
                sequence=1,
                entry_type="LIMIT",
                price=Price(raw=f"{entry_price:.8f}", value=entry_price),
            )
        ],
        stop_loss=StopLoss(price=Price(raw=f"{stop_price:.8f}", value=stop_price)),
        take_profits=[
            TakeProfit(
                sequence=index + 1,
                price=Price(raw=f"{price:.8f}", value=price),
                label=f"TP{index + 1}",
            )
            for index, price in enumerate(tp_prices)
        ],
        completeness="COMPLETE",
    )


def _make_market_snapshot(mark_price: float | None) -> SymbolMarketSnapshot:
    return SymbolMarketSnapshot(
        symbol="1000PEPEUSDT",
        mark_price=mark_price,
        bid=mark_price,
        ask=mark_price,
        min_order_size=100.0,
        price_precision=6,
        qty_precision=0,
        captured_at=datetime.now(timezone.utc),
        source="test",
        payload_json="{}",
    )


def test_numeric_prefix_rescales_asset_style_prices_to_contract_style():
    from src.runtime_v2.signal_enrichment.price_corrections import apply_price_corrections

    signal = _make_signal()
    market_snapshot = _make_market_snapshot(0.0022537)
    config = PriceCorrectionsConfig(
        enabled=True,
        numeric_prefix_exchange_rescale=True,
        numeric_prefix_max_mark_deviation_ratio=0.20,
        reject_on_unresolved_numeric_prefix_mismatch=True,
    )

    result = apply_price_corrections(signal, market_snapshot, config)

    assert result.rejected is False
    assert result.signal.entries[0].price.value == 0.00226
    assert result.signal.stop_loss.price.value == 0.00263
    assert result.signal.take_profits[0].price.value == 0.00192


def test_numeric_prefix_leaves_already_coherent_prices_unchanged():
    from src.runtime_v2.signal_enrichment.price_corrections import apply_price_corrections

    signal = _make_signal(
        entry_price=0.00226,
        stop_price=0.00263,
        tp_prices=[0.00192, 0.00158, 0.00085],
    )
    market_snapshot = _make_market_snapshot(0.0022537)
    config = PriceCorrectionsConfig(
        enabled=True,
        numeric_prefix_exchange_rescale=True,
        numeric_prefix_max_mark_deviation_ratio=0.20,
        reject_on_unresolved_numeric_prefix_mismatch=True,
    )

    result = apply_price_corrections(signal, market_snapshot, config)

    assert result.rejected is False
    assert result.signal.entries[0].price.value == 0.00226
    assert result.audits == []


def test_numeric_prefix_rejects_when_mark_price_missing_and_unresolved():
    from src.runtime_v2.signal_enrichment.price_corrections import apply_price_corrections

    signal = _make_signal()
    market_snapshot = _make_market_snapshot(None)
    config = PriceCorrectionsConfig(
        enabled=True,
        numeric_prefix_exchange_rescale=True,
        numeric_prefix_max_mark_deviation_ratio=0.20,
        reject_on_unresolved_numeric_prefix_mismatch=True,
    )

    result = apply_price_corrections(signal, market_snapshot, config)

    assert result.rejected is True
    assert result.reason_code == "numeric_prefix_price_mismatch_unresolved"


def test_numeric_prefix_rejects_when_scaled_setup_breaks_short_ordering():
    from src.runtime_v2.signal_enrichment.price_corrections import apply_price_corrections

    signal = _make_signal(
        side="SHORT",
        entry_price=0.00000226,
        stop_price=0.00000150,
        tp_prices=[0.00000280, 0.00000158, 0.00000085],
    )
    market_snapshot = _make_market_snapshot(0.0022537)
    config = PriceCorrectionsConfig(
        enabled=True,
        numeric_prefix_exchange_rescale=True,
        numeric_prefix_max_mark_deviation_ratio=0.20,
        reject_on_unresolved_numeric_prefix_mismatch=True,
    )

    result = apply_price_corrections(signal, market_snapshot, config)

    assert result.rejected is True
    assert result.reason_code == "numeric_prefix_price_mismatch_unresolved"
