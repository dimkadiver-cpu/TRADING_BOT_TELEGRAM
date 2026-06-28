# src/runtime_v2/signal_enrichment/processor.py
from __future__ import annotations

import logging
from collections.abc import Callable, Iterable

from src.runtime_v2.parser_pipeline.models import CanonicalParseResult
from src.runtime_v2.signal_enrichment.config_loader import OperationConfigLoader
from src.runtime_v2.signal_enrichment.models import (
    EntrySequenceRealignment,
    EffectiveEnrichmentConfig,
    EnrichedCanonicalMessage,
    EnrichedEntryLeg,
    EnrichedSignalPayload,
    EnrichmentLogEntry,
    RangeDerivation,
    ReshapeAudit,
    ReshapeRejectionInfo,
)
from src.runtime_v2.signal_enrichment.repository import EnrichedCanonicalMessageRepository
from src.runtime_v2.signal_enrichment.reshaping.setup_reshaper import apply_reshape
from src.runtime_v2.symbols import symbol_matches_policy, to_raw_symbol
from src.parser_v2.contracts.entities import Price, RiskHint, StopLoss, TakeProfit

logger = logging.getLogger(__name__)


class SignalEnrichmentProcessor:
    def __init__(
        self,
        config_loader: OperationConfigLoader,
        repository: EnrichedCanonicalMessageRepository,
        on_pass: Callable[[], None] | None = None,
    ) -> None:
        self._config = config_loader
        self._repo = repository
        self._on_pass = on_pass

    def process(self, result: CanonicalParseResult) -> EnrichedCanonicalMessage:
        existing = self._repo.get_by_canonical_message_id(result.canonical_message_id)
        if existing is not None:
            return existing

        self._config.reload_if_changed()
        trader_id = self._resolve_trader_id(result)
        config = self._config.get_effective_config(trader_id)

        if config is None:
            enriched = self._make_outcome(result, "BLOCK", "trader_not_registered",
                                          lifecycle_processed=True)
        elif not config.enabled:
            enriched = self._make_outcome(result, "BLOCK", "trader_disabled",
                                          lifecycle_processed=True)
        else:
            policy_snapshot = config.model_dump()
            policy_version = self._config.get_policy_version(trader_id)
            enriched = self._route(result, config, policy_snapshot, policy_version)

        saved = self._repo.save(enriched)
        if not saved.lifecycle_processed and self._on_pass:
            self._on_pass()
        return saved

    # ── Routing ───────────────────────────────────────────────────────────────

    def _route(
        self,
        result: CanonicalParseResult,
        config: EffectiveEnrichmentConfig,
        policy_snapshot: dict,
        policy_version: str,
    ) -> EnrichedCanonicalMessage:
        pc = result.primary_class
        if pc == "SIGNAL":
            return self._process_signal(result, config, policy_snapshot, policy_version)
        if pc == "UPDATE":
            return self._process_update(result, config, policy_snapshot, policy_version)
        return self._pass_direct(result, config, policy_snapshot, policy_version)

    # ── SIGNAL gate ───────────────────────────────────────────────────────────

    def _process_signal(
        self,
        result: CanonicalParseResult,
        config: EffectiveEnrichmentConfig,
        policy_snapshot: dict,
        policy_version: str,
    ) -> EnrichedCanonicalMessage:
        log: list[EnrichmentLogEntry] = []
        signal = result.canonical_message.signal
        trader_id = self._resolve_trader_id(result)
        symbol = to_raw_symbol(signal.symbol) or ""

        def block(reason: str) -> EnrichedCanonicalMessage:
            return self._make_outcome(
                result, "BLOCK", reason, lifecycle_processed=True,
                log=log, policy_snapshot=policy_snapshot, policy_version=policy_version,
                config=config,
            )

        # 1. Blacklist globale
        if self._symbol_in_policy_values(symbol, self._config.get_symbol_blacklist_global()):
            return block("symbol_blacklisted_global")

        # 2. Blacklist per-trader
        if self._symbol_in_policy_values(symbol, self._config.get_symbol_blacklist_for_trader(trader_id)):
            return block("symbol_blacklisted_trader")

        # 3. Entry structure accettata
        if signal.entry_structure not in config.signal_policy.accepted_entry_structures:
            return block("unsupported_entry_structure")

        # 4. SL richiesto
        if config.signal_policy.sl.require_sl:
            if signal.stop_loss is None or signal.stop_loss.price is None:
                return block("missing_stop_loss")

        reshape_mode = config.setup_mode == "reshape" and config.setup_reshape_template is not None

        if reshape_mode:
            # In reshape mode: realign first (so E1..En are stable), then reshape.
            # use_tp_count trim is bypassed — reshape owns TP cardinality.
            raw_entries, _, _, _ = self._apply_entry_weights(signal, config)
            realigned_entries, _, _ = self._realign_limit_entries_by_side(raw_entries, signal.side)

            signal_entries_for_reshape = [
                (f"E{leg.sequence}", leg.price.value)
                for leg in realigned_entries
                if leg.price is not None
            ]
            sl_price = signal.stop_loss.price.value if signal.stop_loss and signal.stop_loss.price else None
            tp_prices_original = [tp.price.value for tp in signal.take_profits]

            reshape_result = apply_reshape(
                signal_entries=signal_entries_for_reshape,
                signal_sl_price=sl_price,
                signal_tp_prices=tp_prices_original,
                signal_entry_structure=str(signal.entry_structure),
                signal_side=str(signal.side),
                template=config.setup_reshape_template,
                limit_split_config=config.signal_policy.entry_split.LIMIT,
            )

            if isinstance(reshape_result, ReshapeRejectionInfo):
                return self._make_block_reshape(
                    result, config, policy_snapshot, policy_version, log, reshape_result
                )

            reshaped_audit: ReshapeAudit = reshape_result

            enriched_signal = self._build_reshaped_payload(
                symbol,
                signal,
                realigned_entries,
                reshaped_audit,
                signal.risk_hint,
                signal.leverage_hint,
            )

            # Finding 4: price_sanity check on reshaped TPs (mirrors passthrough branch)
            if config.signal_policy.price_sanity.enabled:
                ranges = self._symbol_policy_range(symbol, config.signal_policy.price_sanity.symbol_ranges)
                if ranges and len(ranges) == 2:
                    for tp in enriched_signal.take_profits:
                        if not (ranges[0] <= tp.price.value <= ranges[1]):
                            return block("price_out_of_range")
        else:
            # 5. TP trim (passthrough only)
            take_profits = list(signal.take_profits)
            original_tp_count: int | None = None
            use_tp_count = config.signal_policy.tp.use_tp_count
            if use_tp_count is not None and len(take_profits) > use_tp_count:
                original_tp_count = len(take_profits)
                take_profits = take_profits[:use_tp_count]
                log.append(EnrichmentLogEntry(
                    check="tp_count_trimmed",
                    original=str(original_tp_count),
                    result=str(use_tp_count),
                ))

            # 6. Entry split weights + realign
            entries, normalized_structure, range_derivation, range_logs = self._apply_entry_weights(signal, config)
            log.extend(range_logs)
            entries, entry_sequence_realigned, reorder_logs = self._realign_limit_entries_by_side(entries, signal.side)
            log.extend(reorder_logs)

            # 7. Price sanity
            if config.signal_policy.price_sanity.enabled:
                ranges = self._symbol_policy_range(symbol, config.signal_policy.price_sanity.symbol_ranges)
                if ranges and len(ranges) == 2:
                    for tp in take_profits:
                        if not (ranges[0] <= tp.price.value <= ranges[1]):
                            return block("price_out_of_range")

            enriched_signal = EnrichedSignalPayload(
                symbol=symbol or None,
                side=signal.side,
                entry_structure=normalized_structure,
                entries=entries,
                take_profits=take_profits,
                stop_loss=signal.stop_loss,
                range_derivation=range_derivation,
                risk_hint=signal.risk_hint,
                leverage_hint=signal.leverage_hint,
                entry_sequence_realigned=entry_sequence_realigned,
                original_tp_count=original_tp_count,
            )

        return EnrichedCanonicalMessage(
            canonical_message_id=result.canonical_message_id,
            raw_message_id=result.raw_message_id,
            trader_id=trader_id,
            account_id=config.account_id,
            primary_class=result.primary_class,
            enrichment_decision="PASS",
            enriched_signal=enriched_signal,
            management_plan=config.management_plan,
            enrichment_log=log,
            policy_snapshot=policy_snapshot,
            policy_version=policy_version,
            lifecycle_processed=False,
        )

    def _make_block_reshape(
        self,
        result: CanonicalParseResult,
        config: EffectiveEnrichmentConfig,
        policy_snapshot: dict,
        policy_version: str,
        log: list[EnrichmentLogEntry],
        rejection: ReshapeRejectionInfo,
    ) -> EnrichedCanonicalMessage:
        trader_id = self._resolve_trader_id(result)
        signal = result.canonical_message.signal
        sym = to_raw_symbol(signal.symbol) or ""
        # Attach a minimal payload so the formatter can surface reshape_rejected info.
        rejected_payload = EnrichedSignalPayload(
            symbol=sym or None,
            side=signal.side,
            entry_structure=None,
            entries=[],
            take_profits=[],
            stop_loss=None,
            reshape_rejected=rejection,
        )
        return EnrichedCanonicalMessage(
            canonical_message_id=result.canonical_message_id,
            raw_message_id=result.raw_message_id,
            trader_id=trader_id,
            account_id=config.account_id,
            primary_class=result.primary_class,
            enrichment_decision="BLOCK",
            reason_code=rejection.reason_code,
            enriched_signal=rejected_payload,
            enrichment_log=log,
            policy_snapshot=policy_snapshot,
            policy_version=policy_version,
            lifecycle_processed=True,
        )

    def _build_reshaped_payload(
        self,
        symbol: str,
        signal,
        realigned_legs: list[EnrichedEntryLeg],
        audit: ReshapeAudit,
        risk_hint: RiskHint | None,
        leverage_hint: float | None,
    ) -> EnrichedSignalPayload:
        operative_sources = {e.source for e in audit.operative_entries}
        # Re-number from 1 so the first operative leg is always sequence=1,
        # matching how new_tps are re-numbered below and ensuring
        # EntryCommandFactory attaches SL to the correct first leg.
        operative_legs = [
            leg.model_copy(update={"sequence": i + 1})
            for i, leg in enumerate(
                leg for leg in realigned_legs
                if f"E{leg.sequence}" in operative_sources
            )
        ]

        # Finding 2: renormalize surviving leg weights to sum to 1.0
        total_w = sum(leg.weight for leg in operative_legs)
        if total_w > 0 and abs(total_w - 1.0) > 0.001:
            operative_legs = [
                leg.model_copy(update={"weight": leg.weight / total_w})
                for leg in operative_legs
            ]

        new_sl_price = audit.stop_loss.price
        new_sl = StopLoss(price=Price(raw=str(new_sl_price), value=new_sl_price))

        new_tps = [
            TakeProfit(
                sequence=i + 1,
                price=Price(raw=str(t.price), value=t.price),
            )
            for i, t in enumerate(audit.tp_selection.selected)
        ]

        n_operative = len(operative_legs)
        if n_operative == 1:
            derived_structure = "ONE_SHOT"
        elif n_operative == 2:
            derived_structure = "TWO_STEP"
        else:
            derived_structure = "LADDER"

        return EnrichedSignalPayload(
            symbol=symbol or None,
            side=signal.side,
            entry_structure=derived_structure,
            entries=operative_legs,
            take_profits=new_tps,
            stop_loss=new_sl,
            risk_hint=risk_hint,
            leverage_hint=leverage_hint,
            reshaped=audit,
        )

    @staticmethod
    def _symbol_in_policy_values(symbol: str, configured_symbols: Iterable[str]) -> bool:
        if not symbol:
            return False
        for candidate in configured_symbols:
            if symbol_matches_policy(candidate, symbol):
                return True
        return False

    @staticmethod
    def _symbol_policy_range(
        symbol: str,
        symbol_ranges: dict[str, list[float]],
    ) -> list[float] | None:
        if not symbol:
            return None
        for configured_symbol, ranges in symbol_ranges.items():
            if to_raw_symbol(configured_symbol) == symbol:
                return ranges
        return None

    def _apply_entry_weights(
        self,
        signal,
        config: EffectiveEnrichmentConfig,
    ) -> tuple[list[EnrichedEntryLeg], str | None, RangeDerivation | None, list[EnrichmentLogEntry]]:
        split = config.signal_policy.entry_split
        structure = signal.entry_structure
        first_leg = signal.entries[0] if signal.entries else None
        entry_type_key = first_leg.entry_type if first_leg else "LIMIT"

        if entry_type_key == "LIMIT":
            limit = split.LIMIT
            if structure == "ONE_SHOT":
                weights_map = dict(limit.single.weights)
            elif structure == "RANGE":
                weights_map = dict(limit.range.weights)
            elif structure == "TWO_STEP":
                weights_map = dict(limit.averaging.weights)
            else:
                weights_map = dict(limit.ladder.weights)
        else:
            market = split.MARKET
            if structure == "TWO_STEP":
                weights_map = dict(market.averaging.weights)
            else:
                weights_map = dict(market.single.weights)

        total = sum(weights_map.values())
        if total > 0 and abs(total - 1.0) > 0.001:
            weights_map = {k: v / total for k, v in weights_map.items()}

        result = []
        for i, leg in enumerate(signal.entries):
            key = f"E{i + 1}"
            result.append(EnrichedEntryLeg(
                sequence=leg.sequence,
                entry_type=leg.entry_type,
                price=leg.price,
                role=leg.role,
                weight=weights_map.get(key, 0.0),
            ))

        if structure == "RANGE" and entry_type_key == "LIMIT":
            return self._apply_range_split(result, split.LIMIT.range.split_mode, side=signal.side)

        return result, structure, None, []

    @staticmethod
    def _apply_range_split(
        legs: list[EnrichedEntryLeg],
        split_mode: str,
        *,
        side: str | None = None,
    ) -> tuple[list[EnrichedEntryLeg], str, RangeDerivation | None, list[EnrichmentLogEntry]]:
        from src.parser_v2.contracts.entities import Price

        if len(legs) < 2:
            structure = "ONE_SHOT" if len(legs) == 1 else "RANGE"
            return legs, structure, None, []

        valid_prices = [leg.price.value for leg in legs if leg.price is not None]
        if len(valid_prices) < 2:
            return legs, "RANGE", None, []

        min_price = min(valid_prices)
        max_price = max(valid_prices)
        first_authored_leg = min(legs, key=lambda l: l.sequence)
        last_authored_leg = max(legs, key=lambda l: l.sequence)

        if split_mode == "endpoints":
            ordered_legs = legs
            if side in {"LONG", "SHORT"}:
                reverse = side == "LONG"
                ordered_legs = [
                    leg.model_copy(update={"sequence": idx})
                    for idx, leg in enumerate(
                        sorted(
                            legs,
                            key=lambda l: (
                                l.price.value if l.price is not None else float("-inf")
                            ),
                            reverse=reverse,
                        ),
                        start=1,
                    )
                ]
            meta = RangeDerivation(
                derived_from_range=True,
                split_mode=split_mode,
                original_min_price=min_price,
                original_max_price=max_price,
            )
            log_entry = EnrichmentLogEntry(
                check="range_endpoints_retained",
                original=f"{min_price}-{max_price}",
                result="two_step",
                detail="endpoints",
            )
            return ordered_legs, "TWO_STEP", meta, [log_entry]

        if split_mode == "firstpoint":
            if first_authored_leg.price is None:
                return legs, "RANGE", None, []
            target = first_authored_leg.price.value
        elif split_mode == "lastpoint":
            if last_authored_leg.price is None:
                return legs, "RANGE", None, []
            target = last_authored_leg.price.value
        elif split_mode == "midpoint":
            target = round((min_price + max_price) / 2, 8)
        else:
            return legs, "RANGE", None, []

        meta = RangeDerivation(
            derived_from_range=True,
            split_mode=split_mode,
            original_min_price=min_price,
            original_max_price=max_price,
        )
        first_leg = min(legs, key=lambda l: l.sequence)
        new_price = Price(raw=str(target), value=target)
        log_entry = EnrichmentLogEntry(
            check="range_price_derived",
            original=f"{min_price}-{max_price}",
            result=str(target),
            detail=split_mode,
        )
        return [first_leg.model_copy(update={"price": new_price, "weight": 1.0})], "ONE_SHOT", meta, [log_entry]

    @staticmethod
    def _realign_limit_entries_by_side(
        legs: list[EnrichedEntryLeg],
        side: str | None,
    ) -> tuple[list[EnrichedEntryLeg], EntrySequenceRealignment | None, list[EnrichmentLogEntry]]:
        if side not in {"LONG", "SHORT"} or len(legs) < 2:
            return legs, None, []
        if any(leg.entry_type != "LIMIT" or leg.price is None for leg in legs):
            return legs, None, []

        reverse = side == "LONG"
        ordered = sorted(
            legs,
            key=lambda leg: leg.price.value if leg.price is not None else float("-inf"),
            reverse=reverse,
        )
        if [leg.sequence for leg in ordered] == list(range(1, len(legs) + 1)):
            return legs, None, []

        realigned = [
            leg.model_copy(update={"sequence": idx})
            for idx, leg in enumerate(ordered, start=1)
        ]
        meta = EntrySequenceRealignment(
            side=side,
            original=[
                {"sequence": leg.sequence, "price": leg.price.value}
                for leg in legs
                if leg.price is not None
            ],
            normalized=[
                {"sequence": leg.sequence, "price": leg.price.value}
                for leg in realigned
                if leg.price is not None
            ],
        )
        log_entry = EnrichmentLogEntry(
            check="entry_sequence_realigned_for_side",
            original=", ".join(f"{leg.sequence}:{leg.price.value}" for leg in legs if leg.price is not None),
            result=", ".join(f"{leg.sequence}:{leg.price.value}" for leg in realigned if leg.price is not None),
            detail=side,
        )
        return realigned, meta, [log_entry]

    # ── UPDATE gate (Task 7) ──────────────────────────────────────────────────

    def _process_update(
        self,
        result: CanonicalParseResult,
        config: EffectiveEnrichmentConfig,
        policy_snapshot: dict,
        policy_version: str,
    ) -> EnrichedCanonicalMessage:
        log: list[EnrichmentLogEntry] = []
        trader_id = self._resolve_trader_id(result)
        tags = result.canonical_message.target_action_groups

        if not tags:
            return self._make_outcome(
                result, "REVIEW", "no_actionable_targets",
                lifecycle_processed=True,
                policy_snapshot=policy_snapshot,
                policy_version=policy_version,
                config=config,
            )

        for tag in tags:
            for action_item in tag.actions:
                intent = action_item.source_intent
                admitted = config.update_admission.get(intent, False)
                if not admitted:
                    decision = "BLOCK" if config.gate_mode == "block" else "REVIEW"
                    reason = f"action_type_{'disabled' if decision == 'BLOCK' else 'warned'}:{intent}"
                    return EnrichedCanonicalMessage(
                        canonical_message_id=result.canonical_message_id,
                        raw_message_id=result.raw_message_id,
                        trader_id=trader_id,
                        account_id=config.account_id,
                        primary_class=result.primary_class,
                        enrichment_decision=decision,
                        reason_code=reason,
                        enrichment_log=log,
                        policy_snapshot=policy_snapshot,
                        policy_version=policy_version,
                        lifecycle_processed=True,
                    )

        return EnrichedCanonicalMessage(
            canonical_message_id=result.canonical_message_id,
            raw_message_id=result.raw_message_id,
            trader_id=trader_id,
            account_id=config.account_id,
            primary_class=result.primary_class,
            enrichment_decision="PASS",
            enriched_actions=list(tags),
            enrichment_log=log,
            policy_snapshot=policy_snapshot,
            policy_version=policy_version,
            lifecycle_processed=False,
        )

    # ── REPORT / INFO ─────────────────────────────────────────────────────────

    def _pass_direct(
        self,
        result: CanonicalParseResult,
        config: EffectiveEnrichmentConfig,
        policy_snapshot: dict,
        policy_version: str,
    ) -> EnrichedCanonicalMessage:
        # lifecycle_processed=True: REPORT/INFO non richiedono azione downstream, on_pass non scatta
        return EnrichedCanonicalMessage(
            canonical_message_id=result.canonical_message_id,
            raw_message_id=result.raw_message_id,
            trader_id=self._resolve_trader_id(result),
            account_id=config.account_id,
            primary_class=result.primary_class,
            enrichment_decision="PASS",
            enrichment_log=[],
            policy_snapshot=policy_snapshot,
            policy_version=policy_version,
            lifecycle_processed=True,
        )

    # ── Utility ───────────────────────────────────────────────────────────────

    def _make_outcome(
        self,
        result: CanonicalParseResult,
        decision: str,
        reason_code: str | None = None,
        *,
        lifecycle_processed: bool,
        log: list[EnrichmentLogEntry] | None = None,
        policy_snapshot: dict | None = None,
        policy_version: str = "",
        config: EffectiveEnrichmentConfig | None = None,
    ) -> EnrichedCanonicalMessage:
        return EnrichedCanonicalMessage(
            canonical_message_id=result.canonical_message_id,
            raw_message_id=result.raw_message_id,
            trader_id=self._resolve_trader_id(result),
            account_id=config.account_id if config else "",
            primary_class=result.primary_class,
            enrichment_decision=decision,
            reason_code=reason_code,
            enrichment_log=log or [],
            policy_snapshot=policy_snapshot or {},
            policy_version=policy_version,
            lifecycle_processed=lifecycle_processed,
        )

    @staticmethod
    def _resolve_trader_id(result: CanonicalParseResult) -> str:
        resolved = getattr(result, "resolved_trader_id", None)
        if isinstance(resolved, str) and resolved.strip():
            return resolved
        parser_profile = getattr(result, "parser_profile", None)
        if isinstance(parser_profile, str) and parser_profile.strip():
            return parser_profile
        raise ValueError("canonical parse result missing trader identity")


__all__ = ["SignalEnrichmentProcessor"]
