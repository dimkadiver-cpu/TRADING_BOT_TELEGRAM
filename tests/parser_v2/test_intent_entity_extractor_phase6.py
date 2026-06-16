from __future__ import annotations

from src.parser_v2.contracts.entities import (
    AddEntryEntities,
    CancelPendingEntities,
    ClosePartialEntities,
    EntryFilledEntities,
    ExitBeEntities,
    InfoOnlyEntities,
    ModifyEntryEntities,
    ModifyTargetsEntities,
    MoveStopEntities,
    ReportResultEntities,
    SlHitEntities,
    TpHitEntities,
)
from src.parser_v2.core.marker_evidence_resolver import MarkerEvidenceResolver
from src.parser_v2.core.marker_matcher import MarkerMatcher
from src.parser_v2.core.text_normalizer import TextNormalizer
from src.parser_v2.profiles.Legacy.trader_a_legacy.intent_entity_extractor import IntentEntityExtractor
from src.parser_v2.profiles.Legacy.trader_a_legacy.profile import TraderAProfile


STOP_TO_BE = "\u0441\u0442\u043e\u043f \u0432 \u0431\u0443"
STOP_TO_TP1 = "\u0441\u0442\u043e\u043f \u043d\u0430 1 \u0442\u0435\u0439\u043a"
STOP_TO_PRICE = "\u0441\u0442\u043e\u043f \u043d\u0430 2140"
CLOSE_PARTIAL_50 = "\u0444\u0438\u043a\u0441 50%"
CANCEL_LIMITS = "\u0443\u0431\u0438\u0440\u0430\u0435\u043c \u043b\u0438\u043c\u0438\u0442\u043a\u0438"
INVALIDATE = "\u0442\u0443\u0442 \u043e\u0442\u043c\u0435\u043d\u0430"
REENTER = "\u043f\u0435\u0440\u0435\u0437\u0430\u0445\u043e\u0434\u0438\u043c 2114"
ADD_ENTRY = "\u0434\u043e\u0431\u0430\u0432\u043b\u044f\u044e \u0432\u0445\u043e\u0434 2114"
NEW_ENTRY = "\u043d\u043e\u0432\u044b\u0439 \u0432\u0445\u043e\u0434 2114"
ENTER_MARKET = "\u0432\u0445\u043e\u0434\u0438\u043c \u043f\u043e \u0440\u044b\u043d\u043a\u0443"
REMOVE_ENTRY = "\u0443\u0431\u0438\u0440\u0430\u0435\u043c \u0432\u0445\u043e\u0434"
NEW_TARGETS = "\u043d\u043e\u0432\u044b\u0435 \u0442\u0435\u0439\u043a\u0438 2200 2300"
ENTRY_FILLED = "\u0432\u0445\u043e\u0434 \u0438\u0441\u043f\u043e\u043b\u043d\u0435\u043d 2114"
TP1_HIT = "\u043f\u0435\u0440\u0432\u044b\u0439 \u0442\u0435\u0439\u043a \u0432\u0437\u044f\u043b\u0438 2200"
SL_HIT = "\u0432\u044b\u0431\u0438\u043b\u043e \u043f\u043e \u0441\u0442\u043e\u043f\u0443 2100"
EXIT_BE = "\u0437\u0430\u043a\u0440\u044b\u043b\u0441\u044f \u0432 \u0431\u0443"
REPORT_RESULT = "\u0438\u0442\u043e\u0433 \u043f\u043e \u0441\u0434\u0435\u043b\u043a\u0435 +3R +50% +120$"
MARKET_OVERVIEW = "\u043e\u0431\u0437\u043e\u0440 \u0440\u044b\u043d\u043a\u0430"

_profile = TraderAProfile()
_markers = _profile.load_markers()
_rules = _profile.load_rules()
_matcher = MarkerMatcher()
_resolver = MarkerEvidenceResolver()
_extractor = IntentEntityExtractor()


def _extract(text: str):
    normalized = TextNormalizer().normalize(text)
    matches = _matcher.match(normalized, _markers)
    resolution = _resolver.resolve(matches, _rules)
    return _extractor.extract(normalized, resolution.evidence)


def _one(text: str):
    intents = _extract(text)
    assert len(intents) == 1, f"expected 1 intent, got {[i.type for i in intents]}"
    return intents[0]


