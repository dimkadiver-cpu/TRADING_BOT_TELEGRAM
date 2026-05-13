from __future__ import annotations
import pytest
from src.parser_v2.contracts.context import ParserContext, RawContext, TargetHints
from src.parser_v2.contracts.markers import NormalizedText
from src.parser_v2.contracts.parsed_message import ParsedIntent, ParsedMessage
from src.parser_v2.contracts.entities import MoveStopToBEEntities, CancelPendingEntities
from src.parser_v2.translation.canonical_translator import CanonicalTranslator


def _raw_ctx() -> RawContext:
    return RawContext(raw_text="test")


def _make_parsed(
    intents: list[ParsedIntent],
    target_hints: TargetHints | None = None,
    parse_status: str = "PARSED",
    warnings: list[str] | None = None,
) -> ParsedMessage:
    return ParsedMessage(
        parser_profile="test",
        primary_class="UPDATE",
        parse_status=parse_status,
        confidence=0.9,
        intents=intents,
        target_hints=target_hints,
        warnings=warnings or [],
        raw_context=_raw_ctx(),
    )


def _make_intent(type_: str, occurrence_index: int = 0, target_hints: TargetHints | None = None) -> ParsedIntent:
    return ParsedIntent(
        type=type_,
        category="UPDATE",
        confidence=0.9,
        intent_id=f"{type_}#{occurrence_index}",
        occurrence_index=occurrence_index,
        target_hints=target_hints,
    )


def test_mixed_ops_on_global_target_produces_targeted_actions():
    intents = [
        _make_intent("MOVE_STOP_TO_BE", occurrence_index=0),
        _make_intent("CANCEL_PENDING", occurrence_index=0),
    ]
    global_hints = TargetHints(
        target_source="MESSAGE_TEXT_LINK",
        telegram_message_ids=[111, 222],
    )
    parsed = _make_parsed(intents, target_hints=global_hints)
    result = CanonicalTranslator().translate(parsed)

    assert result.parse_status == "PARSED"
    assert len(result.targeted_actions) == 2
    action_types = {a.action_type for a in result.targeted_actions}
    assert "SET_STOP" in action_types
    assert "CANCEL_PENDING" in action_types
    for action in result.targeted_actions:
        assert action.target_hints.telegram_message_ids == [111, 222]


def test_mixed_ops_no_partial_warning():
    intents = [
        _make_intent("MOVE_STOP_TO_BE", occurrence_index=0),
        _make_intent("CANCEL_PENDING", occurrence_index=0),
    ]
    parsed = _make_parsed(intents, target_hints=TargetHints(telegram_message_ids=[111]))
    result = CanonicalTranslator().translate(parsed)
    assert "multi_ref_mixed_intents_not_supported" not in result.warnings
    assert "ambiguous_target_intent_binding" not in result.warnings


def test_source_intent_id_propagated():
    intents = [_make_intent("MOVE_STOP_TO_BE", occurrence_index=1)]
    parsed = _make_parsed(intents, target_hints=TargetHints(telegram_message_ids=[111]))
    result = CanonicalTranslator().translate(parsed)
    assert result.targeted_actions[0].source_intent_id == "MOVE_STOP_TO_BE#1"


def test_reply_generates_targeted_actions():
    intents = [_make_intent("MOVE_STOP_TO_BE", occurrence_index=0)]
    hints = TargetHints(target_source="REPLY", reply_to_message_id=100)
    parsed = _make_parsed(intents, target_hints=hints)
    result = CanonicalTranslator().translate(parsed)
    assert len(result.targeted_actions) == 1
    assert result.targeted_actions[0].target_hints.reply_to_message_id == 100


def test_per_intent_target_hints_override_global():
    local_hints = TargetHints(target_source="LOCAL_TEXT_LINK", telegram_message_ids=[111])
    global_hints = TargetHints(target_source="MESSAGE_TEXT_LINK", telegram_message_ids=[111, 222])
    intents = [_make_intent("MOVE_STOP_TO_BE", occurrence_index=0, target_hints=local_hints)]
    parsed = _make_parsed(intents, target_hints=global_hints)
    result = CanonicalTranslator().translate(parsed)
    assert result.targeted_actions[0].target_hints.telegram_message_ids == [111]


