"""Unit tests for Price normalisation.

Tests cover:
  - All numeric formats listed in the PRD
  - Explicit thousands_separator configurations
  - Auto-inferred thousands_separator
  - Edge cases: integers, negatives, very small/large values
  - Price.from_raw() preserves raw field
  - Price.from_float() factory
  - Price immutability (frozen model)
  - ValueError on empty / non-numeric input
  - Intent, TargetRef, TraderParseResult basic construction
  - compute_completeness helper
"""

from __future__ import annotations

import pytest

from src.parser.models.canonical import (
    Intent,
    Price,
    TargetRef,
    TraderParseResult,
    normalize_price,
)
from src.parser.models.new_signal import (
    EntryLevel,
    NewSignalEntities,
    StopLoss,
    TakeProfit,
    compute_completeness,
)
from src.parser.models.update import UpdateEntities


# ---------------------------------------------------------------------------
# normalize_price — standard (period decimal, various thousands separators)
# ---------------------------------------------------------------------------

class TestNormalizePriceStandardDecimal:
    """decimal_separator="." (default)."""

    def test_plain_integer(self) -> None:
        assert normalize_price("100000") == 100000.0

    def test_simple_float(self) -> None:
        assert normalize_price("0.1772") == 0.1772

    def test_space_as_thousands_explicit(self) -> None:
        # "90 000.5" with explicit thousands_separator=" "
        assert normalize_price("90 000.5", thousands_separator=" ") == 90000.5

    def test_space_as_thousands_auto(self) -> None:
        # spaces always stripped — no config needed
        assert normalize_price("90 000.5") == 90000.5

    def test_comma_as_thousands_explicit(self) -> None:
        assert normalize_price("90,000.5", thousands_separator=",") == 90000.5

    def test_comma_as_thousands_auto(self) -> None:
        # decimal="." → commas are treated as thousands grouping
        assert normalize_price("90,000.5") == 90000.5

    def test_multi_group_thousands(self) -> None:
        assert normalize_price("1,234,567.89") == 1_234_567.89

    def test_space_thousands_integer(self) -> None:
        assert normalize_price("1 000 000") == 1_000_000.0

    def test_leading_trailing_whitespace(self) -> None:
        assert normalize_price("  0.5  ") == 0.5

    def test_no_separator(self) -> None:
        assert normalize_price("12345") == 12345.0

    def test_price_just_decimal(self) -> None:
        assert normalize_price("0.00001") == 0.00001

    def test_large_precise(self) -> None:
        assert normalize_price("92 265.00") == 92265.0


# ---------------------------------------------------------------------------
# normalize_price — European format (comma decimal)
# ---------------------------------------------------------------------------

class TestNormalizePriceEuropeanDecimal:
    """decimal_separator=","."""

    def test_comma_decimal_simple(self) -> None:
        assert normalize_price("0,1772", decimal_separator=",") == 0.1772

    def test_period_thousands_comma_decimal_explicit(self) -> None:
        # "90.000,5" European: period = thousands, comma = decimal
        assert normalize_price("90.000,5", decimal_separator=",", thousands_separator=".") == 90000.5

    def test_period_thousands_auto(self) -> None:
        # decimal="," → periods inferred as thousands
        assert normalize_price("90.000,5", decimal_separator=",") == 90000.5

    def test_space_thousands_comma_decimal(self) -> None:
        assert normalize_price("1 234,56", decimal_separator=",", thousands_separator=" ") == 1234.56

    def test_space_thousands_comma_decimal_auto(self) -> None:
        assert normalize_price("1 234,56", decimal_separator=",") == 1234.56

    def test_large_european(self) -> None:
        assert normalize_price("1.000.000,00", decimal_separator=",") == 1_000_000.0

    def test_plain_integer_european(self) -> None:
        assert normalize_price("42", decimal_separator=",") == 42.0


# ---------------------------------------------------------------------------
# normalize_price — edge cases
# ---------------------------------------------------------------------------

class TestNormalizePriceEdgeCases:
    def test_negative_value(self) -> None:
        # Negative prices appear in profit/loss reporting (e.g. "Sl -0.5")
        assert normalize_price("-0.5") == -0.5

    def test_negative_european(self) -> None:
        assert normalize_price("-1,234", decimal_separator=",") == -1.234

    def test_integer_only_no_decimal(self) -> None:
        assert normalize_price("93704") == 93704.0

    def test_very_small_crypto_price(self) -> None:
        assert normalize_price("0.00000345") == 0.00000345

    def test_price_with_trailing_spaces(self) -> None:
        assert normalize_price("  1000  ") == 1000.0


