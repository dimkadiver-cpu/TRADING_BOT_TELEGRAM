"""Pydantic/dataclass models for Fase 4 — Operation Rules + Target Resolver.

Three models are defined here:

    OperationalSignal — TraderParseResult + parametri esecutivi calcolati da
                        Layer 4 (Operation Rules Engine).

    ResolvedTarget    — risultato della risoluzione target_ref in position IDs
                        concreti, prodotto da Layer 5 (Target Resolver).

    ResolvedSignal    — output finale di Fase 4, pronto per Sistema 1.
                        Composizione: OperationalSignal + ResolvedTarget.

Usage:
    from src.parser.models.operational import (
        OperationalSignal,
        ResolvedTarget,
        ResolvedSignal,
    )
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


# ---------------------------------------------------------------------------
# OperationalSignal
# ---------------------------------------------------------------------------

class OperationalSignal(BaseModel):
    """TraderParseResult + parametri esecutivi calcolati da Layer 4.

    Composizione: contiene parse_result, non lo copia nei campi flat.

    Campi Set A (apertura posizione) sono popolati solo per NEW_SIGNAL.
    Campi Set B (management_rules) sono popolati per NEW_SIGNAL e UPDATE.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    # composizione — non copia (accetta base.TraderParseResult e canonical.TraderParseResult)
    parse_result: Any

    # trader context — set by the engine from the caller's trader_id
    trader_id: str = ""

    # Set A — parametri apertura (solo NEW_SIGNAL)
    # Modello risk-first: l'input è il rischio massimo accettato;
    # position_size_usdt e position_size_pct sono calcolati dal sistema.
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
    """Distanza percentuale entry → stop loss (0.05 = 5%)."""
    position_size_usdt: float | None = None
    """Size della posizione calcolata (USDT). Derivato da risk_budget / sl_distance."""
    position_size_pct: float | None = None
    """Size come % del capitale. Dato derivato — non è input di config."""
    entry_split: dict[str, float] | None = None
    """Pesi di split per entries, es. {"E1": 0.3, "E2": 0.7}."""
    leverage: int | None = None
    risk_hint_used: bool = False

    # Set B — snapshot regole gestione posizione
    management_rules: dict[str, Any] | None = None

    # gate
    is_blocked: bool = False
    block_reason: str | None = None

    # audit
    applied_rules: list[str] = []
    warnings: list[str] = []


# ---------------------------------------------------------------------------
# ResolvedTarget
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ResolvedTarget:
    """Risultato della risoluzione target_ref in position IDs concreti.

    Prodotto da Layer 5 (Target Resolver).

    position_ids: lista di op_signal_id dei segnali originali risolti.
    eligibility: ELIGIBLE se il target è compatibile con l'intent;
                 INELIGIBLE se incompatibile; WARN se in stato ambiguo;
                 UNRESOLVED se il target non è stato trovato.
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
    """Output finale di Fase 4 — pronto per Sistema 1.

    Composizione: contiene operational (OperationalSignal) e resolved_target
    (ResolvedTarget | None). Non appiattisce i campi.

    is_ready è True quando:
      - operational.is_blocked is False
      - resolved_target è None (NEW_SIGNAL senza target_ref) oppure
        resolved_target.eligibility in {"ELIGIBLE", "WARN"}
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    operational: OperationalSignal
    resolved_target: ResolvedTarget | None = None
    """None per NEW_SIGNAL senza target_ref."""
    is_ready: bool