def test_extracts_update_intents_with_minimal_entities() -> None:
    stop_to_be = _one(STOP_TO_BE)
    assert stop_to_be.type == "MOVE_STOP_TO_BE"
    assert stop_to_be.category == "UPDATE"
    assert stop_to_be.entities.__class__.__name__ == "MoveStopToBEEntities"

    move_stop = _one(STOP_TO_TP1)
    assert move_stop.type == "MOVE_STOP"
    assert isinstance(move_stop.entities, MoveStopEntities)
    assert move_stop.entities.stop_to_tp_level == 1

    move_stop_price = _one(STOP_TO_PRICE)
    assert move_stop_price.type == "MOVE_STOP"
    assert isinstance(move_stop_price.entities, MoveStopEntities)
    assert move_stop_price.entities.new_stop_price is not None
    assert move_stop_price.entities.new_stop_price.value == 2140.0

    close_full = _one("close all at 2114")
    assert close_full.type == "CLOSE_FULL"
    assert close_full.category == "UPDATE"

    close_partial = _one(CLOSE_PARTIAL_50)
    assert close_partial.type == "CLOSE_PARTIAL"
    assert isinstance(close_partial.entities, ClosePartialEntities)
    assert close_partial.entities.fraction == 0.5

    cancel_pending = _one(CANCEL_LIMITS)
    assert cancel_pending.type == "CANCEL_PENDING"
    assert isinstance(cancel_pending.entities, CancelPendingEntities)
    assert cancel_pending.entities.cancel_scope_hint == "ALL_PENDING"

    invalidate = _one(INVALIDATE)
    assert invalidate.type == "INVALIDATE_SETUP"
    assert invalidate.entities.__class__.__name__ == "InvalidateSetupEntities"

    reenter = _one(REENTER)
    assert reenter.type == "REENTER"
    assert reenter.entities.entries[0].value == 2114.0

    add_entry = _one(ADD_ENTRY)
    assert add_entry.type == "ADD_ENTRY"
    assert isinstance(add_entry.entities, AddEntryEntities)
    assert add_entry.entities.entry_price is not None
    assert add_entry.entities.entry_price.value == 2114.0

    modify_entry = _one(NEW_ENTRY)
    assert modify_entry.type == "MODIFY_ENTRY"
    assert isinstance(modify_entry.entities, ModifyEntryEntities)
    assert modify_entry.entities.mode == "UPDATE_PRICE"
    assert modify_entry.entities.entries[0].price is not None
    assert modify_entry.entities.entries[0].price.value == 2114.0

    modify_entry_market = _one(ENTER_MARKET)
    assert modify_entry_market.type == "MODIFY_ENTRY"
    assert isinstance(modify_entry_market.entities, ModifyEntryEntities)
    assert modify_entry_market.entities.mode == "MARKET_NOW"
    assert modify_entry_market.entities.entries[0].entry_type == "MARKET"
    assert modify_entry_market.entities.entries[0].price is None

    modify_entry_remove = _one(REMOVE_ENTRY)
    assert modify_entry_remove.type == "MODIFY_ENTRY"
    assert isinstance(modify_entry_remove.entities, ModifyEntryEntities)
    assert modify_entry_remove.entities.mode == "REMOVE"
    assert modify_entry_remove.entities.entries == []

    modify_targets = _one(NEW_TARGETS)
    assert modify_targets.type == "MODIFY_TARGETS"
    assert isinstance(modify_targets.entities, ModifyTargetsEntities)
    assert [price.value for price in modify_targets.entities.take_profits] == [2200.0, 2300.0]


def test_extracts_report_and_info_intents_without_result_metrics() -> None:
    entry_filled = _one(ENTRY_FILLED)
    assert entry_filled.type == "ENTRY_FILLED"
    assert entry_filled.category == "REPORT"
    assert isinstance(entry_filled.entities, EntryFilledEntities)
    assert entry_filled.entities.fill_price is not None
    assert entry_filled.entities.fill_price.value == 2114.0

    tp_hit = _one(TP1_HIT)
    assert tp_hit.type == "TP_HIT"
    assert isinstance(tp_hit.entities, TpHitEntities)
    assert tp_hit.entities.level == 1
    assert tp_hit.entities.price is not None
    assert tp_hit.entities.price.value == 2200.0

    sl_hit = _one(SL_HIT)
    assert sl_hit.type == "SL_HIT"
    assert isinstance(sl_hit.entities, SlHitEntities)
    assert sl_hit.entities.price is not None
    assert sl_hit.entities.price.value == 2100.0

    exit_be = _one(EXIT_BE)
    assert exit_be.type == "EXIT_BE"
    assert isinstance(exit_be.entities, ExitBeEntities)

    report_result = _one(REPORT_RESULT)
    assert report_result.type == "REPORT_RESULT"
    assert isinstance(report_result.entities, ReportResultEntities)
    assert report_result.entities.raw_summary == REPORT_RESULT
    assert set(type(report_result.entities).model_fields) == {"raw_summary"}

    info = _one(MARKET_OVERVIEW)
    assert info.type == "INFO_ONLY"
    assert info.category == "INFO"
    assert isinstance(info.entities, InfoOnlyEntities)
    assert info.entities.raw_fragment == MARKET_OVERVIEW


def test_unknown_result_metrics_do_not_create_operational_entities() -> None:
    intents = _extract("+3R +50% +120$")

    assert intents == []


def test_extracted_intent_includes_fragment_span_and_evidence() -> None:
    # "стоп в бу" starts at position 8 in "пожалуйста стоп в бу сейчас"
    text = "пожалуйста стоп в бу сейчас"
    marker = "стоп в бу"
    intent = _one(text)

    assert intent.type == "MOVE_STOP_TO_BE"
    assert intent.raw_fragment == marker
    assert intent.span_start == text.index(marker)
    assert intent.span_end == text.index(marker) + len(marker)
    assert intent.evidence[0].name == "MOVE_STOP_TO_BE"
    assert intent.evidence[0].marker == marker
