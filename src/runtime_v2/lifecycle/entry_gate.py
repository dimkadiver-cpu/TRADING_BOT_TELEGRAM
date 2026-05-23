from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from src.runtime_v2.lifecycle.entry_command_factory import EntryCommandFactory
from src.runtime_v2.lifecycle.execution_plan import ExecutionPlanBuilder
from src.runtime_v2.lifecycle.models import (
    BeProtectionStatus, ControlMode, ExecutionCommand,
    LifecycleEvent, LifecycleState, TradeChain,
)
from src.runtime_v2.lifecycle.ports import (
    AccountStateSnapshot, ExchangeDataPort, SymbolMarketSnapshot,
)
from src.runtime_v2.lifecycle.risk_capacity import RiskCapacityEngine
from src.runtime_v2.signal_enrichment.models import (
    EnrichedCanonicalMessage, ManagementPlanConfig,
)

logger = logging.getLogger(__name__)

GLOBAL_SCOPES = frozenset({"ALL_POSITIONS", "ALL_OPEN", "ALL_REMAINING"})


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


@dataclass
class UpdateGateResult:
    chain_results: list[UpdateChainResult]
    review_events: list[LifecycleEvent]


_ATTACHED_PROTECTION_MODES = frozenset({
    "UNIFIED_PLAN",
    "C_SIMPLE_ATTACHED", "C_MULTI_TP",
    "D_MULTI_ENTRY_1TP", "D_MULTI_ENTRY_MULTI_TP", "D_POSITION_TPSL",
})


