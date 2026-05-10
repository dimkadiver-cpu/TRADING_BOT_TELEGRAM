from __future__ import annotations

import pytest

from src.parser_v2.contracts.entities import ModifyEntryEntities
from src.parser_v2.contracts.markers import MarkerEvidence, NormalizedText
from src.parser_v2.profiles.trader_a.intent_entity_extractor import IntentEntityExtractor


def _ev(
    name: str,
    kind: str,
    marker: str,
    start: int,
    strength: str = "strong",
    suppressed: bool = False,
) -> MarkerEvidence:
    return MarkerEvidence(
        name=name,
        kind=kind,  # type: ignore[arg-type]
        strength=strength,  # type: ignore[arg-type]
        marker=marker,
        start=start,
        end=start + len(marker),
        suppressed=suppressed,
    )


def _normalized(text: str) -> NormalizedText:
    return NormalizedText(
        raw_text=text,
        normalized_text=text,
        lines=text.splitlines() or [text],
    )


_extractor = IntentEntityExtractor()


def _extract_first(text: str, evidence: list[MarkerEvidence]) -> ModifyEntryEntities:
    intents = _extractor.extract(_normalized(text), evidence)
    assert len(intents) == 1, f"Expected 1 intent, got {len(intents)}: {intents}"
    e = intents[0].entities
    assert isinstance(e, ModifyEntryEntities), f"Expected ModifyEntryEntities, got {type(e)}"
    return e


def test_update_price_single_new_entry():
    """новый вход 2114 → UPDATE_PRICE / ONE_SHOT / LIMIT 2114"""
    text = "новый вход 2114"
    marker = "новый вход"
    evidence = [
        _ev("MODIFY_ENTRY", "intent", marker, 0),
        _ev("UPDATE_PRICE", "modify_entry_mode", marker, 0),
    ]
    e = _extract_first(text, evidence)
    assert e.mode == "UPDATE_PRICE"
    assert e.entry_structure == "ONE_SHOT"
    assert len(e.entries) == 1
    assert e.entries[0].entry_type == "LIMIT"
    assert e.entries[0].price.value == 2114.0
    assert e.entry_selector is None


def test_update_price_variant_vhod_teper():
    """вход теперь 2114 → UPDATE_PRICE / ONE_SHOT / LIMIT 2114"""
    text = "вход теперь 2114"
    marker = "вход теперь"
    evidence = [
        _ev("MODIFY_ENTRY", "intent", marker, 0),
        _ev("UPDATE_PRICE", "modify_entry_mode", marker, 0),
    ]
    e = _extract_first(text, evidence)
    assert e.mode == "UPDATE_PRICE"
    assert e.entry_structure == "ONE_SHOT"
    assert e.entries[0].price.value == 2114.0


def test_update_range_dash_separated():
    """вход теперь 2114-2120 → UPDATE_RANGE / RANGE / [LIMIT 2114, LIMIT 2120]"""
    text = "вход теперь 2114-2120"
    marker = "вход теперь"
    evidence = [
        _ev("MODIFY_ENTRY", "intent", marker, 0),
        _ev("UPDATE_PRICE", "modify_entry_mode", marker, 0),
    ]
    e = _extract_first(text, evidence)
    assert e.mode == "UPDATE_RANGE"
    assert e.entry_structure == "RANGE"
    assert len(e.entries) == 2
    assert e.entries[0].price.value == 2114.0
    assert e.entries[1].price.value == 2120.0


def test_ladder_three_prices():
    """вход теперь 2114 2100 2080 → UPDATE_PRICE / LADDER / 3 legs"""
    text = "вход теперь 2114 2100 2080"
    marker = "вход теперь"
    evidence = [
        _ev("MODIFY_ENTRY", "intent", marker, 0),
        _ev("UPDATE_PRICE", "modify_entry_mode", marker, 0),
    ]
    e = _extract_first(text, evidence)
    assert e.mode == "UPDATE_PRICE"
    assert e.entry_structure == "LADDER"
    assert len(e.entries) == 3
    assert [leg.price.value for leg in e.entries] == [2114.0, 2100.0, 2080.0]


def test_market_now():
    """входим по рынку → MARKET_NOW / ONE_SHOT / MARKET leg"""
    text = "входим по рынку"
    marker = "входим по рынку"
    evidence = [
        _ev("MODIFY_ENTRY", "intent", marker, 0),
        _ev("MARKET_NOW", "modify_entry_mode", marker, 0),
    ]
    e = _extract_first(text, evidence)
    assert e.mode == "MARKET_NOW"
    assert e.entry_structure == "ONE_SHOT"
    assert len(e.entries) == 1
    assert e.entries[0].entry_type == "MARKET"
    assert e.entries[0].price is None


def test_remove_legacy():
    """убираем вход → REMOVE / no entries / no Pydantic error"""
    text = "убираем вход"
    marker = "убираем вход"
    evidence = [
        _ev("MODIFY_ENTRY", "intent", marker, 0),
        _ev("REMOVE", "modify_entry_mode", marker, 0),
    ]
    e = _extract_first(text, evidence)
    assert e.mode == "REMOVE"
    assert e.entries == []
    assert e.entry_structure is None


