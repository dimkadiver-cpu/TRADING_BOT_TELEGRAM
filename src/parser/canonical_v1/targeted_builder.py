"""Builder helpers: convert actions_structured dicts → TargetedAction / TargetedReport.

Consumes the list of action dicts already produced by trader profile parsers
(each dict may or may not have a ``targeting`` key).  Emits the typed Pydantic
models used in ``CanonicalMessage.targeted_actions`` and
``CanonicalMessage.targeted_reports``.
"""

from __future__ import annotations

import re
from typing import Any

from src.parser.canonical_v1.models import (
    ActionType,
    TargetedAction,
    TargetedActionDiagnostics,
    TargetedActionTargeting,
    TargetedReport,
    TargetedReportResult,
    TargetedReportTargeting,
)

# ---------------------------------------------------------------------------
# Internal regex for per-line result extraction
# ---------------------------------------------------------------------------
# Matches lines of the form:
#   SYMBOL - https://t.me/... [→] VALUE R|%
# (instrument_hint) (telegram link) (optional arrow) (numeric result) (unit)

_LINK_ID_RE = re.compile(
    r"(?:https?://)?t\.me/(?:c/\d+|[A-Za-z0-9_]+)/(?P<id>\d+)",
    re.IGNORECASE,
)
_LINE_REPORT_RE = re.compile(
    r"^\s*(?P<symbol>[A-Z0-9]{1,20})\s*[-–]\s*"      # SYMBOL -
    r"(?:https?://)?t\.me/\S+\s*"                       # telegram link
    r"(?:[→>-]\s*)?"                                     # optional separator
    r"(?P<value>[+-]?\d+(?:[.,]\d+)?)\s*"               # numeric value
    r"(?P<unit>R{1,2}|%)",                              # unit: R/RR/%
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Semantic signature helpers
# ---------------------------------------------------------------------------

def _semantic_signature(action: dict[str, Any]) -> str:
    act = action.get("action", "")
    if act == "MOVE_STOP":
        return f"SET_STOP:{action.get('new_stop_level', 'ENTRY')}"
    if act == "CLOSE_POSITION":
        return f"CLOSE:{action.get('scope', 'FULL')}"
    if act == "CANCEL_PENDING":
        return f"CANCEL_PENDING:{action.get('scope', 'TARGETED')}"
    return act


def _action_type(action: dict[str, Any]) -> ActionType:
    mapping: dict[str, ActionType] = {
        "MOVE_STOP": "SET_STOP",
        "CLOSE_POSITION": "CLOSE",
        "CANCEL_PENDING": "CANCEL_PENDING",
    }
    return mapping.get(action.get("action", ""), "CLOSE")


def _params(action: dict[str, Any]) -> dict[str, Any]:
    act = action.get("action", "")
    if act == "MOVE_STOP":
        level = str(action.get("new_stop_level") or "ENTRY").upper()
        if level == "ENTRY":
            return {"target_type": "ENTRY"}
        tp_match = re.match(r"^TP(\d+)$", level)
        if tp_match:
            return {"target_type": "TP_LEVEL", "value": int(tp_match.group(1))}
        return {"target_type": "ENTRY"}
    if act == "CLOSE_POSITION":
        return {"close_scope": action.get("scope", "FULL")}
    if act == "CANCEL_PENDING":
        scope = action.get("scope", "TARGETED")
        return {"cancel_scope": scope}
    return {}


# ---------------------------------------------------------------------------
# build_targeted_actions
# ---------------------------------------------------------------------------

def build_targeted_actions(
    actions_structured: list[dict[str, Any]],
) -> list[TargetedAction]:
    """Convert actions_structured dicts (those with a ``targeting`` key) to TargetedAction list.

    Groups per-line EXPLICIT_TARGETS actions with the same semantic signature into a
    single TargetedAction (TARGET_ITEM_WIDE grouping).
    Actions already carrying TARGET_GROUP or SELECTOR are passed through as-is (MESSAGE_WIDE).
    """
    targeted = [a for a in actions_structured if "targeting" in a]
    if not targeted:
        return []

    # Preserve insertion order while grouping by semantic signature.
    groups: dict[str, list[dict[str, Any]]] = {}
    for action in targeted:
        sig = _semantic_signature(action)
        groups.setdefault(sig, []).append(action)

    result: list[TargetedAction] = []
    for sig, group in groups.items():
        template = group[0]
        targeting_raw: dict[str, Any] = template.get("targeting", {})
        mode: str = targeting_raw.get("mode", "EXPLICIT_TARGETS")

        if mode in ("TARGET_GROUP", "SELECTOR"):
            # Already fully grouped by upstream logic → MESSAGE_WIDE
            targeting = TargetedActionTargeting(
                mode=mode,
                targets=targeting_raw.get("targets", []),
                selector=targeting_raw.get("selector"),
            )
            diag = TargetedActionDiagnostics(
                resolution_unit="MESSAGE_WIDE",
                semantic_signature=sig,
                grouping_reason="pre_grouped",
            )
        else:
            # Per-line EXPLICIT_TARGETS → merge by signature → TARGET_ITEM_WIDE
            all_targets: list[int] = []
            for act in group:
                all_targets.extend(act.get("targeting", {}).get("targets", []))
            final_mode = "TARGET_GROUP" if len(all_targets) > 1 else "EXPLICIT_TARGETS"
            targeting = TargetedActionTargeting(
                mode=final_mode,
                targets=all_targets,
            )
            diag = TargetedActionDiagnostics(
                resolution_unit="TARGET_ITEM_WIDE",
                semantic_signature=sig,
                grouping_reason=f"grouped_{len(group)}_items_by_signature",
            )

        result.append(
            TargetedAction(
                action_type=_action_type(template),
                params=_params(template),
                targeting=targeting,
                diagnostics=diag,
            )
        )

    return result


# ---------------------------------------------------------------------------
# build_targeted_reports_from_lines
# ---------------------------------------------------------------------------

def build_targeted_reports_from_lines(raw_text: str) -> list[TargetedReport]:
    """Parse per-line result entries from a multi-ref message.

    Expected line format:
        SYMBOL - https://t.me/.../MESSAGE_ID [→] VALUE R|%

    Returns one TargetedReport per matching line.
    """
    reports: list[TargetedReport] = []
    for line in raw_text.splitlines():
        match = _LINE_REPORT_RE.match(line)
        if not match:
            continue
        link_match = _LINK_ID_RE.search(line)
        if not link_match:
            continue

        symbol = match.group("symbol").upper()
        raw_value = match.group("value").replace(",", ".")
        try:
            value = float(raw_value)
        except ValueError:
            continue
        unit_raw = match.group("unit").upper()
        unit = "R" if unit_raw.startswith("R") else "PERCENT"

        message_id = int(link_match.group("id"))

        targeting = TargetedReportTargeting(
            mode="EXPLICIT_TARGETS",
            targets=[message_id],
        )
        result = TargetedReportResult(value=value, unit=unit)
        diag = TargetedActionDiagnostics(
            resolution_unit="TARGET_ITEM_WIDE",
            semantic_signature=f"REPORT:{unit}",
            grouping_reason="per_line",
        )
        reports.append(
            TargetedReport(
                event_type="FINAL_RESULT",
                result=result,
                targeting=targeting,
                instrument_hint=symbol,
                raw_fragment=line.strip(),
                diagnostics=diag,
            )
        )
    return reports
