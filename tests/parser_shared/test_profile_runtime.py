"""Tests for SharedProfileRuntime.

Verifies:
- returns TraderEventEnvelopeV1
- normalizes intents from legacy names
- extracts targets from ParserContext (reply_to_message_id, extracted_links)
- message_type_hint flows from RulesEngine classification
- unknown intent from extractor adds warning, does not crash
"""

from __future__ import annotations

from typing import Any

import pytest

from src.parser.trader_profiles.shared.profile_runtime import SharedProfileRuntime
from src.parser.trader_profiles.base import ParserContext
from src.parser.rules_engine import RulesEngine
from src.parser.event_envelope_v1 import TraderEventEnvelopeV1, InstrumentRaw
from src.parser.trader_profiles.shared.warnings import (
    MISSING_TARGET,
    INTENT_OUTSIDE_TAXONOMY,
)


# ---------------------------------------------------------------------------
# Minimal extractor stubs
# ---------------------------------------------------------------------------

class EmptyExtractor:
    """Extractor that returns nothing — minimal stub."""

    def extract(self, text: str, context: ParserContext, rules: RulesEngine) -> dict[str, Any]:
        return {}


class SignalExtractor:
    """Stub extractor that returns a minimal instrument."""

    def extract(self, text: str, context: ParserContext, rules: RulesEngine) -> dict[str, Any]:
        return {
            "instrument": InstrumentRaw(symbol="BTCUSDT", side="LONG"),
        }


class LegacyIntentExtractor:
    """Stub extractor that returns legacy intent names as extras."""

    def extract(self, text: str, context: ParserContext, rules: RulesEngine) -> dict[str, Any]:
        return {
            "intents_extra": ["U_STOP_HIT"],
        }


class UnknownIntentExtractor:
    """Stub extractor that injects an intent outside the taxonomy."""

    def extract(self, text: str, context: ParserContext, rules: RulesEngine) -> dict[str, Any]:
        return {
            "intents_extra": ["U_RISK_NOTE"],
        }


def _make_context(
    reply_to_message_id: int | None = None,
    extracted_links: list[str] | None = None,
) -> ParserContext:
    return ParserContext(
        trader_code="trader_test",
        message_id=1,
        reply_to_message_id=reply_to_message_id,
        channel_id="test_channel",
        raw_text="test",
        extracted_links=extracted_links or [],
    )


def _simple_engine() -> RulesEngine:
    return RulesEngine.from_dict({
        "classification_markers": {
            "update": {"strong": ["stop hit"], "weak": []},
        },
        "intent_markers": {
            "SL_HIT": {
                "strong": ["stop hit"],
                "weak": [],
            }
        },
    })


def _new_signal_engine() -> RulesEngine:
    return RulesEngine.from_dict({
        "classification_markers": {
            "new_signal": {"strong": ["entry:", "sl:"], "weak": []},
        },
    })


# ---------------------------------------------------------------------------
# SharedProfileRuntime basic contract
# ---------------------------------------------------------------------------

class TestSharedProfileRuntimeBasic:
    def test_parse_returns_trader_event_envelope_v1(self) -> None:
        runtime = SharedProfileRuntime()
        result = runtime.parse(
            trader_code="trader_test",
            text="hello world",
            context=_make_context(),
            rules=RulesEngine.from_dict({}),
            extractors=EmptyExtractor(),
        )
        assert isinstance(result, TraderEventEnvelopeV1)

    def test_parse_propagates_message_type_hint_from_classify(self) -> None:
        runtime = SharedProfileRuntime()
        result = runtime.parse(
            trader_code="trader_test",
            text="stop hit",
            context=_make_context(),
            rules=_simple_engine(),
            extractors=EmptyExtractor(),
        )
        assert result.message_type_hint == "UPDATE"

    def test_parse_unclassified_when_no_markers(self) -> None:
        runtime = SharedProfileRuntime()
        result = runtime.parse(
            trader_code="trader_test",
            text="some random text",
            context=_make_context(),
            rules=RulesEngine.from_dict({}),
            extractors=EmptyExtractor(),
        )
        assert result.message_type_hint == "UNCLASSIFIED"

    def test_parse_includes_trader_code_in_diagnostics(self) -> None:
        runtime = SharedProfileRuntime()
        result = runtime.parse(
            trader_code="trader_a",
            text="hello",
            context=_make_context(),
            rules=RulesEngine.from_dict({}),
            extractors=EmptyExtractor(),
        )
        assert result.diagnostics.get("trader_code") == "trader_a"


