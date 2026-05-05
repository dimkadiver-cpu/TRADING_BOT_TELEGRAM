from __future__ import annotations

from src.parser_v2.contracts.entities import (
    EntryLeg,
    ModifyEntryEntities,
    MoveStopEntities,
    MoveStopToBEEntities,
    TakeProfit,
    Price,
    SlHitEntities,
    StopLoss,
    TpHitEntities,
)
from src.parser_v2.contracts.markers import MarkerEvidence, NormalizedText
from src.parser_v2.contracts.parsed_message import ParsedIntent, SignalDraft
from src.parser_v2.contracts.rules import ParserRules
from src.parser_v2.core.local_disambiguator import LocalDisambiguator


def _intent(intent_type: str, entities=None, marker: str | None = None) -> ParsedIntent:
    return ParsedIntent(
        type=intent_type,
        category="UPDATE" if intent_type.startswith(("MOVE_", "MODIFY_")) else "REPORT",
        confidence=1.0,
        entities=entities or {},
        evidence=[
            MarkerEvidence(
                name=intent_type,
                kind="intent",
                strength="strong",
                marker=marker or intent_type.lower(),
                start=0,
                end=len(marker or intent_type),
            )
        ],
        raw_fragment=marker or intent_type.lower(),
    )


def test_prefer_rule_suppresses_only_configured_over_intents() -> None:
    rules = ParserRules(
        disambiguation=[
            {
                "name": "prefer_move_stop_to_be_over_move_stop",
                "when_all_detected": ["MOVE_STOP_TO_BE", "MOVE_STOP"],
                "prefer": "MOVE_STOP_TO_BE",
                "over": ["MOVE_STOP"],
            }
        ],
        primary_intent_precedence=["MOVE_STOP_TO_BE", "MOVE_STOP"],
    )

    result = LocalDisambiguator().resolve(
        [
            _intent("MOVE_STOP_TO_BE", MoveStopToBEEntities()),
            _intent("MOVE_STOP", MoveStopEntities()),
        ],
        rules,
    )

    assert [intent.type for intent in result.intents] == ["MOVE_STOP_TO_BE"]
    assert [intent.type for intent in result.suppressed_intents] == ["MOVE_STOP"]
    assert result.primary_intent == "MOVE_STOP_TO_BE"
    assert result.diagnostics["applied_disambiguation_rules"] == [
        "prefer_move_stop_to_be_over_move_stop"
    ]


def test_suppress_rule_removes_configured_intents() -> None:
    rules = ParserRules(
        disambiguation=[
            {
                "name": "suppress_move_stop_when_sl_hit_detected",
                "when_all_detected": ["SL_HIT", "MOVE_STOP"],
                "suppress": ["MOVE_STOP"],
            }
        ],
        primary_intent_precedence=["SL_HIT", "MOVE_STOP"],
    )

    result = LocalDisambiguator().resolve(
        [
            _intent("SL_HIT", SlHitEntities()),
            _intent("MOVE_STOP", MoveStopEntities()),
        ],
        rules,
    )

    assert [intent.type for intent in result.intents] == ["SL_HIT"]
    assert [intent.type for intent in result.suppressed_intents] == ["MOVE_STOP"]
    assert result.primary_intent == "SL_HIT"
    assert result.diagnostics["applied_disambiguation_rules"] == [
        "suppress_move_stop_when_sl_hit_detected"
    ]


def test_primary_intent_uses_precedence_without_dropping_compatible_composite() -> None:
    rules = ParserRules(
        primary_intent_precedence=[
            "SL_HIT",
            "EXIT_BE",
            "TP_HIT",
            "MOVE_STOP_TO_BE",
        ]
    )

    result = LocalDisambiguator().resolve(
        [
            _intent("MOVE_STOP_TO_BE", MoveStopToBEEntities()),
            _intent("TP_HIT", TpHitEntities(level=1)),
        ],
        rules,
    )

    assert [intent.type for intent in result.intents] == ["MOVE_STOP_TO_BE", "TP_HIT"]
    assert result.primary_intent == "TP_HIT"
    assert result.suppressed_intents == []


def test_context_market_marker_is_not_modify_entry_when_signal_is_present() -> None:
    market_marker = "\u0432\u0445\u043e\u0434\u0438\u043c \u043f\u043e \u0440\u044b\u043d\u043a\u0443"
    signal = SignalDraft(
        symbol="BTCUSDT",
        side="LONG",
        entry_structure="ONE_SHOT",
        entries=[EntryLeg(sequence=1, entry_type="MARKET", role="PRIMARY")],
        stop_loss=StopLoss(price=Price(raw="61000", value=61000.0)),
        take_profits=[TakeProfit(sequence=1, price=Price(raw="63000", value=63000.0))],
        missing_fields=[],
        completeness="COMPLETE",
    )
    rules = ParserRules(
        disambiguation=[
            {
                "name": "context_market_marker",
                "when_marker_in": [market_marker],
                "if_signal_payload_present": {"interpret_as": "ENTRY_TYPE_MARKET"},
                "if_signal_payload_absent": {"interpret_as": "MODIFY_ENTRY_MARKET_NOW"},
            }
        ],
        primary_intent_precedence=["MODIFY_ENTRY"],
    )

    result = LocalDisambiguator().resolve(
        [
            _intent(
                "MODIFY_ENTRY",
                ModifyEntryEntities(
                    mode="MARKET_NOW",
                    entries=[EntryLeg(sequence=1, entry_type="MARKET", role="PRIMARY")],
                    raw_mode_marker=market_marker,
                ),
                market_marker,
            )
        ],
        rules,
        signal=signal,
        normalized=NormalizedText(
            raw_text=market_marker,
            normalized_text=market_marker,
            lines=[market_marker],
        ),
    )

    assert result.intents == []
    assert [intent.type for intent in result.suppressed_intents] == ["MODIFY_ENTRY"]
    assert result.primary_intent is None
    assert result.diagnostics["applied_disambiguation_rules"] == [
        "context_market_marker"
    ]


def test_context_market_marker_stays_modify_entry_without_signal() -> None:
    market_marker = "\u0432\u0445\u043e\u0434\u0438\u043c \u043f\u043e \u0440\u044b\u043d\u043a\u0443"
    rules = ParserRules(
        disambiguation=[
            {
                "name": "context_market_marker",
                "when_marker_in": [market_marker],
                "if_signal_payload_present": {"interpret_as": "ENTRY_TYPE_MARKET"},
                "if_signal_payload_absent": {"interpret_as": "MODIFY_ENTRY_MARKET_NOW"},
            }
        ],
        primary_intent_precedence=["MODIFY_ENTRY"],
    )

    result = LocalDisambiguator().resolve(
        [
            _intent(
                "MODIFY_ENTRY",
                ModifyEntryEntities(mode="MARKET_NOW", raw_mode_marker=market_marker),
                market_marker,
            )
        ],
        rules,
        signal=None,
        normalized=NormalizedText(
            raw_text=market_marker,
            normalized_text=market_marker,
            lines=[market_marker],
        ),
    )

    assert [intent.type for intent in result.intents] == ["MODIFY_ENTRY"]
    assert result.suppressed_intents == []
    assert result.primary_intent == "MODIFY_ENTRY"
