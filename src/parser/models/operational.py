# LEGACY - non usare in nuovo codice. Usa src.parser.canonical_v1.models.
"""Pydantic/dataclass models for Fase 4 - Operation Rules + Target Resolver.

Three models are defined here:

    OperationalSignal - CanonicalMessage + parametri esecutivi calcolati da
                        Layer 4 (Operation Rules Engine).

    ResolvedTarget    - risultato della risoluzione target_ref in position IDs
                        concreti, prodotto da Layer 5 (Target Resolver).

    ResolvedSignal    - output finale di Fase 4, pronto per Sistema 1.
                        Composizione: OperationalSignal + ResolvedTarget.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.parser.canonical_v1.models import CanonicalMessage
from src.parser.canonical_v1.normalizer import normalize as normalize_legacy_parse_result
from src.parser.models.canonical import (
    Intent as LegacyIntent,
    TargetRef as LegacyTargetRef,
    TraderParseResult as LegacyPydanticParseResult,
)
from src.parser.trader_profiles.base import (
    ParserContext,
    TraderParseResult as LegacyDataclassParseResult,
)


def _coerce_to_canonical_message(value: Any, *, trader_id: str) -> CanonicalMessage:
    if isinstance(value, CanonicalMessage):
        return value

    if isinstance(value, LegacyDataclassParseResult):
        return normalize_legacy_parse_result(value, _build_parser_context(value, trader_id=trader_id))

    if isinstance(value, LegacyPydanticParseResult):
        legacy = LegacyDataclassParseResult(
            message_type=value.message_type,
            intents=[
                item.name if isinstance(item, LegacyIntent) else str(item)
                for item in (value.intents or [])
            ],
            entities=_coerce_legacy_entities(value.entities),
            target_refs=_coerce_legacy_target_refs(value.target_ref),
            warnings=list(value.warnings or []),
            confidence=float(value.confidence or 0.0),
        )
        return normalize_legacy_parse_result(legacy, _build_parser_context(value, trader_id=trader_id))

    raise TypeError(
        "OperationalSignal requires canonical_message: CanonicalMessage "
        "or a legacy parse_result convertible to CanonicalMessage"
    )


def _build_parser_context(value: Any, *, trader_id: str) -> ParserContext:
    return ParserContext(
        trader_code=str(getattr(value, "trader_id", None) or trader_id or "unknown"),
        message_id=None,
        reply_to_message_id=None,
        channel_id=None,
        raw_text=str(getattr(value, "raw_text", "") or ""),
        extracted_links=[],
        hashtags=[],
    )


def _coerce_legacy_entities(entities: Any) -> dict[str, Any]:
    if entities is None:
        return {}
    if isinstance(entities, dict):
        return dict(entities)
    if hasattr(entities, "model_dump"):
        return dict(entities.model_dump(mode="python"))
    return {}


def _coerce_legacy_target_refs(target_ref: LegacyTargetRef | None) -> list[dict[str, Any]]:
    if target_ref is None:
        return []

    if target_ref.kind == "STRONG":
        kind_map = {
            "REPLY": "reply",
            "TELEGRAM_LINK": "telegram_link",
            "EXPLICIT_ID": "message_id",
        }
        return [{"kind": kind_map.get(str(target_ref.method or "REPLY"), "reply"), "ref": target_ref.ref}]

    if target_ref.kind == "SYMBOL":
        return [{"kind": "SYMBOL", "symbol": target_ref.symbol}]

    if target_ref.kind == "GLOBAL":
        return [{"kind": "GLOBAL", "scope": target_ref.scope}]

    return []


# ---------------------------------------------------------------------------
# OperationalSignal
# ---------------------------------------------------------------------------


class OperationalSignal(BaseModel):
    """CanonicalMessage + parametri esecutivi calcolati da Layer 4.

    Composizione: contiene canonical_message, non lo copia nei campi flat.

    Durante la migrazione accetta ancora `parse_result` legacy in input e lo
    normalizza a CanonicalMessage, mantenendolo anche come bridge read-only per
    i consumer non ancora migrati.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, populate_by_name=True)

    canonical_message: CanonicalMessage
    legacy_parse_result: Any | None = Field(default=None, exclude=True, repr=False)

    # trader context - set by the engine from the caller's trader_id
    trader_id: str = ""

    # Set A - parametri apertura (solo SIGNAL)
    risk_mode: str | None = None
    """risk_pct_of_capital | risk_usdt_fixed"""
    risk_pct_of_capital: float | None = None
    """% capitale configurato per trade (input da config)."""
    risk_usdt_fixed: float | None = None
    """USDT fissi configurati se risk_mode=risk_usdt_fixed."""
    capital_base_usdt: float | None = None
    """Capitale di riferimento usato per il calcolo."""
    risk_budget_usdt: float | None = None
    """Perdita massima calcolata per questo segnale (USDT)."""
    sl_distance_pct: float | None = None
    """Distanza percentuale entry -> stop loss (0.05 = 5%)."""
    position_size_usdt: float | None = None
    """Size della posizione calcolata (USDT). Derivato da risk_budget / sl_distance."""
    position_size_pct: float | None = None
    """Size come % del capitale. Dato derivato - non e input di config."""
    entry_split: dict[str, float] | None = None
    """Pesi di split per entries, es. {"E1": 0.3, "E2": 0.7}."""
    leverage: int | None = None
    risk_hint_used: bool = False
    sizing_deferred: bool = False
    """True per SIGNAL MARKET puro senza prezzo entry disponibile."""

    # Set B - snapshot regole gestione posizione {tp, sl, updates, pending}
    management_rules: dict[str, Any] | None = None

    # gate
    is_blocked: bool = False
    block_reason: str | None = None

    # audit
    applied_rules: list[str] = []
    warnings: list[str] = []

    @model_validator(mode="before")
    @classmethod
    def _migrate_parse_result_input(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        if "canonical_message" in data:
            return data

        legacy = data.get("parse_result")
        if legacy is None:
            return data

        migrated = dict(data)
        migrated["canonical_message"] = _coerce_to_canonical_message(
            legacy,
            trader_id=str(data.get("trader_id") or ""),
        )
        migrated["legacy_parse_result"] = legacy
        return migrated

    @property
    def parse_result(self) -> Any:
        """Compatibilita temporanea per i caller non ancora migrati."""
        return self.legacy_parse_result if self.legacy_parse_result is not None else self.canonical_message


# ---------------------------------------------------------------------------
# ResolvedTarget
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ResolvedTarget:
    """Risultato della risoluzione target_ref in position IDs concreti.

    Prodotto da Layer 5 (Target Resolver).

    position_ids: lista di op_signal_id dei segnali originali risolti.
    eligibility: ELIGIBLE se il target e compatibile con l'intent;
                 INELIGIBLE se incompatibile; WARN se in stato ambiguo;
                 UNRESOLVED se il target non e stato trovato.
    reason: motivo testuale se eligibility != ELIGIBLE e != UNRESOLVED.
    """

    kind: Literal["STRONG", "SYMBOL", "GLOBAL"]
    position_ids: list[int]
    eligibility: Literal["ELIGIBLE", "INELIGIBLE", "WARN", "UNRESOLVED"]
    reason: str | None


# ---------------------------------------------------------------------------
# ResolvedSignal
# ---------------------------------------------------------------------------


class ResolvedSignal(BaseModel):
    """Output finale di Fase 4 - pronto per Sistema 1.

    Composizione: contiene operational (OperationalSignal) e resolved_target
    (ResolvedTarget | None). Non appiattisce i campi.

    is_ready e True quando:
      - operational.is_blocked is False
      - resolved_target e None (SIGNAL senza target_ref) oppure
        resolved_target.eligibility in {"ELIGIBLE", "WARN"}
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    operational: OperationalSignal
    resolved_target: ResolvedTarget | None = None
    """None per SIGNAL senza target_ref."""
    is_ready: bool
