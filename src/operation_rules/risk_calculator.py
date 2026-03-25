"""Risk calculator for Layer 4 — Operation Rules Engine.

Fornisce tre funzioni:

    compute_exposure(parse_result, rules) → float
        Calcola l'esposizione % per un NEW_SIGNAL dalla formula:
        position_size_pct × (|entry - SL| / entry) × leverage

    sum_exposure(trader_id, db_path) → float  (async)
        Somma le esposizioni di tutti i segnali aperti del trader.

    sum_exposure_global(db_path) → float  (async)
        Somma le esposizioni di tutti i segnali aperti (globale).

Le funzioni DB leggono da `operational_signals JOIN signals` dove
signals.status != 'CLOSED' AND operational_signals.is_blocked = 0
AND operational_signals.message_type = 'NEW_SIGNAL'.

L'esposizione per riga viene calcolata al volo da:
  - operational_signals.position_size_pct
  - operational_signals.leverage
  - signals.sl  (prezzo SL originale)
  - signals.entry_json  (lista entry, prima voce usata come reference price)

Se i dati necessari non sono disponibili, la riga viene saltata (contribuisce 0).

Usage:
    from src.operation_rules.risk_calculator import (
        compute_exposure,
        sum_exposure,
        sum_exposure_global,
    )
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import aiosqlite

if TYPE_CHECKING:
    from src.operation_rules.loader import MergedRules
    from src.parser.models.canonical import TraderParseResult


# ---------------------------------------------------------------------------
# compute_exposure — pure, sync
# ---------------------------------------------------------------------------

def compute_exposure(parse_result: TraderParseResult, rules: MergedRules) -> float:
    """Compute exposure % for a NEW_SIGNAL.

    Formula: position_size_pct × (|entry_ref - sl| / entry_ref) × leverage

    entry_ref is the average price of all priced entries. For MARKET signals
    without any priced entry, returns 0.0 (cannot compute without a reference).

    Returns:
        Exposure as a percentage (e.g. 0.5 means 0.5% of portfolio).
        Returns 0.0 for non-NEW_SIGNAL messages or when data is insufficient.
    """
    if parse_result.message_type != "NEW_SIGNAL":
        return 0.0

    entities = parse_result.entities
    if entities is None:
        return 0.0

    stop_loss = getattr(entities, "stop_loss", None)
    if stop_loss is None:
        return 0.0

    sl_price = stop_loss.price.value
    if sl_price <= 0:
        return 0.0

    # Reference entry price: average of all priced entries
    entries = getattr(entities, "entries", [])
    priced = [e.price.value for e in entries if e.price is not None and e.price.value > 0]

    if not priced:
        # MARKET signal with no fixed entry — exposure cannot be computed
        return 0.0

    entry_ref = sum(priced) / len(priced)
    if entry_ref <= 0:
        return 0.0

    sl_distance_frac = abs(entry_ref - sl_price) / entry_ref
    return rules.position_size_pct * sl_distance_frac * rules.leverage


# ---------------------------------------------------------------------------
# DB exposure queries — async
# ---------------------------------------------------------------------------

_QUERY_TRADER = """
    SELECT
        op.position_size_pct,
        op.leverage,
        s.sl,
        s.entry_json
    FROM operational_signals op
    JOIN signals s ON op.attempt_key = s.attempt_key
    WHERE op.trader_id = ?
      AND op.message_type = 'NEW_SIGNAL'
      AND op.is_blocked = 0
      AND s.status != 'CLOSED'
"""

_QUERY_GLOBAL = """
    SELECT
        op.position_size_pct,
        op.leverage,
        s.sl,
        s.entry_json
    FROM operational_signals op
    JOIN signals s ON op.attempt_key = s.attempt_key
    WHERE op.message_type = 'NEW_SIGNAL'
      AND op.is_blocked = 0
      AND s.status != 'CLOSED'
"""


def _row_exposure(position_size_pct: float | None,
                  leverage: int | None,
                  sl: float | None,
                  entry_json: str | None) -> float:
    """Compute exposure for a single DB row. Returns 0.0 if data is insufficient."""
    if position_size_pct is None or leverage is None or sl is None or sl <= 0:
        return 0.0

    entry_price: float | None = None
    if entry_json:
        try:
            entries = json.loads(entry_json)
            if isinstance(entries, list) and entries:
                first = entries[0]
                # entry_json può avere forma [{"price": ...}, ...] o [price, ...]
                if isinstance(first, dict):
                    entry_price = float(first.get("price") or 0) or None
                elif isinstance(first, (int, float)):
                    entry_price = float(first) or None
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    if entry_price is None or entry_price <= 0:
        return 0.0

    sl_distance_frac = abs(entry_price - sl) / entry_price
    return position_size_pct * sl_distance_frac * leverage


async def sum_exposure(trader_id: str, db_path: Path | str) -> float:
    """Async: somma esposizioni aperte per il trader specificato.

    Legge da operational_signals JOIN signals dove status != 'CLOSED'.
    Restituisce 0.0 se non ci sono segnali aperti o la tabella è vuota.
    """
    total = 0.0
    try:
        async with aiosqlite.connect(db_path) as db:
            async with db.execute(_QUERY_TRADER, (trader_id,)) as cursor:
                async for row in cursor:
                    total += _row_exposure(row[0], row[1], row[2], row[3])
    except aiosqlite.OperationalError:
        # Tabella non esiste ancora (DB fresh) — restituisce 0
        pass
    return total


async def sum_exposure_global(db_path: Path | str) -> float:
    """Async: somma esposizioni aperte per tutti i trader (global).

    Restituisce 0.0 se non ci sono segnali aperti o la tabella è vuota.
    """
    total = 0.0
    try:
        async with aiosqlite.connect(db_path) as db:
            async with db.execute(_QUERY_GLOBAL) as cursor:
                async for row in cursor:
                    total += _row_exposure(row[0], row[1], row[2], row[3])
    except aiosqlite.OperationalError:
        # Tabella non esiste ancora (DB fresh) — restituisce 0
        pass
    return total
