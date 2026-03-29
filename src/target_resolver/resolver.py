"""Layer 5 — Target Resolver.

Resolves target_ref in an OperationalSignal to concrete op_signal_ids.

Resolution strategy (by target_ref.kind / method):

    STRONG / REPLY          → signals WHERE root_telegram_id = reply_to_msg_id
    STRONG / TELEGRAM_LINK  → parse_results WHERE extracted_link matches
    STRONG / EXPLICIT_ID    → signals WHERE trader_signal_id = ref
    SYMBOL                  → signals WHERE trader_id=? AND symbol=? AND open
    GLOBAL / all_long       → signals WHERE trader_id=? AND side='BUY' AND open
    GLOBAL / all_short      → signals WHERE trader_id=? AND side='SELL' AND open
    GLOBAL / all_positions  → signals WHERE trader_id=? AND open

Eligibility is checked per intent (see _check_eligibility).

Usage:
    resolver = TargetResolver()
    resolved = resolver.resolve(op_signal, db_path=db_path)
    # resolved is None for NEW_SIGNAL without target_ref
"""

from __future__ import annotations

import sqlite3
from typing import Any, Literal

from src.parser.models.operational import OperationalSignal, ResolvedTarget
from src.storage.signals_query import OpenSignal, SignalsQuery


# ---------------------------------------------------------------------------
# Intent → eligibility map
# ---------------------------------------------------------------------------

# Eligibility per status per intent:
#   PENDING → what happens when status is PENDING
#   ACTIVE  → what happens when status is ACTIVE
#   CLOSED  → what happens when status is CLOSED
_INTENT_ELIGIBILITY: dict[str, dict[str, str]] = {
    "U_CANCEL_PENDING":    {"PENDING": "ELIGIBLE", "ACTIVE": "ELIGIBLE",  "CLOSED": "INELIGIBLE"},
    "U_CLOSE_FULL":        {"PENDING": "WARN",      "ACTIVE": "ELIGIBLE",  "CLOSED": "INELIGIBLE"},
    "U_CLOSE_PARTIAL":     {"PENDING": "WARN",      "ACTIVE": "ELIGIBLE",  "CLOSED": "INELIGIBLE"},
    "U_MOVE_STOP":         {"PENDING": "WARN",      "ACTIVE": "ELIGIBLE",  "CLOSED": "INELIGIBLE"},
    "U_MOVE_STOP_TO_BE":   {"PENDING": "WARN",      "ACTIVE": "ELIGIBLE",  "CLOSED": "INELIGIBLE"},
    "U_REENTER":           {"PENDING": "ELIGIBLE",  "ACTIVE": "ELIGIBLE",  "CLOSED": "INELIGIBLE"},
    "U_ADD_ENTRY":         {"PENDING": "ELIGIBLE",  "ACTIVE": "ELIGIBLE",  "CLOSED": "INELIGIBLE"},
    "U_MODIFY_ENTRY":      {"PENDING": "ELIGIBLE",  "ACTIVE": "ELIGIBLE",  "CLOSED": "INELIGIBLE"},
    "U_UPDATE_TAKE_PROFITS": {"PENDING": "WARN",    "ACTIVE": "ELIGIBLE",  "CLOSED": "INELIGIBLE"},
    "U_INVALIDATE_SETUP":  {"PENDING": "ELIGIBLE",  "ACTIVE": "WARN",      "CLOSED": "INELIGIBLE"},
    # Context intents — always INFO_ONLY
    "U_TP_HIT":            {"PENDING": "ELIGIBLE",  "ACTIVE": "ELIGIBLE",  "CLOSED": "ELIGIBLE"},
    "U_TP_HIT_EXPLICIT":   {"PENDING": "ELIGIBLE",  "ACTIVE": "ELIGIBLE",  "CLOSED": "ELIGIBLE"},
    "U_SL_HIT":            {"PENDING": "ELIGIBLE",  "ACTIVE": "ELIGIBLE",  "CLOSED": "ELIGIBLE"},
    "U_STOP_HIT":          {"PENDING": "ELIGIBLE",  "ACTIVE": "ELIGIBLE",  "CLOSED": "ELIGIBLE"},
}

_CLOSED_STATUSES = {"CLOSED", "CANCELLED"}
_ACTION_INTENTS = frozenset({
    "U_CANCEL_PENDING", "U_CLOSE_FULL", "U_CLOSE_PARTIAL", "U_MOVE_STOP",
    "U_MOVE_STOP_TO_BE", "U_REENTER", "U_ADD_ENTRY", "U_MODIFY_ENTRY",
    "U_UPDATE_TAKE_PROFITS", "U_INVALIDATE_SETUP",
})

