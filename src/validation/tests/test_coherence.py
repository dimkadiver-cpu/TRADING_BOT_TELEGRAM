"""Tests per la validazione di coerenza (Layer 3)."""

from __future__ import annotations

from src.parser.trader_profiles.base import TraderParseResult
from src.validation.coherence import ValidationResult, validate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _result(
    message_type: str,
    intents: list[str] | None = None,
    entities: dict | None = None,
) -> TraderParseResult:
    return TraderParseResult(
        message_type=message_type,
        intents=intents or [],
        entities=entities or {},
    )


# ---------------------------------------------------------------------------
# INFO_ONLY e UNCLASSIFIED — sempre INFO_ONLY
# ---------------------------------------------------------------------------


def test_info_only_is_info_only() -> None:
    r = validate(_result("INFO_ONLY"))
    assert r.status == "INFO_ONLY"
    assert not r.errors
    assert not r.is_actionable


def test_unclassified_is_info_only() -> None:
    r = validate(_result("UNCLASSIFIED"))
    assert r.status == "INFO_ONLY"
    assert not r.is_actionable


def test_setup_incomplete_is_info_only() -> None:
    r = validate(_result("SETUP_INCOMPLETE"))
    assert r.status == "INFO_ONLY"
    assert "setup_incomplete" in r.warnings
    assert not r.is_actionable


# ---------------------------------------------------------------------------
# NEW_SIGNAL — strutturale
# ---------------------------------------------------------------------------


def test_new_signal_valid_with_complete_limit_setup() -> None:
    r = validate(
        _result(
            "NEW_SIGNAL",
            entities={
                "symbol": "BTCUSDT",
                "side": "LONG",
                "entry": [100.0],
                "stop_loss": 90.0,
                "take_profits": [110.0],
            },
        )
    )
    assert r.status == "VALID"
    assert r.is_actionable


def test_new_signal_valid_with_market_entry_without_prices() -> None:
    """MARKET richiede il tipo di entry, ma non i prezzi di entry."""
    r = validate(
        _result(
            "NEW_SIGNAL",
            entities={
                "symbol": "ETHUSDT",
                "direction": "SHORT",
                "entry_order_type": "MARKET",
                "stop_loss": 2600.0,
                "take_profits": [2400.0, 2300.0],
            },
        )
    )
    assert r.status == "VALID"


def test_new_signal_missing_symbol() -> None:
    r = validate(
        _result(
            "NEW_SIGNAL",
            entities={
                "side": "LONG",
                "entry": [100.0],
                "stop_loss": 90.0,
                "take_profits": [110.0],
            },
        )
    )
    assert r.status == "STRUCTURAL_ERROR"
    assert "missing_entity:symbol" in r.errors
    assert not r.is_actionable


def test_new_signal_missing_direction() -> None:
    r = validate(
        _result(
            "NEW_SIGNAL",
            entities={
                "symbol": "BTCUSDT",
                "entry": [100.0],
                "stop_loss": 90.0,
                "take_profits": [110.0],
            },
        )
    )
    assert r.status == "STRUCTURAL_ERROR"
    assert "missing_entity:direction" in r.errors


def test_new_signal_missing_entry() -> None:
    r = validate(
        _result(
            "NEW_SIGNAL",
            entities={
                "symbol": "BTCUSDT",
                "side": "LONG",
                "stop_loss": 90.0,
                "take_profits": [110.0],
            },
        )
    )
    assert r.status == "STRUCTURAL_ERROR"
    assert "missing_entity:entry" in r.errors


def test_new_signal_missing_stop_loss() -> None:
    r = validate(
        _result(
            "NEW_SIGNAL",
            entities={
                "symbol": "BTCUSDT",
                "side": "LONG",
                "entry": [100.0],
                "take_profits": [110.0],
            },
        )
    )
    assert r.status == "STRUCTURAL_ERROR"
    assert "missing_entity:stop_loss" in r.errors


def test_new_signal_missing_take_profits() -> None:
    r = validate(
        _result(
            "NEW_SIGNAL",
            entities={
                "symbol": "BTCUSDT",
                "side": "LONG",
                "entry": [100.0],
                "stop_loss": 90.0,
            },
        )
    )
    assert r.status == "STRUCTURAL_ERROR"
    assert "missing_entity:take_profits" in r.errors


# ---------------------------------------------------------------------------
# UPDATE — controllo semantico: almeno un ACTION intent
# ---------------------------------------------------------------------------


def test_update_with_action_intent_valid() -> None:
    r = validate(_result("UPDATE", intents=["U_MOVE_STOP"]))
    assert r.status == "VALID"
    assert r.is_actionable


def test_update_close_full_valid() -> None:
    r = validate(_result("UPDATE", intents=["U_CLOSE_FULL"]))
    assert r.status == "VALID"


def test_update_cancel_pending_valid() -> None:
    r = validate(_result("UPDATE", intents=["U_CANCEL_PENDING_ORDERS"]))
    assert r.status == "VALID"


