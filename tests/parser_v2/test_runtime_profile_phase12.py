from __future__ import annotations

from src.parser_v2.contracts.context import ParserContext
from src.parser_v2.core.runtime import UniversalParserRuntime, parse
from src.parser_v2.profiles.trader_a.profile import TraderAProfile


STOP_TO_BE = "\u0441\u0442\u043e\u043f \u0432 \u0431\u0443"
BE = "\u0431\u0443"


def test_runtime_parse_uses_trader_a_profile_for_signal_to_canonical_message() -> None:
    text = "\n".join(
        [
            "#ETHUSDT",
            "long",
            "entry: 2114",
            "stop: 2100",
            "tp1: 2200",
        ]
    )

    canonical = UniversalParserRuntime().parse(text, ParserContext(message_id=101), TraderAProfile())

    assert canonical.parser_profile == "trader_a"
    assert canonical.primary_class == "SIGNAL"
    assert canonical.parse_status == "PARSED"
    assert canonical.signal.symbol == "ETHUSDT"
    assert canonical.signal.missing_fields == []
    assert canonical.raw_context.message_id == 101


def test_runtime_parse_runs_marker_resolution_disambiguation_and_translation() -> None:
    canonical = parse(STOP_TO_BE, ParserContext(), TraderAProfile())

    assert canonical.primary_class == "UPDATE"
    assert canonical.primary_intent == "MOVE_STOP_TO_BE"
    assert canonical.intents == ["MOVE_STOP_TO_BE"]
    assert canonical.update.operations[0].op_type == "SET_STOP"
    assert canonical.update.operations[0].set_stop.target_type == "ENTRY"
    # "стоп" is a SL_HIT weak marker and a substring of "стоп в бу" — MarkerMatcher
    # finds it first (shorter span sorts before longer span at same start offset).
    # The extractor's span deduplication drops it in favour of the MOVE_STOP_TO_BE strong match.
    assert canonical.diagnostics["matched_markers"] == [
        f"SL_HIT/weak:стоп@0:4",
        f"MOVE_STOP_TO_BE/strong:{STOP_TO_BE}@0:9",
        f"MOVE_STOP_TO_BE/weak:{BE}@7:9",
        f"EXIT_BE/weak:{BE}@7:9",
    ]
    assert canonical.diagnostics["suppressed_markers"] == [
        f"MOVE_STOP_TO_BE/weak:{BE}@7:9",
        f"EXIT_BE/weak:{BE}@7:9",
    ]


def test_runtime_parse_unknown_text_returns_unclassified_info() -> None:
    canonical = UniversalParserRuntime().parse("asdfgh", ParserContext(), TraderAProfile())

    assert canonical.primary_class == "INFO"
    assert canonical.parse_status == "UNCLASSIFIED"
    assert canonical.info.raw_fragment == "asdfgh"


def test_trader_a_profile_exposes_only_profile_contract_not_legacy_entrypoints() -> None:
    profile = TraderAProfile()

    assert profile.trader_code == "trader_a"
    assert profile.load_markers().language == "ru"
    assert not hasattr(profile, "parse_message")
    assert not hasattr(profile, "parse_canonical")
