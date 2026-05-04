from __future__ import annotations

from typing import Any

from src.parser.canonical_v1.models import Price, RawContext, TargetRef, TargetScope, Targeting
from src.parser.parsed_message import MoveStopEntities, MoveStopToBEEntities, ParsedMessage
from src.parser.rules_engine import RulesEngine
from src.parser.shared.runtime import parse
from src.parser.trader_profiles.base import ParserContext


def _context(
    *,
    raw_text: str,
    reply_to_message_id: int | None = None,
    extracted_links: list[str] | None = None,
) -> ParserContext:
    return ParserContext(
        trader_code="trader_test",
        message_id=100,
        reply_to_message_id=reply_to_message_id,
        channel_id="channel",
        raw_text=raw_text,
        extracted_links=extracted_links or [],
    )


class _UpdateExtractor:
    def extract(self, text: str, context: ParserContext, rules: RulesEngine) -> dict[str, Any]:
        return {
            "intents": [
                {
                    "type": "MOVE_STOP_TO_BE",
                    "entities": MoveStopToBEEntities(),
                    "raw_fragment": "stop to breakeven",
                    "confidence": 0.91,
                }
            ]
        }


class _MultiRefExtractor:
    def extract(self, text: str, context: ParserContext, rules: RulesEngine) -> dict[str, Any]:
        btc_targeting = Targeting(
            refs=[TargetRef(ref_type="SYMBOL", value="BTCUSDT")],
            scope=TargetScope(kind="SYMBOL", value="BTCUSDT"),
            strategy="SYMBOL_MATCH",
            targeted=True,
        )
        eth_targeting = Targeting(
            refs=[TargetRef(ref_type="SYMBOL", value="ETHUSDT")],
            scope=TargetScope(kind="SYMBOL", value="ETHUSDT"),
            strategy="SYMBOL_MATCH",
            targeted=True,
        )
        return {
            "targeting": Targeting(
                refs=[
                    TargetRef(ref_type="SYMBOL", value="BTCUSDT"),
                    TargetRef(ref_type="SYMBOL", value="ETHUSDT"),
                ],
                scope=TargetScope(kind="UNKNOWN"),
                strategy="SYMBOL_MATCH",
                targeted=True,
            ),
            "intents": [
                {
                    "type": "MOVE_STOP_TO_BE",
                    "entities": MoveStopToBEEntities(),
                    "targeting_override": btc_targeting,
                },
                {
                    "type": "MOVE_STOP",
                    "entities": MoveStopEntities(
                        new_stop_price=Price(raw="2450", value=2450.0),
                    ),
                    "targeting_override": eth_targeting,
                },
            ],
        }


class _EmptyExtractor:
    def extract(self, text: str, context: ParserContext, rules: RulesEngine) -> dict[str, Any]:
        return {}


def test_phase3_runtime_builds_update_parsed_message_with_detection_strength() -> None:
    rules = RulesEngine.from_dict(
        {
            "classification_markers": {
                "update": {"strong": ["breakeven"], "weak": []},
            },
            "intent_markers": {
                "MOVE_STOP_TO_BE": {"strong": ["breakeven"], "weak": ["be"]},
            },
        }
    )

    parsed = parse(
        trader_code="trader_test",
        text="Move stop to breakeven",
        context=_context(raw_text="Move stop to breakeven", reply_to_message_id=42),
        rules=rules,
        extractors=_UpdateExtractor(),
    )

    assert isinstance(parsed, ParsedMessage)
    assert parsed.primary_class == "UPDATE"
    assert parsed.parse_status == "PARSED"
    assert parsed.intents[0].type == "MOVE_STOP_TO_BE"
    assert parsed.intents[0].detection_strength == "strong"
    assert parsed.intents[0].category == "UPDATE"
    assert parsed.targeting is not None
    assert parsed.targeting.refs[0].ref_type == "REPLY"
    assert parsed.diagnostics["resolution_unit"] == "MESSAGE_WIDE"


def test_phase3_runtime_marks_target_item_wide_when_intents_override_targeting() -> None:
    rules = RulesEngine.from_dict(
        {
            "classification_markers": {
                "update": {"strong": ["stop"], "weak": []},
            },
            "intent_markers": {
                "MOVE_STOP_TO_BE": {"strong": ["breakeven"], "weak": []},
                "MOVE_STOP": {"strong": ["new stop"], "weak": []},
            },
        }
    )

    parsed = parse(
        trader_code="trader_test",
        text="BTC breakeven, ETH new stop 2450",
        context=_context(raw_text="BTC breakeven, ETH new stop 2450"),
        rules=rules,
        extractors=_MultiRefExtractor(),
    )

    assert parsed.diagnostics["resolution_unit"] == "TARGET_ITEM_WIDE"
    assert [intent.targeting_override.refs[0].value for intent in parsed.intents] == [
        "BTCUSDT",
        "ETHUSDT",
    ]


def test_phase3_runtime_unclassified_message_defaults_to_info_shell() -> None:
    parsed = parse(
        trader_code="trader_test",
        text="just chatting",
        context=_context(raw_text="just chatting"),
        rules=RulesEngine.from_dict({}),
        extractors=_EmptyExtractor(),
    )

    assert parsed.primary_class == "INFO"
    assert parsed.parse_status == "UNCLASSIFIED"
    assert parsed.confidence == 0.0
    assert parsed.intents == []
    assert parsed.raw_context.raw_text == "just chatting"
