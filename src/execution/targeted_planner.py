"""Targeted update planner — Fase 4.

Builds TargetedStateUpdatePlan from MultiRefResolvedResult + CanonicalMessage.
No DB access required; all data comes from the resolver output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.parser.canonical_v1.models import CanonicalMessage
from src.target_resolver.models import MultiRefResolvedResult


@dataclass
class TargetedActionPlanItem:
    """Un'azione pianificata con attempt_key stabili e parametri originali."""

    action_type: str
    target_attempt_keys: list[str]
    params: dict[str, Any]
    eligibility: str  # ELIGIBLE | NOT_FOUND | WARN | INELIGIBLE
    reason: str | None = None


@dataclass
class TargetedReportPlanItem:
    """Un report pianificato con attempt_key stabili e risultato originale."""

    event_type: str
    target_attempt_keys: list[str]
    result: dict[str, Any] | None
    eligibility: str
    reason: str | None = None


@dataclass
class TargetedStateUpdatePlan:
    """Piano completo azioni + report per il runtime multi-ref target-aware."""

    action_plans: list[TargetedActionPlanItem] = field(default_factory=list)
    report_plans: list[TargetedReportPlanItem] = field(default_factory=list)


def build_plan(
    resolved: MultiRefResolvedResult,
    canonical: CanonicalMessage,
) -> TargetedStateUpdatePlan:
    """Costruisce il piano dall'output del resolver e dal CanonicalMessage.

    Non accede al DB — ogni campo è derivato dai due argomenti.
    """
    action_plans: list[TargetedActionPlanItem] = []
    for item in resolved.resolved_actions:
        params: dict[str, Any] = {}
        if 0 <= item.action_index < len(canonical.targeted_actions):
            params = dict(canonical.targeted_actions[item.action_index].params or {})
        action_plans.append(
            TargetedActionPlanItem(
                action_type=item.action_type,
                target_attempt_keys=list(item.resolved_attempt_keys),
                params=params,
                eligibility=item.eligibility,
                reason=item.reason,
            )
        )

    report_plans: list[TargetedReportPlanItem] = []
    for item in resolved.resolved_reports:
        result_payload: dict[str, Any] | None = None
        if 0 <= item.report_index < len(canonical.targeted_reports):
            report = canonical.targeted_reports[item.report_index]
            if report.result is not None:
                result_payload = report.result.model_dump()
        report_plans.append(
            TargetedReportPlanItem(
                event_type=item.event_type,
                target_attempt_keys=list(item.resolved_attempt_keys),
                result=result_payload,
                eligibility=item.eligibility,
                reason=item.reason,
            )
        )

    return TargetedStateUpdatePlan(
        action_plans=action_plans,
        report_plans=report_plans,
    )


def build_targeted_plan(
    resolved: MultiRefResolvedResult,
    canonical: CanonicalMessage,
) -> TargetedStateUpdatePlan:
    """Backward-compatible alias kept for existing callers."""
    return build_plan(resolved, canonical)
