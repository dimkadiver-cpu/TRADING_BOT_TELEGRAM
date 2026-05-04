"""ClassificationResolver — decide primary_class e parse_status finale.

La classificazione non dipende più dai soli marker testuali.
L'ordine di priorità è:

    1. SIGNAL  — struttura segnale estratta (symbol + side + entry)
    2. UPDATE  — intenti operativi validati (categoria UPDATE)
    3. REPORT  — intenti report/osservazionali (categoria REPORT)
    4. INFO    — marker info_only senza struttura
    5. INFO    — fallback con parse_status=UNCLASSIFIED

I marker (ClassEvidence) sono evidenza secondaria, non decisori.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.parser.rules_engine import ClassEvidence


@dataclass
class ClassificationInput:
    text: str
    signal: Any | None
    intents: list[Any]
    class_evidence: ClassEvidence
    targeting: Any | None = None


@dataclass
class ResolvedClassification:
    primary_class: str
    parse_status: str
    confidence: float
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)


class ClassificationResolver:
    """Decide primary_class partendo da struttura, intenti e evidenze marker."""

    def resolve(self, inp: ClassificationInput) -> ResolvedClassification:
        signal = inp.signal
        intents = inp.intents
        evidence = inp.class_evidence

        update_intents = [i for i in intents if getattr(i, "category", None) == "UPDATE"]
        report_intents = [i for i in intents if getattr(i, "category", None) == "REPORT"]

        # --- SIGNAL: solo da struttura estratta ---
        if signal is not None:
            completeness = getattr(signal, "completeness", None)
            is_complete = completeness == "COMPLETE" or (
                isinstance(completeness, float) and completeness >= 1.0
            )
            if is_complete:
                return ResolvedClassification(
                    primary_class="SIGNAL",
                    parse_status="PARSED",
                    confidence=0.95,
                    reasons=["signal_structure:COMPLETE"],
                )
            missing = list(getattr(signal, "missing_fields", None) or [])
            return ResolvedClassification(
                primary_class="SIGNAL",
                parse_status="PARTIAL",
                confidence=0.7,
                reasons=["signal_structure:PARTIAL", f"missing:{missing}"],
                warnings=["partial_signal"] if not missing else [f"partial_signal:missing={missing}"],
                diagnostics={"missing_fields": missing},
            )

        # --- UPDATE: intenti operativi state-changing ---
        if update_intents:
            intent_names = sorted({getattr(i.type, "value", str(i.type)) for i in update_intents})
            return ResolvedClassification(
                primary_class="UPDATE",
                parse_status="PARSED",
                confidence=0.85,
                reasons=[f"update_intent:{n}" for n in intent_names],
            )

        # --- REPORT: intenti report/osservazionali ---
        if report_intents:
            intent_names = sorted({getattr(i.type, "value", str(i.type)) for i in report_intents})
            return ResolvedClassification(
                primary_class="REPORT",
                parse_status="PARSED",
                confidence=0.85,
                reasons=[f"report_intent:{n}" for n in intent_names],
            )

        # --- INFO con evidenza marker info_only ---
        if evidence.winning_hint == "INFO_ONLY" and evidence.confidence_hint > 0.0:
            return ResolvedClassification(
                primary_class="INFO",
                parse_status="PARSED",
                confidence=evidence.confidence_hint,
                reasons=["info_only_markers"],
            )

        # --- Fallback: nessuna struttura, nessun intent valido ---
        return ResolvedClassification(
            primary_class="INFO",
            parse_status="UNCLASSIFIED",
            confidence=0.0,
            reasons=["no_structure_no_intents"],
        )