def test_selector_primary():
    """основной вход переносим на 2114 → selector=PRIMARY / seq=1"""
    text = "основной вход переносим на 2114"
    intent_marker = "основной вход переносим"
    mode_marker = "основной вход переносим"
    selector_marker = "основной вход"
    evidence = [
        _ev("MODIFY_ENTRY", "intent", intent_marker, 0),
        _ev("UPDATE_PRICE", "modify_entry_mode", mode_marker, 0),
        _ev("PRIMARY", "entry_selector", selector_marker, 0),
    ]
    e = _extract_first(text, evidence)
    assert e.mode == "UPDATE_PRICE"
    assert e.entry_selector is not None
    assert e.entry_selector.role == "PRIMARY"
    assert e.entry_selector.sequence == 1
    assert e.entry_selector.raw == selector_marker
    assert e.entries[0].price.value == 2114.0


def test_selector_averaging():
    """усреднение переносим на 2114 → selector=AVERAGING"""
    text = "усреднение переносим на 2114"
    intent_marker = "усреднение переносим"
    mode_marker = "усреднение переносим"
    selector_marker = "усреднение"
    evidence = [
        _ev("MODIFY_ENTRY", "intent", intent_marker, 0),
        _ev("UPDATE_PRICE", "modify_entry_mode", mode_marker, 0),
        _ev("AVERAGING", "entry_selector", selector_marker, 0),
    ]
    e = _extract_first(text, evidence)
    assert e.entry_selector is not None
    assert e.entry_selector.role == "AVERAGING"
    assert e.entry_selector.sequence is None
    assert e.entries[0].price.value == 2114.0


def test_no_selector_when_absent():
    """новый вход 2114 (senza selector evidence) → entry_selector=None"""
    text = "новый вход 2114"
    marker = "новый вход"
    evidence = [
        _ev("MODIFY_ENTRY", "intent", marker, 0),
        _ev("UPDATE_PRICE", "modify_entry_mode", marker, 0),
    ]
    e = _extract_first(text, evidence)
    assert e.entry_selector is None


def test_add_entry_not_modify_entry():
    """добавляю вход 2114 → intent ADD_ENTRY, non MODIFY_ENTRY"""
    text = "добавляю вход 2114"
    marker = "добавляю вход"
    evidence = [
        _ev("ADD_ENTRY", "intent", marker, 0),
    ]
    intents = _extractor.extract(_normalized(text), evidence)
    assert len(intents) == 1
    assert intents[0].type == "ADD_ENTRY"


def test_reenter_not_modify_entry():
    """перезаходим 2114 → intent REENTER, non MODIFY_ENTRY"""
    text = "перезаходим 2114"
    marker = "перезаходим"
    evidence = [
        _ev("REENTER", "intent", marker, 0),
    ]
    intents = _extractor.extract(_normalized(text), evidence)
    assert len(intents) == 1
    assert intents[0].type == "REENTER"


def test_context_window_stops_at_next_intent():
    """MODIFY_ENTRY seguito da TP_HIT: i prezzi del TP non finiscono nelle entries"""
    text = "новый вход 2114 тп 2200"
    modify_marker = "новый вход"
    tp_marker = "тп"
    tp_start = text.index("тп")
    evidence = [
        _ev("MODIFY_ENTRY", "intent", modify_marker, 0),
        _ev("UPDATE_PRICE", "modify_entry_mode", modify_marker, 0),
        _ev("TP_HIT", "intent", tp_marker, tp_start),
    ]
    intents = _extractor.extract(_normalized(text), evidence)
    modify_intents = [i for i in intents if i.type == "MODIFY_ENTRY"]
    assert len(modify_intents) == 1, f"Expected 1 MODIFY_ENTRY intent, got {modify_intents}"
    e = modify_intents[0].entities
    assert isinstance(e, ModifyEntryEntities), f"Expected ModifyEntryEntities, got {type(e)}"
    assert len(e.entries) == 1
    assert e.entries[0].price.value == 2114.0


def test_unknown_mode_with_no_mode_evidence_but_price():
    """marker intent senza mode evidence + prezzo → mode=UPDATE_PRICE inferito"""
    text = "точка входа 2114"
    marker = "точка входа"
    evidence = [
        _ev("MODIFY_ENTRY", "intent", marker, 0, strength="weak"),
    ]
    e = _extract_first(text, evidence)
    assert e.mode == "UPDATE_PRICE"
    assert e.entries[0].price.value == 2114.0


def test_unknown_mode_with_no_price_stays_unknown():
    """marker intent senza mode evidence e senza prezzo → mode=UNKNOWN"""
    text = "точка входа"
    marker = "точка входа"
    evidence = [
        _ev("MODIFY_ENTRY", "intent", marker, 0, strength="weak"),
    ]
    e = _extract_first(text, evidence)
    assert e.mode == "UNKNOWN"
    assert e.entries == []
