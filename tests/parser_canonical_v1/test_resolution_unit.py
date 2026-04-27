from __future__ import annotations

from src.parser.shared.resolution_unit import (
    TargetedItem,
    decide_resolution_unit,
    extract_targeted_items,
)


def _target(ref: int, history: list[str] | None = None) -> dict[str, object]:
    payload: dict[str, object] = {"kind": "message_id", "ref": ref}
    if history is not None:
        payload["target_history"] = history
    return payload


def test_case_a_multiple_refs_same_action_uses_message_wide() -> None:
    text = (
        "XRP - https://t.me/c/3171748254/1015\n"
        "ADA - https://t.me/c/3171748254/1017\n\n"
        "пора перенести стоп в бу"
    )

    target_refs = [_target(1015, ["MOVE_STOP_TO_BE"]), _target(1017)]

    assert decide_resolution_unit(text, target_refs) == "MESSAGE_WIDE"

    items = extract_targeted_items(text, target_refs)
    assert all(isinstance(item, TargetedItem) for item in items)
    assert [item.target_ref["ref"] for item in items] == [1015, 1017]
    assert [item.target_history for item in items] == [["MOVE_STOP_TO_BE"], []]
    assert all("пора перенести стоп в бу" in item.text for item in items)


def test_case_b_mixed_common_and_per_item_fragments_uses_target_item_wide() -> None:
    text = (
        "XRP - https://t.me/c/3171748254/1015 stop in be\n"
        "ADA - https://t.me/c/3171748254/1017 stop in be\n"
        "XRP - https://t.me/c/3171748254/1015 3.2%\n"
        "ADA - https://t.me/c/3171748254/1017 -1.4%"
    )

    target_refs = [_target(1015), _target(1017)]

    assert decide_resolution_unit(text, target_refs) == "TARGET_ITEM_WIDE"

    items = extract_targeted_items(text, target_refs)
    assert len(items) == 4
    assert [item.target_ref["ref"] for item in items] == [1015, 1017, 1015, 1017]
    assert any("stop in be" in item.text for item in items)
    assert any("%" in item.text for item in items)


def test_case_c_heterogeneous_rows_use_target_item_wide() -> None:
    text = (
        "LINK - https://t.me/c/3171748254/1015 стоп в бу\n"
        "ALGO - https://t.me/c/3171748254/1017 стоп на 1 тейк"
    )

    target_refs = [_target(1015), _target(1017)]

    assert decide_resolution_unit(text, target_refs) == "TARGET_ITEM_WIDE"

    items = extract_targeted_items(text, target_refs)
    assert [item.target_ref["ref"] for item in items] == [1015, 1017]
    assert [item.text for item in items] == [
        "LINK - https://t.me/c/3171748254/1015 стоп в бу",
        "ALGO - https://t.me/c/3171748254/1017 стоп на 1 тейк",
    ]
