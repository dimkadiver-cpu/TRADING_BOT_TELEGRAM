"""Shared profile runtime: orchestrates the common parsing flow for all trader profiles.

Flow per ogni profilo:
    1. classify(text)           → message_type_hint, confidence
    2. detect_intents_with_evidence(text) → intents_raw con forza
    3. extractors.extract(...)  → instrument, signal/update/report payload raw, intents_extra
    4. extract_targets(context) → targets_raw
    5. build_envelope(...)      → TraderEventEnvelopeV1

Profiles in FASE 4+ dovrebbero chiamare:
    return shared_runtime.parse(
        trader_code=..., text=..., context=..., rules=..., extractors=...,
    )
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from src.parser.event_envelope_v1 import (
    InstrumentRaw,
    ReportPayloadRaw,
    SignalPayloadRaw,
    TargetRefRaw,
    TraderEventEnvelopeV1,
    UpdatePayloadRaw,
)
from src.parser.rules_engine import RulesEngine
from src.parser.trader_profiles.base import ParserContext
from src.parser.trader_profiles.shared.envelope_builder import EnvelopeInputs, build_envelope
from src.parser.trader_profiles.shared.intent_taxonomy import resolve_alias
from src.parser.trader_profiles.shared.targeting import extract_targets

logger = logging.getLogger(__name__)


class ExtractorProtocol(Protocol):
    """Interface for trader-specific extraction logic.

    extract() must return a dict with any subset of:
        instrument:          InstrumentRaw | None
        signal_payload_raw:  SignalPayloadRaw | None
        update_payload_raw:  UpdatePayloadRaw | None
        report_payload_raw:  ReportPayloadRaw | None
        intents_extra:       list[str]   (additional intents not detected by RulesEngine)
        targets_extra:       list[TargetRefRaw]   (additional targets not from context)
        telegram_links:      list[str]   (t.me links found in text — merged with context)
        diagnostics:         dict[str, Any]
    """

    def extract(
        self,
        text: str,
        context: ParserContext,
        rules: RulesEngine,
    ) -> dict[str, Any]:
        ...


class SharedProfileRuntime:
    """Orchestrates the common parsing flow.

    Stateless — a single instance can be shared by all profiles.
    """

    def parse(
        self,
        *,
        trader_code: str,
        text: str,
        context: ParserContext,
        rules: RulesEngine,
        extractors: ExtractorProtocol,
    ) -> TraderEventEnvelopeV1:
        """Run the full parsing pipeline and return a TraderEventEnvelopeV1.

        Args:
            trader_code:  Identifies the trader profile (stored in diagnostics).
            text:         Raw message text.
            context:      Parser context (reply_id, links, channel, …).
            rules:        Loaded RulesEngine for this profile.
            extractors:   Profile-specific extractor providing raw blocks.

        Returns:
            TraderEventEnvelopeV1 ready for downstream normalizer.
        """
        # 1. Classify
        classification = rules.classify(text)

        # 2. Detect intents with evidence from rules engine
        intent_matches = rules.detect_intents_with_evidence(text)
        intents_from_engine = [m.intent for m in intent_matches]

        # 3. Profile-specific extraction
        extracted: dict[str, Any] = extractors.extract(text, context, rules)

        # 4. Merge intents
        intents_extra: list[str] = extracted.get("intents_extra") or []
        intents_all = intents_from_engine + intents_extra
        intents_all = _apply_prefer_rules(
            text=text,
            intents=intents_all,
            rules=rules,
        )

        # 5. Extract targets from context + extractor
        telegram_links = extracted.get("telegram_links") or context.extracted_links
        context_targets = extract_targets(
            reply_to_message_id=context.reply_to_message_id,
            text=text,
            extracted_links=telegram_links,
        )
        targets_extra: list[TargetRefRaw] = extracted.get("targets_extra") or []
        targets_raw = [
            TargetRefRaw(kind=t.kind, value=t.value) for t in context_targets
        ] + targets_extra

        # 6. Build diagnostics
        diagnostics: dict[str, Any] = {"trader_code": trader_code}
        diagnostics.update(extracted.get("diagnostics") or {})

        # 7. Build envelope
        inputs = EnvelopeInputs(
            message_type_hint=classification.message_type,
            intents_raw=intents_all,
            instrument=extracted.get("instrument"),
            signal_payload_raw=extracted.get("signal_payload_raw"),
            update_payload_raw=extracted.get("update_payload_raw"),
            report_payload_raw=extracted.get("report_payload_raw"),
            targets_raw=targets_raw,
            confidence=classification.confidence,
            diagnostics=diagnostics,
        )
        return build_envelope(inputs)


def _apply_prefer_rules(
    *,
    text: str,
    intents: list[str],
    rules: RulesEngine,
) -> list[str]:
    raw_rules = rules.raw_rules
    disambiguation = raw_rules.get("disambiguation_rules")
    if not isinstance(disambiguation, dict):
        return intents

    prefer_rules = disambiguation.get("prefer_rules")
    if not isinstance(prefer_rules, list) or not prefer_rules:
        return intents

    normalized = text.lower()
    resolved_intents = list(intents)

    for rule in prefer_rules:
        if not isinstance(rule, dict):
            continue

        when_all_detected_raw = rule.get("when_all_detected")
        prefer_raw = rule.get("prefer")
        if_contains_any = rule.get("if_contains_any")

        if not isinstance(when_all_detected_raw, list) or not isinstance(prefer_raw, str):
            continue

        try:
            when_all_detected = [resolve_alias(str(item)) for item in when_all_detected_raw]
            prefer = resolve_alias(prefer_raw)
        except ValueError:
            continue

        if prefer not in when_all_detected:
            continue

        if isinstance(if_contains_any, list) and if_contains_any:
            probes = [str(item).lower() for item in if_contains_any if str(item).strip()]
            if not any(probe in normalized for probe in probes):
                continue

        current_set = {resolve_alias(intent) for intent in resolved_intents}
        if not all(intent in current_set for intent in when_all_detected):
            continue

        drop = set(when_all_detected)
        drop.discard(prefer)
        filtered: list[str] = []
        for intent in resolved_intents:
            try:
                official = resolve_alias(intent)
            except ValueError:
                filtered.append(intent)
                continue
            if official in drop:
                continue
            filtered.append(intent)
        resolved_intents = filtered

    return resolved_intents