def test_intents_deduplicated_in_canonical():
    intents = [
        _make_intent("MOVE_STOP_TO_BE", occurrence_index=0),
        _make_intent("MOVE_STOP_TO_BE", occurrence_index=1),
    ]
    parsed = _make_parsed(intents, target_hints=TargetHints(telegram_message_ids=[111, 222]))
    result = CanonicalTranslator().translate(parsed)
    assert result.intents.count("MOVE_STOP_TO_BE") == 1


def test_line_level_intents_each_get_own_target():
    intents = [
        _make_intent("MOVE_STOP_TO_BE", occurrence_index=0,
                     target_hints=TargetHints(target_source="LOCAL_TEXT_LINK", telegram_message_ids=[111])),
        _make_intent("CLOSE_FULL", occurrence_index=0,
                     target_hints=TargetHints(target_source="LOCAL_TEXT_LINK", telegram_message_ids=[222])),
    ]
    parsed = _make_parsed(intents, target_hints=None)
    result = CanonicalTranslator().translate(parsed)
    assert len(result.targeted_actions) == 2
    ids = {a.action_type: a.target_hints.telegram_message_ids for a in result.targeted_actions}
    assert ids["SET_STOP"] == [111]
    assert ids["CLOSE"] == [222]


def test_modify_entry_propagates_entry_selector_and_structure():
    from src.parser_v2.contracts.entities import EntryLeg, EntrySelector, ModifyEntryEntities, Price

    selector = EntrySelector(role="PRIMARY", sequence=1, raw="основной вход")
    entities = ModifyEntryEntities(
        mode="UPDATE_PRICE",
        entry_structure="ONE_SHOT",
        entry_selector=selector,
        entries=[EntryLeg(sequence=1, entry_type="LIMIT", price=Price(raw="2114", value=2114.0))],
    )
    intent = ParsedIntent(
        type="MODIFY_ENTRY",
        category="UPDATE",
        confidence=0.9,
        entities=entities,
        intent_id="MODIFY_ENTRY#0",
        occurrence_index=0,
    )
    parsed = _make_parsed([intent])
    result = CanonicalTranslator().translate(parsed)

    assert result.primary_class == "UPDATE"
    ops = result.update.operations
    assert len(ops) == 1
    me = ops[0].modify_entries
    assert me is not None
    assert me.kind == "UPDATE_PRICE"
    assert me.entry_structure == "ONE_SHOT"
    assert me.entry_selector is not None
    assert me.entry_selector.role == "PRIMARY"
    assert me.entry_selector.sequence == 1
    assert len(me.entries) == 1
    assert me.entries[0].price.value == 2114.0


def test_modify_entry_update_range_propagates():
    from src.parser_v2.contracts.entities import EntryLeg, ModifyEntryEntities, Price

    entities = ModifyEntryEntities(
        mode="UPDATE_RANGE",
        entry_structure="RANGE",
        entries=[
            EntryLeg(sequence=1, entry_type="LIMIT", price=Price(raw="2114", value=2114.0)),
            EntryLeg(sequence=2, entry_type="LIMIT", price=Price(raw="2120", value=2120.0)),
        ],
    )
    intent = ParsedIntent(
        type="MODIFY_ENTRY",
        category="UPDATE",
        confidence=0.9,
        entities=entities,
        intent_id="MODIFY_ENTRY#0",
        occurrence_index=0,
    )
    parsed = _make_parsed([intent])
    result = CanonicalTranslator().translate(parsed)

    me = result.update.operations[0].modify_entries
    assert me.kind == "UPDATE_RANGE"
    assert me.entry_structure == "RANGE"
    assert len(me.entries) == 2


