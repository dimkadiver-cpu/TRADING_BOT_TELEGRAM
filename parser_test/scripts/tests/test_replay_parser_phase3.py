from __future__ import annotations

from parser_test.scripts.replay_parser import _build_normalized_payload
from src.parser.trader_profiles.base import ParserContext, TraderParseResult


def _sample_context() -> ParserContext:
    return ParserContext(
        trader_code="trader_a",
        message_id=101,
        reply_to_message_id=None,
        channel_id="-1001",
        raw_text="BTCUSDT LONG entry 50000 sl 48000 tp 52000",
    )


def _sample_result() -> TraderParseResult:
    return TraderParseResult(
        message_type="NEW_SIGNAL",
        intents=["NS_CREATE_SIGNAL"],
        entities={
            "symbol": "BTCUSDT",
            "side": "LONG",
            "stop_loss": 48000.0,
            "take_profits": [52000.0],
            "entry_plan_entries": [
                {
                    "sequence": 1,
                    "role": "PRIMARY",
                    "order_type": "LIMIT",
                    "price": 50000.0,
                    "is_optional": False,
                }
            ],
            "entry_structure": "ONE_SHOT",
        },
        warnings=["legacy_warning"],
        confidence=0.9,
        primary_intent="NS_CREATE_SIGNAL",
        actions_structured=[{"action_type": "CREATE_SIGNAL"}],
    )


def test_build_normalized_payload_both_contains_legacy_envelope_and_canonical() -> None:
    payload = _build_normalized_payload(
        result=_sample_result(),
        context=_sample_context(),
        parser_system="both",
    )

    assert payload["parser_system"] == "both"
    assert payload["message_type"] == "NEW_SIGNAL"
    assert payload["entities"]["symbol"] == "BTCUSDT"
    assert payload["event_envelope_v1"]["message_type_hint"] == "NEW_SIGNAL"
    assert payload["event_envelope_v1"]["instrument"]["symbol"] == "BTCUSDT"
    assert payload["canonical_message_v1"]["primary_class"] == "SIGNAL"
    assert payload["canonical_message_v1"]["signal"]["symbol"] == "BTCUSDT"


def test_build_normalized_payload_common_keeps_only_common_views() -> None:
    payload = _build_normalized_payload(
        result=_sample_result(),
        context=_sample_context(),
        parser_system="common",
    )

    assert payload["parser_system"] == "common"
    assert "message_type" not in payload
    assert "entities" not in payload
    assert "actions_structured" not in payload
    assert payload["event_envelope_v1"]["message_type_hint"] == "NEW_SIGNAL"
    assert payload["canonical_message_v1"]["primary_class"] == "SIGNAL"