_LEGACY_STRONG_KIND_MAP = {
    "REPLY": ("STRONG", "REPLY"),
    "TELEGRAM_LINK": ("STRONG", "TELEGRAM_LINK"),
    "MESSAGE_ID": ("STRONG", "TELEGRAM_LINK"),
    "SIGNAL_ID": ("STRONG", "EXPLICIT_ID"),
}


def _normalise_status(status: str) -> str:
    """Map raw DB status to PENDING/ACTIVE/CLOSED for eligibility check."""
    s = status.upper()
    if s in {"PENDING", "QUEUED"}:
        return "PENDING"
    if s in {"CLOSED", "CANCELLED"}:
        return "CLOSED"
    return "ACTIVE"


def _check_eligibility(
    signals: list[OpenSignal],
    action_intents: list[str],
) -> tuple[Literal["ELIGIBLE", "INELIGIBLE", "WARN"], str | None]:
    """Determine eligibility for a list of resolved signals and intents.

    Returns (eligibility, reason).
    INELIGIBLE if all signals are closed.
    WARN if any signal is PENDING for an action that prefers ACTIVE.
    ELIGIBLE otherwise.
    """
    if not signals:
        return "INELIGIBLE", "no_matching_signals"

    # If all are closed → INELIGIBLE
    if all(_normalise_status(s.status) in ("CLOSED",) for s in signals):
        return "INELIGIBLE", "all_targets_closed"

    # Check per intent
    worst = "ELIGIBLE"
    worst_reason: str | None = None

    for intent in action_intents:
        intent_map = _INTENT_ELIGIBILITY.get(intent)
        if intent_map is None:
            continue
        for sig in signals:
            norm_status = _normalise_status(sig.status)
            elig = intent_map.get(norm_status, "ELIGIBLE")
            if elig == "INELIGIBLE":
                return "INELIGIBLE", f"{intent}:{norm_status.lower()}"
            if elig == "WARN" and worst != "INELIGIBLE":
                worst = "WARN"
                worst_reason = f"{intent}:{norm_status.lower()}"

    return worst, worst_reason  # type: ignore[return-value]


def _normalize_target_ref(target_ref: dict[str, Any]) -> dict[str, Any]:
    """Normalize legacy parser target refs to the resolver's canonical shape."""
    normalized = dict(target_ref)

    raw_kind = str(normalized.get("kind", "SYMBOL")).strip().upper()
    raw_method = str(normalized.get("method", "")).strip().upper()

    if raw_kind in _LEGACY_STRONG_KIND_MAP:
        canonical_kind, canonical_method = _LEGACY_STRONG_KIND_MAP[raw_kind]
        normalized["kind"] = canonical_kind
        normalized["method"] = canonical_method
        if raw_kind == "MESSAGE_ID" and normalized.get("ref") is not None:
            normalized["ref"] = f"https://t.me/c/0/{normalized['ref']}"
        return normalized

    if raw_kind == "SYMBOL":
        normalized["kind"] = "SYMBOL"
        if not normalized.get("symbol") and normalized.get("ref") is not None:
            normalized["symbol"] = str(normalized["ref"]).upper()
        return normalized

    if raw_kind == "GLOBAL":
        normalized["kind"] = "GLOBAL"
        return normalized

    if raw_kind == "STRONG":
        normalized["kind"] = "STRONG"
        normalized["method"] = raw_method or None
        return normalized

    return normalized


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


