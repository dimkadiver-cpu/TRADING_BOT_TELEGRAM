# src/runtime_v2/signal_enrichment/processor.py
from __future__ import annotations

import logging
from collections.abc import Callable

from src.runtime_v2.parser_pipeline.models import CanonicalParseResult
from src.runtime_v2.signal_enrichment.config_loader import OperationConfigLoader
from src.runtime_v2.signal_enrichment.models import (
    EffectiveEnrichmentConfig,
    EnrichedCanonicalMessage,
    EnrichedEntryLeg,
    EnrichedSignalPayload,
    EnrichmentLogEntry,
    RangeDerivation,
)
from src.runtime_v2.signal_enrichment.repository import EnrichedCanonicalMessageRepository

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
        trader_id = result.parser_profile
        config = self._config.get_effective_config(trader_id)

        if config is None:
            enriched = self._make_outcome(result, "BLOCK", "trader_not_registered",
                                          lifecycle_processed=True)
        elif not config.enabled:
            enriched = self._make_outcome(result, "BLOCK", "trader_disabled",
                                          lifecycle_processed=True)
        else:
            policy_snapshot = config.model_dump()
            policy_version = self._config.get_policy_version()
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
        trader_id = result.parser_profile
        symbol = signal.symbol or ""

        def block(reason: str) -> EnrichedCanonicalMessage:
            return self._make_outcome(
                result, "BLOCK", reason, lifecycle_processed=True,
                log=log, policy_snapshot=policy_snapshot, policy_version=policy_version,
                config=config,
            )

        # 1. Blacklist globale
        if symbol in self._config.get_symbol_blacklist_global():
            return block("symbol_blacklisted_global")

        # 2. Blacklist per-trader
        if symbol in self._config.get_symbol_blacklist_for_trader(trader_id):
            return block("symbol_blacklisted_trader")

        # 3. Entry structure accettata
        if signal.entry_structure not in config.signal_policy.accepted_entry_structures:
            return block("unsupported_entry_structure")

        # 4. SL richiesto
        if config.signal_policy.sl.require_sl:
            if signal.stop_loss is None or signal.stop_loss.price is None:
                return block("missing_stop_loss")

        # 5. TP trim
        take_profits = list(signal.take_profits)
        use_tp_count = config.signal_policy.tp.use_tp_count
        if use_tp_count is not None and len(take_profits) > use_tp_count:
            original_count = len(take_profits)
            take_profits = take_profits[:use_tp_count]
            log.append(EnrichmentLogEntry(
                check="tp_count_trimmed",
                original=str(original_count),
                result=str(use_tp_count),
            ))

        # 6. Entry split weights
        entries, normalized_structure, range_derivation, range_logs = self._apply_entry_weights(signal, config)
        log.extend(range_logs)

        # 7. Price sanity (se abilitata)
        if config.signal_policy.price_sanity.enabled:
            ranges = config.signal_policy.price_sanity.symbol_ranges.get(symbol)
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
            return self._apply_range_split(result, split.LIMIT.range.split_mode)

        return result, structure, None, []

    @staticmethod
    def _apply_range_split(
        legs: list[EnrichedEntryLeg],
        split_mode: str,
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
            return legs, "TWO_STEP", meta, [log_entry]

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

    # ── UPDATE gate (Task 7) ──────────────────────────────────────────────────

    def _process_update(
        self,
        result: CanonicalParseResult,
        config: EffectiveEnrichmentConfig,
        policy_snapshot: dict,
        policy_version: str,
    ) -> EnrichedCanonicalMessage:
        log: list[EnrichmentLogEntry] = []
        trader_id = result.parser_profile
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
            trader_id=result.parser_profile,
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
            trader_id=result.parser_profile,
            account_id=config.account_id if config else "",
            primary_class=result.primary_class,
            enrichment_decision=decision,
            reason_code=reason_code,
            enrichment_log=log or [],
            policy_snapshot=policy_snapshot or {},
            policy_version=policy_version,
            lifecycle_processed=lifecycle_processed,
        )


__all__ = ["SignalEnrichmentProcessor"]
