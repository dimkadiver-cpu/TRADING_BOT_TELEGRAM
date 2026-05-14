from __future__ import annotations

from src.parser_v2.contracts.context import ParserContext, RawContext
from src.parser_v2.contracts.rules import MarkerSet, ParserRules, SemanticMarkers
from src.parser_v2.core.runtime import UniversalParserRuntime


class _InfoShortCircuitProfile:
    trader_code = "trader_test"

    def __init__(self) -> None:
        self.signal_called = False
        self.intent_called = False

    def load_markers(self) -> SemanticMarkers:
        return SemanticMarkers(
            intent_markers={
                "MOVE_STOP": MarkerSet(strong=["buy"]),
            },
            info_markers={
                "ADMIN": MarkerSet(strong=["admin"]),
            },
        )

    def load_rules(self) -> ParserRules:
        return ParserRules()

    def extract_signal(self, text, context, evidence):  # type: ignore[no-untyped-def]
        self.signal_called = True
        raise AssertionError("signal extraction must be skipped for info messages")

    def extract_intent_entities(self, text, context, evidence):  # type: ignore[no-untyped-def]
        self.intent_called = True
        raise AssertionError("intent extraction must be skipped for info messages")


def test_info_marker_short_circuits_operational_parsing() -> None:
    runtime = UniversalParserRuntime()
    profile = _InfoShortCircuitProfile()
    text = "admin buy now with sl 123 and tp 456"
    context = ParserContext(raw_context=RawContext(raw_text=text))

    parsed = runtime.parse(text, context, profile)

    assert parsed.primary_class == "INFO"
    assert parsed.parse_status == "PARSED"
    assert parsed.signal is None
    assert parsed.intents == []
    assert parsed.primary_intent is None
    assert parsed.target_action_groups == []
    assert parsed.report is None
    assert parsed.info is not None
    assert parsed.info.raw_fragment == text
    assert profile.signal_called is False
    assert profile.intent_called is False
