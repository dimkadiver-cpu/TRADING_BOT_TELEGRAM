from src.runtime_v2.control_plane.formatters.templates.clean_log import _build_signal_notes


def test_no_reshape_no_notes():
    notes = _build_signal_notes({})
    assert not any("Reshape" in n for n in notes)


def test_reshaped_pass_note():
    p = {"reshaped": {"rule_id": "ladder_4_aggressive"}}
    notes = _build_signal_notes(p)
    assert any("ladder_4_aggressive" in n for n in notes)
    assert any("Reshaped" in n for n in notes)


def test_reshape_rejected_no_match_note():
    p = {"reshape_rejected": {"rule_id": "ladder_4_aggressive", "phase": "no_match"}}
    notes = _build_signal_notes(p)
    assert any("ladder_4_aggressive" in n for n in notes)
    assert any("did not match" in n for n in notes)


def test_reshape_rejected_invalid_output_note():
    p = {"reshape_rejected": {"rule_id": "ladder_4_aggressive", "phase": "invalid_output"}}
    notes = _build_signal_notes(p)
    assert any("ladder_4_aggressive" in n for n in notes)
    assert any("failed" in n.lower() for n in notes)


def test_existing_notes_unaffected():
    p = {
        "range_derivation": {
            "derived_from_range": True,
            "split_mode": "endpoints",
            "original_min_price": 90.0,
            "original_max_price": 100.0,
        },
        "reshaped": {"rule_id": "ladder_4_aggressive"},
    }
    notes = _build_signal_notes(p)
    assert any("Entry" in n for n in notes)      # range derivation note
    assert any("Reshaped" in n for n in notes)   # reshape note