# ---------------------------------------------------------------------------
# Intent normalization
# ---------------------------------------------------------------------------

class TestIntentNormalization:
    def test_intents_from_engine_included_in_output(self) -> None:
        runtime = SharedProfileRuntime()
        result = runtime.parse(
            trader_code="trader_test",
            text="stop hit",
            context=_make_context(),
            rules=_simple_engine(),
            extractors=EmptyExtractor(),
        )
        assert "SL_HIT" in result.intents_detected

    def test_legacy_intents_from_extractor_normalized(self) -> None:
        runtime = SharedProfileRuntime()
        result = runtime.parse(
            trader_code="trader_test",
            text="hello",
            context=_make_context(),
            rules=RulesEngine.from_dict({}),
            extractors=LegacyIntentExtractor(),
        )
        assert "SL_HIT" in result.intents_detected
        assert "U_STOP_HIT" not in result.intents_detected

    def test_unknown_intent_from_extractor_adds_warning(self) -> None:
        runtime = SharedProfileRuntime()
        result = runtime.parse(
            trader_code="trader_test",
            text="hello",
            context=_make_context(),
            rules=RulesEngine.from_dict({}),
            extractors=UnknownIntentExtractor(),
        )
        assert INTENT_OUTSIDE_TAXONOMY in result.warnings


# ---------------------------------------------------------------------------
# Target extraction from context
# ---------------------------------------------------------------------------

class TestTargetExtractionFromContext:
    def test_reply_to_message_id_becomes_target_ref(self) -> None:
        runtime = SharedProfileRuntime()
        context = _make_context(reply_to_message_id=42)
        result = runtime.parse(
            trader_code="trader_test",
            text="stop hit",
            context=context,
            rules=_simple_engine(),
            extractors=EmptyExtractor(),
        )
        reply_refs = [t for t in result.targets_raw if t.kind == "REPLY"]
        assert len(reply_refs) == 1
        assert reply_refs[0].value == 42

    def test_no_reply_no_target_refs_from_context(self) -> None:
        runtime = SharedProfileRuntime()
        context = _make_context(reply_to_message_id=None)
        result = runtime.parse(
            trader_code="trader_test",
            text="stop hit",
            context=context,
            rules=_simple_engine(),
            extractors=EmptyExtractor(),
        )
        reply_refs = [t for t in result.targets_raw if t.kind == "REPLY"]
        assert len(reply_refs) == 0

    def test_telegram_link_in_context_produces_target_ref(self) -> None:
        runtime = SharedProfileRuntime()
        context = _make_context(
            extracted_links=["https://t.me/c/1234567/999"]
        )
        result = runtime.parse(
            trader_code="trader_test",
            text="see above https://t.me/c/1234567/999",
            context=context,
            rules=RulesEngine.from_dict({}),
            extractors=EmptyExtractor(),
        )
        link_refs = [t for t in result.targets_raw if t.kind == "TELEGRAM_LINK"]
        assert len(link_refs) == 1


# ---------------------------------------------------------------------------
# Instrument from extractor
# ---------------------------------------------------------------------------

class TestInstrumentFromExtractor:
    def test_instrument_from_extractor_propagated(self) -> None:
        runtime = SharedProfileRuntime()
        result = runtime.parse(
            trader_code="trader_test",
            text="entry: 100",
            context=_make_context(),
            rules=_new_signal_engine(),
            extractors=SignalExtractor(),
        )
        assert result.instrument.symbol == "BTCUSDT"
        assert result.instrument.side == "LONG"
