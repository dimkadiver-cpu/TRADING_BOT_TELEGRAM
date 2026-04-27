"""Output models for the multi-ref target-aware resolver (Fase 3)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class ResolvedActionItem:
    """Risultato della risoluzione di un singolo TargetedAction."""

    action_index: int
    action_type: str
    resolved_position_ids: list[int]
    eligibility: str  # ELIGIBLE | NOT_FOUND | WARN | INELIGIBLE
    reason: str | None = None
    resolved_attempt_keys: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ResolvedReportItem:
    """Risultato della risoluzione di un singolo TargetedReport."""

    report_index: int
    event_type: str
    resolved_position_ids: list[int]
    eligibility: str
    reason: str | None = None
    resolved_attempt_keys: list[str] = field(default_factory=list)


@dataclass
class MultiRefResolvedResult:
    """Risultato completo della risoluzione multi-ref target-aware."""

    resolved_actions: list[ResolvedActionItem] = field(default_factory=list)
    resolved_reports: list[ResolvedReportItem] = field(default_factory=list)
