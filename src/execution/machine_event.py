"""Machine event rule engine for position management.

Evaluates event-driven position management rules defined in
``management_rules_json["machine_event"]["rules"]``.

Supported event types
---------------------
TP_EXECUTED   — a take-profit order was filled
EXIT_BE       — a breakeven stop was hit (stop price ≈ entry price)
SL_HIT        — a real (non-BE) stop was hit (no default rules, extensible)

Supported actions
-----------------
MOVE_STOP_TO_BE  — move the stop-loss to the trade entry price (breakeven)
MARK_EXIT_BE     — record that the exit was at breakeven in trade metadata
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class MachineEventAction:
    """A single action emitted by the rule engine."""

    action_type: str  # MOVE_STOP_TO_BE | MARK_EXIT_BE


def evaluate_rules(
    *,
    event_type: str,
    event_context: dict[str, Any],
    management_rules: dict[str, Any] | None,
) -> list[MachineEventAction]:
    """Return actions to execute for *event_type* given *management_rules*.

    Returns an empty list when:
    - ``management_rules`` is absent / not a dict
    - no ``machine_event.rules`` section is present
    - no rule matches the event type + conditions

    Args:
        event_type:      e.g. ``"TP_EXECUTED"``, ``"EXIT_BE"``, ``"SL_HIT"``
        event_context:   key/value pairs the rule ``when`` clause is evaluated against.
                         e.g. ``{"tp_level": 2}`` for a TP_EXECUTED event on TP2.
        management_rules: snapshot dict from ``operational_signals.management_rules_json``.
    """
    if not isinstance(management_rules, dict):
        return []
    machine_event_cfg = management_rules.get("machine_event")
    if not isinstance(machine_event_cfg, dict):
        return []
    rules = machine_event_cfg.get("rules")
    if not isinstance(rules, list):
        return []

    actions: list[MachineEventAction] = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        if rule.get("event_type") != event_type:
            continue
        if not _conditions_match(rule.get("when"), event_context):
            continue
        for action in rule.get("actions") or []:
            if not isinstance(action, dict):
                continue
            action_type = action.get("type")
            if isinstance(action_type, str) and action_type.strip():
                actions.append(MachineEventAction(action_type=action_type.strip()))
    return actions


def _conditions_match(when: Any, event_context: dict[str, Any]) -> bool:
    """Return True when every key in *when* matches the corresponding value in *event_context*."""
    if when is None:
        return True  # no condition clause → unconditional match
    if not isinstance(when, dict):
        return False
    return all(event_context.get(k) == v for k, v in when.items())
