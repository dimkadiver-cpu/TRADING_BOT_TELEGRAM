"""Target Resolver — Layer 5.

Risolve target_ref (STRONG / SYMBOL / GLOBAL) in op_signal_id concreti e
calcola l'eligibilità intent-aware.

Entry point:
    resolved = await resolve(operational_signal, db_path)
    # None se il segnale non ha target_ref (NEW_SIGNAL senza riferimento)

Logica per kind/method:
    STRONG / REPLY        → signals WHERE root_telegram_id = ref AND trader_id = ?
    STRONG / EXPLICIT_ID  → signals WHERE trader_signal_id = ref AND trader_id = ?
    STRONG / TELEGRAM_LINK → UNRESOLVED (non implementato — richiede schema futuro)
    SYMBOL                → signals WHERE trader_id=? AND symbol=? AND status!='CLOSED'
    GLOBAL / all_long     → signals WHERE trader_id=? AND side='BUY' AND status!='CLOSED'
    GLOBAL / all_short    → signals WHERE trader_id=? AND side='SELL' AND status!='CLOSED'
    GLOBAL / all_positions → signals WHERE trader_id=? AND status!='CLOSED'

Eligibilità intent-aware (tabella PRD):
    Intent            PENDING   ACTIVE    CLOSED
    U_CANCEL_PENDING  ELIGIBLE  ELIGIBLE  INELIGIBLE
    U_CLOSE_FULL      WARN      ELIGIBLE  INELIGIBLE
    U_CLOSE_PARTIAL   WARN      ELIGIBLE  INELIGIBLE
    U_MOVE_STOP       WARN      ELIGIBLE  INELIGIBLE
    U_REENTER         ELIGIBLE  ELIGIBLE  INELIGIBLE
    U_TP_HIT          ELIGIBLE  ELIGIBLE  ELIGIBLE   (INFO_ONLY)
    U_SL_HIT          ELIGIBLE  ELIGIBLE  ELIGIBLE   (INFO_ONLY)

La peggiore eligibilità tra tutti i target risolti e tutti gli intent ACTION
determina il risultato finale: INELIGIBLE > WARN > ELIGIBLE.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import aiosqlite

from src.parser.models.canonical import Intent
from src.parser.models.operational import OperationalSignal
from src.storage.signals_query import (
    SignalRow,
    get_by_root_telegram_id,
    get_by_trader_signal_id,
    get_open_by_symbol,
    get_open_by_trader,
)
from src.target_resolver.models import ResolvedTarget


# ---------------------------------------------------------------------------
# Eligibility matrix
# ---------------------------------------------------------------------------

# Per ogni intent ACTION: status → eligibility
# INFO_ONLY intents (U_TP_HIT, U_SL_HIT) are always ELIGIBLE for any status.
_MATRIX: dict[str, dict[str, Literal["ELIGIBLE", "WARN", "INELIGIBLE"]]] = {
    "U_CANCEL_PENDING": {"PENDING": "ELIGIBLE", "ACTIVE": "ELIGIBLE",  "CLOSED": "INELIGIBLE"},
    "U_CLOSE_FULL":     {"PENDING": "WARN",      "ACTIVE": "ELIGIBLE",  "CLOSED": "INELIGIBLE"},
    "U_CLOSE_PARTIAL":  {"PENDING": "WARN",       "ACTIVE": "ELIGIBLE",  "CLOSED": "INELIGIBLE"},
    "U_MOVE_STOP":      {"PENDING": "WARN",       "ACTIVE": "ELIGIBLE",  "CLOSED": "INELIGIBLE"},
    "U_REENTER":        {"PENDING": "ELIGIBLE",   "ACTIVE": "ELIGIBLE",  "CLOSED": "INELIGIBLE"},
    # INFO_ONLY — not in matrix, treated as always ELIGIBLE
}

_SEVERITY: dict[Literal["ELIGIBLE", "WARN", "INELIGIBLE"], int] = {
    "ELIGIBLE": 0,
    "WARN": 1,
    "INELIGIBLE": 2,
}


def _worst(
    a: Literal["ELIGIBLE", "WARN", "INELIGIBLE"],
    b: Literal["ELIGIBLE", "WARN", "INELIGIBLE"],
) -> Literal["ELIGIBLE", "WARN", "INELIGIBLE"]:
    return a if _SEVERITY[a] >= _SEVERITY[b] else b


# ---------------------------------------------------------------------------
# Eligibility check
# ---------------------------------------------------------------------------

def _check_eligibility(
    rows: list[SignalRow],
    intents: list[Intent],
) -> tuple[Literal["ELIGIBLE", "WARN", "INELIGIBLE"], str | None]:
    """Compute combined eligibility for *rows* against *intents*.

    Only ACTION intents are checked; CONTEXT intents (U_TP_HIT, U_SL_HIT)
    are informational only and never degrade eligibility.

    Returns:
        (eligibility, reason) where reason is None when ELIGIBLE.
    """
    action_intents = [i for i in intents if i.kind == "ACTION"]

    if not action_intents:
        # No ACTION intents → nothing to check → eligible (e.g. only CONTEXT intents)
        return "ELIGIBLE", None

    result: Literal["ELIGIBLE", "WARN", "INELIGIBLE"] = "ELIGIBLE"
    worst_reason: str | None = None

    for intent in action_intents:
        per_intent = _MATRIX.get(intent.name)
        if per_intent is None:
            # Unknown intent — treat as ELIGIBLE (conservative)
            continue
        for row in rows:
            # Normalise status: anything other than PENDING/ACTIVE/CLOSED → treat as ACTIVE
            status = row.status.upper()
            if status not in per_intent:
                status = "ACTIVE"
            cell: Literal["ELIGIBLE", "WARN", "INELIGIBLE"] = per_intent[status]
            if _SEVERITY[cell] > _SEVERITY[result]:
                result = cell
                worst_reason = (
                    f"intent={intent.name} status={row.status} "
                    f"attempt_key={row.attempt_key}"
                )

    return result, worst_reason


# ---------------------------------------------------------------------------
# op_signal_id lookup
# ---------------------------------------------------------------------------

async def _get_op_signal_ids(
    attempt_keys: list[str],
    db_path: Path | str,
) -> list[int]:
    """Fetch op_signal_id values from operational_signals for given attempt_keys."""
    if not attempt_keys:
        return []
    placeholders = ",".join("?" * len(attempt_keys))
    try:
        async with aiosqlite.connect(db_path) as db:
            async with db.execute(
                f"SELECT op_signal_id FROM operational_signals "
                f"WHERE attempt_key IN ({placeholders})",
                attempt_keys,
            ) as cur:
                return [row[0] async for row in cur]
    except aiosqlite.OperationalError:
        return []


# ---------------------------------------------------------------------------
# Signal dispatch helpers
# ---------------------------------------------------------------------------

async def _resolve_strong(
    target_ref_method: str | None,
    ref: int | str | None,
    trader_id: str,
    db_path: Path | str,
) -> list[SignalRow]:
    """Dispatch STRONG resolution by method."""
    if target_ref_method == "REPLY":
        if ref is None:
            return []
        row = await get_by_root_telegram_id(ref, trader_id, db_path)
        return [row] if row else []

    if target_ref_method == "EXPLICIT_ID":
        if ref is None or not isinstance(ref, int):
            try:
                ref = int(ref)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return []
        row = await get_by_trader_signal_id(int(ref), trader_id, db_path)
        return [row] if row else []

    # TELEGRAM_LINK — not yet implemented (requires extracted_link in parse_results)
    return []


async def _resolve_symbol(
    symbol: str | None,
    trader_id: str,
    db_path: Path | str,
) -> list[SignalRow]:
    if symbol is None:
        return []
    return await get_open_by_symbol(trader_id, symbol, db_path)


_SCOPE_TO_SIDE: dict[str, Literal["BUY", "SELL"] | None] = {
    "all_long":      "BUY",
    "all_short":     "SELL",
    "all_positions": None,
}


async def _resolve_global(
    scope: str | None,
    trader_id: str,
    db_path: Path | str,
) -> list[SignalRow]:
    if scope is None:
        return []
    side = _SCOPE_TO_SIDE.get(scope)  # None if scope unknown → no side filter
    # For unknown scopes, fall back to all_positions behaviour
    if scope not in _SCOPE_TO_SIDE:
        side = None
    return await get_open_by_trader(trader_id, side, db_path)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def resolve(
    operational: OperationalSignal,
    db_path: Path | str,
) -> ResolvedTarget | None:
    """Resolve target_ref for *operational* into a ResolvedTarget.

    Returns None when target_ref is None (typical for NEW_SIGNAL without
    an explicit reference — caller treats this as "no target to resolve").

    Args:
        operational: The OperationalSignal produced by the engine.
        db_path: Path to the SQLite DB.

    Returns:
        ResolvedTarget with eligibility set, or None for no target_ref.
    """
    target_ref = operational.parse_result.target_ref
    if target_ref is None:
        return None

    trader_id = operational.parse_result.trader_id
    kind = target_ref.kind

    # ── Step 1: find matching signal rows ────────────────────────────────────
    rows: list[SignalRow]

    if kind == "STRONG":
        rows = await _resolve_strong(
            target_ref.method, target_ref.ref, trader_id, db_path
        )
    elif kind == "SYMBOL":
        rows = await _resolve_symbol(target_ref.symbol, trader_id, db_path)
    elif kind == "GLOBAL":
        rows = await _resolve_global(target_ref.scope, trader_id, db_path)
    else:
        rows = []

    # ── Step 2: UNRESOLVED if nothing found ──────────────────────────────────
    if not rows:
        reason: str
        if kind == "STRONG" and target_ref.method == "TELEGRAM_LINK":
            reason = "TELEGRAM_LINK resolution not yet implemented"
        else:
            reason = f"no signals found for kind={kind}"
        return ResolvedTarget(
            kind=kind,
            position_ids=[],
            eligibility="UNRESOLVED",
            reason=reason,
        )

    # ── Step 3: resolve op_signal_ids ────────────────────────────────────────
    attempt_keys = [r.attempt_key for r in rows]
    position_ids = await _get_op_signal_ids(attempt_keys, db_path)

    # ── Step 4: eligibility check (intent-aware) ──────────────────────────────
    intents = operational.parse_result.intents
    eligibility, elig_reason = _check_eligibility(rows, intents)

    return ResolvedTarget(
        kind=kind,
        position_ids=position_ids,
        eligibility=eligibility,
        reason=elig_reason,
    )
