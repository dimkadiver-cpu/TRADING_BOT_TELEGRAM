from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from src.runtime_v2.lifecycle.entry_command_factory import EntryCommandFactory
from src.runtime_v2.lifecycle.be_move_resolver import resolve_be_stop_price
from src.runtime_v2.lifecycle.execution_plan import ExecutionPlanBuilder
from src.runtime_v2.lifecycle.models import (
    BeProtectionStatus, ControlMode, ExecutionCommand,
    LifecycleEvent, LifecycleState, TradeChain,
)
from src.runtime_v2.lifecycle.ports import (
    AccountStateSnapshot, ExchangeDataPort, SymbolMarketSnapshot,
)
from src.runtime_v2.lifecycle.cancel_expander import (
    expand_cancel_pending_commands as _expand_cancel_pending_commands,
)
from src.runtime_v2.lifecycle.risk_capacity import RiskCapacityEngine
from src.runtime_v2.signal_enrichment.models import (
    EnrichedCanonicalMessage, ManagementPlanConfig,
)
from src.runtime_v2.control_plane.outbox_writer import (
    project_clean_log_for_chain,
    write_clean_log_event,
)

logger = logging.getLogger(__name__)

GLOBAL_SCOPES = frozenset({"ALL_POSITIONS", "ALL_OPEN", "ALL_REMAINING"})