def _be_move_extra(chain: "TradeChain") -> dict:
    try:
        rs = json.loads(chain.risk_snapshot_json or "{}")
        hedge_mode = bool(rs.get("hedge_mode", False))
    except Exception:
        hedge_mode = False
    position_idx = LifecycleEntryGate.resolve_position_idx(chain.side, hedge_mode)
    protection_style = (
        "attached_full"
        if chain.execution_mode in _ATTACHED_PROTECTION_MODES
        else "standalone_order"
    )
    return {"protection_style": protection_style, "position_idx": position_idx}


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
    ) -> SignalGateResult:
        eid = enriched.enrichment_id

        if control_mode in ("BLOCK_NEW_ENTRIES", "FULL_STOP"):
            return self._review_signal(eid, "control_mode:new_entries_paused")

        signal = enriched.enriched_signal
        if signal is None or not signal.symbol or not signal.side:
            return self._review_signal(eid, "missing_symbol_or_side")

        if not signal.entries:
            return self._review_signal(eid, "no_entry_legs")

        account_snapshot = self._port.get_account_state(enriched.account_id)
        market_snapshot = self._port.get_symbol_market_state(enriched.account_id, signal.symbol)

        decision = self._risk.validate(enriched, open_chains, account_snapshot, market_snapshot)
        if not decision.passed:
            return self._review_signal(eid, decision.reason or "risk_check_failed")

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

        plan_state = ExecutionPlanBuilder.build(
            eid,
            signal.entries,
            signal.take_profits,
            decision.risk_snapshot,
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

    def _review_signal(self, eid: int | None, reason: str) -> SignalGateResult:
        event = LifecycleEvent(
            event_type="REVIEW_REQUIRED",
            source_type="enrichment",
            source_id=str(eid),
            payload_json=json.dumps({"reason": reason}),
            idempotency_key=f"review_signal:{eid}",
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
                    "execution_strategy": "D_POSITION_TPSL",
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
                    "execution_strategy": "D_POSITION_TPSL",
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
                    "execution_strategy": "D_POSITION_TPSL",
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
                        "execution_strategy": "D_POSITION_TPSL",
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
        if tag.targeting.symbols:
            matched = [c for c in trader_chains if c.symbol in tag.targeting.symbols]
            if len(matched) == 1:
                return matched
            if len(matched) > 1:
                return None

        if tag.targeting.explicit_ids:
            matched = [
                c for c in trader_chains
                if str(c.canonical_message_id) in tag.targeting.explicit_ids
            ]
            if matched:
                return matched

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
        chain_exec_mode = chain.execution_mode
        if chain_exec_mode == "C_SIMPLE_ATTACHED":
            entry_pending = any(
                c.command_type == "PLACE_ENTRY_WITH_ATTACHED_TPSL"
                and c.status in ("PENDING", "SENT", "ACK")
                for c in active_commands
            )
            if entry_pending:
                return self._review_chain(
                    enriched, chain,
                    "c_mode_update_blocked:entry_pending_not_filled",
                )
        action_type = action.action_type
        if action_type == "SET_STOP":
            op = action.set_stop
            if op and op.target_type == "ENTRY":
                return self._apply_move_to_be(enriched, chain, active_commands)
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

        event = LifecycleEvent(
            trade_chain_id=chain_id,
            event_type="TELEGRAM_UPDATE_ACCEPTED",
            source_type="telegram_update",
            source_id=str(cmid),
            payload_json=json.dumps({"action": "MODIFY_ENTRIES"}),
            idempotency_key=f"update_modify_entries:{chain_id}:{cmid}",
        )
        return UpdateChainResult(
            trade_chain_id=chain_id,
            new_lifecycle_state=None,
            new_be_protection_status=None,
            lifecycle_events=[event],
            execution_commands=commands,
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

        cmd = ExecutionCommand(
            trade_chain_id=chain_id,
            command_type="MOVE_STOP_TO_BREAKEVEN",
            payload_json=json.dumps({
                "symbol": chain.symbol, "side": chain.side,
                "target_price": chain.entry_avg_price,
                "be_buffer_pct": mp.be_buffer_pct,
                **_be_move_extra(chain),
            }),
            idempotency_key=f"move_be:{chain_id}:{cmid}",
        )
        event = LifecycleEvent(
            trade_chain_id=chain_id,
            event_type="BE_MOVE_REQUESTED",
            source_type="telegram_update",
            source_id=str(cmid),
            previous_state=chain.lifecycle_state,
            next_state=None,
            idempotency_key=f"be_requested:{chain_id}:{cmid}",
        )
        return UpdateChainResult(
            trade_chain_id=chain_id,
            new_lifecycle_state=None,
            new_be_protection_status="BE_MOVE_PENDING",
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

        cmd = ExecutionCommand(
            trade_chain_id=chain_id,
            command_type="CLOSE_FULL",
            payload_json=json.dumps({"symbol": chain.symbol, "side": chain.side}),
            idempotency_key=f"close_full:{chain_id}:{cmid}",
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
            execution_commands=[cmd],
        )

    def _apply_close_partial(
        self, enriched: EnrichedCanonicalMessage, chain: TradeChain, op
    ) -> UpdateChainResult:
        chain_id = chain.trade_chain_id
        cmid = enriched.canonical_message_id
        fraction = op.fraction or 0.5
        cmd = ExecutionCommand(
            trade_chain_id=chain_id,
            command_type="CLOSE_PARTIAL",
            payload_json=json.dumps({"symbol": chain.symbol, "side": chain.side, "fraction": fraction}),
            idempotency_key=f"close_partial:{chain_id}:{cmid}",
        )
        event = LifecycleEvent(
            trade_chain_id=chain_id,
            event_type="TELEGRAM_UPDATE_ACCEPTED",
            source_type="telegram_update",
            source_id=str(cmid),
            payload_json=json.dumps({"action": "CLOSE_PARTIAL", "fraction": fraction}),
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
            payload_json=json.dumps({"symbol": chain.symbol, "side": chain.side}),
            idempotency_key=f"cancel_pending:{chain_id}:{cmid}",
        )]

        event = LifecycleEvent(
            trade_chain_id=chain_id,
            event_type="TELEGRAM_UPDATE_ACCEPTED",
            source_type="telegram_update",
            source_id=str(cmid),
            payload_json=json.dumps({"action": "CANCEL_PENDING"}),
            idempotency_key=f"update_cancel:{chain_id}:{cmid}",
        )

        if state == "WAITING_ENTRY":
            return UpdateChainResult(
                trade_chain_id=chain_id,
                new_lifecycle_state="CANCELLED",
                new_be_protection_status=None,
                lifecycle_events=[event],
                execution_commands=commands,
            )

        # OPEN or PARTIALLY_CLOSED — position exists; cancel pending orders but keep chain alive
        # Attached-SL modes use a position-level SL that covers the full position automatically;
        # no qty sync is needed regardless of whether the pending leg was partially filled.
        if chain.execution_mode not in _ATTACHED_PROTECTION_MODES:
            commands.append(ExecutionCommand(
                trade_chain_id=chain_id,
                command_type="SYNC_PROTECTIVE_ORDERS",
                payload_json=json.dumps({"symbol": chain.symbol, "side": chain.side}),
                idempotency_key=f"sync_after_cancel_pending:{chain_id}:{cmid}",
            ))
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
            buffer = mp.be_buffer_pct
        except Exception:
            buffer = 0.0
        if chain.side == "LONG":
            return chain.current_stop_price >= chain.entry_avg_price * (1 + buffer)
        return chain.current_stop_price <= chain.entry_avg_price * (1 - buffer)

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
    ) -> None:
        self._parser_db = parser_db_path
        self._ops_db = ops_db_path
        self._gate = gate
        self._chain_repo = chain_repo
        self._event_repo = event_repo
        self._command_repo = command_repo
        self._snapshot_repo = snapshot_repo
        self._control_repo = control_repo

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

        open_chains = self._chain_repo.get_active_by_trader(trader_id)
        symbol = enriched_signal.symbol or "" if enriched_signal else ""
        side = enriched_signal.side or "" if enriched_signal else ""
        control_mode = self._control_repo.get_effective_mode(account_id, trader_id, symbol, side)

        if primary_class == "SIGNAL":
            result = self._gate.process_signal(enriched, open_chains, control_mode)
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

    def _persist_signal(self, enriched: EnrichedCanonicalMessage, result: SignalGateResult) -> None:
        now = datetime.now(timezone.utc).isoformat()
        conn = _sqlite3.connect(self._ops_db)
        try:
            with conn:
                chain_id = None
                if result.trade_chain is not None:
                    c = result.trade_chain
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
                            plan_state_json, created_at, updated_at
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                            c.plan_state_json,
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
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO ops_lifecycle_events (
                            trade_chain_id, event_type, source_type, source_id,
                            previous_state, next_state, payload_json, idempotency_key, created_at
                        ) VALUES (?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            chain_id, event.event_type, event.source_type, event.source_id,
                            event.previous_state, event.next_state, event.payload_json,
                            event.idempotency_key, now,
                        ),
                    )

                for cmd in result.execution_commands:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO ops_execution_commands (
                            trade_chain_id, command_type, status, payload_json,
                            idempotency_key, created_at, updated_at
                        ) VALUES (?,?,?,?,?,?,?)
                        """,
                        (
                            chain_id, cmd.command_type, cmd.status, cmd.payload_json,
                            cmd.idempotency_key, now, now,
                        ),
                    )

                if result.account_snapshot:
                    s = result.account_snapshot
                    conn.execute(
                        """
                        INSERT INTO ops_account_snapshots (
                            account_id, equity_usdt, available_balance_usdt,
                            total_open_risk_usdt, total_margin_used_usdt,
                            source, captured_at, payload_json
                        ) VALUES (?,?,?,?,?,?,?,?)
                        """,
                        (
                            enriched.account_id, s.equity_usdt, s.available_balance_usdt,
                            s.total_open_risk_usdt, s.total_margin_used_usdt,
                            s.source, s.captured_at.isoformat(), "{}",
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
                            s.source, s.captured_at.isoformat(), "{}",
                        ),
                    )
        finally:
            conn.close()

        self._mark_processed(enriched.enrichment_id)

    def _persist_update(self, enriched: EnrichedCanonicalMessage, result: UpdateGateResult) -> None:
        now = datetime.now(timezone.utc).isoformat()
        conn = _sqlite3.connect(self._ops_db)
        try:
            with conn:
                for cr in result.chain_results:
                    if cr.new_lifecycle_state or cr.new_be_protection_status:
                        fields = ["updated_at=?"]
                        vals: list = [now]
                        if cr.new_lifecycle_state:
                            fields.append("lifecycle_state=?")
                            vals.append(cr.new_lifecycle_state)
                        if cr.new_be_protection_status:
                            fields.append("be_protection_status=?")
                            vals.append(cr.new_be_protection_status)
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
                        for payload_json, idempotency_key in _expand_cancel_pending_commands(
                            conn,
                            trade_chain_id=cr.trade_chain_id,
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


def _expand_cancel_pending_commands(
    conn: _sqlite3.Connection,
    *,
    trade_chain_id: int,
    command_type: str,
    payload_json: str,
    idempotency_key: str,
) -> list[tuple[str, str]]:
    if command_type != "CANCEL_PENDING_ENTRY":
        return [(payload_json, idempotency_key)]

    entry_client_order_ids = _load_pending_entry_client_order_ids(conn, trade_chain_id)
    if not entry_client_order_ids:
        return [(payload_json, idempotency_key)]

    payload = json.loads(payload_json or "{}")
    expanded: list[tuple[str, str]] = []
    for entry_client_order_id in entry_client_order_ids:
        item = dict(payload)
        item["entry_client_order_id"] = entry_client_order_id
        expanded.append(
            (
                json.dumps(item),
                f"{idempotency_key}:{entry_client_order_id}",
            )
        )
    return expanded


def _load_pending_entry_client_order_ids(
    conn: _sqlite3.Connection,
    trade_chain_id: int,
) -> list[str]:
    rows = conn.execute(
        """
        SELECT client_order_id
        FROM ops_execution_commands
        WHERE trade_chain_id = ?
          AND command_type IN ('PLACE_ENTRY', 'PLACE_ENTRY_WITH_ATTACHED_TPSL')
          AND status IN ('PENDING','SENT','ACK')
          AND client_order_id IS NOT NULL
        ORDER BY command_id
        """,
        (trade_chain_id,),
    ).fetchall()
    return [str(row[0]) for row in rows if row and row[0]]


__all__ = [
    "LifecycleEntryGate", "LifecycleGateWorker",
    "SignalGateResult", "UpdateGateResult", "UpdateChainResult",
]