# ---------------------------------------------------------------------------
# normalize_price — error cases
# ---------------------------------------------------------------------------

class TestNormalizePriceErrors:
    def test_empty_string_raises(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            normalize_price("")

    def test_whitespace_only_raises(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            normalize_price("   ")

    def test_non_numeric_raises(self) -> None:
        with pytest.raises(ValueError):
            normalize_price("abc")

    def test_letters_mixed_raises(self) -> None:
        with pytest.raises(ValueError):
            normalize_price("12abc")


# ---------------------------------------------------------------------------
# Price model
# ---------------------------------------------------------------------------

class TestPriceModel:
    def test_from_raw_preserves_raw(self) -> None:
        p = Price.from_raw("90 000.5", thousands_separator=" ")
        assert p.raw == "90 000.5"
        assert p.value == 90000.5

    def test_from_raw_comma_thousands(self) -> None:
        p = Price.from_raw("90,000.5", thousands_separator=",")
        assert p.raw == "90,000.5"
        assert p.value == 90000.5

    def test_from_raw_european(self) -> None:
        p = Price.from_raw("90.000,5", decimal_separator=",", thousands_separator=".")
        assert p.raw == "90.000,5"
        assert p.value == 90000.5

    def test_from_raw_default_format(self) -> None:
        p = Price.from_raw("0.1772")
        assert p.raw == "0.1772"
        assert p.value == pytest.approx(0.1772)

    def test_from_float(self) -> None:
        p = Price.from_float(1234.5)
        assert p.value == 1234.5
        assert p.raw == "1234.5"

    def test_frozen_immutable(self) -> None:
        p = Price.from_raw("100")
        with pytest.raises(Exception):  # ValidationError or AttributeError
            p.value = 999.0  # type: ignore[misc]

    def test_direct_construction(self) -> None:
        p = Price(raw="100", value=100.0)
        assert p.raw == "100"
        assert p.value == 100.0

    def test_from_raw_empty_raises(self) -> None:
        with pytest.raises(ValueError):
            Price.from_raw("")


# ---------------------------------------------------------------------------
# Intent
# ---------------------------------------------------------------------------

class TestIntent:
    def test_context_intent(self) -> None:
        i = Intent(name="U_TP_HIT", kind="CONTEXT")
        assert i.name == "U_TP_HIT"
        assert i.kind == "CONTEXT"

    def test_action_intent(self) -> None:
        i = Intent(name="U_MOVE_STOP", kind="ACTION")
        assert i.kind == "ACTION"

    def test_frozen(self) -> None:
        i = Intent(name="U_CLOSE_FULL", kind="ACTION")
        with pytest.raises(Exception):
            i.kind = "CONTEXT"  # type: ignore[misc]

    def test_invalid_kind_raises(self) -> None:
        with pytest.raises(Exception):
            Intent(name="U_CLOSE_FULL", kind="UNKNOWN")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# TargetRef
# ---------------------------------------------------------------------------

class TestTargetRef:
    def test_strong_reply(self) -> None:
        t = TargetRef(kind="STRONG", method="REPLY", ref=1234)
        assert t.kind == "STRONG"
        assert t.ref == 1234

    def test_strong_link(self) -> None:
        t = TargetRef(kind="STRONG", method="TELEGRAM_LINK", ref="https://t.me/c/1/42")
        assert t.method == "TELEGRAM_LINK"

    def test_symbol(self) -> None:
        t = TargetRef(kind="SYMBOL", symbol="BTCUSDT")
        assert t.symbol == "BTCUSDT"

    def test_global(self) -> None:
        t = TargetRef(kind="GLOBAL", scope="all_long")
        assert t.scope == "all_long"

    def test_strong_without_method_raises(self) -> None:
        with pytest.raises(ValueError, match="method"):
            TargetRef(kind="STRONG")

    def test_symbol_without_symbol_raises(self) -> None:
        with pytest.raises(ValueError, match="symbol"):
            TargetRef(kind="SYMBOL")

    def test_global_without_scope_raises(self) -> None:
        with pytest.raises(ValueError, match="scope"):
            TargetRef(kind="GLOBAL")


# ---------------------------------------------------------------------------
# TraderParseResult
# ---------------------------------------------------------------------------

class TestTraderParseResult:
    def test_new_signal_complete(self) -> None:
        r = TraderParseResult(
            message_type="NEW_SIGNAL",
            completeness="COMPLETE",
            trader_id="trader_b",
            raw_text="BTCUSDT LONG\nSL 90000\nTP 95000",
        )
        assert r.message_type == "NEW_SIGNAL"
        assert r.completeness == "COMPLETE"
        assert r.missing_fields == []
        assert r.warnings == []
        assert r.acquisition_mode == "live"

    def test_new_signal_incomplete(self) -> None:
        r = TraderParseResult(
            message_type="NEW_SIGNAL",
            completeness="INCOMPLETE",
            missing_fields=["stop_loss"],
            trader_id="trader_b",
            raw_text="BTCUSDT LONG",
        )
        assert r.completeness == "INCOMPLETE"
        assert "stop_loss" in r.missing_fields

    def test_new_signal_without_completeness_raises(self) -> None:
        with pytest.raises(ValueError, match="completeness"):
            TraderParseResult(
                message_type="NEW_SIGNAL",
                trader_id="trader_b",
                raw_text="...",
            )

    def test_update_without_completeness(self) -> None:
        r = TraderParseResult(
            message_type="UPDATE",
            trader_id="trader_b",
            raw_text="move SL to BE",
        )
        assert r.completeness is None

    def test_non_signal_with_completeness_raises(self) -> None:
        with pytest.raises(ValueError, match="completeness"):
            TraderParseResult(
                message_type="UPDATE",
                completeness="COMPLETE",
                trader_id="trader_b",
                raw_text="...",
            )

    def test_unclassified(self) -> None:
        r = TraderParseResult(
            message_type="UNCLASSIFIED",
            trader_id="trader_b",
            raw_text="???",
            warnings=["could not classify"],
        )
        assert r.message_type == "UNCLASSIFIED"

    def test_catchup_acquisition_mode(self) -> None:
        r = TraderParseResult(
            message_type="INFO_ONLY",
            trader_id="trader_3",
            raw_text="📊 Stats",
            acquisition_mode="catchup",
        )
        assert r.acquisition_mode == "catchup"


# ---------------------------------------------------------------------------
# NewSignalEntities + compute_completeness
# ---------------------------------------------------------------------------

class TestNewSignalEntities:
    def _make_complete(self) -> NewSignalEntities:
        sl_price = Price.from_raw("90000")
        tp_price = Price.from_raw("95000")
        return NewSignalEntities(
            symbol="btcusdt",  # lowercase — should be uppercased by validator
            direction="LONG",
            entry_type="MARKET",
            stop_loss=StopLoss(price=sl_price),
            take_profits=[TakeProfit(price=tp_price)],
        )

    def test_symbol_uppercased(self) -> None:
        e = self._make_complete()
        assert e.symbol == "BTCUSDT"

    def test_symbol_none_preserved(self) -> None:
        e = NewSignalEntities()
        assert e.symbol is None

    def test_defaults_are_none_or_empty(self) -> None:
        e = NewSignalEntities()
        assert e.symbol is None
        assert e.direction is None
        assert e.entry_type is None
        assert e.entries == []
        assert e.stop_loss is None
        assert e.take_profits == []
        assert e.leverage is None
        assert e.risk_pct is None
        assert e.conditions is None

    def test_compute_completeness_complete(self) -> None:
        e = self._make_complete()
        completeness, missing = compute_completeness(e)
        assert completeness == "COMPLETE"
        assert missing == []

    def test_compute_completeness_missing_stop_loss(self) -> None:
        e = NewSignalEntities(
            symbol="BTCUSDT",
            direction="LONG",
            entry_type="MARKET",
            take_profits=[TakeProfit(price=Price.from_float(95000.0))],
        )
        completeness, missing = compute_completeness(e)
        assert completeness == "INCOMPLETE"
        assert "stop_loss" in missing

    def test_compute_completeness_missing_take_profits(self) -> None:
        e = NewSignalEntities(
            symbol="BTCUSDT",
            direction="SHORT",
            entry_type="MARKET",
            stop_loss=StopLoss(price=Price.from_float(92000.0)),
        )
        completeness, missing = compute_completeness(e)
        assert completeness == "INCOMPLETE"
        assert "take_profits" in missing

    def test_compute_completeness_limit_missing_entries(self) -> None:
        e = NewSignalEntities(
            symbol="ETHUSDT",
            direction="LONG",
            entry_type="LIMIT",  # requires entries
            stop_loss=StopLoss(price=Price.from_float(1800.0)),
            take_profits=[TakeProfit(price=Price.from_float(2000.0))],
        )
        completeness, missing = compute_completeness(e)
        assert completeness == "INCOMPLETE"
        assert "entries" in missing

    def test_compute_completeness_limit_with_entries(self) -> None:
        entry = EntryLevel(price=Price.from_float(1900.0), order_type="LIMIT")
        e = NewSignalEntities(
            symbol="ETHUSDT",
            direction="LONG",
            entry_type="LIMIT",
            entries=[entry],
            stop_loss=StopLoss(price=Price.from_float(1800.0)),
            take_profits=[TakeProfit(price=Price.from_float(2000.0))],
        )
        completeness, missing = compute_completeness(e)
        assert completeness == "COMPLETE"
        assert missing == []

    def test_compute_completeness_market_empty_entries_ok(self) -> None:
        """MARKET entry with no entries is still COMPLETE."""
        e = NewSignalEntities(
            symbol="BTCUSDT",
            direction="LONG",
            entry_type="MARKET",
            stop_loss=StopLoss(price=Price.from_float(90000.0)),
            take_profits=[TakeProfit(price=Price.from_float(95000.0))],
        )
        completeness, missing = compute_completeness(e)
        assert completeness == "COMPLETE"

    def test_compute_completeness_all_missing(self) -> None:
        e = NewSignalEntities()
        completeness, missing = compute_completeness(e)
        assert completeness == "INCOMPLETE"
        # symbol, direction, entry_type, stop_loss, take_profits all missing
        assert len(missing) >= 4


# ---------------------------------------------------------------------------
# UpdateEntities
# ---------------------------------------------------------------------------

class TestUpdateEntities:
    def test_defaults_all_none(self) -> None:
        u = UpdateEntities()
        assert u.new_sl_level is None
        assert u.close_price is None
        assert u.close_pct is None
        assert u.reenter_entries == []
        assert u.reenter_entry_type is None
        assert u.new_entry_price is None
        assert u.new_entry_type is None
        assert u.old_entry_price is None
        assert u.modified_entry_price is None
        assert u.old_take_profits is None
        assert u.new_take_profits == []
        assert u.tp_hit_number is None
        assert u.reported_profit_r is None
        assert u.reported_profit_pct is None

    def test_move_stop(self) -> None:
        u = UpdateEntities(new_sl_level=Price.from_float(91000.0))
        assert u.new_sl_level is not None
        assert u.new_sl_level.value == 91000.0

    def test_move_stop_to_be_none_level(self) -> None:
        """new_sl_level=None encodes "move to breakeven"."""
        u = UpdateEntities(new_sl_level=None)
        assert u.new_sl_level is None

    def test_close_partial(self) -> None:
        u = UpdateEntities(close_pct=50.0)
        assert u.close_pct == 50.0

    def test_update_take_profits(self) -> None:
        new_tps = [Price.from_float(96000.0), Price.from_float(98000.0)]
        u = UpdateEntities(new_take_profits=new_tps)
        assert len(u.new_take_profits) == 2

    def test_tp_hit_context(self) -> None:
        u = UpdateEntities(tp_hit_number=2, reported_profit_pct=3.5)
        assert u.tp_hit_number == 2
        assert u.reported_profit_pct == 3.5


# ---------------------------------------------------------------------------
# EntryLevel / StopLoss / TakeProfit
# ---------------------------------------------------------------------------

class TestEntryLevel:
    def test_limit_entry(self) -> None:
        e = EntryLevel(price=Price.from_float(92000.0), order_type="LIMIT")
        assert e.order_type == "LIMIT"
        assert e.price is not None
        assert e.price.value == 92000.0

    def test_market_entry_no_price(self) -> None:
        e = EntryLevel(price=None, order_type="MARKET")
        assert e.price is None
        assert e.order_type == "MARKET"

    def test_entry_with_note(self) -> None:
        e = EntryLevel(
            price=Price.from_float(1900.0),
            order_type="LIMIT",
            note="wait for candle close",
        )
        assert e.note == "wait for candle close"


class TestStopLoss:
    def test_basic(self) -> None:
        s = StopLoss(price=Price.from_float(89000.0))
        assert s.price.value == 89000.0
        assert s.trailing is False
        assert s.condition is None

    def test_trailing(self) -> None:
        s = StopLoss(price=Price.from_float(89000.0), trailing=True)
        assert s.trailing is True

    def test_frozen(self) -> None:
        s = StopLoss(price=Price.from_float(89000.0))
        with pytest.raises(Exception):
            s.trailing = True  # type: ignore[misc]


class TestTakeProfit:
    def test_basic(self) -> None:
        t = TakeProfit(price=Price.from_float(95000.0))
        assert t.price.value == 95000.0
        assert t.label is None
        assert t.close_pct is None

    def test_with_label_and_pct(self) -> None:
        t = TakeProfit(price=Price.from_float(95000.0), label="TP1", close_pct=50.0)
        assert t.label == "TP1"
        assert t.close_pct == 50.0

    def test_frozen(self) -> None:
        t = TakeProfit(price=Price.from_float(95000.0))
        with pytest.raises(Exception):
            t.label = "TP1"  # type: ignore[misc]