def _norm_signal_id(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lstrip("#").strip().lower()
    return normalized or None


def _normalize_signal_ids(values: list[str] | None) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        norm = _norm_signal_id(value)
        if norm is None or norm in seen:
            continue
        normalized.append(norm)
        seen.add(norm)
    return normalized


def _split_external_signal_ids(value: str | None) -> set[str]:
    return set(_normalize_signal_ids(value.split("|") if value else []))


def _find_leg_snap(legs_snap: list[dict], sequence: int) -> dict | None:
    for snap in legs_snap or []:
        if snap.get("sequence") == sequence:
            return snap
    return None


@dataclass
class SignalGateResult:
    trade_chain: TradeChain | None
    lifecycle_events: list[LifecycleEvent]
    execution_commands: list[ExecutionCommand]
    account_snapshot: AccountStateSnapshot | None
    market_snapshot: SymbolMarketSnapshot | None
    review_reason: str | None


@dataclass
class UpdateChainResult:
    trade_chain_id: int
    new_lifecycle_state: LifecycleState | None
    new_be_protection_status: BeProtectionStatus | None
    lifecycle_events: list[LifecycleEvent]
    execution_commands: list[ExecutionCommand]
    new_plan_state_json: str | None = None


@dataclass
class UpdateGateResult:
    chain_results: list[UpdateChainResult]
    review_events: list[LifecycleEvent]


@dataclass(slots=True, frozen=True)
class SignalAdmissionContext:
    signal_message_type: str = "any"
    message_presentation_type: str = "PLAIN"


_ATTACHED_PROTECTION_MODES = frozenset({"UNIFIED_PLAN", "D_POSITION_TPSL"})


def _position_context(chain: "TradeChain") -> dict:
    try:
        rs = json.loads(chain.risk_snapshot_json or "{}")
        hedge_mode = bool(rs.get("hedge_mode", False))
    except Exception:
        hedge_mode = False
    return {
        "hedge_mode": hedge_mode,
        "position_idx": LifecycleEntryGate.resolve_position_idx(chain.side, hedge_mode),
    }


def _be_move_extra(chain: "TradeChain") -> dict:
    context = _position_context(chain)
    protection_style = (
        "attached_full"
        if chain.execution_mode in _ATTACHED_PROTECTION_MODES
        else "standalone_order"
    )
    return {"protection_style": protection_style, **context}


_SOURCE_TYPE_TO_CLEAN_LOG_SOURCE: dict[str, str] = {
    "telegram_update": "trader_update",
    "operation_rules": "operation_rules",
    "manual_command": "manual_command",
}

_ACTION_LABELS: dict[str, str] = {
    "MOVE_SL_TO_BE": "Move SL to BE",
    "CANCEL_PENDING": "Cancel pending",
    "CLOSE_FULL": "Close full",
    "CLOSE_PARTIAL": "Close partial",
    "MODIFY_ENTRIES": "Modify entries",
    "MARKET_ENTRY_NOW": "Market entry now",
    "MOVE_STOP": "Move stop",
}


def _write_update_clean_log(
    conn,
    cr: "UpdateChainResult",
    canonical_message_id: int,
    link: str | None,
) -> None:
    """Synthesize one UPDATE_DONE/PARTIAL/REJECTED CLEAN_LOG row from UpdateChainResult events."""
    accepted = [e for e in cr.lifecycle_events if e.event_type == "TELEGRAM_UPDATE_ACCEPTED"]
    noops = [e for e in cr.lifecycle_events if e.event_type.startswith("NOOP_")]
    if not accepted and not noops:
        return

    if accepted and not noops:
        notif_type = "UPDATE_DONE"
    elif accepted and noops:
        notif_type = "UPDATE_PARTIAL"
    else:
        notif_type = "UPDATE_REJECTED"

    applied_actions: list[str] = []
    changed: list[dict] = []
    failed_actions: list[dict] = []
    reason: str | None = None

    for event in noops:
        try:
            noop_payload = json.loads(event.payload_json or "{}")
        except Exception:
            noop_payload = {}
        noop_reason = noop_payload.get("reason")
        action_name = event.event_type.removeprefix("NOOP_")
        failed_actions.append({
            "action": action_name,
            "reason": str(noop_reason) if noop_reason else "unknown",
        })
        if reason is None and noop_reason:
            reason = str(noop_reason)

    rejected_actions: list[str] = [f["action"] for f in failed_actions]

    for e in accepted:
        try:
            p = json.loads(e.payload_json or "{}")
        except Exception:
            p = {}
        action = p.get("action", "")
        if action:
            applied_actions.append(action)

        if p.get("is_breakeven"):
            changed.append({
                "field": "SL",
                "old": p.get("old_sl_price"),
                "new": p.get("new_sl_price"),
                "note": "BE",
            })
        elif action == "CANCEL_PENDING":
            for entry in p.get("cancelled_entries", []):
                changed.append({
                    "field": f"Entry_{entry.get('sequence', '?')}",
                    "old": entry.get("price"),
                    "new": "cancelled",
                })
        elif action == "CLOSE_FULL":
            changed.append({
                "field": "Position",
                "old": "open",
                "new": "closed 100%",
            })
        elif action == "CLOSE_PARTIAL":
            close_pct = p.get("close_pct")
            if close_pct is not None:
                changed.append({
                    "field": "Position",
                    "old": "open",
                    "new": f"closed {close_pct}%",
                })
        elif action == "MARKET_ENTRY_NOW":
            for ce in p.get("changed_entries", []):
                seq = ce.get("sequence", "?")
                if ce.get("cancelled"):
                    changed.append({
                        "field": f"Entry_{seq}",
                        "old": ce.get("old_price"),
                        "new": "cancelled",
                    })
                else:
                    old_type = ce.get("old_type", "LIMIT")
                    changed.append({
                        "field": f"Entry_{seq}",
                        "old": f"{ce.get('old_price')} {old_type}",
                        "new": "Market",
                    })
        elif action == "MODIFY_ENTRIES":
            for ce in p.get("changed_entries", []):
                changed.append({
                    "field": f"Entry_{ce.get('sequence', '?')}",
                    "old": ce.get("old_price"),
                    "new": ce.get("new_price"),
                })
        elif action == "MOVE_STOP":
            _VALID_REFS = {"Price", "TP_1", "TP_2", "TP_3"}
            changed.append({
                "field": "SL",
                "old": p.get("old_sl_price"),
                "new": p.get("new_sl_price"),
                "note": p.get("reference") if p.get("reference") in _VALID_REFS else None,
            })

    first = (accepted or noops)[0]
    source = _SOURCE_TYPE_TO_CLEAN_LOG_SOURCE.get(first.source_type, "runtime")

    chain_row = conn.execute(
        "SELECT symbol, side, trader_id, account_id FROM ops_trade_chains WHERE trade_chain_id=?",
        (cr.trade_chain_id,),
    ).fetchone()
    symbol = chain_row[0] if chain_row else None
    side = chain_row[1] if chain_row else None
    trader_id = chain_row[2] if chain_row else None
    account_id = chain_row[3] if chain_row else None

    payload = {
        "chain_id": cr.trade_chain_id,
        "symbol": symbol,
        "side": side,
        "trader_id": trader_id,
        "account_id": account_id,
        "applied_actions": applied_actions,
        "rejected_actions": rejected_actions,
        "failed_actions": failed_actions,
        "changed": changed,
        "source": source,
        "link": link,
    }
    if reason is not None:
        payload["reason"] = reason
    write_clean_log_event(
        conn,
        notification_type=notif_type,
        chain_id=cr.trade_chain_id,
        payload=payload,
        account_id=account_id,
        dedupe_key=f"clean:update:{canonical_message_id}:{cr.trade_chain_id}",
    )


def _write_no_target_update_clean_log(
    conn,
    event: "LifecycleEvent",
    enriched: "EnrichedCanonicalMessage",
    link: str | None,
) -> None:
    """Scrive UPDATE_NOT_APPLIED per review_events senza chain (no_update_target / ambiguous)."""
    try:
        ev_data = json.loads(event.payload_json or "{}")
    except Exception:
        ev_data = {}
    reason = ev_data.get("reason", "")
    if reason not in ("no_update_target", "ambiguous_update_target"):
        return
    action_hint = (
        "reply to the specific signal message" if reason == "ambiguous_update_target" else None
    )
    payload: dict = {
        "chain_id": None,
        "symbol": None,
        "side": None,
        "signal_link": link,
        "reason": reason,
        "source": "trader_update",
        "trader_id": enriched.trader_id,
        "account_id": enriched.account_id,
    }
    if action_hint is not None:
        payload["action_hint"] = action_hint
    write_clean_log_event(
        conn,
        notification_type="UPDATE_NOT_APPLIED",
        chain_id=None,
        payload=payload,
        account_id=enriched.account_id,
        dedupe_key=f"clean:{event.idempotency_key}",
    )


_STATUS_RANK: dict[str, int] = {"REVIEW": 3, "PARTIAL": 2, "SKIPPED": 1, "DONE": 0}

_NOOP_REASON_TO_DISPLAY: dict[str, str] = {
    # "Entry_2" is hardcoded here because averaging orders are always placed at sequence 2
    # (business invariant: sequence 1 is the initial entry, sequence 2 is the averaging leg).
    "no pending averaging order": "Entry_2: SKIPPED - no pending averaging order",
}


def _render_update_display_lines(
    accepted: list["LifecycleEvent"],
    noops: list["LifecycleEvent"],
) -> list[str]:
    """Render human-readable display lines for a single chain's update events.

    Noop lines are emitted first (they describe skipped sub-operations),
    followed by lines for accepted actions.
    """
    lines: list[str] = []

    for event in noops:
        try:
            p = json.loads(event.payload_json or "{}")
        except Exception:
            p = {}
        reason = p.get("reason", "")
        if reason in _NOOP_REASON_TO_DISPLAY:
            lines.append(_NOOP_REASON_TO_DISPLAY[reason])
        elif reason:
            lines.append(f"SKIPPED - {reason}")

    for event in accepted:
        try:
            p = json.loads(event.payload_json or "{}")
        except Exception:
            p = {}
        action = p.get("action", "")

        if action == "CANCEL_PENDING":
            for entry in p.get("cancelled_entries", []):
                seq = entry.get("sequence", "?")
                price = entry.get("price", "?")
                lines.append(f"Entry_{seq}: {price} -> cancelled")
        elif action == "MOVE_SL_TO_BE":
            old_sl = p.get("old_sl_price", "?")
            new_sl = p.get("new_sl_price", "?")
            lines.append(f"SL: {old_sl} -> {new_sl} BE")
        elif action == "MOVE_STOP":
            old_sl = p.get("old_sl_price", "?")
            new_sl = p.get("new_sl_price", "?")
            lines.append(f"SL: {old_sl} -> {new_sl}")
            ref = p.get("reference")
            if ref in {"Price", "TP_1", "TP_2", "TP_3"}:
                lines.append(f"Reference: {ref}")
        elif action == "CLOSE_FULL":
            lines.append("Position: open -> closed 100%")
        elif action == "CLOSE_PARTIAL":
            close_pct = p.get("close_pct")
            if close_pct is not None:
                lines.append(f"Position: open -> closed {close_pct}%")
        elif action == "MARKET_ENTRY_NOW":
            for ce in p.get("changed_entries", []):
                seq = ce.get("sequence", "?")
                if ce.get("cancelled"):
                    lines.append(f"Entry_{seq}: {ce.get('old_price')} -> cancelled")
                else:
                    old_type = ce.get("old_type", "LIMIT")
                    lines.append(f"Entry_{seq}: {ce.get('old_price')} {old_type} -> Market")
        elif action == "MODIFY_ENTRIES":
            for ce in p.get("changed_entries", []):
                seq = ce.get("sequence", "?")
                lines.append(f"Entry_{seq}: {ce.get('old_price')} -> {ce.get('new_price')}")

    return lines


def _resolve_summary_status(
    accepted: list["LifecycleEvent"],
    noops: list["LifecycleEvent"],
    reviews: list["LifecycleEvent"],
) -> str:
    # REVIEW outranks all other statuses (consistent with _STATUS_RANK where REVIEW=3 > PARTIAL=2).
    if reviews:
        return "REVIEW"
    elif accepted and not noops:
        return "DONE"
    elif accepted:
        return "PARTIAL"
    else:
        return "SKIPPED"


def _resolve_signal_root_link(conn, chain_id: int) -> str | None:
    tracking_row = conn.execute(
        "SELECT clean_log_root_message_id, telegram_chat_id "
        "FROM ops_clean_log_tracking WHERE trade_chain_id=?",
        (chain_id,),
    ).fetchone()
    if tracking_row and tracking_row[0] and tracking_row[1]:
        normalized_chat = str(tracking_row[1]).removeprefix("-100")
        return f"https://t.me/c/{normalized_chat}/{tracking_row[0]}"
    return None


def _is_close_full_action(payload_json: str | None) -> bool:
    try:
        return json.loads(payload_json or "{}").get("action") == "CLOSE_FULL"
    except Exception:
        return False


def _write_pending_close_full_summary(
    conn,
    chains_payload: list[dict],
    operations_seen: list[str],
    source: str,
    update_source_link: str | None,
    canonical_message_id: int,
) -> None:
    pending_chains = [
        {
            "chain_id": c["chain_id"],
            "symbol": c["symbol"],
            "side": c["side"],
            "status": c["status"],
            "link_mode": "final_close",
            "link": None,
            # Pre-built display_lines are discarded here intentionally: final links and
            # per-chain display lines are populated later when the pending summary is
            # released (_try_release_close_full_summary), once all close-full exchange
            # events have landed and the final-close Telegram message IDs are known.
            "display_lines": [],
        }
        for c in chains_payload
    ]
    done = sum(1 for c in chains_payload if c["status"] == "DONE")
    partial = sum(1 for c in chains_payload if c["status"] == "PARTIAL")
    skipped = sum(1 for c in chains_payload if c["status"] == "SKIPPED")
    error = sum(1 for c in chains_payload if c["status"] == "ERROR")
    payload = {
        "summary_kind": "pending_final_close_links",
        "requested_operations": operations_seen,
        "chains": pending_chains,
        "counts": {"done": done, "partial": partial, "skipped": skipped, "error": error},
        "source": source,
        "link": update_source_link,
    }
    conn.execute(
        "CREATE TABLE IF NOT EXISTS ops_pending_multi_chain_summaries "
        "(pending_id INTEGER PRIMARY KEY, canonical_message_id INTEGER UNIQUE, payload_json TEXT)"
    )
    conn.execute(
        "INSERT OR REPLACE INTO ops_pending_multi_chain_summaries (canonical_message_id, payload_json) VALUES (?, ?)",
        (canonical_message_id, json.dumps(payload)),
    )
    conn.commit()


def _write_multi_chain_summary(
    conn,
    chain_results: list["UpdateChainResult"],
    canonical_message_id: int,
    update_source_link: str | None = None,
) -> None:
    chains_by_id: dict[int, dict] = {}
    operations_seen: list[str] = []
    seen_operations: set[str] = set()

    for cr in chain_results:
        if not cr.trade_chain_id:
            continue

        accepted = [e for e in cr.lifecycle_events if e.event_type == "TELEGRAM_UPDATE_ACCEPTED"]
        noops = [e for e in cr.lifecycle_events if e.event_type.startswith("NOOP_")]
        reviews = [e for e in cr.lifecycle_events if e.event_type == "REVIEW_REQUIRED"]
        if not accepted and not noops and not reviews:
            continue

        status = _resolve_summary_status(accepted, noops, reviews)

        for event in accepted:
            try:
                action = json.loads(event.payload_json or "{}").get("action", "")
            except Exception:
                action = ""
            label = _ACTION_LABELS.get(action, action)
            if label and label not in seen_operations:
                seen_operations.add(label)
                operations_seen.append(label)

        cid = cr.trade_chain_id
        if cid not in chains_by_id:
            row = conn.execute(
                "SELECT symbol, side FROM ops_trade_chains WHERE trade_chain_id=?",
                (cid,),
            ).fetchone()
            signal_link = _resolve_signal_root_link(conn, cid)
            display_lines = _render_update_display_lines(accepted, noops)
            chains_by_id[cid] = {
                "chain_id": cid,
                "symbol": row[0] if row else None,
                "side": row[1] if row else None,
                "status": status,
                "link_mode": "signal_root",
                "link": signal_link,
                "display_lines": display_lines,
            }
        elif _STATUS_RANK.get(status, 0) > _STATUS_RANK.get(chains_by_id[cid]["status"], 0):
            chains_by_id[cid]["status"] = status

    chains_payload = list(chains_by_id.values())
    if len(chains_payload) < 2:
        return

    contains_close_full = any(
        any(
            _is_close_full_action(e.payload_json)
            for e in cr.lifecycle_events
            if e.event_type == "TELEGRAM_UPDATE_ACCEPTED"
        )
        for cr in chain_results
        if cr.trade_chain_id
    )

    source = "runtime"
    for cr in chain_results:
        if cr.lifecycle_events:
            source = _SOURCE_TYPE_TO_CLEAN_LOG_SOURCE.get(
                cr.lifecycle_events[0].source_type, "runtime"
            )
            break

    if contains_close_full:
        _write_pending_close_full_summary(
            conn,
            chains_payload,
            operations_seen,
            source,
            update_source_link,
            canonical_message_id,
        )
        return

    done = sum(1 for c in chains_payload if c["status"] == "DONE")
    partial = sum(1 for c in chains_payload if c["status"] == "PARTIAL")
    skipped = sum(1 for c in chains_payload if c["status"] == "SKIPPED")
    # "error" is currently always 0 because _resolve_summary_status never returns "ERROR".
    # It is kept in the payload as a placeholder so downstream formatters (e.g. close-full footer)
    # can reference it without a schema change when error-state detection is added in the future.
    error = sum(1 for c in chains_payload if c["status"] == "ERROR")

    write_clean_log_event(
        conn,
        notification_type="MULTI_CHAIN_SUMMARY",
        chain_id=None,
        payload={
            "summary_kind": "immediate",
            "requested_operations": operations_seen,
            "chains": chains_payload,
            "counts": {"done": done, "partial": partial, "skipped": skipped, "error": error},
            "source": source,
            "link": update_source_link,
        },
        dedupe_key=f"clean:multi_summary:{canonical_message_id}",
    )


_SIGNAL_CONTENT_REJECT_REASONS: frozenset[str] = frozenset({
    "missing_symbol_or_side",
    "no_entry_legs",
    "no_signal_payload",
    "missing_stop_loss_for_risk_calc",
    "missing_limit_price",
    "zero_risk_distance",
    "unknown_symbol",
})


class LifecycleEntryGate:
    def __init__(
        self,
        risk_engine: RiskCapacityEngine,
        exchange_port: ExchangeDataPort,
        simple_attached_enabled: bool = True,
    ) -> None:
        self._risk = risk_engine
        self._port = exchange_port
        self._simple_attached_enabled = simple_attached_enabled

    # ── SIGNAL ────────────────────────────────────────────────────────────────

    def process_signal(
        self,
        enriched: EnrichedCanonicalMessage,
        open_chains: list[TradeChain],
        control_mode: ControlMode,
        admission: SignalAdmissionContext | None = None,
    ) -> SignalGateResult:
        admission = admission or SignalAdmissionContext()
        eid = enriched.enrichment_id

        if control_mode in ("BLOCK_NEW_ENTRIES", "FULL_STOP"):
            return self._reject_signal(
                eid, "control_mode:new_entries_paused",
                account_id=enriched.account_id, trader_id=enriched.trader_id,
            )

        signal = enriched.enriched_signal
        if signal is None or not signal.symbol or not signal.side:
            return self._reject_signal(
                eid, "missing_symbol_or_side",
                account_id=enriched.account_id, trader_id=enriched.trader_id,
                symbol=signal.symbol if signal else None,
                side=signal.side if signal else None,
            )

        if admission.signal_message_type == "inline_buttons" and admission.message_presentation_type != "INLINE_BUTTONS":
            return self._skip_signal(eid, "signal_message_type_mismatch")

        if not self._port.symbol_exists(enriched.account_id, signal.symbol):
            return self._reject_signal(
                eid, "unknown_symbol",
                account_id=enriched.account_id, trader_id=enriched.trader_id,
                symbol=signal.symbol, side=signal.side,
            )

        if not signal.entries:
            return self._reject_signal(
                eid, "no_entry_legs",
                account_id=enriched.account_id, trader_id=enriched.trader_id,
                symbol=signal.symbol, side=signal.side,
            )

        account_snapshot = self._port.get_account_state(enriched.account_id)
        market_snapshot = self._port.get_symbol_market_state(enriched.account_id, signal.symbol)

        decision = self._risk.validate(enriched, open_chains, account_snapshot, market_snapshot)
        if not decision.passed:
            return self._reject_signal(
                eid, decision.reason or "risk_check_failed",
                risk_snapshot=decision.risk_snapshot,
                account_id=enriched.account_id, trader_id=enriched.trader_id,
                symbol=signal.symbol, side=signal.side,
            )

        management_plan = enriched.management_plan or ManagementPlanConfig()
        timeout_at = None
        if management_plan.cancel_pending_on_timeout:
            timeout_at = datetime.now(timezone.utc) + timedelta(
                hours=management_plan.pending_timeout_hours
            )

        size_usdt = float(decision.size_usdt or 0.0)
        fallback_entry_price = float(decision.risk_snapshot.get("entry_price") or 1.0)
        planned_qty = size_usdt / fallback_entry_price if fallback_entry_price > 0 else 0.0

        sl_price_for_decision = (
            signal.stop_loss.price.value
            if signal.stop_loss and signal.stop_loss.price else None
        )
        if self._simple_attached_enabled is True and sl_price_for_decision is not None:
            chain_execution_mode = "UNIFIED_PLAN"
        else:
            chain_execution_mode = "D_POSITION_TPSL"

        extra_plan: dict = {}
        if signal.range_derivation is not None:
            extra_plan["range_derivation"] = signal.range_derivation.model_dump()
        if decision.hint_applied is not None:
            extra_plan["risk_hint_applied"] = decision.hint_applied
        if signal.original_tp_count is not None and signal.original_tp_count > len(signal.take_profits):
            extra_plan["tp_trimmed"] = {
                "original": signal.original_tp_count,
                "used": len(signal.take_profits),
            }
        if signal.entry_sequence_realigned is not None:
            extra_plan["entry_sequence_realigned"] = signal.entry_sequence_realigned.model_dump()
        close_pcts = self._get_close_pcts(management_plan, len(signal.take_profits))
        if close_pcts:
            extra_plan["close_pcts"] = close_pcts

        plan_state = ExecutionPlanBuilder.build(
            eid,
            signal.entries,
            signal.take_profits,
            decision.risk_snapshot,
            extra_plan_metadata=extra_plan or None,
        )

        chain = TradeChain(
            source_enrichment_id=eid,
            canonical_message_id=enriched.canonical_message_id,
            raw_message_id=enriched.raw_message_id,
            trader_id=enriched.trader_id,
            account_id=enriched.account_id,
            symbol=signal.symbol,
            side=signal.side,
            lifecycle_state="WAITING_ENTRY",
            entry_mode=signal.entry_structure or "ONE_SHOT",
            expected_stop_price=(
                signal.stop_loss.price.value
                if signal.stop_loss and signal.stop_loss.price else None
            ),
            be_protection_status="NOT_PROTECTED",
            entry_timeout_at=timeout_at,
            management_plan_json=management_plan.model_dump_json(),
            risk_snapshot_json=json.dumps(decision.risk_snapshot),
            planned_entry_qty=planned_qty,
            execution_mode=chain_execution_mode,
            plan_state_json=plan_state,
        )

        events = [
            LifecycleEvent(
                event_type="SIGNAL_ACCEPTED",
                source_type="enrichment",
                source_id=str(eid),
                next_state="WAITING_ENTRY",
                idempotency_key=f"sig_accepted:{eid}",
            ),
            LifecycleEvent(
                event_type="TRADE_CHAIN_CREATED",
                source_type="enrichment",
                source_id=str(eid),
                idempotency_key=f"chain_created:{eid}",
            ),
        ]

        commands = self._build_entry_commands(enriched, decision)

        return SignalGateResult(
            trade_chain=chain,
            lifecycle_events=events,
            execution_commands=commands,
            account_snapshot=account_snapshot,
            market_snapshot=market_snapshot,
            review_reason=None,
        )

    def _reject_signal(
        self,
        eid: int | None,
        reason: str,
        risk_snapshot: dict | None = None,
        *,
        account_id: str | None = None,
        trader_id: str | None = None,
        symbol: str | None = None,
        side: str | None = None,
    ) -> SignalGateResult:
        source = "trader_signal" if reason in _SIGNAL_CONTENT_REJECT_REASONS else "runtime"
        ev_payload: dict = {"reason": reason, "source": source}
        if account_id is not None:
            ev_payload["account_id"] = account_id
        if trader_id is not None:
            ev_payload["trader_id"] = trader_id
        if symbol is not None:
            ev_payload["symbol"] = symbol
        if side is not None:
            ev_payload["side"] = side
        if risk_snapshot:
            ev_payload["capital"] = risk_snapshot.get("capital")
            ev_payload["risk_amount"] = risk_snapshot.get("risk_amount")
        event = LifecycleEvent(
            event_type="SIGNAL_REJECTED",
            source_type="enrichment",
            source_id=str(eid),
            payload_json=json.dumps(ev_payload),
            idempotency_key=f"signal_rejected:{eid}",
        )
        return SignalGateResult(
            trade_chain=None,
            lifecycle_events=[event],
            execution_commands=[],
            account_snapshot=None,
            market_snapshot=None,
            review_reason=reason,
        )

    def _skip_signal(self, eid: int | None, reason: str) -> SignalGateResult:
        event = LifecycleEvent(
            event_type="SIGNAL_SKIPPED",
            source_type="enrichment",
            source_id=str(eid),
            payload_json=json.dumps({"reason": reason, "source": "runtime"}),
            idempotency_key=f"signal_skipped:{eid}",
        )
        return SignalGateResult(
            trade_chain=None,
            lifecycle_events=[event],
            execution_commands=[],
            account_snapshot=None,
            market_snapshot=None,
            review_reason=reason,
        )

    def _build_entry_commands(
        self,
        enriched: EnrichedCanonicalMessage,
        decision,
    ) -> list[ExecutionCommand]:
        signal = enriched.enriched_signal
        eid = enriched.enrichment_id

        sl_price = (
            signal.stop_loss.price.value
            if signal.stop_loss and signal.stop_loss.price else None
        )

        if not (self._simple_attached_enabled is True and sl_price is not None):
            return self._build_d_commands(
                signal, eid,
                float(decision.size_usdt or 0.0),
                float(decision.risk_snapshot.get("entry_price") or 0.0),
                int(decision.risk_snapshot.get("leverage") or 1),
                bool(decision.risk_snapshot.get("hedge_mode", False)),
                self.resolve_position_idx(signal.side, bool(decision.risk_snapshot.get("hedge_mode", False))),
                sl_price,
                len(signal.take_profits),
                self._get_close_pcts(enriched.management_plan or ManagementPlanConfig(), len(signal.take_profits)),
                decision.risk_snapshot.get("legs", []),
            )

        factory = EntryCommandFactory()
        return factory.build_entry_commands(
            enrichment_id=eid,
            symbol=signal.symbol,
            side=signal.side,
            entries=signal.entries,
            take_profits=signal.take_profits,
            sl_price=sl_price,
            leverage=int(decision.risk_snapshot.get("leverage") or 1),
            hedge_mode=bool(decision.risk_snapshot.get("hedge_mode", False)),
            position_idx=self.resolve_position_idx(
                signal.side, bool(decision.risk_snapshot.get("hedge_mode", False))
            ),
            risk_snapshot=decision.risk_snapshot,
        )

    def _build_d_commands(
        self, signal, eid, size_usdt, fallback_entry_price,
        leverage, hedge_mode, position_idx, sl_price,
        tp_count, close_pcts, legs_snap: list[dict],
    ) -> list[ExecutionCommand]:
        commands: list[ExecutionCommand] = []

        for leg in signal.entries:
            leg_snap = _find_leg_snap(legs_snap, leg.sequence)
            is_deferred = leg_snap is not None and leg_snap.get("qty_mode") == "deferred_market"

            if is_deferred:
                entry_payload: dict = {

                    "symbol": signal.symbol,
                    "side": signal.side,
                    "entry_type": leg.entry_type,
                    "price": None,
                    "qty_mode": "deferred_market",
                    "risk_amount": leg_snap["risk_amount"],
                    "sl_price": sl_price,
                    "leverage": leverage,
                    "hedge_mode": hedge_mode,
                    "position_idx": position_idx,
                    "sequence": leg.sequence,
                }
            else:
                if leg_snap and leg_snap.get("qty") is not None:
                    leg_qty = float(leg_snap["qty"])
                else:
                    leg_price = leg.price.value if leg.price else fallback_entry_price
                    leg_notional = size_usdt * float(leg.weight or 0.0)
                    leg_qty = self._qty_from_notional(leg_notional, leg_price)
                entry_payload = {

                    "symbol": signal.symbol,
                    "side": signal.side,
                    "entry_type": leg.entry_type,
                    "price": leg.price.value if leg.entry_type == "LIMIT" else None,
                    "qty": leg_qty,
                    "leverage": leverage,
                    "hedge_mode": hedge_mode,
                    "position_idx": position_idx,
                    "sequence": leg.sequence,
                }
            commands.append(ExecutionCommand(
                trade_chain_id=0,
                command_type="PLACE_ENTRY",
                status="PENDING",
                payload_json=json.dumps(entry_payload),
                idempotency_key=f"place_entry:{eid}:leg{leg.sequence}",
            ))

        if tp_count == 0:
            return commands

        total_qty = self._qty_from_notional(size_usdt, fallback_entry_price)

        if tp_count == 1:
            tp = signal.take_profits[0]
            tp_price = tp.price.value if tp.price else None
            commands.append(ExecutionCommand(
                trade_chain_id=0,
                command_type="SET_POSITION_TPSL_FULL",
                status="WAITING_POSITION",
                payload_json=json.dumps({

                    "symbol": signal.symbol,
                    "side": signal.side,
                    "leverage": leverage,
                    "hedge_mode": hedge_mode,
                    "position_idx": position_idx,
                    "take_profit": tp_price,
                    "stop_loss": sl_price,
                    "tp_trigger_by": "MarkPrice",
                    "sl_trigger_by": "MarkPrice",
                }),
                idempotency_key=f"set_tpsl_full:{eid}",
            ))
        else:
            allocated_qty = 0.0
            for i, tp in enumerate(signal.take_profits):
                is_last = (i == len(signal.take_profits) - 1)
                tp_price = tp.price.value if tp.price else None
                close_pct = close_pcts[i] if i < len(close_pcts) else (100.0 / tp_count)
                if is_last:
                    tp_qty = max(0.0, total_qty - allocated_qty)
                else:
                    tp_qty = round(total_qty * close_pct / 100.0, 8)
                    allocated_qty += tp_qty

                commands.append(ExecutionCommand(
                    trade_chain_id=0,
                    command_type="SET_POSITION_TPSL_PARTIAL",
                    status="WAITING_POSITION",
                    payload_json=json.dumps({
    
                        "symbol": signal.symbol,
                        "side": signal.side,
                        "position_idx": position_idx,
                        "tp_sequence": tp.sequence,
                        "take_profit": tp_price,
                        "stop_loss": sl_price,
                        "tp_size": tp_qty,
                        "sl_size": tp_qty,
                        "tp_order_type": "Limit",
                        "tp_limit_price": tp_price,
                        "tp_trigger_by": "MarkPrice",
                        "sl_trigger_by": "MarkPrice",
                    }),
                    idempotency_key=f"set_tpsl_partial:{eid}:tp{tp.sequence}",
                ))

        return commands

    @staticmethod
    def resolve_position_idx(side: str, hedge_mode: bool) -> int:
        if not hedge_mode:
            return 0
        return 1 if side == "LONG" else 2

    @staticmethod
    def _qty_from_notional(notional: float, price: float) -> float:
        if notional <= 0 or price <= 0:
            return 0.0
        return notional / price

    @staticmethod
    def _get_close_pcts(management_plan: ManagementPlanConfig, tp_count: int) -> list[float]:
        if tp_count == 0:
            return []
        dist = management_plan.close_distribution
        if dist.mode == "table" and tp_count in dist.table:
            return [float(p) for p in dist.table[tp_count]]
        pct = 100.0 / tp_count
        return [pct] * tp_count

    # ── UPDATE ────────────────────────────────────────────────────────────────

    def process_update(
        self,
        enriched: EnrichedCanonicalMessage,
        open_chains: list[TradeChain],
        active_commands_by_chain: dict[int, list[ExecutionCommand]],
        *,
        tg_id_to_raw_id: dict[int, int] | None = None,
    ) -> UpdateGateResult:
        tags = enriched.enriched_actions or []
        if not tags:
            event = self._make_review_event_no_chain(enriched, "no_actionable_targets")
            return UpdateGateResult(chain_results=[], review_events=[event])

        chain_results: list[UpdateChainResult] = []
        review_events: list[LifecycleEvent] = []

        for tag in tags:
            matched = self._resolve_targets(enriched, open_chains, tag, tg_id_to_raw_id=tg_id_to_raw_id)

            if matched is None:
                review_events.append(
                    self._make_review_event_no_chain(enriched, "ambiguous_update_target")
                )
                continue
            if len(matched) == 0:
                review_events.append(
                    self._make_review_event_no_chain(enriched, "no_update_target")
                )
                continue

            for chain in matched:
                chain_cmds = active_commands_by_chain.get(chain.trade_chain_id or 0, [])
                for action in tag.actions:
                    chain_results.append(
                        self._apply_action_to_chain(enriched, chain, action, chain_cmds)
                    )

        return UpdateGateResult(chain_results=chain_results, review_events=review_events)

    def _resolve_targets(
        self,
        enriched: EnrichedCanonicalMessage,
        open_chains: list[TradeChain],
        tag,
        *,
        tg_id_to_raw_id: dict[int, int] | None = None,
    ) -> list[TradeChain] | None:
        scope = tag.targeting.scope_hint
        trader_chains = [c for c in open_chains if c.trader_id == enriched.trader_id]

        if scope == "ALL_SHORT":
            return [c for c in trader_chains if c.side == "SHORT"]
        if scope == "ALL_LONG":
            return [c for c in trader_chains if c.side == "LONG"]
        if scope in GLOBAL_SCOPES:
            return trader_chains

        if scope == "SYMBOL":
            symbols = tag.targeting.symbols
            return [c for c in trader_chains if c.symbol in symbols] if symbols else []

        # SINGLE_SIGNAL or UNKNOWN — try symbol matching then explicit_ids then telegram IDs
        if (
            tag.targeting.symbols
            and not tag.targeting.explicit_ids
            and not tag.targeting.telegram_message_ids
            and tag.targeting.reply_to_message_id is None
        ):
            matched = [c for c in trader_chains if c.symbol in tag.targeting.symbols]
            if len(matched) == 1:
                return matched
            if len(matched) > 1:
                return None

        if tag.targeting.explicit_ids:
            matched = [
                c for c in trader_chains
                if _split_external_signal_ids(c.external_signal_id) & set(_normalize_signal_ids(tag.targeting.explicit_ids))
            ]
            if len(matched) == 1:
                return matched
            if len(matched) > 1:
                return None

        tg_ids_to_check = list(tag.targeting.telegram_message_ids)
        if tag.targeting.reply_to_message_id is not None:
            tg_ids_to_check.append(tag.targeting.reply_to_message_id)
        if tg_ids_to_check and tg_id_to_raw_id:
            raw_ids = {
                tg_id_to_raw_id[tid]
                for tid in tg_ids_to_check
                if tid in tg_id_to_raw_id
            }
            if raw_ids:
                matched = [c for c in trader_chains if c.raw_message_id in raw_ids]
                return matched  # [] if no chain matched — do NOT fall through to single-chain

        if len(trader_chains) > 1:
            return None
        return trader_chains

    def _apply_action_to_chain(
        self,
        enriched: EnrichedCanonicalMessage,
        chain: TradeChain,
        action,
        active_commands: list[ExecutionCommand],
    ) -> UpdateChainResult:
        action_type = action.action_type
        if action_type == "SET_STOP":
            op = action.set_stop
            if op and op.target_type == "ENTRY":
                return self._apply_move_to_be(enriched, chain, active_commands)
            if op and op.target_type == "PRICE" and op.price is not None:
                return self._apply_move_stop_price(enriched, chain, op.price.value, active_commands=active_commands)
            if op and op.target_type == "TP_LEVEL" and op.tp_level is not None:
                tp_price = self._resolve_tp_level_price(chain, op.tp_level)
                if tp_price is None:
                    return self._review_chain(enriched, chain, f"tp_level_price_not_found:{op.tp_level}")
                return self._apply_move_stop_price(enriched, chain, tp_price, active_commands=active_commands, reference=f"TP_{op.tp_level}")
            return self._review_chain(enriched, chain, "unsupported_set_stop_target_type")

        if action_type == "CLOSE":
            op = action.close
            if op and op.close_scope == "FULL":
                return self._apply_close_full(enriched, chain)
            if op and op.close_scope == "PARTIAL":
                return self._apply_close_partial(enriched, chain, op)
            return self._review_chain(enriched, chain, "unknown_close_scope")

        if action_type == "CANCEL_PENDING":
            return self._apply_cancel_pending(enriched, chain)

        if action_type == "MODIFY_ENTRIES":
            op = action.modify_entries
            if op and self._is_market_entry_now_convert(op):
                return self._apply_market_entry_now(enriched, chain, active_commands)
            if op and op.kind in {"MARKET_NOW", "UPDATE_PRICE", "REPLACE_ENTRY"}:
                return self._apply_modify_entries(enriched, chain, action, active_commands)
            return self._review_chain(enriched, chain, "unsupported_modify_entries_kind")

        return self._review_chain(enriched, chain, f"unsupported_action_type:{action_type}")

    def _apply_modify_entries(
        self,
        enriched: EnrichedCanonicalMessage,
        chain: TradeChain,
        action,
        active_commands: list[ExecutionCommand],
    ) -> UpdateChainResult:
        from src.runtime_v2.lifecycle.diff_engine import ExecutionPlanDiffEngine

        chain_id = chain.trade_chain_id
        cmid = enriched.canonical_message_id
        changed_entries: list[dict] = []

        try:
            risk_snap = json.loads(chain.risk_snapshot_json or "{}")
        except Exception:
            return self._review_chain(enriched, chain, "modify_entries_invalid_risk_snapshot")

        sl_price_raw = risk_snap.get("sl_price", chain.expected_stop_price)
        if sl_price_raw is None:
            return self._review_chain(enriched, chain, "modify_entries_missing_stop_loss")
        sl_price = float(sl_price_raw)
        risk_total = float(risk_snap.get("risk_amount", 0.0) or 0.0)
        risk_remaining = (
            chain.risk_remaining
            if chain.risk_remaining > 0
            else max(0.0, risk_total - chain.risk_already_realized)
        )
        current_market_price = risk_snap.get("entry_price")

        try:
            current_plan = json.loads(chain.plan_state_json or "{}")
            current_legs_by_sequence = {
                int(leg["sequence"]): leg
                for leg in current_plan.get("legs", [])
                if leg.get("sequence") is not None
            }
            target_plan_json = self._build_target_plan_from_modify_entries(chain, action)
            diff_actions = ExecutionPlanDiffEngine().diff(
                chain.plan_state_json,
                target_plan_json,
                risk_remaining=risk_remaining,
                sl_price=sl_price,
                current_market_price=float(current_market_price) if current_market_price else None,
            )
        except ValueError as exc:
            return self._review_chain(enriched, chain, f"modify_entries_diff_error:{exc}")
        except Exception:
            return self._review_chain(enriched, chain, "modify_entries_plan_build_failed")

        commands: list[ExecutionCommand] = []
        for diff_action in diff_actions:
            if diff_action["action"] == "cancel_pending_entry":
                commands.append(ExecutionCommand(
                    trade_chain_id=chain_id,
                    command_type="CANCEL_PENDING_ENTRY",
                    payload_json=json.dumps({
                        "symbol": chain.symbol,
                        "side": chain.side,
                        "entry_client_order_id": diff_action.get("client_order_id"),
                    }),
                    idempotency_key=(
                        f"cancel_entry:{chain_id}:{cmid}:seq{diff_action['sequence']}"
                    ),
                ))
            elif diff_action["action"] == "replace_entry_leg":
                try:
                    commands.extend(self._build_replacement_entry_commands(
                        enriched=enriched,
                        chain=chain,
                        risk_snapshot=risk_snap,
                        sl_price=sl_price,
                        diff_action=diff_action,
                    ))
                except Exception:
                    return self._review_chain(enriched, chain, "modify_entries_cmd_build_failed")
                # changed_entries is intentionally derived from the replace_entry_leg path:
                # in the current diff engine contract, that opcode is the concrete signal
                # that a pending entry price was actually changed and replaced.
                current_leg = current_legs_by_sequence.get(diff_action["sequence"], {})
                old_price = current_leg.get("price")
                new_price = diff_action.get("new_price")
                if old_price is not None and new_price is not None and old_price != new_price:
                    changed_entries.append({
                        "sequence": diff_action["sequence"],
                        "old_price": old_price,
                        "new_price": new_price,
                    })

        event = LifecycleEvent(
            trade_chain_id=chain_id,
            event_type="TELEGRAM_UPDATE_ACCEPTED",
            source_type="telegram_update",
            source_id=str(cmid),
            payload_json=json.dumps({
                "action": "MODIFY_ENTRIES",
                "changed_entries": changed_entries,
            }),
            idempotency_key=f"update_modify_entries:{chain_id}:{cmid}",
        )
        return UpdateChainResult(
            trade_chain_id=chain_id,
            new_lifecycle_state=None,
            new_be_protection_status=None,
            lifecycle_events=[event],
            execution_commands=commands,
        )

    def _apply_market_entry_now(
        self,
        enriched: EnrichedCanonicalMessage,
        chain: TradeChain,
        active_commands: list[ExecutionCommand],
    ) -> UpdateChainResult:
        from src.runtime_v2.lifecycle.entry_command_factory import EntryCommandFactory
        from src.runtime_v2.signal_enrichment.models import EnrichedEntryLeg, ManagementPlanConfig
        from src.parser_v2.contracts.entities import Price as _Price, TakeProfit as _TakeProfit

        chain_id = chain.trade_chain_id
        cmid = enriched.canonical_message_id

        try:
            plan = json.loads(chain.plan_state_json or "{}")
            risk_snap = json.loads(chain.risk_snapshot_json or "{}")
        except Exception:
            return self._review_chain(enriched, chain, "market_entry_now_invalid_json")

        pending_legs = [l for l in plan.get("legs", []) if l.get("status") == "PENDING"]
        if not pending_legs:
            return self._review_chain(enriched, chain, "no_pending_legs_for_market_convert")

        try:
            mp = ManagementPlanConfig.model_validate_json(chain.management_plan_json)
        except Exception:
            mp = ManagementPlanConfig()
        mode = mp.market_convert_mode

        leg1 = min(pending_legs, key=lambda l: l["sequence"])
        others = [l for l in pending_legs if l["sequence"] != leg1["sequence"]]

        sl_price_raw = risk_snap.get("sl_price", chain.expected_stop_price)
        if sl_price_raw is None:
            return self._review_chain(enriched, chain, "market_entry_now_missing_sl_price")
        sl_price = float(sl_price_raw)

        risk_total = float(risk_snap.get("risk_amount", 0.0) or 0.0)
        risk_remaining = (
            chain.risk_remaining
            if chain.risk_remaining > 0
            else max(0.0, risk_total - chain.risk_already_realized)
        )

        if mode == "cancel_subsequent":
            risk_amount = risk_remaining
        else:
            leg1_snap = next(
                (s for s in risk_snap.get("legs", []) if s.get("sequence") == leg1["sequence"]),
                None,
            )
            if leg1_snap is None or leg1_snap.get("risk_amount") is None:
                return self._review_chain(
                    enriched, chain,
                    f"market_entry_now_missing_leg1_risk_snap:seq{leg1['sequence']}",
                )
            risk_amount = float(leg1_snap["risk_amount"])

        # Guard: if leg1's entry command is already settled, abort to avoid double fill
        leg1_idem = leg1.get("client_order_id")
        if leg1_idem:
            settled = [
                c for c in active_commands
                if c.idempotency_key == leg1_idem and c.status in ("DONE", "CANCELLED")
            ]
            if settled:
                return self._review_chain(
                    enriched, chain,
                    f"market_entry_now_leg1_already_settled:seq{leg1['sequence']}",
                )

        commands: list[ExecutionCommand] = []

        # Cancel existing leg1 LIMIT on exchange
        commands.append(ExecutionCommand(
            trade_chain_id=chain_id,
            command_type="CANCEL_PENDING_ENTRY",
            payload_json=json.dumps({
                "symbol": chain.symbol,
                "side": chain.side,
                "entry_client_order_id": leg1.get("client_order_id"),
                "cancel_origin": "engine_rule",
            }),
            idempotency_key=f"cancel_entry:{chain_id}:{cmid}:seq{leg1['sequence']}",
        ))

        # Build replacement MARKET command via EntryCommandFactory
        hedge_mode = bool(risk_snap.get("hedge_mode", False))
        leverage = int(risk_snap.get("leverage", 1) or 1)
        position_idx = self.resolve_position_idx(chain.side, hedge_mode)
        is_leg1_attached = leg1["sequence"] == 1

        final_tp_val = plan.get("final_tp")
        tp_list: list[_TakeProfit] = []
        if final_tp_val is not None and is_leg1_attached:
            tp_price = _Price(raw=str(final_tp_val), value=float(final_tp_val))
            tp_list = [_TakeProfit(sequence=1, price=tp_price)]

        replacement_leg = EnrichedEntryLeg(
            sequence=leg1["sequence"],
            entry_type="MARKET",
            price=None,
            weight=float(leg1.get("weight") or 1.0),
        )
        replacement_snap = {
            "sequence": leg1["sequence"],
            "qty": None,
            "qty_mode": "deferred_market",
            "risk_amount": risk_amount,
            "weight": float(leg1.get("weight") or 1.0),
        }
        try:
            market_commands = EntryCommandFactory().build_entry_commands(
                enrichment_id=cmid,
                symbol=chain.symbol,
                side=chain.side,
                entries=[replacement_leg],
                take_profits=tp_list,
                sl_price=sl_price,
                leverage=leverage,
                hedge_mode=hedge_mode,
                position_idx=position_idx,
                risk_snapshot={"legs": [replacement_snap]},
            )
        except Exception as exc:
            return self._review_chain(enriched, chain, f"market_entry_now_factory_error:{exc}")
        commands.extend(market_commands)

        # Cancel subsequent legs (cancel mode only)
        if mode == "cancel_subsequent":
            for leg in others:
                commands.append(ExecutionCommand(
                    trade_chain_id=chain_id,
                    command_type="CANCEL_PENDING_ENTRY",
                    payload_json=json.dumps({
                        "symbol": chain.symbol,
                        "side": chain.side,
                        "entry_client_order_id": leg.get("client_order_id"),
                        "cancel_origin": "engine_rule",
                    }),
                    idempotency_key=f"cancel_entry:{chain_id}:{cmid}:seq{leg['sequence']}",
                ))

        # Build updated plan_state_json
        if is_leg1_attached:
            new_leg1_coid = f"place_entry_attached:{cmid}:leg{leg1['sequence']}"
        else:
            new_leg1_coid = f"place_entry:{cmid}:leg{leg1['sequence']}"

        other_seqs_to_cancel = {l["sequence"] for l in others} if mode == "cancel_subsequent" else set()
        updated_legs = []
        for leg in plan.get("legs", []):
            if leg["sequence"] == leg1["sequence"]:
                updated_legs.append({
                    **leg,
                    "entry_type": "MARKET",
                    "price": None,
                    "qty": None,
                    "qty_mode": "deferred_market",
                    "status": "PENDING",
                    "client_order_id": new_leg1_coid,
                })
            elif leg["sequence"] in other_seqs_to_cancel:
                updated_legs.append({**leg, "status": "CANCELLED"})
            else:
                updated_legs.append(leg)
        new_plan_state_json = json.dumps({**plan, "legs": updated_legs})

        changed_entries = [
            {
                "sequence": leg1["sequence"],
                "old_price": leg1.get("price"),
                "old_type": leg1.get("entry_type", "LIMIT"),
                "new_type": "MARKET",
            }
        ]
        if mode == "cancel_subsequent":
            for l in others:
                changed_entries.append({
                    "sequence": l["sequence"],
                    "old_price": l.get("price"),
                    "cancelled": True,
                })
        event = LifecycleEvent(
            trade_chain_id=chain_id,
            event_type="TELEGRAM_UPDATE_ACCEPTED",
            source_type="telegram_update",
            source_id=str(cmid),
            payload_json=json.dumps({
                "action": "MARKET_ENTRY_NOW",
                "mode": mode,
                "changed_entries": changed_entries,
            }),
            idempotency_key=f"update_market_entry_now:{chain_id}:{cmid}",
        )
        return UpdateChainResult(
            trade_chain_id=chain_id,
            new_lifecycle_state=None,
            new_be_protection_status=None,
            lifecycle_events=[event],
            execution_commands=commands,
            new_plan_state_json=new_plan_state_json,
        )

    @staticmethod
    def _is_market_entry_now_convert(op) -> bool:
        if op.kind != "MARKET_NOW":
            return False
        if not op.entries:
            return True
        if len(op.entries) != 1:
            return False
        leg = op.entries[0]
        return (
            leg.sequence == 1
            and leg.entry_type == "MARKET"
            and leg.price is None
        )

    def _build_target_plan_from_modify_entries(self, chain: TradeChain, action) -> str:
        plan = json.loads(chain.plan_state_json or "{}")
        op = action.modify_entries
        requested_by_seq = {
            leg.sequence: leg for leg in (op.entries if op else [])
        }
        target_legs: list[dict] = []
        for leg in plan.get("legs", []):
            requested = requested_by_seq.get(leg["sequence"])
            if requested is None:
                target_legs.append(leg)
                continue
            target_legs.append({
                **leg,
                "entry_type": requested.entry_type,
                "price": requested.price.value if requested.price else None,
                "qty": None if requested.entry_type == "MARKET" else leg.get("qty"),
                "qty_mode": (
                    "deferred_market"
                    if requested.entry_type == "MARKET"
                    else leg.get("qty_mode", "fixed")
                ),
            })
        return json.dumps({**plan, "legs": target_legs})

    def _build_replacement_entry_commands(
        self,
        *,
        enriched: EnrichedCanonicalMessage,
        chain: TradeChain,
        risk_snapshot: dict,
        sl_price: float,
        diff_action: dict,
    ) -> list[ExecutionCommand]:
        from src.parser_v2.contracts.entities import Price, TakeProfit
        from src.runtime_v2.signal_enrichment.models import EnrichedEntryLeg

        seq = int(diff_action["sequence"])
        new_type = diff_action["new_entry_type"]
        new_price = diff_action.get("new_price")
        new_qty = diff_action.get("new_qty")
        leg_snap = next(
            (leg for leg in risk_snapshot.get("legs", []) if leg.get("sequence") == seq),
            {},
        )
        replacement_snap = {
            **leg_snap,
            "sequence": seq,
            "qty": new_qty if new_qty is not None else leg_snap.get("qty"),
            "qty_mode": "fixed" if new_qty is not None else leg_snap.get("qty_mode", "fixed"),
        }
        price = Price(raw=str(new_price), value=float(new_price)) if new_price is not None else None
        replacement_leg = EnrichedEntryLeg(
            sequence=seq,
            entry_type=new_type,
            price=price,
            weight=float(leg_snap.get("weight", 1.0) or 1.0),
        )

        plan = json.loads(chain.plan_state_json or "{}")
        tp_list = []
        final_tp = plan.get("final_tp")
        if final_tp is not None:
            tp_price = Price(raw=str(final_tp), value=float(final_tp))
            tp_list = [TakeProfit(sequence=1, price=tp_price)]

        hedge_mode = bool(risk_snapshot.get("hedge_mode", False))
        return EntryCommandFactory().build_entry_commands(
            enrichment_id=enriched.canonical_message_id,
            symbol=chain.symbol,
            side=chain.side,
            entries=[replacement_leg],
            take_profits=tp_list,
            sl_price=sl_price,
            leverage=int(risk_snapshot.get("leverage", 1) or 1),
            hedge_mode=hedge_mode,
            position_idx=self.resolve_position_idx(chain.side, hedge_mode),
            risk_snapshot={"legs": [replacement_snap]},
        )

    def _apply_move_to_be(
        self,
        enriched: EnrichedCanonicalMessage,
        chain: TradeChain,
        active_commands: list[ExecutionCommand],
    ) -> UpdateChainResult:
        chain_id = chain.trade_chain_id
        cmid = enriched.canonical_message_id

        if chain.entry_avg_price is None:
            return UpdateChainResult(
                trade_chain_id=chain_id,
                new_lifecycle_state=None,
                new_be_protection_status=None,
                lifecycle_events=[LifecycleEvent(
                    trade_chain_id=chain_id,
                    event_type="NOOP_NOT_PENDING",
                    source_type="telegram_update",
                    source_id=str(cmid),
                    idempotency_key=f"noop_no_entry_for_be:{chain_id}:{cmid}",
                )],
                execution_commands=[],
            )

        if self._is_already_be(chain):
            return UpdateChainResult(
                trade_chain_id=chain_id,
                new_lifecycle_state=None,
                new_be_protection_status=None,
                lifecycle_events=[LifecycleEvent(
                    trade_chain_id=chain_id,
                    event_type="NOOP_ALREADY_PROTECTED_BE",
                    source_type="telegram_update",
                    source_id=str(cmid),
                    idempotency_key=f"noop_be:{chain_id}:{cmid}",
                )],
                execution_commands=[],
            )

        active_be = [
            c for c in active_commands
            if c.command_type == "MOVE_STOP_TO_BREAKEVEN" and c.status in ("PENDING", "SENT", "ACK")
        ]
        if active_be:
            return UpdateChainResult(
                trade_chain_id=chain_id,
                new_lifecycle_state=None,
                new_be_protection_status=None,
                lifecycle_events=[LifecycleEvent(
                    trade_chain_id=chain_id,
                    event_type="NOOP_DUPLICATE_COMMAND",
                    source_type="telegram_update",
                    source_id=str(cmid),
                    idempotency_key=f"noop_dup_be:{chain_id}:{cmid}",
                )],
                execution_commands=[],
            )

        try:
            mp = ManagementPlanConfig.model_validate_json(chain.management_plan_json)
        except Exception:
            mp = ManagementPlanConfig()
        extra = _be_move_extra(chain)
        new_stop_price = resolve_be_stop_price(
            chain,
            mp,
            protection_style=extra["protection_style"],
        )
        if new_stop_price is None:
            return self._review_chain(
                enriched,
                chain,
                "missing_entry_avg_price_for_be",
            )

        cmd = ExecutionCommand(
            trade_chain_id=chain_id,
            command_type="MOVE_STOP_TO_BREAKEVEN",
            payload_json=json.dumps({
                "symbol": chain.symbol, "side": chain.side,
                "new_stop_price": new_stop_price,
                "is_breakeven": True,
                **extra,
            }),
            idempotency_key=f"move_be:{chain_id}:{cmid}",
        )
        old_sl_price = chain.current_stop_price
        # Fallback covers chains created before current_stop_price was populated (schema migration)
        if old_sl_price is None:
            try:
                old_sl_price = float(json.loads(chain.risk_snapshot_json or "{}").get("sl_price") or 0) or None
            except Exception:
                pass
        event = LifecycleEvent(
            trade_chain_id=chain_id,
            event_type="TELEGRAM_UPDATE_ACCEPTED",
            source_type="telegram_update",
            source_id=str(cmid),
            payload_json=json.dumps({
                "action": "MOVE_SL_TO_BE",
                "old_sl_price": old_sl_price,
                "new_sl_price": new_stop_price,
                "is_breakeven": True,
            }),
            idempotency_key=f"update_be:{chain_id}:{cmid}",
        )
        return UpdateChainResult(
            trade_chain_id=chain_id,
            new_lifecycle_state=None,
            new_be_protection_status="BE_MOVE_PENDING",
            lifecycle_events=[event],
            execution_commands=[cmd],
        )

    def _resolve_tp_level_price(self, chain: "TradeChain", tp_level: int) -> float | None:
        try:
            plan = json.loads(chain.plan_state_json or "{}")
        except Exception:
            return None
        intermediate: list[float] = plan.get("intermediate_tps") or []
        final_tp: float | None = plan.get("final_tp")
        all_tps = intermediate + ([final_tp] if final_tp is not None else [])
        idx = tp_level - 1
        if 0 <= idx < len(all_tps):
            return all_tps[idx]
        return None

    def _apply_move_stop_price(
        self,
        enriched: "EnrichedCanonicalMessage",
        chain: "TradeChain",
        new_price: float,
        *,
        active_commands: list[ExecutionCommand],
        reference: str | None = None,
    ) -> "UpdateChainResult":
        chain_id = chain.trade_chain_id
        cmid = enriched.canonical_message_id

        if chain.lifecycle_state not in ("OPEN", "PARTIALLY_CLOSED"):
            return UpdateChainResult(
                trade_chain_id=chain_id,
                new_lifecycle_state=None,
                new_be_protection_status=None,
                lifecycle_events=[LifecycleEvent(
                    trade_chain_id=chain_id,
                    event_type="NOOP_NOT_PENDING",
                    source_type="telegram_update",
                    source_id=str(cmid),
                    idempotency_key=f"noop_ms_state:{chain_id}:{cmid}",
                )],
                execution_commands=[],
            )

        active_move = [
            c for c in active_commands
            if c.command_type == "MOVE_STOP" and c.status in ("PENDING", "SENT", "ACK")
        ]
        if active_move:
            return UpdateChainResult(
                trade_chain_id=chain_id,
                new_lifecycle_state=None,
                new_be_protection_status=None,
                lifecycle_events=[LifecycleEvent(
                    trade_chain_id=chain_id,
                    event_type="NOOP_DUPLICATE_COMMAND",
                    source_type="telegram_update",
                    source_id=str(cmid),
                    idempotency_key=f"noop_dup_ms:{chain_id}:{cmid}",
                )],
                execution_commands=[],
            )

        extra = _be_move_extra(chain)
        payload: dict = {
            "symbol": chain.symbol,
            "side": chain.side,
            "new_stop_price": new_price,
            "is_breakeven": False,
            **extra,
        }
        if reference is not None:
            payload["reference"] = reference

        cmd = ExecutionCommand(
            trade_chain_id=chain_id,
            command_type="MOVE_STOP",
            payload_json=json.dumps(payload),
            idempotency_key=f"move_stop:{chain_id}:{cmid}",
        )

        old_sl_price = chain.current_stop_price
        if old_sl_price is None:
            try:
                old_sl_price = float(json.loads(chain.risk_snapshot_json or "{}").get("sl_price") or 0) or None
            except Exception:
                pass

        event_payload: dict = {
            "action": "MOVE_STOP",
            "old_sl_price": old_sl_price,
            "new_sl_price": new_price,
        }
        if reference is not None:
            event_payload["reference"] = reference

        event = LifecycleEvent(
            trade_chain_id=chain_id,
            event_type="TELEGRAM_UPDATE_ACCEPTED",
            source_type="telegram_update",
            source_id=str(cmid),
            payload_json=json.dumps(event_payload),
            idempotency_key=f"update_ms:{chain_id}:{cmid}",
        )
        return UpdateChainResult(
            trade_chain_id=chain_id,
            new_lifecycle_state=None,
            new_be_protection_status=None,
            lifecycle_events=[event],
            execution_commands=[cmd],
        )

    def _apply_close_full(
        self, enriched: EnrichedCanonicalMessage, chain: TradeChain
    ) -> UpdateChainResult:
        chain_id = chain.trade_chain_id
        cmid = enriched.canonical_message_id
        state = chain.lifecycle_state

        if state in ("CLOSED", "CANCELLED", "EXPIRED"):
            return UpdateChainResult(
                trade_chain_id=chain_id,
                new_lifecycle_state=None,
                new_be_protection_status=None,
                lifecycle_events=[LifecycleEvent(
                    trade_chain_id=chain_id,
                    event_type="NOOP_ALREADY_CLOSED",
                    source_type="telegram_update",
                    source_id=str(cmid),
                    idempotency_key=f"noop_closed:{chain_id}:{cmid}",
                )],
                execution_commands=[],
            )

        if state == "WAITING_ENTRY":
            return self._apply_cancel_pending(enriched, chain)

        position_context = _position_context(chain)
        cmd = ExecutionCommand(
            trade_chain_id=chain_id,
            command_type="CLOSE_FULL",
            payload_json=json.dumps({
                "symbol": chain.symbol,
                "side": chain.side,
                "command_source": "trader_update",
                **position_context,
            }),
            idempotency_key=f"close_full:{chain_id}:{cmid}",
        )
        # Cancella anche eventuali ordini di entry pendenti associati alla chain.
        # _expand_cancel_pending_commands li espanderà one-per-client_order_id al momento del commit.
        # Se non ci sono entry pendenti, l'espansione restituisce il comando generico che
        # sull'exchange non trova nulla e termina come no-op (reduceOnly=True sul close
        # non è sufficiente a cancellarli).
        cancel_pending_cmd = ExecutionCommand(
            trade_chain_id=chain_id,
            command_type="CANCEL_PENDING_ENTRY",
            payload_json=json.dumps({"symbol": chain.symbol, "side": chain.side}),
            idempotency_key=f"cancel_pending_for_close:{chain_id}:{cmid}",
        )
        event = LifecycleEvent(
            trade_chain_id=chain_id,
            event_type="TELEGRAM_UPDATE_ACCEPTED",
            source_type="telegram_update",
            source_id=str(cmid),
            payload_json=json.dumps({"action": "CLOSE_FULL"}),
            idempotency_key=f"update_close_full:{chain_id}:{cmid}",
        )
        return UpdateChainResult(
            trade_chain_id=chain_id,
            new_lifecycle_state=None,
            new_be_protection_status=None,
            lifecycle_events=[event],
            execution_commands=[cmd, cancel_pending_cmd],
        )

    def _apply_close_partial(
        self, enriched: EnrichedCanonicalMessage, chain: TradeChain, op
    ) -> UpdateChainResult:
        chain_id = chain.trade_chain_id
        cmid = enriched.canonical_message_id
        fraction = op.fraction or 0.5
        position_context = _position_context(chain)
        cmd = ExecutionCommand(
            trade_chain_id=chain_id,
            command_type="CLOSE_PARTIAL",
            payload_json=json.dumps({
                "symbol": chain.symbol,
                "side": chain.side,
                "fraction": fraction,
                "command_source": "trader_update",
                **position_context,
            }),
            idempotency_key=f"close_partial:{chain_id}:{cmid}",
        )
        event = LifecycleEvent(
            trade_chain_id=chain_id,
            event_type="TELEGRAM_UPDATE_ACCEPTED",
            source_type="telegram_update",
            source_id=str(cmid),
            payload_json=json.dumps({
                "action": "CLOSE_PARTIAL",
                "fraction": fraction,
                "close_pct": round(fraction * 100, 2),
            }),
            idempotency_key=f"update_close_partial:{chain_id}:{cmid}",
        )
        return UpdateChainResult(
            trade_chain_id=chain_id,
            new_lifecycle_state=None,
            new_be_protection_status=None,
            lifecycle_events=[event],
            execution_commands=[cmd],
        )

    def _apply_cancel_pending(
        self, enriched: EnrichedCanonicalMessage, chain: TradeChain
    ) -> UpdateChainResult:
        chain_id = chain.trade_chain_id
        cmid = enriched.canonical_message_id
        state = chain.lifecycle_state

        if state not in ("WAITING_ENTRY", "OPEN", "PARTIALLY_CLOSED"):
            return UpdateChainResult(
                trade_chain_id=chain_id,
                new_lifecycle_state=None,
                new_be_protection_status=None,
                lifecycle_events=[LifecycleEvent(
                    trade_chain_id=chain_id,
                    event_type="NOOP_NOT_PENDING",
                    source_type="telegram_update",
                    source_id=str(cmid),
                    idempotency_key=f"noop_not_pending:{chain_id}:{cmid}",
                )],
                execution_commands=[],
            )

        commands = [ExecutionCommand(
            trade_chain_id=chain_id,
            command_type="CANCEL_PENDING_ENTRY",
            payload_json=json.dumps({
                "symbol": chain.symbol,
                "side": chain.side,
                "cancel_origin": "trader_update",
            }),
            idempotency_key=f"cancel_pending:{chain_id}:{cmid}",
        )]

        cancelled_entries: list[dict] = []
        try:
            plan_data = json.loads(chain.plan_state_json or "{}")
            cancelled_entries = [
                {
                    "sequence": leg.get("sequence"),
                    "price": leg.get("price"),
                    "entry_type": leg.get("entry_type", "LIMIT"),
                }
                for leg in plan_data.get("legs", [])
                if leg.get("status") == "PENDING"
            ]
        except Exception:
            pass

        event = LifecycleEvent(
            trade_chain_id=chain_id,
            event_type="TELEGRAM_UPDATE_ACCEPTED",
            source_type="telegram_update",
            source_id=str(cmid),
            payload_json=json.dumps({
                "action": "CANCEL_PENDING",
                "cancelled_entries": cancelled_entries,
            }),
            idempotency_key=f"update_cancel:{chain_id}:{cmid}",
        )

        if state == "WAITING_ENTRY":
            return UpdateChainResult(
                trade_chain_id=chain_id,
                new_lifecycle_state=None,
                new_be_protection_status=None,
                lifecycle_events=[event],
                execution_commands=commands,
            )

        # OPEN or PARTIALLY_CLOSED — position exists; cancel pending orders but keep chain alive.
        # Position-level SL covers the full position automatically — no qty sync needed.
        return UpdateChainResult(
            trade_chain_id=chain_id,
            new_lifecycle_state=None,
            new_be_protection_status=None,
            lifecycle_events=[event],
            execution_commands=commands,
        )

    @staticmethod
    def _is_already_be(chain: TradeChain) -> bool:
        if chain.be_protection_status == "PROTECTED":
            return True
        if chain.entry_avg_price is None or chain.current_stop_price is None:
            return False
        try:
            mp = ManagementPlanConfig.model_validate_json(chain.management_plan_json)
        except Exception:
            mp = ManagementPlanConfig()
        target_price = resolve_be_stop_price(
            chain,
            mp,
            protection_style=_be_move_extra(chain)["protection_style"],
        )
        if target_price is None:
            return False
        if chain.side == "LONG":
            return chain.current_stop_price >= target_price
        return chain.current_stop_price <= target_price

    def _review_chain(
        self, enriched: EnrichedCanonicalMessage, chain: TradeChain, reason: str
    ) -> UpdateChainResult:
        chain_id = chain.trade_chain_id
        cmid = enriched.canonical_message_id
        return UpdateChainResult(
            trade_chain_id=chain_id,
            new_lifecycle_state=None,
            new_be_protection_status=None,
            lifecycle_events=[LifecycleEvent(
                trade_chain_id=chain_id,
                event_type="REVIEW_REQUIRED",
                source_type="telegram_update",
                source_id=str(cmid),
                payload_json=json.dumps({"reason": reason}),
                idempotency_key=f"review_chain:{chain_id}:{cmid}:{reason}",
            )],
            execution_commands=[],
        )

    def _make_review_event_no_chain(
        self, enriched: EnrichedCanonicalMessage, reason: str
    ) -> LifecycleEvent:
        cmid = enriched.canonical_message_id
        return LifecycleEvent(
            event_type="REVIEW_REQUIRED",
            source_type="telegram_update",
            source_id=str(cmid),
            payload_json=json.dumps({"reason": reason}),
            idempotency_key=f"review_update:{cmid}:{reason}",
        )


import sqlite3 as _sqlite3

_NO_CHAIN_LOGGABLE_EVENTS = frozenset({"SIGNAL_REJECTED"})


def _write_no_chain_signal_clean_log(
    conn: _sqlite3.Connection,
    enriched: "EnrichedCanonicalMessage",
    lifecycle_events: "list[LifecycleEvent]",
    *,
    src_chat_id: str | None,
    tg_msg_id: int | None,
) -> None:
    """Scrive CLEAN_LOG per segnali che non hanno creato una chain (review/rejected).

    project_clean_log_for_chain richiede un chain_id valido; qui chain_id è None
    quindi la proiezione deve avvenire direttamente.
    """
    signal = enriched.enriched_signal
    link = (
        f"https://t.me/c/{str(src_chat_id).removeprefix('-100')}/{tg_msg_id}"
        if src_chat_id and tg_msg_id else None
    )
    entries_payload = [
        {
            "sequence": leg.sequence,
            "entry_type": str(leg.entry_type),
            "price": leg.price.value if leg.price else None,
        }
        for leg in (signal.entries or [])
    ] if signal else []
    sl_payload = (
        signal.stop_loss.price.value
        if signal and signal.stop_loss and signal.stop_loss.price else None
    )
    tps_payload = [
        tp.price.value
        for tp in (signal.take_profits or [])
        if tp.price is not None
    ] if signal else []
    for event in lifecycle_events:
        if event.event_type not in _NO_CHAIN_LOGGABLE_EVENTS:
            continue
        try:
            ev_data = json.loads(event.payload_json or "{}")
        except Exception:
            ev_data = {}
        risk_pct = None
        if ev_data.get("capital") and ev_data.get("risk_amount"):
            risk_pct = round(ev_data["risk_amount"] / ev_data["capital"] * 100, 2)
        payload = {
            "chain_id": None,
            "symbol": signal.symbol if signal else None,
            "side": str(signal.side) if signal and signal.side else None,
            "trader_id": enriched.trader_id,
            "account_id": enriched.account_id,
            "reason": ev_data.get("reason", "unknown"),
            "entries": entries_payload,
            "sl": sl_payload,
            "tps": tps_payload,
            "risk_pct": risk_pct,
            "source": ev_data.get("source", "runtime"),
            "link": link,
        }
        write_clean_log_event(
            conn,
            notification_type=event.event_type,
            chain_id=None,
            payload=payload,
            dedupe_key=f"clean:{event.idempotency_key}",
        )


_ENTRY_HISTORY_EVENT_TYPES = frozenset({"ENTRY_FILLED"})
_EXIT_HISTORY_EVENT_TYPES = frozenset({
    "TP_FILLED", "SL_FILLED", "CLOSE_PARTIAL_FILLED", "CLOSE_FULL_FILLED",
})


class LifecycleGateWorker:
    def __init__(
        self,
        parser_db_path: str,
        ops_db_path: str,
        gate: LifecycleEntryGate,
        chain_repo,
        event_repo,
        command_repo,
        snapshot_repo,
        control_repo,
        channel_resolver=None,
    ) -> None:
        self._parser_db = parser_db_path
        self._ops_db = ops_db_path
        self._gate = gate
        self._chain_repo = chain_repo
        self._event_repo = event_repo
        self._command_repo = command_repo
        self._snapshot_repo = snapshot_repo
        self._control_repo = control_repo
        self._channel_resolver = channel_resolver

    def run_once(self, batch_size: int = 50) -> int:
        rows = self._fetch_pending(batch_size)
        processed = 0
        for row in rows:
            try:
                self._process_row(row)
                processed += 1
            except Exception:
                logger.exception("error processing enrichment_id=%s", row[0])
        return processed

    def _fetch_pending(self, limit: int) -> list[tuple]:
        conn = _sqlite3.connect(self._parser_db)
        try:
            return conn.execute(
                """
                SELECT enrichment_id, canonical_message_id, raw_message_id, trader_id, account_id,
                       primary_class, enrichment_decision, enriched_signal_json,
                       enriched_actions_json, management_plan_json, policy_snapshot_json
                FROM enriched_canonical_messages
                WHERE lifecycle_processed=0
                  AND enrichment_decision='PASS'
                  AND primary_class IN ('SIGNAL','UPDATE')
                ORDER BY created_at
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        finally:
            conn.close()

    def _build_tg_id_to_raw_id(self, enriched_actions) -> dict[int, int]:
        all_tg_ids: set[int] = set()
        for tag in (enriched_actions or []):
            all_tg_ids.update(tag.targeting.telegram_message_ids)
            if tag.targeting.reply_to_message_id is not None:
                all_tg_ids.add(tag.targeting.reply_to_message_id)
        if not all_tg_ids:
            return {}
        placeholders = ",".join("?" for _ in all_tg_ids)
        conn = _sqlite3.connect(self._parser_db)
        try:
            rows = conn.execute(
                f"SELECT telegram_message_id, raw_message_id FROM raw_messages "
                f"WHERE telegram_message_id IN ({placeholders})",
                list(all_tg_ids),
            ).fetchall()
        finally:
            conn.close()
        return {int(r[0]): int(r[1]) for r in rows}

    def _rehydrate_active_chains(self, chains: list[TradeChain]) -> list[TradeChain]:
        if not chains:
            return chains
        conn = _sqlite3.connect(self._ops_db)
        try:
            return [self._rehydrate_chain_from_history(conn, chain) for chain in chains]
        finally:
            conn.close()

    def _rehydrate_chain_from_history(
        self,
        conn: _sqlite3.Connection,
        chain: TradeChain,
    ) -> TradeChain:
        chain_id = chain.trade_chain_id
        if chain_id is None:
            return chain

        rows = conn.execute(
            """
            SELECT event_type, payload_json
            FROM ops_exchange_events
            WHERE trade_chain_id=?
              AND processing_status='DONE'
            ORDER BY received_at, exchange_event_id
            """,
            (chain_id,),
        ).fetchall()
        if not rows:
            return chain

        entry_notional = 0.0
        filled_entry_qty = 0.0
        closed_position_qty = 0.0
        for event_type, payload_json in rows:
            try:
                payload = json.loads(payload_json or "{}")
            except Exception:
                continue
            try:
                qty = float(payload.get("filled_qty") or 0.0)
            except (TypeError, ValueError):
                continue
            if qty <= 0:
                continue
            if event_type in _ENTRY_HISTORY_EVENT_TYPES:
                try:
                    fill_price = float(payload.get("fill_price") or 0.0)
                except (TypeError, ValueError):
                    fill_price = 0.0
                entry_notional += fill_price * qty
                filled_entry_qty += qty
            elif event_type in _EXIT_HISTORY_EVENT_TYPES:
                closed_position_qty += qty

        if filled_entry_qty <= 0:
            return chain

        derived_entry_avg_price = entry_notional / filled_entry_qty if entry_notional > 0 else None
        derived_open_position_qty = max(0.0, filled_entry_qty - closed_position_qty)
        needs_rehydrate = (
            chain.entry_avg_price is None
            or chain.filled_entry_qty <= 0
            or (derived_open_position_qty > 0 and chain.open_position_qty <= 0)
        )
        if not needs_rehydrate:
            return chain

        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            UPDATE ops_trade_chains
            SET entry_avg_price=?,
                filled_entry_qty=?,
                open_position_qty=?,
                closed_position_qty=?,
                updated_at=?
            WHERE trade_chain_id=?
            """,
            (
                derived_entry_avg_price,
                filled_entry_qty,
                derived_open_position_qty,
                closed_position_qty,
                now,
                chain_id,
            ),
        )
        conn.commit()
        return chain.model_copy(update={
            "entry_avg_price": derived_entry_avg_price,
            "filled_entry_qty": filled_entry_qty,
            "open_position_qty": derived_open_position_qty,
            "closed_position_qty": closed_position_qty,
            "updated_at": datetime.fromisoformat(now),
        })

    def _process_row(self, row: tuple) -> None:
        import json as _json
        from src.runtime_v2.signal_enrichment.models import (
            EnrichedCanonicalMessage, EnrichedSignalPayload, ManagementPlanConfig,
        )
        from src.parser_v2.contracts.canonical_message import TargetActionGroup

        (
            enrichment_id, canonical_message_id, raw_message_id, trader_id, account_id,
            primary_class, enrichment_decision, enriched_signal_json,
            enriched_actions_json, management_plan_json, policy_snapshot_json,
        ) = row

        enriched_signal = (
            EnrichedSignalPayload.model_validate_json(enriched_signal_json)
            if enriched_signal_json else None
        )
        enriched_actions = None
        if enriched_actions_json:
            enriched_actions = [
                TargetActionGroup.model_validate(a)
                for a in _json.loads(enriched_actions_json)
            ]
        management_plan = (
            ManagementPlanConfig.model_validate_json(management_plan_json)
            if management_plan_json else ManagementPlanConfig()
        )

        enriched = EnrichedCanonicalMessage(
            enrichment_id=enrichment_id,
            canonical_message_id=canonical_message_id,
            raw_message_id=raw_message_id,
            trader_id=trader_id,
            account_id=account_id,
            primary_class=primary_class,
            enrichment_decision=enrichment_decision,
            enriched_signal=enriched_signal,
            enriched_actions=enriched_actions,
            management_plan=management_plan,
            policy_snapshot=_json.loads(policy_snapshot_json or "{}"),
        )

        open_chains = self._rehydrate_active_chains(
            self._chain_repo.get_active_by_trader(trader_id)
        )
        symbol = enriched_signal.symbol or "" if enriched_signal else ""
        side = enriched_signal.side or "" if enriched_signal else ""
        control_mode = self._control_repo.get_effective_mode(account_id, trader_id, symbol, side)

        if primary_class == "SIGNAL":
            admission = self._build_signal_admission_context(raw_message_id)
            result = self._gate.process_signal(enriched, open_chains, control_mode, admission)
            self._persist_signal(enriched, result)
        else:
            active_cmds = {
                c.trade_chain_id: self._command_repo.get_active_for_chain(c.trade_chain_id)
                for c in open_chains
            }
            tg_id_to_raw_id = self._build_tg_id_to_raw_id(enriched.enriched_actions)
            result = self._gate.process_update(
                enriched, open_chains, active_cmds,
                tg_id_to_raw_id=tg_id_to_raw_id,
            )
            self._persist_update(enriched, result)

    def _build_signal_admission_context(self, raw_message_id: int) -> SignalAdmissionContext:
        conn = _sqlite3.connect(self._parser_db)
        try:
            row = conn.execute(
                "SELECT source_chat_id, source_topic_id, message_presentation_type "
                "FROM raw_messages WHERE raw_message_id=?",
                (raw_message_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return SignalAdmissionContext()
        message_presentation_type = str(row[2] or "PLAIN")
        if self._channel_resolver is None:
            return SignalAdmissionContext(message_presentation_type=message_presentation_type)
        entry = self._channel_resolver.lookup(
            str(row[0]),
            int(row[1]) if row[1] is not None else None,
        )
        return SignalAdmissionContext(
            signal_message_type=(entry.signal_message_type if entry is not None else "any"),
            message_presentation_type=message_presentation_type,
        )

    def _persist_signal(self, enriched: EnrichedCanonicalMessage, result: SignalGateResult) -> None:
        now = datetime.now(timezone.utc).isoformat()
        # Lookup source_chat_id and telegram_message_id from parser DB.
        src_chat_id: str | None = None
        tg_msg_id: int | None = None
        parse_status: str | None = None
        parse_warnings: list[str] = []
        external_signal_id: str | None = None
        try:
            pconn = _sqlite3.connect(self._parser_db)
            try:
                rm_row = pconn.execute(
                    "SELECT source_chat_id, telegram_message_id FROM raw_messages WHERE raw_message_id=?",
                    (enriched.raw_message_id,),
                ).fetchone()
                if rm_row:
                    src_chat_id, tg_msg_id = rm_row[0], rm_row[1]
                cm_row = pconn.execute(
                    "SELECT parse_status, warnings_json, diagnostics_json FROM canonical_messages WHERE canonical_message_id=?",
                    (enriched.canonical_message_id,),
                ).fetchone()
                if cm_row:
                    parse_status = cm_row[0]
                    if parse_status == "PARTIAL":
                        try:
                            parse_warnings = json.loads(cm_row[1] or "[]") or []
                        except Exception:
                            parse_warnings = []
                    try:
                        diagnostics = json.loads(cm_row[2] or "{}") or {}
                    except Exception:
                        diagnostics = {}
                    explicit_ids = _normalize_signal_ids(diagnostics.get("signal_explicit_ids"))
                    if explicit_ids:
                        external_signal_id = "|".join(explicit_ids)
            finally:
                pconn.close()
        except Exception:
            pass  # non-fatal: link will be absent

        conn = _sqlite3.connect(self._ops_db)
        try:
            with conn:
                chain_id = None
                if result.trade_chain is not None:
                    c = result.trade_chain
                    initial_risk_amount = None
                    try:
                        risk_snapshot = json.loads(c.risk_snapshot_json or "{}")
                        raw_risk = risk_snapshot.get("risk_amount")
                        if raw_risk is not None:
                            initial_risk_amount = float(raw_risk)
                    except Exception:
                        initial_risk_amount = None
                    cursor = conn.execute(
                        """
                        INSERT OR IGNORE INTO ops_trade_chains (
                            source_enrichment_id, canonical_message_id, raw_message_id,
                            trader_id, account_id, symbol, side, lifecycle_state, entry_mode,
                            entry_avg_price, current_stop_price, expected_stop_price,
                            be_protection_status, entry_timeout_at, management_plan_json,
                            risk_snapshot_json, planned_entry_qty, filled_entry_qty,
                            open_position_qty, closed_position_qty, last_position_sync_at,
                            execution_mode, risk_already_realized, risk_remaining,
                            plan_state_json, source_chat_id, telegram_message_id,
                            external_signal_id, initial_risk_amount, peak_margin_used,
                            created_at, updated_at
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            c.source_enrichment_id, c.canonical_message_id, c.raw_message_id,
                            c.trader_id, c.account_id, c.symbol, c.side,
                            c.lifecycle_state, c.entry_mode,
                            c.entry_avg_price, c.current_stop_price, c.expected_stop_price,
                            c.be_protection_status,
                            c.entry_timeout_at.isoformat() if c.entry_timeout_at else None,
                            c.management_plan_json, c.risk_snapshot_json,
                            c.planned_entry_qty, c.filled_entry_qty,
                            c.open_position_qty, c.closed_position_qty,
                            c.last_position_sync_at.isoformat() if c.last_position_sync_at else None,
                            c.execution_mode, c.risk_already_realized, c.risk_remaining,
                            c.plan_state_json, src_chat_id, tg_msg_id,
                            external_signal_id, initial_risk_amount, None,
                            now, now,
                        ),
                    )
                    if cursor.lastrowid and cursor.rowcount > 0:
                        chain_id = cursor.lastrowid
                    else:
                        row = conn.execute(
                            "SELECT trade_chain_id FROM ops_trade_chains WHERE source_enrichment_id=?",
                            (c.source_enrichment_id,),
                        ).fetchone()
                        chain_id = row[0] if row else None

                for event in result.lifecycle_events:
                    event_payload_json = event.payload_json
                    if (
                        event.event_type == "SIGNAL_ACCEPTED"
                        and parse_status == "PARTIAL"
                    ):
                        partial_payload: dict = {"parse_status": parse_status}
                        if parse_warnings:
                            partial_payload["parse_warnings"] = parse_warnings
                        event_payload_json = json.dumps(partial_payload)
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO ops_lifecycle_events (
                            trade_chain_id, event_type, source_type, source_id,
                            previous_state, next_state, payload_json, idempotency_key, created_at
                        ) VALUES (?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            chain_id, event.event_type, event.source_type, event.source_id,
                            event.previous_state, event.next_state, event_payload_json,
                            event.idempotency_key, now,
                        ),
                    )

                for cmd in result.execution_commands:
                    for payload_json_c, idempotency_key_c in _expand_cancel_pending_commands(
                        conn,
                        trade_chain_id=chain_id,
                        command_type=cmd.command_type,
                        payload_json=cmd.payload_json,
                        idempotency_key=cmd.idempotency_key,
                    ):
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO ops_execution_commands (
                                trade_chain_id, command_type, status, payload_json,
                                idempotency_key, created_at, updated_at
                            ) VALUES (?,?,?,?,?,?,?)
                            """,
                            (
                                chain_id, cmd.command_type, cmd.status, payload_json_c,
                                idempotency_key_c, now, now,
                            ),
                        )

                if result.account_snapshot:
                    s = result.account_snapshot
                    conn.execute(
                        """
                        INSERT INTO ops_account_snapshots (
                            account_id, equity_usdt, available_balance_usdt,
                            total_open_risk_usdt, total_margin_used_usdt,
                            account_unrealized_pnl_usdt, source, captured_at,
                            payload_json, snapshot_status, error_code
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            enriched.account_id, s.equity_usdt, s.available_balance_usdt,
                            s.total_open_risk_usdt, s.total_margin_used_usdt,
                            s.account_unrealized_pnl_usdt,
                            s.source, s.captured_at.isoformat(), s.payload_json,
                            s.snapshot_status, s.error_code,
                        ),
                    )

                if result.market_snapshot:
                    s = result.market_snapshot
                    conn.execute(
                        """
                        INSERT INTO ops_market_snapshots (
                            account_id, symbol, mark_price, bid, ask, min_order_size,
                            price_precision, qty_precision, source, captured_at, payload_json
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            enriched.account_id, s.symbol, s.mark_price, s.bid, s.ask,
                            s.min_order_size, s.price_precision, s.qty_precision,
                            s.source, s.captured_at.isoformat(), s.payload_json,
                        ),
                    )
                if chain_id is not None:
                    try:
                        project_clean_log_for_chain(conn, chain_id)
                    except Exception:
                        logger.exception("clean_log projection failed for chain %s", chain_id)
                else:
                    try:
                        _write_no_chain_signal_clean_log(
                            conn, enriched, result.lifecycle_events,
                            src_chat_id=src_chat_id, tg_msg_id=tg_msg_id,
                        )
                    except Exception:
                        logger.exception(
                            "no-chain clean_log failed for enrichment_id=%s",
                            enriched.enrichment_id,
                        )
        finally:
            conn.close()

        self._mark_processed(enriched.enrichment_id)

    def _persist_update(self, enriched: EnrichedCanonicalMessage, result: UpdateGateResult) -> None:
        now = datetime.now(timezone.utc).isoformat()
        update_source_link: str | None = None
        try:
            pconn = _sqlite3.connect(self._parser_db)
            try:
                rm_row = pconn.execute(
                    "SELECT source_chat_id, telegram_message_id FROM raw_messages WHERE raw_message_id=?",
                    (enriched.raw_message_id,),
                ).fetchone()
                if rm_row and rm_row[0] and rm_row[1]:
                    chat_id = str(rm_row[0]).removeprefix("-100")
                    update_source_link = f"https://t.me/c/{chat_id}/{rm_row[1]}"
            finally:
                pconn.close()
        except Exception:
            pass  # non-fatal: link will be absent
        conn = _sqlite3.connect(self._ops_db)
        try:
            with conn:
                for cr in result.chain_results:
                    if cr.new_lifecycle_state or cr.new_be_protection_status or cr.new_plan_state_json is not None:
                        fields = ["updated_at=?"]
                        vals: list = [now]
                        if cr.new_lifecycle_state:
                            fields.append("lifecycle_state=?")
                            vals.append(cr.new_lifecycle_state)
                        if cr.new_be_protection_status:
                            fields.append("be_protection_status=?")
                            vals.append(cr.new_be_protection_status)
                        if cr.new_plan_state_json is not None:
                            fields.append("plan_state_json=?")
                            vals.append(cr.new_plan_state_json)
                        vals.append(cr.trade_chain_id)
                        conn.execute(
                            f"UPDATE ops_trade_chains SET {', '.join(fields)} WHERE trade_chain_id=?",
                            vals,
                        )
                    for event in cr.lifecycle_events:
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO ops_lifecycle_events (
                                trade_chain_id, event_type, source_type, source_id,
                                previous_state, next_state, payload_json, idempotency_key, created_at
                            ) VALUES (?,?,?,?,?,?,?,?,?)
                            """,
                            (
                                cr.trade_chain_id, event.event_type, event.source_type,
                                event.source_id, event.previous_state, event.next_state,
                                event.payload_json, event.idempotency_key, now,
                            ),
                        )
                    for cmd in cr.execution_commands:
                        # Inject source_message_link into CLOSE_FULL / CLOSE_PARTIAL
                        # payloads so the downstream notification pipeline can surface
                        # a link back to the trader's original message.
                        cmd_payload_json = cmd.payload_json
                        if (
                            update_source_link
                            and cmd.command_type in ("CLOSE_FULL", "CLOSE_PARTIAL")
                        ):
                            try:
                                _p = json.loads(cmd_payload_json or "{}")
                                _p["source_message_link"] = update_source_link
                                cmd_payload_json = json.dumps(_p)
                            except Exception:
                                pass  # non-fatal: keep original payload
                        for payload_json, idempotency_key in _expand_cancel_pending_commands(
                            conn,
                            trade_chain_id=cr.trade_chain_id,
                            command_type=cmd.command_type,
                            payload_json=cmd_payload_json,
                            idempotency_key=cmd.idempotency_key,
                        ):
                            conn.execute(
                                """
                                INSERT OR IGNORE INTO ops_execution_commands (
                                    trade_chain_id, command_type, status, payload_json,
                                    idempotency_key, created_at, updated_at
                                ) VALUES (?,?,?,?,?,?,?)
                                """,
                                (
                                    cr.trade_chain_id, cmd.command_type, cmd.status, payload_json,
                                    idempotency_key, now, now,
                                ),
                            )
                for event in result.review_events:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO ops_lifecycle_events (
                            trade_chain_id, event_type, source_type, source_id,
                            payload_json, idempotency_key, created_at
                        ) VALUES (?,?,?,?,?,?,?)
                        """,
                        (
                            None, event.event_type, event.source_type, event.source_id,
                            event.payload_json, event.idempotency_key, now,
                        ),
                    )
                    try:
                        _write_no_target_update_clean_log(conn, event, enriched, update_source_link)
                    except Exception:
                        logger.exception(
                            "no_target_update_clean_log failed for %s", event.idempotency_key
                        )
                for cr in result.chain_results:
                    if cr.trade_chain_id:
                        try:
                            project_clean_log_for_chain(conn, cr.trade_chain_id)
                        except Exception:
                            logger.exception(
                                "clean_log projection failed for chain %s", cr.trade_chain_id
                            )

                _active_chain_ids: set[int] = set()
                for cr in result.chain_results:
                    if not cr.trade_chain_id:
                        continue
                    if any(
                        e.event_type in {"TELEGRAM_UPDATE_ACCEPTED", "REVIEW_REQUIRED"}
                        or e.event_type.startswith("NOOP_")
                        for e in cr.lifecycle_events
                    ):
                        _active_chain_ids.add(cr.trade_chain_id)
                _is_multi_chain = len(_active_chain_ids) >= 2

                if not _is_multi_chain:
                    _update_log_by_chain: dict[int, list[UpdateChainResult]] = {}
                    for cr in result.chain_results:
                        if cr.trade_chain_id:
                            _update_log_by_chain.setdefault(cr.trade_chain_id, []).append(cr)
                    for _chain_id, _crs in _update_log_by_chain.items():
                        if len(_crs) == 1:
                            _merged = _crs[0]
                        else:
                            _merged = UpdateChainResult(
                                trade_chain_id=_chain_id,
                                new_lifecycle_state=next(
                                    (c.new_lifecycle_state for c in reversed(_crs) if c.new_lifecycle_state), None
                                ),
                                new_be_protection_status=next(
                                    (c.new_be_protection_status for c in reversed(_crs) if c.new_be_protection_status), None
                                ),
                                lifecycle_events=[ev for c in _crs for ev in c.lifecycle_events],
                                execution_commands=[cmd for c in _crs for cmd in c.execution_commands],
                            )
                        try:
                            _write_update_clean_log(
                                conn, _merged, enriched.canonical_message_id, update_source_link,
                            )
                        except Exception:
                            logger.exception(
                                "update clean_log synthesis failed for chain %s", _chain_id
                            )
                try:
                    _write_multi_chain_summary(conn, result.chain_results, enriched.canonical_message_id, update_source_link)
                except Exception:
                    logger.exception(
                        "multi_chain_summary failed for canonical_message_id=%s",
                        enriched.canonical_message_id,
                    )
        finally:
            conn.close()

        self._mark_processed(enriched.enrichment_id)

    def _mark_processed(self, enrichment_id: int) -> None:
        conn = _sqlite3.connect(self._parser_db)
        try:
            conn.execute(
                "UPDATE enriched_canonical_messages SET lifecycle_processed=1 WHERE enrichment_id=?",
                (enrichment_id,),
            )
            conn.commit()
        finally:
            conn.close()


__all__ = [
    "LifecycleEntryGate", "LifecycleGateWorker",
    "SignalGateResult", "UpdateGateResult", "UpdateChainResult",
    "SignalAdmissionContext",
]
