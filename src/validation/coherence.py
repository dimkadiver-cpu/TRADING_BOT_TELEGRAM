"""Layer 3 — Validazione coerenza.

Controlla l'output del parser (TraderParseResult) per coerenza strutturale e semantica
prima che venga passato ai layer downstream (operation rules, target resolver).

Controllo strutturale:  le entità richieste da ogni intent devono essere presenti.
Controllo semantico:    i messaggi UPDATE devono avere almeno un ACTION intent.

Produce un ValidationResult con status e lista di errori/warning.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from src.parser.trader_profiles.base import TraderParseResult


# ---------------------------------------------------------------------------
# Intent classification
# ---------------------------------------------------------------------------

#: Intenti che richiedono un'azione operativa downstream.
ACTION_INTENTS: frozenset[str] = frozenset(
    {
        "U_MOVE_STOP",
        "U_MOVE_STOP_TO_BE",
        "U_CLOSE_FULL",
        "U_CLOSE_PARTIAL",
        "U_CANCEL_PENDING",
        "U_CANCEL_PENDING_ORDERS",
        "U_REENTER",
        "U_ADD_ENTRY",
        "U_MODIFY_ENTRY",
        "U_UPDATE_TAKE_PROFITS",
        "U_INVALIDATE_SETUP",
        "NS_CREATE_SIGNAL",
    }
)

#: Intenti puramente informativi — non richiedono azione.
CONTEXT_INTENTS: frozenset[str] = frozenset(
    {
        "U_TP_HIT",
        "U_TP_HIT_EXPLICIT",
        "U_SL_HIT",
        "U_STOP_HIT",
        "U_REPORT_FINAL_RESULT",
    }
)

# Intent → chiave entità obbligatoria nel dict entities.
# Solo gli intenti dove l'entità è strettamente necessaria per l'azione.
_INTENT_REQUIRED_ENTITIES: dict[str, str] = {
    "U_CLOSE_PARTIAL": "close_pct",
    "U_UPDATE_TAKE_PROFITS": "new_take_profits",
}


# ---------------------------------------------------------------------------
# Risultato di validazione
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ValidationResult:
    """Risultato del controllo di coerenza su un TraderParseResult.

    status:
        VALID            — risultato azionabile, tutte le entità richieste presenti.
        INFO_ONLY        — risultato informativo, nessuna azione necessaria o possibile.
        STRUCTURAL_ERROR — entità richieste per gli intenti dichiarati mancanti.

    errors:   violazioni strutturali (bloccanti per l'esecuzione).
    warnings: segnalazioni soft (non bloccanti).
    """

    status: Literal["VALID", "INFO_ONLY", "STRUCTURAL_ERROR"]
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def is_actionable(self) -> bool:
        """True solo per risultati VALID che devono essere passati downstream."""
        return self.status == "VALID"

    def to_dict(self) -> dict[str, Any]:
        return {
            "validation_status": self.status,
            "validation_errors": self.errors,
            "validation_warnings": self.warnings,
        }


# ---------------------------------------------------------------------------
# API pubblica
# ---------------------------------------------------------------------------


def validate(result: TraderParseResult) -> ValidationResult:
    """Esegui controlli di coerenza strutturale e semantica su un TraderParseResult.

    Args:
        result: Output del profilo parser.

    Returns:
        ValidationResult con status e lista di problemi.
    """
    message_type = result.message_type
    intents: list[str] = list(result.intents or [])
    entities: dict[str, Any] = result.entities if isinstance(result.entities, dict) else {}

    # Messaggi puramente informativi — nessuna validazione necessaria
    if message_type in {"INFO_ONLY", "UNCLASSIFIED"}:
        return ValidationResult(status="INFO_ONLY")

    # Setup incompleto — archiviato per revisione, non azionabile
    if message_type == "SETUP_INCOMPLETE":
        return ValidationResult(status="INFO_ONLY", warnings=["setup_incomplete"])

    if message_type == "NEW_SIGNAL":
        return _validate_new_signal(entities)

    if message_type == "UPDATE":
        return _validate_update(intents, entities)

    # Tipo sconosciuto / futuro
    return ValidationResult(
        status="INFO_ONLY",
        warnings=[f"unknown_message_type:{message_type}"],
    )


# ---------------------------------------------------------------------------
# Validatori per tipo
# ---------------------------------------------------------------------------


def _validate_new_signal(entities: dict[str, Any]) -> ValidationResult:
    """Verifica che un NEW_SIGNAL abbia i campi minimi richiesti."""
    errors: list[str] = []

    if not entities.get("symbol"):
        errors.append("missing_entity:symbol")

    # "side" è la chiave usata dai profili correnti; "direction" dalla nuova architettura
    if not entities.get("side") and not entities.get("direction"):
        errors.append("missing_entity:direction")

    if errors:
        return ValidationResult(status="STRUCTURAL_ERROR", errors=errors)
    return ValidationResult(status="VALID")


def _validate_update(intents: list[str], entities: dict[str, Any]) -> ValidationResult:
    """Verifica semantica (almeno un ACTION intent) e strutturale (entità per intent)."""
    action_intents = [i for i in intents if i in ACTION_INTENTS]

    # Controllo semantico: un UPDATE senza ACTION intent è solo informativo
    if not action_intents:
        return ValidationResult(
            status="INFO_ONLY",
            warnings=["update_no_action_intent"],
        )

    # Controllo strutturale: ogni ACTION intent con requisiti noti
    errors: list[str] = []
    for intent in action_intents:
        required_key = _INTENT_REQUIRED_ENTITIES.get(intent)
        if required_key is None:
            continue  # Nessun requisito strutturale definito per questo intent
        value = entities.get(required_key)
        if value is None or value == [] or value == "":
            errors.append(f"{intent}:missing_entity:{required_key}")

    if errors:
        return ValidationResult(status="STRUCTURAL_ERROR", errors=errors)
    return ValidationResult(status="VALID")