class TargetResolver:
    """Resolve target_ref in an OperationalSignal to concrete op_signal_ids."""

    def resolve(
        self,
        op_signal: OperationalSignal,
        *,
        db_path: str,
    ) -> ResolvedTarget | None:
        """Resolve the target for *op_signal*.

        Returns:
            None  — for NEW_SIGNAL without explicit target_ref.
            ResolvedTarget — for UPDATE messages or NEW_SIGNAL with target_ref.
        """
        parse_result = op_signal.parse_result
        target_refs: list[dict[str, Any]] = list(parse_result.target_refs or [])
        message_type = parse_result.message_type
        # trader_id is set by the engine; fall back to linking/entities if missing
        trader_id = op_signal.trader_id
        if not trader_id:
            linking = getattr(parse_result, "linking", {}) or {}
            trader_id = str(linking.get("trader_id", ""))
        if not trader_id and isinstance(parse_result.entities, dict):
            trader_id = str(parse_result.entities.get("resolved_trader_id", "") or "")

        # NEW_SIGNAL with no target_refs → no target
        if message_type == "NEW_SIGNAL" and not target_refs:
            return None

        # Get action intents for eligibility
        intents: list[str] = list(parse_result.intents or [])
        action_intents = [i for i in intents if i in _ACTION_INTENTS]

        sq = SignalsQuery(db_path)

        if not target_refs:
            return ResolvedTarget(
                kind="GLOBAL",
                position_ids=[],
                eligibility="UNRESOLVED",
                reason="no_target_ref",
            )

        last_unresolved: ResolvedTarget | None = None

        for raw_target_ref in target_refs:
            target_ref = _normalize_target_ref(raw_target_ref if isinstance(raw_target_ref, dict) else {})
            kind: str = str(target_ref.get("kind", "SYMBOL")).upper()
            method: str = str(target_ref.get("method", "")).upper()
            ref = target_ref.get("ref")
            symbol = str(target_ref.get("symbol", "")).upper()
            scope = str(target_ref.get("scope", "")).lower()

            resolved_signals: list[OpenSignal] = []

            if kind == "STRONG":
                resolved_signals = self._resolve_strong(
                    sq, trader_id, method, ref, db_path
                )
            elif kind == "SYMBOL":
                sym = symbol or str(
                    parse_result.entities.get("symbol", "") if isinstance(parse_result.entities, dict) else ""
                ).upper()
                resolved_signals = sq.get_open_by_trader_and_symbol(trader_id, sym)
            elif kind == "GLOBAL":
                resolved_signals = self._resolve_global(sq, trader_id, scope)
            else:
                last_unresolved = ResolvedTarget(
                    kind="SYMBOL",
                    position_ids=[],
                    eligibility="UNRESOLVED",
                    reason=f"unknown_target_ref_kind:{kind}",
                )
                continue

            if not resolved_signals:
                last_unresolved = ResolvedTarget(
                    kind=kind,  # type: ignore[arg-type]
                    position_ids=[],
                    eligibility="UNRESOLVED",
                    reason="no_matching_open_signal",
                )
                continue

            position_ids: list[int] = []
            for sig in resolved_signals:
                op_id = sq.get_op_signal_id_for_attempt_key(sig.attempt_key)
                if op_id is not None:
                    position_ids.append(op_id)

            eligibility, reason = _check_eligibility(resolved_signals, action_intents)

            return ResolvedTarget(
                kind=kind,  # type: ignore[arg-type]
                position_ids=position_ids,
                eligibility=eligibility,
                reason=reason,
            )

        return last_unresolved or ResolvedTarget(
            kind="SYMBOL",
            position_ids=[],
            eligibility="UNRESOLVED",
            reason="no_matching_open_signal",
        )

    def _resolve_strong(
        self,
        sq: SignalsQuery,
        trader_id: str,
        method: str,
        ref: Any,
        db_path: str,
    ) -> list[OpenSignal]:
        if method == "REPLY" and ref is not None:
            # Match by root_telegram_id (reply_to_message_id of the UPDATE)
            sig = sq.get_by_root_telegram_id(trader_id, str(ref))
            return [sig] if sig is not None else []

        if method == "EXPLICIT_ID" and ref is not None:
            # Match by trader_signal_id in signals table
            try:
                with sqlite3.connect(db_path) as conn:
                    row = conn.execute(
                        """SELECT attempt_key, trader_id, symbol, side, status,
                                  entry_json, sl, confidence, root_telegram_id
                           FROM signals
                           WHERE trader_id = ? AND trader_signal_id = ?
                           LIMIT 1""",
                        (trader_id, int(ref)),
                    ).fetchone()
            except (sqlite3.OperationalError, (TypeError, ValueError)):
                return []
            if row is None:
                return []
            from src.storage.signals_query import SignalsQuery as _SQ
            return [_SQ._row_to_open(row)]

        if method == "TELEGRAM_LINK" and ref is not None:
            # Match by extracted_link in parse_results — complex, best-effort
            try:
                with sqlite3.connect(db_path) as conn:
                    rows = conn.execute(
                        """SELECT s.attempt_key, s.trader_id, s.symbol, s.side, s.status,
                                  s.entry_json, s.sl, s.confidence, s.root_telegram_id
                           FROM signals s
                           JOIN parse_results pr ON pr.raw_message_id = (
                               SELECT rm.raw_message_id FROM raw_messages rm
                               WHERE rm.source_chat_id = s.channel_id
                                 AND rm.telegram_message_id = CAST(s.root_telegram_id AS INTEGER)
                               LIMIT 1
                           )
                           WHERE s.trader_id = ?
                             AND s.status NOT IN ('CLOSED', 'CANCELLED')
                             AND pr.parse_result_normalized_json LIKE ?
                           LIMIT 1""",
                        (trader_id, f"%{ref}%"),
                    ).fetchall()
            except sqlite3.OperationalError:
                return []
            from src.storage.signals_query import SignalsQuery as _SQ
            return [_SQ._row_to_open(r) for r in rows]

        return []

    def _resolve_global(
        self,
        sq: SignalsQuery,
        trader_id: str,
        scope: str,
    ) -> list[OpenSignal]:
        if scope == "all_long":
            return sq.get_open_by_side(trader_id, "BUY")
        if scope == "all_short":
            return sq.get_open_by_side(trader_id, "SELL")
        # all_positions or unknown scope → return all open
        return sq.get_all_open(trader_id)