def test_move_stop_no_price_defaults_to_be() -> None:
    """MOVE_STOP senza prezzo né tp_level → SET_STOP/ENTRY + warning."""
    from src.parser_v2.contracts.entities import MoveStopEntities

    intent = ParsedIntent(
        type="MOVE_STOP",
        category="UPDATE",
        confidence=0.8,
        entities=MoveStopEntities(),  # new_stop_price=None, stop_to_tp_level=None
        intent_id="MOVE_STOP#0",
        occurrence_index=0,
    )
    parsed = _make_parsed([intent])
    result = CanonicalTranslator().translate(parsed)

    assert result.parse_status == "PARSED"
    ops = result.update.operations
    assert len(ops) == 1
    assert ops[0].op_type == "SET_STOP"
    assert ops[0].set_stop is not None
    assert ops[0].set_stop.target_type == "ENTRY"
    assert "move_stop_no_price_defaulted_to_be" in result.warnings


def test_move_stop_with_price_no_warning() -> None:
    """MOVE_STOP con prezzo → comportamento invariato, nessun nuovo warning."""
    from src.parser_v2.contracts.entities import MoveStopEntities, Price

    intent = ParsedIntent(
        type="MOVE_STOP",
        category="UPDATE",
        confidence=0.9,
        entities=MoveStopEntities(new_stop_price=Price(raw="89000", value=89000.0)),
        intent_id="MOVE_STOP#0",
        occurrence_index=0,
    )
    parsed = _make_parsed([intent])
    result = CanonicalTranslator().translate(parsed)

    assert result.parse_status == "PARSED"
    ops = result.update.operations
    assert len(ops) == 1
    assert ops[0].set_stop.target_type == "PRICE"
    assert ops[0].set_stop.price.value == 89000.0
    assert "move_stop_no_price_defaulted_to_be" not in result.warnings


def test_move_stop_to_be_intent_no_new_warning() -> None:
    """MOVE_STOP_TO_BE (intent distinto) non acquisisce il nuovo warning."""
    intent = _make_intent("MOVE_STOP_TO_BE")
    parsed = _make_parsed([intent])
    result = CanonicalTranslator().translate(parsed)

    assert result.parse_status == "PARSED"
    assert "move_stop_no_price_defaulted_to_be" not in result.warnings


def test_no_target_hints_uses_plain_operations_not_targeted() -> None:
    """Senza target hints, le operazioni vanno in update.operations, non targeted_actions."""
    intents = [_make_intent("MOVE_STOP_TO_BE")]
    parsed = _make_parsed(intents, target_hints=None)
    result = CanonicalTranslator().translate(parsed)

    assert len(result.targeted_actions) == 0
    assert result.update is not None
    assert len(result.update.operations) == 1
    assert result.update.operations[0].op_type == "SET_STOP"


def test_message_target_hints_forces_all_to_targeted_operations_empty() -> None:
    """Con target hints a livello messaggio, tutto in targeted_actions e update.operations vuoto."""
    intents = [_make_intent("MOVE_STOP_TO_BE"), _make_intent("CANCEL_PENDING")]
    parsed = _make_parsed(intents, target_hints=TargetHints(telegram_message_ids=[99]))
    result = CanonicalTranslator().translate(parsed)

    assert len(result.targeted_actions) == 2
    assert result.update is not None
    assert len(result.update.operations) == 0


def test_per_intent_local_target_forces_all_to_targeted_no_mix() -> None:
    """Se almeno un intent ha target locale, tutti vanno in targeted_actions — anche quelli senza."""
    local_hints = TargetHints(target_source="LOCAL_TEXT_LINK", telegram_message_ids=[11])
    intents = [
        _make_intent("MOVE_STOP_TO_BE", target_hints=local_hints),
        _make_intent("CANCEL_PENDING"),  # nessun target locale
    ]
    parsed = _make_parsed(intents, target_hints=None)
    result = CanonicalTranslator().translate(parsed)

    assert len(result.targeted_actions) == 2
    assert result.update is not None
    assert len(result.update.operations) == 0
    action_types = {a.action_type for a in result.targeted_actions}
    assert "SET_STOP" in action_types
    assert "CANCEL_PENDING" in action_types