def test_update_move_stop_to_be_valid() -> None:
    r = validate(_result("UPDATE", intents=["U_MOVE_STOP_TO_BE"]))
    assert r.status == "VALID"


def test_update_only_context_intents_is_info_only() -> None:
    """UPDATE con solo intent informativi (U_TP_HIT, U_SL_HIT) = INFO_ONLY."""
    r = validate(_result("UPDATE", intents=["U_TP_HIT", "U_STOP_HIT"]))
    assert r.status == "INFO_ONLY"
    assert "update_no_action_intent" in r.warnings
    assert not r.is_actionable


def test_update_no_intents_is_info_only() -> None:
    """UPDATE senza intenti = INFO_ONLY."""
    r = validate(_result("UPDATE", intents=[]))
    assert r.status == "INFO_ONLY"
    assert "update_no_action_intent" in r.warnings


def test_update_mixed_context_and_action_valid() -> None:
    """U_TP_HIT (context) + U_MOVE_STOP (action) = VALID."""
    r = validate(_result("UPDATE", intents=["U_TP_HIT", "U_MOVE_STOP"]))
    assert r.status == "VALID"
    assert r.is_actionable


# ---------------------------------------------------------------------------
# UPDATE — controllo strutturale: entità per intent
# ---------------------------------------------------------------------------


def test_update_close_partial_missing_close_pct() -> None:
    """U_CLOSE_PARTIAL senza close_pct = STRUCTURAL_ERROR."""
    r = validate(_result("UPDATE", intents=["U_CLOSE_PARTIAL"], entities={}))
    assert r.status == "STRUCTURAL_ERROR"
    assert "U_CLOSE_PARTIAL:missing_entity:close_pct" in r.errors


def test_update_close_partial_with_close_pct_valid() -> None:
    r = validate(
        _result("UPDATE", intents=["U_CLOSE_PARTIAL"], entities={"close_pct": 50.0})
    )
    assert r.status == "VALID"


def test_update_update_take_profits_missing_new_take_profits() -> None:
    """U_UPDATE_TAKE_PROFITS senza new_take_profits = STRUCTURAL_ERROR."""
    r = validate(_result("UPDATE", intents=["U_UPDATE_TAKE_PROFITS"], entities={}))
    assert r.status == "STRUCTURAL_ERROR"
    assert "U_UPDATE_TAKE_PROFITS:missing_entity:new_take_profits" in r.errors


def test_update_update_take_profits_empty_list() -> None:
    """Lista vuota è equivalente a mancante."""
    r = validate(
        _result(
            "UPDATE",
            intents=["U_UPDATE_TAKE_PROFITS"],
            entities={"new_take_profits": []},
        )
    )
    assert r.status == "STRUCTURAL_ERROR"


def test_update_update_take_profits_with_values_valid() -> None:
    r = validate(
        _result(
            "UPDATE",
            intents=["U_UPDATE_TAKE_PROFITS"],
            entities={"new_take_profits": [50000.0, 55000.0]},
        )
    )
    assert r.status == "VALID"


def test_update_intents_without_entity_requirements_valid() -> None:
    """U_MOVE_STOP, U_CLOSE_FULL, U_CANCEL_PENDING_ORDERS non richiedono entità specifiche."""
    for intent in ("U_MOVE_STOP", "U_CLOSE_FULL", "U_CANCEL_PENDING_ORDERS", "U_INVALIDATE_SETUP"):
        r = validate(_result("UPDATE", intents=[intent], entities={}))
        assert r.status == "VALID", f"Expected VALID for intent {intent!r}, got {r.status}"


# ---------------------------------------------------------------------------
# ValidationResult helper
# ---------------------------------------------------------------------------


def test_to_dict_valid() -> None:
    r = ValidationResult(status="VALID")
    d = r.to_dict()
    assert d["validation_status"] == "VALID"
    assert d["validation_errors"] == []
    assert d["validation_warnings"] == []


def test_to_dict_with_errors() -> None:
    r = ValidationResult(
        status="STRUCTURAL_ERROR",
        errors=["missing_entity:symbol"],
        warnings=["low_confidence"],
    )
    d = r.to_dict()
    assert d["validation_status"] == "STRUCTURAL_ERROR"
    assert "missing_entity:symbol" in d["validation_errors"]


def test_is_actionable_only_for_valid() -> None:
    assert ValidationResult(status="VALID").is_actionable is True
    assert ValidationResult(status="INFO_ONLY").is_actionable is False
    assert ValidationResult(status="STRUCTURAL_ERROR").is_actionable is False


# ---------------------------------------------------------------------------
# Tipo sconosciuto
# ---------------------------------------------------------------------------


def test_unknown_message_type_is_info_only() -> None:
    r = validate(_result("FUTURE_TYPE"))
    assert r.status == "INFO_ONLY"
    assert any("unknown_message_type" in w for w in r.warnings)
